# Üç ana serinin bağımsız tanım ve sayı kontrolü

Kontrol tarihi: 7 Eylül 2026. Bu not, gerçek indirilmiş verileri ve aşağıdaki resmî belgeleri denetler. Veri toplama yönteminin denetlenmesi ile tek tek sayının başka bir yayında eşleştirilmesi ayrı işlerdir; ikincisinin yapılmadığı yerler açıkça belirtilmiştir.

## 1. Konut faizi — TP.KTF12

- EVDS kataloğundaki ad: **Konut Kredisi (TL, Akım, %)**. Grup: `bie_kt100h`. Doğal sıklık: cuma tarihli haftalık gözlem.
- TCMB metaverisi 27 Nisan 2026 güncellemelidir. Seri **yıllık efektif faiz yüzdesidir**; haftalık yayımlanması faizin haftalık oran olduğu anlamına gelmez.
- Kapsam: mevduat bankaları ile kalkınma ve yatırım bankaları. Katılım bankaları bu tanımın içinde değildir.
- Yalnızca **sabit faizli konut kredileri** kapsanır; değişken faizli konut kredileri dahil değildir.
- Akım tanımı, ilgili haftada yeni kullandırılmış veya vade sonunda yenilenmiş hesaplardan haftanın son iş gününde bakiye veren hesapları kapsar. Dolayısıyla sadece ilk defa kredi çeken kişi sayısı veya kredi stoku değildir.
- Bankalar yıllık efektif oranları kredi tutarıyla ağırlıklandırır. Bizim haftaları aylara ayırarak aldığımız basit ortalama, **haftalık yayımlanmış yıllık oranların aylık basit ortalamasıdır**. Ayın toplam kullandırım tutarıyla ağırlıklı resmî faiz olduğunu iddia edemeyiz. Haftalık kredi hacmi ağırlıkları indirilmiş değildir.
- Ay sonu son gözlem ve ay içi ortalama farklı sorulara cevap verir; iki kolon ayrı isimlendirilmelidir. Oranları toplamayın. Faiz değişimini yüzde puanla ifade edin. Yıllık oranın aylık efektif eşdeğerini hesaplamak gerekiyorsa `(1+r/100)**(1/12)-1` ayrı bir türetilmiş göstergedir; aylara gruplama işlemi değildir.
- Mevsimsel düzeltme yapılmaz. Geçmiş veriler kaynak düzeltmeleriyle revize edilebilir. Metaveride yayımlama gecikmesi yaklaşık bir haftadır; hafta tarihi yayımlama tarihi değildir.
- BDDK tüm sektör stokları katılım bankalarını veya farklı para kapsamlarını içerebilir. Faiz ve stok aynı tabloda analiz edilebilir, fakat bire bir banka/kapsam eşitliği varsayılmamalıdır.
- Kataloğun `lastUpdated=22-12-2025` alanını son gözlem tarihi saymayın; indirilen gözlemler 2026'ya uzanıyor. Gerçek gözlem son tarihi ve indirme zamanı ayrı kaydedilmelidir.

Kaynaklar: [TCMB kredi faizi metaverisi](https://www.tcmb.gov.tr/wps/wcm/connect/33e09fa9-51fb-412f-b38d-0b7cdbaea493/Metaveri_Kredi_Ag%C4%B1rl%C4%B1kl%C4%B1_T%C3%BCrkce.pdf?MOD=AJPERES), [revizyon politikası](https://www.tcmb.gov.tr/wps/wcm/connect/3bcdf883-96d8-4395-9048-d948e6981eab/Revizyon%2BPolitikas%C4%B1.pdf?MOD=AJPERES), yerel resmî katalog `rates_search.json`.

## 2. Konut fiyat endeksi — TP.KFE.TR

- Mevcut temel yıl **2023=100**, sıklık aylıktır. 17 Şubat 2026 güncellemeli TCMB metaverisiyle doğrulandı.
- Konut kredisi başvuruları sırasında düzenlenen değerleme raporlarına dayalı, gözlemlenebilir kalite etkisinden arındırılmış **hedonik endekstir**. Kredinin kullandırılması veya satışın tamamlanması şart değildir. İlan fiyatı, konut satış adedi veya lira cinsinden ortalama ev fiyatı olarak yorumlanamaz.
- Temmuz 2024 yayınıyla üç aylık hareketli veri yerine aylık veri kullanıldı; modeller, bölgesel toplulaştırma ve ağırlıklandırma değişti. Tüm seri 2010'dan itibaren yeniden hesaplanarak 2023 temeline getirildi. Mevcut seri kullanılmalı; eski 2017 temelli arşiv değerleriyle uç uca eklenmemelidir.
- Güncel ağırlıklar hanehalkı adedi ve ortalama konut değerlerine dayanır. Mevsimsel düzeltme yapılmaz. Gecikmeli değerleme raporları/düzeltmelerle geçmiş değerler değişebilir; yaklaşık ilk yayın t+15 gün, nihai sonuç t+45 gündür.
- Hesaplanan 2023 aylık ortalaması **99.9991666667**; iki ondalıkla yayımlanan seviyelerin yuvarlama hassasiyetinde 100 ile tutarlı. Her 2023 ayının 100 olması beklenmez.
- TÜFE'ye göre reel fiyat göstergesi, aynı aya ait iki endeksin oranının seçilmiş bir başlangıç ayına göre yeniden 100'e getirilmesiyle oluşturulabilir. Farklı temel yıllardaki iki endeks seviyesi doğrudan çıkarılmamalıdır.

Kaynaklar: [KFE metaverisi](https://www.tcmb.gov.tr/wps/wcm/connect/b4628fa9-11a7-4426-aee6-dae67fc56200/KFE-Metaveri.pdf?MOD=AJPERES), [uygulama değişiklikleri](https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES).

## 3. TÜFE — TP.TUKFIY2025.GENEL

- Genel endeksin güncel temeli **2025=100**. Ocak 2026'dan itibaren temel yıl/sınıflama/ağırlık sistemi güncellendiği TÜİK duyurusuyla; fiilen yürürlükte olduğu güncel TÜİK bülteni arama kaydı ve TCMB'nin yayımladığı TÜFE tablosuyla doğrulandı.
- TÜİK, tarihsel seriyi zincir yapı korunarak yeni temele/sınıflamaya dönüştüreceğini; eski dönemin manşet enflasyonunun değişmeyeceğini, bazı alt endekslerde sınıflama farkı oluşabileceğini açıklıyor. Bu yüzden 2021'den itibaren güncel genel seri kullanılabilir. Eski 2003=100 seviyeleriyle 2025=100 seviyelerini doğrudan eklemek hatalıdır.
- Hesaplanan 2025 aylık ortalaması **99.9999999983**; 100 ile sayısal olarak tutarlı.
- `core_cpi.json` içindeki 2020 hazırlık verisi sayesinde, hedef **Ocak 2021–Haziran 2026** döneminin **66 aylık değişiminin ve 66 yıllık değişiminin tamamı** hesaplandı. TCMB'nin ayrı HTML sayfasında yayımlanan TÜİK oranlarıyla **132/132 kontrol iki ondalıkta eşleşti**; farklılık yok. Denetim satırları `methodology/cpi_change_checks.json` içinde.
- Örnek: Haziran 2026 endeks seviyelerinden türetilen yıllık artış %32.109036 ve aylık artış %0.986638; yayındaki %32.11 ve %0.99 ile eşleşiyor. Bunlar büyüme oranı kontrolüdür; endeksin mutlak seviyesinin farklı kurumca yeniden ölçülmesi değildir.

Kaynaklar: [TÜİK temel yıl duyurusu](https://www.tuik.gov.tr/media/announcements/TUFE_Duyuru30102025.pdf), [TCMB TÜFE değişim tablosu](https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Main%2BMenu/Istatistikler/Enflasyon%2BVerileri/Tuketici%2BFiyatlari), [TÜİK Haziran 2026 bülteni](https://veriportali.tuik.gov.tr/tr/press/58289/metadata). Sonuncusunun doğrudan sayfası JavaScript gerektiriyor; tam bülten dosyası indirildi denmiyor.

## 4. Sayı kontrolünün sınırı

| Dönem | TÜFE seviyesi | KFE seviyesi | Kontrol durumu |
|---|---:|---:|---|
| 2021-01 | 16.12513498 | 16.49 | EVDS ham yanıtında mevcut; TÜFE büyüme oranları ayrı resmî yayında eşleşti. |
| 2023-12 | 58.41175431 | 122.43 | EVDS ham yanıtında mevcut; TÜFE büyüme oranları ayrı resmî yayında eşleşti. |
| 2026-06 | 129.99 | 231.34 | EVDS ham yanıtında mevcut; TÜFE büyüme oranları ayrı resmî yayında eşleşti. |

KFE'nin bu üç tarihindeki mutlak seviyeleri ve faizin tarihsel gözlemleri, EVDS dışındaki ikinci resmî tabloda **bire bir doğrulanmış değildir**. Güncel KFE raporu Temmuz 2026'yı içerdiğinden Haziran seviyesini doğrudan doğruladığı söylenemez. Kaynak kimliği, tanım, zaman aralığı ve iç tutarlılık kontrolleri yapılmıştır; hiçbir seri için “her veri noktası bağımsız kaynaktan doğrulandı” iddiası yoktur.

Tarihsel veri şu anda erişilen güncel sürümdür. Bir tahmin yarışmasında geçmişteki bilgi seti taklit edilecekse yayın tarihleri ve tarihsel sürümler ayrıca gerekir. Veri referans ayı ile yayın ayını bir tutmak, daha sonra açıklanan bilgiyi geçmişte varmış gibi kullanmaya yol açar.

## Yerel kanıt ve tekrar çalıştırma

`methodology/source_manifest.json`: Yedi PDF ve bir HTML için kaynak URL, HTTP durum, MIME türü, gerçek indirme zamanı, byte ve SHA-256 kayıtları. Tüm sekiz kaynak indirmesi HTTP 200 döndü. `methodology/fetch_audit_sources.py` belgeleri yeniden indirebilir; güncel adreslerde içerik ileride değişebilir. `methodology/run_numeric_audit.py` mevcut yerel veriler üzerinden sayısal denetimi tekrar üretir. `metadata_audit.json` kısa makine okunur sonuçtur.
