import json
import unittest
from pathlib import Path

import pandas as pd

from data_pipeline.evds.build_regional_housing_manifest import (
    EXPECTED_ROLE_COUNTS,
    build_manifest,
    selected_series_in_other_manifests,
)
from data_pipeline.regional.build_housing_panel import add_housing_price_proxy


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
        self.assertEqual(519, len(codes))
        self.assertEqual(519, len(set(codes)))
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
        self.assertEqual(519, self.validation["series_count"])
        self.assertEqual(519, self.observations["series_code"].nunique())
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_source_nulls_are_preserved_and_classified(self):
        null_rows = self.observations.loc[self.observations["is_missing"]]
        self.assertTrue(null_rows["value"].isna().all())
        self.assertEqual(482, len(null_rows))
        self.assertEqual(322, int(null_rows["is_unresolved_missing"].sum()))
        self.assertEqual(
            {
                "TP.BIRIMFIYAT.ARDAHAN",
                "TP.BIRIMFIYAT.BAYBURT",
                "TP.BIRIMFIYAT.GUMUSHANE",
                "TP.BIRIMFIYAT.HAKKARI",
                "TP.BIRIMFIYAT.TUNCELI",
                "TP.BK.ARDAHAN",
                "TP.BK.BAYBURT",
                "TP.BK.BINGOL",
                "TP.BK.GUMUSHANE",
                "TP.BK.HAKKARI",
                "TP.BK.SIRNAK",
                "TP.BK.TUNCELI",
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
        cls.price_proxy_audit = pd.read_parquet(
            REGIONAL_PANEL / "housing_unit_price_proxy_audit.parquet"
        )

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
        self.assertEqual(81, self.dimension["housing_unit_rent_series_code"].nunique())

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
        self.assertEqual(
            8,
            self.validation[
                "incomplete_province_quarter_sales_aggregations_before_fallback"
            ],
        )
        self.assertEqual(
            0,
            self.validation[
                "incomplete_province_quarter_sales_aggregations_after_fallback"
            ],
        )
        self.assertEqual(
            ["ARDAHAN", "BAYBURT", "GÜMÜŞHANE", "HAKKARİ", "TUNCELİ"],
            self.validation["housing_unit_price_no_observation_provinces"],
        )
        self.assertEqual(
            ["AĞRI", "BİTLİS", "IĞDIR", "KARS", "MUŞ", "VAN", "ŞIRNAK"],
            self.validation["housing_unit_price_partial_coverage_provinces"],
        )
        self.assertEqual(
            [
                "ARDAHAN",
                "BAYBURT",
                "BİNGÖL",
                "GÜMÜŞHANE",
                "HAKKARİ",
                "TUNCELİ",
                "ŞIRNAK",
            ],
            self.validation["housing_unit_rent_no_observation_provinces"],
        )
        self.assertTrue(
            rows["housing_unit_rent_try_per_m2"].isna().all()
        )

    def test_price_proxy_preserves_official_nulls_and_is_explicit(self):
        source = self.panel["housing_unit_price_try_per_m2"]
        with_proxy = self.panel["housing_unit_price_with_proxy_try_per_m2"]
        observed = source.notna()
        imputed = self.panel["housing_unit_price_proxy_origin"].eq(
            "same_region_same_quarter_official_median_proxy"
        )

        self.assertEqual(1620, int(observed.sum()))
        self.assertEqual(162, int(imputed.sum()))
        self.assertEqual(
            0,
            int(
                self.panel["housing_unit_price_proxy_origin"]
                .eq("unavailable")
                .sum()
            ),
        )
        self.assertTrue(source.loc[imputed].isna().all())
        self.assertTrue(with_proxy.loc[observed].eq(source.loc[observed]).all())
        self.assertTrue(with_proxy.loc[imputed].notna().all())
        self.assertTrue(
            self.panel.loc[imputed, "housing_unit_price_proxy_peer_count"].gt(0).all()
        )
        self.assertTrue(
            self.panel.loc[observed, "housing_unit_price_proxy_peer_count"].isna().all()
        )
        self.assertEqual(162, len(self.price_proxy_audit))
        self.assertTrue(self.price_proxy_audit["official_source_value_preserved"].all())

    def test_price_proxy_uses_only_same_region_quarter_official_peers(self):
        imputed = self.panel.loc[
            self.panel["housing_unit_price_proxy_origin"].eq(
                "same_region_same_quarter_official_median_proxy"
            )
        ]
        for row in imputed.itertuples(index=False):
            peers = self.panel.loc[
                self.panel["housing_price_region_code"].eq(
                    row.housing_price_region_code
                )
                & self.panel["quarter"].eq(row.quarter),
                "housing_unit_price_try_per_m2",
            ].dropna()
            self.assertEqual(len(peers), row.housing_unit_price_proxy_peer_count)
            self.assertEqual(
                peers.median(),
                row.housing_unit_price_regional_median_proxy_try_per_m2,
            )
            self.assertEqual(
                peers.median(), row.housing_unit_price_with_proxy_try_per_m2
            )

    def test_price_proxy_has_no_cross_region_or_prior_period_fallback(self):
        sample = pd.DataFrame(
            {
                "housing_price_region_code": ["A", "A", "B", "B"],
                "quarter": ["2026Q1", "2026Q2", "2026Q1", "2026Q2"],
                "housing_unit_price_try_per_m2": [None, None, 10.0, 20.0],
            }
        )
        result = add_housing_price_proxy(sample)
        region_a = result["housing_price_region_code"].eq("A")
        self.assertTrue(
            result.loc[
                region_a, "housing_unit_price_with_proxy_try_per_m2"
            ].isna().all()
        )
        self.assertTrue(
            result.loc[region_a, "housing_unit_price_proxy_origin"]
            .eq("unavailable")
            .all()
        )

    def test_source_and_proxy_readiness_are_not_confused(self):
        self.assertTrue(
            self.panel["analysis_ready"].eq(
                self.panel["analysis_ready_source"]
            ).all()
        )
        self.assertEqual(1620, int(self.panel["analysis_ready_source"].sum()))
        self.assertEqual(
            1782, int(self.panel["analysis_ready_with_price_proxy"].sum())
        )
        self.assertEqual(1620, self.validation["analysis_ready_source_rows"])
        self.assertEqual(
            1782, self.validation["analysis_ready_with_price_proxy_rows"]
        )

    def test_tuik_fallback_is_explicit_and_only_used_for_proven_zeros(self):
        fallback = self.panel.loc[self.panel["mortgaged_sales_fallback_used"]]
        self.assertEqual(8, len(fallback))
        self.assertEqual(9, int(fallback["mortgaged_sales_fallback_month_count"].sum()))
        self.assertTrue(fallback["housing_sales_mortgaged_count"].notna().all())
        self.assertTrue((~fallback["sales_evds_source_complete"]).all())
        self.assertTrue(fallback["sales_source_complete"].all())
        self.assertTrue(
            fallback["mortgaged_sales_source"]
            .eq("TCMB_EVDS+TUIK_DATA_PORTAL_IDENTITY_FALLBACK")
            .all()
        )
        self.assertTrue(
            fallback["mortgaged_sales_tuik_source_sha256"].str.fullmatch(
                r"[0-9a-f]{64}"
            ).all()
        )


if __name__ == "__main__":
    unittest.main()
