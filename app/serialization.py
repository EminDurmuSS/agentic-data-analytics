"""JSON persistence and lossless values at the browser boundary."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from agentic_analytics.lakehouse.service import _json


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(_json(value), ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def browser_json(value):
    """Keep integers beyond JavaScript's exact range lossless across the UI API."""
    def encode(item):
        if isinstance(item, int) and not isinstance(item, bool) and abs(item) > 2**53 - 1:
            return {"$integer": str(item)}
        if isinstance(item, dict):
            return {key: encode(child) for key, child in item.items()}
        if isinstance(item, list):
            return [encode(child) for child in item]
        return item
    return encode(_json(value))
