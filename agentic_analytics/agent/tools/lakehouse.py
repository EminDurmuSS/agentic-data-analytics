"""Register the typed lakehouse tool family against one service instance."""

import copy

from agentic_analytics.agent.schemas import ALIGNMENT, COLUMN, DIMENSIONS, OPERATIONS, PLAN, STRING, obj
from agentic_analytics.lakehouse.service import PlanError, error_envelope


def lakehouse_tools(service):
    definitions = {}

    def execute_with_recovery(plan):
        try:
            result = service.execute(plan)
        except PlanError as exc:
            result = error_envelope(exc)
        if not isinstance(result, dict) or result.get("status") != "blocked" or not any(
                error.get("code") == "INVALID_TEMPORAL_AGGREGATION" for error in result.get("errors", [])):
            return result
        proposed, described, changed = copy.deepcopy(plan), {}, False
        try:
            for column in proposed.get("columns", []):
                metric_id = column["metric_id"]
                if metric_id not in described:
                    described[metric_id] = service.describe({"metric_id": metric_id}).get("metric", {})
                metric = described[metric_id]
                if metric.get("native_frequency") != "event":
                    continue
                if (metric.get("kind") not in {"stock", "count_stock"} or metric.get("status") != "ready"
                        or column.get("alignment", "native") not in {"native", "period_end"}):
                    return result
                if column.get("alignment", "native") == "native":
                    column["alignment"] = "period_end"
                    changed = True
            if not changed:
                return result
            validation = service.validate_plan(proposed)
            scope_changes = []
            scope_error = any(error.get("code") == "SCOPE_MISMATCH" or (
                error.get("code") == "INVALID_PLAN" and error.get("message") == "scope_reason requires scope_policy=explicit_comparison")
                for error in validation.get("errors", []))
            if validation.get("status") != "valid" and scope_error:
                # Preserve an already explicit comparison reason only after
                # alignment alone failed a scope check. Never weaken a valid
                # same-scope comparison or invent population compatibility.
                for operation in proposed.get("operations", []):
                    if (operation.get("op") == "ratio" and operation.get("scope_policy", "same_scope") == "same_scope"
                            and isinstance(operation.get("scope_reason"), str) and 10 <= len(operation["scope_reason"].strip()) <= 500):
                        operation["scope_policy"] = "explicit_comparison"
                        scope_changes.append(operation["output"])
                if scope_changes:
                    validation = service.validate_plan(proposed)
        except (KeyError, TypeError, ValueError, OSError):
            return result
        if validation.get("status") != "valid":
            return result
        return {**result, "recovery": {"publication_performed": False, "validation_status": "valid",
            "suggested_execute_arguments": proposed, "next_request": {"tool": "execute", "arguments": proposed},
            "scope_policy_changes": scope_changes,
            "next_step": "Retry execute with this source-validated plan. Ready event balances use period_end even for daily output; actual source dates must match calendar endpoints exactly. The selected rows, requested scales and ratio operands are preserved. Do not switch to aggregate_dataset or discard calculations; no result has been published by this recovery." + (
                " Ratios in scope_policy_changes now use explicit_comparison with their existing reasons; this is a numerical comparison, not certified population equivalence." if scope_changes else "")}}

    def add(name, description, parameters, handler, mutating=False):
        definitions[name] = {"schema": {"type": "function", "function": {
            "name": name, "description": description, "parameters": parameters}},
            "handler": handler, "mutating": mutating}

    add("discover", "Find a small set of metric cards using short keywords; inspect readiness.",
        obj({"query": {"type": "string", "maxLength": 300}, "limit": {"type": "integer", "minimum": 1, "maximum": 25},
             "status": {"enum": ["ready", "review_required", "metadata_only", "no_numeric"]}}, ["query"]), service.discover)
    add("describe", "Read one metric's semantics, dimensions and coverage before building a plan.", obj({"metric_id": STRING}), service.describe)
    add("validate_plan", "Validate a typed plan without writing an analysis.", PLAN, service.validate_plan)
    add("execute", "Validate and save a new analysis, including source-proven event balances aligned with explicit period_end even at daily frequency and their actual dimensions. A blocked call may return a fully validated recovery.next_request; retry that exact plan before changing tool families. Use revise_analysis to change an existing metric-based table.", PLAN, execute_with_recovery, True)
    add("revise_analysis", "Revise the active table, preserving untouched cells. To REPLACE a column, set operation.output to that existing column name. Use a NEW output name only when the user requests an additional column. add_columns supplies new sources such as a deflator.",
        obj({"analysis_id": STRING, "add_columns": {"type": "array", "maxItems": 25, "items": COLUMN}, "operations": OPERATIONS,
             "start": STRING, "end": STRING}, ["analysis_id"]), service.revise_analysis, True)
    explanation = {"analysis_id": STRING, "column": STRING, "period": STRING}
    if hasattr(service, "query_grouped"):
        explanation["dimensions"] = DIMENSIONS
    add("explain_value", "Trace a saved result cell to its actual input references, with honest verification flags.",
        obj(explanation, ["analysis_id", "column", "period"]), service.explain_value)
    if hasattr(service, "dimension_values"):
        add("dimension_values", "Find actual dimension codes and labels; never guess group codes.",
            obj({"metric_id": STRING, "dimension": STRING, "query": {"type": "string", "maxLength": 300},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 250}}, ["metric_id", "dimension"]), service.dimension_values)
    if hasattr(service, "query_grouped"):
        add("query_grouped", "Rank groups per period for one metric. Fix all other dimensions explicitly.",
            obj({"metric_id": STRING, "group_by": STRING, "dimensions": DIMENSIONS,
                 "start": STRING, "end": STRING, "frequency": PLAN["properties"]["frequency"],
                 "alignment": ALIGNMENT, "order": {"enum": ["desc", "asc"]},
                 "operations": OPERATIONS,
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                ["metric_id", "group_by", "dimensions", "start", "end", "frequency"]), service.query_grouped, True)
    add("ask_user", "Pause for one necessary clarification. Do not invent a critical missing assumption.",
        obj({"question": {"type": "string", "minLength": 1, "maxLength": 1000}}),
        lambda args: {"status": "needs_input", "message": args["question"]})
    return definitions
