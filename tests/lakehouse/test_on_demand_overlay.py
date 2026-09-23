"""On-demand EVDS overlay: an explicitly acquired series becomes executable; nothing else leaks."""
import duckdb
import pandas as pd
import pytest

from agentic_analytics.lakehouse import registry
from agentic_analytics.lakehouse.registry import get_bindings
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore
from data_pipeline.catalog import build_unified_catalog as catalog
from data_pipeline.evds.acquisition import OVERLAY_COLUMNS, publish_overlay


CODE = "TP.BKR.TRY.17"
METRIC = "evds:" + CODE
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
RATES = [33.59, 34.27, 39.69, 39.73, 39.43, 40.12]
PLAN = {"start": "2026-01", "end": "2026-06", "frequency": "monthly",
        "columns": [{"name": "rate", "metric_id": METRIC, "dimensions": {}}]}


def metadata_only(code, title, unit="Ağırlıklı ortalama", group="Kredi Faiz Oranları (Stok)"):
    return catalog.make_metric(
        metric_id="evds:" + code, dataset_id="evds.public_series_catalog", source_system="TCMB_EVDS",
        source_organization="TCMB", competition_scope="explicit_source_metadata_only", source_metric_code=code,
        metric_name_tr=title, group_name=group, native_frequency="AYLIK", unit=unit, default_aggregation="avg",
        observation_available=False, observation_count=0, missing_observation_count=0,
        quality_status="metadata_only", notes="Observation values are not stored locally.",
        source_asset="data_pipeline/catalog/evds_series_catalog.parquet")


def seed(tmp_path, *metrics):
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.register("metric_frame", pd.DataFrame(list(metrics), columns=catalog.METRIC_COLUMNS))
        connection.execute("CREATE TABLE catalog.metrics AS SELECT * FROM metric_frame")
        connection.unregister("metric_frame")
        connection.execute("""CREATE TABLE catalog.metric_bindings AS SELECT metric_id, 'metadata_only' AS status,
            '1.0.0' AS contract_version, CAST(NULL AS VARCHAR) AS binding_json FROM catalog.metrics""")
    return database


def service(database, tmp_path):
    store = LakehouseStore(tmp_path / "lakehouse")
    store.create_workspace(store.publish_snapshot(database)["snapshot_id"], "workspace_overlay")
    return LakehouseService(store, "workspace_overlay")


def bindings(database):
    with duckdb.connect(str(database), read_only=True) as connection:
        return get_bindings(connection)


def acquired(code, values, *, missing=False):
    return pd.DataFrame([{
        "series_code": code, "period": month, "period_start": month + "-01", "period_end": month + "-28",
        "value": None if missing else value, "is_missing": missing,
        "missing_kind": "source_null" if missing else "observed", "native_frequency": "AYLIK",
        "dataset_id": "evds.on_demand.fixture", "fetched_at": pd.Timestamp("2026-09-23 13:34:39"),
        "source_manifest_sha256": "a" * 64, "source_response_sha256": "b" * 64,
    } for month, value in zip(MONTHS, values)], columns=OVERLAY_COLUMNS)


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    path = tmp_path / "runtime" / "on_demand.duckdb"
    monkeypatch.setenv("EVDS_ON_DEMAND_DB", str(path))
    return path


def test_overlay_location_is_resolved_when_used(tmp_path, monkeypatch):
    monkeypatch.delenv("EVDS_ON_DEMAND_DB", raising=False)
    assert registry.on_demand_db_path() == registry.REPO_ROOT / ".lakehouse-runtime" / "on_demand.duckdb"
    monkeypatch.setenv("EVDS_ON_DEMAND_DB", str(tmp_path / "elsewhere.duckdb"))
    assert registry.on_demand_db_path() == tmp_path / "elsewhere.duckdb"


def test_metadata_only_series_stays_blocked_until_acquired(tmp_path, overlay):
    lakehouse = service(seed(tmp_path, metadata_only(CODE, "Taşıt Kredisi (TL, Stok, %)")), tmp_path)
    with pytest.raises(PlanError) as blocked:
        lakehouse.execute(PLAN)
    assert blocked.value.code == "METADATA_ONLY"
    assert f"acquire_evds_series with series_codes=['{CODE}']" in str(blocked.value)


def test_acquired_series_executes_with_the_source_values(tmp_path, overlay):
    lakehouse = service(seed(tmp_path, metadata_only(CODE, "Taşıt Kredisi (TL, Stok, %)")), tmp_path)
    publish_overlay(acquired(CODE, RATES), overlay_path=overlay)
    result = lakehouse.execute(PLAN)
    assert result["row_count"] == 6
    assert [row["rate"] for row in result["preview"]] == RATES


def test_acquired_binding_discloses_that_semantics_were_inferred(tmp_path, overlay):
    database = seed(tmp_path, metadata_only(CODE, "Taşıt Kredisi (TL, Stok, %)"))
    publish_overlay(acquired(CODE, RATES), overlay_path=overlay)
    binding = bindings(database)[METRIC]
    assert (binding["status"], binding["kind"], binding["unit"]) == ("ready", "rate", "percent")
    assert binding["table"] == "on_demand.evds.on_demand_observations"
    assert binding["filters"] == {"series_code": CODE}
    assert "not individually hand-reviewed" in binding["unit_evidence"]
    assert {"source_manifest_sha256", "source_response_sha256"} <= set(binding["provenance_columns"])


def test_all_missing_acquisition_is_not_numeric(tmp_path, overlay):
    lakehouse = service(seed(tmp_path, metadata_only(CODE, "Taşıt Kredisi (TL, Stok, %)")), tmp_path)
    publish_overlay(acquired(CODE, RATES, missing=True), overlay_path=overlay)
    with pytest.raises(PlanError) as blocked:
        lakehouse.execute(PLAN)
    assert blocked.value.code == "NO_NUMERIC_VALUES"


def test_uninferable_meaning_is_not_marked_ready(tmp_path, overlay):
    code = "TP.TLDTHVADE.KB3"
    database = seed(tmp_path, metadata_only(code, "1.3. Türk Lirası 3 Aya Kadar Vadeli", unit="bin TL",
                                            group="Mevduatın Vade Yapısı"))
    publish_overlay(acquired(code, [1.0] * 6), overlay_path=overlay)
    binding = bindings(database)["evds:" + code]
    assert binding["status"] == "review_required"
    assert binding["blocked_reason"]


def test_refetch_replaces_only_the_periods_it_carries(tmp_path, overlay):
    other = "TP.TRY.MT02.S"
    publish_overlay(acquired(CODE, RATES), overlay_path=overlay)
    revised = acquired(CODE, RATES).iloc[[5]].assign(value=40.5)
    publish_overlay(pd.concat([revised, acquired(other, [50.0] * 6)]), overlay_path=overlay)
    with duckdb.connect(str(overlay), read_only=True) as connection:
        values = dict(connection.execute("SELECT period, value FROM evds.on_demand_observations "
                                         "WHERE series_code=? ORDER BY period", [CODE]).fetchall())
        series = connection.execute("SELECT count(DISTINCT series_code) FROM evds.on_demand_observations").fetchone()[0]
    assert values == dict(zip(MONTHS, RATES[:5] + [40.5]))
    assert series == 2
