#!/usr/bin/env python3
"""Download and validate TBB consumer and housing credit reports.

The public TBB report family publishes quarterly Excel, PDF and Word files.
This downloader discovers report links from the public category pages instead
of hard-coding attachment IDs. Every downloaded byte is hashed and retained as
source evidence. A period that is not published by TBB is recorded as a source
gap and is never replaced with an inferred value.
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
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.tbb.org.tr"
CATEGORY_PAGE = "/istatistiki-raporlar/11237"
CATEGORY_ID = 11265
HEADERS = {"User-Agent": "KKB-Agentic-Data-Analytics/1.0"}
SUPPORTED_FORMATS = {"xls", "xlsx", "pdf", "docx"}
REPORT_NAME = "Tüketici Kredileri ve Konut Kredileri"


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
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
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


def parse_quarter(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(03|06|09|12)", value)
    if not match:
        raise ValueError("Dönem YYYY-03, YYYY-06, YYYY-09 veya YYYY-12 olmalı.")
    return int(match.group(1)), int(match.group(2))


def quarters(start: str, end: str) -> list[str]:
    year, month = parse_quarter(start)
    last = parse_quarter(end)
    if (year, month) > last:
        raise ValueError("Başlangıç dönemi bitiş döneminden sonra.")
    result = []
    while (year, month) <= last:
        result.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 3
        else:
            month += 3
    return result


def discover_year_terms(html: str) -> dict[int, int]:
    soup = BeautifulSoup(html, "html.parser")
    target = normalize_text(REPORT_NAME)
    accordion = None
    for button in soup.select("button.accordion-button"):
        if target in normalize_text(button.get_text(" ", strip=True)):
            accordion = button.find_parent("div", class_="accordion-item")
            break
    if accordion is None:
        raise ValueError("TBB tüketici kredileri rapor ailesi bulunamadı.")
    select = accordion.select_one("select.rapor-donemi-secimi")
    if select is None:
        raise ValueError("TBB rapor yılı seçimi bulunamadı.")

    result: dict[int, int] = {}
    for option in select.find_all("option"):
        text = option.get_text(" ", strip=True)
        href = option.get("value", "")
        if not re.fullmatch(r"\d{4}", text) or not href:
            continue
        query = parse_qs(urlparse(href).query)
        values = query.get("rapor_donemi")
        if not values or not values[0].isdigit():
            raise ValueError(f"TBB yıl dönemi kimliği okunamadı: {href}")
        result[int(text)] = int(values[0])
    if not result:
        raise ValueError("TBB rapor yılı kataloğu boş.")
    return result


def discover_report_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, str(anchor.get("href")))
        if (
            "/istatistiki-raporlar/" in href
            and "tuketici-kredileri-ve-konut-kredileri" in href
        ):
            candidates.append(href)
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 1:
        raise ValueError(f"Bir dönem için birden çok TBB raporu bulundu: {unique}")
    return unique[0] if unique else None


def attachment_format(title: str, href: str, content_type: str) -> str | None:
    candidate = f"{title} {href}".casefold()
    for extension in ("xlsx", "xls", "pdf", "docx"):
        if re.search(rf"\.{extension}(?:\W|$)", candidate):
            return extension
    normalized_type = content_type.casefold()
    if "spreadsheetml" in normalized_type:
        return "xlsx"
    if "ms-excel" in normalized_type:
        return "xls"
    if "pdf" in normalized_type:
        return "pdf"
    if "wordprocessingml" in normalized_type:
        return "docx"
    return None


def discover_attachments(html: str, base_url: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[download][href]"):
        href = urljoin(base_url, str(anchor.get("href")))
        title = str(anchor.get("title") or anchor.get_text(" ", strip=True))
        content_type = str(anchor.get("type") or "")
        file_format = attachment_format(title, href, content_type)
        if file_format not in SUPPORTED_FORMATS:
            continue
        if file_format in result:
            raise ValueError(f"TBB raporunda tekrarlanan {file_format} eki var.")
        result[file_format] = {
            "url": href,
            "title": title,
            "declared_content_type": content_type,
        }
    if not any(key in result for key in ("xls", "xlsx")):
        raise ValueError("TBB raporunda Excel eki bulunamadı.")
    return result


def validate_attachment(data: bytes, file_format: str, content_type: str) -> None:
    if len(data) < 512:
        raise ValueError(f"TBB {file_format} eki beklenenden küçük: {len(data)} bayt")
    lowered = data[:256].lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html")) or "text/html" in content_type:
        raise ValueError(f"TBB {file_format} eki yerine HTML döndü.")
    if file_format == "xls" and not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("TBB XLS eki OLE2 imzasına sahip değil.")
    if file_format in {"xlsx", "docx"} and not data.startswith(b"PK"):
        raise ValueError(f"TBB {file_format} eki ZIP imzasına sahip değil.")
    if file_format == "pdf" and not data.startswith(b"%PDF"):
        raise ValueError("TBB PDF eki PDF imzasına sahip değil.")


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
        except Exception as exc:  # noqa: BLE001 - retried and persisted by caller
            last_error = exc
            if attempt <= retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(str(last_error))


def load_cached_attachment(path: Path, file_format: str) -> bytes | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    try:
        validate_attachment(data, file_format, "")
    except ValueError:
        return None
    return data


def run(args: argparse.Namespace) -> int:
    if args.timeout <= 0 or args.delay < 0 or args.retries < 0:
        raise ValueError("Timeout pozitif, delay ve retries negatif olmayan sayılar olmalı.")
    periods = quarters(args.start, args.end)
    requested_formats = list(dict.fromkeys(args.formats))
    if any(item not in SUPPORTED_FORMATS for item in requested_formats):
        raise ValueError(f"Desteklenmeyen dosya biçimi: {requested_formats}")

    output = Path(args.output).expanduser().resolve()
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    category_data, category_fetch = get_with_retries(
        session, BASE_URL + CATEGORY_PAGE, args.timeout, args.retries
    )
    category_html = category_data.decode("utf-8")
    year_terms = discover_year_terms(category_html)
    config = {
        "source": "Türkiye Bankalar Birliği",
        "category_url": BASE_URL + CATEGORY_PAGE,
        "category_id": CATEGORY_ID,
        "report_family": REPORT_NAME,
        "start": args.start,
        "end": args.end,
        "periods": periods,
        "formats": requested_formats,
        "tls_verification": True,
    }
    config_path = output / "request_config.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if existing != config:
            raise ValueError("Bu çıktı klasörü farklı bir TBB seçimiyle kullanılmış.")
    atomic_json(config_path, config)
    atomic_bytes(output / "category_page.html.gz", gzip.compress(category_data, 9))

    manifest: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    source_gaps: list[dict[str, Any]] = []
    attachment_count = 0
    for period in periods:
        year, month = parse_quarter(period)
        year_term = year_terms.get(year)
        if year_term is None:
            gap = {
                "period": period,
                "status": "source_gap",
                "reason": "year_not_listed_by_source",
            }
            source_gaps.append(gap)
            manifest.append(gap)
            atomic_json(output / "manifest.json", manifest)
            print(f"{period}: kaynak yılı yayımlanmamış", flush=True)
            continue

        list_url = (
            f"{BASE_URL}/istatistiki-raporlar-liste/{CATEGORY_ID}"
            f"?rapor_donemi={year_term}&ay={month}"
        )
        try:
            list_data, list_fetch = get_with_retries(
                session, list_url, args.timeout, args.retries
            )
            report_url = discover_report_url(list_data.decode("utf-8"), list_url)
            if report_url is None:
                gap = {
                    "period": period,
                    "status": "source_gap",
                    "reason": "quarterly_report_not_published",
                    "list_url": list_url,
                    "list_page_sha256": sha256(list_data),
                }
                source_gaps.append(gap)
                manifest.append(gap)
                atomic_json(output / "manifest.json", manifest)
                print(f"{period}: kaynakta rapor yok", flush=True)
                continue

            report_data, report_fetch = get_with_retries(
                session, report_url, args.timeout, args.retries
            )
            report_html = report_data.decode("utf-8")
            attachments = discover_attachments(report_html, report_url)
            period_dir = raw_dir / period
            atomic_bytes(period_dir / "report_page.html.gz", gzip.compress(report_data, 9))

            period_entry: dict[str, Any] = {
                "period": period,
                "status": "validated",
                "list_url": list_url,
                "report_url": report_url,
                "list_fetch": list_fetch,
                "report_fetch": report_fetch,
                "attachments": {},
            }
            for requested_format in requested_formats:
                source_format = requested_format
                if source_format not in attachments and source_format == "xls":
                    source_format = "xlsx" if "xlsx" in attachments else source_format
                if source_format not in attachments:
                    raise ValueError(
                        f"{period} raporunda {requested_format} eki bulunamadı."
                    )
                attachment = attachments[source_format]
                target_format = source_format
                target_path = period_dir / f"consumer_credit_report.{target_format}"
                cached_data = load_cached_attachment(target_path, target_format)
                if cached_data is None:
                    data, fetch_info = get_with_retries(
                        session, attachment["url"], args.timeout, args.retries
                    )
                    validate_attachment(data, target_format, fetch_info["content_type"])
                    atomic_bytes(target_path, data)
                    served_from_cache = False
                    time.sleep(args.delay)
                else:
                    data = cached_data
                    fetch_info = {
                        "requested_url": attachment["url"],
                        "final_url": attachment["url"],
                        "http_status": None,
                        "content_type": attachment["declared_content_type"],
                        "bytes": len(data),
                        "sha256": sha256(data),
                        "attempt": 0,
                        "tls_verification": True,
                    }
                    served_from_cache = True
                period_entry["attachments"][target_format] = {
                    **attachment,
                    **fetch_info,
                    "local_file": str(target_path.relative_to(output)),
                    "served_from_cache": served_from_cache,
                }
                attachment_count += 1

            atomic_json(period_dir / "source_info.json", period_entry)
            manifest.append(period_entry)
            atomic_json(output / "manifest.json", manifest)
            print(
                f"{period}: {len(period_entry['attachments'])} ek doğrulandı",
                flush=True,
            )
            time.sleep(args.delay)
        except Exception as exc:  # noqa: BLE001 - exact failure is persisted
            failure = {
                "period": period,
                "status": "failed",
                "error": str(exc),
                "completed_at_utc": utc_now(),
            }
            failures.append(failure)
            manifest.append(failure)
            atomic_json(output / "manifest.json", manifest)
            print(f"DURDU {period}: {exc}", flush=True)
            break

    successful_periods = sum(item.get("status") == "validated" for item in manifest)
    if failures:
        status = "incomplete"
    elif source_gaps:
        status = "complete_with_source_gaps"
    else:
        status = "complete"
    summary = {
        "status": status,
        "expected_periods": len(periods),
        "published_periods": successful_periods,
        "source_gap_periods": [item["period"] for item in source_gaps],
        "failed_periods": [item["period"] for item in failures],
        "validated_attachment_count": attachment_count,
        "formats": requested_formats,
        "category_fetch": category_fetch,
        "limits": [
            "TBB reports are quarterly and do not provide monthly disbursement flows.",
            "A missing source publication is recorded and never imputed.",
            "Reporting-bank coverage can vary and must be retained as metadata.",
        ],
    }
    atomic_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-03")
    parser.add_argument("--end", default="2026-06")
    parser.add_argument(
        "--formats", nargs="+", default=["xls", "pdf", "docx"]
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--output", default="data_pipeline/tbb/consumer_credit_reports")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
