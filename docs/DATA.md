# Veri kapsamı ve kaynaklar

Bu belge, önceki ana README'deki veri envanterini ayrı bir başlık altında korur. Sayılar **10 Eylül 2026 tarihli yerel veri yayınına** aittir; bugünkü bir kamu yayınına veya her klonda hazır bulunan veriye ilişkin yeni bir doğrulama değildir.

## Hangi veri kümesi kullanılıyor?

Depodaki seçilmiş kaynak paketi ile bütün EVDS katalog serileri için hazırlanmış büyük yerel yayın farklıdır. Temiz klonda temel build, seçilmiş paketten veritabanı üretir. Tam yayın, toplama/yayın akışıyla hazırlanır ve büyük ham dosyaları Git'e dahil edilmez. Çalışma alanı oluşturulduğunda seçilen snapshot, o alanın veri sürümünü belirler.

Kesin kapsamı açılan veritabanının `catalog.build_validation` tablosu ve snapshot manifestinden kontrol edin. Katalogda bir serinin bulunması, yerel gözlemin veya istenen ekonomik dönüşüm için doğrulanmış sözleşmenin bulunduğu anlamına gelmez.

Kurulum ve yayın komutları [geliştirme rehberinde](DEVELOPMENT.md#veriyi-hazırlama), veri üretiminin ayrıntıları [pipeline rehberinde](../data_pipeline/README.md), EVDS tamamlanma kanıtları [tarihli yayın kaydında](research/evds-completion-2026-09-10/implementation.md) bulunur.

## Kaynak envanteri


| Kaynak | Kapsam | Durum |
| --- | --- | --- |
| BDDK aylık | Ocak 2021-Haziran 2026, 66 ay, 17 tablonun tamamı, 10 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK haftalık | 286 hafta, 9 tablonun tamamı, 7 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK FinTürk | 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve `YURT DIŞI` | Tamamlandı ve doğrulandı |
| TCMB EVDS katalog | 676 veri grubu, 52.696 benzersiz seri kaydı | Tamamlandı, metadata kataloğu |
| TCMB EVDS repo paketi | 599 seçilmiş fiziksel seri, 587'sinde sayısal değer | Önceden incelenmiş kaynaklar ve hesap sözleşmeleri Git ile taşınır |
| TCMB EVDS yerel tam tarama | 52.696 seri, Ocak 2021-Haziran 2026; 13.519.266 kaynak hücresi | İstek kapsamı tamamlandı; 35.652 seride 6.019.172 sayısal gözlem. Büyük kaynak yayını yereldir, Git'e eklenmez |
| TÜİK il konut satışları | 81 il, Ocak 2020-Haziran 2026, 5 aylık satış metriği | Tamamlandı ve EVDS ile çapraz doğrulandı |
| İl bazlı konut paneli | 81 il, 22 çeyrek, satış, fiyat, kira, kredi, mevduat, KFE ve YKKE göstergeleri | Yerel panel mevcut; eksiklikler sütun bazında kontrol edilir, kaynak hücresine eşlenmemiş türevler agent hesaplarına kapalı |
| TBB tüketici kredileri | 2021 Mart-2026 Mart, 21 yayımlanmış çeyrek | Tamamlandı; 9 Eylül 2026 kontrolünde 2026 Haziran raporu kaynakta yok |
| TBB Risk Merkezi | 2021 Ocak-2026 Haziran, 66 ay, 5 konut kredisi metriği | Tamamlandı; 6 resmî bülten ve tüm kaynak vintageları saklandı |
| Resmî karar belgeleri | 4 BDDK kararı ve 4 TCMB destek belgesi | Tam metin, çıkarılmış metin ve SHA-256 mevcut |

Ham kaynak değerleri değiştirilmez. Bir değer yalnız aynı resmî kaynak içindeki
kesin bir toplamsal kimlikle kanıtlanabiliyorsa ayrı `usable_value` ve audit
kaydıyla kullanılabilir. Stok ile kullandırım akımı karıştırılmaz, kümülatif
değer ile türetilen dönemlik akım ayrı tutulur ve çeyreklik veri ara aylara
yapay olarak yayılmaz.

## Ölçüm ayrıntıları

Aşağıdaki sayılar 10 Eylül 2026 tarihinde kaydedilen seçilmiş kaynak paketine aittir. Tam yerel
EVDS yayını 41.909 fiziksel seri içerir; bu yayınla derlenen uygulama
veritabanı 76 tablo/view ve 44.712 fiziksel gözlemi bulunan metrik taşır.
Fiziksel gözlem sayısı, bütün hücrelerin sayısal veya bütün dönüşümlerin
incelenmiş olduğu anlamına gelmez.

- BDDK aylık: 1.122 resmî istek, 11.220 tablo-grup kaydı, 339.650 kaynak satırı, 1.334.850 semantik ölçüm
- BDDK haftalık: 18.018 resmî sayfa, 1.736.650 ham hücre, 1.025.974 ölçüm. Kaynaktaki 2.230 boş hücrenin tamamı yapısal `FX uygulanamaz` olarak açıklandı
- BDDK FinTürk: 84.484 kaynak satırı, 936.512 ölçüm. 30.892 kaynak boşluğunun 29.564'ü yapısal. 1.328 şube sayısı hücresinin ham null değeri korundu, tamamı fonksiyon grubu kimliğiyle analitik sıfır olarak kanıtlandı, çözümlenmemiş şube boşluğu 0
- EVDS: 52.696 seri metadata kaydı, 599 yerel kaynak seri, 46.178 gözlem satırı ve 43.046 sayısal değer; 1 türetilmiş altın metriği ayrıca tutulur
- Bölgesel panel: 81 il x 22 çeyrek, 1.782 tekil satır, 33 analitik metrik ve 1.620 resmî fiyat gözlemi. Fiyatın bulunması kira ve diğer sütunların da dolu olduğunu göstermez
- TÜİK il konut satışları: 31.590 il-ay-metrik satırı, EVDS ile 25.262 birebir eşleşme, 0 değer uyuşmazlığı
- TBB Risk Merkezi: 6 PDF, 390 vintage gözlem, 66 aylık eksiksiz panel, 5 metrik ve 6 resmî revizyon
- Birleşik katalog: 72 veri varlığı, 55.501 metrik, 3.402 yerel gözlemi bulunan metrik. Fiziksel gözlem bulunması, bütün hesaplara izin verildiği anlamına gelmez
- DuckDB: 10 şema, 70 tablo veya view, mutlak kaynak dosya yoluna ihtiyaç duymayan yerel sorgu dosyası

Seçilmiş paketin raporu `data_pipeline/lakehouse/validation.json`, tam yerel
EVDS yayınının raporu `.lakehouse-runtime/evds-builds/LATEST.json` içindedir.
Açılan veritabanının `catalog.build_validation` tablosu ve snapshot manifesti
o sürümün kesin envanterini verir.

## Kaynak dizinleri

| Dizin | İçerik |
| --- | --- |
| [bddk/](../data_pipeline/bddk/) | Aylık, haftalık ve FinTürk ham kaynakları, semantik ölçümler ve doğrulamalar |
| [evds/](../data_pipeline/evds/) | Seçilmiş kaynak seriler, ham yanıtlar ve yerel tam yayınlar |
| [tuik/](../data_pipeline/tuik/) | İl konut satışları ve EVDS uzlaştırması |
| [tbb/](../data_pipeline/tbb/), [risk_center/](../data_pipeline/risk_center/) | Tüketici kredileri ve ayrı kapsamlı Risk Merkezi bültenleri |
| [regional/](../data_pipeline/regional/) | İl bazlı konut, kredi, mevduat ve fiyat paneli |
| [catalog/](../data_pipeline/catalog/) | Kaynak metrik kataloğu ve birleşik veri sözlüğü |
| [quality/](../data_pipeline/quality/), [evidence/](../data_pipeline/evidence/) | Kaynaklar arası kontroller ve resmî yöntem/karar belgeleri |
| [lakehouse/](../data_pipeline/lakehouse/) | Kaynakları DuckDB'ye derleyen build kodu ve üretilmiş veritabanı |

Kaynak indirme, toplama kuyruğu ve yayın betikleri [tools/](../tools/) içinde kalır. Uygulamanın hesap servisi ve kayıt deposu [agentic_analytics/lakehouse/](../agentic_analytics/lakehouse/) içindedir. Ham ve işlenmiş veri yolları kod düzenlemesi sırasında değiştirilmedi.

## Kaynak anlamı ve eksiklikler


BDDK aylık bültende bütün tablolar ve 10 resmî banka grubu, haftalık bültende
bütün tablolar ve 7 resmî banka grubu alındı. FinTürk'te de bütün banka
grupları ve bütün iller alındı. EVDS'nin 52.696 katalog serisi hedef dönemin
tamamı için sorgulandı, kaynak yanıtları doğrulandı ve yerel lakehouse'a
yayımlandı. `tools.complete_evds_history` bu süreci kesintiden devam ederek
yeniden çalıştırır. Bekleyen veya açıklanmamış başarısız istek kalmadı.
17.044 seri için kaynak hedef dönemde sayısal değer vermedi; bu durum sıfır
değer veya serinin tarih boyunca hiç veri içermediği şeklinde yorumlanmaz.
Kaynak birimi veya ekonomik dönüşümü doğrulanmamış yeni seriler doğal
frekanslarında okunur, dönüşümleri inceleme bekler. Önceden incelenmiş 599
serinin kaynak bağları korunur; hedef dönemdeki 39.068 ortak gözlem yeni
indirmeyle birebir eşleşti. Eski tek-serili kuyruk ve kaynak paketi de korunur.
BIST altın kapanış fiyatından `0.001` katsayısıyla TL/kg ->
TL/gram dönüşümü yapılan 1 ek seri de kaynak ve formül bilgisiyle ayrıca
tutulur. Bölgesel katmanda 81 ilin konut satışları, birim fiyatları, birim
kiraları, bölgesel KFE ve YKKE değerleri FinTürk kredi ve mevduat göstergeleriyle aynı çeyrek
anahtarında birleştirilir. EVDS'de satırı bulunmayan 10 ipotekli satış
gözleminin sıfır olduğu, TÜİK'in aynı il ve ay için yayımladığı `toplam = diğer`
özdeşliğiyle doğrulanmıştır. Bunların 9'u yarışma dönemindedir ve 8 il-çeyrek
toplamını tamamlar. Ham EVDS null değerleri değiştirilmez, fallback kaynağı ve
SHA-256 izi ayrı sütunlarda tutulur. Çeyreklik değerler ara aylara kopyalanmaz.
Kaynakta yayımlanmayan 162 il-çeyrek konut birim fiyatı resmî sütunda null
kalır. İhtiyaç hâlinde yalnız aynı KFE bölgesi ve aynı çeyrekteki resmî il
fiyatlarının medyanından ayrı ve açık etiketli bir proxy üretilir. Türkiye
geneli, önceki dönem veya başka bölge fallback'i kullanılmaz. Resmî-kaynak
hazırlığı ile proxy izinli hazırlık ayrı bayraklarda tutulur.
Katalogdaki herhangi bir başka seri `tools/EVDS_Talep_Uzerine_Indirme_Araci.py`
ile adı veya kodu üzerinden bulunup ham istek, ham cevap ve SHA-256 iziyle
indirilebilir. Güncel toplu yayın komutu ile korunan eski tek-serili kuyruğun
ayrımı [kullanım rehberindedir](AGENT_READY_LAKEHOUSE.md#evds-toplama-kuyruğu).

TBB'nin Haziran 2026 çeyreklik tüketici kredileri raporu yayımlanmadığı için
parasal kullandırım tutarı null kalır. Ayrı kaynak ailesindeki Risk Merkezi
Haziran 2026 aylık bülteni mevcuttur ve bakiye, borçlu sayısı, ortalama risk,
tasfiye oranı ve ilk kez kullanan kişi sayısını sağlar. İlk kez kullanan kişi
sayısı parasal kullandırım değildir ve bu boşluğun yerine geçirilmez.

## Kullanım sınırları

Kaynakta boş bulunan her dönemin ekonomik nedeni açıklanmış değildir. Bir seri doğal frekansında okunabilirken birim veya dönüşüm sözleşmesi inceleme bekleyebilir. Eksik değer, sıfır veya başka döneme ait gözlemle sessizce değiştirilmez.

Belge çıkarımı bütün kaynak düzenlerinde aynı doğruluğu sağlamaz. İlişki araçları genel nedensel etki tahmini sunmaz. Veri hazırlığının tamamlanmış olması, doğal dilde her sorunun doğru yanıtlandığı anlamına gelmez. [Canlı değerlendirmeler](README.md#tarihli-araştırma-ve-doğrulama-kayıtları) uygulama davranışını ayrıca ölçer.
