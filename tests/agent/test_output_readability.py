"""Guards for the final-answer readability gate and domain search aliases."""
import unittest

from agentic_analytics.agent.runtime import _english_sentences, _unreadable, _web_research_message
from agentic_analytics.lakehouse.service import _search_terms, _term_matches


class ReadabilityGateTests(unittest.TestCase):
    def test_flags_foreign_script_and_replacement_characters(self):
        self.assertTrue(_unreadable("流程短虫短:"))          # the q47 garbage case
        self.assertTrue(_unreadable("cevap: ���"))
        self.assertTrue(_unreadable("답변은 여기 있습니다"))     # Hangul

    def test_accepts_normal_turkish_and_numeric_answers(self):
        self.assertFalse(_unreadable("Bankacılık sektörü net kârı 87.249 milyon TL oldu."))
        self.assertFalse(_unreadable("2026-Q1: %12,3 artış"))   # digits/punctuation, no letters
        self.assertFalse(_unreadable(""))
        # A stray non-Latin character below the 10% threshold is tolerated.
        self.assertFalse(_unreadable("Konut kredisi bakiyesi yüksek. 借"))

    def test_flags_degenerate_repetitive_garbage(self):
        # The real "neler görüyorsun" failure: 369 chars, ~13 letters, 2 coherent words.
        garbage = ("2\n 120\n\n2023.0023.0 2\n**.\n\n\n\n\n\n\n\n\nB2\n20\n   ,  **1 \n    2012**: 2022.0"
                   "\n\n1. **0242\n2023.00120202024\n3.0024024200024\n\n    **be\n**\n |  | .2023\n201224\n0"
                   "\n2023.0024\n\n\n**\n   0\n2020\n0203.002024\n120\n2021003.2\n ** safety\n\n **\n**023")
        self.assertTrue(_unreadable(garbage))

    def test_accepts_a_long_numeric_table_answer(self):
        # A legitimate numeric-heavy table answer keeps enough real words and must pass.
        table = ("2024 İstanbul toplam konut satışları (adet): Ocak 15.002, Şubat 18.118, Mart 21.046, "
                 "Nisan 13.867, Mayıs 21.326, Haziran 14.534. Yıl boyunca satışlar arttı.")
        self.assertFalse(_unreadable(table))


class DomainAliasTests(unittest.TestCase):
    def test_english_domain_terms_map_to_turkish_concepts(self):
        self.assertIn("takip", _search_terms("NPL ratio"))
        self.assertIn("konut", _search_terms("mortgage"))
        terms = _search_terms("housing loan")
        self.assertIn("konut", terms)
        self.assertIn("kredi", terms)


class WholeWordMatchTests(unittest.TestCase):
    def test_proper_noun_ending_in_i_matches_itself_but_fragment_does_not(self):
        # Regression: whole-word 'i'-stemming previously broke provinces ending in 'i'.
        for city in ("kocaeli", "kayseri", "denizli", "tunceli"):
            self.assertTrue(_term_matches(city, city, whole_word=True), city)
        # The precision guard still holds: a shorter fragment is not a whole token.
        self.assertFalse(_term_matches("gumus", "gumushane", whole_word=True))
        self.assertFalse(_term_matches("anka", "ankara", whole_word=True))
        # Turkish suffix tolerance survives as a fallback.
        self.assertTrue(_term_matches("mevduati", "altin mevduat", whole_word=True))


class WebResearchMessageTests(unittest.TestCase):
    def _source(self, preview):
        return {"sources": [{"title": "TCMB RPPI", "url": "https://www.tcmb.gov.tr/x", "content": "özet metin",
                             "tables": [{"columns": ["column_1", "column_2"],
                                         "original_columns": {"column_1": "Tarih", "column_2": "Endeks"},
                                         "preview": preview}]}]}

    def test_citations_do_not_dump_source_excerpt_or_first_table(self):
        msg = _web_research_message(self._source([{"column_1": "2026-06", "column_2": "102,0"}]))
        self.assertEqual(msg, "Okunan kaynaklar: [TCMB RPPI](https://www.tcmb.gov.tr/x).")
        self.assertNotIn("102,0", msg)
        self.assertNotIn("özet metin", msg)

    def test_positional_list_row_does_not_crash(self):
        msg = _web_research_message(self._source([["2026-07", "103,0"]]))
        self.assertIn("TCMB RPPI", msg)
        self.assertNotIn("103,0", msg)

    def test_only_safe_read_source_links_are_rendered_once(self):
        result = self._source([])
        result["sources"] += [dict(result["sources"][0]),
            {"url": "javascript:alert(1)", "title": "Unsafe"},
            {"url": "https://user:pass@example.org/", "title": "Credentials"},
            {"url": "https://example.org/next(report)", "title": "[Next]"}]
        msg = _web_research_message(result)
        self.assertEqual(msg.count("https://www.tcmb.gov.tr/x"), 1)
        self.assertNotIn("Unsafe", msg)
        self.assertNotIn("Credentials", msg)
        self.assertIn("next%28report%29", msg)
        self.assertEqual(_web_research_message(self._source([]), "[Existing](https://www.tcmb.gov.tr/x)"), "")


if __name__ == "__main__":
    unittest.main()


class EnglishSlipTests(unittest.TestCase):
    def test_finds_an_english_sentence_in_a_turkish_answer(self):
        answer = ("I need to be direct about the situation before doing anything else.\n\n"
                  "**Önce netleştirmem gereken bir nokta var:** ortada eklenecek bir tablo yok.")
        self.assertEqual(_english_sentences(answer),
                         ["I need to be direct about the situation before doing anything else."])

    def test_keeps_source_titles_quotes_links_and_tables(self):
        answer = ("TCMB Vehicle Loans (TRY) (Stock, %) serisi 33,59'dan 40,12'ye yükseldi.\n"
                  "Raporda “Total assets increased by 12 percent in the year to December” ifadesi geçiyor.\n"
                  "[Annual report of the bank for the year 2025](https://example.org/report.pdf)\n"
                  "Kaynak: Annual Report of the Bank for the Year 2025\n"
                  "| Period | Total assets of the bank in the year |\n| --- | --- |")
        self.assertEqual(_english_sentences(answer), [])

