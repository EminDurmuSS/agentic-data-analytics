"""Acquire EVDS observations for any catalogued series, on demand.

This is the write side of the runtime on-demand overlay described in
agentic_analytics/lakehouse/registry.py (ON_DEMAND_DB_PATH, get_bindings).
It reuses the existing audited path — catalog-validated manifest, then the
EVDS manifest downloader that preserves raw requests/responses and SHA-256
lineage — and merges the resulting observations into a small, independently
writable DuckDB file that the read-only application connection attaches
without ever touching the immutable lakehouse snapshot.

Design notes (see the architecture discussion this implements):
- No file lock is taken. The overlay file is only ever replaced, never
  edited in place: a full merge is built in a fresh scratch DuckDB file and
  then atomically swapped into place with os.replace, exactly like
  data_pipeline/lakehouse/build_lakehouse.py does for the main snapshot.
  Any reader either sees the old, fully-written file or the new one, never
  a partial write. A concurrent acquire() racing another acquire() can lose
  an update (last writer wins) — acceptable for a fetch-and-cache layer that
  is not the audited bulk publication of record; it is not acceptable for
  the main snapshot, which this code never opens for writing.
- No lakehouse rebuild is triggered or required. The overlay is picked up
  by the next agentic_analytics.lakehouse.registry.get_bindings() call.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.EVDS_Manifest_Indirme_Araci import run as run_manifest_download
from tools.EVDS_Talep_Uzerine_Indirme_Araci import build_manifest, generated_dataset_id
from agentic_analytics.lakehouse.registry import ON_DEMAND_DB_PATH

DEFAULT_CATALOG = PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet"
# The shipped container's root filesystem is read-only and data_pipeline/lakehouse
# is bind-mounted read_only (see docker-compose.yml); ".lakehouse-runtime" is the
# one writable, persistent location (the agent-runtime volume). Both the raw
# download staging area and the overlay database itself must live there, not
# under data_pipeline/. OVERLAY_DB_PATH is imported from registry.py, the single
# source of truth for the path get_bindings() attaches at query time.
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".lakehouse-runtime" / "evds_on_demand"
OVERLAY_DB_PATH = ON_DEMAND_DB_PATH

OVERLAY_COLUMNS = ["series_code", "period", "period_start", "period_end", "value",
                   "is_missing", "missing_kind", "native_frequency", "dataset_id",
                   "fetched_at", "source_manifest_sha256", "source_response_sha256"]


class AcquisitionError(ValueError):
    """A machine-readable failure of an on-demand EVDS acquisition request."""

    def __init__(self, message: str, *, code: str = "ACQUISITION_FAILED"):
        super().__init__(message)
        self.code = code


def _load_catalog(catalog_path: Path) -> pd.DataFrame:
    if not catalog_path.exists():
        raise AcquisitionError(f"EVDS series catalog not found: {catalog_path}", code="CATALOG_MISSING")
    return pd.read_parquet(catalog_path)


def acquire_evds_series(
    series_codes: list[str],
    start_date: str,
    end_date: str,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    overlay_path: Path = OVERLAY_DB_PATH,
    timeout: int = 60,
    retries: int = 3,
    delay: float = 0.25,
) -> dict[str, Any]:
    """Validate, download and publish EVDS series observations for on-demand use.

    Returns a summary dict; raises AcquisitionError for any input the audited
    downloader would otherwise reject (unknown code, archived series without
    review, empty selection, ...).
    """
    if not isinstance(series_codes, list) or not series_codes:
        raise AcquisitionError("series_codes must be a non-empty list", code="INVALID_INPUT")
    codes = [str(code).strip() for code in series_codes]
    if any(not code for code in codes):
        raise AcquisitionError("series_codes must not contain empty values", code="INVALID_INPUT")

    catalog = _load_catalog(catalog_path)
    dataset_id = generated_dataset_id(codes, start_date, end_date)
    output_dir = output_root / dataset_id.rsplit(".", 1)[-1]
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_manifest(catalog, codes, start_date, end_date, dataset_id, allow_archive=False)
    except ValueError as exc:
        raise AcquisitionError(str(exc), code="CATALOG_VALIDATION_FAILED") from exc
    manifest_path = output_dir / "generated_manifest.json"
    import json
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = run_manifest_download(SimpleNamespace(
        manifest=manifest_path, catalog=catalog_path, output=output_dir,
        timeout=timeout, retries=retries, delay=delay, transport="curl" if _has_curl() else "urllib",
        update_existing=True,
    ))
    if exit_code != 0:
        raise AcquisitionError("EVDS manifest download did not complete successfully", code="DOWNLOAD_FAILED")

    observations_path = output_dir / "observations_long.parquet"
    if not observations_path.exists():
        raise AcquisitionError("Download completed but produced no observations file", code="DOWNLOAD_FAILED")
    observations = pd.read_parquet(observations_path)

    manifest_sha256 = _sha256_file(manifest_path)
    merged = _prepare_overlay_frame(observations, dataset_id, manifest_sha256)
    publish_summary = publish_overlay(merged, overlay_path=overlay_path)

    return {
        "status": "acquired",
        "dataset_id": dataset_id,
        "series_codes": codes,
        "start_date": start_date,
        "end_date": end_date,
        "output_dir": str(output_dir),
        "overlay_path": str(overlay_path),
        "observation_count": int(len(merged)),
        "non_missing_observation_count": int((~merged["is_missing"]).sum()),
        **publish_summary,
    }


def _has_curl() -> bool:
    import shutil
    return shutil.which("curl") is not None


def _sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_overlay_frame(observations: pd.DataFrame, dataset_id: str, manifest_sha256: str) -> pd.DataFrame:
    frame = observations.copy()
    frame["dataset_id"] = dataset_id
    frame["fetched_at"] = pd.Timestamp.utcnow().tz_localize(None)
    frame["source_manifest_sha256"] = manifest_sha256
    if "source_response_sha256" not in frame.columns:
        frame["source_response_sha256"] = None
    if "missing_kind" not in frame.columns:
        frame["missing_kind"] = None
    return frame[OVERLAY_COLUMNS]


def publish_overlay(frame: pd.DataFrame, *, overlay_path: Path = OVERLAY_DB_PATH) -> dict[str, Any]:
    """Atomically merge new observations into the on-demand overlay database.

    The whole overlay is rewritten into a fresh scratch file (existing rows
    for series_code/period pairs not present in `frame` are preserved; rows
    for pairs present in `frame` are replaced by the newer fetch) and then
    swapped into place with os.replace. No lock is required: only one writer
    process is expected to call this at a time (the acquisition tool serializes
    calls), and readers always see a complete file, old or new.
    """
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    fd, scratch_name = tempfile.mkstemp(prefix=".on_demand.", suffix=".duckdb", dir=str(overlay_path.parent))
    os.close(fd)
    scratch_path = Path(scratch_name)
    scratch_path.unlink(missing_ok=True)
    try:
        connection = duckdb.connect(str(scratch_path))
        try:
            connection.execute("CREATE SCHEMA evds")
            connection.register("_new_observations", frame)
            if overlay_path.exists():
                connection.execute(f"ATTACH '{overlay_path}' AS existing (READ_ONLY)")
                connection.execute("""
                    CREATE TABLE evds.on_demand_observations AS
                    SELECT * FROM existing.evds.on_demand_observations
                    WHERE (series_code, period) NOT IN (SELECT series_code, period FROM _new_observations)
                    UNION ALL
                    SELECT * FROM _new_observations
                """)
                connection.execute("DETACH existing")
            else:
                connection.execute("CREATE TABLE evds.on_demand_observations AS SELECT * FROM _new_observations")
            connection.unregister("_new_observations")
            total = connection.execute("SELECT count(*) FROM evds.on_demand_observations").fetchone()[0]
            series_total = connection.execute("SELECT count(DISTINCT series_code) FROM evds.on_demand_observations").fetchone()[0]
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        _fsync_file(scratch_path)
        os.replace(scratch_path, overlay_path)
        _fsync_dir(overlay_path.parent)
    finally:
        scratch_path.unlink(missing_ok=True)
        Path(str(scratch_path) + ".wal").unlink(missing_ok=True)
    return {"overlay_total_observation_count": int(total), "overlay_total_series_count": int(series_total)}


def _fsync_file(path: Path) -> None:
    if os.name == "nt":
        # Windows cannot fsync a read-only-opened handle; os.replace is atomic
        # at the NTFS level regardless, so durability here is best-effort only.
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        # Directory handles cannot be fsync'd on Windows; os.replace is already
        # atomic at the filesystem level there, so this is a best-effort no-op.
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
