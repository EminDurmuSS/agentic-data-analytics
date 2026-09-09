import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data_pipeline.build_file_manifest import build


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data_pipeline"


class FileManifestBuildTests(unittest.TestCase):
    def test_local_download_artifacts_do_not_change_distributed_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "bddk/weekly_all_groups/raw/observation.html.gz"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"validated source snapshot")
            clean = build(base)
            for relative in [
                "bddk/weekly_group_10003/raw/download.html.gz",
                "bddk/weekly_group_10007/manifest.json",
                "bddk/monthly_all_sector_Paylas.zip",
                "bddk/__pycache__/downloader.pyc",
                "lakehouse/analytics.duckdb",
                "lakehouse/analytics.duckdb.wal",
            ]:
                artifact = base / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"local temporary output")
            self.assertEqual(clean, build(base))
            self.assertEqual(
                {"bddk/weekly_all_groups/raw/observation.html.gz":
                 hashlib.sha256(source.read_bytes()).hexdigest()}, clean
            )

    def test_new_source_files_are_included_without_requiring_git(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "evds/new_collection/raw/series.json"
            source.parent.mkdir(parents=True)
            source.write_bytes(b'{"value": 10}')
            manifest = build(base)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                manifest["evds/new_collection/raw/series.json"],
            )
            self.assertEqual(manifest, build(base))


class FileManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            (DATA_DIR / "FILE_SHA256.json").read_text(encoding="utf-8")
        )

    def test_manifest_has_no_absolute_paths(self) -> None:
        self.assertTrue(self.manifest)
        self.assertFalse(any(Path(path).is_absolute() for path in self.manifest))

    def test_manifested_files_exist_and_match(self) -> None:
        missing = []
        mismatched = []
        for relative, expected in self.manifest.items():
            path = DATA_DIR / relative
            if not path.is_file():
                missing.append(relative)
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                mismatched.append(relative)
        self.assertEqual([], missing)
        self.assertEqual([], mismatched)

    def test_core_raw_collections_are_covered(self) -> None:
        prefixes = [
            "bddk/monthly_all_groups/raw/",
            "bddk/weekly_all_groups/raw/",
            "bddk/finturk_all_groups_all_cities/raw/",
            "evds/housing_causality_v1/raw/",
            "evds/regional_housing_v1/raw/",
            "evds/household_finance_v1/raw/",
            "tuik/province_housing_sales_v1/raw/",
            "tbb/consumer_credit_reports/raw/",
        ]
        for prefix in prefixes:
            self.assertTrue(any(path.startswith(prefix) for path in self.manifest), prefix)


if __name__ == "__main__":
    unittest.main()
