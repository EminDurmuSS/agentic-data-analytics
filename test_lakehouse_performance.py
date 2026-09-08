#!/usr/bin/env python3
"""Diagnose and benchmark the DuckDB lakehouse.

Run from the repository root with:
    python test_lakehouse_performance.py

The script is intentionally read-only. It writes only the JSON benchmark report.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data_pipeline" / "lakehouse" / "analytics.duckdb"
DEFAULT_REPORT = ROOT / "lakehouse_benchmark_results.json"


def qident(value: str) -> str:
    """Quote a DuckDB identifier after validating its simple dotted form."""
    parts = value.split(".")
    if any(not part or not part.replace("_", "").isalnum() for part in parts):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return ".".join('"' + part.replace('"', '""') + '"' for part in parts)


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return json_value(value.item())
    return str(value)


def rows_to_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [
        {column: json_value(value) for column, value in zip(columns, row)}
        for row in cursor.fetchall()
    ]


class Diagnostic:
    def __init__(self, connection: duckdb.DuckDBPyConnection, iterations: int) -> None:
        self.connection = connection
        self.iterations = max(1, iterations)
        self.schemas = self._load_schemas()
        self.tables = self._load_tables()
        self.columns = self._load_columns()
        self.results: dict[str, Any] = {
            "integrity": {"checks": [], "status": "PASS"},
            "schema_and_lineage": {"checks": [], "status": "PASS"},
            "benchmarks": {},
            "storage": {},
        }

    def _load_schemas(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT table_schema FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def _load_tables(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
        ).fetchall()
        return {f"{schema}.{table}" for schema, table in rows}

    def _load_columns(self) -> dict[str, set[str]]:
        rows = self.connection.execute(
            "SELECT table_schema, table_name, column_name "
            "FROM information_schema.columns"
        ).fetchall()
        columns: dict[str, set[str]] = {}
        for schema, table, column in rows:
            columns.setdefault(f"{schema}.{table}", set()).add(str(column))
        return columns

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def add_check(
        self,
        section: str,
        name: str,
        status: str,
        details: dict[str, Any],
        critical: bool = False,
    ) -> None:
        self.results[section]["checks"].append(
            {
                "name": name,
                "status": status,
                "critical": critical,
                **details,
            }
        )
        if status == "FAIL":
            self.results[section]["status"] = "FAIL"

    def scalar(self, query: str) -> Any:
        return self.connection.execute(query).fetchone()[0]

    def table_count(self, table: str) -> int:
        return int(self.scalar(f"SELECT count(*) FROM {qident(table)}"))

    def run_integrity(self) -> None:
        # Known measurement-to-dictionary relations in the current lakehouse.
        relation_specs = [
            (
                "bddk.monthly_measurements",
                "bddk.monthly_metric_dictionary",
                "m.table_no = d.table_no AND m.metric_code = d.metric_code",
                "monthly metric dictionary orphans",
            ),
            (
                "bddk.weekly_measurements",
                "bddk.weekly_metric_dictionary",
                "m.table_id = d.table_id AND m.metric_code = d.metric_code",
                "weekly metric dictionary orphans",
            ),
            (
                "bddk.finturk_measurements",
                "bddk.finturk_metric_dictionary",
                "m.measure_code = d.measure_code",
                "FinTurk metric dictionary orphans",
            ),
        ]
        for measurement, dictionary, predicate, name in relation_specs:
            if not self.has_table(measurement) or not self.has_table(dictionary):
                self.add_check(
                    "integrity", name, "SKIP", {"reason": "table not present"}
                )
                continue
            required_columns = {
                column
                for column in ("table_no", "metric_code", "measure_code")
                if column in predicate
            }
            available_columns = self.columns.get(measurement, set()) | self.columns.get(
                dictionary, set()
            )
            if not required_columns <= available_columns:
                self.add_check(
                    "integrity",
                    name,
                    "SKIP",
                    {
                        "reason": "relation columns not present",
                        "required_columns": sorted(required_columns),
                    },
                )
                continue
            orphan_count = int(
                self.scalar(
                    f"SELECT count(*) FROM {qident(measurement)} m "
                    f"ANTI JOIN {qident(dictionary)} d ON {predicate}"
                )
            )
            self.add_check(
                "integrity",
                name,
                "PASS" if orphan_count == 0 else "FAIL",
                {"orphan_rows": orphan_count},
                critical=True,
            )

        if self.has_table("evds.series_catalog"):
            catalog_codes = {
                str(row[0]).replace(".", "_").replace("-", "_")
                for row in self.connection.execute(
                    "SELECT series_code FROM evds.series_catalog"
                ).fetchall()
            }
            if self.has_table("catalog.metrics"):
                catalog_codes.update(
                    str(row[0]).replace(".", "_").replace("-", "_")
                    for row in self.connection.execute(
                        "SELECT source_metric_code FROM catalog.metrics "
                        "WHERE source_metric_code IS NOT NULL"
                    ).fetchall()
                )
            for table in sorted(self.tables):
                if not table.startswith("evds.") or not table.endswith("_panel"):
                    continue
                panel_columns = self.columns.get(table, set()) - {
                    "target_period",
                    "period",
                    "month",
                    "quarter",
                }
                unknown_columns = sorted(
                    column for column in panel_columns if column not in catalog_codes
                )
                self.add_check(
                    "integrity",
                    f"{table} catalog metric columns",
                    "PASS" if not unknown_columns else "FAIL",
                    {
                        "unknown_metric_columns": unknown_columns,
                        "checked_metric_columns": len(panel_columns),
                    },
                    critical=True,
                )

        for table in sorted(self.tables):
            if not table.startswith("evds.") or not table.endswith("_observations"):
                continue
            if not self.has_table("evds.series_catalog"):
                self.add_check(
                    "integrity", f"{table} series catalog orphans", "SKIP", {"reason": "catalog missing"}
                )
                continue
            orphan_count = int(
                self.scalar(
                    f"SELECT count(*) FROM {qident(table)} o "
                    f"ANTI JOIN evds.series_catalog c ON o.series_code = c.series_code"
                )
            )
            self.add_check(
                "integrity",
                f"{table} series catalog orphans",
                "PASS" if orphan_count == 0 else "FAIL",
                {"orphan_rows": orphan_count},
                critical=True,
            )

        for table, period_column in [
            ("analysis.housing_credit_monthly", "month"),
            ("analysis.housing_credit_quarterly", "quarter"),
        ]:
            if not self.has_table(table):
                self.add_check("integrity", f"{table} duplicate periods", "SKIP", {"reason": "table not present"})
                continue
            duplicate_rows = int(
                self.scalar(
                    f"SELECT count(*) - count(DISTINCT {qident(period_column)}) "
                    f"FROM {qident(table)}"
                )
            )
            self.add_check(
                "integrity",
                f"{table} duplicate periods",
                "PASS" if duplicate_rows == 0 else "FAIL",
                {"duplicate_rows": duplicate_rows},
                critical=True,
            )

        self._report_quality_gaps()

    def _report_quality_gaps(self) -> None:
        tables = sorted(table for table in self.tables if table.startswith("quality."))
        if not tables:
            self.add_check("integrity", "quality gap report", "SKIP", {"reason": "quality schema is empty"})
            return
        reports = []
        for table in tables:
            total = self.table_count(table)
            columns = sorted(self.columns.get(table, set()))
            null_rates = {}
            for column in columns:
                null_count = int(self.scalar(f"SELECT count(*) FROM {qident(table)} WHERE {qident(column)} IS NULL"))
                null_rates[column] = {
                    "null_count": null_count,
                    "null_rate": (null_count / total if total else 0.0),
                }
            deviation_columns = [
                column
                for column in columns
                if any(token in column.casefold() for token in ("pct", "difference", "diff", "residual"))
            ]
            reports.append(
                {
                    "table": table,
                    "row_count": total,
                    "deviation_columns": deviation_columns,
                    "null_rates": null_rates,
                }
            )
        self.results["integrity"]["quality_gap_reports"] = reports

    def run_schema_and_lineage(self) -> None:
        expected_schemas = {"catalog", "bddk", "evds", "tbb", "analysis"}
        missing_schemas = sorted(expected_schemas - set(self.schemas))
        self.add_check(
            "schema_and_lineage",
            "required schemas present",
            "PASS" if not missing_schemas else "FAIL",
            {"missing_schemas": missing_schemas, "schemas": self.schemas},
            critical=True,
        )

        checks = []
        if self.has_table("catalog.metrics"):
            total, available = self.connection.execute(
                "SELECT count(*), count(*) FILTER (WHERE observation_available) "
                "FROM catalog.metrics"
            ).fetchone()
            checks.append(
                {
                    "name": "catalog metric observation coverage",
                    "status": "PASS",
                    "total_metrics": int(total),
                    "observation_available_metrics": int(available),
                    "coverage_ratio": (available / total if total else 0.0),
                }
            )
        else:
            checks.append({"name": "catalog metric observation coverage", "status": "SKIP", "reason": "catalog.metrics missing"})

        if self.has_table("catalog.table_manifest"):
            manifest_missing = int(
                self.scalar(
                    "SELECT count(*) FROM catalog.table_manifest m "
                    "WHERE m.source_path IS NULL OR m.source_path = ''"
                )
            )
            checks.append(
                {
                    "name": "table manifest source paths",
                    "status": "PASS" if manifest_missing == 0 else "FAIL",
                    "missing_source_paths": manifest_missing,
                }
            )
        else:
            checks.append({"name": "table manifest source paths", "status": "SKIP", "reason": "manifest missing"})

        self.results["schema_and_lineage"]["checks"].extend(checks)
        if any(check["status"] == "FAIL" for check in checks):
            self.results["schema_and_lineage"]["status"] = "FAIL"

    def benchmark(self, name: str, query: str) -> None:
        # Warm-up prevents first-read/catalog initialization from dominating results.
        self.connection.execute(query).fetchall()
        samples = []
        result_rows = 0
        for _ in range(self.iterations):
            started = time.perf_counter_ns()
            result = self.connection.execute(query).fetchall()
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            samples.append(elapsed_ms)
            result_rows = len(result)
        ordered = sorted(samples)
        p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
        self.results["benchmarks"][name] = {
            "iterations": self.iterations,
            "result_rows": result_rows,
            "latency_ms": {
                "min": min(samples),
                "median": statistics.median(samples),
                "mean": statistics.mean(samples),
                "p95": ordered[p95_index],
                "max": max(samples),
            },
        }

    def run_benchmarks(self) -> None:
        queries = {
            "simple_metric_date_lookup": (
                "SELECT month, bddk_housing_credit_stock_million_tl "
                "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
            ),
            "monthly_group_by": (
                "SELECT date_trunc('year', CAST(month || '-01' AS DATE)) AS year, "
                "avg(bddk_housing_credit_stock_million_tl) AS average_stock "
                "FROM analysis.housing_credit_monthly GROUP BY 1 ORDER BY 1"
            ),
            "monthly_join_and_derived_calculation": (
                "SELECT a.month, "
                "a.bddk_housing_credit_stock_million_tl * 100.0 / NULLIF(a.TP_TUKFIY2025_GENEL, 0) AS real_proxy, "
                "c.TP_KKM_K1 "
                "FROM analysis.housing_credit_monthly a "
                "LEFT JOIN evds.household_finance_monthly_panel c "
                "ON c.target_period = a.month "
                "WHERE a.month BETWEEN '2021-01' AND '2026-06' ORDER BY a.month"
            ),
            "weekly_large_aggregation": (
                "SELECT group_code, table_id, currency_dimension, "
                "count(*) AS observations, avg(value) AS average_value "
                "FROM bddk.weekly_measurements GROUP BY 1, 2, 3"
            ),
        }
        for name, query in queries.items():
            if name == "monthly_join_and_derived_calculation" and not self.has_table("evds.household_finance_monthly_panel"):
                query = (
                    "SELECT month, "
                    "bddk_housing_credit_stock_million_tl * 100.0 / NULLIF(TP_TUKFIY2025_GENEL, 0) AS real_proxy "
                    "FROM analysis.housing_credit_monthly "
                    "WHERE month BETWEEN '2021-01' AND '2026-06' ORDER BY month"
                )
            if name == "weekly_large_aggregation" and not self.has_table("bddk.weekly_measurements"):
                self.results["benchmarks"][name] = {"status": "SKIP", "reason": "table not present"}
                continue
            self.benchmark(name, query)

    def run_storage(self, database_path: Path) -> None:
        file_size = database_path.stat().st_size
        table_rows = []
        for table in sorted(self.tables):
            table_rows.append({"table": table, "row_count": self.table_count(table)})
        database_size_rows = rows_to_dicts(self.connection.execute("PRAGMA database_size"))
        self.results["storage"] = {
            "database_path": str(database_path.relative_to(ROOT)),
            "database_file_bytes": file_size,
            "database_file_mib": file_size / (1024 * 1024),
            "database_size": database_size_rows,
            "table_rows": table_rows,
            "total_rows": sum(item["row_count"] for item in table_rows),
            "bytes_per_logical_row": (
                file_size / sum(item["row_count"] for item in table_rows)
                if sum(item["row_count"] for item in table_rows)
                else None
            ),
            "storage_note": "DuckDB compression details are reported by PRAGMA database_size; table row counts are logical, not physical byte allocation.",
        }

    def run(self, database_path: Path) -> dict[str, Any]:
        self.run_integrity()
        self.run_schema_and_lineage()
        self.run_benchmarks()
        self.run_storage(database_path)
        critical_failures = []
        for section in ("integrity", "schema_and_lineage"):
            critical_failures.extend(
                check["name"]
                for check in self.results[section]["checks"]
                if check["status"] == "FAIL" and check.get("critical")
            )
        self.results["status"] = "FAIL" if critical_failures else "PASS"
        self.results["critical_failures"] = critical_failures
        self.results["database"] = {
            "path": str(database_path.relative_to(ROOT)),
            "schemas": self.schemas,
            "table_count": len(self.tables),
        }
        return self.results


def print_report(report: dict[str, Any], report_path: Path) -> None:
    print("\nLakehouse Diagnostic & Benchmark Report")
    print("=" * 44)
    print(f"Status: {report['status']}")
    print(f"Database: {report['database']['path']}")
    print(f"Schemas: {len(report['database']['schemas'])} | Tables: {report['database']['table_count']}")
    print("\n[1] Data Integrity & Accuracy")
    for check in report["integrity"]["checks"]:
        print(f"  {check['status']:<4} {check['name']}")
    print("\n[2] Schema & Data Lineage")
    for check in report["schema_and_lineage"]["checks"]:
        print(f"  {check['status']:<4} {check['name']}")
        if "coverage_ratio" in check:
            print(f"       coverage: {check['coverage_ratio']:.2%}")
    print("\n[3] Query Latency (ms)")
    for name, result in report["benchmarks"].items():
        if result.get("status") == "SKIP":
            print(f"  SKIP {name}: {result['reason']}")
            continue
        latency = result["latency_ms"]
        print(f"  PASS {name}: median={latency['median']:.3f}, p95={latency['p95']:.3f}, rows={result['result_rows']}")
    storage = report["storage"]
    print("\n[4] Storage")
    print(f"  file size: {storage['database_file_mib']:.2f} MiB")
    print(f"  logical rows: {storage['total_rows']:,}")
    print(f"\nJSON report: {report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    database_path = args.database.expanduser().resolve()
    report_path = args.output.expanduser().resolve()
    if not database_path.is_file():
        print(f"Database not found: {database_path}", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        diagnostic = Diagnostic(connection, args.iterations)
        report = diagnostic.run(database_path)
    finally:
        connection.close()
    report["generated_at_utc"] = started.isoformat()
    report["configuration"] = {"iterations": max(1, args.iterations)}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_report(report, report_path)
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
