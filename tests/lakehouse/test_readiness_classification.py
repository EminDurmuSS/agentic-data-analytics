"""Commit 9: ready/acquirable/near_match_available/web_required/unavailable classifier.

Encodes the BÖLÜM 4 audit table methodology from
kkb_hackathon_25_demo_senaryolari.md as a reusable classifier instead of a
one-off manual walkthrough, and adds the deterministic acceptance criterion
this commit exists for: a 0-exact/0-near query like "XBANK" (BÖLÜM 4's own
"bilinen risk" row, the query that fed a real DECISION_BUDGET_EXCEEDED crash
in this session's eval testing) must classify as ``unavailable`` from a
single ``discover`` call, never retry internally, and never be confused with
a topic that is merely outside the lakehouse by design (``web_required``).
"""
import unittest
from pathlib import Path

from agentic_analytics.lakehouse.readiness import (
    KNOWN_EXTERNAL_TOPICS,
    classify_query_readiness,
)
from agentic_analytics.lakehouse.service import LakehouseService

DATABASE = Path(__file__).resolve().parents[2] / "data_pipeline" / "lakehouse" / "analytics.duckdb"


class FakeService:
    """A discover() stand-in so the state machine is testable without a DB."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def discover(self, request):
        self.calls += 1
        return self.result


class ClassificationStateMachineTests(unittest.TestCase):
    def test_a_ready_full_match_classifies_ready(self):
        service = FakeService({"metrics": [{"metric_id": "a", "status": "ready"}], "no_confident_match": None})
        result = classify_query_readiness(service, "taşıt kredisi")
        self.assertEqual("ready", result["status"])
        self.assertEqual(1, len(result["matches"]))

    def test_a_full_match_that_is_only_metadata_only_classifies_acquirable(self):
        service = FakeService({"metrics": [{"metric_id": "evds:TP.X", "status": "metadata_only"}], "no_confident_match": None})
        result = classify_query_readiness(service, "bir evds serisi")
        self.assertEqual("acquirable", result["status"])
        self.assertIn("EVDS_Talep_Uzerine_Indirme_Araci", result["reason"])

    def test_a_full_match_that_is_only_review_required_classifies_near_match(self):
        service = FakeService({"metrics": [{"metric_id": "a", "status": "review_required"}], "no_confident_match": None})
        result = classify_query_readiness(service, "belirsiz metrik")
        self.assertEqual("near_match_available", result["status"])

    def test_no_exact_match_but_semantic_near_matches_classifies_near_match(self):
        service = FakeService({"metrics": [{"metric_id": "a", "status": "ready"}], "no_confident_match": True})
        result = classify_query_readiness(service, "belirsiz")
        self.assertEqual("near_match_available", result["status"])
        self.assertEqual(1, len(result["near_matches"]))

    def test_zero_exact_zero_near_and_not_a_known_external_topic_is_unavailable(self):
        service = FakeService({"metrics": [], "no_confident_match": True})
        result = classify_query_readiness(service, "XBANK")
        self.assertEqual("unavailable", result["status"])
        self.assertEqual(1, service.calls, "classification must not retry discover internally")

    def test_zero_exact_zero_near_but_a_known_external_topic_is_web_required(self):
        service = FakeService({"metrics": [], "no_confident_match": True})
        result = classify_query_readiness(service, "Hazine DİBS ihalesi")
        self.assertEqual("web_required", result["status"])
        self.assertEqual(1, service.calls)

    def test_rejects_empty_query(self):
        with self.assertRaises(ValueError):
            classify_query_readiness(FakeService({}), "   ")

    def test_xbank_is_deliberately_absent_from_the_known_external_allowlist(self):
        # The whole point of this commit: XBANK must not be quietly treated
        # as "known to require the web" -- it is a genuine catalog gap.
        self.assertIsNone(KNOWN_EXTERNAL_TOPICS.get("xbank"))


@unittest.skipUnless(DATABASE.is_file(), "Published lakehouse snapshot unavailable; run build_lakehouse.py first")
class RealCatalogAuditReproductionTests(unittest.TestCase):
    """Reproduces (a current, re-verified subset of) BÖLÜM 4 against the real catalog."""

    @classmethod
    def setUpClass(cls):
        class Store:
            def workspace(self, _):
                return {"snapshot_id": "readiness-audit", "datasets": []}

            def snapshot_path(self, _):
                return DATABASE

        cls.service = LakehouseService(Store(), "readiness-audit")

    def classify(self, query):
        return classify_query_readiness(self.service, query)

    # BÖLÜM 4 rows confirmed "Güvenli" (safe / lakehouse-ready).
    def test_senaryo1_industrial_production_index_is_ready(self):
        self.assertEqual("ready", self.classify("sanayi üretim endeksi")["status"])

    def test_senaryo3_kkm_is_ready(self):
        self.assertEqual("ready", self.classify("KKM kur korumalı mevduat stok")["status"])

    def test_senaryo6_consumer_confidence_is_ready(self):
        self.assertEqual("ready", self.classify("tüketici güven endeksi")["status"])

    def test_senaryo7_construction_sector_npl_is_ready(self):
        self.assertEqual("ready", self.classify("inşaat sektörü takipteki kredi")["status"])

    def test_senaryo19_unemployment_is_ready(self):
        self.assertEqual("ready", self.classify("işsizlik oranı")["status"])

    # BÖLÜM 4's flagged "bilinen risk" row: this is the acceptance criterion.
    def test_senaryo10_xbank_is_unavailable_not_a_crash(self):
        result = self.classify("XBANK banka endeksi")
        self.assertEqual("unavailable", result["status"])

    # BÖLÜM 4 rows confirmed "0 eşleşme" + deliberately out-of-lakehouse.
    def test_senaryo12_dibs_gosterge_tahvil_is_web_required(self):
        self.assertEqual("web_required", self.classify("DİBS gösterge tahvil faizi")["status"])

    def test_senaryo18_protestolu_senet_is_web_required(self):
        self.assertEqual("web_required", self.classify("protestolu senet sayısı")["status"])

    def test_senaryo28_kap_is_web_required(self):
        self.assertEqual("web_required", self.classify("KAP kamuyu aydınlatma bildirimi")["status"])

    def test_senaryo32_mkk_is_web_required(self):
        self.assertEqual("web_required", self.classify("MKK yabancı yatırımcı payı")["status"])

    def test_senaryo24_lcr_is_web_required_not_confused_with_generic_likidite(self):
        self.assertEqual("web_required", self.classify("LCR likidite karşılama oranı")["status"])


if __name__ == "__main__":
    unittest.main()
