import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.runtime import AgentRuntime
from agentic_analytics.agent.schemas import obj
from agentic_analytics.agent.tools.selection import AnalysisSelectionTools
from agentic_analytics.agent.tools.statistics import StatisticsTools
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore
from agentic_analytics.providers.mia import MiaError


def call(name, args, call_id="call-1"):
    return {"role": "assistant", "content": None, "finish_reason": "stop", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}], "usage": {"completion_tokens": 10}}


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.options = []

    def chat(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        self.options.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected extra model call")
        result = self.responses.pop(0)
        return result(messages) if callable(result) else result


FINAL = {"role": "assistant", "content": "Analiz kaydedildi.", "tool_calls": [], "finish_reason": "stop"}


class AgentRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        db_path = cls.root / "fixture.duckdb"
        db = duckdb.connect(str(db_path))
        db.execute("CREATE SCHEMA catalog")
        db.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        for metric, kind, unit, currency in [("credit", "stock", "TRY", "TRY"), ("cpi", "index", "index", None)]:
            binding = {"metric_id": metric, "title": metric, "source_system": "TEST", "table": "observations",
                "time_column": "month", "value_column": metric, "filters": {}, "dimensions": {},
                "native_frequency": "monthly", "kind": kind, "unit": unit, "scale": 1,
                "currency": currency, "aggregation": "last", "source_base": "", "provenance_columns": [],
                "status": "ready", "notes": [], "contract_version": "test"}
            if metric == "cpi":
                binding.update(index_role="price_deflator", deflator_currency="TRY", price_scope="consumer_prices")
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [metric, json.dumps(binding)])
        frame = pd.DataFrame({"month": ["2021-01", "2021-02", "2021-03"], "credit": [100., 120., 150.], "cpi": [100., 105., 110.]})
        db.register("input_frame", frame)
        db.execute("CREATE TABLE observations AS SELECT * FROM input_frame")
        db.close()
        cls.store = LakehouseStore(cls.root / "lakehouse")
        cls.snapshot = cls.store.publish_snapshot(db_path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.workspace = self.store.create_workspace(self.snapshot["snapshot_id"])
        self.workspace_id = self.workspace["workspace_id"]
        self.journal = AgentRunStore(self.root / self.workspace_id)
        self.plan = {"start": "2021-01", "end": "2021-03", "frequency": "monthly", "columns": [{"name": "credit", "metric_id": "credit", "dimensions": {}}]}

    def runtime(self, responses, **kwargs):
        client = ScriptedClient(responses)
        return AgentRuntime(self.store, self.workspace_id, client, self.journal, **kwargs), client

    def test_native_stop_tools_execute_and_request_id_replays_without_writes(self):
        runtime, client = self.runtime([call("execute", self.plan), FINAL])
        result = runtime.run("Kredi tablosunu göster", request_id="first")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decisions"], 2)
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 1)
        repeated = runtime.run("Kredi tablosunu göster", request_id="first")
        self.assertEqual(repeated, result)
        self.assertEqual(len(client.requests), 2)
        self.assertTrue(all(options["enable_thinking"] is False for options in client.options))
        self.assertEqual(self.journal.find_request(self.workspace_id, "first")["run_id"], result["run_id"])
        with self.assertRaises(ValueError):
            runtime.run("different message", request_id="first")

    def test_continuation_revises_parent_and_retains_conversation_and_cells(self):
        runtime, _ = self.runtime([call("execute", self.plan), FINAL])
        initial = runtime.run("Kredi tablosu", request_id="initial")
        before, _ = self.store.load_analysis(initial["analysis_id"])
        revision = {"analysis_id": initial["analysis_id"], "add_columns": [{"name": "cpi", "metric_id": "cpi", "dimensions": {}}],
                    "operations": [{"op": "deflate", "column": "credit", "index": "cpi", "base_period": "2021-01", "output": "real_credit"}]}
        runtime, client = self.runtime([call("revise_analysis", revision), FINAL])
        result = runtime.run("TÜFE ile reel sütunu ekle", conversation_id=initial["conversation_id"])
        self.assertEqual(result["conversation_id"], initial["conversation_id"])
        after, manifest = self.store.load_analysis(result["analysis_id"])
        self.assertEqual(manifest["parent_analysis_id"], initial["analysis_id"])
        pd.testing.assert_series_equal(before["credit"], after["credit"])
        self.assertAlmostEqual(after.iloc[-1]["real_credit"], 150 / 110 * 100)
        self.assertIn(initial["analysis_id"], client.requests[0][0]["content"])
        self.assertEqual(sum(m["role"] == "user" for m in client.requests[0]), 2)

    def test_crash_after_analysis_commit_recovers_without_duplicate_analysis(self):
        class Crash(BaseException):
            pass
        original = self.journal.complete_step
        def fail_after_write(*args):
            raise Crash()
        self.journal.complete_step = fail_after_write
        runtime, _ = self.runtime([call("execute", self.plan)])
        with self.assertRaises(Crash):
            runtime.run("Kaydet", request_id="crash")
        committed = self.store.workspace(self.workspace_id)
        self.assertEqual(committed["version"], 1)
        self.journal = AgentRunStore(self.root / self.workspace_id)
        runtime, _ = self.runtime([FINAL])
        result = runtime.run("Kaydet", request_id="crash")
        self.assertEqual(result["analysis_id"], committed["analysis_head"])
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 1)
        self.assertTrue(result["tool_results"][0]["result"]["recovered"])
        self.assertTrue(any(e["kind"] == "tool_recovered" for e in self.journal.events(result["run_id"])))

    def test_extra_fields_are_rejected_before_any_database_write(self):
        unsafe = {**self.plan, "sql": "DROP TABLE observations"}
        runtime, _ = self.runtime([call("execute", unsafe)], max_repairs=0)
        result = runtime.run("test")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "INVALID_TOOL_ARGUMENTS")
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 0)

    def test_non_ascii_or_reserved_column_names_fail_schema_before_execution(self):
        for name in ("kâr", "kredi büyüme", "period", "_private", "a" * 65):
            with self.subTest(name=name):
                plan = copy.deepcopy(self.plan)
                plan["columns"][0]["name"] = name
                runtime, _ = self.runtime([call("execute", plan)], max_repairs=0)
                result = runtime.run("Hesapla " + name)
                self.assertEqual(result["errors"][0]["code"], "INVALID_TOOL_ARGUMENTS")
                self.assertEqual(self.store.workspace(self.workspace_id)["version"], 0)

    def test_saved_analysis_at_final_decision_returns_partial_without_another_model_call(self):
        runtime, client = self.runtime([call("execute", self.plan)], max_decisions=1)
        result = runtime.run("Kaydet")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["analysis_updated"])
        self.assertIsNotNone(result["analysis_id"])
        self.assertEqual(result["warnings"][0]["code"], "FINAL_RESPONSE_BUDGET_EXCEEDED")
        self.assertEqual(len(client.requests), 1)

    def test_next_day_resume_excludes_downtime_but_retains_total_decision_count(self):
        record = self.journal.start(self.workspace_id, "Daha sonra devam et", request_id="next-day")
        record["state"]["decisions"] = 1
        self.journal.checkpoint(record["run_id"], record["state"])
        with self.journal._db() as db:
            db.execute("UPDATE runs SET created_at=? WHERE run_id=?", ("2020-01-01T00:00:00+00:00", record["run_id"]))
        runtime, client = self.runtime([FINAL], max_decisions=2)
        result = runtime.resume(record["run_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["decisions"], 2)
        self.assertEqual(len(client.requests), 1)

    def test_explicit_resume_reopens_retryable_provider_failure_and_keeps_decision_debits(self):
        def unavailable(_messages):
            raise MiaError("PROVIDER_UNAVAILABLE", "MIA bağlantısı tamamlanamadı.", retryable=True, attempts=3)

        runtime, client = self.runtime([unavailable])
        failed = runtime.run("Kısa bir açıklama ver", request_id="retry-provider")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["decisions"], 1)
        self.assertTrue(failed["errors"][0]["retryable"])
        self.assertEqual(runtime.resume(failed["run_id"]), failed)

        runtime, resumed_client = self.runtime([FINAL])
        resumed = runtime.resume(failed["run_id"], retry_terminal=True)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["run_id"], failed["run_id"])
        self.assertEqual(resumed["decisions"], 2)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(resumed_client.requests), 1)
        self.assertNotIn("MIA bağlantısı tamamlanamadı", json.dumps(resumed_client.requests[0], ensure_ascii=False))
        events = self.journal.events(failed["run_id"])
        self.assertEqual(sum(event["kind"] == "run_reopened" for event in events), 1)

    def test_nonretryable_provider_failure_cannot_be_reopened(self):
        def unauthorized(_messages):
            raise MiaError("PROVIDER_AUTH_ERROR", "MIA isteği HTTP 401 ile tamamlanamadı.", status_code=401, attempts=1)

        runtime, _ = self.runtime([unauthorized])
        failed = runtime.run("Kısa bir açıklama ver", request_id="auth-provider")
        self.assertEqual(failed["status"], "failed")
        with self.assertRaisesRegex(ValueError, "retryable provider failure"):
            runtime.resume(failed["run_id"], retry_terminal=True)

    def test_final_provider_failure_can_resume_without_repeating_committed_analysis(self):
        def unavailable(_messages):
            raise MiaError("PROVIDER_UNAVAILABLE", "MIA bağlantısı tamamlanamadı.", retryable=True, attempts=3)

        runtime, _ = self.runtime([call("execute", self.plan), unavailable])
        partial = runtime.run("Kredi tablosunu göster", request_id="analysis-provider")
        self.assertEqual(partial["status"], "partial")
        self.assertTrue(partial["analysis_updated"])
        committed = self.store.workspace(self.workspace_id)
        self.assertEqual(committed["version"], 1)
        self.assertIn("100", partial["message"])
        self.assertIn("150", partial["message"])

        runtime, client = self.runtime([FINAL])
        resumed = runtime.resume(partial["run_id"], retry_terminal=True)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["analysis_id"], committed["analysis_head"])
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 1)
        self.assertEqual(sum(item["tool"] == "execute" for item in resumed["tool_results"]), 1)
        self.assertEqual(len(client.requests), 1)

    def test_provider_failure_inside_tool_is_returned_to_model_without_aborting_turn(self):
        def unavailable(_args):
            raise MiaError(
                "PROVIDER_UNAVAILABLE",
                "MIA bağlantısı tamamlanamadı.",
                retryable=True,
                attempts=3,
            )

        tools = {
            "provider_lookup": {
                "schema": {
                    "type": "function",
                    "function": {
                        "name": "provider_lookup",
                        "parameters": obj({"query": {"type": "string"}}),
                    },
                },
                "handler": unavailable,
            },
        }
        final = {**FINAL, "content": "Sağlayıcı geçici olarak kullanılamadı; doğrulanmamış değer üretilmedi."}
        runtime, client = self.runtime([
            call("provider_lookup", {"query": "resmi tarihsel veri"}),
            final,
        ], extra_tools=tools)

        result = runtime.run("Uzak sağlayıcıdan doğrulanmış veriyi dene")

        self.assertEqual(len(client.requests), 2)
        self.assertNotEqual(result["status"], "failed")
        tool_result = result["tool_results"][0]["result"]
        self.assertEqual(tool_result["status"], "unavailable")
        self.assertEqual(tool_result["code"], "PROVIDER_UNAVAILABLE")
        self.assertEqual(tool_result["errors"][0]["attempts"], 3)
        self.assertTrue(tool_result["errors"][0]["retryable"])
        last_request = client.requests[-1]
        tool_message = next(message for message in last_request if message.get("role") == "tool")
        self.assertIn("PROVIDER_UNAVAILABLE", tool_message["content"])

    def test_anonymous_access_shell_allows_another_public_source(self):
        shell = {
            "status": "ok",
            "source_id": "source_" + "d" * 64,
            "source_url": "https://datastore.borsaistanbul.com/",
            "title": "Borsa İstanbul DataStore",
            "publisher": "Borsa İstanbul",
            "text": "Borsa İstanbul DataStore",
            "article": {"title": "Borsa İstanbul DataStore", "readable_text": "Borsa İstanbul DataStore"},
            "tables": [],
            "pages": [],
        }
        public_url = "https://www.borsaistanbul.com/official-report"
        tools = self.source_tools(inspect=lambda args: shell if "datastore" in args.get("url", "") else {
            "status": "ok", "source_id": "source_" + "e" * 64, "source_url": public_url,
            "text": "Public official report. Historical closing values are published in the attached table.",
            "tables": [], "pages": [],
        })
        runtime, client = self.runtime([
            call("inspect_source", {"url": "https://datastore.borsaistanbul.com/"}, "shell"),
            call("inspect_source", {"url": public_url}, "public"),
            {**FINAL, "content": "Ayrı bir açık resmî kaynak okundu; giriş ekranından sayı türetilmedi."},
        ], extra_tools=tools)

        result = runtime.run(
            "Resmî kaynağı anonim olarak oku; tarihsel değerler erişilemiyorsa erişim kontrolünü aşma ve veri üretme."
        )

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(client.requests), 3)
        reads = [item["result"] for item in result["tool_results"] if item["tool"] == "inspect_source"]
        self.assertEqual(reads[0]["warnings"][0]["code"], "SOURCE_ACCESS_LIMITED")
        self.assertTrue(reads[0]["recovery"]["navigation_only"])
        self.assertIn("Do not log in", reads[0]["recovery"]["next_step"])
        self.assertEqual(reads[1]["source_url"], public_url)

    def test_provider_failure_after_direct_table_read_returns_exact_partial_receipt(self):
        source_id = "source_" + "a" * 64
        source_url = "https://example.org/official-table"
        tools = {"inspect_source": {
            "schema": {"type": "function", "function": {"name": "inspect_source", "parameters": obj({"source_id": {"type": "string"}})}},
            "handler": lambda _args: {"status": "ok", "source_id": source_id, "source_url": source_url,
                "raw_sha256": "b" * 64, "title": "Resmi karar tablosu", "snippet": "uydurma 999",
                "tables": [{"table_id": "table_001", "page": 4, "columns": ["karar_tarihi", "onceki_oran", "yeni_oran"],
                            "preview": [{"karar_tarihi": "6 Mart 2025", "onceki_oran": "45", "yeni_oran": "42,5"}]}]},
        }}
        def unavailable(_messages):
            raise MiaError("PROVIDER_UNAVAILABLE", "MIA bağlantısı tamamlanamadı.", retryable=True, attempts=3)

        runtime, _ = self.runtime([call("inspect_source", {"source_id": source_id}), unavailable], extra_tools=tools)
        result = runtime.run("Resmi karar tablosunu göster", request_id="source-provider")
        self.assertEqual(result["status"], "partial")
        self.assertIn("6 Mart 2025", result["message"])
        self.assertIn("42,5", result["message"])
        self.assertIn(source_url + "#page=4", result["message"])
        self.assertNotIn("999", result["message"])
        self.assertIn("yeni hesap", result["message"].casefold())

    def test_active_invocation_deadline_stops_further_model_calls(self):
        runtime, client = self.runtime([call("describe", {"metric_id": "credit"})], max_elapsed_seconds=240)
        with patch("agentic_analytics.agent.runtime.time.monotonic", side_effect=[100., 100., 341.]):
            result = runtime.run("İncele")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "TIME_BUDGET_EXCEEDED")
        self.assertEqual(result["decisions"], 1)
        self.assertEqual(len(client.requests), 1)

    def test_verbose_discovery_stays_small_then_executes_and_continues_with_schema(self):
        service = LakehouseService(self.store, self.workspace_id)
        cards = [{"metric_id": "credit" if i == 0 else f"catalog:metric:{i}",
                  "title": (f"Metric group {i:02d} technical label " * 6), "unit": "TRY", "scale": 1,
                  "currency": "TRY", "kind": "stock", "native_frequency": "monthly", "status": "ready",
                  "dimensions": {}, "notes": ["VERBOSE_METADATA_ONLY " * 400],
                  "institution_scope": "LONG_SCOPE_DESCRIPTION " * 400} for i in range(25)]
        service.discover = lambda request: {"status": "ok", "total": 25, "metrics": cards[:request.get("limit", 10)]}
        responses = [call("discover", {"query": f"search-{i}", "limit": 25}, f"search-{i}") for i in range(4)]
        responses += [call("describe", {"metric_id": "credit"}, "details"), call("execute", self.plan, "execute"), FINAL]
        runtime, client = self.runtime(responses, service=service, max_context_chars=26000)
        result = runtime.run("Veriyi bul ve hesapla")
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["analysis_updated"])
        self.assertNotIn("VERBOSE_METADATA_ONLY", json.dumps(client.requests))
        self.assertNotIn("LONG_SCOPE_DESCRIPTION", json.dumps(client.requests))
        compacted = False
        initial_compacted = False
        for messages in client.requests:
            context = json.loads(messages[0]["content"].split("Güncel güvenilir çalışma alanı bağlamı:\n", 1)[1])
            initial_compacted |= context["initial_metric_candidates"].get("historical_search_summary", False)
            names = {}
            for message in messages:
                for tool_call in message.get("tool_calls", []):
                    names[tool_call["id"]] = tool_call["function"]["name"]
                if message["role"] == "tool":
                    self.assertIn(message["tool_call_id"], names)
                    payload = json.loads(message["content"])
                    if names[message["tool_call_id"]] == "discover":
                        self.assertLessEqual(len(payload["metrics"]), 10)
                        compacted |= payload.get("historical_search_summary", False)
        self.assertTrue(compacted)
        self.assertTrue(initial_compacted)
        initial = self.journal.get(result["run_id"])["state"]["initial_candidates"]
        self.assertFalse(initial.get("historical_search_summary", False))
        self.assertEqual(initial["metrics"][0]["title"], cards[0]["title"])
        events = self.journal.events(result["run_id"])
        full = next(e["payload"]["result"] for e in events if e["kind"] == "tool_result" and e["payload"].get("tool") == "discover")
        self.assertEqual(len(full["metrics"]), 25)
        self.assertEqual(full["metrics"][0]["notes"], cards[0]["notes"])
        runtime, continuation_client = self.runtime([FINAL], max_context_chars=26000)
        continued = runtime.run("Bu tablonun birimini açıkla", conversation_id=result["conversation_id"])
        self.assertEqual(continued["status"], "completed")
        self.assertIn('"active_schema"', continuation_client.requests[0][0]["content"])
        self.assertIn(result["analysis_id"], continuation_client.requests[0][0]["content"])

    def test_barren_discovery_completes_as_grounded_refusal_not_budget_death(self):
        # A genuinely absent concept: discovery keeps returning no_confident_match and
        # the model loops. Instead of dying on the decision budget with a blocked
        # non-answer, the run completes with a stated 'not found' grounded in the
        # unresolved term and the nearest real series.
        service = LakehouseService(self.store, self.workspace_id)
        service.discover = lambda request: {"status": "ok", "total": 0, "no_confident_match": True,
            "uncovered_terms": ["zephyr"], "metrics": [],
            "near_matches": [{"metric_id": "credit", "title": "Krediler [Toplam]"}]}
        responses = [call("discover", {"query": f"zephyr {i}", "limit": 5}, f"s{i}") for i in range(5)]
        runtime, _ = self.runtime(responses, service=service, max_decisions=3)
        result = runtime.run("zephyr serisini göster")
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("DECISION_BUDGET_EXCEEDED", json.dumps(result.get("errors", [])))
        self.assertIn("zephyr", result["message"])
        self.assertIn("bulunamadı", result["message"])
        self.assertTrue(any(w.get("code") == "METRIC_NOT_FOUND" for w in result.get("warnings", [])))

    def test_barren_after_a_ready_candidate_does_not_false_refuse(self):
        # Regression guard: if a ready candidate was seen earlier in the run, a later
        # barren search + budget exhaustion must NOT be reclassified as "series not found"
        # (that would falsely tell the user a metric that exists does not).
        service = LakehouseService(self.store, self.workspace_id)
        state = {"n": 0}
        def disc(request):
            state["n"] += 1
            if state["n"] <= 2:
                return {"status": "ok", "total": 1, "metrics": [{"metric_id": "credit", "title": "credit", "status": "ready"}]}
            return {"status": "ok", "total": 0, "no_confident_match": True, "uncovered_terms": ["zephyr"], "metrics": [], "near_matches": []}
        service.discover = disc
        responses = [call("discover", {"query": f"q{i}", "limit": 5}, f"s{i}") for i in range(6)]
        runtime, _ = self.runtime(responses, service=service, max_decisions=4)
        result = runtime.run("kredi sonra zephyr")
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("DECISION_BUDGET_EXCEEDED", json.dumps(result.get("errors", [])))
        self.assertNotIn("bulunamadı", result["message"])

    def test_budget_death_still_blocks_when_a_metric_was_found(self):
        # Guard: when discovery DID find candidates (no barren signal), exhausting the
        # budget must still block, not be reclassified as a not-found refusal.
        service = LakehouseService(self.store, self.workspace_id)
        service.discover = lambda request: {"status": "ok", "total": 1,
            "metrics": [{"metric_id": "credit", "title": "credit", "status": "ready"}]}
        responses = [call("discover", {"query": f"kredi {i}", "limit": 5}, f"s{i}") for i in range(5)]
        runtime, _ = self.runtime(responses, service=service, max_decisions=3)
        result = runtime.run("krediyi göster")
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("DECISION_BUDGET_EXCEEDED", json.dumps(result.get("errors", [])))

    def test_repeated_missing_dimension_value_ends_as_grounded_row_refusal(self):
        # The metric is found, but the run keeps looking up a dimension value that does
        # not exist (e.g. a national/"Türkiye" row in a province-only series) and dies on
        # the budget. Instead of a bare budget death with no answer, complete with a
        # stated refusal that names the missing value.
        service = LakehouseService(self.store, self.workspace_id)
        service.discover = lambda request: {"status": "ok", "total": 1,
            "metrics": [{"metric_id": "credit", "title": "credit", "status": "ready"}]}
        service.dimension_values = lambda request: {"status": "ok", "total": 0, "values": [],
            "metric_id": "credit", "dimension": request.get("dimension")}
        responses = [call("discover", {"query": "kredi", "limit": 5}, "d0")] + [
            call("dimension_values", {"metric_id": "credit", "dimension": "city", "query": "Türkiye", "limit": 100 + i}, f"v{i}")
            for i in range(5)]
        runtime, _ = self.runtime(responses, service=service, max_decisions=4)
        result = runtime.run("Türkiye geneli krediyi göster")
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("DECISION_BUDGET_EXCEEDED", json.dumps(result.get("errors", [])))
        self.assertIn("bulunamadı", result["message"])
        self.assertIn("Türkiye", result["message"])
        self.assertTrue(any(w.get("code") == "DIMENSION_VALUE_NOT_FOUND" for w in result.get("warnings", [])))

    def test_no_progress_on_missing_dimension_value_refuses_with_the_row(self):
        # Byte-identical repeated lookups of a nonexistent row trip NO_PROGRESS; the
        # terminal must state the row was not found rather than the generic failure.
        service = LakehouseService(self.store, self.workspace_id)
        service.discover = lambda request: {"status": "ok", "total": 1,
            "metrics": [{"metric_id": "credit", "title": "credit", "status": "ready"}]}
        service.dimension_values = lambda request: {"status": "ok", "total": 0, "values": [],
            "metric_id": "credit", "dimension": request.get("dimension")}
        responses = [call("discover", {"query": "kredi", "limit": 5}, "d0")] + [
            call("dimension_values", {"metric_id": "credit", "dimension": "city", "query": "Türkiye"}, f"v{i}")
            for i in range(3)]
        runtime, _ = self.runtime(responses, service=service, max_decisions=6)
        result = runtime.run("Türkiye satırını getir")
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("bulunamadı", result["message"])
        self.assertTrue(any(w.get("code") == "DIMENSION_VALUE_NOT_FOUND" for w in result.get("warnings", [])))

    def test_found_dimension_value_does_not_trigger_false_row_refusal(self):
        # If a later lookup DOES find values, the missing-row signal must be cleared so a
        # budget death is not misreported as "row not found".
        service = LakehouseService(self.store, self.workspace_id)
        service.discover = lambda request: {"status": "ok", "total": 1,
            "metrics": [{"metric_id": "credit", "title": "credit", "status": "ready"}]}
        seen = {"n": 0}
        def dv(request):
            seen["n"] += 1
            total = 0 if seen["n"] == 1 else 1
            return {"status": "ok", "total": total, "values": [] if total == 0 else [{"value": "ANKARA"}],
                    "metric_id": "credit", "dimension": request.get("dimension")}
        service.dimension_values = dv
        responses = [call("discover", {"query": "kredi", "limit": 5}, "d0"),
                     call("dimension_values", {"metric_id": "credit", "dimension": "city", "query": "Türkiye"}, "v0"),
                     call("dimension_values", {"metric_id": "credit", "dimension": "city", "query": "Ankara"}, "v1")]
        runtime, _ = self.runtime(responses, service=service, max_decisions=3)
        result = runtime.run("önce Türkiye sonra Ankara")
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("DECISION_BUDGET_EXCEEDED", json.dumps(result.get("errors", [])))
        self.assertNotIn("bulunamadı", result["message"])

    def test_repeated_successful_write_with_new_call_id_reuses_artifact(self):
        runtime, _ = self.runtime([call("execute", self.plan, "first-call"), call("execute", self.plan, "second-call"), FINAL])
        result = runtime.run("Kaydet")
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 1)
        self.assertTrue(result["tool_results"][1]["result"]["idempotent_replay"])

    def test_workspace_runs_are_serialized_while_polling_remains_available(self):
        entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        def slow(messages):
            entered.set()
            self.assertTrue(release.wait(3))
            return FINAL
        first, _ = self.runtime([slow])
        second, _ = self.runtime([lambda messages: second_entered.set() or FINAL])
        results, errors = [], []
        def invoke(runtime, message):
            try:
                results.append(runtime.run(message, request_id=message))
            except Exception as exc:
                errors.append(exc)
        thread1 = threading.Thread(target=invoke, args=(first, "first"))
        thread2 = threading.Thread(target=invoke, args=(second, "second"))
        thread1.start()
        self.assertTrue(entered.wait(3))
        thread2.start()
        self.assertEqual(self.journal.find_request(self.workspace_id, "first")["status"], "running")
        self.assertFalse(second_entered.wait(.05))
        release.set()
        thread1.join(3)
        thread2.join(3)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)

    def test_interrupted_custom_mutation_without_recovery_is_never_replayed(self):
        class Crash(BaseException):
            pass
        writes = []
        def mutating(args):
            writes.append(args)
            raise Crash()
        extra = {"publish_document": {"schema": {"type": "function", "function": {"name": "publish_document", "parameters": obj({"source_id": {"type": "string"}})}},
                  "handler": mutating, "mutating": True}}
        runtime, _ = self.runtime([call("publish_document", {"source_id": "trusted-source"})], extra_tools=extra)
        with self.assertRaises(Crash):
            runtime.run("Belgeyi ekle", request_id="custom-write")
        runtime, client = self.runtime([], extra_tools=extra)
        result = runtime.run("Belgeyi ekle", request_id="custom-write")
        self.assertEqual(result["errors"][0]["code"], "UNKNOWN_MUTATION_OUTCOME")
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(client.requests), 0)

    def test_flat_unknown_publication_outcome_is_normalized_and_stops(self):
        class Crash(BaseException):
            pass
        writes = []
        def handler(args):
            writes.append(args)
            raise Crash()
        extra = {"publish_document": {"schema": {"type": "function", "function": {"name": "publish_document", "parameters": obj({"source_id": {"type": "string"}})}},
                 "handler": handler, "mutating": True,
                 "recover": lambda args, intent: {"status": "blocked", "code": "WRITE_OUTCOME_UNKNOWN", "message": "No known committed publication."}}}
        runtime, _ = self.runtime([call("publish_document", {"source_id": "source"})], extra_tools=extra)
        with self.assertRaises(Crash):
            runtime.run("Ekle", request_id="unknown-publication")
        runtime, client = self.runtime([], extra_tools=extra)
        result = runtime.run("Ekle", request_id="unknown-publication")
        self.assertEqual(result["errors"][0]["code"], "UNKNOWN_MUTATION_OUTCOME")
        self.assertEqual(len(writes), 1)

    def test_semantically_invalid_plan_is_not_executed(self):
        plan = {**self.plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2021-01", "output": "invalid"}]}
        runtime, _ = self.runtime([call("execute", plan)], max_repairs=0)
        result = runtime.run("test")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "UNIT_MISMATCH")
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 0)

    def test_model_completion_claim_cannot_hide_failed_tool_or_reuse_old_head(self):
        runtime, _ = self.runtime([call("execute", self.plan), FINAL])
        previous = runtime.run("Önceki doğru analiz")
        invalid = {**self.plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2021-01", "output": "invalid"}]}
        runtime, _ = self.runtime([call("execute", invalid), FINAL])
        result = runtime.run("Yeni hatalı işlem")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "UNIT_MISMATCH")
        self.assertIsNone(result["analysis_id"])
        self.assertEqual(result["active_analysis_id"], previous["analysis_id"])
        self.assertFalse(result["analysis_updated"])
        self.assertNotEqual(result["message"], FINAL["content"])

    def test_valid_repair_resolves_failure_and_can_complete(self):
        invalid = {**self.plan, "sql": "select 1"}
        runtime, _ = self.runtime([call("execute", invalid, "bad"), call("execute", self.plan, "fixed"), FINAL])
        result = runtime.run("Hesapla")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["analysis_updated"])

    def test_successful_analysis_with_unresolved_ancillary_failure_is_partial(self):
        extra = {"web_search": {"schema": {"type": "function", "function": {"name": "web_search", "parameters": obj({"query": {"type": "string"}})}},
                  "handler": lambda args: {"status": "unavailable", "code": "SEARCH_UNAVAILABLE", "message": "No network result."}}}
        runtime, _ = self.runtime([call("execute", self.plan), call("web_search", {"query": "credit"}, "search"), FINAL], extra_tools=extra)
        result = runtime.run("Analizi yap ve ek kaynak ara")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["analysis_updated"])
        self.assertIsNotNone(result["analysis_id"])

    def test_no_progress_and_empty_output_have_bounded_stops(self):
        runtime, client = self.runtime([call("describe", {"metric_id": "credit"}, f"call-{i}") for i in range(3)])
        result = runtime.run("test")
        self.assertEqual(result["errors"][0]["code"], "NO_PROGRESS")
        self.assertEqual(len(client.requests), 3)
        runtime, client = self.runtime([{"content": None, "tool_calls": []}], max_repairs=0)
        result = runtime.run("empty")
        self.assertEqual(result["errors"][0]["code"], "EMPTY_MODEL_RESPONSE")

    @staticmethod
    def source_tools(search=None, inspect=None, research=None):
        tools = {}
        for name, field, handler in (("web_search", "query", search), ("inspect_source", "url", inspect), ("research_web", "query", research)):
            if handler is not None:
                tools[name] = {"schema": {"type": "function", "function": {"name": name,
                    "parameters": obj({field: {"type": "string"}})}}, "handler": handler}
        return tools

    def test_search_query_rewrites_with_same_urls_switch_to_source_reading(self):
        attempts = []
        def search(args):
            attempts.append(args["query"])
            # Different ranking and fragments must not count as new sources.
            urls = ["https://example.org/", "https://example.org/reports"]
            if len(attempts) % 2 == 0:
                urls = [url + "#top" for url in reversed(urls)]
            return {"status": "ok", "results": [{"url": url} for url in urls]}
        extra = self.source_tools(search, lambda args: {"status": "ok", "source_id": "report",
                                  "source_url": args["url"], "text": "Verified report text."})
        responses = [call("web_search", {"query": f"company report variant {i}"}, f"search-{i}") for i in range(3)]
        responses += [call("inspect_source", {"url": "https://example.org/reports"}, "read"), call("execute", self.plan), FINAL]
        runtime, client = self.runtime(responses, extra_tools=extra)
        result = runtime.run("Başka bir şirketi mevcut analize ekle")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(attempts), 3)
        self.assertNotIn("web_search", {tool["function"]["name"] for tool in client.options[3]["tools"]})
        self.assertIn("web_search", {tool["function"]["name"] for tool in client.options[4]["tools"]})
        warning = result["tool_results"][2]["result"]["warnings"][0]
        self.assertEqual(warning["code"], "SEARCH_RESULTS_REPEATED")
        self.assertEqual(result["tool_results"][2]["result"]["progress"]["new_source_urls"], 0)
        self.assertEqual(self.store.workspace(self.workspace_id)["version"], 1)

    def test_identical_search_guard_is_recoverable_without_repeating_handler(self):
        attempts = []
        def search(args):
            attempts.append(args)
            return {"status": "ok", "results": [{"url": "https://example.org/reports"}]}
        extra = self.source_tools(search, research=lambda args: {"status": "ok", "sources": [
            {"url": "https://example.org/reports/quarter.pdf", "content": "Quarterly report."}]})
        responses = [call("web_search", {"query": "company report"}, f"search-{i}") for i in range(3)]
        responses += [call("research_web", {"query": "company financial report"}, "research"), call("execute", self.plan), FINAL]
        runtime, client = self.runtime(responses, extra_tools=extra)
        result = runtime.run("Kaynağı bul ve analiz yap")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result["repairs"], 1)
        self.assertEqual(result["tool_results"][2]["result"]["errors"][0]["code"], "SEARCH_NO_PROGRESS")
        self.assertIn("research_web", result["tool_results"][2]["result"]["recovery"]["available_tools"])
        self.assertFalse(self.journal.get(result["run_id"])["state"]["unresolved_errors"])
        self.assertEqual(len(client.requests), 6)

    def test_unreadable_research_rewrites_are_paused_after_two_attempts(self):
        runtime, _ = self.runtime([], extra_tools=self.source_tools(research=lambda _args: {"status": "unavailable", "sources": []}))
        state = {"search_progress": {"urls": [], "stale_calls": 0}, "tool_results": []}
        first, second = {"status": "unavailable", "sources": []}, {"status": "unavailable", "sources": []}
        runtime._track_search_progress(state, "research_web", first)
        runtime._track_search_progress(state, "research_web", second)
        self.assertTrue(state["search_progress"]["paused"])
        self.assertTrue(state["search_progress"]["research_web_paused"])
        self.assertEqual(second["warnings"][0]["code"], "RESEARCH_RESULTS_REPEATED")
        self.assertNotIn("research_web", {tool["function"]["name"] for tool in runtime._model_tool_schemas(state)})

    def test_source_semantics_accepts_requested_alternative_without_weakening_strict_requirement(self):
        runtime, _ = self.runtime([])
        state = {"messages": [{"role": "user", "content": "TÜFE 2025: haber bülteni veya metodoloji sayfasıyla doğrula"}],
                 "tool_results": [{"tool": "research_web", "result": {"status": "ok", "sources": [{
                     "source_id": "bulletin", "title": "Tüketici Fiyat Endeksi, Aralık 2025",
                     "document_type": "statistical_bulletin", "content": "TÜFE 2025 haber bülteni okundu."}]}}]}
        self.assertEqual(runtime._source_semantic_errors(state, "Kaynak okundu."), [])
        state["messages"][0]["content"] = "TÜFE 2025: yalnız metodoloji sayfasıyla doğrula"
        errors = runtime._source_semantic_errors(state, "Kaynak okundu.")
        self.assertEqual(errors[0]["code"], "SOURCE_DOCUMENT_TYPE_MISMATCH")
        self.assertEqual(errors[0]["accepted_document_types"], ["index_methodology"])

    def test_research_stall_retains_rejected_document_addresses_for_targeted_recovery(self):
        runtime, _ = self.runtime([], extra_tools=self.source_tools())
        state = {"search_progress": {"urls": [], "stale_calls": 0}, "tool_results": []}
        for _ in range(2):
            result = {"status": "unavailable", "sources": [], "failures": [{
                "url": "https://example.org/report", "code": "DOCUMENT_TYPE_MISMATCH",
                "source_id": "report", "suggested_inspection": {"source_id": "report"}}]}
            runtime._track_search_progress(state, "research_web", result)
        self.assertTrue(state["search_progress"]["research_web_paused"])
        self.assertEqual(result["recovery"]["candidate_urls"], ["https://example.org/report"])
        self.assertEqual(result["recovery"]["source_failures"][-1]["suggested_inspection"], {"source_id": "report"})

    def test_ignored_search_stall_is_bounded_and_explains_preserved_analysis(self):
        initial, _ = self.runtime([call("execute", self.plan), FINAL])
        parent = initial.run("Kredi tablosunu oluştur")
        before = self.store.workspace(self.workspace_id)
        attempts = []
        def search(args):
            attempts.append(args)
            return {"status": "ok", "results": [{"url": "https://example.org/"}]}
        extra = self.source_tools(search, lambda args: {"status": "ok", "text": "Unused source."})
        responses = [call("web_search", {"query": f"report variant {i}"}, f"search-{i}") for i in range(6)]
        runtime, client = self.runtime(responses, extra_tools=extra)
        result = runtime.run("Yeni şirketi ekle", conversation_id=parent["conversation_id"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(client.requests), 6)
        self.assertEqual(result["errors"][0]["code"], "SEARCH_STRATEGY_EXHAUSTED")
        self.assertIn("Mevcut analiz korundu", result["message"])
        self.assertIn("rapor bağlantısını", result["message"])
        self.assertEqual(self.store.workspace(self.workspace_id), before)

    def test_source_failure_can_use_another_url_but_mutation_unknown_stops(self):
        attempts = []
        def inspect(args):
            attempts.append(args["url"])
            return ({"status": "blocked", "code": "SOURCE_NOT_FOUND", "message": "Unavailable URL"}
                    if len(attempts) == 1 else {"status": "ok", "source_id": "report", "text": "Source read."})
        runtime, _ = self.runtime([call("inspect_source", {"url": "https://example.org/old"}, "old"),
            call("inspect_source", {"url": "https://example.org/new"}, "new"), call("execute", self.plan), FINAL],
            extra_tools=self.source_tools(inspect=inspect))
        result = runtime.run("Kaynağı bul ve analiz yap")
        self.assertEqual(result["status"], "completed", result)
        runtime, client = self.runtime([call("inspect_source", {"url": "https://example.org/"})],
            extra_tools=self.source_tools(inspect=lambda args: {"status": "blocked", "code": "UNKNOWN_MUTATION_OUTCOME", "message": "Unreconciled write"}))
        result = runtime.run("Durumu kontrol et")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "UNKNOWN_MUTATION_OUTCOME")
        self.assertEqual(len(client.requests), 1)

    def test_institutional_ownership_requires_source_read_and_repairs_once(self):
        invented = {"content": "Ortakları Alpha, Beta, Gamma ve diğer tüm bankalardır.", "tool_calls": [], "finish_reason": "stop"}
        extra = self.source_tools(research=lambda args: {"status": "ok", "sources": [
            {"url": "https://example.org/shareholders", "title": "Shareholders", "content": "Alpha and Beta are the shareholders."}]})
        runtime, client = self.runtime([invented, call("research_web", {"query": "Example company official shareholders"}), FINAL], extra_tools=extra)
        result = runtime.run("Bu şirketin ortakları kimler, hangilerini karşılaştırmaya ekleyelim?")
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("https://example.org/shareholders", result["message"])
        self.assertNotIn("Gamma", result["message"])
        self.assertIn("EXTERNAL_FACTS_UNVERIFIED", json.dumps(client.requests[1]))
        self.assertNotIn("Gamma", json.dumps(self.journal.get(result["run_id"])["state"]["messages"]))

    def test_search_snippets_never_verify_shareholder_list(self):
        invented = {"content": "Ortakları Alpha ve Beta bankalarıdır.", "tool_calls": [], "finish_reason": "stop"}
        extra = self.source_tools(search=lambda args: {"status": "ok", "results": [
            {"url": "https://example.org/shareholders", "snippet": "Alpha and Beta are shareholders."}]})
        runtime, client = self.runtime([call("web_search", {"query": "Example shareholders"}), invented, invented], extra_tools=extra)
        result = runtime.run("Example şirketinin hissedarları kim?")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("EXTERNAL_FACTS_UNVERIFIED", {error["code"] for error in result["errors"]})
        self.assertNotIn("Alpha", result["message"])
        self.assertEqual(len(client.requests), 3)

    def test_replaced_search_argument_error_does_not_poison_successful_research(self):
        extra = self.source_tools(search=lambda args: {"status": "ok", "results": []},
            research=lambda args: {"status": "ok", "sources": [{"url": "https://example.org/report", "content": "Quarterly report read."}]})
        extra["web_search"]["schema"]["function"]["parameters"]["properties"]["limit"] = {"type": "integer"}
        runtime, _ = self.runtime([call("web_search", {"query": "company report", "limit": "5"}),
            call("research_web", {"query": "company report"}, "read"), call("execute", self.plan, "save"), FINAL], extra_tools=extra)
        result = runtime.run("Kaynağı bul ve analize ekle")
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["analysis_updated"])
        self.assertEqual(result["tool_results"][0]["result"]["errors"][0]["code"], "INVALID_TOOL_ARGUMENTS")

    def test_ownership_permission_request_repairs_to_research_without_user_pause(self):
        permission = call("ask_user", {"question": "Resmi ortaklarını kontrol etmemi ister misiniz?"})
        tools = self.source_tools(research=lambda args: {"status": "ok", "sources": [
            {"url": "https://example.org/ownership", "content": "Example ortakları: Alpha ve Beta."}]})
        runtime, client = self.runtime([call("ask_user", {"question": 123}, "invalid-question"), permission,
            call("research_web", {"query": "Example ortaklık yapısı"}),
            {**FINAL, "content": "Example ortakları Alpha ve Beta'dır."}], extra_tools=tools)
        result = runtime.run("Example'nin ortakları kimler?")
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("Alpha", result["message"])
        self.assertEqual(len(client.requests), 4)
        self.assertTrue(self.journal.get(result["run_id"])["state"]["ownership_clarification_repair"])
        runtime, client = self.runtime([permission, permission], extra_tools=tools)
        result = runtime.run("Example'nin ortakları kimler?")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result["errors"][0]["code"], "EXTERNAL_FACTS_UNVERIFIED")

    def test_ownership_requires_matching_topic_and_explicit_subject(self):
        invented = {"content": "Ortakları Alpha, Beta ve uydurma Gamma bankalarıdır.", "tool_calls": [], "finish_reason": "stop"}
        for source in [
            {"text": "Example quarterly total assets report."},
            {"text": "OtherCo shareholders are Alpha and Beta.", "source_url": "https://otherco.example/shareholders"},
            {"article": {"source_links": [{"title": "Shareholders"}]}, "text": "Example home page."},
        ]:
            with self.subTest(source=source):
                tools = self.source_tools(inspect=lambda args: {"status": "ok", "source_id": "any_registered_id", **source})
                runtime, _ = self.runtime([call("inspect_source", {"url": "https://example.org/report"}), invented, invented], extra_tools=tools)
                result = runtime.run("Example'nin hissedarları kimler?")
                self.assertEqual(result["status"], "blocked", result)
                self.assertNotIn("Gamma", result["message"])
        for source in [
            {"article": {"article_body": "Example ortakları: Alpha ve Beta."}},
            {"columns": ["Shareholders"], "rows": [{"Shareholders": "Alpha"}], "source_url": "https://example.org/shareholders"},
        ]:
            with self.subTest(source=source):
                tools = self.source_tools(inspect=lambda args: {"status": "ok", "source_id": "unrelated_id_is_not_evidence", **source})
                runtime, _ = self.runtime([call("inspect_source", {"url": "https://example.org/ownership"}), FINAL], extra_tools=tools)
                result = runtime.run("Example'nin hissedarları kimler?")
                self.assertEqual(result["status"], "completed", result)

    def test_ownership_can_still_ask_for_missing_institution(self):
        runtime, client = self.runtime([call("ask_user", {"question": "Hangi kurumun hissedarlarını öğrenmek istiyorsunuz?"})])
        result = runtime.run("Hissedarları kim?")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(len(client.requests), 1)

    def test_existing_analysis_addition_researches_before_premature_source_permission(self):
        seed, _ = self.runtime([call("execute", self.plan), FINAL])
        previous = seed.run("Kredi tablosunu göster")
        service = LakehouseService(self.store, self.workspace_id)
        service.dimension_values = lambda args: {"status": "ok", "dimension": "group_code",
            "total": 0 if args.get("query") else 10, "values": [] if args.get("query") else ["Sektör", "Mevduat"]}
        for question in (
            "İş Bankası için resmi kaynağı kullanayım mı? Kaynak URL/dosya adını paylaşın.",
            "İş Bankası toplam aktiflerini hangi kaynaktan ekleyelim? (1) Konsolide PDF (Garanti’deki gibi), (2) BDDK Yerli Özel bankalar grubu (tek başına değil).",
        ):
            with self.subTest(question=question):
                permission = call("ask_user", {"question": question}, "permission")
                tools = self.source_tools(research=lambda args: {"status": "ok", "sources": [
                    {"url": "https://example.org/report", "content": "Official report read."}]})
                runtime, client = self.runtime([
                    call("dimension_values", {"metric_id": "credit", "dimension": "group_code", "query": "İş Bankası"}, "empty"),
                    call("dimension_values", {"metric_id": "credit", "dimension": "group_code"}, "groups"), permission,
                    call("research_web", {"query": "Official report for the current analysis period"}, "research"),
                    call("execute", self.plan, "save"), FINAL], service=service, extra_tools=tools)
                result = runtime.run("İş Bankası\nekle", conversation_id=previous["conversation_id"])
                self.assertEqual(result["status"], "completed", result)
                self.assertTrue(result["analysis_updated"])
                self.assertTrue(self.journal.get(result["run_id"])["state"]["source_clarification_repair"])
                repaired_request = json.dumps(client.requests[3], ensure_ascii=False)
                self.assertIn("active_plan", repaired_request)
                self.assertIn("2021-03", repaired_request)
                self.assertIn("workspace_version", repaired_request)
                self.assertEqual(sum(item["tool"] == "research_web" for item in result["tool_results"]), 1)

    def test_source_permission_repair_is_once_and_does_not_override_real_choices_or_restrictions(self):
        seed, _ = self.runtime([call("execute", self.plan), FINAL])
        previous = seed.run("Kredi tablosunu göster")
        service = LakehouseService(self.store, self.workspace_id)
        service.dimension_values = lambda args: {"status": "ok", "total": 0, "values": [], "dimension": "group_code"}
        tools = self.source_tools(research=lambda args: {"status": "blocked", "code": "NO_READABLE_SOURCES", "message": "Unavailable"})
        lookup = call("dimension_values", {"metric_id": "credit", "dimension": "group_code", "query": "OtherCo"}, "lookup")
        question = "Resmi kaynak URL veya dosyasını paylaşır mısınız?"
        permission = call("ask_user", {"question": question}, "permission")
        for request, answer in [
            ("OtherCo ekle, internet kullanma.", question),
            ("OtherCo ekle. Sadece yüklediğim dosyaları kullan.", question),
            ("Add OtherCo, no internet.", question),
            ("Add OtherCo using only uploaded files.", question),
            ("OtherCo ekle", "Hangi dönemin resmi kaynak dosyasını kullanayım?"),
            ("OtherCo ekle", "Konsolide mi solo mu? Resmi kaynağı paylaşır mısınız?"),
            ("OtherCo ekle", "Hangi şirketin resmi kaynak dosyasını ekleyelim?"),
        ]:
            with self.subTest(request=request, answer=answer):
                runtime, client = self.runtime([lookup, call("ask_user", {"question": answer}, "question")],
                    service=service, extra_tools=tools)
                result = runtime.run(request)
                self.assertEqual(result["status"], "needs_input", result)
                self.assertEqual(len(client.requests), 2)
                self.assertFalse(self.journal.get(result["run_id"])["state"].get("source_clarification_repair"))
        # An explicit restriction from the prior user turn still applies.
        restricted, _ = self.runtime([call("ask_user", {"question": "Hangi kurum?"})])
        restricted_turn = restricted.run("Sadece yüklediğim dosyaları kullan.")
        runtime, client = self.runtime([lookup, permission], service=service, extra_tools=tools)
        result = runtime.run("OtherCo ekle", conversation_id=restricted_turn["conversation_id"])
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(len(client.requests), 2)
        for middle in ([], [call("research_web", {"query": "OtherCo report"}, "attempt")]):
            with self.subTest(attempted=bool(middle)):
                # Either a repeated question after the one repair or a real
                # failed research attempt can ask for the missing source.
                responses = [lookup, *middle, permission] if middle else [lookup, permission, permission]
                runtime, client = self.runtime(responses, service=service, extra_tools=tools)
                result = runtime.run("OtherCo ekle")
                self.assertEqual(result["status"], "needs_input", result)
                self.assertEqual(len(client.requests), 3)

    def test_research_restriction_uses_whole_negative_verbs_and_latest_explicit_preference(self):
        from agentic_analytics.agent.runtime import _external_research_forbidden
        prior = [{"role": "user", "content": "Sadece yüklediğim dosyaları kullan."}]
        for text in ("web araması yap", "web araştırması yap", "internette ara", "search the web"):
            with self.subTest(text=text):
                current = {"role": "user", "content": text}
                self.assertFalse(_external_research_forbidden([current]))
                self.assertFalse(_external_research_forbidden([*prior, current]))
        for text in ("web arama", "web araştırması yapma", "interneti kullanma"):
            with self.subTest(text=text):
                self.assertTrue(_external_research_forbidden([{"role": "user", "content": text}]))

    def test_source_url_request_is_valid_when_only_url_reader_exists(self):
        seed, _ = self.runtime([call("execute", self.plan), FINAL])
        seed.run("Kredi tablosunu göster")
        service = LakehouseService(self.store, self.workspace_id)
        service.dimension_values = lambda args: {"status": "ok", "total": 0, "values": []}
        tools = self.source_tools(inspect=lambda args: {"status": "ok", "source_id": "source", "text": "Read"})
        runtime, client = self.runtime([
            call("dimension_values", {"metric_id": "credit", "dimension": "group_code", "query": "OtherCo"}),
            call("ask_user", {"question": "Resmi kaynak URL veya dosyasını paylaşır mısınız?"})],
            service=service, extra_tools=tools)
        result = runtime.run("OtherCo ekle")
        self.assertEqual(result["status"], "needs_input", result)
        self.assertEqual(len(client.requests), 2)

    def test_ownership_table_uses_registered_source_identity(self):
        tools = self.source_tools(inspect=lambda args: {"status": "ok", "source_id": "registered_source",
            "source_url": "https://example.org/report", "raw_sha256": "hash", "text": "Financial report cover."})
        tools["read_source_table"] = {"schema": {"type": "function", "function": {"name": "read_source_table", "parameters": obj({"source_id": {"type": "string"}})}},
            "handler": lambda args: {"status": "ok", "source_id": args["source_id"], "raw_sha256": "hash", "columns": ["Shareholders"], "rows": [{"candidate_row": 1, "values": {"Shareholders": "Alpha"}}]}}
        runtime, _ = self.runtime([call("inspect_source", {"url": "https://example.org/report"}),
            call("read_source_table", {"source_id": "registered_source"}, "ownership-rows"), FINAL], extra_tools=tools)
        result = runtime.run("Example'nin hissedarları kimler?")
        self.assertEqual(result["status"], "completed", result)

    def test_general_lessons_and_clarifications_do_not_force_web_research(self):
        for message, content in [("Ortaklık yapısı nedir?", "Bir şirkette payların sahipler arasındaki dağılımıdır."),
                                 ("What is ownership structure?", "Bir şirkette payların sahipler arasındaki dağılımıdır."),
                                 ("Teşekkür ederim", "Rica ederim."),
                                 ("Hissedarları kim?", "Hangi kurumun hissedarlarını öğrenmek istiyorsunuz?")]:
            with self.subTest(message=message):
                runtime, client = self.runtime([{"content": content, "tool_calls": [], "finish_reason": "stop"}])
                result = runtime.run(message)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(len(client.requests), 1)

    def test_search_stall_progress_survives_crash_after_result_journaling(self):
        class Crash(BaseException):
            pass
        calls = []
        def search(args):
            calls.append(args["query"])
            return {"status": "ok", "results": [{"url": "https://example.org/reports"}]}
        extras = self.source_tools(search, lambda args: {"status": "ok", "text": "Source read."})
        original = self.journal.complete_step
        def crash_after_journal(run_id, step_id, result):
            original(run_id, step_id, result)
            if result.get("progress", {}).get("repeated_result_sets") == 2:
                raise Crash()
        self.journal.complete_step = crash_after_journal
        runtime, _ = self.runtime([call("web_search", {"query": f"variant {i}"}, f"s{i}") for i in range(3)], extra_tools=extras)
        with self.assertRaises(Crash):
            runtime.run("Kaynağı bul", request_id="stalled-crash")
        record = self.journal.find_request(self.workspace_id, "stalled-crash")
        self.journal = AgentRunStore(self.root / self.workspace_id)
        runtime, client = self.runtime([call("inspect_source", {"url": "https://example.org/reports"}), FINAL], extra_tools=extras)
        result = runtime.resume(record["run_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("web_search", {tool["function"]["name"] for tool in client.options[0]["tools"]})
        stalled = next(item["result"] for item in result["tool_results"] if item["call_id"] == "s2")
        self.assertEqual(len(stalled["warnings"]), 1)

    def test_clarification_continues_and_pending_calls_are_closed(self):
        first = call("ask_user", {"question": "Hangi dönemi inceleyelim?"})
        first["tool_calls"].append(call("describe", {"metric_id": "credit"}, "unused")["tool_calls"][0])
        runtime, _ = self.runtime([first])
        result = runtime.run("Krediyi incele")
        self.assertEqual(result["status"], "needs_input")
        runtime, client = self.runtime([FINAL])
        continued = runtime.run("2021 ilk çeyrek", conversation_id=result["conversation_id"])
        self.assertEqual(continued["status"], "completed")
        tool_messages = [m for m in client.requests[0] if m["role"] == "tool"]
        self.assertEqual({m["tool_call_id"] for m in tool_messages}, {"call-1", "unused"})

    def test_ask_user_after_a_produced_analysis_presents_it_not_needs_input(self):
        # If the model computes a valid analysis then asks a follow-up question, the run
        # must present the table (completed + analysis_id), not bury it behind needs_input.
        runtime, _ = self.runtime([call("execute", self.plan, "e1"),
                                   call("ask_user", {"question": "Tek 24 aylık seri mi iki sütun mu?"}, "q1")])
        result = runtime.run("Kredi tablosu oluştur")
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNotNone(result["analysis_id"])
        self.assertIn("mi", result["message"])
        self.assertTrue(any(w.get("code") == "CLARIFICATION_AFTER_RESULT" for w in result.get("warnings", [])))

    def test_custom_tools_publish_artifact_references(self):
        extra = {"inspect_source": {"schema": {"type": "function", "function": {"name": "inspect_source", "description": "Inspect registered document", "parameters": obj({"source_id": {"type": "string"}})}},
                 "handler": lambda args: {"status": "ok", "source_id": args["source_id"], "artifact_ref": "artifact-safe", "tables": []}}}
        runtime, _ = self.runtime([call("inspect_source", {"source_id": "source-safe"}), FINAL], extra_tools=extra)
        result = runtime.run("Belgeyi incele")
        self.assertIn({"kind": "artifact_ref", "id": "artifact-safe"}, result["artifacts"])
        runtime, _ = self.runtime([FINAL], extra_tools=extra)
        continuation = runtime.run("Bu kaynağa dön", conversation_id=result["conversation_id"])
        self.assertEqual(continuation["artifacts"], result["artifacts"])

    def test_conditional_row_request_blocks_unrequested_correlation_then_recovers_with_selection(self):
        initial_runtime, _ = self.runtime([call("execute", {
            **self.plan,
            "columns": [
                {"name": "credit", "metric_id": "credit", "dimensions": {}},
                {"name": "cpi", "metric_id": "cpi", "dimensions": {}},
            ],
        }), FINAL])
        initial = initial_runtime.run("Kredi ve endeks tablosunu getir")
        invoked = []
        statistics = StatisticsTools(self.store, self.workspace_id).extra_tools()
        original = statistics["analyze_relationship"]["handler"]
        statistics["analyze_relationship"]["handler"] = lambda args: invoked.append(args) or original(args)
        tools = {**statistics, **AnalysisSelectionTools(self.store, self.workspace_id).extra_tools()}
        runtime, _ = self.runtime([
            call("analyze_relationship", {
                "analysis_id": initial["analysis_id"], "x": "credit", "y": "cpi",
            }, "wrong-method"),
            call("select_analysis_rows", {
                "analysis_id": initial["analysis_id"],
                "filters": [{"column": "credit", "op": "gt", "value": 110}],
                "columns": ["period", "credit"],
            }, "right-method"),
            FINAL,
        ], extra_tools=tools)
        result = runtime.run("Kredi 110'un üstündeyken hangi aylar var?", conversation_id=initial["conversation_id"])
        self.assertEqual("completed", result["status"], result)
        self.assertEqual([], invoked)
        blocked = next(item["result"] for item in result["tool_results"] if item["call_id"] == "wrong-method")
        self.assertEqual("UNREQUESTED_STATISTICAL_METHOD", blocked["errors"][0]["code"])
        self.assertIn("Şubat 2021", result["message"])
        self.assertIn("Mart 2021", result["message"])
        self.assertNotIn("Korelasyon", result["message"])

    def test_explicit_relationship_request_is_not_stopped_by_the_drift_guard(self):
        initial_runtime, _ = self.runtime([call("execute", {
            **self.plan,
            "columns": [
                {"name": "credit", "metric_id": "credit", "dimensions": {}},
                {"name": "cpi", "metric_id": "cpi", "dimensions": {}},
            ],
        }), FINAL])
        initial = initial_runtime.run("Kredi ve endeks tablosunu getir")
        tools = StatisticsTools(self.store, self.workspace_id).extra_tools()
        runtime, _ = self.runtime([call("analyze_relationship", {
            "analysis_id": initial["analysis_id"], "x": "credit", "y": "cpi", "min_samples": 6,
        })], extra_tools=tools, max_repairs=0)
        result = runtime.run("Bu iki seri için Pearson korelasyonunu hesapla", conversation_id=initial["conversation_id"])
        self.assertEqual("INSUFFICIENT_SAMPLE", result["errors"][0]["code"])
        self.assertNotEqual("UNREQUESTED_STATISTICAL_METHOD", result["errors"][0]["code"])

    def test_conversation_cannot_cross_workspaces(self):
        runtime, _ = self.runtime([FINAL])
        result = runtime.run("merhaba")
        other = self.store.create_workspace(self.snapshot["snapshot_id"])
        runtime = AgentRuntime(self.store, other["workspace_id"], ScriptedClient([FINAL]), self.journal)
        with self.assertRaises(ValueError):
            runtime.run("merhaba", conversation_id=result["conversation_id"])


if __name__ == "__main__":
    unittest.main()
