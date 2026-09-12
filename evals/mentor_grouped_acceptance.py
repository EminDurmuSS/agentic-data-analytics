"""Bounded offline acceptance of saved three-turn BDDK group chart trials.

The oracle reads original table02 row53 TOTAL cells, never the application
analytics table. It checks all nine bank categories; the sector aggregate is
optional unless present in the saved selection. No provider or backend calls
are made and the general mentor grader is not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evals.mentor_grading import ReceiptReader, close, digest, matches_rows, read_json


PERIODS = ["2026-01", "2026-02", "2026-03"]


def source_oracle(repo):
    config_path = repo / "data_pipeline/bddk/monthly_all_groups/request_config.json"
    groups = {int(code): label for code, label in read_json(config_path)["groups"].items()}
    sources, cumulative, addresses = {}, {}, {}
    for period in ["2025-11", "2025-12", *PERIODS]:
        candidates = [path for path in (config_path.parent / "raw").glob(period + "_table02_groups*.json")
                      if not path.name.endswith("_info.json")]
        assert len(candidates) == 1, (period, candidates)
        path = candidates[0]
        document = read_json(path)["Json"]
        if isinstance(document, str):
            document = json.loads(document)
        assert "milyon" in document["caption"].lower() and "TL" in document["caption"]
        sources[period] = {"path": str(path.relative_to(repo)), "sha256": digest(path), "caption": document["caption"]}
        for group, label in groups.items():
            cells = [(index, item["cell"]) for index, item in enumerate(document["data"]["rows"], 1)
                     if item["cell"][0] == label and str(item["cell"][1]) == "53"]
            assert len(cells) == 1, (period, group)
            index, cell = cells[0]
            cumulative[period, group] = cell[-1]
            addresses[period, group] = index
    monthly = {("2025-12", group): cumulative["2025-12", group] - cumulative["2025-11", group] for group in groups}
    differences = {}
    for index, period in enumerate(PERIODS):
        for group in groups:
            monthly[period, group] = cumulative[period, group] - (cumulative[PERIODS[index-1], group] if index else 0)
            prior = PERIODS[index-1] if index else "2025-12"
            differences[period, group] = monthly[period, group] - monthly[prior, group]
    return groups, sources, cumulative, addresses, monthly, differences, digest(config_path)


def scoped_group(manifest, column, dimensions=None):
    return (dimensions or {}).get("group_code") or manifest["schema"].get(column, {}).get("scope", {}).get("dimensions", {}).get("group_code")


def source_columns(manifest):
    if manifest.get("lineage", {}).get("group_by"):
        return ["value"]
    return [item["name"] for item in manifest["plan"].get("columns", [])]


def data_cells(manifest, rows, columns):
    cells = {}
    for row in rows:
        for column in columns:
            group = scoped_group(manifest, column, {"group_code": row.get("group_code")})
            key = (row["period"], group)
            assert key not in cells
            cells[key] = row[column]
    return cells


def source_proof_valid(manifest, sources, cumulative, addresses, monthly, *, require_warmup=False):
    lineage = manifest["lineage"]
    proofs = list(lineage.get("sources", {}).values())
    for group in lineage.get("groups", {}).values():
        proofs.extend(group.get("sources", {}).values())
    if not proofs:
        return False
    def valid(cell):
        key = (cell.get("native_period"), cell.get("group_code"))
        if key not in cumulative:
            return False
        source = sources[key[0]]
        return (cell.get("source_sha256") == source["sha256"]
                and Path(cell.get("source_file", "")).name == Path(source["path"]).name
                and cell.get("source_row_index") == addresses[key]
                and cell.get("value_dimension") == "Toplam" and close(cell.get("source_value"), cumulative[key]))
    for proof in proofs:
        if proof.get("binding", {}).get("source_transformation") != "difference_within_calendar_year":
            return False
        for index, period in enumerate(PERIODS):
            cells = proof.get("cells", {}).get(period, [])
            if len(cells) != 1 or not valid(cells[0]):
                return False
            cell = cells[0]
            if (cell["native_period"] != period
                    or cell["group_code"] != proof.get("dimensions", {}).get("group_code")
                    or not close(cell.get("computed_value"), monthly[period, cell["group_code"]])):
                return False
            previous = cell.get("previous_cumulative_source_cells", [])
            if index:
                if (len(previous) != 1 or not valid(previous[0])
                        or previous[0]["native_period"] != PERIODS[index - 1]
                        or previous[0]["group_code"] != cell["group_code"]):
                    return False
            elif previous:
                return False
        if require_warmup and "2025-12" not in proof.get("cells", {}):
            return False
        if "2025-12" in proof.get("cells", {}):
            cells = proof["cells"]["2025-12"]
            if (len(cells) != 1 or not valid(cells[0]) or cells[0]["native_period"] != "2025-12"
                    or cells[0]["group_code"] != proof.get("dimensions", {}).get("group_code")
                    or not close(cells[0]["computed_value"], monthly["2025-12", cells[0]["group_code"]])):
                return False
            previous = cells[0].get("previous_cumulative_source_cells", [])
            if (len(previous) != 1 or not valid(previous[0])
                    or previous[0]["native_period"] != "2025-11"
                    or previous[0]["group_code"] != cells[0]["group_code"]):
                return False
    return True


def chart_acceptance(chart, manifest, expected, columns, groups):
    checks = {"selected_measure_columns": set(chart["spec"]["columns"]) == set(columns),
              "source_periods_unchanged": chart["periods"] == PERIODS,
              "no_normalization_or_stacking": chart["spec"].get("normalize") == "none"
                  and chart["spec"].get("layout") == "overlay" and chart.get("complete") is True,
              "million_TRY_chart_units": all(item.get("unit") == item.get("raw_unit") == "milyon TL" for item in chart["series"])}
    shown, labels = {}, []
    if chart["spec"]["kind"] == "heatmap":
        for cell in chart.get("cells", []):
            group = scoped_group(manifest, cell["column"], cell.get("dimensions"))
            key = (cell["period"], group)
            if key in shown:
                checks["no_duplicate_chart_cells"] = False
            shown[key] = cell["raw_value"]
            checks.setdefault("display_equals_raw", True)
            checks["display_equals_raw"] &= close(cell["value"], cell["raw_value"]) and cell.get("source_row_available") is True
            category, period_index = cell.get("category_index"), cell.get("period_index")
            categories = chart.get("categories", [])
            checks.setdefault("heatmap_coordinates_name_source_group_and_period", True)
            checks["heatmap_coordinates_name_source_group_and_period"] &= (
                type(category) is int and 0 <= category < len(categories)
                and group in groups and groups[group] in categories[category]
                and type(period_index) is int and 0 <= period_index < len(chart["periods"])
                and chart["periods"][period_index] == cell["period"])
        labels = chart.get("categories", [])
        if chart.get("group_mode") == "matrix":
            flat = chart.get("cells", [])
            checks["matrix_series_matches_cells"] = len(chart["series"]) == 1 and all(
                len(chart["series"][0].get(field, [])) == len(flat) and all(close(a, cell[key]) for a, cell in zip(chart["series"][0][field], flat))
                for field, key in (("raw_values", "raw_value"), ("values", "value")))
    if chart["spec"]["kind"] != "heatmap" or chart.get("group_mode") != "matrix":
        for series in chart["series"]:
            group = scoped_group(manifest, series["column"], series.get("dimensions"))
            labels.append(series["label"]) if chart["spec"]["kind"] != "heatmap" else None
            checks.setdefault("series_values_and_group_names", True)
            checks["series_values_and_group_names"] &= (len(series["raw_values"]) == len(series["values"]) == 3
                and group in groups and groups[group] in series["label"])
            for period, raw, value in zip(chart["periods"], series["raw_values"], series["values"]):
                key = (period, group)
                if chart["spec"]["kind"] != "heatmap":
                    if key in shown:
                        checks["no_duplicate_chart_cells"] = False
                    shown[key] = raw
                checks["series_values_and_group_names"] &= close(raw, value) and close(raw, expected.get(key))
    checks["all_selected_group_period_cells_charted"] = set(shown) == set(expected)
    checks["chart_values_match_source_oracle"] = all(key in expected and close(value, expected[key]) for key, value in shown.items())
    checks["distinct_readable_group_labels"] = len(labels) == len({group for _, group in expected}) and len(set(labels)) == len(labels)
    return checks, sorted({group for _, group in shown})


def evaluate(round_path, repo):
    groups, sources, cumulative, addresses, monthly, differences, config_hash = source_oracle(repo)
    reader = ReceiptReader(round_path)
    trials = []
    for trial_path in sorted(round_path.glob("grouped_*")):
        if not trial_path.is_dir():
            continue
        turns, previous_manifest, previous_rows = [], None, None
        for number, kind in ((1, "line"), (2, "bar"), (3, "heatmap")):
            path = trial_path / f"turn-{number}.json"
            turn = read_json(path)
            result = turn["result"]
            manifest, rows = reader.object("analyses", result["analysis_id"])
            chart = reader.receipt("charts", result["chart_id"], result["workspace_id"], manifest)
            base = source_columns(manifest)
            observed = data_cells(manifest, rows, base)
            selected_groups = {group for _, group in observed}
            checks = {"immutable_receipt_hashes": True, "run_completed": result.get("status") == "completed",
                      "collector_export_matches_saved_bytes": matches_rows(turn["analyses"][manifest["analysis_id"]]["rows"], rows),
                      "all_nine_bank_categories_present": set(groups)-{10001} <= selected_groups <= set(groups),
                      "every_selected_group_has_three_months": set(observed) == {(period, group) for period in PERIODS for group in selected_groups},
                      "monthly_profits_match_original_raw_cells": all(key in monthly and close(value, monthly[key]) for key, value in observed.items()),
                      "raw_source_lineage_verified": source_proof_valid(manifest, sources, cumulative, addresses, monthly, require_warmup=number >= 2),
                      "requested_chart_kind": chart["spec"]["kind"] == kind}
            for column in base:
                meta = manifest["schema"][column]
                checks.setdefault("source_money_semantics", True)
                checks["source_money_semantics"] &= meta.get("unit") == meta.get("currency") == "TRY" and close(meta.get("scale"), 1000000) and meta.get("kind") == "flow"
            columns, expected = base, {key: monthly[key] for key in observed}
            if number >= 2:
                operations = manifest["plan"].get("operations", []) or manifest["plan"].get("request", {}).get("operations", [])
                difference_ops = [op for op in operations if op.get("op") == "difference" and op.get("periods") == 1]
                columns = [op["output"] for op in difference_ops]
                values = data_cells(manifest, rows, columns)
                expected = {key: differences[key] for key in observed}
                checks["all_group_differences_correct_with_December_warmup"] = set(values) == set(expected) and all(close(value, expected[key]) for key, value in values.items())
            if number == 2:
                old_columns = list(previous_rows[0])
                checks["original_rows_columns_values_preserved"] = all(column in rows[0] for column in old_columns) and matches_rows(previous_rows, [{key: row[key] for key in old_columns} for row in rows])
                checks["original_schema_preserved"] = all(manifest["schema"].get(column) == meta or column == "rank" for column, meta in previous_manifest["schema"].items())
                checks["parent_analysis_identity"] = manifest.get("parent_analysis_id") == previous_manifest["analysis_id"]
                preserved = manifest["lineage"].get("preserved_columns", {})
                checks["original_source_bindings_preserved"] = all(preserved.get(column) == {"analysis_id": previous_manifest["analysis_id"], "column": column} for column in base)
            if number == 3:
                checks["heatmap_uses_same_analysis_bytes"] = manifest == previous_manifest and rows == previous_rows
                checks["view_change_does_not_run_data_query"] = not any(tool["tool"] in {"execute", "query_grouped", "revise_analysis", "aggregate_dataset", "publish_source_table", "ingest_source_table"} for tool in result.get("tool_results", []))
            chart_checks, displayed_groups = chart_acceptance(chart, manifest, expected, columns, groups)
            checks.update(chart_checks)
            turns.append({"turn": number, "requested_kind": kind, "turn_file_sha256": digest(path), "analysis_id": manifest["analysis_id"],
                          "data_sha256": manifest["data_sha256"], "chart_id": result["chart_id"], "selected_groups": sorted(selected_groups),
                          "displayed_groups": displayed_groups, "verified_source_monthly_cells": len(observed), "checks": checks,
                          "artifact_grade": "pass" if all(checks.values()) else "fail"})
            previous_manifest, previous_rows = manifest, rows
        trials.append({"trial": trial_path.name, "artifact_grade": "pass" if all(turn["artifact_grade"] == "pass" for turn in turns) else "fail", "turns": turns})
    return {"evidence_type": "independent_saved_autonomous_grouped_trials", "scope": "Nine bank categories must all be retained. The Sektör aggregate (10001) is optional; when selected it must also be preserved. No groups are summed.",
            "round_name": round_path.name, "round_manifest_sha256": digest(round_path / "manifest.json"),
            "general_grader_unchanged": True, "oracle_method": "Read original BDDK table02 row53 Toplam cumulative values for each source group. Monthly profit resets January, otherwise differences cumulative within year. Follow-up January difference uses December2025 monthly profit independently derived from November/December raw files.",
            "source_groups": groups, "group_config_sha256": config_hash, "raw_sources": sources,
            "oracle": [{"period": period, "group_code": group, "group_label": groups[group], "monthly_profit_million_TRY": monthly[period, group], "previous_month_difference_million_TRY": differences[period, group]} for period in PERIODS for group in sorted(groups)],
            "December_2025_monthly_warmup": {str(group): monthly["2025-12", group] for group in sorted(groups)},
            "trials": trials, "limits": "Checks use saved immutable bytes and original cached BDDK downloads; no model calls, network requests, charts or analyses are regenerated."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output folder; saved trials and earlier grades are never changed.")
    result = evaluate(args.round.resolve(), args.repo.resolve())
    result["checker_sha256"] = digest(Path(__file__))
    args.output.mkdir(parents=True)
    (args.output / "grouped-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for trial in result["trials"]:
        print(trial["trial"], trial["artifact_grade"], [{"turn": turn["turn"], "failed": [key for key, ok in turn["checks"].items() if not ok]} for turn in trial["turns"]])


if __name__ == "__main__":
    main()
