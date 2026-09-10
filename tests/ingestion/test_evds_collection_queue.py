import gzip
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from tools.evds_collection_queue import (
    bounded_chunks,
    claim_job,
    connect,
    finish_job,
    plan,
    run_jobs,
    status_report,
    validate_response,
)
from tools.EVDS_Manifest_Indirme_Araci import request_payload, sha256_bytes


class EvdsCollectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="evds-queue-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "catalog.parquet"
        self.database = self.root / "queue.sqlite"
        self.output = self.root / "artifacts"

    def make_plan(self, records=None, start=date(2025, 1, 1), end=date(2025, 3, 31)):
        records = records or [{"series_code": "TEST.A", "frequency": "AYLIK", "default_aggregation": "last", "is_archive": False}]
        pd.DataFrame(records).to_parquet(self.catalog, index=False)
        return plan(self.database, self.catalog, self.root, start, end)

    @staticmethod
    def response(code, values=None, periods=None):
        values = values if values is not None else [10, 20, 30]
        periods = periods if periods is not None else ["2025-01", "2025-02", "2025-03"]
        key = code.replace(".", "_")
        return json.dumps({"items": [{"Tarih": period, key: value} for period, value in zip(periods, values)], "totalCount": len(values), "seriesNames": {key: "Example"}}).encode()

    def fake_success(self, body, timeout, transport):
        self.assertEqual("urllib", transport)
        code = json.loads(body)["series"]
        return self.response(code), {"http_status": 200}

    def test_plan_is_idempotent_and_archive_unknown_lifespan_is_included(self):
        records = [{"series_code": "TEST.ARCHIVE", "frequency": "AYLIK", "default_aggregation": "last", "is_archive": True}]
        first = self.make_plan(records)
        second = plan(self.database, self.catalog, self.root, date(2025, 1, 1), date(2025, 3, 31))
        self.assertEqual(1, first["job_count"])
        self.assertEqual(first, second)
        self.assertEqual(1, second["archive_series"])
        self.assertEqual(1, second["jobs"]["pending"])

    def test_verified_lifespan_can_exclude_an_archive(self):
        result = self.make_plan([{"series_code": "TEST.OLD", "frequency": "AYLIK", "default_aggregation": "last", "is_archive": True, "lifespan_verified": True, "lifespan_end": "2020-12-31"}])
        self.assertEqual(1, result["metadata_series"])
        self.assertEqual(0, result["job_count"])

    def test_high_frequency_leap_year_chunks_are_at_most_365_days(self):
        for frequency in ("GÜNLÜK", "İŞ GÜNÜ", "HAFTALIK(CUMA)", "AYDA İKİ KEZ"):
            chunks = bounded_chunks(date(2021, 1, 1), date(2026, 6, 30), frequency)
            self.assertEqual(date(2021, 1, 1), chunks[0][0])
            self.assertEqual(date(2026, 6, 30), chunks[-1][1])
            for index, (start, end) in enumerate(chunks):
                self.assertLessEqual((end - start).days + 1, 365)
                if index:
                    self.assertEqual(1, (start - chunks[index - 1][1]).days)

    def test_local_native_legacy_data_is_counted_and_partial_coverage_remains_pending(self):
        data = self.root / "processed"
        data.mkdir()
        pd.DataFrame({"series_code": ["TEST.A", "TEST.A"], "observation_date": pd.to_datetime(["2025-01-01", "2025-03-01"]), "value": [10, 30]}).to_parquet(data / "observations_native.parquet", index=False)
        result = self.make_plan()
        self.assertEqual(1, result["existing_physical_series"])
        self.assertEqual(1, result["existing_target_numeric_series"])
        self.assertEqual({"partial_numeric_periods": 1}, result["existing_coverage_status"])
        self.assertEqual(1, result["jobs"]["pending"])

    def test_all_null_physical_series_is_not_numeric_coverage(self):
        data = self.root / "evds/example"
        data.mkdir(parents=True)
        pd.DataFrame({"series_code": ["TEST.A"] * 3, "period": ["2025-01", "2025-02", "2025-03"], "period_start": ["2025-01-01", "2025-02-01", "2025-03-01"], "period_end": ["2025-01-31", "2025-02-28", "2025-03-31"], "value": [None, None, None]}).to_parquet(data / "observations_long.parquet", index=False)
        result = self.make_plan()
        self.assertEqual(1, result["existing_physical_series"])
        self.assertEqual(0, result["existing_target_numeric_series"])
        self.assertEqual(1, result["jobs"]["no_data"])

    def test_verified_existing_request_is_reused_without_claiming_calendar_completeness(self):
        data = self.root / "evds/example"
        data.mkdir(parents=True)
        request = request_payload("TEST.A", "last", "GÜNLÜK", date(2025, 1, 1), date(2025, 3, 31))
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        (data / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2))
        response = self.response("TEST.A", [10], ["01-01-2025"])
        (data / "response.json.gz").write_bytes(gzip.compress(response))
        pd.DataFrame({
            "series_code": ["TEST.A"], "period": ["2025-01-01"], "period_start": ["2025-01-01"], "period_end": ["2025-01-01"], "value": [10],
            "source_request_file": ["request.json"], "source_request_sha256": [sha256_bytes(body)],
            "source_response_file": ["response.json.gz"], "source_response_sha256": [sha256_bytes(response)],
        }).to_parquet(data / "observations_long.parquet", index=False)
        result = self.make_plan([{"series_code": "TEST.A", "frequency": "GÜNLÜK", "default_aggregation": "last", "is_archive": False}])
        self.assertEqual(1, result["jobs"]["succeeded"])
        self.assertEqual({"calendar_unverified": 1}, result["existing_coverage_status"])
        # An unverified raw response cannot be used as completed-request proof.
        (data / "response.json.gz").write_bytes(gzip.compress(b"changed"))
        other = plan(self.root / "other.sqlite", self.catalog, self.root, date(2025, 1, 1), date(2025, 3, 31))
        self.assertEqual(1, other["jobs"]["pending"])
        invalidated = plan(self.database, self.catalog, self.root, date(2025, 1, 1), date(2025, 3, 31))
        self.assertEqual(1, invalidated["jobs"]["pending"])

    def test_conflicting_existing_snapshots_are_not_silently_deduplicated(self):
        for name, values in [("first", [10, 20, 30]), ("second", [10, 99, 30])]:
            data = self.root / "evds" / name
            data.mkdir(parents=True)
            pd.DataFrame({"series_code": ["TEST.A"] * 3, "period": ["2025-01", "2025-02", "2025-03"], "period_start": ["2025-01-01", "2025-02-01", "2025-03-01"], "period_end": ["2025-01-31", "2025-02-28", "2025-03-31"], "value": values}).to_parquet(data / "observations_long.parquet", index=False)
        result = self.make_plan()
        self.assertEqual({"conflicting_snapshots": 1}, result["existing_coverage_status"])
        self.assertEqual(1, result["jobs"]["pending"])

    def test_atomic_claim_and_expired_lease_recovery_reject_stale_worker(self):
        self.make_plan()
        first = claim_job(self.database, now=100, lease_seconds=10)
        self.assertIsNone(claim_job(self.database, now=105, lease_seconds=10))
        recovered = claim_job(self.database, now=111, lease_seconds=10)
        self.assertEqual(first["job_id"], recovered["job_id"])
        self.assertNotEqual(first["lease_token"], recovered["lease_token"])
        self.assertEqual(2, recovered["attempts"])
        with self.assertRaisesRegex(ValueError, "Lease ownership"):
            finish_job(self.database, first, "succeeded")
        finish_job(self.database, recovered, "succeeded", result={"test": "complete"})
        self.assertEqual(1, status_report(self.database)["jobs"]["succeeded"])

    def test_failed_request_does_not_abort_other_jobs_and_retry_is_durable(self):
        records = [{"series_code": code, "frequency": "AYLIK", "default_aggregation": "last", "is_archive": False} for code in ("TEST.A", "TEST.B")]
        self.make_plan(records)
        def fetch(body, timeout, transport):
            if json.loads(body)["series"] == "TEST.A":
                return b"rate limited", {"http_status": 429}
            return self.fake_success(body, timeout, transport)
        result = run_jobs(self.database, self.output, max_jobs=2, min_interval_seconds=0, fetch=fetch)
        self.assertEqual(2, len(result["processed"]))
        self.assertEqual(1, result["queue"]["jobs"]["retryable_failed"])
        self.assertEqual(1, result["queue"]["jobs"]["succeeded"])
        resumed = status_report(self.database)
        self.assertEqual(result["queue"]["jobs"], resumed["jobs"])
        self.assertEqual(2, len(list(self.output.glob("*/attempt-*/response.json.gz"))))

    def test_success_preserves_raw_and_source_hashes_and_does_not_redownload(self):
        self.make_plan()
        result = run_jobs(self.database, self.output, min_interval_seconds=0, fetch=self.fake_success)
        self.assertEqual(1, result["queue"]["jobs"]["succeeded"])
        artifact = next(self.output.glob("*/attempt-*"))
        rows = pd.read_parquet(artifact / "observations.parquet")
        self.assertEqual(3, len(rows))
        self.assertTrue(rows.source_response_sha256.str.len().eq(64).all())
        self.assertEqual(self.response("TEST.A"), gzip.decompress((artifact / "response.json.gz").read_bytes()))
        again = run_jobs(self.database, self.output, min_interval_seconds=0, fetch=lambda *_: self.fail("Unexpected network retry"))
        self.assertEqual([], again["processed"])
        replanned = plan(self.database, self.catalog, self.root, date(2025, 1, 1), date(2025, 3, 31))
        self.assertEqual(1, replanned["jobs"]["succeeded"])

    def test_wrong_series_truncation_out_of_range_and_infinite_values_are_rejected(self):
        self.make_plan()
        job = claim_job(self.database)
        payloads = [
            self.response("TEST.WRONG"),
            self.response("TEST.A", [10], ["2024-12"]),
            self.response("TEST.A", [float("inf")], ["2025-01"]),
            json.dumps({"totalCount": 1000, "items": [{"Tarih": "2025-01", "TEST_A": 10}]}).encode(),
            self.response("TEST.A", [10, 20], ["2025-01", "2025-01"]),
        ]
        for payload in payloads:
            with self.subTest(payload=payload[:100]), self.assertRaises(ValueError):
                validate_response(payload, job)

    def test_no_data_is_terminal_but_not_claimed_as_complete_numeric_history(self):
        self.make_plan()
        result = run_jobs(self.database, self.output, min_interval_seconds=0, fetch=lambda *_: (self.response("TEST.A", [None, None, None]), {"http_status": 200}))
        self.assertEqual(1, result["queue"]["jobs"]["no_data"])
        artifact = next(self.output.glob("*/attempt-*"))
        validation = json.loads((artifact / "validation.json").read_text())
        self.assertEqual("no_target_numeric_data", validation["coverage_status"])
        self.assertEqual(3, len(validation["missing_numeric_periods"]))

    def test_real_evds_weekly_end_boundary_convention_is_excluded_and_audited(self):
        self.make_plan([{"series_code": "TEST.A", "frequency": "HAFTALIK(CUMA)", "default_aggregation": "last", "is_archive": False}])
        job = claim_job(self.database)
        rows, audit = validate_response(self.response("TEST.A", [10, 20], ["03-01-2025", "04-04-2025"]), job)
        self.assertEqual(1, len(rows))
        self.assertEqual("2025-01-03", rows[0]["period"])
        self.assertEqual(["2025-04-04"], audit["excluded_weekly_boundary_periods"])
        self.assertEqual(1, audit["outside_requested_range_rows"])
        for invalid in ["11-04-2025", "03-04-2025", "27-12-2024"]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "outside"):
                validate_response(self.response("TEST.A", [10, 20], ["03-01-2025", invalid]), job)

    def test_permanent_http_error_and_exhausted_retry_are_explicit(self):
        self.make_plan()
        result = run_jobs(self.database, self.output, min_interval_seconds=0, fetch=lambda *_: (b"not found", {"http_status": 404}))
        self.assertEqual(1, result["queue"]["jobs"]["permanent_failed"])
        with connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='pending',attempts=0")
        exhausted = run_jobs(self.database, self.output, max_attempts=1, min_interval_seconds=0, fetch=lambda *_: (b"busy", {"http_status": 503}))
        self.assertEqual(1, exhausted["queue"]["jobs"]["permanent_failed"])

    def test_different_target_window_cannot_silently_reuse_a_queue(self):
        self.make_plan()
        with self.assertRaisesRegex(ValueError, "another catalog snapshot or target window"):
            plan(self.database, self.catalog, self.root, date(2021, 1, 1), date(2026, 6, 30))


if __name__ == "__main__":
    unittest.main()
