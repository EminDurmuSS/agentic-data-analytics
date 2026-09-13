# Conversation readability, 13 September 2026

The fixed activity card and empty composer left only 150 px for the conversation
on a 1200 x 650 screen. Long pasted questions and late follow-up suggestions also
pushed the answer out of view.

The activity summary now scrolls with the conversation and occupies one row when
closed. Long user messages can be expanded, and the composer grows with its text.
Follow-up cards show the main question with the complete prompt and reason in an
accessible disclosure. Selecting a card still fills the original complete prompt.
Loading suggestions preserves the reader's scroll position. Workspace and
conversation changes retain the activity element and its event handlers.
Interrupted work opens at its resume control, or at the latest question when no
resumable job exists, instead of returning to an older answer.

## Browser measurements

Measured against the same saved PDF comparison in Chromium 131.0.6778.33:

| Viewport | Previous message viewport | New message viewport |
| --- | ---: | ---: |
| 1440 x 900 | 419 px | 648 px |
| 1366 x 768 | 287 px | 516 px |
| 1200 x 650 | 150 px | 398 px |
| 390 x 844 | 365 px | 489 px |

The empty composer decreased from 135 px to 87 px. The complete saved answer was
visible at all three desktop sizes. Mobile uses the existing stacked page layout.

## Verification

- `tests/app`: 94 tests and 16 subtests passed, including the Chromium integration
  suites. Two existing framework deprecation warnings remain.
- The three directly affected browser suites passed again after the interrupted
  work scroll correction.
- 13 saved-workspace browser checks passed: layout, overflow, keyboard disclosure,
  prompt folding, composer growth, complete suggestion prefill, divider resizing,
  and conversation reset.
- Six synthetic browser checks passed for empty-workspace welcome content,
  example prefill, interrupted-run resume, reset after resume controls, and the
  visibility of a pending question or resume button after a long older answer.
- No JavaScript errors during browser verification. Optional suggestion POSTs were
  intercepted and served from the cached GET response; no server mutations were
  sent by the final browser checks.
- Before/after workspace, conversation, run, analysis, and chart snapshots matched.
  Their combined SHA-256 was
  `6dda1eeea18904580def9af52d495019c73dd2bf0574a1a19d1d6f4b1d503e7d`.
  The saved area chart was preserved.

This change adds no dependencies. The visual checks cover Chromium at the listed
sizes; they do not establish Safari or Firefox coverage.

Screenshots, geometry, and browser scripts are retained locally under
`conversation-layout-2026-09-13` in the mentor-improvements evidence directory.

## Follow-up: bottom alignment

A long chart result exposed a second issue: scrolling the outer desktop page
increased the space below the composer from 16 px to 98 px. The sticky conversation
kept subtracting the 82 px header even after that header had scrolled away.

The desktop workspace now occupies the viewport, and the result panel scrolls
independently. At 1200 x 650, 1366 x 768, and 1720 x 1000, the composer retains
an 8 px bottom inset both initially and after scrolling to the end of the actual
saved chart. The document height equals the viewport height. The four existing
Chromium integration suites passed after this correction.
