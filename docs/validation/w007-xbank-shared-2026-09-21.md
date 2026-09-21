# W007 — Resmî XBANK verisinin ortak kayda alınması ve arayüz testi

Tarih: 21 Eylül 2026. Bu çalışma kullanıcı isteğiyle veriyi önceden ekler.
**Canlı web aramasının tek başına başarılı olduğunu göstermez.** Eski başarısız
W007 koşuları silinmemiştir. Yeni testte özgün soru değiştirilmez.

## Resmî kaynak ve kapsam

- Keşif: [Borsa İstanbul / Konsolide Veriler](https://www.borsaistanbul.com/veriler/konsolide-veriler), “BIST Pay Endeksleri-Fiyat” indirme düğmesi.
- Ham dosya: [TR_PayEndeksleriFiyat.zip](https://www.borsaistanbul.com/datum/TR_PayEndeksleriFiyat.zip). Anonim HTTP 200; giriş/ödeme/erişim engeli aşılmadı.
- Kimlik: [XBANK / BIST BANKA](https://www.borsaistanbul.com/endeks/xbank). TL fiyat endeksi; getiri endeksi, BIST 100 veya BIST Mali Endeksi değildir.
- İç dosya `TR_PayEndeksleriFiyat.xlsx`, sayfa `Pay Piyasası Fiyat Endeksleri K`, A–E sütunları: tarih, kod, endeks adı, kur türü, kapanış.
- 2025 için 3519–3530 numaralı fiziksel satırlarda 12 ayın gerçek kapanış değerleri bulundu. Ocak: 14.936,81; Aralık: 16.540,04 endeks puanı. Tüm sayılar doğrudan sayısal kaynak hücrelerinden alınır.
- Ay etiketi kaynak gözlem tarihinden türetilir; özgün tarih ayrı saklanır. Mart 2025 gözlemi **28 Mart** tarihli; 31 Mart yapılmaz. Mayıs 30, Ağustos 29, Kasım 28 tarihleri de korunur. Günlük verilerden ortalama/toplam veya interpolasyon yapılmaz.
- Kapsam yalnız 2025 aylık XBANK / TL fiyat endeksidir. Günlük seri, 2024 Aralık bazı veya diğer endeksler bu aktarımda yoktur.
- Önceki “anonim resmî kanal bulunamadı” notları ilgili eski koşuların sonucudur; tüm resmî kanallarda veri olmadığı şeklinde yorumlanmamalıdır. Bu dosya erişilebilir bir resmî alternatiftir.

## Kalıcı kayıt

- Aktarım alanı: `workspace_54f4a02f75a242f0b5866a1a35332c23` — `W007 XBANK resmî veri aktarımı`.
- Dataset: `dataset_a8a376a640b38641362139cf5e0f424463f3f60b7ef9a23c04a32cbd80a38a56`.
- Metrik: `overlay:dataset_a8a376a640b38641362139cf5e0f424463f3f60b7ef9a23c04a32cbd80a38a56:closing`.
- Release: `release_5431f955b8df9c4b4115fab2f91581ed43408ff6df2474ecbd32e7b69d66eb38`.
- Promotion: `promotion_c341f05d57037cccd17ef956cda6c76a16ad652c0d3e018c7323131368ca0521`.
- ZIP SHA-256: `721ae0023453d58a0e94ad02baabd12e4a5652c12f66e2f90415fb262e330345`.
- XLSX SHA-256: `6e2e581dc9267dfdb9c3592e6e2630284467e4821873958748bc4e986a9fd506`.
- Ham ZIP, kaynak manifesti, seçili hücrelerin inceleme kaydı, CSV, Parquet ve veri sözleşmesi ortak pakette tutulur. Temel DuckDB değişmedi; yeni KKB finans alanları release'i alır, mevcut alanlar sürümlerinde kalır.
- Fiziksel konum Docker `agent-runtime` volume'u, `/app/.lakehouse-runtime/app/lakehouse/shared`. Normal container yenilemesinden sonra kayıt ve yeni alana bağlanması doğrulandı. Volume silinirse bu kayıtlar da silinir; git commit'i runtime verisinin yedeği değildir.
- Tekrarlanabilir operatör komutu ve sınırlar: [araç rehberi](../../tools/README.md#bist-banka-endeksi-aylık-kapanış-aktarımı).

## İlk veri-eklenmiş UI koşusu

- Alan: `W007 resmî XBANK verisiyle tekrar`, `workspace_887bb37cbcf54b9fa1245f7a1cb154e3`.
- Koşu: `run_c4e97b51ceb24253ae97bf7c47548678`, 14:23:50–14:26:32 Türkiye saati.
- Özgün W007 arayüzden gönderildi. Agent ikinci keşif sorgusunda eklenmiş veriyi buldu; `describe`, `execute`, `summarize_analysis`, `create_chart` kullandı. Bu koşuda web araması yapılmadı.
- 12 satır × 2 sütun ve 12 noktalı grafik oluştu. `result-content` görünür, `result-empty` gizli. Yenileme yapmadan son duruma geçti. Üst teknik akış ve sonuçtaki yedi araçlık teknik defter açıldı.
- Önce istenen `growth` istatistiği endeks için reddedildi; agent fark istatistiğiyle düzeltti. Bu ara hata kayıtlarda korundu.
- Son durum `partial`: yanlış kurumsal ilişki sınıflandırması nedeniyle `EXTERNAL_FACTS_UNVERIFIED`. Kaynak verisi doğru olmasına rağmen alakasız ortak/üye/kurucu uyarısı verildi; tam başarı sayılmadı.
- Ses 55,149 saniyede üretildi; 38,88 saniyelik WAV arayüzde oynatıldı. `currentTime` 0,009 → 17,163; `paused=false`, `readyState=4`, medya hatası yok. Transkript grafik verilerine dayanıyor, teknik başarısızlık anlatımı içermiyor. Bu medya kontrolü kulakla telaffuz değerlendirmesi değildir.

## İlgili düzeltmeler ve regresyon

- `17c7f865`: resmî arşivden doğrulamalı, kaynak/hücre izli operatör aktarımı.
- `77b0bceb`: ortak web verilerinin `BIST` gibi kurum filtrelerinde kaybolması düzeltildi. Kaynak adı başlıktan tahmin edilmez; kayıtlı URL'nin gerçek host'u kullanılır. `SESSION_DATASET` kimliği korunur; sahte domain/path/URL eşleşmez.
- `1790145b`: “çalışma alanı / aşmaya çalışma” ile “hangi tablo”nun kurumların ortaklarını soruyormuş gibi sınıflandırılması düzeltildi. Gerçek ortak/kurucu/üye sorularının kanıt şartı kaldırılmadı.
- Aktarım, kaynak yönlendirme, keşif ve ortak kayıt regresyonları: **76 test, 27 alt test geçti**.
- Kurumsal niyet ve devam soruları odaklı: **28 test geçti**.
- Agent runtime regresyonu: **56 test, 32 alt test geçti**. Daha önce mevcut olduğu doğrulanan `test_verbose_discovery_stays_small_then_executes_and_continues_with_schema` bağlam-bütçesi testi bu koşuda hariç tutuldu.
- Tam `test_delivery_contracts.py`: **200 geçti, 3 başarısız**. Üç hata düzeltme öncesi `HEAD` runtime belleğe yüklenerek de tekrarlandı: `test_failed_research_is_replaced_only_by_relevant_ownership_read` olumlu örneği, `test_research_replacement_read_does_not_clear_calculation_error`, `test_provider_search_budget_failure_is_recovered_by_relevant_source_read`. Bunlar önceki kaynak-URL keşif kuralıyla eski test kurgularının uyuşmamasına ilişkin; bu çalışmada giderilmiş sayılmadı.

## Son UI doğrulaması

Kaynak yönlendirme ve niyet düzeltmeleri uygulama image'ına alındı. Yeni `W007 XBANK son doğrulama` alanında aynı soruyla tekrar test başlatıldı.

- Alan `workspace_f219a9e5674a4dd5a73d6ce76181d687`; koşu `run_c978c5799739471aa4d5c065f7c72926`; başlangıç 14:30:43 Türkiye saati.
- Yanlış `external_facts_required` artık `false`; kurumsal ilişki türü `null`.
- İlk araç sorgusu `BIST Banka Endeksi XBANK aylık kapanış` eklenmiş metriği buluyor. Agent ayrıca EVDS ve XBANK keşfi, `describe`, `plan_task`, `execute`, `summarize_analysis` yaptı.
- Analiz `analysis_d91cd9892785b4ac10e702159784d634fc0b4fc8cf862b7efc6f89b03aa9e8aa`, 12 satır × 2 sütun.
- Sayısal kontrol aktarım fonksiyonundan bağımsız yapıldı: promotion paketindeki ham ZIP tekrar açıldı, XLSX'teki `XBANK / 2025 / TL` hücreleri doğrudan okundu ve analizin 12 dönem/değer çiftiyle `Decimal` düzeyinde karşılaştırıldı. **12/12 tam eşleşme**; eksik ya da ek ay yok. Analiz SHA-256: `2dc65f4a96a801845eb1178bdc33e77ff32df9057f774873a73b8c52af6b0ace`.
- **Sonuç: `completed`, hata yok, `repairs=0`.** 14:34:04 Türkiye saatinde bitti; yaklaşık 3 dakika 21 saniye. Altı model kararı, yedi başarılı araç kaydı, üst defterde 29 olay. Son model yanıtı iki bağlantı denemesiyle 113,903 saniye sürdü; sürenin önemli kısmı veri sorgusu değil sağlayıcı yanıtıydı.
- Tablo 12 ayı doğru değerlerle gösterdi; `result-content` görünür, `result-empty` gizli. “Sürüyor” → “Tamamlandı” geçişi **refresh yapmadan** görüldü. Bu gözlem, eski çalışma alanını yeniden açmadaki tüm senkronizasyon sorunlarının çözüldüğü iddiası değildir.
- Grafik sekmesi açıldı: 12 noktalı çizgi, endeks birimi, Ocak–Aralık kapsamı ve resmî ZIP bağlantısı görünüyor. Bu koşuda model ayrıca `create_chart` çağırmadı (`chart_id=null`); arayüz kayıtlı analizin standart grafiğini gösterdi.
- Teknik işlem kayıtları (7) ve “Özgün yanıt ve tamamlanma durumu” açıldı. Gerçek durum `completed`; ortak/üye/kurucu uyarısı artık yok. Araçların tamamı `ok`; bu koşuda web araması veya kaynak indirme yapılmadı. “Ek yöntem notları”nda hata gösterecek bir terminal hata/uyarı oluşmadı.
- `Sesli özet` düğmesi çalıştı. Üretim 50,698 saniye (Qwen 12,019; yerel EMA-TTS 38,675); WAV süresi **42,16 saniye**. Oynat tıklandı; `currentTime` 6,048 → 17,109, `paused=false`, `readyState=4`, medya hatası yok. Transkript açılıp okundu; doğru ilk/son/min/maks/fark değerleri var, teknik başarısızlık anlatımı yok. Kulakla telaffuz doğrulaması yapılmadı.
- Son ses kimliği: `voice_9387170d5d84db1aa7a0a066543a8d6d99593dfb1cbdf4e50022556ac75904be`.

## Değerlendirme sınırı

W007'nin **veri önceden eklenmiş senaryosu** artık arayüzde tablo, grafik,
kaynak izi ve oynatılabilir sesle tamamlanıyor. Önceki web-araştırma başarısızlıkları
geçmişe dönük başarıya çevrilmedi. Operatörün resmî ZIP'i bulup aktarması,
agent'ın yeni bir boş alanda aynı dosyayı bağımsız keşfedip işleyebildiğini
kanıtlamaz; o ayrı bir web-search testi olarak tutulmalıdır.
