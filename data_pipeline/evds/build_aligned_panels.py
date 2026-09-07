#!/usr/bin/env python3
"""Build monthly and quarterly panels from a generic EVDS long dataset.

Frequency conversion is controlled by an explicit role policy. Quarterly
observations are never copied into intervening months. Null source values are
not turned into zeros, and each aggregated value has a companion audit row.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "housing_causality_v1"
DEFAULT_POLICY = BASE_DIR / "manifests" / "housing_causality_alignment_v1.json"
HIGH_FREQUENCY = {"GÜNLÜK", "İŞ GÜNÜ", "HAFTALIK(CUMA)", "HAFTALIK(ÇARŞAMBA)", "AYDA İKİ KEZ"}


def safe_column(series_code: str) -> str:
    return series_code.replace(".", "_").replace("-", "_")


def aggregate_value(values: pd.Series, method: str) -> float | None:
    non_null = values.dropna()
    if non_null.empty:
        return None
    if method == "mean":
        return float(non_null.mean())
    if method == "sum":
        return float(non_null.sum())
    if method == "last":
        return float(non_null.iloc[-1])
    if method == "first":
        return float(non_null.iloc[0])
    if method == "min":
        return float(non_null.min())
    if method == "max":
        return float(non_null.max())
    if method == "identity":
        if len(values) != 1:
            raise ValueError(f"Identity toplulastirmasi tek gozlem bekler, gelen={len(values)}")
        return None if pd.isna(values.iloc[0]) else float(values.iloc[0])
    raise ValueError(f"Desteklenmeyen toplulastirma: {method}")


def align_series(
    group: pd.DataFrame,
    target_frequency: str,
    method: str,
) -> list[dict[str, Any]]:
    native_frequency = str(group["native_frequency"].iloc[0])
    group = group.sort_values(["period_end", "period"], kind="stable").copy()
    group["period_end_date"] = pd.to_datetime(group["period_end"])
    if target_frequency == "monthly":
        if native_frequency == "ÜÇ AYLIK":
            group["target_period"] = group["period_end_date"].dt.to_period("M").astype(str)
            applied_method = "identity"
        elif native_frequency == "AYLIK":
            group["target_period"] = group["period_end_date"].dt.to_period("M").astype(str)
            applied_method = "identity"
        else:
            group["target_period"] = group["period_end_date"].dt.to_period("M").astype(str)
            applied_method = method
    elif target_frequency == "quarterly":
        group["target_period"] = group["period_end_date"].dt.to_period("Q").astype(str)
        applied_method = "identity" if native_frequency == "ÜÇ AYLIK" else method
    else:
        raise ValueError(f"Desteklenmeyen hedef frekans: {target_frequency}")

    result = []
    for target_period, observations in group.groupby("target_period", sort=True):
        if applied_method == "identity" and len(observations) != 1:
            raise ValueError(
                f"{group['series_code'].iloc[0]} {target_period} icin tek gozlem bekleniyordu, "
                f"gelen={len(observations)}"
            )
        result.append(
            {
                "target_period": target_period,
                "series_code": group["series_code"].iloc[0],
                "analysis_column": safe_column(str(group["series_code"].iloc[0])),
                "series_name_tr": group["series_name_tr"].iloc[0],
                "role": group["role"].iloc[0],
                "native_frequency": native_frequency,
                "target_frequency": target_frequency,
                "aggregation": applied_method,
                "value": aggregate_value(observations["value"], applied_method),
                "source_observation_count": len(observations),
                "non_null_observation_count": int(observations["value"].notna().sum()),
                "missing_observation_count": int(observations["value"].isna().sum()),
                "first_source_period": observations["period"].iloc[0],
                "last_source_period": observations["period"].iloc[-1],
                "source_period_end_max": observations["period_end"].max(),
            }
        )
    return result


def build(input_dir: Path, policy_path: Path) -> dict[str, Any]:
    observations_path = input_dir / "observations_long.parquet"
    catalog_path = input_dir / "series_catalog.parquet"
    validation_path = input_dir / "validation.json"
    if not all(path.exists() for path in [observations_path, catalog_path, validation_path]):
        raise ValueError("EVDS gozlem, katalog veya dogrulama dosyasi bulunamadi.")
    source_validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if source_validation.get("status") != "passed":
        raise ValueError("Dogrulanmamis EVDS snapshot'i hizalanamaz.")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("dataset_id") != source_validation.get("dataset_id"):
        raise ValueError("EVDS hizalama politikasi farkli veri setine ait.")

    observations = pd.read_parquet(observations_path)
    catalog = pd.read_parquet(catalog_path)
    roles = set(catalog["role"].dropna().astype(str))
    missing_roles = sorted(roles - set(policy["role_policies"]))
    if missing_roles:
        raise ValueError(f"Hizalama politikasi olmayan roller: {missing_roles}")
    if policy.get("quarterly_to_monthly") != "quarter_end_only":
        raise ValueError("Ceyreklik seriler aylara otomatik yayilamaz.")

    outputs: dict[str, pd.DataFrame] = {}
    audits: dict[str, pd.DataFrame] = {}
    for target_frequency in ["monthly", "quarterly"]:
        rows: list[dict[str, Any]] = []
        for series_code, group in observations.groupby("series_code", sort=True):
            role = str(group["role"].iloc[0])
            method = policy["role_policies"][role]["subperiod_aggregation"]
            rows.extend(align_series(group, target_frequency, method))
        audit = pd.DataFrame(rows).sort_values(
            ["target_period", "series_code"], kind="stable"
        )
        if audit.duplicated(["target_period", "series_code"]).any():
            raise ValueError(f"{target_frequency} hizalama anahtari tekrarlaniyor.")
        wide = audit.pivot(
            index="target_period", columns="analysis_column", values="value"
        ).reset_index()
        wide.columns.name = None
        outputs[target_frequency] = wide
        audits[target_frequency] = audit

    enriched_catalog = catalog.copy()
    enriched_catalog["analysis_column"] = enriched_catalog["series_code"].map(safe_column)
    enriched_catalog["temporal_semantics"] = enriched_catalog["role"].map(
        lambda role: policy["role_policies"][role]["temporal_semantics"]
    )
    enriched_catalog["subperiod_aggregation"] = enriched_catalog["role"].map(
        lambda role: policy["role_policies"][role]["subperiod_aggregation"]
    )

    for name, frame in outputs.items():
        frame.to_csv(input_dir / f"{name}_panel.csv", index=False, encoding="utf-8-sig")
        frame.to_parquet(input_dir / f"{name}_panel.parquet", index=False)
        audits[name].to_csv(
            input_dir / f"{name}_alignment_audit.csv", index=False, encoding="utf-8-sig"
        )
        audits[name].to_parquet(
            input_dir / f"{name}_alignment_audit.parquet", index=False
        )
    enriched_catalog.to_csv(
        input_dir / "analysis_series_catalog.csv", index=False, encoding="utf-8-sig"
    )
    enriched_catalog.to_parquet(input_dir / "analysis_series_catalog.parquet", index=False)

    quarterly_series = set(
        catalog.loc[catalog["frequency"].eq("ÜÇ AYLIK"), "series_code"]
    )
    monthly_quarterly_rows = audits["monthly"].loc[
        audits["monthly"]["series_code"].isin(quarterly_series)
    ]
    if not monthly_quarterly_rows["target_period"].str.endswith(("03", "06", "09", "12")).all():
        raise ValueError("Ceyreklik EVDS verisi ara aylara yayildi.")

    result = {
        "status": "passed",
        "dataset_id": source_validation["dataset_id"],
        "series_count": int(catalog["series_code"].nunique()),
        "monthly_period_count": len(outputs["monthly"]),
        "quarterly_period_count": len(outputs["quarterly"]),
        "monthly_audit_rows": len(audits["monthly"]),
        "quarterly_audit_rows": len(audits["quarterly"]),
        "quarterly_series_month_rows": len(monthly_quarterly_rows),
        "quarterly_series_filled_into_intermediate_months": 0,
        "policy_file": policy_path.name,
        "quality_policy": [
            "Every aggregation method is selected through an explicit analytical role policy.",
            "Quarterly observations appear only in quarter-end months in the monthly panel.",
            "Flow variables are summed; rates and survey measures are averaged; period-end levels use the last value.",
            "Every panel value has an audit row with source, non-null and missing observation counts.",
            "No interpolation, forward fill or backward fill is applied.",
        ],
    }
    (input_dir / "alignment_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    result = build(args.input.expanduser().resolve(), args.policy.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
