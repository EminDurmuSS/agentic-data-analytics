#!/usr/bin/env python3
"""Download a catalog-validated, manifest-selected EVDS observation snapshot.

The tool deliberately does not download every EVDS observation. It downloads
the series selected in a versioned analytical manifest, preserves each request
and raw response, detects truncation, retains null observations and writes a
generic long-form Parquet/CSV dataset.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ENDPOINT = "https://evds3.tcmb.gov.tr/igmevdsms-dis/fe"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://evds3.tcmb.gov.tr",
    "Referer": "https://evds3.tcmb.gov.tr/",
    "User-Agent": "KKB-Agentic-Data-Analytics/1.0",
}
FREQUENCY_CODES = {
    "GÜNLÜK": "1",
    "İŞ GÜNÜ": "2",
    "HAFTALIK(CUMA)": "3",
    "HAFTALIK(ÇARŞAMBA)": "3",
    "AYDA İKİ KEZ": "4",
    "AYLIK": "5",
    "ÜÇ AYLIK": "6",
    "ALTI AYLIK": "7",
    "YILLIK": "8",
}
HIGH_FREQUENCY = {"GÜNLÜK", "İŞ GÜNÜ", "HAFTALIK(CUMA)", "HAFTALIK(ÇARŞAMBA)"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def clean_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def post_json(
    body: bytes, timeout: int, transport: str
) -> tuple[bytes, dict[str, Any]]:
    started = utc_now()
    if transport == "curl":
        if not shutil.which("curl"):
            raise RuntimeError("curl bulunamadi; --transport urllib kullanin.")
        with tempfile.TemporaryDirectory(prefix="evds_http_") as temporary:
            response_path = Path(temporary) / "response.bin"
            command = [
                "curl",
                "--disable",
                "--silent",
                "--show-error",
                "--location",
                "--max-redirs",
                "3",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--connect-timeout",
                str(min(timeout, 15)),
                "--max-time",
                str(timeout),
                "--output",
                str(response_path),
                "--write-out",
                "%{http_code}\n%{url_effective}\n%{content_type}",
                "--data-binary",
                "@-",
            ]
            for key, value in HEADERS.items():
                command.extend(["--header", f"{key}: {value}"])
            command.append(ENDPOINT)
            result = subprocess.run(
                command,
                input=body,
                capture_output=True,
                timeout=timeout + 5,
                check=False,
            )
            if result.returncode:
                error = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"curl basarisiz ({result.returncode}): {error}")
            lines = result.stdout.decode("utf-8", errors="replace").splitlines()
            if len(lines) < 3 or not lines[0].isdigit():
                raise RuntimeError("curl EVDS yanit metadatasi okunamadi.")
            data = response_path.read_bytes()
            info = {
                "http_status": int(lines[0]),
                "final_url": lines[1],
                "content_type": lines[2],
            }
    else:
        request = urllib.request.Request(
            ENDPOINT, data=body, method="POST", headers=HEADERS
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                info = {
                    "http_status": response.status,
                    "final_url": response.url,
                    "content_type": response.headers.get("Content-Type", ""),
                }
        except urllib.error.HTTPError as exc:
            data = exc.read()
            info = {
                "http_status": exc.code,
                "final_url": ENDPOINT,
                "content_type": exc.headers.get("Content-Type", ""),
                "error": str(exc),
            }
    info.update(
        {
            "url": ENDPOINT,
            "started_at_utc": started,
            "completed_at_utc": utc_now(),
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "transport": transport,
            "tls_verification": True,
        }
    )
    return data, info


def request_chunks(start: date, end: date, frequency: str) -> list[tuple[date, date]]:
    if frequency not in HIGH_FREQUENCY:
        return [(start, end)]
    result = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        result.append((chunk_start, chunk_end))
    return result


def parse_period_label(value: str, frequency: str) -> tuple[str, date, date]:
    value = value.strip()
    if frequency == "AYLIK":
        parsed = datetime.strptime(value, "%Y-%m").date()
        end = date(parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1])
        return value, parsed, end
    if frequency == "ÜÇ AYLIK":
        year_text, quarter_text = value.split("-", 1)
        quarter = int(quarter_text.rstrip("ÇCçc"))
        if quarter not in {1, 2, 3, 4}:
            raise ValueError(f"EVDS ceyrek etiketi okunamadi: {value}")
        month = quarter * 3
        start = date(int(year_text), month - 2, 1)
        end = date(int(year_text), month, monthrange(int(year_text), month)[1])
        return f"{int(year_text):04d}-Q{quarter}", start, end
    if frequency == "YILLIK":
        year = int(value)
        return str(year), date(year, 1, 1), date(year, 12, 31)
    if frequency == "ALTI AYLIK":
        year_text, half_text = value.split("-", 1)
        half = int(half_text.rstrip("Yy"))
        if half not in {1, 2}:
            raise ValueError(f"EVDS alti aylik etiketi okunamadi: {value}")
        start_month = 1 if half == 1 else 7
        end_month = 6 if half == 1 else 12
        year = int(year_text)
        return f"{year:04d}-H{half}", date(year, start_month, 1), date(year, end_month, monthrange(year, end_month)[1])
    parsed = datetime.strptime(value, "%d-%m-%Y").date()
    return parsed.isoformat(), parsed, parsed


def request_payload(
    series_code: str,
    aggregation: str,
    frequency: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    return {
        "type": "json",
        "series": series_code,
        "aggregationTypes": aggregation,
        "formulas": "0",
        "startDate": start.strftime("%d-%m-%Y"),
        "endDate": end.strftime("%d-%m-%Y"),
        "frequency": FREQUENCY_CODES[frequency],
        "decimalSeperator": ".",
        "decimal": "8",
        "dateFormat": "0",
        "lang": "tr",
        "ozelFormuller": [],
        "groupSeperator": False,
        "isRaporSayfasi": False,
    }


def parse_response(
    data: bytes,
    series_code: str,
    frequency: str,
    requested_start: date,
    requested_end: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(data.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("EVDS yaniti JSON nesnesi degil.")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("EVDS items listesi bulunamadi.")
    total_count = int(payload.get("totalCount", len(items)))
    if total_count != len(items):
        raise ValueError(
            f"EVDS yaniti eksik olabilir: totalCount={total_count}, items={len(items)}"
        )
    value_key = series_code.replace(".", "_")
    parsed = []
    outside_range = 0
    for source_index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or "Tarih" not in item or value_key not in item:
            raise ValueError(f"EVDS gozlem semasi beklenmedik: {series_code}")
        period, period_start, period_end = parse_period_label(
            str(item["Tarih"]), frequency
        )
        if period_end < requested_start or period_start > requested_end:
            outside_range += 1
            continue
        raw_value = item[value_key]
        if raw_value is None or str(raw_value).strip() == "":
            numeric = None
        else:
            try:
                numeric = float(str(raw_value).replace(",", "."))
            except ValueError as exc:
                raise ValueError(
                    f"EVDS sayisal degeri okunamadi: {series_code}={raw_value!r}"
                ) from exc
        unix_value = item.get("UNIXTIME")
        if isinstance(unix_value, dict):
            unix_value = unix_value.get("$numberLong")
        parsed.append(
            {
                "series_code": series_code,
                "period": period,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "source_date_label": str(item["Tarih"]),
                "source_row_index": source_index,
                "value": numeric,
                "value_raw": None if raw_value is None else str(raw_value),
                "is_missing": numeric is None,
                "unix_time": int(unix_value) if unix_value is not None else None,
            }
        )
    metadata = {
        "response_total_count": total_count,
        "retained_rows": len(parsed),
        "outside_requested_range_rows": outside_range,
        "series_names": payload.get("seriesNames"),
        "frequency_conversion": payload.get("frequencyConversion"),
    }
    return parsed, metadata


def build_catalog_index(path: Path) -> dict[str, dict[str, Any]]:
    frame = pd.read_parquet(path)
    if frame["series_code"].duplicated().any():
        raise ValueError("EVDS katalogunda seri kodu tekrari var.")
    result = {}
    for record in frame.to_dict("records"):
        result[str(record["series_code"])] = {
            key: clean_json_value(value) for key, value in record.items()
        }
    return result


def run(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.expanduser().resolve()
    catalog_path = args.catalog.expanduser().resolve()
    output = args.output.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog = build_catalog_index(catalog_path)
    start = date.fromisoformat(manifest["start_date"])
    end = date.fromisoformat(manifest["end_date"])
    if start > end:
        raise ValueError("EVDS manifest baslangici bitisten sonra.")
    selections = manifest.get("series")
    if not isinstance(selections, list) or not selections:
        raise ValueError("EVDS manifestinde seri secimi yok.")
    codes = [str(item["series_code"]) for item in selections]
    if len(codes) != len(set(codes)):
        raise ValueError("EVDS manifestinde tekrarlanan seri kodu var.")
    missing_catalog = sorted(set(codes) - set(catalog))
    if missing_catalog:
        raise ValueError(f"EVDS katalogunda bulunmayan seriler: {missing_catalog}")

    config = {
        "endpoint": ENDPOINT,
        "dataset_id": manifest["dataset_id"],
        "manifest_file": manifest_path.name,
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "catalog_file": catalog_path.name,
        "catalog_sha256": sha256_bytes(catalog_path.read_bytes()),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "series_count": len(codes),
        "transport": args.transport,
        "tls_verification": True,
    }
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    config_path = output / "request_config.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if existing != config:
            raise ValueError("Cikti klasoru farkli bir EVDS manifestiyle kullanilmis.")
    atomic_json(config_path, config)

    all_rows: list[dict[str, Any]] = []
    request_manifest: list[dict[str, Any]] = []
    series_records: list[dict[str, Any]] = []
    for selection in selections:
        code = str(selection["series_code"])
        metadata = catalog[code]
        if metadata.get("is_archive"):
            raise ValueError(f"Aktif manifestte arsiv seri kullaniliyor: {code}")
        frequency = str(metadata.get("frequency"))
        if frequency not in FREQUENCY_CODES:
            raise ValueError(f"Desteklenmeyen EVDS frekansi: {code}, {frequency}")
        aggregation = str(
            selection.get("aggregation")
            or metadata.get("default_aggregation")
            or "avg"
        )
        if aggregation not in {"avg", "sum", "last", "first", "min", "max"}:
            raise ValueError(f"Desteklenmeyen EVDS toplulastirmasi: {code}, {aggregation}")
        series_rows: list[dict[str, Any]] = []
        chunk_records = []
        for chunk_start, chunk_end in request_chunks(start, end, frequency):
            safe_code = code.replace(".", "_")
            stem = f"{safe_code}_{chunk_start.isoformat()}_{chunk_end.isoformat()}"
            request_path = raw_dir / f"{stem}_request.json"
            response_path = raw_dir / f"{stem}_response.json.gz"
            info_path = raw_dir / f"{stem}_info.json"
            request = request_payload(
                code, aggregation, frequency, chunk_start, chunk_end
            )
            body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            cached = False
            if response_path.exists() and info_path.exists():
                raw = gzip.decompress(response_path.read_bytes())
                info = json.loads(info_path.read_text(encoding="utf-8"))
                cached = (
                    info.get("status") == "validated"
                    and info.get("response_sha256") == sha256_bytes(raw)
                    and info.get("request_sha256") == sha256_bytes(body)
                )
            if not cached:
                last_error: Exception | None = None
                for attempt in range(1, args.retries + 2):
                    try:
                        raw, http_info = post_json(body, args.timeout, args.transport)
                        if not 200 <= int(http_info["http_status"]) < 300:
                            raise ValueError(f"EVDS HTTP {http_info['http_status']}")
                        parsed_rows, response_meta = parse_response(
                            raw, code, frequency, chunk_start, chunk_end
                        )
                        last_error = None
                        break
                    except Exception as exc:  # noqa: BLE001 - retry and persist failure
                        last_error = exc
                        if attempt <= args.retries:
                            time.sleep(min(2**attempt, 8))
                if last_error is not None:
                    failure = {
                        "status": "failed",
                        "series_code": code,
                        "chunk_start": chunk_start.isoformat(),
                        "chunk_end": chunk_end.isoformat(),
                        "error": str(last_error),
                        "completed_at_utc": utc_now(),
                    }
                    atomic_json(info_path, failure)
                    request_manifest.append(failure)
                    atomic_json(output / "manifest.json", request_manifest)
                    print(f"DURDU {code} {chunk_start}/{chunk_end}: {last_error}", flush=True)
                    return 1
                response_path.write_bytes(gzip.compress(raw, compresslevel=9))
            else:
                parsed_rows, response_meta = parse_response(
                    raw, code, frequency, chunk_start, chunk_end
                )
                http_info = {
                    "http_status": info.get("http_status"),
                    "final_url": info.get("final_url", ENDPOINT),
                    "content_type": info.get("content_type", "application/json"),
                    "bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                    "transport": info.get("transport", args.transport),
                    "tls_verification": True,
                }

            source_response = str(Path("raw") / response_path.name)
            source_request = str(Path("raw") / request_path.name)
            record = {
                "status": "validated",
                "series_code": code,
                "frequency": frequency,
                "aggregation": aggregation,
                "chunk_start": chunk_start.isoformat(),
                "chunk_end": chunk_end.isoformat(),
                "request_file": source_request,
                "request_sha256": sha256_bytes(body),
                "response_file": source_response,
                "response_sha256": sha256_bytes(raw),
                "served_from_cache": cached,
                **http_info,
                **response_meta,
            }
            atomic_json(info_path, record)
            request_manifest.append(record)
            atomic_json(output / "manifest.json", request_manifest)
            for row in parsed_rows:
                row.update(
                    {
                        "role": selection.get("role"),
                        "reason": selection.get("reason"),
                        "series_name_tr": metadata.get("series_name_tr"),
                        "series_name_en": metadata.get("series_name_en"),
                        "group_code": metadata.get("group_code"),
                        "native_frequency": frequency,
                        "unit": metadata.get("unit"),
                        "default_aggregation": metadata.get("default_aggregation"),
                        "source": metadata.get("source"),
                        "source_request_file": source_request,
                        "source_request_sha256": sha256_bytes(body),
                        "source_response_file": source_response,
                        "source_response_sha256": sha256_bytes(raw),
                    }
                )
            series_rows.extend(parsed_rows)
            chunk_records.append(record)
            print(
                f"{code} {chunk_start}/{chunk_end}: {len(parsed_rows)} gozlem"
                + (" (kayitli)" if cached else ""),
                flush=True,
            )
            if not cached:
                time.sleep(args.delay)

        key_pairs = [(row["series_code"], row["period"]) for row in series_rows]
        if len(key_pairs) != len(set(key_pairs)):
            raise ValueError(f"EVDS seri-donem tekrari var: {code}")
        all_rows.extend(series_rows)
        non_null_rows = [row for row in series_rows if not row["is_missing"]]
        series_records.append(
            {
                **selection,
                **metadata,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "request_frequency_code": FREQUENCY_CODES[frequency],
                "aggregation_used": aggregation,
                "chunk_count": len(chunk_records),
                "observation_count": len(series_rows),
                "non_null_observation_count": len(non_null_rows),
                "missing_observation_count": len(series_rows) - len(non_null_rows),
                "first_period": series_rows[0]["period"] if series_rows else None,
                "last_period": series_rows[-1]["period"] if series_rows else None,
                "first_non_null_period": non_null_rows[0]["period"] if non_null_rows else None,
                "last_non_null_period": non_null_rows[-1]["period"] if non_null_rows else None,
            }
        )

    observations = pd.DataFrame(all_rows)
    if observations.empty:
        raise ValueError("EVDS manifestinden hic gozlem uretilmedi.")
    if observations.duplicated(["series_code", "period"]).any():
        raise ValueError("EVDS uzun tabloda seri-donem tekrari var.")
    observations = observations.sort_values(
        ["series_code", "period_start", "period"], kind="stable"
    ).reset_index(drop=True)
    observations.to_csv(output / "observations_long.csv", index=False, encoding="utf-8-sig")
    observations.to_parquet(output / "observations_long.parquet", index=False)

    series_frame = pd.DataFrame(series_records).sort_values("series_code", kind="stable")
    series_frame.to_csv(output / "series_catalog.csv", index=False, encoding="utf-8-sig")
    series_frame.to_parquet(output / "series_catalog.parquet", index=False)

    summary = {
        "status": "passed",
        "dataset_id": manifest["dataset_id"],
        "series_count": len(series_frame),
        "request_count": len(request_manifest),
        "observation_count": len(observations),
        "non_null_observation_count": int(observations["value"].notna().sum()),
        "missing_observation_count": int(observations["is_missing"].sum()),
        "first_period_start": observations["period_start"].min(),
        "last_period_end": observations["period_end"].max(),
        "frequency_counts": {
            str(key): int(value)
            for key, value in series_frame["frequency"].value_counts().items()
        },
        "series_with_no_observations": sorted(
            series_frame.loc[series_frame["observation_count"].eq(0), "series_code"].tolist()
        ),
        "series_with_no_non_null_observations": sorted(
            series_frame.loc[
                series_frame["non_null_observation_count"].eq(0), "series_code"
            ].tolist()
        ),
        "quality_policy": [
            "Series must exist uniquely in the downloaded EVDS metadata catalog.",
            "High-frequency series are requested in annual chunks to avoid silent row limits.",
            "Response totalCount must equal the returned item count for every request.",
            "Observations outside the requested interval are filtered locally and reported.",
            "Null source observations remain null and are never converted to zero.",
            "Every observation retains request and response files with SHA-256 hashes.",
        ],
    }
    atomic_json(output / "validation.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data_pipeline/evds/manifests/housing_causality_v1.json"),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("data_pipeline/catalog/evds_series_catalog.parquet"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data_pipeline/evds/housing_causality_v1"),
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument(
        "--transport",
        choices=["curl", "urllib"],
        default="curl" if shutil.which("curl") else "urllib",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
