"""Publication, restart, contract and concurrent-update checks using real files."""

from concurrent.futures import ProcessPoolExecutor
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from tools.lakehouse_store import LakehouseStore, StoreError, VersionConflict, file_sha256


def _concurrent_save(arguments):
    root, workspace_id, value = arguments
    store = LakehouseStore(root)
    try:
        result = store.save_analysis(workspace_id, pd.DataFrame({"value": [value]}),
                                     {"select": "value"}, {"value": "fixture"},
                                     expected_version=0)
        return "saved", result["analysis_id"]
    except VersionConflict:
        return "conflict", None


class LakehouseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "input.duckdb"
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("CREATE TABLE observations(month VARCHAR, value DOUBLE)")
            connection.execute("INSERT INTO observations VALUES ('2026-01', 100), ('2026-02', 110)")
        self.store = LakehouseStore(self.directory / "store")
        self.snapshot = self.store.publish_snapshot(self.database)
        self.workspace = self.store.create_workspace(self.snapshot["snapshot_id"], "workspace_test")
        self.contract = {
            "name": "clinic_visits", "frequency": "monthly", "date_column": "month",
            "key": ["month", "clinic"], "grain": ["month", "clinic"],
            "columns": {
                "month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                "clinic": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False},
                "visits": {"dtype": "integer", "unit": "visits", "kind": "count", "nullable": False},
            },
        }

    def csv(self, content="month,clinic,visits\n2026-01,A,10\n2026-02,A,20\n"):
        path = self.directory / "input.csv"
        path.write_text(content)
        return path

    def save(self, frame=None, **kwargs):
        return self.store.save_analysis(
            "workspace_test", frame if frame is not None else pd.DataFrame({"month": ["2026-01"], "value": [100.0]}),
            {"operation": "select", "metric": "value"}, {"value": {"source": "fixture"}}, **kwargs)

    def test_snapshot_is_content_addressed_and_survives_source_rebuild(self):
        source_before = file_sha256(self.database)
        again = self.store.publish_snapshot(self.database)
        self.assertEqual(self.snapshot, again)
        self.assertEqual(len(list((self.store.root / "snapshots").iterdir())), 1)
        with duckdb.connect(str(self.database)) as connection:
            connection.execute("UPDATE observations SET value = 999")
        self.assertNotEqual(file_sha256(self.database), source_before)
        with duckdb.connect(str(self.store.snapshot_path(self.snapshot["snapshot_id"])), read_only=True) as connection:
            self.assertEqual(connection.execute("SELECT sum(value) FROM observations").fetchone()[0], 210)
        changed = self.store.publish_snapshot(self.database)
        self.assertNotEqual(changed["snapshot_id"], self.snapshot["snapshot_id"])
        self.assertEqual(self.store.workspace("workspace_test")["snapshot_id"], self.snapshot["snapshot_id"])

    def test_failed_release_validator_cannot_publish(self):
        original = sorted(path.name for path in (self.store.root / "snapshots").iterdir())
        for rejected in (False, {"passed": False, "reason": "units"}, {"status": "failed"}):
            with self.subTest(rejected=rejected), self.assertRaises(StoreError):
                self.store.publish_snapshot(self.database, lambda path: rejected)
        self.assertEqual(original, sorted(path.name for path in (self.store.root / "snapshots").iterdir()))
        self.assertEqual(list((self.store.root / ".staging").iterdir()), [])

    def test_mutating_release_validator_is_rejected(self):
        def change(path):
            with duckdb.connect(str(path)) as connection:
                connection.execute("UPDATE observations SET value = 12")
            return {"passed": True}

        with self.assertRaisesRegex(StoreError, "must not change"):
            self.store.publish_snapshot(self.database, change)
        self.assertEqual(len(list((self.store.root / "snapshots").iterdir())), 1)

    def test_uncheckpointed_wal_is_rejected(self):
        wal = Path(str(self.database) + ".wal")
        wal.write_bytes(b"pending transaction")
        try:
            with self.assertRaisesRegex(StoreError, "Checkpoint"):
                self.store.publish_snapshot(self.database)
        finally:
            wal.unlink()

    def test_generic_csv_overlay_preserves_raw_and_old_revision(self):
        source = self.csv("month,clinic,visits\n2026-02,A,20\n2026-01,A,10\n")
        old = self.store.workspace("workspace_test")
        updated = self.store.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        self.assertEqual(updated["version"], 1)
        self.assertEqual(len(updated["datasets"]), 1)
        dataset_id = updated["datasets"][0]
        manifest = self.store.dataset_manifest(dataset_id)
        self.assertEqual(manifest["source_sha256"], file_sha256(source))
        self.assertEqual(manifest["contract"], self.contract)
        self.assertEqual(self.store.raw_source_path(dataset_id).read_bytes(), source.read_bytes())
        frame = pd.read_parquet(self.store.overlay_path(dataset_id))
        self.assertEqual(frame["visits"].tolist(), [10, 20])
        restarted = LakehouseStore(self.store.root)
        self.assertEqual(restarted.workspace("workspace_test"), updated)
        self.assertEqual(restarted.workspace("workspace_test", old["revision_id"]), old)
        self.assertEqual(old["datasets"], [])

    def test_duplicate_overlay_is_idempotent(self):
        source = self.csv()
        first = self.store.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        second = self.store.ingest_csv("workspace_test", source, self.contract, expected_version=1)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.store.root / "datasets").iterdir())), 1)

    def test_invalid_csv_never_advances_workspace(self):
        cases = {
            "duplicate grain": "month,clinic,visits\n2026-01,A,10\n2026-01,A,20\n",
            "missing key": "month,clinic,visits\n2026-01,,10\n",
            "missing value": "month,clinic,visits\n2026-01,A,\n",
            "fractional count": "month,clinic,visits\n2026-01,A,1.5\n",
            "not numeric": "month,clinic,visits\n2026-01,A,many\n",
            "not finite": "month,clinic,visits\n2026-01,A,inf\n",
            "invalid calendar": "month,clinic,visits\n2026-13,A,10\n",
            "wrong frequency": "month,clinic,visits\n2026-01-10,A,10\n",
            "duplicate column": "month,clinic,visits,visits\n2026-01,A,10,20\n",
            "unexpected column": "month,clinic,visits,secret\n2026-01,A,10,x\n",
            "empty": "month,clinic,visits\n",
        }
        for reason, csv in cases.items():
            with self.subTest(reason=reason), self.assertRaises(StoreError):
                self.store.ingest_csv("workspace_test", self.csv(csv), self.contract, expected_version=0)
            self.assertEqual(self.store.workspace("workspace_test"), self.workspace)
            self.assertEqual(list((self.store.root / "datasets").iterdir()), [])
            self.assertEqual(list((self.store.root / ".staging").iterdir()), [])

    def test_explicit_contract_is_required(self):
        for field in ("key", "grain", "date_column", "frequency", "columns", "name"):
            contract = copy.deepcopy(self.contract)
            del contract[field]
            with self.subTest(field=field), self.assertRaises(StoreError):
                self.store.ingest_csv("workspace_test", self.csv(), contract, expected_version=0)
        for field in ("dtype", "unit", "kind", "nullable"):
            contract = copy.deepcopy(self.contract)
            del contract["columns"]["visits"][field]
            with self.subTest(field=field), self.assertRaises(StoreError):
                self.store.ingest_csv("workspace_test", self.csv(), contract, expected_version=0)

    def test_partial_csv_cannot_satisfy_explicit_coverage_contract(self):
        contract = copy.deepcopy(self.contract)
        contract["expected_periods"] = ["2026-01", "2026-02", "2026-03"]
        with self.assertRaisesRegex(StoreError, "partial"):
            self.store.ingest_csv("workspace_test", self.csv(), contract, expected_version=0)
        self.assertEqual(self.store.workspace("workspace_test"), self.workspace)
        # Overall period coverage can be complete while one entity is partial.
        contract["expected_periods"] = ["2026-01", "2026-02"]
        source = self.csv("month,clinic,visits\n2026-01,A,10\n2026-02,A,20\n2026-01,B,30\n")
        with self.assertRaisesRegex(StoreError, "entity"):
            self.store.ingest_csv("workspace_test", source, contract, expected_version=0)
        del contract["expected_periods"]
        contract["expected_rows"] = 4
        with self.assertRaisesRegex(StoreError, "partial"):
            self.store.ingest_csv("workspace_test", source, contract, expected_version=0)

    def test_declared_coverage_is_distinct_from_structural_validity(self):
        source = self.csv()
        first = self.store.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        self.assertEqual(self.store.dataset_manifest(first["datasets"][0])["completeness"], "not_asserted")
        contract = copy.deepcopy(self.contract)
        contract["expected_rows"] = 2
        contract["expected_periods"] = ["2026-01", "2026-02"]
        second = self.store.ingest_csv("workspace_test", source, contract, expected_version=1)
        self.assertEqual(self.store.dataset_manifest(second["datasets"][1])["completeness"], "verified_explicit_expectation")

    def test_source_replacement_during_copy_is_rejected(self):
        source = self.csv()
        actual_hash = file_sha256

        def replace_source(path):
            if Path(path).resolve() == source.resolve():
                source.write_text("month,clinic,visits\n2026-01,A,999\n")
            return actual_hash(path)

        with patch("tools.lakehouse_store.file_sha256", side_effect=replace_source):
            with self.assertRaisesRegex(StoreError, "changed or was replaced"):
                self.store.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        self.assertEqual(self.store.workspace("workspace_test"), self.workspace)
        self.assertEqual(list((self.store.root / "datasets").iterdir()), [])

    def test_malformed_record_cannot_silently_shift_csv_columns(self):
        for record in ("2026-01,A,10,20", "2026-01,A", '2026-01,"A,10'):
            with self.subTest(record=record), self.assertRaises(StoreError):
                self.store.ingest_csv("workspace_test", self.csv("month,clinic,visits\n" + record + "\n"),
                                       self.contract, expected_version=0)

    def test_generic_quarterly_static_and_boolean_types(self):
        contract = copy.deepcopy(self.contract)
        contract["frequency"] = "quarterly"
        contract["columns"]["visits"] = {"dtype": "boolean", "unit": "boolean", "kind": "measure", "nullable": False}
        first = self.store.ingest_csv("workspace_test", self.csv("month,clinic,visits\n2026-Q1,A,true\n"),
                                      contract, expected_version=0)
        frame = pd.read_parquet(self.store.overlay_path(first["datasets"][0]))
        self.assertTrue(frame.loc[0, "visits"])
        self.assertEqual(frame.loc[0, "month"], "2026-Q1")
        contract["frequency"] = "static"
        contract["date_column"] = None
        del contract["columns"]["month"]
        contract["key"] = contract["grain"] = ["clinic"]
        second = self.store.ingest_csv("workspace_test", self.csv("clinic,visits\nA,false\n"),
                                       contract, expected_version=1)
        frame = pd.read_parquet(self.store.overlay_path(second["datasets"][1]))
        self.assertFalse(frame.loc[0, "visits"])

    def test_missing_observations_remain_null_when_contract_allows_them(self):
        contract = copy.deepcopy(self.contract)
        contract["columns"]["visits"]["nullable"] = True
        result = self.store.ingest_csv("workspace_test", self.csv("month,clinic,visits\n2026-01,A,\n"),
                                       contract, expected_version=0)
        frame = pd.read_parquet(self.store.overlay_path(result["datasets"][0]))
        self.assertTrue(pd.isna(frame.loc[0, "visits"]))
        self.assertEqual(str(frame.visits.dtype), "Int64")

    def test_large_integer_counts_do_not_wrap_or_round(self):
        for raw in ("9223372036854775808", "18446744073709551615", "-9223372036854775809"):
            with self.subTest(raw=raw), self.assertRaises(StoreError):
                self.store.ingest_csv("workspace_test", self.csv("month,clinic,visits\n2026-01,A," + raw + "\n"),
                                       self.contract, expected_version=0)
        result = self.store.ingest_csv(
            "workspace_test", self.csv("month,clinic,visits\n2026-01,A,9223372036854775807\n2026-02,A,9007199254740993.0\n"),
            self.contract, expected_version=0)
        frame = pd.read_parquet(self.store.overlay_path(result["datasets"][0]))
        self.assertEqual(frame.visits.tolist(), [9223372036854775807, 9007199254740993])

    def test_resource_limits_reject_before_publication(self):
        source = self.csv()
        limited = LakehouseStore(self.store.root, max_source_bytes=10)
        with self.assertRaises(StoreError):
            limited.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        limited = LakehouseStore(self.store.root, max_rows=1)
        with self.assertRaises(StoreError):
            limited.ingest_csv("workspace_test", source, self.contract, expected_version=0)
        with self.assertRaises(StoreError):
            limited.save_analysis("workspace_test", pd.DataFrame({"x": [1, 2]}), {}, {}, expected_version=0)
        limited = LakehouseStore(self.store.root, max_snapshot_bytes=1)
        with self.assertRaises(StoreError):
            limited.publish_snapshot(self.database)
        self.assertEqual(self.store.workspace("workspace_test"), self.workspace)

    def test_analysis_revision_preserves_previous_columns_and_restarts(self):
        frame = pd.DataFrame({"month": ["2026-01", "2026-02"], "credit": [100.0, 110.0], "rate": [40.0, 41.0]})
        first = self.save(frame, expected_version=0)
        revised = frame.assign(real_credit=[100.0, 105.0])
        second = self.save(revised, parent_analysis_id=first["analysis_id"], expected_version=1)
        restarted = LakehouseStore(self.store.root)
        first_frame, first_manifest = restarted.load_analysis(first["analysis_id"])
        second_frame, second_manifest = restarted.load_analysis(second["analysis_id"])
        pd.testing.assert_frame_equal(first_frame, frame)
        pd.testing.assert_frame_equal(second_frame[frame.columns], first_frame)
        self.assertEqual(first, first_manifest)
        self.assertEqual(second, second_manifest)
        self.assertEqual(second_manifest["parent_analysis_id"], first["analysis_id"])
        self.assertEqual(second_manifest["snapshot_id"], self.snapshot["snapshot_id"])
        self.assertEqual(restarted.workspace("workspace_test")["version"], 2)
        self.assertEqual(first_manifest["workspace_revision_id"], self.workspace["revision_id"])

    def test_stale_version_and_wrong_parent_are_rejected(self):
        first = self.save(expected_version=0)
        for arguments in ({"expected_version": 0},
                          {"expected_version": 1, "parent_analysis_id": "analysis_" + "0" * 64}):
            with self.subTest(arguments=arguments), self.assertRaises(VersionConflict):
                self.save(**arguments)
        current = self.store.workspace("workspace_test")
        self.assertEqual(current["analysis_head"], first["analysis_id"])
        self.assertEqual(current["version"], 1)
        self.assertEqual(len(list((self.store.root / "analyses").iterdir())), 1)

    def test_two_processes_cannot_both_overwrite_the_same_revision(self):
        arguments = [(str(self.store.root), "workspace_test", value) for value in (10, 20)]
        with ProcessPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(_concurrent_save, arguments))
        self.assertEqual(sorted(status for status, _ in outcomes), ["conflict", "saved"])
        current = self.store.workspace("workspace_test")
        self.assertEqual(current["version"], 1)
        self.assertEqual(current["analysis_head"], next(identifier for status, identifier in outcomes if status == "saved"))

    def test_failed_overlay_pointer_publication_preserves_old_workspace(self):
        with patch("tools.lakehouse_store.os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.store.ingest_csv("workspace_test", self.csv(), self.contract, expected_version=0)
        self.assertEqual(self.store.workspace("workspace_test"), self.workspace)
        # An unreachable, completely validated object may remain after failure.
        # A retry publishes the same object without exposing a partial revision.
        published = self.store.ingest_csv("workspace_test", self.csv(), self.contract, expected_version=0)
        self.assertEqual(published["version"], 1)
        self.assertEqual(len(published["datasets"]), 1)

    def test_failed_analysis_pointer_publication_preserves_old_head(self):
        first = self.save(expected_version=0)
        before = self.store.workspace("workspace_test")
        with patch("tools.lakehouse_store.os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.save(pd.DataFrame({"value": [200]}), parent_analysis_id=first["analysis_id"], expected_version=1)
        self.assertEqual(self.store.workspace("workspace_test"), before)
        original, _ = self.store.load_analysis(first["analysis_id"])
        self.assertEqual(original.value.tolist(), [100.0])

    def test_corrupted_payload_is_detected_after_restart(self):
        result = self.save(expected_version=0)
        path = Path(result["result_path"])
        path.chmod(0o644)
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(StoreError, "payload hash"):
            LakehouseStore(self.store.root).load_analysis(result["analysis_id"])

    def test_corrupted_manifest_is_detected(self):
        result = self.save(expected_version=0)
        path = Path(result["result_path"]).with_name("manifest.json")
        path.chmod(0o644)
        manifest = json.loads(path.read_text())
        manifest["plan"] = {"operation": "different"}
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(StoreError, "manifest hash"):
            self.store.load_analysis(result["analysis_id"])

    def test_model_like_path_or_sql_inputs_cannot_be_resolved(self):
        for identifier in ("../input.duckdb", "/tmp/database", "x'; COPY secrets TO 'file'", "snapshot_bad"):
            with self.subTest(identifier=identifier), self.assertRaises(StoreError):
                self.store.snapshot_path(identifier)
        with self.assertRaises(StoreError):
            self.store.workspace("../../workspaces")
        link = self.store.root / "analyses" / ("analysis_" + "0" * 64)
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StoreError):
            self.store.load_analysis(link.name)

    def test_nonfinite_result_or_metadata_is_rejected(self):
        with self.assertRaises(StoreError):
            self.save(pd.DataFrame({"value": [float("inf")]}), expected_version=0)
        with self.assertRaises(StoreError):
            self.store.save_analysis("workspace_test", pd.DataFrame({"value": [1]}),
                                     {"bad": float("nan")}, {}, expected_version=0)
        self.assertEqual(self.store.workspace("workspace_test"), self.workspace)


if __name__ == "__main__":
    unittest.main()
