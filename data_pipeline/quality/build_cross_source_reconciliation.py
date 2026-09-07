#!/usr/bin/env python3
"""Reconcile housing-credit stock measures across official source families.

The comparison does not force different scopes to match. BDDK monthly sector
data is checked against the sum of FinTurk domestic provinces after converting
thousand TRY to million TRY. FinTurk abroad, TBB reporting-bank scope and EVDS
are retained as separate comparison columns with explicit differences.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "processed"


def percent_difference(other: pd.Series, reference: pd.Series) -> pd.Series:
    return 100 * (other - reference) / reference


def numeric_summary(values: pd.Series) -> dict[str, Any]:
    clean = values.dropna()
    return {
        "count": len(clean),
        "minimum": float(clean.min()) if len(clean) else None,
        "maximum": float(clean.max()) if len(clean) else None,
        "mean": float(clean.mean()) if len(clean) else None,
        "maximum_absolute": float(clean.abs().max()) if len(clean) else None,
    }


def build(output_dir: Path) -> dict[str, Any]:
    bddk_path = (
        PROJECT_ROOT
        / "data_pipeline"
        / "bddk"
        / "processed"
        / "housing_credit_evds_comparison.parquet"
    )
    finturk_path = (
        PROJECT_ROOT
        / "data_pipeline"
        / "bddk"
        / "processed"
        / "finturk_all_groups_all_cities"
        / "table_03.parquet"
    )
    tbb_path = (
        PROJECT_ROOT
        / "data_pipeline"
        / "tbb"
        / "processed"
        / "housing_credit_quarterly.parquet"
    )

    bddk = pd.read_parquet(bddk_path)
    finturk = pd.read_parquet(finturk_path)
    tbb = pd.read_parquet(tbb_path)

    sector = finturk.loc[finturk["group_code"].eq(10001)].copy()
    if sector["city"].nunique() != 82 or "YURT DIŞI" not in set(sector["city"]):
        raise ValueError("FinTurk sektör coğrafya kapsamı 81 il ve YURT DIŞI değil.")
    finturk_all = (
        sector.groupby("quarter", as_index=False)["KonutKredisi"]
        .sum(min_count=1)
        .rename(columns={"KonutKredisi": "finturk_all_geographies_thousand_try"})
    )
    finturk_domestic = (
        sector.loc[~sector["city"].eq("YURT DIŞI")]
        .groupby("quarter", as_index=False)["KonutKredisi"]
        .sum(min_count=1)
        .rename(columns={"KonutKredisi": "finturk_domestic_provinces_thousand_try"})
    )
    finturk_abroad = (
        sector.loc[sector["city"].eq("YURT DIŞI"), ["quarter", "KonutKredisi"]]
        .rename(columns={"KonutKredisi": "finturk_abroad_thousand_try"})
        .reset_index(drop=True)
    )

    quarterly = bddk.loc[
        bddk["month"].str.endswith(("-03", "-06", "-09", "-12"))
    ].copy()
    quarterly = quarterly.merge(
        finturk_all, left_on="month", right_on="quarter", validate="one_to_one"
    )
    quarterly = quarterly.merge(finturk_domestic, on="quarter", validate="one_to_one")
    quarterly = quarterly.merge(finturk_abroad, on="quarter", validate="one_to_one")
    quarterly = quarterly.merge(
        tbb[
            [
                "quarter",
                "balance_amount_million_try",
                "reporting_bank_count",
                "source_scope",
            ]
        ],
        on="quarter",
        how="left",
        validate="one_to_one",
    )
    quarterly["finturk_all_geographies_million_try"] = (
        quarterly["finturk_all_geographies_thousand_try"] / 1000
    )
    quarterly["finturk_domestic_provinces_million_try"] = (
        quarterly["finturk_domestic_provinces_thousand_try"] / 1000
    )
    quarterly["finturk_abroad_million_try"] = (
        quarterly["finturk_abroad_thousand_try"] / 1000
    )
    reference = quarterly["bddk_housing_credit_stock_million_tl"]
    quarterly["finturk_domestic_minus_bddk_million_try"] = (
        quarterly["finturk_domestic_provinces_million_try"] - reference
    )
    quarterly["finturk_domestic_minus_bddk_pct"] = percent_difference(
        quarterly["finturk_domestic_provinces_million_try"], reference
    )
    quarterly["finturk_all_minus_bddk_pct"] = percent_difference(
        quarterly["finturk_all_geographies_million_try"], reference
    )
    quarterly["tbb_minus_bddk_pct"] = percent_difference(
        quarterly["balance_amount_million_try"], reference
    )
    quarterly["evds_minus_bddk_pct"] = percent_difference(
        quarterly["evds_housing_credit_stock_million_tl"], reference
    )
    quarterly["comparison_interpretation"] = (
        "FinTurk domestic provinces are directly reconcilable after unit conversion; "
        "FinTurk abroad and TBB reporting-bank scope remain separate."
    )

    selected_columns = [
        "quarter",
        "bddk_housing_credit_stock_million_tl",
        "finturk_domestic_provinces_million_try",
        "finturk_abroad_million_try",
        "finturk_all_geographies_million_try",
        "evds_housing_credit_stock_million_tl",
        "balance_amount_million_try",
        "reporting_bank_count",
        "finturk_domestic_minus_bddk_million_try",
        "finturk_domestic_minus_bddk_pct",
        "finturk_all_minus_bddk_pct",
        "evds_minus_bddk_pct",
        "tbb_minus_bddk_pct",
        "source_scope",
        "comparison_interpretation",
    ]
    quarterly = quarterly[selected_columns].sort_values("quarter", kind="stable")

    if len(quarterly) != 22:
        raise ValueError(f"Beklenen 22 çeyrek yerine {len(quarterly)} çeyrek bulundu.")
    if quarterly["quarter"].duplicated().any():
        raise ValueError("Çapraz kaynak karşılaştırmasında çeyrek anahtarı tekrarlanıyor.")
    domestic_diff = quarterly["finturk_domestic_minus_bddk_pct"].abs()
    if (domestic_diff > 0.05).any():
        example = quarterly.loc[domestic_diff.idxmax()]
        raise ValueError(
            "FinTurk yurt içi il toplamı ile BDDK aylık toplamı beklenenden fazla ayrışıyor: "
            f"{example['quarter']} %{example['finturk_domestic_minus_bddk_pct']:.6f}"
        )

    monthly_evds_flagged = bddk.loc[bddk["source_scope_review_required"]]
    if monthly_evds_flagged["month"].tolist() != ["2025-08"]:
        raise ValueError("BDDK-EVDS bilinen kapsam uyarısı beklenmedik biçimde değişti.")

    output_dir.mkdir(parents=True, exist_ok=True)
    quarterly.to_csv(
        output_dir / "housing_credit_stock_reconciliation.csv",
        index=False,
        encoding="utf-8-sig",
    )
    quarterly.to_parquet(
        output_dir / "housing_credit_stock_reconciliation.parquet", index=False
    )

    result = {
        "status": "passed_with_expected_scope_differences",
        "quarter_count": len(quarterly),
        "coverage_start": quarterly["quarter"].min(),
        "coverage_end": quarterly["quarter"].max(),
        "finturk_domestic_vs_bddk_pct": numeric_summary(
            quarterly["finturk_domestic_minus_bddk_pct"]
        ),
        "finturk_all_geographies_vs_bddk_pct": numeric_summary(
            quarterly["finturk_all_minus_bddk_pct"]
        ),
        "tbb_reporting_banks_vs_bddk_pct": numeric_summary(
            quarterly["tbb_minus_bddk_pct"]
        ),
        "evds_vs_bddk_quarter_end_pct": numeric_summary(
            quarterly["evds_minus_bddk_pct"]
        ),
        "monthly_evds_bddk_scope_review_periods": monthly_evds_flagged["month"].tolist(),
        "known_source_gaps": ["TBB 2026-06 report is not published in the source snapshot."],
        "interpretation": [
            "FinTurk values are converted from thousand TRY to million TRY before comparison.",
            "The sum of 81 domestic provinces closely reconciles to the BDDK monthly sector total.",
            "FinTurk YURT DIŞI is retained separately and is not silently mixed into the domestic comparison.",
            "TBB covers the banks listed in each report and is expected to be below the BDDK sector total.",
            "Cross-source differences are scope diagnostics, not automatic evidence that one source is wrong.",
        ],
    }
    (output_dir / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
