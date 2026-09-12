"""Chart semantics, full data, immutable artifacts and precision boundaries."""

from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import duckdb
import pandas as pd

from agentic_analytics.agent.tools.charts import ChartError, ChartTools
from agentic_analytics.lakehouse.store import LakehouseStore


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

    def test_public_presentation_cleans_legacy_labels_and_notes_without_rewriting_chart(self):
        frame = pd.DataFrame({"period": ["2026-03"], "company_assets": [225000],
                              "company_million": [225], "sector_assets": [1000], "ratio_percent": [22.5]})
        base = {"kind": "stock", "unit": "TRY", "scale": 1000000, "currency": "TRY", "status": "ready"}
        schema = {"company_assets": {**base, "scale": 1000, "metric_id": "overlay:amount"},
                  "company_million": {**base, "metric_id": "overlay:amount"},
                  "sector_assets": {**base, "metric_id": "catalog:assets"},
                  "ratio_percent": {**base, "unit": "percent", "kind": "ratio", "scale": 1, "currency": None}}
        operations = [{"op": "scale", "column": "company_assets", "output": "company_million", "target_scale": 1000000},
                      {"op": "ratio", "column": "company_million", "denominator": "sector_assets", "output": "ratio_percent", "multiplier": 100}]
        sources = {"company_assets": {"binding": {"title": "source_financial_facts: amount", "source_system": "SESSION_DATASET",
                        "metric_id": "overlay:amount", "document_provenance": {
                            "source_url": "https://reports.example.org/2026_Quarterly_Report.pdf", "page": 11}}},
                   "sector_assets": {"binding": {"title": "Sektör toplam aktifleri", "source_system": "CATALOG", "metric_id": "catalog:assets"}}}
        lineage = {"sources": sources, "operations": operations, "frequency": "monthly", "warnings": [
            {"code": "heterogeneous_scopes_aligned"},
            {"code": "exact_event_period_end", "column": "company_assets"},
            {"code": "cross_scope_comparison", "column": "ratio_percent", "scope_reason": "PRIVATE MODEL EXPLANATION 999"}]}
        saved = self.store.save_analysis("workspace_charts", frame, {"frequency": "monthly", "operations": operations},
            lineage, schema=schema, expected_version=0)
        aid = saved["analysis_id"]
        result = self.charts.create_chart({"analysis_id": aid, "kind": "area", "layout": "panels",
                                           "columns": ["company_million", "sector_assets", "ratio_percent"]})
        path = self.charts.root / (result["chart_id"] + ".json")
        original = path.read_bytes()
        recorded = json.loads(original)
        before_frame, before_manifest = self.store.load_analysis(aid)
        before_workspace = self.store.workspace("workspace_charts")
        self.assertIn("SESSION_DATASET", str(recorded["sources"]))
        chart = self.charts.load_artifact(result["chart_id"])
        self.assertEqual(chart, self.charts.get_chart(aid))
        self.assertEqual({key: chart[key] for key in recorded}, recorded)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(chart["spec"]["layout"], "panels")
        self.assertEqual(chart["spec"]["kind"], "area")
        presentation = chart["presentation"]
        self.assertEqual(presentation["unit_groups"][0]["columns"], ["company_million", "sector_assets"])
        self.assertEqual(presentation["unit_groups"][1]["columns"], ["ratio_percent"])
        self.assertEqual(presentation["labels"]["company_million"], "Company assets")
        self.assertEqual(presentation["sources"][0], {"label": "2026 Quarterly Report", "url": "https://reports.example.org/2026_Quarterly_Report.pdf", "page": 11})
        notes = {note["code"]: note for note in presentation["warnings"]}
        self.assertEqual(notes["exact_event_period_end"]["level"], "info")
        self.assertIn("ara dönemlere", notes["exact_event_period_end"]["message"])
        self.assertNotIn("heterogeneous_scopes_aligned", notes)
        self.assertIn("resmi pazar payı değildir", notes["cross_scope_comparison"]["message"])
        for technical in ["SESSION_DATASET", "source_financial_facts", "PRIVATE MODEL", "999"]:
            self.assertNotIn(technical, json.dumps(presentation, ensure_ascii=False))
        after_frame, after_manifest = self.store.load_analysis(aid)
        pd.testing.assert_frame_equal(before_frame, after_frame)
        self.assertEqual(before_manifest, after_manifest)
        self.assertEqual(before_workspace, self.store.workspace("workspace_charts"))

    def test_public_unit_groups_require_metadata_compatibility_not_just_matching_unit_text(self):
        for overrides in [{"price_basis": "2020"}, {"measurement_basis": "regulatory_liquidity_weighted"},
                          {"currency": "USD"}, {"kind": "unknown"}, {"status": "review_required"}]:
            with self.subTest(metadata=overrides):
                aid = self.save([10, 20], other=[30, 40], schema={"other": overrides})
                chart = self.charts.get_chart(aid)
                self.assertEqual(chart["series"][0]["unit"], chart["series"][1]["unit"])
                self.assertEqual(len(chart["presentation"]["unit_groups"]), 2)
        aid = self.save([10, 20], other=[30, 40], schema={
            "credit": {"kind": "index", "unit": "index", "currency": None, "scale": 1},
            "other": {"kind": "index", "unit": "index", "currency": None, "scale": 1}})
        self.assertEqual(len(self.charts.get_chart(aid)["presentation"]["unit_groups"]), 2)
        normalized = self.charts.create_chart({"analysis_id": aid, "normalize": "index100", "layout": "overlay"})
        self.assertEqual(len(self.charts.load_artifact(normalized["chart_id"])["presentation"]["unit_groups"]), 1)

    def test_legacy_hashed_chart_with_structured_warnings_projects_without_rewriting_raw_evidence(self):
        aid = self.save([10, 20])
        created = self.charts.create_chart({"analysis_id": aid})
        original_path = self.charts.root / (created["chart_id"] + ".json")
        original_bytes = original_path.read_bytes()
        legacy = json.loads(original_bytes)
        legacy["warnings"] = [
            {"code": "exact_event_period_end", "column": "credit", "message": "PRIVATE SOURCE DETAIL"},
            {"code": "heterogeneous_scopes_aligned", "detail": "PRIVATE SCOPE"},
            {"code": "cross_scope_comparison", "column": "credit", "scope_reason": "PRIVATE MODEL 999"},
            {"code": "unrecognized_legacy_code", "message": "PRIVATE FALLBACK"},
            {"code": ["malformed"], "message": "PRIVATE MALFORMED"}, None]
        encoded = json.dumps(legacy, ensure_ascii=False, sort_keys=True).encode()
        chart_id = "chart_" + hashlib.sha256(encoded).hexdigest()
        path = self.charts.root / (chart_id + ".json")
        path.write_bytes(encoded)
        projected = self.charts.load_artifact(chart_id)
        self.assertEqual(projected["warnings"], legacy["warnings"])
        self.assertEqual(projected["spec"], legacy["spec"])
        self.assertEqual(projected["series"], legacy["series"])
        notices = projected["presentation"]["warnings"]
        self.assertEqual({notice["code"] for notice in notices}, {
            "exact_event_period_end", "cross_scope_comparison", "source_method_note"})
        self.assertEqual(next(note for note in notices if note["code"] == "exact_event_period_end")["level"], "info")
        self.assertIn("resmi pazar payı değildir", next(note for note in notices if note["code"] == "cross_scope_comparison")["message"])
        self.assertNotIn("PRIVATE", str(notices))
        self.assertEqual(path.read_bytes(), encoded)
        self.assertEqual(original_path.read_bytes(), original_bytes)

    def test_wide_bank_groups_all_remain_charted_with_source_group_labels(self):
        for count in (9, 10):
            with self.subTest(groups=count):
                groups = list(range(10011-count, 10011))
                labels = {str(group): f"Bank group {group}" for group in groups}
                frame = pd.DataFrame({"period": ["2026-01", "2026-02", "2026-03"],
                                      **{f"profit_{group}": [group, None if group == groups[-1] else group+10, group+20] for group in groups}})
                schema = {name: {"kind": "flow", "unit": "TRY", "currency": "TRY", "scale": 1000000,
                                 "status": "ready", "metric_id": "fixture:same_profit_metric",
                                 "scope": {"dimensions": {"bank_group": group}}}
                          for name, group in zip(list(frame)[1:], groups)}
                sources = {name: {"dimensions": {"bank_group": group}, "binding": {
                    "metric_id": "fixture:same_profit_metric", "title": "Monthly net profit", "source_system": "FIXTURE",
                    "dimension_labels": {"bank_group": labels}}} for name, group in zip(schema, groups)}
                saved = self.store.save_analysis("workspace_charts", frame, {"frequency": "monthly"},
                    {"sources": sources, "frequency": "monthly"}, schema=schema,
                    expected_version=self.store.workspace("workspace_charts")["version"])
                aid = saved["analysis_id"]
                before_frame, before_manifest = self.store.load_analysis(aid)
                before_workspace = self.store.workspace("workspace_charts")
                for kind in ("line", "bar", "heatmap"):
                    for explicit in (False, True):
                        args = {"analysis_id": aid, "kind": kind}
                        if explicit:
                            args["columns"] = list(schema)
                        result = self.charts.create_chart(args)
                        chart = self.charts.load_artifact(result["chart_id"])
                        self.assertEqual(chart["spec"]["columns"], list(schema))
                        self.assertEqual(len(chart["series"]), count)
                        self.assertEqual(len({series["label"] for series in chart["series"]}), count)
                        for series, group in zip(chart["series"], groups):
                            self.assertIn(labels[str(group)], series["label"])
                            self.assertEqual(series["source_dimensions"], {"bank_group": group})
                            self.assertEqual(series.get("dimensions", {}), {})
                            expected = [group, None if group == groups[-1] else group+10, group+20]
                            self.assertEqual(series["values"], expected)
                            self.assertEqual(series["raw_values"], expected)
                        if kind == "heatmap":
                            self.assertEqual(len(chart["cells"]), count*3)
                            self.assertEqual({cell["source_dimensions"]["bank_group"] for cell in chart["cells"]}, set(groups))
                            self.assertTrue(all(cell["dimensions"] == {} for cell in chart["cells"]))
                after_frame, after_manifest = self.store.load_analysis(aid)
                pd.testing.assert_frame_equal(before_frame, after_frame)
                self.assertEqual(before_manifest, after_manifest)
                self.assertEqual(before_workspace, self.store.workspace("workspace_charts"))
                # Derived aliases share the same metric ID; each retains its
                # own source dimension, not the first matching metric's group.
                diff_schema = {"kind": "flow", "unit": "TRY", "scale": 1000000,
                               "metric_id": "fixture:same_profit_metric", "scope": {"dimensions": {"bank_group": groups[-1]}}}
                meta = self.charts._metadata({"schema": {"change": diff_schema}, "lineage": {"sources": sources},
                                             "plan": {"operations": [{"op": "difference", "column": f"profit_{groups[-1]}", "output": "change", "periods": 1}]}}, "change")
                self.assertIn(labels[str(groups[-1])], meta["label"])
                self.assertEqual(meta["dimensions"], {"bank_group": groups[-1]})

    def test_wide_series_limit_refuses_instead_of_truncating(self):
        aid = self.save([1, 2], **{f"measure_{number}": [number, number+1] for number in range(30)})
        before = self.store.workspace("workspace_charts")
        with self.assertRaises(ChartError) as context:
            self.charts.create_chart({"analysis_id": aid})
        self.assertEqual(context.exception.code, "CHART_SERIES_LIMIT")
        self.assertEqual(before, self.store.workspace("workspace_charts"))
        with self.assertRaises(ChartError):
            self.charts.create_chart({"analysis_id": aid, "columns": ["credit", *[f"measure_{n}" for n in range(30)]]})
        selected = [f"measure_{n}" for n in range(30)]
        chart = self.charts.load_artifact(self.charts.create_chart({"analysis_id": aid, "columns": selected})["chart_id"])
        self.assertEqual(chart["spec"]["columns"], selected)
        self.assertEqual(len(chart["series"]), 30)
        schema = self.charts.extra_tools()["create_chart"]["schema"]["function"]["parameters"]
        self.assertEqual(schema["properties"]["columns"]["maxItems"], 30)

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

    def test_single_series_scatter_is_explicitly_marked_as_a_limited_visual(self):
        aid = self.save([10.0, None, 30.0])
        saved = self.charts.create_chart({"analysis_id": aid, "kind": "scatter"})
        chart = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(chart["x_mode"], "period_index")
        self.assertEqual(chart["x_values"], [0, 1, 2])
        self.assertEqual(chart["series"][0]["values"], [10.0, None, 30.0])
        self.assertIn("Sınırlı görsel", chart["presentation_notice"])

    def test_metric_heatmap_is_explicitly_marked_as_a_limited_visual(self):
        aid = self.save([10.0, None, 30.0], rate=[1.0, 2.0, 3.0])
        saved = self.charts.create_chart({"analysis_id": aid, "kind": "heatmap", "columns": ["credit", "rate"]})
        chart = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(len(chart["cells"]), 6)
        self.assertIsNone(chart["cells"][1]["value"])
        self.assertTrue(chart["cells"][1]["source_row_available"])
        self.assertIn("Sınırlı görsel", chart["presentation_notice"])

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
        before_frame, before_manifest = self.store.load_analysis(aid)
        before_workspace = self.store.workspace("workspace_charts")
        for kind in ("line", "bar", "area"):
            saved = self.charts.create_chart({"analysis_id": aid, "kind": kind})
            chart = self.charts.load_artifact(saved["chart_id"])
            self.assertEqual(chart["group_mode"], "series")
            self.assertEqual(chart["periods"], ["2000-01", "2000-02"])
            self.assertEqual([s["label"] for s in chart["series"]], ["Banka A", "Banka B"])
            self.assertEqual([s["values"] for s in chart["series"]], [[10.0, 30.0], [20.0, None]])
            self.assertEqual([s["dimensions"] for s in chart["series"]], [{"bank": "a"}, {"bank": "b"}])
            self.assertEqual(chart["series"][1]["source_row_available"], [True, False])
            self.assertEqual(chart["series"][1]["summary"]["absent_row_count"], 1)
            self.assertEqual(len({s["series_id"] for s in chart["series"]}), 2)
            self.assertEqual([s["dimensions"] for s in saved["summary"]], [{"bank": "a"}, {"bank": "b"}])
            pd.testing.assert_frame_equal(before_frame, self.store.load_analysis(aid)[0])
            self.assertEqual(before_manifest, self.store.load_analysis(aid)[1])
            self.assertEqual(before_workspace, self.store.workspace("workspace_charts"))

    def test_group_series_preserve_changing_rank_membership_and_source_nulls(self):
        frame = pd.DataFrame({"period": ["2026-01", "2026-01", "2026-02", "2026-02", "2026-03", "2026-03"],
                              "bank": ["b", "a", "c", "b", "a", "c"],
                              "value": [50.0, 10.0, 60.0, None, 70.0, 5.0], "rank": [1, 2, 1, None, 1, 2]})
        schema = {"value": {"kind": "stock", "unit": "TRY", "scale": 1, "status": "ready"}, "rank": {"kind": "rank"}}
        binding = {"title": "Kredi", "dimension_labels": {"bank": {"a": "A", "b": "B", "c": "B"}}}
        aid = self.store.save_analysis("workspace_charts", frame,
            {"query_type": "grouped", "request": {"group_by": "bank", "limit": 2}},
            {"group_by": "bank", "frequency": "monthly", "groups": {"a": {"sources": {"value": {"binding": binding}}}}},
            schema=schema, expected_version=self.store.workspace("workspace_charts")["version"])["analysis_id"]
        for layout in ("overlay", "panels"):
            result = self.charts.create_chart({"analysis_id": aid, "kind": "line", "layout": layout})
            chart = self.charts.load_artifact(result["chart_id"])
            self.assertEqual([s["label"] for s in chart["series"]], ["B (b)", "A", "B (c)"])
            self.assertEqual([s["column"] for s in chart["series"]], ["value"] * 3)
            self.assertEqual(chart["series"][0]["values"], [50.0, None, None])
            self.assertEqual(chart["series"][0]["source_row_available"], [True, True, False])
            self.assertEqual(chart["series"][0]["summary"]["source_null_count"], 1)
            cells = {(row.period, row.bank): row.value for row in frame.itertuples()}
            for series in chart["series"]:
                for period, value, present in zip(chart["periods"], series["values"], series["source_row_available"]):
                    key = period, series["dimensions"]["bank"]
                    self.assertEqual(present, key in cells)
                    self.assertEqual(value, None if key not in cells or pd.isna(cells[key]) else cells[key])
            self.assertTrue(any("sıralama" in warning for warning in chart["warnings"]))
        for options in ({"normalize": "index100"}, {"layout": "dual_axis"}, {"columns": ["rank"]}):
            with self.subTest(options=options), self.assertRaises(ChartError):
                self.charts.create_chart({"analysis_id": aid, "kind": "line", **options})

    def test_revised_grouped_table_selects_a_single_measure_with_its_own_unit(self):
        base = self.grouped(["2000-01", "2000-01", "2000-02"])
        frame, manifest = self.store.load_analysis(base)
        frame["growth"] = [None, None, 200.0]
        schema = {**manifest["schema"], "growth": {"kind": "ratio", "unit": "percent", "scale": 1, "status": "ready"}}
        aid = self.store.save_analysis("workspace_charts", frame, manifest["plan"], manifest["lineage"], schema=schema,
            expected_version=self.store.workspace("workspace_charts")["version"])["analysis_id"]
        self.assertEqual(["value"], self.charts.get_chart(aid)["spec"]["columns"])
        saved = self.charts.create_chart({"analysis_id": aid, "kind": "line", "columns": ["growth"]})
        chart = self.charts.load_artifact(saved["chart_id"])
        self.assertEqual(["%", "%"], [series["unit"] for series in chart["series"]])
        self.assertEqual([None, 200.0], chart["series"][0]["values"])
        self.assertTrue(all(series["column"] == "growth" for series in chart["series"]))
        with self.assertRaises(ChartError):
            self.charts.create_chart({"analysis_id": aid, "kind": "line", "columns": ["value", "growth"]})

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
