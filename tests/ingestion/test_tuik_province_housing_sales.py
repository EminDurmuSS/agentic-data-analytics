import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data_pipeline.tuik import build_province_housing_sales_vintages as vintages


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data_pipeline" / "tuik" / "province_housing_sales_v1"
PROCESSED = BASE / "processed"
FIRST_PUBLISHED_BASE = (
    ROOT
    / "data_pipeline"
    / "tuik"
    / "province_housing_sales_first_published_v1"
)
FIRST_PUBLISHED_PROCESSED = FIRST_PUBLISHED_BASE / "processed"


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


class TuikProvinceHousingSalesVintageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (FIRST_PUBLISHED_BASE / "manifest.json").read_text(encoding="utf-8")
        )
        cls.validation = json.loads(
            (FIRST_PUBLISHED_PROCESSED / "validation.json").read_text(
                encoding="utf-8"
            )
        )
        cls.first_published = pd.read_parquet(
            FIRST_PUBLISHED_PROCESSED / "monthly_sales_first_published.parquet"
        )
        cls.comparison = pd.read_parquet(
            FIRST_PUBLISHED_PROCESSED / "revision_comparison.parquet"
        )
        cls.current = pd.read_parquet(PROCESSED / "monthly_sales_long.parquet")

    def test_first_publications_are_complete_and_cell_traceable(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(24, self.validation["publication_count"])
        self.assertEqual(81, self.validation["province_count"])
        self.assertEqual(5, self.validation["metric_count"])
        self.assertEqual(9_720, self.validation["observation_count"])
        self.assertEqual(0, self.validation["duplicate_observation_keys"])
        self.assertEqual(0, self.validation["current_revised_values_missing"])
        self.assertEqual(24, len(self.manifest["records"]))
        self.assertFalse(
            self.first_published.duplicated(
                ["province_key", "month", "metric_code"]
            ).any()
        )
        required = [
            "source_press_id",
            "release_at",
            "source_press_url",
            "source_press_api_url",
            "source_download_url",
            "source_file",
            "source_sha256",
            "source_sheet",
            "source_row_index",
            "source_column_index",
            "source_cell",
            "revision_status",
            "vintage_policy",
        ]
        self.assertFalse(self.first_published[required].isna().any().any())
        self.assertTrue(self.first_published["source_sha256"].str.len().eq(64).all())
        self.assertTrue(self.first_published["source_cell"].str.startswith("t2!").all())

    def test_istanbul_official_vintages_are_distinct_and_not_silently_merged(self):
        selector = (
            self.first_published["province_key"].eq("istanbul")
            & self.first_published["metric_code"].eq("housing_sales_total_count")
        )
        first = self.first_published.loc[selector]
        first_totals = {
            year: int(first.loc[first["month"].str.startswith(year), "value"].sum())
            for year in ("2023", "2024")
        }
        self.assertEqual({"2023": 198_739, "2024": 239_213}, first_totals)

        current = self.current.loc[
            self.current["province_key"].eq("istanbul")
            & self.current["metric_code"].eq("housing_sales_total_count")
            & self.current["month"].between("2023-01", "2024-12")
        ]
        current_totals = {
            year: int(current.loc[current["month"].str.startswith(year), "value"].sum())
            for year in ("2023", "2024")
        }
        self.assertEqual({"2023": 223_643, "2024": 266_471}, current_totals)
        self.assertNotEqual(first_totals, current_totals)
        self.assertTrue(
            first["vintage_policy"]
            .eq("first_official_publication_for_each_reference_month")
            .all()
        )
        self.assertTrue(
            current["vintage_policy"].eq("latest_official_bulk_snapshot").all()
        )
        self.assertGreater(int(self.comparison["value_changed"].sum()), 0)

    def test_source_hash_mismatch_and_missing_source_fail_closed(self):
        record = dict(self.manifest["records"][0])
        record["workbook_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            vintages.source_bytes(record)

        missing = dict(self.manifest["records"][0])
        missing["workbook_file"] = (
            "data_pipeline/tuik/province_housing_sales_first_published_v1/"
            "raw/does-not-exist/province_sales.xls"
        )
        with self.assertRaises(FileNotFoundError):
            vintages.source_bytes(missing)

    def test_builder_refuses_a_manifest_with_a_corrupted_source_hash(self):
        broken = json.loads(json.dumps(self.manifest))
        broken["records"][0]["workbook_sha256"] = "f" * 64
        with patch.object(vintages, "read_manifest", return_value=broken):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                vintages.build(FIRST_PUBLISHED_PROCESSED)


if __name__ == "__main__":
    unittest.main()
