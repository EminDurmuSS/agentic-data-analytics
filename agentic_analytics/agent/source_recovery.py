"""Conservative, source-owned statement delivery when prose/tool repair stalls.

No web request or model decision is made here. Recovery is limited to one
explicitly requested balance-sheet line/date in a table read in this turn.
The financial compiler still owns date, unit, number and review validation.
"""
from datetime import date
import re

from agentic_analytics.agent.tools.documents import (
    DocumentTools, _consolidation_scope, _source_dates, _search_text,
)
from agentic_analytics.agent.tools.financial_import import FinancialImportTools, _label_key


_ASSET_LABELS = {"toplamaktif", "toplamaktifler", "aktiflertoplami",
                 "toplamvarliklar", "varliklartoplami", "totalassets"}


def statement_recovery_request(store, workspace_id, request, tool_results):
    """Return a single unambiguous import request, or None; never publish.

    Total assets has established Turkish/English synonyms. Other statement
    lines require their actual source label in the user's request. A company,
    amount, period, currency, or column position is never hard-coded.
    """
    dates = _source_dates(request)
    normalized = _search_text(request)
    years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", normalized)}
    annual_request = bool(re.search(r"\byil\s*sonu\b|\byillik\b|\byear[ -]end\b|\bannual\b", normalized))
    if len(dates) > 1 or not dates and not (len(years) == 1 and annual_request):
        return None
    requested_period = date(*next(iter(dates))).isoformat() if dates else None
    requested_year = next(iter(years)) if len(years) == 1 else None
    # Do not infer arithmetic, a compound selection, or a prohibited write.
    if re.search(r"\b(?:ekleme|kaydetme|yayinlama|grafik\s+(?:istemi\w*|olusturma)|"
                 r"oran\w*|buyume\w*|karsilastir\w*|fark\w*|ratio|growth|compare)\b", normalized):
        return None
    requested_key = _label_key(request)
    wants_assets = any(label in requested_key for label in _ASSET_LABELS)
    requested_scope = _consolidation_scope(request)
    documents = DocumentTools(store, workspace_id)
    compiler = FinancialImportTools(documents)
    candidates = {}
    identities = {}

    def remember(source):
        if not isinstance(source, dict) or not source.get("source_id"):
            return
        current = identities.setdefault(source["source_id"], {})
        for key in ("document_type", "reporting_period", "consolidation_scope", "unit_caption"):
            if source.get(key) and not current.get(key):
                current[key] = source[key]

    for item in tool_results:
        result = item.get("result") or {}
        if result.get("status") != "ok":
            continue
        if item.get("tool") == "research_web":
            for source in result.get("sources", []):
                remember(source)
        elif item.get("tool") in {"inspect_source", "read_source_table", "find_source_table_rows"}:
            remember(result)
    # Read complete candidates locally, but only select rows actually returned
    # by a direct read in this turn. Search snippets/previews are not authority.
    for item in tool_results:
        result = item.get("result") or {}
        identity = identities.get(result.get("source_id"), {})
        if (item.get("tool") not in {"read_source_table", "find_source_table_rows"}
                or result.get("status") != "ok" or not result.get("rows")
                or (result.get("document_type") or identity.get("document_type")) != "financial_report"
                or not result.get("source_id") or not result.get("table_id")
                or not result.get("raw_sha256")):
            continue
        if requested_scope and (result.get("consolidation_scope")
                                or identity.get("consolidation_scope")) != requested_scope:
            continue
        table = compiler._candidate(result["source_id"], result["table_id"])
        if (table.get("raw_sha256") != result["raw_sha256"] or table.get("origin") != "parsed"
                or table.get("layout_review_required") or table.get("missing_formula_cache")
                or table.get("preparation")):
            continue
        selected = []
        for read in result["rows"]:
            number = read.get("candidate_row")
            if type(number) is not int or not 1 <= number <= len(table["rows"]):
                continue
            row = table["rows"][number - 1]
            if read.get("values") != dict(zip(table["columns"], row)):
                continue
            label = "".join(value for _, value in compiler._row_label(table, row))
            key = _label_key(label)
            if key and ((wants_assets and key in _ASSET_LABELS)
                        or (not wants_assets and len(key) >= 8 and key in requested_key)):
                selected.append(number)
        if len(selected) != 1:
            continue
        # A repeated label elsewhere in the candidate is ambiguous even when
        # only one occurrence happened to be included in the read window.
        labels, _, resolved = compiler._labels(table, selected)
        if compiler._select_rows(table, None, resolved) != selected:
            continue
        numeric = compiler._amount_columns(table, selected, labels)
        headers = {str(value).strip() for row in table["rows"][:selected[0] - 1]
                   for value in row if _label_key(value or "") in {"toplam", "total"}}
        header = next(iter(headers)) if len(headers) == 1 else None
        values, source_dates, _, _ = compiler._dates(table, selected, numeric, header, None)
        values = [column for column in values if (
            source_dates[column] == requested_period if requested_period else
            source_dates[column] == f"{requested_year:04d}-12-31")]
        if len(values) != 1:
            continue
        period = source_dates[values[0]]
        semantics, _ = compiler._semantics(table, values, "stock")
        if semantics.get("kind") != "stock" or semantics.get("status") == "review_required":
            continue
        compiler._number_style(table, selected, values, None)
        args = {"source_id": result["source_id"], "table_id": table["table_id"],
                "row_numbers": selected, "periods": [period], "measure_kind": "stock",
                "expected_version": store.workspace(workspace_id)["version"]}
        if header:
            args["value_header"] = header
        # The provider may have failed just after import rather than after
        # reading. Replay that exact compiler receipt instead of publishing
        # identical cells again under the workspace's newer revision.
        for previous in tool_results:
            imported = previous.get("result") or {}
            receipt = imported.get("compile_receipt") or {}
            original = receipt.get("arguments") or {}
            if (previous.get("tool") == "ingest_source_table"
                    and imported.get("status") == "ok" and imported.get("publication_performed")
                    and original.get("source_id") == args["source_id"]
                    and original.get("table_id") == args["table_id"]
                    and receipt.get("source_raw_sha256") == table["raw_sha256"]
                    and receipt.get("row_numbers") == selected
                    and receipt.get("source_periods") == {column: period for column in values}):
                # ingest_source_table verifies its saved receipt and immutable
                # publication. A stale/corrupt receipt still cannot write.
                args = {key: value for key, value in original.items() if value is not None}
                break
        candidates[(result["source_id"], table["table_id"], selected[0])] = args
    return next(iter(candidates.values())) if len(candidates) == 1 else None
