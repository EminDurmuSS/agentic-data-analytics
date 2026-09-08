#!/usr/bin/env python3
"""Download the official TÜİK province housing-sales CSV with provenance.

The source export is preserved as deterministic gzip-compressed bytes. The
metadata records both the SHA-256 of the exact HTTP response body and the
stored gzip file. Processing is intentionally separate from downloading.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data_pipeline" / "tuik" / "province_housing_sales_v1"
)
DATAFLOW_ID = "DF_SATIS_SEKLI_DURUMU_ILILCE_V3+V1.0"
SOURCE_URL = (
    "https://veriportali.tuik.gov.tr/api/tr/dataflows/"
    "DF_SATIS_SEKLI_DURUMU_ILILCE_V3%2BV1.0/file/csv"
)
REFERER = "https://veriportali.tuik.gov.tr/tr/bulk-download"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
EXPECTED_HEADER = (
    '"Gözlem Sıklığı (M)";"Satış Türü (_T)";'
    '"Coğrafi Kapsam (TR100)"'
).encode("utf-8")


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
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_csv(data: bytes, content_type: str) -> None:
    if len(data) < 1_000_000:
        raise ValueError(f"TÜİK CSV beklenenden küçük: {len(data)} bayt")
    body = data.removeprefix(b"\xef\xbb\xbf")
    if not body.startswith(EXPECTED_HEADER):
        raise ValueError("TÜİK yanıtı beklenen il konut satış CSV başlığına sahip değil.")
    if "text/csv" not in content_type.casefold():
        raise ValueError(f"TÜİK yanıt içerik türü CSV değil: {content_type}")


def run(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    request_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
    }
    started = utc_now()
    response = requests.get(
        SOURCE_URL,
        headers=request_headers,
        timeout=args.timeout,
    )
    response.raise_for_status()
    data = response.content
    content_type = response.headers.get("Content-Type", "")
    validate_csv(data, content_type)

    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    raw_path = raw_dir / "province_housing_sales.csv.gz"
    atomic_bytes(raw_path, compressed)

    request = {
        "source_organization": "Türkiye İstatistik Kurumu",
        "source_system": "TÜİK Veri Portalı",
        "dataflow_id": DATAFLOW_ID,
        "method": "GET",
        "url": SOURCE_URL,
        "headers": request_headers,
        "tls_verification": True,
    }
    metadata = {
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "requested_url": SOURCE_URL,
        "final_url": response.url,
        "http_status": response.status_code,
        "content_type": content_type,
        "content_disposition": response.headers.get("Content-Disposition", ""),
        "etag": response.headers.get("ETag", ""),
        "raw_response_bytes": len(data),
        "raw_response_sha256": sha256(data),
        "stored_gzip_bytes": len(compressed),
        "stored_gzip_sha256": sha256(compressed),
        "stored_file": raw_path.relative_to(PROJECT_ROOT).as_posix(),
    }
    atomic_json(output / "request.json", request)
    atomic_json(output / "response_metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
