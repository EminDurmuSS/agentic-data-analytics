import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
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
                "CROSS_SOURCE_QUALITY",
            }
            <= systems
        )
        self.assertEqual([], self.validation["missing_required_datasets"])

    def test_evds_metadata_and_local_observations_are_not_confused(self):
        evds = self.metrics.loc[self.metrics["source_system"].eq("TCMB_EVDS")]
        self.assertEqual(52696, len(evds))
        self.assertEqual(61, int(evds["observation_available"].sum()))
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
            "bddk.weekly_all_sector.missingness_audit",
            "bddk.finturk_all_groups_all_cities.missingness_audit",
            "evds.housing_causality_v1.coverage_gaps",
            "evds.housing_causality_controls_v1.coverage_gaps",
            "evds.market_controls_v2.coverage_gaps",
            "tbb.consumer_credit_reports.source_gaps",
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


if __name__ == "__main__":
    unittest.main()
