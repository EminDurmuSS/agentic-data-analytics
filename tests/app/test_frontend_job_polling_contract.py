"""Regression contract for long-running analysis polling in the browser."""
from __future__ import annotations

import re
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "app" / "static" / "app.js"


def test_frontend_poll_window_outlives_backend_analysis_budget():
    source = SCRIPT.read_text(encoding="utf-8")
    interval = re.search(r"const JOB_POLL_INTERVAL_MS = (\d+);", source)
    attempts = re.search(r"const MAX_JOB_POLL_ATTEMPTS = (\d+);", source)

    assert interval and attempts
    poll_window_ms = int(interval.group(1)) * int(attempts.group(1))
    assert poll_window_ms >= 16 * 60 * 1000
    assert source.count("i < MAX_JOB_POLL_ATTEMPTS") == 2
    assert "i < 480" not in source
