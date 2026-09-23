"""One-off generator for `tests/evals/scenario_regression_baseline.json` (Commit 13, Faz 3).

Not a pytest module (leading underscore, no `test_` functions) — this is the tool used to
*produce* the committed baseline snapshot from the 32 real scenario exports under
`/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/{main branch,ebrar branch}/`. It is
checked in for reviewability/reproducibility (so a future maintainer can see exactly how the
snapshot was derived and regenerate it after a deliberate, reviewed change), but
`test_scenario_regression_baseline.py` does not import or run it automatically — the
snapshot is regenerated manually, on purpose, and the diff is reviewed like any other data
change.

Usage (from repo root, after confirming a snapshot update is actually intended):
    python3 tests/evals/_generate_scenario_regression_baseline.py
"""

from __future__ import annotations

import json
from pathlib import Path

EXTERNAL_EXPORT_ROOT = Path("/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM")
OUTPUT_PATH = Path(__file__).resolve().parent / "scenario_regression_baseline.json"


def extract_runs(doc: dict) -> list[dict]:
    """Kept in lockstep with `_extract_runs()` in test_scenario_regression_baseline.py."""
    runs = doc.get("conversation_runs")
    if runs:
        out = []
        for index, run in enumerate(runs):
            result = run.get("result") or {}
            out.append(
                {
                    "prompt_index": index,
                    "run_id": run.get("run_id"),
                    "prompt": run.get("prompt"),
                    "status": run.get("status"),
                    "error_codes": sorted(
                        {e.get("code") for e in (result.get("errors") or []) if e.get("code")}
                    ),
                    "active_analysis_id": result.get("active_analysis_id"),
                    "chart_id": result.get("chart_id"),
                }
            )
        return out
    result = doc.get("result") or {}
    run = doc.get("run") or {}
    return [
        {
            "prompt_index": 0,
            "run_id": run.get("run_id"),
            "prompt": doc.get("prompt"),
            "status": result.get("status") or run.get("status"),
            "error_codes": sorted(
                {e.get("code") for e in (result.get("errors") or []) if e.get("code")}
            ),
            "active_analysis_id": result.get("active_analysis_id"),
            "chart_id": result.get("chart_id"),
        }
    ]


def main() -> None:
    if not EXTERNAL_EXPORT_ROOT.is_dir():
        raise SystemExit(f"External export directory not found: {EXTERNAL_EXPORT_ROOT}")

    files = sorted(EXTERNAL_EXPORT_ROOT.glob("main branch/*.json")) + sorted(
        EXTERNAL_EXPORT_ROOT.glob("ebrar branch/*.json")
    )
    baseline = {}
    for path in files:
        rel = str(path.relative_to(EXTERNAL_EXPORT_ROOT))
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
        baseline[rel] = {
            "session_name": doc.get("session_name"),
            "session_id": doc.get("session_id"),
            "runs": extract_runs(doc),
        }

    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {OUTPUT_PATH} ({len(baseline)} files)")


if __name__ == "__main__":
    main()
