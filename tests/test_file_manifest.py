import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data_pipeline"


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
            "bddk/monthly_all_sector/raw/",
            "bddk/weekly_all_sector/raw/",
            "bddk/finturk_all_groups_all_cities/raw/",
            "evds/housing_causality_v1/raw/",
            "tbb/consumer_credit_reports/raw/",
        ]
        for prefix in prefixes:
            self.assertTrue(any(path.startswith(prefix) for path in self.manifest), prefix)


if __name__ == "__main__":
    unittest.main()
