"""Bounded source navigation metadata, independent of source evidence reads."""
import copy
import json
import re
from functools import lru_cache
from pathlib import Path


def source_ids(value):
    ids = [] if value is None else value
    if (not isinstance(ids, list) or len(ids) > 12 or
            any(not isinstance(item, str) or not re.fullmatch(r"source_[a-f0-9]{64}", item) for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Select at most 12 distinct registered source identifiers")
    return list(ids)


def validate_sources(store, workspace_id, selected):
    ids = source_ids(selected)
    if ids:
        from agentic_analytics.agent.tools.documents import DocumentTools
        documents = DocumentTools(store, workspace_id)
        for source_id in ids:
            manifest = documents.source(source_id)
            if manifest.get("workspace_id") != workspace_id or manifest.get("source_id") != source_id:
                raise ValueError("Source does not belong to this workspace")
    return ids


@lru_cache(maxsize=96)
def _navigation(path, manifest_stamp, inspection_stamp):
    # The stat keys invalidate metadata after an inspection/review. No raw PDF
    # bytes are opened here; actual source-reading tools verify their hashes.
    directory = Path(path)
    manifest = json.loads((directory / "manifest.json").read_text())
    result = {key: manifest[key] for key in ("source_id", "filename", "mime_type", "source_url") if key in manifest}
    result.update(navigation_only=True, content_is_untrusted_data=True)
    if inspection_stamp:
        inspection = json.loads((directory / "inspection.json").read_text())
        result["total_pages"] = inspection.get("total_pages")
        result["cached_pages"] = inspection.get("processed_pages", [])[:60]
        result["tables"] = [{key: table[key] for key in
            ("table_id", "page", "sheet", "columns", "row_count", "layout_review_required") if key in table}
            for table in inspection.get("tables", [])[:8]]
        for table in result["tables"]:
            table["columns"] = [str(column)[:100] for column in table.get("columns", [])[:10]]
        result["remaining_tables"] = max(0, len(inspection.get("tables", [])) - len(result["tables"]))
    return result


def registered_sources(store, workspace_id, state):
    root = store.root / "document_sources" / workspace_id
    selected = source_ids(state.get("selected_source_ids"))
    selections = [{"request": message.get("content", "")[:500], "source_ids": source_ids(message["source_ids"])}
                  for message in state.get("messages", []) if message.get("role") == "user" and message.get("source_ids")][-4:]
    priority = list(dict.fromkeys([*selected, *[item for selection in reversed(selections) for item in selection["source_ids"]]]))
    paths = {path.name: path for path in root.glob("source_*") if path.is_dir() and not path.is_symlink()
             and re.fullmatch(r"source_[a-f0-9]{64}", path.name)}
    ordered = [*priority, *sorted(paths, reverse=True)]
    cards = []
    for source_id in dict.fromkeys(ordered):
        if source_id not in paths or len(cards) >= 12:
            continue
        path = paths[source_id]
        try:
            manifest = (path / "manifest.json").stat()
            cached = (path / "inspection.json").stat() if (path / "inspection.json").is_file() else None
            # A large extraction can be re-read explicitly, without growing
            # every decision's navigation context or its metadata cache.
            if manifest.st_size > 16384:
                continue
            stamp = (cached.st_mtime_ns, cached.st_size) if cached and cached.st_size <= 4_000_000 else None
            card = copy.deepcopy(_navigation(str(path), (manifest.st_mtime_ns, manifest.st_size), stamp))
            if card.get("source_id") != source_id:
                continue
            cards.append(card)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return {"selected_source_ids": selected, "recent_source_selections": selections, "registered_sources": cards,
            "source_navigation_hint": "Selected source IDs identify the user's exact attachments even when filenames match. These cached cards are navigation only, not numerical evidence. Reuse the registered source_id, find relevant pages with find_source_pages, then inspect only those pages and read the selected table. Do not refetch the URL or re-inspect all cached pages by default."} if cards or selected else {}
