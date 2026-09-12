"""Counterexamples from the competition audit, plus generic query contracts."""
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
import pandas as pd

from agentic_analytics.lakehouse.registry import get_bindings, install_bindings
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class SemanticQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        db = cls.root / "fixture.duckdb"
        c = duckdb.connect(str(db))
        c.execute("CREATE SCHEMA catalog")
        c.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
        observations = []
        specs = {
            "profit": ("monthly", "flow", "TRY", "TRY", "Net Kâr"),
            "partial": ("monthly", "flow", "TRY", "TRY", "Eksik Kâr"),
            "duplicate_month": ("monthly", "flow", "TRY", "TRY", "Yinelenmiş Ay"),
            "quarter_flow": ("quarterly", "count_flow", "person", None, "Yeni Başvurular"),
            "partial_quarter": ("quarterly", "flow", "TRY", "TRY", "Eksik Çeyrek"),
            "cpi": ("monthly", "index", "index", None, "Genel Endeks"),
            "industry": ("monthly", "index", "index", None, "Sanayi Üretim Endeksi"),
            "gold": ("monthly", "stock", "TRY", "TRY", "Altın Mevduatı"),
            "per_person": ("quarterly", "ratio", "TRY/person", "TRY", "Kişi Başı Nakdi Kredi"),
            "percent": ("monthly", "ratio", "percent", None, "İşsizlik Oranı"),
            "fraction": ("monthly", "ratio", "ratio", None, "Fraction"),
            "capital": ("monthly", "ratio", "percent", None, "Sermaye Yeterliliği"),
        }
        for metric, (frequency, kind, unit, currency, title) in specs.items():
            binding = {"metric_id": metric, "title": title, "source_system": "FIXTURE",
                       "table": "main.observations", "time_column": "period", "value_column": "value",
                       "filters": {"metric": metric}, "dimensions": {"city": "city", "group_code": "group_code"},
                       "native_frequency": frequency, "kind": kind, "unit": unit, "scale": 1,
                       "currency": currency, "status": "ready", "notes": [], "contract_version": "test",
                       "provenance_columns": ["source_file", "source_sha256", "source_row_index"],
                       "institution_scope": "Fixture all-bank reporting population"}
            if metric == "cpi":
                binding.update(source_system="TCMB_EVDS", source_code="TP.TUKFIY2025.GENEL")
            c.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [metric, json.dumps(binding)])
            for city_index, city in enumerate(["ANKARA", "İSTANBUL", "İZMİR"]):
                periods = pd.period_range("2026-01", "2026-12", freq="M" if frequency == "monthly" else "Q")
                for index, period in enumerate(periods):
                    if metric == "partial_quarter" and index == 1:
                        continue
                    value = [100000, 69401, 119287, *range(1, 10)][index] if metric in {"profit", "partial", "duplicate_month"} else (index + 1) * 10
                    if metric == "gold":
                        value = [100, 300, 200][city_index] + index
                    if metric == "per_person":
                        value = [100, 120, 110, 150][index]
                    if metric in {"cpi", "industry"}:
                        value = 100 + index * 10
                    if metric == "partial" and index == 1:
                        value = None
                    label = str(period).replace("Q", "-Q")
                    if metric == "duplicate_month" and index == 1:
                        label = "2026-01-31"
                    observations.append({"metric": metric, "period": label, "city": city,
                                         "group_code": 10001, "group_name": "Tüm Bankalar",
                                         "value": value, "source_file": "fixture.json",
                                         "source_sha256": "a" * 64, "source_row_index": index})
        c.register("fixture", pd.DataFrame(observations))
        c.execute("CREATE TABLE observations AS SELECT * FROM fixture")
        c.close()
        cls.store = LakehouseStore(cls.root / "store")
        cls.snapshot = cls.store.publish_snapshot(db)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        workspace = self.store.create_workspace(self.snapshot["snapshot_id"])
        self.service = LakehouseService(self.store, workspace["workspace_id"])

    def selection(self, metric, alignment="native"):
        return {"name": metric, "metric_id": metric, "dimensions": {"city": "ANKARA", "group_code": 10001}, "alignment": alignment}

    def plan(self, metric, frequency="monthly", alignment="native", start="2026-01", end="2026-03"):
        return {"start": start, "end": end, "frequency": frequency, "columns": [self.selection(metric, alignment)]}

    def test_last_subperiod_cannot_masquerade_as_quarterly_profit(self):
        plan = self.plan("profit", "quarterly", "last", "2026-Q1", "2026-Q1")
        blocked = self.service.validate_plan(plan)
        self.assertEqual("INVALID_TEMPORAL_AGGREGATION", blocked["errors"][0]["code"])
        plan["columns"][0]["alignment"] = "sum"
        result = self.service.execute(plan)
        self.assertEqual(288688, result["preview"][0]["profit"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "profit", "period": "2026-Q1"})
        self.assertEqual(3, len(proof["lineage"]["source_cells"]))
        self.assertTrue(proof["source_references_complete"])

    def test_complete_monthly_and_quarterly_annual_flow_sums(self):
        for metric, expected in [("profit", 288733), ("quarter_flow", 100)]:
            with self.subTest(metric=metric):
                result = self.service.execute(self.plan(metric, "annual", "sum", "2026", "2026"))
                self.assertEqual(expected, result["preview"][0][metric])
                self.assertEqual("INVALID_TEMPORAL_AGGREGATION", self.service.validate_plan(self.plan(metric, "annual", "last", "2026", "2026"))["errors"][0]["code"])

    def test_missing_numeric_month_and_missing_quarter_never_form_complete_sum(self):
        for metric in ("partial", "partial_quarter"):
            result = self.service.execute(self.plan(metric, "annual", "sum", "2026", "2026"))
            self.assertIsNone(result["preview"][0][metric])
            self.assertTrue(any(w["code"] == "partial_period_blocked" for w in result["warnings"]))

    def test_distinct_dates_in_same_native_month_are_not_three_complete_months(self):
        result = self.service.validate_plan(self.plan("duplicate_month", "quarterly", "sum", "2026-Q1", "2026-Q1"))
        self.assertEqual("AMBIGUOUS_GRAIN", result["errors"][0]["code"])

    def test_production_index_is_not_an_inflation_deflator(self):
        plan = self.plan("profit")
        plan["columns"].append(self.selection("industry"))
        plan["operations"] = [{"op": "deflate", "column": "profit", "index": "industry", "base_period": "2026-01", "output": "real"}]
        self.assertEqual("INVALID_DEFLATOR_ROLE", self.service.validate_plan(plan)["errors"][0]["code"])
        plan["columns"][1] = self.selection("cpi")
        plan["operations"][0]["index"] = "cpi"
        result = self.service.execute(plan)
        self.assertAlmostEqual(69401 / 110 * 100, result["preview"][1]["real"])
        self.assertEqual("price_deflator", self.service.describe({"metric_id": "cpi"})["metric"]["index_role"])

    def test_price_index_difference_does_not_inherit_deflator_role(self):
        plan = self.plan("profit")
        plan["columns"].append(self.selection("cpi"))
        plan["operations"] = [{"op": "difference", "column": "cpi", "output": "index_delta"}, {"op": "deflate", "column": "profit", "index": "index_delta", "base_period": "2026-02", "output": "real"}]
        self.assertEqual("INVALID_DEFLATOR_ROLE", self.service.validate_plan(plan)["errors"][0]["code"])

    def test_per_person_difference_and_growth_have_distinct_correct_units(self):
        plan = self.plan("per_person", "quarterly", "native", "2026-Q1", "2026-Q4")
        plan["operations"] = [{"op": "difference", "column": "per_person", "output": "change"}, {"op": "growth", "column": "per_person", "output": "growth"}]
        result = self.service.execute(plan)
        self.assertEqual(20, result["preview"][1]["change"])
        self.assertAlmostEqual(20, result["preview"][1]["growth"])
        self.assertEqual("TRY/person", result["schema"]["change"]["unit"])
        self.assertEqual("percent", result["schema"]["growth"]["unit"])
        percentage = self.plan("percent")
        percentage["operations"] = [{"op": "difference", "column": "percent", "output": "delta"}]
        self.assertEqual("percentage_points", self.service.execute(percentage)["schema"]["delta"]["unit"])
        percentage["operations"][0]["op"] = "growth"
        self.assertEqual("UNIT_MISMATCH", self.service.validate_plan(percentage)["errors"][0]["code"])

    def test_search_resolves_city_and_controlled_language_variations(self):
        result = self.service.discover({"query": "İstanbul altın mevduatı"})
        self.assertEqual("gold", result["metrics"][0]["metric_id"])
        self.assertEqual(["İSTANBUL"], result["metrics"][0]["matched_dimensions"]["city"])
        self.assertEqual("capital", self.service.discover({"query": "sermaye yeterlilik"})["metrics"][0]["metric_id"])
        self.assertEqual("percent", self.service.discover({"query": "unemployment"})["metrics"][0]["metric_id"])

    def test_balance_search_alias_is_only_added_to_monetary_stocks(self):
        self.assertEqual("gold", self.service.discover({"query": "altın mevduatı bakiyesi"})["metrics"][0]["metric_id"])
        self.assertEqual(0, self.service.discover({"query": "net kâr bakiyesi"})["total"])
        self.assertEqual(0, self.service.discover({"query": "yeni başvurular stoku"})["total"])

    def test_dimension_value_interior_substring_is_not_a_confident_match(self):
        # "anka" is an interior fragment of the province "ANKARA". A concept token
        # must match a dimension VALUE as a whole token, so an unsatisfiable request
        # surfaces no_confident_match instead of a spurious full match. This is the
        # general form of the gümüş/Gümüşhane collision (silver vs the province).
        partial = self.service.discover({"query": "anka mevduatı"})
        self.assertEqual(0, partial["total"])
        self.assertTrue(partial.get("no_confident_match"))
        self.assertIn("anka", partial.get("uncovered_terms", []))
        # The whole province name still matches as a dimension value.
        full = self.service.discover({"query": "ankara altın mevduatı"})
        self.assertEqual("gold", full["metrics"][0]["metric_id"])
        self.assertEqual(["ANKARA"], full["metrics"][0]["matched_dimensions"]["city"])

    def test_no_confident_match_surfaces_near_candidates_and_uncovered_terms(self):
        # "gümüş mevduatı" has no series: "mevduat" partially matches the gold
        # deposit, "gumus" matches nothing. Instead of a bare empty result that
        # invites re-search loops, discover flags no_confident_match, names the
        # unresolved term, and surfaces the nearest real series as a hint.
        result = self.service.discover({"query": "gümüş mevduatı"})
        self.assertEqual(0, result["total"])
        self.assertEqual([], result["metrics"])
        self.assertTrue(result.get("no_confident_match"))
        self.assertIn("gumus", result.get("uncovered_terms", []))
        self.assertNotIn("mevduat", result.get("uncovered_terms", []))
        self.assertIn("gold", [card["metric_id"] for card in result.get("near_matches", [])])
        # A genuinely full match must NOT set the no-confident-match signal.
        confident = self.service.discover({"query": "altın mevduatı"})
        self.assertFalse(confident.get("no_confident_match"))
        self.assertNotIn("near_matches", confident)

    def test_dimension_values_provide_source_group_labels_and_exact_city_values(self):
        group = self.service.dimension_values({"metric_id": "gold", "dimension": "group_code", "query": "tüm bankalar"})
        self.assertEqual(10001, group["values"][0]["value"])
        self.assertEqual("Tüm Bankalar", group["values"][0]["label"])
        city = self.service.dimension_values({"metric_id": "gold", "dimension": "city", "query": "istanbul"})
        self.assertEqual("İSTANBUL", city["values"][0]["value"])
        injected = self.service.dimension_values({"metric_id": "gold", "dimension": "city", "query": "' OR 1=1"})
        self.assertEqual(0, injected["total"])

    def test_grouped_ranking_preserves_members_and_cell_lineage(self):
        request = {"metric_id": "gold", "group_by": "city", "dimensions": {"group_code": 10001}, "start": "2026-01", "end": "2026-02", "frequency": "monthly", "limit": 2}
        result = self.service.query_grouped(request)
        self.assertEqual(4, result["row_count"])
        self.assertEqual(3, result["group_count"])
        self.assertEqual(["İSTANBUL", "İZMİR"], [row["city"] for row in result["preview"][:2]])
        self.assertEqual([300, 200], [row["value"] for row in result["preview"][:2]])
        self.assertTrue(result["truncated"])
        proof = self.service.explain_value({"analysis_id": result["analysis_id"], "column": "value", "period": "2026-01", "dimensions": {"city": "İSTANBUL"}})
        self.assertEqual(300, proof["value"])
        self.assertEqual("İSTANBUL", proof["lineage"]["dimensions"]["city"])
        self.assertTrue(proof["source_references_complete"])
        _, manifest = self.store.load_analysis(result["analysis_id"])
        self.assertEqual({"query_type": "grouped", "request": request}, manifest["plan"])
        with self.assertRaisesRegex(PlanError, "Grouped explanation"):
            self.service.explain_value({"analysis_id": result["analysis_id"], "column": "value", "period": "2026-01"})
        with self.assertRaises(PlanError) as blocked:
            self.service.revise_analysis({"analysis_id": result["analysis_id"], "operations": [{"op": "difference", "column": "value", "output": "change"}]})
        self.assertEqual("GROUPED_REVISION_UNSUPPORTED", blocked.exception.code)

    def test_grouped_flow_obeys_the_same_temporal_guard(self):
        with self.assertRaises(PlanError) as blocked:
            self.service.query_grouped({"metric_id": "profit", "group_by": "city", "dimensions": {"group_code": 10001}, "start": "2026-Q1", "end": "2026-Q1", "frequency": "quarterly", "alignment": "last"})
        self.assertEqual("INVALID_TEMPORAL_AGGREGATION", blocked.exception.code)

    def test_revise_rejects_a_duplicate_series_column(self):
        analysis = self.service.execute(self.plan("gold"))
        # Re-adding the same metric+dimensions (the "add earlier years" mistake on a time
        # series) would repeat identical values, so revise refuses it under a new name too.
        with self.assertRaises(PlanError) as blocked:
            self.service.revise_analysis({"analysis_id": analysis["analysis_id"],
                "add_columns": [{"name": "gold_2", "metric_id": "gold",
                                 "dimensions": {"city": "ANKARA", "group_code": 10001}, "alignment": "native"}]})
        self.assertEqual("DUPLICATE_COLUMN", blocked.exception.code)

    def test_warnings_survive_reload_for_scalar_and_grouped_results(self):
        scalar = self.service.execute(self.plan("partial", "annual", "sum", "2026", "2026"))
        grouped = self.service.query_grouped({"metric_id": "gold", "group_by": "city", "dimensions": {"group_code": 10001}, "start": "2026-01", "end": "2026-01", "frequency": "monthly"})
        for result in (scalar, grouped):
            frame, manifest = self.store.load_analysis(result["analysis_id"])
            self.assertTrue(result["warnings"])
            self.assertEqual(result["warnings"], manifest["lineage"]["warnings"])
            self.assertEqual(result["warnings"], self.service._envelope(frame, manifest, [])["warnings"])
            recovered = self.service._envelope(frame, manifest, [{"code": "recovered"}])
            self.assertEqual(result["warnings"], recovered["warnings"][:-1])

    def test_revision_preserves_parent_cell_evidence_and_durable_warnings(self):
        parent = self.service.execute(self.plan("partial"))
        original_proof = self.service.explain_value({"analysis_id": parent["analysis_id"], "column": "partial", "period": "2026-03"})
        revised = self.service.revise_analysis({"analysis_id": parent["analysis_id"], "add_columns": [self.selection("profit")]})
        frame, manifest = self.store.load_analysis(revised["analysis_id"])
        proof = self.service.explain_value({"analysis_id": revised["analysis_id"], "column": "partial", "period": "2026-03"})
        self.assertEqual(parent["analysis_id"], proof["inherited_from_analysis_id"])
        self.assertEqual(original_proof["lineage"], proof["lineage"])
        self.assertEqual(original_proof["value"], proof["value"])
        self.assertEqual(parent["analysis_id"], manifest["lineage"]["preserved_columns"]["partial"]["analysis_id"])
        self.assertEqual(revised["warnings"], self.service._envelope(frame, manifest, [])["warnings"])
        self.assertTrue(all(warning in revised["warnings"] for warning in parent["warnings"]))


class GenericRegistryTests(unittest.TestCase):
    def test_same_declared_origin_does_not_merge_independent_dataset_populations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "generic.duckdb"
            with duckdb.connect(str(db)) as c:
                c.execute("CREATE TABLE platform_metadata(name VARCHAR)")
            store = LakehouseStore(root / "store")
            snapshot = store.publish_snapshot(db)
            workspace = store.create_workspace(snapshot["snapshot_id"])
            contract = {"name": "clinic_counts", "source_namespace": "external:shared-origin",
                        "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "visits": {"dtype": "integer", "unit": "person", "kind": "count_flow", "nullable": False}},
                        "key": ["month"], "grain": ["month"], "date_column": "month", "frequency": "monthly"}
            for index, value in enumerate([100, 200]):
                source = root / f"clinic-{index}.csv"
                source.write_text(f"month,visits\n2026-01,{value}\n", encoding="utf-8")
                workspace = store.ingest_csv(workspace["workspace_id"], source, contract, expected_version=index)
            service = LakehouseService(store, workspace["workspace_id"])
            selections = [{"name": f"cohort{index}", "metric_id": f"overlay:{dataset}:visits"} for index, dataset in enumerate(workspace["datasets"])]
            for selection in selections:
                binding = service.describe({"metric_id": selection["metric_id"]})["metric"]
                self.assertEqual("external:shared-origin", binding["source_namespace"])
                self.assertEqual(binding["dataset_id"], binding["scope_namespace"])
            plan = {"start": "2026-01", "end": "2026-01", "frequency": "monthly", "columns": selections,
                    "operations": [{"op": "ratio", "column": "cohort0", "denominator": "cohort1", "output": "share"}]}
            self.assertEqual("SCOPE_MISMATCH", service.validate_plan(plan)["errors"][0]["code"])

    def test_group_ranks_preserve_large_integer_ties_and_leave_nulls_unranked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "generic.duckdb"
            with duckdb.connect(str(db)) as c:
                c.execute("CREATE TABLE platform_metadata(name VARCHAR)")
            store = LakehouseStore(root / "store")
            snapshot = store.publish_snapshot(db)
            workspace = store.create_workspace(snapshot["snapshot_id"])
            source = root / "counts.csv"
            source.write_text("month,city,count\n2026-01,A,9007199254740992\n2026-01,B,9007199254740993\n2026-01,C,9007199254740993\n2026-01,D,\n", encoding="utf-8")
            contract = {"name": "native_counters", "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "city": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}, "count": {"dtype": "integer", "unit": "count", "kind": "count_stock", "nullable": True}}, "key": ["month", "city"], "grain": ["month", "city"], "date_column": "month", "frequency": "monthly"}
            workspace = store.ingest_csv(workspace["workspace_id"], source, contract, expected_version=0)
            service = LakehouseService(store, workspace["workspace_id"])
            result = service.query_grouped({"metric_id": f"overlay:{workspace['datasets'][0]}:count", "group_by": "city", "dimensions": {}, "start": "2026-01", "end": "2026-01", "frequency": "monthly", "limit": 4})
            self.assertEqual(["B", "C", "A", "D"], [row["city"] for row in result["preview"]])
            self.assertEqual([1, 1, 3, None], [row["rank"] for row in result["preview"]])
            self.assertEqual(9007199254740993, result["preview"][0]["value"])
            self.assertIsNone(result["preview"][3]["value"])

    def test_finance_free_snapshot_supports_discovery_ingest_and_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = root / "generic.duckdb"
            with duckdb.connect(str(db)) as c:
                c.execute("CREATE TABLE platform_metadata(name VARCHAR)")
                self.assertEqual({}, get_bindings(c))
                self.assertEqual(0, install_bindings(c)["count"])
                self.assertEqual({}, get_bindings(c))
            store = LakehouseStore(root / "store")
            snapshot = store.publish_snapshot(db)
            workspace = store.create_workspace(snapshot["snapshot_id"])
            service = LakehouseService(store, workspace["workspace_id"])
            self.assertEqual(0, service.discover({"query": ""})["total"])
            source = root / "health.csv"
            source.write_text("month,city,visits\n2026-01,Ankara,100\n2026-02,Ankara,120\n", encoding="utf-8")
            contract = {"name": "clinic_visits", "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "city": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}, "visits": {"dtype": "integer", "unit": "person", "kind": "count_flow", "nullable": False}}, "key": ["month", "city"], "grain": ["month", "city"], "date_column": "month", "frequency": "monthly"}
            workspace = store.ingest_csv(workspace["workspace_id"], source, contract, expected_version=0)
            metric_id = f"overlay:{workspace['datasets'][0]}:visits"
            result = service.execute({"start": "2026-01", "end": "2026-02", "frequency": "monthly", "columns": [{"name": "visits", "metric_id": metric_id, "dimensions": {"city": "Ankara"}}], "operations": [{"op": "growth", "column": "visits", "output": "growth"}]})
            self.assertAlmostEqual(20, result["preview"][1]["growth"])

    def test_source_catalog_without_bindings_is_not_silently_treated_as_generic(self):
        with duckdb.connect() as c:
            c.execute("CREATE SCHEMA catalog")
            c.execute("CREATE TABLE catalog.metrics(metric_id VARCHAR)")
            with self.assertRaisesRegex(ValueError, "contracts are absent"):
                get_bindings(c)


@unittest.skipUnless((Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb").is_file(), "Published snapshot unavailable")
class RealDiscoveryRegressionTests(unittest.TestCase):
    def test_housing_loan_inflections_recall_monthly_source_and_rank_frequency_compatibility(self):
        database = Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, workspace_id):
                return {"snapshot_id": "real-discovery", "datasets": []}

            def snapshot_path(self, snapshot_id):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-discovery")
        expected = "bddk_monthly:table04:2:fffae80eca08:Toplam"
        broad = service.discover({"query": "konut kredisi", "limit": 25, "status": "ready"})
        self.assertIn(expected, [metric["metric_id"] for metric in broad["metrics"]])
        for query in ("BDDK aylık konut kredisi bakiyesi", "BDDK aylık bülten konut kredilerinin stoku"):
            with self.subTest(query=query):
                result = service.discover({"query": query, "limit": 5, "status": "ready"})
                self.assertIn(expected, [metric["metric_id"] for metric in result["metrics"]])
                self.assertTrue(all(not metric["frequency_hint_requires_upsampling"] for metric in result["metrics"]))
                self.assertTrue(all(metric["native_frequency"] == "monthly" for metric in result["metrics"]))

    def test_live_natural_bank_profit_queries_surface_the_actual_profit_in_top_five(self):
        database = Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, workspace_id):
                return {"snapshot_id": "real-discovery", "datasets": []}

            def snapshot_path(self, snapshot_id):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-discovery")
        expected = "bddk_monthly:table02:53:ef40239f1db4:Toplam"
        for query in ("bankacılık sektörü net kâr", "bankalar net kâr aylık", "banka net kâr", "2026 ilk çeyrekte bankaların net kârını hesapla", "2026 ilk çeyrekte bankacılık sektörünün aylık net kârını tablo olarak göster"):
            with self.subTest(query=query):
                result = service.discover({"query": query, "limit": 5, "status": "ready"})
                self.assertIn(expected, [metric["metric_id"] for metric in result["metrics"]])
                self.assertNotIn("NET FAİZ", result["metrics"][0]["title"])

    def test_structural_fields_surface_and_total_slice_outranks_currency_splits(self):
        database = Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, workspace_id):
                return {"snapshot_id": "real-discovery", "datasets": []}

            def snapshot_path(self, snapshot_id):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-discovery")
        result = service.discover({"query": "net kâr", "limit": 5, "status": "ready"})
        # The structural slice token is propagated to the model-facing card so the
        # agent can distinguish a canonical metric from its decoy siblings.
        self.assertTrue(all("value_dimension" in metric for metric in result["metrics"]))
        # The aggregate ':Toplam' slice must outrank its ':Tp'/':Yp' currency-split
        # siblings of the same metric (a structural rule, not an alphabetical accident).
        order = {metric["metric_id"]: index for index, metric in enumerate(result["metrics"])}
        base = "bddk_monthly:table02:53:ef40239f1db4"
        self.assertIn(f"{base}:Toplam", order)
        for split in (":Tp", ":Yp"):
            if base + split in order:
                self.assertLess(order[f"{base}:Toplam"], order[base + split])


if __name__ == "__main__":
    unittest.main()
