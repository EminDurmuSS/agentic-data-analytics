"""Read-only display projections for historical, completed analysis runs."""
from __future__ import annotations

import duckdb

from app.activity import public_run
from agentic_analytics.agent.delivery import (
    _analysis_confirmation, _cell_confirmation, _scope_confirmation,
    _statistics_confirmation,
)


def present_run(store, run):
    """Refresh saved numerical prose without altering the journal or its result.

    Refusals, unfinished work, web research, and clarification questions retain
    their original messages. A display projection never upgrades run status.
    """
    public = public_run(run)
    result, state = run.get("result") or {}, run.get("state") or {}
    if (run.get("status") != "completed" or result.get("status") != "completed"
            or not (state.get("analysis_updated") or state.get("analysis_observed"))
            or not state.get("analysis_id") or result.get("errors")
            or (state.get("chart_updated") and not state.get("analysis_updated"))
            or "Devam için soru:" in result.get("message", "")
            or any(item.get("tool") == "research_web" for item in state.get("tool_results", []))):
        return public
    try:
        parts = [render(store, run["workspace_id"], state) for render in (
            _analysis_confirmation, _statistics_confirmation,
            _cell_confirmation, _scope_confirmation,
        )]
    except (ValueError, OSError, duckdb.Error):
        # Missing or changed historical artifacts cannot support fresh prose.
        return public
    message = "\n\n".join(part for part in parts if part)
    if message:
        public["result"] = {**result, "display_message": message}
    return public
