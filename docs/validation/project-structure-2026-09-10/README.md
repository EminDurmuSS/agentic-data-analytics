# Paket ve klasör düzeni doğrulaması

10 Eylül 2026. Başlangıç commit'i `70d688ac`. Uygulama kodu
`agentic_analytics/` altında agent, lakehouse ve sağlayıcı katmanlarına
ayrıldı. `app/` HTTP ve arayüz katmanı; `evals/` değerlendirme araçları;
`tools/` veri işletim komutları olarak düzenlendi. Testler sorumluluklarına
göre klasörlendi. [Dosya taşıma eşlemesi](moves.json) eski konumları gösterir.

Agent döngüsü, prompt, şema, bağlam hazırlama, grafik teslimi ve araç kayıtları
ayrı modüllerdedir. API oluşturma, çalışma alanı yönetimi, istek modelleri ve
route aileleri ayrıdır. Core paketin sunum katmanına veya veri hazırlama
betiklerine bağımlı olmasını engelleyen mimari testler bulunur.
Metrik politikaları build ile serving arasında paylaşılır.

| Kontrol | Sonuç |
| --- | --- |
| Bütün test paketi | 430 test ve 163 alt test geçti, 90,73 saniye |
| Son bağımlılık kontrolü düzenlemesinden sonra hedef testler | 4 test geçti |
| Eski/yeni katalog karşılaştırması | 55.501 metrik, 0 fark |
| HTTP yolları | 24 endpoint korundu |
| Agent araç kayıtları | 9 temel aracın şeması, sırası, handler'ı ve yazma bayrağı korundu |
| Prompt ve karar döngüsü | Taşıma öncesi içerik ve davranış karşılaştırması geçti |
| Gerçek veriyle yeni CLI | Init, 66 aylık hesap, revizyon ve kaynak izi geçti |
| CLI kaynak kontrolü | 3 ham dosya ve 6 kaynak hücresi bağımsız doğrulandı |
| Veri korunması | DuckDB boyutu, SHA-256 ve mtime değişmedi |
| Giriş noktaları | App, lakehouse CLI, evals ve etkilenen build komutlarının `--help` çağrıları geçti |
| Notebook | Taşınan keşif notebookunun başlangıç hücresi yeni dizininden çalıştı |
| Mevcut yerel servis | Status, çalışma alanı, analiz, grafik ve statik dosyalar HTTP 200 |

İki test uyarısı mevcut Starlette bağımlılıklarının kullanım dışı bırakılma
bildirimleridir. Yeni model çağrısı yapılmadı; bu çalışma yeni bir canlı
agent başarı oranı ölçümü değildir. Önceki test beklentileri korundu; yalnız
kaldırılan birebir notebook kopyasını karşılaştıran test çıkarıldı.

`data_pipeline/` altındaki ham ve işlenmiş kaynaklar taşınmadı. Veri dosyası
envanterinde yalnız değişen Python/README dosyalarının hashleri güncellendi;
taşınan registry ile kaldırılan notebook kopyasının eski kayıtları çıkarıldı.
Tarihli değerlendirme JSON'larının ve görsel kanıtların içeriği korunur.

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m app --help
.venv/bin/python -m agentic_analytics.lakehouse.cli --help
```

Özet ölçümler ve yerel kanıt dosyalarının SHA-256 değerleri
[results.json](results.json) içindedir. Tam CLI çıktıları ve test günlüğü
`tmp/project-structure/` altında yerel tutulur.
