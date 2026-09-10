# Docker doğrulaması, 10 Eylül 2026

PR #3, güncel `main` mimarisine uyarlanarak incelendi. Başlangıç PR commit'i `c6e331b5e0ade7c65885c9a401678a3993b6ca6f`, karşılaştırılan `main` commit'i `e0f1e6d0c4f533522068695fe34a0059dd243e75`. Sonuç ve dosya hashleri [results.json](results.json) içinde kayıtlıdır.

## Düzeltilen sorunlar

| Bulgu | Son davranış |
| --- | --- |
| Dockerfile taşınmış `tools.run_agent_app` ve registry dosyalarını kullanıyordu | İmaj `app/` ve `agentic_analytics/` paketlerini içerir, `python -m app` ile başlar |
| Büyük veri ve yerel dosyalar build bağlamına girebiliyordu | `.dockerignore` yalnız sunucu kodu, kilitli bağımlılıklar ve Dockerfile'a izin verir |
| Zorunlu `.env`, anahtarsız kurulumu durduruyordu | Yapılandırma isteğe bağlıdır; anahtarsız arayüz ve boş çalışma alanı açılır |
| Port bütün host arayüzlerinde yayımlanıyordu | Yalnız `127.0.0.1`, değiştirilebilir host portu kullanılır |
| Runtime bind mount yerel uygulamanın kayıtlarıyla çakışıyordu | Projeye ait named volume, UID/GID `10001:10001` ile kalıcı kayıt tutar |
| Exception traceback'i sağlayıcı veya belge metnini loglara taşıyabilirdi | Sadece iş kimliği, hata türü/kodu ve kod konumları loglanır |
| İlk restore komutu kapalı izinli dizinlerde hata verdi | Yalnız tek seferlik sahiplik düzeltme yardımcısı `CHOWN` ve `DAC_OVERRIDE` kullanır; asıl servis yetkileri değişmez |

## Çalıştırılan kontroller

| Kontrol | Sonuç |
| --- | --- |
| Host uygulama ve mimari testleri | 37 test, 11 subtest geçti |
| Linux imajında hata loglama testleri | 6 test geçti; yukarıdaki 37 testin alt kümesidir |
| Linux arm64 ve amd64 build | İkisi de geçti; Python 3.12.14 ve sabitlenmiş temel imaj |
| Her iki mimaride paketler ve native kütüphaneler | `pip check`, PDF oluşturma/okuma/raster, DuckDB, Parquet ve büyük tam sayı kontrolleri geçti |
| Anahtarsız, veritabanı bağlanmamış imaj | Arayüz ve boş profil çalıştı; finans profili 409, model işi 503 döndü |
| Gerçek finans veritabanıyla Compose | Ocak 2021-Haziran 2026 için 66 aylık KOBİ analizi, revizyon, grafik, kaynak açıklaması ve CSV geçti |
| CSV yükleme ve yayınlama | `9007199254740993` değeri tablo, kaynak açıklaması ve CSV'de tam korundu |
| Yeniden başlatma ve `down/up` | 13 API yanıtının hashleri aynı kaldı |
| Soğuk yedek ve yeni volume'a geri yükleme | Ayrı projede aynı 13 yanıt ve kaynak dosya baytları korundu |
| Kaynak veritabanı | Test öncesi ve sonrası SHA-256 aynı |

Testler macOS üzerindeki Docker Engine 29.2.1 / Compose 5.1.2 ile yapıldı. Ana test mimarisi Linux arm64'tür; Linux amd64 imajı emülasyonla çalıştırıldı. Fiziksel Windows/Intel makine testi yapılmadı. Mevcut bağımlılıkların TestClient deprecation uyarıları ve amd64 emülasyonundaki jemalloc uyarısı testleri başarısız kılmadı.

Canlı model veya dış web araması çağrılmadı. Bu kayıt Docker kurulumu, veri işlemleri ve kalıcılığı doğrular; model yanıt kalitesi değerlendirmesi değildir. Finans kaynak açıklaması veritabanındaki kayıtlı konum/hash bilgisini kullanır; `source_files_verified=false` durumunu korur.

## Tekrar çalıştırma

Normal kullanım için [Docker kurulum rehberini](../../DOCKER.md) izleyin. Aşağıdaki komutlar ise ayrı, geçici test projeleri içindir. Kaynak `analytics.duckdb` hazır olmalı ve `18870`, `18871` portları boş olmalıdır. Python komutlarını [geliştirme ortamında](../../DEVELOPMENT.md#ortamı-kurma), repo kökünden çalıştırın.

```sh
python -m pytest tests/app tests/architecture -q
docker compose -p codex-docker-pr3 build agent-app
python -m evals.docker_smoke
```

[Entegrasyon sürücüsü](../../../evals/docker_smoke.py) kendi model/arama anahtarlarını boşaltır, yalnız `codex-docker-pr3` ve `codex-docker-pr3-restore` projelerini yönetir. Ana test projesini yeniden başlatır; restore projesinin geçici volume'unu sıfırlayıp yedeği yükler. İki servisi inceleme için ayakta bırakır. Sonuç varsayılan olarak Git dışında tutulan `tmp/docker-pr3/smoke-report.json` dosyasına yazılır.

Native kütüphane ve verisiz açılış kontrolü:

```sh
docker run --rm -i --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 codex-docker-pr3-agent-app python - < evals/docker_runtime_probe.py
```

Bu stdin örneği macOS/Linux kabuğu içindir. Diğer mimari için imajı `docker build --platform linux/amd64 -t codex-docker-pr3-amd64 .` ile oluşturup aynı kontrolü `--platform linux/amd64` ve bu imaj adıyla çalıştırın.

Test kanıtlarını kaydettikten sonra yalnız geçici projeleri ve kayıtlarını kaldırın:

```sh
docker compose -p codex-docker-pr3-restore down --volumes
docker compose -p codex-docker-pr3 down --volumes
```

Kaynak veritabanı ve yerel Python uygulamasının runtime dizini bu projelerin volume'larına dahil değildir. Ham build logları ve test yedeği yereldir; depoya yalnız küçük doğrulama raporu eklenmiştir.
