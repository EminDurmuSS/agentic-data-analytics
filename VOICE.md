# Yerel Sesli Özet Planı

## Karar özeti

Bu özellik, tamamlanmış ve kaynaklı analiz sonucunu kısa bir Türkçe sesli özete
dönüştürür. Akışta mevcut Kloudeks/Qwen modeli yalnızca **metin özeti** üretir;
ses tanıma ve ses sentezi için hiçbir bulut Voice API kullanılmaz. Ses üretimi
uygulamanın çalıştığı makinede gerçekleşir.

> **Kapsam:** Bu özellik yalnızca yazılı analiz sonucunu yerelde üretilen sese
> dönüştürür. Mikrofon, ses kaydı, ses-metin (STT) ve Whisper kullanılmaz.

## Uygulanacak mimari

Kullanıcının istediği ağaç fikrini koruyoruz: analiz ekranındaki her kanıt
kümesi Qwen'e giden bir dal olur, Qwen'den ise tek bir ses metni çıkar ve tek
bir yerel TTS düğümüne gider. Bunun önünde küçük, deterministik bir
`VoiceContextBuilder` bulunur. Bu katman ham SQL, araç argümanları, modelin
iç düşüncesi, API anahtarları ve işlenmemiş/çok büyük tabloların Qwen'e
gitmesini engeller. Böylece "tüm veri" değil, ekranda görünen ve izlenebilir
**kanıt özeti** gönderilir.

![Yerel sesli özet mimarisi](docs/voice-pipeline.svg)

Bu gerçek SVG dosyasıdır ve Graphviz ile üretildi. Uygulama PR'ında değişirse
aynı mimari güncellenerek SVG yeniden üretilecektir.

## Mevcut kod tabanından alınacak çıktılar

Görselde işaretlenen alanların yanı sıra kod tabanındaki veri akışını da
kontrol ettim. Ses özeti aşağıdaki kayıtlı çıktıları kullanır:

1. Kullanıcının sorusu ve agent'ın tamamlanmış Türkçe metin cevabı.
2. Kaydedilmiş analizin tablo şeması, dönem alanı, satır sayısı, sayısal
   sütunları ve türetilmiş kısa istatistikleri. Ham tablonun tamamı LLM'e
   gönderilmez.
3. Grafik başlığı, seçili seri(ler), grafik türü, KPI'lar, uyarılar ve
   öneriler. Grafik yalnız görünüm bilgisidir; değerlerin kaynağı yine
   kaydedilmiş analizdir.
4. Kaynak izi: metrik başlığı, kaynak sistemi, birim, ölçek, dönem aralığı,
   `snapshot_id` ve analiz satırlarının doğrulanmış veri özeti.
5. Hesap planı: seçili metrikler, tarih aralığı, frekans ve yapılan
   `growth`/`difference`/`deflate` gibi işlemlerin insan okunur özeti.
6. Varsa istatistik artefaktları: anomali, korelasyon, Granger ve kırılma
   sonuçları. Nedensellik yalnız mevcut istatistik kaydı bunu destekliyorsa
   söylenir.
7. Kalıcı agent olayları: çalışma tamamlandı mı, araç hatası/kurtarma oldu mu.
   Araç argümanları, SQL ve model düşüncesi alınmaz.
8. Kullanıcının yüklediği/incelettiği belge kaynakları varsa yalnız doğrulanmış
   kaynak adı ve daha önce cevapta kullanılmasına izin verilmiş alıntı özeti.

## Ses metni sözleşmesi

`VoiceContextBuilder` yukarıdaki kayıtlı nesnelerden sürümlü bir
`VoiceBriefInput` üretir. Bu nesne UI'da görünen gerçek analizden türemeli ve
şunları taşımalıdır: `analysis_id`, soru, cevap özeti, dönem aralığı,
metrik/birim, başlangıç-son değer, değişim, uyarılar, kaynak etiketleri ve
istatistik kanıtı. Değer yoksa alan boş kalır; sıfır veya ortalama uydurulmaz.

Qwen'e verilecek talimat şunları zorunlu kılar:

- 45 saniyeyi aşmayan, doğal Türkçe bir özet yaz.
- Yalnız `VoiceBriefInput` içindeki sayıları, birimleri ve kaynakları kullan.
- Nedensellik, kesinlik veya trend iddiası ekleme.
- Eksik değer, farklı birim veya sınırlı kapsam uyarısını sesli söyle.
- Başlık, Markdown, kod, URL ve "kaynakça" listesi yazma; seslendirmeye
  uygun düz metin üret.

Qwen çıktısı tekrar doğrulanır: sayı/birim eşleşmesi, izinli kaynaklar ve
azami karakter/süre sınırı kontrol edilir. Geçmezse ses dosyası üretilmez;
UI, yazılı analiz sonucunu koruyup kısa hata mesajı gösterir.

Sonuç paneline küçük bir **Sesli özet** kartı eklenir: oynat/durdur, indirme
değil ilk sürümde yalnız oynatma, "Bu ses yerelde üretildi" etiketi ve
altında tam seslendirilmiş metin. Bu metin TTS'nin aldığı kesin metindir.

## Model seçimi - Türkçe için sıralama

Bir modeli "en iyi" ilan etmek için kendi finansal terimlerimizle yerel bir
dinleme ve doğruluk testi gerekir. Bu nedenle sıralama, lisans + Türkçe
desteği + çalıştırma maliyeti + ürün olgunluğuna göre başlangıç sıralamasıdır.

| Sıra | İş | Aday | Neden | Karar |
| --- | --- | --- | --- | --- |
| 1 | TTS | [Piper Türkçe sesleri](https://github.com/rhasspy/piper/blob/master/VOICES.md) | `tr_TR` için `dfki`, `fahrettin`, `fettah` medium sesleri var; ONNX/CPU ile hafif ve düşük gecikmeli. | İlk sesli yanıt sürümünün yerel TTS'si. Ses modelinin tekil lisansı ayrıca doğrulanır. |
| 2 | TTS | [XTTS-v2](https://github.com/DrewThomasson/coqui-ai-TTS/blob/dev/docs/source/models/xtts.md) | Türkçeyi destekleyen çok dilli, daha ifadeli aday. | Varsayılan değil: Coqui Public Model License ayrı hukuk değerlendirmesi ve ses klonlama için açık rıza ister. |
Piper ana deposu arşivlenmiş olduğu için, plan uygulanırken sabit sürüm ve
model dosyasının ayrı lisansı kayda alınacaktır. Demo öncesi finansal terimler
ve sayılarla mutlaka dinleme testi yapılacaktır. Kaynaklar:
[Piper yerel TTS ve lisansı](https://github.com/rhasspy/piper),
[Piper Türkçe ses listesi](https://github.com/rhasspy/piper/blob/master/VOICES.md),
[XTTS-v2 dilleri/lisansı](https://github.com/DrewThomasson/coqui-ai-TTS/blob/dev/docs/source/models/xtts.md).

`gTTS` ve `edge-tts` bu tasarımda kullanılmayacaktır: açık kaynak Python
paketi olsalar da ses üretimini Google/Microsoft servislerine yaptırırlar;
yarışmadaki "haricî bulut Voice API yasak" koşulunu karşılamazlar.

## Aşamalı uygulama planı

1. **Sözleşme ve güvenlik:** `VoiceBriefInput`, redaksiyon, sayısal doğrulama,
   süre sınırı ve ses artefakt yaşam döngüsünü ekle. Yalnız tamamlanmış,
   kaynaklı analizler seslendirilebilir.
2. **Yerel TTS POC:** Piper ile tek bir arka plan işçisi kur. Model
   ağırlıkları imaj/volume için sabit sürümle sağlanır;
   istek sırasında haricî ses servisine ağ çağrısı yapılamaz.
3. **Qwen özetleme:** Yeni, dar bir ses-özet promptu ve çıktı doğrulayıcısını
   ekle. Qwen başarısızsa ham uzun analizi seslendirmek yerine kullanıcıya
   güvenli hata göster.
4. **UI ve erişilebilirlik:** Sonuç panelindeki Sesli özet kartı, `audio`
   kontrolü, metin transkripti, yükleniyor/hata durumları ve klavye erişimini
   ekle. Aynı analiz tekrar açıldığında kayıtlı metin/artefakt doğrulanır.

## Başarı ve test ölçütleri

- Sabit bir analiz fikstüründe üretilen ses metnindeki her sayı, birim ve
  kaynak `VoiceBriefInput` ile eşleşir; ek sayı bulunmaz.
- Türkçe finans terimleri ("mevduat", "çeyrek", "yüzde puan", "milyar TL")
  için Piper dinleme kaydı yapılır; telaffuz sorunları kayıt altına alınır.
- TTS isteği sırasında ağ çıkışı yapılmadığı entegrasyon testinde doğrulanır.
- Ses dosyası, çalışma alanı ve analiz kimliğiyle bağlanır; başka çalışma
  alanından okunamaz.
- Qwen/TTS başarısız olduğunda tablo, grafik, kaynak ve yazılı cevap bozulmaz.

## Kapsam dışı

İlk sürümde kişiye ait sesi klonlama, mikrofon/ses kaydı, ses-metin, gerçek
zamanlı iki yönlü konuşma, haricî Voice API, ses dosyasını kalıcı olarak
herkese açık paylaşma ve modelin ses metninde yeni analiz yapması yoktur.
