"""Source population contracts cannot be inferred from shared total labels."""

from pathlib import Path
import unittest

from agentic_analytics.lakehouse.discovery import compare_intent, query_intent, semantic_profile
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.source_profiles import apply_source_profile


def binding(table):
    # Synthetic IDs and cells. Only the documented source table categories are real.
    return apply_source_profile({"metric_id": f"fixture_{table}", "dataset_id": f"bddk.monthly_all_groups.table_{table:02}",
        "source_system": "BDDK_MONTHLY", "title": "Krediler [Toplam]", "kind": "stock", "unit": "TRY", "currency": "TRY",
        "scale": 1, "native_frequency": "monthly", "status": "ready", "table": "source", "time_column": "period",
        "value_column": "value", "dimensions": {}, "scope_namespace": "BDDK_MONTHLY", "value_dimension": "Toplam"})


class SourcePopulationTests(unittest.TestCase):
    def test_documented_table_categories_define_matching_and_distinct_populations(self):
        self.assertEqual(binding(1)["population_scope"], binding(2)["population_scope"])
        self.assertEqual(binding(3)["population_scope"], binding(5)["population_scope"])
        self.assertNotEqual(binding(1)["population_scope"], binding(3)["population_scope"])
        self.assertEqual("domestic_resident_customers", binding(4)["population_scope"]["customer_residency"])
        self.assertTrue(binding(3)["source_scope_evidence"]["explanations_url"].startswith("https://www.bddk.gov.tr/"))

    def test_restricted_total_is_reviewable_and_weighted_credit_is_not_cash_balance(self):
        intent = query_intent("bankacılık sektörü toplam nakdi kredi bakiyesi")
        overall = compare_intent(intent, semantic_profile(binding(1)))
        restricted = compare_intent(intent, semantic_profile(binding(3)))
        weighted = compare_intent(intent, semantic_profile(binding(11)))
        self.assertLess(overall["penalty"], restricted["penalty"])
        self.assertIn("restricted_reporting_population", [item["code"] for item in restricted["warnings"]])
        self.assertIn("measurement_basis", [item["facet"] for item in weighted["conflicts"]])
        desired = compare_intent(query_intent("likidite krediler"), semantic_profile(binding(11)))
        self.assertNotIn("measurement_basis", [item["facet"] for item in desired["conflicts"]])

    def test_source_population_signature_blocks_unacknowledged_ratio(self):
        service = LakehouseService(None, "fixture")
        metrics = {str(n): binding(n) for n in (1, 2, 3, 11)}
        def plan(second, explicit=False):
            operation = {"op": "ratio", "column": "a", "denominator": "b", "output": "ratio"}
            if explicit:
                operation.update(scope_policy="explicit_comparison", scope_reason="Intentional numerical comparison of the two documented reporting populations.")
            return {"frequency": "monthly", "start": "2026-01", "end": "2026-01", "columns": [
                {"name": "a", "metric_id": "1"}, {"name": "b", "metric_id": str(second)}], "operations": [operation]}
        service._validate(plan(2), metrics)
        with self.assertRaises(PlanError) as error:
            service._validate(plan(3), metrics)
        self.assertEqual("SCOPE_MISMATCH", error.exception.code)
        service._validate(plan(3, explicit=True), metrics)
        with self.assertRaises(PlanError) as error:
            service._validate(plan(11, explicit=True), metrics)
        self.assertEqual("UNIT_MISMATCH", error.exception.code)


@unittest.skipUnless((Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb").exists(), "Published catalogue unavailable")
class RealPopulationCatalogueTests(unittest.TestCase):
    def test_describe_and_discovery_expose_actual_source_population_difference(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"
        class Store:
            def workspace(self, _):
                return {"snapshot_id": "actual-catalogue", "datasets": []}
            def snapshot_path(self, _):
                return database
        service = LakehouseService(Store(), "actual-catalogue")
        with service._context() as (_, bindings, _):
            broad = bindings["bddk_monthly:table01:10:d19739aeda4a:Toplam"]
            limited = bindings["bddk_monthly:table03:20:3d89724a2572:Toplam"]
            self.assertNotEqual(broad["population_scope"], limited["population_scope"])
            self.assertTrue(limited["scope_caveats"])
        result = service.discover({"query": "BDDK bankacılık sektörü toplam nakdi kredi bakiyesi aylık", "status": "ready", "limit": 25})
        self.assertEqual(broad["metric_id"], result["metrics"][0]["metric_id"])
        restricted = [card for card in result["metrics"] if card["metric_id"] == limited["metric_id"]]
        self.assertTrue(restricted)
        self.assertIn("restricted_reporting_population", [item["code"] for item in restricted[0]["semantic_match"]["warnings"]])
