#!/usr/bin/env python3
"""Plan and resume bounded EVDS collection with a durable SQLite job queue.

Planning is entirely local. Running uses the existing EVDS Python HTTP client,
one series per request, and writes an immutable directory for each attempt.
A successful request is distinct from complete numeric or calendar coverage.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.EVDS_Manifest_Indirme_Araci import (  # noqa: E402
    FREQUENCY_CODES,
    atomic_json,
    expected_periods,
    parse_period_label,
    parse_response,
    post_json,
    request_payload,
    sha256_bytes,
)

DEFAULT_DATABASE = ROOT / "tmp/evds_collection/queue.sqlite"
STATUSES = ("pending", "running", "succeeded", "retryable_failed", "permanent_failed", "no_data")
LOW_FREQUENCY = {"AYLIK", "ÜÇ AYLIK", "ALTI AYLIK", "YILLIK"}
ALLOWED_AGGREGATIONS = {"avg", "sum", "last", "first", "min", "max"}


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS series (
            series_code TEXT PRIMARY KEY, frequency TEXT NOT NULL,
            aggregation TEXT NOT NULL, is_archive INTEGER NOT NULL,
            metadata_json TEXT NOT NULL, coverage_json TEXT NOT NULL,
            physical_present INTEGER NOT NULL, numeric_present INTEGER NOT NULL,
            scope_status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, series_code TEXT NOT NULL REFERENCES series(series_code),
            frequency TEXT NOT NULL, aggregation TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','retryable_failed','permanent_failed','no_data')),
            attempts INTEGER NOT NULL DEFAULT 0, available_after REAL NOT NULL DEFAULT 0,
            lease_token TEXT, lease_until REAL, last_error TEXT,
            completion_origin TEXT, result_json TEXT, artifact_path TEXT,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, available_after, job_id);
        CREATE INDEX IF NOT EXISTS jobs_series ON jobs(series_code);
    """)
    return connection


def bounded_chunks(start: date, end: date, frequency: str) -> list[tuple[date, date]]:
    """Keep each request below 1000 possible native rows, including leap years."""
    if frequency not in FREQUENCY_CODES:
        raise ValueError(f"Unsupported EVDS frequency: {frequency}")
    if start > end:
        raise ValueError("Start date exceeds end date.")
    if frequency in LOW_FREQUENCY:
        # Long arbitrary CLI windows are still bounded to at most 120 monthly rows.
        span_days = 3650
    else:
        span_days = 365
    chunks = []
    cursor = start
    while cursor <= end:
        boundary = min(end, cursor + timedelta(days=span_days - 1))
        if frequency in LOW_FREQUENCY and boundary < end:
            # Split only at year boundaries, so no monthly/annual period is
            # requested twice across adjacent low-frequency jobs.
            boundary = date(boundary.year - 1, 12, 31)
        chunks.append((cursor, boundary))
        cursor = boundary + timedelta(days=1)
    return chunks


def _period_for_date(value: Any, frequency: str) -> tuple[str, str, str]:
    timestamp = pd.Timestamp(value)
    if frequency == "AYLIK":
        period = timestamp.to_period("M")
    elif frequency == "ÜÇ AYLIK":
        period = timestamp.to_period("Q")
    elif frequency == "YILLIK":
        period = timestamp.to_period("Y")
    else:
        return timestamp.date().isoformat(), timestamp.date().isoformat(), timestamp.date().isoformat()
    code = str(period) if frequency != "ÜÇ AYLIK" else f"{period.year}-Q{period.quarter}"
    return code, period.start_time.date().isoformat(), period.end_time.date().isoformat()


def discover_existing(data_root: Path, metadata: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Union physical observations without letting a duplicate snapshot win silently."""
    paths = sorted((data_root / "evds").glob("*/observations_long.parquet"))
    legacy = data_root / "processed/observations_native.parquet"
    if legacy.exists():
        paths.append(legacy)
    result: dict[str, dict[str, Any]] = {}
    request_cache: dict[tuple[Path, str], dict[str, Any] | None] = {}
    response_cache: dict[tuple[Path, str], bool] = {}
    for path in paths:
        frame = pd.read_parquet(path)
        for code, group in frame.groupby("series_code", sort=False):
            if code not in metadata:
                continue
            frequency = str(metadata[code]["frequency"])
            item = result.setdefault(code, {"periods": {}, "sources": set(), "request_intervals": []})
            item["sources"].add(str(path.resolve()))
            for row in group.to_dict("records"):
                if "period_start" in row:
                    period, start, end = str(row["period"]), str(row["period_start"]), str(row["period_end"])
                else:
                    period, start, end = _period_for_date(row["observation_date"], frequency)
                value = None if pd.isna(row["value"]) else float(row["value"])
                existing = item["periods"].setdefault(period, {"start": start, "end": end, "values": set()})
                existing["values"].add(value)
            if "source_request_file" not in group:
                continue
            required = ["source_request_file", "source_request_sha256", "source_response_file", "source_response_sha256"]
            if not set(required).issubset(group.columns):
                continue
            requests = group[required].drop_duplicates()
            for source in requests.itertuples(index=False):
                request_path = (path.parent / str(source.source_request_file)).resolve()
                request_key = (request_path, str(source.source_request_sha256))
                if request_key not in request_cache:
                    request_cache[request_key] = None
                    if request_path.exists():
                        raw = request_path.read_bytes()
                        try:
                            parsed_request = json.loads(raw)
                            canonical = json.dumps(parsed_request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            if str(source.source_request_sha256) in {sha256_bytes(raw), sha256_bytes(canonical)}:
                                request_cache[request_key] = parsed_request
                        except (ValueError, UnicodeDecodeError):
                            pass
                response_path = (path.parent / str(source.source_response_file)).resolve()
                response_key = (response_path, str(source.source_response_sha256))
                if response_key not in response_cache:
                    response_cache[response_key] = False
                    if response_path.exists():
                        try:
                            raw_response = response_path.read_bytes()
                            if response_path.suffix == ".gz":
                                raw_response = gzip.decompress(raw_response)
                            response_cache[response_key] = sha256_bytes(raw_response) == str(source.source_response_sha256)
                        except (OSError, EOFError):
                            pass
                request = request_cache[request_key]
                if request and response_cache[response_key] and request.get("series") == code and str(request.get("frequency")) == FREQUENCY_CODES.get(frequency):
                    requested_start = datetime.strptime(request["startDate"], "%d-%m-%Y").date().isoformat()
                    requested_end = datetime.strptime(request["endDate"], "%d-%m-%Y").date().isoformat()
                    try:
                        response_bytes = response_path.read_bytes()
                        if response_path.suffix == ".gz":
                            response_bytes = gzip.decompress(response_bytes)
                        validate_response(response_bytes, {
                            "series_code": code, "frequency": frequency,
                            "start_date": requested_start, "end_date": requested_end,
                        })
                    except (ValueError, KeyError, OSError, EOFError):
                        continue
                    item["request_intervals"].append({
                        "start": requested_start,
                        "end": requested_end,
                        "aggregation": request.get("aggregationTypes"),
                        "request_file": str(request_path),
                    })
    return result


def coverage_summary(existing: dict[str, Any] | None, start: date, end: date, frequency: str) -> dict[str, Any]:
    periods = (existing or {}).get("periods", {})
    selected = {key: value for key, value in periods.items() if value["end"] >= start.isoformat() and value["start"] <= end.isoformat()}
    numeric = {key: value for key, value in selected.items() if any(number is not None for number in value["values"])}
    conflicts = sum(len({number for number in value["values"] if number is not None}) > 1 for value in selected.values())
    expected = {period for period, _, _ in expected_periods(start, end, frequency)}
    missing_rows = sorted(expected - set(selected)) if expected else None
    missing_numeric = sorted(expected - set(numeric)) if expected else None
    return {
        "target_start": start.isoformat(), "target_end": end.isoformat(),
        "physical_present": bool(periods), "target_observation_count": len(selected),
        "target_numeric_count": len(numeric), "conflicting_numeric_period_count": conflicts,
        "observed_start": min((value["start"] for value in numeric.values()), default=None),
        "observed_end": max((value["end"] for value in numeric.values()), default=None),
        "expected_period_count": len(expected) if expected else None,
        "missing_returned_periods": missing_rows, "missing_numeric_periods": missing_numeric,
        "coverage_status": "metadata_only" if not periods else "no_target_numeric_data" if not numeric else "conflicting_snapshots" if conflicts else "calendar_unverified" if not expected else "complete_numeric_periods" if not missing_numeric else "partial_numeric_periods",
        "sources": sorted((existing or {}).get("sources", [])),
    }


def plan(database: Path, catalog_path: Path, data_root: Path, start: date, end: date) -> dict[str, Any]:
    if start > end:
        raise ValueError("Start date exceeds end date.")
    catalog = pd.read_parquet(catalog_path)
    if catalog["series_code"].duplicated().any():
        raise ValueError("Duplicate series codes in EVDS metadata.")
    metadata = {str(row["series_code"]): row for row in catalog.to_dict("records")}
    existing = discover_existing(data_root, metadata)
    settings = {"schema_version": "1", "catalog_sha256": sha256_bytes(catalog_path.read_bytes()), "target_start": start.isoformat(), "target_end": end.isoformat()}
    connection = connect(database)
    try:
        with connection:
            saved = dict(connection.execute("SELECT key,value FROM configuration"))
            if saved and saved != settings:
                raise ValueError("Queue belongs to another catalog snapshot or target window; use a new database.")
            connection.executemany("INSERT OR IGNORE INTO configuration VALUES (?,?)", settings.items())
            for code, record in metadata.items():
                frequency = str(record["frequency"])
                aggregation = str(record.get("default_aggregation", ""))
                coverage = coverage_summary(existing.get(code), start, end, frequency)
                scoped_start, scoped_end = start, end
                # Only explicitly verified lifespan metadata can exclude history.
                # The current catalog has no such fields, so archives are included.
                if record.get("lifespan_verified") is True:
                    if record.get("lifespan_start"):
                        scoped_start = max(start, date.fromisoformat(record["lifespan_start"]))
                    if record.get("lifespan_end"):
                        scoped_end = min(end, date.fromisoformat(record["lifespan_end"]))
                scope_status = "outside_verified_lifespan" if scoped_start > scoped_end else "requested"
                clean_metadata = {key: None if isinstance(value, float) and math.isnan(value) else value for key, value in record.items()}
                connection.execute("""INSERT INTO series VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(series_code) DO UPDATE SET coverage_json=excluded.coverage_json,
                    physical_present=excluded.physical_present,numeric_present=excluded.numeric_present""", (
                    code, frequency, aggregation, int(bool(record.get("is_archive", False))),
                    json.dumps(clean_metadata, ensure_ascii=False), json.dumps(coverage, ensure_ascii=False),
                    int(coverage["physical_present"]), int(coverage["target_numeric_count"] > 0), scope_status,
                ))
                if scope_status != "requested":
                    continue
                supported = frequency in FREQUENCY_CODES and aggregation in ALLOWED_AGGREGATIONS
                chunks = bounded_chunks(scoped_start, scoped_end, frequency) if supported else [(scoped_start, scoped_end)]
                for chunk_start, chunk_end in chunks:
                    key = f"v1|{code}|{frequency}|{aggregation}|{chunk_start}|{chunk_end}"
                    job_id = hashlib.sha256(key.encode()).hexdigest()[:32]
                    status, origin, error = "pending", None, None
                    chunk_coverage = coverage_summary(existing.get(code), chunk_start, chunk_end, frequency)
                    if not supported:
                        status, error = "permanent_failed", "Unsupported frequency or native aggregation in metadata."
                    elif not chunk_coverage["conflicting_numeric_period_count"]:
                        low_complete = chunk_coverage["missing_returned_periods"] == []
                        requested = any(
                            interval["start"] <= chunk_start.isoformat() and interval["end"] >= chunk_end.isoformat()
                            and interval["aggregation"] == aggregation
                            for interval in existing.get(code, {}).get("request_intervals", [])
                        )
                        if low_complete or requested:
                            status = "succeeded" if chunk_coverage["target_numeric_count"] else "no_data"
                            origin = "existing_snapshot"
                    connection.execute("""INSERT INTO jobs
                        (job_id,series_code,frequency,aggregation,start_date,end_date,status,last_error,completion_origin,result_json,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
                        completion_origin=excluded.completion_origin,result_json=excluded.result_json,updated_at=excluded.updated_at
                        WHERE jobs.attempts=0 AND (
                            (jobs.status='pending' AND excluded.completion_origin='existing_snapshot')
                            OR jobs.completion_origin='existing_snapshot')""", (
                        job_id, code, frequency, aggregation, chunk_start.isoformat(), chunk_end.isoformat(), status,
                        error, origin, json.dumps(chunk_coverage, ensure_ascii=False), time.time(),
                    ))
    finally:
        connection.close()
    return status_report(database)


def claim_job(database: Path, *, now: float | None = None, lease_seconds: float = 180, max_attempts: int = 3) -> dict[str, Any] | None:
    now = time.time() if now is None else now
    connection = connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""UPDATE jobs SET status=CASE WHEN attempts>=? THEN 'permanent_failed' ELSE 'retryable_failed' END,
            lease_token=NULL,lease_until=NULL,last_error='Worker lease expired; recovered for retry.',updated_at=?
            WHERE status='running' AND lease_until<=?""", (max_attempts, now, now))
        row = connection.execute("""SELECT * FROM jobs WHERE status IN ('pending','retryable_failed')
            AND available_after<=? AND attempts<? ORDER BY attempts,job_id LIMIT 1""", (now, max_attempts)).fetchone()
        if row is None:
            connection.commit()
            return None
        token = uuid.uuid4().hex
        connection.execute("""UPDATE jobs SET status='running',attempts=attempts+1,
            lease_token=?,lease_until=?,updated_at=? WHERE job_id=?""", (token, now + lease_seconds, now, row["job_id"]))
        result = dict(connection.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())
        connection.commit()
        return result
    finally:
        connection.close()


def finish_job(database: Path, job: dict[str, Any], status: str, *, result: dict[str, Any] | None = None, error: str | None = None, artifact_path: Path | None = None, available_after: float = 0) -> None:
    if status not in set(STATUSES) - {"pending", "running"}:
        raise ValueError("Invalid completion status.")
    connection = connect(database)
    try:
        with connection:
            cursor = connection.execute("""UPDATE jobs SET status=?,result_json=?,last_error=?,artifact_path=?,
                completion_origin='queue_request',available_after=?,lease_token=NULL,lease_until=NULL,updated_at=?
                WHERE job_id=? AND status='running' AND lease_token=?""", (
                status, json.dumps(result, ensure_ascii=False), error, str(artifact_path) if artifact_path else None,
                available_after, time.time(), job["job_id"], job["lease_token"],
            ))
            if cursor.rowcount != 1:
                raise ValueError("Lease ownership changed; refusing to overwrite another worker's result.")
    finally:
        connection.close()


def validate_response(raw: bytes, job: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start, end = date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"])
    rows, info = parse_response(raw, job["series_code"], job["frequency"], start, end)
    payload = json.loads(raw.decode("utf-8-sig"))
    boundary_dates = []
    if info["outside_requested_range_rows"]:
        # Actual EVDS weekly responses include the following native weekday
        # when endDate falls inside that week. Exclude and audit only that one
        # boundary row; distant dates and lower-bound spillovers remain errors.
        weekday = {"HAFTALIK(CUMA)": 4, "HAFTALIK(ÇARŞAMBA)": 2}.get(job["frequency"])
        for item in payload["items"]:
            _, period_start, period_end = parse_period_label(str(item["Tarih"]), job["frequency"])
            if period_end < start or period_start > end:
                if weekday is None or not (0 < (period_start - end).days <= 6) or period_start.weekday() != weekday:
                    raise ValueError("Response contains dates outside the requested range.")
                boundary_dates.append(period_start.isoformat())
        if len(boundary_dates) != 1:
            raise ValueError("Response contains more than one weekly end-boundary row.")
    if info["response_total_count"] >= 1000:
        raise ValueError("Response reached the 1000-row truncation boundary despite bounded request.")
    if len({row["period"] for row in rows}) != len(rows):
        raise ValueError("Response contains duplicate native periods.")
    if any(row["value"] is not None and not math.isfinite(row["value"]) for row in rows):
        raise ValueError("Response contains non-finite numeric values.")
    names = payload.get("seriesNames")
    if isinstance(names, dict) and names and set(names) != {job["series_code"].replace(".", "_")}:
        raise ValueError("Response series identity differs from the requested series.")
    inventory = {"periods": {row["period"]: {"start": row["period_start"], "end": row["period_end"], "values": {row["value"]}} for row in rows}}
    coverage = coverage_summary(inventory, start, end, job["frequency"])
    coverage.update(info)
    coverage["excluded_weekly_boundary_periods"] = boundary_dates
    coverage["physical_present"] = bool(rows)
    coverage["completion_means"] = "Validated response to this bounded request; numeric and calendar completeness are reported separately."
    return rows, coverage


def run_jobs(database: Path, output: Path, *, max_jobs: int = 1, timeout: int = 45, max_attempts: int = 3, retry_base_seconds: float = 30, min_interval_seconds: float = 1, fetch: Callable[..., tuple[bytes, dict[str, Any]]] = post_json) -> dict[str, Any]:
    if max_jobs < 1 or timeout < 1 or max_attempts < 1 or retry_base_seconds < 0 or min_interval_seconds < 0:
        raise ValueError("Job/timeout/attempt limits must be positive and delays non-negative.")
    processed = []
    for index in range(max_jobs):
        job = claim_job(database, lease_seconds=timeout * 2 + 60, max_attempts=max_attempts)
        if job is None:
            break
        attempt = output / job["job_id"] / f"attempt-{job['attempts']:03d}-{job['lease_token'][:12]}"
        attempt.mkdir(parents=True, exist_ok=False)
        status, result, error = "retryable_failed", None, None
        permanent = False
        try:
            payload = request_payload(job["series_code"], job["aggregation"], job["frequency"], date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"]))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            (attempt / "request.json").write_bytes(body)
            raw, http = fetch(body, timeout, "urllib")
            (attempt / "response.json.gz").write_bytes(gzip.compress(raw, mtime=0))
            http = {**http, "sha256": sha256_bytes(raw), "request_sha256": sha256_bytes(body), "transport": "urllib"}
            atomic_json(attempt / "response_info.json", http)
            http_status = int(http["http_status"])
            if not 200 <= http_status < 300:
                permanent = http_status in {400, 404, 405, 410, 422}
                raise ValueError(f"EVDS HTTP status {http_status}.")
            rows, result = validate_response(raw, job)
            for row in rows:
                row.update(native_frequency=job["frequency"], source_request_file=str((attempt / "request.json").resolve()), source_request_sha256=sha256_bytes(body), source_response_file=str((attempt / "response.json.gz").resolve()), source_response_sha256=sha256_bytes(raw))
                row["missing_kind"] = "observed" if row["value"] is not None else "source_null_unresolved"
                row["is_unresolved_missing"] = row["value"] is None
            if rows:
                pd.DataFrame(rows).to_parquet(attempt / "observations.parquet", index=False)
            status = "succeeded" if result["target_numeric_count"] else "no_data"
            atomic_json(attempt / "validation.json", {"status": status, **result})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            status = "permanent_failed" if permanent or job["attempts"] >= max_attempts else "retryable_failed"
            atomic_json(attempt / "failure.json", {"status": status, "error": error})
        retry_at = time.time() + retry_base_seconds * 2 ** (job["attempts"] - 1) if status == "retryable_failed" else 0
        finish_job(database, job, status, result=result, error=error, artifact_path=attempt.resolve(), available_after=retry_at)
        processed.append({"job_id": job["job_id"], "series_code": job["series_code"], "status": status})
        if index + 1 < max_jobs and min_interval_seconds:
            time.sleep(min_interval_seconds)
    return {"processed": processed, "queue": status_report(database)}


def status_report(database: Path) -> dict[str, Any]:
    if not database.exists():
        raise ValueError("Queue does not exist; run plan first.")
    connection = connect(database)
    try:
        series = connection.execute("SELECT COUNT(*),COALESCE(SUM(physical_present),0),COALESCE(SUM(numeric_present),0),COALESCE(SUM(is_archive),0) FROM series").fetchone()
        statuses = {status: 0 for status in STATUSES}
        statuses.update(dict(connection.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")))
        coverage = dict(connection.execute("SELECT json_extract(coverage_json,'$.coverage_status'),COUNT(*) FROM series GROUP BY 1"))
        newly_collected = connection.execute("SELECT COUNT(DISTINCT series_code) FROM jobs WHERE status='succeeded' AND completion_origin='queue_request'").fetchone()[0]
        return {
            "database": str(database.resolve()), "metadata_series": series[0],
            "existing_physical_series": series[1], "existing_target_numeric_series": series[2],
            "archive_series": series[3], "existing_coverage_status": coverage,
            "newly_collected_numeric_series": newly_collected,
            "jobs": statuses, "job_count": sum(statuses.values()),
            "completed_requests_are_not_complete_numeric_coverage": True,
            "configuration": dict(connection.execute("SELECT key,value FROM configuration")),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "run"):
        child = commands.add_parser(name)
        child.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
        if name == "plan":
            child.add_argument("--catalog", type=Path, default=ROOT / "data_pipeline/catalog/evds_series_catalog.parquet")
            child.add_argument("--data-root", type=Path, default=ROOT / "data_pipeline")
            child.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
            child.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
        elif name == "run":
            child.add_argument("--output", type=Path)
            child.add_argument("--max-jobs", type=int, default=1)
            child.add_argument("--timeout", type=int, default=45)
            child.add_argument("--max-attempts", type=int, default=3)
            child.add_argument("--retry-base-seconds", type=float, default=30)
            child.add_argument("--min-interval-seconds", type=float, default=1)
    args = parser.parse_args()
    if args.command == "plan":
        result = plan(args.database, args.catalog, args.data_root, args.start, args.end)
    elif args.command == "run":
        if not args.database.exists():
            parser.error("Queue does not exist; run plan first.")
        result = run_jobs(args.database, args.output or args.database.parent / "artifacts", max_jobs=args.max_jobs, timeout=args.timeout, max_attempts=args.max_attempts, retry_base_seconds=args.retry_base_seconds, min_interval_seconds=args.min_interval_seconds)
    else:
        result = status_report(args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
