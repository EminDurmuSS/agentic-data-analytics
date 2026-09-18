"""Run EMA-TTS with only VoxCPM's AudioVAE decoder loaded.

VoxCPM's package initializer imports its full CUDA-oriented TTS stack. EMA-TTS
needs only two Apache-2.0 AudioVAE source modules, so this shim registers those
modules without importing the package initializer.
"""
from __future__ import annotations

import argparse
from importlib import metadata, util
from pathlib import Path
import runpy
import sys
import types


def _load_module(name: str, path: Path):
    spec = util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("VoxCPM AudioVAE modülü yüklenemedi.")
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def register_audio_vae() -> None:
    """Provide the imported EMA classes without evaluating ``voxcpm.__init__``."""
    package_root = Path(metadata.distribution("voxcpm").locate_file("voxcpm/modules/audiovae"))
    if not (package_root / "audio_vae_v2.py").is_file():
        raise RuntimeError("VoxCPM AudioVAE kaynakları bulunamadı.")
    sys.modules["voxcpm"] = types.ModuleType("voxcpm")
    sys.modules["voxcpm.modules"] = types.ModuleType("voxcpm.modules")
    audio_vae = _load_module("voxcpm.modules.audiovae.audio_vae", package_root / "audio_vae.py")
    audio_vae_v2 = _load_module("voxcpm.modules.audiovae.audio_vae_v2", package_root / "audio_vae_v2.py")
    module = types.ModuleType("voxcpm.modules.audiovae")
    module.AudioVAE = audio_vae.AudioVAE
    module.AudioVAEConfig = audio_vae.AudioVAEConfig
    module.AudioVAEV2 = audio_vae_v2.AudioVAE
    module.AudioVAEConfigV2 = audio_vae_v2.AudioVAEConfig
    sys.modules["voxcpm.modules.audiovae"] = module


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", required=True)
    arguments, remaining = parser.parse_known_args()
    root = Path(arguments.root).resolve()
    if not (root / "inference.py").is_file():
        raise SystemExit("EMA-TTS inference.py bulunamadı.")
    register_audio_vae()
    sys.path.insert(0, str(root))
    sys.argv = [str(root / "inference.py"), *remaining]
    runpy.run_path(str(root / "inference.py"), run_name="__main__")


if __name__ == "__main__":
    main()
