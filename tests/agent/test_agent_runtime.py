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
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore


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
        runtime, client = self.runtime(responses, service=service, max_context_chars=24000)
        result = runtime.run("Veriyi bul ve hesapla")
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["analysis_updated"])
        self.assertNotIn("VERBOSE_METADATA_ONLY", json.dumps(client.requests))
        self.assertNotIn("LONG_SCOPE_DESCRIPTION", json.dumps(client.requests))
        compacted = False
        for messages in client.requests:
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
        events = self.journal.events(result["run_id"])
        full = next(e["payload"]["result"] for e in events if e["kind"] == "tool_result" and e["payload"].get("tool") == "discover")
        self.assertEqual(len(full["metrics"]), 25)
        self.assertEqual(full["metrics"][0]["notes"], cards[0]["notes"])
        runtime, continuation_client = self.runtime([FINAL], max_context_chars=24000)
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

    def test_custom_tools_publish_artifact_references(self):
        extra = {"inspect_source": {"schema": {"type": "function", "function": {"name": "inspect_source", "description": "Inspect registered document", "parameters": obj({"source_id": {"type": "string"}})}},
                 "handler": lambda args: {"status": "ok", "source_id": args["source_id"], "artifact_ref": "artifact-safe", "tables": []}}}
        runtime, _ = self.runtime([call("inspect_source", {"source_id": "source-safe"}), FINAL], extra_tools=extra)
        result = runtime.run("Belgeyi incele")
        self.assertIn({"kind": "artifact_ref", "id": "artifact-safe"}, result["artifacts"])
        runtime, _ = self.runtime([FINAL], extra_tools=extra)
        continuation = runtime.run("Bu kaynağa dön", conversation_id=result["conversation_id"])
        self.assertEqual(continuation["artifacts"], result["artifacts"])

    def test_conversation_cannot_cross_workspaces(self):
        runtime, _ = self.runtime([FINAL])
        result = runtime.run("merhaba")
        other = self.store.create_workspace(self.snapshot["snapshot_id"])
        runtime = AgentRuntime(self.store, other["workspace_id"], ScriptedClient([FINAL]), self.journal)
        with self.assertRaises(ValueError):
            runtime.run("merhaba", conversation_id=result["conversation_id"])


if __name__ == "__main__":
    unittest.main()
