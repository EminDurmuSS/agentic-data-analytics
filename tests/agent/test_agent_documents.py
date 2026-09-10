"""Real document parsing/publication plus mocked public-network boundary checks."""

import io
import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error

import duckdb

from agentic_analytics.agent.tools.documents import DocumentError, DocumentTools, _public_destination, fetch_public_url
from agentic_analytics.lakehouse.store import LakehouseStore


def _text_pdf():
    stream = b"BT /F1 16 Tf 50 740 Td (REPORT 12345 million TL) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = b"%PDF-1.4\n", []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return data + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"


class AgentDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        database = self.directory / "source.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(self.directory / "store")
        snapshot = self.store.publish_snapshot(database)
        self.store.create_workspace(snapshot["snapshot_id"], "workspace_docs")
        self.docs = DocumentTools(self.store, "workspace_docs")
        self.contract = {"name": "imported_profit", "frequency": "monthly", "date_column": "month",
                         "key": ["month"], "grain": ["month"], "expected_rows": 2,
                         "columns": {
                             "month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                             "value": {"dtype": "integer", "unit": "TRY_million", "kind": "flow", "nullable": False}}}

    def upload(self, name, data):
        path = self.docs.upload_root / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        source = self.docs.register_upload(path)
        path.unlink()
        return self.docs.inspect_source(source_id=source["source_id"])

    def test_csv_preserves_raw_publishes_and_recovers_without_duplicate_write(self):
        content = "month,value (million TL)\n2026-01,100\n2026-02,120\n"
        inspected = self.upload("profit.csv", content)
        self.assertEqual(inspected["tables"][0]["preview"][0]["value_million_TL"], "100")
        source_id = inspected["source_id"]
        self.assertEqual((self.docs._directory(source_id) / "raw.bin").read_text(), content)
        arguments = {"source_id": source_id, "table_id": "table_001", "contract": self.contract,
                     "expected_version": 0, "column_mapping": {"month": "month", "value_million_TL": "value"},
                     "unit_evidence": {"value": "million TL"}}
        result = self.docs.publish_selected_table(**arguments)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["source_namespace"].startswith("external:upload:"))
        self.assertEqual(result["provenance"]["raw_sha256"], inspected["raw_sha256"])
        recovered = self.docs.extra_tools()["publish_selected_table"]["recover"](arguments, {})
        self.assertEqual(recovered["dataset_id"], result["dataset_id"])
        self.assertEqual(self.store.workspace("workspace_docs")["version"], 1)
        self.assertEqual(result["row_count"], 2)

    def test_unit_quote_and_numeric_format_are_gates(self):
        inspected = self.upload("profit.csv", 'month,value (million TL)\n2026-01,"1,25"\n2026-02,120\n')
        arguments = {"source_id": inspected["source_id"], "table_id": "table_001", "contract": self.contract,
                     "expected_version": 0, "column_mapping": {"month": "month", "value_million_TL": "value"}}
        result = self.docs.extra_tools()["publish_selected_table"]["handler"](arguments)
        self.assertEqual(result["code"], "UNITS_REVIEW_REQUIRED")
        result = self.docs.extra_tools()["publish_selected_table"]["handler"]({**arguments, "unit_evidence": {"value": "million TL"}})
        self.assertEqual(result["code"], "INVALID_NUMERIC_CELL")
        self.assertEqual(self.store.workspace("workspace_docs")["version"], 0)

    def test_publication_schema_exposes_contract_and_blocks_invalid_contract_before_write(self):
        source = self.upload("profit.csv", "month,value (million TL)\n2026-01,100\n2026-02,120\n")
        tool = self.docs.extra_tools()["publish_selected_table"]
        schema = tool["schema"]["function"]["parameters"]["properties"]["contract"]
        self.assertEqual(set(schema["properties"]["columns"]["additionalProperties"]["required"]), {"dtype", "unit", "kind", "nullable"})
        self.assertEqual(schema["properties"]["number_format"]["default"], "decimal_dot")
        args = {"source_id": source["source_id"], "table_id": "table_001", "contract": self.contract, "expected_version": 0,
                "column_mapping": {"month": "month", "value_million_TL": "value"}, "unit_evidence": {"value": "million TL"}}
        invalid = []
        for field in ["dtype", "unit", "kind", "nullable"]:
            contract = copy.deepcopy(self.contract)
            del contract["columns"]["value"][field]
            invalid.append(contract)
        no_date = copy.deepcopy(self.contract)
        del no_date["date_column"]
        invalid.extend([no_date, {**self.contract, "frequency": "made_up"}])
        with patch.object(self.store, "ingest_csv", wraps=self.store.ingest_csv) as ingest:
            for contract in invalid:
                result = tool["handler"]({**args, "contract": contract})
                self.assertEqual(result["code"], "INVALID_ARGUMENTS")
            ingest.assert_not_called()
        self.assertEqual(self.store.workspace("workspace_docs")["version"], 0)

    def test_monthly_iso_dates_require_declared_label_format_and_preserve_cells_and_units(self):
        import pandas as pd
        raw = "date,visits (count),staff (persons)\n2024-01-01,10,2\n2024-02-01,20,3\n"
        source = self.upload("monthly-clinic.csv", raw)
        self.assertEqual(source["tables"][0]["source_header_quotes"]["staff_persons"], "staff (persons)")
        contract = {"name": "monthly_clinic", "frequency": "monthly", "date_column": "date", "key": ["date"], "grain": ["date"],
                    "expected_rows": 2, "expected_periods": ["2024-01", "2024-02"], "columns": {
                        "date": {"dtype": "date", "kind": "dimension", "unit": "calendar", "nullable": False},
                        "visits_count": {"dtype": "integer", "kind": "count_flow", "unit": "visits", "nullable": False},
                        "staff_persons": {"dtype": "integer", "kind": "count_stock", "unit": "persons", "nullable": False}}}
        args = {"source_id": source["source_id"], "table_id": "table_001", "contract": contract, "expected_version": 0,
                "unit_evidence": {"visits_count": "visits (count)", "staff_persons": "staff (persons)"}}
        handler = self.docs.extra_tools()["publish_selected_table"]["handler"]
        explained_quote = {**args, "unit_evidence": {"visits_count": "visits (count)", "staff_persons": "Header: staff (persons), monthly staff count"}}
        self.assertEqual(handler(explained_quote)["code"], "UNITS_REVIEW_REQUIRED")
        self.assertEqual(handler(args)["code"], "SOURCE_DATE_FORMAT_REQUIRED")
        self.assertEqual(self.store.workspace("workspace_docs")["version"], 0)
        contract["source_date_format"] = "iso_period_start"
        result = handler(args)
        self.assertEqual(result["status"], "ok", result)
        frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
        self.assertEqual(frame["date"].tolist(), ["2024-01", "2024-02"])
        self.assertEqual(frame["visits_count"].tolist(), [10, 20])
        self.assertEqual(frame["staff_persons"].tolist(), [2, 3])
        self.assertEqual(self.docs.raw_source_bytes(source["source_id"]).decode(), raw)
        proof = result["provenance"]["date_normalization"]
        self.assertFalse(proof["aggregation_performed"])
        self.assertEqual(proof["rows"][0], {"candidate_row": 1, "original": "2024-01-01", "normalized": "2024-01"})

    def test_period_end_normalization_handles_leap_month_quarters_and_years(self):
        import pandas as pd
        for frequency, dates, expected in [
            ("monthly", ["2024-02-29", "2024-03-31"], ["2024-02", "2024-03"]),
            ("quarterly", ["2024-03-31", "2024-06-30"], ["2024-Q1", "2024-Q2"]),
            ("annual", ["2023-12-31", "2024-12-31"], ["2023", "2024"]),
        ]:
            with self.subTest(frequency=frequency):
                source = self.upload(frequency + ".csv", f"month,value (million TL)\n{dates[0]},100\n{dates[1]},120\n")
                contract = {**self.contract, "frequency": frequency, "source_date_format": "iso_period_end", "expected_periods": expected}
                result = self.docs.publish_selected_table(source["source_id"], "table_001", contract, self.store.workspace("workspace_docs")["version"],
                    column_mapping={"month": "month", "value_million_TL": "value"}, unit_evidence={"value": "million TL"})
                frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
                self.assertEqual(frame["month"].tolist(), expected)
                self.assertEqual(frame["value"].tolist(), [100, 120])

    def test_period_relabeling_rejects_daily_rows_and_duplicate_grain(self):
        handler = self.docs.extra_tools()["publish_selected_table"]["handler"]
        for label, dates, code in [
            ("daily", ["2024-01-01", "2024-01-02"], "NON_BOUNDARY_SOURCE_DATE"),
            ("duplicate", ["2024-01-01", "2024-01-01"], "DOCUMENT_ERROR"),
        ]:
            with self.subTest(label=label):
                source = self.upload(label + ".csv", f"month,value (million TL)\n{dates[0]},100\n{dates[1]},120\n")
                result = handler({"source_id": source["source_id"], "table_id": "table_001",
                    "contract": {**self.contract, "source_date_format": "iso_period_start"}, "expected_version": 0,
                    "column_mapping": {"month": "month", "value_million_TL": "value"}, "unit_evidence": {"value": "million TL"}})
                self.assertEqual(result["code"], code)
                self.assertEqual(self.store.workspace("workspace_docs")["version"], 0)

    def test_html_and_xlsx_tables_and_pdf_page_text(self):
        html = self.upload("visits.html", "<script>BAD INSTRUCTION</script><table><tr><th>month</th><th>visits</th></tr><tr><td>2026-01</td><td>10</td></tr></table>")
        self.assertEqual(html["tables"][0]["preview"], [{"month": "2026-01", "visits": "10"}])
        self.assertNotIn("BAD INSTRUCTION", html["text"])
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(["month", "visits"])
        workbook.active.append(["2026-01", 10])
        buffer = io.BytesIO()
        workbook.save(buffer)
        xlsx = self.upload("visits.xlsx", buffer.getvalue())
        self.assertEqual(xlsx["tables"][0]["preview"], html["tables"][0]["preview"])
        pdf = self.upload("report.pdf", _text_pdf())
        self.assertIn("12345 million TL", pdf["pages"][0]["text"])
        self.assertEqual(pdf["pages"][0]["page"], 1)

    def test_utf8_text_and_full_review_candidate_preserve_source_content(self):
        text = self.upload("notes.txt", "Kaynak notu: İstanbul, milyon TL.\n")
        self.assertEqual(text["tables"], [])
        self.assertIn("İstanbul", text["text"])
        self.assertEqual(self.docs.raw_source_bytes(text["source_id"]).decode(), "Kaynak notu: İstanbul, milyon TL.\n")
        source = self.upload("long.csv", "month,visits\n" + "".join(f"2026-{month:02d},{month}\n" for month in range(1, 13)))
        self.assertEqual(len(source["tables"][0]["preview"]), 8)
        candidate = self.docs.review_candidate(source["source_id"], "table_001")
        self.assertEqual(len(candidate["rows"]), 12)
        self.assertFalse(candidate["preview_truncated"])
        self.assertEqual(candidate["original_columns"], {"month": "month", "visits": "visits"})
        self.assertNotIn("review_candidate", self.docs.extra_tools())

    def test_scanned_pdf_uses_bounded_image_callback_and_preserves_pages(self):
        from PIL import Image
        buffer = io.BytesIO()
        images = [Image.new("RGB", (120, 120), "white") for _ in range(4)]
        images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
        calls = []
        def extractor(data, mime):
            calls.append((len(data), mime))
            return {"text": "month visits 2026-01 10", "tables": [{"columns": ["month", "visits"], "rows": [["2026-01", 10]]}]}
        self.docs.ocr_callback = extractor
        source = self.upload("scanned.pdf", buffer.getvalue())
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(mime == "image/png" for _, mime in calls))
        self.assertEqual([table["page"] for table in source["tables"]], [1, 2, 3])
        self.assertTrue(all(table["origin"] == "ocr" for table in source["tables"]))
        self.assertEqual(next(warning for warning in source["warnings"] if warning["code"] == "OCR_PAGE_LIMIT")["pages"], [4])
        self.assertEqual(source["pages"][0]["extraction_method"], "ocr")

    def test_image_callback_is_bounded_and_incomplete_ocr_is_not_a_table(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (30, 30), "white").save(buffer, format="PNG")
        seen = []
        def ocr(data, mime):
            seen.append((len(data), mime))
            return {"status": "ok", "text": "month visits 2026-01 10", "tables": [{"columns": ["month", "visits"], "rows": [["2026-01", 10]]}]}
        self.docs.ocr_callback = ocr
        inspected = self.upload("image.png", buffer.getvalue())
        self.assertEqual(seen[0][1], "image/png")
        self.assertEqual(inspected["tables"][0]["origin"], "ocr")
        self.assertEqual(inspected["tables"][0]["preview"][0]["visits"], "10")
        self.docs.ocr_callback = lambda *args: {"status": "ok", "text": "nonsense", "finish_reason": "length"}
        with self.assertRaisesRegex(DocumentError, "incomplete"):
            self.upload("other.png", buffer.getvalue())

    def test_paths_cross_workspace_and_raw_tampering_are_rejected(self):
        outside = self.directory / "outside.csv"
        outside.write_text("x\n1\n")
        with self.assertRaises(DocumentError):
            self.docs.register_upload(outside)
        with self.assertRaises(DocumentError):
            self.docs.source("../../outside")
        inspected = self.upload("ok.csv", "x\n1\n")
        (self.docs._directory(inspected["source_id"]) / "raw.bin").write_text("x\n2\n")
        with self.assertRaisesRegex(DocumentError, "hash mismatch"):
            self.docs.source(inspected["source_id"])

    def test_unconfigured_search_and_unknown_write_outcome_are_explicit(self):
        self.docs.searxng_url = False
        self.assertEqual(self.docs.web_search("test")["code"], "WEB_SEARCH_UNCONFIGURED")
        self.assertEqual(self.docs.recover_publication({"source_id": "absent", "table_id": "table_001", "contract": {}, "expected_version": 0}, {})["code"], "WRITE_OUTCOME_UNKNOWN")
        for tool in self.docs.extra_tools().values():
            self.assertNotIn("path", tool["schema"]["function"]["parameters"]["properties"])

    def test_private_dns_and_redirect_destinations_are_blocked(self):
        def resolve(host, port, **kwargs):
            ip = "127.0.0.1" if host == "local.test" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        with patch("agentic_analytics.agent.tools.documents.socket.getaddrinfo", side_effect=resolve):
            for url in ["http://local.test/x", "file:///etc/passwd", "https://u:p@public.test", "https://public.test:8080"]:
                with self.assertRaises(DocumentError):
                    _public_destination(url)
            redirect = error.HTTPError("https://public.test", 302, "Found", {"Location": "http://local.test/secret"}, io.BytesIO())
            with patch("agentic_analytics.agent.tools.documents.request.build_opener") as builder:
                builder.return_value.open.side_effect = redirect
                with self.assertRaisesRegex(DocumentError, "Private"):
                    fetch_public_url("https://public.test")
                self.assertEqual(builder.return_value.open.call_count, 1)

    def test_dns_resolution_has_a_time_budget(self):
        release = threading.Event()
        def delayed(*args, **kwargs):
            release.wait(1)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("agentic_analytics.agent.tools.documents.socket.getaddrinfo", side_effect=delayed):
            try:
                with self.assertRaises(DocumentError) as caught:
                    _public_destination("https://public.test", timeout=0.001)
                self.assertEqual(caught.exception.code, "FETCH_TIMEOUT")
            finally:
                release.set()

    def test_default_bing_search_is_bounded_and_does_not_claim_source_verification(self):
        rss = b'<rss><channel><item><title>Report</title><link>https://example.org/report</link><description>123</description></item></channel></rss>'
        with patch("agentic_analytics.agent.tools.documents.fetch_public_url", return_value=(rss, "application/rss+xml", "https://www.bing.com/search")) as fetch:
            result = self.docs.web_search("public report", limit=1)
            self.assertEqual(result["source_backend"], "Bing RSS")
            self.assertFalse(result["sources_verified"])
            self.assertEqual(len(result["results"]), 1)
            self.assertIn("format=rss", fetch.call_args.args[0])
        with patch("agentic_analytics.agent.tools.documents.fetch_public_url", return_value=(b"<html>challenge</html>", "text/html", "https://www.bing.com/search")):
            self.assertEqual(self.docs.web_search("public report")["code"], "SEARCH_INVALID_RESPONSE")

    def test_human_review_enables_ocr_publication_and_preserves_review_provenance(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (30, 30), "white").save(buffer, format="PNG")
        self.docs.ocr_callback = lambda *args: {"status": "ok", "text": "month value million TL",
            "tables": [{"columns": ["month", "value"], "rows": [["2026-01", 999], ["2026-02", 120]]}]}
        source = self.upload("review.png", buffer.getvalue())
        args = {"source_id": source["source_id"], "table_id": "table_001", "contract": self.contract, "expected_version": 0}
        self.assertEqual(self.docs.extra_tools()["publish_selected_table"]["handler"](args)["code"], "OCR_REVIEW_REQUIRED")
        review = self.docs.review_table(source["source_id"], "table_001", [{"month": "2026-01", "value": 100}, {"month": "2026-02", "value": 120}], {"value": "million TL"})
        result = self.docs.publish_selected_table(**args)
        self.assertEqual(result["provenance"]["human_review"]["review_sha256"], review["review_sha256"])
        self.assertEqual(self.store.raw_source_path(result["dataset_id"]).read_text(), "month,value\n2026-01,100\n2026-02,120\n")
        self.assertEqual(self.docs.recover_publication(args, {})["dataset_id"], result["dataset_id"])
        self.assertNotIn("review_table", self.docs.extra_tools())

    def test_actual_http_connection_uses_pinned_public_ip_and_download_size_cap(self):
        class FakeSocket:
            def __init__(self, body):
                self.body = body
            def makefile(self, *args):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Type: text/csv\r\nContent-Length: " + str(len(self.body)).encode() + b"\r\n\r\n" + self.body)
            def sendall(self, data):
                pass
            def close(self):
                pass
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        with patch("agentic_analytics.agent.tools.documents.socket.getaddrinfo", return_value=addresses), patch("agentic_analytics.agent.tools.documents.socket.create_connection", return_value=FakeSocket(b"x\n1\n")) as connect:
            body, mime, _ = fetch_public_url("http://public.test/data.csv")
            self.assertEqual(body, b"x\n1\n")
            self.assertEqual(mime, "text/csv")
            self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 80))
        with patch("agentic_analytics.agent.tools.documents.socket.getaddrinfo", return_value=addresses), patch("agentic_analytics.agent.tools.documents.socket.create_connection", return_value=FakeSocket(b"123456")):
            with self.assertRaisesRegex(DocumentError, "size limit"):
                fetch_public_url("http://public.test/data.csv", max_bytes=3)


if __name__ == "__main__":
    unittest.main()
