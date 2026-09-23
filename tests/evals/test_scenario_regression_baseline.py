"""Regression harness over the 32 manually-labeled scenario exports (Faz 3, Commit 13).

Scope / what this test IS and IS NOT:

- This is a **baseline regression snapshot**, not a live-agent test. It does not invoke
  the agent, the lakehouse, or any network/tool call. It loads the already-exported
  conversation JSON files from a prior manual eval run
  (`/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/{main branch,ebrar branch}/`,
  32 files total, produced 2026-09-22/23 and analyzed by hand in
  `docs/eval-set/ASIL SORUN.md` / `kkb_hackathon_25_demo_senaryolari.md`) and re-derives,
  with plain parsing code, the same per-prompt `status` (`completed`/`partial`/`blocked`/
  `needs_input`) and `result.errors[].code` list that a human read out of those exports.
- The recorded ground truth lives in the sidecar snapshot
  `tests/evals/scenario_regression_baseline.json` (committed, reviewable, produced once by
  `tests/evals/_generate_scenario_regression_baseline.py` run against the real export files
  and then eyeballed — not recomputed on every test run).
- What this guards against: a change to the *parsing/extraction logic* in this module (or a
  future helper it comes to share with real code) silently drifting from the known-correct
  reading of these exports. It does **not** verify the agent still behaves this way today —
  for that, rerun the actual scenarios (see Commit 14, "full rerun of 25+10 set").
- The 32 source JSON files are **not** committed to git (they stay under the external
  `/home/neo/Downloads/...` path). Any environment without that path present (e.g. CI, a
  different developer's machine) skips this whole module with a clear reason instead of
  hard-failing, matching the `pytest.skip(...)` pattern used elsewhere in this repo for
  optional external fixtures (see `tests/lakehouse/test_bddk_alias_coverage.py`'s coverage
  section and `tests/evals/test_mentor_grading.py::test_fixed_monthly_oracle_matches_...`,
  which skip when their external data directories are absent).

JSON export schema (verified against the real files in this session):
- Multi-prompt files have `conversation_runs`: a list of
  `{run_id, prompt, status, result: {status, errors: [{code, message}], active_analysis_id,
  chart_id, message}}`, one entry per prompt in conversation order.
- Single-prompt files have no `conversation_runs` (or an empty one); the single run's data is
  at the top level directly: `prompt`, `run.run_id`, `result.{status, errors, ...}`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXTERNAL_EXPORT_ROOT = Path("/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM")
BASELINE_PATH = Path(__file__).resolve().parent / "scenario_regression_baseline.json"

pytestmark = pytest.mark.skipif(
    not EXTERNAL_EXPORT_ROOT.is_dir(),
    reason=(
        "External scenario export directory "
        f"'{EXTERNAL_EXPORT_ROOT}' is not present on this machine/CI environment; "
        "this regression harness only runs where the original 32 eval exports are available."
    ),
)


def _extract_runs(doc: dict) -> list[dict]:
    """Re-derive per-prompt (status, error codes, ...) from one exported conversation JSON.

    Mirrors exactly the logic used to produce `scenario_regression_baseline.json` in
    `tests/evals/_generate_scenario_regression_baseline.py`; kept in sync by
    `test_baseline_snapshot_matches_regenerated_extraction` below.
    """
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
    # Single-prompt export: the one run's data sits at the top level instead of being
    # wrapped in `conversation_runs`.
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


def _load_baseline() -> dict:
    with BASELINE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _discover_export_files() -> list[Path]:
    files = sorted(EXTERNAL_EXPORT_ROOT.glob("main branch/*.json")) + sorted(
        EXTERNAL_EXPORT_ROOT.glob("ebrar branch/*.json")
    )
    return files


def _relative_keys() -> list[str]:
    if not EXTERNAL_EXPORT_ROOT.is_dir():
        return []
    return sorted(
        str(path.relative_to(EXTERNAL_EXPORT_ROOT)) for path in _discover_export_files()
    )


# --- Representative subset used for the detailed, per-code assertions below --------------
#
# Chosen to cover: a fully-completed run, a METADATA_ONLY block, an
# EXTERNAL_NUMERIC_CLAIM_UNVERIFIED block, a NO_PROGRESS case, and a partial run. All four
# were confirmed present in the full 32-file corpus (see module docstring / commit message
# for the full status+code inventory).
REPRESENTATIVE_CASES = {
    # Prompt 0 is `partial` (TASK_DELIVERABLE_MISSING); prompts 1-2 are clean `completed`
    # runs with no errors — this is the "fully completed" happy-path shape in this corpus.
    "fully_completed": (
        "main branch/(3 başarılı) main branch senaryo 3__conversation_e670f0ea67a24730b46c3b89fbffc94d__3-prompt Bu tabloyu hiç bozmadan yeni bir sütun olarak konut fiy__kkb-finans-verileri.json",
        {"completed", "partial"},
        set(),
    ),
    "metadata_only_blocked": (
        "main branch/(1 başarılı 1 başarısız) senaryo 1 kkb gerçek demo__conversation_a1bee1742a0c4242926d008755285d6d__2-prompt Taşıt kredisi tutarlarına faiz oranlarını ekle. Veri se__kkb-finans-verileri.json",
        {"completed", "blocked"},
        {"METADATA_ONLY"},
    ),
    "external_numeric_claim_unverified_blocked": (
        "main branch/(başarısız) pdf import soru 3__conversation_7d04cb6fda3b4176b9da698ab8ceec31__Yüklediğim Excel dosyasından XU100, XBANK ve XUMAL endekslerinin__kendi-veriniz__s003-bist-baslangic.xlsx.json",
        {"blocked"},
        {"EXTERNAL_NUMERIC_CLAIM_UNVERIFIED", "TABLE_NOT_CREATED"},
    ),
    "partial_run": (
        "ebrar branch/(2 başarılı 1 başarısız)ebrar branch senaryo 2__conversation_f70bc17bb3444aadbf6cacebc4df850a__3-prompt Faiz oranlarındaki değişimler ile TL ve döviz mevduat h__kkb-finans-verileri.json",
        {"partial", "completed", "blocked"},
        {"DIMENSION_NOT_FOUND"},
    ),
    "no_progress_blocked": (
        "ebrar branch/senaryo5_1.başarılı_2ve3başarısız.json",
        {"completed", "blocked"},
        {"NO_PROGRESS"},
    ),
}


def _resolve_export_path(rel_name: str) -> Path:
    matches = [p for p in _discover_export_files() if str(p.relative_to(EXTERNAL_EXPORT_ROOT)) == rel_name]
    assert matches, f"Expected export file not found: {rel_name}"
    return matches[0]


@pytest.mark.parametrize(
    "case_name",
    sorted(REPRESENTATIVE_CASES),
)
def test_representative_case_status_and_codes_match_baseline(case_name):
    rel_name, expected_statuses, expected_codes = REPRESENTATIVE_CASES[case_name]
    baseline = _load_baseline()
    assert rel_name in baseline, f"Baseline snapshot missing entry for {rel_name!r}"

    path = _resolve_export_path(rel_name)
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    live_runs = _extract_runs(doc)

    actual_statuses = {r["status"] for r in live_runs}
    actual_codes = {code for r in live_runs for code in r["error_codes"]}

    assert actual_statuses == expected_statuses, (
        f"{case_name}: expected statuses {expected_statuses}, parsed {actual_statuses}"
    )
    assert expected_codes <= actual_codes, (
        f"{case_name}: expected error codes {expected_codes} to be a subset of parsed {actual_codes}"
    )

    # Cross-check the same numbers against the committed baseline snapshot.
    baseline_runs = baseline[rel_name]["runs"]
    assert len(baseline_runs) == len(live_runs)
    for expected_run, live_run in zip(baseline_runs, live_runs):
        assert expected_run["status"] == live_run["status"]
        assert expected_run["error_codes"] == live_run["error_codes"]
        assert expected_run["active_analysis_id"] == live_run["active_analysis_id"]
        assert expected_run["chart_id"] == live_run["chart_id"]


@pytest.mark.parametrize("rel_name", _relative_keys())
def test_every_export_file_matches_its_baseline_snapshot(rel_name):
    """Full 32-file sweep: every prompt's status + error codes must match the recorded baseline."""
    baseline = _load_baseline()
    assert rel_name in baseline, (
        f"{rel_name!r} is present on disk but missing from scenario_regression_baseline.json; "
        "regenerate the snapshot with tests/evals/_generate_scenario_regression_baseline.py"
    )

    path = _resolve_export_path(rel_name)
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    live_runs = _extract_runs(doc)
    baseline_runs = baseline[rel_name]["runs"]

    assert len(live_runs) == len(baseline_runs), (
        f"{rel_name}: run count changed (baseline {len(baseline_runs)}, now {len(live_runs)})"
    )
    for expected_run, live_run in zip(baseline_runs, live_runs):
        assert expected_run["prompt_index"] == live_run["prompt_index"]
        assert expected_run["status"] == live_run["status"], (
            f"{rel_name} prompt {live_run['prompt_index']}: status drifted from baseline "
            f"({expected_run['status']!r} -> {live_run['status']!r})"
        )
        assert expected_run["error_codes"] == live_run["error_codes"], (
            f"{rel_name} prompt {live_run['prompt_index']}: error codes drifted from baseline "
            f"({expected_run['error_codes']!r} -> {live_run['error_codes']!r})"
        )
        assert expected_run["active_analysis_id"] == live_run["active_analysis_id"]
        assert expected_run["chart_id"] == live_run["chart_id"]


def test_baseline_snapshot_covers_all_32_export_files():
    baseline = _load_baseline()
    on_disk = set(_relative_keys())
    assert on_disk == set(baseline.keys())
    assert len(on_disk) == 32


def test_baseline_snapshot_status_and_code_inventory_is_diverse():
    """Sanity check that the corpus (and therefore this harness) actually exercises the
    full status vocabulary and a wide spread of error codes, not just the happy path."""
    baseline = _load_baseline()
    statuses = {run["status"] for entry in baseline.values() for run in entry["runs"]}
    codes = {code for entry in baseline.values() for run in entry["runs"] for code in run["error_codes"]}

    assert {"completed", "partial", "blocked", "needs_input"} <= statuses
    for expected_code in (
        "METADATA_ONLY",
        "EXTERNAL_NUMERIC_CLAIM_UNVERIFIED",
        "NO_PROGRESS",
        "TASK_DELIVERABLE_MISSING",
        "TABLE_NOT_CREATED",
    ):
        assert expected_code in codes, f"Expected ground-truth error code {expected_code!r} missing from corpus"
