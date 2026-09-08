import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data_pipeline" / "bddk" / "processed" / "weekly_all_groups"


class BddkWeeklyDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.catalog = pd.read_parquet(PROCESSED / "source_table_catalog.parquet")
        cls.measurements = pd.read_parquet(PROCESSED / "measurements_long.parquet")

    def test_all_nine_tables_cover_all_286_weeks(self) -> None:
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual("complete", self.validation["source_download_status"])
        self.assertEqual(18018, self.validation["source_page_count"])
        self.assertEqual(286, self.validation["period_count"])
        self.assertEqual(9, self.validation["table_count"])
        self.assertEqual(7, self.validation["group_count"])
        self.assertEqual(18018, len(self.catalog))
        self.assertTrue(
            all(
                item["source_pages"] == 2002
                for item in self.validation["tables"].values()
            )
        )

    def test_source_and_measurement_keys_are_unique(self) -> None:
        self.assertFalse(
            self.catalog.duplicated(
                ["observation_date", "table_id", "group_code"]
            ).any()
        )
        self.assertFalse(
            self.measurements.duplicated(
                [
                    "observation_date",
                    "table_id",
                    "group_code",
                    "metric_code",
                    "currency_dimension",
                ]
            ).any()
        )

    def test_source_references_are_relative_and_hash_backed(self) -> None:
        self.assertTrue(self.catalog["source_file"].str.startswith("raw/").all())
        self.assertFalse(self.catalog["source_file"].str.startswith("/").any())
        self.assertTrue(self.catalog["source_sha256"].str.len().eq(64).all())
        self.assertTrue(
            self.catalog["source_request_info_sha256"].str.len().eq(64).all()
        )

    def test_currency_totals_pass_without_imputation(self) -> None:
        check = self.validation["currency_total_check"]
        self.assertGreater(check["checked_rows"], 0)
        self.assertLessEqual(check["maximum_absolute_rounding_difference"], 1.01)
        self.assertEqual(
            self.validation["missing_measurement_count"],
            int(self.measurements["value"].isna().sum()),
        )

    def test_all_source_nulls_are_structural_tl_only_fx_cells(self) -> None:
        missingness = self.validation["missingness"]
        self.assertEqual(2230, missingness["raw_missing_measurements"])
        self.assertEqual(
            2230, missingness["structural_not_applicable_measurements"]
        )
        self.assertEqual(0, missingness["unresolved_missing_measurements"])
        missing = self.measurements.loc[self.measurements["is_missing"]]
        self.assertTrue(missing["is_structural_na"].all())
        self.assertTrue(missing["currency_dimension"].eq("FX").all())
        self.assertEqual(
            {"292:11", "292:12"}, set(missing["metric_code"].unique())
        )


if __name__ == "__main__":
    unittest.main()
