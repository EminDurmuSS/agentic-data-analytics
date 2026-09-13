"""Read-only display projections for historical, completed analysis runs."""
from __future__ import annotations

import copy
import json
import re

import duckdb

from app.activity import public_run
from agentic_analytics.agent.delivery import (
    _analysis_confirmation, _cell_confirmation, _scope_confirmation,
    _statistics_confirmation, _display_label, _display_period,
)


def _compact_summary_periods(store, run, message):
    """Shorten only repeated native periods proved by saved summary facts."""
    state = run.get("state") or {}
    if (run.get("status") != "completed" or (run.get("result") or {}).get("status") != "completed"
            or (run.get("result") or {}).get("errors")
            or "Devam için soru:" in message
            or not state.get("analysis_updated") or not state.get("analysis_id")):
        return message
    from agentic_analytics.agent.tools.summary import SummaryTools
    summaries = SummaryTools(store, run["workspace_id"])
    seen = set()
    for item in state.get("tool_results", []):
        result = item.get("result") or {}
        artifact = result.get("artifact_id") or result.get("artifact_ref")
        if (item.get("tool") != "summarize_analysis" or result.get("status") != "ok"
                or result.get("analysis_id") != state["analysis_id"] or not artifact or artifact in seen):
            continue
        seen.add(artifact)
        payload = summaries.load_artifact(artifact)
        if payload.get("analysis_id") != state["analysis_id"]:
            continue
        for fact in payload.get("facts", []):
            first, last = fact.get("period_start"), fact.get("period_end")
            if first is None or first != last or fact.get("window") != f"{first} - {last}":
                continue
            original = f", {_display_label(fact['window'])} ({_display_period(first)}):"
            message = message.replace(original, f", {_display_period(first)}:")
    return message


def present_chart(store, payload):
    """Replace opaque file identifiers in display labels, retaining saved proof."""
    public = copy.deepcopy(payload)
    sources = public.get("presentation", {}).get("sources", [])
    opaque = [source for source in sources if re.fullmatch(
        r"(?:source[_ ]|dataset[_ ])?[a-fA-F0-9]{24,}(?:\.pdf)?", str(source.get("label", "")))]
    if not opaque:
        return public
    from agentic_analytics.agent.tools.pdf_research import pdf_title

    _, manifest = store.load_analysis(public["analysis_id"])
    if manifest.get("workspace_id") != public.get("workspace_id"):
        raise ValueError("Chart source labels belong to another workspace")
    documents = {}

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            document = node.get("document_provenance")
            if isinstance(document, dict):
                documents[(document.get("source_url"), document.get("page"))] = document
            for key, child in node.items():
                if key != "document_provenance" and isinstance(child, (dict, list)):
                    visit(child)

    visit(manifest.get("lineage", {}))
    titles = {}
    for source in opaque:
        source["label"] = "Kaynak rapor"
        document = documents.get((source.get("url"), source.get("page")), {})
        source_id = document.get("source_id")
        if not isinstance(source_id, str) or not re.fullmatch(r"source_[a-f0-9]{64}", source_id):
            continue
        identity = (source_id, document.get("raw_sha256"))
        if identity not in titles:
            titles[identity] = None
            try:
                directory = store._path("document_sources", public["workspace_id"], source_id)
                registered = json.loads((directory / "manifest.json").read_text())
                if registered.get("raw_sha256") != identity[1] or registered.get("source_id") != source_id:
                    continue
                cache = directory / "inspection.json"
                if cache.stat().st_size > 8 * 1024**2:
                    continue
                inspection = json.loads(cache.read_text())
                title = pdf_title(inspection.get("document_metadata", {}), inspection.get("pages", []), "")
                if title.get("title_basis") in {"pdf_metadata", "pdf_cover"} and title.get("title"):
                    label = " ".join(title["title"].split())
                    titles[identity] = label if len(label) <= 180 else label[:177].rsplit(" ", 1)[0] + "..."
            except (ValueError, OSError, EOFError):
                pass
        if titles[identity]:
            source["label"] = titles[identity]
    return public


def present_run(store, run):
    """Refresh saved numerical prose without altering the journal or its result.

    Refusals, unfinished work, web research, and clarification questions retain
    their original messages. A display projection never upgrades run status.
    """
    public = public_run(run)
    result, state = run.get("result") or {}, run.get("state") or {}
    try:
        compact = _compact_summary_periods(store, run, result.get("message", ""))
        if compact != result.get("message", ""):
            public["result"] = {**result, "display_message": compact}
    except (ValueError, OSError, duckdb.Error):
        pass
    if (run.get("status") != "completed" or result.get("status") != "completed"
            or not (state.get("analysis_updated") or state.get("analysis_observed"))
            or not state.get("analysis_id") or result.get("errors")
            or (state.get("chart_updated") and not state.get("analysis_updated"))
            or "Devam için soru:" in result.get("message", "")
            or any(item.get("tool") == "research_web" for item in state.get("tool_results", []))):
        return public
    try:
        parts = [render(store, run["workspace_id"], state) for render in (
            _analysis_confirmation, _statistics_confirmation,
            _cell_confirmation, _scope_confirmation,
        )]
        message = _compact_summary_periods(store, run, "\n\n".join(part for part in parts if part))
    except (ValueError, OSError, duckdb.Error):
        # Missing or changed historical artifacts cannot support fresh prose.
        return public
    if message:
        public["result"] = {**result, "display_message": message}
    return public
