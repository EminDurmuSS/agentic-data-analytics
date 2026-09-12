"""Semantic retrieval counterexamples use fixtures; no financial values are invented."""

from contextlib import contextmanager
from pathlib import Path
import unittest

from agentic_analytics.lakehouse.discovery import compare_intent, initial_query, query_intent, semantic_profile
from agentic_analytics.lakehouse.service import LakehouseService


class DiscoveryIntentTests(unittest.TestCase):
    def setUp(self):
        specifications = [
            ("z_overall", "Krediler* [Toplam]", "stock", "TRY", "TRY"),
            ("a_interbank", "Bankalara Kullandırılan Krediler [ToplamNakdi]", "stock", "TRY", "TRY"),
            ("a_guarantee", "Nakdi Kredilerin Teminatı İçin Verilen Teminat Mektupları [Toplam]", "stock", "TRY", "TRY"),
            ("a_micro", "Mikro İşletmelere Kullandırılan Krediler [NakdiKrediToplam]", "stock", "TRY", "TRY"),
            ("a_funding", "Fon Kaynaklı Krediler [Toplam]", "stock", "TRY", "TRY"),
            ("a_noncash", "Gayrinakdi Kredi ve Yükümlülükler [Toplam]", "stock", "TRY", "TRY"),
            ("z_housing", "Tüketici Kredileri - Konut [Toplam]", "stock", "TRY", "TRY"),
            ("a_overdue", "Takipteki Konut Kredileri [Toplam]", "stock", "TRY", "TRY"),
            ("z_rate", "Konut Kredisi (TL, Akım, %)", "rate", "percent", None),
            ("z_count", "Konut Kredisi Kullanan Kişi Sayısı", "count_stock", "count", None),
            ("z_flow", "Konut Kredisi Yeni Kullandırılan Tutarı", "flow", "TRY", "TRY"),
            ("z_clinic", "Clinic Visits", "count_flow", "visits", None),
            ("a_dental", "Dental Clinic Visits", "count_flow", "visits", None),
        ]
        self.bindings = {key: {"metric_id": key, "title": title, "source_system": "BDDK_MONTHLY",
                              "native_frequency": "monthly", "kind": kind, "unit": unit, "currency": currency,
                              "scale": 1, "status": "ready", "dimensions": {}, "value_dimension": "Toplam"}
                         for key, title, kind, unit, currency in specifications}

        @contextmanager
        def context():
            yield None, self.bindings, {"snapshot_id": "synthetic-discovery"}

        self.service = LakehouseService(None, "synthetic-discovery")
        self.service._context = context

    def test_overall_credit_does_not_select_recipient_guarantee_or_novel_product_subset(self):
        for query in ("BDDK bankacılık sektörü toplam nakdi krediler bakiye aylık", "banking total cash loans balance monthly",
                      "BDDK toplam kredi tutarı", "aylık genel kredi bakiyesi"):
            with self.subTest(query=query):
                result = self.service.discover({"query": query, "limit": 25})
                self.assertEqual("z_overall", result["metrics"][0]["metric_id"])
                self.assertIn("query_intent", result)
                if "nakdi" in query:
                    candidates = {card["metric_id"]: card for card in result["metrics"]}
                    self.assertIn("a_interbank", candidates)
                    self.assertIn("a_micro", candidates)
                    self.assertTrue(candidates["a_interbank"]["semantic_match"]["unrequested_qualifiers"])
                    self.assertTrue(any(w["code"] == "additional_title_context" for w in candidates["a_micro"]["semantic_match"]["warnings"]))
                    self.assertEqual("mismatch", candidates["a_noncash"]["semantic_match"]["status"])

    def test_specific_subsets_remain_retrievable_when_the_user_requests_them(self):
        cases = {"bankalara kullandırılan krediler": "a_interbank", "takipteki konut kredisi": "a_overdue",
                 "BDDK konut kredisi bakiyesi": "z_housing", "gayrinakdi krediler": "a_noncash",
                 "mikro işletmelere kullandırılan krediler": "a_micro", "dental clinic visits": "a_dental"}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, self.service.discover({"query": query})["metrics"][0]["metric_id"])

    def test_quantity_semantics_and_generic_domain_do_not_collapse(self):
        cases = {"konut kredisi faiz oranı": "z_rate", "konut kredisi sayısı": "z_count",
                 "konut kredisi akım tutarı": "z_flow", "konut kredisi bakiyesi": "z_housing",
                 "clinic visits count": "z_clinic"}
        for query, expected in cases.items():
            with self.subTest(query=query):
                result = self.service.discover({"query": query})
                self.assertEqual(expected, result["metrics"][0]["metric_id"])

    def test_total_slice_is_not_population_scope_and_unverified_cash_is_marked(self):
        overall, interbank = [semantic_profile(self.bindings[key]) for key in ("z_overall", "a_interbank")]
        intent = query_intent("toplam nakdi kredi bakiyesi")
        self.assertTrue(overall["cash_class_inferred"])
        self.assertTrue(any(note["code"] == "cash_class_inferred" for note in compare_intent(intent, overall)["warnings"]))
        self.assertEqual("Toplam", interbank["slice_label"])
        self.assertIn("bank_recipient", compare_intent(intent, interbank)["unrequested_qualifiers"])

    def test_task_projection_keeps_requested_subsets_flow_and_unknown_meaning(self):
        requests = [
            ("Yüklediğim source_1234567890 dokümanı örnek krediler içeriyor. "
             "BDDK bankalara kullandırılan kredi bakiyelerini getir; sonucu yeni dosyadaki değerlerle karşılaştır ve grafik oluştur.", "a_interbank"),
            ("Yüklediğim source_1234567890 dosyası eski kredi verileri içeriyor. "
             "Konut kredisi yeni kullandırılan tutarı göster; bu verileri dosyaya ekle.", "z_flow"),
        ]
        for request, expected in requests:
            with self.subTest(expected=expected):
                focused = initial_query(request)
                self.assertLessEqual(len(focused), 300)
                self.assertNotIn("source_", focused)
                self.assertEqual(expected, self.service.discover({"query": focused})["metrics"][0]["metric_id"])
        unknown = initial_query("Yüklediğim source_1234567890 dosyası kredi verilerini içeriyor. "
                                "Aylık platin destekli kredi bakiyelerini göster; sonucu tabloya ekle.")
        result = self.service.discover({"query": unknown})
        self.assertFalse(result["metrics"])
        self.assertIn("platin", result["uncovered_terms"])
        self.assertTrue(result["no_confident_match"])


@unittest.skipUnless((Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb").is_file(), "Published catalogue unavailable")
class RealCatalogueIntentTests(unittest.TestCase):
    def test_real_overall_recipient_product_and_interest_choices(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, workspace_id):
                return {"snapshot_id": "real-semantic-discovery", "datasets": []}

            def snapshot_path(self, snapshot_id):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-semantic-discovery")
        cases = [
            ("BDDK bankacılık sektörü toplam nakdi krediler bakiye aylık", "bddk_monthly:table01:10:d19739aeda4a:Toplam"),
            ("BDDK toplam kredi tutarı", "bddk_monthly:table01:10:d19739aeda4a:Toplam"),
            ("bankalara kullandırılan krediler", "bddk_monthly:table01:61:aaedb25ae663:Toplam"),
            ("BDDK aylık konut kredisi bakiyesi", "bddk_monthly:table04:2:fffae80eca08:Toplam"),
            ("TCMB konut kredisi faiz oranı", {"evds:TP.KTF12", "evds:TP.BKR.TRY.18"}),
            ("konut kredisi faiz oranı akım", "evds:TP.KTF12"),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                result = service.discover({"query": query, "status": "ready", "limit": 5})
                self.assertIn(result["metrics"][0]["metric_id"], expected if isinstance(expected, set) else {expected})
                self.assertTrue(all("semantic_match" in card and "coverage_end" in card for card in result["metrics"]))

    def test_actual_full_import_task_prefills_overall_loans_and_prefix_stays_provisional(self):
        # Verbatim task from live round 1. The source and company describe a
        # clearly synthetic upload; all reference candidates are the real catalogue.
        message = ("Yüklediğim source_51744888e29b29204a353fc8361fe59b666892f43f2732d049b54f474a8ea462 kaynak kimlikli deneme_bankasi.csv dosyası, "
                   "tamamen kurgusal Deneme Bankası'nın Ocak-Mart 2026 ay sonu toplam nakdi kredi bakiyelerini içeriyor. Dosyadaki birim milyon TL. "
                   "Bu yeni dosyayı çalışma alanına kat; aynı aylardaki BDDK bankacılık sektörü toplam nakdi kredi bakiyeleriyle tek tabloda karşılaştır. "
                   "Banka bakiyesinin sektör bakiyesine oranını yüzde olarak hesapla ve bu payın çizgi grafiğini oluştur. "
                   "Kaynakları ve bunun kurgusal örnek olduğunu belirt. Kapsamı farklı banka ve sektör verisini bu açık karşılaştırma amacıyla kullanıyorum.")
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-long-task-discovery", "datasets": []}

            def snapshot_path(self, _):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-long-task-discovery")
        result = service.discover({"query": initial_query(message), "limit": 5})
        self.assertEqual("bddk_monthly:table01:10:d19739aeda4a:Toplam", result["metrics"][0]["metric_id"])
        self.assertEqual("stock", result["query_intent"]["time_basis"])
        self.assertNotEqual("rate_or_ratio", result["query_intent"]["measure"])
        self.assertTrue(all("Fon Kaynaklı" not in card["title"] for card in result["metrics"][:3]))
        prefix = service.discover({"query": message[:300], "status": "ready", "limit": 5})
        self.assertTrue(prefix["no_confident_match"])
        self.assertEqual("bddk_monthly:table01:10:d19739aeda4a:Toplam", prefix["near_matches"][0]["metric_id"])
        self.assertFalse(any("source_" in word or ".csv" in word for word in prefix["uncovered_terms"]))
