"""Durable local voice artifacts bound to one completed workspace analysis."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

from app.serialization import write_json
from agentic_analytics.lakehouse.store import file_sha256
from agentic_analytics.voice.context import VoiceContextError, build_voice_brief
from agentic_analytics.voice.script import VoiceScriptService
from agentic_analytics.voice.ema import EmaTTS, VoiceTTSError


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class VoiceServiceError(ValueError):
    pass


class VoiceSummaryService:
    """Create and read WAV artifacts without exposing a filesystem path to callers."""

    def __init__(self, root: str | Path, store, run_store, client, *, tts: EmaTTS | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store, self.run_store = store, run_store
        voice_client = client.with_limits(timeout=60, max_retries=0) if callable(getattr(client, "with_limits", None)) else client
        self.scripts = VoiceScriptService(voice_client)
        self.tts = tts or EmaTTS()

    @staticmethod
    def _checked(value: str, field: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise VoiceServiceError(f"Geçersiz {field} kimliği.")
        return value

    def _directory(self, workspace_id: str) -> Path:
        return self.root / self._checked(workspace_id, "çalışma alanı")

    def create(self, workspace_id: str, run_id: str, *, answer: str | None = None) -> dict:
        workspace_id, run_id = self._checked(workspace_id, "çalışma alanı"), self._checked(run_id, "çalışma")
        try:
            run = self.run_store.get(run_id)
        except ValueError as error:
            raise VoiceServiceError("Çalışma kaydı bulunamadı.") from error
        if run.get("workspace_id") != workspace_id:
            raise VoiceServiceError("Çalışma başka bir çalışma alanına ait.")
        try:
            brief = build_voice_brief(self.store, run, answer=answer)
        except VoiceContextError as error:
            raise VoiceServiceError(str(error)) from error
        started = time.perf_counter()
        script_record = self.scripts.generate_record(brief)
        script = script_record["transcript"]
        script_seconds = time.perf_counter() - started
        seed = json.dumps({"analysis_id": brief.analysis_id, "data_sha256": brief.data_sha256, "script": script},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        voice_id = "voice_" + hashlib.sha256(seed).hexdigest()
        directory = self._directory(workspace_id)
        target = directory / (voice_id + ".wav")
        manifest = directory / (voice_id + ".json")
        prompt = directory / (voice_id + ".prompt.json")
        if manifest.is_file() and target.is_file():
            return self.load(workspace_id, voice_id)
        try:
            tts_started = time.perf_counter()
            audio = self.tts.synthesize(script, target)
            tts_seconds = time.perf_counter() - tts_started
        except VoiceTTSError as error:
            raise VoiceServiceError(str(error)) from error
        record = {"voice_id": voice_id, "workspace_id": workspace_id, "run_id": run_id,
                  "analysis_id": brief.analysis_id, "data_sha256": brief.data_sha256,
                  "audio_sha256": file_sha256(audio), "transcript": script,
                  "format": "audio/wav", "version": 1,
                  "validation": {"prompt": brief.question, "evidence": {"analysis_id": brief.analysis_id,
                                  "data_sha256": brief.data_sha256, "fact_count": len(brief.facts),
                                  "statistic_count": len(brief.statistics), "source_count": len(brief.sources),
                                  "warning_count": len(brief.warnings)},
                                 "qwen": {"seconds": round(script_seconds, 3), "model": script_record["model"],
                                          "usage": script_record["usage"], "tool_steps": script_record["tool_steps"],
                                          "thinking_enabled": script_record["thinking_enabled"]},
                                 "ema_tts_seconds": round(tts_seconds, 3),
                                 "total_seconds": round(time.perf_counter() - started, 3)}}
        write_json(prompt, {"version": 1, "voice_id": voice_id, "messages": script_record["prompt"]})
        write_json(manifest, record)
        return self.load(workspace_id, voice_id)

    def load(self, workspace_id: str, voice_id: str) -> dict:
        workspace_id, voice_id = self._checked(workspace_id, "çalışma alanı"), self._checked(voice_id, "ses")
        directory = self._directory(workspace_id)
        manifest, audio = directory / (voice_id + ".json"), directory / (voice_id + ".wav")
        try:
            record = json.loads(manifest.read_text())
        except (OSError, ValueError) as error:
            raise VoiceServiceError("Ses özeti bulunamadı.") from error
        if (record.get("voice_id") != voice_id or record.get("workspace_id") != workspace_id or
                not audio.is_file() or record.get("audio_sha256") != file_sha256(audio)):
            raise VoiceServiceError("Ses özeti bütünlük doğrulamasını geçemedi.")
        return {key: record[key] for key in ("voice_id", "workspace_id", "run_id", "analysis_id", "data_sha256", "transcript", "format", "validation")}

    def audio_path(self, workspace_id: str, voice_id: str) -> Path:
        self.load(workspace_id, voice_id)
        return self._directory(workspace_id) / (voice_id + ".wav")
