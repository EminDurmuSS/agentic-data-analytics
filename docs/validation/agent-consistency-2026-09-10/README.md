# Canlı agent tutarlılık denemesi, 10 Eylül 2026

Mevcut uygulamada altı senaryo üçer kez, bağımsız çalışma alanlarında çalıştırıldı. Toplam 18 deneme ve 24 kullanıcı turu var. **Tam görev başarısı 8/18.** Bu ölçüt doğru tablo veya güvenlik kontrolünün yanında, istenen kaynak kanıtını ve doğru, kullanılabilir son cevabı da gerektiriyor.

Bu küçük başlangıç ölçümü, genel model doğruluğu veya yarışma puanı değildir. Aynı sayıları tekrar üretmek ile doğru ve eksiksiz cevap vermek ayrı değerlendirildi. Son metinler, bağımsız SQL ve ham kaynak doğruları karşısında Codex asistanı tarafından incelendi; kullanıcı incelemesi veya değerlendirilen modelin kendi puanlaması değildir. İncelemeler tam cevap metninin SHA-256 değeriyle bağlıdır.

## Sonuçlar

| Senaryo | Doğru tablo / güvenlik / araştırma çıktısı | Tam görev başarısı | Bulgu |
| --- | --- | --- | --- |
| Reel Kesim Güven Endeksi, Nisan-Haziran 2026 | 3/3 | 2/3 | Değerler aynı ve doğru. Bir denemede doğru tablo oluşmasına rağmen karar bütçesi doldu, son açıklama tamamlanmadı. |
| Aylık sektör net kârı ve 2026 ilk çeyrek toplamı | 3/3 | 3/3 | Birikimli kaynak değerleri aylık akıma doğru çevrildi. |
| KOBİ kredisi nominal ve reel yıllık büyüme | 2/3 | 1/3 | Bir deneme tablo üretmeden `completed` oldu. Başka denemede doğru tabloya yanıltıcı bir fiyat etkisi yorumu eklendi. |
| Müşteri sayısını TÜFE ile deflate etme isteği | 3/3 güvenlik | 0/3 | Hatalı parasal dönüşüm engellendi; kullanıcıya neden yapılamadığı açıklanmadı. |
| Konut tablosu, sabit fiyatlara dönüştürme, KFE ekleme | 3/3 | 2/3 | Üç turdaki 60 aylık tablolar doğru, korunması istenen hücreler aynı. Bir son yorum reel daralmayı yanlış anlattı. |
| TCMB 6 Mart 2025 kararını web'den doğrulama | 0/3 | 0/3 | Arama ve sayfa açma çağrıları yapıldı; ilgili resmi karar doğrulanamadı. |

Dört pozitif sayısal senaryoda 12 denemenin 11'i doğru tablo dizisini üretti. Bu sayı, son cevabın doğruluğunu içermez. Müşteri sayısı senaryosundaki 3/3, faydalı cevap başarısı değil, geçersiz dönüşümün yayımlanmaması anlamındadır. Web senaryosunda üç başarısızlık, tutarlı doğru sonuç sayılmadı.

## Yeniden üretilebilir doğrular

- Aylık net kâr: Ocak **87.249**, Şubat **82.152**, Mart **119.287 milyon TL**. İlk çeyrek **288.688 milyon TL**. Kaynakta birikimli tutarlar sırasıyla 87.249, 169.401 ve 288.688.
- KOBİ kredisi: Haziran 2025 **4.864.634**, Haziran 2026 **7.253.286 milyon TL**. Nominal büyüme **%49,102399**, reel büyüme **%12,863135**, kullanılan TÜFE artışı **%32,109036**.
- Mevsimsellikten arındırılmamış `TP.GY1.N2`: **100,6 / 103,3 / 103,5**. Katalogdaki dönüşüm kurallarının inceleme gerektirdiği uyarısı korunmalı.
- Konut kredisi, Ocak 2021 fiyatlarıyla: Ocak 2021 **276.785**, Aralık 2025 **99.182,753178 milyon TL**. Bu, yaklaşık **%64,17 reel daralma** demektir.
- TCMB'nin ilgili kararında bir hafta vadeli repo faizi **%45'ten %42,5'e** indirildi. Değerlendirici, erişilebilir resmi sayfayı ayrıca doğruladı; bu adres deneme sırasında agenta verilmedi. [TCMB, 6 Mart 2025 tarihli 2025-15 sayılı karar](https://tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Main%2BMenu/Duyurular/Basin/2025/DUY2025-15).

Kaynak kimlikleri, SQL yaklaşımı ve veri yorumlama sınırları [bağımsız doğrulama notlarında](oracle-notes.md). Tam sayısal diziler yerel `tmp/agent-consistency-2026-09-10/oracles.json` dosyasında; dosya hashleri [results.json](results.json) içinde kayıtlı.

## Öncelikli düzeltmeler

1. **Tamamlama kontrolü:** `kobi_growth_2`, analiz kaydı olmadan ve bozuk son metinle `completed` döndü. Tablo isteyen bir görevde yürütülmüş, okunabilir sonuç ve istenen çıktı bulunmadan başarı bildirilmemeli.
2. **Sayısal yorumların kanıta bağlanması:** `kobi_growth_3` nominal ve reel büyüme arasındaki yaklaşık 36,2 yüzde puan farkını, yüzde fiyat etkisi gibi anlattı. `housing_followup_3` ikinci turda %64,17 reel daralmayı “reel büyüme sınırlı kalmış” diye yorumladı. Doğru tablo, metindeki ilave iddiaları doğrulamıyor.
3. **Anlaşılır ret:** Müşteri sayısı para tutarı olmadığı için sabit fiyatlara çevrilemez. Birim kontrolünün doğru engellediği işlemler, genel hata mesajı yerine bu gerekçeyle açıklanmalı.
4. **Resmi sayfayı bulma:** Web denemeleri alakasız arama sonuçlarına ve tahmin edilen TCMB yollarında 404 hatalarına takıldı. Gerçek arama bağlantılarından ilgili belgeye ulaşma ve tarih/içerik doğrulaması güçlendirilmeli.
5. **Tekrarlanan keşif çağrıları:** `confidence_1` doğru tabloyu ürettiği halde keşif çağrılarıyla karar bütçesini tüketti. Elde edilen sonuç ve eksik teslimler izlenerek tekrarlar azaltılmalı.

Bu bulgular başlangıç ölçümüdür; bu çalışmada backend düzeltmesi uygulanmadı. Sonraki değişikliklerin etkisi aynı sorularla yeni çalışma alanlarında ölçülebilir.

## Yöntem ve sınırlar

- Kod tabanı: `c7f6aa6dfa6fc12ea8f23d87f68b5266f91eecb5`. Backend dosya hashleri ve aktif veritabanı hash'i çalışma boyunca değişmedi. Arayüz düzenlemesi yalnızca statik dosyalarda yapıldı.
- Çalışma aralığı: 10 Eylül 2026, **11:38:03-11:45:07 Türkiye saati**. İki paralel çalışan, her senaryoda üç aynı istem; konut senaryosu kendi içinde üç takip turu.
- Model: `kkbhackathon2026/Qwen3.8-27B`; `temperature=0`, `max_tokens=4096`, `enable_thinking=false`. Mevcut runtime sınırları: 10 karar, 2 onarım. Değerlendirici hatalı denemeleri tekrar çalıştırmadı veya yönlendirmedi.
- Yedi bütünlük kontrolü geçti: doğru veri anlık görüntüsü, bağımsız çalışma alanları, ayrı istek ve çalışma kimlikleri, sabit istemler, değişmeyen backend ve veritabanı.
- Sayısal değerlendirici araç önizlemeleri yerine kaydedilmiş tam sonuç satırlarını kullandı. Sütun takma adlarına değil metriklere, birimlere, boyutlara ve işlemlere göre karşılaştırdı. Konut takiplerinde korunacak hücreler tam eşitlikle kontrol edildi.
- Bağımsız doğrular oluşturulurken **84 farklı ham dosyanın SHA-256 değeri ve 464 kaynak hücresi** doğrulandı. Konutun 60 aylık bağımsız hesabı ile mevcut Gold çıktısı arasındaki en büyük fark sıfır.
- Üretilen **20 analizden seçilen 49 temsilî sonuç hücresi**, ayrıca gerçek kaynak zinciri üzerinden denetlendi. **9 ham dosya**, 107 kaynak hücresi başvurusu (16 benzersiz hücre) kontrol edildi; hata bulunmadı. Bu ikinci denetim tüm çıktı hücrelerinin ham kaynakta tek tek incelendiği anlamına gelmez. Analiz üretmeyen dört tur açıkça ayrı kaydedildi.
- Doğrulama kaydedilen yerel veri kümesine göredir; bütün verilerin güncel kamu yayınıyla aynı olduğunu kanıtlamaz. Üç tekrar geniş kapsamlı bir güvenilirlik tahmini sağlamaz.
- Sağlayıcının bildirdiği toplam kullanım: 1.845.083 giriş, 19.453 çıkış, **1.864.536 toplam token**. Ücret veya kota miktarı hakkında çıkarım yapılmadı.

## Kayıtlar ve tekrar çalıştırma

[results.json](results.json), senaryo sonuçlarını, son cevap incelemelerini, çalışma alanı bağlantılarını ve yerel kanıt dosyalarının SHA-256 değerlerini içerir. Tam çalışma kayıtları `tmp/agent-consistency-2026-09-10/live/` altında tutulur. Bu büyük yerel dosyalar ve aktif veri kümesi Git raporuna dahil değildir; yalnızca repoyu klonlamak bu denemeyi yeniden puanlamak için yeterli değildir.

Mevcut kayıtları tekrar puanlamak model çağrısı yapmaz:

```sh
.venv/bin/python -m tools.grade_agent_consistency \
  --input tmp/agent-consistency-2026-09-10/live \
  --oracles tmp/agent-consistency-2026-09-10/oracles.json \
  --answer-reviews tmp/agent-consistency-2026-09-10/answer-reviews.json \
  --output tmp/agent-consistency-2026-09-10/regraded.json
```

Yeni canlı ölçüm gerçek uygulamaya ve model sağlayıcısına çağrı yapar; uygulamanın ve verinin hazır olması gerekir. Var olmayan yeni bir çıktı klasörü kullanılmalı:

```sh
.venv/bin/python -m tools.evaluate_agent_consistency \
  --output tmp/agent-consistency-next/live --repeats 3 --workers 2
```

Yeni cevaplar için yeni, hash ile bağlı metin incelemesi gerekir. Veri anlık görüntüsü değişirse bağımsız doğrular da yeniden oluşturulmalı. Önceki cevap incelemeleri yeni cevapları otomatik olarak onaylamaz.
