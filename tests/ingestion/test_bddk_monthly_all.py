import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_all_groups"


class BddkMonthlyAllDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.catalog = pd.read_parquet(PROCESSED / "source_table_catalog.parquet")

    def test_all_17_tables_cover_all_66_months(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(1122, self.validation["source_file_count"])
        self.assertEqual(11220, self.validation["source_table_group_count"])
        self.assertEqual(17, self.validation["table_count"])
        self.assertEqual(66, self.validation["period_count"])
        self.assertEqual(10, self.validation["group_count"])
        self.assertEqual(339650, self.validation["total_rows"])
        self.assertEqual(1122, self.validation["group_row_identity_checks"])
        self.assertEqual(11220, len(self.catalog))
        self.assertEqual(set(range(10001, 10011)), set(self.catalog["group_code"]))

    def test_source_keys_are_unique_and_portable(self):
        self.assertFalse(
            self.catalog.duplicated(["month", "table_no", "group_code"]).any()
        )
        self.assertTrue(self.catalog["source_file"].str.startswith("raw/").all())
        self.assertFalse(self.catalog["source_file"].str.startswith("/").any())

    def test_captionless_tables_use_validated_request_metadata(self):
        captionless = self.catalog.loc[self.catalog["table_no"].isin([15, 16, 17])]
        self.assertTrue(captionless["caption_period"].isna().all())
        self.assertEqual(
            {"validated_request_info"},
            set(captionless["period_validation_source"].unique()),
        )


if __name__ == "__main__":
    unittest.main()
