"""Summary downloads verify workspace ownership and immutable saved bytes."""
import pandas as pd
from fastapi.testclient import TestClient

from app.server import create_app
from agentic_analytics.agent.tools.summary import SummaryTools


def test_summary_download_is_verified_and_cannot_cross_workspaces(tmp_path):
    app = create_app(runtime_root=tmp_path, source_db=None, client=None, searxng_url=False)
    context = app.state.context
    with TestClient(app) as client:
        first = context.create_workspace("Summary", "generic")
        second = context.create_workspace("Other", "generic")
        frame = pd.DataFrame({"period": ["2026-01", "2026-02"], "profit": [10, 25]})
        saved = context.store.save_analysis(first["workspace_id"], frame, {"frequency": "monthly"},
            {"frequency": "monthly"}, schema={"profit": {"kind": "flow", "unit": "TRY", "scale": 1000000, "status": "ready"}}, expected_version=0)
        summaries = SummaryTools(context.store, first["workspace_id"])
        summary = summaries.summarize_analysis(saved["analysis_id"], statistics=["sum"])
        url = f"/api/workspaces/{first['workspace_id']}/summaries/{summary['summary_id']}"
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["facts"][0]["value"] == 35
        assert client.get(url.replace(first["workspace_id"], second["workspace_id"])).status_code == 404
        artifact = summaries.root / (summary["summary_id"] + ".json")
        artifact.write_text(artifact.read_text().replace('35', '99'))
        assert client.get(url).status_code == 404
    context.pool.shutdown(wait=True)
