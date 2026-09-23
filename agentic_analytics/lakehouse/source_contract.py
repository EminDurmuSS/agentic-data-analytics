"""Verified source-row contracts: one uniform provenance shape for every source kind.

The codebase already carries real per-source-kind provenance -- EVDS observation
cells (``source_response_file``/``source_response_sha256``/``source_row_index``/
``series_code``/``period``), BDDK monthly/weekly/FinTurk cells (``source_file``/
``source_sha256``/``source_row_index``/``value_dimension``/``group_code``/...),
TUIK web bulletin cells (``source_csv_file``/``source_sheet``/``source_cell``/
``source_press_url``/...), and PDF document cells
(``agentic_analytics/agent/tools/documents.py``'s ``source_id``/``raw_sha256``/
``table_id``/row index). ``registry.py``'s per-binding ``provenance_columns`` and
``LakehouseService.explain_value()``'s ``source_cells`` already expose these, but
each source kind names its fields differently, so nothing downstream (a delivery
gate, an automated regression check) can assert "this number has a verified
source row" without knowing every source kind's private column names.

This module is that single normalization: given a metric binding (from
``agentic_analytics.lakehouse.registry``) and one raw provenance cell (one entry
of ``explain_value()``'s ``source_cells``, or an equivalent PDF/web document
cell), it produces one contract shape:

    {source_id, hash, url, page_or_sheet, table, row, column, period, unit,
     scope, value, complete, missing_fields}

No raw provenance data is invented: a field that cannot be found under any of
its known source-specific names is left ``None``, and the contract reports
itself ``complete: False`` with the missing field names rather than silently
omitting the gap (commit 12 uses ``complete`` as its delivery gate).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


CONTRACT_FIELDS = ("source_id", "hash", "url", "page_or_sheet", "table", "row",
                   "column", "period", "unit", "scope", "value")
# A contract with a None value is legitimate (e.g. a genuinely missing observation).
# page_or_sheet is legitimately absent for a plain time-series API cell (EVDS/BDDK/
# TUIK numeric observations are not paginated or sheet-based); it is required only
# where it is the row's actual locator (a PDF page, a spreadsheet sheet). It is
# therefore never required for "complete", but IS still populated whenever a
# source-specific field names it (see _PAGE_OR_SHEET_KEYS below).
REQUIRED_FOR_COMPLETE = tuple(field for field in CONTRACT_FIELDS if field not in {"value", "page_or_sheet"})

# Known field names for each contract concept, across every source kind this
# repo actually has: EVDS (evds.full_catalog / dataset-specific manifests),
# BDDK monthly/weekly/FinTurk, TUIK web bulletin, TBB reports, and the PDF/web
# document tool (agentic_analytics/agent/tools/documents.py).
_HASH_KEYS = ("source_sha256", "source_response_sha256", "source_csv_sha256", "raw_sha256")
_LOCATOR_KEYS = ("source_file", "source_response_file", "source_csv_file", "source_request_file")
_URL_KEYS = ("source_press_url", "source_download_url", "source_url",
            "official_series_url", "source_metadata_url", "methodology_source_url")
_PAGE_OR_SHEET_KEYS = ("source_sheet", "page_number", "page_numbers", "sheet")
_TABLE_KEYS = ("table_id", "table")
_ROW_KEYS = ("source_row_index", "row")
_COLUMN_KEYS = ("source_column_index", "value_dimension", "source_column", "column")
_SCOPE_KEYS = ("institution_scope", "geography_scope", "population_scope", "source_currency_group")


class SourceContractError(ValueError):
    """The supplied binding/cell cannot be normalized into a source-row contract."""


def _first(sources: list[dict], keys: tuple[str, ...]):
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def build_source_row_contract(binding: dict, cell: dict, *, column: str, period: str, value: Any) -> dict:
    """Normalize one source-specific provenance cell into the uniform contract.

    ``binding`` is a metric binding as produced by
    ``agentic_analytics.lakehouse.registry.build_bindings``/``get_bindings``
    (or, for a PDF/web document, the source manifest from
    ``DocumentsTool.source()``/table metadata -- any mapping carrying the same
    field names is accepted). ``cell`` is one raw provenance record: an entry
    of ``LakehouseService.explain_value()``'s ``source_cells``, or an
    equivalent document/table-cell record. Fields present on ``cell`` win over
    the same field present only on ``binding`` (a cell is the more specific,
    row-level source of truth).
    """
    if not isinstance(binding, dict) or not isinstance(cell, dict):
        raise SourceContractError("binding and cell must be mappings.")
    if not isinstance(column, str) or not column:
        raise SourceContractError("column must be a non-empty string.")
    if not isinstance(period, str) or not period:
        raise SourceContractError("period must be a non-empty string.")

    layers = [cell, binding]
    hash_value = _first(layers, _HASH_KEYS)
    locator = _first(layers, _LOCATOR_KEYS)
    url = _first(layers, _URL_KEYS)
    page_or_sheet = _first(layers, _PAGE_OR_SHEET_KEYS)
    table = _first(layers, _TABLE_KEYS)
    row = _first(layers, _ROW_KEYS)
    scope = _first(layers, _SCOPE_KEYS)
    unit = binding.get("unit")
    source_system = binding.get("source_system") or binding.get("scope_namespace")

    identity = {"source_system": source_system, "locator": locator, "hash": hash_value,
                "table": table, "row": row, "column": column, "period": period,
                "page_or_sheet": page_or_sheet}
    canonical_identity = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_id = "source_row_" + hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()[:32]

    contract = {"source_id": source_id, "hash": hash_value, "url": url, "page_or_sheet": page_or_sheet,
                "table": table, "row": row, "column": column, "period": period, "unit": unit,
                "scope": scope, "value": value}
    missing = [field for field in REQUIRED_FOR_COMPLETE if contract.get(field) is None]
    contract["complete"] = not missing
    contract["missing_fields"] = missing
    return contract


def validate_source_row_contract(contract: dict) -> dict:
    """Structural validation only every contract must pass to be persisted.

    This does not assert the underlying source was re-read (that remains a
    separate, explicit verification step) -- it asserts the contract itself
    is a well-formed, bounded record of the shape commit 12's delivery gate
    can rely on.
    """
    if not isinstance(contract, dict) or set(contract) != {*CONTRACT_FIELDS, "complete", "missing_fields"}:
        raise SourceContractError("Contract must contain exactly the defined fields.")
    if not re.fullmatch(r"source_row_[a-f0-9]{32}", contract["source_id"] or ""):
        raise SourceContractError("source_id must be a content-addressed source_row_<32 hex> identifier.")
    for field in ("hash", "url", "table", "column", "unit", "scope"):
        if contract[field] is not None and not isinstance(contract[field], str):
            raise SourceContractError(f"{field} must be a string or null.")
    if not isinstance(contract["period"], str) or not contract["period"]:
        raise SourceContractError("period must be a non-empty string.")
    if contract["row"] is not None and (isinstance(contract["row"], bool) or not isinstance(contract["row"], (int, str))):
        raise SourceContractError("row must be an int, string or null.")
    # A page number is numeric, a spreadsheet sheet name is a string; both are
    # legitimate row locators depending on source kind.
    if contract["page_or_sheet"] is not None and (isinstance(contract["page_or_sheet"], bool)
            or not isinstance(contract["page_or_sheet"], (int, str))):
        raise SourceContractError("page_or_sheet must be an int, string or null.")
    if type(contract["complete"]) is not bool:
        raise SourceContractError("complete must be a boolean.")
    if not isinstance(contract["missing_fields"], list) or any(
            field not in REQUIRED_FOR_COMPLETE for field in contract["missing_fields"]):
        raise SourceContractError("missing_fields must list only real required-field names.")
    if contract["complete"] != (not contract["missing_fields"]):
        raise SourceContractError("complete must agree with missing_fields.")
    return contract


class SourceRowContractLedger:
    """An append-only, hash-verified, content-addressed store of source-row contracts.

    Mirrors the atomic-write and content-addressing conventions already used by
    ``agentic_analytics/lakehouse/store.py`` (temp file + ``os.replace``, a
    content hash embedded in the identifier, never silently overwritten) so
    persisted contracts share the same integrity guarantees as the rest of the
    lakehouse artifacts. One JSON file per contract, named by its ``source_id``.
    """

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, source_id: str) -> Path:
        if not re.fullmatch(r"source_row_[a-f0-9]{32}", source_id or ""):
            raise SourceContractError("Invalid source_id.")
        return self.root / f"{source_id}.json"

    def persist(self, contract: dict) -> dict:
        """Write a validated contract; a byte-identical re-write is a no-op,
        a conflicting re-write under the same source_id is rejected (the
        identifier is content-addressed, so a conflict means a caller
        attempted to change an already-published verified row)."""
        validate_source_row_contract(contract)
        destination = self._path(contract["source_id"])
        payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        if destination.exists():
            if destination.read_bytes() != payload:
                raise SourceContractError("A different contract is already persisted under this source_id.")
            return contract
        fd, name = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
        return contract

    def get(self, source_id: str) -> dict | None:
        path = self._path(source_id)
        if not path.is_file():
            return None
        contract = json.loads(path.read_text(encoding="utf-8"))
        return validate_source_row_contract(contract)

    def exists(self, source_id: str) -> bool:
        return self._path(source_id).is_file()
