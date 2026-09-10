import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from data_pipeline.lakehouse.registry import get_bindings
from tools.lakehouse_quality import validate_connection


DATABASE = Path(__file__).resolve().parents[1] / "data_pipeline/lakehouse/analytics.duckdb"


class PublishedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = duckdb.connect(str(DATABASE), read_only=True)
        cls.bindings = get_bindings(cls.connection)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_catalog_discovery_does_not_claim_metadata_as_available_data(self):
        evds = [b for b in self.bindings.values() if b["source_system"] == "TCMB_EVDS"]
        self.assertEqual(52696, len(evds))
        physical = [b for b in evds if b.get("binding_available")]
        seed = [b for b in physical if b.get("dataset_id") != "evds.full_catalog"]
        self.assertEqual(599, len(seed))
        self.assertEqual(587, len([b for b in seed if b["status"] != "no_numeric"]))
        for binding in physical:
            if binding.get("dataset_id") == "evds.full_catalog":
                self.assertEqual("evds.full_catalog_observations", binding["table"])
                self.assertIn("source_response_sha256", binding["provenance_columns"])
        for binding in evds:
            if binding["status"] == "metadata_only":
                self.assertIsNone(binding["table"])
                self.assertFalse(binding["binding_available"])

    def test_customer_counts_never_inherit_a_currency_table_unit(self):
        counts = [b for b in self.bindings.values()
                  if b["source_system"] == "BDDK_MONTHLY" and b["kind"] == "count_stock"]
        self.assertTrue(counts)
        for binding in counts:
            self.assertEqual("count", binding["unit"])
            self.assertIsNone(binding["currency"])
            self.assertEqual(1, binding["scale"])
            self.assertIn("unit_evidence", binding)

    def test_legacy_observations_preserve_full_monthly_grain(self):
        count, unique_count, series = self.connection.execute("""
            SELECT count(*), count(DISTINCT (series_code,period)),
                   count(DISTINCT series_code) FROM evds.legacy_observations
        """).fetchone()
        self.assertEqual((1170,1170,15), (count,unique_count,series))

    def test_weekly_definition_history_does_not_duplicate_values(self):
        source, resolved = self.connection.execute("""SELECT
            (SELECT count(*) FROM bddk.weekly_measurements),
            (SELECT count(*) FROM bddk.weekly_measurements_resolved)""").fetchone()
        self.assertEqual(1025974, source)
        self.assertEqual(source, resolved)
        changes = self.connection.execute("""SELECT count(*) FROM (
            SELECT table_id,metric_code FROM bddk.weekly_metric_versions
            GROUP BY ALL HAVING count(*)>1)""").fetchone()[0]
        self.assertEqual(9, changes)

    def test_unmapped_derived_lineage_cannot_be_marked_ready(self):
        derived = [b for b in self.bindings.values() if b["source_system"] in {
            "TCMB_EVDS_DERIVED", "REGIONAL_HOUSING_ANALYSIS"}]
        self.assertEqual(34, len(derived))
        for binding in derived:
            self.assertNotEqual("ready", binding["status"])
            self.assertTrue(binding.get("blocked_reason"))

    def test_release_gates_distinguish_full_evds_coverage_from_request_completion(self):
        report = validate_connection(self.connection)
        self.assertEqual("passed", report["status"])
        bulk = report.get("evds_full_catalog", {})
        if not bulk.get("present"):
            self.assertFalse(report["evds_full_coverage_complete"])
        elif report["evds_full_coverage_complete"]:
            self.assertTrue(bulk["full_request_scope_complete"])


class ReleaseRejectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "damaged.duckdb"
        shutil.copyfile(DATABASE, cls.path)
        cls.connection = duckdb.connect(str(cls.path))

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        cls.directory.cleanup()

    def test_partial_outputs_bad_units_and_invalid_ytd_predecessors_are_rejected(self):
        mutations = [
            ("DELETE FROM analysis.housing_credit_monthly WHERE month='2026-06'",
             "housing_monthly_expected_rows"),
            ("UPDATE bddk.monthly_measurements SET unit='milyon TL' WHERE measure_kind='count_stock'",
             "monthly_count_units"),
            ("UPDATE bddk.monthly_measurements SET prior_source_month='2026-04' "
             "WHERE month='2026-06' AND transformation='difference_within_calendar_year'",
             "ytd_requires_adjacent_calendar_month"),
            ("DELETE FROM bddk.finturk_measurements WHERE quarter='2026-06'",
             "source_snapshot_rows:bddk.finturk_measurements"),
        ]
        for mutation, expected in mutations:
            with self.subTest(expected=expected):
                self.connection.execute("BEGIN")
                try:
                    self.connection.execute(mutation)
                    with self.assertRaisesRegex(ValueError, expected):
                        validate_connection(self.connection)
                finally:
                    self.connection.execute("ROLLBACK")

    def test_a_ready_label_cannot_bypass_unverified_semantics(self):
        metric_id, payload = self.connection.execute(
            "SELECT metric_id,binding_json FROM catalog.metric_bindings WHERE status='ready' LIMIT 1"
        ).fetchone()
        binding = json.loads(payload)
        binding["blocked_reason"] = "Source unit still requires review"
        self.connection.execute("BEGIN")
        try:
            self.connection.execute("UPDATE catalog.metric_bindings SET binding_json=? WHERE metric_id=?",
                                    [json.dumps(binding), metric_id])
            with self.assertRaisesRegex(ValueError, "unverified binding"):
                validate_connection(self.connection)
        finally:
            self.connection.execute("ROLLBACK")


if __name__ == "__main__":
    unittest.main()
