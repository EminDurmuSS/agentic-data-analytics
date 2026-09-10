import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENT_DIR = PROJECT_ROOT / "data_pipeline" / "evidence" / "events"


class EventEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = json.loads((EVENT_DIR / "events.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads(
            (EVENT_DIR / "source_documents_manifest.json").read_text(encoding="utf-8")
        )

    def test_all_bddk_decisions_have_downloaded_primary_fulltext(self) -> None:
        bddk_events = [event for event in self.events if event["event_id"].startswith("BDDK_")]
        self.assertEqual(len(bddk_events), 4)
        for event in bddk_events:
            self.assertIn("downloaded_primary_fulltext", event["verification_level"])
            decision = event["evidence_access"]["decision_document"]
            self.assertTrue(decision["full_document_downloaded"])
            self.assertEqual(decision["http_status"], 200)
            self.assertTrue((EVENT_DIR / decision["local_file"]).exists())

    def test_every_pdf_has_hash_and_searchable_text(self) -> None:
        self.assertEqual(len(self.manifest), 8)
        for item in self.manifest:
            self.assertEqual(len(item["sha256"]), 64)
            self.assertEqual(len(item["extracted_text_sha256"]), 64)
            text = (EVENT_DIR / item["extracted_text_file"]).read_text(encoding="utf-8")
            self.assertTrue(text.strip())

    def test_unverified_effective_dates_are_not_invented(self) -> None:
        for event in self.events:
            self.assertIsNone(event["effective_date"])
            self.assertFalse(event["causal_effect_estimated"])


if __name__ == "__main__":
    unittest.main()
