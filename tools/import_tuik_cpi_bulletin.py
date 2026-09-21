"""Operator-only import of the rendered official December 2025 CPI bulletin.

The portal serves a JavaScript shell to plain HTTP clients. Supply an unchanged
HTML capture of the anonymously rendered official page, not search snippets or
hand-assembled numbers. The capture is preserved and labelled rendered DOM (not
an original HTTP response). No inferred index levels or rebasing are imported.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import tempfile

from bs4 import BeautifulSoup

from agentic_analytics.agent.tools.documents import DocumentTools, OFFICIAL_SOURCE_REGISTRY
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore, _write_json

SOURCE_URL = "https://veriportali.tuik.gov.tr/tr/press/58294"
TITLE = "Tüketici Fiyat Endeksi, Aralık 2025"
TABLE_TITLE = "TÜFE değişim oranları (%), Aralık 2025"
PARSER_VERSION = "tuik-cpi-58294-rendered-table-v1"
MAX_CAPTURE = 2 * 1024**2
MEASURES = {
    "monthly_change": "Bir önceki aya göre değişim oranı",
    "year_to_date_change": "Bir önceki yılın Aralık ayına göre değişim oranı",
    "annual_change": "Bir önceki yılın aynı ayına göre değişim oranı",
    "twelve_month_average_change": "On iki aylık ortalamalara göre değişim oranı",
}
SEMANTICS = (
    "TÜİK TÜFE (2003=100) Aralık 2025 bülteninde yayımlanan yüzde değişim oranları; "
    "endeks seviyesi değildir. Yıllık değişim aynı ayın önceki yılına göre; aylık "
    "değişim önceki aya göre hesaplanır. On iki aylık ortalama değişimi farklı bir "
    "ölçüdür. 2025=100 bazlı EVDS endeks seviyesiyle doğrudan eşitlenmez veya "
    "ortalaması alınmaz. Kaynakta yayımlanan değerler, yeni hesap yapılmadan alınır."
)


def _text(element):
    return " ".join(element.get_text(" ", strip=True).split())


def parse_capture(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_CAPTURE:
        raise ValueError("Invalid rendered bulletin capture size")
    soup = BeautifulSoup(raw.decode("utf-8", errors="strict"), "html.parser")
    text = _text(soup)
    for required in (TITLE, TABLE_TITLE, "TÜFE'deki (2003=100)", "Sayı: 58294"):
        if required not in text:
            raise ValueError(f"Official bulletin identity or unit is missing: {required}")
    published = re.search(r"Yayım Tarihi:\s*(\d{2}) Ocak (\d{4}) (\d{2}:\d{2})", text)
    if not published:
        raise ValueError("Publication timestamp is missing")
    published_at = datetime.strptime(" ".join(published.groups()), "%d %Y %H:%M").replace(month=1).isoformat()
    if published_at != "2026-01-05T10:00:00":
        raise ValueError("Unexpected publication timestamp for bulletin 58294")
    candidates = []
    for index, table in enumerate(soup.select("table"), 1):
        rows = [[_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]
                for row in table.find_all("tr")]
        if rows and rows[0] == ["", "Aralık 2025", "Aralık 2024", "Aralık 2023"]:
            candidates.append((index, rows))
    if len(candidates) != 1:
        raise ValueError("Expected one uniquely identified CPI percentage-change table")
    index, rows = candidates[0]
    if len(rows) != 5 or any(len(row) != 4 for row in rows):
        raise ValueError("CPI comparison table shape changed")
    values, origins = {}, {}
    for row_number, ((key, label), row) in enumerate(zip(MEASURES.items(), rows[1:]), 2):
        if row[0] != label:
            raise ValueError("CPI change definitions or order changed")
        # All comparison cells must remain plain, unambiguous percentages.
        for value in row[1:]:
            if not re.fullmatch(r"-?\d{1,3},\d{2}", value):
                raise ValueError("Ambiguous percentage cell")
            if not Decimal("-100") < Decimal(value.replace(",", ".")) < Decimal("1000"):
                raise ValueError("Percentage outside accepted source range")
        values[key] = str(Decimal(row[1].replace(",", ".")))
        origins[key] = {"html_table": index, "source_row": row_number, "source_column": 2,
                        "row_label": label, "column_label": "Aralık 2025", "original_value": row[1],
                        "transform": "decimal_comma_to_point_only"}
    if values["annual_change"] != values["year_to_date_change"]:
        raise ValueError("December annual and year-to-date cells contradict each other")
    return {"source_url": SOURCE_URL, "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "published_at": published_at, "publication_timezone": "Europe/Istanbul",
            "title": TITLE, "base_year": "2003=100", "period": "2025-12",
            "values": values, "origins": origins, "html_table": index,
            "original_rows": rows, "semantics": SEMANTICS}


def publish(store: LakehouseStore, workspace_id: str, raw: bytes) -> dict:
    parsed = parse_capture(raw)
    workspace = store.workspace(workspace_id)
    import_key = f"{PARSER_VERSION}:{parsed['raw_sha256']}"
    for dataset in workspace["datasets"]:
        provenance = store.dataset_manifest(dataset)["contract"].get("document_provenance", {})
        if provenance.get("import_key") == import_key:
            return SharedLakehouse(store).promote(workspace_id, dataset,
                official_sources=OFFICIAL_SOURCE_REGISTRY, reason="Identical validated CPI bulletin capture")
    documents = DocumentTools(store, workspace_id, searxng_url=False)
    source = documents._register(raw, "tuik-58294-rendered.html", "text/html", SOURCE_URL)
    columns = ["period", *MEASURES]
    row = {"period": parsed["period"], **parsed["values"]}
    capture = {"method": "operator_observed_anonymous_browser_rendered_dom",
               "original_http_response": False, "capture_scope": "main.outerHTML",
               "url": SOURCE_URL, "authentication_required": False}
    inspection = {"source_id": source["source_id"], "parser": PARSER_VERSION,
                  "capture": capture, "text": TITLE + "\n" + SEMANTICS + "\n" + "\n".join(
                      " | ".join(r) for r in parsed["original_rows"]),
                  "pages": [], "processed_pages": [], "text_truncated": False,
                  "warnings": [SEMANTICS, "Rendered DOM capture, not original HTTP body; other bulletin tables were not imported."],
                  "tables": [{"table_id": "table_001", "origin": "parsed", "columns": columns,
                              "original_columns": {"period": "Aralık 2025", **MEASURES},
                              "rows": [[row[c] for c in columns]], "row_count": 1,
                              "cell_origins": [parsed["origins"]],
                              "original_table": parsed["original_rows"]}]}
    _write_json(documents._directory(source["source_id"]) / "inspection.json", inspection)
    contract = {"name": "TÜİK TÜFE Aralık 2025 yıllık aylık değişim oranları resmî 58294 bülteni (2003=100)",
                "frequency": "monthly", "date_column": "period", "key": ["period"], "grain": ["period"],
                "expected_rows": 1, "expected_periods": ["2025-12"], "source_namespace": source["source_namespace"],
                "columns": {"period": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    **{key: {"dtype": "float", "unit": "percent", "kind": "rate", "scale": 1,
                             "aggregation": "none", "additive_over_time": False, "nullable": False,
                             "source_semantics": label + ". " + SEMANTICS} for key, label in MEASURES.items()}},
                "document_provenance": {"source_id": source["source_id"], "table_id": "table_001",
                    "source_url": SOURCE_URL, "raw_sha256": parsed["raw_sha256"], "parser": PARSER_VERSION,
                    "import_key": import_key, "capture": capture, "published_at": parsed["published_at"],
                    "publication_timezone": parsed["publication_timezone"], "base_year": parsed["base_year"],
                    "reported_period": parsed["period"], "source_title": TITLE,
                    "cell_origins": [parsed["origins"]], "unit_evidence": TABLE_TITLE,
                    "numeric_verification": "original_rendered_html_table_cells_not_snippets",
                    "semantic_warning": SEMANTICS}}
    with tempfile.TemporaryDirectory(prefix="tuik-cpi-bulletin-") as temporary:
        path = Path(temporary) / "cpi_changes.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerow(row)
        updated = store.ingest_csv(workspace_id, path, contract, expected_version=workspace["version"])
    added = set(updated["datasets"]) - set(workspace["datasets"])
    if len(added) != 1:
        raise ValueError("Expected exactly one new CPI dataset")
    result = SharedLakehouse(store).promote(workspace_id, added.pop(),
        official_sources=OFFICIAL_SOURCE_REGISTRY, reason="User-requested verified TÜİK CPI bulletin 58294")
    return {**result, "row_count": 1, "values": parsed["values"], "published_at": parsed["published_at"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True, help="Unmodified rendered main HTML from the official page")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--workspace-id")
    args = parser.parse_args()
    if args.publish and (not args.store or not args.workspace_id):
        parser.error("--publish requires --store and --workspace-id")
    if args.html.stat().st_size > MAX_CAPTURE:
        parser.error("HTML capture exceeds size limit")
    raw = args.html.read_bytes()
    result = publish(LakehouseStore(args.store), args.workspace_id, raw) if args.publish else parse_capture(raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
