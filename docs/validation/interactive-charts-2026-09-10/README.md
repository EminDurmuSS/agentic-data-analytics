# Etkileşimli grafikler ve ayarlanabilir çalışma alanı

Analiz ekranı artık tablonun ilk sayfasını çizmek yerine kayıtlı analizin bütün satırlarını kullanıyor. Grafik görünümü ayrı bir kayıt olarak saklanıyor; grafik türünü değiştirmek tabloyu, analiz kimliğini veya çalışma alanının veri sürümünü değiştirmiyor.

Konuşma ile analiz arasındaki ayıraç sağa ve sola sürüklenebiliyor. Seçilen oran tarayıcıda korunuyor; çift tıklama varsayılana dönüyor. Klavyede sol/sağ oklar, Home ve End destekleniyor. Mobilde paneller alt alta yerleşiyor ve ayıraç gizleniyor. Grafik, panel genişliği değiştikçe yeniden yerleşiyor.

## Kullanılabilen görünümler

- Çizgi, çubuk, alan ve dağılım grafikleri; gruplu analizlerde dönem/bölge ısı haritası.
- Birden çok seri, birimleri farklı olduğunda ayrı paneller, açıkça istendiğinde iki etiketli eksen.
- Ortak ilk dönem pozitif ve doluysa başlangıç=100 karşılaştırması. Bu yalnızca görünüme uygulanır, enflasyondan arındırma işlemi değildir.
- Türkçe dönem ve sayı gösterimi, seri açıklamaları, son gözlem kartları, eksik değer ve kaynak kapsamı notları.
- Yakınlaştırma, seri gizleme, geniş sunum görünümü ve başlık/birim/kaynak içeren PNG/SVG indirme.
- Bir noktadan özgün analiz değerine, oradan kaynak hücresine gitme. Normalize edilmiş değer ile özgün değer ayrı gösterilir.
- Mevcut sütunlara göre önerilen sonraki incelemeler. Öneriye tıklamak soruyu yazma alanına koyar; kullanıcı göndermeden yeni analiz başlatmaz.

Örnek istekler:

> Konut kredileriyle faizi ayrı panellerde çizgi grafik olarak göster.

> Konut kredisi ve konut fiyat endeksini Ocak 2021=100 başlangıcıyla karşılaştır. Tabloyu değiştirme.

> Faiz yatay eksende, kredi bakiyesi dikey eksende olsun. Dağılım grafiği çiz.

> Kredi bakiyesinin yıllık yüzde değişimini hesapla ve grafikte göster. Mevcut sütunları koru.

Grafik türü, seçili sütunlar, başlık, eksen düzeni ve normalizasyon arayüzden de değiştirilebilir. Desteklenmeyen seçenekler sessizce başka grafiğe çevrilmez; açıklanır veya doğrulamada reddedilir.

## Uygulama sözleşmesi

`tools/agent_charts.py` içindeki `ChartTools`, tam ve değişmez analiz kaydından doğrulanmış grafik verisi üretir. `create_chart` modeli bir grafik tarifine yönlendirir; modelden sayı dizisi, JavaScript, Python veya serbest grafik kodu kabul edilmez. Grafik içerik özetiyle saklanır ve aynı analizin son görünümü ayrı bir işaretçiyle seçilir. Önceki grafik kayıtları korunur.

`GET /api/workspaces/{workspace_id}/analyses/{analysis_id}/chart` son görünümü veya veri değiştirmeyen varsayılan görünümü döndürür. Aynı adrese `POST`, URL'deki analiz için yeni görünüm kaydeder. `GET /api/workspaces/{workspace_id}/charts/{chart_id}` değişmez görünümü açar. Bütün yollar çalışma alanı sahipliğini doğrular.

Runtime, veri değişikliğini `analysis_updated`, grafik değişikliğini `chart_updated` ile ayrı izler. Açık grafik oluşturma/değiştirme isteği, bu turda grafik kaydı oluşmadan başarıyla bitmiş sayılmaz. Grafik oluşturulduktan sonra tablo tekrar değişirse eski grafik bu şartı karşılamaz. Yeni sohbet bağlamı kayıtlı grafik tercihlerini içerir. Modelin aldığı kısa araç sonucu hem özgün hem normalize edilmiş hesaplanmış özetleri içerir.

Yalnızca görünüm değiştiren isteklerin tamamlanma mesajı, kayıtlı grafik tarifinden oluşturulur. Canlı kontrolde model doğru grafiğe rağmen en düşük bakiyeyi başlangıç bakiyesi olarak anlattı; bu yol artık modelden serbest sayısal yorum almıyor. Yeni hesaplama ve yorumlama isteyen analizlerin metin sentezi modelde kalır ve genel sayısal doğruluk kontrolleri gerektirir.

Grafik motoru, Apache-2.0 lisanslı **Apache ECharts 6.0.0**. Dağıtım dosyası, LICENSE, NOTICE ve SHA-256 manifesti `app/static/vendor/` altında; uygulama CDN gerektirmeden yerel dosyayı kullanır. [Lisans](https://github.com/apache/echarts/blob/6.0.0/LICENSE), [eksen ve yakınlaştırma belgeleri](https://echarts.apache.org/handbook/en/concepts/axis/).

## Doğrulama

Son sürümde ilgili beş test modülünün **60 testi ve 21 alt testi geçti**. Model çağrısı yapmayan bu kontroller; 250 satır sınırının aşılmasını, eksikleri, yanlış birimleri, geçersiz başlangıç değerlerini, büyük tam sayıları, analiz değişmezliğini, kayıtların yeniden açılmasını, çalışma alanları arasındaki erişim sınırlarını ve grafik onayına uydurma sayısal iddia taşınmamasını kapsıyor.

```sh
.venv/bin/python -m pytest \
  tests/test_agent_charts.py tests/test_agent_chart_workflow.py \
  tests/test_agent_runtime.py tests/test_agent_app.py \
  tests/test_agent_app_precision.py -q
```

İlk canlı MIA denemesinde gerçek 60 aylık BDDK/EVDS verisi üzerinde beş grafik isteğinin beşi karşılandı: ayrı paneller, başlangıç=100, dağılım, çift eksen ve yatay çubuk. Her turda bütün tablo değerleri, analiz başı ve çalışma alanı sürümü aynı kaldı. Ardından doğal Türkçe iki grafik isteği ve yıllık değişim önerisinin devamı denendi; hesaplama isteyen son adım, ilk 12 ayı boş bırakıp eski sütunları koruyarak yeni analize bağlı grafik oluşturdu. Tamamlanma mesajı düzeltmesi bir ek canlı denemeyle doğrulandı. Toplam **9/9 grafik sözleşmesi kontrolü** geçti; bu, serbest metnin bütün iddiaları için başarı puanı değildir. Yakalanan eski yorum hatası ve düzeltme ayrı kaydedildi. Ayrıntılar [results.json](results.json) dosyasında. Bu küçük örneklem, bütün olası grafik istekleri için başarı oranı değildir.

Chrome kontrolü; masaüstü ve 390 piksel mobil görünüm, sunum aç/kapat, gerçek grafik noktasından kaynak penceresi, önerinin yazma alanına taşınması, PNG/SVG indirme ve ayıraçla yeniden boyutlandırmayı kapsıyor. PNG indirme mevcut içerik güvenlik politikası altında çalışıyor. Gerçek FinTürk örneğinde 8 çeyrek, her çeyreğin ilk 10 kaydı, toplam 80 dolu hücre ve 11 farklı coğrafi kategori çizildi. Kaynaktaki `YURT DIŞI` kategorisi de korunuyor; boş sıralama hücreleri sıfır kabul edilmiyor.

[Ayıraç ve masaüstü](desktop-divider.png), [sunum görünümü](presentation.png), [mobil](mobile-panels.png), [ısı haritası](heatmap.png) ve [SVG dışa aktarımı](heatmap.svg) kaydedildi. [PNG indirme kontrolü](png-export-check.png), gerçek 60 satır üzerinde tarayıcıya özel yatay çubuk görünümüyle yapıldı; bu kontrol kayıtlı demo grafiğini değiştirmedi. Tam yerel canlı kayıtlar `tmp/chart-validation-2026-09-10/` altında tutulur; bu büyük kayıtlar ve veri kümesi Git'e dahil değildir.

## Kapsam sınırları

Bir grafik en fazla 10.000 analiz satırı ve 6 seçili seri kullanır. Sınır aşılırsa kullanıcıdan kapsamı daraltması istenir; sessiz örnekleme yapılmaz. Isı haritası kayıtlı grup/dönem boyutu gerektirir. Birimleri veya fiyat bazları farklı seriler tek eksene karıştırılmaz. İnceleme gerektiren seriler normalize edilmez. Tarayıcıda kayıpsız temsil edilemeyen tam sayılar grafik doğrulamasında reddedilir, tablo görünümü kendi kesin sayı sözleşmesini korur. Dağılım grafiği veya bir sonraki analiz önerisi nedensellik kanıtı değildir.
