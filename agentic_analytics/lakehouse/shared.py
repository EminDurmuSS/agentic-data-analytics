"""Immutable shared releases for explicitly promoted web datasets.

The primary lakehouse snapshot remains immutable. A shared release is a small,
content-addressed manifest over already validated Parquet overlays plus a
self-contained copy of the source evidence used to publish each overlay.
Only new workspaces opt into the current release, so existing analyses remain
pinned to their original workspace revision.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from agentic_analytics.lakehouse.store import (
    LakehouseStore,
    StoreError,
    _canonical,
    _copy_source,
    _digest,
    _fsync_directory,
    _identifier,
    _write_json,
    file_sha256,
)


class SharedLakehouseError(StoreError):
    """A shared promotion failed a durable evidence or policy gate."""

    def __init__(self, message: str, code: str = "SHARED_LAKEHOUSE_ERROR"):
        super().__init__(message)
        self.code = code


class SharedLakehouse:
    """Content-addressed promotion packages and immutable shared release heads."""

    POLICY_VERSION = "official-web-v1"
    _REQUIRED_PROMOTION_FILES = {
        "source/raw.bin",
        "source/manifest.json",
        "source/inspection.json",
        "dataset/manifest.json",
        "dataset/source.csv",
        "dataset/data.parquet",
    }

    def __init__(self, store: LakehouseStore):
        self.store = store
        self.root = self.store._path("shared")
        self.root.mkdir(exist_ok=True)
        for name in ("promotions", "releases", ".staging"):
            self._path(name).mkdir(exist_ok=True)

    def _path(self, *parts) -> Path:
        return self.store._path("shared", *parts)

    @contextmanager
    def _lock(self):
        path = self.store._path(".locks", "shared-lakehouse.lock")
        with path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _stage(self):
        path = Path(tempfile.mkdtemp(prefix="build-", dir=self._path(".staging")))
        try:
            yield path
        finally:
            if path.exists():
                shutil.rmtree(path)

    @staticmethod
    def _read_json(path: Path, message: str, code: str = "SHARED_SOURCE_INTEGRITY_FAILED") -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SharedLakehouseError(message, code) from exc
        if not isinstance(value, dict):
            raise SharedLakehouseError(message, code)
        return value

    @staticmethod
    def _relative_file(value: str) -> PurePosixPath:
        if not isinstance(value, str):
            raise SharedLakehouseError("Promotion file path is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise SharedLakehouseError("Promotion file path is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        return path

    @staticmethod
    def _official_source(url: str, official_sources: dict) -> tuple[str, dict]:
        if not isinstance(official_sources, dict) or not official_sources:
            raise SharedLakehouseError("No official-source policy was configured.", "SHARED_SOURCE_POLICY_MISSING")
        if not isinstance(url, str) or not url.strip():
            raise SharedLakehouseError("The dataset has no valid official web URL.", "SHARED_SOURCE_NOT_OFFICIAL")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise SharedLakehouseError("The dataset has no valid official web URL.", "SHARED_SOURCE_NOT_OFFICIAL") from exc
        if (parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or port not in {None, 80, 443} or any(ord(character) < 32 for character in url)):
            raise SharedLakehouseError("The dataset has no valid official web URL.", "SHARED_SOURCE_NOT_OFFICIAL")
        hostname = parsed.hostname.casefold().rstrip(".").removeprefix("www.")
        matches = []
        for raw_domain, descriptor in official_sources.items():
            domain = str(raw_domain).casefold().rstrip(".").removeprefix("www.")
            if hostname == domain or hostname.endswith("." + domain):
                matches.append((domain.count("."), domain, descriptor))
        if not matches:
            raise SharedLakehouseError(
                "Only anonymously accessible sources in the configured official registry can be promoted.",
                "SHARED_SOURCE_NOT_OFFICIAL",
            )
        _, domain, descriptor = max(matches, key=lambda item: item[0])
        return domain, descriptor if isinstance(descriptor, dict) else {}

    @staticmethod
    def _metric_ids(dataset_id: str, manifest: dict) -> list[str]:
        contract = manifest["contract"]
        return [
            f"overlay:{dataset_id}:{name}"
            for name, definition in contract["columns"].items()
            if definition.get("dtype") in {"integer", "float"} and name not in contract.get("grain", [])
        ]

    def _copy_verified(self, source: Path, target: Path, *, expected_sha256: str | None = None,
                       limit: int | None = None) -> str:
        if source.is_symlink() or not source.is_file():
            raise SharedLakehouseError("Required promotion evidence is missing.", "SHARED_SOURCE_INTEGRITY_FAILED")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            copied = _copy_source(source, target, limit or self.store.limits["max_snapshot_bytes"])
        except (OSError, StoreError) as exc:
            raise SharedLakehouseError("Promotion evidence changed while it was copied.", "SHARED_SOURCE_INTEGRITY_FAILED") from exc
        if expected_sha256 is not None and copied != expected_sha256:
            raise SharedLakehouseError("Promotion evidence hash does not match its published provenance.", "SHARED_SOURCE_INTEGRITY_FAILED")
        return copied

    def _review_path(self, source_directory: Path, review: dict, raw_sha256: str, *,
                     source_id: str, table_id: str) -> Path:
        if not isinstance(review, dict) or not isinstance(review.get("table_id"), str):
            raise SharedLakehouseError("Source review reference is invalid.", "SHARED_SOURCE_REVIEW_REQUIRED")
        if (review.get("source_id") != source_id or review.get("table_id") != table_id
                or not re.fullmatch(r"table_(?:\d{3}|p\d{6}(?:_text)?_\d{3}|c[a-f0-9]{20})", table_id)):
            raise SharedLakehouseError("Source review table identifier is invalid.", "SHARED_SOURCE_REVIEW_REQUIRED")
        path = source_directory / (table_id + "_review.json")
        stored = self._read_json(path, "Source review evidence is missing or invalid.")
        content = {key: value for key, value in stored.items() if key != "review_sha256"}
        expected = _digest(_canonical(content))
        reference = {key: value for key, value in stored.items() if key != "rows"}
        if (stored.get("review_sha256") != expected or review.get("review_sha256") != expected
                or stored.get("source_id") != source_id or stored.get("table_id") != table_id
                or stored.get("raw_sha256") != raw_sha256 or review != reference
                or stored.get("verification") != "explicit_user_cell_and_unit_review"):
            raise SharedLakehouseError("Source review evidence hash mismatch.", "SHARED_SOURCE_REVIEW_REQUIRED")
        return path

    @staticmethod
    def _fsync_tree(root: Path) -> None:
        directories = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
                path.chmod(0o444)
            elif path.is_dir():
                directories.append(path)
        for directory in reversed(directories):
            _fsync_directory(directory)
        _fsync_directory(root)

    def _publish_directory(self, stage: Path, kind: str, object_id: str) -> None:
        destination = self._path(kind, object_id)
        self._fsync_tree(stage)
        try:
            os.rename(stage, destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} or not destination.is_dir():
                raise
            if kind == "promotions":
                self._promotion(object_id)
            else:
                self._release(object_id)
        else:
            _fsync_directory(destination.parent)

    def _promotion(self, promotion_id: str) -> dict:
        _identifier(promotion_id, "promotion")
        directory = self._path("promotions", promotion_id)
        manifest = self._read_json(
            directory / "manifest.json", "Promotion manifest is missing or invalid.",
            "SHARED_RELEASE_INTEGRITY_FAILED",
        )
        content = {key: value for key, value in manifest.items() if key != "promotion_id"}
        if manifest.get("promotion_id") != promotion_id or promotion_id != "promotion_" + _digest(_canonical(content)):
            raise SharedLakehouseError("Promotion manifest hash mismatch.", "SHARED_RELEASE_INTEGRITY_FAILED")
        if manifest.get("format_version") != 1 or not isinstance(manifest.get("policy_version"), str):
            raise SharedLakehouseError("Promotion manifest version is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise SharedLakehouseError("Promotion file inventory is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        seen = set()
        for item in files:
            if (not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}
                    or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                    or type(item.get("size_bytes")) is not int or item["size_bytes"] < 0):
                raise SharedLakehouseError("Promotion file inventory is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
            relative = self._relative_file(item["path"])
            if relative.as_posix() in seen:
                raise SharedLakehouseError("Promotion file inventory contains duplicates.", "SHARED_RELEASE_INTEGRITY_FAILED")
            seen.add(relative.as_posix())
            path = self._path("promotions", promotion_id, *relative.parts)
            if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size_bytes"] or file_sha256(path) != item["sha256"]:
                raise SharedLakehouseError("Promotion evidence hash mismatch.", "SHARED_RELEASE_INTEGRITY_FAILED")
        if not self._REQUIRED_PROMOTION_FILES.issubset(seen):
            raise SharedLakehouseError("Promotion package is incomplete.", "SHARED_RELEASE_INTEGRITY_FAILED")
        actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*")
                  if path.is_file() and path.relative_to(directory).as_posix() != "manifest.json"}
        if actual != seen:
            raise SharedLakehouseError("Promotion package contains untracked files.", "SHARED_RELEASE_INTEGRITY_FAILED")

        dataset_id = manifest.get("dataset_id")
        dataset_manifest = self.store.dataset_manifest(dataset_id)
        self.store.raw_source_path(dataset_id)
        self.store.overlay_path(dataset_id)
        packaged_dataset = self._read_json(
            self._path("promotions", promotion_id, "dataset", "manifest.json"),
            "Packaged dataset manifest is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED",
        )
        if packaged_dataset != dataset_manifest:
            raise SharedLakehouseError("Packaged dataset manifest differs from the shared artifact.", "SHARED_RELEASE_INTEGRITY_FAILED")
        packaged_csv = self._path("promotions", promotion_id, "dataset", "source.csv")
        packaged_parquet = self._path("promotions", promotion_id, "dataset", "data.parquet")
        if (file_sha256(packaged_csv) != dataset_manifest["source_sha256"]
                or file_sha256(packaged_parquet) != dataset_manifest["data_sha256"]
                or manifest.get("dataset_source_sha256") != dataset_manifest["source_sha256"]
                or manifest.get("dataset_data_sha256") != dataset_manifest["data_sha256"]
                or manifest.get("contract_sha256") != _digest(_canonical(dataset_manifest["contract"]))
                or manifest.get("metric_ids") != self._metric_ids(dataset_id, dataset_manifest)):
            raise SharedLakehouseError("Packaged dataset bytes differ from their manifest.", "SHARED_RELEASE_INTEGRITY_FAILED")
        source_manifest = self._read_json(
            self._path("promotions", promotion_id, "source", "manifest.json"),
            "Packaged source manifest is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED",
        )
        if (source_manifest.get("source_id") != manifest.get("source_id")
                or source_manifest.get("source_url") != manifest.get("source_url")
                or source_manifest.get("raw_sha256") != manifest.get("raw_sha256")):
            raise SharedLakehouseError("Packaged source identity differs from the promotion manifest.", "SHARED_RELEASE_INTEGRITY_FAILED")
        raw = self._path("promotions", promotion_id, "source", "raw.bin")
        if (file_sha256(raw) != manifest.get("raw_sha256")
                or source_manifest.get("size_bytes") != raw.stat().st_size):
            raise SharedLakehouseError("Packaged raw source hash mismatch.", "SHARED_RELEASE_INTEGRITY_FAILED")
        inspection = self._read_json(
            self._path("promotions", promotion_id, "source", "inspection.json"),
            "Packaged source inspection is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED",
        )
        if not any(isinstance(table, dict) and table.get("table_id") == manifest.get("table_id")
                   for table in inspection.get("tables", [])):
            raise SharedLakehouseError("Packaged source inspection does not contain the promoted table.",
                                       "SHARED_RELEASE_INTEGRITY_FAILED")
        return manifest

    def _release(self, release_id: str) -> dict:
        _identifier(release_id, "release")
        manifest = self._read_json(
            self._path("releases", release_id, "manifest.json"),
            "Shared release manifest is missing or invalid.", "SHARED_RELEASE_INTEGRITY_FAILED",
        )
        content = {key: value for key, value in manifest.items() if key != "release_id"}
        if manifest.get("release_id") != release_id or release_id != "release_" + _digest(_canonical(content)):
            raise SharedLakehouseError("Shared release manifest hash mismatch.", "SHARED_RELEASE_INTEGRITY_FAILED")
        if manifest.get("format_version") != 1 or not isinstance(manifest.get("policy_version"), str):
            raise SharedLakehouseError("Shared release manifest version is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        promotion_ids, dataset_ids = manifest.get("promotion_ids"), manifest.get("dataset_ids")
        if (not isinstance(promotion_ids, list) or promotion_ids != sorted(set(promotion_ids))
                or not isinstance(dataset_ids, list) or dataset_ids != sorted(set(dataset_ids))):
            raise SharedLakehouseError("Shared release membership is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        promotions = [self._promotion(promotion_id) for promotion_id in promotion_ids]
        expected_datasets = sorted({promotion["dataset_id"] for promotion in promotions})
        if dataset_ids != expected_datasets:
            raise SharedLakehouseError("Shared release dataset membership does not match its promotions.", "SHARED_RELEASE_INTEGRITY_FAILED")
        return manifest

    def _current_release_unlocked(self) -> dict | None:
        pointer = self._path("CURRENT.json")
        if not pointer.exists():
            return None
        value = self._read_json(pointer, "Shared release pointer is missing or invalid.",
                                "SHARED_RELEASE_INTEGRITY_FAILED")
        if set(value) != {"release_id"}:
            raise SharedLakehouseError("Shared release pointer is invalid.", "SHARED_RELEASE_INTEGRITY_FAILED")
        return self._release(value["release_id"])

    def current_release(self) -> dict | None:
        """Return the active release after verifying every referenced byte."""
        return self._current_release_unlocked()

    def current_dataset_ids(self) -> list[str]:
        release = self.current_release()
        return list(release["dataset_ids"]) if release else []

    def promotion_file(self, promotion_id: str, relative_path: str) -> Path:
        manifest = self._promotion(promotion_id)
        relative = self._relative_file(relative_path).as_posix()
        if relative not in {item["path"] for item in manifest["files"]}:
            raise SharedLakehouseError("File is not part of the promotion package.", "SHARED_RELEASE_INTEGRITY_FAILED")
        return self._path("promotions", promotion_id, *PurePosixPath(relative).parts)

    def _build_release(self, promotion_ids: list[str]) -> dict:
        promotion_ids = sorted(set(promotion_ids))
        promotions = [self._promotion(promotion_id) for promotion_id in promotion_ids]
        content = {
            "format_version": 1,
            "policy_version": self.POLICY_VERSION,
            "promotion_ids": promotion_ids,
            "dataset_ids": sorted({promotion["dataset_id"] for promotion in promotions}),
        }
        release_id = "release_" + _digest(_canonical(content))
        manifest = {**content, "release_id": release_id}
        with self._stage() as stage:
            _write_json(stage / "manifest.json", manifest)
            self._publish_directory(stage, "releases", release_id)
        return self._release(release_id)

    def _activate(self, release: dict) -> None:
        current = self._current_release_unlocked()
        if current and current["release_id"] == release["release_id"]:
            return
        temporary = self._path(".current-" + os.urandom(16).hex() + ".json")
        try:
            _write_json(temporary, {"release_id": release["release_id"]})
            os.replace(temporary, self._path("CURRENT.json"))
            _fsync_directory(self.root)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _result(promotion: dict, release: dict, *, publication_performed: bool,
                recovered: bool = False, reason: str | None = None) -> dict:
        return {
            "status": "ok",
            "promotion_id": promotion["promotion_id"],
            "shared_release_id": release["release_id"],
            "dataset_id": promotion["dataset_id"],
            "metric_ids": list(promotion["metric_ids"]),
            "official_source": {
                "institution": promotion.get("source_institution"),
                "domain": promotion["official_domain"],
                "url": promotion["source_url"],
                "raw_sha256": promotion["raw_sha256"],
            },
            "publication_performed": publication_performed,
            "recovered": recovered,
            "available_to_new_finance_workspaces": True,
            **({"reason_acknowledged": reason.strip()} if isinstance(reason, str) and reason.strip() else {}),
        }

    def _release_promotion_for_dataset(self, release: dict | None, dataset_id: str) -> dict | None:
        if not release or dataset_id not in release["dataset_ids"]:
            return None
        matches = []
        for promotion_id in release["promotion_ids"]:
            promotion = self._promotion(promotion_id)
            if promotion["dataset_id"] == dataset_id:
                matches.append(promotion)
        if len(matches) != 1:
            raise SharedLakehouseError(
                "Shared release contains ambiguous promotion membership for this dataset.",
                "SHARED_RELEASE_INTEGRITY_FAILED",
            )
        return matches[0]

    def promote(self, workspace_id: str, dataset_id: str, *, official_sources: dict,
                reason: str | None = None) -> dict:
        """Package one workspace-owned official web dataset and advance CURRENT."""
        workspace = self.store.workspace(workspace_id)
        if dataset_id not in workspace.get("datasets", []):
            raise SharedLakehouseError("Dataset does not belong to this workspace.", "SHARED_DATASET_NOT_IN_WORKSPACE")
        with self._lock():
            current = self._current_release_unlocked()
            existing = self._release_promotion_for_dataset(current, dataset_id)
            if existing:
                return self._result(existing, current, publication_performed=False, reason=reason)
        dataset_manifest = self.store.dataset_manifest(dataset_id)
        dataset_csv = self.store.raw_source_path(dataset_id)
        dataset_parquet = self.store.overlay_path(dataset_id)
        contract = dataset_manifest.get("contract") or {}
        provenance = contract.get("document_provenance")
        required = ("source_id", "table_id", "raw_sha256")
        if (not isinstance(provenance, dict) or "source_url" not in provenance
                or any(not isinstance(provenance.get(key), str) or not provenance[key] for key in required)):
            raise SharedLakehouseError(
                "Only a published document table with complete source provenance can be promoted.",
                "SHARED_SOURCE_PROVENANCE_REQUIRED",
            )
        if not re.fullmatch(r"[0-9a-f]{64}", provenance["raw_sha256"]):
            raise SharedLakehouseError("Source hash in dataset provenance is invalid.", "SHARED_SOURCE_INTEGRITY_FAILED")
        for definition in contract.get("columns", {}).values():
            if definition.get("dtype") in {"integer", "float"} and definition.get("kind") == "unknown":
                raise SharedLakehouseError(
                    "Numeric columns with unknown semantics cannot enter the shared lakehouse.",
                    "SHARED_DATASET_SEMANTICS_REVIEW_REQUIRED",
                )

        official_domain, descriptor = self._official_source(provenance["source_url"], official_sources)
        source_id = _identifier(provenance["source_id"], "source")
        source_directory = self.store._path("document_sources", _identifier(workspace_id), source_id)
        if source_directory.is_symlink() or not source_directory.is_dir():
            raise SharedLakehouseError("Original source evidence is no longer available.", "SHARED_SOURCE_INTEGRITY_FAILED")
        source_manifest = self._read_json(source_directory / "manifest.json", "Source manifest is missing or invalid.")
        if (source_manifest.get("source_id") != source_id
                or source_manifest.get("workspace_id") != workspace_id
                or source_manifest.get("source_url") != provenance["source_url"]
                or source_manifest.get("raw_sha256") != provenance["raw_sha256"]):
            raise SharedLakehouseError("Source manifest does not match dataset provenance.", "SHARED_SOURCE_INTEGRITY_FAILED")
        raw_source = source_directory / "raw.bin"
        if (raw_source.is_symlink() or not raw_source.is_file()
                or file_sha256(raw_source) != provenance["raw_sha256"]
                or source_manifest.get("size_bytes") != raw_source.stat().st_size):
            raise SharedLakehouseError("Raw source bytes do not match dataset provenance.", "SHARED_SOURCE_INTEGRITY_FAILED")
        inspection_path = source_directory / "inspection.json"
        inspection = self._read_json(inspection_path, "Source inspection evidence is missing or invalid.")
        table = next((item for item in inspection.get("tables", [])
                      if isinstance(item, dict) and item.get("table_id") == provenance["table_id"]), None)
        if table is None:
            raise SharedLakehouseError("Published table is absent from source inspection evidence.", "SHARED_SOURCE_INTEGRITY_FAILED")
        direct_review = provenance.get("human_review")
        preparation = provenance.get("preparation") or {}
        source_review = preparation.get("source_review")
        if (table.get("origin") == "ocr" or table.get("missing_formula_cache") or table.get("layout_review_required")) and not direct_review:
            raise SharedLakehouseError(
                "OCR, formula-cache or merged-layout evidence must be independently reviewed before shared promotion.",
                "SHARED_SOURCE_REVIEW_REQUIRED",
            )
        review = direct_review or source_review
        review_table_id = provenance["table_id"] if direct_review else preparation.get("source_table_id")
        review_path = self._review_path(
            source_directory, review, provenance["raw_sha256"],
            source_id=source_id, table_id=review_table_id,
        ) if review else None

        with self._stage() as stage:
            self._copy_verified(raw_source, stage / "source" / "raw.bin",
                                expected_sha256=provenance["raw_sha256"], limit=self.store.limits["max_source_bytes"])
            self._copy_verified(source_directory / "manifest.json", stage / "source" / "manifest.json",
                                limit=8 * 1024**2)
            self._copy_verified(inspection_path, stage / "source" / "inspection.json",
                                limit=self.store.limits["max_source_bytes"])
            if review_path:
                self._copy_verified(review_path, stage / "source" / "reviews" / review_path.name, limit=8 * 1024**2)
            extraction_ref = provenance.get("extraction_artifact_ref")
            if extraction_ref is not None:
                if not isinstance(extraction_ref, str) or not re.fullmatch(r"extraction_[0-9a-f]{64}", extraction_ref):
                    raise SharedLakehouseError("Extraction evidence reference is invalid.", "SHARED_SOURCE_INTEGRITY_FAILED")
                extraction = self.store._path("document_sources", workspace_id, "extractions", extraction_ref + ".json")
                extracted = self._read_json(extraction, "Extraction evidence is missing or invalid.")
                extraction_content = {key: value for key, value in extracted.items() if key != "extraction_id"}
                if extracted.get("extraction_id") != extraction_ref or extraction_ref != "extraction_" + _digest(_canonical(extraction_content)):
                    raise SharedLakehouseError("Extraction evidence hash mismatch.", "SHARED_SOURCE_INTEGRITY_FAILED")
                self._copy_verified(extraction, stage / "source" / "extractions" / (extraction_ref + ".json"),
                                    limit=8 * 1024**2)

            dataset_directory = self.store._path("datasets", dataset_id)
            self._copy_verified(dataset_directory / "manifest.json", stage / "dataset" / "manifest.json", limit=8 * 1024**2)
            self._copy_verified(dataset_csv, stage / "dataset" / "source.csv",
                                expected_sha256=dataset_manifest["source_sha256"], limit=self.store.limits["max_source_bytes"])
            self._copy_verified(dataset_parquet, stage / "dataset" / "data.parquet",
                                expected_sha256=dataset_manifest["data_sha256"], limit=self.store.limits["max_result_bytes"])
            files = [{"path": path.relative_to(stage).as_posix(), "sha256": file_sha256(path),
                      "size_bytes": path.stat().st_size}
                     for path in sorted(stage.rglob("*")) if path.is_file()]
            content = {
                "format_version": 1,
                "policy_version": self.POLICY_VERSION,
                "source_workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "metric_ids": self._metric_ids(dataset_id, dataset_manifest),
                "source_id": source_id,
                "table_id": provenance["table_id"],
                "source_url": provenance["source_url"],
                "official_domain": official_domain,
                "source_institution": descriptor.get("institution"),
                "raw_sha256": provenance["raw_sha256"],
                "dataset_source_sha256": dataset_manifest["source_sha256"],
                "dataset_data_sha256": dataset_manifest["data_sha256"],
                "contract_sha256": _digest(_canonical(contract)),
                "files": files,
            }
            promotion_id = "promotion_" + _digest(_canonical(content))
            promotion = {**content, "promotion_id": promotion_id}
            _write_json(stage / "manifest.json", promotion)
            with self._lock():
                self._publish_directory(stage, "promotions", promotion_id)
                promotion = self._promotion(promotion_id)
                current = self._current_release_unlocked()
                existing = self._release_promotion_for_dataset(current, dataset_id)
                if existing:
                    promotion = existing
                    release = current
                else:
                    release = self._build_release([*(current or {}).get("promotion_ids", []), promotion_id])
                    self._activate(release)
                return self._result(promotion, release, publication_performed=existing is None, reason=reason)

    def recover_promotion(self, dataset_id: str) -> dict | None:
        """Recover an interrupted promotion without republishing source bytes."""
        _identifier(dataset_id, "dataset")
        with self._lock():
            current = self._current_release_unlocked()
            if current:
                for promotion_id in current["promotion_ids"]:
                    promotion = self._promotion(promotion_id)
                    if promotion["dataset_id"] == dataset_id:
                        return self._result(promotion, current, publication_performed=False, recovered=True)
            candidates = []
            for path in sorted(self._path("promotions").glob("promotion_*")):
                if not path.is_dir():
                    continue
                promotion = self._promotion(path.name)
                if promotion["dataset_id"] == dataset_id and promotion.get("policy_version") == self.POLICY_VERSION:
                    candidates.append(promotion)
            if not candidates:
                return None
            if len(candidates) != 1:
                raise SharedLakehouseError("Multiple promotion packages match this dataset.", "SHARED_PROMOTION_AMBIGUOUS")
            promotion = candidates[0]
            release = self._build_release([*(current or {}).get("promotion_ids", []), promotion["promotion_id"]])
            self._activate(release)
            return self._result(promotion, release, publication_performed=True, recovered=True)
