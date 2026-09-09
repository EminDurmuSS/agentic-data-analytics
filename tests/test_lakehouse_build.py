"""Exercise the real builder's semantic reproducibility and publication gate."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_pipeline.lakehouse import build_lakehouse
from tools.lakehouse_store import file_sha256


class LakehouseBuildPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="kkb-build-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.database = cls.directory / "analytics.duckdb"
        cls.result = build_lakehouse.build(cls.database)
        cls.validation_path = cls.directory / "validation.json"

    def test_clean_build_preserves_tracked_semantic_report_bytes(self):
        tracked = build_lakehouse.PROJECT_ROOT / "data_pipeline/lakehouse/validation.json"
        rebuilt_bytes = self.validation_path.read_bytes()
        tracked_bytes = tracked.read_bytes()
        self.assertEqual(json.loads(rebuilt_bytes), json.loads(tracked_bytes))
        self.assertEqual(rebuilt_bytes, tracked_bytes)
        self.assertNotIn("database_bytes", json.loads(rebuilt_bytes))
        self.assertEqual(self.result["database_bytes"], self.database.stat().st_size)

    def test_failed_semantic_gate_preserves_published_database_and_report(self):
        before_database = file_sha256(self.database)
        before_report = self.validation_path.read_bytes()
        install = build_lakehouse.install_bindings

        def install_then_corrupt_unit(connection):
            result = install(connection)
            connection.execute("""UPDATE bddk.monthly_measurements SET unit='million_try'
                WHERE rowid=(SELECT rowid FROM bddk.monthly_measurements
                             WHERE measure_kind='count_stock'
                             ORDER BY month,group_code,metric_code LIMIT 1)""")
            return result

        with patch.object(build_lakehouse, "install_bindings", install_then_corrupt_unit):
            with self.assertRaisesRegex(ValueError, "monthly_count_units"):
                build_lakehouse.build(self.database)
        self.assertEqual(file_sha256(self.database), before_database)
        self.assertEqual(self.validation_path.read_bytes(), before_report)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         ["analytics.duckdb", "validation.json"])


if __name__ == "__main__":
    unittest.main()
