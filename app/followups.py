"""Optional next-question jobs, independent of the immutable analysis ledger."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import threading

from fastapi import HTTPException

from agentic_analytics.agent.run_store import fingerprint, identifier
from agentic_analytics.lakehouse.store import StoreError
from app.presentation import present_run
from app.serialization import write_json


VERSION = "followup-sidecar-v1"


class FollowupService:
    """One bounded generation per run, with read-only polling and owner checks.

    A held file lock covers queued and running work. After a process exits, an
    orphaned pending sidecar is reported unavailable instead of silently retried.
    Main run records, conversation messages and financial artifacts are untouched.
    """

    def __init__(self, root, store, run_store, client=None, *, builder=None, generator=None,
                 max_workers=2, max_pending=8):
        self.root = Path(root)
        self.store, self.run_store, self.client = store, run_store, client
        self.builder, self.generator = builder, generator
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="followups")
        self.max_pending = max_pending
        self.lock = threading.RLock()
        self.futures = {}
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True)

    def _run(self, workspace_id, run_id):
        try:
            identifier(workspace_id)
            identifier(run_id)
            self.store.workspace(workspace_id)
            run = self.run_store.get(run_id)
            if run["workspace_id"] != workspace_id:
                raise ValueError("Wrong owner")
            return run
        except (ValueError, StoreError, FileNotFoundError):
            raise HTTPException(404, "Çalışma bu çalışma alanında bulunamadı.") from None

    @staticmethod
    def _eligible(run):
        result = run.get("result") or {}
        state = run.get("state") or {}
        if (run.get("status") != "completed" or result.get("status") != "completed"
                or result.get("errors") or state.get("unresolved_errors")
                or any((item.get("code") if isinstance(item, dict) else item) == "CLARIFICATION_AFTER_RESULT"
                       for item in result.get("warnings", []))
                or "Devam için soru:" in str(result.get("message", ""))):
            return False
        return not any(item.get("tool", item.get("name")) == "ask_user" for item in
                       result.get("tool_results", state.get("tool_results", [])) if isinstance(item, dict))

    def _current(self, run, analysis_id):
        latest = self.run_store.list(run["workspace_id"], run["conversation_id"], limit=1)
        if not latest or latest[0]["run_id"] != run["run_id"]:
            return False
        workspace_latest = self.run_store.list(run["workspace_id"], limit=1)
        if not workspace_latest or workspace_latest[0]["run_id"] != run["run_id"]:
            return False
        workspace = self.store.workspace(run["workspace_id"])
        return analysis_id is None or workspace.get("analysis_head") == analysis_id

    def _context(self, run):
        from agentic_analytics.agent.followups import VERSION as generator_version
        if self.builder is None:
            from agentic_analytics.agent.followup_context import build_followup_context
            builder = build_followup_context
        else:
            builder = self.builder
        history = list(reversed(self.run_store.list(run["workspace_id"], run["conversation_id"], limit=6)))
        displayed = [{**item, "result": present_run(self.store, item).get("result")}
                     for item in history]
        current = next((item for item in displayed if item["run_id"] == run["run_id"]), None)
        if current is None:
            current = {**run, "result": present_run(self.store, run).get("result")}
        context = builder(self.store, run["workspace_id"], current, displayed)
        # Include the source records as hashes: bounded projection changes must
        # never allow a previously generated answer to attach to different input.
        input_hashes = [fingerprint({key: item.get(key) for key in
                                    ("run_id", "updated_at", "message", "result")}) for item in history]
        digest = fingerprint({"version": VERSION, "generator_version": generator_version,
                              "context": context, "inputs": input_hashes})
        return context, digest

    def _path(self, run):
        return self.root / run["workspace_id"] / (run["run_id"] + ".json")

    def _lock_file(self, run, *, create=True):
        path = self._path(run).with_suffix(".lock")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+" if create else "r+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            handle.close()
            return None

    @staticmethod
    def _release(handle):
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _public(run, status, *, analysis_id=None, digest=None, items=None, reason=None, method=None):
        value = {"status": status, "workspace_id": run["workspace_id"], "run_id": run["run_id"],
                 "conversation_id": run["conversation_id"], "analysis_id": analysis_id,
                 "context_digest": digest, "items": items or []}
        if reason:
            value["reason"] = reason
        if method:
            value["method"] = method
        return value

    def _read(self, run):
        path = self._path(run)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 256000:
                raise ValueError("Oversized sidecar")
            value = json.loads(path.read_text())
            if (not isinstance(value, dict) or value.get("workspace_id") != run["workspace_id"]
                    or value.get("run_id") != run["run_id"]
                    or value.get("conversation_id") != run["conversation_id"]):
                raise ValueError("Invalid sidecar")
            return value
        except (OSError, ValueError):
            return {"status": "unavailable", "reason": "invalid_cache"}

    def get(self, workspace_id, run_id):
        """Poll without creating jobs, making provider calls or updating records."""
        run = self._run(workspace_id, run_id)
        analysis_id = (run.get("result") or {}).get("analysis_id")
        if not self._eligible(run):
            return self._public(run, "unavailable", analysis_id=analysis_id, reason="run_not_eligible")
        if not self._current(run, analysis_id):
            return self._public(run, "stale", analysis_id=analysis_id, reason="context_changed")
        cached = self._read(run)
        if cached is None:
            return self._public(run, "unavailable", analysis_id=analysis_id, reason="not_requested")
        if cached.get("status") == "unavailable" and not cached.get("context_digest"):
            return self._public(run, "unavailable", analysis_id=analysis_id, reason=cached.get("reason"))
        try:
            context, digest = self._context(run)
            analysis_id = context.get("analysis_id")
            if digest != cached.get("context_digest") or not self._current(run, analysis_id):
                return self._public(run, "stale", analysis_id=analysis_id, digest=digest, reason="context_changed")
        except Exception:
            return self._public(run, "unavailable", analysis_id=analysis_id, reason="context_unavailable")
        status, reason = cached.get("status"), cached.get("reason")
        if status == "pending":
            try:
                handle = self._lock_file(run, create=False)
            except FileNotFoundError:
                handle = None
                status, reason = "unavailable", "interrupted"
            if handle is not None:
                self._release(handle)
                status, reason = "unavailable", "interrupted"
        if status not in {"pending", "ready", "unavailable", "stale"}:
            status, reason = "unavailable", "invalid_cache"
        return self._public(run, status, analysis_id=analysis_id, digest=digest,
                            items=cached.get("items", []) if status == "ready" else [],
                            reason=reason, method=cached.get("method"))

    def start(self, workspace_id, run_id):
        run = self._run(workspace_id, run_id)
        with self.lock:
            if self._read(run) is not None or not self._eligible(run):
                return self.get(workspace_id, run_id)
            if not self._current(run, (run.get("result") or {}).get("analysis_id")):
                return self.get(workspace_id, run_id)
            handle = self._lock_file(run)
            if handle is None:
                return self.get(workspace_id, run_id)
            try:
                # Another process may have completed between the initial read
                # and this lock. Existing failures are deliberately not retried.
                if self._read(run) is not None:
                    return self.get(workspace_id, run_id)
                context, digest = self._context(run)
                analysis_id = context.get("analysis_id")
                if not self._current(run, analysis_id):
                    return self._public(run, "stale", analysis_id=analysis_id, digest=digest, reason="context_changed")
                pending = self._public(run, "pending", analysis_id=analysis_id, digest=digest)
                self.futures = {key: future for key, future in self.futures.items() if not future.done()}
                active = len(self.futures)
                reason = ("provider_unavailable" if self.client is None else "service_closed" if self.closed
                          else "queue_full" if active >= self.max_pending else None)
                record = {**pending, "version": VERSION, "context": context,
                          "created_at": datetime.now(timezone.utc).isoformat()}
                if reason:
                    write_json(self._path(run), {**record, "status": "unavailable", "reason": reason})
                    return self._public(run, "unavailable", analysis_id=analysis_id, digest=digest, reason=reason)
                write_json(self._path(run), record)
                future = self.pool.submit(self._work, run, record, handle)
                handle = None  # The worker owns the lock until its final write.
                self.futures[run_id] = future
                return pending
            except Exception:
                value = self._public(run, "unavailable", reason="context_unavailable")
                write_json(self._path(run), value)
                return value
            finally:
                self._release(handle)

    def _work(self, run, record, handle):
        try:
            if self.generator is None:
                from agentic_analytics.agent.followups import generate_followups
                generator = generate_followups
            else:
                generator = self.generator
            output = generator(self.client, record["context"])
            finished = {**record, "status": "ready", "items": output["items"],
                        "method": output["method"], "usage": output.get("usage", {})}
        except Exception:
            # Never expose adapter errors, response bodies or credentials.
            finished = {**record, "status": "unavailable", "items": [], "reason": "generation_failed"}
        try:
            current = self._run(run["workspace_id"], run["run_id"])
            _, digest = self._context(current)
            if (not self._eligible(current) or digest != record["context_digest"]
                    or not self._current(current, record["analysis_id"])):
                finished.update(status="stale", items=[], reason="context_changed")
            finished["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(self._path(run), finished)
        except HTTPException:
            pass  # A deleted run must not be recreated by optional work.
        except Exception:
            write_json(self._path(run), {**record, "status": "unavailable", "items": [],
                                         "reason": "context_unavailable"})
        finally:
            self._release(handle)
