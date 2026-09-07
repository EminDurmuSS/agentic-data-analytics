#!/usr/bin/env python3
"""Normalize and validate all downloaded BDDK monthly sector tables.

Each source table is written independently because the BDDK schemas differ by
table and can change over time. Source rows, captions, units, files and hashes
remain traceable in every output row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "monthly_all_sector"
DEFAULT_OUTPUT = BASE_DIR / "processed" / "monthly_all_sector"

TABLES = {
    1: "Bilanco",
    2: "Kar Zarar",
    3: "Krediler",
    4: "Tuketici Kredileri",
    5: "Sektorel Kredi Dagilimi",
    6: "KOBI Kredileri",
    7: "Sendikasyon Sekuritizasyon Kredileri",
    8: "Menkul Kiymetler",
    9: "Mevduat Turler Itibariyla",
    10: "Mevduat Vade Itibariyla",
    11: "Likidite Durumu",
    12: "Sermaye Yeterliligi",
    13: "Yabanci Para Pozisyonu",
    14: "Bilanco Disi Islemler",
    15: "Rasyolar",
    16: "Diger Bilgiler",
    17: "Yurt Disi Sube Rasyolari",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_payload(path: Path) -> dict[str, Any]:
    outer = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(outer, dict) or outer.get("success") is not True:
        raise ValueError(f"Gecersiz BDDK kaynak cevabi: {path}")
    payload = outer.get("Json")
    for _ in range(2):
        if isinstance(payload, str):
            payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError(f"BDDK Json tablo zarfi bulunamadi: {path}")
    return payload


def parse_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    match = re.fullmatch(
        r"(\d{4}-\d{2})_table(\d{2})_group(\d+)\.json", path.name
    )
    if not match:
        raise ValueError(f"Beklenmeyen BDDK dosya adi: {path.name}")
    requested_period = match.group(1)
    table_no = int(match.group(2))
    group_code = int(match.group(3))
    info_path = path.with_name(path.stem + "_info.json")
    if not info_path.exists():
        raise ValueError(f"BDDK kaynak istek bilgisi bulunamadi: {info_path}")
    request_info = json.loads(info_path.read_text(encoding="utf-8"))
    if request_info.get("status") != "validated":
        raise ValueError(f"BDDK kaynak istek kaydi dogrulanmamis: {info_path}")
    if request_info.get("sha256") != sha256(path):
        raise ValueError(f"BDDK kaynak dosya hash'i istek kaydiyla uyusmuyor: {path}")
    if (
        request_info.get("period") != requested_period
        or int(request_info.get("table_no")) != table_no
        or int(request_info.get("group_code")) != group_code
    ):
        raise ValueError(f"BDDK dosya adi ve istek kaydi uyusmuyor: {path}")
    payload = load_payload(path)

    models = payload.get("colModels")
    labels = payload.get("colNames")
    envelope = payload.get("data")
    source_rows = envelope.get("rows") if isinstance(envelope, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError(f"BDDK kolon modeli bos: {path}")
    if not isinstance(labels, list) or len(labels) != len(models):
        raise ValueError(f"BDDK kolon modeli ve etiketi uyusmuyor: {path}")
    names = [model.get("name") if isinstance(model, dict) else None for model in models]
    if not all(isinstance(name, str) and name for name in names):
        raise ValueError(f"BDDK bos kolon kimligi: {path}")
    if len(names) != len(set(names)):
        raise ValueError(f"BDDK tekrarlanan kolon kimligi: {path}")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError(f"BDDK veri satiri bos: {path}")

    caption = str(payload.get("caption", ""))
    period_match = re.search(r"Dönem:(\d{4})/(\d{1,2})", caption)
    caption_period = None
    period_validation_source = "validated_request_info"
    if period_match:
        caption_period = (
            f"{int(period_match.group(1)):04d}-{int(period_match.group(2)):02d}"
        )
        if caption_period != requested_period:
            raise ValueError(
                "BDDK dosya/caption donemi uyusmuyor: "
                f"{requested_period} != {caption_period}"
            )
        period_validation_source = "caption_and_validated_request_info"
    unit_match = re.search(r"\(([^()]*)\),\s*Dönem:", caption)
    source_unit = unit_match.group(1).strip() if unit_match else None
    source_file = str(Path("raw") / path.name)
    source_info_file = str(Path("raw") / info_path.name)

    parsed = []
    exact_rows = []
    for row_index, source_row in enumerate(source_rows, start=1):
        cells = source_row.get("cell") if isinstance(source_row, dict) else None
        if not isinstance(cells, list) or len(cells) != len(names):
            raise ValueError(f"BDDK hucre sayisi kolonlarla uyusmuyor: {path}")
        row = dict(zip(names, cells))
        exact_rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        parsed.append(
            {
                "month": requested_period,
                "table_no": table_no,
                "table_name": TABLES.get(table_no, f"Table {table_no}"),
                "group_code": group_code,
                "source_row_index": row_index,
                "source_caption": caption,
                "source_unit": source_unit,
                "source_file": source_file,
                "source_sha256": sha256(path),
                "source_request_info_file": source_info_file,
                "source_request_info_sha256": sha256(info_path),
                "period_validation_source": period_validation_source,
                **row,
            }
        )
    if len(exact_rows) != len(set(exact_rows)):
        raise ValueError(f"BDDK ayni kaynak dosyada birebir tekrar eden satir var: {path}")

    schema_payload = json.dumps(names, ensure_ascii=False, separators=(",", ":"))
    metadata = {
        "month": requested_period,
        "table_no": table_no,
        "group_code": group_code,
        "rows": len(parsed),
        "columns": names,
        "column_labels": labels,
        "caption": caption,
        "caption_period": caption_period,
        "source_unit": source_unit,
        "period_validation_source": period_validation_source,
        "schema_sha256": hashlib.sha256(schema_payload.encode("utf-8")).hexdigest(),
        "source_file": source_file,
        "source_sha256": sha256(path),
        "source_request_info_file": source_info_file,
        "source_request_info_sha256": sha256(info_path),
    }
    return parsed, metadata


def build(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    config_path = input_dir / "request_config.json"
    summary_path = input_dir / "summary.json"
    if not config_path.exists() or not summary_path.exists():
        raise ValueError("BDDK indirme config veya summary dosyasi bulunamadi.")
    request_config = json.loads(config_path.read_text(encoding="utf-8"))
    download_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if download_summary.get("status") != "complete":
        raise ValueError("Tamamlanmamis BDDK indirme klasoru islenemez.")

    expected_periods = pd.period_range(
        request_config["start"], request_config["end"], freq="M"
    ).astype(str).tolist()
    expected_tables = sorted(int(value) for value in request_config["tables"])
    expected_groups = sorted(int(value) for value in request_config["groups"])
    expected_files = {
        f"{period}_table{table_no:02}_group{group_code}.json"
        for period in expected_periods
        for table_no in expected_tables
        for group_code in expected_groups
    }
    actual_files = {
        path.name
        for path in (input_dir / "raw").glob("*.json")
        if not path.name.endswith("_info.json")
    }
    missing_files = sorted(expected_files - actual_files)
    unexpected_files = sorted(actual_files - expected_files)
    if missing_files or unexpected_files:
        raise ValueError(
            f"BDDK kaynak dosya envanteri uyusmuyor: missing={missing_files[:3]}, "
            f"unexpected={unexpected_files[:3]}"
        )

    rows_by_table: dict[int, list[dict[str, Any]]] = defaultdict(list)
    metadata = []
    for filename in sorted(expected_files):
        rows, source_metadata = parse_file(input_dir / "raw" / filename)
        rows_by_table[source_metadata["table_no"]].extend(rows)
        metadata.append(source_metadata)

    output_dir.mkdir(parents=True, exist_ok=True)
    table_summaries = {}
    for table_no, rows in sorted(rows_by_table.items()):
        frame = pd.DataFrame(rows)
        frame = frame.sort_values(
            ["month", "group_code", "source_row_index"], kind="stable"
        ).reset_index(drop=True)
        frame.to_csv(
            output_dir / f"table_{table_no:02}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        frame.to_parquet(output_dir / f"table_{table_no:02}.parquet", index=False)
        table_meta = [item for item in metadata if item["table_no"] == table_no]
        period_counts = Counter(item["month"] for item in table_meta)
        row_counts = [item["rows"] for item in table_meta]
        units = sorted(
            {item["source_unit"] for item in table_meta},
            key=lambda value: "" if value is None else str(value),
        )
        schemas = sorted({item["schema_sha256"] for item in table_meta})
        table_summaries[str(table_no)] = {
            "table_name": TABLES.get(table_no),
            "rows": len(frame),
            "periods": len(period_counts),
            "groups": sorted(frame["group_code"].unique().tolist()),
            "min_rows_per_source_table": min(row_counts),
            "max_rows_per_source_table": max(row_counts),
            "source_units": units,
            "schema_version_count": len(schemas),
            "schema_sha256_values": schemas,
        }

    metadata_frame = pd.DataFrame(metadata).sort_values(
        ["month", "table_no", "group_code"], kind="stable"
    )
    metadata_frame["columns_json"] = metadata_frame["columns"].map(
        lambda value: json.dumps(value, ensure_ascii=False)
    )
    metadata_frame["column_labels_json"] = metadata_frame["column_labels"].map(
        lambda value: json.dumps(value, ensure_ascii=False)
    )
    metadata_frame = metadata_frame.drop(columns=["columns", "column_labels"])
    metadata_frame.to_csv(
        output_dir / "source_table_catalog.csv", index=False, encoding="utf-8-sig"
    )
    metadata_frame.to_parquet(output_dir / "source_table_catalog.parquet", index=False)

    result = {
        "status": "passed",
        "source_download_status": download_summary.get("status"),
        "period_count": len(expected_periods),
        "table_count": len(expected_tables),
        "group_count": len(expected_groups),
        "source_file_count": len(expected_files),
        "total_rows": sum(len(rows) for rows in rows_by_table.values()),
        "tables": table_summaries,
        "quality_policy": [
            "Changing row counts and schemas are reported, not silently coerced.",
            "BasitSira is treated as display order, not as a globally unique key.",
            "Tables without a period in the source caption are validated against the signed request-info record.",
            "No missing values are imputed.",
            "Each output row retains source file, source hash, caption and unit.",
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
    args = parser.parse_args()
    result = build(args.input.expanduser().resolve(), args.output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
