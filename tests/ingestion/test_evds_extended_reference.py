import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "evds" / "extended_reference_v1"


class EvdsExtendedReferenceTests(unittest.TestCase):
    """Extended macro/market reference series (trade balance, TCMB reserves,
    M1/M2/M3 money supply, agricultural PPI and bounced-check statistics)
    must be physically present, not metadata-only."""

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
        self.assertEqual(9, self.validation["series_count"])
        self.assertEqual(1224, self.validation["observation_count"])
        self.assertEqual(0, self.validation["missing_observation_count"])
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_trade_balance_reaches_target_end_without_imputation(self):
        trade = self.observations.loc[
            self.observations["series_code"].eq("TP.ODEAYRSUNUM6.Q4")
        ]
        self.assertEqual(78, len(trade))
        self.assertEqual(0, int(trade["value"].isna().sum()))
        self.assertEqual("2020-01", trade["period"].min())
        self.assertEqual("2026-06", trade["period"].max())
        # Real values fetched live from the TCMB EVDS public endpoint
        # (https://evds3.tcmb.gov.tr/igmevdsms-dis/fe). Turkey's balance-of-
        # payments-basis trade balance is consistently negative (a deficit).
        recent = trade.sort_values("period").tail(3)["value"].round(1).tolist()
        self.assertEqual([-6900.0, -4452.0, -8566.0], recent)
        self.assertTrue((trade["value"] < 0).all())

    def test_reserve_series_have_no_null_observations(self):
        gross = self.observations.loc[self.observations["series_code"].eq("TP.AB.N07")]
        self.assertEqual(339, len(gross))
        self.assertEqual(0, int(gross["value"].isna().sum()))
        self.assertEqual("HAFTALIK(CUMA)", gross["native_frequency"].iloc[0])

        net = self.observations.loc[self.observations["series_code"].eq("TP.AB.N06")]
        self.assertEqual(339, len(net))
        self.assertEqual(0, int(net["value"].isna().sum()))
        # Turkey's net international reserves (ex-swap effects) went negative
        # during 2022-2023; the real downloaded series preserves that sign.
        self.assertTrue((net["value"] < 0).any())

    def test_money_supply_series_are_monotonically_growing_stocks(self):
        for code in ("TP.PBD.H01", "TP.PBD.H09", "TP.PBD.H17"):
            series = self.observations.loc[
                self.observations["series_code"].eq(code)
            ].sort_values("period")
            self.assertEqual(78, len(series))
            self.assertEqual(0, int(series["value"].isna().sum()))
            # Nominal TRY money supply grows over a 2020-2026 window that
            # includes very high inflation; first value must be far below last.
            self.assertLess(series["value"].iloc[0], series["value"].iloc[-1])

    def test_agri_ppi_and_bounced_check_series_have_no_null_observations(self):
        ppi = self.observations.loc[self.observations["series_code"].eq("TP.TARIMUFE01")]
        self.assertEqual(78, len(ppi))
        self.assertEqual(0, int(ppi["value"].isna().sum()))
        self.assertEqual("2020=100", ppi["unit"].iloc[0])

        count = self.observations.loc[self.observations["series_code"].eq("TP.BTO3")]
        self.assertEqual(78, len(count))
        self.assertEqual(0, int(count["value"].isna().sum()))

        amount = self.observations.loc[self.observations["series_code"].eq("TP.BTO4")]
        self.assertEqual(78, len(amount))
        self.assertEqual(0, int(amount["value"].isna().sum()))

    def test_monthly_panel_has_no_gaps_for_monthly_stock_series(self):
        self.assertEqual(78, self.alignment_validation["monthly_period_count"])
        for column in ("TP_PBD_H01", "TP_PBD_H09", "TP_PBD_H17", "TP_TARIMUFE01"):
            self.assertEqual(0, int(self.monthly[column].isna().sum()), column)

    def test_analysis_catalog_declares_roles(self):
        roles = dict(zip(self.catalog["series_code"], self.catalog["role"]))
        self.assertEqual("trade_balance", roles["TP.ODEAYRSUNUM6.Q4"])
        self.assertEqual("gross_fx_reserves", roles["TP.AB.N07"])
        self.assertEqual("net_international_reserves", roles["TP.AB.N06"])
        self.assertEqual("money_supply_m1", roles["TP.PBD.H01"])
        self.assertEqual("money_supply_m2", roles["TP.PBD.H09"])
        self.assertEqual("money_supply_m3", roles["TP.PBD.H17"])
        self.assertEqual("agri_ppi", roles["TP.TARIMUFE01"])
        self.assertEqual("bounced_check_amount", roles["TP.BTO4"])
        self.assertEqual("bounced_check_count", roles["TP.BTO3"])


class EvdsExtendedReferenceLakehouseBindingTests(unittest.TestCase):
    """Confirms these series (plus the already-downloaded gram-gold price
    series TP.MK.KUL.YTL, flipped to ready by a registry unit override only)
    are discoverable and bound in the built lakehouse, not METADATA_ONLY."""

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

    def test_new_series_are_ready(self):
        expected = [
            ("evds:TP.ODEAYRSUNUM6.Q4", 78, "USD", "flow"),
            ("evds:TP.AB.N07", 339, "TRY", "stock"),
            ("evds:TP.AB.N06", 339, "TRY", "stock"),
            ("evds:TP.PBD.H01", 78, "TRY", "stock"),
            ("evds:TP.PBD.H09", 78, "TRY", "stock"),
            ("evds:TP.PBD.H17", 78, "TRY", "stock"),
            ("evds:TP.TARIMUFE01", 78, "index", "index"),
            ("evds:TP.BTO3", 78, "count", "flow"),
            ("evds:TP.BTO4", 78, "TRY", "flow"),
        ]
        for metric_id, expected_count, unit, kind in expected:
            binding = self._binding(metric_id)
            self.assertTrue(binding["binding_available"], metric_id)
            self.assertEqual("ready", binding["status"], metric_id)
            self.assertEqual(unit, binding["unit"], metric_id)
            self.assertEqual(kind, binding["kind"], metric_id)
            self.assertEqual(expected_count, binding["observation_count"], metric_id)
            self.assertEqual(0, binding["missing_observation_count"], metric_id)
            self.assertEqual(
                "evds.extended_reference_observations", binding["table"], metric_id
            )

    def test_gram_gold_price_flips_to_ready_without_new_download(self):
        # TP.MK.KUL.YTL already had real observations under
        # housing_causality_controls_v1; only the registry.py unit override
        # (blank source unit -> TRY/gram) was needed here.
        binding = self._binding("evds:TP.MK.KUL.YTL")
        self.assertTrue(binding["binding_available"])
        self.assertEqual("ready", binding["status"])
        self.assertEqual("TRY/gram", binding["unit"])
        self.assertEqual("price", binding["kind"])
        self.assertEqual(
            "evds.housing_controls_observations", binding["table"]
        )

    def test_trade_balance_query_matches_verified_live_values(self):
        rows = self.connection.execute(
            """
            SELECT period, value FROM evds.extended_reference_observations
            WHERE series_code = 'TP.ODEAYRSUNUM6.Q4'
            ORDER BY period DESC LIMIT 3
            """
        ).fetchall()
        values = [round(value, 1) for _, value in rows]
        self.assertEqual([-8566.0, -4452.0, -6900.0], values)


if __name__ == "__main__":
    unittest.main()
