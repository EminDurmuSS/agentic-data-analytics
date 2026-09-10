# Yerel Qwen analiz uygulaması: uygulama ve kullanım notu

> Dosya düzeni notu: çalıştırma komutları ve kod bağlantıları güncel konumlara uyarlandı. Bu rapordaki ölçümler, hashler ve eski dosya/satır atıfları kaydedilen commit'e aittir; yeniden doğrulama yapılmadı. [Taşıma açıklaması](../../README.md#dosya-düzeni-değişikliği).

Tarih: 10 Eylül 2026. Bu not mevcut kodun davranışını ve sınırlarını anlatır.
[Önceki harness araştırmasındaki](harness-research.md) önerilerin tamamının
uygulandığı veya bütün gerçek soruların canlı modelle doğrulandığı iddia
edilmez. [Sağlayıcı yetenek deneyleri](../mia-probe-2026-09-10/README.md)
ayrı sentetik deneylerdir.

## Kurulum ve başlatma

Repo kökünde Python 3.12 kullanın:

```bash
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-app.lock
python data_pipeline/lakehouse/build_lakehouse.py
python -m app --prompt-key --port 8870
```

Tarayıcı adresi [http://127.0.0.1:8870](http://127.0.0.1:8870).
`requirements-app.lock`, Python 3.12 için doğrudan ve dolaylı bütün uygulama
bağımlılıklarının tam sürümlerini sabitler. Kaynak liste
`requirements-app.txt`, veri katmanıyla birlikte FastAPI/Uvicorn, belge,
görüntü, JSON şeması ve istatistik paketlerini içerir. Var olan ortamda yeniden
`uv venv` çalıştırmak gerekmez.
Finans profili kullanılmayacaksa lakehouse build adımı atlanabilir.

`--prompt-key`, `MIA_API_KEY` zaten tanımlı değilse anahtarı terminalden
gizli okur. Anahtar sağlayıcı istemcisinin belleğinde kalır. Tarayıcıda
anahtar girişi ve `.env` dosyasını kendiliğinden yükleme davranışı yoktur.
Anahtarsız başlatmada çalışma alanı ve mevcut yerel kayıtlar açılabilir;
canlı agent ve görsel okuma için sağlayıcı ayarı gerekir.

| Ayar | Davranış |
| --- | --- |
| `--port 8870` | Varsayılan yerel HTTP portu |
| `--host` | Yalnız `127.0.0.1`, `localhost`, `::1` |
| `--db /dosya/analytics.duckdb` | Finans profilinin kaynak veritabanı |
| `--runtime-root /dizin/app` | Snapshot, konuşma, kaynak ve sonuç kayıtlarının kökü |
| `MIA_API_KEY` | Sunucu sürecinin sağlayıcı anahtarı |
| `SEARXNG_URL` | Varsayılan Bing RSS yerine kullanılacak herkese açık SearXNG adresi |

Varsayılan çalışma kökü `.lakehouse-runtime/app/`, kaynak veritabanı
`data_pipeline/lakehouse/analytics.duckdb` dosyasıdır. Kaynak veritabanı
Git'te taşınmaz; build komutu repodaki kaynak çıktılarından üretir.
Finans çalışma alanı ilk açılışta doğrulanmış, değişmez bir snapshot oluşturur.
Çalışma kayıtları yereldir; kullanıcı hesabı, paylaşımlı sunucu kurulumu ve
otomatik eski snapshot temizliği bu uygulamanın özellikleri değildir.

## Kullanıcı akışı

**KKB finans verileri** profili mevcut BDDK, EVDS ve diğer kaynakların metrik
sözleşmelerini kullanır. **Boş çalışma alanı** finans tablolarını gerektirmez;
uygulamanın temel metadata tablosuyla başlayıp açık sözleşmeli zaman
serilerini eklemeye izin verir. Sağlık verisinin eklenmesi kendi başına
klinik analiz kuralları veya bir klinik karar sistemi oluşturmaz.

Konuşmada yeni analiz oluşturulabilir; devamında aktif analize sütun veya
dönüşüm eklenebilir. `revise_analysis`, eski analizi değiştirmeden parent
kimliği taşıyan yeni sonuç üretir ve hedef dışındaki hücreleri korur.
**Yeni konuşma** aynı çalışma alanının verisini silmez. Kritik dönem,
kurum grubu veya ölçü belirsizse agent tek soruyla `needs_input` durumunda
durabilir; cevap aynı konuşmada yeni tur olarak sürdürülür.

Sonuç alanında tablo, grafik, kaynaklar ve hesap planı görünür. Sayısal
hücreden `explain_value` çağrısıyla kaynak izi açılır. CSV indirimi kayıtlı
analizden üretilir. Grafik seçilen sayısal sütunu, o anda yüklenmiş tablo
sayfasından çizer; eksik değerlerin üzerinden çizgi geçirmez. Büyük tablolarda
250 satırlık sayfalama kullanılır, grafik bütün arşivi otomatik yüklemez.

İlk denemeler için örnek sorular:

- “2026 ilk çeyrekte tüm bankaların aylık net kârını göster.”
- “Aynı tabloya Ocak 2026 TÜFE bazında reel kâr ekle.”
- “İstanbul altın mevduatını 2025 ve 2026 çeyreklerinde göster.”
- “Bu kaydedilmiş seride önceki 12 döneme göre olağandışı ayları bul.”
- “Aynı tabloda X'in bir ay önceki değeri ile Y'nin ilişkisini incele.”

Bu örnekler kullanıcı girdileridir, tümünün yeni canlı uygulamada başarıyla
çalıştırıldığına ilişkin bir sonuç kaydı değildir.

## Altı araç ailesi ve somut kapsamı

Bu eşleme yarışmadaki altı aileyi açıklar; altı fonksiyon veya altı ayrı agent
anlamına gelmez.

| Aile | Uygulanan araçlar | Sağlanan davranış ve sınır |
| --- | --- | --- |
| Lakehouse | `discover`, `describe`, `dimension_values`, `validate_plan`, `execute`, `revise_analysis`, `query_grouped`, `explain_value` | Kısa metrik kartı, gerçek boyut kodu/etiketi, kontrollü dönem eşleme ve hesap, sürümlü sonuç, kaynak izi. Serbest SQL veya modelin seçtiği dosya yolu yoktur. Gruplama tek metriği dönem başına sıralar; banka topluluklarını toplayan genel bir sorgu dili değildir. |
| Web arama | `web_search` | Bing RSS veya yapılandırılan SearXNG ile en fazla 10 sonuç. Arama özetleri doğrulanmış kaynak değildir; iddia için URL ayrıca okunmalıdır. Arama hizmeti hata/challenge döndürürse `unavailable` sonucu gelir. |
| URL ve belge | `inspect_source`, `publish_selected_table` | URL veya kayıtlı kaynak kimliğinden metin/tablo adayları; açık sözleşmeyle seçilmiş tablonun yayını. CSV, XLSX, HTML, PDF, UTF-8 metin, PNG ve JPEG desteklenir. Görsel hücreler kullanıcı incelemesi olmadan yayımlanmaz. |
| Anomali | `rolling_anomalies` | Kaydedilmiş tek seride yalnız geçmiş gözlemlerle rolling median/MAD. İlk dönemler ve eksik geçmiş puanlanmaz. MAD sıfırken sapma işaretlenebilir; sonsuz standart puan üretilmez. |
| İlişki/nedensellik incelemesi | `analyze_relationship` | Gecikmeli Pearson korelasyonu ve ADF/dolu örnek koşulları sağlanan Granger testi. Pozitif `lag`, `X(t-lag)` ile `Y(t)` eşlemesidir. Sonuçlar nedensel etki kanıtı değildir. |
| Değişim tespiti | `detect_changes` | İki komşu, tam penceredeki medyan farkıyla kalıcı seviye değişimi adayları. Geriye dönük betimleyici yöntemdir; PELT, çevrimiçi erken uyarı veya kırılma tarihi için anlamlılık testi olarak sunulmaz. |

İstatistik araçları modelden veri dizisi veya dosya yolu almaz. Aynı çalışma
alanına ait `analysis_id` ve sütunları kullanır. Düzenli ve tekil takvim
anahtarı gerekir; aynı dönemde çok sayıda şehir içeren sıralama tablosu
doğrudan tek seri gibi analiz edilemez. Önce ilgili seriyi seçmek gerekir.

Pearson için varsayılan en az 12 tam eşleşme ve en fazla %20 eksik eşleşme
koşulu vardır. Granger eksik satır düşürmez; kesintisiz tam örnek, en az
`max(min_samples, 30, 5*lag+5)` gözlem ve iki seri için ADF p-değerinin
0,05'ten küçük olması gerekir. `difference` dönüşümü açıkça seçilebilir.
Bu kontroller model varsayımlarının tamamını doğrulamaz. Pearson p-değeri
bağımsız gözlem varsayımı taşır; otokorelasyon bu yorumu bozabilir.

## Dosya inceleme, insan kontrolü ve yayın

Dosya yüklendiğinde ham baytlar çalışma alanının kaynak deposuna kopyalanır;
SHA-256, dosya adı, MIME türü ve varsa URL kaydedilir. Sunucunun geçici
upload dosyası bundan sonra silinebilir. Model kaynak kimliğini kullanır.
`inspect_source` metin, PDF sayfa numarası, Excel sayfa adı ve tablo önizlemeleri
döndürür; yükleme veya inceleme workspace veri kümesini kendiliğinden
değiştirmez.

Doğal PDF metni ve tabloları yerelde ayrıştırılır. Metinsiz PDF sayfası,
görsel callback'i varsa sınırlı PNG'ye çevrilip okunur. Uygulamanın mevcut
callback'i MIA Qwen görsel modelini ve yerelde doğrulanan JSON şemasını
kullanır. Ayrı `Unlimited-OCR` çağrısı `MiaClient.ocr` içinde vardır; uygulama
her belgede otomatik iki model karşılaştırması yapmaz. Yatay/kare görsel
farkına ilişkin önceki deney, genel OCR doğruluk garantisi değildir.

Görsel adayının **Çıkarılan hücreleri kontrol et** ekranı tam tabloyu alır,
sekiz satırlık önizlemeyi tablo yerine kaydetmez. Kullanıcı kaynakla
karşılaştırıp değerleri ve birimleri onayladığında `review_table` ham kaynak
hash'i, düzeltilmiş satırlar, birim beyanı ve inceleme hash'ini kaydeder.
Bu doğrulama modelin kendi çıktısını onaylaması değildir; model araçları
arasında `review_table` veya tam tabloyu getiren `review_candidate` yoktur.

Yayın için `publish_selected_table` şu açık bilgileri ister:

- Kaynak ve tablo kimliği; gerekirse bütün sütunlar için birebir ad eşlemesi.
- Veri kümesi adı, doğal frekans, tarih sütunu, tekil anahtar ve grain.
- Her sütun için `dtype`, `unit`, `kind`, `nullable`; parasal ölçek ve
  para birimi gerektiğinde ayrıca belirtilir.
- Sayısal birimle uyumlu kaynak alıntısı veya kaydedilmiş kullanıcı incelemesi.
- Workspace'in beklenen sürümü; gerekirse beklenen satır/dönem kapsamı.

Sayılar biçimden tahmin edilmez. Varsayılan `decimal_dot`; Türkçe ondalık
virgül için sözleşmede `number_format="decimal_comma"` seçilir. Satır
genişliği, tip, sonlu sayısal değer, null, tarih ve tekil anahtar kontrolleri
`store.ingest_csv` ile tamamlanır. Orijinal belge korunur; üretilen CSV'nin
hash'i ile orijinal belgenin hash'i ayrı tutulur. Yeni kaynak adı, başka
kaynaktaki aynı banka kodunu eşdeğer topluluk yapmaz.

Kaynak aylık, çeyreklik veya yıllık satırlarını `YYYY-MM-DD` biçimindeki
dönem sınırlarıyla etiketliyorsa sözleşmede açık `source_date_format` seçilir:
`iso_period_start` her tarihin ilgili dönemin ilk günü, `iso_period_end` son
günü olmasını gerektirir. Örneğin aylık `2025-01-01`, ilk seçenekle `2025-01`
etiketine dönüştürülür. Sayılar ve satır sayısı korunur; günlük gözlemler
toplanmaz veya yeniden örneklenmez. Dönem içindeki diğer tarihler reddedilir.
Özgün ve normalize tarih eşlemeleri kaynak izine yazılır, tekil anahtar
kontrolü yayın sırasında korunur. Varsayılan `native`, zaten uygun dönem
etiketleri bekler. `unit_evidence` değerleri kaynak başlığından/metninden
aynen kopyalanır; çeviri veya açıklama eklemek alıntıyı geçersiz yapar.

Varsayılan kaynak sınırları: 16 MiB belge, 8 MiB görsel, 20 milyon görüntü
pikseli, 30 PDF sayfası, belge başına üç görsel okuma sayfası, 10 aday tablo,
tablo başına 5.000 satır ve 64 sütun. İşlenmeyen tarama sayfaları numaralarıyla
bildirilir. CSV ve metin UTF-8 olmalıdır. XLSX formülleri çalıştırılmaz;
formül içeren sayfa değer olarak hazırlanmalıdır. `.xls` ayrıştırması yoktur.
Birleşik veya iç içe HTML tabloları belirsizlik hatası verir.

Herkese açık URL indirmesinde protokol/port denetimi, her yönlendirmede
yeniden adres doğrulaması ve bağlantının doğrulanan IP'ye sabitlenmesi vardır.
Özel IP, loopback ve yerel adresler engellenir; ortam HTTP proxy'si kullanılmaz.
Üç yönlendirme, DNS bekleme, toplam indirme ve bayt bütçeleri uygulanır.
JavaScript çalıştıran tarayıcıyla gezinme veya giriş gerektiren site oturumu
bu aracın kapsamı değildir.

## Yürütme, durum ve kayıtlar

| Kod | Sorumluluk |
| --- | --- |
| [mia_client.py](../../../agentic_analytics/providers/mia.py) | MIA Chat Completions, embedding ve görsel/OCR HTTP adaptörü; sınırlı yeniden deneme, yapı denetimi, anahtar ve özel muhakemenin çıktıdan ayrılması |
| [agent_runtime.py](../../../agentic_analytics/agent/runtime.py) | Tek karar verici, yerel JSON şeması denetimi, zorunlu hesap planı doğrulaması, araç çağrıları, hata ve bütçe duruşları |
| [agent_run_store.py](../../../agentic_analytics/agent/run_store.py) | SQLite konuşma, run, olay, checkpoint, araç niyeti ve sonuç kayıtları; çalışma alanı kilidi |
| [lakehouse_store.py](../../../agentic_analytics/lakehouse/store.py) | Değişmez snapshot/dataset/analysis, kaynak ve payload hash'leri, sürüm karşılaştırmalı workspace yazımı |
| [agent_documents.py](../../../agentic_analytics/agent/tools/documents.py) | Kaynak alımı, aday tablo, güvenilen UI inceleme API'si ve seçilmiş tablo yayını |
| [agent_statistics.py](../../../agentic_analytics/agent/tools/statistics.py) | Kayıtlı analize bağlı deterministik istatistik ve sonuç artefaktı |
| [app/server.py](../../../app/server.py), [app/static](../../../app/static) | Yerel API, arka plan görevleri, durum sorgulama ve tarayıcı arayüzü |

Güncel varsayılan bütçe bir run için 10 model kararı ve iki hata düzeltmesidir.
Model çıktısı en fazla 4096 token istenir. Bağlam bütçesi 75.000 karakterdir;
bu ölçü sağlayıcının ilan edilmiş token penceresi değildir. Eski konuşma
turları araç çağrısı/sonucu bağı korunarak azaltılır; aktif analiz planı,
şeması ve kaynak kimlikleri store'dan yeniden alınır. Tam araç kayıtları
olay defterinde kalır, modele sınırlı içerik gönderilir.

240 saniyelik varsayılan aktif çalıştırma bütçesi monoton saatle ölçülür;
uygulamanın kapalı kaldığı süre hesaba katılmaz. Devam ettirme yeni aktif
süre aralığı açar, kalıcı toplam model kararı sınırı ise korunur. Bütçe yeni
model isteğinin başlamasını engeller; devam eden HTTP isteğini veya araç
işlemini zorla kesen kesin duvar saati sınırı değildir. Sağlayıcıda 429 ve seçili geçici HTTP/ulaşım
hataları sınırlı tekrar edilir; 401/403 tekrar edilmez. Kesilmiş model
çıktısından araç yürütülmez.

Yazma öncesinde araç niyeti kaydedilir. Aynı istek kimliği aynı sonucu
yeniden kullanabilir. Yazma tamamlanıp cevap kaydı kaybolursa analiz veya
yayın manifestiyle uzlaştırma yapılır. Sonuç belirsizse otomatik yeniden
yazma yerine `UNKNOWN_MUTATION_OUTCOME` ile durulur. Bu mekanizma bütün dış
sistemler için genel bir “tam bir kez” garantisi değildir.

`completed`, `partial`, `needs_input`, `blocked` ve `failed` sonuçları ayrıdır.
Çözülememiş araç hatası, modelin “tamamlandı” demesiyle başarıya dönüşmez.
Kesinti sonrası aynı çalışma kaydı API'deki resume yolu ile devam
ettirilebilir; harcanmış model kararı bütçesi yeniden sıfırlanmaz.

Varsayılan dizin düzeni:

```text
.lakehouse-runtime/app/
  lakehouse/                 snapshot, dataset, analysis ve workspace
    document_sources/        workspace'e bağlı ham belge ve inceleme kayıtları
    statistics/              kaynak analizine bağlı statistic_<hash>.json
  runs/runs.sqlite3          konuşma, run, olay ve araç niyeti/sonucu
  application/               uygulamanın workspace ve iş metadata'sı
```

İstatistiklerin tam sonucu JSON artefaktında kalır. Büyük yanıtlar modele
özet/önizleme ve artefakt kimliği verir. Kullanım kayıtlarındaki ana model
usage'ı, belge callback'lerinin bütün token tüketimini kapsayan eksiksiz
bir maliyet defteri olarak sunulmamalıdır.

## Testler ve kanıt sınırı

```bash
uv pip install -r requirements-app.lock -r requirements-dev.txt
python -m pytest tests/agent/test_mia_client.py tests/agent/test_agent_runtime.py tests/agent/test_agent_documents.py tests/agent/test_agent_statistics.py -q
python -m pytest tests/agent/test_agent_real_contracts.py -q
python -m pytest tests -q
```

İlk komut grubu sağlayıcı yanıtları için sahte taşıma/istemci ve yerel
veri dosyaları kullanır. JSON protokolü, tekrar sınırı, kesinti sonrası
yazma toparlama, konuşma devamı, kaynak/format denetimi, sayısal istatistik
ve artefakt izi test edilir. Belge testleri gerçek yerel ayrıştırıcıları
çalıştırır; görsel callback sonucu sentetiktir, canlı model çağrısı yapmaz.

`test_agent_real_contracts`, yerel competition veritabanı varsa gerçek
lakehouse verisi ve önceden tanımlı model kararlarıyla çalışır; veritabanı
yoksa atlanır. Bu testler doğal dilde Qwen başarı yüzdesi değildir.
Tüm testlerin güncel sayısı ve son tam test koşusunun sonucu ayrı çalıştırma
kaydından alınmalıdır; bu not yeni canlı uçtan uca doğrulama iddiası taşımaz.

## Açık sınırlar ve embedding kararı

EVDS metadata kataloğu ile fiziksel gözlem kapsamı ayrıdır. Mevcut kaynak
envanteri 52.696 metadata serisi, 599 fiziksel seri ve 587 sayısal seri
bildirir; yeni snapshot'ın manifesti güncel kapsamın otoritesidir. Tüm
EVDS'nin hedef dönem gözlemleri tamamlanmamıştır. Toplama kuyruğu çıktısı
kendiliğinden açık çalışma alanına eklenmez.

Doğru plan şeması, modelin doğru metrik veya doğru ekonomik açıklama seçtiğini
kanıtlamaz. Kaynak boşlukları, gözden geçirilmemiş haftalık aileler, eksik
geçmiş dönem ve kaynak izi eksikleri açık hata/uyarı olarak kalabilir.
`source_references_complete` ile `source_files_verified` farklıdır.

Tablo, çizgi grafik ve CSV çıktısı vardır; genel PDF rapor tasarımı, keyfi
grafik türleri, her veri alanı için uzman semantiği veya genel nedensel
etki tahmini yoktur. Görülmemiş uzun belge ve geniş doğal dil soru setinde
tekrarlı canlı değerlendirme ayrıca gerekir.

`MiaClient.embedding` uygulanmıştır; katalog `discover` ve agent döngüsü
şu anda embedding çağırmaz. Mevcut arama Türkçe normalizasyon, sınırlı eş
anlamlılar, İngilizce/arama metni ve uygun adaylarda boyut değerleri kullanır.
Uzun cümleler ve görülmemiş ifade biçimleri için recall hâlâ ölçülmelidir.
Embedding için sonraki sınırlı adım, aynı etiketli Türkçe/İngilizce sorgularda
metin araması ile hibrit sıralamanın recall@5 karşılaştırmasıdır. Doğru ama
`metadata_only` adayları dışarı atmadan ve indeksi snapshot/sözleşme sürümüne
bağlayarak ölçülen yarar görülürse katalog geneline açılabilir. Önceki tek
4096 boyutlu küçük embedding deneyi bütün katalog başarısını kanıtlamaz.

## Canlı sağlayıcıda kullanılan yürütme ayarı

Araç yönlendiren Qwen çağrıları `chat_template_kwargs.enable_thinking=false`
ile gönderilir. [vLLM bu istek alanını belgeliyor](https://docs.vllm.ai/en/stable/features/reasoning_outputs/);
Kloudeks uç noktasında da aynı takılmış konut bağlamıyla doğrudan denendi.
Varsayılan modda üç yanıtın her biri 4.096 düşünme tokenında kesilirken,
ayarı kapatan tek deneme 302 çıktı tokenında geçerli araç çağrısı verdi.
Bu küçük deney, bütün görevlerde bu modun daha doğru olduğu anlamına gelmez.
Araç sonuçlarını denetleyen veri kuralları her iki moddan bağımsızdır.
Görsel ve OCR istemci varsayılanları değiştirilmedi.

HTTP tablo ve hücre açıklamasında JavaScript'in kesin tam sayı sınırını aşan
değerler `{"$integer":"9007199254740993"}` biçiminde taşınır. Tarayıcı bunları
BigInt ile tam gösterir; grafik çizimine almaz. CSV indirmesinde özgün tam
sayı korunur.

Son otomatik ve canlı sonuçlar, başarısız ilk denemeler dahil, [doğrulama kaydında](live-validation.md) bulunur.
