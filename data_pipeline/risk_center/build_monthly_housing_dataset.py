#!/usr/bin/env python3
"""Extract an auditable monthly housing-credit panel from Risk Center PDFs.

The official June bulletins contain 13-month chart windows. All chart vintages
are retained, overlapping months are compared, and the latest publication is
selected for the analysis panel. Published chart rounding is preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
DEFAULT_INPUT = BASE_DIR / "monthly_housing_v1"
DEFAULT_OUTPUT = DEFAULT_INPUT / "processed"
TARGET_START = "2021-01"
TARGET_END = "2026-06"
DATE_BLOCK = re.compile(r"(?P<dates>(?:20\d{2}-\d{2}\s*){13})")
NUMBER_TOKEN = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?|\d+)(?![\w])"
)


METRIC_SPECS: dict[str, dict[str, str]] = {
    "housing_credit_balance_billion_try": {
        "metric_name_tr": "Konut kredisi kalan ana para bakiyesi",
        "unit": "billion_try",
        "temporal_semantics": "period_end_stock",
        "default_aggregation": "last",
        "caution": (
            "Faiz reeskontu ve faiz tahakkuku dahil, tasfiye olunacak alacaklar "
            "hariçtir. Yeni kredi kullandırım akımı değildir."
        ),
    },
    "housing_credit_borrower_count_million_person": {
        "metric_name_tr": "Konut kredisi tekil kişi sayısı",
        "unit": "million_person",
        "temporal_semantics": "period_end_level",
        "default_aggregation": "last",
        "caution": "Bültendeki grafik yuvarlaması korunur.",
    },
    "housing_credit_average_balance_try": {
        "metric_name_tr": "Kişi başına ortalama konut kredisi riski",
        "unit": "try_per_person",
        "temporal_semantics": "period_end_ratio",
        "default_aggregation": "last",
        "caution": "Toplam risk tutarının tekil kişi sayısına bölümüdür.",
    },
    "housing_credit_npl_ratio_pct": {
        "metric_name_tr": "Tasfiye olunacak konut kredileri oranı",
        "unit": "percent",
        "temporal_semantics": "period_end_ratio",
        "default_aggregation": "last",
        "caution": (
            "Tasfiye olunacak alacak tutarının konut kredisi ile tasfiye olunacak "
            "alacak toplamına oranıdır."
        ),
    },
    "first_time_housing_credit_users_thousand_person": {
        "metric_name_tr": "Finansal sisteme ilk kez konut kredisiyle giren kişi sayısı",
        "unit": "thousand_person",
        "temporal_semantics": "monthly_event_count",
        "default_aggregation": "sum_with_methodology_caution",
        "caution": (
            "Kredi kullandırım tutarı değildir. KVKK kaynaklı kayıt silme kuralı "
            "nedeniyle daha önce ürünü kullanmış bir kişi sonraki kullanımında yeniden "
            "ilk kez girmiş sayılabilir."
        ),
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_frame(frame: pd.DataFrame, output: Path, stem: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / f"{stem}.csv", index=False, encoding="utf-8-sig")
    frame.to_parquet(output / f"{stem}.parquet", index=False)


def chart_blocks(text: str) -> list[dict[str, Any]]:
    blocks = []
    for match in DATE_BLOCK.finditer(text.replace("\u00a0", " ")):
        dates = re.findall(r"20\d{2}-\d{2}", match.group("dates"))
        values = NUMBER_TOKEN.findall(text[: match.start()])[-13:]
        after = text[match.end() :].lstrip()
        label = next(
            (line.strip() for line in after.splitlines() if line.strip()),
            "",
        )
        if len(dates) != 13 or len(values) != 13:
            raise ValueError(
                f"13 dönem ve değer bekleniyordu, dates={len(dates)}, values={len(values)}"
            )
        blocks.append({"dates": dates, "values": values, "label": label})
    return blocks


def classify_housing_metric(label: str) -> str | None:
    if "Konut Kredisi" in label and "Milyar TL" in label:
        return "housing_credit_balance_billion_try"
    if (
        "Milyon Kişi" in label
        and ("Konut Kredisi Kişi Sayısı" in label or label.startswith("Kişi Sayısı"))
    ):
        return "housing_credit_borrower_count_million_person"
    if "Tasfiye Olunacak" in label and "Konut Kredileri" in label and "(%)" in label:
        return "housing_credit_npl_ratio_pct"
    if "Kişi Başına Ortalama Konut Kredisi" in label or label.startswith(
        "Kişi Başına Ortalama Tutar"
    ):
        return "housing_credit_average_balance_try"
    return None


def parse_turkish_number(raw: str, metric_code: str) -> float:
    if metric_code == "housing_credit_average_balance_try":
        return float(raw.replace(".", "").replace(",", "."))
    return float(raw.replace(",", "."))


def display_precision(raw: str, metric_code: str) -> int:
    if metric_code == "housing_credit_average_balance_try":
        return 0
    return len(raw.rsplit(",", 1)[1]) if "," in raw else 0


def select_page(
    page_texts: list[str], predicate: Callable[[str, list[dict[str, Any]]], bool]
) -> tuple[int, list[dict[str, Any]]]:
    candidates = []
    for page_number, text in enumerate(page_texts, start=1):
        blocks = chart_blocks(text)
        if predicate(text, blocks):
            candidates.append((page_number, blocks))
    if len(candidates) != 1:
        raise ValueError(f"Tek kaynak sayfası bekleniyordu, bulunan={len(candidates)}")
    return candidates[0]


def extract_publication(
    input_dir: Path, publication: dict[str, Any]
) -> list[dict[str, Any]]:
    publication_month = str(publication["publication_period"])
    pdf_path = input_dir / str(publication["local_file"])
    expected_sha256 = str(publication["fetch"]["sha256"])
    actual_sha256 = sha256(pdf_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Risk Merkezi PDF hash uyuşmazlığı: {pdf_path}, "
            f"expected={expected_sha256}, actual={actual_sha256}"
        )
    reader = PdfReader(pdf_path)
    page_texts = [(page.extract_text() or "").replace("\u00a0", " ") for page in reader.pages]

    housing_page, housing_blocks = select_page(
        page_texts,
        lambda _text, blocks: any(
            "Konut Kredisi" in block["label"] and "Milyar TL" in block["label"]
            for block in blocks
        ),
    )
    newcomer_page, newcomer_blocks = select_page(
        page_texts,
        lambda text, blocks: (
            "Finansal Sisteme Yeni Giren Kişi Sayısı" in text
            and any(
                "Konut Kredisi" in block["label"]
                and "Bin Kişi" in block["label"]
                for block in blocks
            )
        ),
    )

    selected: list[tuple[int, str, dict[str, Any]]] = []
    for block in housing_blocks:
        metric_code = classify_housing_metric(str(block["label"]))
        if metric_code:
            selected.append((housing_page, metric_code, block))
    for block in newcomer_blocks:
        label = str(block["label"])
        if "Konut Kredisi" in label and "Bin Kişi" in label:
            selected.append(
                (
                    newcomer_page,
                    "first_time_housing_credit_users_thousand_person",
                    block,
                )
            )

    found_metrics = {metric_code for _, metric_code, _ in selected}
    if found_metrics != set(METRIC_SPECS):
        raise ValueError(
            f"{publication_month} bülteninde metrik seti eksik: {found_metrics}"
        )

    rows = []
    for page_number, metric_code, block in selected:
        for month, raw_value in zip(block["dates"], block["values"]):
            rows.append(
                {
                    "publication_month": publication_month,
                    "observation_month": month,
                    "metric_code": metric_code,
                    "value": parse_turkish_number(raw_value, metric_code),
                    "source_value_raw": raw_value,
                    "source_display_precision": display_precision(
                        raw_value, metric_code
                    ),
                    "source_chart_label": block["label"],
                    "source_page_number": page_number,
                    "source_file": pdf_path.relative_to(PROJECT_ROOT).as_posix(),
                    "source_sha256": actual_sha256,
                    "source_url": publication["source_url"],
                }
            )
    return rows


def build(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest_path = input_dir / "manifest.json"
    manifest = read_json(manifest_path)
    publications = list(manifest["publications"])
    if manifest.get("status") != "complete" or len(publications) != 6:
        raise ValueError("Altı yıllık Risk Merkezi Haziran bülteni snapshot'ı eksik.")

    vintage_rows = []
    for publication in publications:
        vintage_rows.extend(extract_publication(input_dir, publication))
    vintages = pd.DataFrame(vintage_rows).sort_values(
        ["observation_month", "metric_code", "publication_month"],
        kind="stable",
    )
    duplicate_vintage_keys = int(
        vintages.duplicated(
            ["publication_month", "observation_month", "metric_code"]
        ).sum()
    )
    if duplicate_vintage_keys:
        raise ValueError(f"Tekrarlanan bülten gözlemleri var: {duplicate_vintage_keys}")

    overlap_rows = []
    for (month, metric_code), rows in vintages.groupby(
        ["observation_month", "metric_code"], sort=True
    ):
        if len(rows) < 2:
            continue
        ordered = rows.sort_values("publication_month", kind="stable")
        earliest = ordered.iloc[0]
        latest = ordered.iloc[-1]
        overlap_rows.append(
            {
                "observation_month": month,
                "metric_code": metric_code,
                "earliest_publication_month": earliest["publication_month"],
                "latest_publication_month": latest["publication_month"],
                "earliest_value": float(earliest["value"]),
                "latest_value": float(latest["value"]),
                "difference": float(latest["value"] - earliest["value"]),
                "value_changed": bool(latest["value"] != earliest["value"]),
                "earliest_source_sha256": earliest["source_sha256"],
                "latest_source_sha256": latest["source_sha256"],
            }
        )
    overlap = pd.DataFrame(overlap_rows)

    latest = (
        vintages.sort_values("publication_month", kind="stable")
        .drop_duplicates(["observation_month", "metric_code"], keep="last")
        .sort_values(["observation_month", "metric_code"], kind="stable")
        .reset_index(drop=True)
    )
    latest["is_target_period"] = latest["observation_month"].between(
        TARGET_START, TARGET_END
    )
    latest["selected_vintage_policy"] = "latest_official_publication"

    target = latest.loc[latest["is_target_period"]].copy()
    panel = (
        target.pivot(
            index="observation_month", columns="metric_code", values="value"
        )
        .reset_index()
        .rename(columns={"observation_month": "month"})
    )
    source_publications = (
        target.groupby("observation_month")["publication_month"]
        .agg(lambda values: " | ".join(sorted(set(values))))
        .rename("selected_source_publication_month")
        .reset_index()
        .rename(columns={"observation_month": "month"})
    )
    panel = panel.merge(source_publications, on="month", how="left", validate="one_to_one")
    changed_months = set(overlap.loc[overlap["value_changed"], "observation_month"])
    panel["has_source_revision"] = panel["month"].isin(changed_months)
    panel["housing_credit_balance_million_try_from_rounded_chart"] = (
        panel["housing_credit_balance_billion_try"] * 1000
    )
    panel = panel.sort_values("month", kind="stable").reset_index(drop=True)

    dictionary = pd.DataFrame(
        [
            {
                "metric_code": metric_code,
                **spec,
                "source_system": "TBB_RISK_CENTER",
                "source_organization": "Türkiye Bankalar Birliği Risk Merkezi",
                "native_frequency": "monthly",
                "source_value_is_rounded_chart_label": True,
            }
            for metric_code, spec in METRIC_SPECS.items()
        ]
    )

    expected_months = pd.period_range(TARGET_START, TARGET_END, freq="M").astype(str)
    missing_months = sorted(set(expected_months) - set(panel["month"]))
    metric_columns = list(METRIC_SPECS)
    missing_target_values = int(panel[metric_columns].isna().sum().sum())
    validation = {
        "status": "passed",
        "publication_count": len(publications),
        "publication_periods": [item["publication_period"] for item in publications],
        "source_coverage_start": str(latest["observation_month"].min()),
        "source_coverage_end": str(latest["observation_month"].max()),
        "target_coverage_start": TARGET_START,
        "target_coverage_end": TARGET_END,
        "vintage_observation_rows": len(vintages),
        "latest_observation_rows": len(latest),
        "monthly_panel_rows": len(panel),
        "metric_count": len(METRIC_SPECS),
        "duplicate_vintage_keys": duplicate_vintage_keys,
        "missing_target_months": missing_months,
        "missing_target_metric_values": missing_target_values,
        "overlap_metric_months": len(overlap),
        "overlap_value_revisions": int(overlap["value_changed"].sum()),
        "latest_vintage_policy": "latest_official_publication",
        "quality_policy": [
            "All six source PDFs are hash-verified before extraction.",
            "Every source chart vintage is retained before selecting the latest official publication.",
            "Overlapping June observations are compared and revisions remain explicit.",
            "Published chart rounding is preserved and no interpolation is applied.",
            "First-time housing-credit user counts are people, not monetary disbursement amounts.",
            "Risk Center balances are a separate reporting scope and do not overwrite BDDK, EVDS or TBB values.",
        ],
    }
    if missing_months or missing_target_values or len(panel) != 66:
        validation["status"] = "failed"
        raise ValueError(f"Risk Merkezi aylık panel doğrulaması geçmedi: {validation}")

    write_frame(vintages, output_dir, "housing_metric_vintages")
    write_frame(latest, output_dir, "housing_metrics_latest")
    write_frame(overlap, output_dir, "overlap_revision_audit")
    write_frame(panel, output_dir, "housing_credit_monthly")
    write_frame(dictionary, output_dir, "metric_dictionary")
    atomic_json(output_dir / "validation.json", validation)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.input.expanduser().resolve(), args.output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
