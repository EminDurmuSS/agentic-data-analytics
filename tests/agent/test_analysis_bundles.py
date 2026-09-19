"""Immutable mixed-frequency analysis bundle contracts."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from agentic_analytics.agent.context import workspace_context
from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.runtime import AgentRuntime
from agentic_analytics.agent.tools.bundles import AnalysisBundleError, AnalysisBundleTools
from agentic_analytics.lakehouse.store import LakehouseStore


def call(name, arguments, identifier=None):
    return {"content": None, "finish_reason": "stop", "tool_calls": [{
        "id": identifier or name,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


def final(text="Analiz paketi kaydedildi."):
    return {"content": text, "finish_reason": "stop", "tool_calls": []}


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, **options):
        self.requests.append(copy.deepcopy(messages))
        assert self.responses, "Unexpected provider request"
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


@pytest.fixture
def bundle_env(tmp_path):
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "store")
    workspace = store.create_workspace(store.publish_snapshot(database)["snapshot_id"], "workspace_bundle")

    def save(frame, frequency, column, kind, *, native_frequency=None, alignment="native", additive=False):
        current = store.workspace(workspace["workspace_id"])
        metric_id = f"fixture:{column}"
        schema = {
            "period": {"kind": "dimension", "unit": "period", "status": "ready"},
            column: {
                "kind": kind,
                "unit": "percent" if kind == "rate" else "TRY",
                "scale": 1 if kind == "rate" else 1_000_000,
                "currency": None if kind == "rate" else "TRY",
                "status": "ready",
                "metric_id": metric_id,
                "additive_over_time": additive,
            },
        }
        binding = {
            "metric_id": metric_id,
            "title": column,
            "source_code": column,
            "source_system": "FIXTURE",
            "source_organization": "Fixture Authority",
            "source_url": "https://example.org/source",
            "source_metadata_url": "https://example.org/metadata",
            "binding_sha256": "a" * 64,
            "native_frequency": native_frequency or frequency,
            "unit": schema[column]["unit"],
            "scale": schema[column]["scale"],
            "currency": schema[column]["currency"],
            "kind": kind,
        }
        lineage = {
            "frequency": frequency,
            "sources": {column: {"binding": binding, "alignment": alignment, "dimensions": {}}},
            "operations": [],
            "warnings": [],
        }
        plan = {
            "start": frame["period"].iloc[0],
            "end": frame["period"].iloc[-1],
            "frequency": frequency,
            "columns": [{"name": column, "metric_id": metric_id, "alignment": alignment}],
        }
        return store.save_analysis(
            workspace["workspace_id"], frame, plan, lineage, schema=schema,
            expected_version=current["version"],
        )["analysis_id"]

    monthly = save(
        pd.DataFrame({"period": ["2025-01", "2025-02"], "credit": [100.0, 110.0]}),
        "monthly", "credit", "stock",
    )
    quarterly = save(
        pd.DataFrame({"period": ["2025-Q1", "2025-Q2"], "province_credit": [500.0, float("nan")]}),
        "quarterly", "province_credit", "stock",
    )
    aligned = save(
        pd.DataFrame({"period": ["2025-01", "2025-02"], "monthly_rate": [40.5, 39.9]}),
        "monthly", "monthly_rate", "rate", native_frequency="weekly_friday", alignment="mean",
    )
    return store, workspace["workspace_id"], monthly, quarterly, aligned


def test_bundle_retains_native_grains_sources_transformations_and_null_policy(bundle_env):
    store, workspace_id, monthly, quarterly, aligned = bundle_env
    tools = AnalysisBundleTools(store, workspace_id)
    saved = tools.save_analysis_bundle(
        [
            {"analysis_id": monthly, "role": "monthly_credit", "label": "Aylık kredi stoku"},
            {"analysis_id": quarterly, "role": "quarterly_city", "label": "Çeyreklik il kredisi"},
            {"analysis_id": aligned, "role": "monthly_rate", "label": "Aylık ortalama faiz"},
        ],
        "2025 konut finansmanı",
        purpose="Doğal frekansları koruyan karşılaştırma",
    )
    bundle = tools.load_bundle(saved["bundle_id"])
    assert bundle["frequencies"] == ["monthly", "quarterly"]
    assert bundle["frequency_policy"] == {
        "mode": "separate_immutable_analyses",
        "mixed_grain_single_table": False,
        "quarterly_values_copied_to_months": False,
        "native_period_labels_preserved": True,
    }
    assert bundle["missing_value_policy"] == {
        "mode": "preserve_nulls",
        "zero_fill": False,
        "forward_fill": False,
        "interpolation": False,
        "estimation": False,
    }
    by_role = {component["role"]: component for component in bundle["components"]}
    assert by_role["quarterly_city"]["period_labels"] == ["2025-Q1", "2025-Q2"]
    quarterly_column = next(column for column in by_role["quarterly_city"]["columns"] if column["name"] == "province_credit")
    assert quarterly_column["missing_count"] == 1
    assert not quarterly_column["additive_over_time"]
    aligned_source = by_role["monthly_rate"]["sources"][0]
    assert aligned_source["metric_id"] == "fixture:monthly_rate"
    assert aligned_source["native_frequency"] == "weekly_friday"
    assert aligned_source["output_frequency"] == "monthly"
    assert aligned_source["alignment"] == "mean"
    assert by_role["monthly_rate"]["transformations"]["source_alignments"] == [{
        "column": "monthly_rate",
        "metric_id": "fixture:monthly_rate",
        "native_frequency": "weekly_friday",
        "output_frequency": "monthly",
        "method": "mean",
    }]
    assert store.workspace(workspace_id)["version"] == 3


def test_parent_bundle_preserves_roles_and_replaces_only_named_role(bundle_env):
    store, workspace_id, monthly, quarterly, aligned = bundle_env
    tools = AnalysisBundleTools(store, workspace_id)
    first = tools.save_analysis_bundle([
        {"analysis_id": monthly, "role": "monthly_view"},
        {"analysis_id": quarterly, "role": "quarterly_view"},
    ], "İlk paket")
    updated = tools.save_analysis_bundle([
        {"analysis_id": aligned, "role": "monthly_view", "label": "Aylık hizalanmış görünüm"},
    ], "Güncel paket", parent_bundle_id=first["bundle_id"])
    bundle = tools.load_bundle(updated["bundle_id"])
    assert bundle["parent_bundle_id"] == first["bundle_id"]
    roles = {component["role"]: component["analysis_id"] for component in bundle["components"]}
    assert roles == {"monthly_view": aligned, "quarterly_view": quarterly}
    assert tools.load_bundle(first["bundle_id"])["components"][0]["analysis_id"] == monthly


def test_bundle_rejects_duplicate_analysis_roles_and_cross_workspace_components(bundle_env):
    store, workspace_id, monthly, quarterly, _ = bundle_env
    tools = AnalysisBundleTools(store, workspace_id)
    with pytest.raises(AnalysisBundleError, match="cannot fill multiple"):
        tools.save_analysis_bundle([
            {"analysis_id": monthly, "role": "one"},
            {"analysis_id": monthly, "role": "two"},
        ], "Duplicate")

    other = store.create_workspace(store.workspace(workspace_id)["snapshot_id"], "workspace_other_bundle")
    other_analysis = store.save_analysis(
        other["workspace_id"], pd.DataFrame({"period": ["2025-01"], "value": [1.0]}),
        {"frequency": "monthly"}, {"frequency": "monthly"}, expected_version=0,
    )["analysis_id"]
    with pytest.raises(AnalysisBundleError) as error:
        tools.save_analysis_bundle([
            {"analysis_id": monthly, "role": "one"},
            {"analysis_id": other_analysis, "role": "two"},
        ], "Cross workspace")
    assert error.value.code == "WORKSPACE_MISMATCH"
    assert tools.load_bundle(tools.save_analysis_bundle([
        {"analysis_id": monthly, "role": "one"},
        {"analysis_id": quarterly, "role": "two"},
    ], "Valid")["bundle_id"])["workspace_id"] == workspace_id


def test_runtime_persists_bundle_identity_and_exposes_compact_followup_context(bundle_env, tmp_path):
    store, workspace_id, monthly, quarterly, _ = bundle_env
    journal = AgentRunStore(tmp_path / "runs")
    tools = AnalysisBundleTools(store, workspace_id)
    arguments = {
        "components": [
            {"analysis_id": monthly, "role": "monthly_view"},
            {"analysis_id": quarterly, "role": "quarterly_view"},
        ],
        "title": "Çok frekanslı görünüm",
    }
    client = Client([
        call("plan_task", {"deliverables": ["bundle"]}),
        call("save_analysis_bundle", arguments),
        final(),
    ])
    runtime = AgentRuntime(
        store, workspace_id, client, journal,
        extra_tools=tools.extra_tools(), max_decisions=5,
    )
    first = runtime.run("Aylık ve çeyreklik analizleri frekanslarını bozmadan birlikte kaydet.")
    assert first["status"] == "completed"
    assert first["bundle_updated"]
    assert first["analysis_bundle_id"].startswith("analysis_bundle_")
    assert "çeyreklik değer aylara kopyalanmaz" in first["message"]

    second_client = Client([final("Paketin toplama politikasını açıkla.")])
    second_runtime = AgentRuntime(
        store, workspace_id, second_client, journal,
        extra_tools=tools.extra_tools(), max_decisions=2,
    )
    second = second_runtime.run(
        "Bu pakette hangi seriler zaman boyunca toplanabilir?",
        conversation_id=first["conversation_id"],
    )
    assert second["status"] == "completed"
    context_text = second_client.requests[0][0]["content"]
    assert first["analysis_bundle_id"] in context_text
    assert "quarterly_values_copied_to_months" in context_text
    assert "sum_forbidden_stock_rate_ratio_or_unverified" in context_text

    state = journal.get(second["run_id"])["state"]
    context = workspace_context(store, workspace_id, state, max_decisions=5, charts_enabled=False)
    assert context["active_analysis_bundle"]["bundle_id"] == first["analysis_bundle_id"]
    assert {component["role"] for component in context["active_analysis_bundle"]["components"]} == {
        "monthly_view", "quarterly_view",
    }
