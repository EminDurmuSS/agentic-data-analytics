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

    def test_change_scan_starts_after_an_undefined_first_change(self):
        analysis = self.save([np.nan] + [10.0] * 17 + [30.0] * 18)
        result = self.stats.detect_changes(analysis, "value", window=6)["results"]
        self.assertEqual(result["edge_undefined_periods"], ["2000-01"])
        # Indices refer to the saved analysis rows, as without the undefined edge.
        self.assertTrue(any(abs(change["index"] - 18) <= 2 and change["shift"] == 20 for change in result["changes"]))

    def test_positive_lag_pairs_earlier_x_with_current_y(self):
        rng = np.random.default_rng(21)
        x = rng.normal(size=80)
        y = np.r_[np.nan, x[:-1] * 2]
        analysis = self.save(x, response=y)
        result = self.stats.analyze_relationship(analysis, "value", "response", lag=1)
        self.assertAlmostEqual(result["results"]["correlation"], 1.0)
        self.assertEqual(result["results"]["sample_size"], 79)
        self.assertEqual(result["results"]["candidate_pair_count"], 79)
        self.assertEqual(result["results"]["sample_start_period"], "2000-02")
        self.assertEqual(result["results"]["sample_end_period"], "2006-08")
        self.assertEqual(result["results"]["lag_dropped_periods"], ["2000-01"])
        self.assertEqual(result["results"]["excluded_missing_periods"], [])
        self.assertIn("value(t-1)", result["results"]["lag_interpretation"])
        self.assertFalse(result["causal_claim"])
        zero = self.stats.analyze_relationship(analysis, "value", "response", lag=0)
        self.assertLess(abs(zero["results"]["correlation"]), 0.5)

    def test_spearman_uses_the_same_complete_pairs_and_records_missing_periods(self):
        x = np.arange(1, 19, dtype=float)
        y = x ** 3
        x[3] = np.nan
        y[7] = np.nan
        analysis = self.save(x, response=y)
        pearson = self.stats.analyze_relationship(
            analysis, "value", "response", method="pearson", max_missing_fraction=0.2)
        spearman = self.stats.analyze_relationship(
            analysis, "value", "response", method="spearman", max_missing_fraction=0.2)
        self.assertLess(pearson["results"]["correlation"], 0.95)
        self.assertAlmostEqual(spearman["results"]["correlation"], 1.0)
        for key in ("sample_size", "candidate_pair_count", "sample_start_period", "sample_end_period",
                    "excluded_missing_pairs", "excluded_missing_periods", "sample_period_basis"):
            self.assertEqual(pearson["results"][key], spearman["results"][key])
        self.assertEqual(spearman["results"]["sample_size"], 16)
        self.assertEqual(spearman["results"]["excluded_missing_periods"], ["2000-04", "2000-08"])
        self.assertEqual(spearman["method"], "lagged_spearman")

    def test_granger_direction_and_stationarity_gate(self):
        rng = np.random.default_rng(17)
        x = rng.normal(size=180)
        y = np.r_[0, x[:-1]] + rng.normal(scale=0.2, size=180)
        analysis = self.save(x, response=y)
        result = self.stats.analyze_relationship(analysis, "value", "response", lag=1, method="granger")
        self.assertLess(result["results"]["p_value"], 0.001)
        self.assertEqual(result["results"]["direction"], "past value adds predictive information for response")
        self.assertEqual(result["results"]["test"], "ssr_ftest")
        self.assertIn("do not add predictive information", result["results"]["null_hypothesis"])
        self.assertEqual(result["results"]["sample_start_period"], "2000-01")
        self.assertFalse(result["causal_claim"])
        missing = self.save(np.r_[x[:30], np.nan, x[31:]], response=y)
        with self.assertRaisesRegex(StatisticsError, "contiguous complete"):
            self.stats.analyze_relationship(missing, "value", "response", lag=1, method="granger")

    def test_granger_starts_after_an_undefined_first_change_and_reports_it(self):
        # A monthly change saved in the analysis has no value for its first month.
        rng = np.random.default_rng(17)
        x = rng.normal(size=180)
        y = np.r_[0, x[:-1]] + rng.normal(scale=0.2, size=180)
        analysis = self.save(np.r_[np.nan, x[1:]], response=y)
        result = self.stats.analyze_relationship(analysis, "value", "response", lag=1, method="granger")["results"]
        self.assertEqual((result["sample_start_period"], result["edge_undefined_periods"]), ("2000-02", ["2000-01"]))
        self.assertEqual(result["sample_size"], 179)

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

    def save_calendar(self, frequency, labels):
        return self.store.save_analysis("workspace_stats", pd.DataFrame({"period": labels, "value": [10.0] * (len(labels) - 1) + [100.0]}),
            {"frequency": frequency}, {"source": "synthetic_calendar"},
            expected_version=self.store.workspace("workspace_stats")["version"])["analysis_id"]

    def test_native_calendar_aliases_preserve_labels_and_detect_same_observation(self):
        cases = {
            "weekly": pd.date_range("2026-01-02", periods=12, freq="W-FRI").strftime("%Y-%m-%d").tolist(),
            "weekly_friday": pd.date_range("2026-01-02", periods=12, freq="W-FRI").strftime("%Y-%m-%d").tolist(),
            "weekly_wednesday": pd.date_range("2026-01-07", periods=12, freq="W-WED").strftime("%Y-%m-%d").tolist(),
            "yearly": [str(year) for year in range(2015, 2027)],
            "annual": [str(year) for year in range(2015, 2027)],
            "half_yearly": [f"{year}-H{half}" for year in range(2021, 2027) for half in (1, 2)],
        }
        for frequency, labels in cases.items():
            with self.subTest(frequency=frequency):
                aid = self.save_calendar(frequency, labels)
                before = self.store.load_analysis(aid)
                result = self.stats.rolling_anomalies(aid, "value", window=6, min_history=4)
                self.assertTrue(result["results"]["rows"][-1]["anomaly"])
                self.assertEqual(result["results"]["rows"][-1]["period"], labels[-1])
                pd.testing.assert_frame_equal(before[0], self.store.load_analysis(aid)[0])
                self.assertEqual(before[1], self.store.load_analysis(aid)[1])
                skipped = self.save_calendar(frequency, labels[:5] + labels[6:])
                with self.assertRaises(StatisticsError) as failure:
                    self.stats.rolling_anomalies(skipped, "value", window=6, min_history=4)
                self.assertEqual(failure.exception.code, "IRREGULAR_TIME_AXIS")

    def test_explicit_calendars_are_not_silently_inferred_as_daily(self):
        dates = pd.date_range("2026-01-01", periods=12).strftime("%Y-%m-%d").tolist()
        for frequency in ("twice_monthly", "event", "static", "unsupported"):
            aid = self.save_calendar(frequency, dates)
            with self.subTest(frequency=frequency), self.assertRaises(StatisticsError) as failure:
                self.stats.rolling_anomalies(aid, "value")
            self.assertEqual(failure.exception.code, "UNKNOWN_FREQUENCY")
        thursdays = pd.date_range("2026-01-01", periods=12, freq="W-THU").strftime("%Y-%m-%d").tolist()
        for frequency in ("weekly_friday", "weekly_wednesday"):
            aid = self.save_calendar(frequency, thursdays)
            with self.assertRaises(StatisticsError) as failure:
                self.stats.rolling_anomalies(aid, "value")
            self.assertEqual(failure.exception.code, "INVALID_TIME_LABEL")


if __name__ == "__main__":
    unittest.main()
