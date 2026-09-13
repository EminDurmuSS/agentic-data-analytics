"""Reference attachment preserves empty-domain isolation and immutable evidence."""
import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.context import AppContext
from agentic_analytics.agent.tools.reference_catalogues import ReferenceCatalogueTools
from agentic_analytics.lakehouse.service import LakehouseService, PlanError


class ReferenceCatalogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "reference.duckdb"
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE SCHEMA catalog")
            connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
            binding = {"metric_id": "fixture:reference", "title": "Reference assets", "source_system": "FIXTURE_REFERENCE",
                       "table": "observations", "time_column": "month", "value_column": "value", "filters": {},
                       "dimensions": {}, "native_frequency": "monthly", "kind": "stock", "unit": "TRY", "scale": 1000,
                       "currency": "TRY", "aggregation": "last", "source_base": "", "provenance_columns": [],
                       "status": "ready", "notes": [], "contract_version": "test"}
            connection.execute("INSERT INTO catalog.metric_bindings VALUES (?, ?)", [binding["metric_id"], json.dumps(binding)])
            connection.execute("CREATE TABLE observations(month VARCHAR,value DOUBLE)")
            connection.execute("INSERT INTO observations VALUES ('2026-03',100)")
        self.app = AppContext(self.root / "app", self.database, validate_finance=False)
        self.addCleanup(self.app.close)
        self.workspace = self.app.create_workspace("Empty domain", "generic")
        self.wid = self.workspace["workspace_id"]
        self.store = self.app.store
        self.service = LakehouseService(self.store, self.wid)

    def tools(self, workspace_id=None):
        return ReferenceCatalogueTools(self.store, workspace_id or self.wid, self.app.reference_catalogues())

    def imported_source(self):
        raw = b"month,amount\n2026-03,20\n"
        source = self.app.documents(self.wid)._register(raw, "uploaded.csv", "text/csv")
        path = self.root / "uploaded.csv"
        path.write_bytes(raw)
        contract = {"name": "User assets", "frequency": "monthly", "date_column": "month", "key": ["month"], "grain": ["month"],
                    "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                                "amount": {"dtype": "integer", "unit": "TRY", "currency": "TRY", "scale": 1000,
                                           "kind": "stock", "aggregation": "last", "nullable": False}}}
        workspace = self.store.ingest_csv(self.wid, path, contract, expected_version=self.store.workspace(self.wid)["version"])
        return source, workspace["datasets"][-1]

    def test_explicit_attachment_preserves_imported_bytes_old_revisions_and_analysis(self):
        initial = self.store.workspace(self.wid)
        references = self.tools()
        self.assertEqual(self.service.discover({"query": "assets"})["total"], 0)
        card = references.available_catalogues()[0]
        self.assertEqual(card["status"], "available")
        self.assertEqual(card["source_systems"], ["FIXTURE_REFERENCE"])
        self.assertEqual(self.store.workspace(self.wid), initial)
        source, dataset_id = self.imported_source()
        overlay_metric = self.service.discover({"query": "amount"})["metrics"][0]["metric_id"]
        plan = {"start": "2026-03", "end": "2026-03", "frequency": "monthly",
                "columns": [{"name": "uploaded", "metric_id": overlay_metric, "dimensions": {}}]}
        prior = self.service.execute(plan)
        prior_frame, prior_analysis = self.store.load_analysis(prior["analysis_id"])
        before = self.store.workspace(self.wid)
        overlay_bytes = self.store.overlay_path(dataset_id).read_bytes()
        snapshot_bytes = self.store.snapshot_path(before["snapshot_id"]).read_bytes()
        result = references.extra_tools()["attach_reference_catalogue"]["handler"]({"catalogue_id": "finance", "expected_version": before["version"]})
        self.assertEqual(result["status"], "ok", result)
        current = self.store.workspace(self.wid)
        self.assertEqual(current["version"], before["version"] + 1)
        self.assertEqual(current["analysis_head"], before["analysis_head"])
        self.assertEqual(current["datasets"], before["datasets"])
        self.assertEqual(self.store.workspace(self.wid, before["revision_id"]), before)
        self.assertEqual(self.store.snapshot_path(before["snapshot_id"]).read_bytes(), snapshot_bytes)
        self.assertEqual(self.store.overlay_path(dataset_id).read_bytes(), overlay_bytes)
        self.assertEqual(self.app.documents(self.wid).raw_source_bytes(source["source_id"]), b"month,amount\n2026-03,20\n")
        old_frame, old_analysis = self.store.load_analysis(prior["analysis_id"])
        self.assertEqual(old_analysis, prior_analysis)
        self.assertTrue(old_frame.equals(prior_frame))
        self.assertEqual(references.available_catalogues()[0]["status"], "attached")
        self.assertEqual(self.service.discover({"query": "Reference assets"})["total"], 1)
        self.assertEqual(result["preserved_analysis_id"], prior["analysis_id"])
        self.assertIn("reuse its saved plan's existing columns, periods, alignment, operations and scope requirements", result["next_step"])
        self.assertIn("call execute", result["next_step"])
        self.assertIn("Do not use revise_analysis across snapshots", result["next_step"])
        with self.assertRaises(PlanError) as denied:
            self.service.revise_analysis({"analysis_id": prior["analysis_id"], "add_columns": [
                {"name": "reference", "metric_id": "fixture:reference", "dimensions": {}}]})
        self.assertEqual(denied.exception.code, "INVALID_PLAN")
        self.assertEqual(self.store.workspace(self.wid), current)
        compared = self.service.execute({**plan, "columns": [*plan["columns"],
            {"name": "reference", "metric_id": "fixture:reference", "dimensions": {}}]})
        frame, analysis = self.store.load_analysis(compared["analysis_id"])
        self.assertEqual(frame[["uploaded", "reference"]].to_dict("records"), [{"uploaded": 20.0, "reference": 100.0}])
        self.assertEqual(analysis["snapshot_id"], card["snapshot_id"])
        self.assertEqual(analysis["plan"]["columns"][:-1], prior_analysis["plan"]["columns"])
        self.assertEqual({key: analysis["plan"][key] for key in ("start", "end", "frequency")},
                         {key: prior_analysis["plan"][key] for key in ("start", "end", "frequency")})
        self.assertTrue(self.store.load_analysis(prior["analysis_id"])[0].equals(prior_frame))

    def test_stale_attachment_does_not_write_and_exact_committed_receipt_recovers(self):
        references = self.tools()
        handler = references.extra_tools()["attach_reference_catalogue"]["handler"]
        self.imported_source()
        before = self.store.workspace(self.wid)
        stale = handler({"catalogue_id": "finance", "expected_version": 0})
        self.assertEqual(stale["code"], "VERSION_CONFLICT", stale)
        self.assertEqual(self.store.workspace(self.wid), before)
        args = {"catalogue_id": "finance", "expected_version": before["version"]}
        attached = handler(args)
        self.assertEqual(attached["status"], "ok", attached)
        after = self.store.workspace(self.wid)
        recovered = references.recover(args, {"input_revision": before["revision_id"]})
        self.assertEqual(recovered["status"], "ok", recovered)
        self.assertTrue(recovered["recovered"])
        self.assertIsNone(recovered["preserved_analysis_id"])
        self.assertNotIn("reuse its saved plan", recovered["next_step"])
        self.assertEqual(handler(args)["code"], "VERSION_CONFLICT")
        again = handler({**args, "expected_version": after["version"]})
        self.assertFalse(again["workspace_changed"])
        self.assertEqual(self.store.workspace(self.wid), after)
        self.assertEqual(handler({**args, "catalogue_id": "../foreign"})["code"], "INVALID_ARGUMENTS")

    def test_existing_catalogue_cannot_be_replaced_by_a_new_release(self):
        workspace = self.app.create_workspace("Pinned catalogue", "finance")
        before = self.store.workspace(workspace["workspace_id"])
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("INSERT INTO observations VALUES ('2026-04',120)")
        references = self.tools(workspace["workspace_id"])
        self.assertEqual(references.available_catalogues()[0]["status"], "unavailable")
        result = references.extra_tools()["attach_reference_catalogue"]["handler"]({"catalogue_id": "finance", "expected_version": 0})
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(self.store.workspace(workspace["workspace_id"]), before)

    def test_missing_or_unreviewed_reference_data_stays_unavailable(self):
        self.app.source_db = None
        references = self.tools()
        before = self.store.workspace(self.wid)
        self.assertEqual(references.available_catalogues()[0]["status"], "unavailable")
        result = references.extra_tools()["attach_reference_catalogue"]["handler"]({"catalogue_id": "finance", "expected_version": 0})
        self.assertEqual(result["code"], "REFERENCE_CATALOGUE_UNAVAILABLE")
        self.assertEqual(self.store.workspace(self.wid), before)
        self.app.source_db = self.database
        with duckdb.connect(str(self.database)) as connection:
            binding = json.loads(connection.execute("SELECT binding_json FROM catalog.metric_bindings").fetchone()[0])
            binding["status"] = "review_required"
            connection.execute("UPDATE catalog.metric_bindings SET binding_json = ?", [json.dumps(binding)])
        card = self.tools().available_catalogues()[0]
        self.assertEqual(card["status"], "unavailable")
        self.assertEqual(card["ready_metric_count"], 0)
        self.assertEqual(self.store.workspace(self.wid), before)
