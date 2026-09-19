#!/usr/bin/env python3
"""Resume EVDS acquisition and publish a verified database for new workspaces.

Run with --publish-only after a separately supervised collector has drained.
The checked-in seed catalogs and previous workspace snapshots are preserved.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def refresh(database: Path, *, output: Path, runtime_root: Path) -> dict:
    """Publish only when every planned request has a validated terminal result."""
    from tools.evds_bulk_collection import status_report, atomic_json
    from tools.publish_evds_bulk import publish
    from data_pipeline.catalog.build_unified_catalog import build as build_catalog
    from data_pipeline.lakehouse.build_lakehouse import build as build_lakehouse
    from agentic_analytics.lakehouse.store import file_sha256

    status = status_report(database)
    if (status["configuration"].get("target_start"), status["configuration"].get("target_end")) != ("2021-01-01", "2026-06-30"):
        raise ValueError("Hackathon acquisition must cover 2021-01-01 through 2026-06-30")
    unresolved = {key: status["jobs"][key] for key in
                  ("pending", "running", "retryable_failed", "permanent_failed") if status["jobs"][key]}
    if unresolved or status["configuration"].get("selection_kind") != "full_catalog":
        raise ValueError(f"Full EVDS acquisition is not complete: {unresolved or 'filtered catalog scope'}")
    publication = publish(database=database)
    if not publication["validation"]["full_request_scope_complete"]:
        raise ValueError("Published request intervals do not cover the complete catalog target scope")
    emit({"event": "source_published", **publication})
    build_root = runtime_root.resolve() / publication["publication_id"]
    catalog_dir = build_root / "catalog"
    catalog = build_catalog(catalog_dir, include_full_catalog=True)
    if catalog.get("evds_full_catalog", {}).get("publication_id") != publication["publication_id"]:
        raise ValueError("The catalog resolved a different concurrently published EVDS release")
    emit({"event": "catalog_built", "queryable_metric_count": catalog["queryable_metric_count"]})
    staged_db = build_root / "database" / "analytics.duckdb"
    build_lakehouse(staged_db, catalog_dir=catalog_dir)
    source_hash = file_sha256(staged_db)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        # Copy beside the destination, verify and close before atomic replace.
        # The application selects the new release on its next workspace open.
        with staged_db.open("rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if file_sha256(temporary) != source_hash:
            raise ValueError("Serving database copy failed its SHA256 check")
        wal = Path(str(output) + ".wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("Serving database has an active WAL; close its writer before publication")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    report = {"status": "completed", "acquisition_scope_complete": True,
              "publication_id": publication["publication_id"],
              "source_validation": publication["validation"],
              "serving_database": str(output), "serving_database_sha256": source_hash,
              "catalog_directory": str(catalog_dir), "build_report": str(staged_db.parent / "validation.json"),
              "serving_database_bytes": output.stat().st_size,
              "workspace_policy": "New workspaces use the new database; existing workspaces keep their snapshots.",
              "numeric_coverage_is_separate": True}
    atomic_json(build_root / "completion.json", report)
    atomic_json(runtime_root / "LATEST.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "tmp/evds_bulk/queue.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data_pipeline/lakehouse/analytics.duckdb")
    parser.add_argument("--runtime-root", type=Path, default=ROOT / ".lakehouse-runtime/evds-builds")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--min-interval-seconds", type=float, default=1)
    parser.add_argument("--max-seconds", type=float, default=14400)
    args = parser.parse_args()
    from tools.evds_bulk_collection import plan, run_jobs
    try:
        if not args.publish_only:
            planned = plan(args.database, ROOT / "data_pipeline/catalog/evds_series_catalog.parquet",
                           date(2021, 1, 1), date(2026, 6, 30), batch_size=args.batch_size)
            emit({"event": "planned", **planned})
            collected = run_jobs(args.database, drain=True, timeout=args.timeout,
                                 min_interval_seconds=args.min_interval_seconds,
                                 max_seconds=args.max_seconds, progress=emit)
            emit({"event": "collection_finished", **collected})
        emit(refresh(args.database, output=args.output, runtime_root=args.runtime_root))
        return 0
    except KeyboardInterrupt:
        emit({"status": "interrupted", "message": "Queue and published workspaces are retained; rerun to resume."})
        return 130
    except Exception as exc:
        emit({"status": "incomplete", "error_type": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
