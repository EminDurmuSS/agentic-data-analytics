#!/usr/bin/env python3
"""Durable, bounded native-frequency EVDS bulk collection over public /fe.

Every queue pins a catalog and target window. Successful HTTP requests are not
claims of complete numeric/calendar coverage. Only validated attempt artifacts
are eligible for the separate offline publisher. No credentials are required.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.EVDS_Manifest_Indirme_Araci import (
    ENDPOINT, HEADERS, FREQUENCY_CODES, expected_periods, parse_period_label,
    request_payload as _single_payload,
)

DEFAULT_DATABASE = ROOT / "tmp/evds_bulk/queue.sqlite"
LOW = {"AYLIK", "ÜÇ AYLIK", "ALTI AYLIK", "YILLIK"}
AGGREGATIONS = {"avg", "sum", "last", "first", "min", "max"}
STATUSES = ("pending", "running", "retryable_failed", "succeeded", "no_data", "split", "permanent_failed", "superseded")
OBSERVATION_COLUMNS = {
    "series_code": "string", "period": "string", "period_start": "string", "period_end": "string",
    "source_date_label": "string", "source_row_index": "int64", "source_cell_path": "string", "value": "float64", "value_raw": "string",
    "is_missing": "bool", "unix_time": "Int64", "native_frequency": "string",
    "source_request_file": "string", "source_request_sha256": "string", "source_response_file": "string",
    "source_response_sha256": "string", "missing_kind": "string", "is_unresolved_missing": "bool",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            file.write(canonical(value) + "\n")
            file.flush()
            import os
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def connect(database):
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS configuration(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS series(series_code TEXT PRIMARY KEY,metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
            job_id TEXT PRIMARY KEY,series_codes_json TEXT NOT NULL,group_code TEXT NOT NULL,
            frequency TEXT NOT NULL,aggregation TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,
            status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,artifact_path TEXT,result_json TEXT,
            available_after REAL NOT NULL DEFAULT 0,lease_token TEXT,lease_until REAL,last_error TEXT,
            parent_job_id TEXT,updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS ready_jobs ON jobs(status,available_after,attempts,job_id);
        CREATE TABLE IF NOT EXISTS series_results(
            job_id TEXT NOT NULL,series_code TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,
            status TEXT NOT NULL,result_json TEXT,PRIMARY KEY(job_id,series_code));
        CREATE TABLE IF NOT EXISTS rate_limit(id INTEGER PRIMARY KEY CHECK(id=1),next_allowed REAL NOT NULL);
        INSERT OR IGNORE INTO rate_limit VALUES(1,0);
        CREATE TABLE IF NOT EXISTS job_supersessions(
            job_id TEXT PRIMARY KEY,replacement_job_id TEXT NOT NULL,
            previous_job_json TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL);
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def bounded_chunks(start, end, frequency):
    """At most 900 daily dates, or conservatively fewer than 1000 native rows."""
    if not isinstance(start, date) or not isinstance(end, date) or start > end or frequency not in FREQUENCY_CODES:
        raise ValueError("Invalid target dates or unsupported native frequency")
    span = 900 if frequency in {"GÜNLÜK", "İŞ GÜNÜ"} else 6300 if frequency.startswith("HAFTALIK") else 3650
    chunks = []
    cursor = start
    while cursor <= end:
        boundary = min(end, cursor + timedelta(days=min(span - 1, (date.max - cursor).days)))
        if frequency in LOW and boundary < end:
            boundary = date(boundary.year - 1, 12, 31)
        chunks.append((cursor, boundary))
        if boundary == end:
            break
        cursor = boundary + timedelta(days=1)
    return chunks


def _codes(job):
    codes = job.get("series_codes")
    if codes is None:
        codes = json.loads(job["series_codes_json"])
    if not isinstance(codes, list) or not 1 <= len(codes) <= 100 or len(codes) != len(set(codes)) or not all(isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,250}", code) for code in codes):
        raise ValueError("A request requires 1 to 100 unique catalog series identifiers")
    if len({code.replace(".", "_") for code in codes}) != len(codes):
        raise ValueError("Series identifiers collide in the EVDS response encoding")
    return codes


def request_payload(job):
    codes = _codes(job)
    if job["aggregation"] not in AGGREGATIONS:
        raise ValueError("Unsupported native aggregation")
    start, end = date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"])
    if start > end:
        raise ValueError("Invalid request window")
    payload = _single_payload("-".join(codes), "-".join([job["aggregation"]] * len(codes)), job["frequency"], start, end)
    payload["formulas"] = "-".join(["0"] * len(codes))
    return payload


def _insert_job(db, codes, group, frequency, aggregation, start, end, *, parent=None, status="pending", error=None):
    identity = {"codes": codes, "group": group, "frequency": frequency, "aggregation": aggregation,
                "start": start.isoformat(), "end": end.isoformat()}
    job_id = sha256_bytes(canonical(identity).encode())[:40]
    db.execute("""INSERT OR IGNORE INTO jobs(job_id,series_codes_json,group_code,frequency,aggregation,start_date,end_date,status,parent_job_id,last_error,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (job_id, canonical(codes), group, frequency, aggregation, start.isoformat(), end.isoformat(), status, parent, error, time.time()))
    for code in codes:
        db.execute("INSERT OR IGNORE INTO series_results VALUES(?,?,?,?,?,?)", (job_id, code, start.isoformat(), end.isoformat(), status, canonical({"error": error}) if error else None))
    return job_id


def plan(database, catalog_path, start=date(2021, 1, 1), end=date(2026, 6, 30), *, batch_size=20, groups=None, series_codes=None):
    if type(batch_size) is not int or not 1 <= batch_size <= 100 or start > end:
        raise ValueError("Batch size must be 1..100 and target dates ordered")
    catalog_path = Path(catalog_path)
    frame = pd.read_parquet(catalog_path)
    required = {"series_code", "group_code", "frequency", "default_aggregation"}
    if not required.issubset(frame) or frame["series_code"].duplicated().any() or frame["series_code"].isna().any():
        raise ValueError("Catalog is missing fields or has duplicate/missing series identities")
    if groups:
        missing = set(groups) - set(frame["group_code"])
        if missing:
            raise ValueError(f"Unknown catalog groups: {sorted(missing)}")
        frame = frame[frame["group_code"].isin(groups)]
    if series_codes:
        missing = set(series_codes) - set(frame["series_code"])
        if missing:
            raise ValueError(f"Unknown series in selected catalog scope: {sorted(missing)}")
        frame = frame[frame["series_code"].isin(series_codes)]
    if frame.empty:
        raise ValueError("Planning filter selected no catalog series")
    identifiers = frame["series_code"].tolist()
    if not all(isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,250}", code) for code in identifiers):
        raise ValueError("Catalog contains invalid EVDS series identifiers")
    if len({code.replace(".", "_") for code in identifiers}) != len(identifiers):
        raise ValueError("Catalog series identifiers collide in the EVDS response encoding")
    settings = {"schema_version": "evds_bulk_v1", "target_start": start.isoformat(), "target_end": end.isoformat(),
                "catalog_sha256": sha256_bytes(catalog_path.read_bytes()), "batch_size": str(batch_size),
                "selection_kind": "filtered" if groups or series_codes else "full_catalog",
                "selection_sha256": sha256_bytes(canonical(sorted(frame["series_code"].tolist())).encode())}
    grouped = {}
    with connect(database) as db:
        saved = dict(db.execute("SELECT key,value FROM configuration"))
        if saved and saved != settings:
            raise ValueError("Queue pins another catalog/window/batch/filter; use a separate database")
        db.executemany("INSERT OR IGNORE INTO configuration VALUES(?,?)", settings.items())
        for source in frame.to_dict("records"):
            metadata = _clean(source)
            code = metadata["series_code"]
            db.execute("INSERT OR IGNORE INTO series VALUES(?,?)", (code, canonical(metadata)))
            key = (str(metadata["group_code"]), str(metadata["frequency"]), str(metadata["default_aggregation"]))
            grouped.setdefault(key, []).append(code)
        for (group, frequency, aggregation), codes in sorted(grouped.items()):
            supported = frequency in FREQUENCY_CODES and aggregation in AGGREGATIONS
            chunks = bounded_chunks(start, end, frequency) if supported else [(start, end)]
            codes.sort()
            for offset in range(0, len(codes), batch_size):
                batch = codes[offset:offset + batch_size]
                for first, last in chunks:
                    _insert_job(db, batch, group, frequency, aggregation, first, last,
                                status="pending" if supported else "permanent_failed", error=None if supported else "Unsupported catalog frequency/aggregation")
    return status_report(database)


class ValidationError(ValueError):
    def __init__(self, message, code="INVALID_RESPONSE"):
        super().__init__(message)
        self.code = code


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Duplicate JSON key in source response")
        result[key] = value
    return result


def _cell_pointer(*parts):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _transposed_null_columns(payload, keys, frequency, start, end):
    """Resolve only complete monthly null columns from the explicit second view.

    This is evidence of this API response's nulls, not proof that a series never
    had observations. Never use the second view to supply a numeric value.
    """
    items = payload["items"]
    encoded = set(keys.values())
    if not items or all(isinstance(item, dict) and encoded.issubset(item) for item in items):
        return {}
    if frequency != "AYLIK" or not all(isinstance(item, dict) and isinstance(item.get("Tarih"), str) for item in items):
        raise ValidationError("Observation missing its date or a requested series cell", "INVALID_CELL_SCHEMA")
    source_dates = [item["Tarih"] for item in items]
    try:
        native = [parse_period_label(label, frequency)[0] for label in source_dates]
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("Invalid native date label", "INVALID_DATE") from exc
    expected = {period for period, _, _ in expected_periods(start, end, frequency)}
    if len(set(source_dates)) != len(items) or len(set(native)) != len(items) or set(native) != expected:
        raise ValidationError("Null transpose fallback requires the complete expected monthly date set", "INVALID_TRANSPOSE_NULL_EVIDENCE")
    columns = payload.get("transposedColumns")
    if not isinstance(columns, list) or len(columns) != len(source_dates) + 1:
        raise ValidationError("Null transpose fallback requires complete transposed column descriptors", "INVALID_TRANSPOSE_NULL_EVIDENCE")
    column_keys = set()
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"key", "title"} or not isinstance(column["key"], str) or not isinstance(column["title"], str) or column["key"] in column_keys:
            raise ValidationError("Null transpose fallback has invalid or duplicate column descriptors", "INVALID_TRANSPOSE_NULL_EVIDENCE")
        if column["key"] != "serieName" and column["title"] != column["key"]:
            raise ValidationError("Null transpose fallback has inconsistent date column titles", "INVALID_TRANSPOSE_NULL_EVIDENCE")
        column_keys.add(column["key"])
    if column_keys != set(source_dates) | {"serieName"}:
        raise ValidationError("Null transpose fallback column dates differ from source items", "INVALID_TRANSPOSE_NULL_EVIDENCE")
    absent = {key for key in encoded if all(key not in item for item in items)}
    if not absent or any(key not in item for item in items for key in encoded - absent):
        raise ValidationError("Null transpose fallback cannot fill a partially missing item column", "INVALID_TRANSPOSE_NULL_EVIDENCE")
    transpose = payload.get("transposedItems")
    if not isinstance(transpose, list) or len(transpose) != len(keys):
        raise ValidationError("Null transpose fallback requires one row for every requested series", "INVALID_TRANSPOSE_NULL_EVIDENCE")
    lookup = {}
    for index, row in enumerate(transpose):
        if not isinstance(row, dict) or not isinstance(row.get("serieCode"), str) or row["serieCode"] not in encoded or row["serieCode"] in lookup:
            raise ValidationError("Null transpose fallback has invalid or duplicate series identities", "INVALID_TRANSPOSE_NULL_EVIDENCE")
        key = row["serieCode"]
        title = payload["seriesNames"][key]
        if not isinstance(title, str) or row.get("serieName") != title or set(row) != set(source_dates) | {"serieCode", "serieName"}:
            raise ValidationError("Null transpose fallback has inconsistent series title or date keys", "INVALID_TRANSPOSE_NULL_EVIDENCE")
        lookup[key] = (index, row)
    for item in items:
        label = item["Tarih"]
        for key in encoded:
            _, transposed = lookup[key]
            value = transposed[label]
            if key in absent:
                if value is not None:
                    raise ValidationError("Null transpose fallback only accepts explicit JSON null cells", "INVALID_TRANSPOSE_NULL_EVIDENCE")
            elif type(item[key]) is not type(value) or item[key] != value:
                raise ValidationError("Items and transposedItems contain conflicting source cells", "SOURCE_REPRESENTATION_CONFLICT")
    return {key: lookup[key][0] for key in absent}


def validate_response(raw, job):
    """Parse once for a batch, preserving original source row positions.

    Pure replay API used by the publisher. No network, SQL or filesystem calls.
    Accept only verified native weekly boundary rows, recording their exclusion.
    Public Wednesday responses additionally include the preceding native week.
    """
    codes, frequency = _codes(job), job["frequency"]
    start, end = date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"])
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValidationError("Non-finite JSON number")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Response is not a valid JSON document") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValidationError("Response requires an items array")
    items = payload["items"]
    count = payload.get("totalCount", len(items))
    if type(count) is bool or not isinstance(count, (int, str)) or not str(count).isdigit() or int(count) != len(items):
        raise ValidationError("Response totalCount differs from the returned rows", "TRUNCATED_RESPONSE")
    if len(items) >= 1000:
        raise ValidationError("Response reached the 1000-row boundary", "ROW_LIMIT")
    keys = {code: code.replace(".", "_") for code in codes}
    names = payload.get("seriesNames")
    if not isinstance(names, dict) or set(names) != set(keys.values()):
        raise ValidationError("Returned series identities differ from requested catalog members", "SERIES_IDENTITY_MISMATCH")
    transposed_nulls = _transposed_null_columns(payload, keys, frequency, start, end)
    by_series = {code: [] for code in codes}
    periods, boundaries, boundary_sides = set(), [], set()
    weekday = {"HAFTALIK(CUMA)": 4, "HAFTALIK(ÇARŞAMBA)": 2}.get(frequency)
    for source_index, item in enumerate(items, 1):
        if not isinstance(item, dict) or not (set(keys.values()) - set(transposed_nulls)).issubset(item):
            raise ValidationError("Observation missing its date or a requested series cell", "INVALID_CELL_SCHEMA")
        source_label = item.get("Tarih")
        # Actual public EVDS annual responses use JSON integer years. Other
        # native frequencies still require their explicit source date strings.
        if frequency == "YILLIK" and type(source_label) is int and 1 <= source_label <= 9999:
            source_label = str(source_label)
        if not isinstance(source_label, str):
            raise ValidationError("Observation has an invalid native date type", "INVALID_DATE")
        unexpected = set(item) - set(keys.values()) - {"Tarih", "UNIXTIME", "YEARWEEK"}
        if unexpected:
            raise ValidationError(f"Unexpected observation fields: {sorted(unexpected)}", "SERIES_IDENTITY_MISMATCH")
        try:
            period, first, last = parse_period_label(source_label, frequency)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValidationError("Invalid native date label", "INVALID_DATE") from exc
        if period in periods:
            raise ValidationError("Duplicate native period", "DUPLICATE_PERIOD")
        periods.add(period)
        if weekday is not None and first.weekday() != weekday:
            raise ValidationError("Weekly date does not match the catalog native weekday", "INVALID_DATE")
        outside = last < start or first > end
        if outside:
            side = "after" if 0 < (first - end).days <= 6 else "before" if frequency == "HAFTALIK(ÇARŞAMBA)" and 0 < (start - first).days <= 6 else None
            if weekday is None or side is None or first.weekday() != weekday or side in boundary_sides:
                raise ValidationError("Response contains an unexpected out-of-window date", "INVALID_DATE")
            boundary_sides.add(side)
            boundaries.append(first.isoformat())
        unix = item.get("UNIXTIME")
        if isinstance(unix, dict):
            if set(unix) != {"$numberLong"}:
                raise ValidationError("Invalid source UNIXTIME object")
            unix = unix["$numberLong"]
        if unix is not None:
            try:
                if type(unix) is bool or isinstance(unix, float) or not re.fullmatch(r"-?\d+", str(unix)):
                    raise ValueError()
                unix = int(unix)
                if not -(2 ** 63) <= unix < 2 ** 63:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValidationError("Invalid source UNIXTIME value") from None
        for code, key in keys.items():
            value_raw = item[key] if key in item else None
            if isinstance(value_raw, (dict, list, bool)):
                raise ValidationError(f"Invalid scalar value for {code}", "INVALID_NUMERIC")
            if value_raw is None or str(value_raw).strip() == "":
                value = None
            else:
                try:
                    value = float(str(value_raw).replace(",", "."))
                    if not math.isfinite(value):
                        raise ValueError()
                except (ValueError, TypeError, OverflowError):
                    raise ValidationError(f"Non-finite or invalid numeric value for {code}", "INVALID_NUMERIC") from None
            if outside:
                continue
            by_series[code].append({"series_code": code, "period": period, "period_start": first.isoformat(),
                "period_end": last.isoformat(), "source_date_label": source_label, "source_row_index": source_index,
                "source_cell_path": _cell_pointer("transposedItems", transposed_nulls[key], source_label) if key in transposed_nulls else _cell_pointer("items", source_index - 1, key),
                "value": value, "value_raw": None if value_raw is None else str(value_raw), "is_missing": value is None,
                "unix_time": unix})
    expected = {period for period, _, _ in expected_periods(start, end, frequency)}
    results = {}
    for code, rows in by_series.items():
        observed = {row["period"] for row in rows}
        numeric = {row["period"] for row in rows if row["value"] is not None}
        status = "succeeded" if numeric else "no_data"
        results[code] = {"status": status, "returned_row_count": len(rows), "target_numeric_count": len(numeric),
            "source_null_count": len(rows) - len(numeric), "physical_present": bool(rows),
            "observed_start": min((row["period_start"] for row in rows if row["value"] is not None), default=None),
            "observed_end": max((row["period_end"] for row in rows if row["value"] is not None), default=None),
            "expected_period_count": len(expected) if expected else None,
            "missing_returned_periods": sorted(expected - observed) if expected else None,
            "missing_numeric_periods": sorted(expected - numeric) if expected else None,
            "coverage_status": "no_target_numeric_data" if not numeric else "calendar_unverified" if not expected else "complete_numeric_periods" if expected <= numeric else "partial_numeric_periods"}
    result = {"status": "succeeded" if any(v["target_numeric_count"] for v in results.values()) else "no_data",
              "series_results": results, "response_total_count": len(items),
              "excluded_weekly_boundary_periods": boundaries, "frequency_conversion": payload.get("frequencyConversion"),
              "completion_means": "Validated bounded request; numeric/calendar completeness is reported per series separately."}
    if transposed_nulls:
        result["transposed_null_fallback"] = {
            "series_codes": sorted(code for code, key in keys.items() if key in transposed_nulls),
            "cell_count": len(transposed_nulls) * len(items),
            "source_representation": "transposedItems",
            "reason": "Complete monthly null columns are explicit in the same API response's transposed representation; no numeric values were inferred. source_cell_path is the value location; source_row_index remains the items date-row anchor.",
        }
    return [row for code in codes for row in by_series[code]], result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_public(body, timeout=45):
    """Use Python only, fixed public EVDS host, verified TLS, no redirects/key."""
    started = datetime.now(timezone.utc).isoformat()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers=HEADERS)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValidationError("Response exceeds 64 MiB transport limit", "RESPONSE_TOO_LARGE")
        info = {"http_status": response.code, "content_type": response.headers.get("Content-Type", ""),
                "retry_after": response.headers.get("Retry-After"), "final_url": response.geturl(),
                "url": ENDPOINT, "transport": "urllib", "tls_verification": True, "started_at_utc": started,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(), "bytes": len(raw)}
    return raw, info


def retry_after_seconds(value, now=None):
    now = time.time() if now is None else now
    if value is None:
        return 0.
    try:
        delay = float(value)
        if math.isfinite(delay):
            return min(max(0., delay), 86400.)
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return min(max(0., parsed.timestamp() - now), 86400.)
    except (TypeError, ValueError, OverflowError):
        return 0.


def claim_job(database, *, now=None, lease_seconds=180, max_attempts=3, min_interval_seconds=1):
    now = time.time() if now is None else now
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        expired = db.execute("SELECT job_id,attempts FROM jobs WHERE status='running' AND lease_until<=?", (now,)).fetchall()
        for row in expired:
            status = "permanent_failed" if row["attempts"] >= max_attempts else "retryable_failed"
            db.execute("UPDATE jobs SET status=?,lease_token=NULL,lease_until=NULL,last_error='Worker lease expired',updated_at=? WHERE job_id=?", (status, now, row["job_id"]))
            db.execute("UPDATE series_results SET status=? WHERE job_id=?", (status, row["job_id"]))
        # A resumed invocation may lower its retry budget. Such jobs must become
        # terminal instead of keeping --drain alive while remaining unclaimable.
        exhausted = db.execute("SELECT job_id FROM jobs WHERE status IN ('pending','retryable_failed') AND attempts>=?", (max_attempts,)).fetchall()
        for row in exhausted:
            db.execute("UPDATE jobs SET status='permanent_failed',last_error='Retry budget exhausted',updated_at=? WHERE job_id=?", (now, row["job_id"]))
            db.execute("UPDATE series_results SET status='permanent_failed' WHERE job_id=?", (row["job_id"],))
        if db.execute("SELECT next_allowed FROM rate_limit WHERE id=1").fetchone()[0] > now:
            return None
        row = db.execute("SELECT * FROM jobs WHERE status IN ('pending','retryable_failed') AND available_after<=? AND attempts<? ORDER BY attempts,job_id LIMIT 1", (now, max_attempts)).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? WHERE job_id=?", (token, now + lease_seconds, now, row["job_id"]))
        db.execute("UPDATE series_results SET status='running' WHERE job_id=?", (row["job_id"],))
        db.execute("UPDATE rate_limit SET next_allowed=? WHERE id=1", (now + min_interval_seconds,))
        return dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())


def _split_specs(job, *, date_split=False):
    codes = _codes(job)
    start, end = date.fromisoformat(job["start_date"]), date.fromisoformat(job["end_date"])
    if len(codes) > 1:
        middle = len(codes) // 2
        return [(codes[:middle], start, end), (codes[middle:], start, end)]
    if date_split and start < end:
        middle = start + timedelta(days=(end - start).days // 2)
        if job["frequency"] in LOW:
            middle = date(middle.year, 12, 31)
            if middle >= end:
                middle = date(middle.year - 1, 12, 31)
        if start <= middle < end:
            return [(codes, start, middle), (codes, middle + timedelta(days=1), end)]
    return []


def finish_job(database, job, status, *, result=None, error=None, artifact_path=None, available_after=0, split_specs=None, cooldown_until=0):
    if status not in set(STATUSES) - {"pending", "running", "superseded"}:
        raise ValueError("Invalid final job state")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        owned = db.execute("SELECT 1 FROM jobs WHERE job_id=? AND status='running' AND lease_token=?", (job["job_id"], job["lease_token"])).fetchone()
        if not owned:
            raise ValueError("Lease ownership changed; refusing stale completion")
        for codes, start, end in split_specs or []:
            _insert_job(db, codes, job["group_code"], job["frequency"], job["aggregation"], start, end, parent=job["job_id"])
        db.execute("UPDATE jobs SET status=?,result_json=?,last_error=?,artifact_path=?,available_after=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE job_id=?",
            (status, canonical(result) if result is not None else None, error, str(artifact_path) if artifact_path else None, available_after, time.time(), job["job_id"]))
        for code in _codes(job):
            detail = (result or {}).get("series_results", {}).get(code)
            db.execute("UPDATE series_results SET status=?,result_json=? WHERE job_id=? AND series_code=?",
                (detail.get("status", status) if detail else status, canonical(detail) if detail else canonical({"error": error}) if error else None, job["job_id"], code))
        db.execute("UPDATE rate_limit SET next_allowed=max(next_allowed,?) WHERE id=1", (cooldown_until,))


def retry_validated_batch(database, job_id, *, max_attempts=3):
    """Queue a previously split batch after verified replay, with audited children.

    Operator-only repair. No HTTP request occurs here. A fresh normal collector
    attempt must still succeed before the publisher can accept the replacement.
    Existing attempt files and counters are never reset or overwritten.
    """
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{40}", job_id) or type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError("Invalid batch identity or retry budget")
    if not Path(database).is_file():
        raise ValueError("Queue does not exist")
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        saved = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if saved is None or saved["status"] != "split":
            raise ValueError("Only an existing split parent batch can be retried")
        parent = dict(saved)
        if parent["attempts"] >= max_attempts:
            raise ValueError("Replacement batch has exhausted its total attempt budget")
        if not parent["artifact_path"] or parent["lease_token"] is not None:
            raise ValueError("Split parent has no stable completed attempt artifact")
        parent_codes = set(_codes(parent))
        first, last = date.fromisoformat(parent["start_date"]), date.fromisoformat(parent["end_date"])
        descendants = [dict(row) for row in db.execute("""
            WITH RECURSIVE descendants(job_id) AS (
                SELECT job_id FROM jobs WHERE parent_job_id=?
                UNION
                SELECT child.job_id FROM jobs child JOIN descendants ON child.parent_job_id=descendants.job_id
            ) SELECT jobs.* FROM jobs JOIN descendants USING(job_id) LIMIT 10001
        """, (job_id,))]
        if not descendants or len(descendants) > 10000:
            raise ValueError("Replacement requires 1..10000 bounded descendants")
        for child in descendants:
            if child["job_id"] == job_id or child["status"] in {"running", "superseded"} or child["status"] not in STATUSES or child["lease_token"] is not None:
                raise ValueError("A descendant is running, already superseded, or has invalid lineage/state")
            if db.execute("SELECT 1 FROM job_supersessions WHERE job_id=?", (child["job_id"],)).fetchone():
                raise ValueError("A descendant already has immutable supersession history")
            if any(child[field] != parent[field] for field in ("group_code", "frequency", "aggregation")) or not set(_codes(child)) <= parent_codes:
                raise ValueError("Descendant series or native scope escapes the replacement batch")
            child_first, child_last = date.fromisoformat(child["start_date"]), date.fromisoformat(child["end_date"])
            if not first <= child_first <= child_last <= last:
                raise ValueError("Descendant date scope escapes the replacement batch")
            interval_codes = {row[0] for row in db.execute("SELECT series_code FROM series_results WHERE job_id=?", (child["job_id"],))}
            if interval_codes != set(_codes(child)):
                raise ValueError("Descendant series interval records are incomplete")
        artifact = Path(parent["artifact_path"])
        body = (artifact / "request.json").read_bytes()
        with gzip.open(artifact / "response.json.gz", "rb") as file:
            raw = file.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("Stored response exceeds replay budget")
        info = json.loads((artifact / "response_info.json").read_text(), object_pairs_hook=_unique_object)
        request_hash, response_hash = sha256_bytes(body), sha256_bytes(raw)
        if info.get("http_status") != 200 or info.get("request_sha256") != request_hash or info.get("sha256") != response_hash:
            raise ValueError("Stored successful response/request hash proof is invalid")
        if json.loads(body, object_pairs_hook=_unique_object) != request_payload(parent):
            raise ValueError("Stored request differs from the exact replacement batch")
        rows, replay = validate_response(raw, parent)
        if replay["status"] not in {"succeeded", "no_data"}:
            raise ValueError("Stored original response does not pass current validation")
        created = time.time()
        reason = f"Original batch verified under current parser; replacement requires a fresh successful attempt. Request SHA256 {request_hash}; decompressed response SHA256 {response_hash}."
        for child in descendants:
            db.execute("INSERT INTO job_supersessions VALUES(?,?,?,?,?)", (child["job_id"], job_id, canonical(child), reason, created))
            db.execute("UPDATE jobs SET status='superseded',updated_at=? WHERE job_id=?", (created, child["job_id"]))
            db.execute("UPDATE series_results SET status='superseded' WHERE job_id=?", (child["job_id"],))
        db.execute("UPDATE jobs SET status='retryable_failed',available_after=0,updated_at=? WHERE job_id=?", (created, job_id))
        db.execute("UPDATE series_results SET status='retryable_failed' WHERE job_id=?", (job_id,))
        return {"status": "queued", "replacement_job_id": job_id, "next_attempt": parent["attempts"] + 1,
                "max_attempts": max_attempts, "superseded_job_ids": [child["job_id"] for child in descendants],
                "replay_status": replay["status"], "replay_row_count": len(rows),
                "request_sha256": request_hash, "source_response_sha256": response_hash,
                "previous_parent_job": parent, "audit_table": "job_supersessions"}


def _write_observations(path, rows):
    frame = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
    for column, dtype in OBSERVATION_COLUMNS.items():
        frame[column] = frame[column].astype(dtype)
    frame.to_parquet(path, index=False)


def run_jobs(database, output=None, *, max_jobs=1, drain=False, timeout=45, max_attempts=3,
             retry_base_seconds=30, min_interval_seconds=1, max_seconds=43200,
             fetch=fetch_public, sleeper=time.sleep, progress=None):
    if max_jobs is not None and (type(max_jobs) is not int or max_jobs < 1):
        raise ValueError("max_jobs must be positive or None")
    budgets = (timeout, retry_base_seconds, min_interval_seconds, max_seconds)
    if not all(type(value) in (int, float) and math.isfinite(value) for value in budgets) or type(max_attempts) is not int or not 1 <= timeout <= 180 or not 1 <= max_attempts <= 10 or retry_base_seconds < 0 or min_interval_seconds < 0 or max_seconds <= 0:
        raise ValueError("Invalid collector budgets")
    if not Path(database).exists():
        raise ValueError("Plan a queue before running")
    output = Path(output or Path(database).parent / "artifacts").resolve()
    started, processed = time.monotonic(), 0
    limit = max_jobs if max_jobs is not None else 1_000_000 if drain else 1
    while processed < limit and time.monotonic() - started < max_seconds:
        job = claim_job(database, lease_seconds=timeout * 2 + 60, max_attempts=max_attempts, min_interval_seconds=min_interval_seconds)
        if job is None:
            report = status_report(database)
            outstanding = sum(report["jobs"][status] for status in ("pending", "running", "retryable_failed"))
            if not drain or not outstanding:
                break
            delay = max(.05, min(30., report["next_wake_at"] - time.time()))
            if progress:
                progress({"event": "waiting", "seconds": round(delay, 3), "outstanding_jobs": outstanding})
            sleeper(delay)
            continue
        attempt = output / job["job_id"] / f"attempt-{job['attempts']:03d}-{job['lease_token'][:12]}"
        attempt.mkdir(parents=True, exist_ok=False)
        result, error, split_specs, retry_at, cooldown = None, None, [], 0., 0.
        status, failure_code, http_status = "retryable_failed", None, None
        try:
            payload = request_payload(job)
            body = canonical(payload).encode()
            (attempt / "request.json").write_bytes(body)
            raw, http = fetch(body, timeout)
            (attempt / "response.json.gz").write_bytes(gzip.compress(raw, mtime=0))
            response_hash, request_hash = sha256_bytes(raw), sha256_bytes(body)
            info = {**http, "sha256": response_hash, "request_sha256": request_hash}
            atomic_json(attempt / "response_info.json", info)
            http_status = int(http["http_status"])
            delay = retry_after_seconds(http.get("retry_after"))
            if http_status == 429 or http_status in {401, 403, 503}:
                cooldown = time.time() + max(delay, retry_base_seconds if http_status != 403 else 300)
            if not 200 <= http_status < 300:
                if http_status in {400, 404, 405, 410, 413, 422}:
                    raise ValidationError(f"EVDS HTTP {http_status}", "HTTP_REQUEST_REJECTED")
                raise OSError(f"EVDS HTTP {http_status}")
            rows, result = validate_response(raw, job)
            for row in rows:
                row.update(native_frequency=job["frequency"], source_request_file=str(attempt / "request.json"),
                           source_request_sha256=request_hash, source_response_file=str(attempt / "response.json.gz"),
                           source_response_sha256=response_hash, missing_kind="observed" if row["value"] is not None else "source_null_unresolved",
                           is_unresolved_missing=row["value"] is None)
            _write_observations(attempt / "observations.parquet", rows)
            status = result["status"]
            atomic_json(attempt / "validation.json", result)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failure_code = getattr(exc, "code", "TRANSPORT_OR_IO_ERROR")
            if isinstance(exc, (ValidationError, ValueError)):
                split_specs = _split_specs(job, date_split=failure_code in {"ROW_LIMIT", "TRUNCATED_RESPONSE", "RESPONSE_TOO_LARGE"})
                status = "split" if split_specs else "permanent_failed"
            elif job["attempts"] >= max_attempts:
                split_specs = _split_specs(job) if http_status not in {401, 403, 429, 503} else []
                status = "split" if split_specs else "permanent_failed"
            else:
                status = "retryable_failed"
            result = {"status": status, "errors": [{"code": failure_code, "message": error}],
                      "series_results": {code: {"status": status, "error": error, "error_code": failure_code} for code in _codes(job)}}
            atomic_json(attempt / "validation.json", result)
            retry_at = max(time.time() + retry_base_seconds * 2 ** (job["attempts"] - 1), cooldown) if status == "retryable_failed" else 0
        finish_job(database, job, status, result=result, error=error, artifact_path=attempt,
                   available_after=retry_at, split_specs=split_specs, cooldown_until=cooldown)
        processed += 1
        if progress:
            progress({"event": "job_completed", "job_id": job["job_id"], "series_count": len(_codes(job)),
                      "frequency": job["frequency"], "start_date": job["start_date"], "end_date": job["end_date"],
                      "attempt": job["attempts"], "status": status, "http_status": http_status,
                      "numeric_series": sum(v.get("target_numeric_count", 0) > 0 for v in (result or {}).get("series_results", {}).values()),
                      "error_code": failure_code, "processed": processed})
    return {"processed_jobs": processed, "elapsed_seconds": round(time.monotonic() - started, 3), "queue": status_report(database)}


def status_report(database):
    with connect(database) as db:
        statuses = {status: 0 for status in STATUSES}
        statuses.update(dict(db.execute("SELECT status,count(*) FROM jobs GROUP BY status")))
        next_ready = db.execute("SELECT min(available_after) FROM jobs WHERE status IN ('pending','retryable_failed')").fetchone()[0]
        next_lease = db.execute("SELECT min(lease_until) FROM jobs WHERE status='running'").fetchone()[0]
        rate = db.execute("SELECT next_allowed FROM rate_limit WHERE id=1").fetchone()[0]
        wake_options = ([max(next_ready, rate)] if next_ready is not None else []) + ([next_lease] if next_lease is not None else [])
        next_wake = min(wake_options) if wake_options else time.time()
        return {"database": str(Path(database).resolve()), "configuration": dict(db.execute("SELECT key,value FROM configuration")),
                "metadata_series": db.execute("SELECT count(*) FROM series").fetchone()[0], "jobs": statuses,
                "job_count": sum(statuses.values()), "attempted_requests": db.execute("SELECT coalesce(sum(attempts),0) FROM jobs").fetchone()[0],
                "numeric_series": db.execute("SELECT count(DISTINCT series_code) FROM series_results WHERE status='succeeded'").fetchone()[0],
                "series_interval_statuses": dict(db.execute("SELECT status,count(*) FROM series_results GROUP BY status")),
                "next_wake_at": next_wake, "completed_requests_are_not_complete_numeric_coverage": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run", "status", "retry-validated-batch"):
        child = commands.add_parser(command)
        child.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
        if command == "plan":
            child.add_argument("--catalog", type=Path, default=ROOT / "data_pipeline/catalog/evds_series_catalog.parquet")
            child.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
            child.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 30))
            child.add_argument("--batch-size", type=int, default=20)
            child.add_argument("--group", action="append")
            child.add_argument("--series", action="append")
        elif command == "retry-validated-batch":
            child.add_argument("--job-id", required=True)
            child.add_argument("--max-attempts", type=int, default=3)
        elif command == "run":
            child.add_argument("--output", type=Path)
            child.add_argument("--max-jobs", type=int)
            child.add_argument("--drain", action="store_true")
            child.add_argument("--timeout", type=int, default=45)
            child.add_argument("--max-attempts", type=int, default=3)
            child.add_argument("--retry-base-seconds", type=float, default=30)
            child.add_argument("--min-interval-seconds", type=float, default=1)
            child.add_argument("--max-seconds", type=float, default=43200)
    args = parser.parse_args()
    emit = lambda value: print(canonical(value), flush=True)
    try:
        if args.command == "plan":
            result = plan(args.database, args.catalog, args.start, args.end, batch_size=args.batch_size, groups=args.group, series_codes=args.series)
        elif args.command == "retry-validated-batch":
            result = retry_validated_batch(args.database, args.job_id, max_attempts=args.max_attempts)
        elif args.command == "run":
            result = run_jobs(args.database, args.output, max_jobs=args.max_jobs, drain=args.drain, timeout=args.timeout,
                              max_attempts=args.max_attempts, retry_base_seconds=args.retry_base_seconds,
                              min_interval_seconds=args.min_interval_seconds, max_seconds=args.max_seconds, progress=emit)
        else:
            result = status_report(args.database)
        emit(result)
        report = result.get("queue", result)
        return 1 if args.command == "run" and report["jobs"]["permanent_failed"] else 0
    except KeyboardInterrupt:
        emit({"event": "interrupted", "message": "In-flight lease remains durable and will be recovered after expiry.", "database": str(args.database)})
        return 130
    except Exception as exc:
        emit({"event": "failed", "error_type": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
