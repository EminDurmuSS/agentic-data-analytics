"""Grade saved live-app evidence against independent, locally verified oracles.

This program makes no application, provider, database, or network calls. Numeric
checks use full exported analysis rows, never tool previews or model judgments.
Final prose requires an explicit reviewer decision before task_pass can be true.

Optional --answer-reviews JSON has this form::

    {"trial_id": {"1": {"status": "pass", "reason": "Reviewed claims ...",
                         "answer_sha256": "hash from this report"}}}

Reviews are bound to exact final-answer bytes. A review cannot override failed
artifact checks. Missing scheduled trials remain visible as not_run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, urlunsplit


WRITE_TOOLS = {"execute", "revise_analysis", "query_grouped"}
REL_TOL = 1e-10
ABS_TOL = 1e-7


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_hook=lambda item:
                      int(item["$integer"]) if set(item) == {"$integer"} else item)


def sha(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def close(actual, expected):
    return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
            and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL))


def check(checks, name, passed, detail=None):
    item = {"name": name, "passed": bool(passed)}
    if detail is not None:
        item["detail"] = detail
    checks.append(item)
    return bool(passed)


def normalize_text(value):
    return value.casefold().translate(str.maketrans({"ı": "i", "ş": "s", "ğ": "g",
                                                   "ü": "u", "ö": "o", "ç": "c"})).replace("i\u0307", "i")


def normalized_url(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        return urlunsplit(("https", host, unquote(parsed.path).rstrip("/"), parsed.query, ""))
    except ValueError:
        return ""


def official_url(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        return parsed.scheme in {"http", "https"} and (host == "tcmb.gov.tr" or host.endswith(".tcmb.gov.tr"))
    except ValueError:
        return False


def result_of(turn):
    return (turn.get("final_job") or {}).get("result") or {}


def tool_evidence(turn):
    """Prefer full event results; one response per call identity, not two copies."""
    result = result_of(turn)
    by_id = {}
    for item in result.get("tool_results", []):
        by_id[item.get("call_id") or sha(item)] = item
    for event in (turn.get("final_job") or {}).get("events", []):
        payload = event.get("payload") or {}
        if event.get("kind") in {"tool_result", "tool_reused", "tool_recovered"}:
            if isinstance(payload.get("result"), dict):
                by_id[payload.get("call_id") or sha(payload)] = payload
    return list(by_id.values())


def expressions(analysis):
    """Resolve operation dependencies before assigning an in-place output."""
    values = {}
    for column in (analysis.get("plan") or {}).get("columns", []):
        values[column["name"]] = {"op": "metric", "metric_id": column.get("metric_id")}
    for name, source in analysis.get("sources", {}).items():
        values.setdefault(name, {"op": "metric", "metric_id": source.get("metric_id")})
    for operation in (analysis.get("plan") or {}).get("operations", []):
        expr = {k: v for k, v in operation.items()
                if k not in {"column", "output", "index", "denominator"}}
        for field in ("column", "index", "denominator"):
            if field in operation:
                expr[field] = values.get(operation[field], {"op": "unresolved"})
        values[operation["output"]] = expr
    return values


def role_of(expr, metric_roles):
    op = expr.get("op")
    if op == "metric":
        return metric_roles.get(expr.get("metric_id"))
    role = role_of(expr.get("column", {}), metric_roles)
    if op == "scale":
        return role
    if op == "deflate" and role_of(expr.get("index", {}), metric_roles) == "cpi":
        return role + "_real" if role else None
    if op == "growth" and expr.get("periods", 1) == 12:
        return role + "_yoy" if role else None
    return None


def role_columns(analysis, metric_roles):
    roles = {}
    for name, expr in expressions(analysis).items():
        role = role_of(expr, metric_roles)
        if role and name in analysis.get("columns", []):
            roles.setdefault(role, []).append(name)
    return roles


def rows_by_period(analysis):
    return {row.get("period"): row for row in analysis.get("rows", [])}


def compare_role(checks, analysis, roles, role, periods, expected, *, scale=None):
    names = roles.get(role, [])
    check(checks, role + "_column_present", bool(names), names)
    rows = rows_by_period(analysis)
    candidates = []
    for name in names:
        observed = [rows.get(period, {}).get(name) for period in periods]
        mismatches = [{"period": p, "actual": a, "expected": e}
                      for p, a, e in zip(periods, observed, expected) if not close(a, e)]
        candidates.append((name, observed, mismatches))
    best = min(candidates, key=lambda value: len(value[2]), default=None)
    check(checks, role + "_values", bool(best is not None and not best[2]),
          {"column": best[0], "compared_cells": len(expected), "mismatches": best[2][:12]}
          if best else {"reason": "No column with the required source/operation role"})
    if best is None:
        return None
    name = best[0]
    schema = analysis.get("schema", {}).get(name, {})
    if scale is not None:
        check(checks, role + "_scale", close(schema.get("scale"), scale), schema.get("scale"))
    if role in {"profit", "kobi", "credit", "credit_real"}:
        check(checks, role + "_monetary_unit", schema.get("unit") == "TRY"
              and schema.get("currency") == "TRY", {k: schema.get(k) for k in ("unit", "currency")})
    if role == "rate" or role.endswith("_yoy"):
        check(checks, role + "_percentage_unit", schema.get("unit") == "percent", schema.get("unit"))
    return name


def base_checks(turn, checks):
    result, analysis = result_of(turn), turn.get("analysis") or {}
    check(checks, "full_analysis_exported", bool(analysis))
    check(checks, "analysis_matches_run", bool(analysis.get("analysis_id"))
          and analysis.get("analysis_id") == result.get("analysis_id"))
    check(checks, "current_turn_analysis_write", result.get("analysis_updated") is True
          and any(item.get("tool") in WRITE_TOOLS and item.get("result", {}).get("status") == "ok"
                  and item["result"].get("analysis_id") == analysis.get("analysis_id")
                  for item in tool_evidence(turn)))
    rows = analysis.get("rows", [])
    check(checks, "all_rows_exported", bool(rows) and len(rows) == analysis.get("row_count")
          and analysis.get("offset", 0) == 0)
    periods = [row.get("period") for row in rows]
    check(checks, "unique_ordered_periods", bool(periods) and all(isinstance(p, str) for p in periods)
          and len(periods) == len(set(periods)) and periods == sorted(periods))
    check(checks, "monthly_frequency", analysis.get("plan", {}).get("frequency") == "monthly")
    return analysis


def source_checks(analysis, metric_roles, bindings):
    checks = []
    columns = analysis.get("plan", {}).get("columns", [])
    for metric_id, role in metric_roles.items():
        selected = [col for col in columns if col.get("metric_id") == metric_id]
        check(checks, role + "_source_selected", bool(selected), metric_id)
        binding = next((b for b in bindings.values() if b.get("metric_id") == metric_id), {})
        for column in selected:
            name = column["name"]
            source = analysis.get("sources", {}).get(name, {})
            schema = analysis.get("schema", {}).get(name, {})
            check(checks, name + "_source_identity", source.get("metric_id") == metric_id)
            if binding:
                check(checks, name + "_source_unit", source.get("unit") == binding.get("unit"), source.get("unit"))
                check(checks, name + "_source_scale", close(source.get("scale"), binding.get("scale", 1)))
                check(checks, name + "_source_system", source.get("source_system") == binding.get("source_system"))
                if role not in {"profit", "kobi", "credit", "count"}:
                    check(checks, name + "_no_unrequested_dimensions", column.get("dimensions", {}) == {})
            if metric_id.startswith("bddk_monthly:"):
                check(checks, name + "_sector_dimension", column.get("dimensions") == {"group_code": 10001})
                check(checks, name + "_schema_sector", schema.get("scope", {}).get("dimensions") == {"group_code": 10001})
            alignment = "mean" if role == "rate" else "native"
            check(checks, name + "_alignment", column.get("alignment", "native") == alignment,
                  {"actual": column.get("alignment", "native"), "expected": alignment})
    return checks


def canonical_values(analysis, metric_roles):
    roles = role_columns(analysis, metric_roles)
    data = {}
    for role, names in roles.items():
        # Preserve distinct role variants without depending on chosen aliases.
        variants = []
        for name in names:
            schema = analysis.get("schema", {}).get(name, {})
            values = [[row.get("period"), format(row[name], ".10g")
                       if isinstance(row.get(name), (int, float)) and not isinstance(row.get(name), bool)
                       else row.get(name)] for row in analysis.get("rows", [])]
            variants.append({"values": values, "unit": schema.get("unit"),
                             "scale": schema.get("scale"), "price_basis": schema.get("price_basis")})
        data[role] = sorted({sha(value): value for value in variants}.values(), key=sha)
    return data


def numeric_grade(case, index, turn, previous, oracles):
    checks = []
    analysis = base_checks(turn, checks)
    bindings = oracles["metadata"]["metric_bindings"]
    if case == "monthly_profit":
        oracle = oracles["A_net_profit"]
        metrics = {oracle["metric_id"]: "profit"}
        roles = role_columns(analysis, metrics)
        periods = oracle["monthly_periods"]
        check(checks, "exact_requested_periods", [r.get("period") for r in analysis.get("rows", [])] == periods)
        compare_role(checks, analysis, roles, "profit", periods, oracle["monthly_values"], scale=1e6)
        check(checks, "no_extra_profit_transformation", not analysis.get("plan", {}).get("operations"))
    elif case == "confidence":
        oracle = oracles["C_rkge_native"]
        metrics = {oracle["metric_id"]: "confidence"}
        roles = role_columns(analysis, metrics)
        check(checks, "exact_requested_periods", [r.get("period") for r in analysis.get("rows", [])] == oracle["periods"])
        compare_role(checks, analysis, roles, "confidence", oracle["periods"], oracle["values"])
        check(checks, "no_transformations", not analysis.get("plan", {}).get("operations"))
        check(checks, "review_warning_preserved", any("review" in str(w.get("code", "")).casefold()
                                                     for w in analysis.get("warnings", [])), analysis.get("warnings", []))
    elif case == "kobi_growth":
        oracle = oracles["B_kobi_yoy"]
        metrics = {oracle["metric_id"]: "kobi", oracle["cpi_metric_id"]: "cpi"}
        roles = role_columns(analysis, metrics)
        compare_role(checks, analysis, roles, "kobi", oracle["periods"], oracle["stocks_million_try"], scale=1e6)
        compare_role(checks, analysis, roles, "cpi", oracle["periods"], oracle["cpi_index_values"], scale=1)
        compare_role(checks, analysis, roles, "kobi_yoy", ["2026-06"], [oracle["nominal_yoy_pct"]], scale=1)
        compare_role(checks, analysis, roles, "kobi_real_yoy", ["2026-06"], [oracle["real_yoy_pct"]], scale=1)
        for role in ("kobi_yoy", "kobi_real_yoy"):
            check(checks, role + "_unit_percent", any(analysis.get("schema", {}).get(n, {}).get("unit") == "percent"
                                                      for n in roles.get(role, [])))
    else:
        oracle = oracles["D_housing_revision"]
        wanted = {"credit", "rate"} | ({"cpi"} if index >= 2 else set()) | ({"hpi"} if index >= 3 else set())
        metrics = {metric: role for role, metric in oracle["metric_ids"].items() if role in wanted}
        roles = role_columns(analysis, metrics)
        periods = oracle["arrays"]["period"]
        check(checks, "exact_60_periods", [r.get("period") for r in analysis.get("rows", [])] == periods)
        role = "credit" if index == 1 else "credit_real"
        values = oracle["arrays"]["credit_nominal_million_tl" if index == 1 else "credit_real_million_tl_jan2021_prices"]
        credit_name = compare_role(checks, analysis, roles, role, periods, values, scale=1e6)
        compare_role(checks, analysis, roles, "rate", periods, oracle["arrays"]["rate_pct"], scale=1)
        for extra in wanted & {"cpi", "hpi"}:
            compare_role(checks, analysis, roles, extra, periods, oracle["arrays"][extra + "_index"], scale=1)
        if index >= 2:
            schema = analysis.get("schema", {}).get(credit_name, {})
            check(checks, "credit_price_basis", schema.get("price_basis") == "2021-01")
            parent = (previous.get("analysis") or {}) if previous else {}
            check(checks, "parent_link", bool(parent.get("analysis_id"))
                  and analysis.get("parent_analysis_id") == parent.get("analysis_id"))
            if index == 2:
                original_roles = role_columns(parent, {oracle["metric_ids"]["credit"]: "credit"})
                original_names = original_roles.get("credit", [])
                check(checks, "original_credit_column_replaced", credit_name in original_names and bool(credit_name),
                      {"original_columns": original_names, "real_column": credit_name})
                preserved = [n for n in parent.get("columns", []) if n not in original_names]
            else:
                preserved = parent.get("columns", [])
                hpi_names = roles.get("hpi", [])
                check(checks, "hpi_is_added_column", any(n not in parent.get("columns", []) for n in hpi_names))
            before, after = parent.get("rows", []), analysis.get("rows", [])
            unchanged = len(before) == len(after) and bool(before)
            differences = []
            for a, b in zip(before, after):
                for name in preserved:
                    if name not in b or a.get(name) != b.get(name):
                        differences.append({"period": a.get("period"), "column": name})
            check(checks, "previous_cells_exactly_preserved", unchanged and not differences,
                  {"columns": preserved, "differences": differences[:12]})
    sources = source_checks(analysis, metrics, bindings)
    return {"artifact_checks": checks, "source_checks": sources,
            "artifact_pass": all(c["passed"] for c in checks),
            "source_pass": all(c["passed"] for c in sources),
            "canonical_values": canonical_values(analysis, metrics)}


def refusal_grade(turn, oracles):
    checks, metrics = [], {oracles["E_invalid_count_deflation"]["metric_id"]: "count",
                           oracles["E_invalid_count_deflation"]["cpi_metric_id"]: "cpi"}
    evidence = tool_evidence(turn)
    analysis = turn.get("analysis") or {}
    successful_writes = [item for item in evidence if item.get("tool") in WRITE_TOOLS
                         and item.get("result", {}).get("status") == "ok"]
    plans = [analysis.get("plan", {})]
    for event in (turn.get("final_job") or {}).get("events", []):
        payload = event.get("payload", {})
        if event.get("kind") == "plan_validation" and payload.get("validation", {}).get("status") == "valid":
            plans.append(payload.get("plan", {}))
    bogus = [operation for plan in plans for operation in plan.get("operations", [])
             if operation.get("op") == "deflate"]
    check(checks, "no_published_count_deflation", not (successful_writes and bogus), bogus)
    roles = role_columns(analysis, metrics)
    check(checks, "no_deflated_count_column", not roles.get("count_real"), roles.get("count_real", []))
    text = normalize_text(result_of(turn).get("message") or "")
    cues = {"count": bool(re.search(r"musteri|say[ıi]|adet|count", text)),
            "inflation": bool(re.search(r"tufe|enflasyon|deflat|reel|cpi", text)),
            "monetary_distinction": bool(re.search(r"parasal|para birimi|nominal tutar|monetary|currency", text)),
            "refusal": bool(re.search(r"uygulanamaz|uygun degil|anlamli degil|dogru degil|indirgenemez|deflate edilemez|yapilamaz|donusturulemez|cannot|not applicable", text))}
    oracle = oracles["E_invalid_count_deflation"]
    count_values = dict(zip(oracle["periods"], oracle["counts"]))
    if roles.get("count"):
        compare_role(checks, analysis, roles, "count", ["2025-06", "2026-06"],
                     [count_values["2025-06"], count_values["2026-06"]], scale=1)
    # Cues are review aids, never a substitute for judging the actual refusal.
    return {"artifact_checks": checks, "source_checks": [],
            "artifact_pass": all(c["passed"] for c in checks), "source_pass": None,
            "refusal_text_cues": cues, "canonical_values": canonical_values(analysis, metrics)}


def web_grade(turn, oracle):
    checks, evidence = [], tool_evidence(turn)
    searches = [item["result"] for item in evidence if item.get("tool") == "web_search"]
    inspections = [item["result"] for item in evidence if item.get("tool") == "inspect_source"
                   and item.get("result", {}).get("status") == "ok"]
    check(checks, "web_search_executed", bool(searches))
    check(checks, "web_oracle_available", bool(oracle))
    def contains_rate(content, key):
        value = (oracle or {}).get(key)
        if not isinstance(value, (int, float)):
            return False
        pattern = re.escape(format(value, "g")).replace(r"\.", "[.,]")
        return bool(re.search(r"(?<!\d)" + pattern + r"(?!\d)", content))
    relevant = []
    for source in inspections:
        url = source.get("source_url", "")
        if not official_url(url):
            continue
        content = normalize_text(" ".join([source.get("text", "")]
                                         + [page.get("text", "") for page in source.get("pages", [])]))
        has_date = bool(re.search(r"(?:0?6\s+mart\s+2025|march\s+0?6,?\s+2025|0?6[./-]0?3[./-]2025|2025-03-06)", content))
        has_instrument = bool(re.search(r"bir hafta|one.week|repo", content))
        has_rates = (contains_rate(content, "previous_rate_percent")
                     and contains_rate(content, "new_rate_percent"))
        if has_date and has_instrument and has_rates and source.get("raw_sha256"):
            relevant.append({"url": url, "source_id": source.get("source_id"),
                             "raw_sha256": source["raw_sha256"]})
    check(checks, "official_decision_opened_with_relevant_content", bool(relevant), relevant)
    answer = result_of(turn).get("message") or ""
    links = re.findall(r"https?://[^\s<>\]\)]+", answer)
    cited = {normalized_url(url) for url in links if official_url(url)}
    check(checks, "answer_cites_inspected_decision", any(normalized_url(item["url"]) in cited for item in relevant), sorted(cited))
    verified_citations = sorted(normalized_url(item["url"]) for item in relevant
                                if normalized_url(item["url"]) in cited)
    return {"artifact_checks": [], "source_checks": checks, "artifact_pass": None,
            "source_pass": all(c["passed"] for c in checks),
            "canonical_values": {"verified_cited_sources": verified_citations} if verified_citations else {}}


REVIEW_REQUIREMENTS = {
    "monthly_profit": ["Monthly numbers match periods and million TRY units", "Q1 total is 288688 million TRY", "Explains source cumulative values versus derived monthly flow; no contradictory claims"],
    "confidence": ["Shows 100.6, 103.3, 103.5 in the correct months without seasonal adjustment", "June source record is identified", "Review-required semantics are visible; no invented units or transformations"],
    "kobi_growth": ["Both June stocks and CPI values are present and correctly labeled", "Nominal 49.1023990705% and real 12.8631345354% growth are correctly stated at sensible rounding", "Uses ratio adjustment, not subtracting inflation; no unsupported factual claims"],
    "housing_followup": ["Explains the requested stage and units without contradicting the saved result", "Source selection and monthly mean are correctly described when relevant", "Replacement or preservation claims agree with actual columns and cells"],
    "invalid_count_deflation": ["Explicitly refuses CPI deflation because customer counts are not monetary amounts", "Does not present fabricated real-count numbers or claim that the requested invalid transformation succeeded", "Any shown nominal counts match the oracle; any unique-person claim acknowledges cross-bank duplication"],
    "web_research": ["States 6 March 2025 and the one-week repo auction rate reduction from 45% to 42.5%", "Official citation supports these claims in inspected content", "No contradictory rate, date, instrument or source claims"],
}


def grade_turn(case, index, turn, previous, oracles, web_oracle, review):
    result = result_of(turn)
    if case == "invalid_count_deflation":
        grade = refusal_grade(turn, oracles)
    elif case == "web_research":
        grade = web_grade(turn, web_oracle)
    else:
        grade = numeric_grade(case, index, turn, previous, oracles)
    answer = result.get("message") or ""
    digest = sha(answer)
    answer_status = "pending" if answer.strip() else "fail"
    reason = "Substantive final prose requires explicit review against independent evidence" if answer.strip() else "No final answer was exported"
    if review:
        if (review.get("answer_sha256") == digest and review.get("status") in {"pass", "fail"}
                and str(review.get("reason", "")).strip()):
            answer_status, reason = review["status"], review["reason"]
        else:
            reason = "Review is invalid or does not match the exact final answer"
    runtime_completed = result.get("status") == "completed"
    evidence_pass = all(value is not False for value in (grade["artifact_pass"], grade["source_pass"]))
    task_pass = False if not runtime_completed or not evidence_pass or answer_status == "fail" else (True if answer_status == "pass" else None)
    events = (turn.get("final_job") or {}).get("events", [])
    explains = [item["result"] for item in tool_evidence(turn) if item.get("tool") == "explain_value"]
    grade.update(index=index, runtime_status=result.get("status"), transport_status=turn.get("final_job", {}).get("status"),
                 run_id=result.get("run_id"), request_id=result.get("request_id"), conversation_id=result.get("conversation_id"),
                 analysis_id=result.get("analysis_id"), analysis_updated=result.get("analysis_updated"),
                 elapsed_seconds=turn.get("elapsed_seconds"), decisions=result.get("decisions"), repairs=result.get("repairs"),
                 error_codes=[item.get("code") for item in result.get("errors", [])],
                 warning_codes=[item.get("code") for item in result.get("warnings", [])],
                 runtime_completed=runtime_completed, deterministic_evidence_pass=evidence_pass, task_pass=task_pass,
                 answer_review={"status": answer_status, "reason": reason, "answer_sha256": digest,
                                "reviewer": review.get("reviewer", "explicit reviewer") if review else None,
                                "method": review.get("method") if review else None,
                                "requirements": REVIEW_REQUIREMENTS[case]}, final_answer=answer,
                 answer_present=bool(answer.strip()),
                 answer_lexical_diagnostics={"replacement_characters": answer.count("\ufffd"),
                                             "alphabetic_characters": sum(c.isalpha() for c in answer),
                                             "note": "Diagnostics do not establish semantic adequacy."},
                 explanation_evidence={"count": len(explains), "source_references_complete": [x.get("source_references_complete") for x in explains],
                                       "source_files_verified": [x.get("source_files_verified") for x in explains],
                                       "limit": "App explanations preserve source references; independent raw verification belongs to the oracle."},
                 tool_counts={"distinct_call_ids": len(tool_evidence(turn)),
                              "executed_starts": sum(e.get("kind") == "tool_started" for e in events),
                              "reused": sum(e.get("kind") == "tool_reused" for e in events),
                              "unique_artifact_ids": len({a["id"] for a in result.get("artifacts", []) if a.get("id")})})
    grade["value_fingerprint"] = sha(grade["canonical_values"]) if grade["canonical_values"] else None
    return grade


def aggregate(values):
    if any(value is False for value in values):
        return False
    return True if values and all(value is True for value in values) else None


def grade_suite(input_dir, oracle_path, *, web_oracle_path=None, reviews_path=None):
    manifest = load_json(input_dir / "manifest.json")
    oracles = load_json(oracle_path)
    web_oracle_path = web_oracle_path or oracle_path.with_name("web-oracle.json")
    web_oracle = load_json(web_oracle_path) if web_oracle_path.exists() else None
    reviews = load_json(reviews_path) if reviews_path else {}
    trials = []
    for repeat in range(1, manifest["repeats"] + 1):
        for case, prompts in manifest["cases"].items():
            trial_id = f"{case}_{repeat}"
            directory = input_dir / trial_id
            path = directory / "trial.json"
            saved = load_json(path) if path.exists() else {}
            turns = []
            previous = None
            for index in range(1, len(prompts) + 1):
                path = directory / f"turn-{index}.json"
                if not path.exists():
                    turns.append({"index": index, "collection_status": "not_run" if not saved else "not_exported",
                                  "artifact_pass": None, "source_pass": None, "task_pass": None,
                                  "answer_review": {"status": "not_run"}})
                    previous = None
                    continue
                turn = load_json(path)
                grade = grade_turn(case, index, turn, previous, oracles, web_oracle,
                                   reviews.get(trial_id, {}).get(str(index)))
                grade["prompt_matches_manifest"] = turn.get("prompt") == prompts[index - 1]
                grade["collection_status"] = "exported"
                grade["evidence_path"] = str(path.resolve())
                turns.append(grade)
                previous = turn
            exported = [t for t in turns if t["collection_status"] == "exported"]
            complete = len(exported) == len(prompts)
            task_pass = aggregate([t["task_pass"] for t in turns])
            if saved.get("collector_error"):
                task_pass = False
            trials.append({"trial_id": trial_id, "case_id": case, "repeat": repeat,
                           "workspace_id": saved.get("workspace", {}).get("workspace_id"),
                           "collection_status": "complete" if complete else ("incomplete" if saved else "not_run"),
                           "collector_error": saved.get("collector_error"), "turns": turns,
                           "artifact_pass": aggregate([t["artifact_pass"] for t in turns]),
                           "source_pass": aggregate([t["source_pass"] for t in turns]),
                           "task_pass": task_pass,
                           "value_fingerprint": sha([t.get("value_fingerprint") for t in turns])
                           if complete and all(t.get("value_fingerprint") for t in turns) else None})
    integrity = []
    check(integrity, "oracle_snapshot_matches_suite", manifest.get("database_sha256") == oracles["metadata"].get("database_sha256_before"))
    workspaces = [t["workspace_id"] for t in trials if t["workspace_id"]]
    check(integrity, "independent_workspaces", len(workspaces) == len(set(workspaces)))
    requests = [x.get("request_id") for t in trials for x in t["turns"] if x.get("request_id")]
    runs = [x.get("run_id") for t in trials for x in t["turns"] if x.get("run_id")]
    check(integrity, "distinct_request_ids", len(requests) == len(set(requests)))
    check(integrity, "distinct_run_ids", len(runs) == len(set(runs)))
    check(integrity, "prompts_match_manifest", all(t.get("prompt_matches_manifest", True)
                                                  for trial in trials for t in trial["turns"]))
    for field in ("backend_unchanged", "database_unchanged"):
        if field in manifest:
            check(integrity, field, manifest[field] is True)
    by_case = {}
    for case in manifest["cases"]:
        group = [t for t in trials if t["case_id"] == case]
        hashes = [t["value_fingerprint"] for t in group if t["value_fingerprint"]]
        by_case[case] = {"scheduled": len(group), "exported_trials": sum(t["collection_status"] == "complete" for t in group),
                         "artifact_passes": sum(t["artifact_pass"] is True for t in group),
                         "task_passes": sum(t["task_pass"] is True for t in group),
                         "task_failures": sum(t["task_pass"] is False for t in group),
                         "task_pending": sum(t["task_pass"] is None for t in group),
                         "distinct_value_fingerprints": len(set(hashes)), "fingerprinted_trials": len(hashes),
                         "all_repeats_same_values": len(hashes) == len(group) and len(set(hashes)) == 1,
                         "consistency_status": "no_usable_results" if not hashes else (
                             "incomplete_results" if len(hashes) < len(group) else "compared"),
                         "consistency_reason": "No exported role values or verified cited web sources were available."
                         if not hashes else ("Some scheduled trials have no usable value fingerprint."
                                             if len(hashes) < len(group) else None),
                         "all_repeats_task_pass": all(t["task_pass"] is True for t in group)}
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "suite_id": manifest["suite_id"],
            "method": "Deterministic comparison against independent raw-source-backed oracles; no model grader.",
            "input": str(input_dir.resolve()), "oracles_sha256": sha(oracle_path.read_text()),
            "integrity_checks": integrity, "integrity_pass": all(c["passed"] for c in integrity),
            "tolerance": {"relative": REL_TOL, "absolute": ABS_TOL, "preserved_cells": "exact equality", "fingerprint_precision": "10 significant digits"},
            "limitations": ["A matching value fingerprint measures repeatability, not correctness.",
                            "Final prose requires an explicit hash-bound reviewer decision against independent evidence; regex cues cannot pass it. The program does not identify that reviewer as the user or as a human.",
                            "Numeric source checks validate exported identities, units, scope and alignment; they do not manufacture absent citations.",
                            "Historical artifact immutability cannot be rechecked from one export per turn; preservation is checked on those saved exports.",
                            "KOBI growth columns must have identifiable typed growth/deflation lineage; alternative expressions require separate documented review.",
                            "Missing turn exports are unknown, not successful results. Running the grader never retries a provider call."],
            "summary": {"scheduled_trials": len(trials), "task_passes": sum(t["task_pass"] is True for t in trials),
                        "task_failures": sum(t["task_pass"] is False for t in trials),
                        "task_pending": sum(t["task_pass"] is None for t in trials), "by_case": by_case},
            "trials": trials}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--oracles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--web-oracle", type=Path)
    parser.add_argument("--answer-reviews", type=Path)
    args = parser.parse_args()
    report = grade_suite(args.input, args.oracles, web_oracle_path=args.web_oracle, reviews_path=args.answer_reviews)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(args.output)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
