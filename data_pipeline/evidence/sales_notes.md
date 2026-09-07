# Konut satışları — araştırma ve indirilen veri

## Tamamlanan iş

TCMB EVDS3'ün halka açık site veri sorgusu üzerinden TÜİK kaynaklı dört Türkiye konut satış serisi indirildi. API anahtarı, cookie, kullanıcı oturumu veya kimlik bilgisi kullanılmadı. Tüm kodlar gerçek güncel katalog JSON'unda isimleriyle eşleştirildi; tahmin edilmedi.

| Çıktı sütunu | Resmî seri | Resmî adı |
|---|---|---|
| housing_sales_total_count | TP.AKONUTSAT1.KTRTOPLAM | Türkiye_Konut_Toplam Satışlar |
| housing_sales_mortgaged_count | TP.AKONUTSAT2.KTRTOPLAM | Türkiye_Konut_İpotekli Satışlar |
| housing_sales_first_hand_count | TP.AKONUTSAT3.KTRTOPLAM | Türkiye_Konut_İlk El Satışlar |
| housing_sales_second_hand_count | TP.AKONUTSAT4.KTRTOPLAM | Türkiye_Konut_İkinci El Satışlar |

DİKKAT: Güncel kodlarda KTRTOPLAM konutu ifade ediyor. Aynı gruptaki TRTOPLAM ise iş yeri satışını ifade ediyor. Eski kodu adıyla yeniden doğrulamadan kullanmak yanlış veri getirir.

## Kapsam ve anlam

- Ham indirme: Ocak 2020–Haziran 2026, 78 ay, her ay dört gösterge.
- Asıl analiz: Ocak 2021–Haziran 2026, 66 ay.
- 2020 yılı 12 ayı yıllık değişim hesaplarına başlangıç verisidir; ana hedef kapsamı genişletmez.
- Sıklık: AYLIK. Birim: Adet. Kaynak: TÜİK. Arındırılmamış aylık dönem içi satış sayısıdır. Yılbaşından birikimli toplam veya konut/kredi bakiyesi değildir.
- Güncel katalog her grubun SUM varsayılan toplulaştırmasını doğruluyor. Aylıktan çeyreğe/yıla satış sayısı için toplama uygulanır.
- Mevsim ve takvim etkisinden arındırılmış KMA serileri de katalogda var, bu indirmede seçilmedi.
- İpotekli konut satış sayısını yeni kredi kullandırım TL tutarı veya kredi bakiyesi diye adlandırmayın.

## Doğrulama

78 ayın tamamı mevcut; tarih tekrarı, boş/ND değer, negatif değer, tam sayı olmayan değer yok. Toplam = ilk el + ikinci el eşitliği 78 ayın tamamında sağlanıyor. İpotekli, ilk el ve ikinci el her ay toplamdan küçük/eşit.

2021-01: toplam 75.603, ipotekli 11.560, ilk el 25.035, ikinci el 50.568.
2026-06: toplam 129.979, ipotekli 25.993, ilk el 43.406, ikinci el 86.573.

Ayrı Diğer Satışlar serisi bulunan dört resmî EVDS grubunda yok. İşlenmiş CSV'deki housing_sales_other_derived_count = toplam - ipotekli olarak TÜRETİLDİ. Toplam = ipotekli + diğer eşitliği bu sebeple bağımsız doğrulama değildir. housing_sales_mortgaged_share_pct = 100 × ipotekli / toplam; bu da türetilmiş ölçüdür.

## 2026 revizyonu — birincil kaynakla doğrulandı

TÜİK'in 19 Şubat 2026 tarihli açıklamasına göre bağımsız bölümlerin konut olarak sınıflandırılması güncellendi ve geçmiş seriler 2013'e kadar yeniden üretildi. Aynı yayın genişlemesiyle iş yeri satışları ve mevsim/takvim etkilerinden arındırılmış seriler eklendi. Bu nedenle bütün dönemleri aynı güncel revizyondan indirin; eski haber bültenlerindeki tarihsel sayıları yeni seriyle birleştirmeyin. Bu paket güncel EVDS snapshot'ıdır, geçmişte o gün bilinen veri (vintage) değildir.

Resmî revizyon açıklaması:
https://veriportali.tuik.gov.tr/api/tr/data/downloads?p=pihc1VXXHmw5ifxux1plpZpXf5sZ1CobgeuHmZBtObVcRshQpIwgcBy1KUbs2p9OefTGYyvG6FaN1Cj2Lc0iDhMUW%2Fg1gAu5xzYWqSItISXiLI79F4aXylwdhJGOcpOu&t=r

Revizyon PDF'i web aracıyla okunabildi (6 sayfa; belge tarihi 19.02.2026). Scratch'e HTTP indirmesi 403 döndürdü; bu sebeple dosya olarak indirildiğini iddia etmiyoruz. Yeni rota/key denemesi yapılmadı. TÜİK metadata sayfasının okunabilir web çıktısı JavaScript gerekli mesajı döndürdü; birim ve sıklık actual EVDS catalog metadata ile doğrulandı.

## Dosyalar

- sales_data.json: dokunulmamış resmî yanıt.
- sales_request.json: tam POST gövdesi.
- sales_download_manifest.json: indirme UTC zamanı, HTTP durum, SHA256, endpoint, seri listesi.
- sales_bie_akonutsat1_catalog.json ... sales_bie_akonutsat4_catalog.json: resmî kataloglar.
- sales_catalog_manifest.json: katalog kaynak URL / UTC / SHA256.
- sales_categories.json: resmî grup kataloğu; birim Adet ve TÜİK kaynağını içeriyor.
- sales_registry.json: root'un registry'si için field/code/group/metadata eşleştirmesi.
- sales_monthly_with_warmup.csv: 78 ay, four original measures + two clearly named derived measures + warmup flag.
- sales_monthly_target.csv: 66 hedef ay, aynı alanlar.
- sales_validation.json: kontrollerin sonuçları.

## Çekim yolu

POST https://evds3.tcmb.gov.tr/igmevdsms-dis/fe

Tam istek sales_request.json içinde. Gözlemler items listesindedir. Seri kodlarındaki noktalar JSON alanlarında alt çizgiye çevrilir. Aylık Tarih YYYY-MM'dir; UNIXTIME yerine yayımlanan Tarih kullanılmalıdır. Frontend frequency=5 aylığı ifade eder; bunu grup metadata'sındaki internal FREQUENCY=9 ile karıştırmayın.

Bu, 7 Eylül 2026 tarihinde çalıştığı doğrulanan halka açık site veri yoludur; belgeli EVDS API sözleşmesi olduğu iddia edilmez. Yenilemede HTTP200 yetmez: içerik şeması, seri isimleri, 78/66 ay kapsamı ve ölçü tutarlılığı yeniden kontrol edilmelidir.

Katalog sayfaları:
- https://evds3.tcmb.gov.tr/tumSeriler/5002/bie_akonutsat1
- https://evds3.tcmb.gov.tr/tumSeriler/5002/bie_akonutsat2
- https://evds3.tcmb.gov.tr/tumSeriler/5002/bie_akonutsat3
- https://evds3.tcmb.gov.tr/tumSeriler/5002/bie_akonutsat4
- TÜİK metaveri bağlantısı: https://veriportali.tuik.gov.tr/tr/press/58340/metadata
