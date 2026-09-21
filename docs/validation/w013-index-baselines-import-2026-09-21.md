# W013 — Official Borsa İstanbul index baseline importer

Validated and published on 2026-09-21. This note records importer validation,
not a rerun or rescore of the R3 UI evaluation. No R3 report or evidence changed.
Implementation tests were isolated; the coordinating operator subsequently
published and verified the data in the running Docker runtime as recorded below.

## Canlı yayın kanıtı

- İçe aktarma çalışma alanı: `workspace_25e459b9991d4d1ab8d6b9775d27a68d`.
- Veri kümesi: `dataset_d87adfa200dfbcc0e26017d19c2d7521947bd1e9745314c9935fcd00a4c3746d`.
- Yayın: `release_941f38d8d36195c18879cef5bab766ee06b03592a7329a98f4f429bceaca1afc`.
- Promotion: `promotion_fa896c8a8f94030e5d03b34e3cdec082c99b0d271eea605894bc6bdc55704a7c`.
- Uygulamanın HTTP API'sinden sonradan oluşturulan tüketici çalışma alanı:
  `workspace_7eff1a4979d44298b66a3c7225d77215`; güncel yayını otomatik bağladı.
- Canlı `discover`: XU100, XBANK, XUMAL için yeni metrik bulundu.
- `aggregate_dataset(source_value, group_by=index_code)`: üç özgün değer doğrulandı.
- Analiz: `analysis_aa4bf1b98a8f758eedf0b2547523918dbf4f2ba78dd1b83fbaf4dd9f6ac66c4f`.
- `create_chart(kind=bar)`: `status=ok`, üç satır. Bu, backend araç doğrulamasıdır;
  Qwen'in özgün W013 sorusunu yeni UI koşusunda tamamladığı iddiası değildir.

Ham dosya ve yayın paketi Docker `agent-runtime` kalıcı birimindedir. Git commit'i
çalışan veritabanının yedeği değildir; tekrar üretim için importer ve bu kaynak izi
korunur. Eski çalışma alanları sabitlenmiş eski yayını kullanmaya devam eder.

## Source and meaning

The anonymously available [official workbook](https://www.borsaistanbul.com/files/bist-endeks-kodlari-ve-baslangic-degerleri.xlsx)
contains `Sheet1`, 513 rows and five worksheet columns (the selected data use
A–D). Downloaded length: **36,677 bytes**. SHA-256:
`ff54d9b0076789a78f52e8211edf98f1a6d34e2fb7e778eb1ce7da03d0665977`.

The title and row-3 headers identify index base values, not investment returns.
The selected official cells are literal **text**, not numeric or formula cells:

- XU100: `A4:D4`, `D4 = 01.01.1986=0,01`; parsed date `1986-01-01`, base value `0.01`.
- XBANK: `A358:D358`, `D358 = 27.12.1996=9,14`; parsed date `1996-12-27`, base value `9.14`.
- XUMAL: `A352:D352`, `D352 = 28.12.1990=0,33`; parsed date `1990-12-28`, base value `0.33`.

These are source-reported index points at **different base dates**. Their numeric
ordering is not a performance ranking, return comparison, current-price
comparison, or a common-date observation. The importer does not rebase or apply
a historical scale correction. Those limitations are also stored in the metric
semantics and document provenance.

## Implementation

- [Operator importer](../../tools/import_bist_index_baselines.py): default
  read-only download/validation; explicit `--publish` required for mutation.
- [Tests](../../tests/lakehouse/test_bist_index_baselines_import.py): 25 tests,
  including a synthetic source with deliberately different numbers to detect
  accidental use of hardcoded known answers.

The reusable entry point is `publish(store, workspace_id, raw)`. It validates the
entire selected source before any store call, registers the unmodified XLSX with
`DocumentTools`, writes an inspection containing the original text, ingests the
derived CSV under an explicit contract, and invokes `SharedLakehouse.promote`.
The source values come only from downloaded cells; known official values appear
in tests and this evidence note, not as input constants in the parser.

Validation covers bounded downloads and XLSX expansion, duplicate ZIP parts,
formula rejection, exact source title/headers and code/name identity, one row per
target code, real calendar dates, and an unambiguous positive decimal-comma
literal. Redirects are not followed. The URL is fixed and requires no credential.
Changed/ambiguous data fail rather than being substituted with a remembered value.

Source row numbers are discovered, not assumed. Both derived `base_date` and
`base_value` point back to the same original D cell with its unmodified text and
explicit parsing transformation. Raw-file hash, URL, inspection, CSV, Parquet and
cell origins survive in the immutable promotion package. The store's sorting
permutation is verified so a sorted XUMAL result still resolves to its own D cell.

The supported cross-sectional contract is `frequency="static"`,
`date_column=null`, key/grain `index_code`, numeric measure `base_value`, kind
`index`, unit `index`, scale 1, nonadditive, no inferred currency. `base_date`
remains a date-typed descriptive column, not a shared time axis. `discover`
finds the metric using XU100, XBANK, XUMAL or Turkish baseline wording;
`describe` exposes its static frequency and warning.

The existing `aggregate_dataset` route with `group_by="index_code"` and
`source_value` creates a three-category analysis usable by `create_chart` with
`kind="bar"`. No time bucket is needed. Calendar `execute` or date bucketing is
not appropriate and is tested to reject these static data. Names, dates and
original source strings remain in the dataset/inspection/provenance; a generic
numeric source-value chart alone does not deliver all W013 explanatory text.

## Commands and results

Executed from the repository root:

```sh
.venv/bin/python -m tools.import_bist_index_baselines
.venv/bin/python -m pytest -q tests/lakehouse/test_bist_index_baselines_import.py tests/lakehouse/test_bist_xbank_import.py tests/agent/test_dataset_analytics.py
```

The first command only inspected the fresh official download and returned the
three values/dates/rows/hash above. The combined tests finished with **53 passed,
8 subtests passed**. The new importer's own 25 cases are included in those 53.

A second check downloaded the same official file and ran the real publication
APIs in `TemporaryDirectory(prefix="w013-official-validation-")`, using a tiny
isolated DuckDB seed and `workspace_operator` / `workspace_reader` workspaces.
This was not the production runtime store. Its results were:

- All three source values matched the original cells: XU100 `0.01/D4`, XBANK
  `9.14/D358`, XUMAL `0.33/D352`; original strings and split transformation retained.
- Saved analysis: three rows; native frequency `static`; bar chart status `ok`.
- Identical repeat in the same operator workspace: `publication_performed=false`.
- Previously opened workspace: unchanged; newly opened workspace: shared data
  discoverable and usable.
- Promoted `source/raw.bin`: byte-for-byte identical to the downloaded XLSX.

The first synthetic integration run exposed an incorrect test expectation about
cell-origin array order after store key sorting. The test was corrected to select
the index identity and follow `source_rows`; the source parser and source values
were not changed to satisfy the expectation.

## Live operator prerequisites and handoff

Use an existing, dedicated operator workspace tied to the intended snapshot and
the actual runtime store path, not the base DuckDB file. Ensure the application
environment contains the new module and its existing dependencies (`httpx`,
`openpyxl`, DuckDB/pandas/Parquet stack). No model prompt or agent API call is
needed for the import.

The operator may call `publish(store, workspace_id, raw)` with a validated fresh
download, or use the CLI with explicit values:

```sh
python -m tools.import_bist_index_baselines --publish --store /absolute/runtime/store --workspace-id EXISTING_OPERATOR_WORKSPACE
```

That command is a **template**, not a record of live execution by this author.
The identical raw file and parser version are idempotent within the same import
workspace. Do not create a new operator workspace for every repeat. Existing
user workspaces intentionally stay pinned; a new consumer workspace must opt
into the current shared release. A changed source hash represents a new source
version and requires explicit publication; old packages remain immutable.

The importer prepares durable source-backed data and a usable static analysis
path. It does not establish that Qwen now completes W013 through the UI, renders
the requested chart, or states the differing-base-date limitation. Those require
a fresh post-publication UI test, separate from the preserved R3 failure.
