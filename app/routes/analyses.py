"""Saved analysis tables, exports, charts, statistics, and source-cell evidence."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.presentation import analysis_presentation
from app.context import AppContext
from app.serialization import browser_json
from app.presentation import present_chart


def create_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workspaces/{workspace_id}/discover")
    def discover(workspace_id: str, query: str = "", limit: int = 10):
        return LakehouseService(context.store, workspace_id).discover({"query": query, "limit": limit})

    @router.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}")
    def analysis(workspace_id: str, analysis_id: str, offset: int = 0, limit: int = 250):
        if not 0 <= offset or not 1 <= limit <= 2000:
            raise HTTPException(400, "Geçersiz sayfalama.")
        frame, manifest = context.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != workspace_id:
            raise HTTPException(404, "Analiz bu çalışma alanında bulunamadı.")
        return browser_json({"analysis_id": analysis_id, "parent_analysis_id": manifest.get("parent_analysis_id"), "columns": list(frame), "presentation": analysis_presentation(frame, manifest), "schema": manifest.get("schema", {}), "row_count": len(frame), "offset": offset, "warnings": manifest.get("lineage", {}).get("warnings", []), "preserved_columns": list(manifest.get("lineage", {}).get("preserved_columns", {})), "rows": frame.iloc[offset:offset + limit].to_dict("records"), "plan": manifest["plan"], "sources": {name: {"metric_id": proof.get("binding", {}).get("metric_id"), "title": proof.get("binding", {}).get("title"), "unit": proof.get("binding", {}).get("unit"), "scale": proof.get("binding", {}).get("scale"), "source_system": proof.get("binding", {}).get("source_system")} for name, proof in manifest.get("lineage", {}).get("sources", {}).items()}})

    @router.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/csv")
    def download(workspace_id: str, analysis_id: str):
        frame, manifest = context.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != workspace_id:
            raise HTTPException(404, "Analiz bu çalışma alanında bulunamadı.")
        safe = frame.copy()
        for column in safe.select_dtypes(include=["object", "string"]):
            safe[column] = safe[column].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v)
        return Response(safe.to_csv(index=False).encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="analysis.csv"'})

    @router.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/chart")
    def analysis_chart(workspace_id: str, analysis_id: str):
        from agentic_analytics.agent.tools.charts import ChartTools
        return browser_json(present_chart(context.store, ChartTools(context.store, workspace_id).get_chart(analysis_id)))

    @router.post("/api/workspaces/{workspace_id}/analyses/{analysis_id}/chart")
    def save_chart(workspace_id: str, analysis_id: str, body: dict):
        from agentic_analytics.agent.tools.charts import ChartTools, ChartError
        if "analysis_id" in body:
            raise ChartError("Analiz kimliği URL üzerinden seçilir.")
        with context.run_store.workspace_lock(workspace_id):
            charts = ChartTools(context.store, workspace_id)
            saved = charts.create_chart({**body, "analysis_id": analysis_id})
            return browser_json(present_chart(context.store, charts.load_artifact(saved["chart_id"])))

    @router.get("/api/workspaces/{workspace_id}/charts/{chart_id}")
    def chart_artifact(workspace_id: str, chart_id: str):
        from agentic_analytics.agent.tools.charts import ChartTools
        return browser_json(present_chart(context.store, ChartTools(context.store, workspace_id).load_artifact(chart_id)))

    @router.get("/api/workspaces/{workspace_id}/analyses/{analysis_id}/explain")
    def explain(workspace_id: str, analysis_id: str, column: str, period: str, dimensions: str | None = None):
        arguments = {"analysis_id": analysis_id, "column": column, "period": period}
        if dimensions is not None:
            try:
                value = json.loads(dimensions)
                if not isinstance(value, dict) or len(value) > 10:
                    raise ValueError()
                import re
                for key, item in value.items():
                    if isinstance(item, dict) and set(item) == {"$integer"}:
                        if not isinstance(item["$integer"], str) or not re.fullmatch(r"-?\d{1,19}", item["$integer"]):
                            raise ValueError()
                        value[key] = int(item["$integer"])
                arguments["dimensions"] = value
            except ValueError:
                raise HTTPException(400, "Geçersiz hücre boyutları.") from None
        return browser_json(LakehouseService(context.store, workspace_id).explain_value(arguments))

    @router.get("/api/workspaces/{workspace_id}/statistics/{artifact_id}")
    def statistics(workspace_id: str, artifact_id: str):
        from agentic_analytics.agent.tools.statistics import StatisticsTools, StatisticsError
        try:
            return StatisticsTools(context.store, workspace_id).load_artifact(artifact_id)
        except (StatisticsError, FileNotFoundError):
            raise HTTPException(404, "İstatistik kaydı bu çalışma alanında bulunamadı.") from None

    @router.get("/api/workspaces/{workspace_id}/summaries/{artifact_id}")
    def summary(workspace_id: str, artifact_id: str):
        from agentic_analytics.agent.tools.summary import SummaryTools
        try:
            return browser_json(SummaryTools(context.store, workspace_id).load_artifact(artifact_id))
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Hesap özeti bu çalışma alanında bulunamadı.") from None

    @router.get("/api/workspaces/{workspace_id}/selections/{artifact_id}")
    def selection(workspace_id: str, artifact_id: str):
        from agentic_analytics.agent.tools.selection import AnalysisSelectionTools, SelectionError
        try:
            return browser_json(AnalysisSelectionTools(context.store, workspace_id).load_artifact(artifact_id))
        except (SelectionError, FileNotFoundError):
            raise HTTPException(404, "Satır seçimi bu çalışma alanında bulunamadı.") from None

    @router.get("/api/workspaces/{workspace_id}/analysis-bundles/{bundle_id}")
    def analysis_bundle(workspace_id: str, bundle_id: str):
        from agentic_analytics.agent.tools.bundles import AnalysisBundleError, AnalysisBundleTools
        try:
            return browser_json(AnalysisBundleTools(context.store, workspace_id).load_bundle(bundle_id))
        except (AnalysisBundleError, FileNotFoundError, ValueError):
            raise HTTPException(404, "Analiz paketi bu çalışma alanında bulunamadı.") from None

    return router
