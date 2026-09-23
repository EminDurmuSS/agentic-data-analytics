import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "evds" / "precious_metals_market_v1"


class EvdsPreciousMetalsMarketTests(unittest.TestCase):
    """BIST Altin Piyasasi (Kiymetli Madenler ve Kiymetli Taslar Piyasasi)
    is gunu gold trading volume (TRY) and trading quantity (kg) must be
    physically present, not metadata-only, and correctly summed to monthly
    totals despite daily calendar nulls (weekends/holidays)."""

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
        self.assertEqual(2, self.validation["series_count"])
        self.assertEqual(3390, self.validation["observation_count"])
        self.assertEqual(0, self.validation["unresolved_missing_observation_count"])
        self.assertFalse(
            self.observations.duplicated(["series_code", "period"]).any()
        )

    def test_daily_series_have_only_structural_nulls(self):
        for code in ("TP.ALTINPIYASA.HACM02", "TP.ALTINPIYASA.MIKT02"):
            series = self.observations.loc[self.observations["series_code"].eq(code)]
            self.assertEqual(1695, len(series))
            self.assertEqual("İŞ GÜNÜ", series["native_frequency"].iloc[0])
            missing = series.loc[series["value"].isna()]
            # Every null observation is a documented calendar non-observation
            # or a period before the series formally starts; none is an
            # unresolved (unexplained) trading-day gap.
            self.assertTrue(missing["is_structural_na"].all())
            self.assertFalse(missing["is_unresolved_missing"].any())

    def test_monthly_panel_sums_are_fully_populated_2023_2025(self):
        # This is the exact window Benchmark Senaryo 4 prompt 1 asks for.
        window = self.monthly.loc[
            (self.monthly["target_period"] >= "2023-01")
            & (self.monthly["target_period"] <= "2025-12")
        ]
        self.assertEqual(36, len(window))
        self.assertEqual(0, int(window["TP_ALTINPIYASA_HACM02"].isna().sum()))
        self.assertEqual(0, int(window["TP_ALTINPIYASA_MIKT02"].isna().sum()))
        # Real observed monthly totals (TRY volume, kg quantity) downloaded
        # live from the public TCMB EVDS endpoint
        # (https://evds3.tcmb.gov.tr/igmevdsms-dis/fe); trading volume in TRY
        # is always strictly positive and quantity in kg is always strictly
        # positive.
        self.assertTrue((window["TP_ALTINPIYASA_HACM02"] > 0).all())
        self.assertTrue((window["TP_ALTINPIYASA_MIKT02"] > 0).all())

    def test_alignment_reports_no_incomplete_sums(self):
        self.assertEqual(78, self.alignment_validation["monthly_period_count"])
        status_counts = self.alignment_validation["value_status_counts"]["monthly"]
        self.assertEqual({"available": 156}, status_counts)

    def test_analysis_catalog_declares_roles(self):
        roles = dict(zip(self.catalog["series_code"], self.catalog["role"]))
        self.assertEqual("gold_trading_volume_try", roles["TP.ALTINPIYASA.HACM02"])
        self.assertEqual("gold_trading_quantity_kg", roles["TP.ALTINPIYASA.MIKT02"])


class EvdsPreciousMetalsMarketLakehouseBindingTests(unittest.TestCase):
    """Confirms the trading-volume and trading-quantity series are
    discoverable and bound in the built lakehouse, not METADATA_ONLY."""

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
            ("evds:TP.ALTINPIYASA.HACM02", "TRY", "flow"),
            ("evds:TP.ALTINPIYASA.MIKT02", "kg", "flow"),
        ]
        for metric_id, unit, kind in expected:
            binding = self._binding(metric_id)
            self.assertTrue(binding["binding_available"], metric_id)
            self.assertEqual("ready", binding["status"], metric_id)
            self.assertEqual(unit, binding["unit"], metric_id)
            self.assertEqual(kind, binding["kind"], metric_id)
            self.assertEqual(1695, binding["observation_count"], metric_id)
            self.assertEqual(
                "evds.precious_metals_market_observations", binding["table"], metric_id
            )

    def test_monthly_query_matches_precomputed_panel(self):
        rows = self.connection.execute(
            """
            SELECT strftime(CAST(period AS DATE), '%Y-%m') AS ym, SUM(value)
            FROM evds.precious_metals_market_observations
            WHERE series_code = 'TP.ALTINPIYASA.HACM02'
              AND ym BETWEEN '2024-01' AND '2024-01'
            GROUP BY ym
            """
        ).fetchall()
        self.assertEqual(1, len(rows))
        _, total = rows[0]
        monthly = pd.read_parquet(DATA / "monthly_panel.parquet")
        panel_row = monthly.loc[monthly["target_period"] == "2024-01"]
        self.assertEqual(1, len(panel_row))
        self.assertAlmostEqual(
            float(total), float(panel_row["TP_ALTINPIYASA_HACM02"].iloc[0]), places=2
        )


if __name__ == "__main__":
    unittest.main()
