# Kaynak hücresinden grafik kurtarma — 21 Eylül 2026

## Sorun ve kapsam

Garanti W001 koşusunda resmî raporun toplam aktifler hücresi okunmuştu, ancak
sonraki MIA çağrısı üç denemede başarısız oldu. Önizleme metni korunsa da
analiz/grafik oluşmadı. Bu düzeltme sağlayıcı bağlantısını düzeltmez; kesintiden
önce gerçekten okunmuş, tek anlamlı bilanço satırını normal araçlarla teslim eder.

Türkçe finansal tablo tarih başlıkları ayrı `2b7c3fc1` commit'inde düzeltildi.
Buradaki ikinci düzeltme şirket, tutar, dönem veya sütun numarası sabitlemez.

## Güvenlik ve doğruluk sınırları

- Aynı turda doğrudan okunmuş tablo hücreleri ile hash-doğrulamalı kaynak adayı
  eşleşmelidir. Arama özeti ve tablo önizlemesi tek başına yeterli değildir.
- Kullanıcı açık tek tarih belirtmelidir. İstenen satır, dönem, toplam sütunu,
  konsolidasyon kapsamı, para birimi/ölçek ve sayı ayrıştırması belirsizse kurtarma
  yapılmaz. Gerekli insan incelemesi atlanmaz.
- Normal `ingest_source_table → aggregate_dataset → create_chart` araçları
  çalıştırılır. Kaynak değeri `source_value` olarak alınır; bilanço bakiyesi
  dönemler arasında toplanmaz. Kaynak hücresi izi ve denetim kayıtları korunur.
- Ortak lakehouse'a otomatik yayın yoktur. Yazma mevcut çalışma alanıyla sınırlıdır.
  Önceden kaydedilmiş analiz üzerine yazılmaz.
- Sağlayıcı hatası gizlenmez: sonuç `partial` kalır ve özgün hata teknik kayıtta
  durur. Kurtarma sırasında yeni model/web çağrısı yapılmaz.
- Sonraki konuşma turu varsa eski tur yeniden açılmaz. Bu mevcut koruma canlı
  denemede de çalıştı; W002 sonrasında W001'i yeniden başlatma reddedildi.

## Otomatik kanıt

`tests/agent/test_delivery_contracts.py` içindeki 14 hedefli vaka geçti (son koşu 5,81 saniye):

- Gerçek ayrıştırıcı, yayın, hesaplama ve grafik araçlarıyla kontrollü MIA kesintisi.
- Önceki/cari dönem ve TP/YP/Toplam sütunları ayrılır; yeni sentetik tutar kullanılır.
- Tarihsiz istek, yanlış dönem/kapsam, okunmamış satır, belirsiz sayı, eksik birim
  ve yalnız önizleme halinde grafik uydurulmaz.
- Mevcut analiz, kaynak hash'i ve gerekli inceleme korunur; değiştirilmiş hücre reddedilir.
- Eski kaydedilmiş uygun kesinti açıkça devam ettirildiğinde yeni sağlayıcı çağrısı
  yapmadan kaynak değeri ve grafik üretilir; normal tekrar aynı sonucu döndürür.
- Kaynak zaten içeri aktarıldıktan sonra bağlantı kesilirse aynı derleme/yayın
  makbuzu tekrar kullanılır; aynı hücreler ikinci veri kümesine kopyalanmaz.

```sh
.venv/bin/python -m pytest tests/agent/test_delivery_contracts.py \
  -k 'provider_outage or explicit_resume_of_saved_source_only_outage' -q --tb=short
```

Dört modüllük geniş koşuda (`test_delivery_contracts`, `test_agent_runtime`,
`test_financial_import`, `test_agent_documents`) son idempotent-aktarım vakası
eklenmeden önce **396 test ve 117 alt test geçti; dört test kaldı**. Bu dört hata
önceki commit'in runtime koduyla da yeniden üretildi: üç kurumsal kaynak/ortaklık
doğrulama beklentisi ve bir `CONTEXT_BUDGET_EXCEEDED` vakası. Bu nedenle tüm paket
yeşil diye raporlanmıyor; yeni kurtarma yolunun 14 vakası ayrıca yeniden çalıştırıldı.

## Gerçek kaynakla izole tekrar

Canlı W001'in önbelleğe alınmış resmî belgesi, ayrı geçici çalışma alanına kopyalandı:

- Kaynak: [Garanti BBVA, 31 Mart 2026 konsolide finansal rapor](https://www.garantibbvainvestorrelations.com/tr/images/pdf/31_Mart_2026_Konsolide_finansal_tablo_ve_aciklamalari.pdf#page=11).
- Ham SHA-256: `8d4982ee72430131e291fe75cddbc42bdcb8fbd508181947edba97c7a3fad0ca`.
- PDF sayfa 11, `table_p000011_text_001`, aday satırı 58, `column_10`.
- Satır: `VARLIKLAR TOPLAMI`; tarih `2026-03-31`; tutar **4.783.750.292 bin TL**.
- Oluşan analiz `analysis_3feee564b40746af78251a3e00e705717da2a69d0098e16f14f7ae99e39f25de`.
- Oluşan grafik `chart_98b4f1093c38d82df47ba289a4350785d73f856e4ec7760570bd8d319f4d507b`.

İlk sağlayıcı cevabı kaynak okumasını istedi; ikinci çağrıda kontrollü bağlantı
hatası üretildi. Sonraki üç gerçek araç çağrısı doğru tek hücreli analizi ve grafiği
kaydetti. Yeni sağlayıcı çağrısı yoktu. Bu **canlı Qwen başarısı değil**, gerçek
kaynak üzerinde hata kurtarma entegrasyon kanıtıdır; üretim verisine yazılmadı.

## Canlı arayüz doğrulaması

Docker uygulaması yeni kodla yeniden oluşturuldu; model/veri volume'ları korunarak
yalnız `agent-app` yenilendi. Garanti çalışma alanında ayrı grafik isteği gönderildi.

- Çalışma alanı: `Astra R2 W001-W002 Garanti rapor`.
- Koşu: `run_d159c0aa73be4cec8869113cffed2132`, 15:26:44–15:29:46 Türkiye saati.
- İstek: “Bulduğun resmî Garanti BBVA 31 Mart 2026 konsolide finansal raporundaki
  toplam aktifler tutarını kaynak tablodan doğrulayarak analiz tablosuna aktar ve
  grafikte göster. Raporun özgün birimini ve belge sayfasını koru; tutarı arama
  özetinden veya tahminden üretme.” Cevap/tutar prompta verilmedi.
- Gerçek Qwen sırası: `plan_task`, `inspect_source`, `read_source_table`,
  `ingest_source_table`, `execute`, `create_chart`, otomatik `summarize_analysis`.
- Analiz: `analysis_1953ddb7d2105477c7fedbe11e110cd0a03a028235685fc9ac1c94579cacb3f4`.
- Grafik: `chart_bffe9e15ed5cc717be72a48664948ab3f0fda393ecc0b4b171f29abf28592f92`.
- Chrome'da sayfa yenilenmeden `result-content` görünür, `result-empty` gizli;
  Grafik sekmesi seçili, tarih `31 Mart 2026`, tutar `4.783.750.292`, birim `bin TL`.
  Tek dönemlik çubuk grafik ve özgün PDF'nin 11. sayfasına bağlantı görüldü.
- Ek yöntem notları (1) ve teknik kayıtlar (7) açıldı. Sesli özet 15,2 saniye
  üretildi; oynatma `0,18 → 10,02` saniye ilerledi. İşitsel telaffuz kontrolü değildir.
- [Canlı grafiğin ekran görüntüsü](garanti-live-chart.png).

**Sınır:** Bu yeni canlı turda sağlayıcı kesintisi olmadı; dolayısıyla otomatik
kesinti kurtarma dalının canlı kanıtı değildir. Türkçe tarih düzeltmesi ve gerçek
araçların grafik üretip arayüze taşıması doğrulandı. Kesinti dalı yukarıdaki
kontrollü gerçek-kaynak tekrarında ve otomatik testlerde doğrulandı.

**Açık sorun:** Koşu `FINANCIAL_REPORT_CELL_PROOF_INCOMPLETE` nedeniyle `partial`
bitti. Mevcut son-yanıt kontrolü yalnız `toplam aktif / total assets` metnini
arıyor; bu kaynaktaki bölünmüş `VARLIKLAR TOPLAMI` satırını kabul etmiyor. Grafik
ve hücre kaydı mevcut olmasına rağmen bu uyarı teknik/yöntem notlarında korunuyor.
Ses metni de tutarı okumadan yalnız tek kaydı tarif ediyor. Bu çalışma tam ve
hatasız W001 başarısı olarak puanlanmadı; ayrı takip konularıdır.
