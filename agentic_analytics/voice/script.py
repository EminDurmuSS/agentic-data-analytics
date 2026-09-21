"""Constrained Qwen prompting and conservative validation for spoken Turkish text."""
from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP

from agentic_analytics.voice.context import VoiceBriefInput

MAX_SCRIPT_CHARS = 900
VOICE_SCRIPT_VERSION = 3
_URL = re.compile(r"https?://|```|[#*_`]")
_NUMBER = re.compile(r"\d[\d.,%]*")
_TURKISH_LIRA = re.compile(r"\bTL(?:(?:['’])(ye|ya|yi|yı|nin|nın|den|dan))?\b", re.IGNORECASE)
# These are machine-precision observations, not Turkish thousands formatting:
# e.g. ``88.57829117``.  A number with another separator following its decimal
# digits (such as ``598.085.487``) intentionally does not match.
_LONG_DECIMAL = re.compile(r"(?<![\d.,])(\d+)([.,])(\d{5,})(%?)(?![\d.,])")
_OPERATIONAL_FAILURE = re.compile(
    r"\b(?:elde\s+edileme|alınama|tamamlanama|başarısız|doğrulanama|bulunama|"
    r"ulaşılama|erişileme|erişilemi|üretileme|üretilme|hesaplanama|yapılama|oluşturulama)\w*|"
    r"oluşturulma(?:dı|mış)\w*|bağlantı\s+hatası|erişim\s+sınırı|"
    r"\b(?:MIA|PROVIDER_UNAVAILABLE|NO_READABLE_SOURCES)\b",
    re.IGNORECASE,
)


class VoiceScriptError(ValueError):
    pass


def _prompt(brief: VoiceBriefInput) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Sen kaynaklı finansal analiz sonucunu seslendirmek için kısa Türkçe metin yazarsın. Grafik bilgisi kanıt kapsülünde varsa metne tam olarak 'Grafik incelendiğinde,' diye başla; grafik yoksa bu ifadeyi kullanma. Grafikte veya analizde gerçek değerler varsa açıkla; yalnız kaynak bilgisi varsa doğrulanmış kaynak ve kapsam bilgisini anlat. Ses metni bir işlem veya hata raporu değildir. 'Sonuç alınamadı', 'analiz tamamlanamadı', 'veri bulunamadı', bağlantı hatası, erişim sınırı, MIA veya teknik hata kodları gibi ifadeleri seslendirme; bunlar yazılı sonuç ve teknik işlem kayıtlarında kalır. Somut bulgu yoksa yalnız incelemenin ayrıntılarının yazılı bölümden takip edilebileceğini söyle; başarı, tamamlanma, veri veya içgörü uydurma. Son cümlede yalnız kanıt kapsülündeki değerler destekliyorsa ve nedensellik iddia etmeden '... görülebilir.' biçiminde kısa bir içgörü sun. Yalnız verilen kanıt kapsülündeki gerçekleri kullan. Yeni hesap, sayı, tarih, kaynak, nedensellik veya öneri üretme. Sayı sözcüğüyle yazma. Beş veya daha fazla ondalık basamaklı makine hassasiyetindeki değerleri en fazla iki ondalığa yuvarla ve 'yaklaşık' diye belirt; diğer sayıları kapsüldeki biçimiyle yaz. Para birimi için 'TL' kısaltmasını yazma; her zaman 'Türk lirası' yaz. Gösterge adındaki parantez içi yer veya kod ifadesinden sonra bir nokta koy; sonraki sayısal değere yeni cümleyle geç. Bu yazılı cümle sonu seslendirmedeki kısa duraklamayı da oluşturur. Veriyi tüm dönemi temsil ediyormuş gibi sunma; yorumu yalnız mevcut gözlemlerle sınırla. Başlık, Markdown, URL ve kaynakça yazma."},
        {"role": "user", "content": "45 saniyeyi aşmayacak sade bir ses metni üret. Kanıt kapsülü:\n" + json.dumps(brief.public_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"))},
    ]


def _permitted_numbers(brief: VoiceBriefInput) -> set[str]:
    values = [brief.question, brief.answer, json.dumps(brief.analysis, ensure_ascii=False)]
    for fact in brief.facts:
        values.extend(str(fact.get(key, "")) for key in ("value", "period_start", "period_end"))
    permitted = set()
    for value in values:
        for token in _NUMBER.findall(value):
            permitted.add("".join(char for char in token if char.isdigit()))
            rounded = _normalise_spoken_decimals(token)
            permitted.update("".join(char for char in item if char.isdigit()) for item in _NUMBER.findall(rounded))
    return permitted


def _expand_turkish_lira(match: re.Match[str]) -> str:
    suffix = (match.group(1) or "").casefold()
    forms = {
        "ye": "Türk lirasına", "ya": "Türk lirasına",
        "yi": "Türk lirasını", "yı": "Türk lirasını",
        "nin": "Türk lirasının", "nın": "Türk lirasının",
        "den": "Türk lirasından", "dan": "Türk lirasından",
    }
    return forms.get(suffix, "Türk lirası")


def _normalise_spoken_decimals(value: str) -> str:
    """Round only overly precise decimal observations for clear Turkish speech."""
    def replace(match: re.Match[str]) -> str:
        number = Decimal(match.group(1) + "." + match.group(3)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rounded = format(number, ".2f").replace(".", ",")
        return "yaklaşık " + rounded + match.group(4)

    return _LONG_DECIMAL.sub(replace, value)


def _local_fallback_script(brief: VoiceBriefInput) -> str:
    """Describe only persisted metadata when the text model is unavailable."""
    if brief.analysis_id is None:
        return validate_voice_script(
            "İncelemenin ayrıntılarını yazılı bölümden takip edebilirsiniz.",
            brief,
        )
    period = brief.analysis.get("period") if isinstance(brief.analysis, dict) else None
    start = period.get("start") if isinstance(period, dict) else None
    end = period.get("end") if isinstance(period, dict) else None
    rows = brief.analysis.get("row_count") if isinstance(brief.analysis, dict) else None
    chart_title = brief.chart.get("title") if isinstance(brief.chart, dict) else ""
    subject = chart_title or "kaydedilmiş analiz"
    prefix = "Grafik incelendiğinde," if brief.chart else "Kaydedilmiş analiz incelendiğinde,"
    range_text = f" {start} ile {end} arasındaki" if start and end else ""
    row_text = f" {rows} kayıt" if isinstance(rows, int) else " kayıtlı veri"
    warning = " Bu özet yalnız ekranda gösterilen mevcut gözlemleri kapsar."
    return validate_voice_script(f"{prefix} {subject} için{range_text}{row_text} görüntülenmektedir.{warning}", brief)


def validate_voice_script(value: object, brief: VoiceBriefInput | None = None) -> str:
    if not isinstance(value, str):
        raise VoiceScriptError("Ses metni boş veya geçersiz.")
    text = " ".join(value.replace("\x00", " ").split())
    text = _TURKISH_LIRA.sub(_expand_turkish_lira, text)
    text = _normalise_spoken_decimals(text)
    if not 1 <= len(text) <= MAX_SCRIPT_CHARS or _URL.search(text):
        raise VoiceScriptError("Ses metni biçimi veya uzunluğu geçersiz.")
    if _OPERATIONAL_FAILURE.search(text):
        # Reject the complete script rather than deleting clauses that might
        # reverse its meaning. The fallback never claims a failed run succeeded.
        raise VoiceScriptError("Teknik işlem durumu seslendirme metnine dahil edilmez.")
    if not text.endswith((".", "!", "?", "…")):
        # A token-budget cutoff (or any other truncation) ends mid-sentence.
        # Speaking half a sentence is worse than falling back to the
        # deterministic local script, so reject it the same way as any other
        # malformed script.
        raise VoiceScriptError("Ses metni yarım cümleyle bitiyor.")
    if not re.search(r"[a-zçğıöşü]", text, re.IGNORECASE):
        raise VoiceScriptError("Ses metni Türkçe okunabilirlik koşulunu karşılamıyor.")
    if brief is not None:
        permitted = _permitted_numbers(brief)
        used = {"".join(char for char in token if char.isdigit()) for token in _NUMBER.findall(text)}
        if not used.issubset(permitted):
            raise VoiceScriptError("Ses metni kanıt kapsülünde olmayan bir sayı içeriyor.")
    return text


class VoiceScriptService:
    """Use the configured text model once; it has no tool access."""

    def __init__(self, client):
        self.client = client

    def generate(self, brief: VoiceBriefInput) -> str:
        return self.generate_record(brief)["transcript"]

    def generate_record(self, brief: VoiceBriefInput) -> dict[str, object]:
        prompt = _prompt(brief)
        try:
            if self.client is None or not callable(getattr(self.client, "chat", None)):
                raise VoiceScriptError("Ses metni modeli kullanılamıyor.")
            response = self.client.chat(prompt, temperature=0, max_tokens=420,
                                        tool_choice="none", enable_thinking=False)
            if not isinstance(response, dict):
                raise VoiceScriptError("Ses metni modeli geçersiz yanıt verdi.")
            tool_calls = response.get("tool_calls") or []
            if tool_calls:
                raise VoiceScriptError("Ses metni modeli araç çağrısı yapmamalı.")
            if response.get("finish_reason") == "length":
                # A response cut off by the token budget is a half sentence,
                # not a shorter valid script. Fall back rather than speak it.
                raise VoiceScriptError("Ses metni belirlenen sınırda tamamlanamadı.")
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            return {"transcript": validate_voice_script(response.get("content"), brief),
                    "prompt": prompt,
                    "model": response.get("model") if isinstance(response.get("model"), str) else None,
                    "usage": {key: value for key, value in usage.items() if isinstance(key, str) and isinstance(value, (int, float))},
                    "tool_steps": 0, "thinking_enabled": False, "fallback": False}
        except (ConnectionError, TimeoutError, OSError, RuntimeError, TypeError, ValueError):
            # The audio model is local. A text-provider outage must therefore
            # degrade wording only, never remove the already verified output.
            return {"transcript": _local_fallback_script(brief), "prompt": prompt,
                    "model": "yerel-yedek", "usage": {}, "tool_steps": 0,
                    "thinking_enabled": False, "fallback": True}
