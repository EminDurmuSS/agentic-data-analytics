#!/usr/bin/env python3
"""Normalize and validate downloaded BDDK weekly bulletin tables.

The downloader preserves every source HTML page and every source cell. This
builder adds a semantic row layer: source sequence, source label, currency
dimension and numeric value in the unit declared by the page. It does not
rename source measures into domain concepts or fill missing observations.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.BDDK_Haftalik_Indirme_Araci import Period, parse_page


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "weekly_all_groups"
DEFAULT_OUTPUT = BASE_DIR / "processed" / "weekly_all_groups"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_number(value: str, context: str) -> float | None:
    text = value.strip().replace("\u00a0", "")
    if text in {"", "-", "--", "n.a.", "N/A"}:
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1]
    text = text.replace("%", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1 or (
        text.count(".") == 1 and len(text.rsplit(".", 1)[1]) == 3
    ):
        text = text.replace(".", "")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"BDDK haftalik sayisal deger okunamadi: {context}={value!r}") from exc
    return -number if negative_parentheses else number


def normalize_dimension(value: str) -> str:
    normalized = value.strip().casefold()
    mapping = {
        "tp": "TRY",
        "yp": "FX",
        "toplam": "TOTAL",
    }
    return mapping.get(normalized, value.strip())


def parse_source(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})_table(\d+)_group(\d+)\.html\.gz", path.name
    )
    if match is None:
        raise ValueError(f"Beklenmeyen BDDK haftalik dosya adi: {path.name}")
    observation_date = date.fromisoformat(match.group(1))
    table_id = int(match.group(2))
    group_code = int(match.group(3))
    info_path = path.with_name(path.name.removesuffix(".html.gz") + "_info.json")
    if not info_path.exists():
        raise ValueError(f"BDDK haftalik istek bilgisi yok: {info_path}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    raw = gzip.decompress(path.read_bytes())
    if info.get("status") != "validated":
        raise ValueError(f"BDDK haftalik kaynak dogrulanmamis: {info_path}")
    if info.get("sha256_uncompressed") != sha256_bytes(raw):
        raise ValueError(f"BDDK haftalik kaynak hash'i uyusmuyor: {path}")
    if (
        info.get("observation_date") != observation_date.isoformat()
        or int(info.get("table_id")) != table_id
        or int(info.get("group_code")) != group_code
    ):
        raise ValueError(f"BDDK haftalik dosya adi ve metadata uyusmuyor: {path}")

    period = Period(
        period_id=int(info["period_id"]),
        observation_date=observation_date,
        week_number=int(info["week_number"]),
        source_label=str(info["source_period_label"]),
    )
    html = raw.decode("utf-8")
    headers, rows, parsed_metadata = parse_page(html, period, table_id, group_code)
    if headers != info.get("headers") or len(rows) != int(info.get("rows")):
        raise ValueError(f"BDDK haftalik yeniden ayrisma sonucu degisti: {path}")
    if len(headers) < 3:
        raise ValueError(f"BDDK haftalik tablo semasi cok dar: {path}")

    source_file = str(Path("raw") / path.name)
    source_info_file = str(Path("raw") / info_path.name)
    measurements: list[dict[str, Any]] = []
    source_sequences = []
    for row_index, row in enumerate(rows, start=1):
        if len(row) != len(headers):
            raise ValueError(f"BDDK haftalik satir genisligi degisti: {path}, {row_index}")
        source_sequence = row[0].strip()
        metric_label = row[1].strip()
        if not source_sequence or not metric_label:
            raise ValueError(f"BDDK haftalik satir kimligi bos: {path}, {row_index}")
        source_sequences.append(source_sequence)
        for column_index in range(2, len(headers)):
            raw_value = row[column_index]
            dimension = normalize_dimension(headers[column_index])
            measurements.append(
                {
                    "observation_date": observation_date.isoformat(),
                    "native_frequency": "weekly",
                    "period_id": period.period_id,
                    "week_number": period.week_number,
                    "table_id": table_id,
                    "table_name": info["table_name"],
                    "group_code": group_code,
                    "group_name": info["group_name"],
                    "metric_code": f"{table_id}:{source_sequence}",
                    "source_sequence": source_sequence,
                    "metric_label": metric_label,
                    "currency_dimension": dimension,
                    "value": parse_number(
                        raw_value,
                        f"{observation_date} table={table_id} row={source_sequence} column={dimension}",
                    ),
                    "value_raw": raw_value,
                    "source_unit": parsed_metadata.get("unit_heading"),
                    "source_file": source_file,
                    "source_sha256": sha256_bytes(raw),
                    "source_request_info_file": source_info_file,
                    "source_request_info_sha256": sha256_path(info_path),
                }
            )
    if len(source_sequences) != len(set(source_sequences)):
        raise ValueError(f"BDDK haftalik kaynak sira numarasi tekrarlaniyor: {path}")

    schema_payload = json.dumps(headers, ensure_ascii=False, separators=(",", ":"))
    catalog = {
        "observation_date": observation_date.isoformat(),
        "period_id": period.period_id,
        "week_number": period.week_number,
        "table_id": table_id,
        "table_name": info["table_name"],
        "group_code": group_code,
        "group_name": info["group_name"],
        "source_rows": len(rows),
        "measurement_rows": len(measurements),
        "headers_json": json.dumps(headers, ensure_ascii=False),
        "schema_sha256": hashlib.sha256(schema_payload.encode("utf-8")).hexdigest(),
        "source_unit": parsed_metadata.get("unit_heading"),
        "source_file": source_file,
        "source_sha256": sha256_bytes(raw),
        "source_request_info_file": source_info_file,
        "source_request_info_sha256": sha256_path(info_path),
    }
    return measurements, catalog


def validate_currency_totals(frame: pd.DataFrame) -> dict[str, int]:
    relevant = frame.loc[frame["currency_dimension"].isin(["TRY", "FX", "TOTAL"])]
    wide = relevant.pivot_table(
        index=["observation_date", "table_id", "group_code", "metric_code"],
        columns="currency_dimension",
        values="value",
        aggfunc="first",
    )
    complete = wide.dropna(subset=["TRY", "FX", "TOTAL"], how="any")
    differences = (complete["TRY"] + complete["FX"] - complete["TOTAL"]).abs()
    failures = differences > 1.01
    if failures.any():
        example = differences.loc[failures].index[0]
        raise ValueError(
            "BDDK haftalik TP + YP toplami uyusmuyor: "
            f"{example}, fark={differences.loc[example]}"
        )
    return {
        "checked_rows": len(complete),
        "maximum_absolute_rounding_difference": float(differences.max())
        if len(differences)
        else 0.0,
    }


def classify_missing_measurements(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Explain source nulls without changing their numeric values.

    The weekly bulletin uses ``-`` in the FX column for some measures that are
    defined only in Turkish lira.  When TRY and TOTAL are both present and
    equal, that FX cell is structurally not applicable rather than an unknown
    observation.  Every other null remains explicitly unresolved.
    """

    classified = frame.copy()
    classified["is_missing"] = classified["value"].isna()
    classified["missing_kind"] = "observed"
    classified["missing_reason"] = ""
    classified["is_structural_na"] = False
    classified["is_unresolved_missing"] = False

    index = ["observation_date", "table_id", "group_code", "metric_code"]
    relevant = classified.loc[
        classified["currency_dimension"].isin(["TRY", "FX", "TOTAL"])
    ]
    wide = relevant.pivot(index=index, columns="currency_dimension", values="value")
    structural_keys = wide.loc[
        wide.get("FX").isna()
        & wide.get("TRY").notna()
        & wide.get("TOTAL").notna()
        & (wide.get("TRY") - wide.get("TOTAL")).abs().le(1.01)
    ].index
    key_index = pd.MultiIndex.from_frame(classified[index])
    structural_mask = (
        classified["is_missing"]
        & classified["currency_dimension"].eq("FX")
        & key_index.isin(structural_keys)
    )
    unresolved_mask = classified["is_missing"] & ~structural_mask

    classified.loc[structural_mask, "missing_kind"] = "source_not_applicable"
    classified.loc[structural_mask, "missing_reason"] = (
        "Source reports '-' for the FX component while TRY equals TOTAL; "
        "the measure is Turkish-lira-only."
    )
    classified.loc[structural_mask, "is_structural_na"] = True
    classified.loc[unresolved_mask, "missing_kind"] = "source_not_reported"
    classified.loc[unresolved_mask, "missing_reason"] = (
        "Source cell is empty and no structural rule explains it."
    )
    classified.loc[unresolved_mask, "is_unresolved_missing"] = True

    counts = {
        "raw_missing_measurements": int(classified["is_missing"].sum()),
        "structural_not_applicable_measurements": int(
            classified["is_structural_na"].sum()
        ),
        "unresolved_missing_measurements": int(
            classified["is_unresolved_missing"].sum()
        ),
    }
    if counts["raw_missing_measurements"] != (
        counts["structural_not_applicable_measurements"]
        + counts["unresolved_missing_measurements"]
    ):
        raise ValueError("BDDK haftalik eksik deger siniflandirmasi tutarsiz.")
    return classified, counts


def validate_source_inventory(
    config: dict[str, Any],
    download_summary: dict[str, Any],
    manifest: list[dict[str, Any]],
    raw_dir: Path,
) -> dict[str, Any]:
    expected_groups = {int(value) for value in config["groups"]}
    expected_tables = {int(value) for value in config["tables"]}
    expected_period_count = int(config["period_count"])
    expected_request_count = (
        expected_period_count * len(expected_tables) * len(expected_groups)
    )
    if int(download_summary["expected_requests"]) != expected_request_count:
        raise ValueError("BDDK haftalik config ve indirme ozeti kapsam sayisi uyusmuyor.")

    validated = [item for item in manifest if item.get("status") == "validated"]
    keys = [
        (
            str(item["observation_date"]),
            int(item["table_id"]),
            int(item["group_code"]),
        )
        for item in validated
    ]
    if len(keys) != expected_request_count or len(keys) != len(set(keys)):
        raise ValueError("BDDK haftalik manifest kapsami eksik veya tekrarli.")

    manifest_dates = {key[0] for key in keys}
    manifest_tables = {key[1] for key in keys}
    manifest_groups = {key[2] for key in keys}
    if len(manifest_dates) != expected_period_count:
        raise ValueError("BDDK haftalik manifest donem sayisi config ile uyusmuyor.")
    if manifest_tables != expected_tables or manifest_groups != expected_groups:
        raise ValueError("BDDK haftalik manifest tablo veya grup kapsami uyusmuyor.")
    if min(manifest_dates) < config["start"] or max(manifest_dates) > config["end"]:
        raise ValueError("BDDK haftalik manifest tarihi istenen aralik disinda.")

    combination_counts = Counter((table_id, group_code) for _, table_id, group_code in keys)
    if any(count != expected_period_count for count in combination_counts.values()):
        raise ValueError("BDDK haftalik tablo-grup donem kapsami eksik.")
    if len(combination_counts) != len(expected_tables) * len(expected_groups):
        raise ValueError("BDDK haftalik tablo-grup kombinasyonu eksik.")

    expected_raw_names = {
        f"{observation_date}_table{table_id}_group{group_code}.html.gz"
        for observation_date, table_id, group_code in keys
    }
    expected_info_names = {
        name.removesuffix(".html.gz") + "_info.json"
        for name in expected_raw_names
    }
    actual_raw_names = {path.name for path in raw_dir.glob("*.html.gz")}
    actual_info_names = {path.name for path in raw_dir.glob("*_info.json")}
    if actual_raw_names != expected_raw_names:
        raise ValueError("BDDK haftalik ham HTML envanteri manifest ile uyusmuyor.")
    if actual_info_names != expected_info_names:
        raise ValueError("BDDK haftalik request-info envanteri manifest ile uyusmuyor.")

    return {
        "expected_request_count": expected_request_count,
        "manifest_key_count": len(keys),
        "period_count": len(manifest_dates),
        "table_group_combinations": len(combination_counts),
        "raw_html_file_count": len(actual_raw_names),
        "request_info_file_count": len(actual_info_names),
    }


def build(
    input_dir: Path, output_dir: Path, write_csv: bool = False
) -> dict[str, Any]:
    config_path = input_dir / "request_config.json"
    summary_path = input_dir / "summary.json"
    manifest_path = input_dir / "manifest.json"
    if not all(path.exists() for path in [config_path, summary_path, manifest_path]):
        raise ValueError("BDDK haftalik config, summary veya manifest dosyasi bulunamadi.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    download_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if download_summary.get("status") != "complete":
        raise ValueError("Tamamlanmamis BDDK haftalik indirmesi islenemez.")
    inventory = validate_source_inventory(
        config, download_summary, manifest, input_dir / "raw"
    )
    raw_files = sorted((input_dir / "raw").glob("*.html.gz"))

    rows = []
    catalog_rows = []
    for path in raw_files:
        parsed, catalog = parse_source(path)
        rows.extend(parsed)
        catalog_rows.append(catalog)
    measurements = pd.DataFrame(rows).sort_values(
        [
            "observation_date",
            "table_id",
            "group_code",
            "source_sequence",
            "currency_dimension",
        ],
        kind="stable",
    )
    key = [
        "observation_date",
        "table_id",
        "group_code",
        "metric_code",
        "currency_dimension",
    ]
    if measurements.duplicated(key).any():
        raise ValueError("BDDK haftalik olcum anahtari tekrarlaniyor.")
    measurements, missingness = classify_missing_measurements(measurements)
    catalog = pd.DataFrame(catalog_rows).sort_values(
        ["observation_date", "table_id", "group_code"], kind="stable"
    )
    if catalog.duplicated(["observation_date", "table_id", "group_code"]).any():
        raise ValueError("BDDK haftalik kaynak katalog anahtari tekrarlaniyor.")

    currency_check = validate_currency_totals(measurements)
    wide = measurements.pivot(
        index=[
            "observation_date",
            "native_frequency",
            "period_id",
            "week_number",
            "table_id",
            "table_name",
            "group_code",
            "group_name",
            "metric_code",
            "source_sequence",
            "metric_label",
            "source_unit",
            "source_file",
            "source_sha256",
            "source_request_info_file",
            "source_request_info_sha256",
        ],
        columns="currency_dimension",
        values="value",
    ).reset_index()
    wide.columns.name = None

    dictionary = (
        measurements[
            ["table_id", "table_name", "metric_code", "source_sequence", "metric_label", "source_unit"]
        ]
        .drop_duplicates()
        .sort_values(["table_id", "source_sequence", "metric_label"], kind="stable")
        .reset_index(drop=True)
    )
    label_variants = Counter(dictionary["metric_code"])

    output_dir.mkdir(parents=True, exist_ok=True)
    missingness_audit = (
        measurements.loc[measurements["is_missing"]]
        .groupby(
            [
                "table_id",
                "table_name",
                "metric_code",
                "metric_label",
                "currency_dimension",
                "missing_kind",
                "missing_reason",
            ],
            as_index=False,
            dropna=False,
        )
        .agg(missing_measurement_count=("value", "size"))
        .sort_values(["table_id", "metric_code", "currency_dimension"], kind="stable")
    )
    if write_csv:
        measurements.to_csv(
            output_dir / "measurements_long.csv",
            index=False,
            encoding="utf-8-sig",
        )
    measurements.to_parquet(output_dir / "measurements_long.parquet", index=False)
    if write_csv:
        missingness_audit.to_csv(
            output_dir / "missingness_audit.csv",
            index=False,
            encoding="utf-8-sig",
        )
    missingness_audit.to_parquet(
        output_dir / "missingness_audit.parquet", index=False
    )
    if write_csv:
        wide.to_csv(
            output_dir / "metrics_wide.csv", index=False, encoding="utf-8-sig"
        )
    wide.to_parquet(output_dir / "metrics_wide.parquet", index=False)
    if write_csv:
        dictionary.to_csv(
            output_dir / "metric_dictionary.csv",
            index=False,
            encoding="utf-8-sig",
        )
    dictionary.to_parquet(output_dir / "metric_dictionary.parquet", index=False)
    if write_csv:
        catalog.to_csv(
            output_dir / "source_table_catalog.csv",
            index=False,
            encoding="utf-8-sig",
        )
    catalog.to_parquet(output_dir / "source_table_catalog.parquet", index=False)

    table_summaries = {}
    for table_id, group in measurements.groupby("table_id"):
        source_group = catalog.loc[catalog["table_id"].eq(table_id)]
        table_summaries[str(int(table_id))] = {
            "table_name": group["table_name"].iloc[0],
            "source_pages": len(source_group),
            "measurement_rows": len(group),
            "metric_codes": int(group["metric_code"].nunique()),
            "metric_label_variants": int(
                dictionary.loc[dictionary["table_id"].eq(table_id), "metric_label"].nunique()
            ),
            "min_source_rows": int(source_group["source_rows"].min()),
            "max_source_rows": int(source_group["source_rows"].max()),
            "schema_versions": int(source_group["schema_sha256"].nunique()),
        }

    result = {
        "status": "passed",
        "source_download_status": download_summary["status"],
        "source_inventory": inventory,
        "source_page_count": len(catalog),
        "period_count": int(catalog["observation_date"].nunique()),
        "table_count": int(catalog["table_id"].nunique()),
        "group_count": int(catalog["group_code"].nunique()),
        "measurement_row_count": len(measurements),
        "wide_row_count": len(wide),
        "csv_written": write_csv,
        "primary_output_format": "parquet",
        "missing_measurement_count": int(measurements["value"].isna().sum()),
        "missingness": missingness,
        "missingness_audit_rows": len(missingness_audit),
        "metric_codes_with_label_changes": sorted(
            key for key, count in label_variants.items() if count > 1
        ),
        "currency_total_check": currency_check,
        "tables": table_summaries,
        "requested_scope": config,
        "quality_policy": [
            "Every HTML source page is reparsed and hash-checked.",
            "Source sequence and labels are preserved instead of guessed from row position.",
            "Turkish thousands and decimal formatting is normalized without filling missing cells.",
            "TL-only measures with source '-' in FX are marked source_not_applicable when TRY equals TOTAL.",
            "Structural not-applicable cells remain null; unresolved source gaps are counted separately.",
            "TRY plus FX totals are checked with a one-million-TL rounding tolerance.",
            "Metric label and schema changes remain visible in the catalogs.",
        ],
    }
    (output_dir / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Parquet dosyalarina ek olarak buyuk CSV kopyalarini da yaz.",
    )
    args = parser.parse_args()
    result = build(
        args.input.expanduser().resolve(),
        args.output.expanduser().resolve(),
        write_csv=args.write_csv,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
