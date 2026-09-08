# BDDK resmî veri indirme protokolü

Doğrulama tarihi: 8 Eylül 2026

Bu belge, BDDK'nın herkese açık aylık ve haftalık bülten arayüzlerinin
tarayıcı ağ akışından doğrulanan istek sözleşmesini açıklar. Kimlik bilgisi,
özel cookie veya TLS doğrulamasını kapatan bir yöntem kullanılmaz.

## Aylık bülten

- Başlangıç sayfası: `https://www.bddk.org.tr/BultenAylik/tr/`
- Veri isteği: `POST /BultenAylik/tr/Home/BasitRaporGetir`
- Temel form alanları: `tabloNo`, `yil`, `ay`, `paraBirimi`
- Banka grubu alanı: tekrarlanabilir `taraf`
- Yanıt biçimi: JSON içinde jqGrid kolon modeli ve kaynak satırları

İndirici, tek bir dönem ve tablo isteğinde aşağıdaki 10 resmî grubu birlikte
isteyebilir:

| Kod | Grup |
| ---: | --- |
| 10001 | Sektör |
| 10002 | Mevduat |
| 10003 | Katılım |
| 10004 | Kalkınma ve Yatırım |
| 10005 | Yerli Özel |
| 10006 | Kamu |
| 10007 | Yabancı |
| 10008 | Mevduat-Yerli Özel |
| 10009 | Mevduat-Kamu |
| 10010 | Mevduat-Yabancı |

Bir cevap ancak HTTP isteği başarılıysa, dış JSON alanı `success=true` ise,
kolon modeli ile hücre sayıları eşleşiyorsa ve dönen `BankaAdi` değerleri
istenen grup kümesiyle birebir aynıysa doğrulanmış kabul edilir. Ham cevap ve
istek bilgisi ayrı dosyalarda SHA-256 özetiyle saklanır.

Uygulama: `tools/BDDK_Indirme_Araci.py`

## Haftalık bülten

- Başlangıç sayfası: `https://www.bddk.org.tr/BultenHaftalik/`
- Grup seçimi: `POST /BultenHaftalik/tr/Home/TarafSec`
- Tablo seçimi: `POST /BultenHaftalik/`
- Dönem seçimi: `POST /BultenHaftalik/tr/Home/DonemDegistir`
- Kaynak tablo: HTML içindeki `table#Tablo`

Haftalık uygulama oturum tabanlıdır. İndirici tarayıcıyla aynı sırayı izler:

1. Başlangıç sayfasını TLS doğrulaması açıkken alır.
2. ASP.NET oturum cookie'sini aynı oturum içinde korur.
3. Her formun kendi `__RequestVerificationToken` değerini ilgili HTML'den
   okur.
4. Banka grubunu, tabloyu ve dönemi sırayla seçer.
5. Dönen sayfada tarih, tablo başlığı ve banka grubu başlığını beklenen
   seçimlerle karşılaştırır.
6. Kaynak HTML'i gzip olarak, metadata ve SHA-256 kaydını JSON olarak saklar.

Haftalık arayüzde doğrulanan 7 resmî grup şunlardır:

| Kod | Grup |
| ---: | --- |
| 10001 | Sektör |
| 10002 | Mevduat |
| 10003 | Kalkınma ve Yatırım |
| 10004 | Katılım |
| 10005 | Kamu |
| 10006 | Yabancı |
| 10007 | Yerli Özel |

Bir sayfa ancak seçilen tarih, tablo ve grup HTML içinde yeniden doğrulanırsa
kabul edilir. Geçici ağ ve oturum hataları sınırlı sayıda yeniden denenir.
Doğrulanamayan cevap veri setine eklenmez ve süreç başarısız olarak durur.

Uygulamalar:

- `tools/BDDK_Haftalik_Indirme_Araci.py`
- `tools/merge_bddk_weekly_snapshots.py`
- `data_pipeline/bddk/build_weekly_dataset.py`

## Veri bütünlüğü ilkeleri

- TLS sertifika doğrulaması kapatılmaz.
- Kaynak boş hücreler sıfıra çevrilmez.
- Ham değer, ham dosya ve kaynak hash'i korunur.
- İşlenmiş Parquet üretiminde ham dosyalar yeniden ayrıştırılır ve hash'ler
  yeniden kontrol edilir.
- Dönem, tablo ve grup kombinasyonlarının eksiksizliği işleme başlamadan önce
  doğrulanır.
- Paralel indirilen parçalar yalnız doğrulanmış ham dosya ile metadata çifti
  aynı SHA-256 değerini taşıyorsa ana snapshot'a alınır.
