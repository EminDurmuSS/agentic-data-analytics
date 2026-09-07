# İlk paket eleştirisinin güncel değerlendirmesi

Bu dosya, başlangıç ZIP'ine yöneltilen eleştirileri 7 Eylül 2026 tarihindeki
güncel repo durumuyla karşılaştırır.

## Eleştiride haklı olunan ve hâlâ geçerli noktalar

1. Veri katmanı yarışmanın tamamı değildir. Agent, dinamik web araştırması,
   güvenli sorgu yürütme, analiz durumu, API ve kullanıcı arayüzü ayrıca
   geliştirilecektir.
2. Aylık kredi bakiyesi yeni kredi kullandırım akımı değildir. Bakiye farkı da
   geri ödemeler ve kapsam değişimleri nedeniyle gerçek kullandırım sayılmaz.
3. Birlikte hareket nedensellik kanıtı değildir. Faiz, reel kredi ve satış
   karşılaştırmaları önce betimleyici bulgu üretir.
4. EVDS'nin 52.696 serisinin bütün tarihsel gözlemleri yerelde değildir. Tam
   metadata kataloğu bulunur, gözlem olarak analitik değeri yüksek 60 seri
   seçilmiştir.
5. Aynı veri ve dönüşümler üzerinde devam eden kullanıcı oturumu henüz ürün
   olarak uygulanmadı.

## Artık giderilmiş eksikler

### BDDK aylık bülten

Başlangıç paketinde yalnız tüketici kredileri alt kümesi vardı. Güncel repoda
Ocak 2021-Haziran 2026 arasındaki 66 ay için 17 tablonun tamamı sektör toplamı
düzeyinde bulunur. 1.122 resmî istek ve 33.965 kaynak satırı doğrulandı.

### BDDK haftalık bülten

Başlangıçta yoktu. Güncel repoda 286 hafta ve 9 tablonun tamamına ait 2.574
resmî sayfa ile 249.070 ham hücre vardır. 147.154 ölçüm normalize edildi ve
47.584 döviz bileşeni toplam kontrolü geçti.

### BDDK FinTürk

Başlangıçta yoktu. Güncel repoda 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve
ayrı `YURT DIŞI` coğrafyası bulunur. 936.512 ölçüm üretildi.

### Gerçek kredi kullandırım verisi

TBB'nin yayımlanmış 21 çeyreklik tüketici kredisi raporu doğrudan indirildi.
Kullandırım tutarı ve kişi sayısı, dönem sonu bakiye ve kişi sayısından ayrı
tutulur. Haziran 2026 raporu kaynakta bulunmadığı için açık boşluk olarak
işaretlenir.

### BDDK kararlarının tam metni

10249, 10525, 10656 ve 11364 sayılı kararların resmî PDF'leri indirildi. Önceki
502 erişim notları güncellendi. Belgelerin aranabilir metinleri ve SHA-256
özetleri saklanır.

### Sorgulanabilir veri altyapısı

SQLite/Pandas başlangıcının yanında artık birleşik katalog ve self-contained
DuckDB bulunur. DuckDB 7 şema ve 32 tablo içerir. BDDK aylık, haftalık ve
FinTürk verileri, TBB raporları, 60 seçilmiş EVDS serisi, kalite tabloları ve
olay kayıtları tek sorgu yüzeyinde erişilebilirdir.

## Bilinçli kapsam sınırları

- BDDK aylık ve haftalık bültenlerde tüm tablolar sektör toplamı için alındı.
  FinTürk ve seçilmiş EVDS serileri banka grubu kırılımlarını ayrıca sağlar.
- EVDS'nin bütün tarihsel gözlemlerini indirmek yerine tam metadata kataloğu ve
  görevle ilişkili 60 seri tutulur. Bu, yanlış seri seçimini ve gereksiz veri
  hacmini azaltan bilinçli bir tasarımdır.
- TBB Haziran 2026 raporu yayımlanmadığı için mevcut değildir.
- Ağustos 2025 EVDS ve BDDK konut kredisi farkı otomatik düzeltilmez, kaynak
  kapsamı uyarısı olarak korunur.

## Net sonuç

İlk eleştiride BDDK, FinTürk, TBB ve geniş nedensellik verisinin eksik olduğu
tespiti doğruydu. Bu eksiklerin veri kaynağında mevcut olan kısmı güncel repoda
giderildi. Hâlâ tamamlanması gereken bölüm veri toplama değil, bu veri temelini
kullanan genel amaçlı analitik agent ve ürün katmanıdır.
