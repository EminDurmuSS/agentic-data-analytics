# Cross-source data quality

`build_cross_source_reconciliation.py` compares housing-credit stocks without
forcing different institutional or geographic scopes to match.

The primary direct reconciliation is:

1. BDDK monthly sector housing-credit stock in million TRY.
2. FinTurk sector housing-credit stock summed across the 81 domestic provinces.
3. FinTurk values converted from thousand TRY to million TRY.

FinTurk `YURT DIŞI`, TBB reporting-bank totals and EVDS totals remain separate
columns. Their differences are reported as scope diagnostics, not silently
corrected and not treated as proof that a source is wrong.
