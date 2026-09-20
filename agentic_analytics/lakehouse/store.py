"""Local immutable lakehouse releases, explicit CSV overlays and analysis revisions.

This is a trusted Python API, not an endpoint accepting model-selected paths or SQL.
Only store-generated identifiers are accepted when opening published artifacts.
One advisory lock serializes writers per workspace; readers pin immutable revisions.
There is deliberately no automatic deletion of assets referenced by older results.
"""

from __future__ import annotations

from contextlib import contextmanager
import csv
from decimal import Decimal, InvalidOperation
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable
import uuid

import duckdb
import numpy as np
import pandas as pd


class StoreError(ValueError):
    """Invalid contract, artifact or state transition."""


class VersionConflict(StoreError):
    """The caller attempted to update a workspace it has not read."""


def _canonical(value) -> bytes:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StoreError("Metadata must be finite, JSON-serializable values.") from exc
    if len(result) > 8 * 1024**2:
        raise StoreError("Metadata exceeds the 8 MiB size limit.")
    return result


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_source(source: Path, target: Path, limit: int) -> str:
    """Bound a copy and reject a source that changes or is replaced while read."""
    with source.open("rb") as original, target.open("xb") as output:
        before = os.fstat(original.fileno())
        if before.st_size > limit:
            raise StoreError("Source exceeds its size limit.")
        total = 0
        digest = hashlib.sha256()
        while block := original.read(min(1024 * 1024, limit - total + 1)):
            total += len(block)
            if total > limit:
                raise StoreError("Source exceeds its size limit.")
            output.write(block)
            digest.update(block)
        after = os.fstat(original.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev) or total != before.st_size:
            raise StoreError("Source changed during ingestion.")
    copied = digest.hexdigest()
    if file_sha256(source) != copied:
        raise StoreError("Source changed or was replaced during ingestion.")
    return copied


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value) -> None:
    with path.open("xb") as handle:
        handle.write(_canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _identifier(value: str, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,127}", value):
        raise StoreError("Invalid artifact identifier.")
    if prefix and not re.fullmatch(re.escape(prefix) + r"_[0-9a-f]{64}", value):
        raise StoreError("Invalid content-addressed identifier.")
    return value


class LakehouseStore:
    """A disk-backed store; independent instances and restarted readers interoperate."""

    def __init__(self, root, *, max_source_bytes=64 * 1024**2,
                 max_snapshot_bytes=2 * 1024**3, max_rows=1_000_000,
                 max_columns=256, max_result_bytes=256 * 1024**2):
        self.root = Path(root).expanduser().resolve()
        self.limits = {"max_source_bytes": max_source_bytes,
                       "max_snapshot_bytes": max_snapshot_bytes, "max_rows": max_rows,
                       "max_columns": max_columns, "max_result_bytes": max_result_bytes}
        if any(type(value) is not int or value <= 0 for value in self.limits.values()):
            raise StoreError("Resource limits must be positive integers.")
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("snapshots", "datasets", "analyses", "workspaces", ".staging", ".locks"):
            self._path(name).mkdir(exist_ok=True)

    def _path(self, *parts) -> Path:
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise StoreError("Store path escapes its root.")
        current = self.root
        for part in path.relative_to(self.root).parts:
            current /= part
            if current.is_symlink():
                raise StoreError("Symlinks are not allowed inside the store.")
        return path

    @contextmanager
    def _lock(self, workspace_id):
        path = self._path(".locks", _identifier(workspace_id) + ".lock")
        with path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _stage(self):
        path = Path(tempfile.mkdtemp(prefix="build-", dir=self._path(".staging")))
        try:
            yield path
        finally:
            if path.exists():
                shutil.rmtree(path)

    def _publish_object(self, stage: Path, kind: str, manifest: dict) -> dict:
        prefix = {"snapshots": "snapshot", "datasets": "dataset", "analyses": "analysis"}[kind]
        object_id = prefix + "_" + _digest(_canonical(manifest))
        manifest = {**manifest, prefix + "_id": object_id}
        _write_json(stage / "manifest.json", manifest)
        for path in stage.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            path.chmod(0o444)
        _fsync_directory(stage)
        destination = self._path(kind, object_id)
        # Two publishers may produce the same content. The winner's manifest and
        # payload are checked rather than replacing an already published object.
        try:
            os.rename(stage, destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} or not destination.is_dir():
                raise
            existing = self._manifest(kind, object_id)
            if existing != manifest:
                raise StoreError("Published object does not match its content identifier.")
            filename = {"snapshots": "data.duckdb", "datasets": "data.parquet",
                        "analyses": "data.parquet"}[kind]
            self._verified_payload(kind, object_id, filename)
            if kind == "datasets":
                self._verified_payload(kind, object_id, "source.csv")
        else:
            _fsync_directory(destination.parent)
        return manifest

    def _manifest(self, kind, object_id) -> dict:
        prefix = {"snapshots": "snapshot", "datasets": "dataset", "analyses": "analysis"}[kind]
        _identifier(object_id, prefix)
        try:
            value = json.loads(self._path(kind, object_id, "manifest.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("Artifact manifest is missing or invalid.") from exc
        if not isinstance(value, dict):
            raise StoreError("Artifact manifest must be an object.")
        content = {key: val for key, val in value.items() if key != prefix + "_id"}
        if value.get(prefix + "_id") != object_id or object_id != prefix + "_" + _digest(_canonical(content)):
            raise StoreError("Artifact manifest hash mismatch.")
        return value

    def _verified_payload(self, kind, object_id, filename) -> Path:
        manifest = self._manifest(kind, object_id)
        path = self._path(kind, object_id, filename)
        expected = manifest["source_sha256" if filename == "source.csv" else "data_sha256"]
        if not path.is_file() or file_sha256(path) != expected:
            raise StoreError("Artifact payload hash mismatch.")
        return path

    def publish_snapshot(self, source_db, validator: Callable | None = None) -> dict:
        """Copy a closed/checkpointed DB and validate before publishing its release.

        ``validator(staged_db_path)`` may run additional project-specific checks;
        it must raise on failure, return False, or an explicit failed status. Its JSON
        result is retained. It is trusted application code, never model input.
        """
        source = Path(source_db).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > self.limits["max_snapshot_bytes"]:
            raise StoreError("Snapshot source exceeds its size limit or is not a file.")
        wal = Path(str(source) + ".wal")
        if wal.exists() and wal.stat().st_size:
            raise StoreError("Checkpoint and close the source database before publishing.")
        with self._stage() as stage:
            target = stage / "data.duckdb"
            # DuckDB's read lock excludes a writer in another process. Source
            # hashes also detect mutation/replacement during the physical copy.
            with duckdb.connect(str(source), read_only=True):
                before = _copy_source(source, target, self.limits["max_snapshot_bytes"])
                if file_sha256(target) != before:
                    raise StoreError("Snapshot source changed during publication.")
                if wal.exists() and wal.stat().st_size:
                    raise StoreError("Source acquired an uncheckpointed WAL.")
            with duckdb.connect(str(target), read_only=True,
                                config={"enable_external_access": "false"}) as connection:
                tables = connection.execute(
                    "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                    "WHERE table_catalog = current_database() "
                    "ORDER BY table_schema, table_name"
                ).fetchall()
                if not any(kind == "BASE TABLE" for _, _, kind in tables):
                    raise StoreError("A published lakehouse must contain at least one table.")
                counts = []
                for schema, table, kind in tables:
                    quoted = '.'.join('"' + part.replace('"', '""') + '"' for part in (schema, table))
                    count = connection.execute("SELECT count(*) FROM " + quoted).fetchone()[0]
                    counts.append({"schema": schema, "table": table, "type": kind, "rows": count})
            additional = validator(target) if validator else None
            if additional is False or isinstance(additional, dict) and (
                    additional.get("passed") is False or additional.get("status") in {"failed", "error", "blocked"}):
                raise StoreError("Snapshot validator rejected the release.")
            if file_sha256(target) != before:
                raise StoreError("A snapshot validator must not change the database.")
            manifest = {"format_version": 1, "data_sha256": before,
                        "size_bytes": target.stat().st_size, "duckdb_version": duckdb.__version__,
                        "tables": counts, "validation": additional}
            return self._publish_object(stage, "snapshots", manifest)

    def snapshot_path(self, snapshot_id) -> Path:
        return self._verified_payload("snapshots", snapshot_id, "data.duckdb")

    def dataset_manifest(self, dataset_id) -> dict:
        return self._manifest("datasets", dataset_id)

    def overlay_path(self, dataset_id) -> Path:
        return self._verified_payload("datasets", dataset_id, "data.parquet")

    def raw_source_path(self, dataset_id) -> Path:
        return self._verified_payload("datasets", dataset_id, "source.csv")

    @staticmethod
    def _revision(manifest):
        return {**manifest, "revision_id": "revision_" + _digest(_canonical(manifest))}

    def create_workspace(self, snapshot_id, workspace_id=None, *, initial_dataset_ids=None,
                         shared_release_id=None) -> dict:
        self.snapshot_path(snapshot_id)
        workspace_id = _identifier(workspace_id or "workspace_" + uuid.uuid4().hex)
        if not isinstance(initial_dataset_ids, (list, tuple, type(None))):
            raise StoreError("Initial workspace datasets must be a bounded unique list.")
        datasets = list(initial_dataset_ids or [])
        if len(datasets) != len(set(datasets)) or len(datasets) > 1000:
            raise StoreError("Initial workspace datasets must be a bounded unique list.")
        for dataset_id in datasets:
            self.dataset_manifest(dataset_id)
            self.raw_source_path(dataset_id)
            self.overlay_path(dataset_id)
        if shared_release_id is not None:
            _identifier(shared_release_id, "release")
        with self._lock(workspace_id):
            destination = self._path("workspaces", workspace_id)
            if destination.exists():
                raise StoreError("Workspace already exists.")
            content = {"format_version": 1, "workspace_id": workspace_id,
                       "version": 0, "snapshot_id": snapshot_id, "datasets": datasets,
                       "analysis_head": None, "parent_revision_id": None}
            if shared_release_id is not None:
                content["shared_release_id"] = shared_release_id
            manifest = self._revision(content)
            with self._stage() as stage:
                (stage / "revisions").mkdir()
                _write_json(stage / "revisions" / (manifest["revision_id"] + ".json"), manifest)
                (stage / "revisions" / (manifest["revision_id"] + ".json")).chmod(0o444)
                _write_json(stage / "CURRENT.json", {"revision_id": manifest["revision_id"]})
                _fsync_directory(stage / "revisions")
                _fsync_directory(stage)
                os.rename(stage, destination)
                _fsync_directory(destination.parent)
        return manifest

    def workspace(self, workspace_id, revision_id=None) -> dict:
        _identifier(workspace_id)
        try:
            if revision_id is None:
                revision_id = json.loads(self._path("workspaces", workspace_id, "CURRENT.json").read_text())["revision_id"]
            _identifier(revision_id, "revision")
            value = json.loads(self._path("workspaces", workspace_id, "revisions", revision_id + ".json").read_text())
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise StoreError("Workspace revision is missing or invalid.") from exc
        if not isinstance(value, dict):
            raise StoreError("Workspace revision must be an object.")
        content = {key: val for key, val in value.items() if key != "revision_id"}
        if self._revision(content) != value or value["revision_id"] != revision_id or value["workspace_id"] != workspace_id:
            raise StoreError("Workspace revision hash mismatch.")
        return value

    def _expect(self, workspace_id, expected_version):
        current = self.workspace(workspace_id)
        if type(expected_version) is not int or current["version"] != expected_version:
            raise VersionConflict("Workspace changed; read its current revision before retrying.")
        return current

    def is_empty_domain_snapshot(self, snapshot_id):
        """Recognize only the application's metadata-only generic base."""
        with duckdb.connect(str(self.snapshot_path(snapshot_id)), read_only=True,
                            config={"enable_external_access": "false"}) as connection:
            tables = connection.execute("SELECT table_schema, table_name FROM information_schema.tables").fetchall()
            if set(tables) != {("main", "platform_metadata")}:
                return False
            columns = {row[0] for row in connection.execute("DESCRIBE platform_metadata").fetchall()}
            if not {"key", "value"}.issubset(columns):
                return False
            return connection.execute("SELECT value FROM platform_metadata WHERE key = 'profile'").fetchall() == [("generic",)]

    def attach_reference_catalogue(self, workspace_id, snapshot_id, catalogue_id, *, expected_version):
        """Pin an explicit trusted catalogue without changing imported assets.

        The application supplies allowed catalogue releases. Existing nonempty
        bases cannot be replaced here. Old revisions and analysis manifests keep
        their original snapshot, even after the workspace gains reference data.
        """
        _identifier(catalogue_id)
        self.snapshot_path(snapshot_id)
        with self._lock(workspace_id):
            current = self._expect(workspace_id, expected_version)
            if current["snapshot_id"] == snapshot_id:
                return current
            if not self.is_empty_domain_snapshot(current["snapshot_id"]):
                raise StoreError("Reference attachment cannot replace this workspace's existing pinned catalogue.")
            receipt = {"catalogue_id": catalogue_id, "snapshot_id": snapshot_id,
                       "previous_snapshot_id": current["snapshot_id"], "previous_revision_id": current["revision_id"]}
            return self._advance(current, snapshot_id=snapshot_id, reference_catalogue=receipt)

    def _advance(self, current, **updates):
        content = {key: value for key, value in current.items() if key != "revision_id"}
        content.update(updates, version=current["version"] + 1,
                       parent_revision_id=current["revision_id"])
        manifest = self._revision(content)
        directory = self._path("workspaces", current["workspace_id"])
        revision = self._path("workspaces", current["workspace_id"], "revisions", manifest["revision_id"] + ".json")
        if not revision.exists():
            _write_json(revision, manifest)
            revision.chmod(0o444)
            _fsync_directory(revision.parent)
        elif self.workspace(current["workspace_id"], manifest["revision_id"]) != manifest:
            raise StoreError("Existing workspace revision does not match the update.")
        pointer = directory / (".current-" + uuid.uuid4().hex + ".json")
        try:
            _write_json(pointer, {"revision_id": manifest["revision_id"]})
            os.replace(pointer, self._path("workspaces", current["workspace_id"], "CURRENT.json"))
            _fsync_directory(directory)
        finally:
            pointer.unlink(missing_ok=True)
        return manifest

    def _validate_contract(self, contract):
        contract = json.loads(_canonical(contract))
        if not isinstance(contract, dict):
            raise StoreError("Dataset contract must be an object.")
        columns = contract.get("columns")
        if not isinstance(columns, dict) or not 1 <= len(columns) <= self.limits["max_columns"]:
            raise StoreError("Explicit columns are required within the column limit.")
        if not isinstance(contract.get("name"), str) or not contract["name"].strip():
            raise StoreError("An explicit dataset name is required.")
        for name, spec in columns.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
                raise StoreError("CSV column identifiers must be simple ASCII identifiers.")
            if not isinstance(spec, dict) or spec.get("dtype") not in {"string", "integer", "float", "boolean", "date"}:
                raise StoreError("Every column requires a supported explicit dtype.")
            if any(not isinstance(spec.get(key), str) or not spec[key].strip() for key in ("unit", "kind")):
                raise StoreError("Every column requires explicit unit and kind.")
            if type(spec.get("nullable")) is not bool:
                raise StoreError("Every column requires an explicit nullable boolean.")
        key, grain = contract.get("key"), contract.get("grain")
        if (not isinstance(key, list) or not key or len(set(key)) != len(key)
                or any(column not in columns for column in key)):
            raise StoreError("An explicit unique key is required.")
        if not isinstance(grain, list) or set(grain) != set(key) or len(grain) != len(key):
            raise StoreError("Grain must explicitly name the unique key columns.")
        frequency = contract.get("frequency")
        if frequency not in {"daily", "business_daily", "weekly", "weekly_observed", "monthly", "quarterly", "annual", "event", "static"}:
            raise StoreError("Unsupported or missing native frequency.")
        date_column = contract.get("date_column")
        if frequency != "static" and (date_column not in columns or columns[date_column]["dtype"] != "date" or date_column not in key):
            raise StoreError("Time data requires a date column in its grain.")
        if frequency == "static" and date_column is not None:
            raise StoreError("Static datasets must not declare a time axis.")
        if "expected_rows" in contract and (type(contract["expected_rows"]) is not int
                or not 1 <= contract["expected_rows"] <= self.limits["max_rows"]):
            raise StoreError("Expected row count must be a positive integer within the row limit.")
        if "expected_periods" in contract:
            periods = contract["expected_periods"]
            if (frequency == "static" or not isinstance(periods, list) or not periods
                    or any(not isinstance(period, str) for period in periods)
                    or len(set(periods)) != len(periods)):
                raise StoreError("Expected periods must be distinct explicit time labels.")
            self._dates(pd.Series(periods, dtype="string"), frequency)
        return contract

    @staticmethod
    def _dates(series, frequency):
        nonnull = series.dropna()
        if frequency == "monthly":
            pattern, fmt = r"\d{4}-\d{2}", "%Y-%m"
        elif frequency == "quarterly":
            if not nonnull.str.fullmatch(r"\d{4}-Q[1-4]").all():
                raise StoreError("Quarterly dates must use YYYY-Qn.")
            try:
                pd.PeriodIndex(nonnull, freq="Q")
            except (ValueError, OverflowError) as exc:
                raise StoreError("Invalid quarterly date.") from exc
            return series
        elif frequency == "annual":
            pattern, fmt = r"\d{4}", "%Y"
        else:
            pattern, fmt = r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"
        if not nonnull.str.fullmatch(pattern).all():
            raise StoreError("Date values do not match the declared native frequency.")
        try:
            pd.to_datetime(nonnull, format=fmt, errors="raise")
        except (ValueError, OverflowError) as exc:
            raise StoreError("Invalid calendar date.") from exc
        return series

    def _read_csv(self, path, contract, *, return_source_order=False):
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, strict=True)
                header = next(reader, [])
                if len(header) != len(set(header)) or set(header) != set(contract["columns"]):
                    raise StoreError("CSV header must match the contract exactly, without duplicates.")
                for number, row in enumerate(reader, start=1):
                    if len(row) != len(header):
                        raise StoreError("Every CSV record must have exactly the declared number of columns.")
                    if number > self.limits["max_rows"]:
                        raise StoreError("CSV exceeds its row limit.")
        except (csv.Error, UnicodeError) as exc:
            raise StoreError("CSV is malformed or is not UTF-8 text.") from exc
        try:
            frame = pd.read_csv(path, dtype="string", encoding="utf-8-sig",
                                keep_default_na=False, na_values=[""],
                                nrows=self.limits["max_rows"] + 1, on_bad_lines="error")
        except (ValueError, pd.errors.ParserError) as exc:
            raise StoreError("CSV could not be parsed under its contract.") from exc
        if frame.empty or len(frame) > self.limits["max_rows"]:
            raise StoreError("CSV must contain observations within the row limit.")
        for column, spec in contract["columns"].items():
            series = frame[column]
            if not spec["nullable"] and series.isna().any():
                raise StoreError("Unexpected nulls in column " + column)
            try:
                if spec["dtype"] == "integer":
                    # Parsing through float can round integers above 2**53;
                    # UInt64 -> Int64 can silently wrap instead of raising.
                    exact = []
                    for raw in series:
                        if pd.isna(raw):
                            exact.append(pd.NA)
                            continue
                        number = Decimal(raw)
                        if (not number.is_finite() or number != number.to_integral_value()
                                or not -(2**63) <= number < 2**63):
                            raise StoreError("Integer value is fractional, non-finite or outside Int64 in " + column)
                        exact.append(int(number))
                    frame[column] = pd.Series(exact, index=series.index, dtype="Int64")
                elif spec["dtype"] == "float":
                    values = pd.to_numeric(series, errors="raise")
                    numbers = values.dropna().to_numpy(dtype=float)
                    if not np.isfinite(numbers).all():
                        raise StoreError("Non-finite numeric value in " + column)
                    frame[column] = values.astype("Float64")
                elif spec["dtype"] == "boolean":
                    if not series.dropna().isin(["true", "false"]).all():
                        raise StoreError("Boolean values must be true or false.")
                    frame[column] = series.map({"true": True, "false": False}).astype("boolean")
                elif spec["dtype"] == "date":
                    frame[column] = self._dates(series, contract["frequency"] if column == contract.get("date_column") else "daily")
            except (ValueError, TypeError, OverflowError, InvalidOperation) as exc:
                raise StoreError("Invalid value for declared type in " + column) from exc
        if frame[contract["key"]].isna().any().any() or frame.duplicated(contract["key"]).any():
            raise StoreError("Dataset grain contains a null or duplicate key.")
        if "expected_rows" in contract and len(frame) != contract["expected_rows"]:
            raise StoreError("CSV is partial or has unexpected rows for its contract.")
        if "expected_periods" in contract:
            axis = contract["date_column"]
            entities = [column for column in contract["key"] if column != axis]
            groups = frame.groupby(entities, dropna=False, sort=False) if entities else [(None, frame)]
            expected = set(contract["expected_periods"])
            if any(set(group[axis]) != expected for _, group in groups):
                raise StoreError("CSV has partial or unexpected period coverage for an entity.")
        ordered = frame[list(contract["columns"])].sort_values(contract["key"], kind="stable")
        # The input RangeIndex survives dtype conversion. Preserve its actual
        # permutation before reset_index so provenance follows stored values.
        source_order = [int(index) + 1 for index in ordered.index]
        result = ordered.reset_index(drop=True)
        return (result, source_order) if return_source_order else result

    def _write_frame(self, frame, path):
        if (not isinstance(frame, pd.DataFrame) or len(frame) > self.limits["max_rows"]
                or not 1 <= len(frame.columns) <= self.limits["max_columns"]
                or frame.columns.has_duplicates):
            raise StoreError("Result must be a DataFrame within row/column limits, without duplicate columns.")
        if any(not isinstance(name, str) or not name or len(name) > 128 or any(ord(c) < 32 for c in name) for name in frame.columns):
            raise StoreError("Result column names must be bounded, printable strings.")
        if frame.memory_usage(deep=True).sum() > self.limits["max_result_bytes"]:
            raise StoreError("Result exceeds its memory size limit.")
        for column in frame.select_dtypes(include="number"):
            if not np.isfinite(frame[column].dropna().to_numpy(dtype=float)).all():
                raise StoreError("Result has non-finite numeric values.")
        frame.to_parquet(path, index=False, engine="pyarrow")
        if path.stat().st_size > self.limits["max_result_bytes"]:
            raise StoreError("Serialized result exceeds its size limit.")
        # Verify serialized bytes can be read before making the object reachable.
        restored = pd.read_parquet(path)
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), restored, check_dtype=True)

    def ingest_csv(self, workspace_id, source_path, contract, *, expected_version) -> dict:
        """Validate an explicitly described CSV and atomically add it to a workspace."""
        _identifier(workspace_id)
        contract = self._validate_contract(contract)
        source = Path(source_path).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > self.limits["max_source_bytes"]:
            raise StoreError("CSV source exceeds its size limit or is not a file.")
        with self._stage() as stage:
            raw = stage / "source.csv"
            source_hash = _copy_source(source, raw, self.limits["max_source_bytes"])
            frame, source_order = self._read_csv(raw, contract, return_source_order=True)
            provenance = contract.get("document_provenance")
            if isinstance(provenance, dict):
                for field in ("cell_origins", "row_origins"):
                    if field not in provenance:
                        continue
                    origins = provenance[field]
                    if not isinstance(origins, list) or len(origins) != len(frame) or any(not isinstance(origin, dict) for origin in origins):
                        raise StoreError("Document row provenance must align with every input CSV record: " + field)
                    provenance[field] = [origins[index - 1] for index in source_order]
                provenance["row_order"] = {"operation": "stable_sort_after_contract_dtype_conversion",
                    "sorted_by": list(contract["key"]), "stored_row_to_source_csv_row": source_order,
                    "source_row_numbering": "one_based_data_records_excluding_csv_header",
                    "cell_origins_order": "stored_dataset_rows", "source_csv_sha256": source_hash}
            parquet = stage / "data.parquet"
            self._write_frame(frame, parquet)
            manifest = {"format_version": 1, "contract": contract,
                        "source_sha256": source_hash, "data_sha256": file_sha256(parquet),
                        "row_count": len(frame), "columns": list(frame.columns),
                        "completeness": "verified_explicit_expectation" if any(
                            field in contract for field in ("expected_rows", "expected_periods")) else "not_asserted"}
            with self._lock(workspace_id):
                current = self._expect(workspace_id, expected_version)
                published = self._publish_object(stage, "datasets", manifest)
                if published["dataset_id"] in current["datasets"]:
                    return current
                return self._advance(current, datasets=[*current["datasets"], published["dataset_id"]])

    def save_analysis(self, workspace_id, frame, plan, lineage, *, schema=None,
                      parent_analysis_id=None, expected_version) -> dict:
        """Persist result + recipe, then move the workspace head with compare-and-swap.

        A parent's content is never updated. Revisions require that parent to be
        the workspace's current analysis head; concurrent stale writers fail.
        ``workspace_revision_id`` records the exact input revision, not the new head.
        """
        _identifier(workspace_id)
        plan, lineage = json.loads(_canonical(plan)), json.loads(_canonical(lineage))
        if schema is None:
            schema = {name: {"dtype": str(dtype)} for name, dtype in frame.dtypes.items()}
        schema = json.loads(_canonical(schema))
        with self._stage() as stage:
            parquet = stage / "data.parquet"
            self._write_frame(frame, parquet)
            with self._lock(workspace_id):
                current = self._expect(workspace_id, expected_version)
                if parent_analysis_id is not None:
                    _identifier(parent_analysis_id, "analysis")
                    if parent_analysis_id != current["analysis_head"]:
                        raise VersionConflict("Revision parent is not the current analysis head.")
                    parent = self._manifest("analyses", parent_analysis_id)
                    if parent["workspace_id"] != workspace_id:
                        raise StoreError("Revision parent belongs to another workspace.")
                # Check that referenced source artifacts still match their hashes.
                self.snapshot_path(current["snapshot_id"])
                for dataset_id in current["datasets"]:
                    self.overlay_path(dataset_id)
                manifest = {"format_version": 1, "workspace_id": workspace_id,
                            "workspace_revision_id": current["revision_id"],
                            "snapshot_id": current["snapshot_id"], "datasets": current["datasets"],
                            "parent_analysis_id": parent_analysis_id, "plan": plan,
                            "lineage": lineage, "schema": schema, "row_count": len(frame),
                            "data_sha256": file_sha256(parquet),
                            "new_workspace_version": current["version"] + 1}
                published = self._publish_object(stage, "analyses", manifest)
                self._advance(current, analysis_head=published["analysis_id"])
                return {**published, "result_path": str(self._path("analyses", published["analysis_id"], "data.parquet"))}

    def load_analysis(self, analysis_id) -> tuple[pd.DataFrame, dict]:
        manifest = self._manifest("analyses", analysis_id)
        path = self._verified_payload("analyses", analysis_id, "data.parquet")
        return pd.read_parquet(path), {**manifest, "result_path": str(path)}
