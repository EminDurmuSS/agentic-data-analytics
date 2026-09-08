import json
import unittest
from pathlib import Path

import pandas as pd

from tools.TBB_Kredi_Raporlari_Indirme_Araci import (
    discover_attachments,
    discover_report_url,
    discover_year_terms,
    quarters,
)


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data_pipeline" / "tbb" / "processed"


class TbbDownloaderTests(unittest.TestCase):
    def test_default_range_has_22_quarters(self):
        periods = quarters("2021-03", "2026-06")
        self.assertEqual(22, len(periods))
        self.assertEqual("2021-03", periods[0])
        self.assertEqual("2026-06", periods[-1])

    def test_public_page_parsers_use_report_family_and_attachment_metadata(self):
        category_html = """
        <div class="accordion-item">
          <button class="accordion-button">Tüketici Kredileri ve Konut Kredileri</button>
          <select class="rapor-donemi-secimi">
            <option value="/istatistiki-raporlar-liste/11265?rapor_donemi=11282">2021</option>
          </select>
        </div>
        """
        self.assertEqual({2021: 11282}, discover_year_terms(category_html))

        list_html = """
        <a href="/istatistiki-raporlar/2021-mart-tuketici-kredileri-ve-konut-kredileri">
          Rapor
        </a>
        """
        report_url = discover_report_url(list_html, "https://www.tbb.org.tr")
        self.assertEqual(
            "https://www.tbb.org.tr/istatistiki-raporlar/2021-mart-tuketici-kredileri-ve-konut-kredileri",
            report_url,
        )

        report_html = """
        <a href="/download/report.xls" type="application/vnd.ms-excel"
           title="Rapor.xls" download>Excel</a>
        <a href="/download/report.pdf" type="application/pdf"
           title="Rapor.pdf" download>PDF</a>
        """
        attachments = discover_attachments(report_html, "https://www.tbb.org.tr")
        self.assertEqual({"xls", "pdf"}, set(attachments))


class TbbDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (PROCESSED / "validation.json").read_text(encoding="utf-8")
        )
        cls.housing = pd.read_parquet(
            PROCESSED / "housing_credit_quarterly.parquet"
        )
        cls.metrics = pd.read_parquet(
            PROCESSED / "consumer_credit_product_metrics.parquet"
        )

    def test_only_unpublished_2026_june_is_a_source_gap(self):
        self.assertEqual("passed_with_source_gaps", self.validation["status"])
        self.assertEqual(["2026-06"], self.validation["source_gap_periods"])
        self.assertEqual(21, self.validation["parsed_workbooks"])
        gap = self.validation["source_gap_details"][0]
        self.assertEqual("not_published", gap["source_gap_reason"])
        self.assertIn("last_checked_at_utc", gap)
        self.assertTrue(gap["official_listing_url"].startswith("https://"))
        source_gaps = pd.read_parquet(PROCESSED / "source_gaps.parquet")
        self.assertEqual(["2026-06"], source_gaps["period"].tolist())
        self.assertEqual(["not_published"], source_gaps["source_gap_reason"].tolist())

    def test_housing_disbursement_is_quarterly_and_unique(self):
        self.assertEqual(21, len(self.housing))
        self.assertFalse(self.housing["quarter"].duplicated().any())
        self.assertFalse(self.housing["is_monthly_disbursement"].any())
        first = self.housing.loc[self.housing["quarter"].eq("2021-03")].iloc[0]
        last = self.housing.loc[self.housing["quarter"].eq("2026-03")].iloc[0]
        self.assertAlmostEqual(
            11691.641374, first["disbursement_amount_million_try"], places=6
        )
        self.assertAlmostEqual(
            99520.736272, last["disbursement_amount_million_try"], places=6
        )

    def test_flow_and_stock_are_separate_measures(self):
        self.assertEqual(
            {"flow", "stock"}, set(self.metrics["flow_stock_semantics"].unique())
        )
        self.assertEqual(
            0,
            int(
                self.metrics.duplicated(
                    ["quarter", "measure", "currency_group"]
                ).sum()
            ),
        )


if __name__ == "__main__":
    unittest.main()
