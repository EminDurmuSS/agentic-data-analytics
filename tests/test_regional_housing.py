import json
import unittest
from pathlib import Path

import pandas as pd

from data_pipeline.evds.build_regional_housing_manifest import (
    EXPECTED_ROLE_COUNTS,
    build_manifest,
    selected_series_in_other_manifests,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet"
MANIFEST_PATH = ROOT / "data_pipeline" / "evds" / "manifests" / "regional_housing_v1.json"
REGIONAL_EVDS = ROOT / "data_pipeline" / "evds" / "regional_housing_v1"
HOUSEHOLD_FINANCE = ROOT / "data_pipeline" / "evds" / "household_finance_v1"
REGIONAL_PANEL = ROOT / "data_pipeline" / "regional" / "processed"


class RegionalHousingManifestTests(unittest.TestCase):
    def test_catalog_driven_manifest_has_expected_non_overlapping_scope(self):
        catalog = pd.read_parquet(CATALOG_PATH)
        excluded = selected_series_in_other_manifests(
            MANIFEST_PATH.parent, MANIFEST_PATH
        )
        manifest, validation = build_manifest(catalog, excluded)
        codes = [item["series_code"] for item in manifest["series"]]
        self.assertEqual("passed", validation["status"])
        self.assertEqual(441, len(codes))
        self.assertEqual(441, len(set(codes)))
        self.assertEqual(EXPECTED_ROLE_COUNTS, validation["role_counts"])
        self.assertFalse(set(codes) & excluded)


class RegionalHousingSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (REGIONAL_EVDS / "validation.json").read_text(encoding="utf-8")
        )
        cls.observations = pd.read_parquet(REGIONAL_EVDS / "observations_long.parquet")
        cls.catalog = pd.read_parquet(REGIONAL_EVDS / "analysis_series_catalog.parquet")

    def test_all_selected_series_are_stored_once(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(441, self.validation["series_count"])
        self.assertEqual(441, self.observations["series_code"].nunique())
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_source_nulls_are_preserved_and_classified(self):
        null_rows = self.observations.loc[self.observations["is_missing"]]
        self.assertTrue(null_rows["value"].isna().all())
        self.assertEqual(220, len(null_rows))
        self.assertEqual(140, int(null_rows["is_unresolved_missing"].sum()))
        self.assertEqual(
            {
                "TP.BIRIMFIYAT.ARDAHAN",
                "TP.BIRIMFIYAT.BAYBURT",
                "TP.BIRIMFIYAT.GUMUSHANE",
                "TP.BIRIMFIYAT.HAKKARI",
                "TP.BIRIMFIYAT.TUNCELI",
            },
            set(self.validation["series_with_no_non_null_observations"]),
        )

    def test_quarterly_series_are_not_forward_filled(self):
        monthly = pd.read_parquet(REGIONAL_EVDS / "monthly_panel.parquet")
        april = monthly.loc[monthly["target_period"].eq("2026-04")].iloc[0]
        self.assertTrue(pd.isna(april["TP_BIRIMFIYAT_IST"]))
        june = monthly.loc[monthly["target_period"].eq("2026-06")].iloc[0]
        self.assertFalse(pd.isna(june["TP_BIRIMFIYAT_IST"]))


class HouseholdFinanceSnapshotTests(unittest.TestCase):
    def test_kkm_and_household_deposits_are_complete_after_series_start(self):
        validation = json.loads(
            (HOUSEHOLD_FINANCE / "validation.json").read_text(encoding="utf-8")
        )
        observations = pd.read_parquet(HOUSEHOLD_FINANCE / "observations_long.parquet")
        monthly = pd.read_parquet(HOUSEHOLD_FINANCE / "monthly_panel.parquet")
        self.assertEqual("passed", validation["status"])
        self.assertEqual(4, validation["series_count"])
        self.assertEqual(0, validation["unresolved_missing_observation_count"])
        self.assertTrue(
            observations.loc[observations["is_missing"], "missing_kind"]
            .eq("before_series_start")
            .all()
        )
        june = monthly.loc[monthly["target_period"].eq("2026-06")].iloc[0]
        self.assertTrue(
            june[["TP_KKM_K1", "TP_KKM_K2", "TP_KKM_K4", "TP_KM_E041"]]
            .notna()
            .all()
        )


class ProvinceQuarterPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (REGIONAL_PANEL / "validation.json").read_text(encoding="utf-8")
        )
        cls.panel = pd.read_parquet(
            REGIONAL_PANEL / "province_quarter_housing_panel.parquet"
        )
        cls.dimension = pd.read_parquet(REGIONAL_PANEL / "province_dimension.parquet")
        cls.dictionary = pd.read_parquet(REGIONAL_PANEL / "metric_dictionary.parquet")

    def test_panel_has_one_row_per_province_and_quarter(self):
        self.assertEqual("passed_with_source_gaps", self.validation["status"])
        self.assertEqual(1782, len(self.panel))
        self.assertEqual(81, self.panel["province_key"].nunique())
        self.assertEqual(22, self.panel["quarter"].nunique())
        self.assertFalse(self.panel.duplicated(["province_key", "quarter"]).any())

    def test_sales_identities_and_names_are_semantically_safe(self):
        complete = self.panel.loc[
            self.panel[
                [
                    "housing_sales_total_count",
                    "housing_sales_first_hand_count",
                    "housing_sales_second_hand_count",
                    "housing_sales_mortgaged_count",
                ]
            ].notna().all(axis=1)
        ]
        self.assertTrue(
            complete["housing_sales_total_count"].eq(
                complete["housing_sales_first_hand_count"]
                + complete["housing_sales_second_hand_count"]
            ).all()
        )
        self.assertTrue(
            (
                complete["mortgaged_sales_share_pct"]
                + complete["non_mortgaged_sales_share_pct"]
            ).round(10).eq(100).all()
        )
        labels = " ".join(
            [
                *self.panel.columns,
                *self.dictionary["metric_code"].astype(str).tolist(),
                *self.dictionary["metric_name_tr"].astype(str).tolist(),
            ]
        ).casefold()
        self.assertNotIn("cash sales", labels)
        self.assertNotIn("nakit satış", labels)
        non_mortgaged = self.dictionary.loc[
            self.dictionary["metric_code"].eq("housing_sales_non_mortgaged_count")
        ].iloc[0]
        self.assertIn("nakit satış olarak etiketlenemez", non_mortgaged["caution"].casefold())

    def test_every_province_has_explicit_source_and_region_mapping(self):
        self.assertEqual(81, len(self.dimension))
        self.assertFalse(self.dimension.isna().any().any())
        self.assertEqual(19, self.dimension["regional_kfe_series_code"].nunique())
        self.assertEqual(19, self.dimension["regional_ykke_series_code"].nunique())

    def test_known_source_gaps_remain_null(self):
        unavailable = {
            "ARDAHAN",
            "BAYBURT",
            "GÜMÜŞHANE",
            "HAKKARİ",
            "TUNCELİ",
        }
        rows = self.panel.loc[self.panel["province_name"].isin(unavailable)]
        self.assertTrue(rows["housing_unit_price_try_per_m2"].isna().all())
        self.assertGreater(self.validation["incomplete_province_quarter_sales_aggregations"], 0)


if __name__ == "__main__":
    unittest.main()
