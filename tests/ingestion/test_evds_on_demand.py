import unittest

import pandas as pd

from tools.EVDS_Talep_Uzerine_Indirme_Araci import (
    build_manifest,
    generated_dataset_id,
    search_catalog,
)


class EvdsOnDemandTests(unittest.TestCase):
    def setUp(self):
        self.catalog = pd.DataFrame(
            [
                {
                    "series_code": "TP.KFE.TR",
                    "series_name_tr": "Konut Fiyat Endeksi",
                    "group_name_tr": "Konut Fiyat Endeksi",
                    "frequency": "AYLIK",
                    "unit": "Endeks",
                    "default_aggregation": "last",
                    "source": "TCMB",
                    "is_archive": False,
                    "searchable_text": "TP.KFE.TR konut fiyat endeksi",
                },
                {
                    "series_code": "TP.OLD.KFE",
                    "series_name_tr": "Eski Konut Endeksi",
                    "group_name_tr": "Arsiv",
                    "frequency": "AYLIK",
                    "unit": "Endeks",
                    "default_aggregation": "last",
                    "source": "TCMB",
                    "is_archive": True,
                    "searchable_text": "eski konut endeksi",
                },
            ]
        )

    def test_search_prefers_active_exact_series(self):
        result = search_catalog(self.catalog, "TP.KFE.TR")
        self.assertEqual("TP.KFE.TR", result.iloc[0]["series_code"])
        self.assertFalse(bool(result.iloc[0]["is_archive"]))

    def test_manifest_uses_catalog_aggregation(self):
        manifest = build_manifest(
            self.catalog,
            ["TP.KFE.TR"],
            "2021-01-01",
            "2026-06-30",
            "evds.on_demand.test",
        )
        self.assertEqual("last", manifest["series"][0]["aggregation"])
        self.assertEqual("catalog_validated_on_demand", manifest["selection_policy"])

    def test_archive_requires_explicit_permission(self):
        with self.assertRaisesRegex(ValueError, "Arsiv EVDS serisi"):
            build_manifest(
                self.catalog,
                ["TP.OLD.KFE"],
                "2021-01-01",
                "2026-06-30",
                "evds.on_demand.test",
            )

    def test_generated_dataset_id_is_deterministic(self):
        first = generated_dataset_id(["TP.KFE.TR"], "2021-01-01", "2026-06-30")
        second = generated_dataset_id(["TP.KFE.TR"], "2021-01-01", "2026-06-30")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
