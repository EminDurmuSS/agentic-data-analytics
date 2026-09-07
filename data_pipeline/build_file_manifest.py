#!/usr/bin/env python3
"""Create a SHA-256 inventory for all stable files in data_pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "FILE_SHA256.json"


def included(path: Path) -> bool:
    relative = path.relative_to(BASE_DIR)
    if path == OUTPUT_PATH:
        return False
    if ".DS_Store" in relative.parts or "__pycache__" in relative.parts:
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


def build() -> dict[str, str]:
    manifest = {
        path.relative_to(BASE_DIR).as_posix(): sha256(path)
        for path in sorted(BASE_DIR.rglob("*"))
        if included(path)
    }
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT_PATH)
    return manifest


if __name__ == "__main__":
    result = build()
    print(f"Saved {len(result)} SHA-256 entries to {OUTPUT_PATH}")
