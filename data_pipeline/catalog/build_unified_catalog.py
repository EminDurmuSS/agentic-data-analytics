#!/usr/bin/env python3
"""Build one discovery catalog across EVDS, BDDK, FinTurk and TBB data.

The catalog separates physical data assets from metrics. It also distinguishes
metadata-only EVDS series from series whose observations are present locally.
No source values are changed by this script.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = CATALOG_DIR / "unified"

MONTHLY_METADATA_COLUMNS = {
    "month",
    "table_no",
    "table_name",
    "group_code",
    "source_row_index",
    "source_caption",
    "source_unit",
    "source_file",
    "source_sha256",
    "source_request_info_file",
    "source_request_info_sha256",
    "period_validation_source",
    "BankaAdi",
    "BasitSira",
    "Ad",
    "BasitFont",
}

PRODUCT_LABELS_TR = {
    "vehicle": "Taşıt kredisi",
    "housing": "Konut kredisi",
    "need": "İhtiyaç kredisi",
    "other": "Diğer tüketici kredisi",
    "total": "Toplam tüketici kredisi",
}

MEASURE_LABELS_TR = {
    "disbursement_amount_million_try": "kullandırım tutarı",
    "disbursement_person_count": "kullandırım kişi sayısı",
    "balance_amount_million_try": "dönem sonu bakiyesi",
    "balance_person_count": "bakiye kişi sayısı",
}

ASSET_COLUMNS = [
    "asset_id",
    "dataset_id",
    "source_system",
    "source_organization",
    "competition_scope",
    "status",
    "data_kind",
    "native_frequency",
    "temporal_semantics",
    "geography_grain",
    "institution_grain",
    "coverage_start",
    "coverage_end",
    "row_count",
    "column_count",
    "metric_count",
    "missing_value_count",
    "progress_completed",
    "progress_expected",
    "file_path",
    "file_format",
    "validation_file",
    "source_url",
    "description",
    "searchable_text",
]

METRIC_COLUMNS = [
    "metric_id",
    "dataset_id",
    "source_system",
    "source_organization",
    "competition_scope",
    "source_metric_code",
    "metric_name_tr",
    "metric_name_en",
    "group_name",
    "role",
    "dimension",
    "native_frequency",
    "unit",
    "temporal_semantics",
    "default_aggregation",
    "geography_grain",
    "institution_grain",
    "coverage_start",
    "coverage_end",
    "observation_available",
    "observation_count",
    "missing_observation_count",
    "quality_status",
    "is_archive",
    "source_asset",
    "source_metadata_url",
    "notes",
    "searchable_text",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def compact_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def text_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def make_asset(**values: Any) -> dict[str, Any]:
    return {column: values.get(column) for column in ASSET_COLUMNS}


def make_metric(**values: Any) -> dict[str, Any]:
    return {column: values.get(column) for column in METRIC_COLUMNS}


def evds_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog_path = CATALOG_DIR / "evds_series_catalog.parquet"
    catalog_summary_path = CATALOG_DIR / "evds_series_catalog_summary.json"
    dataset_specs = [
        {
            "directory": "housing_causality_v1",
            "dataset_id": "evds.housing_causality_v1",
            "description": "Selected housing-credit causal analysis observations with raw request lineage.",
            "searchable_text": "EVDS housing credit causality observations",
        },
        {
            "directory": "housing_causality_controls_v1",
            "dataset_id": "evds.housing_causality_controls_v1",
            "description": "Additional policy, activity, supply and rent controls for housing-credit analysis.",
            "searchable_text": "EVDS housing credit additional causal controls",
        },
        {
            "directory": "market_controls_v2",
            "dataset_id": "evds.market_controls_v2",
            "description": "Selected BIST 100, active BIST TL/kg gold and legacy sparse TL/gram gold controls with source missingness preserved.",
            "searchable_text": "EVDS BIST 100 active gold legacy gold market controls",
        },
    ]

    catalog_summary = read_json(catalog_summary_path)
    full_catalog = pd.read_parquet(catalog_path)
    selected_lookup: dict[str, dict[str, Any]] = {}
    for spec in dataset_specs:
        dataset_dir = PROJECT_ROOT / "data_pipeline" / "evds" / spec["directory"]
        selected = pd.read_parquet(dataset_dir / "analysis_series_catalog.parquet")
        for row in selected.to_dict("records"):
            series_code = str(row["series_code"])
            if series_code in selected_lookup:
                raise ValueError(f"EVDS secili seri birden fazla pakette: {series_code}")
            selected_lookup[series_code] = {"row": row, "spec": spec}

    metrics: list[dict[str, Any]] = []
    for row in full_catalog.to_dict("records"):
        series_code = str(row["series_code"])
        selected_entry = selected_lookup.get(series_code)
        has_observations = selected_entry is not None
        observed = selected_entry["row"] if selected_entry else None
        spec = selected_entry["spec"] if selected_entry else None
        role = text_value(observed.get("role")) if observed else ""
        temporal_semantics = (
            text_value(observed.get("temporal_semantics")) if observed else ""
        )
        aggregation = (
            text_value(observed.get("subperiod_aggregation"))
            if observed
            else text_value(row.get("default_aggregation"))
        )
        source_asset = catalog_path
        if spec is not None:
            source_asset = (
                PROJECT_ROOT
                / "data_pipeline"
                / "evds"
                / spec["directory"]
                / "observations_long.parquet"
            )
        metric_name_tr = text_value(row.get("series_name_tr"))
        metric_name_en = text_value(row.get("series_name_en"))
        group_name = text_value(row.get("group_name_tr"))
        source_organization = text_value(row.get("source")) or "TCMB"
        searchable_text = " | ".join(
            value
            for value in [
                series_code,
                metric_name_tr,
                metric_name_en,
                group_name,
                source_organization,
                text_value(row.get("unit")),
                role,
            ]
            if value
        )
        metrics.append(
            make_metric(
                metric_id=f"evds:{series_code}",
                dataset_id=(spec["dataset_id"] if spec else "evds.public_series_catalog"),
                source_system="TCMB_EVDS",
                source_organization=source_organization,
                competition_scope=(
                    "explicit_source_selected_observation"
                    if has_observations
                    else "explicit_source_metadata_only"
                ),
                source_metric_code=series_code,
                metric_name_tr=metric_name_tr,
                metric_name_en=metric_name_en,
                group_name=group_name,
                role=role,
                dimension="series_defined",
                native_frequency=text_value(row.get("frequency")),
                unit=text_value(row.get("unit")),
                temporal_semantics=temporal_semantics,
                default_aggregation=aggregation,
                geography_grain="series_defined",
                institution_grain="series_defined",
                coverage_start=(text_value(observed.get("requested_start")) if observed else ""),
                coverage_end=(text_value(observed.get("requested_end")) if observed else ""),
                observation_available=has_observations,
                observation_count=(int(observed["observation_count"]) if observed else 0),
                missing_observation_count=(
                    int(observed["missing_observation_count"]) if observed else 0
                ),
                quality_status="passed" if has_observations else "metadata_only",
                is_archive=bool(row.get("is_archive")),
                source_asset=relative(source_asset),
                source_metadata_url=text_value(row.get("metadata_url")),
                notes=(
                    text_value(observed.get("reason"))
                    if observed
                    else "Observation values are not stored locally."
                ),
                searchable_text=searchable_text,
            )
        )

    full_catalog_codes = set(full_catalog["series_code"].astype(str))
    for series_code, selected_entry in sorted(selected_lookup.items()):
        if series_code in full_catalog_codes:
            continue
        observed = selected_entry["row"]
        spec = selected_entry["spec"]
        if not bool(observed.get("is_derived")):
            raise ValueError(
                f"EVDS katalogunda bulunmayan ve turetilmis olmayan seri: {series_code}"
            )
        source_asset = (
            PROJECT_ROOT
            / "data_pipeline"
            / "evds"
            / spec["directory"]
            / "monthly_panel.parquet"
        )
        source_code = text_value(observed.get("source_series_code"))
        metric_name_tr = text_value(observed.get("series_name_tr"))
        metric_name_en = text_value(observed.get("series_name_en"))
        source_organization = text_value(observed.get("source"))
        metrics.append(
            make_metric(
                metric_id=f"evds_derived:{series_code}",
                dataset_id=spec["dataset_id"],
                source_system="TCMB_EVDS_DERIVED",
                source_organization=source_organization,
                competition_scope="derived_from_explicit_source",
                source_metric_code=series_code,
                metric_name_tr=metric_name_tr,
                metric_name_en=metric_name_en,
                group_name=text_value(observed.get("group_name_tr")),
                role=text_value(observed.get("role")),
                dimension="series_defined",
                native_frequency="derived",
                unit=text_value(observed.get("unit")),
                temporal_semantics=text_value(observed.get("temporal_semantics")),
                default_aggregation=text_value(observed.get("subperiod_aggregation")),
                geography_grain="series_defined",
                institution_grain="series_defined",
                coverage_start=text_value(observed.get("requested_start")),
                coverage_end=text_value(observed.get("requested_end")),
                observation_available=True,
                observation_count=int(observed.get("non_null_observation_count", 0)),
                missing_observation_count=int(observed.get("missing_observation_count", 0)),
                quality_status="passed",
                is_archive=False,
                source_asset=relative(source_asset),
                source_metadata_url=text_value(observed.get("metadata_url")),
                notes=(
                    f"{text_value(observed.get('reason'))} "
                    f"Source series: {source_code}; operation: "
                    f"{text_value(observed.get('derivation_operation'))}; factor: "
                    f"{text_value(observed.get('derivation_factor'))}."
                ),
                searchable_text=" | ".join(
                    value
                    for value in [
                        series_code,
                        metric_name_tr,
                        metric_name_en,
                        source_code,
                        source_organization,
                    ]
                    if value
                ),
            )
        )

    assets = [
        make_asset(
            asset_id="evds.public_series_catalog",
            dataset_id="evds.public_series_catalog",
            source_system="TCMB_EVDS",
            source_organization="TCMB and upstream official producers",
            competition_scope="explicit_source_metadata",
            status=catalog_summary["status"],
            data_kind="metadata_catalog",
            native_frequency="mixed",
            temporal_semantics="metadata_only",
            geography_grain="series_defined",
            institution_grain="series_defined",
            coverage_start="",
            coverage_end="",
            row_count=int(catalog_summary["series_rows"]),
            column_count=len(full_catalog.columns),
            metric_count=int(catalog_summary["unique_series_codes"]),
            missing_value_count=None,
            progress_completed=int(catalog_summary["downloaded_group_count"]),
            progress_expected=int(catalog_summary["declared_group_count"]),
            file_path=relative(catalog_path),
            file_format="parquet",
            validation_file=relative(catalog_summary_path),
            source_url="https://evds3.tcmb.gov.tr/",
            description="Complete public EVDS series metadata catalog. It does not contain observations.",
            searchable_text="EVDS TCMB complete series metadata catalog",
        )
    ]
    for spec in dataset_specs:
        dataset_dir = PROJECT_ROOT / "data_pipeline" / "evds" / spec["directory"]
        validation_path = dataset_dir / "validation.json"
        alignment_path = dataset_dir / "alignment_validation.json"
        validation = read_json(validation_path)
        alignment = read_json(alignment_path)
        observation_path = dataset_dir / "observations_long.parquet"
        observation_frame = pd.read_parquet(observation_path)
        assets.append(
            make_asset(
                asset_id=f"{spec['dataset_id']}.observations_long",
                dataset_id=spec["dataset_id"],
                source_system="TCMB_EVDS",
                source_organization="TCMB and upstream official producers",
                competition_scope="explicit_source_selected_observation",
                status=validation["status"],
                data_kind="observations_long",
                native_frequency="mixed",
                temporal_semantics="metric_defined",
                geography_grain="series_defined",
                institution_grain="series_defined",
                coverage_start=validation["first_period_start"],
                coverage_end=validation["last_period_end"],
                row_count=len(observation_frame),
                column_count=len(observation_frame.columns),
                metric_count=int(
                    validation.get("source_series_count", validation["series_count"])
                ),
                missing_value_count=int(validation["missing_observation_count"]),
                progress_completed=int(validation["request_count"]),
                progress_expected=int(validation["request_count"]),
                file_path=relative(observation_path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url="https://evds3.tcmb.gov.tr/",
                description=spec["description"],
                searchable_text=f"{spec['searchable_text']} observations long",
            )
        )
        coverage_gaps_path = dataset_dir / "coverage_gaps.parquet"
        coverage_gaps = pd.read_parquet(coverage_gaps_path)
        assets.append(
            make_asset(
                asset_id=f"{spec['dataset_id']}.coverage_gaps",
                dataset_id=spec["dataset_id"],
                source_system="TCMB_EVDS",
                source_organization="TCMB and upstream official producers",
                competition_scope="derived_quality_evidence",
                status=validation["status"],
                data_kind="source_availability",
                native_frequency="mixed",
                temporal_semantics="source_missingness_classification",
                geography_grain="series_defined",
                institution_grain="series_defined",
                coverage_start=validation["first_period_start"],
                coverage_end=validation["last_period_end"],
                row_count=len(coverage_gaps),
                column_count=len(coverage_gaps.columns),
                metric_count=0,
                missing_value_count=int(coverage_gaps.isna().sum().sum()),
                progress_completed=len(coverage_gaps),
                progress_expected=len(coverage_gaps),
                file_path=relative(coverage_gaps_path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url="https://evds3.tcmb.gov.tr/",
                description=(
                    "Expected periods absent from EVDS source rows, classified "
                    "without creating observations."
                ),
                searchable_text=(
                    f"{spec['searchable_text']} coverage gaps source not published"
                ),
            )
        )
        for frequency in ["monthly", "quarterly"]:
            panel_path = dataset_dir / f"{frequency}_panel.parquet"
            audit_path = dataset_dir / f"{frequency}_alignment_audit.parquet"
            panel = pd.read_parquet(panel_path)
            audit = pd.read_parquet(audit_path)
            assets.append(
                make_asset(
                    asset_id=f"{spec['dataset_id']}.{frequency}_panel",
                    dataset_id=spec["dataset_id"],
                    source_system="TCMB_EVDS",
                    source_organization="TCMB and upstream official producers",
                    competition_scope="derived_from_explicit_source",
                    status=alignment["status"],
                    data_kind="aligned_panel",
                    native_frequency=frequency,
                    temporal_semantics="metric_defined",
                    geography_grain="series_defined",
                    institution_grain="series_defined",
                    coverage_start=str(panel["target_period"].min()),
                    coverage_end=str(panel["target_period"].max()),
                    row_count=len(panel),
                    column_count=len(panel.columns),
                    metric_count=len(panel.columns) - 1,
                    missing_value_count=int(
                        panel.drop(columns=["target_period"]).isna().sum().sum()
                    ),
                    progress_completed=len(audit),
                    progress_expected=len(audit),
                    file_path=relative(panel_path),
                    file_format="parquet",
                    validation_file=relative(alignment_path),
                    source_url="https://evds3.tcmb.gov.tr/",
                    description=(
                        f"{spec['description']} {frequency.capitalize()} aligned panel "
                        "with a separate alignment audit."
                    ),
                    searchable_text=(
                        f"{spec['searchable_text']} {frequency} aligned panel"
                    ),
                )
            )
    return assets, metrics


def monthly_bddk_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_all_sector"
    validation_path = base / "validation.json"
    validation = read_json(validation_path)
    semantic_base = PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_semantic"
    semantic_validation_path = semantic_base / "validation.json"
    semantic_validation = read_json(semantic_validation_path)
    semantic_measurements_path = semantic_base / "measurements_long.parquet"
    dictionary = pd.read_parquet(semantic_base / "metric_dictionary.parquet")
    assets: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    for row in dictionary.to_dict("records"):
        table_no = int(row["table_no"])
        dataset_id = f"bddk.monthly_all_sector.table_{table_no:02d}"
        source_code = str(row["metric_code"])
        metric_name = f"{row['metric_label']} [{row['value_dimension']}]"
        metrics.append(
            make_metric(
                metric_id=f"bddk_monthly:{source_code}",
                dataset_id=dataset_id,
                source_system="BDDK_MONTHLY",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                source_metric_code=source_code,
                metric_name_tr=metric_name,
                metric_name_en="",
                group_name=text_value(row["table_name"]),
                role="banking_supervision",
                dimension=text_value(row["value_dimension"]),
                native_frequency="monthly",
                unit=text_value(row["unit"]),
                temporal_semantics=text_value(row["analysis_semantics"]),
                default_aggregation=text_value(row["quarterly_aggregation"]),
                geography_grain="national",
                institution_grain="sector",
                coverage_start=text_value(row["first_month"]),
                coverage_end=text_value(row["last_month"]),
                observation_available=int(row["observation_count"]) > 0,
                observation_count=int(row["observation_count"]),
                missing_observation_count=int(row["missing_observation_count"]),
                quality_status=(
                    "passed_high_confidence_inference"
                    if row["semantic_confidence"] == "high_inferred"
                    else semantic_validation["status"]
                ),
                is_archive=False,
                source_asset=relative(semantic_measurements_path),
                source_metadata_url="https://www.bddk.org.tr/BultenAylik/",
                notes=(
                    f"Transformation={row['transformation']}; "
                    f"semantic_confidence={row['semantic_confidence']}; "
                    f"label_variant_count={int(row['label_variant_count'])}."
                ),
                searchable_text=" | ".join(
                    [
                        source_code,
                        metric_name,
                        text_value(row["table_name"]),
                        text_value(row["unit"]),
                        text_value(row["analysis_semantics"]),
                    ]
                ),
            )
        )

    for table_key, table_summary in sorted(
        validation["tables"].items(), key=lambda item: int(item[0])
    ):
        table_no = int(table_key)
        dataset_id = f"bddk.monthly_all_sector.table_{table_no:02d}"
        path = base / f"table_{table_no:02d}.parquet"
        frame = pd.read_parquet(path)
        value_columns = [column for column in frame.columns if column not in MONTHLY_METADATA_COLUMNS]
        table_metric_count = int(dictionary["table_no"].eq(table_no).sum())
        assets.append(
            make_asset(
                asset_id=dataset_id,
                dataset_id=dataset_id,
                source_system="BDDK_MONTHLY",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                status=validation["status"],
                data_kind="source_table",
                native_frequency="monthly",
                temporal_semantics="metric_defined",
                geography_grain="national",
                institution_grain="sector",
                coverage_start=str(frame["month"].min()),
                coverage_end=str(frame["month"].max()),
                row_count=len(frame),
                column_count=len(frame.columns),
                metric_count=table_metric_count,
                missing_value_count=int(frame[value_columns].isna().sum().sum()),
                progress_completed=int(table_summary["periods"]),
                progress_expected=int(validation["period_count"]),
                file_path=relative(path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url="https://www.bddk.org.tr/BultenAylik/",
                description=f"BDDK monthly sector table: {table_summary['table_name']}.",
                searchable_text=f"BDDK monthly sector {table_summary['table_name']}",
            )
        )
    semantic_measurements = pd.read_parquet(semantic_measurements_path)
    assets.append(
        make_asset(
            asset_id="bddk.monthly_all_sector.semantic_measurements_long",
            dataset_id="bddk.monthly_all_sector.semantic",
            source_system="BDDK_MONTHLY",
            source_organization="BDDK",
            competition_scope="derived_from_explicit_source",
            status=semantic_validation["status"],
            data_kind="semantic_observations_long",
            native_frequency="monthly",
            temporal_semantics="stock_flow_ratio_count_separated",
            geography_grain="national",
            institution_grain="sector",
            coverage_start=str(semantic_measurements["month"].min()),
            coverage_end=str(semantic_measurements["month"].max()),
            row_count=len(semantic_measurements),
            column_count=len(semantic_measurements.columns),
            metric_count=int(semantic_validation["metric_count"]),
            missing_value_count=int(semantic_measurements["analysis_value"].isna().sum()),
            progress_completed=int(semantic_validation["table_count"]),
            progress_expected=17,
            file_path=relative(semantic_measurements_path),
            file_format="parquet",
            validation_file=relative(semantic_validation_path),
            source_url="https://www.bddk.org.tr/BultenAylik/",
            description="All monthly BDDK metrics with explicit semantics and source-preserving transformations.",
            searchable_text="BDDK monthly semantic long stock flow cumulative ratio count",
        )
    )
    return assets, metrics


def finturk_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = (
        PROJECT_ROOT
        / "data_pipeline"
        / "bddk"
        / "processed"
        / "finturk_all_groups_all_cities"
    )
    validation_path = base / "validation.json"
    validation = read_json(validation_path)
    dictionary = pd.read_parquet(base / "column_dictionary.parquet")
    measurements_path = base / "measurements_long.parquet"
    measurements = pd.read_parquet(measurements_path)
    stats = (
        measurements.groupby(["table_no", "measure_code"], sort=True)
        .agg(
            observation_count=("value", "count"),
            total_row_count=("value", "size"),
            coverage_start=("quarter", "min"),
            coverage_end=("quarter", "max"),
        )
        .reset_index()
    )
    stats["missing_observation_count"] = (
        stats["total_row_count"] - stats["observation_count"]
    )
    stats_lookup = stats.set_index(["table_no", "measure_code"]).to_dict("index")

    metrics: list[dict[str, Any]] = []
    for row in dictionary.to_dict("records"):
        key = (int(row["table_no"]), str(row["measure_code"]))
        metric_stats = stats_lookup[key]
        source_code = f"table{key[0]:02d}:{key[1]}"
        metric_name = text_value(row["measure_label"])
        metrics.append(
            make_metric(
                metric_id=f"bddk_finturk:{source_code}",
                dataset_id="bddk.finturk_all_groups_all_cities",
                source_system="BDDK_FINTURK",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                source_metric_code=source_code,
                metric_name_tr=metric_name,
                metric_name_en="",
                group_name=text_value(row["table_name"]),
                role="regional_banking",
                dimension="city,bank_group",
                native_frequency=text_value(row["native_frequency"]),
                unit=text_value(row["unit"]),
                temporal_semantics=text_value(row["value_semantics"]),
                default_aggregation="identity_at_quarter_end_no_monthly_fill",
                geography_grain="province_and_abroad",
                institution_grain="bank_group",
                coverage_start=metric_stats["coverage_start"],
                coverage_end=metric_stats["coverage_end"],
                observation_available=metric_stats["observation_count"] > 0,
                observation_count=int(metric_stats["observation_count"]),
                missing_observation_count=int(metric_stats["missing_observation_count"]),
                quality_status=validation["status"],
                is_archive=False,
                source_asset=relative(measurements_path),
                source_metadata_url="https://www.bddk.org.tr/BultenFinturk/",
                notes=f"Official source unit: {text_value(row['source_unit_label'])}",
                searchable_text=" | ".join(
                    [source_code, metric_name, text_value(row["table_name"]), text_value(row["unit"])]
                ),
            )
        )

    assets = [
        make_asset(
            asset_id="bddk.finturk_all_groups_all_cities.measurements_long",
            dataset_id="bddk.finturk_all_groups_all_cities",
            source_system="BDDK_FINTURK",
            source_organization="BDDK",
            competition_scope="explicit_required_source",
            status=validation["status"],
            data_kind="observations_long",
            native_frequency="quarterly",
            temporal_semantics="metric_defined",
            geography_grain="province_and_abroad",
            institution_grain="bank_group",
            coverage_start=str(measurements["quarter"].min()),
            coverage_end=str(measurements["quarter"].max()),
            row_count=len(measurements),
            column_count=len(measurements.columns),
            metric_count=len(dictionary),
            missing_value_count=int(measurements["value"].isna().sum()),
            progress_completed=int(validation["source_file_count"]),
            progress_expected=int(validation["source_file_count"]),
            file_path=relative(measurements_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="https://www.bddk.org.tr/BultenFinturk/",
            description="All FinTurk tables, bank groups, provinces and abroad in generic long form.",
            searchable_text="BDDK FinTurk province city bank group measurements",
        )
    ]
    missingness_path = base / "missingness_audit.parquet"
    missingness = pd.read_parquet(missingness_path)
    assets.append(
        make_asset(
            asset_id="bddk.finturk_all_groups_all_cities.missingness_audit",
            dataset_id="bddk.finturk_all_groups_all_cities",
            source_system="BDDK_FINTURK",
            source_organization="BDDK",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="missingness_audit",
            native_frequency="quarterly",
            temporal_semantics="source_missingness_classification",
            geography_grain="province_and_abroad",
            institution_grain="bank_group",
            coverage_start=str(measurements["quarter"].min()),
            coverage_end=str(measurements["quarter"].max()),
            row_count=len(missingness),
            column_count=len(missingness.columns),
            metric_count=0,
            missing_value_count=int(missingness.isna().sum().sum()),
            progress_completed=int(validation["missing_measurement_count"]),
            progress_expected=int(validation["missing_measurement_count"]),
            file_path=relative(missingness_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="https://www.bddk.org.tr/BultenFinturk/",
            description="Explicit classification of every FinTurk source null.",
            searchable_text="BDDK FinTurk missingness structural undefined not applicable source not reported",
        )
    )
    for table_no in sorted(validation["tables"], key=int):
        summary = validation["tables"][table_no]
        path = base / f"table_{int(table_no):02d}.parquet"
        frame = pd.read_parquet(path)
        assets.append(
            make_asset(
                asset_id=f"bddk.finturk_all_groups_all_cities.table_{int(table_no):02d}",
                dataset_id="bddk.finturk_all_groups_all_cities",
                source_system="BDDK_FINTURK",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                status=validation["status"],
                data_kind="source_table",
                native_frequency="quarterly",
                temporal_semantics="metric_defined",
                geography_grain="province_and_abroad",
                institution_grain="bank_group",
                coverage_start=str(frame["quarter"].min()),
                coverage_end=str(frame["quarter"].max()),
                row_count=len(frame),
                column_count=len(frame.columns),
                metric_count=int(summary["measure_count"]),
                missing_value_count=int(summary["missing_measurements"]),
                progress_completed=int(summary["period_count"]),
                progress_expected=int(validation["period_count"]),
                file_path=relative(path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url="https://www.bddk.org.tr/BultenFinturk/",
                description=f"BDDK FinTurk table: {summary['table_name']}.",
                searchable_text=f"BDDK FinTurk {summary['table_name']} province bank group",
            )
        )
    return assets, metrics


def tbb_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = PROJECT_ROOT / "data_pipeline" / "tbb" / "processed"
    validation_path = base / "validation.json"
    validation = read_json(validation_path)
    metrics_path = base / "consumer_credit_product_metrics.parquet"
    frame = pd.read_parquet(metrics_path)
    products = ["vehicle", "housing", "need", "other", "total"]
    metrics: list[dict[str, Any]] = []
    for (measure, currency), rows in frame.groupby(
        ["measure", "currency_group"], sort=True
    ):
        semantics = text_value(rows["flow_stock_semantics"].iloc[0])
        for product in products:
            values = pd.to_numeric(rows[product], errors="coerce")
            unit = "million_try" if "amount" in measure else "count"
            source_code = f"{measure}:{product}:{currency}"
            metric_name = (
                f"{PRODUCT_LABELS_TR[product]} {MEASURE_LABELS_TR[measure]} ({currency})"
            )
            metrics.append(
                make_metric(
                    metric_id=f"tbb:{slug(source_code)}",
                    dataset_id="tbb.consumer_credit_reports",
                    source_system="TBB_REPORTS",
                    source_organization="Türkiye Bankalar Birliği",
                    competition_scope="supporting_source_not_explicitly_required",
                    source_metric_code=source_code,
                    metric_name_tr=metric_name,
                    metric_name_en="",
                    group_name="Tüketici Kredileri ve Konut Kredileri",
                    role="credit_disbursement" if semantics == "flow" else "credit_balance",
                    dimension=f"currency_group={currency}",
                    native_frequency="quarterly",
                    unit=unit,
                    temporal_semantics=semantics,
                    default_aggregation="identity_at_quarter_end",
                    geography_grain="national",
                    institution_grain="reporting_banks_variable_by_period",
                    coverage_start=str(rows["quarter"].min()),
                    coverage_end=str(rows["quarter"].max()),
                    observation_available=bool(values.notna().any()),
                    observation_count=int(values.notna().sum()),
                    missing_observation_count=int(values.isna().sum()),
                    quality_status=validation["status"],
                    is_archive=False,
                    source_asset=relative(metrics_path),
                    source_metadata_url="https://www.tbb.org.tr/istatistiki-raporlar/11237",
                    notes="2026-06 publication is absent at the source and is not imputed.",
                    searchable_text=" | ".join(
                        [source_code, metric_name, "TBB consumer housing credit"]
                    ),
                )
            )

    assets: list[dict[str, Any]] = []
    asset_specs = [
        (
            "product_metrics",
            "consumer_credit_product_metrics.parquet",
            "observations_wide",
            len(metrics),
            "Quarterly product, currency, disbursement and balance measures.",
        ),
        (
            "housing_credit_quarterly",
            "housing_credit_quarterly.parquet",
            "analysis_table",
            4,
            "Quarterly housing credit disbursement and balance summary.",
        ),
        (
            "reporting_banks",
            "reporting_banks.parquet",
            "scope_metadata",
            1,
            "Reporting-bank scope by report period.",
        ),
        (
            "workbook_cells_long",
            "workbook_cells_long.parquet",
            "lossless_source_cells",
            0,
            "All non-empty workbook cells retained for audit and re-parsing.",
        ),
        (
            "source_gaps",
            "source_gaps.parquet",
            "source_availability",
            0,
            "Officially checked but not yet published report periods.",
        ),
    ]
    for asset_name, filename, data_kind, metric_count, description in asset_specs:
        path = base / filename
        data = pd.read_parquet(path)
        period_column = next(
            (
                candidate
                for candidate in ["quarter", "report_period", "period"]
                if candidate in data.columns
            ),
            None,
        )
        coverage_start = str(data[period_column].min()) if period_column else ""
        coverage_end = str(data[period_column].max()) if period_column else ""
        assets.append(
            make_asset(
                asset_id=f"tbb.consumer_credit_reports.{asset_name}",
                dataset_id="tbb.consumer_credit_reports",
                source_system="TBB_REPORTS",
                source_organization="Türkiye Bankalar Birliği",
                competition_scope="supporting_source_not_explicitly_required",
                status=validation["status"],
                data_kind=data_kind,
                native_frequency="quarterly",
                temporal_semantics="flow_and_stock_separated",
                geography_grain="national",
                institution_grain="reporting_banks_variable_by_period",
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                row_count=len(data),
                column_count=len(data.columns),
                metric_count=metric_count,
                missing_value_count=int(data.isna().sum().sum()),
                progress_completed=int(validation["parsed_workbooks"]),
                progress_expected=int(validation["expected_periods"]),
                file_path=relative(path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url="https://www.tbb.org.tr/istatistiki-raporlar/11237",
                description=description,
                searchable_text=f"TBB consumer credit {asset_name}",
            )
        )
    return assets, metrics


def weekly_bddk_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_dir = PROJECT_ROOT / "data_pipeline" / "bddk" / "weekly_all_sector"
    config = read_json(raw_dir / "request_config.json")
    expected = int(config["period_count"]) * len(config["tables"]) * len(config["groups"])
    completed = len(list((raw_dir / "raw").glob("*_info.json")))
    summary_path = raw_dir / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    download_complete = summary.get("status") == "complete"
    status = "complete" if download_complete else "in_progress"
    asset = make_asset(
        asset_id="bddk.weekly_all_sector.raw_snapshot",
        dataset_id="bddk.weekly_all_sector",
        source_system="BDDK_WEEKLY",
        source_organization="BDDK",
        competition_scope="explicit_required_source",
        status=status,
        data_kind="raw_source_collection",
        native_frequency="weekly",
        temporal_semantics="source_reported",
        geography_grain="national",
        institution_grain="sector",
        coverage_start=config["start"],
        coverage_end=config["end"],
        row_count=completed,
        column_count=None,
        metric_count=None,
        missing_value_count=None,
        progress_completed=completed,
        progress_expected=expected,
        file_path=relative(raw_dir),
        file_format="gzip_html_and_json",
        validation_file=relative(summary_path) if summary_path.exists() else "",
        source_url=config["source_url"],
        description="BDDK weekly bulletin source pages. Processing starts only after a complete download.",
        searchable_text="BDDK weekly sector bulletin raw snapshot",
    )
    assets = [asset]
    metrics: list[dict[str, Any]] = []

    processed_dir = PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "weekly_all_sector"
    processed_validation_path = processed_dir / "validation.json"
    if processed_validation_path.exists():
        validation = read_json(processed_validation_path)
        measurements_path = processed_dir / "measurements_long.parquet"
        measurements = pd.read_parquet(measurements_path)
        assets.append(
            make_asset(
                asset_id="bddk.weekly_all_sector.measurements_long",
                dataset_id="bddk.weekly_all_sector",
                source_system="BDDK_WEEKLY",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                status=validation["status"],
                data_kind="observations_long",
                native_frequency="weekly",
                temporal_semantics="metric_defined_or_review_required",
                geography_grain="national",
                institution_grain="sector",
                coverage_start=str(measurements["observation_date"].min()),
                coverage_end=str(measurements["observation_date"].max()),
                row_count=len(measurements),
                column_count=len(measurements.columns),
                metric_count=int(
                    measurements[["metric_code", "currency_dimension"]]
                    .drop_duplicates()
                    .shape[0]
                ),
                missing_value_count=int(measurements["value"].isna().sum()),
                progress_completed=int(validation["source_page_count"]),
                progress_expected=expected,
                file_path=relative(measurements_path),
                file_format="parquet",
                validation_file=relative(processed_validation_path),
                source_url=config["source_url"],
                description="Validated BDDK weekly measures with source-page lineage.",
                searchable_text="BDDK weekly sector measurements long",
            )
        )
        missingness_path = processed_dir / "missingness_audit.parquet"
        if missingness_path.exists():
            missingness = pd.read_parquet(missingness_path)
            assets.append(
                make_asset(
                    asset_id="bddk.weekly_all_sector.missingness_audit",
                    dataset_id="bddk.weekly_all_sector",
                    source_system="BDDK_WEEKLY",
                    source_organization="BDDK",
                    competition_scope="derived_quality_evidence",
                    status=validation["status"],
                    data_kind="missingness_audit",
                    native_frequency="weekly",
                    temporal_semantics="source_missingness_classification",
                    geography_grain="national",
                    institution_grain="sector",
                    coverage_start=str(measurements["observation_date"].min()),
                    coverage_end=str(measurements["observation_date"].max()),
                    row_count=len(missingness),
                    column_count=len(missingness.columns),
                    metric_count=0,
                    missing_value_count=int(missingness.isna().sum().sum()),
                    progress_completed=int(validation["missing_measurement_count"]),
                    progress_expected=int(validation["missing_measurement_count"]),
                    file_path=relative(missingness_path),
                    file_format="parquet",
                    validation_file=relative(processed_validation_path),
                    source_url=config["source_url"],
                    description="Explicit classification of every BDDK weekly source null.",
                    searchable_text="BDDK weekly missingness source not applicable unresolved",
                )
            )
        for (table_id, metric_code, dimension), rows in measurements.groupby(
            ["table_id", "metric_code", "currency_dimension"], sort=True
        ):
            labels = sorted({text_value(value) for value in rows["metric_label"] if text_value(value)})
            label = labels[-1] if labels else str(metric_code)
            values = pd.to_numeric(rows["value"], errors="coerce")
            source_code = f"table{int(table_id)}:{metric_code}:{dimension}"
            metrics.append(
                make_metric(
                    metric_id=f"bddk_weekly:{slug(source_code)}",
                    dataset_id="bddk.weekly_all_sector",
                    source_system="BDDK_WEEKLY",
                    source_organization="BDDK",
                    competition_scope="explicit_required_source",
                    source_metric_code=source_code,
                    metric_name_tr=f"{label} [{dimension}]",
                    metric_name_en="",
                    group_name=text_value(rows["table_name"].iloc[0]),
                    role="weekly_banking_supervision",
                    dimension=str(dimension),
                    native_frequency="weekly",
                    unit=text_value(rows["source_unit"].dropna().iloc[0]) if rows["source_unit"].notna().any() else "",
                    temporal_semantics="source_reported_requires_semantic_review",
                    default_aggregation="semantic_policy_required_for_resampling",
                    geography_grain="national",
                    institution_grain="sector",
                    coverage_start=str(rows["observation_date"].min()),
                    coverage_end=str(rows["observation_date"].max()),
                    observation_available=bool(values.notna().any()),
                    observation_count=int(values.notna().sum()),
                    missing_observation_count=int(values.isna().sum()),
                    quality_status=validation["status"],
                    is_archive=False,
                    source_asset=relative(measurements_path),
                    source_metadata_url=config["source_url"],
                    notes="Source labels and currencies are preserved.",
                    searchable_text=" | ".join([source_code, label, str(dimension)]),
                )
            )

    return assets, metrics, {
        "status": status,
        "completed": completed,
        "expected": expected,
        "progress_percent": round(100 * completed / expected, 2),
    }


def event_assets() -> list[dict[str, Any]]:
    path = PROJECT_ROOT / "data_pipeline" / "evidence" / "events" / "events.json"
    events = read_json(path)
    dates = [item["annotation_date"] for item in events]
    verified = sum(
        "downloaded_primary_fulltext" in item["verification_level"]
        or item["verification_level"]
        == "downloaded_primary_fulltext_and_official_announcement"
        for item in events
    )
    return [
        make_asset(
            asset_id="official_context.housing_credit_events",
            dataset_id="official_context.housing_credit_events",
            source_system="OFFICIAL_DOCUMENTS",
            source_organization="BDDK and TCMB",
            competition_scope="supporting_source_not_explicitly_required",
            status="passed" if verified == len(events) else "passed_with_document_access_gaps",
            data_kind="event_annotations",
            native_frequency="event",
            temporal_semantics="descriptive_annotation_only",
            geography_grain="national",
            institution_grain="policy_system",
            coverage_start=min(dates),
            coverage_end=max(dates),
            row_count=len(events),
            column_count=len(events[0]) if events else 0,
            metric_count=0,
            missing_value_count=None,
            progress_completed=verified,
            progress_expected=len(events),
            file_path=relative(path),
            file_format="json",
            validation_file=relative(
                PROJECT_ROOT / "data_pipeline" / "evidence" / "events" / "events_notes.md"
            ),
            source_url="multiple_official_documents",
            description="Regulatory and methodology events for cautious contextual interpretation.",
            searchable_text="BDDK TCMB housing credit regulation methodology events",
        )
    ]


def quality_assets() -> list[dict[str, Any]]:
    base = PROJECT_ROOT / "data_pipeline" / "quality" / "processed"
    path = base / "housing_credit_stock_reconciliation.parquet"
    validation_path = base / "validation.json"
    validation = read_json(validation_path)
    frame = pd.read_parquet(path)
    return [
        make_asset(
            asset_id="quality.housing_credit_stock_reconciliation",
            dataset_id="quality.housing_credit_stock_reconciliation",
            source_system="CROSS_SOURCE_QUALITY",
            source_organization="BDDK, TCMB EVDS and TBB",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="cross_source_reconciliation",
            native_frequency="quarterly",
            temporal_semantics="period_end_stock_comparison",
            geography_grain="national_and_domestic_province_sum",
            institution_grain="sector_and_reporting_banks",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(frame),
            column_count=len(frame.columns),
            metric_count=4,
            missing_value_count=int(frame.isna().sum().sum()),
            progress_completed=int(validation["quarter_count"]),
            progress_expected=int(validation["quarter_count"]),
            file_path=relative(path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="multiple_official_sources",
            description="Quarter-end housing-credit stock scope reconciliation across BDDK, FinTurk, EVDS and TBB.",
            searchable_text="housing credit stock cross source reconciliation quality BDDK FinTurk EVDS TBB",
        )
    ]


def validate_catalog(
    assets: pd.DataFrame,
    metrics: pd.DataFrame,
    weekly_progress: dict[str, Any],
) -> dict[str, Any]:
    duplicate_assets = int(assets["asset_id"].duplicated().sum())
    duplicate_metrics = int(metrics["metric_id"].duplicated().sum())
    absolute_paths = int(assets["file_path"].fillna("").str.startswith("/").sum())
    missing_asset_files = sorted(
        path
        for path in assets["file_path"].dropna().astype(str)
        if path and not (PROJECT_ROOT / path).exists()
    )
    required_datasets = {
        "evds.public_series_catalog",
        "evds.housing_causality_v1",
        "evds.housing_causality_controls_v1",
        "evds.market_controls_v2",
        "bddk.monthly_all_sector.table_01",
        "bddk.finturk_all_groups_all_cities",
        "bddk.weekly_all_sector",
    }
    present_datasets = set(assets["dataset_id"])
    missing_required = sorted(required_datasets - present_datasets)
    selected_evds = metrics.loc[
        (metrics["source_system"] == "TCMB_EVDS")
        & metrics["observation_available"].eq(True)
    ]
    if duplicate_assets or duplicate_metrics or absolute_paths or missing_asset_files or missing_required:
        status = "failed"
    else:
        status = "passed"
    return {
        "status": status,
        "data_readiness_status": (
            "core_sources_complete"
            if weekly_progress["status"] == "complete"
            else "weekly_bddk_download_in_progress"
        ),
        "asset_count": len(assets),
        "metric_count": len(metrics),
        "queryable_metric_count": int(metrics["observation_available"].eq(True).sum()),
        "metadata_only_metric_count": int(metrics["observation_available"].eq(False).sum()),
        "metrics_by_source_system": {
            str(key): int(value)
            for key, value in metrics.groupby("source_system").size().sort_index().items()
        },
        "assets_by_source_system": {
            str(key): int(value)
            for key, value in assets.groupby("source_system").size().sort_index().items()
        },
        "selected_evds_observation_series": len(selected_evds),
        "weekly_bddk": weekly_progress,
        "duplicate_asset_ids": duplicate_assets,
        "duplicate_metric_ids": duplicate_metrics,
        "absolute_asset_paths": absolute_paths,
        "missing_asset_files": missing_asset_files,
        "missing_required_datasets": missing_required,
        "known_source_gaps": [
            "TBB 2026-06 consumer credit report is not published in the source snapshot.",
            *(
                ["BDDK weekly snapshot is still downloading."]
                if weekly_progress["status"] != "complete"
                else []
            ),
        ],
        "quality_policy": [
            "Metadata-only series are never presented as locally available observations.",
            "Stock, flow, ratio, count and survey semantics remain separate.",
            "Resampling requires an explicit metric policy; unsafe generic aggregation is not assigned.",
            "All local paths are repository-relative.",
            "Known source gaps remain explicit and are not imputed.",
        ],
    }


def build(output_dir: Path) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    for builder in [
        evds_assets_and_metrics,
        monthly_bddk_assets_and_metrics,
        finturk_assets_and_metrics,
        tbb_assets_and_metrics,
    ]:
        source_assets, source_metrics = builder()
        assets.extend(source_assets)
        metrics.extend(source_metrics)

    weekly_assets, weekly_metrics, weekly_progress = weekly_bddk_assets_and_metrics()
    assets.extend(weekly_assets)
    metrics.extend(weekly_metrics)
    assets.extend(event_assets())
    assets.extend(quality_assets())

    asset_frame = pd.DataFrame(assets, columns=ASSET_COLUMNS).sort_values(
        ["source_system", "dataset_id", "asset_id"], kind="stable"
    )
    metric_frame = pd.DataFrame(metrics, columns=METRIC_COLUMNS).sort_values(
        ["source_system", "dataset_id", "metric_id"], kind="stable"
    )
    validation = validate_catalog(asset_frame, metric_frame, weekly_progress)
    if validation["status"] != "passed":
        raise ValueError(f"Birlesik katalog dogrulamasi gecmedi: {validation}")

    output_dir.mkdir(parents=True, exist_ok=True)
    asset_frame.to_csv(
        output_dir / "unified_data_catalog.csv", index=False, encoding="utf-8-sig"
    )
    asset_frame.to_parquet(output_dir / "unified_data_catalog.parquet", index=False)
    metric_frame.to_parquet(output_dir / "unified_metric_catalog.parquet", index=False)
    metric_frame.loc[metric_frame["observation_available"].eq(True)].to_csv(
        output_dir / "queryable_metric_catalog.csv", index=False, encoding="utf-8-sig"
    )
    (output_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
