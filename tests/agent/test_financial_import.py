"""Business selections compile financial facts without caller-authored ETL."""
import copy
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
