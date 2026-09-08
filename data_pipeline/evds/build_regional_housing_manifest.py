#!/usr/bin/env python3
"""Build the catalog-driven EVDS manifest for the regional housing dataset.

The generated manifest intentionally excludes series already stored by another
versioned EVDS dataset. This keeps each official source series in exactly one
local snapshot while still allowing the regional analysis layer to join the
existing national and metropolitan series when needed.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet"
DEFAULT_OUTPUT = PROJECT_ROOT / "data_pipeline" / "evds" / "manifests" / "regional_housing_v1.json"
DEFAULT_VALIDATION = (
    PROJECT_ROOT
    / "data_pipeline"
    / "evds"
    / "manifests"
    / "regional_housing_v1_validation.json"
)

SALES_GROUP_ROLES = {
    "bie_akonutsat1": "province_housing_sales_total",
    "bie_akonutsat2": "province_housing_sales_mortgaged",
    "bie_akonutsat3": "province_housing_sales_first_hand",
    "bie_akonutsat4": "province_housing_sales_second_hand",
}

EXPECTED_ROLE_COUNTS = {
    "province_housing_sales_total": 81,
    "province_housing_sales_mortgaged": 81,
    "province_housing_sales_first_hand": 81,
    "province_housing_sales_second_hand": 81,
    "province_housing_unit_price": 81,
    "regional_housing_price_index": 16,
    "regional_new_tenant_rent_index": 20,
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def selected_series_in_other_manifests(
    manifests_dir: Path, output_path: Path
) -> set[str]:
    selected: set[str] = set()
    for path in sorted(manifests_dir.glob("*.json")):
        if path.resolve() == output_path.resolve() or "alignment" in path.stem:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("series", []):
            selected.add(str(item["series_code"]))
    return selected


def build_manifest(
    catalog: pd.DataFrame,
    excluded_series: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    active = catalog.loc[~catalog["is_archive"].astype(bool)].copy()
    selections: list[dict[str, str]] = []

    for group_code, role in SALES_GROUP_ROLES.items():
        rows = active.loc[
            active["group_code"].eq(group_code)
            & active["series_code"].astype(str).str.match(r"^TP\.AKONUTSAT[1-4]\.KTR")
            & ~active["series_code"].astype(str).str.endswith("KTRTOPLAM")
            & active["series_name_tr"].fillna("").str.contains("_Konut_")
        ]
        for row in rows.sort_values("series_code").to_dict("records"):
            selections.append(
                {
                    "series_code": str(row["series_code"]),
                    "role": role,
                    "reason": (
                        "Il bazinda konut satis hareketini ve finansman "
                        "kompozisyonunu olcmek"
                    ),
                    "aggregation": "sum",
                }
            )

    unit_prices = active.loc[
        active["group_code"].eq("bie_birimfiyat")
        & ~active["series_code"].eq("TP.BIRIMFIYAT.TR")
    ]
    for row in unit_prices.sort_values("series_code").to_dict("records"):
        selections.append(
            {
                "series_code": str(row["series_code"]),
                "role": "province_housing_unit_price",
                "reason": "Il bazinda konut birim fiyat seviyesini olcmek",
                "aggregation": "last",
            }
        )

    regional_kfe = active.loc[active["group_code"].eq("bie_kfe")]
    for row in regional_kfe.sort_values("series_code").to_dict("records"):
        code = str(row["series_code"])
        if code in excluded_series:
            continue
        selections.append(
            {
                "series_code": code,
                "role": "regional_housing_price_index",
                "reason": "Il grubunun nominal konut fiyat hareketini kontrol etmek",
                "aggregation": "last",
            }
        )

    regional_rents = active.loc[active["group_code"].eq("bie_ykke")]
    for row in regional_rents.sort_values("series_code").to_dict("records"):
        selections.append(
            {
                "series_code": str(row["series_code"]),
                "role": "regional_new_tenant_rent_index",
                "reason": "Yeni kiraci kira maliyetindeki bolgesel hareketi kontrol etmek",
                "aggregation": "last",
            }
        )

    codes = [item["series_code"] for item in selections]
    role_counts = Counter(item["role"] for item in selections)
    duplicates = sorted(code for code, count in Counter(codes).items() if count > 1)
    overlaps = sorted(set(codes) & excluded_series)
    actual_role_counts = dict(sorted(role_counts.items()))
    expected_role_counts = dict(sorted(EXPECTED_ROLE_COUNTS.items()))
    if duplicates:
        raise ValueError(f"Bolgesel EVDS manifestinde tekrar var: {duplicates[:5]}")
    if overlaps:
        raise ValueError(f"EVDS serisi baska pakette zaten secili: {overlaps[:5]}")
    if actual_role_counts != expected_role_counts:
        raise ValueError(
            "Bolgesel EVDS katalog kapsami beklenen sayilarla uyusmuyor: "
            f"beklenen={expected_role_counts}, bulunan={actual_role_counts}"
        )

    manifest = {
        "dataset_id": "regional_housing_v1",
        "description": (
            "Il bazinda konut satislari ve birim fiyatlari ile bolgesel KFE ve "
            "yeni kiraci kira endekslerini birlestiren EVDS gozlem paketi."
        ),
        "start_date": "2020-01-01",
        "end_date": "2026-06-30",
        "selection_policy": "catalog_driven_regional_housing_without_cross_dataset_duplicates",
        "series": sorted(selections, key=lambda item: item["series_code"]),
    }
    validation = {
        "status": "passed",
        "dataset_id": manifest["dataset_id"],
        "series_count": len(selections),
        "role_counts": actual_role_counts,
        "excluded_existing_series_count": len(excluded_series),
        "cross_dataset_duplicate_count": len(overlaps),
        "within_manifest_duplicate_count": len(duplicates),
        "catalog_selection_rules": [
            "Only active EVDS series are selected.",
            "Housing sales include 81 provinces and exclude national and workplace series.",
            "Unit prices include 81 provinces and exclude the national series already stored elsewhere.",
            "Regional KFE excludes the four series already stored in housing_causality_v1.",
            "All 20 active YKKE series are selected.",
        ],
    }
    return manifest, validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    args = parser.parse_args()

    catalog_path = args.catalog.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    validation_path = args.validation.expanduser().resolve()
    catalog = pd.read_parquet(catalog_path)
    excluded = selected_series_in_other_manifests(output_path.parent, output_path)
    manifest, validation = build_manifest(catalog, excluded)
    atomic_json(output_path, manifest)
    atomic_json(validation_path, validation)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
