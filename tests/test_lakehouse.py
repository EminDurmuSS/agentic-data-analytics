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
            {
                "catalog",
                "evds",
                "bddk",
                "tbb",
                "quality",
                "evidence",
                "tuik",
                "regional",
                "risk_center",
                "analysis",
            }
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
        self.assertEqual(599, available)
        derived = self.connection.execute(
            "SELECT count(*) FROM catalog.metrics "
            "WHERE source_system = 'TCMB_EVDS_DERIVED' "
            "AND source_metric_code = 'DERIVED.BIST.GOLD.TL.GR'"
        ).fetchone()[0]
        self.assertEqual(1, derived)

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
                "TP_ALTINPIYASA_KAP02",
                "DERIVED_BIST_GOLD_TL_GR",
            }
            <= columns
        )
        june_2026 = self.connection.execute(
            "SELECT TP_MK_KUL_YTL, TP_MK_F_BILESIK, "
            "TP_ALTINPIYASA_KAP02, DERIVED_BIST_GOLD_TL_GR "
            "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
        ).fetchone()
        self.assertIsNone(june_2026[0])
        self.assertIsNotNone(june_2026[1])
        self.assertIsNotNone(june_2026[2])
        self.assertAlmostEqual(june_2026[2] / 1000, june_2026[3], places=10)

    def test_empty_gap_tables_keep_semantic_column_types(self):
        for table in ["evds.housing_coverage_gaps", "evds.market_controls_coverage_gaps"]:
            schema = {
                row[0]: row[1]
                for row in self.connection.execute(f"DESCRIBE {table}").fetchall()
            }
            self.assertEqual("VARCHAR", schema["series_code"])
            self.assertEqual("VARCHAR", schema["period"])
            self.assertEqual("BOOLEAN", schema["is_structural_na"])
            self.assertEqual("BOOLEAN", schema["is_unresolved_missing"])

    def test_completed_weekly_bddk_is_loaded(self):
        count = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_measurements"
        ).fetchone()[0]
        self.assertEqual(1025974, count)
        source_count = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_source_tables"
        ).fetchone()[0]
        self.assertEqual(18018, source_count)
        dictionary_count = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_metric_dictionary"
        ).fetchone()[0]
        self.assertEqual(188, dictionary_count)
        unmatched = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_measurements m "
            "ANTI JOIN bddk.weekly_source_tables s USING "
            "(observation_date, table_id, group_code)"
        ).fetchone()[0]
        self.assertEqual(0, unmatched)
        unmatched_metrics = self.connection.execute(
            "SELECT count(*) FROM bddk.weekly_measurements m "
            "ANTI JOIN bddk.weekly_metric_dictionary d USING "
            "(table_id, metric_code)"
        ).fetchone()[0]
        self.assertEqual(0, unmatched_metrics)

    def test_finturk_branch_zero_fallbacks_are_queryable_and_audited(self):
        source_nulls, usable_zeros, derived = self.connection.execute(
            "SELECT "
            "count(*) FILTER (WHERE value IS NULL), "
            "count(*) FILTER (WHERE value IS NULL AND usable_value = 0), "
            "count(*) FILTER (WHERE is_analytically_resolved) "
            "FROM bddk.finturk_measurements "
            "WHERE table_no = 6 AND measure_code = 'SubeSayisi'"
        ).fetchone()
        self.assertEqual((1328, 1328, 1328), (source_nulls, usable_zeros, derived))
        audit_count, violations = self.connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE NOT identity_holds OR identity_residual <> 0) "
            "FROM bddk.finturk_branch_zero_fallback_audit"
        ).fetchone()
        self.assertEqual((1328, 0), (audit_count, violations))

    def test_finturk_compact_measurements_keep_source_provenance(self):
        source_count = self.connection.execute(
            "SELECT count(*) FROM bddk.finturk_source_tables"
        ).fetchone()[0]
        self.assertEqual(154, source_count)
        unmatched = self.connection.execute(
            "SELECT count(*) FROM bddk.finturk_measurements m "
            "ANTI JOIN bddk.finturk_source_tables s USING "
            "(quarter, table_no, source_file, source_sha256)"
        ).fetchone()[0]
        self.assertEqual(0, unmatched)

    def test_regional_housing_panel_is_queryable_and_unique(self):
        count, distinct_count = self.connection.execute(
            "SELECT count(*), count(DISTINCT province_key || ':' || quarter) "
            "FROM regional.housing_quarterly"
        ).fetchone()
        self.assertEqual((1782, 1782), (count, distinct_count))
        province_count = self.connection.execute(
            "SELECT count(DISTINCT province_key) FROM regional.housing_quarterly"
        ).fetchone()[0]
        self.assertEqual(81, province_count)
        istanbul = self.connection.execute(
            "SELECT housing_sales_total_count, housing_credit_per_capita_try, "
            "regional_ykke_index FROM regional.housing_quarterly "
            "WHERE province_name = 'İSTANBUL' AND quarter = '2026Q2'"
        ).fetchone()
        self.assertTrue(all(value is not None for value in istanbul))

    def test_regional_price_proxy_is_queryable_without_overwriting_source(self):
        source_nulls, proxy_values, proxy_ready = self.connection.execute(
            "SELECT "
            "count(*) FILTER (WHERE housing_unit_price_try_per_m2 IS NULL), "
            "count(*) FILTER (WHERE housing_unit_price_try_per_m2 IS NULL "
            "AND housing_unit_price_with_proxy_try_per_m2 IS NOT NULL), "
            "count(*) FILTER (WHERE analysis_ready_with_price_proxy) "
            "FROM regional.housing_quarterly"
        ).fetchone()
        self.assertEqual((162, 162, 1782), (source_nulls, proxy_values, proxy_ready))

    def test_tuik_fallback_and_reconciliation_are_queryable(self):
        monthly_count = self.connection.execute(
            "SELECT count(*) FROM tuik.province_housing_sales_monthly"
        ).fetchone()[0]
        fallback_count = self.connection.execute(
            "SELECT count(*) FROM tuik.province_housing_sales_identity_zero_fallbacks"
        ).fetchone()[0]
        mismatch_count = self.connection.execute(
            "SELECT count(*) FROM tuik.province_housing_sales_evds_reconciliation "
            "WHERE reconciliation_status = 'value_mismatch'"
        ).fetchone()[0]
        panel_fallback_quarters = self.connection.execute(
            "SELECT count(*) FROM regional.housing_quarterly "
            "WHERE mortgaged_sales_fallback_used"
        ).fetchone()[0]
        self.assertEqual(31_590, monthly_count)
        self.assertEqual(10, fallback_count)
        self.assertEqual(0, mismatch_count)
        self.assertEqual(8, panel_fallback_quarters)

    def test_household_finance_is_available_in_national_analysis(self):
        columns = {
            row[1]
            for row in self.connection.execute(
                "PRAGMA table_info('analysis.housing_credit_monthly')"
            ).fetchall()
        }
        self.assertTrue(
            {"TP_KKM_K1", "TP_KKM_K2", "TP_KKM_K4", "TP_KM_E041"} <= columns
        )
        june = self.connection.execute(
            "SELECT TP_KKM_K1, TP_KKM_K2, TP_KKM_K4, TP_KM_E041 "
            "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
        ).fetchone()
        self.assertTrue(all(value is not None for value in june))

    def test_risk_center_monthly_data_is_queryable_and_kept_separate(self):
        count, distinct_count = self.connection.execute(
            "SELECT count(*), count(DISTINCT month) "
            "FROM risk_center.housing_credit_monthly"
        ).fetchone()
        self.assertEqual((66, 66), (count, distinct_count))
        june = self.connection.execute(
            "SELECT risk_center_housing_credit_balance_billion_try, "
            "risk_center_housing_credit_borrower_count_million_person, "
            "risk_center_housing_credit_average_balance_try, "
            "risk_center_housing_credit_npl_ratio_pct, "
            "risk_center_first_time_housing_credit_users_thousand_person "
            "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
        ).fetchone()
        self.assertEqual((810.0, 1.6, 509549.0, 0.1, 13.0), june)
        revision_count = self.connection.execute(
            "SELECT count(*) FROM risk_center.overlap_revision_audit "
            "WHERE value_changed"
        ).fetchone()[0]
        self.assertEqual(6, revision_count)

    def test_unpublished_tbb_disbursement_is_not_filled_from_risk_center(self):
        tbb_value = self.connection.execute(
            "SELECT disbursement_amount_million_try "
            "FROM analysis.housing_credit_quarterly WHERE quarter = '2026-06'"
        ).fetchone()[0]
        first_time_users = self.connection.execute(
            "SELECT risk_center_first_time_housing_credit_users_thousand_person "
            "FROM analysis.housing_credit_monthly WHERE month = '2026-06'"
        ).fetchone()[0]
        self.assertIsNone(tbb_value)
        self.assertEqual(13.0, first_time_users)

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
