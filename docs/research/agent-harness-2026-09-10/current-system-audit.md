# Mevcut sistemin bağımsız denetimi

İncelenen commit: `dc757c291fc4cdeecb510f0c6e506c759a4bf60a`.
Tarih: 10 Eylül 2026. Bu çalışma araştırma ve planlama içindir; üretim kodu
ve kaynak veri değiştirilmedi. Gerçek servis üzerinde 30 temsilî çağrı,
doğrudan kaynak SQL karşılaştırmaları ve ayrı bir sağlık veritabanı deneyi
yapıldı. Snapshot, CSV ve analiz yazımları yalnız geçici dizinlerdeydi.
Kaynak DuckDB SHA-256 değeri inceleme öncesi ve sonrası aynıdır.

İstekler, cevaplar, sayısal sonuçlar, kaynak referansı durumları ve ek sorgular
[current-system-audit.json](current-system-audit.json) içinde saklandı.
`P01` gibi kimlikler bu dosyanın `experiments` kayıtlarını gösterir.
Deneyler bütün soru uzayını veya Qwen başarısını temsil etmez; seçilen veri
ve araç davranışlarını gerçekten çalıştırarak sınar.

## Sonuç ve uygulama kararı

Mevcut lakehouse, KOBİ ve konut örneğinin ötesinde kullanılabilir bir veri
temelidir. Aylık net kâr, FinTürk altın mevduatı ve şube sayısı, işsizlik,
sanayi endeksi, TBB ihtiyaç kredisi kullandırımı ve sentetik sağlık akımı
aynı servisle okunabildi. Doğal çeyrek tarihleri FinTürk, TBB ve EVDS'de
çalıştı. Bu nedenle önce depolama teknolojisini değiştirmek için bir
uygulama gerekçesi bulunmadı.

Bununla birlikte `ready` durumu, serbest bir agent'ın önerebileceği her
hesabın güvenli olduğu anlamına gelmiyor. Akımın çeyreklik son ayını
çeyreklik değer gibi taşıma ve üretim endeksini enflasyon deflatörü olarak
kabul etme açıkları, model bağlanmadan önce kapatılmalıdır. Metrik/boyut
bulma, grup bazında hesap, yeni belge alımı ve yarışmanın istatistik
araçları da tamamlanmış değil. Sonraki iş, mevcut depolama üzerinde bu
sınırları açıkça tanımlayan agent yürütme katmanıdır.

## Hazır olanlar ve kanıtın sınırı

| Parça | Gerçek durum | Kanıt ve sınır |
| --- | --- | --- |
| Kaynak katmanı | 70 ilişki, 10 şema; 599 fiziksel EVDS serisi | Metadata katalog büyüklüğü tüm gözlem kapsamı değildir |
| Metrik durumları | 2.824 `ready`, 564 `review_required`, 14 `no_numeric`, 52.099 `metadata_only` | Bunlar bütün kaynakların metrik sayılarıdır, bağımsız seri veya satır sayısı değildir |
| Aylık semantik veri | Kümülatif ve aylık değer ayrımı uygulanmış | P08: Haziran net kârı 106.642 milyon TL; ham kümülatif değer ayrıca korunur |
| Doğru aylık akım toplamı | Üç aylık tam veriyle çeyrek toplamı çalışıyor | P09: 2026 Q1 net kârı 288.688 milyon TL |
| FinTürk doğal çeyrek | İl ve banka grubu seçimiyle çalışıyor | P11 ve P12, kaynak SQL ile aynı tarih/değerler |
| Haftalık veri | Doğal ham seçim çalışıyor | P16; dönüşümler bütün haftalık ailede hâlâ semantik incelemeye kapalı |
| EVDS hesapları | Oran farkı ve endeks büyümesi çalışıyor | P18-P19; birim ile yüzde puan ayrımı doğru |
| TBB doğal çeyrek | İhtiyaç kredisi kullandırım akımı okunuyor | P23, 2026 Q1: 655.500,795406 milyon TL |
| Risk Merkezi | Aylık kaynak vintage referanslarıyla oran okunuyor | P24; yalnız mevcut normalize konut ailesi sınandı |
| Değişmez sonuç deposu | Snapshot, manifest, sürüm ve Parquet kayıtları mevcut | Bu araştırmada ayrı geçici snapshot üzerinde çağrılar yapıldı; eski eşzamanlılık testleri tekrar çalıştırılmadı |
| Veri eksikliği | Metadata-only seri açık hata veriyor | P28: `METADATA_ONLY`; sayı uydurulmadı |
| Kaynak açıklaması | Referans yeterliliği ile dosya doğrulaması ayrılmış | P22 legacy kaynakta `source_references_complete=false`; ham dosyalar tekrar açılmadı |
| Finans dışı CSV | Açık sözleşmeli sayısal zaman serisi çalışıyor | P29: sentetik klinik ziyaretlerinde aylık büyüme %100 ve %50 |
| Doğal dil agent'ı | Henüz bu araçları seçen canlı Qwen döngüsü yok | Bu deneyler model yetenek veya cevap doğruluğu testi değildir |

Mevcut yayın kontrolleri ve durum sayıları
[validation.json](../../../data_pipeline/lakehouse/validation.json),
[kalite kapısı](../../../tools/lakehouse_quality.py) ve
[registry](../../../data_pipeline/lakehouse/registry.py) ile çapraz okundu.

## Yeni bulunan hesap ve erişim açıkları

### A01, P0: akımın son ayı, çeyrek akımı gibi sunulabiliyor

P09 ve P10 aynı net kâr metriği, aynı banka grubu ve aynı dönemleri kullanır.
Yalnız `alignment` değişir:

| Çeyrek | `sum` sonucu, milyon TL | `last` sonucu, milyon TL |
| --- | ---: | ---: |
| 2026 Q1 | 288.688 | 119.287 |
| 2026 Q2 | 239.743 | 106.642 |

`last` sonucu yalnız Mart/Haziran aylık akımıdır. Buna rağmen cevap `ok`,
uyarı listesi boş ve metrik türü hâlâ `flow` olur. Seçim işlemi matematiksel
olarak tanımlıdır; sorun çıktının anlamının çeyrek akımından farklı olduğunu
sözleşmenin zorunlu olarak belirtmemesidir. Agent bunu çeyrek kârı diye
sunarsa Q1 için yaklaşık %58,7 düşük bir sayı kullanır.

En küçük yeniden üretim:

```json
{
  "start": "2026-Q1", "end": "2026-Q1", "frequency": "quarterly",
  "columns": [{
    "name": "profit",
    "metric_id": "bddk_monthly:table02:53:ef40239f1db4:Toplam",
    "dimensions": {"group_code": 10001}, "alignment": "last"
  }]
}
```

[Servis frekans doğrulaması](../../../tools/lakehouse_service.py#L223)
`sum` ve `mean` için anlam denetimi yapar; `last` için eşdeğer kısıt yoktur.
P26'da TBB çeyreklik kullandırımına yıllık `last` da kabul edilirken P27'de
doğru yıllık `sum` desteklenmediği için reddedildi.

Plan: her ölçüye izinli zaman dönüşümleri eklenmeli. Akım toplamı ile
“dönemin son alt döneminin akımı” ayrı çıktı anlamları olmalı. Aylık-yıllık
ve çeyreklik-yıllık tam akım toplamları takvim bütünlüğüyle desteklenmeli.
Bir modelin açık `last` seçmesi semantik denetimi kaldırmamalı.

### A02, P0: her endeks deflatör sayılıyor

P25'te `TP.TSANAYMT2021.Y1` sanayi üretim endeksi, net kârı reel değere
çevirmek için kullanıldığında `validate_plan` sonucu `valid` oldu.

```json
{"op":"deflate","column":"profit","index":"industry",
 "base_period":"2026-01","output":"real_profit"}
```

Mevcut kontrol yalnız girdinin `kind=index` ve `status=ready` olmasını
ister. Sanayi üretim endeksi bir fiyat deflatörü değildir. Kullanıcı özel
olarak “üretim endeksine oranla normalize et” diyebilir; o durumda sonuç
enflasyondan arındırılmış kâr diye etiketlenmemelidir.

Plan: endeksin rolü, fiyat kapsamı, baz ve amaç sözleşmede bulunmalı.
Enflasyon deflasyonu ile başka bir endekse normalizasyon ayrı işlem/çıktı
anlamları olmalı. Yanlış ama sayısal olarak çalışabilir plan da reddedilmeli.
İlgili kod: [deflate doğrulaması](../../../tools/lakehouse_service.py#L289).

### A03, P1: günlük dilde metrik ve boyut bulma eksik

P01-P07 sonuçları:

| Sorgu | Eşleşme |
| --- | ---: |
| `İstanbul altın mevduatı` | 0 |
| `Altın Mevduatı` | 6 |
| `sermaye yeterlilik` | 0 |
| `işsizlik` | 13 |
| `unemployment` | 0 |
| `KOBİ` | 29 |
| `bddk_monthly:table06` | 80 |

İstanbul altın mevduatı P11'de gerçekten okunabildi. Başarısız arama veri
eksikliği değildir: arama yalnız metrik kimliği ve Türkçe başlık üzerinde
kelimelerin tümünü içermeyi ister. Şehir değeri başlığın parçası değildir.
Türkçe ek değişimi ve İngilizce eş anlamlılar da karşılanmıyor.

`describe` boyut adlarını verir; `İSTANBUL` gibi geçerli değerleri ve
`10001` gibi kodların kaynak bazındaki etiketlerini keşfeden araç yoktur.
Modelden bu kodları bilmesini beklemek, veri sözlüğünü prompt'a gizlice
taşımak olur.

Plan: metrik araması ve boyut/değer çözümü ayrı çıktılar üretmeli.
İngilizce adlar, mevcut `searchable_text`, kaynak/tablo adı ve kontrollü
eş anlamlılar kullanılmalı. Embedding seçimi ve yeniden sıralama, arama
başarısının ölçüldüğü soru kümesiyle değerlendirilmelidir. Anahtar kelime
bulunmayınca `metadata_only` sanılıp gereksiz indirme başlatılmamalı.

Ölçülen bağlam farkı: bütün sözleşmeleri JSON'a açmak 43.396.763 karakter,
beş altın metrik kartı 2.507 karakterdir. Bunlar karakter ölçümüdür, token
ve model pencere hesabı değildir. Küçük kart verme yaklaşımı korunmalı.
Kod: [discover](../../../tools/lakehouse_service.py#L168).

### A04, P1: grup/rank soruları için sorgu dili yeterli değil

P15'te şehir listesi, “boyutlar yalnız skaler olabilir” hatasıyla reddedildi.
Bütün boyutların tek değerle sabitlenmesi gerekiyor. Ayrıca plan en fazla
25 metrik sütunu alıyor. Dolayısıyla 81 ilin altın mevduatı sıralaması,
ilk 10 il, tüm illerin ağırlıklı toplamı veya gruplu karşılaştırma bir ortak
gruplama operasyonuyla yapılamıyor. Aynı lakehouse'u doğrudan SQL ile
kullanan insan bu soruları çözebilir; mevcut agent arayüzü çözemiyor.

Plan: sınırlı `filter`, boyut listesi, `group_by`, kaynakça izinli toplam,
`order_by`, `top_k` ve sonuç sayfalama sözleşmeleri. Grain ve kardinalite
kontrolleri korunmalı. Kamu/özel, mevduat/katılım gibi çakışabilen banka
grupları körlemesine toplanmamalı. Genişletme, serbest SQL vermeyi gerektirmez.

### A05, P1: kişi başı tutar, yüzde oranıyla aynı türde

`bddk_finturk:table06:KisiBasiNakdiKredi`, `TRY/person` biriminde ve
`kind=ratio`. P13'te büyüme `UNIT_MISMATCH` ile reddedildi ve fark önerildi.
P14'te fark da yüzde olmayan girdiye yüzde puan uygulanamayacağı için
reddedildi. Böylece geçerli “kişi başı kredi kaç TL arttı?” sorusu için
iki doğal hesap yolu da kapalı.

Plan: birimin boyutu ile ölçünün zamansal türü ayrı tutulmalı. Yüzde oranı,
kişi başı para, fiyat, süre ve yoğunluk aynı davranışa zorlanmamalı.
`difference` uygun özgün birimde fark; yüzde biriminde ise yüzde puan
üretmeli. Kişi başı tutarın büyüme ve gerektiğinde reel dönüşümü desteklenmeli.
Kod: [büyüme/fark kontrolü](../../../tools/lakehouse_service.py#L275).

### A06, P1: haftalık kaynak var, temel büyümesi bile kısıtlı

P16'da haftalık ticari kredi okunuyor ve kaynak izi var. P17'de aynı
serinin haftalık büyümesi `SEMANTICS_REVIEW_REQUIRED` ile reddediliyor.
Bu doğru bir temkinli engel; fakat “haftalık veriler agent analizi için
hazır” iddiasını sınırlar. Sadece verinin yüklenmesi işlevsel kapsam değildir.

Plan: önce kredi/mevduat bakiyesi, kart bakiyesi ve açık oranlar gibi
doğrulanabilir haftalık aileler için tür/birim/zaman politikaları tamamlanmalı.
2022 menkul tanım kırılması için eski-yeni karşılaştırma açıkça bölünmeli
veya engellenmeli. Bütün haftalık aileyi koşulsuz `ready` yapmak çözüm değil.

### A07, P1: takvim sonu ile son sayısal gözlem ayrılmalı

P20'de altın fiyatının Mayıs 2026 aylık `last` sonucu null, Haziran sonucu
6.051.500 TL/kg. Ham veri Mayıs 25'te 6.737.900; Mayıs 26-29 satırları null.
Mevcut davranış son fiziksel satırı seçer. Eksikliği açıkça raporlaması
olumludur; “ayın son yayımlanmış fiyatı” sorusuna otomatik cevap değildir.
Null nedeninin resmî takvimi bu deneyde doğrulanmadı.

Plan: `last_native_row`, `last_observed` ve tarihli `as_of` anlamları ayrı
olmalı. Son sayısal değerde kullanılan gerçek tarih, gözlem yaşı ve izinli
eskime aralığı gösterilmeli. Resmî takvim kanıtı olmadan tatil veya piyasa
kapanışı uydurulmamalı. Kaynak null'larını genel ileri doldurmak önerilmiyor.

### A08, P1: kaynak referansları bazı ailelerde eksik veya yanıltıcı

P22'de legacy EVDS katılım tüketici kredisi okunuyor; ham kaynak hash'i var
ama locator bağlanmadığı için açıklama eksikliği doğru olarak bildiriyor.
Bu iz, yerel normalize artefakt ile ham HTTP cevabı ayrılarak tamamlanmalı.

TBB ihtiyacın YP metriğinin görünür başlığı hâlâ `FX-linked` diyor.
Sözleşme notu bunun yabancı para kredilerin TL karşılığı olduğunu açıklıyor.
Doğru not, yanıltıcı ana başlığı düzeltmiyor. Kalıcı metrik kimliği geriye
uyum için korunabilir; başlık ve model kartı açık anlamı taşımalı.

Risk Merkezi tekil borçlu sayısı `kind=unknown`, `review_required`.
Kaynakta tanımlanmış kişi stokunun neden kapalı kaldığı sözleşme eksiğidir.
Bölgesel türevlerin ve altın türevinin hücre tarifi eşlenmediği için
kısıtlanması ise şu an doğru davranıştır.

Kaynak açıklamalarında `source_references_complete` ile
`source_files_verified` ayrımının korunması gerekir. Bu incelemede ham
dosya doğrulaması tekrar yapılmadı; `true` dönen referans yeterliliği bu
iddia için kullanılmadı.

### Açıkların kullanıcı sorusu ve kabul özeti

Dosya/satır referansları incelenen commit'e aittir. Beklenen davranış,
mevcut kodun zaten yaptığı iddiası değildir.

| Açık | Kullanıcı sorusu | Beklenen ve gözlenen | Kod konumu | Kabul şartı |
| --- | --- | --- | --- | --- |
| A01 | “2026 ilk çeyrek net kârı ne?” | Beklenen toplam 288.688; `last` ile 119.287 ve uyarı yok | `tools/lakehouse_service.py:223` | Akım `last` ya reddedilir ya alt dönem seçimi olarak farklı anlam ve zorunlu uyarıyla sunulur |
| A02 | “Kârı enflasyondan arındır.” | Fiyat deflatörü gerekir; üretim endeksi geçerli sayılıyor | `tools/lakehouse_service.py:289` | Üretim endeksi enflasyon deflatörü seçilirse açık semantik hata |
| A03 | “İstanbul altın mevduatını göster.” | Metrik ve şehir bulunmalı; tam soruda 0 sonuç | `tools/lakehouse_service.py:168` | Altın metriği ile kaynak şehir değeri ayrı çözümlenir |
| A04 | “Altın mevduatında ilk 10 il hangisi?” | Bütün illerde grup/rank gerekir; şehir listesi reddediliyor | `tools/lakehouse_service.py:200`, `tools/lakehouse_service.py:218` | 81 il üzerinden tek kontrollü grup/rank planı, açık Türkiye/yurt dışı kapsamı |
| A05 | “Ankara kişi başı kredi kaç TL ve yüzde kaç arttı?” | TL/kişi farkı ve büyüme geçerli; ikisi de bloke | `tools/lakehouse_service.py:275` | Kişi başı para ile yüzde oranına farklı hesap politikası |
| A06 | “Ticari krediler geçen haftaya göre ne kadar arttı?” | İncelenmiş stokta büyüme; mevcut bütün haftalık semantik türleri kapalı | `data_pipeline/lakehouse/registry.py:146` | Seçilen temel haftalık stoklarda doğru büyüme, tanım kırılmasında engel |
| A07 | “Mayısın son yayımlanan altın fiyatı ne?” | Son sayısal tarih ve değer gerekir; son fiziksel null seçiliyor | `tools/lakehouse_service.py:345` | 25 Mayıs değerini seçen işlem tarih/eskime politikasını açıkça taşır; null nedeni uydurulmaz |
| A08 | “Kaynağı göster; YP neyi ifade ediyor?” | Ham locator ve doğru ana başlık; legacy iz kısmi, TBB başlığı FX-linked | `data_pipeline/lakehouse/registry.py:168`, `data_pipeline/lakehouse/registry.py:197` | Legacy referansı tamamlanır veya kısmi kalır; TBB görünen adında doğru YP anlamı |

P01-P30 isteklerinin JSON kopyaları aynı dosya adındaki kanıt artefaktında
mevcuttur. Kabul şartları yeni uygulanmış testler olarak sayılmıyor.

## Finans dışına taşınma deneyi

P29'da üç aylık sentetik klinik CSV'si finans snapshot'ına eklendi.
Ziyaret sayısı 10, 20, 30; nüfus 100, 100, 100. Seçim ve ziyaret büyümesi
aynı araçlarla çalıştı. Bu, açık sözleşmeli yeni sayısal zaman serisinin
eklenebildiğine ilişkin olumlu kanıttır.

P30'da ziyaret/nüfus oranı, pay ve paydanın aynı tür ve birimde olması
şartı nedeniyle reddedildi. Ziyaret akımı / kişi stoku gibi boyut üreten
oranlar mevcut `ratio` aracıyla hesaplanamıyor. Sağlıkta başvuru yoğunluğu,
sanayide üretim/işçi, finansta bakiye/müşteri gibi sorular için izinli
birim cebiri ve payda anlamı gereklidir. Her türlü bölmeye izin vermek
gerektiği sonucu çıkarılmamalı.

Ayrıca finans verisi olmadan tek klinik tablosu içeren bir DuckDB denendi:

- Genel `LakehouseStore.publish_snapshot` başarılı oldu.
- Finans yarışma doğrulayıcısı, BDDK/EVDS/housing tabloları bulunmadığından
  yayını reddetti.
- `LakehouseService.discover`, metrik sözleşmeleri bulunmadığından çalışmadı.

Dolayısıyla “finansı çıkar, sağlık koy” koşulu henüz doğrudan tamamlanmış
değil. Depo genel, varsayılan build/kalite/registry adaptörleri finans
paketine bağlı. Plan: genel veri sözleşmesi ve yayın çekirdeği; bunun
üzerinde finans kaynak profili ve CSV/sağlık profili. Alan profili veri
anlamını, izinli hesapları ve kalite beklentilerini eklemeli. Bunun için
DuckDB yerine başka bir tablo formatına geçmek gerekmiyor.

## Notebook ve istatistik araçları

[tools/lakehouse_analysis.py](../../../tools/lakehouse_analysis.py) yalnız
`residualize_fixed_effects` sayısal yardımcısını içerir. Anomali, korelasyon,
nedensellik ve değişim tespiti çağrıları bu modülde veya servis araçları
arasında uygulanmış değil.

[Keşif notebook'u](../../../lakehouse_veri_kesfi_ve_iliskiler.ipynb), 68 hücre:

| Hücreler, sıfır tabanlı | Mevcut çalışma | Agent'a taşınırken gereken |
| --- | --- | --- |
| 31-33 | Konut faiz değişimi ile ileri reel stok değişimi arasında 1/3/6 ay gecikmeli korelasyon | Genel seri seçimi, ortak gözlem sayısı, gecikme yönü, eksiklik politikası ve nedensel olmayan sonuç etiketi |
| 34-36 | İl sabit etkili keşif regresyonu | Örneklem, model varsayımları, istatistik belirsizliği ve hesap tarifi |
| 37-39 | Önceki en fazla altı ayla rolling-MAD noktasal anomali taraması | Seri/frekans bağımsız araç, skorlanamayan dönemler, parametre ve geçmiş veri sınırı |
| 64-65 | İl ve çeyrek sabit etkili dengesiz panel projeksiyonu | Standart hata/güven aralığı seçimi ve nedensellik iddiasının sınırı |

Rolling-MAD, kalıcı rejim/değişim noktası tespiti değildir. Gecikmeli
korelasyon veya sabit etki katsayısı da tek başına nedensel etki tahmini
değildir. Notebook bu sınırları zaten ifade ediyor; ürün çıktısı bunları
korumalı. İki `KKB_Verileri_Dogrulanmis.ipynb` dosyası 24 hücrelik veri
durumu/kullanım notlarıdır; bağımsız ek istatistik motorları değildir.

Yarışmadaki altı asgari araç ailesi ile servisteki altı veri API'si aynı
liste değildir. Keşif, okuma ve birleştirme temeli kısmen var; web arama,
yeni URL/PDF/Excel/görsel okuma, anomali, ilişki/nedensellik ve değişim
tespiti uçtan uca agent araçları henüz tamamlanmadı.

## Soru ve değerlendirme öncelik matrisi

Aşağıdaki 24 satır önerilen kabul aileleridir. Tamamı çalıştırılmış test
olarak sunulmuyor; kanıt sütunundaki P kimlikleri bu araştırmada yürütülen
çağrılardır. Diğerleri sonraki agent değerlendirmesi için kabul şartıdır.

| ID | Soru/istek ailesi | Durum ve kanıt | Öncelik | Kabul şartı |
| --- | --- | --- | --- | --- |
| E01 | Aylık net kârı çeyrekleştir | Doğru `sum` P08-P09; yanlış `last` P10 | P0 | Alt dönem seçimi çeyrek toplamı diye sunulamaz |
| E02 | Kârı reel göster | Yanlış endeks kabulü P25 | P0 | Deflatör rolü ve amaç doğrulanır |
| E03 | İstanbul altın mevduatını bul | Arama 0, doğrudan veri var P01/P11 | P1 | Metrik ve şehir ayrı çözülür; kod uydurulmaz |
| E04 | Sermaye yeterlilik ve İngilizce soru | P03/P05 arama 0 | P1 | Türkçe ek/eş anlamlı ve İngilizce adlar için ölçülen arama başarısı |
| E05 | 81 ili sırala, ilk 10'u göster | Dil desteklemiyor P15 | P1 | Gruplu hesap, limit ve grain doğrulanır |
| E06 | Haftalık ticari kredi büyümesi | Ham var, hesap kapalı P16/P17 | P1 | İncelenen haftalık stok ailesi desteklenir |
| E07 | 2022 menkul tanım kırılması | Kaynak sözleşmesinde tarihler var | P1 | Kırılma üzerinden örtük büyüme hesaplanamaz |
| E08 | Kişi başı kredi büyüme/TL farkı | İkisi de kapalı P13/P14 | P1 | Yüzde oranından farklı anlamla hesaplanır |
| E09 | İşsizlik yüzde puan farkı | Çalışıyor P18 | P1 | Birim ve fark yönü doğal dilden doğru seçilir |
| E10 | Sanayi endeksi büyümesi | Çalışıyor P19 | P1 | Endeks seviyesi ile büyümesi ayrılır |
| E11 | Altının son yayımlanan fiyatı | Fiziksel son satır null P20 | P1 | Gerçek tarih ve eskime bilgisi açık olur |
| E12 | TBB yıllık kullandırım | `last` açık, `sum` kapalı P26/P27 | P1 | Dört çeyrek akımı eksiksiz toplanır |
| E13 | YP ve kurum kapsamlarını karşılaştır | Başlık/not çelişkisi, scope koruması mevcut | P1 | Kurum ve döviz anlamı tabloda görünür |
| E14 | Legacy seri için kaynak göster | Eksik iz doğru bildiriliyor P22 | P1 | Mevcut kanıt sınırı korunur, locator tamamlanır |
| E15 | Elimizde olmayan EVDS serisi | Doğru bloklanıyor P28 | P1 | Sınırlı toplama, doğrulama ve yeni yayın akışı |
| E16 | Aynı tabloya yeni sütun ekle | Mevcut servis/store mekanizması var | P0 | Önceki sütunlar, sıra, grain ve parent kimliği korunur |
| E17 | Ziyaret ve nüfus oranı | Temel sağlık seçimi var, oran kapalı P29/P30 | P1 | Alan bağımsız birim/payda sözleşmesi |
| E18 | Finanssız sağlık paketiyle başlat | Varsayılan gate/registry finans istiyor | P1 | Aynı çekirdek başka alan profiliyle çalışır |
| E19 | Yeni URL/PDF/Excel/görsel ekle | Otomatik araç yok | P1 | Kaynak indirimi, parse/OCR, sözleşme, quarantine, yayın |
| E20 | Yeni belgedeki talimatları yönet | Henüz agent okuma katmanı yok | P0 | Belge içeriği veri olarak kalır, araç yetkisi vermez |
| E21 | Olağandışı dönemleri bul | Notebook MAD örneği var | P1 | Genel araç, parametre ve skorlanamayan dönemler |
| E22 | Korelasyon/nedensellik sorusu | Keşif korelasyonu var | P1 | Soruya uygun yöntem, örneklem ve iddia sınırı |
| E23 | Kalıcı değişim noktalarını bul | Genel araç yok | P1 | Noktasal anomaliyle karışmayan yöntem/çıktı |
| E24 | Grafik/tablo/rapor üret | Parquet/JSON sonuç var, sunum motoru yok | P1 | Sayılar sonuç artefaktından alınır, kaynak ve sürüm görünür |

Agent değerlendirmesi yalnız son metne bakmamalı. Her örnekte seçilen
metrik/boyut, plan, çağrılan araç, sayısal sonuç, hata toparlama, kaynak izi,
korunan önceki tablo ve istek sayısı ayrı puanlanmalı. Normal Türkçe,
belirsiz soru, yanlış varsayım, kaynak eksiği ve ikinci/üçüncü tur revizyon
aynı soru ailesinin farklı örnekleri olmalı. Bu raporda Qwen çağrısı veya
model başarı yüzdesi üretilmedi.

## Depolama mimarisine ilişkin karar

Bu denetimde bulunan en önemli açıklar veri formatından bağımsızdır.
DuckLake veya Iceberg'e taşınmak, akımın nasıl toplanacağını, şehir kodunu,
fiyat endeksinin rolünü veya klinik paydasını kendiliğinden çözmez.
Mevcut veri ölçüsünde, salt okunur sorgular ve doğrulanan toplu yayınlar
için çalışan DuckDB + izlenen Parquet + değişmez sonuç yaklaşımı korunmalı.

“Bronze/silver/gold” bugün bir sorumluluk düzenidir: orijinal kaynaklar,
doğal frekansta anlamlandırılmış veri ve analiz çıktıları vardır. Dizin
adlarının birebir bu üç sözcük olması gerekmez. Türetilmiş panelin daha alt
katmandaki kaynak yerine geçmemesi ve kaynak izinin korunması asıl koşuldur.

Mevcut snapshot dosyası her yayın için kopyalandığından yayın sayısı ve
veri boyutu büyüdüğünde depolama maliyeti artar. Depoda otomatik eski sürüm
temizliği yoktur. Yazma ve kilitleme yereldir; varsayılan kalite paketi
finansa özeldir. Bu sınırlar ilk agent'ın kurulmasına engel değil, sonraki
ölçek kararı için ölçülecek koşullardır.

DuckLake/Iceberg kararı, bağımsız yazar ihtiyacı, artımlı yayın sıklığı,
tam kopya maliyeti, çoklu sorgu motoru ihtiyacı ve elde yazılan bakım kodunun
maliyetiyle tekrar değerlendirilmelidir. Yalnız “lakehouse” adını daha
inandırıcı yapmak için format göçü öncelik değildir. Ürün/sürüm özelliklerine
ilişkin resmî kaynak karşılaştırması ana araştırma raporunun kapsamındadır;
bu dosyada yeni bir format performans veya çoklu yazar deneyi yapılmadı.

## Sonraki uygulama sırası

1. A01-A02 için olumsuz örnekleri kabul kapısına eklemek; anlamı yanlış
   ama çalışabilir planları durdurmak.
2. Metrik ve boyut keşfini güçlendirmek; küçük kartları, geçerli değerleri,
   hesap izinlerini ve kaynak kapsamını modele ayrı vermek.
3. Araç JSON şemaları, sınırlı plan/çalıştır/doğrula döngüsü, kayıtlı konuşma
   durumu, hata koduna göre sınırlı yeniden deneme ve sonuç referansı oluşturmak.
4. İlk Qwen değerlendirmesini KOBİ dışındaki E01, E03, E09, E10, E14, E16
   ailelerini de içerecek şekilde çalıştırmak. Modelin yerel sonucu kopyalamak
   yerine yeni değer hesaplamasına izin vermemek.
5. Gruplu sorgu, doğru kişi başı türleri, haftalık kurallar ve alan profillerini
   genişletmek. E05/E08/E17/E18 bu genişlemenin kabul şartlarıdır.
6. Yeni URL alımı ve istatistik araçlarını aynı sonuç/sürüm/kaynak sözleşmesine
   bağlamak. EVDS toplama kuyruğu bağımsız ilerler; yeni gözlemler aktif
   snapshot'a otomatik olarak olmuş kabul edilmez.

Bu sıralama bir uygulama planıdır. Bu araştırma turunda söz konusu eksikler
sessizce düzeltilmedi ve yeni bir model/istatistik hizmeti kurulmadı.
