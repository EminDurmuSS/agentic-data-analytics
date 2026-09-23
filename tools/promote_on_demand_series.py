#!/usr/bin/env python3
"""Promote a validated workspace-scoped on-demand EVDS acquisition to a
permanent, reviewable manifest (commit 7, Faz 2 architecture step).

``tools/EVDS_Talep_Uzerine_Indirme_Araci.py`` acquires series into an
isolated, locked ``DEFAULT_OUTPUT_ROOT/<workspace_id>/<dataset_hash>/``
directory (see that module's docstring). That location is deliberately
*not* wired into ``data_pipeline/catalog/build_unified_catalog.py`` or
``data_pipeline/lakehouse/build_lakehouse.py`` — an on-demand acquisition is
scratch space for one workspace, not vetted for the shared lakehouse build
that every workspace/container reads.

This tool is the explicit, auditable step that turns a validated on-demand
acquisition into a candidate permanent manifest, following the exact shape
established by commit 1/3/4 (``data_pipeline/evds/manifests/<dataset_id>.json``):
``{dataset_id, description, start_date, end_date, series:[{series_code, role, reason}]}``.

It does NOT perform steps 5-9 of the established data-ingestion pattern
documented in ``docs/eval-set/COMMIT_PLAN_STATUS.md`` (catalog wiring,
``build_lakehouse.py`` wiring, ``registry.py`` overrides, rebuild). Those
remain a deliberate human/reviewer action, exactly as commit 5 documented
for the minimum-wage reference table: promotion produces a *reviewable*
artifact, it does not silently graduate on-demand data into the shared
build.

Usage::

    python3 tools/promote_on_demand_series.py \\
        --workspace ws_demo --dataset-hash 3f9c2a1b4d5e6f70 \\
        --promoted-dataset-id vehicle_loan_supplement_v1 \\
        --description "..." \\
        --roles '{"TP.BKR.TRY.17": {"role": "vehicle_loan_rate", "reason": "..."}}'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.EVDS_Talep_Uzerine_Indirme_Araci import (
    DEFAULT_OUTPUT_ROOT,
    validate_workspace_id,
)

MANIFEST_DIR = PROJECT_ROOT / "data_pipeline" / "evds" / "manifests"


class PromotionError(ValueError):
    """The on-demand acquisition is not eligible for promotion as-is."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_acquisition(workspace_id: str, dataset_hash: str,
                      output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    """Load and structurally validate a completed on-demand acquisition.

    Raises ``PromotionError`` if the acquisition directory is missing the
    artifacts a real, executed download always produces (a half-finished or
    dry-run acquisition must never be promotable).
    """
    validate_workspace_id(workspace_id)
    acquisition_dir = (output_root / workspace_id / dataset_hash).resolve()
    if not acquisition_dir.is_relative_to(output_root.resolve()):
        raise PromotionError("Acquisition path escapes the on-demand store root.")
    if not acquisition_dir.is_dir():
        raise PromotionError(f"No on-demand acquisition at {acquisition_dir}.")

    generated_manifest_path = acquisition_dir / "generated_manifest.json"
    observations_path = acquisition_dir / "observations_long.parquet"
    validation_path = acquisition_dir / "validation.json"
    missing = [p.name for p in (generated_manifest_path, observations_path, validation_path) if not p.is_file()]
    if missing:
        raise PromotionError(
            "Acquisition is incomplete, missing: " + ", ".join(missing) +
            " -- run the on-demand downloader (not --dry-run) before promoting."
        )

    generated_manifest = json.loads(generated_manifest_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    observations_sha256 = file_sha256(observations_path)

    return {
        "acquisition_dir": acquisition_dir,
        "generated_manifest": generated_manifest,
        "validation": validation,
        "observations_sha256": observations_sha256,
    }


def build_promoted_manifest(acquisition: dict[str, Any], *, workspace_id: str, dataset_hash: str,
                             promoted_dataset_id: str, description: str,
                             roles: dict[str, dict[str, str]]) -> dict[str, Any]:
    generated_manifest = acquisition["generated_manifest"]
    codes = [entry["series_code"] for entry in generated_manifest["series"]]
    missing_roles = sorted(set(codes) - set(roles))
    if missing_roles:
        raise PromotionError(
            "Every acquired series requires an explicit reviewed role/reason before promotion "
            f"(missing: {missing_roles}); the on-demand placeholder role 'on_demand' is not "
            "acceptable in a permanent manifest."
        )
    series = []
    for code in codes:
        spec = roles[code]
        if not isinstance(spec, dict) or not spec.get("role") or not spec.get("reason"):
            raise PromotionError(f"Role/reason for {code} must be non-empty strings.")
        series.append({"series_code": code, "role": spec["role"], "reason": spec["reason"]})

    if not promoted_dataset_id or promoted_dataset_id == generated_manifest["dataset_id"]:
        raise PromotionError(
            "Promotion requires a distinct permanent dataset_id "
            "(the on-demand hash-based id is not a valid permanent identifier)."
        )
    if not description or not description.strip():
        raise PromotionError("Promotion requires a non-empty human description.")

    return {
        "dataset_id": promoted_dataset_id,
        "description": description,
        "start_date": generated_manifest["start_date"],
        "end_date": generated_manifest["end_date"],
        "series": series,
        "provenance": {
            "promoted_from": "on_demand_acquisition_store",
            "promoted_from_workspace": workspace_id,
            "promoted_from_dataset_hash": dataset_hash,
            "on_demand_dataset_id": generated_manifest["dataset_id"],
            "on_demand_observations_sha256": acquisition["observations_sha256"],
            "on_demand_validation_summary": acquisition["validation"],
            "review_required": True,
            "review_note": (
                "Structural promotion only. This manifest is not yet wired into "
                "data_pipeline/catalog/build_unified_catalog.py or "
                "data_pipeline/lakehouse/build_lakehouse.py, and no registry.py override "
                "has been applied. A reviewer must complete the remaining steps of the "
                "data-ingestion pattern in docs/eval-set/COMMIT_PLAN_STATUS.md before "
                "this series becomes queryable through discover/describe/execute."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dataset-hash", required=True, help="The on-demand dataset hash fragment (last '.'-segment of its dataset_id, or --dataset-id).")
    parser.add_argument("--promoted-dataset-id", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--roles", required=True, help="JSON object: {series_code: {role, reason}} for every acquired series.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing promoted manifest file.")
    args = parser.parse_args(argv)

    try:
        roles = json.loads(args.roles)
        if not isinstance(roles, dict):
            raise PromotionError("--roles must be a JSON object.")
        acquisition = load_acquisition(args.workspace, args.dataset_hash, args.output_root)
        promoted = build_promoted_manifest(
            acquisition,
            workspace_id=args.workspace,
            dataset_hash=args.dataset_hash,
            promoted_dataset_id=args.promoted_dataset_id,
            description=args.description,
            roles=roles,
        )
    except (PromotionError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2

    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    destination = args.manifest_dir / f"{args.promoted_dataset_id}.json"
    if destination.exists() and not args.force:
        print(json.dumps({
            "status": "error",
            "error": f"{destination} already exists; pass --force to overwrite after review.",
        }, ensure_ascii=False))
        return 2
    destination.write_text(json.dumps(promoted, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "promoted", "manifest_path": str(destination), "manifest": promoted},
                      ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
