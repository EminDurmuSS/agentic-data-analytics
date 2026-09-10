"""Small immutable publications exercise catalog, binding and coverage integration."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

from data_pipeline.catalog import build_unified_catalog as catalog
from data_pipeline.lakehouse import build_lakehouse as lakehouse
from agentic_analytics.lakehouse.registry import install_bindings, get_bindings
from agentic_analytics.lakehouse.quality import validate_full_evds


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


class BulkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.package = self.root / "data_pipeline/evds/full_catalog"
        self.patches = [patch.object(catalog, "PROJECT_ROOT", self.root),
                        patch.object(lakehouse, "PROJECT_ROOT", self.root)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.directory.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def publication(self, *, null_last=True, increment=0):
        stage = self.package / "stage"
        stage.mkdir(parents=True)
        codes = ["TP.KTF10", "TP.KTF12", "TP.NULL", "TP.NEW.UNCATALOGED"]
        observations, metadata, coverage = [], [], []
        for index, code in enumerate(codes):
            null = code == "TP.NULL" and null_last
            metadata.append({"series_code": code, "series_name_tr": code + " kaynak ölçüsü",
                             "series_name_en": code, "group_name_tr": "Fixture", "frequency": "monthly",
                             "unit": "Endeks" if code == "TP.NEW.UNCATALOGED" else "Yüzde",
                             "source": "TCMB", "metadata_url": "https://evds3.tcmb.gov.tr/", "is_archive": False})
            for month in ("2025-01", "2025-02"):
                value = None if null else float(index + 10 + increment)
                observations.append({"series_code": code, "period": month, "period_start": month + "-01",
                    "period_end": month + ("-31" if month.endswith("01") else "-28"), "value": value,
                    "value_raw": "" if null else str(value), "is_missing": null, "native_frequency": "monthly",
                    "source_response_file": "raw/response.json", "source_response_sha256": "a" * 64,
                    "source_request_file": "raw/request.json", "source_request_sha256": "b" * 64,
                    "source_row_index": 0 if month.endswith("01") else 1, "source_job_id": "fixture-job",
                    "source_attempt": 1, "missing_kind": "source_null" if null else None,
                    "is_unresolved_missing": null})
            coverage.append({"series_code": code, "native_frequency": "monthly", "target_start": "2025-01-01",
                "target_end": "2025-02-28", "observation_count": 2, "numeric_observation_count": 0 if null else 2,
                "missing_observation_count": 2 if null else 0, "physical_present": True,
                "observed_start": "2025-01-01", "observed_end": "2025-02-28",
                "coverage_status": "source_missing" if null else "complete", "request_coverage_complete": True,
                "numeric_coverage_complete": not null, "attempted_job_count": 1})
        pd.DataFrame(observations).to_parquet(stage / "observations_long.parquet", index=False)
        pd.DataFrame(metadata).to_parquet(stage / "analysis_series_catalog.parquet", index=False)
        pd.DataFrame(coverage).to_parquet(stage / "coverage.parquet", index=False)
        summary = {"status": "passed", "metadata_series": 4, "physical_series": 4,
                   "numeric_series": 3 if null_last else 4, "observation_count": 8,
                   "source_null_count": 2 if null_last else 0, "completed_request_series": 4,
                   "full_request_scope_complete": True, "full_numeric_coverage_complete": not null_last,
                   "evds_full_observation_coverage_complete": not null_last,
                   "attempted_series": 4, "attempted_job_count": 1}
        (stage / "validation.json").write_bytes(json_bytes(summary))
        files = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}
                 for path in stage.iterdir()}
        manifest = {"format_version": 1, "dataset_id": "evds.full_catalog", "target_start": "2025-01-01",
                    "target_end": "2025-02-28", "files": files, "validation": summary}
        publication_id = hashlib.sha256(json_bytes(manifest)).hexdigest()[:24]
        manifest["publication_id"] = publication_id
        (stage / "manifest.json").write_bytes(json_bytes(manifest))
        release = self.package / "releases" / publication_id
        release.parent.mkdir(exist_ok=True)
        stage.rename(release)
        (self.package / "CURRENT.json").write_bytes(json_bytes({"publication_id": publication_id,
            "manifest_sha256": hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()}))
        return release, manifest

    def metrics(self):
        rows = []
        for code in ("TP.KTF10", "TP.KTF12", "TP.NULL"):
            physical = code == "TP.KTF12"
            rows.append(catalog.make_metric(metric_id=f"evds:{code}", dataset_id="evds.housing_causality_v1" if physical else "evds.public_series_catalog",
                source_system="TCMB_EVDS", source_metric_code=code, metric_name_tr=code,
                source_organization="TCMB", observation_available=physical, observation_count=2 if physical else 0,
                missing_observation_count=0, native_frequency="monthly", unit="Yüzde",
                temporal_semantics="rate" if physical else "", default_aggregation="mean", notes="Reviewed primary" if physical else "",
                coverage_start="2025-01", coverage_end="2025-02", source_asset="data_pipeline/evds/reviewed/observations_long.parquet",
                institution_grain="series_defined", geography_grain="series_defined"))
        return rows

    def connection(self, release, manifest, metrics, summary):
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("CREATE SCHEMA catalog")
        table_manifest = lakehouse.install_full_evds(connection, release, manifest, summary)
        connection.execute("CREATE TABLE evds.reviewed_observations(series_code VARCHAR,period VARCHAR,value DOUBLE)")
        connection.execute("INSERT INTO evds.reviewed_observations VALUES ('TP.KTF12','2025-01',1),('TP.KTF12','2025-02',2)")
        table_manifest.append({"schema_name": "evds", "table_name": "reviewed_observations", "row_count": 2,
                               "source_path": "data_pipeline/evds/reviewed/observations_long.parquet"})
        connection.register("metric_frame", pd.DataFrame(metrics, columns=catalog.METRIC_COLUMNS))
        connection.execute("CREATE TABLE catalog.metrics AS SELECT * FROM metric_frame")
        connection.unregister("metric_frame")
        connection.register("manifest_frame", pd.DataFrame(table_manifest))
        connection.execute("CREATE TABLE catalog.table_manifest AS SELECT * FROM manifest_frame")
        connection.unregister("manifest_frame")
        install_bindings(connection)
        return connection

    def test_absent_package_and_seed_catalog_are_optional(self):
        metrics = self.metrics()
        self.assertFalse(catalog.include_full_catalog_evds([], metrics)["present"])
        self.assertFalse(lakehouse.resolve_full_evds({"status": "passed"}))
        self.publication()
        self.assertIsNone(lakehouse.resolve_full_evds({"status": "passed"}))

    def test_new_native_bindings_preserve_reviewed_identity_and_nulls(self):
        release, manifest = self.publication()
        metrics, assets = self.metrics(), []
        reviewed = dict(metrics[1])
        summary = catalog.include_full_catalog_evds(assets, metrics)
        self.assertEqual(reviewed, metrics[1])
        self.assertEqual(4, len(metrics))
        self.assertEqual(1, summary["preserved_primary_series"])
        connection = self.connection(release, manifest, metrics, summary)
        bindings = get_bindings(connection)
        new = bindings["evds:TP.KTF10"]
        self.assertTrue(new["binding_available"])
        self.assertEqual("evds.full_catalog_observations", new["table"])
        self.assertEqual("review_required", new["status"])
        self.assertEqual("unknown", new["kind"])
        self.assertEqual("review_required", new["aggregation"])
        self.assertEqual("decompressed_response", new["hash_basis"])
        self.assertIn(manifest["publication_id"], new["source_base"])
        self.assertIn("source_response_sha256", new["provenance_columns"])
        self.assertEqual("ready", bindings["evds:TP.KTF12"]["status"])
        self.assertEqual("evds.reviewed_observations", bindings["evds:TP.KTF12"]["table"])
        self.assertEqual("no_numeric", bindings["evds:TP.NULL"]["status"])
        self.assertEqual("unknown", bindings["evds:TP.NEW.UNCATALOGED"]["kind"])
        self.assertEqual([(10.0,), (10.0,)], connection.execute("SELECT value FROM evds.full_catalog_observations WHERE series_code='TP.KTF10' ORDER BY period").fetchall())
        quality = validate_full_evds(connection)
        self.assertTrue(quality["full_request_scope_complete"])
        self.assertFalse(quality["evds_full_observation_coverage_complete"])
        self.assertEqual(3, quality["numeric_series"])
        self.assertEqual(1, quality["semantic_ready_series"])
        self.assertEqual(1, quality["attempted_job_count"])
        self.assertEqual(4, quality["attempted_series_request_count"])

    def test_numeric_completion_does_not_imply_semantic_readiness(self):
        release, manifest = self.publication(null_last=False)
        metrics = self.metrics()
        summary = catalog.include_full_catalog_evds([], metrics)
        quality = validate_full_evds(self.connection(release, manifest, metrics, summary))
        self.assertTrue(quality["evds_full_observation_coverage_complete"])
        self.assertFalse(quality["full_semantic_scope_ready"])
        self.assertEqual(1, quality["numeric_and_semantic_ready_series"])

    def test_subset_cannot_claim_complete_catalog_scope(self):
        release, manifest = self.publication(null_last=False)
        metrics = self.metrics()
        missing = dict(metrics[0], metric_id="evds:TP.UNFETCHED", source_metric_code="TP.UNFETCHED")
        metrics.append(missing)
        summary = catalog.include_full_catalog_evds([], metrics)
        self.assertFalse(summary["catalog_scope_complete"])
        self.assertFalse(summary["full_request_scope_complete"])
        # Publisher claimed completion of its four-series input, but consumer
        # rejects that global claim when the discovery universe has five series.
        with self.assertRaisesRegex(ValueError, "overstates"):
            validate_full_evds(self.connection(release, manifest, metrics, summary))

    def test_pinned_release_survives_current_advance_and_tampering_is_rejected(self):
        first, manifest = self.publication()
        summary = catalog.include_full_catalog_evds([], self.metrics())
        self.publication(increment=1)
        resolved, _ = lakehouse.resolve_full_evds({"evds_full_catalog": summary})
        self.assertEqual(first, resolved)
        with (first / "coverage.parquet").open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            lakehouse.resolve_full_evds({"evds_full_catalog": summary})

    def test_copied_fact_count_and_identity_are_release_gates(self):
        release, manifest = self.publication()
        metrics = self.metrics()
        summary = catalog.include_full_catalog_evds([], metrics)
        connection = self.connection(release, manifest, metrics, summary)
        connection.execute("INSERT INTO evds.full_catalog_observations SELECT * FROM evds.full_catalog_observations LIMIT 1")
        with self.assertRaisesRegex(ValueError, "full_evds_native_key_unique"):
            validate_full_evds(connection)


if __name__ == "__main__":
    unittest.main()
