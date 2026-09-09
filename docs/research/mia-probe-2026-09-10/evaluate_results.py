"""Validate the saved observations without calling any model or modifying data."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics

from bs4 import BeautifulSoup
import jsonschema
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent
rows = json.loads((ROOT / "results.json").read_text())["experiments"]
records = {row["label"]: row for row in rows}
assert len(records) == len(rows), "Duplicate experiment labels"


def row(prefix):
    return next(value for key, value in records.items() if key.startswith(prefix + "_"))


def message(prefix):
    return row(prefix)["response"]["choices"][0]["message"]


def parsed(prefix):
    return json.loads(message(prefix)["content"])


observations = []


def observe(name, value, evidence):
    observations.append({"name": name, "observed": bool(value), "evidence": evidence})


observe("all_requests_http_200", all(r.get("http_status") == 200 for r in rows), list(records))
observe("turkish_text_exact", message("P01")["content"].strip() == "İstanbul, işsizlik, yüzde puan.", ["P01"])
for prefix in ["P02", "P03"]:
    calls = message(prefix).get("tool_calls", [])
    correct = len(calls) == 1 and bool(calls[0].get("id")) and calls[0]["function"]["name"] == "lookup_fixture" and json.loads(calls[0]["function"]["arguments"]) == {"key": "I34"}
    observe(prefix + "_native_call_valid", correct, [prefix])
observe("named_call_stop_finish_reason", bool(message("P03")["tool_calls"]) and row("P03")["response"]["choices"][0]["finish_reason"] == "stop", ["P03"])
for prefix, expected in [("P04", {"value": 17.375, "unit": "TRY_million", "nonce": "a9c74e-probe-713"}), ("P05", {"value": 64.125, "unit": "TRY_million", "nonce": "b47f1d-probe-902"})]:
    observe(prefix + "_tool_result_exact", parsed(prefix) == expected and not message(prefix).get("tool_calls"), [prefix])
observe("tool_not_called_when_unnecessary", message("P06")["content"].strip() == "MERHABA" and not message("P06").get("tool_calls"), ["P06"])

schema_cases = ["P07", "P09", "P17", "P20"]
for prefix in schema_cases:
    schema = row(prefix)["request"]["response_format"]["json_schema"]["schema"]
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(parsed(prefix)))
    observe(prefix + "_schema_valid", not errors, [prefix])
expected_plan = {"plan": {"metric_id": "S", "filters": {"city": "İSTANBUL"}, "unit": "TRY_million"}, "clarification": None}
observe("nested_plan_semantics_exact", parsed("P07") == expected_plan and parsed("P08") == expected_plan, ["P07", "P08"])
observe("schema_holds_in_conflicting_prompt", parsed("P09") == {"decision": "schema_ok"}, ["P09"])

embedding = row("P10")
vectors = embedding["response"]["data"]
observe("embedding_batch_indices", [v["index"] for v in vectors] == [0, 1, 2, 3], ["P10"])
observe("embedding_4096_finite_near_unit_norm", all(v["embedding_summary"]["dimension"] == 4096 and v["embedding_summary"]["finite"] and abs(v["embedding_summary"]["l2_norm"] - 1) < 1e-6 for v in vectors), ["P10"])
similarities = embedding["cosine_matrix"][0][1:]
observe("embedding_related_card_ranked_first", similarities[0] > max(similarities[1:]), ["P10"])

expected_table = {"birim": "milyon TL", "kayitlar": [{"donem": "2026-01", "deger": 100}, {"donem": "2026-02", "deger": 120}, {"donem": "2026-03", "deger": 90}]}
free_content = message("P12")["content"].strip()
observe("unconstrained_vision_added_code_fence", free_content.startswith("```json"), ["P12"])
free_json = re.sub(r"^```json\s*|\s*```$", "", free_content)
observe("vision_table_values_exact", json.loads(free_json) == expected_table and parsed("P17") == expected_table, ["P12", "P17"])
observe("flow_and_deflator_selected_correctly", parsed("P13") == {"aggregation": "sum", "quarter_total": 60, "deflator": "CPI"}, ["P13"])
missing = parsed("P14")
observe("untyped_missing_data_safely_blocked", missing["status"] == "blocked" and missing["metric_id"] == "M" and missing["value"] is None and missing["source_files_verified"] is False, ["P14"])
observe("untyped_error_code_differs_from_service", missing["code"] != "METADATA_ONLY", ["P14"])
observe("typed_missing_data_contract_exact", parsed("P20") == {"status": "blocked", "metric_id": "M", "value": None, "code": "METADATA_ONLY", "source_files_verified": False}, ["P20"])

for prefix in ["P11", "P16"]:
    content = message(prefix)["content"]
    observe(prefix + "_landscape_ocr_failed", not any(period in content for period in ["2026-01", "2026-02", "2026-03"]) and row(prefix)["response"]["choices"][0]["finish_reason"] == "length", [prefix])
observe("plain_document_ocr_read_identifiers", all(s in message("P15")["content"] for s in ["TEST DOCUMENT", "12345", "100 USD"]), ["P15"])
expected_cells = [["Donem", "Aylik net kar"], ["2026-01", "100"], ["2026-02", "120"], ["2026-03", "90"]]
for prefix in ["P18", "P19"]:
    content = message(prefix)["content"]
    table = BeautifulSoup(content, "html.parser").find("table")
    cells = [[c.get_text(strip=True) for c in r.find_all(["td", "th"])] for r in table.find_all("tr")] if table else []
    observe(prefix + "_square_ocr_table_exact", cells == expected_cells and "milyon TL" in content, [prefix])

a = Image.open(ROOT / "synthetic-table.png")
b = Image.open(ROOT / "synthetic-table-square.png")
observe("original_table_pixels_preserved_in_square", ImageChops.difference(a, b.crop((0, 0, *a.size))).getbbox() is None, ["synthetic-table.png", "synthetic-table-square.png"])
p16 = row("P16")["request"]
p19 = row("P19")["request"]
without_image = lambda payload: {**payload, "messages": [{"role": "user", "content": [payload["messages"][0]["content"][1]]}]}
observe("square_comparison_only_image_changed", without_image(p16) == without_image(p19), ["P16", "P19"])

groups = defaultdict(list)
usage = defaultdict(lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
for r in rows:
    model = r["request"]["model"]
    groups[model].append(r["elapsed_seconds"])
    for field in usage[model]:
        usage[model][field] += r["response"].get("usage", {}).get(field, 0)
timings = {model: {"requests": len(values), "min_seconds": min(values), "median_seconds": round(statistics.median(values), 3), "max_seconds": max(values)} for model, values in groups.items()}
profile = {
    "date_local": "2026-09-10", "timezone": "Europe/Istanbul", "request_count": len(rows),
    "scope": "Small synthetic live capability probes, not production task accuracy or load benchmark",
    "observations": observations, "observed_request_timings": timings, "reported_usage_sum": dict(usage),
    "unknown": ["max_context_length", "rate_limits", "streaming", "parallel_tool_calls", "general_real_document_accuracy", "end_to_end_lakehouse_agent_accuracy"],
    "fixtures": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(ROOT.glob("*.png"))],
}
(ROOT / "capability-profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"requests": len(rows), "observations": len(observations), "asserted_observations_confirmed": sum(o["observed"] for o in observations), "timings": timings, "usage": dict(usage)}, ensure_ascii=False))
assert all(o["observed"] for o in observations), "An expected observation was not reproduced from stored evidence"
