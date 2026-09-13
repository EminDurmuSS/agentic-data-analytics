"""Display improvements must preserve saved evidence and original exports."""
import copy
import hashlib
import json

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


def test_single_period_summary_is_compact_even_after_source_research(saved_comparison):
    from agentic_analytics.agent.delivery import _analysis_confirmation
    from agentic_analytics.agent.tools.summary import SummaryTools
    context, _, wid, aid, run_id = saved_comparison
    summary = SummaryTools(context.store, wid).summarize_analysis(aid, columns=["company_million"], statistics=["last"])
    run = context.run_store.get(run_id)
    run["state"]["tool_results"] = [
        {"tool": "research_web", "result": {"status": "ok", "sources": []}},
        {"tool": "summarize_analysis", "result": summary},
    ]
    run["result"]["message"] = _analysis_confirmation(context.store, wid, run["state"]) + "\n\nKaynak: kayıtlı rapor."
    original, evidence = copy.deepcopy(run), _evidence_hashes(context)
    assert "2026-03 - 2026-03 (Mart 2026)" in run["result"]["message"]
    public = present_run(context.store, run)
    assert "Mart 2026: 225 milyon TL" in public["result"]["display_message"]
    assert "2026-03 - 2026-03" not in public["result"]["display_message"]
    assert public["result"]["display_message"].endswith("Kaynak: kayıtlı rapor.")
    assert public["result"]["message"] == original["result"]["message"]
    assert run == original and _evidence_hashes(context) == evidence


def test_named_summary_window_is_not_discarded(saved_comparison):
    from agentic_analytics.agent.delivery import _analysis_confirmation
    from agentic_analytics.agent.tools.summary import SummaryTools
    context, _, wid, aid, run_id = saved_comparison
    summary = SummaryTools(context.store, wid).summarize_analysis(
        aid, columns=["company_million"], statistics=["last"],
        windows=[{"label": "Denetlenen kapanış", "start": "2026-03", "end": "2026-03"}])
    run = context.run_store.get(run_id)
    run["state"]["tool_results"] = [{"tool": "summarize_analysis", "result": summary}]
    run["result"]["message"] = _analysis_confirmation(context.store, wid, run["state"])
    public = present_run(context.store, run)
    assert "Denetlenen kapanış (Mart 2026)" in public["result"]["display_message"]


@pytest.mark.parametrize("matching_hash", [True, False])
def test_chart_opaque_source_label_uses_registered_cover_not_search_title(saved_comparison, matching_hash):
    from agentic_analytics.agent.tools.charts import ChartTools
    context, client, wid, aid, _ = saved_comparison
    frame, manifest = context.store.load_analysis(aid)
    lineage = copy.deepcopy(manifest["lineage"])
    source_id, source_hash = "source_" + "a" * 64, "b" * 64
    url = "https://reports.example.test/download/" + "c" * 32
    lineage["sources"]["company_assets"]["binding"].update(
        source_system="SESSION_DATASET", title="source_financial_facts: amount",
        document_provenance={"source_id": source_id, "raw_sha256": source_hash, "source_url": url, "page": 12})
    directory = context.store._path("document_sources", wid, source_id)
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({
        "source_id": source_id, "raw_sha256": source_hash if matching_hash else "d" * 64,
        "source_url": url, "filename": "c" * 32, "title": "UNVERIFIED SEARCH TITLE"}))
    (directory / "inspection.json").write_text(json.dumps({"document_metadata": {}, "pages": [
        {"page": 1, "text": "Örnek Kurum\n31 Mart 2026\nKonsolide Finansal Rapor"}]}))
    saved = context.store.save_analysis(wid, frame, manifest["plan"], lineage, schema=manifest["schema"],
                                        expected_version=context.store.workspace(wid)["version"])
    charts = ChartTools(context.store, wid)
    created = charts.create_chart({"analysis_id": saved["analysis_id"], "columns": ["company_million"]})
    original = charts.load_artifact(created["chart_id"])
    evidence = _evidence_hashes(context)
    for endpoint in (f"/api/workspaces/{wid}/analyses/{saved['analysis_id']}/chart",
                     f"/api/workspaces/{wid}/charts/{created['chart_id']}"):
        response = client.get(endpoint)
        assert response.status_code == 200
        chart = response.json()
        label = chart["presentation"]["sources"][0]["label"]
        assert label == ("Örnek Kurum 31 Mart 2026 Konsolide Finansal Rapor" if matching_hash else "Kaynak rapor")
        assert chart["presentation"]["sources"][0]["page"] == 12
        assert chart["sources"] == original["sources"]
        assert chart["series"] == original["series"]
        assert chart["spec"] == original["spec"]
        assert "UNVERIFIED SEARCH TITLE" not in json.dumps(chart)
    assert charts.load_artifact(created["chart_id"]) == original
    assert _evidence_hashes(context) == evidence
