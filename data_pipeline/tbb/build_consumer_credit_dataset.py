#!/usr/bin/env python3
"""Normalize TBB quarterly consumer and housing credit workbooks.

The source workbooks contain several tables on one worksheet. This builder
preserves every non-empty workbook cell and derives a narrowly defined table
for product-level disbursement and balance measures. Disbursement flow and
outstanding balance are deliberately kept as different measure types.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import numbers
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "consumer_credit_reports"
DEFAULT_OUTPUT = BASE_DIR / "processed"

TURKISH_MONTHS = {
    "ocak": 1,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "agustos": 8,
    "eylul": 9,
    "ekim": 10,
    "kasim": 11,
    "aralik": 12,
}
PRODUCT_ORDER = ["vehicle", "housing", "need", "other", "total"]
PRODUCT_LABELS = {
    "tasit": "vehicle",
    "konut": "housing",
    "ihtiyac": "need",
    "diger": "other",
    "toplam": "total",
}
CURRENCY_LABELS = {
    "tp": "TRY",
    "yp": "FX-linked",
    "toplam": "Total",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    translation = str.maketrans(
        {
            "ı": "i",
            "İ": "I",
            "ş": "s",
            "Ş": "S",
            "ğ": "g",
            "Ğ": "G",
            "ü": "u",
            "Ü": "U",
            "ö": "o",
            "Ö": "O",
            "ç": "c",
            "Ç": "C",
        }
    )
    normalized = unicodedata.normalize("NFKD", str(value).translate(translation))
    return " ".join(normalized.encode("ascii", "ignore").decode("ascii").split()).casefold()


def canonical_product(value: Any) -> str | None:
    normalized = normalize_text(value).strip(" *")
    for prefix, product in PRODUCT_LABELS.items():
        if normalized.startswith(prefix):
            return product
    return None


def find_row(frame: pd.DataFrame, phrase: str, start: int = 0) -> int:
    target = normalize_text(phrase)
    for row_index in range(start, len(frame)):
        values = [normalize_text(value) for value in frame.iloc[row_index].tolist()]
        if any(target in value for value in values):
            return row_index
    raise ValueError(f"TBB çalışma sayfasında başlık bulunamadı: {phrase}")


def header_sequences(frame: pd.DataFrame, start: int) -> tuple[int, list[int], list[int]]:
    for row_index in range(start, min(start + 5, len(frame))):
        entries = [
            (column_index, canonical_product(value))
            for column_index, value in enumerate(frame.iloc[row_index].tolist())
        ]
        entries = [(index, value) for index, value in entries if value is not None]
        sequences: list[list[int]] = []
        for offset in range(len(entries) - len(PRODUCT_ORDER) + 1):
            candidate = entries[offset : offset + len(PRODUCT_ORDER)]
            if [item[1] for item in candidate] == PRODUCT_ORDER:
                sequences.append([item[0] for item in candidate])
        if len(sequences) >= 2:
            return row_index, sequences[0], sequences[1]
    raise ValueError("TBB ürün başlıkları için iki ölçü dizisi bulunamadı.")


def number(value: Any, context: str) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError(f"TBB sayısal hücresi boş: {context}")
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)
    cleaned = str(value).strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"TBB sayısal hücresi okunamadı: {context}={value!r}") from exc


def parse_period_groups(
    frame: pd.DataFrame,
    data_start: int,
    data_end: int,
    amount_columns: list[int],
    count_columns: list[int],
    amount_measure: str,
    count_measure: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    row_index = data_start
    while row_index + 2 < data_end:
        currency_sequence = [
            normalize_text(frame.iat[row_index + offset, 1]) for offset in range(3)
        ]
        if currency_sequence != ["tp", "yp", "toplam"]:
            row_index += 1
            continue
        year_value = frame.iat[row_index, 0]
        if not isinstance(year_value, numbers.Real):
            raise ValueError(f"TBB dönem yılı okunamadı, satır {row_index}: {year_value!r}")
        year = int(year_value)
        month_name = normalize_text(frame.iat[row_index + 1, 0])
        if month_name not in TURKISH_MONTHS:
            raise ValueError(
                f"TBB dönem ayı okunamadı, satır {row_index + 1}: {month_name!r}"
            )
        period = f"{year:04d}-{TURKISH_MONTHS[month_name]:02d}"

        for offset, source_currency in enumerate(currency_sequence):
            source_row = row_index + offset
            base = {
                "quarter": period,
                "source_currency_group": frame.iat[source_row, 1],
                "currency_group": CURRENCY_LABELS[source_currency],
                "source_row_index": source_row,
            }
            amount_values = {
                product: number(
                    frame.iat[source_row, column],
                    f"{period} {source_currency} {amount_measure} {product}",
                )
                for product, column in zip(PRODUCT_ORDER, amount_columns)
            }
            count_values = {
                product: number(
                    frame.iat[source_row, column],
                    f"{period} {source_currency} {count_measure} {product}",
                )
                for product, column in zip(PRODUCT_ORDER, count_columns)
            }
            result.append({**base, "measure": amount_measure, **amount_values})
            result.append({**base, "measure": count_measure, **count_values})
        row_index += 3
    if not result:
        raise ValueError(f"TBB ölçü bloğunda dönem satırı bulunamadı: {amount_measure}")
    return result


def parse_credit_sheet(frame: pd.DataFrame) -> list[dict[str, Any]]:
    distribution_row = find_row(frame, "Mal ve hizmet gruplarına göre dağılım")
    disbursement_heading = find_row(
        frame, "Kullandırılan Miktar", start=distribution_row
    )
    balance_heading = find_row(frame, "Bakiye Miktar", start=disbursement_heading + 1)

    disbursement_headers, disbursement_amount, disbursement_count = header_sequences(
        frame, disbursement_heading + 1
    )
    disbursement_period_row = find_row(
        frame, "Dönem", start=disbursement_headers + 1
    )
    balance_headers, balance_amount, balance_count = header_sequences(
        frame, balance_heading + 1
    )
    balance_period_row = find_row(frame, "Dönem", start=balance_headers + 1)

    rows = parse_period_groups(
        frame,
        disbursement_period_row + 1,
        balance_heading,
        disbursement_amount,
        disbursement_count,
        "disbursement_amount_million_try",
        "disbursement_person_count",
    )
    rows.extend(
        parse_period_groups(
            frame,
            balance_period_row + 1,
            len(frame),
            balance_amount,
            balance_count,
            "balance_amount_million_try",
            "balance_person_count",
        )
    )
    return rows


def find_sheet(sheet_names: list[str], required_words: tuple[str, ...]) -> str:
    for sheet_name in sheet_names:
        normalized = normalize_text(sheet_name)
        if all(word in normalized for word in required_words):
            return sheet_name
    raise ValueError(f"TBB çalışma sayfası bulunamadı: {required_words}")


def reporting_banks(frame: pd.DataFrame) -> list[dict[str, Any]]:
    result = []
    for row_index, row in frame.iterrows():
        if len(row) < 2 or pd.isna(row.iloc[0]) or pd.isna(row.iloc[1]):
            continue
        sequence = row.iloc[0]
        if not isinstance(sequence, numbers.Real):
            continue
        name = str(row.iloc[1]).strip()
        if not name:
            continue
        result.append(
            {
                "source_row_index": int(row_index),
                "source_sequence": int(sequence),
                "bank_name": name,
            }
        )
    if not result:
        raise ValueError("TBB raporlayan banka listesi boş.")
    if len({item["source_sequence"] for item in result}) != len(result):
        raise ValueError("TBB raporlayan banka sıra numarası tekrarlanıyor.")
    return result


def nonempty_cells(
    frame: pd.DataFrame, period: str, sheet_name: str, source_file: str
) -> list[dict[str, Any]]:
    rows = []
    for row_index in range(len(frame)):
        for column_index in range(len(frame.columns)):
            value = frame.iat[row_index, column_index]
            if pd.isna(value):
                continue
            if isinstance(value, pd.Timestamp):
                value_type = "datetime"
                value_text = value.isoformat()
                value_numeric = None
            elif isinstance(value, numbers.Real) and not isinstance(value, bool):
                value_type = "number"
                value_text = str(value)
                value_numeric = float(value)
            else:
                value_type = "text"
                value_text = str(value)
                value_numeric = None
            rows.append(
                {
                    "report_period": period,
                    "sheet_name": sheet_name,
                    "row_index": row_index,
                    "column_index": column_index,
                    "value_type": value_type,
                    "value_text": value_text,
                    "value_numeric": value_numeric,
                    "source_file": source_file,
                }
            )
    return rows


def validate_product_totals(frame: pd.DataFrame) -> None:
    component_sum = frame[["vehicle", "housing", "need", "other"]].sum(axis=1)
    differences = (component_sum - frame["total"]).abs()
    amount_rows = frame["measure"].str.contains("amount")
    tolerances = amount_rows.map({True: 0.01, False: 0.001})
    invalid = differences > tolerances
    if invalid.any():
        example = frame.loc[invalid].iloc[0]
        raise ValueError(
            "TBB ürün toplamı bileşenlerle uyuşmuyor: "
            f"{example['quarter']} {example['currency_group']} {example['measure']}"
        )


def validate_currency_totals(frame: pd.DataFrame) -> None:
    products = PRODUCT_ORDER
    for (quarter, measure), group in frame.groupby(["quarter", "measure"]):
        indexed = group.set_index("currency_group")
        if set(indexed.index) != {"TRY", "FX-linked", "Total"}:
            raise ValueError(f"TBB para grubu eksik: {quarter} {measure}")
        expected = indexed.loc["TRY", products] + indexed.loc["FX-linked", products]
        actual = indexed.loc["Total", products]
        tolerance = 0.01 if "amount" in measure else 0.001
        if ((expected - actual).abs() > tolerance).any():
            raise ValueError(f"TBB TP + YP toplamı uyuşmuyor: {quarter} {measure}")


def build(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    summary_path = input_dir / "summary.json"
    manifest_path = input_dir / "manifest.json"
    if not summary_path.exists() or not manifest_path.exists():
        raise ValueError("TBB indirme özeti veya manifesti bulunamadı.")
    download_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if download_summary.get("status") not in {"complete", "complete_with_source_gaps"}:
        raise ValueError("Tamamlanmamış TBB indirme klasörü işlenemez.")

    derived_rows: list[dict[str, Any]] = []
    bank_rows: list[dict[str, Any]] = []
    cell_rows: list[dict[str, Any]] = []
    workbook_rows: list[dict[str, Any]] = []
    published = [item for item in manifest if item.get("status") == "validated"]
    for item in published:
        period = item["period"]
        attachments = item.get("attachments", {})
        excel_key = "xls" if "xls" in attachments else "xlsx"
        attachment = attachments.get(excel_key)
        if not attachment:
            raise ValueError(f"TBB Excel manifest kaydı yok: {period}")
        workbook_path = input_dir / attachment["local_file"]
        if not workbook_path.exists():
            raise ValueError(f"TBB Excel dosyası yok: {workbook_path}")
        source_hash = sha256(workbook_path)
        if source_hash != attachment["sha256"]:
            raise ValueError(f"TBB Excel hash uyuşmuyor: {period}")

        engine = "xlrd" if workbook_path.suffix.casefold() == ".xls" else "openpyxl"
        workbook = pd.ExcelFile(workbook_path, engine=engine)
        credit_sheet = find_sheet(workbook.sheet_names, ("kredi",))
        bank_sheet = find_sheet(workbook.sheet_names, ("banka", "listesi"))
        credit_frame = pd.read_excel(
            workbook_path, sheet_name=credit_sheet, header=None, engine=engine
        )
        bank_frame = pd.read_excel(
            workbook_path, sheet_name=bank_sheet, header=None, engine=engine
        )

        parsed = pd.DataFrame(parse_credit_sheet(credit_frame))
        target = parsed.loc[parsed["quarter"].eq(period)].copy()
        expected_measures = {
            "disbursement_amount_million_try",
            "disbursement_person_count",
            "balance_amount_million_try",
            "balance_person_count",
        }
        if len(target) != 12 or set(target["measure"]) != expected_measures:
            raise ValueError(
                f"TBB hedef dönem ölçüleri eksik: {period}, satır={len(target)}"
            )

        source_file = str(workbook_path.relative_to(input_dir))
        target["report_period"] = period
        target["source_report_url"] = item["report_url"]
        target["source_file"] = source_file
        target["source_sha256"] = source_hash
        target["native_frequency"] = "quarterly"
        target["flow_stock_semantics"] = target["measure"].map(
            lambda value: "flow" if value.startswith("disbursement") else "stock"
        )
        derived_rows.extend(target.to_dict(orient="records"))

        banks = reporting_banks(bank_frame)
        for bank in banks:
            bank_rows.append(
                {
                    "report_period": period,
                    **bank,
                    "source_report_url": item["report_url"],
                    "source_file": source_file,
                    "source_sha256": source_hash,
                }
            )

        for sheet_name in workbook.sheet_names:
            source_frame = pd.read_excel(
                workbook_path, sheet_name=sheet_name, header=None, engine=engine
            )
            cell_rows.extend(nonempty_cells(source_frame, period, sheet_name, source_file))

        workbook_rows.append(
            {
                "report_period": period,
                "report_url": item["report_url"],
                "source_file": source_file,
                "source_sha256": source_hash,
                "source_bytes": workbook_path.stat().st_size,
                "sheet_names_json": json.dumps(workbook.sheet_names, ensure_ascii=False),
                "credit_sheet_rows": len(credit_frame),
                "credit_sheet_columns": len(credit_frame.columns),
                "reporting_bank_count": len(banks),
            }
        )

    derived = pd.DataFrame(derived_rows).sort_values(
        ["quarter", "measure", "currency_group"], kind="stable"
    )
    validate_product_totals(derived)
    validate_currency_totals(derived)
    if derived.duplicated(["quarter", "measure", "currency_group"]).any():
        raise ValueError("TBB türetilmiş veri anahtarı tekrarlanıyor.")

    bank_frame = pd.DataFrame(bank_rows).sort_values(
        ["report_period", "source_sequence"], kind="stable"
    )
    cell_frame = pd.DataFrame(cell_rows).sort_values(
        ["report_period", "sheet_name", "row_index", "column_index"], kind="stable"
    )
    workbook_frame = pd.DataFrame(workbook_rows).sort_values("report_period")

    total_rows = derived.loc[derived["currency_group"].eq("Total")]
    housing = total_rows.pivot(
        index="quarter", columns="measure", values="housing"
    ).reset_index()
    housing = housing.merge(
        workbook_frame[["report_period", "reporting_bank_count", "report_url", "source_file", "source_sha256"]],
        left_on="quarter",
        right_on="report_period",
        validate="one_to_one",
    ).drop(columns="report_period")
    housing["native_frequency"] = "quarterly"
    housing["is_monthly_disbursement"] = False
    housing["source_scope"] = "Banks listed in each TBB source workbook"

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "consumer_credit_product_metrics": derived,
        "housing_credit_quarterly": housing,
        "reporting_banks": bank_frame,
        "workbook_catalog": workbook_frame,
        "workbook_cells_long": cell_frame,
    }
    for name, frame in outputs.items():
        frame.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
        frame.to_parquet(output_dir / f"{name}.parquet", index=False)

    expected_periods = download_summary["expected_periods"]
    source_gap_periods = download_summary.get("source_gap_periods", [])
    result = {
        "status": "passed_with_source_gaps" if source_gap_periods else "passed",
        "expected_periods": expected_periods,
        "parsed_workbooks": len(workbook_frame),
        "parsed_periods": workbook_frame["report_period"].tolist(),
        "source_gap_periods": source_gap_periods,
        "derived_metric_rows": len(derived),
        "housing_quarter_rows": len(housing),
        "reporting_bank_rows": len(bank_frame),
        "preserved_nonempty_workbook_cells": len(cell_frame),
        "duplicate_metric_keys": int(
            derived.duplicated(["quarter", "measure", "currency_group"]).sum()
        ),
        "quality_policy": [
            "Quarterly disbursement flow and end-period balance are separate measures.",
            "Only the target quarter from each source workbook is used in the derived table.",
            "Product totals and TP plus YP currency totals are validated.",
            "Every non-empty workbook cell is preserved in a lossless long-form table.",
            "Missing source publications are recorded and are not imputed.",
        ],
    }
    (output_dir / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


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
