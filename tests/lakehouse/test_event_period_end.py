"""Dated document stocks join calendars only at independently exact endpoints."""
import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class EventPeriodEndTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with duckdb.connect(str(root / "source.duckdb")) as db:
            db.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(root / "store")
        snapshot = self.store.publish_snapshot(root / "source.duckdb")
        self.store.create_workspace(snapshot["snapshot_id"], "event_test")
        self.docs = DocumentTools(self.store, "event_test")
        self.service = LakehouseService(self.store, "event_test")

    def publish(self, rows, frequency="event", kind="stock", scale=1000, **metadata):
        path = self.docs.upload_root / "observations.csv"
        unit_quote = {1: "TRY", 1000: "thousand TRY", 1000000: "million TRY"}[scale]
        path.write_text(f"date,value ({unit_quote})\n" + "\n".join(f"{date},{value}" for date, value in rows) + "\n")
        source = self.docs.register_upload(path)
        inspection = self.docs.inspect_source(source_id=source["source_id"])
        contract = {"name": "Synthetic dated stock", "frequency": frequency, "date_column": "date",
                    "key": ["date"], "grain": ["date"], "columns": {
                        "date": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                        "value": {"dtype": "integer", "unit": "TRY", "currency": "TRY", "scale": scale,
                                  "kind": kind, "nullable": True, **metadata}}}
        result = self.docs.publish_selected_table(source["source_id"], "table_001", contract,
            self.store.workspace("event_test")["version"],
            column_mapping=dict(zip(inspection["tables"][0]["columns"], contract["columns"])),
            unit_evidence={"value": unit_quote})
        self.assertEqual("ok", result.get("status"), result)
        return "overlay:" + result["dataset_id"] + ":value"

    def plan(self, metric, start="2026-03", end="2026-03", frequency="monthly", alignment="period_end"):
        return {"start": start, "end": end, "frequency": frequency,
                "columns": [{"name": "amount", "metric_id": metric, "alignment": alignment}]}

    def test_new_dated_stock_and_existing_calendar_share_one_exact_calculation(self):
        event = self.publish([("2026-03-31", 125000)])
        monthly = self.publish([("2026-03", 500)], frequency="monthly", scale=1000000)
        plan = self.plan(event)
        plan["columns"].append({"name": "sector", "metric_id": monthly})
        plan["operations"] = [{"op": "ratio", "column": "amount", "denominator": "sector", "output": "comparison",
                               "scope_policy": "explicit_comparison", "scope_reason": "Synthetic firm and sector populations are intentionally different."}]
        result = self.service.execute(plan)
        self.assertEqual({"period": "2026-03", "amount": 125000, "sector": 500, "comparison": 25.0}, result["preview"][0])
        _, manifest = self.store.load_analysis(result["analysis_id"])
        proof = manifest["lineage"]["sources"]["amount"]
        self.assertEqual("event", proof["binding"]["native_frequency"])
        self.assertEqual("period_end", proof["alignment"])
        self.assertEqual("2026-03-31", str(proof["cells"]["2026-03"][0]["native_period"])[:10])
        self.assertFalse(result["join_contract"]["population_equivalence_asserted"])
        self.assertTrue(any(note["code"] == "exact_event_period_end" for note in result["warnings"]))
        explained = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "amount", "period": "2026-03"})
        self.assertIn("2026-03-31", json.dumps(explained))

    def test_non_endpoint_in_requested_window_is_never_shifted_or_discarded(self):
        for rows in ([('2026-03-30', 10)], [('2026-03-30', 10), ('2026-03-31', 20)]):
            with self.subTest(rows=rows):
                metric = self.publish(rows)
                version = self.store.workspace("event_test")["version"]
                with self.assertRaises(PlanError) as error:
                    self.service.execute(self.plan(metric))
                self.assertEqual("PERIOD_END_MISMATCH", error.exception.code)
                self.assertEqual(version, self.store.workspace("event_test")["version"])

    def test_missing_months_remain_null_including_changes_and_ratios(self):
        event = self.publish([("2026-01-31", 100), ("2026-03-31", 160)])
        monthly = self.publish([("2026-01", 1000), ("2026-02", 1000), ("2026-03", 1000)], frequency="monthly")
        plan = self.plan(event, start="2026-01")
        plan["columns"].append({"name": "sector", "metric_id": monthly})
        plan["operations"] = [
            {"op": "difference", "column": "amount", "output": "change"},
            {"op": "ratio", "column": "amount", "denominator": "sector", "output": "comparison",
             "scope_policy": "explicit_comparison", "scope_reason": "Synthetic sources represent distinct populations."}]
        result = self.service.execute(plan)
        self.assertEqual([100, None, 160], [row["amount"] for row in result["preview"]])
        self.assertEqual([None, None, None], [row["change"] for row in result["preview"]])
        self.assertEqual([10, None, 16], [row["comparison"] for row in result["preview"]])

    def test_flows_unknown_semantics_and_unreviewed_stocks_cannot_be_relabelled(self):
        for kind, metadata in [("flow", {}), ("unknown", {}), ("stock", {"status": "review_required"})]:
            with self.subTest(kind=kind, metadata=metadata):
                metric = self.publish([("2026-03-31", 100)], kind=kind, **metadata)
                with self.assertRaises(PlanError) as error:
                    self.service.execute(self.plan(metric))
                self.assertIn(error.exception.code, {"INVALID_TEMPORAL_AGGREGATION", "SEMANTICS_REVIEW_REQUIRED"})

    def test_event_has_no_inferred_frequency_and_calendar_source_keeps_native_rule(self):
        event = self.publish([("2026-03-31", 100)])
        for alignment in ("native", "last", "mean", "sum"):
            with self.subTest(alignment=alignment):
                with self.assertRaises(PlanError):
                    self.service.execute(self.plan(event, alignment=alignment))
        monthly = self.publish([("2026-03", 100)], frequency="monthly")
        with self.assertRaises(PlanError):
            self.service.execute(self.plan(monthly))

    def test_quarter_and_year_are_exact_calendar_endpoints_not_fiscal_guesses(self):
        metric = self.publish([("2025-12-31", 2**53 + 1), ("2026-03-31", 150)])
        for start, frequency, expected in [("2025", "annual", 2**53 + 1), ("2026-Q1", "quarterly", 150), ("2026-03-31", "daily", 150)]:
            with self.subTest(frequency=frequency):
                result = self.service.execute(self.plan(metric, start=start, end=start, frequency=frequency))
                self.assertEqual(expected, result["preview"][0]["amount"])
        with self.assertRaises(PlanError) as error:
            self.service.execute(self.plan(metric, start="2026", end="2026", frequency="annual"))
        self.assertEqual("PERIOD_END_MISMATCH", error.exception.code)
