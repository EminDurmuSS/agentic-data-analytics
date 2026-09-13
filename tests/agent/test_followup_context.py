"""Feasibility and evidence tests with synthetic, explicitly labelled fixtures."""

import copy
import hashlib
import json

import duckdb
import pandas as pd
import pytest

from agentic_analytics.agent.followup_context import build_followup_context, MAX_CONTEXT_CHARS
from agentic_analytics.agent.tools.statistics import StatisticsTools
from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.store import LakehouseStore


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE seed (value INTEGER)")
    result = LakehouseStore(tmp_path / "store")
    snapshot = result.publish_snapshot(database)
    result.create_workspace(snapshot["snapshot_id"], "followup_test")
    result.create_workspace(snapshot["snapshot_id"], "other")
    return result


def measure(label="Örnek aktifler", **overrides):
    return {"kind": "stock", "status": "ready", "unit": "TRY", "currency": "TRY",
            "scale": 1, "scope": {"population": "synthetic_fixture"}, "label": label, **overrides}


def save(store, data, *, schema=None, lineage=None, frequency="monthly", workspace="followup_test"):
    frame = pd.DataFrame(data)
    for column in frame:
        if column != "period" and all(pd.isna(value) or isinstance(value, (int, float)) for value in frame[column]):
            frame[column] = pd.Series(frame[column], dtype="Float64")
    schema = schema or {column: measure() for column in frame if column != "period"}
    saved = store.save_analysis(workspace, frame, {"frequency": frequency},
        {"frequency": frequency, **(lineage or {})}, schema=schema,
        expected_version=store.workspace(workspace)["version"])
    return {"workspace_id": workspace, "run_id": "run_current", "conversation_id": "conversation_one",
            "message": "Sonucu göster.", "result": {"status": "completed", "message": "Sonuç hazır.",
            "analysis_id": saved["analysis_id"], "tool_results": []}}


def context(store, run, history=None):
    return build_followup_context(store, "followup_test", run, history or [])


def intents(value):
    return {candidate["intent"] for candidate in value["candidates"]}


def candidate(value, intent):
    return next(item for item in value["candidates"] if item["intent"] == intent)


def source(title, *, pdf=False, dimensions=None, metric_id=None):
    binding = {"title": title, "source_system": "TEST"}
    if pdf:
        binding["document_provenance"] = {"source_id": "source_" + "a" * 64,
            "raw_sha256": "b" * 64, "source_url": "https://example.org/synthetic-report.pdf",
            "table_id": "table_p000011_text_001", "page": 11}
    if metric_id:
        binding["metric_id"] = metric_id
    return {"binding": binding, "dimensions": dimensions or {}}


def test_single_period_pdf_uses_own_column_and_done_ratio_is_not_repeated(store):
    run = save(store, {"period": ["2026-03"], "ratio_pct": [10.0], "sector_raw": [1000],
        "firm_raw": [100000], "firm_common": [100]}, schema={
        "ratio_pct": measure("Örnek karşılaştırma", kind="ratio", unit="percent", currency=None),
        "sector_raw": measure("Örnek sektör toplam aktifleri"),
        "firm_raw": measure("Örnek şirket toplam aktifleri", scale=1000),
        "firm_common": measure("Örnek şirket toplam aktifleri", scale=1000000)},
        lineage={"sources": {"sector_raw": source("Örnek sektör toplam aktifleri"),
                            "firm_raw": source("Örnek şirket toplam aktifleri", pdf=True)},
        "operations": [{"op": "scale", "column": "firm_raw", "output": "firm_common", "target_scale": 1000000},
                       {"op": "ratio", "column": "firm_common", "denominator": "sector_raw", "output": "ratio_pct"}],
        "warnings": [{"code": "cross_scope_comparison"}]})
    value = context(store, run)
    assert {"find_comparison_period", "source_components", "matched_scope"} <= intents(value)
    assert not {"growth", "difference", "relationship", "anomalies", "ratio", "focus_change"} & intents(value)
    prior = candidate(value, "find_comparison_period")
    assert prior["columns"] == ["firm_common"]
    assert "Örnek şirket toplam aktifleri" in prior["prompt"] and "Mart 2026" in prior["prompt"]
    assert "varsa" in prior["prompt"] and prior["requires_new_data"] is True
    components = candidate(value, "source_components")
    assert components["columns"] == ["sector_raw"] and "Örnek sektör toplam aktifleri" in components["prompt"]
    assert "ratio_pct" not in components["prompt"]
    assert all(item["requires_new_data"] for item in value["candidates"] if item["intent"] in {"matched_scope", "source_components", "find_comparison_period"})


def test_unassociated_pdf_cannot_offer_another_series_a_report_period(store):
    run = save(store, {"period": ["2026-03"], "sector": [100]}, lineage={
        "sources": {"unselected_firm": source("Örnek şirket", pdf=True), "sector": source("Örnek sektör")}})
    assert "find_comparison_period" not in intents(context(store, run))


def test_dataset_source_columns_are_used_for_native_grouped_report(store):
    proof = source("Örnek", pdf=True)["binding"]["document_provenance"]
    run = save(store, {"period": ["2026-03-31", "2026-03-31"], "line_item": ["Cash", "Total assets"], "value": [20, 100]},
        schema={"value": measure("Tutar"), "line_item": {"kind": "dimension", "unit": "label"}}, frequency="event",
        lineage={"group_by": "line_item", "dataset_query": {"document_provenance": proof,
            "cells": {"[date,cash]": {"value": {"source_rows": [1]}}}}})
    value = context(store, run)
    assert candidate(value, "find_comparison_period")["columns"] == ["value"]
    assert "Total assets" in candidate(value, "source_components")["prompt"]
    assert not {"growth", "relationship", "difference"} & intents(value)


def test_plain_explanation_does_not_borrow_unrelated_workspace_head(store):
    save(store, {"period": ["2026-01", "2026-02"], "value": [10, 20]})
    run = {"run_id": "explanation", "conversation_id": "conversation_one", "message": "Reel değer nedir?",
           "result": {"status": "completed", "message": "Fiyat değişimini ayıran bir ölçüdür."}}
    value = context(store, run)
    assert value["analysis_id"] is None and not value["analysis"]
    assert intents(value) == {"apply_explanation", "explain_limits"}


def test_successful_current_read_observation_is_bound_but_failed_or_historical_read_is_not(store):
    run = save(store, {"period": ["2026-01", "2026-02"], "value": [10, 20]})
    aid = run["result"].pop("analysis_id")
    run["result"]["tool_results"] = [{"tool": "read_analysis", "result": {"status": "ok", "analysis_id": aid}}]
    assert context(store, run)["analysis_id"] == aid
    run["result"]["tool_results"][0]["result"]["status"] = "error"
    assert context(store, run)["analysis_id"] is None


@pytest.mark.parametrize("cross_workspace", [False, True])
def test_unverified_analysis_is_not_used(store, cross_workspace):
    run = save(store, {"period": ["2026-01"], "value": [10]}, workspace="other" if cross_workspace else "followup_test")
    run["workspace_id"] = "followup_test"
    if not cross_workspace:
        path = store.root / "analyses" / run["result"]["analysis_id"] / "data.parquet"
        path.chmod(0o644)
        path.write_bytes(path.read_bytes() + b"tamper")
    value = context(store, run)
    assert value["analysis"]["status"] == "unavailable" and value["candidates"] == []


def test_exact_saved_values_select_largest_change_instead_of_latest_period(store):
    run = save(store, {"period": ["2026-01", "2026-02", "2026-03", "2026-04"], "raw_alias": [10, 12, 80, 81]},
               schema={"raw_alias": measure("Örnek konut kredileri")})
    value = context(store, run)
    focus = candidate(value, "focus_change")
    assert focus["start"] == "2026-02" and focus["end"] == "2026-03"
    assert "Şubat 2026" in focus["prompt"] and "Mart 2026" in focus["prompt"]
    assert "Örnek konut kredileri" in focus["prompt"] and "raw_alias" not in focus["prompt"]
    assert value["analysis"]["observed_changes"] == [{"column": "raw_alias", "previous_period": "2026-02", "period": "2026-03",
        "previous_value": 12, "value": 80, "change": 68, "selection": "largest_absolute_adjacent_change_within_same_series", "unit": "TRY", "scale": 1}]


def test_already_requested_largest_change_is_not_offered_again(store):
    run = save(store, {"period": ["2026-01", "2026-02", "2026-03"], "value": [10, 20, 100]})
    run["message"] = "Bu serideki en büyük dönem değişimini açıklar mısın?"
    value = context(store, run)
    assert "focus_change" not in intents(value)
    assert value["analysis"]["observed_changes"][0]["change"] == 80


def test_ratio_named_total_does_not_become_a_total_components_question(store):
    run = save(store, {"period": ["2026-03"], "ratio": [10]}, schema={
        "ratio": measure("Toplam içindeki oran", kind="ratio", unit="percent", currency=None)},
        lineage={"sources": {"ratio": source("Toplam içindeki oran", pdf=True)}})
    assert "source_components" not in intents(context(store, run))


@pytest.mark.parametrize("extra,frequency,periods", [
    ({"kind": "unknown"}, "monthly", ["2026-01", "2026-02", "2026-03"]),
    ({"status": "review_required"}, "monthly", ["2026-01", "2026-02", "2026-03"]),
    ({"kind": "flow", "temporal_basis": "year_to_date"}, "monthly", ["2026-01", "2026-02", "2026-03"]),
    ({}, "event", ["2026-01-31", "2026-02-28", "2026-03-31"]),
    ({}, "monthly", ["2026-01", "2026-03", "2026-04"]),
])
def test_semantic_or_calendar_ambiguity_never_becomes_arithmetic_candidate(store, extra, frequency, periods):
    run = save(store, {"period": periods, "value": [10, 12, 80]}, schema={"value": measure(**extra)}, frequency=frequency)
    assert not {"focus_change", "difference", "growth", "annual_growth", "period_sum", "relationship", "anomalies"} & intents(context(store, run))


def test_rate_difference_is_percentage_points_without_interest_or_growth_topic(store):
    run = save(store, {"period": ["2026-01", "2026-02"], "rate": [8.1, 8.3]},
               schema={"rate": measure("Örnek işsizlik oranı", kind="rate", unit="percent", currency=None)})
    value = context(store, run)
    assert "yüzde puan" in candidate(value, "difference")["prompt"]
    assert not {"growth", "annual_growth", "period_sum"} & intents(value)
    assert "faiz" not in str(value["candidates"]).lower()


def test_group_rows_do_not_count_as_calendar_samples_or_cross_population_sum(store):
    periods = ["2026-01", "2026-02", "2026-03"]
    data = {"period": [period for group in range(10) for period in periods],
            "bank": [f"Örnek grup {group}" for group in range(10) for _ in periods], "value": list(range(1, 31))}
    run = save(store, data, schema={"value": measure("Örnek net kâr", kind="flow")}, lineage={"group_by": "bank"})
    value = context(store, run)
    assert value["analysis"]["period_count"] == 3 and value["analysis"]["row_count"] == 30
    assert not {"relationship", "anomalies", "annual_growth", "focus_change"} & intents(value)
    assert "grupları birbirine ekleme" in candidate(value, "period_sum")["prompt"]


def test_missing_group_memberships_cannot_generate_nonadjacent_growth_or_partial_sum(store):
    run = save(store, {"period": ["2026-01", "2026-03", "2026-02"], "bank": ["A", "A", "B"], "value": [10, 30, 20]},
        schema={"value": measure(kind="flow")}, lineage={"group_by": "bank", "warnings": [{"code": "group_missing_observations"}]})
    value = context(store, run)
    assert "missingness" in intents(value)
    assert not {"growth", "difference", "period_sum"} & intents(value)


@pytest.mark.parametrize("count,missing,constant,expected", [(11, 0, False, False), (12, 0, False, True),
    (15, 3, False, True), (16, 4, False, False), (12, 0, True, False)])
def test_correlation_requires_actual_joint_variable_observations(store, count, missing, constant, expected):
    run = save(store, {"period": pd.period_range("2025-01", periods=count, freq="M").astype(str),
        "x": list(range(count)), "y": [None] * missing + ([5] * (count - missing) if constant else [i * i for i in range(missing, count)])},
        schema={"x": measure("Örnek kredi"), "y": measure("Örnek mevduat")})
    assert ("relationship" in intents(context(store, run))) is expected


def test_mechanical_scale_and_duplicate_original_bindings_are_not_relationships(store):
    periods = pd.period_range("2025-01", periods=12, freq="M").astype(str)
    for lineage in [{"operations": [{"op": "scale", "column": "x", "output": "y", "target_scale": 1000}]},
                    {"sources": {"x": source("Örnek", metric_id="fixture:amount"), "y": source("Örnek", metric_id="fixture:amount")}}]:
        run = save(store, {"period": periods, "x": list(range(12)), "y": [i / 1000 for i in range(12)]}, lineage=lineage)
        assert not {"relationship", "ratio"} & intents(context(store, run))


def test_recorded_operations_and_actual_history_suppress_repeated_requests(store):
    run = save(store, {"period": pd.period_range("2025-01", periods=13, freq="M").astype(str),
        "value": list(range(100, 113)), "yoy": [None] * 12 + [12]}, schema={"value": measure(), "yoy": measure(kind="ratio", currency=None, unit="percent")},
        lineage={"operations": [{"op": "growth", "column": "value", "output": "yoy", "periods": 12}]})
    earlier = copy.deepcopy(run)
    earlier.update(run_id="earlier", message="Aylık farkları göster.")
    earlier["result"]["message"] = "Aylık farklar gösterildi."
    foreign = {**earlier, "conversation_id": "other_conversation", "message": "Farklı konuşma"}
    run["message"] = "Şimdi tabloyu göster."
    run["result"]["display_message"] = "Okunabilir teslim edilen yanıt."
    value = context(store, run, [earlier, foreign, run])
    assert not {"annual_growth", "difference"} & intents(value)
    assert value["history"] == [{"question": "Aylık farkları göster.", "answer": "Aylık farklar gösterildi."}]
    assert value["answer"] == "Okunabilir teslim edilen yanıt."


def test_verified_summary_prevents_repeat_but_tampered_artifact_is_not_evidence(store):
    run = save(store, {"period": ["2026-01", "2026-02", "2026-03"], "value": [10, 20, 30]}, schema={"value": measure(kind="flow")})
    tools = SummaryTools(store, "followup_test")
    artifact = tools.summarize_analysis(run["result"]["analysis_id"], statistics=["sum"])
    run["result"]["tool_results"] = [{"tool": "summarize_analysis", "result": artifact}]
    value = context(store, run)
    assert "period_sum" not in intents(value)
    assert value["analysis"]["verified_summary_facts"][0]["value"] == 60
    (tools.root / (artifact["summary_id"] + ".json")).write_text("{}")
    value = context(store, run)
    assert value["analysis"]["verified_summary_facts"] == [] and "period_sum" in intents(value)


def test_completed_statistics_are_verified_and_not_suggested_again(store):
    run = save(store, {"period": pd.period_range("2025-01", periods=12, freq="M").astype(str),
        "x": list(range(12)), "y": [i * i for i in range(12)]})
    tools = StatisticsTools(store, "followup_test")
    aid = run["result"]["analysis_id"]
    artifacts = [tools.analyze_relationship(aid, "x", "y"), tools.rolling_anomalies(aid, "x"), tools.rolling_anomalies(aid, "y")]
    earlier = copy.deepcopy(run)
    earlier["run_id"] = "earlier"
    earlier["result"]["tool_results"] = [{"tool": "analyze_relationship" if index == 0 else "rolling_anomalies", "result": artifact}
                                         for index, artifact in enumerate(artifacts)]
    value = context(store, run, [earlier])
    assert not {"relationship", "anomalies"} & intents(value)
    assert len(value["analysis"]["completed_statistics"]) == 3


def test_context_is_deterministic_bounded_diverse_and_does_not_write(store):
    data = {"period": pd.period_range("2025-01", periods=24, freq="M").astype(str)}
    data.update({f"column_{i}": [(j + i + 1) ** 2 for j in range(24)] for i in range(16)})
    run = save(store, data, schema={column: measure("Örnek çok uzun başlık " * 12, kind="flow") for column in data if column != "period"})
    history = [{**copy.deepcopy(run), "run_id": f"old_{i}", "message": "Çok uzun bağlam " * 500} for i in range(6)]
    def snapshot():
        return {str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in store.root.rglob("*") if path.is_file()}
    before = snapshot()
    value = context(store, run, history)
    assert value == context(store, run, history) and snapshot() == before
    assert len(json.dumps(value, ensure_ascii=True, separators=(",", ":"))) <= MAX_CONTEXT_CHARS
    assert 1 <= len(value["candidates"]) <= 12
    assert len(intents(value)) >= 3
    assert len({item["id"] for item in value["candidates"]}) == len(value["candidates"])
    assert all(10 <= len(item["prompt"]) <= 700 for item in value["candidates"])
