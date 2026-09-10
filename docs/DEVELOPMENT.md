# Geliştirme ve yerel kullanım

Bütün komutlar repo kökünden, Python 3.12 ile çalıştırılır. Bu depo kaynak dizininden modülle çalıştırılır; paket yayımlama adımı gerekmez.

## Ortamı kurma

```sh
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-app.lock -r requirements-dev.txt
```

`requirements-app.lock` uygulamanın sabitlenmiş doğrudan ve dolaylı bağımlılıklarını, `requirements-app.txt` kaynak bağımlılık listesini, `requirements-dev.txt` test ve notebook bağımlılıklarını içerir. Yalnız uygulamayı kullanacaksanız `requirements-dev.txt` gerekmez.

## Veriyi hazırlama

Temiz klonda finans çalışma alanı ve gerçek veriye bağlı kontroller için:

```sh
python data_pipeline/lakehouse/build_lakehouse.py
```

Bu adım mevcut kaynaklardan `data_pipeline/lakehouse/analytics.duckdb` üretir; kaynakları yeniden indirmez. Üretilmiş veritabanı Git'te izlenmez. Yalnız kendi dosyalarınız için boş çalışma alanı kullanıyorsanız bu veritabanına ihtiyaç yoktur.

**Mevcut tam EVDS yayınını temel build ile değiştirmeyin.** Temel build, depodaki seçilmiş kaynak paketini kullanır. Tam yerel EVDS veritabanının güncellenmesi aşağıdaki toplama/yayın akışına aittir. Seçilmiş paket, yerel tam yayın ve metriklerin hesaplara hazır oluşu [veri rehberinde](DATA.md) ayrı açıklanır.

```sh
# Kuyruk durumunu ağ isteği yapmadan gösterir.
python -m tools.evds_bulk_collection status

# Resmî kaynaktan toplar, doğrular ve tamamlandığında yayımlar.
python -m tools.complete_evds_history

# Daha önce tamamlanan toplama için yalnız doğrular, derler ve yayımlar.
python -m tools.complete_evds_history --publish-only
```

Toplama komutu dış kaynağa istek gönderir ve yerel veri yayını oluşturur. Kaldığı yerden devam eder; başarısız veya eksik işler varken aktif veritabanını değiştirmez. Ayrıntılar [EVDS yayın kaydında](research/evds-completion-2026-09-10/implementation.md) ve [pipeline rehberinde](../data_pipeline/README.md).

## Uygulamayı çalıştırma

```sh
python -m app --prompt-key --port 8870
```

[http://127.0.0.1:8870](http://127.0.0.1:8870) adresini açın. `--prompt-key`, `MIA_API_KEY` tanımlı değilse anahtarı terminalde gizli olarak sorar. Anahtarı kaynak dosyalara, notebook hücrelerine veya komut argümanlarına yazmayın. Sağlayıcı tanımlı değilken uygulama kayıt inceleme için açılabilir; yeni model çalışması başlatılamaz.

| Seçenek | Kullanım |
| --- | --- |
| `--host` | `127.0.0.1`, `localhost` veya `::1`; varsayılan `127.0.0.1` |
| `--port` | Varsayılan `8870` |
| `--db /dosya/analytics.duckdb` | Finans profili için farklı kaynak veritabanı |
| `--runtime-root /dizin/agent-app` | Snapshot, konuşma ve sonuç kayıtları için ayrı kök |
| `SEARXNG_URL` | Web araması için yapılandırılmış SearXNG adresi; yoksa Bing RSS kullanılır |

Standart MIA yapılandırması `kkbhackathon2026/Qwen3.8-27B` sohbet modelini kullanır. Model ve HTTP adaptörü [providers/mia.py](../agentic_analytics/providers/mia.py) içinde, uygulama bağlantısı [app/cli.py](../app/cli.py) içindedir.

Arayüzde **KKB finans verileri** hazır yerel snapshot'ı, **Boş çalışma alanı** kendi kaynaklarınız için finans tabloları içermeyen alanı açar. Dosya yüklemek, tabloyu otomatik olarak hesaplara açmaz. Seçilen sütunlar, dönem, birim ve tekil anahtar sözleşmesiyle yayımlanmalıdır. Görselden çıkarılan tablolar kullanıcı incelemesi gerektirir.

## Deterministik lakehouse CLI

Bu komutlar model çağırmaz. Aşağıdaki örnek bir snapshot ve çalışma alanı oluşturur; KOBİ kredi bakiyesine nominal/reel yıllık değişim ekler ve kaynak referanslarını döndürür:

```sh
mkdir -p tmp/agent-demo
python -m agentic_analytics.lakehouse.cli init > tmp/agent-demo/workspace.json
KKB_WORKSPACE_ID=$(python -c "import json; print(json.load(open('tmp/agent-demo/workspace.json'))['workspace_id'])")
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" demo
```

CLI varsayılan deposu `.lakehouse-runtime/`, tarayıcı uygulamasının varsayılan deposu `.lakehouse-runtime/app/` altındadır. Bunlar ayrı kayıt kökleridir. CLI'de `--store`, uygulamada `--runtime-root` ile konum açıkça seçilebilir.

Tekil `discover`, `describe`, `validate_plan`, `execute`, `revise_analysis` ve `explain_value` JSON örnekleri [lakehouse kullanım rehberinde](AGENT_READY_LAKEHOUSE.md) bulunur.

## Testler

```sh
python -m pytest tests -q
```

| Dizin | Kontrol alanı |
| --- | --- |
| `tests/agent/` | Karar döngüsü, araçlar, sağlayıcı sözleşmesi ve teslim davranışı |
| `tests/app/` | HTTP API, çalışma alanları, grafik akışı ve tarayıcıya giden sayıların kesinliği |
| `tests/lakehouse/` | Veri sözleşmeleri, hesaplar, kaynak zinciri, depo ve build |
| `tests/ingestion/` | Kaynak indirme/ayrıştırma, normalizasyon, toplama ve yayın |
| `tests/evals/` | Değerlendirme ve benchmark davranışı |
| `tests/architecture/` | Paketlerin bağımlılık yönü ve katman sınırları |

Örneğin yalnız uygulama ile agent sözleşmelerini çalıştırmak için:

```sh
python -m pytest tests/agent tests/app -q
```

Scripted sağlayıcı kullanan testler gerçek model başarısını ölçmez. Bazı gerçek snapshot kontrolleri veritabanı yoksa atlanır; atlanan kontrol başarılı kabul edilmemelidir. Gerçek yerel kaynaklara bağlı deterministik kabul akışı:

```sh
python -m tools.validate_agent_lakehouse
```

## Canlı değerlendirme

Canlı deneme toplama ile mevcut kayıtları puanlama ayrı komutlardır. İlk komut çalışan yerel uygulama üzerinden gerçek sağlayıcı çağrıları yapar:

```sh
python -m evals.consistency \
  --base-url http://127.0.0.1:8870 \
  --output tmp/agent-consistency-next/live \
  --repeats 3 --workers 2
```

Aynı manifestin üzerine yazmayan yeni bir çıktı dizini kullanın. Her tekrar ayrı çalışma alanı ve istek kimliğiyle yürütülür; başarısız sonuçlar kayıtta tutulur. Komut yalnız kanıt toplar, doğruluk puanı vermez.

Önceden oluşturulmuş bağımsız doğrular ve cevaba bağlı inceleme kayıtlarıyla puanlama:

```sh
python -m evals.grading \
  --input tmp/agent-consistency-next/live \
  --oracles tmp/agent-consistency-next/oracles.json \
  --answer-reviews tmp/agent-consistency-next/answer-reviews.json \
  --output tmp/agent-consistency-next/graded.json
```

`oracles.json` ve `answer-reviews.json` örnekte var olması gereken girdilerdir; toplama komutu bunları üretmez. Doğrular aynı veri snapshot'ından bağımsız yöntemle hazırlanmalı, metin incelemesi o cevabın SHA-256 değerine bağlanmalıdır. Puanlayıcı model veya ağ çağrısı yapmaz; eksik incelemeyi başarıya çeviremez. [İlk ölçümün kayıtları ve sınırları](validation/agent-consistency-2026-09-10/README.md) örnek sağlar.

## Notebook ve benchmark

- [Veri keşfi notebooku](../notebooks/lakehouse_veri_kesfi_ve_iliskiler.ipynb): salt okunur envanter, kaynak ilişkileri ve örnek analizler.
- [Doğrulanmış veri notebooku](../notebooks/KKB_Verileri_Dogrulanmis.ipynb): `tools/build_data_status_notebook.py` ile üretilen kaynak kapsamı görünümü.

Aktif ortamın çekirdeğiyle keşif notebookunu çalıştırmak için:

```sh
jupyter-execute notebooks/lakehouse_veri_kesfi_ve_iliskiler.ipynb --inplace --timeout=180
```

Bütün hücreleri çalıştırmak çıktı hücrelerini günceller. Notebook, otomatik testlerin veya kaynak doğrulamalarının yerine geçmez. [Yorumlama rehberi](LAKEHOUSE_VERI_KESFI_SADE_ANLATIM.md) sonuçların sınırlarını açıklar.

```sh
python -m evals.benchmark --iterations 5
```

Benchmark veritabanını salt okunur açar, bütünlük kontrolleri ile sorgu sürelerini ayrı raporlar. Varsayılan çıktı `tmp/lakehouse-benchmark/results.json` olur; `--database` ve `--output` ile konum seçilebilir. [Eski benchmark kaydı](validation/lakehouse-benchmark/results.json) yeni çalıştırmayla değişmez. Süreler makine, veri sürümü ve önbelleğe bağlıdır; ekonomik doğruluk ölçütü değildir.

## Değişiklik yaparken

İşlevin ait olduğu katmanı [mimari rehberinden](ARCHITECTURE.md) seçin. Sayısal veya kapsam davranışı değişiyorsa ilgili sözleşme ve gerçek kaynak örneklerini birlikte kontrol edin. Arayüz değişikliklerinde mobil görünüm, kaynak hücresine erişim ve grafik dışa aktarımı ayrıca sınanmalıdır.

Tarihli kanıt dosyalarını yeni sürümün sonuçlarıyla değiştirmeyin; yeni ölçümü ayrı dizine kaydedin. Yerel veritabanı, büyük çalışma kayıtları ve sağlayıcı anahtarları commit kapsamına girmez. Eski belgelerdeki kod hashleri ve taşınan yolların nasıl okunacağı [dokümantasyon dizininde](README.md#dosya-düzeni-değişikliği) açıklanır.
