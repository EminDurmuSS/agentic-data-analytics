# Agentic Data Analytics

KKB Agentic Data Analytics Hackathon 2026 kapsamında geliştirilen, yerel verileri Türkçe doğal dille incelemek için kaynak izini koruyan bir analiz çalışma alanı. FastAPI tabanlı HTTP API, tarayıcı arayüzü, tek agent döngüsü ve DuckDB lakehouse; soruyu hesap planına, kayıtlı tabloya ve etkileşimli grafiğe dönüştürür.

BDDK, TCMB EVDS, TÜİK ve TBB kaynaklarıyla finans analizi yapılabilir. Boş çalışma alanında kendi dosyalarınız da aynı veri sözleşmeleriyle kullanılabilir.

## Neler yapar?

- Metrik, dönem, birim ve kurum kapsamını keşfeder; büyüme, fark, oran ve uygun parasal serilerde sabit fiyat hesabı yapar.
- Takip sorularıyla önceki analizi sürdürür; değişmeyen sütunları ve önceki analiz kayıtlarını korur.
- Kayıtlı tablonun tamamından çizgi, çubuk, alan, dağılım ve gruplu ısı haritası üretir. Görünüm değişiklikleri veriyi değiştirmez; CSV, PNG ve SVG dışa aktarımı sunar.
- CSV, XLSX, PDF, görsel, HTML ve metin kaynaklarını inceler. Seçilen tablo, açık sütun ve birim sözleşmesiyle hesaplara açılır; görselden çıkarılan hücreler inceleme gerektirir.
- Kaynak hücresine kadar açıklama, web kaynak araştırması, anomali taraması, değişim tespiti ve ilişki incelemesi sağlar.

## Başlatma

Repo kökünde Python 3.12 ve `uv` ile:

```sh
uv venv --python python3.12 .venv
source .venv/bin/activate
uv pip install -r requirements-app.lock
```

Temiz klonda finans çalışma alanını kullanmak için yerel veritabanını mevcut kaynaklardan üretin:

```sh
python data_pipeline/lakehouse/build_lakehouse.py
```

Veritabanınız hazırsa veya yalnızca kendi dosyalarınızla boş çalışma alanı kullanacaksanız bu adımı atlayın. Tam EVDS yayınını kullanan mevcut veritabanını temel build ile yeniden üretmeyin; yayın akışı [geliştirme rehberinde](docs/DEVELOPMENT.md#veriyi-hazırlama) açıklanır.

```sh
python -m app --prompt-key --port 8870
```

[Yerel uygulamayı açın](http://127.0.0.1:8870). Anahtar terminalde gizli olarak sorulur; sunucuda `MIA_API_KEY` tanımlıysa mevcut değer kullanılır. Uygulama yerel loopback adresinde çalışır. Kayıtlar varsayılan olarak `.lakehouse-runtime/app/` altında tutulur.

Örnek başlangıç sorusu: “Bankacılık sektörünün 2026 ilk üç aydaki aylık net kârını milyon TL olarak göster.” Sonucu **Tablo**, **Grafik**, **Kaynaklar** ve **Hesap adımları** görünümlerinde inceleyebilirsiniz.

## Proje düzeni

| Dizin | Sorumluluk |
| --- | --- |
| [app/](app/) | HTTP API, uygulama kurulumu ve tarayıcı arayüzü |
| [agentic_analytics/](agentic_analytics/) | Agent, deterministik lakehouse servisleri ve sağlayıcı adaptörü |
| [data_pipeline/](data_pipeline/) | Kaynak veriler, dönüşümler, katalog ve veritabanı üretimi |
| [tools/](tools/) | Kaynak indirme, toplama, yayın ve doğrulama betikleri |
| [evals/](evals/) | Canlı tutarlılık ölçümü, bağımsız puanlama ve sorgu benchmarkı |
| [tests/](tests/) | Agent, API, lakehouse, veri alımı ve değerlendirme testleri |
| [notebooks/](notebooks/) | Veri keşfi ve doğrulama notebookları |
| [docs/](docs/README.md) | Kullanım, mimari, veri kapsamı ve tarihli kanıtlar |

## Kapsam ve doğrulama

Son [paket ve klasör düzeni doğrulamasında](docs/validation/project-structure-2026-09-10/README.md) **430 test ve 163 alt test** geçti; veri ve mevcut davranış kontrolleri korundu. Bu çalışma yeni bir canlı model başarısı ölçümü değildir.

Sayısal hesapların doğrulanması, doğal dildeki bütün yorumların doğru olduğunu garanti etmez. Önceki [canlı tutarlılık başlangıç ölçümü](docs/validation/agent-consistency-2026-09-10/README.md), doğru tablo üretimi ile tam görev başarısını ayrı değerlendirir. [Grafik doğrulaması](docs/validation/interactive-charts-2026-09-10/README.md) ise grafik akışını ölçer. Tarihli sonuçlar genel başarı oranı olarak yorumlanmamalıdır.

Arama sonucu kaynak doğrulaması değildir; ilgili belge ayrıca açılmalıdır. Kaynak referanslarının bulunması ile ham dosyaların ayrıca doğrulanması farklı kontrollerdir. Korelasyon, Granger testi ve dağılım grafikleri nedensel etki kanıtı üretmez. Eksik veya anlamı incelenmemiş veriler için uygulanabilecek işlemler sınırlıdır.

Kurulum, test ve CLI için [geliştirme rehberi](docs/DEVELOPMENT.md); kodun sorumlulukları için [mimari](docs/ARCHITECTURE.md); kaynak kapsamı için [veri rehberi](docs/DATA.md).
