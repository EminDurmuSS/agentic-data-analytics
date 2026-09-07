# KKB veri katmanı

Araştırma tarihi: 7 Eylül 2026. Ana analiz dönemi: Ocak 2021-Haziran 2026.
Yıllık değişim ve gecikmeli analiz için gerekli yerlerde 2020 hazırlık verisi
de korunur.

## Kaynak kapsamı

| Kaynak | Yerel kapsam | Frekans | Durum |
| --- | --- | --- | --- |
| BDDK aylık | 17 tablo, 66 ay, sektör | Aylık | Tamamlandı |
| BDDK haftalık | 9 tablo, 286 hafta, sektör | Haftalık | Tamamlandı |
| BDDK FinTürk | 7 tablo, 7 grup, 81 il ve yurt dışı, 22 dönem | Çeyreklik | Tamamlandı |
| TCMB EVDS | 60 seçilmiş seri | Günlük, iş günü, haftalık, aylık, çeyreklik | Tamamlandı |
| TCMB EVDS katalog | 52.696 seri metadata kaydı | Metadata | Tamamlandı |
| TBB | 21 yayımlanmış tüketici kredisi raporu | Çeyreklik | Kaynak boşluğuyla tamamlandı |
| BDDK ve TCMB belgeleri | 8 resmî PDF | Olay/yöntem | Tamamlandı |

TBB Haziran 2026 raporu 7 Eylül 2026 itibarıyla kaynakta yayımlanmamıştır.
Bu dönem boş bırakılmış, tahmin veya başka seriden kopyalama yapılmamıştır.

## EVDS seçim mantığı

EVDS'deki her tarihsel gözlemi indirmek yerine tam metadata kataloğu yerelde
tutulur ve analitik olarak gerekli seriler seçilir. 60 seri şunları kapsar:

- Konut kredisi stoku, faizi ve kredi arz-talep anketleri
- Konut satışları, KFE, birim fiyatlar ve kiralar
- TÜFE, enflasyon ve politika faizi beklentileri
- Türkiye, ABD ve Euro Bölgesi politika faizleri
- Döviz kuru, altın, BIST 100 ve mevduat faizi
- İşsizlik, tüketici ve reel kesim güveni, sanayi üretimi
- Reel GSYİH ve hanehalkı tüketimi
- Yapı ruhsatı ve yapı kullanma izni göstergeleri

Seri seçimi kaynak kataloğundaki kod, ad, birim, frekans ve toplulaştırma
özelliklerine dayanır. Günlük ve haftalık seriler yıllık istek parçalarıyla
çekilir. Her istek ve cevap SHA-256 ile saklanır.

## Veri doğruluğu ilkeleri

1. Kaynak boş değerler sıfıra veya tahmine çevrilmez.
2. Stok, stok değişimi ve yeni kullandırım akımı ayrı kavramlardır.
3. Kümülatif kaynak değer ile türetilmiş aylık akım birlikte saklanır.
4. Çeyreklik veri ara aylara forward fill edilmez.
5. Frekans dönüşümü seri bazlı açık kurala dayanır.
6. Kaynak kapsam farkları zorla eşitlenmez.
7. Birlikte hareket nedensellik kanıtı olarak sunulmaz.
8. Her işlenmiş satırın kaynak dosyası ve hash bilgisi korunur.

## Doğrulama özeti

- BDDK aylık: 1.122 kaynak sayfası, 33.965 satır, durum `passed`
- BDDK haftalık: 2.574 kaynak sayfası, 147.154 ölçüm, durum `passed`
- FinTürk: 936.512 ölçüm, durum `passed`
- EVDS seçilmiş seriler: 10.787 gözlem, üç paket de `passed`
- TBB: 21 yayımlanmış dönem, durum `passed_with_source_gaps`
- Birleşik katalog: 44 varlık, 55.457 metrik, durum `passed`
- DuckDB: 32 tablo, durum `passed`

## Üretim sırası

Mevcut ham dosyalardan çalıştırılabilir temel sıra:

```bash
.venv/bin/python data_pipeline/build_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_all_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_semantic_dataset.py
.venv/bin/python data_pipeline/bddk/build_finturk_dataset.py
.venv/bin/python data_pipeline/bddk/build_weekly_dataset.py
.venv/bin/python data_pipeline/tbb/build_consumer_credit_dataset.py
.venv/bin/python data_pipeline/quality/build_cross_source_reconciliation.py
.venv/bin/python data_pipeline/evidence/events/build_events.py
.venv/bin/python data_pipeline/catalog/build_unified_catalog.py
.venv/bin/python data_pipeline/lakehouse/build_lakehouse.py
.venv/bin/python tools/build_data_status_notebook.py
.venv/bin/python data_pipeline/build_file_manifest.py
.venv/bin/python -m unittest discover -s tests -v
```

Bu komutların çoğu ağ erişimi kullanmadan mevcut ham kaynaklardan işlenmiş
çıktıları üretir. Canlı yeniden indirme ayrı araçlarla yapılır ve eski snapshot
ile yeni snapshot sessizce karıştırılmaz.

## Ana çıktılar

- `catalog/unified/unified_data_catalog.parquet`
- `catalog/unified/unified_metric_catalog.parquet`
- `catalog/unified/queryable_metric_catalog.csv`
- `lakehouse/analytics.duckdb`
- `bddk/processed/monthly_semantic/measurements_long.parquet`
- `bddk/processed/weekly_all_sector/measurements_long.parquet`
- `bddk/processed/finturk_all_groups_all_cities/measurements_long.parquet`
- `evds/housing_causality_v1/monthly_panel.parquet`
- `evds/housing_causality_controls_v1/monthly_panel.parquet`
- `evds/market_controls_v1/monthly_panel.parquet`
- `tbb/processed/housing_credit_quarterly.parquet`
- `evidence/events/events.json`

`lakehouse/analytics.duckdb` verileri kendi içine kopyalar. Sorgu sırasında bu
bilgisayardaki mutlak dosya yollarına ihtiyaç duymaz.
