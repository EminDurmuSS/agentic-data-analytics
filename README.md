# Agentic Data Analytics Hackathon

KKB Agentic Data Analytics Hackathon için hazırlanan veri temeli. Bu sürümde
öncelik agent veya arayüz değil, resmî kaynak verilerinin eksiksiz, izlenebilir
ve yeniden üretilebilir biçimde hazırlanmasıdır.

## Veri aşamasının güncel durumu

| Kaynak | Kapsam | Durum |
| --- | --- | --- |
| BDDK aylık | Ocak 2021-Haziran 2026, 66 ay, 17 tablonun tamamı, 10 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK haftalık | 286 hafta, 9 tablonun tamamı, 7 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK FinTürk | 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve `YURT DIŞI` | Tamamlandı ve doğrulandı |
| TCMB EVDS katalog | 676 veri grubu, 52.696 benzersiz seri kaydı | Tamamlandı, metadata kataloğu |
| TCMB EVDS gözlem | 61 ulusal, 441 bölgesel ve 4 hanehalkı finansmanı serisi, ayrıca 1 açıkça türetilmiş seri | Tamamlandı ve doğrulandı |
| İl bazlı konut paneli | 81 il, 22 çeyrek, satış, fiyat, kredi, mevduat, KFE ve YKKE göstergeleri | Tamamlandı, kaynak boşlukları işaretli |
| TBB tüketici kredileri | 2021 Mart-2026 Mart, 21 yayımlanmış çeyrek | Tamamlandı; 8 Eylül 2026 kontrolünde 2026 Haziran raporu kaynakta yok |
| Resmî karar belgeleri | 4 BDDK kararı ve 4 TCMB destek belgesi | Tam metin, çıkarılmış metin ve SHA-256 mevcut |

Ham kaynak değerleri değiştirilmez. Eksik gözlemler sıfır yapılmaz, stok ile
kullandırım akımı karıştırılmaz, kümülatif değer ile türetilen dönemlik akım
ayrı tutulur ve çeyreklik veri ara aylara yapay olarak yayılmaz.

## Ölçek

- BDDK aylık: 1.122 resmî istek, 11.220 tablo-grup kaydı, 339.650 kaynak satırı, 1.334.850 semantik ölçüm
- BDDK haftalık: 18.018 resmî sayfa, 1.736.650 ham hücre, 1.025.974 ölçüm. Kaynaktaki 2.230 boş hücrenin tamamı yapısal `FX uygulanamaz` olarak açıklandı
- BDDK FinTürk: 84.484 kaynak satırı, 936.512 ölçüm. 30.892 kaynak boşluğunun 29.564'ü yapısal, 1.328'i kaynakta raporlanmamış olarak sınıflandırıldı
- EVDS: 52.696 seri metadata kaydı, 506 seçilmiş kaynak seride 42.980 gözlem ve 1 türetilmiş altın serisi
- Bölgesel panel: 81 il x 22 çeyrek, 1.782 tekil satır, 26 analitik metrik
- Birleşik katalog: 61 veri varlığı, 55.484 metrik, 3.292 yerel sorgulanabilir metrik
- DuckDB: 8 şema, 55 tablo, mutlak dosya yoluna ihtiyaç duymayan tek dosya

## Klasörler

| Yol | İçerik |
| --- | --- |
| `data_pipeline/bddk/` | Aylık, haftalık ve FinTürk ham verileri ile doğrulanmış çıktılar |
| `data_pipeline/evds/` | Seçilmiş gözlemler, hizalama denetimleri ve kaynak manifestleri |
| `data_pipeline/regional/` | İl bazlı konut, kredi, mevduat ve fiyat analitik paneli |
| `data_pipeline/tbb/` | Tüketici kredisi raporları, gerçek kullandırım akımı ve bakiye verileri |
| `data_pipeline/catalog/` | Tam EVDS metadata kataloğu ve birleşik veri sözlüğü |
| `data_pipeline/quality/` | Kurumlar arası kapsam ve tutarlılık kontrolleri |
| `data_pipeline/evidence/` | Resmî yöntem ve karar belgeleri |
| `data_pipeline/lakehouse/` | Sorgulanabilir, self-contained DuckDB dosyası |
| `notebooks/` | Doğrulama ve örnek analiz notebook'u |
| `docs/` | Mevcut durum, kaynak ve araştırma notları |
| `tools/` | Resmî kaynak indiricileri |

## Kurulum ve doğrulama

Python 3.12 kullanılır:

```bash
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

İşlenmiş katmanları mevcut ham kaynaklardan yeniden üretmek için:

```bash
.venv/bin/python data_pipeline/bddk/build_monthly_all_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_semantic_dataset.py
.venv/bin/python data_pipeline/bddk/build_finturk_dataset.py
.venv/bin/python data_pipeline/bddk/build_weekly_dataset.py
.venv/bin/python data_pipeline/tbb/build_consumer_credit_dataset.py
.venv/bin/python data_pipeline/regional/build_housing_panel.py
.venv/bin/python data_pipeline/quality/build_cross_source_reconciliation.py
.venv/bin/python data_pipeline/evidence/events/build_events.py
.venv/bin/python data_pipeline/catalog/build_unified_catalog.py
.venv/bin/python data_pipeline/lakehouse/build_lakehouse.py
.venv/bin/python tools/build_data_status_notebook.py
.venv/bin/python data_pipeline/build_file_manifest.py
```

## Önemli kapsam kararı

BDDK aylık bültende bütün tablolar ve 10 resmî banka grubu, haftalık bültende
bütün tablolar ve 7 resmî banka grubu alındı. FinTürk'te de bütün banka
grupları ve bütün iller alındı. EVDS'nin tüm 52.696 serisinin tarihsel
gözlemleri indirilmedi. Bunun yerine 61 ulusal nedensellik ve piyasa serisi,
441 il veya bölge bazlı konut serisi ve KKM ile hanehalkı mevduatını kapsayan
4 seri seçildi. BIST altın kapanış fiyatından `0.001` katsayısıyla TL/kg ->
TL/gram dönüşümü yapılan 1 ek seri de kaynak ve formül bilgisiyle ayrıca
tutulur. Bölgesel katmanda 81 ilin konut satışları, birim fiyatları, bölgesel
KFE ve YKKE değerleri FinTürk kredi ve mevduat göstergeleriyle aynı çeyrek
anahtarında birleştirilir. Çeyreklik değerler ara aylara kopyalanmaz ve kaynak
boşlukları doldurulmaz.
Katalogdaki herhangi bir başka seri `tools/EVDS_Talep_Uzerine_Indirme_Araci.py`
ile adı veya kodu üzerinden bulunup ham istek, ham cevap ve SHA-256 iziyle
indirilebilir. Bu yaklaşım veri kapsamını güçlü tutarken gereksiz veri hacmini
ve yanlış seri seçimi riskini sınırlar.

Agent, API ve frontend sonraki aşamadır. Güncel ayrıntılar için önce
`docs/CURRENT_STATE.md` ve `data_pipeline/README.md` dosyalarını okuyun.
