"""Durable run, conversation and tool-result journal with workspace serialization."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Invalid run, conversation, request or workspace identifier")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


class AgentRunStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "locks").mkdir(exist_ok=True)
        self.db_path = self.root / "runs.sqlite3"
        with self._db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                    messages TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL,
                    result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, request_id));
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq));
                CREATE TABLE IF NOT EXISTS steps (
                    run_id TEXT NOT NULL, step_id TEXT NOT NULL, name TEXT NOT NULL,
                    args TEXT NOT NULL, intent TEXT NOT NULL, result TEXT,
                    PRIMARY KEY(run_id, step_id));
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def workspace_lock(self, workspace_id):
        """A separate lock from the lakehouse writer lock; safe across processes."""
        with (self.root / "locks" / (identifier(workspace_id) + ".lock")).open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def start(self, workspace_id, message, conversation_id=None, request_id=None):
        identifier(workspace_id)
        request_id = identifier(request_id or "request_" + uuid.uuid4().hex)
        if conversation_id is not None:
            identifier(conversation_id)
        with self._db() as db:
            existing = db.execute("SELECT * FROM runs WHERE workspace_id=? AND request_id=?", (workspace_id, request_id)).fetchone()
            if existing:
                if existing["message"] != message or (conversation_id is not None and existing["conversation_id"] != conversation_id):
                    raise ValueError("request_id was already used for a different request")
                return self._run(existing)
            unfinished = db.execute("SELECT run_id FROM runs WHERE workspace_id=? AND status='running' LIMIT 1", (workspace_id,)).fetchone()
            if unfinished:
                raise ValueError("Workspace has an interrupted or active run; resume its run_id before starting another request")
            conversation_id = conversation_id or "conversation_" + uuid.uuid4().hex
            row = db.execute("SELECT workspace_id,messages FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            if row and row["workspace_id"] != workspace_id:
                raise ValueError("Conversation belongs to another workspace")
            if not row:
                db.execute("INSERT INTO conversations VALUES (?,?,?)", (conversation_id, workspace_id, "[]"))
            messages = json.loads(row["messages"]) if row else []
            messages.append({"role": "user", "content": message})
            previous = db.execute("SELECT result FROM runs WHERE conversation_id=? AND result IS NOT NULL ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
            prior = json.loads(previous["result"]) if previous else {}
            state = {"messages": messages, "decisions": 0, "repairs": 0, "pending": [],
                     "analysis_id": None, "artifacts": prior.get("artifacts", []), "tool_results": [], "usage": [], "seen": {}}
            run_id, now = "run_" + uuid.uuid4().hex, _now()
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, workspace_id, conversation_id, request_id, message, "running", canonical(state), None, now, now))
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return self._run(row)

    @staticmethod
    def _run(row):
        if row is None:
            raise ValueError("Unknown run_id")
        result = dict(row)
        result["state"] = json.loads(result["state"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def get(self, run_id):
        with self._db() as db:
            return self._run(db.execute("SELECT * FROM runs WHERE run_id=?", (identifier(run_id),)).fetchone())

    def find_request(self, workspace_id, request_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE workspace_id=? AND request_id=?", (identifier(workspace_id), identifier(request_id))).fetchone()
            return self._run(row) if row else None

    def delete_workspace(self, workspace_id):
        identifier(workspace_id)
        with self._db() as db:
            db.execute("DELETE FROM steps WHERE run_id IN (SELECT run_id FROM runs WHERE workspace_id=?)", (workspace_id,))
            db.execute("DELETE FROM events WHERE run_id IN (SELECT run_id FROM runs WHERE workspace_id=?)", (workspace_id,))
            db.execute("DELETE FROM runs WHERE workspace_id=?", (workspace_id,))
            db.execute("DELETE FROM conversations WHERE workspace_id=?", (workspace_id,))

    def delete_conversation(self, workspace_id, conversation_id):
        identifier(workspace_id)
        identifier(conversation_id)
        with self._db() as db:
            db.execute("DELETE FROM steps WHERE run_id IN (SELECT run_id FROM runs WHERE workspace_id=? AND conversation_id=?)", (workspace_id, conversation_id))
            db.execute("DELETE FROM events WHERE run_id IN (SELECT run_id FROM runs WHERE workspace_id=? AND conversation_id=?)", (workspace_id, conversation_id))
            db.execute("DELETE FROM runs WHERE workspace_id=? AND conversation_id=?", (workspace_id, conversation_id))
            db.execute("DELETE FROM conversations WHERE workspace_id=? AND conversation_id=?", (workspace_id, conversation_id))

    def list(self, workspace_id, conversation_id=None, limit=20):
        identifier(workspace_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid run list limit")
        with self._db() as db:
            rows = db.execute("SELECT * FROM runs WHERE workspace_id=? AND (? IS NULL OR conversation_id=?) ORDER BY created_at DESC LIMIT ?", (workspace_id, conversation_id, conversation_id, limit)).fetchall()
            return [self._run(row) for row in rows]

    def conversation(self, conversation_id, workspace_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM conversations WHERE conversation_id=?", (identifier(conversation_id),)).fetchone()
            if not row or row["workspace_id"] != workspace_id:
                raise ValueError("Unknown conversation in this workspace")
            return {**dict(row), "messages": json.loads(row["messages"])}

    def checkpoint(self, run_id, state):
        with self._db() as db:
            db.execute("UPDATE runs SET state=?,updated_at=? WHERE run_id=?", (canonical(state), _now(), identifier(run_id)))

    def event(self, run_id, kind, payload):
        with self._db() as db:
            db.execute("INSERT INTO events SELECT ?,COALESCE(MAX(seq),0)+1,?,?,? FROM events WHERE run_id=?", (identifier(run_id), kind, canonical(payload), _now(), run_id))

    def events(self, run_id):
        with self._db() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (identifier(run_id),)).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def step(self, run_id, step_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM steps WHERE run_id=? AND step_id=?", (identifier(run_id), step_id)).fetchone()
            if row is None:
                return None
            return {**dict(row), "args": json.loads(row["args"]), "intent": json.loads(row["intent"]), "result": json.loads(row["result"]) if row["result"] is not None else None}

    def begin_step(self, run_id, step_id, name, args, intent):
        with self._db() as db:
            db.execute("INSERT INTO steps VALUES (?,?,?,?,?,NULL)", (identifier(run_id), step_id, name, canonical(args), canonical(intent)))

    def complete_step(self, run_id, step_id, result):
        with self._db() as db:
            db.execute("UPDATE steps SET result=? WHERE run_id=? AND step_id=?", (canonical(result), identifier(run_id), step_id))

    def finish(self, run_id, state, result):
        with self._db() as db:
            row = db.execute("SELECT conversation_id FROM runs WHERE run_id=?", (identifier(run_id),)).fetchone()
            if row is None:
                raise ValueError("Unknown run_id")
            db.execute("UPDATE runs SET status=?,state=?,result=?,updated_at=? WHERE run_id=?", (result["status"], canonical(state), canonical(result), _now(), run_id))
            db.execute("UPDATE conversations SET messages=? WHERE conversation_id=?", (canonical(state["messages"]), row["conversation_id"]))
