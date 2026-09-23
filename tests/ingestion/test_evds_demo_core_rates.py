import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "evds" / "demo_core_rates_v1"


class EvdsDemoCoreRatesTests(unittest.TestCase):
    """Verified core interest-rate controls closing the Benchmark Senaryo 1
    prompt 2 gap: EVDS Tasit Kredisi (TL, Stok, %) plus two related rate
    series must be physically present, not metadata-only.
    """

    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (DATA / "validation.json").read_text(encoding="utf-8")
        )
        cls.alignment_validation = json.loads(
            (DATA / "alignment_validation.json").read_text(encoding="utf-8")
        )
        cls.observations = pd.read_parquet(DATA / "observations_long.parquet")
        cls.catalog = pd.read_parquet(DATA / "analysis_series_catalog.parquet")
        cls.monthly = pd.read_parquet(DATA / "monthly_panel.parquet")

    def test_snapshot_is_complete_and_unique(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(3, self.validation["series_count"])
        self.assertEqual(495, self.validation["observation_count"])
        self.assertEqual(0, self.validation["missing_observation_count"])
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_vehicle_loan_rate_reaches_target_end_without_imputation(self):
        vehicle = self.observations.loc[
            self.observations["series_code"].eq("TP.BKR.TRY.17")
        ]
        self.assertEqual(78, len(vehicle))
        self.assertEqual(0, int(vehicle["value"].isna().sum()))
        self.assertEqual("2020-01", vehicle["period"].min())
        self.assertEqual("2026-06", vehicle["period"].max())
        # These are real values fetched live from the TCMB EVDS public
        # endpoint (https://evds3.tcmb.gov.tr/igmevdsms-dis/fe), matching
        # the officially published TP.BKR.TRY.17 series for 2026-01..2026-06.
        recent = (
            vehicle.sort_values("period")
            .tail(6)["value"]
            .round(2)
            .tolist()
        )
        self.assertEqual([33.59, 34.27, 39.69, 39.73, 39.43, 40.12], recent)

    def test_commercial_and_deposit_rate_series_have_no_null_observations(self):
        commercial = self.observations.loc[
            self.observations["series_code"].eq("TP.BKR.TRY.1")
        ]
        self.assertEqual(78, len(commercial))
        self.assertEqual(0, int(commercial["value"].isna().sum()))

        deposit = self.observations.loc[
            self.observations["series_code"].eq("TP.TRY.MT02")
        ]
        self.assertEqual(339, len(deposit))
        self.assertEqual(0, int(deposit["value"].isna().sum()))
        self.assertEqual("HAFTALIK(CUMA)", deposit["native_frequency"].iloc[0])

    def test_monthly_panel_has_no_gaps_for_stock_rate_series(self):
        self.assertEqual(78, self.alignment_validation["monthly_period_count"])
        vehicle_column = "TP_BKR_TRY_17"
        commercial_column = "TP_BKR_TRY_1"
        self.assertEqual(
            0, int(self.monthly[vehicle_column].isna().sum())
        )
        self.assertEqual(
            0, int(self.monthly[commercial_column].isna().sum())
        )

    def test_analysis_catalog_declares_rate_roles(self):
        roles = dict(
            zip(self.catalog["series_code"], self.catalog["role"])
        )
        self.assertEqual("vehicle_loan_rate", roles["TP.BKR.TRY.17"])
        self.assertEqual("commercial_loan_rate", roles["TP.BKR.TRY.1"])
        self.assertEqual("short_term_deposit_rate", roles["TP.TRY.MT02"])


class EvdsDemoCoreRatesLakehouseBindingTests(unittest.TestCase):
    """Confirms these 3 series are discoverable and bound in the built
    lakehouse, not left as METADATA_ONLY."""

    DATABASE = ROOT / "data_pipeline" / "lakehouse" / "analytics.duckdb"

    @classmethod
    def setUpClass(cls):
        if not cls.DATABASE.exists():
            raise unittest.SkipTest("Lakehouse database has not been built.")
        import duckdb

        cls.connection = duckdb.connect(str(cls.DATABASE), read_only=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "connection"):
            cls.connection.close()

    def _binding(self, metric_id: str) -> dict:
        row = self.connection.execute(
            "SELECT binding_json FROM catalog.metric_bindings WHERE metric_id = ?",
            [metric_id],
        ).fetchone()
        self.assertIsNotNone(row, f"{metric_id} missing from catalog.metric_bindings")
        return json.loads(row[0])

    def test_vehicle_commercial_and_deposit_rates_are_ready(self):
        for metric_id, expected_count in [
            ("evds:TP.BKR.TRY.17", 78),
            ("evds:TP.BKR.TRY.1", 78),
            ("evds:TP.TRY.MT02", 339),
        ]:
            binding = self._binding(metric_id)
            self.assertTrue(binding["binding_available"], metric_id)
            self.assertEqual("ready", binding["status"], metric_id)
            self.assertEqual("percent", binding["unit"], metric_id)
            self.assertEqual("rate", binding["kind"], metric_id)
            self.assertEqual(expected_count, binding["observation_count"], metric_id)
            self.assertEqual(0, binding["missing_observation_count"], metric_id)
            self.assertEqual(
                "evds.demo_core_rates_observations", binding["table"], metric_id
            )

    def test_vehicle_loan_rate_query_matches_verified_live_values(self):
        rows = self.connection.execute(
            """
            SELECT period, value FROM evds.demo_core_rates_observations
            WHERE series_code = 'TP.BKR.TRY.17'
            ORDER BY period DESC LIMIT 6
            """
        ).fetchall()
        values = [round(value, 2) for _, value in rows]
        self.assertEqual([40.12, 39.43, 39.73, 39.69, 34.27, 33.59], values)


if __name__ == "__main__":
    unittest.main()
