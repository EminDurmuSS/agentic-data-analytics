# Financial chart readability, 13 September 2026

The reported comparison used independent panel axes for two amounts expressed in million TL. The original saved bar chart rendered the company amount almost as tall as the sector amount, although the numerical ratio was approximately 9.62%. This made the comparison visually misleading.

The renderer now uses shared axis bounds and equal plot heights for panels whose financial metadata is compatible. Percentages, different currencies, price bases, measurement methods, and unreviewed semantics remain separate. Legend changes preserve the shared range. Vertical and horizontal bars use the original numerical values and preserve missing observations.

A saved line or area chart containing one observed period now explains why no trend can be drawn and offers an explicit **Çubuk grafik göster** action. Loading the analysis does not change its saved chart selection. Newly selecting a line or area requires at least two observed periods.

Chart and analysis warnings appear in one disclosure area. Report names and PDF page links replace internal dataset identifiers in the chart source list. Exports include readable sources and material warnings, including the scope caveat that the ratio is not an official market share.

## Preservation and validation

- Chart presentation metadata is added after artifact hash validation. Existing artifact bytes, chart specifications, analysis rows, source values, and workspace versions are preserved.
- Legacy structured warnings receive readable explanations without changing their saved representation.
- The reported values remain 4,783,750.292 and 49,735,194 million TL, with a calculated ratio of 9.618441001758232%.
- 90 tests and 37 subtests passed across `tests/app` and `tests/agent/test_agent_charts.py`. All three functional Chromium suites ran, including the new readability regression. Two existing framework deprecation warnings remain.
- Browser regression checks inspect actual ECharts axis extents and rendered bar dimensions, preserve nulls, cover both orientations and single-period line/area selections, reject unsafe source links, and inspect downloaded SVG text for sources and the scope caveat.
- The real saved comparison passed 14 browser checks. A read-only replay of its original bar specification produced company/sector bar lengths of 17.6042/183.0255 pixels, matching the numerical ratio. The saved area selection, analysis identity, and workspace version were unchanged. The chart fit a 390-pixel mobile viewport; there were no browser errors or write requests.

Run the focused suite with Playwright available through `PLAYWRIGHT_MODULE`:

```sh
.venv/bin/python -m pytest tests/app tests/agent/test_agent_charts.py -q
```

These checks establish chart presentation behavior. They do not revalidate the financial statements or certify equivalence between the company and sector reporting populations.
