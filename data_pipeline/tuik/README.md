# TÜİK veri katmanı

Bu dizin TÜİK Veri Portalı'ndan doğrudan alınan destekleyici resmî kaynakları
tutar. İlk veri seti il bazlı konut satışlarıdır.

## İl bazlı konut satışları

Dataflow: `DF_SATIS_SEKLI_DURUMU_ILILCE_V3+V1.0`

Canlı indirme:

```bash
.venv/bin/python tools/TUIK_Il_Konut_Satislari_Indirme_Araci.py
```

İşlenmiş veri ve EVDS uzlaştırması:

```bash
.venv/bin/python data_pipeline/tuik/build_province_housing_sales.py
```

İndirici tam CSV yanıtını gzip ile kaydeder. `response_metadata.json`, HTTP
yanıtının sıkıştırılmadan önceki SHA-256 değerini ve depolanan gzip dosyasının
SHA-256 değerini ayrı ayrı içerir.

İşleyici Ocak 2020-Haziran 2026 döneminde 81 il ve şu beş aylık metriği üretir:

- Toplam konut satışı
- İpotekli konut satışı
- Diğer konut satışı
- İlk el konut satışı
- İkinci el konut satışı

TÜİK CSV'si, ipotekli satışın sıfır olduğu 10 il-ay için doğrudan ipotekli
satış satırı yayımlamamıştır. Bu satırlar yalnız aynı resmî dosyada toplam
satış ile diğer satış birebir eşitse sıfır olarak kullanılabilir. Ham satır
yokluğu `direct_value=null` olarak korunur. Kullanılabilir değer, formül,
`value_origin` ve kaynak SHA-256 ayrı alanlardadır.

Ana çıktılar:

- `province_housing_sales_v1/processed/monthly_sales_long.parquet`
- `province_housing_sales_v1/processed/evds_reconciliation.parquet`
- `province_housing_sales_v1/processed/identity_zero_fallbacks.parquet`
- `province_housing_sales_v1/processed/validation.json`

Ortak 25.262 doğrudan TÜİK ve EVDS gözlemi birebir eşleşmelidir. Herhangi bir
değer farkı işleme adımını başarısız kılar.
