"""On-demand EVDS observation ingestion.

Usage (single series):
    python evds_on_demand.py --series TP.BKR.TRY.17 --start 01-01-2020 --end 30-06-2026

Usage (multiple):
    python evds_on_demand.py --series "TP.BKR.TRY.17-TP.BKR.TRY.18" --aggregation "avg-avg"

This script:
1. Checks if series observation data already exists in raw/ (skips if so).
2. Looks up series metadata from the local catalog parquet.
3. Fetches observations from the EVDS public frontend endpoint (no API key needed).
4. Saves raw JSON response and request body to data_pipeline/raw/.
5. Appends the series to series_registry.json with a skeleton entry.
6. Prints a summary for each fetched series.

The caller is responsible for:
- Verifying the registry entry's output_column, native_unit, measurement fields.
- Re-running build_dataset.py if integrating into the main pipeline.

Limitations:
- Uses the anonymous public frontend endpoint. Rate limits may apply.
- observation_count=0 in catalog means the catalog has metadata only; this script
  resolves that by fetching actual observations.
- TCMB API limit: 1000 observations per request. For daily series, split by year.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import sys

# Windows charmap fix: force UTF-8 for all print output
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
REGISTRY_PATH = ROOT / "series_registry.json"
CATALOG_PATH = ROOT / "catalog" / "evds_series_catalog.parquet"
EVDS_FE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis/fe"

FREQ_MAP = {
    "AYLIK": "5",
    "HAFTALIK(CUMA)": "3",
    "HAFTALIK(ÇARŞAMBA)": "3",
    "GÜNLÜK": "1",
    "İŞ GÜNÜ": "2",
    "ÜÇ AYLIK": "6",
    "YILLIK": "8",
    "AYDA İKİ KEZ": "4",
    "ALTI AYLIK": "7",
}

FREQ_NATIVE_MAP = {
    "AYLIK": "monthly",
    "HAFTALIK(CUMA)": "weekly_friday",
    "HAFTALIK(ÇARŞAMBA)": "weekly_wednesday",
    "GÜNLÜK": "daily",
    "İŞ GÜNÜ": "business_daily",
    "ÜÇ AYLIK": "quarterly",
    "YILLIK": "annual",
}


def load_catalog() -> dict[str, dict]:
    """Load series catalog parquet into a dict keyed by series_code."""
    try:
        import pandas as pd
        df = pd.read_parquet(CATALOG_PATH)
        return {row["series_code"]: row.to_dict() for _, row in df.iterrows()}
    except Exception as e:
        print(f"[warn] Could not load catalog: {e}. Proceeding without metadata lookup.")
        return {}


def load_registry() -> list[dict]:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return []


def save_registry(registry: list[dict]) -> None:
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def slugify(series_code: str) -> str:
    return series_code.lower().replace(".", "_")


def fetch_observations(
    series_codes: list[str],
    aggregation_types: list[str],
    start_date: str,
    end_date: str,
    frequency_code: str,
) -> tuple[dict, bytes]:
    """POST to EVDS frontend and return (parsed_json, raw_bytes)."""
    body = {
        "type": "json",
        "series": "-".join(series_codes),
        "aggregationTypes": "-".join(aggregation_types),
        "formulas": "-".join(["0"] * len(series_codes)),
        "startDate": start_date,
        "endDate": end_date,
        "frequency": frequency_code,
        "decimalSeperator": ".",
        "decimal": "8",
        "dateFormat": "0",
        "lang": "tr",
        "ozelFormuller": [],
        "groupSeperator": False,
        "isRaporSayfasi": False,
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        EVDS_FE_URL,
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://evds3.tcmb.gov.tr",
            "Referer": "https://evds3.tcmb.gov.tr/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    parsed = json.loads(raw)
    if not isinstance(parsed.get("items"), list):
        raise ValueError(f"Unexpected response structure: {list(parsed.keys())}")
    if not parsed["items"]:
        raise ValueError("Empty items list returned — series may not exist or date range has no data.")
    return parsed, raw, body


def make_slug_filename(series_code: str) -> str:
    return slugify(series_code)


def ingest_series(
    series_code: str,
    start_date: str,
    end_date: str,
    aggregation: str = "avg",
    force: bool = False,
) -> dict:
    """Fetch and persist a single EVDS series. Returns summary dict."""
    slug = slugify(series_code)
    raw_file = f"evds_{slug}.json"
    request_file = f"evds_{slug}_request.json"
    raw_path = RAW / raw_file
    req_path = RAW / request_file

    # Check if already present
    registry = load_registry()
    existing = next((r for r in registry if r.get("series_code") == series_code), None)
    if existing and raw_path.exists() and not force:
        print(f"[skip] {series_code} already in registry and raw file exists. Use --force to re-fetch.")
        return {"series_code": series_code, "status": "skipped", "raw_file": raw_file}

    # Catalog metadata lookup
    catalog = load_catalog()
    meta = catalog.get(series_code, {})
    freq_str = meta.get("frequency", "AYLIK")
    freq_code = FREQ_MAP.get(freq_str, "5")
    native_freq = FREQ_NATIVE_MAP.get(freq_str, "monthly")
    group_code = meta.get("group_code", "unknown")
    series_name = meta.get("series_name_tr", series_code)
    default_agg = meta.get("default_aggregation", aggregation)

    print(f"[fetch] {series_code} | {series_name} | freq={freq_str}({freq_code}) | agg={default_agg}")

    # Fetch
    try:
        parsed, raw_bytes, body = fetch_observations(
            [series_code], [aggregation or default_agg],
            start_date, end_date, freq_code
        )
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RuntimeError(f"HTTP {e.code} — rate limited or blocked. Stop and retry later.")
        raise

    # Persist
    RAW.mkdir(exist_ok=True)
    raw_path.write_bytes(raw_bytes)
    req_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    obs_count = len(parsed["items"])
    total_count = parsed.get("totalCount", obs_count)
    first_date = parsed["items"][0].get("Tarih", "?")
    last_date = parsed["items"][-1].get("Tarih", "?")

    print(f"  -> {obs_count} observations ({first_date} ... {last_date}) | SHA256: {sha256[:12]}...")
    if obs_count != total_count:
        print(f"  [WARN] totalCount={total_count} but items={obs_count}. API 1000-obs limit may apply.")

    # Registry entry
    raw_col = series_code.replace(".", "_")
    output_col = f"evds_{slug}_value"
    entry = {
        "series_code": series_code,
        "raw_column": raw_col,
        "name_tr": series_name,
        "group_code": group_code,
        "catalog_frequency": freq_str,
        "raw_file": raw_file,
        "request_file": request_file,
        "output_column": output_col,
        "native_unit": "percent" if "%" in series_name else "unknown — verify",
        "measurement": "rate" if "%" in series_name or "faiz" in series_name.lower() else "unknown — verify",
        "native_frequency": native_freq,
        "observed_vintage": datetime.date.today().isoformat(),
        "raw_sha256": sha256,
        "observation_count": obs_count,
        "first_observation": first_date,
        "last_observation": last_date,
        "source": "TCMB EVDS",
        "ingestion_note": "Added by evds_on_demand.py — verify output_column and measurement before adding to build_dataset.py",
    }

    # Update registry (replace if exists, append if new)
    if existing:
        idx = registry.index(existing)
        registry[idx] = entry
        print(f"  [registry] Updated existing entry for {series_code}")
    else:
        registry.append(entry)
        print(f"  [registry] Added new entry for {series_code}")
    save_registry(registry)

    return {
        "series_code": series_code,
        "status": "ok",
        "raw_file": raw_file,
        "obs_count": obs_count,
        "sha256": sha256,
        "first": first_date,
        "last": last_date,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", required=True,
                        help='Single series code or dash-separated list: "TP.BKR.TRY.17" or "TP.BKR.TRY.17-TP.BKR.TRY.18"')
    parser.add_argument("--start", default="01-01-2020", help="Start date DD-MM-YYYY (default: 01-01-2020)")
    parser.add_argument("--end", default="30-06-2026", help="End date DD-MM-YYYY (default: 30-06-2026)")
    parser.add_argument("--aggregation", default="avg",
                        help='Aggregation type(s): avg, last, sum. Dash-separated for multiple series.')
    parser.add_argument("--force", action="store_true", help="Re-fetch even if raw file already exists.")
    args = parser.parse_args()

    series_list = args.series.split("-") if "-" in args.series and "TP." not in args.series.split("-")[0][:4] else [args.series]
    # Handle TP.XXX-TP.YYY format properly
    parts = args.series.split("-TP.")
    if len(parts) > 1:
        series_list = [parts[0]] + ["TP." + p for p in parts[1:]]
    else:
        series_list = [args.series]

    agg_list = args.aggregation.split("-")
    if len(agg_list) == 1:
        agg_list = agg_list * len(series_list)

    results = []
    for i, sc in enumerate(series_list):
        agg = agg_list[i] if i < len(agg_list) else "avg"
        try:
            result = ingest_series(sc.strip(), args.start, args.end, aggregation=agg, force=args.force)
        except Exception as e:
            result = {"series_code": sc, "status": "error", "error": str(e)}
            print(f"  [error] {e}")
        results.append(result)
        if i < len(series_list) - 1:
            time.sleep(0.3)  # polite delay between requests

    print("\n=== Summary ===")
    for r in results:
        status = r.get("status", "?")
        obs = r.get("obs_count", "-")
        print(f"  {r['series_code']}: {status} | obs={obs}")
    print(f"\nRaw files saved to: {RAW}")
    print(f"Registry updated: {REGISTRY_PATH}")
    print("\nNext steps:")
    print("  1. Review the registry entry (verify output_column, native_unit, measurement).")
    print("  2. If integrating into build_dataset.py, add the column to the pipeline.")
    print("  3. Run: python build_dataset.py")


if __name__ == "__main__":
    main()
