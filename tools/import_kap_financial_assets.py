"""Operator-only YFMEN 2025 consolidated total-assets import from KAP.

The FR notification supplies identity/publication metadata and the exact PDF
attachment link, never the numeric answer. The original PDF supplies the row,
period headings, consolidation scope and unit/purchasing-power caption. Both
downloads survive shared promotion byte-for-byte (HTML is losslessly encoded
inside inspection.json). Not exposed as an agent tool; inspect-only by default.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unicodedata

from bs4 import BeautifulSoup
import httpx
import pdfplumber
from pdfplumber.utils import extract_text, extract_words

from agentic_analytics.agent.tools.documents import DocumentTools, OFFICIAL_SOURCE_REGISTRY
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore, _write_json

NOTIFICATION_URL = "https://www.kap.org.tr/tr/Bildirim/1566753"
PDF_URL = "https://www.kap.org.tr/tr/api/file/download/4028328c9c81f417019cbf1bed6c4e4f"
PDF_FILENAME = "31.12.2025 SPK Rapor_Final.pdf"
ISSUER = "YATIRIM FİNANSMAN MENKUL DEĞERLER A.Ş."
PARSER_VERSION = "kap-yfmen-2025-consolidated-assets-v1"
PDF_PAGE = 8  # 1-based physical page; the printed financial-statement page is 1.
MAX_DOWNLOAD = 16 * 1024**2
TABLE_ID = "table_p000008_001"
JAVA_BYTE_ARRAY_PREFIX = bytes.fromhex("aced0005757200025b42acf317f8060854e00200007870")


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def download() -> tuple[bytes, bytes]:
    """Fetch only the two pinned anonymous HTTPS URLs, without redirects."""
    downloads = []
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for url in (NOTIFICATION_URL, PDF_URL):
            with client.stream("GET", url) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_DOWNLOAD:
                        raise ValueError("Official source exceeds download limit")
                downloads.append(bytes(data))
    return tuple(downloads)


def _notification(raw: bytes, notification_url: str, pdf_url: str) -> dict:
    if notification_url != NOTIFICATION_URL or pdf_url != PDF_URL:
        raise ValueError("Unexpected official notification/PDF source URL")
    if not raw or len(raw) > MAX_DOWNLOAD:
        raise ValueError("Invalid notification size")
    soup = BeautifulSoup(raw, "html.parser")
    if not soup.html or not soup.body:
        raise ValueError("Expected the original KAP notification HTML")
    for node in soup(["script", "style"]):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    folded = _fold(text)
    checks = {
        "issuer": _fold(ISSUER) in folded and re.search(r"\byfmen\b", folded),
        "FR annual period": "bildirim tipi fr yil 2025 periyot yillik" in folded,
        "consolidated scope": ("finansal tablo niteligi konsolide" in folded
                               and "finansal tablo niteligi konsolide olmayan" not in folded),
        "original TRY unit": "sunum para birimi tl finansal tablo niteligi" in folded,
    }
    for label, valid in checks.items():
        if not valid:
            raise ValueError(f"Notification {label} does not match")
    links = [a for a in soup.find_all("a", href=pdf_url)
             if a.get_text(" ", strip=True) == PDF_FILENAME]
    if not links:
        raise ValueError("Notification does not link the exact financial-report PDF attachment")
    dates = re.findall(r"G[oö]nderim Tarihi\s+(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})", text)
    if len(set(dates)) != 1:
        raise ValueError("Missing or ambiguous notification publication date")
    published = datetime.strptime(dates[0], "%d.%m.%Y %H:%M:%S")
    if published != datetime(2026, 3, 5, 21, 39, 59):
        raise ValueError("Unexpected notification publication date")
    return {"source_url": notification_url, "raw_sha256": _sha(raw), "size_bytes": len(raw),
            "issuer": ISSUER, "issuer_code": "YFMEN", "notification_type": "FR",
            "period": "2025", "period_type": "Yıllık", "scope": "consolidated",
            "published_at": published.isoformat() + "+03:00", "timezone": "Europe/Istanbul",
            "pdf_url": pdf_url, "attachment_filename": PDF_FILENAME,
            "metadata_evidence": ["Gönderim Tarihi " + dates[0],
                                  "Bildirim Tipi FR Yıl 2025 Periyot Yıllık",
                                  "Sunum Para Birimi TL Finansal Tablo Niteliği Konsolide"]}


def _horizontal_chars(page) -> list[dict]:
    # The actual PDF has tiny negative shear (~2e-8), which pdfminer's exact
    # upright flag treats as rotation. Normalize ONLY numerical zero, retaining
    # all original glyph text/coordinates. Never flatten genuinely rotated text.
    chars = []
    if len(page.chars) > 50_000:
        raise ValueError("Unexpected PDF page complexity")
    for char in page.chars:
        a, b, c, d, _, _ = char["matrix"]
        if a <= 0 or d <= 0 or abs(b) > 1e-6 or abs(c) > 1e-6:
            raise ValueError("Unsupported rotated PDF glyph layout")
        chars.append({**char, "upright": True})
    return chars


def parse_sources(notification_raw: bytes, pdf_raw: bytes, *,
                  notification_url: str = NOTIFICATION_URL, pdf_url: str = PDF_URL) -> dict:
    """Fail closed on changed source identity, layout, scope, units or periods."""
    notification = _notification(notification_raw, notification_url, pdf_url)
    if not pdf_raw or len(pdf_raw) > MAX_DOWNLOAD:
        raise ValueError("Expected a bounded real PDF, not HTML or an error page")
    # This KAP endpoint currently wraps the PDF in a Java-serialized byte[].
    # Preserve the HTTP body unmodified, but validate this exact inert envelope;
    # do not invoke Java deserialization or search arbitrary HTML for '%PDF'.
    document = pdf_raw
    envelope = "none"
    if pdf_raw.startswith(JAVA_BYTE_ARRAY_PREFIX):
        prefix = len(JAVA_BYTE_ARRAY_PREFIX)
        document = pdf_raw[prefix + 4:]
        if len(pdf_raw) < prefix + 4 or int.from_bytes(pdf_raw[prefix:prefix + 4], "big") != len(document):
            raise ValueError("Invalid KAP Java byte-array length")
        envelope = "java_serialized_byte_array_27_byte_prefix"
    if not document.startswith(b"%PDF-"):
        raise ValueError("Expected a real PDF or the exact KAP byte-array envelope")
    try:
        with pdfplumber.open(io.BytesIO(document)) as pdf:
            if len(pdf.pages) != 72:
                raise ValueError("Unexpected financial-report PDF page count")
            page = pdf.pages[PDF_PAGE - 1]
            chars = _horizontal_chars(page)
            text = extract_text(chars, x_tolerance=2, y_tolerance=3)
            words = extract_words(chars, x_tolerance=2, y_tolerance=3)
            width, height = page.width, page.height
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Malformed or unreadable financial-report PDF") from exc
    lines = text.splitlines()
    folded_lines = [_fold(line) for line in lines]
    expected_issuer = _fold(ISSUER + " VE BAĞLI ORTAKLIĞI")
    if not lines or folded_lines[0] != expected_issuer:
        raise ValueError("PDF page issuer/consolidated group does not match")
    expected_title = "31 aralik 2025 tarihi itibariyla konsolide finansal durum tablosu"
    if len(lines) < 5 or folded_lines[1] != expected_title:
        raise ValueError("PDF balance-sheet period/scope heading does not match")
    caption = " ".join(lines[2:4])
    expected_caption = ("(Tutarlar aksi belirtilmedikçe Türk Lirası'nın (“TL”) 31 Aralık 2025 tarihi "
                        "itibarıyla satın alma gücü esasına göre TL olarak ifade edilmiştir.)")
    # Quotes can differ between font encodings; words/scale/currency may not.
    clean = lambda value: _fold(value).translate(str.maketrans("", "", "()“”‘’\"'"))
    if clean(caption) != clean(expected_caption):
        raise ValueError("PDF original TRY unit/purchasing-power caption does not match")
    if not any("cari donem onceki donem" in line for line in folded_lines):
        raise ValueError("PDF current/prior period columns are missing or reversed")
    if not any("31 aralik 2025 31 aralik 2024" in line for line in folded_lines[4:]):
        raise ValueError("PDF current/prior column dates do not match")
    if lines[-1].strip() != "1":
        raise ValueError("PDF printed page label changed")
    number = r"(?:[1-9]\d{0,2}(?:\.\d{3})+|[1-9]\d*)"
    rows = {}
    for label in ("Toplam Dönen Varlıklar", "Toplam Duran Varlıklar", "TOPLAM VARLIKLAR"):
        pattern = re.compile(re.escape(_fold(label)) + rf" ({number}) ({number})")
        matches = [(index, pattern.fullmatch(line)) for index, line in enumerate(folded_lines)]
        matches = [(index, match) for index, match in matches if match]
        if len(matches) != 1:
            raise ValueError(f"Missing, malformed or ambiguous PDF row: {label}")
        index, match = matches[0]
        rows[label] = {"line": lines[index], "current": int(match[1].replace(".", "")),
                       "prior": int(match[2].replace(".", "")),
                       "current_text": match[1], "prior_text": match[2]}
    for column in ("current", "prior"):
        if rows["Toplam Dönen Varlıklar"][column] + rows["Toplam Duran Varlıklar"][column] != rows["TOPLAM VARLIKLAR"][column]:
            raise ValueError("PDF total assets do not reconcile to current + noncurrent assets")
    total = rows["TOPLAM VARLIKLAR"]
    labels = [word for word in words if word["text"] == "TOPLAM"]
    if len(labels) != 1:
        raise ValueError("Ambiguous total-assets row coordinates")
    label = labels[0]
    cells = []
    for column, token in (("current", total["current_text"]), ("prior", total["prior_text"])):
        matches = [word for word in words if word["text"] == token and abs(word["top"] - label["top"]) < 3]
        if len(matches) != 1:
            raise ValueError("PDF numeric cell is not on the total-assets row")
        word = matches[0]
        cells.append({"source_url": pdf_url, "page": PDF_PAGE, "page_index": PDF_PAGE - 1,
                      "printed_page": 1, "row_label": "TOPLAM VARLIKLAR", "column": column,
                      "column_header": "31 Aralık 2025" if column == "current" else "31 Aralık 2024",
                      "source_text": token, "bbox": [round(word[key], 4) for key in ("x0", "top", "x1", "bottom")]})
    if cells[0]["bbox"][0] <= label["x1"] or cells[0]["bbox"][2] >= cells[1]["bbox"][0]:
        raise ValueError("PDF total-assets column order is inconsistent")
    observation = {"year": "2025", "statement_date": "2025-12-31", "issuer_code": "YFMEN",
                   "issuer_name": ISSUER, "scope": "consolidated", "currency": "TRY",
                   "purchasing_power_date": "2025-12-31", "total_assets": total["current"]}
    return {"parser": PARSER_VERSION, "source_url": pdf_url, "raw_sha256": _sha(pdf_raw),
            "inner_pdf_sha256": _sha(document), "inner_pdf_size_bytes": len(document), "transport_envelope": envelope,
            "size_bytes": len(pdf_raw), "notification": notification, "total_pages": 72,
            "page": PDF_PAGE, "page_index": PDF_PAGE - 1, "printed_page": 1,
            "page_size": [width, height], "page_text": text, "unit_evidence": caption,
            "currency": "TRY", "scale": 1, "kind": "stock", "frequency": "annual",
            "row_checks": rows, "cell_origins": cells, "observations": [observation]}


def publish(store: LakehouseStore, workspace_id: str, notification_raw: bytes, pdf_raw: bytes, *,
            notification_url: str = NOTIFICATION_URL, pdf_url: str = PDF_URL) -> dict:
    """Validate all evidence first, ingest an explicit stock contract, promote."""
    parsed = parse_sources(notification_raw, pdf_raw, notification_url=notification_url, pdf_url=pdf_url)
    workspace = store.workspace(workspace_id)
    shared = SharedLakehouse(store)
    import_key = f"{PARSER_VERSION}:{parsed['raw_sha256']}"
    current = shared.current_release()
    # HTML contains dynamic site state; identical validated PDFs must not create
    # duplicate metrics merely because the notification bytes changed on fetch.
    candidates = set(workspace["datasets"]) | set((current or {}).get("dataset_ids", []))
    for dataset_id in sorted(candidates):
        provenance = store.dataset_manifest(dataset_id)["contract"].get("document_provenance", {})
        if provenance.get("import_key") == import_key:
            if dataset_id in workspace["datasets"]:
                return shared.promote(workspace_id, dataset_id, official_sources=OFFICIAL_SOURCE_REGISTRY,
                                      reason="Repeat of the same validated official KAP PDF import")
            recovered = shared.recover_promotion(dataset_id)
            if recovered:
                return recovered
    documents = DocumentTools(store, workspace_id, searxng_url=False)
    source = documents._register(pdf_raw, PDF_FILENAME, "application/pdf", pdf_url)
    observation = parsed["observations"][0]
    columns = list(observation)
    origins = {"total_assets": parsed["cell_origins"][0]}
    notification_evidence = {**parsed["notification"], "mime_type": "text/html",
                             "raw_encoding": "gzip+base64",
                             "raw_data": base64.b64encode(gzip.compress(notification_raw, mtime=0)).decode("ascii")}
    inspection = {
        "source_id": source["source_id"], "parser": PARSER_VERSION, "text": parsed["page_text"],
        "text_truncated": False, "total_pages": 72, "processed_pages": [PDF_PAGE],
        "processed_extractions": [f"{PDF_PAGE}:lines"],
        "pages": [{"page": PDF_PAGE, "printed_page": 1, "text": parsed["page_text"]}],
        "warnings": ["Only the 2025 current-period total-assets stock is imported; comparative 2024 cells are reconciliation evidence, not a second observation."],
        "notification_evidence": notification_evidence,
        "document_metadata": parsed["notification"], "row_checks": parsed["row_checks"],
        "transport_envelope": parsed["transport_envelope"], "inner_pdf_sha256": parsed["inner_pdf_sha256"],
        "inner_pdf_size_bytes": parsed["inner_pdf_size_bytes"],
        "tables": [{"table_id": TABLE_ID, "origin": "parsed", "page": PDF_PAGE,
                    "printed_page": 1, "columns": columns, "original_columns": {**dict(zip(columns, columns)), "total_assets": "TOPLAM VARLIKLAR / Cari dönem 31 Aralık 2025"},
                    "rows": [[observation[key] for key in columns]], "row_count": 1,
                    "unit_caption": parsed["unit_evidence"], "context_text": parsed["page_text"],
                    "row_origins": [{"page": PDF_PAGE, "printed_page": 1, "row_label": "TOPLAM VARLIKLAR"}],
                    "cell_origins": [origins]}],
    }
    _write_json(documents._directory(source["source_id"]) / "inspection.json", inspection)
    dimension = {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}
    contract = {
        "name": "KAP YFMEN Yatırım Finansman Menkul Değerler 2025 konsolide toplam varlıklar (TL)",
        "frequency": "annual", "date_column": "year", "key": ["year"], "grain": ["year"],
        "expected_rows": 1, "expected_periods": ["2025"], "source_namespace": source["source_namespace"],
        "columns": {"year": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    **{key: {**dimension, **({"unit": "calendar"} if key.endswith("_date") else {})}
                       for key in columns if key not in {"year", "total_assets"}},
                    "total_assets": {"dtype": "integer", "unit": "TRY", "currency": "TRY", "scale": 1,
                                     "kind": "stock", "aggregation": "last", "additive_over_time": False,
                                     "price_basis": "2025-12-31", "measurement_basis": "TFRS consolidated; source-reported 2025-12-31 purchasing power",
                                     "source_semantics": "Annual financial-report year-end balance-sheet stock; not a period flow or thousands of TRY",
                                     "nullable": False}},
        "document_provenance": {
            "source_id": source["source_id"], "table_id": TABLE_ID, "source_url": pdf_url,
            "raw_sha256": parsed["raw_sha256"], "page": PDF_PAGE, "page_index": PDF_PAGE - 1,
            "inner_pdf_sha256": parsed["inner_pdf_sha256"], "inner_pdf_size_bytes": parsed["inner_pdf_size_bytes"],
            "transport_envelope": parsed["transport_envelope"],
            "printed_page": 1, "import_key": import_key, "parser": PARSER_VERSION,
            "notification": parsed["notification"], "notification_evidence_location": "source/inspection.json#/notification_evidence",
            "numeric_verification": "original_pdf_text_cells_with_coordinates_and_subtotal_reconciliation",
            "cell_origins": [origins], "unit_evidence": {"total_assets": parsed["unit_evidence"]},
            "scope": "consolidated", "issuer": ISSUER, "issuer_code": "YFMEN",
            "statement_date": "2025-12-31", "purchasing_power_date": "2025-12-31",
            "row_checks": parsed["row_checks"],
            "date_normalization": {"operation": "annual_label_of_source_year_end_statement", "aggregation_performed": False,
                                   "original_date_column": "statement_date", "date_column": "year",
                                   "rows": [{"original": "2025-12-31", "normalized": "2025"}]},
        },
    }
    with tempfile.TemporaryDirectory(prefix="kap-yfmen-assets-") as temporary:
        csv_path = Path(temporary) / "assets.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(contract["columns"]))
            writer.writeheader()
            writer.writerow(observation)
        updated = store.ingest_csv(workspace_id, csv_path, contract, expected_version=workspace["version"])
    added = set(updated["datasets"]) - set(workspace["datasets"])
    if len(added) != 1:
        raise ValueError("Expected exactly one new verified KAP dataset")
    result = shared.promote(workspace_id, added.pop(), official_sources=OFFICIAL_SOURCE_REGISTRY,
                            reason="User-requested official KAP YFMEN 2025 consolidated total-assets stock; original PDF row and units verified")
    return {**result, "row_count": 1, "observations": parsed["observations"], "notification": parsed["notification"],
            "page": PDF_PAGE, "printed_page": 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Explicitly publish (default: download and inspect only)")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--workspace-id", help="Existing operator import workspace")
    args = parser.parse_args()
    if args.publish and (args.store is None or not args.workspace_id):
        parser.error("--publish requires --store and --workspace-id")
    notification_raw, pdf_raw = download()
    result = (publish(LakehouseStore(args.store), args.workspace_id, notification_raw, pdf_raw)
              if args.publish else parse_sources(notification_raw, pdf_raw))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
