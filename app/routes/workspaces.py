"""Workspace discovery, conversation submission, and job recovery routes."""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from app.activity import activity_feed, activity_journey
from app.presentation import present_run
from app.context import AppContext
from app.models import RunBody, WorkspaceBody
from app.serialization import browser_json
from app.run_export import build_run_export, content_disposition
from agentic_analytics.providers.mia import (
    DEFAULT_MIA_CHAT_MODEL, DEFAULT_MIA_EMBEDDING_MODEL, DEFAULT_MIA_OCR_MODEL,
)


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/status")
    def status():
        client = context.client
        return {"status": "ok", "provider_ready": client is not None,
                "finance_available": bool(context.source_db and context.source_db.is_file()),
                "models": {"chat": getattr(client, "chat_model", DEFAULT_MIA_CHAT_MODEL),
                           "embedding": getattr(client, "embedding_model", DEFAULT_MIA_EMBEDDING_MODEL),
                           "ocr": getattr(client, "ocr_model", DEFAULT_MIA_OCR_MODEL)}}

    @router.get("/api/workspaces")
    def workspaces():
        return {"workspaces": context.workspaces()}

    @router.post("/api/workspaces")
    def new_workspace(body: WorkspaceBody):
        return context.create_workspace(body.name, body.profile)

    @router.delete("/api/workspaces/{workspace_id}")
    def delete_workspace(workspace_id: str):
        return context.delete_workspace(workspace_id)

    @router.delete("/api/workspaces/{workspace_id}/conversations/{conversation_id}")
    def delete_conversation(workspace_id: str, conversation_id: str):
        return context.delete_conversation(workspace_id, conversation_id)

    @router.get("/api/workspaces/{workspace_id}")
    def workspace(workspace_id: str):
        value = context.workspace(workspace_id)
        runs = context.run_store.list(workspace_id, limit=30)
        value["runs"] = [present_run(context.store, run) for run in runs]
        if runs:
            latest = runs[0]
            events = context.run_store.events(latest["run_id"])
            value["latest_activity"] = activity_feed(events)
            value["latest_journey"] = activity_journey(events, latest["status"])
            value["activity_count"] = len(value["latest_activity"])
            if not latest["result"]:
                value["pending_job_id"] = "job_" + hashlib.sha256((workspace_id + ":" + latest["request_id"]).encode()).hexdigest()[:32]
                try:
                    value["latest_journey"] = context.job(value["pending_job_id"])["journey"]
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
            elif context.run_store.retryable_provider_result(latest["result"]):
                value["retryable_job_id"] = "job_" + hashlib.sha256((workspace_id + ":" + latest["request_id"]).encode()).hexdigest()[:32]
        return browser_json(value)

    @router.post("/api/workspaces/{workspace_id}/runs")
    def submit(workspace_id: str, body: RunBody):
        return context.submit(workspace_id, body)

    @router.get("/api/workspaces/{workspace_id}/runs/{run_id}/followups")
    def followups(workspace_id: str, run_id: str):
        return browser_json(context.followups.get(workspace_id, run_id))

    @router.get("/api/workspaces/{workspace_id}/runs/{run_id}/technical-records")
    def technical_records(workspace_id: str, run_id: str):
        try:
            payload, filename = build_run_export(context, workspace_id, run_id)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        body = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        return Response(body, media_type="application/json; charset=utf-8", headers={
            "Content-Disposition": content_disposition(filename), "Cache-Control": "no-store"})

    @router.post("/api/workspaces/{workspace_id}/runs/{run_id}/followups")
    def start_followups(workspace_id: str, run_id: str):
        return browser_json(context.followups.start(workspace_id, run_id))

    @router.post("/api/workspaces/{workspace_id}/runs/{run_id}/voice")
    def create_voice_summary(workspace_id: str, run_id: str):
        return context.submit_voice(workspace_id, run_id)

    @router.get("/api/voice-jobs/{job_id}")
    def voice_job(job_id: str):
        return context.voice_job(job_id)

    @router.get("/api/workspaces/{workspace_id}/voice/{voice_id}")
    def voice_summary(workspace_id: str, voice_id: str):
        try:
            return browser_json(context.voice_summaries().load(workspace_id, voice_id))
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/api/workspaces/{workspace_id}/voice/{voice_id}/audio")
    def voice_audio(workspace_id: str, voice_id: str):
        try:
            audio = context.voice_summaries().audio_path(workspace_id, voice_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return FileResponse(audio, media_type="audio/wav", filename="sesli-ozet.wav")

    @router.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return context.job(job_id)

    @router.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        return context.resume_job(job_id)

    return router
