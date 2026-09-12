"""Long-document page discovery must preserve literal source evidence."""
import io

from PIL import Image
import pytest

from agentic_analytics.agent.tools.source_index import SourceIndexTools
from agentic_analytics.agent.tools.documents import DocumentTools
from app.context import AppContext


def test_find_note_beyond_default_inspection_and_preserve_bounded_continuation(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    buffer = io.BytesIO()
    pdf = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    for number in range(1, 36):
        page = pdf.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        title = "Revenue and Cost of Sales" if number == 34 else f"Unrelated note {number}"
        text = f"BT /F1 12 Tf 40 750 Td ({title}) Tj ET"
        if number == 34:
            text += " BT /F1 12 Tf 40 720 Td (Revenue 2025: 123456; Revenue 2024: 145678) Tj ET"
        stream = DecodedStreamObject()
        stream.set_data(text.encode())
        page[NameObject("/Contents")] = pdf._add_object(stream)
    pdf.write(buffer)
    context = AppContext(tmp_path / "runtime", None)
    try:
        workspace = context.create_workspace("PDF search", "generic")
        docs = DocumentTools(context.store, workspace["workspace_id"])
        uploaded = docs.upload_root / "long.pdf"
        uploaded.write_bytes(buffer.getvalue())
        source = docs.register_upload(uploaded)
        search = SourceIndexTools(context.store, workspace["workspace_id"])
        first = search.find_source_pages(source["source_id"], "Revenue and Cost of Sales", page_limit=30)
        assert not first["complete"] and first["next_start_page"] == 31 and first["matches"] == []
        result = search.find_source_pages(source["source_id"], "Revenue and Cost of Sales")
        assert result["complete"] and result["matches"][0]["page"] == 34
        assert "Revenue 2025: 123456" in result["matches"][0]["excerpt"]
        before = docs.source(source["source_id"])
        assert result["raw_sha256"] == before["raw_sha256"]
        assert context.store.workspace(workspace["workspace_id"])["version"] == 0
        other = context.create_workspace("Other", "generic")
        with pytest.raises(FileNotFoundError):
            SourceIndexTools(context.store, other["workspace_id"]).find_source_pages(source["source_id"], "Revenue")
    finally:
        context.pool.shutdown(wait=True)


def test_image_only_page_is_explicitly_not_searched(tmp_path):
    context = AppContext(tmp_path / "runtime", None)
    try:
        workspace = context.create_workspace("Scanned search", "generic")
        docs = context.documents(workspace["workspace_id"])
        uploaded = docs.upload_root / "scan.pdf"
        Image.new("RGB", (40, 40), "white").save(uploaded, format="PDF")
        source = docs.register_upload(uploaded)
        result = SourceIndexTools(context.store, workspace["workspace_id"]).find_source_pages(source["source_id"], "Revenue")
        assert result["image_only_pages"] == [1] and not result["complete"]
        assert result["matches"] == [] and result["warnings"]
    finally:
        context.pool.shutdown(wait=True)
