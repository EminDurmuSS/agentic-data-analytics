"""Small MIA HTTP adapter. Credentials and private reasoning never enter results."""
from __future__ import annotations

import base64
import json
import math
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


DEFAULT_MIA_BASE_URL = "https://mia.csp.kloudeks.com/v1"
DEFAULT_MIA_CHAT_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
DEFAULT_MIA_EMBEDDING_MODEL = "kkbhackathon2026/Qwen3-Embedding-8B"
DEFAULT_MIA_OCR_MODEL = "kkbhackathon2026/Unlimited-OCR"


def _configured(value, environment_name, default):
    if value is None:
        value = os.environ.get(environment_name) or default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{environment_name} must be a non-empty string")
    return value.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward the bearer header to a redirected host or endpoint.
        raise urllib.error.HTTPError(request.full_url, code, "Provider redirect refused", headers, fp)


class MiaError(RuntimeError):
    def __init__(self, code, message, *, retryable=False, status_code=None, attempts=None,
                 failure_kind=None, elapsed_ms=None):
        super().__init__(message)
        self.code, self.retryable, self.status_code = code, retryable, status_code
        self.attempts = attempts
        self.failure_kind, self.elapsed_ms = failure_kind, elapsed_ms


class MiaClient:
    def __init__(self, api_key, base_url=None, *, chat_model=None,
                 embedding_model=None, ocr_model=None, timeout=60,
                 max_retries=2, transport=None, sleeper=time.sleep):
        if not isinstance(api_key, str) or not api_key.strip():
            raise MiaError("MISSING_API_KEY", "MIA API anahtarı yapılandırılmamış.")
        base_url = _configured(base_url, "MIA_BASE_URL", DEFAULT_MIA_BASE_URL)
        chat_model = _configured(chat_model, "MIA_CHAT_MODEL", DEFAULT_MIA_CHAT_MODEL)
        embedding_model = _configured(
            embedding_model, "MIA_EMBEDDING_MODEL", DEFAULT_MIA_EMBEDDING_MODEL)
        ocr_model = _configured(ocr_model, "MIA_OCR_MODEL", DEFAULT_MIA_OCR_MODEL)
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an operator-configured HTTPS URL")
        if not 0 < timeout <= 180 or type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError("Invalid provider timeout/retry budget")
        self._key, self.base_url = api_key, base_url.rstrip("/")
        self.chat_model, self.embedding_model, self.ocr_model = chat_model, embedding_model, ocr_model
        self.timeout, self.max_retries = timeout, max_retries
        self._transport, self._sleep = transport, sleeper

    def __repr__(self):
        return f"MiaClient(base_url={self.base_url!r}, chat_model={self.chat_model!r})"

    def with_limits(self, *, timeout, max_retries=0):
        """Give optional background work its own budget without changing a run."""
        return MiaClient(self._key, self.base_url, chat_model=self.chat_model,
                         embedding_model=self.embedding_model, ocr_model=self.ocr_model,
                         timeout=timeout, max_retries=max_retries,
                         transport=self._transport, sleeper=self._sleep)

    def _post(self, endpoint, payload):
        started = time.monotonic()
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 24 * 1024 * 1024:
            raise MiaError("REQUEST_TOO_LARGE", "Model isteği boyut sınırını aşıyor.")
        for attempt in range(self.max_retries + 1):
            try:
                if self._transport:
                    result = self._transport(endpoint, payload)
                else:
                    request = urllib.request.Request(self.base_url + endpoint, data=encoded,
                        headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
                    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                        raw = response.read(24 * 1024 * 1024 + 1)
                    if len(raw) > 24 * 1024 * 1024:
                        raise MiaError("RESPONSE_TOO_LARGE", "Model yanıtı boyut sınırını aşıyor.")
                    result = json.loads(raw)
                if not isinstance(result, dict):
                    raise MiaError("INVALID_PROVIDER_RESPONSE", "Model yanıtı JSON nesnesi değil.")
                return {**result, "_request_meta": {"attempts": attempt + 1, "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}}
            except urllib.error.HTTPError as exc:
                status = exc.code
                retryable = status in {429, 500, 502, 503, 504}
                code = "PROVIDER_AUTH_ERROR" if status in {401, 403} else "PROVIDER_RATE_LIMIT" if status == 429 else "PROVIDER_HTTP_ERROR"
                error = MiaError(code, f"MIA isteği HTTP {status} ile tamamlanamadı.", retryable=retryable, status_code=status)
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                delay = min(float(retry_after), 10) if retry_after.replace(".", "", 1).isdigit() else min(2 ** attempt, 8)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                # Exception messages may contain URLs, credentials or payloads.
                # Persist only a closed set of diagnostic categories.
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                kind = ("timeout" if isinstance(cause, TimeoutError) else
                        "dns" if isinstance(cause, socket.gaierror) else
                        "tls" if isinstance(cause, ssl.SSLError) else
                        "connection" if isinstance(cause, ConnectionError) else "network")
                error = MiaError("PROVIDER_UNAVAILABLE", "MIA bağlantısı tamamlanamadı.",
                                 retryable=True, failure_kind=kind)
                delay = min(2 ** attempt, 8)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise MiaError("INVALID_PROVIDER_RESPONSE", "Model yanıtı geçerli JSON değil.") from None
            if not error.retryable or attempt == self.max_retries:
                error.attempts = attempt + 1
                error.elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                raise error from None
            self._sleep(delay)

    @staticmethod
    def _message(response):
        try:
            choice = response["choices"][0]
            raw = choice["message"]
            content = raw.get("content")
            if content is not None and not isinstance(content, str):
                raise TypeError
            calls = []
            for index, call in enumerate(raw.get("tool_calls") or []):
                function = call["function"]
                arguments = function["arguments"]
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
                if not isinstance(arguments, str) or not isinstance(function["name"], str):
                    raise TypeError
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    raise TypeError
                calls.append({"id": call_id, "type": "function", "function": {"name": function["name"], "arguments": arguments}})
            if len(calls) > 8 or len({call["id"] for call in calls}) != len(calls):
                raise TypeError
            # Probe P03 had valid native calls even with finish_reason=stop.
            return {"role": "assistant", "content": content, "tool_calls": calls,
                    "finish_reason": choice.get("finish_reason"), "usage": response.get("usage", {}),
                    "model": response.get("model"), "response_id": response.get("id"), "request_meta": response.get("_request_meta", {})}
        except (KeyError, IndexError, TypeError, ValueError):
            raise MiaError("INVALID_PROVIDER_RESPONSE", "Model yanıtının mesaj veya araç sözleşmesi geçersiz.") from None

    def chat(self, messages, tools=None, *, temperature=0, max_tokens=8192,
             response_format=None, tool_choice=None, enable_thinking: bool | None = None):
        if not isinstance(messages, list) or not 1 <= len(messages) <= 200:
            raise ValueError("messages must contain between 1 and 200 messages")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 16384:
            raise ValueError("Invalid output token budget")
        if enable_thinking is not None and type(enable_thinking) is not bool:
            raise ValueError("enable_thinking must be a boolean or None")
        # Pin decoding for run-to-run stability (no-op if the server ignores them).
        payload = {"model": self.chat_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "top_p": 1, "seed": 0}
        if enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        if tools:
            payload["tools"] = tools
        if response_format is not None:
            payload["response_format"] = response_format
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        return self._message(self._post("/chat/completions", payload))

    def embedding(self, inputs):
        values = [inputs] if isinstance(inputs, str) else inputs
        if not isinstance(values, list) or not 1 <= len(values) <= 64 or not all(isinstance(v, str) and 0 < len(v) <= 16000 for v in values):
            raise ValueError("Embedding input must be 1 to 64 bounded strings")
        raw = self._post("/embeddings", {"model": self.embedding_model, "input": values, "encoding_format": "float"})
        try:
            rows = sorted(raw["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in rows]
            if [row["index"] for row in rows] != list(range(len(values))) or not vectors or not vectors[0]:
                raise ValueError
            if any(len(v) != len(vectors[0]) or not all(type(x) in {int, float} and math.isfinite(x) for x in v) for v in vectors):
                raise ValueError
            return {"vectors": vectors, "dimension": len(vectors[0]), "usage": raw.get("usage", {}), "model": raw.get("model", self.embedding_model), "request_meta": raw.get("_request_meta", {})}
        except (KeyError, TypeError, ValueError):
            raise MiaError("INVALID_EMBEDDING_RESPONSE", "Embedding sırası veya sayısal değerleri geçersiz.") from None

    def image_chat(self, image_bytes, mime_type="image/png", *, ocr=False,
                   response_format=None, prompt="Belgedeki tabloyu tarih, sayı ve birimleriyle çıkar.", max_tokens=8192):
        if ocr:
            return self.ocr(image_bytes, max_tokens=max_tokens)
        if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= 8 * 1024 * 1024 or mime_type not in {"image/png", "image/jpeg"}:
            raise ValueError("Vision input must be bounded PNG/JPEG bytes")
        return self.chat([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64," + base64.b64encode(image_bytes).decode()}},
            {"type": "text", "text": prompt}]}], response_format=response_format, max_tokens=max_tokens)

    def ocr(self, images, *, max_tokens=8192):
        """Accept trusted image bytes, never model-selected paths or external URLs."""
        if isinstance(images, bytes):
            images = [images]
        if not isinstance(images, list) or not 1 <= len(images) <= 3 or not all(isinstance(v, bytes) and 0 < len(v) <= 8 * 1024 * 1024 for v in images):
            raise ValueError("OCR accepts one to three bounded PNG/JPEG byte strings")
        content = []
        for data in images:
            mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff") else None
            if not mime:
                raise ValueError("OCR image signature must be PNG or JPEG")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}})
        content.append({"type": "text", "text": "<image>\ndocument parsing"})
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("Invalid OCR token limit")
        result = self._message(self._post("/chat/completions", {"model": self.ocr_model,
            "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
            "temperature": 0, "skip_special_tokens": False,
            "vllm_xargs": {"ngram_size": 35, "window_size": 128 if len(images) == 1 else 1024}}))
        result["requires_content_validation"] = True
        result["truncated"] = result["finish_reason"] == "length"
        return result
