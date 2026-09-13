"""Workspace discovery, conversation submission, and job recovery routes."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException

from app.activity import activity_feed, activity_journey
from app.presentation import present_run
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
        return browser_json(value)

    @router.post("/api/workspaces/{workspace_id}/runs")
    def submit(workspace_id: str, body: RunBody):
        return context.submit(workspace_id, body)

    @router.get("/api/workspaces/{workspace_id}/runs/{run_id}/followups")
    def followups(workspace_id: str, run_id: str):
        return browser_json(context.followups.get(workspace_id, run_id))

    @router.post("/api/workspaces/{workspace_id}/runs/{run_id}/followups")
    def start_followups(workspace_id: str, run_id: str):
        return browser_json(context.followups.start(workspace_id, run_id))

    @router.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return context.job(job_id)

    @router.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        job = context.job(job_id)
        return context.submit(job["workspace_id"], RunBody(message=job["message"], conversation_id=job.get("conversation_id"),
            request_id=job["request_id"], source_ids=job.get("source_ids", [])))

    return router
