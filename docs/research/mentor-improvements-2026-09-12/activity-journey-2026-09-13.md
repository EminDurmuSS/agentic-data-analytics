# Anlaşılır çalışma özeti

Eski panel, model çağrılarını ve araç başlangıç/bitişlerini ayrı ayrı sayıyordu. Gerçek PDF karşılaştırmasının 63 işlem kaydı, kullanıcıya 63 iş adımı gibi görünüyordu.

Yeni görünüm üç seviyeden oluşur:

- Kapalı kart: çalışmanın durumu ve gerçekten kullanılmış aşamalar.
- Açık kart: kaynaklar, veri hazırlığı, hesaplama, kontroller ve sunum. Her aşama açıldığında belge adı, incelenen sayfa veya eklenen satır gibi kayıtla desteklenen ayrıntılar görünür.
- İsteğe bağlı teknik kayıt: mevcut olayların tamamı. Bu liste yalnız açıldığında oluşturulur.

Özet, `app/activity.py` içindeki kayıt projeksiyonundan üretilir. Yeni model çağrısı, düşünce metni veya tahmini tamamlanma yüzdesi kullanılmaz. Başarılı belge incelemesi, belgenin tamamının okunduğu ya da verinin analize eklendiği anlamına getirilmez. Başarısız ve eksik işlemler ayrıca gösterilir; sonraki farklı bir işlemin başarısı önceki hatayı otomatik olarak kapatmaz.

Arayüz `app/static/activity.js` içinde aşamaları kimlikleriyle günceller. Canlı yenileme açık ayrıntıları ve klavye odağını korur. Sonradan eklenen bir aşama doğru sıraya yerleşir. Azaltılmış hareket tercihi desteklenir ve kaynak metinleri HTML olarak çalıştırılmaz.

Kaydedilmiş analiz, grafik, yanıt ve olaylar değiştirilmez. Yeni API alanları `workspace.latest_journey` ve `job.journey` içindedir. Mevcut teknik olay listeleri korunur.

Doğrulama, gerçek kayıt üzerinden salt okunur tarayıcı incelemesiyle ve çalışan, hata veren, kullanıcı yanıtı bekleyen durumlar için kontrollü Chromium senaryolarıyla yapılır. Bu senaryolar yeni canlı model denemesi değildir.

Uygulama ve mimari testleri, iki gerçek Chromium testi dahil, 72 test ve 16 alt test ile geçti. Kontroller sayfa kapsamını, başarısız denemenin başka bir kaynakla örtülmemesini, yayınlanmamış tablonun eklenmiş sayılmamasını ve kesinti sonrası durumunu kapsar. Yalnız plan oluşturulması, hesaplamanın tamamlanması sayılmaz. Tarayıcı kontrolleri açık panel/odak korunmasını, sonradan gelen aşama sırasını, isteğe bağlı teknik kayıtları, güvenli metni ve 390 piksel mobil görünümü sınar. Açık ayrıntılarla gönder düğmesinin görünürlüğü ve tıklanabilirliği mobilde ve 900 piksel yüksekliğindeki masaüstünde doğrulandı.

Gerçek önizlemedeki 63 ve 69 kayıtlı iki çalışma, beşer aşama olarak özetlendi. Her iki çalışma alanının sürümü, analiz başlığı ve kaydedilmiş yanıtları önceki değerleriyle aynı kaldı. Ölçüm ve ekran görüntüleri `mentor-improvements-2026-09-12/journey-after-2026-09-13/` kanıt dizinindedir.

Commit öncesindeki [tam test turu](activity-journey-full-tests.txt), **784 test ve 264 alt test** ile geçti (154,09 saniye; iki mevcut bağımlılık uyarısı). [Doğrulama kaydı](activity-journey-verification.json), kaynak dosyalarının hash değerlerini ve gerçek tarayıcıdaki 18 süreç, 13 yerleşim/durum, 17 tablo/grafik/kaynak kontrolünü içerir. Hiçbir yeni model çağrısı yapılmadı.
