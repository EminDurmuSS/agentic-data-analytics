"""Deterministic row selection over immutable saved analyses.

This tool covers questions such as "which months satisfy both conditions?",
"show the largest absolute change", and "which source rows disagree?" without
turning a filtering task into an unrelated statistical model.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd

from agentic_analytics.lakehouse.store import StoreError


class SelectionError(ValueError):
    def __init__(self, message, code="INVALID_SELECTION_REQUEST"):
        super().__init__(message)
        self.code = code


_COMPARISONS = {"lt", "lte", "eq", "ne", "gte", "gt"}
_NULL_TESTS = {"is_null", "not_null"}


def _json(value):
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if value is pd.NA or value is pd.NaT or value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    return value


def _column_name(value, *, label="column"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}|period", value):
        raise SelectionError(f"{label} must name an existing saved column.")
    return value


def _numeric(series):
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


class AnalysisSelectionTools:
    """Read-only predicates and ranking with content-addressed result records."""

    def __init__(self, store, workspace_id, *, max_rows=10000):
        self.store, self.workspace_id = store, workspace_id
        self.store.workspace(workspace_id)
        self.max_rows = max_rows
        self.root = store.root / "selections" / workspace_id
        self.root.mkdir(parents=True, exist_ok=True)

    def _analysis(self, analysis_id):
        frame, manifest = self.store.load_analysis(analysis_id)
        if manifest.get("workspace_id") != self.workspace_id:
            raise SelectionError("Analysis belongs to another workspace.", "WORKSPACE_MISMATCH")
        if not 1 <= len(frame) <= self.max_rows:
            raise SelectionError("Analysis exceeds the row-selection budget.", "ROW_LIMIT")
        if frame.columns.has_duplicates:
            raise SelectionError("Analysis has duplicate columns.", "AMBIGUOUS_GRAIN")
        return frame, manifest

    @staticmethod
    def _compatible_columns(frame, schema, left, right, op):
        left_numeric, right_numeric = _numeric(frame[left]), _numeric(frame[right])
        if left_numeric != right_numeric:
            raise SelectionError("Compared columns must have compatible stored types.", "INCOMPATIBLE_COLUMNS")
        if not left_numeric:
            return None
        left_meta, right_meta = schema.get(left, {}), schema.get(right, {})
        facets = ("kind", "unit", "scale", "currency", "price_basis")
        if any(left_meta.get(key) != right_meta.get(key) for key in facets):
            raise SelectionError(
                "Numeric column comparisons require matching kind, unit, scale, currency and price basis.",
                "INCOMPATIBLE_COLUMNS",
            )
        basis_differs = left_meta.get("measurement_basis") != right_meta.get("measurement_basis")
        if basis_differs and op not in {"eq", "ne"}:
            raise SelectionError(
                "Ordered numeric comparisons require matching measurement bases.",
                "INCOMPATIBLE_COLUMNS",
            )
        differences = {}
        for facet in ("measurement_basis", "scope"):
            if left_meta.get(facet) != right_meta.get(facet):
                differences[facet] = {left: _json(left_meta.get(facet)), right: _json(right_meta.get(facet))}
        if differences:
            return {
                "code": "NUMERIC_EQUALITY_ONLY_ACROSS_DISTINCT_SOURCE_CONTRACTS",
                "message": (
                    "Equality or mismatch is evaluated only on stored values. Different source scopes or measurement "
                    "bases remain distinct and numerical equality does not establish semantic identity."
                ),
                "columns": [left, right],
                "differences": differences,
            }
        return None

    @staticmethod
    def _constant(series, value):
        if _numeric(series):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise SelectionError("Numeric columns require a finite numeric comparison value.", "INCOMPATIBLE_VALUE")
            return value
        if pd.api.types.is_bool_dtype(series):
            if type(value) is not bool:
                raise SelectionError("Boolean columns require a boolean comparison value.", "INCOMPATIBLE_VALUE")
            return value
        if not isinstance(value, str):
            raise SelectionError("Text and period columns require a string comparison value.", "INCOMPATIBLE_VALUE")
        return value

    def select_analysis_rows(self, analysis_id, filters, *, columns=None, sort=None, limit=100):
        frame, manifest = self._analysis(analysis_id)
        if not isinstance(filters, list) or not 1 <= len(filters) <= 12:
            raise SelectionError("Choose 1 to 12 row filters.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise SelectionError("limit must be an integer between 1 and 500.")
        available = list(frame.columns)
        if columns is None:
            columns = available
        if (not isinstance(columns, list) or not 1 <= len(columns) <= 25
                or len(set(columns)) != len(columns)):
            raise SelectionError("Choose 1 to 25 distinct output columns.")
        for column in columns:
            _column_name(column)
            if column not in frame:
                raise SelectionError("Output column does not exist.", "COLUMN_NOT_FOUND")

        schema = manifest.get("schema") or {}
        mask = pd.Series(True, index=frame.index, dtype=bool)
        normalized_filters = []
        comparison_caveats = []
        for item in filters:
            if not isinstance(item, dict):
                raise SelectionError("Every filter must be an object.")
            column = _column_name(item.get("column"))
            if column not in frame:
                raise SelectionError("Filter column does not exist.", "COLUMN_NOT_FOUND")
            op = item.get("op")
            if op in _NULL_TESTS:
                if set(item) != {"column", "op"}:
                    raise SelectionError("Null filters accept only column and op.")
                current = frame[column].isna() if op == "is_null" else frame[column].notna()
                normalized_filters.append({"column": column, "op": op})
            elif op in _COMPARISONS:
                keys = set(item)
                has_value, has_column = "value" in item, "other_column" in item
                if keys - {"column", "op", "value", "other_column"} or has_value == has_column:
                    raise SelectionError("Comparison filters require exactly one of value or other_column.")
                left = frame[column]
                if has_column:
                    other = _column_name(item["other_column"], label="other_column")
                    if other not in frame or other == column:
                        raise SelectionError("other_column must name a distinct saved column.", "COLUMN_NOT_FOUND")
                    caveat = self._compatible_columns(frame, schema, column, other, op)
                    right = frame[other]
                    normalized = {"column": column, "op": op, "other_column": other}
                    if caveat:
                        normalized["comparison_caveat"] = caveat["code"]
                        comparison_caveats.append(caveat)
                    normalized_filters.append(normalized)
                else:
                    right = self._constant(left, item["value"])
                    normalized_filters.append({"column": column, "op": op, "value": _json(right)})
                complete = left.notna() & (right.notna() if isinstance(right, pd.Series) else True)
                operation = {
                    "lt": left.lt, "lte": left.le, "eq": left.eq,
                    "ne": left.ne, "gte": left.ge, "gt": left.gt,
                }[op]
                try:
                    current = complete & operation(right).fillna(False)
                except (TypeError, ValueError) as exc:
                    raise SelectionError("Comparison value is incompatible with the selected column.", "INCOMPATIBLE_VALUE") from exc
            else:
                raise SelectionError("Unknown filter operation.")
            mask &= current.astype(bool)

        selected = frame.loc[mask].copy()
        matched_count = len(selected)
        normalized_sort = None
        if sort is not None:
            if not isinstance(sort, dict) or set(sort) - {"column", "direction", "absolute"}:
                raise SelectionError("sort accepts column, direction and absolute only.")
            column = _column_name(sort.get("column"), label="sort.column")
            if column not in frame:
                raise SelectionError("Sort column does not exist.", "COLUMN_NOT_FOUND")
            direction = sort.get("direction", "asc")
            absolute = sort.get("absolute", False)
            if direction not in {"asc", "desc"} or type(absolute) is not bool:
                raise SelectionError("Sort direction must be asc/desc and absolute must be boolean.")
            if absolute and not _numeric(frame[column]):
                raise SelectionError("Absolute sorting requires a numeric column.", "NON_NUMERIC_COLUMN")
            selected["__selection_order"] = np.arange(len(selected))
            if absolute:
                selected["__selection_key"] = selected[column].abs()
                selected = selected.sort_values(
                    ["__selection_key", "__selection_order"],
                    ascending=[direction == "asc", True],
                    na_position="last",
                    kind="stable",
                ).drop(columns="__selection_key")
            else:
                selected = selected.sort_values(
                    [column, "__selection_order"],
                    ascending=[direction == "asc", True],
                    na_position="last",
                    kind="stable",
                )
            selected = selected.drop(columns="__selection_order")
            normalized_sort = {"column": column, "direction": direction, "absolute": absolute}
        selected = selected.loc[:, columns].head(limit)
        rows = [_json(row) for row in selected.to_dict("records")]
        selected_schema = {column: _json(schema.get(column, {"dtype": str(frame[column].dtype)})) for column in columns}
        payload = {
            "status": "ok",
            "method": "deterministic_row_selection",
            "analysis_id": analysis_id,
            "workspace_id": self.workspace_id,
            "provenance": {
                "snapshot_id": manifest.get("snapshot_id"),
                "data_sha256": manifest["data_sha256"],
                "lineage_ref": analysis_id,
            },
            "parameters": {"filters": normalized_filters, "columns": columns, "sort": normalized_sort, "limit": limit},
            "columns": columns,
            "schema": selected_schema,
            "rows": rows,
            "row_count": len(rows),
            "total_match_count": matched_count,
            "truncated": matched_count > len(rows),
            "warnings": [
                "Null values match only explicit is_null filters; comparisons never treat null as equal, unequal, zero or carried forward.",
                *comparison_caveats,
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        artifact_id = "selection_" + hashlib.sha256(encoded).hexdigest()
        target = self.root / (artifact_id + ".json")
        if not target.exists():
            fd, name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        preview = rows[:40]
        return {**payload, "rows": preview, "preview_truncated": len(rows) > len(preview),
                "selection_id": artifact_id, "artifact_id": artifact_id, "artifact_ref": artifact_id}

    def load_artifact(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r"selection_[a-f0-9]{64}", artifact_id):
            raise SelectionError("Invalid selection artifact identifier.")
        path = self.root / (artifact_id + ".json")
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != artifact_id.removeprefix("selection_"):
            raise SelectionError("Selection artifact hash mismatch.", "SELECTION_INTEGRITY_ERROR")
        payload = json.loads(encoded)
        _, manifest = self.store.load_analysis(payload["analysis_id"])
        if (payload.get("workspace_id") != self.workspace_id
                or manifest.get("workspace_id") != self.workspace_id
                or payload.get("provenance", {}).get("data_sha256") != manifest.get("data_sha256")):
            raise SelectionError("Selection source does not match this workspace.", "SELECTION_INTEGRITY_ERROR")
        return {**payload, "preview_truncated": False,
                "selection_id": artifact_id, "artifact_id": artifact_id, "artifact_ref": artifact_id}

    def extra_tools(self):
        column = {"type": "string", "pattern": "^(?:period|[A-Za-z][A-Za-z0-9_]{0,63})$", "maxLength": 64}
        null_filter = {"type": "object", "properties": {
            "column": column, "op": {"enum": sorted(_NULL_TESTS)},
        }, "required": ["column", "op"], "additionalProperties": False}
        comparison = {"type": "object", "properties": {
            "column": column, "op": {"enum": sorted(_COMPARISONS)},
            "value": {"type": ["string", "number", "boolean"]}, "other_column": column,
        }, "required": ["column", "op"], "oneOf": [
            {"required": ["value"], "not": {"required": ["other_column"]}},
            {"required": ["other_column"], "not": {"required": ["value"]}},
        ], "additionalProperties": False}
        parameters = {"type": "object", "properties": {
            "analysis_id": {"type": "string"},
            "filters": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"oneOf": [null_filter, comparison]}},
            "columns": {"type": "array", "minItems": 1, "maxItems": 25, "uniqueItems": True, "items": column},
            "sort": {"type": "object", "properties": {
                "column": column, "direction": {"enum": ["asc", "desc"]}, "absolute": {"type": "boolean"},
            }, "required": ["column"], "additionalProperties": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        }, "required": ["analysis_id", "filters"], "additionalProperties": False}

        def handler(arguments):
            try:
                return self.select_analysis_rows(**arguments)
            except (SelectionError, StoreError, ValueError, TypeError, KeyError, OSError) as exc:
                return {"status": "blocked", "code": getattr(exc, "code", "SELECTION_ERROR"), "message": str(exc)}

        return {"select_analysis_rows": {
            "schema": {"type": "function", "function": {
                "name": "select_analysis_rows",
                "description": (
                    "Filter, compare and rank rows already stored in a saved analysis. Use this for conditional month lists, "
                    "min/max rows, largest absolute changes and exact cross-column mismatches. It does not calculate correlation."
                ),
                "parameters": parameters,
            }},
            "handler": handler,
            "mutating": False,
        }}
