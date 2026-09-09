"""Deterministic, typed agent tools over immutable lakehouse snapshots.

The request language contains metric identifiers and operations, never SQL or paths.
Model integration can call these methods directly without owning database access.
"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

import duckdb
import numpy as np
import pandas as pd

from tools.lakehouse_store import LakehouseStore, StoreError, VersionConflict


class PlanError(ValueError):
    """A machine-readable failure of a semantic or structural request contract."""

    def __init__(self, message: str, *, code: str = "INVALID_PLAN"):
        super().__init__(message)
        self.code = code


def error_envelope(error: Exception) -> dict:
    """Keep errors actionable for an agent without parsing human message text."""
    if isinstance(error, PlanError):
        code = error.code
    elif isinstance(error, VersionConflict):
        code = "VERSION_CONFLICT"
    elif isinstance(error, json.JSONDecodeError):
        code = "INVALID_JSON"
    elif isinstance(error, StoreError):
        code = "STORE_CONTRACT_ERROR"
    elif isinstance(error, duckdb.Error):
        code = "DATA_ACCESS_ERROR"
    elif isinstance(error, OSError):
        code = "IO_ERROR"
    else:
        code = "INVALID_INPUT"
    return {"status": "blocked", "errors": [{"code": code, "message": str(error)}]}


def _object(value: Any, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise PlanError(f"{label}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value) or value == "period":
        raise PlanError("Column names must be identifiers of at most 64 characters, excluding period")
    return value


def _identifier(value: str) -> str:
    # Registry identifiers are trusted application configuration, still quote them.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", value):
        raise PlanError("Invalid identifier in metric binding")
    return ".".join('"' + part + '"' for part in value.split("."))


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, pd.Period, date, datetime)):
        return str(value)
    return value


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold().replace("ı", "i")) if not unicodedata.combining(c))


_FREQUENCIES = {"monthly": "M", "quarterly": "Q", "weekly_friday": "W-FRI", "weekly_wednesday": "W-WED", "weekly": "W-FRI", "daily": "D", "business_daily": "B", "annual": "Y", "yearly": "Y"}


def _period(value: Any, frequency: str) -> pd.Period:
    pattern = r"\d{4}-\d{2}" if frequency == "monthly" else r"\d{4}-Q[1-4]" if frequency == "quarterly" else r"\d{4}" if frequency in {"annual", "yearly"} else r"\d{4}-\d{2}-\d{2}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PlanError(f"Expected {frequency} period, got {value!r}")
    try:
        period = pd.Period(value, freq=_FREQUENCIES[frequency])
        if frequency.startswith("weekly") and period.end_time.date().isoformat() != value:
            raise PlanError("Weekly bounds must be native week-ending dates")
        return period
    except (ValueError, KeyError) as exc:
        raise PlanError(f"Invalid period {value}") from exc


def _label(period: pd.Period) -> str:
    if period.freqstr.startswith("Q"):
        return str(period).replace("Q", "-Q")
    if period.freqstr.startswith("W"):
        return period.end_time.date().isoformat()
    return str(period)


class LakehouseService:
    """Six small tools: discover, describe, validate, execute, revise and explain."""

    def __init__(self, store: LakehouseStore, workspace_id: str):
        self.store = store
        self.workspace_id = workspace_id

    @contextmanager
    def _context(self, workspace: dict | None = None) -> Iterator[tuple[Any, dict, dict]]:
        from data_pipeline.lakehouse.registry import get_bindings

        workspace = workspace or self.store.workspace(self.workspace_id)
        connection = duckdb.connect(str(self.store.snapshot_path(workspace["snapshot_id"])), read_only=True)
        try:
            bindings = get_bindings(connection)
            # Overlays are application-validated manifests, not model-provided SQL.
            for dataset_id in workspace.get("datasets", []):
                manifest = self.store.dataset_manifest(dataset_id)
                contract = manifest["contract"]
                table = "overlay_" + re.sub(r"[^A-Za-z0-9_]", "_", dataset_id)
                connection.register(table, pd.read_parquet(self.store.overlay_path(dataset_id)))
                for column, definition in contract["columns"].items():
                    if definition["dtype"] not in {"integer", "float"} or column in contract["grain"]:
                        continue
                    metric_id = f"overlay:{dataset_id}:{column}"
                    kind = definition.get("kind", "unknown")
                    if kind not in {"stock", "flow", "count", "count_stock", "count_flow", "ratio", "rate", "index", "price"}:
                        kind = "unknown"
                    bindings[metric_id] = {
                        "metric_id": metric_id, "title": f"{contract['name']}: {column}",
                        "source_system": "SESSION_DATASET", "table": table,
                        "time_column": contract["date_column"], "value_column": column,
                        "filters": {}, "dimensions": {key: key for key in contract["grain"] if key != contract["date_column"]},
                        "native_frequency": contract["frequency"], "kind": kind,
                        "unit": definition["unit"], "scale": definition.get("scale", 1),
                        "currency": definition.get("currency"), "aggregation": definition.get("aggregation", "none"),
                        "source_base": "", "provenance_columns": [], "status": "ready" if kind != "unknown" else "review_required",
                        "notes": ["User-supplied explicit data contract"], "contract_version": "1",
                        "dataset_id": dataset_id, "source_sha256": manifest["source_sha256"],
                    }
            yield connection, bindings, workspace
        finally:
            connection.close()

    @staticmethod
    def _card(binding: dict) -> dict:
        fields = ("metric_id", "title", "source_system", "native_frequency", "kind", "unit", "scale", "currency", "status", "dimensions", "institution_scope", "geography_scope", "notes")
        return {key: binding.get(key) for key in fields}

    def discover(self, request: dict) -> dict:
        _object(request, {"query", "limit", "status"}, {"query"}, "discover")
        if not isinstance(request["query"], str) or len(request["query"]) > 300:
            raise PlanError("query must be a string of at most 300 characters")
        limit = request.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise PlanError("limit must be between 1 and 25")
        if request.get("status") is not None and (not isinstance(request["status"], str) or request["status"] not in {"ready", "review_required", "metadata_only", "no_numeric"}):
            raise PlanError("Unknown readiness status")
        terms = _fold(request["query"]).split()
        with self._context() as (_, bindings, workspace):
            matches = [b for b in bindings.values() if all(term in _fold(f"{b['metric_id']} {b.get('title', '')}") for term in terms) and (not request.get("status") or b["status"] == request["status"])]
            matches.sort(key=lambda b: (b["status"] != "ready", b["metric_id"]))
            return {"status": "ok", "snapshot_id": workspace["snapshot_id"], "total": len(matches), "metrics": [self._card(b) for b in matches[:limit]]}

    def describe(self, request: dict) -> dict:
        _object(request, {"metric_id"}, {"metric_id"}, "describe")
        if not isinstance(request["metric_id"], str):
            raise PlanError("metric_id must be a string")
        with self._context() as (_, bindings, _):
            if request["metric_id"] not in bindings:
                raise PlanError("Unknown metric_id", code="METRIC_NOT_FOUND")
            return {"status": "ok", "metric": copy.deepcopy(bindings[request["metric_id"]])}

    def _validate(self, plan: dict, bindings: dict) -> dict:
        _object(plan, {"start", "end", "frequency", "columns", "operations"}, {"start", "end", "frequency", "columns"}, "plan")
        frequency = plan["frequency"]
        if not isinstance(frequency, str) or frequency not in _FREQUENCIES:
            raise PlanError("Unsupported output frequency")
        start, end = _period(plan["start"], frequency), _period(plan["end"], frequency)
        if start > end or end.ordinal - start.ordinal > 5000:
            raise PlanError("Invalid or excessive period range")
        if not isinstance(plan["columns"], list) or not 1 <= len(plan["columns"]) <= 25:
            raise PlanError("Select between 1 and 25 metric columns")
        if not isinstance(plan.get("operations", []), list) or len(plan.get("operations", [])) > 50:
            raise PlanError("At most 50 operations are supported")
        schemas: dict[str, dict] = {}
        for selection in plan["columns"]:
            _object(selection, {"name", "metric_id", "dimensions", "alignment"}, {"name", "metric_id"}, "column")
            name = _name(selection["name"])
            if name in schemas:
                raise PlanError(f"Duplicate column {name}")
            metric_id = selection["metric_id"]
            if not isinstance(metric_id, str) or metric_id not in bindings:
                raise PlanError(f"Unknown metric_id {metric_id!r}", code="METRIC_NOT_FOUND")
            binding = bindings[metric_id]
            if binding["status"] in {"metadata_only", "no_numeric"}:
                raise PlanError(f"{metric_id}: {binding['status']}; observations must be acquired first", code="METADATA_ONLY" if binding["status"] == "metadata_only" else "NO_NUMERIC_VALUES")
            if binding.get("binding_available") is False or not binding.get("table") or not binding.get("time_column") or not binding.get("value_column"):
                raise PlanError(f"{metric_id}: no executable physical binding", code="NO_PHYSICAL_BINDING")
            dimensions = selection.get("dimensions", {})
            if not isinstance(dimensions, dict) or set(dimensions) != set(binding.get("dimensions", {})):
                raise PlanError(f"{metric_id}: specify exactly these dimensions: {sorted(binding.get('dimensions', {}))}")
            if any(value is None or isinstance(value, (dict, list, bool)) or not isinstance(value, (str, int, float)) or (isinstance(value, float) and not math.isfinite(value)) for value in dimensions.values()):
                raise PlanError("Dimension values must be finite scalar strings or numbers")
            alignment = selection.get("alignment", "native")
            native = binding["native_frequency"]
            rank = {"daily": 0, "business_daily": 0, "weekly_friday": 1, "weekly_wednesday": 1, "weekly": 1, "twice_monthly": 1, "monthly": 2, "quarterly": 3, "half_yearly": 4, "annual": 5, "yearly": 5}
            if native not in rank:
                raise PlanError(f"Unsupported native frequency {native}")
            if rank[native] > rank[frequency]:
                raise PlanError("Upsampling is forbidden; choose the source's native or a coarser frequency", code="INVALID_TEMPORAL_AGGREGATION")
            if not isinstance(alignment, str) or alignment not in {"native", "last", "mean", "sum"}:
                raise PlanError("Unknown alignment operation")
            if native != frequency and alignment == "native":
                raise PlanError("Frequency conversion requires an explicit alignment", code="INVALID_TEMPORAL_AGGREGATION")
            if native == frequency and alignment != "native":
                raise PlanError("Native frequency selection must use native alignment")
            if alignment == "sum" and (binding["kind"] not in {"flow", "count_flow"} or native != "monthly" or frequency != "quarterly"):
                raise PlanError("Only complete monthly flows may be summed to quarters", code="INVALID_TEMPORAL_AGGREGATION")
            if alignment == "mean" and binding["kind"] not in {"rate", "ratio", "index", "price"}:
                raise PlanError("Mean alignment requires a rate, ratio, index or price", code="INVALID_TEMPORAL_AGGREGATION")
            if native != frequency and binding["status"] != "ready":
                raise PlanError("Unreviewed semantics permit native raw selection only", code="SEMANTICS_REVIEW_REQUIRED")
            schemas[name] = {key: binding.get(key) for key in ("kind", "unit", "scale", "currency", "status")}
            schemas[name]["metric_id"] = metric_id
            schemas[name]["scope"] = {
                "namespace": binding.get("scope_namespace") or binding.get("dataset_id") or binding.get("source_system"),
                "institution_scope": binding.get("institution_scope") or "unspecified",
                "geography_scope": binding.get("geography_scope") or "unspecified",
                "dimensions": copy.deepcopy(dimensions),
            }
        warmup = 0
        operation_schemas = []
        for operation in plan.get("operations", []):
            if not isinstance(operation, dict):
                raise PlanError("operation must be an object")
            op = operation.get("op")
            fields = {
                "growth": {"op", "column", "output", "periods"},
                "difference": {"op", "column", "output", "periods"},
                "deflate": {"op", "column", "index", "base_period", "output"},
                "scale": {"op", "column", "output", "target_scale"},
                "ratio": {"op", "column", "denominator", "output", "multiplier", "scope_policy", "scope_reason"},
            }
            if not isinstance(op, str) or op not in fields:
                raise PlanError(f"Unsupported operation {op!r}")
            required = fields[op] - ({"periods"} if op in {"growth", "difference"} else {"multiplier", "scope_policy", "scope_reason"} if op == "ratio" else set())
            _object(operation, fields[op], required, "operation")
            output = _name(operation["output"])
            column = operation["column"]
            if not isinstance(column, str) or column not in schemas:
                raise PlanError("Operation references an unknown column")
            source = schemas[column]
            if source["status"] != "ready" or source["kind"] == "unknown":
                raise PlanError("Unreviewed semantics permit raw selection only", code="SEMANTICS_REVIEW_REQUIRED")
            schema = copy.deepcopy(source)
            if op in {"growth", "difference"}:
                periods = operation.get("periods", 1)
                if isinstance(periods, bool) or not isinstance(periods, int) or not 1 <= periods <= 120:
                    raise PlanError("periods must be an integer between 1 and 120")
                # Sum is conservative for chained operations and preserves warmup.
                warmup += periods
                if op == "growth":
                    if source["kind"] in {"rate", "ratio"}:
                        raise PlanError("Use difference for rates and ratios to obtain percentage points", code="UNIT_MISMATCH")
                    schema.update(kind="ratio", unit="percent", scale=1, currency=None)
                elif source["kind"] in {"rate", "ratio"}:
                    if source["unit"] not in {"percent", "%"}:
                        raise PlanError("Percentage-point differences require percent-valued inputs", code="UNIT_MISMATCH")
                    schema.update(kind="difference", unit="percentage_points", scale=1, currency=None)
            elif op == "deflate":
                index = operation["index"]
                if not isinstance(index, str) or index not in schemas or schemas[index]["kind"] != "index" or schemas[index]["status"] != "ready":
                    raise PlanError("Deflation requires a reviewed index column", code="UNIT_MISMATCH")
                if source["kind"] not in {"stock", "flow", "price"} or not source.get("currency"):
                    raise PlanError("Only monetary stocks, flows and prices may be deflated", code="UNIT_MISMATCH")
                base = _period(operation["base_period"], frequency)
                if base > end:
                    raise PlanError("Base period must not be after the selected end")
                warmup = max(warmup, start.ordinal - base.ordinal)
                schema["price_basis"] = operation["base_period"]
            elif op == "scale":
                scale = operation["target_scale"]
                if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                    raise PlanError("target_scale must be finite and positive")
                if source["kind"] not in {"stock", "flow", "price", "count", "count_stock", "count_flow"}:
                    raise PlanError("Scale conversion is unavailable for this metric kind")
                schema["scale"] = scale
            elif op == "ratio":
                other = operation["denominator"]
                if not isinstance(other, str) or other not in schemas:
                    raise PlanError("Unknown denominator")
                denominator = schemas[other]
                if denominator["status"] != "ready" or source["kind"] != denominator["kind"] or source["unit"] != denominator["unit"] or source.get("currency") != denominator.get("currency") or source.get("price_basis") != denominator.get("price_basis"):
                    raise PlanError("Ratios require compatible reviewed metric kinds, units, currencies and price bases", code="UNIT_MISMATCH")
                policy = operation.get("scope_policy", "same_scope")
                reason = operation.get("scope_reason")
                if not isinstance(policy, str) or policy not in {"same_scope", "explicit_comparison"}:
                    raise PlanError("scope_policy must be same_scope or explicit_comparison")
                if policy == "explicit_comparison" and (not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500):
                    raise PlanError("Explicit scope comparison requires a scope_reason of 10 to 500 characters")
                if policy == "same_scope" and reason is not None:
                    raise PlanError("scope_reason requires scope_policy=explicit_comparison")
                if source["scope"] != denominator["scope"]:
                    if policy != "explicit_comparison":
                        raise PlanError("Ratio inputs have different source populations, geography or selected dimensions; an intentional comparison must name its scope_policy and scope_reason", code="SCOPE_MISMATCH")
                    schema["scope"] = {"comparison": {"numerator": copy.deepcopy(source["scope"]), "denominator": copy.deepcopy(denominator["scope"])}, "scope_reason": reason}
                multiplier = operation.get("multiplier", 100)
                if isinstance(multiplier, bool) or multiplier not in {1, 100}:
                    raise PlanError("Ratio multiplier must be 1 or 100")
                schema.update(kind="ratio", unit="percent" if multiplier == 100 else "ratio", scale=1, currency=None)
            schemas[output] = schema
            operation_schemas.append(copy.deepcopy(schema))
        if warmup > 1200:
            raise PlanError("Excessive calculation warmup")
        return {"start": start, "end": end, "warmup": warmup, "schema": schemas, "operation_schemas": operation_schemas}

    def validate_plan(self, plan: dict) -> dict:
        try:
            with self._context() as (connection, bindings, _):
                validated = self._validate(plan, bindings)
                _, _, warnings = self._evaluate(connection, bindings, plan, validated)
                return {"status": "valid", "schema": validated["schema"], "warnings": warnings}
        except PlanError as exc:
            return error_envelope(exc)

    def _read_column(self, connection: Any, binding: dict, selection: dict, calendar: pd.PeriodIndex) -> tuple[pd.Series, dict, list[dict]]:
        time, value = binding["time_column"], binding["value_column"]
        provenance = list(dict.fromkeys(binding.get("provenance_columns", [])))
        selected = list(dict.fromkeys([time, value, *provenance]))
        predicates, params = [], []
        for key, val in {**binding.get("filters", {}), **{binding["dimensions"][key]: val for key, val in selection.get("dimensions", {}).items()}}.items():
            if isinstance(val, list):
                if not val:
                    raise PlanError("Empty registry filter")
                predicates.append(f"{_identifier(key)} IN ({','.join('?' for _ in val)})")
                params.extend(val)
            else:
                predicates.append(f"{_identifier(key)} = ?")
                params.append(val)
        sql = f"SELECT {', '.join(_identifier(x) for x in selected)} FROM {_identifier(binding['table'])}"
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += " LIMIT 100001"
        try:
            frame = connection.execute(sql, params).fetchdf()
        except duckdb.Error as exc:
            raise PlanError("Metric binding or dimension types do not match the selected data") from exc
        if len(frame) > 100000:
            raise PlanError("Metric selection exceeds 100000 source rows; narrow the dimensions")
        if frame.empty:
            raise PlanError(f"No observations for {selection['name']} and the selected dimensions", code="MISSING_OBSERVATIONS")
        raw_dates = frame[time].astype(str)
        try:
            if binding["native_frequency"] == "quarterly":
                dates = pd.PeriodIndex(raw_dates.str.replace("-Q", "Q", regex=False), freq="Q").to_timestamp(how="end")
            else:
                dates = pd.to_datetime(raw_dates, format="mixed", errors="raise")
            frame["_period"] = pd.PeriodIndex(dates, freq=calendar.freqstr)
        except (ValueError, TypeError) as exc:
            raise PlanError(f"Invalid source period for {selection['name']}") from exc
        frame["_date"] = raw_dates
        source_rows = {str(when): group for when, group in frame.groupby("_date")}
        frame = frame[frame["_period"].between(calendar[0], calendar[-1])].sort_values("_date")
        if frame.empty:
            raise PlanError(f"No observations in the requested window for {selection['name']}", code="MISSING_OBSERVATIONS")
        if frame["_date"].duplicated().any():
            raise PlanError(f"Ambiguous grain for {selection['name']}: more than one row per native period", code="AMBIGUOUS_GRAIN")
        numeric = pd.to_numeric(frame[value], errors="coerce")
        if (frame[value].notna() & numeric.isna()).any() or np.isinf(numeric).any():
            raise PlanError("Source contains invalid numeric observations")
        frame[value] = numeric
        alignment = selection.get("alignment", "native")
        integer_source = pd.api.types.is_integer_dtype(numeric.dtype)
        if integer_source:
            frame[value] = frame[value].astype("Int64")
            if alignment not in {"native", "last"} and ((frame[value] > 2**53) | (frame[value] < -(2**53))).any():
                raise PlanError("Integer aggregation exceeds the exact floating-point range; select native values", code="NUMERIC_PRECISION_UNSUPPORTED")
        grouped = frame.groupby("_period", sort=True)
        warnings = []
        if alignment == "native":
            if frame["_period"].duplicated().any():
                raise PlanError("Native selection would fan out the requested period", code="AMBIGUOUS_GRAIN")
            series = frame.set_index("_period")[value]
        elif alignment == "last":
            series = grouped.tail(1).set_index("_period")[value]
        elif alignment == "mean":
            series = grouped[value].mean()
            warnings.append({"code": "observed_sample_mean", "column": selection["name"], "detail": "Arithmetic mean of observed native values; a complete publication calendar is not asserted"})
        else:
            sizes, numeric_sizes = grouped.size(), grouped[value].count()
            complete = (sizes == 3) & (numeric_sizes == 3)
            series = grouped[value].sum(min_count=3).where(complete)
            if (~complete).any():
                warnings.append({"code": "partial_period_blocked", "column": selection["name"], "periods": [_label(p) for p in complete.index[~complete]]})
        cells = {}
        def source_cell(row: pd.Series) -> dict:
            return _json({"native_period": row[time], "computed_value": row[value], "source_value": row[value], **{key: row[key] for key in provenance}})

        for period, group in grouped:
            period_cells = []
            for _, row in group.iterrows():
                cell = source_cell(row)
                if row.get("transformation") == "difference_within_calendar_year" and str(row[time])[5:7] != "01":
                    prior = row.get("prior_source_month")
                    predecessors = source_rows.get(str(prior))
                    cell["previous_cumulative_source_cells"] = [] if predecessors is None else [source_cell(previous) for _, previous in predecessors.iterrows()]
                period_cells.append(cell)
            cells[_label(period)] = period_cells
        if integer_source and alignment != "mean":
            series = series.astype("Int64")
        return series.reindex(calendar), {"binding": copy.deepcopy(binding), "dimensions": selection.get("dimensions", {}), "alignment": alignment, "cells": cells}, warnings

    def _evaluate(self, connection: Any, bindings: dict, plan: dict, validated: dict) -> tuple[pd.DataFrame, dict, list[dict]]:
        calendar = pd.period_range(validated["start"] - validated["warmup"], validated["end"], freq=validated["start"].freq)
        frame = pd.DataFrame(index=calendar)
        lineage: dict[str, Any] = {"sources": {}, "operations": [], "frequency": plan["frequency"]}
        warnings = []
        # These are independent scalar series aligned on period, not a claim of
        # identical covered populations or a row-level entity join.
        raw_scopes = {selection["name"]: {
            "namespace": bindings[selection["metric_id"]].get("scope_namespace") or bindings[selection["metric_id"]].get("dataset_id") or bindings[selection["metric_id"]].get("source_system"),
            "institution_scope": bindings[selection["metric_id"]].get("institution_scope") or "unspecified",
            "geography_scope": bindings[selection["metric_id"]].get("geography_scope") or "unspecified",
            "dimensions": copy.deepcopy(selection.get("dimensions", {})),
        } for selection in plan["columns"]}
        if len({json.dumps(scope, sort_keys=True) for scope in raw_scopes.values()}) > 1:
            warnings.append({"code": "heterogeneous_scopes_aligned", "detail": "Columns align by period only; identical source populations are not asserted", "column_scopes": raw_scopes})
        lineage["join_contract"] = {"key": "period", "cardinality": "one_value_per_column_per_period", "population_equivalence_asserted": False}
        for selection in plan["columns"]:
            binding = bindings[selection["metric_id"]]
            series, proof, notes = self._read_column(connection, binding, selection, calendar)
            frame[selection["name"]] = series
            lineage["sources"][selection["name"]] = proof
            warnings.extend(notes)
            if binding["status"] != "ready":
                warnings.append({"code": "semantics_unreviewed", "column": selection["name"], "detail": binding.get("blocked_reason", "Raw values only; calculations are blocked")})
            definitions = binding.get("definition_periods", [])
            overlapping = [item for item in definitions if str(item.get("start", "0000")) <= str(calendar[-1].end_time.date()) and str(item.get("end") or "9999") >= str(calendar[0].start_time.date())]
            if len({item.get("definition_id", item.get("label")) for item in overlapping}) > 1 and any(selection["name"] in (op.get("column"), op.get("index"), op.get("denominator")) for op in plan.get("operations", [])):
                raise PlanError(f"{selection['name']}: calculation crosses a source definition change", code="DEFINITION_BREAK")
        schemas = {s["name"]: {key: bindings[s["metric_id"]].get(key) for key in ("kind", "unit", "scale", "currency")} for s in plan["columns"]}
        for operation_index, operation in enumerate(plan.get("operations", [])):
            op, column, output = operation["op"], operation["column"], operation["output"]
            source = frame[column].copy()
            for input_name in (column, operation.get("index"), operation.get("denominator")):
                if input_name is None:
                    continue
                values = frame[input_name]
                if pd.api.types.is_integer_dtype(values.dtype) and ((values > 2**53) | (values < -(2**53))).any():
                    raise PlanError(f"{input_name}: exact integer arithmetic beyond 2**53 is not supported; native values remain available", code="NUMERIC_PRECISION_UNSUPPORTED")
            if op in {"growth", "difference"}:
                previous = source.shift(operation.get("periods", 1))
                result = (source / previous.where(previous != 0) - 1) * 100 if op == "growth" else source - previous
                if op == "growth" and (previous == 0).any():
                    warnings.append({"code": "zero_denominator", "column": output})
            elif op == "deflate":
                index = frame[operation["index"]]
                base_period = _period(operation["base_period"], plan["frequency"])
                base = index.get(base_period, np.nan)
                if pd.isna(base) or base <= 0:
                    raise PlanError("Deflator base observation must exist and be positive", code="MISSING_OBSERVATIONS")
                result = source / index.where(index > 0) * base
                if ((index <= 0) & index.notna()).any():
                    warnings.append({"code": "invalid_deflator", "column": output})
            elif op == "scale":
                result = source * schemas[column].get("scale", 1) / operation["target_scale"]
            else:
                if "comparison" in validated["operation_schemas"][operation_index].get("scope", {}):
                    warnings.append({"code": "cross_scope_comparison", "column": output, "detail": "Explicit numerical comparison across different scopes; population compatibility is not certified", "scope_reason": operation["scope_reason"], "scopes": validated["operation_schemas"][operation_index]["scope"]["comparison"]})
                denominator = frame[operation["denominator"]] * schemas[operation["denominator"]].get("scale", 1)
                result = source * schemas[column].get("scale", 1) / denominator.where(denominator != 0) * operation.get("multiplier", 100)
                if (denominator == 0).any():
                    warnings.append({"code": "zero_denominator", "column": output})
            frame[output] = result.replace([np.inf, -np.inf], np.nan)
            schemas[output] = copy.deepcopy(validated["operation_schemas"][operation_index])
            lineage["operations"].append(copy.deepcopy(operation))
        frame = frame.loc[validated["start"]:validated["end"]].copy()
        for column in frame:
            missing = frame[column].isna()
            if missing.any():
                warnings.append({"code": "missing_result", "column": column, "periods": [_label(p) for p in frame.index[missing]][:12], "count": int(missing.sum())})
        frame.insert(0, "period", [_label(p) for p in frame.index])
        frame = frame.reset_index(drop=True)
        return frame, lineage, warnings

    @staticmethod
    def _envelope(frame: pd.DataFrame, manifest: dict, warnings: list) -> dict:
        return _json({"status": "ok", "analysis_id": manifest["analysis_id"], "parent_analysis_id": manifest.get("parent_analysis_id"), "snapshot_id": manifest["snapshot_id"], "workspace_version": manifest["new_workspace_version"], "row_count": len(frame), "columns": list(frame), "schema": manifest.get("schema"), "preview": (frame if len(frame) <= 10 else pd.concat([frame.head(5), frame.tail(5)])).to_dict("records"), "preview_truncated": len(frame) > 10, "warnings": warnings, "result_ref": manifest["analysis_id"], "join_contract": manifest["lineage"].get("join_contract")})

    def execute(self, plan: dict) -> dict:
        with self._context() as (connection, bindings, workspace):
            validated = self._validate(plan, bindings)
            frame, lineage, warnings = self._evaluate(connection, bindings, plan, validated)
            manifest = self.store.save_analysis(self.workspace_id, frame, copy.deepcopy(plan), _json(lineage), schema=validated["schema"], expected_version=workspace["version"])
            return self._envelope(frame, manifest, warnings)

    def revise_analysis(self, request: dict) -> dict:
        _object(request, {"analysis_id", "add_columns", "operations"}, {"analysis_id"}, "revision")
        if not isinstance(request["analysis_id"], str):
            raise PlanError("analysis_id must be a string")
        if not isinstance(request.get("add_columns", []), list) or not isinstance(request.get("operations", []), list):
            raise PlanError("Revision columns and operations must be arrays")
        if not request.get("add_columns") and not request.get("operations"):
            raise PlanError("An empty revision has no effect")
        parent, parent_manifest = self.store.load_analysis(request["analysis_id"])
        if parent_manifest["workspace_id"] != self.workspace_id:
            raise PlanError("Analysis belongs to another workspace")
        for selection in request.get("add_columns", []):
            if isinstance(selection, dict) and selection.get("name") in parent.columns:
                raise PlanError("An added column must have a new name; use an operation for explicit replacement")
        plan = copy.deepcopy(parent_manifest["plan"])
        plan["columns"].extend(copy.deepcopy(request.get("add_columns", [])))
        plan.setdefault("operations", []).extend(copy.deepcopy(request.get("operations", [])))
        with self._context() as (connection, bindings, workspace):
            if parent_manifest["snapshot_id"] != workspace["snapshot_id"] or not set(parent_manifest.get("datasets", [])).issubset(workspace.get("datasets", [])):
                raise PlanError("Revision must retain its original snapshot and dataset versions")
            validated = self._validate(plan, bindings)
            frame, lineage, warnings = self._evaluate(connection, bindings, plan, validated)
            if not frame["period"].equals(parent["period"]):
                raise PlanError("Revision changed the original period keys")
            replaced = {op["output"] for op in request.get("operations", [])}
            preserved = [column for column in parent if column not in replaced]
            for column in preserved:
                # Preserve the saved parent's cells, even if a new operation reuses
                # a source name. Revision semantics are explicit column replacement.
                frame[column] = parent[column]
            manifest = self.store.save_analysis(self.workspace_id, frame, plan, _json(lineage), schema=validated["schema"], parent_analysis_id=request["analysis_id"], expected_version=workspace["version"])
            result = self._envelope(frame, manifest, warnings)
            result["preserved_columns"] = preserved
            return result

    def explain_value(self, request: dict) -> dict:
        """Return stored reference lineage, without claiming raw-file byte verification.

        Snapshot/result hashes are verified by the store on read. Source file
        locators and hashes are preserved references; this tool does not reopen
        the original external files. ``lineage_complete`` is a compatibility
        alias for ``source_references_complete``.
        """
        _object(request, {"analysis_id", "column", "period"}, {"analysis_id", "column", "period"}, "explanation")
        if not all(isinstance(value, str) for value in request.values()):
            raise PlanError("Explanation fields must be strings")
        frame, manifest = self.store.load_analysis(request["analysis_id"])
        if manifest["workspace_id"] != self.workspace_id:
            raise PlanError("Analysis belongs to another workspace")
        column = request["column"]
        if column == "period" or column not in frame:
            raise PlanError("Unknown result column")
        selected = frame[frame["period"] == request["period"]]
        if len(selected) != 1:
            raise PlanError("Unknown result period")
        lineage = manifest["lineage"]
        period = _period(request["period"], lineage["frequency"])
        operations = lineage["operations"]
        explanation_nodes = 0
        lineage_issues: set[str] = set()

        def explain(name: str, at: pd.Period, before: int) -> dict:
            nonlocal explanation_nodes
            explanation_nodes += 1
            if explanation_nodes > 512:
                lineage_issues.add("Explanation exceeds the node limit; the stored recipe is available in the analysis manifest")
                return {"column": name, "period": _label(at), "status": "truncated", "recipe_operation_limit": before}
            for idx in range(before - 1, -1, -1):
                operation = operations[idx]
                if operation["output"] != name:
                    continue
                inputs = [explain(operation["column"], at, idx)]
                if operation["op"] in {"growth", "difference"}:
                    inputs.append(explain(operation["column"], at - operation.get("periods", 1), idx))
                elif operation["op"] == "deflate":
                    inputs.extend([explain(operation["index"], at, idx), explain(operation["index"], _period(operation["base_period"], lineage["frequency"]), idx)])
                elif operation["op"] == "ratio":
                    inputs.append(explain(operation["denominator"], at, idx))
                return {"column": name, "period": _label(at), "operation": operation, "inputs": inputs}
            proof = lineage["sources"][name]
            binding = proof["binding"]
            source_cells = proof["cells"].get(_label(at), [])
            if not source_cells:
                lineage_issues.add(f"{binding['metric_id']} at {_label(at)}: no native observation is available")
            for cell in source_cells:
                has_hash = any(cell.get(key) for key in ("source_sha256", "source_response_sha256", "source_csv_sha256"))
                has_locator = any(cell.get(key) for key in ("source_file", "source_response_file", "source_csv_file"))
                overlay_source = bool(binding.get("dataset_id") and binding.get("source_sha256"))
                if not (has_hash and has_locator or overlay_source):
                    lineage_issues.add(f"{binding['metric_id']}: raw source hash and locator are not both bound")
                if "previous_cumulative_source_cells" in cell and not cell["previous_cumulative_source_cells"]:
                    lineage_issues.add(f"{binding['metric_id']} at {_label(at)}: previous cumulative source cell is unavailable")
            return {"column": name, "period": _label(at), "metric_id": binding["metric_id"], "contract_version": binding.get("contract_version"), "unit": binding.get("unit"), "scale": binding.get("scale"), "source_base": binding.get("source_base"), "hash_basis": binding.get("hash_basis", "file_bytes"), "source_sha256": binding.get("source_sha256"), "dataset_id": binding.get("dataset_id"), "dimensions": proof["dimensions"], "alignment": proof["alignment"], "source_cells": proof["cells"].get(_label(at), [])}

        proof = explain(column, period, len(operations))
        return _json({"status": "ok", "analysis_id": request["analysis_id"], "snapshot_id": manifest["snapshot_id"], "column": column, "period": request["period"], "value": selected.iloc[0][column], "schema": manifest.get("schema", {}).get(column), "lineage": proof, "lineage_complete": not lineage_issues, "source_references_complete": not lineage_issues, "source_files_verified": False, "lineage_issues": sorted(lineage_issues)})
