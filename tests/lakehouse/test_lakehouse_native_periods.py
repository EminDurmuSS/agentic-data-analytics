"""Native half-year and observed-date twice-monthly queries retain source cells."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import duckdb
import pandas as pd

from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore


class NativePeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        database = cls.root / "native.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
        definitions = {
            "half": ("half_yearly", "review_required", ["2022-H1", "2022-H2", "2023-H1", "2023-H2", "2024-H1", "2024-H2"], [10, 20, 30, 40, 50, 60]),
            "half_gap": ("half_yearly", "review_required", ["2023-H1", "2024-H1"], [100, 200]),
            "half_ready": ("half_yearly", "ready", ["2023-H1", "2023-H2", "2024-H1"], [10, 20, 30]),
            "half_duplicate": ("half_yearly", "review_required", ["2024-H1", "2024-H1"], [1, 2]),
            "half_bad": ("half_yearly", "review_required", ["2024-H3"], [1]),
            "twice": ("twice_monthly", "review_required", ["2024-01-08", "2024-01-23", "2024-02-06", "2024-02-21"], [1, 2, None, 4]),
            "twice_other": ("twice_monthly", "review_required", ["2024-01-09", "2024-01-23", "2024-02-22"], [10, 20, 30]),
            "twice_ready": ("twice_monthly", "ready", ["2024-01-08", "2024-01-23"], [1, 2]),
            "twice_duplicate": ("twice_monthly", "review_required", ["2024-01-08", "2024-01-08"], [1, 2]),
            "annual": ("annual", "review_required", ["2023", "2024"], [100, 200]),
            "quarter": ("quarterly", "review_required", ["2024-Q1", "2024-Q2"], [100, 200]),
            "weekly": ("weekly_friday", "review_required", ["2024-01-05", "2024-01-12"], [100, 200]),
        }
        rows = []
        for metric_id, (frequency, status, periods, values) in definitions.items():
            binding = {"metric_id": metric_id, "title": metric_id, "dataset_id": "fixture.native", "source_system": "FIXTURE",
                "table": "native_observations", "time_column": "period", "value_column": "value", "filters": {"series_code": metric_id},
                "dimensions": {}, "native_frequency": frequency, "kind": "stock" if status == "ready" else "unknown",
                "unit": "TRY", "scale": 1, "currency": "TRY", "status": status, "binding_available": True,
                "source_base": "fixture", "hash_basis": "decompressed_response", "contract_version": "fixture-v1",
                "provenance_columns": ["source_response_file", "source_response_sha256", "source_row_index", "source_date_label", "is_missing"]}
            connection.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [metric_id, json.dumps(binding)])
            for index, (period, value) in enumerate(zip(periods, values)):
                rows.append({"series_code": metric_id, "period": period, "value": value,
                             "source_date_label": period.replace("-H", "-"), "source_row_index": index + 1,
                             "source_response_file": "raw/response.json.gz", "source_response_sha256": "a" * 64,
                             "is_missing": value is None})
        connection.register("fixture", pd.DataFrame(rows))
        connection.execute("CREATE TABLE native_observations AS SELECT * FROM fixture")
        connection.close()
        cls.store = LakehouseStore(cls.root / "store")
        cls.snapshot = cls.store.publish_snapshot(database)

    def setUp(self):
        self.workspace = self.store.create_workspace(self.snapshot["snapshot_id"])
        self.service = LakehouseService(self.store, self.workspace["workspace_id"])

    def plan(self, metric_id, frequency, start, end):
        return {"start": start, "end": end, "frequency": frequency,
                "columns": [{"name": "measure", "metric_id": metric_id}]}

    def run_plan(self, plan):
        result = self.service.execute(plan)
        frame, manifest = self.store.load_analysis(result["analysis_id"])
        return result, frame, manifest

    def test_half_years_are_anchored_and_explanation_keeps_native_labels(self):
        plan = self.plan("half", "half_yearly", "2022-H2", "2024-H1")
        result, frame, manifest = self.run_plan(plan)
        self.assertEqual(["2022-H2", "2023-H1", "2023-H2", "2024-H1"], frame.period.tolist())
        self.assertEqual([20, 30, 40, 50], frame.measure.tolist())
        self.assertEqual("calendar_half_years_january_june_and_july_december", manifest["lineage"]["calendar_policy"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2023-H2"})
        self.assertTrue(proof["source_references_complete"])
        self.assertEqual(40, proof["value"])
        self.assertEqual("2023-H2", proof["lineage"]["source_cells"][0]["native_period"])
        self.assertEqual("2023-2", proof["lineage"]["source_cells"][0]["source_date_label"])

    def test_half_year_missing_period_remains_missing_without_carrying_values(self):
        result, frame, _ = self.run_plan(self.plan("half_gap", "half_yearly", "2023-H1", "2024-H1"))
        self.assertEqual(["2023-H1", "2023-H2", "2024-H1"], frame.period.tolist())
        self.assertTrue(pd.isna(frame.iloc[1].measure))
        self.assertIn("missing_result", {item["code"] for item in result["warnings"]})
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2023-H2"})
        self.assertFalse(proof["source_references_complete"])
        self.assertEqual([], proof["lineage"]["source_cells"])

    def test_twice_monthly_uses_observed_dates_and_preserves_source_nulls(self):
        result, frame, manifest = self.run_plan(self.plan("twice", "twice_monthly", "2024-01-01", "2024-02-29"))
        self.assertEqual(["2024-01-08", "2024-01-23", "2024-02-06", "2024-02-21"], frame.period.tolist())
        self.assertTrue(pd.isna(frame.iloc[2].measure))
        self.assertEqual("union_of_observed_native_dates", manifest["lineage"]["calendar_policy"])
        self.assertIn("native_calendar_unverified", {item["code"] for item in result["warnings"]})
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2024-02-06"})
        self.assertTrue(proof["source_references_complete"])
        self.assertIsNone(proof["value"])
        self.assertTrue(proof["lineage"]["source_cells"][0]["is_missing"])

    def test_twice_monthly_union_aligns_exact_source_dates_only(self):
        plan = self.plan("twice", "twice_monthly", "2024-01-09", "2024-02-22")
        plan["columns"].append({"name": "other", "metric_id": "twice_other"})
        _, frame, _ = self.run_plan(plan)
        self.assertEqual(["2024-01-09", "2024-01-23", "2024-02-06", "2024-02-21", "2024-02-22"], frame.period.tolist())
        self.assertTrue(pd.isna(frame.iloc[0].measure))
        self.assertTrue(pd.isna(frame.iloc[-1].measure))
        self.assertEqual(20, frame.loc[frame.period.eq("2024-01-23"), "other"].iloc[0])

    def test_duplicates_and_malformed_half_years_cannot_publish(self):
        for metric, frequency, start, end, code in [
            ("half_duplicate", "half_yearly", "2024-H1", "2024-H2", "AMBIGUOUS_GRAIN"),
            ("twice_duplicate", "twice_monthly", "2024-01-01", "2024-01-31", "AMBIGUOUS_GRAIN"),
            ("half_bad", "half_yearly", "2024-H1", "2024-H2", "INVALID_PLAN"),
        ]:
            with self.subTest(metric=metric):
                response = self.service.validate_plan(self.plan(metric, frequency, start, end))
                self.assertEqual("blocked", response["status"])
                self.assertEqual(code, response["errors"][0]["code"])
        self.assertEqual("blocked", self.service.validate_plan(self.plan("half", "half_yearly", "2024-07", "2024-H2"))["status"])

    def test_transformations_and_frequency_conversion_reject_before_warmup(self):
        for metric, frequency, start, end in [("half_ready", "half_yearly", "2023-H2", "2024-H1"),
                                               ("twice_ready", "twice_monthly", "2024-01-01", "2024-01-31")]:
            plan = self.plan(metric, frequency, start, end)
            with self.service._context() as (_, bindings, _):
                self.assertEqual(0, self.service._validate(plan, bindings)["warmup"])
            plan["operations"] = [{"op": "growth", "column": "measure", "output": "growth", "periods": 1}]
            response = self.service.validate_plan(plan)
            self.assertEqual("NATIVE_PERIOD_OPERATIONS_UNSUPPORTED", response["errors"][0]["code"])
            converted = self.plan(metric, "annual", "2023", "2024")
            converted["columns"][0]["alignment"] = "last"
            self.assertEqual("NATIVE_FREQUENCY_CONVERSION_UNSUPPORTED", self.service.validate_plan(converted)["errors"][0]["code"])
        unknown = self.plan("twice", "twice_monthly", "2024-01-01", "2024-01-31")
        unknown["operations"] = [{"op": "difference", "column": "measure", "output": "change"}]
        self.assertEqual("SEMANTICS_REVIEW_REQUIRED", self.service.validate_plan(unknown)["errors"][0]["code"])

    def test_existing_annual_quarterly_and_weekly_native_labels_are_unchanged(self):
        for metric, frequency, start, end in [("annual", "annual", "2023", "2024"),
                                             ("quarter", "quarterly", "2024-Q1", "2024-Q2"),
                                             ("weekly", "weekly_friday", "2024-01-05", "2024-01-12")]:
            with self.subTest(frequency=frequency):
                _, frame, _ = self.run_plan(self.plan(metric, frequency, start, end))
                self.assertEqual([start, end], frame.period.tolist())
                self.assertEqual([100, 200], frame.measure.tolist())


if __name__ == "__main__":
    unittest.main()
