"""A saved analysis whose only value column is a source-preserved string rate
(e.g. "%39.67", kept verbatim for provenance) must still render its values in
the delivery receipt, not a bare "... aralığında:" header with an empty body.

Regression for the "kısmen tamamlandı ama hiç cevap yok" report: the numeric
`is_numeric_dtype` filter dropped the only value column, so the confirmation
emitted a period-range header followed by nothing.
"""
import pandas as pd
import pytest

from app.server import create_app
from agentic_analytics.agent.delivery import _analysis_confirmation


@pytest.fixture
def string_rate_analysis(tmp_path):
    app = create_app(runtime_root=tmp_path, source_db=None, client=None, searxng_url=False)
    context = app.state.context
    workspace = context.create_workspace("Faiz", "generic")
    wid = workspace["workspace_id"]
    periods = ["2026-05-01", "2026-05-08", "2026-05-15", "2026-05-22", "2026-05-29",
               "2026-06-05", "2026-06-12", "2026-06-19", "2026-06-26"]
    values = ["%39.67", "%39.68", "%33.07", "%33.74", "%35.23",
              "%45.87", "%49.16", "%49.38", "%51.85"]
    frame = pd.DataFrame({"period": periods, "faiz_orani": values})
    # Source value preserved verbatim: a rate stored as a string, no numeric dtype.
    schema = {"faiz_orani": {"kind": "rate", "unit": "percent", "scale": 1,
                             "status": "ready", "source_values_only": True}}
    plan = {"start": "2026-05-01", "end": "2026-06-26", "frequency": "weekly", "operations": []}
    lineage = {"frequency": "weekly", "operations": [], "sources": {
        "faiz_orani": {"binding": {**schema["faiz_orani"], "title": "Taşıt kredisi faiz oranı"}}}}
    saved = context.store.save_analysis(wid, frame, plan, lineage, schema=schema, expected_version=0)
    try:
        yield context, wid, saved["analysis_id"]
    finally:
        context.pool.shutdown(wait=True)


def test_string_valued_rate_column_renders_first_and_last_values(string_rate_analysis):
    context, wid, aid = string_rate_analysis
    state = {"analysis_id": aid, "analysis_updated": True, "tool_results": []}
    receipt = _analysis_confirmation(context.store, wid, state)
    assert receipt, "confirmation must not be empty"
    # The body must carry the actual first and last source values, not a bare
    # "... aralığında:" header trailing off into nothing.
    assert "%39.67" in receipt, receipt
    assert "%51.85" in receipt, receipt
