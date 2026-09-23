"""Asgari ücret (minimum wage) decision lookup table: a finite, curated
Resmi Gazete decision history, not an EVDS time series (see
data_pipeline/evidence/reference/minimum_wage_notes.md for provenance)."""
import json
import unittest
from datetime import date
from pathlib import Path

from agentic_analytics.lakehouse.reference_data import (
    load_minimum_wage_decisions,
    minimum_wage_for_date,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "data_pipeline" / "evidence" / "reference" / "minimum_wage_decisions.json"
NOTES_PATH = ROOT / "data_pipeline" / "evidence" / "reference" / "minimum_wage_notes.md"


class MinimumWageReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decisions = load_minimum_wage_decisions()

    def test_loads_expected_number_of_decisions_covering_2021_to_2026(self):
        self.assertEqual(len(self.decisions), 8)
        self.assertEqual(self.decisions[0]["decision_id"], "AUTK_2021")
        self.assertEqual(self.decisions[0]["effective_start"], "2021-01-01")
        self.assertEqual(self.decisions[-1]["decision_id"], "AUTK_2026")
        self.assertEqual(self.decisions[-1]["effective_start"], "2026-01-01")
        self.assertIsNone(self.decisions[-1]["effective_end"])

    def test_every_decision_has_required_fields_and_honest_provenance(self):
        required = {"decision_id", "effective_start", "effective_end", "net_monthly_try",
                    "gross_monthly_try", "gross_daily_try", "currency", "decision_body",
                    "decision_number", "decision_date", "resmi_gazete_date", "resmi_gazete_no",
                    "resmi_gazete_url", "source_url", "verified_live", "verified_asof_utc", "source_note"}
        for row in self.decisions:
            self.assertEqual(required, set(row), row["decision_id"])
            self.assertTrue(row["verified_live"] is True, row["decision_id"])
            self.assertTrue(row["source_url"].startswith("https://"), row["decision_id"])
            self.assertTrue(row["source_note"].strip(), row["decision_id"])
            self.assertEqual(row["currency"], "TRY")
            # ISO date sanity: raises ValueError if malformed.
            date.fromisoformat(row["effective_start"])
            date.fromisoformat(row["resmi_gazete_date"])
            if row["effective_end"] is not None:
                date.fromisoformat(row["effective_end"])
                self.assertLess(row["effective_start"], row["effective_end"])

    def test_net_and_gross_amounts_are_positive_and_monotonically_non_decreasing(self):
        nets = [row["net_monthly_try"] for row in self.decisions]
        grosses = [row["gross_monthly_try"] for row in self.decisions]
        self.assertTrue(all(value > 0 for value in nets))
        self.assertTrue(all(value > 0 for value in grosses))
        self.assertEqual(nets, sorted(nets))
        self.assertEqual(grosses, sorted(grosses))
        for row in self.decisions:
            self.assertLess(row["net_monthly_try"], row["gross_monthly_try"], row["decision_id"])

    def test_gross_daily_matches_gross_monthly_over_thirty_as_official_daily_rule(self):
        for row in self.decisions:
            self.assertAlmostEqual(row["gross_daily_try"], row["gross_monthly_try"] / 30, places=2,
                                    msg=row["decision_id"])

    def test_periods_are_contiguous_with_no_gap_or_overlap(self):
        for previous, current in zip(self.decisions, self.decisions[1:]):
            self.assertIsNotNone(previous["effective_end"], previous["decision_id"])
            previous_end = date.fromisoformat(previous["effective_end"])
            current_start = date.fromisoformat(current["effective_start"])
            self.assertEqual((current_start - previous_end).days, 1,
                              f"{previous['decision_id']} -> {current['decision_id']}")

    def test_minimum_wage_for_date_resolves_the_covering_decision(self):
        self.assertEqual(minimum_wage_for_date("2023-08-15")["decision_id"], "AUTK_2023_H2")
        self.assertEqual(minimum_wage_for_date("2021-01-01")["decision_id"], "AUTK_2021")
        self.assertEqual(minimum_wage_for_date("2026-09-23")["decision_id"], "AUTK_2026")
        self.assertIsNone(minimum_wage_for_date("2020-12-31"))

    def test_missing_field_raises_instead_of_silently_loading(self):
        with self.assertRaises(ValueError):
            load_minimum_wage_decisions(Path(__file__))  # not JSON at all -> json.JSONDecodeError subclass path
        broken_dir = Path(__file__).resolve().parent
        broken_path = broken_dir / "_broken_minimum_wage_fixture.json"
        broken_path.write_text(json.dumps([{"decision_id": "X"}]), encoding="utf-8")
        self.addCleanup(broken_path.unlink)
        with self.assertRaises(ValueError):
            load_minimum_wage_decisions(broken_path)

    def test_notes_file_exists_and_documents_discovery_status(self):
        self.assertTrue(NOTES_PATH.exists())
        text = NOTES_PATH.read_text(encoding="utf-8")
        self.assertIn("discover", text)
        self.assertIn("EVDS", text)

    def test_raw_json_on_disk_is_valid_and_matches_loader_count(self):
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(raw), len(self.decisions))


if __name__ == "__main__":
    unittest.main()
