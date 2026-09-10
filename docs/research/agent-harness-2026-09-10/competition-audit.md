# KKB Hackathon yarışma beklentileri ve agent kabul çerçevesi

Yarışmanın merkezinde, doğal dilde soruyu anlayan, uygun veriyi ve araçları seçen, çok adımlı analizi gerçekten çalıştıran, sonucunu doğrulayan ve istenen biçimde sunan genel bir analitik platform bulunuyor. Lakehouse bu platformun veri temelidir. Değerlendirme açısından belirleyici davranış, sonradan gelen veri ve sorular karşısında aynı motorun çalışmaya devam etmesidir. Sunumun ana akışı ile teknik açıklamalar bu beklentiyi birlikte ortaya koyuyor.[^1][^2]

Konut örneği, hesap yapma, aynı konuşmada sonucu koruyarak değiştirme, ek veri bağlama ve açıklamayı kanıtla destekleme yeteneklerini gösteriyor. Bir ürünün yalnız bu örnekte iyi sonuç vermesi, genel platform beklentisinin karşılandığını göstermez. Teknik konuşmada finans verileri çıkarıldığında sağlık verisiyle çalışabilme açıkça isteniyor.[^3][^4]

Bu denetim, 7 Eylül 2026 tarihli açılış sunumunun 31 sayfasına ve aynı toplantının otomatik konuşma dökümünün tamamına dayanır. Sunumdaki metin, orijinal PDF ile sayfa bazında karşılaştırılmıştır; yalnız başlık olarak çıkarılan 12. sayfanın grafik içeriği ayrıca değerlendirilmiştir. Konuşma atıfları 520 segmentlik otomatik dökümün zamanlarına dayanır; son segment 36:05.36'da biter. Sonraki Slack duyuruları bu kaynak kümesinde değildir. Dolayısıyla aşağıdaki takvim ve servis ayrıntıları açılışta açıklanan durumu temsil eder.[^20]

## Kaynakların bağlayıcılığı ve güven sınırı

**Açık gereklilik**, organizasyonun doğrudan istediği davranışı veya kısıtı ifade eder. **Örnek**, belirli bir soru, veri veya çıktı biçimi üzerinden gösterilen uygulamadır. **Teknik yönlendirme**, organizasyonun güçlü biçimde önerdiği tasarım yaklaşımıdır. **Önerilen kabul kontrolü**, bu raporun gerekliliği ölçülebilir hale getiren önerisidir; yayımlanmış jüri testi veya puan tablosu değildir.

PDF'de yazılı, konuşmada da tekrar edilen maddelerin ana anlamına güven yüksektir. Yalnız konuşmada bulunan maddeler otomatik döküm sınırlaması taşır. Dökümde Kloudeks için “CloudX”, “Cladex” gibi, Lakehouse ve agentic kavramları için de farklı çözümlemeler vardır. Metinde ürün adı PDF'deki yazımıyla kullanılır. Konuşmacı adları ve geçici model adları hakkında kesinlik üretilmez; konuşma tanıma puanları da insan tarafından doğrulanmış olasılıklar olarak yorumlanmaz.[^20]

Yazılı bir maddenin soru-cevapta açıklanması önemlidir. PDF 24. sayfadaki çalışır ve canlıda deploy edilmiş çözüm beklentisi, 24:46-25:11 aralığında yerel bilgisayarda demonun da yapılabileceği açıklamasıyla birlikte okunmalıdır. Buradan zorunlu bulut barındırma şartı çıkarılamaz. Çalışabilirlik, yeniden kurulabilirlik ve hazır bir hizmete temel görevi devretmeme beklentisi korunur.[^12][^14]

## Kesin görev kapsamı

| Konu | Kaynakta istenen | Sınıflandırma ve sınır |
| --- | --- | --- |
| Uçtan uca akış | Soruyu anlama, analiz planı, doğru veri/araç seçimi, gerçek hesap, doğrulama ve karar destek çıktısı | Açık gereklilik. Tek bir ürün ekranı veya belirli agent framework'ü zorunlu tutulmuyor. PDF s.7; video 08:02-09:11.[^1][^2] |
| Veri platformu | Temizlenmiş, ilişkilendirilebilir, kaynağı ve kapsamı kayıtlı bir lakehouse | Açık gereklilik. Bronze, silver ve gold katmanları konuşmada açıklanıyor; belirli tablo formatı veya ticari ürün adı zorunlu tutulmuyor. PDF s.7; video 06:44-08:02.[^1][^2] |
| BDDK | Haftalık Bülten, Aylık Bülten, FinTürk illere göre veri | Açık gereklilik. Her alt kaynağın gerçek frekansı ve anlamı kaynak metadata ile doğrulanmalı. PDF s.8; video 09:25-10:31.[^5] |
| EVDS | Tüm seriler içindeki veriler | PDF'de açık gereklilik. Bir metadata kataloğunun indirilmesi, bütün gözlemlerin edinildiği anlamına gelmez. PDF s.9; video 10:31-11:12.[^6] |
| Veri dönemi | Ocak 2021-Haziran 2026 | Açık gereklilik. Aylık karşılığı 66 aydır. Konuşmadaki yaklaşık “5 senelik” anlatım, yazılı tarih aralığını 60 aya düşürmez. PDF s.8-9.[^5][^6] |
| Veri temini | Kaynaklara ekiplerin kendilerinin erişmesi | Açık gereklilik. Organizasyon hazır veri dosyası paylaşmayacağını söylüyor. Video 12:18-12:33.[^3] |
| Frekans ve birim | Tarih, frekans, birim uyumu; kalite ve eksik veri kontrolü | Açık gereklilik. Kaynağın genel tanıtımındaki haftalık/aylık ifadesi, daha seyrek bir ölçüyü ara aylara uydurma izni değildir. PDF s.7-9; video 10:15-11:12.[^1][^5][^6] |
| Kümülatif veriler | Yıllık ve başlangıçtan beri biriken serileri ayırıp uygun biçimde düzenleme | Açık gereklilik. Her bilanço stokunu fark almak veya her aylık seriyi toplamak anlamına gelmez. PDF s.8; video 10:15-10:31.[^5] |
| Alan değişimi | Finans verileri çıkarılarak sağlık gibi başka alanın verileriyle çalışabilme | Konuşmada açık gereklilik. Sağlık ürününün yarışmada ayrıca tamamlanacağı söylenmiyor; teknik taşınabilirlik isteniyor. Video 12:33-12:55.[^3] |
| Sonradan gelen veri | Yeni URL ve veri kaynaklarını mevcut analize dahil etme | Açık gereklilik. Yalnız önceden hazırlanmış yerel tablolarla sınırlı bir demo beklenmiyor. PDF s.13,26; video 13:29-14:05 ve 17:49-18:22.[^7][^8] |

Kaynakta gerçekten bulunmayan bir gözlemi üretmek bu kapsamı tamamlamaz. “Tüm seriler” hedefi için katalogda kayıtlı seri, hedef dönemle ilişkisi, denenmiş indirme, dönen gözlem, sayısal gözlem ve sorgulanabilir veri ayrı izlenmelidir. Bu ayrım, veri temini ile kalite/doğrulama beklentilerini birlikte karşılamak için önerilen uygulamadır; organizasyonun yayımladığı ayrı bir sayım standardı değildir.[^1][^6]

## Altı asgari araç ailesi

Sunum altı araç ailesi sayar; konuşmanın 15:45-16:04 bölümü bunların minimum paket olduğunu ve başka araçlarla genişletilebileceğini açıklar. Altı araç ailesi, tam olarak altı Python fonksiyonu veya altı ayrı agent kurulması şartı değildir.[^9]

| Araç ailesi | Zorunlu davranış | Önerilen kabul kontrolü |
| --- | --- | --- |
| Lakehouse Tool | Doğal dilde veri keşfi, sorgulama, birleştirme ve analiz; doğru kaynaklardan bilgi bulma | Görülmemiş bir metrik sorusunda doğru birim, frekans ve kurum kapsamı seçilir; sorgu gerçekten çalışır; sonuç kaynak ölçümüne bağlanır. |
| Web Search Tool | Analiz için gerekli ek bilgi ve veriyi internette araştırma | Agent eksik dış bağlamı fark eder, uygun arama yapar ve iddiasını sonuç sayfasının gerçek içeriğine bağlar. |
| Web URL Agent Tool | Verilen URL'deki PDF, Excel, resim veya metinden anlam çıkarma ve analize dahil etme | Yeni bir URL'den alınan tabloda kaynak hücresi/sayfa, birim ve dönem korunur; yanlış veya belirsiz birim otomatik olarak doğru kabul edilmez. |
| Anomali Tool | Olağandışı hareket ve aykırı değerleri yöntemle tespit etme | Çalışan yöntem, kullanılan parametreler, geçerli gözlem sayısı ve işaretlenen dönemler gösterilir; yetersiz veride yöntem çalışmış gibi anlatılmaz. |
| Causality Tool | Görülen ilişkinin neden-sonuç ilişkisi taşıyıp taşımadığını incelemeye yardımcı olma | Betimleyici korelasyon, gecikmeli ilişki ve nedensel iddia ayrılır; verinin desteklemediği nedensellik açıkça belirtilir. |
| Change Detection Tool | Trend, seviye veya davranış değişimleri ve kırılma noktaları | Kalıcı seviye değişimi ile tek bir aykırı sıçrama ayrılır; kullanılan seri ve tarih eşlemesiyle kırılma sonucu yeniden üretilebilir. |

Bu araçlar için belirli bir istatistik algoritması, kütüphane, gecikme sayısı, anlamlılık eşiği veya grafik tipi belirtilmiyor. Causality Tool'un yazılı tanımı “incelemeye yardımcı olur” şeklindedir; konuşmada causality ve correlation birlikte anılır. Dolayısıyla her gözlemsel soruda kesin nedensel etki kanıtlama zorunluluğu çıkarılamaz. Buna karşılık, hesaplanan ilişkinin sınırını doğru açıklamak doğruluk ve kanıt beklentisiyle uyumludur.[^9][^1]

## Demonun ölçtüğü davranış

PDF 11. sayfadaki örnek 2021-2025 arasındaki 60 ayı kullanır. İlk adım konut kredileri ile faiz oranlarını gösterir ve faiz düştüğü halde kredi miktarının artmadığı dönemleri araştırır. İkinci adım aynı tabloyu koruyarak yalnız kredi tutarını enflasyondan arındırmayı ister. Üçüncü adım tabloya konut fiyat endeksi sütunu ekler ve fiyatların açıklayıcı olup olamayacağını sorar. Video 17:13-17:24 bölümünde bu soruların aynı konuşma penceresinde devam edebileceği belirtilir.[^4]

Burada genellenmesi gereken davranış, önceki sonucu bağımsız bir analiz nesnesi olarak koruyup hedeflenen işlemi onun üzerinde uygulamaktır. Önerilen kontrolde ilk sonuçtaki satır anahtarları, sıra, faiz sütunu ve kaynak referansları kaydedilir. Reel dönüşümden sonra yalnız ilgili kredi sütunu veya onun açıkça adlandırılmış türevi değişir; KFE eklendiğinde önceki sütunların değerleri aynı kalır. Belirsiz bir istek varsa agent anlamlı bir ayrım sorabilir; zaten belirlenmiş dönem veya tabloyu tekrar istememelidir. Bu sonuç-sürümü yaklaşımı önerilen tasarımdır, organizasyon belirli bir depolama sınıfı adı vermemiştir.[^4]

PDF 12. sayfa metin çıkarımında yalnız başlık gibi görünür, ancak görselde kredi ve konut fiyatlarının 2021-01=100 bazında endekslenmesi, faiz ve ipotekli satış payının yüzde ekseninde gösterilmesi bulunur. Bu, karşılaştırmalı çıktı örneğidir. Görseldeki eğrileri veya uç değerleri sabit cevap anahtarı yapmak için kaynakta bir talimat yoktur. Hesaplanan sayılar seçilen resmi seri, revizyon, birim ve dönüşümle gerekçelendirilmelidir.[^4]

PDF 13. sayfa, demo sırasında prompt içinde yeni URL verileceğini açıkça söyler. Borsa İstanbul kıymetli madenler sayfasındaki altın işlemleri PDF'si ve bir endeks sayfası örnek gösterilir; resim, Excel ve yazı bölümleri de listelenir. Bunlar önceden görülen iki URL'ye özel bir ayrıştırıcı yazma sınırı oluşturmaz. Ayrıca sayfadaki endeks URL'si ile metinde sözü edilen BIST100 adının aynı seri olduğu kendiliğinden kabul edilmemeli; gerçek kaynak kimliği doğrulanmalıdır.[^7]

## Mimari ve model kısıtları

Python ana geliştirme dilidir; özel gereksinim olmadıkça ek dil kullanılmaması istenir, frontend için dil ve framework seçimi serbest bırakılır. Açık kaynak kütüphaneler ve açık ağırlıklı modeller beklenir. PDF'de parantez içinde listelenen pypdf, Pandas, DuckDB, Plotly ve LanceDB örneklerdir; hepsinin aynı projede bulunması şartı değildir.[^10]

Üçüncü taraf LLM API servisleri kullanılamaz. PDF 28. sayfa bunu geliştirme süreci için de açıkça söyler; video 29:39-30:38 aralığı geliştirme ve demo sırasında Kloudeks API'sinin kullanılmasını tekrarlar. Bu kısıtı yalnız finalde model sağlayıcısını değiştirmek yeterliymiş gibi yorumlamak kaynakla uyumlu değildir. Açılış PDF'sinin örnek model isimleri veya videodaki geçici model tahminleri, sonradan gerçekten sağlanan model kimliklerinin yerine geçmez.[^13][^16]

Konuşmada model çevresinde kurulan dünyanın önemine vurgu yapılır ve 29:04-29:09 aralığında harness yapısının güçlü, modele bırakılan işin küçük olması istenir. 11:35-12:18 bölümü de çok büyük veri kümesini doğrudan context içine koyarak analiz beklemeyi eleştirir. Bu yönlendirme, kapsamı dar araç çağrıları, açık veri sözleşmeleri, araçla yapılan hesaplar, hata kontrolleri ve sınırlı bağlam sunumu için güçlü dayanak oluşturur. Belirli bir agent döngüsü, framework, planlama dili veya çok-agent topolojisi zorunlu tutulmaz.[^15][^3]

Fine-tuning beklentisi sorulduğunda yanıt olumsuzdur; genel modellerin etrafında yapılandırma ve modüler kurgu önerilir. Bu açıklama, kaynakların kendiliğinden bir “skill sistemi zorunluluğu” getirdiği anlamına gelmez. Alan bilgisi ve metrik kuralları yapılandırmayla değişirken keşif, doğrulama, yürütme ve sonuç üretimi aynı kalabiliyorsa taşınabilirlik beklentisi somut biçimde sınanabilir.[^17][^3]

Yerel GPU satın alma veya bağımsız büyük model çalıştırma şartı yoktur; Kloudeks erişimi model hizmetini sağlar. Açılışta embedding, LLM ve görsel model erişiminden, soru-cevapta reranker'dan söz edilse de model listesi ve parametreleri o anda kesinleşmiş olarak sunulmaz. Native tool calling, strict JSON Schema, context uzunluğu, sabit gecikme veya sınırsız paralellik garantisi bu kaynaklarda verilmemiştir. Uygulama tasarımında bunlar ayrıca doğrulanacak yeteneklerdir.[^16]

## Değerlendirme hakkında gerçekten bilinenler

Resmi bir sayısal puan dağılımı, kriter ağırlıkları veya kabul barajı bu sunum ve dökümde bulunmuyor. Aşağıdaki tablo açıklanan değerlendirme sinyallerini gösterir; bir jüri puan cetveli değildir.

| Açıklanan sinyal | Dayanak | Projede gösterilebilir karşılığı |
| --- | --- | --- |
| Çalışan demo ve yeni girdiden sonuç | PDF s.26; video 21:37-21:52 ve 23:09-23:17 | Girdi değişince hesap gerçekten yeniden çalışır; önceden hazırlanmış çıktıdan bağımsız kanıt vardır. |
| İyi tasarım | Video 21:04-21:28 | Veri, plan, hesap, doğrulama ve sunum sınırları incelenebilir. |
| Modülerlik ve genel yapı | Video 23:17-23:25; 30:57-31:26 | Alan verisi değişince motor korunur; yeni araç eklemek temel döngüyü yeniden yazmayı gerektirmez. |
| Teknik ekibin kodu incelemesi | Video 23:09-23:25; 30:57-31:09 | Mimari iddialar gerçek modüller, testler ve giriş/çıkış sözleşmeleriyle eşleşir. |
| Yeniden kurulum | Video 24:04-24:17 | Teslim edilen commit, bağımlılıklar ve veri tanımlarıyla başka ortamda çalışır. |
| Kaynak, kanıt, doğruluk | PDF s.7 | Sayısal sonuç, kaynak hücresi/seri/dönem ve hesap adımlarına izlenebilir biçimde bağlanır. |
| Anahtarların korunması | Video 31:27-32:09 | Takıma ait anahtarlar private repo dahil Git geçmişine veya paylaşıma girmez; konuşmada ihlalin negatif değerlendirileceği açıkça söylenir. |
| Takımın kendi çözümü | Video 23:31-24:17 ve 24:56-25:11 | Hazır lisanslı bir lakehouse/analiz hizmeti projenin temel görevini üstlenmez; yardımcı açık kaynak araçlar kullanılabilir. |

Bu sinyallerin hangi oranda puana döküleceği bilinmiyor. “Performans yüzde 30, tasarım yüzde 20” gibi ağırlıklar, ilk üçe kalma olasılığı veya herhangi bir özelliğin garanti puan getireceği iddiası için kaynak bulunmuyor. 33:26-33:40 bölümünde farklı hikayeler ve çözümler beklenmesi yaratıcılık yönlendirmesidir; tek başına yayımlanmış ayrı bir inovasyon puanı değildir.[^11][^14][^17][^18][^19]

## Konut örneğinin ötesinde ayırt edici bir genel agent

Aşağıdaki gösterimler, resmi beklentilerden çıkarılmış önerilerdir. Ortak amaç, platformun yeni soruya uyumunu ve doğru sınırlarda çalışmasını görünür hale getirmektir.

| Önerilen gösterim | Neyi kanıtlar? | Başarı ölçütü |
| --- | --- | --- |
| KOBİ kredi bakiyesi, müşteri sayısı, takip oranı ve TÜFE ile üç adımlı konuşma | Para, sayım ve oran ölçülerini ayırma; konut dışında aynı araçları kullanma | Agent müşteri sayısını paraya dönüştürmez; kurum kapsamını korur; reel dönüşüm yalnız uygun tutarda yapılır. |
| Önceden gösterilmemiş bir sektörel veya bölgesel soru | Katalog keşfi, doğru ölçü seçimi, güvenli birleştirme | Hazır konut paneline bağlı olmadan doğru veri ailesi ve join anahtarları bulunur; satır çoğalması ve çakışan grup toplamları önlenir. |
| Yeni URL'den PDF/Excel ölçüsünü mevcut tabloya ekleme | URL okuyucu ile lakehouse aracının birlikte çalışması | Kaynak birimi, dönem, sayfa/hücre ve analiz sürümü görünürdür; belirsiz eşleme gerekçesiz kullanılmaz. |
| Bir ayı eksik olan akım serisi ve etiketi değişmiş metrik | Kalite kontrolünün hesap davranışına etkisi | Eksik çeyrek toplamı tam toplam gibi sunulmaz; tanım değişimi boyunca otomatik büyüme hesabı engellenir veya açıkça sınırlanır. |
| Aynı seride aykırı gözlem ve kalıcı seviye değişimi | Anomali ve change detection araçlarının ayrı işlevleri | Tek gözlemli sıçrama ile sonraki dönemde kalıcı seviye değişimi aynı çıktı olarak adlandırılmaz. |
| “Faiz değişti, bu yüzden mi sonuç değişti?” sorusu | Korelasyon, zaman sırası, kontrol değişkeni ve nedensellik sınırı | İlişki hesaplanır; açıklayıcı hipotez ile nedensel kanıt ayrı sunulur; kanıt yetersizse sınır belirtilir. |
| Finans yerine küçük ve kimliksiz sağlık işletim verisi | Teknik alan taşınabilirliği | Aynı keşif, dönem hizalama, hesap ve çıktı akışı çalışır; yalnız veri/alan sözleşmesi değişir. Klinik karar vermek gibi yeni bir ürün kapsamı iddia edilmez. |
| Yeni istekte yalnız tek sütunu değiştirme | Konuşma durumu ve sonuç bütünlüğü | Önceki sonuç kimliği ve değişmeyen sütunların değerleri korunur; yeni hesap öncekinin üzerine izlenebilir sürüm olarak gelir. |
| Hatalı araç çağrısı veya geçici sağlayıcı hatası | Güçlü harness ve hata yönetimi | Hata anlamlı biçimde sınıflandırılır; sınırlı düzeltme/yeniden deneme yapılır; yanlış sonuç başarı gibi sunulmaz. |

Alan değişimi gösteriminin ikna edici olması için ikinci alanın küçük olması sorun değildir; asıl koşul onun ayrı bir sabit akışa yönlendirilmemesidir. Aynı planlama ve yürütme yapısında farklı ölçü, birim ve dönemlerle doğru sonuç alınması gerekir. Bunun çok-agent sayısını artırarak veya çok sayıda “skill” adı göstererek kanıtlandığı söylenemez; ölçülen şey davranışın yeniden kullanılabilirliğidir.[^3][^8][^17]

Önerilen teslim değerlendirmesinde her senaryo için beklenen kaynak/ölçü seçimi, izinli işlem, sonuç kontrolü, hata durumu ve kanıt zinciri kaydedilmelidir. Başarılı örneklerin yanında belirsiz birim, eksik dönem, yanlış join, yetersiz istatistik örneklemi ve model servis hatası gibi durumlar bulunmalıdır. Bu, organizasyonun ayrıca ilan ettiği bir test adedi şartı değildir; doğruluk, modülerlik ve harness beklentisini somutlaştıran bir değerlendirme önerisidir.[^1][^15][^17]

## Teslim, takvim ve operasyon

| Tarih veya kural | Açılışta açıklanan durum | Belirsizlik |
| --- | --- | --- |
| 7 Eylül 2026 | Açılış ve brief paylaşımı | Kaynaklar bu tarihteki açıklamaları içerir. |
| 7-20 Eylül 2026 | Online geliştirme ve mentorluk | Son teslimin kesin saati ve zaman dilimi belirtilmiyor. |
| 5 Ekim 2026 | Finale kalan ilk 10 takımın açıklanması | Değerlendirme toplantılarının ara günleri verilmemiş. |
| 17 Ekim 2026 | Final günü sunumları ve ödüller | Takım başına demo süresi ve ayrıntılı final akışı verilmemiş. |
| Private GitHub repo | KKB'nin bildireceği kullanıcılar collaborator olarak eklenir | Kullanıcı adları bu kaynaklarda yok, Slack üzerinden bildirilir. |
| Freeze sonrası | Repo freeze anında klonlanır; demo son teslim edilen commit üzerinden yapılır | Freeze'in kesin anı ayrıca duyurulacak. Sonradan farklı kodla demo yapmanın elenmeye yol açabileceği söyleniyor. |
| Teknik teslim | Mimari çizimler, dokümanlar, repo, veri tabanı tanımları ve kullanım/kurulum bilgileri paylaşılır | Özel dosya şablonu bu belgelerde yok. |
| Model kotası | Başlangıçta limit olacağı ve gerektiğinde Slack üzerinden artırılabileceği belirtilir | Token, istek/dakika ve eşzamanlılık değerleri açıklanmıyor. |

Takvim PDF 16. sayfa ve video 19:05-19:37 ile desteklenir. Repo teslimi PDF 22 ve 24. sayfalarda; freeze ve aynı commit beklentisi 22:41-23:09 ile 25:46-26:07 bölümlerinde açıklanır. Anahtar güvenliği ve limitler 31:27-32:43 aralığındadır. Slack duyurularının takip edilmesi PDF 30. sayfa ve video 34:35-35:03 bölümünde vurgulanır.[^11][^12][^18][^19]

## Kaynaklarda bulunmayan şartlar

Bu kaynaklar belirli bir lakehouse markası/formatı, Spark kümesi, zorunlu bulut deployment'ı, belirli agent framework'ü, çok-agent adedi, skill sistemi, fine-tuning, hazır sağlık ürünü veya bütün veri tabanını model context'ine koyma şartı getirmiyor. Fine-tuning beklenmediği ve yerel demonun kabul edildiği ayrıca açıklanıyor.[^14][^17]

Resmi ağırlıklı puan cetveli, toplam puan, başarı barajı, kabul edilen gecikme sınırı, değerlendirme soru bankasının tamamı, yeni URL'lerin kesin listesi, freeze saati, final sunum süresi ve sabit API kota rakamları da bulunmuyor. Bunlar sonraki resmi duyurularla netleşmedikçe plan içinde varsayım olarak işaretlenmelidir. Bu belirsizlikler, açıkça tanımlanmış altı araç, veri kapsamı, aynı konuşmada analiz sürdürme veya taşınabilirlik gerekliliklerini erteleme gerekçesi oluşturmaz.[^7][^9][^11][^18]

## Kaynaklar

Aşağıdaki sunum ve toplantı dökümü repo dışında tutulan tarihli kaynaklardır; Git klonuna dahil değildir. Dosya adları ve hashler kaynak kaydını korumak için listelenmiştir.

1. **KKB / Coderspace, “KKB Hackathon 2026 Kick Off Sunum_07092026_FİNAL.pdf”, 7 Eylül 2026.** 31 sayfa. Yerel sunum dosyası: `KKB Hackathon 2026 Kick Off Sunum_07092026_FİNAL.pdf`. SHA256: `225375fda210672fc571ee894dcc8f9def09297a00bee556884d85a7ead0f796`. PDF sayfaları 1 tabanlı numaralandırılmıştır. Yerel erişimli paylaşılan kaynak; kamuya açık bir indirme URL'si bu denetimde doğrulanmamıştır.
2. **“Hackathon 2026 Kick Off”, toplantı kaydından otomatik Türkçe konuşma dökümü.** Tam döküm: `kayit-transkript.md` ve zamanlı segmentler: `kayit-transkript.json`. 520 segment, 00:00-36:05.36 konuşma aralığı. JSON SHA256: `11736639bb8016b36fa57c54513488d4472aefe175639bdf17e529fdca7560ec`. İnsan tarafından satır satır düzeltilmiş resmi tutanak değildir; zaman damgaları ilgili konuşma bölümünü bulmak içindir.
3. **Sunumun metin çıkarımı, “sunum-metin.txt”.** Yerel metin: `sunum-metin.txt`. SHA256: `2a902c553a705ef16d67ba5520eea9f4b48504a7d713071489a882ff099bae3f`. 31 sayfanın metni orijinal PDF ile eşleşir. Grafik sayfası için metin çıkarımı tek başına yeterli değildir.

[^1]: Sunum PDF'si, s.7: beş aşamalı akış ve “Kaynak gösterimi - İzlenebilirlik - Doğruluk Kontrolü” güven katmanı.
[^2]: Video dökümü, 06:44-09:11: lakehouse katmanları, işlenebilir veri, esnek motor ve istenen çıktının sunulması.
[^3]: Video dökümü, 11:35-13:22: büyük veriyi context'e koyma sınırı, kaynakların ekiplerce temini, genel platform ve 12:33-12:55 sağlık verisine taşıma açıklaması.
[^4]: Sunum PDF'si, s.11-12; video dökümü, 16:08-17:49: konut örneği, 60 ay, tabloyu koruma, reel dönüşüm, KFE ekleme ve karşılaştırmalı çıktı. PDF s.12 grafik olarak değerlendirilmiştir.
[^5]: Sunum PDF'si, s.8; video dökümü, 09:25-10:31: BDDK kaynakları, Ocak 2021-Haziran 2026 ve kümülatif değerler.
[^6]: Sunum PDF'si, s.9; video dökümü, 10:31-11:12: EVDS kapsamı ve frekans hizalama. “Tüm veriler” ifadesi PDF'de açıkça bulunur.
[^7]: Sunum PDF'si, s.13 ve s.26: demo prompt'unda URL, çeşitli dosya biçimleri ve demo günü verilecek girdiler. PDF'deki Borsa İstanbul URL'leri örnek kaynaklardır.
[^8]: Video dökümü, 13:29-14:05 ve 17:49-18:22: dinamik web/URL araçları, genişletilebilir motor ve sonradan verilen veri.
[^9]: Sunum PDF'si, s.10; video dökümü, 14:05-16:04: altı araç ailesi, SQL/join, anomali, causality/correlation ve change detection; asgari paket açıklaması 15:45-16:04.
[^10]: Sunum PDF'si, s.18 ve s.20; video dökümü, 19:55-20:24: Python, frontend serbestliği ve açık kaynak beklentisi.
[^11]: Sunum PDF'si, s.16; video dökümü, 19:05-19:37: geliştirme, finalist açıklaması ve final günü takvimi.
[^12]: Sunum PDF'si, s.22,24,26; video dökümü, 20:24-21:52 ve 22:41-23:25: private repo, teknik bilgiler, çalışan demo ve kod incelemesi.
[^13]: Sunum PDF'si, s.28; video dökümü, 29:39-30:38: geliştirme ve demo için Kloudeks, üçüncü taraf LLM API kısıtı.
[^14]: Video dökümü, 23:31-25:11: hazır lisanslı çözümle temel görevi dışarıya devretmeme, yeniden kurulum ve 24:46-24:56 yerel demo izni.
[^15]: Video dökümü, 28:38-29:09: model çevresindeki yapının önemi ve güçlü harness yönlendirmesi. “Harness” kelimesi ve bitişik açıklama ASR segmentleri 353-354'te yer alır; 29:04.78-29:09.36.
[^16]: Video dökümü, 24:26-24:45 ve 26:45-28:37: OpenAI uyumlu erişim biçimi, sağlanacak model aileleri ve o anda geçici olan model seçenekleri. Model adları kesin servis sözleşmesi değildir.
[^17]: Video dökümü, 30:46-31:26: fine-tuning beklenmemesi, genel modeller, modüler yapı ve yapılandırma.
[^18]: Video dökümü, 22:55-23:09, 25:46-26:07 ve 31:27-32:43: freeze, aynı commit, anahtar güvenliği ve başlangıç kotaları.
[^19]: Sunum PDF'si, s.15,30; video dökümü, 33:26-33:40 ve 34:35-35:19: farklı çözümler beklentisi, Slack duyuruları ve finalist seçimi.
[^20]: Orijinal PDF'nin tüm sayfaları, metin çıkarımı ve 520 segmentlik tam döküm. Özellikle PDF s.12'nin metin dışı grafik içeriği ve konuşma tanıma kaynaklı ürün adı değişimleri atıf sınırını belirler.
