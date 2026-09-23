#!/usr/bin/env python3
"""Build one discovery catalog across EVDS, BDDK, FinTurk and TBB data.

The catalog separates physical data assets from metrics. It also distinguishes
metadata-only EVDS series from series whose observations are present locally.
No source values are changed by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = CATALOG_DIR / "unified"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentic_analytics.lakehouse.semantics import BDDK_WEEKLY_EXPLANATION_URL

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
    "vintage_policy",
    "release_date",
    "revision_status",
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
    "vintage_policy",
    "release_date",
    "revision_status",
    "canonical_series_code",
    "historical_name",
    "current_name",
    "name_change_effective_date",
    "name_change_source_url",
    "scale_revision_effective_date",
    "scale_revision_factor",
    "scale_revision_source_url",
    "historical_archive_source_url",
    "historical_original_scale_included",
    "methodology_source_url",
    "official_series_url",
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


def include_legacy_evds(assets: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> None:
    """Expose locally stored native series not present in the newer packages.

    The newer package wins only for overlapping *identical series identities*;
    this does not overwrite observations or merge different source vintages.
    """
    path = PROJECT_ROOT / "data_pipeline" / "processed" / "observations_native.parquet"
    if not path.exists():
        return
    frame = pd.read_parquet(path)
    existing = {m["source_metric_code"] for m in metrics
                if m["source_system"] == "TCMB_EVDS" and m["observation_available"]}
    extra = frame.loc[~frame["series_code"].isin(existing)].copy()
    if extra.empty:
        return
    if extra.duplicated(["series_code", "observation_date"]).any():
        raise ValueError("Legacy EVDS has ambiguous observation vintages; resolve explicitly.")
    lookup = {m["source_metric_code"]: m for m in metrics if m["source_system"] == "TCMB_EVDS"}
    for code, rows in extra.groupby("series_code", sort=True):
        if code not in lookup:
            raise ValueError(f"Legacy EVDS series missing from metadata: {code}")
        units, frequencies = rows["native_unit"].unique(), rows["native_frequency"].unique()
        if len(units) != 1 or len(frequencies) != 1:
            raise ValueError(f"Legacy EVDS has conflicting units/frequencies: {code}")
        unit = str(units[0])
        semantics = "period_end_stock" if unit.casefold() == "thousand_try" else "requires_semantic_review"
        metric = lookup[code]
        metric.update(dataset_id="evds.legacy_native", source_asset=relative(path),
                      competition_scope="explicit_source_selected_observation",
                      observation_available=True, observation_count=len(rows),
                      missing_observation_count=int(rows["value"].isna().sum()),
                      quality_status="passed_native_source", unit=unit,
                      native_frequency=str(frequencies[0]), temporal_semantics=semantics,
                      default_aggregation="last" if semantics == "period_end_stock" else "review_required",
                      coverage_start=str(rows["observation_date"].min().date()),
                      coverage_end=str(rows["observation_date"].max().date()),
                      notes="Existing native observation package; source hashes and vintage retained. No new download.")
        metric["searchable_text"] += " | native observations available locally"
    assets.append(make_asset(
        asset_id="evds.legacy_native.observations", dataset_id="evds.legacy_native",
        source_system="TCMB_EVDS", source_organization="TCMB",
        competition_scope="explicit_required_source", status="passed_native_source",
        data_kind="native_observations", native_frequency="series_defined",
        temporal_semantics="series_defined", geography_grain="series_defined",
        institution_grain="series_defined", coverage_start=str(extra["observation_date"].min().date()),
        coverage_end=str(extra["observation_date"].max().date()), row_count=len(extra),
        column_count=len(extra.columns), metric_count=extra["series_code"].nunique(),
        missing_value_count=int(extra["value"].isna().sum()), file_path=relative(path),
        file_format="parquet", description="Additional native EVDS series, excluding newer-package overlaps.",
        searchable_text="EVDS native participation development investment deposit banks credit"))


def include_full_catalog_evds(
    assets: list[dict[str, Any]], metrics: list[dict[str, Any]], package: Path | None = None,
) -> dict[str, Any]:
    """Add a verified bulk publication while keeping reviewed native bindings.

    Availability means returned physical rows, including source nulls. Collection
    completion and reviewed economic semantics are separate coverage properties.
    """
    package = package or PROJECT_ROOT / "data_pipeline" / "evds" / "full_catalog"
    if not (package / "CURRENT.json").exists():
        return {"present": False, "catalog_scope_complete": False,
                "full_request_scope_complete": False, "full_numeric_coverage_complete": False,
                "evds_full_observation_coverage_complete": False}
    from tools.publish_evds_bulk import resolve_publication

    release, manifest = resolve_publication(package, verify=True)
    if manifest.get("dataset_id") != "evds.full_catalog" or manifest.get("format_version") != 1 or manifest["validation"].get("status") != "passed":
        raise ValueError("EVDS full catalog publication has an unsupported or unvalidated contract")
    selected = pd.read_parquet(release / "analysis_series_catalog.parquet")
    coverage = pd.read_parquet(release / "coverage.parquet")
    if selected["series_code"].duplicated().any() or coverage["series_code"].duplicated().any():
        raise ValueError("EVDS full catalog has duplicate series identities")
    if set(selected["series_code"]) != set(coverage["series_code"]):
        raise ValueError("EVDS full catalog metadata and coverage identities differ")
    coverage_lookup = coverage.set_index("series_code").to_dict("index")
    existing = {m["source_metric_code"]: m for m in metrics if m["source_system"] == "TCMB_EVDS"}
    preserved = 0
    for row in selected.to_dict("records"):
        code = str(row["series_code"])
        if code in existing and existing[code]["observation_available"]:
            preserved += 1
            continue
        count = coverage_lookup[code]
        physical = bool(count["physical_present"])
        name_tr = text_value(row.get("series_name_tr")) or code
        name_en = text_value(row.get("series_name_en"))
        group = text_value(row.get("group_name_tr"))
        unit = text_value(row.get("unit"))
        metric = make_metric(
            metric_id=f"evds:{code}", dataset_id="evds.full_catalog", source_system="TCMB_EVDS",
            source_organization=text_value(row.get("source")) or "TCMB",
            competition_scope="explicit_source_native_observation" if physical else "explicit_source_metadata_only",
            source_metric_code=code, metric_name_tr=name_tr, metric_name_en=name_en,
            group_name=group, role="", dimension="series_defined",
            native_frequency=text_value(count.get("native_frequency")) or text_value(row.get("frequency")),
            unit=unit, temporal_semantics="requires_semantic_review", default_aggregation="review_required",
            geography_grain="series_defined", institution_grain="series_defined",
            coverage_start=text_value(count.get("observed_start")), coverage_end=text_value(count.get("observed_end")),
            observation_available=physical, observation_count=int(count["observation_count"]),
            missing_observation_count=int(count["missing_observation_count"]),
            quality_status="review_required" if physical else "metadata_only",
            is_archive=bool(row.get("is_archive", False)) if pd.notna(row.get("is_archive")) else False,
            source_asset=relative(release / ("observations_long.parquet" if physical else "analysis_series_catalog.parquet")),
            source_metadata_url=text_value(row.get("metadata_url")),
            notes=(f"Native bulk publication {manifest['publication_id']}; coverage={count['coverage_status']}; "
                   f"numeric observations={int(count['numeric_observation_count'])}. "
                   "Source units and economic aggregation require review; native values retain request/response evidence."),
            searchable_text=" | ".join(filter(None, [code, name_tr, name_en, group, unit])),
        )
        if code in existing:
            existing[code].update(metric)
        else:
            metrics.append(metric)
            existing[code] = metric
    summary = dict(manifest["validation"])
    scope_complete = set(existing).issubset(coverage_lookup)
    result = {**summary, "present": True, "publication_id": manifest["publication_id"],
              "manifest_path": relative(release / "manifest.json"),
              "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest(),
              "catalog_scope_series": len(existing), "catalog_scope_complete": scope_complete,
              "preserved_primary_series": preserved}
    for key in ("full_request_scope_complete", "full_numeric_coverage_complete", "evds_full_observation_coverage_complete"):
        result[key] = scope_complete and bool(summary.get(key, False))
    for filename, suffix, kind, row_count, missing_count in [
        ("observations_long.parquet", "observations", "native_observations", int(summary["observation_count"]), int(summary["source_null_count"])),
        ("analysis_series_catalog.parquet", "series_catalog", "metadata_catalog", len(selected), None),
        ("coverage.parquet", "coverage", "coverage_audit", len(coverage), None),
    ]:
        assets.append(make_asset(
            asset_id=f"evds.full_catalog.{suffix}", dataset_id="evds.full_catalog", source_system="TCMB_EVDS",
            source_organization="TCMB and upstream official producers", competition_scope="explicit_required_source",
            status="passed", data_kind=kind, native_frequency="series_defined", temporal_semantics="requires_semantic_review",
            geography_grain="series_defined", institution_grain="series_defined",
            coverage_start=manifest["target_start"], coverage_end=manifest["target_end"], row_count=row_count,
            metric_count=len(selected), missing_value_count=missing_count,
            progress_completed=int(summary["completed_request_series"]), progress_expected=len(selected),
            file_path=relative(release / filename), file_format="parquet",
            validation_file=relative(release / "validation.json"), source_url="https://evds3.tcmb.gov.tr/",
            description="Immutable native EVDS bulk publication; request completion, numeric coverage and semantics are distinct.",
            searchable_text="EVDS full catalog native observations request numeric coverage source evidence"))
    return result


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
        {
            "directory": "regional_housing_v1",
            "dataset_id": "evds.regional_housing_v1",
            "description": "Province housing sales and unit prices plus regional KFE and new-tenant rent indices.",
            "searchable_text": "EVDS province regional housing sales unit price KFE YKKE rent",
        },
        {
            "directory": "household_finance_v1",
            "dataset_id": "evds.household_finance_v1",
            "description": "Selected KKM components and household savings deposits for housing-demand analysis.",
            "searchable_text": "EVDS KKM household savings deposits financial alternatives",
        },
        {
            "directory": "demo_core_rates_v1",
            "dataset_id": "evds.demo_core_rates_v1",
            "description": "Verified core interest-rate controls: vehicle loan, commercial loan and short-term TL deposit rates.",
            "searchable_text": "EVDS tasit kredisi ticari kredi mevduat faiz orani vehicle commercial loan deposit rate",
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
        identity_values = [
            text_value(observed.get("canonical_index_code")) if observed else "",
            text_value(observed.get("historical_name")) if observed else "",
            text_value(observed.get("current_name")) if observed else "",
            text_value(observed.get("vintage_policy")) if observed else "",
        ]
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
                *identity_values,
            ]
            if value
        )
        identity_note = ""
        if observed and text_value(observed.get("canonical_index_code")):
            identity_note = (
                f" Canonical index code={text_value(observed.get('canonical_index_code'))}; "
                f"historical name={text_value(observed.get('historical_name'))}; "
                f"current name={text_value(observed.get('current_name'))}; "
                f"official name change effective={text_value(observed.get('name_change_effective_date'))}; "
                f"name-change evidence={text_value(observed.get('name_change_source_url'))}; "
                f"methodology={text_value(observed.get('methodology_source_url'))}."
            )
            if text_value(observed.get("scale_revision_effective_date")):
                identity_note += (
                    f" Stored value vintage={text_value(observed.get('vintage_policy'))}; "
                    f"official scale revision effective={text_value(observed.get('scale_revision_effective_date'))}; "
                    f"publisher revision factor={text_value(observed.get('scale_revision_factor'))}; "
                    f"scale-revision evidence={text_value(observed.get('scale_revision_source_url'))}; "
                    f"contemporaneous archive={text_value(observed.get('historical_archive_source_url'))}; "
                    "current EVDS history is not proof of the originally published pre-revision value."
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
                vintage_policy=(
                    text_value(observed.get("vintage_policy")) if observed else ""
                ),
                revision_status=(
                    text_value(observed.get("revision_status")) if observed else ""
                ),
                canonical_series_code=(
                    text_value(observed.get("canonical_index_code")) if observed else ""
                ),
                historical_name=(
                    text_value(observed.get("historical_name")) if observed else ""
                ),
                current_name=(
                    text_value(observed.get("current_name")) if observed else ""
                ),
                name_change_effective_date=(
                    text_value(observed.get("name_change_effective_date"))
                    if observed
                    else ""
                ),
                name_change_source_url=(
                    text_value(observed.get("name_change_source_url"))
                    if observed
                    else ""
                ),
                scale_revision_effective_date=(
                    text_value(observed.get("scale_revision_effective_date"))
                    if observed
                    else ""
                ),
                scale_revision_factor=(
                    observed.get("scale_revision_factor") if observed else None
                ),
                scale_revision_source_url=(
                    text_value(observed.get("scale_revision_source_url"))
                    if observed
                    else ""
                ),
                historical_archive_source_url=(
                    text_value(observed.get("historical_archive_source_url"))
                    if observed
                    else ""
                ),
                historical_original_scale_included=(
                    observed.get("historical_original_scale_included")
                    if observed
                    else None
                ),
                methodology_source_url=(
                    text_value(observed.get("methodology_source_url"))
                    if observed
                    else ""
                ),
                official_series_url=(
                    text_value(observed.get("official_index_page_url"))
                    if observed
                    else ""
                ),
                notes=(
                    text_value(observed.get("reason")) + identity_note
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
        served = pd.read_parquet(source_asset)[["target_period", series_code.replace(".", "_").replace("-", "_")]]
        served_values = served.iloc[:, 1]
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
                native_frequency="monthly",
                unit=text_value(observed.get("unit")),
                temporal_semantics=text_value(observed.get("temporal_semantics")),
                default_aggregation=text_value(observed.get("subperiod_aggregation")),
                geography_grain="series_defined",
                institution_grain="series_defined",
                coverage_start=text_value(observed.get("requested_start")),
                coverage_end=text_value(observed.get("requested_end")),
                observation_available=True,
                observation_count=len(served),
                missing_observation_count=int(served_values.isna().sum()),
                quality_status="passed",
                is_archive=False,
                source_asset=relative(source_asset),
                source_metadata_url=text_value(observed.get("metadata_url")),
                notes=(
                    f"{text_value(observed.get('reason'))} "
                    f"Source series: {source_code}; operation: "
                    f"{text_value(observed.get('derivation_operation'))}; factor: "
                    f"{text_value(observed.get('derivation_factor'))}."
                    f" Native source observations={int(observed.get('observation_count', 0))}; "
                    "catalog observation count refers to served monthly rows."
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
    base = PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_all_groups"
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
        dataset_id = f"bddk.monthly_all_groups.table_{table_no:02d}"
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
                institution_grain="all_public_bank_groups",
                coverage_start=text_value(row["first_month"]),
                coverage_end=text_value(row["last_month"]),
                observation_available=int(row["observation_count"]) > 0,
                observation_count=int(row["observation_count"]),
                missing_observation_count=int(row["missing_observation_count"]),
                quality_status=(
                    "not_applicable" if row["semantic_confidence"] == "not_applicable"
                    else "passed_high_confidence_inference" if row["semantic_confidence"] == "high_inferred"
                    else "requires_semantic_review" if row["unit"] in {"source_defined", "percent_or_ratio_source_defined"}
                    else semantic_validation["status"]
                ),
                is_archive=False,
                source_asset=relative(semantic_measurements_path),
                source_metadata_url="https://www.bddk.org.tr/BultenAylik/",
                notes=(
                    f"Transformation={row['transformation']}; "
                    f"semantic_confidence={row['semantic_confidence']}; "
                    f"label_variant_count={int(row['label_variant_count'])}."
                    f" Unit evidence={text_value(row.get('unit_evidence'))}; "
                    f"deduplication={text_value(row.get('deduplication_status'))}; "
                    f"caveat={text_value(row.get('aggregation_caveat'))}."
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
        dataset_id = f"bddk.monthly_all_groups.table_{table_no:02d}"
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
                institution_grain="all_public_bank_groups",
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
                description=f"BDDK monthly all-groups table: {table_summary['table_name']}.",
                searchable_text=f"BDDK monthly all groups {table_summary['table_name']}",
            )
        )
    semantic_measurements = pd.read_parquet(semantic_measurements_path)
    assets.append(
        make_asset(
            asset_id="bddk.monthly_all_groups.semantic_measurements_long",
            dataset_id="bddk.monthly_all_groups.semantic",
            source_system="BDDK_MONTHLY",
            source_organization="BDDK",
            competition_scope="derived_from_explicit_source",
            status=semantic_validation["status"],
            data_kind="semantic_observations_long",
            native_frequency="monthly",
            temporal_semantics="stock_flow_ratio_count_separated",
            geography_grain="national",
            institution_grain="all_public_bank_groups",
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
            usable_observation_count=("usable_value", "count"),
            analytically_resolved_count=("is_analytically_resolved", "sum"),
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
        notes = f"Official source unit: {text_value(row['source_unit_label'])}"
        if int(metric_stats["analytically_resolved_count"]) > 0:
            notes += (
                "; source-null values resolved for analytics="
                f"{int(metric_stats['analytically_resolved_count'])} via exact "
                "FinTurk functional-group identity; raw value remains null"
            )
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
                notes=notes,
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
    branch_fallback_path = base / "branch_zero_fallback_audit.parquet"
    branch_fallbacks = pd.read_parquet(branch_fallback_path)
    assets.append(
        make_asset(
            asset_id="bddk.finturk_all_groups_all_cities.branch_zero_fallback_audit",
            dataset_id="bddk.finturk_all_groups_all_cities",
            source_system="BDDK_FINTURK",
            source_organization="BDDK",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="identity_derived_zero_audit",
            native_frequency="quarterly",
            temporal_semantics="source_null_preserved_analytical_zero_proven",
            geography_grain="province",
            institution_grain="functional_bank_group",
            coverage_start=str(branch_fallbacks["quarter"].min()),
            coverage_end=str(branch_fallbacks["quarter"].max()),
            row_count=len(branch_fallbacks),
            column_count=len(branch_fallbacks.columns),
            metric_count=1,
            missing_value_count=int(branch_fallbacks["source_value"].isna().sum()),
            progress_completed=int(validation["branch_zero_fallback_audit_rows"]),
            progress_expected=int(validation["branch_zero_fallback_audit_rows"]),
            file_path=relative(branch_fallback_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=validation["branch_function_group_identity"]["metadata_url"],
            description=(
                "Audit proving source-null function-group branch counts are zero "
                "without overwriting the raw FinTurk values."
            ),
            searchable_text=(
                "BDDK FinTurk branch count source null derived zero function "
                "group identity audit"
            ),
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


def regional_housing_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base = PROJECT_ROOT / "data_pipeline" / "regional" / "processed"
    validation_path = base / "validation.json"
    validation = read_json(validation_path)
    panel_path = base / "province_quarter_housing_panel.parquet"
    dimension_path = base / "province_dimension.parquet"
    dictionary_path = base / "metric_dictionary.parquet"
    price_proxy_audit_path = base / "housing_unit_price_proxy_audit.parquet"
    panel = pd.read_parquet(panel_path)
    dimension = pd.read_parquet(dimension_path)
    dictionary = pd.read_parquet(dictionary_path)
    price_proxy_audit = pd.read_parquet(price_proxy_audit_path)

    assets = [
        make_asset(
            asset_id="regional.housing_v1.province_quarter_panel",
            dataset_id="regional.housing_v1",
            source_system="REGIONAL_HOUSING_ANALYSIS",
            source_organization="BDDK, TCMB and TÜİK",
            competition_scope="supporting_derived_analysis",
            status=validation["status"],
            data_kind="analysis_panel",
            native_frequency="quarterly",
            temporal_semantics="metric_defined",
            geography_grain="province",
            institution_grain="banking_sector_total",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(panel),
            column_count=len(panel.columns),
            metric_count=len(dictionary),
            missing_value_count=int(panel.isna().sum().sum()),
            progress_completed=int(validation["analysis_ready_rows"]),
            progress_expected=int(validation["expected_row_count"]),
            file_path=relative(panel_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=(
                "https://evds3.tcmb.gov.tr/ and "
                "https://www.bddk.org.tr/BultenFinturk/ and "
                "https://veriportali.tuik.gov.tr/"
            ),
            description=(
                "One row per province and quarter, combining housing sales, "
                "prices, regional rent indices and FinTurk household-finance measures."
            ),
            searchable_text="province quarter housing credit sales price rent gold deposit regional panel",
        ),
        make_asset(
            asset_id="regional.housing_v1.province_dimension",
            dataset_id="regional.housing_v1",
            source_system="REGIONAL_HOUSING_ANALYSIS",
            source_organization="BDDK, TCMB and TÜİK",
            competition_scope="supporting_derived_analysis",
            status=validation["status"],
            data_kind="dimension",
            native_frequency="not_applicable",
            temporal_semantics="mapping",
            geography_grain="province_to_housing_price_region",
            institution_grain="not_applicable",
            coverage_start="",
            coverage_end="",
            row_count=len(dimension),
            column_count=len(dimension.columns),
            metric_count=0,
            missing_value_count=int(dimension.isna().sum().sum()),
            progress_completed=len(dimension),
            progress_expected=81,
            file_path=relative(dimension_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=(
                "https://evds3.tcmb.gov.tr/ and "
                "https://www.bddk.org.tr/BultenFinturk/ and "
                "https://veriportali.tuik.gov.tr/"
            ),
            description="Validated mapping of 81 provinces to EVDS source series and KFE/YKKE regions.",
            searchable_text="province dimension EVDS KFE YKKE FinTurk mapping",
        ),
        make_asset(
            asset_id="regional.housing_v1.metric_dictionary",
            dataset_id="regional.housing_v1",
            source_system="REGIONAL_HOUSING_ANALYSIS",
            source_organization="BDDK, TCMB and TÜİK",
            competition_scope="supporting_derived_analysis",
            status=validation["status"],
            data_kind="metric_dictionary",
            native_frequency="quarterly",
            temporal_semantics="metadata",
            geography_grain="province",
            institution_grain="banking_sector_total",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(dictionary),
            column_count=len(dictionary.columns),
            metric_count=len(dictionary),
            missing_value_count=int(dictionary.isna().sum().sum()),
            progress_completed=len(dictionary),
            progress_expected=len(dictionary),
            file_path=relative(dictionary_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="",
            description="Metric semantics, formulas and cautions for the province-quarter panel.",
            searchable_text="regional housing metric dictionary formulas cautions",
        ),
        make_asset(
            asset_id="regional.housing_v1.housing_unit_price_proxy_audit",
            dataset_id="regional.housing_v1",
            source_system="REGIONAL_HOUSING_ANALYSIS",
            source_organization="BDDK, TCMB and TÜİK",
            competition_scope="supporting_derived_analysis",
            status=validation["status"],
            data_kind="explicit_proxy_audit",
            native_frequency="quarterly",
            temporal_semantics="same_region_same_quarter_proxy",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(price_proxy_audit),
            column_count=len(price_proxy_audit.columns),
            metric_count=0,
            missing_value_count=int(price_proxy_audit.isna().sum().sum()),
            progress_completed=int(
                validation["housing_unit_price_regional_median_proxy_applied_count"]
            ),
            progress_expected=int(validation["housing_unit_price_proxy_audit_rows"]),
            file_path=relative(price_proxy_audit_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="https://evds3.tcmb.gov.tr/",
            description=(
                "Audit rows for official province unit-price gaps and the explicit "
                "same-region, same-quarter official-median proxy."
            ),
            searchable_text=(
                "province housing unit price source gap regional median proxy "
                "provenance peer count audit"
            ),
        ),
    ]

    metrics: list[dict[str, Any]] = []
    for row in dictionary.to_dict("records"):
        code = str(row["metric_code"])
        value_type = text_value(row.get("value_type")) or "numeric"
        if value_type == "categorical":
            values = panel[code].astype("string").replace("", pd.NA)
        else:
            values = pd.to_numeric(panel[code], errors="coerce")
        derivation = text_value(row.get("derivation"))
        caution = text_value(row.get("caution"))
        metrics.append(
            make_metric(
                metric_id=f"regional_housing:{code}",
                dataset_id="regional.housing_v1",
                source_system="REGIONAL_HOUSING_ANALYSIS",
                source_organization="BDDK, TCMB and TÜİK",
                competition_scope="supporting_derived_analysis",
                source_metric_code=code,
                metric_name_tr=text_value(row.get("metric_name_tr")),
                metric_name_en="",
                group_name="Province-quarter housing and household finance",
                role="regional_housing_analysis",
                dimension="province",
                native_frequency="quarterly",
                unit=text_value(row.get("unit")),
                temporal_semantics=text_value(row.get("temporal_semantics")),
                default_aggregation="identity_at_province_quarter",
                geography_grain="province",
                institution_grain="banking_sector_total_or_not_applicable",
                coverage_start=validation["coverage_start"],
                coverage_end=validation["coverage_end"],
                observation_available=bool(values.notna().any()),
                observation_count=int(values.notna().sum()),
                missing_observation_count=int(values.isna().sum()),
                quality_status=validation["status"],
                is_archive=False,
                source_asset=relative(panel_path),
                source_metadata_url="",
                notes=" ".join(
                    part
                    for part in [f"value_type={value_type}.", derivation, caution]
                    if part
                ),
                searchable_text=" | ".join(
                    part
                    for part in [
                        code,
                        text_value(row.get("metric_name_tr")),
                        value_type,
                        derivation,
                        caution,
                    ]
                    if part
                ),
            )
        )
    return assets, metrics


def tuik_province_sales_assets_and_metrics() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    base = PROJECT_ROOT / "data_pipeline" / "tuik" / "province_housing_sales_v1"
    processed = base / "processed"
    validation_path = processed / "validation.json"
    validation = read_json(validation_path)
    request = read_json(base / "request.json")
    response = read_json(base / "response_metadata.json")
    raw_path = base / "raw" / "province_housing_sales.csv.gz"
    monthly_path = processed / "monthly_sales_long.parquet"
    reconciliation_path = processed / "evds_reconciliation.parquet"
    fallback_path = processed / "identity_zero_fallbacks.parquet"
    monthly = pd.read_parquet(monthly_path)
    reconciliation = pd.read_parquet(reconciliation_path)
    fallbacks = pd.read_parquet(fallback_path)

    assets = [
        make_asset(
            asset_id="tuik.province_housing_sales_v1.raw_snapshot",
            dataset_id="tuik.province_housing_sales_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="supporting_official_source",
            status=validation["status"],
            data_kind="raw_source_export",
            native_frequency="monthly_and_annual",
            temporal_semantics="source_reported",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start="2013-01",
            coverage_end="2026-07",
            row_count=int(validation["raw_source_rows"]),
            column_count=12,
            metric_count=None,
            missing_value_count=None,
            progress_completed=int(validation["raw_source_rows"]),
            progress_expected=int(validation["raw_source_rows"]),
            file_path=relative(raw_path),
            file_format="gzip_csv",
            validation_file=relative(validation_path),
            source_url=request["url"],
            vintage_policy=validation["vintage_policy"],
            release_date=validation["source_vintage_at"],
            revision_status=validation["revision_status"],
            description=(
                "Exact TÜİK province housing-sales bulk CSV response with "
                f"raw SHA-256 {response['raw_response_sha256']}."
            ),
            searchable_text="TÜİK province monthly housing sales raw official export",
        ),
        make_asset(
            asset_id="tuik.province_housing_sales_v1.monthly_sales_long",
            dataset_id="tuik.province_housing_sales_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="supporting_official_source",
            status=validation["status"],
            data_kind="observations_long",
            native_frequency="monthly",
            temporal_semantics="monthly_flow",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(monthly),
            column_count=len(monthly.columns),
            metric_count=int(validation["metric_count"]),
            missing_value_count=int(monthly["direct_value"].isna().sum()),
            progress_completed=int(monthly["value"].notna().sum()),
            progress_expected=len(monthly),
            file_path=relative(monthly_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=request["url"],
            vintage_policy=validation["vintage_policy"],
            release_date=validation["source_vintage_at"],
            revision_status=validation["revision_status"],
            description=(
                "Province-month housing sales. Direct source-row absence and "
                "official identity-derived zeros are stored separately."
            ),
            searchable_text="TÜİK province monthly housing sales total mortgaged other first second",
        ),
        make_asset(
            asset_id="tuik.province_housing_sales_v1.evds_reconciliation",
            dataset_id="tuik.province_housing_sales_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK and TCMB EVDS",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="cross_source_reconciliation",
            native_frequency="monthly",
            temporal_semantics="exact_value_comparison",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(reconciliation),
            column_count=len(reconciliation.columns),
            metric_count=0,
            missing_value_count=int(reconciliation.isna().sum().sum()),
            progress_completed=int(validation["evds_exact_matches"]),
            progress_expected=int(validation["evds_exact_matches"]),
            file_path=relative(reconciliation_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=request["url"],
            description="Exact comparison of common TÜİK and EVDS province-month sales values.",
            searchable_text="TÜİK EVDS province housing sales reconciliation exact match",
        ),
        make_asset(
            asset_id="tuik.province_housing_sales_v1.identity_zero_fallbacks",
            dataset_id="tuik.province_housing_sales_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="identity_derived_fallback_audit",
            native_frequency="monthly",
            temporal_semantics="official_identity_derived_zero",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=str(fallbacks["month"].min()),
            coverage_end=str(fallbacks["month"].max()),
            row_count=len(fallbacks),
            column_count=len(fallbacks.columns),
            metric_count=1,
            missing_value_count=int(fallbacks["direct_value"].isna().sum()),
            progress_completed=len(fallbacks),
            progress_expected=len(fallbacks),
            file_path=relative(fallback_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=request["url"],
            description=(
                "Audit rows where an omitted mortgaged-sales source row is "
                "proven zero by official total sales equalling official other sales."
            ),
            searchable_text="TÜİK mortgage sales zero fallback identity audit",
        ),
    ]

    labels = {
        "housing_sales_total_count": "Toplam konut satışı",
        "housing_sales_mortgaged_count": "İpotekli konut satışı",
        "housing_sales_other_count": "Diğer konut satışı",
        "housing_sales_first_hand_count": "İlk el konut satışı",
        "housing_sales_second_hand_count": "İkinci el konut satışı",
    }
    metrics: list[dict[str, Any]] = []
    for code, rows in monthly.groupby("metric_code", sort=True):
        direct_missing = int(rows["direct_value"].isna().sum())
        identity_derived = int(rows["is_identity_derived"].sum())
        metrics.append(
            make_metric(
                metric_id=f"tuik_province_housing_sales:{code}",
                dataset_id="tuik.province_housing_sales_v1",
                source_system="TUIK_DATA_PORTAL",
                source_organization="TÜİK",
                competition_scope="supporting_official_source",
                source_metric_code=str(code),
                metric_name_tr=labels[str(code)],
                metric_name_en="",
                group_name="İl bazlı konut satışları",
                role="housing_market_outcome",
                dimension="province",
                native_frequency="monthly",
                unit="count",
                temporal_semantics="flow",
                default_aggregation="sum",
                geography_grain="province",
                institution_grain="not_applicable",
                coverage_start=validation["coverage_start"],
                coverage_end=validation["coverage_end"],
                observation_available=bool(rows["value"].notna().any()),
                observation_count=int(rows["value"].notna().sum()),
                missing_observation_count=direct_missing,
                quality_status=validation["status"],
                is_archive=False,
                source_asset=relative(monthly_path),
                source_metadata_url=request["url"],
                vintage_policy=validation["vintage_policy"],
                release_date=validation["source_vintage_at"],
                revision_status=validation["revision_status"],
                notes=(
                    f"Direct source-row absence={direct_missing}; "
                    f"official identity-derived zero={identity_derived}. "
                    "Current official bulk snapshot uses the housing definition "
                    "revised on 2026-02-19; do not present it as the values first "
                    "published in historical bulletins."
                ),
                searchable_text=(
                    f"{code} {labels[str(code)]} TÜİK il aylık konut satışı "
                    "güncel revize current revised latest vintage"
                ),
            )
        )
    return assets, metrics


def tuik_first_published_sales_assets_and_metrics() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    base = (
        PROJECT_ROOT
        / "data_pipeline"
        / "tuik"
        / "province_housing_sales_first_published_v1"
    )
    processed = base / "processed"
    manifest_path = base / "manifest.json"
    validation_path = processed / "validation.json"
    monthly_path = processed / "monthly_sales_first_published.parquet"
    comparison_path = processed / "revision_comparison.parquet"
    manifest = read_json(manifest_path)
    validation = read_json(validation_path)
    monthly = pd.read_parquet(monthly_path)
    comparison = pd.read_parquet(comparison_path)
    source_url = "https://veriportali.tuik.gov.tr/tr/search?q=konut"
    first_release = min(item["release_at"] for item in manifest["records"])
    last_release = max(item["release_at"] for item in manifest["records"])

    assets = [
        make_asset(
            asset_id="tuik.province_housing_sales_first_published_v1.source_manifest",
            dataset_id="tuik.province_housing_sales_first_published_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="supporting_official_source_historical_vintage",
            status=validation["status"],
            data_kind="official_bulletin_source_manifest",
            native_frequency="monthly",
            temporal_semantics="first_publication_vintage",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=validation["publication_count"],
            column_count=None,
            metric_count=validation["metric_count"],
            missing_value_count=0,
            progress_completed=validation["publication_count"],
            progress_expected=validation["publication_count"],
            file_path=relative(manifest_path),
            file_format="json",
            validation_file=relative(validation_path),
            source_url=source_url,
            vintage_policy=validation["vintage_policy"],
            release_date=last_release,
            revision_status="historical_first_publication_pre_2026_revision",
            description=(
                "Exact official bulletin and workbook identities for each monthly "
                "province housing-sales first publication."
            ),
            searchable_text="TÜİK historical first publication bulletin housing sales vintage",
        ),
        make_asset(
            asset_id="tuik.province_housing_sales_first_published_v1.monthly_sales",
            dataset_id="tuik.province_housing_sales_first_published_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="supporting_official_source_historical_vintage",
            status=validation["status"],
            data_kind="observations_long",
            native_frequency="monthly",
            temporal_semantics="monthly_flow_first_publication",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(monthly),
            column_count=len(monthly.columns),
            metric_count=validation["metric_count"],
            missing_value_count=int(monthly["value"].isna().sum()),
            progress_completed=int(monthly["value"].notna().sum()),
            progress_expected=len(monthly),
            file_path=relative(monthly_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url=source_url,
            vintage_policy=validation["vintage_policy"],
            release_date=last_release,
            revision_status="historical_first_publication_pre_2026_revision",
            description=(
                "Province-month values copied from each reference month's official "
                "TÜİK bulletin workbook, before the 2026 methodology revision."
            ),
            searchable_text=(
                "TÜİK province monthly housing sales first published original "
                "historical bulletin pre revision vintage"
            ),
        ),
        make_asset(
            asset_id="tuik.province_housing_sales_first_published_v1.revision_comparison",
            dataset_id="tuik.province_housing_sales_first_published_v1",
            source_system="TUIK_DATA_PORTAL",
            source_organization="TÜİK",
            competition_scope="derived_quality_evidence",
            status=validation["status"],
            data_kind="official_vintage_comparison",
            native_frequency="monthly",
            temporal_semantics="first_publication_vs_current_revision",
            geography_grain="province",
            institution_grain="not_applicable",
            coverage_start=validation["coverage_start"],
            coverage_end=validation["coverage_end"],
            row_count=len(comparison),
            column_count=len(comparison.columns),
            metric_count=0,
            missing_value_count=int(comparison.isna().sum().sum()),
            progress_completed=len(comparison),
            progress_expected=len(comparison),
            file_path=relative(comparison_path),
            file_format="parquet",
            validation_file=relative(validation_path),
            source_url="https://veriportali.tuik.gov.tr/tr/press/58340",
            vintage_policy="explicit_side_by_side_official_vintages",
            release_date="2026-02-19T10:00:00",
            revision_status="comparison_only",
            description=(
                "Cell-level comparison of first-published bulletin values with the "
                "current official bulk series after the 2026 definition revision."
            ),
            searchable_text="TÜİK housing sales revision first publication current comparison",
        ),
    ]

    labels = {
        "housing_sales_total_count": "İlk yayımlanan toplam konut satışı",
        "housing_sales_mortgaged_count": "İlk yayımlanan ipotekli konut satışı",
        "housing_sales_other_count": "İlk yayımlanan diğer konut satışı",
        "housing_sales_first_hand_count": "İlk yayımlanan ilk el konut satışı",
        "housing_sales_second_hand_count": "İlk yayımlanan ikinci el konut satışı",
    }
    metrics: list[dict[str, Any]] = []
    for code, rows in monthly.groupby("metric_code", sort=True):
        metrics.append(
            make_metric(
                metric_id=f"tuik_province_housing_sales_first_published:{code}",
                dataset_id="tuik.province_housing_sales_first_published_v1",
                source_system="TUIK_DATA_PORTAL",
                source_organization="TÜİK",
                competition_scope="supporting_official_source_historical_vintage",
                source_metric_code=str(code),
                metric_name_tr=labels[str(code)],
                metric_name_en="",
                group_name="İl bazlı konut satışları, ilk yayımlanan bülten vintage'ı",
                role="housing_market_outcome",
                dimension="province",
                native_frequency="monthly",
                unit="count",
                temporal_semantics="flow",
                default_aggregation="sum",
                geography_grain="province",
                institution_grain="not_applicable",
                coverage_start=validation["coverage_start"],
                coverage_end=validation["coverage_end"],
                observation_available=True,
                observation_count=int(rows["value"].notna().sum()),
                missing_observation_count=int(rows["value"].isna().sum()),
                quality_status=validation["status"],
                is_archive=True,
                source_asset=relative(monthly_path),
                source_metadata_url=source_url,
                vintage_policy=validation["vintage_policy"],
                release_date=f"{first_release}..{last_release}",
                revision_status="historical_first_publication_pre_2026_revision",
                notes=(
                    "Every monthly value comes from the table attached to that "
                    "month's first official bulletin. The series predates TÜİK's "
                    "2026 housing-definition revision and must not be silently "
                    "mixed with the current revised bulk series."
                ),
                searchable_text=(
                    f"{code} {labels[str(code)]} TÜİK il aylık konut satışı "
                    "ilk yayın orijinal tarihsel bülten first published vintage"
                ),
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


def risk_center_assets_and_metrics() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]]
]:
    base = (
        PROJECT_ROOT
        / "data_pipeline"
        / "risk_center"
        / "monthly_housing_v1"
    )
    processed = base / "processed"
    manifest_path = base / "manifest.json"
    validation_path = processed / "validation.json"
    manifest = read_json(manifest_path)
    validation = read_json(validation_path)
    official_url = str(manifest["official_listing_url"])

    panel_path = processed / "housing_credit_monthly.parquet"
    vintages_path = processed / "housing_metric_vintages.parquet"
    overlap_path = processed / "overlap_revision_audit.parquet"
    dictionary_path = processed / "metric_dictionary.parquet"
    panel = pd.read_parquet(panel_path)
    vintages = pd.read_parquet(vintages_path)
    overlap = pd.read_parquet(overlap_path)
    dictionary = pd.read_parquet(dictionary_path)

    asset_specs = [
        (
            "housing_credit_monthly",
            panel_path,
            panel,
            "monthly_analysis_panel",
            "metric_defined",
            "month",
            len(dictionary),
            (
                "Monthly housing-credit balance, borrower, average risk, NPL ratio "
                "and first-time user measures selected from the latest official "
                "Risk Center bulletin vintage."
            ),
        ),
        (
            "housing_metric_vintages",
            vintages_path,
            vintages,
            "source_chart_vintages",
            "official_publication_vintage",
            "observation_month",
            len(dictionary),
            "All extracted official chart vintages, including overlapping months.",
        ),
        (
            "overlap_revision_audit",
            overlap_path,
            overlap,
            "source_revision_audit",
            "official_vintage_comparison",
            "observation_month",
            0,
            "Audit of overlapping official chart values and later revisions.",
        ),
        (
            "metric_dictionary",
            dictionary_path,
            dictionary,
            "metric_dictionary",
            "metadata",
            None,
            len(dictionary),
            "Metric semantics, units, aggregation rules and interpretation cautions.",
        ),
    ]
    assets: list[dict[str, Any]] = []
    for (
        asset_name,
        path,
        frame,
        data_kind,
        semantics,
        period_column,
        metric_count,
        description,
    ) in asset_specs:
        coverage_start = str(frame[period_column].min()) if period_column else ""
        coverage_end = str(frame[period_column].max()) if period_column else ""
        assets.append(
            make_asset(
                asset_id=f"risk_center.monthly_housing_v1.{asset_name}",
                dataset_id="risk_center.monthly_housing_v1",
                source_system="TBB_RISK_CENTER",
                source_organization="Türkiye Bankalar Birliği Risk Merkezi",
                competition_scope="supporting_source_not_explicitly_required",
                status=validation["status"],
                data_kind=data_kind,
                native_frequency="monthly",
                temporal_semantics=semantics,
                geography_grain="national",
                institution_grain="risk_center_reporting_scope",
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                row_count=len(frame),
                column_count=len(frame.columns),
                metric_count=metric_count,
                missing_value_count=int(frame.isna().sum().sum()),
                progress_completed=int(validation["publication_count"]),
                progress_expected=6,
                file_path=relative(path),
                file_format="parquet",
                validation_file=relative(validation_path),
                source_url=official_url,
                description=description,
                searchable_text=(
                    f"TBB Risk Merkezi monthly housing credit {asset_name}"
                ),
            )
        )

    role_by_metric = {
        "housing_credit_balance_billion_try": "credit_balance",
        "housing_credit_borrower_count_million_person": "credit_borrower_count",
        "housing_credit_average_balance_try": "credit_average_balance",
        "housing_credit_npl_ratio_pct": "credit_quality",
        "first_time_housing_credit_users_thousand_person": "first_time_credit_users",
    }
    metrics: list[dict[str, Any]] = []
    for row in dictionary.to_dict("records"):
        code = str(row["metric_code"])
        values = pd.to_numeric(panel[code], errors="coerce")
        caution = text_value(row.get("caution"))
        metrics.append(
            make_metric(
                metric_id=f"tbb_risk_center:{code}",
                dataset_id="risk_center.monthly_housing_v1",
                source_system="TBB_RISK_CENTER",
                source_organization="Türkiye Bankalar Birliği Risk Merkezi",
                competition_scope="supporting_source_not_explicitly_required",
                source_metric_code=code,
                metric_name_tr=text_value(row.get("metric_name_tr")),
                metric_name_en="",
                group_name="Risk Merkezi aylık konut kredisi göstergeleri",
                role=role_by_metric[code],
                dimension="national",
                native_frequency="monthly",
                unit=text_value(row.get("unit")),
                temporal_semantics=text_value(row.get("temporal_semantics")),
                default_aggregation=text_value(row.get("default_aggregation")),
                geography_grain="national",
                institution_grain="risk_center_reporting_scope",
                coverage_start=validation["target_coverage_start"],
                coverage_end=validation["target_coverage_end"],
                observation_available=bool(values.notna().any()),
                observation_count=int(values.notna().sum()),
                missing_observation_count=int(values.isna().sum()),
                quality_status=validation["status"],
                is_archive=False,
                source_asset=relative(panel_path),
                source_metadata_url=official_url,
                notes=(
                    f"{caution} Published chart rounding is preserved. "
                    "Overlaps use the latest official publication while every "
                    "vintage remains auditable."
                ),
                searchable_text=" | ".join(
                    [
                        code,
                        text_value(row.get("metric_name_tr")),
                        caution,
                        "TBB Risk Merkezi monthly housing credit",
                    ]
                ),
            )
        )
    return assets, metrics


def weekly_bddk_assets_and_metrics() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_dir = PROJECT_ROOT / "data_pipeline" / "bddk" / "weekly_all_groups"
    config = read_json(raw_dir / "request_config.json")
    expected = int(config["period_count"]) * len(config["tables"]) * len(config["groups"])
    completed = len(list((raw_dir / "raw").glob("*_info.json")))
    summary_path = raw_dir / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    download_complete = summary.get("status") == "complete"
    status = "complete" if download_complete else "in_progress"
    asset = make_asset(
        asset_id="bddk.weekly_all_groups.raw_snapshot",
        dataset_id="bddk.weekly_all_groups",
        source_system="BDDK_WEEKLY",
        source_organization="BDDK",
        competition_scope="explicit_required_source",
        status=status,
        data_kind="raw_source_collection",
        native_frequency="weekly",
        temporal_semantics="source_reported",
        geography_grain="national",
        institution_grain="all_public_bank_groups",
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
        searchable_text="BDDK weekly all groups bulletin raw snapshot",
    )
    assets = [asset]
    metrics: list[dict[str, Any]] = []

    processed_dir = PROJECT_ROOT / "data_pipeline" / "bddk" / "processed" / "weekly_all_groups"
    processed_validation_path = processed_dir / "validation.json"
    if processed_validation_path.exists():
        validation = read_json(processed_validation_path)
        measurements_path = processed_dir / "measurements_long.parquet"
        measurements = pd.read_parquet(measurements_path)
        assets.append(
            make_asset(
                asset_id="bddk.weekly_all_groups.measurements_long",
                dataset_id="bddk.weekly_all_groups",
                source_system="BDDK_WEEKLY",
                source_organization="BDDK",
                competition_scope="explicit_required_source",
                status=validation["status"],
                data_kind="observations_long",
                native_frequency="weekly",
                temporal_semantics="metric_defined_or_review_required",
                geography_grain="national",
                institution_grain="all_public_bank_groups",
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
                searchable_text="BDDK weekly all groups measurements long",
            )
        )
        missingness_path = processed_dir / "missingness_audit.parquet"
        if missingness_path.exists():
            missingness = pd.read_parquet(missingness_path)
            assets.append(
                make_asset(
                    asset_id="bddk.weekly_all_groups.missingness_audit",
                    dataset_id="bddk.weekly_all_groups",
                    source_system="BDDK_WEEKLY",
                    source_organization="BDDK",
                    competition_scope="derived_quality_evidence",
                    status=validation["status"],
                    data_kind="missingness_audit",
                    native_frequency="weekly",
                    temporal_semantics="source_missingness_classification",
                    geography_grain="national",
                    institution_grain="all_public_bank_groups",
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
            reviewed_credit_stock = int(table_id) == 289
            metrics.append(
                make_metric(
                    metric_id=f"bddk_weekly:{slug(source_code)}",
                    dataset_id="bddk.weekly_all_groups",
                    source_system="BDDK_WEEKLY",
                    source_organization="BDDK",
                    competition_scope="explicit_required_source",
                    source_metric_code=source_code,
                    metric_name_tr=f"{label} [{dimension}]",
                    metric_name_en="",
                    group_name=text_value(rows["table_name"].iloc[0]),
                    role="weekly_banking_supervision",
                    dimension=str(dimension),
                    native_frequency=(
                        "weekly_observed" if reviewed_credit_stock else "weekly"
                    ),
                    unit=text_value(rows["source_unit"].dropna().iloc[0]) if rows["source_unit"].notna().any() else "",
                    temporal_semantics=(
                        "source_date_stock"
                        if reviewed_credit_stock
                        else "source_reported_requires_semantic_review"
                    ),
                    default_aggregation=(
                        "last"
                        if reviewed_credit_stock
                        else "semantic_policy_required_for_resampling"
                    ),
                    geography_grain="national",
                    institution_grain="all_public_bank_groups",
                    coverage_start=str(rows["observation_date"].min()),
                    coverage_end=str(rows["observation_date"].max()),
                    observation_available=bool(values.notna().any()),
                    observation_count=int(values.notna().sum()),
                    missing_observation_count=int(values.isna().sum()),
                    quality_status=validation["status"],
                    is_archive=False,
                    source_asset=relative(measurements_path),
                    source_metadata_url=(
                        BDDK_WEEKLY_EXPLANATION_URL
                        if reviewed_credit_stock
                        else config["source_url"]
                    ),
                    revision_status=(
                        "provisional_revisable" if reviewed_credit_stock else None
                    ),
                    notes=(
                        "Source labels, currencies, and actual observation dates are preserved. "
                        "Table 289 credit amounts are source-date stock balances. BDDK states that "
                        "the weekly bulletin uses temporary financial statements and later issues "
                        "may revise a period."
                        if reviewed_credit_stock
                        else "Source labels and currencies are preserved."
                    ),
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
    full_evds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    full_evds = full_evds or {}
    full_complete = bool(full_evds.get("evds_full_observation_coverage_complete", False))
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
        "evds.regional_housing_v1",
        "evds.household_finance_v1",
        "tuik.province_housing_sales_v1",
        "tuik.province_housing_sales_first_published_v1",
        "bddk.monthly_all_groups.table_01",
        "bddk.finturk_all_groups_all_cities",
        "bddk.weekly_all_groups",
        "regional.housing_v1",
        "risk_center.monthly_housing_v1",
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
            ("local_snapshot_validated_evds_numeric_coverage_complete" if full_complete
             else "local_snapshot_validated_evds_coverage_incomplete")
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
        "evds_metadata_series": int(metrics["source_system"].eq("TCMB_EVDS").sum()),
        "evds_numeric_series": int((selected_evds["observation_count"] > selected_evds["missing_observation_count"]).sum()),
        "evds_full_observation_coverage_complete": full_complete,
        "evds_full_request_scope_complete": bool(full_evds.get("full_request_scope_complete", False)),
        "evds_full_catalog": full_evds,
        "weekly_bddk": weekly_progress,
        "duplicate_asset_ids": duplicate_assets,
        "duplicate_metric_ids": duplicate_metrics,
        "absolute_asset_paths": absolute_paths,
        "missing_asset_files": missing_asset_files,
        "missing_required_datasets": missing_required,
        "known_source_gaps": [
            *(["EVDS full observation coverage is incomplete; request completion and metadata count do not prove numeric coverage."] if not full_complete else []),
            "Bulk EVDS native observations require independent semantic review before economic aggregation.",
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


def build(output_dir: Path, *, include_full_catalog: bool = False) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []

    for builder in [
        evds_assets_and_metrics,
        monthly_bddk_assets_and_metrics,
        finturk_assets_and_metrics,
        tuik_province_sales_assets_and_metrics,
        tuik_first_published_sales_assets_and_metrics,
        regional_housing_assets_and_metrics,
        tbb_assets_and_metrics,
        risk_center_assets_and_metrics,
    ]:
        source_assets, source_metrics = builder()
        assets.extend(source_assets)
        metrics.extend(source_metrics)

    weekly_assets, weekly_metrics, weekly_progress = weekly_bddk_assets_and_metrics()
    assets.extend(weekly_assets)
    metrics.extend(weekly_metrics)
    assets.extend(event_assets())
    assets.extend(quality_assets())
    include_legacy_evds(assets, metrics)
    full_evds = include_full_catalog_evds(assets, metrics) if include_full_catalog else {"present": False}

    asset_frame = pd.DataFrame(assets, columns=ASSET_COLUMNS).sort_values(
        ["source_system", "dataset_id", "asset_id"], kind="stable"
    )
    metric_frame = pd.DataFrame(metrics, columns=METRIC_COLUMNS).sort_values(
        ["source_system", "dataset_id", "metric_id"], kind="stable"
    )
    validation = validate_catalog(asset_frame, metric_frame, weekly_progress, full_evds)
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
    publication = parser.add_mutually_exclusive_group()
    publication.add_argument(
        "--with-full-catalog",
        action="store_true",
        help="Include the local, separately published full EVDS release.",
    )
    publication.add_argument(
        "--without-full-catalog",
        action="store_true",
        help="Deprecated explicit spelling of the portable default.",
    )
    args = parser.parse_args()
    result = build(
        args.output.expanduser().resolve(),
        include_full_catalog=args.with_full_catalog,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
