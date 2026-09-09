# Kalan veri boşlukları için derin araştırma

Hedef kitle: KKB Agentic Data Analytics Hackathon geliştirme ekibi  
Araştırma ve canlı kontrol tarihi: 9 Eylül 2026
Coğrafya: Türkiye  
Ana dönem: Ocak 2021-Haziran 2026

## Yönetici özeti

Kalan dört veri konusu ve bunları güvenli biçimde tamamlayabilecek ayrı resmî
kaynak aileleri, yerel ham dosyalar ve tüm EVDS metadata kataloğu üzerinden
yeniden incelendi. Sonuç şöyledir:

| Konu | Karar | Sonuç |
| --- | --- | --- |
| FinTürk şube sayısı kaynak null değerleri | Kesin kimlikle ayrı analitik değer üret | 1.328 / 1.328 hücre için `usable_value=0`, ham `value=null` korunur |
| TBB Haziran 2026 raporu | Bekle ve periyodik yeniden kontrol et | Resmî kaynakta henüz yayımlanmamış, tahmin edilemez |
| TBB Risk Merkezi aylık bülteni | Ayrı metrik ailesi olarak ekle | Haziran 2026 dahil 66 aylık bakiye ve kişi göstergeleri var, parasal kullandırım yok |
| İl bazlı konut birim fiyatı | Resmî alanı doldurma, ayrı proxy üret | 162 il-çeyrek değer kaynakta yok; aynı bölge ve çeyrekteki resmî il fiyatı medyanı ayrı ve denetlenebilir proxy olarak eklendi |
| Haziran 2026 serbest piyasa altını | Kaynak seriyi doldurma | Aktif BIST altın serisi ayrı proxy ve kontrol olarak kullanılabilir |

Bu araştırmada gerçek anlamda kapatılabilen kaynak boşluğu FinTürk şube
sayısıdır. İl fiyatı kaynak boşluğu kapanmamıştır, ancak resmî alanı bozmayan
ayrı bir analiz proxy'siyle kullanılabilir hâle getirilmiştir. Diğer konular
veri çekme hatası değil, kurumun yayımlamadığı gözlem veya farklı tanımlı kaynak
sorunudur.

## Repoya uygulanan sonuç

Araştırma yalnız rapor olarak bırakılmadı. FinTürk işleme koduna ham kaynak
değerini koruyan, kesin kimlikle kanıtlanmış analitik değeri ayrı alanda tutan
bir mekanizma eklendi:

- `value`: resmî ham değer, kaynakta boşsa null kalır
- `usable_value`: yalnız kanıt varsa analitik değer, bu durumda 0
- `value_origin`: değerin kaynaktan mı, kimlikten mi geldiğini belirtir
- `derivation_formula`: kullanılan kesin formülü taşır
- `is_analytically_resolved`: kaynak boşluğunun kanıtla çözüldüğünü belirtir

1.328 türetimin tamamı ayrı CSV ve Parquet audit dosyasına yazıldı. Bu audit
birleşik veri kataloğunda ayrı bir varlık ve DuckDB içinde ayrı bir tablo olarak
sorgulanabilir. FinTürk ölçüm tablosunun tam istek provenance bilgisi de ayrı
`bddk.finturk_source_tables` tablosunda korunur.

İl fiyatı tarafında da resmî `housing_unit_price_try_per_m2` alanı aynen
korundu. Kaynakta bulunmayan 162 il-çeyrek için yalnız aynı KFE bölgesi ve aynı
çeyrekteki resmî il fiyatlarının medyanı ayrı
`housing_unit_price_with_proxy_try_per_m2` alanında sunulur. Köken ve medyana
giren resmî akran il sayısı ayrı sütunlar ile 162 satırlık audit dosyasında
tutulur. Resmî-kaynak hazırlığı ile proxy izinli hazırlık ayrı bayraklardır.

Güncel birleşik katalog 71 veri varlığı ve 55.501 metrik içerir. Bunların
3.387'si yerel gözlemi bulunan sorgulanabilir metriktir. Tek dosyalık DuckDB
çıktısı 10 şema ve 64 tablo veya view içerir.

## 1. FinTürk şube sayısı boşlukları

[BDDK FinTürk Tablo 6 metaverisi](https://www.bddk.org.tr/BultenFinturk/tr/Home/MetaveriPdfIndir?tabloNo=6),
şube sayısını il bazında yurt içi şube adedi olarak tanımlar. Aynı belge bankaları
fonksiyon grubuna göre mevduat, katılım, kalkınma ve yatırım bankaları olarak
sınıflandırır.

Yerel FinTürk Tablo 6 verisinde aşağıdaki kimlik kontrol edildi:

```text
SEKTÖR = MEVDUAT + KATILIM + KALKINMA VE YATIRIM
```

Kontrol sonucu:

- 22 çeyrek ve 81 il, toplam 1.782 il-çeyrek satırı
- Maksimum mutlak kimlik farkı: 0
- Kimlik ihlali: 0
- Negatif veya kesirli yayımlanmış şube sayısı: 0
- Kaynak-null fonksiyon grubu şube hücresi: 1.328
- Kimlikle kanıtlanan analitik sıfır: 1.328
- Çözümlenmemiş şube sayısı boşluğu: 0

1.074 il-çeyrekte tek bir fonksiyon grubu, 127 il-çeyrekte iki fonksiyon grubu
boştur. Şube sayıları negatif olamayacağı ve sektör toplamından yayımlanmış
fonksiyon gruplarının toplamı çıkarıldığında kalan her satırda tam sıfır olduğu
için, boş bileşenlerin her biri sıfırdır.

Bu sonuç ham kaynağı değiştirmek için kullanılmaz. `value` null kalır,
`usable_value` sıfır olur ve `value_origin` alanı
`derived_zero_function_group_identity` değerini taşır. Her türetim kaynak dosya
hash'i, formül ve kimlik kalıntısıyla
[`branch_zero_fallback_audit.parquet`](../../data_pipeline/bddk/processed/finturk_all_groups_all_cities/branch_zero_fallback_audit.parquet)
içinde denetlenebilir.

`SubeyeDusenNufus`, şube sayısı sıfırken matematiksel olarak tanımsız kalır.
Bu nedenle o alan sıfıra çevrilmez ve `structural_undefined` olarak korunur.

## 2. TBB Haziran 2026 kredi raporu

[TBB Tüketici Kredileri ve Konut Kredileri rapor ailesi](https://www.tbb.org.tr/istatistiki-raporlar/11237)
9 Eylül 2026 tarihinde canlı indiriciyle yeniden kontrol edildi. Haziran 2026
için yayımlanmış XLS, PDF veya DOCX eki bulunmadı. Canlı kontrol sonucu:

- Beklenen dönem: 1
- Yayımlanmış dönem: 0
- Kaynak boşluğu: `2026-06`
- Neden: `not_published`
- Son kontrol: `2026-09-09T10:03:13Z`

[TBB İstatistikleri Yayınlama Takvimi](https://www.tbb.org.tr/sites/default/files/docs/tbb_istatistikleri_yayinlama_takvimi.pdf),
Haziran dönemi Tüketici Kredileri ve Konut Kredileri raporunu Ağustos ayının
dördüncü haftasında planlar. Buna rağmen 9 Eylül kontrolünde rapor listede yoktur.
Bu durum gecikmiş yayın olarak kaydedilebilir, ancak veri tahmin edilemez.

Doğru yaklaşım, mevcut indiriciyi günlük veya manuel periyodik çalıştırmak ve
rapor yayımlandığında aynı hash, ek türü ve dönem doğrulamalarından geçirerek
eklemektir.

### Ayrı resmî kaynak: TBB Risk Merkezi aylık bülteni

[TBB Risk Merkezi aylık bülten listesinde](https://www.riskmerkezi.org/istatistiki-raporlar-liste/2541)
Haziran 2021-Haziran 2026 arasındaki altı tam bülten bulundu ve indirildi. Her
Haziran bültenindeki 13 aylık grafikler birlikte Haziran 2020-Haziran 2026
arasında kesintisiz kaynak kapsamı sağlar. Yarışma dönemi için 66 ayın tamamında
şu beş metrik vardır:

- Konut kredisi kalan ana para bakiyesi
- Konut kredisi tekil kişi sayısı
- Kişi başına ortalama konut kredisi riski
- Tasfiye olunacak konut kredileri oranı
- Finansal sisteme ilk kez konut kredisiyle giren kişi sayısı

Altı PDF SHA-256 ile doğrulanır. Toplam 390 vintage gözlem saklanır. Birbirini
izleyen bültenlerin ortak Haziran aylarında 25 metrik-ay karşılaştırılmış, 6
resmî değer revizyonu bulunmuştur. Analiz panelinde en yeni resmî yayın seçilir,
önceki değer audit katmanında kalır.

Bu kaynak TBB çeyreklik tüketici kredileri raporunun eş değeri değildir. İlk kez
kullanan kişi sayısı bir kişi olayıdır, kredi kullandırım tutarı değildir. Risk
Merkezi bakiyesi de ayrı kapsam ve grafik yuvarlaması taşıdığı için BDDK, EVDS
ve TBB bakiyelerinin üzerine yazılmaz.

## 3. İl bazlı konut birim fiyatları

[TCMB Konut Fiyat Endekslerine İlişkin Uygulama Değişiklikleri](https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES)
belgesine göre il bazlı ortanca konut birim m2 fiyatları 2024 Temmuz yayınıyla,
yalnız veri sayısının yeterli olduğu iller için çeyreklik yayımlanmaya başladı.

Yerel 52.696 serilik EVDS metadata kataloğunun tamamı tarandı. Konut birim
fiyatı ailesinde 81 il ile Türkiye kaydı bulunurken, arşivde yalnız Türkiye,
İstanbul, Ankara ve İzmir için dört eski seri vardır. Eksik iller için aynı
tanıma sahip alternatif veya arşiv seri bulunmadı.

Yarışma dönemindeki 162 boş il-çeyrek gözlem:

- Ardahan, Bayburt, Gümüşhane, Hakkari ve Tunceli: 22 çeyreğin tamamı
- Ağrı, Bitlis, Iğdır, Kars, Muş ve Van: 2021Q1-2022Q4
- Şırnak: 2021Q1-2021Q4

Bu hücreler resmî il birim fiyatı olarak doldurulamaz. Sistemde üç güvenli
seçenek vardır:

1. İl birim fiyatı isteyen analizde null ve kaynak açıklaması göstermek.
2. Seviye analizi için yalnız aynı KFE bölgesi ve aynı çeyrekteki resmî il
   fiyatlarının medyanını açık `proxy` kökeniyle kullanmak.
3. Fiyat eğilimi gerekiyorsa ilgili bölgesel KFE'yi ayrı metrik ve
   `proxy_for_trend_only` etiketiyle kullanmak.

Bölgesel KFE bir endekstir, il bazlı TL/m2 fiyat seviyesi değildir. Bu iki
metrik birbirinin yerine etiketlenmemelidir.

### İl bazlı konut birim kiraları

[TCMB'nin Yeni Kiracı Kira Endeksi açıklamasına](https://www.tcmb.gov.tr/wps/wcm/connect/blog/tr/main+menu/analizler/yeni+kiraci+kira+endeksi+ve+kira+enflasyonu)
göre YKKE ve il bazında konut birim kiraları 17 Şubat 2026 tarihinde
yayımlanmaya başladı. Tam EVDS kataloğundan 81 ilin kodu araştırıldı. İstanbul,
Ankara ve İzmir için daha önce ulusal kontrol paketine alınmış 3 seri, bölgesel
pakette tekrarlanmadı. Kalan 78 benzersiz seri bölgesel pakete eklendi.

Bu yeni katmanın sonucu:

- Bölgesel EVDS paketi 441 seriden 519 seriye çıktı
- 2.028 yeni çeyreklik kaynak satırında 1.766 dolu değer ve 262 kaynak null var
- 81 il x 22 çeyrek paneline `housing_unit_rent_try_per_m2` eklendi
- Yeterli geçmişi olan gözlemler için `housing_unit_rent_yoy_pct` hesaplandı
- Ardahan, Bayburt, Bingöl, Gümüşhane, Hakkari, Tunceli ve Şırnak tamamen boş
- Ağrı, Bitlis, Iğdır, Kars, Muş ve Van kısmi kapsama sahip

Kira seviyesi satış fiyatı değildir. Bu nedenle eksik il konut birim fiyatını
doldurmak için kullanılmaz, yalnız ayrı bir barınma maliyeti ve talep kontrolü
olarak tutulur.

## 4. Altın serisinin Haziran 2026 boşluğu

`TP.MK.KUL.YTL`, Ankara Kuyumcular ve Saatçiler Odası kaynaklı aylık ortalama
TL/gram serisidir ve Mayıs 2026'da biter. `TP.ALTINPIYASA.KAP02`, Borsa İstanbul
kaynaklı iş günü kapanış fiyatıdır ve birimi TL/kg'dır. İkinci seri 0,001 ile
çarpılarak ayrı bir TL/gram kontrol serisine dönüştürülmüştür.

2020-01 ile 2026-05 arasındaki 77 ortak ayda, BIST iş günü kapanışlarının aylık
ortalaması ile serbest piyasa aylık ortalaması karşılaştırıldığında:

- Seviye korelasyonu: 0,999924
- Aylık değişim korelasyonu: 0,979775
- Ortalama seviye farkı: yüzde -1,1456
- Medyan seviye farkı: yüzde -1,0258
- En düşük ve en yüksek aylık fark: yüzde -5,7322 ile yüzde +3,9801
- Haziran 2026 BIST aylık ortalaması: 6.358,654 TL/gram
- Haziran 2026 BIST ay sonu kapanışı: 6.051,5 TL/gram

Çok yüksek korelasyona rağmen kaynak, piyasa ve toplulaştırma yöntemi farklıdır.
Bu nedenle BIST değeri `TP.MK.KUL.YTL` içine yazılmaz. Kullanıcı açıkça birleşik
altın göstergesi isterse Haziran 2026 değeri
`proxy_bist_monthly_average` kökeniyle ayrı bir türetilmiş seride sunulabilir.
Mevcut BIST serisi zaten Haziran 2026'ya kadar eksiksiz kontrol değişkenidir.

## 5. BDDK haftalık boş hücreleri

BDDK haftalık verisindeki 2.230 boş hücrenin tamamı TL-only metriklerin yabancı
para alanlarıdır. Bunlar veri kaybı değil, tanım gereği uygulanamaz hücrelerdir
ve `source_not_applicable` olarak sınıflandırılmıştır. Çözümlenmemiş haftalık
kaynak boşluğu yoktur.

## Uygulama kararı

| Veri | Ham değer | Analitik değer | Etiket |
| --- | --- | --- | --- |
| FinTürk kaynak-null şube sayısı | null | 0 | `derived_zero_function_group_identity` |
| Şubeye düşen nüfus, şube sayısı 0 | null | null | `structural_undefined` |
| Yayımlanmamış il birim fiyatı, resmî alan | null | null | `source_not_published` |
| Yayımlanmamış il birim fiyatı, proxy izinli alan | null | aynı bölge ve çeyrek resmî il medyanı | `same_region_same_quarter_official_median_proxy` |
| Bölgesel KFE proxy'si | kaynak değeri | kaynak değeri | `proxy_for_trend_only` |
| İl konut birim kirası | kaynak değeri veya null | ayrı kira metriği | `source_observed` / `source_not_published` |
| TBB Haziran 2026 raporu | yok | null | `not_published` |
| Risk Merkezi Haziran 2026 ilk kullanıcı sayısı | 13 bin kişi | 13 bin kişi | `source_observed_person_count` |
| Risk Merkezi Haziran 2026 parasal kullandırım | yayımlanmıyor | null | `not_available_in_source` |
| `TP.MK.KUL.YTL` Haziran 2026 | yok | null | `source_not_published` |
| BIST altın kontrolü | kaynak değeri | TL/kg ve ayrı TL/gram dönüşümü | `source_observed` / `unit_converted` |

## Sınırlamalar

- FinTürk sıfır sonucu yalnız şube sayısı metriği ve belirtilen fonksiyon
  grupları için geçerlidir. Diğer null alanlara genellenmez.
- Bölgesel fiyat medyanı resmî il gözlemi değildir ve yalnız açık proxy
  politikasıyla kullanılabilir.
- Bölgesel KFE, il düzeyinde fiyat seviyesini ölçmez.
- İl bazlı kira, il bazlı satış fiyatının yerine geçmez.
- Altın proxy'si kaynak serinin devamı değil, farklı piyasa tanımlı bir kontrol
  serisidir.
- TBB raporunun gelecekte yayımlanması mümkündür. Bu rapor için sonuç yalnız
  9 Eylül 2026 canlı kontrolünü ifade eder.
- Risk Merkezi PDF grafik değerleri yayımlandıkları hassasiyette ve yuvarlamayla
  saklanır. Daha hassas bir sayı varmış gibi gösterilmez.

## Araştırma kapsamı ve durma gerekçesi

Resmî BDDK metaverisi, TBB rapor listesi ve yayın takvimi, TCMB yöntem belgesi,
tam yerel EVDS metadata kataloğu ve ham gözlemler incelendi. İl fiyatı için
eş tanımlı arşiv seri bulunmadı, TBB raporu yayımlanmadı, altın serilerinin
tanımları farklı kaldı ve FinTürk kimliği tüm satırlarda kesin sonuç verdi.
Yeni bir arama dalgasının bu dört kararın güven düzeyini değiştirmesi beklenmediği
için araştırma durduruldu.

## İddia-kaynak kaydı

| İddia | Kaynak | Yayın veya erişim bilgisi |
| --- | --- | --- |
| FinTürk şube tanımı ve fonksiyon grupları | [BDDK FinTürk Tablo 6 metaverisi](https://www.bddk.org.tr/BultenFinturk/tr/Home/MetaveriPdfIndir?tabloNo=6) | BDDK, erişim 8 Eylül 2026 |
| 1.328 sıfır ve 1.782 kimlik kontrolü | Yerel FinTürk ham snapshot'ı ve [`branch_zero_fallback_audit.parquet`](../../data_pipeline/bddk/processed/finturk_all_groups_all_cities/branch_zero_fallback_audit.parquet) | Hesaplama 8 Eylül 2026 |
| TBB Haziran 2026 raporu yayımlanmamış | [TBB resmî rapor ailesi](https://www.tbb.org.tr/istatistiki-raporlar/11237) ve canlı indirici çıktısı | Erişim 9 Eylül 2026, 10:03:13 UTC |
| Risk Merkezi Haziran 2021-Haziran 2026 bültenleri mevcut | [TBB Risk Merkezi resmî bülten listesi](https://www.riskmerkezi.org/istatistiki-raporlar-liste/2541), yerel `manifest.json` ve altı hash doğrulanmış PDF | Erişim 8 Eylül 2026, 15:54 UTC |
| Risk Merkezi 66 aylık paneli 5 eksiksiz metrik içerir | [`validation.json`](../../data_pipeline/risk_center/monthly_housing_v1/processed/validation.json), vintage ve panel Parquet dosyaları | Hesaplama ve doğrulama 8 Eylül 2026 |
| Haziran raporu planı Ağustos 4. hafta | [TBB yayınlama takvimi](https://www.tbb.org.tr/sites/default/files/docs/tbb_istatistikleri_yayinlama_takvimi.pdf) | TBB, erişim 8 Eylül 2026 |
| İl fiyatları yalnız yeterli veri olan illerde yayımlanır | [TCMB KFE uygulama değişiklikleri](https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES) | TCMB, 2024 uygulama değişikliği, erişim 8 Eylül 2026 |
| Eksik iller için eş tanımlı arşiv seri yok | [`evds_series_catalog.parquet`](../../data_pipeline/catalog/evds_series_catalog.parquet) | 52.696 seri, yerel tarama 8 Eylül 2026 |
| YKKE ve il bazlı birim kira yayını 17 Şubat 2026'da başladı | [TCMB Yeni Kiracı Kira Endeksi açıklaması](https://www.tcmb.gov.tr/wps/wcm/connect/blog/tr/main+menu/analizler/yeni+kiraci+kira+endeksi+ve+kira+enflasyonu) | TCMB, 17 Şubat 2026, erişim 8 Eylül 2026 |
| İl kira katmanı 81 ili kapsar, kaynak null değerler korunur | [`regional_housing_v1/validation.json`](../../data_pipeline/evds/regional_housing_v1/validation.json) ve [`regional/processed/validation.json`](../../data_pipeline/regional/processed/validation.json) | Hesaplama ve doğrulama 8 Eylül 2026 |
| 162 il-çeyrek fiyat proxy'si yalnız aynı bölge ve çeyrekteki resmî il medyanıdır | [`housing_unit_price_proxy_audit.parquet`](../../data_pipeline/regional/processed/housing_unit_price_proxy_audit.parquet) ve [`validation.json`](../../data_pipeline/regional/processed/validation.json) | Hesaplama ve doğrulama 9 Eylül 2026 |
| Altın serileri yüksek korelasyonlu ama farklı tanımlı | Yerel EVDS ham gözlemleri ve seri metadata kayıtları | 77 ortak ay, hesaplama 8 Eylül 2026 |
