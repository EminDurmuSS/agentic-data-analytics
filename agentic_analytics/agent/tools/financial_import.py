"""Compile business selections into source-owned financial facts.

The model selects report lines and periods. This compiler owns the mechanical
contract, preserves original cell addresses, and performs one publication.
It deliberately refuses uncertain date groups, units and numeric conventions.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from agentic_analytics.agent.tools.documents import DocumentError, _canonical, _search_text, _unit_caption, _write_json
from agentic_analytics.lakehouse.store import StoreError, VersionConflict
from agentic_analytics.lakehouse.units import _quote_identity


_NULLS = {"-", "–", "N/A", "n/a"}
_NUMBER = re.compile(r"^[+-]?[\d.,]+$|^\(\s*[\d.,]+\s*\)$")
_CODE = re.compile(r"^(?:[IVXLCDM]+\.?|\d+(?:\.\d+)*\.?)$", re.I)


def _label_key(text):
    text = re.sub(r"^(?:[IVXLCDM]+\.|\d+(?:\.\d+)*\.?)\s+", "", str(text), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", _search_text(text))


def _numeric(value, style):
    if value is None or value in _NULLS:
        return None
    value = str(value).strip()
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1].strip()
    patterns = {"decimal_dot": r"[+-]?\d+(?:\.\d+)?",
                "decimal_dot_grouped": r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?",
                "decimal_comma": r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?"}
    if style not in patterns or not re.fullmatch(patterns[style], value):
        raise ValueError("Source number does not match convention")
    value = value.replace(",", "") if style == "decimal_dot_grouped" else value.replace(".", "").replace(",", ".") if style == "decimal_comma" else value
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Nonfinite source number")
    return result


class FinancialImportTools:
    def __init__(self, documents):
        self.documents = documents
        self.store, self.workspace_id = documents.store, documents.workspace_id
        self.root = documents.root / "compiled_imports"
        self.root.mkdir(exist_ok=True)

    @staticmethod
    def _refuse(message, code, **choices):
        raise DocumentError(message, code, recovery={"business_choices": choices, "publication_performed": False})

    @staticmethod
    def _arguments(source_id, table_id, expected_version, row_numbers=None, row_labels=None,
                   periods=None, value_header=None, value_columns=None, measure_kind="unknown", number_style=None):
        return dict(source_id=source_id, table_id=table_id, expected_version=expected_version,
                    row_numbers=row_numbers, row_labels=row_labels, periods=periods, value_header=value_header,
                    value_columns=value_columns, measure_kind=measure_kind, number_style=number_style)

    def _receipt_path(self, args):
        return self.root / ("import_" + hashlib.sha256(_canonical(args)).hexdigest() + ".json")

    def _candidate(self, source_id, table_id):
        table = self.documents.review_candidate(source_id, table_id)
        if not table.get("context_text"):
            inspection = json.loads((self.documents._directory(source_id) / "inspection.json").read_text())
            original = [item for item in inspection["tables"] if not item.get("preparation")]
            # One source table permits its surrounding source caption/title.
            # Multiple unrelated tables cannot borrow each other's context.
            if len(original) == 1 and inspection.get("total_pages") is None:
                table["context_text"] = inspection.get("text", "")
        return table

    def _alternative_recovery(self, table, args, requires_review=False):
        """Offer another extraction, without transferring review or cell IDs."""
        recovery = {"business_choices": {"table_id": table["table_id"]}, "publication_performed": False,
                    "rejected_candidate_still_requires_review": requires_review, "alternative_candidates": []}
        page = table.get("page")
        if type(page) is not int or page < 1:
            return recovery
        inspection = json.loads((self.documents._directory(args["source_id"]) / "inspection.json").read_text())
        for candidate in inspection["tables"]:
            if (candidate["table_id"] == table["table_id"] or candidate.get("page") != page
                    or candidate.get("origin") != "parsed" or candidate.get("preparation") or candidate.get("review")
                    or candidate.get("missing_formula_cache") or candidate.get("layout_review_required")
                    or not candidate.get("columns") or not candidate.get("rows")
                    or any(len(row) != len(candidate["columns"]) for row in candidate["rows"])
                    or (self.documents._directory(args["source_id"]) / (candidate["table_id"] + "_review.json")).exists()):
                continue
            label_match, selection_evidence = False, None
            if args.get("row_labels") and not args.get("row_numbers"):
                try:
                    rows = self._select_rows(candidate, None, args["row_labels"])
                    label_match = True
                except DocumentError:
                    continue
                try:
                    labels, _, resolved = self._labels(candidate, rows)
                    numeric = self._amount_columns(candidate, rows, labels)
                    _, dates, recipe, _ = self._dates(candidate, rows, numeric, args.get("value_header"), None)
                    if not args.get("periods") or set(args["periods"]) <= set(dates.values()):
                        selection_evidence = {"source_rows": rows, "source_labels": resolved,
                                              "source_periods": dates, "source_date_cells": recipe}
                except DocumentError:
                    pass
            alternative = {"source_id": args["source_id"], "raw_sha256": table["raw_sha256"],
                           "table_id": candidate["table_id"], "page": page, "origin": "parsed",
                           "layout_review_required": False, "row_count": len(candidate["rows"]),
                           "requested_labels_match_uniquely": label_match,
                           "next_request": {"tool": "read_source_table", "arguments": {
                               "source_id": args["source_id"], "table_id": candidate["table_id"], "row_start": 1, "row_limit": 20}}}
            if selection_evidence:
                alternative["selection_evidence"] = selection_evidence
            if label_match and selection_evidence and not args.get("value_columns"):
                alternative["suggested_ingest_arguments"] = {key: value for key, value in {
                    **args, "table_id": candidate["table_id"]}.items() if value is not None}
            recovery["alternative_candidates"].append(alternative)
            if len(recovery["alternative_candidates"]) == 3:
                break
        ready = [candidate for candidate in recovery["alternative_candidates"] if candidate.get("suggested_ingest_arguments")]
        if len(ready) == 1:
            recovery["suggested_ingest_arguments"] = ready[0]["suggested_ingest_arguments"]
            recovery["next_step"] = "Retry ingest_source_table with these exact suggested_ingest_arguments. This is a different source extraction with matching business labels, not approval of the rejected candidate. Its date, unit, scope and number gates still apply."
        elif recovery["alternative_candidates"]:
            recovery["next_step"] = "Read the same-page alternative candidates and select the intended statement. Candidate-local row_numbers and value_columns cannot be transferred between extractions. No candidate was published or independently reviewed."
        elif not any(candidate.get("page") == page and candidate.get("table_strategy") == "text" for candidate in inspection["tables"]):
            manifest = self.documents.source(args["source_id"])
            if manifest.get("filename", "").casefold().endswith(".pdf") or manifest.get("mime_type", "").split(";")[0] == "application/pdf":
                recovery["next_request"] = {"tool": "inspect_source", "arguments": {
                    "source_id": args["source_id"], "page_numbers": [page], "table_strategy": "text"}}
                recovery["next_step"] = "Inspect a text extraction of this same PDF page. If it still requires review, independent source-cell review is necessary; do not publish the rejected extraction."
        return recovery

    @staticmethod
    def _amount_columns(table, rows, labels):
        return [column for index, column in enumerate(table["columns"]) if column not in labels
                and all(isinstance(table["rows"][row - 1][index], str)
                        and (_NUMBER.fullmatch(table["rows"][row - 1][index].strip())
                             or table["rows"][row - 1][index] in _NULLS) for row in rows)]

    @staticmethod
    def _row_label(table, row):
        """Read textual cells preceding numbers; never join numeric fragments."""
        parts = []
        for column, value in zip(table["columns"], row):
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if not parts and _CODE.fullmatch(value):
                continue
            if _NUMBER.fullmatch(value) or _CODE.fullmatch(value) and not re.search(r"[a-z]", value, re.I):
                break
            if value in _NULLS:
                break
            parts.append((column, value))
        return parts

    def _select_rows(self, table, row_numbers, row_labels):
        if bool(row_numbers) == bool(row_labels):
            self._refuse("Select report rows by row_labels or row_numbers, using exactly one selector.", "IMPORT_SELECTION_REQUIRED")
        if row_numbers:
            if (len(row_numbers) > 30 or len(set(row_numbers)) != len(row_numbers)
                    or any(type(row) is not int or not 1 <= row <= len(table["rows"]) for row in row_numbers)):
                self._refuse("Choose at most 30 distinct existing report row numbers.", "INVALID_TABLE_SELECTION")
            return sorted(row_numbers)
        if len(row_labels) > 30 or any(not isinstance(label, str) or not 1 <= len(label) <= 300 for label in row_labels):
            self._refuse("Choose at most 30 bounded report row labels.", "INVALID_TABLE_SELECTION")
        candidates = []
        for number, row in enumerate(table["rows"], 1):
            parts = self._row_label(table, row)
            if parts and any(isinstance(value, str) and _NUMBER.fullmatch(value.strip()) for value in row):
                candidates.append((number, "".join(value for _, value in parts)))
        selected = []
        for requested in row_labels:
            key = _label_key(requested)
            exact = [(row, label) for row, label in candidates if _label_key(label) == key]
            matches = exact or [(row, label) for row, label in candidates if key and key in _label_key(label)]
            if len(matches) != 1:
                self._refuse("A report label must identify exactly one source row. Select from the actual source labels.",
                             "AMBIGUOUS_IMPORT_ROWS", requested_label=requested, matching_row_count=len(matches),
                             rows=[{"row_number": row, "source_label": label} for row, label in (matches or candidates)[:20]])
            selected.append(matches[0][0])
        if len(set(selected)) != len(selected):
            self._refuse("Requested labels refer to the same source row more than once.", "AMBIGUOUS_IMPORT_ROWS")
        return sorted(selected)

    def _labels(self, table, rows):
        parts = [self._row_label(table, table["rows"][number - 1]) for number in rows]
        columns = [column for column in table["columns"] if any(column in dict(row) for row in parts)]
        if not columns or len(columns) > 10:
            self._refuse("Report labels need a bounded, unambiguous group of text cells.", "AMBIGUOUS_IMPORT_LABELS")
        context = " ".join(_search_text(table.get("context_text", "")).split())
        variants = []
        for separator in ("", " "):
            labels = [separator.join(str(table["rows"][row - 1][table["columns"].index(column)] or "") for column in columns).strip() for row in rows]
            # Joining split letters is justified by a complete source text line.
            if len(columns) == 1 or all(" ".join(_search_text(label).split()) in context for label in labels):
                variants.append((separator, labels))
        unique = {tuple(" ".join(label.split()) for label in labels) for _, labels in variants}
        if not variants or len(unique) != 1:
            self._refuse("Source text does not uniquely confirm how fragmented report labels join.", "AMBIGUOUS_IMPORT_LABELS",
                         label_columns=columns, row_numbers=rows)
        separator, labels = variants[0]
        if len({_label_key(label) for label in labels}) != len(labels):
            self._refuse("Repeated report labels may belong to different entities or scopes. Select an unambiguous statement section.", "AMBIGUOUS_IMPORT_ROWS")
        return columns, separator, labels

    def _dates(self, table, rows, numeric_columns, value_header, value_columns):
        # A subset must retain the same period ownership proved by the full
        # source header group. Removing a sibling amount column cannot erase
        # its merged header context or change the remaining column's date.
        if value_columns:
            if len(set(value_columns)) != len(value_columns) or not set(value_columns) <= set(numeric_columns):
                self._refuse("Value columns must be distinct source numeric columns for the selected rows.",
                             "AMBIGUOUS_IMPORT_VALUES", value_columns=numeric_columns)
            try:
                all_values, all_dates, recipe, basis = self._dates_for_columns(table, rows, numeric_columns, value_header, None)
            except DocumentError as exc:
                if exc.code != "AMBIGUOUS_IMPORT_PERIODS":
                    raise
            else:
                if not set(value_columns) <= set(all_values):
                    self._refuse("Selected value columns conflict with the actual requested value header.",
                                 "AMBIGUOUS_IMPORT_VALUES", value_columns=all_values)
                selected = [column for column in all_values if column in value_columns]
                return selected, {column: all_dates[column] for column in selected}, {
                    **recipe, "period_sources": {column: recipe["period_sources"][column] for column in selected}}, {
                    "complete_source_period_mapping": all_dates, "selected_value_columns": selected,
                    "complete_source_mapping_evidence": basis}
        return self._dates_for_columns(table, rows, numeric_columns, value_header, value_columns)

    def _dates_for_columns(self, table, rows, numeric_columns, value_header, value_columns):
        docs, all_columns = self.documents, table["columns"]
        headers = range(1, min(min(rows), 61))
        wanted = list(value_columns or numeric_columns)
        if value_columns and (len(set(wanted)) != len(wanted) or any(column not in numeric_columns for column in wanted)):
            self._refuse("Value columns must be distinct source numeric columns for the selected rows.", "AMBIGUOUS_IMPORT_VALUES", value_columns=numeric_columns)
        header_matches = []
        if value_header:
            for row_number in headers:
                values = table["rows"][row_number - 1]
                matches = [column for column in numeric_columns if _label_key(values[all_columns.index(column)] or "") == _label_key(value_header)]
                if matches:
                    header_matches.append(matches)
            if not header_matches or len({tuple(values) for values in header_matches}) != 1:
                self._refuse("Choose a value header that occurs unambiguously above the source amounts.", "AMBIGUOUS_IMPORT_VALUES", requested_header=value_header, value_columns=numeric_columns)
            wanted = [column for column in wanted if column in header_matches[0]]
        wanted.sort(key=all_columns.index)
        maps, alternatives = [], []
        for row_number in headers:
            recovery = docs._period_header_recovery(table, {"header_row": row_number, "columns": wanted})
            alternatives.extend(recovery["date_candidates"])
            suggestion = recovery["suggested_unpivot_update"]
            if suggestion:
                maps.append((suggestion, "date_source_cells_overlap_value_columns"))
        # A repeated explicit subheader group proves date ownership even when
        # a merged date is printed over TL/FC rather than the requested Total.
        for group_row in headers:
            values = table["rows"][group_row - 1]
            for start in range(len(all_columns)):
                for width in range(2, min(8, (len(all_columns) - start) // 2) + 1):
                    pattern = [_label_key(value or "") for value in values[start:start + width]]
                    if not all(pattern) or len(set(pattern)) != width:
                        continue
                    groups = []
                    cursor = start
                    while cursor + width <= len(all_columns) and [_label_key(value or "") for value in values[cursor:cursor + width]] == pattern:
                        groups.append(all_columns[cursor:cursor + width])
                        cursor += width
                    if len(groups) < 2 or not all(any(column in group for group in groups) for column in wanted):
                        continue
                    choices = {column: [] for column in wanted}
                    for candidate in alternatives:
                        if candidate["row"] >= group_row:
                            continue
                        for item in candidate["dates"]:
                            for group in groups:
                                if set(item["source_columns"]) <= set(group):
                                    for column in wanted:
                                        if column in group:
                                            choices[column].append((item["normalized_date"], candidate, item))
                    if not all(choices.values()) or any(len({item[0] for item in entries}) != 1 for entries in choices.values()):
                        continue
                    recipes, formats = {}, set()
                    for column, entries in choices.items():
                        _, candidate, item = min(entries, key=lambda entry: len(entry[1]["columns"]))
                        formats.add(candidate["period_format"])
                        recipes[column] = {"row": candidate["row"], "columns": candidate["columns"], "separator": candidate["separator"], "date_index": item["date_index"]}
                    if len(formats) == 1:
                        maps.append(({"period_format": next(iter(formats)), "period_sources": recipes},
                                     {"repeated_source_header_row": group_row, "groups": groups, "pattern": pattern}))
        resolved = []
        for recipe, basis in maps:
            dates = {}
            for column, source in recipe["period_sources"].items():
                joined = source["separator"].join(table["rows"][source["row"] - 1][all_columns.index(name)] for name in source["columns"])
                literals = docs._source_date_occurrences(joined, recipe["period_format"])
                dates[column] = docs._source_period_label(literals[source["date_index"]], recipe["period_format"])
            resolved.append((dates, recipe, basis))
        signatures = {tuple(sorted(dates.items())) for dates, _, _ in resolved}
        if len(signatures) != 1:
            self._refuse("Source dates do not uniquely identify the selected value columns. Choose a statement table or explicit value scope; no date was guessed.",
                         "AMBIGUOUS_IMPORT_PERIODS", value_headers=sorted({str(value) for number in headers for value in table["rows"][number - 1] if isinstance(value, str) and len(value) < 40})[:20],
                         date_candidates=alternatives[:8])
        dates, recipe, basis = resolved[0]
        if len(set(dates.values())) != len(dates):
            self._refuse("Several source columns share a period end. Their currency component or reporting interval must be selected explicitly.",
                         "AMBIGUOUS_IMPORT_PERIODS", columns_and_periods=dates, value_header=value_header)
        # A reporting interval is more than its end date. The initial compiler
        # handles statement-date columns, not overlapping cumulative intervals.
        if any(re.search(r"\d{1,2}/\d{1,2}\s*[-–]\s*\d{1,2}/\d{1,2}/\d{4}", candidate["joined_source_text"]) for candidate in alternatives):
            self._refuse("This table contains reporting intervals. A period-end filter alone cannot distinguish overlapping intervals.",
                         "AMBIGUOUS_IMPORT_INTERVAL", source_date_candidates=alternatives[:8])
        return wanted, dates, recipe, basis

    def _number_style(self, table, rows, columns, requested):
        raw = [table["rows"][row - 1][table["columns"].index(column)] for row in rows for column in columns]
        styles = [requested] if requested else ["decimal_dot", "decimal_dot_grouped", "decimal_comma"]
        valid = {}
        for style in styles:
            try:
                valid[style] = [_numeric(value, style) for value in raw]
            except (ValueError, InvalidOperation):
                continue
        if not valid:
            self._refuse("The selected amounts do not share a supported numeric convention. Review the source cells.", "AMBIGUOUS_IMPORT_NUMBERS", source_values=raw[:12])
        equations = self._source_equations(table, columns)
        proof = []
        if len({tuple(values) for values in valid.values()}) > 1 and equations:
            compatible = {}
            for style, values in valid.items():
                checked = []
                try:
                    for equation in equations:
                        operands = [_numeric(cell["value"], style) for cell in equation["operands"]]
                        total = _numeric(equation["total"]["value"], style)
                        if None in operands or total is None or sum(operands) != total:
                            raise ValueError
                        checked.append(equation)
                except (ValueError, InvalidOperation):
                    continue
                compatible[style] = values
                proof = checked
            if compatible:
                valid = compatible
        if len({tuple(values) for values in valid.values()}) != 1:
            self._refuse("Source punctuation supports different numeric values. Choose the number convention after checking the source; no magnitude or locale guess was made.",
                         "AMBIGUOUS_IMPORT_NUMBERS", number_style=[{"choice": style, "examples": [str(value) if value is not None else None for value in values[:4]]} for style, values in valid.items()])
        style, parsed = next(iter(valid.items()))
        return style, ("integer" if all(value is None or value == value.to_integral_value() for value in parsed) else "float"), proof

    @staticmethod
    def _source_equations(table, columns):
        """Only literal row-code sums printed in the source, never guessed totals."""
        codes, formulas = {}, []
        first_value = min(table["columns"].index(column) for column in columns)
        for number, row in enumerate(table["rows"], 1):
            label = "".join(str(value or "") for value in row[:first_value])
            match = re.match(r"^([IVXLCDM]+)\.\s*", label, re.I)
            if match:
                codes.setdefault(match[1].upper(), []).append(number)
            equation = re.search(r"\(([IVXLCDM]+(?:\+[IVXLCDM]+){1,3})\)", label, re.I)
            if equation:
                formulas.append((number, equation.group(0), equation.group(1).upper().split("+")))
        proof = []
        for number, expression, operands in formulas[:8]:
            if any(len(codes.get(code, [])) != 1 for code in operands):
                continue
            for column in columns:
                index = table["columns"].index(column)
                address = lambda row: {"candidate_row": row, "candidate_column": column, "value": table["rows"][row - 1][index]}
                proof.append({"printed_expression": expression, "expression_row": number, "total": address(number), "operands": [address(codes[code][0]) for code in operands]})
        return proof

    def _semantics(self, table, value_columns, requested_kind):
        context = table.get("context_text", "")
        captions = table.get("unit_caption", "") + "\n" + _unit_caption(context)
        unit_candidates = []
        for quote in captions.splitlines():
            currencies, scales, units, _ = _quote_identity(quote)
            if len(currencies) == 1 and len(scales) <= 1 and not units:
                unit_candidates.append((next(iter(currencies)), next(iter(scales), 1), quote))
        if len({(unit, scale) for unit, scale, _ in unit_candidates}) != 1:
            self._refuse("An exact common money unit and multiplier must be unambiguous in the selected statement's source caption.",
                         "AMBIGUOUS_IMPORT_UNIT", source_caption=captions)
        unit, scale, quote = unit_candidates[0]
        # The title identifies measurement kind. Row names alone do not.
        title = " ".join(_search_text(context[:2000]).split())
        stock = bool(re.search(r"balance sheet|statements? of financial position|bilan[cç]o|finansal durum", title))
        flow = bool(re.search(r"statements? of (?:profit|income|cash flows)|kar veya zarar tablosu", title))
        inferred = "stock" if stock and not flow else "flow" if flow and not stock else "unknown"
        if requested_kind != "unknown" and inferred not in {"unknown", requested_kind}:
            self._refuse("Requested measurement kind conflicts with the actual statement title.", "IMPORT_SEMANTICS_CONFLICT", source_kind=inferred, source_title=context[:500])
        # A model's business selection alone is not independent financial
        # evidence. Unknown source kinds remain readable but unreviewed.
        kind = inferred
        spec = {"dtype": "integer", "unit": unit, "currency": unit, "scale": scale, "kind": kind, "nullable": True,
                "aggregation": "last" if kind == "stock" else "none"}
        evidence = {"unit_quote": quote, "kind_source_title": context[:500], "kind_selection": requested_kind}
        if kind == "flow":
            # The compiler has only a reporting end date. Keep interval flows
            # nonadditive until an explicit source interval is represented.
            spec.update(temporal_semantics="reported_interval_unresolved", status="review_required",
                        source_semantics="Reported statement flow; interval start is not established by this date-only import.", additive_over_time=False)
        elif kind == "unknown":
            spec.update(status="review_required", additive_over_time=False)
        basis = re.search(r"purchasing power|inflation[- ]adjusted|constant prices|satin alma gucu|sabit fiyat", _search_text(context[:2000]))
        if basis:
            segment = context[basis.start():basis.start() + 300]
            parsed = []
            for literal in self.documents._source_date_occurrences(segment, "english_dmy"):
                try:
                    parsed.append(self.documents._source_period_label(literal, "english_dmy"))
                except DocumentError:
                    pass
            if not parsed:
                self._refuse("The source declares adjusted purchasing power but does not establish its basis date in the caption.", "PRICE_BASIS_REVIEW_REQUIRED", source_basis_caption=segment)
            spec["price_basis"] = "purchasing_power_" + parsed[0]
            evidence["price_basis_source_quote"] = segment
        return spec, evidence

    def _compile(self, args):
        try:
            result = self._compile_candidate(args)
        except DocumentError as exc:
            choices = (getattr(exc, "recovery", None) or {}).get("business_choices", {})
            structural = (exc.code in {"AMBIGUOUS_IMPORT_PERIODS", "AMBIGUOUS_IMPORT_LABELS"}
                          or exc.code == "AMBIGUOUS_IMPORT_ROWS" and choices.get("matching_row_count") == 0)
            if structural:
                table = self._candidate(args["source_id"], args["table_id"])
                recovery = {**self._alternative_recovery(table, args), **(getattr(exc, "recovery", None) or {})}
                exc.recovery = recovery
                if recovery.get("suggested_ingest_arguments"):
                    exc.args = (str(exc) + " A source-verified high-level retry is available in recovery.suggested_ingest_arguments; use it before manual preparation.",)
            raise
        if result.get("import_status") == "unsupported_layout":
            table = self._candidate(args["source_id"], args["table_id"])
            recovery = self._alternative_recovery(table, args)
            if recovery.get("alternative_candidates") or recovery.get("next_request"):
                result["recovery"] = recovery
                if recovery.get("suggested_ingest_arguments"):
                    result["next_step"] = recovery["next_step"] + " Advanced fallback remains scoped to the unsupported candidate only."
        return result

    def _compile_candidate(self, args):
        source_id, table_id = args["source_id"], args["table_id"]
        table = self._candidate(source_id, table_id)
        if ((table.get("origin") == "ocr" or table.get("missing_formula_cache") or table.get("layout_review_required")) and not table.get("review")):
            recovery = self._alternative_recovery(table, args, requires_review=True)
            alternative = recovery.get("suggested_ingest_arguments", {}).get("table_id")
            message = "This extraction requires independent cell review before financial import."
            if alternative:
                message += f" A separate same-page parsed candidate {alternative} contains the requested labels. Retry the exact suggested_ingest_arguments; do not retry or publish the rejected candidate."
            raise DocumentError(message, "TABLE_REVIEW_REQUIRED", recovery=recovery)
        manifest = self.documents.source(source_id)
        is_csv = manifest.get("filename", "").casefold().endswith(".csv") or manifest.get("mime_type", "").split(";")[0] == "text/csv"
        embedded_dates = any(len(self.documents._source_date_occurrences(" ".join(str(value or "") for value in row)[:2000], fmt)) >= 1
                             for row in table["rows"][:30] for fmt in ("english_dmy", "dmy"))
        long_dates = any(_label_key(table["original_columns"].get(column, column)) in {"date", "month", "period", "tarih", "ay", "donem", "year"}
                         and all(isinstance(row[index], str) and re.fullmatch(r"\d{4}(?:-(?:\d{2}(?:-\d{2})?|Q[1-4]))?", row[index]) for row in table["rows"])
                         for index, column in enumerate(table["columns"]))
        if long_dates or is_csv and not embedded_dates:
            return {"status": "ok", "import_status": "unsupported_layout", "publication_performed": False,
                    "source_id": source_id, "table_id": table_id,
                    "message": "This source is already a record-oriented table or has dates in its column headers, rather than embedded statement header rows. No publication occurred. Use the advanced table publication path with its actual existing columns; do not reinterpret records as financial statement line items.",
                    "next_step": "Use publish_selected_table with the existing tabular schema, or prepare_source_table for a source-header pivot. Preserve native date frequency and exact source unit evidence."}
        if len(table["rows"]) > 5000 or len(table["columns"]) > 64:
            return {"status": "ok", "import_status": "unsupported_layout", "publication_performed": False,
                    "message": "Use a bounded statement candidate through inspect_source and the advanced preparation tools."}
        rows = self._select_rows(table, args["row_numbers"], args["row_labels"])
        labels, separator, resolved_labels = self._labels(table, rows)
        numeric = self._amount_columns(table, rows, labels)
        if args["value_columns"]:
            numeric = list(dict.fromkeys([*numeric, *[column for column in args["value_columns"] if column in table["columns"] and column not in labels]]))
        if not numeric:
            return {"status": "ok", "import_status": "unsupported_layout", "publication_performed": False,
                    "message": "No aligned amount columns were found for these lines. Use source review and advanced prepare_source_table for this layout."}
        values, dates, recipe, date_basis = self._dates(table, rows, numeric, args["value_header"], args["value_columns"])
        periods = args["periods"]
        if periods:
            missing = sorted(set(periods) - set(dates.values()))
            if missing:
                self._refuse("Requested periods must occur in this statement's actual date headers.", "IMPORT_PERIOD_NOT_FOUND", requested_missing=missing, available_periods=sorted(set(dates.values())))
            values = [column for column in values if dates[column] in periods]
            recipe["period_sources"] = {column: recipe["period_sources"][column] for column in values}
        style, dtype, equations = self._number_style(table, rows, values, args["number_style"])
        amount, semantic_evidence = self._semantics(table, values, args["measure_kind"])
        amount["dtype"] = dtype
        unpivot = {"columns": values, "period_column": "period", "value_column": "amount", **recipe}
        prepared = self.documents.prepare_source_table(source_id, table_id, selected_rows=rows, selected_columns=[*labels, *values],
            unpivot=unpivot, join_columns={"columns": labels, "output": "line_item", "separator": separator} if len(labels) > 1 else None)
        contract = {"name": "source_financial_facts", "frequency": "event", "date_column": "period", "key": ["line_item", "period"],
                    "grain": ["line_item", "period"], "expected_rows": len(rows) * len(values), "number_format": style,
                    "negative_format": "accounting_parentheses", "null_values": sorted(_NULLS), "columns": {
                        "line_item": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False},
                        "period": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "amount": amount}}
        mapping = {column: ("line_item" if len(labels) == 1 and column == labels[0] else column) for column in prepared["columns"]}
        publication = {"source_id": source_id, "table_id": prepared["table_id"], "contract": contract,
                       "expected_version": args["expected_version"], "column_mapping": mapping, "unit_evidence": {"amount": semantic_evidence["unit_quote"]}}
        prepared_candidate = self._candidate(source_id, prepared["table_id"])
        receipt = {"compiler_version": 2, "arguments": args, "source_raw_sha256": table["raw_sha256"],
                   "source_table_id": table_id, "source_candidate_sha256": hashlib.sha256(_canonical(table)).hexdigest(),
                   "prepared_table_id": prepared["table_id"], "source_review_sha256": table.get("review", {}).get("review_sha256"),
                   "prepared_candidate_sha256": hashlib.sha256(_canonical(prepared_candidate)).hexdigest(),
                   "publication_key": self.documents._publication_key(publication),
                   "row_numbers": rows, "line_items": resolved_labels, "source_periods": {column: dates[column] for column in values},
                   "date_mapping_evidence": date_basis, "semantic_evidence": semantic_evidence,
                   "numeric_format_evidence": {"number_style": style, "selected_by_caller": args["number_style"] is not None, "printed_source_equations": equations},
                   "publication_arguments": publication}
        receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()
        return receipt

    def _result(self, publication, receipt):
        if publication.get("status") != "ok":
            return publication
        import pandas as pd
        from agentic_analytics.lakehouse.financial_semantics import cumulative_evidence

        amount = publication["published_columns"]["amount"]
        # Read the immutable, hash-verified published records. Compiler input
        # labels or later source reviews are not authoritative dataset keys.
        frame = pd.read_parquet(self.store.overlay_path(publication["dataset_id"]))
        labels = sorted(frame["line_item"].unique().tolist())
        status = "review_required" if amount.get("kind") == "unknown" or amount.get("status") == "review_required" or cumulative_evidence(amount) else "ready"
        available = []
        for label in labels[:30]:
            periods = sorted(frame.loc[frame["line_item"] == label, "period"].astype(str).unique().tolist())
            available.append({"metric_id": f"overlay:{publication['dataset_id']}:amount", "dimensions": {"line_item": label},
                              "native_frequency": publication["frequency"], "kind": amount["kind"], "status": status,
                              "unit": amount["unit"], "scale": amount.get("scale", 1), "currency": amount.get("currency"),
                              "observed_periods": periods[:12], "observed_periods_count": len(periods),
                              "observed_periods_truncated": len(periods) > 12})
        result = {**publication, "import_status": "published", "publication_performed": True,
                  "available_series": available, "available_series_count": len(labels), "available_series_truncated": len(labels) > 30,
                  "compile_receipt": {key: value for key, value in receipt.items() if key != "publication_arguments"}}
        result["analysis_request"] = {"tool": "aggregate_dataset", "arguments": {
            "dataset_id": publication["dataset_id"], "group_by": "line_item", "time_bucket": {"frequency": "daily"},
            "measures": [{"name": "reported_amount", "op": "source_value", "column": "amount"}]}}
        result["next_step"] = "For standalone source display, run analysis_request and chart its saved analysis. To combine this source with existing metrics, use available_series metric_id and dimensions in execute at the intended common frequency. Preserve native event dates; ready stock/count_stock may use alignment=period_end only at exact output calendar endpoints. Do not aggregate_dataset first merely to enable cross-source arithmetic. Reporting flows keep unresolved interval semantics; raw display does not permit temporal summation."
        return result

    def ingest_source_table(self, source_id, table_id, expected_version, row_numbers=None, row_labels=None,
                            periods=None, value_header=None, value_columns=None, measure_kind="unknown", number_style=None):
        args = self._arguments(source_id, table_id, expected_version, row_numbers, row_labels, periods, value_header, value_columns, measure_kind, number_style)
        path = self._receipt_path(args)
        if path.exists():
            receipt = self._load_receipt(path)
            recovered = self._find_committed(receipt)
            if recovered.get("status") == "ok":
                return self._result(recovered, receipt)
        else:
            receipt = self._compile(args)
            if receipt.get("import_status") == "unsupported_layout":
                return receipt
            _write_json(path, receipt)
        self._validate_candidate_identity(receipt)
        publication = self.documents.publish_selected_table(**receipt["publication_arguments"])
        return self._result(publication, receipt)

    @staticmethod
    def _load_receipt(path):
        receipt = json.loads(path.read_text())
        saved_hash = receipt.pop("receipt_sha256", None)
        if hashlib.sha256(_canonical(receipt)).hexdigest() != saved_hash:
            raise DocumentError("Compiled import receipt hash mismatch.", "IMPORT_RECEIPT_HASH_MISMATCH")
        receipt["receipt_sha256"] = saved_hash
        return receipt

    def _find_committed(self, receipt):
        # The original key remains authoritative even if a later human review
        # changes the current candidate. Never rewrite an immutable dataset.
        key = receipt.get("publication_key")
        if key:
            return self.documents._find_publication(key)
        return self.documents.recover_publication(receipt["publication_arguments"], {})

    def _validate_candidate_identity(self, receipt):
        source_id = receipt["arguments"]["source_id"]
        original = self._candidate(source_id, receipt["source_table_id"])
        prepared = self._candidate(source_id, receipt["prepared_table_id"])
        if (hashlib.sha256(_canonical(original)).hexdigest() != receipt["source_candidate_sha256"]
                or hashlib.sha256(_canonical(prepared)).hexdigest() != receipt.get("prepared_candidate_sha256")
                or self.documents._publication_key(receipt["publication_arguments"]) != receipt.get("publication_key")):
            self._refuse("Source extraction or review changed after compilation. The saved compilation cannot publish different cells; select and compile the reviewed source again.",
                         "IMPORT_SOURCE_CHANGED", source_table_id=receipt["source_table_id"], prepared_table_id=receipt["prepared_table_id"])

    def recover(self, args, intent):
        args = self._arguments(**args)
        path = self._receipt_path(args)
        if not path.exists():
            return None if self.store.workspace(self.workspace_id)["revision_id"] == intent.get("input_revision") else {
                "status": "blocked", "code": "UNKNOWN_MUTATION_OUTCOME", "message": "No compiled receipt exists and workspace changed; refusing replay."}
        receipt = self._load_receipt(path)
        recovered = self._find_committed(receipt)
        if recovered.get("status") == "ok":
            return self._result(recovered, receipt)
        workspace = self.store.workspace(self.workspace_id)
        if workspace["revision_id"] == intent.get("input_revision") and workspace["version"] == args["expected_version"]:
            try:
                self._validate_candidate_identity(receipt)
            except DocumentError as exc:
                return {"status": "blocked", "code": exc.code, "message": str(exc)}
            return None
        return {"status": "blocked", "code": "UNKNOWN_MUTATION_OUTCOME", "message": "Workspace changed without the exact compiled publication; refusing uncertain replay."}

    def extra_tools(self):
        import jsonschema
        parameters = {"type": "object", "additionalProperties": False,
            "required": ["source_id", "table_id", "expected_version"], "properties": {
                "source_id": {"type": "string"}, "table_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 0},
                "row_labels": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"type": "string", "maxLength": 300}, "description": "Actual report line labels, matched uniquely including fragmented source labels. Use this OR row_numbers."},
                "row_numbers": {"type": "array", "minItems": 1, "maxItems": 30, "uniqueItems": True, "items": {"type": "integer", "minimum": 1}, "description": "Alternative exact 1-based candidate row selection."},
                "periods": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True, "items": {"type": "string"}, "description": "Optional ISO date filters over dates actually present in the source. Dates are never inserted or inferred from this request."},
                "value_header": {"type": "string", "maxLength": 100, "description": "Optional actual source subheader such as Total when a period has multiple currency components. Omit when the statement has one amount column per period."},
                "value_columns": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True, "items": {"type": "string"}, "description": "Advanced alternative selection of existing source value columns; dates still require unique source evidence."},
                "measure_kind": {"type": "string", "enum": ["stock", "flow", "unknown"], "default": "unknown", "description": "Normally omitted: derive balances versus reporting flows from the actual statement title. A choice cannot override a contradictory source title."},
                "number_style": {"type": "string", "enum": ["decimal_dot", "decimal_dot_grouped", "decimal_comma"], "description": "Normally omitted. Only choose after AMBIGUOUS_IMPORT_NUMBERS presents actual source alternatives; no locale or financial-magnitude guessing."}}}

        def handler(args):
            try:
                jsonschema.validate(args, parameters)
                return self.ingest_source_table(**args)
            except jsonschema.ValidationError as exc:
                return {"status": "blocked", "code": "INVALID_ARGUMENTS", "message": exc.message}
            except VersionConflict as exc:
                result = {"status": "blocked", "code": "VERSION_CONFLICT", "message": str(exc),
                          "submitted_version": args.get("expected_version"), "publication_performed": False}
                try:
                    workspace = self.store.workspace(self.workspace_id)
                except StoreError:
                    result["recovery"] = {"publication_performed": False,
                        "next_step": "Read the current workspace revision, then retry ingest_source_table with its current expected_version and the same source cells. Do not guess a version or change the source selection."}
                    return result
                result.update(current_version=workspace["version"], current_revision_id=workspace["revision_id"])
                result["recovery"] = {"publication_performed": False,
                    "suggested_ingest_arguments": {**args, "expected_version": workspace["version"]},
                    "next_step": "Read current_version and current_revision_id above before retrying ingest_source_table. Keep the same source, table, row, period and value selections; only expected_version is updated in suggested_ingest_arguments. This is a retry suggestion, not a completed publication; if the workspace changes again, refresh its revision again."}
                return result
            except (DocumentError, StoreError, ValueError, KeyError, TypeError) as exc:
                return {"status": "blocked", "code": getattr(exc, "code", "IMPORT_FAILED"), "message": str(exc),
                        **({"recovery": exc.recovery} if getattr(exc, "recovery", None) else {})}

        return {"ingest_source_table": {"schema": {"type": "function", "function": {
            "name": "ingest_source_table", "description": "Import selected financial statement lines and source periods in one operation. The system derives and validates cell joins, dates, numeric syntax, money scale and source provenance, then publishes immutable line_item/period/amount facts. Prefer this to manually writing an ETL contract. For standalone display use analysis_request. To combine with existing metrics use available_series metric_id/dimensions in execute; ready event stocks support exact period_end alignment. Ambiguity requires business selection or review; only unsupported_layout permits advanced preparation fallback.",
            "parameters": parameters}}, "handler": handler, "mutating": True, "recover": self.recover}}
