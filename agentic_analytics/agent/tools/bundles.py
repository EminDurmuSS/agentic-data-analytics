"""Immutable multi-analysis bundles for mixed-frequency results.

A bundle links existing content-addressed analyses. It never reshapes their
period keys, copies quarterly values into monthly rows, or manufactures a
single mixed-grain table. All source identities and transformations are
derived from the saved analysis manifests rather than supplied by the model.
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


class AnalysisBundleError(ValueError):
    def __init__(self, message, code="INVALID_ANALYSIS_BUNDLE"):
        super().__init__(message)
        self.code = code


_ANALYSIS_ID = re.compile(r"analysis_[a-f0-9]{64}")
_BUNDLE_ID = re.compile(r"analysis_bundle_[a-f0-9]{64}")
_ROLE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_ADDITIVE_KINDS = {"flow", "count_flow"}


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
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    return value


def _canonical(value):
    try:
        return json.dumps(
            _json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AnalysisBundleError("Bundle metadata must be finite and JSON serializable.") from exc


def _frequency(manifest):
    plan = manifest.get("plan") or {}
    request = plan.get("request") if isinstance(plan.get("request"), dict) else {}
    return plan.get("frequency") or request.get("frequency") or (manifest.get("lineage") or {}).get("frequency")


def _source_proofs(lineage):
    """Yield scalar source proofs from scalar or grouped analysis lineage."""
    if not isinstance(lineage, dict):
        return
    for name, proof in (lineage.get("sources") or {}).items():
        if isinstance(proof, dict):
            yield name, proof
    for group_lineage in (lineage.get("groups") or {}).values():
        if isinstance(group_lineage, dict):
            yield from _source_proofs(group_lineage)


def _column_alignments(manifest):
    plan = manifest.get("plan") or {}
    request = plan.get("request") if isinstance(plan.get("request"), dict) else plan
    alignments = {}
    for column in request.get("columns", []) if isinstance(request, dict) else []:
        if isinstance(column, dict) and isinstance(column.get("name"), str):
            alignments[column["name"]] = {
                "metric_id": column.get("metric_id"),
                "alignment": column.get("alignment", "native"),
            }
    if plan.get("query_type") == "grouped" and isinstance(request, dict):
        alignments["value"] = {
            "metric_id": request.get("metric_id"),
            "alignment": request.get("alignment", "native"),
        }
    return alignments


class AnalysisBundleTools:
    """Persist and verify a report made of separate immutable analyses."""

    def __init__(self, store, workspace_id, *, max_components=12):
        self.store, self.workspace_id = store, workspace_id
        self.store.workspace(workspace_id)
        self.max_components = max_components
        self.root = store.root / "analysis_bundles" / workspace_id
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bundle_id):
        if not isinstance(bundle_id, str) or not _BUNDLE_ID.fullmatch(bundle_id):
            raise AnalysisBundleError("Invalid analysis bundle identifier.")
        return self.root / (bundle_id + ".json")

    def _source_cards(self, manifest):
        cards, seen = [], set()
        alignments = _column_alignments(manifest)
        for column, proof in _source_proofs(manifest.get("lineage") or {}):
            binding = proof.get("binding") or {}
            metric_id = binding.get("metric_id") or alignments.get(column, {}).get("metric_id")
            alignment = proof.get("alignment") or alignments.get(column, {}).get("alignment", "native")
            key = (column, metric_id, alignment, json.dumps(_json(proof.get("dimensions") or {}), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            cards.append({
                "column": column,
                "metric_id": metric_id,
                "title": binding.get("title"),
                "source_code": binding.get("source_code"),
                "source_system": binding.get("source_system"),
                "source_organization": binding.get("source_organization"),
                "source_url": binding.get("source_url"),
                "source_metadata_url": binding.get("source_metadata_url"),
                "binding_sha256": binding.get("binding_sha256"),
                "native_frequency": binding.get("native_frequency"),
                "output_frequency": _frequency(manifest),
                "alignment": alignment,
                "unit": binding.get("unit"),
                "scale": binding.get("scale"),
                "currency": binding.get("currency"),
                "kind": binding.get("kind"),
                "dimensions": _json(proof.get("dimensions") or {}),
            })
        return cards

    def _component(self, requested):
        analysis_id = requested.get("analysis_id")
        role = requested.get("role")
        label = requested.get("label")
        if not isinstance(analysis_id, str) or not _ANALYSIS_ID.fullmatch(analysis_id):
            raise AnalysisBundleError("Every component requires a valid analysis_id.")
        if not isinstance(role, str) or not _ROLE.fullmatch(role):
            raise AnalysisBundleError("Every component role must be a simple ASCII identifier.")
        if label is not None and (not isinstance(label, str) or not 1 <= len(label.strip()) <= 180):
            raise AnalysisBundleError("Component labels must contain 1 to 180 characters.")
        frame, manifest = self.store.load_analysis(analysis_id)
        if manifest.get("workspace_id") != self.workspace_id:
            raise AnalysisBundleError("Analysis belongs to another workspace.", "WORKSPACE_MISMATCH")
        if "period" not in frame or frame["period"].isna().any() or not len(frame):
            raise AnalysisBundleError("Bundle analyses require nonempty explicit period labels.")
        periods = frame["period"].astype(str).tolist()
        schema = manifest.get("schema") or {}
        columns = []
        for name in frame.columns:
            meta = schema.get(name) or {}
            numeric = pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name])
            kind = meta.get("kind")
            explicitly_additive = meta.get("additive_over_time") is True and kind in _ADDITIVE_KINDS
            columns.append({
                "name": name,
                "numeric": bool(numeric),
                "kind": kind,
                "unit": meta.get("unit"),
                "scale": meta.get("scale"),
                "currency": meta.get("currency"),
                "status": meta.get("status"),
                "metric_id": meta.get("metric_id"),
                "missing_count": int(frame[name].isna().sum()),
                "additive_over_time": bool(explicitly_additive),
                "time_aggregation_policy": (
                    "sum_allowed_by_reviewed_metadata" if explicitly_additive
                    else "sum_forbidden_stock_rate_ratio_or_unverified"
                ),
            })
        plan = manifest.get("plan") or {}
        request = plan.get("request") if isinstance(plan.get("request"), dict) else plan
        transformations = {
            "source_alignments": [
                {
                    "column": source["column"],
                    "metric_id": source.get("metric_id"),
                    "native_frequency": source.get("native_frequency"),
                    "output_frequency": source.get("output_frequency"),
                    "method": source.get("alignment"),
                }
                for source in self._source_cards(manifest)
            ],
            "operations": _json(request.get("operations", []) if isinstance(request, dict) else []),
        }
        return {
            "analysis_id": analysis_id,
            "role": role,
            "label": label.strip() if isinstance(label, str) else role,
            "data_sha256": manifest["data_sha256"],
            "snapshot_id": manifest.get("snapshot_id"),
            "frequency": _frequency(manifest),
            "period_start": min(periods),
            "period_end": max(periods),
            "period_count": len(set(periods)),
            "row_count": len(frame),
            "period_labels": periods if len(periods) <= 24 else periods[:4] + periods[-4:],
            "period_labels_truncated": len(periods) > 24,
            "columns": columns,
            "sources": self._source_cards(manifest),
            "transformations": transformations,
            "warnings": _json((manifest.get("lineage") or {}).get("warnings", [])),
        }

    def save_analysis_bundle(self, components, title, *, parent_bundle_id=None, purpose=None):
        if not isinstance(components, list) or not 1 <= len(components) <= self.max_components:
            raise AnalysisBundleError(f"Choose 1 to {self.max_components} bundle components.")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 240:
            raise AnalysisBundleError("Bundle title must contain 1 to 240 characters.")
        if purpose is not None and (not isinstance(purpose, str) or not 1 <= len(purpose.strip()) <= 500):
            raise AnalysisBundleError("Bundle purpose must contain 1 to 500 characters.")

        merged = {}
        parent = None
        if parent_bundle_id is not None:
            parent = self.load_bundle(parent_bundle_id)
            for component in parent["components"]:
                merged[component["role"]] = {
                    "analysis_id": component["analysis_id"],
                    "role": component["role"],
                    "label": component.get("label"),
                }
        for requested in components:
            if not isinstance(requested, dict) or set(requested) - {"analysis_id", "role", "label"}:
                raise AnalysisBundleError("Bundle components accept analysis_id, role and optional label only.")
            role = requested.get("role")
            if not isinstance(role, str):
                raise AnalysisBundleError("Every component requires a role.")
            merged[role] = requested
        if not 2 <= len(merged) <= self.max_components:
            raise AnalysisBundleError("A mixed-analysis bundle must retain 2 to 12 distinct roles.")

        resolved = [self._component(component) for component in merged.values()]
        analysis_ids = [component["analysis_id"] for component in resolved]
        if len(analysis_ids) != len(set(analysis_ids)):
            raise AnalysisBundleError("The same immutable analysis cannot fill multiple bundle roles.")
        snapshots = {component["snapshot_id"] for component in resolved}
        if len(snapshots) != 1:
            raise AnalysisBundleError("Bundle components must use the same pinned snapshot.", "SNAPSHOT_MISMATCH")

        frequencies = sorted({component["frequency"] for component in resolved if component.get("frequency")})
        payload = {
            "format_version": 1,
            "workspace_id": self.workspace_id,
            "snapshot_id": next(iter(snapshots)),
            "parent_bundle_id": parent_bundle_id,
            "title": title.strip(),
            "purpose": purpose.strip() if isinstance(purpose, str) else None,
            "components": resolved,
            "frequencies": frequencies,
            "frequency_policy": {
                "mode": "separate_immutable_analyses",
                "mixed_grain_single_table": False,
                "quarterly_values_copied_to_months": False,
                "native_period_labels_preserved": True,
            },
            "missing_value_policy": {
                "mode": "preserve_nulls",
                "zero_fill": False,
                "forward_fill": False,
                "interpolation": False,
                "estimation": False,
            },
            "warnings": ([{
                "code": "mixed_frequencies_retained_separately",
                "message": "Components keep their own frequency and period labels; no mixed-grain row union was created.",
            }] if len(frequencies) > 1 else []),
        }
        encoded = _canonical(payload)
        bundle_id = "analysis_bundle_" + hashlib.sha256(encoded).hexdigest()
        stored = _canonical({**payload, "bundle_id": bundle_id})
        path = self._path(bundle_id)
        if not path.exists():
            fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(stored)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        verified = self.load_bundle(bundle_id)
        return {
            "status": "ok",
            "bundle_id": bundle_id,
            "artifact_id": bundle_id,
            "artifact_ref": bundle_id,
            "workspace_id": self.workspace_id,
            "title": verified["title"],
            "parent_bundle_id": verified.get("parent_bundle_id"),
            "component_count": len(verified["components"]),
            "frequencies": verified["frequencies"],
            "components": [{key: component[key] for key in (
                "analysis_id", "role", "label", "frequency", "period_start", "period_end", "row_count"
            )} for component in verified["components"]],
            "frequency_policy": verified["frequency_policy"],
            "missing_value_policy": verified["missing_value_policy"],
            "warnings": verified["warnings"],
        }

    def load_bundle(self, bundle_id):
        path = self._path(bundle_id)
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, ValueError, TypeError) as exc:
            raise AnalysisBundleError("Analysis bundle is missing or invalid.", "BUNDLE_INTEGRITY_ERROR") from exc
        if not isinstance(value, dict) or value.get("bundle_id") != bundle_id:
            raise AnalysisBundleError("Analysis bundle identity is invalid.", "BUNDLE_INTEGRITY_ERROR")
        payload = {key: item for key, item in value.items() if key != "bundle_id"}
        if bundle_id != "analysis_bundle_" + hashlib.sha256(_canonical(payload)).hexdigest():
            raise AnalysisBundleError("Analysis bundle hash mismatch.", "BUNDLE_INTEGRITY_ERROR")
        if value.get("workspace_id") != self.workspace_id:
            raise AnalysisBundleError("Analysis bundle belongs to another workspace.", "WORKSPACE_MISMATCH")
        for component in value.get("components", []):
            frame, manifest = self.store.load_analysis(component["analysis_id"])
            if (manifest.get("workspace_id") != self.workspace_id
                    or manifest.get("data_sha256") != component.get("data_sha256")
                    or len(frame) != component.get("row_count")):
                raise AnalysisBundleError("A linked analysis no longer matches the bundle.", "BUNDLE_INTEGRITY_ERROR")
        return value

    def extra_tools(self):
        text = {"type": "string", "minLength": 1}
        component = {
            "type": "object",
            "properties": {
                "analysis_id": {"type": "string", "pattern": r"^analysis_[a-f0-9]{64}$"},
                "role": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,63}$"},
                "label": {**text, "maxLength": 180},
            },
            "required": ["analysis_id", "role"],
            "additionalProperties": False,
        }
        parameters = {
            "type": "object",
            "properties": {
                "components": {"type": "array", "minItems": 1, "maxItems": self.max_components, "items": component},
                "title": {**text, "maxLength": 240},
                "purpose": {**text, "maxLength": 500},
                "parent_bundle_id": {"type": "string", "pattern": r"^analysis_bundle_[a-f0-9]{64}$"},
            },
            "required": ["components", "title"],
            "additionalProperties": False,
        }

        def handler(arguments):
            try:
                return self.save_analysis_bundle(**arguments)
            except (AnalysisBundleError, StoreError, ValueError, TypeError, KeyError, OSError) as exc:
                return {
                    "status": "blocked",
                    "errors": [{"code": getattr(exc, "code", "ANALYSIS_BUNDLE_ERROR"), "message": str(exc)}],
                }

        return {"save_analysis_bundle": {
            "schema": {"type": "function", "function": {
                "name": "save_analysis_bundle",
                "description": (
                    "Save two or more immutable analyses as one mixed-frequency report without joining their rows. "
                    "Use this when monthly, weekly and quarterly views must remain separate. Supply parent_bundle_id "
                    "to retain prior roles; a new component with the same role replaces that role. Source identities, "
                    "frequency conversions, periods, summability and missing-value policy are derived from saved analyses."
                ),
                "parameters": parameters,
            }},
            "handler": handler,
            "mutating": False,
        }}
