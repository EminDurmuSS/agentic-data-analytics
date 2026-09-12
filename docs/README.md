# Dokümantasyon

Uygulamayı başlatmak için [ana README](../README.md), kod üzerinde çalışmak için [geliştirme rehberi](DEVELOPMENT.md) ile başlayın.

## Güncel rehberler

| Belge | İçerik |
| --- | --- |
| [Mimari](ARCHITECTURE.md) | Katmanlar, bağımlılık yönü, agent akışı ve kayıt modeli |
| [Geliştirme](DEVELOPMENT.md) | Kurulum, yerel uygulama, deterministik CLI, testler ve ölçümler |
| [Docker](DOCKER.md) | Konteyner kurulumu, veri bağlama, kalıcı kayıtlar ve yedekleme |
| [Açık kaynak bileşenleri](OPEN_SOURCE.md) | Lisans envanteri, yerel SearXNG ve yarışmanın model hizmeti ayrımı |
| [Mentör geliştirme planı](MENTOR_READINESS_PLAN.md) | Yeni kaynak, finansal doğruluk, görev teslimi ve gerçek kaynak kabul ölçütleri |
| [Veri kapsamı](DATA.md) | Kaynak aileleri, seçilmiş paket ile tam yerel yayın ayrımı ve veri sınırları |
| [Lakehouse araçları](AGENT_READY_LAKEHOUSE.md) | Metrik keşfi, hesap planı, revizyon ve kaynak hücresi örnekleri |
| [Notebook rehberi](LAKEHOUSE_VERI_KESFI_SADE_ANLATIM.md) | Keşif çıktılarının ve analiz sınırlarının açıklaması |
| [Veri pipeline rehberi](../data_pipeline/README.md) | Kaynaklardan işlenmiş katmanları ve veritabanını üretme |
| [Kaynak kayıtları](SOURCE_IMPORTS.md) | İçe aktarılan dosyalar ve kaynak kökeni |
| [BDDK indirme protokolü](BDDK_DOWNLOAD_PROTOCOL.md) | BDDK indirme ve doğrulama kuralları |

## Tarihli araştırma ve doğrulama kayıtları

Bu kayıtlar, ilgili tarihte ve kaydedilen commit üzerinde yapılan çalışmaları anlatır. [9 Eylül durum raporu](CURRENT_STATE.md) gibi eski envanterler, daha sonraki veri yayınını temsil etmez.

- [Docker doğrulaması, 10 Eylül 2026](validation/docker-2026-09-10/README.md): imajlar, veri kalıcılığı ve temiz volume'a geri yükleme.
- [Paket ve klasör düzeni doğrulaması, 10 Eylül 2026](validation/project-structure-2026-09-10/README.md): mimari sınırlar, 430 test ve 163 alt test, veri ve davranış korunması.
- [Agent tutarlılık başlangıç ölçümü, 10 Eylül 2026](validation/agent-consistency-2026-09-10/README.md): 18 bağımsız deneme, doğru çıktı ile tam görev başarısının ayrımı.
- [Etkileşimli grafik doğrulaması, 10 Eylül 2026](validation/interactive-charts-2026-09-10/README.md): grafik sözleşmeleri, takip istekleri ve tarayıcı kontrolleri.
- [Hesap adımları arayüzü](validation/analysis-steps-ui-2026-09-10/README.md): okunabilir yöntem açıklaması ve ekran kontrolleri.
- [Agent uygulaması ve ilk canlı kontroller](research/agent-harness-2026-09-10/live-validation.md): erken denemeler, başarısızlıklar ve kapsam sınırları.
- [MIA sağlayıcı deneyleri](research/mia-probe-2026-09-10/README.md): sağlayıcı kabiliyetlerinin ayrı sınanması.
- [EVDS tamamlama kaydı](research/evds-completion-2026-09-10/implementation.md): toplama, yayın ve kaynak hücresi denetimleri.
- [Lakehouse kabul sonuçları](validation/agent_lakehouse_2026-09-10.json) ve [kayıtlı benchmark](validation/lakehouse-benchmark/results.json): deterministik kontrollerin tarihli çıktıları.

## Dosya düzeni değişikliği

Uygulama kodu `tools/` içinden `agentic_analytics/` paketine, değerlendirme betikleri `evals/` içine taşındı. HTTP katmanı `app/` içinde ayrıştırıldı. [Mimari rehberi](ARCHITECTURE.md) yeni sorumlulukları gösterir.

| Önceki giriş veya konum | Güncel kullanım |
| --- | --- |
| `python -m tools.run_agent_app` | `python -m app` |
| `python -m tools.lakehouse_cli` | `python -m agentic_analytics.lakehouse.cli` |
| `tools/agent_runtime.py`, `tools/agent_run_store.py` | `agentic_analytics/agent/runtime.py`, `agentic_analytics/agent/run_store.py` |
| `tools/agent_documents.py`, `tools/agent_statistics.py`, `tools/agent_charts.py` | `agentic_analytics/agent/tools/` |
| `tools/lakehouse_*.py` | `agentic_analytics/lakehouse/` içindeki servis, depo, analiz, kalite ve CLI modülleri |
| `data_pipeline/lakehouse/registry.py` | `agentic_analytics/lakehouse/registry.py`; ortak politikalar `semantics.py` içinde |
| `tools/mia_client.py` | `agentic_analytics/providers/mia.py` |
| `python -m tools.evaluate_agent_consistency` | `python -m evals.consistency` |
| `python -m tools.grade_agent_consistency` | `python -m evals.grading` |
| `python test_lakehouse_performance.py` | `python -m evals.benchmark` |
| Kök dizindeki `lakehouse_veri_kesfi_ve_iliskiler.ipynb` | `notebooks/lakehouse_veri_kesfi_ve_iliskiler.ipynb` |
| `data_pipeline/KKB_Verileri_Dogrulanmis.ipynb` kopyası | `notebooks/KKB_Verileri_Dogrulanmis.ipynb` |
| `lakehouse_benchmark_results.json` | Tarihli kayıt `docs/validation/lakehouse-benchmark/results.json`; yeni ölçüm varsayılanı `tmp/lakehouse-benchmark/results.json` |

Eski modül adları yeni çalıştırma girişleri değildir. Rehberlerdeki çalıştırılabilir komutlar ve kod bağlantıları yeni konumlara yönlendirilir. Tarihsel denetimlerdeki eski dosya/satır atıfları ise incelenen commit bağlamında okunmalıdır.

Tarihli `results.json` dosyaları, kaynak hashleri, ekran görüntüleri ve diğer kanıtlar yeniden üretilmedi. İçlerindeki eski dosya adları ve backend SHA-256 değerleri o denemenin koduna aittir; bugünkü dosya yapısının doğrulaması gibi sunulmaz. Büyük yerel `tmp/` kayıtları, repo dışında tutulan yarışma kaynakları veya aktif veritabanı Git'e dahil olmayabilir. Raporun kendi kayıt ve tekrar çalıştırma bölümünü kontrol edin.
