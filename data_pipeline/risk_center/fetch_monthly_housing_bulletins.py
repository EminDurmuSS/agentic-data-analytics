#!/usr/bin/env python3
"""Download official Risk Center June bulletins used for monthly housing data.

Each June bulletin contains a 13-month chart window. The June bulletins from
2021 through 2026 therefore cover June 2020 through June 2026 with one-month
overlaps between adjacent publications. The overlaps are retained so later
revisions can be detected by the offline builder.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.riskmerkezi.org"
LIST_URL = f"{BASE_URL}/istatistiki-raporlar-liste/2541"
HEADERS = {"User-Agent": "KKB-Agentic-Data-Analytics/1.0"}
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "monthly_housing_v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def normalize_text(value: str) -> str:
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
    normalized = unicodedata.normalize("NFKD", value.translate(translation))
    return " ".join(normalized.encode("ascii", "ignore").decode("ascii").split()).casefold()


def discover_june_bulletins(html: str, base_url: str = BASE_URL) -> dict[int, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[int, dict[str, str]] = {}
    for item in soup.select("#accordion-istatistiki-rapor .item"):
        heading = item.select_one("a.node-title")
        if heading is None:
            continue
        title = " ".join(heading.stripped_strings)
        match = re.fullmatch(
            r"(\d{4})\s+Haziran\s+-\s+Risk Merkezi Aylık Bülteni",
            title,
        )
        if not match:
            continue
        year = int(match.group(1))
        candidates = []
        for anchor in item.select("a[download][href]"):
            attachment_title = str(
                anchor.get("title") or anchor.get_text(" ", strip=True)
            )
            content_type = str(anchor.get("type") or "")
            if "pdf" not in content_type.casefold():
                continue
            if "ozet" in normalize_text(attachment_title):
                continue
            candidates.append(
                {
                    "url": urljoin(base_url, str(anchor.get("href"))),
                    "title": attachment_title,
                    "report_title": title,
                }
            )
        if not candidates:
            continue
        if len(candidates) != 1:
            raise ValueError(
                f"{year} Haziran için tek tam bülten bekleniyordu, bulunan={candidates}"
            )
        result[year] = candidates[0]
    if not result:
        raise ValueError("Risk Merkezi Haziran bültenleri listede bulunamadı.")
    return result


def get_with_retries(
    session: requests.Session, url: str, timeout: int, retries: int
) -> tuple[bytes, dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        started = utc_now()
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.content
            return data, {
                "started_at_utc": started,
                "completed_at_utc": utc_now(),
                "requested_url": url,
                "final_url": response.url,
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
                "bytes": len(data),
                "sha256": sha256(data),
                "attempt": attempt,
                "tls_verification": True,
            }
        except Exception as exc:  # noqa: BLE001 - bounded retry and persisted failure
            last_error = exc
            if attempt <= retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(str(last_error))


def validate_pdf(data: bytes, content_type: str) -> None:
    if len(data) < 10_000:
        raise ValueError(f"Risk Merkezi PDF dosyası beklenenden küçük: {len(data)} bayt")
    if not data.startswith(b"%PDF"):
        raise ValueError("Risk Merkezi eki PDF imzasına sahip değil.")
    if "html" in content_type.casefold():
        raise ValueError("Risk Merkezi PDF eki yerine HTML döndü.")


def run(args: argparse.Namespace) -> int:
    if args.start_year > args.end_year:
        raise ValueError("Başlangıç yılı bitiş yılından sonra olamaz.")
    if args.timeout <= 0 or args.retries < 0 or args.delay < 0:
        raise ValueError("Timeout pozitif, retry ve delay negatif olmayan sayı olmalı.")

    output = args.output.expanduser().resolve()
    session = requests.Session()
    session.headers.update(HEADERS)

    list_data, list_fetch = get_with_retries(
        session, LIST_URL, args.timeout, args.retries
    )
    list_html = list_data.decode("utf-8")
    discovered = discover_june_bulletins(list_html)
    target_years = list(range(args.start_year, args.end_year + 1))
    missing_years = [year for year in target_years if year not in discovered]
    if missing_years:
        raise ValueError(f"Kaynak listesinde Haziran bülteni bulunmayan yıllar: {missing_years}")

    atomic_bytes(output / "category_page.html.gz", gzip.compress(list_data, 9))
    records: list[dict[str, Any]] = []
    for year in target_years:
        period = f"{year:04d}-06"
        source = discovered[year]
        pdf_data, fetch = get_with_retries(
            session, source["url"], args.timeout, args.retries
        )
        validate_pdf(pdf_data, str(fetch["content_type"]))
        local_path = output / "raw" / period / "risk_center_monthly_bulletin.pdf"
        atomic_bytes(local_path, pdf_data)
        record = {
            "publication_period": period,
            "status": "validated",
            "report_title": source["report_title"],
            "attachment_title": source["title"],
            "source_url": source["url"],
            "local_file": local_path.relative_to(output).as_posix(),
            "fetch": fetch,
        }
        atomic_json(local_path.parent / "source_info.json", record)
        records.append(record)
        print(
            f"{period}: {fetch['bytes']} bayt, sha256={fetch['sha256']}",
            flush=True,
        )
        if args.delay:
            time.sleep(args.delay)

    manifest = {
        "status": "complete",
        "source": "Türkiye Bankalar Birliği Risk Merkezi",
        "official_listing_url": LIST_URL,
        "list_fetch": list_fetch,
        "selection": "June bulletins with 13-month rolling charts",
        "target_years": target_years,
        "publication_count": len(records),
        "publications": records,
    }
    atomic_json(output / "manifest.json", manifest)
    print(f"Saved {len(records)} official bulletins to {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
