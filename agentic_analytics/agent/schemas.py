"""JSON schemas for lakehouse tool arguments exposed to the decision-maker."""

def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


STRING = {"type": "string", "minLength": 1, "maxLength": 500}
COLUMN_NAME = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$", "not": {"const": "period"}, "maxLength": 64}
DIMENSIONS = {"type": "object", "maxProperties": 12, "additionalProperties": {"type": ["string", "number"]}}
COLUMN = obj({"name": COLUMN_NAME, "metric_id": STRING, "dimensions": DIMENSIONS,
              "alignment": {"enum": ["native", "last", "mean", "sum"], "description": "Use native when source and output frequencies are equal. last/mean/sum only convert a finer source frequency to a coarser output frequency; aggregation metadata does not change this rule."}}, ["name", "metric_id"])


def operation_schema():
    variants = []
    for name in ("growth", "difference", "deflate", "scale", "ratio"):
        props = {"op": {"const": name}, "column": COLUMN_NAME, "output": COLUMN_NAME}
        required = list(props)
        if name in {"growth", "difference"}:
            props["periods"] = {"type": "integer", "minimum": 1, "maximum": 120}
        elif name == "deflate":
            props.update(index=COLUMN_NAME, base_period=STRING)
            required += ["index", "base_period"]
        elif name == "scale":
            props["target_scale"] = {"type": "number", "exclusiveMinimum": 0}
            required += ["target_scale"]
        else:
            props.update(denominator=COLUMN_NAME, multiplier={"enum": [1, 100]},
                         scope_policy={"enum": ["same_scope", "explicit_comparison"]}, scope_reason=STRING)
            required += ["denominator"]
        variants.append(obj(props, required))
    return {"oneOf": variants}


OPERATIONS = {"type": "array", "maxItems": 50, "items": operation_schema()}
PLAN = obj({"start": {"type": "string", "description": "First output period: YYYY-MM for monthly, YYYY-Qn for quarterly, YYYY for yearly, YYYY-H1/H2 for half_yearly, ISO date for daily/weekly/twice_monthly."},
            "end": {"type": "string", "description": "Last output period, using the same format as start. twice_monthly bounds are ISO dates and include only dates actually present in the source."},
            "frequency": {"enum": ["monthly", "quarterly", "weekly_friday", "weekly_wednesday", "weekly", "daily", "business_daily", "annual", "yearly", "half_yearly", "twice_monthly"],
                          "description": "half_yearly and twice_monthly support native selection only; do not use conversions or operations with these frequencies."},
            "columns": {"type": "array", "minItems": 1, "maxItems": 25, "items": COLUMN},
            "operations": OPERATIONS}, ["start", "end", "frequency", "columns"])
