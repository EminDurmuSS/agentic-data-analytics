"""Application lifecycle coverage for shared web-dataset releases."""

from pathlib import Path

import duckdb

from app.context import AppContext
from agentic_analytics.agent.tools.documents import OFFICIAL_SOURCE_REGISTRY


def _database(path: Path):
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
        connection.execute("INSERT INTO seed VALUES (1)")


def _publish(context: AppContext, workspace_id: str):
    documents = context.documents(workspace_id)
    source = documents._register(
        b"month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n",
        "housing.csv", "text/csv", "https://www.bddk.org.tr/data/housing.csv",
    )
    documents.inspect_source(source_id=source["source_id"])
    return documents.publish_selected_table(
        source["source_id"], "table_001",
        {
            "name": "Official Housing Credit", "frequency": "monthly", "date_column": "month",
            "key": ["month"], "grain": ["month"],
            "columns": {
                "month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                "housing_credit_million_TRY": {
                    "dtype": "integer", "unit": "TRY", "scale": 1_000_000, "currency": "TRY",
                    "kind": "stock", "aggregation": "last", "nullable": False,
                },
            },
        }, expected_version=0,
        unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )


def test_only_new_finance_workspaces_receive_active_shared_release_after_restart(tmp_path):
    database = tmp_path / "finance.duckdb"
    _database(database)
    context = AppContext(tmp_path / "runtime", database, validate_finance=False, searxng_url=False)
    old_finance = context.create_workspace("Old finance", "finance")
    source_workspace = context.create_workspace("Research", "finance")
    published = _publish(context, source_workspace["workspace_id"])
    promoted = context.shared_lakehouse.promote(
        source_workspace["workspace_id"], published["dataset_id"],
        official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Verified official source",
    )

    new_finance = context.create_workspace("New finance", "finance")
    generic = context.create_workspace("Generic", "generic")
    assert context.workspace(old_finance["workspace_id"])["datasets"] == []
    assert new_finance["datasets"] == [published["dataset_id"]]
    assert new_finance["shared_release_id"] == promoted["shared_release_id"]
    assert generic["datasets"] == []
    assert "shared_release_id" not in generic

    context.delete_workspace(source_workspace["workspace_id"])
    context.close()

    restarted = AppContext(tmp_path / "runtime", database, validate_finance=False, searxng_url=False)
    try:
        after_restart = restarted.create_workspace("After restart", "finance")
        assert after_restart["datasets"] == [published["dataset_id"]]
        assert after_restart["shared_release_id"] == promoted["shared_release_id"]
        evidence = restarted.shared_lakehouse.promotion_file(promoted["promotion_id"], "source/raw.bin")
        assert evidence.read_bytes().startswith(b"month,housing_credit")
    finally:
        restarted.close()
