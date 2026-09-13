"""Business selections compile financial facts without caller-authored ETL."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.financial_import import FinancialImportTools
from agentic_analytics.agent.tools.datasets import DatasetTools
from agentic_analytics.lakehouse.store import LakehouseStore


class FinancialImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        database = self.root / "seed.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(self.root / "store")
        snapshot = self.store.publish_snapshot(database)
        self.store.create_workspace(snapshot["snapshot_id"], "financial_import")
        self.docs = DocumentTools(self.store, "financial_import")
        self.compiler = FinancialImportTools(self.docs)
        self.handler = self.compiler.extra_tools()["ingest_source_table"]["handler"]

    def source(self, rows, columns=None, caption="Amounts expressed in thousands of Turkish Lira (TRY)", title="Consolidated balance sheet"):
        columns = columns or ["label", "first", "second"]
        table = "<table><tr>" + "".join(f"<th>{column}</th>" for column in columns) + "</tr>"
        table += "".join("<tr>" + "".join(f"<td>{value or ''}</td>" for value in row) + "</tr>" for row in rows) + "</table>"
        raw = f"<html><h1>{title}</h1><p>{caption}</p>{table}</html>".encode()
        source = self.docs._register(raw, "statement.html", "text/html")
        inspection = self.docs.inspect_source(source_id=source["source_id"])
        return {"source_id": source["source_id"], "table_id": inspection["tables"][0]["table_id"], "expected_version": 0}

    def basic(self, **kwargs):
        return self.source([[None, "31 December 2025", "31 March 2026"], ["Total assets", "1,234,567", "2,345,678"],
                            ["Cash", "123456", "234567"]], **kwargs)

    def pdf_note(self, heading=None, current_header="Current Period", date_rows=None, section_titles=None, note_titles=None, footnotes=None):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer, buffer = PdfWriter(), io.BytesIO()
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        for number in (1, 2):
            page = writer.add_blank_page(width=612, height=792)
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            commands = []
            def text(x, y, value):
                escaped = value.replace("(", r"\(").replace(")", r"\)")
                commands.append(f"BT /F1 10 Tf {x} {y} Td ({escaped}) Tj ET")
            for index, line in enumerate(heading if number == 2 and heading is not None else [
                    "Consolidated Financial Report", "for the Three-Month Period Ended 30 June 2027" if number == 2 else
                    "as of 31 December 2026", "Thousands of Turkish Lira (TL)"]):
                text(40, 750 - index * 15, line)
            if number == 1:
                for index, line in enumerate(section_titles or []):
                    text(40, 690 - index * 15, line)
            if number == 2:
                # Separate the report heading from the table's own dates.
                for y, line in zip((705, 695, 685), note_titles or ("Loan disclosures", "Historical comparisons", "Relevant statement table")):
                    text(40, y, line)
                rows = [["", current_header, "Prior Period"], *(date_rows or []),
                        ["Domestic Loans", "1,234,567", "987,654"], ["Foreign Loans", "234,567", "123,456"],
                        ["Total", "1,469,134", "1,111,110"]]
                bottom = 680 - len(rows) * 20
                for x in (40, 220, 350, 490):
                    commands.append(f"{x} {bottom} m {x} 680 l S")
                for y in range(bottom, 681, 20):
                    commands.append(f"40 {y} m 490 {y} l S")
                for row, y in zip(rows, range(666, bottom, -20)):
                    for x, value in zip((45, 225, 355), row):
                        text(x, y, value)
                for index, line in enumerate(footnotes or []):
                    text(40, bottom - 14 - index * 14, line)
            stream = DecodedStreamObject()
            stream.set_data("\n".join(commands).encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(buffer)
        source = self.docs._register(buffer.getvalue(), "note.pdf", "application/pdf")
        inspected = self.docs.inspect_source(source_id=source["source_id"], page_numbers=[2])
        table = next(table for table in inspected["tables"] if table.get("row_count") == 3 + len(date_rows or []))
        return {"source_id": source["source_id"], "table_id": table["table_id"], "expected_version": 0,
                "row_labels": ["Domestic Loans", "Foreign Loans", "Total"], "periods": ["2027-06-30"], "measure_kind": "stock"}

    def test_pdf_note_current_header_binds_own_report_date_and_preserves_original_cells(self):
        args = self.pdf_note()
        original = self.docs.review_candidate(args["source_id"], args["table_id"])
        original_bytes = self.docs.raw_source_bytes(args["source_id"])
        result = self.handler(args)
        self.assertEqual(result["status"], "ok", result)
        frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
        self.assertEqual(frame["amount"].tolist(), [1234567, 234567, 1469134])
        self.assertEqual(frame["period"].tolist(), ["2027-06-30"] * 3)
        self.assertEqual(result["published_columns"]["amount"]["scale"], 1000)
        self.assertEqual(self.docs.review_candidate(args["source_id"], args["table_id"]), original)
        self.assertEqual(self.docs.raw_source_bytes(args["source_id"]), original_bytes)
        for row, origin in enumerate(result["provenance"]["cell_origins"], 1):
            self.assertEqual(origin["amount"], {"candidate_row": row, "candidate_column": "Current_Period"})
            self.assertEqual(origin["period"]["source_header_quote"], "Current Period")
            proof = origin["period"]["report_header"]
            self.assertEqual((proof["page"], proof["line"], proof["matched_source_date"]), (2, 2, "30 June 2027"))
            self.assertEqual(proof["raw_sha256"], original["raw_sha256"])
        self.assertEqual(result["compile_receipt"]["date_mapping_evidence"]["unresolved_value_columns"], ["Prior_Period"])
        again = self.handler(args)
        self.assertEqual(again["dataset_id"], result["dataset_id"])
        self.assertEqual(self.store.workspace("financial_import")["version"], 1)

    def test_pdf_note_accepts_actual_column_header_as_business_selection(self):
        result = self.handler({**self.pdf_note(), "value_header": "Current Period", "value_columns": ["Current_Period"]})
        self.assertEqual(result["status"], "ok", result)

    def test_pdf_scope_footnote_is_bound_to_selected_section_and_published_lineage(self):
        from agentic_analytics.lakehouse.service import LakehouseService
        args = self.pdf_note(section_titles=["7.3 Consolidated assets"], note_titles=["7.3.2 Loans (*)"],
            footnotes=["(*) Non-performing loans are not included.", "7.3.3 Related party loans",
                       "(*) Foreign subsidiaries are excluded."])
        original = copy.deepcopy(self.compiler._candidate(args["source_id"], args["table_id"]))
        raw = self.docs.raw_source_bytes(args["source_id"])
        result = self.handler(args)
        self.assertEqual(result["status"], "ok", result)
        notes = result["provenance"]["source_scope_evidence"]
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note["source_quote"], "(*) Non-performing loans are not included.")
        self.assertEqual(note["section_quote"], "7.3.2 Loans (*)")
        self.assertEqual((note["page"], note["source_table_id"], note["raw_sha256"]),
                         (2, args["table_id"], original["raw_sha256"]))
        self.assertEqual(note["line_start"], note["line_end"])
        self.assertEqual(self.compiler._candidate(args["source_id"], args["table_id"]), original)
        self.assertEqual(self.docs.raw_source_bytes(args["source_id"]), raw)
        service = LakehouseService(self.store, "financial_import")
        metric = service.describe({"metric_id": result["available_series"][0]["metric_id"]})["metric"]
        self.assertEqual(metric["document_provenance"]["source_scope_evidence"], notes)
        again = self.handler(args)
        self.assertEqual(again["dataset_id"], result["dataset_id"])
        self.assertEqual(again["provenance"]["source_scope_evidence"], notes)

    def test_pdf_scope_does_not_borrow_next_section_or_unmatched_footnote(self):
        for heading, footnotes in [
            ("7.3.2 Loans (*)", ["7.3.3 Related party loans", "(*) Foreign subsidiaries are excluded."]),
            ("7.3.2 Loans (*)", ["(2) Non-performing loans are not included."]),
            ("Loan table without section association", ["(*) Non-performing loans are not included."]),
        ]:
            with self.subTest(heading=heading, footnotes=footnotes):
                args = self.pdf_note(note_titles=[heading], footnotes=footnotes)
                self.assertEqual(self.compiler._table_scope_evidence(self.compiler._candidate(args["source_id"], args["table_id"])), [])

    def test_pdf_note_never_guesses_prior_period_or_accepts_requested_date_as_evidence(self):
        args = self.pdf_note()
        for selection in ({"value_columns": ["Prior_Period"]}, {"value_header": "Prior Period"},
                          {"periods": None}, {"periods": ["2026-12-31"]},
                          {"periods": ["2027-06-30", "2026-12-31"]},
                          {"value_columns": ["Current_Period", "Prior_Period"]}):
            with self.subTest(selection=selection):
                result = self.handler({key: value for key, value in {**args, **selection}.items() if value is not None})
                self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_PERIODS", result)
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_omitted_periods_returns_source_verified_retry_without_publishing(self):
        args = self.pdf_note(section_titles=["7.3 Consolidated assets"], note_titles=["7.3.2 Loans"])
        args.pop("periods")
        args["value_header"] = "Current Period"
        original = self.compiler._candidate(args["source_id"], args["table_id"])
        cache = (self.docs._directory(args["source_id"]) / "inspection.json").read_bytes()
        blocked = self.handler(args)
        self.assertEqual(blocked["code"], "AMBIGUOUS_IMPORT_PERIODS", blocked)
        recovery = blocked["recovery"]
        self.assertFalse(recovery["publication_performed"])
        suggested = recovery["suggested_ingest_arguments"]
        self.assertEqual(suggested, {**args, "periods": ["2027-06-30"], "measure_kind": "stock"})
        self.assertEqual(recovery["next_request"], {"tool": "ingest_source_table", "arguments": suggested})
        proof = recovery["source_period_evidence"]["source_period_binding"]["Current_Period"]["report_header"]
        self.assertEqual((proof["page"], proof["line"], proof["normalized_date"], proof["raw_sha256"]),
                         (2, 2, "2027-06-30", original["raw_sha256"]))
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
        self.assertEqual((self.docs._directory(args["source_id"]) / "inspection.json").read_bytes(), cache)
        self.assertEqual(list(self.compiler.root.glob("import_*.json")), [])
        published = self.handler(suggested)
        self.assertEqual(published["status"], "ok", published)
        self.assertEqual(published["available_series"][0]["observed_periods"], ["2027-06-30"])
        self.assertEqual(self.store.workspace("financial_import")["version"], 1)

    def test_omitted_period_retry_is_not_offered_for_prior_or_conflicting_dates(self):
        cases = [
            ({}, {"value_header": "Prior Period"}),
            ({}, {"value_columns": ["Prior_Period"]}),
            ({}, {"periods": ["2027-03-31"], "value_header": "Current Period"}),
            ({"heading": ["Consolidated Financial Report", "as of 30 June 2027", "as of 31 March 2027",
                          "Thousands of Turkish Lira (TL)"]}, {"value_header": "Current Period"}),
            ({"date_rows": [["", "30 June 2027", "31 December 2026"],
                            ["", "31 March 2027", "30 September 2026"]]}, {"value_header": "Current Period"}),
        ]
        for fixture, selection in cases:
            with self.subTest(fixture=fixture, selection=selection):
                args = self.pdf_note(**fixture)
                args.pop("periods")
                blocked = self.handler({**args, **selection})
                self.assertEqual(blocked["code"], "AMBIGUOUS_IMPORT_PERIODS", blocked)
                self.assertNotIn("suggested_ingest_arguments", blocked.get("recovery", {}))
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_pdf_note_date_must_be_unique_explicit_report_heading_on_its_own_page(self):
        headings = [
            ["Consolidated Financial Report", "Loan note without its date", "Thousands of Turkish Lira (TL)"],
            ["Consolidated Financial Report", "Approved on 30 June 2027", "Thousands of Turkish Lira (TL)"],
            ["Consolidated Financial Report", "as of 30 June 2027", "Published on 15 July 2027", "Thousands of Turkish Lira (TL)"],
            ["Loan customer example", "as of 30 June 2027", "Thousands of Turkish Lira (TL)"],
        ]
        for heading in headings:
            with self.subTest(heading=heading):
                result = self.handler(self.pdf_note(heading))
                self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_PERIODS", result)
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_pdf_report_date_binding_verifies_original_bytes_before_publication(self):
        args = self.pdf_note()
        raw = self.docs._directory(args["source_id"]) / "raw.bin"
        raw.write_bytes(raw.read_bytes() + b"\nchanged")
        result = self.handler(args)
        self.assertEqual(result["code"], "SOURCE_HASH_MISMATCH", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_report_heading_never_overrides_conflicting_table_date_cells(self):
        args = self.pdf_note(date_rows=[["", "30 June 2027", "31 December 2026"],
                                       ["", "31 March 2027", "30 September 2026"]])
        original = self.docs.review_candidate(args["source_id"], args["table_id"])
        for selection in ({}, {"value_columns": ["Current_Period"]}, {"value_header": "Current Period"}):
            with self.subTest(selection=selection):
                result = self.handler({**args, **selection})
                self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_PERIODS", result)
                self.assertTrue(result["recovery"]["business_choices"]["date_candidates"])
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)
                self.assertEqual(self.docs.review_candidate(args["source_id"], args["table_id"]), original)

    def test_balance_note_semantics_require_numbered_source_ancestry_not_loan_labels(self):
        args = self.pdf_note(section_titles=["7.3 Consolidated assets"], note_titles=["7.3.2 Loans"])
        result = self.handler(args)
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(all(item["kind"] == "stock" and item["status"] == "ready" for item in result["available_series"]))
        proof = result["compile_receipt"]["semantic_evidence"]["statement_scope"]
        self.assertEqual(proof["section"]["page"], 1)
        self.assertEqual(proof["section"]["source_quote"], "7.3 Consolidated assets")
        self.assertEqual(proof["note_headings"][0]["source_quote"], "7.3.2 Loans")
        self.assertEqual(proof["section"]["raw_sha256"], result["compile_receipt"]["source_raw_sha256"])

    def test_loan_notes_outside_balance_scope_or_under_movements_stay_unreviewed(self):
        variants = [({}, "no section"), ({"section_titles": ["7.2 Consolidated assets"], "note_titles": ["7.3.2 Loans"]}, "unrelated"),
                    ({"section_titles": ["7.3 Consolidated assets", "7.3.2 Changes in loans"], "note_titles": ["7.3.2.4 Loans"]}, "movement ancestor"),
                    ({"section_titles": ["7.3 Consolidated assets"], "note_titles": ["7.3.2 Interest income"]}, "flow note"),
                    ({"section_titles": ["7.3 Consolidated assets"], "note_titles": ["7.3.2 Loan groups", "Movements in non-performing loan groups", "Additions during the period"]}, "unnumbered movement table"),
                    ({"section_titles": ["7.3 Consolidated assets"], "note_titles": ["7.3.2 Expected credit losses", "Collections and write-offs"]}, "mixed balances and flows"),
                    ({"section_titles": ["7.3 Consolidated assets"], "note_titles": ["7.3.2 Loans", "7.4.1 Deposits"]}, "mixed sections")]
        for options, name in variants:
            with self.subTest(case=name):
                args = self.pdf_note(**options)
                result = self.handler({**args, "expected_version": self.store.workspace("financial_import")["version"]})
                self.assertEqual(result["status"], "ok", result)
                self.assertTrue(all(item["kind"] == "unknown" and item["status"] == "review_required" for item in result["available_series"]))

    def test_event_balance_execute_recovery_preserves_million_scales_ratio_operands_and_source_lineage(self):
        from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
        from agentic_analytics.lakehouse.service import LakehouseService
        result = self.handler(self.pdf_note(section_titles=["7.3 Consolidated assets"], note_titles=["7.3.2 Loans"]))
        metric = result["available_series"][0]["metric_id"]
        service = LakehouseService(self.store, "financial_import")
        execute = lakehouse_tools(service)["execute"]["handler"]
        plan = {"start": "2027-06-30", "end": "2027-06-30", "frequency": "daily", "columns": [
            {"name": name, "metric_id": metric, "dimensions": {"line_item": label}, "alignment": "native"}
            for name, label in zip(("domestic", "foreign", "total"), ("Domestic Loans", "Foreign Loans", "Total"))],
            "operations": [{"op": "scale", "column": name, "output": name + "_million", "target_scale": 1000000}
                for name in ("domestic", "foreign", "total")] + [
                {"op": "ratio", "column": name + "_million", "denominator": "total_million", "output": name + "_pct",
                 "multiplier": 100, "scope_policy": "same_scope", "scope_reason": "Numerical comparison with the explicitly selected source total."}
                for name in ("domestic", "foreign")]}
        original = copy.deepcopy(plan)
        before = self.store.workspace("financial_import")
        blocked = execute(plan)
        self.assertEqual(blocked["status"], "blocked", blocked)
        self.assertEqual(self.store.workspace("financial_import"), before)
        self.assertEqual(plan, original)
        retry = blocked["recovery"]["next_request"]["arguments"]
        self.assertTrue(all(column["alignment"] == "period_end" for column in retry["columns"]))
        self.assertEqual(retry["operations"][:3], plan["operations"][:3])
        self.assertEqual(retry["operations"][3]["denominator"], "total_million")
        self.assertEqual(retry["operations"][3]["scope_policy"], "explicit_comparison")
        saved = execute(retry)
        self.assertEqual(saved["status"], "ok", saved)
        frame, manifest = self.store.load_analysis(saved["analysis_id"])
        self.assertEqual(frame["domestic_million"].tolist(), [1234.567])
        self.assertAlmostEqual(frame["domestic_pct"].iloc[0], 1234567 / 1469134 * 100)
        explanation = service.explain_value({"analysis_id": saved["analysis_id"], "column": "domestic_pct", "period": "2027-06-30"})
        self.assertEqual(explanation["status"], "ok", explanation)
        self.assertTrue(explanation["source_references_complete"])
        sources = [branch["inputs"][0] for branch in explanation["lineage"]["inputs"]]
        self.assertEqual([source["dimensions"]["line_item"] for source in sources], ["Domestic Loans", "Total"])
        self.assertTrue(all(len(source["source_cells"]) == 1 for source in sources))

    def test_event_recovery_keeps_valid_same_scope_ratio_and_only_validates_alignment(self):
        from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
        from agentic_analytics.lakehouse.service import LakehouseService
        result = self.handler(self.pdf_note(section_titles=["7.3 Consolidated assets"], note_titles=["7.3.2 Loans"]))
        service = LakehouseService(self.store, "financial_import")
        plan = {"start": "2027-06-30", "end": "2027-06-30", "frequency": "daily", "columns": [{
            "name": "loans", "metric_id": result["available_series"][0]["metric_id"],
            "dimensions": {"line_item": "Domestic Loans"}, "alignment": "native"}],
            "operations": [{"op": "ratio", "column": "loans", "denominator": "loans", "output": "same_source_pct",
                            "scope_policy": "same_scope", "multiplier": 100}]}
        with patch.object(service, "validate_plan", wraps=service.validate_plan) as validate:
            blocked = lakehouse_tools(service)["execute"]["handler"](plan)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(blocked["recovery"]["scope_policy_changes"], [])
        self.assertEqual(blocked["recovery"]["next_request"]["arguments"]["operations"], plan["operations"])
        self.assertEqual(blocked["recovery"]["validation_status"], "valid")

    def test_unknown_event_kind_cannot_receive_calendar_alignment_recovery(self):
        from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
        from agentic_analytics.lakehouse.service import LakehouseService
        result = self.handler(self.pdf_note())
        execute = lakehouse_tools(LakehouseService(self.store, "financial_import"))["execute"]["handler"]
        blocked = execute({"start": "2027-06-30", "end": "2027-06-30", "frequency": "daily", "columns": [{
            "name": "loans", "metric_id": result["available_series"][0]["metric_id"], "dimensions": {"line_item": "Domestic Loans"}}]})
        self.assertEqual(blocked["status"], "blocked")
        self.assertNotIn("recovery", blocked)

    def test_business_labels_publish_exact_sorted_cells_and_native_analysis(self):
        args = {**self.basic(), "row_labels": ["Total assets", "Cash"], "periods": ["2026-03-31", "2025-12-31"]}
        result = self.handler(args)
        self.assertEqual(result["status"], "ok", result)
        frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
        self.assertEqual(frame["amount"].tolist(), [123456, 234567, 1234567, 2345678])
        self.assertEqual(frame["line_item"].tolist(), ["Cash", "Cash", "Total assets", "Total assets"])
        self.assertEqual(result["published_columns"]["amount"]["scale"], 1000)
        available = result["available_series"]
        self.assertEqual(result["available_series_count"], 2)
        self.assertFalse(result["available_series_truncated"])
        self.assertEqual([item["dimensions"] for item in available], [{"line_item": "Cash"}, {"line_item": "Total assets"}])
        for item in available:
            self.assertEqual(item["metric_id"], result["metric_ids"][0])
            self.assertEqual(item["native_frequency"], "event")
            self.assertEqual(item["observed_periods"], ["2025-12-31", "2026-03-31"])
            self.assertEqual(item["status"], "ready")
            self.assertNotIn("amount", item)
        candidate = self.docs.review_candidate(args["source_id"], args["table_id"])
        for row, origin in zip(frame.to_dict("records"), result["provenance"]["cell_origins"]):
            address = origin["amount"]
            raw = candidate["rows"][address["candidate_row"] - 1][candidate["columns"].index(address["candidate_column"])]
            self.assertEqual(int(raw.replace(",", "")), row["amount"])
        query = result["analysis_request"]
        self.assertEqual(query["arguments"]["measures"][0]["op"], "source_value")
        analysis = DatasetTools(self.store, "financial_import").aggregate_dataset(**query["arguments"])
        self.assertEqual(analysis["status"], "ok", analysis)

    def test_turkish_uppercase_long_currency_caption_keeps_exact_source_quote(self):
        args = self.basic(caption="BİN TÜRK LİRASI", title="KONSOLİDE BİLANÇO (FİNANSAL DURUM TABLOSU)")
        result = self.handler({**args, "row_labels": ["Total assets"], "measure_kind": "stock"})
        self.assertEqual(result["status"], "ok", result)
        amount = result["published_columns"]["amount"]
        self.assertEqual((amount["unit"], amount["currency"], amount["scale"]), ("TRY", "TRY", 1000))
        self.assertEqual(result["compile_receipt"]["semantic_evidence"]["unit_quote"], "BİN TÜRK LİRASI")
        self.assertEqual(pd.read_parquet(self.store.overlay_path(result["dataset_id"]))["amount"].tolist(), [1234567, 2345678])

    def test_stale_version_preserves_workspace_and_same_selection_can_retry_current_version(self):
        source = self.basic()
        first = self.handler({**source, "row_labels": ["Cash"]})
        self.assertEqual(first["status"], "ok", first)
        before = self.store.workspace("financial_import")
        first_path = self.store.overlay_path(first["dataset_id"])
        original_bytes = first_path.read_bytes()
        args = {**source, "row_labels": ["Total assets"], "periods": ["2026-03-31"], "measure_kind": "stock"}
        submitted = copy.deepcopy(args)
        blocked = self.handler(args)
        self.assertEqual(blocked["code"], "VERSION_CONFLICT", blocked)
        self.assertEqual(blocked["submitted_version"], 0)
        self.assertEqual(blocked["current_version"], before["version"])
        self.assertEqual(blocked["current_revision_id"], before["revision_id"])
        self.assertFalse(blocked["publication_performed"])
        self.assertEqual(self.store.workspace("financial_import"), before)
        self.assertEqual(first_path.read_bytes(), original_bytes)
        self.assertEqual(args, submitted)
        retry = blocked["recovery"]["suggested_ingest_arguments"]
        self.assertEqual(retry, {**submitted, "expected_version": before["version"]})
        result = self.handler(retry)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(pd.read_parquet(self.store.overlay_path(result["dataset_id"])).to_dict("records"),
                         [{"line_item": "Total assets", "period": "2026-03-31", "amount": 2345678}])
        self.assertEqual(self.store.workspace("financial_import")["version"], before["version"] + 1)

    def test_turkish_money_caption_still_requires_one_explicit_common_currency_and_scale(self):
        from agentic_analytics.agent.tools.documents import _unit_caption
        from agentic_analytics.lakehouse.units import unit_quote_matches
        for caption, scale in [("BİN TÜRK LİRASI", 1000), ("MİLYON TÜRK LİRASI", 1000000),
                               ("(Milyar Türk Lirası)", 1000000000)]:
            with self.subTest(caption=caption):
                self.assertEqual(_unit_caption("KONSOLİDE BİLANÇO\n" + caption), caption)
                self.assertTrue(unit_quote_matches("TRY", scale, caption))
                self.assertFalse(unit_quote_matches("USD", scale, caption))
                self.assertFalse(unit_quote_matches("TRY", scale * 1000, caption))
        for caption in ("", "Banka geçen yıl bin Türk Lirası yatırmıştır.", "BİN TÜRK LİRASI\nMİLYON TÜRK LİRASI"):
            with self.subTest(ambiguous=caption):
                result = self.handler({**self.basic(caption=caption), "row_labels": ["Total assets"]})
                self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_UNIT", result)
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_repeated_header_groups_require_explicit_total_and_bind_their_dates(self):
        args = self.source([[None, None, "31 March 2026", None, "31 December 2025", None, None],
                            [None, "LC", "FC", "Total", "LC", "FC", "Total"],
                            ["Cash", "1,000", "2,000", "3,000", "4,000", "5,000", "9,000"]],
                           columns=["label", "a", "b", "c", "d", "e", "f"])
        blocked = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
        result = self.handler({**args, "row_labels": ["Cash"], "value_header": "Total", "number_style": "decimal_dot_grouped"})
        self.assertEqual(result["status"], "ok", result)
        frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
        self.assertEqual(frame["amount"].tolist(), [9000, 3000])
        self.assertEqual(result["compile_receipt"]["date_mapping_evidence"]["repeated_source_header_row"], 2)

    def test_source_missing_marker_remains_missing_not_zero(self):
        args = self.source([[None, "31 December 2025", "31 March 2026"], ["Cash", "-", "123456"]])
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["status"], "ok", result)
        values = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))["amount"].tolist()
        self.assertTrue(pd.isna(values[0]))
        self.assertEqual(values[1], 123456)

    def test_duplicate_source_label_is_business_ambiguity_before_publication(self):
        args = self.source([[None, "31 December 2025", "31 March 2026"], ["Cash", "1000", "2000"], ["Cash", "3000", "4000"]])
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_ROWS", result)
        self.assertEqual(len(result["recovery"]["business_choices"]["rows"]), 2)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_requested_date_is_only_filter_never_inserted(self):
        result = self.handler({**self.basic(), "row_labels": ["Cash"], "periods": ["2030-01-01"]})
        self.assertEqual(result["code"], "IMPORT_PERIOD_NOT_FOUND", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_ambiguous_numeric_punctuation_has_business_choice_without_guess(self):
        args = self.source([[None, "31 December 2025", "31 March 2026"], ["Cash", "1.234", "2.345"]])
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_NUMBERS", result)
        choices = result["recovery"]["business_choices"]["number_style"]
        self.assertIn(["1234", "2345"], [item["examples"] for item in choices])
        self.assertIn(["1.234", "2.345"], [item["examples"] for item in choices])

    def test_literal_source_equation_resolves_dot_grouping_and_parentheses(self):
        args = self.source([[None, None, "30 June 2026", "30 June 2025"],
                            ["I.", "Current result", "34.333", "24.850"], ["II.", "Other result", "(8.207)", "19"],
                            ["III.", "Combined result (I+II)", "26.126", "24.869"]],
                           columns=["code", "label", "first", "second"], title="Consolidated statement of profit or loss")
        result = self.handler({**args, "row_labels": ["Current result", "Other result"]})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["published_columns"]["amount"]["kind"], "flow")
        self.assertEqual(result["published_columns"]["amount"]["temporal_semantics"], "reported_interval_unresolved")
        self.assertTrue(all(item["kind"] == "flow" and item["status"] == "review_required" for item in result["available_series"]))
        proof = result["compile_receipt"]["numeric_format_evidence"]
        self.assertEqual(proof["number_style"], "decimal_comma")
        self.assertEqual(len(proof["printed_source_equations"]), 2)
        self.assertEqual(pd.read_parquet(self.store.overlay_path(result["dataset_id"]))["amount"].tolist(), [24850, 34333, 19, -8207])

    def test_adjusted_price_basis_is_separate_from_comparative_observation_date(self):
        args = self.basic(caption="Amounts expressed in thousands of Turkish Lira (TRY) in terms of purchasing power at 31 March 2026")
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["published_columns"]["amount"]["price_basis"], "purchasing_power_2026-03-31")
        self.assertEqual(pd.read_parquet(self.store.overlay_path(result["dataset_id"]))["period"].tolist(), ["2025-12-31", "2026-03-31"])

    def test_multiline_numeric_cell_requires_review(self):
        args = self.source([[None, "31 December 2025", "31 March 2026"], ["Cash", "100<br>200", "300"]])
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["code"], "TABLE_REVIEW_REQUIRED", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_crash_after_commit_recovers_same_publication_exactly_once(self):
        args = {**self.basic(), "row_labels": ["Cash"]}
        before = self.store.workspace("financial_import")
        original = self.docs.publish_selected_table
        def interrupted(**arguments):
            original(**arguments)
            raise RuntimeError("Interrupted after publication")
        with patch.object(self.docs, "publish_selected_table", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.compiler.ingest_source_table(**args)
        result = self.compiler.recover(args, {"input_revision": before["revision_id"], "workspace_version": before["version"]})
        self.assertEqual(result["status"], "ok", result)
        retry = self.compiler.ingest_source_table(**args)
        self.assertEqual(retry["dataset_id"], result["dataset_id"])
        workspace = self.store.workspace("financial_import")
        self.assertEqual(workspace["version"], 1)
        self.assertEqual(len(workspace["datasets"]), 1)

    def test_compiled_receipt_tampering_is_rejected(self):
        args = {**self.basic(), "row_labels": ["Cash"]}
        self.compiler.ingest_source_table(**args)
        path = next(self.compiler.root.glob("import_*.json"))
        receipt = json.loads(path.read_text())
        receipt["publication_arguments"]["expected_version"] = 100
        path.write_text(json.dumps(receipt))
        result = self.handler(args)
        self.assertEqual(result["code"], "IMPORT_RECEIPT_HASH_MISMATCH", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 1)

    def test_uncommitted_compilation_refuses_changed_prepared_review(self):
        args = {**self.basic(), "row_labels": ["Cash"]}
        before = self.store.workspace("financial_import")
        with patch.object(self.docs, "publish_selected_table", side_effect=RuntimeError("before publication")):
            with self.assertRaises(RuntimeError):
                self.compiler.ingest_source_table(**args)
        receipt = json.loads(next(self.compiler.root.glob("import_*.json")).read_text())
        prepared = self.docs.review_candidate(args["source_id"], receipt["prepared_table_id"])
        changed = copy.deepcopy(prepared["rows"])
        changed[0][prepared["columns"].index("amount")] = "999"
        self.docs.review_table(args["source_id"], prepared["table_id"], changed, {"amount": "thousands of Turkish Lira"})
        result = self.handler(args)
        self.assertEqual(result["code"], "IMPORT_SOURCE_CHANGED", result)
        recovered = self.compiler.recover(args, {"input_revision": before["revision_id"], "workspace_version": before["version"]})
        self.assertEqual(recovered["code"], "IMPORT_SOURCE_CHANGED", recovered)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_committed_compilation_recovers_original_dataset_after_later_review(self):
        args = {**self.basic(), "row_labels": ["Cash"]}
        before = self.store.workspace("financial_import")
        first = self.compiler.ingest_source_table(**args)
        prepared = self.docs.review_candidate(args["source_id"], first["table_id"])
        changed = copy.deepcopy(prepared["rows"])
        changed[0][prepared["columns"].index("amount")] = "999"
        self.docs.review_table(args["source_id"], prepared["table_id"], changed, {"amount": "thousands of Turkish Lira"})
        recovered = self.compiler.recover(args, {"input_revision": before["revision_id"], "workspace_version": before["version"]})
        self.assertEqual(recovered["dataset_id"], first["dataset_id"], recovered)
        self.assertEqual(pd.read_parquet(self.store.overlay_path(first["dataset_id"]))["amount"].tolist(), [123456, 234567])
        self.assertEqual(self.store.workspace("financial_import")["version"], 1)

    def test_unknown_source_kind_cannot_be_upgraded_by_model_choice(self):
        args = {**self.basic(title="Financial data extract"), "row_labels": ["Cash"], "measure_kind": "stock"}
        result = self.handler(args)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["published_columns"]["amount"]["kind"], "unknown")
        self.assertEqual(result["published_columns"]["amount"]["status"], "review_required")
        analysis = DatasetTools(self.store, "financial_import").aggregate_dataset(**result["analysis_request"]["arguments"])
        self.assertEqual(analysis["status"], "ok", analysis)

    def test_regular_monthly_csv_uses_supported_advanced_fallback(self):
        source = self.docs._register(b"month,revenue (TRY)\n2026-01,100\n2026-02,120\n", "records.csv", "text/csv")
        inspected = self.docs.inspect_source(source_id=source["source_id"])
        args = {"source_id": source["source_id"], "table_id": inspected["tables"][0]["table_id"], "expected_version": 0}
        result = self.handler({**args, "row_labels": ["revenue"]})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["import_status"], "unsupported_layout")
        self.assertFalse(result["publication_performed"])
        self.assertNotIn("dataset_id", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
        contract = {"name": "monthly_revenue", "frequency": "monthly", "date_column": "month", "key": ["month"], "grain": ["month"],
                    "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                                "revenue_TRY": {"dtype": "integer", "unit": "TRY", "kind": "flow", "nullable": False}}}
        published = self.docs.publish_selected_table(**args, contract=contract, unit_evidence={"revenue_TRY": "revenue (TRY)"})
        self.assertEqual(published["status"], "ok", published)
        self.assertEqual(published["row_count"], 2)

    def alternative_fixture(self, **changes):
        args = self.basic()
        path = self.docs._directory(args["source_id"]) / "inspection.json"
        inspection = json.loads(path.read_text())
        good = inspection["tables"][0]
        good.update(page=1, table_id="table_p000001_text_001", table_strategy="text", context_text=inspection["text"])
        bad = copy.deepcopy(good)
        bad.update(table_id="table_p000001_001", table_strategy="lines", layout_review_required=True)
        bad["rows"][1][1] = "100\n200"
        other_page = copy.deepcopy(good)
        other_page.update(page=2, table_id="table_p000002_text_001")
        good.update(changes)
        inspection["tables"] = [bad, other_page, good]
        path.write_text(json.dumps(inspection))
        return {**args, "table_id": bad["table_id"]}, good

    def test_review_error_offers_same_page_independent_candidate_and_preserves_business_selection(self):
        args, good = self.alternative_fixture()
        args.update(row_labels=["Cash"], periods=["2026-03-31"])
        result = self.handler(args)
        self.assertEqual(result["code"], "TABLE_REVIEW_REQUIRED", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
        recovery = result["recovery"]
        self.assertTrue(recovery["rejected_candidate_still_requires_review"])
        self.assertEqual(len(recovery["alternative_candidates"]), 1)
        suggestion = recovery["suggested_ingest_arguments"]
        for key in ("source_id", "row_labels", "periods", "expected_version"):
            self.assertEqual(suggestion[key], args[key])
        self.assertEqual(suggestion["table_id"], good["table_id"])
        published = self.handler(suggestion)
        self.assertEqual(published["status"], "ok", published)
        self.assertEqual(pd.read_parquet(self.store.overlay_path(published["dataset_id"]))["amount"].tolist(), [234567])
        self.assertTrue(self.docs.review_candidate(args["source_id"], args["table_id"])["layout_review_required"])

    def test_review_error_never_offers_ocr_or_other_review_required_alternatives(self):
        for flags in ({"origin": "ocr"}, {"layout_review_required": True}, {"missing_formula_cache": ["B2"]},
                      {"preparation": {"source_table_id": "table_p000001_001"}}):
            with self.subTest(flags=flags):
                args, good = self.alternative_fixture(**flags)
                result = self.handler({**args, "row_labels": ["Cash"]})
                self.assertEqual(result["code"], "TABLE_REVIEW_REQUIRED", result)
                self.assertEqual(result["recovery"]["alternative_candidates"], [])
                self.assertNotIn("suggested_ingest_arguments", result["recovery"])
                self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_candidate_local_numeric_addresses_are_not_reused_for_alternative_extraction(self):
        args, good = self.alternative_fixture()
        for selection in ({"row_numbers": [3]}, {"row_labels": ["Cash"], "value_columns": ["second"]}):
            with self.subTest(selection=selection):
                result = self.handler({**args, **selection})
                self.assertEqual(result["code"], "TABLE_REVIEW_REQUIRED", result)
                self.assertNotIn("suggested_ingest_arguments", result["recovery"])
                self.assertEqual(result["recovery"]["alternative_candidates"][0]["next_request"]["tool"], "read_source_table")

    def structural_alternative(self, rows):
        args, good = self.alternative_fixture()
        path = self.docs._directory(args["source_id"]) / "inspection.json"
        inspection = json.loads(path.read_text())
        bad = inspection["tables"][0]
        bad.update(layout_review_required=False, rows=rows, row_count=len(rows))
        path.write_text(json.dumps(inspection))
        return args, good

    def test_missing_business_label_suggests_source_verified_same_page_alternative(self):
        args, good = self.structural_alternative([[None, "31 December 2025", "31 March 2026"],
                                                ["Cash", "123456", "234567"]])
        requested = {**args, "row_labels": ["Total assets", "Cash"], "periods": ["2026-03-31"]}
        result = self.handler(requested)
        self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_ROWS", result)
        self.assertEqual(result["recovery"]["business_choices"]["matching_row_count"], 0)
        suggestion = result["recovery"]["suggested_ingest_arguments"]
        self.assertEqual(suggestion["row_labels"], requested["row_labels"])
        self.assertEqual(suggestion["periods"], requested["periods"])
        self.assertEqual(suggestion["table_id"], good["table_id"])
        evidence = result["recovery"]["alternative_candidates"][0]["selection_evidence"]
        self.assertEqual(evidence["source_rows"], [2, 3])
        self.assertEqual(evidence["source_periods"]["second"], "2026-03-31")
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
        published = self.handler(suggestion)
        self.assertEqual(published["status"], "ok", published)
        self.assertEqual(pd.read_parquet(self.store.overlay_path(published["dataset_id"]))["amount"].tolist(), [234567, 2345678])

    def test_duplicate_source_scope_is_not_downgraded_to_missing_label_recovery(self):
        args, _ = self.structural_alternative([[None, "31 December 2025", "31 March 2026"],
                                              ["Cash", "123456", "234567"], ["Cash", "777777", "888888"]])
        result = self.handler({**args, "row_labels": ["Cash"]})
        self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_ROWS", result)
        self.assertEqual(result["recovery"]["business_choices"]["matching_row_count"], 2)
        self.assertNotIn("suggested_ingest_arguments", result["recovery"])
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_unsupported_unaligned_rows_prioritize_valid_business_alternative(self):
        args, good = self.structural_alternative([[None, "31 December 2025", "31 March 2026"],
                                                ["Total assets", "1234567", None], ["Cash", None, "234567"]])
        result = self.handler({**args, "row_labels": ["Total assets", "Cash"], "periods": ["2026-03-31"]})
        self.assertEqual(result["import_status"], "unsupported_layout", result)
        self.assertFalse(result["publication_performed"])
        self.assertNotIn("dataset_id", result)
        self.assertEqual(result["recovery"]["suggested_ingest_arguments"]["table_id"], good["table_id"])
        self.assertIn("Retry ingest_source_table", result["next_step"])
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_ambiguous_dates_only_suggest_alternative_with_proven_requested_periods(self):
        args, good = self.structural_alternative([[None, "unclear", "unclear"], ["Cash", "123456", "234567"]])
        for period, suggested in [("2026-03-31", True), ("2027-03-31", False)]:
            with self.subTest(period=period):
                result = self.handler({**args, "row_labels": ["Cash"], "periods": [period]})
                self.assertEqual(result["code"], "AMBIGUOUS_IMPORT_PERIODS", result)
                self.assertEqual("suggested_ingest_arguments" in result["recovery"], suggested)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)

    def test_complete_merged_date_ownership_is_invariant_under_value_column_subset(self):
        args = self.source([[None, "3", "0 June 2025 31 De", "cember 2024"],
                            ["Cash", None, "90,351,730", "85,795,568"],
                            ["Total assets", None, "545,630,257", "529,848,729"]],
                           columns=["label", "prefix", "current", "prior"])
        for columns, expected in [(["current", "prior"], [85795568, 90351730, 529848729, 545630257]),
                                  (["current"], [90351730, 545630257]), (["prior"], [85795568, 529848729])]:
            with self.subTest(columns=columns):
                version = self.store.workspace("financial_import")["version"]
                result = self.handler({**args, "expected_version": version, "row_numbers": [2, 3], "value_columns": columns})
                self.assertEqual(result["status"], "ok", result)
                frame = pd.read_parquet(self.store.overlay_path(result["dataset_id"]))
                self.assertEqual(frame["amount"].tolist(), expected)
                self.assertEqual(result["compile_receipt"]["date_mapping_evidence"]["complete_source_period_mapping"],
                                 {"current": "2025-06-30", "prior": "2024-12-31"})
                self.assertEqual(result["compile_receipt"]["source_periods"],
                                 {column: {"current": "2025-06-30", "prior": "2024-12-31"}[column] for column in columns})

    def test_missing_table_preserves_controlled_error_without_recovery_metadata(self):
        args = self.basic()
        result = self.handler({**args, "table_id": "table_missing", "row_labels": ["Cash"]})
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("code", result)
        self.assertNotIn("recovery", result)
        self.assertEqual(self.store.workspace("financial_import")["version"], 0)
