"""Executable delivery obligations, grounded numbers and interruption recovery.

Models are scripted; network responses are isolated fixtures. The parser,
publication, lakehouse calculations, summary and chart tools are real.
"""
import copy
import json
import re
from unittest.mock import patch

import duckdb
import pytest

from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.context import _model_tool_result
from agentic_analytics.agent.delivery import (
    _requests_table, _source_scope_confirmation, _verified_policy_decision_confirmation,
)
from agentic_analytics.agent.runtime import AgentRuntime, _explicit_year_window, _requests_shared_scale
from agentic_analytics.agent.schemas import obj
from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.financial_import import FinancialImportTools
from agentic_analytics.agent.tools.datasets import DatasetTools
from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.store import LakehouseStore
from agentic_analytics.providers.mia import MiaError


def call(name, arguments, identifier=None):
    return {"content": None, "finish_reason": "stop", "tool_calls": [{
        "id": identifier or name, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]}


def final(text="Sonuç hazır."):
    return {"content": text, "finish_reason": "stop", "tool_calls": []}


def page_navigation_tools(executed):
    def search(args):
        executed.append(("find", copy.deepcopy(args)))
        return {"status": "ok", "source_id": args["source_id"], "complete": True,
                "searched_pages": list(range(1, 81)), "matches": [{"page": 47, "excerpt": "Credit risk disclosures"}],
                "suggested_inspection": {"source_id": args["source_id"], "page_numbers": [47]}}
    def inspect(args):
        executed.append(("read", copy.deepcopy(args)))
        return {"status": "ok", "source_id": args["source_id"], "source_url": "https://reports.example.org/report.pdf",
                "pages": [{"page": page, "text": "Credit risk disclosures. The requested breakdown is not provided."}
                          for page in args["page_numbers"]]}
    parameters = {
        "find_source_pages": obj({"source_id": {"type": "string"}, "query": {"type": "string"}}),
        "inspect_source": obj({"source_id": {"type": "string"}, "page_numbers": {"type": "array", "items": {"type": "integer"}}})}
    return {name: {"schema": {"type": "function", "function": {"name": name, "parameters": parameters[name]}}, "handler": handler}
            for name, handler in (("find_source_pages", search), ("inspect_source", inspect))}


def result_of(messages, name):
    return next(json.loads(message["content"]) for message in reversed(messages)
                if message.get("role") == "tool" and message["tool_call_id"] == name)


class Client:
    def __init__(self, responses):
        self.responses, self.requests, self.options = list(responses), [], []

    def chat(self, messages, **options):
        self.requests.append(copy.deepcopy(messages))
        self.options.append(copy.deepcopy(options))
        assert self.responses, "Unexpected provider request"
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


@pytest.fixture
def env(tmp_path):
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE SCHEMA catalog")
        db.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
        db.execute("CREATE TABLE observations(month VARCHAR,credit DOUBLE,profit DOUBLE)")
        db.executemany("INSERT INTO observations VALUES (?,?,?)", [
            ("2026-01", 100, 10), ("2026-02", 120, 15), ("2026-03", 150, 20), ("2026-04", 180, 25)])
        for name, kind in [("credit", "stock"), ("profit", "flow")]:
            binding = {"metric_id": name, "title": name, "source_system": "FIXTURE", "table": "observations",
                       "time_column": "month", "value_column": name, "filters": {}, "dimensions": {},
                       "native_frequency": "monthly", "kind": kind, "unit": "TRY", "scale": 1,
                       "currency": "TRY", "aggregation": "last" if kind == "stock" else "sum", "source_base": "",
                       "provenance_columns": [], "status": "ready", "notes": [], "contract_version": "test"}
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [name, json.dumps(binding)])
    store = LakehouseStore(tmp_path / "store")
    snapshot = store.publish_snapshot(database)
    workspace = store.create_workspace(snapshot["snapshot_id"])
    wid = workspace["workspace_id"]
    journal = AgentRunStore(tmp_path / "runs")
    extras = {**SummaryTools(store, wid).extra_tools(), **ChartTools(store, wid).extra_tools()}
    plan = {"start": "2026-01", "end": "2026-03", "frequency": "monthly",
            "columns": [{"name": "credit", "metric_id": "credit", "dimensions": {}}]}

    def runtime(responses, more=None, **kwargs):
        client = Client(responses)
        return AgentRuntime(store, wid, client, journal, extra_tools={**extras, **(more or {})}, **kwargs), client

    return store, wid, journal, plan, runtime


def test_page_search_rewrites_require_reading_and_recover_without_workspace_mutation(env):
    store, wid, journal, _, build = env
    executed = []
    search = lambda text, identifier: call("find_source_pages", {"source_id": "document-a", "query": text}, identifier)
    runtime, client = build([search("credit sectors", "find1"), search("sector allocation", "find2"),
        search("credit sectors", "blocked-find"), call("inspect_source", {"source_id": "document-a", "page_numbers": [47]}, "read"),
        search("credit sectors", "after-read"), final("Kaynağın ilgili bölümü okundu.")], more=page_navigation_tools(executed))
    result = runtime.run("Raporun ilgili kredi bölümünü incele.")
    assert result["status"] == "completed" and result["repairs"] == 1
    assert len([step for step in executed if step[0] == "find"]) == 3
    assert store.workspace(wid)["version"] == 0
    state = journal.get(result["run_id"])["state"]
    assert not state["unresolved_errors"]
    assert state["source_page_progress"]["document-a"]["candidate_read_after_search"]
    errors = [step for step in result["tool_results"] if step["call_id"] == "blocked-find"]
    assert errors[0]["result"]["errors"][0]["code"] == "SOURCE_READ_REQUIRED"
    events = [event for event in journal.events(result["run_id"]) if event["payload"].get("call_id") == "blocked-find"]
    assert [event["kind"] for event in events] == ["tool_result"]
    # Recovery survives a resumed persisted result without counting it twice.
    before = copy.deepcopy(state["source_page_progress"])
    runtime._dispatch(result["run_id"], {**state, "decisions": 5}, client.responses[0] if client.responses else
        search("credit sectors", "after-read")["tool_calls"][0])
    assert state["source_page_progress"] == before


def test_source_navigation_only_actual_new_candidate_read_clears_own_error(env):
    *_, build = env
    runtime, _ = build([])
    state = {}
    args = {"source_id": "a", "query": "credit", "start_page": 1}
    first = {"status": "ok", "source_id": "a", "complete": False, "next_start_page": 31,
             "matches": [{"page": 12, "excerpt": "credit"}]}
    runtime._track_source_pages(state, "find_source_pages", args, first, "f1")
    runtime._track_source_pages(state, "find_source_pages", args, first, "f2")
    assert runtime._source_read_required(state, "find_source_pages", {**args, "start_page": 31}) is None
    assert runtime._source_read_required(state, "find_source_pages", {"source_id": "b", "query": "credit"}) is None
    error = runtime._source_read_required(state, "find_source_pages", args)["errors"][0]
    state["unresolved_errors"] = {"find_source_pages": [error], "execute": [{"code": "INVALID_PLAN"}]}
    cases = [("inspect_source", {"status": "blocked", "source_id": "a", "pages": [{"page": 12, "text": "bad"}]}),
             ("inspect_source", {"status": "ok", "source_id": "a", "pages": [{"page": 12, "text": ""}]}),
             ("inspect_source", {"status": "ok", "source_id": "a", "pages": [{"page": 1, "text": "cover"}]}),
             ("inspect_source", {"status": "ok", "source_id": "b", "pages": [{"page": 12, "text": "other report"}]}),
             ("read_source_table", {"status": "ok", "source_id": "a", "page": 12, "rows": []})]
    for index, (name, payload) in enumerate(cases):
        runtime._track_source_pages(state, name, {"source_id": payload["source_id"]}, payload, f"bad{index}")
        assert state["unresolved_errors"]["find_source_pages"] == [error]
        assert runtime._source_read_required(state, "find_source_pages", args)
    read = {"status": "ok", "source_id": "a", "pages": [{"page": 12, "text": "Relevant source body"}]}
    runtime._track_source_pages(state, "inspect_source", args, read, "read1")
    assert state["unresolved_errors"] == {"execute": [{"code": "INVALID_PLAN"}]}
    runtime._track_source_pages(state, "find_source_pages", args, {**first, "matches": [{"page": 40}]}, "f3")
    runtime._track_source_pages(state, "find_source_pages", args, {**first, "matches": [{"page": 40}]}, "f4")
    runtime._track_source_pages(state, "inspect_source", args, read, "same-read-new-call")
    assert runtime._source_read_required(state, "find_source_pages", args)


def test_complete_search_and_relevant_read_preserve_cited_limitation_as_partial(env):
    store, wid, journal, _, build = env
    limitation = "İstenen dağılım raporda bulunmuyor. Bu nedenle sektör tablosu, oranlar ve grafik üretilemedi. [Kredi dipnotu](https://reports.example.org/report.pdf#page=47)."
    runtime, client = build([call("plan_task", {"deliverables": ["analysis", "chart", "sources"]}),
        call("find_source_pages", {"source_id": "a", "query": "credit sectors"}),
        call("inspect_source", {"source_id": "a", "page_numbers": [47]}), final(limitation)], more=page_navigation_tools([]))
    result = runtime.run("Raporda sektör dağılımını bul, tablo ve grafik göster.")
    assert result["status"] == "partial"
    assert result["message"].startswith(limitation)
    assert {error.get("deliverable") for error in result["errors"]} >= {"analysis", "chart"}
    assert not result["analysis_updated"] and not result["chart_updated"]
    assert store.workspace(wid)["version"] == 0
    assert len(client.requests) == 4 and not journal.get(result["run_id"])["state"].get("delivery_repairs")
    saved_state = journal.get(result["run_id"])["state"]
    assert not runtime._sourced_limitation({**saved_state, "analysis_updated": True}, limitation, result["errors"])


@pytest.mark.parametrize("case", ["search_only", "incomplete", "wrong_source", "wrong_page", "empty", "failed", "no_citation", "no_limitation"])
def test_limitation_never_promotes_navigation_or_failed_reads_to_evidence(env, case):
    *_, build = env
    runtime, _ = build([])
    state = {}
    runtime._track_source_pages(state, "find_source_pages", {"source_id": "a", "query": "sectors"},
        {"status": "ok", "source_id": "a", "complete": case != "incomplete", "matches": [{"page": 47}]}, "search")
    if case != "search_only":
        runtime._track_source_pages(state, "inspect_source", {}, {"status": "blocked" if case == "failed" else "ok",
            "source_id": "b" if case == "wrong_source" else "a", "source_url": "https://reports.example.org/report.pdf",
            "pages": [{"page": 1 if case == "wrong_page" else 47, "text": "" if case == "empty" else "Source body"}]}, "read")
    text = "Dağılım raporda bulunmuyor." if case != "no_limitation" else "İstenen analiz tamamlandı."
    if case != "no_citation":
        text += " [Kaynak](https://reports.example.org/report.pdf#page=47)"
    assert not runtime._sourced_limitation(state, text, [{"code": "TASK_DELIVERABLE_MISSING", "deliverable": "chart"}])


def test_focused_page_projection_retains_each_body_and_accurate_search_excerpt_lines():
    pages = [{"page": page, "text": "Repeating header " * 30 + f"Body of page {page}: source omission.", "text_truncated": False}
             for page in (41, 42, 43)]
    payload = {"status": "ok", "pages": pages, "text": "unhelpful combined header", "total_pages": 80}
    projected = _model_tool_result("inspect_source", payload)
    for page in projected["pages"]:
        assert f"Body of page {page['page']}" in page["text"]
        assert not page["text_truncated"]
    assert "text" not in projected
    payload["pages"][0]["text_truncated"] = True
    assert _model_tool_result("inspect_source", payload)["pages"][0]["text_truncated"]
    search = _model_tool_result("find_source_pages", {"matches": [{"page": 41, "line_start": 8, "line_end": 60,
        "excerpt": "one line\n" * 200}], "suggested_inspection": {"source_id": "a", "page_numbers": [41]}})
    match = search["matches"][0]
    assert match["excerpt_truncated"] and match["line_end"] == 8 + len(match["excerpt"].splitlines()) - 1
    assert search["suggested_inspection"]["page_numbers"] == [41]
    article = _model_tool_result("inspect_source", {"status": "ok", "pages": [],
        "text": "The institution's current ownership list is readable HTML."})
    assert article["text"] == "The institution's current ownership list is readable HTML."


def test_duplicate_read_guard_records_result_without_executing_a_third_read(env):
    _, _, journal, _, build = env
    executed = []
    runtime, _ = build([call("inspect_source", {"source_id": "a", "page_numbers": [47]}, f"read{n}") for n in range(3)],
                      more=page_navigation_tools(executed))
    result = runtime.run("Belgeyi incele.")
    assert result["status"] == "blocked" and result["errors"][0]["code"] == "NO_PROGRESS"
    assert len(executed) == 2
    events = [event for event in journal.events(result["run_id"]) if event["payload"].get("call_id") == "read2"]
    assert [event["kind"] for event in events] == ["tool_result"]


def test_repeated_candidate_read_gets_one_focused_cited_limitation_response(env):
    _, _, journal, _, build = env
    executed = []
    limitation = "İstenen dağılımı okunan dipnotta doğrulayamadım. Bu nedenle tablo ve grafik oluşturulamadı. [Dipnot](https://reports.example.org/report.pdf#page=47)."
    runtime, client = build([call("plan_task", {"deliverables": ["analysis", "chart", "sources"]}),
        call("find_source_pages", {"source_id": "a", "query": "credit sectors"}),
        *[call("inspect_source", {"source_id": "a", "page_numbers": [47]}, f"read{n}") for n in range(3)], final(limitation)],
        more=page_navigation_tools(executed))
    result = runtime.run("Kredi dağılımını kaynakta bul, tablo ve grafik göster.")
    assert result["status"] == "partial" and result["message"].startswith(limitation)
    assert len([item for item in executed if item[0] == "read"]) == 2
    assert "SOURCE_READ_REPEATED" in {error["code"] for error in result["errors"]}
    assert "Aynı belge okuması tekrarlandığı" in client.requests[-1][0]["content"]
    tool_messages = [message for message in client.requests[-1] if message.get("role") == "tool"]
    assert any("The requested breakdown is not provided" in message["content"] for message in tool_messages)
    state = journal.get(result["run_id"])["state"]
    assert state["source_read_repair_used"] and not state.get("source_read_repair_pending")
    bad = {**state, "unresolved_errors": {"execute": [{"code": "INVALID_PLAN"}]}}
    assert not runtime._sourced_limitation(bad, limitation, result["errors"])


def test_repeated_read_recovery_does_not_disable_the_loop_guard(env):
    _, _, journal, _, build = env
    executed = []
    runtime, _ = build([call("find_source_pages", {"source_id": "a", "query": "credit sectors"}),
        *[call("inspect_source", {"source_id": "a", "page_numbers": [47]}, f"read{n}") for n in range(4)]],
        more=page_navigation_tools(executed))
    result = runtime.run("Kredi dipnotunu incele.")
    assert result["status"] == "blocked" and result["errors"][0]["code"] == "NO_PROGRESS"
    assert len([item for item in executed if item[0] == "read"]) == 2
    failures = [item["result"]["errors"][0]["code"] for item in result["tool_results"] if item["result"].get("errors")]
    assert failures == ["SOURCE_READ_REPEATED", "NO_PROGRESS"]


def test_explicit_four_page_read_preserves_each_late_body_with_a_shared_budget():
    pages = [{"page": page, "text": "Header text " * 30 + f"Late body of page {page}. " * 150} for page in (41, 42, 43, 44)]
    projected = _model_tool_result("inspect_source", {"status": "ok", "selected_pages": [41, 42, 43, 44], "pages": pages})
    assert sum(len(page["text"]) for page in projected["pages"]) <= 12000
    assert all(f"Late body of page {page['page']}" in page["text"] for page in projected["pages"])
    assert all(page["text_truncated"] for page in projected["pages"])


@pytest.mark.parametrize("case", ["new_table", "new_page_body", "duplicate", "empty", "failed", "other_source", "other_page"])
def test_only_fresh_same_source_candidate_evidence_clears_repeated_read_failure(env, case):
    _, _, _, _, build = env
    runtime, _ = build([])
    state = {"source_read_repair_used": {"source_id": "a"}}
    runtime._track_source_pages(state, "find_source_pages", {"source_id": "a"},
        {"status": "ok", "source_id": "a", "matches": [{"page": 47}], "complete": True}, "find")
    original = {"status": "ok", "source_id": "a", "pages": [{"page": 47, "text": "A relevant source note."}]}
    runtime._track_source_pages(state, "inspect_source", {}, original, "first")
    state["unresolved_errors"] = {
        "inspect_source": [{"code": "SOURCE_READ_REPEATED", "source_id": "a"}, {"code": "SOURCE_READ_REPEATED", "source_id": "b"}],
        "execute": [{"code": "INVALID_PLAN"}]}
    result = copy.deepcopy(original)
    tool = "inspect_source"
    if case == "new_table":
        tool = "read_source_table"
        result = {"status": "ok", "source_id": "a", "page": 47, "table_id": "table_a", "rows": [{"label": "Domestic", "amount": "12"}]}
    elif case == "new_page_body":
        result["pages"][0]["text"] += " Newly read full text."
    elif case == "empty":
        result["pages"][0]["text"] = ""
    elif case == "failed":
        result["status"] = "blocked"
    elif case == "other_source":
        result["source_id"] = "b"
    elif case == "other_page":
        result["pages"][0]["page"] = 12
    runtime._track_source_pages(state, tool, {}, result, "next")
    remaining = state["unresolved_errors"]["inspect_source"]
    assert ({"code": "SOURCE_READ_REPEATED", "source_id": "a"} not in remaining) == (case in {"new_table", "new_page_body"})
    assert {"code": "SOURCE_READ_REPEATED", "source_id": "b"} in remaining
    assert state["unresolved_errors"]["execute"] == [{"code": "INVALID_PLAN"}]
    assert state["source_read_repair_used"] == {"source_id": "a"}


def test_final_numbers_come_from_saved_values_and_not_model_prose(env):
    store, wid, journal, plan, build = env
    runtime, client = build([call("execute", plan), final("Mart kredisi 999 TL ve büyüme yüzde 888.")])
    result = runtime.run("Kredi tablosunu göster")
    assert result["status"] == "completed"
    assert "150" in result["message"]
    assert "999" not in result["message"] and "888" not in result["message"]
    assert len(client.requests) == 2
    summaries = [step for step in result["tool_results"] if step["tool"] == "summarize_analysis"]
    assert len(summaries) == 1 and summaries[0]["automatic"]
    history = journal.get(result["run_id"])["state"]["messages"]
    assistant_prose = "\n".join(message.get("content") or "" for message in history
                                if message.get("role") == "assistant" and not message.get("tool_calls"))
    assert not re.search(r"(?<!\w)(?:999|888)(?!\w)", assistant_prose)
    assert store.workspace(wid)["version"] == 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_imported_source_value_survives_budget_without_claiming_an_analysis(env, corrupt):
    store, wid, journal, _, build = env
    docs = DocumentTools(store, wid)
    payload = b"<html><h1>Consolidated balance sheet</h1><p>Amounts in thousands of Turkish Lira (TRY)</p><table><tr><th>Line</th><th>Current</th></tr><tr><td></td><td>31 March 2026</td></tr><tr><td>Total assets</td><td>4,783,750,292</td></tr></table></html>"
    source = docs._register(payload, "financial-report.html", "text/html", "https://reports.example.org/financial-report.html")
    inspected = docs.inspect_source(source_id=source["source_id"])
    extras = {**docs.extra_tools(), **FinancialImportTools(docs).extra_tools()}
    original = extras["ingest_source_table"]["handler"]
    def ingest(args):
        result = original(args)
        assert result["status"] == "ok", result
        if corrupt:
            store.overlay_path(result["dataset_id"]).write_bytes(b"corrupt")
        return result
    extras["ingest_source_table"]["handler"] = ingest
    runtime, _ = build([call("ingest_source_table", {"source_id": source["source_id"],
        "table_id": inspected["tables"][0]["table_id"], "row_labels": ["Total assets"],
        "periods": ["2026-03-31"], "expected_version": 0, "measure_kind": "stock"})], more=extras, max_decisions=1)
    result = runtime.run("Kaynağı ekle, sektörle karşılaştır ve çubuk grafik göster.", source_ids=[source["source_id"]])
    assert result["status"] == ("blocked" if corrupt else "partial"), result
    assert result["analysis_id"] is None and not result["analysis_updated"] and not result["chart_updated"]
    assert store.workspace(wid)["analysis_head"] is None
    assert "Mevcut analiz korundu" not in result["message"]
    if corrupt:
        assert "4.783.750.292" not in result["message"]
    else:
        assert "4.783.750.292 bin TL" in result["message"]
        assert "31 Mart 2026" in result["message"]
        assert "https://reports.example.org/financial-report.html" in result["message"]
        assert "analiz ve grafik henüz tamamlanmadı" in result["message"]
        assert "milyon TL" not in result["message"]


def test_empty_workspace_search_failure_does_not_claim_preserved_analysis(env):
    _, _, _, _, build = env
    schema = {"type": "function", "function": {"name": "web_search", "parameters": obj({})}}
    runtime, _ = build([call("web_search", {})], more={"web_search": {"schema": schema,
        "handler": lambda args: {"status": "blocked", "errors": [{"code": "SEARCH_UNAVAILABLE", "message": "Unavailable"}]}}}, max_decisions=1)
    result = runtime.run("Kaynakları araştır")
    assert result["status"] == "blocked"
    assert "Henüz analiz tablosu veya grafik oluşturulmadı" in result["message"]
    assert "korundu" not in result["message"]


def _statement_recovery_fixture(env, *, amount="321,456,789", unit="BİN TÜRK LİRASI"):
    store, wid, _, _, _ = env
    docs = DocumentTools(store, wid)
    raw = f"""<html><h1>Konsolide Finansal Rapor</h1><h2>Konsolide Bilanço</h2><p>{unit}</p>
    <table><tr><th>Kalem</th><th>c1</th><th>c2</th><th>c3</th><th>p1</th><th>p2</th><th>p3</th></tr>
    <tr><td></td><td></td><td>30 Haziran 2027</td><td></td><td>31</td><td>Aralık 2026</td><td></td></tr>
    <tr><td></td><td>TP</td><td>YP</td><td>Toplam</td><td>TP</td><td>YP</td><td>Toplam</td></tr>
    <tr><td>VARLIKLAR TOPLAMI</td><td>200,000,000</td><td>121,456,789</td><td>{amount}</td>
    <td>150,000,000</td><td>100,000,000</td><td>250,000,000</td></tr></table></html>""".encode()
    source = docs._register(raw, "consolidated-financial-report.html", "text/html", "https://reports.example.org/statement")
    inspected = docs.inspect_source(source_id=source["source_id"])
    args = {"source_id": source["source_id"], "table_id": inspected["tables"][0]["table_id"],
            "row_start": 1, "row_limit": 20}
    extras = {**docs.extra_tools(), **FinancialImportTools(docs).extra_tools(), **DatasetTools(store, wid).extra_tools()}
    return docs, args, extras


def _provider_outage(_messages):
    raise MiaError("PROVIDER_UNAVAILABLE", "MIA bağlantısı tamamlanamadı.", retryable=True, attempts=3)


def test_provider_outage_delivers_verified_statement_value_and_chart_without_llm(env):
    store, wid, journal, _, build = env
    docs, args, extras = _statement_recovery_fixture(env)
    runtime, client = build([call("read_source_table", args), _provider_outage], more=extras)
    result = runtime.run("30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.", source_ids=[args["source_id"]])
    assert result["status"] == "partial", result
    assert result["analysis_updated"] and result["chart_updated"], result
    assert result["errors"][0]["code"] == "PROVIDER_UNAVAILABLE"
    assert len(client.requests) == 2  # recovery makes no provider call
    frame, manifest = store.load_analysis(result["analysis_id"])
    assert frame["period"].tolist() == ["2027-06-30"]
    assert frame["reported_amount"].tolist() == [321456789]
    assert "321.456.789" in result["message"]
    assert "250.000.000" not in result["message"]
    chart = ChartTools(store, wid).load_artifact(result["chart_id"])
    assert chart["analysis_id"] == result["analysis_id"]
    assert chart["series"][0]["values"] == [321456789]
    automatic = [item["tool"] for item in result["tool_results"] if item.get("automatic")]
    assert automatic == ["ingest_source_table", "aggregate_dataset", "create_chart"]
    imported = next(item["result"] for item in result["tool_results"] if item["tool"] == "ingest_source_table")
    assert imported["published_columns"]["amount"]["scale"] == 1000
    assert imported["provenance"]["cell_origins"][0]["amount"] == {"candidate_row": 3, "candidate_column": "c3"}
    assert any(e["kind"] == "delivery_repair" and e["payload"].get("reason") == "provider_outage_source_delivery"
               for e in journal.events(result["run_id"]))
    before = store.workspace(wid)
    assert runtime.resume(result["run_id"]) == result
    assert store.workspace(wid) == before


def test_bad_model_number_is_replaced_by_verified_statement_analysis_without_second_guess(env):
    store, wid, journal, _, build = env
    _, args, extras = _statement_recovery_fixture(env)
    runtime, client = build([
        call("read_source_table", args),
        final("Toplam aktifler 999.999.999 bin Türk lirasıdır."),
    ], more=extras)
    result = runtime.run(
        "30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.",
        source_ids=[args["source_id"]],
    )
    assert result["status"] == "completed", result
    assert result["analysis_updated"] and result["chart_updated"]
    assert len(client.requests) == 2
    assert "321.456.789" in result["message"]
    assert "999.999.999" not in result["message"]
    frame, _ = store.load_analysis(result["analysis_id"])
    assert frame["reported_amount"].tolist() == [321456789]
    assert any(e["kind"] == "delivery_repair" and e["payload"].get("reason") == "verified_source_delivery"
               for e in journal.events(result["run_id"]))


def test_verified_statement_recovery_supersedes_failed_guess_based_import(env):
    store, wid, _, _, build = env
    _, args, extras = _statement_recovery_fixture(env)
    guessed = {"source_id": args["source_id"], "table_id": args["table_id"],
               "row_labels": ["VARLIKLAR TOPLAMI"], "periods": ["2027-06-30"],
               "value_columns": ["c1", "c2", "c3"], "measure_kind": "stock", "expected_version": 0}
    runtime, client = build([
        call("read_source_table", args),
        call("ingest_source_table", guessed),
        final("Toplam aktifler 999.999.999 bin Türk lirasıdır."),
    ], more=extras)
    result = runtime.run(
        "30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.",
        source_ids=[args["source_id"]],
    )
    assert result["status"] == "completed", result
    assert result["analysis_updated"] and result["chart_updated"]
    assert len(client.requests) == 3
    assert "321.456.789" in result["message"] and "999.999.999" not in result["message"]
    assert not result.get("errors")
    assert store.load_analysis(result["analysis_id"])[0]["reported_amount"].tolist() == [321456789]


@pytest.mark.parametrize("case", ["no_date", "different_period", "wrong_scope", "unread_row", "ambiguous_number", "missing_unit", "preview_only"])
def test_provider_outage_never_invents_statement_chart_when_evidence_is_insufficient(env, case):
    store, wid, journal, _, build = env
    docs, args, extras = _statement_recovery_fixture(env,
        amount="321,456" if case == "ambiguous_number" else "321,456,789",
        unit="" if case == "missing_unit" else "BİN TÜRK LİRASI")
    prompt = "30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster."
    if case == "no_date":
        prompt = "Konsolide raporda toplam aktifleri göster."
    elif case == "different_period":
        prompt = prompt.replace("2027", "2028")
    elif case == "wrong_scope":
        prompt = prompt.replace("konsolide", "konsolide olmayan")
    elif case == "unread_row":
        args["row_limit"] = 2
    action = call("inspect_source", {"source_id": args["source_id"]}) if case == "preview_only" else call("read_source_table", args)
    runtime, client = build([action, _provider_outage], more=extras)
    result = runtime.run(prompt, source_ids=[args["source_id"]])
    assert not result["analysis_updated"] and not result["chart_updated"], result
    assert store.workspace(wid)["version"] == 0
    assert result["errors"][0]["code"] == "PROVIDER_UNAVAILABLE"
    assert len(client.requests) == 2


def test_provider_outage_recovery_preserves_previous_analysis(env):
    store, wid, _, plan, build = env
    docs, args, extras = _statement_recovery_fixture(env)
    initial, _ = build([call("execute", plan), final()])
    previous = initial.run("Kredi tablosunu göster")
    before = store.workspace(wid)
    runtime, _ = build([call("read_source_table", args), _provider_outage], more=extras)
    result = runtime.run("30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.", source_ids=[args["source_id"]])
    assert not result["analysis_updated"] and not result["chart_updated"]
    assert store.workspace(wid) == before
    assert result["active_analysis_id"] == previous["analysis_id"]
    assert not any(item.get("automatic") for item in result["tool_results"])


def test_explicit_resume_of_saved_source_only_outage_recovers_without_provider(env):
    store, wid, journal, _, build = env
    docs, args, extras = _statement_recovery_fixture(env)
    old_tools = {k:v for k,v in extras.items() if k != "aggregate_dataset"}
    old, _ = build([call("read_source_table", args), _provider_outage], more=old_tools)
    saved = old.run("30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.", source_ids=[args["source_id"]])
    assert not saved["analysis_updated"]
    runtime, client = build([], more=extras)
    result = runtime.resume(saved["run_id"], retry_terminal=True)
    assert result["run_id"] == saved["run_id"]
    assert result["status"] == "partial" and result["errors"] == saved["errors"]
    assert result["analysis_updated"] and result["chart_updated"]
    assert not client.requests
    assert store.load_analysis(result["analysis_id"])[0]["reported_amount"].tolist() == [321456789]


def test_provider_outage_after_statement_import_reuses_the_same_dataset(env):
    store, wid, _, _, build = env
    _, args, extras = _statement_recovery_fixture(env)
    ingest = {"source_id": args["source_id"], "table_id": args["table_id"],
              "row_labels": ["VARLIKLAR TOPLAMI"], "periods": ["2027-06-30"],
              "value_header": "Toplam", "measure_kind": "stock", "expected_version": 0}
    runtime, client = build([call("read_source_table", args),
                             call("ingest_source_table", ingest), _provider_outage], more=extras)
    result = runtime.run("30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.")
    assert result["status"] == "partial" and result["chart_updated"], result
    imports = [item["result"] for item in result["tool_results"] if item["tool"] == "ingest_source_table"]
    assert len(imports) == 2
    assert imports[0]["dataset_id"] == imports[1]["dataset_id"]
    assert store.load_analysis(result["analysis_id"])[0]["reported_amount"].tolist() == [321456789]
    assert len(client.requests) == 3


@pytest.mark.parametrize("problem", ["review_required", "hash_mismatch", "altered_cell"])
def test_provider_outage_recovery_does_not_bypass_source_identity_or_review(env, problem):
    from agentic_analytics.agent.source_recovery import statement_recovery_request
    store, wid, _, _, _ = env
    docs, args, _ = _statement_recovery_fixture(env)
    result = docs.read_source_table(**args)
    candidate = docs.review_candidate(args["source_id"], args["table_id"])
    if problem == "review_required":
        candidate["layout_review_required"] = True
    elif problem == "hash_mismatch":
        result["raw_sha256"] = "0" * 64
    else:
        result["rows"][-1]["values"]["c3"] = "999,999,999"
    with patch.object(DocumentTools, "review_candidate", return_value=candidate):
        recovered = statement_recovery_request(store, wid,
            "30 Haziran 2027 tarihli konsolide raporda toplam aktifleri göster.",
            [{"tool": "read_source_table", "result": result}])
    assert recovered is None
    assert store.workspace(wid)["version"] == 0


def test_source_navigation_reuses_cached_inspection_and_tracks_exact_selection(env):
    from agentic_analytics.agent.source_context import registered_sources
    store, wid, _, _, _ = env
    docs = DocumentTools(store, wid)
    source = docs._register(b"month,value\n2026-01,12345\n", "report.csv", "text/csv")
    docs.inspect_source(source_id=source["source_id"])
    state = {"selected_source_ids": [source["source_id"]], "messages": []}
    with patch.object(DocumentTools, "source", side_effect=AssertionError("No raw hash read for navigation")), patch.object(DocumentTools, "inspect_source", side_effect=AssertionError("No new inspection")):
        context = registered_sources(store, wid, state)
        assert registered_sources(store, wid, state) == context
    card = context["registered_sources"][0]
    assert card["source_id"] == source["source_id"] and card["navigation_only"]
    assert card["tables"] and "12345" not in json.dumps(context)


def test_table_claim_without_produced_result_is_not_completed(env):
    *_, build = env
    runtime, _ = build([final("Tablo hazır, 150 TL.")])
    result = runtime.run("Kredi tablosunu oluştur")
    assert result["status"] == "blocked"
    assert "TABLE_NOT_CREATED" in {error["code"] for error in result["errors"]}


def test_source_table_metadata_field_is_not_mistaken_for_a_materialized_table_request():
    assert not _requests_table(
        "Kod, ad, kaynak tablo, kapsam, birim ve doğal frekansı listele."
    )
    assert _requests_table("Kaynak tablosunu göster.")
    assert _requests_table("Sonucu tablo halinde göster.")


@pytest.mark.parametrize("repair_full", [False, True])
def test_capacity_failure_cannot_drop_valid_difference_series_and_complete(env, repair_full):
    store, wid, journal, plan, build = env
    plan = copy.deepcopy(plan)
    plan["columns"] = [{**plan["columns"][0],"name":f"credit_{index}"} for index in range(3)]
    differences = [f"delta_{index}" for index in range(3)]
    plan["operations"] = [{"op":"difference","column":f"credit_{index}","output":name,"periods":1}
                          for index,name in enumerate(differences)]
    extras = ChartTools(store,wid).extra_tools()
    extras["create_chart"]["schema"] = copy.deepcopy(extras["create_chart"]["schema"])
    # Reproduce a provider-visible capacity smaller than a valid saved selection.
    # The real chart backend can render all three through its default selection.
    extras["create_chart"]["schema"]["function"]["parameters"]["properties"]["columns"]["maxItems"] = 2
    def chart(columns, identifier):
        return lambda messages: call("create_chart",{
            "analysis_id":result_of(messages,"execute")["analysis_id"],"kind":"bar",
            **({"columns":columns} if columns is not None else {})},identifier)
    responses = [call("plan_task",{"deliverables":["analysis","chart"]}),call("execute",plan),
                 chart(differences,"capacity-failure"),chart(differences[:2],"smaller-chart"),final()]
    if repair_full:
        responses.append(chart(None,"complete-chart"))
    responses.append(final())
    runtime, client = build(responses,more=extras,max_decisions=8)
    result = runtime.run("Her grubun fark sütununu grafikte göster",conversation_id="chart-coverage")
    record = journal.get(result["run_id"])
    assert record["state"]["chart_capacity_selections"][result["analysis_id"]] == differences
    assert result["status"] == ("completed" if repair_full else "partial")
    if repair_full:
        assert set(differences) <= set(record["state"]["chart_columns"])
        # Coverage of only the requested differences is sufficient. Their
        # original source columns are not added to the retained obligation.
        assert not runtime._chart_coverage_errors({**record["state"],"chart_columns":differences})
    else:
        error = next(error for error in result["errors"] if error["code"] == "CHART_SELECTION_INCOMPLETE")
        assert error["missing_columns"] == [differences[-1]]
    # A later explicit user subset starts a new scope and is legitimate.
    client.responses.extend([call("create_chart",{"analysis_id":result["analysis_id"],
        "kind":"bar","columns":differences[:2]},"new-turn-chart"),final()])
    later = runtime.run("Şimdi yalnız ilk iki fark sütununu grafikte göster",conversation_id="chart-coverage")
    assert later["status"] == "completed"
    assert not journal.get(later["run_id"])["state"].get("chart_capacity_selections")


def test_nonpublishing_import_outcome_is_rechecked_and_never_hides_a_later_refusal(env):
    _, _, journal, _, build = env
    attempts = []
    def ingest(arguments):
        attempts.append(arguments)
        if len(attempts) == 1:
            return {"status":"ok","import_status":"unsupported_layout","publication_performed":False}
        return {"status":"blocked","errors":[{"code":"SEMANTICS_REVIEW_REQUIRED",
                "message":"Updated source evidence requires review before publication."}]}
    schema = lambda name: {"type":"function","function":{
        "name":name,"description":"Fixture source tool","parameters":obj({},[])}}
    extras = {
        "ingest_source_table":{"schema":schema("ingest_source_table"),"handler":ingest,"mutating":True},
        "inspect_source":{"schema":schema("inspect_source"),"handler":lambda args:{"status":"ok"}},
    }
    runtime, _ = build([call("plan_task",{"deliverables":["dataset"]}),
        call("ingest_source_table",{},"first-import"),call("inspect_source",{}),
        call("ingest_source_table",{},"second-import"),final("Kaynak inceleme bekliyor.")],more=extras,max_decisions=5)
    result = runtime.run("Kaynak tablosunu içeri al")
    assert len(attempts) == 2
    assert result["status"] == "blocked"
    assert "SEMANTICS_REVIEW_REQUIRED" in {error["code"] for error in result["errors"]}
    assert not journal.get(result["run_id"])["state"].get("successful_writes")


def _source_workflow_tools(attempts, compiler_results):
    """Instrument the handler boundary; parser/publication integration is tested below."""
    def handler(name, args):
        attempts.append((name, copy.deepcopy(args)))
        if name == "ingest_source_table":
            return copy.deepcopy(compiler_results.pop(0))
        if name == "prepare_source_table":
            return {"status":"ok", "source_id":args["source_id"], "table_id":"prepared",
                    "preparation":{"source_table_id":args["table_id"]}}
        return {"status":"ok", "dataset_id":"fixture_dataset", "source_id":args["source_id"]}
    return {name:{"schema":{"type":"function","function":{"name":name,
                    "parameters":obj({"source_id":{"type":"string"},"table_id":{"type":"string"}},
                                     ["source_id","table_id"])}},
                  "handler":lambda args, name=name:handler(name,args),
                  "mutating":name != "prepare_source_table"}
            for name in ("ingest_source_table","prepare_source_table","publish_selected_table")}


def test_semantic_refusal_never_exposes_or_executes_low_level_publication(env):
    _,_,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[{"status":"blocked","code":"TABLE_REVIEW_REQUIRED", "message":"Review source cells"}])
    args={"source_id":"report","table_id":"unreviewed"}
    runtime,client=build([call("ingest_source_table",args),call("publish_selected_table",args),final()],more=tools)
    result=runtime.run("Kaynağı içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts]==["ingest_source_table"]
    assert "SOURCE_WORKFLOW_REQUIRED" in {error["code"] for error in result["errors"]}
    assert all("publish_selected_table" not in {tool["function"]["name"] for tool in request["tools"]}
               for request in client.options)
    assert not journal.get(result["run_id"])["state"].get("advanced_source_tables")


@pytest.mark.parametrize("other", [{"source_id":"another","table_id":"table_a"},
                                   {"source_id":"report","table_id":"table_b"}])
def test_unsupported_permission_is_exact_and_unrelated_publication_keeps_denial(env, other):
    _,_,journal,_,build=env
    attempts=[]
    unsupported={"status":"ok","import_status":"unsupported_layout","publication_performed":False}
    tools=_source_workflow_tools(attempts,[unsupported])
    args={"source_id":"report","table_id":"table_a"}
    runtime,_=build([call("ingest_source_table",args),call("prepare_source_table",other,"denied"),
                     call("publish_selected_table",args),final()],more=tools)
    result=runtime.run("Kaynakları içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts]==["ingest_source_table","publish_selected_table"]
    error=next(error for error in result["errors"] if error["code"]=="SOURCE_WORKFLOW_REQUIRED")
    assert {key:error[key] for key in other}==other
    assert journal.get(result["run_id"])["state"]["advanced_source_tables"]=={"report":{"table_a":"table_a"}}


def test_refusal_revokes_prepared_descendants_and_prevents_cached_write_replay(env):
    _,_,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[
        {"status":"ok","import_status":"unsupported_layout","publication_performed":False},
        {"status":"blocked","code":"SEMANTICS_REVIEW_REQUIRED","message":"New evidence requires review"}])
    args={"source_id":"report","table_id":"table_a"}
    prepared={**args,"table_id":"prepared"}
    runtime,client=build([call("ingest_source_table",args,"unsupported"),call("prepare_source_table",args),
        call("publish_selected_table",prepared,"published"),call("ingest_source_table",args,"review"),
        call("publish_selected_table",prepared,"denied-replay"),final()],more=tools)
    result=runtime.run("Kaynağı içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts].count("publish_selected_table")==1
    assert "publish_selected_table" not in {tool["function"]["name"] for tool in client.options[4]["tools"]}
    assert not journal.get(result["run_id"])["state"].get("advanced_source_tables")


def test_unsupported_receipt_recovers_capability_once_and_new_turn_resets_it(env):
    _,wid,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[{"status":"ok","import_status":"unsupported_layout","publication_performed":False}])
    args={"source_id":"report","table_id":"table_a"}
    class Crash(BaseException):
        pass
    complete=journal.complete_step
    def interrupt(run_id,step_id,result):
        complete(run_id,step_id,result)
        if result.get("import_status")=="unsupported_layout":
            raise Crash()
    runtime,_=build([call("ingest_source_table",args)],more=tools)
    with patch.object(journal,"complete_step",side_effect=interrupt),pytest.raises(Crash):
        runtime.run("Kaynağı içeri al",conversation_id="fallback",request_id="fallback-resume")
    pending=journal.find_request(wid,"fallback-resume")
    runtime,client=build([call("prepare_source_table",args),
        call("publish_selected_table",{**args,"table_id":"prepared"}),final()],more=tools)
    result=runtime.resume(pending["run_id"])
    assert result["status"]=="completed"
    assert [name for name,_ in attempts].count("ingest_source_table")==1
    assert journal.get(result["run_id"])["state"]["advanced_source_tables"]["report"]["prepared"]=="table_a"
    client.responses.extend([call("publish_selected_table",{**args,"table_id":"prepared"},"new-turn"),final()])
    later=runtime.run("Kaynağı yeniden içeri al",conversation_id="fallback")
    assert later["status"]=="blocked"
    assert "SOURCE_WORKFLOW_REQUIRED" in {error["code"] for error in later["errors"]}
    assert [name for name,_ in attempts].count("publish_selected_table")==1


def test_cross_scope_disclosure_comes_from_saved_warning_and_survives_chart_edit(env):
    import pandas as pd
    store,wid,_,_,build=env
    saved=store.save_analysis(wid,pd.DataFrame({"period":["2026-01","2026-02"],"comparison":[1.5,2.0]}),
        {},{"warnings":[{"code":"cross_scope_comparison","column":"comparison", "scope_reason":"Fabricated 999 claim"}]},
        schema={"period":{"kind":"dimension"},"comparison":{"dtype":"float64","unit":"percent","scale":1,"kind":"ratio"}},
        expected_version=0)
    runtime,client=build([final("Bu resmi pazar payı 999.")])
    result=runtime.run("Mevcut analizin sonucunu açıkla",conversation_id="scope-warning")
    assert result["status"]=="completed"
    assert "seçilen pay ve paydanın sayısal karşılaştırmasıdır" in result["message"]
    assert "Kurum ve raporlama kapsamları farklıdır" not in result["message"]
    assert "resmî sektör veya pazar payı olduğu varsayılmaz" in result["message"] and "999" not in result["message"]
    client.responses.extend([call("create_chart",{"analysis_id":saved["analysis_id"],"kind":"line"}),final()])
    later=runtime.run("Çizgi grafiğini göster",conversation_id="scope-warning")
    assert later["status"]=="completed" and later["chart_updated"]
    assert "resmî sektör veya pazar payı olduğu varsayılmaz" in later["message"] and "999" not in later["message"]


def test_selected_pdf_footnote_is_rendered_only_with_matching_published_evidence(env):
    import pandas as pd
    store, wid, _, _, _ = env
    url = "https://reports.example.org/statement.pdf"
    provenance = {"source_id": "source-a", "source_url": url, "raw_sha256": "abc", "page": 96,
                  "preparation": {"source_table_id": "table-96"},
                  "source_scope_evidence": [{"basis": "selected_pdf_table_footnote", "source_id": "source-a",
                      "source_table_id": "table-96", "source_url": url, "raw_sha256": "abc", "page": 96,
                      "line_start": 12, "line_end": 12, "source_quote": "(*) Non-performing loans are not included.",
                      "section_quote": "Allocation of domestic and foreign loans"}]}
    analysis = store.save_analysis(wid, pd.DataFrame({"period": ["2026-03"], "credit": [100.0]}), {},
        {"sources": {"credit": {"binding": {"document_provenance": provenance}}}},
        schema={"period": {"kind": "dimension"}, "credit": {"kind": "stock", "dtype": "float64", "unit": "TRY", "scale": 1000}},
        expected_version=0)
    state = {"analysis_id": analysis["analysis_id"]}
    question = "Takipteki krediler bu tabloya dahil mi?"
    response = _source_scope_confirmation(store, wid, state, question)
    assert "takipteki krediler bu tutarlara dahil değil" in response
    assert f"{url}#page=96" in response
    assert _source_scope_confirmation(store, wid, state, "Tabloyu göster") is None
    provenance["source_scope_evidence"][0]["raw_sha256"] = "unrelated"
    bad = store.save_analysis(wid, pd.DataFrame({"period": ["2026-03"], "credit": [100.0]}), {},
        {"sources": {"credit": {"binding": {"document_provenance": provenance}}}},
        schema={"period": {"kind": "dimension"}, "credit": {"kind": "stock", "dtype": "float64", "unit": "TRY", "scale": 1000}},
        expected_version=1)
    assert _source_scope_confirmation(store, wid, {"analysis_id": bad["analysis_id"]}, question) is None


@pytest.mark.parametrize("text,expected", [
    ("Para birimi ve bin/milyon ölçeklerini eşitle, iki tutarı ve oranını hesapla", True),
    ("İki tutarı aynı para birimi ve ölçekte göster", True),
    ("Tutarları ortak ölçekte karşılaştır", True),
    ("Compare the amounts in the same currency and scale", True),
    ("Farklı ölçekleri aynı grafikte göster", False),
    ("Ölçeklerini eşitleme, özgün birimleri koru", False),
    ("Aynı ölçekte ne demek?", False),
    ("İki tutarı aynı ölçekte nasıl gösterebilirim?", False),
    ("How can I present amounts in the same scale?", False),
    ("Do not use the same scale", False),
])
def test_shared_scale_detector_is_an_explicit_command_boundary(text, expected):
    assert _requests_shared_scale(text) is expected


def _mixed_scale_plan(env, sector_currency="TRY"):
    """Replay the real rejected unit pairing through actual source publication."""
    store,wid,_,_,_=env
    docs=DocumentTools(store,wid)
    columns=[]
    for name,date,value,frequency,scale,currency in [
        ("company","2026-03-31",4783750292,"event",1000,"TRY"),
        ("sector","2026-03",49735194,"monthly",1000000,sector_currency)]:
        unit_quote=("thousand " if scale==1000 else "million ")+currency
        path=docs.upload_root/(name+".csv")
        path.write_text(f"date,value ({unit_quote})\n{date},{value}\n")
        source=docs.register_upload(path)
        table=docs.inspect_source(source_id=source["source_id"])["tables"][0]
        contract={"name":name,"frequency":frequency,"date_column":"date","key":["date"],"grain":["date"],
                  "columns":{"date":{"dtype":"date","unit":"calendar","kind":"dimension","nullable":False},
                             "value":{"dtype":"integer","unit":currency,"currency":currency,"scale":scale,"kind":"stock","nullable":False}}}
        result=docs.publish_selected_table(source["source_id"],table["table_id"],contract,store.workspace(wid)["version"],
            column_mapping=dict(zip(table["columns"],contract["columns"])),unit_evidence={"value":unit_quote})
        assert result["status"]=="ok",result
        columns.append({"name":name,"metric_id":f"overlay:{result['dataset_id']}:value",
                        "alignment":"period_end" if frequency=="event" else "native"})
    plan={"start":"2026-03","end":"2026-03","frequency":"monthly","columns":columns}
    if sector_currency=="TRY":
        plan["operations"]=[{"op":"ratio","column":"company","denominator":"sector","output":"comparison",
            "multiplier":100,"scope_policy":"explicit_comparison","scope_reason":"Explicit size comparison of different source populations"}]
    return plan


@pytest.mark.parametrize("repair", [False, True])
def test_actual_ratio_can_be_correct_while_common_scale_delivery_requires_saved_conversion(env, repair):
    store,wid,journal,_,build=env
    plan=_mixed_scale_plan(env)
    task={"deliverables":["analysis","chart"]}  # Actual failed run omitted normalization.
    def chart(messages):
        aid=result_of(messages,"execute")["analysis_id"]
        return call("create_chart",{"analysis_id":aid,"kind":"bar","columns":["comparison"]},"original-chart")
    def revise(messages):
        return call("revise_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "operations":[{"op":"scale","column":"company","output":"company_million","target_scale":1000000}]})
    def revised_chart(messages):
        return call("create_chart",{"analysis_id":result_of(messages,"revise_analysis")["analysis_id"],
            "kind":"bar","columns":["company_million","sector"]},"revised-chart")
    responses=[call("plan_task",task),call("execute",plan),chart,final("Tutarlar eşit ölçekte ve oran doğru.")]
    if repair:
        responses.extend([revise,revised_chart,final()])
    runtime,client=build(responses,max_decisions=len(responses))
    result=runtime.run("Para birimi ve bin/milyon ölçeklerini eşitle, iki tutarı ve oranını hesapla ve grafik göster")
    state=journal.get(result["run_id"])["state"]
    assert state["task_plan"]["normalization"]=={"same_unit_scale":True}
    original=next(item["result"] for item in result["tool_results"] if item["tool"]=="execute")
    old_frame,old_manifest=store.load_analysis(original["analysis_id"])
    assert old_frame.iloc[0]["comparison"]==pytest.approx(9.618441001758232)
    assert old_manifest["schema"]["company"]["scale"]==1000
    assert old_manifest["schema"]["sector"]["scale"]==1000000
    if repair:
        assert result["status"]=="completed",result
        current,manifest=store.load_analysis(result["analysis_id"])
        assert current.iloc[0]["company_million"]==pytest.approx(4783750.292)
        assert current.iloc[0]["company"]==4783750292
        assert manifest["schema"]["company_million"]["scale"]==manifest["schema"]["sector"]["scale"]==1000000
        assert state["chart_analysis_id"]==result["analysis_id"]!=original["analysis_id"]
        assert len(client.requests)==7 and state["delivery_repairs"]==1
    else:
        assert result["status"]=="partial"
        error=next(error for error in result["errors"] if error["code"]=="NORMALIZATION_NOT_SATISFIED")
        assert error["saved_units"]["company"]["scale"]==1000
        assert error["saved_units"]["sector"]["scale"]==1000000
        assert "revise_analysis" in error["message"]


def test_existing_explicit_scale_operations_satisfy_abstract_requirement_with_raw_columns_retained(env):
    _,_,journal,_,build=env
    plan=_mixed_scale_plan(env)
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("İki tutarı aynı para birimi ve ölçekte göster")
    assert result["status"]=="completed",result
    state=journal.get(result["run_id"])["state"]
    assert state.get("task_plan") is None
    assert state["request_normalization"]=={"same_unit_scale":True}


@pytest.mark.parametrize("repair", [False, True])
def test_prior_scaled_level_cannot_normalize_a_later_in_place_difference(env, repair):
    store,wid,journal,_,build=env
    plan={"start":"2026-02","end":"2026-03","frequency":"monthly",
          "columns":[{"name":"credit","metric_id":"credit"},{"name":"profit","metric_id":"profit"}],
          "operations":[
              {"op":"scale","column":"credit","output":"credit_thousand","target_scale":1000},
              {"op":"scale","column":"profit","output":"profit","target_scale":1000},
              {"op":"difference","column":"credit","output":"credit","periods":1}]}
    task={"deliverables":["analysis","chart"],"normalization":{
        "same_unit_scale":True,"columns":["credit","profit"]}}
    def chart(tool, columns, identifier):
        return lambda messages: call("create_chart",{
            "analysis_id":result_of(messages,tool)["analysis_id"],"kind":"bar","columns":columns},identifier)
    def revise(messages):
        return call("revise_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "operations":[{"op":"scale","column":"credit","output":"credit_change_thousand","target_scale":1000}]})
    responses=[call("execute",plan),call("plan_task",task),
               chart("execute",["credit","profit"],"original-chart"),final("Tutarlar aynı ölçekte.")]
    if repair:
        responses.extend([revise,chart("revise_analysis",["credit_change_thousand","profit"],"revised-chart"),final()])
    runtime,_=build(responses,max_decisions=len(responses))
    result=runtime.run("Kredi değişimi ile kârı aynı ölçekte karşılaştır ve grafikte göster")
    state=journal.get(result["run_id"])["state"]
    original=next(item["result"] for item in result["tool_results"] if item["tool"]=="execute")
    frame,manifest=store.load_analysis(original["analysis_id"])
    assert frame["credit"].tolist()==[20,30]
    assert frame["credit_thousand"].tolist()==pytest.approx([.12,.15])
    assert frame["profit"].tolist()==pytest.approx([.015,.02])
    assert manifest["schema"]["credit"]["scale"]==1
    assert manifest["schema"]["profit"]["scale"]==1000
    error=runtime._normalization_errors({"analysis_id":original["analysis_id"],"task_plan":task})[0]
    assert error["code"]=="NORMALIZATION_NOT_SATISFIED"
    assert "credit_thousand" not in error["saved_units"]  # The old level is a different quantity.
    if repair:
        assert result["status"]=="completed",result
        current,_=store.load_analysis(result["analysis_id"])
        assert current["credit_change_thousand"].tolist()==pytest.approx([.02,.03])
        assert current["credit"].tolist()==frame["credit"].tolist()
        assert state["chart_analysis_id"]==result["analysis_id"]!=original["analysis_id"]
        assert runtime._task_delivery_errors(state)==[]
        assert state["delivery_repairs"]==1
    else:
        assert result["status"]=="partial",result
        assert "NORMALIZATION_NOT_SATISFIED" in {item["code"] for item in result["errors"]}
        assert "NORMALIZATION_NOT_SATISFIED" in {item["code"] for item in runtime._task_delivery_errors(state)}


def test_saved_analysis_lineage_fulfils_sources_deliverable(env):
    store,_,_,_,build = env
    plan = {"start":"2026-01","end":"2026-03","frequency":"monthly",
            "columns":[{"name":"credit","metric_id":"credit","dimensions":{}}]}
    runtime,_ = build([call("execute", plan), final()])
    result = runtime.run("Konut kredisi stokunu kaynaklarıyla göster")
    analysis_id = result["analysis_id"]
    _, manifest = store.load_analysis(analysis_id)
    assert manifest["lineage"]["sources"]
    state = {"analysis_id": analysis_id, "analysis_updated": True,
             "task_plan": {"deliverables": ["analysis", "sources"]},
             "tool_results": result["tool_results"]}
    assert runtime._task_delivery_errors(state) == []


def test_explicit_historical_year_ranges_reject_a_saved_recent_analysis(env):
    _, _, _, _, build = env
    plan = {"start": "2020-01", "end": "2026-06", "frequency": "monthly",
            "columns": [{"name": "credit", "metric_id": "credit", "dimensions": {}}]}
    runtime, _ = build([call("execute", plan), final()])
    result = runtime.run("2020-2026 kredi stokunu aylık göster")
    request = "2010–2014 İMKB 100 ile 2014–2018 BIST 100 aylık kapanış verisini tek analizde göster"
    errors = runtime._analysis_request_scope_errors({"analysis_id": result["analysis_id"], "analysis_updated": True,
                                                      "request_message": request})
    assert _explicit_year_window(request) == (2010, 2018)
    assert errors[0]["code"] == "REQUESTED_PERIOD_MISMATCH"
    assert errors[0]["actual_start"] == "2020"


def test_single_date_delivery_has_each_presented_amount_once_and_sources_without_technical_boilerplate(env):
    from agentic_analytics.agent.delivery import _analysis_confirmation, _cell_confirmation
    store,wid,journal,_,build=env
    plan=_mixed_scale_plan(env)
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    def explain(column, identifier):
        def response(messages):
            return call("explain_value",{"analysis_id":result_of(messages,"execute")["analysis_id"],"column":column,"period":"2026-03"},identifier)
        return response
    runtime,_=build([call("execute",plan),explain("company","company-proof"),explain("sector","sector-proof"),final()])
    result=runtime.run("İki tutarı ortak ölçekte ve kaynaklarıyla göster")
    assert result["status"]=="completed"
    assert result["message"].count("4.783.750,292")==1
    assert result["message"].count("49.735.194")==1
    assert result["message"].count("9,618441")==1
    assert "4.783.750.292" not in result["message"]
    assert "company_million" not in result["message"] and "source_financial_facts" not in result["message"]
    assert "ilk değer" not in result["message"].casefold() and "son değer" not in result["message"].casefold()
    assert "bayt" not in result["message"] and "Kaynaklar:" in result["message"]
    state=copy.deepcopy(journal.get(result["run_id"])["state"])
    for item in state["tool_results"]:
        if item["tool"]=="summarize_analysis":
            item.pop("automatic",None)  # Durable interruption recovery uses the reserved call id.
    assert _analysis_confirmation(store,wid,state).count("4.783.750,292")==1
    proof=next(item["result"] for item in state["tool_results"] if item.get("call_id")=="company-proof")
    proof["lineage"]["document_provenance"].update(source_url="https://reports.example.org/statement.pdf",page=11)
    assert "https://reports.example.org/statement.pdf#page=11" in _cell_confirmation(store,wid,state)
    assert "s. 11" in _cell_confirmation(store,wid,state)


def test_explicit_grouped_summary_preserves_requested_totals_and_missing_warning(env):
    from agentic_analytics.agent.delivery import _analysis_confirmation
    import pandas as pd
    store,wid,_,_,_=env
    frame=pd.DataFrame({"period":["2026-01","2026-02","2026-01","2026-02"],"bank":["A","A","B","B"],"profit":[10,15,20,None]})
    schema={"profit":{"kind":"flow","status":"ready","unit":"TRY","currency":"TRY","scale":1,"additive_over_time":True}}
    lineage={"group_by":"bank","frequency":"monthly","groups":{json.dumps(group):{"sources":{"profit":{
        "binding":{"title":"Dönem kârı","dimension_labels":{"bank":{"A":"Grup A","B":"Grup B"}}},"dimensions":{"bank":group}}}} for group in ["A","B"]}}
    saved=store.save_analysis(wid,frame,{"frequency":"monthly"},lineage,schema=schema,expected_version=0)
    summary=SummaryTools(store,wid).summarize_analysis(saved["analysis_id"],statistics=["sum"])
    text=_analysis_confirmation(store,wid,{"analysis_id":saved["analysis_id"],"analysis_updated":True,
        "tool_results":[{"tool":"summarize_analysis","call_id":"requested-total","result":summary}]})
    assert "Grup A" in text and "Grup B" in text
    assert "25 TL" in text and "hesaplanamadı" in text and "eksik" in text.casefold()
    assert "Grup A) (Grup B" not in text


def test_unrequested_scale_conversion_is_not_enforced(env):
    *_,build=env
    plan=_mixed_scale_plan(env)
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("Özgün kaynak tutarlarını ve oranını göster")
    assert result["status"]=="completed",result


def test_common_scale_requirement_never_authorizes_implicit_currency_conversion(env):
    store,_,_,_,build=env
    plan=_mixed_scale_plan(env,sector_currency="USD")
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("Tutarları aynı para birimi ve ölçekte göster")
    assert result["status"]=="partial"
    error=next(error for error in result["errors"] if error["code"]=="NORMALIZATION_NOT_SATISFIED")
    assert error["currency_conversion_required"] and "not authorized" in error["message"]
    _,manifest=store.load_analysis(result["analysis_id"])
    assert manifest["schema"]["company"]["currency"]=="TRY" and manifest["schema"]["sector"]["currency"]=="USD"


def test_common_scale_contract_cannot_discard_columns_or_count_scaled_copy_twice(env):
    _,_,journal,_,build=env
    plan=_mixed_scale_plan(env)
    task={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["company","sector"]}}
    runtime,_=build([call("execute",plan),call("plan_task",task,"original"),call("plan_task",{
        "deliverables":["analysis"],"normalization":{"same_unit_scale":True}},"weaken")],max_repairs=0)
    result=runtime.run("Tutarları ortak ölçekte göster")
    assert result["status"]=="partial" and result["errors"][0]["code"]=="TASK_PLAN_LOCKED"
    assert journal.get(result["run_id"])["state"]["task_plan"]==task
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    duplicated={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["company","company_million"]}}
    runtime,_=build([call("execute",plan),call("plan_task",duplicated)],max_decisions=2,max_repairs=0)
    result=runtime.run("İki tutarı ortak ölçekte karşılaştır")
    assert result["status"]=="partial"
    assert "TASK_PLAN_INVALID_NORMALIZATION" in {error["code"] for error in result["errors"]}
    assert "NORMALIZATION_DUPLICATE_SOURCE" in result["errors"][0]["message"]


def test_normalization_does_not_lock_guessed_columns_before_analysis(env):
    _,_,journal,_,build=env
    task={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["guessed_a","guessed_b"]}}
    runtime,_=build([call("plan_task",task)],max_decisions=1,max_repairs=0)
    result=runtime.run("Tutarları ortak ölçekte göster")
    assert result["errors"][0]["code"]=="TASK_PLAN_REQUIRES_ANALYSIS"
    assert not journal.get(result["run_id"])["state"].get("task_plan")


def test_numeric_followup_reads_saved_analysis_without_mutating_or_trusting_prose(env):
    store, wid, journal, plan, build = env
    runtime, _ = build([call("execute", plan), final()])
    first = runtime.run("Kredi tablosunu göster")
    before = store.workspace(wid)
    runtime, client = build([final("Son kredi 999 TL oldu.")])
    result = runtime.run("Mevcut tablonun son değerini açıkla", conversation_id=first["conversation_id"])
    assert result["status"] == "completed"
    assert "150" in result["message"] and "999" not in result["message"]
    assert result["tool_results"] == [] and not result["analysis_updated"]
    assert store.workspace(wid) == before
    assert len(client.requests) == 1
    assert "999" not in journal.get(result["run_id"])["state"]["messages"][-1]["content"]


@pytest.mark.parametrize("repairs", [True, False])
def test_confident_dataset_numeric_answer_requires_tool_evidence(env, repairs):
    _, _, _, plan, build = env
    responses = [final("credit 999 TL")]
    responses += [call("execute", plan), final()] if repairs else [final("credit 999 TL")]
    runtime, client = build(responses, max_decisions=3 if repairs else 2)
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("credit")
    assert "999" not in result["message"]
    if repairs:
        assert result["status"] == "completed" and "150" in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "NUMERICAL_EVIDENCE_MISSING" in {error["code"] for error in result["errors"]}
    assert len(client.requests) == len(responses)


def test_direct_source_numeric_claims_must_exist_in_read_evidence(env):
    *_, build = env
    runtime, _ = build([])
    state = {"initial_candidates": {"status": "ok", "no_confident_match": True, "metrics": []},
        "task_plan": {"deliverables": ["sources"]}, "tool_results": [{"tool": "inspect_source", "result": {
            "status": "ok", "source_id": "source", "source_url": "https://example.org/decision-2025-15",
            "text": "6 Mart 2025 tarihinde bir hafta vadeli repo faizi yüzde 45'ten yüzde 42,5'e indirildi."}}]}
    assert runtime._numeric_evidence_errors(state, "Politika faizi yüzde 45'ten yüzde 42.5'e indirildi.") == []
    errors = runtime._numeric_evidence_errors(state, "Politika faizi yüzde 45'ten yüzde 41'e indirildi.")
    assert errors[0]["code"] == "EXTERNAL_NUMERIC_CLAIM_UNVERIFIED"
    assert errors[0]["unsupported_numbers"] == ["41"]


def test_grouping_separator_variants_match_but_unpublished_rounding_does_not(env):
    *_, build = env
    runtime, _ = build([])
    state = {"initial_candidates": {"status": "ok", "no_confident_match": True, "metrics": []},
        "task_plan": {"deliverables": ["sources"]}, "tool_results": [{"tool": "find_source_table_rows", "result": {
            "status": "ok", "source_id": "source", "raw_sha256": "a" * 64, "table_id": "table_p000011_text_001",
            "page": 11, "rows": [{"candidate_row": 31, "values": {"Line": "TOTAL ASSETS", "Total": "4,783,750,292"}}]}}]}
    assert runtime._numeric_evidence_errors(state, "Toplam aktifler 4.783.750.292 bin TL'dir.") == []
    errors = runtime._numeric_evidence_errors(state, "Toplam aktifler 4.783,8 milyon TL'dir.")
    assert errors[0]["code"] == "EXTERNAL_NUMERIC_CLAIM_UNVERIFIED"
    assert errors[0]["unsupported_numbers"] == ["4.783,8"]


def test_search_snippet_is_not_numeric_evidence(env):
    *_, build = env
    runtime, _ = build([])
    state = {"initial_candidates": {"status": "ok", "metrics": [{"metric_id": "credit"}]},
        "task_plan": {"deliverables": ["sources"]}, "tool_results": [{"tool": "web_search", "result": {
            "status": "ok", "results": [{"url": "https://example.org/result", "snippet": "Invented value 999"}]}}]}
    errors = runtime._numeric_evidence_errors(state, "Doğrulanan değer 999 TL'dir.")
    assert errors[0]["code"] == "NUMERICAL_EVIDENCE_MISSING"


def test_policy_decision_cannot_be_satisfied_by_a_meeting_summary(env):
    *_, build = env
    runtime, _ = build([])
    state = {"messages": [{"role": "user", "content": "TCMB 6 Mart 2025 Para Politikası Kurulu faiz kararını göster"}],
        "tool_results": [{"tool": "inspect_source", "result": {"status": "ok", "source_id": "summary",
            "source_url": "https://www.tcmb.gov.tr/summary", "document_type": "meeting_summary",
            "text": "Toplantı özeti içinde yüzde 42,5 oranı değerlendirildi."}}]}
    errors = runtime._source_semantic_errors(state, "Politika faizi yüzde 42,5 oldu.")
    assert errors[0]["code"] == "SOURCE_DOCUMENT_TYPE_MISMATCH"


def test_policy_rate_answer_must_name_the_one_week_repo_instrument(env):
    *_, build = env
    runtime, _ = build([])
    state = {"messages": [{"role": "user", "content": "TCMB 6 Mart 2025 Para Politikası Kurulu faiz kararını göster"}],
        "tool_results": [{"tool": "inspect_source", "result": {"status": "ok", "source_id": "decision",
            "source_url": "https://www.tcmb.gov.tr/decision", "document_type": "policy_decision",
            "text": "Bir hafta vadeli repo ihale faiz oranı yüzde 45'ten yüzde 42,5'e indirildi."}}]}
    errors = runtime._source_semantic_errors(state, "Ticari kredi faizi yüzde 42,5 oldu.")
    assert errors[0]["code"] == "POLICY_RATE_INSTRUMENT_MISMATCH"
    assert runtime._source_semantic_errors(
        state, "Politika faizi olan bir hafta vadeli repo ihale faizi yüzde 45'ten yüzde 42,5'e indirildi.") == []


@pytest.mark.parametrize('explanation', [
    'Bu konut kredisi faizi değildir.',
    'Bu ticari kredi faiz oranı değildir.',
    'Bu ihtiyaç kredisi oranı değil, politika faizidir.',
    'Bu oran konut kredisi faizleriyle karıştırılmamalıdır.',
    'Bu oran ticari kredi faiz oranı olarak yorumlanmamalıdır.',
    'This is not a mortgage rate.',
    'A mortgage rate is not the policy-rate instrument.',
])
def test_policy_rate_explicit_loan_disclaimer_is_not_a_mismatch(env, explanation):
    *_, build = env
    runtime, _ = build([])
    state = {'messages': [{'role': 'user', 'content': 'TCMB Para Politikası Kurulu faiz kararını göster'}],
             'tool_results': [{'tool': 'inspect_source', 'result': {
                 'status': 'ok', 'source_id': 'decision', 'document_type': 'policy_decision',
                 'text': 'Bir hafta vadeli repo ihale faiz oranı yüzde 45’ten yüzde 42,5’e indirildi.'}}]}
    answer = 'Bir hafta vadeli repo ihale faiz oranı yüzde 42,5 oldu. ' + explanation
    assert runtime._source_semantic_errors(state, answer) == []
    # A correct disclaimer cannot hide a separate incorrect affirmative claim.
    errors = runtime._source_semantic_errors(state, answer + ' Konut kredisi faizi yüzde 42,5 oldu.')
    assert errors[0]['code'] == 'POLICY_RATE_INSTRUMENT_MISMATCH'


@pytest.mark.parametrize("text, previous, current", [
    ("Bir hafta vadeli repo ihale faiz oranının yüzde 38'den yüzde 35,5'e indirilmesine karar verilmiştir.",
     "%38", "%35,5"),
    ("The one-week repo auction rate was reduced from 6.25 to 5.75 percent.", "%6,25", "%5,75"),
])
def test_policy_decision_receipt_builds_a_grounded_presentation_table(text, previous, current):
    state = {"tool_results": [{"tool": "research_web", "result": {"status": "ok", "sources": [{
        "source_id": "decision", "source_url": "https://centralbank.example.org/decision-27",
        "document_type": "policy_decision", "reporting_period": "2027-04-08", "content": text,
    }]}}]}
    receipt = _verified_policy_decision_confirmation(state)
    assert "| Öğe | Değer |" in receipt
    assert previous in receipt and current in receipt
    assert "2027-04-08" in receipt
    assert "https://centralbank.example.org/decision-27" in receipt


def test_policy_decision_receipt_rejects_snippets_and_wrong_document_types():
    snippet = {"tool_results": [{"tool": "web_search", "result": {"status": "ok", "results": [{
        "url": "https://example.org/search", "snippet": "one-week repo rate from 9 to 8"
    }]}}]}
    assert _verified_policy_decision_confirmation(snippet) == ""
    wrong_type = {"tool_results": [{"tool": "inspect_source", "result": {"status": "ok",
        "source_url": "https://example.org/news", "document_type": "news",
        "text": "The one-week repo rate moved from 9 to 8."}}]}
    assert _verified_policy_decision_confirmation(wrong_type) == ""


def test_policy_receipt_replaces_unsupported_model_draft_before_delivery_grading(env):
    *_, build = env
    source = {"status": "ok", "sources": [{
        "source_id": "decision", "source_url": "https://centralbank.example.org/decision-27",
        "document_type": "policy_decision", "reporting_period": "2027-04-08",
        "content": "Bir hafta vadeli repo ihale faiz oranının yüzde 38'den yüzde 35,5'e indirilmesine karar verilmiştir.",
    }]}
    bad_draft = ("Bir hafta vadeli repo ihale faiz oranı yüzde 38'den yüzde 35,5'e indirildi; "
                 "2,5 puan düştü ve konut kredisi faizi yüzde 35,5 oldu.")
    runtime, client = build([
        call("research_web", {"query": "official policy decision"}), final(bad_draft),
    ], more=web_tool(lambda args: source))
    result = runtime.run(
        "8 Nisan 2027 politika kararını resmî kaynaktan bul; önceki ve yeni oranı kısa tabloda göster."
    )
    assert result["status"] == "completed", result
    assert len(client.requests) == 2
    assert "%38" in result["message"] and "%35,5" in result["message"]
    assert "2,5 puan" not in result["message"]
    assert "konut kredisi faizi yüzde 35,5" not in result["message"]
    assert not result.get("errors")


def test_consolidated_total_assets_requires_scoped_page_table_and_unit_proof(env):
    *_, build = env
    runtime, _ = build([])
    request = "Garanti BBVA 31 Mart 2026 konsolide finansal raporundaki toplam aktifleri doğrula"
    base = {"messages": [{"role": "user", "content": request}], "tool_results": [{"tool": "find_source_table_rows",
        "result": {"status": "ok", "source_id": "source", "source_url": "https://example.org/report.pdf",
            "raw_sha256": "a" * 64, "document_type": "financial_report", "reporting_period": "2026-03-31",
            "consolidation_scope": "solo", "page": 11, "table_id": "table_11", "unit_caption": "THOUSANDS OF TL",
            "rows": [{"candidate_row": 20, "values": {"Line": "TOTAL ASSETS", "Total": "4783750292"}}]}}]}
    errors = runtime._source_semantic_errors(base, "Toplam aktifler 4.783.750.292 bin TL'dir.")
    assert errors[0]["code"] == "SOURCE_SCOPE_MISMATCH"
    valid = copy.deepcopy(base)
    valid["tool_results"][0]["result"]["consolidation_scope"] = "consolidated"
    assert runtime._source_semantic_errors(valid, "Toplam aktifler 4.783.750.292 bin TL'dir.") == []
    del valid["tool_results"][0]["result"]["unit_caption"]
    errors = runtime._source_semantic_errors(valid, "Toplam aktifler 4.783.750.292 bin TL'dir.")
    assert errors[0]["code"] == "FINANCIAL_REPORT_CELL_PROOF_INCOMPLETE"


def test_consolidated_total_assets_accepts_adjacent_split_label_cells(env):
    *_, build = env
    runtime, _ = build([])
    state = {"messages": [{"role": "user", "content":
        "Örnek Banka 30 Haziran 2027 konsolide finansal raporundaki toplam aktifleri doğrula"}],
        "tool_results": [{"tool": "find_source_table_rows", "result": {
            "status": "ok", "source_id": "source", "source_url": "https://example.org/report.pdf",
            "raw_sha256": "b" * 64, "document_type": "financial_report", "reporting_period": "2027-06-30",
            "consolidation_scope": "consolidated", "page": 9, "table_id": "table_9",
            "unit_caption": "BİN TÜRK LİRASI", "rows": [{"candidate_row": 41, "values": {
                "label_1": "VARLIKLA", "label_2": "R TOPLAMI", "Toplam": "321456789"}}]}}]}
    assert runtime._source_semantic_errors(state, "Toplam aktifler 321.456.789 bin TL'dir.") == []


def test_educational_numeric_example_is_labeled_and_not_mistaken_for_source_data(env):
    *_, build = env
    runtime, _ = build([call("plan_task", {"deliverables":["explanation"]}), final("Varsayımsal 100 TL ve 120 TL iki örnek değerdir.")])
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("Kredi stok kavramını varsayımsal sayılarla açıkla")
    assert result["status"] == "completed"
    assert "100" in result["message"] and "kaynak verilerden hesaplanmış bir sonuç değildir" in result["message"]
    assert not result["analysis_updated"]


@pytest.mark.parametrize("repairs", [True, False])
def test_metadata_description_cannot_ground_an_invented_observation(env, repairs):
    _, _, _, plan, build = env
    responses = [call("describe", {"metric_id":"credit"}), final("credit 999 TL")]
    responses += [call("execute", plan), final()] if repairs else [final("credit 999 TL")]
    runtime, client = build(responses, max_decisions=len(responses))
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("credit")
    assert "999" not in result["message"]
    if repairs:
        assert result["status"] == "completed" and "150" in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "NUMERICAL_EVIDENCE_MISSING" in {error["code"] for error in result["errors"]}
    assert len(client.requests) == len(responses)


def test_malformed_json_tool_history_is_sanitized_only_for_provider_retry(env):
    _, _, journal, plan, build = env
    malformed = call("describe", {})
    malformed["tool_calls"][0]["function"]["arguments"] = '{"metric_id":'
    def repair(messages):
        for message in messages:
            for item in message.get("tool_calls", []):
                assert isinstance(json.loads(item["function"]["arguments"]),dict)
        assert result_of(messages,"describe")["errors"][0]["code"] == "INVALID_TOOL_ARGUMENTS"
        return call("describe",{"metric_id":"credit"},"describe-fixed")
    runtime, _ = build([malformed,repair,call("execute",plan),final()])
    result = runtime.run("Kredi tablosunu göster")
    assert result["status"] == "completed"
    calls = [item for message in journal.get(result["run_id"])["state"]["messages"] for item in message.get("tool_calls", [])]
    assert calls[0]["function"]["arguments"] == '{"metric_id":'


def test_fatal_tool_failure_keeps_grounded_partial_analysis_receipt(env):
    _, _, _, plan, build = env
    invalid = {**plan,"operations":[{"op":"deflate","column":"credit","index":"credit","output":"bad","base_period":"2026-01"}]}
    runtime, _ = build([call("execute",plan,"good"),call("execute",invalid,"bad")],max_repairs=0)
    result = runtime.run("Hesapla")
    assert result["status"] == "partial" and result["analysis_updated"]
    assert "150" in result["message"]
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}


def test_ask_user_after_analysis_cannot_bypass_requested_chart(env):
    _, _, _, plan, build = env
    runtime, _ = build([call("execute", plan), call("ask_user", {"question": "Başka bir şey ister misiniz?"})])
    result = runtime.run("Kredi tablosunu oluştur ve çizgi grafik çiz")
    assert result["status"] == "partial"
    assert result["analysis_updated"] and not result["chart_updated"]
    assert "CHART_NOT_CREATED" in {error["code"] for error in result["errors"]}


def test_ask_user_pending_calls_are_closed_before_automatic_summary(env):
    _, _, journal, plan, build = env
    question = call("ask_user", {"question": "Başka bir dönem de gerekli mi?"})
    question["tool_calls"].extend(call("describe", {"metric_id": "credit"}, "unused")["tool_calls"])
    runtime, _ = build([call("execute", plan), question])
    result = runtime.run("Kredi tablosunu göster")
    history = journal.get(result["run_id"])["state"]["messages"]
    pending = set()
    for message in history:
        if message.get("role") == "assistant":
            assert not pending, "An assistant message orphaned an earlier tool call"
            pending.update(tool["id"] for tool in message.get("tool_calls", []))
        elif message.get("role") == "tool":
            pending.remove(message["tool_call_id"])
    assert not pending


def test_automatic_summary_recovers_after_interruption_without_provider_replay(env):
    _, _, journal, plan, build = env

    class Crash(BaseException):
        pass

    original = journal.complete_step
    def interrupt(run_id, step_id, result):
        if "runtime_summary_" in step_id:
            raise Crash()
        return original(run_id, step_id, result)

    runtime, client = build([call("execute", plan), final("Untrusted 999.")])
    with patch.object(journal, "complete_step", side_effect=interrupt), pytest.raises(Crash):
        runtime.run("Kredi tablosunu göster", request_id="interrupted-summary")
    pending = journal.find_request(env[1], "interrupted-summary")
    assert pending["state"]["delivery_pending"]
    runtime, resumed_client = build([])
    result = runtime.resume(pending["run_id"])
    assert result["status"] == "completed"
    assert result["decisions"] == 2 and len(client.requests) == 2
    assert not resumed_client.requests
    assert "150" in result["message"] and "999" not in result["message"]


def web_tool(handler):
    return {"research_web": {"schema": {"type": "function", "function": {"name": "research_web",
            "parameters": obj({"query": {"type": "string"}})}}, "handler": handler}}


def test_web_failure_can_recover_and_success_does_not_disable_tools(env):
    _, _, _, plan, build = env
    attempts = []
    def research(args):
        attempts.append(args["query"])
        if len(attempts) == 1:
            return {"status": "unavailable", "code": "NO_READABLE_SOURCES", "message": "No readable source"}
        return {"status": "ok", "sources": [{"url": "https://example.org/report", "title": "Report", "content": "2026 report."}]}
    runtime, client = build([
        call("research_web", {"query": "first"}, "first"), call("research_web", {"query": "refined"}, "second"),
        call("execute", plan), lambda messages: call("create_chart", {
            "analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"}), final()], more=web_tool(research))
    result = runtime.run("Kaynak araştır, kredi tablosunu oluştur ve çizgi grafik çiz")
    assert result["status"] == "completed" and result["chart_updated"]
    assert attempts == ["first", "refined"]
    assert all(option["tools"] for option in client.options)


def test_web_source_cannot_erase_failed_calculation(env):
    _, _, _, plan, build = env
    invalid = {**plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2026-01", "output": "real"}]}
    research = lambda args: {"status": "ok", "sources": [{"url": "https://example.org/report", "content": "Report available."}]}
    runtime, _ = build([call("execute", invalid), call("research_web", {"query": "report"}), final()], more=web_tool(research))
    result = runtime.run("Analiz yap ve kaynak bul")
    assert result["status"] == "partial"
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}
    assert not result["analysis_updated"]


def test_source_backed_ownership_answer_keeps_complete_list_and_recommendation(env):
    store, wid, journal, plan, build = env
    # A follow-up can research ownership while an earlier quantitative analysis
    # remains active; it must not be replaced by that analysis or a page excerpt.
    previous, _ = build([call("execute", plan), final()])
    prior = previous.run("Kredi tablosunu göster")
    names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota"]
    ownership = {"url": "https://example.org/shareholders", "title": "Example ortaklık yapısı",
                 "content": "Example ortakları. " + "Kaynak açıklaması. " * 40 + ", ".join(names)}
    cover = {"url": "https://example.org/report.pdf", "title": "Report",
             "content": "Financial report cover", "tables": [{"columns": ["Cover"], "preview": [{"Cover": None}]}]}
    response = ("Example ortakları: " + ", ".join(names) + ". "
                "[Resmi ortaklık sayfası](https://example.org/shareholders)\n\n"
                "Karşılaştırmayı Alpha ve Beta ile genişletmeyi öneriyorum. "
                "Her biri için aynı dönem ve konsolidasyon kapsamındaki raporu inceleyelim.")
    runtime, _ = build([call("research_web", {"query": "Example shareholders"}), final(response)],
                       more=web_tool(lambda args: {"status": "ok", "sources": [ownership, cover]}))
    result = runtime.run("Example'nin ortakları kimler, karşılaştırmaya hangilerini ekleyelim?",
                         conversation_id=prior["conversation_id"])
    assert result["status"] == "completed"
    assert result["message"].startswith(response)
    assert all(name in result["message"] for name in names)
    assert result["message"].count(ownership["url"]) == 1
    assert "Kaynak metninden" not in result["message"] and "None" not in result["message"]
    assert "150" not in result["message"] and not result["analysis_updated"]
    assert store.workspace(wid)["analysis_head"] == prior["analysis_id"]
    assert result["tool_results"][0]["result"]["sources"][1]["tables"] == cover["tables"]
    delivered = journal.get(result["run_id"])["state"]["messages"][-1]["content"]
    assert response in delivered


@pytest.mark.parametrize("repair", [True, False])
def test_unsolicited_ownership_percentages_are_repaired_or_not_delivered(env, repair):
    _, _, journal, _, build = env
    source = {"url": "https://example.org/owners", "title": "Example ortakları",
              "content": "Example ortakları: Alpha yüzde 9,09; Beta yüzde 18,18."}
    bad = "Example ortakları Alpha (%9,09) ve Beta (%18,18). Alpha'yı öneririm: en büyük ortaklardan (%18,18)."
    corrected = ("Example ortakları Alpha ve Beta. Alpha'nın mevcut analizle aynı dönem ve konsolidasyon "
                 "kapsamındaki raporunu bularak karşılaştırmaya eklemeyi öneriyorum.")
    runtime, client = build([call("research_web", {"query": "Example shareholders"}), final(bad),
                            final(corrected if repair else bad)],
                            more=web_tool(lambda args: {"status": "ok", "sources": [source]}))
    result = runtime.run("Example'nin ortakları kimler, hangisini karşılaştırmaya ekleyelim?")
    assert len(client.requests) == 3
    assert "UNSOLICITED_OWNERSHIP_PERCENTAGES" in json.dumps(client.requests[2])
    assert "9,09" not in result["message"] and "18,18" not in result["message"]
    assert result["tool_results"][0]["result"]["sources"][0] == source
    history = journal.get(result["run_id"])["state"]["messages"]
    delivered_prose = [item["content"] for item in history if item.get("role") == "assistant" and not item.get("tool_calls")]
    assert bad not in delivered_prose
    if repair:
        assert result["status"] == "completed"
        assert result["message"].startswith(corrected)
        assert source["url"] in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "UNSOLICITED_OWNERSHIP_PERCENTAGES" in {error["code"] for error in result["errors"]}


@pytest.mark.parametrize("question", ["Example'nin ortakları ve payları nedir?", "Who are Example's shareholders and their percentages?"])
def test_requested_ownership_percentages_are_not_silently_removed(env, question):
    *_, build = env
    source = {"url": "https://example.org/owners", "title": "Example ortakları",
              "content": "Example shareholders: Alpha 40%, Beta 60%."}
    response = "Example ortakları Alpha (%40) ve Beta (%60)."
    runtime, client = build([call("research_web", {"query": "Example shareholders"}), final(response)],
                           more=web_tool(lambda args: {"status": "ok", "sources": [source]}))
    result = runtime.run(question)
    assert result["status"] == "completed" and result["message"].startswith(response)
    assert len(client.requests) == 2


def test_percent_encoded_citation_and_nonownership_analysis_do_not_trigger_ownership_gate(env):
    from agentic_analytics.agent.runtime import _ownership_percentage_errors, _requests_ownership_percentages
    state = {"external_facts_required": True, "ownership_percentages_requested": False}
    assert not _ownership_percentage_errors(state, "[Ortaklar](https://example.org/ortak%20listesi?name=%25)")
    assert not _ownership_percentage_errors({"external_facts_required": False}, "Büyüme %20.")
    assert not _requests_ownership_percentages("Example'nin ortaklarını paylaşır mısın? https://example.org/ortak%20listesi")
    for claim in ("Alpha yüzde 18,18", "Alpha 18.18 percent", "Alpha %18,18", "Alpha 18,18%"):
        assert _ownership_percentage_errors(state, claim)[0]["code"] == "UNSOLICITED_OWNERSHIP_PERCENTAGES"


def test_research_links_cannot_replace_verified_arithmetic_with_model_claims(env):
    _, _, _, plan, build = env
    research = lambda args: {"status": "ok", "sources": [{
        "url": "https://example.org/report", "title": "Report", "content": "Model sees unrelated 777 TL here."}]}
    runtime, _ = build([call("research_web", {"query": "report"}), call("execute", plan),
                         final("Kredi 999 TL, büyüme yüzde 888.")], more=web_tool(research))
    result = runtime.run("Kaynak bul ve kredi tablosunu göster")
    assert result["status"] == "completed"
    assert "150" in result["message"]
    assert all(value not in result["message"] for value in ("777", "888", "999"))
    assert "[Report](https://example.org/report)" in result["message"]


def test_inspected_source_answer_has_citation_without_research_wrapper(env):
    *_, build = env
    tools = {"inspect_source": {"schema": {"type": "function", "function": {"name": "inspect_source",
             "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
             "status": "ok", "source_id": "source", "source_url": args["url"],
             "text": "Example ortakları: Alpha ve Beta.", "title": "Example ortaklık yapısı"}}}
    response = "Example ortakları Alpha ve Beta. Karşılaştırmaya Alpha ile başlamayı öneriyorum."
    runtime, _ = build([call("inspect_source", {"url": "https://example.org/shareholders"}), final(response)], more=tools)
    result = runtime.run("Example'nin ortakları kimler ve hangisini ekleyelim?")
    assert result["status"] == "completed"
    assert result["message"].startswith(response)
    assert "[Example ortaklık yapısı](https://example.org/shareholders)" in result["message"]


@pytest.mark.parametrize("read_status,source_text,completes", [
    ("ok", "Example ortakları: Alpha ve Beta.", True),
    ("ok", "OtherCo shareholders are Alpha and Beta.", False),
    ("ok", "Example total assets report.", False),
    ("blocked", "", False),
])
def test_failed_research_is_replaced_only_by_relevant_ownership_read(env, read_status, source_text, completes):
    *_, build = env
    tools = web_tool(lambda args: {"status": "blocked", "code": "NO_READABLE_SOURCES", "message": "No readable result"})
    tools["inspect_source"] = {"schema": {"type": "function", "function": {"name": "inspect_source",
        "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
            "status": read_status, "source_id": "source", "text": source_text,
            "source_url": "https://otherco.org/shareholders" if "OtherCo" in source_text else args["url"],
            **({"code": "SOURCE_NOT_FOUND", "message": "Unavailable"} if read_status != "ok" else {})}}
    response = "Example ortakları Alpha ve Beta. Karşılaştırmaya Alpha ile başlamayı öneriyorum."
    runtime, _ = build([call("research_web", {"query": "Example shareholders"}),
        call("inspect_source", {"url": "https://example.org/shareholders"}), final(response)], more=tools, max_decisions=3)
    result = runtime.run("Example'nin ortakları kimler, hangisini ekleyelim?")
    errors = {error["code"] for error in result.get("errors", [])}
    if completes:
        assert result["status"] == "completed"
        assert result["message"].startswith(response)
        assert "https://example.org/shareholders" in result["message"]
        assert not errors
    else:
        assert result["status"] == "blocked"
        assert {"NO_READABLE_SOURCES", "EXTERNAL_FACTS_UNVERIFIED"} <= errors
        assert "Alpha" not in result["message"]
    # Reconciliation changes live error state, never the original evidence.
    assert result["tool_results"][0]["result"]["errors"][0]["code"] == "NO_READABLE_SOURCES"


def test_research_replacement_read_does_not_clear_calculation_error(env):
    _, _, _, plan, build = env
    invalid = {**plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2026-01", "output": "real"}]}
    tools = web_tool(lambda args: {"status": "blocked", "code": "NO_READABLE_SOURCES", "message": "Unavailable"})
    tools["inspect_source"] = {"schema": {"type": "function", "function": {"name": "inspect_source",
        "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
            "status": "ok", "source_id": "source", "text": "Report read.", "source_url": args["url"]}}
    runtime, _ = build([call("execute", invalid), call("research_web", {"query": "report"}),
        call("inspect_source", {"url": "https://example.org/report"}), final()], more=tools)
    result = runtime.run("Kaynağı bul ve analiz yap")
    assert result["status"] == "partial"
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}
    assert "NO_READABLE_SOURCES" not in {error["code"] for error in result["errors"]}


def test_default_first_last_does_not_satisfy_declared_period_total(env):
    _, _, _, plan, build = env
    task = {"deliverables": ["analysis", "summary"], "summary": {"statistics": ["sum"]}}
    profit = {**plan,"columns":[{"name":"profit","metric_id":"profit","dimensions":{}}]}
    runtime, _ = build([call("execute", profit),call("plan_task", task),final()], max_decisions=3)
    result = runtime.run("Dönem toplamını hesapla")
    assert result["status"] == "partial"
    assert any(error.get("deliverable") == "summary" for error in result["errors"])
    assert not any(fact["statistic"] == "sum" for item in result["tool_results"]
                   if item["tool"] == "summarize_analysis" for fact in item["result"]["facts"])


def test_contract_cannot_be_weakened_after_research(env):
    *_, build = env
    runtime, _ = build([call("plan_task", {"deliverables": ["analysis", "chart"]}, "first"),
                        call("plan_task", {"deliverables": ["sources"]}, "weaken")], max_repairs=0)
    result = runtime.run("Analiz yap ve grafik çiz")
    assert result["status"] == "blocked"
    assert result["errors"][0]["code"] == "TASK_PLAN_LOCKED"


def test_task_plan_adds_source_known_summary_details_after_execution(env):
    _, _, journal, plan, build = env
    task = {"deliverables":["analysis","summary"]}
    refined = {**task,"summary":{"columns":["credit"],"statistics":["first","last"],
                                 "windows":[{"start":"2026-01","end":"2026-03"}]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "columns":["credit"],"statistics":["first","last"],"windows":[{"label":"Dönem","start":"2026-01","end":"2026-03"}]})
    runtime,_ = build([call("plan_task",task,"initial-plan"),call("execute",plan),
                       call("plan_task",refined,"source-known-plan"),summarize,final()])
    result = runtime.run("Kaynak dönemlerini karşılaştır")
    assert result["status"] == "completed"
    assert journal.get(result["run_id"])["state"]["task_plan"] == refined


def test_speculative_ratio_growth_plan_is_rejected_before_it_can_be_locked(env):
    _,_,journal,plan,build=env
    abstract={"deliverables":["analysis","summary"]}
    guessed={**abstract,"summary":{"columns":["credit","share"],"statistics":["first","last","growth"]}}
    actual={**plan,"columns":[*plan["columns"],{"name":"other_credit","metric_id":"credit","dimensions":{}}],
            "operations":[{"op":"ratio","column":"other_credit","denominator":"credit","output":"share","multiplier":100}]}
    valid={**abstract,"summary":{"columns":["credit","share"],"statistics":["first","last"]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "columns":["credit","share"],"statistics":["first","last"]})
    runtime,_=build([call("plan_task",guessed,"too-early"),call("plan_task",abstract,"abstract"),
                     call("execute",actual),call("plan_task",guessed,"incompatible"),
                     call("plan_task",valid,"schema-valid"),summarize,final()])
    result=runtime.run("Kaynak oranını ve ilk-son gözlemleri karşılaştır")
    assert result["status"]=="completed"
    rejected=[item["result"]["errors"][0]["code"] for item in result["tool_results"] if item["result"].get("status")=="blocked"]
    assert rejected==["TASK_PLAN_REQUIRES_ANALYSIS","TASK_PLAN_INVALID_SUMMARY"]
    assert journal.get(result["run_id"])["state"]["task_plan"]==valid


def test_empty_optional_summary_is_an_abstract_plan_without_a_repair(env):
    _,_,journal,plan,build=env
    task={"deliverables":["analysis"],"summary":{}}
    runtime,_=build([call("plan_task",task),call("execute",plan),final()])
    result=runtime.run("Kredi değerlerini göster")
    assert result["status"]=="completed" and result["repairs"]==0
    state=journal.get(result["run_id"])["state"]
    assert state["task_plan"]=={"deliverables":["analysis"]}
    first=next(message for message in state["messages"] if message.get("tool_calls"))
    assert json.loads(first["tool_calls"][0]["function"]["arguments"])["summary"]=={}


def test_summary_plan_windows_use_real_native_calendar_before_locking(env):
    _,_,journal,plan,build=env
    quarterly={**plan,"start":"2026-Q1","end":"2026-Q1","frequency":"quarterly",
               "columns":[{**plan["columns"][0],"alignment":"last"}]}
    task={"deliverables":["summary"],"summary":{"statistics":["last"],"windows":[{"start":"2026-03-31","end":"2026-03-31"}]}}
    valid={**task,"summary":{**task["summary"],"windows":[{"start":"2026-Q1","end":"2026-Q1"}]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "statistics":["last"],"windows":[{"label":"Çeyrek","start":"2026-Q1","end":"2026-Q1"}]})
    runtime,_=build([call("execute",quarterly),call("plan_task",task,"wrong-calendar"),
                     call("plan_task",valid,"native-calendar"),summarize,final()])
    result=runtime.run("Çeyrek sonu bakiyesini göster")
    assert result["status"]=="completed"
    assert journal.get(result["run_id"])["state"]["task_plan"]==valid
    assert any(item["result"].get("errors",[{}])[0].get("code")=="TASK_PLAN_INVALID_SUMMARY" for item in result["tool_results"])


@pytest.mark.parametrize("change",["window","column","statistic","comparison"])
def test_task_refinement_cannot_replace_existing_summary_constraints(env,change):
    _,_,_,plan,build = env
    initial = {"deliverables":["summary"],"summary":{"columns":["credit"],"statistics":["first","last"],
        "windows":[{"start":"2026-01","end":"2026-02"},{"start":"2026-03","end":"2026-04"}],"compare_windows":True}}
    replacement = copy.deepcopy(initial)
    if change=="window": replacement["summary"]["windows"][0]["end"]="2026-03"
    elif change=="column": replacement["summary"]["columns"]=["profit"]
    elif change=="statistic": replacement["summary"]["statistics"]=["first"]
    else: replacement["summary"]["compare_windows"]=False
    runtime,_ = build([call("execute",{**plan,"end":"2026-04"}),call("plan_task",initial,"initial-plan"),call("plan_task",replacement,"replacement-plan")],max_repairs=0)
    result = runtime.run("Kaynak dönemlerini karşılaştır")
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "TASK_PLAN_LOCKED"


@pytest.mark.parametrize("mismatched_window", [False, True])
def test_exact_summary_windows_and_statistics_are_required(env, mismatched_window):
    _, _, _, plan, build = env
    task = {"deliverables": ["analysis", "summary"], "summary": {
        "columns": ["profit"], "statistics": ["sum"], "compare_windows": True,
        "windows": [{"start": "2026-01", "end": "2026-02"}, {"start": "2026-03", "end": "2026-04"}]}}
    profit = {**plan, "end": "2026-04", "columns": [{"name": "profit", "metric_id": "profit", "dimensions": {}}]}
    def summarize(messages):
        windows = [{"label": "Baz", **task["summary"]["windows"][0]}, {"label": "Sonraki", **task["summary"]["windows"][1]}]
        if mismatched_window:
            windows[0]["end"] = "2026-03"
            windows[1]["start"] = "2026-04"
        return call("summarize_analysis", {"analysis_id": result_of(messages, "execute")["analysis_id"],
            "statistics": ["sum"], "compare_windows": True,
            "windows": windows})
    runtime, _ = build([call("execute", profit),call("plan_task", task),summarize,final("Toplam 999, büyüme yüzde 888")], max_decisions=4)
    result = runtime.run("İki dönemin toplamını karşılaştır")
    if mismatched_window:
        assert result["status"] == "partial"
        assert any(error.get("deliverable") == "summary" for error in result["errors"])
        return
    assert result["status"] == "completed"
    assert "25" in result["message"] and "45" in result["message"] and "80" in result["message"]
    assert "999" not in result["message"] and "888" not in result["message"]


def test_missing_declared_output_gets_one_bounded_repair_opportunity(env):
    _, _, _, plan, build = env
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"})
    runtime, client = build([call("plan_task", {"deliverables": ["analysis", "chart"]}),
                             call("execute", plan), final("Grafik de hazır"), chart, final()])
    result = runtime.run("Kredi analizi ve grafik")
    assert result["status"] == "completed" and result["chart_updated"]
    assert len(client.requests) == 5
    assert any("Teslim kontrolü" in (message.get("content") or "") for message in client.requests[-1])


@pytest.mark.parametrize("repair_scaled_alias", [False, True])
@pytest.mark.parametrize("compiler_enabled", [False, True])
def test_real_parser_publication_analysis_summary_chart_chain_after_research(env, repair_scaled_alias, compiler_enabled):
    store, wid, _, _, build = env
    docs = DocumentTools(store, wid)
    url = "https://reports.example.org/new-company.csv"
    # Relevance must be established by the fetched source itself, not only
    # by the unverified search-result title.
    payload = b"month,New company monthly report value (million TL)\n2026-01,10\n2026-02,15\n2026-03,20\n"
    docs.web_search = lambda query, limit=5, **kwargs: {"status": "ok", "results": [{"title": "New company monthly report", "url": url, "snippet": "monthly report"}]}
    task = {"deliverables": ["sources", "dataset", "analysis", "summary", "chart"],
            "summary": {"columns": ["value"], "statistics": ["sum"], "windows": [{"start": "2026-01", "end": "2026-03"}]}}
    def inspect(messages):
        source = result_of(messages, "research_web")["sources"][0]
        return call("inspect_source", {"source_id": source["source_id"]})
    def publish(messages):
        source = result_of(messages, "inspect_source")
        return call("publish_selected_table", {"source_id": source["source_id"], "table_id": source["tables"][0]["table_id"],
            "expected_version": 0, "column_mapping": {"month": "month", "New_company_monthly_report_value_million_TL": "value"},
            "unit_evidence": {"value": "million TL"}, "contract": {"name": "new_company", "frequency": "monthly",
                "date_column": "month", "key": ["month"], "grain": ["month"], "expected_rows": 3,
                "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                            "value": {"dtype": "integer", "unit": "TRY_million", "kind": "flow", "nullable": False}}}})
    def ingest(messages):
        source = result_of(messages, "inspect_source")
        return call("ingest_source_table", {"source_id": source["source_id"], "table_id": source["tables"][0]["table_id"],
                                            "expected_version": 0, "row_numbers": [1, 2, 3]})
    def execute(messages):
        dataset = result_of(messages, "publish_selected_table")["dataset_id"]
        return call("execute", {"start": "2026-01", "end": "2026-03", "frequency": "monthly",
            "columns": [{"name": "value", "metric_id": f"overlay:{dataset}:value", "dimensions": {}}]})
    def summarize(messages):
        return call("summarize_analysis", {"analysis_id": result_of(messages, "execute")["analysis_id"], "statistics": ["sum"],
            "windows": [{"label": "İlk çeyrek", "start": "2026-01", "end": "2026-03"}]})
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"})
    def invalid_publish(messages):
        request = publish(messages)
        args = json.loads(request["tool_calls"][0]["function"]["arguments"])
        args["contract"]["columns"]["value"]["scale"] = 1000000
        return call("publish_selected_table", args, "bad-publish")
    publication_steps = [invalid_publish, final("Yayımlama başarısız, işlemi bitirdim."), publish] if repair_scaled_alias else [publish]
    responses=[call("plan_task",{"deliverables":task["deliverables"]},"abstract-plan"),
               call("research_web", {"query": "new company monthly report", "limit": 1}),inspect,
               *([ingest] if compiler_enabled else []), *publication_steps,
               execute,call("plan_task",task,"source-known-plan"),summarize,chart,final("999 milyon TL hesaplandı")]
    extras = {**docs.extra_tools(), **(FinancialImportTools(docs).extra_tools() if compiler_enabled else {})}
    runtime, client = build(responses,more=extras,max_decisions=len(responses))
    with patch("agentic_analytics.agent.tools.documents.fetch_public_url", return_value=(payload, "text/csv", url)):
        result = runtime.run("Yeni raporu bul, içeri al, ilk çeyrek toplamını hesapla ve çizgi grafik çiz")
    assert result["status"] == "completed", result
    assert result["analysis_updated"] and result["chart_updated"]
    assert "45 milyon TL" in result["message"] and "999" not in result["message"]
    frame, _ = store.load_analysis(result["analysis_id"])
    assert frame["value"].tolist() == [10, 15, 20]
    assert len(store.workspace(wid)["datasets"]) == 1
    assert len(client.requests) == (11 if repair_scaled_alias else 9) + int(compiler_enabled)
    assert "inspect_source" in {tool["function"]["name"] for tool in client.options[2]["tools"]}
    if compiler_enabled:
        assert "publish_selected_table" not in {tool["function"]["name"] for tool in client.options[0]["tools"]}
        assert "publish_selected_table" in {tool["function"]["name"] for tool in client.options[4]["tools"]}
        assert next(item["result"] for item in result["tool_results"] if item["tool"] == "ingest_source_table")["import_status"] == "unsupported_layout"


@pytest.mark.parametrize("error_code,retries", [("INVALID_UNPIVOT",1),("INVALID_SOURCE_DATE_FORMAT",1),("UNIT_MISMATCH",0)])
def test_final_repairs_only_explicit_representation_errors_once(env,error_code,retries):
    *_, build = env
    extra = {"prepare_source_table":{"schema":{"type":"function","function":{
        "name":"prepare_source_table","description":"Fixture preparation","parameters":obj({},[])}},
        "handler":lambda args:{"status":"blocked","errors":[{"code":error_code,"message":"Fixture source mapping error"}]}}}
    responses = [call("plan_task",{"deliverables":["dataset"]}),call("prepare_source_table",{}),final("İşlem tamamlanamadı.")]
    if retries:
        responses.append(final("Hâlâ tamamlanamadı."))
    runtime,client = build(responses,more=extra,max_decisions=5)
    result = runtime.run("Kaynak tabloyu hazırla")
    assert result["status"] == "blocked"
    assert len(client.requests) == 3+retries
    assert error_code in {error["code"] for error in result["errors"]}


def test_source_date_recovery_retains_exact_source_cells_in_model_view():
    recovery = {"header_rows":[{"row":10,"cells":[{"column":"column_9","text":"31 December 2025"}]}],
                "suggested_unpivot_update":{"period_format":"english_dmy","period_sources":{
                    "column_11":{"row":10,"columns":["column_9"],"date_index":0}}},
                "mapping_basis":"Actual source header cells; numeric cells unchanged."}
    raw = {"status":"blocked","errors":[{"code":"INVALID_SOURCE_DATE_FORMAT","message":"Select the actual source header date."}],
           "recovery":recovery}
    for tool in ("prepare_source_table","inspect_source"):
        assert _model_tool_result(tool,raw)["recovery"] == recovery


def test_inspected_calendar_retains_article_body_links_beyond_site_navigation():
    navigation = [{"title":f"Menu {index}","url":f"https://example.org/menu/{index}","in_main_content":False} for index in range(150)]
    decision = {"title":"6 Mart 2025","url":"https://example.org/releases/2025/decision-15","in_main_content":True}
    raw = {"status":"ok","source_id":"calendar","text":"Full site navigation "*500,
           "article":{"title":"Decisions 2025","readable_text":"6 Mart 2025 meeting decision",
                      "source_links":[*navigation,decision],"link_count":151}}
    view = _model_tool_result("inspect_source",raw)
    assert view["text"] == "6 Mart 2025 meeting decision"
    assert view["article"]["source_links"] == [{"title":decision["title"],"url":decision["url"]}]
    assert view["article"]["link_selection"] == "main_content"
    assert len(raw["article"]["source_links"]) == 151


def test_grouped_revision_recovers_and_charts_each_saved_group(env, tmp_path):
    store, wid, journal, _, build = env
    source = tmp_path / "groups.csv"
    source.write_text("month,bank,value\n2026-01,A,10\n2026-02,A,20\n2026-03,A,30\n2026-01,B,20\n2026-02,B,25\n2026-03,B,40\n")
    contract = {"name": "Grouped flows", "frequency": "monthly", "date_column": "month", "key": ["month", "bank"], "grain": ["month", "bank"],
        "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    "bank": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False},
                    "value": {"dtype": "integer", "unit": "TRY", "scale": 1, "currency": "TRY", "kind": "flow", "nullable": False}}}
    dataset = store.ingest_csv(wid, source, contract, expected_version=0)["datasets"][-1]
    grouped = {"metric_id": f"overlay:{dataset}:value", "group_by": "bank", "dimensions": {},
               "start": "2026-01", "end": "2026-03", "frequency": "monthly", "limit": 10}
    def revise(messages):
        return call("revise_analysis", {"analysis_id": result_of(messages, "query_grouped")["analysis_id"],
            "operations": [{"op": "growth", "column": "value", "output": "change", "periods": 1}]}, "revision")

    class Crash(BaseException):
        pass
    original = journal.complete_step
    def interrupt(run_id, step_id, result):
        if step_id.endswith(":revision"):
            raise Crash()
        return original(run_id, step_id, result)
    runtime, _ = build([call("query_grouped", grouped), revise])
    with patch.object(journal, "complete_step", side_effect=interrupt), pytest.raises(Crash):
        runtime.run("Grupların değişimini hesapla ve çizgi grafik çiz", request_id="group-revision")
    before = store.workspace(wid)
    runtime, _ = build([call("create_chart", {"analysis_id": before["analysis_head"], "kind": "line", "columns": ["value"]}), final()])
    result = runtime.resume(journal.find_request(wid, "group-revision")["run_id"])
    assert result["status"] == "completed" and result["chart_updated"]
    assert store.workspace(wid)["version"] == before["version"]
    frame, manifest = store.load_analysis(result["analysis_id"])
    assert manifest["plan"]["query_type"] == "grouped"
    assert frame.loc[(frame["bank"] == "A") & (frame["period"] == "2026-02"), "change"].iloc[0] == 100
    assert frame.loc[(frame["bank"] == "B") & (frame["period"] == "2026-02"), "change"].iloc[0] == 25
    assert any(step["result"].get("recovered") for step in result["tool_results"] if step["tool"] == "revise_analysis")


def test_event_dataset_aggregation_is_delivered_as_analysis_and_chart(env, tmp_path):
    store, wid, _, _, build = env
    source = tmp_path / "events.csv"
    source.write_text("date,employee\n2026-01-03,A\n2026-01-15,B\n2026-02-02,C\n")
    contract = {"name": "Departures", "frequency": "event", "date_column": "date", "key": ["date", "employee"], "grain": ["date", "employee"],
        "columns": {"date": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    "employee": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}}}
    dataset = store.ingest_csv(wid, source, contract, expected_version=0)["datasets"][-1]
    args = {"dataset_id": dataset, "measures": [{"name": "departures", "op": "count"}], "time_bucket": {"frequency": "monthly"}}
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "aggregate_dataset")["analysis_id"], "kind": "line"})
    runtime, _ = build([call("aggregate_dataset", args), chart, final("999 kişi ayrıldı.")], more=DatasetTools(store, wid).extra_tools())
    result = runtime.run("Aylık ayrılan çalışan sayısını tablo olarak göster ve grafik çiz")
    assert result["status"] == "completed" and result["analysis_updated"] and result["chart_updated"]
    frame, _ = store.load_analysis(result["analysis_id"])
    assert frame["departures"].tolist() == [2, 1]
    assert "999" not in result["message"]


def test_long_pdf_model_view_is_bounded_and_preserves_candidate_identity():
    source = {"status":"ok","source_id":"source_"+"a"*64,"total_pages":141,
              "text":"Financial report text "*10000,"pages":[{"page":i,"text":"Long page "*1000} for i in range(1,31)],
              "tables":[{"table_id":f"table_p{i:06d}_001","page":i,"columns":["period","value"],
                         "original_columns":{"period":"Period","value":"Amount (million TL)"},
                         "source_header_quotes":{"value":"Amount (million TL)"},"context_text":"Source context "*2000,
                         "row_count":100,"preview":[{"period":"2026-01","value":None},{"period":"2026-02","value":"150"}]}
                        for i in range(1,31)]}
    original = copy.deepcopy(source)
    view = _model_tool_result("inspect_source",source)
    assert len(json.dumps(view,ensure_ascii=False)) < 24000
    assert view["model_tables_truncated"] and view["full_evidence_retained"]
    assert view["tables"][0]["table_id"] == "table_p000001_001"
    assert view["tables"][0]["preview"][0]["value"] is None
    assert source == original


def test_source_row_reader_projection_preserves_stable_row_addresses():
    source = {"status":"ok","source_id":"source_"+"a"*64,"table_id":"table_001",
              "columns":["name","value"],"row_count":100,
              "rows":[{"candidate_row":i,"values":{"name":f"Source row {i}","value":str(i*10)}} for i in range(31,61)]}
    view = _model_tool_result("read_source_table",source)
    assert view["rows"][0] == {"candidate_row":31,"values":{"name":"Source row 31","value":"310"}}
    assert view["next_row_start"] == 51 and view["model_rows_truncated"]


@pytest.mark.parametrize('question,kind', [
    ('Example nin hangi bankalarla çalışmaya başlamıştı bu tabloya başka ne ekleyebiliriz', 'relationship'),
    ('Example hangi kurumlarla iş birliği yapıyor?', 'relationship'),
    ('Example kurucuları kimler?', 'relationship'),
    ('Which institutions founded Example?', 'relationship'),
    ('Who does Example work with?', 'relationship'),
    ('Example üyeleri hangi şirketler?', 'relationship'),
    ("Example'nin ortakları kim?", 'ownership'),
    ('Üyelik nedir?', None), ('Kredi kartı tablosunu göster', None),
    ('BIST Banka Endeksi aylık kapanışlarını çalışma alanında ara. Hangi tablo gerektiğini bildir.', None),
    ('Banka endeksini getir. Erişim kontrolünü aşmaya çalışma; hangi kaynağa erişilemediğini bildir.', None),
    ('Çalışma alanındaki banka verileri için hangi aylarda gözlem var?', None),
    ('Find bank index closings in this workspace; explain which source supplies them.', None),
])
def test_institutional_question_keeps_relationship_intent(question, kind):
    from agentic_analytics.agent.runtime import _institutional_fact_kind
    assert _institutional_fact_kind(question) == kind


def test_workspace_and_access_instructions_do_not_add_ownership_delivery_obligations(env):
    store, wid, journal, plan, build = env
    runtime, _ = build([call('execute', plan), final('Analiz kaydedildi.')])
    result = runtime.run('Banka verilerini çalışma alanında ara ve analiz et. '
                         'Erişim kontrolünü aşmaya çalışma; hangi kaynağa erişilemediğini bildir.')
    state = journal.get(result['run_id'])['state']
    assert not state['external_facts_required']
    assert state['institutional_fact_kind'] is None
    assert 'EXTERNAL_FACTS_UNVERIFIED' not in {error['code'] for error in result.get('errors', [])}


@pytest.mark.parametrize('bad_query', ['Example kredi kartı bankalarla çalışmaya başladı', 'Example üye bankalar 2025'])
def test_invented_product_or_year_is_not_executed_and_research_recovers(env, bad_query):
    store, wid, journal, _, build = env
    attempts = []
    source = {'source_id': 'source', 'url': 'https://example.org/report', 'title': 'Example faaliyet raporu',
              'content': 'Example ortaklık yapısı: Alpha ve Beta. Üyeler ortaklardan ayrı gruptur.'}
    def research(args):
        attempts.append(args['query'])
        return {'status': 'ok', 'sources': [source]}
    response = 'Rapordaki ortak bankalar Alpha ve Beta. Bu listeyi tarihsel kurucular olarak doğrulamadım. Alpha için mevcut analizin dönem ve kapsamına uygun raporu bulmayı öneriyorum.'
    runtime, client = build([call('research_web', {'query': bad_query}, 'bad'),
        call('research_web', {'query': 'Example hangi bankalarla çalışmaya başladı'}, 'good'), final(response)], more=web_tool(research))
    before = store.workspace(wid)
    result = runtime.run('Example nin hangi bankalarla çalışmaya başlamıştı bu tabloya başka ne ekleyebiliriz')
    assert result['status'] == 'completed' and result['message'].startswith(response)
    assert attempts == ['Example hangi bankalarla çalışmaya başladı']
    assert result['tool_results'][0]['result']['errors'][0]['code'] == 'RESEARCH_QUERY_SCOPE_MISMATCH'
    assert 'RESEARCH_QUERY_SCOPE_MISMATCH' in json.dumps(client.requests[1])
    state = journal.get(result['run_id'])['state']
    assert state['initial_candidates']['status'] == 'not_requested'
    assert state['institutional_fact_kind'] == 'relationship'
    assert store.workspace(wid) == before


@pytest.mark.parametrize('tool', ['discover', 'web_search', 'research_web'])
def test_query_scope_guard_preserves_explicit_products_and_years(tool):
    from agentic_analytics.agent.runtime import _research_scope_error
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship',
             'institutional_request': 'Example hangi bankalarla kredi kartı için 2025 yılında çalıştı?'}
    assert not _research_scope_error(state, tool, {'query': 'Example kredi kartı ortakları 2025'})
    assert _research_scope_error({**state, 'institutional_request': 'Example kimlerle çalışıyor?'}, tool,
                                {'query': 'Example kredi kartı ortakları 2025'})
    read = {'tool': 'research_web', 'result': {'status': 'ok', 'sources': [
        {'content': 'Example ortakları: Alpha, Beta.', 'url': 'https://example.org/owners'}]}}
    assert not _research_scope_error({**state, 'institutional_request': 'Example kimlerle çalışıyor?', 'tool_results': [read]},
                                    tool, {'query': 'Alpha kredi kartı 2025'})


def test_truncated_relevant_institutional_page_is_read_before_answering(env):
    *_, build = env
    source = {'source_id': 'source', 'url': 'https://example.org/report.pdf', 'title': 'Example faaliyet raporu',
        'content': 'Example kurulan kurum. Ortaklık yapısı: Alpha...', 'content_truncated': True,
        'matched_pages': [4], 'suggested_inspection': {'source_id': 'source', 'page_numbers': [4]},
        'passages': [{'page': 4, 'text': 'Example ortaklık yapısı: Alpha...', 'content_truncated': True}]}
    tools = web_tool(lambda args: {'status': 'ok', 'sources': [source]})
    tools['inspect_source'] = {'schema': {'type': 'function', 'function': {'name': 'inspect_source',
        'parameters': obj({'source_id': {'type': 'string'}, 'page_numbers': {'type': 'array', 'items': {'type': 'integer'}}})}},
        'handler': lambda args: {'status': 'ok', 'source_id': 'source', 'source_url': source['url'],
            'text': 'Example raporundaki ortaklar Alpha ve Beta. Üyeler ayrı gruptur.',
            'pages': [{'page': 4, 'text': 'Example raporundaki ortaklar Alpha ve Beta. Üyeler ayrı gruptur.'}], 'text_truncated': False}}
    bad = 'Okunabilir kaynak bulamadım. Yeniden araştırmamı ister misiniz?'
    good = 'Rapordaki ortaklar Alpha ve Beta. Bu güncel ortak listesinin tarihsel kurucularla aynı olduğunu doğrulamadım. Alpha raporunu mevcut karşılaştırmayla aynı dönem için incelemeyi öneriyorum.'
    runtime, client = build([call('research_web', {'query': 'Example kuruluş bankaları'}), final(bad),
        call('inspect_source', {'source_id': 'source', 'page_numbers': [4]}), final(good)], more=tools)
    result = runtime.run('Example nin hangi bankalarla çalışmaya başlamıştı, tabloya ne ekleyebiliriz?')
    assert result['status'] == 'completed' and result['message'].startswith(good)
    assert len(client.requests) == 4 and 'SOURCE_READING_INCOMPLETE' in json.dumps(client.requests[2])
    assert bad not in result['message'] and source['url'] in result['message']


def test_complete_read_cannot_be_replaced_by_claim_that_no_source_was_found(env):
    *_, build = env
    tools = web_tool(lambda args: {'status': 'ok', 'sources': [
        {'url': 'https://example.org/members', 'content': 'Example üyeleri: Alpha ve Beta.'}]})
    good = 'Example üyeleri Alpha ve Beta. Üyelik ile ortaklık ayrı ilişkilerdir.'
    runtime, client = build([call('research_web', {'query': 'Example üyeleri'}),
        final('İlgili kaynak bulamadım.'), final(good)], more=tools)
    result = runtime.run('Example üyeleri hangi kurumlar?')
    assert result['status'] == 'completed' and result['message'].startswith(good)
    assert 'READ_SOURCE_ANSWER_REQUIRED' in json.dumps(client.requests[2])


def test_relationship_evidence_needs_actual_role_and_does_not_trigger_ownership_percent_gate():
    from agentic_analytics.agent.runtime import _ownership_percentage_errors
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example'}
    for body in ['Example için güncel duyuru bulunmamaktadır.', 'OtherCompany kurucuları Alpha ve Beta.', 'Example toplam aktifleri raporu.']:
        result = {'status': 'ok', 'source_id': 'source', 'source_url': 'https://reports.test/doc', 'text': body}
        assert AgentRuntime._external_fact_errors({**state, 'tool_results': [{'tool': 'inspect_source', 'result': result}]}, 'Liste Alpha ve Beta.')[0]['code'] == 'EXTERNAL_FACTS_UNVERIFIED'
    assert not _ownership_percentage_errors(state, 'Önceki analizde büyüklük karşılaştırması %9,6 idi.')
    source = {'status': 'ok', 'sources': [{'url': 'https://example.org/report', 'content': 'Example kurulan kurum.',
        'content_truncated': True, 'passages': [{'page': 4, 'text': 'Example ortakları Alpha ve Beta.', 'content_truncated': False}]}]}
    assert not AgentRuntime._external_fact_errors({**state, 'tool_results': [{'tool': 'research_web', 'result': source}]},
        'Rapordaki ortaklar Alpha ve Beta. Tarihsel kurucu kimliklerini doğrulayamadım.')


def test_historical_founder_source_gap_does_not_discard_supported_current_owners():
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example',
        'tool_results': [{'tool': 'research_web', 'result': {'status': 'ok', 'sources': [
            {'url': 'https://example.org/report', 'content': 'Example ortaklık yapısı: Alpha ve Beta.'}]}}]}
    assert not AgentRuntime._external_fact_errors(state,
        'Tarihsel kurucu listesini doğrulayacak kaynak bulamadım. Rapordaki güncel ortaklar Alpha ve Beta.')
    assert AgentRuntime._external_fact_errors(state,
        'İlgili kaynak bulamadım. Rapordaki ortakları tekrar araştırmamı ister misiniz?')[0]['code'] == 'READ_SOURCE_ANSWER_REQUIRED'


@pytest.mark.parametrize('question', [
    'Hangi bankalarla çalışmalıyım?', 'Hangi kurumlarla çalışalım?',
    'Hangi bankayı önerirsin?', 'Which banks should I work with?',
    'Kuruculuk nedir?', 'Üyelik ne demek?', 'Explain membership',
])
def test_personal_advice_and_institutional_lessons_do_not_require_owner_research(question):
    from agentic_analytics.agent.runtime import _institutional_fact_kind
    assert _institutional_fact_kind(question) is None


@pytest.mark.parametrize('repaired', [True, False])
def test_current_owners_cannot_be_delivered_as_historical_founders(env, repaired):
    _, _, journal, _, build = env
    source = {'source_id': 'source', 'url': 'https://example.org/report.pdf',
        'content': 'Example dokuz bankanın ortaklığıyla kuruldu.', 'content_truncated': True,
        'passages': [{'page': 3, 'text': 'Example dokuz bankanın ortaklığıyla kuruldu.', 'content_truncated': False},
                     {'page': 4, 'text': 'Example ortaklık yapısı: Alpha, Beta.', 'content_truncated': False}]}
    bad = '**Kurucu/ortak bankalar**\nAlpha, Beta.\nBu, raporun ortaklık yapısı bölümündeki kurucu ortaklardır.'
    good = 'Rapordaki ortak bankalar Alpha ve Beta. Bu listeyi tarihsel kurucular olarak doğrulamadım. Alpha ve Beta için mevcut analizdeki dönem ve kapsama uygun raporları eklemeyi öneriyorum.'
    runtime, client = build([call('research_web', {'query': 'Example kuruluş bankaları'}),
        final(bad), final(good if repaired else bad)], more=web_tool(lambda args: {'status': 'ok', 'sources': [source]}))
    result = runtime.run('Example nin hangi bankalarla çalışmaya başlamıştı bu tabloya başka ne ekleyebiliriz?')
    assert 'INSTITUTIONAL_ROLE_UNVERIFIED' in json.dumps(client.requests[2])
    assert bad not in result['message']
    assert not any(message.get('content') == bad for message in journal.get(result['run_id'])['state']['messages'])
    if repaired:
        assert result['status'] == 'completed' and result['message'].startswith(good)
    else:
        assert result['status'] == 'partial'
        assert 'INSTITUTIONAL_ROLE_UNVERIFIED' in {error['code'] for error in result['errors']}


@pytest.mark.parametrize('text', [
    'Example kurucu bankaları: Alpha Bank ve Beta Bank.',
    'Example was founded by Alpha Bank and Beta Bank.',
    'Example, Alpha Bank ve Beta Bank tarafından kuruldu.',
])
def test_explicit_named_founder_source_allows_founder_answer(text):
    from agentic_analytics.agent.runtime import _institutional_role_errors
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example'}
    assert not _institutional_role_errors(state, 'Kurucu bankalar Alpha Bank ve Beta Bank.', [{'text': text}])
    assert _institutional_role_errors(state, 'Kurucu bankalar Alpha Bank ve Beta Bank.',
        [{'text': 'Example dokuz banka tarafından kuruldu. Ortaklık yapısı: Alpha Bank, Beta Bank.'}])[0]['code'] == 'INSTITUTIONAL_ROLE_UNVERIFIED'


def test_relationship_statistics_repair_keeps_existing_asset_ratio_separate(env):
    from agentic_analytics.agent.runtime import _institutional_role_errors
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship',
        'ownership_subject': 'example', 'institutional_request': 'Example kimlerle çalışıyor, tabloya ne ekleyebiliriz?'}
    sources = [{'text': 'Example ortakları Alpha ve Beta.'}]
    assert not _institutional_role_errors(state, 'Rapordaki ortaklar Alpha ve Beta. Önceki aktif karşılaştırma oranı %9,6 idi.', sources)
    for content in ['| Banka | Ortaklık payı |\n| Alpha | %9,09 |', '207 üyesi vardır.', 'Üye dağılımı: Bankalar 68, Faktoring 49.']:
        assert _institutional_role_errors(state, content, sources)[0]['code'] == 'UNSOLICITED_INSTITUTIONAL_STATISTICS'
    assert not _institutional_role_errors({**state, 'institutional_request': 'Example kaç üyeyle çalışıyor?'}, '207 üyesi vardır.', sources)
    assert not _institutional_role_errors({**state, 'ownership_percentages_requested': True}, '| Banka | Ortaklık payı |\n| Alpha | %9,09 |', sources)


@pytest.mark.parametrize('uncertainty', [
    'Kurucu bankaların adlarını kaynakla teyit edemedim.',
    'The report does not identify the founders.',
    'Kurucu banka adları bu kaynakta yer almıyor.',
    'Kurucu banka adları bu kaynakta belirtilmiyor.',
])
def test_founder_identity_uncertainty_is_not_an_assertion_of_founder_names(uncertainty):
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example',
        'tool_results': [{'tool': 'research_web', 'result': {'status': 'ok', 'sources': [
            {'url': 'https://example.org/report', 'content': 'Example ortaklık yapısı: Alpha, Beta.'}]}}]}
    response = uncertainty + ' Rapordaki güncel ortaklar Alpha ve Beta.'
    assert not AgentRuntime._external_fact_errors(state, response)
    # A limitation elsewhere cannot license a separate positive founder claim.
    bad = 'Kurucu bankalar Alpha ve Beta; ' + uncertainty
    assert 'INSTITUTIONAL_ROLE_UNVERIFIED' in {e['code'] for e in AgentRuntime._external_fact_errors(state, bad)}


def test_other_institutions_founder_list_cannot_certify_requested_institutions_founders():
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example',
        'tool_results': [{'tool': 'research_web', 'result': {'status': 'ok', 'sources': [
            {'source_id': 'first', 'url': 'https://example.org/report', 'content': 'Example ortaklık yapısı: Alpha, Beta.'},
            {'source_id': 'second', 'url': 'https://otherco.org/history', 'content': 'OtherCo kurucu bankaları: Alpha ve Beta.'}]}}]}
    errors = AgentRuntime._external_fact_errors(state, 'Example kurucu bankaları Alpha ve Beta.')
    assert 'INSTITUTIONAL_ROLE_UNVERIFIED' in {error['code'] for error in errors}
    state['tool_results'][0]['result']['sources'][0]['content'] = 'Example kurucu bankaları: Alpha ve Beta.'
    assert not AgentRuntime._external_fact_errors(state, 'Example kurucu bankaları Alpha ve Beta.')


def test_provider_search_budget_failure_is_recovered_by_relevant_source_read(env):
    *_, build = env
    tools = web_tool(lambda args: {'status': 'unavailable', 'code': 'SEARCH_BUDGET_EXHAUSTED', 'message': 'Search deadline reached'})
    tools['inspect_source'] = {'schema': {'type': 'function', 'function': {'name': 'inspect_source',
        'parameters': obj({'url': {'type': 'string'}})}}, 'handler': lambda args: {
            'status': 'ok', 'source_url': args['url'], 'text': 'Example ortakları: Alpha ve Beta.'}}
    response = 'Rapordaki ortaklar Alpha ve Beta.'
    runtime, _ = build([call('research_web', {'query': 'Example ortakları'}),
        call('inspect_source', {'url': 'https://example.org/owners'}), final(response)], more=tools)
    result = runtime.run("Example'nin ortakları kim?")
    assert result['status'] == 'completed' and result['message'].startswith(response)
    assert result['tool_results'][0]['result']['errors'][0]['code'] == 'SEARCH_BUDGET_EXHAUSTED'


def test_institutional_repair_uses_current_source_evidence_without_replaying_old_analysis(env):
    store, wid, journal, plan, build = env
    prior_runtime, _ = build([call('execute', plan), final()])
    prior = prior_runtime.run('Kredi verilerinin ilk çeyrek tablosunu göster')
    before = store.workspace(wid)
    source = {'source_id': 'corp-source', 'url': 'https://example.org/report.pdf', 'title': 'Example 2025 report',
        'content': 'Example dokuz bankayla kuruldu.', 'content_truncated': True,
        'passages': [{'page': 4, 'text': 'Example ortaklık yapısı: Alpha Bank, Beta Bank.', 'content_truncated': False}],
        'suggested_inspection': {'source_id': 'corp-source', 'page_numbers': [4]}}
    bad = 'Kurucu ortak bankalar Alpha Bank ve Beta Bank.'
    good = 'Rapordaki ortaklar Alpha Bank ve Beta Bank. Tarihsel kurucu kimliklerini doğrulayamadım. Bu iki bankanın mevcut analizle aynı dönem ve kapsamdaki raporlarını bulup karşılaştırmayı öneriyorum.'
    runtime, client = build([call('research_web', {'query': 'Example kuruluş bankaları'}, 'corp-read'), final(bad), final(good)],
        more=web_tool(lambda args: {'status': 'ok', 'sources': [source]}))
    question = 'Example nin hangi bankalarla çalışmaya başlamıştı, tabloya ne ekleyebiliriz?'
    result = runtime.run(question, conversation_id=prior['conversation_id'])
    assert result['status'] == 'completed' and result['message'].startswith(good)
    repaired = client.requests[2]
    assert [m['content'] for m in repaired if m['role'] == 'user'] == [question]
    assert [m['tool_call_id'] for m in repaired if m['role'] == 'tool'] == ['corp-read']
    assert 'INSTITUTIONAL_ROLE_UNVERIFIED' in repaired[0]['content']
    assert 'active_plan' in repaired[0]['content'] and '2026-03' in repaired[0]['content']
    assert 'aylık seri' in repaired[0]['content'] and 'TAMAMINI' in repaired[0]['content']
    evidence = json.loads(next(m['content'] for m in repaired if m['role'] == 'tool'))
    assert evidence['sources'][0]['passages'] == source['passages']
    assert 'execute' not in {schema['function']['name'] for schema in client.options[2]['tools']}
    assert store.workspace(wid) == before
    durable = journal.get(result['run_id'])['state']
    assert any(call['function']['name'] == 'execute' for m in durable['messages'] for call in m.get('tool_calls', []))
    assert durable['delivery_repairs'] == 1 and durable['institutional_delivery_repair']


@pytest.mark.parametrize('claim', [
    'Example kurucu bankaları Alpha Bank ve Beta Bank.',
    'Example kuruluşunda yer alan bankalar Alpha Bank ve Beta Bank.',
    'Example başlangıçta Alpha Bank ve Beta Bank ile çalışmaya başladı.',
])
def test_founder_date_and_count_never_identify_current_owner_names(claim):
    from agentic_analytics.agent.runtime import _institutional_role_errors
    source = {'text': 'Example, 11 Nisan 1995 tarihinde dokuz banka tarafından kuruldu.\nOrtaklık yapısı\nAlpha Bank\nBeta Bank'}
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example'}
    assert _institutional_role_errors(state, claim, [source])[0]['code'] == 'INSTITUTIONAL_ROLE_UNVERIFIED'
    assert not _institutional_role_errors(state,
        '1995 yılında dokuz kurucu bankayla başladı; güncel ortaklar Alpha Bank ve Beta Bank, kurucu isimleri doğrulanamadı.', [source])


def test_institutional_answer_repair_cannot_mutate_the_analysis(env):
    store, wid, _, plan, build = env
    source = {'url': 'https://example.org/report', 'content': 'Example ortakları: Alpha ve Beta.'}
    runtime, _ = build([call('research_web', {'query': 'Example kurucuları'}),
        final('Kurucu bankalar Alpha ve Beta.'), call('execute', plan)],
        more=web_tool(lambda args: {'status': 'ok', 'sources': [source]}), max_decisions=3)
    before = store.workspace(wid)
    result = runtime.run("Example'nin kurucuları kim?")
    assert store.workspace(wid) == before
    assert any(error['code'] == 'INSTITUTIONAL_REPAIR_READ_ONLY'
               for item in result['tool_results'] for error in item.get('result', {}).get('errors', []))


@pytest.mark.parametrize('uncertainty', [
    'Bu dokuz kurucu bankanın açık isimleri raporda listelenmiyor.',
    'Bu isimler rapordaki ortak bankalardır; bunların geçmişteki kurucu dokuz bankayla birebir aynı olup olmadığı bu kaynaklardan doğrulanamıyor.',
    'The founder names are not listed in this report.',
])
def test_negative_founder_identity_predicate_does_not_assert_inherited_names(uncertainty):
    from agentic_analytics.agent.runtime import _institutional_role_errors
    source = {'text': 'Example dokuz bankayla kuruldu. Ortaklık yapısı: Alpha ve Beta.'}
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example'}
    assert not _institutional_role_errors(state, uncertainty, [source])
    assert _institutional_role_errors(state, 'Kurucu bankalar Alpha ve Beta; ' + uncertainty, [source])[0]['code'] == 'INSTITUTIONAL_ROLE_UNVERIFIED'
    assert _institutional_role_errors(state, 'Kurucu bankalar Alpha ve Beta. Raporda üye isimleri listelenmiyor.', [source])[0]['code'] == 'INSTITUTIONAL_ROLE_UNVERIFIED'


def test_plain_pay_column_still_is_unrequested_owner_statistics():
    from agentic_analytics.agent.runtime import _institutional_role_errors
    state = {'external_facts_required': True, 'institutional_fact_kind': 'relationship', 'ownership_subject': 'example'}
    assert _institutional_role_errors(state, '| Ortak banka/kurum (rapordaki rol: ortak) | Pay |\n| Alpha | %9,09 |',
        [{'text': 'Example ortakları Alpha ve Beta.'}])[0]['code'] == 'UNSOLICITED_INSTITUTIONAL_STATISTICS'


def test_institutional_followup_keeps_user_intent_across_two_implicit_turns(env):
    _, _, journal, _, build = env
    source = {'status':'ok','source_id':'source','source_url':'https://example.org/owners',
              'text':'Example ortakları: Alpha Bank, Beta Bank ve Gamma Bank.'}
    more = {'inspect_source': {'schema': {'type':'function','function':{'name':'inspect_source',
             'parameters':obj({'url':{'type':'string'}})}}, 'handler':lambda args:copy.deepcopy(source)}}
    good = 'Rapordaki ortaklar Alpha Bank, Beta Bank ve Gamma Bank.'
    runtime, client = build([call('inspect_source',{'url':source['source_url']}),final(good),
        final('Kaynağı okumadan 57 bankalık yeni bir liste veriyorum.'),
        call('inspect_source',{'url':source['source_url']}),final(good),
        call('inspect_source',{'url':source['source_url']}),final(good)], more=more)
    first = runtime.run("Example'nin ortakları kimler?")
    second = runtime.run('tamam liste olarak versene bana bunlar hangi bankalardı', conversation_id=first['conversation_id'])
    third = runtime.run('hangi bankalarla ortak', conversation_id=first['conversation_id'])
    assert first['status'] == second['status'] == third['status'] == 'completed'
    assert '57' not in second['message']
    for result in (second, third):
        state = journal.get(result['run_id'])['state']
        assert state['external_facts_required'] and state['ownership_subject'] == 'example'
        assert state['institutional_fact_kind'] == 'ownership'
        assert not state['ownership_percentages_requested']
    assert journal.get(second['run_id'])['state']['delivery_repairs'] == 1
    assert 'Example' in journal.get(third['run_id'])['state']['institutional_request']


@pytest.mark.parametrize('current,previous_kind,inherit',[
    ('tamam liste olarak ver',None,False),
    ('Mart kredilerini liste olarak ver','ownership',False),
    ('grafiğe ekle','ownership',False),
    ("OtherCompany'nin ortakları kim?",'ownership',False),
    ('liste olarak göster','ownership',True),
])
def test_institutional_followup_never_jumps_across_a_changed_user_topic(env,current,previous_kind,inherit):
    _, wid, journal, _, build = env
    runtime,_ = build([])
    previous = {'run_id':'previous','message':'Mart kredilerini göster' if previous_kind is None else "Example'nin ortakları kim?",
                'state':{'external_facts_required':bool(previous_kind),'institutional_fact_kind':previous_kind,
                         'ownership_subject':'example','institutional_origin_request':"Example'nin ortakları kim?"}}
    state = {'messages':[{'role':'user','content':"EarlierCompany'nin ortakları kim?"},
                        {'role':'assistant','content':'An invented institution list must not be used.'},
                        {'role':'user','content':previous['message']},{'role':'user','content':current}]}
    with patch.object(journal,'list',return_value=[previous]):
        intent=runtime._institutional_intent({'message':current,'run_id':'current','conversation_id':'conversation'},state)
    assert (intent['ownership_subject']=='example') is inherit
    if current.startswith('OtherCompany'):
        assert intent['ownership_subject']=='othercompany'


def test_institutional_followup_does_not_inherit_percentage_permission(env):
    _, _, journal, _, build = env
    runtime, _ = build([])
    previous={'run_id':'previous','message':"Example'nin ortaklık yüzdeleri nedir?",'state':{
        'external_facts_required':True,'institutional_fact_kind':'ownership','ownership_subject':'example',
        'ownership_percentages_requested':True}}
    message='liste olarak ver'
    with patch.object(journal,'list',return_value=[previous]):
        intent=runtime._institutional_intent({'message':message,'run_id':'current','conversation_id':'conversation'},
            {'messages':[{'role':'user','content':previous['message']},{'role':'user','content':message}]})
    from agentic_analytics.agent.runtime import _requests_ownership_percentages
    assert intent['institutional_fact_kind']=='ownership' and not _requests_ownership_percentages(message)


@pytest.mark.parametrize("case", ["other_source", "other_page", "empty", "failed"])
def test_dispatch_does_not_clear_an_unrecovered_source_read_stall(env, case):
    _, _, journal, _, build = env
    executed = []
    tools = page_navigation_tools(executed)
    inspect = tools["inspect_source"]["handler"]
    def inspect_result(args):
        result = inspect(args)
        if args["page_numbers"] == [48] and case in {"empty", "failed"}:
            result["pages"][0]["text"] = ""
            if case == "failed":
                result.update(status="blocked", errors=[{"code": "SOURCE_NOT_FOUND"}])
        return result
    tools["inspect_source"]["handler"] = inspect_result
    next_args = {"source_id": "b" if case == "other_source" else "a", "page_numbers": [47] if case == "other_source" else [48]}
    runtime, _ = build([call("find_source_pages", {"source_id": "a", "query": "credit sectors"}),
        *[call("inspect_source", {"source_id": "a", "page_numbers": [47]}, f"read{n}") for n in range(3)],
        call("inspect_source", next_args, "different_read"), final("Kaynak incelemesi tamamlanamadı.")], more=tools)
    result = runtime.run("Kredi dipnotunu incele.")
    assert result["status"] == "partial"
    state = journal.get(result["run_id"])["state"]
    assert {"code": "SOURCE_READ_REPEATED", "source_id": "a"}.items() <= next(
        error for error in state["unresolved_errors"]["inspect_source"] if error.get("code") == "SOURCE_READ_REPEATED").items()
    assert state["source_read_repair_used"]["source_id"] == "a"


@pytest.mark.parametrize("last_response", [final("Raporda sektör dağılımı kesinlikle yoktur. Tutar 999 milyon TL."),
    call("find_source_pages", {"source_id": "a", "query": "one more query"}, "unused_search"),
    final("")])
def test_final_existing_decision_closes_an_explicit_read_source_gap_without_more_tools(env, last_response):
    _, _, journal, _, build = env
    executed = []
    tools = page_navigation_tools(executed)
    inspect = tools["inspect_source"]["handler"]
    def omission(args):
        result = inspect(args)
        result["pages"][0]["text"] = "4.8 Credit risk disclosures\nNot prepared in compliance with the reporting requirements.\n4.9 Other disclosures"
        return result
    tools["inspect_source"]["handler"] = omission
    runtime, client = build([call("plan_task", {"deliverables": ["analysis", "chart", "sources", "summary"]}),
        call("find_source_pages", {"source_id": "a", "query": "credit sectors"}),
        call("inspect_source", {"source_id": "a", "page_numbers": [47]}), last_response],
        more=tools, max_decisions=4)
    result = runtime.run("Kredi dağılımını bul, tutar tablosu, oranlar ve grafik göster.")
    assert result["status"] == "partial" and result["decisions"] == 4
    assert len(client.requests) == 4 and client.options[-1]["tools"] == []
    assert len(executed) == 2
    assert "Not prepared in compliance" in result["message"]
    assert "https://reports.example.org/report.pdf#page=47" in result["message"]
    assert "tablosu, oranlar ve grafik oluşturulmadı" in result["message"]
    assert "999" not in result["message"] and "kesinlikle yoktur" not in result["message"]
    assert "DECISION_BUDGET_EXCEEDED" not in {error["code"] for error in result["errors"]}
    assert "SOURCE_DATA_NOT_VERIFIED" in {error["code"] for error in result["errors"]}
    state = journal.get(result["run_id"])["state"]
    assert state["source_final_review"][0]["page"] == 47
    assert not state.get("analysis_updated") and not state.get("chart_updated")


def test_scope_change_question_closes_read_pdf_gap_with_citation_instead_of_pausing(env):
    _, _, journal, _, build = env
    executed = []
    tools = page_navigation_tools(executed)
    inspect = tools["inspect_source"]["handler"]

    def omission(args):
        result = inspect(args)
        result["pages"][0]["text"] = (
            "5.1.5.6 Allocation of loans by customers\n"
            "Not prepared in compliance with the reporting requirements.\n"
            "5.1.5.7 Allocation of domestic and foreign loans")
        return result

    tools["inspect_source"]["handler"] = omission
    runtime, client = build([
        call("plan_task", {"deliverables": ["analysis", "chart", "sources", "summary"]}),
        call("find_source_pages", {"source_id": "a", "query": "loans to customers by sector"}),
        call("inspect_source", {"source_id": "a", "page_numbers": [47]}),
        call("ask_user", {"question": "Sector only occurs in investments. Use product types instead?"}),
    ], more=tools)
    result = runtime.run("Kredilerin sektörel dağılımını ve oranlarını bu PDF'den bul, grafik göster; bulamadığını belirt.")

    assert result["status"] == "partial" and result["decisions"] == 4
    assert len(client.requests) == 4
    assert "Allocation of loans by customers" in result["message"]
    assert "https://reports.example.org/report.pdf#page=47" in result["message"]
    assert "Sector only occurs" not in result["message"]
    assert "tablosu, oranlar ve grafik oluşturulmadı" in result["message"]
    assert "SOURCE_DATA_NOT_VERIFIED" in {error["code"] for error in result["errors"]}
    assert not journal.get(result["run_id"])["state"].get("analysis_updated")


@pytest.mark.parametrize("case", ["search_only", "incomplete", "unrelated_source", "wrong_page", "failed", "empty", "no_omission", "tool_error"])
def test_final_source_gap_requires_actual_candidate_omission_and_complete_search(env, case):
    _, _, _, _, build = env
    runtime, _ = build([])
    state = {"source_page_progress": {"a": {"complete_search": case != "incomplete", "candidate_read_after_search": True,
        "candidate_pages": [47], "source_url": "https://reports.example.org/report.pdf"}}, "tool_results": []}
    if case != "search_only":
        state["tool_results"] = [{"tool": "inspect_source", "result": {"status": "blocked" if case == "failed" else "ok",
            "source_id": "b" if case == "unrelated_source" else "a", "pages": [{"page": 48 if case == "wrong_page" else 47,
                "text": "" if case == "empty" else "Credit risk disclosures\n" + ("Domestic loans table" if case == "no_omission" else "Not prepared in compliance with reporting requirements.")} ]}}]
    if case == "tool_error":
        state["unresolved_errors"] = {"execute": [{"code": "INVALID_PLAN"}]}
    assert runtime._source_final_evidence(state) == []


@pytest.mark.parametrize("case", ["relevant_omission", "unrelated_omission", "other_source", "search_only"])
def test_repeated_search_terminal_preserves_only_a_read_same_source_relevant_gap(env, case):
    _, _, _, _, build = env
    executed = []
    tools = page_navigation_tools(executed)
    inspect = tools["inspect_source"]["handler"]
    def read(args):
        result = inspect(args)
        label = "Allocation of loans by customers" if case != "unrelated_omission" else "Write-off policy"
        result["pages"][0]["text"] = label + "\nNot prepared in compliance with reporting requirements."
        return result
    tools["inspect_source"]["handler"] = read
    responses = [call("plan_task", {"deliverables": ["analysis", "chart", "sources"]}),
        call("find_source_pages", {"source_id": "a", "query": "loans to customers by sector"}, "initial_search")]
    if case != "search_only":
        responses.append(call("inspect_source", {"source_id": "b" if case == "other_source" else "a", "page_numbers": [47]}))
    responses += [call("find_source_pages", {"source_id": "a", "query": "loans by sector"}, f"search{n}") for n in range(3)]
    # In negative cases the ordinary read-required guard can stop first;
    # all such failures must remain technical blocks without an absence claim.
    runtime, _ = build(responses, more=tools, max_repairs=1)
    result = runtime.run("Kredi dağılımını kaynakta bul ve grafik göster.")
    if case == "relevant_omission":
        assert result["status"] == "partial"
        assert "Allocation of loans by customers" in result["message"]
        assert "#page=47" in result["message"]
        assert "tablosu, oranlar ve grafik oluşturulmadı" in result["message"]
        assert {"NO_PROGRESS", "SOURCE_DATA_NOT_VERIFIED"} <= {error["code"] for error in result["errors"]}
        assert next(error for error in result["errors"] if error["code"] == "NO_PROGRESS")["source_id"] == "a"
        assert len([action for action in executed if action[0] == "find"]) == 3
    else:
        assert result["status"] == "blocked"
        assert "SOURCE_DATA_NOT_VERIFIED" not in {error["code"] for error in result["errors"]}


def test_source_gap_ranking_prefers_requested_heading_and_never_another_run_or_source(env):
    _, _, _, _, build = env
    runtime, _ = build([])
    state = {"source_page_progress": {"a": {"complete_search": True, "candidate_read_after_search": True,
        "candidate_pages": [47, 51], "last_query": "loans to customers by sector", "source_url": "https://reports.example.org/report.pdf"}},
        "tool_results": [
            {"tool": "inspect_source", "result": {"status": "ok", "source_id": "a", "pages": [{"page": 47,
                "text": "Allocation of loans by customers\nNot prepared in compliance with reporting requirements."}]}},
            {"tool": "inspect_source", "result": {"status": "ok", "source_id": "a", "pages": [{"page": 51,
                "text": "Write-off policy\nNot prepared in compliance with reporting requirements."}]}}],
        "unresolved_errors": {"find_source_pages": [{"code": "NO_PROGRESS", "source_id": "a"}]}}
    evidence = runtime._source_final_evidence(state, stalled_source="a")
    assert len(evidence) == 1 and evidence[0]["page"] == 47
    assert runtime._source_final_evidence(state, stalled_source="b") == []
    state["unresolved_errors"]["execute"] = [{"code": "INVALID_PLAN"}]
    assert runtime._source_final_evidence(state, stalled_source="a") == []


@pytest.mark.parametrize("last_read", ["success", "failed", "other_source"])
def test_last_allowed_read_can_close_a_previously_blocked_source_assessment(env, last_read):
    _, _, journal, _, build = env
    executed = []
    tools = page_navigation_tools(executed)
    find, inspect = tools["find_source_pages"]["handler"], tools["inspect_source"]["handler"]
    def search(args):
        result = find(args)
        if args["query"] != "credit sectors":
            result["matches"] = [{"page": 48}]
            result["suggested_inspection"]["page_numbers"] = [48]
        return result
    def read(args):
        result = inspect(args)
        result["pages"][0]["text"] = ("Credit risk disclosures\nNot prepared in compliance with reporting requirements."
            if args["page_numbers"] == [47] else "An adjacent source section was read.")
        if args["page_numbers"] == [48] and last_read == "failed":
            result.update(status="blocked", errors=[{"code": "SOURCE_NOT_FOUND"}])
        return result
    tools["find_source_pages"]["handler"], tools["inspect_source"]["handler"] = search, read
    responses = [call("plan_task", {"deliverables": ["analysis", "chart", "sources"]}),
        call("find_source_pages", {"source_id": "a", "query": "credit sectors"}, "find1"),
        call("inspect_source", {"source_id": "a", "page_numbers": [47]}, "read1"),
        *[call("find_source_pages", {"source_id": "a", "query": f"credit sectors detail {n}"}, f"find{n+2}") for n in range(3)],
        call("inspect_source", {"source_id": "b" if last_read == "other_source" else "a", "page_numbers": [48]}, "last_read")]
    runtime, client = build(responses, more=tools, max_decisions=7)
    result = runtime.run("Kredi dağılımını kaynakta bul, tablo ve grafik göster.")
    assert result["decisions"] == 7 and len(client.requests) == 7
    assert client.options[-1]["tools"]  # Last decision still needed a real read.
    state = journal.get(result["run_id"])["state"]
    assert not state.get("source_final_review")
    if last_read == "success":
        assert result["status"] == "partial" and "#page=47" in result["message"]
        assert "Not prepared" in result["message"] and "tablosu, oranlar ve grafik oluşturulmadı" in result["message"]
        assert not state["unresolved_errors"]
        assert {error["code"] for error in result["errors"]} >= {"SOURCE_DATA_NOT_VERIFIED", "TASK_DELIVERABLE_MISSING"}
    else:
        assert result["status"] == "blocked"
        assert "SOURCE_DATA_NOT_VERIFIED" not in {error["code"] for error in result["errors"]}
