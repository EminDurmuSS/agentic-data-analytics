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

## Repo / branch bilgisi

- Repo: `https://github.com/EminDurmuSS/agentic-data-analytics`
- Branch: `main-test-updates`
- Bu worktree: `/home/neo/Desktop/GITHUB MYZ21/agentic-data-analytics-main-test`
- Şu anki HEAD (lokal): `9d1f3d4f` — `data(macro): add extended reference series for dış ticaret & piyasa ailesi`
- `origin/main-test-updates`: **`6db768bd`'de kaldı** — `9d1f3d4f` henüz push EDİLMEDİ. Devam eden ajan
  başlatmadan önce mutlaka `git push origin main-test-updates` ile senkronize edin, yoksa yeni ajan
  worktree'leri yine eski noktadan başlar (bkz. "Bilinen sorun" bölümü — bu tam olarak o sorunu tetikler).

## Durum tablosu

| # | Commit mesajı | Durum | Not |
|---|---|---|---|
| 1 | `data(evds): add verified demo core rate observations` | ✅ TAMAMLANDI, merge edildi, **push edildi** (`6db768bd`) | TP.BKR.TRY.17, TP.TRY.MT02, TP.BKR.TRY.1 — 78/78/339 gözlem, gerçek EVDS verisi |
| 2 | `data(evds): add validated macro join pack` (TÜFE+KFE) | ✅ GEREKSİZ BULUNDU, atlandı | `TP.TUKFIY2025.GENEL` (TÜFE) ve `TP.KFE.TR` (KFE) zaten `ready` durumda, `housing_causality_v1` manifestinde `price_deflator` rolüyle önceden bağlı. Kod değişikliği gerekmiyor. |
| 3 | `data(macro): add extended reference series for dış ticaret & piyasa ailesi` | ✅ TAMAMLANDI, commit atıldı (`9d1f3d4f`), **push edilmedi** | 10 seri eklendi, detay aşağıda |
| 4 | `data(borsa): import official precious-metals monthly panel` | ✅ TAMAMLANDI, commit atılacak | EVDS `TP.ALTINPIYASA.HACM02`/`MIKT02` — detay aşağıda |
| 5 | `data(reference): add minimum wage decision lookup table` | ✅ TAMAMLANDI, commit atılacak | 8 karar dönemi (2021-2026), EVDS pattern'i bilinçli uygulanmadı — detay aşağıda |
| 6 | `docs(eval): rerun benchmark 1-4 checkpoint` | ⬜ Başlamadı | |
| 7 | `feat(runtime): replace global on-demand overlay with workspace-scoped versioned acquisition store` | ⬜ Başlamadı | |
| 8 | `fix(lakehouse): add executable aliases for BDDK financial metrics` | ⬜ Başlamadı | |
| 9 | `feat(lakehouse): classify ready/acquirable/near_match_available/web_required/unavailable` | ⬜ Başlamadı | |
| 10 | `fix(discover): cap lexical reformulation retries and resolve from already-ranked near-matches` | ⬜ Başlamadı | |
| 11 | `feat(sources): persist verified source-row contracts` | ⬜ Başlamadı | |
| 12 | `fix(runtime): enforce source-row contract gate before analysis/chart delivery` | ⬜ Başlamadı | |
| 13 | `test(eval): convert scenario exports into automated regression harness` | ⬜ Başlamadı | |
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
