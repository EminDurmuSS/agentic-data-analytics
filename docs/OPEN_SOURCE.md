# Açık kaynak bileşenleri ve dış hizmetler

12 Eylül 2026 geliştirmeleri mevcut açık kaynak Python ve JavaScript bileşenleriyle uygulanır. Veri deposu, belge ayrıştırma, hesaplama, grafik ve uygulama sunucusu yerelde çalışır. Yeni zorunlu paket eklenmemiştir.

| İşlev | Bileşen | Kurulu paketteki lisans beyanı |
| --- | --- | --- |
| Lakehouse ve kalıcı tablolar | DuckDB, PyArrow | MIT, Apache-2.0 |
| Sayısal hesap | pandas, NumPy, SciPy, statsmodels | BSD ailesi |
| PDF metni ve tabloları | pdfplumber, pdfminer.six, pypdf | MIT, MIT, BSD-3-Clause |
| Excel ve görsel işleme | openpyxl, Pillow | MIT, MIT-CMU |
| HTTP uygulaması ve veri sözleşmesi | FastAPI, Uvicorn, jsonschema | MIT, BSD-3-Clause, MIT |
| Tarayıcı grafikleri | Apache ECharts 6.0.0 | Apache-2.0, LICENSE ve NOTICE depoda |

Tablo paketlerin kendi beyanlarını özetler; ikili dağıtımların beraberinde gelen ek lisansları kaldırmaz. Kurulu çalışma zamanı bağımlılık zincirindeki 65 paketin metaverisi, lisans dosyalarının hash'leri ve kaynak proje bağlantıları [envanterde](research/mentor-improvements-2026-09-12/dependency-licenses.json) tutulur. Örneğin pdfplumber'ın MIT metni [özgün deposunda](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt) da bulunur.

Envanteri kendi kurulumunuzda yeniden üretmek için:

```sh
python evals/dependency_licenses.py --output dependency-licenses.json
```

Kloudeks MIA, yarışmanın sağladığı model erişimidir. Açık ağırlıklı model erişimi, servis yazılımının lisansı ve yerel Python kütüphanelerinin lisansları ayrı konulardır. Bu envanter Kloudeks servisinin açık kaynak olduğu iddiasını taşımaz. Yarışmanın model kullanım koşulları ayrıca korunur.

Web araması için kendi açık kaynak SearXNG sunucunuz `SEARXNG_URL` ile seçilebilir. SearXNG'nin [arama API'sinde](https://docs.searxng.org/dev/search_api.html) JSON çıktı biçimi etkin olmalıdır. Örnek yerel yapılandırma:

```sh
SEARXNG_URL=http://127.0.0.1:8080 python -m app --prompt-key --port 8870
```

Bu adres uygulamayı başlatan kişinin yapılandırmasıdır; model bunu değiştiremez. Sabit `/search` yolu kullanılır, yanıt boyutu ve süre sınırlıdır, yönlendirmeler izlenmez. Bulunan belge URL'leri ayrıca mevcut genel ağ kontrollerinden geçer. Docker içinde adres, aynı ağdaki SearXNG servis adı ve portu olmalıdır. SearXNG yapılandırılmadığında mevcut Bing RSS yedeği kullanılır. Arama motorları ve kamuya açık rapor siteleri dış veri kaynaklarıdır; ulaşılabilirlikleri yerel yazılımın lisansıyla garanti edilmez.

Finansal raporların ve veri kaynaklarının kullanım koşulları kendi yayıncılarına aittir. Bu belgelerin indirilmesi onları uygulama kodunun lisansına dönüştürmez. Depoda üçüncü taraf ECharts'ın lisans ve atıf dosyaları korunur; proje kodu için ayrıca tüm depoyu kapsayan yeni bir lisans ataması bu çalışmada yapılmamıştır.
