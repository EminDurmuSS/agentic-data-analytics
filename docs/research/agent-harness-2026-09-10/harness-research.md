# KKB için Qwen agent yürütme katmanı: araştırma ve karar notu

**Araştırma tarihi ve bütün web kaynakları için erişim tarihi: 10 Eylül 2026.** Bu dosya araştırma ve uygulama önerisidir. Uygulama kodu değiştirilmedi, paket kurulmadı ve canlı model çağrısı yapılmadı.

**Öneri: tek karar verici agent, açık durum geçişleri ve mevcut deterministik lakehouse araçları.** İlk ölçüm için küçük bir Python kontrol döngüsü kurulabilir. Duraklama, kullanıcı cevabından devam etme ve süreç çökmesinden toparlanma ürünün ilk sürümünde gerekiyorsa, aynı akışın LangGraph StateGraph ile yürütülmesi daha güçlü adaydır. Qwen-Agent, ancak gerçek Kloudeks servisiyle protokol deneyi avantajını gösterirse model adaptörü veya alternatif yürütücü olarak seçilmelidir. Framework seçimi, modelin doğru metriği bulduğunu veya kurum kapsamını anladığını kanıtlamaz.

### Yarışma demosunda göstereceğimiz davranış

Bu araştırmanın önerdiği ürün hedefi, aynı analiz üzerinde güvenilir biçimde ilerleyen bir çalışma alanıdır. Jüriye gösterilecek örnek akış şu dört kabul koşuluyla sınanabilir; bunlar resmi puanlama ağırlığı iddiası değildir:

| Kullanıcı hamlesi | Harness'in sağlaması gereken sonuç | Kanıt |
| --- | --- | --- |
| “KOBİ kredi stokunun yıllık artışını göster.” | Doğru kurum grubu, stok türü, birim ve takvim gecikmesiyle hesaplama; geçmiş dönem yoksa açık eksiklik. | Doğrulanmış plan, dönem tablosu ve sayısal referans karşılaştırması. |
| “Buna TÜFE ile reel artışı ekle.” | Endeks ve baz dönemi açık yeni analiz sürümü; önceki sütunların korunması. | Ebeveyn analiz kimliği, sütun reçetesi ve iki sürümün karşılaştırması. |
| “Bu rakam nereden geldi?” | Görünen sonuçtan kullanılan kaynak hücrelerine izleme; referans bulunmasıyla dosya doğrulamasını ayırma. | `explain_value` sonucu, kaynak konumu ve doğrulama bayrakları. |
| “Bu kez sağlık verisi yükledim, aynı çalışma biçimini kullan.” | Açık veri sözleşmesini okuyarak metrik seçimi; finans varsayımlarını yeni veri alanına taşımama. | Aynı araç protokolüyle veri keşfi, uygun işlem doğrulaması ve uyumsuz işlemin gerekçeli reddi. |

Önce bu akışın gerçek MIA modeliyle tekrarlanabilirliğini ölçmek gerekir. Web, URL/PDF/görsel alma, istatistik ve görselleştirme gibi eksik yarışma araçları ayrıca tamamlanmalıdır; iyi bir harness bu eksikleri kendiliğinden kapatmaz.

## 1. Yerel temel ve araştırmanın sınırı

Önce [mevcut kullanım rehberi](../../AGENT_READY_LAKEHOUSE.md) okundu. Yerel `data_pipeline/lakehouse/validation.json` şu durumu kaydediyor:

| Yerel gerçek | Agent tasarımına etkisi |
| --- | --- |
| 55.501 metrik sözleşmesi: 2.824 `ready`, 564 `review_required`, 14 `no_numeric`, 52.099 `metadata_only` | Bulunabilirlik ve hesaplanabilirlik ayrı kararlar olmalı. |
| EVDS metadata evreni 52.696 seri; fiziksel gözlem kapsamı 599 seri, 587 seride sayısal değer | Sadece hazır metriklerde arama yapmak, kullanıcının istediği eksik seriyi benzer bir hazır seriyle sessizce değiştirme riski taşır. |
| `discover`, `describe`, `validate_plan`, `execute`, `revise_analysis`, `explain_value` uygulanmış | Modelin ilk araç yüzeyi bu sözleşmeler olmalı. |
| Sonuçlar ve veri snapshot'ları değişmez; çalışma alanında sürüm kontrolü var | Sohbet durumu, `workspace_id`, `snapshot_id` ve `analysis_id` referanslarını taşımalı. |
| `source_references_complete` ile `source_files_verified` ayrılmış | Agent bir kaynak referansını, dosya baytlarının yeniden doğrulanması gibi anlatmamalı. |
| Web arama, otomatik URL/PDF/görsel alma ve yarışmanın bütün istatistik araçları tamamlanmamış | Altı yerel veri aracı, yarışmanın altı asgari araç ailesinin tamamlandığı anlamına gelmez. |

Bu sayılar yerel yayın kaydıdır. Bu araştırma yeniden bütün veri kalite testlerini çalıştırmadı. Rehberdeki 223 test ve 20 gerçek veri/CLI kontrolü modelin doğal dil başarısı değildir.

Kullanıcının paylaştığı MIA rehberindeki kimlikler aynen korunmalı:

- `kkbhackathon2026/Qwen3.8-27B`: metin ve görsel, `/v1/chat/completions`.
- `kkbhackathon2026/Qwen3-Embedding-8B`: `/v1/embeddings`.
- `kkbhackathon2026/Unlimited-OCR`: belge görseli, `/v1/chat/completions`.
- Temel adres: `https://mia.csp.kloudeks.com/v1`.

**Qwen3.8-27B servis adını, kamuya açık Qwen3, Qwen3.5 veya başka bir modelin teknik özellikleriyle eşitlemek için kanıt yok.** Model ağırlığı, tokenizer, bağlam sınırı, düşünme modu, araç ayrıştırıcısı, sunucu sürümü, eşzamanlılık, kota ve yapılandırılmış çıktı desteği bu notta bilinmiyor. Kamuya açık model ailesinin makalesi, bu dağıtımın yetenek testi değildir.

## 2. Qwen ve vLLM belgelerindeki kritik ayrım

Qwen'in resmi function-calling rehberi, kamuya açık Qwen3 için araç kullanım şablonunu ve vLLM tarafında ayrıştırıcıyla sunumu ayrı anlatıyor. Örnekte otomatik araç seçimi ve Hermes ayrıştırıcısı sunucu ayarıdır; sonuçlar `tool_call_id` ile çağrılarına bağlanır. Bozuk araç argümanlarının yine ele alınması gerektiği de belirtilir. Bu, Kloudeks'in aynı ayarlarla açıldığını göstermez. [Qwen function calling, S1](https://qwen.readthedocs.io/en/stable/framework/function_call.html)

Qwen-Agent README'si bazı kamuya açık Qwen/QwQ modellerinde araç çıktısını kendisinin ayrıştırdığı bir yol, bazı Coder örneklerinde ise yerel API ayrıştırıcısı ve `use_raw_api` yolu gösteriyor. Bunlar alternatif protokol yollarıdır. İki katmanın şablonlarını ve ayrıştırıcılarını birlikte açmak varsayılan çözüm olmamalı. README'deki yerel Python executor ayrıca üretim sandbox'ı olarak sunulmuyor; mevcut SQL/kabuk kabul etmeyen araç yüzeyimiz korunmalı. [Qwen-Agent resmi depo, S2](https://github.com/QwenLM/Qwen-Agent)

Güncel vLLM belgelerinde araç şemasına uyum; `tool_choice`, `strict`, seçilen ayrıştırıcı ve sunucu yapılandırmasına bağlı. Adlandırılmış/`required` çağrı ile `auto` çağrının garanti koşulları aynı değil. Yapısal JSON üretmek ayrıca ayrı bir özellik; `response_format` ve yapılandırılmış çıktı seçenekleri belgeleniyor. Bunların dağıtımdaki sürümde mevcut olduğunu veya ağ geçidinin parametreleri ilettiğini bir örnek başarılı yanıtla kanıtlayamayız. [vLLM tool calling, S3](https://docs.vllm.ai/en/stable/features/tool_calling/), [vLLM structured outputs, S4](https://docs.vllm.ai/en/stable/features/structured_outputs/)

Bundan çıkan uygulama kararı:

1. HTTP yanıtını kendi tarafımızda normalize eden bir sağlayıcı adaptörü olsun.
2. Adaptör sonucu tek bir iç karara dönüştürsün: `tool_call`, `final`, `needs_input` veya `provider_error`.
3. Araç adı ve argümanlar her koşulda yerel doğrulayıcıdan geçsin. Sunucunun JSON üretmesi semantik plan doğrulamasının yerine geçmesin.
4. `reasoning_content` veya benzeri alanlar, araç argümanı ya da son cevap diye çalıştırılmasın. Arayüze kısa plan özeti ve araç izi gösterilsin.

Bu dört madde bizim tasarım önerimizdir; mevcut sistemde uygulanmış bir model adaptörü değildir.

### Anahtar geldiğinde Python ile yapılacak yetenek deneyi

Anahtarın bilindiği varsayılmıyor; bu araştırmada anahtar aranmadı ve istek gönderilmedi. İlk deney sentetik, küçük ve değişiklik yapmayan araçlarla yürütülmeli. Python standart kütüphanesiyle JSON HTTP istekleri yeterli; `curl` gerekmiyor.

| Deney | Kaydedilecek kanıt | Karara etkisi |
| --- | --- | --- |
| Basit Türkçe metin isteği | Durum kodu, model alanı, yanıt alanları, gecikme, usage varsa gerçek token bilgisi | Temel erişim ve protokol biçimi |
| Tek zararsız araç, otomatik ve adlandırılmış seçim ayrı ayrı | Araç adı, argüman JSON'u, `tool_call_id`, `finish_reason` | Yerel API araç protokolü kullanılabilir mi? |
| Araç sonucu verildikten sonraki ikinci tur | Sonucu kullanan cevap, yeni/tekrarlı çağrılar | Tek çağrı demosundan gerçek döngüye geçiş |
| Araç gerekmeyen soru | Gereksiz çağrı oranı | Her soruda araç zorlamanın maliyeti |
| İç içe plan şeması, enum, yasak ek alan, null/opsiyonel alan | Parametre kabulü ve bağımsız yerel şema doğrulama sonucu | Hangi yapılandırılmış çıktı yolu gerçekten kullanılabilir? |
| Aynı ölçümün tekrarları ve artan küçük bağlamlar | İlk deneme başarısı, bütün tekrarların başarısı, p50/p95 gecikme | Ölçülmüş bağlam/çıktı bütçesi; ilan edilmemiş kapasiteyi varsaymama |
| Embedding tekli ve küçük batch | Boyut, sıralama, sonlu değerler, normlar, Türkçe benzerlik örnekleri | Vektör indeksinin gerçek şeması; normalize etme tercihi |
| Kontrollü tek belge görseli | OCR metni, yerleşim etiketleri, sayı/birim hücresi doğruluğu | Görselden çıkarılan değerin otomatik yayına yeterli olup olmadığı |

Bir HTTP 200, parametrenin gerçekten uygulandığını kanıtlamaz. Deney sonuçlarına `observed_schema_pass_rate` gibi ölçüm isimleri verilmeli; dağıtım yapılandırması doğrulanmadıysa “sunucu şemayı zorunlu kılıyor” denmemeli. Desteklenmeyen bir alana gelen 400'de yalnız o yetenek kapatılmalı; her hatada bütün parametreleri kaldıran örtük fallback kullanılmamalı.

İç protokol için tercih sırası: doğrulanmış yerel `tool_calls`, yoksa doğrulanmış JSON karar zarfı, o da yoksa sıkı yerel JSON ayrıştırma ve sınırlı düzeltme. Üçüncü yol güvenilir davranmıyorsa sonucu çalıştırmak yerine `blocked` dönülmeli. Qwen-Agent şablon ayrıştırması bu sıradaki ayrı bir deney adayıdır, sessiz bir kurtarma adımı değildir.

## 3. Harness seçenekleri ve önerilen karar

| Seçenek | Bu repoya uyumu | Uygulamanın yine çözmesi gerekenler | Seçim koşulu |
| --- | --- | --- | --- |
| Küçük Python durum makinesi | Mevcut altı aracı ve hata kodlarını doğrudan kullanır; protokolü ve maliyeti görmek kolaydır. | Kalıcı run kaydı, çökme aralığı, tekrar çağrı önleme, iptal, bütçe ve bekleyen kullanıcı cevabı | İlk gerçek Qwen başarısını ölçmek için referans uygulama; kısa ve sınırlı akışta kalabiliyorsa ana uygulama olabilir. |
| LangGraph StateGraph, tek agent | Açık düğümler, dallanma ve konuşma/süreç devamlılığı için uygun adaydır. Veri Parquet'lerini graph state'e taşımak gerekmez. | Model adaptörü, plan semantiği, idempotency, iş kuralı ve kaynak doğruluğu | İlk üründe OCR/URL bekleme, kesintiden devam etme ve görünür durum akışı gerekiyorsa tercih edilir. Aynı görev setinde küçük döngüyle karşılaştırılmalı. |
| Qwen-Agent | Kamuya açık Qwen araç şablonları ve model arayüzüne daha yakındır. Mevcut veri servisleri özel tool olarak bağlanabilir. | Yarışma alias'ı uyumu, kalıcı iş durumu, immutable analiz sözleşmesi, hata politikası | Gerçek servis deneyi araç ayrıştırmasında veya maliyette ölçülebilir avantaj gösterirse. Dahili code interpreter otomatik açılmamalı. |
| Birden çok otonom agent | Bağımsız belge araştırmalarını ayırabilir. | Paylaşılan analiz sürümü, kaynak uzlaştırma, çelişki, toplam token ve çağrı bütçesi | Ancak bağımsız araştırma alt görevlerinde aynı bütçeli deney belirgin yarar gösterirse. Hesap tablosunun varsayılan yürütücüsü yapılmamalı. |

LangGraph resmi belgeleri, konuşma/checkpoint durumu ile genel store'u ayırıyor. Bellekteki saver süreç yeniden başladığında kalıcılık sağlamıyor. Bu projede lakehouse store veri ve sonuçların otoritesi olarak kalmalı; harness checkpoint'i yalnız işlem durumu ve referansları taşımalı. [LangGraph persistence, S5](https://docs.langchain.com/oss/python/langgraph/persistence)

Checkpoint kullanmak dış yan etkiler için kendiliğinden “tam bir kez” çalıştırma garantisi sağlamaz. LangGraph Functional API belgeleri de tamamlanmadan kesilen task'ın yeniden çalışabileceğini, idempotency veya mevcut sonuç kontrolü gerektiğini anlatıyor. `execute` sonrası sonuç kaydolup harness checkpoint'i yazılamadan süreç ölürse, yeniden başlatma ikinci bir analiz üretmemeli. [LangGraph Functional API, S6](https://docs.langchain.com/oss/python/langgraph/functional-api)

**Karar kapısı:** önce sağlayıcı yetenek profili ve aynı 40 senaryoda küçük döngü referansı. Ardından kesinti/yeniden başlatma deneyi. LangGraph ile bu gereksinim daha az uygulama mantığı ve eşit/doğru sonuçla karşılanıyorsa LangGraph seçilir. Framework değiştirmek metrik seçimi başarısızlığının tedavisi değildir. Bu karşılaştırma henüz yapılmadı.

## 4. Tek agent'ın somut akışı

Aşağıdaki durumlar öneridir; modelin serbestçe atlayabileceği talimatlar değildir:

```text
RECEIVED
  -> LOAD_REFERENCES
  -> RETRIEVE_CANDIDATES
  -> PLAN_OR_CLARIFY
  -> VALIDATE
  -> EXECUTE_OR_REVISE
  -> VERIFY_RESULT
  -> RENDER
  -> COMPLETED

VALIDATE -> REPAIR -> VALIDATE
PLAN_OR_CLARIFY -> WAITING_FOR_INPUT
Her aşama -> BLOCKED | FAILED | CANCELLED
```

Agent yeni tablo, önceki analizin revizyonu, açıklama ve yeni kaynak ihtiyacını ayırt eder. Model veri tabanı bağlantısını, dosya yolunu veya shell'i almaz. `validate_plan` başarılı olmadan `execute` ve `revise_analysis` erişimi açılmaz. Tool şemalarının tamamı her aşamada görünmek zorunda değildir: keşifte keşif araçları, planlamada sınırlı aday kartları, sonuçta ilgili analiz ve kanıt referansları yeterlidir.

Önerilen kalıcı durum:

- `run_id`, `conversation_id`, `workspace_id`, `snapshot_id`, son `analysis_id` ve çalışma alanı sürümü.
- Kullanıcının isteği, kabul edilmiş varsayımlar, korunacak sütunlar ve hedef dönem.
- Seçilmiş metrik kimlikleri ve sözleşme sürümleri; belge parçaları için kaynak/sayfa referansları.
- Yapısal plan, araç sonuç referansları, son hata kodu, düzeltme sayacı, süre/çağrı/token bütçesi.
- Sağlayıcı yetenek profili, prompt/araç şeması sürümü, olay kaydı ve terminal durum.

Tam Parquet, bütün metadata, ham PDF ve modelin uzun iç muhakemesi kalıcı sohbet mesajı olarak taşınmamalı. Model yeniden çağrılırken yetkili durum bu kayıt ve immutable manifestlerden üretilmeli. Serbest sohbet özeti birim, kaynak veya analiz kimliğinin otoritesi olmamalı.

`run_id + step_id + plan_hash + input_workspace_revision` işlem kimliği olarak tasarlanabilir. Aynı anahtarın sonucu önce kontrol edilmeli. Mevcut `VERSION_CONFLICT` koruması değerlidir, ancak tek başına HTTP cevabı kaybolmuş başarılı bir çağrının sonucunu yeniden bulma protokolü değildir. Bu, harness uygulamasında kapatılacak somut bir boşluktur.

### Hata ve durma politikası

Aşağıdaki sayılar başlangıç önerisidir, ölçülmüş servis sınırı veya yarışma kuralı değildir: bir kullanıcı turunda en fazla 8 model kararı, en fazla 2 plan düzeltmesi, en fazla 3 farklı keşif sorgusu. Zaman ve token limitleri gerçek servis gecikmesiyle ayarlanmalı.

| Sinyal | Harness davranışı |
| --- | --- |
| `METADATA_ONLY`, `NO_NUMERIC_VALUES`, `MISSING_OBSERVATIONS` | Veri edinme ihtiyacını veya dönem yetersizliğini açıkça bildir. Benzer bir hazır seriyi sessizce ikame etme. |
| `UNIT_MISMATCH`, `INVALID_TEMPORAL_AGGREGATION`, `NUMERIC_PRECISION_UNSUPPORTED` | Hata kodu ve ilgili sözleşmeyle sınırlı plan düzeltmesi. Sayıyı metin içinde yeniden hesaplayarak engeli aşma. |
| `SCOPE_MISMATCH` | Uygun kapsamı bul; kullanıcı açık bir karşılaştırma istemişse gerekçeli karşılaştırma yolu değerlendir. Sırf kodu geçmek için `scope_reason` üretme. |
| `SEMANTICS_REVIEW_REQUIRED`, `DEFINITION_BREAK`, `AMBIGUOUS_GRAIN` | Uyarıyı kalite eşiği olarak koru; geçerli dar dönem/tek seri varsa onu öner. |
| `VERSION_CONFLICT` | Güncel durumu oku, kullanıcının hedeflediği parent analizini kontrol et. Kör yeniden deneme veya otomatik olarak başka tabloyu değiştirme yok. |
| 401/403 | Erişim engeli olarak bitir; aynı anahtarla döngüye girme. |
| 429/ulaşım kesintisi/5xx | Küçük ve bütçeli gecikmeli deneme; sunucunun bekleme bilgisini dikkate al. Sonucu kaydedilmiş yazma çağrısını tekrar etmeden işlem kimliğini çöz. |
| Aynı araç + aynı argüman + aynı sonuç tekrar ediyor | İlerleme yok durumuyla dur. |
| Zorunlu çıktı ve referansları hazır | Model daha fazla araştırma istemese de yürütücü işi tamamlayabilir. |
| Bütçe bitti | Kayıtlı kısmi sonucu ve tamamlanamayan parçayı açıkça bildir. |

Veri revizyonu için ayrı agent'lar başlatmak yerine tek yazıcı akışı kullanılmalı. Bağımsız belge indirme/okuma işlemleri normal Python görevleri olarak paralel çalışabilir; her paralellik yeni bir LLM agent gerektirmez.

## 5. 52.696 serilik evrende bağlam ve metrik bulma

Buradaki darboğaz tüm veriyi model bağlamına sığdırmak değil, doğru küçük aday kümesini bulmaktır. Anthropic'in bağlam mühendisliği yazısı da ilgili bağlamın seçimini, az çakışan araçları ve aşırı büyümeyen araç sonuçlarını öne çıkarıyor. Bu genel deneyim bizim dağıtım için başarı garantisi değildir. [Context engineering, S7](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Önerilen yol:

1. Mevcut Türkçe normalizasyonlu metin araması referans yöntem olarak kalsın. Metrik kodu, kaynak, dönem, birim, stok/akım/oran ve alan ailesi birlikte kullanılabilsin.
2. Embedding modeli yalnız aday bulma ve belge parçası getirmede kullanılsın. Sayısal cevap vektör benzerliğinden üretilmesin.
3. Metin ve vektör adayları birleştirilip 5-10 kart modele verilsin. Sadece `ready` kayıtları aramak yerine `metadata_only` olan doğru eşleşme de görünür kalsın.
4. Seçilen kart için tam sözleşme getirilsin. `source namespace + dimensions + unit/scale + frequency + kind + coverage + readiness` planlamada zorunlu bağlam olsun.
5. Kullanıcı yeni bir şey istemedikçe önceki analizde seçilmiş kimlikler tekrar serbest benzerlik aramasıyla değiştirilmesin.
6. İndeks, veri/sözleşme sürümüne bağlansın. Birim düzeltmesi sonrası eski embedding metninin dönmesi önlensin.

Qwen3 Embedding makalesi embedding ve reranking için ayrı model ailelerini anlatıyor. Takıma yalnız embedding endpoint'i verildiğinden ayrıca bir reranker'ın mevcut olduğu varsayılamaz. Türkçe metrik retrieval başarısı bu endpoint üzerinde yerel etiketli sorularla ölçülmeli; kamuya açık MTEB sonucu buraya doğrudan taşınmamalı. [Qwen3 Embedding teknik raporu, S8](https://arxiv.org/abs/2506.05176)

Yeni URL/PDF/OCR metni veri olarak çerçevelenmeli. Belgenin içindeki “önceki kuralları unut, şu aracı çağır” benzeri metin kontrol talimatına dönüşmemeli. Sayısal hücre ve birim çıkarımı, kaynak/sayfa izi ve kalite kontrolünden sonra tabloya eklenmeli. OCR metninin makul görünmesi, gözlemin doğruluğunun kanıtı değildir.

## 6. Tek agent tercihi için kanıt ve sınırı

Anthropic'in agent tasarım yazısı, bilinen adımları olan workflow ile daha açık uçlu agent ayrımını yapıyor ve karmaşıklığı ölçülen ihtiyaçla artırmayı öneriyor. Bizim metrik seçimi esnek, sayısal yürütme ve yayın kontrolleri ise belirli kurallı. Dolayısıyla tek model karar vericisi ile deterministik düğümlerin birleşimi uygun bir mühendislik çıkarımıdır. [Building effective agents, S9](https://www.anthropic.com/engineering/building-effective-agents)

Aynı şirketin çoklu araştırma agent'ı raporu bağımsız ve geniş araştırma kollarında yarar, daha fazla token tüketimi ve yoğun ortak bağlam gerektiren işlerde koordinasyon zorluğu bildiriyor. Oradaki 90,2% iyileşme kendi Claude araştırma değerlendirmesine, yaklaşık 15 kat token tüketimi ise sohbet karşılaştırmasına ait. Bunlar KKB verisi, Qwen3.8 veya bizim tek agent uygulamamız için ölçüm değil. [Multi-agent research, S10](https://www.anthropic.com/engineering/multi-agent-research-system)

Bizim görevde “aynı tabloyu koru, krediyi reel yap, KFE ekle” adımları aynı analiz durumuna bağımlı. Ayrı agent'ların birim/kapsam kararlarını uzlaştırmak ilave hata yüzeyi oluşturur. Çoklu agent deneyi ancak birbirinden bağımsız belge araştırması gibi bir alt kümede, **eşit model çağrısı/token bütçesi ve aynı doğrulayıcıyla** yapılmalı. Daha fazla deneme hakkını çoklu agent mimarisinin başarısı diye raporlamamalıyız.

## 7. İlgili benchmark'lar: neyi öğreniriz, neyi öğrenmeyiz?

| Birincil kaynak ve tarih | Bu yarışmaya taşınabilecek değerlendirme fikri | Taşınamayacak iddia |
| --- | --- | --- |
| [Spider 2.0, ICLR 2025; ilk çalışma 2024, S11](https://spider2-sql.github.io/) | 632 kurumsal workflow; büyük şema, metadata ve birden fazla sorgu adımı. Metrik retrieval ile sonucun doğruluğunu ayrı ölçmek. | Büyük SQL benchmark skoru, bizim izinli plan dilimizin başarısı değildir. Serbest SQL'i açmak için gerekçe oluşturmaz. |
| [DABstep, 30 Haziran 2025 makalesi, S12](https://arxiv.org/abs/2506.23719) | 450'den fazla finansal analiz görevi; yapılandırılmış veriyle iş kuralı belgelerini birleştirme ve kesin cevap kontrolü. BDDK dipnotu + gözlem + hesap için yakın örnek. | Makaledeki eski baseline oranı güncel leaderboard ya da Kloudeks sonucu olarak sunulamaz. |
| [BIRD-INTERACT, 6 Ekim 2025 makalesi; ICLR 2026, S13](https://arxiv.org/abs/2510.05318) | Açıklama isteme, ortamı araştırma ve hata sonrası toparlanmayı etkileşimli test etmek. | Benchmark'ın CRUD yetkilerini finans analiz agent'ına vermek gerekmez. |
| [DSBench, 12 Eylül 2024 ilk sürüm, S14](https://arxiv.org/abs/2409.07703) | Veri analizi ve modelleme görevlerini yalnız çalışan kodla değil analitik sonuçla değerlendirmek. | Diğer veri kümelerindeki geçmiş agent sıralamasını bu model alias'ına başarı tahmini olarak aktarmak. |
| [DSAgentBench, 11 Ağustos 2026 ön baskısı, S15](https://arxiv.org/abs/2608.10366) | 275 görevde veri hazırlama, analiz, görsel çıktı ve model sonucu dahil bütün iş akışını değerlendirme fikri. Bu notta özet ve kaynak metadata'sı incelendi. | Gerçek işletim sistemi arayüzlü benchmark sonucunu, bizim kontrollü araç arayüzüne doğrudan kıyaslamak; ön baskıyı bağımsız tekrarlanmış sonuç saymak. |

Benchmark'lardan çıkardığımız sonuç bir model sıralaması değil: görev başarısı, doğru çıktı, doğru süreç ve etkileşim devamlılığı birlikte ölçülmeli. Bu kaynakların hiçbiri `kkbhackathon2026/Qwen3.8-27B` dağıtımını doğrulamıyor.

## 8. Yerel değerlendirme ve kabul tasarısı

İlk set için **40 geliştirme senaryosu ve 20 saklı varyant** öneriyorum. Sayılar öneridir; hazırlanmış veya çalıştırılmış yeni eval paketi değildir. Önceki 273 soru kaydı doğrudan 273 başarılı model testi diye kullanılamaz. Her senaryonun uygulanmış referans sonucu veya gerekçeli engellenme sonucu bulunmalı.

| Test ailesi | Örnek | Deterministik kabul |
| --- | --- | --- |
| Metrik seçimi | KOBİ kredi bakiyesi, müşteri sayısı, takip oranı | Beklenen metrik kimliği/kapsamı, doğru stok/sayım/oran ayrımı |
| Zaman ve birim | Yıllık büyüme, yüzde puan farkı, haftalık faiz ortalaması | Takvim lag'i, doğru birim, görünür aggregation ve eksik dönem uyarısı |
| Çok turlu revizyon | Konut tablosu, yalnız krediyi reel yap, KFE ekle | Anahtarların ve hedef dışı hücrelerin aynen korunması |
| Kapsam çatışması | Farklı kaynaklardaki aynı banka grup kodu | Sessiz nüfus eşitlemesi olmaması; gerekirse açık karşılaştırma |
| Veri yokluğu | Metadata var, gözlem yok; sayısal değer yok | Doğru hata sınıfı; uydurma veya benzer seri ikamesi olmaması |
| Kaynak izi | “Bu hücre hangi kaynakta?” | Sonuç kimliği, formül ve kaynak referansı; doğrulanmamış dosyanın doğrulanmış denmemesi |
| Yeni veri/alan | Sentetik klinik CSV, ardından tablo revizyonu | Aynı araçlarla çalışma; alan sözleşmesi ve sürüm korunması |
| Yeni belge | Birim/dipnot içeren görülmemiş PDF veya görsel | Kaynak/sayfa bağı ve sayı-birim kontrolü; metindeki talimatın yürütülmemesi |
| Hata toparlanması | Şema hatası, 429, cevap kaybı, eski workspace | Sınırlı yeniden deneme, ikinci yazmanın oluşmaması, açık terminal durum |
| İstatistiksel iddia | Korelasyondan nedensellik çıkarma isteği | Desteklenmeyen nedensel iddianın üretilmemesi; yöntem ve veri sınırının belirtilmesi |

Ölçümler: aday retrieval recall@k, ilk geçerli plan oranı, ilk denemede uçtan uca başarı, bütün üç tekrarı geçen görev oranı, doğru engellenme, gereksiz açıklama isteme, gereksiz araç kullanımı, kaynak doğruluğu, p50/p95 gecikme ve gerçek usage varsa token tüketimi. Token bilgisi servisçe verilmezse karakter sayısı ayrı adla raporlanmalı.

Kod tabanlı doğrulayıcılar sayı, tablo, anahtar ve sürümü denetlesin. Dil modeliyle puanlama ancak açıklık ve gerekçe kalitesi gibi kalan boyutlarda, insan örnekleriyle kalibre edilerek kullanılmalı. Aynı Qwen'in kendi cevabını onaylaması sayısal doğruluk kanıtı değildir. Tek başarılı deneme ile tekrarlı güvenilirlik ayrımı agent değerlendirmesi literatüründe de özellikle vurgulanıyor. [Anthropic eval rehberi, 9 Ocak 2026, S16](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

İlk karşılaştırma matrisi küçük tutulmalı: metin retrieval / hibrit retrieval; doğrudan JSON karar / doğrulanmış native tool calling; küçük durum makinesi / gerekirse LangGraph; düzeltme yok / en fazla iki düzeltme. Her seferinde bir değişken değiştirilmeli. Sağlayıcı desteklemiyorsa düşünme modu veya eşzamanlı çağrı varyantı uydurulmamalı.

## 9. Önerilen uygulama sırası

1. **Sağlayıcı deneyi:** Anahtar mevcut olduğunda Python ile küçük yetenek profili çıkar. Bir framework kurarak servis eksiklerini kapatabildiğimizi varsayma.
2. **Referans görevler:** KOBİ, konut revizyonu, müşteri sayısı ve kapsam/eksik veri engelleri için gold kimlik/sonuçları tanımla.
3. **Model adaptörü ve tek döngü:** Aday getir, yapısal karar al, yerel doğrula, aracı çalıştır, kaynaklı sonuç üret. En basit uçtan uca yolun başarısını ölç.
4. **Kalıcı run ve kesinti deneyi:** Başarılı araç çağrısı ile checkpoint arasındaki çökme dahil tekrar çalıştırmayı doğrula. Bu aşamada küçük döngü veya LangGraph kararı ver.
5. **Bağlam retrieval:** Yalnız ölçülmüş retrieval hatalarını hedefleyerek embedding/hibridi ekle. Doğru ama henüz indirilmeyen metrikleri görünür tut.
6. **Yarışma araçlarını tamamla:** URL/PDF/görsel okuyucu, web arama ve istatistik araçları aynı plan/kanıt/sonuç sözleşmesine bağlansın. Yeni kaynaklar doğrudan aktif snapshot'ı değiştirmesin.
7. **Saklı senaryolar ve demo:** Konut dışındaki ailelerle, görülmemiş girdiyle ve üç tekrarlı oturumlarla sonuçları ölç. Model sürümü/alias, paket sürümleri, prompt ve sözleşme hash'lerini demo manifestine koy.

## 10. Claude Code Agent SDK'dan ne alabiliriz?

Kullanıcının sonraki yönlendirmesi üzerine resmi Claude Agent SDK belgeleri ayrıca incelendi. **Buradaki öneri Claude SDK'yı Kloudeks'e bağlanmış hazır bir runtime saymak değil, doğrulanmış harness tasarım fikirlerini kendi MIA yürütücümüze taşımaktır.** SDK kendisini Claude Code'un döngü, araç ve bağlam yönetimini Python/TypeScript'e açan bir kütüphane olarak tanımlar. Quickstart, Anthropic ve belirli Claude bulut sağlayıcılarını belgeliyor. MIA rehberindeki Chat Completions adresinin bu SDK için desteklenen bir model sağlayıcısı olduğunu doğrulayan resmi kanıt bulunmadı. [SDK overview, S17](https://code.claude.com/docs/en/agent-sdk/overview), [SDK quickstart, S18](https://code.claude.com/docs/en/agent-sdk/quickstart)

Bu nedenle `ANTHROPIC_BASE_URL` benzeri bir yönlendirme, tek başına protokol/model uyumluluğu kanıtı değildir. Claude'un modeline, fiyatlarına, tool-search özelliğine veya otomatik compaction davranışına bağımlı özellikler Qwen tarafında yeniden sınanmalı. Yarışmanın gerçek LLM çağrıları MIA modellerinde kalmalı.

### Desenlerin mevcut analitik motora eşlenmesi

| Claude harness'taki desen | Bizim motorda önerilen karşılığı | Kopyalanmaması gereken varsayım |
| --- | --- | --- |
| Araç kullanan döngü ve terminal sonuç mesajı | Yapısal karar -> `validate_plan` -> veri aracı -> kayıtlı sonuç -> cevap; ayrı `completed`, `blocked`, `failed`, `cancelled` durumları | Normal metin gelmesini işin başarıyla bittiği saymak |
| Kalıcı session ve belirli session'ı sürdürme | `conversation_id` ile açık `workspace_id`/`analysis_id` eşlemesi | Çalışma klasöründeki “son oturumu” bütün kullanıcılar için devam ettirmek |
| Compaction öncesi olay ve bağlamın yeniden kurulması | Kanonik analiz referanslarını kaydet, eski konuşmayı özetle, sonraki turu manifest ve kabul edilmiş varsayımlardan kur | Sohbet özetinin birim, kaynak veya korunacak sütunları eksiksiz taşıdığına güvenmek |
| Araç öncesi/sonrası hook | Yetki + şema + bütçe + workspace sürümü kontrolü; sonra sonuç referansı ve hata kodu kaydı | Denetimi yalnız prompt'a veya isteğe bağlı bir callback'e bırakmak |
| Bağlamı ayrılmış alt agent | Yalnız gerekli belge parçaları verilen, kısa kanıt zarfı döndüren araştırma görevi | Alt agent'ın dosya/analiz durumunun da otomatik izole edildiğini sanmak |
| İlerleme dosyası ve test edilebilir hedefler | `run_manifest` içinde beklenen çıktı, tamamlanan adım, eksik veri ve son doğrulama | “Grafik hazırladım” cümlesini gerçekten oluşmuş bir görsel/artifact saymak |
| Toplam maliyet ve tur bütçesi | Model, embedding, OCR ve araç sürelerinin tek run defterinde toplanması | Yalnız ana agent'ın usage alanının bütün işi kapsadığını varsaymak |

Tablonun sağ ve orta sütunları bu repoya yönelik tasarım çıkarımıdır; SDK'nın MIA üzerinde çalıştığına ilişkin deney değildir.

### Compaction, dosya durumu ve devamlılık

Resmi agent-loop belgesi otomatik compaction ve `compact_boundary` olayını anlatıyor; eski konuşma özetlenirken erken talimatların kaybolabileceğini de belirtiyor. Aktaracağımız fikir, **bağlamı kısaltma işlemini görünür bir yaşam döngüsü olayı yapmaktır**. Qwen servisine aynı komutun veya aynı eşiğin taşınabildiği varsayılmıyor. [Agent loop, S19](https://code.claude.com/docs/en/agent-sdk/agent-loop)

Bizim önerilen devir kaydı, “kullanıcı reel kredi istedi” gibi serbest bir özetten daha kesin olmalı: hedef `analysis_id`, parent sürümü, dönem anahtarları, korunacak sütunlar, seçilmiş CPI metriği/baz dönemi, veri boşlukları, kalan çıktı ve kaynak referansları. Sayısal hücreler mevcut Parquet'ten yeniden okunmalı. Compaction sonrası ilk kontrol aynı analiz kimliğinin açılması ve değişmemesi gereken sütunların hash/anahtar kontrolüdür.

SDK session'ı konuşmayı saklar; session fork dosya sistemini dallandırmaz. Dosya checkpoint sistemi de belirli yerleşik düzenleme araçlarının değişikliklerini izler; genel Bash değişikliklerini veya bütün dış sistem yan etkilerini kapsamaz. Dolayısıyla sohbeti geri almak ile lakehouse sonucunu geri almak aynı işlem değildir. Bizde bunu sağlayan katman mevcut immutable snapshot/analiz store'u olmalı. [SDK sessions, S20](https://code.claude.com/docs/en/agent-sdk/sessions), [SDK file checkpointing, S21](https://code.claude.com/docs/en/agent-sdk/file-checkpointing)

### Hook ve permission tasarımından çıkan somut ders

SDK'da `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop` ve `PreCompact` gibi olaylar var. Bizim karşılıkları normal Python fonksiyonları olabilir; Claude SDK bağımlılığı gerektirmez. [SDK hooks, S22](https://code.claude.com/docs/en/agent-sdk/hooks)

Önemli bir ayrıntı: resmi permission belgesinde `allowed_tools`, araçların otomatik onayını tanımlar; listelenmeyen bütün araçların yok olduğu anlamına gelmez. Daha önce onaylanan bir çağrı `canUseTool` callback'ine ulaşmayabilir. Her çağrı için zorunlu kontrol, gerçekten her yürütmenin geçtiği noktada olmalıdır. [SDK permissions, S23](https://code.claude.com/docs/en/agent-sdk/permissions)

Bizim uygulama planında model yalnız kayıtlı veri araçlarını görmeli. Ortak dispatch kapısı, modelin seçtiği `analysis_id` değerinin ilgili konuşmaya ait olduğunu, revizyonun hedef parent'ını, planın doğrulandığını ve bütçeyi kontrol etmeli. Bu kapı birimi bozuk planı durdurduğunda model farklı bir tool adı veya serbest metin hesabıyla kapıyı geçememeli. Kaynak PDF'si bir permission/policy dosyası gibi yüklenmemeli.

### Alt agent izolasyonu ve sonuç devri

Resmi SDK alt agent'ların ayrı konuşma bağlamı ve sınırlı araçlarla çalıştırılabildiğini, ara araç sonuçlarının ana bağlama taşınmayabildiğini açıklıyor. Bu bilgi ve araç ayrımıdır; kendi başına ayrı veri tabanı, ayrı workspace veya işletim sistemi sandbox'ı değildir. [SDK subagents, S24](https://code.claude.com/docs/en/agent-sdk/subagents)

Bizde olası tek erken alt görev, bağımsız bir yeni belgeden gerekli dipnotu veya sayı hücresini bulmaktır. Girdi, belirli kaynak kimliği ve sınırlı soru; çıktı ise kısa özet, kaynak/sayfa/hücre referansı ve belirsizlik olur. Alt göreve `execute` veya `revise_analysis` verilmez. Parent bulguyu kontrol edip aynı merkezi yazıcıdan kullanır. İlk sürümde bunu ayrı agent yerine sıradan bir belge işleme göreviyle yapabilmek de geçerli seçenektir.

### Uzun işlerde ilerleme, yeniden deneme ve “bitti” ölçütü

Anthropic'in 26 Kasım 2025 yazısı, ilk hazırlık, kalıcı ilerleme kaydı ve test edilmiş küçük adımlarla oturumlar arası devamlılığı inceliyor. Bizim karşılığı, analiz talebini beklenen çıktı ve kabul koşullarına bölmek; her yeni turda mevcut manifestten devam etmektir. Kod geliştirme örneğindeki Git commit yöntemi kullanıcı analizini Git'e yazmamız gerektiği anlamına gelmez. [Long-running harness, S25](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

24 Mart 2026 tarihli devam yazısı generator/evaluator ayrımı, açık kabul sözleşmesi ve bağlam devrini tartışıyor. Örneklerinden biri tek çalıştırma ile daha uzun/pahalı tam harness arasında aynı bütçeli olmayan karşılaştırma içeriyor. Bu yüzden oradaki kalite farkını bizim için çoklu agent'ın bağımsız etkisi gibi sunamayız. Bizim sayısal doğrulayıcımız zaten deterministik; ek bir LLM eleştirmeni ancak açıklama kalitesinde ölçülen boşluk varsa denenmeli. [Harness design, S26](https://www.anthropic.com/engineering/harness-design-long-running-apps)

Önerilen tamamlanma sözleşmesi: istenen tablo/şema ve varsa görsel artifact oluştu; ilgili kaynak ve uyarılar bağlandı; korunacak hücreler korunmuş; açıklamanın sayıları kayıtlı sonuçtan geliyor; açık veri eksikliği saklanmıyor. Araçtan yanıt beklenirken süreç kesilmişse önce işlem kimliğiyle kayıtlı sonuç aranır. Harcanmış çağrıları sıfırlayıp aynı işi yeniden başlatmak toparlanma sayılmaz.

### Gecikme ve maliyet bütçesine aktarım

SDK maliyet rehberi ana döngü usage'ı ile alt görevleri de içeren toplamları ayırıyor; crash sonrası bazı final alanlarının eksik veya sıfırlanmış olabileceğini belirtiyor. Bizim dersimiz, yalnız son mesajı okumak yerine istek kimliğiyle olayları kaydetmek ve çift sayımı önlemektir. Claude fiyat tablosu MIA maliyet hesabında kullanılamaz. [SDK cost tracking, S27](https://code.claude.com/docs/en/agent-sdk/cost-tracking)

Başlangıç ürün hedefleri, **ölçülmemiş öneriler**:

- Bilinen veriden standart soru için 2-4 model isteği; sıradan keşif/sözleşme kontrolü gerektiğinde aynı aşamada gruplanabilir.
- Bir tur için önceki bölümdeki 8 model kararı bütçesi ortak kalsın. Düzeltme, özetleme ve varsa alt agent bu bütçeyi yeniden başlatmasın.
- Embedding isteği ve OCR sayfası ayrıca sayılsın; bütün MIA istekleri toplam süre/kota defterine girsin. OCR batch veya paralellik yalnız sağlayıcı deneyinden sonra artırılsın.
- Bilinen veri, yeni belge ve eksik veri edinme yollarının p50/p95 gecikmesi ayrı ölçülsün. Uzun OCR işini basit metrik sorgusunun gecikmesine karıştırmayalım.
- Token birim fiyatı veya kota resmi olarak bilinmiyorsa para cinsinden maliyet uydurulmasın. İstek, gözlenen token, sayfa ve süre ölçümleri gösterilsin.

Asıl alınacak fikir, modelin çevresinde görünür ve tekrar başlatılabilir bir çalışma ortamıdır: sabit araç sözleşmeleri, analiz referansları, küçük bağlam paketleri, olay kaydı, sınırlar ve sonuç testleri. Claude SDK'nın tüm araçlarını veya kendi model runtime'ını taşımak gerekmiyor.

## Kaynak kaydı

Tüm web kaynaklarına erişim: **2026-09-10**. Dokümantasyon için bir sürüm/yayın tarihi görünmüyorsa tarih uydurulmadı. `stable` ve `main` URL'leri değişebilir; uygulama seçildiğinde kullanılan paket sürümü ve kaynak commit'i ayrıca sabitlenmeli.

| ID | Birincil kaynak | Kaynak tarihi / incelenen kapsam |
| --- | --- | --- |
| S1 | [Qwen Function Calling](https://qwen.readthedocs.io/en/stable/framework/function_call.html) | Güncel `stable` doküman; tek sayfa yayın tarihi belirtilmiyor. Qwen3 örneği ve native araç protokolü incelendi. |
| S2 | [QwenLM/Qwen-Agent](https://github.com/QwenLM/Qwen-Agent) | Güncel `main` README; 2026-02-16 haber kaydı dahil. Model yolları, araç ayrıştırma ve executor sınırı incelendi. |
| S3 | [vLLM Tool Calling](https://docs.vllm.ai/en/stable/features/tool_calling/) | Güncel `stable` doküman; bu araştırma kurulu Kloudeks sürümünü bilmiyor. Strict/auto/named ayrımı incelendi. |
| S4 | [vLLM Structured Outputs](https://docs.vllm.ai/en/stable/features/structured_outputs/) | Güncel `stable` doküman; yayın tarihi belirtilmiyor. JSON ve reasoning etkileşimi incelendi. |
| S5 | [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Güncel doküman; tek yayın tarihi belirtilmiyor. Kalıcılık türleri ve bellek saver sınırı incelendi. |
| S6 | [LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api) | Güncel doküman; tek yayın tarihi belirtilmiyor. Replay, side effect ve idempotency bölümleri incelendi. |
| S7 | [Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | 2025-09-29; bağlam ve araç tasarımı üzerine şirket mühendislik deneyimi. |
| S8 | [Qwen3 Embedding teknik raporu](https://arxiv.org/abs/2506.05176) | İlk sürüm 2025-06-05, v3 2025-06-11; özet, görevler ve model ailesi ayrımı incelendi. |
| S9 | [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) | 2024-12-19; güncel sayfadaki workflow/agent ve basitlik ilkeleri. |
| S10 | [Multi-agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system) | 2025-06-13; şirket içi araştırma sistemi sonuçları ve sınırları. |
| S11 | [Spider 2.0 proje sayfası](https://spider2-sql.github.io/) | İlk makale 2024, ICLR 2025; problem tanımı incelendi, güncel model sıralaması alınmadı. |
| S12 | [DABstep makalesi](https://arxiv.org/abs/2506.23719) | 2025-06-30. Ayrıca [benchmark kurucularının 2025-02-04 yazısı](https://huggingface.co/blog/dabstep) ile görev/veri yapısı karşılaştırıldı. |
| S13 | [BIRD-INTERACT makalesi](https://arxiv.org/abs/2510.05318) ve [resmi depo](https://github.com/bird-bench/BIRD-Interact) | İlk sürüm 2025-10-06; depo ICLR 2026 kabulünü 2026-02-08 tarihinde duyuruyor. |
| S14 | [DSBench makalesi](https://arxiv.org/abs/2409.07703) | İlk sürüm 2024-09-12, son revizyon 2025-04-11; özet ve görev kapsamı incelendi. |
| S15 | [DSAgentBench ön baskısı](https://arxiv.org/abs/2608.10366) | 2026-08-11 v1; yalnız özet ve metadata kapsamı. |
| S16 | [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 2026-01-09; grader seçimi, tekrar güvenilirliği ve erken değerlendirme tasarımı incelendi. |
| S17 | [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) | Güncel resmi doküman, ayrı yayın tarihi belirtilmiyor. |
| S18 | [Claude Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart) | Güncel resmi doküman; belgelenen kimlik doğrulama/sağlayıcı yolları. |
| S19 | [How the agent loop works](https://code.claude.com/docs/en/agent-sdk/agent-loop) | Güncel resmi doküman; compaction ve terminal durumlar. |
| S20 | [SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions) | Güncel resmi doküman; konuşma ve dosya durumu ayrımı. |
| S21 | [SDK file checkpointing](https://code.claude.com/docs/en/agent-sdk/file-checkpointing) | Güncel resmi doküman; izlenen araçlar ve kapsam sınırları. |
| S22 | [SDK hooks](https://code.claude.com/docs/en/agent-sdk/hooks) | Güncel resmi doküman; yaşam döngüsü olayları. |
| S23 | [SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions) | Güncel resmi doküman; onay sırası ve araç yüzeyi ayrımı. |
| S24 | [SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents) | Güncel resmi doküman; bağlam ve araç izolasyonu. |
| S25 | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 2025-11-26. |
| S26 | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) | 2026-03-24. |
| S27 | [SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking) | Güncel resmi doküman; ana döngü/alt görev ve crash muhasebesi. |
| Yerel | [Lakehouse kullanım rehberi](../../AGENT_READY_LAKEHOUSE.md), `data_pipeline/lakehouse/validation.json`, kullanıcı tarafından paylaşılan MIA rehberi | Yerel durum 2026-09-10'da okundu. MIA metninde ayrı yayın tarihi yok. |
