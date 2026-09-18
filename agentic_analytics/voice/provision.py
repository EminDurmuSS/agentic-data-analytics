"""Download the pinned EMA-TTS package once, outside request handling."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from huggingface_hub import hf_hub_download, snapshot_download


MODEL_REPOSITORY = "canberkkkkkk/ema-tts"
CODEC_REPOSITORY = "openbmb/VoxCPM2"
CODEC_FILENAME = "audiovae.pth"
REQUIRED_FILES = ("inference.py", "model.py", "text.py", "ckpt/config.json", "ckpt/model.safetensors")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _existing_manifest(target: Path, hub_cache: Path) -> dict[str, object] | None:
    """Return the prior installation only when both model and codec are usable."""
    manifest_path = target / "PROVISIONED.json"
    if not all((target / name).is_file() for name in REQUIRED_FILES):
        return None
    if not any(hub_cache.rglob(CODEC_FILENAME)):
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("model_repository") != MODEL_REPOSITORY or manifest.get("codec_repository") != CODEC_REPOSITORY:
        return None
    return manifest


def provision(model_dir: str | Path, *, cache_dir: str | Path | None = None, download=snapshot_download, codec_download=hf_hub_download) -> dict[str, object]:
    """Materialise code, model, and codec in a volume before serving requests."""
    target = Path(model_dir).resolve()
    cache = Path(cache_dir or os.environ.get("HF_HOME", target.parent)).resolve()
    hub_cache = cache / "hub"
    target.parent.mkdir(parents=True, exist_ok=True)
    hub_cache.mkdir(parents=True, exist_ok=True)
    existing = _existing_manifest(target, hub_cache)
    if existing is not None:
        return existing
    with tempfile.TemporaryDirectory(prefix="ema-provision-", dir=target.parent) as temporary:
        staged = Path(temporary) / "ema-tts"
        download(MODEL_REPOSITORY, local_dir=str(staged), local_dir_use_symlinks=False)
        missing = [name for name in REQUIRED_FILES if not (staged / name).is_file()]
        if missing:
            raise RuntimeError("EMA-TTS paketi eksik dosya içeriyor: " + ", ".join(missing))
        codec_path = Path(codec_download(CODEC_REPOSITORY, CODEC_FILENAME, cache_dir=str(hub_cache)))
        if not codec_path.is_file() or codec_path.stat().st_size == 0:
            raise RuntimeError("EMA-TTS AudioVAE ağırlığı indirilemedi.")
        if target.exists():
            shutil.rmtree(target)
        staged.replace(target)
    manifest = {
        "model_repository": MODEL_REPOSITORY,
        "codec_repository": CODEC_REPOSITORY,
        "codec_filename": CODEC_FILENAME,
        "model_sha256": _sha256(target / "ckpt/model.safetensors"),
        "codec_sha256": _sha256(codec_path),
    }
    (target / "PROVISIONED.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    model_dir = os.environ.get("VOICE_EMA_MODEL")
    if not model_dir:
        raise SystemExit("VOICE_EMA_MODEL tanımlanmalı.")
    print(json.dumps(provision(model_dir), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
