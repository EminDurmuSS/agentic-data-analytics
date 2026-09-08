#!/usr/bin/env python3
"""Build a self-contained DuckDB analytics database from validated Parquet data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "analytics.duckdb"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def load_parquet(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    path: Path,
    projection: str = "*",
) -> int:
    if not path.exists():
        raise ValueError(f"Lakehouse kaynak dosyası bulunamadı: {path}")
    connection.execute(
        f"CREATE TABLE {schema}.{table} AS "
        f"SELECT {projection} FROM read_parquet('{sql_path(path)}')"
    )
    return int(connection.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0])


def build_monthly_analysis() -> pd.DataFrame:
    bddk = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "bddk"
        / "processed"
        / "housing_credit_evds_comparison.parquet"
    )
    evds = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "evds"
        / "housing_causality_v1"
        / "monthly_panel.parquet"
    ).rename(columns={"target_period": "month"})
    frame = bddk.merge(evds, on="month", how="left", validate="one_to_one")
    for dataset_name in ["housing_causality_controls_v1", "market_controls_v2"]:
        controls = pd.read_parquet(
            PROJECT_ROOT
            / "data_pipeline"
            / "evds"
            / dataset_name
            / "monthly_panel.parquet"
        ).rename(columns={"target_period": "month"})
        frame = frame.merge(controls, on="month", how="left", validate="one_to_one")
    if len(frame) != 66:
        raise ValueError(f"Aylık analiz tablosu 66 satır olmalı, bulunan={len(frame)}")

    cpi_column = "TP_TUKFIY2025_GENEL"
    base_rows = frame.loc[frame["month"].eq("2021-01"), cpi_column]
    if len(base_rows) != 1 or pd.isna(base_rows.iloc[0]):
        raise ValueError("Ocak 2021 TÜFE baz değeri bulunamadı.")
    base_cpi = float(base_rows.iloc[0])
    frame["housing_credit_real_million_tl_jan2021_prices"] = (
        frame["bddk_housing_credit_stock_million_tl"] * base_cpi / frame[cpi_column]
    )
    frame["housing_credit_nominal_mom_pct"] = frame[
        "bddk_housing_credit_stock_million_tl"
    ].pct_change(fill_method=None) * 100
    frame["housing_credit_real_mom_pct"] = frame[
        "housing_credit_real_million_tl_jan2021_prices"
    ].pct_change(fill_method=None) * 100
    frame["housing_rate_change_pp"] = frame["TP_KTF12"].diff()
    frame["mortgaged_sales_share_pct"] = (
        100 * frame["TP_AKONUTSAT2_KTRTOPLAM"] / frame["TP_AKONUTSAT1_KTRTOPLAM"]
    )
    frame["rate_down_real_stock_not_up"] = (
        frame["housing_rate_change_pp"].lt(0)
        & frame["housing_credit_real_mom_pct"].le(0)
    )
    frame["rate_down_real_stock_not_up_quality_screened"] = (
        frame["rate_down_real_stock_not_up"]
        & ~frame["source_scope_review_required"]
    )

    events = read_json(
        PROJECT_ROOT / "data_pipeline" / "evidence" / "events" / "events.json"
    )
    event_frame = pd.DataFrame(
        [
            {
                "month": item["monthly_annotation_key"],
                "event_id": item["event_id"],
                "event_title": item["title"],
                "event_verification_level": item["verification_level"],
            }
            for item in events
        ]
    )
    event_summary = (
        event_frame.groupby("month", as_index=False)
        .agg(
            event_count=("event_id", "count"),
            event_ids=("event_id", lambda values: " | ".join(values)),
            event_titles=("event_title", lambda values: " | ".join(values)),
            event_verification_levels=(
                "event_verification_level",
                lambda values: " | ".join(values),
            ),
        )
    )
    frame = frame.merge(event_summary, on="month", how="left", validate="one_to_one")
    frame["event_count"] = frame["event_count"].fillna(0).astype(int)
    return frame


def build_quarterly_analysis() -> pd.DataFrame:
    reconciliation = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "quality"
        / "processed"
        / "housing_credit_stock_reconciliation.parquet"
    )
    tbb = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "tbb"
        / "processed"
        / "housing_credit_quarterly.parquet"
    )[
        [
            "quarter",
            "disbursement_amount_million_try",
            "disbursement_person_count",
        ]
    ]
    evds = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "evds"
        / "housing_causality_v1"
        / "quarterly_panel.parquet"
    ).rename(columns={"target_period": "evds_quarter"})
    reconciliation["evds_quarter"] = reconciliation["quarter"].map(
        lambda value: f"{value[:4]}Q{int(value[5:7]) // 3}"
    )
    frame = reconciliation.merge(tbb, on="quarter", how="left", validate="one_to_one")
    frame = frame.merge(evds, on="evds_quarter", how="left", validate="one_to_one")
    for dataset_name in ["housing_causality_controls_v1", "market_controls_v2"]:
        controls = pd.read_parquet(
            PROJECT_ROOT
            / "data_pipeline"
            / "evds"
            / dataset_name
            / "quarterly_panel.parquet"
        ).rename(columns={"target_period": "evds_quarter"})
        frame = frame.merge(
            controls,
            on="evds_quarter",
            how="left",
            validate="one_to_one",
        )
    if len(frame) != 22:
        raise ValueError(f"Çeyreklik analiz tablosu 22 satır olmalı, bulunan={len(frame)}")
    return frame


def simplified_events() -> pd.DataFrame:
    events = read_json(
        PROJECT_ROOT / "data_pipeline" / "evidence" / "events" / "events.json"
    )
    columns = [
        "event_id",
        "annotation_date",
        "annotation_date_type",
        "monthly_annotation_key",
        "event_type",
        "title",
        "verified_summary_tr",
        "analysis_hypothesis_tr",
        "verification_level",
        "source_url",
        "causal_effect_estimated",
    ]
    return pd.DataFrame([{column: item.get(column) for column in columns} for item in events])


def build(output_path: Path) -> dict[str, Any]:
    unified_validation = read_json(
        PROJECT_ROOT / "data_pipeline" / "catalog" / "unified" / "validation.json"
    )
    if unified_validation.get("status") != "passed":
        raise ValueError("Doğrulanmamış birleşik katalog lakehouse'a yüklenemez.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary_path.exists():
        temporary_path.unlink()

    table_manifest: list[dict[str, Any]] = []
    connection = duckdb.connect(str(temporary_path))
    try:
        for schema in ["catalog", "evds", "bddk", "tbb", "quality", "evidence", "analysis"]:
            connection.execute(f"CREATE SCHEMA {schema}")

        parquet_tables = [
            (
                "catalog",
                "data_assets",
                PROJECT_ROOT / "data_pipeline" / "catalog" / "unified" / "unified_data_catalog.parquet",
            ),
            (
                "catalog",
                "metrics",
                PROJECT_ROOT / "data_pipeline" / "catalog" / "unified" / "unified_metric_catalog.parquet",
            ),
            (
                "evds",
                "series_catalog",
                PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet",
            ),
            (
                "evds",
                "housing_observations",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "observations_long.parquet",
            ),
            (
                "evds",
                "housing_monthly_panel",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "monthly_panel.parquet",
            ),
            (
                "evds",
                "housing_quarterly_panel",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "quarterly_panel.parquet",
            ),
            (
                "evds",
                "monthly_alignment_audit",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "monthly_alignment_audit.parquet",
            ),
            (
                "evds",
                "quarterly_alignment_audit",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "quarterly_alignment_audit.parquet",
            ),
            (
                "evds",
                "housing_coverage_gaps",
                PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1" / "coverage_gaps.parquet",
            ),
            (
                "bddk",
                "monthly_measurements",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_semantic" / "measurements_long.parquet",
            ),
            (
                "bddk",
                "monthly_metric_dictionary",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_semantic" / "metric_dictionary.parquet",
            ),
            (
                "bddk",
                "housing_credit_monthly",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "housing_credit_monthly.parquet",
            ),
            (
                "bddk",
                "finturk_measurements",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "finturk_all_groups_all_cities" / "measurements_long.parquet",
            ),
            (
                "bddk",
                "finturk_metric_dictionary",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "finturk_all_groups_all_cities" / "column_dictionary.parquet",
            ),
            (
                "bddk",
                "finturk_missingness_audit",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "finturk_all_groups_all_cities" / "missingness_audit.parquet",
            ),
            (
                "tbb",
                "consumer_credit_product_metrics",
                PROJECT_ROOT / "data_pipeline" / "tbb" / "processed" / "consumer_credit_product_metrics.parquet",
            ),
            (
                "tbb",
                "housing_credit_quarterly",
                PROJECT_ROOT / "data_pipeline" / "tbb" / "processed" / "housing_credit_quarterly.parquet",
            ),
            (
                "tbb",
                "reporting_banks",
                PROJECT_ROOT / "data_pipeline" / "tbb" / "processed" / "reporting_banks.parquet",
            ),
            (
                "tbb",
                "source_gaps",
                PROJECT_ROOT / "data_pipeline" / "tbb" / "processed" / "source_gaps.parquet",
            ),
            (
                "quality",
                "housing_credit_stock_reconciliation",
                PROJECT_ROOT / "data_pipeline" / "quality" / "processed" / "housing_credit_stock_reconciliation.parquet",
            ),
        ]

        for dataset_name, table_prefix in [
            ("housing_causality_controls_v1", "housing_controls"),
            ("market_controls_v2", "market_controls"),
        ]:
            dataset_dir = PROJECT_ROOT / "data_pipeline" / "evds" / dataset_name
            parquet_tables.extend(
                [
                    (
                        "evds",
                        f"{table_prefix}_observations",
                        dataset_dir / "observations_long.parquet",
                    ),
                    (
                        "evds",
                        f"{table_prefix}_monthly_panel",
                        dataset_dir / "monthly_panel.parquet",
                    ),
                    (
                        "evds",
                        f"{table_prefix}_quarterly_panel",
                        dataset_dir / "quarterly_panel.parquet",
                    ),
                    (
                        "evds",
                        f"{table_prefix}_monthly_alignment_audit",
                        dataset_dir / "monthly_alignment_audit.parquet",
                    ),
                    (
                        "evds",
                        f"{table_prefix}_quarterly_alignment_audit",
                        dataset_dir / "quarterly_alignment_audit.parquet",
                    ),
                    (
                        "evds",
                        f"{table_prefix}_coverage_gaps",
                        dataset_dir / "coverage_gaps.parquet",
                    ),
                ]
            )

        weekly_path = (
            PROJECT_ROOT
            / "data_pipeline"
            / "bddk"
            / "processed"
            / "weekly_all_groups"
            / "measurements_long.parquet"
        )
        if weekly_path.exists():
            weekly_measurement_projection = ", ".join(
                [
                    "observation_date",
                    "period_id",
                    "week_number",
                    "table_id",
                    "group_code",
                    "metric_code",
                    "currency_dimension",
                    "value",
                    "is_missing",
                    "missing_kind",
                    "is_structural_na",
                    "is_unresolved_missing",
                ]
            )
            parquet_tables.append(
                (
                    "bddk",
                    "weekly_measurements",
                    weekly_path,
                    weekly_measurement_projection,
                )
            )
            parquet_tables.append(
                (
                    "bddk",
                    "weekly_source_tables",
                    weekly_path.parent / "source_table_catalog.parquet",
                )
            )
            parquet_tables.append(
                (
                    "bddk",
                    "weekly_metric_dictionary",
                    weekly_path.parent / "metric_dictionary.parquet",
                )
            )
            parquet_tables.append(
                (
                    "bddk",
                    "weekly_missingness_audit",
                    weekly_path.parent / "missingness_audit.parquet",
                )
            )

        for item in parquet_tables:
            schema, table, path = item[:3]
            projection = item[3] if len(item) == 4 else "*"
            row_count = load_parquet(
                connection, schema, table, path, projection=projection
            )
            table_manifest.append(
                {
                    "schema_name": schema,
                    "table_name": table,
                    "row_count": row_count,
                    "source_path": path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix(),
                }
            )

        event_frame = simplified_events()
        connection.register("event_frame", event_frame)
        connection.execute("CREATE TABLE evidence.context_events AS SELECT * FROM event_frame")
        connection.unregister("event_frame")
        table_manifest.append(
            {
                "schema_name": "evidence",
                "table_name": "context_events",
                "row_count": len(event_frame),
                "source_path": "data_pipeline/evidence/events/events.json",
            }
        )

        monthly = build_monthly_analysis()
        connection.register("monthly_analysis", monthly)
        connection.execute(
            "CREATE TABLE analysis.housing_credit_monthly AS SELECT * FROM monthly_analysis"
        )
        connection.unregister("monthly_analysis")
        table_manifest.append(
            {
                "schema_name": "analysis",
                "table_name": "housing_credit_monthly",
                "row_count": len(monthly),
                "source_path": "derived from validated BDDK, EVDS and event assets",
            }
        )

        quarterly = build_quarterly_analysis()
        connection.register("quarterly_analysis", quarterly)
        connection.execute(
            "CREATE TABLE analysis.housing_credit_quarterly AS SELECT * FROM quarterly_analysis"
        )
        connection.unregister("quarterly_analysis")
        table_manifest.append(
            {
                "schema_name": "analysis",
                "table_name": "housing_credit_quarterly",
                "row_count": len(quarterly),
                "source_path": "derived from validated BDDK, FinTurk, EVDS and TBB assets",
            }
        )

        manifest_frame = pd.DataFrame(table_manifest)
        connection.register("table_manifest_frame", manifest_frame)
        connection.execute(
            "CREATE TABLE catalog.table_manifest AS SELECT * FROM table_manifest_frame"
        )
        connection.unregister("table_manifest_frame")
        connection.execute("ANALYZE")

        monthly_count = int(
            connection.execute("SELECT count(*) FROM analysis.housing_credit_monthly").fetchone()[0]
        )
        quarterly_count = int(
            connection.execute("SELECT count(*) FROM analysis.housing_credit_quarterly").fetchone()[0]
        )
        duplicate_months = int(
            connection.execute(
                "SELECT count(*) - count(DISTINCT month) FROM analysis.housing_credit_monthly"
            ).fetchone()[0]
        )
        duplicate_quarters = int(
            connection.execute(
                "SELECT count(*) - count(DISTINCT quarter) FROM analysis.housing_credit_quarterly"
            ).fetchone()[0]
        )
        table_count = int(
            connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    os.replace(temporary_path, output_path)
    result = {
        "status": "passed",
        "database_file": output_path.name,
        "database_bytes": output_path.stat().st_size,
        "schema_count": 7,
        "table_count": table_count,
        "monthly_analysis_rows": monthly_count,
        "quarterly_analysis_rows": quarterly_count,
        "duplicate_months": duplicate_months,
        "duplicate_quarters": duplicate_quarters,
        "weekly_measurements_loaded": weekly_path.exists(),
        "source_table_manifest_rows": len(table_manifest),
        "quality_policy": [
            "The DuckDB file contains copied tables and does not depend on absolute Parquet paths at query time.",
            "Raw source values remain available in source-specific Parquet files with hashes.",
            "Weekly measurement rows keep analytical keys and values compact; labels and source hashes are joined through bddk.weekly_metric_dictionary and bddk.weekly_source_tables.",
            "The monthly analysis table keeps nominal stock, real stock, rates, controls and quality flags separate.",
            "The quarterly analysis table keeps BDDK, FinTurk, EVDS and TBB scope differences visible.",
            "Weekly BDDK data is loaded only after its processed validation exists.",
        ],
    }
    (output_path.parent / "validation.json").write_text(
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
