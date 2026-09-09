import json
import unittest
from pathlib import Path

import pandas as pd

from data_pipeline.risk_center.fetch_monthly_housing_bulletins import (
    discover_june_bulletins,
)


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data_pipeline" / "risk_center" / "monthly_housing_v1"
PROCESSED = BASE / "processed"


class RiskCenterDiscoveryTests(unittest.TestCase):
    def test_full_bulletin_is_selected_without_summary(self):
        html = """
        <div id="accordion-istatistiki-rapor">
          <div class="item">
            <a class="node-title">2026 Haziran - Risk Merkezi Aylık Bülteni</a>
            <a download href="/summary.pdf" type="application/pdf"
               title="RM Aylık Bülten Özeti - Haziran 2026">Özet</a>
            <a download href="/full.pdf" type="application/pdf"
               title="RM Aylık Bülten - Haziran 2026">Tam bülten</a>
          </div>
        </div>
        """
        discovered = discover_june_bulletins(html, "https://example.test")
        self.assertEqual([2026], sorted(discovered))
        self.assertEqual("https://example.test/full.pdf", discovered[2026]["url"])


class RiskCenterDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.panel = pd.read_parquet(PROCESSED / "housing_credit_monthly.parquet")
        cls.vintages = pd.read_parquet(
            PROCESSED / "housing_metric_vintages.parquet"
        )
        cls.overlap = pd.read_parquet(PROCESSED / "overlap_revision_audit.parquet")
        cls.dictionary = pd.read_parquet(PROCESSED / "metric_dictionary.parquet")

    def test_target_period_is_complete(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(6, self.validation["publication_count"])
        self.assertEqual(66, len(self.panel))
        self.assertEqual(66, self.panel["month"].nunique())
        self.assertEqual("2021-01", self.panel["month"].min())
        self.assertEqual("2026-06", self.panel["month"].max())
        metric_columns = self.dictionary["metric_code"].tolist()
        self.assertFalse(self.panel[metric_columns].isna().any().any())

    def test_june_2026_values_match_the_official_chart(self):
        june = self.panel.loc[self.panel["month"].eq("2026-06")].iloc[0]
        self.assertEqual(810.0, june["housing_credit_balance_billion_try"])
        self.assertEqual(1.6, june["housing_credit_borrower_count_million_person"])
        self.assertEqual(509549.0, june["housing_credit_average_balance_try"])
        self.assertEqual(0.1, june["housing_credit_npl_ratio_pct"])
        self.assertEqual(
            13.0,
            june["first_time_housing_credit_users_thousand_person"],
        )

    def test_source_revisions_are_retained_and_latest_vintage_is_selected(self):
        self.assertEqual(390, len(self.vintages))
        self.assertEqual(25, len(self.overlap))
        self.assertEqual(6, int(self.overlap["value_changed"].sum()))
        revision = self.overlap.loc[
            self.overlap["observation_month"].eq("2022-06")
            & self.overlap["metric_code"].eq(
                "housing_credit_balance_billion_try"
            )
        ].iloc[0]
        self.assertEqual(357.2, revision["earliest_value"])
        self.assertEqual(350.2, revision["latest_value"])
        selected = self.panel.loc[self.panel["month"].eq("2022-06")].iloc[0]
        self.assertEqual(350.2, selected["housing_credit_balance_billion_try"])
        self.assertTrue(selected["has_source_revision"])

    def test_first_time_user_count_is_not_described_as_money_disbursement(self):
        row = self.dictionary.loc[
            self.dictionary["metric_code"].eq(
                "first_time_housing_credit_users_thousand_person"
            )
        ].iloc[0]
        self.assertEqual("thousand_person", row["unit"])
        self.assertIn("Kredi kullandırım tutarı değildir", row["caution"])


if __name__ == "__main__":
    unittest.main()
