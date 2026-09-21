# W008 — KAP YFMEN 2025 consolidated total-assets import

Validation date: 2026-09-21. This is a new, explicitly authorized operator data-publication task, not a rerun or regrading of the original R3 model evaluation. The R3 first-attempt response, failure and scores remain unchanged.

## Canlı Docker yayını ve sorgu kanıtı

- Operatör çalışma alanı: `workspace_08034d0c9fcc47838c8da18d52bb28bc`.
- Veri kümesi: `dataset_1e1870184563e9658aeb4a9c1dffa98685e8c36b3d530498f46a572887f9fead`.
- Paylaşımlı yayın: `release_256de8ad8e971e7040b8bfc07d1ad492440407f3fdd456182bf535709ba8adde`.
- Promotion: `promotion_cf7e30263cdf4b3b5556947fcdae58c9cf1a7cc859e4a067f7b038ce5e79fb7c`.
- Sonradan HTTP API'sinden açılan `workspace_d8a9769e6326409786a80d3fb414ef53`
  yeni veriyi otomatik devraldı; `discover(query=YFMEN)` başarılı.
- `execute(2025, annual)` sonucu **10.657.492.608 TRY**, uyarı listesi boş.
- Analiz: `analysis_e1681cb1ca804beab48b67a7bc3bafcf307327017bdc8b0c3c467b3538156b60`.
- `explain_value`: fiziksel PDF sayfa 8 ve duyuru tarihi `2026-03-05T21:39:59+03:00` korundu.
- `create_chart(kind=bar)`: `status=ok`.
- Yayın paketindeki sıkıştırılmış duyuru HTML'i açılıp özgün hash ile doğrulandı.

Bu, çalışan backend'in deterministik araç testi; yeni Qwen/UI accuracy ölçümü
değildir. Mevcut kullanıcı çalışma alanları değiştirilmedi. Veri ve ham kanıtlar
`agent-runtime` kalıcı Docker biriminde; commit kaynak veritabanını içermez.

## Official source chain and verified value

The [KAP FR notification 1566753](https://www.kap.org.tr/tr/Bildirim/1566753) identifies **Yatırım Finansman Menkul Değerler A.Ş. / YFMEN**, publication **05.03.2026 21:39:59 Europe/Istanbul**, report year **2025**, annual period, **consolidated** scope and presentation currency **TL**. Its actual attachment anchor links to [31.12.2025 SPK Rapor_Final.pdf](https://www.kap.org.tr/tr/api/file/download/4028328c9c81f417019cbf1bed6c4e4f).

The independently downloaded attachment has 72 physical pages. Physical **page 8**, zero-based page index **7**, printed financial-statement **page 1** contains the consolidated statement of financial position at 31 December 2025. Its current-period total-assets cell is **10,657,492,608 TRY**, in original lira units, **scale 1**, not thousands. The unit caption specifies purchasing power at **31 December 2025**. This is a year-end balance-sheet **stock**, not an annual flow.

The page was rendered with Poppler and visually inspected, including issuer/group header, statement heading, unit/purchasing-power caption, both period headings and total-assets row. The parser separately reads and reconciles both columns:

- Current period: 9,765,163,734 current assets + 892,328,874 noncurrent assets = **10,657,492,608** total assets.
- Comparative period: 11,468,538,006 + 688,721,386 = **12,157,259,392**. These are comparative figures printed in the 2025 report at the stated purchasing power, not a separately imported 2024 vintage.
- Current total-assets cell bounding box: `[399.9623, 637.4701, 447.2993, 650.0058]`, in PDF points, top-origin coordinates on physical page 8 (page size 595.32 × 842.04 points).

The former reference's physical-page-54 pointer is not used. HTML titles or search snippets supply no imported numerical value.

## Download integrity and transport detail

Anonymous downloads and the inspect-only CLI succeeded on 2026-09-21. This capture's hashes are:

- Notification HTTP body: 5,110,484 bytes; SHA-256 `fec666db38adf3982d21cefc6a9388fd45181f9366af6e195a0afe7c9208346c`.
- Attachment HTTP body: 1,558,392 bytes; SHA-256 `93467d781a62484108a9c60418b9257b59148e341e7591fc3fcc0ba69e2e8b43`.
- Inner PDF: 1,558,365 bytes; SHA-256 `a85af21db14d5b07857af19a2147b764d1877d1bddd812163f34b294447e36e5`.

The attachment response currently contains a **27-byte Java serialized byte-array envelope**, followed by `%PDF-1.7`. The importer recognizes only the exact fixed byte-array signature and checks its four-byte payload length; it does not perform Java deserialization or find arbitrary embedded PDFs in HTML. The original downloaded bytes, including the envelope, are preserved as `source/raw.bin`; inner-PDF hash, size and envelope identity are separate provenance fields. A normal unwrapped PDF can also be validated by the same strict document checks.

The PDF's glyph transformation matrices contain numerical-zero negative shear (approximately 2e-8). pdfplumber's default upright flag incorrectly segments these glyphs. The source-specific parser accepts only positive-scale matrices with off-diagonal terms within 1e-6, marks copies of those glyphs upright and retains their text and coordinates. Genuinely rotated/skewed glyphs are rejected. This agrees with independent Poppler text extraction and the rendered page.

## Import contract and immutable evidence

Implementation: `tools/import_kap_financial_assets.py`; tests: `tests/lakehouse/test_kap_financial_assets_import.py`.

The operator entry point is `publish(store, workspace_id, notification_raw, pdf_raw)`. `download()` returns the two raw byte bodies in that order. All source validation precedes store writes. Only the pinned HTTPS notification and attachment URLs are fetched; redirects, invalid dates, wrong issuer/scope/period/unit, missing attachment chains, malformed PDFs, changed page layout and inconsistent subtotals fail closed.

Publication uses the existing `LakehouseStore.ingest_csv` → `SharedLakehouse.promote` path; no shared core code is changed. The explicit contract contains one 2025 observation, statement date 2025-12-31, issuer, consolidated scope, TRY currency, integer total assets, scale 1, annual source-report frequency, stock kind, last-value aggregation, `additive_over_time=false`, and 2025-12-31 purchasing-power basis. No prior-year observation, currency conversion, resampling or numerical adjustment is generated.

The immutable promotion contains:

- Original attachment HTTP bytes and source manifest/hash.
- Original notification bytes, losslessly gzip+base64 encoded inside `source/inspection.json#/notification_evidence`, with original URL, SHA-256, size and FR/publication metadata. Decode with `gzip.decompress(base64.b64decode(evidence["raw_data"]))`; this is the original HTML, not a rewritten extract.
- Original PDF page text, physical/printed page identities, period headers, total-assets cell coordinates/raw text, unit evidence, and current/prior subtotal checks.
- Explicit dataset contract, generated CSV clearly identified as derived ingestion input, and validated Parquet overlay.

Idempotency is keyed by parser version and original PDF download hash. Changing unrelated dynamic notification HTML does not create duplicate metrics. A matching current shared dataset is reused even from a different operator workspace; existing workspaces stay pinned. The current shared release must be selected when creating a fresh finance workspace.

## Verification

```sh
.venv/bin/python -m tools.import_kap_financial_assets
.venv/bin/python -m pytest -q tests/lakehouse/test_kap_financial_assets_import.py tests/lakehouse/test_bist_xbank_import.py
```

Results: inspect-only real download/parser succeeded; **50 tests passed** (37 KAP, 13 existing XBANK). Coverage includes real synthetic text-layer PDFs with deliberately nonofficial values, title-versus-PDF separation, malformed/HTML responses, wrong units/purchasing-power dates/periods/scope/issuer/source links, duplicate rows, subtotal mismatch, byte-array length validation, rotated-glyph rejection, download limits/redirect rejection, no workspace mutation on validation failure, exact preservation of both source bodies, repeat/cross-workspace idempotency, immutable pinned-workspace behavior, fresh-workspace discovery/execution and value lineage.

Additional unchanged-core regression command:

```sh
.venv/bin/python -m pytest -q tests/lakehouse/test_shared_lakehouse.py tests/agent/test_financial_import.py tests/agent/test_pdf_research.py
```

Result: **64 tests and 52 subtests passed**. Python compilation and `git diff --check` also passed. No shared core files were edited.

An additional **real-source isolated-store integration check** succeeded: the independently downloaded KAP bytes were imported into a temporary local lakehouse, promoted, loaded into a newly created workspace, discovered via `KAP YFMEN toplam varlıklar`, and executed for 2025. It returned exactly one row with `total_assets=10657492608`, TRY, scale 1, stock, annual, price basis 2025-12-31, with no execution warnings. That temporary test store was removed after the check; its IDs are not live publication IDs.

The discovery result preserves `SESSION_DATASET` identity and the official source URL. The current generic discovery router has no special `kap` source alias; this import does not claim otherwise or change routing code. Official-domain eligibility is checked by shared promotion, and all source evidence is retained in lineage.

## Live publication handoff

No live runtime/Docker store was changed by the importer author, and no commit was made. The main operator is responsible for the authorized live publication, then recording actual dataset/promotion/shared-release IDs and a fresh live-workspace verification below. Do not use temporary validation IDs as live evidence.

Explicit publication command (requires an existing operator workspace and the runtime store path):

```sh
.venv/bin/python -m tools.import_kap_financial_assets --publish --store /absolute/runtime/store --workspace-id workspace_operator
```

Live publication identifiers and verification: **pending main-operator publication**.
