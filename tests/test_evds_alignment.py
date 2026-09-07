import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_pipeline" / "evds" / "housing_causality_v1"


class EvdsAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (DATA / "alignment_validation.json").read_text(encoding="utf-8")
        )
        cls.raw = pd.read_parquet(DATA / "observations_long.parquet")
        cls.monthly = pd.read_parquet(DATA / "monthly_panel.parquet")
        cls.monthly_audit = pd.read_parquet(DATA / "monthly_alignment_audit.parquet")
        cls.quarterly = pd.read_parquet(DATA / "quarterly_panel.parquet")

    def test_panel_shapes_and_no_quarterly_forward_fill(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(47, self.validation["series_count"])
        self.assertEqual(78, self.validation["monthly_period_count"])
        self.assertEqual(26, self.validation["quarterly_period_count"])
        self.assertEqual(0, self.validation["quarterly_series_filled_into_intermediate_months"])

    def test_weekly_housing_rate_monthly_value_is_source_mean(self):
        source = self.raw.loc[
            (self.raw["series_code"] == "TP.KTF12")
            & self.raw["period_start"].str.startswith("2021-01")
        ]
        expected = source["value"].mean()
        actual = self.monthly.loc[
            self.monthly["target_period"].eq("2021-01"), "TP_KTF12"
        ].iloc[0]
        self.assertAlmostEqual(expected, actual, places=10)

    def test_quarterly_housing_sales_are_sum_of_months(self):
        source = self.raw.loc[
            (self.raw["series_code"] == "TP.AKONUTSAT1.KTRTOPLAM")
            & self.raw["period"].isin(["2021-01", "2021-02", "2021-03"])
        ]
        expected = source["value"].sum()
        actual = self.quarterly.loc[
            self.quarterly["target_period"].eq("2021Q1"),
            "TP_AKONUTSAT1_KTRTOPLAM",
        ].iloc[0]
        self.assertAlmostEqual(expected, actual, places=10)

    def test_quarterly_survey_has_only_quarter_end_month_values(self):
        rows = self.monthly_audit.loc[
            self.monthly_audit["series_code"].eq("TP.BKEA.S056")
        ]
        self.assertEqual(26, len(rows))
        self.assertTrue(rows["target_period"].str.endswith(("03", "06", "09", "12")).all())


if __name__ == "__main__":
    unittest.main()
