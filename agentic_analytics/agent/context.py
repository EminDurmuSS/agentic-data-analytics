"""Build bounded model context while retaining complete durable tool records."""

import copy
import json

from agentic_analytics.agent.prompts import CHART_PROMPT, SYSTEM_PROMPT
from agentic_analytics.agent.run_store import canonical
from agentic_analytics.lakehouse.service import PlanError


def _compact(value, *, depth=0):
    """Bound model-facing results; complete tool outputs remain in the ledger."""
    if depth > 9:
        return "[nested detail retained in tool ledger]"
    if isinstance(value, dict):
        hidden = {"table", "source_base", "result_path", "raw_path", "local_path", "reasoning", "reasoning_content"}
        return {k: _compact(v, depth=depth + 1) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        items = [_compact(v, depth=depth + 1) for v in value[:30]]
        return items + ([{"remaining_items": len(value) - 30}] if len(value) > 30 else [])
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + " [truncated; full text retained in tool ledger]"
    return value


def _model_tool_result(name, result, *, terse=False):
    """Retrieval cards are navigation hints; describe carries full semantics.

    This view is intentionally separate from the full durable tool-result
    ledger. A request for 25 search matches never sends 25 verbose bindings to
    the model, and old search results can shed metadata without losing IDs.
    """
    if name in {"inspect_source", "read_source_table", "research_web", "find_source_pages"} and isinstance(result, dict):
        return _document_result(name, result)
    if name != "discover" or not isinstance(result, dict) or not isinstance(result.get("metrics"), list):
        return _compact(result)
    fields = ("metric_id", "title", "status", "value_dimension", "semantic_match") if terse else (
        "metric_id", "title", "source_system", "group_name", "value_dimension", "is_archive",
        "temporal_semantics", "unit", "scale", "currency", "kind",
        "native_frequency", "status", "dimensions", "matched_dimensions", "missing_terms",
        "semantic_profile", "semantic_match", "coverage_start", "coverage_end", "frequency_hint_requires_upsampling",
        "price_basis", "blocked_reason", "cumulative_evidence", "population_scope", "measurement_basis",
        "scope_caveats", "source_table_category", "source_scope_evidence", "source_scope_policy_version")

    def project(cards):
        out = [{key: copy.deepcopy(card[key]) for key in fields if key in card}
               for card in cards[:5 if terse else 10] if isinstance(card, dict)]
        for card in out:
            if isinstance(card.get("title"), str):
                card["title"] = card["title"][:80 if terse else 240]
        return out
    cards = project(result["metrics"])
    view = {key: copy.deepcopy(result[key]) for key in
            ("status", "snapshot_id", "query", "searched_query", "query_projection_note", "total", "errors",
             "no_confident_match", "uncovered_terms", "query_intent") if key in result}
    view.update(metrics=cards, model_card_count=len(cards),
                model_cards_truncated=len(result["metrics"]) > len(cards) or result.get("total", len(cards)) > len(cards))
    if isinstance(result.get("near_matches"), list):
        view["near_matches"] = project(result["near_matches"])
    if terse:
        view["historical_search_summary"] = True
    return _compact(view)


def _document_result(name, result):
    """A bounded navigation view, with full evidence retained in source artifacts.

    A large PDF must be searched and selectively read; thirty pages and table
    contexts are not thirty independent additions to every model request.
    Small sources retain their useful preview and source-unit evidence.
    """
    fields = ("status", "code", "message", "errors", "source_id", "artifact_ref", "filename", "mime_type",
              "source_url", "raw_sha256", "size_bytes", "total_pages", "processed_pages", "selected_pages",
              "inspection_complete", "content_is_untrusted_data", "warnings", "query", "next_step",
              "publication_guidance", "recovery",
              "table_id", "row_count", "sheet", "page", "read", "searched", "failures", "matches", "total_matches")
    view = {key: copy.deepcopy(result[key]) for key in fields if key in result}
    article = result.get("article") or {}
    source_text = article.get("article_body") or article.get("readable_text") or result.get("text")
    if isinstance(source_text, str):
        view["text"] = source_text[:2400]
        view["text_truncated"] = result.get("text_truncated", False) or len(source_text) > 2400
    if article:
        view["article"] = {key: copy.deepcopy(article[key]) for key in
                           ("title", "canonical_url", "date_published", "date_modified", "link_count") if key in article}
        links = article.get("source_links", [])
        body_links = [link for link in links if link.get("in_main_content") and not link.get("in_navigation")]
        selected = article.get("discovery_links") or body_links or links
        view["article"]["source_links"] = [{"title": str(link.get("title", ""))[:160],
                                             "url": str(link.get("url", ""))[:2048]}
                                            for link in selected[:24] if link.get("url")]
        view["article"]["model_links_truncated"] = article.get("model_links_truncated", False) or len(selected) > len(view["article"]["source_links"])
        view["article"]["link_selection"] = "query_ranked" if article.get("discovery_links") else "main_content" if body_links else article.get("link_selection", "source_order")
        view["article"]["document_links"] = article.get("document_links", [])[:5]
    def table_card(table):
        names = list(table.get("columns") or [])
        shown = names[:12]
        card = {key: copy.deepcopy(table[key]) for key in
                ("table_id", "page", "sheet", "row_count", "origin", "source_pages", "table_strategy",
                 "header_rows", "layout_review_required", "missing_formula_cache", "quality_notes", "review") if key in table}
        card.update(columns=shown, column_count=len(names), model_columns_truncated=len(names) > len(shown))
        for key in ("original_columns", "source_header_quotes"):
            if isinstance(table.get(key), dict):
                card[key] = {column: str(table[key][column])[:200] for column in shown if column in table[key]}
        if isinstance(table.get("context_text"), str):
            card["context_text"] = table["context_text"][:400]
        preview = table.get("preview", table.get("rows", []))
        card["preview"] = []
        for row in preview[:2]:
            mapped = row if isinstance(row, dict) else dict(zip(names, row))
            card["preview"].append({column: mapped[column][:100] if isinstance(mapped[column], str) else mapped[column]
                                    for column in shown if column in mapped})
        card["preview_truncated"] = table.get("preview_truncated", False) or table.get("row_count", len(preview)) > len(card["preview"])
        # Cards with exceptionally wide headers stay navigable; the table ID
        # remains enough to request its complete contract and selected rows.
        if len(canonical(card)) > 2600:
            card.pop("context_text", None)
            card["preview"] = card["preview"][:1]
            card.pop("original_columns", None)
        return card
    if isinstance(result.get("tables"), list):
        view["tables"] = [table_card(table) for table in result["tables"][:8] if isinstance(table, dict)]
        view["model_table_count"] = len(view["tables"])
        view["model_tables_truncated"] = len(result["tables"]) > len(view["tables"])
    if isinstance(result.get("pages"), list):
        view["pages"] = [{"page": page.get("page"), "text": str(page.get("text", ""))[:240],
                          "extraction_method": page.get("extraction_method")} for page in result["pages"][:10] if isinstance(page, dict)]
        view["model_pages_truncated"] = len(result["pages"]) > len(view["pages"])
    if name == "read_source_table":
        view.update(table_card(result))
        view.pop("preview", None)
        view["rows"] = [{"candidate_row": row["candidate_row"], "values": {
            column: value[:200] if isinstance(value, str) else value
            for column, value in list((row.get("values") or {}).items())[:16]},
            **({"raw_cells": row["raw_cells"], "requires_review": True} if "raw_cells" in row else {})}
            for row in result.get("rows", [])[:20]]
        view["model_rows_truncated"] = len(result.get("rows", [])) > len(view["rows"])
        view["next_row_start"] = (view["rows"][-1]["candidate_row"] + 1
                                  if view["rows"] and view["rows"][-1]["candidate_row"] < result.get("row_count", 0) else None)
    if name == "find_source_pages":
        view["matches"] = [{**match, "excerpt": str(match.get("excerpt", ""))[:1200]}
                           for match in result.get("matches", [])[:8]]
        for key in ("complete", "searched_pages", "next_start_page", "image_only_pages"):
            if key in result:
                view[key] = copy.deepcopy(result[key])
    if isinstance(result.get("sources"), list):
        sources = []
        for source in result["sources"][:3]:
            card = {key: copy.deepcopy(source[key]) for key in
                    ("source_id", "title", "url", "domain", "date_published", "date_modified", "verification",
                     "document_links", "discovery_links", "source_role", "raw_sha256", "inspection_complete", "warnings", "content_is_untrusted_data") if key in source}
            card["content"] = str(source.get("content", ""))[:1600]
            card["tables"] = [table_card(table) for table in source.get("tables", [])[:2]]
            sources.append(card)
        view["sources"] = sources
    view["model_evidence_view"] = True
    view["full_evidence_retained"] = True
    view["navigation_hint"] = "For long documents use find_source_pages, then inspect_source with explicit page_numbers or read_source_table. Omitted or shortened previews are not the complete table; publication reads the stored candidate, not this preview."
    # Even unusually large warning/metadata fields remain inside a fixed model
    # budget. This projection never changes raw source bytes or cached rows.
    view = _compact(view)
    while len(canonical(view)) > 24000 and len(view.get("tables", [])) > 1:
        view["tables"].pop()
        view["model_tables_truncated"] = True
        view["model_table_count"] = len(view["tables"])
    while len(canonical(view)) > 24000 and len(view.get("rows", [])) > 1:
        view["rows"].pop()
        view["model_rows_truncated"] = True
        view["next_row_start"] = view["rows"][-1]["candidate_row"] + 1
    while len(canonical(view)) > 24000 and len(view.get("article", {}).get("source_links", [])) > 1:
        view["article"]["source_links"].pop()
        view["article"]["model_links_truncated"] = True
    return view


def workspace_context(store, workspace_id, state, *, max_decisions, charts_enabled):
    workspace = store.workspace(workspace_id)
    context = {"workspace_id": workspace_id, "snapshot_id": workspace["snapshot_id"],
               "workspace_version": workspace["version"], "active_analysis_id": workspace.get("analysis_head"),
               "datasets": workspace.get("datasets", []), "remaining_decisions": max_decisions - state["decisions"]}
    if workspace.get("analysis_head"):
        _, manifest = store.load_analysis(workspace["analysis_head"])
        context["active_plan"] = manifest["plan"]
        context["active_schema"] = manifest.get("schema")
        if charts_enabled:
            from agentic_analytics.agent.tools.charts import ChartTools, ChartError
            try:
                chart = ChartTools(store, workspace_id).get_chart(workspace["analysis_head"])
                context["active_chart"] = {key: chart[key] for key in
                    ("chart_id", "analysis_id", "spec", "title", "recommendations") if key in chart}
            except (ChartError, OSError):
                context["active_chart"] = {"status": "unavailable", "analysis_id": workspace["analysis_head"]}
    context["artifacts"] = state.get("artifacts", [])[-10:]
    context["current_task"] = {
        "required_outputs": state.get("task_plan"),
        "analysis_id": state.get("analysis_id"),
        "analysis_updated": state.get("analysis_updated", False),
        "chart_updated": state.get("chart_updated", False),
        "chart_analysis_id": state.get("chart_analysis_id"),
        "chart_columns": state.get("chart_columns", []),
        "chart_capacity_selections": state.get("chart_capacity_selections", {}),
        "unresolved_errors": state.get("unresolved_errors", {}),
        "summary_results": [{key: result.get(key) for key in ("analysis_id", "summary_id", "parameters", "warnings")}
                            for item in state.get("tool_results", [])
                            if item.get("tool") == "summarize_analysis" and (result := item.get("result", {})).get("status") == "ok"],
    }
    context["initial_metric_candidates"] = _model_tool_result("discover", state.get("initial_candidates"))
    return context


def _published_navigation_receipt(name, result, arguments, publication):
    """Retain navigation and quality state after raw content became a dataset.

    The publication proves only its selected table/rows. Other candidates stay
    addressable and retain review warnings; this is never source-wide approval.
    """
    receipt = copy.deepcopy(result)
    for field in ("text", "context_text", "rows", "preview", "article"):
        receipt.pop(field, None)
    for field, bulky in (("pages", {"text"}), ("matches", {"excerpt", "text"}),
                        ("tables", {"preview", "rows", "context_text", "original_columns", "source_header_quotes"})):
        if isinstance(result.get(field), list):
            receipt[field] = [{key: copy.deepcopy(value) for key, value in item.items() if key not in bulky}
                              for item in result[field] if isinstance(item, dict)]
    if isinstance(result.get("rows"), list):
        receipt["read_row_addresses"] = [{key: row[key] for key in ("candidate_row", "requires_review") if key in row}
                                         for row in result["rows"] if isinstance(row, dict)]
    reread = copy.deepcopy(arguments)
    reread["source_id"] = result["source_id"]
    reread.pop("url", None)
    receipt.update(model_evidence_view="published_source_navigation_receipt", full_evidence_retained=True,
                   related_publication=copy.deepcopy(publication), re_read={"tool": name, "arguments": reread},
                   navigation_hint="Historical navigation content is retained in the source artifact and full tool ledger. Re-read these exact source/page/table/row addresses for raw values. Publication covered only its selected rows; other candidates and review warnings are not approved.")
    return receipt


def model_messages(state, *, context_factory, charts_enabled, max_context_chars):
    messages = copy.deepcopy(state["messages"])
    call_names, call_arguments, discovery_messages = {}, {}, []
    source_navigation, publications = [], {}
    # Reapply the compact view when resuming old journals created before
    # small cards existed. Calls and result messages keep their identities.
    for index, message in enumerate(messages):
        for call in message.get("tool_calls", []):
            call_names[call["id"]] = call["function"]["name"]
            arguments = call["function"].get("arguments")
            try:
                parsed = json.loads(arguments, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
                if not isinstance(parsed, dict):
                    raise ValueError("Tool arguments are not an object")
                call_arguments[call["id"]] = parsed
            except (TypeError, ValueError):
                # Some providers validate the JSON in historical tool calls.
                # Preserve the raw call in the durable journal, but repair the
                # provider-facing copy so a malformed call does not poison all
                # subsequent requests. Its matching failure result is retained.
                call["function"]["arguments"] = "{}"
                message["content"] = ((message.get("content") or "") +
                    "\nÖnceki araç çağrısının JSON argümanları eksik veya geçersizdi; çalıştırılmadı. "
                    "Ham çağrı günlükte korunuyor; aşağıdaki hata sonucuna göre tam JSON ile düzelt.").strip()
        if message.get("role") == "tool" and call_names.get(message.get("tool_call_id")) in {
                "inspect_source", "read_source_table", "research_web", "find_source_pages"}:
            try:
                result = json.loads(message["content"])
                message["content"] = canonical(_model_tool_result(call_names[message["tool_call_id"]], result))
                if (call_names[message["tool_call_id"]] in {"inspect_source", "find_source_pages", "read_source_table"}
                        and result.get("status") == "ok" and result.get("source_id")
                        and not result.get("errors") and not result.get("recovery")):
                    source_navigation.append((index, result, call_names[message["tool_call_id"]],
                                              call_arguments.get(message["tool_call_id"], {})))
            except (TypeError, ValueError):
                pass
        if message.get("role") == "tool" and call_names.get(message.get("tool_call_id")) in {"ingest_source_table", "publish_selected_table"}:
            try:
                result = json.loads(message["content"])
                source_id = result.get("source_id") or call_arguments.get(message["tool_call_id"], {}).get("source_id")
                if source_id and result.get("status") == "ok" and result.get("dataset_id") and result.get("publication_performed") is not False:
                    publications.setdefault(source_id, (index, {key: result[key] for key in ("dataset_id", "table_id", "artifact_ref") if key in result}))
            except (TypeError, ValueError):
                pass
        if message.get("role") == "tool" and call_names.get(message.get("tool_call_id")) == "discover":
            try:
                result = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            message["content"] = canonical(_model_tool_result("discover", result))
            discovery_messages.append((index, result))
    prompt = SYSTEM_PROMPT + (CHART_PROMPT if charts_enabled else "")
    context = _compact(context_factory(state))
    system = prompt + "\nGüncel güvenilir çalışma alanı bağlamı:\n" + canonical(context)
    # Keep the newest discovery cards detailed; older successful searches
    # need only their metric identity/title/readiness once context is tight.
    if len(system) + len(canonical(messages)) > max_context_chars * .75:
        for index, result in discovery_messages[:-2]:
            messages[index]["content"] = canonical(_model_tool_result("discover", result, terse=True))
    # New tool families add fixed instructions. If the complete current turn is
    # still too large, preserve identities of the newest candidates as well,
    # while leaving describe/analysis results and task obligations untouched.
    for index, result in discovery_messages[-2:]:
        if len(system) + len(canonical(messages)) <= max_context_chars:
            break
        messages[index]["content"] = canonical(_model_tool_result("discover", result, terse=True))
    # Only under actual pressure, retire successful navigation that preceded a
    # publication of this source. Keep active re-reads and every failed/recovery
    # result, source warning, publication contract, analysis and proof unchanged.
    for index, result, name, arguments in source_navigation:
        if len(system) + len(canonical(messages)) <= max_context_chars:
            break
        publication = publications.get(result["source_id"])
        if publication and index < publication[0]:
            receipt = canonical(_published_navigation_receipt(name, result, arguments, publication[1]))
            if len(receipt) < len(messages[index]["content"]):
                messages[index]["content"] = receipt
    # Keep complete user turns, never orphan a tool result from its call.
    while (len(system) + len(canonical(messages)) > max_context_chars or len(messages) > 180) and sum(m["role"] == "user" for m in messages) > 1:
        next_user = next(i for i, m in enumerate(messages[1:], 1) if m["role"] == "user")
        messages = messages[next_user:]
    # Last fallback: initial retrieval is only a navigation hint too. Leave all
    # requests that already fit unchanged; shrink these duplicate cards only
    # where the earlier compaction stages would otherwise reject the request.
    if len(system) + len(canonical(messages)) > max_context_chars and isinstance(context.get("initial_metric_candidates"), dict):
        context["initial_metric_candidates"] = _model_tool_result("discover", context["initial_metric_candidates"], terse=True)
        system = prompt + "\nGüncel güvenilir çalışma alanı bağlamı:\n" + canonical(context)
    if len(system) + len(canonical(messages)) > max_context_chars:
        raise PlanError("Current task exceeds the configured context budget; use a smaller scope.", code="CONTEXT_BUDGET_EXCEEDED")
    return [{"role": "system", "content": system}] + messages
