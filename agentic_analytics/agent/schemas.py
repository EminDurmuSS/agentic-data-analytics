"""JSON schemas for lakehouse tool arguments exposed to the decision-maker."""

def obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}


STRING = {"type": "string", "minLength": 1, "maxLength": 500}
COLUMN_NAME = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$", "not": {"const": "period"}, "maxLength": 64}
DIMENSIONS = {"type": "object", "maxProperties": 12, "additionalProperties": {"type": ["string", "number"]}}
ALIGNMENT = {"enum": ["native", "last", "mean", "sum", "period_end"],
             "description": "Use native for equal source/output frequencies. last/mean/sum convert finer regular frequencies to coarser ones. period_end is only for ready event stock/count_stock observations whose actual source date exactly equals the requested target period's calendar end; it never carries observations, guesses frequency, sums or averages. Event flows, unknown or review-required measures cannot use period_end."}
COLUMN = obj({"name": COLUMN_NAME, "metric_id": STRING, "dimensions": DIMENSIONS,
              "alignment": ALIGNMENT}, ["name", "metric_id"])


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
            props["target_scale"] = {"type": "number", "exclusiveMinimum": 0,
                "description": "Absolute number of base units represented by one output value, not a relative divisor. For TRY: 1=TRY, 1000=thousand TRY, 1000000=million TRY, 1000000000=billion TRY. The backend accounts for the input scale: converting an input scale of 1000 to millions requires target_scale=1000000, not 1000."}
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
