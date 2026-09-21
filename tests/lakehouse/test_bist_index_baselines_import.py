"""Official base-value import: literal source cells, no invented calendar/returns."""
import hashlib
import io
import json
from unittest.mock import Mock
import zipfile

import duckdb
import httpx
from openpyxl import Workbook
import pytest

from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.datasets import DatasetTools
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore
from tools import import_bist_index_baselines as importer


def source_rows():
    # Synthetic and deliberately different from official answers: the importer
    # must read cells, not recreate the three known W013 scalar values.
    return [
        ["XU100", "BIST 100", "BIST 100", "02.01.1987=1,25"],
        ["XUMAL", "BIST MALI", "BIST FINANCIALS", "29.12.1991=3,50"],
        ["XBANK", "BIST BANKA", "BIST BANKS", "30.12.1997=7,125"],
    ]


def workbook_bytes(rows=None, headers=importer.HEADERS, title=importer.TITLE, sheet_name=importer.SHEET):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append([title])
    sheet.append([])
    sheet.append(list(headers))
    for row in source_rows() if rows is None else rows:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_reads_original_text_cells_and_keeps_distinct_base_dates():
    raw = workbook_bytes()
    parsed = importer.parse_workbook(raw)
    assert parsed["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert parsed["frequency"] == "static"
    assert [row["base_value"] for row in parsed["observations"]] == ["1.25", "3.50", "7.125"]
    assert [row["base_date"] for row in parsed["observations"]] == ["1987-01-02", "1991-12-29", "1997-12-30"]
    assert parsed["observations"][1]["source_row"] == 5
    assert parsed["observations"][1]["base_text"] == "29.12.1991=3,50"


def test_known_official_cells_are_test_expectations_not_parser_inputs():
    rows = [[None] * 4 for _ in range(355)]
    rows[0] = ["XU100", "BIST 100", "BIST 100", "01.01.1986=0,01"]
    rows[348] = ["XUMAL", "BIST MALI", "BIST FINANCIALS", "28.12.1990=0,33"]
    rows[354] = ["XBANK", "BIST BANKA", "BIST BANKS", "27.12.1996=9,14"]
    parsed = importer.parse_workbook(workbook_bytes(rows))
    assert [(row["index_code"], row["source_row"], row["base_date"], row["base_value"])
            for row in parsed["observations"]] == [
        ("XU100", 4, "1986-01-01", "0.01"),
        ("XUMAL", 352, "1990-12-28", "0.33"),
        ("XBANK", 358, "1996-12-27", "9.14"),
    ]


@pytest.mark.parametrize("problem", [
    "missing", "duplicate", "name", "english_name", "formula", "formula_code", "numeric",
    "empty", "boolean", "invalid_date", "decimal_point", "ambiguous_grouping", "negative",
    "zero", "percent", "multiple_equals", "extra_column",
])
def test_rejects_changed_ambiguous_and_formula_cells_before_writing(problem):
    rows = source_rows()
    if problem == "missing":
        rows.pop()
    elif problem == "duplicate":
        rows.append(list(rows[0]))
    elif problem == "name":
        rows[0][1] = "BIST 30"
    elif problem == "english_name":
        rows[0][2] = "BIST 30"
    elif problem == "formula_code":
        rows[0][0] = '= "XU100"'
    elif problem == "extra_column":
        rows[0].append("return")
    else:
        rows[0][3] = {
            "formula": '= "02.01.1987=1,25"', "numeric": 1.25, "empty": None,
            "boolean": True, "invalid_date": "31.02.1987=1,25", "decimal_point": "02.01.1987=1.25",
            "ambiguous_grouping": "02.01.1987=1.000,25", "negative": "02.01.1987=-1,25",
            "zero": "02.01.1987=0", "percent": "02.01.1987=1,25%", "multiple_equals": "02.01.1987=1=25",
        }[problem]
    store = Mock()
    with pytest.raises(ValueError):
        importer.publish(store, "workspace_untouched", workbook_bytes(rows))
    store.workspace.assert_not_called()
    store.ingest_csv.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"headers": (*importer.HEADERS[:3], "Return")},
    {"title": "Performance"}, {"sheet_name": "Other"},
])
def test_rejects_changed_source_layout(kwargs):
    with pytest.raises(ValueError):
        importer.parse_workbook(workbook_bytes(**kwargs))


def test_rejects_non_workbook_oversize_and_duplicate_archive_parts(monkeypatch):
    for raw in [b"", b"<html>Access denied</html>"]:
        with pytest.raises(ValueError):
            importer.parse_workbook(raw)
    raw = workbook_bytes()
    monkeypatch.setattr(importer, "MAX_DOWNLOAD", len(raw) - 1)
    with pytest.raises(ValueError, match="size"):
        importer.parse_workbook(raw)
    monkeypatch.setattr(importer, "MAX_DOWNLOAD", len(raw) * 3)
    monkeypatch.setattr(importer, "MAX_EXPANDED", 1)
    with pytest.raises(ValueError, match="oversized"):
        importer.parse_workbook(raw)
    monkeypatch.setattr(importer, "MAX_EXPANDED", 64 * 1024**2)
    duplicate = io.BytesIO(raw)
    with zipfile.ZipFile(duplicate, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("[Content_Types].xml", "duplicate")
    with pytest.raises(ValueError, match="Unsafe"):
        importer.parse_workbook(duplicate.getvalue())


def test_download_is_fixed_bounded_and_does_not_follow_redirects(monkeypatch):
    original_client = httpx.Client
    requests = []
    status, data = 200, b"fake bytes: parser is independently tested"

    def respond(request):
        requests.append(request)
        return httpx.Response(status, content=data, headers={"location": "https://elsewhere.invalid/"})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(importer.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    assert importer.download() == data
    assert str(requests[0].url) == importer.SOURCE_URL
    assert "authorization" not in requests[0].headers
    status = 302
    with pytest.raises(httpx.HTTPStatusError):
        importer.download()
    assert len(requests) == 2
    status = 200
    monkeypatch.setattr(importer, "MAX_DOWNLOAD", 1)
    with pytest.raises(ValueError, match="download limit"):
        importer.download()


def test_publication_is_pinned_idempotent_discoverable_and_cross_sectional(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    store.create_workspace(snapshot, "workspace_import")
    before = store.create_workspace(snapshot, "workspace_before")
    raw = workbook_bytes()
    published = importer.publish(store, "workspace_import", raw)
    shared = SharedLakehouse(store)
    assert shared.promotion_file(published["promotion_id"], "source/raw.bin").read_bytes() == raw
    assert store.workspace("workspace_before") == before
    import_state = store.workspace("workspace_import")
    repeated = importer.publish(store, "workspace_import", raw)
    assert repeated["publication_performed"] is False
    assert repeated["promotion_id"] == published["promotion_id"]
    assert repeated["observations"] == published["observations"]
    assert store.workspace("workspace_import") == import_state

    release = shared.current_release()
    store.create_workspace(snapshot, "workspace_after", initial_dataset_ids=release["dataset_ids"],
                           shared_release_id=release["release_id"])
    service = LakehouseService(store, "workspace_after")
    metric = published["metric_ids"][0]
    for query in ["XU100", "XBANK", "XUMAL", "endeks başlangıç değerleri"]:
        assert metric in [item["metric_id"] for item in service.discover({"query": query, "limit": 5})["metrics"]]
    descriptor = service.describe({"metric_id": metric})["metric"]
    assert descriptor["native_frequency"] == "static"
    assert descriptor["kind"] == "index"
    assert descriptor["unit"] == "index"
    assert descriptor["currency"] is None
    assert descriptor["dimensions"] == {"index_code": "index_code"}
    assert "Not a return" in descriptor["temporal_semantics"]
    dataset_id = published["dataset_id"]
    contract = store.dataset_manifest(dataset_id)["contract"]
    assert contract["date_column"] is None
    provenance = contract["document_provenance"]
    assert provenance["source_url"] == importer.SOURCE_URL
    assert provenance["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    xumal_origin = next(row for row in provenance["cell_origins"] if row["index_code"]["original_value"] == "XUMAL")
    assert xumal_origin["base_value"]["cell"] == "D5"
    assert xumal_origin["base_date"]["cell"] == "D5"
    assert xumal_origin["base_value"]["original_value"] == "29.12.1991=3,50"
    assert provenance["date_normalization"]["common_date_asserted"] is False
    inspected = DocumentTools(store, "workspace_import", searxng_url=False).inspect_source(
        source_id=provenance["source_id"])
    assert inspected["tables"][0]["preview"][1]["base_text"] == "29.12.1991=3,50"
    copied = json.loads(shared.promotion_file(published["promotion_id"], "source/inspection.json").read_text())
    assert copied["tables"][0]["row_origins"][1]["source_row"] == 5

    tools = DatasetTools(store, "workspace_after")
    measures = [{"name": "base_value", "op": "source_value", "column": "base_value"}]
    result = tools.aggregate_dataset(dataset_id, measures, group_by="index_code")
    assert result["row_count"] == 3
    frame, manifest = store.load_analysis(result["analysis_id"])
    assert dict(zip(frame.index_code, frame.base_value)) == {"XU100": 1.25, "XUMAL": 3.5, "XBANK": 7.125}
    assert frame.period.tolist() == ["Tüm kayıtlar"] * 3
    assert manifest["lineage"]["frequency"] == "static"
    chart = ChartTools(store, "workspace_after").create_chart({
        "analysis_id": result["analysis_id"], "kind": "bar", "columns": ["base_value"]})
    assert chart["status"] == "ok"
    assert chart["row_count"] == 3
    proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "base_value",
                                   "period": "Tüm kayıtlar", "dimensions": {"index_code": "XUMAL"}})
    assert proof["value"] == 3.5
    # The store sorts XBANK,XU100,XUMAL; source-cell proof must still resolve D5.
    assert proof["lineage"]["source_rows"] == [3]
    source_origin = proof["lineage"]["document_provenance"]["cell_origins"][proof["lineage"]["source_rows"][0] - 1]
    assert source_origin["base_value"]["cell"] == "D5"
    assert source_origin["index_code"]["original_value"] == "XUMAL"
    with pytest.raises(PlanError):
        tools.aggregate_dataset(dataset_id, measures, group_by="index_code", time_bucket={"frequency": "daily"})
    with pytest.raises(PlanError):
        tools.aggregate_dataset(dataset_id, measures)
    with pytest.raises(PlanError):
        service.execute({"start": "1990-01", "end": "1990-12", "frequency": "monthly",
                         "columns": [{"name": "base", "metric_id": metric, "dimensions": {"index_code": "XUMAL"}}]})
