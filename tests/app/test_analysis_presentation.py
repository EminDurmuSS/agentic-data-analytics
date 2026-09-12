"""Display improvements must preserve saved evidence and original exports."""
import copy
import hashlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.presentation import present_run
from app.server import create_app


@pytest.fixture
def saved_comparison(tmp_path):
    app = create_app(runtime_root=tmp_path, source_db=None, client=None, searxng_url=False)
    context = app.state.context
    workspace = context.create_workspace("Comparison", "generic")
    wid = workspace["workspace_id"]
    frame = pd.DataFrame({"period": ["2026-03"], "company_assets": [225000],
                          "sector_assets": [1000], "company_million": [225], "ratio_percent": [22.5]})
    base = {"kind": "stock", "status": "ready", "unit": "TRY", "currency": "TRY", "scale": 1000000}
    schema = {"company_assets": {**base, "scale": 1000}, "sector_assets": base,
              "company_million": base, "ratio_percent": {"kind": "ratio", "unit": "percent", "scale": 1}}
    operations = [{"op": "scale", "column": "company_assets", "output": "company_million", "target_scale": 1000000},
                  {"op": "ratio", "column": "company_million", "denominator": "sector_assets",
                   "output": "ratio_percent", "multiplier": 100}]
    plan = {"start": "2026-03", "end": "2026-03", "frequency": "monthly", "operations": operations}
    lineage = {"frequency": "monthly", "operations": operations, "sources": {
        "company_assets": {"binding": {**schema["company_assets"], "title": "Şirket toplam aktifleri"}},
        "sector_assets": {"binding": {**base, "title": "Sektör toplam aktifleri"}},
    }}
    saved = context.store.save_analysis(wid, frame, plan, lineage, schema=schema, expected_version=0)
    run = context.run_store.start(wid, "Aynı ölçekte karşılaştır.")
    state = {**run["state"], "analysis_id": saved["analysis_id"], "analysis_updated": True}
    result = {"run_id": run["run_id"], "status": "completed", "message": "Eski teknik yanıt, ilk değer ve son değer.",
              "analysis_id": saved["analysis_id"], "analysis_updated": True, "tool_results": []}
    context.run_store.finish(run["run_id"], state, result)
    with TestClient(app) as client:
        yield context, client, wid, saved["analysis_id"], run["run_id"]
    context.pool.shutdown(wait=True)


def _evidence_hashes(context):
    return {str(path.relative_to(context.store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in context.store.root.rglob("*") if path.is_file() and path.suffix in {".json", ".parquet"}}


def test_saved_run_gets_new_display_without_changing_recorded_result(saved_comparison):
    context, client, wid, aid, run_id = saved_comparison
    before = copy.deepcopy(context.run_store.get(run_id))
    events = context.run_store.events(run_id)
    evidence = _evidence_hashes(context)
    public = client.get(f"/api/workspaces/{wid}").json()["runs"][0]
    text = public["result"]["display_message"]
    assert public["result"]["message"] == before["result"]["message"]
    assert public["status"] == public["result"]["status"] == "completed"
    assert "225" in text and "1.000" in text and "22,5" in text
    assert "225.000" not in text
    assert "İlk değer" not in text and "Son değer" not in text
    assert "company_assets" not in text and "company_million" not in text
    assert public["result"]["analysis_id"] == aid
    assert context.run_store.get(run_id) == before
    assert context.run_store.events(run_id) == events
    assert _evidence_hashes(context) == evidence


def test_default_table_projects_common_units_but_csv_keeps_original_values(saved_comparison):
    context, client, wid, aid, _ = saved_comparison
    response = client.get(f"/api/workspaces/{wid}/analyses/{aid}")
    assert response.status_code == 200
    analysis = response.json()
    assert analysis["presentation"]["columns"] == ["period", "company_million", "sector_assets", "ratio_percent"]
    assert "Şirket" in analysis["presentation"]["labels"]["company_million"]
    assert analysis["rows"][0]["company_assets"] == 225000
    assert analysis["rows"][0]["company_million"] == 225
    exported = client.get(f"/api/workspaces/{wid}/analyses/{aid}/csv").text
    assert "company_assets" in exported and "225000" in exported
    other = context.create_workspace("Other", "generic")["workspace_id"]
    assert client.get(f"/api/workspaces/{other}/analyses/{aid}").status_code == 404


def test_one_date_chart_uses_comparable_columns_and_no_trend_recommendation(saved_comparison):
    context, client, wid, aid, _ = saved_comparison
    before = context.store.workspace(wid)
    url = f"/api/workspaces/{wid}/analyses/{aid}/chart"
    payload = client.get(url).json()
    assert payload["spec"]["kind"] == "bar"
    assert payload["spec"]["columns"] == ["company_million", "sector_assets", "ratio_percent"]
    assert {item["column"]: item["values"] for item in payload["series"]} == {
        "company_million": [225], "sector_assets": [1000], "ratio_percent": [22.5]}
    assert not {"Başlangıcı 100 yap", "Birlikte değişimi gör"} & {
        item["label"] for item in payload["recommendations"]}
    explicit = client.post(url, json={"kind": "line", "columns": ["company_assets"]}).json()
    assert explicit["spec"]["columns"] == ["company_assets"]
    assert explicit["series"][0]["raw_values"] == [225000]
    assert explicit["spec"]["kind"] == "line"
    assert context.store.workspace(wid) == before


@pytest.mark.parametrize("status,message,extra", [
    ("partial", "Grafik üretilemedi.", {}),
    ("blocked", "Kaynak doğrulanamadı.", {}),
    ("completed", "Devam için soru: Hangi dönemi istiyorsunuz?", {}),
    ("completed", "Hata ayrıntıları.", {"errors": [{"code": "MISSING_SOURCE"}]}),
])
def test_projection_never_hides_incomplete_work_or_questions(saved_comparison, status, message, extra):
    context, _, _, _, run_id = saved_comparison
    run = context.run_store.get(run_id)
    run["status"] = status
    run["result"] = {**run["result"], "status": status, "message": message, **extra}
    original = copy.deepcopy(run)
    public = present_run(context.store, run)
    assert public["result"] == run["result"]
    assert "display_message" not in public["result"]
    assert run == original


def test_chart_only_followup_keeps_its_display_transformation_confirmation(saved_comparison):
    context, _, _, _, run_id = saved_comparison
    run = context.run_store.get(run_id)
    run["message"] = "Grafiğin başlangıcını 100 yap; tabloyu koru."
    run["state"].update(analysis_updated=False, analysis_observed=True, chart_updated=True)
    run["result"]["message"] = "Grafik başlangıç=100 olarak kaydedildi. Özgün tablo değerleri korundu."
    original = copy.deepcopy(run)
    public = present_run(context.store, run)
    assert public["result"] == original["result"]
    assert "display_message" not in public["result"]
    assert run == original
