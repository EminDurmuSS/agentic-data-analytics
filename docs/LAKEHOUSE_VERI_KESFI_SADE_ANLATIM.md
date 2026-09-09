# Lakehouse veri keşfi: kullanım ve sonuçların anlamı

[Veri keşfi notebooku](../lakehouse_veri_kesfi_ve_iliskiler.ipynb), mevcut
`data_pipeline/lakehouse/analytics.duckdb` dosyasını salt okunur olarak inceler.
68 hücrede veri envanteri, kaynak ilişkileri, kalite kontrolleri ve örnek
analizler bulunur. Bu belge, çıktıların nasıl okunacağını açıklar; sayısal
sonuçların güncel kaynağı notebookun yeniden çalıştırılmış çıktılarıdır.

9 Eylül 2026 tarihindeki birleşim tabanında **10 şema ve 64 tablo veya view**
vardır. Bunlar notebookun ilk bölümünde veritabanından sayılır. Yeni veri veya
şema eklendiğinde satır sayıları, kapsam oranları ve analiz sonuçları değişebilir.
Kayıtlı bir notebook çıktısı, başka bir veritabanı kopyasının güncel durumunu
kanıtlamaz.

## Başlatma

Ortamı [README'deki kurulum adımlarıyla](../README.md#kurulum-ve-doğrulama)
hazırlayın. Notebooku repo kökünden veya repo içindeki bir dizinden, bu ortamın
Python çekirdeğiyle açıp hücreleri baştan sona çalıştırın. İlk kod hücresi
veritabanını üst dizinlerde arar ve `read_only=True` ile açar. Sabit bir Windows
veya Linux kullanıcı dizinine ihtiyaç duyulmaz.

Sayısal bir bulguyu paylaşırken kullanılan veritabanı sürümünü ve notebookun
çalıştırılma zamanını da kaydedin. Kaynak dosyaları veya veri katmanları
birbirinden farklıysa önce [güncel durum belgesine](CURRENT_STATE.md) ve
[veri hazırlama açıklamasına](../data_pipeline/README.md) bakın.

## Veriler neyi kapsıyor?

Kaynak tablolarında bankacılığın birçok alanı bulunur. Notebookun ekonomik
örnekleri, hazır panellerden yararlanarak ağırlıklı olarak konut ve krediye
odaklanır. Bu örnekler bütün lakehouse kapsamını temsil etmez.

| Şema | İçerik ve kullanım |
| --- | --- |
| `bddk` | Aylık, haftalık ve FinTürk ölçümleri; metrik sözlükleri ve kaynak kayıtları |
| `evds` | TCMB serileri, gözlemler ve frekans hizalama kayıtları |
| `tuik` | İl bazında konut satışları ve EVDS ile karşılaştırmalar |
| `tbb` | Tüketici kredilerinin bakiyesi, gerçek kullandırım akımı ve kaynak boşlukları |
| `risk_center` | Risk Merkezi konut kredisi göstergeleri, kaynak sürümleri ve revizyonlar |
| `regional` | İl-çeyrek paneli, il eşleştirmeleri ve kaynak kalite bilgileri |
| `analysis` | Ulusal aylık ve çeyreklik konut kredisi analiz panelleri |
| `catalog` | Metrik tanımları, veri varlıkları ve tablo/kaynak manifesti |
| `quality` | Kaynaklar arasında kapsam ve tutarlılık denetimleri |
| `evidence` | Analize bağlam sağlayan resmî olay ve belge kayıtları |

`analysis.housing_credit_monthly` ve `analysis.housing_credit_quarterly`,
kaynakların seçilmiş birleşimleridir. BDDK'nın konut dışındaki kredi,
mevduat, menkul kıymet ve diğer göstergeleri kendi kaynak tablolarında bulunur.

## Temel kavramlar

- **Metrik:** Ölçülen büyüklük ve tanımı. Adı yanında birimi, kapsamı ve dönemi gerekir.
- **Stok:** Belirli bir tarihteki bakiye. Kredi stokunun artışı, o dönemdeki yeni kullandırım tutarı değildir.
- **Akım:** Belirli bir dönemde gerçekleşen hareket. TBB kullandırım tutarı buna örnektir.
- **Kümülatif değer:** Başlangıçtan veya yıl başından itibaren biriken toplam. Dönemlik akımla ayrı tutulur.
- **NULL:** Bilinmeyen, yayımlanmayan veya uygulanmayan değer. Kendiliğinden sıfıra çevrilmez.
- **Proxy:** Resmî gözlemin yerine geçmeyen, yöntemi ve kaynağı açıklanmış yaklaşık gösterge.
- **Kaynak izi:** Bir değerin hangi kaynak dosyasından ve dönüşümden geldiğini izleyen kayıt.
- **Mantıksal ilişki:** Bir tablonun anahtarının ilgili sözlük veya kaynak tablosunda karşılığının bulunması.

## Envanter ve kalite çıktıları nasıl okunur?

**Şema, tablo, kolon ve depolama görünümü**, veritabanının fiziksel kapsamını
anlatır. Satır toplamı, dosya büyüklüğü veya sütun sayısı tek başına veri kalitesi
ölçüsü değildir. Ölçüm satırlarıyla katalog kayıtlarını toplamak da bağımsız
ekonomik gözlem sayısı vermez.

**NULL profili**, seçili analiz panellerinde her sütunun eksikliğini gösterir.
Bir olay başlığının boş olmasıyla kredi tutarının boş olması aynı anlama gelmez.
`analysis_ready` veya fiyat hazırlığı bayrağı, her sütunun dolu olduğunu garanti
etmez. Örneklem, yapılacak hesapta kullanılan alanların kapsamı ve eksikliğiyle
birlikte seçilmelidir.

**Mantıksal ilişki kontrolleri**, ölçümlerin metrik sözlüğünde veya kaynak
kaydında karşılığı olup olmadığını sınar. `orphan_rows=0` bağlantının kopuk
olmadığını gösterir. Bu kontrol, iki farklı kurumun aynı ekonomik kavramı
ölçtüğünü kanıtlamaz. `SKIP` olan bir kontrol yapılmamıştır; `CHECK` olan sonuç
incelenmelidir.

**Katalog kapsamı**, adı ve tanımı bilinen metriklerle yerelde gözlemi bulunan
metrikleri ayırır. EVDS'nin geniş metadata kataloğu, bütün serilerin tarihsel
gözlemlerinin indirildiği anlamına gelmez. `observation_available` ve
`coverage_pct` yerel erişim kapsamını anlatır; veri kalitesi puanı veya yarışmanın
bütün veri kapsamının tamamlandığına ilişkin bir ölçü değildir. Diskteki ham
paketlerin tamamının lakehouse içinde temsil edildiği de varsayılmamalıdır.

**Banka grubu çıktıları**, ölçüm, dolu/boş değer, metrik ve hafta kapsamını
özetler. Farklı metriklerin sayısal değerleri toplanarak veya ortalanarak banka
grupları arasında ekonomik büyüklük sıralaması yapılmaz. Ekonomik karşılaştırma
aynı metrik, para boyutu, dönem ve uyumlu grup tanımlarıyla ayrıca kurulmalıdır.

**Frekans ve anahtar kontrolleri**, aylık/çeyreklik panellerin dönem kapsamını ve
tekrarlı kayıtları gösterir. Çeyreklik bir değeri ara aylara kopyalamak yeni
aylık gözlem üretmez. Seri bazındaki hizalama yöntemi ayrıca incelenmelidir.

**Kümülatif akım kontrolü**, ilgili BDDK değerleri için aylık akımlar tekrar
toplandığında aynı yılın kaynak kümülatif değerine dönülüp dönülmediğini sınar.
Ocak, önceki yılın Aralık değerinden çıkarılmaz. Bu kontrol dönüşümün aritmetik
tutarlılığını gösterir; kaynak serinin ekonomik tanımını ayrıca doğrulamak gerekir.

**Uzlaştırma ve kalite özeti**, TÜİK-EVDS ortak gözlemlerindeki eşleşmeleri,
dönem tekrarlarını ve açıklanmış kaynak boşluklarını görünür kılar. Kaynak
kimliğiyle doğrulanan analitik sıfırlar, ham NULL değerinden ayrı değerlendirilir.
Bir uyuşmazlıkta il, tarih, metrik kodu ve kaynak sürümü birlikte incelenir.

## Örnek analizler ne gösteriyor?

**Aylık ulusal görünüm**, kredi stokunu, reel stok değişimini, kredi faizini ve
ipotekli satış payını yan yana getirir. Nominal büyüme ile reel büyüme aynı
şey değildir. Birlikte hareket eden seriler, sonraki araştırma için soru
üretebilir; kendiliğinden bir etki açıklaması oluşturmaz.

**BDDK, FinTürk, TBB ve Risk Merkezi karşılaştırmaları**, ortak dönemde farklı
kurumların ölçümlerini gösterir. Bakiye, yeni kullandırım ve borçlu kişi sayısı
birbirinin yerine kullanılmaz. Risk Merkezi ve BDDK farkının düzenli görünmesi,
farkın nedenini tek başına kanıtlamaz. Kurum kapsamı, kaynak tanımı, birim,
yuvarlama ve revizyonlar ayrıca kontrol edilmelidir. TBB için yerel kayıtlarda
belgelenmiş son kullandırım dönemi 2026Q1'dir; eksik 2026Q2 değeri sıfır değildir.

**İl satış tablosu ve göreli segmentler**, satış, ipotekli satış payı ve kişi
başına kredi gibi alanları karşılaştırır. Düşük/orta/yüksek gruplar, seçilmiş
örneklem içindeki göreli dilimlerdir. Risk notu, yatırım önerisi veya kalıcı
sınıflar olarak okunmaz. Fiyat kullanılmayan bir segmentasyonda yalnız fiyat
eksikliği nedeniyle bir ili dışlamak gerekmez; kullanılan alanların doluluğu
kontrol edilir.

**Gecikmeli korelasyon**, faiz değişimi ile sonraki aylardaki reel kredi stoku
değişiminin birlikte hareketini tarar. Gözlem sayısı ve katsayılar çalışma
çıktısından okunmalıdır. Seçili gecikmeler, enflasyon ve diğer ortak değişimler
kontrol edilmeden bir politika etkisini kanıtlamaz.

**Panel regresyonları**, il içindeki ve il/çeyrek etkileri hesaba katıldıktan
sonraki ilişkileri keşfetmek için örnektir. Kullanılan örneklem ve ölçü birimleri
sonuçla birlikte okunmalıdır. İki yönlü modelde il ve çeyrek etkileri,
dengesiz paneli de ele alan gösterge matrisi projeksiyonuyla çıkarılır.
Katsayılar nedensel etki, güven aralığı veya istatistiksel anlamlılık sonucu
olarak sunulmaz. Bu notebooktan otomatik ekonomik karar çıkarılmaz.

**Hareketli medyan ve MAD sapma taraması**, reel kredi değişiminde yakın döneme
göre alışılmadık gözlemleri işaretler. Bir sapma işareti, kalıcı bir rejim veya
trend kırılması bulunduğu anlamına gelmez. Bu örnek, yarışmadaki genel değişim
tespit aracının tamamlandığını göstermez. İşaretlenen dönemin kaynak kalitesi
ve ekonomik bağlamı ayrıca araştırılmalıdır.

## Resmî fiyat ve proxy nasıl ayrılıyor?

İl panelindeki resmî fiyat sütununda boşluk korunur. Ayrı proxy sütunu, aynı
bölge ve çeyrekteki resmî il fiyatlarından üretilen medyanı ve dayandığı il
sayısını taşır. Bölgesel KFE/YKKE de bağımsız resmî il endeksi olarak sunulmaz.

Notebook, resmî fiyatla hazır, proxy kullanımına açık ve belirli akran sayısı
koşulu taşıyan örneklemleri ayrı sayar. Bu sayımlar, modelin üç örneklemde de
çalıştırıldığı veya sonuçlarının dayanıklı olduğunun sınandığı anlamına gelmez.
Fiyat kullanılan analizde hazırlık bayrağına ek olarak modelin diğer alanları
için de eksiklik kontrolü gerekir. Fiyat kullanılmayan analizde fiyat kaynaklı
hazırlık bayrağı tek başına genel örneklem filtresi yapılmaz.

Proxy'nin dayandığı il sayısı, kökeni ve örnekleme etkisi sonuçla birlikte
raporlanmalıdır. Resmî ve proxy değerler aynı kanıt düzeyinde gösterilmez.

## Benchmark ve otomatik kontroller

[Benchmark betiği](../test_lakehouse_performance.py), aynı lakehouse'u salt
okunur açarak bütünlük, şema/kaynak izi ve seçili sorgu sürelerini raporlar.
Repo kökünden çalıştırılır:

```bash
.venv/bin/python test_lakehouse_performance.py
```

Bu komut [kayıtlı JSON raporunu](../lakehouse_benchmark_results.json) yeniler.
Başka bir veritabanı veya ayrı rapor dosyası için:

```bash
.venv/bin/python test_lakehouse_performance.py \
  --database data_pipeline/lakehouse/analytics.duckdb \
  --output /tmp/lakehouse-benchmark.json \
  --iterations 5
```

Zamanlamalar makine, veritabanı sürümü, önbellek ve yineleme sayısına bağlı
ölçümlerdir. Hızlı bir sorgu doğru ekonomik hesap yapıldığını kanıtlamaz;
bütünlük kontrolleri ve zamanlama sonuçları ayrı okunur. Atlanan bir kontrol,
başarılı bir doğrulama değildir.

Projenin otomatik doğrulama testleri ayrıca çalıştırılır:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Notebookun bütün hücrelerini çalıştırmak, SQL ve analiz örneklerinin mevcut
veritabanında yürüdüğünü gösterir. Otomatik testlerin ve kaynak doğrulamalarının
yerine geçmez.

## Genel platform açısından sınırı

Ortak katalog alanları kaynak, metrik, frekans, birim ve kaynak izini birlikte
keşfetmeye yardımcı olur. Finans dışındaki bir kaynak için de bu yaklaşım
kullanılabilir; ancak yeni kaynağın anlamı, indirme/normalizasyon kuralları ve
kalite kontrolleri ayrıca tanımlanmalıdır.

Buradaki SQL sorguları ve analiz örnekleri elle hazırlanmış veri keşif
çalışmalarıdır. Bir doğal dil agentının, yeni URL işleme akışının veya gelen
veriye göre beceri oluşturabilen genel bir platformun uygulanmış fonksiyonları
olarak değerlendirilmemelidir. Sağlam veri temeli üzerine kurulacak uygulama
kararlarını incelemek ve doğrulanabilir örnekler sağlamak için kullanılırlar.
