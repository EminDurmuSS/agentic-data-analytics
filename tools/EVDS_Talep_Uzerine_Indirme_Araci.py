#!/usr/bin/env python3
"""Search the local EVDS catalog and fetch arbitrary public series on demand.

The full EVDS metadata catalog stays local, while observation values are
downloaded only for series selected by the user or an agent. The generated
manifest is then executed by the audited EVDS manifest downloader, so raw
requests, raw responses, missing values and SHA-256 lineage are preserved.

Acquisitions are workspace-scoped (commit 7): every on-demand download is
written under ``DEFAULT_OUTPUT_ROOT/<workspace_id>/<dataset_hash>/`` instead
of a single global directory shared by every caller. A ``--workspace``
argument is required for any download (``--search``-only calls do not touch
storage and do not need one). An advisory ``fcntl`` file lock, keyed by
``(workspace_id, dataset_hash)``, serializes concurrent acquisitions of the
same series set within the same workspace so two callers cannot interleave
writes into the same acquisition directory (mirrors the per-workspace lock
in ``agentic_analytics/lakehouse/store.py::LakehouseStore._lock``). This
on-demand store is explicitly NOT the permanent manifest pattern used by
``data_pipeline/evds/manifests/*.json`` (see commit 1/3/4 in
``docs/eval-set/COMMIT_PLAN_STATUS.md``); a validated on-demand acquisition
must be promoted with ``tools/promote_on_demand_series.py`` before it is
wired into the catalog/lakehouse build for other workspaces or a permanent
release.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.EVDS_Manifest_Indirme_Araci import run as run_manifest_download


DEFAULT_CATALOG = PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data_pipeline" / "evds" / "on_demand"
WORKSPACE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def validate_workspace_id(workspace_id: Any) -> str:
    """Reject anything that is not a bounded, filesystem-safe identifier.

    A caller-controlled workspace id ends up as a directory and lock-file
    name; it must never be usable to escape the on-demand store root or to
    collide with another workspace's storage.
    """
    if not isinstance(workspace_id, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        raise ValueError(
            "Workspace id must be a non-empty, bounded alphanumeric/underscore/hyphen identifier."
        )
    return workspace_id


def workspace_output_root(workspace_id: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    validate_workspace_id(workspace_id)
    root = (output_root / workspace_id).resolve()
    if not root.is_relative_to(output_root.resolve()):
        raise ValueError("Workspace output root escapes the on-demand store root.")
    return root


@contextlib.contextmanager
def workspace_acquisition_lock(workspace_id: str, dataset_hash: str, output_root: Path = DEFAULT_OUTPUT_ROOT):
    """Serialize acquisitions of the same series set within one workspace.

    Two independent processes racing to acquire the same
    ``(workspace_id, dataset_hash)`` pair will block on this lock rather than
    interleave writes into the same acquisition directory. Different
    workspaces (or different series/date selections within the same
    workspace) never contend on the same lock file, so this does not
    serialize unrelated acquisitions.
    """
    validate_workspace_id(workspace_id)
    if not isinstance(dataset_hash, str) or not re.fullmatch(r"[a-f0-9]{8,64}", dataset_hash):
        raise ValueError("Dataset hash must be a lowercase hex digest fragment.")
    lock_dir = output_root / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{workspace_id}__{dataset_hash}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield lock_path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def search_catalog(
    catalog: pd.DataFrame,
    query: str,
    limit: int = 20,
    include_archive: bool = False,
) -> pd.DataFrame:
    if limit <= 0:
        raise ValueError("Arama limiti pozitif olmali.")
    tokens = [token for token in normalize(query).split() if token]
    if not tokens:
        raise ValueError("EVDS katalog aramasi bos olamaz.")

    frame = catalog.copy()
    if not include_archive:
        frame = frame.loc[~frame["is_archive"]].copy()

    code = frame["series_code"].fillna("").astype(str).str.casefold()
    searchable = frame["searchable_text"].fillna("").astype(str).str.casefold()
    name_tr = frame["series_name_tr"].fillna("").astype(str).str.casefold()
    group = frame["group_name_tr"].fillna("").astype(str).str.casefold()

    score = pd.Series(0, index=frame.index, dtype="int64")
    exact_query = normalize(query)
    score += code.eq(exact_query).astype(int) * 10_000
    score += code.str.contains(re.escape(exact_query), regex=True).astype(int) * 500
    for token in tokens:
        escaped = re.escape(token)
        score += code.str.contains(escaped, regex=True).astype(int) * 100
        score += name_tr.str.contains(escaped, regex=True).astype(int) * 30
        score += group.str.contains(escaped, regex=True).astype(int) * 15
        score += searchable.str.contains(escaped, regex=True).astype(int) * 5

    frame = frame.assign(_score=score)
    frame = frame.loc[frame["_score"].gt(0)].sort_values(
        ["_score", "is_archive", "series_code"],
        ascending=[False, True, True],
        kind="stable",
    )
    return frame.head(limit)[
        [
            "series_code",
            "series_name_tr",
            "group_name_tr",
            "frequency",
            "unit",
            "default_aggregation",
            "source",
            "is_archive",
            "_score",
        ]
    ]


def build_manifest(
    catalog: pd.DataFrame,
    series_codes: list[str],
    start_date: str,
    end_date: str,
    dataset_id: str,
    allow_archive: bool = False,
) -> dict[str, Any]:
    codes = list(dict.fromkeys(str(code).strip() for code in series_codes))
    if not codes or any(not code for code in codes):
        raise ValueError("En az bir dolu EVDS seri kodu gerekli.")
    if len(codes) != len(series_codes):
        raise ValueError("EVDS seri kodlari tekrarlanmamali.")

    indexed = catalog.set_index("series_code", drop=False)
    missing = sorted(set(codes) - set(indexed.index.astype(str)))
    if missing:
        raise ValueError(f"EVDS katalogunda bulunmayan seri kodlari: {missing}")

    selections = []
    for code in codes:
        row = indexed.loc[code]
        if isinstance(row, pd.DataFrame):
            raise ValueError(f"EVDS katalogunda tekrarlanan seri kodu var: {code}")
        if bool(row["is_archive"]) and not allow_archive:
            raise ValueError(
                f"Arsiv EVDS serisi acik izin olmadan indirilemez: {code}"
            )
        selections.append(
            {
                "series_code": code,
                "role": "on_demand",
                "reason": "Katalogdan talep uzerine secildi",
                "aggregation": str(row.get("default_aggregation") or "avg"),
            }
        )

    return {
        "dataset_id": dataset_id,
        "start_date": start_date,
        "end_date": end_date,
        "selection_policy": "catalog_validated_on_demand",
        "series": selections,
    }


def generated_dataset_id(codes: list[str], start_date: str, end_date: str) -> str:
    payload = "|".join([*codes, start_date, end_date]).encode("utf-8")
    return "evds.on_demand." + hashlib.sha256(payload).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--search", help="Katalogda Turkce ad, grup veya seri kodu ara.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--series", nargs="+", help="Indirilecek EVDS seri kodlari.")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--dataset-id")
    parser.add_argument(
        "--workspace",
        help="Bu acquisition'i izole eden workspace kimligi (--series ile zorunlu; "
        "--search-only cagrilarda gerekmez). Cikti "
        "DEFAULT_OUTPUT_ROOT/<workspace>/<hash> altina, workspace'e ozel kilitle yazilir.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-archive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument(
        "--transport",
        choices=["curl", "urllib"],
        default="curl" if shutil.which("curl") else "urllib",
    )
    args = parser.parse_args()

    catalog_path = args.catalog.expanduser().resolve()
    catalog = pd.read_parquet(catalog_path)
    if args.search:
        result = search_catalog(
            catalog,
            args.search,
            limit=args.limit,
            include_archive=args.include_archive,
        )
        print(result.to_json(orient="records", force_ascii=False, indent=2))
        if not args.series:
            return 0
    if not args.series:
        parser.error("--search veya --series parametrelerinden en az biri gerekli.")
    if not args.output and not args.workspace:
        parser.error(
            "--workspace gerekli (ya da acik bir --output verin): on-demand "
            "acquisition'lar artik global degil, workspace'e izole edilir."
        )

    dataset_id = args.dataset_id or generated_dataset_id(
        args.series, args.start, args.end
    )
    dataset_hash = dataset_id.rsplit(".", 1)[-1]
    lock_context = contextlib.nullcontext()
    if args.output:
        output = args.output.expanduser().resolve()
    else:
        workspace_id = validate_workspace_id(args.workspace)
        output = workspace_output_root(workspace_id) / dataset_hash
        lock_context = workspace_acquisition_lock(workspace_id, dataset_hash)

    with lock_context:
        output.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(
            catalog,
            args.series,
            args.start,
            args.end,
            dataset_id,
            allow_archive=args.include_archive,
        )
        manifest_path = output / "generated_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Manifest: {manifest_path}")
        if args.dry_run:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0

        return run_manifest_download(
            SimpleNamespace(
                manifest=manifest_path,
                catalog=catalog_path,
                output=output,
                timeout=args.timeout,
                retries=args.retries,
                delay=args.delay,
                transport=args.transport,
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
