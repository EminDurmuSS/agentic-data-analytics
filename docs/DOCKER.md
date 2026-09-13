# Docker ile çalıştırma

Linux'ta Docker Engine ve Compose eklentisi veya Linux konteynerleri çalıştıran Docker Desktop kullanılabilir. Docker Desktop zorunlu değildir. Komutları `docker-compose.yml` dosyasının bulunduğu repo kökünde çalıştırın; `docker version` ve `docker compose version` komutları erişilebilir olmalıdır.

İmaj, Python bağımlılıkları ile `app/` ve `agentic_analytics/` kodunu içerir. Finans veritabanı, ham veri, notebooklar ve çalışma kayıtları imaja eklenmez. Host üzerinde Python kurulumu, yalnız veritabanını kaynaklardan üretecekseniz gerekir.

## Yapılandırma

`.env` zorunlu değildir. Mevcut terminal ortamındaki `MIA_API_KEY` ve `SEARXNG_URL`, Compose üzerinden uygulamaya aktarılır. Dosya kullanmak isterseniz mevcut `.env` dosyanızın üzerine yazmadan örneği kopyalayın:

macOS/Linux:

```sh
cp -n .env.example .env
```

Windows PowerShell:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

`.env` dosyasını yerel editörde düzenleyin. Anahtarı arayüze, sohbet mesajına veya Git'e yazmayın. Ortam değişkenleri Docker'a erişebilen kullanıcılarca görülebilir; `docker compose config` çıktısı da çözümlenmiş değerleri içerir, bu çıktıyı paylaşmayın.

Terminalde tanımlı değişkenler, `.env` ve `--env-file` değerlerinden önceliklidir. Dosyadaki port veya anahtar değişikliği etkisiz görünüyorsa aynı değişkenin terminal ortamında tanımlı olup olmadığını kontrol edin. [Compose değişken önceliği](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

| Değişken | Anlamı |
| --- | --- |
| `MIA_API_KEY` | Canlı model kullanımı için anahtar. Boşken arayüz açılır, model işi başlatılamaz |
| `SEARXNG_URL` | İsteğe bağlı, JSON araması etkin SearXNG sunucusunun temel adresi. Docker içinde örneğin aynı ağdaki `http://searxng:8080`; boşken Bing RSS ve gerektiğinde tek DuckDuckGo Lite denemesi kullanılır. Modelin seçtiği belge URL'leri için ağ kontrolleri ayrı uygulanır |
| `AGENT_PORT` | Host portu; varsayılan `8870` |
| `LAKEHOUSE_DIR` | `analytics.duckdb` dosyasını içeren host dizini; varsayılan `./data_pipeline/lakehouse` |

Host üzerinde çalışan yerel uygulama `8870` portunu kullanıyorsa `.env` içinde `AGENT_PORT=8871` seçin. Tarayıcı adresi bu durumda `http://127.0.0.1:8871` olur. Windows'ta özel dizin için `LAKEHOUSE_DIR=C:/veri/lakehouse` gibi ileri eğik çizgili bir yol kullanılabilir. Seçilen dizin önceden bulunmalı ve Docker tarafından okunabilmelidir; içindeki veritabanı dosyası isteğe bağlıdır.

## Finans verisini hazırlama

`analytics.duckdb` bulunmasa da **Boş çalışma alanı** profili kullanılabilir. **KKB finans verileri** için aşağıdaki iki yoldan birini seçin:

1. [Geliştirme ortamını](DEVELOPMENT.md#ortamı-kurma) host üzerinde kurup `python data_pipeline/lakehouse/build_lakehouse.py` komutuyla klondaki seçilmiş kaynak paketinden veritabanı üretin.
2. Ekipten alınan doğrulanmış veritabanı yayınını bir host dizinine kopyalayıp `LAKEHOUSE_DIR` ile bu dizini seçin. Kaynak yayının hashini ve kapsamını teslim kaydıyla karşılaştırın; yazılmakta olan veritabanını kopyalamayın.

**Mevcut tam EVDS veritabanını temel build ile yeniden üretmeyin.** Temel build seçilmiş kaynak paketini kullanır. Tam yayın ve seçilmiş paket ayrımı [veri rehberinde](DATA.md), güncelleme akışı [geliştirme rehberinde](DEVELOPMENT.md#veriyi-hazırlama) açıklanır.

Compose, tek dosya yerine veritabanının dizinini `/app/data_pipeline/lakehouse` konumuna salt okunur bağlar. Böylece yayın değişimi ve `.wal` durumu görülebilir. Konteyner bu kaynak dizinine yazmaz; çalışma alanları kendi snapshot kopyalarını kalıcı kayıt deposunda tutar. Yeni kaynak yayını, eski çalışma alanlarının snapshot'ını değiştirmez.

Finans analizi ve kayıtlı kaynak hücresi açıklaması için bütün ham veri arşivini bağlamak gerekmez. Bu açıklama, veritabanındaki kaynak konumu ve hash bilgisini gösterir; ham dosyanın baytlarını ayrıca doğrulamaz. Bu akışta `source_files_verified` değeri `false` kalır.

## Başlatma ve günlük kullanım

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 agent-app
```

Varsayılan adres [http://127.0.0.1:8870](http://127.0.0.1:8870). `ps` çıktısındaki `healthy`, konteyner içinden `/api/status` yanıtının alındığını gösterir; model anahtarını, finans veri kapsamını veya analiz doğruluğunu sınamaz. İlk çalıştırma temel imajı ve bağımlılıkları indirebilir.

Uygulama konteyner içinde `0.0.0.0:8870` dinler. Compose portu host üzerinde `127.0.0.1:${AGENT_PORT:-8870}` adresine bağlar. Bu yapı yerel kullanım içindir; açık internete yayın ve kimlik doğrulama kurulumu içermez. Docker'ın port yayınlama davranışı için [resmî ağ rehberine](https://docs.docker.com/engine/network/port-publishing/) bakın.

```sh
docker compose restart agent-app
docker compose down
```

`restart`, aynı yapılandırmayla yeniden başlatır. `.env`, port veya Compose ayarı değiştiğinde `docker compose up -d` ile konteyneri yeniden oluşturun; kod değişikliğinde `--build` ekleyin. `down`, konteyner ve ağı kaldırır, adlandırılmış çalışma kaydı volume'unu korur. `down -v` bu kalıcı kayıtları da siler; rutin durdurma komutu değildir. [Docker Compose down](https://docs.docker.com/reference/cli/docker/compose/down/).

## Kalıcı kayıtlar ve izinler

| Konum | İçerik ve ömrü |
| --- | --- |
| Host'taki `LAKEHOUSE_DIR` | Kaynak finans veritabanı; salt okunur bind mount |
| `agent-runtime` adlı Compose volume'u | `/app/.lakehouse-runtime` altındaki snapshot, yüklenen dosya, analiz, grafik, konuşma ve iş kayıtları |
| Konteyner `/tmp` dizini | Geçici `tmpfs`; kalıcı yedek yeri değildir |

Gerçek volume adı Compose proje adıyla öneklenir. Aynı checkout ve proje adı yeniden kullanıldığında kayıtlar bulunur; `-p` veya `COMPOSE_PROJECT_NAME` değiştirmek ayrı volume oluşturur. Host'taki `.lakehouse-runtime/` dizini bağlanmaz, yerel Python uygulamasıyla Docker aynı çalışma deposuna yazmaz.

Uygulama UID/GID `10001:10001` ile çalışır. İmaj, yeni volume için yazılabilir kayıt dizinini bu kullanıcıya ait hazırlar. İçe aktarılan bir yedekte aynı sahiplik korunmalıdır. `Permission denied` durumunda önce kaynak dizininin okunabilirliğini ve kayıtların sahipliğini kontrol edin; uygulamayı kalıcı olarak root kullanıcısına geçirmeyin.

Kök dosya sistemi salt okunur, yazma alanları runtime volume'u ve `/tmp` ile sınırlıdır. Compose `init` ile süreç sonlandırmasını yönetir. Bu ayarların kaynağı [docker-compose.yml](../docker-compose.yml) ve [Dockerfile](../Dockerfile); alanların anlamı [Compose servis başvurusunda](https://docs.docker.com/reference/compose-file/services/) açıklanır. `.dockerignore` build bağlamından veri ve sırları dışlar; bağlamın imaja kopyalanmasından önceki rolü [Docker build rehberinde](https://docs.docker.com/build/concepts/context/) açıklanır.

## Yedekleme ve geri yükleme

Runtime yedeği, konuşma SQLite dosyasının yanında snapshot ve artifact dosyalarını da içermelidir. Aktif işler bittikten sonra uygulamayı durdurun; açık SQLite/WAL dosyalarını çalışırken ayrı ayrı kopyalamayın. Kaynak `analytics.duckdb` yayını ve yerel `.env` yapılandırması runtime volume'una dahil değildir; bunları ayrı koruyun. Kodun commit kimliğini ve kaynak yayının hash kaydını da saklayın; geri yüklemeye aynı kod sürümüyle başlayın.

Örnekteki `./runtime-backup` dizini henüz bulunmamalıdır; her yedeğe ayrı isim verin. Aşağıdaki komutlar macOS/Linux kabuğu ve PowerShell'de aynıdır:

```sh
docker compose stop agent-app
docker compose cp agent-app:/app/.lakehouse-runtime ./runtime-backup
docker compose start agent-app
```

Kopyalama tamamlanmadan servisi başlatmayın. Yedek dizininde `app/` altındaki konuşma kayıtlarını ve lakehouse snapshot/artifact ağacını birlikte kontrol edin. Dosya kopyası kişisel yüklemeleri ve konuşmaları içerir; Git'e eklemeyin, ayrı yedek ortamına taşıyın. [Compose cp](https://docs.docker.com/reference/cli/docker/compose/cp/) kullanımı volume'un Docker tarafından verilen fiziksel adını bilmenizi gerektirmez.

Geri yüklemeyi önce ayrı bir proje ve yeni volume üzerinde deneyin. Yerel editörde `.env.restore` oluşturup boş bir host portunu, örneğin `AGENT_PORT=8872`, ve gerekli `LAKEHOUSE_DIR` değerini yazın. Canlı model kullanacaksanız anahtarı da bu yerel yapılandırmada sağlayın. Aynı adlı `agent-restore` projesinin daha önce oluşturulmadığından emin olun:

```sh
docker compose --env-file .env.restore -p agent-restore create --build agent-app
docker compose --env-file .env.restore -p agent-restore cp ./runtime-backup/. agent-app:/app/.lakehouse-runtime/
docker compose --env-file .env.restore -p agent-restore run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint chown agent-app -R 10001:10001 /app/.lakehouse-runtime
docker compose --env-file .env.restore -p agent-restore up -d
docker compose --env-file .env.restore -p agent-restore ps
```

`cp` ile gelen dosyaların sahipliği hosta göre değişebilir. Tek seferlik yardımcı konteyner, kapalı izinli alt dizinlere erişmek için `DAC_OVERRIDE`, sahipliği düzeltmek için `CHOWN` kullanır. İşlem yalnız geri yüklenen volume'a uygulanır; asıl uygulama UID `10001` ve `cap_drop: ALL` ile başlar. Bu örnekte volume proje adına bağlıdır; Compose'a sabit `name:` veya `external:` eklenirse proje adını değiştirmek tek başına depoyu ayırmaz.

Geri yüklenen arayüzde önceki konuşmayı, bir tam analiz tablosunu ve kayıtlı grafiği açın. Yüklenen dosya ve kaynak açıklamasına erişimi kontrol edin; yalnız `healthy` durumunu yedek doğrulaması saymayın. Eski proje ve yedeği, geri yükleme kontrolü bitene kadar koruyun. Geri yüklenen ortamı yönetirken aynı `--env-file .env.restore -p agent-restore` seçeneklerini kullanın.

İmaj, veri kalıcılığı ve temiz volume'a geri yükleme kontrollerinin kapsamı ve sonuçları [10 Eylül 2026 Docker doğrulama raporunda](validation/docker-2026-09-10/README.md) kayıtlıdır. Bu kontroller canlı model doğruluğu ölçümü değildir.
