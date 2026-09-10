import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from tools.publish_evds_bulk import OBSERVATION_SCHEMA, publish, resolve_publication


class PublishEvdsBulkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="evds-publish-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "queue.sqlite"
        self.catalog = self.root / "catalog.parquet"
        self.output = self.root / "published"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()

    def plan(self, codes=("TEST.A",), extra_catalog_codes=(), frequency="AYLIK"):
        records = [{"series_code": code, "series_name_tr": code, "frequency": frequency, "group_code": "fixture",
                    "default_aggregation": "last", "unit": "Kaynak birimi", "is_archive": False}
                   for code in (*codes, *extra_catalog_codes)]
        pd.DataFrame(records).to_parquet(self.catalog, index=False)
        with sqlite3.connect(self.database) as connection:
            connection.executescript("""
                CREATE TABLE configuration (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE series (series_code TEXT PRIMARY KEY,metadata_json TEXT);
                CREATE TABLE jobs (job_id TEXT PRIMARY KEY,series_codes_json TEXT,group_code TEXT,frequency TEXT,
                    aggregation TEXT,start_date TEXT,end_date TEXT,status TEXT,attempts INTEGER,
                    artifact_path TEXT,result_json TEXT);
            """)
            connection.executemany("INSERT INTO configuration VALUES (?,?)", {
                "schema_version": "evds_bulk_v1", "target_start": "2025-01-01", "target_end": "2025-03-31",
                "catalog_sha256": hashlib.sha256(self.catalog.read_bytes()).hexdigest(), "batch_size": "20",
            }.items())
            connection.executemany("INSERT INTO series VALUES (?,?)", [(row["series_code"], json.dumps(row))
                                                                       for row in records if row["series_code"] in codes])

    def add_job(self, values, *, job_id="job-1", periods=None, start="2025-01-01", end="2025-03-31",
                pending=False, attempts=None, transposed_null_codes=()):
        from tools.evds_bulk_collection import request_payload, validate_response

        codes = list(values)
        with sqlite3.connect(self.database) as connection:
            frequency = json.loads(connection.execute("SELECT metadata_json FROM series WHERE series_code=?", (codes[0],)).fetchone()[0])["frequency"]
        job = {"job_id": job_id, "series_codes_json": json.dumps(codes), "group_code": "fixture", "frequency": frequency,
               "aggregation": "last", "start_date": start, "end_date": end,
               "status": "pending", "attempts": attempts if attempts is not None else int(not pending),
               "artifact_path": None, "result_json": None}
        if not pending:
            periods = periods if periods is not None else ["2025-01", "2025-02", "2025-03"]
            body = json.dumps(request_payload(job), ensure_ascii=False).encode()
            payload = {"items": [{"Tarih": period, **{code.replace(".", "_"): values[code][index]
                 for code in codes if code not in transposed_null_codes}} for index, period in enumerate(periods)],
                 "totalCount": len(periods), "seriesNames": {code.replace(".", "_"): code for code in codes}}
            if transposed_null_codes:
                payload["transposedItems"] = [{"serieCode": code.replace(".", "_"), "serieName": code,
                     **{period: values[code][index] for index, period in enumerate(periods)}} for code in codes]
                payload["transposedColumns"] = [{"key": "serieName", "title": ""}] + [{"key": p, "title": p} for p in reversed(periods)]
            raw = json.dumps(payload).encode()
            rows, result = validate_response(raw, job)
            job["status"] = "succeeded" if any(row["value"] is not None for row in rows) else "no_data"
            result["status"] = job["status"]
            destination = self.artifacts / job_id
            destination.mkdir()
            job["artifact_path"] = str(destination)
            job["result_json"] = json.dumps(result)
            (destination / "request.json").write_bytes(body)
            (destination / "response.json.gz").write_bytes(gzip.compress(raw, mtime=0))
            (destination / "response_info.json").write_text(json.dumps({"sha256": hashlib.sha256(raw).hexdigest(),
                "request_sha256": hashlib.sha256(body).hexdigest(), "http_status": 200, "transport": "urllib"}))
            (destination / "validation.json").write_text(json.dumps(result))
            for row in rows:
                row.update(native_frequency=frequency, missing_kind="observed" if row["value"] is not None else "source_null_unresolved",
                           is_unresolved_missing=row["value"] is None,
                           source_request_file=str(destination / "request.json"), source_request_sha256=hashlib.sha256(body).hexdigest(),
                           source_response_file=str(destination / "response.json.gz"), source_response_sha256=hashlib.sha256(raw).hexdigest(),
                           source_job_id=job_id, source_attempt=job["attempts"])
            pq.write_table(pa.Table.from_pylist(rows, schema=OBSERVATION_SCHEMA), destination / "observations.parquet")
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(job.values()))
        return self.artifacts / job_id

    def supersede(self, job_id, replacement_job_id):
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            previous = dict(connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
            connection.execute("CREATE TABLE IF NOT EXISTS job_supersessions(job_id TEXT PRIMARY KEY,replacement_job_id TEXT,previous_job_json TEXT,reason TEXT,created_at REAL)")
            connection.execute("INSERT INTO job_supersessions VALUES (?,?,?,?,?)", (job_id, replacement_job_id, json.dumps(previous), "Explicit full-batch replacement after source representation validation", 1))
            connection.execute("UPDATE jobs SET status='superseded' WHERE job_id=?", (job_id,))

    def publish(self):
        return publish(self.database, self.output, self.catalog)

    def frames(self):
        release, manifest = resolve_publication(self.output)
        return release, manifest, pd.read_parquet(release / "coverage.parquet").set_index("series_code")

    def test_numeric_and_no_data_members_stay_distinct_and_release_is_portable(self):
        self.plan(("TEST.A", "TEST.EMPTY"))
        self.add_job({"TEST.A": [10, 20, 30], "TEST.EMPTY": [None, None, None]})
        result = self.publish()
        release, manifest, coverage = self.frames()
        self.assertTrue(result["validation"]["full_request_scope_complete"])
        self.assertFalse(result["validation"]["full_numeric_coverage_complete"])
        self.assertEqual(2, result["validation"]["physical_series"])
        self.assertEqual(1, result["validation"]["numeric_series"])
        self.assertEqual("no_target_numeric_data", coverage.loc["TEST.EMPTY", "coverage_status"])
        self.assertEqual(1, coverage.loc["TEST.EMPTY", "no_data_job_count"])
        frame = pd.read_parquet(release / "observations_long.parquet")
        empty = frame[frame.series_code == "TEST.EMPTY"]
        self.assertEqual({"source_null_unresolved"}, set(empty.missing_kind))
        self.assertTrue(empty.is_unresolved_missing.all())
        self.assertTrue(all(not Path(value).is_absolute() for value in frame.source_response_file))
        shutil.rmtree(self.artifacts)
        resolve_publication(self.output)
        for row in frame.itertuples():
            source = release / row.source_response_file
            self.assertEqual(row.source_response_sha256, hashlib.sha256(gzip.decompress(source.read_bytes())).hexdigest())
        self.assertEqual("decompressed_response_bytes", manifest["provenance_hash_basis"]["source_response_sha256"])

    def test_publish_is_idempotent_and_source_queue_is_unchanged(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]})
        before = self.database.read_bytes()
        first = self.publish()
        pointer = (self.output / "CURRENT.json").read_bytes()
        second = self.publish()
        self.assertEqual(first, second)
        self.assertEqual(pointer, (self.output / "CURRENT.json").read_bytes())
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(1, len(list((self.output / "releases").iterdir())))

    def test_raw_hash_tamper_does_not_replace_the_current_release(self):
        self.plan()
        artifact = self.add_job({"TEST.A": [10, 20, 30]})
        self.publish()
        pointer = (self.output / "CURRENT.json").read_bytes()
        (artifact / "response.json.gz").write_bytes(gzip.compress(b'{"changed":true}'))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.publish()
        self.assertEqual(pointer, (self.output / "CURRENT.json").read_bytes())
        resolve_publication(self.output)
        self.assertFalse(list(self.output.glob(".staging-*")))

    def test_changed_parquet_value_is_rejected_by_raw_replay(self):
        self.plan()
        artifact = self.add_job({"TEST.A": [10, 20, 30]})
        frame = pd.read_parquet(artifact / "observations.parquet")
        frame.loc[0, "value"] = 999
        frame.to_parquet(artifact / "observations.parquet", index=False)
        with self.assertRaisesRegex(ValueError, "Parquet observations differ"):
            self.publish()
        self.assertFalse((self.output / "CURRENT.json").exists())

    def test_request_membership_is_checked_even_when_request_hash_is_updated(self):
        self.plan()
        artifact = self.add_job({"TEST.A": [10, 20, 30]})
        request = json.loads((artifact / "request.json").read_text())
        request["series"] = "TEST.WRONG"
        body = json.dumps(request).encode()
        (artifact / "request.json").write_bytes(body)
        info = json.loads((artifact / "response_info.json").read_text())
        info["request_sha256"] = hashlib.sha256(body).hexdigest()
        (artifact / "response_info.json").write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError, "Request does not match"):
            self.publish()

    def test_conflicting_vintages_are_preserved_and_neither_value_is_chosen(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]}, job_id="old")
        self.add_job({"TEST.A": [99, 20, 30]}, job_id="new")
        result = self.publish()
        release, _, coverage = self.frames()
        canonical = pd.read_parquet(release / "observations_long.parquet")
        self.assertEqual(["2025-02", "2025-03"], canonical.period.tolist())
        self.assertEqual(6, len(pd.read_parquet(release / "observations_vintages.parquet")))
        self.assertEqual(1, len(pd.read_parquet(release / "conflicts.parquet")))
        self.assertEqual("conflicting_vintages", coverage.loc["TEST.A", "coverage_status"])
        self.assertTrue(result["validation"]["full_request_scope_complete"])
        self.assertFalse(result["validation"]["evds_full_observation_coverage_complete"])

    def test_identical_duplicates_collapse_but_keep_all_source_evidence(self):
        self.plan()
        for job_id in ("one", "two"):
            self.add_job({"TEST.A": [10, 20, 30]}, job_id=job_id)
        result = self.publish()
        release, _, coverage = self.frames()
        self.assertEqual(3, result["validation"]["observation_count"])
        self.assertEqual(6, len(pd.read_parquet(release / "observations_vintages.parquet")))
        self.assertEqual(0, coverage.loc["TEST.A", "conflicting_period_count"])
        self.assertTrue(result["validation"]["evds_full_observation_coverage_complete"])

    def test_unique_rows_and_duplicate_winners_keep_their_exact_source_evidence(self):
        self.plan(("TEST.A", "TEST.UNIQUE"))
        self.add_job({"TEST.A": [10, 20, 30], "TEST.UNIQUE": [None, 0, 50]}, job_id="first")
        self.add_job({"TEST.A": ["10.00", "20.000", 99]}, job_id="second")
        self.publish()
        release, _, _ = self.frames()
        canonical = pd.read_parquet(release / "observations_long.parquet")
        vintages = pd.read_parquet(release / "observations_vintages.parquet")
        self.assertEqual(5, len(canonical))
        self.assertEqual(9, len(vintages))
        unique = canonical[canonical.series_code == "TEST.UNIQUE"]
        self.assertTrue(pd.isna(unique.iloc[0].value))
        self.assertEqual([0, 50], unique.iloc[1:].value.tolist())
        self.assertEqual({"first"}, set(unique.source_job_id))
        for period in ("2025-01", "2025-02"):
            candidates = vintages[(vintages.series_code == "TEST.A") & (vintages.period == period)]
            expected = candidates.sort_values(["source_response_sha256", "source_job_id", "source_attempt"]).iloc[0]
            actual = canonical[(canonical.series_code == "TEST.A") & (canonical.period == period)].iloc[0]
            pd.testing.assert_series_equal(expected, actual, check_names=False)
        conflict = pd.read_parquet(release / "conflicts.parquet").iloc[0]
        self.assertEqual(("TEST.A", "2025-03", 2, 2), tuple(conflict))

    def test_unrequested_interval_and_attempted_retry_remain_visible(self):
        self.plan()
        self.add_job({"TEST.A": [10]}, periods=["2025-01"], end="2025-01-31")
        self.add_job({"TEST.A": []}, job_id="pending", start="2025-02-01", pending=True, attempts=1)
        result = self.publish()
        _, _, coverage = self.frames()
        row = coverage.loc["TEST.A"]
        self.assertFalse(row.request_coverage_complete)
        self.assertEqual(31, row.covered_request_day_count)
        self.assertEqual([{"start": "2025-02-01", "end": "2025-03-31"}], json.loads(row.request_gap_intervals_json))
        self.assertEqual(2, row.missing_numeric_period_count)
        self.assertEqual(1, row.pending_job_count)
        self.assertEqual(2, row.attempted_job_count)
        self.assertEqual(2, result["validation"]["attempted_job_count"])

    def test_completed_request_with_missing_months_is_not_numeric_completion(self):
        self.plan()
        self.add_job({"TEST.A": [10]}, periods=["2025-01"])
        self.publish()
        _, _, coverage = self.frames()
        row = coverage.loc["TEST.A"]
        self.assertTrue(row.request_coverage_complete)
        self.assertFalse(row.numeric_coverage_complete)
        self.assertEqual(2, row.missing_returned_period_count)

    def test_empty_response_keeps_catalog_entry_and_request_proof(self):
        self.plan()
        self.add_job({"TEST.A": []}, periods=[])
        result = self.publish()
        release, _, coverage = self.frames()
        self.assertEqual(0, result["validation"]["physical_series"])
        self.assertTrue(coverage.loc["TEST.A", "request_coverage_complete"])
        self.assertFalse(coverage.loc["TEST.A", "numeric_coverage_complete"])
        self.assertEqual(1, len(pd.read_parquet(release / "analysis_series_catalog.parquet")))
        self.assertEqual(1, len(pd.read_parquet(release / "request_coverage.parquet")))

    def test_subset_of_catalog_cannot_claim_full_universe_completion(self):
        self.plan(extra_catalog_codes=("TEST.UNREQUESTED",))
        self.add_job({"TEST.A": [10, 20, 30]})
        result = self.publish()["validation"]
        self.assertTrue(result["queue_request_scope_complete"])
        self.assertFalse(result["metadata_scope_complete"])
        self.assertFalse(result["full_request_scope_complete"])
        self.assertFalse(result["evds_full_observation_coverage_complete"])

    def test_old_release_remains_resolvable_after_new_current_publication(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]})
        first = self.publish()
        self.add_job({"TEST.A": [11, 20, 30]}, job_id="revision")
        second = self.publish()
        self.assertNotEqual(first["publication_id"], second["publication_id"])
        old, _ = resolve_publication(self.output, publication_id=first["publication_id"], manifest_sha256=first["manifest_sha256"])
        self.assertEqual(3, len(pd.read_parquet(old / "observations_long.parquet")))

    def test_release_inventory_detects_changed_files(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]})
        result = self.publish()
        release = Path(result["release_path"])
        (release / "coverage.parquet").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "file hash mismatch"):
            resolve_publication(self.output)

    def test_source_null_and_numeric_vintages_are_not_silently_coalesced(self):
        self.plan()
        self.add_job({"TEST.A": [None, 20, 30]}, job_id="source-null")
        self.add_job({"TEST.A": [10, 20, 30]}, job_id="source-numeric")
        self.publish()
        release, _, coverage = self.frames()
        self.assertEqual(1, coverage.loc["TEST.A", "conflicting_period_count"])
        self.assertNotIn("2025-01", set(pd.read_parquet(release / "observations_long.parquet").period))

    def test_changed_queue_unit_is_rejected_against_pinned_source_metadata(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]})
        with sqlite3.connect(self.database) as connection:
            record = json.loads(connection.execute("SELECT metadata_json FROM series").fetchone()[0])
            record["unit"] = "Invented percent unit"
            connection.execute("UPDATE series SET metadata_json=?", (json.dumps(record),))
        with self.assertRaisesRegex(ValueError, "labels or units differ"):
            self.publish()

    def test_catalog_bytes_must_match_the_pinned_hash(self):
        self.plan()
        self.add_job({"TEST.A": [10, 20, 30]})
        frame = pd.read_parquet(self.catalog)
        frame.loc[0, "unit"] = "Changed after planning"
        frame.to_parquet(self.catalog, index=False)
        with self.assertRaisesRegex(ValueError, "Pinned catalog hash"):
            self.publish()

    def test_publishing_metadata_only_queue_keeps_pending_scope_honest(self):
        self.plan(("TEST.A", "TEST.B"))
        self.add_job({"TEST.A": [], "TEST.B": []}, pending=True)
        result = self.publish()
        release, _, coverage = self.frames()
        self.assertEqual(0, len(pd.read_parquet(release / "observations_long.parquet")))
        self.assertEqual(2, len(coverage))
        self.assertFalse(result["validation"]["full_request_scope_complete"])
        self.assertEqual(1, result["validation"]["pending_job_count"])
        self.assertEqual(0, result["validation"]["attempted_series"])

    def test_integer_annual_source_label_survives_raw_replay(self):
        self.plan(frequency="YILLIK")
        self.add_job({"TEST.A": [10]}, periods=[2025])
        self.publish()
        release, _, _ = self.frames()
        row = pd.read_parquet(release / "observations_long.parquet").iloc[0]
        self.assertEqual("2025", row.source_date_label)
        self.assertEqual("2025", row.period)
        self.assertEqual(10, row.value)
        source = json.loads(gzip.decompress((release / row.source_response_file).read_bytes()))
        self.assertIs(type(source["items"][0]["Tarih"]), int)

    def test_split_parent_is_not_successful_request_evidence(self):
        self.plan(("TEST.A", "TEST.B"))
        self.add_job({"TEST.A": [], "TEST.B": []}, job_id="parent", pending=True, attempts=1)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='split' WHERE job_id='parent'")
        self.add_job({"TEST.A": [10, 20, 30]}, job_id="child-good")
        self.add_job({"TEST.B": []}, job_id="child-pending", pending=True)
        self.publish()
        _, _, coverage = self.frames()
        self.assertTrue(coverage.loc["TEST.A", "request_coverage_complete"])
        self.assertFalse(coverage.loc["TEST.B", "request_coverage_complete"])
        self.assertEqual(1, coverage.loc["TEST.B", "attempted_job_count"])
        self.assertEqual(0, coverage.loc["TEST.B", "successful_job_count"])

    def test_legacy_artifact_without_cell_paths_gets_paths_from_raw_replay(self):
        self.plan()
        artifact = self.add_job({"TEST.A": [10, 20, 30]})
        frame = pd.read_parquet(artifact / "observations.parquet").drop(columns="source_cell_path")
        frame.to_parquet(artifact / "observations.parquet", index=False)
        self.publish()
        release, _, _ = self.frames()
        published = pd.read_parquet(release / "observations_long.parquet")
        self.assertEqual(["/items/0/TEST_A", "/items/1/TEST_A", "/items/2/TEST_A"], published.source_cell_path.tolist())

    def test_saved_wrong_cell_path_is_rejected_even_if_values_match(self):
        self.plan()
        artifact = self.add_job({"TEST.A": [10, 10, 30]})
        frame = pd.read_parquet(artifact / "observations.parquet")
        frame.loc[0, "source_cell_path"] = "/items/1/TEST_A"
        frame.to_parquet(artifact / "observations.parquet", index=False)
        with self.assertRaisesRegex(ValueError, "source cell paths differ"):
            self.publish()

    def test_transposed_source_nulls_publish_their_actual_value_locations(self):
        self.plan(("TEST.A", "TEST.NULL"))
        self.add_job({"TEST.A": [10, 20, 30], "TEST.NULL": [None, None, None]}, transposed_null_codes={"TEST.NULL"})
        result = self.publish()
        release, _, coverage = self.frames()
        rows = pd.read_parquet(release / "observations_long.parquet")
        nulls = rows[rows.series_code == "TEST.NULL"]
        self.assertEqual(["/transposedItems/1/2025-01", "/transposedItems/1/2025-02", "/transposedItems/1/2025-03"], nulls.source_cell_path.tolist())
        self.assertEqual([1, 2, 3], nulls.source_row_index.tolist())
        self.assertTrue(result["validation"]["full_request_scope_complete"])
        self.assertFalse(result["validation"]["full_numeric_coverage_complete"])
        self.assertEqual(1, coverage.loc["TEST.NULL", "no_data_job_count"])
        for row in nulls.itertuples():
            raw = json.loads(gzip.decompress((release / row.source_response_file).read_bytes()))
            _, container, index, source_date = row.source_cell_path.split("/")
            self.assertIsNone(raw[container][int(index)][source_date])
            self.assertNotIn("TEST_NULL", raw["items"][row.source_row_index-1])
            self.assertIsNone(row.value_raw)

    def test_transposed_fallback_cannot_hide_a_missing_provenance_path(self):
        self.plan(("TEST.A", "TEST.NULL"))
        artifact = self.add_job({"TEST.A": [10, 20, 30], "TEST.NULL": [None, None, None]}, transposed_null_codes={"TEST.NULL"})
        frame = pd.read_parquet(artifact / "observations.parquet").drop(columns="source_cell_path")
        frame.to_parquet(artifact / "observations.parquet", index=False)
        with self.assertRaisesRegex(ValueError, "requires source_cell_path"):
            self.publish()

    def test_validated_replacement_covers_superseded_failed_child_without_faking_success(self):
        self.plan(("TEST.A", "TEST.NULL"))
        self.add_job({"TEST.NULL": []}, job_id="failed-child", pending=True, attempts=3)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='permanent_failed' WHERE job_id='failed-child'")
        self.add_job({"TEST.A": [10, 20, 30], "TEST.NULL": [None, None, None]}, job_id="replacement", transposed_null_codes={"TEST.NULL"})
        self.supersede("failed-child", "replacement")
        result = self.publish()
        release, manifest, coverage = self.frames()
        self.assertTrue(result["validation"]["full_request_scope_complete"])
        self.assertEqual(1, result["validation"]["superseded_job_count"])
        self.assertEqual("permanent_failed", manifest["supersessions"][0]["previous_status"])
        self.assertEqual("replacement", manifest["supersessions"][0]["replacement_job_id"])
        self.assertEqual(0, coverage.loc["TEST.NULL", "successful_job_count"])
        self.assertEqual(1, coverage.loc["TEST.NULL", "no_data_job_count"])
        self.assertEqual({"replacement"}, set(pd.read_parquet(release / "observations_long.parquet").source_job_id))

    def test_unmapped_superseded_job_is_rejected(self):
        self.plan()
        self.add_job({"TEST.A": []}, pending=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='superseded'")
        with self.assertRaisesRegex(ValueError, "no supersession audit mapping"):
            self.publish()

    def test_supersession_cannot_replace_a_different_series(self):
        self.plan(("TEST.A", "TEST.B"))
        self.add_job({"TEST.B": []}, job_id="child", pending=True)
        self.add_job({"TEST.A": [10, 20, 30]}, job_id="replacement")
        self.supersede("child", "replacement")
        with self.assertRaisesRegex(ValueError, "does not contain the original series"):
            self.publish()

    def test_supersession_cannot_replace_a_longer_interval_with_a_shorter_one(self):
        self.plan()
        self.add_job({"TEST.A": []}, job_id="child", pending=True)
        self.add_job({"TEST.A": [10]}, periods=["2025-01"], end="2025-01-31", job_id="replacement")
        self.supersede("child", "replacement")
        with self.assertRaisesRegex(ValueError, "does not contain the original time"):
            self.publish()

    def test_supersession_requires_terminal_replacement_and_preserved_scope_audit(self):
        self.plan()
        self.add_job({"TEST.A": []}, job_id="child", pending=True)
        self.add_job({"TEST.A": []}, job_id="replacement", pending=True)
        self.supersede("child", "replacement")
        with self.assertRaisesRegex(ValueError, "validated terminal response"):
            self.publish()
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='no_data' WHERE job_id='replacement'")
            connection.execute("UPDATE job_supersessions SET previous_job_json='{}'")
        with self.assertRaisesRegex(ValueError, "original job scope"):
            self.publish()

    def test_supersession_still_replays_the_replacement_raw_response(self):
        self.plan()
        self.add_job({"TEST.A": []}, job_id="child", pending=True)
        artifact = self.add_job({"TEST.A": [10, 20, 30]}, job_id="replacement")
        self.supersede("child", "replacement")
        (artifact / "response.json.gz").write_bytes(gzip.compress(b'{"tampered":true}'))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.publish()

    def test_unknown_job_state_is_not_silently_excluded_from_completion(self):
        self.plan()
        self.add_job({"TEST.A": []}, pending=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE jobs SET status='ignored'")
        with self.assertRaisesRegex(ValueError, "Unknown EVDS queue job state"):
            self.publish()


if __name__ == "__main__":
    unittest.main()
