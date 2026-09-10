"""Document uploads, inspection, raw downloads, and candidate-table review."""
from __future__ import annotations

from pathlib import Path
import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.context import AppContext
from app.models import ReviewBody, SourceBody
from app.serialization import browser_json


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workspaces/{workspace_id}/sources")
    def source_list(workspace_id: str):
        return {"sources": context.documents(workspace_id).list_sources()}

    @router.get("/api/workspaces/{workspace_id}/sources/{source_id}")
    def source_inspect(workspace_id: str, source_id: str):
        return browser_json(context.documents(workspace_id).inspect_source(source_id=source_id))

    @router.get("/api/workspaces/{workspace_id}/sources/{source_id}/tables/{table_id}/review")
    def source_candidate(workspace_id: str, source_id: str, table_id: str):
        return browser_json(context.documents(workspace_id).review_candidate(source_id, table_id))

    @router.get("/api/workspaces/{workspace_id}/sources/{source_id}/raw")
    def source_raw(workspace_id: str, source_id: str):
        documents = context.documents(workspace_id)
        source = documents.source(source_id)
        from urllib.parse import quote
        return Response(documents.raw_source_bytes(source_id), media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(source["filename"])})

    @router.post("/api/workspaces/{workspace_id}/sources/{source_id}/review")
    def source_review(workspace_id: str, source_id: str, body: ReviewBody):
        return context.documents(workspace_id).review_table(source_id, body.table_id, body.reviewed_rows, body.unit_evidence)

    @router.post("/api/workspaces/{workspace_id}/sources/url")
    def source_url(workspace_id: str, body: SourceBody):
        return browser_json(context.documents(workspace_id).inspect_source(url=body.url))

    @router.post("/api/workspaces/{workspace_id}/sources/upload")
    async def source_upload(workspace_id: str, file: UploadFile):
        context.workspace(workspace_id)
        filename = Path(file.filename or "upload").name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".csv", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".html", ".htm", ".txt"}:
            raise HTTPException(400, "Desteklenen biçimler: CSV, Excel, PDF, görsel, HTML ve metin.")
        size_limit = (8 if suffix in {".png", ".jpg", ".jpeg"} else 16) * 1024**2
        body = await file.read(size_limit + 1)
        if len(body) > size_limit:
            raise HTTPException(413, "Dosya boyut sınırını aşıyor (görsel 8 MiB, diğerleri 16 MiB).")
        directory = context.store.root / "uploads"
        directory.mkdir(exist_ok=True)
        path = directory / (uuid.uuid4().hex + suffix)
        path.write_bytes(body)
        try:
            return await run_in_threadpool(context.documents(workspace_id).register_upload, path, filename=filename)
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    return router
