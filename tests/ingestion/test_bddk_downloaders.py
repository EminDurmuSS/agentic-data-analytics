import json
import unittest
from datetime import date

from tools.BDDK_Indirme_Araci import GROUPS as MONTHLY_GROUPS
from tools.BDDK_Indirme_Araci import request_batches, validate_requested_groups
from tools.BDDK_FinTurk_Indirme_Araci import parse_response, quarters
from tools.BDDK_Haftalik_Indirme_Araci import (
    GROUPS as WEEKLY_GROUPS,
    Period,
    load_context_with_retry,
    parse_page,
    parse_periods,
)


class FinTurkDownloaderTests(unittest.TestCase):
    def test_default_competition_range_has_22_quarters(self):
        result = quarters("2021-3", "2026-6")
        self.assertEqual(22, len(result))
        self.assertEqual("2021-3", result[0])
        self.assertEqual("2026-6", result[-1])

    def test_response_parser_preserves_source_columns(self):
        payload = {
            "success": True,
            "Json": {
                "colNames": ["Kod", "Yıl", "Ay", "Şehir", "Grup", "Konut"],
                "colModels": [
                    {"name": "EftKodu"},
                    {"name": "Yil"},
                    {"name": "Ay"},
                    {"name": "Sehir"},
                    {"name": "Grup"},
                    {"name": "KonutKredisi"},
                ],
                "data": {
                    "rows": [
                        {"cell": [10001, 2026, 6, "ANKARA", "SEKTÖR", 123]}
                    ]
                },
                "uyari": "",
            },
        }
        rows, metadata = parse_response(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"), "2026-6"
        )
        self.assertEqual(123, rows[0]["KonutKredisi"])
        self.assertEqual(1, metadata["rows"])


class MonthlyDownloaderTests(unittest.TestCase):
    def test_live_catalog_group_contract_is_complete(self):
        self.assertEqual(set(range(10001, 10011)), set(MONTHLY_GROUPS))
        self.assertEqual("Mevduat-Yabancı", MONTHLY_GROUPS[10010])

    def test_combined_request_uses_one_batch_and_validates_all_groups(self):
        groups = sorted(MONTHLY_GROUPS)
        self.assertEqual([groups], request_batches(groups, "combined"))
        rows = [{"BankaAdi": MONTHLY_GROUPS[group]} for group in groups]
        counts = validate_requested_groups(rows, groups)
        self.assertEqual(10, len(counts))


class WeeklyDownloaderTests(unittest.TestCase):
    def test_live_catalog_group_contract_is_complete(self):
        self.assertEqual(set(range(10001, 10008)), set(WEEKLY_GROUPS))
        self.assertEqual("Kalkinma ve Yatirim", WEEKLY_GROUPS[10003])

    def test_context_reload_retries_transient_root_failure(self):
        class Response:
            text = '<form action="/BultenHaftalik/tr/Home/TarafSec"><input name="__RequestVerificationToken" value="token"></form>'

            def raise_for_status(self):
                return None

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, url, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary timeout")
                return Response()

        session = Session()
        html = load_context_with_retry(session, timeout=1, retries=1)
        self.assertIn("TarafSec", html)
        self.assertEqual(2, session.calls)

    def test_period_catalog_is_filtered_and_sorted(self):
        html = """
        <select id="Donem">
          <option value="2" class="Yil-2021 YilDonem">Ocak/15 (2. Hafta)</option>
          <option value="1" class="Yil-2021 YilDonem">Ocak/08 (1. Hafta)</option>
          <option value="3" class="Yil-2026 YilDonem">Temmuz/03 (27. Hafta)</option>
        </select>
        """
        periods = parse_periods(html, date(2021, 1, 1), date(2026, 6, 30))
        self.assertEqual([date(2021, 1, 8), date(2021, 1, 15)], [p.observation_date for p in periods])

    def test_weekly_page_requires_exact_period_table_and_group(self):
        html = """
        <html><body>
          <h4>Krediler</h4>
          <h5>Sektör</h5>
          <h5>Tarih: <b>8 Ocak 2021 Cuma</b> | Birim: <b>Milyon TL</b></h5>
          <table id="Tablo">
            <thead><tr><th></th><th>Krediler</th><th>TP</th><th>YP</th><th>TOPLAM</th></tr></thead>
            <tbody><tr><td>1</td><td>Toplam Krediler</td><td>10</td><td>5</td><td>15</td></tr></tbody>
          </table>
        </body></html>
        """
        period = Period(367, date(2021, 1, 8), 1, "Ocak/08 (1. Hafta)")
        headers, rows, metadata = parse_page(html, period, 289, 10001)
        self.assertEqual(["", "Krediler", "TP", "YP", "TOPLAM"], headers)
        self.assertEqual("15", rows[0][-1])
        self.assertEqual("2021-01-08", metadata["source_date_parsed"])


if __name__ == "__main__":
    unittest.main()
