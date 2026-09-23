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
        return None if values.isna().any() else float(non_null.sum())
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


def coverage_audit(
    observations: pd.DataFrame,
    target_period: str,
    target_frequency: str,
    native_frequency: str,
    method: str,
) -> dict[str, Any]:
    """Describe coverage without treating market-calendar nulls as proven gaps."""
    target = pd.Period(target_period, freq="M" if target_frequency == "monthly" else "Q")
    target_end = target.end_time.normalize()
    non_null = observations.loc[observations["value"].notna()]
    expected: set[str] | None = None
    if native_frequency == "AYLIK":
        expected = set(pd.period_range(target.start_time, target.end_time, freq="M").astype(str))
        actual = set(observations["period_end_date"].dt.to_period("M").astype(str))
        valid = set(non_null["period_end_date"].dt.to_period("M").astype(str))
    elif native_frequency == "ÜÇ AYLIK":
        expected = {str(target.asfreq("Q"))}
        actual = set(observations["period_end_date"].dt.to_period("Q").astype(str))
        valid = set(non_null["period_end_date"].dt.to_period("Q").astype(str))
    else:
        actual, valid = set(), set()
    missing = sorted(expected - valid) if expected is not None else None
    complete = not missing if expected is not None else None
    selected = None
    if not non_null.empty and method in {"identity", "first", "last"}:
        selected = non_null.iloc[0 if method in {"identity", "first"} else -1]
    valid_end = non_null["period_end_date"].max() if not non_null.empty else None
    selected_end = selected["period_end_date"] if selected is not None else None
    return {
        "expected_source_observation_count": len(expected) if expected is not None else None,
        "absent_source_period_count": len(expected - actual) if expected is not None else None,
        "missing_source_periods": json.dumps(missing, ensure_ascii=False),
        "is_complete": complete,
        "completeness_status": "complete" if complete is True else "partial" if complete is False else "calendar_unverified",
        "selected_source_period": str(selected["period"]) if selected is not None else None,
        "selected_source_period_end": selected_end.strftime("%Y-%m-%d") if selected_end is not None else None,
        "valid_source_period_end_min": non_null["period_end_date"].min().strftime("%Y-%m-%d") if not non_null.empty else None,
        "valid_source_period_end_max": valid_end.strftime("%Y-%m-%d") if valid_end is not None else None,
        "staleness_days": int((target_end - (selected_end if selected_end is not None else valid_end)).days) if valid_end is not None else None,
        "representation": "quarter_end_only" if native_frequency == "ÜÇ AYLIK" and target_frequency == "monthly" else "target_period_aggregate",
        "value_frequency": "quarterly" if native_frequency == "ÜÇ AYLIK" else target_frequency,
    }


def align_series(
    group: pd.DataFrame,
    target_frequency: str,
    method: str,
) -> list[dict[str, Any]]:
    native_frequency = str(group["native_frequency"].iloc[0])
    if native_frequency not in HIGH_FREQUENCY | {"AYLIK", "ÜÇ AYLIK"}:
        raise ValueError(f"Bu hedefler icin acik hizalama politikasi yok: {native_frequency}")
    if group["native_frequency"].nunique() != 1 or group["series_code"].nunique() != 1:
        raise ValueError("Tek bir seri ve dogal frekans bekleniyordu.")
    if group.duplicated("period").any() or group.duplicated("period_end").any():
        raise ValueError("EVDS kaynak donemi tekrarlaniyor; toplulastirma satir cogaltamaz.")
    group = group.sort_values(["period_end", "period"], kind="stable").copy()
    group["period_end_date"] = pd.to_datetime(group["period_end"])
    if native_frequency == "ÜÇ AYLIK" and not group["period_end_date"].dt.is_quarter_end.all():
        raise ValueError("Ceyreklik kaynak donem sonu ceyrek sonu olmali.")
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
        coverage = coverage_audit(observations, target_period, target_frequency, native_frequency, applied_method)
        value = aggregate_value(observations["value"], applied_method)
        status = "available"
        if applied_method == "sum" and coverage["is_complete"] is not True:
            value = None
            status = "unavailable_incomplete_sum"
        elif value is None:
            status = "source_missing"
        elif coverage["is_complete"] is False:
            status = "available_partial_" + applied_method
        elif coverage["is_complete"] is None:
            status = "available_calendar_unverified"
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
                "value": value,
                "value_status": status,
                "source_observation_count": len(observations),
                "non_null_observation_count": int(observations["value"].notna().sum()),
                "missing_observation_count": int(observations["value"].isna().sum()),
                "first_source_period": observations["period"].iloc[0],
                "last_source_period": observations["period"].iloc[-1],
                "source_period_end_max": observations["period_end"].max(),
                **coverage,
            }
        )
    return result


def add_derived_series(
    audit: pd.DataFrame,
    derived_specs: list[dict[str, Any]],
) -> pd.DataFrame:
    """Apply small, declarative transformations after source aggregation."""

    result = audit.copy()
    result["is_derived"] = False
    result["source_series_code"] = ""
    result["derivation_operation"] = ""
    result["derivation_factor"] = float("nan")
    additions = []
    source_codes = set(result["series_code"])
    derived_codes: set[str] = set()
    for spec in derived_specs:
        code = str(spec["series_code"])
        source_code = str(spec["source_series_code"])
        operation = str(spec["operation"])
        if code in source_codes or code in derived_codes:
            raise ValueError(f"Turetilmis EVDS seri kodu tekrarlaniyor: {code}")
        if source_code not in source_codes:
            raise ValueError(f"Turetilmis EVDS kaynak serisi bulunamadi: {source_code}")
        if operation != "scale":
            raise ValueError(f"Desteklenmeyen EVDS turetim islemi: {operation}")
        factor = float(spec["factor"])
        derived = result.loc[result["series_code"].eq(source_code)].copy()
        derived["series_code"] = code
        derived["analysis_column"] = safe_column(code)
        derived["series_name_tr"] = spec["series_name_tr"]
        derived["role"] = spec["role"]
        derived["native_frequency"] = "DERIVED"
        derived["aggregation"] = (
            "scale_after_" + derived["aggregation"].astype(str)
        )
        derived["value"] = derived["value"] * factor
        derived["is_derived"] = True
        derived["source_series_code"] = source_code
        derived["derivation_operation"] = operation
        derived["derivation_factor"] = factor
        additions.append(derived)
        derived_codes.add(code)
    if additions:
        result = pd.concat([result, *additions], ignore_index=True)
    return result.sort_values(["target_period", "series_code"], kind="stable")


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
    derived_specs = policy.get("derived_series", [])
    for target_frequency in ["monthly", "quarterly"]:
        rows: list[dict[str, Any]] = []
        for series_code, group in observations.groupby("series_code", sort=True):
            role = str(group["role"].iloc[0])
            method = policy["role_policies"][role]["subperiod_aggregation"]
            rows.extend(align_series(group, target_frequency, method))
        audit = pd.DataFrame(rows).sort_values(
            ["target_period", "series_code"], kind="stable"
        )
        audit = add_derived_series(audit, derived_specs)
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
    enriched_catalog["is_derived"] = False
    enriched_catalog["source_series_code"] = ""
    enriched_catalog["derivation_operation"] = ""
    enriched_catalog["derivation_factor"] = float("nan")
    derived_catalog_rows = []
    for spec in derived_specs:
        source_rows = enriched_catalog.loc[
            enriched_catalog["series_code"].eq(spec["source_series_code"])
        ]
        if len(source_rows) != 1:
            raise ValueError(
                f"Turetilmis katalog kaynak serisi tekil degil: {spec['source_series_code']}"
            )
        derived_row = source_rows.iloc[0].to_dict()
        derived_row.update(
            {
                "series_code": spec["series_code"],
                "series_name_tr": spec["series_name_tr"],
                "series_name_en": spec.get("series_name_en", ""),
                "role": spec["role"],
                "reason": spec["reason"],
                "unit": spec["unit"],
                "source": spec.get(
                    "source", "Derived from an official TCMB EVDS source series"
                ),
                "frequency": "DERIVED",
                "native_frequency": "DERIVED",
                "analysis_column": safe_column(str(spec["series_code"])),
                "temporal_semantics": policy["role_policies"][spec["role"]][
                    "temporal_semantics"
                ],
                "subperiod_aggregation": "scale_after_"
                + policy["role_policies"][spec["role"]]["subperiod_aggregation"],
                "is_derived": True,
                "source_series_code": spec["source_series_code"],
                "derivation_operation": spec["operation"],
                "derivation_factor": float(spec["factor"]),
                "is_archive": False,
            }
        )
        derived_catalog_rows.append(derived_row)
    if derived_catalog_rows:
        enriched_catalog = pd.concat(
            [enriched_catalog, pd.DataFrame(derived_catalog_rows)], ignore_index=True
        ).sort_values("series_code", kind="stable")

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
        "source_series_count": int(catalog["series_code"].nunique()),
        "derived_series_count": len(derived_specs),
        "series_count": int(enriched_catalog["series_code"].nunique()),
        "monthly_period_count": len(outputs["monthly"]),
        "quarterly_period_count": len(outputs["quarterly"]),
        "monthly_audit_rows": len(audits["monthly"]),
        "quarterly_audit_rows": len(audits["quarterly"]),
        "quarterly_series_month_rows": len(monthly_quarterly_rows),
        "quarterly_series_filled_into_intermediate_months": 0,
        "policy_file": policy_path.name,
        "value_status_counts": {
            frequency: {str(key): int(value) for key, value in audit["value_status"].value_counts().items()}
            for frequency, audit in audits.items()
        },
        "quality_policy": [
            "Every aggregation method is selected through an explicit analytical role policy.",
            "Quarterly observations appear only in quarter-end months in the monthly panel.",
            "Flow sums require every expected native period and numeric value; incomplete sums remain null.",
            "Partial means and last values retain completeness warnings, actual selected dates and staleness in days.",
            "High-frequency calendar completeness is unverified unless a source-specific calendar is supplied.",
            "Every panel value has an audit row with source, non-null and missing observation counts.",
            "No interpolation, forward fill or backward fill is applied.",
            "Derived series use explicit declarative operations and retain their source series code and factor.",
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
