# BDDK veri katmanı

Bu klasör BDDK aylık bülten, haftalık bülten ve FinTürk verilerinin doğrudan
indirilmiş ham kaynaklarını, metadata kayıtlarını ve doğrulanmış Parquet/CSV
çıktılarını içerir.

## Aylık bülten

- Ocak 2021-Haziran 2026
- 66 ay
- 17 tablonun tamamı
- Sektör grubu, kod `10001`
- 1.122 / 1.122 başarılı istek
- 33.965 kaynak satırı
- 133.485 semantik ölçüm

Kâr-zarar gibi yılbaşından bugüne kümülatif kaynak tabloları ayrı bir semantik
katmanda aylık akıma çevrilir. Kaynak YTD değeri korunur ve türetilmiş akımların
yeniden toplamı kaynağa karşı kontrol edilir.

## Haftalık bülten

- Ocak 2021-Haziran 2026
- 286 hafta
- 9 tablonun tamamı
- Sektör grubu, kod `10001`
- 2.574 / 2.574 doğrulanmış kaynak sayfası
- 249.070 ham hücre
- 147.154 normalize ölçüm

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

Aylık ve haftalık bültenlerin bütün tabloları sektör toplamı için alınmıştır.
FinTürk'te tüm banka grupları ve coğrafyalar bulunur. Sektör toplamı dışındaki
aylık ve haftalık banka grup kırılımlarını da toplamak teknik olarak mümkündür,
fakat mevcut nedensellik senaryosu için FinTürk ve EVDS banka grubu serileri bu
kırılım ihtiyacını karşılar. Gereksiz tekrar veri hacmi bilinçli olarak
eklenmemiştir.

## 502 erişim sorununun sonucu

Önceki çalışma ortamındaki 502 yanıtı, aradaki bağlantı katmanının upstream
sertifika doğrulama hatasıydı. Aynı resmî BDDK adresleri bu bilgisayardan TLS
doğrulaması açıkken başarıyla indirildi. Hem bülten verileri hem de dört konut
kredisi karar PDF'i artık yerel kaynak ve hash kaydıyla mevcuttur.
