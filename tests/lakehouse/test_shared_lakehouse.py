"""Durable promotion of verified web datasets into shared lakehouse releases."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import duckdb
import pytest

from agentic_analytics.agent.tools.documents import DocumentTools, OFFICIAL_SOURCE_REGISTRY
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.shared import SharedLakehouse, SharedLakehouseError
from agentic_analytics.lakehouse.store import LakehouseStore


def _snapshot(store: LakehouseStore, root: Path) -> str:
    database = root / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
        connection.execute("INSERT INTO seed VALUES (1)")
    return store.publish_snapshot(database)["snapshot_id"]


def _contract(*, kind="stock"):
    metric = {
        "dtype": "integer", "unit": "TRY", "scale": 1_000_000,
        "currency": "TRY", "kind": kind, "nullable": False,
    }
    if kind != "unknown":
        metric["aggregation"] = "last"
    return {
        "name": "Official Housing Credit",
        "frequency": "monthly",
        "date_column": "month",
        "key": ["month"],
        "grain": ["month"],
        "expected_rows": 2,
        "expected_periods": ["2025-01", "2025-02"],
        "columns": {
            "month": {
                "dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False,
            },
            "housing_credit_million_TRY": metric,
        },
    }


def _publish_web_dataset(store, snapshot_id, workspace_id, *, url="https://www.bddk.org.tr/data/housing.csv",
                         kind="stock"):
    store.create_workspace(snapshot_id, workspace_id)
    documents = DocumentTools(store, workspace_id, searxng_url=False)
    raw = b"month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n"
    source = documents._register(raw, "housing.csv", "text/csv", url)
    inspected = documents.inspect_source(source_id=source["source_id"])
    assert inspected["tables"][0]["table_id"] == "table_001"
    published = documents.publish_selected_table(
        source["source_id"],
        "table_001",
        _contract(kind=kind),
        expected_version=0,
        unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )
    return documents, source, published, raw


def test_promoted_web_dataset_survives_workspace_deletion_and_restart(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    documents, source, published, raw = _publish_web_dataset(store, snapshot_id, "workspace_source")
    dataset_id = published["dataset_id"]
    source_workspace = store.workspace("workspace_source")

    shared = SharedLakehouse(store)
    promotion = shared.promote(
        "workspace_source", dataset_id, official_sources=OFFICIAL_SOURCE_REGISTRY,
        reason="Verified official monthly housing-credit table",
    )

    assert store.workspace("workspace_source") == source_workspace
    assert promotion["publication_performed"] is True
    assert promotion["dataset_id"] == dataset_id
    assert promotion["metric_ids"] == [f"overlay:{dataset_id}:housing_credit_million_TRY"]
    assert shared.promotion_file(promotion["promotion_id"], "source/raw.bin").read_bytes() == raw

    shutil.rmtree(store.root / "document_sources" / "workspace_source")
    shutil.rmtree(store.root / "workspaces" / "workspace_source")

    restarted_store = LakehouseStore(store.root)
    restarted_shared = SharedLakehouse(restarted_store)
    release = restarted_shared.current_release()
    assert release["release_id"] == promotion["shared_release_id"]
    assert release["dataset_ids"] == [dataset_id]

    workspace = restarted_store.create_workspace(
        snapshot_id,
        "workspace_consumer",
        initial_dataset_ids=release["dataset_ids"],
        shared_release_id=release["release_id"],
    )
    assert workspace["datasets"] == [dataset_id]
    assert workspace["shared_release_id"] == release["release_id"]

    service = LakehouseService(restarted_store, "workspace_consumer")
    discovered = service.discover({"query": "official housing credit", "limit": 5})
    metric_id = f"overlay:{dataset_id}:housing_credit_million_TRY"
    assert metric_id in [metric["metric_id"] for metric in discovered["metrics"]]
    described = service.describe({"metric_id": metric_id})["metric"]
    assert described["native_frequency"] == "monthly"
    assert described["kind"] == "stock"
    assert described["scale"] == 1_000_000

    result = service.execute({
        "start": "2025-01", "end": "2025-02", "frequency": "monthly",
        "columns": [{"name": "credit", "metric_id": metric_id, "dimensions": {}}],
    })
    assert [row["credit"] for row in result["preview"]] == [100, 110]
    proof = service.explain_value({
        "analysis_id": result["analysis_id"], "column": "credit", "period": "2025-02",
    })
    assert proof["value"] == 110
    assert proof["lineage"]["document_provenance"]["source_url"] == source["source_url"]


def test_duplicate_promotion_is_idempotent(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source")
    shared = SharedLakehouse(store)

    first = shared.promote("workspace_source", published["dataset_id"],
                           official_sources=OFFICIAL_SOURCE_REGISTRY, reason="First request")
    second = shared.promote("workspace_source", published["dataset_id"],
                            official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Retry with different wording")

    assert second["promotion_id"] == first["promotion_id"]
    assert second["shared_release_id"] == first["shared_release_id"]
    assert second["publication_performed"] is False
    assert len(list((shared.root / "promotions").glob("promotion_*"))) == 1
    assert len(list((shared.root / "releases").glob("release_*"))) == 1


@pytest.mark.parametrize(
    ("url", "expected_code"),
    [
        (None, "SHARED_SOURCE_NOT_OFFICIAL"),
        ("https://example.invalid/housing.csv", "SHARED_SOURCE_NOT_OFFICIAL"),
        ("https://www.bddk.org.tr:8443/data/housing.csv", "SHARED_SOURCE_NOT_OFFICIAL"),
    ],
)
def test_upload_or_unrecognised_domain_cannot_be_promoted(tmp_path, url, expected_code):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source", url=url)

    with pytest.raises(SharedLakehouseError) as error:
        SharedLakehouse(store).promote(
            "workspace_source", published["dataset_id"],
            official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Should be rejected",
        )
    assert error.value.code == expected_code


def test_missing_provenance_and_unknown_numeric_semantics_are_rejected(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    workspace = store.create_workspace(snapshot_id, "workspace_plain")
    csv_path = tmp_path / "plain.csv"
    csv_path.write_text("month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n")
    plain = store.ingest_csv("workspace_plain", csv_path, _contract(), expected_version=workspace["version"])
    shared = SharedLakehouse(store)
    with pytest.raises(SharedLakehouseError) as missing:
        shared.promote("workspace_plain", plain["datasets"][0],
                       official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Missing source proof")
    assert missing.value.code == "SHARED_SOURCE_PROVENANCE_REQUIRED"

    _, _, unknown, _ = _publish_web_dataset(store, snapshot_id, "workspace_unknown", kind="unknown")
    with pytest.raises(SharedLakehouseError) as ambiguous:
        shared.promote("workspace_unknown", unknown["dataset_id"],
                       official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Ambiguous semantics")
    assert ambiguous.value.code == "SHARED_DATASET_SEMANTICS_REVIEW_REQUIRED"


def test_review_evidence_must_bind_the_exact_source_table(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    store.create_workspace(snapshot_id, "workspace_source")
    documents = DocumentTools(store, "workspace_source", searxng_url=False)
    raw = b"""
        <table><tr><th>month</th><th>housing_credit_million_TRY</th></tr>
        <tr><td>2025-01</td><td>90</td></tr></table>
        <table><tr><th>month</th><th>housing_credit_million_TRY</th></tr>
        <tr><td>2025-01</td><td>100</td></tr><tr><td>2025-02</td><td>110</td></tr></table>
    """
    source = documents._register(
        raw, "housing.html", "text/html", "https://www.bddk.org.tr/data/housing.html",
    )
    inspected = documents.inspect_source(source_id=source["source_id"])
    assert [table["table_id"] for table in inspected["tables"]] == ["table_001", "table_002"]
    documents.review_table(
        source["source_id"], "table_001", [["2025-01", "90"]],
        {"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )
    review = documents.review_candidate(source["source_id"], "table_001")["review"]
    csv_path = tmp_path / "forged-review.csv"
    csv_path.write_text("month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n")
    contract = _contract()
    contract["document_provenance"] = {
        "source_id": source["source_id"], "table_id": "table_002",
        "source_url": source["source_url"], "raw_sha256": source["raw_sha256"],
        "human_review": review,
    }
    workspace = store.ingest_csv("workspace_source", csv_path, contract, expected_version=0)

    with pytest.raises(SharedLakehouseError) as error:
        SharedLakehouse(store).promote(
            "workspace_source", workspace["datasets"][0],
            official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Mismatched review must fail",
        )

    assert error.value.code == "SHARED_SOURCE_REVIEW_REQUIRED"


def test_prepared_table_can_retain_a_review_bound_to_its_source_table(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    store.create_workspace(snapshot_id, "workspace_source")
    documents = DocumentTools(store, "workspace_source", searxng_url=False)
    source = documents._register(
        b"month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n",
        "housing.csv", "text/csv", "https://www.bddk.org.tr/data/housing.csv",
    )
    documents.inspect_source(source_id=source["source_id"])
    documents.review_table(
        source["source_id"], "table_001", [["2025-01", "100"], ["2025-02", "110"]],
        {"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )
    prepared = documents.prepare_source_table(
        source["source_id"], "table_001", selected_rows=[1, 2],
        selected_columns=["month", "housing_credit_million_TRY"],
    )
    published = documents.publish_selected_table(
        source["source_id"], prepared["table_id"], _contract(), expected_version=0,
        unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"},
    )

    promoted = SharedLakehouse(store).promote(
        "workspace_source", published["dataset_id"], official_sources=OFFICIAL_SOURCE_REGISTRY,
        reason="Reviewed source table was prepared without changing numeric values",
    )

    assert promoted["status"] == "ok"
    assert promoted["publication_performed"] is True


def test_failed_promotion_keeps_previous_release_and_pointer_unchanged(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, first_dataset, _ = _publish_web_dataset(store, snapshot_id, "workspace_first")
    second_documents, second_source, second_dataset, _ = _publish_web_dataset(
        store, snapshot_id, "workspace_second", url="https://www.bddk.org.tr/data/other.csv")
    shared = SharedLakehouse(store)
    first = shared.promote("workspace_first", first_dataset["dataset_id"],
                           official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Known good")
    pointer = (shared.root / "CURRENT.json").read_bytes()

    (second_documents._directory(second_source["source_id"]) / "raw.bin").write_bytes(b"tampered")
    with pytest.raises(SharedLakehouseError) as error:
        shared.promote("workspace_second", second_dataset["dataset_id"],
                       official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Must fail closed")
    assert error.value.code == "SHARED_SOURCE_INTEGRITY_FAILED"
    assert (shared.root / "CURRENT.json").read_bytes() == pointer
    assert shared.current_release()["release_id"] == first["shared_release_id"]
    assert list((shared.root / ".staging").iterdir()) == []


def test_concurrent_promotions_do_not_lose_a_dataset(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, first, _ = _publish_web_dataset(
        store, snapshot_id, "workspace_first", url="https://www.bddk.org.tr/data/first.csv")
    _, _, second, _ = _publish_web_dataset(
        store, snapshot_id, "workspace_second", url="https://www.tcmb.gov.tr/data/second.csv")

    def promote(workspace_id, dataset_id):
        return SharedLakehouse(LakehouseStore(store.root)).promote(
            workspace_id, dataset_id, official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Concurrent publish",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(promote, "workspace_first", first["dataset_id"]),
            pool.submit(promote, "workspace_second", second["dataset_id"]),
        ]
        results = [future.result() for future in futures]

    release = SharedLakehouse(LakehouseStore(store.root)).current_release()
    assert set(release["dataset_ids"]) == {first["dataset_id"], second["dataset_id"]}
    assert release["release_id"] in {result["shared_release_id"] for result in results}
    assert len(release["promotion_ids"]) == 2


def test_existing_workspace_is_pinned_while_new_workspace_can_seed_active_release(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    old = store.create_workspace(snapshot_id, "workspace_old")
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source")
    shared = SharedLakehouse(store)
    promoted = shared.promote("workspace_source", published["dataset_id"],
                              official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Shared release")

    assert store.workspace("workspace_old") == old
    new = store.create_workspace(
        snapshot_id, "workspace_new",
        initial_dataset_ids=shared.current_dataset_ids(),
        shared_release_id=promoted["shared_release_id"],
    )
    assert old["datasets"] == []
    assert new["datasets"] == [published["dataset_id"]]


def test_seeded_workspace_can_idempotently_promote_after_original_source_is_deleted(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source")
    shared = SharedLakehouse(store)
    first = shared.promote("workspace_source", published["dataset_id"],
                           official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Initial promotion")
    shutil.rmtree(store.root / "document_sources" / "workspace_source")
    shutil.rmtree(store.root / "workspaces" / "workspace_source")
    store.create_workspace(
        snapshot_id, "workspace_consumer", initial_dataset_ids=[published["dataset_id"]],
        shared_release_id=first["shared_release_id"],
    )

    repeated = shared.promote("workspace_consumer", published["dataset_id"],
                              official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Explicit retry")

    assert repeated["promotion_id"] == first["promotion_id"]
    assert repeated["shared_release_id"] == first["shared_release_id"]
    assert repeated["publication_performed"] is False


def test_tampered_shared_package_is_rejected_on_every_read(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source")
    shared = SharedLakehouse(store)
    promoted = shared.promote("workspace_source", published["dataset_id"],
                              official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Integrity test")
    packaged_raw = shared.root / "promotions" / promoted["promotion_id"] / "source" / "raw.bin"
    packaged_raw.chmod(0o644)
    packaged_raw.write_bytes(b"tampered package")

    with pytest.raises(SharedLakehouseError) as error:
        shared.current_release()
    assert error.value.code == "SHARED_RELEASE_INTEGRITY_FAILED"


def test_orphaned_promotion_package_can_recover_release_pointer(tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot_id = _snapshot(store, tmp_path)
    _, _, published, _ = _publish_web_dataset(store, snapshot_id, "workspace_source")
    shared = SharedLakehouse(store)
    with patch.object(shared, "_build_release", side_effect=RuntimeError("simulated crash after package commit")):
        with pytest.raises(RuntimeError, match="simulated crash"):
            shared.promote("workspace_source", published["dataset_id"],
                           official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Recovery test")
    assert not (shared.root / "CURRENT.json").exists()
    packages = list((shared.root / "promotions").glob("promotion_*"))
    assert len(packages) == 1

    recovered = SharedLakehouse(LakehouseStore(store.root)).recover_promotion(published["dataset_id"])

    assert recovered["recovered"] is True
    assert recovered["promotion_id"] == packages[0].name
    assert shared.current_release()["release_id"] == recovered["shared_release_id"]
