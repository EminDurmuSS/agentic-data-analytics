"""Trusted official workbook import: values, source dates and shared persistence."""
import calendar
from datetime import datetime
import hashlib
import io
import zipfile

import duckdb
from openpyxl import Workbook
import pytest

from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore
from tools.import_bist_xbank import HEADERS, MEMBER, SHEET, SOURCE_URL, parse_archive, publish


def archive(rows=None, headers=HEADERS):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET
    sheet.append(headers)
    if rows is None:
        rows = fixture_rows()
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(MEMBER, output.getvalue())
    return zipped.getvalue()


def fixture_rows():
    # Deliberately synthetic values: only live validation uses official data.
    return [[datetime(2025, month, 28 if month == 3 else calendar.monthrange(2025, month)[1]),
             "XBANK", "BIST BANKA", "TL", 1000 + month / 10] for month in range(1, 13)]


def test_keeps_original_business_closing_date_and_value():
    raw = archive()
    parsed = parse_archive(raw, 2025)
    march = parsed["observations"][2]
    assert march["month"] == "2025-03"
    assert march["closing_date"] == "2025-03-28"  # NOT calendar March 31.
    assert march["closing"] == "1000.3"
    assert march["source_row"] == 4
    assert parsed["archive_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("problem", ["missing", "duplicate", "currency", "name", "formula", "text", "negative", "midmonth", "daily", "wrong_index"])
def test_rejects_incomplete_ambiguous_or_changed_sources(problem):
    rows = fixture_rows()
    if problem == "missing":
        rows.pop()
    elif problem == "duplicate":
        rows.append(list(rows[0]))
    elif problem == "currency":
        rows[0][3] = "USD"
    elif problem == "name":
        rows[0][2] = "BIST MALI"
    elif problem == "formula":
        rows[0][4] = "=1+2"
    elif problem == "text":
        rows[0][4] = "1.000,1"
    elif problem == "negative":
        rows[0][4] = -2
    elif problem == "midmonth":
        rows[0][0] = datetime(2025, 1, 15)
    elif problem == "daily":
        rows.append([datetime(2025, 1, 30), "XBANK", "BIST BANKA", "TL", 1010])
    elif problem == "wrong_index":
        for row in rows:
            row[1] = "XUMAL"
    with pytest.raises(ValueError):
        parse_archive(archive(rows), 2025)


def test_rejects_changed_headers_and_archive_members():
    with pytest.raises(ValueError, match="headers"):
        parse_archive(archive(headers=(*HEADERS[:-1], "Getiri")), 2025)
    wrong = io.BytesIO()
    with zipfile.ZipFile(wrong, "w") as handle:
        handle.writestr("other.xlsx", b"wrong member")
    with pytest.raises(ValueError, match="archive"):
        parse_archive(wrong.getvalue(), 2025)


def test_publication_is_reusable_pinned_idempotent_and_has_cell_lineage(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    store.create_workspace(snapshot, "workspace_import")
    before = store.create_workspace(snapshot, "workspace_before")
    raw = archive()
    published = publish(store, "workspace_import", raw, 2025)
    shared = SharedLakehouse(store)
    assert shared.promotion_file(published["promotion_id"], "source/raw.bin").read_bytes() == raw
    assert store.workspace("workspace_before") == before
    assert publish(store, "workspace_import", raw, 2025)["publication_performed"] is False
    release = shared.current_release()
    store.create_workspace(snapshot, "workspace_after", initial_dataset_ids=release["dataset_ids"],
                           shared_release_id=release["release_id"])
    service = LakehouseService(store, "workspace_after")
    metric = published["metric_ids"][0]
    assert metric in [item["metric_id"] for item in service.discover({"query": "XBANK", "limit": 5})["metrics"]]
    descriptor = service.describe({"metric_id": metric})["metric"]
    assert descriptor["native_frequency"] == "monthly"
    assert descriptor["kind"] == "index"
    assert descriptor["unit"] == "index"
    result = service.execute({"start": "2025-01", "end": "2025-12", "frequency": "monthly",
                              "columns": [{"name": "closing", "metric_id": metric, "dimensions": {}}]})
    assert result["row_count"] == 12
    assert result["preview"][2]["closing"] == 1000.3
    proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "closing", "period": "2025-03"})
    provenance = proof["lineage"]["document_provenance"]
    inspected = DocumentTools(store, "workspace_import", searxng_url=False).inspect_source(source_id=provenance["source_id"])
    assert inspected["tables"][0]["row_count"] == 12
    assert inspected["tables"][0]["preview"][2]["closing_date"] == "2025-03-28"
    assert provenance["source_url"] == SOURCE_URL
    assert provenance["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["date_normalization"]["aggregation_performed"] is False
    assert provenance["cell_origins"][2]["closing"]["cell"] == "E4"
    assert provenance["cell_origins"][2]["closing"]["archive_member"] == MEMBER
