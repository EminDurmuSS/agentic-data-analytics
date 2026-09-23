# Asgari ücret karar tablosu (finite reference lookup, zaman serisi değil)

Araştırma tarihi: 23 Eylül 2026. Kapsam: 2021-01-01 — 2026 (güncel, açık uçlu).

`minimum_wage_decisions.json`, Asgari Ücret Tespit Komisyonu'nun Resmi Gazete'de yayımlanan
net/brüt asgari ücret kararlarının **sonlu, elle küratörlüğü yapılmış bir listesidir**. Bu bir EVDS
zaman serisi değildir ve `data_pipeline/evds/manifests/` deseniyle indirilmemiştir — asgari ücret
yılda 1 (bazı yıllarda 2, "ara zam" ile) kez değişen ayrı bir karar geçmişidir, EVDS kataloğunda
karşılığı yoktur (bkz. aşağıdaki "EVDS kontrolü" bölümü).

Her kayıt canlı web araması ile doğrulandı (bu commit'in hazırlandığı oturumda `WebSearch` aracıyla,
23 Eylül 2026). Bir kayıt yalnızca eğitim verisinden hatırlanan bir rakamla eklenmedi — her satırın
`verified_live: true` alanı ve `source_url`'si, o oturumda gerçekten getirilen bir kaynağa karşılık
gelir. 2025 kaydı doğrudan resmigazete.gov.tr'nin arşivlenmiş PDF'ine karşı doğrulandı
(`resmi_gazete_url` alanı dolu); diğer yıllarda doğrudan resmigazete.gov.tr PDF bağlantısı bu
oturumda bulunamadı, bunun yerine ÇSGB'nin kendi duyuru sayfası veya en az iki bağımsız profesyonel
kaynağın (PwC, Grant Thornton, Andersen, TÜRK-İŞ, KPMG, Lexpera'nın Resmi Gazete tam metin portalı)
aynı tarih/sayı ve tutarları teyit ettiği kayıtlar kullanıldı — her satırın `source_note` alanında bu
açıkça belirtilir.

| Kayıt | Yürürlük | Net (TL) | Brüt (TL) | Resmi Gazete |
|---|---|---|---|---|
| AUTK_2021 | 2021-01-01 — 2021-12-31 | 2.825,90 | 3.577,50 | 30.12.2020, No 31350 |
| AUTK_2022_H1 | 2022-01-01 — 2022-06-30 | 4.253,40 | 5.004,00 | 25.12.2021, No 31700 |
| AUTK_2022_H2 | 2022-07-01 — 2022-12-31 (ara zam) | 5.500,35 | 6.471,00 | 01.07.2022, No 31883 Mükerrer |
| AUTK_2023_H1 | 2023-01-01 — 2023-06-30 | 8.506,80 | 10.008,00 | 29.12.2022, No 32058 |
| AUTK_2023_H2 | 2023-07-01 — 2023-12-31 (ara zam) | 11.402,32 | 13.414,50 | 24.06.2023, No 32231 |
| AUTK_2024 | 2024-01-01 — 2024-12-31 | 17.002,12 | 20.002,50 | 30.12.2023, No 32415 |
| AUTK_2025 | 2025-01-01 — 2025-12-31 | 22.104,67 | 26.005,50 | 27.12.2024, No 32765 |
| AUTK_2026 | 2026-01-01 — (açık, güncel) | 28.075,50 | 33.030,00 | 26.12.2025, No 33119 |

## Tutarlılık kontrolü

Her kaydın `gross_daily_try` alanı `gross_monthly_try / 30`'a eşittir (asgari ücretin resmî günlük
hesap kuralı); bu sekiz kaydın hepsinde tam olarak tutuyor — bağımsız bir iç tutarlılık sinyali.

## EVDS kontrolü (neden bu bir EVDS commit'i değil)

`data_pipeline/catalog/evds_series_catalog.parquet` (52.696 satır) "Asgari" için arandı: yalnızca
2 eşleşme var, ikisi de asgari ücretle **ilgisiz** — `TP.KB.GEL0131` (Yerel Asgari Tamamlayıcı
Kurumlar Vergisi) ve `TP.KB.GEL0132` (Küresel Asgari Tamamlayıcı Kurumlar Vergisi), OECD küresel asgari
kurumlar vergisiyle ilgili bütçe gelir kalemleri. Asgari ücretin kendisi EVDS'te yayımlanmıyor —
bu beklenen bir durum, çünkü TCMB değil ÇSGB/Resmi Gazete kaynaklı, sonlu bir idari karar listesidir.

## Agent tool discovery durumu (dürüst rapor)

Bu dosya `data_pipeline/evidence/events/events.json` ile aynı klasör ailesindedir (`evidence/`), ama
**o dosya bile** `agentic_analytics/lakehouse/service.py`'nin `discover`/`describe`/`execute`
araçlarından **erişilebilir değildir**: bu üç araç yalnızca `catalog.metric_bindings` üzerinden
çalışır (`agentic_analytics/lakehouse/registry.py::build_bindings()`, ki bu da yalnızca
`catalog.metrics`'ten beslenir — EVDS/BDDK/TUIK/TBB kaynak adaptörleriyle sınırlı). `events.json`,
`data_pipeline/catalog/build_unified_catalog.py::event_assets()` içinde `catalog.assets` tablosuna
kaydedilir (bu tablo bir doğrulama/denetim kaydı, agent'ın sorgulayabildiği bir "metrik" değil) ve
ayrıca `data_pipeline/lakehouse/build_lakehouse.py` içinde tek bir spesifik analiz adımına
(`build_monthly_analysis`, konut kredisi aylık tablosuna olay etiketleri eklemek için) pandas
join'iyle gömülür. Yani `evidence/` klasörü genel olarak "agent'ın sorgulayabildiği bir katalog"
değil, statik/yardımcı dokümantasyon + tek-kullanımlık build-time zenginleştirmedir.

Bu commit'te aynı gerçek durum bu dosya için de geçerlidir: `minimum_wage_decisions.json`
`discover`/`describe`/`execute` üzerinden sorgulanamaz. Bunun yerine
`agentic_analytics/lakehouse/reference_data.py::load_minimum_wage_decisions()` adında küçük, test
edilmiş, gerçek bir Python yükleyici eklendi — bu, ileride bir agent tool'unun (örn. "asgari ücret
tarihini/tutarını sorgula" gibi dar bir amaçlı yardımcı fonksiyon) üzerine inşa edebileceği somut bir
yapı taşıdır, ama kendisi bir tool olarak kayıtlı değildir.

**Takip işi (bu commit'in kapsamı dışında):** Asgari ücreti gerçekten `discover`/`describe`/`execute`
üzerinden erişilebilir kılmak, ya (a) `catalog.metrics`/`registry.py`'nin zaman-serisi odaklı
(`native_frequency`, `aggregation`, periyot bazlı sorgu) varsayımlarını sonlu bir karar listesi için
zorlamak — ki bu, bu görevin kendi talimatının da uyardığı gibi yanlış temsil olur — ya da servis
katmanına ayrı, küçük bir "reference lookup" tool tipi eklemeyi (örn. `lookup_reference(topic)`)
gerektirir. İkincisi gerçek bir mimari karar ve `agentic_analytics/lakehouse/service.py`'ye yeni bir
genel kavram eklemeyi gerektirdiğinden, bu commit'in kapsamının dışında bırakıldı.
