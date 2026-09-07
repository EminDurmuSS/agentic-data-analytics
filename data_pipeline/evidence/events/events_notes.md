# Konut kredisi olay ve yöntem katmanı

Araştırma tarihi: 7 Eylül 2026. Örneklem: Ocak 2021-Haziran 2026.

`events.json` 4 kredi düzenlemesi ve 1 istatistik yöntemi kaydı içerir. Dört BDDK kararının da resmî tam metni indirildi. KFE yöntem değişikliği resmî TCMB duyurusu ve indirilen yöntem PDF'iyle doğrulandı. Belgelerin PDF ve çıkarılmış metin dosyaları SHA-256 özetleriyle `source_documents_manifest.json` içinde kayıtlıdır.

| Kayıt | Doğrulanan tarih | Doğrulanan temel olgu | Erişim |
|---|---|---|---|
| BDDK 10249 | Karar: 23.06.2022 | Konut değeri, enerji sınıfı ve birinci/ikinci el ayrımına göre kredi sınırları | BDDK resmî tam karar PDF'i; ayrıca TCMB Enflasyon Raporu 2022-III PDF s. 15 ve 26. |
| BDDK 10525 | Karar: 24.02.2023 | Konut kredisi ve konut teminatlı kredi oran/azami tutar kararı; Yeni Konut Finansmanı Programı istisnası | BDDK resmî tam karar PDF'i, iki sayfa. |
| BDDK 10656 | Karar: 24.08.2023 | Mevcut konut sahipliğinde 10525 oranlarının yüzde 75 azaltılması ve belirtilen istisnalar | BDDK resmî tam karar PDF'i; ayrıca TCMB Mayıs 2025 PDF s. 14 ve 2024-I Kutu 2.4 PDF s. 4. |
| KFE revizyonu | Duyuru: 15.08.2024; ilk yayın: 16.08.2024; referans: Temmuz 2024 | Geçmiş yeniden hesaplandı, aylık yöntem ve 2023 baz | TCMB resmî duyurusu tam metin; yöntem PDF s. 3 indirildi ve görsel kontrol edildi. |
| BDDK 11364 | Karar: 29.01.2026; kamuoyu duyurusu: 30.01.2026 | Tek kredi/değer oranı tablosu ve 10656 kuralının yeni oranlar üzerinden devamı | BDDK resmî tam karar PDF'i; basın açıklaması bağlantıları ayrıca korunur. |

## Kullanım

- Aylık tabloyla yalnızca `monthly_annotation_key` üzerinden açıklama eklemek için eşleştirin. Aynı ayda gerçekleşen bütün sayısal değişimleri olaya bağlamayın.
- `decision_date` karar günüdür. Ayrı yürürlük günü doğrulanamadığında `effective_date=null` tutuldu. Duyuru tarihi yürürlük tarihi gibi sunulmadı.
- KFE kaydı ekonomik şok değildir. Revizyon bütün geçmişe uygulanmıştır; eski ve yeni yayın seviyelerini birleştirmeyin. Bu olay modelde otomatik faiz/kredi politika kuklası olarak kullanılmamalı.
- Kredi stokundaki değişim, o ay verilen yeni kredi miktarı veya hane talebi değildir. Geri ödemeler ve stokun kapsamı da değişimi etkiler. Olay katmanı, ipotekli satışlar ve haftalık akım kredi faizi ile birlikte alternatif açıklamalar üretmeye yarar; tek başına nedensellik belirlemez.
- 2023 azaltımının daha sonraki uygulama istisnaları bu küçük veri setinde bütünüyle kodlanmadı. Bu nedenle bu dosya kredi uygunluğu ya da güncel azami kredi hesaplama motoru olarak kullanılmamalı.
- Beş kaydın tamamı birincil veya resmî destekleyici belgeyle tam metin düzeyinde doğrulandı. Yine de `effective_date`, belgede karar tarihinden ayrı bir yürürlük tarihi belirtilmedikçe boş bırakıldı.

## Resmî kaynaklar

- [BDDK 10249](https://www.bddk.org.tr/Mevzuat/DokumanGetir/1126)
- [TCMB Enflasyon Raporu 2022-III](https://tcmb.gov.tr/wps/wcm/connect/5d6d752d-ef72-4d2a-afd6-a132c2f6d7b9/enftemmuz22_iii_tam.pdf?MOD=AJPERES)
- [BDDK 10525](https://www.bddk.org.tr/Mevzuat/DokumanGetir/1164)
- [BDDK 10656](https://www.bddk.org.tr/Mevzuat/DokumanGetir/1191)
- [TCMB Mayıs 2025 Finansal İstikrar Raporu](https://www.tcmb.gov.tr/wps/wcm/connect/72d745d3-f8b1-404d-bc65-04b9469084f3/Tam%2BMetin.pdf?MOD=AJPERES)
- [TCMB 2024-I Kutu 2.4](https://www.tcmb.gov.tr/wps/wcm/connect/ee67cf46-654c-449a-8acc-d8bbcca93f45/Kutu_2_4_2024_i.pdf?MOD=AJPERES)
- [TCMB KFE revizyon duyurusu 2024-44](https://tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Main%2BMenu/Duyurular/Basin/2024/DUY2024-44)
- [KFE uygulama değişiklikleri](https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES)
- [BDDK 11364](https://www.bddk.org.tr/Mevzuat/DokumanGetir/1327)
- [BDDK 30 Ocak 2026 basın açıklaması](https://www.bddk.org.tr/Duyuru/EkGetir/2157?ekId=889)
- [BDDK 31 Ocak 2026 açıklaması](https://www.bddk.org.tr/Duyuru/EkGetir/2158?ekId=890)

`download_log*.json` ilk erişim denemelerini, `source_documents_manifest.json` ise son durumda indirilen sekiz resmî PDF'nin boyut, sayfa sayısı, PDF SHA-256 ve çıkarılmış metin SHA-256 değerlerini içerir. Dört BDDK kararı ve dört TCMB destek belgesi yerel olarak saklanır. İndirmelerde kimlik bilgisi kullanılmadı ve TLS doğrulaması kapatılmadı.
