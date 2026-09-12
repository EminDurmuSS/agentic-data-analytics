"""The agent interface must expose the service's explicit date alignment."""
import copy
from types import SimpleNamespace

import jsonschema
import pytest

from agentic_analytics.agent.tools.lakehouse import lakehouse_tools


def test_period_end_reaches_all_plan_interfaces_without_allowing_carry_forward():
    handlers = ("discover", "describe", "validate_plan", "execute", "revise_analysis", "explain_value", "query_grouped")
    service = SimpleNamespace(**{name: lambda args: None for name in handlers})
    registry = lakehouse_tools(service)
    column = {"name":"reported_assets","metric_id":"overlay:source:amount",
              "dimensions":{"line_item":"Total assets"},"alignment":"period_end"}
    plan = {"start":"2026-03","end":"2026-03","frequency":"monthly","columns":[column]}
    requests = {"validate_plan":plan,"execute":plan,
                "revise_analysis":{"analysis_id":"saved-analysis","add_columns":[column]},
                "query_grouped":{"metric_id":column["metric_id"],"group_by":"line_item","dimensions":{},
                                 "start":"2026-03","end":"2026-03","frequency":"monthly","alignment":"period_end"}}
    for name, request in requests.items():
        validator = jsonschema.Draft202012Validator(registry[name]["schema"]["function"]["parameters"])
        validator.validate(request)
        invalid = copy.deepcopy(request)
        target = invalid if name == "query_grouped" else invalid["add_columns" if name == "revise_analysis" else "columns"][0]
        target["alignment"] = "carry_forward"
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid)
