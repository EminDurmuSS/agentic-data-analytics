#!/usr/bin/env python3
"""Download and validate public BDDK weekly bulletin tables.

The weekly application is session based. This downloader follows the public
forms, keeps the ASP.NET session cookie and anti-forgery tokens, stores each
source page as gzip-compressed HTML, and writes a lossless long-form cell CSV.

Default scope: all nine weekly tables for the sector group, from 2021-01-01
through 2026-06-30. Additional groups can be selected explicitly.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.bddk.org.tr"
ROOT_PATH = "/BultenHaftalik/"
PERIOD_PATH = "/BultenHaftalik/tr/Home/DonemDegistir"
GROUP_PATH = "/BultenHaftalik/tr/Home/TarafSec"
HEADERS = {"User-Agent": "KKB-Agentic-Data-Analytics/1.0"}

TABLES = {
    289: "Krediler",
    290: "Takipteki Alacaklar",
    291: "Menkul Degerler",
    292: "Mevduat",
    293: "Diger Bilanco Kalemleri",
    294: "Bilanco Disi Islemler",
    295: "Bankalarda Saklanan Menkul Degerler - 1",
    296: "Bankalarda Saklanan Menkul Degerler - 2",
    297: "Yabanci Para Pozisyonu",
}

GROUPS = {
    10001: "Sektor",
    10002: "Mevduat",
    10003: "Kalkinma ve Yatirim",
    10004: "Katilim",
    10005: "Kamu",
    10006: "Yabanci",
    10007: "Yerli Ozel",
}

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


@dataclass(frozen=True)
class Period:
    period_id: int
    observation_date: date
    week_number: int
    source_label: str


@dataclass
class HttpResponse:
    text: str
    status_code: int
    headers: dict[str, str]
    url: str

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}: {self.url}")


class CurlSession:
    """Small curl-backed session that keeps cookies and verifies TLS."""

    def __init__(self, headers: dict[str, str]):
        if not shutil.which("curl"):
            raise RuntimeError("curl bulunamadi; --transport requests kullanin.")
        self.headers = dict(headers)
        self.temporary = tempfile.TemporaryDirectory(prefix="bddk_weekly_http_")
        self.cookie_path = Path(self.temporary.name) / "cookies.txt"
        self.counter = 0

    def close(self) -> None:
        self.temporary.cleanup()

    def get(self, url: str, timeout: int) -> HttpResponse:
        return self._request("GET", url, None, timeout)

    def post(self, url: str, data: dict[str, str], timeout: int) -> HttpResponse:
        return self._request("POST", url, data, timeout)

    def _request(
        self,
        method: str,
        url: str,
        data: dict[str, str] | None,
        timeout: int,
    ) -> HttpResponse:
        self.counter += 1
        response_path = Path(self.temporary.name) / f"response_{self.counter}.bin"
        command = [
            "curl",
            "--disable",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "3",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--connect-timeout",
            str(min(timeout, 15)),
            "--max-time",
            str(timeout),
            "--cookie",
            str(self.cookie_path),
            "--cookie-jar",
            str(self.cookie_path),
            "--output",
            str(response_path),
            "--write-out",
            "%{http_code}\n%{url_effective}\n%{content_type}",
        ]
        headers = dict(self.headers)
        headers.setdefault("Accept", "text/html,application/xhtml+xml")
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            command.extend(["--data-binary", "@-"])
            request_body = urllib.parse.urlencode(data).encode("utf-8")
        else:
            request_body = b""
            if method != "GET":
                command.extend(["--request", method])
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        command.append(url)
        try:
            result = subprocess.run(
                command,
                input=request_body,
                capture_output=True,
                timeout=timeout + 5,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"curl zaman asimi: {url}") from exc
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"curl basarisiz ({result.returncode}): {error}")
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise RuntimeError(f"curl yanit metadatasi okunamadi: {url}")
        if not response_path.exists():
            raise RuntimeError(f"curl yanit govdesi olusturmadi: {url}")
        body = response_path.read_bytes()
        return HttpResponse(
            text=body.decode("utf-8", errors="replace"),
            status_code=int(lines[0]),
            headers={"Content-Type": lines[2]},
            url=lines[1],
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def normalize_text(value: str) -> str:
    translation = str.maketrans(
        {"ı": "i", "İ": "I", "ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"}
    )
    value = value.translate(translation)
    value = unicodedata.normalize("NFKD", value)
    return " ".join(value.encode("ascii", "ignore").decode("ascii").split()).casefold()


def parse_periods(html: str, start: date, end: date) -> list[Period]:
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", id="Donem")
    if select is None:
        raise ValueError("Haftalik donem listesi bulunamadi.")
    result = []
    for option in select.find_all("option"):
        classes = option.get("class") or []
        year_class = next((item for item in classes if item.startswith("Yil-")), None)
        if not year_class:
            continue
        year = int(year_class.removeprefix("Yil-"))
        label = " ".join(option.get_text(" ", strip=True).split())
        match = re.fullmatch(r"([^/]+)/([0-9]{1,2}) \(([0-9]{1,2})\. Hafta\)", label)
        if not match:
            raise ValueError(f"Bilinmeyen haftalik donem etiketi: {label!r}")
        month_name = normalize_text(match.group(1))
        if month_name not in TURKISH_MONTHS:
            raise ValueError(f"Bilinmeyen Turkce ay adi: {match.group(1)!r}")
        observation_date = date(year, TURKISH_MONTHS[month_name], int(match.group(2)))
        if start <= observation_date <= end:
            result.append(
                Period(
                    period_id=int(option["value"]),
                    observation_date=observation_date,
                    week_number=int(match.group(3)),
                    source_label=label,
                )
            )
    result.sort(key=lambda item: item.observation_date)
    if not result:
        raise ValueError("Secilen tarih araliginda haftalik donem bulunamadi.")
    if len({item.period_id for item in result}) != len(result):
        raise ValueError("Haftalik donem kimlikleri tekrarlaniyor.")
    return result


def form_token(html: str, action: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"action": action})
    if form is None:
        raise ValueError(f"Form bulunamadi: {action}")
    token = form.find("input", attrs={"name": "__RequestVerificationToken"})
    if token is None or not token.get("value"):
        raise ValueError(f"Dogrulama token'i bulunamadi: {action}")
    return str(token["value"])


def post_form(
    session: Any,
    current_html: str,
    path: str,
    fields: dict[str, str],
    timeout: int,
) -> str:
    payload = {"__RequestVerificationToken": form_token(current_html, path), **fields}
    response = session.post(BASE_URL + path, data=payload, timeout=timeout)
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", ""):
        raise ValueError(f"Beklenmeyen haftalik icerik turu: {response.headers.get('Content-Type')}")
    return response.text


def load_context_with_retry(
    session: Any,
    timeout: int,
    retries: int,
    group_code: int | None = None,
    table_id: int | None = None,
) -> str:
    """Reload the weekly application and restore the selected context.

    BDDK occasionally times out while the downloader is refreshing the
    session after a failed period request. Refresh requests therefore need
    the same retry protection as period requests. TLS verification remains
    enabled in both supported transports.
    """

    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            response = session.get(BASE_URL + ROOT_PATH, timeout=timeout)
            response.raise_for_status()
            html = response.text
            if group_code is not None:
                html = post_form(
                    session,
                    html,
                    GROUP_PATH,
                    {"tarafKodu": str(group_code)},
                    timeout,
                )
            if table_id is not None:
                html = post_form(
                    session,
                    html,
                    ROOT_PATH,
                    {"tabloId": str(table_id)},
                    timeout,
                )
            return html
        except Exception as exc:  # noqa: BLE001 - retry exact public request
            last_error = exc
            if attempt <= retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(
        "BDDK haftalik oturumu yeniden kurulamadi: " f"{last_error}"
    ) from last_error


def parse_page(
    html: str, expected_period: Period, table_id: int, group_code: int
) -> tuple[list[str], list[list[str]], dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="Tablo")
    if table is None:
        raise ValueError("Haftalik veri tablosu bulunamadi.")

    headings = [" ".join(tag.get_text(" ", strip=True).split()) for tag in soup.find_all("h4")]
    expected_table = normalize_text(TABLES[table_id])
    if expected_table not in {normalize_text(value) for value in headings}:
        raise ValueError(f"Secilen haftalik tablo basligi bulunamadi: {TABLES[table_id]}")

    h5_texts = [" ".join(tag.get_text(" ", strip=True).split()) for tag in soup.find_all("h5")]
    if normalize_text(GROUPS[group_code]) not in {normalize_text(value) for value in h5_texts}:
        raise ValueError(f"Secilen haftalik grup basligi bulunamadi: {GROUPS[group_code]}")

    date_text = next((value for value in h5_texts if "Tarih:" in value), "")
    iso_date = expected_period.observation_date.isoformat()
    date_match = re.search(
        r"Tarih:\s*([0-9]{1,2})\s+([^\s]+)\s+([0-9]{4})", date_text
    )
    if not date_match:
        raise ValueError(f"Haftalik cevap tarihi okunamadi: {date_text!r}")
    source_month = TURKISH_MONTHS.get(normalize_text(date_match.group(2)))
    if source_month is None:
        raise ValueError(f"Haftalik cevap ay adi okunamadi: {date_text!r}")
    source_date = date(
        int(date_match.group(3)), source_month, int(date_match.group(1))
    )
    if source_date != expected_period.observation_date:
        raise ValueError(
            "Haftalik cevap tarihi istenen donemle uyusmuyor: "
            f"beklenen={iso_date}, gelen={source_date.isoformat()}"
        )

    header_rows = []
    body_rows = []
    for tr in table.find_all("tr"):
        headers = [" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all("th")]
        cells = [" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all("td")]
        if headers:
            header_rows.append(headers)
        elif cells:
            body_rows.append(cells)
    if not body_rows:
        raise ValueError("Haftalik tabloda veri satiri bulunamadi.")
    headers = header_rows[-1] if header_rows else []
    max_cells = max(len(row) for row in body_rows)
    if headers and len(headers) != max_cells:
        # Some source pages use multi-row or colspan headers. Keep positional
        # columns without inventing a semantic merge.
        headers = [f"source_column_{index}" for index in range(max_cells)]
    elif not headers:
        headers = [f"source_column_{index}" for index in range(max_cells)]

    metadata = {
        "table_id": table_id,
        "table_name": TABLES[table_id],
        "group_code": group_code,
        "group_name": GROUPS[group_code],
        "period_id": expected_period.period_id,
        "observation_date": iso_date,
        "week_number": expected_period.week_number,
        "source_period_label": expected_period.source_label,
        "source_date_heading": date_text,
        "source_date_parsed": source_date.isoformat(),
        "headers": headers,
        "rows": len(body_rows),
        "max_cells_per_row": max_cells,
        "unit_heading": date_text.split("Birim:", 1)[1].strip() if "Birim:" in date_text else None,
        "date_heading_normalized": normalize_text(date_text),
    }
    return headers, body_rows, metadata


def load_cached(
    raw_path: Path,
    info_path: Path,
    period: Period,
    table_id: int,
    group_code: int,
) -> tuple[str, list[str], list[list[str]], dict[str, Any]] | None:
    if not raw_path.exists() or not info_path.exists():
        return None
    data = gzip.decompress(raw_path.read_bytes())
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info.get("status") != "validated" or info.get("sha256_uncompressed") != sha256(data):
        return None
    html = data.decode("utf-8")
    headers, rows, metadata = parse_page(html, period, table_id, group_code)
    return html, headers, rows, metadata


def write_long_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "observation_date",
        "period_id",
        "week_number",
        "table_id",
        "table_name",
        "group_code",
        "group_name",
        "source_row_index",
        "source_column_index",
        "source_column_name",
        "value_raw",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        raise ValueError("Baslangic tarihi bitis tarihinden sonra.")
    if args.timeout <= 0 or args.delay < 0 or args.retries < 0:
        raise ValueError("Timeout pozitif, delay ve retries negatif olmayan sayilar olmali.")
    table_ids = sorted(set(args.tables))
    group_codes = sorted(set(args.groups))
    if any(table_id not in TABLES for table_id in table_ids):
        raise ValueError("Haftalik tablo kimligi 289..297 olmali.")
    if any(group_code not in GROUPS for group_code in group_codes):
        raise ValueError("Haftalik grup kodu 10001..10007 olmali.")

    output = Path(args.output).expanduser().resolve()
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.transport == "curl":
        session: Any = CurlSession(HEADERS)
    else:
        session = requests.Session()
        session.headers.update(HEADERS)
    current_html = load_context_with_retry(
        session,
        timeout=args.timeout,
        retries=args.retries,
    )
    periods = parse_periods(current_html, start, end)

    config = {
        "source_url": BASE_URL + ROOT_PATH,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "period_count": len(periods),
        "tables": {str(key): TABLES[key] for key in table_ids},
        "groups": {str(key): GROUPS[key] for key in group_codes},
        "currency": "TL",
        "raw_format": "gzip-compressed source HTML",
        "normalized_format": "lossless long-form cell CSV",
        "transport": args.transport,
        "tls_verification": True,
    }
    config_path = output / "request_config.json"
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        legacy_config = {key: value for key, value in config.items() if key != "transport"}
        if existing == legacy_config:
            atomic_json(config_path, config)
        elif existing != config:
            raise ValueError("Bu cikti klasoru farkli bir haftalik secimle kullanilmis.")
    atomic_json(config_path, config)

    manifest = []
    long_rows: list[dict[str, Any]] = []
    failures = []
    for group_code in group_codes:
        current_html = load_context_with_retry(
            session,
            timeout=args.timeout,
            retries=args.retries,
            group_code=group_code,
        )
        for table_id in table_ids:
            current_html = load_context_with_retry(
                session,
                timeout=args.timeout,
                retries=args.retries,
                group_code=group_code,
                table_id=table_id,
            )
            for period in periods:
                stem = f"{period.observation_date.isoformat()}_table{table_id}_group{group_code}"
                raw_path = raw_dir / f"{stem}.html.gz"
                info_path = raw_dir / f"{stem}_info.json"
                cached = load_cached(raw_path, info_path, period, table_id, group_code)
                if cached:
                    html, headers, rows, metadata = cached
                    served_from_cache = True
                else:
                    served_from_cache = False
                    error = None
                    restore_context = False
                    for attempt in range(1, args.retries + 2):
                        try:
                            if restore_context:
                                current_html = load_context_with_retry(
                                    session,
                                    timeout=args.timeout,
                                    retries=args.retries,
                                    group_code=group_code,
                                    table_id=table_id,
                                )
                                restore_context = False
                            current_html = post_form(
                                session,
                                current_html,
                                PERIOD_PATH,
                                {
                                    "yil": str(period.observation_date.year),
                                    "donemId": str(period.period_id),
                                    "para": "TL",
                                },
                                args.timeout,
                            )
                            headers, rows, metadata = parse_page(
                                current_html, period, table_id, group_code
                            )
                            html = current_html
                            error = None
                            break
                        except Exception as exc:  # noqa: BLE001 - retry exact public request
                            error = exc
                            restore_context = True
                            if attempt <= args.retries:
                                time.sleep(min(2**attempt, 8))
                    if error is not None:
                        failure = {
                            "status": "failed",
                            "observation_date": period.observation_date.isoformat(),
                            "period_id": period.period_id,
                            "table_id": table_id,
                            "group_code": group_code,
                            "error": str(error),
                            "completed_at_utc": utc_now(),
                        }
                        atomic_json(info_path, failure)
                        manifest.append(failure)
                        failures.append(failure)
                        atomic_json(output / "manifest.json", manifest)
                        print(
                            f"DURDU {period.observation_date} tablo={table_id} "
                            f"grup={group_code}: {error}",
                            flush=True,
                        )
                        session.close()
                        return 1
                    raw_bytes = html.encode("utf-8")
                    raw_path.write_bytes(gzip.compress(raw_bytes, compresslevel=9))
                    time.sleep(args.delay)

                raw_bytes = html.encode("utf-8")
                info = {
                    "status": "validated",
                    "diagnosis": "validated_bddk_weekly_html_table",
                    "source_url": BASE_URL + ROOT_PATH,
                    "sha256_uncompressed": sha256(raw_bytes),
                    "compressed_bytes": raw_path.stat().st_size,
                    "uncompressed_bytes": len(raw_bytes),
                    "served_from_cache": served_from_cache,
                    "completed_at_utc": utc_now(),
                    **metadata,
                }
                atomic_json(info_path, info)
                manifest.append(info)
                atomic_json(output / "manifest.json", manifest)

                for row_index, row in enumerate(rows, start=1):
                    for column_index, value in enumerate(row):
                        long_rows.append(
                            {
                                "observation_date": period.observation_date.isoformat(),
                                "period_id": period.period_id,
                                "week_number": period.week_number,
                                "table_id": table_id,
                                "table_name": TABLES[table_id],
                                "group_code": group_code,
                                "group_name": GROUPS[group_code],
                                "source_row_index": row_index,
                                "source_column_index": column_index,
                                "source_column_name": headers[column_index]
                                if column_index < len(headers)
                                else f"source_column_{column_index}",
                                "value_raw": value,
                            }
                        )
                print(
                    f"{period.observation_date} tablo={table_id} grup={group_code}: "
                    f"{len(rows)} satir" + (" (kayitli)" if served_from_cache else ""),
                    flush=True,
                )

    write_long_csv(output / "weekly_cells_long.csv", long_rows)
    expected = len(periods) * len(table_ids) * len(group_codes)
    summary = {
        "status": "complete" if len(manifest) == expected and not failures else "incomplete",
        "expected_requests": expected,
        "successful_requests": sum(item.get("status") == "validated" for item in manifest),
        "period_count": len(periods),
        "table_count": len(table_ids),
        "group_count": len(group_codes),
        "long_cell_rows": len(long_rows),
        "first_observation_date": periods[0].observation_date.isoformat(),
        "last_observation_date": periods[-1].observation_date.isoformat(),
        "limits": [
            "Source HTML tables are preserved because weekly schemas vary by table and period.",
            "Raw numeric formatting is preserved. Numeric normalization is a separate audited step.",
            "Missing cells are not imputed.",
        ],
    }
    atomic_json(output / "summary.json", summary)
    session.close()
    print(
        f"Sonuc: {summary['status']} "
        f"({summary['successful_requests']}/{summary['expected_requests']} istek, "
        f"{summary['long_cell_rows']} hucre)"
    )
    return 0 if summary["status"] == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--tables", nargs="+", type=int, default=sorted(TABLES))
    parser.add_argument("--groups", nargs="+", type=int, default=[10001])
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument(
        "--transport",
        choices=["curl", "requests"],
        default="curl" if shutil.which("curl") else "requests",
    )
    parser.add_argument("--output", default="data_pipeline/bddk/weekly_all_sector")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
