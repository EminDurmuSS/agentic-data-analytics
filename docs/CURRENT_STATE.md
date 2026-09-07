# Mevcut durum

Tarih: 7 Eylül 2026

## Sonuç

Zorunlu kaynak ailelerinin veri toplama aşaması tamamlandı. BDDK aylık,
BDDK haftalık, BDDK FinTürk ve seçilmiş TCMB EVDS gözlemleri yerel olarak
saklanıyor, kaynak hash'leriyle izleniyor ve doğrulanmış işlenmiş çıktılara
dönüştürülüyor.

Tek açık kaynak boşluğu TBB'nin Haziran 2026 tüketici kredileri raporudur.
7 Eylül 2026 itibarıyla kaynakta yayımlanmadığı için değer tahmin edilmedi.

## Zorunlu veri kaynakları

### BDDK aylık bülten

- Dönem: Ocak 2021-Haziran 2026
- 66 ay, 17 tablo, sektör grubu
- 1.122 / 1.122 resmî istek
- 33.965 kaynak satırı
- 133.485 semantik ölçüm
- 10.494 kümülatif kâr-zarar satırı aylık akıma dönüştürüldü
- Aylık akımların kaynak yılbaşından bugüne değerlerine yeniden toplam farkı: 0

### BDDK haftalık bülten

- Dönem: Ocak 2021-Haziran 2026
- 286 hafta, 9 tablo, sektör grubu
- 2.574 / 2.574 resmî HTML sayfası
- 249.070 kayıp vermeden saklanan ham hücre
- 147.154 normalize ölçüm
- 514 kaynak boş değeri, doldurulmadan korundu
- 47.584 `TP + YP = Toplam` kontrolü geçti
- Kaynak yuvarlamasından oluşan azami mutlak fark: 1 milyon TL

### BDDK FinTürk

- 22 çeyrek
- 7 tablo ve 7 banka grubu
- 81 il ve ayrı `YURT DIŞI` bölgesi
- 84.484 kaynak satırı
- 936.512 normalize ölçüm
- 76 sorgulanabilir metrik

## TCMB EVDS

### Metadata kataloğu

- 676 / 676 veri grubu tarandı
- 52.696 benzersiz seri metadata kaydı
- Metadata kaydı ile yerel gözlem varlığı ayrı işaretlenir

### Yerel gözlem kapsamı

Toplam 60 seçilmiş seri bulunuyor:

- 47 ana konut kredisi nedensellik serisi
- 11 ek politika, faaliyet, arz ve kira kontrolü
- 2 piyasa kontrolü: BIST 100 ve BIST altın piyasası

Bu seri grubu kredi faizi, kredi stoku, konut satışı, TÜFE, KFE, kredi arzı ve
talebi anketleri, politika faizleri, döviz, güven, işsizlik, sanayi, GSYİH,
tüketim, yapı izinleri, kiralar, altın ve hisse piyasası gibi alternatif
açıklamaları kapsar.

- Toplam kaynak gözlemi: 10.787
- Dolu gözlem: 8.289
- Kaynakta boş gözlem: 2.498
- Ana dönem: 2020-01-01 ile 2026-06-30
- 78 aylık ve 26 çeyreklik hizalama tabloları
- Çeyreklik değerler ara aylara forward fill edilmez

Bilinen iki EVDS sınırı:

- `TP.MK.KUL.YTL` Haziran 2026 yerine Mayıs 2026'da biter, değer uydurulmadı.
- BIST altın piyasası serisi seyrektir ve 24 Kasım 2025'te biter. Ana kontrol
  yerine çapraz kontrol olarak kullanılmalıdır.

## Destekleyici kaynaklar

### TBB tüketici kredileri

- Mart 2021-Mart 2026 arasında yayımlanmış 21 çeyrek
- 63 doğrulanmış XLS, PDF ve DOCX eki
- 252 ürün bazlı ölçüm
- Gerçek kullandırım akımı ile dönem sonu bakiye ayrı tutulur
- Haziran 2026 raporu kaynakta bulunmadığı için açık boşluktur

### Resmî karar ve yöntem belgeleri

- BDDK 10249, 10525, 10656 ve 11364 kararlarının tam PDF'leri
- 4 TCMB destek veya yöntem belgesi
- Her PDF için çıkarılmış aranabilir metin, dosya boyutu ve SHA-256
- Ayrı yürürlük tarihi belgede doğrulanmadığında `effective_date=null`
- Olay kayıtları nedensel sonuç değil, araştırma bağlamı olarak işaretlenir

## Çapraz kaynak kontrolleri

- BDDK aylık konut kredisi ile FinTürk 81 il toplamı en fazla yaklaşık yüzde
  0,0132 farklıdır.
- FinTürk `YURT DIŞI` değeri il toplamına gizlice eklenmez.
- TBB bakiyesi BDDK sektör toplamından yaklaşık yüzde 6,8 ile 9,9 düşüktür.
  Bu fark raporlayan banka kapsamından kaynaklanabilir ve hata diye kapatılmaz.
- EVDS ile BDDK arasında Ağustos 2025'teki kapsam farkı açık kalite uyarısıdır.
- Kredi stoku, stok değişimi ve yeni kullandırım akımı aynı ölçü değildir.

## Sorgulanabilir çıktı

- Birleşik katalog: 44 veri varlığı
- Toplam katalog metriği: 55.457
- Yerel gözlemi bulunan sorgulanabilir metrik: 2.819
- DuckDB: 7 şema, 32 tablo
- Aylık analiz tablosu: 66 benzersiz ay
- Çeyreklik analiz tablosu: 22 benzersiz çeyrek
- Haftalık BDDK ölçümleri DuckDB içine kopyalandı
- EVDS ana, ek nedensellik ve piyasa kontrolleri analiz tablolarına eklendi

## Henüz yapılmayan ürün parçaları

Veri tabanı hazırdır, yarışmanın bütünü henüz hazır değildir. Sonraki aşamada:

- Generic dosya ve URL ingestion aracı
- Kalıcı analiz nesnesi ve tablo sürümleme
- Agent koordinatörü ve kontrollü tool çağrıları
- Güvenli SQL yürütme
- Anomali, değişim noktası ve nedensellik iş akışları
- Dinamik web araştırması ve kaynaklı açıklama
- API, kullanıcı arayüzü ve CloudX entegrasyonu
- Görülmemiş finans dışı veriyle kabul testleri

geliştirilecektir.
