"""Backend contracts for the local voice-summary pipeline."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import wave

import duckdb
from fastapi.testclient import TestClient

from agentic_analytics.voice.context import VoiceContextError, build_voice_brief
from agentic_analytics.voice.script import VoiceScriptError, VoiceScriptService
from agentic_analytics.voice.service import VoiceServiceError, VoiceSummaryService
from agentic_analytics.voice.ema import EmaTTS, VoiceTTSError
from app.server import create_app


def tool(name, arguments):
    return {"role": "assistant", "content": None, "finish_reason": "stop", "tool_calls": [
        {"id": "call-1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


class VoiceBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Path(self.temp.name) / "finance.duckdb"
        with duckdb.connect(str(database)) as db:
            db.execute("CREATE SCHEMA catalog")
            binding = {"metric_id": "test:credit", "title": "Kredi bakiyesi", "source_system": "TEST",
                       "table": "observations", "time_column": "month", "value_column": "credit", "filters": {},
                       "dimensions": {}, "native_frequency": "monthly", "kind": "stock", "unit": "TRY", "scale": 1,
                       "currency": "TRY", "aggregation": "last", "source_base": "", "provenance_columns": [],
                       "status": "ready", "notes": [], "contract_version": "test"}
            db.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [binding["metric_id"], json.dumps(binding)])
            db.execute("CREATE TABLE observations(month VARCHAR,credit DOUBLE)")
            db.execute("INSERT INTO observations VALUES ('2026-01',100),('2026-02',120),('2026-03',150)")
        self.provider = Provider([
            tool("execute", {"start": "2026-01", "end": "2026-03", "frequency": "monthly",
                             "columns": [{"name": "credit", "metric_id": "test:credit", "dimensions": {}}]}),
            {"role": "assistant", "content": "Sonuç hazır.", "finish_reason": "stop", "tool_calls": []},
        ])
        self.app = create_app(runtime_root=Path(self.temp.name) / "runtime", source_db=database,
                              client=self.provider, validate_finance=False, searxng_url=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.app.state.context.close()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def _completed_run(self):
        workspace = self.client.post("/api/workspaces", json={"name": "Ses", "profile": "finance"}).json()
        submitted = self.client.post(f"/api/workspaces/{workspace['workspace_id']}/runs", json={"message": "Kredi nasıl değişti?"}).json()
        self.app.state.context.futures[submitted["job_id"]].result(timeout=10)
        job = self.client.get(f"/api/jobs/{submitted['job_id']}").json()
        self.assertEqual(job["result"]["status"], "completed")
        return self.app.state.context.run_store.get(job["run"]["run_id"])

    def test_context_accepts_completed_analysis_and_redacts_urls(self):
        run = self._completed_run()
        run["result"]["display_message"] = "Kredi arttı. https://gizli.example/yer-almaz"
        brief = build_voice_brief(self.app.state.context.store, run)
        self.assertEqual(brief.analysis_id, run["state"]["analysis_id"])
        self.assertEqual(brief.analysis["row_count"], 3)
        self.assertNotIn("https://", brief.answer)
        self.assertEqual(brief.sources[0]["title"], "Kredi bakiyesi")

    def test_context_rejects_web_research_and_script_has_no_tools(self):
        run = self._completed_run()
        run["state"]["tool_results"].append({"tool": "research_web", "result": {"status": "ok"}})
        with self.assertRaises(VoiceContextError):
            build_voice_brief(self.app.state.context.store, run)
        run["state"]["tool_results"].pop()
        brief = build_voice_brief(self.app.state.context.store, run)
        script_client = Provider([{"content": "Kredi bakiyesi incelenen dönemde arttı."}])
        self.assertEqual(VoiceScriptService(script_client).generate(brief), "Kredi bakiyesi incelenen dönemde arttı.")
        self.assertEqual(script_client.requests[0]["tool_choice"], "none")
        self.assertIn("Grafik incelendiğinde", script_client.requests[0]["messages"][0]["content"])
        self.assertIn("parantez içi yer veya kod", script_client.requests[0]["messages"][0]["content"])
        with self.assertRaises(VoiceScriptError):
            VoiceScriptService(Provider([{"content": "# başlık"}])).generate(brief)
        with self.assertRaises(VoiceScriptError):
            VoiceScriptService(Provider([{"content": "Kredi bakiyesi 999 arttı."}])).generate(brief)

    def test_voice_script_expands_tl_for_clear_turkish_pronunciation(self):
        run = self._completed_run()
        brief = build_voice_brief(self.app.state.context.store, run)
        service = VoiceScriptService(Provider([{"content": "Kredi bakiyesi 150 TL oldu; 100 TL'den yükseldi."}]))

        self.assertEqual(service.generate(brief), "Kredi bakiyesi 150 Türk lirası oldu; 100 Türk lirasından yükseldi.")
        self.assertIn("'Türk lirası'", service.client.requests[0]["messages"][0]["content"])

    def _ema_model(self):
        model = Path(self.temp.name) / "ema"
        (model / "ckpt").mkdir(parents=True)
        for name in ("inference.py", "model.py", "text.py"):
            (model / name).write_text("# local EMA fixture\n")
        (model / "ckpt" / "config.json").write_text("{}")
        (model / "ckpt" / "model.safetensors").write_bytes(b"local-model")
        return model

    def test_ema_uses_explicit_local_model_and_validates_wav(self):
        model = self._ema_model()
        output = Path(self.temp.name) / "speech.wav"

        def runner(command, **kwargs):
            self.assertIn("--model", command)
            self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
            target = Path(command[command.index("--out") + 1])
            with wave.open(str(target), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(b"\0\0" * 32)
            return type("Completed", (), {"returncode": 0})()

        self.assertEqual(EmaTTS(model, runner=runner).synthesize("Yerel ses özeti.", output), output.resolve())
        self.assertTrue(output.is_file())
        with self.assertRaises(VoiceTTSError):
            EmaTTS(Path(self.temp.name) / "missing", runner=runner).synthesize("Metin", output)

    def test_service_persists_voice_for_its_workspace_only(self):
        run = self._completed_run()
        model = self._ema_model()

        def runner(command, **kwargs):
            target = Path(command[command.index("--out") + 1])
            with wave.open(str(target), "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48000); audio.writeframes(b"\0\0" * 32)
            return type("Completed", (), {"returncode": 0})()

        voice_client = Provider([{"content": "Kredi bakiyesi incelenen dönemde arttı."}])
        service = VoiceSummaryService(Path(self.temp.name) / "voice", self.app.state.context.store,
                                      self.app.state.context.run_store, voice_client,
                                      tts=EmaTTS(model, runner=runner))
        saved = service.create(run["workspace_id"], run["run_id"])
        self.assertEqual(saved["analysis_id"], run["state"]["analysis_id"])
        self.assertEqual(saved["validation"]["qwen"]["tool_steps"], 0)
        self.assertFalse(saved["validation"]["qwen"]["thinking_enabled"])
        self.assertEqual(saved["validation"]["evidence"]["source_count"], 1)
        self.assertTrue(service.audio_path(run["workspace_id"], saved["voice_id"]).is_file())
        self.assertTrue((Path(self.temp.name) / "voice" / run["workspace_id"] / (saved["voice_id"] + ".prompt.json")).is_file())
        with self.assertRaises(VoiceServiceError):
            service.load("workspace_other", saved["voice_id"])
        voice_client.responses.append({"content": "Kredi bakiyesi incelenen dönemde arttı."})
        self.app.state.context._voice_service = service
        created = self.client.post(f"/api/workspaces/{run['workspace_id']}/runs/{run['run_id']}/voice")
        self.assertEqual(created.status_code, 200)
        voice_job = created.json()["voice_job_id"]
        self.app.state.context.voice_futures[voice_job].result(timeout=10)
        completed = self.client.get(f"/api/voice-jobs/{voice_job}").json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["voice_id"], saved["voice_id"])
        audio = self.client.get(f"/api/workspaces/{run['workspace_id']}/voice/{saved['voice_id']}/audio")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.headers["content-type"], "audio/wav")
