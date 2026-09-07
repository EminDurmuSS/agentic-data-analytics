import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data_pipeline" / "bddk" / "processed"


class BddkDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.housing = pd.read_parquet(PROCESSED / "housing_credit_monthly.parquet")
        cls.comparison = pd.read_parquet(
            PROCESSED / "housing_credit_evds_comparison.parquet"
        )

    def test_download_and_normalization_passed(self):
        self.assertEqual(self.validation["status"], "passed")
        self.assertEqual(self.validation["periods"], 66)
        self.assertEqual(self.validation["rows"], 2706)
        self.assertEqual(self.validation["rows_per_period"], 41)
        self.assertEqual(self.validation["duplicate_keys"], 0)

    def test_housing_series_is_complete_and_unique(self):
        self.assertEqual(len(self.housing), 66)
        self.assertFalse(self.housing["month"].duplicated().any())
        self.assertEqual(self.housing.iloc[0]["month"], "2021-01")
        self.assertEqual(self.housing.iloc[-1]["month"], "2026-06")

    def test_august_2025_source_difference_is_preserved(self):
        august = self.comparison.loc[self.comparison["month"].eq("2025-08")].iloc[0]
        self.assertEqual(august["bddk_housing_credit_stock_million_tl"], 608694)
        self.assertAlmostEqual(
            august["evds_housing_credit_stock_million_tl"], 662734.155, places=3
        )
        self.assertTrue(august["source_scope_review_required"])

    def test_only_one_month_exceeds_one_percent(self):
        flagged = self.comparison.loc[
            self.comparison["source_scope_review_required"], "month"
        ].tolist()
        self.assertEqual(flagged, ["2025-08"])


if __name__ == "__main__":
    unittest.main()
