# EVDS toplaması sırasında veri kalitesi bulguları

Bu bir ara kesit denetimidir; tam indirmenin veya son yayımlamanın bittiğini göstermez. SQLite kuyruğu tek salt okunur transaction içinde **2026-09-10T07:12:52.428334+00:00** zamanında okundu (Türkiye saati 10:12:52). O anda 429 başarılı, 188 `no_data`, 618 bekleyen, 2 çalışan, 7 yeniden denenebilir ve 7 bölünmüş üst iş vardı. Sonuçlar o anda doğrulanmış **617 terminal işe** ait değişmez deneme dosyalarının tamamından hesaplandı. Ağ isteği yapılmadı; kuyruk, kaynak dosyaları ve uygulama veritabanı değiştirilmedi.

Metadata evreni **52.696 seri**, arşiv işaretli metadata **22.092 seri**, boş birim alanlı metadata **18.869 seri**. Fiziksel kesitte **22.087 farklı seri**, bunların **17.818** tanesinde en az bir sayısal gözlem var. Bunlar yalnız incelenen terminal işlerdeki sayımlardır; kalan işler bu sayıları değiştirebilir.

| Kaynağın frekansı | Saklanan satır | Sayısal satır | Fiziksel seri | En geç dönem sonu |
|---|---:|---:|---:|---|
| AYLIK | 291.975 | 243.796 | 4.748 | 2026-06-30 |
| GÜNLÜK | 4.948.512 | 1.848.205 | 6.749 | 2026-06-30 |
| HAFTALIK(CUMA) | 593.372 | 545.010 | 3.673 | 2026-06-26 |
| YILLIK | 16.272 | 12.314 | 4.316 | 2025-12-31 |
| ÜÇ AYLIK | 45.969 | 44.976 | 2.199 | 2026-06-30 |
| İŞ GÜNÜ | 340.197 | 138.299 | 402 | 2026-06-30 |
| Toplam | 6.236.297 | 2.832.600 | 22.087 | 2026-06-30 |

## Dönem sınırı ve yıllık verinin anlamı

İncelenen **6.236.297 satırın hiçbirinde** `period_start < 2021-01-01` veya `period_end > 2026-06-30` yok. Hedefle hiç kesişmeyen dönem sayısı da sıfır. Yıllık serilerde **2026 satırı yok**, dolayısıyla 2026 yıllık sayısal gözlemi de yok. Bu kesitte tam yılı kapsayan bir 2026 değerinin Haziran'a kadar gerçekleşmiş veri gibi sunulduğunu gösteren bir bulgu çıkmadı.

| Yıllık kaynak dönemi | Saklanan satır | Sayısal satır |
|---|---:|---:|
| 2021 | 4.316 | 2.473 |
| 2022 | 4.301 | 2.616 |
| 2023 | 4.301 | 4.194 |
| 2024 | 2.505 | 2.497 |
| 2025 | 849 | 534 |

Yıllık native dönem her zaman 1 Ocak-31 Aralık aralığını ifade eder. Collector dönemleri hedef aralıkla kesişmeye göre kabul eder; bu yüzden ileride bir kaynak 2026 yıllık değeri döndürürse, ham yanıt korunurken bunun 2026 ilk yarıyıl gerçekleşmesi olduğu söylenemez. Şu anki sayısal bulgu böyle bir kayıt içermiyor; bu denetim nedeniyle ürün kodu değiştirilmedi. Son yayımdan önce sınır sorgusu yeniden çalıştırılmalı.

Haftalık kaynak, isteğin bitiminden sonraki ilk cumayı ham yanıta ekleyebiliyor. `0c57248e4525512e21d18022bf6aec0610a56cfa` işinde ham yanıt 288 tarih içeriyor; **2026-07-03** açıkça dışlanmış ve doğrulama kaydında saklanmış. Analitik satırlarda en geç cuma **2026-06-26**. Bu sınır satırını Haziran gözlemi saymamak mevcut davranışla doğrulandı.

## Seri kodundaki yıl, gözlem tarihi değildir

- `TP.UREN.S01.2015`: metadata grubu “Üretim Endeksi - Çalışılan Saat Başına (2015=100) (Arşiv)”. Kodun sonundaki **2015 baz yılıdır**. `3027e67f0d2612315629ed8894ab6bcb905a90aa` işinde 2021-Q1 ile 2023-Q4 arasında **12 sayısal gözlem** var; 2015 tarihli gözlem sızıntısı yok.
- `TP.OSYIBSB01.V.TRY.2015`: kaynak adı “Vadesi 2015 Yılında Dolacak Olanlar”. Buradaki **2015 vade kategorisidir**. `0397116b5aa1403e9bb8ef445dbdc9cc7cf1b48c` işinde 2021-01-01 ile 2021-08-06 tarihleri döndürülmüş, bu alt seride sayısal değer yok. Bu durum eski bir vade kategorisinin sıfır bakiyesi olduğunu kanıtlamaz.

Tarih filtresi seri kodundan veya başlığından çıkarılmamalı; kaynak dönem alanları kullanılmalı.

## `no_data`, kaynak null değeri ve takvim ayrı konulardır

Bu kesitteki **188 `no_data` işinin tamamında kaynak `items=[]` döndürmüş**. İsim anahtarları ve HTTP yanıtı doğrulanmış boş kaynak yanıtları söz konusu; 188 başarısız indirme veya 188 sıfır değer değil. Örnek `00e35ed3b8d409ccb82dbd3b7d1dd35784a869dc`, 100 arşiv aylık seriyi ister ve sıfır tarih satırı döndürür. Üyelerden `TP.UR.22.U03.TOP`, “Basım Yayım İmalatı-Diğer İşlerde Çalışanlar Ortalaması” arşiv serisidir.

Bunun yanında sayısal veri içeren karma partilerde **3.403.697 kaynak null satırı** saklanmış. Bunların tamamı `missing_kind=source_null_unresolved` ve `is_unresolved_missing=true`; sıfıra, tatil boşluğuna veya kesin seri başlangıcına dönüştürülmemiş. Partinin başarılı olması içindeki her serinin bütün tarihleriyle sayısal olduğu anlamına gelmiyor. Fiziksel olarak gözlenen 4.269 serinin bu kesitte hiç sayısal değeri yok; bunların 2.769'u arşiv, 1.500'ü arşiv işaretli değil.

| Takvim konumu | Günlük satır | Günlük sayısal satır |
|---|---:|---:|
| Hafta içi | 3.531.894 | 1.393.025 |
| Hafta sonu | 1.416.618 | 455.180 |

**Hafta sonu otomatik olarak veri olmayan gün değildir.** `TP.TRD200324T11`, “Devlet İç Borçlanma Senetlerinin Gösterge Niteliğindeki Değerleri” grubunda, 2023-06-24 Cumartesi için **1570.249471**, 2023-06-25 Pazar için **1570.348104** döndürmüş. Kaynak işi `016078cf4c94945e86ec6f79337ee129b19c6009`. Ayrı `İŞ GÜNÜ` frekansında incelenen 340.197 satırın tamamı hafta içi; 138.299'u sayısal. Günlük ve iş günü frekansları aynı takvim varsayımıyla temizlenemez.

## Arşiv ve birim bilgisi

Arşiv işaretli fiziksel serilerin **2.892** tanesinde hedef aralıkta toplam **335.922 sayısal satır** bulundu. Bu nedenle bütün arşiv serilerini plan dışında bırakmak veri kaybettirirdi. Arşiv etiketi, hedef dönemde veri bulunmadığı veya bütün eksik tarihlerinin yapısal olduğu anlamına gelmiyor.

Birim alanı boş **8.275 fiziksel serinin 4.744'ünde** sayısal gözlem var. Bu serilerde toplam 1.844.238 sayısal satır bulunuyor. Kaynak metadata alanının boşluğunu aynı gruptaki başka bir serinin birimiyle doldurmak güvenilir değil:

- Aynı arşiv grubunda `TP.BEKODTUFE.BT1` başlığı “Gözlem Sayısı”, `TP.BEKODTUFE.BT10` başlığı ise olasılık ve “(%)” içeriyor. İkisinin de metadata birim alanı boş; ikisi de 2021-01 ile 2023-11 arasında 35 sayısal gözlem taşıyor.
- Kanıt işi: `1868984d9feeeb98e76f5d83ce8e795dfceaee4b`; ham yanıt SHA256: `40e2cd43f1d2fa8912a7981fbc9a2c30fe269fdb168ded856f0f082624204878`.
- Başlıklar sonraki ölçü bazlı inceleme için kanıt sağlar. Grubun bütün üyelerine “yüzde” atamak gözlem sayısını yanlış sınıflandırır. Toplu kaynaklar için mevcut `review_required` davranışı bu ayrımı korumalı.

## Tekillik ve kanıt sınırı

Bütün incelenen terminal Parquet dosyaları birlikte tarandığında yinelenen **(series_code, period)** anahtarı **0**, fazladan yinelenen satır **0**, aynı dönemde çelişen sayısal/null varyant sayısı **0** çıktı. Bu sonuç gelecekteki revizyonların çakışmayacağı iddiası değildir; publisher'ın varyantları koruyan çakışma denetimi gerekli olmaya devam ediyor.

Frekans, arşiv durumu ve boş yanıt örnekleri için seçilen aşağıdaki **12 işin** ham yanıt SHA256'sı (gzip açılmış yanıt baytları) ve istek SHA256'sı (kaydedilen istek baytları) yeniden hesaplandı: **12/12 geçti**. Genel satır sayımları 617 işin Parquet çıktısından, ham hash çapraz kontrolü ise bu 12 örnekten gelir. Tam yayımlama aracı bütün terminal işleri ayrıca ham yanıttan yeniden doğrular.

| Kaynak iş kimliği | Frekans | Durum | Ham tarih satırı | Ham yanıt SHA256 |
|---|---|---|---:|---|
| 004cf5ba53e843745ff06cd9f0862d86a22d8599 | İŞ GÜNÜ | succeeded | 147 | 9ff086c600c1a14a50549949fbaa181c973e8bf818902f32d99b980eddda1943 |
| 00e35ed3b8d409ccb82dbd3b7d1dd35784a869dc | AYLIK | no_data | 0 | c0a32d20d104d50383c8812900e33e279e92d24f6fcf2fabd607a5b110fd44a9 |
| 01574740628c5b94fc921f9ea27f49521d6d5e28 | AYLIK | succeeded | 66 | cfcfc4ba1fd05c0d7c28602c76887f98a87c04d7e0750761cdb02a4a4a186605 |
| 016078cf4c94945e86ec6f79337ee129b19c6009 | GÜNLÜK | succeeded | 900 | 35bcc23d2c87c12bf30bedca142cb136fcc5a957d4f6823d6134e31958b0fb7f |
| 0397116b5aa1403e9bb8ef445dbdc9cc7cf1b48c | HAFTALIK(CUMA) | succeeded | 32 | 856305b4e76fae4a06f18ce5d95112e36c8dc4819d0a6156e36689cf191fdeff |
| 04208b688c2064769415a0be699f24c39e22a17a | GÜNLÜK | succeeded | 900 | a65a56994b0b095a6f2da1a66ba4894f3b8642e25f297a74a9112a679b58c0dd |
| 09f9b326c459b68d43d05be23a1d45b41871c2be | ÜÇ AYLIK | succeeded | 21 | 03280d0ccef1713d6be4e2fb68107fa849664b51eed13445a8174e3bd308e4f2 |
| 0a265068c62cac78ae620f9198e7907633f3c0e5 | AYLIK | succeeded | 10 | cb447e4f4e4ff45be6e9a37c966a8d8c1e61fec80c4d67ce97579367fbb640ca |
| 0c57248e4525512e21d18022bf6aec0610a56cfa | HAFTALIK(CUMA) | succeeded | 288 | cf203a04e120acd7242597d73c67fc3da942cadaeb9d5ab0354c317938f2eeac |
| 185b902391e8aa82848cc986e7d788f71004f975 | YILLIK | succeeded | 3 | 48d448d52cdc9c4fc1d778f99c9b59a4eab0326e3134aaad1d14029111c605c3 |
| 3027e67f0d2612315629ed8894ab6bcb905a90aa | ÜÇ AYLIK | succeeded | 12 | 93d27890350f7284ebece4cc67d9a336c17379f1540a5811381fcc8dcfc633b9 |
| 3eaf38e514d554804da40855ce0e155cc3d38808 | YILLIK | succeeded | 1 | 943cda4faeef78168a0d8e4390250b70d206e5820b09650d4c26b148df250cc9 |

Seçilen kaynak yanıtlarında `frequencyConversion` sözlüğü de korunuyor. Bu alanın adından “veriye kesin dönüşüm uygulandı” sonucu çıkarılmadı; istek frekansı metadata frekansıyla eşleştirildi, değerler kaynak yanıtından alındı.

Denetlenen terminal iş kümesinin kimliği, sıralı iş kimlikleri arasına LF konularak hesaplanan SHA256: `6ac76f5869f37d406c7c3f0c2a2e4a37106e9ef41d4f3fe06c63a0ab41b46492`. İş kimliğinin kaynağı `tmp/evds_bulk/queue.sqlite` içindeki `jobs` tablosu; deneme dizini `artifact_path` alanından çözülür. Bu dosyada anahtar, token veya makineye özgü mutlak kaynak yolu bulunmaz.

Son raporda ayrı tutulması gereken iddialar: **istek kapsamının tamamlanması**, **sayısal gözlem kapsamı**, **birim ve dönüşüm anlamlarının doğrulanması**. Bu ara kesit ilk iddianın tamamlandığını henüz söylemez; son ikisi de ilkinden otomatik çıkmaz.

## Ek sayı hassasiyeti kontrolü

Bu ek kontrol daha sonraki **2026-09-10T07:17:19.216525+00:00** kesitindeki **783 terminal işin 3.651.915 sayısal satırına** aittir; yukarıdaki 617 işlik kesitle karıştırılmamalı.

| Kontrol | Satır sayısı |
|---|---:|
| Mutlak değeri 2^53'ten büyük | 12 |
| Kaynak ondalık metninde 15'ten fazla anlamlı basamak | 965 |
| Kaynak ondalık metninde 17'den fazla anlamlı basamak | 0 |
| NaN veya sonsuz sayısal değer | 0 |
| Sabit ondalık biçiminin dışında sayısal kaynak metni | 0 |

Anlamlı basamak sayımında işaret, ondalık ayırıcı, baştaki sıfırlar ve sondaki gereksiz sıfırlar çıkarıldı. Büyük değerlerin tamamı **TP.IMFMBM.IDN**, “Endonezya (IDN) Geniş Para Arzı (M3) (Ulusal Para Birimi)” serisinde. Metadata birim alanı boş. Kaynak işi **782d03ea5d79a55f3e0c54ed254986bb0491dbf3**; en büyük kaynak değer **9595253595145390.00000000**, dönem **2025-06**.

2^53 sınırının aşılması tek başına bu gözlemlerin bozulduğu anlamına gelmedi: bu 12 kaynak değerin hepsi ilgili float aralığında tam temsil edilebilen çift tam sayılar. **Decimal.from_float(value) == Decimal(value_raw)** karşılaştırması 12/12 geçti; kanıtlanmış tam sayı kaybı **0**.

İki koşulun birleşimindeki **977 aday satırın tamamında** kaynak ondalık metni ile DOUBLE'ın Python/JSON ondalık gösterimi ayrıca karşılaştırıldı: **Decimal(value_raw) == Decimal(str(value))**, 977/977 geçti. Bu adaylarda kaynağın sayısal ondalık gösteriminden farklı bir JSON değeri saptanmadı. Örnek: **TP.HPBITABLO1.18**, “3.M3”, kaynak birimi **bin TL**, dönem **2026-06-26**, iş **5080015adbee1151d4dd8846549f6602e4d68ed2**. Kaynak metni **30042363242.51113500**, DOUBLE'ın ondalık gösterimi **30042363242.511135**; değişen yalnız gereksiz sondaki sıfırlar.

Bu sonuç binary kayan noktayla her olası aritmetik işlemin matematiksel olarak tam olduğu iddiası değildir. İncelenen gerçek native değerlerde tam sayı kaybı veya aday kaynak ondalık metinlerinden farklı servis gösterimi bulunmadı; kaynak metni **value_raw** ve ham yanıt içinde korunuyor. Bu kontrol nedeniyle kod değiştirilmedi.

## Kuyruk tamamlandıktan sonraki bütün kaynak denetimi

Son kaynak kesiti **2026-09-10T07:41:51.454873+00:00** tarihinde alındı. Makinece okunabilir kanıt [final-date-key-audit.json](final-date-key-audit.json) dosyasındadır. Bu kesitte **52.696 metadata serisi**, **883 succeeded**, **364 no_data**, **10 split** ve **14 superseded** iş var; bekleyen, çalışan veya başarısız son durumlu iş yok. Tarama yalnız yayımlamaya aday **1.247 succeeded/no_data** işin son deneme dosyalarını içerir. Split üst işler ve açık denetim kaydıyla yerine başka iş konmuş superseded işler sayısal satırlara katılmadı.

Bu bölüm kaynak toplamanın son durumunu ve kaynak dosyalarının tarih/anahtar tutarlılığını gösterir. Tam yayın paketinin ham yanıt doğrulaması, atomik etkin veritabanı değişimi ve agent sorgusu doğrulamasının bitmiş olduğu iddiasını taşımaz. Yayınlama sırasında birleştirme belleği sınırına takılan ilk deneme etkin veritabanını değiştirmedi; birleştirme, yalnız gerçek tekrar anahtarlarına pahalı karşılaştırma uygulayacak şekilde düzeltildi.

| Kontrol | Sonuç |
|---|---:|
| Kaynak gözlem satırı | 13.519.266 |
| Sayısal kaynak satırı | 6.019.172 |
| Kaynak null satırı | 7.500.094 |
| En az bir gözlem satırı olan seri | 41.909 |
| En az bir sayısal gözlemi olan seri | 35.652 |
| Yinelenen (series_code, period) anahtarı | 0 |
| Çelişen dönem değeri | 0 |
| Hedef dönemle hiç kesişmeyen kayıt | 0 |
| Hedef dışına uzayan sayısal dönem | 0 |
| Eksik/null işaretlerinde tutarsızlık | 0 |
| NaN veya sonsuz sayısal değer | 0 |

Sayısal seriye sahip olmak, serinin bütün hedef dönemlerinde sayısal değere sahip olması anlamına gelmez. İşin `no_data` olması da metadata serisi sayısı ile aynı ölçü değildir: tek iş birden çok seri içerebilir; sayısal değer bulunan bir işin bazı üyelerinde yalnız açık kaynak null değerleri bulunabilir.

| Native frekans | Kaynak satırı | Sayısal satır | Fiziksel seri |
|---|---:|---:|---:|
| AYLIK | 632.548 | 532.651 | 10.395 |
| GÜNLÜK | 10.977.204 | 4.059.427 | 9.796 |
| HAFTALIK(CUMA) | 1.076.777 | 986.655 | 6.801 |
| HAFTALIK(ÇARŞAMBA) | 1.430 | 1.430 | 5 |
| YILLIK | 31.861 | 23.371 | 8.483 |
| ÜÇ AYLIK | 123.763 | 119.685 | 5.956 |
| İŞ GÜNÜ | 675.683 | 295.953 | 473 |

Önceki ara kesitlerde henüz gelmemiş **91 yıllık 2026 kaydı**, doğal dönem sonu **2026-12-31** olduğu için hedef sonu **2026-06-30** dışına uzanıyor. **91 kaydın tamamı kaynak null**, sayısal yıllık 2026 değeri **0**. Hepsi **dbd48b0bbd6b6b1d165487b7ab0bbc5e6f144e63** işinde, **TP.SEKBILY01A.01** ile **TP.SEKBILY01A.S** arasındaki kaynak seri kümesinde. Gzip açılmış ham yanıt SHA256: **db9cd982ee87ac18e9e1eafc671261bfaad283751378177b8c34d6a886e5ac4a**. Bu doğal yıllık dönemler Haziran'a kesilmedi ve altı aylık gerçekleşmiş değer olarak yorumlanmadı. Bu 91 null dışında hedef dışına uzayan başka dönem yok.

Son tarama, kaynak dosyalarını tek tek okuyup özel geçici disk DuckDB tablosuna aktardı; **256MB bellek sınırı ve tek iş parçacığı** kullandı. Önce anahtar tekrarları sayıldı, değer çelişkisi kontrolü yalnız bulunan tekrarlar üzerinde uygulandı. Kaynak kuyruğu, ham yanıtlar ve yayımlanan veritabanları değiştirilmedi. Son terminal iş kümesinin SHA256 kimliği **de285092fc565015247fc4a3d02f3dbaf8c4f4d04074ddb5f6cf23f5ef06b062**, sabitlenmiş metadata kataloğunun SHA256'sı **ccdd130fb752defb99a295857b56e61b088f8c49708722f9b6e303f454e576da**.
