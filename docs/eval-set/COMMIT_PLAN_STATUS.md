# KKB Hackathon — 14 Commit'lik Plan: Durum ve Devam Rehberi

## Proje kapsamı

**Ne:** `agentic_analytics` — KKB Hackathon 2026 için bir agentic data analytics motoru. Kullanıcı doğal dilde finans sorusu soruyor (ör. "Taşıt kredisi tutarlarına faiz oranlarını ekle"); agent (`plan_task → discover → describe → execute` araç zinciri, Qwen/DeepSeek modeli) bunu yerel bir DuckDB lakehouse'unda (`data_pipeline/lakehouse/analytics.duckdb`, ~55.500+ metrik) sorgulayıp analiz tablosu/grafik üretiyor; lakehouse'ta yoksa `research_web` ile resmi kaynaklara (TCMB EVDS, BDDK, TÜİK, KAP, Borsa İstanbul) düşüyor.

**Neden bu plan var:** `docs/eval-set/ASIL SORUN.md` ve `Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/` altındaki 32 gerçek eval koşusunun analizinden çıkan teşhis — sistemin asıl sorunu veri yokluğu değil, "aday bulundu → doğrulanmış hücre → çalıştırılabilir dataset → analiz → grafik" zincirinin sistem genelinde tek bir sözleşmeyle temsil edilmemesi. 14 commit'lik plan bunu iki fazda çözüyor: **Faz 1** (commit 1-6) eksik ama gerçekten gerekli veriyi lakehouse'a ekliyor; **Faz 2** (commit 7-12) mimari/orkestrasyon açıklarını (global overlay, BDDK alias, discover retry storm, source-row contract) kapatıyor; **Faz 3** (13-14) bunu otomatik regresyon testine bağlıyor.

**Repo/deploy:** Branch `main-test-updates`, worktree `agentic-data-analytics-main-test`. Docker: `docker-compose.main-test.yml`, `agent-app` servisi `LAKEHOUSE_DIR`'ı read-only bind-mount eder. **Kritik:** `analytics.duckdb` `.gitignore`'da — her ortam kendi `python3 data_pipeline/lakehouse/build_lakehouse.py`'sini çalıştırmalı, VE container'ı başlatırken `LAKEHOUSE_DIR` mutlaka **bu worktree'yi** (`.../agentic-data-analytics-main-test/data_pipeline/lakehouse`) göstermeli — başka bir klon/worktree'yi gösterirse (ör. `agentic-data-analytics` orijinal klonu) container yeni commit'leri görmez, `METADATA_ONLY` hataları devam eder. Doğru başlatma komutu:
```bash
AGENT_PORT=8871 LAKEHOUSE_DIR="/home/neo/Desktop/GITHUB MYZ21/agentic-data-analytics-main-test/data_pipeline/lakehouse" docker compose \
  --env-file .env -p agentic-main-test -f docker-compose.main-test.yml \
  up -d --build --force-recreate --no-deps agent-app
```

> Bu belge, `main-test-updates` branch'inde yürütülen 14 commit'lik planın anlık durumunu ve
> başka bir ajan/oturumun kaldığı yerden devam edebilmesi için gereken tüm bağlamı içerir.
> Kaynak tartışma: bu conversation'da `ASIL SORUN.md` + 32 eval JSON export'unun analizinden çıktı.

## ⚠️ ACİL: origin/main ayrı ilerlemiş, çakışma riski var

`main-test-updates` ile ortak atası `fec327e8`. 23 Eylül 2026 denetiminde ortak atadan sonra
**origin/main 20 commit**, `main-test-updates` ise 12 commit ileride. Main'deki commit'ler Faz 2
(7-12) ile büyük örtüşme gösteriyor:
- `d7900bd2 fix: resolve METADATA_ONLY errors with on-demand EVDS acquisition and runtime DuckDB overlay` + `7b59ad67`, `6d38c64d` → **Commit 7'nin (overlay)** main'de zaten bir versiyonu var.
- `7dfa2b0f fix(discovery): find BDDK [Tp] and [Yp] series from one query`, `6450ab7b fix(discovery): rank catalog rates labelled "Ağırlıklı ortalama" as rates` → **Commit 8/10 (discovery/alias)** ile örtüşüyor.
- `012e05d6 fix(sources)`, `49dc964b/e3bc30ea/b3b6fa22/08b368ab/e34f4fe0 fix(delivery)` → **Commit 11/12 (source contract/delivery gate)** ile örtüşüyor.
- `3697212a/ffa53226 fix(statistics)`, `8ae964ba fix(lakehouse)` → plan dışı ama ilgili.

**Sonraki oturum/ajan için:** Yeni runtime/delivery kodu yazmadan önce
`git log fec327e8..origin/main --oneline` ile main'deki değişiklikleri incele. Doğru sıra
"yeniden implement etme, `origin/main`'i kontrollü biçimde karşılaştır, sonra hangi Faz 2
maddelerinin hâlâ eksik olduğuna göre işi daralt" olmalı. Henüz merge/rebase yapılmadı;
mevcut branch'te #12 için üretim entegrasyonu da yok.

## Repo / branch bilgisi

- Repo: `https://github.com/EminDurmuSS/agentic-data-analytics`
- Branch: `main-test-updates`
- Bu worktree: `/home/neo/Desktop/GITHUB MYZ21/agentic-data-analytics-main-test`
- Şu anki HEAD: `1b7a1a6d` — `fix(runtime): enforce source-row contract gate before analysis/chart delivery`
- `origin/main-test-updates`: **`1b7a1a6d`** — çalışma ağacı temiz ve remote ile eşit.

## Durum tablosu

| # | Commit mesajı | Durum | Not |
|---|---|---|---|
| 1 | `data(evds): add verified demo core rate observations` | ✅ TAMAMLANDI, merge edildi, **push edildi** (`6db768bd`) | TP.BKR.TRY.17, TP.TRY.MT02, TP.BKR.TRY.1 — 78/78/339 gözlem, gerçek EVDS verisi |
| 2 | `data(evds): add validated macro join pack` (TÜFE+KFE) | ✅ GEREKSİZ BULUNDU, atlandı | `TP.TUKFIY2025.GENEL` (TÜFE) ve `TP.KFE.TR` (KFE) zaten `ready` durumda, `housing_causality_v1` manifestinde `price_deflator` rolüyle önceden bağlı. Kod değişikliği gerekmiyor. |
| 3 | `data(macro): add extended reference series for dış ticaret & piyasa ailesi` | ✅ TAMAMLANDI, commit atıldı (`9d1f3d4f`), **push edilmedi** | 10 seri eklendi, detay aşağıda |
| 4 | `data(borsa): import official precious-metals monthly panel` | ✅ TAMAMLANDI, commit atılacak | EVDS `TP.ALTINPIYASA.HACM02`/`MIKT02` — detay aşağıda |
| 5 | `data(reference): add minimum wage decision lookup table` | ✅ TAMAMLANDI, commit atılacak | 8 karar dönemi (2021-2026), EVDS pattern'i bilinçli uygulanmadı — detay aşağıda |
| 6 | `docs(eval): rerun benchmark 1-4 checkpoint` | ✅ TAMAMLANDI | Tüm 4 senaryonun sayısal bağımlılıkları `ready`; `docs/eval-set/benchmark-1-4-readiness-checkpoint.md` |
| 7 | `feat(runtime): replace global on-demand overlay with workspace-scoped versioned acquisition store` | ✅ TAMAMLANDI | `tools/EVDS_Talep_Uzerine_Indirme_Araci.py` artık `--workspace` zorunlu, çıktı `on_demand/<workspace>/<hash>/`, fcntl kilidi; yeni `tools/promote_on_demand_series.py` terfi yolu |
| 8 | `fix(lakehouse): add executable aliases for BDDK financial metrics` | ✅ TAMAMLANDI | `_TERM_ALIASES` (`service.py`) genişletildi + `tests/lakehouse/test_bddk_alias_coverage.py` gerçek katalog karşı doğrulama |
| 9 | `feat(lakehouse): classify ready/acquirable/near_match_available/web_required/unavailable` | ✅ TAMAMLANDI | Yeni `agentic_analytics/lakehouse/readiness.py::classify_query_readiness()` + `tests/lakehouse/test_readiness_classification.py` (XBANK→unavailable doğrulandı) |
| 10 | `fix(discover): cap lexical reformulation retries and resolve from already-ranked near-matches` | ✅ TAMAMLANDI | `agentic_analytics/agent/runtime.py`: `_discover_target_key`/`_discover_retry_cap`/`_record_discover_evidence`, 2 çağrı/hedef sınırı |
| 11 | `feat(sources): persist verified source-row contracts` | ✅ TAMAMLANDI | `1b7a1a6d` içinde `source_contract.py` ve 19 unit test eklendi. |
| 12 | `fix(runtime): enforce source-row contract gate before analysis/chart delivery` | ⛔ UYGULANMADI | `1b7a1a6d` commit mesajına rağmen `runtime.py`, `delivery.py` veya üretim ingestion çağrı noktası değişmedi; contract henüz hiç çağrılmıyor. |
| 13 | `test(eval): convert scenario exports into automated regression harness` | ⚠️ KISMİ TAMAMLANDI | Aynı `1b7a1a6d` içine 32 dışa aktarım için snapshot tabanlı 39 test eklendi. Bu canlı agent regresyonu ya da contract-completeness testi değildir. |
| 14 | `docs(eval): full rerun of 25+10 set, before/after report` | ⬜ Başlamadı | |

## Commit 3 — sonuç (tamamlandı)

Commit SHA: `9d1f3d4f`. **9 yeni seri indirildi + 1 seri registry-only düzeltmeyle `ready` hâline getirildi:**

1. `TP.ODEAYRSUNUM6.Q4` — Dış Ticaret Dengesi, aylık, 78 gözlem, milyon USD, aralık -12.581..-50 (her zaman açık, gerçek Türkiye ticaret tarihiyle uyumlu).
2. `TP.AB.N07` (Brüt Döviz Rezervleri) + `TP.AB.N06` (Net Uluslararası Rezervler), haftalık-Cuma, 339'ar gözlem. N06, 2022-2023 negatif net rezerv döneminde gerçekten negatife düşüyor (-118.2M..4.21B) — gerçek veriyle uyumlu.
3. `TP.PBD.H01/H09/H17` — M1/M2/M3 para arzı, aylık, 78'er gözlem, bin TL, hepsi monoton artan.
4. `TP.TARIMUFE01` — Tarım-ÜFE genel endeks, aylık, 78 gözlem, 2020=100, aralık 91.55-1208.99.
5. `TP.MK.KUL.YTL` — Gram Altın (Külçe Altın Satış Fiyatı TL/Gr) — **yeni indirme gerekmedi**, `housing_causality_controls_v1` altında zaten 77 gerçek gözlem vardı ama EVDS unit alanı boş olduğu için `review_required` durumundaydı; yalnız `registry.py`'de override ile `ready`'e çevrildi.
6. `TP.BTO4` (Karşılıksız Çek Tutarı, Bin TL) + `TP.BTO3` (Karşılıksız Çek Adedi), aylık, 78'er gözlem.

**Atlananlar (commit mesajında belgelendi):**
- **Protestolu Senet Sayısı** — 52.696 satırlık EVDS kataloğunda gerçekten yok ("Protesto"/"Senet" için 0 eşleşme), uydurulmadı.
- **XBANK** — EVDS kataloğunda yok, ama `tools/import_bist_xbank.py` zaten çalıştırılmış ve **ayrı bir commit'te** (`17c7f865`, `docs/validation/w007-xbank-shared-2026-09-21.md`) zaten landed — XU100'ün kullandığı SharedLakehouse operator-only promotion mekanizmasıyla. Tekrar çalıştırmak mükerrer iş olurdu, dokunulmadı.

**Test sonucu:** `pytest tests/lakehouse tests/ingestion -q` → 532 geçti, yalnız aynı 2 öncesinden var/ilgisiz hata (`test_bist_index_baselines_import.py`, `test_publish_evds_bulk.py`). Yeni regresyon yok.

**⚠️ PUSH EDİLMEDİ.** Yeni bir ajan/oturum devam etmeden önce:
```bash
cd "/home/neo/Desktop/GITHUB MYZ21/agentic-data-analytics-main-test"
git log --oneline -3   # 9d1f3d4f görünmeli
git push origin main-test-updates
```

## Kritik teknik bulgular (her commit için geçerli)

### EVDS için API anahtarı GEREKMİYOR
`tools/EVDS_Manifest_Indirme_Araci.py`, EVDS web sitesinin herkese açık, anahtarsız uç noktasını kullanıyor:
`https://evds3.tcmb.gov.tr/igmevdsms-dis/fe` (POST, JSON body, browser-benzeri header'lar). Test edildi,
gerçek veri döndü. `.env` dosyasında `EVDS_API_KEY` yok ve gerekmiyor.

### Veri ekleme deseni (Commit 1'de kuruldu, sonraki data commit'leri bunu taklit etmeli)
1. `data_pipeline/evds/manifests/<dataset_id>.json` — `{dataset_id, description, start_date, end_date, series:[{series_code, role, reason}]}`
2. (Opsiyonel) `<dataset_id>_alignment_v1.json` — hizalama politikası (`temporal_semantics`, `subperiod_aggregation`)
3. `python3 tools/EVDS_Manifest_Indirme_Araci.py` ile indir → `data_pipeline/evds/<dataset_id>/observations_long.parquet` + ham request/response + SHA-256
4. `data_pipeline/evds/build_aligned_panels.py` ile `monthly_panel.parquet`, `quarterly_panel.parquet`, `*_alignment_audit.parquet`, `coverage_gaps.parquet` üret (bu dosyalar `build_lakehouse.py`'nin ihtiyacı)
5. `data_pipeline/catalog/build_unified_catalog.py` → `dataset_specs` listesine ekle (~satır 348)
6. `data_pipeline/lakehouse/build_lakehouse.py` → per-dataset parquet tablo döngüsüne ekle (~satır 493, `demo_core_rates_v1` örneğini takip et). **BDDK-join merge döngülerine (satır ~128, ~252) EKLEME** — bu tekil oran/seviye serileri için gerekli değil, gereksiz risk.
7. `agentic_analytics/lakehouse/registry.py` → `build_bindings()` içinde EVDS kataloğunun `unit` alanı gerçek birim değilse (örn. "Ağırlıklı ortalama") unit/kind override ekle (percent/rate gibi).
8. `python3 data_pipeline/lakehouse/build_lakehouse.py` ile yerel `analytics.duckdb`'yi yeniden derle (bu dosya `.gitignore`'da, commit'e girmez — her ortam kendi rebuild eder).
9. Doğrula: `catalog.metric_bindings` içinde yeni seri `status='ready'`, doğru `observation_count`.
10. Test ekle (`tests/ingestion/test_evds_demo_core_rates.py`'yi örnek al), `pytest tests/lakehouse tests/ingestion -q` çalıştır.
11. Tek commit, conventional commit formatı, gerçek gözlenen değer aralıklarını commit mesajına yaz (denetlenebilirlik için).

### Zaten lakehouse'ta olan seriler (tekrar eklemeyin!)
- SÜE (Sanayi Üretim Endeksi) — `TP.TSANAYMT2021.Y1`
- Tüketici Güven Endeksi — zaten bağlı
- TÜFE genel endeks — `TP.TUKFIY2025.GENEL` (price_deflator rolüyle)
- KFE (Konut Fiyat Endeksi) — `TP.KFE.TR` + bölgesel varyantlar
- USD/TRY alış kuru, XU100/BIST100 — zaten bağlı

Yeni bir seri eklemeden önce MUTLAKA şu sorguyla kontrol edin:
```python
import duckdb, json
con = duckdb.connect("data_pipeline/lakehouse/analytics.duckdb", read_only=True)
con.execute("SELECT metric_id, status FROM catalog.metric_bindings WHERE metric_id ILIKE '%ARANAN_TERIM%' AND status='ready'").fetchall()
```

### Bilinen sorun: Agent tool `isolation: "worktree"` ile stale worktree
Commit 3'te **4 kez** art arda, izole worktree ajanları `fec327e8`'den (commit 1'den ÖNCEsinden)
başlatıldı — `6db768bd` ata olarak görünmedi, `merge-base --is-ancestor` false döndü. Push sonrası bile
tekrarladı. Kök neden netleşmedi (muhtemelen worktree provisioning ile base branch güncellemesi arasında
bir cache/timing sorunu). **Çözüm:** `isolation` parametresini hiç vermeden (agent bu worktree'de
doğrudan çalışır) ajan başlatmak — bu şekilde 5. denemede çalıştı. Yeni data commit'leri için de
`isolation: "worktree"` KULLANMAYIN, doğrudan bu dizinde çalıştırın.

## Commit 4 — sonuç (tamamlandı)

Borsa İstanbul Kıymetli Madenler ve Kıymetli Taşlar Piyasası'nın (Altın Piyasası) resmi işlem hacmi/miktarı
**PDF/web'den değil, EVDS'ten** bulundu — Borsa İstanbul'un kendi PDF arşivini kazımaya gerek kalmadı:

- `TP.ALTINPIYASA.HACM02` — Altın İşlem Hacmi (TL/kg segmenti, TL), iş günü, 1695 gözlem (2020-01-01..2026-06-30).
- `TP.ALTINPIYASA.MIKT02` — Altın İşlem Miktarı (TL/kg segmenti, kg), iş günü, 1695 gözlem, aynı aralık.

Her iki seri de önceden `metadata_only` idi (EVDS kataloğunda kayıtlı ama hiç indirilmemiş); bu commit'te
gerçek EVDS genel-erişim uç noktasından (`https://evds3.tcmb.gov.tr/igmevdsms-dis/fe`) indirildi.
2023-2025 aylık toplamlar (36 ay, hepsi dolu, sıfır boşluk):
- İşlem hacmi (TL): ~10.8 milyar (2024-06) ile ~75.2 milyar (2025-09) arası.
- İşlem miktarı (kg): ~2508 kg (2025-07) ile ~39615 kg (2023-01) arası.

**Teknik bulgu — `build_aligned_panels.py`'de gerçek bir sınırlama bulundu ve minimal şekilde genişletildi:**
Mevcut `coverage_audit()`/`aggregate_value()` mantığı, `flow`+`sum` politikasını yalnızca AYLIK/ÜÇ AYLIK
native frekanslı serilerde destekliyordu; İŞ GÜNÜ (business-day) gibi yüksek frekanslı seriler için
tamlık kanıtlanamadığından (genel bir borsa takvimi verilmediği sürece) sum her zaman
`unavailable_incomplete_sum` dönüyordu — hafta sonu/tatil kaynaklı yapısal null'lar bile "kanıtlanmamış
boşluk" sayılıyordu. Bunu çözmek için indiricinin (EVDS_Manifest_Indirme_Araci.py) zaten hesapladığı
satır-bazlı sınıflandırma yeniden kullanıldı: her null gözlem `missing_kind` ile
(`before_series_start`/`calendar_non_observation`/gerçek çözümlenmemiş boşluk) etiketleniyor ve
`is_unresolved_missing` bayrağı taşıyor. `coverage_audit()`'e yeni bir dal eklendi: İŞ GÜNÜ gibi yüksek
frekanslı + `sum` politikalı bir ay, yalnızca o ay içinde **sıfır çözümlenmemiş boşluk** varsa ve en az
bir gözlem mevcutsa "complete" sayılıyor. `align_series()`'te de aynı dal için toplam, `aggregate_value`'nun
genel `values.isna().any()` katı kontrolünü atlayıp yalnızca dolu değerleri topluyor (tamlık zaten
coverage_audit'te kanıtlanmış olduğu için). Bu değişiklik **yalnızca** yüksek-frekans + `sum` kombinasyonunu
etkiliyor; mevcut hiçbir manifest (`extended_reference`, `housing_causality`, `regional_housing`,
`housing_causality_controls`) bu kombinasyonu kullanmıyordu (hepsi AYLIK/ÜÇ AYLIK native), bu yüzden
mevcut veri setlerinin çıktısı değişmedi — `pytest tests/ingestion/test_evds_alignment.py` (11 test) ve
tüm mevcut EVDS ingestion testleri değişmeden geçti.

**Registry.py:** `TP.ALTINPIYASA.HACM02` → `unit="TRY", kind="flow"`, `TP.ALTINPIYASA.MIKT02` →
`unit="kg", kind="flow"` açık override'ları eklendi (EVDS grup-seviyesi `unit` alanı "TL/kg, USD/ons,
Euro/ons, TL/gr" gibi anlamsız bir liste döndürüyordu, KAP02/KAP05/TP.MK.KUL.YTL'de olduğu gibi).
`known_unit` allowlist'ine `"kg"` eklendi (fiziksel miktar birimi, gerçek ve genellenebilir).

**Atlanmayan ama tercih edilmeyen:** BIST'in kendi PDF arşivi
(`https://www.borsaistanbul.com/files/2023-yilina-ait-piyasa-verileri.pdf` tarzı) kontrol edilmedi çünkü
EVDS'te aynı gerçek veriye zaten yapısal, denetlenebilir, hash'li erişim vardı — PDF kazımak gereksiz risk
olurdu (görev talimatındaki "EVDS varsa PDF'e tercih edilir" kuralına uygun).

**Test sonucu:** `pytest tests/lakehouse tests/ingestion -q` → 539 geçti, yalnız aynı 2 öncesinden var/ilgisiz
hata. 4 tane ek test 611→613 / 599→601 gibi hardcoded EVDS seri sayısı beklentisini güncellemek gerektirdi
(commit 1/3'te de aynı desen); `data_pipeline/FILE_SHA256.json` yeniden üretildi
(`python3 data_pipeline/build_file_manifest.py`). Yeni `tests/ingestion/test_evds_precious_metals_market.py`
eklendi (7 test, `demo_core_rates`/`extended_reference` testleriyle aynı desen).

## Commit 5 — sonuç (tamamlandı)

`data_pipeline/catalog/evds_series_catalog.parquet` (52.696 satır) "Asgari" için arandı: yalnızca 2
eşleşme var (`TP.KB.GEL0131`/`TP.KB.GEL0132`, "Yerel/Küresel Asgari Tamamlayıcı Kurumlar Vergisi" —
OECD küresel asgari kurumlar vergisi bütçe kalemleri, asgari ücretle ilgisiz). Asgari ücretin kendisi
EVDS'te yok — beklenen, çünkü ÇSGB/Resmi Gazete kaynaklı, TCMB değil. Bu yüzden görev talimatının
söylediği gibi EVDS manifest deseni (Commit 1/3/4) **bilinçli olarak uygulanmadı**.

**Eklenenler:**
- `data_pipeline/evidence/reference/minimum_wage_decisions.json` — 8 karar dönemi, 2021-01-01'den
  bugüne (2026, açık uçlu) kesintisiz kapsıyor:
  - AUTK_2021: 2021-01-01–12-31, net 2.825,90 / brüt 3.577,50 TL, RG 30.12.2020 No 31350
  - AUTK_2022_H1: 2022-01-01–06-30, net 4.253,40 / brüt 5.004,00 TL, RG 25.12.2021 No 31700
  - AUTK_2022_H2 (ara zam): 2022-07-01–12-31, net 5.500,35 / brüt 6.471,00 TL, RG 01.07.2022 No 31883 Mükerrer
  - AUTK_2023_H1: 2023-01-01–06-30, net 8.506,80 / brüt 10.008,00 TL, RG 29.12.2022 No 32058 (karar 2022/2, 22.12.2022)
  - AUTK_2023_H2 (ara zam): 2023-07-01–12-31, net 11.402,32 / brüt 13.414,50 TL, RG 24.06.2023 No 32231
  - AUTK_2024: 2024-01-01–12-31, net 17.002,12 / brüt 20.002,50 TL, RG 30.12.2023 No 32415
  - AUTK_2025: 2025-01-01–12-31, net 22.104,67 / brüt 26.005,50 TL, RG 27.12.2024 No 32765 (doğrudan
    resmigazete.gov.tr PDF'i bulundu ve kaynak olarak kullanıldı)
  - AUTK_2026: 2026-01-01– (güncel, açık), net 28.075,50 / brüt 33.030,00 TL, RG 26.12.2025 No 33119
  - Her satırın `gross_daily_try` alanı `gross_monthly_try/30`'a tam eşit (bağımsız iç tutarlılık kontrolü).
- `data_pipeline/evidence/reference/minimum_wage_notes.md` — kaynak metodolojisi, EVDS kontrolü ve
  agent tool discovery durumunun dürüst raporu.
- `agentic_analytics/lakehouse/reference_data.py` — `load_minimum_wage_decisions()` /
  `minimum_wage_for_date()`: küçük, test edilmiş, gerçek bir Python yükleyici.
- `tests/ingestion/test_minimum_wage_reference.py` — 9 test: alan tamlığı, tarih aralığı sürekliliği
  (boşluk/çakışma yok), net<brüt ve monoton artış, `gross_daily` tutarlılığı, `minimum_wage_for_date`
  çözümlemesi, bozuk veri üzerinde sessiz yükleme yerine hata.

**Veri doğruluğu (provenance) — dürüstlük kontrolü:** Sekiz kararın tamamı bu görev sırasında
`WebSearch` aracıyla **canlı doğrulandı** (23 Eylül 2026), eğitim verisinden sessizce hatırlanmadı.
2025 kaydı doğrudan resmigazete.gov.tr'nin arşivlenmiş PDF'ine karşı doğrulandı. Diğerleri ÇSGB'nin
kendi duyuru sayfası veya en az iki bağımsız profesyonel kaynağın (PwC, Grant Thornton, Andersen,
TÜRK-İŞ, KPMG, Lexpera Resmi Gazete tam metin portalı) aynı tarih/sayı ve tutarları teyit ettiği
aramalarla doğrulandı. 2024 kaydının net tutarındaki kuruş hassasiyeti (17.002,12 TL), ilk arama
yalnızca yuvarlak basın rakamı (17.002 TL) döndürdüğü için ayrı bir takip aramasıyla doğrulandı — bu,
"emin olmadığım rakamı ekleme" ilkesinin nasıl uygulandığına somut bir örnek. Her satırda
`verified_live: true`, `source_url` ve `source_note` alanları var; hiçbir rakam uydurulmadı.

**Agent tool discovery — dürüst rapor:** `agentic_analytics/lakehouse/service.py`'nin
`discover`/`describe`/`execute` araçları **yalnızca** `catalog.metric_bindings` üzerinden çalışır
(`registry.py::build_bindings()`, ki bu yalnızca `catalog.metrics`'ten, yani EVDS/BDDK/TUIK/TBB kaynak
adaptörlerinden beslenir). `data_pipeline/evidence/` klasörü — bu yeni dosya dahil, ama aynı zamanda
**önceden var olan** `events.json`/`events/` de dahil — bu üç araçtan erişilebilir değil. `events.json`
`build_unified_catalog.py::event_assets()` ile yalnızca `catalog.assets`'e (bir doğrulama/denetim
tablosu, sorgulanabilir bir metrik değil) kaydediliyor ve `build_lakehouse.py`'de tek bir spesifik
analiz adımına pandas join'iyle gömülüyor — yani mevcut desen de zaten "genel amaçlı agent-keşfedilebilir
katalog" değil. `agentic_analytics/agent/tools/reference_catalogues.py` da incelendi: o mekanizma tüm
bir lakehouse snapshot'ını (`catalog.metric_bindings` tabanlı) bir workspace'e iğnelemek için, tek bir
küçük lookup tablosu eklemek için değil.

Bu yüzden bu commit'te **tam wiring yapılmadı** (görev talimatının izin verdiği "derin runtime
değişikliği kapsam dışı" yoluna gidildi): veri + gerçek, test edilmiş bir Python yükleyici eklendi;
`discover`/`describe`/`execute` üzerinden sorgulanabilir hale getirmek ya `registry.py`'nin zaman-serisi
varsayımlarını sonlu bir karar listesine zorlamayı (yanlış temsil olur) ya da servise yeni bir
"reference lookup" tool tipi eklemeyi gerektirir — ikisi de ayrı bir mimari karar, bu commit'in kapsamı
dışında bırakıldı ve `minimum_wage_notes.md`'de takip işi olarak belgelendi.

**Test sonucu:** `tests/ingestion/test_minimum_wage_reference.py` → 9/9 geçti.
`pytest tests/lakehouse tests/ingestion -q` → bkz. commit mesajı (yalnız aynı 2 öncesinden var/ilgisiz
hata, yeni regresyon yok — bu commit hiçbir mevcut dosyayı değiştirmedi, yalnızca ekledi).

## Commit 7 — sonuç (tamamlandı)

**Bulgu:** `agentic_analytics/agent/tools/` altında `EVDS_Talep_Uzerine_Indirme_Araci.py`'ye giden HİÇBİR
tipli araç yoktu (grep doğrulandı) — canlı bir eval koşusunda bu script'in çalıştırılması, tipli
`discover/describe/execute` sözleşmesinin dışında, agent'a genel bir kabuk/komut yeteneği üzerinden
gerçekleşmiş olmalı. Script kendisi tamamen global paylaşımlıydı: çıktı her zaman
`data_pipeline/evds/on_demand/<hash>/` altına yazılıyordu (workspace kavramı yok), kilitsiz (iki eşzamanlı
çağrı aynı dizine yazabilirdi), ve doğrudan kalıcı `data_pipeline/evds/manifests/` desenine (commit 1)
hiçbir yolla bağlanmıyordu.

**Yapılan değişiklik (minimal, hedefli):**
1. `tools/EVDS_Talep_Uzerine_Indirme_Araci.py`: `--workspace` argümanı eklendi (bir `--output` açıkça
   verilmediği sürece zorunlu). Çıktı artık `DEFAULT_OUTPUT_ROOT/<workspace_id>/<dataset_hash>/` altında.
   `validate_workspace_id()` path-traversal/geçersiz karakterlere karşı regex ile kısıtlıyor.
   `workspace_acquisition_lock()` — `agentic_analytics/lakehouse/store.py::LakehouseStore._lock`'un aynı
   `fcntl.flock` desenini tekrar kullanarak `(workspace_id, dataset_hash)` çiftine özel bir kilit dosyası
   ediniyor; farklı workspace'ler veya farklı seri/tarih seçimleri birbirini bloklamıyor.
2. Yeni `tools/promote_on_demand_series.py` — doğrulanmış bir on-demand acquisition'ı (generated_manifest.json
   + observations_long.parquet + validation.json'un tam varlığı zorunlu) commit 1 şeklinde
   (`{dataset_id, description, start_date, end_date, series:[{series_code, role, reason}]}`) kalıcı bir
   manifest'e dönüştürüyor; her seri için gerçek (placeholder olmayan) role/reason zorunlu, `provenance`
   alanında `review_required: true` ve hangi adımların (katalog/build_lakehouse/registry wiring) hâlâ manuel
   olduğu açıkça belirtiliyor — commit 5'teki "yalnız yapısal terfi, sessizce wiring yapma" dürüstlük
   deseniyle tutarlı. `--force` olmadan var olan bir terfi dosyasının üzerine yazmıyor.
3. `.gitignore`'a `data_pipeline/evds/on_demand/` eklendi (analytics.duckdb gibi scratch/reproducible veri,
   commit'e girmemeli).
4. Yeni test dosyası `tests/ingestion/test_evds_on_demand_workspace_scoping.py` (18 test): workspace id
   doğrulama, iki workspace'in ayrı depolama kökü alması, path-escape reddi, kilidin aynı
   `(workspace, hash)` çiftini gerçekten serialize ettiği (thread ile kanıtlandı), farklı çiftlerin
   birbirini bloklamadığı, terfi aracının eksik/kısmi acquisition'ı reddettiği, rol eksikse reddettiği,
   on-demand kimliğini yeniden kullanmayı reddettiği, ve `--force` olmadan üzerine yazmadığı.

**Kapsam dışı bırakılan (bilinçli):** Terfi sonrası katalog/`build_lakehouse.py`/`registry.py` wiring'i
otomatikleştirilmedi — bu, commit 1-5'in kurduğu "her yeni seri insan gözden geçirmesiyle wiring'e girer"
deseniyle kasıtlı olarak tutarlı; otomatik wiring, review adımını atlayarak doğrulanmamış on-demand veriyi
sessizce kalıcı lakehouse'a sokma riski taşırdı.

**Test sonucu:** `pytest tests/ingestion/test_evds_on_demand_workspace_scoping.py tests/ingestion/test_evds_on_demand.py -q`
→ 31/31 geçti. Tam paket (`pytest tests/lakehouse tests/ingestion -q`) yalnızca aynı 2 bilinen ilgisiz
hata dışında geçti (aşağıda commit mesajında detay).

## Commit 8 — sonuç (tamamlandı)

**Bulgu:** `agentic_analytics/lakehouse/service.py::_search_terms()` içinde ZATEN kelime bazlı bir
alias/crosswalk sözlüğü vardı (`npl`→`takip`, `mortgage`→`konut`, `stock`→`bakiye` gibi) — bu `discover`'ın
gerçek entegrasyon noktasıydı, sıfırdan yeni bir mimari eklemeye gerek yoktu. Eksik olan: taşıt/vehicle,
ticari/commercial, ihracat/export, KOBİ/sme, kredi kartı/card, sektör/sector, vade/maturity, döviz/currency,
kurum/institution, kümülatif/cumulative, akım/flow gibi terimlerin İngilizce/İngilizce-kısaltma biçimleri
sözlükte yoktu (Türkçe biçimleri zaten `_fold()` normalizasyonuyla başlık metniyle otomatik eşleşiyordu —
"taşıt" → "tasit" → BDDK başlığı "Taşıt Kredisi" ile zaten eşleşir; eksik olan yalnızca yabancı dil/varyant
girişleriydi).

**Yapılan:** Sözlük fonksiyon içinden modül seviyesine (`_TERM_ALIASES`) taşındı (tek, incelenebilir,
test edilebilir sabit) ve yukarıdaki terimler eklendi (aynı satırda). Ayrıca önceden var olan zararsız
`"satis": "satis"` (kendine eşleme, no-op) girdisi temizlendi.

**Test:** Yeni `tests/lakehouse/test_bddk_alias_coverage.py`:
- Şema doğrulama: her alias anahtarı/değeri sınırlı, küçük harf, alfasayısal bir kimlik; hiçbir terim
  kendine eşlenmiyor; commit'in belirttiği tüm hedef token'lar (`tasit, ticari, ihracat, kobi, kart, takip,
  sektor, vade, doviz, kurum, kumulatif, akim, oran`) sözlükte gerçekten var.
- Coverage (gerçek `analytics.duckdb` karşısında, yoksa `skip`): her yeni alias terimi için gerçek bir
  `LakehouseService.discover()` çağrısı `status=ready` bir BDDK metriği döndürüyor mu doğrulanıyor
  (`vehicle loan stock`→`bddk_finturk:table03:TasitKredisi`, `total sme loans`→table06 KOBİ metrikleri,
  `credit card`→Kredi Kartları, vb.) — ölü/isabetsiz bir alias sessizce kalmıyor.

**Kapsam dışı:** `ask_user`/orkestrasyon akışının kendisi (discover retry sınırlama, near-match'ten karar
verme) bu commit'in değil, commit 10'un konusu; commit 8 yalnızca lexical eşleşme kapsamını genişletiyor.

**Test sonucu:** `pytest tests/lakehouse/test_bddk_alias_coverage.py -q` → 6 passed, 9 subtests passed.
Tam paket (`pytest tests/lakehouse tests/ingestion -q`) → bkz. commit mesajı.

## Commit 9 — sonuç (tamamlandı)

Yeni `agentic_analytics/lakehouse/readiness.py::classify_query_readiness(service, query)` — tek bir
`discover()` çağrısından 5 durumlu deterministik sınıflandırma üretir:
- **ready**: tam eşleşen `status=ready` metrik var.
- **acquirable**: tam eşleşen ama yalnız `status=metadata_only` (bilinen EVDS serisi, henüz indirilmemiş —
  commit 7'nin `tools/EVDS_Talep_Uzerine_Indirme_Araci.py` + `tools/promote_on_demand_series.py` yoluna
  yönlendiriyor).
- **near_match_available** (BÖLÜM 4'te yoktu, bu commit'te eklenen yeni durum): tam eşleşme yok ama
  `discover`'ın `no_confident_match` ile döndürdüğü semantik near-match adayları var, YA DA tam eşleşen
  adaylar var ama hepsi `review_required`/`no_numeric` (gerçek kanıt var, henüz teslim edilebilir değil).
- **web_required**: 0 tam + 0 near eşleşme, VE terim `KNOWN_EXTERNAL_TOPICS`'te (DİBS, KAP, MKK, SPK, ODMD,
  Protestolu Senet, KGF, LCR — `COMMIT_PLAN_STATUS.md`'nin "Kapsam sınırı" bölümünden ve BÖLÜM 4'ün kendi
  "bilinçli web" etiketli satırlarından alınan, gerçek/doğrulanmış bir dış kaynak listesi).
- **unavailable**: 0 tam + 0 near eşleşme VE bilinen dış kaynak listesinde değil — **tek bir `discover`
  çağrısıyla, hiçbir dahili tekrar olmadan** döner. Kabul kriteri: BÖLÜM 4'ün "bilinen risk" olarak
  işaretlediği XBANK sorgusu (bu oturumda gerçek bir `DECISION_BUDGET_EXCEEDED` çökmesine yol açtığı
  görülen sorgu) artık deterministik olarak `unavailable` döner, tekrar tekrar aranmaz.

**Önemli bulgu:** BÖLÜM 4 yazıldığından beri commit 1/3/4'ün eklediği veriler (M1/M2/M3 para arzı,
Karşılıksız Çek TP.BTO3/4, Dış Ticaret Dengesi, rezervler) artık gerçekten `ready` — BÖLÜM 4'te "0 eşleşme"
olarak işaretli bu satırlar artık `classify_query_readiness` ile test edilince `ready` çıkıyor. Bu,
sınıflandırıcının donmuş bir BÖLÜM 4 kopyası değil, GERÇEK katalog karşısında çalışan canlı bir fonksiyon
olduğunun kanıtı — testler donmuş beklentiler yerine güncel duruma göre yazıldı.

**Test:** `tests/lakehouse/test_readiness_classification.py` — sahte `discover()` ile durum makinesi testleri
(8 test, DB gerektirmez) + gerçek `analytics.duckdb` karşısında BÖLÜM 4'ün bir alt kümesini yeniden üreten
11 test (Senaryo 1/3/6/7/19 → ready, Senaryo 10 XBANK → unavailable, Senaryo 12/18/24/28/32 → web_required).
19/19 geçti. Tam paket (`pytest tests/lakehouse tests/ingestion -q`) → 600 passed, yalnız aynı 2 bilinen
ilgisiz hata.

## Commit 10 — sonuç (tamamlandı)

**Kod inceleme bulgusu:** `agentic_analytics/agent/runtime.py::_dispatch()`'te zaten genel bir
"aynı (name,args,revision) 2 kez görülürse NO_PROGRESS" korunması var (`state["seen"]`, fingerprint
`{name,args,revision}` üzerinden). Ama bu yalnız BİREBİR AYNI argümanlar tekrarlanırsa tetikleniyor.
`discover`'ın `query` metni her defasında yeniden ifade edilince (nakit/kullanım/hariç/harcama/çekim gibi
kelime permütasyonları) fingerprint her seferinde FARKLI oluyor — bu yüzden genel koruma hiç devreye
girmiyor ve model aynı hedefi 12-17 kez arayabiliyor, sonunda `NO_PROGRESS`/`DECISION_BUDGET_EXCEEDED` ile
tüm toplanan near-match kanıtını atıyor (`ASIL SORUN.md`'de 4 ayrı eval transkriptinde doğrulanmış desen).

**Yapılan değişiklik:** `AgentRuntime`'a 3 yeni metod eklendi (`_track_search_progress`'in hemen ardına,
aynı state-tracking deseniyle):
- `_discover_target_key(query)` — `agentic_analytics.lakehouse.discovery.query_intent()`'in zaten
  `discover()`'ın kendi semantik sıralaması için kullandığı `families`/`qualifiers`/`meaning_terms`
  çıkarımını yeniden kullanarak, farklı lexical ifadeleri (örn. "kredi kartı 1" / "kredi kartı 2") aynı
  hedef anahtarına indirger.
- `_discover_retry_cap(state, args)` — `_dispatch()`'in `discover` dalına, mevcut `paused`/`seen>=2`
  kontrollerinin hemen öncesine eklendi. Aynı hedef için 3. çağrıdan itibaren `service.discover()`
  ÇALIŞTIRILMIYOR; bunun yerine ilk iki çağrıdan biriken kanıttan deterministik çözülüyor: tek düşük
  belirsizlikli aday varsa otomatik çözümlenmiş olarak döner (`no_confident_match: false`), birden fazla
  aday varsa modele TEK bir netleştirme sorusu sorması gerektiğini söyleyen `ambiguous_candidates` listesiyle
  döner, hiç aday yoksa kesin bir "bulunamadı" döner (hiçbiri tekrar aramayı önermez).
- `_record_discover_evidence(state, args, result)` — her gerçek `discover()` çağrısından sonra
  (`result = _normalize_result(result)`'ın hemen ardından) o hedefin kanıtını (`metrics`, `near_matches`,
  `uncovered_terms`) `state["discover_targets"]` altında biriktirir; bu state zaten var olan
  `self.run_store.checkpoint(run_id, state)` mekanizmasıyla kalıcılaşır.

**Önemli düzeltme (test sırasında bulundu):** İlk taslak, capped sonuçta `uncovered_terms`'i taşımıyordu —
bu, mevcut `test_barren_discovery_completes_as_grounded_refusal_not_budget_death` testini kırdı (mesaj
"zephyr" terimini içermiyordu). Düzeltildi: `entry["uncovered_terms"]` ve `entry["near_matches"]` de
biriktirilip capped sonuca aktarılıyor.

**Regresyon kontrolü:** `agentic_analytics/agent/runtime.py`'nin dokunduğu her test dosyası hem
`git stash` ile (değişiklik YOKKEN, temiz taban) hem de değişiklikle çalıştırılıp BİREBİR karşılaştırıldı:
- `tests/agent/test_agent_runtime.py`: taban 55 geçti/2 hata (biri bu commit'in düzelttiği
  `test_barren_discovery...`, diğeri `test_verbose_discovery_stays_small...` — bu ikincisi hem tabanda hem
  değişiklikle AYNI şekilde başarısız, commit 10'dan tamamen bağımsız, önceden var olan bir hata); değişiklikle
  59 geçti/1 hata (yalnız ilgisiz `test_verbose_discovery...`).
- `tests/agent/test_delivery_contracts.py` + `test_never_dead_end.py`: taban 238 geçti/11 hata, değişiklikle
  238 geçti/11 hata — BİREBİR AYNI 11 test adı, hiç yeni regresyon yok. (Bu 11 hata da bu commit'ten önce,
  bu oturumun dokunmadığı kodda zaten var — muhtemelen origin/main ayrışmasıyla ilgili, ayrı takip gerektirir.)
- Yeni `tests/agent/test_agent_runtime.py`'de 3 test eklendi: aynı hedefin 3. çağrısının gerçek
  `service.discover()`'a hiç ulaşmadığını ve tek adaydan otomatik çözüldüğünü kanıtlayan test, birden fazla
  adayda `ambiguous_candidates` döndüğünü kanıtlayan test, farklı hedeflerin birbirini capping etmediğini
  kanıtlayan test.
- `pytest tests/lakehouse tests/ingestion -q` → değişmedi (bu commit yalnızca `agentic_analytics/agent/`
  dokunuyor), yalnız aynı 2 bilinen ilgisiz hata.

**Kapsam dışı bırakılan:** `test_delivery_contracts.py`'deki 11 önceden var olan hatanın kök nedeni
araştırılmadı — bu commit'in konusu değil, muhtemelen `origin/main`'in 16 commit'lik ayrışmasıyla (yukarıdaki
ACİL bölümü) ilişkili; ayrı bir takip konusu olarak bırakıldı.

## Commit 11 — sonuç (tamamlandı)

**Bulgu:** Kodda literal `technical_ledger`/`source_records` isimli bir şey yok — görev talimatındaki bu
terimler, bu oturumda görülen eval JSON export'larının kendi anlatım diliydi. Gerçek karşılığı:
`agentic_analytics/lakehouse/registry.py`'nin her binding için tuttuğu `provenance_columns` listesi
(kaynağa göre TAMAMEN FARKLI alan adları: EVDS `source_response_file/source_response_sha256/source_row_index`,
BDDK_MONTHLY `source_file/source_sha256/source_row_index/value_dimension/group_code`, TUIK
`source_csv_file/source_sheet/source_cell/source_press_url`, PDF `agentic_analytics/agent/tools/documents.py`'nin
`source_id/raw_sha256/table_id/row`) + `LakehouseService.explain_value()`'ün zaten ürettiği `source_cells`
listesi (her biri bu heterojen alan adlarını taşıyan ham satırlar).

**Yapılan:** Yeni `agentic_analytics/lakehouse/source_contract.py`:
- `build_source_row_contract(binding, cell, *, column, period, value) -> dict` — herhangi bir kaynak
  türünün ham `cell` sözlüğünü (yukarıdaki 4 gerçek şekilden biri) tek bir şemaya normalize eder:
  `{source_id, hash, url, page_or_sheet, table, row, column, period, unit, scope, value, complete,
  missing_fields}`. `source_id` içerik-adresli (`source_row_` + kimlik alanlarının sha256'sının ilk 32
  hex karakteri) — `store.py`'nin zaten kullandığı "içerik hash'i = kimlik" felsefesiyle tutarlı.
  `page_or_sheet` hiçbir zaman "complete" için zorunlu değil (EVDS/BDDK/TUIK sayısal zaman serisi
  hücrelerinin doğal olarak sayfa/sheet'i yok); diğer tüm alanlar eksikse `complete: false` +
  `missing_fields` ile açıkça raporlanıyor, sessizce atlanmıyor.
- `validate_source_row_contract(contract)` — commit 12'nin teslimat kapısının güvenebileceği yapısal
  doğrulama (tam alan kümesi, `source_id` biçimi, tip kontrolleri, `complete`/`missing_fields` tutarlılığı).
- `SourceRowContractLedger` — `store.py`'nin atomic-write deseniyle (temp dosya + `os.replace`, fsync)
  aynı disiplinde, `source_id`'ye göre içerik-adresli, ekleme-yalnızca (append-only) bir JSON dosya deposu;
  aynı `source_id` altında farklı içerikli bir yeniden yazma reddediliyor (doğrulanmış bir satırın sessizce
  değiştirilmesini engelliyor).

**Test:** `tests/lakehouse/test_source_row_contract.py` (19 test) — EVDS, BDDK, TUIK (web bülteni), PDF
belge hücresi için GERÇEKÇİ (registry.py/documents.py'deki gerçek alan adlarını kullanan) fixture'larla her
4 kaynak türünün de eksiksiz bir contract ürettiği; hash eksikse `complete: false` olduğu; `source_id`'nin
teslim edilen değere değil satır kimliğine bağlı olduğu (değer değişince id değişmiyor, satır değişince
değişiyor); `validate_source_row_contract`'ın bozuk şekilleri reddettiği; `SourceRowContractLedger`'ın
round-trip, idempotent yeniden yazma, çakışan yeniden yazmayı reddetme davranışları doğrulandı.

**Kapsam dışı bırakılan (bilinçli, commit 12'nin konusu):** Bu commit yalnız şemayı + oluşturucu/doğrulayıcı/
depoyu tanımlıyor; `LakehouseService.explain_value()`/PDF `inspect_source` gibi gerçek üretim çağrı
noktalarının bu contract'ı otomatik oluşturup teslimat öncesi zorunlu kılması commit 12'de yapılacak.

### 23 Eylül denetimi — commit mesajı ile gerçek içerik farkı

`1b7a1a6d`'nin mesajı #12'yi tamamlamış gibi görünse de değişen altı dosya yalnızca
`source_contract.py`, onun unit testleri, regression-snapshot testleri ve bu belgedir.
`runtime.py`, `delivery.py`, `LakehouseService.explain_value()` ve ingestion araçlarında
`build_source_row_contract`, `validate_source_row_contract` ya da `SourceRowContractLedger`
çağrısı yoktur. Dolayısıyla #12 için kabul koşulu henüz karşılanmadı; bu commit **#11 ve #13'ün
parçalarının yanlış mesaj altında birlikte paketlenmiş hâlidir**.

Bu tespit testle de doğrulandı:

- `pytest tests/lakehouse/test_source_row_contract.py tests/evals/test_scenario_regression_baseline.py -q`
  → **58 geçti**.
- Bu test sonucu yalnız contract şemasını ve geçmiş 32 JSON export'unun snapshot ayrıştırmasını
  kanıtlar; canlı runtime'ın contract'ı kullandığını kanıtlamaz.

**#12'ye güvenli başlangıç:** Önce `LakehouseService.explain_value()` çıktısına, mevcut
`source_cells`den üretilen contract'ları ekleyip yalnız gözlem/raporlama yapılmalı. Ardından mevcut
ready EVDS, BDDK, yüklenmiş Excel ve PDF yollarında tamamlanma oranı ölçülmeli. Bu ölçüm olmadan
contract'ı global teslim kapısı yapmak, geçmiş bağların URL/scope/satır alanları eksik olabileceği
için çalışan analiz ve grafiklerin gereksiz yere engellenmesi riskini taşır.

**Test sonucu:** `pytest tests/lakehouse/test_source_row_contract.py -q` → 19/19 geçti. Tam paket
(`pytest tests/lakehouse tests/ingestion -q`) → bkz. commit mesajı.

## Kalan commit'ler için orijinal plan detayları

### Faz 1 — Veri (devam)
**4. `data(borsa): import official precious-metals monthly panel`**
Borsa İstanbul resmi altın işlem hacmi/miktarı, günlükten aylığa manifestli agregasyon; BIST100 zaten
bağlı, tekrar indirilmez. **Kabul:** Benchmark Senaryo 4 prompt 1 PDF/web ayrıştırmasına düşmeden aylık
altın tablosunu üretir. Not: `tools/import_bist_xbank.py`, `tools/import_bist_xu100_history.py`,
`tools/import_bist_index_baselines.py` gibi hazır importer'lar var, önce bunları incele.

**5. `data(reference): add minimum wage decision lookup table`**
Resmi Gazete asgari ücret komisyon kararları — tarih + net/brüt tutar, sonlu ve sabit liste (sürekli
seri değil, EVDS pattern'i uygulanmaz, küçük bir referans tablosu/JSON olarak eklenmeli).
**Kabul:** Senaryo 17, 25, 30 asgari ücret tarihini web aramasına düşmeden bulur.

**6. `docs(eval): rerun benchmark 1-4 checkpoint`**
Ara doğrulama — Benchmark Senaryo 1-4'ü (bkz. `Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/kkb_hackathon_25_demo_senaryolari.md` Bölüm 1) manuel/otomatik tekrar koştur, önce/sonra karşılaştır.

### Faz 2 — Mimari (kalıcı, hotfix değil)

**7. `feat(runtime): replace global on-demand overlay with workspace-scoped versioned acquisition store`**
Repo'da zaten `tools/EVDS_Talep_Uzerine_Indirme_Araci.py` (on-demand EVDS indirme) ve
`tests/lakehouse/test_overlay_source_routing.py` gibi bir overlay/source-routing altyapısı var —
**önce bunu oku**, sıfırdan yazma. `fix_ebrar` branch'indeki (`dc138952`) global `on_demand.duckdb`
overlay'i **main-test-updates'te yok**, main'in kendi ayrı bir on-demand mekanizması var
(`4aa7ba5c Complete official banking data coverage`). Workspace-scoped, kilitli, versiyonlu hâle
getirilmesi ve doğrulanmış serinin çekirdek pakete (commit 1/3/4) terfi ettiği bir adım eklenmeli.

**8. `fix(lakehouse): add executable aliases for BDDK financial metrics`**
Şema doğrulamalı, coverage testli alias/crosswalk registry (taşıt, ticari, ihracat, KOBİ, NPL, kredi
kartı; stok/akım/kümülatif/rate; sector/currency/maturity/institution kapsamı).

**9. `feat(lakehouse): classify ready/acquirable/near_match_available/web_required/unavailable`**
`kkb_hackathon_25_demo_senaryolari.md` Bölüm 4'teki audit tablosu (35 senaryo) fixture olarak
kodlanmalı. 0-eşleşmeli adaylar (XBANK vb.) bütçe tükenmeden deterministik `unavailable` dönmeli.

**10. `fix(discover): cap lexical reformulation retries and resolve from already-ranked near-matches`**
Kanıt (bu conversation'da doğrulandı): main+Ebrar'da 4 ayrı eval koşusunda `discover` aynı 6 adayı
12-17 kez, yalnız kelime permütasyonuyla (nakit/kullanım/hariç/harcama/çekim) tekrar çağırıp
`NO_PROGRESS` ile tüm near-match kanıtını atıyor. `discover` hedef başına en fazla 2 çağrıyla
sınırlanmalı; sonrasında toplanan near-match'ler otomatik değerlendirilmeli (düşük belirsizlikli tek
adayla devam / birden fazla adayda tek net soru). İlgili kod muhtemelen `agentic_analytics/agent/`
altında bir orkestrasyon/planner modülünde.

**11. `feat(sources): persist verified source-row contracts`**
`source_id + hash + URL + page/sheet + table + row + column + period + unit + scope + value` — EVDS,
BDDK, PDF hücresi, web bülteni aynı şemayla.

**12. `fix(runtime): enforce source-row contract gate before analysis/chart delivery`**
Contract'ı her ingestion path'inin (PDF/Excel/web/lakehouse) geçmek zorunda olduğu tek bir doğrulama
kapısı yap.

### Faz 3 — Doğrulama

**13. `test(eval): convert scenario exports into automated regression harness`**
`/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/` klasöründeki 32 elle etiketlenmiş JSON export
yerine aynı sınıflandırmayı (`completed/partial/blocked/needs_input` + contract completeness) otomatik
assert eden `pytest` suite; 35 senaryo fixture, CI'da koşar. JSON export şeması:
`conversation_runs[].{status, result.{errors[], active_analysis_id, chart_id, message}}`.

**14. `docs(eval): full rerun of 25+10 set, before/after report`**
Commit 1-12'nin toplu etkisini önceki main 41 / Ebrar 42 koşu tabanına karşı raporlar.

## Kapsam sınırı (bilinçli olarak dışarıda bırakılanlar)

52.696 EVDS serisinin tamamı, KAP'ın tüm arşivi, DİBS ihale sonuçları, ODMD satış verisi, MKK/SPK
bültenleri, Resmi Gazete düzenleme metinleri (asgari ücret hariç) — bunlar `kkb_hackathon_25_demo_senaryolari.md`'nin
kendi tasarımıyla bilinçli web/PDF sınıfında kalıyor, lakehouse'a girmiyor.

## Kaynak belgeler (bu planın dayandığı analiz)

- `/home/neo/Desktop/son-testler-ve-son-buyuk-fix-KKB-hackaton.md` — Ebrar'ın on-demand EVDS + overlay geçmişi, main branch force-push/recovery hikayesi
- `/home/neo/Desktop/GITHUB MYZ21/agentic-data-analytics/docs/eval-set/ASIL SORUN.md` — 83 koşunun (main 41, fix_ebrar 42) karşılaştırmalı analizi, 4 kırılma noktası
- `/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/kkb_hackathon_25_demo_senaryolari.md` — 35 senaryonun tam listesi + Bölüm 4 audit tablosu (lakehouse'ta var/yok)
- `/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/main branch/` ve `ebrar branch/` — 32 ham eval JSON export'u
