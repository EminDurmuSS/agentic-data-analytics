"""A captured official bulletin remains source evidence, not an inferred index."""
import hashlib
from html import escape

import duckdb
import pytest

from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.shared import SharedLakehouse
from agentic_analytics.lakehouse.store import LakehouseStore
from tools.import_tuik_cpi_bulletin import MAX_CAPTURE, SOURCE_URL, parse_capture, publish


LABELS = (
    "Bir önceki aya göre değişim oranı",
    "Bir önceki yılın Aralık ayına göre değişim oranı",
    "Bir önceki yılın aynı ayına göre değişim oranı",
    "On iki aylık ortalamalara göre değişim oranı",
)


def capture(*, values=("1,23", "23,45", "23,45", "27,89"), duplicate=False):
    # Deliberately NOT the live bulletin's figures: an importer must read cells.
    rows = [["", "Aralık 2025", "Aralık 2024", "Aralık 2023"]]
    rows.extend([[label, value, "12,34", "56,78"] for label, value in zip(LABELS, values)])
    table = "<table>" + "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows
    ) + "</table>"
    return (
        "<main><h1>Tüketici Fiyat Endeksi, Aralık 2025</h1>"
        "<p>Sayı: 58294</p><p>Yayım Tarihi: 05 Ocak 2026 10:00</p>"
        "<p>TÜFE'deki (2003=100) değişim oranları.</p>"
        "<h2>TÜFE değişim oranları (%), Aralık 2025</h2>" + table * (2 if duplicate else 1) + "</main>"
    ).encode("utf-8")


def seeded_store(tmp_path):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("CREATE TABLE seed(value INTEGER)")
    store = LakehouseStore(tmp_path / "lakehouse")
    snapshot = store.publish_snapshot(database)["snapshot_id"]
    store.create_workspace(snapshot, "workspace_import")
    return store, snapshot


def test_reads_source_percentages_and_original_cell_coordinates_without_recalculation():
    raw = capture()
    parsed = parse_capture(raw)
    assert parsed["values"] == {
        "monthly_change": "1.23",
        "year_to_date_change": "23.45",
        "annual_change": "23.45",
        "twelve_month_average_change": "27.89",
    }
    assert parsed["period"] == "2025-12"
    assert parsed["base_year"] == "2003=100"
    assert parsed["published_at"] == "2026-01-05T10:00:00"
    assert parsed["publication_timezone"] == "Europe/Istanbul"
    assert parsed["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert parsed["source_url"] == SOURCE_URL
    assert parsed["origins"]["annual_change"] == {
        "html_table": 1, "source_row": 4, "source_column": 2,
        "row_label": LABELS[2], "column_label": "Aralık 2025",
        "original_value": "23,45", "transform": "decimal_comma_to_point_only",
    }
    assert parsed["original_rows"][3] == [LABELS[2], "23,45", "12,34", "56,78"]


def test_accepts_actual_negative_change_without_converting_it_to_positive():
    parsed = parse_capture(capture(values=("-0,25", "-1,50", "-1,50", "0,00")))
    assert parsed["values"]["monthly_change"] == "-0.25"
    assert parsed["values"]["annual_change"] == "-1.50"


@pytest.mark.parametrize(("old", "new"), [
    ("Tüketici Fiyat Endeksi, Aralık 2025", "Tüketici Fiyat Endeksi, Kasım 2025"),
    ("Sayı: 58294", "Sayı: 00000"),
    ("TÜFE'deki (2003=100)", "TÜFE'deki (2025=100)"),
    ("TÜFE değişim oranları (%)", "TÜFE endeks seviyeleri"),
    ("05 Ocak 2026 10:00", "06 Ocak 2026 10:00"),
    ("05 Ocak 2026 10:00", "05 Ocak 2026 11:00"),
    ("Yayım Tarihi:", "Doğrulanmamış tarih:"),
    ("<td>Aralık 2025</td>", "<td>Kasım 2025</td>"),
    ("Bir önceki aya göre değişim oranı", "Bir önceki yıla göre endeks seviyesi"),
    ("1,23", "1.23"),
    ("1,23", "=1+2"),
    ("1,23", "NaN"),
    ("1,23", ""),
    ("1,23", "-100,00"),
    ("1,23", "1000,00"),
    ("12,34", ".."),  # Even comparison-year cells cannot silently change meaning.
    ("<td>56,78</td>", ""),
])
def test_rejects_changed_identity_date_table_shape_labels_or_ambiguous_cells(old, new):
    raw = capture().decode("utf-8").replace(old, new).encode("utf-8")
    with pytest.raises(ValueError):
        parse_capture(raw)


@pytest.mark.parametrize("raw", [b"", b"\xff", b"x" * (MAX_CAPTURE + 1), capture(duplicate=True)])
def test_rejects_empty_invalid_utf8_oversized_or_duplicate_capture(raw):
    with pytest.raises(ValueError):
        parse_capture(raw)


def test_rejects_contradictory_december_annual_and_year_to_date_values():
    with pytest.raises(ValueError, match="contradict"):
        parse_capture(capture(values=("1,23", "23,45", "23,46", "27,89")))


def test_invalid_capture_creates_no_source_dataset_or_shared_release(tmp_path):
    store, _ = seeded_store(tmp_path)
    before = store.workspace("workspace_import")
    files = sorted(str(path.relative_to(store.root)) for path in store.root.rglob("*") if path.is_file())
    with pytest.raises(ValueError):
        publish(store, "workspace_import", capture(duplicate=True))
    assert store.workspace("workspace_import") == before
    assert sorted(str(path.relative_to(store.root)) for path in store.root.rglob("*") if path.is_file()) == files


def test_publication_preserves_capture_is_idempotent_and_executes_as_percentage_rates(tmp_path):
    store, snapshot = seeded_store(tmp_path)
    before = store.create_workspace(snapshot, "workspace_before")
    raw = capture()
    published = publish(store, "workspace_import", raw)
    assert published["publication_performed"] is True
    assert published["row_count"] == 1
    assert len(published["metric_ids"]) == 4
    assert store.workspace("workspace_before") == before
    source_workspace = store.workspace("workspace_import")
    shared = SharedLakehouse(store)
    assert shared.promotion_file(published["promotion_id"], "source/raw.bin").read_bytes() == raw
    repeat = publish(store, "workspace_import", raw)
    assert repeat["publication_performed"] is False
    assert repeat["promotion_id"] == published["promotion_id"]
    assert repeat["shared_release_id"] == published["shared_release_id"]
    assert store.workspace("workspace_import") == source_workspace
    assert len(list((shared.root / "promotions").glob("promotion_*"))) == 1

    restarted = LakehouseStore(store.root)
    release = SharedLakehouse(restarted).current_release()
    restarted.create_workspace(snapshot, "workspace_after", initial_dataset_ids=release["dataset_ids"],
                               shared_release_id=release["release_id"])
    service = LakehouseService(restarted, "workspace_after")
    found = service.discover({"query": "TÜİK TÜFE Aralık 2025", "limit": 10})["metrics"]
    assert set(published["metric_ids"]) <= {metric["metric_id"] for metric in found}
    metric_by_column = {metric.rsplit(":", 1)[-1]: metric for metric in published["metric_ids"]}
    for metric in published["metric_ids"]:
        descriptor = service.describe({"metric_id": metric})["metric"]
        assert descriptor["kind"] == "rate"
        assert descriptor["unit"] == "percent"
        assert descriptor["scale"] == 1
        assert descriptor["native_frequency"] == "monthly"
        assert descriptor["aggregation"] == "none"
    columns = [{"name": column, "metric_id": metric, "dimensions": {}}
               for column, metric in metric_by_column.items()]
    result = service.execute({"start": "2025-12", "end": "2025-12", "frequency": "monthly", "columns": columns})
    assert result["row_count"] == 1
    assert {key: result["preview"][0][key] for key in metric_by_column} == {
        "monthly_change": 1.23, "year_to_date_change": 23.45,
        "annual_change": 23.45, "twelve_month_average_change": 27.89,
    }
    proof = service.explain_value({"analysis_id": result["analysis_id"], "column": "annual_change", "period": "2025-12"})
    provenance = proof["lineage"]["document_provenance"]
    assert provenance["source_url"] == SOURCE_URL
    assert provenance["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["base_year"] == "2003=100"
    assert provenance["reported_period"] == "2025-12"
    assert provenance["published_at"] == "2026-01-05T10:00:00"
    assert provenance["capture"]["original_http_response"] is False
    assert provenance["capture"]["authentication_required"] is False
    assert provenance["capture"]["method"] == "operator_observed_anonymous_browser_rendered_dom"
    assert provenance["numeric_verification"] == "original_rendered_html_table_cells_not_snippets"
    assert provenance["cell_origins"][0]["annual_change"]["original_value"] == "23,45"
    assert "endeks seviyesi değildir" in provenance["semantic_warning"]
    inspected = DocumentTools(store, "workspace_import", searxng_url=False).inspect_source(source_id=provenance["source_id"])
    table = inspected["tables"][0]
    assert table["row_count"] == 1
    assert table["preview"][0]["annual_change"] == "23.45"
    assert table["source_header_quotes"]["annual_change"] == LABELS[2]
