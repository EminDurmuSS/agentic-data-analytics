# İlk paket eleştirisinin güncel değerlendirmesi

Bu dosya, başlangıç ZIP'ine yöneltilen eleştirileri 9 Eylül 2026 tarihindeki
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
   metadata kataloğu bulunur, gözlem olarak analitik değeri yüksek 584 kaynak
   seri seçilmiştir.
5. Aynı veri ve dönüşümler üzerinde devam eden kullanıcı oturumu henüz ürün
   olarak uygulanmadı.

## Artık giderilmiş eksikler

### BDDK aylık bülten

Başlangıç paketinde yalnız tüketici kredileri alt kümesi vardı. Güncel repoda
Ocak 2021-Haziran 2026 arasındaki 66 ay için 17 tablonun tamamı ve 10 resmî
banka grubu bulunur. 1.122 resmî birleşik istek, 11.220 tablo-grup kaydı ve
339.650 kaynak satırı doğrulandı.

### BDDK haftalık bülten

Başlangıçta yoktu. Güncel repoda 286 hafta, 9 tablonun tamamı ve 7 resmî banka
grubu için 18.018 resmî sayfa ile 1.736.650 ham hücre vardır. 1.025.974 ölçüm
normalize edildi ve 333.088 döviz bileşeni toplam kontrolü geçti. Kaynaktaki
2.230 boş hücrenin tamamı
TL-only metriklerde uygulanamaz FX alanı olarak açıklandı, çözümlenmemiş boşluk
kalmadı.

### BDDK FinTürk

Başlangıçta yoktu. Güncel repoda 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve
ayrı `YURT DIŞI` coğrafyası bulunur. 936.512 ölçüm üretildi.
30.892 kaynak boşluğunun 29.564'ü yapısal, 1.328'i kaynakta raporlanmamış şube
hücresi olarak açıkça sınıflandırıldı. Bu 1.328 ham null değerin tamamı,
`SEKTÖR = MEVDUAT + KATILIM + KALKINMA VE YATIRIM` kimliğinin 1.782
il-çeyrekte sıfır farkla geçmesiyle analitik sıfır olarak kanıtlandı. Ham
değerler değiştirilmedi ve ayrı audit katmanı üretildi.

### Gerçek kredi kullandırım verisi

TBB'nin yayımlanmış 21 çeyreklik tüketici kredisi raporu doğrudan indirildi.
Kullandırım tutarı ve kişi sayısı, dönem sonu bakiye ve kişi sayısından ayrı
tutulur. Haziran 2026 raporu 9 Eylül 2026 tarihli resmî liste kontrolünde
bulunmadığı için açık boşluk olarak işaretlenir.

TBB Risk Merkezi'nin Haziran 2021-Haziran 2026 arasındaki 6 aylık bülteni de
indirildi. Bu bültenlerin 13 aylık grafiklerinden yarışma dönemi için 66 aylık
ve 5 metrikli eksiksiz panel çıkarıldı. İlk kez konut kredisi kullanan kişi
sayısı gerçek bir aylık olay sayısıdır, fakat parasal kullandırım tutarı
değildir. Bu nedenle yayımlanmayan TBB Haziran 2026 kullandırım tutarının yerine
geçirilmez.

### BDDK kararlarının tam metni

10249, 10525, 10656 ve 11364 sayılı kararların resmî PDF'leri indirildi. Önceki
502 erişim notları güncellendi. Belgelerin aranabilir metinleri ve SHA-256
özetleri saklanır.

### Sorgulanabilir veri altyapısı

SQLite/Pandas başlangıcının yanında artık birleşik katalog ve self-contained
DuckDB bulunur. DuckDB 10 şema ve 64 tablo veya view içerir. BDDK aylık,
haftalık ve FinTürk verileri, TBB ve Risk Merkezi raporları, 584 seçilmiş EVDS kaynak serisi, 1 açıkça
türetilmiş seri, TÜİK il satışları, il bazlı konut paneli, kalite tabloları ve
olay kayıtları tek sorgu yüzeyinde erişilebilirdir.

### Bölgesel konut ve hanehalkı finansmanı

81 ilin toplam, ipotekli, ilk el ve ikinci el konut satışları ile il bazlı
birim fiyatlar, il bazlı birim kiralar, bölgesel KFE ve YKKE serileri indirildi. Bunlar FinTürk konut
kredisi, tasarruf mevduatı, altın mevduatı ve nakdi kredi göstergeleriyle 22
çeyreklik panelde birleştirildi. Ayrıca 3 KKM ve 1 hanehalkı mevduat serisi
ulusal aylık analize eklendi. Ham kaynak null değerleri korunur. EVDS'de
satırı olmayan 10 il-ay ipotekli satış gözlemi, TÜİK'in aynı tabloda yayımladığı
`toplam satış = diğer satış` özdeşliğiyle sıfır olarak doğrulandı. Yarışma
dönemindeki 9 gözlem, 8 il-çeyrek toplamında ayrı kaynak ve SHA-256 provenance
ile kullanılır.

Kaynakta yayımlanmayan 162 il-çeyrek birim fiyatı resmî alanda null kalır.
Bunlar için aynı KFE bölgesi ve aynı çeyrekteki yalnız resmî il fiyatlarının
medyanından ayrı bir analiz proxy'si üretildi. Proxy kökeni ve akran il sayısı
saklanır. `analysis_ready_source` ile `analysis_ready_with_price_proxy`
birbirinden ayrıdır, bu nedenle proxy resmî gözlem gibi gösterilemez.

## Bilinçli kapsam sınırları

- BDDK aylık bültende 10, haftalık bültende 7 resmî banka grubu alınır.
- EVDS'nin bütün tarihsel gözlemlerini indirmek yerine tam metadata kataloğu ve
  görevle ilişkili 584 kaynak seri tutulur. Bunların 61'i ulusal, 519'u
  bölgesel konut, 4'ü hanehalkı finansmanı serisidir. Bu, yanlış seri seçimini
  ve gereksiz veri hacmini azaltan bilinçli bir tasarımdır.
- TBB Haziran 2026 çeyreklik tüketici kredileri raporu yayımlanmadığı için
  parasal kullandırım akımı mevcut değildir. Risk Merkezi aylık bülteni ayrı
  ölçülerle mevcuttur.
- Ağustos 2025 EVDS ve BDDK konut kredisi farkı otomatik düzeltilmez, kaynak
  kapsamı uyarısı olarak korunur.

## Net sonuç

İlk eleştiride BDDK, FinTürk, TBB ve geniş nedensellik verisinin eksik olduğu
tespiti doğruydu. Bu eksiklerin veri kaynağında mevcut olan kısmı güncel repoda
giderildi. Hâlâ tamamlanması gereken bölüm veri toplama değil, bu veri temelini
kullanan genel amaçlı analitik agent ve ürün katmanıdır.
