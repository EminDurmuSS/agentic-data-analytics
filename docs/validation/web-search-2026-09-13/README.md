# Web araştırması ve konuşmadan analize devam etme

## Sorun ve kapsam

Garanti raporu ile BDDK sektör aktifleri karşılaştırıldıktan sonra sorulan KKB kuruluş/ortaklık sorusunda resmî PDF okunmuş olmasına rağmen model banka listesini göremediğini söylüyordu. Araştırma kartındaki kısa metin, PDF'nin ilgili bölümünü model bağlamına taşımıyordu. Genel bağlantı etiketi de rapor başlığı yerine gösteriliyordu. Bazı denemelerde model soruya kredi kartı veya istenmeyen bir yıl ekliyordu.

Arama tarafında yapılandırılmış SearXNG'nin boş veya ilgisiz yanıtı alternatif sağlayıcıları devre dışı bırakıyordu. Bing'in benzer sonuçları sorgu değişse de tekrar dönüyor, genel bankacılık sözcükleri yanlış kurumu yeterince ayırt etmiyordu. Bir kaynak bulunması, hesaplama ve grafik isteğinin tamamlandığı anlamına gelmemelidir.

Bu değişiklikler kaynak okuma, model bağlamı ve arama sağlayıcıları arasındaki geçişi kapsar. Finansal hücre doğrulaması, birim dönüşümü ve çalışma alanı yayınlama sözleşmeleri korunur.

## PR incelemesi

[PR #7](https://github.com/EminDurmuSS/agentic-data-analytics/pull/7), `3bea80d523608a65c60bafdf60a863ddd718e8c3` sürümüyle incelendi. Gerçek parent `0020e933` üzerinde geçen 7 mevcut akış testinden 6'sı PR sürümünde başarısız oldu. Başlıca nedenler:

- Başarılı araştırmanın hemen ardından analiz çalışması tamamlanmış sayılıyor; içe aktarma, hesap ve grafik atlanıyor. Kaynak bulunamaması da tamamlanmış sayılabiliyor.
- Yalnız URL üzerinden tekrar denetimi, aynı PDF'nin başka sayfasını veya başka çıkarma stratejisini okumayı engelliyor. Başarısız bir indirme de ziyaret edilmiş sayılıyor.
- Bütün `OSError` hatalarını yutan değişiklik, enjekte edilen gerçek disk EIO hatasını gizliyor.
- Compose değişikliği uygulamanın mevcut localhost port sınırını kaldırıyor ve haricî arama servisi seçilse bile yerel servise başlangıç bağımlılığı getiriyor.

PR bütünüyle birleştirilmedi. Yerel SearXNG servisi fikri ve arama yayın tarihi metaverisi ayrı uygulanarak uyarlandı. Uygulama localhost'ta kalıyor, imaj digest ile sabit, ayarlar salt okunur ve oturum anahtarı başlangıçta üretiliyor. Uygulamanın açılması yerel arama servisinin sağlığına bağlı değil.

## Uygulanan davranış

- Bütün arama sağlayıcıları aynı normalleştirme, kurum/kapsam denetimi ve süre sınırını kullanıyor. SearXNG, Bing RSS, uygun finans raporu isteklerinde KAP ve DuckDuckGo yolları gerektiğinde deneniyor. Yalnız gezinme bağlantısı veya kimliği belirsiz rapor, doğrulanmış cevap sayılmıyor.
- Arama toplam 45 saniye, her sağlayıcı en fazla 10 saniye (KAP 15 saniye) kullanıyor. Araştırmanın arama ve kaynak indirme işlemleri ortak 120 saniye bütçeyi paylaşıyor. Sağlayıcı ve motor hataları ayrı kaydediliyor. CAPTCHA veya erişim engeli bildirilen motor başka ön yüzden tekrar denenmiyor.
- Kurum kimliği genel `bank`, `kredi`, `ortaklık` gibi sözcüklerden çıkarılmıyor. Açık konsolide/solo çelişkileri eleniyor. Alan adı kısıtları alternatif sağlayıcıya geçerken korunuyor.
- PDF araştırması, önizleme kesitinin yerine önceden okunmuş sayfalardaki ilgili bitişik pasajları getiriyor. Her pasajın fiziksel sayfası, satır sınırları ve kısaltılma durumu korunuyor. Bu, bütün PDF'nin okunduğu iddiası değildir. Başlık PDF metaverisi, kapak veya kayıtlı dosya adından geliyor.
- Kuruluş bilgisi, tarihsel kurucu isimleri, rapor dönemindeki ortaklar ve üyeler farklı roller olarak ele alınıyor. Güncel ortak listesi tarihsel kurucu listesine dönüştürülemiyor. Sorulmamış sahiplik yüzdeleri ve üye sayıları, kullanıcının banka seçme sorusunun önüne geçmiyor.

## Doğrulama

Canlı model denemeleri kullanıcının çalışma deposunun ayrı kopyalarında yapıldı. Kaynak depo değiştirilmedi; bağımsız doğru cevap ve finansal tutarlar model bağlamına eklenmedi. Yalnız sonuç metni değil, kayıtlı analiz, kaynak hücreleri, grafik ve gerçek kullanıcıya sunum çıktısı değerlendirildi.

Önceki sürümde özgün KKB sorusu 49 saniyede tamamlanmış görünmesine rağmen banka listesini vermedi. İlk düzeltme resmî rapordaki listeyi görünür yaptı; canlı deneme modelin bu kez güncel ortakları kurucular diye etiketlediğini gösterdi. Bu hata da kabul kontrolüne ve yanıt düzeltmesine dahil edildi.

Yerel SearXNG servisi gerçek sorgularla sınandı: KKB resmî ortaklık sayfası, banka yatırımcı ilişkileri sayfaları ve konsolide raporlar bulundu. Daha sonraki motor CAPTCHA/hız sınırı hatalarında İş Bankası raporu KAP yoluyla bulunabildi. Bu sonuçlar arama sağlayıcılarının sürekli erişilebilir olduğu garantisi değildir.

`İş Bankası ekle` devam isteği 114,9 saniyede 9 model kararıyla tamamlandı ve 24 bağımsız kabul kontrolünün tamamını geçti. KAP raporunun fiziksel 12. sayfasındaki `VARLIKLAR TOPLAMI` kaynak hücresi, 31 Mart 2026 konsolide toplamını **5.782.905.466 bin TL** olarak doğruluyor. Analize **5.782.905,466 milyon TL** ve sektör tutarına oranı **%11,627390989969799** kaydedildi. Garanti, BDDK ve önceki oran hücreleri birebir korundu. Beş serili çubuk grafik güncellendi; tutarlar ile yüzdeler kendi ölçeklerinde gösterildi. Kullanıcıya sunulan sonuçta model taslağındaki desteklenmeyen birleşik sektör oranı yer almıyor.

Bu sonuç ayrıca sunumdan tekrar geçirildi. Tek dönem `Mart 2026` olarak gösteriliyor. Grafik kaynağındaki KAP ek kimliği, kaynak kaydıyla eşleşen önceden okunmuş PDF başlığıyla değiştiriliyor. Analiz ve grafik dosyaları, sayılar ve özgün kaynak kimlikleri değişmiyor; görüntüleme sırasında PDF yeniden ayrıştırılmıyor.

Özgün KKB sorusunun son canlı denemesi **41,8 saniyede 4 model kararıyla tamamlandı**. Dokuz banka 2025 raporundaki ortaklar olarak kaynak bağlantısıyla verildi; tarihsel kurucu isimlerinin bu listeden doğrulanamadığı açıklandı. İş Bankası, Yapı Kredi ve Ziraat için Mart 2026 konsolide raporlarını aynı karşılaştırmaya ekleme önerileri sunuldu. Sahiplik yüzdesi tablosu, kanıtsız banka büyüklüğü yorumu veya tek PDF'den aylık geçmiş üretme önerisi verilmedi. Önceki analiz ve kullanıcı çalışma deposu korunmuştu. Serbest metinde isim listesinin bir kez tekrarlanması küçük bir sunum kusuru olarak kaldı.

Otomatik regresyon koşusu: `tests/agent`, `tests/app` ve `tests/architecture` altında **552 test ve 137 alt test geçti**. Playwright etkin çalıştırıldı; konuşma görünümü, gerçek işlem adımları, bağlama göre öneriler, grafik birimleri ve kaynak sunumu testleri atlanmadı. Son canlı denemenin açığa çıkardığı olumsuz isim doğrulama cümlesi ve kısa yanıt düzeltmesi sonrasında **187 odak test ve 32 alt test** ayrıca geçti. Karar ve tekrar bütçeleri artırılmadı.

## Sınırlar

Arama özetleri ve arama motorunun yayın tarihi, kaynağın kendisinden doğrulanmış bilgi sayılmaz. Güncel ortak listesinden geçmiş kurucu isimleri çıkarılamaz. PDF'nin okunmayan sayfaları kanıt değildir. Konsolide banka grubu ile BDDK sektör toplamının kapsamları farklı olabilir; hesaplanan oran resmî pazar payı değildir.
