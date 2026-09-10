"""Chart semantics, full data, immutable artifacts and precision boundaries."""

from pathlib import Path
import json
import tempfile
import unittest

import duckdb
import pandas as pd

from tools.agent_charts import ChartError, ChartTools
from tools.lakehouse_store import LakehouseStore


class AgentChartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        db = root / "source.duckdb"
        with duckdb.connect(str(db)) as connection:
            connection.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(root / "store")
        self.snapshot = self.store.publish_snapshot(db)["snapshot_id"]
        self.store.create_workspace(self.snapshot, "workspace_charts")
        self.charts = ChartTools(self.store, "workspace_charts")

    def save(self, values, schema=None, operations=None, warnings=None, **others):
        frame = pd.DataFrame({"period": pd.period_range("2000-01", periods=len(values), freq="M").astype(str), "credit": values, **others})
        default = {column: {"kind": "stock", "unit": "TRY", "scale": 1000000, "currency": "TRY", "status": "ready", "metric_id": "fixture:" + column} for column in frame if column != "period"}
        for column, overrides in (schema or {}).items():
            default.setdefault(column, {}).update(overrides)
        sources = {column: {"binding": {"title": "Konut kredisi" if column == "credit" else column,
                                        "metric_id": default[column]["metric_id"], "source_system": "FIXTURE",
                                        "dimension_labels": {"bank": {"a": "Banka A", "b": "Banka B"}}}}
                   for column in default}
        lineage = {"sources": sources, "frequency": "monthly", "operations": operations or [], "warnings": warnings or []}
        manifest = self.store.save_analysis("workspace_charts", frame,
            {"frequency": "monthly", "operations": operations or []}, lineage,
            schema=default, expected_version=self.store.workspace("workspace_charts")["version"])
        return manifest["analysis_id"]

    def test_default_is_complete_deterministic_and_does_not_create_directories(self):
        aid = self.save(list(range(1, 402)), rate=[5.0] * 401,
                        schema={"rate": {"unit": "percent", "kind": "rate", "currency": None, "scale": 1}})
        before = self.store.workspace("workspace_charts")
        first = self.charts.get_chart(aid)
        self.assertEqual(first, self.charts.get_chart(aid))
        self.assertEqual(first["row_count"], 401)
        self.assertTrue(first["complete"])
        self.assertEqual(len(first["periods"]), 401)
        self.assertEqual(first["series"][0]["values"], list(range(1, 402)))
        self.assertEqual(first["spec"]["layout"], "panels")
        self.assertFalse(self.charts.root.exists())
        self.assertEqual(before, self.store.workspace("workspace_charts"))
        self.assertLessEqual(len(first["recommendations"]), 3)
        self.assertTrue(any("Yıllık" in x["label"] for x in first["recommendations"]))

    def test_saved_views_are_compact_content_addressed_and_do_not_mutate_data(self):
        aid = self.save([10.0, None, 30.0])
        before_frame, before_manifest = self.store.load_analysis(aid)
        before_workspace = self.store.workspace("workspace_charts")
        args = {"analysis_id": aid, "kind": "bar", "title": "Konut görünümü"}
        first = self.charts.create_chart(args)
        self.assertEqual(first, self.charts.create_chart(args))
        self.assertNotIn("series", first)
        self.assertNotIn("periods", first)
        full = self.charts.load_artifact(first["chart_id"])
        self.assertEqual(full["series"][0]["values"], [10.0, None, 30.0])
        self.assertEqual(full, self.charts.get_chart(aid))
        second = self.charts.create_chart({"analysis_id": aid, "kind": "area"})
        self.assertNotEqual(first["chart_id"], second["chart_id"])
        self.assertEqual(self.charts.get_chart(aid)["spec"]["kind"], "area")
        self.assertEqual(self.charts.load_artifact(first["chart_id"])["spec"]["kind"], "bar")
        after_frame, after_manifest = self.store.load_analysis(aid)
        pd.testing.assert_frame_equal(before_frame, after_frame)
        self.assertEqual(before_manifest, after_manifest)
        self.assertEqual(before_workspace, self.store.workspace("workspace_charts"))

    def test_mixed_units_and_price_bases_require_panels_or_two_explicit_axes(self):
        aid = self.save([10.0, 20.0], nominal=[10.0, 30.0],
                        schema={"credit": {"price_basis": "2000-01"}})
        self.assertEqual(self.charts.get_chart(aid)["spec"]["layout"], "panels")
        with self.assertRaises(ChartError) as context:
            self.charts.create_chart({"analysis_id": aid, "layout": "overlay"})
        self.assertEqual(context.exception.code, "UNIT_MISMATCH")
        saved = self.charts.create_chart({"analysis_id": aid, "layout": "dual_axis"})
        full = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual([x["axis"] for x in full["series"]], ["left", "right"])
        self.assertIn("reel", full["series"][0]["label"])
        self.assertIn("Sol:", full["subtitle"])
        three = self.save([10.0, 20.0], a=[3.0, 4.0], b=[7.0, 8.0])
        with self.assertRaises(ChartError):
            self.charts.create_chart({"analysis_id": three, "layout": "dual_axis"})

    def test_normalization_uses_same_first_period_preserves_missing_and_raw_summaries(self):
        aid = self.save([10.0, None, 20.0], rate=[5.0, 6.0, 7.0],
                        schema={"rate": {"unit": "percent", "kind": "rate", "currency": None, "scale": 1}})
        saved = self.charts.create_chart({"analysis_id": aid, "normalize": "index100", "layout": "overlay"})
        full = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(full["spec"]["base_period"], "2000-01")
        self.assertEqual(full["series"][0]["values"], [100.0, None, 200.0])
        self.assertEqual(full["series"][0]["raw_values"], [10.0, None, 20.0])
        self.assertEqual(full["series"][0]["summary"]["first"], 10.0)
        self.assertEqual(full["series"][0]["display_summary"]["first"], 100.0)
        self.assertIn("=100", full["series"][0]["unit"])
        # The model receives computed values for both representations, so its
        # final explanation need not guess the normalized endpoint from raw money.
        self.assertEqual(saved["summary"][0]["last"], 20.0)
        self.assertEqual(saved["summary"][0]["display_summary"]["last"], 200.0)
        self.assertIn("=100", saved["summary"][0]["display_unit"])
        for first in (None, 0.0, -1.0):
            invalid = self.save([first, 10.0], other=[2.0, 4.0])
            with self.subTest(first=first), self.assertRaises(ChartError) as context:
                self.charts.create_chart({"analysis_id": invalid, "normalize": "index100"})
            self.assertEqual(context.exception.code, "INVALID_NORMALIZATION_BASE")

    def test_summary_respects_rates_indices_unknown_and_source_warnings(self):
        aid = self.save([4.0, 6.0], index=[100.0, 120.0], unknown=[8.0, 10.0],
            schema={"credit": {"kind": "rate", "unit": "percent", "scale": 1, "currency": None},
                    "index": {"kind": "index", "unit": "index", "scale": 1, "currency": None},
                    "unknown": {"kind": "unknown", "unit": "unknown", "scale": 1, "currency": None, "status": "review_required"}},
            warnings=[{"code": "observed_sample_mean", "column": "credit"}, {"code": "heterogeneous_scopes_aligned"}])
        chart = self.charts.get_chart(aid)
        rate, index, unknown = chart["series"]
        self.assertEqual(rate["summary"]["change"], 2.0)
        self.assertEqual(rate["summary"]["change_unit"], "yüzde puan")
        self.assertEqual(index["summary"]["change_unit"], "endeks puanı")
        self.assertIsNone(unknown["summary"]["change"])
        self.assertTrue(all(x["summary"]["change_percent"] is None for x in chart["series"]))
        self.assertTrue(any("aritmetik" in warning for warning in chart["warnings"]))
        self.assertTrue(any("kurumların" in warning for warning in chart["warnings"]))
        with self.assertRaises(ChartError) as context:
            self.charts.create_chart({"analysis_id": aid, "columns": ["unknown"], "normalize": "index100"})
        self.assertEqual(context.exception.code, "SEMANTICS_REVIEW_REQUIRED")

    def test_scatter_retains_periods_and_missing_pairs_without_filling(self):
        aid = self.save([10.0, None, 30.0], rate=[1.0, 2.0, None])
        saved = self.charts.create_chart({"analysis_id": aid, "kind": "scatter", "x": "rate", "columns": ["credit"]})
        chart = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(chart["x_values"], [1.0, 2.0, None])
        self.assertEqual(chart["periods"], ["2000-01", "2000-02", "2000-03"])
        self.assertEqual(chart["series"][0]["values"], [10.0, None, 30.0])
        with self.assertRaises(ChartError):
            self.charts.create_chart({"analysis_id": aid, "kind": "scatter", "x": "credit", "columns": ["credit"]})
        scaled_count = self.save([2.0, 3.0], schema={"credit": {
            "kind": "count", "unit": "count", "scale": 1000, "currency": None}})
        count_chart = self.charts.get_chart(scaled_count)
        self.assertEqual(count_chart["series"][0]["unit"], "bin adet")
        self.assertEqual(count_chart["series"][0]["values"], [2.0, 3.0])

    def test_precision_infinity_and_all_null_boundaries(self):
        for values, code in [([2**53, 2**53 + 1], "UNSAFE_INTEGER")]:
            aid = self.save(values)
            with self.subTest(code=code), self.assertRaises(ChartError) as context:
                self.charts.get_chart(aid)
            self.assertEqual(context.exception.code, code)
        # The real store already rejects infinity during publication; the chart adapter
        # also checks it before serialization when handed a numeric frame.
        with self.assertRaises(ChartError) as context:
            self.charts._values(pd.DataFrame({"value": [1.0, float("inf")]}), "value")
        self.assertEqual(context.exception.code, "NON_FINITE_VALUE")
        aid = self.save(pd.Series([pd.NA, pd.NA], dtype="Float64"))
        chart = self.charts.get_chart(aid)
        self.assertEqual(chart["series"][0]["values"], [None, None])
        self.assertIsNone(chart["series"][0]["summary"]["change"])
        self.assertEqual(chart["series"][0]["summary"]["missing_count"], 2)
        safe = self.save(pd.Series([2**53 - 2, 2**53 - 1], dtype="Int64"))
        self.assertEqual(self.charts.get_chart(safe)["series"][0]["values"], [2**53 - 2, 2**53 - 1])

    def test_cross_workspace_path_and_artifact_integrity(self):
        aid = self.save([1.0, 2.0])
        self.store.create_workspace(self.snapshot, "workspace_other")
        other = ChartTools(self.store, "workspace_other")
        for action in (lambda: other.get_chart(aid), lambda: other.create_chart({"analysis_id": aid})):
            with self.assertRaises(ChartError) as context:
                action()
            self.assertEqual(context.exception.code, "WORKSPACE_MISMATCH")
        for identifier in ("../escape", "chart_../../escape", 7):
            with self.assertRaises(ChartError):
                self.charts.load_artifact(identifier)
        saved = self.charts.create_chart({"analysis_id": aid})
        path = self.charts.root / (saved["chart_id"] + ".json")
        path.write_text("{}")
        with self.assertRaises(ChartError) as context:
            self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(context.exception.code, "ARTIFACT_HASH_MISMATCH")

    def grouped(self, periods):
        frame = pd.DataFrame({"period": periods, "bank": ["a", "b", "a"][:len(periods)], "value": [10.0, 20.0, 30.0][:len(periods)], "rank": [1, 2, 1][:len(periods)]})
        binding = {"title": "Grup kredisi", "source_system": "FIXTURE", "dimension_labels": {"bank": {"a": "Banka A", "b": "Banka B"}}}
        schema = {"value": {"kind": "stock", "unit": "TRY", "scale": 1, "currency": "TRY", "status": "ready"}, "bank": {"kind": "dimension"}, "rank": {"kind": "rank"}}
        return self.store.save_analysis("workspace_charts", frame,
            {"query_type": "grouped", "request": {"group_by": "bank"}},
            {"group_by": "bank", "frequency": "monthly", "groups": {"a": {"sources": {"value": {"binding": binding}}}}},
            schema=schema, expected_version=self.store.workspace("workspace_charts")["version"])["analysis_id"]

    def test_grouped_bar_and_heatmap_preserve_drilldown_grain(self):
        aid = self.grouped(["2000-01", "2000-01"])
        bar = self.charts.get_chart(aid)
        self.assertEqual(bar["spec"]["kind"], "bar")
        self.assertEqual(bar["categories"], ["Banka A", "Banka B"])
        self.assertEqual(bar["point_dimensions"], [{"bank": "a"}, {"bank": "b"}])
        self.assertEqual(bar["available_columns"][0]["column"], "value")
        self.assertEqual(len(bar["available_columns"]), 1)
        self.assertIsNone(bar["series"][0]["summary"]["change"])
        aid = self.grouped(["2000-01", "2000-01", "2000-02"])
        heatmap = self.charts.get_chart(aid)
        self.assertEqual(heatmap["spec"]["kind"], "heatmap")
        self.assertEqual(heatmap["periods"], ["2000-01", "2000-02"])
        self.assertEqual(len(heatmap["cells"]), 4)
        self.assertIsNone(heatmap["cells"][-1]["value"])
        self.assertFalse(heatmap["cells"][-1]["source_row_available"])
        self.assertEqual(heatmap["cells"][2]["dimensions"], {"bank": "a"})
        with self.assertRaises(ChartError):
            self.charts.create_chart({"analysis_id": aid, "kind": "line"})

    def test_typed_tool_recovery_and_invalid_arguments(self):
        aid = self.save([1.0, 2.0])
        definition = self.charts.extra_tools()["create_chart"]
        self.assertTrue(definition["mutating"])
        self.assertFalse(definition["schema"]["function"]["parameters"]["additionalProperties"])
        for extra in ({"kind": "javascript"}, {"data": [1, 2]}, {"columns": []}, {"columns": ["credit", "credit"]}, {"title": "x" * 161}, {"orientation": True}, {"x": None}):
            with self.subTest(extra=extra):
                self.assertEqual(definition["handler"]({"analysis_id": aid, **extra})["status"], "blocked")
        args = {"analysis_id": aid, "kind": "bar"}
        first = self.charts.create_chart(args)
        latest = self.charts.create_chart({"analysis_id": aid, "kind": "area"})
        recovered = definition["recover"](args, {"workspace_id": "workspace_charts"})
        self.assertEqual(recovered["chart_id"], first["chart_id"])
        self.assertTrue(recovered["superseded"])
        self.assertEqual(self.charts.get_chart(aid)["chart_id"], latest["chart_id"])
        pointer = self.charts.root / "latest" / (aid + ".json")
        pointer.unlink()
        recovered = definition["recover"](args, {"workspace_id": "workspace_charts"})
        self.assertEqual(self.charts.get_chart(aid)["chart_id"], first["chart_id"])

    def test_short_flows_default_to_bars_and_long_flows_to_lines(self):
        aid = self.save([10.0, 15.0, 20.0], schema={"credit": {"kind": "flow"}})
        self.assertEqual(self.charts.get_chart(aid)["spec"]["kind"], "bar")
        aid = self.save([10.0] * 25, schema={"credit": {"kind": "flow"}})
        self.assertEqual(self.charts.get_chart(aid)["spec"]["kind"], "line")


if __name__ == "__main__":
    unittest.main()
