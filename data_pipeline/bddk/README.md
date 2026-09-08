# BDDK veri katmanı

Bu klasör BDDK aylık bülten, haftalık bülten ve FinTürk verilerinin doğrudan
indirilmiş ham kaynaklarını, metadata kayıtlarını ve doğrulanmış Parquet/CSV
çıktılarını içerir.

## Aylık bülten

- Ocak 2021-Haziran 2026
- 66 ay
- 17 tablonun tamamı
- 10 resmî banka grubunun tamamı
- 1.122 / 1.122 başarılı birleşik istek
- 11.220 tablo-grup kaydı
- 339.650 kaynak satırı
- 1.334.850 semantik ölçüm

Kâr-zarar gibi yılbaşından bugüne kümülatif kaynak tabloları ayrı bir semantik
katmanda aylık akıma çevrilir. Kaynak YTD değeri korunur ve türetilmiş akımların
yeniden toplamı kaynağa karşı kontrol edilir.

## Haftalık bülten

- Ocak 2021-Haziran 2026
- 286 hafta
- 9 tablonun tamamı
- 7 resmî banka grubunun tamamı
- 18.018 / 18.018 doğrulanmış kaynak sayfası
- 1.736.650 ham hücre
- 1.025.974 normalize ölçüm
- 2.230 kaynak boşluğunun tamamı yapısal `FX uygulanamaz`, çözümlenmemiş boşluk 0

Her HTML sayfası gzip olarak saklanır. Yanında tarih, dönem kimliği, tablo,
grup, kaynak şeması ve SHA-256 içeren bir bilgi dosyası bulunur. İşlenmiş veri
üretilirken bütün sayfalar yeniden ayrıştırılır ve hash ile doğrulanır.

## FinTürk

- 22 çeyrek
- 7 tablo
- 7 banka grubu
- 81 il ve ayrı `YURT DIŞI` bölgesi
- 84.484 kaynak satırı
- 936.512 ölçüm

FinTürk değerleri çeyreklik dönem sonu gözlemleridir. Aylık veri veya yeni kredi
kullandırım akımı gibi sunulmaz.

## Yeniden üretim

Repo kökünden:

```bash
.venv/bin/python data_pipeline/bddk/build_monthly_all_dataset.py
.venv/bin/python data_pipeline/bddk/build_monthly_semantic_dataset.py
.venv/bin/python data_pipeline/bddk/build_finturk_dataset.py
.venv/bin/python data_pipeline/bddk/build_weekly_dataset.py
```

Canlı yeniden indirme araçları:

```bash
.venv/bin/python tools/BDDK_Indirme_Araci.py --help
.venv/bin/python tools/BDDK_Haftalik_Indirme_Araci.py --help
.venv/bin/python tools/BDDK_FinTurk_Indirme_Araci.py --help
```

Araçlar mevcut doğrulanmış dosyaları hash kontrolünden geçirerek önbellekten
devam eder. TLS doğrulaması kapatılmaz. Hatalı veya eksik HTTP cevabı veri
olarak kabul edilmez.

## Önemli kapsam notu

Aylık bültenin 10, haftalık bültenin 7 resmî banka grubu alınır. FinTürk'te de
tüm banka grupları ve coğrafyalar bulunur. Tarayıcıdan doğrulanan istek
sözleşmesi ve kaynak doğrulama adımları `docs/BDDK_DOWNLOAD_PROTOCOL.md`
dosyasında açıklanır.

## 502 erişim sorununun sonucu

Önceki çalışma ortamındaki 502 yanıtı, aradaki bağlantı katmanının upstream
sertifika doğrulama hatasıydı. Aynı resmî BDDK adresleri bu bilgisayardan TLS
doğrulaması açıkken başarıyla indirildi. Hem bülten verileri hem de dört konut
kredisi karar PDF'i artık yerel kaynak ve hash kaydıyla mevcuttur.
