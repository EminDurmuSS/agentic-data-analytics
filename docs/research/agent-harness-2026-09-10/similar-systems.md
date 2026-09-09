# Benzer analitik agent sistemlerinden yarışmaya aktarılabilecek desenler

Yarışma için yararlı birleşim, açık metrik anlamı, küçük ve denetlenebilir araç çağrıları, konuşma boyunca korunan analiz sonuçları ve görülmemiş sorularla değerlendirmedir. WrenAI semantik bağlamı, Vanna araç ve sunum akışını, DB-GPT farklı veri kaynaklarında planlama/yürütme ayrımını, Spider 2.0 ise gerçek iş akışına benzeyen değerlendirmeyi düşünmek için uygun birincil örneklerdir. Bunlar hazır çözüm seçimi olarak değil, [yarışma gerekliliklerini](competition-audit.md) kendi motorumuzda karşılamaya yardımcı tasarım desenleri olarak ele alınmalıdır.

Değerlendirme 10 Eylül 2026'da erişilen resmi repo ve proje belgelerine dayanır. Projelerin kodu bu çalışma kapsamında klonlanmadı, kurulmadı veya çalıştırılmadı. Aşağıdaki ürün özellikleri belgelenen mimariyi anlatır; bizim verimiz veya Kloudeks üzerinde doğrulanmış başarı sonuçları değildir.

## Yarışma açısından karşılaştırma

| Sistem | Belgelenen mimari | Alınabilecek desen | Yarışma için bizim ayrıca kanıtlamamız gereken |
| --- | --- | --- | --- |
| **WrenAI** | İş anlamını MDL ve proje dosyalarında tutar; ilgili bağlamı getirir; SQL planlama, doğrulama ve yürütmeyi ayırır.[^1] | Soruya yalnız ilgili metrik sözleşmelerini getir; kaynak/grup/birim seçimini yürütme öncesinde görünür kıl. | BDDK grup kodu farkları, stok/akım, kümülatif dönem, eksik veri ve yeni PDF ölçüsünün anlamı bizim sözleşmelerimizle denetlenmeli. |
| **Vanna 2.0** | Kullanıcı bağlamı taşıyan araç yürütme, kalıcı konuşma ve tablo/grafik/özet akışı belgelenir.[^3] | Agent ilerlemesini ve araç sonucunu yapılandırılmış olaylar olarak aktar; konuşma durumunu kalıcı tut. | Konuşma geçmişi saklamak, önceki tablonun değişmeyen hücrelerini korumayı tek başına garanti etmez. Sonuç sürümü ayrıca sınanmalı. |
| **DB-GPT** | Agent içinde profil, bellek, plan, eylem ve kaynak bileşenleri ayrılır; SQL/Python ve farklı veri türleriyle akışlar belgelenir.[^4][^5] | Genel yürütme motoruna farklı veri kaynakları ve analiz araçlarını aynı sözleşmeyle bağla. | Yeni URL'den veri alıp aynı analize katma; hata sonrası kontrollü düzeltme; sağlık gibi ikinci alanda aynı motor. |
| **Spider 2.0** | Karmaşık şemalar ve gerçek uygulamalardan gelen çok adımlı SQL iş akışlarını değerlendiren benchmark.[^6] | Sadece üretilen SQL metnini değil, kaynak seçimi ve çalışan iş akışının son çıktısını değerlendir. | Kendi BDDK/EVDS verimizde, aynı konuşmada revizyon ve yeni dosya sorularını içeren bağımsız kabul seti. |

## WrenAI: anlamı planın önüne koymak

Resmi mimari, model/kolon/ilişkilerin seçimini, değer profillemeyi, belirsizlik kontrolünü, üretim izini ve hata düzeltmeyi ayrı parçalar olarak açıklar. `dry-plan` genişletilmiş SQL'i gösterir; `dry-run` sorguyu doğrular. Kabul edilmiş soru-SQL eşleşmeleri sonraki sorular için bağlam olabilir. Sorgu yolu proje tanımı, planlayıcı, semantik motor ve bağlantı katmanı üzerinden ilerler; DuckDB de bağlantı seçenekleri arasındadır.[^1]

Bizim için somut öneri, mevcut metrik sözleşmelerini agent'ın başlangıç referansı yapmak ve serbest doğal dil sorusunu önce bu sözleşmelere bağlamaktır. Örneğin “müşteri başına kredi” sorusunda müşteri sayısının bankalar arasında tekilleştirilmediği, grup kapsamı ve paydanın anlamı planın parçası olmalıdır. SQL'in çalışması bu tanımların doğru seçildiğini kanıtlamaz. Bu nedenle kendi doğrulayıcımız anlamsal uygunluğu, çalıştırıcı ise hesap ve kaynak izini denetlemelidir.

WrenAI'daki bütün altyapıyı taşımak bu aşamadaki öneri değildir. Resmi lisans haritası mevcut `core`, `sdk`, `skills` ve örnek kod yollarını Apache 2.0, dokümantasyonu CC BY 4.0 olarak listeler; AGPL metninin ilerideki modüller için bulunduğunu ayrıca açıklar. Sadece GitHub lisans etiketinden bütün repo hakkında tek lisans sonucu çıkarılmamalıdır.[^2]

## Vanna: konuşma ve araç sonuçlarını ürüne taşımak

Vanna'nın güncel repo açıklaması, araç çağrılarına kullanıcı bağlamı taşınmasını, yaşam döngüsü kancalarını, gözlemlenebilirliği, iterasyon kontrolünü ve konuşma depolamayı öne çıkarır. Yanıt tablo, grafik ve özet bileşenleri olarak akabilir. Kendi sunucusu ve mevcut kimlik sistemiyle entegrasyon anlatılır; README lisansı MIT olarak belirtir.[^3]

Bizim aktaracağımız desen, konuşma mesajı ile sayısal sonucu farklı varlıklar olarak yönetmektir. “Bu tabloyu bozmadan KFE ekle” isteği önceki `result_id` üzerinde bir revizyon planına dönüşmelidir. Arayüz olayları “veri bulundu”, “hesap doğrulandı”, “tablo oluşturuldu” gibi gerçek araç sonuçlarına dayanmalıdır. Tam veri tablosunu her turda modele yeniden gönderme veya önceki tablonun yalnız metinsel özetini saklama, yarışmanın hücre bütünlüğü beklentisini karşılamaya yetmez.

Hazır sohbet bileşeninin varlığı, bu çok turlu analiz sözleşmesini çözmüş sayılmaz. Bu yarışma için erken kabul testinin merkezi, değişmeyen sütunların korunması ve yeni sonucun kaynaklarıyla birlikte sürümlenmesidir. Kullanıcı/rol özelliklerinin tamamını bu aşamada kopyalamak ise ana veri-analiz davranışının önüne geçirilmemelidir.

## DB-GPT: genel yürütme ve farklı veri kaynakları

DB-GPT'nin agent dokümanı planlama, eylem, bellek ve kaynak erişimini ayrı bileşenlerle ifade eder; farklı işbirliği düzenleri de sunar. Güncel README, veri tabanları yanında CSV/Excel ve belgeleri, SQL ile Python analizini, rapor üretimini ve kontrollü/sandbox yürütmeyi kapsamına alır. README lisansı MIT olarak bildirir. Bu son güvenlik özelliklerinin bizim ortamımızdaki etkisi ayrıca denenmelidir.[^4][^5]

Bizim için yararlı tasarım, eldeki veriyle hesaplayan araç ile sonradan gelen dosyayı tanıyan aracın aynı planlama/yürütme çekirdeğine bağlanmasıdır. Yeni dosya doğrudan tüm sisteme güvenilir veri olarak yayımlanmamalı; önce kaynak, kolon, ölçü, birim, dönem ve anahtar bilgisi çıkarılmalı, ardından mevcut sonuçla birleştirmenin uygunluğu denetlenmelidir.

Çok-agent düzenini aynen almak için yarışma kaynaklarında zorunluluk yoktur. Önce tek koordinatörün keşif, plan, araç yürütme ve doğrulama döngüsü ölçülmelidir. Ayrı uzmanların eklenmesi, ancak aynı soru setinde doğruluk veya süreyi ölçülebilir biçimde iyileştirirse anlamlıdır. Bu, yarışmanın güçlü harness beklentisine yönelik öneridir; DB-GPT'nin tüm özelliklerinin gerekli olduğu iddiası değildir.

## Spider 2.0: değerlendirmeyi gerçek iş akışına yaklaştırmak

Resmi proje sayfası Spider 2.0'ı 632 gerçek uygulama kökenli problemle tanımlar. Büyük şemalar, farklı veritabanı ortamları ve birden çok SQL işlemi içeren görevler bulunur. Bazı ortamlar BigQuery veya Snowflake kullanır; dolayısıyla benchmark'ın bütününü çalıştırmak, yalnız yerel veri tabanımızı açmakla eşdeğer değildir. Bu rapor herhangi bir leaderboard sonucunu bizim modele ait başarı tahmini olarak kullanmaz.[^6]

Aktarılabilecek fikir, bir soruyu sadece iyi görünen SQL ile başarılı saymamaktır. Bizim kabul setimizde doğru seri/kapsam, doğru hesap, sonuç dosyası, değişmeyen hücreler ve kanıt zinciri birlikte kontrol edilmelidir. Görülmemiş yeni sorular, daha önceki örneğin kelimelerini değiştirmekten öteye gitmelidir: farklı ölçü türü, farklı frekans, yeni dış kaynak veya aynı konuşmada hedefli revizyon içermelidir.

## Bir sonraki adım için önerilen kabul paketi

Aşağıdaki paket bu araştırmanın önerisidir, resmi jüri soru listesi değildir. Amaç başka projelerin adlarını çoğaltmak yerine yarışmanın üç ana sınamasını erken çalıştırmaktır: yeni veri, semantik doğruluk ve çok adımlı revizyon.

1. **Dört normal soru:** KOBİ stok büyümesi, bir oranın yüzde puan değişimi, il bazında karşılaştırma ve kümülatiften aylık akım. Her biri için doğru ölçü ve beklenen hesap bağımsız belirlenir.
2. **İki konuşma zinciri:** Birinde yalnız parasal sütunu reel yapma, diğerinde önceki tabloya yeni endeks ekleme. Satır anahtarı, sıra ve değişmeyen hücreler test edilir.
3. **İki yeni veri girdisi:** Bir Excel ve bir PDF/URL kaynağından aynı analize ölçü ekleme. Bilinmeyen birim veya join anahtarı kesinmiş gibi kullanılmaz.
4. **Üç yanlışlık durumu:** Eksik dönemle toplam, müşteriyi tekil kişi sanma ve uyuşmayan banka grubunu birleştirme. Agent'ın yanlış hesabı durdurması da başarılı davranış sayılır.
5. **Bir alan değişimi:** Küçük, kimliksiz sağlık işletim verisiyle aynı keşif, hesap ve çıktı araçlarını kullanma. Ayrı bir sabit demo akışı eklenmez.

Bu paket önce belirli veri sürümünde deterministik araçlarla, ardından aynı girişlerle Kloudeks agent'ı üzerinden çalıştırılmalıdır. Ölçümler görev başarısı, yanlış başarı bildirimi, gereksiz araç çağrısı, tamamlama süresi ve sonuç/kanıt bütünlüğünü kapsamalıdır. İstatistik araçlarının ayrı kabul örnekleri de yarışmanın altı araç şartı kapsamında korunmalıdır.

Bu projelerin çok sayıda model sağlayıcısını desteklemesi, Kloudeks'le doğrudan uyumlarının test edildiği anlamına gelmez. Yarışma çözümünde model ve embedding çağrıları izinli servis üzerinden doğrulanmalıdır. Güçlü dış model, hazır cloud lakehouse veya örnek bir dashboard, aynı veride kendi harness'imizin başarısına kanıt oluşturmaz; ölçülecek olan yukarıdaki gerçek davranışlardır.

## Kaynaklar

[^1]: Canner, [WrenAI resmi mimari belgesi](https://github.com/Canner/WrenAI/blob/main/docs/core/reference/architecture.md), `main`, erişim 10 Eylül 2026. Semantik proje tanımı, planlama/doğrulama/yürütme ayrımı, bellek ve araç ilkelleri.
[^2]: Canner, [WrenAI resmi lisans haritası](https://github.com/Canner/WrenAI/blob/main/LICENSE), `main`, erişim 10 Eylül 2026. Dosya yolu bazındaki lisanslar; yayımlanmış paket manifestlerinin ayrıca belirleyici olabileceği belirtilir.
[^3]: Vanna AI, [Vanna resmi repo ve README](https://github.com/vanna-ai/vanna), `main`, erişim 10 Eylül 2026. Vanna 2.0 mimarisi, araçlar, konuşma depolama, çıktı bileşenleri, entegrasyonlar ve MIT bildirimi.
[^4]: Eosphoros AI, [DB-GPT resmi agent mimarisi](https://github.com/eosphoros-ai/DB-GPT/blob/main/docs/docs/getting-started/concepts/agents.md), `main`, erişim 10 Eylül 2026. Profil, bellek, plan, eylem ve kaynak bileşenleri.
[^5]: Eosphoros AI, [DB-GPT resmi repo ve README](https://github.com/eosphoros-ai/DB-GPT), `main`, erişim 10 Eylül 2026. Veri kaynakları, SQL/Python yürütme, raporlama ve MIT bildirimi.
[^6]: XLANG Lab ve Spider 2.0 yazarları, [Spider 2.0 resmi proje sayfası](https://spider2-sql.github.io/), erişim 10 Eylül 2026. Gerçek iş akışı görevleri, şema ölçeği ve yerel/bulut veritabanı ortamları.
