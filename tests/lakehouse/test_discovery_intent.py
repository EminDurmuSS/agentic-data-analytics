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

    def test_catalog_rate_whose_unit_field_names_an_aggregation_is_still_a_rate(self):
        # EVDS records "Ağırlıklı ortalama" (weighted average) as the unit of many rates.
        profile = semantic_profile({"title": "3 Aya Kadar Vadeli (TL Mevduat, Akım, %)", "kind": "unknown",
                                    "unit": "Ağırlıklı ortalama", "currency": None})
        self.assertEqual(profile["measure"], "rate_or_ratio")

    def test_weighted_average_deposit_rate_is_not_outranked_by_maximum_rate_siblings(self):
        def metadata_only(code, title, unit, group, frequency):
            return {"metric_id": "evds:" + code, "title": title, "group_name": group, "source_system": "TCMB_EVDS",
                    "native_frequency": frequency, "kind": "unknown", "unit": unit, "currency": None, "scale": 1,
                    "status": "metadata_only", "dimensions": {}, "binding_available": False}
        self.bindings = {binding["metric_id"]: binding for binding in (
            metadata_only("TP.TRY.MT02.S", "3 Aya Kadar Vadeli (TL, %)", "percent",
                          "Bankalarca Mevduatlara Fiilen Uygulanan Azami Faiz Oranları", "monthly"),
            metadata_only("TP.TRY.MT02", "3 Aya Kadar Vadeli (TL Mevduat, Akım, %)", "Ağırlıklı ortalama",
                          "Mevduat Faiz Oranları (Akım)", "weekly_friday"))}
        found = self.service.discover({"query": "3 aya kadar vadeli TL mevduat faiz oranları"})["metrics"]
        self.assertEqual(found[0]["metric_id"], "evds:TP.TRY.MT02")

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

    def test_vintage_intent_is_explicit_and_never_inferred_from_a_bare_year(self):
        self.assertEqual("unspecified", query_intent("2024 aylık konut satışları")["vintage_preference"])
        self.assertEqual([2024], query_intent("2024 aylık konut satışları")["reference_years"])
        self.assertEqual("first_published", query_intent("ilk yayımlanan 2024 konut satışları")["vintage_preference"])
        self.assertEqual("current_revised", query_intent("güncel revize 2024 konut satışları")["vintage_preference"])

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

    def test_explicit_source_name_prefers_the_source_native_metric_over_a_derived_panel(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-source-ranking", "datasets": []}

            def snapshot_path(self, _):
                return database

        result = LakehouseService(ReadOnlyStore(), "real-source-ranking").discover({
            "query": "2025 BDDK toplam konut kredisi stoku",
            "limit": 10,
            "status": "ready",
        })
        self.assertEqual(
            "bddk_monthly:table04:2:fffae80eca08:Toplam",
            result["metrics"][0]["metric_id"],
        )
        self.assertEqual("BDDK", result["metrics"][0]["source_organization"])
        self.assertEqual("source_system", result["metrics"][0]["source_match"]["basis"])
        self.assertEqual(["bddk"], result["source_selection"]["requested"])

    def test_cross_source_reconciliation_language_returns_native_candidates_for_each_source(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-cross-source-discovery", "datasets": []}

            def snapshot_path(self, _):
                return database

        result = LakehouseService(ReadOnlyStore(), "real-cross-source-discovery").discover({
            "query": "TÜİK ve EVDS kataloglarında il bazında ortak bulunan, anlamı ve birimi gerçekten eşleşen konut satış göstergeleri",
            "limit": 25,
            "status": "ready",
        })
        self.assertFalse(result.get("no_confident_match"), result)
        self.assertEqual(["tuik", "evds"], result["source_selection"]["requested"])
        self.assertEqual("compare_sources", result["source_selection"]["mode"])
        direct = {
            card["source_match"]["requested_source"]: card
            for card in result["metrics"]
            if card.get("source_match", {}).get("basis") == "source_system"
        }
        self.assertIn("tuik", direct)
        self.assertIn("evds", direct)
        self.assertEqual("TUIK_DATA_PORTAL", direct["tuik"]["source_system"])
        self.assertEqual("TCMB_EVDS", direct["evds"]["source_system"])
        self.assertNotIn("bazinda", result.get("uncovered_terms", []))
        self.assertNotIn("gostergeleri", result.get("uncovered_terms", []))

    def test_cross_source_reconciliation_ignores_output_format_words_in_full_prompt(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-cross-source-full-prompt", "datasets": []}

            def snapshot_path(self, _):
                return database

        prompt = (
            "TÜİK ve EVDS kataloglarında il bazında ortak bulunan, anlamı ve birimi gerçekten "
            "eşleşen konut satış göstergelerini iki kaynaktaki kodlarıyla listele."
        )
        result = LakehouseService(ReadOnlyStore(), "real-cross-source-full-prompt").discover({
            "query": prompt,
            "limit": 25,
            "status": "ready",
        })
        self.assertFalse(result.get("no_confident_match"), result)
        self.assertEqual(["tuik", "evds"], result["source_selection"]["requested"])
        self.assertEqual(["tuik", "evds"], result["source_selection"]["represented"])
        direct_sources = {
            card["source_match"]["requested_source"]: card["source_system"]
            for card in result["metrics"]
            if card.get("source_match", {}).get("basis") == "source_system"
        }
        self.assertEqual("TUIK_DATA_PORTAL", direct_sources["tuik"])
        self.assertEqual("TCMB_EVDS", direct_sources["evds"])

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

    def test_natural_housing_sales_prompts_preserve_historical_first_publication_vintage(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-housing-sales-discovery", "datasets": []}

            def snapshot_path(self, _):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-housing-sales-discovery")
        expected = "tuik_province_housing_sales_first_published:housing_sales_total_count"
        revised = "tuik_province_housing_sales:housing_sales_total_count"
        for prompt in (
            "2024 yılında İstanbul'da aylık toplam konut satışlarını göster.",
            "Pekâlâ, şimdi bu tabloya 2023 yılı İstanbul toplam konut satışlarını da ekle.",
        ):
            with self.subTest(prompt=prompt):
                result = service.discover({"query": initial_query(prompt), "limit": 5, "status": "ready"})
                self.assertFalse(result.get("no_confident_match"), result)
                self.assertEqual(result["metrics"][0]["metric_id"], expected)
                self.assertEqual(result["metrics"][0]["matched_dimensions"]["province_key"], ["istanbul"])
                self.assertEqual(result["metrics"][0]["native_frequency"], "monthly")
                self.assertEqual(
                    [expected, revised],
                    [card["metric_id"] for card in result["metrics"][:2]],
                )
                self.assertEqual("requires_disclosure", result["vintage_selection"]["status"])
                self.assertEqual("first_published", result["vintage_selection"]["recommended"])
                self.assertEqual(
                    {"first_published", "current_revised"},
                    {
                        candidate["vintage_class"]
                        for group in result["vintage_selection"]["candidate_groups"]
                        for candidate in group["candidates"]
                    },
                )

    def test_explicit_housing_sales_vintage_wording_overrides_historical_default(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-housing-sales-vintage", "datasets": []}

            def snapshot_path(self, _):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-housing-sales-vintage")
        cases = (
            ("2024 İstanbul ilk yayımlanan toplam konut satışları",
             "first_published", "tuik_province_housing_sales_first_published:housing_sales_total_count"),
            ("2024 İstanbul güncel revize toplam konut satışları",
             "current_revised", "tuik_province_housing_sales:housing_sales_total_count"),
        )
        for prompt, preference, expected in cases:
            with self.subTest(prompt=prompt):
                result = service.discover({"query": prompt, "limit": 5, "status": "ready"})
                self.assertEqual(preference, result["query_intent"]["vintage_preference"])
                self.assertEqual(expected, result["metrics"][0]["metric_id"])
                if "vintage_selection" in result:
                    self.assertEqual("explicit", result["vintage_selection"]["status"])

    def test_historical_imkb_wording_resolves_to_the_bist_xu100_identity(self):
        database = Path(__file__).parents[2] / "data_pipeline/lakehouse/analytics.duckdb"

        class ReadOnlyStore:
            def workspace(self, _):
                return {"snapshot_id": "real-imkb-discovery", "datasets": []}

            def snapshot_path(self, _):
                return database

        service = LakehouseService(ReadOnlyStore(), "real-imkb-discovery")
        result = service.discover({"query": initial_query("2010 yılına ait İMKB 100 kapanış verisini arıyorum."),
                                   "limit": 5, "status": "ready"})
        self.assertFalse(result.get("no_confident_match"), result)
        self.assertEqual(result["metrics"][0]["metric_id"], "evds:TP.MK.F.BILESIK")
        self.assertEqual(result["metrics"][0]["coverage_start"], "2010-01-01")
        described = service.describe({"metric_id": "evds:TP.MK.F.BILESIK"})["metric"]
        self.assertEqual("XU100", described["canonical_series_code"])
        self.assertEqual("İMKB 100", described["historical_name"])
        self.assertEqual("BIST 100", described["current_name"])
        self.assertEqual("2013-04-05", described["name_change_effective_date"])
        self.assertIn("GenelMektup_4030", described["name_change_source_url"])
        self.assertEqual(
            "current_official_history_after_2020_two_zero_revision",
            described["vintage_policy"],
        )
        self.assertEqual("2020-07-27", described["scale_revision_effective_date"])
        self.assertEqual(0.01, described["scale_revision_factor"])
        self.assertIn("2020-46_Removal_of_Zero", described["scale_revision_source_url"])
        self.assertIn("IMKB_FINAL.pdf", described["historical_archive_source_url"])
        self.assertFalse(described["historical_original_scale_included"])
