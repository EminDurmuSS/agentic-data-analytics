"""Compose the local HTTP API and UI. Start with ``python -m app``.

Provider credentials exist only in the server process. Browser requests carry
questions and application-owned identifiers, never provider keys or SQL.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agentic_analytics.lakehouse.service import PlanError, error_envelope
from agentic_analytics.lakehouse.store import StoreError
from agentic_analytics.paths import REPO_ROOT
from app.context import AppContext
from app.routes import analyses, sources, workspaces

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_DB = REPO_ROOT / "data_pipeline/lakehouse/analytics.duckdb"


def create_app(*, runtime_root=None, source_db=DEFAULT_DB, client=None, validate_finance=True, searxng_url=None, followup_client=None):
    context = AppContext(Path(runtime_root or REPO_ROOT / ".lakehouse-runtime/app"), source_db, client, validate_finance=validate_finance, searxng_url=searxng_url, followup_client=followup_client)

    @asynccontextmanager
    async def lifespan(app):
        yield
        context.close()

    app = FastAPI(title="Agentic Minds", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.context = context
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_request_policy(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Farklı bir siteden yazma isteği kabul edilmez."}, status_code=403)
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 32 * 1024**2):
                return JSONResponse({"detail": "İstek 32 MiB sınırını aşıyor."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(StoreError)
    @app.exception_handler(PlanError)
    async def contract_error(request, error):
        return JSONResponse(error_envelope(error), status_code=400)

    from agentic_analytics.agent.tools.documents import DocumentError
    app.add_exception_handler(DocumentError, contract_error)
    from agentic_analytics.agent.tools.charts import ChartError
    app.add_exception_handler(ChartError, contract_error)

    from agentic_analytics.providers.mia import MiaError

    @app.exception_handler(MiaError)
    async def provider_error(request, error):
        return JSONResponse({"status": "failed", "errors": [{"code": error.code, "message": str(error)}]}, status_code=502)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, error):
        return JSONResponse({"detail": "Kayıt bu çalışma alanında bulunamadı."}, status_code=404)

    app.include_router(workspaces.create_router(context))
    app.include_router(analyses.create_router(context))
    app.include_router(sources.create_router(context))

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
