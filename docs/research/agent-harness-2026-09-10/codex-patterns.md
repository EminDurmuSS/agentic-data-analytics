# Codex'ten yarışma platformuna aktarılabilecek örüntüler

10 Eylül 2026. Bu not ürün entegrasyonu değil, mimari karşılaştırmadır. Önce yerel Codex CLI paketinin README'si incelendi; kurulu paket genel bir analitik yürütücü kaynak kodu sunmadığından özellikler güncel resmi belgelerden doğrulandı. Yerel Codex süreci veya başka bir model API'si çalıştırılmadı.

Codex SDK, Codex'i programatik olarak kontrol eder. Güncel belgeler hem TypeScript hem Python SDK'sı gösterir; Python SDK yerel app-server ile konuşur. Bu belgenin varlığı, yarışmanın MIA Chat Completions servisinin SDK'ya doğrudan bağlanabileceğini kanıtlamaz. SDK'nın dilinden bağımsız olarak sağlayıcı protokolü ve yarışmanın model kısıtı ayrı değerlendirilmelidir.[^1]

## Yarışma görevine somut aktarım

| Resmi belgede gözlenen yapı | KKB platformu için önerilen karşılık | Sınanacak yarışma davranışı |
| --- | --- | --- |
| App-server konuşmayı thread, isteği turn, girdileri ve araç çıktısını item olarak ayırır.[^2] | `conversation_id`, `run_id`, tipli araç olayları; ayrıca veri deposunda `analysis_id` | Aynı penceredeki ikinci soru doğru analize bağlanır. |
| Konuşma başlatma, sürdürme, çatallama ve çalışma olaylarını akıtma arayüzleri vardır.[^2] | Kaydedilmiş analizden revizyon veya alternatif analiz dalı; arayüzde gerçek çalışma durumu | “Sadece krediyi reel yap” isteği önceki faiz sütununu değiştirmez. |
| Konuşma sıkıştırma ayrı bir yaşam döngüsü işlemidir.[^2] | Sohbet metni kısalırken veri snapshot'ı, analiz referansı, dönem ve korunacak sütunlar kalıcı kayıttan tekrar yüklenir | Uzun konuşmada birim ve kaynak sessizce değişmez. |
| Skill önce kısa tanımıyla görünür, gerektiğinde tam talimatları okunur.[^3] | Önce araç ailesi ve metrik kartı, sonra seçilen metriğin tam sözleşmesi ve yöntem notu | 52.696 EVDS serisini modele taşımadan doğru aday bulunur. |
| Skill, talimatlarla birlikte isteğe bağlı betik ve referans dosyaları paketleyebilir.[^3] | İleride alan profili, örnek plan, yöntem ve kabul testlerinin sürümlü paketi | Finans veya sağlık sözlüğü değişir; çekirdekte özel if/else çoğalmaz. |

Tablonun sağındaki tasarımlar mevcut Codex özelliğinin KKB'de ölçülmüş sonucu değildir. Bunlar resmi davranışlardan çıkarılmış uygulama önerileridir. Codex konuşma çatallaması, bizim DuckDB/Parquet verimizin bir işlem içinde geri alınacağını garanti etmez. Kalıcı analizin doğruluğu kendi snapshot ve yayın protokolümüzde korunmalıdır.

## İlk sürümün sınırı

Model, gerekli aracı ve metrikleri seçer; sayısal hesap izinli Python/DuckDB araçlarında yapılır. Her çalıştırmada sözleşme ve kapsam doğrulanır. Sonuç tablosu, kaynak izi ve grafik dosyası çalışma alanında saklanır. Modelin son metni bu sonuçlara referans verir. Yeni konuşma veya bağlam sıkıştırma, bir önceki tablonun sayılarının yeniden üretilmesini gerektirmez.

Genel amaçlı kod ajanının dosya düzenleme, shell, paket kurma ve serbest program çalıştırma yüzeyini aynen açmak bu hedefe gerekli değildir. İzinli plan dili yetersizse önce eksik genel işlemler, özellikle gruplama, sıralama, birim cebiri ve zaman dönüşümleri eklenmelidir. Böylece esneklik, hesaplanabilir ve denetlenebilir işlemlerin kapsamıyla artar.

Skill sistemi ilk teslim için ayrı bir platform projesi yapılmamalı. Şimdiden alan sözlüğü ve yöntem kuralları çekirdekten ayrılırsa ileride skill yüklemek mümkün olur. Kendiliğinden üretilen yeni skill'in aktif hesap kurallarını değiştirmesi ise sözleşme ve değerlendirme kapısından geçmelidir. Salt Markdown talimatı, yanlış bir çeyreklik toplamı engelleyen yürütme kontrolünün yerine geçmez.

## Kaynaklar

Erişim tarihi bütün kaynaklar için 10 Eylül 2026. Belgeler yaşayan dokümandır; kullanılan bir SDK olursa sürümü ayrıca sabitlenmelidir. Eski `developers.openai.com/codex/` bağlantıları inceleme sırasında aşağıdaki resmi `learn.chatgpt.com` sayfalarına yönlendi.

[^1]: OpenAI, [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk). SDK'nın amacı, TypeScript/Python kütüphaneleri ve yerel runtime ilişkisi incelendi; yayımlanma tarihi belirtilmiyor.
[^2]: OpenAI, [Codex App Server](https://learn.chatgpt.com/docs/app-server). Core primitives, lifecycle overview, thread/turn ve compaction API'leri incelendi; yayımlanma tarihi belirtilmiyor. Deneysel uzak taşıma protokolü bu planın bağımlılığı değildir.
[^3]: OpenAI, [Build skills](https://learn.chatgpt.com/docs/build-skills). Aşamalı bağlam yükleme ve skill dosya yapısı incelendi; yayımlanma tarihi belirtilmiyor.
