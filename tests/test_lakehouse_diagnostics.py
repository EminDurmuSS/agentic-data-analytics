import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "test_lakehouse_performance.py"


class LakehouseDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "fixture.duckdb"
        self.report = self.directory / "reports" / "diagnostic.json"
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("""
                CREATE SCHEMA analysis;
                CREATE SCHEMA bddk;
                CREATE SCHEMA catalog;
                CREATE SCHEMA evds;
                CREATE SCHEMA quality;
                CREATE SCHEMA tbb;
                CREATE TABLE analysis.housing_credit_monthly AS
                    SELECT '2026-06' AS month,
                           120.0 AS bddk_housing_credit_stock_million_tl,
                           150.0 AS TP_TUKFIY2025_GENEL;
                CREATE TABLE analysis.housing_credit_quarterly AS
                    SELECT '2026-06' AS quarter;
                CREATE TABLE bddk.monthly_measurements AS
                    SELECT 1 AS table_no, 'credit' AS metric_code;
                CREATE TABLE bddk.monthly_metric_dictionary AS
                    SELECT * FROM bddk.monthly_measurements;
                CREATE TABLE bddk.weekly_measurements AS
                    SELECT 1 AS table_id, 'credit' AS metric_code,
                           10001 AS group_code, 'TRY' AS currency_dimension,
                           120.0 AS value;
                CREATE TABLE bddk.weekly_metric_dictionary AS
                    SELECT table_id, metric_code FROM bddk.weekly_measurements;
                CREATE TABLE bddk.finturk_measurements AS
                    SELECT 'housing' AS measure_code;
                CREATE TABLE bddk.finturk_metric_dictionary AS
                    SELECT * FROM bddk.finturk_measurements;
                CREATE TABLE catalog.metrics AS
                    SELECT 'credit' AS source_metric_code,
                           true AS observation_available;
                CREATE TABLE catalog.table_manifest AS
                    SELECT 'fixture.csv' AS source_path;
                CREATE TABLE evds.series_catalog AS
                    SELECT 'credit' AS series_code;
                CREATE TABLE quality.reconciliation AS
                    SELECT 0.0 AS difference;
                CREATE TABLE tbb.source_gaps (reason VARCHAR);
            """)

    def change_fixture(self, sql):
        with duckdb.connect(str(self.database)) as connection:
            connection.execute(sql)

    def run_cli(self, *extra_arguments):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database", str(self.database),
                "--output", str(self.report),
                "--iterations", "1",
                *extra_arguments,
            ],
            cwd=self.directory,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_report(self):
        return json.loads(self.report.read_text(encoding="utf-8"))

    def test_external_database_runs_read_only_and_records_measurement_context(self):
        original_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        completed = self.run_cli()
        self.assertEqual(0, completed.returncode, completed.stderr)
        report = self.read_report()
        self.assertEqual("PASS", report["status"])
        self.assertEqual([], report["failed_checks"])
        self.assertEqual(original_hash, report["database"]["sha256"])
        self.assertEqual(
            original_hash, hashlib.sha256(self.database.read_bytes()).hexdigest()
        )
        self.assertEqual(self.database.resolve().as_posix(), report["database"]["path"])
        self.assertEqual(duckdb.__version__, report["runtime"]["duckdb_version"])
        self.assertFalse(Path(str(self.database) + ".wal").exists())
        measured = report["benchmarks"]["simple_metric_date_lookup"]
        self.assertEqual("MEASURED", measured["status"])
        self.assertEqual(1, measured["result_rows"])
        self.assertEqual(1, len(measured["samples_ms"]))
        self.assertIn("2026-06", measured["query"])
        self.assertIn("MEASURED simple_metric_date_lookup", completed.stdout)

    def test_noncritical_manifest_failure_fails_report_and_cli(self):
        self.change_fixture("UPDATE catalog.table_manifest SET source_path = NULL")
        completed = self.run_cli()
        self.assertEqual(1, completed.returncode, completed.stderr)
        report = self.read_report()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["schema_and_lineage"]["status"])
        self.assertIn("table manifest source paths", report["failed_checks"])
        self.assertEqual([], report["critical_failures"])
        self.assertIn("FAIL table manifest source paths", completed.stdout)

    def test_critical_orphan_failure_fails_report_and_cli(self):
        self.change_fixture("DELETE FROM bddk.monthly_metric_dictionary")
        completed = self.run_cli()
        self.assertEqual(1, completed.returncode, completed.stderr)
        report = self.read_report()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("monthly metric dictionary orphans", report["failed_checks"])
        self.assertIn("monthly metric dictionary orphans", report["critical_failures"])

    def test_missing_join_key_is_reported_instead_of_aborting(self):
        self.change_fixture("ALTER TABLE bddk.weekly_metric_dictionary DROP table_id")
        completed = self.run_cli()
        self.assertEqual(1, completed.returncode, completed.stderr)
        report = self.read_report()
        check = next(
            item for item in report["integrity"]["checks"]
            if item["name"] == "weekly metric dictionary orphans"
        )
        self.assertEqual("FAIL", check["status"])
        self.assertEqual(
            {"bddk.weekly_metric_dictionary": ["table_id"]}, check["missing_columns"]
        )
        self.assertEqual("MEASURED", report["benchmarks"]["weekly_large_aggregation"]["status"])

    def test_skipped_checks_are_warnings_not_a_complete_pass(self):
        self.change_fixture("DROP TABLE bddk.finturk_measurements")
        completed = self.run_cli()
        self.assertEqual(0, completed.returncode, completed.stderr)
        report = self.read_report()
        self.assertEqual("WARN", report["status"])
        self.assertEqual("WARN", report["integrity"]["status"])
        self.assertEqual([], report["failed_checks"])
        self.assertIn("FinTurk metric dictionary orphans", report["warnings_and_skips"])
        self.assertIn("Status: WARN", completed.stdout)

    def test_failed_benchmark_remains_visible_in_json_and_exit_code(self):
        self.change_fixture("ALTER TABLE analysis.housing_credit_monthly DROP TP_TUKFIY2025_GENEL")
        completed = self.run_cli()
        self.assertEqual(1, completed.returncode, completed.stderr)
        report = self.read_report()
        self.assertEqual("FAIL", report["status"])
        name = "monthly_join_and_derived_calculation"
        self.assertIn(name, report["failed_checks"])
        self.assertEqual("FAIL", report["benchmarks"][name]["status"])
        self.assertIn("TP_TUKFIY2025_GENEL", report["benchmarks"][name]["reason"])
        self.assertEqual("MEASURED", report["benchmarks"]["weekly_large_aggregation"]["status"])

    def test_report_cannot_overwrite_database(self):
        original = self.database.read_bytes()
        completed = self.run_cli("--output", str(self.database))
        self.assertEqual(2, completed.returncode)
        self.assertIn("must not overwrite", completed.stderr)
        self.assertEqual(original, self.database.read_bytes())
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
