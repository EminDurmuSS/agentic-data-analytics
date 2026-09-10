#!/usr/bin/env python3
"""Build a self-contained DuckDB analytics database from validated Parquet data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

import duckdb
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "analytics.duckdb"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from agentic_analytics.lakehouse.registry import install_bindings
from data_pipeline.lakehouse.source_views import install_source_views
from agentic_analytics.lakehouse.quality import validate_connection, validate_database


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


def resolve_full_evds(unified_validation: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    """Pin the same verified immutable publication used to build the catalog."""
    package = PROJECT_ROOT / "data_pipeline" / "evds" / "full_catalog"
    expected = unified_validation.get("evds_full_catalog", {})
    if not expected.get("present"):
        return None
    from tools.publish_evds_bulk import resolve_publication

    release, manifest = resolve_publication(package, verify=True,
        publication_id=expected["publication_id"], manifest_sha256=expected["manifest_sha256"])
    if manifest.get("dataset_id") != "evds.full_catalog" or manifest.get("format_version") != 1 or manifest["validation"].get("status") != "passed":
        raise ValueError("EVDS full catalog publication has an unsupported or unvalidated contract")
    if expected.get("manifest_path") != release.joinpath("manifest.json").relative_to(PROJECT_ROOT).as_posix():
        raise ValueError("EVDS catalog manifest path does not match the pinned publication")
    return release, manifest


def install_full_evds(
    connection: duckdb.DuckDBPyConnection, release: Path, manifest: dict[str, Any],
    catalog_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copy native facts and coverage, retaining the exact publication identity."""
    connection.execute("CREATE SCHEMA IF NOT EXISTS evds")
    result = []
    for filename, table in [
        ("observations_long.parquet", "full_catalog_observations"),
        ("analysis_series_catalog.parquet", "full_catalog_series_catalog"),
        ("coverage.parquet", "full_catalog_coverage"),
        ("request_coverage.parquet", "full_catalog_request_coverage"),
        ("conflicts.parquet", "full_catalog_conflicts"),
    ]:
        if filename not in manifest["files"]:
            if filename in {"observations_long.parquet", "analysis_series_catalog.parquet", "coverage.parquet"}:
                raise ValueError(f"EVDS publication lacks required artifact: {filename}")
            continue
        path = release / filename
        count = load_parquet(connection, "evds", table, path)
        result.append({"schema_name": "evds", "table_name": table, "row_count": count,
                       "source_path": path.relative_to(PROJECT_ROOT).as_posix(),
                       "source_sha256": manifest["files"][filename]["sha256"],
                       "publication_id": manifest["publication_id"]})
    manifest_path = release / "manifest.json"
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    connection.execute("""CREATE TABLE evds.full_catalog_publication(
        publication_id VARCHAR, manifest_path VARCHAR, manifest_sha256 VARCHAR,
        validation_json VARCHAR, catalog_scope_complete BOOLEAN, catalog_scope_series BIGINT,
        target_start VARCHAR, target_end VARCHAR)""")
    connection.execute("INSERT INTO evds.full_catalog_publication VALUES (?,?,?,?,?,?,?,?)", [
        manifest["publication_id"], manifest_path.relative_to(PROJECT_ROOT).as_posix(), manifest_hash,
        json.dumps(manifest["validation"], ensure_ascii=False, sort_keys=True),
        bool(catalog_summary.get("catalog_scope_complete", False)), int(catalog_summary.get("catalog_scope_series", 0)),
        manifest["target_start"], manifest["target_end"],
    ])
    result.append({"schema_name": "evds", "table_name": "full_catalog_publication", "row_count": 1,
                   "source_path": manifest_path.relative_to(PROJECT_ROOT).as_posix(),
                   "source_sha256": manifest_hash, "publication_id": manifest["publication_id"]})
    return result


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
    for dataset_name in [
        "housing_causality_controls_v1",
        "market_controls_v2",
        "household_finance_v1",
    ]:
        controls = pd.read_parquet(
            PROJECT_ROOT
            / "data_pipeline"
            / "evds"
            / dataset_name
            / "monthly_panel.parquet"
        ).rename(columns={"target_period": "month"})
        frame = frame.merge(controls, on="month", how="left", validate="one_to_one")

    risk_center = pd.read_parquet(
        PROJECT_ROOT
        / "data_pipeline"
        / "risk_center"
        / "monthly_housing_v1"
        / "processed"
        / "housing_credit_monthly.parquet"
    )
    risk_center = risk_center.rename(
        columns={
            column: f"risk_center_{column}"
            for column in risk_center.columns
            if column != "month"
        }
    )
    frame = frame.merge(risk_center, on="month", how="left", validate="one_to_one")
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
    for dataset_name in [
        "housing_causality_controls_v1",
        "market_controls_v2",
        "household_finance_v1",
    ]:
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


def build(output_path: Path, *, catalog_dir: Path | None = None) -> dict[str, Any]:
    catalog_dir = catalog_dir or PROJECT_ROOT / "data_pipeline" / "catalog" / "unified"
    unified_validation = read_json(catalog_dir / "validation.json")
    if unified_validation.get("status") != "passed":
        raise ValueError("Doğrulanmamış birleşik katalog lakehouse'a yüklenemez.")
    full_evds = resolve_full_evds(unified_validation)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")

    table_manifest: list[dict[str, Any]] = []
    connection = duckdb.connect(str(temporary_path))
    try:
        for schema in [
            "catalog",
            "evds",
            "bddk",
            "tbb",
            "quality",
            "evidence",
            "tuik",
            "regional",
            "risk_center",
            "analysis",
        ]:
            connection.execute(f"CREATE SCHEMA {schema}")

        parquet_tables = [
            (
                "catalog",
                "data_assets",
                catalog_dir / "unified_data_catalog.parquet",
            ),
            (
                "catalog",
                "metrics",
                catalog_dir / "unified_metric_catalog.parquet",
            ),
            (
                "evds",
                "series_catalog",
                PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet",
                ", ".join(
                    [
                        "series_code",
                        "series_name_tr",
                        "series_name_en",
                        "group_code",
                        "group_name_tr",
                        "category_path_tr",
                        "frequency",
                        "default_aggregation",
                        "unit",
                        "is_archive",
                        "metadata_url",
                        "group_catalog_sha256",
                    ]
                ),
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
                ", ".join(
                    [
                        "quarter",
                        "observation_date",
                        "native_frequency",
                        "table_no",
                        "table_name",
                        "group_code",
                        "group_name",
                        "city",
                        "source_row_index",
                        "source_file",
                        "source_sha256",
                        "measure_code",
                        "value",
                        "measure_label",
                        "unit",
                        "source_unit_label",
                        "value_semantics",
                        "is_missing",
                        "missing_kind",
                        "missing_reason",
                        "dependency_codes",
                        "dependency_value",
                        "is_structural_na",
                        "is_unresolved_missing",
                        "usable_value",
                        "value_origin",
                        "derivation_formula",
                        "is_analytically_resolved",
                    ]
                ),
            ),
            (
                "bddk",
                "finturk_source_tables",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "finturk_all_groups_all_cities" / "source_table_catalog.parquet",
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
                "bddk",
                "finturk_branch_zero_fallback_audit",
                PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "finturk_all_groups_all_cities" / "branch_zero_fallback_audit.parquet",
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
                "risk_center",
                "housing_metric_vintages",
                PROJECT_ROOT
                / "data_pipeline"
                / "risk_center"
                / "monthly_housing_v1"
                / "processed"
                / "housing_metric_vintages.parquet",
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
            ("regional_housing_v1", "regional_housing"),
            ("household_finance_v1", "household_finance"),
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

        regional_dir = PROJECT_ROOT / "data_pipeline" / "regional" / "processed"
        tuik_dir = (
            PROJECT_ROOT
            / "data_pipeline"
            / "tuik"
            / "province_housing_sales_v1"
            / "processed"
        )
        parquet_tables.extend(
            [
                (
                    "tuik",
                    "province_housing_sales_monthly",
                    tuik_dir / "monthly_sales_long.parquet",
                ),
                (
                    "tuik",
                    "province_housing_sales_evds_reconciliation",
                    tuik_dir / "evds_reconciliation.parquet",
                ),
                (
                    "tuik",
                    "province_housing_sales_identity_zero_fallbacks",
                    tuik_dir / "identity_zero_fallbacks.parquet",
                ),
                (
                    "regional",
                    "province_dimension",
                    regional_dir / "province_dimension.parquet",
                ),
                (
                    "regional",
                    "housing_quarterly",
                    regional_dir / "province_quarter_housing_panel.parquet",
                ),
                (
                    "regional",
                    "metric_dictionary",
                    regional_dir / "metric_dictionary.parquet",
                ),
            ]
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

        if full_evds is not None:
            table_manifest.extend(install_full_evds(
                connection, *full_evds, unified_validation["evds_full_catalog"]))

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
                "source_path": "derived from validated BDDK, EVDS, Risk Center and event assets",
            }
        )

        connection.execute(
            """
            CREATE VIEW risk_center.housing_credit_monthly AS
            SELECT
                month,
                risk_center_first_time_housing_credit_users_thousand_person
                    AS first_time_housing_credit_users_thousand_person,
                risk_center_housing_credit_average_balance_try
                    AS housing_credit_average_balance_try,
                risk_center_housing_credit_balance_billion_try
                    AS housing_credit_balance_billion_try,
                risk_center_housing_credit_borrower_count_million_person
                    AS housing_credit_borrower_count_million_person,
                risk_center_housing_credit_npl_ratio_pct
                    AS housing_credit_npl_ratio_pct,
                risk_center_selected_source_publication_month
                    AS selected_source_publication_month,
                risk_center_has_source_revision AS has_source_revision,
                risk_center_housing_credit_balance_million_try_from_rounded_chart
                    AS housing_credit_balance_million_try_from_rounded_chart
            FROM analysis.housing_credit_monthly
            """
        )
        connection.execute(
            """
            CREATE VIEW risk_center.metric_dictionary AS
            SELECT
                source_metric_code AS metric_code,
                metric_name_tr,
                unit,
                temporal_semantics,
                default_aggregation,
                notes AS caution,
                source_system,
                source_organization,
                native_frequency,
                TRUE AS source_value_is_rounded_chart_label
            FROM catalog.metrics
            WHERE source_system = 'TBB_RISK_CENTER'
            """
        )
        connection.execute(
            """
            CREATE VIEW risk_center.overlap_revision_audit AS
            SELECT
                observation_month,
                metric_code,
                min(publication_month) AS earliest_publication_month,
                max(publication_month) AS latest_publication_month,
                arg_min(value, publication_month) AS earliest_value,
                arg_max(value, publication_month) AS latest_value,
                arg_max(value, publication_month) - arg_min(value, publication_month)
                    AS difference,
                arg_max(value, publication_month) <> arg_min(value, publication_month)
                    AS value_changed,
                arg_min(source_sha256, publication_month) AS earliest_source_sha256,
                arg_max(source_sha256, publication_month) AS latest_source_sha256
            FROM risk_center.housing_metric_vintages
            GROUP BY observation_month, metric_code
            HAVING count(*) > 1
            """
        )
        for table_name, row_count, source_path in [
            (
                "housing_credit_monthly",
                len(monthly),
                "data_pipeline/risk_center/monthly_housing_v1/processed/housing_credit_monthly.parquet",
            ),
            (
                "metric_dictionary",
                5,
                "data_pipeline/risk_center/monthly_housing_v1/processed/metric_dictionary.parquet",
            ),
            (
                "overlap_revision_audit",
                25,
                "data_pipeline/risk_center/monthly_housing_v1/processed/overlap_revision_audit.parquet",
            ),
        ]:
            table_manifest.append(
                {
                    "schema_name": "risk_center",
                    "table_name": table_name,
                    "row_count": row_count,
                    "source_path": source_path,
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

        table_manifest.extend(install_source_views(connection, PROJECT_ROOT))
        manifest_frame = pd.DataFrame(table_manifest)
        connection.register("table_manifest_frame", manifest_frame)
        connection.execute(
            "CREATE TABLE catalog.table_manifest AS SELECT * FROM table_manifest_frame"
        )
        connection.unregister("table_manifest_frame")
        binding_summary = install_bindings(connection)
        release_quality = validate_connection(connection)
        connection.execute("CREATE TABLE catalog.build_validation(report_json VARCHAR)")
        connection.execute("INSERT INTO catalog.build_validation VALUES (?)", [json.dumps(release_quality,ensure_ascii=False)])
        connection.execute("ANALYZE")
        connection.execute("CHECKPOINT")

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
        regional_count, regional_distinct_count = connection.execute(
            "SELECT count(*), count(DISTINCT province_key || ':' || quarter) "
            "FROM regional.housing_quarterly"
        ).fetchone()
        risk_center_count, risk_center_distinct_count = connection.execute(
            "SELECT count(*), count(DISTINCT month) "
            "FROM risk_center.housing_credit_monthly"
        ).fetchone()
        table_count = int(
            connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
            ).fetchone()[0]
        )
    except BaseException:
        connection.close()
        temporary_path.unlink(missing_ok=True)
        Path(str(temporary_path)+".wal").unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    # Reopen the completed copy before making it visible to legacy readers.
    # Agent sessions additionally pin immutable copies through LakehouseStore.
    try:
        validate_database(temporary_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    with temporary_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary_path, output_path)
    directory_fd = os.open(output_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    result = {
        "status": "passed",
        "database_file": output_path.name,
        "database_bytes": output_path.stat().st_size,
        "schema_count": 10,
        "table_count": table_count,
        "monthly_analysis_rows": monthly_count,
        "quarterly_analysis_rows": quarterly_count,
        "duplicate_months": duplicate_months,
        "duplicate_quarters": duplicate_quarters,
        "regional_housing_rows": int(regional_count),
        "regional_housing_distinct_keys": int(regional_distinct_count),
        "risk_center_monthly_rows": int(risk_center_count),
        "risk_center_monthly_distinct_months": int(risk_center_distinct_count),
        "weekly_measurements_loaded": weekly_path.exists(),
        "source_table_manifest_rows": len(table_manifest),
        "metric_bindings": binding_summary,
        "release_quality": release_quality,
        "quality_policy": [
            "The DuckDB file contains copied tables and does not depend on absolute Parquet paths at query time.",
            "Raw source values remain available in source-specific Parquet files with hashes.",
            "The DuckDB copy of the EVDS series catalog keeps discovery-critical columns; the complete source metadata remains in data_pipeline/catalog/evds_series_catalog.parquet.",
            "Weekly labels and source hashes are served through bddk.weekly_measurements_resolved using dated definitions and exact source keys; the historical dictionary alone is not a safe join.",
            "FinTurk measurement rows keep analytical keys and values compact; complete request provenance is joined through bddk.finturk_source_tables.",
            "The monthly analysis table keeps nominal stock, real stock, rates, controls and quality flags separate.",
            "Risk Center balances, borrower measures and first-time user counts retain explicit source-prefixed columns and do not overwrite BDDK, EVDS or TBB measures.",
            "The quarterly analysis table keeps BDDK, FinTurk, EVDS and TBB scope differences visible.",
            "The regional panel keeps official province observations distinct from regional KFE, YKKE and explicitly labelled same-region price proxies.",
            "TÜİK direct observations, identity-derived zero fallbacks and EVDS reconciliation remain separately queryable.",
            "FinTurk raw branch-count nulls and exact functional-group identity-derived analytical zeros remain separately queryable.",
            "Weekly BDDK data is loaded only after its processed validation exists.",
        ],
    }
    # DuckDB allocation can differ between equivalent builds. Keep physical
    # byte size in the command result, outside the tracked semantic report so
    # clean builds preserve the source-file checksum inventory.
    semantic_report = {key:value for key,value in result.items() if key != "database_bytes"}
    report_path = output_path.parent / "validation.json"
    report_temporary = report_path.with_name(f".{report_path.name}.{uuid.uuid4().hex}.tmp")
    report_temporary.write_text(
        json.dumps(semantic_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(report_temporary, report_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--catalog-dir", type=Path, default=None,
                        help="Validated unified catalog directory; its EVDS publication pin is honored.")
    args = parser.parse_args()
    result = build(args.output.expanduser().resolve(),
                   catalog_dir=args.catalog_dir.expanduser().resolve() if args.catalog_dir else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
