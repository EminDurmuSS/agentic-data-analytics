# Local DuckDB lakehouse

Build the self-contained analytics database with:

```bash
.venv/bin/python data_pipeline/lakehouse/build_lakehouse.py
```

The generated `analytics.duckdb` copies validated Parquet data into schemas:

- `catalog`: assets, metrics and build manifest
- `evds`: source observations and aligned panels
- `bddk`: monthly semantic data, FinTurk and completed weekly data
- `tbb`: quarterly consumer-credit reports
- `quality`: cross-source reconciliation
- `evidence`: official event annotations
- `analysis`: ready-to-query monthly and quarterly housing-credit tables

Example query:

```sql
SELECT month,
       bddk_housing_credit_stock_million_tl,
       TP_KTF12 AS housing_credit_rate_pct,
       housing_credit_real_mom_pct
FROM analysis.housing_credit_monthly
WHERE rate_down_real_stock_not_up_quality_screened
ORDER BY month;
```

The database contains copied tables, so it does not rely on machine-specific
absolute paths after it has been built.

Current validated build:

- 7 schemas
- 32 tables
- 60 locally available EVDS series represented in the metric catalog
- 147,154 BDDK weekly measurements
- 66 unique monthly analysis periods
- 22 unique quarterly analysis periods
