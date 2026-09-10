"""Statistical semantics and provenance tests using immutable saved analyses."""

from pathlib import Path
import tempfile
import unittest

import duckdb
import numpy as np
import pandas as pd

from agentic_analytics.agent.tools.statistics import StatisticsError, StatisticsTools
from agentic_analytics.lakehouse.store import LakehouseStore


class AgentStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        database = self.directory / "source.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(self.directory / "store")
        snapshot = self.store.publish_snapshot(database)
        self.store.create_workspace(snapshot["snapshot_id"], "workspace_stats")
        self.stats = StatisticsTools(self.store, "workspace_stats")

    def save(self, values, **columns):
        frame = pd.DataFrame({"period": pd.period_range("2000-01", periods=len(values), freq="M").astype(str), "value": values, **columns})
        workspace = self.store.workspace("workspace_stats")
        return self.store.save_analysis("workspace_stats", frame, {"frequency": "monthly"}, {"source": "synthetic"}, expected_version=workspace["version"])["analysis_id"]

    def test_rolling_mad_uses_only_past_and_flags_constant_baseline_deviation(self):
        first = self.save([10.0] * 8 + [100.0] + [10.0] * 8)
        second = self.save([10.0] * 8 + [100.0] + [999.0] * 8)
        result = self.stats.rolling_anomalies(first, "value", window=6, min_history=4)
        changed = self.stats.rolling_anomalies(second, "value", window=6, min_history=4)
        self.assertEqual(result["results"]["rows"][:9], changed["results"]["rows"][:9])
        self.assertTrue(result["results"]["rows"][8]["anomaly"])
        self.assertIsNone(result["results"]["rows"][8]["score"])
        self.assertEqual(self.stats.load_artifact(result["artifact_id"])["analysis_id"], first)
        self.assertFalse(result["causal_claim"])

    def test_change_scan_finds_sustained_shift_and_rejects_missingness(self):
        analysis = self.save([10.0] * 18 + [30.0] * 18)
        result = self.stats.detect_changes(analysis, "value", window=6)
        changes = result["results"]["changes"]
        self.assertTrue(any(abs(change["index"] - 18) <= 2 and change["shift"] == 20 for change in changes))
        self.assertIn("Retrospective", result["warnings"][0])
        missing = self.save([10.0] * 8 + [np.nan] + [30.0] * 15)
        with self.assertRaisesRegex(StatisticsError, "complete observations"):
            self.stats.detect_changes(missing, "value")

    def test_positive_lag_pairs_earlier_x_with_current_y(self):
        rng = np.random.default_rng(21)
        x = rng.normal(size=80)
        y = np.r_[np.nan, x[:-1] * 2]
        analysis = self.save(x, response=y)
        result = self.stats.analyze_relationship(analysis, "value", "response", lag=1)
        self.assertAlmostEqual(result["results"]["correlation"], 1.0)
        self.assertEqual(result["results"]["sample_size"], 79)
        self.assertIn("value(t-1)", result["results"]["lag_interpretation"])
        self.assertFalse(result["causal_claim"])
        zero = self.stats.analyze_relationship(analysis, "value", "response", lag=0)
        self.assertLess(abs(zero["results"]["correlation"]), 0.5)

    def test_granger_direction_and_stationarity_gate(self):
        rng = np.random.default_rng(17)
        x = rng.normal(size=180)
        y = np.r_[0, x[:-1]] + rng.normal(scale=0.2, size=180)
        analysis = self.save(x, response=y)
        result = self.stats.analyze_relationship(analysis, "value", "response", lag=1, method="granger")
        self.assertLess(result["results"]["p_value"], 0.001)
        self.assertEqual(result["results"]["direction"], "past value adds predictive information for response")
        self.assertFalse(result["causal_claim"])
        missing = self.save(np.r_[x[:30], np.nan, x[31:]], response=y)
        with self.assertRaisesRegex(StatisticsError, "contiguous complete"):
            self.stats.analyze_relationship(missing, "value", "response", lag=1, method="granger")

    def test_ownership_constant_series_and_parameter_gates(self):
        analysis = self.save([1.0] * 24, response=[2.0] * 24)
        result = self.stats.extra_tools()["analyze_relationship"]["handler"]({"analysis_id": analysis, "x": "value", "y": "response"})
        self.assertEqual(result["code"], "CONSTANT_SERIES")
        snapshot = self.store.workspace("workspace_stats")["snapshot_id"]
        self.store.create_workspace(snapshot, "workspace_other")
        other = StatisticsTools(self.store, "workspace_other")
        with self.assertRaisesRegex(StatisticsError, "another workspace"):
            other.rolling_anomalies(analysis, "value")
        with self.assertRaises(StatisticsError):
            self.stats.rolling_anomalies(analysis, "value", window=True)

    def test_large_result_has_bounded_preview_and_complete_saved_artifact(self):
        analysis = self.save([10.0] * 200)
        result = self.stats.rolling_anomalies(analysis, "value")
        self.assertTrue(result["preview_truncated"])
        self.assertEqual(len(result["results"]["rows"]), 20)
        self.assertEqual(len(self.stats.load_artifact(result["artifact_id"])["results"]["rows"]), 200)


if __name__ == "__main__":
    unittest.main()
