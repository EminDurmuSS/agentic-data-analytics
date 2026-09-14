"""Constrained Qwen prompting and conservative validation for spoken Turkish text."""
from __future__ import annotations

import json
import re

from agentic_analytics.voice.context import VoiceBriefInput

MAX_SCRIPT_CHARS = 900
_URL = re.compile(r"https?://|```|[#*_`]")
_NUMBER = re.compile(r"\d[\d.,%]*")


class VoiceScriptError(ValueError):
    pass


def _prompt(brief: VoiceBriefInput) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Sen kaynaklı finansal analiz sonucunu seslendirmek için kısa Türkçe metin yazarsın. Yalnız verilen kanıt kapsülündeki gerçekleri kullan. Yeni hesap, sayı, tarih, kaynak, nedensellik veya öneri üretme. Sayı kullanırsan kapsüldeki biçimini aynen rakamla yaz; sayı sözcüğüyle yazma. Eksik veri ve kapsam uyarısını varsa söyle. Başlık, Markdown, URL ve kaynakça yazma."},
        {"role": "user", "content": "45 saniyeyi aşmayacak sade bir ses metni üret. Kanıt kapsülü:\n" + json.dumps(brief.public_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"))},
    ]


def _permitted_numbers(brief: VoiceBriefInput) -> set[str]:
    values = [brief.question, brief.answer, json.dumps(brief.analysis, ensure_ascii=False)]
    for fact in brief.facts:
        values.extend(str(fact.get(key, "")) for key in ("value", "period_start", "period_end"))
    return {"".join(char for char in token if char.isdigit()) for value in values for token in _NUMBER.findall(value)}


def validate_voice_script(value: object, brief: VoiceBriefInput | None = None) -> str:
    if not isinstance(value, str):
        raise VoiceScriptError("Ses metni boş veya geçersiz.")
    text = " ".join(value.replace("\x00", " ").split())
    if not 1 <= len(text) <= MAX_SCRIPT_CHARS or _URL.search(text):
        raise VoiceScriptError("Ses metni biçimi veya uzunluğu geçersiz.")
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
        if client is None or not callable(getattr(client, "chat", None)):
            raise VoiceScriptError("Ses metni modeli yapılandırılmamış.")
        self.client = client

    def generate(self, brief: VoiceBriefInput) -> str:
        return self.generate_record(brief)["transcript"]

    def generate_record(self, brief: VoiceBriefInput) -> dict[str, object]:
        prompt = _prompt(brief)
        response = self.client.chat(prompt, temperature=0, max_tokens=220,
                                    tool_choice="none", enable_thinking=False)
        if not isinstance(response, dict):
            raise VoiceScriptError("Ses metni modeli geçersiz yanıt verdi.")
        tool_calls = response.get("tool_calls") or []
        if tool_calls:
            raise VoiceScriptError("Ses metni modeli araç çağrısı yapmamalı.")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return {"transcript": validate_voice_script(response.get("content"), brief),
                "prompt": prompt,
                "model": response.get("model") if isinstance(response.get("model"), str) else None,
                "usage": {key: value for key, value in usage.items() if isinstance(key, str) and isinstance(value, (int, float))},
                "tool_steps": 0, "thinking_enabled": False}
