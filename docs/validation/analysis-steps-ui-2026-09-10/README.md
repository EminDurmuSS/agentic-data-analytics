# Hesap adımları görünümü

Ham JSON yerine kayıtlı hesap planından oluşturulan dönem özeti, kaynak kartları ve numaralı işlem adımları gösteriliyor. Dönem eşleme, büyüme, fark, ölçek, oran ve sabit fiyatlara dönüştürme işlemleri, plandaki parametrelerle açıklanıyor. Ek işlem yoksa bu da açıkça belirtiliyor. Kaynak birimi, çıktıdaki dönüşmüş birimden ayrı ele alınıyor.

Orijinal plan, başlangıçta kapalı olan **Teknik ayrıntılar** bölümünde korunuyor. Kaynak verinin dönüşüm kuralları incelenmemişse, kendi dönemindeki değerleri göstermek ile hesaplama izni arasındaki fark açıklanıyor.

Çalışan yerel uygulama üzerinde Chrome ile doğrulandı:

- Reel Kesim Güven Endeksi, Nisan-Haziran 2026: kaynak, dönem, dönüşüm yapılmadığı bilgisi, uyarı ve açılabilir JSON.
- 60 aylık konut analizi: BDDK kaynak birimi, haftalık faizin dönem ortalaması, Ocak 2021 fiyatlarına dönüştürme formülü, önceki analiz bağlantısı ve kaynak hücresi penceresi.
- 1680 × 1150 masaüstü ve 390 × 844 mobil ekran; mobilde yatay sayfa taşması yok.
- Teknik ayrıntılar fare ve klavyeyle açılıp kapanıyor. Tablo satırları, grafik ve kaynak kanıtı görünmeye devam ediyor. Tarayıcı JavaScript hatası görülmedi.

Ekran görüntüleri: [masaüstü](desktop.png), [mobil](mobile.png). Kontrol kayıtları: [genel görünüm](browser-check.json), [konut analizi](housing-check.json).

Değişiklikler `app/static/app.js`, `app/static/app.css` ve `app/static/index.html` dosyalarıyla sınırlı. Bu panel kaydedilmiş analiz planını açıklar; kaynak verinin lakehouse'a alınmadan önceki bütün işlemlerini yeniden oluşturmaz. Backend hesaplamaları ve veri kümesi değiştirilmedi.
