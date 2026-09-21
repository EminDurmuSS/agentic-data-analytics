"""Pinned consumers can inspect durable evidence without editing shared packages."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import shutil

import duckdb
import pytest

from agentic_analytics.agent.tools.documents import DocumentError, DocumentTools, OFFICIAL_SOURCE_REGISTRY, _write_json
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore, StoreError, _canonical


def environment(tmp_path, *, review=False, extraction=False):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    store.create_workspace(snapshot, "workspace_owner")
    documents = DocumentTools(store, "workspace_owner", searxng_url=False)
    raw = b"month,housing_credit_million_TRY\n2025-01,100\n2025-02,110\n"
    source = documents._register(raw, "housing.csv", "text/csv", "https://www.bddk.org.tr/data/housing.csv")
    documents.inspect_source(source_id=source["source_id"])
    if review:
        documents.review_table(source["source_id"], "table_001", [["2025-01", "100"], ["2025-02", "110"]],
                               {"housing_credit_million_TRY": "housing_credit_million_TRY"})
    extraction_ref = None
    if extraction:
        record = {"image_sha256": hashlib.sha256(raw).hexdigest(), "page": 1,
                  "raw_machine_output": {"text": "machine evidence"}, "verified": False}
        extraction_ref = "extraction_" + hashlib.sha256(_canonical(record)).hexdigest()
        directory = documents.root / "extractions"
        directory.mkdir()
        _write_json(directory / (extraction_ref + ".json"), {**record, "extraction_id": extraction_ref})
        cache = documents._directory(source["source_id"]) / "inspection.json"
        inspection = json.loads(cache.read_text())
        inspection["tables"][0]["extraction_artifact_ref"] = extraction_ref
        _write_json(cache, inspection)
    published = documents.publish_selected_table(source["source_id"], "table_001", {
        "name": "Official Housing Credit", "frequency": "monthly", "date_column": "month",
        "key": ["month"], "grain": ["month"], "columns": {
            "month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
            "housing_credit_million_TRY": {"dtype": "integer", "unit": "TRY", "scale": 1000000,
                "currency": "TRY", "kind": "stock", "aggregation": "last", "nullable": False},
        }}, expected_version=0, unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"})
    shared = SharedLakehouse(store)
    promotion = shared.promote("workspace_owner", published["dataset_id"], official_sources=OFFICIAL_SOURCE_REGISTRY)
    store.create_workspace(snapshot, "workspace_consumer", initial_dataset_ids=[published["dataset_id"]],
                           shared_release_id=promotion["shared_release_id"])
    return store, snapshot, documents, source, published, shared, promotion, raw, extraction_ref


def package_bytes(shared, promotion):
    root = shared._path("promotions", promotion["promotion_id"])
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_consumer_inspects_raw_table_after_original_workspace_deleted_and_restart(tmp_path):
    store, _, owner, source, _, shared, promotion, raw, _ = environment(tmp_path)
    saved = package_bytes(shared, promotion)
    original_manifest = owner.source(source["source_id"])
    shutil.rmtree(owner.root)
    shutil.rmtree(store.root / "workspaces" / "workspace_owner")
    reader = DocumentTools(LakehouseStore(store.root), "workspace_consumer", searxng_url=False)
    source_id = source["source_id"]
    copied = reader.source(source_id)
    assert copied["workspace_id"] == "workspace_consumer"
    assert copied["source_id"] == source_id
    assert copied["raw_sha256"] == source["raw_sha256"]
    assert copied["shared_origin"]["workspace_id"] == "workspace_owner"
    assert copied["shared_origin"]["shared_release_id"] == promotion["shared_release_id"]
    origin_path = reader._directory(source_id) / "shared-origin-manifest.json"
    assert json.loads(origin_path.read_text()) == original_manifest
    assert reader.raw_source_bytes(source_id) == raw
    assert reader.inspect_source(source_id=source_id)["tables"][0]["preview"][0]["housing_credit_million_TRY"] == "100"
    table = reader.read_source_table(source_id, "table_001")
    assert table["row_count"] == 2
    assert reader.list_sources()[0]["source_id"] == source_id
    assert reader.source(source_id) == copied
    assert package_bytes(shared, promotion) == saved


def test_consumer_local_review_does_not_change_shared_evidence(tmp_path):
    store, _, _, source, _, shared, promotion, _, _ = environment(tmp_path)
    before = package_bytes(shared, promotion)
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    reader.review_table(source["source_id"], "table_001", [["2025-01", "99"], ["2025-02", "110"]],
                        {"housing_credit_million_TRY": "explicit local review"})
    assert reader.inspect_source(source_id=source["source_id"])["tables"][0]["preview"][0]["housing_credit_million_TRY"] == "99"
    assert package_bytes(shared, promotion) == before
    assert shared.current_release()["release_id"] == promotion["shared_release_id"]


def test_required_review_and_extraction_evidence_are_copied_and_remain_valid(tmp_path):
    store, _, owner, source, _, shared, promotion, _, extraction_ref = environment(tmp_path, review=True, extraction=True)
    before = package_bytes(shared, promotion)
    original_review = (owner._directory(source["source_id"]) / "table_001_review.json").read_bytes()
    original_extraction = (owner.root / "extractions" / (extraction_ref + ".json")).read_bytes()
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    inspected = reader.inspect_source(source_id=source["source_id"])
    assert inspected["tables"][0]["review"]["verification"] == "explicit_user_cell_and_unit_review"
    assert (reader._directory(source["source_id"]) / "table_001_review.json").read_bytes() == original_review
    assert (reader.root / "extractions" / (extraction_ref + ".json")).read_bytes() == original_extraction
    assert package_bytes(shared, promotion) == before


@pytest.mark.parametrize("pin,dataset", [(False, False), (False, True), (True, False)])
def test_release_pin_and_dataset_membership_are_both_required(tmp_path, pin, dataset):
    store, snapshot, _, source, published, _, promotion, _, _ = environment(tmp_path)
    store.create_workspace(snapshot, "workspace_denied", initial_dataset_ids=[published["dataset_id"]] if dataset else [],
                           shared_release_id=promotion["shared_release_id"] if pin else None)
    reader = DocumentTools(store, "workspace_denied", searxng_url=False)
    with pytest.raises(FileNotFoundError):
        reader.source(source["source_id"])
    assert not reader._directory(source["source_id"]).exists()


def test_unpublished_foreign_source_is_not_found_by_scanning_workspaces(tmp_path):
    store, _, owner, _, _, _, _, _, _ = environment(tmp_path)
    private = owner._register(b"secret", "private.txt", "text/plain")
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    with pytest.raises(FileNotFoundError):
        reader.source(private["source_id"])
    assert not reader._directory(private["source_id"]).exists()


def test_package_tamper_and_consumer_byte_limit_fail_before_materialization(tmp_path):
    store, _, _, source, _, shared, promotion, raw, _ = environment(tmp_path)
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False, max_source_bytes=len(raw) - 1)
    with pytest.raises(DocumentError, match="byte limit"):
        reader.source(source["source_id"])
    assert not reader._directory(source["source_id"]).exists()
    packaged_raw = shared.promotion_file(promotion["promotion_id"], "source/raw.bin")
    packaged_raw.chmod(0o600)
    packaged_raw.write_bytes(raw.replace(b"100", b"999"))
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    with pytest.raises(DocumentError, match="hash mismatch"):
        reader.source(source["source_id"])
    assert not reader._directory(source["source_id"]).exists()


@pytest.mark.parametrize("target", ["source", "extractions"])
def test_consumer_symlinks_are_rejected_without_writing_outside_cache(tmp_path, target):
    store, _, _, source, _, _, _, _, _ = environment(tmp_path, extraction=True)
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    (reader.root / (source["source_id"] if target == "source" else "extractions")).symlink_to(outside, target_is_directory=True)
    with pytest.raises((DocumentError, StoreError)):
        reader.source(source["source_id"])
    assert list(outside.iterdir()) == []


def test_concurrent_consumers_install_identical_evidence_once(tmp_path):
    store, _, _, source, _, shared, promotion, raw, _ = environment(tmp_path)
    before = package_bytes(shared, promotion)
    def read(_):
        return DocumentTools(store, "workspace_consumer", searxng_url=False).raw_source_bytes(source["source_id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, range(8))) == [raw] * 8
    assert package_bytes(shared, promotion) == before


def test_existing_local_source_keeps_original_behavior(tmp_path):
    _, _, owner, source, _, shared, promotion, raw, _ = environment(tmp_path)
    before = package_bytes(shared, promotion)
    assert "shared_origin" not in owner.source(source["source_id"])
    assert owner.raw_source_bytes(source["source_id"]) == raw
    assert package_bytes(shared, promotion) == before


def test_pinned_inspection_does_not_follow_current_and_conflicting_versions_fail_closed(tmp_path):
    store, snapshot, owner, source, published, shared, first, _, _ = environment(tmp_path)
    cache = owner._directory(source["source_id"]) / "inspection.json"
    inspection = json.loads(cache.read_text())
    inspection["warnings"].append("Second explicitly prepared inspection version")
    _write_json(cache, inspection)
    contract = dict(store.dataset_manifest(published["dataset_id"])["contract"])
    contract["name"] = "Another table publication from the same document"
    changed = owner.publish_selected_table(source["source_id"], "table_001", contract, expected_version=1,
        unit_evidence={"housing_credit_million_TRY": "housing_credit_million_TRY"})
    second = shared.promote("workspace_owner", changed["dataset_id"], official_sources=OFFICIAL_SOURCE_REGISTRY)
    assert second["shared_release_id"] != first["shared_release_id"]
    # A first read after CURRENT changed must still use the consumer's old pin.
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    assert "Second explicitly prepared inspection version" not in reader.inspect_source(source_id=source["source_id"])["warnings"]
    assert reader.source(source["source_id"])["shared_origin"]["shared_release_id"] == first["shared_release_id"]
    store.create_workspace(snapshot, "workspace_newer", initial_dataset_ids=shared.current_dataset_ids(),
                           shared_release_id=second["shared_release_id"])
    with pytest.raises(DocumentError) as error:
        DocumentTools(store, "workspace_newer", searxng_url=False).source(source["source_id"])
    assert error.value.code == "SHARED_SOURCE_AMBIGUOUS"


def test_incomplete_or_damaged_local_cache_is_not_silently_overwritten(tmp_path):
    store, _, _, source, _, _, _, raw, _ = environment(tmp_path)
    reader = DocumentTools(store, "workspace_consumer", searxng_url=False)
    directory = reader._directory(source["source_id"])
    directory.mkdir()
    with pytest.raises(DocumentError, match="incomplete"):
        reader.source(source["source_id"])
    assert list(directory.iterdir()) == []
    directory.rmdir()
    reader.source(source["source_id"])
    (directory / "raw.bin").chmod(0o600)
    (directory / "raw.bin").write_bytes(raw.replace(b"100", b"999"))
    with pytest.raises(DocumentError) as error:
        reader.source(source["source_id"])
    assert error.value.code == "SOURCE_HASH_MISMATCH"
