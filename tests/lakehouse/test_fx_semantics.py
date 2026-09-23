"""TCMB indicative USD/EUR rates carry reviewed price semantics: Turkish lira per one unit."""
import json

import duckdb
import pandas as pd
import pytest

from agentic_analytics.lakehouse.semantics import apply_semantic_policy
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore


def catalog_binding(code, title, status="review_required"):
    # Shape of the shipped evds:TP.DK.USD.A.YTL contract: catalog unit, unreviewed kind.
    return {"metric_id": "evds:" + code, "title": title, "source_system": "TCMB_EVDS", "source_code": code,
            "table": "fx", "time_column": "period", "value_column": "value", "filters": {"series_code": code},
            "dimensions": {}, "native_frequency": "daily", "kind": "unknown", "unit": "Türk lirası", "scale": 1.0,
            "currency": None, "aggregation": "mean", "source_base": "", "provenance_columns": ["series_code", "period"],
            "status": status, "notes": "USD TL doviz alis kuru", "contract_version": "1.0.0"}


def test_usd_buying_rate_monthly_average_is_the_mean_of_observed_days(tmp_path):
    code = "TP.DK.USD.A.YTL"
    days = pd.bdate_range("2021-01-01", "2021-02-26")
    values = [7.0 + index / 100 for index in range(len(days))]
    database = tmp_path / "seed.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA catalog")
        connection.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR, binding_json VARCHAR)")
        connection.execute("INSERT INTO catalog.metric_bindings VALUES (?, ?)",
                           ["evds:" + code, json.dumps(catalog_binding(code, "(USD)  ABD  Doları  (Döviz  Alış)"))])
        connection.register("frame", pd.DataFrame({"series_code": code, "period": days.strftime("%Y-%m-%d"),
                                                   "value": values}))
        connection.execute("CREATE TABLE fx AS SELECT * FROM frame")
    store = LakehouseStore(tmp_path / "lakehouse")
    store.create_workspace(store.publish_snapshot(database)["snapshot_id"], "workspace_fx")
    result = LakehouseService(store, "workspace_fx").execute({
        "start": "2021-01", "end": "2021-02", "frequency": "monthly",
        "columns": [{"name": "usd", "metric_id": "evds:" + code, "dimensions": {}, "alignment": "mean"}]})
    expected = pd.Series(values, index=days).groupby(days.to_period("M")).mean()
    assert [row["usd"] for row in result["preview"]] == pytest.approx(list(expected))


def test_reviewed_rate_states_its_quote_and_evidence():
    binding = apply_semantic_policy(catalog_binding("TP.DK.EUR.A.YTL", "(EUR) Euro (Döviz Alış)"))
    assert (binding["status"], binding["kind"], binding["unit"], binding["currency"], binding["scale"]) == (
        "ready", "price", "TRY/EUR", "TRY", 1.0)
    assert "tcmb.gov.tr" in binding["unit_evidence"]


def test_rates_quoted_per_hundred_units_or_without_values_are_not_upgraded():
    per_hundred = apply_semantic_policy(catalog_binding("TP.DK.JPY.A.YTL", "(JPY) Japon Yeni (Döviz Alış)"))
    assert (per_hundred["kind"], per_hundred["status"]) == ("unknown", "review_required")
    empty = apply_semantic_policy(catalog_binding("TP.DK.USD.A.YTL", "(USD) ABD Doları (Döviz Alış)", status="no_numeric"))
    assert empty["status"] == "no_numeric"
