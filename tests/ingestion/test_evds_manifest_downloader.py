import argparse
import unittest
from datetime import date
import json
from pathlib import Path
import tempfile
from unittest import mock

import pandas as pd

from tools.EVDS_Manifest_Indirme_Araci import (
    classify_series_missingness,
    expected_periods,
    missing_expected_periods,
    parse_period_label,
    parse_response,
    request_chunks,
    run,
    selection_window,
    sha256_bytes,
    validate_existing_config,
)


class EvdsManifestDownloaderTests(unittest.TestCase):
    def test_high_frequency_requests_are_split_by_calendar_year(self):
        chunks = request_chunks(date(2020, 6, 1), date(2022, 2, 1), "GÜNLÜK")
        self.assertEqual(
            [
                (date(2020, 6, 1), date(2020, 12, 31)),
                (date(2021, 1, 1), date(2021, 12, 31)),
                (date(2022, 1, 1), date(2022, 2, 1)),
            ],
            chunks,
        )

    def test_monthly_and_quarterly_periods_have_explicit_boundaries(self):
        self.assertEqual(
            ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),
            parse_period_label("2026-02", "AYLIK"),
        )
        self.assertEqual(
            ("2026-Q2", date(2026, 4, 1), date(2026, 6, 30)),
            parse_period_label("2026-2Ç", "ÜÇ AYLIK"),
        )

    def test_out_of_range_weekly_row_is_filtered_and_null_is_preserved(self):
        response = {
            "totalCount": 2,
            "items": [
                {"Tarih": "26-06-2026", "TP_TEST": None, "UNIXTIME": {"$numberLong": "1"}},
                {"Tarih": "03-07-2026", "TP_TEST": "7.5", "UNIXTIME": {"$numberLong": "2"}},
            ],
        }
        import json

        rows, metadata = parse_response(
            json.dumps(response).encode(),
            "TP.TEST",
            "HAFTALIK(CUMA)",
            date(2026, 1, 1),
            date(2026, 6, 30),
        )
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["is_missing"])
        self.assertEqual(1, metadata["outside_requested_range_rows"])

    def test_returned_nulls_and_omitted_months_are_classified_separately(self):
        rows = [
            {
                "period": "2026-04",
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "is_missing": False,
            },
            {
                "period": "2026-05",
                "period_start": "2026-05-01",
                "period_end": "2026-05-31",
                "is_missing": True,
            },
        ]
        classified, counts = classify_series_missingness(rows, "AYLIK")
        self.assertEqual("source_not_published", classified[1]["missing_kind"])
        self.assertEqual({"observed": 1, "source_not_published": 1}, counts)
        gaps = missing_expected_periods(
            classified, "AYLIK", date(2026, 4, 1), date(2026, 6, 30)
        )
        self.assertEqual("2026-06", gaps[0]["period"])
        self.assertEqual("source_not_published", gaps[0]["missing_kind"])

    def test_expected_months_are_calendar_complete(self):
        periods = expected_periods(date(2026, 4, 10), date(2026, 6, 2), "AYLIK")
        self.assertEqual(["2026-04", "2026-05", "2026-06"], [row[0] for row in periods])

    def test_series_specific_window_can_extend_only_one_selected_series(self):
        default_start, default_end = date(2020, 1, 1), date(2026, 6, 30)
        self.assertEqual(
            (date(2010, 1, 1), default_end),
            selection_window(
                {"series_code": "TP.MK.F.BILESIK", "start_date": "2010-01-01"},
                default_start,
                default_end,
            ),
        )
        self.assertEqual(
            (default_start, default_end),
            selection_window(
                {"series_code": "TP.ALTINPIYASA.KAP02"},
                default_start,
                default_end,
            ),
        )

    def test_existing_output_update_rejects_dataset_or_series_identity_change(self):
        base = {
            "endpoint": "https://evds3.tcmb.gov.tr/igmevdsms-dis/fe",
            "dataset_id": "market",
            "catalog_sha256": "a" * 64,
            "series_count": 1,
            "series_codes": ["TP.MK.F.BILESIK"],
        }
        validate_existing_config(base, {**base, "manifest_sha256": "new"}, update_existing=True)
        for changed in (
            {**base, "dataset_id": "other"},
            {**base, "series_codes": ["TP.KTF12"]},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    validate_existing_config(base, changed, update_existing=True)
        with self.assertRaisesRegex(ValueError, "--update-existing"):
            validate_existing_config(base, {**base, "manifest_sha256": "new"}, update_existing=False)

    def test_validated_cache_preserves_original_acquisition_record(self):
        response = json.dumps(
            {
                "totalCount": 1,
                "items": [
                    {
                        "Tarih": "2026-01",
                        "TP_TEST": "7.5",
                        "UNIXTIME": {"$numberLong": "1"},
                    }
                ],
            }
        ).encode()
        http_info = {
            "http_status": 200,
            "final_url": "https://evds3.tcmb.gov.tr/igmevdsms-dis/fe",
            "content_type": "application/json",
            "url": "https://evds3.tcmb.gov.tr/igmevdsms-dis/fe",
            "started_at_utc": "2026-09-19T10:00:00+00:00",
            "completed_at_utc": "2026-09-19T10:00:01+00:00",
            "bytes": len(response),
            "sha256": sha256_bytes(response),
            "transport": "fixture",
            "tls_verification": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            catalog_path = root / "catalog.parquet"
            output = root / "output"
            manifest_path.write_text(
                json.dumps(
                    {
                        "dataset_id": "cache-provenance-test",
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                        "series": [{"series_code": "TP.TEST", "role": "test"}],
                    }
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "series_code": "TP.TEST",
                        "frequency": "AYLIK",
                        "default_aggregation": "avg",
                        "is_archive": False,
                        "series_name_tr": "Test serisi",
                        "source": "TCMB EVDS",
                    }
                ]
            ).to_parquet(catalog_path, index=False)
            args = argparse.Namespace(
                manifest=manifest_path,
                catalog=catalog_path,
                output=output,
                timeout=1,
                retries=0,
                delay=0,
                update_existing=False,
                transport="urllib",
            )

            with mock.patch(
                "tools.EVDS_Manifest_Indirme_Araci.post_json",
                return_value=(response, http_info),
            ) as post:
                self.assertEqual(0, run(args))
                info_path = output / "raw" / "TP_TEST_2026-01-01_2026-01-31_info.json"
                first_info = info_path.read_bytes()
                first_manifest = (output / "manifest.json").read_bytes()

                self.assertEqual(0, run(args))
                self.assertEqual(1, post.call_count)
                self.assertEqual(first_info, info_path.read_bytes())
                self.assertEqual(first_manifest, (output / "manifest.json").read_bytes())

            record = json.loads(first_info)
            self.assertFalse(record["served_from_cache"])
            self.assertEqual("2026-09-19T10:00:00+00:00", record["started_at_utc"])
            self.assertEqual("2026-09-19T10:00:01+00:00", record["completed_at_utc"])


class EvdsHousingSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / "data_pipeline" / "evds" / "housing_causality_v1"
        cls.validation = json.loads((root / "validation.json").read_text(encoding="utf-8"))
        cls.observations = pd.read_parquet(root / "observations_long.parquet")
        cls.catalog = pd.read_parquet(root / "series_catalog.parquet")

    def test_manifest_snapshot_has_all_selected_series(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(47, self.validation["series_count"])
        self.assertEqual(47, self.observations["series_code"].nunique())
        self.assertEqual(47, len(self.catalog))
        self.assertEqual([], self.validation["series_with_no_non_null_observations"])

    def test_observation_keys_are_unique_and_nulls_are_not_zero_filled(self):
        self.assertFalse(self.observations.duplicated(["series_code", "period"]).any())
        self.assertEqual(
            self.validation["missing_observation_count"],
            int(self.observations["is_missing"].sum()),
        )
        null_rows = self.observations.loc[self.observations["is_missing"]]
        self.assertTrue(null_rows["value"].isna().all())
        self.assertFalse(null_rows["missing_kind"].eq("observed").any())
        self.assertEqual(
            self.validation["unresolved_missing_observation_count"],
            int(null_rows["is_unresolved_missing"].sum()),
        )


if __name__ == "__main__":
    unittest.main()
