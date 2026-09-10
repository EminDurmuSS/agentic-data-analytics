import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "quality" / "processed"


class CrossSourceReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (DATA / "validation.json").read_text(encoding="utf-8")
        )
        cls.frame = pd.read_parquet(
            DATA / "housing_credit_stock_reconciliation.parquet"
        )

    def test_complete_quarterly_comparison(self):
        self.assertEqual("passed_with_expected_scope_differences", self.validation["status"])
        self.assertEqual(22, len(self.frame))
        self.assertEqual("2021-03", self.frame.iloc[0]["quarter"])
        self.assertEqual("2026-06", self.frame.iloc[-1]["quarter"])
        self.assertFalse(self.frame["quarter"].duplicated().any())

    def test_finturk_domestic_sum_reconciles_to_bddk(self):
        self.assertLessEqual(
            self.frame["finturk_domestic_minus_bddk_pct"].abs().max(), 0.05
        )
        self.assertTrue((self.frame["finturk_abroad_million_try"] > 0).all())
        self.assertTrue(
            (
                self.frame["finturk_all_geographies_million_try"]
                > self.frame["finturk_domestic_provinces_million_try"]
            ).all()
        )

    def test_tbb_scope_is_not_forced_to_equal_bddk(self):
        comparable = self.frame.dropna(subset=["balance_amount_million_try"])
        self.assertEqual(21, len(comparable))
        self.assertTrue((comparable["tbb_minus_bddk_pct"] < 0).all())
        self.assertTrue(self.frame.iloc[-1]["balance_amount_million_try"] != self.frame.iloc[-1]["balance_amount_million_try"])

    def test_known_monthly_evds_difference_remains_flagged(self):
        self.assertEqual(
            ["2025-08"], self.validation["monthly_evds_bddk_scope_review_periods"]
        )


if __name__ == "__main__":
    unittest.main()
