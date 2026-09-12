"""Independently grade preserved mentor trials using saved bytes and fixed oracles.

No model, application, provider or network calls are made. Analysis numbers are
read from hash-checked Parquet, not previews, run status or model assertions.
`artifact_grade` covers the explicit checks below, not every possible claim in
the final prose. `task_complete` additionally requires successful delivery.
Earlier trial files are never changed. Use a fresh --output directory.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pandas as pd


ORACLES = {
    "profit": {"basis": "Original BDDK table02 row53, Sektör, Toplam cumulative cells, differenced within each year with January reset to zero; million TRY.",
               "monthly": {"2025": [47347, 70867, 98437, 47551, 62632, 95625],
                           "2026": [87249, 82152, 119287, 74625, 58476, 106642]},
               "cumulative": {"2025": [47347, 118214, 216651, 264202, 326834, 422459],
                              "2026": [87249, 169401, 288688, 363313, 421789, 528431]},
               "raw_sha256": {"2025": ["8851a8de695dbdddf080b1c905213cb9e4b30de30d3fd6f9cdde54202270a9e5", "d4a3b5eaca3f5aa1355e3bf28daee4fede1c06022a9a05dc6518279ac8463372", "8cfd002b1868c4ed21cb8e1789c0ffa4caf464d5d0ead16b7b335d2441b8dfe6", "eb150ee1e6aa1a6bba6b9eee7982178399a0e583ee28011fe5ef59f9f5268d4f", "4b03ce547a4b71c794210d7947bf21f19d4de5047f2caeef3a6624596fde2ae8", "ac620053f954732f72f26abd02fd102c1ce149d22f10c8e3997d5d055b74c7eb"],
                              "2026": ["cb16fc0e35ffbb5545ff8bebed8fec3059c831eee3fbe39e853224631818bab4", "896ea7fb92028976806e72fe10c869a3623dafb80cc86bdcad43c18d30b22d12", "967b44d4b988c9ac07fe5dbf39e8f7b95127cd210f8de823df2aefa2740041c8", "6a60d89ca3cbdf9380453790a26b16f36b6ca41d80917f34b0875c9f18656834", "0a247c021d974ea8ff3b73f98b72dc75f64f3896d259045900f107ca026571dc", "0d4cc56d19a740f278799faa6ce96c82c53943de974284a96378472ff7bfd698"]},
               "totals": {"2025": 422459, "2026": 528431}},
    "new_source": {"basis": "Synthetic uploaded bank values divided by full-sector BDDK balance-sheet loans, million TRY. Credit-breakdown reporting population is not the full-sector denominator.",
                   "periods": ["2026-01", "2026-02", "2026-03"], "bank": [100000, 120000, 150000],
                   "sector": [23646098, 24217659, 24907270]},
    "pdf_sector": {"basis": "Actual Garanti consolidated TOTAL ASSETS, original PDF page11 row60 TOTAL column8, divided by original BDDK March2026 full-sector balance-sheet total assets. This intentional cross-population amount comparison is not official market share.",
                   "host": "garantibbvainvestorrelations.com", "page": 11,
                   "raw_sha256": "b8dd1916b3943ffdaa06d2be75a8b7e33950812eb990ae8b1bfe601b7f99c288",
                   "source_table": "table_p000011_text_001",
                   "source_cells": {"column_8": {"date": "2026-03-31", "rows": {"60": "total_assets"}}},
                   "date": "2026-03-31", "period": "2026-03", "bank_thousand_TRY": 4783750292,
                   "bank_million_TRY": "4783750.292", "sector_million_TRY": 49735194,
                   "percent": "9.618441001758231806635759780",
                   "sector_metric_id": "bddk_monthly:table01:26:f7e2a5324c36:Toplam",
                   "sector_source_file": "data_pipeline/bddk/monthly_all_groups/raw/2026-03_table01_groups10001-10002-10003-10004-10005-10006-10007-10008-10009-10010.json",
                   "sector_raw_sha256": "a8226807f1cf87a2919f592de40a89080549e9cf7424138049ebd369714f3176",
                   "sector_source_row": 26, "sector_group_code": 10001},
    "garanti_pdf": {"basis": "Consolidated balance sheet, PDF page 11, TOTAL columns, thousand TRY.",
                    "host": "garantibbvainvestorrelations.com", "page": 11,
                    "raw_sha256":"b8dd1916b3943ffdaa06d2be75a8b7e33950812eb990ae8b1bfe601b7f99c288",
                    "source_table":"table_p000011_text_001",
                    "source_cells":{"column_8":{"date":"2026-03-31","rows":{"12":"financial_assets","13":"cash"}},
                                    "column_11":{"date":"2025-12-31","rows":{"12":"financial_assets","13":"cash"}}},
                    "values": {"financial_assets": {"2025-12-31": 1260568409, "2026-03-31": 1279933463},
                               "cash": {"2025-12-31": 1005229845, "2026-03-31": 867799356}}},
    "web": {"basis": "TCMB 6 March 2025 monetary policy decision, prior 45%, new 42.5%, actual decision page.",
            "date": "2025-03-06", "prior": 45, "new": 42.5, "release": "duy2025-15"},
    "tupras_pdf": {"basis": "Consolidated balance sheet, PDF page 3, thousand TRY at 30 June 2025 purchasing power.",
                   "host": "tupras.com.tr", "page": 3, "raw_sha256": "54c50d2e352fe71288fc9427966335c848fad7628f67ff83220d478ef91fb29a",
                   "source_table":"table_p000003_text_001",
                   "source_cells":{"column_7":{"date":"2025-06-30","rows":{"12":"cash","38":"total_assets"}}},
                   "price_basis": "2025-06-30",
                   "values": {"total_assets": {"2025-06-30": 545630257}, "cash": {"2025-06-30": 90351730}}},
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalized(value):
    return re.sub(r"\s+", " ", str(value).casefold().translate(str.maketrans("ıışğüöç", "iisguoc"))
                  .replace("i\u0307", "i").replace("_", " ")).strip()


def close(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return (isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool)
            and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9))


class EvidenceUnavailable(ValueError):
    pass


class ReceiptReader:
    """Read existing immutable objects without constructing an application store."""
    def __init__(self, round_path):
        self.root = round_path.resolve() / "runtime" / "lakehouse"

    def path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Evidence reference escapes this trial's saved runtime.")
        if not path.is_file():
            raise EvidenceUnavailable(f"Saved evidence is missing: {'/'.join(parts)}")
        return path

    def object(self, kind, identifier):
        prefix = {"analyses": "analysis", "datasets": "dataset"}[kind]
        if not re.fullmatch(prefix + r"_[0-9a-f]{64}", identifier or ""):
            raise EvidenceUnavailable(f"No saved {prefix} identifier.")
        manifest = read_json(self.path(kind, identifier, "manifest.json"))
        basis = {key: value for key, value in manifest.items() if key != prefix + "_id"}
        if prefix + "_" + hashlib.sha256(canonical(basis)).hexdigest() != identifier:
            raise ValueError(f"{prefix} manifest hash mismatch")
        payload = self.path(kind, identifier, "data.parquet")
        if digest(payload) != manifest.get("data_sha256"):
            raise ValueError(f"{prefix} Parquet hash mismatch")
        rows = pd.read_parquet(payload).astype(object)
        rows = rows.where(pd.notna(rows), None).to_dict(orient="records")
        if len(rows) != manifest.get("row_count"):
            raise ValueError(f"{prefix} row count mismatch")
        return manifest, rows

    def receipt(self, kind, identifier, workspace, manifest):
        prefix = {"charts": "chart", "summaries": "summary"}[kind]
        if not re.fullmatch(prefix + r"_[0-9a-f]{64}", identifier or ""):
            raise EvidenceUnavailable(f"No saved {prefix} identifier.")
        path = self.path(kind, workspace, identifier + ".json")
        if digest(path) != identifier.removeprefix(prefix + "_"):
            raise ValueError(f"{prefix} receipt hash mismatch")
        payload = read_json(path)
        if (payload.get("workspace_id") != workspace or payload.get("analysis_id") != manifest["analysis_id"]
                or payload.get("provenance", {}).get("data_sha256") != manifest["data_sha256"]):
            raise ValueError(f"{prefix} receipt does not reference this saved analysis")
        return payload

    def source(self, workspace, source_id, expected_hash=None):
        if not re.fullmatch(r"source_[0-9a-f]{64}", source_id or ""):
            raise EvidenceUnavailable("No registered source identifier")
        manifest = read_json(self.path("document_sources", workspace, source_id, "manifest.json"))
        identity = {key: manifest.get(key) for key in
                    ("filename", "mime_type", "source_url", "raw_sha256", "size_bytes", "workspace_id")}
        if ("source_" + hashlib.sha256(canonical(identity)).hexdigest() != source_id
                or manifest.get("source_id") != source_id or manifest.get("workspace_id") != workspace):
            raise ValueError("Registered source identity or URL does not match its manifest hash")
        path = self.path("document_sources", workspace, source_id, "raw.bin")
        actual = digest(path)
        if (actual != manifest.get("raw_sha256") or expected_hash and actual != expected_hash
                or path.stat().st_size != manifest.get("size_bytes")):
            raise ValueError("Registered source bytes do not match their receipt")
        return manifest, path


def record(checks, name, passed, detail=None, *, missing=False):
    checks.append({"check": name, "status": "pass" if passed else "incomplete" if missing else "fail",
                   **({"detail": detail} if detail is not None else {})})


def attempt(checks, name, function):
    try:
        result = function()
        record(checks, name, True)
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record(checks, name, False, str(exc), missing=isinstance(exc, (EvidenceUnavailable, FileNotFoundError)))
        return None


def matches_rows(exported, saved):
    return (len(exported) == len(saved) and all(set(a) == set(b) and all(
        close(a[key], b[key]) if isinstance(b[key], (int, float)) or b[key] is None else a[key] == b[key]
        for key in b) for a, b in zip(exported, saved)))


def series_values(rows, column, periods, dimensions=None):
    selected = [row for row in rows if all(row.get(key) == value for key, value in (dimensions or {}).items())]
    by_period = {row.get("period"): row for row in selected}
    if len(by_period) != len(selected):
        return []
    return [by_period.get(period, {}).get(column) for period in periods]


def monetary(meta, scale):
    return meta.get("unit") == "TRY" and meta.get("currency") == "TRY" and close(meta.get("scale"), scale)


def expected_chart_unit(meta):
    """Display units required by these fixed financial acceptance cases."""
    if meta.get("unit") == "TRY":
        return {1: "TL", 1000: "bin TL", 1000000: "milyon TL"}.get(meta.get("scale"))
    if meta.get("unit") == "percent" and close(meta.get("scale", 1), 1):
        return "%"
    return None


def period_end(label):
    label = str(label)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
        return label
    match = re.fullmatch(r"(\d{4})-(?:Q([1-4])|(\d{2}))", label)
    if match:
        year, quarter, month = match.groups()
        month = int(quarter) * 3 if quarter else int(month)
        if 1 <= month <= 12:
            return f"{year}-{month:02d}-{calendar.monthrange(int(year), month)[1]:02d}"
    return None


def date_in_text(text, iso):
    text = normalized(text)
    year, month, day = (int(part) for part in iso.split("-"))
    month_names = [("january","ocak"),("february","subat"),("march","mart"),("april","nisan"),
                   ("may","mayis"),("june","haziran"),("july","temmuz"),("august","agustos"),
                   ("september","eylul"),("october","ekim"),("november","kasim"),("december","aralik")][month-1]
    names = "(?:"+"|".join(month_names)+")"
    return bool(re.search(rf"(?:{iso}|0?{day}[./]0?{month}[./]{year}|0?{day}\s+{names}\s+{year}|{names}\s+0?{day},?\s+{year})",text))


def delivered_rate_change(message):
    """Accept explicit old-to-new prose or labeled rates, never a bare URL."""
    text = normalized(re.sub(r"https?://\S+", "", message)).replace("*", "")
    prior = r"(?<![\d.,])45(?:[.,]0+)?(?![\d.,])"
    new = r"(?<![\d.,])42[.,]5(?:0+)?(?![\d.,])"
    forward = re.search(prior + r"[^\d]{0,120}" + new, text)
    if forward and re.search(r"(?:ten|tan|from|\bto\b|onceki|eski|prior|old|->|→|indir|dusur|cut|decreas)",
                             text[max(0, forward.start()-45):forward.end()+55]):
        return True
    old_label = re.search(r"(?:onceki|eski|prior|previous|old)[^\d]{0,55}" + prior, text)
    new_label = re.search(r"(?:yeni|sonraki|new)[^\d]{0,55}" + new, text)
    return bool(old_label and new_label)


def chart_checks(checks, reader, result, manifest, rows, kind, required_columns=()):
    chart = attempt(checks, "chart_receipt_integrity", lambda: reader.receipt(
        "charts", result.get("chart_id"), result["workspace_id"], manifest))
    if chart is None:
        return
    record(checks, "requested_chart_kind", chart.get("spec", {}).get("kind") == kind, chart.get("spec"))
    series = chart.get("series", [])
    record(checks, "requested_columns_charted", bool(series) and set(required_columns).issubset(
        {item.get("column") for item in series}), list(required_columns))
    periods = chart.get("periods", [])
    categorical = chart.get("group_mode") == "categories"
    points = chart.get("point_dimensions", [])
    record(checks, "chart_period_coverage", bool(periods) and set(periods) == {row.get("period") for row in rows}
           and (categorical or len(periods) == len(set(periods))))
    coverage = []
    valid_columns = set(manifest.get("schema", {}))
    for index, item in enumerate(series):
        column = item.get("column")
        expected, available = [], []
        valid = column in valid_columns and (not categorical or len(points) == len(periods))
        for position, period in enumerate(periods):
            dimensions = points[position] if categorical and position < len(points) else item.get("dimensions", {})
            selected = [(i, row) for i, row in enumerate(rows) if row.get("period") == period
                        and all(row.get(key) == value for key, value in dimensions.items())]
            valid = valid and len(selected) <= 1
            expected.append(selected[0][1].get(column) if len(selected) == 1 else None)
            available.append(len(selected) == 1)
            coverage.extend((i, column) for i, _ in selected)
        actual = item.get("raw_values", [])
        record(checks, f"chart_series_{index}_matches_saved_cells", valid and bool(expected) and len(expected) == len(actual)
               and all(close(a, b) for a, b in zip(actual, expected)))
        displayed = item.get("values", [])
        expected_unit = expected_chart_unit(manifest.get("schema", {}).get(column, {}))
        record(checks, f"chart_series_{index}_display_matches_saved_cells", valid and len(displayed) == len(expected)
               and bool(expected) and all(close(a, b) for a, b in zip(displayed, expected))
               and item.get("unit") == item.get("raw_unit")
               and (expected_unit is None or item.get("unit") == expected_unit))
        if "source_row_available" in item:
            record(checks, f"chart_series_{index}_source_nulls_preserved", item["source_row_available"] == available)
    expected_coverage = {(i, item.get("column")) for i in range(len(rows)) for item in series}
    record(checks, "chart_all_saved_cells_covered_once", bool(coverage) and len(coverage) == len(set(coverage))
           and set(coverage) == expected_coverage)
    units = {item.get("unit") for item in series}
    if len(units) > 1:
        record(checks, "chart_different_units_have_distinct_scales", chart.get("spec", {}).get("layout") == "panels"
               or chart.get("spec", {}).get("layout") == "dual_axis" and len(series) == 2
               and {item.get("axis") for item in series} == {"left", "right"})
    record(checks, "chart_complete_without_reinterpreting_values", chart.get("complete") is True
           and chart.get("spec", {}).get("normalize") == "none")


def summarize_receipts(checks, reader, result, manifest):
    summaries = []
    seen = set()
    for tool in result.get("tool_results", []):
        payload = tool.get("result", {})
        sid = payload.get("summary_id") or payload.get("artifact_id")
        if (tool.get("tool") != "summarize_analysis" or payload.get("status") != "ok"
                or payload.get("analysis_id") != manifest["analysis_id"] or sid in seen):
            continue
        seen.add(sid)
        summary = attempt(checks, "summary_receipt_integrity", lambda: reader.receipt(
            "summaries", sid, result["workspace_id"], manifest))
        if summary:
            summaries.append(summary)
    return [fact for summary in summaries for fact in summary.get("facts", [])]


def grade_profit(checks, reader, result, manifest, rows):
    periods = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 7)]
    schema = manifest.get("schema", {})
    candidates = [name for name, meta in schema.items() if meta.get("kind") == "flow" and monetary(meta, 1000000)]
    totals = ORACLES["profit"]["totals"]
    correct = []
    for name in candidates:
        values = series_values(rows, name, periods)
        expected = [value for year in ("2025", "2026") for value in ORACLES["profit"]["monthly"][year]]
        if len(values) == 12 and all(close(value, oracle) for value, oracle in zip(values, expected)):
            correct.append(name)
    record(checks, "twelve_independent_monthly_profit_cells", bool(correct),
           {"expected": ORACLES["profit"]["monthly"], "observed": {name: series_values(rows, name, periods) for name in candidates}})
    record(checks, "six_month_non_cumulative_profit_totals", bool(correct), {"expected_million_TRY": totals, "matching_columns": correct})
    facts = summarize_receipts(checks, reader, result, manifest)
    for year, expected in totals.items():
        record(checks, f"delivered_{year}_first_half_total", any(f.get("column") in correct and f.get("statistic") == "sum"
               and f.get("period_start") == year+"-01" and f.get("period_end") == year+"-06"
               and close(f.get("value"), expected) and f.get("unit") == "TRY" and close(f.get("scale"), 1000000)
               for f in facts), missing=not facts)
    expected_growth = (Decimal(totals["2026"]) / Decimal(totals["2025"]) - 1) * 100
    record(checks, "delivered_year_on_year_total_growth", any(f.get("column") in correct and f.get("statistic") == "window_growth"
           and f.get("basis_statistic") == "sum" and f.get("period_start") == "2025-01"
           and f.get("period_end") == "2026-06" and f.get("unit") == "percent"
           and close(f.get("value"), expected_growth) for f in facts), float(expected_growth), missing=not facts)


def grade_new_source(checks, reader, result, manifest, rows):
    oracle = ORACLES["new_source"]
    schema = manifest.get("schema", {})
    columns = {}
    for role in ("bank", "sector"):
        candidates = [name for name, meta in schema.items() if monetary(meta, 1000000) and meta.get("kind") == "stock"
                      and (str(meta.get("metric_id", "")).startswith("overlay:") if role == "bank" else
                           str(meta.get("metric_id", "")).startswith("bddk_monthly:"))]
        columns[role] = next((name for name in candidates if all(close(a, b) for a, b in zip(
            series_values(rows, name, oracle["periods"]), oracle[role])) and len(series_values(rows,name,oracle["periods"])) == 3), None)
        record(checks, role+"_balance_values", columns[role] is not None,
               {"expected":oracle[role],"observed":{name:series_values(rows,name,oracle["periods"]) for name in candidates}})
    expected = [Decimal(bank) / Decimal(sector) * 100 for bank, sector in zip(oracle["bank"], oracle["sector"])]
    ratio = next((name for name, meta in schema.items() if meta.get("unit") == "percent" and all(
        close(a,b) for a,b in zip(series_values(rows,name,oracle["periods"]),expected))
        and len(series_values(rows,name,oracle["periods"])) == 3), None)
    record(checks, "bank_divided_by_full_sector_percent", ratio is not None, [float(value) for value in expected])
    operations = manifest.get("lineage", {}).get("operations", [])
    record(checks, "ratio_has_explicit_source_scope", any(op.get("op") == "ratio" and op.get("column") == columns["bank"]
           and op.get("denominator") == columns["sector"] and op.get("output") == ratio and close(op.get("multiplier"),100)
           and op.get("scope_policy") == "explicit_comparison" for op in operations))
    datasets = source_checks(checks, reader, result, manifest)
    bank_metric = str(schema.get(columns["bank"], {}).get("metric_id", ""))
    bank_dataset = bank_metric.split(":")[1] if bank_metric.startswith("overlay:") else None
    record(checks, "bank_series_uses_published_source", bank_dataset in datasets, bank_dataset, missing=not bank_dataset)
    sector_source = manifest.get("lineage", {}).get("sources", {}).get(columns["sector"], {}).get("binding", {})
    if sector_source.get("population_scope") or sector_source.get("measurement_basis"):
        record(checks, "sector_population_and_measurement_basis",
               sector_source.get("population_scope", {}).get("id") == "bddk_monthly_full_reporting_population"
               and not sector_source.get("population_scope", {}).get("exclusions")
               and sector_source.get("measurement_basis") == "source_reported", sector_source.get("population_scope"))
    chart_checks(checks, reader, result, manifest, rows, "line", [ratio] if ratio else [])
    record(checks, "synthetic_source_is_disclosed", any(term in normalized(result.get("message", "")) for term in ("kurgusal","sentetik","synthetic")))


def source_checks(checks, reader, result, manifest, oracle=None):
    verified = []
    datasets = {}
    for dataset_id in manifest.get("datasets", []):
        dataset = attempt(checks, "published_dataset_integrity", lambda: reader.object("datasets",dataset_id))
        if not dataset:
            continue
        proof = dataset[0].get("contract", {}).get("document_provenance", {})
        source = attempt(checks, "published_raw_source_integrity", lambda: reader.source(
            result["workspace_id"], proof.get("source_id"), proof.get("raw_sha256")))
        if source:
            verified.append((proof, source[0]))
            datasets[dataset_id] = dataset
            record(checks, "publication_source_url_matches_registered_source",
                   proof.get("source_url") == source[0].get("source_url"))
    record(checks, "new_source_was_published", bool(verified), missing=not manifest.get("datasets"))
    if oracle:
        def matches(item):
            proof, source = item
            host = (urlsplit(source.get("source_url") or "").hostname or "").lower().removeprefix("www.")
            pages = {proof.get("page"), *(proof.get("source_pages") or [])}
            for row in proof.get("row_origins", []):
                pages.add(row.get("page"))
            encoded = json.dumps(proof,ensure_ascii=False)
            page_matches = oracle["page"] in pages or bool(re.search(r'"page"\s*:\s*'+str(oracle["page"])+r'\b',encoded))
            return (host == oracle["host"] or host.endswith("."+oracle["host"])) and page_matches and (
                not oracle.get("raw_sha256") or source.get("raw_sha256") == oracle["raw_sha256"])
        record(checks, "expected_report_and_page_provenance", any(matches(item) for item in verified),
               {"host":oracle["host"],"page":oracle["page"]}, missing=not verified)
    return datasets


def metric_role(text):
    text = normalized(text)
    if re.search(r"\bcash\b",text) and "cash flow" not in text or "nakit" in text:
        return "cash"
    if re.search(r"\bfinancial\s+assets\b", text) or "finansal varlik" in text:
        return "financial_assets"
    if "total assets" in text or "toplam varlik" in text:
        return "total_assets"
    return None


def fixed_source_date(manifest,row,column,role,oracle,datasets):
    """Bind an output cell to the independently identified source coordinate.

    A purchasing-power date is not treated as an observation date. This fallback
    requires the exact fixed report hash, original source table, row and column,
    with a single preserved source value. Other static layouts stay incomplete.
    """
    lineage = manifest.get("lineage", {})
    query = lineage.get("dataset_query", {})
    binding = lineage.get("sources", {}).get(column, {}).get("binding", {})
    dataset_id = query.get("dataset_id") or binding.get("dataset_id")
    if dataset_id not in datasets:
        return None
    dataset, source_rows = datasets[dataset_id]
    proof = dataset.get("contract", {}).get("document_provenance", {})
    if (query and query.get("document_provenance") != proof
            or not query and binding.get("document_provenance") != proof):
        return None
    if (proof.get("raw_sha256") != oracle.get("raw_sha256") or proof.get("page") != oracle["page"]
            or proof.get("preparation",{}).get("source_table_id") != oracle.get("source_table")):
        return None
    if query:
        key=[row.get(name) for name in query.get("output_keys",[])]
        refs=next((value.get(column,{}) for encoded,value in query.get("cells",{}).items() if json.loads(encoded)==key),{})
    else:
        dimensions = lineage.get("sources", {}).get(column, {}).get("dimensions", {})
        candidates = [(index, source) for index, source in enumerate(source_rows, 1)
                      if period_end(source.get(binding.get("time_column"))) == period_end(row.get("period"))
                      and all(source.get(key) == value for key, value in dimensions.items())]
        refs = {"source_rows": [index for index, _ in candidates], "source_column": binding.get("value_column"),
                "source_values": [source.get(binding.get("value_column")) for _, source in candidates]}
    positions=refs.get("source_rows",[])
    if len(positions)!=1 or len(refs.get("source_values",[]))!=1 or not close(refs["source_values"][0],row[column]):
        return None
    origins=proof.get("cell_origins",[])
    if (not isinstance(positions[0], int) or not 0 < positions[0] <= min(len(origins),len(source_rows))
            or not close(source_rows[positions[0]-1].get(refs.get("source_column")),row[column])):
        return None
    origin=origins[positions[0]-1]
    cell=origin.get(refs.get("source_column"))
    if cell is None:
        # A renamed single numeric field has one direct source cell; joined
        # dimension labels have `parts` instead of numeric cell coordinates.
        direct=[value for value in origin.values() if "candidate_column" in value and "candidate_row" in value]
        cell=direct[0] if len(direct)==1 else {}
    expected=oracle.get("source_cells",{}).get(cell.get("candidate_column"),{})
    return expected.get("date") if expected.get("rows",{}).get(str(cell.get("candidate_row")))==role else None


def grade_pdf(checks, reader, result, manifest, rows, case):
    oracle = ORACLES[case]
    schema = manifest.get("schema", {})
    observed = {}
    selected = set()
    datasets = source_checks(checks, reader, result, manifest, oracle)
    for row in rows:
        labels = " ".join(str(value) for name,value in row.items() if name != "period" and isinstance(value,str))
        for name, meta in schema.items():
            value = row.get(name)
            if not isinstance(value,(int,float)):
                continue
            source = manifest.get("lineage",{}).get("sources",{}).get(name,{})
            binding = source.get("binding", {})
            role = metric_role(" ".join([labels,name,str(meta.get("source_semantics","")),str(binding.get("title","")),json.dumps(source.get("dimensions",{}))]))
            source_date = fixed_source_date(manifest,row,name,role,oracle,datasets)
            label_date = period_end(row.get("period"))
            at = label_date or source_date
            if role in oracle["values"] and at in oracle["values"][role]:
                observed.setdefault((role,at),[]).append((value,name,meta))
                record(checks, f"{role}_{at}_source_coordinate_date", source_date == at,
                       {"source_coordinate_date": source_date, "output_date": label_date}, missing=source_date is None)
    for role, values in oracle["values"].items():
        for at, expected in values.items():
            matches = [item for item in observed.get((role,at),[]) if close(item[0],expected)
                       and monetary(item[2],1000) and item[2].get("kind") == "stock"]
            selected.update(item[1] for item in matches)
            record(checks, f"{role}_{at}_TOTAL_thousand_TRY", bool(matches),
                   {"expected":expected,"observed":[{"value":item[0],"column":item[1],"unit":item[2].get("unit"),"scale":item[2].get("scale")} for item in observed.get((role,at),[])]},
                   missing=not observed.get((role,at)))
            if oracle.get("price_basis"):
                record(checks, f"{role}_purchasing_power_basis", bool(matches) and all(
                    date_in_text(item[2].get("price_basis", ""),oracle["price_basis"]) for item in matches), oracle["price_basis"],missing=not matches)
    chart_checks(checks, reader, result, manifest, rows, "bar", sorted(selected))


def pdf_sector_scope_disclosures(message):
    text = normalized(message).replace("î", "i")
    # The bindings already identify the two institutions. The final caveat
    # must describe reporting populations, rather than merely different URLs.
    population_context = bool(re.search(r"\b(?:kurum\w*|raporlama|nufus\w*|populasyon\w*|population\w*|reporting|konsolide|consolidat\w*)\b", text))
    scope = (population_context
             and bool(re.search(r"(?:kapsam|populasyon|population)[^.\n]{0,110}(?:farkli(?! degil)|esdeger degil|ayni degil|ortusm|differ|not equivalent)|"
                                r"farkli(?! degil)[^.\n]{0,70}(?:kapsam|populasyon)|different[^.\n]{0,70}population", text)))
    market_share = bool(re.search(
        r"(?:pazar payi)[^.\n]{0,85}(?:degil|sayilmaz|nitelenemez|adlandirilmam|kullanilmam|yorumlanmam|okunmam|niteligi tasim)|"
        r"not[^.\n]{0,55}(?:official )?market share", text))
    return scope, market_share


def grade_pdf_sector(checks, reader, result, manifest, rows):
    oracle = ORACLES["pdf_sector"]
    schema, lineage = manifest.get("schema", {}), manifest.get("lineage", {})
    sources, operations = lineage.get("sources", {}), lineage.get("operations", [])
    datasets = source_checks(checks, reader, result, manifest, oracle)
    record(checks, "one_exact_March_2026_comparison_row", len(rows) == 1 and period_end(rows[0].get("period")) == oracle["date"])
    row = rows[0] if len(rows) == 1 else {}
    bank_raw, sector_raw = [], []
    for name, proof in sources.items():
        binding = proof.get("binding", {})
        dataset_id = binding.get("dataset_id")
        if dataset_id in datasets:
            dataset, _ = datasets[dataset_id]
            contract_column = dataset.get("contract", {}).get("columns", {}).get(binding.get("value_column"), {})
            native = proof.get("cells", {}).get(row.get("period"), [])
            if (monetary(schema.get(name, {}), 1000) and schema[name].get("kind") == "stock"
                    and monetary(contract_column, 1000) and contract_column.get("kind") == "stock"
                    and close(row.get(name), oracle["bank_thousand_TRY"])
                    and fixed_source_date(manifest, row, name, "total_assets", oracle, datasets) == oracle["date"]
                    and len(native) == 1 and str(native[0].get("native_period")) == oracle["date"]
                    and close(native[0].get("source_value"), oracle["bank_thousand_TRY"])
                    and close(native[0].get("computed_value"), oracle["bank_thousand_TRY"])):
                bank_raw.append(name)
        if (binding.get("metric_id") == oracle["sector_metric_id"]
                and monetary(schema.get(name, {}), 1000000) and schema[name].get("kind") == "stock"
                and close(row.get(name), oracle["sector_million_TRY"])):
            sector_raw.append(name)
    record(checks, "Garanti_TOTAL_ASSETS_exact_original_PDF_cell", bool(bank_raw),
           {"expected_thousand_TRY": oracle["bank_thousand_TRY"], "date": oracle["date"], "row": 60, "TOTAL_column": "column_8", "matching_columns": bank_raw}, missing=not datasets)
    record(checks, "BDDK_full_sector_total_assets_amount", bool(sector_raw), oracle["sector_million_TRY"])

    def original_bddk_cell():
        path = Path(__file__).resolve().parents[1] / oracle["sector_source_file"]
        if digest(path) != oracle["sector_raw_sha256"]:
            raise ValueError("Original BDDK download hash differs from the fixed source oracle")
        document = read_json(path)["Json"]
        if isinstance(document, str):
            document = json.loads(document)
        cell = document["data"]["rows"][oracle["sector_source_row"] - 1]["cell"]
        if not (cell[0] == "Sektör" and cell[1] == 26 and cell[2] == "TOPLAM AKTİFLER"
                and close(cell[6], oracle["sector_million_TRY"]) and "milyon TL" in document["caption"]):
            raise ValueError("Original BDDK total-assets cell or unit does not match the oracle")
        return True
    attempt(checks, "original_BDDK_download_hash_and_total_assets_cell", original_bddk_cell)
    sector_proof = False
    for name in sector_raw:
        proof = sources[name]
        binding = proof.get("binding", {})
        population = binding.get("population_scope", {})
        cells = proof.get("cells", {}).get(row.get("period"), [])
        sector_proof |= (population.get("id") == "bddk_monthly_full_reporting_population"
            and not population.get("exclusions") and binding.get("measurement_basis") == "source_reported"
            and proof.get("dimensions") == {"group_code": oracle["sector_group_code"]}
            and len(cells) == 1 and cells[0].get("native_period") == oracle["period"]
            and cells[0].get("group_code") == oracle["sector_group_code"]
            and cells[0].get("source_sha256") == oracle["sector_raw_sha256"]
            and Path(cells[0].get("source_file", "")).name == Path(oracle["sector_source_file"]).name
            and cells[0].get("source_row_index") == oracle["sector_source_row"]
            and cells[0].get("value_dimension") == "Toplam"
            and close(cells[0].get("source_value"), oracle["sector_million_TRY"])
            and close(cells[0].get("computed_value"), oracle["sector_million_TRY"]))
    record(checks, "BDDK_exact_total_cell_and_full_reporting_population", sector_proof)

    # A scale conversion must follow saved operations back to a verified raw
    # source column. Matching copied numbers do not establish this lineage.
    def scaled_root(name, before=None):
        before = len(operations) if before is None else before
        for index in range(before - 1, -1, -1):
            op = operations[index]
            if op.get("output") != name:
                continue
            source = op.get("column")
            old, new = schema.get(source, {}), schema.get(name, {})
            if (op.get("op") != "scale" or not monetary(new, op.get("target_scale"))
                    or old.get("unit") != "TRY" or old.get("currency") != "TRY"
                    or not isinstance(old.get("scale"), (int, float)) or not old["scale"] > 0
                    or not isinstance(new.get("scale"), (int, float)) or not new["scale"] > 0
                    or not isinstance(row.get(name), (int, float, Decimal))
                    or not isinstance(row.get(source), (int, float, Decimal))
                    or not close(row.get(name), Decimal(str(row.get(source))) * Decimal(str(old["scale"])) / Decimal(str(new["scale"])))):
                return None
            return scaled_root(source, index)
        return name if name in sources else None
    common = [(bank, sector) for bank, meta in schema.items() for sector, other in schema.items()
              if meta.get("kind") == other.get("kind") == "stock" and monetary(meta, other.get("scale"))
              and isinstance(meta.get("scale"), (int, float)) and meta["scale"] > 0
              and scaled_root(bank) in bank_raw and scaled_root(sector) in sector_raw
              and close(row.get(bank), Decimal(oracle["bank_thousand_TRY"]) * 1000 / Decimal(str(meta["scale"])))
              and close(row.get(sector), Decimal(oracle["sector_million_TRY"]) * 1000000 / Decimal(str(meta["scale"])))]
    record(checks, "same_scale_saved_amounts_with_source_conversion_lineage", bool(common),
           {"matching_column_pairs": common, "expected_million_TRY": {"bank": oracle["bank_million_TRY"], "sector": oracle["sector_million_TRY"]}})
    ratios = [name for name, meta in schema.items() if meta.get("unit") == "percent" and close(meta.get("scale"), 1)
              and close(row.get(name), Decimal(oracle["percent"]))]
    record(checks, "saved_cross_population_percent", bool(ratios), oracle["percent"])
    valid_ratios = [op["output"] for op in operations if op.get("op") == "ratio" and op.get("output") in ratios
                    and scaled_root(op.get("column")) in bank_raw and scaled_root(op.get("denominator")) in sector_raw
                    and close(op.get("multiplier"), 100) and op.get("scope_policy") == "explicit_comparison"
                    and isinstance(op.get("scope_reason"), str) and len(op["scope_reason"].strip()) >= 10]
    record(checks, "ratio_operation_binds_two_verified_sources_with_explicit_scope", bool(valid_ratios))
    record(checks, "period_alignment_does_not_assert_population_equivalence",
           lineage.get("join_contract", {}).get("population_equivalence_asserted") is False)
    scope, not_share = pdf_sector_scope_disclosures(result.get("message", ""))
    record(checks, "different_consolidated_and_BDDK_populations_disclosed", scope)
    record(checks, "ratio_explicitly_not_official_market_share", not_share)
    chart = attempt(checks, "comparison_chart_receipt_integrity", lambda: reader.receipt(
        "charts", result.get("chart_id"), result["workspace_id"], manifest))
    if chart:
        kind = chart.get("spec", {}).get("kind")
        record(checks, "comparison_uses_line_or_bar_chart", kind in {"line", "bar"})
        chart_checks(checks, reader, result, manifest, rows, kind, valid_ratios)


def grade_web(checks, reader, result):
    found = []
    for item in result.get("tool_results", []):
        payload = item.get("result", {})
        if payload.get("status") != "ok":
            continue
        sources = payload.get("sources", []) if item.get("tool") == "research_web" else [payload] if item.get("tool") == "inspect_source" else []
        for source in sources:
            verified = attempt(checks, "official_decision_raw_source_integrity", lambda: reader.source(
                result["workspace_id"],source.get("source_id"),source.get("raw_sha256")))
            if not verified:
                continue
            manifest,path = verified
            url = manifest.get("source_url") or ""
            host = (urlsplit(url).hostname or "").lower()
            if not (host == "tcmb.gov.tr" or host.endswith(".tcmb.gov.tr")) or "duy2025-15" not in unquote(url).lower():
                continue
            if manifest.get("mime_type") == "application/pdf":
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    content = "\n".join(page.extract_text() or "" for page in pdf.pages)
            else:
                content = path.read_text(encoding="utf-8",errors="replace")
                content = re.sub(r"<(script|style)\b[^>]*>.*?</\1>"," ",content,flags=re.S|re.I)
                content = html.unescape(re.sub(r"<[^>]*>"," ",content))
            text = normalized(content)
            date = date_in_text(text,ORACLES["web"]["date"])
            rates = bool(re.search(r"(?:yuzde\s*)?45(?:[.,]0+)?(?:'|’)?(?:ten|tan)?[^\d]{0,100}(?:yuzde\s*)?42[.,]5",text))
            policy = ("repo" in text and ("bir hafta" in text or "one week" in text or "one-week" in text))
            found.append({"url":url,"source_id":source.get("source_id"),"date":date,"rates":rates,"policy":policy})
    record(checks,"actual_official_decision_opened",bool(found),found,missing=not found)
    record(checks,"decision_date_and_prior_to_new_rate",any(item["date"] and item["rates"] and item["policy"] for item in found),missing=not found)
    record(checks,"decision_link_delivered",any(item["url"] in result.get("message","") for item in found),missing=not found)
    message = normalized(re.sub(r"https?://\S+", "", result.get("message", "")))
    record(checks, "decision_date_delivered", date_in_text(message, ORACLES["web"]["date"]), missing=not found)
    record(checks, "prior_to_new_policy_rate_delivered", delivered_rate_change(message), missing=not found)


def grade_turn(round_path, case, turn):
    checks = []
    result = turn.get("result",{})
    reader = ReceiptReader(round_path)
    if case not in ORACLES:
        return {"artifact_grade":"unavailable","task_complete":False,"reason":"No independent oracle supplied for this case."}
    if not result:
        record(checks,"run_evidence_present",False,turn.get("collector_error"),missing=True)
    elif case == "web":
        grade_web(checks,reader,result)
    else:
        saved = attempt(checks,"saved_analysis_integrity",lambda:reader.object("analyses",result.get("analysis_id")))
        if saved:
            manifest,rows = saved
            record(checks,"analysis_workspace_matches_run",manifest.get("workspace_id") == result.get("workspace_id"))
            exported = turn.get("analyses",{}).get(manifest["analysis_id"],{}).get("rows")
            record(checks,"collector_export_matches_saved_bytes",exported is not None and matches_rows(exported,rows),missing=exported is None)
            if case == "profit": grade_profit(checks,reader,result,manifest,rows)
            elif case == "new_source": grade_new_source(checks,reader,result,manifest,rows)
            elif case == "pdf_sector": grade_pdf_sector(checks,reader,result,manifest,rows)
            else: grade_pdf(checks,reader,result,manifest,rows,case)
    statuses = {item["status"] for item in checks}
    grade = "fail" if "fail" in statuses else "incomplete" if "incomplete" in statuses else "pass" if checks else "unavailable"
    return {"artifact_grade":grade,"task_complete":grade == "pass" and result.get("status") == "completed",
            "run_status":result.get("status"),"decisions":result.get("decisions"),"elapsed_seconds":turn.get("elapsed_seconds"),
            "checks":checks,"runtime_errors":result.get("errors",[]),"answer_sha256":hashlib.sha256(result.get("message","").encode()).hexdigest(),
            "limits":["Checks cover the stated acceptance oracles; other prose claims and browser usability were not independently reviewed.",
                      "Analysis, attached chart/summary and imported raw-source bytes are checked; original BDDK raw downloads are not re-fetched."]}


def grade_round(round_path):
    round_path = Path(round_path).resolve()
    manifest = read_json(round_path/"manifest.json")
    trials = []
    for case in manifest.get("cases",{}):
        for repeat in range(1,manifest.get("repeats",1)+1):
            trial_path = round_path/f"{case}_{repeat}"
            files = sorted(trial_path.glob("turn-*.json"))
            files = [path for path in files if not path.name.endswith("-journal.json")]
            turns = [{"file":str(path),"sha256":digest(path),**grade_turn(round_path,case,read_json(path))} for path in files]
            trials.append({"case":case,"repeat":repeat,"turns":turns,"artifact_grade":"not_run" if not turns else
                           "fail" if any(turn["artifact_grade"]=="fail" for turn in turns) else
                           "pass" if all(turn["artifact_grade"]=="pass" for turn in turns) else "incomplete",
                           "task_complete":bool(turns) and all(turn["task_complete"] for turn in turns)})
    return {"round":str(round_path),"manifest_sha256":digest(round_path/"manifest.json"),
            "backend_unchanged_during_collection":manifest.get("backend_unchanged"),"trials":trials}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rounds",nargs="+",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report directory; previous grades are preserved.")
    report = {"created_at":datetime.now(timezone.utc).isoformat(),"grader_sha256":digest(Path(__file__)),
              "oracles":ORACLES,"rounds":[grade_round(path) for path in args.rounds]}
    args.output.mkdir(parents=True)
    (args.output/"grades.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    lines = ["Mentor acceptance evidence", "", "Grades use saved bytes and independent fixed oracles. Run status alone never proves correctness.", "",
             "| Round | Case | Artifact grade | Task complete | Failed or missing checks |", "| --- | --- | --- | --- | --- |"]
    for round_result in report["rounds"]:
        for trial in round_result["trials"]:
            issues = [check["check"] for turn in trial["turns"] for check in turn.get("checks",[]) if check["status"] != "pass"]
            lines.append(f"| {Path(round_result['round']).name} | {trial['case']}_{trial['repeat']} | {trial['artifact_grade']} | {trial['task_complete']} | {', '.join(issues) or '-'} |")
    lines.extend(["", "Full checks, hashes, oracles and limitations are in grades.json. Previous live evidence is unchanged."])
    (args.output/"grades.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"report":str(args.output.resolve()),"trials":sum(len(item["trials"]) for item in report["rounds"])}))


if __name__ == "__main__":
    main()
