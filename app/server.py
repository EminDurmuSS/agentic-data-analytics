"""Local API and UI for the persistent, tool-backed analysis workspace.

Provider credentials exist only in the server process. Browser requests carry
questions and application-owned identifiers, never provider keys or SQL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import uuid

import duckdb
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from tools.lakehouse_service import LakehouseService, PlanError, _json, error_envelope
from tools.lakehouse_store import LakehouseStore, StoreError, file_sha256

REPO = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_DB = REPO / "data_pipeline/lakehouse/analytics.duckdb"


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(_json(value), ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _safe_id(value: str):
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,160}", value):
        raise HTTPException(400, "Geçersiz kayıt kimliği.")
    return value


def _browser_json(value):
    """Keep integers beyond JavaScript's exact range lossless across the UI API."""
    def encode(item):
        if isinstance(item, int) and not isinstance(item, bool) and abs(item) > 2**53 - 1:
            return {"$integer": str(item)}
        if isinstance(item, dict):
            return {key: encode(child) for key, child in item.items()}
        if isinstance(item, list):
            return [encode(child) for child in item]
        return item
    return encode(_json(value))


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceBody(StrictBody):
    name: str = Field(default="Yeni analiz", min_length=1, max_length=100)
    profile: str = Field(default="finance", pattern="^(finance|generic)$")


class RunBody(StrictBody):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)


class SourceBody(StrictBody):
    url: str = Field(min_length=8, max_length=2000)


class ReviewBody(StrictBody):
    table_id: str = Field(min_length=1, max_length=160)
    reviewed_rows: list = Field(min_length=1, max_length=50000)
    unit_evidence: dict[str, str]


class AppContext:
    def __init__(self, root: Path, source_db: Path | None, client=None, *, validate_finance=True, searxng_url=None):
        from tools.agent_run_store import AgentRunStore

        self.root = Path(root).resolve()
        self.store = LakehouseStore(self.root / "lakehouse")
        self.run_store = AgentRunStore(self.root / "runs")
        self.client = client
        self.source_db = Path(source_db).resolve() if source_db else None
        self.validate_finance = validate_finance
        self.searxng_url = searxng_url
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="analysis")
        self.futures = {}
        self.snapshots = {}
        self.snapshot_sources = {}
        self._metadata = self.root / "application"
        for name in ("jobs", "workspaces"):
            (self._metadata / name).mkdir(parents=True, exist_ok=True)

    def snapshot(self, profile):
        with self.lock:
            source = self.source_db
            if profile == "generic":
                source = self.root / "empty-domain.duckdb"
                if not source.exists():
                    with duckdb.connect(str(source)) as conn:
                        conn.execute("CREATE TABLE platform_metadata (key VARCHAR PRIMARY KEY, value VARCHAR)")
                        conn.execute("INSERT INTO platform_metadata VALUES ('profile', 'generic')")
            if source is None or not source.is_file():
                raise HTTPException(409, "Veri tabanı hazır değil. Önce lakehouse build komutunu çalıştırın veya boş çalışma alanı açın.")
            stat = source.stat()
            # Published databases are atomically replaced. New workspaces see
            # the new release; existing workspaces retain their own snapshot.
            source_version = (str(source), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if profile in self.snapshots and self.snapshot_sources.get(profile) == source_version:
                return self.snapshots[profile]
            source_hash = file_sha256(source)
            cached = self._metadata / ("snapshot-" + profile + ".json")
            if cached.exists():
                value = json.loads(cached.read_text())
                if value.get("source_sha256") == source_hash:
                    try:
                        self.store.snapshot_path(value["snapshot_id"])
                        self.snapshots[profile] = value["snapshot_id"]
                        self.snapshot_sources[profile] = source_version
                        return value["snapshot_id"]
                    except StoreError:
                        pass
            validator = None
            if profile == "finance" and self.validate_finance:
                from tools.lakehouse_quality import validate_database
                validator = validate_database
            release = self.store.publish_snapshot(source, validator)
            self.snapshots[profile] = release["snapshot_id"]
            self.snapshot_sources[profile] = source_version
            _write(cached, {"snapshot_id": release["snapshot_id"], "source_sha256": source_hash})
            return release["snapshot_id"]

    def create_workspace(self, name, profile):
        workspace = self.store.create_workspace(self.snapshot(profile))
        metadata = {"name": name, "profile": profile, "created_at": datetime.now(timezone.utc).isoformat()}
        _write(self._metadata / "workspaces" / (workspace["workspace_id"] + ".json"), metadata)
        return {**workspace, **metadata}

    def workspace(self, workspace_id):
        value = self.store.workspace(workspace_id)
        meta = self._metadata / "workspaces" / (_safe_id(workspace_id) + ".json")
        return {**value, **(json.loads(meta.read_text()) if meta.exists() else {"name": workspace_id, "profile": "finance"})}

    def workspaces(self):
        result = []
        for path in (self.store.root / "workspaces").iterdir():
            if path.is_dir():
                result.append(self.workspace(path.name))
        return sorted(result, key=lambda value: value.get("created_at", ""), reverse=True)

    def documents(self, workspace_id):
        from tools.agent_documents import DocumentTools
        self.workspace(workspace_id)
        return DocumentTools(self.store, workspace_id, ocr_callback=self.read_image if self.client else None, searxng_url=self.searxng_url)

    def read_image(self, image_bytes, mime_type):
        """Vision extraction is schema constrained; the document tool verifies units.

        The provider's dedicated OCR remains available in MiaClient. The verified
        vision route is the initial fallback for document/table images here.
        """
        schema = {"type": "object", "properties": {
            "text": {"type": "string"},
            "tables": {"type": "array", "items": {"type": "object", "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}},
                "units": {"type": "object", "additionalProperties": {"type": "string"}},
            }, "required": ["columns", "rows", "units"], "additionalProperties": False}},
        }, "required": ["text", "tables"], "additionalProperties": False}
        response = self.client.image_chat(image_bytes, mime_type, ocr=False,
            prompt="Transcribe the visible source verbatim into text and tables. Preserve all dates, values, decimal separators, headings and explicit units. Never guess unreadable cells: use null. units maps original column headings to units visibly stated in the source. Return the requested JSON schema.",
            response_format={"type": "json_schema", "json_schema": {"name": "source_tables", "strict": True, "schema": schema}})
        if response.get("finish_reason") == "length":
            from tools.agent_documents import DocumentError
            raise DocumentError("Görsel çıktısı tamamlanamadı.", "OCR_INVALID_OUTPUT")
        import jsonschema
        try:
            value = json.loads(response["content"])
            jsonschema.validate(value, schema)
        except (ValueError, TypeError, jsonschema.ValidationError):
            from tools.agent_documents import DocumentError
            raise DocumentError("Görselden geçerli bir tablo çıkarılamadı.", "OCR_INVALID_OUTPUT") from None
        return {**value, "extraction_method": "mia_qwen_vision", "machine_extracted": True}

    def runtime(self, workspace_id):
        from tools.agent_runtime import AgentRuntime
        from tools.agent_statistics import StatisticsTools
        from tools.agent_charts import ChartTools
        tools = self.documents(workspace_id).extra_tools()
        tools.update(StatisticsTools(self.store, workspace_id).extra_tools())
        tools.update(ChartTools(self.store, workspace_id).extra_tools())
        return AgentRuntime(self.store, workspace_id, self.client, self.run_store, extra_tools=tools)

    def submit(self, workspace_id, body: RunBody):
        if self.client is None:
            raise HTTPException(503, "Kloudeks anahtarı sunucu ortamında tanımlı değil. MIA_API_KEY ile veya --prompt-key seçeneğiyle başlatın.")
        self.workspace(workspace_id)
        request_id = _safe_id(body.request_id or "request_" + uuid.uuid4().hex)
        job_id = "job_" + hashlib.sha256((workspace_id + ":" + request_id).encode()).hexdigest()[:32]
        job_path = self._metadata / "jobs" / (job_id + ".json")
        values = {"job_id": job_id, "workspace_id": workspace_id, "request_id": request_id,
                  "conversation_id": body.conversation_id, "message": body.message}
        with self.lock:
            if job_path.exists():
                previous = json.loads(job_path.read_text())
                if any(previous.get(key) != values.get(key) for key in ("workspace_id", "request_id", "conversation_id", "message")):
                    raise HTTPException(409, "Aynı istek kimliği farklı içerikle kullanılamaz.")
                if previous.get("status") == "finished" or job_id in self.futures and not self.futures[job_id].done():
                    return self.job(job_id)
            _write(job_path, {**values, "status": "queued"})

            def work():
                _write(job_path, {**values, "status": "running"})
                try:
                    result = self.runtime(workspace_id).run(body.message, conversation_id=body.conversation_id, request_id=request_id)
                    _write(job_path, {**values, "status": "finished", "result": result})
                except Exception as exc:
                    # Provider exceptions must already be scrubbed by MiaClient;
                    # the app returns a generic message for unexpected failures.
                    detail = error_envelope(exc) if isinstance(exc, (PlanError, StoreError)) else {"status": "failed", "message": "Çalışma tamamlanamadı. Kaydedilmiş araç adımlarından yeniden deneyebilirsiniz.", "error_type": type(exc).__name__}
                    _write(job_path, {**values, "status": "failed", "result": detail})

            self.futures[job_id] = self.pool.submit(work)
        return {"job_id": job_id, "workspace_id": workspace_id, "request_id": request_id, "status": "queued"}

    def job(self, job_id):
        path = self._metadata / "jobs" / (_safe_id(job_id) + ".json")
        if not path.exists():
            raise HTTPException(404, "Çalışma bulunamadı.")
        job = json.loads(path.read_text())
        run = self.run_store.find_request(job["workspace_id"], job["request_id"])
        if run:
            job["run"] = run
            job["events"] = self.run_store.events(run["run_id"])
        else:
            job["events"] = []
        if job["status"] in {"queued", "running"} and job_id not in self.futures:
            job["status"] = "interrupted"
        return _browser_json(job)


def create_app(*, runtime_root=None, source_db=DEFAULT_DB, client=None, validate_finance=True, searxng_url=None):
    context = AppContext(Path(runtime_root or REPO / ".lakehouse-runtime/app"), source_db, client, validate_finance=validate_finance, searxng_url=searxng_url)
    app = FastAPI(title="Agentic Minds", docs_url=None, redoc_url=None, openapi_url=None)
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

    from tools.agent_documents import DocumentError
    app.add_exception_handler(DocumentError, contract_error)
    from tools.agent_charts import ChartError
    app.add_exception_handler(ChartError, contract_error)

    from tools.mia_client import MiaError

    @app.exception_handler(MiaError)
    async def provider_error(request, error):
        return JSONResponse({"status": "failed", "errors": [{"code": error.code, "message": str(error)}]}, status_code=502)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, error):
        return JSONResponse({"detail": "Kayıt bu çalışma alanında bulunamadı."}, status_code=404)

    @app.get("/api/status")
    def status():
        return {"status": "ok", "provider_ready": context.client is not None, "finance_available": bool(context.source_db and context.source_db.is_file()), "models": {"chat": "kkbhackathon2026/Qwen3.8-27B", "embedding": "kkbhackathon2026/Qwen3-Embedding-8B", "ocr": "kkbhackathon2026/Unlimited-OCR"}}

    @app.get("/api/workspaces")
    def workspaces():
        return {"workspaces": context.workspaces()}

    @app.post("/api/workspaces")
    def new_workspace(body: WorkspaceBody):
        return context.create_workspace(body.name, body.profile)

    @app.get("/api/workspaces/{workspace_id}")
    def workspace(workspace_id: str):
        value = context.workspace(workspace_id)
        value["runs"] = context.run_store.list(workspace_id, limit=30)
        if value["runs"]:
            latest = value["runs"][0]
            value["latest_events"] = context.run_store.events(latest["run_id"])
            if not latest["result"]:
                value["pending_job_id"] = "job_" + hashlib.sha256((workspace_id + ":" + latest["request_id"]).encode()).hexdigest()[:32]
        return _browser_json(value)

    @app.post("/api/workspaces/{workspace_id}/runs")
    def submit(workspace_id: str, body: RunBody):
        return context.submit(workspace_id, body)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return context.job(job_id)

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        job = context.job(job_id)
        return context.submit(job["workspace_id"], RunBody(message=job["message"], conversation_id=job.get("conversation_id"), request_id=job["request_id"]))

    @app.get("/api/workspaces/{workspace_id}/discover")
    def discover(workspace_id: str, query: str = "", limit: int = 10):
        return LakehouseService(context.store, workspace_id).discover({"query": query, "limit": limit})

    @app.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}")
    def analysis(workspace_id: str, analysis_id: str, offset: int = 0, limit: int = 250):
        if not 0 <= offset or not 1 <= limit <= 2000:
            raise HTTPException(400, "Geçersiz sayfalama.")
        frame, manifest = context.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != workspace_id:
            raise HTTPException(404, "Analiz bu çalışma alanında bulunamadı.")
        return _browser_json({"analysis_id": analysis_id, "parent_analysis_id": manifest.get("parent_analysis_id"), "columns": list(frame), "schema": manifest.get("schema", {}), "row_count": len(frame), "offset": offset, "warnings": manifest.get("lineage", {}).get("warnings", []), "preserved_columns": list(manifest.get("lineage", {}).get("preserved_columns", {})), "rows": frame.iloc[offset:offset + limit].to_dict("records"), "plan": manifest["plan"], "sources": {name: {"metric_id": proof.get("binding", {}).get("metric_id"), "title": proof.get("binding", {}).get("title"), "unit": proof.get("binding", {}).get("unit"), "scale": proof.get("binding", {}).get("scale"), "source_system": proof.get("binding", {}).get("source_system")} for name, proof in manifest.get("lineage", {}).get("sources", {}).items()}})

    @app.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/csv")
    def download(workspace_id: str, analysis_id: str):
        frame, manifest = context.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != workspace_id:
            raise HTTPException(404, "Analiz bu çalışma alanında bulunamadı.")
        safe = frame.copy()
        for column in safe.select_dtypes(include=["object", "string"]):
            safe[column] = safe[column].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v)
        return Response(safe.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="analysis.csv"'})

    @app.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/chart")
    def analysis_chart(workspace_id: str, analysis_id: str):
        from tools.agent_charts import ChartTools
        return _browser_json(ChartTools(context.store, workspace_id).get_chart(analysis_id))

    @app.post("/api/workspaces/{workspace_id}/analyses/{analysis_id}/chart")
    def save_chart(workspace_id: str, analysis_id: str, body: dict):
        from tools.agent_charts import ChartTools, ChartError
        if "analysis_id" in body:
            raise ChartError("Analiz kimliği URL üzerinden seçilir.")
        with context.run_store.workspace_lock(workspace_id):
            charts = ChartTools(context.store, workspace_id)
            saved = charts.create_chart({**body, "analysis_id": analysis_id})
            return _browser_json(charts.load_artifact(saved["chart_id"]))

    @app.get("/api/workspaces/{workspace_id}/charts/{chart_id}")
    def chart_artifact(workspace_id: str, chart_id: str):
        from tools.agent_charts import ChartTools
        return _browser_json(ChartTools(context.store, workspace_id).load_artifact(chart_id))

    @app.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/explain")
    def explain(workspace_id: str, analysis_id: str, column: str, period: str, dimensions: str | None = None):
        arguments = {"analysis_id": analysis_id, "column": column, "period": period}
        if dimensions is not None:
            try:
                value = json.loads(dimensions)
                if not isinstance(value, dict) or len(value) > 10:
                    raise ValueError()
                import re
                for key, item in value.items():
                    if isinstance(item, dict) and set(item) == {"$integer"}:
                        if not isinstance(item["$integer"], str) or not re.fullmatch(r"-?\d{1,19}", item["$integer"]):
                            raise ValueError()
                        value[key] = int(item["$integer"])
                arguments["dimensions"] = value
            except ValueError:
                raise HTTPException(400, "Geçersiz hücre boyutları.") from None
        return _browser_json(LakehouseService(context.store, workspace_id).explain_value(arguments))

    @app.get("/api/workspaces/{workspace_id}/sources")
    def source_list(workspace_id: str):
        return {"sources": context.documents(workspace_id).list_sources()}

    @app.get("/api/workspaces/{workspace_id}/sources/{source_id}")
    def source_inspect(workspace_id: str, source_id: str):
        return _browser_json(context.documents(workspace_id).inspect_source(source_id=source_id))

    @app.get("/api/workspaces/{workspace_id}/sources/{source_id}/tables/{table_id}/review")
    def source_candidate(workspace_id: str, source_id: str, table_id: str):
        return _browser_json(context.documents(workspace_id).review_candidate(source_id, table_id))

    @app.get("/api/workspaces/{workspace_id}/sources/{source_id}/raw")
    def source_raw(workspace_id: str, source_id: str):
        documents = context.documents(workspace_id)
        source = documents.source(source_id)
        from urllib.parse import quote
        return Response(documents.raw_source_bytes(source_id), media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(source["filename"])})

    @app.post("/api/workspaces/{workspace_id}/sources/{source_id}/review")
    def source_review(workspace_id: str, source_id: str, body: ReviewBody):
        return context.documents(workspace_id).review_table(source_id, body.table_id, body.reviewed_rows, body.unit_evidence)

    @app.get("/api/workspaces/{workspace_id}/statistics/{artifact_id}")
    def statistics(workspace_id: str, artifact_id: str):
        from tools.agent_statistics import StatisticsTools, StatisticsError
        try:
            return StatisticsTools(context.store, workspace_id).load_artifact(artifact_id)
        except (StatisticsError, FileNotFoundError):
            raise HTTPException(404, "İstatistik kaydı bu çalışma alanında bulunamadı.") from None

    @app.post("/api/workspaces/{workspace_id}/sources/url")
    def source_url(workspace_id: str, body: SourceBody):
        return _browser_json(context.documents(workspace_id).inspect_source(url=body.url))

    @app.post("/api/workspaces/{workspace_id}/sources/upload")
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

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
