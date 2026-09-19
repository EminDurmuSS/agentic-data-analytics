#!/usr/bin/env python3
"""Download first-published TÜİK province housing-sales bulletin tables.

The mutable bulk dataflow exposes the latest revised history. Historical
questions also need the values published in the bulletin for each reference
month. This downloader keeps those two official vintages separate by storing
the exact bulletin JSON and the exact province table workbook for every month.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data_pipeline"
    / "tuik"
    / "province_housing_sales_first_published_v1"
)
BASE_URL = "https://veriportali.tuik.gov.tr"
TABLE_TITLE = "İllere Göre Konut Satış Sayıları"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/",
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
}

# The IDs are the official bulletin identities returned by the previousPresses
# chain of the December 2023 and December 2024 releases. Keeping this requested
# set explicit makes an acquisition retry reproducible even if the live search
# index later changes ordering.
PRESS_IDS = {
    "2023-01": 49519,
    "2023-02": 49515,
    "2023-03": 49517,
    "2023-04": 49520,
    "2023-05": 49525,
    "2023-06": 49521,
    "2023-07": 49522,
    "2023-08": 49523,
    "2023-09": 49518,
    "2023-10": 49514,
    "2023-11": 49524,
    "2023-12": 49516,
    "2024-01": 53766,
    "2024-02": 53757,
    "2024-03": 53767,
    "2024-04": 53758,
    "2024-05": 53759,
    "2024-06": 53760,
    "2024-07": 53761,
    "2024-08": 53768,
    "2024-09": 53762,
    "2024-10": 53763,
    "2024-11": 53764,
    "2024-12": 54146,
}

MONTH_NAMES = {
    "01": "Ocak",
    "02": "Şubat",
    "03": "Mart",
    "04": "Nisan",
    "05": "Mayıs",
    "06": "Haziran",
    "07": "Temmuz",
    "08": "Ağustos",
    "09": "Eylül",
    "10": "Ekim",
    "11": "Kasım",
    "12": "Aralık",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def request_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    attempts: int,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, headers=REQUEST_HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    raise last_error


def validate_workbook(data: bytes, period: str) -> None:
    if not data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError(f"{period} TÜİK tablosu OLE XLS imzasına sahip değil.")
    if len(data) < 20_000:
        raise ValueError(f"{period} TÜİK tablosu beklenenden küçük: {len(data)} bayt")
    frame = pd.read_excel(BytesIO(data), header=None)
    heading = str(frame.iloc[0, 0]) if not frame.empty else ""
    if "İllere göre konut satış sayıları" not in heading:
        raise ValueError(f"{period} dosyası beklenen il konut satış tablosu değil.")
    expected_year, expected_month = period.split("-")
    expected_label = f"{MONTH_NAMES[expected_month]} {expected_year}"
    if expected_label not in heading:
        raise ValueError(
            f"{period} dönemi çalışma sayfası başlığında doğrulanamadı: "
            f"{expected_label!r}"
        )


def run(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    selected = {
        period: press_id
        for period, press_id in PRESS_IDS.items()
        if args.start <= period <= args.end
    }
    if not selected:
        raise ValueError("İstenen aralıkta tanımlı TÜİK bülteni yok.")

    records: list[dict[str, Any]] = []
    started = utc_now()
    with requests.Session() as session:
        for period, press_id in selected.items():
            api_url = f"{BASE_URL}/api/tr/press/{press_id}"
            response = request_with_retry(
                session, api_url, timeout=args.timeout, attempts=args.attempts
            )
            payload = response.json()
            press = payload.get("data")
            if not isinstance(press, dict):
                raise ValueError(f"{period} bülten API yanıtında data nesnesi yok.")
            if int(press.get("id", -1)) != press_id:
                raise ValueError(f"{period} bülten kimliği eşleşmiyor.")
            if str(press.get("period", "")) == "":
                raise ValueError(f"{period} bülten dönemi boş.")
            tables = [
                item
                for item in press.get("tables", [])
                if item.get("title") == TABLE_TITLE and item.get("type") == "xls"
            ]
            if len(tables) != 1:
                raise ValueError(
                    f"{period} bülteninde tek bir {TABLE_TITLE!r} XLS tablosu bekleniyor."
                )
            download_url = BASE_URL + str(tables[0]["url"])
            workbook_response = None
            workbook = b""
            validation_error: Exception | None = None
            for attempt in range(1, args.attempts + 1):
                workbook_response = request_with_retry(
                    session, download_url, timeout=args.timeout, attempts=1
                )
                workbook = workbook_response.content
                try:
                    validate_workbook(workbook, period)
                    validation_error = None
                    break
                except (ValueError, OSError) as exc:
                    validation_error = exc
                    if attempt < args.attempts:
                        time.sleep(min(2 ** (attempt - 1), 4))
            if validation_error is not None:
                content_type = (
                    workbook_response.headers.get("Content-Type", "")
                    if workbook_response is not None
                    else ""
                )
                raise ValueError(
                    f"{period} çalışma kitabı {args.attempts} doğrulama "
                    f"denemesinde alınamadı; content_type={content_type!r}: "
                    f"{validation_error}"
                ) from validation_error
            assert workbook_response is not None

            directory = output / "raw" / period
            press_bytes = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            press_gzip = gzip.compress(press_bytes, compresslevel=9, mtime=0)
            press_path = directory / "press.json.gz"
            workbook_path = directory / "province_sales.xls"
            atomic_bytes(press_path, press_gzip)
            atomic_bytes(workbook_path, workbook)
            records.append(
                {
                    "period": period,
                    "press_id": press_id,
                    "press_title": press["title"],
                    "press_period": press["period"],
                    "release_at": press["date"],
                    "press_page_url": f"{BASE_URL}/tr/press/{press_id}",
                    "press_api_url": api_url,
                    "source_table_title": TABLE_TITLE,
                    "source_download_url": download_url,
                    "press_file": press_path.relative_to(PROJECT_ROOT).as_posix(),
                    "press_json_sha256": sha256(press_bytes),
                    "press_gzip_sha256": sha256(press_gzip),
                    "workbook_file": workbook_path.relative_to(PROJECT_ROOT).as_posix(),
                    "workbook_sha256": sha256(workbook),
                    "workbook_bytes": len(workbook),
                    "workbook_content_type": workbook_response.headers.get(
                        "Content-Type", ""
                    ),
                }
            )

    manifest = {
        "format_version": 1,
        "dataset_id": "tuik.province_housing_sales_first_published_v1",
        "source_organization": "Türkiye İstatistik Kurumu",
        "source_system": "TÜİK Veri Portalı haber bültenleri",
        "retrieved_at_utc": utc_now(),
        "retrieval_started_at_utc": started,
        "requested_start": min(selected),
        "requested_end": max(selected),
        "vintage_policy": "first_official_publication_for_each_reference_month",
        "records": records,
    }
    atomic_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start", default="2023-01")
    parser.add_argument("--end", default="2024-12")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--attempts", type=int, default=4)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
