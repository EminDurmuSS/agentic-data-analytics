"""Build a complete, user-downloadable audit record for one completed run."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import unicodedata
from urllib.parse import quote

from app.activity import activity_feed, activity_journey
from app.serialization import browser_json
from agentic_analytics.lakehouse.presentation import analysis_presentation


_SOURCE_ID = re.compile(r"^source_[a-f0-9]{64}$")
_TERMINAL = {"completed", "partial", "blocked", "failed", "needs_input", "interrupted"}


def _source_ids(run: dict) -> list[str]:
    state, found = run.get("state") or {}, []

    def add(value):
        if isinstance(value, str) and _SOURCE_ID.fullmatch(value) and value not in found:
            found.append(value)

    for value in state.get("selected_source_ids") or []:
        add(value)
    for message in state.get("messages") or []:
        for value in message.get("source_ids") or []:
            add(value)
    for record in state.get("tool_results") or []:
        result = record.get("result") or {}
        add(result.get("source_id"))
        if isinstance(result.get("source"), dict):
            add(result["source"].get("source_id"))
    return found


def _filename_part(value, limit):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    value = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .,_-")
    return (value or "bilgi-yok")[:limit].rstrip(" .,_-")


def _utf8_limit(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    return value if len(encoded) <= limit else encoded[:limit].decode("utf-8", errors="ignore").rstrip(" .,_-")


def export_filename(workspace: dict, run: dict, imported_files: list[dict], prompt_count: int = 1) -> str:
    profile = "kendi-veriniz" if workspace.get("profile") == "generic" else "kkb-finans-verileri"
    prompt_label = ((f"{prompt_count}-prompt " if prompt_count > 1 else "")
                    + str(run.get("message") or ""))
    parts = [_filename_part(workspace.get("name"), 36),
             _filename_part(run.get("conversation_id"), 45),
             _filename_part(prompt_label, 64), profile]
    if imported_files:
        parts.append(_filename_part("-".join(item["filename"] for item in imported_files), 48))
    return _utf8_limit("__".join(parts), 235) + ".json"


def content_disposition(filename: str) -> str:
    return "attachment; filename=technical-session.json; filename*=UTF-8''" + quote(filename)


def _analysis_table(context, workspace_id: str, run: dict, result: dict) -> dict:
    analysis_id = result.get("analysis_id") or (run.get("state") or {}).get("analysis_id")
    if not analysis_id:
        return {"available": False, "analysis_id": None, "reason": "not_created_for_run"}
    try:
        frame, manifest = context.store.load_analysis(analysis_id)
    except (OSError, ValueError):
        return {"available": False, "analysis_id": analysis_id,
                "reason": "unavailable_or_integrity_check_failed"}
    if manifest.get("workspace_id") != workspace_id:
        return {"available": False, "analysis_id": analysis_id, "reason": "workspace_mismatch"}
    public_manifest = {key: value for key, value in manifest.items() if key != "result_path"}
    return browser_json({
        "available": True, "analysis_id": analysis_id, "row_count": len(frame),
        "columns": list(frame), "rows": frame.to_dict("records"),
        "presentation": analysis_presentation(frame, manifest),
        "schema": manifest.get("schema", {}), "plan": manifest.get("plan", {}),
        "lineage": manifest.get("lineage", {}), "manifest": public_manifest,
        "csv_url": f"/api/workspaces/{workspace_id}/analyses/{analysis_id}/csv",
    })


def _voice_summary(context, workspace_id: str, run_id: str) -> dict:
    from app.context import voice_job_id
    from fastapi import HTTPException

    job_id = voice_job_id(workspace_id, run_id)
    try:
        job = context.voice_job(job_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        return {"available": False, "status": "not_created", "voice_job_id": job_id}
    status = job.get("status", "unknown")
    saved = job.get("result") if isinstance(job.get("result"), dict) else None
    voice_id = saved.get("voice_id") if saved else None
    if status != "completed" or not voice_id:
        payload = {"available": False, "status": status, "voice_job_id": job_id}
        if status == "failed" and job.get("detail"):
            payload["detail"] = job["detail"]
        return payload
    try:
        record = context.voice_summaries().load(workspace_id, voice_id)
    except (OSError, ValueError):
        return {"available": False, "status": "integrity_check_failed",
                "voice_job_id": job_id, "voice_id": voice_id}
    if record.get("run_id") != run_id:
        return {"available": False, "status": "run_mismatch",
                "voice_job_id": job_id, "voice_id": voice_id}
    return {"available": True, "status": "completed", "voice_job_id": job_id,
            **record, "audio_url": f"/api/workspaces/{workspace_id}/voice/{voice_id}/audio"}


def _run_record(context, workspace_id: str, run: dict) -> dict:
    result = run.get("result") or {}
    status = result.get("status") or run.get("status")
    records = result.get("tool_results") or (run.get("state") or {}).get("tool_results") or []
    events = context.run_store.events(run["run_id"])
    analysis_table = _analysis_table(context, workspace_id, run, result)
    voice_summary = _voice_summary(context, workspace_id, run["run_id"])
    return {
        "run_id": run["run_id"], "request_id": run.get("request_id"),
        "prompt": run.get("message"), "status": status,
        "created_at": run.get("created_at"), "updated_at": run.get("updated_at"),
        "result": result,
        "technical_records": records,
        "event_log": events,
        "activity_log": activity_feed(events),
        "activity_journey": activity_journey(events, status),
        "deliverables": {"analysis_table_available": analysis_table["available"],
                         "voice_summary_available": voice_summary["available"]},
        "analysis_table": analysis_table,
        "voice_summary": voice_summary,
    }


def build_run_export(context, workspace_id: str, run_id: str) -> tuple[dict, str]:
    workspace, run = context.workspace(workspace_id), context.run_store.get(run_id)
    if run.get("workspace_id") != workspace_id:
        raise ValueError("Run does not belong to this workspace")
    result = run.get("result") or {}
    status = result.get("status") or run.get("status")
    if status not in _TERMINAL or not run.get("result"):
        raise RuntimeError("Technical records can be downloaded after the agent run finishes")

    conversation_runs = list(reversed(context.run_store.list(
        workspace_id, conversation_id=run["conversation_id"], limit=100)))
    conversation_runs = [item for item in conversation_runs if item.get("result") is not None]
    imported_files, source_records, unavailable_source_ids, source_ids = [], [], [], []
    documents = context.documents(workspace_id)
    for conversation_run in conversation_runs:
        for source_id in _source_ids(conversation_run):
            if source_id not in source_ids:
                source_ids.append(source_id)
    for source_id in source_ids:
        try:
            source = documents.source(source_id)
        except (OSError, ValueError):
            unavailable_source_ids.append(source_id)
            continue
        source_records.append(source)
        if not source.get("source_url"):
            imported_files.append({key: source[key] for key in (
                "source_id", "filename", "mime_type", "raw_sha256", "size_bytes")
                if source.get(key) is not None})

    technical_records = result.get("tool_results") or (run.get("state") or {}).get("tool_results") or []
    events = context.run_store.events(run_id)
    conversation = context.run_store.conversation(run["conversation_id"], workspace_id)
    public_messages = [message for message in conversation.get("messages", [])
                       if message.get("role") in {"user", "assistant"}
                       and isinstance(message.get("content"), str) and not message.get("tool_calls")]
    analysis_table = _analysis_table(context, workspace_id, run, result)
    voice_summary = _voice_summary(context, workspace_id, run_id)
    exported_runs = [_run_record(context, workspace_id, item) for item in conversation_runs]
    payload = {
        "export_version": 2, "exported_at": datetime.now(timezone.utc).isoformat(),
        "included_information": ["session_and_workspace_metadata", "prompt_and_public_conversation",
            "original_result_status_and_errors", "all_technical_tool_records",
            "raw_durable_event_log", "user_facing_activity_log_and_journey",
            "associated_source_and_imported_file_metadata",
            "analysis_table_or_explicit_unavailable_flag",
            "voice_summary_or_explicit_unavailable_flag"],
        "session_name": workspace.get("name", workspace_id), "session_id": run["conversation_id"],
        "prompt_count": len(exported_runs),
        "prompts": [item["prompt"] for item in exported_runs],
        "run_id": run["run_id"], "workspace_id": workspace_id, "prompt": run["message"],
        "workspace_type": {"code": workspace.get("profile", "finance"),
            "label": "Kendi veriniz" if workspace.get("profile") == "generic" else "KKB finans verileri"},
        "imported_files": imported_files, "workspace": workspace,
        "conversation": {"conversation_id": run["conversation_id"], "messages": public_messages},
        "run": {key: run.get(key) for key in ("run_id", "workspace_id", "conversation_id",
            "request_id", "message", "status", "created_at", "updated_at")},
        "result": result,
        "technical_ledger": {"record_count": len(technical_records),
            "original_response": {"message": result.get("message"),
                "display_message": result.get("display_message"), "status": status,
                "errors": result.get("errors") or []}, "records": technical_records},
        "technical_records": technical_records, "event_log": events,
        "activity_log": activity_feed(events), "activity_journey": activity_journey(events, status),
        "source_records": source_records,
        "deliverables": {"analysis_table_available": analysis_table["available"],
                         "voice_summary_available": voice_summary["available"]},
        "analysis_table": analysis_table, "voice_summary": voice_summary,
        "conversation_runs": exported_runs,
    }
    if unavailable_source_ids:
        payload["unavailable_source_ids"] = unavailable_source_ids
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload, export_filename(workspace, run, imported_files, len(exported_runs))
