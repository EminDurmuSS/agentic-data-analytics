"""User-facing process summaries stay tied to observable, scoped outcomes."""
import copy
import json

import pytest

from app.activity import activity_feed, activity_journey


def attempt(tool, result=None, *, call_id="call", **arguments):
    events = [{"kind": "tool_started", "payload": {"tool": tool, "call_id": call_id, "arguments": arguments}}]
    if result is not None:
        events.append({"kind": "tool_result", "payload": {"tool": tool, "call_id": call_id, "result": result}})
    return events


def item(journey, stage, index=0):
    return next(value for value in journey["stages"] if value["id"] == stage)["items"][index]


def test_search_links_do_not_claim_source_content_was_read():
    result = {"status": "ok", "results": [{"url": "https://reports.example.org/report"}], "progress": {"new_source_urls": 1}}
    journey = activity_journey(attempt("web_search", result), "running")
    assert "1 kaynak bağlantısı bulundu" in item(journey, "sources")["detail"]
    assert "henüz okunmadı" in item(journey, "sources")["detail"]
    result["progress"]["new_source_urls"] = 0
    journey = activity_journey(attempt("web_search", result), "blocked")
    assert item(journey, "sources")["status"] == "attention"
    assert "araştırma tamamlanmadı" in item(journey, "sources")["detail"]


def test_reference_catalogue_has_a_human_readable_record():
    journey = activity_journey(attempt("attach_reference_catalogue", {"status": "ok", "catalogue_attached": True}), "running")
    assert item(journey, "sources")["label"] == "Ortak veriler"
    assert "kataloğu" in item(journey, "sources")["detail"]


def test_shared_lakehouse_promotion_has_readable_success_and_failure_records():
    success = activity_journey(attempt("promote_dataset_to_shared_lakehouse", {
        "status": "ok", "dataset_id": "private", "shared_release_id": "private",
        "publication_performed": True,
    }), "completed")
    assert item(success, "data")["label"] == "Ortak lakehouse"
    assert "kalıcı ortak" in item(success, "data")["detail"]

    failure = activity_journey(attempt("promote_dataset_to_shared_lakehouse", {
        "status": "blocked", "errors": [{"code": "SHARED_PROMOTION_NOT_AUTHORIZED"}],
    }), "blocked")
    assert item(failure, "data")["status"] == "attention"
    assert "eklenemedi" in item(failure, "data")["detail"]
    assert "private" not in str(success) + str(failure)


def test_observed_document_journey_is_bounded_and_preserves_the_technical_ledger():
    events = [{"kind": "model_request", "payload": {"content": "PRIVATE MODEL TEXT"}}]
    for pages in [[1, 2], [2, 3]]:
        events += attempt("inspect_source", {"status": "ok", "source_id": "source_private", "filename": "/private/financial-report.pdf",
            "processed_pages": pages, "total_pages": 141, "text": "PRIVATE DOCUMENT TEXT", "sql": "SELECT secret"},
            call_id=str(pages), source_id="source_private", page_numbers=pages)
    events += attempt("ingest_source_table", {"status": "ok", "dataset_id": "dataset_private", "publication_performed": True,
        "row_count": 4}, source_id="source_private", table_id="table_private")
    events += attempt("execute", {"status": "ok", "analysis_id": "analysis_private", "row_count": 2}, columns=[{"name": "PRIVATE_ALIAS"}])
    events += attempt("explain_value", {"status": "ok", "source_references_complete": True, "source_files_verified": False,
        "value": 4783750292}, analysis_id="analysis_private", column="PRIVATE_ALIAS", period="2026-03")
    events += attempt("create_chart", {"status": "ok", "chart_id": "chart_private", "complete": True}, analysis_id="analysis_private")
    events.append({"kind": "run_finished", "payload": {"status": "completed"}})
    before, ledger = copy.deepcopy(events), activity_feed(events)
    journey = activity_journey(events)
    assert journey["status"] == "completed" and journey["event_count"] == len(events)
    assert [stage["id"] for stage in journey["stages"]] == ["sources", "data", "calculation", "checks", "presentation"]
    assert "3 sayfanın" in item(journey, "sources")["detail"]
    assert "financial-report.pdf" in item(journey, "sources")["detail"]
    assert item(journey, "sources")["count"] == 2
    assert "4 satır" in item(journey, "data")["detail"]
    assert "2 satırlık" in item(journey, "calculation")["detail"]
    assert "kaynak bağlantısı incelendi" in item(journey, "checks")["detail"]
    assert all(len(stage["summary"]) <= 100 for stage in journey["stages"])
    assert all("financial-report.pdf" not in stage["summary"] for stage in journey["stages"])
    rendered = json.dumps(journey, ensure_ascii=False)
    for text in ["PRIVATE", "SELECT", "/private", "source_private", "dataset_private", "analysis_private", "4783750292", "141 sayfa", "bayt"]:
        assert text not in rendered
    assert events == before and activity_feed(events) == ledger


@pytest.mark.parametrize("same_target", [False, True])
def test_only_retry_of_the_same_document_page_can_resolve_its_failure(same_target):
    events = attempt("inspect_source", {"status": "blocked", "errors": [{"message": "PRIVATE ERROR"}]},
                     source_id="s", page_numbers=[1], call_id="first")
    events += attempt("inspect_source", {"status": "ok", "source_id": "s", "processed_pages": [1 if same_target else 2]},
                      source_id="s", page_numbers=[1 if same_target else 2], call_id="second")
    journey = activity_journey(events, "completed")
    value = item(journey, "sources")
    assert value["count"] == 2
    assert value["status"] == ("complete" if same_target else "attention")
    assert ("düzeltildi" in value["detail"]) is same_target
    assert ("bazı denemeler sonuç vermedi" in journey["detail"]) is not same_target
    assert "PRIVATE ERROR" not in str(journey)


def test_unsupported_compiler_is_not_a_published_dataset_and_scoped_preparation_can_recover():
    events = attempt("ingest_source_table", {"status": "ok", "import_status": "unsupported_layout", "publication_performed": False},
                     source_id="s", table_id="raw", call_id="compile")
    journey = activity_journey(events, "partial")
    assert item(journey, "data")["status"] == "attention"
    assert "henüz" in item(journey, "data")["detail"]
    events += attempt("prepare_source_table", {"status": "ok", "source_id": "s", "table_id": "prepared",
        "preparation": {"source_table_id": "raw"}}, source_id="s", table_id="raw", call_id="prepare")
    events += attempt("publish_selected_table", {"status": "ok", "source_id": "s", "table_id": "prepared",
        "dataset_id": "d", "row_count": 3}, source_id="s", table_id="prepared", call_id="publish")
    journey = activity_journey(events, "completed")
    publication = item(journey, "data")
    assert publication["status"] == "complete" and "düzeltildi" in publication["detail"]
    assert "3 satır eklendi" in publication["detail"]


def test_unrelated_table_publication_never_hides_another_table_failure():
    events = attempt("ingest_source_table", {"status": "blocked"}, source_id="s", table_id="A", call_id="a")
    events += attempt("ingest_source_table", {"status": "ok", "dataset_id": "d", "row_count": 2},
                      source_id="s", table_id="B", call_id="b")
    assert item(activity_journey(events, "completed"), "data")["status"] == "attention"


def test_active_stage_requires_an_actual_started_tool_and_model_events_are_not_stages():
    events = [{"kind": "model_request", "payload": {"decision": 1}}]
    assert activity_journey(events, "running")["stages"] == []
    events += attempt("execute", columns=[{"metric_id": "private"}])
    journey = activity_journey(events, "running")
    assert item(journey, "calculation")["status"] == "active"
    assert "hesaplanıyor" in item(journey, "calculation")["detail"]
    assert "kaydedildi" not in item(journey, "calculation")["detail"]
    events.append({"kind": "tool_result", "payload": {"tool": "execute", "call_id": "call",
        "result": {"status": "ok", "analysis_id": "a", "row_count": 2}}})
    events.append({"kind": "model_request", "payload": {"decision": 2}})
    assert activity_journey(events, "running")["stages"][0]["status"] == "complete"


def test_recorded_plan_does_not_claim_a_calculation_before_actual_analysis_starts():
    events = attempt("plan_task", {"status": "ok", "task_plan": {"deliverables": ["analysis", "chart"]}})
    assert activity_journey(events, "running")["stages"] == []
    events += attempt("inspect_source", {"status": "ok", "source_id": "s", "processed_pages": [1, 2]}, source_id="s")
    events += attempt("ingest_source_table", source_id="s", table_id="t")
    for status in ["running", "failed"]:
        journey = activity_journey(events, status)
        assert [stage["id"] for stage in journey["stages"]] == ["sources", "data"]
        assert journey["event_count"] == len(events)
    assert any(value["tool"] == "plan_task" for value in activity_feed(events))


@pytest.mark.parametrize("status", ["partial", "blocked", "failed", "needs_input"])
def test_terminal_incomplete_work_cannot_appear_all_green_after_a_successful_tool(status):
    events = attempt("create_chart", {"status": "ok", "chart_id": "c", "complete": True}, analysis_id="a")
    journey = activity_journey(events, status)
    assert journey["status"] == status
    assert journey["stages"][-1]["status"] == "attention"
    assert journey["stages"][-1]["items"][-1]["label"] == "Tamamlanma durumu"


def test_recorded_recovery_deduplicates_start_and_does_not_double_count_published_rows():
    events = attempt("publish_selected_table", source_id="s", table_id="t")
    events.append({"kind": "tool_recovered", "payload": {"tool": "publish_selected_table", "call_id": "call",
        "result": {"status": "ok", "dataset_id": "d", "row_count": 7}}})
    events.append({"kind": "tool_reused", "payload": {"tool": "publish_selected_table", "call_id": "replay",
        "result": {"status": "ok", "dataset_id": "d", "row_count": 7}}})
    value = item(activity_journey(events, "completed"), "data")
    assert value["status"] == "complete" and value["count"] == 2
    assert "7 satır" in value["detail"] and "14" not in value["detail"]
    assert "yeniden kullanıldı" in value["detail"]


def test_web_metadata_exposes_only_hostname_not_url_credentials_or_query():
    events = attempt("research_web", {"status": "ok", "sources": [{
        "url": "https://user:PRIVATE_PASSWORD@reports.example.org/path?token=PRIVATE_TOKEN", "title": "PRIVATE TITLE"}]}, query="PRIVATE QUERY")
    value = item(activity_journey(events, "completed"), "sources")
    assert "reports.example.org" in value["detail"]
    assert "PRIVATE" not in str(value)


def test_incomplete_source_reference_is_not_presented_as_verification():
    events = attempt("explain_value", {"status": "ok", "source_references_complete": False,
        "source_files_verified": False, "lineage_issues": ["PRIVATE"]}, analysis_id="a", column="c", period="2026-03")
    value = item(activity_journey(events, "partial"), "checks")
    assert value["status"] == "attention"
    assert "doğrulandı" not in value["detail"] and "PRIVATE" not in str(value)


def test_each_document_attempt_shows_observed_pages_and_rows_in_order():
    events = attempt("inspect_source", {"status": "ok", "source_id": "source_secret", "processed_pages": list(range(1, 31)),
        "cached_pages": list(range(1, 142)), "total_pages": 141, "text": "PRIVATE TEXT 4783750292"},
        call_id="inspect-first", page_numbers=[99], source_id="source_secret")
    events += attempt("find_source_pages", {"status": "ok", "matches": [{"page": 11, "excerpt": "PRIVATE 4783750292"},
        {"page": 42}, {"page": 43}], "searched_pages": list(range(1, 142)), "total_matches": 80, "complete": True},
        call_id="find-pages", query="PRIVATE SEARCH", source_id="source_secret")
    events += attempt("inspect_source", {"status": "ok", "processed_pages": [11]}, call_id="inspect-selected", page_numbers=[11])
    events += attempt("read_source_table", {"status": "ok", "page": 11, "row_count": 900,
        "rows": [{"candidate_row": row, "values": {"amount": 4783750292}} for row in (43, 44, 45)]},
        call_id="read-rows", table_id="table_secret", row_start=40, row_limit=100)
    before = copy.deepcopy(events)
    value = item(activity_journey(events, "completed"), "sources")
    assert value["count"] == 4 and len(value["actions"]) == 4
    assert value["actions"] == [
        {"label": "Belgenin 1-30. sayfaları incelendi.", "status": "complete"},
        {"label": "Arama sözcükleriyle eşleşen 11, 42-43. sayfalar bulundu.", "status": "complete"},
        {"label": "Belgenin 11. sayfası incelendi.", "status": "complete"},
        {"label": "11. sayfadaki tablonun 43-45. satırları okundu.", "status": "complete"},
    ]
    rendered = json.dumps(value["actions"], ensure_ascii=False)
    for forbidden in ("PRIVATE", "4783750292", "900", "141", "99", "source_secret", "table_secret", "row_start", "inspect_source"):
        assert forbidden not in rendered
    assert events == before


def test_source_table_row_search_is_visible_without_exposing_query_or_values():
    events = attempt("find_source_table_rows", {"status": "ok", "source_id": "source_secret",
        "table_id": "table_secret", "page": 11, "total_matches": 1,
        "rows": [{"candidate_row": 31, "values": {"Line": "TOTAL ASSETS", "Total": "4783750292"}}]},
        source_id="source_secret", table_id="table_secret", query="PRIVATE QUERY")
    before = copy.deepcopy(events)
    value = item(activity_journey(events, "completed"), "sources")
    assert value["status"] == "complete"
    assert value["actions"] == [{"label": "11. sayfadaki tablonun 31. satırı bulundu.", "status": "complete"}]
    assert "seçili tablolardan 1 satır okundu" in value["detail"]
    rendered = json.dumps(value, ensure_ascii=False)
    for forbidden in ("PRIVATE", "4783750292", "source_secret", "table_secret", "TOTAL ASSETS"):
        assert forbidden not in rendered
    assert events == before


def test_page_search_with_no_matches_describes_only_the_searched_range():
    events = attempt("find_source_pages", {"status": "ok", "matches": [], "searched_pages": list(range(1, 31)),
        "complete": False, "total_pages": 141, "next_start_page": 31}, query="PRIVATE")
    value = item(activity_journey(events, "running"), "sources")["actions"][0]
    assert "1-30. sayfalarda arandı; eşleşme bulunamadı" in value["label"]
    assert "Tarama kısmi" in value["label"]
    assert "141" not in value["label"] and "PRIVATE" not in value["label"]


def test_long_pdf_search_does_not_claim_all_pages_were_inspected_or_hide_stall():
    events = attempt("inspect_source", {"status": "ok", "source_id": "s", "processed_pages": list(range(1, 31))})
    for index in range(2):
        events += attempt("find_source_pages", {"status": "ok", "source_id": "s",
            "searched_pages": list(range(1, 142)), "matches": [{"page": 90}], "complete": True}, call_id=str(index))
    events += attempt("find_source_pages", {"status": "blocked", "errors": [{"code": "NO_PROGRESS", "message": "PRIVATE"}]})
    before = copy.deepcopy(events)
    value = item(activity_journey(events, "blocked"), "sources")
    assert "141 sayfada metin araması yapıldı" in value["detail"]
    assert "30 sayfanın içeriği incelendi" in value["detail"]
    assert "282" not in value["detail"] and "141 sayfanın içeriği" not in value["detail"]
    assert "Sayfa araması tekrarlandı" in value["actions"][-1]["label"]
    assert "PRIVATE" not in str(value) and events == before


def test_failed_attempt_stays_visible_after_a_successful_retry():
    events = attempt("inspect_source", {"status": "blocked", "message": "PRIVATE ERROR"},
        call_id="first", source_id="s", page_numbers=[11])
    events += attempt("inspect_source", {"status": "ok", "processed_pages": [11]},
        call_id="retry", source_id="s", page_numbers=[11])
    value = item(activity_journey(events, "completed"), "sources")
    assert value["status"] == "complete"
    assert value["actions"][0] == {"label": "Belgenin 11. sayfası incelenemedi.", "status": "attention"}
    assert value["actions"][1] == {"label": "Belgenin 11. sayfası incelendi.", "status": "complete"}
    assert "PRIVATE" not in str(value["actions"])


def test_repeated_source_read_explains_recovery_without_claiming_new_read():
    events = attempt("inspect_source", {"status": "blocked", "errors": [
        {"code": "SOURCE_READ_REPEATED", "message": "PRIVATE"}]}, source_id="s", page_numbers=[92, 93, 94, 95])
    action = item(activity_journey(events, "running"), "sources")["actions"][0]
    assert action["status"] == "attention"
    assert "Aynı kaynak okuması tekrarlandı" in action["label"]
    assert "incelendi" not in action["label"] and "PRIVATE" not in str(action)


def test_active_and_interrupted_actions_do_not_claim_completion():
    events = attempt("inspect_source", page_numbers=[11, 12])
    action = item(activity_journey(events, "running"), "sources")["actions"][0]
    assert action == {"label": "Belgenin 11-12. sayfaları inceleniyor.", "status": "active"}
    action = item(activity_journey(events, "failed"), "sources")["actions"][0]
    assert action["status"] == "attention" and "bu adım bitmeden durdu" in action["label"]
    assert "incelendi" not in action["label"]
    events.append({"kind": "tool_result", "payload": {"tool": "inspect_source", "call_id": "call",
        "result": {"status": "ok", "processed_pages": [11, 12]}}})
    actions = item(activity_journey(events, "running"), "sources")["actions"]
    assert len(actions) == 1 and actions[0]["status"] == "complete"


def test_recovered_and_reused_actions_are_explicit_without_double_counting_starts():
    events = attempt("publish_selected_table", call_id="first")
    result = {"status": "ok", "dataset_id": "dataset_secret", "row_count": 7, "amount": 4783750292}
    events.append({"kind": "tool_recovered", "payload": {"tool": "publish_selected_table", "call_id": "first", "result": result}})
    events.append({"kind": "tool_reused", "payload": {"tool": "publish_selected_table", "call_id": "replay", "result": result}})
    value = item(activity_journey(events, "completed"), "data")
    assert len(value["actions"]) == 2
    assert value["actions"][0]["label"].startswith("Kesinti öncesindeki sonuç kullanıldı:")
    assert value["actions"][1]["label"].startswith("Önceki sonuç yeniden kullanıldı:")
    assert all(action["status"] == "complete" for action in value["actions"])
    assert "4783750292" not in str(value["actions"]) and "dataset_secret" not in str(value["actions"])


@pytest.mark.parametrize("tool,result,stage,expected", [
    ("discover", {"total": 3}, "sources", "3 veri adayı"),
    ("dimension_values", {"total": 0}, "sources", "karşılaştırma grubu bulunamadı"),
    ("describe", {}, "sources", "birimi, dönemi ve kapsamı"),
    ("web_search", {"results": [{"url": "https://example.org"}]}, "sources", "1 kaynak bağlantısı"),
    ("prepare_source_table", {"row_count": 2}, "data", "2 satırlık kaynak tablosu"),
    ("combine_source_tables", {"source_pages": [11, 12]}, "data", "11-12. sayfalardaki devam tabloları"),
    ("ingest_source_table", {"dataset_id": "d", "row_count": 1}, "data", "1 satır kaynak verisi"),
    ("execute", {"analysis_id": "a", "row_count": 2}, "calculation", "2 satırlık analiz tablosu"),
    ("summarize_analysis", {}, "calculation", "sonuç özeti hazırlandı"),
    ("explain_value", {"source_references_complete": True}, "checks", "özgün kaynak bağlantısı"),
    ("validate_plan", {}, "checks", "Birim, dönem ve hesap kuralları"),
    ("create_chart", {"chart_id": "c"}, "presentation", "Analiz grafiği kaydedildi"),
])
def test_other_actions_use_observable_counts_and_plain_language(tool, result, stage, expected):
    events = attempt(tool, {"status": "ok", **result, "value": 4783750292, "sql": "PRIVATE SQL"})
    action = item(activity_journey(events, "completed"), stage)["actions"][0]
    assert expected in action["label"]
    assert action["status"] == "complete"
    assert "4783750292" not in action["label"] and "PRIVATE" not in action["label"]
    assert set(action) == {"label", "status"}
