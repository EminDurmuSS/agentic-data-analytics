"""Tests for the Compose init-time EMA-TTS model provisioner."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agentic_analytics.voice import provision as voice_provision


class ProvisionTests(unittest.TestCase):
    def test_existing_valid_installation_skips_network_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ema-tts"
            calls: list[str] = []

            def download(_repository, *, local_dir, **_kwargs):
                calls.append("model")
                staged = Path(local_dir)
                (staged / "ckpt").mkdir(parents=True)
                for filename in ("inference.py", "model.py", "text.py", "ckpt/config.json", "ckpt/model.safetensors"):
                    (staged / filename).write_bytes(b"fixture")

            def codec_download(_repository, filename, *, cache_dir):
                calls.append("codec")
                codec = Path(cache_dir) / "models--codec" / "snapshots" / "fixture" / filename
                codec.parent.mkdir(parents=True)
                codec.write_bytes(b"codec")
                return str(codec)

            first = voice_provision.provision(target, cache_dir=root, download=download, codec_download=codec_download)
            second = voice_provision.provision(
                target,
                cache_dir=root,
                download=lambda *_args, **_kwargs: self.fail("model download should be skipped"),
                codec_download=lambda *_args, **_kwargs: self.fail("codec download should be skipped"),
            )

            self.assertEqual(calls, ["model", "codec"])
            self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
