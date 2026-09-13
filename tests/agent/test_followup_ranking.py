"""Model selection obeys the verified candidate and answered-topic contract."""
import copy
import json

import pytest

from agentic_analytics.agent.followups import FollowupError, generate_followups
from agentic_analytics.providers.mia import MiaClient


def context():
    return {
        "current_question": "A ve B kurumlarının toplam aktiflerini aynı birimde karşılaştır.",
        "answer": "Kurumların raporlama kapsamları farklıdır. Büyüklük oranı hesaplandı.",
        "history": [{"question": "Bu kaynak hangi dönemi kapsıyor?", "answer": "Mart 2026."}],
        "analysis": {"periods": ["2026-03"], "scope_warning": True},
        "candidates": [
            {"id": "previous_a", "intent": "new_period", "label": "Önceki döneme bakalım mı?",
             "prompt": "A kurumunun önceki dönem toplam aktifleri aynı kaynakta bulunursa değişimi ne kadar?",
             "reason": "Mevcut karşılaştırma tek dönemi kapsıyor.", "requires_new_data": True},
            {"id": "scope", "intent": "scope_match", "label": "Kapsamları eşleştirebilir miyiz?",
             "prompt": "A ve B için raporlama kapsamı eşleşen veriler bulunabilir mi?",
             "reason": "Mevcut oran farklı raporlama kapsamlarını karşılaştırıyor.", "requires_new_data": True},
            {"id": "previous_b", "intent": "new_period", "label": "B kurumunun önceki dönemi",
             "prompt": "B kurumunun önceki dönem toplam aktifleri aynı kaynakta bulunabilir mi?",
             "reason": "İkinci dönem önce kaynaktan doğrulanmalı.", "requires_new_data": True},
        ],
    }


class ChoiceClient:
    def __init__(self, value):
        self.value, self.calls = value, []

    def chat(self, messages, **options):
        self.calls.append((messages, options))
        return {"content": json.dumps({"answered_ids": [], **self.value}), "finish_reason": "stop", "tool_calls": [],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10, "private": "not public"},
                "reasoning_content": "private thoughts never become suggestions"}


def test_ranked_questions_preserve_verified_text_and_include_actual_answer_and_history():
    value, client = context(), ChoiceClient({"candidate_ids": ["scope", "previous_a", "previous_b"]})
    before = copy.deepcopy(value)
    result = generate_followups(client, value)
    assert [item["id"] for item in result["items"]] == ["scope", "previous_a"]
    assert result["items"][0]["prompt"] == value["candidates"][1]["prompt"]
    assert result["items"][1]["requires_new_data"] is True
    assert result["usage"] == {"prompt_tokens": 40, "completion_tokens": 10}
    assert "private" not in json.dumps(result)
    assert value == before
    sent, options = client.calls[0]
    assert json.loads(sent[-1]["content"]) == value
    assert options["enable_thinking"] is False
    assert options["max_tokens"] == 700
    assert options["response_format"] == {"type": "json_object"}
    assert "tools" not in options


@pytest.mark.parametrize("selection", [
    {"candidate_ids": ["invented_metric"]},
    {"candidate_ids": ["scope", "scope"]},
    {"candidate_ids": ["scope"], "prompt": "Invented investment recommendation"},
    {"candidate_ids": ["scope", "previous_a", "previous_b", "scope"]},
    {"message": "Some unsupported conclusion"},
])
def test_model_cannot_invent_questions_or_run_actions(selection):
    with pytest.raises(FollowupError):
        generate_followups(ChoiceClient(selection), context())


def test_already_asked_questions_are_removed_before_any_model_call():
    value = context()
    value["candidates"] = [value["candidates"][0]]
    value["history"].append({"question": value["candidates"][0]["prompt"], "answer": "Kontrol edildi."})
    client = ChoiceClient({"candidate_ids": ["previous_a"]})
    result = generate_followups(client, value)
    assert result["items"] == []
    assert not client.calls


def test_oversized_optional_context_never_reaches_provider():
    value, client = context(), ChoiceClient({"candidate_ids": ["scope"]})
    value["answer"] = "x" * 25000
    with pytest.raises(FollowupError):
        generate_followups(client, value)
    assert not client.calls


def test_answered_topics_cannot_reappear_even_when_model_also_selects_them():
    result = generate_followups(ChoiceClient({"answered_ids": ["scope"], "candidate_ids": ["scope", "previous_a"]}), context())
    assert [item["id"] for item in result["items"]] == ["previous_a"]


def test_optional_client_budget_is_independent_of_main_analysis():
    sent = []
    def transport(endpoint, payload):
        sent.append((endpoint, payload))
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]}
    main = MiaClient("dummy-test-secret", timeout=90, max_retries=2,
                     chat_model="open-model", transport=transport)
    optional = main.with_limits(timeout=20, max_retries=0)
    assert (main.timeout, main.max_retries) == (90, 2)
    assert (optional.timeout, optional.max_retries) == (20, 0)
    assert optional.chat_model == main.chat_model
    optional.chat([{"role": "user", "content": "Choose next question"}])
    assert sent[0][1]["model"] == "open-model"
    assert "dummy-test-secret" not in repr(optional)
