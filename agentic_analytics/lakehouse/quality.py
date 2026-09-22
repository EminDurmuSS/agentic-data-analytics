"""Enforced release gates for the checked-in KKB source snapshot.

These gates validate this source package, not the completeness of all EVDS data
or clinical/economic suitability of arbitrary newly uploaded datasets.
"""
from pathlib import Path
import json

import duckdb


def validate_full_evds(connection: duckdb.DuckDBPyConnection) -> dict:
    """Validate an optional copied publication without consulting mutable CURRENT.

    Completeness refers to the collector's declared target, and never follows
    merely from HTTP success, nonempty data, or availability of source metadata.
    """
    tables = {f"{r[0]}.{r[1]}" for r in connection.execute(
        "SELECT table_schema,table_name FROM information_schema.tables").fetchall()}
    required = {"evds.full_catalog_observations", "evds.full_catalog_series_catalog",
                "evds.full_catalog_coverage", "evds.full_catalog_publication"}
    if not tables.intersection(required):
        return {"present": False, "full_request_scope_complete": False,
                "full_numeric_coverage_complete": False, "evds_full_observation_coverage_complete": False}
    if not required.issubset(tables):
        raise ValueError(f"Partial EVDS bulk installation: {sorted(required - tables)}")
    checks = []

    def zero(name, sql):
        violations = int(connection.execute(sql).fetchone()[0])
        checks.append({"check": name, "violations": violations, "passed": violations == 0})

    zero("full_evds_publication_singleton", "SELECT abs(count(*)-1) FROM evds.full_catalog_publication")
    zero("full_evds_metadata_identity_unique", "SELECT count(*)-count(DISTINCT series_code) FROM evds.full_catalog_series_catalog")
    zero("full_evds_coverage_identity_unique", "SELECT count(*)-count(DISTINCT series_code) FROM evds.full_catalog_coverage")
    zero("full_evds_native_key_unique", """SELECT count(*) FROM (
        SELECT series_code,period FROM evds.full_catalog_observations GROUP BY ALL HAVING count(*)>1)""")
    zero("full_evds_required_keys", """SELECT count(*) FROM evds.full_catalog_observations
        WHERE series_code IS NULL OR period IS NULL OR period_start IS NULL OR period_end IS NULL""")
    zero("full_evds_values_finite", "SELECT count(*) FROM evds.full_catalog_observations WHERE value IS NOT NULL AND NOT isfinite(value)")
    zero("full_evds_source_nulls_preserved", "SELECT count(*) FROM evds.full_catalog_observations WHERE is_missing IS DISTINCT FROM (value IS NULL)")
    zero("full_evds_response_evidence", """SELECT count(*) FROM evds.full_catalog_observations
        WHERE source_response_file IS NULL OR source_response_file='' OR source_response_sha256 IS NULL
        OR NOT regexp_full_match(source_response_sha256,'[0-9a-f]{64}') OR source_row_index IS NULL""")
    zero("full_evds_coverage_metadata_identity", """SELECT count(*) FROM evds.full_catalog_coverage c
        FULL JOIN evds.full_catalog_series_catalog s USING(series_code) WHERE c.series_code IS NULL OR s.series_code IS NULL""")
    zero("full_evds_counts_match_canonical_rows", """WITH actual AS (
        SELECT series_code,count(*) n,count(value) numeric_n,count(*)-count(value) null_n
        FROM evds.full_catalog_observations GROUP BY series_code)
        SELECT count(*) FROM evds.full_catalog_coverage c FULL JOIN actual a USING(series_code)
        WHERE c.series_code IS NULL OR c.observation_count<>coalesce(a.n,0)
        OR c.numeric_observation_count<>coalesce(a.numeric_n,0)
        OR c.missing_observation_count<>coalesce(a.null_n,0)
        OR c.physical_present IS DISTINCT FROM (coalesce(a.n,0)>0)""")
    zero("full_evds_frequency_matches_coverage", """SELECT count(*) FROM evds.full_catalog_observations o
        JOIN evds.full_catalog_coverage c USING(series_code) WHERE o.native_frequency IS DISTINCT FROM c.native_frequency""")
    zero("full_evds_every_series_discoverable", """SELECT count(*) FROM evds.full_catalog_coverage c
        ANTI JOIN catalog.metrics m ON m.source_system='TCMB_EVDS' AND m.source_metric_code=c.series_code""")
    zero("full_evds_physical_bindings_resolved", """SELECT count(*) FROM catalog.metrics m
        LEFT JOIN catalog.metric_bindings b USING(metric_id)
        WHERE m.dataset_id='evds.full_catalog' AND m.observation_available AND (
            b.binding_json IS NULL OR coalesce(json_extract_string(b.binding_json,'$.binding_available'),'false')<>'true'
            OR json_extract_string(b.binding_json,'$.table') IS DISTINCT FROM 'evds.full_catalog_observations')""")
    failures = [item for item in checks if not item["passed"]]
    if failures:
        raise ValueError(f"EVDS bulk release quality failed: {json.dumps(failures)}")
    publication_id, summary_json, pinned_scope_complete, pinned_scope_series, target_start, target_end = connection.execute(
        "SELECT publication_id,validation_json,catalog_scope_complete,catalog_scope_series,target_start,target_end FROM evds.full_catalog_publication"
    ).fetchone()
    declared = json.loads(summary_json)
    names = ["metadata_series", "physical_series", "numeric_series", "observation_count", "source_null_count",
             "completed_request_series", "numeric_complete_series", "attempted_series", "attempted_series_request_count"]
    values = connection.execute("""SELECT count(*),count(*) FILTER (WHERE physical_present),
        count(*) FILTER (WHERE numeric_observation_count>0),coalesce(sum(observation_count),0),
        coalesce(sum(missing_observation_count),0),count(*) FILTER (WHERE request_coverage_complete),
        count(*) FILTER (WHERE numeric_coverage_complete),count(*) FILTER (WHERE attempted_job_count>0),
        coalesce(sum(attempted_job_count),0) FROM evds.full_catalog_coverage""").fetchone()
    counts = dict(zip(names, map(int, values)))
    for key, value in counts.items():
        if key in declared and int(declared[key]) != value:
            raise ValueError(f"EVDS bulk manifest count differs from copied coverage: {key}")
    scope_missing = int(connection.execute("""SELECT count(*) FROM catalog.metrics m
        ANTI JOIN evds.full_catalog_coverage c ON m.source_metric_code=c.series_code
        WHERE m.source_system='TCMB_EVDS'""").fetchone()[0])
    catalog_count = int(connection.execute("SELECT count(*) FROM catalog.metrics WHERE source_system='TCMB_EVDS'").fetchone()[0])
    scope_complete = bool(pinned_scope_complete) and scope_missing == 0 and catalog_count == pinned_scope_series
    request_complete = scope_complete and counts["metadata_series"] > 0 and counts["completed_request_series"] == counts["metadata_series"]
    numeric_complete = scope_complete and counts["metadata_series"] > 0 and counts["numeric_complete_series"] == counts["metadata_series"]
    for key, actual in [("full_request_scope_complete", request_complete),
                        ("full_numeric_coverage_complete", numeric_complete),
                        ("evds_full_observation_coverage_complete", numeric_complete and request_complete)]:
        if declared.get(key) and not actual:
            raise ValueError(f"EVDS bulk manifest overstates {key}")
    ready, numeric_ready, primary_physical, primary_numeric = connection.execute("""SELECT
        count(*) FILTER (WHERE b.status='ready'),
        count(*) FILTER (WHERE b.status='ready' AND m.observation_count>m.missing_observation_count),
        count(*) FILTER (WHERE m.observation_available),
        count(*) FILTER (WHERE m.observation_available AND m.observation_count>m.missing_observation_count)
        FROM catalog.metrics m JOIN catalog.metric_bindings b USING(metric_id) WHERE m.source_system='TCMB_EVDS'""").fetchone()
    return {**counts, "present": True, "publication_id": publication_id, "catalog_scope_complete": scope_complete,
            "attempted_job_count": int(declared.get("attempted_job_count", 0)),
            "catalog_scope_series": catalog_count, "primary_physical_series": int(primary_physical),
            "primary_numeric_series": int(primary_numeric), "semantic_ready_series": int(ready),
            "numeric_and_semantic_ready_series": int(numeric_ready),
            "full_semantic_scope_ready": bool(catalog_count) and ready == catalog_count,
            "full_request_scope_complete": request_complete and bool(declared.get("full_request_scope_complete")),
            "full_numeric_coverage_complete": numeric_complete and bool(declared.get("full_numeric_coverage_complete")),
            "evds_full_observation_coverage_complete": numeric_complete and request_complete and bool(declared.get("evds_full_observation_coverage_complete")),
            "target_start": target_start, "target_end": target_end, "checks": checks}


def validate_connection(connection: duckdb.DuckDBPyConnection) -> dict:
    checks = []
    required = {
        "catalog.metrics", "catalog.metric_bindings", "catalog.table_manifest",
        "bddk.monthly_measurements", "bddk.weekly_metric_versions",
        "bddk.weekly_measurements_resolved", "evds.legacy_observations",
        "risk_center.housing_metrics_latest",
        "analysis.housing_credit_monthly", "analysis.housing_credit_quarterly",
        "regional.housing_quarterly", "bddk.finturk_measurements",
    }
    tables = {f"{r[0]}.{r[1]}" for r in connection.execute(
        "SELECT table_schema,table_name FROM information_schema.tables").fetchall()}
    missing = sorted(required-tables)
    if missing:
        raise ValueError(f"Lakehouse release missing required contract/source tables: {missing}")

    def zero(name, sql):
        violations = int(connection.execute(sql).fetchone()[0])
        checks.append({"check":name,"violations":violations,"passed":violations==0})

    zero("metric_identity_unique", "SELECT count(*)-count(DISTINCT metric_id) FROM catalog.metrics")
    for table, key_columns in {
        "catalog.metrics":["metric_id"],
        "bddk.monthly_measurements":["month","group_code","metric_code"],
        "bddk.weekly_measurements":["observation_date","period_id","table_id","group_code","metric_code","currency_dimension"],
        "bddk.finturk_measurements":["quarter","table_no","measure_code","group_code","city"],
        "evds.legacy_observations":["series_code","period"],
        "tuik.province_housing_sales_monthly":["province_key","month","metric_code"],
    }.items():
        zero(f"nonempty:{table}",f"SELECT CASE WHEN count(*)=0 THEN 1 ELSE 0 END FROM {table}")
        zero(f"required_keys:{table}",f"SELECT count(*) FROM {table} WHERE "+" OR ".join(f"{key} IS NULL" for key in key_columns))
    # Optional table: present only when lakehouse was built after first-publication vintage data was added.
    if "tuik.province_housing_sales_first_published" in tables:
        zero(f"nonempty:tuik.province_housing_sales_first_published",
             "SELECT CASE WHEN count(*)=0 THEN 1 ELSE 0 END FROM tuik.province_housing_sales_first_published")
        zero(f"required_keys:tuik.province_housing_sales_first_published",
             "SELECT count(*) FROM tuik.province_housing_sales_first_published WHERE province_key IS NULL OR month IS NULL OR metric_code IS NULL")
    zero("binding_identity_unique", "SELECT count(*)-count(DISTINCT metric_id) FROM catalog.metric_bindings")
    for table, expected in {
        "bddk.monthly_measurements":1334850,
        "bddk.weekly_measurements":1025974,
        "bddk.finturk_measurements":936512,
    }.items():
        zero(f"source_snapshot_rows:{table}", f"SELECT abs(count(*)-{expected}) FROM {table}")
    zero("finturk_observation_key_unique", """SELECT count(*) FROM (
        SELECT quarter,table_no,measure_code,group_code,city FROM bddk.finturk_measurements
        GROUP BY ALL HAVING count(*)>1)""")
    zero("every_metric_has_contract_status", "SELECT count(*) FROM catalog.metrics m ANTI JOIN catalog.metric_bindings b USING(metric_id)")
    zero("monthly_observation_key_unique", """SELECT count(*) FROM (
        SELECT month,group_code,metric_code FROM bddk.monthly_measurements GROUP BY ALL HAVING count(*)>1)""")
    zero("monthly_count_units", "SELECT count(*) FROM bddk.monthly_measurements WHERE measure_kind='count_stock' AND unit<>'count'")
    zero("monthly_ratio_not_table_currency", "SELECT count(*) FROM bddk.monthly_measurements WHERE measure_kind='ratio' AND unit NOT IN ('percent','percent_or_ratio_source_defined')")
    zero("monthly_identity_values_preserved", """SELECT count(*) FROM bddk.monthly_measurements
        WHERE transformation='identity' AND source_value IS DISTINCT FROM analysis_value""")
    zero("ytd_requires_adjacent_calendar_month", """SELECT count(*) FROM bddk.monthly_measurements
        WHERE transformation='difference_within_calendar_year' AND NOT ends_with(month,'-01')
        AND analysis_value IS NOT NULL AND (prior_source_month IS NULL OR
            date_diff('month',CAST(prior_source_month||'-01' AS DATE),CAST(month||'-01' AS DATE))<>1)""")
    zero("weekly_parent_source_unique", """SELECT count(*) FROM (
        SELECT observation_date,period_id,table_id,group_code FROM bddk.weekly_source_tables
        GROUP BY ALL HAVING count(*)>1)""")
    zero("weekly_definition_intervals_nonoverlapping", """SELECT count(*) FROM bddk.weekly_metric_versions a
        JOIN bddk.weekly_metric_versions b ON a.table_id=b.table_id AND a.metric_code=b.metric_code
         AND a.definition_id<b.definition_id AND a.valid_from<=b.valid_to AND b.valid_from<=a.valid_to""")
    zero("weekly_each_observation_has_one_definition", """SELECT count(*) FROM (
        SELECT m.observation_date,m.period_id,m.table_id,m.group_code,m.metric_code,m.currency_dimension,count(v.definition_id) n
        FROM bddk.weekly_measurements m LEFT JOIN bddk.weekly_metric_versions v
          ON m.table_id=v.table_id AND m.metric_code=v.metric_code
         AND m.observation_date BETWEEN v.valid_from AND v.valid_to GROUP BY ALL HAVING n<>1)""")
    zero("weekly_resolved_join_preserves_rows", """SELECT abs(
        (SELECT count(*) FROM bddk.weekly_measurements_resolved)-(SELECT count(*) FROM bddk.weekly_measurements))""")
    zero("weekly_resolved_key_unique", """SELECT count(*) FROM (
        SELECT observation_date,period_id,table_id,group_code,metric_code,currency_dimension
        FROM bddk.weekly_measurements_resolved GROUP BY ALL HAVING count(*)>1)""")
    zero("legacy_evds_native_key_unique", """SELECT count(*) FROM (
        SELECT series_code,period FROM evds.legacy_observations GROUP BY ALL HAVING count(*)>1)""")
    zero("risk_center_vintage_identity_unique", """SELECT count(*) FROM (
        SELECT observation_month,metric_code,publication_month FROM risk_center.housing_metric_vintages GROUP BY ALL HAVING count(*)>1)""")
    tuik_monthly_cols = {r[0] for r in connection.execute("DESCRIBE tuik.province_housing_sales_monthly").fetchall()}
    if "vintage_policy" in tuik_monthly_cols and "revision_status" in tuik_monthly_cols:
        zero("tuik_current_vintage_is_explicit", """SELECT count(*) FROM tuik.province_housing_sales_monthly
            WHERE vintage_policy <> 'latest_official_bulk_snapshot'
               OR revision_status <> 'current_official_series_after_2026_methodology_revision'""")
    if "tuik.province_housing_sales_first_published" in tables:
        zero("tuik_first_published_vintage_is_explicit", """SELECT count(*) FROM tuik.province_housing_sales_first_published
            WHERE vintage_policy <> 'first_official_publication_for_each_reference_month'
               OR revision_status <> 'first_publication'
               OR source_press_id IS NULL OR release_at IS NULL OR source_cell IS NULL""")
    for table, key in [("analysis.housing_credit_monthly","month"),
                       ("analysis.housing_credit_quarterly","quarter"),
                       ("regional.housing_quarterly","province_key||':'||quarter")]:
        if table in tables:
            zero(f"unique:{table}",f"SELECT count(*)-count(DISTINCT {key}) FROM {table}")
    if "analysis.housing_credit_monthly" in tables:
        zero("housing_monthly_expected_rows", "SELECT abs(count(*)-66) FROM analysis.housing_credit_monthly")
        zero("housing_core_coverage", """SELECT count(*) FROM analysis.housing_credit_monthly
             WHERE bddk_housing_credit_stock_million_tl IS NULL OR TP_KTF12 IS NULL OR TP_TUKFIY2025_GENEL IS NULL""")
    if "analysis.housing_credit_quarterly" in tables:
        zero("housing_quarterly_expected_rows", "SELECT abs(count(*)-22) FROM analysis.housing_credit_quarterly")
    if "regional.housing_quarterly" in tables:
        zero("regional_expected_rows", "SELECT abs(count(*)-1782) FROM regional.housing_quarterly")
    for table, column in [("bddk.monthly_measurements","analysis_value"),
                          ("bddk.weekly_measurements","value"),
                          ("bddk.finturk_measurements","usable_value"),
                          ("evds.legacy_observations","value")]:
        if table in tables:
            zero(f"finite:{table}",f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND NOT isfinite({column})")
    zero("contract_readiness_known", "SELECT count(*) FROM catalog.metric_bindings WHERE status NOT IN ('ready','review_required','metadata_only','no_numeric')")
    for metric_id, payload in connection.execute("SELECT metric_id,binding_json FROM catalog.metric_bindings WHERE status='ready'").fetchall():
        binding = json.loads(payload)
        if not binding.get("binding_available") or binding.get("kind") == "unknown" or binding.get("blocked_reason"):
            raise ValueError(f"Ready metric has unverified binding: {metric_id}")
    failures = [c for c in checks if not c["passed"]]
    if failures:
        raise ValueError(f"Lakehouse release quality failed: {json.dumps(failures,ensure_ascii=False)}")
    full_evds = validate_full_evds(connection)
    return {"status":"passed","checks":checks,
            "evds_full_coverage_complete":full_evds["evds_full_observation_coverage_complete"],
            "evds_full_request_scope_complete":full_evds["full_request_scope_complete"],
            "evds_full_catalog":full_evds,
            "scope":"Local source snapshot, contracts and exact relationships. Query-specific readiness is checked at execution."}


def validate_database(path: Path) -> dict:
    with duckdb.connect(str(Path(path)),read_only=True) as connection:
        return validate_connection(connection)
