# Mentör beklentileri için geliştirme ve kabul planı

Başlangıç sürümü `95680ab6`. Hedef, yeni ve önceden görülmemiş bir kaynağı mevcut verilerle doğru kapsamda birleştirip hesap, grafik ve kaynaklı açıklamaya dönüştürmek. Kabul, çalışan araç sayısına değil kullanıcının istediği sonucun doğruluğuna dayanır.

| Alan | Uygulama | Kabul kanıtı |
| --- | --- | --- |
| Yeni kaynak | İş düzeyindeki kalem/dönem seçimini kaynak kanıtlı yayımlamaya çeviren derleyici; modelden ETL tarifi istemeyen ana akış | Gerçek banka ve banka dışı PDF'den, elle sözleşme veya tarih eşlemesi yazmadan doğru tablo ve grafik |
| Kaynak bulma | Resmî kaynak keşfi ve bounded belge takibi; araştırmadan sonra hesap akışının devamı | TCMB kararının özgün metni; webden bulunan tablonun kayıtlı analize dönüşmesi |
| Hesap ve açıklama | Kayıtlı analizden deterministik özet istatistikler, dönem toplamları ve karşılaştırma; sayıları hesaplara bağlayan cevap | Altı aylık kâr ve dönem karşılaştırmasının bağımsız doğruyla eşleşmesi; uydurma son sayıların görünmemesi |
| Görev tamamlama | Bütün çıkış yollarında tablo/grafik/özet kanıtı ve kısmi sonuç davranışı | Eksik grafikte completed verilmemesi; ask_user ve web yollarında aynı kurallar |
| Grafik | Gruplu çizgi/çubuk/alan, sabit kaynak ve değerler, grup bazlı kaynak hücresi | BDDK banka grupları için aynı tablonun farklı görünümleri |
| Genel veri | Statik/olay kaydı sorgulama, açık gruplama/toplama ve null davranışı | Banka dışı gerçek raporda kategori hesabı; olay ve statik tablolar için bağımsız testler |
| İstatistik | Ortak takvim adları ve doğru boş dönem kontrolü | Düzenli haftalık/yıllık serilerde geçerli sonuç, gerçek boşlukta açık hata |
| Tekrarlanabilirlik | Sabit girdi, kaynak ve kod hash'i; taze çalışma alanları; bağımsız sayısal doğru | Önce/sonra karşılaştırması, tekrarlar, başarısız ve kısmi denemelerin korunması |

Mimari sınırlar korunur: yerel Python/DuckDB, değişmez kaynak ve analiz kayıtları, typed araçlar, model tarafından SQL/kabuk çalıştırılmaması, Kloudeks model hizmeti, kaynak ve finansal kapsam kontrolleri. Stoklar dönemler boyunca toplanmaz; çakışan banka grupları ulusal toplam diye birleştirilmez. Eksik gözlem sıfır kabul edilmez.

Özet hesabı, pencere içindeki değerlerin sayısını değiştiren bir toplulaştırmadır. Ham tabloyu ve eski analizleri değiştirmeden ayrı kanıt kaydı üretmelidir. Boş veya eksik veri için toplamın sessizce sıfır olması önlenir. Bu ayrımlar DuckDB'nin toplulaştırma ve null semantiğiyle birlikte ele alınır.[^1]

Gerçek kaynak kabul seti mevcut BDDK ham dosyalarını, TCMB'nin 6 Mart 2025 kararını, Akbank ve Garanti'nin 2026 ara dönem raporlarını, İşbank sunumunu ve Tüpraş'ın 2025 ara dönem raporunu içerir. Banka ile sektörün konsolide/konsolide olmayan kapsamları ve rapordaki brüt/net kredi tanımları ayrıca doğrulanmadan pazar payı eşitliği iddia edilmez.[^2][^3] Akbank 2025 yıllık raporu ilk 16 MiB sınırına takılmıştır. Belge sınırı 32 MiB yapıldıktan sonra 19.390.970 baytlık, 335 sayfalık rapor indirildi ve sınırlı sayfa araması doğrulandı; bu sonuç yıllık rapordaki bütün tabloların çıkarıldığı anlamına gelmez.

Uygulama ve gerçekleşen kabul sonuçları [geliştirme raporunda](research/mentor-improvements-2026-09-12/README.md) tutulur; başarısız denemeler saklanır. Yarışma sıralaması garantisi veya resmî puan ağırlığı iddiası içermez. Açık kaynak bileşenleri ve yarışmanın model hizmeti [ayrı belgelenmiştir](OPEN_SOURCE.md).

[^1]: DuckDB, [Aggregate Functions](https://www.duckdb.org/docs/current/sql/functions/aggregates), erişim 12 Eylül 2026.
[^2]: TCMB, [Press Release on Interest Rates, 2025-15](https://www.tcmb.gov.tr/wps/wcm/connect/en/tcmb%2Ben/main%2Bmenu/announcements/press%2Breleases/2025/ano2025-15), 6 Mart 2025.
[^3]: Akbank, [30 June 2026 consolidated financial statements](https://www.akbankinvestorrelations.com/en/images/pdf/consalidated/akbank_en_consolidated_2q26.pdf), PDF s.10; Garanti BBVA, [31 March 2026 consolidated financial report](https://www.garantibbvainvestorrelations.com/en/images/pdf/31_March_2026_Consolidated_Financial_Report.pdf), PDF s.11; Tüpraş, [30 June 2025 consolidated financial statements](https://www.tupras.com.tr/assets/uploads/financial-reports/tupras-consolited-cmb-30062025.pdf), PDF s.3.
