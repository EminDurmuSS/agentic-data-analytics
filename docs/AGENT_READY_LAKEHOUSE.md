# Agent için lakehouse kullanım rehberi

Bu uygulama, doğal dil modelinin çağırabileceği deterministik veri araçlarını
sağlar. Metrik keşfi, plan doğrulama, gerçek hesap, tablo revizyonu ve kaynak
referansları çalışır. Buradaki komutlar canlı Qwen çağrısı yapmaz.

10 Eylül doğrulamasında 223 test ve gerçek veri/CLI üzerinde 20 kabul kontrolü
geçti. [Kaydedilen sonuçlar](validation/agent_lakehouse_2026-09-10.json),
kontrol edilen kaynak hücrelerini ve kapsam sınırlarını da içerir. Gerçek veri
kabul akışını yeniden çalıştırmak için, build sonrasında:

```bash
python -m tools.validate_agent_lakehouse
```

Çıktılar `tmp/agent-lakehouse-validation/` içine yazılır. Bu komut yerel
kaynakları kullanır; belgelenen tek canlı EVDS bağlantı denemesini tekrarlamaz.

## Kurulum ve veri üretimi

Repo kökünde Python 3.12 ortamını kurun:

```bash
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-app.lock -r requirements-dev.txt
python data_pipeline/lakehouse/build_lakehouse.py
python -m pytest tests -q
```

Temiz klonda **build, testlerden önce gelmelidir**. `analytics.duckdb`
yeniden üretilebilir bir çıktıdır; GitHub'ın 100 MiB sınırını aştığı için
Git'te izlenmez. İzlenen kaynak Parquet'ler, kaynak manifestleri, üretim kodu
ve metrik sözleşmeleri build girdileridir. Bu komut mevcut kaynakları
indirmez. Kaynak temizleme kodu değiştirildiğinde ilgili kaynağın işleme
betiğini ve birleşik kataloğu da önce yeniden üretin.

Repoyla gelen seçilmiş kaynak paketi 70 tablo/view ve 599 fiziksel EVDS
serisi içerir; 587 seride sayısal değer vardır. Bütün katalog için yerel
toplama ve yayın `python -m tools.complete_evds_history` ile yapılır.
Tam yerel yayın kullanılıyorsa temel build komutunu yeniden çalıştırmayın;
yayınlama komutu kendi sabitlenmiş kataloğunu ve veritabanını üretir.
Seçilmiş paketin raporu `data_pipeline/lakehouse/validation.json`, tam
EVDS yayınının raporu `.lakehouse-runtime/evds-builds/LATEST.json`
konumundadır. Açılan veritabanının `catalog.build_validation` tablosu ve
snapshot manifesti kesin sürümü gösterir. İstek kapsamı, sayısal gözlem
kapsamı ve ekonomik dönüşümlerin hazır oluşu ayrı değerlendirilir.
[Uygulama ve kaynak doğrulamaları](research/evds-completion-2026-09-10/implementation.md).

## Uygulanan veri kuralları

- BDDK aylıkta birim ve ölçü anlamı satır/metrik düzeyinde değerlendirilir.
  Para, oran ve müşteri sayısı ayrı tutulur. Bankalar arası müşteri sayıları
  tekilleştirilmiş kişi sayısı olarak yorumlanmaz.
- Kümülatif kaynak değeri korunur. Dönemlik fark için aynı takvim yılındaki
  ardışık kaynak ayı gerekir; eksik ayın üzerinden fark alınmaz.
- Haftalık BDDK sözlüğü geçerlilik tarihleriyle eşlenir. Tanım aralıklarının
  çakışması veya kaynak birleştirmesinin satır çoğaltması yayını durdurur.
- Aylık, haftalık ve FinTürk banka grup kodları kendi kaynak ad alanındadır.
  Aynı sayı farklı kaynaklarda aynı grubu garanti etmez.
- Ham null, kanıtlanmış yapısal durum ve türetilmiş kullanılabilir değer
  ayrı tutulur. Eksik değerler genel bir sıfır doldurma işlemiyle kapatılmaz.
- Risk Merkezi'nin farklı kaynak vintageları saklanır. Son kaynak sürümünün
  seçimi ayrıca tanımlıdır; kaynak vintage etiketi doğrulanmış yayın tarihi
  olarak sunulmaz.
- Metrik sözleşmesi tablo/sütun/filtre, grain, birim/ölçek, frekans, anlam,
  kaynak referansları ve kullanım durumunu belirtir. `review_required`
  metriklerin ham seçimi uyarılı olabilir; sayısal dönüşümleri engellenir.
- Bölgesel türevler ve hazır türetilmiş altın metriği, hesap tarifi bireysel
  kaynak hücrelerine bağlanana kadar yeni agent dönüşümlerine kapalıdır.
  Uygun olduğunda kaynak metriği seçilip izinli işlem araçları kullanılabilir.

Yayın kapısı kaynak anahtarlarını, dönemleri, birimleri, sayısal değerleri,
sözlük ilişkilerini ve sözleşme durumlarını kontrol eder. Yeni veri dosyası
kontroller geçmeden aktif dosyanın yerine geçirilmez. Bu kapı yerel kaynak
sürümünü doğrular; bütün EVDS kapsamını veya herhangi bir ekonomik iddiayı
doğruladığı anlamına gelmez.

## Araç arayüzü

`agentic_analytics/lakehouse/service.py` içindeki `LakehouseService`, şu çağrıları sağlar:

| Araç | Girdi ve davranış |
| --- | --- |
| `discover` | Metinle metrik arar, sınırlı sayıda küçük kart döndürür |
| `describe` | Bir metriğin fiziksel ve semantik sözleşmesini gösterir |
| `validate_plan` | Boyut, dönem, frekans ve işlemleri gerçek veri üzerinde kontrol eder |
| `execute` | Planı DuckDB/Pandas ile hesaplayıp değişmez sonucu kaydeder |
| `revise_analysis` | Kayıtlı plana sütun veya işlem ekleyip yeni analiz oluşturur |
| `explain_value` | Bir sonuç hücresinin saklanan hesap ve kaynak referanslarını açar |

İstek dili serbest SQL, kabuk komutu veya dosya yolu kabul etmez. Dosya
yolları yalnız yönetici CLI/Python yapılandırmasıdır. İzinli hesaplar
`growth`, `difference`, `deflate`, `scale` ve `ratio` işlemleridir.
Frekans dönüşümü açıkça seçilir. Aylık akımın çeyreklik toplamı için üç
sayısal ay gerekir; oran ve stoklar bu şekilde toplanamaz. Üst frekansa
yapay gözlem üretimi engellenir. Oranlarda uygun yüzde puan farkı kullanılır.
Eksiklik ve sıfır payda sonuçlarda raporlanır.
CSV tam sayıları ham seçimde eksik dönemler olsa da tam olarak korunur.
Kayan noktalı hesabın tam sayı hassasiyetini koruyamadığı büyüklüklerde
hesaplama `NUMERIC_PRECISION_UNSUPPORTED` ile engellenir.

İki sütunun aynı dönem tablosunda bulunması, aynı nüfusu veya kurum kapsamını
ölçtükleri anlamına gelmez. `ratio`, varsayılan `scope_policy="same_scope"`
ile kaynak ad alanı, kurum/coğrafya kapsamı ve seçilmiş boyutların eşleşmesini
ister. Birim, para birimi ve reel fiyat bazının da uyumlu olması gerekir.
Farklı kapsamları bilinçli olarak karşılaştırmak için ilgili `ratio`
işlemine `scope_policy="explicit_comparison"` ve 10-500 karakterlik
`scope_reason` eklenir. Sonuç `cross_scope_comparison` uyarısını ve iki
kapsamı korur; karşılaştırma nüfusların eşdeğer olduğunu kanıtlamaz.
Bu seçenek birim veya fiyat bazı uyuşmazlığını geçersiz kılmaz.

Engellenen istekler `errors: [{"code": "...", "message": "..."}]`
biçiminde açıklanır. `METADATA_ONLY`, `NO_NUMERIC_VALUES`,
`NO_PHYSICAL_BINDING`, `METRIC_NOT_FOUND`, `SEMANTICS_REVIEW_REQUIRED`,
`UNIT_MISMATCH`, `SCOPE_MISMATCH`, `INVALID_TEMPORAL_AGGREGATION`, `AMBIGUOUS_GRAIN`,
`DEFINITION_BREAK` ve `MISSING_OBSERVATIONS` farklı nedenlerdir. Çalışma
alanı yarışında CLI `VERSION_CONFLICT` döndürür. Böylece agent eksik veri
toplama, plan düzeltme ve güncel sürümü tekrar okuma adımlarını ayırabilir.

## CLI: başlatma, keşif ve hesap

Bütün komutları repo kökünde, aynı etkin Python ortamında çalıştırın.
`--store` ve `--workspace` seçenekleri alt komuttan **önce** yazılır.
Varsayılan depo `.lakehouse-runtime/` dizinidir.

```bash
mkdir -p tmp/agent-demo
python -m agentic_analytics.lakehouse.cli init > tmp/agent-demo/workspace.json
KKB_WORKSPACE_ID=$(python -c "import json; print(json.load(open('tmp/agent-demo/workspace.json'))['workspace_id'])")
```

`init`, mevcut DuckDB'yi yayın kapısından geçirip değişmez kopyasını saklar
ve bir çalışma alanı döndürür. İstek dosyalarını oluşturun:

```bash
python - <<'PY'
import json
from pathlib import Path

directory = Path("tmp/agent-demo")
metric = "bddk_monthly:table06:1:38c9ed21984d:NakdiKrediToplam"
requests = {
    "discover": {"query": "bddk_monthly:table06", "limit": 5},
    "describe": {"metric_id": metric},
    "plan": {
        "start": "2021-01", "end": "2026-06", "frequency": "monthly",
        "columns": [{"name": "sme_credit", "metric_id": metric,
                     "dimensions": {"group_code": 10001}}],
        "operations": [{"op": "growth", "column": "sme_credit",
                        "output": "nominal_yoy_pct", "periods": 12}],
    },
}
for name, request in requests.items():
    (directory / f"{name}.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
PY
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" discover --request tmp/agent-demo/discover.json
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" describe --request tmp/agent-demo/describe.json
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" validate_plan --request tmp/agent-demo/plan.json
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" execute --request tmp/agent-demo/plan.json > tmp/agent-demo/first.json
```

`execute` bir `analysis_id`, sonuç şeması, uyarılar ve kısa önizleme döndürür.
Tam tablo depoda Parquet olarak tutulur. Bu kaynak penceresinde 2020 KOBİ
bakiyesi bulunmadığından 2021 yıllık büyümesi null kalır ve uyarılır.

## Aynı tabloya reel büyüme ekleme ve açıklama

Önceki yanıtın kimliğini yeni istekte kullanın:

```bash
python - <<'PY'
import json
from pathlib import Path

directory = Path("tmp/agent-demo")
first = json.loads((directory / "first.json").read_text())
request = {
    "analysis_id": first["analysis_id"],
    "add_columns": [{"name": "cpi", "metric_id": "evds:TP.TUKFIY2025.GENEL"}],
    "operations": [
        {"op": "deflate", "column": "sme_credit", "index": "cpi",
         "base_period": "2021-01", "output": "real_sme_credit"},
        {"op": "growth", "column": "real_sme_credit",
         "output": "real_yoy_pct", "periods": 12},
    ],
}
(directory / "revision.json").write_text(json.dumps(request, indent=2))
PY
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" revise_analysis --request tmp/agent-demo/revision.json > tmp/agent-demo/revised.json
python - <<'PY'
import json
from pathlib import Path

directory = Path("tmp/agent-demo")
revised = json.loads((directory / "revised.json").read_text())
request = {"analysis_id": revised["analysis_id"],
           "column": "real_yoy_pct", "period": "2026-06"}
(directory / "explain.json").write_text(json.dumps(request))
PY
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" explain_value --request tmp/agent-demo/explain.json
```

Revizyon, değişmesi açıkça istenmeyen önceki sütunları ve dönem anahtarlarını
korur. Önceki Parquet ve manifest üzerine yazılmaz. Aynı çalışma alanına
eşzamanlı yazan iki çağrıdan eski sürümü kullanan `VersionConflict` alır;
yeniden denemeden önce güncel çalışma alanı okunmalıdır.

`source_references_complete` ve uyumluluk alanı `lineage_complete`, kayıtlı
kaynak referanslarının ve hesap ağacının yeterliliğini bildirir. Bu çağrı ham
harici dosyaları yeniden açıp doğrulamaz; `source_files_verified` bunu ayrı
belirtir. Kaynak izi eksik türevler tam kaynak kanıtı varmış gibi sunulmaz.

Aynı KOBİ, reel revizyon ve açıklama akışını tek komutla çalıştırmak için:

```bash
python -m agentic_analytics.lakehouse.cli --workspace "$KKB_WORKSPACE_ID" demo
```

## CSV ile yeni bir alan ekleme

CSV ekleme şu an güvenilen yerel Python API'sindedir; CLI'da bir `ingest`
komutu yoktur. Alan anlamı model tarafından otomatik tahmin edilmez. Aşağıdaki
**sentetik klinik ziyaretleri** örneği, finans dışındaki verinin aynı depoya
açık sözleşmeyle eklenmesini gösterir:

```python
import json
from pathlib import Path

from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore

directory = Path("tmp/agent-demo")
original = json.loads((directory / "workspace.json").read_text())
store = LakehouseStore(".lakehouse-runtime")
workspace = store.create_workspace(original["snapshot_id"])
source = directory / "synthetic_visits.csv"
source.write_text(
    "month,clinic,visits\n2026-01,A,10\n2026-02,A,20\n", encoding="utf-8")
contract = {
    "name": "synthetic_clinic_visits",
    "frequency": "monthly", "date_column": "month",
    "key": ["month", "clinic"], "grain": ["month", "clinic"],
    "expected_rows": 2, "expected_periods": ["2026-01", "2026-02"],
    "columns": {
        "month": {"dtype": "date", "unit": "calendar",
                  "kind": "dimension", "nullable": False},
        "clinic": {"dtype": "string", "unit": "label",
                   "kind": "dimension", "nullable": False},
        "visits": {"dtype": "integer", "unit": "visits",
                   "kind": "count_flow", "nullable": False},
    },
}
workspace = store.ingest_csv(
    workspace["workspace_id"], source, contract,
    expected_version=workspace["version"])
dataset_id = workspace["datasets"][-1]
service = LakehouseService(store, workspace["workspace_id"])
result = service.execute({
    "start": "2026-01", "end": "2026-02", "frequency": "monthly",
    "columns": [{"name": "visits", "metric_id": f"overlay:{dataset_id}:visits",
                 "dimensions": {"clinic": "A"}}],
})
frame, manifest = store.load_analysis(result["analysis_id"])
print(frame)
```

Her sütunun `dtype`, `unit`, `kind` ve `nullable` alanları açıkça belirtilir.
Türler `string`, `integer`, `float`, `boolean` ve `date` olabilir. Tarihler
aylık için `YYYY-MM`, çeyreklik için `YYYY-Qn`, yıllık için `YYYY`, diğer
takvim tarihleri için `YYYY-MM-DD` biçimindedir. Boolean değerleri `true` ve
`false` olarak yazılır. Tam sayılar Int64 sınırında kesin olarak doğrulanır.

Anahtar null veya tekrarlı olamaz; CSV başlığı ve kayıt genişliği sözleşmeyle
eşleşmelidir. Ham dosya ve normalize Parquet ayrı hash'lerle saklanır.
`expected_periods`, her mevcut varlığın beklenen dönemlerini kontrol eder;
hiç gelmemiş bir varlık listesini kendiliğinden bilemez. İstenirse
`expected_rows` ile toplam kayıt sayısı da doğrulanır. Bu beklentiler
verilmezse manifestte kapsam `not_asserted` kalır. Başarısız doğrulama aktif
çalışma alanını değiştirmez.

## Depo ve kaynak sınırları

Varsayılan depoda `snapshots`, `datasets`, `analyses` ve `workspaces`
dizinleri bulunur. İçerik hash'li nesneler değişmezdir. Çalışma alanının
`CURRENT.json` işaretçisi ancak yeni revizyon hazır olduğunda atomik
değiştirilir. Eski revizyonlar saklanır; otomatik çöp toplama uygulanmadı.
Diskten yeniden açılan okuyucu aynı kayıtlı sonucu okuyabilir.

Varsayılan sınırlar CSV için 64 MiB, snapshot için 2 GiB, sonuç için
1.000.000 satır/256 sütun/256 MiB ve JSON metadata için 8 MiB'dir. Bunlar
depo sınırlarıdır. Araçların sorgu, plan ve önizleme sınırları ayrıca
uygulanır. Yerel kilitleme POSIX `flock` kullanır; bu uygulama çok makineli
bir yazma servisi veya işletim sistemi düzeyinde SQL sandbox'ı değildir.

## EVDS toplama kuyruğu

Tam katalog için güncel yol, toplama ile yayını birlikte yürüten komuttur:

```bash
python -m tools.complete_evds_history
python -m tools.evds_bulk_collection status
```

Bu yol `tmp/evds_bulk/queue.sqlite` kullanır. 10 Eylül 2026 yerel yayını
52.696 serinin istek aralığını tamamladı; sayısal kaynak eksikleri ve
doğrulanmamış ekonomik dönüşümler ayrıca raporlanır. Yeni çalışma alanları
tamamlanan yayını açar. [Uygulama kaydı](research/evds-completion-2026-09-10/implementation.md).

Aşağıdaki önceki tek-serili araç ayrı `tmp/evds_collection/queue.sqlite`
kuyruğuyla korunur. Bu eski kuyruğun durumu yeni toplamanın kapsam raporu
değildir. Eski araç, mevcut bütün metadata üzerinden Ocak 2021-Haziran 2026 isteklerini
planlar. Planlama ve durum okuma yereldir; `run` ağ isteği yapar. Mevcut
gözlemler ve doğrulanabilen istek/cevap izleri yeniden kullanılır. Arşiv
etiketi tek başına seriyi yarışma penceresinden çıkarmak için yeterli değildir.

```bash
python tools/evds_collection_queue.py plan --database tmp/evds_collection/queue.sqlite --start 2021-01-01 --end 2026-06-30
python tools/evds_collection_queue.py status --database tmp/evds_collection/queue.sqlite
python tools/evds_collection_queue.py run --database tmp/evds_collection/queue.sqlite --max-jobs 1 --timeout 45 --max-attempts 3
```

Yeni `run` çağrıları sıradaki işi alır. İşler SQLite'ta saklanır; başarısız
denemeler, bekleme zamanı ve süresi dolmuş iş sahipliği yönetilir. Python
`urllib` taşıması kullanılır. Her denemenin isteği, cevabı, SHA-256 değerleri,
gözlemleri ve doğrulaması ayrı saklanır. `succeeded`, sınırlı isteğin geçerli
cevap aldığı anlamına gelir; bütün dönemlerde sayısal veri bulunduğu anlamına
gelmez. Takvimi kanıtlanmamış yüksek frekans boşlukları tamamlanmış sayılmaz.

Eski kuyruğun çıktıları doğrudan aktif analize eklenmez. Yeni gözlemler kaynak
adaptörleri ve katalogla bütünleştirilip yayın kapısından geçirilmelidir.
Güncel toplu komut bu doğrulama ve yayınlama adımını da yapar.

## Kalan işler

Bu rehber veri servisini anlatır. Çalışan arayüz, Kloudeks agent döngüsü,
belge ve istatistik araçları [uygulama notunda](research/agent-harness-2026-09-10/implementation.md)
açıklanır. EVDS kaynak istek kapsamı tamamlandı; bütün kaynak boşluklarının
ekonomik açıklaması ve yeni serilerin dönüşüm sözleşmeleri ayrıca incelenmelidir.
Geniş bir soru kümesinde canlı model başarısı, her belge düzeninde doğru
çıkarım ve genel nedensel etki tahmini tamamlanmış kabul edilmez.
