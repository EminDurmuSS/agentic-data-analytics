# EVDS tarihsel gözlemlerini tamamlama planı

Bu belge uygulama öncesindeki denetim ve planı saklar. Sonradan yapılan
uygulama ve kaynak doğrulamaları [uygulama kaydında](implementation.md) yer alır.

Hedef dönem: 1 Ocak 2021 - 30 Haziran 2026. Bu belge mevcut durumu, üç küçük canlı denemeyi ve önerilen uygulamayı ayırır. Toplu indirme bu denetimde başlatılmadı; deneme yanıtları da lakehouse'a yayımlanmadı. Sayısal kanıt: [audit.json](audit.json).

## Mevcut durum

- Katalog 52.696 seri içeriyor; 22.092'si arşiv işaretli.
- Yerel kaynak dosyalarında 599 farklı seri, hedef dönemde sayısal gözlemi bulunan 587 seri var. Bunlardan 556'sı mevcut düşük frekanslı takvim kontrolünde bütün beklenen dönemlerde sayısal değer içeriyor. Diğer durumlar: 24 kısmi, 7 takvimi doğrulanmamış, 12 hedef dönemde sayısal gözlemsiz. Kalan 52.097 seri yalnız metadata düzeyinde.
- Kuyruk 154.516 iş içeriyor: 153.894 bekliyor, 608 başarılı istek/snapshot ile karşılanmış, 14 veri dönmemiş. Önceki iki yeni istek de veri dönmeyen durumdaydı. Bu iş sayıları tamamlanmış seri sayısı değildir.
- `tools/evds_collection_queue.py` tek seri çağırıyor. Günlük, iş günü, haftalık ve ayda iki kez yayımlanan serilerin hepsini 365 günlük pencerelere bölüyor. İşler analitik öncelik yerine deneme sayısı ve iş kimliğiyle sıralanıyor.
- Kuyruk çıktısı geçici dizinde tutuluyor; lakehouse'a yayımlayan bir finalizasyon komutu yok. Toplama ve agent'a sorgulatma ayrı işler.
- Katalogda 18.869 serinin `unit` alanı boş. Bu, sayısal gözlemi indirmenin tek başına güvenilir analiz sözleşmesi oluşturmadığını gösteriyor.

## Resmi kaynak ve canlı doğrulama

[Güncel TCMB EVDS3 Web Servis Kılavuzu](https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=8), s.1, uzun tarih aralıklarında bitişten geriye doğru 1.000 gözleme kadar sonuç döndürüldüğünü açıklıyor. Aynı gruptaki serileri birlikte istemeyi öneriyor. Seri, formül ve gözlem parametreleri çoklu seçimde `-` ile ayrılıyor. Frekans verilmezse ortak frekans kullanılabildiğinden, farklı doğal frekansları aynı pakete karıştırmamalıyız. Düzey formülü parametre tablosunda `0` olarak tanımlı. Kimlik doğrulamalı servis ayrı EVDS anahtarını `key` HTTP başlığında istiyor; Kloudeks anahtarı bu amaçla kullanılamaz. Kılavuz sayısal istek/saniye kotası veya paket başına azami seri sayısı belirtmiyor. Bu kılavuzun kimlik doğrulamalı servisi tarif etmesi, bütün kuralların public `/fe` yolunda aynı olduğunu kanıtlamaz.

Mevcut Python istemcisinin `urllib` taşımasıyla `https://evds3.tcmb.gov.tr/igmevdsms-dis/fe` üzerinde üç istek yapıldı. Kimlik bilgisi kullanılmadı. Ham istek, sıkıştırılmış yanıt ve SHA256 kayıtları `tmp/evds_completion_probe/` altında.

| Deneme | Gerçek sonuç | Sınır |
| --- | --- | --- |
| Aynı gruptan 20 aylık TÜFE alt serisi, hedef dönemin tamamı | HTTP 200, 66 tarih satırı ve istenen 20 seri sütunu. 18 seride 66 sayısal değer; iki seride 31 ve 7 sayısal değer | Kaynaktaki boşluklar korundu; eksik dönemlere sıfır atanmadı. 1.320 hücrelik sonuç alındı. |
| İki haftalık kredi faizi serisi, hedef dönemin tamamı | HTTP 200, her seride hedef dönem içinde 287 farklı tarih ve 287 sayısal değer | Kaynak ayrıca 3 Temmuz 2026 tarihini döndürdü; hedef dönem dışında olduğu için ayıklandı. |
| İki günlük döviz serisi, 900 takvim günü | HTTP 200, her seride 900 farklı tarih, 619 sayısal değer | Sayısal olmayan 281 tarih doğrudan teknik indirme hatası veya sıfır kabul edilemez. |

Bu denemeler tüm katalog için başarı oranı veya servis kotası ölçümü değildir. 20 serili aylık paket çalıştı; 20 serili günlük paket henüz denenmedi. İlk deneme kodunda HTTP gövdesi bayt yerine sözlük verildiği için yerel TypeError oluştu; kaydedilen başarılı ölçümler JSON gövdesi bayta çevrildikten sonraki üç çağrıya aittir.

Bağımsız karşılaştırma: `TP.KTF12` için haftalık deneme yanıtı, salt okunur `analytics.duckdb` içindeki `evds.housing_observations` ile karşılaştırıldı. Hedef dönemde 287/287 dönem eşleşti, 287/287 değer 1e-10 tolerans içinde aynıydı; eksik veya fazla hedef dönem yoktu. Aynı paketteki `TP.KTF10` mevcut lakehouse kataloğunda `observation_available=False`; bu yeni veri için yayınlama köprüsünü test etmeye uygun bir örnek.

Yayınlama açığının kod kanıtı: `tools/evds_collection_queue.py:374` geçici iş çıktısını yazıyor; `data_pipeline/catalog/build_unified_catalog.py:205` yalnız önceden tanımlı beş EVDS paketini fiziksel gözlem kaynağı olarak değerlendiriyor; `data_pipeline/lakehouse/build_lakehouse.py:434` sabit paketleri içeri alıyor. Yeni bir dizine Parquet yazmak tek başına bu kayıtları değiştirmiyor. Uygulamanın snapshot önbelleği de yeni sürüm için yenilenmeli; mevcut workspace'ler eski snapshot'a bağlı kalmalı. Mevcut katalog ve kalite aracındaki sabit `False` kapsam bayrakları elle açılmamalı, kapsam tablosundan hesaplanmalı.

## Uygulama sırası

1. **Kapsam ve metadata sürümünü sabitle.** Katalogdaki her seri için grup, doğal frekans, birim, arşiv durumu ve kaynakla doğrulanmış başlangıç/bitiş bilgisi tut. Sırf arşiv etiketi nedeniyle seri çıkarma. İlk dönen sayısal gözlemden önceki boşluğu, bağımsız metadata kanıtı olmadan kesin seri başlangıcı sayma. Hedef aralıkta veri olmadığı doğrulanabilen serileri gerekçesiyle kaydet.
2. **Toplu istek planlayıcısı ekle.** Aynı grup, aynı doğal frekans ve varsayılan gözlem yöntemiyle en fazla 20 seriyi paketle. Düzey verisini iste. Günlük ve iş günü serileri en fazla 900 dahil takvim günlük aralıklara böl; haftalık ve daha seyrek seriler bu 5,5 yıllık hedefte tek aralığa sığar. Daha uzun genel CLI aralıklarında frekansa göre sınırlandırmayı koru. Mevcut başarılı işleri ve kaynak kanıtlarını yeni planla eşleştir; eski kuyruğu silme.
3. **Kaldığı yerden devam eden toplama yap.** Önce küçük, farklı frekansları kapsayan bir pilot çalıştır. Tüm çalışanların paylaştığı hız sınırı, timeout, Retry-After ve artan bekleme uygula. Hatalı paketleri küçült; tek bozuk seri bütün paketi kalıcı biçimde kaybettirmesin. Başarıyı seri ve tarih aralığı bazında tut. Kesilmiş yanıtı tamamlanmış kabul etme. Önce aktif ve kısmi serileri işle; arşivler kapsamda kalsın.
4. **Kalite ve revizyonları uzlaştır.** Ham yanıtı değişmez sakla. Seri kimliği, doğal frekans, tarih sınırı, tekrar, sayısal olmayan değer ve yanıt kesilmesini denetle. Tatil/hafta sonu, kaynak null'u, yayımlanmamış dönem ve teknik başarısızlığı ayır. Önceki snapshot ile farklı bir değer varsa kaynağı ve çekim zamanını koruyarak revizyon olarak işle; sessizce üzerine yazma. Yıllık 2026 gibi dönemlerin Haziran kapsamıyla ilişkisini açıkça tanımla; hedef tarih sonrasını analize sızdırma.
5. **Kaynak paketini ve yeni lakehouse sürümünü üret.** İş parçalarını tekrarları ve çelişkileri ele alarak birleştir, taşınabilir kaynak yolları ve hash kanıtlarıyla manifestli veri paketi oluştur. Lakehouse kaynağına kaydet; Silver gözlemleri, Gold görünümleri, katalog bağları ve kalite raporlarını yeniden üret. Birim/anlamı doğrulanmayan seriler keşfedilebilir kalabilir; agent'a her işlemi otomatik açma. Yeni workspace yeni snapshot'ı kullansın; mevcut analizlerin eski snapshot'a dayanan kanıtlarını koru.
6. **Tamamlanmayı uçtan uca ölç.** Her katalog serisi ya indirilmiş ve doğrulanmış hedef aralığa, ya kaynak kanıtıyla açıklanmış kapsam dışı/verisiz duruma bağlanmalı. Açıklanamayan indirme hatası varsa tam kapsam iddiasında bulunma. İndirme kapsamı, sayısal gözlem kapsamı, takvim kalitesi ve agent'ın analize hazır serileri ayrı sayılmalı. Yayımlanan yeni veriler üzerinden gerçek agent sorguları ve kaynak karşılaştırması çalıştırılmalı.

## İstek sayısının hesaplanması

Katalogda `(group_code, frequency, default_aggregation)` ile 685 grup oluşuyor. Tüm seriler dahil, günlük/iş günü serilerine üç tarih parçası, diğerlerine bir parça verildiğinde:

| Paket üst sınırı | Hesaplanan temel istek sayısı |
| --- | ---: |
| 10 seri | 8.045 |
| 20 seri | 4.233 |
| 50 seri | 1.937 |

Önerilen başlangıç üst sınırı 20 seridir; 50'li paket canlı doğrulanmadı. Sayılar metadata yenileme, başarısız çağrı tekrarı, paket bölme, ek doğrulama ve yayınlama maliyetini içermeyen plan tahminleridir. Henüz yeni bir çalışma kuyruğuna uygulanmadılar. Yaklaşık 4.233 istek, mevcut 154.516 işten yaklaşık %97,3 azdır. Üç kısa denemenin gecikmesinden güvenilir tamamlama süresi veya izin verilen trafik miktarı çıkarılamaz.

Bir sonraki somut geliştirme, toplu planlayıcı ve yayınlama köprüsünün birlikte uygulanması, ardından küçük pilotun indirme -> doğrulama -> yeni snapshot -> agent sorgusu zincirinde denenmesidir.
