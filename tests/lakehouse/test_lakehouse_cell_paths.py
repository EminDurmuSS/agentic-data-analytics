"""Actual value pointers survive native reads, saves and explanations."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import duckdb
import pandas as pd

from data_pipeline.catalog.build_unified_catalog import make_metric, METRIC_COLUMNS
from agentic_analytics.lakehouse.registry import get_bindings, install_bindings
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore


class SourceCellPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def service(self, with_paths=True):
        # Separate validated monthly response windows: January's explicit null
        # exists only in transposedItems, while February has a numeric item.
        january = {"items": [{"Tarih": "2025-01", "TP_OTHER": "9"}],
                   "transposedItems": [{"2025-01": "9"}, {"2025-01": None}]}
        if not with_paths:
            january["items"][0]["TP_TEST"] = None
        february = {"items": [{"Tarih": "2025-02", "TP_TEST": "7"}]}
        observations = []
        for month, payload, value, pointer in [
            ("2025-01", january, None, "/transposedItems/1/2025-01"),
            ("2025-02", february, 7.0, "/items/0/TP_TEST"),
        ]:
            raw = json.dumps(payload, separators=(",", ":")).encode()
            (self.root / f"{month}.json").write_bytes(raw)
            row = {"series_code": "TP.TEST", "period": month, "value": value,
                   "source_response_file": f"{month}.json", "source_response_sha256": hashlib.sha256(raw).hexdigest(),
                   "source_row_index": 1, "source_date_label": month, "value_raw": None if value is None else "7",
                   "is_missing": value is None, "missing_kind": "source_null" if value is None else None,
                   "is_unresolved_missing": value is None}
            if with_paths:
                row["source_cell_path"] = pointer
            observations.append(row)
        database = self.root / "source.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute("CREATE SCHEMA evds")
        connection.execute("CREATE SCHEMA catalog")
        connection.register("source_frame", pd.DataFrame(observations))
        connection.execute("CREATE TABLE evds.full_catalog_observations AS SELECT * FROM source_frame")
        source_path = "data_pipeline/evds/full_catalog/releases/fixture/observations_long.parquet"
        connection.execute("CREATE TABLE catalog.table_manifest(schema_name VARCHAR,table_name VARCHAR,source_path VARCHAR)")
        connection.execute("INSERT INTO catalog.table_manifest VALUES ('evds','full_catalog_observations',?)", [source_path])
        metric = make_metric(metric_id="evds:TP.TEST", dataset_id="evds.full_catalog", source_system="TCMB_EVDS",
            source_metric_code="TP.TEST", metric_name_tr="Fixture source", native_frequency="AYLIK", unit="",
            temporal_semantics="requires_semantic_review", default_aggregation="review_required",
            observation_available=True, observation_count=2, missing_observation_count=1,
            source_asset=source_path, coverage_start="2025-01", coverage_end="2025-02", notes="Fixture")
        connection.register("metric_frame", pd.DataFrame([metric], columns=METRIC_COLUMNS))
        connection.execute("CREATE TABLE catalog.metrics AS SELECT * FROM metric_frame")
        install_bindings(connection)
        binding = get_bindings(connection)["evds:TP.TEST"]
        connection.close()
        store = LakehouseStore(self.root / "store")
        snapshot = store.publish_snapshot(database)
        workspace = store.create_workspace(snapshot["snapshot_id"])
        return LakehouseService(store, workspace["workspace_id"]), binding

    @staticmethod
    def plan():
        return {"start": "2025-01", "end": "2025-02", "frequency": "monthly",
                "columns": [{"name": "measure", "metric_id": "evds:TP.TEST", "alignment": "native"}]}

    def test_transposed_null_pointer_is_the_value_location_and_survives_reload(self):
        service, binding = self.service()
        self.assertIn("source_cell_path", binding["provenance_columns"])
        self.assertIn("authoritative", binding["source_cell_locator_policy"]["value_location"])
        result = service.execute(self.plan())
        saved = service.store.load_analysis(result["analysis_id"])[1]
        cell = saved["lineage"]["sources"]["measure"]["cells"]["2025-01"][0]
        self.assertEqual("/transposedItems/1/2025-01", cell["source_cell_path"])
        self.assertEqual(1, cell["source_row_index"])
        reloaded = LakehouseService(service.store, service.workspace_id)
        proof = reloaded.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2025-01"})
        self.assertIsNone(proof["value"])
        self.assertTrue(proof["source_references_complete"])
        self.assertFalse(proof["source_files_verified"])
        self.assertIn("date-row anchor", proof["lineage"]["source_cell_locator_policy"]["source_row_index_role"])
        raw = json.loads((self.root / cell["source_response_file"]).read_text())
        self.assertNotIn("TP_TEST", raw["items"][0])
        self.assertIsNone(raw["transposedItems"][1]["2025-01"])
        self.assertEqual(cell, proof["lineage"]["source_cells"][0])

    def test_regular_item_pointer_keeps_zero_based_path_and_one_based_date_row(self):
        service, _ = self.service()
        result = service.execute(self.plan())
        proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2025-02"})
        cell = proof["lineage"]["source_cells"][0]
        self.assertEqual("/items/0/TP_TEST", cell["source_cell_path"])
        self.assertEqual(1, cell["source_row_index"])
        self.assertEqual(7, proof["value"])
        self.assertEqual("7", cell["value_raw"])

    def test_older_publication_without_path_remains_queryable_without_synthetic_pointer(self):
        service, binding = self.service(with_paths=False)
        self.assertNotIn("source_cell_path", binding["provenance_columns"])
        self.assertNotIn("source_cell_locator_policy", binding)
        result = service.execute(self.plan())
        proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "measure", "period": "2025-01"})
        self.assertTrue(proof["source_references_complete"])
        self.assertNotIn("source_cell_locator_policy", proof["lineage"])
        self.assertNotIn("source_cell_path", proof["lineage"]["source_cells"][0])
        self.assertEqual(1, proof["lineage"]["source_cells"][0]["source_row_index"])


if __name__ == "__main__":
    unittest.main()
