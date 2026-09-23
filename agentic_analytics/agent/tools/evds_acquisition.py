"""Agent tool: acquire EVDS observations for a metadata_only series on demand.

Architecture decision (see agentic_analytics/lakehouse/registry.py and
data_pipeline/evds/acquisition.py for the runtime/write sides): this is an
explicit, agent-visible TOOL CALL, not a silent service-level interceptor on
LakehouseService's PlanError path. Reasons:

- LakehouseService's plan validation (agentic_analytics/lakehouse/service.py)
  is deliberately side-effect-free; a hidden network fetch inside it would
  violate that contract and risk request timeouts, since a real EVDS fetch
  can take tens of seconds to a minute.
- The codebase already has a proven pattern for durable, model-visible
  mutating tools (see FinancialImportTools.extra_tools() in
  agentic_analytics/agent/tools/financial_import.py); this tool follows the
  same extra_tools() shape without needing its optimistic-concurrency
  machinery, because acquisition never touches per-workspace store state --
  it only ever appends to the shared, workspace-independent on-demand
  overlay database.
- A tool call is an auditable step in the conversation; a transparent
  interceptor hides a network call and its EVDS rate-limit exposure from
  both the user and the agent's own reasoning about elapsed time.
"""
from __future__ import annotations

from agentic_analytics.agent.schemas import obj


def evds_acquisition_tools(acquire_evds_series, acquisition_error) -> dict:
    def handler(args):
        try:
            return acquire_evds_series(args["series_codes"], args["start_date"], args["end_date"])
        except acquisition_error as exc:
            return {"status": "blocked", "code": exc.code, "message": str(exc)}
        except (OSError, ValueError) as exc:
            return {"status": "blocked", "code": "ACQUISITION_FAILED", "message": str(exc)}

    parameters = obj({
        "series_codes": {
            "type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "description": "Exact EVDS series codes as they appear in discover/describe results or a PlanError's "
                           "source_code hint, e.g. TP.BKR.TRY.17. Codes not present in the EVDS catalog are rejected.",
        },
        "start_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "ISO date, inclusive."},
        "end_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "ISO date, inclusive."},
    }, ["series_codes", "start_date", "end_date"])

    return {"acquire_evds_series": {
        "schema": {"type": "function", "function": {
            "name": "acquire_evds_series",
            "description": (
                "Download and publish TCMB EVDS observations for series that discover, describe or "
                "validate_plan report as status=metadata_only (error code METADATA_ONLY, \"observations must be "
                "acquired first\"). Validates every code against the local EVDS catalog, fetches raw responses "
                "through the audited manifest downloader (request/response/SHA-256 lineage preserved), and merges "
                "the result into the on-demand overlay that every lakehouse tool call already reads -- the metric "
                "becomes queryable on your very next discover/describe/execute call in this same conversation, "
                "with no lakehouse rebuild and no restart. This performs a real network request and can take up "
                "to roughly a minute for a wide date range; call it only after a tool actually reported "
                "METADATA_ONLY for a series the user needs, not speculatively. After status=acquired, retry the "
                "exact call that previously failed."
            ),
            "parameters": parameters}},
        "handler": handler}}
