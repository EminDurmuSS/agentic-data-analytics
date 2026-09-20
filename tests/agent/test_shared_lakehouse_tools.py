"""Agent authorization and interruption recovery for shared promotion."""

import copy
import json
from pathlib import Path

import duckdb
import pytest

from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.runtime import AgentRuntime, _shared_promotion_authorized
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.shared_lakehouse import SharedLakehouseTools
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore


def _call(name, arguments, call_id="call-1"):
    return {"role": "assistant", "content": None, "finish_reason": "stop", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    ], "usage": {"completion_tokens": 10}}


FINAL = {"role": "assistant", "content": "Kalıcı ortak sürüm hazır.", "finish_reason": "stop", "tool_calls": []}


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.options = []

    def chat(self, messages, **kwargs):
        self.options.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


def _environment(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
        connection.execute("INSERT INTO seed VALUES (1)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = store.publish_snapshot(database)["snapshot_id"]
    workspace = store.create_workspace(snapshot_id, "workspace_agent")
    documents = DocumentTools(store, workspace["workspace_id"], searxng_url=False)
    source = documents._register(
        b"month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n",
        "housing.csv", "text/csv", "https://www.bddk.org.tr/data/housing.csv",
    )
    documents.inspect_source(source_id=source["source_id"])
    published = documents.publish_selected_table(
        source["source_id"], "table_001",
        {
            "name": "Official Housing Credit", "frequency": "monthly", "date_column": "month",
            "key": ["month"], "grain": ["month"], "expected_rows": 2,
            "columns": {
                "month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                "housing_credit_million_TRY": {
                    "dtype": "integer", "unit": "TRY", "scale": 1_000_000, "currency": "TRY",
                    "kind": "stock", "aggregation": "last", "nullable": False,
                },
            },
        },
        expected_version=0,
        unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )
    shared = SharedLakehouse(store)
    tools = SharedLakehouseTools(store, workspace["workspace_id"], shared=shared).extra_tools()
    return store, workspace, published["dataset_id"], shared, tools


def _tool_names(runtime, message):
    state = {"request_message": message, "messages": [{"role": "user", "content": message}]}
    return {schema["function"]["name"] for schema in runtime._model_tool_schemas(state)}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Bu doğrulanmış veri setini kalıcı olarak ortak lakehouse'a ekle", True),
        ("Bu veriyi ortak lakehouse'a ekleyebilir misin?", True),
        ("Bu veriyi ortak veri katmanına al", True),
        ("Add this dataset to the shared lakehouse", True),
        ("Can you publish this dataset to the shared lakehouse?", True),
        ("Ortak lakehouse'a ekleme yapma", False),
        ("Workspace'e ekle, ortak lakehouse'a ekleme", False),
        ("Bu veri ortak lakehouse'a nasıl eklenir?", False),
        ("Can the system add this dataset to the shared lakehouse?", False),
        ("Do not add this dataset to the shared lakehouse", False),
    ],
)
def test_shared_promotion_authorization_requires_an_explicit_positive_write(message, expected):
    assert _shared_promotion_authorized(message) is expected


def test_promotion_tool_is_hidden_without_explicit_global_write_intent(tmp_path):
    store, workspace, _, shared, tools = _environment(tmp_path)
    journal = AgentRunStore(tmp_path / "runs")
    runtime = AgentRuntime(store, workspace["workspace_id"], ScriptedClient([]), journal, extra_tools=tools)

    assert "promote_dataset_to_shared_lakehouse" not in _tool_names(
        runtime, "Web'de bulduğun bu veriyi analiz et ve kaynağını göster",
    )
    assert "promote_dataset_to_shared_lakehouse" in _tool_names(
        runtime, "Bu doğrulanmış veri setini kalıcı olarak ortak lakehouse'a ekle",
    )
    assert shared.current_release() is None


def test_injected_unauthorised_promotion_call_is_blocked_before_handler(tmp_path):
    store, workspace, dataset_id, shared, tools = _environment(tmp_path)
    client = ScriptedClient([
        _call("promote_dataset_to_shared_lakehouse", {"dataset_id": dataset_id, "reason": "Model chose it"}),
        FINAL,
    ])
    runtime = AgentRuntime(
        store, workspace["workspace_id"], client, AgentRunStore(tmp_path / "runs"),
        extra_tools=tools, max_decisions=2, max_repairs=0,
    )

    result = runtime.run("Bu web tablosuyla aylık değişimi hesapla")

    promotion_result = next(item["result"] for item in result["tool_results"]
                            if item["tool"] == "promote_dataset_to_shared_lakehouse")
    assert promotion_result["errors"][0]["code"] == "SHARED_PROMOTION_NOT_AUTHORIZED"
    assert shared.current_release() is None


def test_interrupted_authorised_promotion_recovers_without_duplicate_release(tmp_path):
    store, workspace, dataset_id, shared, tools = _environment(tmp_path)
    run_root = tmp_path / "runs"
    journal = AgentRunStore(run_root)
    original_complete = journal.complete_step

    class Crash(BaseException):
        pass

    def crash_after_commit(*args, **kwargs):
        raise Crash()

    journal.complete_step = crash_after_commit
    request = "Bu doğrulanmış veri setini kalıcı olarak ortak lakehouse'a ekle"
    runtime = AgentRuntime(
        store, workspace["workspace_id"],
        ScriptedClient([_call("promote_dataset_to_shared_lakehouse", {
            "dataset_id": dataset_id, "reason": "Resmi ve doğrulanmış aylık seri",
        })]),
        journal, extra_tools=tools, max_decisions=3,
    )
    with pytest.raises(Crash):
        runtime.run(request, request_id="promotion-request")

    committed = shared.current_release()
    assert committed is not None
    assert committed["dataset_ids"] == [dataset_id]

    restarted_journal = AgentRunStore(run_root)
    recovered_runtime = AgentRuntime(
        store, workspace["workspace_id"], ScriptedClient([FINAL]), restarted_journal,
        extra_tools=tools, max_decisions=3,
    )
    result = recovered_runtime.run(request, request_id="promotion-request")

    promotion_result = next(item["result"] for item in result["tool_results"]
                            if item["tool"] == "promote_dataset_to_shared_lakehouse")
    assert result["status"] == "completed"
    assert promotion_result["recovered"] is True
    assert promotion_result["shared_release_id"] == committed["release_id"]
    assert len(list((shared.root / "promotions").glob("promotion_*"))) == 1
    assert len(list((shared.root / "releases").glob("release_*"))) == 1
    journal.complete_step = original_complete
