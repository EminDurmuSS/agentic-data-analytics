# Contextual follow-up questions, 13 September 2026

Completed answers now receive up to three follow-up questions based on the latest question, the answer actually shown to the user, recent question/answer pairs from that conversation, and verified saved analysis evidence. Static chart recommendations no longer appear in the conversation or chart panel.

## Selection and presentation

The context builder inspects existing periods, values, units, financial semantics, operations and source references. It prepares a bounded pool of feasible questions. For example, a single-period PDF result can suggest finding a comparable period in the same report, while a regular time series can identify the actual largest adjacent-period change. PDF suggestions follow the specific source column through scale conversions. A total-components question targets an actual total measure, rather than whichever column happens to appear first.

The existing Qwen model first identifies topics already answered, then selects useful remaining candidate IDs. Local validation rejects invented IDs and removes answered topics and duplicate intents. The model cannot rewrite the questions, introduce numerical claims or execute tools. Selection is probabilistic; the feasibility gates and validated candidate text constrain what can be shown.

Cards show the complete question, a short reason and an additional-data hint when appropriate. Clicking fills the composer for the user to edit and send. New questions, conversations and workspace changes clear old cards. Questions are independent of chart appearance and may be absent when there is no useful eligible suggestion.

## Runtime contract

- The financial run finishes before optional suggestion generation. Neither the immutable run journal nor saved analytical artifacts are rewritten.
- A separate two-worker queue, with at most eight pending jobs, uses a separate 20-second client budget without retries. Generation makes one tool-free call capped at 700 output tokens.
- Atomic per-run sidecars and a process-shared file lock make POST idempotent. GET only reads status. Successful and failed outcomes are cached; polling cannot trigger repeated model calls.
- Workspace, conversation, run, observed analysis and context digest are checked again before publication. Results for a superseded conversation or run are hidden, including cases where both runs use the same analysis.
- Browser polling stops after 60 seconds. Optional failures do not replace the main answer with an error.
- The deployed Qwen gateway returned empty choices for the strict JSON-schema response format during live testing. JSON-object mode succeeded; the same candidate-ID schema remains enforced locally.

## Verification

The complete suite passed: **850 tests and 269 subtests**, including all four functional Chromium suites. Two existing framework deprecation warnings remain. New coverage includes immutable-artifact validation, cumulative and irregular data, joint-sample limits, actual change selection, history isolation, already-completed operations, concurrency, interrupted jobs, stale ownership, prefill-only clicks and mobile reachability.

Live selection used `kkbhackathon2026/Qwen3.8-27B` with the user's saved Garanti/BDDK comparison. The actual browser received three questions in approximately 2.05 seconds: previous-period comparison, compatible-scope comparison and total-assets components. All **13 live browser checks** passed. Reload used the same cached questions. The run and analysis identities were preserved; 15 source, dataset and chart files retained their SHA256 hashes across the browser-validation interval.

A separate evaluation fixture used 18 actual BDDK monthly observations from January 2025 through June 2026. The builder located the largest absolute adjacent change between November and December 2025, using 44,967,721 and 46,946,798 million TL. Changing the supplied answer/history to include completed change calculations and source/component checks caused the live selector to leave only the unanswered anomaly question. These are real-data suggestion-selection tests, not newly executed follow-up analyses or a fresh PDF ingestion test.

The machine-readable evidence summary is in [contextual-followups-verification.json](contextual-followups-verification.json).

Run the full suite with Playwright available through `PLAYWRIGHT_MODULE`:

```sh
.venv/bin/python -m pytest -q
```
