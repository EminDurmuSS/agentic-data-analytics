#!/usr/bin/env python3
"""Normalize and validate the complete BDDK FinTurk quarterly snapshot.

FinTurk exposes seven tables with different schemas. This builder preserves a
wide table per source table and also creates one generic long-form measurement
table for discovery and analytics. Every output value remains traceable to the
compressed source response, its validated request record and its SHA-256 hash.

Missing institution, city or metric combinations are kept missing. No value is
forward-filled, interpolated or inferred from another series.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from calendar import monthrange
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "finturk_all_groups_all_cities"
DEFAULT_OUTPUT = BASE_DIR / "processed" / "finturk_all_groups_all_cities"

TABLES = {
    1: "Krediler",
    2: "Mevduat",
    3: "Bireysel Bankacilik",
    4: "Secilmis Sektorel Krediler",
    5: "Oranlar",
    6: "Subeler ve Nufusa Gore Dagilim",
    7: "Altin Kredileri ve Altin Mevduati",
}

IDENTITY_COLUMNS = ["EftKodu", "Yil", "Ay", "Sehir", "Grup"]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_period(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(3|6|9|12)", value)
    if not match:
        raise ValueError(f"Gecersiz FinTurk donemi: {value!r}")
    return int(match.group(1)), int(match.group(2))


def expected_periods(start: str, end: str) -> list[str]:
    year, month = parse_period(start)
    end_year, end_month = parse_period(end)
    if (year, month) > (end_year, end_month):
        raise ValueError("FinTurk baslangic donemi bitis doneminden sonra.")
    result = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year}-{month}")
        if month == 12:
            year, month = year + 1, 3
        else:
            month += 3
    return result


def source_unit(table_no: int, measure_code: str) -> tuple[str, str]:
    """Return a query-friendly unit and the official UI unit description."""
    if table_no in {1, 2, 3, 4, 7}:
        return "thousand_try", "Bin TL"
    if table_no == 5:
        return "percent", "%"
    if table_no == 6:
        if measure_code == "SubeSayisi":
            return "count", "Adet"
        if measure_code == "SubeyeDusenNufus":
            return "people_per_branch", "Kisi / sube"
        return "try_per_person", "TL / kisi"
    return "source_native", "Kaynak birimi"


def value_semantics(table_no: int, measure_code: str) -> str:
    if table_no == 5:
        return "ratio"
    if table_no == 6 and measure_code == "SubeSayisi":
        return "count"
    if table_no == 6:
        return "per_capita_or_density"
    return "period_end_stock"


def numeric_value(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"FinTurk boolean sayisal deger olamaz: {context}")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace(".", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError as exc:
        raise ValueError(f"FinTurk sayisal degeri okunamadi: {context}={value!r}") from exc


def load_source(
    raw_path: Path,
    expected_period: str,
    expected_table: int,
    requested_groups: set[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    info_path = raw_path.with_name(raw_path.name.removesuffix(".json.gz") + "_info.json")
    if not info_path.exists():
        raise ValueError(f"FinTurk istek bilgi dosyasi yok: {info_path}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    compressed = raw_path.read_bytes()
    raw = gzip.decompress(compressed)
    if info.get("status") != "validated":
        raise ValueError(f"FinTurk kaynak kaydi dogrulanmamis: {info_path}")
    if info.get("sha256") != sha256_bytes(raw):
        raise ValueError(f"FinTurk kaynak hash'i uyusmuyor: {raw_path}")
    if info.get("period") != expected_period or int(info.get("table_no")) != expected_table:
        raise ValueError(f"FinTurk dosya adi ve istek kaydi uyusmuyor: {raw_path}")

    outer = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(outer, dict) or outer.get("success") is not True:
        raise ValueError(f"FinTurk success=true cevabi yok: {raw_path}")
    payload = outer.get("Json")
    if not isinstance(payload, dict):
        raise ValueError(f"FinTurk veri zarfi bulunamadi: {raw_path}")
    models = payload.get("colModels")
    labels = payload.get("colNames")
    envelope = payload.get("data")
    source_rows = envelope.get("rows") if isinstance(envelope, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError(f"FinTurk kolon modeli bos: {raw_path}")
    if not isinstance(labels, list) or len(labels) != len(models):
        raise ValueError(f"FinTurk kolon etiketi/modeli uyusmuyor: {raw_path}")
    columns = [model.get("name") if isinstance(model, dict) else None for model in models]
    if not all(isinstance(column, str) and column for column in columns):
        raise ValueError(f"FinTurk kolon kimligi bos: {raw_path}")
    if len(columns) != len(set(columns)):
        raise ValueError(f"FinTurk kolon kimligi tekrarlaniyor: {raw_path}")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError(f"FinTurk veri satirlari bos: {raw_path}")
    if any(column not in columns for column in IDENTITY_COLUMNS):
        raise ValueError(f"FinTurk kimlik kolonlari eksik: {raw_path}")

    year, month = parse_period(expected_period)
    source_file = str(Path("raw") / raw_path.name)
    source_info_file = str(Path("raw") / info_path.name)
    source_hash = sha256_bytes(raw)
    source_info_hash = sha256_path(info_path)
    parsed: list[dict[str, Any]] = []
    identities = []
    for row_index, source_row in enumerate(source_rows, start=1):
        cells = source_row.get("cell") if isinstance(source_row, dict) else None
        if not isinstance(cells, list) or len(cells) != len(columns):
            raise ValueError(f"FinTurk hucre sayisi kolonlarla uyusmuyor: {raw_path}")
        row = dict(zip(columns, cells))
        row_year = int(row["Yil"])
        row_month = int(row["Ay"])
        group_code = int(row["EftKodu"])
        if (row_year, row_month) != (year, month):
            raise ValueError(f"FinTurk kaynak donemi uyusmuyor: {raw_path}")
        if group_code not in requested_groups:
            raise ValueError(f"FinTurk beklenmeyen grup kodu: {raw_path}, {group_code}")
        identity = tuple(str(row[column]) for column in IDENTITY_COLUMNS)
        identities.append(identity)
        parsed.append(
            {
                "quarter": f"{year:04d}-{month:02d}",
                "observation_date": f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}",
                "native_frequency": "quarterly",
                "table_no": expected_table,
                "table_name": TABLES[expected_table],
                "group_code": group_code,
                "group_name": str(row["Grup"]),
                "city": str(row["Sehir"]),
                "source_row_index": row_index,
                "source_file": source_file,
                "source_sha256": source_hash,
                "source_request_info_file": source_info_file,
                "source_request_info_sha256": source_info_hash,
                **row,
            }
        )
    if len(identities) != len(set(identities)):
        raise ValueError(f"FinTurk kaynak kimligi tekrarlaniyor: {raw_path}")

    schema_payload = json.dumps(columns, ensure_ascii=False, separators=(",", ":"))
    metadata = {
        "quarter": f"{year:04d}-{month:02d}",
        "requested_period": expected_period,
        "table_no": expected_table,
        "table_name": TABLES[expected_table],
        "rows": len(parsed),
        "columns": columns,
        "column_labels": labels,
        "schema_sha256": hashlib.sha256(schema_payload.encode("utf-8")).hexdigest(),
        "source_file": source_file,
        "source_sha256": source_hash,
        "source_request_info_file": source_info_file,
        "source_request_info_sha256": source_info_hash,
        "source_warning": payload.get("uyari"),
    }
    return parsed, metadata


def assert_close(left: pd.Series, right: pd.Series, label: str) -> None:
    comparable = left.notna() & right.notna()
    differences = (left.loc[comparable] - right.loc[comparable]).abs()
    if (differences > 0.001).any():
        index = differences.idxmax()
        raise ValueError(
            f"FinTurk toplamsal kontrolu gecmedi: {label}, satir={index}, "
            f"fark={differences.loc[index]}"
        )


def validate_additive_identities(table_no: int, frame: pd.DataFrame) -> list[str]:
    checks: list[str] = []
    if table_no == 1:
        assert_close(
            frame["ToplamNakdiKrediler"],
            frame["NakdiKrediler"] + frame["TakiptekiAlacaklar"],
            "ToplamNakdiKrediler = NakdiKrediler + TakiptekiAlacaklar",
        )
        checks.append("ToplamNakdiKrediler = NakdiKrediler + TakiptekiAlacaklar")
    elif table_no == 2:
        assert_close(
            frame["TasarrufMevduati"],
            frame["TasarrufMevduatiTurkLirasi"]
            + frame["TasarrufMevduatiDovizTevdiatHesabi"],
            "TasarrufMevduati = TL + DTH",
        )
        assert_close(
            frame["DigerMevduat"],
            frame["DigerMevduatTurkLirasi"]
            + frame["DigerMevduatDovizTevdiatHesabi"],
            "DigerMevduat = TL + DTH",
        )
        assert_close(
            frame["ToplamMevduat"],
            frame["TasarrufMevduati"] + frame["DigerMevduat"],
            "ToplamMevduat = TasarrufMevduati + DigerMevduat",
        )
        checks.extend(
            [
                "TasarrufMevduati = TL + DTH",
                "DigerMevduat = TL + DTH",
                "ToplamMevduat = TasarrufMevduati + DigerMevduat",
            ]
        )
    elif table_no == 7:
        assert_close(
            frame["AltinDepoToplam"],
            frame["AltinDepoGercek"] + frame["AltinDepoTuzel"],
            "AltinDepoToplam = AltinDepoGercek + AltinDepoTuzel",
        )
        checks.append("AltinDepoToplam = AltinDepoGercek + AltinDepoTuzel")
    return checks


def build(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    config_path = input_dir / "request_config.json"
    summary_path = input_dir / "summary.json"
    manifest_path = input_dir / "manifest.json"
    if not all(path.exists() for path in [config_path, summary_path, manifest_path]):
        raise ValueError("FinTurk config, summary veya manifest dosyasi bulunamadi.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    download_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if download_summary.get("status") != "complete":
        raise ValueError("Tamamlanmamis FinTurk indirmesi islenemez.")

    periods = expected_periods(config["start"], config["end"])
    tables = sorted(int(value) for value in config["tables"])
    groups = {int(value) for value in config["groups"]}
    expected_files = {
        f"{period}_table{table_no:02}.json.gz"
        for period in periods
        for table_no in tables
    }
    actual_files = {path.name for path in (input_dir / "raw").glob("*.json.gz")}
    missing_files = sorted(expected_files - actual_files)
    unexpected_files = sorted(actual_files - expected_files)
    if missing_files or unexpected_files:
        raise ValueError(
            "FinTurk kaynak envanteri uyusmuyor: "
            f"missing={missing_files[:3]}, unexpected={unexpected_files[:3]}"
        )
    if len(manifest) != len(expected_files) or not all(
        item.get("status") == "validated" for item in manifest
    ):
        raise ValueError("FinTurk manifesti eksik veya dogrulanmamis kayit iceriyor.")

    rows_by_table: dict[int, list[dict[str, Any]]] = defaultdict(list)
    metadata: list[dict[str, Any]] = []
    for filename in sorted(expected_files):
        match = re.fullmatch(r"(.+)_table(\d{2})\.json\.gz", filename)
        if match is None:
            raise ValueError(f"Beklenmeyen FinTurk dosya adi: {filename}")
        period = match.group(1)
        table_no = int(match.group(2))
        rows, source_metadata = load_source(
            input_dir / "raw" / filename, period, table_no, groups
        )
        rows_by_table[table_no].extend(rows)
        metadata.append(source_metadata)

    output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[pd.DataFrame] = []
    dictionaries: list[dict[str, Any]] = []
    table_summaries: dict[str, Any] = {}
    additive_checks: list[str] = []
    provenance_columns = {
        "quarter",
        "observation_date",
        "native_frequency",
        "table_no",
        "table_name",
        "group_code",
        "group_name",
        "city",
        "source_row_index",
        "source_file",
        "source_sha256",
        "source_request_info_file",
        "source_request_info_sha256",
        *IDENTITY_COLUMNS,
    }

    for table_no, rows in sorted(rows_by_table.items()):
        frame = pd.DataFrame(rows)
        table_meta = [item for item in metadata if item["table_no"] == table_no]
        schemas = {item["schema_sha256"] for item in table_meta}
        if len(schemas) != 1:
            raise ValueError(f"FinTurk tablo semasi donemler arasinda degisti: {table_no}")
        source_columns = table_meta[0]["columns"]
        source_labels = table_meta[0]["column_labels"]
        label_by_code = dict(zip(source_columns, source_labels))
        measure_columns = [
            column for column in source_columns if column not in IDENTITY_COLUMNS
        ]
        for column in measure_columns:
            frame[column] = frame[column].map(
                lambda value, c=column: numeric_value(value, f"table={table_no} column={c}")
            )
        identity_key = ["quarter", "table_no", "group_code", "city"]
        if frame.duplicated(identity_key).any():
            raise ValueError(f"FinTurk islenmis kimligi tekrarlaniyor: tablo={table_no}")
        frame = frame.sort_values(identity_key, kind="stable").reset_index(drop=True)
        additive_checks.extend(validate_additive_identities(table_no, frame))

        frame.to_csv(
            output_dir / f"table_{table_no:02}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        frame.to_parquet(output_dir / f"table_{table_no:02}.parquet", index=False)

        id_columns = [column for column in frame.columns if column in provenance_columns]
        long_frame = frame.melt(
            id_vars=id_columns,
            value_vars=measure_columns,
            var_name="measure_code",
            value_name="value",
        )
        long_frame["measure_label"] = long_frame["measure_code"].map(label_by_code)
        long_frame["unit"] = long_frame["measure_code"].map(
            lambda value: source_unit(table_no, value)[0]
        )
        long_frame["source_unit_label"] = long_frame["measure_code"].map(
            lambda value: source_unit(table_no, value)[1]
        )
        long_frame["value_semantics"] = long_frame["measure_code"].map(
            lambda value: value_semantics(table_no, value)
        )
        long_frame["is_missing"] = long_frame["value"].isna()
        measurements.append(long_frame)

        for column in measure_columns:
            unit, unit_label = source_unit(table_no, column)
            dictionaries.append(
                {
                    "table_no": table_no,
                    "table_name": TABLES[table_no],
                    "measure_code": column,
                    "measure_label": label_by_code[column],
                    "unit": unit,
                    "source_unit_label": unit_label,
                    "value_semantics": value_semantics(table_no, column),
                    "native_frequency": "quarterly",
                    "source": "BDDK FinTurk",
                }
            )

        table_summaries[str(table_no)] = {
            "table_name": TABLES[table_no],
            "rows": len(frame),
            "measurement_rows": len(long_frame),
            "measure_count": len(measure_columns),
            "period_count": int(frame["quarter"].nunique()),
            "city_count": int(frame["city"].nunique()),
            "group_count": int(frame["group_code"].nunique()),
            "groups_present": sorted(int(value) for value in frame["group_code"].unique()),
            "missing_measurements": int(long_frame["is_missing"].sum()),
            "min_rows_per_source_table": min(item["rows"] for item in table_meta),
            "max_rows_per_source_table": max(item["rows"] for item in table_meta),
            "schema_sha256": next(iter(schemas)),
        }

    measurement_frame = pd.concat(measurements, ignore_index=True)
    measurement_key = [
        "quarter",
        "table_no",
        "group_code",
        "city",
        "measure_code",
    ]
    if measurement_frame.duplicated(measurement_key).any():
        raise ValueError("FinTurk uzun olcum anahtari tekrarlaniyor.")
    measurement_frame = measurement_frame.sort_values(
        measurement_key, kind="stable"
    ).reset_index(drop=True)
    measurement_frame.to_parquet(output_dir / "measurements_long.parquet", index=False)

    dictionary_frame = pd.DataFrame(dictionaries).sort_values(
        ["table_no", "measure_code"], kind="stable"
    )
    dictionary_frame.to_csv(
        output_dir / "column_dictionary.csv", index=False, encoding="utf-8-sig"
    )
    dictionary_frame.to_parquet(output_dir / "column_dictionary.parquet", index=False)

    catalog = pd.DataFrame(metadata).sort_values(
        ["quarter", "table_no"], kind="stable"
    )
    catalog["columns_json"] = catalog["columns"].map(
        lambda value: json.dumps(value, ensure_ascii=False)
    )
    catalog["column_labels_json"] = catalog["column_labels"].map(
        lambda value: json.dumps(value, ensure_ascii=False)
    )
    catalog = catalog.drop(columns=["columns", "column_labels"])
    catalog.to_csv(
        output_dir / "source_table_catalog.csv", index=False, encoding="utf-8-sig"
    )
    catalog.to_parquet(output_dir / "source_table_catalog.parquet", index=False)

    result = {
        "status": "passed",
        "source_download_status": download_summary["status"],
        "period_count": len(periods),
        "table_count": len(tables),
        "requested_group_count": len(groups),
        "source_file_count": len(expected_files),
        "source_row_count": sum(len(rows) for rows in rows_by_table.values()),
        "measurement_row_count": len(measurement_frame),
        "missing_measurement_count": int(measurement_frame["is_missing"].sum()),
        "column_dictionary_count": len(dictionary_frame),
        "tables": table_summaries,
        "additive_checks": additive_checks,
        "quality_policy": [
            "Every raw response is hash-checked against its validated request record.",
            "Different source table schemas remain separate in wide outputs.",
            "A generic long measurement table is produced without inventing missing rows.",
            "Missing values remain null and are never converted to zero.",
            "FinTurk values are quarterly period-end observations, not monthly flows.",
            "Source row counts, absent group-city combinations and schema changes are reported.",
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
