# Agentic Data Analytics Hackathon

KKB Agentic Data Analytics Hackathon için yerel lakehouse ve agent'ın
çağırabileceği deterministik veri araçları. Kaynak verisi, metrik sözleşmeleri,
hesap planları ve sürümlü analiz sonuçları birlikte yönetilir. Qwen bağlantısı
ve kullanıcı arayüzü henüz uygulanmadı; EVDS'nin tüm tarihsel gözlem kapsamı
henüz tamamlanmadı.

Uygulanan araçlar ve çalıştırılabilir örnekler:
[Agent için lakehouse kullanım rehberi](docs/AGENT_READY_LAKEHOUSE.md).

## Veri aşamasının güncel durumu

| Kaynak | Kapsam | Durum |
| --- | --- | --- |
| BDDK aylık | Ocak 2021-Haziran 2026, 66 ay, 17 tablonun tamamı, 10 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK haftalık | 286 hafta, 9 tablonun tamamı, 7 resmî banka grubu | Tamamlandı ve doğrulandı |
| BDDK FinTürk | 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve `YURT DIŞI` | Tamamlandı ve doğrulandı |
| TCMB EVDS katalog | 676 veri grubu, 52.696 benzersiz seri kaydı | Tamamlandı, metadata kataloğu |
| TCMB EVDS gözlem | 584 seçilmiş seri ve eski yerel paketten bağlanan 15 ek seri: 599 fiziksel seri, 587'sinde sayısal değer | Tüm EVDS kapsamı tamamlanmadı; devam ettirilebilir toplama kuyruğu mevcut |
| TÜİK il konut satışları | 81 il, Ocak 2020-Haziran 2026, 5 aylık satış metriği | Tamamlandı ve EVDS ile çapraz doğrulandı |
| İl bazlı konut paneli | 81 il, 22 çeyrek, satış, fiyat, kira, kredi, mevduat, KFE ve YKKE göstergeleri | Yerel panel mevcut; eksiklikler sütun bazında kontrol edilir, kaynak hücresine eşlenmemiş türevler agent hesaplarına kapalı |
| TBB tüketici kredileri | 2021 Mart-2026 Mart, 21 yayımlanmış çeyrek | Tamamlandı; 9 Eylül 2026 kontrolünde 2026 Haziran raporu kaynakta yok |
| TBB Risk Merkezi | 2021 Ocak-2026 Haziran, 66 ay, 5 konut kredisi metriği | Tamamlandı; 6 resmî bülten ve tüm kaynak vintageları saklandı |
| Resmî karar belgeleri | 4 BDDK kararı ve 4 TCMB destek belgesi | Tam metin, çıkarılmış metin ve SHA-256 mevcut |

Ham kaynak değerleri değiştirilmez. Bir değer yalnız aynı resmî kaynak içindeki
kesin bir toplamsal kimlikle kanıtlanabiliyorsa ayrı `usable_value` ve audit
kaydıyla kullanılabilir. Stok ile kullandırım akımı karıştırılmaz, kümülatif
değer ile türetilen dönemlik akım ayrı tutulur ve çeyreklik veri ara aylara
yapay olarak yayılmaz.

## Ölçek

- BDDK aylık: 1.122 resmî istek, 11.220 tablo-grup kaydı, 339.650 kaynak satırı, 1.334.850 semantik ölçüm
- BDDK haftalık: 18.018 resmî sayfa, 1.736.650 ham hücre, 1.025.974 ölçüm. Kaynaktaki 2.230 boş hücrenin tamamı yapısal `FX uygulanamaz` olarak açıklandı
- BDDK FinTürk: 84.484 kaynak satırı, 936.512 ölçüm. 30.892 kaynak boşluğunun 29.564'ü yapısal. 1.328 şube sayısı hücresinin ham null değeri korundu, tamamı fonksiyon grubu kimliğiyle analitik sıfır olarak kanıtlandı, çözümlenmemiş şube boşluğu 0
- EVDS: 52.696 seri metadata kaydı, 599 yerel kaynak seri, 46.178 gözlem satırı ve 43.046 sayısal değer; 1 türetilmiş altın metriği ayrıca tutulur
- Bölgesel panel: 81 il x 22 çeyrek, 1.782 tekil satır, 33 analitik metrik ve 1.620 resmî fiyat gözlemi. Fiyatın bulunması kira ve diğer sütunların da dolu olduğunu göstermez
- TÜİK il konut satışları: 31.590 il-ay-metrik satırı, EVDS ile 25.262 birebir eşleşme, 0 değer uyuşmazlığı
- TBB Risk Merkezi: 6 PDF, 390 vintage gözlem, 66 aylık eksiksiz panel, 5 metrik ve 6 resmî revizyon
- Birleşik katalog: 72 veri varlığı, 55.501 metrik, 3.402 yerel gözlemi bulunan metrik. Fiziksel gözlem bulunması, bütün hesaplara izin verildiği anlamına gelmez
- DuckDB: 10 şema, 70 tablo veya view, mutlak kaynak dosya yoluna ihtiyaç duymayan yerel sorgu dosyası

Bu sayılar mevcut kaynak sürümünün envanteridir. Yeni bir yayının gerçek
sayıları ve durumu `data_pipeline/lakehouse/validation.json`,
`catalog.build_validation` ve yayımlanan snapshot manifestiyle doğrulanır.

## Klasörler

| Yol | İçerik |
| --- | --- |
| `data_pipeline/bddk/` | Aylık, haftalık ve FinTürk ham verileri ile doğrulanmış çıktılar |
| `data_pipeline/evds/` | Seçilmiş gözlemler, hizalama denetimleri ve kaynak manifestleri |
| `data_pipeline/tuik/` | TÜİK il konut satışlarının ham ihracı, işlenmiş gözlemleri ve EVDS uzlaştırması |
| `data_pipeline/regional/` | İl bazlı konut, kredi, mevduat ve fiyat analitik paneli |
| `data_pipeline/tbb/` | Tüketici kredisi raporları, gerçek kullandırım akımı ve bakiye verileri |
| `data_pipeline/risk_center/` | TBB Risk Merkezi aylık bültenleri, konut kredisi grafikleri ve vintage denetimi |
| `data_pipeline/catalog/` | Tam EVDS metadata kataloğu ve birleşik veri sözlüğü |
| `data_pipeline/quality/` | Kurumlar arası kapsam ve tutarlılık kontrolleri |
| `data_pipeline/evidence/` | Resmî yöntem ve karar belgeleri |
| `data_pipeline/lakehouse/` | DuckDB üreticisi, kaynak ilişkileri ve çalıştırılabilir metrik sözleşmeleri |
| `notebooks/` | Doğrulama ve örnek analiz notebook'u |
| `docs/` | Mevcut durum, kaynak ve araştırma notları |
| `tools/` | Kaynak indiricileri, EVDS kuyruğu, yayın kontrolleri, agent araçları ve sürümlü sonuç deposu |

## Kurulum ve doğrulama

Python 3.12 kullanılır:

```bash
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-dev.txt
python data_pipeline/lakehouse/build_lakehouse.py
python -m unittest discover -s tests -v
```

**Temiz klonda testlerden önce veritabanını üretin.** `analytics.duckdb`
üretilmiş bir dosyadır ve GitHub'ın 100 MiB dosya sınırını aştığı için artık
Git'te izlenmez. Kaynak Parquet'ler, üretim kodu ve metrik kuralları repodadır.
Derleme bunları kullanarak yerel dosyayı üretir; mevcut kaynakları yeniden
indirmez. `.lakehouse-runtime/` içindeki snapshot, oturum ve sonuçlar da
yereldir ve Git'e eklenmez.

İlk agent veri akışını çalıştırmak için:

```bash
mkdir -p tmp/agent-demo
python -m tools.lakehouse_cli init > tmp/agent-demo/workspace.json
KKB_WORKSPACE_ID=$(python -c "import json; print(json.load(open('tmp/agent-demo/workspace.json'))['workspace_id'])")
python -m tools.lakehouse_cli --workspace "$KKB_WORKSPACE_ID" demo
```

Bu demo gerçek KOBİ kredi verisiyle yıllık büyüme hesaplar, aynı analize TÜFE
ve reel büyüme ekler, ardından Haziran 2026 sonucunun kaynak referanslarını
gösterir. Model çağırmaz. Ayrı `discover`, `describe`, `validate_plan`,
`execute`, `revise_analysis` ve `explain_value` istekleri için
[kullanım rehberine](docs/AGENT_READY_LAKEHOUSE.md) bakın.

İşlenmiş katmanları mevcut ham kaynaklardan yeniden üretmek için:

```bash
.venv/bin/python data_pipeline/bddk/build_monthly_all_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_semantic_dataset.py
.venv/bin/python data_pipeline/bddk/build_finturk_dataset.py
.venv/bin/python data_pipeline/bddk/build_weekly_dataset.py
.venv/bin/python data_pipeline/tbb/build_consumer_credit_dataset.py
.venv/bin/python data_pipeline/risk_center/build_monthly_housing_dataset.py
.venv/bin/python data_pipeline/tuik/build_province_housing_sales.py
.venv/bin/python data_pipeline/regional/build_housing_panel.py
.venv/bin/python data_pipeline/quality/build_cross_source_reconciliation.py
.venv/bin/python data_pipeline/evidence/events/build_events.py
.venv/bin/python data_pipeline/catalog/build_unified_catalog.py
.venv/bin/python data_pipeline/lakehouse/build_lakehouse.py
.venv/bin/python tools/build_data_status_notebook.py
.venv/bin/python data_pipeline/build_file_manifest.py
```

## Veri keşfi ve performans kontrolü

[Veri keşfi notebooku](lakehouse_veri_kesfi_ve_iliskiler.ipynb), lakehouse'u
salt okunur inceler; envanter, kaynak ilişkileri, eksiklikler ve örnek analizler
sunar. Notebooku kurulan `.venv` ortamının çekirdeğiyle baştan sona çalıştırın.
Terminalden tüm hücreleri çalıştırıp kayıtlı çıktıları yenilemek için:

```bash
.venv/bin/jupyter-execute lakehouse_veri_kesfi_ve_iliskiler.ipynb --inplace --timeout=180
```

[Kullanım ve yorumlama rehberi](docs/LAKEHOUSE_VERI_KESFI_SADE_ANLATIM.md),
çıktıların kapsamını ve analitik sınırlarını açıklar. Notebooklar keşif
çalışmalarıdır; agent'ın çağıracağı uygulanan araçlar
`tools/lakehouse_service.py` içindedir.

[Benchmark betiği](test_lakehouse_performance.py) bütünlük kontrollerini ve
seçili sorgu sürelerini ölçer:

```bash
.venv/bin/python test_lakehouse_performance.py
```

Komut [JSON raporunu](lakehouse_benchmark_results.json) yeniler. Ayrı bir rapor
için `--output /tmp/lakehouse-benchmark.json`, tekrar sayısı için
`--iterations 5` kullanılabilir. Veritabanı salt okunur açılır; zamanlama
sonuçları makineye ve önbelleğe bağlıdır, ekonomik doğruluk kanıtı değildir.

## Kapsam ve açık işler

BDDK aylık bültende bütün tablolar ve 10 resmî banka grubu, haftalık bültende
bütün tablolar ve 7 resmî banka grubu alındı. FinTürk'te de bütün banka
grupları ve bütün iller alındı. EVDS'nin tüm 52.696 serisinin tarihsel
gözlemleri indirilmedi. Bunun yerine 61 ulusal nedensellik ve piyasa serisi,
519 il veya bölge bazlı konut serisi ve KKM ile hanehalkı mevduatını kapsayan
4 seri seçildi; eski yerel paketteki 15 ek seri de kataloğa ve sorgu katmanına
bağlandı. Yarışmanın tüm EVDS gözlemlerini toplama gereksinimi hâlâ açıktır.
`tools/evds_collection_queue.py`, tüm katalog için yerel plan, sınırlı indirme
ve yeniden başlatılabilir kuyruk sağlar. İndirme başarısı, sayısal gözlem ve
takvim bütünlüğü ayrı izlenir. Kuyruk çıktıları doğrulandıktan sonra kaynak
adaptörüne alınmalıdır; otomatik olarak aktif lakehouse'a yayımlanmaz.
BIST altın kapanış fiyatından `0.001` katsayısıyla TL/kg ->
TL/gram dönüşümü yapılan 1 ek seri de kaynak ve formül bilgisiyle ayrıca
tutulur. Bölgesel katmanda 81 ilin konut satışları, birim fiyatları, birim
kiraları, bölgesel KFE ve YKKE değerleri FinTürk kredi ve mevduat göstergeleriyle aynı çeyrek
anahtarında birleştirilir. EVDS'de satırı bulunmayan 10 ipotekli satış
gözleminin sıfır olduğu, TÜİK'in aynı il ve ay için yayımladığı `toplam = diğer`
özdeşliğiyle doğrulanmıştır. Bunların 9'u yarışma dönemindedir ve 8 il-çeyrek
toplamını tamamlar. Ham EVDS null değerleri değiştirilmez, fallback kaynağı ve
SHA-256 izi ayrı sütunlarda tutulur. Çeyreklik değerler ara aylara kopyalanmaz.
Kaynakta yayımlanmayan 162 il-çeyrek konut birim fiyatı resmî sütunda null
kalır. İhtiyaç hâlinde yalnız aynı KFE bölgesi ve aynı çeyrekteki resmî il
fiyatlarının medyanından ayrı ve açık etiketli bir proxy üretilir. Türkiye
geneli, önceki dönem veya başka bölge fallback'i kullanılmaz. Resmî-kaynak
hazırlığı ile proxy izinli hazırlık ayrı bayraklarda tutulur.
Katalogdaki herhangi bir başka seri `tools/EVDS_Talep_Uzerine_Indirme_Araci.py`
ile adı veya kodu üzerinden bulunup ham istek, ham cevap ve SHA-256 iziyle
indirilebilir. Python HTTP taşımasını kullanan yeni kuyruğun gerçek komutları
[kullanım rehberindedir](docs/AGENT_READY_LAKEHOUSE.md#evds-toplama-kuyruğu).

TBB'nin Haziran 2026 çeyreklik tüketici kredileri raporu yayımlanmadığı için
parasal kullandırım tutarı null kalır. Ayrı kaynak ailesindeki Risk Merkezi
Haziran 2026 aylık bülteni mevcuttur ve bakiye, borçlu sayısı, ortalama risk,
tasfiye oranı ve ilk kez kullanan kişi sayısını sağlar. İlk kez kullanan kişi
sayısı parasal kullandırım değildir ve bu boşluğun yerine geçirilmez.

Agent'ın veri araçları uygulanmıştır. Canlı Qwen planlama başarısı, otomatik
PDF/URL ayrıştırma, tam EVDS kapsamı, istatistik araçlarının tamamı ve frontend
bu teslimin tamamlanmış özellikleri değildir. Mevcut deterministik API ve
sınırlar için [uygulama rehberini](docs/AGENT_READY_LAKEHOUSE.md), veri aileleri
için [veri pipeline rehberini](data_pipeline/README.md) okuyun.
