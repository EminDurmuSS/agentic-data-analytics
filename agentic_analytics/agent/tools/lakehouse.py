"""Register the typed lakehouse tool family against one service instance."""

from agentic_analytics.agent.schemas import COLUMN, DIMENSIONS, OPERATIONS, PLAN, STRING, obj


def lakehouse_tools(service):
    definitions = {}

    def add(name, description, parameters, handler, mutating=False):
        definitions[name] = {"schema": {"type": "function", "function": {
            "name": name, "description": description, "parameters": parameters}},
            "handler": handler, "mutating": mutating}

    add("discover", "Find a small set of metric cards using short keywords; inspect readiness.",
        obj({"query": {"type": "string", "maxLength": 300}, "limit": {"type": "integer", "minimum": 1, "maximum": 25},
             "status": {"enum": ["ready", "review_required", "metadata_only", "no_numeric"]}}, ["query"]), service.discover)
    add("describe", "Read one metric's semantics, dimensions and coverage before building a plan.", obj({"metric_id": STRING}), service.describe)
    add("validate_plan", "Validate a typed plan without writing an analysis.", PLAN, service.validate_plan)
    add("execute", "Validate and save a new analysis. Use revise_analysis to change the existing table.", PLAN, service.execute, True)
    add("revise_analysis", "Revise the active table, preserving untouched cells. To REPLACE a column, set operation.output to that existing column name. Use a NEW output name only when the user requests an additional column. add_columns supplies new sources such as a deflator.",
        obj({"analysis_id": STRING, "add_columns": {"type": "array", "maxItems": 25, "items": COLUMN}, "operations": OPERATIONS}, ["analysis_id"]), service.revise_analysis, True)
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
                 "alignment": {"enum": ["native", "last", "mean", "sum"]}, "order": {"enum": ["desc", "asc"]},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                ["metric_id", "group_by", "dimensions", "start", "end", "frequency"]), service.query_grouped, True)
    add("ask_user", "Pause for one necessary clarification. Do not invent a critical missing assumption.",
        obj({"question": {"type": "string", "minLength": 1, "maxLength": 1000}}),
        lambda args: {"status": "needs_input", "message": args["question"]})
    return definitions
