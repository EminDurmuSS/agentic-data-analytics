import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.merge_bddk_weekly_snapshots import merge


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def config(groups: dict[str, str], end: str = "2026-06-30") -> dict:
    return {
        "source_url": "https://www.bddk.org.tr/BultenHaftalik/",
        "start": "2021-01-01",
        "end": end,
        "period_count": 286,
        "tables": {"289": "Krediler"},
        "groups": groups,
        "currency": "TL",
        "raw_format": "gzip-compressed source HTML",
        "tls_verification": True,
    }


class BddkWeeklyMergeTests(unittest.TestCase):
    def test_only_validated_hash_matching_pairs_are_merged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            source = root / "source"
            (destination / "raw").mkdir(parents=True)
            (source / "raw").mkdir(parents=True)
            write_json(
                destination / "request_config.json",
                config({"10001": "Sektor", "10002": "Mevduat"}),
            )
            write_json(
                source / "request_config.json", config({"10002": "Mevduat"})
            )

            html = b"<html><table id='Tablo'></table></html>"
            raw_path = source / "raw" / "2021-01-08_table289_group10002.html.gz"
            raw_path.write_bytes(gzip.compress(html))
            info_path = source / "raw" / "2021-01-08_table289_group10002_info.json"
            write_json(
                info_path,
                {
                    "status": "validated",
                    "sha256_uncompressed": hashlib.sha256(html).hexdigest(),
                },
            )

            result = merge(destination, [source])
            self.assertEqual(1, result["validated_pairs"])
            self.assertEqual(1, result["linked"] + result["copied"])
            self.assertEqual(raw_path.read_bytes(), (destination / "raw" / raw_path.name).read_bytes())

            repeated = merge(destination, [source])
            self.assertEqual(1, repeated["existing"])

            destination_info_path = destination / "raw" / info_path.name
            destination_info = json.loads(
                destination_info_path.read_text(encoding="utf-8")
            )
            destination_info["served_from_cache"] = True
            write_json(destination_info_path, destination_info)
            repeated_after_cache_replay = merge(destination, [source])
            self.assertEqual(1, repeated_after_cache_replay["existing"])

    def test_incompatible_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            source = root / "source"
            destination.mkdir()
            source.mkdir()
            write_json(
                destination / "request_config.json",
                config({"10001": "Sektor"}),
            )
            write_json(
                source / "request_config.json",
                config({"10001": "Sektor"}, end="2026-03-31"),
            )
            with self.assertRaisesRegex(ValueError, "Uyumsuz"):
                merge(destination, [source])


if __name__ == "__main__":
    unittest.main()
