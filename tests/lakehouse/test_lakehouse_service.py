"""Behavioral tests of the public agent tools on persisted snapshot fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
import pandas as pd

from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class LakehouseServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.db = cls.root / "fixture.duckdb"
        connection = duckdb.connect(str(cls.db))
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        rows = []
        definitions = {
            "credit": ("stock", "TRY", 1e6, "TRY", "monthly", "ready"),
            "cpi": ("index", "index", 1, None, "monthly", "ready"),
            "count": ("count_stock", "count", 1, None, "monthly", "ready"),
            "ratio": ("ratio", "percent", 1, None, "monthly", "ready"),
            "gap": ("flow", "TRY", 1e6, "TRY", "monthly", "ready"),
            "quarter": ("stock", "TRY", 1e6, "TRY", "quarterly", "ready"),
            "weekly": ("rate", "percent", 1, None, "weekly_friday", "ready"),
            "duplicate": ("stock", "TRY", 1e6, "TRY", "monthly", "ready"),
            "other_population": ("stock", "TRY", 1e6, "TRY", "monthly", "ready"),
            "untraced": ("stock", "TRY", 1e6, "TRY", "monthly", "ready"),
            "unknown": ("unknown", "source", 1, None, "monthly", "review_required"),
            "absent": ("unknown", "source", 1, None, "monthly", "metadata_only"),
            "null": ("unknown", "source", 1, None, "monthly", "no_numeric"),
        }
        for metric, (kind, unit, scale, currency, frequency, status) in definitions.items():
            binding = {"metric_id": metric, "title": "KOBİ " + metric, "source_system": "FIXTURE", "table": "observations", "time_column": "month", "value_column": "value", "filters": {"metric": metric}, "dimensions": {"group_code": "group_code"}, "native_frequency": frequency, "kind": kind, "unit": unit, "scale": scale, "currency": currency, "aggregation": "last", "source_base": "fixtures", "provenance_columns": ["source_file", "source_sha256", "source_row_index"], "status": status, "notes": [], "contract_version": "test-1"}
            if metric == "cpi":
                binding.update(index_role="price_deflator", deflator_currency="TRY", price_scope="Fixture consumer basket")
            if metric == "credit":
                binding.update(
                    official_series_url="https://example.test/credit-series",
                    institution_scope="fixture_reporting_population",
                )
            if metric == "other_population":
                binding["institution_scope"] = "A different set of reporting institutions"
                binding["geography_scope"] = "A different geographic population"
            if metric == "untraced":
                binding["provenance_columns"] = []
            connection.execute("INSERT INTO catalog.metric_bindings VALUES (?, ?)", [metric, json.dumps(binding)])
            for index, period in enumerate(pd.period_range("2020-01", "2022-12", freq="M")):
                if metric in {"absent", "null"}:
                    continue
                if metric == "gap" and str(period) == "2021-02":
                    continue
                if metric == "quarter" and period.month % 3:
                    continue
                value = 100 + 10 * index if metric == "credit" else 100 + index if metric == "cpi" else 2 + index / 10 if metric == "ratio" else 10 + index
                dates = [str(period)] if metric != "weekly" else [str(period) + "-01", str(period) + "-08"]
                for offset, when in enumerate(dates):
                    rows.append({"metric": metric, "month": when, "value": float(value + offset * 2), "group_code": 10001, "source_file": "source.json", "source_sha256": "a" * 64, "source_row_index": index + 1})
                if metric == "duplicate" and str(period) == "2021-01":
                    rows.append(copy.deepcopy(rows[-1]))
        frame = pd.DataFrame(rows)
        connection.register("fixture", frame)
        connection.execute("CREATE TABLE observations AS SELECT * FROM fixture")
        connection.close()
        cls.store = LakehouseStore(cls.root / "store")
        cls.snapshot = cls.store.publish_snapshot(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.workspace = self.store.create_workspace(self.snapshot["snapshot_id"])
        self.service = LakehouseService(self.store, self.workspace["workspace_id"])

    def plan(self, metric="credit", **extra):
        return {"start": "2021-01", "end": "2022-12", "frequency": "monthly", "columns": [{"name": metric, "metric_id": metric, "dimensions": {"group_code": 10001}}], **extra}

    def add(self, metric):
        return {"name": metric, "metric_id": metric, "dimensions": {"group_code": 10001}}

    def test_discovery_is_bounded_and_turkish_searchable(self):
        result = self.service.discover({"query": "kobi", "limit": 2})
        self.assertEqual(2, len(result["metrics"]))
        self.assertEqual(13, result["total"])
        self.assertEqual("monthly", self.service.describe({"metric_id": "credit"})["metric"]["native_frequency"])

    def test_calendar_growth_loads_warmup_before_result_start(self):
        result = self.service.execute(self.plan(operations=[{"op": "growth", "column": "credit", "output": "yoy", "periods": 12}]))
        frame, manifest = self.store.load_analysis(result["analysis_id"])
        self.assertEqual(24, len(frame))
        self.assertAlmostEqual(120, frame.loc[0, "yoy"])
        self.assertEqual("2021-01", frame.loc[0, "period"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "yoy", "period": "2021-01"})
        self.assertTrue(proof["lineage_complete"])
        previous = proof["lineage"]["inputs"][1]
        self.assertEqual("2020-01", previous["period"])
        self.assertEqual(100, previous["source_cells"][0]["source_value"])
        self.assertEqual("a" * 64, previous["source_cells"][0]["source_sha256"])

    def test_explanation_exposes_complete_normalized_source_row_contracts(self):
        result = self.service.execute(self.plan("credit"))
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "credit", "period": "2021-01"})
        contracts = proof["source_row_contracts"]
        self.assertTrue(proof["source_row_contracts_complete"])
        self.assertEqual(1, len(contracts))
        self.assertTrue(contracts[0]["complete"])
        self.assertEqual("2021-01", contracts[0]["period"])
        self.assertEqual("credit", contracts[0]["column"])
        self.assertEqual("a" * 64, contracts[0]["hash"])
        self.assertEqual("https://example.test/credit-series", contracts[0]["url"])

    def test_missing_calendar_month_is_not_previous_available_row(self):
        result = self.service.execute(self.plan("gap", operations=[{"op": "growth", "column": "gap", "output": "mom"}]))
        frame, _ = self.store.load_analysis(result["analysis_id"])
        self.assertTrue(pd.isna(frame.loc[frame.period == "2021-03", "mom"].iloc[0]))
        self.assertTrue(any(note["code"] == "missing_result" for note in result["warnings"]))

    def test_selected_window_difference_keeps_first_visible_period_empty(self):
        plan = self.plan(
            operations=[{
                "op": "difference", "column": "credit", "output": "change",
                "periods": 1, "prior_scope": "selected_window",
            }]
        )
        result = self.service.execute(plan)
        frame, _ = self.store.load_analysis(result["analysis_id"])
        self.assertTrue(pd.isna(frame.iloc[0].change))
        self.assertEqual(10, frame.iloc[1].change)

    def test_default_difference_can_use_verified_history_before_window(self):
        result = self.service.execute(self.plan(
            operations=[{"op": "difference", "column": "credit", "output": "change", "periods": 1}]
        ))
        frame, _ = self.store.load_analysis(result["analysis_id"])
        self.assertEqual(10, frame.iloc[0].change)

    def test_revisions_preserve_other_columns_and_parent_bytes(self):
        first = self.service.execute(self.plan(operations=[{"op": "growth", "column": "credit", "output": "yoy", "periods": 12}]))
        original, _ = self.store.load_analysis(first["analysis_id"])
        revised = self.service.revise_analysis({"analysis_id": first["analysis_id"], "add_columns": [self.add("cpi")], "operations": [{"op": "deflate", "column": "credit", "index": "cpi", "base_period": "2021-01", "output": "credit"}]})
        changed, _ = self.store.load_analysis(revised["analysis_id"])
        unchanged, _ = self.store.load_analysis(first["analysis_id"])
        pd.testing.assert_frame_equal(original, unchanged)
        pd.testing.assert_series_equal(original.yoy, changed.yoy)
        pd.testing.assert_series_equal(original.period, changed.period)
        self.assertNotEqual(original.credit.iloc[-1], changed.credit.iloc[-1])
        proof = self.service.explain_value({"analysis_id": revised["analysis_id"], "column": "credit", "period": "2022-12"})
        self.assertEqual("deflate", proof["lineage"]["operation"]["op"])

    def test_ratio_difference_is_percentage_points(self):
        result = self.service.execute(self.plan("ratio", operations=[{"op": "difference", "column": "ratio", "output": "change"}]))
        self.assertEqual("percentage_points", result["schema"]["change"]["unit"])
        self.assertAlmostEqual(0.1, result["preview"][0]["change"])
        self.assertEqual("blocked", self.service.validate_plan(self.plan("ratio", operations=[{"op": "growth", "column": "ratio", "output": "growth"}]))["status"])

    def test_counts_cannot_be_deflated(self):
        plan = self.plan("count", operations=[{"op": "deflate", "column": "count", "index": "cpi", "base_period": "2021-01", "output": "real"}])
        plan["columns"].append(self.add("cpi"))
        with self.assertRaisesRegex(PlanError, "monetary"):
            self.service.execute(plan)

    def test_weekly_rates_require_explicit_observed_mean(self):
        plan = self.plan("weekly")
        self.assertEqual("blocked", self.service.validate_plan(plan)["status"])
        plan["columns"][0]["alignment"] = "mean"
        result = self.service.execute(plan)
        self.assertAlmostEqual(23, result["preview"][0]["weekly"])
        self.assertTrue(any(note["code"] == "observed_sample_mean" for note in result["warnings"]))

    def test_upsampling_and_stock_summing_are_blocked(self):
        with self.assertRaisesRegex(PlanError, "Upsampling"):
            self.service.execute(self.plan("quarter"))
        plan = self.plan(start="2021-Q1", end="2022-Q4", frequency="quarterly")
        plan["columns"][0]["alignment"] = "sum"
        with self.assertRaisesRegex(PlanError, "flows"):
            self.service.execute(plan)

    def test_quarterly_native_and_complete_flow_aggregation(self):
        result = self.service.execute(self.plan("quarter", start="2021-Q1", end="2022-Q4", frequency="quarterly"))
        self.assertEqual(8, result["row_count"])
        flow = self.plan("gap", start="2021-Q1", end="2022-Q4", frequency="quarterly")
        flow["columns"][0]["alignment"] = "sum"
        result = self.service.execute(flow)
        self.assertIsNone(result["preview"][0]["gap"])
        self.assertIsNotNone(result["preview"][1]["gap"])

    def test_ambiguous_grain_is_rejected_before_save(self):
        with self.assertRaisesRegex(PlanError, "Ambiguous grain"):
            self.service.execute(self.plan("duplicate"))
        self.assertEqual(0, self.store.workspace(self.workspace["workspace_id"])["version"])

    def test_unreviewed_raw_selection_and_missing_statuses(self):
        self.assertEqual("ok", self.service.execute(self.plan("unknown"))["status"])
        for metric in ("absent", "null"):
            self.assertEqual("blocked", self.service.validate_plan(self.plan(metric))["status"])
        plan = self.plan("unknown", operations=[{"op": "growth", "column": "unknown", "output": "growth"}])
        self.assertEqual("blocked", self.service.validate_plan(plan)["status"])

    def test_extra_sql_and_missing_dimension_are_rejected(self):
        with self.assertRaisesRegex(PlanError, "unknown fields"):
            self.service.execute(self.plan(sql="DROP TABLE observations"))
        plan = self.plan()
        plan["columns"][0]["dimensions"] = {}
        with self.assertRaisesRegex(PlanError, "dimensions"):
            self.service.execute(plan)
        plan["columns"][0]["dimensions"] = {"group_code": "10001 OR 1=1"}
        # A bound value cannot become a predicate; the typed conversion may fail.
        with self.assertRaises((PlanError, duckdb.ConversionException)):
            self.service.execute(plan)

    def test_scale_conversion_can_replace_column_twice(self):
        plan = self.plan(operations=[{"op": "scale", "column": "credit", "output": "credit", "target_scale": 1}, {"op": "scale", "column": "credit", "output": "credit", "target_scale": 1e3}])
        result = self.service.execute(plan)
        self.assertAlmostEqual(220000, result["preview"][0]["credit"])
        self.assertEqual(1e3, result["schema"]["credit"]["scale"])

    def test_missing_index_base_is_blocked(self):
        plan = self.plan(operations=[{"op": "deflate", "column": "credit", "index": "cpi", "base_period": "2019-01", "output": "real"}])
        plan["columns"].append(self.add("cpi"))
        with self.assertRaisesRegex(PlanError, "base observation"):
            self.service.execute(plan)

    def test_health_overlay_uses_same_tools_and_count_guard(self):
        source = self.root / "visits.csv"
        source.write_text("month,city,visits\n2021-01,Ankara,100\n2021-02,Ankara,120\n", encoding="utf-8")
        contract = {"name": "clinic_visits", "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "city": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}, "visits": {"dtype": "integer", "unit": "persons", "kind": "count_flow", "nullable": False}}, "key": ["month", "city"], "grain": ["month", "city"], "date_column": "month", "frequency": "monthly"}
        workspace = self.store.ingest_csv(self.workspace["workspace_id"], source, contract, expected_version=0)
        metric_id = f"overlay:{workspace['datasets'][0]}:visits"
        plan = {"start": "2021-01", "end": "2021-02", "frequency": "monthly", "columns": [{"name": "visits", "metric_id": metric_id, "dimensions": {"city": "Ankara"}}], "operations": [{"op": "growth", "column": "visits", "output": "growth"}]}
        result = self.service.execute(plan)
        self.assertAlmostEqual(20, result["preview"][1]["growth"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "visits", "period": "2021-02"})
        self.assertEqual(64, len(proof["lineage"]["source_sha256"]))

    def test_new_overlay_can_extend_an_existing_analysis(self):
        first = self.service.execute(self.plan())
        original, _ = self.store.load_analysis(first["analysis_id"])
        source = self.root / "new_measure.csv"
        source.write_text("month,visits\n2021-01,100\n2021-02,120\n", encoding="utf-8")
        contract = {"name": "new_measure", "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "visits": {"dtype": "integer", "unit": "persons", "kind": "count_flow", "nullable": False}}, "key": ["month"], "grain": ["month"], "date_column": "month", "frequency": "monthly"}
        workspace = self.store.ingest_csv(self.workspace["workspace_id"], source, contract, expected_version=1)
        revised = self.service.revise_analysis({"analysis_id": first["analysis_id"], "add_columns": [{"name": "visits", "metric_id": f"overlay:{workspace['datasets'][0]}:visits"}]})
        frame, _ = self.store.load_analysis(revised["analysis_id"])
        pd.testing.assert_frame_equal(original, frame[original.columns])
        self.assertEqual(120, frame.visits.iloc[1])
        self.assertTrue(pd.isna(frame.visits.iloc[2]))

    def test_computed_value_without_raw_provenance_is_not_complete_lineage(self):
        result = self.service.execute(self.plan("untraced"))
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "untraced", "period": "2021-01"})
        self.assertIsNotNone(proof["value"])
        self.assertFalse(proof["lineage_complete"])
        self.assertIn("hash and locator", proof["lineage_issues"][0])

    def test_blocked_plans_have_stable_machine_readable_codes(self):
        examples = [(self.plan("absent"), "METADATA_ONLY"), (self.plan("null"), "NO_NUMERIC_VALUES"), (self.plan("duplicate"), "AMBIGUOUS_GRAIN"), (self.plan("quarter"), "INVALID_TEMPORAL_AGGREGATION")]
        count = self.plan("count", operations=[{"op": "deflate", "column": "count", "index": "cpi", "base_period": "2021-01", "output": "real"}])
        count["columns"].append(self.add("cpi"))
        examples.append((count, "UNIT_MISMATCH"))
        for plan, expected in examples:
            with self.subTest(code=expected):
                result = self.service.validate_plan(plan)
                self.assertEqual("blocked", result["status"])
                self.assertEqual(expected, result["errors"][0]["code"])
                self.assertIsInstance(result["errors"][0]["message"], str)

    def test_ratio_requires_matching_populations_or_explicit_comparison(self):
        plan = self.plan(operations=[{"op": "ratio", "column": "credit", "denominator": "other_population", "output": "comparison"}])
        plan["columns"].append(self.add("other_population"))
        blocked = self.service.validate_plan(plan)
        self.assertEqual("SCOPE_MISMATCH", blocked["errors"][0]["code"])
        plan["operations"][0].update(scope_policy="explicit_comparison", scope_reason="Compare the reported amounts, without assuming common institution coverage")
        result = self.service.execute(plan)
        self.assertAlmostEqual(1000, result["preview"][0]["comparison"])
        self.assertTrue(any(w["code"] == "cross_scope_comparison" for w in result["warnings"]))
        self.assertIn("comparison", result["schema"]["comparison"]["scope"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "comparison", "period": "2021-01"})
        self.assertEqual("explicit_comparison", proof["lineage"]["operation"]["scope_policy"])

    def test_different_group_dimensions_cannot_silently_form_a_ratio(self):
        plan = self.plan(operations=[{"op": "ratio", "column": "credit", "denominator": "other_group", "output": "share"}])
        plan["columns"].append({"name": "other_group", "metric_id": "credit", "dimensions": {"group_code": 10002}})
        result = self.service.validate_plan(plan)
        self.assertEqual("SCOPE_MISMATCH", result["errors"][0]["code"])

    def test_same_scope_ratio_and_heterogeneous_table_alignment_are_explicit(self):
        plan = self.plan(operations=[{"op": "ratio", "column": "credit", "denominator": "credit_copy", "output": "identity"}])
        plan["columns"].append({"name": "credit_copy", "metric_id": "credit", "dimensions": {"group_code": 10001}})
        result = self.service.execute(plan)
        self.assertEqual(100, result["preview"][0]["identity"])
        table = self.plan()
        table["columns"].append(self.add("other_population"))
        aligned = self.service.execute(table)
        self.assertEqual("period", aligned["join_contract"]["key"])
        self.assertFalse(aligned["join_contract"]["population_equivalence_asserted"])
        self.assertTrue(any(w["code"] == "heterogeneous_scopes_aligned" for w in aligned["warnings"]))

    def test_large_integer_native_values_and_missing_periods_remain_exact(self):
        source = self.root / "large_counts.csv"
        source.write_text("month,count\n2021-01,9007199254740993\n2021-03,9007199254740995\n", encoding="utf-8")
        contract = {"name": "large_counts", "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "count": {"dtype": "integer", "unit": "count", "kind": "count_stock", "nullable": False}}, "key": ["month"], "grain": ["month"], "date_column": "month", "frequency": "monthly"}
        workspace = self.store.ingest_csv(self.workspace["workspace_id"], source, contract, expected_version=0)
        plan = {"start": "2021-01", "end": "2021-03", "frequency": "monthly", "columns": [{"name": "count", "metric_id": f"overlay:{workspace['datasets'][0]}:count"}]}
        result = self.service.execute(plan)
        frame, _ = self.store.load_analysis(result["analysis_id"])
        self.assertEqual("Int64", str(frame["count"].dtype))
        self.assertEqual(9007199254740993, result["preview"][0]["count"])
        self.assertIsNone(result["preview"][1]["count"])
        self.assertEqual(9007199254740995, int(frame["count"].iloc[2]))
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "count", "period": "2021-01"})
        self.assertEqual(9007199254740993, proof["value"])
        self.assertEqual(9007199254740993, proof["lineage"]["source_cells"][0]["source_value"])
        plan["operations"] = [{"op": "growth", "column": "count", "output": "growth"}]
        blocked = self.service.validate_plan(plan)
        self.assertEqual("NUMERIC_PRECISION_UNSUPPORTED", blocked["errors"][0]["code"])


if __name__ == "__main__":
    unittest.main()
