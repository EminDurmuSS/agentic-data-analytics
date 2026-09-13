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
    if name == "web_search" and isinstance(result, dict):
        view = _compact(result)
        if "provider_attempts" in result:
            view["provider_attempts"] = _provider_attempts(result["provider_attempts"])
            _trim_provider_diagnostics(view)
        return view
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


def _provider_attempts(attempts):
    """Keep provider fallback decisions reviewable without large engine logs."""
    if not isinstance(attempts, list):
        return []
    cards = []
    for attempt in attempts[:4]:
        if not isinstance(attempt, dict):
            continue
        card = {key: value[:240] if isinstance(value, str) else copy.deepcopy(value)
                for key, value in attempt.items() if key in {
                    "provider", "status", "code", "elapsed_ms", "raw_count", "accepted_count", "rejected_count"}}
        diagnostics = attempt.get("diagnostics", [])
        if isinstance(diagnostics, dict):
            diagnostics = [{"engine": key, "detail": value} for key, value in list(diagnostics.items())[:12]]
        if isinstance(diagnostics, list):
            card["diagnostics"] = [{str(key)[:80]: str(value)[:240] for key, value in list(item.items())[:5]}
                if isinstance(item, dict) else str(item)[:240] for item in diagnostics[:12]]
            card["diagnostics_truncated"] = bool(attempt.get("diagnostics_truncated") or len(diagnostics) > 12)
        cards.append(card)
    return cards


def _trim_provider_diagnostics(view):
    attempts = [*view.get("provider_attempts", []),
                *(attempt for search in view.get("searches", []) for attempt in search.get("provider_attempts", []))]
    while len(canonical(view)) > 24000:
        attempt = next((attempt for attempt in reversed(attempts) if attempt.get("diagnostics")), None)
        if attempt is None:
            break
        attempt["diagnostics"].pop()
        attempt["diagnostics_truncated"] = True


def _document_result(name, result):
    """A bounded navigation view, with full evidence retained in source artifacts.

    A large PDF must be searched and selectively read; thirty pages and table
    contexts are not thirty independent additions to every model request.
    Small sources retain their useful preview and source-unit evidence.
    """
    fields = ("status", "code", "message", "errors", "source_id", "artifact_ref", "filename", "mime_type",
              "source_url", "raw_sha256", "size_bytes", "total_pages", "processed_pages", "selected_pages",
              "inspection_complete", "content_is_untrusted_data", "warnings", "query", "next_step",
              "publication_guidance", "recovery", "source_backend", "budget_exhausted",
              "table_id", "row_count", "sheet", "page", "read", "searched", "failures", "matches", "total_matches")
    view = {key: copy.deepcopy(result[key]) for key in fields if key in result}
    if "provider_attempts" in result:
        view["provider_attempts"] = _provider_attempts(result["provider_attempts"])
    if isinstance(result.get("searches"), list):
        view["searches"] = [{**{key: copy.deepcopy(search[key]) for key in
            ("query", "status", "code", "source_backend", "errors", "warnings", "budget_exhausted") if key in search},
            **({"provider_attempts": _provider_attempts(search["provider_attempts"])} if "provider_attempts" in search else {})}
            for search in result["searches"][:3] if isinstance(search, dict)]
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
        # Selected reads need the body of every requested page, not only the
        # repeating report header. Broad initial inspections stay navigation.
        focused = 1 <= len(result["pages"]) <= 3 or bool(result.get("selected_pages")) and 1 <= len(result["pages"]) <= 6
        page_budget = min(4000, 12000 // len(result["pages"])) if focused else 240
        view["pages"] = [{"page": page.get("page"), "text": str(page.get("text", ""))[:page_budget],
                          "text_truncated": bool(page.get("text_truncated") or len(str(page.get("text", ""))) > page_budget),
                          "extraction_method": page.get("extraction_method")} for page in result["pages"][:10] if isinstance(page, dict)]
        if focused and all(str(page.get("text") or "").strip() for page in result["pages"]):
            view.pop("text", None)  # Do not duplicate the first selected page.
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
        view["matches"] = []
        for match in result.get("matches", [])[:8]:
            excerpt = str(match.get("excerpt", ""))[:1200]
            card = {**match, "excerpt": excerpt,
                    "excerpt_truncated": bool(match.get("excerpt_truncated") or len(str(match.get("excerpt", ""))) > 1200)}
            if type(match.get("line_start")) is int:
                card["line_end"] = match["line_start"] + max(0, len(excerpt.splitlines()) - 1)
            view["matches"].append(card)
        for key in ("complete", "searched_pages", "next_start_page", "image_only_pages", "suggested_inspection", "page_number_basis"):
            if key in result:
                view[key] = copy.deepcopy(result[key])
    if isinstance(result.get("sources"), list):
        sources = []
        for source in result["sources"][:3]:
            card = {key: copy.deepcopy(source[key]) for key in
                    ("source_id", "title", "url", "domain", "date_published", "date_modified", "verification",
                     "document_links", "discovery_links", "source_role", "raw_sha256", "inspection_complete", "warnings", "content_is_untrusted_data",
                     "filename", "mime_type", "title_basis", "title_page", "total_pages", "cached_pages", "matched_pages",
                     "search_published_at", "search_publication_date_basis",
                     "suggested_inspection", "next_step", "page_number_basis", "passage_search_scope") if key in source}
            card["content"] = str(source.get("content", ""))[:1600]
            card["content_truncated"] = bool(source.get("content_truncated") or len(str(source.get("content", ""))) > 1600)
            if isinstance(source.get("passages"), list):
                card["passages"] = [{**{key: copy.deepcopy(passage[key]) for key in
                    ("page", "line_start", "line_end", "extraction_method") if key in passage},
                    "text": str(passage.get("text", ""))[:3000],
                    "content_truncated": bool(passage.get("content_truncated") or len(str(passage.get("text", ""))) > 3000)}
                    for passage in source["passages"][:3] if isinstance(passage, dict)]
                card["model_passages_truncated"] = bool(source.get("model_passages_truncated") or len(source["passages"]) > len(card["passages"]))
            card["tables"] = [table_card(table) for table in source.get("tables", [])[:2]]
            sources.append(card)
        view["sources"] = sources
    view["model_evidence_view"] = True
    view["full_evidence_retained"] = True
    view["navigation_hint"] = "For long documents use find_source_pages, then inspect_source with explicit page_numbers or read_source_table. Omitted or shortened previews are not the complete table; publication reads the stored candidate, not this preview."
    # Even unusually large warning/metadata fields remain inside a fixed model
    # budget. This projection never changes raw source bytes or cached rows.
    view = _compact(view)
    _trim_provider_diagnostics(view)
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
    while len(canonical(view)) > 24000:
        source = next((source for source in reversed(view.get("sources", [])) if len(source.get("passages", [])) > 1), None)
        if source is None:
            break
        source["passages"].pop()
        source["model_passages_truncated"] = True
    while len(canonical(view)) > 24000:
        source = next((source for source in reversed(view.get("sources", [])) if len(source.get("tables", [])) > 1), None)
        if source is None:
            break
        source["tables"].pop()
        source["model_tables_truncated"] = True
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
    from agentic_analytics.agent.source_context import registered_sources
    context.update(registered_sources(store, workspace_id, state))
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


def _historical_tool_receipt(name, result, arguments):
    """Keep prior-turn source addresses and outcomes, not a second PDF dump.

    Only the provider-facing copy changes. The current turn's evidence and every
    failed/recovery response stay intact; full historical evidence remains in
    the journal and can be retrieved by its original source or analysis ID.
    """
    if not isinstance(result, dict) or result.get("status") not in {"ok", "valid", "completed"} or result.get("errors") or result.get("recovery"):
        return None
    keys = ("status", "code", "message", "warnings", "source_id", "source_url", "filename",
            "artifact_ref", "artifact_id", "analysis_id", "dataset_id", "table_id", "chart_id",
            "summary_id", "raw_sha256", "total_pages", "processed_pages", "inspection_complete",
            "publication_performed", "import_status", "quality_status", "row_count", "unit",
            "scale", "currency", "kind", "temporal_semantics", "scope_caveats", "title")
    receipt = {key: copy.deepcopy(result[key]) for key in keys if key in result}
    if name == "discover":
        receipt.update(_model_tool_result(name, result, terse=True))
    if isinstance(result.get("metric"), dict):
        receipt["metric"] = {key: copy.deepcopy(value) for key, value in result["metric"].items()
            if key in {"metric_id", "title", "status", "unit", "scale", "currency", "kind",
                       "dimensions", "coverage_start", "coverage_end", "temporal_semantics", "scope_caveats"}}
    for field in ("tables", "available_series", "sources", "results"):
        if isinstance(result.get(field), list):
            receipt[field] = [{key: copy.deepcopy(item[key]) for key in
                ("source_id", "table_id", "metric_id", "title", "url", "page", "sheet", "status",
                 "unit", "scale", "currency", "kind", "dimensions", "row_count", "layout_review_required",
                 "quality_notes", "review", "warnings", "verification", "scope_caveats") if key in item}
                for item in result[field][:12] if isinstance(item, dict)]
            if len(result[field]) > 12:
                receipt[field + "_truncated"] = True
    # Store only read-only navigation. Never offer a prior mutation as a replay.
    if name in {"inspect_source", "read_source_table", "find_source_pages"} and result.get("source_id"):
        args = {key: copy.deepcopy(value) for key, value in arguments.items()
                if key in {"table_id", "page_numbers", "row_start", "limit", "query", "start_page", "max_pages", "table_strategy"}}
        args["source_id"] = result["source_id"]
        receipt["re_read"] = {"tool": name, "arguments": args}
    receipt.update(model_evidence_view="historical_tool_receipt", full_evidence_retained=True,
                   historical_only=True,
                   navigation_hint="Prior user turn: source addresses and outcomes only. This is not new evidence for the current question. Re-read the source or saved analysis before using omitted facts; use the current workspace plan/schema when extending the analysis.")
    return _compact(receipt)


def _archive_prior_searches(messages, current_turn, call_names):
    """Do not teach a new turn to replay old search-query loops.

    Searches are navigation, not read evidence. Keep their outcome and links
    once per previous turn, while source reads and the durable ledger retain
    their original identities. Current-turn calls are never changed here.
    """
    archived, receipt, receipt_position = [], None, 0
    search_ids = {call["id"] for message in messages[:current_turn] for call in message.get("tool_calls", [])
                  if call_names.get(call["id"]) == "web_search"}
    for index, message in enumerate(messages):
        if index >= current_turn:
            archived.append(message)
            continue
        if message.get("role") == "user":
            receipt = None
            receipt_position = len(archived) + 1
        if message.get("tool_calls"):
            retained = [call for call in message["tool_calls"] if call["id"] not in search_ids]
            if len(retained) != len(message["tool_calls"]):
                message = {**message, "tool_calls": retained}
                if not retained:
                    message.pop("tool_calls")
                    if not message.get("content"):
                        continue
        if message.get("role") == "tool" and message.get("tool_call_id") in search_ids:
            if receipt is None:
                receipt = {"historical_searches": 0, "historical_only": True, "full_evidence_retained": True,
                           "error_codes": [], "unverified_navigation": [],
                           "meaning": "Previous user turn's search attempts, not current evidence or pending instructions. Form a new query from the current request and active analysis; old queries are archived in the technical ledger."}
                archived.insert(receipt_position, {"role": "assistant", "content": receipt})
            receipt["historical_searches"] += 1
            try:
                result = json.loads(message["content"])
            except (TypeError, ValueError):
                receipt["unreadable_results"] = receipt.get("unreadable_results", 0) + 1
                continue
            if not isinstance(result, dict):
                continue
            receipt["error_codes"] = sorted(set(receipt["error_codes"]) | {error["code"] for error in result.get("errors", []) if error.get("code")})
            for item in result.get("results", []):
                if not isinstance(item, dict):
                    continue
                link = {key: item[key] for key in ("url", "title") if key in item}
                if link.get("url") and not any(old.get("url") == link["url"] for old in receipt["unverified_navigation"]):
                    receipt["unverified_navigation"].append(link)
            continue
        archived.append(message)
    return [{**message, "content": canonical(message["content"])} if isinstance(message.get("content"), dict) else message
            for message in archived]


def _compact_source_navigation(messages, current_turn, call_names, arguments, remaining_chars):
    """Under pressure, retain addresses of earlier reads, not repeated PDF dumps."""
    records = []
    for index, message in enumerate(messages[current_turn:], current_turn):
        name = call_names.get(message.get("tool_call_id"))
        if message.get("role") != "tool" or name not in {"find_source_pages", "inspect_source"}:
            continue
        try:
            result = json.loads(message["content"])
        except (TypeError, ValueError):
            continue
        if result.get("status") != "ok" or result.get("errors") or not result.get("source_id"):
            continue
        records.append((index, name, result, arguments.get(message["tool_call_id"], {})))
    latest_search, read_addresses = {}, {}
    for index, name, result, args in records:
        source = result["source_id"]
        if name == "find_source_pages":
            latest_search[source] = index
        elif args.get("page_numbers") and any(page.get("text") for page in result.get("pages", []) if isinstance(page, dict)):
            address = canonical({"pages": sorted(set(args["page_numbers"])), "table_strategy": args.get("table_strategy", "lines")})
            read_addresses.setdefault(source, {})[address] = index
    reads = {source: sorted(addresses.values()) for source, addresses in read_addresses.items()}
    candidates = [entry for entry in records if entry[1] == "find_source_pages" and entry[0] != latest_search[entry[2]["source_id"]]]
    candidates += [entry for entry in records if entry[1] == "inspect_source"
                   and entry[0] not in reads.get(entry[2]["source_id"], [])[-2:]
                   and any(index > entry[0] for index in reads.get(entry[2]["source_id"], []))]
    for index, name, result, args in candidates:
        if len(canonical(messages)) <= remaining_chars:
            break
        keys = ("status", "source_id", "source_url", "raw_sha256", "filename", "mime_type", "total_pages",
                "processed_pages", "selected_pages", "inspection_complete", "warnings", "query",
                "complete", "searched_pages", "next_start_page", "image_only_pages", "suggested_inspection", "page_number_basis")
        receipt = {key: copy.deepcopy(result[key]) for key in keys if key in result}
        receipt["tables"] = [{key: copy.deepcopy(table[key]) for key in
            ("table_id", "page", "sheet", "source_pages", "row_count",
             "unit_caption", "units", "unit_contexts", "layout_review_required", "missing_formula_cache", "quality_notes", "review")
            if key in table} for table in result.get("tables", []) if isinstance(table, dict)]
        receipt["matches"] = [{key: copy.deepcopy(match[key]) for key in
            ("page", "match_type", "all_query_terms")
            if key in match} for match in result.get("matches", []) if isinstance(match, dict)]
        receipt.update(model_evidence_view="source_navigation_receipt", full_evidence_retained=True,
                       source_content_omitted=True, re_read={"tool": name, "arguments": copy.deepcopy(args)},
                       navigation_hint="Earlier current-turn source content is retained in the full ledger. These are navigation addresses, not evidence of omitted values or absence. Re-read the exact page/table when its omitted body is needed; later focused source reads remain below.")
        reduced = canonical(_compact(receipt))
        if len(reduced) < len(messages[index]["content"]):
            messages[index]["content"] = reduced
    # Keep both recent page bodies; retire only duplicate table previews from
    # the focused inspections if navigation receipts are insufficient. Numeric
    # read_source_table responses are never included in these candidates.
    for index, name, result, args in records:
        source_reads = reads.get(result["source_id"], [])
        if len(canonical(messages)) <= remaining_chars:
            break
        if name != "inspect_source" or index not in source_reads[-2:]:
            continue
        if not any(table.get("preview") for table in result.get("tables", [])):
            continue
        view = copy.deepcopy(result)
        for table in view.get("tables", []):
            for key in ("preview", "context_text", "original_columns", "source_header_quotes"):
                table.pop(key, None)
            table["preview_truncated"] = True
        view.update(model_table_previews_omitted=True, full_evidence_retained=True,
                    re_read={"tool": name, "arguments": copy.deepcopy(args)})
        reduced = canonical(view)
        if len(reduced) < len(messages[index]["content"]):
            messages[index]["content"] = reduced


def model_messages(state, *, context_factory, charts_enabled, max_context_chars, system_prompt=None):
    messages = copy.deepcopy(state["messages"])
    for message in messages:
        message.pop("source_ids", None)  # Internal attachment metadata is supplied through the trusted context.
    current_turn = max((i for i, message in enumerate(messages) if message.get("role") == "user"), default=0)
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
    # Prior completed work is navigation, not the working set for a new user
    # question. Compact it before hitting the context limit, so the original
    # request (period, scope and normalization) is not discarded wholesale.
    for index, message in enumerate(messages[:current_turn]):
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(message["content"])
        except (TypeError, ValueError):
            continue
        call_id = message.get("tool_call_id")
        receipt = _historical_tool_receipt(call_names.get(call_id), result, call_arguments.get(call_id, {}))
        if receipt is not None and len(canonical(receipt)) < len(message["content"]):
            message["content"] = canonical(receipt)
    prompt = system_prompt if system_prompt is not None else SYSTEM_PROMPT + (CHART_PROMPT if charts_enabled else "")
    context = _compact(context_factory(state))
    system = prompt + "\nGüncel güvenilir çalışma alanı bağlamı:\n" + canonical(context)
    # Keep the newest discovery cards detailed; older successful searches
    # need only their metric identity/title/readiness once context is tight.
    if len(system) + len(canonical(messages)) > max_context_chars * .75:
        for index, result in discovery_messages[:-2]:
            if index < current_turn:
                continue
            messages[index]["content"] = canonical(_model_tool_result("discover", result, terse=True))
    # New tool families add fixed instructions. If the complete current turn is
    # still too large, preserve identities of the newest candidates as well,
    # while leaving describe/analysis results and task obligations untouched.
    for index, result in discovery_messages[-2:]:
        if len(system) + len(canonical(messages)) <= max_context_chars:
            break
        if index < current_turn:
            continue
        messages[index]["content"] = canonical(_model_tool_result("discover", result, terse=True))
    # Only under actual pressure, retire successful navigation that preceded a
    # publication of this source. Keep active re-reads and every failed/recovery
    # result, source warning, publication contract, analysis and proof unchanged.
    for index, result, name, arguments in source_navigation:
        if len(system) + len(canonical(messages)) <= max_context_chars:
            break
        if index < current_turn:
            continue
        publication = publications.get(result["source_id"])
        if publication and index < publication[0]:
            receipt = canonical(_published_navigation_receipt(name, result, arguments, publication[1]))
            if len(receipt) < len(messages[index]["content"]):
                messages[index]["content"] = receipt
    _compact_source_navigation(messages, current_turn, call_names, call_arguments, max_context_chars - len(system))
    messages = _archive_prior_searches(messages, current_turn, call_names)
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
    # Repeated successful retrievals can return exactly the same candidates.
    # Under pressure, reference the first retained card set instead of copying
    # it again. Compare all projected metadata so a changed scope or warning
    # never disappears just because the metric IDs happen to match.
    if len(system) + len(canonical(messages)) > max_context_chars:
        seen_candidates = {}
        for message in messages:
            call_id = message.get("tool_call_id")
            if message.get("role") != "tool" or call_names.get(call_id) != "discover":
                continue
            result = json.loads(message["content"])
            if result.get("status") != "ok" or result.get("errors") or not result.get("metrics"):
                continue
            cards = canonical({key: result.get(key) for key in ("metrics", "near_matches")})
            if cards in seen_candidates:
                result.update(metrics=[], repeated_candidates_from=seen_candidates[cards],
                              model_card_count=0, model_cards_truncated=True,
                              navigation_hint="Identical candidate cards are retained in the referenced tool result; describe a metric before using its semantics.")
                result.pop("near_matches", None)
                message["content"] = canonical(result)
            else:
                seen_candidates[cards] = call_id
    if len(system) + len(canonical(messages)) > max_context_chars:
        raise PlanError("Current task exceeds the configured context budget; use a smaller scope.", code="CONTEXT_BUDGET_EXCEEDED")
    return [{"role": "system", "content": system}] + messages
