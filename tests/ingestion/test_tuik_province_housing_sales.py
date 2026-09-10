import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data_pipeline" / "tuik" / "province_housing_sales_v1"
PROCESSED = BASE / "processed"


class TuikProvinceHousingSalesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.monthly = pd.read_parquet(PROCESSED / "monthly_sales_long.parquet")
        cls.reconciliation = pd.read_parquet(
            PROCESSED / "evds_reconciliation.parquet"
        )
        cls.fallbacks = pd.read_parquet(
            PROCESSED / "identity_zero_fallbacks.parquet"
        )

    def test_source_snapshot_and_processed_grid_are_complete(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(81, self.validation["province_count"])
        self.assertEqual(78, self.validation["month_count"])
        self.assertEqual(5, self.validation["metric_count"])
        self.assertEqual(31_590, len(self.monthly))
        self.assertEqual(0, int(self.monthly["value"].isna().sum()))
        self.assertFalse(
            self.monthly.duplicated(
                ["province_key", "month", "metric_code"]
            ).any()
        )

    def test_only_official_identity_proven_zeros_fill_absent_rows(self):
        expected = {
            ("ADIYAMAN", "2023-03"),
            ("ARDAHAN", "2024-05"),
            ("BAYBURT", "2023-10"),
            ("HAKKARİ", "2020-05"),
            ("HAKKARİ", "2021-01"),
            ("HAKKARİ", "2021-05"),
            ("HAKKARİ", "2023-10"),
            ("HAKKARİ", "2024-04"),
            ("HAKKARİ", "2024-06"),
            ("ŞIRNAK", "2021-05"),
        }
        self.assertEqual(expected, set(zip(self.fallbacks["province_name"], self.fallbacks["month"])))
        self.assertTrue(self.fallbacks["direct_value"].isna().all())
        self.assertTrue(self.fallbacks["value"].eq(0).all())
        self.assertTrue(
            self.fallbacks["value_origin"]
            .eq("official_identity_total_equals_other_implies_zero_mortgaged")
            .all()
        )

    def test_common_evds_and_tuik_values_match_exactly(self):
        exact = self.reconciliation.loc[
            self.reconciliation["reconciliation_status"].eq("exact_match")
        ]
        mismatches = self.reconciliation.loc[
            self.reconciliation["reconciliation_status"].eq("value_mismatch")
        ]
        self.assertEqual(25_262, len(exact))
        self.assertTrue(mismatches.empty)
        self.assertTrue(exact["difference_direct"].eq(0).all())


if __name__ == "__main__":
    unittest.main()
