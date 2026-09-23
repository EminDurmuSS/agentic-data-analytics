"""Budget pruning must keep message positions aligned with the pruned view."""
import copy
import json

from agentic_analytics.agent.context import _resolved_discovery_receipt, canonical, model_messages


def tool_call(call_id, name):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}]}


def tool_result(call_id, payload):
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)}


def discovery(call_id):
    return {"status": "ok", "total": 20, "metrics": [
        {"metric_id": f"{call_id}_metric_{index}", "title": "Vadeli Mevduat seri başlığı " * 12, "status": "ready"}
        for index in range(20)]}


def view(messages, budget):
    return model_messages({"messages": messages}, context_factory=lambda _: {}, charts_enabled=False,
                          max_context_chars=budget)


def test_pruned_earlier_turn_does_not_misalign_discovery_compaction():
    # 200 short messages exceed the 180-message window, so the earlier turn is pruned.
    earlier = [{"role": "user", "content": "Önceki soru"}] + [
        {"role": "assistant", "content": f"not {index}"} for index in range(200)]
    current = [{"role": "user", "content": "Yeni soru"}]
    for call_id in ("d0", "d1", "d2"):
        current += [tool_call(call_id, "discover"), tool_result(call_id, discovery(call_id))]
    current += [tool_call("e", "execute"), tool_result("e", {
        "status": "ok", "analysis_id": "analysis_1", "schema": {"deposit": {"metric_id": "d1_metric_1"}}})]
    # Fits only once this turn's discoveries are retired to receipts of the saved analysis.
    retired = copy.deepcopy(current)
    for message in retired:
        if message.get("tool_call_id") in {"d0", "d1", "d2"}:
            message["content"] = canonical(_resolved_discovery_receipt(json.loads(message["content"]), {"d1_metric_1"}))
    system = view(current, 10 ** 6)[0]["content"]
    messages = view(earlier + current, len(system) + len(canonical(retired)) + 200)[1:]
    assert messages[0] == {"role": "user", "content": "Yeni soru"}
    assert json.loads(next(m for m in messages if m.get("tool_call_id") == "d0")["content"]).get(
        "resolved_metric_ids") == ["d1_metric_1"]
    assert json.loads(messages[-1]["content"])["analysis_id"] == "analysis_1"
