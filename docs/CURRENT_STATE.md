# Mevcut durum

Tarih: 8 Eylül 2026

## Sonuç

Zorunlu kaynak ailelerinin veri toplama aşaması tamamlandı. BDDK aylık,
BDDK haftalık, BDDK FinTürk ve seçilmiş TCMB EVDS gözlemleri yerel olarak
saklanıyor, kaynak hash'leriyle izleniyor ve doğrulanmış işlenmiş çıktılara
dönüştürülüyor.

TBB'nin Haziran 2026 tüketici kredileri raporu 8 Eylül 2026 itibarıyla kaynakta
yayımlanmamıştır. Ayrıca bazı kaynakların kendi yayın takvimindeki veya tanımındaki
boşluklar açıkça sınıflandırılır. Hiçbir değer tahmin edilmez.

## Zorunlu veri kaynakları

### BDDK aylık bülten

- Dönem: Ocak 2021-Haziran 2026
- 66 ay, 17 tablo, 10 resmî banka grubu
- 1.122 / 1.122 resmî birleşik istek
- 11.220 tablo-grup kaydı
- 339.650 kaynak satırı
- 1.334.850 semantik ölçüm
- 104.940 kümülatif kâr-zarar ölçümü aylık akıma dönüştürüldü
- Aylık akımların kaynak yılbaşından bugüne değerlerine yeniden toplam farkı: 0

### BDDK haftalık bülten

- Dönem: Ocak 2021-Haziran 2026
- 286 hafta, 9 tablo, 7 resmî banka grubu
- 18.018 / 18.018 resmî HTML sayfası
- 1.736.650 kayıp vermeden saklanan ham hücre
- 1.025.974 normalize ölçüm
- 2.230 kaynak boş değerin tamamı `TRY = TOTAL` olan TL-only metriklerin FX
  hücreleridir ve `source_not_applicable` olarak sınıflandırıldı
- Çözümlenmemiş haftalık kaynak boşluğu: 0
- 333.088 `TP + YP = Toplam` kontrolü geçti
- Kaynak yuvarlamasından oluşan azami mutlak fark: 1 milyon TL

### BDDK FinTürk

- 22 çeyrek
- 7 tablo ve 7 banka grubu
- 81 il ve ayrı `YURT DIŞI` bölgesi
- 84.484 kaynak satırı
- 936.512 normalize ölçüm
- 76 sorgulanabilir metrik
- 30.892 kaynak boş değer: 7.128 `source_not_applicable`, 22.436
  `structural_undefined`, 1.328 `source_not_reported`
- 29.564 yapısal boşluk ayrı işaretlendi, 1.328 raporlanmamış şube hücresi
  tahmin edilmeden açık bırakıldı

## TCMB EVDS

### Metadata kataloğu

- 676 / 676 veri grubu tarandı
- 52.696 benzersiz seri metadata kaydı
- Metadata kaydı ile yerel gözlem varlığı ayrı işaretlenir

### Yerel gözlem kapsamı

Toplam 61 seçilmiş kaynak seri ve 1 türetilmiş seri bulunuyor:

- 47 ana konut kredisi nedensellik serisi
- 11 ek politika, faaliyet, arz ve kira kontrolü
- 3 piyasa kaynak kontrolü: BIST 100, aktif BIST altın kapanış fiyatı ve eski
  seyrek altın serisi
- 1 türetilmiş seri: aktif altın fiyatının açık `0.001` katsayısıyla
  TL/kg'dan TL/grama çevrilmiş hâli

Bu seri grubu kredi faizi, kredi stoku, konut satışı, TÜFE, KFE, kredi arzı ve
talebi anketleri, politika faizleri, döviz, güven, işsizlik, sanayi, GSYİH,
tüketim, yapı izinleri, kiralar, altın ve hisse piyasası gibi alternatif
açıklamaları kapsar.

Katalogda olup bu 61 serilik başlangıç setinde bulunmayan bir seri,
`tools/EVDS_Talep_Uzerine_Indirme_Araci.py` ile ad veya kod üzerinden seçilip
aynı ham istek, ham cevap, eksiklik sınıflandırması ve SHA-256 sözleşmesiyle
indirilebilir. Bu akış 8 Eylül 2026 tarihinde önceden seçilmemiş bir turizm
gelirleri serisinin 12 aylık gözlemiyle canlı doğrulandı.

- Toplam kaynak gözlemi: 12.482
- Dolu gözlem: 9.901
- Kaynakta boş gözlem: 2.581
- Ana dönem: 2020-01-01 ile 2026-06-30
- 78 aylık ve 26 çeyreklik hizalama tabloları
- Çeyreklik değerler ara aylara forward fill edilmez
- Kaynak boşlukları `calendar_non_observation`, `before_series_start`,
  `source_not_published` ve `interior_source_null` olarak ayrılır

Bilinen EVDS sınırları:

- `TP.MK.KUL.YTL` Haziran 2026 yerine Mayıs 2026'da biter, değer uydurulmadı.
- Eski `TP.ALTINPIYASA.KAP05` serisi seyrektir ve 24 Kasım 2025'te biter.
  Yalnız tarihsel çapraz kontrol olarak tutulur.
- Ana altın kontrolü `TP.ALTINPIYASA.KAP02`, 30 Haziran 2026'ya kadar doludur.
  Kaynak birimi TL/kg'dır; TL/gram dönüşümü ayrı türetilmiş seri olarak tutulur.

## Destekleyici kaynaklar

### TBB tüketici kredileri

- Mart 2021-Mart 2026 arasında yayımlanmış 21 çeyrek
- 63 doğrulanmış XLS, PDF ve DOCX eki
- 252 ürün bazlı ölçüm
- Gerçek kullandırım akımı ile dönem sonu bakiye ayrı tutulur
- Haziran 2026 raporu 8 Eylül 2026 tarihli resmî liste kontrolünde bulunmadığı
  için açık kaynak boşluğudur

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

- Birleşik katalog: 50 veri varlığı
- Toplam katalog metriği: 55.458
- Yerel gözlemi bulunan sorgulanabilir metrik: 2.821
- DuckDB: 7 şema, 40 tablo
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
