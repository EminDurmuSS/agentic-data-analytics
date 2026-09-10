# EVDS tarihsel toplama ve lakehouse yayını

**10 Eylül 2026: toplama, doğrulama ve uygulamaya yayın tamamlandı.**
Yayın kimliği `9785f908a87de00bb801881a`. Bekleyen veya çözümlenmemiş
başarısız istek yok. Kesin sayılar ve kabul kanıtı [completion.json](completion.json)
dosyasındadır.

| Ölçüm | Sonuç |
|---|---:|
| Hedef aralığı sorgulanıp doğrulanan katalog serisi | 52.696 |
| Fiziksel gözlemi bulunan seri | 41.909 |
| Sayısal gözlemi bulunan seri | 35.652 |
| Saklanan kaynak hücresi | 13.519.266 |
| Sayısal hücre | 6.019.172 |
| Kaynağın boş bıraktığı hücre | 7.500.094 |
| Yinelenen veya çelişen seri-dönem | 0 |
| Doğrulanmış son kaynak isteği | 1.247 |
| Tekrarlar dahil istek denemesi | 1.303 |

Yeni veritabanı 621.031.424 bayt, 76 tablo/view ve 44.712 fiziksel gözlemi
bulunan metrik içerir. SHA256:
`c2e6c9fd749efe5c96e9e4dc23b925a792cce57b3d96ad42cc02969d8395b81a`.
Uygulama yeni çalışma alanlarında bu sürümü kullanıyor. Kaynakta sayısal
verisi olmayan 17.044 seri için sayı üretilmedi.

404 test ve 147 alt test, mevcut gerçek veri kabulünün 20 kontrolü, yeni
günlük/aylık/yıllık üç doğal frekans sorgusu geçti. Ayrıca çalışan uygulama
üzerinden gerçek Qwen modeli `TP.GY1.N2` serisini keşfetti, Nisan-Haziran
2026 için **100.6, 103.3, 103.5** değerlerini hesap servisine sorgulattı ve
Haziran değerini `/items/65/TP_GY1_N2` kaynak hücresine bağladı. Tablo, CSV,
ham yanıt SHA256'sı ve kaynak hücresi ayrıca doğrulandı.
[Yerel canlı örnek](http://127.0.0.1:8870/?workspace=workspace_8552cd5099c647cba312ec34d11ac60a).

Bu tek model denemesinde sağlayıcının final metni aynı bloğu iki kez
içeriyordu; kayıtlı tablo ve kaynak kanıtı doğruydu. Bu deneme geniş bir soru
kümesinin başarı oranını ölçmez.

Hedef, katalogdaki 52.696 EVDS serisinin her biri için 1 Ocak 2021-30 Haziran
2026 aralığını kaynaktan sorgulamak, dönen gözlemleri doğrulamak ve agent'ın
doğal frekansta okuyabildiği lakehouse sürümüne bağlamaktır. Bir isteğin
tamamlanması, serinin bütün dönemlerinde sayısal değer bulunduğu anlamına
gelmez. Birim ve ekonomik dönüşüm incelemesi de ayrı bir durumdur.

## Çalıştırma

```bash
.venv/bin/python -m tools.complete_evds_history
.venv/bin/python -m tools.evds_bulk_collection status
```

İlk komut planlama, kesintiden devam eden toplama, ham yanıt doğrulaması,
birleşik katalog, DuckDB derlemesi ve aktif veritabanına yayını yapar. Ayrı
çalıştırılan collector tamamen bittiyse son aşama
`python -m tools.complete_evds_history --publish-only` ile yürütülür.

Kuyruk `tmp/evds_bulk/queue.sqlite`, değişmez kaynak yayınları
`data_pipeline/evds/full_catalog/releases/`, son tamamlanma raporu
`.lakehouse-runtime/evds-builds/LATEST.json` konumundadır. Büyük kaynak
yayınları Git'e eklenmez; kendi SHA256 manifestleriyle doğrulanır. Repodaki
seçilmiş kaynak paketi ayrı tutulur.

## Toplama ve kaynak doğruluğu

`tools/evds_bulk_collection.py`, aynı kaynak grubu, frekans ve varsayılan
toplama kuralındaki en fazla 100 seriyi birlikte ister. Günlük ve iş günü
verileri en fazla 900 takvim günlük parçalara ayrılır. Böylece kaynağın
gözlem sınırından dolayı yalnız son dönemleri döndürmesi engellenir;
ayrıca şüpheli sınır yanıtları doğrulamada reddedilir. Bütün katalog için
başlangıç planı 1.237 iştir. Hatalı bir parti gerektiğinde daha küçük
işlere bölünür; bölünmüş üst iş analitik yayına girmez.

HTTP erişimi Python ile EVDS'nin herkese açık web veri endpoint'inden
yapılır. Bu endpoint, resmî anahtarlı API ile aynı erişim sözleşmesine sahip
varsayılmaz. Kaynak arayüzündeki gelecekteki değişiklikler ayrı uyarlama
gerektirebilir. İlgili resmî kaynak:
[EVDS 3 kullanım kılavuzu](https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=8).

SQLite kuyruğu iş sahipliğini, deneme sayısını, gecikmeli tekrar denemeyi ve
ortak istek aralığını saklar. Bu çalışmada iki worker tek küresel saniyelik
istek aralığını paylaşır. HTTP hız sınırı ve geçici servis hataları yeniden
denenir. Kesinti, tamamlanmış ham yanıtları veya önceki denemeleri silmez.

Her denemede istek JSON'u, sıkıştırılmış ham yanıt, HTTP bilgisi, hashler,
doğrulama sonucu ve normalize gözlemler saklanır. Seri kimliği, doğal tarih
biçimi, sayısal değer, yinelenen dönem ve istenen aralık kontrol edilir.
Kaynak null değeri sıfıra çevrilmez. `value_raw` özgün sayısal metni korur.
Kaynağın haftalık sınıra eklediği doğrulanmış komşu tarihler ayrı kaydedilir
ve hesap dışına alınır. Yıllık kaynakta tamsayı dönen `Tarih` alanı da
yalnız yıllık frekansta kabul edilir.

`bie_tsanaymt2021` grubunda kaynak yanıtının `items` görünümü yalnız toplam
sanayi sütununu içeriyor; diğer 40 seri `transposedItems` görünümünde 66 ay
boyunca açık JSON null değerleri taşıyor. Tekil boş alt seri isteği kaynakta
HTTP 500 veriyor. Bu hata boş gözlem sayılmıyor. Collector yalnız aylık,
beklenen bütün tarihleri dönen, seri kimlikleri ve iki görünümde ortak
hücreleri birebir uyuşan yanıtta açık transpose null değerlerini kabul
ediyor. Kısmen eksik sütun, eksik tarih, çelişen değer veya sayısal fallback
reddediliyor. Bu özel grupta 2.640 null hücrenin her biri gerçek
`/transposedItems/...` adresiyle korunuyor; `items` içinde varmış gibi
gösterilmiyor. Normal hücreler `/items/...` adresine bağlanıyor.

Eski ayrıştırıcıyla bölünmüş bir parti, `retry-validated-batch --job-id ...`
komutuyla ancak saklanan asıl yanıtın hash ve yeni ayrıştırıcı kontrolleri
geçerse yeniden kuyruğa alınabilir. Alt işlerin geçmişi `job_supersessions`
denetim tablosunda korunur ve durumları açıkça `superseded` olur. Asıl
partinin aynı istekle yeni bir ağ denemesi başarıyla tamamlanmadan bu
ilişkiler yayımlanamaz. Hatalı eski denemeler başarılı diye yeniden
etiketlenmez; deneme sayıları sıfırlanmaz.

## Yayın ve uygulama bağlantısı

`tools/publish_evds_bulk.py`, kuyruğu tek salt okunur transaction ile okur.
Bütün tamamlanmış işleri ham yanıtlarından yeniden ayrıştırır, Parquet
hücrelerini bunlarla karşılaştırır ve isteklerin seri-tarih aralığını
kanıtlar. Yalnız geçerli bir yanıtın varlığı değil, katalogdaki her serinin
hedef aralığının tamamının istenmiş olması kontrol edilir.

Yayın, doğal frekanstaki gözlemleri, kaynak vintagelarını, olası çelişkileri,
seri kataloğunu ve istek kapsamını ayrı Parquet dosyalarında tutar. Çelişen
değerler korunur ve canonical gözlemlere sessizce seçilmez. `CURRENT.json`
değişmez yayının kimliğini ve manifest hashini atomik olarak işaret eder.

Birleşik katalog bu yayın kimliğine sabitlenir; derleme sırasında daha yeni
bir yayın çıkması katalog ile gözlemlerin karışmasına yol açmaz. Önceden
incelenmiş 599 EVDS serisinin mevcut kaynak bağları korunur. Yeni seriler
`evds.full_catalog` kaynağına bağlanır. Kaynağın birim metni korunur; henüz
doğrulanmamış ekonomik tür ve dönüşümler `review_required` durumundadır.

`tools/complete_evds_history.py`, bekleyen, çalışan, yeniden denenebilir veya
kalıcı başarısız iş varsa aktif veritabanını değiştirmez. Eksiksiz istek
kapsamı doğrulanınca ayrı bir katalog ve DuckDB üretir. Veritabanı kopyasının
SHA256'sı doğrulanır, aktif yazma günlüğü olmadığı kontrol edilir ve dosya
atomik olarak değiştirilir. Yeni çalışma alanları bu sürümü kullanır;
mevcut çalışma alanları kendi snapshot'ını korur.

Agent yeni serileri katalogdan bulabilir, doğal frekansta sorgulayabilir ve
bir değeri kaynak işi, ham yanıt ve satırına kadar açıklayabilir. Yarıyıllık
seriler kendi dönemlerinde okunur. Ayda iki kez yayımlanan serilerde
kaynağın gerçekten gözlenen tarihleri kullanılır; 15'i veya ay sonu gibi
varsayımsal tarihler üretilmez. Bu iki seyrek frekansta dönüşümler henüz
açılmamıştır.

## Kanıt ve sınırlar

İlk gerçek pilot altı seri ve beş istekle günlük, haftalık ve aylık yolları
doğruladı: 4.720 satır, 1.356 kaynak null değeri, sıfır çelişki. Pilot yayının
kimliği `e4436da6b9ca193a56caa38e`. Altı serinin istek kapsamı tamamlandığı
halde bütün katalog kapsamı doğru biçimde eksik raporlandı. Yeni fiziksel
kaynağa bağlanan `evds:TP.KTF10` için agent veri servisi 287 haftalık satır
üretti. Bu, canlı dil modelinin bütün soru çeşitlerinde başarı kanıtı değildir.

Kaynak ara kesitindeki takvim, arşiv, boş birim, null ve sayı hassasiyeti
bulguları [ayrı denetim notunda](data-findings.md) yer alır. Notlardaki
kesit sayıları nihai yayının toplamlarıyla karıştırılmamalıdır. Son yayının
kimliği, kesin toplamları ve uygulama kabulü tamamlanma raporunda kaydedilir.

Mevcut 599 birincil EVDS serisinin hedef dönemdeki 39.068 doğal gözlemi yeni
indirmeyle karşılaştırıldı: 36.494 sayısal çift tamamen eşit, 2.574 çift iki
tarafta da null. Eksik/fazla dönem, frekans uyuşmazlığı veya sayısal revizyon
farkı bulunmadı. Ayrıca 19 ham kaynak hücresi SHA256 kontrolüyle karşılaştırıldı.
[Seri bazında karşılaştırma kaydı](seed-overlap-audit.json).

Tam veri hacmindeki ilk offline birleştirmede 256 MB DuckDB sınırı aşıldı;
aktif veritabanı değişmedi. Birleştirme, önce tekil ve yinelenen anahtarları
ayıracak biçimde düzenlendi. Geniş değer karşılaştırması ve sıralı vintage
seçimi yalnız gerçek tekrarlar üzerinde çalışır. Yayınlama bir worker ve
1024 MB DuckDB bütçesi kullanır; ham yanıtları yeniden doğrulamak için ağ
isteği yapmaz. Python'ın ayrı parti belleği bu DuckDB sınırına dahil değildir.

İstek kapsamı tamamlandıktan sonra da kaynakta yayımlanmamış dönemler,
arşiv serilerin sınırlı ömrü, null hücreler ve doğrulanmayı bekleyen ekonomik
anlamlar açık kalabilir. Bu durumlar yeni veri uydurularak kapatılmaz.
