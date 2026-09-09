# Kloudeks canlı yetenek doğrulaması

**10 Eylül 2026, 01:01-01:08 Türkiye saati.** Python `urllib` ile yalnız rehberde verilen Kloudeks adresine 20 küçük sentetik istek gönderildi. Anahtar gizli terminal girdisinden belleğe alındı; dosyaya, Git'e veya test çıktılarına yazılmadı. Test süreci kapatıldı. Bu çalışma üretim agent'ını kurmaz ve gerçek lakehouse sorularındaki başarıyı ölçmez.

**Karar:** Qwen'in native araç çağırması ve yapılandırılmış JSON yolu üzerinde ilk agent döngüsünü kurmak için somut olumlu kanıt var. Embedding çıktısı 4096 boyutlu. Belge alımında OCR sonucu ayrıca denetlenmeli; bu örnekte görselin tuval biçimi başarıyı değiştirdi.

## Sonuçlar

| Deney | Gözlenen sonuç | Kanıt |
| --- | --- | --- |
| Türkçe metin | Beklenen metin aynen döndü. | P01 |
| Otomatik araç seçimi | Gerçek `message.tool_calls`, doğru ad/argüman ve çağrı kimliği döndü. | P02 |
| Adıyla zorunlu araç seçimi | Doğru araç çağrısı var; `finish_reason` ise `stop`. | P03 |
| Araç sonucuyla ikinci tur | Aynı ilk çağrıya iki farklı değer ve önceden açıklanmayan doğrulama kodu verildi. Her iki cevap da yalnız aldığı sonucu aynen taşıdı. | P04-P05 |
| Gereksiz araç kullanımı | Araç mevcutken basit selamlamada çağrı yapmadı. | P06 |
| İç içe JSON şeması | Nesne, enum, zorunlu alan, ek alan yasağı ve nullable alan içeren çıktı yerelde şemaya uydu. | P07 |
| JSON object | Aynı küçük metrik planı doğru JSON ve doğru içerikle döndü. | P08 |
| Çelişen düz metin isteği | Kullanıcı düz metin isterken `json_schema` çıktısı şemaya uygun JSON kaldı. | P09 |
| Embedding | Dört girdinin sırası korundu; tüm vektörler sonlu, 4096 boyutlu ve yaklaşık birim normda. | P10 |
| Türkçe küçük retrieval örneği | İstanbul altın sorusuyla ilgili kartın kosinüs benzerliği 0,786; iki ilgisiz kart 0,269 ve 0,226. | P10 |
| Görsel tablo, Qwen | Dönemler, 100/120/90 değerleri ve milyon TL birimi doğru çıkarıldı. Serbest JSON isteği kod çiti ekledi; `json_schema` ile çıktı doğrudan ayrıştırıldı. | P12, P17 |
| Akım ve deflatör seçimi | Sentetik ilk çeyrek için `sum`, toplam 60 ve fiyat endeksi CPI seçildi. | P13 |
| Eksik veri | Metrik ikame edilmedi, sayı üretilmedi ve kaynak dosyası doğrulanmış denmedi. Serbest hata kodu mevcut API kodumuzdan farklıydı; açık hata sözleşmesiyle `METADATA_ONLY` döndü. | P14, P20 |

Bütün 20 istek HTTP 200 aldı. Bu, bütün görevlerin başarılı olduğu anlamına gelmiyor: iki OCR denemesi açık veri çıkarımı hatasıdır. Aynı şekilde yerel değerlendiricide 30 gözlemin doğrulanması, modelin 30 soruyu doğru cevapladığı anlamına gelmez; bu gözlemler başarısızlıkları ve protokol ayrıntılarını da kapsar.

## OCR'daki somut bulgu

İlk görsel [1100 x 560 yatay tablo](synthetic-table.png) idi. Rehberdeki tam model kimliği, base64 PNG, `<image>\ndocument parsing`, `temperature=0`, `max_tokens=8192`, `skip_special_tokens=false` ve `vllm_xargs={ngram_size:35,window_size:128}` kullanıldı. OpenAI istemcisinin `extra_body` alanındaki seçenekler, ham HTTP gövdesinde üst seviyeye kondu.

- **P11:** Baştan itibaren ilgisiz ve tekrarlı çıktı, 8192 çıktı token'ı, `finish_reason=length`; dönem ve değerler yok.
- **P16:** Aynı görsel ve 512 çıktı token sınırıyla hata tekrarlandı.
- **P15:** Ayrı bir [768 x 768 düz metin belgesi](synthetic-document.png), 512 sınırında 12345 ve 100 USD bilgilerini doğru verdi.
- **P18-P19:** Aynı tablo pikselleri [1100 x 1100 kare tuvalde](synthetic-table-square.png) doğru dönem/değer hücreleriyle okundu. P19 ve P16'da 512 sınırı dahil istek ayarları aynıydı; yalnız görsel farklıydı.

Orijinal tablonun tüm piksellerinin kare görselin üst kısmında aynı kaldığı yerelde doğrulandı. Bu, bu örnekte tuval biçimine duyarlılık gösterir. Servisin kök nedeni veya bütün yatay belgelerin davranışı belirlenmiş değildir. Kare tuval tek başına genel bir doğruluk garantisi değildir. OCR çıktısında ayrıca kısa gürültülü önek ve yerleşim etiketleri vardı; başarılı sonuç değerlendirmesi tablo hücrelerine ve birime dayandı.

Yarışma ürününde önce yerel PDF/Excel/HTML ayrıştırması; gerektiğinde kontrollü OCR; başarısızlıkta aynı Kloudeks Qwen görsel modeliyle ikinci okuma önerilir. Sayı/birim/tarih doğrulaması, tekrar tespiti ve `finish_reason=length` kontrolü zorunlu olmalı. Kare tuval kullanılırsa koordinat dönüşümü kaydedilmeli; OCR konum etiketlerinin ölçeği bu deneyde doğrulanmadığından bunlar doğrudan orijinal PDF piksel konumu diye sunulmamalı. Çok sayfalı gerçek belge doğruluğu ayrıca ölçülmelidir.

## Harness için doğrudan kararlar

1. **Önce `tool_calls` içeriğini kontrol et.** `finish_reason=stop` ile gelen geçerli araç çağrısını son cevap sayma. Araç adı/argümanını yerelde doğrula, ardından çağrı kimliğiyle sonucu bağla.
2. **Araç istemeyen JSON çıktılarında doğrulanmış şema kullan.** “Yalnız JSON yaz” prompt'u kod çitini her durumda engellemiyor. Sunucu şeması da birim/zaman/kapsamın doğru olduğunu tek başına kanıtlamaz.
3. **Hata kodları ve önceliği araç sözleşmesinden gelsin.** Serbest metinle hata kodu üretmek mevcut API kodlarıyla tutarsızlık doğurabiliyor. P14 ile P20 arasında prompt ve şema birlikte değiştiğinden iyileşme yalnız şemaya atfedilemez.
4. **Modelin iç muhakeme alanını araç çıktısından ayır.** Bu yanıtlarda `reasoning` alanı ve ayrı reasoning token sayacı gözlendi. Kayda yalnız karakter sayısı ve usage alındı; iç metin arayüz çıktısına veya araç argümanına dönüştürülmedi.
5. **Embedding indeksini ölçülen profile bağla.** Bu dağıtımda 4096 boyut gözlendi. Tek küçük eşleştirme, bütün 52.696 EVDS serisinde arama başarısını kanıtlamaz.
6. **OCR için içerik kabul kapısı kur.** HTTP 200 yeterli değil. Hatalı belgeyi tekrar tekrar uzun çıktıyla denemek yerine sınırlı onarım veya doğrulanan alternatif okuma kullan.

Yerel kâr/deflatör yürütme açıkları, model P13'te doğru seçim yaptı diye kapanmış olmaz. İlk gerçek lakehouse agent'ı kurulurken önceki denetimin P0 düzeltmeleri hâlâ gereklidir.

## Zaman ve kullanım ölçümü

| Model | İstek | Gözlenen en kısa / medyan / en uzun süre | Servisin bildirdiği toplam token |
| --- | ---: | --- | ---: |
| Qwen3.8-27B | 14 | 0,421 / 1,014 / 3,927 saniye | 7.077 |
| Qwen3-Embedding-8B | 1, dört girdili batch | 0,302 saniye | 89 |
| Unlimited-OCR | 5 | 0,342 / 0,984 / 13,439 saniye | 12.083 |

Toplam bildirilen kullanım **19.249 token**. Model türlerinin token sayıları kota veya parasal ücret bakımından eşdeğer varsayılmadı. Bunlar küçük, sıralı isteklerin süreleri; üretim gecikmesi, eşzamanlı kapasite veya p95 yük ölçümü değildir. İlk OCR başarısızlığı kullanımın önemli kısmını tüketti.

Hâlâ doğrulanmayanlar: en uzun context, kota/RPM, streaming, eşzamanlı araç çağrısı, çok görselli batch, karmaşık şemaların tümü, gerçek uzun belgeler ve uçtan uca lakehouse soru başarısı. Dönen `system_fingerprint` alanları kayıtta korunur; bu alanlar alttaki model ağırlıklarını bağımsız olarak doğrulamaz.

## Kanıt ve yeniden kontrol

- [results.json](results.json): İstekler, anahtarsız yanıtlar, kimlikler, süre ve usage. Görsel base64 gövdeleri hash referanslarıyla değiştirildi, tam embedding yerine boyut/norm özeti kaydedildi.
- [capability-profile.json](capability-profile.json): Yerelde doğrulanan gözlemler ve açık bilinmeyenler.
- [evaluate_results.py](evaluate_results.py): Kayıtlı cevapların JSON şeması, hücre, nonce ve metrik kontrolleri; ağ isteği yapmaz.
- [probe.py](probe.py): Rehberin üç modeliyle sınırlı canlı deney aracı; credential için `MIA_API_KEY` veya gizli terminal girdisi kullanır.

Yerel kontrol, repo kökünden:

```sh
.venv/bin/python docs/research/mia-probe-2026-09-10/evaluate_results.py
```

Kaynak: konuşmada paylaşılan **MIA Hackathon Rehberi** ve bu dizindeki canlı API gözlemleri. Harici LLM sağlayıcısı kullanılmadı; araştırma çıktıları uygulama kodundan ayrıdır.
