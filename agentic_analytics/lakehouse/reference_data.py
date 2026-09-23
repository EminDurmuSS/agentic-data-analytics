"""Loaders for small, finite, hand-curated reference lookup tables.

These are NOT time series and are NOT ingested through the EVDS/BDDK manifest
pipeline (`data_pipeline/evds/...`, `agentic_analytics/lakehouse/registry.py`).
They live in `data_pipeline/evidence/reference/` as plain JSON: a closed,
citation-bearing list of official decisions that changes at most once or
twice a year (e.g. minimum-wage commission decisions), not a queryable
observation series with a frequency/aggregation contract.

This module is a real, tested, importable building block. It is deliberately
NOT wired into `agentic_analytics.lakehouse.service.LakehouseService`'s
`discover`/`describe`/`execute` surface: that surface is built exclusively on
`catalog.metric_bindings` (see `registry.py::build_bindings`), which assumes a
physical table with a time column, a value column and a period-aggregation
policy. Forcing a finite decision history into that shape would misrepresent
it as a resamplable time series. See
`data_pipeline/evidence/reference/minimum_wage_notes.md` for the full
provenance record and the documented follow-up needed to expose this kind of
data through a dedicated reference-lookup agent tool.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MINIMUM_WAGE_DECISIONS_PATH = (
    PROJECT_ROOT / "data_pipeline" / "evidence" / "reference" / "minimum_wage_decisions.json"
)

_REQUIRED_FIELDS = {
    "decision_id", "effective_start", "effective_end", "net_monthly_try", "gross_monthly_try",
    "gross_daily_try", "currency", "decision_body", "decision_number", "decision_date",
    "resmi_gazete_date", "resmi_gazete_no", "resmi_gazete_url", "source_url", "verified_live",
    "verified_asof_utc", "source_note",
}


def load_minimum_wage_decisions(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the curated Turkish minimum-wage (asgari ücret) decision history.

    Each row is one Asgari Ücret Tespit Komisyonu decision period: an
    effective date range plus the net/gross monthly TRY amount and its
    Resmi Gazete citation. Rows are ordered by `effective_start`.
    """
    data_path = path or MINIMUM_WAGE_DECISIONS_PATH
    decisions = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(decisions, list) or not decisions:
        raise ValueError(f"{data_path} must contain a non-empty JSON array of decisions.")
    for row in decisions:
        missing = _REQUIRED_FIELDS - set(row)
        if missing:
            raise ValueError(f"Decision {row.get('decision_id')!r} is missing fields: {sorted(missing)}")
    return sorted(decisions, key=lambda row: row["effective_start"])


def minimum_wage_for_date(as_of: str, path: Path | None = None) -> dict[str, Any] | None:
    """Return the decision in effect on an ISO `YYYY-MM-DD` date, or None if before coverage starts."""
    applicable = [row for row in load_minimum_wage_decisions(path)
                  if row["effective_start"] <= as_of and (row["effective_end"] is None or as_of <= row["effective_end"])]
    if not applicable:
        return None
    return applicable[-1]
