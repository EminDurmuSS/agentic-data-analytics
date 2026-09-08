# Kalan veri boşlukları için derin araştırma

Hedef kitle: KKB Agentic Data Analytics Hackathon geliştirme ekibi  
Araştırma ve canlı kontrol tarihi: 8 Eylül 2026  
Coğrafya: Türkiye  
Ana dönem: Ocak 2021-Haziran 2026

## Yönetici özeti

Kalan dört veri konusu resmî kaynaklar, yerel ham dosyalar ve tüm EVDS metadata
kataloğu üzerinden yeniden incelendi. Sonuç şöyledir:

| Konu | Karar | Sonuç |
| --- | --- | --- |
| FinTürk şube sayısı kaynak null değerleri | Kesin kimlikle ayrı analitik değer üret | 1.328 / 1.328 hücre için `usable_value=0`, ham `value=null` korunur |
| TBB Haziran 2026 raporu | Bekle ve periyodik yeniden kontrol et | Resmî kaynakta henüz yayımlanmamış, tahmin edilemez |
| İl bazlı konut birim fiyatı | İl metriğini doldurma | 162 il-çeyrek değer kaynakta yok, eş tanımlı arşiv seri bulunamadı |
| Haziran 2026 serbest piyasa altını | Kaynak seriyi doldurma | Aktif BIST altın serisi ayrı proxy ve kontrol olarak kullanılabilir |

Bu araştırmada gerçek anlamda kapatılabilen boşluk FinTürk şube sayısıdır.
Diğer üç konu, veri çekme hatası değil, kurumun yayımlamadığı gözlem veya farklı
tanımlı kaynak sorunudur.

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

Güncel birleşik katalog 66 veri varlığı ve 55.489 metrik içerir. Bunların
3.297'si yerel gözlemi bulunan sorgulanabilir metriktir. Tek dosyalık DuckDB
çıktısı 9 şema ve 60 tablo içerir.

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
8 Eylül 2026 tarihinde canlı indiriciyle yeniden kontrol edildi. Haziran 2026
için yayımlanmış XLS, PDF veya DOCX eki bulunmadı. Canlı kontrol sonucu:

- Beklenen dönem: 1
- Yayımlanmış dönem: 0
- Kaynak boşluğu: `2026-06`
- Neden: `not_published`
- Son kontrol: `2026-09-08T15:04:02Z`

[TBB İstatistikleri Yayınlama Takvimi](https://www.tbb.org.tr/sites/default/files/docs/tbb_istatistikleri_yayinlama_takvimi.pdf),
Haziran dönemi Tüketici Kredileri ve Konut Kredileri raporunu Ağustos ayının
dördüncü haftasında planlar. Buna rağmen 8 Eylül kontrolünde rapor listede yoktur.
Bu durum gecikmiş yayın olarak kaydedilebilir, ancak veri tahmin edilemez.

Doğru yaklaşım, mevcut indiriciyi günlük veya manuel periyodik çalıştırmak ve
rapor yayımlandığında aynı hash, ek türü ve dönem doğrulamalarından geçirerek
eklemektir.

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

Bu hücreler resmî il birim fiyatı olarak doldurulamaz. Sistemde iki güvenli
seçenek vardır:

1. İl birim fiyatı isteyen analizde null ve kaynak açıklaması göstermek.
2. Fiyat eğilimi gerekiyorsa ilgili bölgesel KFE'yi ayrı metrik ve
   `proxy_for_trend_only` etiketiyle kullanmak.

Bölgesel KFE bir endekstir, il bazlı TL/m2 fiyat seviyesi değildir. Bu iki
metrik birbirinin yerine etiketlenmemelidir.

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
| Yayımlanmamış il birim fiyatı | null | null | `source_not_published` |
| Bölgesel KFE proxy'si | kaynak değeri | kaynak değeri | `proxy_for_trend_only` |
| TBB Haziran 2026 raporu | yok | null | `not_published` |
| `TP.MK.KUL.YTL` Haziran 2026 | yok | null | `source_not_published` |
| BIST altın kontrolü | kaynak değeri | TL/kg ve ayrı TL/gram dönüşümü | `source_observed` / `unit_converted` |

## Sınırlamalar

- FinTürk sıfır sonucu yalnız şube sayısı metriği ve belirtilen fonksiyon
  grupları için geçerlidir. Diğer null alanlara genellenmez.
- Bölgesel KFE, il düzeyinde fiyat seviyesini ölçmez.
- Altın proxy'si kaynak serinin devamı değil, farklı piyasa tanımlı bir kontrol
  serisidir.
- TBB raporunun gelecekte yayımlanması mümkündür. Bu rapor için sonuç yalnız
  8 Eylül 2026 canlı kontrolünü ifade eder.

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
| TBB Haziran 2026 raporu yayımlanmamış | [TBB resmî rapor ailesi](https://www.tbb.org.tr/istatistiki-raporlar/11237) ve canlı indirici çıktısı | Erişim 8 Eylül 2026, 15:04:02 UTC |
| Haziran raporu planı Ağustos 4. hafta | [TBB yayınlama takvimi](https://www.tbb.org.tr/sites/default/files/docs/tbb_istatistikleri_yayinlama_takvimi.pdf) | TBB, erişim 8 Eylül 2026 |
| İl fiyatları yalnız yeterli veri olan illerde yayımlanır | [TCMB KFE uygulama değişiklikleri](https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES) | TCMB, 2024 uygulama değişikliği, erişim 8 Eylül 2026 |
| Eksik iller için eş tanımlı arşiv seri yok | [`evds_series_catalog.parquet`](../../data_pipeline/catalog/evds_series_catalog.parquet) | 52.696 seri, yerel tarama 8 Eylül 2026 |
| Altın serileri yüksek korelasyonlu ama farklı tanımlı | Yerel EVDS ham gözlemleri ve seri metadata kayıtları | 77 ortak ay, hesaplama 8 Eylül 2026 |
