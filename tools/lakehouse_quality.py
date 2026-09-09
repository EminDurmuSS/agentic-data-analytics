"""Enforced release gates for the checked-in KKB source snapshot.

These gates validate this source package, not the completeness of all EVDS data
or clinical/economic suitability of arbitrary newly uploaded datasets.
"""
from pathlib import Path
import json

import duckdb


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
    }.items():
        zero(f"nonempty:{table}",f"SELECT CASE WHEN count(*)=0 THEN 1 ELSE 0 END FROM {table}")
        zero(f"required_keys:{table}",f"SELECT count(*) FROM {table} WHERE "+" OR ".join(f"{key} IS NULL" for key in key_columns))
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
    return {"status":"passed","checks":checks,"evds_full_coverage_complete":False,
            "scope":"Local source snapshot, contracts and exact relationships. Query-specific readiness is checked at execution."}


def validate_database(path: Path) -> dict:
    with duckdb.connect(str(Path(path)),read_only=True) as connection:
        return validate_connection(connection)
