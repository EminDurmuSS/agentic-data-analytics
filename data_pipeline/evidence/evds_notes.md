# EVDS3 public data fetch proof — 2026-09-07

This is an intermediate research note for the root agent to incorporate and persist.

## Verified series

| Measure | Official code | Group | Native frequency |
|---|---|---|---|
| Housing loan rate TRY flow weighted rate (%) | TP.KTF12 | bie_kt100h | Weekly Friday |
| Türkiye residential property price index | TP.KFE.TR | bie_kfe | Monthly |
| CPI general index, 2025 series | TP.TUKFIY2025.GENEL | bie_tukfiy2025 | Monthly |

Names/codes were matched with actual HTTP 200 official frontend catalog JSON, not guessed. Catalog snapshots: rates_series.json, kfe_series.json, cpi_series.json. rates_search.json contains official individual rate metadata with native frequency HAFTALIK(CUMA), data source Mevduat, Kalkınma ve Yatırım Bankaları, and group ID. Source code conversion TP.KFE.TR -> TP_KFE_TR is the returned JSON column naming convention.

## Public access actually executed

Official public SPA HTML: https://evds3.tcmb.gov.tr/charts/portlet/Njk4YjFjMzljNjAxMWY0MDU2MDdjYzll/tr (HTTP 200).
Official script advertised by page: https://evds3.tcmb.gov.tr/assets/index-DsYSds65.js (HTTP 200; saved evds_index.js).
The site's own client advertises these ordinary read-only data methods:

- GET /igmevdsms-dis/serieList/fe/type=json&code=GROUP
- GET /igmevdsms-dis/searchResults?searchVal=CODE
- GET /igmevdsms-dis/public/charts/portlet/ENCODED_ID
- POST /igmevdsms-dis/fe (data query)
- POST /igmevdsms-dis/fe/excel-indir (Excel export; discovered, NOT tested)
- POST /igmevdsms-dis/serieList/baslangicBitis (range metadata; discovered, NOT tested)

No API key, session cookies, credentials, or authentication workaround were used. The public frontend POST /fe accepted the site's normal JSON query body with HTTP 200. This is distinct from the documented authenticated API. It is a working public-site extraction route today, not a claim of a stable documented API contract.

## Raw observation files

kfe_data.json: 66 monthly observations 2021-01 through 2026-06, first 16.49, last 231.34. No missing/null/ND, no duplicate month, expected 66-month coverage complete.
cpi_data.json: 66 monthly observations 2021-01 through 2026-06, first 16.12513498, last 129.99. No missing/null/ND, no duplicate month, expected 66-month coverage complete.
rate_data.json: 288 weekly observations, first 01-01-2021 (18.61), last 03-07-2026 (41.45). No missing/null/ND, no duplicates, all date spacings exactly seven days.

IMPORTANT: Request endDate was 30-06-2026 but weekly response includes the week ending 03-07-2026. Keep raw response intact; the processed series must explicitly filter observation date <= 2026-06-30, yielding 287 weeks, last 26-06-2026. This API boundary behavior was actually observed.

Exact request bodies: kfe_data_request.json, cpi_data_request.json, rate_data_request.json.
Response structure: totalCount, items, transposedItems, transposedColumns, seriesNames, frequencyConversion. Parse items; value columns are decimal strings. Monthly Tarih is YYYY-MM; weekly is DD-MM-YYYY. Avoid UTC conversion of UNIXTIME to infer dates; use published Tarih as period key.
Checksums/status/first/last/missing counts: evds_validation.json.

## Official source links

- KFE page: https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Reel+Sektor+Istatistikleri/Konut+Fiyat+Endeksi/
- KFE catalog linked by TCMB page: https://evds3.tcmb.gov.tr/tumSeriler/2003/bie_kfe
- KFE table: https://evds3.tcmb.gov.tr/charts/portlet/Njk3OWY4ODM5NjgyN2M2YzU2OGIyNTlm/tr
- KFE regional table: https://evds3.tcmb.gov.tr/charts/portlet/Njk3OWZjMzk5NjgyN2M2YzU2OGIyNWEy/tr
- Loan-rate table: https://evds3.tcmb.gov.tr/charts/portlet/Njk4YjFjMzljNjAxMWY0MDU2MDdjYzll/tr
- Loan-rate methodology page: https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Faiz+Istatistikleri/Haftalik/Kredi+Faiz+Oranlari/
- CPI catalogue: https://evds3.tcmb.gov.tr/tumSeriler/2005/bie_tukfiy2025

For downstream monthly rates: a mean of available weekly flow-weighted annual rates is a derived monthly summary, not volume-weighted monthly lending rate. Retain weekly series, label aggregation, include n weeks; month-end last observation is a separate derived option. No annual-to-monthly rate conversion should be conflated with frequency aggregation.
