"""Source-specific relationships with explicit temporal and key constraints."""
from pathlib import Path

import duckdb


def install_source_views(connection: duckdb.DuckDBPyConnection, root: Path) -> list[dict]:
    manifest = []
    weekly = root / "data_pipeline/bddk/processed/weekly_all_groups/measurements_long.parquet"
    if weekly.exists():
        # Grouping by label preserves the source definition. Overlapping validity
        # intervals fail the quality gate; DISTINCT is not a fanout repair.
        connection.execute("""CREATE TABLE bddk.weekly_metric_versions AS
            SELECT table_id, metric_code, metric_label, source_unit,
                   min(observation_date) AS valid_from, max(observation_date) AS valid_to,
                   md5(metric_code || ':' || metric_label || ':' || source_unit) AS definition_id
            FROM read_parquet(?) GROUP BY table_id,metric_code,metric_label,source_unit""", [str(weekly)])
        connection.execute("""CREATE VIEW bddk.weekly_measurements_resolved AS
            SELECT m.*, v.metric_label, v.source_unit, v.definition_id,
                   s.source_file, s.source_sha256, s.group_name,
                   s.source_request_info_file, s.source_request_info_sha256
            FROM bddk.weekly_measurements m
            JOIN bddk.weekly_metric_versions v
              ON m.table_id=v.table_id AND m.metric_code=v.metric_code
             AND m.observation_date BETWEEN v.valid_from AND v.valid_to
            JOIN bddk.weekly_source_tables s
              ON m.observation_date=s.observation_date AND m.period_id=s.period_id
             AND m.table_id=s.table_id AND m.group_code=s.group_code""")
        for name in ("weekly_metric_versions", "weekly_measurements_resolved"):
            manifest.append({"schema_name":"bddk","table_name":name,
                "row_count":connection.execute(f"SELECT count(*) FROM bddk.{name}").fetchone()[0],
                "source_path":"derived:dated_weekly_labels_and_exact_source_keys"})
    legacy = root / "data_pipeline/processed/observations_native.parquet"
    if legacy.exists():
        unsupported = connection.execute("""SELECT count(*) FROM read_parquet(?)
            WHERE series_code IN (SELECT source_metric_code FROM catalog.metrics WHERE dataset_id='evds.legacy_native')
              AND native_frequency IS DISTINCT FROM 'monthly'""", [str(legacy)]).fetchone()[0]
        if unsupported:
            raise ValueError("Legacy EVDS adapter currently supports monthly observations only; preserve new native frequencies explicitly.")
        connection.execute("""CREATE TABLE evds.legacy_observations AS
            SELECT series_code, strftime(observation_date,'%Y-%m') AS period,
                   CAST(observation_date AS DATE)::VARCHAR AS period_start,
                   last_day(observation_date)::VARCHAR AS period_end,
                   value, native_unit AS unit, native_frequency, source_sha256, vintage
            FROM read_parquet(?)
            WHERE series_code IN (SELECT source_metric_code FROM catalog.metrics
                                  WHERE dataset_id='evds.legacy_native')""",[str(legacy)])
        manifest.append({"schema_name":"evds","table_name":"legacy_observations",
            "row_count":connection.execute("SELECT count(*) FROM evds.legacy_observations").fetchone()[0],
            "source_path":legacy.relative_to(root).as_posix()})
    # Resolve one known source vintage per observation without coupling silver
    # observations to the housing gold table.
    connection.execute("""CREATE VIEW risk_center.housing_metrics_latest AS
        SELECT * FROM risk_center.housing_metric_vintages
        QUALIFY row_number() OVER (
            PARTITION BY observation_month,metric_code ORDER BY publication_month DESC)=1""")
    manifest.append({"schema_name":"risk_center","table_name":"housing_metrics_latest",
        "row_count":connection.execute("SELECT count(*) FROM risk_center.housing_metrics_latest").fetchone()[0],
        "source_path":"derived:latest_risk_center_source_vintage"})
    return manifest
