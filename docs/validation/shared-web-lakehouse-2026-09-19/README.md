# Shared web lakehouse doğrulaması, 19-20 Eylül 2026

Bu kayıt, resmî web kaynağından yayımlanmış bir workspace dataset'inin kalıcı ortak lakehouse release'ine alınması değişikliğini doğrular.

## Doğrulanan davranışlar

- Resmî HTTP(S) kaynağı, ham dosya ve inspection kanıtıyla içerik adresli promotion paketine alınır.
- Dataset manifesti, kaynak CSV'si, Parquet verisi ve özgün ham belge her okumada SHA-256 ile doğrulanır.
- Upload, tanınmayan alan adı, eksik belge provenance'ı ve anlamı `unknown` kalan sayısal sütun reddedilir.
- Aynı terfi farklı açıklamayla tekrarlandığında yeni promotion veya release üretilmez.
- Eşzamanlı iki terfi aktif release'te veri kaybı oluşturmadan birleşir.
- Promotion paketi yazıldıktan sonra süreç kesilirse recovery release'i tamamlar ve aynı yazmayı tekrarlamaz.
- Kaynak workspace ve belge klasörü silinse de paket içindeki ham kanıt ile dataset kullanılabilir kalır.
- Yeni finance workspace aktif release'i alır; mevcut ve generic workspace'ler değişmez.
- Yeni workspace veriyi `discover`, `describe`, `execute` ve `explain_value` ile kullanabilir.
- Global yazma aracı sıradan araştırma sorularında modele gösterilmez ve enjekte edilmiş yetkisiz çağrı dispatch aşamasında engellenir.
- Olumsuz talep, yetenek sorusu veya yalnız kullanım açıklaması isteyen ifade global yazma yetkisi vermez; Türkçe ve İngilizce açık olumlu talep örnekleri ayrı test edilir.
- İnsan incelemesi kanıtı yalnız bağlandığı kaynak ve tablo için geçerlidir; başka bir tablonun review kaydı terfi kapısını açamaz. Hazırlanmış bir tablonun kendi kaynak tablosuna bağlı geçerli review zinciri korunur.
- Yerel HTTP smoke testinde uygulama gerçek finans veritabanıyla başlatıldı; yeni finance workspace promoted dataset'i aldı, generic workspace almadı ve metrik keşif API'sinde bulundu.

## Test sonucu

Repo kökünde aşağıdaki komut çalıştırıldı:

```sh
.venv/bin/pytest -q
```

20 Eylül 2026 tarihli son tam koşu sonucu: **1.202 test geçti, 346 alt test geçti, 5 test atlandı**. Süre 296,84 saniyeydi. Atlanan testler başarılı sayılmadı. Koşuda iki bağımlılık deprecation uyarısı ve iki mevcut pandas `PeriodDtype[B]` gelecek sürüm uyarısı görüldü; yeni shared lakehouse akışına ait hata veya uyarı oluşmadı.

Bu koşu yerel ve scripted entegrasyonları kapsar. Canlı model sağlayıcısı veya gerçek dış ağ kaynağı çağrısı yapılmadı.
