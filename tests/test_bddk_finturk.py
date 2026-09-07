import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = (
    ROOT
    / "data_pipeline"
    / "bddk"
    / "processed"
    / "finturk_all_groups_all_cities"
)


class BddkFinTurkDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.catalog = pd.read_parquet(PROCESSED / "source_table_catalog.parquet")
        cls.measurements = pd.read_parquet(PROCESSED / "measurements_long.parquet")
        cls.housing = pd.read_parquet(PROCESSED / "table_03.parquet")

    def test_complete_fin_turk_scope_is_present(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(22, self.validation["period_count"])
        self.assertEqual(7, self.validation["table_count"])
        self.assertEqual(7, self.validation["requested_group_count"])
        self.assertEqual(154, self.validation["source_file_count"])
        self.assertEqual(84484, self.validation["source_row_count"])
        self.assertEqual(154, len(self.catalog))

    def test_source_catalog_is_unique_and_portable(self):
        self.assertFalse(self.catalog.duplicated(["quarter", "table_no"]).any())
        self.assertTrue(self.catalog["source_file"].str.startswith("raw/").all())
        self.assertFalse(self.catalog["source_file"].str.startswith("/").any())

    def test_long_measurement_key_is_unique_and_missing_values_are_explicit(self):
        key = ["quarter", "table_no", "group_code", "city", "measure_code"]
        self.assertFalse(self.measurements.duplicated(key).any())
        self.assertEqual(
            self.validation["missing_measurement_count"],
            int(self.measurements["is_missing"].sum()),
        )
        self.assertGreater(self.validation["missing_measurement_count"], 0)

    def test_housing_credit_is_quarterly_stock_not_disbursement(self):
        self.assertEqual(22, self.housing["quarter"].nunique())
        self.assertEqual("quarterly", self.housing["native_frequency"].unique().item())
        self.assertIn("KonutKredisi", self.housing.columns)
        dictionary = pd.read_parquet(PROCESSED / "column_dictionary.parquet")
        housing = dictionary.loc[
            (dictionary["table_no"] == 3)
            & (dictionary["measure_code"] == "KonutKredisi")
        ].iloc[0]
        self.assertEqual("period_end_stock", housing["value_semantics"])
        self.assertEqual("thousand_try", housing["unit"])


if __name__ == "__main__":
    unittest.main()
