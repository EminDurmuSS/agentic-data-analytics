# Lakehouse Veri Keşfi — Teknik Bilgisi Olmayanlar İçin Ayrıntılı Açıklama

**İncelenen dosya:** `lakehouse_veri_kesfi_ve_iliskiler.ipynb`  
**İnceleme yöntemi:** Notebook’taki 51 hücrenin kayıtlı kodu, açıklaması ve çıktısı tek tek okundu.  
**Önemli not:** Notebook çıktıları, çalıştırıldığı anda Windows’taki bir `analytics.duckdb` kopyasından alınmış bir snapshot’tır. Bu nedenle güncel lakehouse yeniden üretildiyse tablo sayıları veya kayıt sayıları az miktarda değişebilir.

## Önce büyük resim: Bu veri neyi anlatıyor?

Bu veri setini büyük bir **Türkiye konut ve kredi dosyası** gibi düşünebilirsin. Tek bir kaynaktan değil, farklı kurumların yayımladığı resmî bilgilerden hazırlanmış:

- **BDDK:** Bankacılık sektörünün kredi, mevduat ve banka grubu verileri.
- **TCMB EVDS:** Faiz, enflasyon, konut fiyatları, satışlar, döviz, güven endeksleri gibi ekonomik göstergeler.
- **TÜİK:** İl bazında konut satışı verileri.
- **TBB:** Bankaların raporladığı tüketici kredileri; özellikle yeni kredi kullandırımını ayırmak için kullanılıyor.
- **Resmî karar/dokümanlar:** Sayılardaki değişimlerin yanında görülebilecek düzenleme veya politika bağlamı.

Amaç “ev fiyatları neden değişti?” ya da “kredi faizi düşünce satışlar ne yaptı?” gibi sorulara tek bir kaynaktan değil, birden fazla resmî göstergeyi aynı zaman çizelgesinde kullanarak yaklaşmaktır.

Bu dosya doğrudan bir tahmin motoru değildir. Önce güvenilir, izlenebilir ve sorgulanabilir veri hazırlıyor. Yani bir doktorun teşhis koymadan önce tahlilleri, röntgeni ve hasta geçmişini aynı yerde toplamasına benzer.

## Sık geçen kavramlar

- **Lakehouse:** Farklı ham verileri tek sorgu alanında toplamak için kurulan veri deposu. Burada bu rolü `analytics.duckdb` dosyası üstleniyor.
- **DuckDB:** Büyük veri tablolarını bilgisayarda hızlı sorgulamaya yarayan gömülü veritabanı. Ayrı bir sunucu açılması gerekmez.
- **Schema (şema):** Dolap/klasör gibi düşün. Birbiriyle ilgili tabloları gruplayan bölüm.
- **Tablo:** Excel sayfası gibi satır ve sütunlardan oluşan veri parçası.
- **Metrik:** Ölçülen şey. Örneğin konut kredisi stoku, faiz oranı veya ipotekli satış sayısı.
- **NULL:** “Sıfır” değil, “değer bilinmiyor/yayımlanmamış/uygulanmıyor” demek.
- **Stok:** Bir andaki toplam bakiye. Örneğin ay sonundaki toplam konut kredisi borcu.
- **Akım (flow):** Bir dönemde gerçekleşen hareket. Örneğin o çeyrekte kullandırılan yeni kredi tutarı.
- **Kaynak izi (lineage/provenance):** Bir sayının hangi dosyadan, hangi istekle ve hangi dönüşümle geldiğini takip edebilme özelliği.

---

## Hücre hücre açıklama

### Hücre 1 — Notebook’un amacı

Bu hücre başlık ve amaç açıklamasıdır. Notebook, veriyi değiştirmek için değil **okumak ve keşfetmek** için hazırlanmış. Ayrıca veritabanında fiziksel foreign key kuralları olmasa bile, tabloların birbirine teknik olarak doğru bağlanıp bağlanmadığını kontrol edeceğini söylüyor.

Basit karşılığı: “Bu Excel sayfalarının birbirine ait satırları doğru mu eşleşiyor?” sorusu test ediliyor.

### Hücre 2 — Snapshot uyarısı

Notebook, her açılışta sabit bir sonuç göstermiyor; aynı dosya yolundaki güncel DuckDB’yi okumaya çalışıyor. Lakehouse yeniden üretildiğinde sonuçlar yeni verilere göre değişebilir.

Bu iyi bir tasarım: rapor statik bir ekran görüntüsü değil, güncel veriye yeniden bağlanabilen bir keşif aracı.

### Hücre 3 — Veritabanına salt-okunur bağlanma

Kod `analytics.duckdb` dosyasını buluyor ve `read_only=True` ile açıyor. Çıktıdaki `Bağlantı: read-only`, notebook’un veriyi yanlışlıkla değiştiremeyeceğini gösteriyor.

Çıktıda görünen `c:\Users\SAMET\...` yolu, notebook’un en son Windows ortamında çalıştırıldığını gösterir. Bu yol veri anlamı açısından sorun değildir; ancak farklı bir bilgisayarda çalıştırıldığında kod dosyayı yeniden bulabilmelidir.

### Hücre 4 — Genel görünüm bölümü

Bu bir bölüm başlığıdır. Sonraki hücreler hangi veri klasörleri ve tabloların bulunduğunu sayar.

### Hücre 5 — Şema, tablo ve satır sayıları

Çıktı **9 şema** ve **60 tablo** gösteriyor:

- `analysis`: Analize hazır özet tablolar.
- `bddk`: Bankacılık düzenleme/veri kaynağından gelen tablolar.
- `catalog`: Veri sözlüğü; “hangi metrik ne anlama geliyor?” bölümü.
- `evds`: TCMB ekonomiye dair zaman serileri.
- `evidence`: Olay ve belge bağlamı.
- `quality`: Kalite ve kaynaklar arası tutarlılık kontrolleri.
- `regional`: İl bazlı konut paneli.
- `tbb`: Banka raporlarından gelen kredi verileri.
- `tuik`: İl bazında resmî konut satışları.

En büyük tablolar BDDK tarafında:

- `bddk.monthly_measurements`: 1.334.850 aylık ölçüm.
- `bddk.weekly_measurements`: 1.025.974 haftalık ölçüm.
- `bddk.finturk_measurements`: 936.512 il/çeyrek/banka grubu ölçümü.

Kullanıcı için en kolay başlangıç tabloları ise küçük olan iki analiz tablosudur:

- `analysis.housing_credit_monthly`: 66 aylık özet.
- `analysis.housing_credit_quarterly`: 22 çeyreklik özet.

Yani milyonlarca teknik satır olsa da, “Türkiye’de konut kredisi ne oldu?” sorusuna başlamak için 66 satırlık hazır özet kullanılabilir.

### Hücre 6 — Veritabanının boyutu ve ağırlık merkezi

DuckDB dosyası notebook snapshot’ında **98,2 MiB**. Bu, milyonlarca ölçüm olmasına rağmen taşınabilir bir dosya boyutudur.

Şema bazında satır toplamı:

- BDDK: 3.319.323 satır
- EVDS: 143.152 satır
- TÜİK: 56.872 satır
- Katalog: 55.614 satır
- Diğerleri daha küçük özet veya kontrol tabloları

Buradan çıkarılacak sonuç: Veri ağırlıklı olarak bankacılık verisidir; fakat ekonomik bağlam, il bazlı satış ve açıklama katmanları da bulunmaktadır. `used_blocks=393` ve `free_blocks=0` yalnız teknik depolama bilgisidir; “veri kalitesi kötü” anlamına gelmez.

### Hücre 7 — Kolon ve NULL inceleme bölümü

Bu hücre, her tablodaki sütunların türünü ve boş değer oranını inceleyeceğini açıklar. Amaç “hangi sütunda ne var?” ve “hangi değerler eksik?” sorularını yanıtlamaktır.

### Hücre 8 — Tüm tabloların sütunları ve veri tipleri

Bu, notebook’un en uzun çıktılarından biridir. Her tablo için sütun adları gösterilir. Buradaki ana mesaj şudur: veri yalnızca sayı saklamaz; sayının **birimi**, **kaynağı**, **zamanı**, **eksik olma nedeni**, **dönüşüm yöntemi** ve bazen **hash değeri** de saklanır.

Örnekler:

- `analysis.housing_credit_monthly` 90 sütun içerir. Ay, konut kredisi, faiz, enflasyon, satış, olay etiketi ve kalite bayrağı aynı satırda buluşur.
- `analysis.housing_credit_quarterly` 84 sütun içerir. BDDK, FinTürk, TBB ve çeyreklik EVDS göstergelerini birleştirir.
- `bddk.finturk_measurements` içinde `value`, `is_missing`, `missing_kind`, `missing_reason`, `usable_value` gibi alanlar vardır. Bu sayede kaynak boşluğu ile analitik kullanılabilirlik ayrılır.
- EVDS gözlem tablolarında her sayı için seri kodu, dönem, ham değer, kaynak dosya ve SHA-256 izleri bulunur.
- `catalog.metrics`, her metriğin adı, birimi, frekansı, kapsamı ve yerelde sorgulanabilir olup olmadığını tutar.

Bu hücredeki 120 çıktı tablo yapısını belgeliyor; ekonomik bir sonuç üretmiyor. Ancak agent’in veya kullanıcının doğru tabloyu seçebilmesi için altyapı sağlıyor.

### Hücre 9 — Boş değer (NULL) profili

Üç önemli tablo için her sütunda kaç boş değer olduğu hesaplanıyor.

**Aylık ulusal analiz tablosu:**

- Olay başlığı/olay kimliği alanlarında 61 boş değer var. Bu normaldir; 66 ayın yalnız 5’inde kayıtlı resmî olay bulunuyor.
- Bazı anket veya özel EVDS serilerinde yüksek boşluk oranı var. Örneğin `TP_HANEBEK_HAN18A` 60 ay boş.
- Temel analiz alanları — kredi stoku, reel kredi değişimi, ipotekli satış payı ve kalite bayrağı — boş değil.

**Çeyreklik ulusal analiz tablosu:**

- Aynı seyrek seriler burada da yüksek oranda boş.
- KKM serileri üç çeyrekte boş. Bu genellikle serinin daha sonra başlamasıyla ilgilidir.
- Altın, piyasa ve temel bazı seriler tam kapsamlıdır.

**İl bazlı panel:**

- 1.782 il-çeyrek satırında fiyat seviyesinde 162 boş değer var (%9,09). Bu, bazı iller için resmî fiyat serisi bulunmadığını gösterir.
- Yıllık değişim oranlarının boşluğu daha yüksektir. Bir yıllık değişim hesaplamak için önceki yılın değeri gerektiği için başlangıç dönemlerinde boşluk normaldir.
- Satış, il kimliği, kredi/mevduat alanları ve `analysis_ready` gibi temel alanlar boş değildir.

En önemli ders: NULL değer “0 satış” veya “0 kredi” değildir. Önce kaynağın yayımlamadığı bir veri mi, serinin henüz başlamadığı bir dönem mi, yoksa gerçekten uygulanmayan bir alan mı olduğu kontrol edilmelidir.

### Hücre 10 — NULL’ların doğru yorumlanması

Bu hücre Hücre 9’daki uyarıyı metinle açıklar. TBB’nin yayımlanmayan 2026-06 raporu ya da bazı illerde olmayan fiyat serileri boş görünür; bunları sıfıra çevirmek yanlış olur.

Kullanıcı için kural: Ulusal analizde temel alanlar doluysa analiz yapılabilir. İl bazlı analizde ise `analysis_ready` ve kaynak kapsamı kontrol edilerek uygun satırlar seçilmelidir.

### Hücre 11 — Mantıksal foreign key bölümü

Bu bölüm, tablolar arasında “yetim kayıt” olup olmadığını sınar. Örneğin ölçüm tablosundaki bir metrik kodu, sözlükte de var mı? Bir TÜİK uzlaştırma satırı, asıl TÜİK satış tablosunda karşılık buluyor mu?

### Hücre 12 — Tablo ilişkileri testi

Çıktıda test edilen 12 ilişkinin tamamı `PASS`, yani `orphan_rows=0`:

- BDDK aylık ölçümlerinin metrik sözlüğünde karşılığı var.
- BDDK haftalık ölçümlerinin hem sözlükte hem kaynak tabloda karşılığı var.
- FinTürk ölçümlerinin sözlük ve kaynak tablosu bağlantısı kopuk değil.
- İl panelindeki il anahtarlarının il boyut tablosunda karşılığı var.
- TÜİK-EVDS uzlaştırma satırları, TÜİK aylık verisine bağlanıyor.
- EVDS gözlemlerinin seri kodları EVDS kataloğunda bulunuyor.

Bu “verinin ekonomik yorumu mutlaka doğru” demek değildir. Sadece teknik olarak bir satırın aradığı kimlik/sözlük/kaynak kaydını bulabildiğini gösterir. Excel’de VLOOKUP yapınca `#YOK` hatası çıkmaması gibi düşünülebilir.

### Hücre 13 — Foreign key sonucunun sınırı

Bu markdown hücresi doğru bir uyarı verir: `PASS`, bağlantının kopuk olmadığını kanıtlar; ekonometrik veya ekonomik doğruluğu tek başına kanıtlamaz. Örneğin iki doğru bağlanmış seri yine de farklı kapsamda olabilir.

### Hücre 14 — Yerelde sorgulanabilir metrik kapsamı

Çıktı her kaynak için katalogdaki metrik sayısını ve bunların kaçının yerelde gözlemi bulunduğunu gösterir:

- BDDK FinTürk: 76/76
- BDDK aylık: 2.108/2.108
- BDDK haftalık: 515/517
- Bölgesel panel: 26/26
- TBB: 60/60
- TÜİK: 5/5
- EVDS: 506/52.696 (%0,96)

Toplamda 55.489 katalog metriğinin 3.297’si yerelde sorgulanabilir (%5,94).

Bu oran ilk bakışta düşük görünebilir; fakat anlamı “veri bozuk” değildir. EVDS’nin tüm seri adları katalogda var, ama her serinin yıllarca süren tüm gözlemleri yerel diskte saklanmıyor. Hackathon için seçilen analiz seti indiriliyor; başka seri gerektiğinde katalogdan bulunup kontrollü indirilebiliyor.

### Hücre 15 — EVDS yüzdesinin yorumu

Bu hücre Hücre 14’ün en önemli yorumunu yapar: EVDS’de %0,96, veri kalitesi puanı değil; lokal kopyanın bilinçli kapsam sınırıdır. Bunu jüriye “52 bin seriyi rastgele indirmek yerine, katalog tümünü koruyor; analitik olarak gerekli 506 serinin gözlemini izlenebilir şekilde yerelde tutuyoruz” diye anlatmak gerekir.

### Hücre 16 — Basit analiz bölümü

Bu bölümden sonra veri yapısı değil, verinin neler anlatabildiği örnek sorgularla gösterilir.

### Hücre 17 — Son 12 ay: kredi stoku, reel değişim, faiz, ipotekli satış payı

Çıktı 2025-07 ile 2026-06 arasındaki 12 ayı yan yana gösteriyor.

Öne çıkan sayılar:

- Konut kredisi stoku Temmuz 2025’te **594.607 milyon TL** iken Haziran 2026’da **801.438 milyon TL**.
- Konut kredisi faizi Temmuz 2025’te %42,56; Mart 2026’da %34,66’ya kadar düşüyor; Haziran 2026’da %40,34’e çıkıyor.
- Reel aylık kredi stoku değişimi her ay pozitif değil. Örneğin Ocak 2026’da %-2,88, Nisan 2026’da %-0,74.
- İpotekli satış payı Temmuz 2025’te %12,97 iken Mart 2026’da %22,91’e yükseliyor; Haziran 2026’da %20,00.

Sade yorum: Toplam kredi borcu TL cinsinden büyüse de, enflasyondan arındırılmış büyüme her ay aynı yönde değil. Faiz ve ipotekli satış oranı aynı anda hareket ediyor gibi görünse bile, buradan “faiz bunu kesinlikle yaptı” sonucu çıkarılamaz. Gelir, enflasyon, konut arzı, kampanyalar ve düzenlemeler de etkili olabilir.

### Hücre 18 — Hücre 17 için nedensellik uyarısı

Notebook bu tabloyu doğru biçimde temkinli yorumluyor. Yan yana giden iki seri, birbirinin sebebi olmak zorunda değildir. Bu hücre, basit grafiğin “araştırma sorusu” ürettiğini; “ispat” üretmediğini hatırlatır.

### Hücre 19 — BDDK, FinTürk ve TBB karşılaştırması

Bu sorgu üç farklı kaynağı çeyrek bazında yan yana koyar:

- 2026-06’da BDDK konut kredisi stoku: **801.438 milyon TL**.
- Aynı dönemde FinTürk’ün yurt içi il toplamı: **801.440,79 milyon TL**.
- Bu ikisinin yakın olması, iki farklı BDDK görünümünün uyumlu olduğunu destekler.
- TBB’nin 2026-06 bakiye ve kullandırım alanı `NULL`. Bunun sebebi raporun yayımlanmamış olmasıdır.
- 2026-03’te TBB bakiyesi **675.178,32 milyon TL**, yeni kullandırım tutarı **99.520,74 milyon TL**.

Buradaki kritik fark: BDDK/FinTürk satırları **mevcut toplam borç bakiyesini**, TBB kullandırım satırı ise o çeyrekte verilen **yeni kredi akımını** anlatır. Bunlar birbirinin yerine kullanılamaz.

### Hücre 20 — Kaynak kapsamı uyarısı

Bu metin, Hücre 19’daki sayıları doğrudan eşitlememeyi söyler. TBB tüm sektör yerine raporlayan bankaların kapsamını taşıyabilir. 2026-06 `NULL` değeri de “sıfır kredi verildi” değil, “kaynak raporu yok” anlamındadır.

### Hücre 21 — 2026Q2’de en çok konut satılan iller

Çıktı, 2026 ikinci çeyrekte en yüksek toplam satışa sahip illeri gösterir:

- İstanbul: 66.133 satış, 15.328 ipotekli satış, %23,18 ipotekli pay.
- Ankara: 31.169 satış, %25,50 ipotekli pay.
- İzmir: 20.704 satış, %22,02 ipotekli pay.
- Antalya: 17.569 satış, %15,11 ipotekli pay.

`analysis_ready=True`, ilgili il-çeyrek satırının seçilmiş veri kaynakları bakımından analiz kullanımına uygun olduğunu belirtir. Bu etiket “gelecekte fiyat artacak” demek değildir; yalnız veri satırının gerekli kaynak/kapsam kontrollerini geçtiğini anlatır.

### Hücre 22 — İl sıralamasının yorumu

Bu hücre, büyük satış hacminin tek başına daha güçlü kredi pazarı anlamına gelmediğini vurgular. Örneğin Antalya’nın toplam satışı yüksek olabilir ama ipotekli satış payı Ankara’dan daha düşük olabilir. Hacim ve finansman biçimi ayrı ayrı okunmalıdır.

### Hücre 23 — TÜİK–EVDS uzlaştırması ve teknik hata

İlk sorgu başarıyla çalışmış:

- `exact_match`: 25.262 gözlem.
- `evds_null_tuik_identity_zero`: 10 gözlem.

Bu şu demektir: Ortak bulunan 25.262 il-ay-metrik değeri iki resmî kaynakta birebir aynı. EVDS’de satırın boş olduğu 10 durumda ise TÜİK’in aynı resmî tablosundaki matematiksel kimlik, değerin sıfır olduğunu kanıtlıyor. Örneğin “toplam satış = diğer satış” ise ipotekli satışın 0 olması gerekir.

İkinci sorgu ise `BinderException` ile hata veriyor: `group_name` sütunu sorgulanan tabloda yok. Hata veri bozukluğu değildir; notebook sorgusunda yanlış tablo/sütun eşleştirmesi vardır. Bu hücre demo öncesi düzeltilmelidir. Muhtemel çözüm, `group_name` alanını `bddk.weekly_source_tables` tablosundan almak veya sorgudaki alanı mevcut sütunla değiştirmektir.

### Hücre 24 — Uzlaştırma sonucunun yorumu

Bu hücre, `exact_match` sonucunun yüksek güven verdiğini ama uyuşmazlık olursa tarih, il, metrik kodu ve kaynak sürümünün tek tek kontrol edilmesi gerektiğini söyler. Bu sağlıklı bir kontrol yaklaşımıdır.

### Hücre 25 — Veriyle yapılabilecek işler

Notebook’un önerdiği kullanım alanları:

- Ulusal kredi, faiz, enflasyon ve satış ilişkilerini araştırmak.
- Banka gruplarının kompozisyonunu karşılaştırmak.
- İllerin satış, fiyat, kredi ve mevduat profillerini karşılaştırmak.
- Kaynak belgelerini ve kalite uyarılarını analize bağlamak.

Bu bölüm, sistemin “sadece tablo deposu” değil; finansal araştırma başlangıç noktası olduğunu gösterir.

### Hücre 26 — İl kümeleri bölümü

Bu bölüm, illeri makine öğrenmesiyle kesin sınıflara ayırmak yerine, son çeyrekte göreli olarak düşük/orta/yüksek gruplara bölen basit bir segmentasyon başlatır.

### Hücre 27 — İl kümeleri: kredi yoğunluğu ve ipotekli satış payı

Çıktı 2026Q2’de gerekli alanları dolu **76 ili** alıyor. Beş il, eksik kaynak fiyat bilgisi nedeniyle bu analizden dışarıda kalmış olabilir.

İller iki ölçüte göre üçer gruba ayrılıyor:

- İpotekli satış payı: düşük / orta / yüksek.
- Kişi başına konut kredisi: düşük / orta / yüksek.

Dokuz kombinasyon oluşuyor. Örneğin:

- Yüksek-yüksek grupta 15 il var; ortalama satış 11.303,6 ve kişi başına kredi yaklaşık 11.464 TL.
- Düşük-düşük grupta 15 il var; ortalama satış 1.855,9 ve kişi başına kredi yaklaşık 2.926 TL.

Bu, “hangi iller krediyle finanse edilen konut piyasası bakımından daha yoğun?” sorusuna ilk cevap verir. Ancak gruplar göreli sıralamadır; “yüksek” etiketi Türkiye içindeki diğer illere göre yüksek demektir, mutlak risk notu değildir.

### Hücre 28 — Kümelerin doğru yorumu

`qcut` ile yapılan ayırma, yaklaşık eşit sayıda ili her gruba koymaya çalışır. Bu nedenle neden-sonuç veya tahmin modeli değildir. Yüksek-yüksek grubu, daha ayrıntılı inceleme için aday üretir; otomatik karar vermez.

### Hücre 29 — Banka grubu karşılaştırması bölümü

Bu bölüm, haftalık BDDK verilerinden banka gruplarını özetler. Notebook çok önemli bir uyarı yapar: Farklı tabloların ve farklı ölçülerin toplu ortalaması ekonomik bir gösterge olarak doğrudan yorumlanmamalıdır.

### Hücre 30 — Banka gruplarının kapsama ve büyüklük özeti

Çıktı 7 grup için 286 haftalık kapsam bulunduğunu gösterir:

- Sektör
- Mevduat
- Kalkınma ve yatırım
- Katılım
- Kamu
- Yabancı
- Yerli özel

Ham toplamlarda sektör en büyük, ardından mevduat, kamu, yerli özel, yabancı, katılım ve kalkınma-yatırım geliyor.

Ama bu sıra “en iyi banka grubu” veya “en fazla kredi veren grup” değildir. Çünkü sonuç farklı tablolar, metrikler ve para boyutlarının toplamından elde edilmiş genel bir teknik özet. Ekonomik karşılaştırma için aynı `table_id`, aynı `metric_code` ve aynı para birimi boyutu seçilmelidir.

### Hücre 31 — Banka grubu çıktısının yorumu

Bu markdown hücresi Hücre 30’daki sınırı tekrar açıklar. En değerli bulgu, 7 grup × 286 hafta kapsamında veri sürekliliğinin korunmuş olmasıdır. Büyüklük sıralaması ise yalnız seçilen toplam tanımlandığında anlamlıdır.

### Hücre 32 — Faiz sonrası gecikmeli tepki analizi

Bu bölüm, faiz değişimiyle kredi stokunun enflasyondan arındırılmış değişimi arasında 1, 3 ve 6 ay gecikmeli korelasyon hesaplar.

Bu, “faiz değiştiğinde kredi stoku hemen mi, birkaç ay sonra mı hareket ediyor?” sorusuna tarama amaçlı yaklaşır.

### Hücre 33 — Gecikmeli korelasyon sonuçları

Sonuçlar:

- 1 ay gecikme: 64 gözlem, korelasyon **-0,374**.
- 3 ay gecikme: 62 gözlem, korelasyon **-0,275**.
- 6 ay gecikme: 59 gözlem, korelasyon **-0,166**.

Negatif işaret, faiz değişimi arttığında takip eden dönemlerde reel kredi stoku değişiminin daha düşük olma eğilimini gösterir. En güçlü ilişki 1 ay gecikmede görülüyor; gecikme uzadıkça zayıflıyor.

Fakat bu bir sebep-sonuç ispatı değildir. 66 aylık seri kısa; enflasyon, bankaların kredi politikası, konut arzı, düzenlemeler ve ölçüm kapsamı gibi başka etkenler aynı dönemde değişebilir.

### Hücre 34 — Korelasyonun sınırı

Bu hücre doğru biçimde “birlikte hareket = nedensellik değildir” uyarısını verir. Çıktı, ileri modellemede hangi gecikmelerin önce araştırılabileceğini söyler; politika etkisini ispatlamaz.

### Hücre 35 — İl panelinde keşif amaçlı regresyon

Bu bölüm, her ilin zaman içinde kendi ortalamasından ne kadar saptığına bakar. Böylece İstanbul’un doğal olarak büyük bir il olması gibi zaman içinde değişmeyen il özelliklerinin etkisi kabaca azaltılır. Buna sabit etkileri basitleştirilmiş biçimde kontrol etme denebilir.

### Hücre 36 — Panel regresyonu sonucu

Analizde **1.620 gözlem** ve **76 il** kullanılmış.

Katsayılar:

- Kişi başına kredi: `+0,321`
- İpotekli satış payı: `-17,787`
- Bölgesel KFE: `-39,567`
- Bölgesel YKKE: `+23,208`
- Within R-kare: **0,0417**

Sade yorum: Bu değişkenler, bu çok basit modelde il içindeki satış değişimini yalnız yaklaşık %4,17 oranında açıklıyor. Yani modelin açıklama gücü düşüktür ve katsayılarla güçlü sonuçlar çıkarmak doğru olmaz.

Örneğin kişi başına kredi katsayısının pozitif olması, kredi yoğunluğu yükselen il-dönemlerde satışın da yükselme eğiliminde olabildiğini gösterir. Ancak sayıların birimleri, eksik fiyat verileri, aynı anda değişen koşullar ve standart hata analizi yapılmamış olması nedeniyle bunu nedensel ilişki diye sunmamak gerekir.

### Hücre 37 — Regresyon sonucunun yorumu

Bu hücre iki önemli sınırı açıklar:

1. Katsayı işareti yalnız birlikte hareketi anlatır.
2. İstatistiksel anlamlılık, güven aralığı, standart hata ve güçlü model kontrolleri burada yapılmamıştır.

Sunumda bu çıktıyı “ön keşif” olarak anlatmak doğrudur; “model kanıtladı” demek doğru değildir.

### Hücre 38 — Anomali ve değişim noktası bölümü

Bu analiz, aylık reel kredi değişimindeki alışılmadık ayları bulur. Son 6 aylık tipik hareketten çok uzak değerler `robust z-score` ile işaretlenir.

Bu, güvenlik kamerasındaki “normalden farklı hareket” alarmı gibidir: alarm, olayın nedenini söylemez; hangi tarihe bakılması gerektiğini söyler.

### Hücre 39 — İşaretlenen olağandışı dönemler

Dört dönem `change_point_flag=True` olarak işaretlenmiş:

- 2021-12: reel kredi değişimi %-9,76, z-skoru -9,04.
- 2022-01: %-9,46, z-skoru -5,09.
- 2024-03: %-1,71, z-skoru +4,27.
- 2025-11: %+1,71, z-skoru +4,20.

İlk iki ay özellikle çok güçlü negatif sapma gösteriyor. Bu dönemlerde kaynak kapsamı, enflasyon hesaplaması, faiz koşulu, yıl dönüşü veya düzenleme belgeleri ayrıca incelenmelidir.

Listede değişim noktası olmayan ama görece dikkat çekici başka aylar da bulunuyor. Bu normaldir: eşik aşılmadığı sürece “anomali” etiketi verilmez.

### Hücre 40 — Anomali sonucunun sınırı

Hücre, işaretli ayların ekonomik açıklama olmadığını açıkça belirtiyor. Sebep faiz, düzenleme, enflasyon, veri revizyonu veya kapsam değişikliği olabilir. Bu doğru bir risk kontrolüdür.

### Hücre 41 — AI olmadan SQL sorgulama bölümü

Bu bölüm, verinin agent olmadan da veri uzmanı tarafından DuckDB SQL ile kullanılabileceğini gösteriyor. Agent ileride bu SQL bilgisini kullanıcıya arayüzle sunabilir; ama temel değer zaten sorgulanabilir verinin hazır olmasındadır.

### Hücre 42 — Doğrudan SQL ile 18 aylık özet

Çıktı 2025-01 ile 2026-06 arasındaki 18 ayı getiriyor:

- Kredi stoku 523.455 milyon TL’den 801.438 milyon TL’ye yükseliyor.
- Faiz %40,54’ten dönem içinde %34,66’ya kadar inip tekrar %40,34’e geliyor.
- Reel aylık kredi stoku değişimi dalgalı: bazı aylarda negatif, bazı aylarda pozitif.

Bu tablo, “AI olmadan da doğru kolonları bilen biri sorusunu cevaplayabilir” mesajını verir. Agent’in görevi bu erişimi teknik olmayan kullanıcı için kolaylaştırmak olmalıdır; verinin yerine tahmin yapmak değil.

### Hücre 43 — Frekans ve zaman hizalaması bölümü

Farklı veriler aylık veya çeyreklik gelir. Bu bölüm, bunların aynı analize konurken yanlışlıkla çeyreklik değerin her aya kopyalanıp kopyalanmadığını kontrol eder.

Bu önemlidir: Mart çeyrek sonu ölçümünü Nisan ve Mayıs için de varmış gibi göstermek yanlış bir bilgi üretir.

### Hücre 44 — Hizalama kontrolleri

Sonuçlar:

- Aylık analiz satırı: 66 / 66 `PASS`.
- Çeyreklik analiz satırı: 22 / 22 `PASS`.
- İl-çeyrek paneli: 1.782 / 1.782 `PASS`.
- Tekil il-çeyrek anahtar sayısı: 1.782 / 1.782 `PASS`.
- EVDS aylık panelinde çeyreklik serilerin ara aylara taşınması için ek denetim bilgisi veriliyor.

Bu, zaman boyutunun düzenli ve tekrarsız olduğunu destekler. Son satırdaki `INFO`, seri bazında alignment audit tablolarına ayrıca bakmak gerektiğini söyler; hata anlamına gelmez.

### Hücre 45 — Kümülatif BDDK verisinin aylık akıma ayrıştırılması

Bazı BDDK kalemleri yıl başından itibaren biriken toplamlar olarak yayımlanır. Örneğin yılın Mart ayındaki değer, Ocak+Şubat+Mart toplamı olabilir. Bu hücre, builder’ın aynı yıl içinde ardışık ayların farkını alarak aylık akımı hesapladığını açıklar.

Ocak ayı özel durumdur: önceki yılın Aralık değerinden çıkarılmaz; doğrudan kaynak değeri olarak korunur.

### Hücre 46 — Kümülatif akım dönüşümünün doğrulaması

Çıktı:

- 104.940 kümülatif ölçüm satırı işlenmiş.
- 9.540 Ocak başlangıç satırı var.
- Kaynak değer ile aylık akımların tekrar toplanmış değeri arasındaki en büyük fark **0,0**.

Bu çok güçlü bir teknik doğrulama: Builder’ın hesapladığı aylık akımlar tekrar toplandığında resmî kaynağın kümülatif sayısına aynen dönüyor.

Ek bilgi olarak, 7.069 Ocak satırında yeni yıl başındaki mutlak değer önceki yıl sonundan daha düşük; bu da yılbaşında kümülatif sayacın sıfırlanmasının beklenen davranış olduğunu destekliyor.

### Hücre 47 — Genel kalite özeti bölümü

Bu bölüm, dönem tekrarları, TÜİK-EVDS uyumu ve resmî kaynak boşluklarını tek bir kısa tabloda toplar.

### Hücre 48 — Genel kalite özeti

Sonuçlar:

- Aylık analizde tekrar eden dönem: 0 (`PASS`).
- Çeyreklik analizde tekrar eden dönem: 0 (`PASS`).
- TÜİK–EVDS değer uyuşmazlığı: 0 (`PASS`).
- TBB’de açıklanmış kaynak boşluğu: 1 (`INFO`).

Bu sonuç, temel analitik tablolarda aynı ayın veya çeyreğin iki kez yazılmadığını ve iki resmî satış kaynağının ortak verilerde çelişmediğini gösterir. TBB boşluğu hata olarak gizlenmemiş; açıkça belgelenmiş.

### Hücre 49 — Generik yapı bölümü

Bu bölüm, lakehouse’un yalnız konut kredisi için yazılmış dar bir tablo seti olmadığını anlatır. Katalog tablosunda kaynak, frekans, birim, kapsam, kaynak izi ve gözlem durumu ortak biçimde tutulmaktadır.

### Hücre 50 — Generik katalog sonuçları

Üç çıktı üretilir:

1. Metrikler; kaynak, frekans ve birim bazında gruplanır. Örneğin FinTürk’te `count`, `percent`, `thousand_try` ve `try_per_person` gibi farklı birimler vardır.
2. Veri varlıkları; kaynak sistemi ve veri türüne göre gruplanır. Bu, ham tablo, gözlem tablosu, audit, boyut tablosu ve analiz panelinin aynı katalog sözleşmesinde tanımlanabildiğini gösterir.
3. Lakehouse tabloları ve kaynak yolları listelenir. Böylece her tablo fiziksel kaynağına kadar izlenebilir.

Sade anlamı: Gelecekte turizm, enerji veya başka bir finans dışı veri ailesi eklenirse, aynı katalog ve kaynak izi modeli kullanılabilir. Ancak yeni kaynağın indirme, temizleme ve kalite kontrol kuralları yine ayrıca geliştirilmelidir.

### Hücre 51 — Generik yapı sonucunun sınırı

Son hücre çok yerinde bir ayrım yapar: Katalog ve metadata modeli geneldir, fakat sistem henüz tamamen otomatik bir agent değildir. Yeni bir kaynağı eklemek için o kaynağa özel indirici, normalizasyon ve kalite testleri yazılması gerekir.

---

## Veriyi tek cümlede nasıl anlatırsın?

> Bu lakehouse, Türkiye’de konut piyasası ve kredileri anlamak için BDDK, TCMB, TÜİK ve TBB’nin resmî verilerini aynı yerde; kaynağı, dönemi, birimi ve eksiklik uyarısıyla birlikte sorgulanabilir hâle getiriyor.

## Bu veriyle güvenle söylenebilecekler

- Konut kredisi stokunun dönemsel gelişimi izlenebilir.
- Faiz, reel kredi stoku ve ipotekli satış oranı birlikte incelenebilir.
- İller, satış hacmi, ipotekli satış payı, kredi yoğunluğu ve bölgesel fiyat göstergeleriyle karşılaştırılabilir.
- TÜİK ve EVDS’de ortak bulunan satış değerlerinin 25.262 gözlemde birebir aynı olduğu gösterilebilir.
- Kümülatif BDDK değerlerinin aylık akıma dönüşümünün matematiksel olarak geri doğrulandığı gösterilebilir.
- Veri eksikleri saklanmadan, “kaynakta yayımlanmadı” veya “analitik olarak kimlikle çözüldü” şeklinde açıklanabilir.

## Bu veriden doğrudan söylenemeyecekler

- “Faiz düşünce satış kesin arttı” denemez.
- “Kredi stoku arttı, o ay yeni kredi kullandırımı da arttı” denemez.
- TBB, BDDK ve FinTürk değerleri kapsamlara bakmadan eşit kabul edilemez.
- `NULL` değer sıfır kabul edilemez.
- İl kümeleri kredi riski, yatırım tavsiyesi veya kesin sınıflama değildir.
- Basit korelasyon ve regresyon sonuçları nedensellik kanıtı değildir.

## Hackathon demosu için en iyi anlatım akışı

1. Kullanıcı bir soru sorar: “Faiz, konut kredisi ve ipotekli satışlarda son bir yılda ne değişti?”
2. Sistem `analysis.housing_credit_monthly` üzerinden 66 aylık güvenilir özet tabloyu seçer.
3. Cevap, sayıları grafikle verir; her grafikte dönem, birim ve resmî kaynak görünür.
4. Eğer TBB 2026-06 gibi boş bir alan varsa, sistem “veri yok” der; sıfır veya tahmin göstermez.
5. Kullanıcı il bazına inerse `regional.housing_quarterly` kullanılır; `analysis_ready` ve eksik fiyat kapsamı görünür.
6. Sistem sonuçla birlikte “bu korelasyondur, nedensellik değildir” uyarısını gerektiğinde ekler.

Bu yaklaşım, agent’in en güçlü tarafını oluşturur: yalnız cevap vermek değil, cevabın kaynağını ve sınırını da açıkça göstermek.

