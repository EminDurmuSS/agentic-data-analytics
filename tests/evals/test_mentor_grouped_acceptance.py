"""Counterexamples for the bounded grouped-trial checker, using invented data."""
import copy

import pytest

from evals.mentor_grouped_acceptance import PERIODS, chart_acceptance, source_proof_valid


def chart_fixture(kind):
    groups = {group: f"Test group {group}" for group in range(2, 11)}
    columns = [f"profit_{group}" for group in groups]
    expected = {(period, group): group * (index + 1) for group in groups for index, period in enumerate(PERIODS)}
    manifest = {"schema": {column: {"scope": {"dimensions": {"group_code": group}}}
                           for column, group in zip(columns, groups)}}
    series = [{"column": column, "label": f"Profit · {groups[group]}", "unit": "milyon TL", "raw_unit": "milyon TL",
               "values": [expected[period, group] for period in PERIODS],
               "raw_values": [expected[period, group] for period in PERIODS]}
              for column, group in zip(columns, groups)]
    chart = {"spec": {"kind": kind, "columns": columns[:], "normalize": "none", "layout": "overlay"},
             "periods": PERIODS[:], "series": series, "complete": True}
    if kind == "heatmap":
        chart["categories"] = [item["label"] for item in series]
        chart["cells"] = [{"column": column, "dimensions": {}, "period": period, "period_index": index,
                           "category_index": category, "raw_value": expected[period, group],
                           "value": expected[period, group], "source_row_available": True}
                          for category, (column, group) in enumerate(zip(columns, groups))
                          for index, period in enumerate(PERIODS)]
    return chart, manifest, expected, columns, groups


@pytest.mark.parametrize("kind", ["line", "bar", "heatmap"])
def test_nine_group_control_passes_but_six_group_chart_cannot(kind):
    args = chart_fixture(kind)
    assert all(chart_acceptance(*args)[0].values())
    chart = args[0]
    chart["spec"]["columns"] = chart["spec"]["columns"][:6]
    chart["series"] = chart["series"][:6]
    if kind == "heatmap":
        chart["cells"] = [cell for cell in chart["cells"] if cell["category_index"] < 6]
        chart["categories"] = chart["categories"][:6]
    checks, _ = chart_acceptance(*args)
    assert not checks["all_selected_group_period_cells_charted"]
    assert not checks["selected_measure_columns"]


@pytest.mark.parametrize("mutation", ["amount", "unit", "label", "category", "period"])
def test_correct_heatmap_raw_values_cannot_hide_wrong_display_or_identity(mutation):
    args = chart_fixture("heatmap")
    chart = args[0]
    if mutation == "amount":
        chart["cells"][0]["value"] *= 1000
    elif mutation == "unit":
        chart["series"][0]["unit"] = "bin TL"
    elif mutation == "label":
        chart["categories"][0] = "Unrelated group"
    elif mutation == "category":
        chart["cells"][0]["category_index"] = 1
    else:
        chart["cells"][0]["period_index"] = 1
    assert not all(chart_acceptance(*args)[0].values())


def proof_fixture():
    periods = ["2025-11", "2025-12", *PERIODS]
    sources = {period: {"path": f"raw/{period}.json", "sha256": f"test-hash-{period}"} for period in periods}
    cumulative = {(period, 2): value for period, value in zip(periods, [100, 140, 60, 130, 220])}
    monthly = {(period, 2): value for period, value in zip(periods[1:], [40, 60, 70, 90])}
    addresses = {(period, 2): 9 for period in periods}
    cells = {period: {"native_period": period, "group_code": 2, "source_sha256": source["sha256"],
                      "source_file": source["path"], "source_row_index": 9, "value_dimension": "Toplam",
                      "source_value": cumulative[period, 2]} for period, source in sources.items()}
    proof = {"binding": {"source_transformation": "difference_within_calendar_year"},
             "dimensions": {"group_code": 2}, "cells": {}}
    for index, period in enumerate(periods[1:], 1):
        cell = {**cells[period], "computed_value": monthly[period, 2]}
        if period != "2026-01":
            cell["previous_cumulative_source_cells"] = [copy.deepcopy(cells[periods[index - 1]])]
        proof["cells"][period] = [cell]
    manifest = {"lineage": {"sources": {"value": proof}}}
    return manifest, sources, cumulative, addresses, monthly


@pytest.mark.parametrize("mutation", ["prior_hash", "prior_period", "current_period", "group", "missing_warmup"])
def test_correct_computed_amounts_require_bound_current_prior_and_warmup_sources(mutation):
    args = proof_fixture()
    assert source_proof_valid(*args, require_warmup=True)
    proof = args[0]["lineage"]["sources"]["value"]
    if mutation == "prior_hash":
        proof["cells"]["2026-02"][0]["previous_cumulative_source_cells"][0]["source_sha256"] = "wrong"
    elif mutation == "prior_period":
        proof["cells"]["2026-03"][0]["previous_cumulative_source_cells"] = copy.deepcopy(
            proof["cells"]["2026-02"][0]["previous_cumulative_source_cells"])
    elif mutation == "current_period":
        copied = copy.deepcopy(proof["cells"]["2026-02"][0])
        copied["computed_value"] = args[-1]["2026-01", 2]
        copied.pop("previous_cumulative_source_cells")
        proof["cells"]["2026-01"] = [copied]
    elif mutation == "group":
        proof["dimensions"]["group_code"] = 3
    else:
        del proof["cells"]["2025-12"]
    assert not source_proof_valid(*args, require_warmup=True)
