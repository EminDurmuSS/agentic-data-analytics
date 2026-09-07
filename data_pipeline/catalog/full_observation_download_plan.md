# EVDS3 kapsam envanteri ve tüm veri indirme planı

## Bu aşamada indirilen şey

Tüm public frontend kategori/grup listesi: 154 kategori; 124 kategorinin doğrudan veri grubu var; toplam 676 benzersiz grup. Kategori ağacı ve açık arşiv adlarına göre 448 arşiv dışı, 228 arşiv grubu. STATUS=1 arşiv gruplarında da var; bu alanı güncel/arşiv ayrımı diye kullanmayın.

Gruplar: 284 aylık, 163 üç aylık, 102 yıllık, 88 haftalık, 26 iş günü, 10 günlük, 2 altı aylık, 1 ayda iki. Kök konu sayısı 11; bunlardan biri arşivdir.

Bu çalışma bütün serilerin gözlemlerini indirmedi. Yalnız kategori/grup kataloğu eksiksiz public endpoint yanıtı olarak alındı. Per-group seri metadata taramasının gerçekleşen kapsamı series_fetch_summary.json içinde yer alacaktır; seri sayısını toplam EVDS seri sayısı diye sunmayın.

## Kaynak ve kapsamın anlamı

- Ana sayfa: https://evds3.tcmb.gov.tr/tumSeriler
- Kullanılan public frontend metodu: GET https://evds3.tcmb.gov.tr/igmevdsms-dis/categories/withDatagroups/type=json
- Seri listesi: GET https://evds3.tcmb.gov.tr/igmevdsms-dis/serieList/fe/type=json&code=GROUP
- Public uygulamanın kendi JavaScript'i bu tek kategori isteğini ve grup seçimi başına tek seri listesi isteğini yapıyor. Bu listelerde bir pagination parametresi veya yanıt belirteci görünmüyor. Gözlenen public liste evreni raporlanıyor; erişilemeyen yönetici katalogları hakkında çıkarım yapılmıyor.
- Belgeli API: https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=8

## Tüm 2021-01–2026-06 gözlemleri istenirse

1. Önce bu 676 grup için metadata taramasını tamamlayın, başarısız ve boş grupları açık raporlayın. Aynı seri kodunun birden fazla grupta görünmesi kontrol edilir. Seri adı, birim, sıklık, kaynak, hiyerarşik üst seri ve varsayılan toplulaştırma kaydedilir. Tüm seriler bulunmadan tahmini seri sayısı kesin diye söylenmez.
2. Tarih kapsamını ayrıca sorgulayın. Public kataloglarda START_DATE/END_DATE yok. LAST_UPDATED alanı son gözlem tarihi değildir. Belgeli API metadata'sı başlangıç/bitiş alanlarını tanımlar; alternatif public uygulama /serieList/baslangicBitis sorgusunu kullanır. Arşiv grubunu yalnız eski göründüğü için silmeyin: 2021–2026 ile kesişen arşiv verisi olabilir. Arşiv dışı olmak da tüm hedef dönemin mevcut olduğu anlamına gelmez.
3. Kaynak düzeyini çekin: formulas=0 ve serinin gerçek frekansı. Bütün serileri aylığa dönüştürmeyin. Ayrı normalleştirilmiş uzun tablo anahtarı (series_code, observation_period, snapshot_id) olsun. Dönem başlangıcı/bitişi ve frequency açıkça saklansın.
4. Aynı gruptaki ve aynı frekanstaki serileri küçük partilerle çekin. Daily/workday serileri 2021,2022,2023,2024,2025 ve 2026 ilk yarı olarak yıllık pencerelere bölün. Böylece tek seride pencere başına en fazla366 takvim tarihi vardır. Aylık hedef66, çeyreklik22, altıaylık11 dönemdir; bunlar tarih kapsamı izin veriyorsa tek pencerede1000 tarih noktasının altında kalır. Haftalık hedef yaklaşık287 haftadır; yayımlanan gerçek gün ve tatil takvimiyle doğrulanır. Yıllık2026 verisini Haziran'a kadar yayımlanmış saymayın.
5. Bir test partisiyle dönen toplamCount, seri sütunları, ilk/son tarih, ND ve dönem sayısını kontrol edin. Yanıt boyutu veya sunucu limiti görülürse seri partisini ve tarih penceresini küçültün. HTTP200 tek başına veri tamamlandı anlamına gelmez.
6. Her isteğin tam gövdesi/URL'si, çekim zamanı, HTTP durumu, SHA256 ve kaynak ham yanıtını saklayın. İşlenmiş tabloda ND'yi sıfır yapmayın. İç içe toplam ve alt kalemleri birlikte toplayıp çift saymayın.
7. Dönem sınırlarını yerel olarak tekrar uygulayın. Bu oturumda haftalık faiz sorgusu endDate=30-06-2026 olmasına rağmen03-07-2026 haftasını dahil etti. Ham yanıt korunup hedef dışı gözlem işlenmiş veriden çıkarılır.
8. Çekimleri yeniden başlatılabilir yapın: tamamlanan grup×seri-partisi×tarih-penceresi manifestten atlanır.429 ve403 alınırsa durup resmi yanıt koşullarına uyulur. Sınırlı eşzamanlılık kullanılır. Aynı veriyi gün içinde tekrar tekrar çekmeyin.
9. Farklı baz yılları, yöntem revizyonları, mevsimden arındırılmış ve ham varyantlar ayrı seriler olarak saklanır. Örneğin2026 TÜİK konut satışlarında aynı görünümlü TRTOPLAMkodları artık işyerine, KTRTOPLAMkonuta karşılık geliyor; isim-kod eşleştirmesi zorunlu. TÜFE2003/2025 bazlarını ve KFE eski/yeni bazlarını körlemesine birleştirmeyin.
10. Geçmişe yönelik revizyonları yakalamak için yeni snapshot mevcut dönemin üzerine sessizce yazılmamalı; önceki snapshot korunmalı. Aynı(publicasyon tarihinde bilinen)seri ile güncel revize seriyi ayırmak için vintage/çekim zamanı tutulmalı.

## API1000 gözlem sınırı

TCMB'nin7 sayfalık resmî Web Servis/API kılavuzu,1000 gözlemi aşan tarih aralığında bitişten geriye1000 gözlemin döndürüleceğini açıkça söylüyor. Kılavuz ayrıca normal API için kullanıcıya ait key header gerektiriyor; günlük tek çekim ve aynı grup serilerini birlikte çağırmayı öneriyor. Bu sözleşme belgeliAPI içindir. Halka açık site /fe uçnoktasının aynı sınırı birebir uyguladığı test edilmedi; bu nedenle her iki yol için de sonuç kapsamı doğrulaması ve küçük tarih pencereleri gerekir.

Kategori/seri metadata taraması küçük bir katalog çalışmasıdır. Tüm gözlemleri çekme işi ancak yarışmanın gerekli veri kapsamı seçilip dönem/sıklık/anlam kuralları belirlenince sayısallaştırılabilir. Dosyadaki676 sayısı seri sayısı değil veri grubu sayısıdır.

## Gerçekleşen bounded seri taraması

300 saniyelik incelemede 66/676 grup sorgulandı ve 3.892 benzersiz seri metadata kaydı indirildi. 610 grup henüz taranmadı. Sorgulanan grupların tümü HTTP 200 döndürdü; boş grup ve tekrarlanan seri kodu görülmedi. Bu bir örneklem/altkümedir, bütün EVDS seri sayısı bilinmiyor. Kısmi düz liste evds_series_partial.csv dosyasındadır.

fetch_series_catalog.py, aynı snapshot için SHA256 ile doğrulanmış tamamlanan grupları tekrar istemeden devam edebilecek şekilde bırakıldı. Her çalıştırma 300 saniye/100 MiB bütçesine ve en fazla iki eşzamanlı isteğe sahiptir. Metadata keşfi sırasında hiçbir seri gözlemi indirilmedi.
