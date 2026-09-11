"""Application services: workspace snapshots, tool wiring, and persistent jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import uuid

import duckdb
from fastapi import HTTPException

from agentic_analytics.lakehouse.service import PlanError, error_envelope
from agentic_analytics.lakehouse.store import LakehouseStore, StoreError, file_sha256
from app.diagnostics import log_job_failure
from app.models import RunBody
from app.serialization import browser_json, write_json


def _safe_id(value: str):
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,160}", value):
        raise HTTPException(400, "Geçersiz kayıt kimliği.")
    return value


class AppContext:
    def __init__(self, root: Path, source_db: Path | None, client=None, *, validate_finance=True, searxng_url=None):
        from agentic_analytics.agent.run_store import AgentRunStore

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
                from agentic_analytics.lakehouse.quality import validate_database
                validator = validate_database
            release = self.store.publish_snapshot(source, validator)
            self.snapshots[profile] = release["snapshot_id"]
            self.snapshot_sources[profile] = source_version
            write_json(cached, {"snapshot_id": release["snapshot_id"], "source_sha256": source_hash})
            return release["snapshot_id"]

    def create_workspace(self, name, profile):
        workspace = self.store.create_workspace(self.snapshot(profile))
        metadata = {"name": name, "profile": profile, "created_at": datetime.now(timezone.utc).isoformat()}
        write_json(self._metadata / "workspaces" / (workspace["workspace_id"] + ".json"), metadata)
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
        from agentic_analytics.agent.tools.documents import DocumentTools
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
            from agentic_analytics.agent.tools.documents import DocumentError
            raise DocumentError("Görsel çıktısı tamamlanamadı.", "OCR_INVALID_OUTPUT")
        import jsonschema
        try:
            value = json.loads(response["content"])
            jsonschema.validate(value, schema)
        except (ValueError, TypeError, jsonschema.ValidationError):
            from agentic_analytics.agent.tools.documents import DocumentError
            raise DocumentError("Görselden geçerli bir tablo çıkarılamadı.", "OCR_INVALID_OUTPUT") from None
        return {**value, "extraction_method": "mia_qwen_vision", "machine_extracted": True}

    def runtime(self, workspace_id):
        from agentic_analytics.agent.runtime import AgentRuntime
        from agentic_analytics.agent.tools.statistics import StatisticsTools
        from agentic_analytics.agent.tools.charts import ChartTools
        tools = self.documents(workspace_id).extra_tools()
        tools.update(StatisticsTools(self.store, workspace_id).extra_tools())
        tools.update(ChartTools(self.store, workspace_id).extra_tools())
        # Generous bounds so multi-step analyses reach execution instead of dying
        # on the budget; a finite cap still prevents a stuck model from looping
        # forever (the wall clock is the ultimate backstop).
        return AgentRuntime(self.store, workspace_id, self.client, self.run_store, extra_tools=tools,
                            max_decisions=30, max_repairs=4, max_elapsed_seconds=900)

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
            write_json(job_path, {**values, "status": "queued"})

            def work():
                write_json(job_path, {**values, "status": "running"})
                try:
                    result = self.runtime(workspace_id).run(body.message, conversation_id=body.conversation_id, request_id=request_id)
                    write_json(job_path, {**values, "status": "finished", "result": result})
                except Exception as exc:
                    # Provider exceptions must already be scrubbed by MiaClient;
                    # the app returns a generic message for unexpected failures.
                    log_job_failure(exc, job_id=job_id, workspace_id=workspace_id)
                    detail = error_envelope(exc) if isinstance(exc, (PlanError, StoreError)) else {"status": "failed", "message": "Çalışma tamamlanamadı. Kaydedilmiş araç adımlarından yeniden deneyebilirsiniz.", "error_type": type(exc).__name__}
                    write_json(job_path, {**values, "status": "failed", "result": detail})

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
        return browser_json(job)
