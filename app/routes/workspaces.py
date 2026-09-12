"""Workspace discovery, conversation submission, and job recovery routes."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter

from app.activity import activity_feed, public_run
from app.context import AppContext
from app.models import RunBody, WorkspaceBody
from app.serialization import browser_json


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/status")
    def status():
        return {"status": "ok", "provider_ready": context.client is not None, "finance_available": bool(context.source_db and context.source_db.is_file()), "models": {"chat": "kkbhackathon2026/Qwen3.8-27B", "embedding": "kkbhackathon2026/Qwen3-Embedding-8B", "ocr": "kkbhackathon2026/Unlimited-OCR"}}

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
        value["runs"] = [public_run(run) for run in runs]
        if runs:
            latest = runs[0]
            value["latest_activity"] = activity_feed(context.run_store.events(latest["run_id"]))
            value["activity_count"] = len(value["latest_activity"])
            if not latest["result"]:
                value["pending_job_id"] = "job_" + hashlib.sha256((workspace_id + ":" + latest["request_id"]).encode()).hexdigest()[:32]
        return browser_json(value)

    @router.post("/api/workspaces/{workspace_id}/runs")
    def submit(workspace_id: str, body: RunBody):
        return context.submit(workspace_id, body)

    @router.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return context.job(job_id)

    @router.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        job = context.job(job_id)
        return context.submit(job["workspace_id"], RunBody(message=job["message"], conversation_id=job.get("conversation_id"), request_id=job["request_id"]))

    return router
