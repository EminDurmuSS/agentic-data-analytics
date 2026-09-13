import copy
import gzip
import json
from pathlib import Path

from agentic_analytics.agent.context import _model_tool_result, model_messages
from agentic_analytics.agent.run_store import canonical


def test_ragged_ocr_source_rows_remain_reviewable_in_model_context():
    source = {"status": "ok", "source_id": "source_test", "table_id": "table_001", "columns": ["label", "amount"],
              "row_count": 1, "layout_review_required": True,
              "rows": [{"candidate_row": 1, "values": None, "raw_cells": ["A", "10", "20"]}]}
    result = _model_tool_result("read_source_table", source)
    assert result["layout_review_required"]
    assert result["rows"] == [{"candidate_row": 1, "values": {}, "raw_cells": ["A", "10", "20"], "requires_review": True}]
    assert source["rows"][0]["values"] is None


def test_actual_published_pdf_journal_fits_context_without_losing_task_proofs_or_raw_ledger():
    # Provider-visible records from the preserved public Garanti PDF acceptance
    # run. Every artifact passed, but final synthesis hit the 150k context cap.
    fixture_path = Path(__file__).parent / "fixtures" / "mentor_pdf_published_context.json.gz"
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(gzip.decompress(fixture_bytes))
    assert fixture["provenance"]["original_error_codes"] == ["CONTEXT_BUDGET_EXCEEDED"]
    state = fixture["state"]
    raw = canonical(state)
    # A large budget needs no archival projection at all.
    full = model_messages(state, context_factory=lambda _: fixture["context"], charts_enabled=True,
                          max_context_chars=1000000)
    assert not any("published_source_navigation_receipt" in message.get("content", "") for message in full)
    view = model_messages(state, context_factory=lambda _: fixture["context"], charts_enabled=True,
                          max_context_chars=150000)
    assert len(view[0]["content"]) + len(canonical(view[1:])) <= 150000
    assert len(view) == len(state["messages"]) + 1
    receipt_count = 0
    call_names = {call["id"]:call["function"]["name"] for message in state["messages"] for call in message.get("tool_calls", [])}
    for original, current in zip(state["messages"], view[1:]):
        if original.get("role") != "tool":
            assert current == original
            continue
        before, after = json.loads(original["content"]), json.loads(current["content"])
        if after.get("model_evidence_view") == "published_source_navigation_receipt":
            receipt_count += 1
            assert after["source_id"] == before["source_id"]
            assert after["raw_sha256"] == fixture["provenance"]["source_raw_sha256"]
            assert after["full_evidence_retained"] and after["re_read"]["arguments"]["source_id"] == before["source_id"]
            assert after.get("warnings") == before.get("warnings")
            assert [table["table_id"] for table in after.get("tables", [])] == [table["table_id"] for table in before.get("tables", [])]
            for old, receipt in zip(before.get("tables", []), after.get("tables", [])):
                for key in ("page", "layout_review_required", "quality_notes", "review", "missing_formula_cache"):
                    assert receipt.get(key) == old.get(key)
            assert [page["page"] for page in after.get("pages", [])] == [page["page"] for page in before.get("pages", [])]
        elif call_names[original["tool_call_id"]] in {"ingest_source_table", "execute", "create_chart", "explain_value", "plan_task"}:
            assert current == original
    assert receipt_count
    assert canonical(fixture["context"]["current_task"]["required_outputs"]) in view[0]["content"]
    assert canonical(state) == raw and fixture_path.read_bytes() == fixture_bytes


def test_pressure_compaction_keeps_failed_recovery_unpublished_and_new_source_reads():
    messages = [{"role":"user", "content":"Kaynağı içeri al ve diğer adayın inceleme hatasını koru"}]
    def add(name, identifier, source_id, result, **args):
        messages.extend([
            {"role":"assistant","content":None,"tool_calls":[{"id":identifier,"type":"function",
                "function":{"name":name,"arguments":canonical({"source_id":source_id, **args})}}]},
            {"role":"tool","tool_call_id":identifier,"content":canonical({"source_id":source_id, **result})}])
    source = {"status":"ok", "raw_sha256":"a"*64, "text":"evidence "*1000,
        "pages":[{"page":1,"text":"page evidence "*1000}], "warnings":[{"code":"PARTIAL_INSPECTION"}],
        "tables":[{"table_id":"unpublished_candidate", "page":1, "row_count":1, "columns":["amount"],
                   "layout_review_required":True,"quality_notes":["Merged source cells require review"],
                   "preview":[{"amount":"10"}]}]}
    add("inspect_source","historical","published",source,page_numbers=[1])
    failure = {"status":"blocked","errors":[{"code":"TABLE_REVIEW_REQUIRED", "message":"Review original source"}],
               "recovery":{"alternative_candidates":[{"table_id":"other_candidate","page":2}]}}
    add("read_source_table","failure","published",failure,table_id="unpublished_candidate",row_start=1,limit=1)
    add("inspect_source","unpublished","another",source,page_numbers=[1])
    add("ingest_source_table","publication","published",{"status":"ok","dataset_id":"dataset_one",
        "table_id":"published_table", "publication_performed":True,"available_series":[{"metric_id":"metric_one"}]},table_id="original")
    add("read_source_table","active-read","published",{"status":"ok","table_id":"other_candidate", "page":2,
        "row_count":1,"columns":["amount"],"rows":[{"candidate_row":1,"values":{"amount":20}}]},table_id="other_candidate",row_start=1,limit=1)
    state = {"messages":messages}
    raw = copy.deepcopy(state)
    full = model_messages(state,context_factory=lambda _: {},charts_enabled=False,max_context_chars=1000000)
    full_size = len(full[0]["content"]) + len(canonical(full[1:]))
    view = model_messages(state,context_factory=lambda _: {},charts_enabled=False,max_context_chars=full_size-1500)
    before = {m["tool_call_id"]:json.loads(m["content"]) for m in full if m.get("role")=="tool"}
    after = {m["tool_call_id"]:json.loads(m["content"]) for m in view if m.get("role")=="tool"}
    receipt = after["historical"]
    assert receipt["model_evidence_view"] == "published_source_navigation_receipt"
    assert receipt["re_read"] == {"tool":"inspect_source","arguments":{"source_id":"published","page_numbers":[1]}}
    assert receipt["tables"][0]["layout_review_required"]
    assert receipt["warnings"] == before["historical"]["warnings"]
    for identifier in ("failure","unpublished","publication","active-read"):
        assert after[identifier] == before[identifier]
    assert state == raw


def test_followup_keeps_original_request_and_source_navigation_without_replaying_pdf():
    fixture_path = Path(__file__).parent / "fixtures" / "mentor_pdf_published_context.json.gz"
    fixture = json.loads(gzip.decompress(fixture_path.read_bytes()))
    state = copy.deepcopy(fixture["state"])
    original_request = state["messages"][0]
    previous_count = len(state["messages"])
    state["messages"].append({"role": "user", "content": "Bu karşılaştırmaya başka bir banka ekle."})
    # Current-turn proof must stay available even when earlier calls were large.
    current = {"status": "ok", "source_id": "new_source", "table_id": "new_table", "columns": ["amount"],
               "row_count": 1, "rows": [{"candidate_row": 1, "values": {"amount": "20"}}]}
    state["messages"].extend([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "new_read", "type": "function",
            "function": {"name": "read_source_table", "arguments": canonical({"source_id": "new_source", "table_id": "new_table"})}}]},
        {"role": "tool", "tool_call_id": "new_read", "content": canonical(current)},
    ])
    untouched = canonical(state)
    view = model_messages(state, context_factory=lambda _: fixture["context"], charts_enabled=True,
                          max_context_chars=150000)
    assert view[1] == original_request
    assert len(view) == len(state["messages"]) + 1
    receipts = [json.loads(message["content"]) for message in view[1:previous_count+1]
                if message.get("role") == "tool" and '"historical_only":true' in message["content"]]
    assert receipts
    assert any(item.get("re_read", {}).get("arguments", {}).get("source_id") for item in receipts)
    assert any(item.get("dataset_id") for item in receipts)
    assert all(item["full_evidence_retained"] for item in receipts)
    assert len(canonical(view[1:previous_count+1])) < len(canonical(state["messages"][:previous_count])) * .5
    assert json.loads(view[-1]["content"]) == _model_tool_result("read_source_table", current)
    calls = set()
    for message in view:
        calls.update(call["id"] for call in message.get("tool_calls", []))
        if message.get("role") == "tool":
            assert message["tool_call_id"] in calls
    assert canonical(state) == untouched


def test_followup_keeps_prior_source_quality_failure_and_does_not_offer_mutation_replay():
    failure = {"status": "blocked", "errors": [{"code": "TABLE_REVIEW_REQUIRED"}],
               "recovery": {"table_id": "ambiguous_table"}}
    messages = [{"role": "user", "content": "Özgün dönemi ve birimi koru."}]
    for name, call_id, result in [
        ("ingest_source_table", "publish", {"status": "ok", "source_id": "source_a", "dataset_id": "dataset_a",
            "warnings": [{"code": "PARTIAL_INSPECTION"}], "unused_large_preview": "data " * 1000}),
        ("read_source_table", "review", failure),
    ]:
        messages.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
                "function": {"name": name, "arguments": canonical({"source_id": "source_a", "table_id": "table_a"})}}]},
            {"role": "tool", "tool_call_id": call_id, "content": canonical(result)}])
    messages.append({"role": "user", "content": "Şimdi bir başka kaynak ekle."})
    view = model_messages({"messages": messages}, context_factory=lambda _: {}, charts_enabled=False, max_context_chars=50000)
    results = {item["tool_call_id"]: json.loads(item["content"]) for item in view if item.get("role") == "tool"}
    assert results["review"]["errors"] == failure["errors"]
    assert results["review"]["recovery"] == failure["recovery"]
    assert results["publish"]["warnings"] == [{"code": "PARTIAL_INSPECTION"}]
    assert "re_read" not in results["publish"]


def test_prior_failed_search_queries_are_archived_without_orphaning_parallel_reads():
    stale_query = 'example.org "31_March_2026_Consolidated_Financial_Report.pdf"'
    call = lambda name, identifier, args: {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": canonical(args)}}
    source = {"status": "ok", "source_id": "source_a", "text": "Read financial evidence."}
    search = {"status": "ok", "query": stale_query, "results": [{"url": "https://example.org/", "title": "Example"}]}
    failure = {"status": "blocked", "errors": [{"code": "NO_PROGRESS"}]}
    messages = [
        {"role": "user", "content": "Mart 2026 konsolide aktiflerini milyon TL ile karşılaştır."},
        {"role": "assistant", "content": None, "tool_calls": [
            call("web_search", "search1", {"query": stale_query}),
            call("inspect_source", "read", {"source_id": "source_a"})]},
        {"role": "tool", "tool_call_id": "search1", "content": canonical(search)},
        {"role": "tool", "tool_call_id": "read", "content": canonical(source)},
        {"role": "assistant", "content": None, "tool_calls": [call("web_search", "search2", {"query": stale_query})]},
        {"role": "tool", "tool_call_id": "search2", "content": canonical(failure)},
        {"role": "assistant", "content": "Önceki arama tamamlanamadı."},
        {"role": "user", "content": "İş Bankası ekle"},
        {"role": "assistant", "content": None, "tool_calls": [call("web_search", "current", {"query": "Current request"})]},
        {"role": "tool", "tool_call_id": "current", "content": canonical(search)},
    ]
    original = copy.deepcopy(messages)
    view = model_messages({"messages": messages}, context_factory=lambda _: {}, charts_enabled=False, max_context_chars=50000)[1:]
    current = next(i for i, message in enumerate(view) if message.get("content") == "İş Bankası ekle")
    assert "31_March_2026_Consolidated_Financial_Report.pdf" not in canonical(view[:current])
    assert view[current:] == messages[7:]
    receipts = [json.loads(message["content"]) for message in view[:current]
                if message.get("role") == "assistant" and '"historical_searches"' in (message.get("content") or "")]
    assert len(receipts) == 1 and receipts[0]["historical_searches"] == 2
    assert receipts[0]["error_codes"] == ["NO_PROGRESS"]
    assert receipts[0]["unverified_navigation"] == search["results"]
    pending = set()
    for message in view:
        if message.get("role") == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
        else:
            assert not pending
            pending.update(call["id"] for call in message.get("tool_calls", []))
    assert not pending
    assert any(message.get("tool_call_id") == "read" for message in view)
    assert messages == original
