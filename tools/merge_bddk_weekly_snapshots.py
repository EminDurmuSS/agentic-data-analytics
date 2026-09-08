#!/usr/bin/env python3
"""Merge validated BDDK weekly source pages into one destination snapshot.

Only raw HTML and its matching request-info record are merged. The destination
downloader must be rerun afterwards so it reparses every cached page and writes
one authoritative manifest and summary for the complete requested scope.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


CONFIG_KEYS = {
    "source_url",
    "start",
    "end",
    "period_count",
    "tables",
    "currency",
    "raw_format",
    "tls_verification",
}

INFO_IDENTITY_KEYS = {
    "status",
    "source_url",
    "sha256_uncompressed",
    "uncompressed_bytes",
    "table_id",
    "table_name",
    "group_code",
    "group_name",
    "period_id",
    "observation_date",
    "week_number",
    "source_period_label",
    "source_date_parsed",
    "headers",
    "rows",
    "max_cells_per_row",
    "unit_heading",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_compatible(destination: dict[str, Any], source: dict[str, Any]) -> None:
    mismatches = sorted(
        key for key in CONFIG_KEYS if destination.get(key) != source.get(key)
    )
    if mismatches:
        raise ValueError(f"Uyumsuz haftalik snapshot config alanlari: {mismatches}")

    destination_groups = {int(code) for code in destination["groups"]}
    source_groups = {int(code) for code in source["groups"]}
    if not source_groups <= destination_groups:
        raise ValueError(
            "Kaynak snapshot gruplari hedef kapsaminda degil: "
            f"{sorted(source_groups - destination_groups)}"
        )


def install_file(source: Path, destination: Path) -> str:
    if destination.exists():
        if source.read_bytes() != destination.read_bytes():
            raise ValueError(f"Hedefte farkli icerikli dosya var: {destination}")
        return "existing"
    try:
        os.link(source, destination)
        return "linked"
    except OSError:
        shutil.copy2(source, destination)
        return "copied"


def install_info(source: Path, destination: Path) -> str:
    if destination.exists():
        source_info = read_json(source)
        destination_info = read_json(destination)
        mismatches = sorted(
            key
            for key in INFO_IDENTITY_KEYS
            if source_info.get(key) != destination_info.get(key)
        )
        if mismatches:
            raise ValueError(
                f"Hedefte farkli kimlikli request-info var: {destination}, "
                f"alanlar={mismatches}"
            )
        return "existing"
    try:
        os.link(source, destination)
        return "linked"
    except OSError:
        shutil.copy2(source, destination)
        return "copied"


def merge(destination: Path, sources: list[Path]) -> dict[str, int]:
    destination_config = read_json(destination / "request_config.json")
    destination_raw = destination / "raw"
    destination_raw.mkdir(parents=True, exist_ok=True)

    counts = {"validated_pairs": 0, "linked": 0, "copied": 0, "existing": 0}
    for source in sources:
        source_config = read_json(source / "request_config.json")
        validate_compatible(destination_config, source_config)
        source_raw = source / "raw"
        for info_path in sorted(source_raw.glob("*_info.json")):
            info = read_json(info_path)
            if info.get("status") != "validated":
                continue
            raw_name = info_path.name.removesuffix("_info.json") + ".html.gz"
            raw_path = source_raw / raw_name
            if not raw_path.exists():
                raise ValueError(f"Dogrulanmis kaydin ham HTML dosyasi yok: {info_path}")
            uncompressed = gzip.decompress(raw_path.read_bytes())
            if sha256(uncompressed) != info.get("sha256_uncompressed"):
                raise ValueError(f"Kaynak haftalik hash'i uyusmuyor: {raw_path}")

            raw_result = install_file(raw_path, destination_raw / raw_path.name)
            info_result = install_info(info_path, destination_raw / info_path.name)
            if raw_result != info_result:
                raise ValueError(
                    "Ham HTML ve request-info birlestirme durumlari farkli: "
                    f"{raw_path.name}"
                )
            counts[raw_result] += 1
            counts["validated_pairs"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path, action="append")
    args = parser.parse_args()
    result = merge(
        args.destination.expanduser().resolve(),
        [path.expanduser().resolve() for path in args.source],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
