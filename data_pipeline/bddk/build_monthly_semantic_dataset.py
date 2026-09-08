#!/usr/bin/env python3
"""Create a semantics-aware long dataset from all BDDK monthly tables.

Source values are always retained. The income-statement table is treated as a
year-to-date cumulative flow using an explicit, reviewable policy. Its analysis
value is the within-calendar-year monthly difference. All other tables use an
identity transformation with stock, level, ratio or count semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from calendar import monthrange
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "processed" / "monthly_all_groups"
DEFAULT_POLICY = BASE_DIR / "monthly_semantics_v1.json"
DEFAULT_OUTPUT = BASE_DIR / "processed" / "monthly_semantic"

METADATA_COLUMNS = {
    "month",
    "table_no",
    "table_name",
    "group_code",
    "source_row_index",
    "source_caption",
    "source_unit",
    "source_file",
    "source_sha256",
    "source_request_info_file",
    "source_request_info_sha256",
    "period_validation_source",
    "BankaAdi",
    "BasitSira",
    "Ad",
    "BasitFont",
}


def compact_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def label_fingerprint(value: Any) -> str:
    normalized = " ".join(str(value).split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def source_unit(row: pd.Series, value_column: str) -> str:
    declared = row.get("source_unit")
    if declared is not None and not pd.isna(declared) and str(declared).strip():
        return str(declared).strip()
    if value_column == "Rasyo":
        return "percent_or_ratio_source_defined"
    if value_column == "Adet":
        return "count"
    return "source_defined"


def to_long(path: Path, table_policy: dict[str, str]) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    value_columns = [column for column in frame.columns if column not in METADATA_COLUMNS]
    id_columns = [column for column in frame.columns if column not in value_columns]
    long = frame.melt(
        id_vars=id_columns,
        value_vars=value_columns,
        var_name="value_dimension",
        value_name="source_value",
    )
    long["source_value"] = pd.to_numeric(long["source_value"], errors="coerce")
    long["source_sequence"] = long["BasitSira"].map(compact_id)
    long["source_label_fingerprint"] = long["Ad"].map(label_fingerprint)
    long["metric_code"] = (
        "table"
        + long["table_no"].astype(int).astype(str).str.zfill(2)
        + ":"
        + long["source_sequence"]
        + ":"
        + long["source_label_fingerprint"]
        + ":"
        + long["value_dimension"]
    )
    long["metric_label"] = long["Ad"].astype(str)
    long["observation_date"] = long["month"].map(
        lambda value: (
            f"{value}-{monthrange(int(value[:4]), int(value[5:7]))[1]:02d}"
        )
    )
    long["calendar_year"] = long["month"].str[:4].astype(int)
    long["native_frequency"] = "monthly"
    long["source_semantics"] = table_policy["source_semantics"]
    long["analysis_semantics"] = table_policy["analysis_semantics"]
    long["transformation"] = table_policy["transformation"]
    long["quarterly_aggregation"] = table_policy["quarterly_aggregation"]
    long["semantic_confidence"] = table_policy["confidence"]
    long["unit"] = long.apply(
        lambda row: source_unit(row, str(row["value_dimension"])), axis=1
    )
    return long


def apply_transformations(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(
        ["table_no", "metric_code", "group_code", "month"], kind="stable"
    ).reset_index(drop=True)
    frame["analysis_value"] = frame["source_value"]
    cumulative = frame["transformation"].eq("difference_within_calendar_year")
    cumulative_frame = frame.loc[cumulative].copy()
    group_columns = ["table_no", "metric_code", "group_code", "calendar_year"]
    differences = cumulative_frame.groupby(group_columns, sort=False)[
        "source_value"
    ].diff()
    january = cumulative_frame["month"].str.endswith("-01")
    differences.loc[january] = cumulative_frame.loc[january, "source_value"]
    frame.loc[cumulative, "analysis_value"] = differences
    return frame


def validate_recomposition(frame: pd.DataFrame) -> dict[str, Any]:
    cumulative = frame.loc[
        frame["transformation"].eq("difference_within_calendar_year")
    ].copy()
    group_columns = ["table_no", "metric_code", "group_code", "calendar_year"]
    cumulative["recomposed"] = cumulative.groupby(group_columns, sort=False)[
        "analysis_value"
    ].cumsum()
    comparable = cumulative["source_value"].notna() & cumulative["recomposed"].notna()
    differences = (
        cumulative.loc[comparable, "source_value"]
        - cumulative.loc[comparable, "recomposed"]
    ).abs()
    if (differences > 0.000001).any():
        failed = cumulative.loc[differences.index[differences > 0.000001]].iloc[0]
        raise ValueError(
            "BDDK kümülatif yeniden bileşim kontrolü geçmedi: "
            f"{failed['month']} {failed['metric_code']}"
        )

    january = cumulative.loc[cumulative["month"].str.endswith("-01")]
    january_differences = (january["source_value"] - january["analysis_value"]).abs()
    if (january_differences.dropna() > 0.000001).any():
        raise ValueError("BDDK ocak kümülatif başlangıç değerleri korunmadı.")

    reset_pairs = []
    for _, group in cumulative.groupby(["metric_code", "group_code"], sort=False):
        group = group.sort_values("month")
        december = group.loc[group["month"].str.endswith("-12"), ["calendar_year", "source_value"]]
        january_rows = group.loc[group["month"].str.endswith("-01"), ["calendar_year", "source_value"]]
        december_lookup = {
            int(row.calendar_year): row.source_value for row in december.itertuples()
        }
        for row in january_rows.itertuples():
            previous = december_lookup.get(int(row.calendar_year) - 1)
            if previous is None or pd.isna(previous) or pd.isna(row.source_value):
                continue
            reset_pairs.append(abs(float(row.source_value)) < abs(float(previous)))

    return {
        "cumulative_measurement_rows": len(cumulative),
        "recomposition_comparisons": len(differences),
        "maximum_recomposition_difference": float(differences.max()) if len(differences) else 0.0,
        "january_start_rows": len(january),
        "annual_reset_pairs_checked": len(reset_pairs),
        "annual_reset_pairs_with_lower_january_absolute_value": int(sum(reset_pairs)),
        "annual_reset_share": round(sum(reset_pairs) / len(reset_pairs), 6) if reset_pairs else None,
    }


def build(input_dir: Path, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    validation_path = input_dir / "validation.json"
    if not validation_path.exists():
        raise ValueError("BDDK aylık doğrulama dosyası bulunamadı.")
    source_validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if source_validation.get("status") != "passed":
        raise ValueError("Doğrulanmamış BDDK aylık verisi semantik katmana alınamaz.")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    expected_tables = {str(value) for value in range(1, 18)}
    if set(policy["tables"]) != expected_tables:
        raise ValueError("BDDK aylık semantik politikası 17 tablonun tamamını kapsamıyor.")

    frames = []
    for table_no in range(1, 18):
        path = input_dir / f"table_{table_no:02d}.parquet"
        frames.append(to_long(path, policy["tables"][str(table_no)]))
    measurements = apply_transformations(pd.concat(frames, ignore_index=True))

    key = ["month", "table_no", "group_code", "metric_code"]
    if measurements.duplicated(key).any():
        duplicate = measurements.loc[measurements.duplicated(key, keep=False), key].iloc[0]
        raise ValueError(f"BDDK semantik ölçüm anahtarı tekrarlanıyor: {duplicate.to_dict()}")

    identity = measurements["transformation"].eq("identity")
    identity_difference = (
        measurements.loc[identity, "source_value"]
        - measurements.loc[identity, "analysis_value"]
    ).abs()
    if (identity_difference.dropna() > 0.000001).any():
        raise ValueError("BDDK identity dönüşümü kaynak değerini değiştirdi.")
    if not measurements.loc[identity, "source_value"].isna().equals(
        measurements.loc[identity, "analysis_value"].isna()
    ):
        raise ValueError("BDDK identity dönüşümü eksik değer durumunu değiştirdi.")

    recomposition = validate_recomposition(measurements)
    dictionary = (
        measurements.groupby(
            [
                "table_no",
                "table_name",
                "metric_code",
                "source_sequence",
                "source_label_fingerprint",
                "value_dimension",
                "unit",
                "source_semantics",
                "analysis_semantics",
                "transformation",
                "quarterly_aggregation",
                "semantic_confidence",
            ],
            dropna=False,
            sort=True,
        )
        .agg(
            metric_label=("metric_label", "last"),
            label_variant_count=("metric_label", "nunique"),
            first_month=("month", "min"),
            last_month=("month", "max"),
            observation_count=("analysis_value", "count"),
            missing_observation_count=("analysis_value", lambda values: int(values.isna().sum())),
        )
        .reset_index()
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    measurements.to_parquet(output_dir / "measurements_long.parquet", index=False)
    dictionary.to_csv(
        output_dir / "metric_dictionary.csv", index=False, encoding="utf-8-sig"
    )
    dictionary.to_parquet(output_dir / "metric_dictionary.parquet", index=False)

    result = {
        "status": "passed",
        "policy_id": policy["policy_id"],
        "source_validation_status": source_validation["status"],
        "table_count": int(measurements["table_no"].nunique()),
        "period_count": int(measurements["month"].nunique()),
        "metric_count": int(measurements["metric_code"].nunique()),
        "measurement_row_count": len(measurements),
        "source_missing_value_count": int(measurements["source_value"].isna().sum()),
        "analysis_missing_value_count": int(measurements["analysis_value"].isna().sum()),
        "identity_measurement_rows": int(identity.sum()),
        "derived_monthly_flow_rows": int((~identity).sum()),
        "recomposition": recomposition,
        "semantic_counts": {
            str(key): int(value)
            for key, value in measurements.groupby("analysis_semantics").size().sort_index().items()
        },
        "confidence_counts": {
            str(key): int(value)
            for key, value in measurements.groupby("semantic_confidence").size().sort_index().items()
        },
        "quality_policy": [
            "Source values and hashes remain attached to every semantic measurement.",
            "Year-to-date income-statement values are differenced only within the same calendar year.",
            "January analysis values equal January source values; December is never subtracted across years.",
            "Derived monthly flows recompose exactly to source cumulative values.",
            "No missing value is filled or converted to zero.",
            "Quarterly aggregation is explicit per table: cumulative monthly flows sum, stocks and ratios use the last observation.",
        ],
    }
    (output_dir / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(
        args.input.expanduser().resolve(),
        args.policy.expanduser().resolve(),
        args.output.expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
