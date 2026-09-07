# BDDK erişim durumu, tarihsel hata kaydı

> Bu belge ilk çalışma ortamında görülen erişim hatasının tarihsel kaydıdır.
> Aynı resmî adresler daha sonra yerel geliştirme ortamından, TLS doğrulaması
> açıkken başarıyla indirildi. Güncel durumda BDDK aylık, haftalık ve FinTürk
> veri kapsamı tamamlandı. Ayrıntı için `data_pipeline/bddk/README.md` dosyasına
> bakın.

## İlk oturumdaki durum, 7 Eylül 2026

Normal HTTP isteklerinde doğrulanan sonuçlar:

- https://www.bddk.org.tr/BultenAylik/ : HTTP 502.
- https://www.bddk.gov.tr/BultenAylik/tr/Home/Gelismis : HTTP 502.
- Resmî arama indeksi erişilebildi; ilgili konut kalemi ve Excel aktarımı bulundu.

Normal tarayıcıda gelişmiş aylık sayfaya gitme isteği de yapıldı. Sayfa/DOM sonucu dönmedi; uzayan çağrı 726,4 saniye sonunda sonlandırıldı. Yönlendirmenin tamamlandığı ve herhangi bir dosyanın indiği doğrulanamadı. Görünür sayfa olmadığı için CAPTCHA, bot engeli veya sunucu arızası teşhisi konulmadı.

**BDDK'dan doğrudan aylık, haftalık veya FinTürk dosyası indirilemedi.** Verinin kamuya açık olması bu oturumdaki erişimin başarılı olduğunu göstermiyor. Erişim hatasının kurum sunucusundan mı ağ katmanından mı kaynaklandığı ayrıştırılamadı.

Konut bakiyesi analizini yapabilmek için TCMB'nin aylık para/banka ürününden 15 kredi serisi indirildi. Bunlar 78 ayı eksiksiz kapsıyor; yurt içi şube kapsamıyla BDDK bülteninden farklılaşabiliyor. Pakette TCMB serileri BDDK verisi olarak adlandırılmadı.

Bu belgede tarif edilen kalan işler daha sonra tamamlandı. Ham dosyalar,
istek bilgileri, filtreler, kaynak SHA-256 değerleri ve doğrulanmış işlenmiş
çıktılar `data_pipeline/bddk/` altında bulunur.
