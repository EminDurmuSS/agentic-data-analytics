# Aylık kredi stoklarının toplulaştırılması: ikinci kontrol

7 Eylül 2026; `credit_monthly_metadata.txt`, üç kredi kataloğu ve iki gerçek EVDS yanıtı üzerinden kontrol edildi.

## Doğru toplam ve birim

TCMB aylık istatistiklerinin özgün birimi **bin TL**. Konut kredi stokları için ayrı banka grupları: mevduat `TP.KM.B11`, kalkınma/yatırım `TP.KM.A11`, katılım `TP.KB.KRE10`. Bunların toplamı **bizim hesapladığımız TCMB kapsamındaki üç banka grubunun konut stoku toplamıdır**. BDDK'dan indirilmiş tek bir sektör serisi değildir.

- Milyon TL = özgün değer / 1.000.
- Milyar TL = özgün değer / 1.000.000.
- Önerilen kolon: `housing_stock_tcmb_domestic_allbanks_million_try`.
- Ocak 2021: 276.760.846 bin TL = **276.760,846 milyon TL**.
- Haziran 2026: 800.685.464 bin TL = **800.685,464 milyon TL**.

Yerleşiklik/kapsam: yurt içi şube işlemleri; genel metaveride TL ve yabancı para işlemlerinin TL karşılığı olarak yayımlandığı belirtiliyor. Seçilen stok serileri özgün para türünü ayırmıyor. Dolayısıyla yalnız TL cinsinden açılmış kredi veya kur etkisinden arındırılmış kredi büyümesi denmemelidir. TGA ve reeskontun bu özel konut satırlarına dahil/hariç olması genel metaveriden kesinleştirilmiş değildir; kolon adına eklenmemelidir.

## Hiyerarşi tuzağı yakalandı

Kalkınma/yatırım kataloğunda bireysel kredi kartı `TP.KM.A14`, tüketici kredileri `TP.KM.A10` altında gösteriliyor. Diğer iki katalogda kartlar tüketici toplamının dışında. Ancak **78 ay × 3 banka grubu = 234 sayısal eşitlik** kontrolünde:

`tüketici kredileri = konut + taşıt + ihtiyaç ve diğer`

tam olarak sağlandı; tüm artıklar sıfır. A14'ün katalog üst-seri etiketinden hareketle kartları tüketici toplamına dahil saymak yanlıştır. Tüketici toplamı ve bireysel kart bakiyesi ayrı kolonlar olmalı. Tüketici+kart toplamı ayrıca türetilecekse tüketici bakiyesine kart bakiyesi bir kez eklenir.

## Türetme kuralları

1. Stok farkı `stok[t] - stok[t-1]` **stok değişimi**dir. Yeni kredi kullandırım tutarı, başvuru sayısı veya doğrudan kredi talebi değildir; ödemeler, değerleme ve sınıflama hareketleri etkiler.
2. Stok büyümesi `100*(stok[t]/stok[t-k]-1)`; faiz değişimi **yüzde puan** olarak `faiz[t]-faiz[t-k]`.
3. TÜFE 2025=100 kullanılıyorsa `milyon_TL_stok*100/TÜFE`, ortalama 2025 fiyatlarıyla milyon TL cinsinden türetilmiş reel stoktur. Kaynakta yayımlanan resmî reel kredi serisi değildir.
4. Reel KFE göstergesi `(KFE/TÜFE)/(KFE_başlangıç/TÜFE_başlangıç)*100` olarak türetilir. Ayrı baz yıllarındaki iki endeksi çıkarmayın.
5. Haftalık faizin aylık ortalaması yıllık oran birimini korur. Stokları zaman boyunca toplamayın; yıl sonu stok için aralık/son gözlem gerekir.
6. Mevduat+kalkınma/yatırım stoku, katılım hariç faiz serisine banka türü bakımından yaklaşır; yine de stok yurt içi şubelerle, faiz bazı yurt dışı şube işlemlerini de içeren kapsamla ölçülür. Kusursuz kapsam eşitliği yoktur.

Kaynak: [TCMB Aylık Para ve Banka İstatistikleri metaverisi](https://www.tcmb.gov.tr/wps/wcm/connect/f3ad1a37-6d59-4a0b-b0d7-09b51cd7a73f/MetaveriAPB%C4%B02018.pdf?MOD=AJPERES), yerel resmî katalog/ham yanıtlar. Bu kontroller dış yayındaki her stok seviyesini bağımsız olarak eşleştirme anlamına gelmez.
