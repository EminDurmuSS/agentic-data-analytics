# KKB veri katmanı

Araştırma tarihi: 9 Eylül 2026. Ana analiz dönemi: Ocak 2021-Haziran 2026.
Yıllık değişim ve gecikmeli analiz için gerekli yerlerde 2020 hazırlık verisi
de korunur.

## Kaynak kapsamı

| Kaynak | Yerel kapsam | Frekans | Durum |
| --- | --- | --- | --- |
| BDDK aylık | 17 tablo, 66 ay, 10 resmî banka grubu | Aylık | Tamamlandı |
| BDDK haftalık | 9 tablo, 286 hafta, 7 resmî banka grubu | Haftalık | Tamamlandı |
| BDDK FinTürk | 7 tablo, 7 grup, 81 il ve yurt dışı, 22 dönem | Çeyreklik | Tamamlandı |
| TCMB EVDS ulusal | 61 seçilmiş kaynak seri ve 1 türetilmiş seri | Günlük, iş günü, haftalık, aylık, çeyreklik | Tamamlandı |
| TCMB EVDS bölgesel | 519 il ve bölge bazlı konut serisi | Aylık, çeyreklik | Kaynak boşluklarıyla tamamlandı |
| TCMB EVDS hanehalkı finansmanı | 3 KKM ve 1 hanehalkı mevduat serisi | Aylık | Tamamlandı |
| TÜİK il konut satışları | 81 il, 78 ay, 5 satış metriği | Aylık | Tamamlandı ve EVDS ile uzlaştırıldı |
| İl bazlı konut paneli | 81 il, 22 çeyrek, 33 analitik metrik | Çeyreklik | Yayımlanmayan fiyat ve kiralar ile ayrı fiyat proxy'si işaretli |
| TCMB EVDS katalog | 52.696 seri metadata kaydı | Metadata | Tamamlandı |
| TBB | 21 yayımlanmış tüketici kredisi raporu | Çeyreklik | Kaynak boşluğuyla tamamlandı |
| TBB Risk Merkezi | 6 Haziran bülteni, 66 aylık panel, 5 konut kredisi metriği | Aylık | Tamamlandı ve vintage revizyonları denetlendi |
| BDDK ve TCMB belgeleri | 8 resmî PDF | Olay/yöntem | Tamamlandı |

TBB Haziran 2026 çeyreklik tüketici kredileri raporu 9 Eylül 2026 itibarıyla
kaynakta yayımlanmamıştır. Bu dönemin parasal kullandırım tutarı boş bırakılmış,
tahmin veya başka seriden kopyalama yapılmamıştır. Ayrı yayın ailesindeki Risk
Merkezi Haziran 2026 aylık bülteni mevcuttur, fakat ilk kez kullanan kişi sayısı
parasal kredi kullandırım tutarı değildir.

## EVDS seçim mantığı

EVDS'deki her tarihsel gözlemi indirmek yerine tam metadata kataloğu yerelde
tutulur. Yerel 584 kaynak seri üç katmana ayrılır.

Ulusal 61 kaynak seri şunları kapsar:

- Konut kredisi stoku, faizi ve kredi arz-talep anketleri
- Konut satışları, KFE, birim fiyatlar ve kiralar
- TÜFE, enflasyon ve politika faizi beklentileri
- Türkiye, ABD ve Euro Bölgesi politika faizleri
- Döviz kuru, altın, BIST 100 ve mevduat faizi
- İşsizlik, tüketici ve reel kesim güveni, sanayi üretimi
- Reel GSYİH ve hanehalkı tüketimi
- Yapı ruhsatı ve yapı kullanma izni göstergeleri

Bölgesel 519 kaynak seri şunları kapsar:

- 81 il için toplam, ipotekli, ilk el ve ikinci el konut satışları
- 81 il için konut birim fiyatı serileri
- 81 il için araştırılan ve 78 benzersiz EVDS koduyla temsil edilen konut
  birim kira serileri
- 20 bölgesel KFE ve 20 bölgesel YKKE serisi

Hanehalkı finansmanı katmanı 3 KKM serisi ile 1 hanehalkı mevduat serisini
içerir. İl bazlı panel bu kaynakları FinTürk sektör toplamındaki konut kredisi,
tasarruf mevduatı, altın mevduatı ve nakdi kredi göstergeleriyle birleştirir.

Aktif BIST altın kapanış serisinin kaynak birimi TL/kg'dır. Analizde kullanılan
TL/gram karşılığı, kaynak serinin `0.001` ile çarpıldığı deklaratif bir dönüşüm
olarak ayrıca kaydedilir. Eski seyrek altın serisi yalnız çapraz kontrol olarak
korunur.

Seri seçimi kaynak kataloğundaki kod, ad, birim, frekans ve toplulaştırma
özelliklerine dayanır. Günlük ve haftalık seriler yıllık istek parçalarıyla
çekilir. Her istek ve cevap SHA-256 ile saklanır.

## Veri doğruluğu ilkeleri

1. Kaynak boş değerler tahmine çevrilmez. Sıfır yalnız aynı resmî kaynak
   içindeki kesin bir toplamsal kimlikle kanıtlanırsa, ham null korunarak ayrı
   provenance ile kullanılabilir.
2. Stok, stok değişimi ve yeni kullandırım akımı ayrı kavramlardır.
3. Kümülatif kaynak değer ile türetilmiş aylık akım birlikte saklanır.
4. Çeyreklik veri ara aylara forward fill edilmez.
5. Frekans dönüşümü seri bazlı açık kurala dayanır.
6. Kaynak kapsam farkları zorla eşitlenmez.
7. Birlikte hareket nedensellik kanıtı olarak sunulmaz.
8. Her işlenmiş satırın kaynak dosyası ve hash bilgisi korunur.
9. Risk Merkezi ilk kullanıcı sayısı, TBB parasal kullandırım akımıyla
   birleştirilmez veya onun yerine geçirilmez.
10. Resmî il fiyatı null değerleri değiştirilmez. Ayrı fiyat proxy'si yalnız
    aynı KFE bölgesi ve aynı çeyrekteki resmî il değerlerinin medyanını kullanır.

## Doğrulama özeti

- BDDK aylık: 1.122 kaynak yanıtı, 11.220 tablo-grup kaydı, 339.650 satır, durum `passed`
- BDDK haftalık: 18.018 kaynak sayfası, 1.025.974 ölçüm, 2.230 yapısal boşluk,
  0 çözümlenmemiş boşluk, durum `passed`
- FinTürk: 936.512 ölçüm, 29.564 yapısal boşluk. 1.328 kaynak-null şube
  hücresinin tamamı `SEKTÖR = MEVDUAT + KATILIM + KALKINMA VE YATIRIM`
  kimliğiyle analitik sıfır olarak kanıtlandı, ham değerler değişmedi,
  çözümlenmemiş şube boşluğu 0, durum `passed`
- EVDS ulusal kaynak seriler: 12.482 gözlem, üç paket de `passed`
- EVDS bölgesel konut: 519 seri, 32.214 gözlem, 31.732 dolu değer ve 482
  kaynak null, durum `passed`
- EVDS hanehalkı finansmanı: 4 seri, 312 gözlem, durum `passed`
- TÜİK il konut satışları: 31.590 satır, 25.262 EVDS birebir eşleşmesi,
  10 resmî özdeşlikle doğrulanmış sıfır, 0 değer uyuşmazlığı, durum `passed`
- İl bazlı konut paneli: 1.782 tekil il-çeyrek satırı, 1.620 resmî-kaynak hazır
  satır, 1.782 açık fiyat-proxy hazır satır, 162 proxy audit kaydı, ipotekli
  satışta 0 eksik çeyrek, durum `passed_with_source_gaps`
- TBB: 21 yayımlanmış dönem, durum `passed_with_source_gaps`
- TBB Risk Merkezi: 6 PDF, 390 vintage gözlem, 365 en güncel gözlem, 66 aylık
  eksiksiz hedef panel ve 6 kaynak revizyonu, durum `passed`
- Birleşik katalog: 71 varlık, 55.501 metrik, 3.387 sorgulanabilir metrik, durum `passed`
- DuckDB: 10 şema, 64 tablo veya view, durum `passed`

## Üretim sırası

Mevcut ham dosyalardan çalıştırılabilir temel sıra:

```bash
.venv/bin/python data_pipeline/build_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_all_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_semantic_dataset.py
.venv/bin/python data_pipeline/bddk/build_finturk_dataset.py
.venv/bin/python data_pipeline/bddk/build_weekly_dataset.py
.venv/bin/python data_pipeline/tbb/build_consumer_credit_dataset.py
.venv/bin/python data_pipeline/risk_center/build_monthly_housing_dataset.py
.venv/bin/python data_pipeline/tuik/build_province_housing_sales.py
.venv/bin/python data_pipeline/regional/build_housing_panel.py
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
- `bddk/processed/weekly_all_groups/measurements_long.parquet`
- `bddk/processed/finturk_all_groups_all_cities/measurements_long.parquet`
- `evds/housing_causality_v1/monthly_panel.parquet`
- `evds/housing_causality_controls_v1/monthly_panel.parquet`
- `evds/market_controls_v2/monthly_panel.parquet`
- `evds/regional_housing_v1/observations_long.parquet`
- `evds/household_finance_v1/monthly_panel.parquet`
- `tuik/province_housing_sales_v1/processed/monthly_sales_long.parquet`
- `tuik/province_housing_sales_v1/processed/evds_reconciliation.parquet`
- `regional/processed/province_quarter_housing_panel.parquet`
- `regional/processed/housing_unit_price_proxy_audit.parquet`
- `tbb/processed/housing_credit_quarterly.parquet`
- `risk_center/monthly_housing_v1/processed/housing_credit_monthly.parquet`
- `risk_center/monthly_housing_v1/processed/housing_metric_vintages.parquet`
- `risk_center/monthly_housing_v1/processed/overlap_revision_audit.parquet`
- `evidence/events/events.json`

`lakehouse/analytics.duckdb` verileri kendi içine kopyalar. Sorgu sırasında bu
bilgisayardaki mutlak dosya yollarına ihtiyaç duymaz.
