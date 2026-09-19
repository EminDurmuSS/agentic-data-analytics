#!/usr/bin/env python3
"""Build first-published TÜİK province housing-sales observations.

The current bulk dataflow is a revised historical series. This builder reads
the province table attached to each contemporaneous monthly bulletin so the
first-published values remain queryable under a separate metric identity.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = (
    PROJECT_ROOT
    / "data_pipeline"
    / "tuik"
    / "province_housing_sales_first_published_v1"
)
MANIFEST_PATH = BASE_DIR / "manifest.json"
PROVINCE_DIMENSION_PATH = (
    PROJECT_ROOT / "data_pipeline" / "regional" / "processed" / "province_dimension.parquet"
)
CURRENT_SERIES_PATH = (
    PROJECT_ROOT
    / "data_pipeline"
    / "tuik"
    / "province_housing_sales_v1"
    / "processed"
    / "monthly_sales_long.parquet"
)
DEFAULT_OUTPUT = BASE_DIR / "processed"

METRIC_COLUMNS = {
    "housing_sales_total_count": "toplamtotal",
    "housing_sales_mortgaged_count": "ipoteklisatslarmortgagedsales",
    "housing_sales_other_count": "digersatslarothersales",
    "housing_sales_first_hand_count": "ilkelsatsfirsthandsale",
    "housing_sales_second_hand_count": "ikincielsatssecondhandsale",
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_key(value: Any) -> str:
    text = str(value).strip().replace("İ", "I").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def normalize_header(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"[^a-z]+", "", unicodedata.normalize("NFKD", str(value).casefold())
                  .encode("ascii", "ignore").decode())


def excel_column(index: int) -> str:
    result = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


def read_manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("TÜİK ilk-yayın manifest sürümü desteklenmiyor.")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("TÜİK ilk-yayın manifestinde kayıt yok.")
    periods = [str(item.get("period")) for item in records]
    if len(periods) != len(set(periods)):
        raise ValueError("TÜİK ilk-yayın manifestinde dönem tekrarı var.")
    return manifest


def source_bytes(record: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    workbook_path = PROJECT_ROOT / str(record["workbook_file"])
    press_path = PROJECT_ROOT / str(record["press_file"])
    workbook = workbook_path.read_bytes()
    compressed_press = press_path.read_bytes()
    if sha256(workbook) != record["workbook_sha256"]:
        raise ValueError(f"{record['period']} çalışma kitabı SHA-256 eşleşmiyor.")
    if sha256(compressed_press) != record["press_gzip_sha256"]:
        raise ValueError(f"{record['period']} bülten gzip SHA-256 eşleşmiyor.")
    press_bytes = gzip.decompress(compressed_press)
    if sha256(press_bytes) != record["press_json_sha256"]:
        raise ValueError(f"{record['period']} bülten JSON SHA-256 eşleşmiyor.")
    payload = json.loads(press_bytes)
    press = payload.get("data")
    if not isinstance(press, dict) or int(press.get("id", -1)) != int(record["press_id"]):
        raise ValueError(f"{record['period']} bülten kimliği doğrulanamadı.")
    if press.get("date") != record["release_at"] or press.get("period") != record["press_period"]:
        raise ValueError(f"{record['period']} bülten metadata alanları manifestle eşleşmiyor.")
    return workbook, press


def _header_columns(frame: pd.DataFrame) -> dict[str, int]:
    header_row = None
    for index in range(min(12, len(frame))):
        if normalize_header(frame.iloc[index, 0]) in {"illerprovinces", "illerprovince"}:
            header_row = index
            break
    if header_row is None:
        raise ValueError("İller başlık satırı bulunamadı.")
    found: dict[str, int] = {}
    normalized = [normalize_header(value) for value in frame.iloc[header_row]]
    for metric, expected in METRIC_COLUMNS.items():
        candidates = [index for index, value in enumerate(normalized) if value == expected]
        if not candidates:
            raise ValueError(f"Kaynak tabloda {metric} sütunu bulunamadı.")
        found[metric] = candidates[0]
    found["__header_row__"] = header_row
    return found


def extract_publication(
    record: dict[str, Any],
    dimension: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workbook, press = source_bytes(record)
    excel = pd.ExcelFile(BytesIO(workbook))
    if excel.sheet_names != ["t2"]:
        raise ValueError(f"{record['period']} beklenmeyen çalışma sayfaları: {excel.sheet_names}")
    frame = pd.read_excel(BytesIO(workbook), sheet_name="t2", header=None)
    columns = _header_columns(frame)
    header_row = columns.pop("__header_row__")
    canonical = {
        normalize_key(name): (name, key)
        for name, key in dimension[["province_name", "province_key"]].itertuples(index=False)
    }

    extracted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_index in range(header_row + 1, len(frame)):
        source_name = frame.iloc[row_index, 0]
        key = normalize_key(source_name)
        if key not in canonical:
            continue
        province_name, province_key = canonical[key]
        if province_key in seen:
            raise ValueError(f"{record['period']} tablosunda il tekrarı: {province_name}")
        seen.add(province_key)
        values: dict[str, int] = {}
        for metric_code, column_index in columns.items():
            value = pd.to_numeric(frame.iloc[row_index, column_index], errors="raise")
            if pd.isna(value) or float(value) < 0 or not float(value).is_integer():
                raise ValueError(
                    f"{record['period']} {province_name} {metric_code} geçersiz: {value!r}"
                )
            values[metric_code] = int(value)
            extracted.append(
                {
                    "month": record["period"],
                    "province_name": province_name,
                    "province_key": province_key,
                    "metric_code": metric_code,
                    "value": int(value),
                    "unit": "count",
                    "native_frequency": "monthly",
                    "revision_status": "first_publication",
                    "vintage_policy": "first_official_publication_for_each_reference_month",
                    "source_press_id": int(record["press_id"]),
                    "source_press_title": record["press_title"],
                    "source_press_period": record["press_period"],
                    "release_at": record["release_at"],
                    "source_press_url": record["press_page_url"],
                    "source_press_api_url": record["press_api_url"],
                    "source_download_url": record["source_download_url"],
                    "source_file": record["workbook_file"],
                    "source_sha256": record["workbook_sha256"],
                    "source_sheet": "t2",
                    "source_row_index": row_index + 1,
                    "source_column_index": column_index + 1,
                    "source_cell": f"t2!{excel_column(column_index)}{row_index + 1}",
                }
            )
        if values["housing_sales_total_count"] != (
            values["housing_sales_mortgaged_count"] + values["housing_sales_other_count"]
        ):
            raise ValueError(f"{record['period']} {province_name} satış şekli toplamı tutmuyor.")
        if values["housing_sales_total_count"] != (
            values["housing_sales_first_hand_count"] + values["housing_sales_second_hand_count"]
        ):
            raise ValueError(f"{record['period']} {province_name} satış durumu toplamı tutmuyor.")

    if len(seen) != 81:
        raise ValueError(f"{record['period']} tablosunda 81 il bekleniyor, bulunan={len(seen)}")
    if str(press.get("title")) != "Konut Satış İstatistikleri":
        raise ValueError(f"{record['period']} bülten başlığı beklenmiyor: {press.get('title')}")
    return extracted, {
        "period": record["period"],
        "press_id": int(record["press_id"]),
        "release_at": record["release_at"],
        "province_count": len(seen),
        "observation_count": len(extracted),
        "workbook_sha256": record["workbook_sha256"],
    }


def build(output_dir: Path) -> dict[str, Any]:
    manifest = read_manifest()
    dimension = pd.read_parquet(PROVINCE_DIMENSION_PATH)
    all_rows: list[dict[str, Any]] = []
    publication_audit: list[dict[str, Any]] = []
    for record in sorted(manifest["records"], key=lambda item: item["period"]):
        rows, audit = extract_publication(record, dimension)
        all_rows.extend(rows)
        publication_audit.append(audit)

    observations = pd.DataFrame(all_rows).sort_values(
        ["province_key", "month", "metric_code"], kind="stable"
    ).reset_index(drop=True)
    duplicate_count = int(
        observations.duplicated(["province_key", "month", "metric_code"]).sum()
    )
    if duplicate_count:
        raise ValueError(f"TÜİK ilk-yayın gözlem anahtarı tekrarlanıyor: {duplicate_count}")

    current = pd.read_parquet(CURRENT_SERIES_PATH)[
        ["province_key", "month", "metric_code", "value", "source_csv_sha256"]
    ].rename(
        columns={"value": "current_revised_value", "source_csv_sha256": "current_source_sha256"}
    )
    comparison = observations[
        [
            "province_key",
            "province_name",
            "month",
            "metric_code",
            "value",
            "source_press_id",
            "release_at",
            "source_sha256",
            "source_cell",
        ]
    ].rename(columns={"value": "first_published_value"}).merge(
        current,
        on=["province_key", "month", "metric_code"],
        how="left",
        validate="one_to_one",
    )
    comparison["revision_difference"] = (
        comparison["current_revised_value"] - comparison["first_published_value"]
    )
    comparison["value_changed"] = comparison["revision_difference"].ne(0)

    expected_rows = len(manifest["records"]) * 81 * len(METRIC_COLUMNS)
    istanbul = observations.loc[
        observations["province_key"].eq("istanbul")
        & observations["metric_code"].eq("housing_sales_total_count")
    ]
    annual_totals = {
        year: int(istanbul.loc[istanbul["month"].str.startswith(year), "value"].sum())
        for year in ("2023", "2024")
    }
    expected_annual_totals = {"2023": 198_739, "2024": 239_213}
    hard_failures = any(
        [
            len(observations) != expected_rows,
            observations["province_key"].nunique() != 81,
            observations["month"].nunique() != len(manifest["records"]),
            observations["metric_code"].nunique() != len(METRIC_COLUMNS),
            observations["value"].isna().any(),
            not comparison["current_revised_value"].notna().all(),
            annual_totals != expected_annual_totals,
        ]
    )
    validation = {
        "status": "failed" if hard_failures else "passed",
        "dataset_id": manifest["dataset_id"],
        "vintage_policy": manifest["vintage_policy"],
        "coverage_start": observations["month"].min(),
        "coverage_end": observations["month"].max(),
        "publication_count": len(manifest["records"]),
        "province_count": int(observations["province_key"].nunique()),
        "metric_count": int(observations["metric_code"].nunique()),
        "observation_count": len(observations),
        "expected_observation_count": expected_rows,
        "duplicate_observation_keys": duplicate_count,
        "current_revised_values_missing": int(comparison["current_revised_value"].isna().sum()),
        "changed_observation_count": int(comparison["value_changed"].sum()),
        "istanbul_first_published_annual_totals": annual_totals,
        "publication_audit": publication_audit,
        "quality_policy": [
            "Each monthly value is copied from the province table attached to that reference month's first official bulletin.",
            "The mutable current bulk dataflow remains a separate revised series and is never overwritten by this dataset.",
            "Every value retains bulletin ID, release time, workbook hash, worksheet and exact source cell.",
            "Total equals mortgaged plus other and also first-hand plus second-hand for every source row.",
            "No absent observation is filled, carried forward, interpolated or inferred.",
        ],
    }
    if hard_failures:
        raise ValueError(f"TÜİK ilk-yayın doğrulaması geçmedi: {validation}")

    output_dir.mkdir(parents=True, exist_ok=True)
    observations.to_csv(
        output_dir / "monthly_sales_first_published.csv",
        index=False,
        encoding="utf-8-sig",
    )
    observations.to_parquet(
        output_dir / "monthly_sales_first_published.parquet", index=False
    )
    comparison.to_csv(
        output_dir / "revision_comparison.csv", index=False, encoding="utf-8-sig"
    )
    comparison.to_parquet(output_dir / "revision_comparison.parquet", index=False)
    atomic_json(output_dir / "validation.json", validation)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    result = build(parser.parse_args().output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
