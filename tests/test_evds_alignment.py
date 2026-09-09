import json
import unittest
from pathlib import Path

import pandas as pd

from data_pipeline.evds.build_aligned_panels import aggregate_value, align_series


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_pipeline" / "evds" / "housing_causality_v1"


class EvdsAlignmentRegressionTests(unittest.TestCase):
    @staticmethod
    def observations(periods, values, frequency="AYLIK"):
        return pd.DataFrame({
            "period": periods,
            "period_end": [str(pd.Period(period, freq="M").end_time.date()) if len(period) == 7 else period for period in periods],
            "value": values, "native_frequency": frequency,
            "series_code": "TEST.SERIES", "series_name_tr": "Test serisi", "role": "flow",
        })

    def test_sum_rejects_missing_row_and_explicit_null_month(self):
        for periods, values in [(["2025-01", "2025-03"], [10, 30]), (["2025-01", "2025-02", "2025-03"], [10, None, 30])]:
            with self.subTest(periods=periods):
                result = align_series(self.observations(periods, values), "quarterly", "sum")[0]
                self.assertIsNone(result["value"])
                self.assertEqual("unavailable_incomplete_sum", result["value_status"])
                self.assertEqual(3, result["expected_source_observation_count"])
                self.assertEqual(["2025-02"], json.loads(result["missing_source_periods"]))
                self.assertFalse(result["is_complete"])

    def test_direct_sum_never_treats_null_as_zero(self):
        self.assertIsNone(aggregate_value(pd.Series([10, None, 30]), "sum"))
        result = align_series(self.observations(["2025-01", "2025-02", "2025-03"], [10, 0, 30]), "quarterly", "sum")[0]
        self.assertEqual(40, result["value"])
        self.assertTrue(result["is_complete"])

    def test_last_records_actual_selected_date_and_staleness(self):
        result = align_series(self.observations(["2025-01", "2025-02", "2025-03"], [10, 20, None]), "quarterly", "last")[0]
        self.assertEqual(20, result["value"])
        self.assertEqual("2025-02", result["selected_source_period"])
        self.assertEqual("2025-02-28", result["selected_source_period_end"])
        self.assertEqual("2025-03-31", result["source_period_end_max"])
        self.assertEqual(31, result["staleness_days"])
        self.assertEqual("available_partial_last", result["value_status"])

    def test_partial_mean_is_explicit_and_has_no_single_source_date(self):
        result = align_series(self.observations(["2025-01", "2025-02", "2025-03"], [10, None, 30]), "quarterly", "mean")[0]
        self.assertEqual(20, result["value"])
        self.assertIsNone(result["selected_source_period"])
        self.assertEqual("2025-03-31", result["valid_source_period_end_max"])
        self.assertEqual("available_partial_mean", result["value_status"])

    def test_weekday_nulls_do_not_prove_valid_market_calendar(self):
        result = align_series(self.observations(["2025-03-27", "2025-03-28", "2025-03-31"], [10, None, 30], "İŞ GÜNÜ"), "monthly", "mean")[0]
        self.assertEqual("calendar_unverified", result["completeness_status"])
        self.assertIsNone(result["is_complete"])
        self.assertIsNone(result["expected_source_observation_count"])
        self.assertEqual(1, result["missing_observation_count"])

    def test_quarterly_value_keeps_quarterly_frequency_when_placed_in_march(self):
        result = align_series(self.observations(["2025-03-31"], [100], "ÜÇ AYLIK"), "monthly", "sum")
        self.assertEqual(1, len(result))
        self.assertEqual("2025-03", result[0]["target_period"])
        self.assertEqual("quarter_end_only", result[0]["representation"])
        self.assertEqual("quarterly", result[0]["value_frequency"])

    def test_duplicate_source_period_and_unsupported_upsampling_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "tekrarlaniyor"):
            align_series(self.observations(["2025-01", "2025-01"], [10, 20]), "quarterly", "sum")
        with self.assertRaisesRegex(ValueError, "politikasi yok"):
            align_series(self.observations(["2025-12-31"], [100], "YILLIK"), "monthly", "last")


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
