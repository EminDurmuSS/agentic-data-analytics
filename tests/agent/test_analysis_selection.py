"""Exact conditional row selection over immutable analysis results."""
from pathlib import Path
import json
import tempfile
import unittest

import duckdb
import numpy as np
import pandas as pd

from agentic_analytics.agent.tools.selection import AnalysisSelectionTools, SelectionError
from agentic_analytics.agent.delivery import _selection_confirmation
from agentic_analytics.lakehouse.store import LakehouseStore


class AnalysisSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        database = root / "source.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(root / "store")
        snapshot = self.store.publish_snapshot(database)
        self.store.create_workspace(snapshot["snapshot_id"], "selection_test")
        self.tools = AnalysisSelectionTools(self.store, "selection_test")

    def save(self):
        frame = pd.DataFrame({
            "period": ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"],
            "rate_change": [np.nan, -1.0, -0.5, 0.25, -0.1],
            "real_stock_change": [np.nan, 2.0, 0.0, -3.0, -7.0],
            "source_a": pd.Series([10, 20, 30, pd.NA, 50], dtype="Int64"),
            "source_b": pd.Series([10, 21, 30, 40, pd.NA], dtype="Int64"),
        })
        schema = {
            "period": {"kind": "dimension", "unit": "period", "status": "ready"},
            "rate_change": {"kind": "difference", "unit": "percentage_point", "scale": 1,
                            "currency": None, "measurement_basis": "source_reported", "status": "ready"},
            "real_stock_change": {"kind": "difference", "unit": "TRY", "scale": 1000000,
                                  "currency": "TRY", "measurement_basis": "real_2021_01", "status": "ready"},
            "source_a": {"kind": "count_flow", "unit": "count", "scale": 1,
                         "currency": None, "measurement_basis": "same_definition", "status": "ready"},
            "source_b": {"kind": "count_flow", "unit": "count", "scale": 1,
                         "currency": None, "measurement_basis": "same_definition", "status": "ready"},
        }
        workspace = self.store.workspace("selection_test")
        return self.store.save_analysis(
            "selection_test", frame, {"frequency": "monthly"}, {"source": "fixture"}, schema=schema,
            expected_version=workspace["version"],
        )["analysis_id"]

    def test_filters_are_conjunctive_and_nulls_are_never_implicit_values(self):
        analysis_id = self.save()
        result = self.tools.select_analysis_rows(
            analysis_id,
            [
                {"column": "rate_change", "op": "not_null"},
                {"column": "real_stock_change", "op": "not_null"},
                {"column": "rate_change", "op": "lt", "value": 0},
                {"column": "real_stock_change", "op": "lte", "value": 0},
            ],
            columns=["period", "rate_change", "real_stock_change"],
        )
        self.assertEqual(["2025-03", "2025-05"], [row["period"] for row in result["rows"]])
        self.assertEqual(2, result["total_match_count"])
        self.assertIn("data_sha256", result["provenance"])
        self.assertEqual(result, self.tools.load_artifact(result["artifact_id"]))

    def test_cross_column_mismatch_excludes_missing_values_unless_requested(self):
        analysis_id = self.save()
        mismatch = self.tools.select_analysis_rows(
            analysis_id,
            [
                {"column": "source_a", "op": "not_null"},
                {"column": "source_b", "op": "not_null"},
                {"column": "source_a", "op": "ne", "other_column": "source_b"},
            ],
            columns=["period", "source_a", "source_b"],
        )
        self.assertEqual([{"period": "2025-02", "source_a": 20, "source_b": 21}], mismatch["rows"])
        missing = self.tools.select_analysis_rows(
            analysis_id,
            [{"column": "source_a", "op": "is_null"}],
            columns=["period", "source_a", "source_b"],
        )
        self.assertEqual("2025-04", missing["rows"][0]["period"])

    def test_absolute_sort_finds_the_largest_change_without_recomputing_the_analysis(self):
        analysis_id = self.save()
        before = self.store.load_analysis(analysis_id)
        result = self.tools.select_analysis_rows(
            analysis_id,
            [{"column": "real_stock_change", "op": "not_null"}],
            columns=["period", "real_stock_change"],
            sort={"column": "real_stock_change", "direction": "desc", "absolute": True},
            limit=1,
        )
        self.assertEqual([{"period": "2025-05", "real_stock_change": -7.0}], result["rows"])
        self.assertEqual(4, result["total_match_count"])
        self.assertTrue(result["truncated"])
        after = self.store.load_analysis(analysis_id)
        pd.testing.assert_frame_equal(before[0], after[0])
        self.assertEqual(before[1], after[1])

    def test_incompatible_columns_and_tampered_artifacts_are_rejected(self):
        analysis_id = self.save()
        with self.assertRaises(SelectionError) as failure:
            self.tools.select_analysis_rows(analysis_id, [
                {"column": "rate_change", "op": "lt", "other_column": "real_stock_change"},
            ])
        self.assertEqual("INCOMPATIBLE_COLUMNS", failure.exception.code)
        saved = self.tools.select_analysis_rows(
            analysis_id, [{"column": "source_a", "op": "not_null"}], limit=1,
        )
        path = self.tools.root / (saved["artifact_id"] + ".json")
        payload = json.loads(path.read_text())
        payload["rows"][0]["source_a"] = 999
        path.chmod(0o644)
        path.write_text(json.dumps(payload))
        with self.assertRaises(SelectionError) as tampered:
            self.tools.load_artifact(saved["artifact_id"])
        self.assertEqual("SELECTION_INTEGRITY_ERROR", tampered.exception.code)

    def test_cross_source_equality_preserves_contract_differences_as_an_explicit_caveat(self):
        original = self.save()
        frame, manifest = self.store.load_analysis(original)
        schema = json.loads(json.dumps(manifest["schema"]))
        schema["source_b"]["measurement_basis"] = "other_official_vintage"
        schema["source_b"]["scope"] = {"namespace": "OTHER_SOURCE", "geography_scope": "province"}
        workspace = self.store.workspace("selection_test")
        analysis_id = self.store.save_analysis(
            "selection_test", frame, {"frequency": "monthly"}, {"source": "cross_source_fixture"},
            schema=schema, expected_version=workspace["version"],
        )["analysis_id"]
        mismatch = self.tools.select_analysis_rows(
            analysis_id,
            [{"column": "source_a", "op": "ne", "other_column": "source_b"}],
            columns=["period", "source_a", "source_b"],
        )
        self.assertEqual([{"period": "2025-02", "source_a": 20, "source_b": 21}], mismatch["rows"])
        caveat = next(warning for warning in mismatch["warnings"] if isinstance(warning, dict))
        self.assertEqual("NUMERIC_EQUALITY_ONLY_ACROSS_DISTINCT_SOURCE_CONTRACTS", caveat["code"])
        self.assertIn("measurement_basis", caveat["differences"])
        self.assertIn("scope", caveat["differences"])
        with self.assertRaises(SelectionError) as ordered:
            self.tools.select_analysis_rows(analysis_id, [
                {"column": "source_a", "op": "lt", "other_column": "source_b"},
            ])
        self.assertEqual("INCOMPATIBLE_COLUMNS", ordered.exception.code)
        message = _selection_confirmation(self.store, "selection_test", {
            "analysis_id": analysis_id,
            "tool_results": [{"tool": "select_analysis_rows", "result": mismatch}],
        })
        self.assertIn("yalnız kayıtlı sayısal eşitlik", message)
        self.assertIn("eşdeğer sayılmadı", message)

    def test_tool_handler_returns_machine_readable_failures(self):
        analysis_id = self.save()
        result = self.tools.extra_tools()["select_analysis_rows"]["handler"]({
            "analysis_id": analysis_id,
            "filters": [{"column": "missing", "op": "not_null"}],
        })
        self.assertEqual("blocked", result["status"])
        self.assertEqual("COLUMN_NOT_FOUND", result["code"])

    def test_delivery_is_rendered_from_the_verified_artifact(self):
        analysis_id = self.save()
        selected = self.tools.select_analysis_rows(
            analysis_id,
            [{"column": "source_a", "op": "ne", "other_column": "source_b"}],
            columns=["period", "source_a", "source_b"],
        )
        message = _selection_confirmation(self.store, "selection_test", {
            "analysis_id": analysis_id,
            "tool_results": [{"tool": "select_analysis_rows", "result": selected}],
        })
        self.assertIn("Şubat 2025", message)
        self.assertIn("20", message)
        self.assertNotIn("Nisan 2025", message)
        self.assertNotIn("Mayıs 2025", message)


if __name__ == "__main__":
    unittest.main()
