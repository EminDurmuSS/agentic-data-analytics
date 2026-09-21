"""Build a bounded, integrity-checked evidence capsule for a voice script."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any

from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.statistics import StatisticsTools
from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.presentation import analysis_presentation


MAX_MESSAGE_CHARS = 2_400
MAX_FACTS = 18
MAX_WARNINGS = 12
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


class VoiceContextError(ValueError):
    """The selected run does not supply a safe, persisted voice brief."""


@dataclass(frozen=True)
class VoiceBriefInput:
    """Only source-bound facts that Qwen may use in a spoken summary."""

    workspace_id: str
    run_id: str
    analysis_id: str | None
    data_sha256: str
    snapshot_id: str | None
    question: str
    answer: str
    analysis: dict[str, Any]
    facts: list[dict[str, Any]]
    statistics: list[dict[str, Any]]
    chart: dict[str, Any] | None
    sources: list[dict[str, Any]]
    warnings: list[str]

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _short_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = _URL.sub("", value)
    return " ".join(value.replace("\x00", " ").split())[:limit]


def _run_result(run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result, state = run.get("result"), run.get("state")
    if not isinstance(state, dict):
        raise VoiceContextError("Çalışma sonucu sesli özet için hazır değil.")
    # A provider outage can happen after execute/create_chart persisted valid
    # artifacts. Those bytes are still safe to summarize; the eventual run
    # status and its raw error text must not discard a usable result.
    return result if isinstance(result, dict) else {}, state


def _run_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Project only persisted source identity metadata for a source-only run."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in state.get("tool_results", []):
        if not isinstance(item, dict) or item.get("tool") not in {
                "research_web", "inspect_source", "read_source_table", "find_source_table_rows"}:
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("status") != "ok":
            continue
        for source in result.get("sources", []) if item.get("tool") == "research_web" else [result]:
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id") or source.get("raw_sha256")
            if not isinstance(source_id, str) or source_id in seen:
                continue
            seen.add(source_id)
            found.append({
                "title": _short_text(source.get("title") or (source.get("article") or {}).get("title")
                                     or source.get("filename"), 180),
                "publisher": _short_text(source.get("publisher"), 100),
                "document": _short_text(source.get("filename"), 180),
                "page": source.get("page") if isinstance(source.get("page"), int) else None,
            })
            if len(found) == 8:
                return found
    return found


def _artifact_ids(state: dict[str, Any], tool: str, key: str) -> list[str]:
    found: list[str] = []
    for item in state.get("tool_results", []):
        result = item.get("result") if isinstance(item, dict) else None
        value = result.get(key) if isinstance(result, dict) else None
        if item.get("tool") == tool and isinstance(value, str) and _ID.fullmatch(value) and value not in found:
            found.append(value)
    return found


def _warnings(manifest: dict[str, Any], chart: dict[str, Any] | None) -> list[str]:
    values: list[str] = []
    for value in manifest.get("lineage", {}).get("warnings", []):
        text = _short_text(value.get("message") if isinstance(value, dict) else value, 260)
        if text and text not in values:
            values.append(text)
    if chart:
        for value in chart.get("warnings", chart.get("presentation", {}).get("warnings", [])):
            text = _short_text(value.get("message") if isinstance(value, dict) else value, 260)
            if text and text not in values:
                values.append(text)
    return values[:MAX_WARNINGS]


def _sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for column, proof in (manifest.get("lineage", {}).get("sources") or {}).items():
        binding = proof.get("binding") if isinstance(proof, dict) else {}
        binding = binding if isinstance(binding, dict) else {}
        document = proof.get("document_provenance") or binding.get("document_provenance") or {}
        item = {"column": _short_text(column, 120), "title": _short_text(binding.get("title"), 180),
                "publisher": _short_text(binding.get("source_system"), 100),
                "unit": _short_text(binding.get("unit"), 48), "scale": binding.get("scale")}
        if isinstance(document, dict):
            item.update({"document": _short_text(document.get("filename"), 180),
                         "page": document.get("page") if isinstance(document.get("page"), int) else None})
        if item not in result:
            result.append(item)
    return result[:8]


def _small_mapping(value: object, *, limit: int = 12) -> dict[str, object]:
    """Keep scalar statistical evidence; rows remain in their immutable artifact."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for key, item in value.items():
        if len(result) >= limit or not isinstance(key, str):
            break
        if item is None or isinstance(item, (str, int, float, bool)):
            result[key] = _short_text(item, 180) if isinstance(item, str) else item
        elif isinstance(item, list):
            result[key + "_count"] = len(item)
        elif isinstance(item, dict):
            nested = {name: child for name, child in item.items()
                      if isinstance(name, str) and (child is None or isinstance(child, (str, int, float, bool)))}
            if nested:
                result[key] = {name: _short_text(child, 180) if isinstance(child, str) else child
                               for name, child in list(nested.items())[:8]}
    return result


def _statistic_capsule(payload: dict[str, Any]) -> dict[str, Any]:
    return {"method": _short_text(payload.get("method"), 80),
            "parameters": _small_mapping(payload.get("parameters"), limit=8),
            "results": _small_mapping(payload.get("results")),
            "warnings": [_short_text(value, 180) for value in payload.get("warnings", []) if isinstance(value, str)][:4]}


def build_voice_brief(store, run: dict[str, Any], *, answer: str | None = None) -> VoiceBriefInput:
    """Return bounded evidence only when every included artifact matches one analysis."""
    result, state = _run_result(run)
    workspace_id, run_id, analysis_id = run.get("workspace_id"), run.get("run_id"), state.get("analysis_id")
    if not all(isinstance(value, str) and _ID.fullmatch(value) for value in (workspace_id, run_id)):
        raise VoiceContextError("Sesli özet kimlikleri geçersiz.")
    if not isinstance(analysis_id, str):
        candidate = answer if answer is not None else result.get("display_message") or result.get("message")
        message = _short_text(candidate, MAX_MESSAGE_CHARS)
        if not message:
            raise VoiceContextError("Çalışma sonucu sesli özet için hazır değil.")
        sources = _run_sources(state)
        warning_values = [
            _short_text(value.get("message") if isinstance(value, dict) else value, 260)
            for value in [*(result.get("warnings") or []), *(result.get("errors") or [])]
        ]
        warnings = list(dict.fromkeys(value for value in warning_values if value))[:MAX_WARNINGS]
        evidence = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "question": _short_text(run.get("message"), 800),
            "answer": message,
            "sources": sources,
            "warnings": warnings,
        }
        digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
        workspace = store.workspace(workspace_id)
        return VoiceBriefInput(
            workspace_id=workspace_id,
            run_id=run_id,
            analysis_id=None,
            data_sha256=digest,
            snapshot_id=workspace.get("snapshot_id"),
            question=evidence["question"],
            answer=message,
            analysis={"row_count": 0, "period": None, "columns": [], "plan": {"mode": "source_only"}},
            facts=[],
            statistics=[],
            chart=None,
            sources=sources,
            warnings=warnings,
        )
    if not _ID.fullmatch(analysis_id):
        raise VoiceContextError("Sesli özet kimlikleri geçersiz.")
    frame, manifest = store.load_analysis(analysis_id)
    if manifest.get("workspace_id") != workspace_id or not isinstance(manifest.get("data_sha256"), str):
        raise VoiceContextError("Analiz başka çalışma alanına ait veya bütünlüğü eksik.")

    summaries = SummaryTools(store, workspace_id)
    facts: list[dict[str, Any]] = []
    for artifact_id in _artifact_ids(state, "summarize_analysis", "artifact_id"):
        try:
            payload = summaries.load_artifact(artifact_id)
        except (OSError, ValueError):
            continue
        if payload.get("analysis_id") != analysis_id or payload.get("provenance", {}).get("data_sha256") != manifest["data_sha256"]:
            continue
        facts.extend(payload.get("facts", []))
    facts = [fact for fact in facts if isinstance(fact, dict)][:MAX_FACTS]

    statistics: list[dict[str, Any]] = []
    statistics_tools = StatisticsTools(store, workspace_id)
    for artifact_id in _artifact_ids(state, "rolling_anomalies", "artifact_id") + _artifact_ids(state, "detect_changes", "artifact_id") + _artifact_ids(state, "analyze_relationship", "artifact_id"):
        try:
            payload = statistics_tools.load_artifact(artifact_id)
        except (OSError, ValueError):
            continue
        if payload.get("analysis_id") == analysis_id and payload.get("provenance", {}).get("data_sha256") == manifest["data_sha256"]:
            statistics.append(_statistic_capsule(payload))

    chart = None
    chart_id = state.get("chart_id")
    if isinstance(chart_id, str) and _ID.fullmatch(chart_id) and state.get("chart_analysis_id") == analysis_id:
        try:
            candidate = ChartTools(store, workspace_id).load_artifact(chart_id)
            if candidate.get("analysis_id") == analysis_id and candidate.get("provenance", {}).get("data_sha256") == manifest["data_sha256"]:
                presentation = candidate.get("presentation", {})
                chart = {"title": _short_text(candidate.get("title"), 180), "kind": _short_text(candidate.get("spec", {}).get("kind"), 32),
                         "columns": candidate.get("spec", {}).get("columns", [])[:6],
                         "sources": [_short_text(item.get("label") if isinstance(item, dict) else item, 180)
                                     for item in presentation.get("sources", [])][:6],
                         "warnings": [_short_text(item.get("message") if isinstance(item, dict) else item, 240)
                                      for item in presentation.get("warnings", [])][:6]}
        except (OSError, ValueError):
            pass

    presentation = analysis_presentation(frame, manifest)
    labels, schema = presentation.get("labels", {}), manifest.get("schema", {})
    analysis = {"row_count": len(frame), "period": {"start": str(frame["period"].min()), "end": str(frame["period"].max())} if "period" in frame else None,
                "columns": [{"name": name, "label": _short_text(labels.get(name, name), 120), "unit": schema.get(name, {}).get("unit"), "scale": schema.get(name, {}).get("scale")}
                            for name in presentation.get("columns", [])][:12],
                "plan": _small_mapping(manifest.get("plan"), limit=10)}
    # A failed final provider call commonly leaves only its transport error in
    # `message`. Do not feed that error to the spoken-summary model. The
    # persisted analysis/chart below is enough for the local fallback script.
    terminal_failed = result.get("status") in {"failed", "blocked"} or bool(result.get("errors"))
    candidate = answer if answer is not None else ("" if terminal_failed else result.get("display_message") or result.get("message"))
    message = _short_text(candidate, MAX_MESSAGE_CHARS)
    return VoiceBriefInput(workspace_id=workspace_id, run_id=run_id, analysis_id=analysis_id,
                           data_sha256=manifest["data_sha256"], snapshot_id=manifest.get("snapshot_id"),
                           question=_short_text(run.get("message"), 800), answer=message, analysis=analysis,
                           facts=facts, statistics=statistics, chart=chart, sources=_sources(manifest),
                           warnings=_warnings(manifest, chart))
