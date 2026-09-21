"""KAP importer uses actual PDF cells; synthetic fixtures are not official data."""
import base64
import gzip
import hashlib
import json

import duckdb
import httpx
import pytest

from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore
from tools.import_kap_financial_assets import (
    ISSUER, JAVA_BYTE_ARRAY_PREFIX, NOTIFICATION_URL, PDF_FILENAME, PDF_URL,
    parse_sources, publish,
)
from tools import import_kap_financial_assets as importer


def notification():
    return f'''<html><body><h1>{ISSUER}</h1><p>YAT, YFMEN</p>
    <div>Gönderim Tarihi <span>05.03.2026</span> <span>21:39:59</span></div>
    <div>Bildirim Tipi FR Yıl 2025 Periyot Yıllık</div>
    <div>Sunum Para Birimi TL Finansal Tablo Niteliği Konsolide</div>
    <a href="{PDF_URL}">{PDF_FILENAME}</a></body></html>'''.encode()


PAGE_TEXT = '''YATIRIM FINANSMAN MENKUL DEGERLER A.S. VE BAGLI ORTAKLIGI
31 ARALIK 2025 TARIHI ITIBARIYLA KONSOLIDE FINANSAL DURUM TABLOSU
(Tutarlar aksi belirtilmedikce Turk Lirasi'nin ("TL") 31 Aralik 2025 tarihi itibariyla satin alma gucu esasina gore TL
olarak ifade edilmistir.)
                                               Cari donem        Onceki donem
VARLIKLAR                                      31 Aralik 2025    31 Aralik 2024
Toplam Donen Varliklar                          1.000.000         2.000.000
Toplam Duran Varliklar                          1.000             2.000
TOPLAM VARLIKLAR                                1.001.000         2.002.000
1'''


def pdf_source(text=PAGE_TEXT, count=72):
    # Minimal real text-layer PDF, no optional reportlab/font dependency. ASCII
    # transliteration of Turkish tests normalized matching; values are synthetic.
    pages = ["Synthetic test page"] * count
    if count >= 8:
        pages[7] = text
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(count))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    for index, page in enumerate(pages):
        escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in page.splitlines()]
        stream = ("BT /F1 7 Tf 32 760 Td\n" + "\n".join(f"({line}) Tj 0 -15 Td" for line in escaped) + "\nET").encode("ascii")
        objects.extend([f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>".encode(),
                        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"])
    raw, offsets = b"%PDF-1.4\n", []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(raw)
    raw += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return raw + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_reads_pdf_cells_not_notification_title_or_known_answer():
    html = notification().replace(b"</body>", b"<p>TOPLAM VARLIKLAR 999.999.999</p></body>")
    raw = pdf_source()
    parsed = parse_sources(html, raw)
    assert parsed["observations"][0]["total_assets"] == 1_001_000
    assert parsed["row_checks"]["TOPLAM VARLIKLAR"]["prior"] == 2_002_000
    assert parsed["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert parsed["page"] == 8 and parsed["printed_page"] == 1
    assert parsed["scale"] == 1 and parsed["kind"] == "stock"
    assert parsed["notification"]["published_at"] == "2026-03-05T21:39:59+03:00"
    assert parsed["cell_origins"][0]["source_text"] == "1.001.000"
    assert parsed["cell_origins"][0]["bbox"][0] < parsed["cell_origins"][1]["bbox"][0]


def test_handles_only_exact_kap_java_byte_array_envelope():
    document = pdf_source()
    wrapped = JAVA_BYTE_ARRAY_PREFIX + len(document).to_bytes(4, "big") + document
    parsed = parse_sources(notification(), wrapped)
    assert parsed["raw_sha256"] == hashlib.sha256(wrapped).hexdigest()
    assert parsed["inner_pdf_sha256"] == hashlib.sha256(document).hexdigest()
    assert parsed["inner_pdf_size_bytes"] == len(document)
    assert parsed["transport_envelope"] == "java_serialized_byte_array_27_byte_prefix"
    with pytest.raises(ValueError, match="length"):
        parse_sources(notification(), JAVA_BYTE_ARRAY_PREFIX + (len(document) + 1).to_bytes(4, "big") + document)
    with pytest.raises(ValueError, match="real PDF"):
        parse_sources(notification(), b"<html>login" + document)


def test_normalizes_only_near_zero_glyph_shear():
    class Page:
        chars = [{"matrix": (0.75, -2e-8, -1.7e-8, 1.25, 0, 0), "upright": False, "text": "7"}]
    assert importer._horizontal_chars(Page())[0]["upright"] is True
    assert Page.chars[0]["upright"] is False  # Original glyph evidence unchanged.
    Page.chars = [{"matrix": (1, 0.1, 0, 1, 0, 0), "text": "7"}]
    with pytest.raises(ValueError, match="rotated"):
        importer._horizontal_chars(Page())


@pytest.mark.parametrize("status,body,error", [(302, b"", httpx.HTTPStatusError),
                                               (200, b"x" * 101, ValueError)])
def test_download_rejects_redirects_and_oversized_bodies(monkeypatch, status, body, error):
    requests = []
    original_client = httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(status, content=body, headers={"Location": "https://example.com/untrusted"})
    monkeypatch.setattr(importer.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(importer, "MAX_DOWNLOAD", 100)
    with pytest.raises(error):
        importer.download()
    assert [str(request.url) for request in requests] == [NOTIFICATION_URL]
    assert "Authorization" not in requests[0].headers


@pytest.mark.parametrize("old,new", [
    ("YATIRIM FINANSMAN", "OTHER ISSUER"),
    ("VE BAGLI ORTAKLIGI", "SOLO"),
    ("KONSOLIDE FINANSAL", "SOLO FINANSAL"),
    ("31 ARALIK 2025", "30 HAZIRAN 2025"),
    ("gore TL", "gore bin TL"),
    ("(\"TL\")", "(\"USD\")"),
    ("31 Aralik 2025 tarihi itibariyla", "31 Aralik 2024 tarihi itibariyla"),
    ("Cari donem        Onceki donem", "Onceki donem      Cari donem"),
    ("31 Aralik 2025    31 Aralik 2024", "31 Aralik 2024    31 Aralik 2025"),
    ("1.001.000", "1.001,000"),
    ("1.001.000", "1.001.001"),
    ("TOPLAM VARLIKLAR", "TOPLAM KAYNAKLAR"),
    ("\n1", "\n54"),
])
def test_rejects_wrong_pdf_identity_scope_period_units_layout_and_totals(old, new):
    with pytest.raises(ValueError):
        parse_sources(notification(), pdf_source(PAGE_TEXT.replace(old, new)))


@pytest.mark.parametrize("old,new", [
    (ISSUER, "OTHER ISSUER"), ("YFMEN", "AKFIN"),
    ("Bildirim Tipi FR", "Bildirim Tipi ÖDA"), ("Yıl 2025", "Yıl 2024"),
    ("Periyot Yıllık", "Periyot 6 Aylık"), ("Konsolide", "Konsolide Olmayan"),
    ("Sunum Para Birimi TL", "Sunum Para Birimi 1000 TL"),
    ("05.03.2026", "06.03.2026"), (PDF_URL, "https://example.com/wrong.pdf"),
    (PDF_FILENAME, "wrong.pdf"),
])
def test_rejects_wrong_notification_metadata_or_attachment_chain(old, new):
    with pytest.raises(ValueError):
        parse_sources(notification().decode().replace(old, new).encode(), pdf_source())


@pytest.mark.parametrize("raw", [b"", b"<html>login</html>", b"%PDF-1.7\ninvalid", pdf_source(count=8)])
def test_rejects_malformed_pdf(raw):
    with pytest.raises(ValueError):
        parse_sources(notification(), raw)


def test_rejects_duplicate_total_assets_rows():
    repeated = PAGE_TEXT.replace("\n1", "\nTOPLAM VARLIKLAR 1.001.000 2.002.000\n1")
    with pytest.raises(ValueError, match="ambiguous PDF row"):
        parse_sources(notification(), pdf_source(repeated))


@pytest.mark.parametrize("kwargs", [{"notification_url": "https://example.com/1566753"},
                                     {"pdf_url": "https://www.kap.org.tr/tr/api/file/download/other"}])
def test_rejects_wrong_source_url(kwargs):
    with pytest.raises(ValueError, match="source URL"):
        parse_sources(notification(), pdf_source(), **kwargs)


def test_publication_preserves_raw_chain_is_idempotent_and_loads_fresh_workspace(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    store.create_workspace(snapshot, "workspace_import")
    before = store.create_workspace(snapshot, "workspace_before")
    html, document = notification(), pdf_source()
    raw = JAVA_BYTE_ARRAY_PREFIX + len(document).to_bytes(4, "big") + document
    published = publish(store, "workspace_import", html, raw)
    shared = SharedLakehouse(store)
    assert shared.promotion_file(published["promotion_id"], "source/raw.bin").read_bytes() == raw
    packaged = json.loads(shared.promotion_file(published["promotion_id"], "source/inspection.json").read_bytes())
    evidence = packaged["notification_evidence"]
    assert gzip.decompress(base64.b64decode(evidence["raw_data"])) == html
    assert evidence["raw_sha256"] == hashlib.sha256(html).hexdigest()
    assert packaged["inner_pdf_sha256"] == hashlib.sha256(document).hexdigest()
    assert store.workspace("workspace_before") == before
    changed_html = html.replace(b"</body>", b"<p>Unrelated dynamic site state changed.</p></body>")
    assert publish(store, "workspace_import", changed_html, raw)["publication_performed"] is False
    assert publish(store, "workspace_before", changed_html, raw)["publication_performed"] is False
    assert store.workspace("workspace_before") == before
    release = shared.current_release()
    assert release["dataset_ids"] == [published["dataset_id"]]
    store.create_workspace(snapshot, "workspace_fresh", initial_dataset_ids=release["dataset_ids"],
                           shared_release_id=release["release_id"])
    service = LakehouseService(store, "workspace_fresh")
    metric = published["metric_ids"][0]
    found = service.discover({"query": "KAP YFMEN toplam varlıklar", "limit": 5})["metrics"]
    assert [item["metric_id"] for item in found] == [metric]
    assert found[0]["source_system"] == "SESSION_DATASET"
    assert found[0]["source_url"] == PDF_URL
    assert published["official_source"]["domain"] == "kap.org.tr"
    descriptor = service.describe({"metric_id": metric})["metric"]
    assert descriptor["native_frequency"] == "annual"
    assert descriptor["kind"] == "stock" and descriptor["unit"] == "TRY"
    assert descriptor["scale"] == 1 and descriptor["currency"] == "TRY"
    assert descriptor["price_basis"] == "2025-12-31"
    result = service.execute({"start": "2025", "end": "2025", "frequency": "annual",
                              "columns": [{"name": "total_assets", "metric_id": metric, "dimensions": {}}]})
    assert result["row_count"] == 1
    assert result["preview"][0]["total_assets"] == 1_001_000
    proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "total_assets", "period": "2025"})
    provenance = proof["lineage"]["document_provenance"]
    assert provenance["source_url"] == PDF_URL
    assert provenance["notification"]["source_url"] == NOTIFICATION_URL
    assert provenance["page"] == 8 and provenance["printed_page"] == 1
    assert provenance["scope"] == "consolidated"
    assert provenance["cell_origins"][0]["total_assets"]["column_header"] == "31 Aralık 2025"
    assert provenance["date_normalization"]["aggregation_performed"] is False
    inspected = DocumentTools(store, "workspace_import", searxng_url=False).inspect_source(source_id=provenance["source_id"], page_numbers=[8])
    assert inspected["tables"][0]["preview"][0]["total_assets"] == 1_001_000
    assert inspected["processed_pages"] == [8]
    assert inspected["total_pages"] == 72


def test_invalid_evidence_does_not_modify_workspace_or_create_shared_release(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    before = store.create_workspace(snapshot, "workspace_import")
    with pytest.raises(ValueError):
        publish(store, "workspace_import", notification(), pdf_source(PAGE_TEXT.replace("gore TL", "gore bin TL")))
    assert store.workspace("workspace_import") == before
    assert not (store.root / "shared" / "CURRENT.json").exists()
