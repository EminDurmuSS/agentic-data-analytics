#!/usr/bin/env python3
"""Build and reconcile TÜİK province housing-sales observations.

The source omits rows when mortgaged sales are zero in a small number of
province-months. A zero becomes usable only when the same official export has
both total sales and other sales and those values are exactly equal. The
missing direct source row remains visible in ``direct_value`` and provenance.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_ROOT / "data_pipeline" / "tuik" / "province_housing_sales_v1"
RAW_PATH = BASE_DIR / "raw" / "province_housing_sales.csv.gz"
RESPONSE_METADATA_PATH = BASE_DIR / "response_metadata.json"
PROVINCE_DIMENSION_PATH = (
    PROJECT_ROOT / "data_pipeline" / "regional" / "processed" / "province_dimension.parquet"
)
EVDS_OBSERVATIONS_PATH = (
    PROJECT_ROOT
    / "data_pipeline"
    / "evds"
    / "regional_housing_v1"
    / "observations_long.parquet"
)
DEFAULT_OUTPUT = BASE_DIR / "processed"

DATAFLOW_ID = "DF_SATIS_SEKLI_DURUMU_ILILCE_V3+V1.0"
SOURCE_URL = (
    "https://veriportali.tuik.gov.tr/api/tr/dataflows/"
    "DF_SATIS_SEKLI_DURUMU_ILILCE_V3%2BV1.0/file/csv"
)
TARGET_START = "2020-01"
TARGET_END = "2026-06"

SOURCE_COLUMNS = {
    "frequency": "Gözlem Sıklığı (M)",
    "sale_type": "Satış Türü (_T)",
    "province": "Coğrafi Kapsam (TR100)",
    "breakdown": "Konut ve iş yeri Gösterge (2)",
    "indicator": "İstatistiksel Gösterge (MII_KSS)",
    "month": "Zaman (2013-01)",
    "value": "Gözlem",
}

METRIC_SPECS = {
    "housing_sales_total_count": ("Toplam", "Satış Şekline Göre"),
    "housing_sales_mortgaged_count": ("İpotekli Satış", "Satış Şekline Göre"),
    "housing_sales_other_count": ("Diğer Satış", "Satış Şekline Göre"),
    "housing_sales_first_hand_count": ("İlk El Satış", "Satış Durumuna Göre"),
    "housing_sales_second_hand_count": ("İkinci El Satış", "Satış Durumuna Göre"),
}

EVDS_SERIES_COLUMNS = {
    "housing_sales_total_count": "housing_sales_total_count_series_code",
    "housing_sales_mortgaged_count": "housing_sales_mortgaged_count_series_code",
    "housing_sales_first_hand_count": "housing_sales_first_hand_count_series_code",
    "housing_sales_second_hand_count": "housing_sales_second_hand_count_series_code",
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_key(value: Any) -> str:
    text = str(value).strip().replace("İ", "I").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def read_source() -> tuple[pd.DataFrame, dict[str, Any]]:
    metadata = json.loads(RESPONSE_METADATA_PATH.read_text(encoding="utf-8"))
    compressed = RAW_PATH.read_bytes()
    if sha256(compressed) != metadata["stored_gzip_sha256"]:
        raise ValueError("TÜİK gzip SHA-256 değeri metadata ile eşleşmiyor.")
    raw = gzip.decompress(compressed)
    if sha256(raw) != metadata["raw_response_sha256"]:
        raise ValueError("TÜİK ham CSV SHA-256 değeri metadata ile eşleşmiyor.")
    frame = pd.read_csv(
        RAW_PATH,
        sep=";",
        encoding="utf-8-sig",
        dtype=str,
    )
    missing_columns = sorted(set(SOURCE_COLUMNS.values()) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"TÜİK CSV sütunları eksik: {missing_columns}")
    return frame, metadata


def build_monthly_long(
    source: pd.DataFrame,
    metadata: dict[str, Any],
    dimension: pd.DataFrame,
) -> pd.DataFrame:
    rows = source.loc[
        source[SOURCE_COLUMNS["frequency"]].eq("Aylık")
        & source[SOURCE_COLUMNS["indicator"]].eq("Konut Satış Sayıları")
        & source[SOURCE_COLUMNS["month"]].between(TARGET_START, TARGET_END)
    ].copy()
    rows["numeric_value"] = pd.to_numeric(
        rows[SOURCE_COLUMNS["value"]], errors="raise"
    )

    canonical = {
        normalize_key(name): name for name in dimension["province_name"].astype(str)
    }
    if len(canonical) != 81:
        raise ValueError("İl boyutunda 81 tekil il bekleniyor.")

    selected: list[pd.DataFrame] = []
    for metric_code, (sale_type, breakdown) in METRIC_SPECS.items():
        metric_rows = rows.loc[
            rows[SOURCE_COLUMNS["sale_type"]].eq(sale_type)
            & rows[SOURCE_COLUMNS["breakdown"]].eq(breakdown),
            [SOURCE_COLUMNS["province"], SOURCE_COLUMNS["month"], "numeric_value"],
        ].copy()
        metric_rows["metric_code"] = metric_code
        selected.append(metric_rows)
    direct = pd.concat(selected, ignore_index=True).rename(
        columns={
            SOURCE_COLUMNS["province"]: "source_province_name",
            SOURCE_COLUMNS["month"]: "month",
            "numeric_value": "direct_value",
        }
    )
    direct["province_name"] = direct["source_province_name"].map(normalize_key).map(
        canonical
    )
    if direct["province_name"].isna().any():
        unknown = sorted(direct.loc[direct["province_name"].isna(), "source_province_name"].unique())
        raise ValueError(f"TÜİK il adları boyutla eşleşmedi: {unknown}")
    if direct.duplicated(["province_name", "month", "metric_code"]).any():
        raise ValueError("TÜİK il-ay-metrik anahtarı tekrarlanıyor.")

    grid = (
        dimension[["province_name", "province_key"]]
        .merge(
            pd.DataFrame(
                {
                    "month": pd.period_range(
                        TARGET_START, TARGET_END, freq="M"
                    ).astype(str)
                }
            ),
            how="cross",
        )
        .merge(pd.DataFrame({"metric_code": list(METRIC_SPECS)}), how="cross")
    )
    result = grid.merge(
        direct[["province_name", "month", "metric_code", "direct_value"]],
        on=["province_name", "month", "metric_code"],
        how="left",
        validate="one_to_one",
    )
    result["value"] = result["direct_value"]
    result["value_origin"] = result["direct_value"].notna().map(
        {True: "direct_source_row", False: "source_row_absent"}
    )
    result["derivation"] = ""

    identity = result.loc[
        result["metric_code"].isin(
            [
                "housing_sales_total_count",
                "housing_sales_other_count",
                "housing_sales_mortgaged_count",
            ]
        )
    ].pivot(
        index=["province_name", "month"],
        columns="metric_code",
        values="direct_value",
    )
    candidates = identity.loc[
        identity["housing_sales_mortgaged_count"].isna()
        & identity["housing_sales_total_count"].notna()
        & identity["housing_sales_other_count"].notna()
        & identity["housing_sales_total_count"].eq(
            identity["housing_sales_other_count"]
        )
    ].reset_index()[["province_name", "month"]]
    fallback_keys = pd.MultiIndex.from_frame(candidates)
    result_keys = pd.MultiIndex.from_frame(result[["province_name", "month"]])
    fallback_mask = (
        result["metric_code"].eq("housing_sales_mortgaged_count")
        & result["direct_value"].isna()
        & result_keys.isin(fallback_keys)
    )
    result.loc[fallback_mask, "value"] = 0.0
    result.loc[fallback_mask, "value_origin"] = (
        "official_identity_total_equals_other_implies_zero_mortgaged"
    )
    result.loc[fallback_mask, "derivation"] = (
        "housing_sales_total_count - housing_sales_other_count"
    )
    result["source_row_present"] = result["direct_value"].notna()
    result["is_identity_derived"] = fallback_mask
    result["source_dataflow_id"] = DATAFLOW_ID
    result["source_url"] = SOURCE_URL
    result["source_csv_file"] = RAW_PATH.relative_to(PROJECT_ROOT).as_posix()
    result["source_csv_sha256"] = metadata["raw_response_sha256"]
    result["unit"] = "count"
    result["native_frequency"] = "monthly"
    return result.sort_values(
        ["province_key", "month", "metric_code"], kind="stable"
    ).reset_index(drop=True)


def reconcile_evds(
    monthly: pd.DataFrame,
    evds: pd.DataFrame,
    dimension: pd.DataFrame,
) -> pd.DataFrame:
    mappings: list[pd.DataFrame] = []
    for metric_code, source_column in EVDS_SERIES_COLUMNS.items():
        mapping = dimension[["province_name", "province_key", source_column]].rename(
            columns={source_column: "series_code"}
        )
        mapping["metric_code"] = metric_code
        mappings.append(mapping)
    mapping = pd.concat(mappings, ignore_index=True)
    source = evds.merge(mapping, on="series_code", how="inner", validate="many_to_one")
    source = source[["province_name", "province_key", "period", "metric_code", "value"]].rename(
        columns={"period": "month", "value": "evds_value"}
    )
    tuik = monthly.loc[
        monthly["metric_code"].isin(EVDS_SERIES_COLUMNS),
        [
            "province_name",
            "province_key",
            "month",
            "metric_code",
            "direct_value",
            "value",
            "value_origin",
            "source_csv_sha256",
        ],
    ].rename(columns={"value": "tuik_usable_value"})
    result = source.merge(
        tuik,
        on=["province_name", "province_key", "month", "metric_code"],
        how="outer",
        validate="one_to_one",
    )
    result["difference_direct"] = result["evds_value"] - result["direct_value"]
    result["reconciliation_status"] = "unclassified"
    exact = result["evds_value"].notna() & result["direct_value"].notna()
    result.loc[exact & result["difference_direct"].eq(0), "reconciliation_status"] = (
        "exact_match"
    )
    result.loc[exact & result["difference_direct"].ne(0), "reconciliation_status"] = (
        "value_mismatch"
    )
    fallback = result["evds_value"].isna() & result["value_origin"].eq(
        "official_identity_total_equals_other_implies_zero_mortgaged"
    )
    result.loc[fallback, "reconciliation_status"] = "evds_null_tuik_identity_zero"
    result.loc[
        result["evds_value"].isna()
        & result["direct_value"].isna()
        & ~fallback,
        "reconciliation_status",
    ] = "missing_in_both"
    result.loc[
        result["evds_value"].notna() & result["direct_value"].isna(),
        "reconciliation_status",
    ] = "tuik_direct_row_absent"
    result.loc[
        result["evds_value"].isna() & result["direct_value"].notna(),
        "reconciliation_status",
    ] = "evds_null_tuik_direct_value"
    return result.sort_values(
        ["province_key", "month", "metric_code"], kind="stable"
    ).reset_index(drop=True)


def build(output_dir: Path) -> dict[str, Any]:
    source, metadata = read_source()
    dimension = pd.read_parquet(PROVINCE_DIMENSION_PATH)
    evds = pd.read_parquet(EVDS_OBSERVATIONS_PATH)
    monthly = build_monthly_long(source, metadata, dimension)
    reconciliation = reconcile_evds(monthly, evds, dimension)
    fallbacks = monthly.loc[monthly["is_identity_derived"]].copy()

    expected_rows = 81 * 78 * len(METRIC_SPECS)
    exact_matches = int(reconciliation["reconciliation_status"].eq("exact_match").sum())
    mismatches = int(reconciliation["reconciliation_status"].eq("value_mismatch").sum())
    fallback_count = int(len(fallbacks))
    unresolved = int(monthly["value"].isna().sum())
    target_fallbacks = int(fallbacks["month"].between("2021-01", TARGET_END).sum())
    fallback_quarters = int(
        fallbacks.loc[fallbacks["month"].between("2021-01", TARGET_END)]
        .assign(quarter=lambda frame: pd.PeriodIndex(frame["month"], freq="M").asfreq("Q").astype(str))
        [["province_key", "quarter"]]
        .drop_duplicates()
        .shape[0]
    )

    hard_failures = any(
        [
            len(monthly) != expected_rows,
            monthly["province_key"].nunique() != 81,
            monthly["month"].nunique() != 78,
            monthly["metric_code"].nunique() != len(METRIC_SPECS),
            unresolved,
            mismatches,
            fallback_count != 10,
            not fallbacks["value"].eq(0).all(),
            exact_matches != 25_262,
        ]
    )
    status = "failed" if hard_failures else "passed"
    validation = {
        "status": status,
        "dataflow_id": DATAFLOW_ID,
        "source_url": SOURCE_URL,
        "raw_source_rows": len(source),
        "raw_source_sha256": metadata["raw_response_sha256"],
        "coverage_start": TARGET_START,
        "coverage_end": TARGET_END,
        "province_count": int(monthly["province_key"].nunique()),
        "month_count": int(monthly["month"].nunique()),
        "metric_count": int(monthly["metric_code"].nunique()),
        "processed_rows": len(monthly),
        "expected_processed_rows": expected_rows,
        "direct_source_rows": int(monthly["direct_value"].notna().sum()),
        "direct_source_rows_absent": int(monthly["direct_value"].isna().sum()),
        "identity_derived_zero_rows": fallback_count,
        "identity_derived_zero_rows_in_competition_window": target_fallbacks,
        "affected_province_quarters_in_competition_window": fallback_quarters,
        "usable_value_missing_rows": unresolved,
        "evds_exact_matches": exact_matches,
        "evds_value_mismatches": mismatches,
        "quality_policy": [
            "The exact TÜİK CSV response is retained as deterministic gzip with SHA-256 metadata.",
            "Processing is cut at 2026-06 even when the source export contains later observations.",
            "A missing mortgaged-sales row becomes zero only when official total sales equals official other sales for the same province-month.",
            "The absent direct row remains null in direct_value and the usable zero is explicitly labelled as identity-derived.",
            "All common direct TÜİK and EVDS province-month observations must match exactly.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(
        output_dir / "monthly_sales_long.csv", index=False, encoding="utf-8-sig"
    )
    monthly.to_parquet(output_dir / "monthly_sales_long.parquet", index=False)
    reconciliation.to_csv(
        output_dir / "evds_reconciliation.csv", index=False, encoding="utf-8-sig"
    )
    reconciliation.to_parquet(output_dir / "evds_reconciliation.parquet", index=False)
    fallbacks.to_csv(
        output_dir / "identity_zero_fallbacks.csv", index=False, encoding="utf-8-sig"
    )
    fallbacks.to_parquet(output_dir / "identity_zero_fallbacks.parquet", index=False)
    atomic_json(output_dir / "validation.json", validation)
    if status != "passed":
        raise ValueError(f"TÜİK il konut satış doğrulaması geçmedi: {validation}")
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
