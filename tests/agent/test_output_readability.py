"""Guards for the final-answer readability gate and domain search aliases."""
import unittest

from agentic_analytics.agent.runtime import _unreadable
from agentic_analytics.lakehouse.service import _search_terms


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


if __name__ == "__main__":
    unittest.main()
