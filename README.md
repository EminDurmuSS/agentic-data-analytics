# Agentic Data Analytics Hackathon

KKB Agentic Data Analytics Hackathon için hazırlanan veri temeli. Bu sürümde
öncelik agent veya arayüz değil, resmî kaynak verilerinin eksiksiz, izlenebilir
ve yeniden üretilebilir biçimde hazırlanmasıdır.

## Veri aşamasının güncel durumu

| Kaynak | Kapsam | Durum |
| --- | --- | --- |
| BDDK aylık | Ocak 2021-Haziran 2026, 66 ay, sektör toplamı, 17 tablonun tamamı | Tamamlandı ve doğrulandı |
| BDDK haftalık | 286 hafta, sektör toplamı, 9 tablonun tamamı | Tamamlandı ve doğrulandı |
| BDDK FinTürk | 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve `YURT DIŞI` | Tamamlandı ve doğrulandı |
| TCMB EVDS katalog | 676 veri grubu, 52.696 benzersiz seri kaydı | Tamamlandı, metadata kataloğu |
| TCMB EVDS gözlem | Konut kredisi nedensellik analizi için seçilmiş 60 seri | Tamamlandı ve doğrulandı |
| TBB tüketici kredileri | 2021 Mart-2026 Mart, 21 yayımlanmış çeyrek | Tamamlandı; 2026 Haziran raporu kaynakta yok |
| Resmî karar belgeleri | 4 BDDK kararı ve 4 TCMB destek belgesi | Tam metin, çıkarılmış metin ve SHA-256 mevcut |

Ham kaynak değerleri değiştirilmez. Eksik gözlemler sıfır yapılmaz, stok ile
kullandırım akımı karıştırılmaz, kümülatif değer ile türetilen dönemlik akım
ayrı tutulur ve çeyreklik veri ara aylara yapay olarak yayılmaz.

## Ölçek

- BDDK aylık: 1.122 resmî istek, 33.965 kaynak satırı, 133.485 semantik ölçüm
- BDDK haftalık: 2.574 resmî sayfa, 249.070 ham hücre, 147.154 ölçüm
- BDDK FinTürk: 84.484 kaynak satırı, 936.512 ölçüm
- EVDS: 52.696 seri metadata kaydı, 60 seçilmiş seride 10.787 gözlem
- Birleşik katalog: 44 veri varlığı, 55.457 metrik, 2.819 yerel sorgulanabilir metrik
- DuckDB: 7 şema, 32 tablo, mutlak dosya yoluna ihtiyaç duymayan tek dosya

## Klasörler

| Yol | İçerik |
| --- | --- |
| `data_pipeline/bddk/` | Aylık, haftalık ve FinTürk ham verileri ile doğrulanmış çıktılar |
| `data_pipeline/evds/` | Seçilmiş gözlemler, hizalama denetimleri ve kaynak manifestleri |
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
.venv/bin/python data_pipeline/quality/build_cross_source_reconciliation.py
.venv/bin/python data_pipeline/evidence/events/build_events.py
.venv/bin/python data_pipeline/catalog/build_unified_catalog.py
.venv/bin/python data_pipeline/lakehouse/build_lakehouse.py
.venv/bin/python tools/build_data_status_notebook.py
.venv/bin/python data_pipeline/build_file_manifest.py
```

## Önemli kapsam kararı

BDDK aylık ve haftalık bültenlerde bütün tablolar sektör toplamı için alındı.
FinTürk'te ise bütün banka grupları ve bütün iller alındı. EVDS'nin tüm 52.696
serisinin tarihsel gözlemleri indirilmedi. Bunun yerine, yarışmanın konut kredisi
senaryosunda nedensellik ve alternatif açıklamalar için gerekli 60 seri seçildi.
Bu yaklaşım veri kapsamını güçlü tutarken gereksiz veri hacmini ve yanlış seri
seçimi riskini sınırlar.

Agent, API ve frontend sonraki aşamadır. Güncel ayrıntılar için önce
`docs/CURRENT_STATE.md` ve `data_pipeline/README.md` dosyalarını okuyun.
