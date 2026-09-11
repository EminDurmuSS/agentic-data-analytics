"""Chart HTTP and agent workflows with synthetic data and a scripted provider."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
from fastapi.testclient import TestClient

from app.server import create_app


def tool(name, arguments, call_id="chart-call"):
    return {"role": "assistant", "content": None, "finish_reason": "tool_calls",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def final(message="İstenen görünüm kaydedildi."):
    return {"role": "assistant", "content": message, "finish_reason": "stop", "tool_calls": []}


class ScriptedProvider:
    def __init__(self):
        self.responses = []
        self.messages = []
        self.options = []

    def chat(self, messages, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        self.options.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected provider request; this test must remain offline")
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


class AgentChartWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory(prefix="chart-http-fixture-")
        cls.fixture_db = Path(cls.fixture_temp.name) / "fixture.duckdb"
        with duckdb.connect(str(cls.fixture_db)) as connection:
            connection.execute("CREATE SCHEMA catalog")
            connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
            connection.execute("CREATE TABLE observations(month VARCHAR,credit DOUBLE,rate DOUBLE)")
            for name, kind, unit, currency in [("credit", "stock", "TRY", "TRY"),
                                                ("rate", "rate", "percent", None)]:
                binding = {"metric_id": "chart_test:" + name, "title": "Synthetic " + name,
                           "source_system": "CHART_TEST", "table": "observations",
                           "time_column": "month", "value_column": name, "filters": {},
                           "dimensions": {}, "native_frequency": "monthly", "kind": kind,
                           "unit": unit, "scale": 1, "currency": currency, "aggregation": "last",
                           "source_base": "", "provenance_columns": [], "status": "ready",
                           "notes": [], "contract_version": "chart-test"}
                connection.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)",
                                   [binding["metric_id"], json.dumps(binding)])
            cls.expected_rows = [{"period": f"{2023 + i // 12}-{i % 12 + 1:02d}",
                                  "credit": float(100 + i * 10), "rate": 10 + i / 10}
                                 for i in range(36)]
            connection.executemany("INSERT INTO observations VALUES (?,?,?)",
                                   [[r["period"], r["credit"], r["rate"]] for r in cls.expected_rows])

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chart-http-runtime-")
        self.provider = ScriptedProvider()
        self.start_app()
        self.plan = {"start": "2023-01", "end": "2025-12", "frequency": "monthly",
                     "columns": [{"name": name, "metric_id": "chart_test:" + name}
                                 for name in ("credit", "rate")]}

    def start_app(self):
        self.app = create_app(runtime_root=Path(self.temp.name), source_db=self.fixture_db,
                              client=self.provider, validate_finance=False, searxng_url=False)
        self.context = self.app.state.context
        self.client = TestClient(self.app)
        self.client.__enter__()

    def stop_app(self):
        self.context.pool.shutdown(wait=True)
        self.client.__exit__(None, None, None)

    def tearDown(self):
        self.stop_app()
        self.temp.cleanup()

    def workspace(self):
        response = self.client.post("/api/workspaces", json={"name": "Synthetic chart workflow", "profile": "finance"})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["workspace_id"]

    def submit(self, workspace_id, message, **fields):
        response = self.client.post(f"/api/workspaces/{workspace_id}/runs", json={"message": message, **fields})
        self.assertEqual(200, response.status_code, response.text)
        job_id = response.json()["job_id"]
        self.context.futures[job_id].result(timeout=15)
        response = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def seed_analysis(self):
        workspace_id = self.workspace()
        self.provider.responses.extend([tool("execute", self.plan, "seed-analysis"), final("Aylık tablo kaydedildi.")])
        job = self.submit(workspace_id, "Aylık kredi ve faiz tablosunu kaydet.")
        self.assertEqual("completed", job["result"]["status"], job)
        return workspace_id, job["result"]["analysis_id"], job["result"]["conversation_id"]

    def analysis(self, workspace_id, analysis_id):
        response = self.client.get(f"/api/workspaces/{workspace_id}/analyses/{analysis_id}")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def chart(self, workspace_id, analysis_id, body=None):
        url = f"/api/workspaces/{workspace_id}/analyses/{analysis_id}/chart"
        response = self.client.get(url) if body is None else self.client.post(url, json=body)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def assert_analysis_unchanged(self, workspace_id, analysis_id, before_workspace, before_analysis):
        workspace = self.context.store.workspace(workspace_id)
        self.assertEqual(before_workspace["analysis_head"], workspace["analysis_head"])
        self.assertEqual(before_workspace["version"], workspace["version"])
        self.assertEqual(before_workspace["revision_id"], workspace["revision_id"])
        self.assertEqual(before_analysis, self.analysis(workspace_id, analysis_id))
        self.assertEqual(self.expected_rows, before_analysis["rows"])

    def test_default_chart_is_read_only_and_contains_all_saved_periods(self):
        wid, aid, _ = self.seed_analysis()
        before = self.context.store.workspace(wid)
        analysis = self.analysis(wid, aid)
        first, second = self.chart(wid, aid), self.chart(wid, aid)
        self.assertEqual(first, second)
        self.assertIsNone(first.get("chart_id"))
        self.assertEqual(aid, first["analysis_id"])
        self.assertEqual(36, first["row_count"])
        self.assertTrue(first["complete"])
        self.assertEqual([r["period"] for r in self.expected_rows], first["periods"])
        series = {item["column"]: item for item in first["series"]}
        self.assertEqual([r["credit"] for r in self.expected_rows], series["credit"]["raw_values"])
        self.assertEqual([r["rate"] for r in self.expected_rows], series["rate"]["raw_values"])
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_chart_api_creates_all_visual_kinds_without_changing_analysis_values(self):
        wid, aid, _ = self.seed_analysis()
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        requests = [
            {"kind": "line", "columns": ["credit"]},
            {"kind": "bar", "columns": ["credit"]},
            {"kind": "area", "columns": ["credit"]},
            {"kind": "scatter", "x": "rate", "columns": ["credit"]},
            {"kind": "heatmap", "columns": ["credit", "rate"]},
        ]
        for request in requests:
            with self.subTest(kind=request["kind"]):
                chart = self.chart(wid, aid, request)
                self.assertEqual(request["kind"], chart["spec"]["kind"])
                self.assertTrue(chart["complete"])
                self.assertEqual(self.expected_rows[0]["credit"], chart["series"][0]["raw_values"][0])
        heatmap = self.chart(wid, aid)
        self.assertEqual(72, len(heatmap["cells"]))
        self.assertIn("Sınırlı görsel", heatmap["presentation_notice"])
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_chart_client_keeps_all_kind_buttons_available_with_limited_visual_notice(self):
        client = (Path(__file__).parents[2] / "app" / "static" / "charts.js").read_text(encoding="utf-8")
        self.assertIn('button.dataset.unavailable = "false"', client)
        self.assertIn("payload.presentation_notice", client)

    def test_chart_api_saves_immutable_views_and_restores_latest_after_restart(self):
        wid, aid, _ = self.seed_analysis()
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        first = self.chart(wid, aid, {"kind": "bar", "columns": ["credit"], "orientation": "horizontal", "title": "Kredi görünümü"})
        self.assertTrue(first.get("chart_id"), first)
        saved_first = self.client.get(f"/api/workspaces/{wid}/charts/{first['chart_id']}")
        self.assertEqual(200, saved_first.status_code, saved_first.text)
        first_payload = saved_first.json()
        second = self.chart(wid, aid, {"kind": "area", "columns": ["credit"], "title": "İkinci görünüm"})
        self.assertNotEqual(first["chart_id"], second["chart_id"])
        self.assertEqual(first_payload, self.client.get(f"/api/workspaces/{wid}/charts/{first['chart_id']}").json())
        self.assertEqual(second["chart_id"], self.chart(wid, aid)["chart_id"])
        self.stop_app()
        self.start_app()
        restored = self.chart(wid, aid)
        self.assertEqual(second["chart_id"], restored["chart_id"])
        self.assertEqual("area", restored["spec"]["kind"])
        self.assertEqual(["credit"], restored["spec"]["columns"])
        self.assertEqual(first_payload, self.client.get(f"/api/workspaces/{wid}/charts/{first['chart_id']}").json())
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_chart_only_agent_followup_uses_saved_analysis_and_compact_result(self):
        wid, aid, conversation = self.seed_analysis()
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        self.provider.messages.clear()
        self.provider.options.clear()
        self.provider.responses.extend([
            tool("create_chart", {"analysis_id": aid, "kind": "bar", "columns": ["credit"],
                                  "title": "Aylık kredi"}),
            final("Kredi sütunu çubuk grafikte gösterildi; kayıtlı tablo korunuyor."),
        ])
        job = self.submit(wid, "Bu tablodaki kredi sütununu çubuk grafikte göster.", conversation_id=conversation)
        result = job["result"]
        self.assertEqual("completed", result["status"], job)
        self.assertFalse(result["analysis_updated"])
        steps = result["tool_results"]
        self.assertEqual(["create_chart"], [step["tool"] for step in steps])
        chart_id = steps[0]["result"]["chart_id"]
        self.assertIn(chart_id, {artifact["id"] for artifact in result["artifacts"]})
        self.assertEqual(chart_id, self.chart(wid, aid)["chart_id"])
        offered = {definition["function"]["name"] for definition in self.provider.options[0]["tools"]}
        self.assertIn("create_chart", offered)
        model_result = json.loads(next(message["content"] for message in self.provider.messages[-1]
                                      if message.get("role") == "tool" and message.get("tool_call_id") == "chart-call"))
        self.assertEqual(chart_id, model_result["chart_id"])
        self.assertIn("spec", model_result)
        self.assertIn("recommendations", model_result)
        self.assertNotIn("series", model_result)
        self.assertNotIn("rows", model_result)
        self.assertLess(len(json.dumps(model_result)), 12000)
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_saved_chart_spec_is_available_for_followup_in_a_new_conversation(self):
        wid, aid, _ = self.seed_analysis()
        saved = self.chart(wid, aid, {"kind": "bar", "columns": ["credit"], "orientation": "horizontal", "title": "Hatırlanan görünüm"})
        self.provider.messages.clear()
        self.provider.responses.extend([tool("create_chart", {"analysis_id": aid, "kind": "area", "columns": ["credit"]}),
                                        final("Kayıtlı görünüm alan grafiği olarak değiştirildi.")])
        job = self.submit(wid, "Mevcut grafiği alan grafiğine çevir.")
        self.assertEqual("completed", job["result"]["status"], job)
        context = self.provider.messages[0][0]["content"]
        self.assertIn(saved["chart_id"], context)
        self.assertIn("Hatırlanan görünüm", context)
        self.assertIn("horizontal", context)
        self.assertIn('"bar"', context)
        self.assertEqual("area", self.chart(wid, aid)["spec"]["kind"])

    def test_chart_only_confirmation_uses_saved_view_instead_of_invented_numeric_claim(self):
        wid, aid, conversation = self.seed_analysis()
        self.provider.responses.extend([
            tool("create_chart", {"analysis_id": aid, "kind": "line", "columns": ["credit"], "title": "Aylık kredi"}),
            final("Kredi 999999 TL'den başladı ve yüzde 8123 büyüdü."),
        ])
        job = self.submit(wid, "Kredi için çizgi grafik oluştur.", conversation_id=conversation)
        result = job["result"]
        self.assertEqual("completed", result["status"])
        self.assertIn("36 kayıt", result["message"])
        self.assertIn("Aylık kredi", result["message"])
        self.assertNotIn("999999", result["message"])
        self.assertNotIn("8123", result["message"])
        continued = self.context.run_store.get(result["run_id"])["state"]["messages"]
        self.assertFalse(any("999999" in (item.get("content") or "") for item in continued))

    def test_explicit_chart_request_cannot_complete_with_only_a_prose_claim(self):
        wid, aid, conversation = self.seed_analysis()
        self.provider.responses.extend([final("Grafik hazır.")] * 12)
        job = self.submit(wid, "Bu tablo için çizgi grafik oluştur.", conversation_id=conversation)
        self.assertEqual("finished", job["status"], job)
        self.assertIn(job["result"]["status"], {"blocked", "partial", "needs_input"}, job)
        self.assertFalse(any(step["tool"] == "create_chart" for step in job["result"]["tool_results"]))
        self.assertIsNone(self.chart(wid, aid).get("chart_id"))

    def test_old_chart_does_not_satisfy_a_new_change_request(self):
        wid, aid, conversation = self.seed_analysis()
        self.provider.responses.extend([tool("create_chart", {"analysis_id": aid, "kind": "line", "columns": ["credit"]}),
                                        final("Çizgi grafik oluşturuldu.")])
        initial = self.submit(wid, "Kredi tablosunun çizgi grafiğini oluştur.", conversation_id=conversation)
        self.assertEqual("completed", initial["result"]["status"], initial)
        saved = self.chart(wid, aid)
        self.assertIn(saved["chart_id"], {a["id"] for a in initial["result"]["artifacts"]})
        self.provider.responses.extend([final("Grafiği çubuk grafiğe çevirdim.")] * 12)
        job = self.submit(wid, "Mevcut grafiği çubuk grafiğe dönüştür.", conversation_id=conversation)
        self.assertEqual("finished", job["status"], job)
        self.assertIn(job["result"]["status"], {"blocked", "partial", "needs_input"}, job)
        self.assertEqual(saved["chart_id"], self.chart(wid, aid)["chart_id"])
        self.assertEqual("line", self.chart(wid, aid)["spec"]["kind"])

    def test_explaining_an_existing_chart_does_not_require_a_new_chart_write(self):
        wid, aid, conversation = self.seed_analysis()
        saved = self.chart(wid, aid, {"kind": "line", "columns": ["credit"]})
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        self.provider.responses.append(final("Grafikte kredi bakiyesi 100 TL'den 450 TL'ye yükseliyor."))
        job = self.submit(wid, "Grafikteki değişimi açıkla.", conversation_id=conversation)
        self.assertEqual("completed", job["result"]["status"], job)
        self.assertEqual([], job["result"]["tool_results"])
        self.assertEqual(saved["chart_id"], self.chart(wid, aid)["chart_id"])
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_new_analysis_after_chart_requires_a_chart_for_the_new_data(self):
        wid, aid, conversation = self.seed_analysis()
        revised_plan = {**self.plan, "start": "2024-01"}
        self.provider.responses.extend([
            tool("create_chart", {"analysis_id": aid, "kind": "line", "columns": ["credit"]}, "old-data-chart"),
            tool("execute", revised_plan, "new-data-analysis"),
            final("Son 24 ayın grafiği hazır."),
        ])
        job = self.submit(wid, "Son 24 ay için kredi grafiği oluştur.", conversation_id=conversation)
        result = job["result"]
        self.assertEqual("finished", job["status"], job)
        self.assertEqual("partial", result["status"], job)
        self.assertTrue(result["analysis_updated"])
        self.assertFalse(result["chart_updated"])
        self.assertIsNone(result["chart_id"])
        self.assertIn("CHART_NOT_CREATED", [error["code"] for error in result.get("errors", [])])
        self.assertNotEqual(aid, result["analysis_id"])
        self.assertEqual(24, self.analysis(wid, result["analysis_id"])["row_count"])
        old_chart_id = result["tool_results"][0]["result"]["chart_id"]
        self.assertIn(old_chart_id, {artifact["id"] for artifact in result["artifacts"]})
        self.assertEqual(old_chart_id, self.chart(wid, aid)["chart_id"])
        self.assertIsNone(self.chart(wid, result["analysis_id"]).get("chart_id"))

    def test_chart_endpoints_reject_cross_workspace_access(self):
        wid, aid, _ = self.seed_analysis()
        chart = self.chart(wid, aid, {"kind": "line", "columns": ["credit"]})
        foreign = self.workspace()
        before = self.context.store.workspace(foreign)
        responses = [self.client.get(f"/api/workspaces/{foreign}/analyses/{aid}/chart"),
                     self.client.post(f"/api/workspaces/{foreign}/analyses/{aid}/chart", json={"kind": "bar", "columns": ["credit"]}),
                     self.client.get(f"/api/workspaces/{foreign}/charts/{chart['chart_id']}")]
        for response in responses:
            self.assertIn(response.status_code, {400, 404}, response.text)
            self.assertNotIn("raw_values", response.text)
            self.assertNotIn('"series"', response.text)
        self.assertEqual(before, self.context.store.workspace(foreign))

    def test_invalid_chart_requests_return_400_without_changing_saved_view(self):
        wid, aid, _ = self.seed_analysis()
        saved = self.chart(wid, aid, {"kind": "line", "columns": ["credit"]})
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        for request in [{"kind": "pie", "columns": ["credit"]},
                        {"kind": "bar", "columns": ["missing"]},
                        {"kind": "line", "columns": []},
                        {"kind": "line", "columns": ["credit"], "normalize": "logarithm"},
                        {"kind": "line", "columns": ["credit"], "sql": "SELECT 1"}]:
            with self.subTest(request=request):
                response = self.client.post(f"/api/workspaces/{wid}/analyses/{aid}/chart", json=request)
                self.assertEqual(400, response.status_code, response.text)
                self.assertEqual(saved["chart_id"], self.chart(wid, aid)["chart_id"])
        self.assert_analysis_unchanged(wid, aid, before, analysis)

    def test_duplicate_chart_tool_calls_reuse_one_saved_view(self):
        wid, aid, conversation = self.seed_analysis()
        before, analysis = self.context.store.workspace(wid), self.analysis(wid, aid)
        request = {"analysis_id": aid, "kind": "bar", "columns": ["credit"]}
        self.provider.responses.extend([tool("create_chart", request, "chart-one"),
                                        tool("create_chart", request, "chart-two"), final("Görünüm kaydedildi.")])
        job = self.submit(wid, "Kredi sütununun çubuk grafiğini oluştur.", conversation_id=conversation)
        self.assertEqual("completed", job["result"]["status"], job)
        results = [step["result"] for step in job["result"]["tool_results"] if step["tool"] == "create_chart"]
        self.assertEqual(2, len(results))
        self.assertEqual(results[0]["chart_id"], results[1]["chart_id"])
        self.assertTrue(results[1].get("idempotent_replay"), results[1])
        self.assertEqual(1, sum(event["kind"] == "tool_reused" for event in job["events"]))
        self.assert_analysis_unchanged(wid, aid, before, analysis)
