import json
import unittest
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
LAKEHOUSE = ROOT / "data_pipeline" / "lakehouse"


class LakehouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (LAKEHOUSE / "validation.json").read_text(encoding="utf-8")
        )
        cls.connection = duckdb.connect(
            str(LAKEHOUSE / "analytics.duckdb"), read_only=True
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_database_is_self_contained_and_has_expected_schemas(self):
        self.assertEqual("passed", self.validation["status"])
        schemas = {
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT table_schema FROM information_schema.tables"
            ).fetchall()
        }
        self.assertTrue(
            {"catalog", "evds", "bddk", "tbb", "quality", "evidence", "analysis"}
            <= schemas
        )

    def test_monthly_analysis_is_complete_and_unique(self):
        count, distinct_count = self.connection.execute(
            "SELECT count(*), count(DISTINCT month) "
            "FROM analysis.housing_credit_monthly"
        ).fetchone()
        self.assertEqual((66, 66), (count, distinct_count))
        missing_core = self.connection.execute(
            "SELECT count(*) FROM analysis.housing_credit_monthly "
            "WHERE bddk_housing_credit_stock_million_tl IS NULL "
            "OR TP_KTF12 IS NULL OR TP_TUKFIY2025_GENEL IS NULL"
        ).fetchone()[0]
        self.assertEqual(0, missing_core)

    def test_quarterly_analysis_keeps_source_gap_explicit(self):
        count, distinct_count = self.connection.execute(
            "SELECT count(*), count(DISTINCT quarter) "
            "FROM analysis.housing_credit_quarterly"
        ).fetchone()
        self.assertEqual((22, 22), (count, distinct_count))
        missing = self.connection.execute(
            "SELECT quarter FROM analysis.housing_credit_quarterly "
            "WHERE disbursement_amount_million_try IS NULL"
        ).fetchall()
        self.assertEqual([("2026-06",)], missing)

    def test_metric_catalog_distinguishes_metadata_only_series(self):
        total, available = self.connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE observation_available) "
            "FROM catalog.metrics WHERE source_system = 'TCMB_EVDS'"
        ).fetchone()
        self.assertEqual(52696, total)
        self.assertEqual(60, available)

    def test_causality_and_market_controls_are_queryable(self):
        columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info('analysis.housing_credit_monthly')"
            ).fetchall()
        }
        self.assertTrue(
            {
                "TP_BISPOLFAIZ_TUR",
                "TP_BISPOLFAIZ_USA",
                "TP_BISPOLFAIZ_XM",
                "TP_IN_KL2_TOPLAM_TOP_D",
                "TP_MK_F_BILESIK",
            }
            <= columns
        )
        june_2026 = self.connection.execute(
            "SELECT TP_MK_KUL_YTL, TP_MK_F_BILESIK "
            "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
        ).fetchone()
        self.assertIsNone(june_2026[0])
        self.assertIsNotNone(june_2026[1])

    def test_completed_weekly_bddk_is_loaded(self):
        count = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_measurements"
        ).fetchone()[0]
        self.assertEqual(147154, count)

    def test_demo_query_returns_only_quality_screened_rows(self):
        rows = self.connection.execute(
            "SELECT month FROM analysis.housing_credit_monthly "
            "WHERE rate_down_real_stock_not_up_quality_screened "
            "ORDER BY month"
        ).fetchall()
        self.assertGreater(len(rows), 0)
        self.assertNotIn(("2025-08",), rows)


if __name__ == "__main__":
    unittest.main()
