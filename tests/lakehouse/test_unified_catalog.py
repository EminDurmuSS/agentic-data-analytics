import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data_pipeline" / "catalog" / "unified"


class UnifiedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (CATALOG / "validation.json").read_text(encoding="utf-8")
        )
        cls.assets = pd.read_parquet(CATALOG / "unified_data_catalog.parquet")
        cls.metrics = pd.read_parquet(CATALOG / "unified_metric_catalog.parquet")

    def test_catalog_is_valid_and_has_unique_ids(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertFalse(self.assets["asset_id"].duplicated().any())
        self.assertFalse(self.metrics["metric_id"].duplicated().any())
        self.assertEqual(0, self.validation["absolute_asset_paths"])
        self.assertEqual([], self.validation["missing_asset_files"])

    def test_required_source_families_are_discoverable(self):
        systems = set(self.assets["source_system"])
        self.assertTrue(
            {
                "TCMB_EVDS",
                "BDDK_MONTHLY",
                "BDDK_WEEKLY",
                "BDDK_FINTURK",
                "TUIK_DATA_PORTAL",
                "CROSS_SOURCE_QUALITY",
                "TBB_RISK_CENTER",
            }
            <= systems
        )
        self.assertEqual([], self.validation["missing_required_datasets"])

    def test_evds_metadata_and_local_observations_are_not_confused(self):
        evds = self.metrics.loc[self.metrics["source_system"].eq("TCMB_EVDS")]
        self.assertEqual(52696, len(evds))
        self.assertEqual(611, int(evds["observation_available"].sum()))
        legacy = evds.loc[evds["dataset_id"].eq("evds.legacy_native")]
        self.assertEqual(15, len(legacy))
        self.assertTrue(legacy["observation_available"].all())
        self.assertTrue(legacy["observation_count"].eq(78).all())
        self.assertTrue(legacy["native_frequency"].eq("monthly").all())
        self.assertFalse(self.validation["evds_full_observation_coverage_complete"])
        metadata_only = evds.loc[~evds["observation_available"]]
        self.assertTrue(metadata_only["quality_status"].eq("metadata_only").all())
        self.assertTrue(metadata_only["observation_count"].eq(0).all())
        derived = self.metrics.loc[
            self.metrics["source_system"].eq("TCMB_EVDS_DERIVED")
        ]
        self.assertEqual(
            ["DERIVED.BIST.GOLD.TL.GR"],
            derived["source_metric_code"].tolist(),
        )

    def test_missingness_and_source_gap_assets_are_discoverable(self):
        expected = {
            "bddk.weekly_all_groups.missingness_audit",
            "bddk.finturk_all_groups_all_cities.missingness_audit",
            "bddk.finturk_all_groups_all_cities.branch_zero_fallback_audit",
            "evds.housing_causality_v1.coverage_gaps",
            "evds.housing_causality_controls_v1.coverage_gaps",
            "evds.market_controls_v2.coverage_gaps",
            "evds.regional_housing_v1.coverage_gaps",
            "evds.household_finance_v1.coverage_gaps",
            "tbb.consumer_credit_reports.source_gaps",
            "tuik.province_housing_sales_v1.identity_zero_fallbacks",
            "regional.housing_v1.housing_unit_price_proxy_audit",
        }
        self.assertTrue(expected <= set(self.assets["asset_id"]))

    def test_stock_and_flow_semantics_remain_separate(self):
        finturk_housing = self.metrics.loc[
            self.metrics["metric_id"].eq("bddk_finturk:table03:KonutKredisi")
        ].iloc[0]
        self.assertEqual("period_end_stock", finturk_housing["temporal_semantics"])

        tbb_flow = self.metrics.loc[
            (self.metrics["source_system"] == "TBB_REPORTS")
            & (self.metrics["role"] == "credit_disbursement")
            & self.metrics["source_metric_code"].str.contains("housing")
        ]
        self.assertFalse(tbb_flow.empty)
        self.assertTrue(tbb_flow["temporal_semantics"].eq("flow").all())
        self.assertTrue(tbb_flow["native_frequency"].eq("quarterly").all())

    def test_weekly_credit_table_has_narrow_reviewed_stock_semantics(self):
        weekly = self.metrics.loc[
            self.metrics["metric_id"].eq("bddk_weekly:table289_289_4_total")
        ].iloc[0]
        self.assertEqual("weekly_observed", weekly["native_frequency"])
        self.assertEqual("source_date_stock", weekly["temporal_semantics"])
        self.assertEqual("last", weekly["default_aggregation"])
        self.assertEqual("provisional_revisable", weekly["revision_status"])
        self.assertEqual(
            "https://www.bddk.org.tr/BultenHaftalik/tr/Home/Aciklama",
            weekly["source_metadata_url"],
        )

        other = self.metrics.loc[
            self.metrics["metric_id"].eq("bddk_weekly:table290_290_1_total")
        ]
        self.assertFalse(other.empty)
        self.assertEqual(
            "source_reported_requires_semantic_review",
            other.iloc[0]["temporal_semantics"],
        )

    def test_queryable_metrics_have_local_relative_assets(self):
        queryable = self.metrics.loc[self.metrics["observation_available"]]
        self.assertTrue(queryable["metric_name_tr"].fillna("").ne("").all())
        self.assertTrue(queryable["source_asset"].fillna("").ne("").all())
        self.assertFalse(queryable["source_asset"].str.startswith("/").any())
        missing = [
            path
            for path in queryable["source_asset"].unique()
            if not (ROOT / path).exists()
        ]
        self.assertEqual([], missing)

    def test_regional_analysis_metrics_are_discoverable(self):
        regional = self.metrics.loc[
            self.metrics["source_system"].eq("REGIONAL_HOUSING_ANALYSIS")
        ]
        self.assertEqual(33, len(regional))
        self.assertTrue(regional["observation_available"].all())
        self.assertIn(
            "regional_housing:housing_credit_per_capita_try",
            set(regional["metric_id"]),
        )
        proxy_origin = regional.loc[
            regional["metric_id"].eq(
                "regional_housing:housing_unit_price_proxy_origin"
            )
        ].iloc[0]
        self.assertEqual(1782, proxy_origin["observation_count"])
        self.assertIn("value_type=categorical", proxy_origin["notes"])
        proxy_value = regional.loc[
            regional["metric_id"].eq(
                "regional_housing:housing_unit_price_with_proxy_try_per_m2"
            )
        ].iloc[0]
        self.assertEqual(1782, proxy_value["observation_count"])

    def test_tuik_sales_fallback_source_is_discoverable(self):
        metrics = self.metrics.loc[
            self.metrics["source_system"].eq("TUIK_DATA_PORTAL")
        ]
        self.assertEqual(10, len(metrics))
        self.assertTrue(metrics["observation_available"].all())
        current = metrics.loc[
            metrics["dataset_id"].eq("tuik.province_housing_sales_v1")
        ]
        first_published = metrics.loc[
            metrics["dataset_id"].eq(
                "tuik.province_housing_sales_first_published_v1"
            )
        ]
        self.assertEqual(5, len(current))
        self.assertEqual(5, len(first_published))
        self.assertTrue(
            current["vintage_policy"].eq("latest_official_bulk_snapshot").all()
        )
        self.assertTrue(
            first_published["vintage_policy"]
            .eq("first_official_publication_for_each_reference_month")
            .all()
        )
        self.assertTrue(current["is_archive"].eq(False).all())
        self.assertTrue(first_published["is_archive"].eq(True).all())
        self.assertEqual(
            set(current["source_metric_code"]),
            set(first_published["source_metric_code"]),
        )
        self.assertTrue(set(current["metric_id"]).isdisjoint(first_published["metric_id"]))
        mortgage = current.loc[
            current["source_metric_code"].eq("housing_sales_mortgaged_count")
        ].iloc[0]
        self.assertEqual(10, mortgage["missing_observation_count"])
        self.assertIn("identity-derived zero=10", mortgage["notes"])

    def test_risk_center_metrics_are_monthly_and_semantically_separate(self):
        metrics = self.metrics.loc[
            self.metrics["source_system"].eq("TBB_RISK_CENTER")
        ]
        self.assertEqual(5, len(metrics))
        self.assertTrue(metrics["observation_available"].all())
        self.assertTrue(metrics["observation_count"].eq(66).all())
        first_time = metrics.loc[
            metrics["source_metric_code"].eq(
                "first_time_housing_credit_users_thousand_person"
            )
        ].iloc[0]
        self.assertEqual("thousand_person", first_time["unit"])
        self.assertEqual("first_time_credit_users", first_time["role"])
        self.assertNotEqual("credit_disbursement", first_time["role"])
        self.assertIn("Kredi kullandırım tutarı değildir", first_time["notes"])

    def test_finturk_branch_metric_discloses_derived_usable_zeros(self):
        branch = self.metrics.loc[
            self.metrics["metric_id"].eq("bddk_finturk:table06:SubeSayisi")
        ].iloc[0]
        self.assertEqual(1328, branch["missing_observation_count"])
        self.assertIn("resolved for analytics=1328", branch["notes"])


if __name__ == "__main__":
    unittest.main()
