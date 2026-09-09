# Yerel DuckDB lakehouse

Bu klasör, izlenen kaynak Parquet'lerden kendine yeterli bir DuckDB sorgu
dosyası üretir. Agent araçları ayrıca bu dosyanın değişmez bir kopyasını
yayımlar; yeniden derleme eski oturumların verisini değiştirmez.

## Temiz klonda üretim

Repo kökünde, Python 3.12 ortamını kurduktan sonra:

```bash
python data_pipeline/lakehouse/build_lakehouse.py
python -m unittest discover -s tests -v
```

**Testlerden önce build komutunu çalıştırın.** Üretilen `analytics.duckdb`
GitHub'ın 100 MiB sınırını aştığı için Git'te izlenmez ve `.gitignore`
içindedir. Kaynak Parquet'ler, kaynak manifestleri ve üretim kodu izlenir.
Build mevcut yerel girdilerle çalışır, ağdan yeniden veri indirmez.

## Üretim ve yayın

`build_lakehouse.py`, kaynak doğrulamalarını kontrol eder, verileri geçici
DuckDB dosyasına kopyalar, kaynak ilişkilerini ve metrik sözleşmelerini kurar.
`tools/lakehouse_quality.py` kontrolleri geçtikten sonra dosya kapatılır,
salt okunur yeniden açılıp doğrulanır ve hedef dosyanın yerine atomik olarak
geçirilir. Başarısız doğrulama mevcut sorgu dosyasını değiştirmez.

`source_views.py`, haftalık BDDK sözlüklerini tarih aralıklarıyla bağlar,
eski yerel paketten 15 ek EVDS serisini ekler ve Risk Merkezi kaynak
vintagelarını housing gold tablosuna bağımlı olmadan çözer.

`registry.py`, her katalog metriğine bir durum atar. Fiziksel olarak
çözülebilen metriklerde tablo, değer sütunu, filtreler, boyutlar, birim,
ölçek, frekans ve kaynak referansları kaydedilir. `ready`, `review_required`,
`metadata_only` ve `no_numeric` farklı durumlardır; yalnız metadata bulunması
sayısal hesap izni vermez. İncelenmemiş metrikler için dönüşümler engellenir.

Agent oturumları `tools/lakehouse_store.py` üzerinden SHA-256 ile doğrulanan
snapshot'lara bağlanır. CSV ekleri ve analiz sonuçları ayrı değişmez nesneler
olarak saklanır. Çalışma alanı güncellemeleri kilit ve beklenen sürüm kontrolü
kullanır; önceki analiz dosyaları üzerine yazılmaz.

## Şemalar

| Şema | İçerik |
| --- | --- |
| `catalog` | Veri varlıkları, metrikler, çalıştırılabilir sözleşmeler ve build doğrulaması |
| `evds` | Doğal frekansta gözlemler, eski paket gözlemleri ve hizalanmış paneller |
| `bddk` | Aylık semantik ölçümler, FinTürk ve tarihli haftalık kaynak ilişkileri |
| `tbb` | Çeyreklik tüketici kredisi raporları |
| `risk_center` | Aylık konut kredisi ölçümleri, kaynak vintageları ve son kaynak sürümü |
| `quality` | Kaynaklar arası uzlaştırma |
| `evidence` | Resmî karar ve olay açıklamaları |
| `tuik` | İl konut satışları, kimlikle kanıtlanan sıfırlar ve EVDS uzlaştırması |
| `regional` | İl-çeyrek paneli, resmî değerlerden ayrı tutulan fiyat proxy'leri |
| `analysis` | Aylık ve çeyreklik konut kredisi analiz tabloları |

Mevcut kaynak sürümünde 10 şema ve **70 tablo veya view** vardır. EVDS'de
**599 fiziksel kaynak seri**, bunların 587'sinde sayısal değer bulunur.
52.696 serilik metadata kataloğunun tamamı için tarihsel gözlemler henüz
toplanmadı. Aylık analiz 66, çeyreklik analiz 22, bölgesel panel 1.782 tekil
anahtar içerir. Bunlar sürüm envanteridir; yeni build için `validation.json`,
`catalog.build_validation` ve yayımlanan snapshot manifestini esas alın.

Örnek yönetici sorgusu:

```sql
SELECT month,
       bddk_housing_credit_stock_million_tl,
       TP_KTF12 AS housing_credit_rate_pct,
       housing_credit_real_mom_pct
FROM analysis.housing_credit_monthly
WHERE rate_down_real_stock_not_up_quality_screened
ORDER BY month;
```

Bu SQL yerel veri incelemesi içindir. Agent araçlarının istek dili serbest
SQL veya dosya yolu kabul etmez; metrik kimliği ve izinli işlemler kullanır.
CLI, sürümlü analiz, CSV sözleşmesi, EVDS kuyruğu ve kalan sınırlar için
[uygulama rehberine](../../docs/AGENT_READY_LAKEHOUSE.md) bakın.
