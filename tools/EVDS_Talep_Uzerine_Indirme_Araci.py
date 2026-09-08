#!/usr/bin/env python3
"""Search the local EVDS catalog and fetch arbitrary public series on demand.

The full EVDS metadata catalog stays local, while observation values are
downloaded only for series selected by the user or an agent. The generated
manifest is then executed by the audited EVDS manifest downloader, so raw
requests, raw responses, missing values and SHA-256 lineage are preserved.
"""

from __future__ import annotations

import argparse
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

    dataset_id = args.dataset_id or generated_dataset_id(
        args.series, args.start, args.end
    )
    output = (
        args.output.expanduser().resolve()
        if args.output
        else (DEFAULT_OUTPUT_ROOT / dataset_id.rsplit(".", 1)[-1]).resolve()
    )
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
