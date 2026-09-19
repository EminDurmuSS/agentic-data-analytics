import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "evds" / "housing_causality_controls_v1"
MARKET_DATA = ROOT / "data_pipeline" / "evds" / "market_controls_v2"


class EvdsCausalityControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (DATA / "validation.json").read_text(encoding="utf-8")
        )
        cls.alignment_validation = json.loads(
            (DATA / "alignment_validation.json").read_text(encoding="utf-8")
        )
        cls.observations = pd.read_parquet(DATA / "observations_long.parquet")
        cls.catalog = pd.read_parquet(DATA / "analysis_series_catalog.parquet")
        cls.monthly = pd.read_parquet(DATA / "monthly_panel.parquet")

    def test_snapshot_is_complete_and_unique(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(11, self.validation["series_count"])
        self.assertEqual(545, self.validation["observation_count"])
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_actual_policy_rate_has_full_target_coverage(self):
        policy_rate = self.observations.loc[
            self.observations["series_code"].eq("TP.BISPOLFAIZ.TUR")
        ]
        self.assertEqual(78, len(policy_rate))
        self.assertEqual(0, int(policy_rate["value"].isna().sum()))
        self.assertEqual("2020-01", policy_rate["period"].min())
        self.assertEqual("2026-06", policy_rate["period"].max())

    def test_quarterly_controls_are_not_filled_into_intermediate_months(self):
        self.assertEqual(
            0,
            self.alignment_validation[
                "quarterly_series_filled_into_intermediate_months"
            ],
        )
        april_2026 = self.monthly.loc[
            self.monthly["target_period"].eq("2026-04")
        ].iloc[0]
        self.assertTrue(pd.isna(april_2026["TP_GSYIH60_HY_B1GQ"]))
        self.assertTrue(pd.isna(april_2026["TP_BK_TR"]))

    def test_unpublished_gold_month_is_not_imputed(self):
        gold = self.catalog.loc[
            self.catalog["series_code"].eq("TP.MK.KUL.YTL")
        ].iloc[0]
        self.assertEqual("2026-05", gold["last_non_null_period"])
        june_2026 = self.monthly.loc[
            self.monthly["target_period"].eq("2026-06"), "TP_MK_KUL_YTL"
        ].iloc[0]
        self.assertTrue(pd.isna(june_2026))
        gaps = pd.read_parquet(DATA / "coverage_gaps.parquet")
        june_gap = gaps.loc[
            gaps["series_code"].eq("TP.MK.KUL.YTL")
            & gaps["period"].eq("2026-06")
        ].iloc[0]
        self.assertEqual("source_not_published", june_gap["missing_kind"])
        self.assertTrue(june_gap["is_unresolved_missing"])


class EvdsMarketControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (MARKET_DATA / "validation.json").read_text(encoding="utf-8")
        )
        cls.alignment_validation = json.loads(
            (MARKET_DATA / "alignment_validation.json").read_text(encoding="utf-8")
        )
        cls.observations = pd.read_parquet(
            MARKET_DATA / "observations_long.parquet"
        )
        cls.monthly = pd.read_parquet(MARKET_DATA / "monthly_panel.parquet")
        cls.monthly_audit = pd.read_parquet(
            MARKET_DATA / "monthly_alignment_audit.parquet"
        )
        cls.catalog = pd.read_parquet(
            MARKET_DATA / "analysis_series_catalog.parquet"
        )

    def test_market_snapshot_is_unique(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(3, self.validation["series_count"])
        self.assertEqual(3, self.alignment_validation["source_series_count"])
        self.assertEqual(1, self.alignment_validation["derived_series_count"])
        self.assertEqual(4, self.alignment_validation["series_count"])
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_bist_100_reaches_target_end_without_imputation(self):
        bist = self.observations.loc[
            self.observations["series_code"].eq("TP.MK.F.BILESIK")
            & self.observations["value"].notna()
        ]
        self.assertEqual("2026-06-30", bist["period"].max())
        june_2026 = self.monthly.loc[
            self.monthly["target_period"].eq("2026-06"), "TP_MK_F_BILESIK"
        ].iloc[0]
        self.assertFalse(pd.isna(june_2026))

    def test_bist_history_uses_last_real_business_day_and_keeps_name_change(self):
        bist = self.observations.loc[
            self.observations["series_code"].eq("TP.MK.F.BILESIK")
        ].copy()
        self.assertEqual("2010-01-01", bist["period"].min())
        july_2010 = self.monthly_audit.loc[
            self.monthly_audit["series_code"].eq("TP.MK.F.BILESIK")
            & self.monthly_audit["target_period"].eq("2010-07")
        ].iloc[0]
        observed = bist.loc[
            bist["period"].str.startswith("2010-07") & bist["value"].notna()
        ].sort_values("period")
        self.assertEqual(observed.iloc[-1]["period"], july_2010["selected_source_period"])
        self.assertEqual(observed.iloc[-1]["period_end"], july_2010["selected_source_period_end"])
        self.assertEqual(observed.iloc[-1]["value"], july_2010["value"])
        self.assertNotEqual("2010-07-31", july_2010["selected_source_period_end"])

        metadata = self.catalog.loc[
            self.catalog["series_code"].eq("TP.MK.F.BILESIK")
        ].iloc[0]
        self.assertEqual("2010-01-01", metadata["requested_start"])
        self.assertEqual("XU100", metadata["canonical_index_code"])
        self.assertEqual("İMKB 100", metadata["historical_name"])
        self.assertEqual("BIST 100", metadata["current_name"])
        self.assertEqual("2013-04-05", metadata["name_change_effective_date"])
        self.assertIn("GenelMektup_4030", metadata["name_change_source_url"])
        self.assertIn("bist-pay-endeksleri", metadata["methodology_source_url"])

    def test_tl_per_gram_gold_is_an_explicit_unit_conversion(self):
        june = self.monthly.loc[
            self.monthly["target_period"].eq("2026-06")
        ].iloc[0]
        self.assertAlmostEqual(
            june["TP_ALTINPIYASA_KAP02"] / 1000,
            june["DERIVED_BIST_GOLD_TL_GR"],
            places=10,
        )
        catalog = pd.read_parquet(MARKET_DATA / "analysis_series_catalog.parquet")
        derived = catalog.loc[
            catalog["series_code"].eq("DERIVED.BIST.GOLD.TL.GR")
        ].iloc[0]
        self.assertTrue(derived["is_derived"])
        self.assertEqual("TP.ALTINPIYASA.KAP02", derived["source_series_code"])
        self.assertAlmostEqual(0.001, derived["derivation_factor"], places=12)

    def test_active_bist_gold_source_reaches_target_end(self):
        gold = self.observations.loc[
            self.observations["series_code"].eq("TP.ALTINPIYASA.KAP02")
            & self.observations["value"].notna()
        ]
        self.assertEqual("2026-06-30", gold["period"].max())
        june_2026 = self.monthly.loc[
            self.monthly["target_period"].eq("2026-06"),
            "TP_ALTINPIYASA_KAP02",
        ].iloc[0]
        self.assertFalse(pd.isna(june_2026))

    def test_sparse_legacy_bist_gold_source_is_not_filled(self):
        gold = self.observations.loc[
            self.observations["series_code"].eq("TP.ALTINPIYASA.KAP05")
            & self.observations["value"].notna()
        ]
        self.assertEqual("2025-11-24", gold["period"].max())
        june_2026 = self.monthly.loc[
            self.monthly["target_period"].eq("2026-06"),
            "TP_ALTINPIYASA_KAP05",
        ].iloc[0]
        self.assertTrue(pd.isna(june_2026))


if __name__ == "__main__":
    unittest.main()
