"""Build bounded model context while retaining complete durable tool records."""

import copy
import json

from agentic_analytics.agent.prompts import CHART_PROMPT, SYSTEM_PROMPT
from agentic_analytics.agent.run_store import canonical
from agentic_analytics.lakehouse.service import PlanError


def _compact(value, *, depth=0):
    """Bound model-facing results; complete tool outputs remain in the ledger."""
    if depth > 9:
        return "[nested detail retained in tool ledger]"
    if isinstance(value, dict):
        hidden = {"table", "source_base", "result_path", "raw_path", "local_path", "reasoning", "reasoning_content"}
        return {k: _compact(v, depth=depth + 1) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        items = [_compact(v, depth=depth + 1) for v in value[:30]]
        return items + ([{"remaining_items": len(value) - 30}] if len(value) > 30 else [])
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [truncated; full text retained in tool ledger]"
    return value


def _model_tool_result(name, result, *, terse=False):
    """Retrieval cards are navigation hints; describe carries full semantics.

    This view is intentionally separate from the full durable tool-result
    ledger. A request for 25 search matches never sends 25 verbose bindings to
    the model, and old search results can shed metadata without losing IDs.
    """
    if name != "discover" or not isinstance(result, dict) or not isinstance(result.get("metrics"), list):
        return _compact(result)
    fields = ("metric_id", "title", "status", "value_dimension") if terse else (
        "metric_id", "title", "source_system", "group_name", "value_dimension", "is_archive",
        "temporal_semantics", "unit", "scale", "currency", "kind",
        "native_frequency", "status", "dimensions", "matched_dimensions", "missing_terms")

    def project(cards):
        out = [{key: copy.deepcopy(card[key]) for key in fields if key in card}
               for card in cards[:10] if isinstance(card, dict)]
        for card in out:
            if isinstance(card.get("title"), str):
                card["title"] = card["title"][:240]
        return out
    cards = project(result["metrics"])
    view = {key: copy.deepcopy(result[key]) for key in
            ("status", "snapshot_id", "query", "total", "errors", "no_confident_match", "uncovered_terms") if key in result}
    view.update(metrics=cards, model_card_count=len(cards),
                model_cards_truncated=len(result["metrics"]) > len(cards) or result.get("total", len(cards)) > len(cards))
    if isinstance(result.get("near_matches"), list):
        view["near_matches"] = project(result["near_matches"])
    if terse:
        view["historical_search_summary"] = True
    return _compact(view)


def workspace_context(store, workspace_id, state, *, max_decisions, charts_enabled):
    workspace = store.workspace(workspace_id)
    context = {"workspace_id": workspace_id, "snapshot_id": workspace["snapshot_id"],
               "workspace_version": workspace["version"], "active_analysis_id": workspace.get("analysis_head"),
               "datasets": workspace.get("datasets", []), "remaining_decisions": max_decisions - state["decisions"]}
    if workspace.get("analysis_head"):
        _, manifest = store.load_analysis(workspace["analysis_head"])
        context["active_plan"] = manifest["plan"]
        context["active_schema"] = manifest.get("schema")
        if charts_enabled:
            from agentic_analytics.agent.tools.charts import ChartTools, ChartError
            try:
                chart = ChartTools(store, workspace_id).get_chart(workspace["analysis_head"])
                context["active_chart"] = {key: chart[key] for key in
                    ("chart_id", "analysis_id", "spec", "title", "recommendations") if key in chart}
            except (ChartError, OSError):
                context["active_chart"] = {"status": "unavailable", "analysis_id": workspace["analysis_head"]}
    context["artifacts"] = state.get("artifacts", [])[-10:]
    context["initial_metric_candidates"] = _model_tool_result("discover", state.get("initial_candidates"))
    return context


def model_messages(state, *, context_factory, charts_enabled, max_context_chars):
    messages = copy.deepcopy(state["messages"])
    call_names, discovery_messages = {}, []
    # Reapply the compact view when resuming old journals created before
    # small cards existed. Calls and result messages keep their identities.
    for index, message in enumerate(messages):
        for call in message.get("tool_calls", []):
            call_names[call["id"]] = call["function"]["name"]
        if message.get("role") == "tool" and call_names.get(message.get("tool_call_id")) == "discover":
            try:
                result = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            message["content"] = canonical(_model_tool_result("discover", result))
            discovery_messages.append((index, result))
    prompt = SYSTEM_PROMPT + (CHART_PROMPT if charts_enabled else "")
    system = prompt + "\nGüncel güvenilir çalışma alanı bağlamı:\n" + canonical(_compact(context_factory(state)))
    # Keep the newest discovery cards detailed; older successful searches
    # need only their metric identity/title/readiness once context is tight.
    if len(system) + len(canonical(messages)) > max_context_chars * .75:
        for index, result in discovery_messages[:-2]:
            messages[index]["content"] = canonical(_model_tool_result("discover", result, terse=True))
    # Keep complete user turns, never orphan a tool result from its call.
    while (len(system) + len(canonical(messages)) > max_context_chars or len(messages) > 180) and sum(m["role"] == "user" for m in messages) > 1:
        next_user = next(i for i, m in enumerate(messages[1:], 1) if m["role"] == "user")
        messages = messages[next_user:]
    if len(system) + len(canonical(messages)) > max_context_chars:
        raise PlanError("Current task exceeds the configured context budget; use a smaller scope.", code="CONTEXT_BUDGET_EXCEEDED")
    return [{"role": "system", "content": system}] + messages
