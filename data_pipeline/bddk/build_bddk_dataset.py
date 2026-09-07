#!/usr/bin/env python3
"""Normalize and validate the downloaded BDDK monthly consumer-credit table."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "monthly_consumer_credit_sector"
DEFAULT_EVDS_MONTHLY = BASE_DIR.parent / "processed" / "monthly_2021_2026.parquet"
DEFAULT_OUTPUT_DIR = BASE_DIR / "processed"

SOURCE_COLUMNS = {
    "_requested_period": "month",
    "_requested_table_no": "table_no",
    "_requested_group_code": "group_code",
    "BankaAdi": "bank_group_name_tr",
    "BasitSira": "metric_order",
    "Ad": "metric_name_tr",
    "BasitFont": "row_style",
    "Tp": "tp_million_tl",
    "Yp": "yp_million_tl",
    "Toplam": "total_million_tl",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def response_payload(path: Path) -> dict:
    payload = load_json(path)
    inner = payload.get("Json")
    for _ in range(2):
        if isinstance(inner, str):
            inner = json.loads(inner)
    if not isinstance(inner, dict):
        raise ValueError(f"Unexpected BDDK response payload: {path}")
    return inner


def validate_source_captions(raw_dir: Path) -> dict:
    response_files = sorted(
        path
        for path in raw_dir.glob("*_table04_group10001.json")
        if not path.name.endswith("_info.json")
    )
    if not response_files:
        raise ValueError(f"No BDDK raw response found in {raw_dir}")

    captions = []
    periods = []
    for path in response_files:
        payload = response_payload(path)
        caption = str(payload.get("caption", ""))
        captions.append(caption)
        match = re.search(r"Dönem:(\d{4})/(\d{1,2})", caption)
        if not match:
            raise ValueError(f"Caption does not contain a period: {caption!r}")
        periods.append(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}")

    bad_units = [caption for caption in captions if "(milyon TL)" not in caption]
    if bad_units:
        raise ValueError(f"Unexpected BDDK unit captions: {bad_units[:3]}")

    return {
        "response_files": len(response_files),
        "caption_periods": sorted(periods),
        "source_unit": "million TRY",
        "source_unit_evidence": "Every response caption contains '(milyon TL)'.",
    }


def normalize(download_dir: Path) -> tuple[pd.DataFrame, dict]:
    input_path = download_dir / "monthly_rows.csv"
    request_config_path = download_dir / "request_config.json"
    summary_path = download_dir / "summary.json"
    raw_dir = download_dir / "raw"

    source = pd.read_csv(input_path, encoding="utf-8-sig")
    missing_columns = sorted(set(SOURCE_COLUMNS) - set(source.columns))
    if missing_columns:
        raise ValueError(f"Missing BDDK columns: {missing_columns}")

    normalized = source.rename(columns=SOURCE_COLUMNS)[list(SOURCE_COLUMNS.values())].copy()
    normalized["month"] = normalized["month"].astype(str)
    normalized["row_style"] = normalized["row_style"].fillna("").astype(str)
    for column in [
        "table_no",
        "group_code",
        "metric_order",
        "tp_million_tl",
        "yp_million_tl",
        "total_million_tl",
    ]:
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype("int64")

    normalized["source"] = "BDDK Monthly Bulletin"
    normalized["source_url"] = "https://www.bddk.org.tr/BultenAylik/"
    normalized["native_frequency"] = "monthly"
    normalized["observation_type"] = "stock"
    normalized["unit"] = "million TRY"
    normalized["total_rounding_delta"] = normalized["total_million_tl"] - (
        normalized["tp_million_tl"] + normalized["yp_million_tl"]
    )

    request_config = load_json(request_config_path)
    download_summary = load_json(summary_path)
    caption_validation = validate_source_captions(raw_dir)

    requested_periods = pd.period_range(
        request_config["start"], request_config["end"], freq="M"
    ).astype(str).tolist()
    actual_periods = sorted(normalized["month"].unique().tolist())
    rows_per_period = normalized.groupby("month").size()
    duplicate_keys = int(normalized.duplicated(["month", "metric_order"]).sum())
    unstable_metric_names = (
        normalized.groupby("metric_order")["metric_name_tr"].nunique().loc[lambda s: s > 1]
    )
    max_rounding_delta = int(normalized["total_rounding_delta"].abs().max())

    checks = {
        "download_complete": download_summary.get("status") == "complete",
        "all_requests_successful": download_summary.get("successful_requests")
        == download_summary.get("expected_requests"),
        "requested_periods_match": requested_periods == actual_periods,
        "caption_periods_match": requested_periods
        == caption_validation["caption_periods"],
        "one_row_per_period_and_metric": duplicate_keys == 0,
        "stable_metric_names": unstable_metric_names.empty,
        "stable_rows_per_period": rows_per_period.nunique() == 1,
        "rounding_delta_at_most_one": max_rounding_delta <= 1,
    }
    if not all(checks.values()):
        raise ValueError(f"BDDK validation failed: {checks}")

    validation = {
        "status": "passed",
        "checks": checks,
        "periods": len(actual_periods),
        "first_period": actual_periods[0],
        "last_period": actual_periods[-1],
        "rows": len(normalized),
        "rows_per_period": int(rows_per_period.iloc[0]),
        "duplicate_keys": duplicate_keys,
        "max_total_rounding_delta_million_tl": max_rounding_delta,
        **caption_validation,
        "scope": {
            "table_no": sorted(normalized["table_no"].unique().tolist()),
            "group_code": sorted(normalized["group_code"].unique().tolist()),
            "bank_group_names": sorted(normalized["bank_group_name_tr"].unique().tolist()),
        },
        "limits": [
            "This dataset contains BDDK monthly table 4 for sector group 10001 only.",
            "It does not include BDDK weekly bulletins or FinTurk.",
            "The table contains outstanding balances, not new loan originations.",
            "A BDDK and EVDS comparison is a source-scope audit, not an equality assumption.",
        ],
    }
    return normalized.sort_values(["month", "metric_order"]).reset_index(drop=True), validation


def build_comparison(normalized: pd.DataFrame, evds_path: Path) -> tuple[pd.DataFrame, dict]:
    housing = normalized.loc[
        normalized["metric_order"].eq(2),
        [
            "month",
            "bank_group_name_tr",
            "metric_name_tr",
            "tp_million_tl",
            "yp_million_tl",
            "total_million_tl",
            "source",
            "source_url",
            "unit",
        ],
    ].copy()
    if housing["month"].duplicated().any():
        raise ValueError("Housing-credit series is not unique by month.")

    evds = pd.read_parquet(evds_path)[
        ["month", "housing_credit_three_groups_stock_million_tl"]
    ].copy()
    comparison = housing.merge(evds, on="month", how="inner", validate="one_to_one")
    comparison = comparison.rename(
        columns={
            "total_million_tl": "bddk_housing_credit_stock_million_tl",
            "housing_credit_three_groups_stock_million_tl": "evds_housing_credit_stock_million_tl",
        }
    )
    comparison["evds_minus_bddk_million_tl"] = (
        comparison["evds_housing_credit_stock_million_tl"]
        - comparison["bddk_housing_credit_stock_million_tl"]
    )
    comparison["evds_minus_bddk_pct_of_bddk"] = 100 * (
        comparison["evds_minus_bddk_million_tl"]
        / comparison["bddk_housing_credit_stock_million_tl"]
    )
    comparison["source_scope_review_required"] = comparison[
        "evds_minus_bddk_pct_of_bddk"
    ].abs().gt(1.0)

    largest_index = comparison["evds_minus_bddk_pct_of_bddk"].abs().idxmax()
    largest = comparison.loc[largest_index]
    audit = {
        "matched_months": len(comparison),
        "months_with_absolute_difference_over_one_pct": int(
            comparison["source_scope_review_required"].sum()
        ),
        "largest_absolute_difference": {
            "month": largest["month"],
            "bddk_million_tl": float(largest["bddk_housing_credit_stock_million_tl"]),
            "evds_million_tl": float(largest["evds_housing_credit_stock_million_tl"]),
            "difference_million_tl": float(largest["evds_minus_bddk_million_tl"]),
            "difference_pct_of_bddk": float(
                largest["evds_minus_bddk_pct_of_bddk"]
            ),
        },
    }
    return comparison, audit


def write_outputs(
    normalized: pd.DataFrame,
    validation: dict,
    comparison: pd.DataFrame,
    comparison_audit: dict,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(
        output_dir / "consumer_credit_monthly_long.csv",
        index=False,
        encoding="utf-8-sig",
    )
    normalized.to_parquet(output_dir / "consumer_credit_monthly_long.parquet", index=False)

    housing = comparison[
        [
            "month",
            "bank_group_name_tr",
            "metric_name_tr",
            "tp_million_tl",
            "yp_million_tl",
            "bddk_housing_credit_stock_million_tl",
            "unit",
            "source",
            "source_url",
        ]
    ].copy()
    housing.to_csv(
        output_dir / "housing_credit_monthly.csv", index=False, encoding="utf-8-sig"
    )
    housing.to_parquet(output_dir / "housing_credit_monthly.parquet", index=False)

    comparison.to_csv(
        output_dir / "housing_credit_evds_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )
    comparison.to_parquet(
        output_dir / "housing_credit_evds_comparison.parquet", index=False
    )

    validation["evds_comparison"] = comparison_audit
    atomic_json(output_dir / "validation.json", validation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--evds-monthly", type=Path, default=DEFAULT_EVDS_MONTHLY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    normalized, validation = normalize(args.download_dir.resolve())
    comparison, audit = build_comparison(normalized, args.evds_monthly.resolve())
    write_outputs(normalized, validation, comparison, audit, args.output_dir.resolve())
    print(json.dumps({**validation, "evds_comparison": audit}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
