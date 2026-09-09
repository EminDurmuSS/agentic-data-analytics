#!/usr/bin/env python3
"""Create a SHA-256 inventory for all stable files in data_pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "FILE_SHA256.json"


def included(path: Path, base_dir: Path = BASE_DIR) -> bool:
    relative = path.relative_to(base_dir)
    if relative.as_posix() == "FILE_SHA256.json":
        return False
    if any(part in {".DS_Store", "__pycache__", ".ipynb_checkpoints", "tmp", "temp"}
           for part in relative.parts):
        return False
    # Match the local downloader artifacts excluded by the repository's
    # .gitignore. Only the merged weekly_all_groups snapshot is distributed.
    if (len(relative.parts) > 1 and relative.parts[0] == "bddk"
            and relative.parts[1].startswith("weekly_group_")):
        return False
    if path.name.endswith("_Paylas.zip"):
        return False
    if path.suffix in {".pyc", ".tmp"}:
        return False
    return path.is_file()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(base_dir: Path = BASE_DIR) -> dict[str, str]:
    output_path = base_dir / "FILE_SHA256.json"
    manifest = {
        path.relative_to(base_dir).as_posix(): sha256(path)
        for path in sorted(base_dir.rglob("*"))
        if included(path, base_dir)
    }
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return manifest


if __name__ == "__main__":
    result = build()
    print(f"Saved {len(result)} SHA-256 entries to {OUTPUT_PATH}")
