"""Guards for the final-answer readability gate and domain search aliases."""
import unittest

from agentic_analytics.agent.runtime import _unreadable, _web_research_message
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

    def test_renders_source_table_cells_by_sanitized_key(self):
        # Regression: preview rows are keyed by sanitized names; the header uses the
        # original column, and the data cells must not come out blank.
        msg = _web_research_message(self._source([{"column_1": "2026-06", "column_2": "102,0"}]))
        self.assertIn("| Tarih | Endeks |", msg)
        self.assertIn("2026-06", msg)
        self.assertIn("102,0", msg)

    def test_positional_list_row_does_not_crash(self):
        msg = _web_research_message(self._source([["2026-07", "103,0"]]))
        self.assertIn("103,0", msg)


if __name__ == "__main__":
    unittest.main()
