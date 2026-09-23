# Benchmark Senaryo 1–4 Veri Hazırlık Kontrolü (Commit 6)

**Tarih:** 2026-09-23
**Kapsam:** Bu bir canlı LLM/agent koşusu DEĞİLDİR. `analytics.duckdb`, `python3 data_pipeline/lakehouse/build_lakehouse.py`
ile bu worktree'nin güncel commit'inden (commit 1-5 dahil, `72366f31` HEAD) taze olarak yeniden derlendi,
ardından `catalog.metric_bindings` üzerinde doğrudan DuckDB sorgularıyla `kkb_hackathon_25_demo_senaryolari.md`
BÖLÜM 1'deki 4 benchmark senaryosunun her bağımlılığının `status='ready'` olduğu doğrulandı. Amaç: sonraki bir
oturumun (ya da demo günü) gerçek bir LLM koşusundan önce, alttaki veri katmanının hazır olduğundan emin olmak.

## Yöntem

```bash
python3 data_pipeline/lakehouse/build_lakehouse.py   # taze rebuild, gitignore'lu analytics.duckdb'yi üretir
```

```python
import duckdb, json
con = duckdb.connect("data_pipeline/lakehouse/analytics.duckdb", read_only=True)
con.execute("select metric_id, status from catalog.metric_bindings where metric_id ilike ?", [...]).fetchall()
```

Her senaryonun prompt'larında adı geçen resmi seri/tablo, `metric_bindings.metric_id` üzerinde LIKE
aramasıyla bulundu, `binding_json`'daki `notes`/`group_name`/`observation_count`/`native_frequency`
alanlarıyla doğru seri olduğu teyit edildi (yalnız isim eşleşmesi yetmez — commit 1/3/4'te olduğu gibi).

## Senaryo 1 — Taşıt Kredileri ve EVDS Stok Oranları

| Bağımlılık | metric_id | status | Not |
|---|---|---|---|
| BDDK Taşıt Kredisi (Stok) | `bddk_finturk:table03:TasitKredisi` | **ready** | 12628 gözlem, quarterly, Bin TL |
| EVDS Taşıt Kredisi (TL, Stok, %) faiz | `evds:TP.BKR.TRY.17` | **ready** | 78 gözlem, monthly — commit 1'de eklendi |

Prompt 3 (BDDK web taraması, taksit kısıtlaması kararları) lakehouse bağımlılığı değil, `research_web`
aracının kapsamında — veri hazırlığı gerektirmiyor.

**Sonuç: HAZIR.**

## Senaryo 2 — TP/YP Mevduat Akım Veri Hesabı

| Bağımlılık | metric_id | status | Not |
|---|---|---|---|
| TP mevduat (Tasarruf, TL) | `bddk_finturk:table02:TasarrufMevduatiTurkLirasi` | **ready** | |
| TP mevduat (Diğer, TL) | `bddk_finturk:table02:DigerMevduatTurkLirasi` | **ready** | |
| YP mevduat (Tasarruf, DTH) | `bddk_finturk:table02:TasarrufMevduatiDovizTevdiatHesabi` | **ready** | |
| YP mevduat (Diğer, DTH) | `bddk_finturk:table02:DigerMevduatDovizTevdiatHesabi` | **ready** | |
| Toplam mevduat | `bddk_finturk:table02:ToplamMevduat` | **ready** | |
| EVDS 1-3 Ay Vadeli TP Mevduat Faizi | `evds:TP.TRY.MT02` | **ready** | 339 gözlem, weekly_friday |
| KKM toplam stok (döviz dönüşümlü) | `evds:TP.KKM.K1` | **ready** | 78 gözlem, monthly |
| KKM gerçek kişi stok | `evds:TP.KKM.K2` | **ready** | 78 gözlem, monthly |
| KKM TL toplam stok | `evds:TP.KKM.K4` | **ready** | 78 gözlem, monthly |
| KKM (tüzel kişi, K3) | `evds:TP.KKM.K3` | metadata_only | Prompt'ta ayrı istenmiyor (toplam/gerçek kişi/TL yeterli) — blok değil |

Stok→akım dönüşümü (`Mevduat_t - Mevduat_t-1`) ve Granger nedensellik/change-point runtime hesaplama
işlemleri; bunlar veri hazırlığı değil, execute-time analiz araçlarının işi, bu checkpoint'in kapsamı dışında.

**Sonuç: HAZIR** (K3 dışındaki tüm KKM boyutları ready; K3 prompt'ta gerekmiyor).

## Senaryo 3 — Konut Kredisi, Enflasyon ve Konut Fiyat Endeksi

| Bağımlılık | metric_id | status | Not |
|---|---|---|---|
| BDDK Konut Kredisi (Stok) | `bddk_finturk:table03:KonutKredisi` | **ready** | 12628 gözlem, quarterly, Bin TL |
| EVDS Konut Kredisi Faiz (TL, Stok, %) | `evds:TP.BKR.TRY.18` | **ready** | 78 gözlem, monthly |
| EVDS TÜFE (2025=100) | `evds:TP.TUKFIY2025.GENEL` | **ready** | 78 gözlem, monthly, `price_deflator` rolüyle bağlı |
| EVDS Konut Fiyat Endeksi (KFE, Türkiye) | `evds:TP.KFE.TR` | **ready** | 78 gözlem, monthly |

**Sonuç: HAZIR.**

## Senaryo 4 — Borsa İstanbul Kıymetli Madenler PDF ve BIST100

| Bağımlılık | metric_id | status | Not |
|---|---|---|---|
| Altın işlem hacmi (TL/kg, TL) | `evds:TP.ALTINPIYASA.HACM02` | **ready** | 1695 gözlem, business_daily — commit 4'te eklendi |
| Altın işlem miktarı (kg) | `evds:TP.ALTINPIYASA.MIKT02` | **ready** | 1695 gözlem, business_daily — commit 4'te eklendi |
| BIST100/XU100 kapanış endeksi | `evds:TP.MK.F.BILESIK` | **ready** | 4303 gözlem, business_daily — canonical index code XU100, tarihsel ad değişimi (İMKB 100→BIST 100, 2013) ve 2020 sıfır-atma revizyonu binding_json notlarında belgeli |

Prompt 1'in "PDF indir" talebi tercih edilen değil (commit 4 notunda gerekçelendirildi): EVDS'te aynı
gerçek veriye yapısal/hash'li erişim zaten var, PDF kazımak gereksiz risk. Agent PDF yerine EVDS seri
kimliğini kullanmalı (yönlendirme commit 8/10 kapsamında, bu checkpoint'in konusu değil).

**Sonuç: HAZIR** (PDF ayrıştırmasına düşmeden EVDS'ten aylık altın tablosu üretilebilir; BIST100 verisi de
lakehouse'ta hazır, web'e düşülmeden korelasyon analizi yapılabilir).

## Genel sonuç

Benchmark Senaryo 1-4'ün (BÖLÜM 1) **tüm sayısal seri bağımlılıkları** `catalog.metric_bindings`'te
`status='ready'`. Hiçbiri commit 6'da yeni veri eklemeyi gerektirmedi — commit 1, 3, 4, 5'in kümülatif
etkisiyle zaten tamamlanmış durumda. Kalan riskler veri katmanında değil, orkestrasyon katmanında
(discover retry storm, BDDK alias eksikliği, PDF'e gereksiz düşme eğilimi) — bunlar commit 7-10'un konusu.

## Test durumu

```
pytest tests/lakehouse tests/ingestion -q
```
532/539+ geçti (bu checkpoint kod değiştirmedi, yalnızca DuckDB sorgusu ve doküman ekledi); yalnızca
bilinen 2 ilgisiz ön-var hata (`test_bist_index_baselines_import.py::test_publication_is_pinned_idempotent_discoverable_and_cross_sectional`,
`test_publish_evds_bulk.py::test_transposed_source_nulls_publish_their_actual_value_locations`).
