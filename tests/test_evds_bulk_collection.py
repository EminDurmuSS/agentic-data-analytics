import gzip
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools.evds_bulk_collection import (
    OBSERVATION_COLUMNS, ValidationError, bounded_chunks, canonical, claim_job,
    connect, finish_job, plan, request_payload, retry_after_seconds, run_jobs,
    retry_validated_batch, sha256_bytes, status_report, validate_response,
)


class EvdsBulkCollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evds-bulk-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "queue.sqlite"
        self.catalog = self.root / "catalog.parquet"

    def make_plan(self, records=None, **kwargs):
        records = records or [{"series_code": "TEST.A", "group_code": "group", "frequency": "AYLIK", "default_aggregation": "last", "is_archive": False}]
        pd.DataFrame(records).to_parquet(self.catalog, index=False)
        return plan(self.database, self.catalog, date(2025, 1, 1), date(2025, 3, 31), **kwargs)

    def job(self, codes=None, frequency="AYLIK", start="2025-01-01", end="2025-03-31"):
        return {"series_codes_json": canonical(codes or ["TEST.A"]), "frequency": frequency,
                "aggregation": "last", "start_date": start, "end_date": end}

    def response(self, codes=None, periods=None, values=None):
        codes = codes or ["TEST.A"]
        periods = ["2025-01", "2025-02", "2025-03"] if periods is None else periods
        values = {code: [10, 20, 30] for code in codes} if values is None else values
        return canonical({"items": [{"Tarih": period, **{code.replace(".", "_"): values[code][index] for code in codes}}
                                     for index, period in enumerate(periods)],
                          "totalCount": len(periods), "seriesNames": {code.replace(".", "_"): code for code in codes}}).encode()

    def fake_success(self, body, timeout):
        return self.response(json.loads(body)["series"].split("-")), {"http_status": 200}

    def test_plan_batches_only_matching_group_frequency_aggregation_and_keeps_archives(self):
        records = []
        for code, group, frequency, aggregation, archive in [
            ("A.1", "one", "AYLIK", "last", False), ("A.2", "one", "AYLIK", "last", True),
            ("A.3", "one", "AYLIK", "avg", False), ("A.4", "two", "AYLIK", "last", False),
            ("A.5", "one", "GÜNLÜK", "last", False),
        ]:
            records.append(dict(series_code=code, group_code=group, frequency=frequency, default_aggregation=aggregation, is_archive=archive))
        result = self.make_plan(records, batch_size=100)
        self.assertEqual(5, result["metadata_series"])
        self.assertEqual(4, result["job_count"])
        again = plan(self.database, self.catalog, date(2025, 1, 1), date(2025, 3, 31), batch_size=100)
        self.assertEqual(result["jobs"], again["jobs"])
        with connect(self.database) as db:
            batches = [json.loads(row[0]) for row in db.execute("SELECT series_codes_json FROM jobs")]
            archived = json.loads(db.execute("SELECT metadata_json FROM series WHERE series_code='A.2'").fetchone()[0])
        self.assertIn(["A.1", "A.2"], batches)
        self.assertTrue(archived["is_archive"])

    def test_plan_filter_and_catalog_identity_are_pinned(self):
        self.make_plan(series_codes=["TEST.A"])
        with self.assertRaisesRegex(ValueError, "pins another"):
            plan(self.database, self.catalog, date(2025, 1, 1), date(2025, 3, 31))
        with self.assertRaisesRegex(ValueError, "Unknown series"):
            plan(self.root / "other.sqlite", self.catalog, series_codes=["MISSING"])
        frame = pd.read_parquet(self.catalog)
        pd.concat([frame, frame]).to_parquet(self.catalog, index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            plan(self.root / "other.sqlite", self.catalog)

    def test_bulk_chunk_bounds_and_no_overlaps_across_arbitrary_long_windows(self):
        for frequency, maximum in [("GÜNLÜK", 900), ("İŞ GÜNÜ", 900), ("HAFTALIK(CUMA)", 6300), ("AYDA İKİ KEZ", 3650), ("AYLIK", 3650), ("YILLIK", 3650)]:
            chunks = bounded_chunks(date(1990, 2, 14), date(2090, 8, 31), frequency)
            self.assertEqual(date(1990, 2, 14), chunks[0][0])
            self.assertEqual(date(2090, 8, 31), chunks[-1][1])
            for index, (start, end) in enumerate(chunks):
                self.assertLessEqual((end - start).days + 1, maximum)
                self.assertLessEqual(start, end)
                if index:
                    self.assertEqual(timedelta(days=1), start - chunks[index - 1][1])
        self.assertEqual(3, len(bounded_chunks(date(2021, 1, 1), date(2026, 6, 30), "GÜNLÜK")))
        self.assertEqual(1, len(bounded_chunks(date(2021, 1, 1), date(2026, 6, 30), "HAFTALIK(CUMA)")))

    def test_request_has_native_frequency_and_one_aggregation_and_formula_per_series(self):
        payload = request_payload(self.job(["A.1", "A.2"]))
        self.assertEqual("A.1-A.2", payload["series"])
        self.assertEqual("last-last", payload["aggregationTypes"])
        self.assertEqual("0-0", payload["formulas"])
        self.assertEqual("5", str(payload["frequency"]))
        for codes in [["A.1", "A_1"], ["A-1"], ["A.1", "A.1"], ["A" + str(i) for i in range(101)]]:
            with self.subTest(codes=codes[:2]), self.assertRaises(ValueError):
                request_payload(self.job(codes))

    def test_mixed_batch_distinguishes_no_data_and_incomplete_numeric_calendar(self):
        raw = self.response(["A.1", "A.2"], values={"A.1": ["1,25", None, 0], "A.2": [None, "", None]})
        rows, result = validate_response(raw, self.job(["A.1", "A.2"]))
        self.assertEqual("succeeded", result["status"])
        self.assertEqual([1.25, None, 0], [row["value"] for row in rows[:3]])
        self.assertEqual([1, 2, 3, 1, 2, 3], [row["source_row_index"] for row in rows])
        self.assertEqual("1,25", rows[0]["value_raw"])
        first, second = result["series_results"].values()
        self.assertEqual("partial_numeric_periods", first["coverage_status"])
        self.assertEqual(["2025-02"], first["missing_numeric_periods"])
        self.assertEqual("no_data", second["status"])
        self.assertTrue(second["physical_present"])
        self.assertEqual(3, second["source_null_count"])

    def test_no_rows_with_exact_series_identity_is_valid_no_data(self):
        rows, result = validate_response(self.response(periods=[], values={"TEST.A": []}), self.job())
        self.assertEqual([], rows)
        self.assertEqual("no_data", result["status"])
        self.assertFalse(result["series_results"]["TEST.A"]["physical_present"])
        self.assertEqual(3, len(result["series_results"]["TEST.A"]["missing_returned_periods"]))

    def test_only_next_native_weekly_boundary_is_excluded_and_retains_original_row_index(self):
        job = self.job(frequency="HAFTALIK(CUMA)", start="2026-06-01", end="2026-06-30")
        raw = self.response(periods=["03-07-2026", "26-06-2026"], values={"TEST.A": [20, 10]})
        rows, result = validate_response(raw, job)
        self.assertEqual(["2026-07-03"], result["excluded_weekly_boundary_periods"])
        self.assertEqual(2, rows[0]["source_row_index"])
        self.assertEqual("calendar_unverified", result["series_results"]["TEST.A"]["coverage_status"])
        for period in ("10-07-2026", "29-05-2026", "02-07-2026"):
            with self.subTest(period=period), self.assertRaises(ValidationError):
                validate_response(self.response(periods=[period], values={"TEST.A": [1]}), job)
        with self.assertRaises(ValidationError):
            validate_response(self.response(periods=["03-07-2026"], values={"TEST.A": ["NaN"]}), job)

    def test_verified_wednesday_response_excludes_both_nearest_boundary_rows(self):
        job = self.job(frequency="HAFTALIK(ÇARŞAMBA)", start="2021-01-01", end="2026-06-30")
        raw = self.response(periods=["30-12-2020", "06-01-2021", "24-06-2026", "01-07-2026"], values={"TEST.A": [1, 2, 3, 4]})
        rows, result = validate_response(raw, job)
        self.assertEqual(["2020-12-30", "2026-07-01"], result["excluded_weekly_boundary_periods"])
        self.assertEqual(["2021-01-06", "2026-06-24"], [row["period"] for row in rows])
        self.assertEqual([2, 3], [row["source_row_index"] for row in rows])
        for period, value in [("23-12-2020", 1), ("08-07-2026", 1), ("31-12-2020", 1), ("30-12-2020", "NaN")]:
            with self.subTest(period=period, value=value), self.assertRaises(ValidationError):
                validate_response(self.response(periods=[period], values={"TEST.A": [value]}), job)

    def test_source_schema_identity_dates_counts_and_nonfinite_cells_fail_closed(self):
        base = json.loads(self.response())
        variants = []
        wrong_count = json.loads(self.response()); wrong_count["totalCount"] = 1000; variants.append(wrong_count)
        wrong_identity = json.loads(self.response()); wrong_identity["seriesNames"] = {"OTHER": "Other"}; variants.append(wrong_identity)
        duplicate_period = json.loads(self.response()); duplicate_period["items"][1]["Tarih"] = "2025-01"; variants.append(duplicate_period)
        missing_cell = json.loads(self.response()); del missing_cell["items"][0]["TEST_A"]; variants.append(missing_cell)
        extra_cell = json.loads(self.response()); extra_cell["items"][0]["OTHER"] = 10; variants.append(extra_cell)
        invalid_date = json.loads(self.response()); invalid_date["items"][0]["Tarih"] = "2025-13"; variants.append(invalid_date)
        for value in (True, {}, [], "NaN", "Infinity", "1e1000", "not-number"):
            variant = json.loads(self.response()); variant["items"][0]["TEST_A"] = value; variants.append(variant)
        for payload in variants:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                validate_response(canonical(payload).encode(), self.job())
        with self.assertRaisesRegex(ValidationError, "Duplicate JSON key"):
            validate_response(b'{"items":[],"items":[]}', self.job())
        with self.assertRaises(ValidationError):
            validate_response(json.dumps(base).replace('10', 'NaN', 1).encode(), self.job())
        full = {**base, "items": base["items"][:1] * 1000, "totalCount": 1000}
        with self.assertRaises(ValidationError) as caught:
            validate_response(canonical(full).encode(), self.job())
        self.assertEqual("ROW_LIMIT", caught.exception.code)

    def test_unix_source_metadata_preserves_int64_and_rejects_lossy_shapes(self):
        payload = json.loads(self.response())
        payload["items"][0]["UNIXTIME"] = {"$numberLong": "9007199254740993"}
        rows, _ = validate_response(canonical(payload).encode(), self.job())
        self.assertEqual(9007199254740993, rows[0]["unix_time"])
        for value in (True, 1.2, "1.2", 2 ** 63, {"number": 1}):
            payload["items"][0]["UNIXTIME"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_response(canonical(payload).encode(), self.job())

    def test_annual_integer_year_matches_verified_public_response_without_accepting_float_or_bool(self):
        job = self.job(frequency="YILLIK", start="2021-01-01", end="2026-06-30")
        rows, result = validate_response(self.response(periods=[2021, 2022], values={"TEST.A": [10, 20]}), job)
        self.assertEqual(["2021", "2022"], [row["period"] for row in rows])
        self.assertEqual("2021", rows[0]["source_date_label"])
        self.assertEqual("2021-12-31", rows[0]["period_end"])
        self.assertEqual("partial_numeric_periods", result["series_results"]["TEST.A"]["coverage_status"])
        for value in (True, 2021.0, 10000, 0):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_response(self.response(periods=[value], values={"TEST.A": [1]}), job)
        with self.assertRaises(ValidationError):
            validate_response(self.response(periods=[2021], values={"TEST.A": [1]}), self.job())

    def test_run_persists_replayable_hashes_typed_rows_and_per_series_status(self):
        self.make_plan()
        with patch("tools.evds_bulk_collection.sha256_bytes", wraps=sha256_bytes) as hashing:
            report = run_jobs(self.database, fetch=self.fake_success, min_interval_seconds=0)
        self.assertEqual(2, hashing.call_count, "Large response hashes must be computed once per attempt, never per cell")
        self.assertEqual(1, report["queue"]["jobs"]["succeeded"])
        with connect(self.database) as db:
            saved = dict(db.execute("SELECT * FROM jobs").fetchone())
            series_result = dict(db.execute("SELECT * FROM series_results").fetchone())
        folder = Path(saved["artifact_path"])
        raw = gzip.decompress((folder / "response.json.gz").read_bytes())
        body = (folder / "request.json").read_bytes()
        info = json.loads((folder / "response_info.json").read_text())
        self.assertEqual(sha256_bytes(raw), info["sha256"])
        self.assertEqual(sha256_bytes(body), info["request_sha256"])
        self.assertEqual(request_payload(saved), json.loads(body))
        frame = pd.read_parquet(folder / "observations.parquet")
        self.assertEqual(list(OBSERVATION_COLUMNS), list(frame))
        self.assertEqual([1, 2, 3], frame.source_row_index.tolist())
        self.assertEqual([info["sha256"]] * 3, frame.source_response_sha256.tolist())
        self.assertEqual("succeeded", series_result["status"])
        self.assertEqual(json.loads(saved["result_json"]), json.loads((folder / "validation.json").read_text()))
        self.assertEqual(0, run_jobs(self.database, fetch=self.fake_success, min_interval_seconds=0)["processed_jobs"])

    def test_empty_valid_response_still_persists_typed_empty_parquet(self):
        self.make_plan()
        fetch = lambda body, timeout: (self.response(periods=[], values={"TEST.A": []}), {"http_status": 200})
        result = run_jobs(self.database, fetch=fetch, min_interval_seconds=0)
        self.assertEqual(1, result["queue"]["jobs"]["no_data"])
        with connect(self.database) as db:
            folder = Path(db.execute("SELECT artifact_path FROM jobs").fetchone()[0])
        frame = pd.read_parquet(folder / "observations.parquet")
        self.assertEqual(0, len(frame))
        self.assertEqual(list(OBSERVATION_COLUMNS), list(frame))
        self.assertEqual("float64", str(frame.value.dtype))

    def test_bad_batch_splits_once_and_isolates_good_series_without_publishing_failure(self):
        records = [dict(series_code=code, group_code="g", frequency="AYLIK", default_aggregation="last") for code in ("TEST.GOOD", "TEST.BAD")]
        self.make_plan(records)
        requested = []
        def fetch(body, timeout):
            codes = json.loads(body)["series"].split("-")
            requested.append(codes)
            values = {code: (["NaN"] * 3 if code == "TEST.BAD" else [10, 20, 30]) for code in codes}
            return self.response(codes, values=values), {"http_status": 200}
        report = run_jobs(self.database, fetch=fetch, drain=True, max_jobs=10, min_interval_seconds=0, retry_base_seconds=0)
        self.assertEqual(3, report["processed_jobs"])
        self.assertEqual(1, report["queue"]["jobs"]["split"])
        self.assertEqual(1, report["queue"]["jobs"]["succeeded"])
        self.assertEqual(1, report["queue"]["jobs"]["permanent_failed"])
        self.assertEqual(1, sum(len(codes) > 1 for codes in requested))
        with connect(self.database) as db:
            failed = db.execute("SELECT artifact_path FROM jobs WHERE status IN ('split','permanent_failed')").fetchall()
        self.assertTrue(all(not (Path(row[0]) / "observations.parquet").exists() for row in failed))

    def test_transient_retry_is_bounded_and_does_not_drop_prior_attempt_artifacts(self):
        self.make_plan()
        calls = []
        def fetch(body, timeout):
            calls.append(body)
            if len(calls) < 3:
                raise TimeoutError("temporary")
            return self.fake_success(body, timeout)
        result = run_jobs(self.database, fetch=fetch, drain=True, max_jobs=10, min_interval_seconds=0, retry_base_seconds=0)
        self.assertEqual(3, result["processed_jobs"])
        self.assertEqual(1, result["queue"]["jobs"]["succeeded"])
        self.assertEqual(3, len(list((self.root / "artifacts").glob("*/attempt-*"))))

    def test_shared_rate_limit_retry_after_and_http_date(self):
        self.make_plan([dict(series_code=code, group_code=code, frequency="AYLIK", default_aggregation="last") for code in ("A", "B")])
        first = claim_job(self.database, now=1000, min_interval_seconds=2)
        self.assertIsNotNone(first)
        self.assertIsNone(claim_job(self.database, now=1001, min_interval_seconds=2))
        finish_job(self.database, first, "retryable_failed", available_after=1100, cooldown_until=1100)
        self.assertIsNone(claim_job(self.database, now=1099, min_interval_seconds=2))
        self.assertIsNotNone(claim_job(self.database, now=1100, min_interval_seconds=2))
        self.assertEqual(120, retry_after_seconds("120", now=1000))
        text = format_datetime(datetime.fromtimestamp(1120, timezone.utc), usegmt=True)
        self.assertEqual(120, retry_after_seconds(text, now=1000))
        self.assertEqual(0, retry_after_seconds("bad", now=1000))
        self.assertEqual(86400, retry_after_seconds("999999999", now=1000))

    def test_http429_sets_durable_global_cooldown_and_does_not_split(self):
        self.make_plan()
        with patch("tools.evds_bulk_collection.time.time", return_value=1000):
            result = run_jobs(self.database, fetch=lambda body, timeout: (b"rate limited", {"http_status": 429, "retry_after": "120"}), min_interval_seconds=0)
        self.assertEqual(1, result["queue"]["jobs"]["retryable_failed"])
        self.assertEqual(0, result["queue"]["jobs"]["split"])
        with connect(self.database) as db:
            self.assertEqual(1120, db.execute("SELECT next_allowed FROM rate_limit").fetchone()[0])
        self.assertIsNone(claim_job(self.database, now=1119, min_interval_seconds=0))
        self.assertIsNotNone(claim_job(self.database, now=1120, min_interval_seconds=0))

    def test_expired_lease_recovers_but_stale_completion_cannot_overwrite(self):
        self.make_plan()
        first = claim_job(self.database, now=1000, lease_seconds=5, min_interval_seconds=0)
        self.assertIsNone(claim_job(self.database, now=1004, min_interval_seconds=0))
        second = claim_job(self.database, now=1005, min_interval_seconds=0)
        self.assertEqual(2, second["attempts"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaisesRegex(ValueError, "Lease ownership changed"):
            finish_job(self.database, first, "succeeded")
        finish_job(self.database, second, "no_data")
        self.assertEqual(1, status_report(self.database)["jobs"]["no_data"])

    def test_lower_retry_budget_terminates_unclaimable_jobs_on_resume(self):
        self.make_plan()
        job = claim_job(self.database, now=1000, min_interval_seconds=0)
        finish_job(self.database, job, "retryable_failed", available_after=1001)
        self.assertIsNone(claim_job(self.database, now=1002, max_attempts=1, min_interval_seconds=0))
        self.assertEqual(1, status_report(self.database)["jobs"]["permanent_failed"])
        with connect(self.database) as db:
            self.assertEqual("permanent_failed", db.execute("SELECT status FROM series_results").fetchone()[0])

    def test_global_service_unavailable_does_not_expand_into_series_children(self):
        self.make_plan([dict(series_code=code, group_code="g", frequency="AYLIK", default_aggregation="last") for code in ("A", "B")])
        result = run_jobs(self.database, fetch=lambda body, timeout: (b"unavailable", {"http_status": 503}),
                          max_jobs=10, drain=True, min_interval_seconds=0, retry_base_seconds=0, max_attempts=2)
        self.assertEqual(2, result["processed_jobs"])
        self.assertEqual(1, result["queue"]["job_count"])
        self.assertEqual(1, result["queue"]["jobs"]["permanent_failed"])

    def test_invalid_budget_cannot_create_unbounded_drain(self):
        self.make_plan()
        for kwargs in ({"max_seconds": float("nan")}, {"max_seconds": float("inf")},
                       {"retry_base_seconds": float("nan")}, {"min_interval_seconds": float("nan")},
                       {"max_attempts": True}, {"timeout": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_jobs(self.database, fetch=self.fake_success, **kwargs)

    def test_parallel_workers_share_one_global_claim_slot(self):
        self.make_plan([dict(series_code=code, group_code=code, frequency="AYLIK", default_aggregation="last") for code in ("A", "B", "C")])
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: claim_job(self.database, now=1000, min_interval_seconds=1), range(6)))
        claimed = [result for result in results if result is not None]
        self.assertEqual(1, len(claimed))
        next_job = claim_job(self.database, now=1001, min_interval_seconds=1)
        self.assertIsNotNone(next_job)
        self.assertNotEqual(claimed[0]["job_id"], next_job["job_id"])
        self.assertEqual(2, status_report(self.database)["attempted_requests"])

    def test_sparse_item_cells_require_complete_transposed_evidence(self):
        # Observed live for TSANAYMT2021: seriesNames lists an all-null series,
        # transposedItems renders its nulls, but items omits every source cell.
        # An incomplete second representation cannot justify any fallback.
        payload = json.loads(self.response(["A", "B"]))
        for row in payload["items"]:
            del row["B"]
        payload["transposedItems"] = [{"serieCode": "B", "serieName": "B", "2025-01": None, "2025-02": None, "2025-03": None}]
        with self.assertRaises(ValidationError) as caught:
            validate_response(canonical(payload).encode(), self.job(["A", "B"]))
        self.assertEqual("INVALID_TRANSPOSE_NULL_EVIDENCE", caught.exception.code)

    def sparse_payload(self):
        payload = json.loads(self.response(["A", "B"], values={"A": [10, 20, 30], "B": [None, None, None]}))
        payload["transposedColumns"] = [{"key": "serieName", "title": ""}] + [{"key": item["Tarih"], "title": item["Tarih"]} for item in payload["items"]]
        payload["transposedItems"] = [
            {"serieCode": code, "serieName": code, **{item["Tarih"]: item[code] for item in payload["items"]}}
            for code in ("B", "A")
        ]
        for item in payload["items"]:
            del item["B"]
        return payload

    def test_complete_explicit_null_transpose_cells_have_authoritative_value_pointers(self):
        payload = self.sparse_payload()
        rows, result = validate_response(canonical(payload).encode(), self.job(["A", "B"]))
        self.assertEqual(6, len(rows))
        self.assertEqual([1, 2, 3], [row["source_row_index"] for row in rows[3:]])
        self.assertEqual("/items/0/A", rows[0]["source_cell_path"])
        self.assertEqual("/transposedItems/0/2025-01", rows[3]["source_cell_path"])
        for row in rows:
            source = payload
            for part in row["source_cell_path"].split("/")[1:]:
                source = source[int(part)] if isinstance(source, list) else source[part.replace("~1", "/").replace("~0", "~")]
            self.assertEqual(source, row["value"])
        self.assertEqual("no_data", result["series_results"]["B"]["status"])
        self.assertEqual(3, result["series_results"]["B"]["source_null_count"])
        self.assertTrue(result["series_results"]["B"]["physical_present"])
        self.assertEqual(["B"], result["transposed_null_fallback"]["series_codes"])
        self.assertEqual(3, result["transposed_null_fallback"]["cell_count"])
        _, normal = validate_response(self.response(), self.job())
        self.assertNotIn("transposed_null_fallback", normal)

    def test_null_transpose_fallback_rejects_incomplete_conflicting_or_nonnull_evidence(self):
        variants = []
        for bad_value in (0, "", "null", "NaN", False, {}):
            payload = self.sparse_payload(); payload["transposedItems"][0]["2025-01"] = bad_value; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedItems"][1]["2025-01"] = "10"; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedItems"][0]["serieName"] = "different"; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedItems"][0]["serieCode"] = "A"; variants.append(payload)
        payload = self.sparse_payload(); del payload["transposedItems"][0]["2025-01"]; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedItems"][0]["2025-04"] = None; variants.append(payload)
        payload = self.sparse_payload(); payload["items"][0]["B"] = None; variants.append(payload)
        payload = self.sparse_payload(); payload["items"].pop(); payload["totalCount"] -= 1; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedColumns"][1]["title"] = "another month"; variants.append(payload)
        payload = self.sparse_payload(); payload["transposedColumns"].pop(); variants.append(payload)
        payload = self.sparse_payload(); payload["transposedColumns"][1] = payload["transposedColumns"][2]; variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                validate_response(canonical(payload).encode(), self.job(["A", "B"]))
        with self.assertRaises(ValidationError):
            validate_response(canonical(self.sparse_payload()).encode(), self.job(["A", "B"], frequency="ÜÇ AYLIK"))

    def make_split_batch(self):
        self.make_plan([dict(series_code=code, group_code="group", frequency="AYLIK", default_aggregation="last") for code in ("A", "B")])
        raw = canonical(self.sparse_payload()).encode()
        with patch("tools.evds_bulk_collection.validate_response", side_effect=ValidationError("Previous parser did not support this explicit representation")):
            run_jobs(self.database, fetch=lambda body, timeout: (raw, {"http_status": 200}), min_interval_seconds=0)
        with connect(self.database) as db:
            return dict(db.execute("SELECT * FROM jobs WHERE parent_job_id IS NULL").fetchone())

    def test_validated_batch_retry_preserves_descendant_history_and_requires_fresh_attempt(self):
        parent = self.make_split_batch()
        child_run = run_jobs(self.database, fetch=self.fake_success, min_interval_seconds=0)
        self.assertEqual(1, child_run["queue"]["jobs"]["succeeded"])
        with connect(self.database) as db:
            before = {row["job_id"]: dict(row) for row in db.execute("SELECT * FROM jobs WHERE parent_job_id=?", (parent["job_id"],))}
        original_request = (Path(parent["artifact_path"]) / "request.json").read_bytes()
        original_validation = (Path(parent["artifact_path"]) / "validation.json").read_bytes()
        repaired = retry_validated_batch(self.database, parent["job_id"])
        self.assertEqual("queued", repaired["status"])
        self.assertEqual(2, repaired["next_attempt"])
        self.assertEqual(6, repaired["replay_row_count"])
        self.assertEqual(set(before), set(repaired["superseded_job_ids"]))
        with connect(self.database) as db:
            replacement = dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (parent["job_id"],)).fetchone())
            for row in db.execute("SELECT * FROM job_supersessions"):
                self.assertEqual(before[row["job_id"]], json.loads(row["previous_job_json"]))
                self.assertEqual(parent["job_id"], row["replacement_job_id"])
                self.assertTrue(row["reason"])
            for job_id, previous in before.items():
                after = dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
                self.assertEqual("superseded", after["status"])
                for key in ("attempts", "artifact_path", "result_json", "last_error"):
                    self.assertEqual(previous[key], after[key])
            self.assertEqual({"superseded"}, {row[0] for row in db.execute("SELECT status FROM series_results WHERE job_id!=?", (parent["job_id"],))})
        self.assertEqual(1, replacement["attempts"])
        self.assertEqual("retryable_failed", replacement["status"])
        self.assertEqual(parent["result_json"], replacement["result_json"])
        self.assertEqual(original_validation, (Path(parent["artifact_path"]) / "validation.json").read_bytes())
        bodies = []
        def fetch(body, timeout):
            bodies.append(body)
            return canonical(self.sparse_payload()).encode(), {"http_status": 200}
        done = run_jobs(self.database, fetch=fetch, min_interval_seconds=0)
        self.assertEqual([original_request], bodies)
        self.assertEqual(1, done["queue"]["jobs"]["succeeded"])
        self.assertEqual(2, done["queue"]["jobs"]["superseded"])
        with connect(self.database) as db:
            final = dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (parent["job_id"],)).fetchone())
        self.assertEqual(2, final["attempts"])
        self.assertNotEqual(parent["artifact_path"], final["artifact_path"])
        self.assertEqual(original_validation, (Path(parent["artifact_path"]) / "validation.json").read_bytes())
        frame = pd.read_parquet(Path(final["artifact_path"]) / "observations.parquet")
        self.assertTrue(frame.source_cell_path.str.startswith("/").all())

    def test_validated_batch_retry_rejects_running_descendant_atomically(self):
        parent = self.make_split_batch()
        running = claim_job(self.database, min_interval_seconds=0)
        self.assertIsNotNone(running)
        with self.assertRaisesRegex(ValueError, "running"):
            retry_validated_batch(self.database, parent["job_id"])
        with connect(self.database) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM job_supersessions").fetchone()[0])
            self.assertEqual("split", db.execute("SELECT status FROM jobs WHERE job_id=?", (parent["job_id"],)).fetchone()[0])
            self.assertEqual("running", db.execute("SELECT status FROM jobs WHERE job_id=?", (running["job_id"],)).fetchone()[0])

    def test_validated_batch_retry_rejects_descendant_scope_escape(self):
        parent = self.make_split_batch()
        with connect(self.database) as db:
            db.execute("UPDATE jobs SET start_date='2024-12-31' WHERE parent_job_id=?", (parent["job_id"],))
        with self.assertRaisesRegex(ValueError, "date scope escapes"):
            retry_validated_batch(self.database, parent["job_id"])
        with connect(self.database) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM job_supersessions").fetchone()[0])
            db.execute("UPDATE jobs SET start_date='2025-01-01',aggregation='sum' WHERE parent_job_id=?", (parent["job_id"],))
        with self.assertRaisesRegex(ValueError, "native scope escapes"):
            retry_validated_batch(self.database, parent["job_id"])

    def test_validated_batch_retry_checks_exact_raw_and_request_proof(self):
        parent = self.make_split_batch()
        artifact = Path(parent["artifact_path"])
        info = json.loads((artifact / "response_info.json").read_text())
        (artifact / "response.json.gz").write_bytes(gzip.compress(b"changed"))
        with self.assertRaisesRegex(ValueError, "hash proof"):
            retry_validated_batch(self.database, parent["job_id"])
        raw = canonical(self.sparse_payload()).encode()
        (artifact / "response.json.gz").write_bytes(gzip.compress(raw))
        body = json.loads((artifact / "request.json").read_bytes()); body["startDate"] = "01-02-2025"
        changed = canonical(body).encode(); (artifact / "request.json").write_bytes(changed)
        info["request_sha256"] = sha256_bytes(changed)
        (artifact / "response_info.json").write_text(canonical(info))
        with self.assertRaisesRegex(ValueError, "exact replacement"):
            retry_validated_batch(self.database, parent["job_id"])
        with connect(self.database) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM job_supersessions").fetchone()[0])

    def test_validated_batch_retry_cannot_reset_exhausted_attempt_budget(self):
        parent = self.make_split_batch()
        with self.assertRaisesRegex(ValueError, "attempt budget"):
            retry_validated_batch(self.database, parent["job_id"], max_attempts=1)
        with connect(self.database) as db:
            self.assertEqual(1, db.execute("SELECT attempts FROM jobs WHERE job_id=?", (parent["job_id"],)).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM job_supersessions").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
