"""One bounded decision-maker over typed, durable analytics tools.

The LLM does not receive a database connection, arbitrary SQL, local filesystem
paths or a shell. Tool results are journaled before advancing the conversation.
"""
from __future__ import annotations

import copy
import json
import re
import time

import duckdb
import jsonschema

from agentic_analytics.agent.context import _compact, _model_tool_result, model_messages, workspace_context
from agentic_analytics.agent.delivery import (
    _analysis_confirmation, _cell_confirmation, _chart_confirmation, _requests_chart, _requests_table,
    _scope_confirmation, _statistics_confirmation,
)
from agentic_analytics.agent.run_store import AgentRunStore, canonical, fingerprint
from agentic_analytics.agent.schemas import COLUMN_NAME, obj
from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
from agentic_analytics.lakehouse.discovery import initial_query
from agentic_analytics.lakehouse.service import LakehouseService, PlanError, error_envelope
from agentic_analytics.providers.mia import MiaError


def _blocked(code, message):
    return {"status": "blocked", "errors": [{"code": code, "message": message}]}


def _schema_validation_error(error, tool_schema):
    """Explain the invalid shape without echoing submitted values or objects."""
    def brief(value):
        return str(value)[:96]

    def expected_value(value):
        return value if value is None or isinstance(value, (bool, int, float)) else brief(value) if isinstance(value, str) else type(value).__name__

    def locations(field):
        found = []
        def walk(schema, path=(), depth=0):
            if not isinstance(schema, dict) or depth > 8 or len(found) >= 3:
                return
            for name, child in schema.get("properties", {}).items():
                if name == field:
                    found.append(".".join((*path, name)))
                walk(child, (*path, name), depth+1)
            if isinstance(schema.get("items"), dict):
                walk(schema["items"], (*path, "[]"), depth+1)
            if isinstance(schema.get("additionalProperties"), dict):
                walk(schema["additionalProperties"], (*path, "*"), depth+1)
            for keyword in ("oneOf", "anyOf", "allOf"):
                for child in schema.get(keyword, []):
                    walk(child, path, depth+1)
        walk(tool_schema)
        return list(dict.fromkeys(found))[:3]

    errors = [(error, ())]
    if error.validator in {"oneOf", "anyOf"} and isinstance(error.instance, dict):
        # A tagged operation such as op=ratio should receive its own required
        # fields, not errors from every other operation in the union.
        variants = error.validator_value
        compatible = [variant for variant in variants if isinstance(variant, dict) and not any(
            "const" in spec and name in error.instance and error.instance[name] != spec["const"]
            for name, spec in variant.get("properties", {}).items())]
        if len(compatible) == 1:
            errors = [(item, tuple(error.absolute_path)) for item in
                      list(jsonschema.Draft202012Validator(compatible[0]).iter_errors(error.instance))[:3]] or errors
    details = []
    for item, prefix in errors:
        path = ".".join(brief(part) for part in (*prefix, *item.absolute_path))[:240] or "<root>"
        info = {"path": path, "validator": item.validator}
        schema, instance = item.schema, item.instance
        properties = schema.get("properties", {})
        if item.validator in {"additionalProperties", "required"}:
            info["allowed_properties"] = [brief(key) for key in list(properties)[:24]]
            if isinstance(instance, dict):
                missing = [key for key in schema.get("required", []) if key not in instance]
                if missing:
                    info["missing_fields"] = [brief(key) for key in missing[:16]]
                if item.validator == "additionalProperties":
                    unknown = [key for key in instance if key not in properties and not any(
                        re.search(pattern, key) for pattern in schema.get("patternProperties", {}))]
                    info["unexpected_fields"] = [brief(key) for key in unknown[:16]]
                    suggestions = {brief(key): locations(key) for key in unknown[:4]}
                    if any(suggestions.values()):
                        info["accepted_field_locations"] = {key: value for key, value in suggestions.items() if value}
        elif item.validator in {"enum", "const", "type", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "minLength", "maxLength"}:
            expected = item.validator_value
            info["allowed_values" if item.validator in {"enum", "const"} else "expected"] = (
                [expected_value(value) for value in expected[:24]] if isinstance(expected, list) else expected_value(expected))
        elif item.validator == "not" and isinstance(item.validator_value, dict) and "const" in item.validator_value:
            info["forbidden_value"] = expected_value(item.validator_value["const"])
        elif item.validator in {"oneOf", "anyOf"}:
            tags = {}
            for variant in item.validator_value:
                if not isinstance(variant, dict):
                    continue
                for name, spec in variant.get("properties", {}).items():
                    if "const" in spec:
                        tags.setdefault(name, []).append(brief(spec["const"]))
            if tags:
                info["allowed_variant_tags"] = {name: list(dict.fromkeys(values))[:24] for name, values in list(tags.items())[:4]}
        details.append(info)
    encoded = canonical(details)
    if len(encoded) > 3000:
        details = details[:1]
        for key in ("allowed_properties", "unexpected_fields", "missing_fields"):
            if key in details[0]:
                details[0][key] = details[0][key][:8]
        details[0].pop("accepted_field_locations", None)
        encoded = canonical(details)
    return _blocked("INVALID_TOOL_ARGUMENTS", "Tool arguments violate schema; no tool was executed. "
                    + encoded[:3500] + ". The full permitted schema is supplied with this tool.")


def _refines_task_plan(previous, proposed, path=()):
    """Allow added source-known detail while retaining every prior constraint."""
    if isinstance(previous, dict):
        return isinstance(proposed, dict) and all(
            key in proposed and _refines_task_plan(value, proposed[key], (*path, key))
            for key, value in previous.items())
    if isinstance(previous, list):
        if path in {("deliverables",), ("summary", "statistics"), ("summary", "columns"), ("statistics", "methods"), ("normalization", "columns")}:
            return isinstance(proposed, list) and set(previous).issubset(proposed)
        # Window order determines the comparison's base; retain it exactly.
        return previous == proposed
    if path == ("summary", "compare_windows") and previous is False:
        return isinstance(proposed, bool)
    return previous == proposed


def _requests_shared_scale(message):
    """Recognize explicit normalization commands, not general finance intent."""
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    for clause in re.split(r"[.;\n]", text):
        if re.search(r"eşitleme(?:yin|n|z)?\b|eşitlenmesin|getirme(?:yin)?\b|gerek\s+yok|do\s+not|don't|without|not\s+(?:the\s+)?same", clause):
            continue
        if re.search(r"(?:ne demek|nedir|ne anlama|nasil|what (?:is|does)|how (?:to|can i|do i)|explain (?:the )?(?:concept|meaning))", clause):
            continue
        if re.search(r"\b(?:ayni|ortak)\s+(?:para\s+birimi\s+ve\s+|birim(?:de)?\s+ve\s+)?ölçe[kğ]\w*", clause):
            return True
        if re.search(r"\bölçe[kğ]\w*\s+eşitle(?:yin|yiniz|meni|menizi)?\b", clause):
            return True
        if re.search(r"\b(?:same|common)\s+(?:(?:currency|units?)\s+and\s+)?scale\b", clause):
            return True
    return False


def _unreadable(content):
    """Flag a final answer that is garbled: replacement characters, non-Latin/Turkish
    script, or degenerate repetition with almost no coherent words, so it is
    regenerated rather than delivered."""
    if "�" in content:
        return True
    letters = [c for c in content if c.isalpha()]
    stripped = content.strip()
    # Degenerate model output: a long answer carrying almost no real words (repetitive
    # digits/symbols) is garbage, not a Turkish sentence or a numeric table answer.
    if (len(stripped) >= 100 and len(re.findall(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{2,}", content)) <= 3
            and len(letters) / len(stripped) < 0.10):
        return True
    if not letters:
        return False
    def foreign(c):
        return ("一" <= c <= "鿿" or "぀" <= c <= "ヿ"
                or "가" <= c <= "힣" or "Ѐ" <= c <= "ӿ"
                or "؀" <= c <= "ۿ")
    foreign_count = sum(foreign(c) for c in letters)
    return foreign_count > 3 and foreign_count / len(letters) > 0.10


def _grounded_refusal(barren):
    """A completed, grounded 'not found' answer built from the barren-discovery
    signal, so a genuinely absent concept ends as a stated refusal rather than a
    budget-death blocked non-answer."""
    terms = ", ".join(t for t in (barren.get("uncovered_terms") or []) if t) or "istenen seri"
    message = f"İstenen '{terms}' için kaynakta eşleşen bir seri bulunamadı; değer uydurulmaz."
    near = barren.get("near_titles") or []
    if near:
        message += " Kaynaktaki en yakın seriler: " + "; ".join(near) + "."
    return message + " Farklı bir seri, kapsam veya dönem belirtirseniz analizi ona göre yapabilirim."


def _row_not_found_refusal(barren):
    """A completed 'row not found' answer for a run that repeatedly looked up a
    dimension value the series does not contain (e.g. a national/aggregate row in a
    province-only series) and would otherwise die on the budget with no answer."""
    value = (barren.get("query") or "").strip() or "istenen satır"
    dimension = f" ({barren['dimension']} boyutunda)" if barren.get("dimension") else ""
    return (f"'{value}' değeri bu seride{dimension} bulunamadı; il/kalem bazlı bir seride ulusal ya da toplam "
            "bir satır her zaman yer almaz ve mevcut olmayan bir satır türetilmez. "
            "Var olan bir değer, kapsam veya dönem belirtirseniz analizi ona göre yapabilirim.")


def _normalize_result(result):
    """Preserve tool payloads while giving every failure one error contract."""
    if not isinstance(result, dict):
        raise PlanError("Tool result must be a JSON object")
    result = copy.deepcopy(result)
    if result.get("status") in {"blocked", "error", "failed", "unavailable"} and not result.get("errors"):
        code = result.get("code", "TOOL_UNAVAILABLE" if result["status"] == "unavailable" else "TOOL_FAILED")
        if code == "WRITE_OUTCOME_UNKNOWN":
            code = "UNKNOWN_MUTATION_OUTCOME"
        result["errors"] = [{"code": code, "message": result.get("message", "Tool could not complete the request.")}]
    return result


def _web_research_message(result):
    lines = ["Web kaynakları doğrudan okunarak bulundu. Aşağıdaki bilgiler yalnızca içerikleri okunabilen kaynaklara dayanır.", ""]
    for index, source in enumerate(result.get("sources", []), 1):
        title = source.get("title") or source.get("domain") or "Kaynak"
        date = source.get("date_published") or source.get("date_modified")
        summary = " ".join(str(source.get("content", "")).split())[:500]
        lines.append(f"{index}. **{title}**")
        if date:
            lines.append(f"   Yayın tarihi: {date}")
        if summary:
            lines.append(f"   Kaynak metninden kısa bölüm: {summary}")
        for table in source.get("tables", [])[:1]:
            # preview rows are dicts keyed by the SANITIZED column names; original_columns
            # maps those to the human header. Look up by sanitized key, display the header.
            keys = table.get("columns") or []
            header_map = table.get("original_columns") if isinstance(table.get("original_columns"), dict) else {}
            headers = [header_map.get(key) or key for key in keys]
            rows = table.get("preview", [])[:8]
            if keys and rows:
                lines.append("   Tablo:")
                lines.append("   | " + " | ".join(str(header) for header in headers) + " |")
                lines.append("   | " + " | ".join("---" for _ in keys) + " |")
                for row in rows:
                    cells = [str(row.get(key, "")) for key in keys] if isinstance(row, dict) else [str(value) for value in row][:len(keys)]
                    lines.append("   | " + " | ".join(cells) + " |")
        if source.get("url"):
            lines.append(f"   Kaynak: [{source.get('url')}]({source.get('url')})")
        else:
            lines.append(f"   Yüklenen kaynak: {title}")
        lines.append("")
    if result.get("failures"):
        lines.append(f"Not: {len(result['failures'])} kaynak okunamadığı için listeye alınmadı.")
    return "\n".join(lines).strip()


def _web_research_failure_message(result):
    code = result.get("code")
    if code == "OFFICIAL_SOURCE_NOT_FOUND":
        return "İstenen resmi kurum alanında konuya uygun ve okunabilir bir kaynak bulunamadı. İlgisiz web siteleri kaynak olarak kullanılmadı."
    if code == "NO_READABLE_SOURCES":
        return "Arama sonuçları bulundu ancak doğrudan okunabilen ve konuya uygun bir kaynak bulunamadı. Arama snippet'leri kanıt olarak kullanılmadı."
    return result.get("message") or "Web araştırması güvenilir bir kaynak okuyamadı."


class AgentRuntime:
    def __init__(self, store, workspace_id, client, run_store: AgentRunStore,
                 extra_tools=None, *, max_decisions=10, max_repairs=2,
                 service=None, max_context_chars=75000, max_elapsed_seconds=240):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 30 or type(max_repairs) is not int or not 0 <= max_repairs <= 5:
            raise ValueError("Invalid agent decision/repair budget")
        if type(max_context_chars) is not int or not 8000 <= max_context_chars <= 500000 or not 10 <= max_elapsed_seconds <= 3600:
            raise ValueError("Invalid context or elapsed-time budget")
        self.store, self.workspace_id, self.client, self.run_store = store, workspace_id, client, run_store
        self.service = service or LakehouseService(store, workspace_id)
        self.max_decisions, self.max_repairs, self.max_context_chars = max_decisions, max_repairs, max_context_chars
        self.max_elapsed_seconds = max_elapsed_seconds
        self.tools = self._tools()
        for name, definition in (extra_tools or {}).items():
            if name in self.tools or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                raise ValueError("Duplicate or invalid extra tool name")
            if not callable(definition.get("handler")) or definition.get("schema", {}).get("function", {}).get("name") != name:
                raise ValueError("Extra tool requires matching schema and callable handler")
            jsonschema.Draft202012Validator.check_schema(definition["schema"]["function"]["parameters"])
            self.tools[name] = dict(definition)

    def _tools(self):
        definitions = lakehouse_tools(self.service)
        text_field = {"type": "string", "minLength": 1, "maxLength": 100}
        summary = obj({
            "columns": {"type": "array", "minItems": 1, "maxItems": 6, "uniqueItems": True, "items": text_field},
            "statistics": {"type": "array", "minItems": 1, "maxItems": 9, "uniqueItems": True,
                           "items": {"enum": ["first", "last", "min", "max", "sum", "mean", "count", "change", "growth"]}},
            "windows": {"type": "array", "minItems": 1, "maxItems": 4,
                        "items": obj({"start": text_field, "end": text_field})},
            "compare_windows": {"type": "boolean"},
        }, ["statistics"])
        summary["description"] = "Omit before a saved analysis exists. Later specify only user-requested statistics on actual saved columns/native periods. Initial example: {deliverables:[analysis,chart,sources]}."
        parameters = obj({
            "deliverables": {"type": "array", "minItems": 1, "maxItems": 7, "uniqueItems": True,
                             "items": {"enum": ["analysis", "chart", "sources", "dataset", "statistics", "summary", "explanation"]}},
            "summary": summary,
            "normalization": obj({
                "same_unit_scale": {"const": True},
                "columns": {"type":"array", "minItems":2, "maxItems":12, "uniqueItems":True, "items":COLUMN_NAME,
                    "description":"Independent saved monetary amount columns to present in one unit/currency/scale. Only bind after a saved analysis exists. A recorded scale-only descendant can satisfy each column; raw originals and percentage ratios are exempt. Exactly two monetary source roots can be selected automatically."},
                "target_scale": {"type":"number", "exclusiveMinimum":0,
                    "description":"Optional user-requested absolute output scale: 1=base units, 1000=thousands, 1000000=millions. This never authorizes currency conversion."},
            }, ["same_unit_scale"]),
            "statistics": obj({"methods": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                                            "items": {"enum": ["rolling_anomalies", "detect_changes", "analyze_relationship"]}}}),
        }, ["deliverables"])
        definitions["plan_task"] = {
            "schema": {"type": "function", "function": {"name": "plan_task", "parameters": parameters,
                "description": "Declare only user-requested outputs. For an explicit common unit/scale request, include normalization:{same_unit_scale:true}; source-known columns and a requested target_scale may be added later. Before an analysis is saved, summary details are rejected until actual saved columns/native periods exist. Same-row ratios are analysis outputs, not period summaries. Retain all previous requirements, never weaken constraints. Use explanation alone only for educational examples."}},
            "handler": lambda args: {"status": "ok", "task_plan": copy.deepcopy(args)},
        }
        return definitions

    def _context(self, state):
        context = workspace_context(self.store, self.workspace_id, state,
                                    max_decisions=self.max_decisions,
                                    charts_enabled="create_chart" in self.tools)
        if state.get("request_normalization") or (state.get("task_plan") or {}).get("normalization"):
            context["current_task"]["normalization_requirement"] = (
                (state.get("task_plan") or {}).get("normalization") or state["request_normalization"])
        if "ingest_source_table" in self.tools:
            context["source_workflow"] = {"primary_tool": "ingest_source_table",
                "advanced_tools_require": "explicit unsupported_layout for the exact source and table",
                "authorized_tables": [{"source_id": source, "table_id": table, "root_table_id": root}
                    for source, tables in state.get("advanced_source_tables", {}).items() for table, root in tables.items()]}
        return context

    def _model_tool_schemas(self, state):
        hidden = ({"prepare_source_table", "publish_selected_table"}
                  if "ingest_source_table" in self.tools and not any(state.get("advanced_source_tables", {}).values()) else set())
        return [definition["schema"] for name, definition in self.tools.items() if name not in hidden]

    def _advanced_source_allowed(self, state, args):
        return ("ingest_source_table" not in self.tools or
                args.get("table_id") in state.get("advanced_source_tables", {}).get(args.get("source_id"), {}))

    @staticmethod
    def _clear_source_workflow_errors(state, source_id, table_id):
        # These calls were denied before execution. A successful replacement
        # path resolves only the capability error, never a source/semantic error.
        unresolved = state.get("unresolved_errors", {})
        for name in ("prepare_source_table", "publish_selected_table"):
            remaining = [error for error in unresolved.get(name, []) if not (
                error.get("code") == "SOURCE_WORKFLOW_REQUIRED" and error.get("source_id") == source_id
                and error.get("table_id") == table_id)]
            if remaining:
                unresolved[name] = remaining
            else:
                unresolved.pop(name, None)

    def _update_source_capabilities(self, state, name, args, result):
        if "ingest_source_table" not in self.tools or name not in {"ingest_source_table", "prepare_source_table", "publish_selected_table"}:
            return
        source_id, table_id = args.get("source_id"), args.get("table_id")
        if not source_id or not table_id:
            return
        grants = state.setdefault("advanced_source_tables", {})
        tables = grants.setdefault(source_id, {})
        if name == "ingest_source_table":
            unsupported = (result.get("status") == "ok" and result.get("import_status") == "unsupported_layout"
                           and result.get("publication_performed") is False and not result.get("dataset_id"))
            if unsupported:
                tables[table_id] = table_id
                self._clear_source_workflow_errors(state, source_id, table_id)
            else:
                root = tables.get(table_id, table_id)
                family = {table_id, root, *(candidate for candidate, parent in tables.items() if parent == root)}
                for candidate in family:
                    tables.pop(candidate, None)
                if result.get("status") == "ok" and result.get("dataset_id"):
                    for candidate in family:
                        self._clear_source_workflow_errors(state, source_id, candidate)
        elif result.get("status") == "ok" and table_id in tables:
            if name == "prepare_source_table" and result.get("source_id") == source_id and result.get("table_id"):
                if result.get("preparation", {}).get("source_table_id") == table_id:
                    tables[result["table_id"]] = tables[table_id]
                    self._clear_source_workflow_errors(state, source_id, result["table_id"])
            elif name == "publish_selected_table" and result.get("dataset_id"):
                root = tables[table_id]
                for candidate in {root, *(candidate for candidate, parent in tables.items() if parent == root)}:
                    self._clear_source_workflow_errors(state, source_id, candidate)
        if not tables:
            grants.pop(source_id, None)

    def _messages(self, state):
        return model_messages(state, context_factory=self._context,
                              charts_enabled="create_chart" in self.tools,
                              max_context_chars=self.max_context_chars)

    def run(self, message, conversation_id=None, request_id=None):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 16000:
            raise ValueError("message must be a nonempty string of at most 16000 characters")
        with self.run_store.workspace_lock(self.workspace_id):
            record = self.run_store.start(self.workspace_id, message, conversation_id, request_id)
            return self._run(record)

    def resume(self, run_id):
        with self.run_store.workspace_lock(self.workspace_id):
            record = self.run_store.get(run_id)
            if record["workspace_id"] != self.workspace_id:
                raise ValueError("Run belongs to another workspace")
            return self._run(record)

    def _run(self, record):
        if record["result"] is not None:
            return record["result"]
        # Elapsed time is bounded per active invocation, using a monotonic clock.
        # Application downtime never consumes this budget. The total decision
        # count remains durable across every resume and is never reset here.
        invocation_started = time.monotonic()
        state, run_id = record["state"], record["run_id"]
        try:
            if "request_normalization" not in state:
                state["request_normalization"] = {"same_unit_scale": True} if _requests_shared_scale(record["message"]) else None
            if state.get("delivery_pending") and not state["pending"]:
                return self._complete(record, state, **state["delivery_pending"])
            if "initial_candidates" not in state:
                # The model can refine this bounded natural-language search.
                state["initial_candidates"] = _model_tool_result("discover", self.service.discover({"query": initial_query(record["message"]), "limit": 5}))
                self.run_store.event(run_id, "run_started", {"workspace_id": self.workspace_id, "conversation_id": record["conversation_id"], "request_id": record["request_id"]})
                self.run_store.checkpoint(run_id, state)
            while state["decisions"] < self.max_decisions or state["pending"]:
                if state["pending"]:
                    call = state["pending"][0]
                    result = self._dispatch(run_id, state, call)
                    state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_model_tool_result(call["function"]["name"], result))})
                    state["tool_results"].append({"tool": call["function"]["name"], "call_id": call["id"], "result": _compact(result)})
                    if call["function"]["name"] in {"ingest_source_table", "prepare_source_table", "publish_selected_table"}:
                        try:
                            capability_args = json.loads(call["function"]["arguments"])
                        except (ValueError, TypeError):
                            capability_args = {}
                        if isinstance(capability_args, dict):
                            self._update_source_capabilities(state, call["function"]["name"], capability_args, result)
                    if (self.tools.get(call["function"]["name"], {}).get("mutating")
                            and result.get("status") == "ok" and result.get("publication_performed") is not False):
                        # A compiler can successfully inspect an unsupported
                        # layout without publishing. This capability outcome
                        # must not be remembered as a committed write.
                        write_key = fingerprint({"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])})
                        state.setdefault("successful_writes", {})[write_key] = result
                    state["pending"].pop(0)
                    if call["function"]["name"] == "plan_task" and result.get("status") == "ok":
                        state["task_plan"] = result["task_plan"]
                    # Reading a web page is an intermediate result. The same turn
                    # may still need to inspect another URL, publish a table,
                    # calculate, summarize, or create a chart.
                    if result.get("analysis_id") and result.get("status") in {"ok", "valid"}:
                        state["analysis_id"] = result["analysis_id"]
                        if call["function"]["name"] in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"}:
                            state["analysis_updated"] = True
                            if state.get("chart_analysis_id") != result["analysis_id"]:
                                state["chart_updated"] = False
                                state["chart_id"] = None
                                state["chart_columns"] = []
                                state["recommendations"] = []
                    if call["function"]["name"] == "create_chart" and result.get("status") == "ok":
                        state["chart_id"] = result.get("chart_id")
                        state["chart_analysis_id"] = result.get("analysis_id")
                        state["chart_updated"] = bool(result.get("chart_id"))
                        state["chart_columns"] = result.get("spec", {}).get("columns", [])
                        state["recommendations"] = result.get("recommendations", [])[:3]
                    if call["function"]["name"] == "discover" and isinstance(result, dict):
                        # Remember an unresolved discovery so a genuinely absent concept can end
                        # as a grounded refusal. Guard: once any ready candidate has been seen,
                        # a later barren search is a mid-analysis stall, not a missing series.
                        if any(isinstance(m, dict) and m.get("status") == "ready" for m in (result.get("metrics") or [])):
                            state["saw_ready_candidate"] = True
                            state.pop("discovery_barren", None)
                        elif result.get("no_confident_match"):
                            state["discovery_barren"] = {"uncovered_terms": result.get("uncovered_terms") or [],
                                                         "near_titles": [m.get("title") for m in (result.get("near_matches") or []) if isinstance(m, dict) and m.get("title")][:3]}
                    elif call["function"]["name"] in {"describe", "dimension_values", "validate_plan"} and result.get("status") in {"ok", "valid"}:
                        state.pop("discovery_barren", None)
                        # A dimension lookup that returns nothing for a named value means the
                        # requested row (e.g. a national/aggregate row in province-only data)
                        # does not exist; remember it so a looping run refuses by naming the
                        # missing value instead of dying blank. A later non-empty lookup clears it.
                        if call["function"]["name"] == "dimension_values":
                            lookup = (json.loads(call["function"]["arguments"] or "{}").get("query") or "").strip()
                            if lookup and not result.get("total"):
                                state["dimension_barren"] = {"query": lookup, "dimension": result.get("dimension")}
                            elif result.get("total"):
                                state.pop("dimension_barren", None)
                    failed = result.get("status") in {"blocked", "error", "failed", "unavailable"}
                    unresolved = state.setdefault("unresolved_errors", {})
                    tool_name = call["function"]["name"]
                    if failed:
                        unresolved[tool_name] = result.get("errors", [])
                    elif result.get("status") in {"ok", "valid"}:
                        unresolved.pop(tool_name, None)
                        if tool_name == "research_web" and result.get("sources"):
                            state["web_research_completed"] = True
                            # A web result can recover a failed search, but does
                            # not repair an invalid calculation or failed chart.
                            unresolved.pop("web_search", None)
                            for lookup in ("discover", "describe", "dimension_values"):
                                if unresolved.get(lookup) and all(error.get("code") in {
                                        "METRIC_NOT_FOUND", "DIMENSION_VALUE_NOT_FOUND", "SOURCE_NOT_FOUND"}
                                        for error in unresolved[lookup]):
                                    unresolved.pop(lookup, None)
                        if tool_name in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"}:
                            for resolved_name in ("execute", "revise_analysis", "query_grouped", "aggregate_dataset", "validate_plan"):
                                unresolved.pop(resolved_name, None)
                    for key in ("artifact_ref", "artifact_id", "source_id"):
                        if result.get(key):
                            item = {"kind": key, "id": result[key]}
                            if item not in state["artifacts"]:
                                state["artifacts"].append(item)
                    self.run_store.checkpoint(run_id, state)
                    if state.get("delivery_pending") and call["id"] == state.get("automatic_summary_call_id"):
                        if failed:
                            state.setdefault("delivery_warnings", []).extend(result.get("errors", []))
                            unresolved.pop(tool_name, None)
                        return self._complete(record, state, **state["delivery_pending"])
                    if result.get("status") == "needs_input":
                        # A clarifying question asked AFTER a result was produced this turn
                        # must not bury it behind a dead-end needs_input; present the saved
                        # analysis/chart and surface the question with it instead.
                        if state.get("analysis_updated") or state.get("chart_updated"):
                            return self._complete(record, state, result["message"], followup=True,
                                                warnings=[{"code": "CLARIFICATION_AFTER_RESULT", "message": "A result was produced this turn; the model's follow-up question is surfaced alongside it rather than pausing for input."}])
                        return self._finish(record, state, "needs_input", result["message"])
                    if result.get("status") in {"blocked", "error", "failed", "unavailable"}:
                        state["repairs"] += 1
                        self.run_store.checkpoint(run_id, state)
                        if state["repairs"] > self.max_repairs or any(e.get("code") in {"UNKNOWN_MUTATION_OUTCOME", "NO_PROGRESS"} for e in result.get("errors", [])):
                            refusal = self._barren_refusal(state)
                            if refusal:
                                return self._finish(record, state, "completed", refusal[0], warnings=[refusal[1]])
                            return self._finish(record, state, "blocked", "Analiz güvenilir biçimde tamamlanamadı. Araç hata ayrıntıları kaydedildi.", errors=result.get("errors", []))
                    continue

                messages = self._messages(state)
                elapsed = time.monotonic() - invocation_started
                if elapsed >= self.max_elapsed_seconds:
                    return self._finish(record, state, "blocked", "Bu çalıştırmanın aktif süre sınırına ulaşıldı; kaydedilmiş sonuçlar korundu.", errors=[{"code": "TIME_BUDGET_EXCEEDED", "message": "No further provider request was started after this active invocation's deadline; the total decision budget remains durable."}])
                # Persist the budget debit before network I/O, so a crash cannot
                # reset provider-call limits or pretend a request was free.
                state["decisions"] += 1
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "model_request", {"decision": state["decisions"], "message_count": len(messages)})
                # The MIA live probe confirmed this server option avoids
                # exhausting the bounded output on private reasoning alone.
                tool_schemas = self._model_tool_schemas(state)
                response = self.client.chat(messages, tools=tool_schemas, temperature=0, max_tokens=4096, enable_thinking=False)
                calls = response.get("tool_calls") or []
                content = response.get("content")
                usage = response.get("usage") or {}
                state["usage"].append(usage)
                self.run_store.event(run_id, "model_response", {"decision": state["decisions"], "finish_reason": response.get("finish_reason"), "tool_names": [c["function"]["name"] for c in calls], "usage": usage, "response_id": response.get("response_id"), "request_meta": response.get("request_meta", {})})
                if response.get("finish_reason") == "length":
                    state["repairs"] += 1
                    state["messages"].append({"role": "assistant", "content": "Önceki model çıktısı kesildi; hiçbir araç çalıştırılmadı."})
                    self.run_store.checkpoint(run_id, state)
                    if state["repairs"] > self.max_repairs:
                        return self._finish(record, state, "blocked", "Model çıktısı izin verilen uzunlukta tamamlanamadı.", errors=[{"code": "TRUNCATED_MODEL_OUTPUT", "message": "No truncated tool call was executed."}])
                    continue
                if calls:
                    state["messages"].append({"role": "assistant", "content": content, "tool_calls": copy.deepcopy(calls)})
                    state["pending"] = copy.deepcopy(calls)
                    self.run_store.checkpoint(run_id, state)
                    continue
                if isinstance(content, str) and content.strip():
                    if _unreadable(content):
                        state["repairs"] += 1
                        state["messages"].append({"role": "assistant", "content": "Önceki yanıt okunaksız veya yanlış dildeydi; yalnızca Türkçe, okunabilir bir son cevap gerekiyor."})
                        self.run_store.checkpoint(run_id, state)
                        if state["repairs"] > self.max_repairs:
                            return self._finish(record, state, "blocked", "Model okunabilir bir Türkçe cevap üretemedi.", errors=[{"code": "UNREADABLE_MODEL_OUTPUT", "message": "Final answer failed the charset/language readability check."}])
                        continue
                    missing_outputs = self._task_delivery_errors(state) + self._numeric_evidence_errors(state, content)
                    corrective_errors = self._corrective_delivery_errors(state)
                    if ((missing_outputs or corrective_errors)
                            and (not state.get("unresolved_errors") or corrective_errors)
                            and state.get("delivery_repairs", 0) < 1 and state["decisions"] < self.max_decisions):
                        state["delivery_repairs"] = state.get("delivery_repairs", 0) + 1
                        state["messages"].append({"role": "assistant", "content":
                            "Teslim kontrolü: gerekli kaynak kanıtı veya belirtilen çıktılar henüz oluşmadı. "
                            "Son cevap yerine eksik araç adımlarını tamamla; gereksinimleri azaltma. "
                            + canonical(missing_outputs + corrective_errors)})
                        self.run_store.event(run_id, "delivery_repair", {"errors": missing_outputs + corrective_errors})
                        self.run_store.checkpoint(run_id, state)
                        continue
                    return self._complete(record, state, content)
                state["repairs"] += 1
                state["messages"].append({"role": "assistant", "content": "Model boş yanıt verdi; geçerli bir araç çağrısı veya son cevap gerekiyor."})
                self.run_store.checkpoint(run_id, state)
                if state["repairs"] > self.max_repairs:
                    return self._finish(record, state, "blocked", "Model geçerli bir cevap üretmedi.", errors=[{"code": "EMPTY_MODEL_RESPONSE", "message": "No content or tool calls."}])
            if (state.get("analysis_updated") or state.get("chart_updated")) and not state.get("unresolved_errors"):
                return self._complete(record, state, "Analiz kaydedildi; son yanıtı üretme sınırına ulaşıldı.", terminal_status="partial", warnings=[{"code": "FINAL_RESPONSE_BUDGET_EXCEEDED", "message": "Verified analysis is available; no additional provider call was made for prose synthesis."}])
            refusal = self._barren_refusal(state)
            if refusal:
                return self._finish(record, state, "completed", refusal[0], warnings=[refusal[1]])
            return self._finish(record, state, "blocked", "Bu adımın model çağrı sınırına ulaşıldı; mevcut sonuçlar korundu.", errors=[{"code": "DECISION_BUDGET_EXCEEDED", "message": "Bounded agent decision budget reached."}])
        except MiaError as exc:
            return self._finish(record, state, "failed", str(exc), errors=[{"code": exc.code, "message": str(exc), "retryable": exc.retryable, "attempts": exc.attempts, "usage_unknown": True}])
        except (ValueError, OSError, duckdb.Error) as exc:
            error = error_envelope(exc)
            return self._finish(record, state, "blocked", "Çalışma alanı veya plan doğrulaması tamamlanamadı.", errors=error["errors"])

    def _complete(self, record, state, content, *, followup=False, warnings=None, terminal_status="completed"):
        """One delivery boundary for prose and clarification-after-result exits.

        A model's stop reason is not evidence that the requested artifact exists.
        Numerical analysis prose is built from saved bytes before it reaches the
        user or becomes context for the next turn.
        """
        self._close_pending(state)
        if not state.get("analysis_id") and not state.get("tool_results") and re.search(r"\d", content):
            active_analysis = self.store.workspace(self.workspace_id).get("analysis_head")
            if active_analysis:
                # Numerical followups must read the current saved result rather
                # than reconstructing cells from compact conversation memory.
                state["analysis_id"] = active_analysis
                state["analysis_observed"] = True
        state["delivery_pending"] = {"content": content, "followup": followup,
                                     "warnings": warnings, "terminal_status": terminal_status}
        self.run_store.checkpoint(record["run_id"], state)
        self._default_summary(record, state)
        warnings = [*(warnings or []), *state.get("delivery_warnings", [])]
        errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
        errors.extend(self._task_delivery_errors(state))
        errors.extend(self._numeric_evidence_errors(state, content))
        if "create_chart" in self.tools and _requests_chart(record["message"]) and not state.get("chart_updated"):
            errors.append({"code": "CHART_NOT_CREATED", "message": "This turn requested a chart but produced no saved chart artifact."})
        source_table = any(
            item.get("result", {}).get("status") == "ok" and (
                item["result"].get("tables") or any(source.get("tables") for source in item["result"].get("sources", [])))
            for item in state.get("tool_results", []))
        if _requests_table(record["message"]) and not state.get("analysis_updated") and not source_table:
            # A chart-only edit can legitimately retain the existing table.
            if not state.get("chart_updated"):
                errors.append({"code": "TABLE_NOT_CREATED", "message": "This turn requested a table but produced no saved analysis or inspected source table."})
        quantitative = _analysis_confirmation(self.store, self.workspace_id, state)
        statistics = _statistics_confirmation(self.store, self.workspace_id, state)
        cells = _cell_confirmation(self.store, self.workspace_id, state)
        chart = _chart_confirmation(state, record["message"])
        scope = _scope_confirmation(self.store, self.workspace_id, state)
        grounded = "\n\n".join(part for part in (chart or quantitative, statistics, cells, scope) if part)
        web_sources, seen_urls = [], set()
        for item in state.get("tool_results", []):
            result = item.get("result", {})
            if item.get("tool") != "research_web" or result.get("status") != "ok":
                continue
            for source in result.get("sources", []):
                if source.get("url") and source["url"] not in seen_urls:
                    seen_urls.add(source["url"])
                    web_sources.append(source)
        if web_sources:
            source_message = _web_research_message({"sources": web_sources})
            grounded = "\n\n".join(part for part in (grounded, source_message) if part)
        if errors:
            has_output = state.get("analysis_updated") or state.get("chart_updated") or bool(web_sources)
            message = "Bazı istenen adımlar tamamlanamadı; kaydedilen sonuçlar ve hata ayrıntıları korundu."
            if not has_output:
                message = "İstenen işlem tamamlanamadı; yeni bir analiz veya grafik sonucu üretilmedi."
            if grounded:
                message += "\n\n" + grounded
            return self._finish(record, state, "partial" if has_output else "blocked", message, errors=errors,
                                **({"warnings": warnings} if warnings else {}))
        message = grounded or content
        if set((state.get("task_plan") or {}).get("deliverables", [])) == {"explanation"} and not grounded:
            message = "Genel açıklama (kaynak verilerden hesaplanmış bir sonuç değildir):\n\n" + content
        if followup and grounded:
            # Keep a clearly labeled question separate from computed facts.
            message += "\n\nDevam için soru: " + content
        return self._finish(record, state, terminal_status, message,
                            **({"warnings": warnings} if warnings else {}))

    def _default_summary(self, record, state):
        """Journal a bounded factual receipt without spending a provider decision.

        Custom calculations stay explicit model tool calls. This fallback never
        chooses a sum, custom period window, or cross-period comparison for the
        user, and cannot satisfy a task contract that requires those operations.
        """
        if "summarize_analysis" not in self.tools or not state.get("analysis_updated"):
            return
        analysis_id = state.get("analysis_id")
        if any(item.get("tool") == "summarize_analysis" and item.get("result", {}).get("analysis_id") == analysis_id
               for item in state.get("tool_results", [])):
            return
        args = {"analysis_id": analysis_id, "statistics": ["first", "last"]}
        call = {"id": "runtime_summary_" + fingerprint(args)[:16], "type": "function", "function": {
            "name": "summarize_analysis", "arguments": canonical(args)}}
        state["automatic_summary_call_id"] = call["id"]
        state["messages"].append({"role": "assistant", "content": None, "tool_calls": [call]})
        state["pending"] = [call]
        self.run_store.checkpoint(record["run_id"], state)
        result = self._dispatch(record["run_id"], state, call)
        state["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                  "content": canonical(_model_tool_result("summarize_analysis", result))})
        state["tool_results"].append({"tool": "summarize_analysis", "call_id": call["id"],
                                      "automatic": True, "result": _compact(result)})
        state["pending"] = []
        if result.get("status") == "ok":
            for key in ("artifact_id", "artifact_ref"):
                if result.get(key):
                    item = {"kind": key, "id": result[key]}
                    if item not in state["artifacts"]:
                        state["artifacts"].append(item)
        else:
            # A default receipt failure does not make a valid table disappear.
            # The failure is visible and cannot satisfy a required summary.
            state.setdefault("delivery_warnings", []).extend(result.get("errors", []))
        self.run_store.checkpoint(record["run_id"], state)

    def _remember_chart_capacity_selection(self, state, args):
        """A capacity error cannot silently remove a proven saved selection."""
        columns = args.get("columns")
        if (not isinstance(columns, list) or not columns or not all(isinstance(name, str) for name in columns)
                or len(columns) != len(set(columns)) or not isinstance(args.get("analysis_id"), str)):
            return
        try:
            from pandas.api.types import is_bool_dtype, is_numeric_dtype
            frame, manifest = self.store.load_analysis(args["analysis_id"])
            group_by = manifest.get("lineage", {}).get("group_by")
            available = {name for name in frame if name not in {"period", "rank", group_by}
                         and is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])
                         and manifest.get("schema", {}).get(name, {}).get("kind") not in {"dimension", "rank"}}
            if manifest.get("workspace_id") != self.workspace_id or not set(columns) <= available:
                return
        except (ValueError, OSError, duckdb.Error):
            return
        required = state.setdefault("chart_capacity_selections", {}).setdefault(args["analysis_id"], [])
        required.extend(name for name in columns if name not in required)

    @staticmethod
    def _chart_coverage_errors(state):
        selections = state.get("chart_capacity_selections", {})
        analysis_id = state.get("analysis_id") or state.get("chart_analysis_id")
        if analysis_id is None and len(selections) == 1:
            analysis_id = next(iter(selections))
        required = selections.get(analysis_id, [])
        actual = (set(state.get("chart_columns", [])) if state.get("chart_updated")
                  and state.get("chart_analysis_id") == analysis_id else set())
        missing = [name for name in required if name not in actual]
        return ([{"code": "CHART_SELECTION_INCOMPLETE", "analysis_id": analysis_id,
                  "missing_columns": missing,
                  "message": "The earlier chart request contained these saved numeric columns, but they were omitted after a capacity error. Restore the selection or report partial completion; a smaller chart does not complete that request."}]
                if missing else [])

    def _task_delivery_errors(self, state):
        plan = state.get("task_plan") or {}
        required = plan.get("deliverables", [])
        chart_coverage_errors = self._chart_coverage_errors(state) + self._normalization_errors(state)
        if not required:
            return chart_coverage_errors
        successful = [item for item in state.get("tool_results", []) if item.get("result", {}).get("status") == "ok"]
        results = [item["result"] for item in successful]
        evidence = {
            "analysis": bool(state.get("analysis_updated")),
            "chart": bool(state.get("chart_updated")),
            "sources": any(result.get("sources") or result.get("source_id") for result in results),
            "dataset": any(result.get("dataset_id") for result in results),
            "statistics": any(item["tool"] in {"rolling_anomalies", "detect_changes", "analyze_relationship", "summarize_analysis"}
                              and item["result"].get("analysis_id") == state.get("analysis_id") for item in successful),
            "summary": False,
            "explanation": True,
        }
        summary_requirement = plan.get("summary") or {}
        required_methods = (plan.get("statistics") or {}).get("methods", [])
        if required_methods:
            actual_methods = {item["tool"] for item in successful
                              if item["result"].get("analysis_id") == state.get("analysis_id")}
            evidence["statistics"] = set(required_methods).issubset(actual_methods)
        if "summary" in required or summary_requirement:
            from agentic_analytics.agent.tools.summary import SummaryTools
            for item in successful:
                result = item["result"]
                if item["tool"] != "summarize_analysis" or result.get("analysis_id") != state.get("analysis_id"):
                    continue
                summary = SummaryTools(self.store, self.workspace_id).load_artifact(result["artifact_id"])
                parameters = summary["parameters"]
                required_statistics = set(summary_requirement.get("statistics", []))
                required_columns = set(summary_requirement.get("columns", []))
                actual_statistics = {fact["statistic"] for fact in summary["facts"]}
                requested_windows = [(window["start"], window["end"]) for window in summary_requirement.get("windows", [])]
                actual_windows = [(window["start"], window["end"]) for window in parameters.get("windows", [])]
                if (not required_statistics.issubset(actual_statistics)
                        or not required_columns.issubset(parameters.get("columns", []))
                        or (requested_windows and requested_windows != actual_windows)
                        or (summary_requirement.get("compare_windows") and not parameters.get("compare_windows"))):
                    continue
                relevant = [fact for fact in summary["facts"] if fact["statistic"] in required_statistics
                            or (summary_requirement.get("compare_windows") and fact["statistic"].startswith("window_"))]
                if relevant and any(fact.get("value") is None for fact in relevant):
                    continue
                evidence["summary"] = True
                break
        return [{"code": "TASK_DELIVERABLE_MISSING", "deliverable": name,
                 "message": f"The declared task requires {name}; no matching completed output was produced."}
                for name in required if not evidence[name]] + chart_coverage_errors

    def _normalization_errors(self, state):
        """Verify requested common-scale amounts against immutable analysis.

        Only an explicit task/request condition activates this gate. Scale-only
        descendants can fulfill a source amount; ratios, differences and a
        duplicate copy of the same source cannot stand in for another amount.
        """
        requirement = (state.get("task_plan") or {}).get("normalization") or state.get("request_normalization")
        if not requirement:
            return []
        aid = state.get("analysis_id")
        if not aid:
            return [{"code":"NORMALIZATION_ANALYSIS_MISSING", "message":"The requested common-unit/scale comparison has no saved analysis."}]
        from pandas.api.types import is_bool_dtype, is_numeric_dtype
        from agentic_analytics.lakehouse.presentation import column_quantity_lineage
        frame, manifest = self.store.load_analysis(aid)
        if manifest.get("workspace_id") != self.workspace_id:
            raise PlanError("Normalization analysis belongs to another workspace")
        schema, lineage = manifest.get("schema", {}), manifest.get("lineage", {})
        quantities, _, _, _ = column_quantity_lineage(frame, lineage.get("operations", []))
        available = {name for name in frame if is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])
                     and schema.get(name, {}).get("currency") and schema.get(name, {}).get("kind") not in {"ratio", "rate", "growth", "index", "dimension", "rank"}}
        sources = lineage.get("sources", {})
        def identity(name):
            kind, original = quantities[name]
            if kind != "source":
                return canonical({"operation":original})
            meta, source = schema.get(original, {}), sources.get(original, {})
            metric = meta.get("metric_id") or source.get("binding", {}).get("metric_id")
            return canonical({"metric_id":metric, "scope":meta.get("scope"),
                              "dimensions":source.get("dimensions")}) if metric else original
        columns = requirement.get("columns")
        if columns is None:
            candidates = [name for name in sources if name in available and quantities[name] == ("source", name)]
            columns = list({identity(name):name for name in candidates}.values())
            if len(columns) != 2:
                return [{"code":"NORMALIZATION_SELECTION_REQUIRED", "analysis_id":aid, "available_columns":sorted(available),
                         "message":"Bind normalization.columns to the independent amount series requested by the user. More or fewer than two source roots cannot be selected implicitly; exclude percentage ratios and raw/scaled duplicates."}]
        missing = [name for name in columns if name not in available]
        if missing:
            return [{"code":"NORMALIZATION_COLUMNS_INVALID", "analysis_id":aid, "columns":missing,
                     "available_columns":sorted(available), "message":"Normalization columns must be saved monetary amount columns, excluding percent/rate/ratio outputs."}]
        if len({identity(name) for name in columns}) != len(columns):
            return [{"code":"NORMALIZATION_DUPLICATE_SOURCE", "analysis_id":aid,
                     "message":"An original amount and its scaled copy represent one source series, not two independent comparison amounts."}]
        candidates = [[candidate for candidate in available if quantities[candidate] == quantities[name]] for name in columns]
        signatures = [{(schema[name].get("unit"),schema[name].get("currency"),schema[name].get("scale",1))
                       for name in group} for group in candidates]
        common = set.intersection(*signatures)
        if requirement.get("target_scale") is not None:
            common = {signature for signature in common if signature[2] == requirement["target_scale"]}
        if common:
            return []
        metadata = {name:{key:schema[name].get(key) for key in ("unit","currency","scale")} for group in candidates for name in sorted(group)}
        currencies = {schema[name].get("currency") for name in columns}
        return [{"code":"NORMALIZATION_NOT_SATISFIED", "analysis_id":aid, "columns":columns, "saved_units":metadata,
                 "target_scale":requirement.get("target_scale"), "currency_conversion_required":len(currencies)>1,
                 "message":("The saved independent amounts do not share the requested unit, currency and scale. "
                            "Currency conversion is not authorized or performed by scale; obtain an explicit supported conversion or report the limitation."
                            if len(currencies)>1 else
                            "The saved amounts still have different units/scales. Use revise_analysis with scale operations and fresh output aliases at one absolute target_scale, then rebuild the requested chart and source proofs on the revised analysis. Original source values stay unchanged; a correct percentage ratio alone does not satisfy this request.")}]

    @staticmethod
    def _corrective_delivery_errors(state):
        """Offer one final-stage repair for explicit representation errors only.

        No evidence gap, unsafe semantic request or unknown write outcome is
        softened here. Actual corrective tool calls retain the ordinary repair,
        decision, time and no-progress limits.
        """
        hints = {
            "INVALID_UNIT_SEMANTICS": "Kaynak birimini koru: TRY_thousand gibi ölçekli birim için scale=1, ya da temel TRY birimi için scale=1000 kullan; ikisini birlikte uygulama. Kaynak kanıtını değiştirme.",
            "INVALID_UNPIVOT": "Dönemi tahmin etme. Kaynaktaki gerçek başlık satırını ve hücreyi oku; birleşik başlıklarda unpivot.period_sources ile her değer sütununu kaynak row/columns adreslerine eşle veya doğru sütunları seç.",
            "INVALID_SOURCE_DATE_FORMAT": "Kaynak başlığındaki gerçek tarihleri kullan. Hatanın recovery.suggested_unpivot_update önerisini ve kaynak hücre adreslerini incele; bir hücrede birden çok tam tarih varsa uygun date_index seçimini kaynak üzerinden doğrula, tarih uydurma.",
            "INVALID_COLUMN_MAPPING": "Gerçek candidate_columns ve sözleşme sütunlarını kullan. column_mapping kaynak sütunu -> çıktı sütunu yönünde eksiksiz birebir eşleme olmalı; gereksiz yeniden adlandırmada identity mapping kullan.",
            "INVALID_DATASET_GROUP": "Hata içindeki gerçek kategori sütununu group_by olarak seç; value/period gibi ayrılmış çıktı adını grup adı yapma. Tarih ekseni için gereken time_bucket koşullarını koru.",
            "TASK_PLAN_REQUIRES_ANALYSIS": "Henüz gerçek analiz şeması yok. Kullanıcının istediği çıktı türlerini koruyan ayrıntısız plan_task çağır; analizi kaydet, sonra yalnız istenen ve şemaya uygun özet koşullarını ekle.",
            "TASK_PLAN_INVALID_SUMMARY": "Geçersiz yeni özet koşulları kaydedilmedi. Gerçek sütun türlerini ve dönem etiketlerini oku; mevcut plan koşullarını koruyarak yalnız istenen, geçerli hesapları ekle.",
            "TASK_PLAN_INVALID_NORMALIZATION": "Yeni ölçek koşulları kaydedilmedi. Kayıtlı bağımsız tutar sütunlarını seç; oran sütununu veya aynı tutarın ölçekli kopyasını ikinci kaynak sayma. Önceki koşulları koru.",
        }
        errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
        if not errors or any(error.get("code") not in hints for error in errors):
            return []
        return [{**error, "recovery_hint": hints[error["code"]]} for error in errors]

    def _validate_task_summary(self, state, requirement):
        """Never lock a guessed or semantically impossible summary obligation."""
        from pandas.api.types import is_bool_dtype, is_numeric_dtype
        from agentic_analytics.agent.tools.summary import GROWTH_KINDS
        from agentic_analytics.lakehouse.service import _label, _period
        aid = state.get("analysis_id") or self.store.workspace(self.workspace_id).get("analysis_head")
        if not aid:
            raise PlanError("Declare abstract deliverables before a saved analysis exists. Summary details may be added after reading actual saved columns and native periods; no guessed constraints were saved.", code="TASK_PLAN_REQUIRES_ANALYSIS")
        frame, manifest = self.store.load_analysis(aid)
        if manifest.get("workspace_id") != self.workspace_id:
            raise PlanError("Summary plan analysis belongs to another workspace")
        schema = manifest.get("schema", {})
        group = manifest.get("lineage", {}).get("group_by")
        available = [name for name in frame if name not in {"period", "rank", group}
                     and is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])]
        columns = requirement.get("columns", available[:6])
        def invalid(message):
            raise PlanError(message + " No new summary constraints were saved.", code="TASK_PLAN_INVALID_SUMMARY")
        if not columns or any(name not in available for name in columns):
            invalid("Summary columns must exist in the saved numeric schema: " + ", ".join(available))
        for name in columns:
            meta = schema.get(name, {})
            for statistic in requirement["statistics"]:
                if statistic in {"sum", "mean", "change", "growth"} and meta.get("status") != "ready":
                    invalid(f"{name}: calculated summaries require reviewed semantics")
                if statistic == "sum" and (meta.get("kind") not in {"flow", "count_flow"} or meta.get("additive_over_time") is False):
                    invalid(f"{name}: period sums require additive, noncumulative flows")
                if statistic == "growth" and meta.get("kind") not in GROWTH_KINDS:
                    invalid(f"{name}: percentage growth is incompatible with {meta.get('kind')}; use a change only if the user requested it, and keep same-period column ratios in analysis")
        windows = requirement.get("windows", [])
        frequency = manifest.get("plan", {}).get("frequency") or manifest.get("lineage", {}).get("frequency")
        labels = frame["period"].astype(str)
        for window in windows:
            for bound in ("start", "end"):
                value = window[bound]
                try:
                    native = value in set(labels) if frequency == "static" else _label(_period(value, frequency)) == value
                except PlanError:
                    native = False
                if not native or value < labels.min() or value > labels.max():
                    invalid(f"Window {bound} must use the saved {frequency} calendar inside {labels.min()} to {labels.max()}; extend the analysis first when needed")
            if window["start"] > window["end"]:
                invalid("Window start must not follow its end")
        if requirement.get("compare_windows") and (len(windows) != 2 or not set(requirement["statistics"]).intersection({"sum", "mean", "first", "last"})):
            invalid("Window comparison needs exactly two native windows and a sum, mean, first or last basis")

    def _numeric_evidence_errors(self, state, content):
        """Gate unsupported numeric answers when the catalogue matched a dataset.

        Digits only select an evidence requirement, never validate arithmetic or
        whitelist values. Sourced dates, ratios and educational examples remain
        possible. The calculation's output is rendered from verified artifacts.
        """
        initial = state.get("initial_candidates") or {}
        if (not re.search(r"\d", content) or initial.get("no_confident_match")
                or not initial.get("metrics") or state.get("analysis_id")
                or self.store.workspace(self.workspace_id).get("analysis_head")):
            return []
        if set((state.get("task_plan") or {}).get("deliverables", [])) == {"explanation"}:
            return []
        for item in state.get("tool_results", []):
            result = item.get("result", {})
            if result.get("status") != "ok":
                continue
            if item.get("tool") in {"summarize_analysis", "explain_value", "rolling_anomalies", "detect_changes", "analyze_relationship"}:
                return []
            if item.get("tool") in {"inspect_source", "read_source_table", "research_web", "find_source_pages"}:
                if (result.get("text") or result.get("tables") or result.get("rows")
                        or any(page.get("text") for page in result.get("pages", []))
                        or any(match.get("excerpt") for match in result.get("matches", []))
                        or any(source.get("content") or source.get("text") or source.get("tables")
                               for source in result.get("sources", []))):
                    return []
        return [{"code": "NUMERICAL_EVIDENCE_MISSING", "message":
                 "A matching dataset exists, but the numerical answer has no supporting calculation or source-reading tool result."}]

    def _barren_refusal(self, state):
        """Pick a grounded refusal for a run terminating with no saved analysis because
        it looped on a missing row or a genuinely absent concept. Returns
        (message, warning) or None to fall through to the plain terminal. Loop errors
        (NO_PROGRESS) are non-blocking here; any real tool failure suppresses the refusal."""
        if state.get("analysis_updated") or state.get("chart_updated"):
            return None
        if any(e.get("code") not in {"NO_PROGRESS", "UNKNOWN_MUTATION_OUTCOME"}
               for errors in (state.get("unresolved_errors") or {}).values() for e in errors):
            return None
        if state.get("dimension_barren"):
            return (_row_not_found_refusal(state["dimension_barren"]),
                    {"code": "DIMENSION_VALUE_NOT_FOUND", "message": "Completed as a grounded refusal: the requested dimension value/row was repeatedly not found."})
        if state.get("discovery_barren") and not state.get("saw_ready_candidate"):
            return (_grounded_refusal(state["discovery_barren"]),
                    {"code": "METRIC_NOT_FOUND", "message": "Completed as a grounded refusal: no metric matched the requested concept within the decision budget."})
        return None

    def _expected_plan(self, name, args):
        if name == "execute":
            return copy.deepcopy(args)
        if name == "query_grouped":
            return {"query_type": "grouped", "request": copy.deepcopy(args)}
        if name == "revise_analysis":
            _, parent = self.store.load_analysis(args["analysis_id"])
            if parent.get("workspace_id") != self.workspace_id:
                raise PlanError("Analysis belongs to another workspace")
            if parent["plan"].get("query_type") == "grouped":
                if args.get("add_columns"):
                    raise PlanError("Grouped source additions require an explicit per-group mapping", code="GROUPED_SOURCE_MAPPING_REQUIRED")
                if args.get("start") or args.get("end"):
                    raise PlanError("Grouped period changes require a new grouped query", code="GROUPED_WINDOW_CHANGE_REQUIRED")
                plan = copy.deepcopy(parent["plan"])
                plan["request"].setdefault("operations", []).extend(copy.deepcopy(args.get("operations", [])))
                return plan
            if parent["plan"].get("query_type") == "dataset":
                raise PlanError("Dataset aggregates require a new explicit aggregation", code="DATASET_REVISION_REQUIRED")
            plan = copy.deepcopy(parent["plan"])
            for bound in ("start", "end"):
                if bound in args:
                    plan[bound] = args[bound]
            plan["columns"].extend(copy.deepcopy(args.get("add_columns", [])))
            plan.setdefault("operations", []).extend(copy.deepcopy(args.get("operations", [])))
            return plan
        return None

    def _recover(self, step, definition):
        intent, args, name = step["intent"], step["args"], step["name"]
        if definition.get("recover"):
            return definition["recover"](args, intent)
        if not definition.get("mutating"):
            return None
        if name not in {"execute", "revise_analysis", "query_grouped"}:
            return _blocked("UNKNOWN_MUTATION_OUTCOME", "Interrupted custom write has no reconciliation handler; it will not be repeated.")
        current = self.store.workspace(self.workspace_id)
        if current["revision_id"] == intent["input_revision"]:
            return None
        if current.get("analysis_head"):
            frame, manifest = self.store.load_analysis(current["analysis_head"])
            if manifest.get("workspace_revision_id") == intent["input_revision"] and manifest.get("plan") == intent["expected_plan"]:
                result = self.service._envelope(frame, manifest, [{"code": "RECOVERED_RESULT", "detail": "Previously committed result recovered after interruption; original transient warnings were not journaled."}])
                result["recovered"] = True
                return result
        return _blocked("UNKNOWN_MUTATION_OUTCOME", "Workspace changed after an interrupted tool; automatic replay is blocked.")

    def _dispatch(self, run_id, state, call):
        name = call["function"]["name"]
        step_id = f"{state['decisions']}:{call['id']}"
        definition = self.tools.get(name)
        if definition is None:
            result = _blocked("UNKNOWN_TOOL", "Requested tool is not registered.")
            self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
            return result
        try:
            raw_args = call["function"]["arguments"]
            if not isinstance(raw_args, str) or len(raw_args) > 48000:
                raise PlanError("Tool arguments exceed the request budget")
            args = json.loads(raw_args, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            if name == "plan_task" and isinstance(args, dict) and args.get("summary") == {}:
                # An empty optional container asserts no summary constraints.
                # Preserve the raw call in history and normalize only its
                # executable meaning to the same abstract plan as omission.
                args = {key: value for key, value in args.items() if key != "summary"}
            jsonschema.Draft202012Validator(definition["schema"]["function"]["parameters"]).validate(args)
            if name == "plan_task" and "normalization" not in args:
                normalization = (state.get("task_plan") or {}).get("normalization") or state.get("request_normalization")
                if normalization:
                    args = {**args, "normalization":copy.deepcopy(normalization)}
            if name in {"prepare_source_table", "publish_selected_table"} and not self._advanced_source_allowed(state, args):
                result = {"status": "blocked", "errors": [{"code": "SOURCE_WORKFLOW_REQUIRED",
                    "source_id": args["source_id"], "table_id": args["table_id"],
                    "message": "Use ingest_source_table for this source/table. Advanced preparation/publication is available only after its explicit unsupported_layout result. For review or semantic refusal, follow the compiler's source-based recovery; low-level ETL cannot bypass it."}]}
                self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                return result
            if name == "plan_task":
                if args.get("summary") and "summary" not in args["deliverables"]:
                    raise PlanError("Summary requirements must include the summary deliverable", code="INVALID_TASK_PLAN")
                if args.get("statistics") and "statistics" not in args["deliverables"]:
                    raise PlanError("Statistical method requirements must include the statistics deliverable", code="INVALID_TASK_PLAN")
                if state.get("task_plan") and not _refines_task_plan(state["task_plan"], args):
                    raise PlanError("The current task contract may only gain detail or stronger requirements; existing constraints and fixed windows cannot be removed or replaced", code="TASK_PLAN_LOCKED")
                if args.get("summary"):
                    self._validate_task_summary(state, args["summary"])
                if args.get("normalization", {}).get("columns"):
                    aid = state.get("analysis_id") or self.store.workspace(self.workspace_id).get("analysis_head")
                    if not aid:
                        raise PlanError("Declare normalization:{same_unit_scale:true} before analysis. Bind columns only after actual saved monetary columns exist; no guessed aliases were locked.", code="TASK_PLAN_REQUIRES_ANALYSIS")
                    invalid = [error for error in self._normalization_errors({**state,"analysis_id":aid,
                        "task_plan":{"normalization":args["normalization"]}})
                        if error["code"] in {"NORMALIZATION_COLUMNS_INVALID","NORMALIZATION_DUPLICATE_SOURCE"}]
                    if invalid:
                        raise PlanError(canonical(invalid) + " No new normalization constraints were saved.", code="TASK_PLAN_INVALID_NORMALIZATION")
            write_key = fingerprint({"name": name, "args": args})
            if definition.get("mutating") and write_key in state.get("successful_writes", {}):
                result = {**state["successful_writes"][write_key], "idempotent_replay": True}
                self.run_store.event(run_id, "tool_reused", {"tool": name, "call_id": call["id"], "result": result})
                return result
            step = self.run_store.step(run_id, step_id)
            if step:
                if step["args"] != args or step["name"] != name:
                    raise PlanError("Persisted call identity changed", code="CALL_ID_CONFLICT")
                if step["result"] is not None:
                    return step["result"]
                recovered = self._recover(step, definition)
                if recovered is not None:
                    recovered = _normalize_result(recovered)
                    self.run_store.complete_step(run_id, step_id, recovered)
                    self.run_store.event(run_id, "tool_recovered", {"tool": name, "call_id": call["id"], "result": recovered})
                    return recovered
            else:
                key = fingerprint({"name": name, "args": args, "revision": self.store.workspace(self.workspace_id)["revision_id"]})
                if state["seen"].get(key, 0) >= 2:
                    return _blocked("NO_PROGRESS", "The same tool request has already been attempted twice without a workspace change.")
                state["seen"][key] = state["seen"].get(key, 0) + 1
                current = self.store.workspace(self.workspace_id)
                intent = {"workspace_id": self.workspace_id, "input_revision": current["revision_id"], "workspace_version": current["version"], "expected_plan": self._expected_plan(name, args)}
                self.run_store.begin_step(run_id, step_id, name, args, intent)
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "tool_started", {"tool": name, "call_id": call["id"], "arguments": args})
            if name in {"execute", "revise_analysis"}:
                plan = step["intent"]["expected_plan"] if step else intent["expected_plan"]
                if plan.get("query_type") == "grouped":
                    # The grouped service validates each member under the same
                    # scalar semantics before committing one grouped revision.
                    result = definition["handler"](args)
                else:
                    validation = self.service.validate_plan(plan)
                    self.run_store.event(run_id, "plan_validation", {"tool": name, "call_id": call["id"], "plan": plan, "validation": validation})
                    if validation.get("status") != "valid":
                        result = validation
                    else:
                        result = definition["handler"](args)
            else:
                result = definition["handler"](args)
            result = _normalize_result(result)
            if name == "create_chart" and any(error.get("code") == "CHART_SERIES_LIMIT" for error in result.get("errors", [])):
                self._remember_chart_capacity_selection(state, args)
            canonical(result)
        except jsonschema.ValidationError as exc:
            if name == "create_chart" and exc.validator == "maxItems" and list(exc.absolute_path) == ["columns"]:
                self._remember_chart_capacity_selection(state, args)
            result = _schema_validation_error(exc, definition["schema"]["function"]["parameters"])
        except json.JSONDecodeError:
            result = _blocked("INVALID_TOOL_ARGUMENTS", "Tool arguments are incomplete or invalid JSON. No tool was executed; submit one complete JSON object.")
        except (ValueError, OSError, duckdb.Error) as exc:
            result = error_envelope(exc)
        # Only a persisted intent gets a persisted result. Invalid calls still
        # get an event and a matching tool response for provider round trips.
        if self.run_store.step(run_id, step_id):
            self.run_store.complete_step(run_id, step_id, result)
        self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
        return result

    @staticmethod
    def _close_pending(state):
        # Close every tool call before retaining history for the next user turn.
        for call in state["pending"]:
            state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_blocked("TOOL_NOT_EXECUTED", "Run stopped before this call."))})
        state["pending"] = []

    def _finish(self, record, state, status, message, **extra):
        if status in {"blocked", "failed", "partial"} and (state.get("analysis_updated") or state.get("chart_updated")):
            status = "partial"
            if not state.get("delivery_pending"):
                try:
                    parts = [_analysis_confirmation(self.store, self.workspace_id, state),
                             _statistics_confirmation(self.store, self.workspace_id, state),
                             _cell_confirmation(self.store, self.workspace_id, state),
                             _scope_confirmation(self.store, self.workspace_id, state)]
                    message += "\n\n" + "\n\n".join(part for part in parts if part)
                except (ValueError, OSError, duckdb.Error) as exc:
                    # An integrity/read failure must never relabel unverified
                    # bytes as a usable partial calculation.
                    status = "blocked"
                    extra["errors"] = [*extra.get("errors", []), *error_envelope(exc)["errors"]]
        if status == "completed" and state.get("task_plan"):
            # Grounded absence/refusal exits must also respect any compound
            # task's declared outputs. They cannot bypass the delivery boundary.
            missing = self._task_delivery_errors(state)
            if missing:
                status = "partial" if state.get("analysis_updated") or state.get("chart_updated") else "blocked"
                extra["errors"] = [*extra.get("errors", []), *missing]
        self._close_pending(state)
        state.pop("delivery_pending", None)
        state.pop("automatic_summary_call_id", None)
        if not state["messages"] or state["messages"][-1].get("role") != "assistant" or state["messages"][-1].get("content") != message:
            state["messages"].append({"role": "assistant", "content": message})
        workspace = self.store.workspace(self.workspace_id)
        result = {"run_id": record["run_id"], "conversation_id": record["conversation_id"], "request_id": record["request_id"],
                  "workspace_id": self.workspace_id, "status": status, "message": message,
                  "analysis_id": state.get("analysis_id"), "analysis_updated": state.get("analysis_updated", False),
                  "active_analysis_id": workspace.get("analysis_head"),
                  "chart_id": state.get("chart_id"), "chart_updated": state.get("chart_updated", False),
                  "recommendations": state.get("recommendations", []),
                  "artifacts": state["artifacts"], "tool_results": state["tool_results"],
                  "decisions": state["decisions"], "repairs": state["repairs"], "usage": state["usage"], **extra}
        self.run_store.finish(record["run_id"], state, result)
        self.run_store.event(record["run_id"], "run_finished", {"status": status, "analysis_id": result["analysis_id"], "decisions": state["decisions"]})
        return result
