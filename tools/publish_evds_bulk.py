#!/usr/bin/env python3
"""Publish a verified, portable EVDS bulk snapshot without making network calls.

The SQLite queue is read in one transaction. Each completed response is replayed
and compared with its Parquet artifact before any observation is accepted.
Conflicting vintages remain available as evidence and are excluded from the
canonical table. A release becomes visible only through an atomic CURRENT.json.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.EVDS_Manifest_Indirme_Araci import expected_periods

DEFAULT_OUTPUT = ROOT / "data_pipeline/evds/full_catalog"
DEFAULT_DATABASE = ROOT / "tmp/evds_bulk/queue.sqlite"
DEFAULT_CATALOG = ROOT / "data_pipeline/catalog/evds_series_catalog.parquet"
TERMINAL = {"succeeded", "no_data"}
QUEUE_STATES = TERMINAL | {"pending", "running", "retryable_failed", "permanent_failed", "split", "superseded"}
BASE_COLUMNS = {
    "series_code": pa.string(), "period": pa.string(),
    "period_start": pa.string(), "period_end": pa.string(),
    "source_date_label": pa.string(), "source_row_index": pa.int64(),
    "value": pa.float64(), "value_raw": pa.string(), "is_missing": pa.bool_(),
    "unix_time": pa.int64(),
}
OBSERVATION_SCHEMA = pa.schema(list(BASE_COLUMNS.items()) + [
    ("native_frequency", pa.string()), ("missing_kind", pa.string()),
    ("source_cell_path", pa.string()),
    ("is_unresolved_missing", pa.bool_()), ("source_request_file", pa.string()),
    ("source_request_sha256", pa.string()), ("source_response_file", pa.string()),
    ("source_response_sha256", pa.string()), ("source_job_id", pa.string()),
    ("source_attempt", pa.int64()),
])
META_FIELDS = ["series_code", "series_name_tr", "series_name_en", "group_code",
               "group_name_tr", "frequency", "unit", "source", "default_aggregation",
               "metadata_url", "metadata_json"]
METADATA_SCHEMA = pa.schema([(key, pa.string()) for key in META_FIELDS] + [("is_archive", pa.bool_())])
COVERAGE_SCHEMA = pa.schema([
    *[(key, pa.string()) for key in ("series_code", "native_frequency", "target_start", "target_end",
       "observed_start", "observed_end", "request_gap_intervals_json", "coverage_status")],
    *[(key, pa.int64()) for key in ("observation_count", "numeric_observation_count", "missing_observation_count",
       "conflicting_period_count", "expected_period_count", "missing_returned_period_count",
       "missing_numeric_period_count", "requested_day_count", "covered_request_day_count",
       "successful_job_count", "no_data_job_count", "pending_job_count", "failed_job_count", "attempted_job_count")],
    *[(key, pa.bool_()) for key in ("physical_present", "request_coverage_complete", "numeric_coverage_complete")],
])
REQUEST_SCHEMA = pa.schema([
    *[(key, pa.string()) for key in ("series_code", "job_id", "start_date", "end_date", "status",
       "series_status", "source_request_file", "source_response_file", "source_response_sha256")],
    ("validated", pa.bool_()), ("returned_row_count", pa.int64()), ("target_numeric_count", pa.int64()),
])


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"..", "."} for part in path.parts):
        raise ValueError("Unsafe publication-relative path.")
    target = root / path
    if root.is_symlink() or any((root / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1)):
        raise ValueError("Publication files must not be symlinks.")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Publication path escapes the release.")
    return target


def resolve_publication(output: Path = DEFAULT_OUTPUT, verify: bool = True,
                        publication_id: str | None = None,
                        manifest_sha256: str | None = None) -> tuple[Path, dict[str, Any]]:
    """Resolve an immutable release and verify its complete file inventory."""
    output = Path(output)
    if (publication_id is None) != (manifest_sha256 is None):
        raise ValueError("Pinned publication ID and manifest SHA must be supplied together.")
    pointer = ({"publication_id": publication_id, "manifest_sha256": manifest_sha256}
               if publication_id is not None else json.loads((output / "CURRENT.json").read_text()))
    publication_id = pointer["publication_id"]
    if not re.fullmatch(r"[0-9a-f]{24}", publication_id):
        raise ValueError("Invalid EVDS publication identifier.")
    release = output / "releases" / publication_id
    manifest_path = _safe_file(release, "manifest.json")
    if _sha(manifest_path) != pointer["manifest_sha256"]:
        raise ValueError("EVDS publication manifest hash mismatch.")
    manifest = json.loads(manifest_path.read_text())
    identity = {key: value for key, value in manifest.items() if key != "publication_id"}
    if manifest.get("publication_id") != publication_id or _sha_bytes(_json(identity))[:24] != publication_id:
        raise ValueError("EVDS publication identity mismatch.")
    required = {"observations_long.parquet", "analysis_series_catalog.parquet", "coverage.parquet", "validation.json"}
    if not required.issubset(manifest["files"]):
        raise ValueError("EVDS publication is missing required inventory entries.")
    if verify:
        actual = {str(path.relative_to(release)) for path in release.rglob("*") if path.is_file()} - {"manifest.json"}
        if actual != set(manifest["files"]):
            raise ValueError("EVDS publication file inventory differs from the release.")
        for relative, expected in manifest["files"].items():
            path = _safe_file(release, relative)
            if not path.is_file() or path.stat().st_size != expected["size_bytes"] or _sha(path) != expected["sha256"]:
                raise ValueError(f"EVDS publication file hash mismatch: {relative}")
        if json.loads((release / "validation.json").read_text()) != manifest["validation"]:
            raise ValueError("EVDS publication validation summary mismatch.")
    return release, manifest


def _append(connection: duckdb.DuckDBPyConnection, table: str, records: list[dict], schema: pa.Schema) -> None:
    if not records:
        return
    connection.register("_incoming", pa.Table.from_pylist(records, schema=schema))
    try:
        connection.execute(f"INSERT INTO {table} SELECT * FROM _incoming")
    finally:
        connection.unregister("_incoming")


def _empty_table(connection: duckdb.DuckDBPyConnection, name: str, schema: pa.Schema) -> None:
    connection.register("_incoming", pa.Table.from_pylist([], schema=schema))
    connection.execute(f"CREATE TABLE {name} AS SELECT * FROM _incoming")
    connection.unregister("_incoming")


def _copy_table(connection: duckdb.DuckDBPyConnection, query: str, destination: Path) -> None:
    escaped = str(destination).replace("'", "''")
    connection.execute(f"COPY ({query}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")


def _verify_attempt(job: dict, metadata: dict[str, dict]) -> tuple[list[dict], dict, dict[str, bytes]]:
    # Pure collector functions are reused; this module never calls its transport.
    from tools.evds_bulk_collection import request_payload, validate_response

    source = Path(job["artifact_path"])
    files = {name: (source / name).read_bytes() for name in
             ("request.json", "response.json.gz", "response_info.json", "validation.json")}
    request = json.loads(files["request.json"])
    if request != request_payload(job):
        raise ValueError(f"Request does not match queue job {job['job_id']}.")
    raw = gzip.decompress(files["response.json.gz"])
    info = json.loads(files["response_info.json"])
    if info.get("sha256") != _sha_bytes(raw) or info.get("request_sha256") != _sha_bytes(files["request.json"]):
        raise ValueError(f"Raw/request hash mismatch for job {job['job_id']}.")
    if not 200 <= int(info.get("http_status", 0)) < 300:
        raise ValueError("Only successful HTTP responses can be published.")
    rows, result = validate_response(raw, job)
    validation = json.loads(files["validation.json"])
    recorded = json.loads(job["result_json"])
    for proof in (validation, recorded):
        for code, expected in result["series_results"].items():
            actual = proof.get("series_results", {}).get(code, {})
            for key in ("status", "returned_row_count", "target_numeric_count"):
                if actual.get(key) != expected.get(key):
                    raise ValueError("Per-series validation result differs from raw replay.")
        if set(proof.get("series_results", {})) != set(result["series_results"]):
            raise ValueError("Validation contains unexpected series.")
    expected_status = "succeeded" if any(row["value"] is not None for row in rows) else "no_data"
    if job["status"] != expected_status or validation.get("status") != expected_status:
        raise ValueError("Terminal job status differs from raw replay.")
    saved = pq.read_table(source / "observations.parquet")
    if not set(BASE_COLUMNS).issubset(saved.column_names):
        raise ValueError("Observation artifact is missing replay columns.")
    # Keep replay comparison in Arrow rather than materializing two additional
    # lists of Python dictionaries for a 100-series, 900-period response.
    actual = saved.select(list(BASE_COLUMNS)).cast(pa.schema(BASE_COLUMNS))
    expected = pa.Table.from_pylist(rows, schema=pa.schema(BASE_COLUMNS))
    order = [(key, "ascending") for key in ("series_code", "period", "source_row_index")]
    if not actual.sort_by(order).equals(expected.sort_by(order)):
        raise ValueError("Parquet observations differ from raw replay.")
    # Legacy complete-item artifacts predate cell paths. Their paths are
    # derived from the immutable raw response during replay. A newly accepted
    # transposed null must carry an explicit pointer to that source cell.
    if result.get("transposed_null_fallback") and "source_cell_path" not in saved.column_names:
        raise ValueError("Transposed fallback artifact requires source_cell_path.")
    if "source_cell_path" in saved.column_names:
        path_schema = pa.schema([(key, BASE_COLUMNS[key]) for key in ("series_code", "period", "source_row_index")]
                                + [("source_cell_path", pa.string())])
        actual_paths = saved.select(path_schema.names).cast(path_schema)
        expected_paths = pa.Table.from_pylist(rows, schema=path_schema)
        if not actual_paths.sort_by(order).equals(expected_paths.sort_by(order)):
            raise ValueError("Parquet source cell paths differ from raw replay.")
    for row in rows:
        code = row["series_code"]
        if code not in metadata or metadata[code]["frequency"] != job["frequency"]:
            raise ValueError("Observation frequency differs from pinned metadata.")
        if row["value"] is not None and not math.isfinite(row["value"]):
            raise ValueError("Non-finite observation value.")
        if not isinstance(row.get("source_cell_path"), str) or not row["source_cell_path"].startswith(("/items/", "/transposedItems/")):
            raise ValueError("Raw replay did not identify the source value cell.")
    return rows, result, files


def _request_gaps(intervals: list[tuple[date, date]], start: date, end: date) -> tuple[list[dict], int]:
    cursor, gaps = start, []
    for left, right in sorted(intervals):
        left, right = max(left, start), min(right, end)
        if right < cursor or left > end:
            continue
        if left > cursor:
            gaps.append({"start": cursor.isoformat(), "end": (left - timedelta(days=1)).isoformat()})
        cursor = max(cursor, right + timedelta(days=1))
    if cursor <= end:
        gaps.append({"start": cursor.isoformat(), "end": end.isoformat()})
    missing = sum((date.fromisoformat(gap["end"]) - date.fromisoformat(gap["start"])).days + 1 for gap in gaps)
    return gaps, (end - start).days + 1 - missing


def _verify_supersession(queue: sqlite3.Connection, job: dict) -> dict:
    """A failed child is covered only by an explicit, verifiable replacement."""
    exists = queue.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_supersessions'").fetchone()
    if not exists:
        raise ValueError("Superseded job has no supersession audit mapping.")
    row = queue.execute("SELECT * FROM job_supersessions WHERE job_id=?", (job["job_id"],)).fetchone()
    if row is None:
        raise ValueError("Superseded job has no supersession audit mapping.")
    audit = dict(row)
    replacement_row = queue.execute("SELECT * FROM jobs WHERE job_id=?", (audit["replacement_job_id"],)).fetchone()
    if replacement_row is None or replacement_row["status"] not in TERMINAL:
        raise ValueError("Supersession replacement must have a validated terminal response.")
    replacement = dict(replacement_row)
    previous = json.loads(audit["previous_job_json"])
    identity_fields = ("job_id", "series_codes_json", "group_code", "frequency", "aggregation", "start_date", "end_date")
    if any(key not in previous or previous[key] != job.get(key) for key in identity_fields):
        raise ValueError("Supersession audit does not preserve the original job scope.")
    if previous.get("status") not in QUEUE_STATES - {"superseded"} or not str(audit.get("reason", "")).strip():
        raise ValueError("Supersession requires the previous status and an explicit reason.")
    if any(job.get(key) != replacement.get(key) for key in ("group_code", "frequency", "aggregation")):
        raise ValueError("Supersession replacement group/frequency/aggregation differs.")
    if not set(json.loads(job["series_codes_json"])).issubset(json.loads(replacement["series_codes_json"])):
        raise ValueError("Supersession replacement does not contain the original series scope.")
    if replacement["start_date"] > job["start_date"] or replacement["end_date"] < job["end_date"]:
        raise ValueError("Supersession replacement does not contain the original time interval.")
    return {"job_id": job["job_id"], "replacement_job_id": replacement["job_id"],
            "previous_status": previous["status"], "reason": audit["reason"]}


def publish(database: Path = DEFAULT_DATABASE, output: Path = DEFAULT_OUTPUT,
            catalog_path: Path | None = None) -> dict[str, Any]:
    """Build a release from the queue's validated terminal attempts, offline."""
    database, output = Path(database).resolve(), Path(output).resolve()
    catalog_path = Path(catalog_path or DEFAULT_CATALOG).resolve()
    queue = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    queue.row_factory = sqlite3.Row
    queue.execute("BEGIN")
    stage = None
    connection = None
    try:
        configuration = dict(queue.execute("SELECT key,value FROM configuration"))
        if configuration.get("schema_version") != "evds_bulk_v1":
            raise ValueError("Unsupported EVDS bulk queue schema.")
        start, end = date.fromisoformat(configuration["target_start"]), date.fromisoformat(configuration["target_end"])
        if start > end or _sha(catalog_path) != configuration["catalog_sha256"]:
            raise ValueError("Pinned catalog hash or target interval is invalid.")
        output.mkdir(parents=True, exist_ok=True)
        (output / "releases").mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=output))
        connection = duckdb.connect(str(stage / ".merge.duckdb"))
        connection.execute("SET memory_limit='1024MB'")
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=false")
        _empty_table(connection, "metadata", METADATA_SCHEMA)
        _empty_table(connection, "vintages", OBSERVATION_SCHEMA)
        _empty_table(connection, "requests", REQUEST_SCHEMA)
        _empty_table(connection, "coverage", COVERAGE_SCHEMA)
        metadata = {}
        batch = []
        for item in queue.execute("SELECT series_code,metadata_json FROM series ORDER BY series_code"):
            record = json.loads(item["metadata_json"])
            if record.get("series_code") != item["series_code"]:
                raise ValueError("Queue metadata series identity mismatch.")
            metadata[item["series_code"]] = {"frequency": str(record["frequency"]),
                                             "aggregation": str(record.get("default_aggregation", ""))}
            batch.append({**{key: str(record.get(key) or "") for key in META_FIELDS},
                          "metadata_json": item["metadata_json"], "is_archive": bool(record.get("is_archive", False))})
            if len(batch) >= 512:
                _append(connection, "metadata", batch, METADATA_SCHEMA)
                batch = []
        _append(connection, "metadata", batch, METADATA_SCHEMA)
        if not metadata:
            raise ValueError("Cannot publish an empty metadata universe.")
        connection.read_parquet(str(catalog_path)).create_view("pinned_catalog")
        catalog_count = connection.execute("SELECT count(DISTINCT series_code) FROM pinned_catalog").fetchone()[0]
        mismatch = connection.execute("""SELECT count(*) FROM metadata m LEFT JOIN pinned_catalog p USING(series_code)
            WHERE p.series_code IS NULL OR m.frequency IS DISTINCT FROM p.frequency
               OR m.default_aggregation IS DISTINCT FROM p.default_aggregation""").fetchone()[0]
        if mismatch:
            raise ValueError("Queue metadata differs from pinned catalog.")
        pinned_columns = {item[0] for item in connection.execute("DESCRIBE pinned_catalog").fetchall()}
        text_checks = [f"m.{key} IS DISTINCT FROM coalesce(cast(p.{key} AS VARCHAR),'')"
                       for key in META_FIELDS if key != "metadata_json" and key in pinned_columns]
        if "is_archive" in pinned_columns:
            text_checks.append("m.is_archive IS DISTINCT FROM coalesce(p.is_archive,false)")
        if connection.execute("SELECT count(*) FROM metadata m JOIN pinned_catalog p USING(series_code) WHERE "
                              + " OR ".join(text_checks)).fetchone()[0]:
            raise ValueError("Queue source labels or units differ from pinned catalog.")
        intervals = defaultdict(list)
        job_counts = defaultdict(Counter)
        statuses = Counter()
        supersessions = []
        verified_jobs = set()
        for job_row in queue.execute("SELECT * FROM jobs ORDER BY job_id"):
            job = dict(job_row)
            if job["status"] not in QUEUE_STATES:
                raise ValueError(f"Unknown EVDS queue job state: {job['status']}")
            statuses[job["status"]] += 1
            codes = json.loads(job["series_codes_json"])
            if not codes or len(codes) != len(set(codes)) or any(code not in metadata for code in codes):
                raise ValueError("Job has invalid or unknown series membership.")
            left, right = date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"])
            if left < start or right > end or left > right:
                raise ValueError("Job interval lies outside pinned target.")
            if job["status"] == "superseded":
                supersessions.append(_verify_supersession(queue, job))
                for code in codes:
                    job_counts[code]["attempted"] += int(job["attempts"] > 0)
                continue
            if job["status"] == "split":
                for code in codes:
                    job_counts[code]["attempted"] += int(job["attempts"] > 0)
                continue
            if any(metadata[code]["frequency"] != job["frequency"] or metadata[code]["aggregation"] != job["aggregation"] for code in codes):
                raise ValueError("Job frequency or aggregation differs from metadata.")
            validated = job["status"] in TERMINAL
            rows, result, files = _verify_attempt(job, metadata) if validated else ([], {}, {})
            relative = None
            response_sha = None
            if validated:
                verified_jobs.add(job["job_id"])
                # Content-derived directories avoid trusting job identifiers as paths.
                token = _sha_bytes(_json({"job_id": job["job_id"], "attempt": job["attempts"],
                                         "raw": _sha_bytes(files["response.json.gz"])}))
                relative = Path("raw") / token
                (stage / relative).mkdir(parents=True)
                for name, content in files.items():
                    (stage / relative / name).write_bytes(content)
                response_sha = _sha_bytes(gzip.decompress(files["response.json.gz"]))
                request_sha = _sha_bytes(files["request.json"])
                for row in rows:
                    row.update(native_frequency=job["frequency"], missing_kind="observed" if row["value"] is not None else "source_null_unresolved",
                               is_unresolved_missing=row["value"] is None, source_job_id=job["job_id"], source_attempt=job["attempts"],
                               source_request_file=str(relative / "request.json"), source_request_sha256=request_sha,
                               source_response_file=str(relative / "response.json.gz"), source_response_sha256=response_sha)
                _append(connection, "vintages", rows, OBSERVATION_SCHEMA)
            request_records = []
            for code in codes:
                detail = result.get("series_results", {}).get(code, {})
                series_status = detail.get("status", job["status"])
                job_counts[code][series_status] += 1
                job_counts[code]["attempted"] += int(job["attempts"] > 0)
                if validated:
                    intervals[code].append((left, right))
                request_records.append({"series_code": code, "job_id": job["job_id"], "start_date": left.isoformat(),
                    "end_date": right.isoformat(), "status": job["status"], "series_status": series_status, "validated": validated,
                    "source_request_file": str(relative / "request.json") if relative else None,
                    "source_response_file": str(relative / "response.json.gz") if relative else None,
                    "source_response_sha256": response_sha,
                    "returned_row_count": detail.get("returned_row_count"), "target_numeric_count": detail.get("target_numeric_count")})
            _append(connection, "requests", request_records, REQUEST_SCHEMA)

        if not {item["replacement_job_id"] for item in supersessions}.issubset(verified_jobs):
            raise ValueError("Supersession replacement was not verified from its raw source response.")

        # Most source periods have one vintage. Keep the wide DISTINCT and window
        # state limited to duplicate keys, rather than all historical observations.
        connection.execute("""CREATE TABLE key_counts AS SELECT series_code,period,count(*) AS vintage_count
            FROM vintages GROUP BY series_code,period""")
        connection.execute("""CREATE TABLE duplicate_keys AS SELECT * FROM key_counts WHERE vintage_count>1""")
        # Null versus numeric is a conflict too: neither is silently preferred.
        connection.execute("""CREATE TABLE duplicate_variants AS SELECT v.series_code,v.period,
            count(DISTINCT struct_pack(value := v.value, start_date := v.period_start, end_date := v.period_end,
                                      frequency := v.native_frequency)) AS distinct_value_count
            FROM vintages v JOIN duplicate_keys d USING(series_code,period)
            GROUP BY v.series_code,v.period""")
        connection.execute("""CREATE TABLE period_versions AS SELECT k.*,
            coalesce(d.distinct_value_count,1) AS distinct_value_count
            FROM key_counts k LEFT JOIN duplicate_variants d USING(series_code,period)""")
        connection.execute("""CREATE TABLE canonical AS
            SELECT v.* FROM vintages v ANTI JOIN duplicate_variants d USING(series_code,period)
            UNION ALL
            (SELECT v.* FROM vintages v JOIN duplicate_variants d USING(series_code,period)
             WHERE d.distinct_value_count=1
             QUALIFY row_number() OVER(PARTITION BY v.series_code,v.period
                ORDER BY v.source_response_sha256,v.source_job_id,v.source_attempt)=1)""")
        stats = {row[0]: row[1:] for row in connection.execute("""SELECT series_code,count(*),count(value),
            min(CASE WHEN value IS NOT NULL THEN period_start END),max(CASE WHEN value IS NOT NULL THEN period_end END)
            FROM canonical GROUP BY series_code""").fetchall()}
        conflicts = dict(connection.execute("SELECT series_code,count(*) FROM period_versions WHERE distinct_value_count>1 GROUP BY series_code").fetchall())
        native_expected = {frequency: len(expected_periods(start, end, frequency)) or None
                           for frequency in {item["frequency"] for item in metadata.values()}}
        batch = []
        for code, meta in metadata.items():
            total, numeric, first, last = stats.get(code, (0, 0, None, None))
            expected = native_expected[meta["frequency"]]
            gaps, covered = _request_gaps(intervals[code], start, end)
            conflict_count = conflicts.get(code, 0)
            complete_numeric = bool(expected and numeric == expected and not conflict_count)
            counts = job_counts[code]
            state = ("conflicting_vintages" if conflict_count else "metadata_only" if not total and gaps
                     else "no_target_numeric_data" if not numeric else "calendar_unverified" if expected is None
                     else "complete_numeric_periods" if complete_numeric else "partial_numeric_periods")
            batch.append({"series_code": code, "native_frequency": meta["frequency"], "target_start": start.isoformat(),
                "target_end": end.isoformat(), "observed_start": first, "observed_end": last,
                "observation_count": total, "numeric_observation_count": numeric, "missing_observation_count": total - numeric,
                "conflicting_period_count": conflict_count, "expected_period_count": expected,
                "missing_returned_period_count": expected - total if expected is not None else None,
                "missing_numeric_period_count": expected - numeric if expected is not None else None,
                "physical_present": total > 0, "request_coverage_complete": not gaps,
                "requested_day_count": (end-start).days+1, "covered_request_day_count": covered,
                "request_gap_intervals_json": _json(gaps).decode().strip(),
                "successful_job_count": counts["succeeded"], "no_data_job_count": counts["no_data"],
                "pending_job_count": counts["pending"] + counts["running"] + counts["retryable_failed"],
                "failed_job_count": counts["permanent_failed"], "coverage_status": state,
                "attempted_job_count": counts["attempted"],
                "numeric_coverage_complete": complete_numeric})
            if len(batch) >= 512:
                _append(connection, "coverage", batch, COVERAGE_SCHEMA)
                batch = []
        _append(connection, "coverage", batch, COVERAGE_SCHEMA)
        _copy_table(connection, "SELECT * FROM canonical ORDER BY series_code,period", stage / "observations_long.parquet")
        _copy_table(connection, "SELECT * FROM vintages ORDER BY series_code,period,source_response_sha256,source_job_id", stage / "observations_vintages.parquet")
        _copy_table(connection, "SELECT * FROM period_versions WHERE distinct_value_count>1 ORDER BY series_code,period", stage / "conflicts.parquet")
        _copy_table(connection, "SELECT * FROM requests ORDER BY series_code,start_date,job_id", stage / "request_coverage.parquet")
        _copy_table(connection, "SELECT * FROM coverage ORDER BY series_code", stage / "coverage.parquet")
        _copy_table(connection, """SELECT m.*,c.* EXCLUDE(series_code,native_frequency),
            c.physical_present AS observation_available,c.target_start AS requested_start,c.target_end AS requested_end,
            '' AS role,'' AS temporal_semantics,'' AS subperiod_aggregation,false AS is_derived,
            'Native observations only; semantic role and source null causes require review. Conflicting vintages are excluded.' AS reason
            FROM metadata m JOIN coverage c USING(series_code) ORDER BY series_code""", stage / "analysis_series_catalog.parquet")
        totals = connection.execute("""SELECT count(*),count(*) FILTER(WHERE physical_present),
            count(*) FILTER(WHERE numeric_observation_count>0),coalesce(sum(observation_count),0),
            coalesce(sum(missing_observation_count),0),coalesce(sum(conflicting_period_count),0),
            count(*) FILTER(WHERE request_coverage_complete),bool_and(request_coverage_complete),
            bool_and(numeric_coverage_complete) FROM coverage""").fetchone()
        validation = dict(zip(["metadata_series", "physical_series", "numeric_series", "observation_count",
             "source_null_count", "conflicting_period_count", "completed_request_series",
             "full_request_scope_complete", "full_numeric_coverage_complete"], totals))
        full_metadata = len(metadata) == catalog_count
        validation.update(catalog_series_count=catalog_count, metadata_scope_complete=full_metadata,
                          queue_request_scope_complete=bool(totals[7]),
                          full_request_scope_complete=bool(totals[7] and full_metadata),
                          full_numeric_coverage_complete=bool(totals[8] and full_metadata),
                          attempted_series=sum(bool(counts["attempted"]) for counts in job_counts.values()),
                          attempted_job_count=queue.execute("SELECT count(*) FROM jobs WHERE attempts>0").fetchone()[0])
        validation.update(status="passed", dataset_id="evds.full_catalog",
            evds_full_observation_coverage_complete=bool(totals[7] and totals[8] and full_metadata),
            pending_job_count=sum(statuses[key] for key in ("pending", "running", "retryable_failed")),
            failed_job_count=statuses["permanent_failed"], job_statuses=dict(sorted(statuses.items())),
            superseded_job_count=statuses["superseded"],
            completion_means="Validated immutable source responses; successful requests do not establish complete numeric history.",
            conflict_policy="Exclude conflicting periods from canonical observations; preserve all vintages and conflict inventory.",
            null_policy="Source null causes remain unresolved; no inferred lifespan or holiday zero.")
        connection.close()
        connection = None
        for file in stage.glob(".merge.duckdb*"):
            shutil.rmtree(file) if file.is_dir() else file.unlink()
        (stage / "validation.json").write_bytes(_json(validation))
        inventory = {str(path.relative_to(stage)): {"sha256": _sha(path), "size_bytes": path.stat().st_size}
                     for path in sorted(stage.rglob("*")) if path.is_file()}
        manifest = {"format_version": 1, "dataset_id": "evds.full_catalog", "target_start": start.isoformat(),
                    "target_end": end.isoformat(), "catalog_sha256": configuration["catalog_sha256"],
                    "catalog_file_verified": True, "configuration": configuration, "files": inventory, "validation": validation,
                    "supersessions": supersessions,
                    "provenance_hash_basis": {"source_response_sha256": "decompressed_response_bytes",
                                              "source_request_sha256": "file_bytes", "manifest_files": "file_bytes"}}
        publication_id = _sha_bytes(_json(manifest))[:24]
        manifest["publication_id"] = publication_id
        (stage / "manifest.json").write_bytes(_json(manifest))
        destination = output / "releases" / publication_id
        if destination.exists():
            # Identical publication is a no-op; never overwrite an old release.
            if (destination / "manifest.json").read_bytes() != (stage / "manifest.json").read_bytes():
                raise ValueError("Existing publication identity collision.")
            actual = {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()} - {"manifest.json"}
            if actual != set(inventory):
                raise ValueError("Existing publication inventory is corrupted.")
            for relative, info in inventory.items():
                if _sha(_safe_file(destination, relative)) != info["sha256"]:
                    raise ValueError("Existing publication is corrupted.")
            shutil.rmtree(stage)
        else:
            os.rename(stage, destination)
        stage = None
        pointer = {"publication_id": publication_id, "manifest_sha256": _sha(destination / "manifest.json")}
        with tempfile.NamedTemporaryFile(prefix=".CURRENT-", dir=output, delete=False) as handle:
            handle.write(_json(pointer))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, output / "CURRENT.json")
        return {**pointer, "release_path": str(destination), "validation": validation}
    finally:
        if connection is not None:
            connection.close()
        queue.close()
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    print(json.dumps(publish(args.database, args.output, args.catalog), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
