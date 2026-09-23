"""Commit 7: workspace-scoped, locked on-demand EVDS acquisition + promotion."""
import json
import threading
import time

import pandas as pd
import pytest

from tools.EVDS_Talep_Uzerine_Indirme_Araci import (
    validate_workspace_id,
    workspace_output_root,
    workspace_acquisition_lock,
)
from tools.promote_on_demand_series import (
    PromotionError,
    build_promoted_manifest,
    load_acquisition,
    main as promote_main,
)


# --- workspace id validation -------------------------------------------------

@pytest.mark.parametrize("bad_id", [
    "", None, 123, "../escape", "a/b", "a\\b", "a b", "a" * 200, ".", "..",
    "-leading-hyphen", "a" * 129,
])
def test_invalid_workspace_ids_are_rejected(bad_id):
    with pytest.raises(ValueError):
        validate_workspace_id(bad_id)


@pytest.mark.parametrize("good_id", ["ws1", "workspace_demo", "a", "abc-123_XYZ"])
def test_valid_workspace_ids_are_accepted(good_id):
    assert validate_workspace_id(good_id) == good_id


# --- workspace scoping of storage -------------------------------------------

def test_two_workspaces_get_distinct_output_roots(tmp_path):
    root_a = workspace_output_root("ws_a", tmp_path)
    root_b = workspace_output_root("ws_b", tmp_path)
    assert root_a != root_b
    assert root_a.is_relative_to(tmp_path)
    assert root_b.is_relative_to(tmp_path)


def test_output_root_cannot_escape_the_store_root(tmp_path):
    with pytest.raises(ValueError):
        workspace_output_root("../escape", tmp_path)


# --- locking -----------------------------------------------------------------

def test_lock_serializes_same_workspace_and_hash(tmp_path):
    order = []

    def hold_lock():
        with workspace_acquisition_lock("ws1", "deadbeef", tmp_path):
            order.append("first-acquired")
            time.sleep(0.2)
            order.append("first-released")

    thread = threading.Thread(target=hold_lock)
    thread.start()
    time.sleep(0.05)  # let the first thread acquire before we try
    with workspace_acquisition_lock("ws1", "deadbeef", tmp_path):
        order.append("second-acquired")
    thread.join()

    assert order == ["first-acquired", "first-released", "second-acquired"], order


def test_lock_does_not_serialize_unrelated_workspaces_or_hashes(tmp_path):
    # Different workspace_id -> different lock file -> no contention.
    with workspace_acquisition_lock("ws1", "deadbeef", tmp_path) as lock_a:
        with workspace_acquisition_lock("ws2", "deadbeef", tmp_path) as lock_b:
            assert lock_a != lock_b
    # Different dataset hash within same workspace -> also a different file.
    with workspace_acquisition_lock("ws1", "deadbeef", tmp_path) as lock_a:
        with workspace_acquisition_lock("ws1", "cafef00d", tmp_path) as lock_c:
            assert lock_a != lock_c


def test_lock_rejects_malformed_hash(tmp_path):
    with pytest.raises(ValueError):
        with workspace_acquisition_lock("ws1", "not-hex!", tmp_path):
            pass


# --- promotion -----------------------------------------------------------------

def _write_fake_acquisition(root, workspace_id, dataset_hash, *, series_codes=("TP.BKR.TRY.17",)):
    acquisition_dir = root / workspace_id / dataset_hash
    acquisition_dir.mkdir(parents=True)
    generated_manifest = {
        "dataset_id": f"evds.on_demand.{dataset_hash}",
        "start_date": "2021-01-01",
        "end_date": "2026-06-30",
        "selection_policy": "catalog_validated_on_demand",
        "series": [{"series_code": code, "role": "on_demand", "reason": "x", "aggregation": "avg"}
                   for code in series_codes],
    }
    (acquisition_dir / "generated_manifest.json").write_text(
        json.dumps(generated_manifest, ensure_ascii=False), encoding="utf-8")
    frame = pd.DataFrame({"series_code": list(series_codes), "period": ["2021-01"] * len(series_codes),
                           "value": [1.23] * len(series_codes)})
    frame.to_parquet(acquisition_dir / "observations_long.parquet", index=False)
    (acquisition_dir / "validation.json").write_text(
        json.dumps({"status": "ok", "checks": []}, ensure_ascii=False), encoding="utf-8")
    return acquisition_dir


def test_load_acquisition_rejects_incomplete_directory(tmp_path):
    (tmp_path / "ws1" / "deadbeef").mkdir(parents=True)
    with pytest.raises(PromotionError):
        load_acquisition("ws1", "deadbeef", tmp_path)


def test_load_acquisition_reads_a_complete_directory(tmp_path):
    _write_fake_acquisition(tmp_path, "ws1", "deadbeef")
    acquisition = load_acquisition("ws1", "deadbeef", tmp_path)
    assert acquisition["generated_manifest"]["series"][0]["series_code"] == "TP.BKR.TRY.17"
    assert len(acquisition["observations_sha256"]) == 64


def test_build_promoted_manifest_requires_explicit_roles_for_every_series(tmp_path):
    _write_fake_acquisition(tmp_path, "ws1", "deadbeef", series_codes=("TP.BKR.TRY.17", "TP.BKR.TRY.1"))
    acquisition = load_acquisition("ws1", "deadbeef", tmp_path)
    with pytest.raises(PromotionError):
        build_promoted_manifest(
            acquisition, workspace_id="ws1", dataset_hash="deadbeef",
            promoted_dataset_id="promoted_v1", description="desc",
            roles={"TP.BKR.TRY.17": {"role": "vehicle_loan_rate", "reason": "why"}},
        )  # missing TP.BKR.TRY.1


def test_build_promoted_manifest_rejects_reusing_the_on_demand_id(tmp_path):
    _write_fake_acquisition(tmp_path, "ws1", "deadbeef")
    acquisition = load_acquisition("ws1", "deadbeef", tmp_path)
    with pytest.raises(PromotionError):
        build_promoted_manifest(
            acquisition, workspace_id="ws1", dataset_hash="deadbeef",
            promoted_dataset_id=acquisition["generated_manifest"]["dataset_id"],
            description="desc", roles={"TP.BKR.TRY.17": {"role": "r", "reason": "x"}},
        )


def test_build_promoted_manifest_produces_commit1_shaped_manifest(tmp_path):
    _write_fake_acquisition(tmp_path, "ws1", "deadbeef")
    acquisition = load_acquisition("ws1", "deadbeef", tmp_path)
    promoted = build_promoted_manifest(
        acquisition, workspace_id="ws1", dataset_hash="deadbeef",
        promoted_dataset_id="vehicle_supplement_v1", description="Human-reviewed description",
        roles={"TP.BKR.TRY.17": {"role": "vehicle_loan_rate", "reason": "why"}},
    )
    assert set(promoted) >= {"dataset_id", "description", "start_date", "end_date", "series", "provenance"}
    assert promoted["series"] == [{"series_code": "TP.BKR.TRY.17", "role": "vehicle_loan_rate", "reason": "why"}]
    assert promoted["provenance"]["promoted_from_workspace"] == "ws1"
    assert promoted["provenance"]["review_required"] is True


def test_promote_main_writes_manifest_and_refuses_overwrite_without_force(tmp_path):
    output_root = tmp_path / "on_demand"
    _write_fake_acquisition(output_root, "ws1", "deadbeef")
    manifest_dir = tmp_path / "manifests"
    roles = json.dumps({"TP.BKR.TRY.17": {"role": "vehicle_loan_rate", "reason": "why"}})

    exit_code = promote_main([
        "--workspace", "ws1", "--dataset-hash", "deadbeef",
        "--promoted-dataset-id", "vehicle_supplement_v1", "--description", "desc",
        "--roles", roles, "--output-root", str(output_root), "--manifest-dir", str(manifest_dir),
    ])
    assert exit_code == 0
    written = json.loads((manifest_dir / "vehicle_supplement_v1.json").read_text(encoding="utf-8"))
    assert written["dataset_id"] == "vehicle_supplement_v1"

    # Re-running without --force must refuse to silently clobber a reviewed file.
    exit_code = promote_main([
        "--workspace", "ws1", "--dataset-hash", "deadbeef",
        "--promoted-dataset-id", "vehicle_supplement_v1", "--description", "desc",
        "--roles", roles, "--output-root", str(output_root), "--manifest-dir", str(manifest_dir),
    ])
    assert exit_code == 2
