# Independent consistency oracles

This is a copy of the local oracle notes. References to `oracles.json` below mean `tmp/agent-consistency-2026-09-10/oracles.json` relative to the repository root. Full local evidence paths and hashes are recorded in [results.json](results.json); the large oracle arrays are not bundled in this directory.

Produced from the active lakehouse with DuckDB read_only=True and direct SELECT statements. The validation script was read for schema hints and was not imported or run. No model or network calls were made. Only oracles.json and this file were written.

- A: monthly sector net profit is 87,249 / 82,152 / 119,287 million TRY for January / February / March 2026; Q1 total is 288,688 million TRY. Raw YTD values are 87,249 / 169,401 / 288,688. Their sum, 545,338, is an incorrect Q1 answer. January starts a new annual accumulation.
- B: KOBI cash loan stocks are 4,864,634 and 7,253,286 million TRY in June 2025 and June 2026. CPI index values are 98.39599463 and 129.99. Nominal YoY is 49.1023990705159%; CPI YoY is 32.10903603221191%; real YoY is 12.863134535445809%. Subtracting percentage growth rates yields 16.99336303830399% and is incorrect.
- C: raw TP.GY1.N2 April / May / June 2026 values are 100.6 / 103.3 / 103.5. The active binding has review_required status with unknown unit/kind. Its native values can be reported with the applicable warning. TP.GY1.N2.MA is a different, seasonally adjusted series and does not satisfy this request.
- D: all 60 months from January 2021 through December 2025 are present. Arrays, independent SQL, gold SQL, all gold rows, raw credit cells, all weekly rate inputs, CPI inputs and KFE inputs are in oracles.json. January 2021 nominal and real credit both equal 276,785 million TRY; base CPI is 16.12513498. December 2025 nominal credit is 678,970, real credit is 99,182.75317806524, rate is 37.29%, and KFE is 204.36. The independent maximum residual from the curated real-credit gold is 0.0.
- E: count metric is bddk_monthly:table06:5:414c01e26c04:NakdiKrediToplam. It is count scale 1 with no currency. Monetary deflation must fail with UNIT_MISMATCH or a clear equivalent refusal without computed deflated counts. Customer counts are not deduplicated people across banks or product columns.

## Grading cautions

1. Compare numeric meaning and units, not exact text, column names, citation wording or artifact IDs. Million, billion and trillion TRY conversions are acceptable if consistent. Presentation rounding is acceptable to the stated precision. For machine artifact comparison use tolerance 1e-7 for real credit, 1e-10 for growth percentages, and 1e-9 for rate/index values; counts and nominal million-TRY stocks are exact integers here.
2. BDDK_MONTHLY group_code 10001 means Sektor, verified in the source row BankaAdi and the binding label. Codes from other BDDK namespace catalogs are not interchangeable. Retain the Toplam currency dimension for profit and housing and NakdiKrediToplam for KOBI cash credit.
3. Q1 must sum monthly flow values, not the YTD values. Stock comparisons use June endpoints, not a sum or average. Real growth is a ratio of growth factors, not nominal percentage minus CPI percentage.
4. Housing credit uses BDDK sector total while the EVDS rate series covers its documented institution population. A shared calendar month is not proof of equal populations. Weekly-rate monthly mean is unweighted across published weekly observations, not the last Friday and not a volume-weighted monthly estimate.
5. CPI rebase is January 2021 even though the source CPI has 2025=100. Neither source index divided by 100 nor an arbitrary latest CPI base satisfies the requested January 2021 price basis.
6. For the multi-turn housing sequence inspect all 60 stored result rows at each stage. Sampled previews or narrative assurances alone do not demonstrate preservation. Stage two changes only credit among existing columns and adds CPI; stage three only adds KFE. Audit both the current analysis lineage and immutability of earlier analysis artifacts.
7. C is deliberately a native bulk-catalog series under semantic review. Grade value correctness separately from policy behavior. A reviewed-ready claim or silent substitution to the adjusted index is a failure even if some displayed values coincide. A platform refusal is a capability outcome and should not be mislabeled as incorrect numeric data.
8. A count-based refusal does not require making an invalid execution call if the assistant identifies the unit conflict from metric metadata. If it does validate, expect UNIT_MISMATCH. A normalized or CPI-divided numeric customer series is not monetary deflation and must not be returned as such.
9. Source evidence completeness and raw file verification are distinct. Here 84 distinct source files had SHA-256 verified, and 464 requested/input cells were checked directly. EVDS checks hash decompressed JSON, BDDK checks file bytes. Full-catalog source_cell_path is the authoritative JSON Pointer; source_row_index is only a one-based date-row anchor.
10. These oracles validate the local dataset as of the recorded database checksum. They do not independently establish that the local dataset equals a current public release. Request payloads and potential credentials were not read or copied.

## Artifact verification

- oracles.json SHA-256: 03219dc5bcba8fcade70ef16bf350331b70984ca866a23ac905077f78f053772
- Database SHA-256 before and after: c2e6c9fd749efe5c96e9e4dc23b925a792cce57b3d96ad42cc02969d8395b81a
- Database bytes: 621031424; size, mtime and hash unchanged.
- Verified source files: 84; verified source cells: 464.
- Machine artifact: tmp/agent-consistency-2026-09-10/oracles.json
