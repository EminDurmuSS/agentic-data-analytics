"""Local entry point for the typed lakehouse tools.

Run with ``python -m agentic_analytics.lakehouse.cli --help`` from the repository root.
Paths are operator configuration; the JSON tool language never accepts paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from agentic_analytics.lakehouse.service import LakehouseService, PlanError, error_envelope
from agentic_analytics.lakehouse.store import LakehouseStore

from agentic_analytics.paths import REPO_ROOT as ROOT


def demo(service: LakehouseService) -> dict:
    """Exercise actual KOBI data, a revision and source-cell explanation."""
    plan = {
        "start": "2021-01", "end": "2026-06", "frequency": "monthly",
        "columns": [{"name": "sme_credit", "metric_id": "bddk_monthly:table06:1:38c9ed21984d:NakdiKrediToplam", "dimensions": {"group_code": 10001}}],
        "operations": [{"op": "growth", "column": "sme_credit", "output": "nominal_yoy_pct", "periods": 12}],
    }
    first = service.execute(plan)
    revised = service.revise_analysis({
        "analysis_id": first["analysis_id"],
        "add_columns": [{"name": "cpi", "metric_id": "evds:TP.TUKFIY2025.GENEL"}],
        "operations": [
            {"op": "deflate", "column": "sme_credit", "index": "cpi", "base_period": "2021-01", "output": "real_sme_credit"},
            {"op": "growth", "column": "real_sme_credit", "output": "real_yoy_pct", "periods": 12},
        ],
    })
    proof = service.explain_value({"analysis_id": revised["analysis_id"], "column": "real_yoy_pct", "period": "2026-06"})
    return {"status": "ok", "first": first, "revised": revised, "explanation": proof}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=ROOT / ".lakehouse-runtime")
    parser.add_argument("--workspace", help="Workspace identifier returned by init")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Publish a verified local snapshot and create a workspace")
    init.add_argument("--database", type=Path, default=ROOT / "data_pipeline/lakehouse/analytics.duckdb")
    for command in ("discover", "describe", "validate_plan", "execute", "revise_analysis", "explain_value"):
        child = sub.add_parser(command)
        child.add_argument("--request", required=True, type=Path, help="UTF-8 JSON request file")
    sub.add_parser("demo", help="Run the KOBI growth, real-value revision and lineage scenario")
    args = parser.parse_args(argv)
    try:
        store = LakehouseStore(args.store)
        if args.command == "init":
            from agentic_analytics.lakehouse.quality import validate_database

            snapshot = store.publish_snapshot(args.database, validator=validate_database)
            result = store.create_workspace(snapshot["snapshot_id"])
        else:
            if not args.workspace:
                parser.error("--workspace is required for this command")
            service = LakehouseService(store, args.workspace)
            if args.command == "demo":
                result = demo(service)
            else:
                request = json.loads(args.request.read_text(encoding="utf-8"))
                result = getattr(service, args.command)(request)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result.get("status") != "blocked" else 2
    except (PlanError, ValueError, OSError, duckdb.Error) as exc:
        print(json.dumps(error_envelope(exc), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
