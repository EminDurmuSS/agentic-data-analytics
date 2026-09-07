# BDDK aylık konut kredisi bakiyesi, tarihsel erişim ve seri seçimi notu

> Bu belge ilk erişim denemesinin tarihsel araştırma kaydıdır. Canlı indirme
> daha sonra yerel geliştirme ortamında TLS doğrulaması açıkken tamamlandı.
> Güncel kapsam ve doğrulama sonuçları için `data_pipeline/bddk/README.md`
> dosyasını kullanın.

Araştırma tarihi: 2026-09-07. Hedef: 2021-01–2026-06 (66 ay).

## Doğrulanan / dokümante edilen
- Resmî veri ürünü: **Aylık Bankacılık Sektörü Verileri**. Gelişmiş gösterim arama indeksinde kalem **Tüketici Kredileri-Tüketici Kredileri - Konut** olarak yer alıyor. Bu gözlem doğru tablo ailesini ve satır adını seçmek içindir; kalem ID'si veya API kodu bu oturumda görülmedi.
- Giriş: https://www.bddk.org.tr/bultenaylik
- Gelişmiş arayüz: https://www.bddk.org.tr/BultenAylik/tr/Home/Gelismis
- Resmî metadata: https://www.bddk.org.tr/BultenDosyalari/Home/Index/Aylik-MetaVeri
- Metadata'nın resmî arama indeksindeki açıklamasına göre ürün mevduat, katılım, kalkınma ve yatırım bankalarının raporladığı bilgileri kapsıyor. Tüketici kredileri tablosunun tanımı yurt içi yerleşik müşterilerin tüketici kredisi, taksitli ticari kredi ve kredi kartı alacaklarını belirtiyor.
- Aynı metadata arama indeksinde Excel indirme / MS Excel'e aktarma olanağı açıkça belirtiliyor.
- Aylık temel sayfanın indekslenen güncel başlığı Bilanço (milyon TL), Dönem:2026/7. Bu Haziran 2026'nın zaman bakımından geçmiş dönem olduğunu destekler; konut satırında 66 eksiksiz gözlem olduğunu kanıtlamaz.

## Somut indirme seçimi (uygulama talimatı, canlı export testi yapılmadı)
1. Aylık Bülten → Gelişmiş Gösterim.
2. Tablo ailesi Tüketici Kredileri; kalem Tüketici Kredileri - Konut.
3. İlk ana seri için banka grubu Sektör seçimi; 2021 Ocak–2026 Haziran ayları.
4. Para ayrımı sunuluyorsa TP, YP ve Toplam ayrı kaydedilmeli; ana kredi toplamı ile faiz serisinin TL kapsamı birbirine karıştırılmamalı. Güncel alanların bire bir adları ve sütun düzeni dosya indirildiğinde doğrulanmalı.
5. Birim başlığını dosyadan kontrol et: aylık bülten için milyon TL hedefleniyor; yanlışlıkla bin TL veya para birimi dönüşümü seçilmemeli.
6. Excel dışa aktar; ham dosyayı ve seçilmiş filtrelerin kaydını koru.
7. Tam 66 benzersiz ay, aylık kapsam, sayısal parse, birim ve bankacılık kapsamını doğrula. En az 2021-01, 2023-12, 2026-06 değerlerini arayüzle kontrol et.

## Test edilen erişim ve sonuç
- Container Python urllib.request.urlopen('https://www.bddk.org.tr/BultenAylik/', timeout=15): HTTP 502 Bad Gateway.
- web aracında aylık ana sayfa/gelişmiş arayüz: 502 veya timeout.
- Metadata ve TR/EN açıklamalarının doğrudan açılması: timeout.
- Resmî web arama indeksi sonuçları erişilebilir. Bu nedenle keşif yapılabildi; **gerçek Excel/CSV ve 66 gözlem indirilip parse edilemedi**.
- Sorunun BDDK sunucusundan mı erişim katmanından mı kaynaklandığı ayrıştırılmadı. 'BDDK çalışmıyor' veya 'API yok' sonucu çıkarılmamalı.

## Açık kalanlar
- Güncel export dosyasının sütun/sayfa adları, veri kalemi dahili ID'si, resmî belgelenmiş API olup olmadığı.
- Konut satırındaki takipteki krediler ve reeskont dahil/hariç tanımı. Bu tanımlar belge ya da tablonun alt metadata'sı okunmadan varsayılmamalı.
- Banka grubu Sektör ile TCMB faiz serisinin banka kapsamı bire bir örtüşmeyebilir. Birlikte grafik çizmek mümkün; aynı örneklemmiş gibi nedensel analiz yapılmamalı. Gerekirse BDDK mevduat bankaları alt serisi ayrıca alınır.
- 66 ayın hiçbir sayısal gözlemi bu oturumda doğrulanmış değildir. Kod hazır veya veri çekildi denmemeli.

## Güncel sonuç

Bu ilk oturumdaki 502 engeli kalıcı bir BDDK veri yokluğu değildi. Aylık
bültenin 66 ay ve 17 tablo kapsamı daha sonra doğrudan resmî kaynaktan
indirildi, ham yanıt ve istek bilgileriyle saklandı ve işlenmiş katmanda
doğrulandı. Haftalık bülten ve FinTürk kapsamı da ayrıca tamamlandı.
