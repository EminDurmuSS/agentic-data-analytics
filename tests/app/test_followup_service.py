"""Optional suggestions never change a completed financial run or cross owners."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.context import AppContext
from app.followups import FollowupService
from app.models import RunBody
from app.server import create_app
from agentic_analytics.providers.mia import MiaClient


ITEM = {"id": "compare_periods", "label": "Önceki dönem", "prompt": "Önceki dönemle karşılaştırabilir misin?",
        "reason": "Dönemler arası değişimi incelemek için.", "requires_new_data": True}


def build_context(store, wid, run, history):
    return {"workspace_id": wid, "run_id": run["run_id"], "conversation_id": run["conversation_id"],
            "analysis_id": (run.get("result") or {}).get("analysis_id"),
            "current_question": run["message"], "answer": run["result"]["message"],
            "history": [{"question": previous["message"], "answer": previous["result"]["message"]}
                        for previous in history if previous.get("result")], "candidates": [ITEM]}


class Generator:
    def __init__(self, *, wait=False, error=None):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not wait:
            self.release.set()
        self.error = error

    def __call__(self, client, context):
        self.calls.append(copy.deepcopy(context))
        self.started.set()
        if not self.release.wait(10):
            raise AssertionError("Test worker was not released")
        if self.error:
            raise self.error
        return {"items": [ITEM], "method": "model_ranked", "usage": {"total_tokens": 120}}


@pytest.fixture
def setup_service(tmp_path):
    context = AppContext(tmp_path, None, searxng_url=False)
    wid = context.create_workspace("Followups", "generic")["workspace_id"]
    services, generators = [], []

    def make(generator=None, **kwargs):
        generator = generator or Generator()
        service = FollowupService(tmp_path / "followup-test", context.store, context.run_store,
                                  client=object(), builder=build_context, generator=generator, **kwargs)
        services.append(service)
        generators.append(generator)
        return service, generator

    yield context, wid, make
    for generator in generators:
        generator.release.set()
    for service in services:
        service.close()
    context.close()


def finish(context, wid, *, message="Veriyi açıkla.", conversation_id=None, analysis_id=None, status="completed", extra=None):
    run = context.run_store.start(wid, message, conversation_id=conversation_id)
    state = {**run["state"], "messages": [{"role": "assistant", "content": "PRIVATE_TOOL_REASONING"}]}
    result = {"run_id": run["run_id"], "status": status, "analysis_id": analysis_id,
              "message": "Kayıtlı sonuç hazır.", "tool_results": [], "usage": [], **(extra or {})}
    context.run_store.finish(run["run_id"], state, result)
    return context.run_store.get(run["run_id"])


def saved_analysis(context, wid, value):
    current = context.store.workspace(wid)
    return context.store.save_analysis(wid, pd.DataFrame({"period": ["2026-01"], "amount": [value]}),
                                       {}, {}, expected_version=current["version"])["analysis_id"]


def test_get_never_generates_and_owner_mismatch_is_404(setup_service):
    context, wid, make = setup_service
    service, generator = make()
    run = finish(context, wid)
    before = copy.deepcopy(context.run_store.get(run["run_id"]))
    assert service.get(wid, run["run_id"])["reason"] == "not_requested"
    assert generator.calls == []
    assert not service.root.exists()
    other = context.create_workspace("Other", "generic")["workspace_id"]
    for method in (service.get, service.start):
        with pytest.raises(HTTPException) as caught:
            method(other, run["run_id"])
        assert caught.value.status_code == 404
    assert context.run_store.get(run["run_id"]) == before


def test_parallel_start_is_once_and_cache_does_not_change_main_ledger(setup_service):
    context, wid, make = setup_service
    service, generator = make(Generator(wait=True))
    run = finish(context, wid)
    before = copy.deepcopy(run)
    conversation = context.run_store.conversation(run["conversation_id"], wid)
    with ThreadPoolExecutor(max_workers=5) as executor:
        replies = list(executor.map(lambda _: service.start(wid, run["run_id"]), range(5)))
    assert all(reply["status"] == "pending" for reply in replies)
    assert generator.started.wait(5)
    assert service.get(wid, run["run_id"])["status"] == "pending"
    generator.release.set()
    service.futures[run["run_id"]].result(timeout=5)
    ready = service.get(wid, run["run_id"])
    assert ready["status"] == "ready" and ready["items"] == [ITEM]
    assert len(ready["context_digest"]) == 64
    assert service.start(wid, run["run_id"]) == ready
    assert len(generator.calls) == 1
    assert "PRIVATE_TOOL_REASONING" not in json.dumps(generator.calls)
    sidecar = json.loads(service._path(run).read_text())
    assert sidecar["usage"] == {"total_tokens": 120}
    assert "usage" not in ready
    assert context.run_store.get(run["run_id"]) == before
    assert context.run_store.conversation(run["conversation_id"], wid) == conversation
    assert context.run_store.events(run["run_id"]) == []


@pytest.mark.parametrize("same_conversation", [True, False])
def test_new_turn_discards_pending_questions_even_when_analysis_does_not_change(setup_service, same_conversation):
    context, wid, make = setup_service
    service, generator = make(Generator(wait=True))
    aid = saved_analysis(context, wid, 5)
    run = finish(context, wid, analysis_id=aid)
    service.start(wid, run["run_id"])
    assert generator.started.wait(5)
    next_run = finish(context, wid, analysis_id=aid,
                      conversation_id=run["conversation_id"] if same_conversation else None, message="Yeni soru.")
    next_before = copy.deepcopy(next_run)
    generator.release.set()
    service.futures[run["run_id"]].result(timeout=5)
    assert service.get(wid, run["run_id"])["status"] == "stale"
    sidecar = json.loads(service._path(run).read_text())
    assert sidecar["status"] == "stale" and sidecar["items"] == []
    assert context.run_store.get(next_run["run_id"]) == next_before


def test_changed_analysis_head_invalidates_ready_cache(setup_service):
    context, wid, make = setup_service
    service, generator = make()
    run = finish(context, wid, analysis_id=saved_analysis(context, wid, 5))
    service.start(wid, run["run_id"])
    service.futures[run["run_id"]].result(timeout=5)
    saved_analysis(context, wid, 6)
    assert service.get(wid, run["run_id"])["status"] == "stale"
    assert service.start(wid, run["run_id"])["items"] == []
    assert len(generator.calls) == 1


def test_context_digest_detects_changed_input_without_modifying_old_receipt(setup_service):
    context, wid, make = setup_service
    service, _ = make()
    run = finish(context, wid)
    service.start(wid, run["run_id"])
    service.futures[run["run_id"]].result(timeout=5)
    before = service._path(run).read_bytes()
    service.builder = lambda *args: {**build_context(*args), "updated_projection": True}
    assert service.get(wid, run["run_id"])["status"] == "stale"
    assert service._path(run).read_bytes() == before


def test_failed_generator_is_optional_redacted_and_not_retried(setup_service):
    context, wid, make = setup_service
    service, generator = make(Generator(error=RuntimeError("SECRET_PROVIDER_BODY")))
    run = finish(context, wid)
    service.start(wid, run["run_id"])
    service.futures[run["run_id"]].result(timeout=5)
    result = service.start(wid, run["run_id"])
    assert result["status"] == "unavailable" and result["reason"] == "generation_failed"
    assert "SECRET_PROVIDER_BODY" not in service._path(run).read_text()
    assert len(generator.calls) == 1
    assert context.run_store.get(run["run_id"])["result"] == run["result"]


@pytest.mark.parametrize("status,extra", [
    ("partial", {}), ("blocked", {}), ("failed", {}),
    ("completed", {"errors": [{"code": "MISSING_ANALYSIS"}]}),
    ("completed", {"tool_results": [{"tool": "ask_user", "result": {"status": "ok"}}]}),
    ("completed", {"warnings": [{"code": "CLARIFICATION_AFTER_RESULT"}]}),
    ("completed", {"message": "Devam için soru: Hangi dönem?"}),
])
def test_incomplete_work_and_clarifications_do_not_start_suggestions(setup_service, status, extra):
    context, wid, make = setup_service
    service, generator = make()
    run = finish(context, wid, status=status, extra=extra)
    assert service.start(wid, run["run_id"])["reason"] == "run_not_eligible"
    assert generator.calls == [] and not service.root.exists()


def test_history_contains_only_last_six_runs_in_current_conversation(setup_service):
    context, wid, make = setup_service
    service, generator = make()
    run = finish(context, wid, message="First conversation")
    cid = run["conversation_id"]
    finish(context, wid, message="OTHER_CONVERSATION_SECRET")
    for number in range(8):
        run = finish(context, wid, message=f"Question {number}", conversation_id=cid)
    service.start(wid, run["run_id"])
    service.futures[run["run_id"]].result(timeout=5)
    history = generator.calls[0]["history"]
    assert [turn["question"] for turn in history] == [f"Question {number}" for number in range(2, 8)]
    assert "OTHER_CONVERSATION_SECRET" not in json.dumps(generator.calls)


def test_deleted_run_is_not_recreated_by_pending_worker(setup_service):
    context, wid, make = setup_service
    service, generator = make(Generator(wait=True))
    run = finish(context, wid)
    service.start(wid, run["run_id"])
    assert generator.started.wait(5)
    before = service._path(run).read_bytes()
    context.run_store.delete_conversation(wid, run["conversation_id"])
    generator.release.set()
    service.futures[run["run_id"]].result(timeout=5)
    with pytest.raises(HTTPException):
        service.get(wid, run["run_id"])
    assert service._path(run).read_bytes() == before


def test_orphaned_pending_cache_does_not_auto_retry(setup_service):
    context, wid, make = setup_service
    service, generator = make()
    run = finish(context, wid)
    service.start(wid, run["run_id"])
    service.futures[run["run_id"]].result(timeout=5)
    path = service._path(run)
    data = json.loads(path.read_text())
    data.update(status="pending", items=[])
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert service.get(wid, run["run_id"])["reason"] == "interrupted"
    assert service.start(wid, run["run_id"])["status"] == "unavailable"
    assert len(generator.calls) == 1 and path.read_bytes() == before


def test_second_service_observes_live_lock_without_starting_a_second_call(setup_service):
    context, wid, make = setup_service
    first, generator = make(Generator(wait=True))
    second, other_generator = make()
    run = finish(context, wid)
    first.start(wid, run["run_id"])
    assert generator.started.wait(5)
    assert second.start(wid, run["run_id"])["status"] == "pending"
    assert second.get(wid, run["run_id"])["status"] == "pending"
    assert other_generator.calls == []
    generator.release.set()
    first.futures[run["run_id"]].result(timeout=5)
    assert second.get(wid, run["run_id"])["items"] == [ITEM]


def test_optional_queue_is_bounded_without_affecting_second_main_result(setup_service):
    context, wid, make = setup_service
    service, generator = make(Generator(wait=True), max_pending=1)
    first = finish(context, wid)
    service.start(wid, first["run_id"])
    assert generator.started.wait(5)
    other = context.create_workspace("Other", "generic")["workspace_id"]
    second = finish(context, other)
    result = service.start(other, second["run_id"])
    assert result["status"] == "unavailable" and result["reason"] == "queue_full"
    assert context.run_store.get(second["run_id"])["result"]["status"] == "completed"
    assert len(generator.calls) == 1
    generator.release.set()


def test_api_and_main_job_finish_before_optional_generation(tmp_path):
    generator = Generator(wait=True)
    app = create_app(runtime_root=tmp_path, source_db=None, client=object(), followup_client=object(), searxng_url=False)
    context = app.state.context
    context.followups.builder = build_context
    context.followups.generator = generator
    try:
        with TestClient(app) as client:
            wid = context.create_workspace("Test", "generic")["workspace_id"]

            class Runtime:
                def run(self, message, **kwargs):
                    run = context.run_store.start(wid, message, **kwargs)
                    result = {"run_id": run["run_id"], "status": "completed", "message": "Sonuç hazır.", "analysis_id": None}
                    context.run_store.finish(run["run_id"], run["state"], result)
                    return result

            with patch.object(context, "runtime", return_value=Runtime()):
                job = context.submit(wid, RunBody(message="Başlat."))
                context.futures[job["job_id"]].result(timeout=5)
            assert generator.started.wait(5)
            finished = client.get(f"/api/jobs/{job['job_id']}").json()
            assert finished["status"] == "finished" and finished["result"]["status"] == "completed"
            run_id = finished["result"]["run_id"]
            url = f"/api/workspaces/{wid}/runs/{run_id}/followups"
            assert client.get(url).json()["status"] == "pending"
            assert client.post(url).json()["status"] == "pending"
            foreign = context.create_workspace("Foreign", "generic")["workspace_id"]
            assert client.post(f"/api/workspaces/{foreign}/runs/{run_id}/followups").status_code == 404
            generator.release.set()
            context.followups.futures[run_id].result(timeout=5)
            assert client.get(url).json()["items"] == [ITEM]
            assert client.get(f"/api/jobs/{job['job_id']}").json()["result"] == finished["result"]
    finally:
        generator.release.set()
        context.close()


def test_client_limits_are_dedicated_and_unconfigured_fake_is_not_reused(tmp_path):
    class Client:
        def __init__(self):
            self.limits = []

        def with_limits(self, **limits):
            self.limits.append(limits)
            return object()

    client = Client()
    context = AppContext(tmp_path / "bounded", None, client=client)
    assert context.followups.client is not client
    assert client.limits == [{"timeout": 20, "max_retries": 0}]
    context.close()
    fake = AppContext(tmp_path / "fake", None, client=object())
    assert fake.followups.client is None
    fake.close()


@pytest.mark.parametrize("already_answered", [False, True])
def test_production_builder_generator_and_api_use_displayed_answer_and_cached_selection(tmp_path, already_answered):
    calls = []

    def transport(endpoint, payload):
        calls.append(copy.deepcopy(payload))
        data = json.loads(payload["messages"][1]["content"])
        assert data["candidates"]
        selected_id = data["candidates"][0]["id"]
        return {"choices": [{"message": {"content": json.dumps({
            "answered_ids": [selected_id] if already_answered else [],
            "candidate_ids": [selected_id]})}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}

    provider = MiaClient("test-only-key", transport=transport)
    app = create_app(runtime_root=tmp_path, source_db=None, client=provider, searxng_url=False)
    context = app.state.context
    with TestClient(app) as client:
        wid = context.create_workspace("Measured deposits", "generic")["workspace_id"]
        schema = {"amount": {"kind": "stock", "status": "ready", "unit": "TRY", "currency": "TRY", "scale": 1}}
        plan = {"frequency": "monthly", "start": "2026-01", "end": "2026-03"}
        lineage = {"frequency": "monthly", "sources": {"amount": {"binding": {
            **schema["amount"], "title": "Kayıtlı mevduat", "metric_id": "test:deposits"}}}}
        saved = context.store.save_analysis(wid, pd.DataFrame({"period": ["2026-01", "2026-02", "2026-03"],
                                                               "amount": [100, 120, 150]}), plan, lineage,
                                             schema=schema, expected_version=0)
        previous = finish(context, wid, analysis_id=saved["analysis_id"], message="Mevduatı göster.",
                          extra={"message": "OLD_TECHNICAL_RECEIPT"})
        context.run_store.finish(previous["run_id"], {**previous["state"], "analysis_observed": True,
                                "analysis_id": saved["analysis_id"]}, previous["result"])
        run = finish(context, wid, analysis_id=saved["analysis_id"], conversation_id=previous["conversation_id"],
                     message="Mevcut tabloyu açıkla.", extra={"message": "OLD_TECHNICAL_RECEIPT"})
        context.run_store.finish(run["run_id"], {**run["state"], "analysis_observed": True,
                                "analysis_id": saved["analysis_id"]}, run["result"])
        before = context.run_store.get(run["run_id"])
        public = client.get(f"/api/workspaces/{wid}").json()["runs"][0]
        displayed = public["result"]["display_message"]
        url = f"/api/workspaces/{wid}/runs/{run['run_id']}/followups"
        assert client.get(url).json()["reason"] == "not_requested"
        assert calls == []
        assert client.post(url).json()["status"] == "pending"
        context.followups.futures[run["run_id"]].result(timeout=5)
        ready = client.get(url).json()
        assert ready["status"] == "ready"
        assert len(ready["items"]) == (0 if already_answered else 1)
        assert ready["analysis_id"] == saved["analysis_id"]
        selected_context = json.loads(calls[0]["messages"][1]["content"])
        assert selected_context["answer"] == " ".join(displayed.split())
        assert "OLD_TECHNICAL_RECEIPT" not in json.dumps(selected_context)
        assert "PRIVATE_TOOL_REASONING" not in json.dumps(selected_context)
        assert len(selected_context["history"]) == 1
        for _ in range(3):
            assert client.post(url).json() == ready
            assert client.get(url).json() == ready
        assert len(calls) == 1 and calls[0]["max_tokens"] == 700
        assert calls[0]["response_format"] == {"type": "json_object"}
        assert "tools" not in calls[0]
        assert provider.timeout == 60 and context.followups.client.timeout == 20
        assert context.run_store.get(run["run_id"]) == before
