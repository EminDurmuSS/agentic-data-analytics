# Kaynak okuma ve yayınlama: gerçek veri kanıtları

Asıl kanıt klasörü: `/Users/edurmus/Downloads/kkb-hackathon-2026-notlar/mentor-improvements-2026-09-12/source-validation`. Dosya adları bu klasöre göredir.

Bu rapor 12 Eylül 2026 geliştirme çalışmasının kaynak araçları doğrulamasını özetler. Ham dosyalar, içerik özetleri, hazırlanmış aday tablolar, yayın sözleşmeleri, veri dosyaları ve analiz/grafik kayıtları `store/` altında bulunur. Ana çalışmadaki otonom model denemeleri ayrı `live-*` klasörlerindedir. Buradaki araç düzeyindeki başarılar modelin her soruda aynı akışı tek başına kurduğu anlamına gelmez.

## Mimari düzeltme: model teknik veri sözleşmesi yazmıyor

Önceki otonom denemeler, doğru kaynak ve hücrelere ulaşmasına rağmen tarih başlığı, sütun eşlemesi ve sayı biçimi sözleşmesini elle kurarken tekrar tekrar duruyordu. `FinancialImportTools.ingest_source_table` artık gerçek kaynak kalemlerini ve dönemlerini tek çağrıda kaynak adresli `line_item / period / amount` kayıtlarına derleyip yayınlıyor. Model yalnız kaynak/tablo kimliği, gerçek kalem adları veya satır seçimi, istenen dönemler ve gerektiğinde `Total` gibi kaynak alt başlığı veriyor. Tarihler istekten eklenmiyor, kaynakta okunmuş tarihlere filtre uygulanıyor.

Derleyici kaynak metninin doğruladığı etiket birleşimlerini, tek anlamlı tarih hücrelerini veya açıkça tekrarlanan başlık gruplarını, ortak para birimi ve ölçek alıntısını, sayısal biçimi ve satın alma gücü esasını kendisi çıkarıyor. En yakın tarih tahmini veya finansal büyüklükten sayı biçimi tahmini yok. Nokta/binlik ayrımı gerçekten belirsizse kaynakta basılı `I+II` gibi küçük toplam eşitlikleri yalnız özgün satır kodları, hücre koordinatları ve kesin aritmetik ile sınanabiliyor. Tek bir sayı yorumu kalmazsa kullanıcıya kaynak örnekli biçim seçenekleri dönüyor.

Derleme yalnız bir çalışma alanı yayını yapar. Hazırlanmış aday özeti, özgün kaynak özeti, inceleme özeti, kesin yayın argümanları ve ilk yayın anahtarı önceden kaydedilir. Yayından önce kaynak veya inceleme değişirse eski derleme yeni hücreleri yayınlayamaz. Kesinti sonrasında, daha yeni bir inceleme olsa bile zaten yayınlanmış özgün veri kümesi aynı kimlikle bulunur. Bu iki durum gerçek Tüpraş kaynağında ayrıca bağımsız olarak doğrulandı.

Yayın sonucu hazır `aggregate_dataset` çağrısı verir. `source_value` her kalem/tarihte tam bir kaynak kaydını kopyalar; dönem veya farklı kalem toplamı yapmaz. Gelir tablosu başlığının desteklediği `flow` türü korunur, başlangıç tarihi belirsizse `reported_interval_unresolved` ve `review_required` kaydedilir. Bu, kaynağın kümülatif olduğuna ilişkin uydurma bir iddia değildir. Doğrudan gösterim mümkündür, belirsiz dönemi toplamak mümkün değildir. Kaynak türü bilinmiyorsa modelin `stock` tercihi tek başına aritmetik yetkisi sağlamaz.

Mevcut verilerle birleştirme için yayın cevabı ayrıca `available_series` kataloğu verir: değiştirilemez yayın verisinden okunan gerçek metrik kimliği, `line_item` boyutu, gözlenen tarihler, tür, inceleme durumu, para birimi ve ölçek. Model kaynak boyutunu tahmin etmek zorunda değildir. Katalog en fazla 30 kalem ve kalem başına 12 tarih gösterir; toplamlar ve kısaltma işaretleri açıktır. Bağımsız belge gösterimi hazır `analysis_request` kullanır; mevcut metrikle ortak hesap doğrudan bu katalogdaki seçimlerle `execute` kullanır. Gerçek yayınlanmış Garanti toplam aktifleri için katalog ve çalışma alanının değişmediği `pdf-sector-integration/available-series-real-source-proof.json` ile doğrulandı. Son katalog düzenlemesinde 71 belge/derleyici testi ve 29 alt test geçti.

Gerçek belgelerle ilk genel API kabulü üç kurumdan 10 tutarı, para ölçeğini, satın alma gücü esasını ve sıralama sonrası kaynak hücrelerini doğruladı: Garanti 4, Tüpraş 2 ve Akbank 4 kayıt. Çağrılarda teknik sözleşme, tarih eşlemesi, satır numarası, sayısal sütun adı veya sayı biçimi verilmedi; yalnız Garanti için gerçek `TOTAL` alt başlığı seçildi. Analiz ve grafik kanıtları da kontrol edildi. Kanıtlar kardeş klasörde `../high-level-ingestion-acceptance/independent-public-api-round1/`; bu ilk tur sonraki inceleme kimliği ve dönem anlambilimi düzeltmelerinden önceki ayrı, korunmuş kayıttır.

Ana uygulamada aynı doğal dil sorularıyla yapılan ilk mimari denemede Garanti görevi 101,065 saniye/12 kararda, Tüpraş görevi 69,603 saniye/9 kararda tamamlandı; ikisi de bağımsız kaynak ve grafik denetiminden geçti. Kayıtlar `../live-highlevel1/` altında. Son sabit sürümün tekrar sonuçları ana çalışma raporunda ayrıca bulunur. Bu iki başarı evrensel PDF başarısı veya yarışma derecesi garantisi değildir.

Derleyici sıradan satır bazlı CSV tablolarını finansal tablo satırlarına çevirmeye çalışmaz: `unsupported_layout`, `publication_performed=false` ile mevcut genel yayın akışına geçiş verir. Gerçek tarih, kapsam, birim veya inceleme belirsizliğini aynı etiketle gizlemez; bunlar yayın öncesi engellenir. Eksik değer sıfıra çevrilmez. Aynı dönem sonuna ait ilk yarı ve ikinci çeyrek sütunları dönem başlangıcı belli olmadan birleştirilmez. Akbank sayfa 9 bu sınıra ilişkin gerçek olumsuz kabul örneğidir.

Sonraki Garanti tekrarında model, düzgün metin çıkarımını okuduktan sonra yanlışlıkla inceleme gerektiren çizgi adayını tekrar seçti. Bu gerçek başarısızlık `../live-chart-final/garanti_pdf_2/` altında korundu. İnceleme hatası artık aynı ham kaynağın aynı sayfasındaki bağımsız, makineyle ayrıştırılmış ve inceleme işareti taşımayan alternatiflerini gösteriyor. Kalemler tek anlamlı eşleşiyorsa yalnız tablo kimliğini değiştiren hazır `suggested_ingest_arguments` veriyor; kalem, dönem ve açık kapsam seçimleri korunuyor. Satır numarası veya sayısal sütun adı gibi adaya özgü adresler farklı çıkarıma taşınmıyor. OCR, eksik formül değeri, bozuk düzen veya önceki hazırlama sonucu alternatif kabul edilmiyor. İlk adayın inceleme durumu kaldırılmıyor, otomatik yayın yapılmıyor.

Gerçek başarısız çağrının salt okunur tekrarında uygun `table_p000011_text_001` adayı bulundu ve çalışma alanı değişmedi: `garanti-same-page-alternative-recovery.json`. Bu düzeltmenin hedefli derleyici testleri 18 test ve 6 alt testle geçti. Bu kayıt modelin düzeltilmiş çağrıyı uyguladığını iddia etmez; sonraki canlı tekrar ana çalışma tarafından ayrıca değerlendirilir.

## Gerçek finansal kaynaklar

| Kaynak ve gerçek biçim | Yapılan işlem | Doğrulanan sonuç | Kanıt |
| --- | --- | --- | --- |
| [Garanti BBVA 31 Mart 2026 konsolide raporu](https://www.garantibbvainvestorrelations.com/en/images/pdf/31_March_2026_Consolidated_Financial_Report.pdf), 141 sayfa | PDF sayfa 11, alternatif metin tablo çıkarımı; birleşmiş tarih başlıklarının gerçek hücre adresleriyle hazırlanması; uzun tabloya dönüştürme; yayın; günlük stok gözlemi; gruplu grafik | Finansal varlıklar 31.03.2026: 1.279.933.463, 31.12.2025: 1.260.568.409; nakit ve nakit benzerleri: 867.799.356 ve 1.005.229.845. Hepsi bin TRY, konsolide TL+FC toplamıdır. | `garanti-end-to-end-row-order-verified.json`, `garanti-page11.png` |
| [Tüpraş 30 Haziran 2025 konsolide raporu](https://www.tupras.com.tr/assets/uploads/financial-reports/tupras-consolited-cmb-30062025.pdf), 51 sayfa | PDF sayfa 3; parçalanmış etiketleri birleştirme; yalnız gerçek mevcut dönem sütununu seçme; statik yayın; metrik bazında kaynak değerini koruyan analiz; çubuk grafik | Nakit 90.351.730, toplam varlıklar 545.630.257. Birim bin TRY; 30.06.2025 satın alma gücü esası korunur. İki kalem toplanmaz. | `tupras-end-to-end.json`, `tupras-page3.png` |
| [Akbank 2026 ilk yarı konsolide raporu](https://www.akbankinvestorrelations.com/en/images/pdf/consalidated/akbank_en_consolidated_2q26.pdf), 104 sayfa | PDF sayfa 10; sınırları olmayan tablo için metin stratejisi; gerçek tarih başlıklarını açma; parçalı kalem adlarını birleştirme; muhasebe parantezli negatif sayıları okuma; yayın | Dönem kârı 2026 ilk yarı: 34.333, 2025 ilk yarı: 24.850; diğer kapsamlı gelir: -8.207 ve 19, milyon TRY. Yıl içi kümülatif değerler aylık akış gibi sunulmaz. | `akbank-income-publication.json`, `akbank-income-text.json` |
| [İşbank yatırımcı sunumu](https://www.isbank.com.tr/contentmanagement/IsbankInvestorDocumentsEN/pdf/InvestorPresentation.pdf), 31 sayfa | İlk 30 sayfa; son sayfayı sonradan inceleme; ilk aday kimliklerini koruma; sayfa 29 iştirak tablosu seçimi; USD ölçekli yayın | Anadolu Hayat: varlık 10.408.721, özkaynak 281.200; TSKB: varlık 7.979.762, özkaynak 1.085.335. Kaynaktaki birim bin USD. | `isbank_holdings_publication.json`, `isbank-page29.png`, `isbank-page31.json` |

Garanti ve Tüpraş rakamları kaynak sayfası görüntüsünden ayrıca kontrol edildi. Hazırlama işlemi sayıları model tarafından üretilen yeni değerlerle değiştirmez. Garanti cari tarih için aday satır 10/sütun 7; önceki tarih için aynı satırın sütun 9 ve 10 hücrelerini kullanır. Çıktıdaki her tarihin ve tutarın kaynak hücre adresi yayın kanıtında saklanır. İşbank sunumu finansal tablo alımına ek örnektir; banka bilançosu yerine geçirilmez.

## Yeni PDF ile mevcut sektör verisinin aynı hesapta birleşmesi

İlk derleyici başarısı, belgenin kendi tablosu ve grafiğini kapsıyordu. Sonraki kontrolde önemli bir sınır bulundu: gerçek belge tarihleri `event` olarak korunuyor, fakat genel çapraz kaynak hesap motoru bu sıklığı kabul etmiyordu. Bu nedenle PDF ile mevcut aylık sektör verisini tek oranda birleştirmek ayrıca tamamlandı. Eski ret, çalışma alanını değiştirmeyen `pdf-sector-gap-before-period-end.json` kaydında korunuyor.

Yeni `period_end` hizalaması yalnız incelemesi tamam stoklarda gerçek gözlem günü istenen takvim döneminin son gününe birebir eşitse çalışır. Verinin asıl `event` sıklığı, tam tarihi ve hücre adresleri korunur. Yakın tarihi seçmez, ileri taşımaz, toplamaz; dönem sonu dışında gözlem varsa reddeder. Eksik aylar doldurulmaz.

Taze ve yalıtılmış finans çalışma alanında gerçek Garanti PDF'si yüksek düzeyli araçla alındı. Çağrı yalnız `TOTAL ASSETS`, `2026-03-31` ve `TOTAL` seçimini kullandı. Bu kalem yukarıdaki finansal varlıklar alt toplamından farklıdır: PDF sayfa 11, aday satır 60/sütun 8, **4.783.750.292 bin TL**. İçerideki BDDK bilanço tablosunun `bddk_monthly:table01:26:f7e2a5324c36:Toplam` metriği, `group_code=10001` sektör toplamı, Mart 2026 için **49.735.194 milyon TL** verir. BDDK değeri özgün kayıtlı yanıtın `Json.data.rows[25].cell[6]` adresinden ayrıca okundu; dosya özeti katalog kaydıyla eşleşti.

Aynı kaydedilmiş analizde Garanti değeri **4.783.750,292 milyon TL** olarak ölçeklendi. Bölme ve yüzde işlemi **%9,618441001758232** verdi; bağımsız Decimal hesabıyla mutlak fark `1e-12` altındadır. Garanti grubunun mali iştirakler dahil konsolide kapsamı ile BDDK sektör istatistiğinin kapsamı eşdeğer kabul edilmez. Hesap açık `explicit_comparison` ve kaynak kapsamı açıklaması taşır; resmi pazar payı diye adlandırılmaz.

İki tutarı ve oranı kapsayan iki grafik, dört hücre açıklaması, özgün PDF ve BDDK yanıt özetleri, kaynak tarihleri, sıralanmış veri ve grafik bağları denetlendi. **26 kontrol geçti**, bunların içinde çalışma alanını değiştirmeyen üç olumsuz deneme var: açık kapsam seçimi olmadan oran, olay tarihli stokta `last` ile taşıma ve Mart tutarını yıl sonuna eşleme. Taşınabilir küçük kanıt [pdf-sector-acceptance.json](pdf-sector-acceptance.json); tam kayıtlar `pdf-sector-integration/result.json` ve `pdf-sector-integration/acceptance.json` altında. Bu kanıt gerçek veriyle araç/API akışıdır; sonraki otonom model tekrarının sonucu ana raporda ayrı değerlendirilir.

## Birleşmiş tarih başlıkları ve yayın ergonomisi

Tüpraş kaynağında iki tarih üç hücreye bölünmüştür: aday satır 8, sütun 6 `3`, sütun 7 `0 June 2025 31 De`, sütun 8 `cember 2024`. `period_sources` bu gerçek hücreleri boş ayraçla birleştirir; `english_dmy` ve `date_index=0/1` kaynak metnindeki ilk/ikinci tam tarihi seçer. Modelden yeni bir tarih alınmaz. Birleştirilmiş özgün metin, seçilmiş tam tarih, sıra numarası ve hücre adresleri kanıtta saklanır.

Bu işlemle hem 30.06.2025 hem 31.12.2024 değerleri yayın, günlük stok analizi ve grafik aşamalarından geçti: nakit 90.351.730 / 85.795.568; toplam varlıklar 545.630.257 / 529.848.729, bin TRY. Her iki dönem de kaynakta belirtilen 30.06.2025 satın alma gücüyle korunur. Güncel hücre kanıtı `tupras-two-period-end-to-end-row-order-verified.json`.

Yayın eşlemesinin yönü şemada açıklandı: kaynak sütunu -> çıktı sözleşmesi sütunu. Tam ve bire bir ters eşleme yalnız yönü matematiksel olarak tek anlamlıysa çevrilir; asıl eşleme ve yön düzeltmesi kanıtta tutulur. Eksik veya aynı kaynağı çoğaltan eşleme reddedilir. Hata, gerçek kaynak/çıktı sütunlarını ve eksik eşlemeyi açıklar. Hazırlama çıktısı ayrıca boş seçili sütunları ve yayınlanacak gerçek sütun adlarını gösterir.

Tarih çözümlemesi başarısız olduğunda araç artık seçilmiş başlık satırının gerçek sıralı hücrelerini, en fazla dört komşu hücrenin birleştirilmesinden okunabilen geçerli tarihleri ve kaynak adresli tekrar tarifini döndürüyor. Otomatik öneri için tarih sayısı ve değer sütunu sayısı aynı olmalı, sıralama ve kaynak hücre kapsamı eşleşmeli ve yalnız tek geçerli tarih yorumu bulunmalı. Örneğin komşu bir `2` hücresinin `8 February` ile birleşip birleşmeyeceği belirsizse 8/28 tercihi yapılmıyor. `tupras-source-header-recovery.json`, gerçekten başarısız olan kaynak seçiminin aracın kendi verdiği tarifle düzelmesini gösterir.

`publish_selected_table` sayısal biçim seçeneklerini sözleşme içinde veya üst düzey açık parametre olarak kabul eder. Çelişen iki tanım reddedilir; uygulanan biçim ve tanımın yeri kaynak kanıtında tutulur. Bu ergonomi düzeltmesi sayılara otomatik biçim tahmini yapmaz.

## Kaynak hücresi sırasındaki hata ve doğrulanmış düzeltme

Bağımsız kontrolde önemli bir hata bulundu: saklanan veri sözleşmedeki türlere çevrildikten sonra anahtarla sıralanıyor, ancak kaynak hücresi listesi önceki satır sırasında kalıyordu. Rakamlar doğruyken belirli bir saklanan satır yanlış kaynak hücresini işaret edebiliyordu. Önceki değiştirilemez veri kümeleri veya analizler yeniden yazılmadı. Eski kanıt JSON'ları `*-before-row-order-fix.json` adlarıyla ayrıca korundu; bu dosyalar doğru hücre izleme kanıtı sayılmamalıdır.

Düzeltme, aynı gerçek tür dönüşümü ve sıralama işleminden çıkan özgün CSV kayıt indislerini kullanır. Kaynak hücreleri saklanan satır sırasına geçirilir; `row_order.stored_row_to_source_csv_row` ve CSV özeti kaydedilir. Sütun adresleri çıktı sözleşmesi adlarına eşlenir. İnsan tarafından düzeltilmiş hücreler özgün makine hücresi gibi gösterilmez; ayrı `review_row` ve `review_sha256` taşır.

Garanti ve Tüpraş için yeni veri kümeleri, analizler ve grafikler üretildi. Toplam sekiz saklanan tarih/tutar/etiketin tamamı gerçek PDF adayındaki kaynak koordinatına geri gidilerek doğrulandı. Yeni kanıtlar `garanti-end-to-end-row-order-verified.json` ve `tupras-two-period-end-to-end-row-order-verified.json`. Ayrıca hazırlanmamış, tarihi ve sayısal kimliği sırasız kaynakta gerçek tür dönüşümü sonrası sıra test edildi; metin olarak sıralama kullanılmadı.

## Büyük yıllık rapor

[Akbank 2025 entegre yıllık raporu](https://www.akbankinvestorrelations.com/en/images/pdf/akbank_integrated_annual_report_2025.pdf), 19.390.970 bayt, önceki 16 MiB sınırını aşıyordu. Yeni sınırlı 32 MiB indirmeyle alındı; PDF 335 sayfa. İlk sayfa incelendi, kaynak içi başlık araması süre sınırında ilk 281 sayfayı taradı ve kalan sayfaları `next_start_page=282` ile açıkça bildirdi. Toplam süre 43,466 saniye. `annual-report-32mib-validation.json` yalnız indirme ve başlık bulma kanıtıdır; bütün raporun tabloları çıkarılmış veya doğrulanmış değildir.

## Web araştırması

Gerçek uzun sorgu: `TCMB Para Politikası Kurulu 6 Mart 2025 faiz kararı bir hafta vadeli repo ihale faiz oranı`.

Son başarılı deneme, resmi kurumun yıl arşivinden [6 Mart 2025 karar sayfasına](https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Duyurular/Basin/2025/DUY2025-15) ve aynı kararın PDF dosyasına ulaştı. Kaynak metni politika faizinin yüzde 45'ten yüzde 42,5'e indirildiğini doğruladı. Süre 5,597 saniye; bütçe en fazla 18 kaynak okuması. Kanıt `final-research-tcmb-long-query3.json`.

İkinci kurumda `İşbank 2026 H1 financial results` sorgusu [gerçek ilk yarı kazanç sunumuna](https://www.isbank.com.tr/contentmanagement/IsbankEarnings/pdf/IsbankEarningsPresentation.pdf) ulaştı. Süre 12,248 saniye; kanıt `final-research-isbank-after-synonym-fix.json`.

Daha önceki yanlış konuya giden ve kaynak bulamayan denemeler silinmedi. Özellikle `final-research-tcmb-long-query.json`, `final-research-tcmb-long-query2.json` ve `final-research-isbank-after-archive-fix.json` başarısız geliştirme denemeleridir; son başarı kanıtı olarak kullanılmamalıdır. Özel bir karar URL'si veya şirket tablosu kod içine yerleştirilmedi. Eşleştirme tarih, konu, kaynak alan adı, arşiv bağlamı ve bağlantı metnini kullanır.

Varsayılan Bing RSS her sorguda iyi sonuç vermiyor. Operatörün yerel SearXNG kurması destekleniyor; bu klasörde genel internet üzerinden çalışan gerçek SearXNG servisi denemesi yok. Yerel HTTP protokol testi ana test kümesinde bulunur. Kaynak sonucu ile arama motorunun cevabı ayrı tutulur; bozuk sonuç girdileri uydurma veriyle doldurulmaz.

## Taranmış belge ve yapı belirsizliği

Garanti kaynak sayfasının gerçek görüntüsünden ana çalışma tarafından alınmış OCR model çıktısı ayrıca tekrar işlendi. Model 47 satırda 9 hücre vermiş, 8 başlık yazmıştı. Güvenli yapısal hipotez eksik ilk kalem kodu başlığını ekler; hiçbir sayı silinmez veya kaydırılarak onaylanmaz. `garanti-ocr-recovery.json`, gerçek ham makine hücrelerinin tamamının korunduğunu gösterir. Bu yeniden işleme yeni OCR çağrısı veya 47 satırın bağımsız doğrulanması değildir.

OCR çıktısı bağımsız hücre incelemesi yapılmadan yayınlanamaz. Farklı genişlikteki satırlar doldurulmadan, kırpılmadan ham haliyle tutulur. Kesilmiş model çıktısı da hata kanıtı olarak saklanır. HTML `100<br>200` gibi iki görsel satır `100200` sayısına dönüştürülmez; inceleme gerektirir.

## Sınırlar ve henüz kanıtlanmayan kapsam

- Her internet sitesi veya her PDF için evrensel doğruluk iddiası yok. Görsel olarak karmaşık tablolar alternatif çıkarım, sayfa seçimi veya açık insan incelemesi gerektirebilir.
- Bir PDF incelemesi en fazla 30 sayfa ve 30 tablo adayı işler. Sayfa ve tablo sınırları çıktıda görünür; özgün PDF uzun olduğu için bütünüyle reddedilmez. Belge indirme ve yükleme sınırı 32 MiB, görsel sınırı 8 MiB olarak korunur. Daha düşük açık yapılandırma sınırları da uygulanır.
- Açıkça uyumlu ardışık sayfa tabloları birleştirilebilir. Başlığı veya kapsamı farklı tablolar otomatik birleştirilmez.
- XLSX formülleri çalıştırılmaz. Dosyaya kaydedilmiş hesap sonuçları korunur; eksik önbellek değerleri inceleme gerektirir. Kaydedilmiş bir sonuç dosya tarihinden sonra yeniden hesaplanmış gibi sunulmaz. [openpyxl veri okuma belgeleri](https://openpyxl.readthedocs.io/en/stable/tutorial.html)
- Kümülatif akış, para birimi/ölçek çelişkisi, bilinmeyen toplama kuralı ve satın alma gücü esası belirsizliği sessizce aşılmaz. Net kârın ilk yarı değeri aylık akışa çevrilmedi.
- BDDK ek dosyası alma denemesi ağ hatası verdi (`bddk_march2025.json`). Şişecam kaynağındaki HTTP 403 ana çalışma tarafından kaydedildi. Bu kaynaklar başarılı alım sayılmadı.
- Buradaki CSV/XLSX/HTML özel köşe durumları bağımsız küçük test girdileriyle sınandı. Canlı CSV/XLSX kurumsal kaynakları için kapsamlı bir kıyas veri kümesi tamamlandığı iddia edilmiyor.

## Teknik denetim

Açık kaynaklı yerel ayrıştırıcılar kullanılır: pdfplumber/pypdf, openpyxl, standart Python HTML/CSV okuyucuları. PDF çizgi ve metin stratejileri [pdfplumber resmi açıklamasına](https://github.com/jsvine/pdfplumber/blob/stable/README.md) göre ayrı adaylar üretir. Yeni zorunlu kapalı kaynaklı hizmet eklenmedi. Yarışmanın mevcut model entegrasyonu ana uygulama tarafından yönetilir.

Son hedefli test komutu:

```text
.venv/bin/pytest tests/agent/test_financial_import.py tests/agent/test_agent_documents.py tests/lakehouse/test_lakehouse_store.py tests/agent/test_source_page_search.py tests/app/test_agent_app.py tests/app/test_agent_app_precision.py -q
```

Sonuç: 111 test ve 59 alt test geçti (4,43 saniye), 2 mevcut bağımlılık kullanımdan kaldırma uyarısı. Bunun 15 testi yeni derleyiciyi kapsar: gerçek hücre sırası, kaynak başlık grupları, sayı belirsizliği, kaynakta basılı aritmetik kanıtı, eksik değer, satın alma gücü, inceleme kapısı, yayın kesintisi ve inceleme sonrası kimlik, bilinmeyen türü modelle yükseltmeme, sıradan CSV için genel yayın geçişi. `git diff --check` temiz. Bu, tüm depo testleri veya son otonom model tekrarı yerine geçmez; onların raporu ana çalışmada bulunur. Kod ve ana kanıt dosyalarının SHA-256 değerleri `validation-manifest.json` içindedir; derleyici son sürüm kanıtı ayrıca `compiler-validation-manifest.json` dosyasındadır.


## Yapısal alternatif ve tarih alt kümesi doğrulaması

Son otonom Tüpraş tekrarında iki ayrı sorun görüldü: çizgi çıkarımındaki toplam varlık satırı sayısal satır olarak eşleşmiyordu; daha sonra doğru metin adayı ve doğru `column_7` seçimi birleşik başlığın tarih bağlamını kaybediyordu. Aynı sayfadaki diğer çıkarımı bulmak ile doğru kaynak sütununun tarihini korumak ayrı düzeltildi.

Derleyici, hiç eşleşmeyen iş satırı, hizalanamayan tutar sütunları veya yapısal tarih/etiket belirsizliği için aynı kaynak ve sayfadaki bağımsız, inceleme gerektirmeyen adayları gösterir. Doğrudan yeniden deneme önerisi, istenen etiketlerin tekil eşleşmesi ve kaynak tarihleriyle doğrulanması şartına bağlıdır. Eski adayın satır/sütun indisleri başka adaya taşınmaz; tekrarlanan kapsam etiketi, OCR incelemesi, para birimi veya dönem belirsizliği onaylanmış sayılmaz. Yayın otomatik yapılmaz.

Tarih eşlemesi önce tam kaynak tutar/başlık grubu üzerinde kanıtlanır, sonra açık sütun seçimine daraltılır. Aynı kaynak sütunu tek başına seçilince tarihini değiştiremez. Tüpraş son başarısız çağrısındaki `row_numbers=[12,38]` ve `value_columns=[column_7]` doğru seçimlerdi; aynen tekrarlandığında artık 30 Haziran 2025 tarihli 90.351.730 ve 545.630.257 değerleri yayımlandı. Yalnız `column_8` seçimi 31 Aralık 2024 tarihli 85.795.568 ve 529.848.729 değerlerini korudu.

Gerçek başarısız kaydın salt kaynak kopyalarıyla üç ayrı çalışma alanında doğrulama yapıldı: ilk iş etiketi hatasından aynı sayfa alternatifine geçiş, yalnız cari dönem, yalnız önceki dönem. Toplam sekiz tutar ve tarih koordinatı özgün PDF adayıyla birebir eşleşti; bin TRY ölçeği ve `purchasing_power_2025-06-30` esası korundu. Her çalışma alanında bir yayın gerçekleşti. Eski canlı günlük ve veri kümeleri değiştirilmedi. Kanıt: `structural-recovery-final/acceptance.json` ve aynı klasördeki tam sonuçlar. Bu araç düzeyinde yeniden oynatmadır; yeni otonom model başarısı iddiası değildir.

Bu son yapısal değişikliğin hedefli doğrulaması: 77 test ve 34 alt test geçti (2,20 saniye). Kaynak bulunamadığında boş kurtarma metaverisinin kontrollü hatayı bozmaması ayrıca sınandı. Dosya özetleri `compiler-structural-validation-manifest.json` dosyasındadır.
