"""Offline EMA-TTS subprocess adapter for a pre-provisioned local model directory."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave


class VoiceTTSError(RuntimeError):
    pass


class EmaTTS:
    """Run EMA-TTS without permitting Hugging Face downloads at request time."""

    def __init__(self, model_dir: str | Path | None = None, *, python: str | Path | None = None,
                 device: str = "cpu", timeout_seconds: int = 240, runner=subprocess.run):
        self.model_dir = Path(model_dir or os.environ.get("VOICE_EMA_MODEL", ""))
        self.python = str(python or os.environ.get("VOICE_EMA_PYTHON") or sys.executable)
        self.device = device
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def _validate_model(self) -> tuple[Path, Path]:
        root = self.model_dir.resolve()
        checkpoint = root / "ckpt"
        expected = (root / "inference.py", root / "model.py", root / "text.py",
                    checkpoint / "config.json", checkpoint / "model.safetensors")
        if not all(path.is_file() for path in expected):
            raise VoiceTTSError("Yerel EMA-TTS model dizini veya ağırlıkları yapılandırılmamış.")
        return root, checkpoint

    def synthesize(self, text: str, output: str | Path) -> Path:
        if not isinstance(text, str) or not text.strip() or len(text) > 900:
            raise VoiceTTSError("Seslendirilecek metin geçersiz.")
        root, checkpoint = self._validate_model()
        target = Path(output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="voice-", suffix=".wav", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
        application_root = str(Path(__file__).resolve().parents[2])
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "PYTHONPATH": application_root + os.pathsep + os.environ.get("PYTHONPATH", "")}
        try:
            completed = self.runner([self.python, "-m", "agentic_analytics.voice.ema_runner", "--root", str(root),
                                    text, "--model", str(checkpoint), "--out", str(temporary), "--device", self.device], cwd=str(root), env=environment,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=self.timeout_seconds, check=False)
            if getattr(completed, "returncode", 1) != 0:
                raise VoiceTTSError("Yerel EMA-TTS ses üretimini tamamlayamadı.")
            try:
                with wave.open(str(temporary), "rb") as audio:
                    if audio.getnframes() <= 0 or audio.getframerate() != 48000:
                        raise wave.Error
            except (wave.Error, EOFError):
                raise VoiceTTSError("EMA-TTS geçerli 48 kHz WAV dosyası üretmedi.") from None
            temporary.replace(target)
            return target
        except (OSError, subprocess.SubprocessError) as error:
            raise VoiceTTSError("Yerel EMA-TTS işlemi başlatılamadı.") from error
        finally:
            temporary.unlink(missing_ok=True)
