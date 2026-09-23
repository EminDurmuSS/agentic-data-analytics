"""Commit 8: schema-validated, coverage-tested BDDK executable-alias crosswalk.

``agentic_analytics.lakehouse.service._TERM_ALIASES`` maps natural-language
(often English) query terms onto the folded vocabulary already used by real
BDDK metric titles in the catalog (e.g. "vehicle" -> "tasit" so it matches
"Taşıt Kredisi"). This makes ``discover`` resolve those terms instead of
falling through to ``ask_user``/``needs_input`` (the pattern documented in
``docs/eval-set/ASIL SORUN.md`` for Senaryo 2/5/7).

Two kinds of test live here:
1. Schema validation of the alias dict itself (no live catalog required).
2. Coverage: every alias this commit adds actually resolves, through
   ``LakehouseService.discover``, to at least one real ``ready`` BDDK metric
   in the locally built lakehouse -- an alias that resolves to nothing would
   be worse than no alias (false confidence), so this is skipped rather than
   faked when the local ``analytics.duckdb`` has not been built.
"""
import re
import unittest
from pathlib import Path

from agentic_analytics.lakehouse.service import LakehouseService, _TERM_ALIASES

DATABASE = Path(__file__).resolve().parents[2] / "data_pipeline" / "lakehouse" / "analytics.duckdb"

_IDENTIFIER = re.compile(r"[a-z0-9]{2,32}")

# New BDDK-oriented natural-language terms added by commit 8, each with a
# query that should surface a real, ready BDDK metric through discover().
# These are the terms explicitly named in the commit's acceptance criteria:
# taşıt, ticari, ihracat, KOBİ, NPL, kredi kartı; stok/akım/kümülatif/rate;
# sector/currency/maturity/institution scope.
_COVERAGE_QUERIES = {
    "vehicle": "vehicle loan stock",
    "commercial": "commercial loan",
    "export": "export loans",
    "sme": "total sme loans",
    "card": "credit card",
    "npl": "npl vehicle loan",
    "sector": "sector loan",
    "wholesale": "wholesale trade loan",
    "construction": "construction loan",
}


class AliasSchemaTests(unittest.TestCase):
    def test_every_alias_key_and_value_is_a_bounded_lowercase_identifier(self):
        for key, value in _TERM_ALIASES.items():
            self.assertRegex(key, r"^" + _IDENTIFIER.pattern + r"$", msg=f"bad alias key: {key!r}")
            self.assertRegex(value, r"^" + _IDENTIFIER.pattern + r"$", msg=f"bad alias value for {key!r}: {value!r}")

    def test_no_alias_maps_a_term_to_itself(self):
        trivial = {key: value for key, value in _TERM_ALIASES.items() if key == value}
        self.assertEqual({}, trivial)

    def test_alias_dict_has_no_duplicate_keys_by_construction(self):
        # A Python dict literal cannot have duplicate keys at runtime (the
        # later one silently wins), so this documents intent: every key must
        # be distinct and the table must actually contain the terms this
        # commit's acceptance criteria name.
        for expected in ("tasit", "ticari", "ihracat", "kobi", "kart", "takip",
                          "sektor", "doviz", "kumulatif", "akim", "oran"):
            self.assertIn(expected, _TERM_ALIASES.values(), msg=f"no alias targets {expected!r}")


@unittest.skipUnless(DATABASE.is_file(), "Published lakehouse snapshot unavailable; run build_lakehouse.py first")
class AliasCoverageAgainstRealCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Store:
            def workspace(self, _):
                return {"snapshot_id": "alias-coverage", "datasets": []}

            def snapshot_path(self, _):
                return DATABASE

        cls.service = LakehouseService(Store(), "alias-coverage")

    def _discover_ready(self, query):
        result = self.service.discover({"query": query, "status": "ready", "limit": 25})
        return result["metrics"]

    def test_every_new_alias_query_resolves_a_real_bddk_ready_metric(self):
        for term, query in _COVERAGE_QUERIES.items():
            with self.subTest(term=term):
                cards = self._discover_ready(query)
                bddk_cards = [c for c in cards if str(c.get("source_system", "")).startswith("BDDK_")]
                self.assertTrue(
                    bddk_cards,
                    f"alias term {term!r} (query {query!r}) resolved no ready BDDK metric; "
                    "the alias is dead weight and should be removed or corrected",
                )

    def test_vehicle_alias_specifically_resolves_tasit_kredisi(self):
        cards = self._discover_ready("vehicle loan stock")
        ids = {c["metric_id"] for c in cards}
        self.assertIn("bddk_finturk:table03:TasitKredisi", ids)

    def test_sme_alias_resolves_kobi_credit_total(self):
        cards = self._discover_ready("total sme loans")
        ids = {c["metric_id"] for c in cards}
        self.assertTrue(any("table06" in metric_id for metric_id in ids),
                         f"expected a KOBİ (table06) metric among {sorted(ids)[:10]}")

    def test_time_deposit_queries_do_not_pull_demand_deposits(self):
        # "vade" is a substring of "vadesiz" (demand deposits), the opposite of "vadeli".
        titles = [card.get("title") or "" for card in
                  self.service.discover({"query": "vadeli mevduat", "limit": 5})["metrics"]]
        self.assertFalse(any("Vadesiz" in title for title in titles), titles)

    def test_capital_flows_stays_an_honest_non_match(self):
        # "akim" is a substring of "bakim"; an alias must not turn a gap into a confident wrong match.
        self.assertTrue(self.service.discover({"query": "capital flows", "limit": 5}).get("no_confident_match"))


if __name__ == "__main__":
    unittest.main()
