#!/usr/bin/env python3
"""Download and validate public BDDK FinTurk quarterly data.

The default scope is every published FinTurk table, every institution group,
and every province for 2021-Q1 through 2026-Q2. The downloader stores the raw
JSON response for provenance and writes one union-schema CSV per source table.

Python 3.9+ and the standard library are sufficient.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENDPOINT = "https://www.bddk.org.tr/BultenFinturk/tr/Home/VeriGetir"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "KKB-Agentic-Data-Analytics/1.0",
    "X-Requested-With": "XMLHttpRequest",
}

TABLES = {
    1: "Krediler",
    2: "Mevduat",
    3: "Bireysel Bankacilik",
    4: "Secilmis Sektorel Krediler",
    5: "Oranlar",
    6: "Subeler ve Nufusa Gore Dagilim",
    7: "Altin Kredileri ve Altin Mevduati",
}

GROUPS = {
    10001: "SEKTOR",
    10002: "MEVDUAT",
    10003: "KALKINMA VE YATIRIM",
    10004: "KATILIM",
    10005: "YABANCI",
    10006: "KAMU",
    10007: "YERLI OZEL",
}


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


def parse_quarter(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(3|6|9|12)", value)
    if not match:
        raise ValueError("Donem YYYY-3, YYYY-6, YYYY-9 veya YYYY-12 olmali.")
    return int(match.group(1)), int(match.group(2))


def quarters(start: str, end: str) -> list[str]:
    year, month = parse_quarter(start)
    last = parse_quarter(end)
    if (year, month) > last:
        raise ValueError("Baslangic donemi bitis doneminden sonra.")
    result = []
    while (year, month) <= last:
        result.append(f"{year}-{month}")
        if month == 12:
            year, month = year + 1, 3
        else:
            month += 3
    return result


def request_body(table_no: int, period: str, groups: list[int]) -> bytes:
    fields: list[tuple[str, str]] = [("tabloNo", str(table_no)), ("donem", period)]
    fields.extend(("tarafList", str(group)) for group in groups)
    # The public UI uses HEPSI to request every province and YURT DISI.
    fields.append(("sehirList", "HEPSİ"))
    return urllib.parse.urlencode(fields).encode("utf-8")


def fetch(
    body: bytes,
    timeout: int,
    transport: str,
    ca_bundle: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    started = utc_now()
    request = urllib.request.Request(
        ENDPOINT, data=body, headers=HEADERS, method="POST"
    )
    data = b""
    status = None
    content_type = ""
    error = ""
    final_url = ENDPOINT
    if transport == "curl":
        if not shutil.which("curl"):
            raise RuntimeError("curl bulunamadı; --transport urllib kullanın.")
        with tempfile.TemporaryDirectory(prefix="bddk_finturk_http_") as temp_dir:
            response_path = Path(temp_dir) / "response.bin"
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
                "--output",
                str(response_path),
                "--write-out",
                "%{http_code}\n%{url_effective}\n%{content_type}",
                "--request",
                "POST",
                "--data-binary",
                "@-",
            ]
            for key, value in HEADERS.items():
                command.extend(["--header", f"{key}: {value}"])
            if ca_bundle:
                command.extend(["--cacert", str(ca_bundle)])
            command.append(ENDPOINT)
            try:
                result = subprocess.run(
                    command,
                    input=body,
                    capture_output=True,
                    timeout=timeout + 5,
                    check=False,
                )
                lines = result.stdout.decode("utf-8", errors="replace").splitlines()
                if lines and lines[0].isdigit():
                    status = int(lines[0]) or None
                if len(lines) > 1:
                    final_url = lines[1]
                if len(lines) > 2:
                    content_type = lines[2]
                if result.returncode:
                    error = result.stderr.decode("utf-8", errors="replace")[:1000]
                if response_path.exists():
                    data = response_path.read_bytes()
            except subprocess.TimeoutExpired:
                error = "curl subprocess timeout"
    else:
        context = ssl.create_default_context(
            cafile=str(ca_bundle) if ca_bundle else None
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                data = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                final_url = response.url
        except urllib.error.HTTPError as exc:
            status = exc.code
            data = exc.read()
            content_type = exc.headers.get("Content-Type", "")
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - persisted as transport evidence
            error = str(exc)
    return data, {
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "url": ENDPOINT,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "bytes": len(data),
        "sha256": sha256(data),
        "error": error,
        "transport": transport,
        "tls_verification": True,
    }


def parse_response(data: bytes, period: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outer = json.loads(data.decode("utf-8-sig"))
    if not isinstance(outer, dict) or outer.get("success") is not True:
        raise ValueError("FinTurk success=true yaniti yok.")
    payload = outer.get("Json")
    if not isinstance(payload, dict):
        raise ValueError("FinTurk Json nesnesi bulunamadi.")
    models = payload.get("colModels")
    labels = payload.get("colNames")
    data_envelope = payload.get("data")
    rows = data_envelope.get("rows") if isinstance(data_envelope, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("FinTurk kolon modeli bos.")
    if not isinstance(labels, list) or len(labels) != len(models):
        raise ValueError("FinTurk kolon etiketi/modeli uyusmuyor.")
    names = [model.get("name") if isinstance(model, dict) else None for model in models]
    if not all(isinstance(name, str) and name for name in names):
        raise ValueError("FinTurk kolon kimligi bos.")
    if len(names) != len(set(names)):
        raise ValueError("FinTurk kolon kimlikleri tekrarlaniyor.")
    if not isinstance(rows, list) or not rows:
        raise ValueError("FinTurk veri satirlari bos.")

    parsed = []
    for row in rows:
        cells = row.get("cell") if isinstance(row, dict) else None
        if not isinstance(cells, list) or len(cells) != len(names):
            raise ValueError("FinTurk hucre sayisi kolon modeliyle uyusmuyor.")
        parsed.append(dict(zip(names, cells)))

    year, month = parse_quarter(period)
    if "Yil" not in names or "Ay" not in names:
        raise ValueError("FinTurk Yil/Ay kolonlari bulunamadi.")
    if any(int(row["Yil"]) != year or int(row["Ay"]) != month for row in parsed):
        raise ValueError("FinTurk cevap donemi istenen donemle uyusmuyor.")

    identity = [name for name in ["EftKodu", "Yil", "Ay", "Sehir", "Grup"] if name in names]
    keys = [tuple(str(row.get(name)) for name in identity) for row in parsed]
    if len(keys) != len(set(keys)):
        raise ValueError("FinTurk cevapta tekrarlanan kimlik satiri var.")

    metadata = {
        "columns": names,
        "column_labels": labels,
        "rows": len(parsed),
        "identity_columns": identity,
        "warning": payload.get("uyari"),
    }
    return parsed, metadata


def fetch_with_retries(
    body: bytes,
    period: str,
    timeout: int,
    retries: int,
    transport: str,
    ca_bundle: Path | None,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        data, info = fetch(body, timeout, transport, ca_bundle)
        info["attempt"] = attempt
        try:
            if not info["http_status"] or not 200 <= int(info["http_status"]) < 300:
                raise ValueError(f"HTTP status {info['http_status']}: {info['error']}")
            rows, metadata = parse_response(data, period)
            info["status"] = "validated"
            info["diagnosis"] = "validated_bddk_finturk_table"
            return data, info, rows, metadata
        except Exception as exc:  # noqa: BLE001 - retry and persist exact failure
            last_error = exc
            info["status"] = "failed"
            info["validation_error"] = str(exc)
            if attempt <= retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(str(last_error))


def write_table_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    if args.timeout <= 0 or args.delay < 0 or args.retries < 0:
        raise ValueError("Timeout pozitif, delay ve retries negatif olmayan sayilar olmali.")
    periods = quarters(args.start, args.end)
    tables = sorted(set(args.tables))
    groups = sorted(set(args.groups))
    if any(table not in TABLES for table in tables):
        raise ValueError("FinTurk tablo numarasi 1..7 olmali.")
    if any(group not in GROUPS for group in groups):
        raise ValueError("FinTurk grup kodu 10001..10007 olmali.")

    output = Path(args.output).expanduser().resolve()
    raw_dir = output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "endpoint": ENDPOINT,
        "start": args.start,
        "end": args.end,
        "periods": periods,
        "tables": {str(table): TABLES[table] for table in tables},
        "groups": {str(group): GROUPS[group] for group in groups},
        "cities": "HEPSI, meaning every province and YURT DISI in the public UI",
        "native_frequency": "quarterly",
        "raw_format": "gzip-compressed source JSON",
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
            raise ValueError("Bu cikti klasoru farkli bir FinTurk secimiyle kullanilmis.")
    else:
        atomic_json(config_path, config)

    manifest: list[dict[str, Any]] = []
    table_rows: dict[int, list[dict[str, Any]]] = {table: [] for table in tables}
    failures: list[dict[str, Any]] = []
    for period in periods:
        for table in tables:
            stem = f"{period}_table{table:02}"
            raw_path = raw_dir / f"{stem}.json.gz"
            info_path = raw_dir / f"{stem}_info.json"
            cached = False
            if raw_path.exists() and info_path.exists():
                data = gzip.decompress(raw_path.read_bytes())
                info = json.loads(info_path.read_text(encoding="utf-8"))
                try:
                    rows, metadata = parse_response(data, period)
                    cached = info.get("status") == "validated" and info.get("sha256") == sha256(data)
                except Exception:
                    cached = False
            if not cached:
                body = request_body(table, period, groups)
                try:
                    data, info, rows, metadata = fetch_with_retries(
                        body,
                        period,
                        args.timeout,
                        args.retries,
                        args.transport,
                        args.ca_bundle,
                    )
                    raw_path.write_bytes(gzip.compress(data, compresslevel=9))
                except Exception as exc:  # noqa: BLE001 - failure becomes manifest evidence
                    info = {
                        "period": period,
                        "table_no": table,
                        "status": "failed",
                        "error": str(exc),
                        "completed_at_utc": utc_now(),
                    }
                    failures.append(info)
                    atomic_json(info_path, info)
                    manifest.append(info)
                    atomic_json(output / "manifest.json", manifest)
                    print(f"DURDU {period} tablo={table}: {exc}", flush=True)
                    return 1
            info.update(
                {
                    "period": period,
                    "table_no": table,
                    "table_name": TABLES[table],
                    "requested_groups": groups,
                    "served_from_cache": cached,
                    "compressed_bytes": raw_path.stat().st_size,
                    **metadata,
                }
            )
            atomic_json(info_path, info)
            manifest.append(info)
            atomic_json(output / "manifest.json", manifest)
            for row in rows:
                table_rows[table].append(
                    {
                        "_requested_period": period,
                        "_requested_table_no": table,
                        "_requested_table_name": TABLES[table],
                        **row,
                    }
                )
            print(
                f"{period} tablo={table}: {len(rows)} satir"
                + (" (kayitli)" if cached else ""),
                flush=True,
            )
            if not cached:
                time.sleep(args.delay)

    for table, rows in table_rows.items():
        write_table_csv(output / f"table_{table:02}.csv", rows)

    expected = len(periods) * len(tables)
    summary = {
        "status": "complete" if len(manifest) == expected and not failures else "incomplete",
        "expected_requests": expected,
        "successful_requests": sum(item.get("status") == "validated" for item in manifest),
        "period_count": len(periods),
        "table_count": len(tables),
        "group_count": len(groups),
        "rows_by_table": {str(table): len(rows) for table, rows in table_rows.items()},
        "total_rows": sum(len(rows) for rows in table_rows.values()),
        "limits": [
            "FinTurk is quarterly, not monthly.",
            "Missing institution-city combinations are preserved as absent and are not filled.",
            "Source tables have different schemas and are stored separately.",
        ],
    }
    atomic_json(output / "summary.json", summary)
    print(
        f"Sonuc: {summary['status']} "
        f"({summary['successful_requests']}/{summary['expected_requests']} istek, "
        f"{summary['total_rows']} satir)"
    )
    return 0 if summary["status"] == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-3")
    parser.add_argument("--end", default="2026-6")
    parser.add_argument("--tables", nargs="+", type=int, default=sorted(TABLES))
    parser.add_argument("--groups", nargs="+", type=int, default=sorted(GROUPS))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument(
        "--transport",
        choices=["curl", "urllib"],
        default="curl" if shutil.which("curl") else "urllib",
    )
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument(
        "--output", default="data_pipeline/bddk/finturk_all_groups_all_cities"
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
