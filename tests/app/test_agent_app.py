"""HTTP integration contracts using scripted provider messages, never live keys."""
import copy
import csv
import io
import json
import shutil
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import duckdb
from fastapi.testclient import TestClient
import pandas as pd

from app.server import create_app


def tool(name, arguments, call_id="call-1"):
    return {"role": "assistant", "content": None, "finish_reason": "stop", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


FINAL = {"role": "assistant", "content": "Sonuç hazır.", "finish_reason": "stop", "tool_calls": []}


class ScriptedProvider:
    def __init__(self):
        self.responses = []
        self.messages = []
        self.image_responses = []
        self.image_requests = []

    def chat(self, messages, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        if not self.responses:
            raise AssertionError("An unexpected provider call was attempted")
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response

    def image_chat(self, image_bytes, mime_type, **kwargs):
        self.image_requests.append({"bytes": image_bytes, "mime_type": mime_type, **kwargs})
        if not self.image_responses:
            raise AssertionError("Unexpected image model call")
        return self.image_responses.pop(0)


class AgentAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.fixture_db = Path(cls.fixture_temp.name) / "finance.duckdb"
        with duckdb.connect(str(cls.fixture_db)) as db:
            db.execute("CREATE SCHEMA catalog")
            db.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
            binding = {"metric_id": "test:credit", "title": "Test finance credit", "source_system": "APP_TEST",
                       "table": "observations", "time_column": "month", "value_column": "credit", "filters": {},
                       "dimensions": {}, "native_frequency": "monthly", "kind": "stock", "unit": "TRY", "scale": 1,
                       "currency": "TRY", "aggregation": "last", "source_base": "", "provenance_columns": [],
                       "status": "ready", "notes": [], "contract_version": "test"}
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [binding["metric_id"], json.dumps(binding)])
            db.execute("CREATE TABLE observations(month VARCHAR,credit DOUBLE)")
            db.execute("INSERT INTO observations VALUES ('2021-01',100),('2021-02',120),('2021-03',150)")
            regional = {**binding, "metric_id": "test:regional", "title": "Regional deposits", "table": "regional",
                        "value_column": "value", "dimensions": {"city": "city"}}
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [regional["metric_id"], json.dumps(regional)])
            db.execute("CREATE TABLE regional(month VARCHAR,city VARCHAR,value DOUBLE)")
            db.execute("INSERT INTO regional VALUES ('2021-01','A',10),('2021-01','B',20)")

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = ScriptedProvider()
        self.app = create_app(runtime_root=Path(self.temp.name), source_db=self.fixture_db,
                              client=self.provider, validate_finance=False, searxng_url=False)
        self.client = TestClient(self.app)
        self.context = self.app.state.context
        self.client.__enter__()
        self.plan = {"start": "2021-01", "end": "2021-03", "frequency": "monthly",
                     "columns": [{"name": "credit", "metric_id": "test:credit", "dimensions": {}}]}

    def tearDown(self):
        self.context.pool.shutdown(wait=True)
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def workspace(self, profile="finance"):
        response = self.client.post("/api/workspaces", json={"name": "Test workspace", "profile": profile})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_new_workspace_uses_new_database_release_and_old_workspace_keeps_snapshot(self):
        source = Path(self.temp.name) / "source.duckdb"
        shutil.copyfile(self.fixture_db, source)
        self.context.source_db = source
        first = self.workspace()
        unchanged = self.workspace()
        self.assertEqual(first["snapshot_id"], unchanged["snapshot_id"])
        replacement = source.with_name("replacement.duckdb")
        shutil.copyfile(source, replacement)
        with duckdb.connect(str(replacement)) as connection:
            connection.execute("INSERT INTO observations VALUES ('2021-04',175)")
        replacement.replace(source)
        second = self.workspace()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["snapshot_id"], self.context.workspace(first["workspace_id"])["snapshot_id"])
        for workspace, expected_rows in [(first, 3), (second, 4)]:
            with duckdb.connect(str(self.context.store.snapshot_path(workspace["snapshot_id"])), read_only=True) as connection:
                self.assertEqual(expected_rows, connection.execute("SELECT count(*) FROM observations").fetchone()[0])

    def submit_and_wait(self, workspace_id, message="Kredi tablosu", **fields):
        response = self.client.post(f"/api/workspaces/{workspace_id}/runs", json={"message": message, **fields})
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()["job_id"]
        self.context.futures[job_id].result(timeout=10)
        job = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(job.status_code, 200, job.text)
        return job.json()

    def analysis(self, workspace_id):
        self.provider.responses.extend([tool("execute", self.plan), FINAL])
        job = self.submit_and_wait(workspace_id)
        self.assertEqual(job["result"]["status"], "completed", job)
        return job["result"]["analysis_id"]

    def test_finance_and_generic_workspaces_keep_separate_catalogs_and_state(self):
        finance, generic = self.workspace(), self.workspace("generic")
        self.assertNotEqual(finance["snapshot_id"], generic["snapshot_id"])
        available = self.client.get(f"/api/workspaces/{finance['workspace_id']}/discover", params={"query": "credit"})
        empty = self.client.get(f"/api/workspaces/{generic['workspace_id']}/discover", params={"query": "credit"})
        self.assertEqual(available.status_code, 200)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(available.json()["total"], 1)
        self.assertEqual(empty.json()["total"], 0)
        analysis_id = self.analysis(finance["workspace_id"])
        untouched = self.client.get(f"/api/workspaces/{generic['workspace_id']}").json()
        self.assertEqual(untouched["version"], 0)
        self.assertIsNone(untouched["analysis_head"])
        self.assertEqual(untouched["runs"], [])
        self.assertNotIn(analysis_id, json.dumps(untouched))

    def test_run_submit_poll_and_request_id_replay_preserve_single_write(self):
        workspace = self.workspace()
        self.provider.responses.extend([tool("execute", self.plan), FINAL])
        job = self.submit_and_wait(workspace["workspace_id"], request_id="same-request")
        result = job["result"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["analysis_updated"])
        self.assertEqual(job["run"]["run_id"], result["run_id"])
        self.assertTrue(any(event["kind"] == "tool_result" for event in job["activity"]))
        self.assertEqual(job["activity_count"], len(job["activity"]))
        self.assertEqual(job["journey"]["status"], "completed")
        self.assertEqual(job["journey"]["event_count"], len(job["activity"]))
        restored = self.client.get(f"/api/workspaces/{workspace['workspace_id']}").json()
        self.assertEqual(restored["latest_journey"], job["journey"])
        replay = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs",
                                  json={"message": "Kredi tablosu", "request_id": "same-request"})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["job_id"], job["job_id"])
        self.assertEqual(replay.json()["result"], result)
        self.assertEqual(len(self.provider.messages), 2)
        self.assertEqual(self.context.store.workspace(workspace["workspace_id"])["version"], 1)
        conflict = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs",
                                    json={"message": "Başka soru", "request_id": "same-request"})
        self.assertEqual(conflict.status_code, 409)

    def test_polling_is_available_during_provider_work(self):
        workspace = self.workspace()
        entered, release = threading.Event(), threading.Event()
        def delayed(messages):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the provider")
            return FINAL
        self.provider.responses.append(delayed)
        submitted = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs", json={"message": "Merhaba"}).json()
        try:
            self.assertTrue(entered.wait(5))
            polled = self.client.get(f"/api/jobs/{submitted['job_id']}").json()
            self.assertEqual(polled["run"]["status"], "running")
            self.assertTrue(any(event["kind"] == "model_request" for event in polled["activity"]))
            self.assertEqual(polled["activity_count"], len(polled["activity"]))
            self.assertEqual(polled["journey"]["status"], "running")
            self.assertEqual(polled["journey"]["stages"], [])
        finally:
            release.set()
        self.context.futures[submitted["job_id"]].result(timeout=5)

    def test_failed_worker_job_resumes_same_committed_analysis(self):
        workspace = self.workspace()
        self.provider.responses.append(tool("execute", self.plan))
        with patch.object(self.context.run_store, "complete_step", side_effect=RuntimeError("simulated worker interruption")):
            job = self.submit_and_wait(workspace["workspace_id"], request_id="failed-worker")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["run"]["status"], "running")
        committed = self.context.store.workspace(workspace["workspace_id"])
        self.assertEqual(committed["version"], 1)
        self.provider.responses.append(FINAL)
        resumed = self.client.post(f"/api/jobs/{job['job_id']}/resume")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.context.futures[job["job_id"]].result(timeout=10)
        finished = self.client.get(f"/api/jobs/{job['job_id']}").json()
        self.assertEqual(finished["result"]["status"], "completed")
        self.assertEqual(finished["result"]["run_id"], job["run"]["run_id"])
        self.assertEqual(finished["result"]["analysis_id"], committed["analysis_head"])
        self.assertEqual(self.context.store.workspace(workspace["workspace_id"])["version"], 1)

    def test_restarted_app_detects_interrupted_job_and_recovers_from_tool_intent(self):
        class SimulatedProcessExit(BaseException):
            pass
        workspace = self.workspace()
        self.provider.responses.append(tool("execute", self.plan))
        with patch.object(self.context.run_store, "complete_step", side_effect=SimulatedProcessExit()):
            response = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs",
                                        json={"message": "Kaydet", "request_id": "interrupted-worker"})
            job_id = response.json()["job_id"]
            with self.assertRaises(SimulatedProcessExit):
                self.context.futures[job_id].result(timeout=10)
        committed = self.context.store.workspace(workspace["workspace_id"])
        self.context.pool.shutdown(wait=True)
        self.client.__exit__(None, None, None)
        self.provider.responses.append(FINAL)
        self.app = create_app(runtime_root=Path(self.temp.name), source_db=self.fixture_db,
                              client=self.provider, validate_finance=False, searxng_url=False)
        self.context = self.app.state.context
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").json()["status"], "interrupted")
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/resume").status_code, 200)
        self.context.futures[job_id].result(timeout=10)
        final = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(final["result"]["status"], "completed")
        self.assertEqual(final["result"]["analysis_id"], committed["analysis_head"])
        self.assertEqual(self.context.store.workspace(workspace["workspace_id"])["version"], 1)
        self.assertTrue(any(event["kind"] == "tool_recovered" for event in final["activity"]))

    def test_upload_csv_inspection_preserves_raw_hash_and_workspace_ownership(self):
        own, other = self.workspace("generic"), self.workspace("generic")
        content = b"month,visits (persons)\n2026-01,100\n2026-02,120\n"
        upload = self.client.post(f"/api/workspaces/{own['workspace_id']}/sources/upload",
                                  files={"file": ("clinic.csv", content, "text/csv")})
        self.assertEqual(upload.status_code, 200, upload.text)
        source_id = upload.json()["source_id"]
        inspected = self.client.get(f"/api/workspaces/{own['workspace_id']}/sources/{source_id}")
        self.assertEqual(inspected.status_code, 200, inspected.text)
        import hashlib
        self.assertEqual(inspected.json()["raw_sha256"], hashlib.sha256(content).hexdigest())
        table = inspected.json()["tables"][0]
        self.assertEqual(table["row_count"], 2)
        self.assertEqual(table["preview"][0]["visits_persons"], "100")
        self.assertEqual(self.context.store.workspace(own["workspace_id"])["datasets"], [])
        denied = self.client.get(f"/api/workspaces/{other['workspace_id']}/sources/{source_id}")
        self.assertIn(denied.status_code, {400, 404})
        self.assertNotIn("100", denied.text)
        source_list = self.client.get(f"/api/workspaces/{own['workspace_id']}/sources")
        self.assertEqual(source_list.status_code, 200)
        self.assertIn(source_id, source_list.text)

    def test_uploaded_source_is_inspected_by_the_same_agent_tool_loop(self):
        workspace = self.workspace("generic")
        upload = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/sources/upload",
                                  files={"file": ("visits.csv", b"month,visits\n2026-01,12\n", "text/csv")}).json()
        self.provider.responses.extend([tool("inspect_source", {"source_id": upload["source_id"]}), FINAL])
        job = self.submit_and_wait(workspace["workspace_id"], message="Yüklediğim tabloyu incele")
        self.assertEqual(job["result"]["status"], "completed", job)
        self.assertTrue(any(item["id"] == upload["source_id"] for item in job["result"]["artifacts"]))
        self.assertEqual(job["result"]["tool_results"][0]["result"]["tables"][0]["preview"][0]["visits"], "12")

    def test_explicit_source_cell_review_preserves_raw_source_and_is_not_a_model_tool(self):
        workspace = self.workspace("generic")
        base = f"/api/workspaces/{workspace['workspace_id']}/sources"
        source = self.client.post(base + "/upload", files={"file": ("visits.csv", b"month,visits\n2026-01,12\n", "text/csv")}).json()
        before = self.client.get(base + "/" + source["source_id"]).json()
        reviewed = self.client.post(base + "/" + source["source_id"] + "/review", json={
            "table_id": "table_001", "reviewed_rows": [{"month": "2026-01", "visits": "13"}],
            "unit_evidence": {"visits": "Kullanıcı beyanı: visits birimi ziyaret adedidir."}})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        after = self.client.get(base + "/" + source["source_id"]).json()
        self.assertEqual(before["raw_sha256"], after["raw_sha256"])
        self.assertEqual(after["tables"][0]["preview"][0]["visits"], "13")
        self.assertEqual(after["tables"][0]["review"]["verification"], "explicit_user_cell_and_unit_review")
        tool_names = self.context.runtime(workspace["workspace_id"]).tools
        self.assertNotIn("review_table", tool_names)
        invalid = self.client.post(base + "/" + source["source_id"] + "/review", json={
            "table_id": "table_001", "reviewed_rows": [{"unknown": "13"}], "unit_evidence": {}})
        self.assertEqual(invalid.status_code, 400)

    def test_image_source_raw_download_and_explicit_cell_unit_review(self):
        from PIL import Image
        own, other = self.workspace("generic"), self.workspace("generic")
        buffer = io.BytesIO()
        Image.new("RGB", (128, 128), "white").save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        # This checks transport/review integration, not visual model accuracy.
        extracted = {"text": "month visits (persons) 2026-01 12", "tables": [{
            "columns": ["month", "visits"], "rows": [["2026-01", "12"]], "units": {"visits": "persons"}}]}
        self.provider.image_responses.append({"content": json.dumps(extracted), "finish_reason": "stop"})
        base = f"/api/workspaces/{own['workspace_id']}/sources"
        upload = self.client.post(base + "/upload", files={"file": ("clinic.png", image_bytes, "image/png")})
        self.assertEqual(upload.status_code, 200, upload.text)
        source_id = upload.json()["source_id"]
        inspection = self.client.get(base + "/" + source_id)
        self.assertEqual(inspection.status_code, 200, inspection.text)
        self.assertEqual(inspection.json()["tables"][0]["origin"], "ocr")
        self.assertTrue(any(w["code"] == "OCR_REVIEW_REQUIRED" for w in inspection.json()["warnings"]))
        self.assertEqual(len(self.provider.image_requests), 1)
        self.assertEqual(self.provider.image_requests[0]["bytes"], image_bytes)
        self.assertEqual(self.provider.image_requests[0]["response_format"]["type"], "json_schema")
        raw = self.client.get(base + "/" + source_id + "/raw")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.content, image_bytes)
        self.assertIn("attachment", raw.headers["Content-Disposition"])
        denied = self.client.get(f"/api/workspaces/{other['workspace_id']}/sources/{source_id}/raw")
        self.assertIn(denied.status_code, {400, 404})
        reviewed = self.client.post(base + "/" + source_id + "/review", json={
            "table_id": "table_001", "reviewed_rows": [{"month": "2026-01", "visits": "12"}], "unit_evidence": {"visits": "persons"}})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        inspected_again = self.client.get(base + "/" + source_id).json()
        self.assertEqual(inspected_again["tables"][0]["review"]["verification"], "explicit_user_cell_and_unit_review")
        self.assertEqual(len(self.provider.image_requests), 1)

    def test_analysis_pagination_csv_and_explanation_enforce_ownership(self):
        own, other = self.workspace(), self.workspace()
        analysis_id = self.analysis(own["workspace_id"])
        base = f"/api/workspaces/{own['workspace_id']}/analyses/{analysis_id}"
        page = self.client.get(base, params={"offset": 1, "limit": 1})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["row_count"], 3)
        self.assertEqual(page.json()["rows"], [{"period": "2021-02", "credit": 120.0}])
        csv_response = self.client.get(base + "/csv")
        self.assertEqual(csv_response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 3)
        self.assertEqual(float(rows[-1]["credit"]), 150)
        proof = self.client.get(base + "/explain", params={"column": "credit", "period": "2021-02"})
        self.assertEqual(proof.status_code, 200)
        self.assertEqual(proof.json()["value"], 120)
        self.assertFalse(proof.json()["source_files_verified"])
        self.assertFalse(proof.json()["source_references_complete"])
        wrong_base = f"/api/workspaces/{other['workspace_id']}/analyses/{analysis_id}"
        for suffix, params in [("", {}), ("/csv", {}), ("/explain", {"column": "credit", "period": "2021-02"})]:
            denied = self.client.get(wrong_base + suffix, params=params)
            self.assertIn(denied.status_code, {400, 404})
            self.assertNotIn('"value":120', denied.text)
        self.assertEqual(self.client.get(base, params={"offset": -1}).status_code, 400)
        self.assertEqual(self.client.get(base, params={"limit": 2001}).status_code, 400)

    def test_csv_export_neutralizes_formula_strings_without_changing_negative_numbers(self):
        workspace = self.workspace()
        frame = pd.DataFrame({"period": ["2021-01"], "label": ["=1+1"], "value": [-12.5]})
        manifest = self.context.store.save_analysis(workspace["workspace_id"], frame, {"kind": "export_fixture"}, {}, expected_version=0)
        response = self.client.get(f"/api/workspaces/{workspace['workspace_id']}/analyses/{manifest['analysis_id']}/csv")
        self.assertEqual(response.status_code, 200)
        row = next(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(row["label"], "'=1+1")
        self.assertEqual(float(row["value"]), -12.5)

    def test_grouped_result_explanation_requires_explicit_dimension_json(self):
        workspace = self.workspace()
        request = {"metric_id": "test:regional", "group_by": "city", "dimensions": {},
                   "start": "2021-01", "end": "2021-01", "frequency": "monthly", "limit": 2}
        self.provider.responses.extend([tool("query_grouped", request), FINAL])
        result = self.submit_and_wait(workspace["workspace_id"], message="Şehirleri sırala")["result"]
        self.assertEqual(result["status"], "completed", result)
        base = f"/api/workspaces/{workspace['workspace_id']}/analyses/{result['analysis_id']}/explain"
        args = {"column": "value", "period": "2021-01"}
        self.assertEqual(self.client.get(base, params=args).status_code, 400)
        self.assertEqual(self.client.get(base, params={**args, "dimensions": "[]"}).status_code, 400)
        proof = self.client.get(base, params={**args, "dimensions": json.dumps({"city": "B"})})
        self.assertEqual(proof.status_code, 200, proof.text)
        self.assertEqual(proof.json()["value"], 20)

    def test_origin_host_and_request_schema_restrictions(self):
        denied = self.client.post("/api/workspaces", json={"profile": "generic"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.client.get("/api/workspaces").json()["workspaces"], [])
        accepted = self.client.post("/api/workspaces", json={"profile": "generic"}, headers={"Origin": "http://testserver"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.get("/api/status", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/workspaces", json={"profile": "generic", "sql": "select 1"}).status_code, 422)
        oversized = self.client.post("/api/workspaces", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": str(33 * 1024 * 1024)})
        self.assertEqual(oversized.status_code, 413)
        self.assertIn("frame-ancestors 'none'", self.client.get("/api/status").headers["Content-Security-Policy"])

    def test_missing_provider_is_explicit_and_never_enters_fake_mode(self):
        workspace = self.workspace()
        self.context.client = None
        self.assertFalse(self.client.get("/api/status").json()["provider_ready"])
        response = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs", json={"message": "Hesapla"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.provider.messages, [])
        self.assertEqual(self.context.run_store.list(workspace["workspace_id"]), [])


if __name__ == "__main__":
    unittest.main()
