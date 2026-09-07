# Konut kredisi verileri: TCMB aylık alternatif ve erişim kanıtı

## İndirilen aylık seri

2020-01–2026-06, 78 benzersiz ay, 3 serinin her birinde sıfır boş değer. 2020 yılı 2021 yıllık değişimlerinin hesaplanması için ısınma dönemi; ana hedef 2021-01–2026-06, 66 ay.

| Kod | Banka grubu | Birim | Sıklık |
|---|---|---|---|
| TP.KM.B11 | Mevduat bankaları | bin TL | Aylık |
| TP.KM.A11 | Kalkınma ve yatırım bankaları | bin TL | Aylık |
| TP.KB.KRE10 | Katılım bankaları | bin TL | Aylık |

Kaynak dosya credit_monthly_data.json; aynen kullanılan POST gövdesi credit_monthly_request.json. Seri kodları resmi public searchResults ve serieList kataloglarından keşfedildi, tahmin edilmedi. Ham yanıt korunmuştur. Hiçbir API anahtarı veya oturum çerezi kullanılmadı. Bu public web arayüzü yolu bugün HTTP200 ile çalıştı; kararlı belgelenmiş API sözleşmesi olduğu ileri sürülmüyor.

2026-06: mevduat=724644148 bin TL; kalkınma/yatırım=10030 bin TL; katılım=76031286 bin TL. Üç banka grubunun aritmetik toplamı 800685464 bin TL, yani yaklaşık 800.685 milyar TL. Bu hesap TCMB serilerinden türetilir; BDDK aylık resmi sektör değeri değildir. Banka grupları birbirinden ayrıdır. Veriler stoktur; yeni kredi kullandırımı değildir.

## Kapsam ve önemli ayrım

Resmi aylık metadata indirildi ve metni incelendi: TCMB para/banka verileri bankaların yalnız yurt içi şubelerinin faaliyetlerini içerir; yurt dışı şubeler dahil değildir. Belge bu nedenle BDDK ve TBB yayınlarından farklılaşabileceğini açıkça belirtir. Banka verilerinin kaynağı, BDDK Tekdüzen Hesap Planı çerçevesindeki aylık Bankalar ve Katılım Bankaları Tekdüzen Raporlama Paketi'dir. Bu ifade TCMB serisini doğrudan BDDK aylık export'una dönüştürmez.

Krediler nominal değerle gösterilir. Genel metaverinin geniş kredi tanımından bu üç konut alt satırının takipteki kredi/reeskont dahil-hariç durumunu kesin olarak çıkarmadım. Genel parasal durum için işlemiş faizlerin ayrı gösterilmesi açıklanıyor; bu konut alt serilerinin özgül tanımına otomatik taşınmamalı.

Üç aylık konut serisinin adı orijinal kredi para birimi ayrımını belirtmiyor. Birim bin TL olduğundan 'Türk lirası cinsinden raporlanan bakiye' demek güvenlidir; 'yalnız TL krediler' etiketi verilmemeli. Katalogda bu konut satırı için ayrı TP/YP serisi görünmedi. Haftalık alternatif .HPBITABLO6.3 ise adında açıkça TL+YP der.

Faiz serisi TP.KTF12 mevduat+kalkınma/yatırım bankalarına aittir. Stok karşılaştırmasında B11+A11 toplamı banka grubu bakımından daha yakındır; tüm banka grupları toplamı katılımı da içerir. Kredi para birimi ve akım/stok farkı devam eder.

## 12 ek aylık seri: indirildi

credit_monthly_extra_data.json: 2020-01–2026-06, 78 satır; aşağıdaki12seride sıfır boş değer ve sıfır yinelenen ay. İstek credit_monthly_extra_request.json.

- Mevduat: TP.KM.B10 tüketici toplamı; B12 taşıt; B13 ihtiyaç ve diğer; B14 bireysel kredi kartları.
- Kalkınma/yatırım: TP.KM.A10 tüketici toplamı; A12 taşıt; A13 ihtiyaç ve diğer; A14 bireysel kredi kartları.
- Katılım: TP.KB.KRE09 tüketici toplamı; KRE11 taşıt; KRE12 ihtiyaç ve diğer; KRE13 bireysel kredi kartları.

Katalog hiyerarşilerinde fark var: kalkınma/yatırımda kredi kartı A14 tüketici A10 altında; mevduatta B14 tüketici B10 yanında listeleniyor. Toplamları satır hiyerarşisini incelemeden birbirine eklemeyin. Sektör bazında tüketici toplamını keyfi türetmeyin. credit_chosen_series.json tüm15serinin özgün adlarını ve üst satır kodlarını saklar.

## Haftalık alternatif: daha kısa geçmiş

credit_data.json: 10seçilmiş kredi serisi, istek2021-01–2026-06. Yanıt288hafta; 2024-06-28 öncesi182satırın değerleri boş. Resmi haftalık metadata seçilmiş kredi tablolarının karşılaştırılabilir başlangıcını2024-06-28 olarak doğrular. Her seride106doluhafta vardır; son yanıt haftası03-07-2026 olduğu için hedef aralıkta105doluhafta kalır. Bu dosya ilk66aylık paketi doldurmak için kullanılmamalı ve önceki arşivle otomatik birleştirilmemeli.

TP.HPBITABLO6.3 konut TL+YP, binTL, Bankacılık Sektörü Seçilmiş Kredi Büyüklükleri (Yurt İçi Şubeler); takipteki konut TP.HPBITABLO6.51 ayrı satırdır. İki farklı seriyi aylık birleştirme gereksiz: tam78aylık konut stokları zaten indirildi.

## Kaynak ve kanıt dosyaları

- Resmi mevduat katalog: https://evds3.tcmb.gov.tr/tumSeriler/450105/bie_kmmbkre
- Resmi kalkınma/yatırım katalog: https://evds3.tcmb.gov.tr/tumSeriler/450105/bie_kmkykre
- Resmi katılım katalog: https://evds3.tcmb.gov.tr/tumSeriler/450105/bie_kbkmkre
- Aylık metadata URL: http://www.tcmb.gov.tr/wps/wcm/connect/f3ad1a37-6d59-4a0b-b0d7-09b51cd7a73f/MetaveriAPB%C4%B02018.pdf?MOD=AJPERES&CACHEID=ROOTWORKSPACE-f3ad1a37-6d59-4a0b-b0d7-09b51cd7a73f-ml2zpGJ
- Haftalık metadata URL: https://www.tcmb.gov.tr/wps/wcm/connect/af2e86c3-6410-45ce-9eb6-1770e459b528/Metaveri.pdf?MOD=AJPERES&CACHEID=ROOTWORKSPACE-af2e86c3-6410-45ce-9eb6-1770e459b528-nCuKFP8
- credit_manifest.json: HTTP200, SHA256, ham dosya baytları, tarih kapsamı, boş/yinelenen değer kontrolü; indirme zamanı dosyanın indirme sonrası son yazma zamanıdır (yaklaşık), sunucu timestamp'i kaydedilmedi.
- credit_monthly_validation.json:78ay kontrolü ve dört dönemde örnek değerler.
- credit_*_catalog.json: asıl katalog yanıtları; credit_search_*.json: arama yanıtları.

Bu dosya ara araştırma notudur; final dosyalara root tarafından alınacaktır.
