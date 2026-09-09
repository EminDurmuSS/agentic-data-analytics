# Canlı uygulama doğrulaması, 10 Eylül 2026

Gerçek Kloudeks Qwen çağrılarıyla çalışan uygulama doğrulandı. Bu küçük senaryo
kümesi genel bir başarı yüzdesi veya yarışma sonucu garantisi değildir.
Tam araç sıraları, başarısız ilk denemeler, kod hash'leri ve bağımsız kontroller
[live-validation.json](live-validation.json) içinde kayıtlıdır.

- 316 otomatik test ve 82 alt test geçti. İki uyarı Starlette test istemcisi bağımlılıklarının kullanımdan kaldırılma bildirimleridir.
- Gerçek kaynaklar üzerindeki 20 kabul kontrolü geçti; üç ham kaynak ayrıca hash ile doğrulandı.
- Canlı sonuçlara uygulanan 19 bağımsız kontrol geçti.
- Tarayıcıda hata yok; tablo, grafik, hücre kanıtı ve 390 px mobil görünüm doğrulandı.
- 2^53 üzerindeki tam sayılar HTTP ve tarayıcı sınırından eksiksiz geçti.

## Geçen canlı senaryolar

1. BDDK sektör net kârı: Ocak, Şubat, Mart 2026 için 87.249, 82.152 ve 119.287 milyon TL; toplam 288.688 milyon TL.
2. Finanssız boş snapshot üzerine sentetik klinik CSV'si: tarih etiketleri açık kuralla normalize edildi, 12 aylık ziyaret/personel değerleri değişmeden hesaplandı.
3. Aynı klinik tablosunda üç istatistik aracı: Eylül sıçraması işaretlendi, kalıcı medyan kayması bulunmadı, Pearson ilişkisi kaynaklı artefakta kaydedildi.
4. Sentetik görseldeki 107, 114, 121 değerleri doğru okundu; görsel tablo hücre kontrolü yapılmadan yayımlanmadı.
5. 60 aylık konut tablosu, kredi sütununu reel değerle değiştirme ve KFE ekleme aynı konuşmada tamamlandı. Faiz hücreleri ve sonraki adımda bütün eski sütunlar birebir korundu. İlk analiz değişmedi.

Konut reel kredi sonucu bağımsız Gold hesabıyla karşılaştırıldı. En büyük
mutlak fark `5.820766091346741e-11` milyon TL, kayan nokta hassasiyeti düzeyindedir.

## Denemelerden çıkan düzeltmeler

İlk başarısız denemeler silinmedi ve başarı olarak sayılmadı. Türkçe çekim
farkları ve kaynak/grup adları keşifte düzeltildi. Modele gönderilen keşif
kartları küçültüldü. Belge birim alıntıları ile dönem başlangıcı/sonu tarih
etiketleri açık sözleşmeye bağlandı. Yerel kaynak ve çıktı frekansı aynıysa
`native` kullanılacağı hata mesajında ilgili sütun adıyla belirtildi.
Mevcut sütunu değiştirme ile yeni sütun ekleme araç açıklamasında ayrıldı.

Modelin düşünme modu konut bağlamında üç kez 4.096 token sınırında kesildi.
Aynı bağlamda `enable_thinking=false` denemesi 302 token ile araç çağrısı
üretti. Bu bir protokol kontrolüydü; plan ayrıca semantik doğrulamadan geçmek
zorundadır. Son başarılı senaryo bu ayar ve normal veri kontrolleriyle çalıştı.

## Kapsam sınırı

EVDS'nin bütün tarihsel gözlemleri henüz alınmış değildir. Yerel kuyruk
52.696 seri için planlandı; mevcut 599 fiziksel serinin 587'sinde hedef
dönem sayısal gözlemi vardır. İki sınırlı yeni istekte veri dönmedi; bunlar
kapsam artışı sayılmadı. İndirme kuyruğu çıktısı aktif snapshot'a kendiliğinden
yayımlanmaz. Geniş soru kümesiyle canlı değerlendirme ayrıca yapılmalıdır.

[Kurulum ve kullanım](implementation.md) · [Masaüstü görünümü](app-desktop.png) ·
[Grafik](app-chart.png) · [Mobil görünüm](app-mobile.png)
