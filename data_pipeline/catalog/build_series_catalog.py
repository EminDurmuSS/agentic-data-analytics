#!/usr/bin/env python3
"""Build a validated searchable EVDS series catalog from downloaded group JSON."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent

SERIES_FIELDS = {
    "SERIE_CODE": "series_code",
    "SERIE_NAME": "series_name_tr",
    "SERIE_NAME_ENG": "series_name_en",
    "DATAGROUP_CODE": "group_code",
    "FREQUENCY_STR": "frequency",
    "DEFAULT_AGG_METHOD": "default_aggregation",
    "DEFAULT_AGG_METHOD_STR": "default_aggregation_tr",
    "UST_SERIE_CODE": "parent_series_code",
    "SEVIYE": "level",
    "SCREEN_ORDER": "screen_order",
    "SUMABLE": "can_sum",
    "AVGABLE": "can_average",
    "FIRSTABLE": "can_first",
    "LASTABLE": "can_last",
    "MINABLE": "can_min",
    "MAXABLE": "can_max",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_groups(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    groups = {row["group_code"]: row for row in rows}
    if len(groups) != len(rows):
        raise ValueError("EVDS grup katalogunda tekrarlanan group_code var.")
    return groups


def successful_manifest(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("result") == "success":
            latest[str(record["group_code"])] = record
    return latest


def build(catalog_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    groups = load_groups(catalog_dir / "evds_groups.csv")
    manifest = successful_manifest(catalog_dir / "series_catalog_manifest.jsonl")
    rows: list[dict[str, Any]] = []
    invalid_groups = []
    hash_mismatches = []

    for group_code, group in sorted(groups.items()):
        source_path = catalog_dir / "groups" / f"{group_code}.json"
        if not source_path.exists():
            continue
        record = manifest.get(group_code)
        actual_hash = sha256(source_path)
        if record and record.get("sha256") != actual_hash:
            hash_mismatches.append(group_code)
            continue
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            invalid_groups.append({"group_code": group_code, "error": "not_a_list"})
            continue
        for position, item in enumerate(payload, start=1):
            if not isinstance(item, dict) or item.get("DATAGROUP_CODE") != group_code:
                invalid_groups.append(
                    {
                        "group_code": group_code,
                        "position": position,
                        "error": "group_code_mismatch_or_invalid_row",
                    }
                )
                continue
            normalized = {
                target: item.get(source) for source, target in SERIES_FIELDS.items()
            }
            normalized.update(
                {
                    "series_catalog_key": f"{group_code}::{item.get('SERIE_CODE')}",
                    "group_name_tr": group["group_name_tr"],
                    "category_id": group["category_id"],
                    "category_path_tr": group["category_path_tr"],
                    "source": group["source"],
                    "unit": group["unit"],
                    "is_archive": group["is_archive"].lower() == "true",
                    "metadata_url": group["metadata_url"],
                    "revision_url": group["revision_url"],
                    "method_change_url": group["method_change_url"],
                    "group_catalog_sha256": actual_hash,
                    "group_downloaded_utc": record.get("downloaded_utc") if record else None,
                    "searchable_text": " | ".join(
                        str(value)
                        for value in [
                            item.get("SERIE_CODE"),
                            item.get("SERIE_NAME"),
                            item.get("SERIE_NAME_ENG"),
                            group["group_name_tr"],
                            group["category_path_tr"],
                            group["source"],
                            group["unit"],
                        ]
                        if value
                    ),
                }
            )
            rows.append(normalized)

    if invalid_groups:
        raise ValueError(f"Gecersiz EVDS seri katalog satirlari: {invalid_groups[:5]}")
    if hash_mismatches:
        raise ValueError(f"EVDS katalog hash uyusmazligi: {hash_mismatches[:5]}")
    if not rows:
        raise ValueError("Islenecek EVDS seri katalog satiri bulunamadi.")

    frame = pd.DataFrame(rows)
    if frame["series_catalog_key"].duplicated().any():
        raise ValueError("EVDS series_catalog_key tekrari var.")
    series_counts = Counter(frame["series_code"].astype(str))
    frame["is_duplicate_series_code"] = frame["series_code"].astype(str).map(
        lambda value: series_counts[value] > 1
    )
    frame = frame.sort_values(
        ["is_archive", "category_path_tr", "group_code", "screen_order", "series_code"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)

    available_groups = set(frame["group_code"].astype(str))
    summary = {
        "status": "complete" if available_groups == set(groups) else "partial",
        "declared_group_count": len(groups),
        "downloaded_group_count": len(available_groups),
        "missing_group_count": len(set(groups) - available_groups),
        "missing_groups": sorted(set(groups) - available_groups),
        "series_rows": len(frame),
        "unique_series_codes": int(frame["series_code"].nunique()),
        "duplicate_series_code_rows": int(frame["is_duplicate_series_code"].sum()),
        "archive_series_rows": int(frame["is_archive"].sum()),
        "frequency_counts": {
            str(key): int(value)
            for key, value in frame["frequency"].fillna("UNKNOWN").value_counts().items()
        },
        "source_counts": {
            str(key): int(value)
            for key, value in frame["source"].fillna("UNKNOWN").value_counts().items()
        },
        "scope": "EVDS public frontend series metadata catalog. Observation values are not included.",
    }
    return frame, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", type=Path, default=BASE_DIR)
    args = parser.parse_args()
    catalog_dir = args.catalog_dir.expanduser().resolve()
    frame, summary = build(catalog_dir)
    frame.to_csv(catalog_dir / "evds_series_catalog.csv", index=False, encoding="utf-8-sig")
    frame.to_parquet(catalog_dir / "evds_series_catalog.parquet", index=False)
    (catalog_dir / "evds_series_catalog_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
