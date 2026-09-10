"""Finalization uses real local queue/publication proofs and isolated DB copies.

Only the heavyweight multi-source catalog/lakehouse builders are substituted.
The acquisition, response replay, publication inventory and active-file copy
remain real; no network call or repository source database is used.
"""
from contextlib import ExitStack
from datetime import date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from tools.complete_evds_history import refresh
from tools.evds_bulk_collection import plan, run_jobs
from tools.EVDS_Manifest_Indirme_Araci import expected_periods
from tools.publish_evds_bulk import publish as real_publish, resolve_publication


class CompleteEvdsHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="evds-completion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue = self.root / "queue.sqlite"
        self.catalog = self.root / "catalog.parquet"
        self.package = self.root / "package"
        self.runtime = self.root / "runtime"
        self.active = self.root / "serving.duckdb"
        with duckdb.connect(str(self.active)) as connection:
            connection.execute("CREATE TABLE previous_release AS SELECT 'preserve me' AS marker")
        self.old_bytes = self.active.read_bytes()
        records = [{"series_code": code, "series_name_tr": code, "group_code": "fixture",
                    "frequency": "AYLIK", "default_aggregation": "last", "unit": "Kaynak birimi",
                    "is_archive": False} for code in ("TEST.NUMERIC", "TEST.NULL")]
        pd.DataFrame(records).to_parquet(self.catalog, index=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("tools.complete_evds_history.emit"))
        self.publisher = self.stack.enter_context(patch("tools.publish_evds_bulk.publish", side_effect=self.publish_local))
        self.catalog_builder = self.stack.enter_context(patch("data_pipeline.catalog.build_unified_catalog.build", side_effect=self.build_catalog))
        self.database_builder = self.stack.enter_context(patch("data_pipeline.lakehouse.build_lakehouse.build", side_effect=self.build_database))

    def create_queue(self, *, filtered=False, start=date(2021, 1, 1), end=date(2026, 6, 30)):
        plan(self.queue, self.catalog, start, end, batch_size=2,
             series_codes=["TEST.NUMERIC"] if filtered else None)

    def collect(self):
        def fetch(body, timeout):
            request = json.loads(body)
            codes = request["series"].split("-")
            first = datetime.strptime(request["startDate"], "%d-%m-%Y").date()
            last = datetime.strptime(request["endDate"], "%d-%m-%Y").date()
            periods = expected_periods(first, last, "AYLIK")
            items = [{"Tarih": period, **{code.replace(".", "_"): None if code == "TEST.NULL" else index + 100
                      for code in codes}} for index, (period, _, _) in enumerate(periods)]
            return json.dumps({"items": items, "totalCount": len(items),
                               "seriesNames": {code.replace(".", "_"): code for code in codes}}).encode(), {"http_status": 200}
        result = run_jobs(self.queue, output=self.root / "attempts", max_jobs=10,
                          min_interval_seconds=0, fetch=fetch)
        self.assertEqual(0, result["queue"]["jobs"]["pending"])

    def publish_local(self, *, database):
        return real_publish(database, self.package, self.catalog)

    def build_catalog(self, output):
        _, manifest = resolve_publication(self.package)
        output.mkdir(parents=True, exist_ok=True)
        return {"status": "passed", "queryable_metric_count": 2,
                "evds_full_catalog": {"publication_id": manifest["publication_id"]}}

    def build_database(self, output, *, catalog_dir):
        release, manifest = resolve_publication(self.package)
        output.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(output)) as connection:
            connection.read_parquet(str(release / "observations_long.parquet")).create("observations")
        (output.parent / "validation.json").write_text(json.dumps({"status": "passed", "publication_id": manifest["publication_id"]}))
        return {"status": "passed"}

    def refresh(self):
        return refresh(self.queue, output=self.active, runtime_root=self.runtime)

    def assert_previous_database(self):
        self.assertEqual(self.old_bytes, self.active.read_bytes())
        self.assertFalse(list(self.active.parent.glob(".serving.duckdb.*.tmp")))

    def test_pending_running_retryable_and_permanent_failures_block_before_publication(self):
        self.create_queue()
        for state in ("pending", "running", "retryable_failed", "permanent_failed"):
            with self.subTest(state=state):
                with sqlite3.connect(self.queue) as connection:
                    connection.execute("UPDATE jobs SET status=?", (state,))
                with self.assertRaisesRegex(ValueError, "acquisition is not complete"):
                    self.refresh()
                self.assert_previous_database()
                self.publisher.assert_not_called()
                self.catalog_builder.assert_not_called()
                self.database_builder.assert_not_called()
        self.assertFalse(self.package.exists())

    def test_filtered_but_drained_queue_cannot_replace_the_serving_database(self):
        self.create_queue(filtered=True)
        self.collect()
        with self.assertRaisesRegex(ValueError, "filtered catalog scope"):
            self.refresh()
        self.assert_previous_database()
        self.publisher.assert_not_called()

    def test_full_catalog_with_wrong_target_window_is_rejected_before_publication(self):
        self.create_queue(start=date(2025, 1, 1), end=date(2025, 3, 31))
        self.collect()
        with self.assertRaises(ValueError):
            self.refresh()
        self.assert_previous_database()
        self.publisher.assert_not_called()

    def test_drained_jobs_with_missing_request_interval_fail_real_publication_gate(self):
        self.create_queue()
        # All planned jobs can be terminal while the target interval is not
        # covered, for example after a damaged or incomplete job plan.
        with sqlite3.connect(self.queue) as connection:
            connection.execute("UPDATE jobs SET end_date='2021-01-31'")
        self.collect()
        with self.assertRaisesRegex(ValueError, "request intervals"):
            self.refresh()
        self.assert_previous_database()
        self.catalog_builder.assert_not_called()
        _, manifest = resolve_publication(self.package)
        self.assertFalse(manifest["validation"]["full_request_scope_complete"])

    def test_tampered_raw_source_stops_before_build_or_active_replace(self):
        self.create_queue()
        self.collect()
        response = next((self.root / "attempts").rglob("response.json.gz"))
        response.write_bytes(gzip.compress(b'{"tampered":true}'))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.refresh()
        self.assert_previous_database()
        self.catalog_builder.assert_not_called()
        self.database_builder.assert_not_called()

    def test_failed_build_preserves_previous_database_and_completion_pointer(self):
        self.create_queue()
        self.collect()
        self.runtime.mkdir()
        previous_report = b'{"publication_id":"previous"}\n'
        (self.runtime / "LATEST.json").write_bytes(previous_report)
        def fail_build(output, *, catalog_dir):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"partial failed database")
            raise RuntimeError("Source quality check failed")
        self.database_builder.side_effect = fail_build
        with self.assertRaisesRegex(RuntimeError, "Source quality check failed"):
            self.refresh()
        self.assert_previous_database()
        self.assertEqual(previous_report, (self.runtime / "LATEST.json").read_bytes())

    def test_concurrently_advanced_catalog_release_is_rejected(self):
        self.create_queue()
        self.collect()
        self.catalog_builder.side_effect = lambda _: {"evds_full_catalog": {"publication_id": "another-release"}, "queryable_metric_count": 2}
        with self.assertRaisesRegex(ValueError, "different concurrently published"):
            self.refresh()
        self.assert_previous_database()
        self.database_builder.assert_not_called()

    def test_verified_copy_failure_preserves_previous_database(self):
        self.create_queue()
        self.collect()
        with patch("tools.complete_evds_history.shutil.copyfileobj", side_effect=lambda source, destination, **kwargs: destination.write(b"corrupted-copy")):
            with self.assertRaisesRegex(ValueError, "SHA256 check"):
                self.refresh()
        self.assert_previous_database()
        self.assertFalse((self.runtime / "LATEST.json").exists())

    def test_active_writer_wal_blocks_replace(self):
        self.create_queue()
        self.collect()
        Path(str(self.active) + ".wal").write_bytes(b"active-writer-proof")
        with self.assertRaisesRegex(ValueError, "active WAL"):
            self.refresh()
        self.assert_previous_database()

    def test_complete_verified_requests_publish_without_claiming_numeric_completeness(self):
        self.create_queue()
        self.collect()
        report = self.refresh()
        self.assertNotEqual(self.old_bytes, self.active.read_bytes())
        self.assertEqual("completed", report["status"])
        self.assertTrue(report["acquisition_scope_complete"])
        self.assertTrue(report["numeric_coverage_is_separate"])
        self.assertFalse(report["source_validation"]["full_numeric_coverage_complete"])
        self.assertEqual(2, report["source_validation"]["completed_request_series"])
        self.assertEqual(hashlib.sha256(self.active.read_bytes()).hexdigest(), report["serving_database_sha256"])
        with duckdb.connect(str(self.active), read_only=True) as connection:
            self.assertEqual((132, 66), connection.execute("SELECT count(*),count(value) FROM observations").fetchone())
            self.assertEqual(("2021-01", "2026-06"), connection.execute("SELECT min(period),max(period) FROM observations").fetchone())
        self.assertEqual(report, json.loads((self.runtime / "LATEST.json").read_text()))
        self.assertEqual(report, json.loads((self.runtime / report["publication_id"] / "completion.json").read_text()))
        self.assertFalse(list(self.active.parent.glob(".serving.duckdb.*.tmp")))


if __name__ == "__main__":
    unittest.main()
