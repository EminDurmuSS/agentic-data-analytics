"""Small interactive MIA experiments; no credential is persisted.

Run with the repository Python in a TTY. Supply the credential at the hidden
prompt, then JSON commands on stdin. MIA_API_KEY in the process environment is
also supported. Payloads and sanitized responses are research artifacts only.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib import error, request

ROOT = Path(__file__).resolve().parent
CHAT = "kkbhackathon2026/Qwen3.8-27B"
EMBED = "kkbhackathon2026/Qwen3-Embedding-8B"
OCR = "kkbhackathon2026/Unlimited-OCR"
BASE = "https://mia.csp.kloudeks.com/v1"


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_image():
    from PIL import Image, ImageDraw, ImageFont

    filename = ROOT / "synthetic-table.png"
    canvas = Image.new("RGB", (1100, 560), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    font = ImageFont.truetype(font_path, 34)
    heading = ImageFont.truetype(font_path, 40)
    draw.text((45, 35), "SENTETIK TEST TABLOSU", fill="black", font=heading)
    draw.text((45, 95), "Birim: milyon TL | Kaynak: yerel test", fill="black", font=font)
    rows = [("Donem", "Aylik net kar"), ("2026-01", "100"), ("2026-02", "120"), ("2026-03", "90")]
    for n, (period, value) in enumerate(rows):
        y = 160 + n * 78
        draw.rectangle((40, y, 1060, y + 78), outline="black", width=2)
        draw.line((550, y, 550, y + 78), fill="black", width=2)
        draw.text((65, y + 16), period, fill="black", font=font)
        draw.text((585, y + 16), value, fill="black", font=font)
    canvas.save(filename)
    return filename


def prepare(command):
    mode = command.get("mode", "chat")
    if mode in {"ocr", "vision"}:
        file = make_image()
        encoded = base64.b64encode(file.read_bytes()).decode("ascii")
        text = "<image>\ndocument parsing" if mode == "ocr" else command.get("prompt", "Görseldeki tablonun dönem, değer ve birimini JSON olarak çıkar. Hesap yapma.")
        payload = {
            "model": OCR if mode == "ocr" else CHAT,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                {"type": "text", "text": text},
            ]}],
            "temperature": 0.0,
            "max_tokens": 8192 if mode == "ocr" else 1536,
        }
        if mode == "ocr":
            # The OpenAI client's extra_body is flattened into the HTTP body.
            payload.update(skip_special_tokens=False, vllm_xargs={"ngram_size": 35, "window_size": 128})
        payload.update(command.get("overrides", {}))
        return "/chat/completions", payload
    payload = command["payload"]
    endpoint = "/embeddings" if mode == "embedding" else "/chat/completions"
    if payload.get("model") not in {CHAT, EMBED, OCR}:
        raise ValueError("Only the three guide model identifiers are allowed")
    return endpoint, payload


def main():
    key = os.environ.get("MIA_API_KEY")
    if not key:
        if not sys.stdin.isatty():
            raise SystemExit("A TTY or MIA_API_KEY is required")
        key = getpass.getpass("MIA API key (hidden): ").strip()
    if not key:
        raise SystemExit("Empty credential")
    opener = request.build_opener(NoRedirect)
    previous_file = ROOT / "results.json"
    records = json.loads(previous_file.read_text())["experiments"] if previous_file.exists() else []
    runtime_responses = {row["label"]: {"payload": row["request"], "body": row["response"]} for row in records if "response" in row}
    counter = len(records)

    def clean(value):
        if isinstance(value, dict):
            result = {}
            for field, item in value.items():
                if field in {"reasoning_content", "reasoning"}:
                    result[field + "_characters"] = len(item or "") if isinstance(item, str) or item is None else len(json.dumps(item))
                elif field == "embedding" and isinstance(item, list):
                    result["embedding_summary"] = {
                        "dimension": len(item), "finite": all(math.isfinite(float(v)) for v in item),
                        "l2_norm": math.sqrt(sum(float(v) ** 2 for v in item)), "first_values": item[:5],
                    }
                else:
                    result[field] = clean(item)
            return result
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            if value.startswith("data:image/"):
                return "<local image omitted; sha256=" + hashlib.sha256(value.encode()).hexdigest() + ">"
            return re.sub(r"sk-mia_[A-Za-z0-9_-]+", "<redacted>", value.replace(key, "<redacted>"))
        return value

    def save(record):
        records.append(clean(record))
        target = ROOT / "results.json"
        temp = ROOT / "results.json.tmp"
        temp.write_text(json.dumps({"base_url": BASE, "credential_persisted": False, "experiments": records}, ensure_ascii=False, indent=2) + "\n")
        temp.replace(target)

    print(json.dumps({"status": "ready", "credential_persisted": False}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        command = json.loads(line)
        if "file" in command:
            filename = ROOT / command["file"]
            if filename.resolve().parent != ROOT or filename.suffix != ".json":
                raise ValueError("Commands must be JSON files in the probe directory")
            command = json.loads(filename.read_text())
        if command.get("mode") == "exit":
            print(json.dumps({"status": "finished", "requests": counter}), flush=True)
            break
        if counter >= 24:
            print(json.dumps({"status": "request_budget_exhausted"}), flush=True)
            break
        label = command["label"]
        endpoint, payload = prepare(command)
        if "continue_from" in command:
            previous = runtime_responses[command["continue_from"]]
            prior = previous["payload"]
            assistant = previous["body"]["choices"][0]["message"]
            assistant_message = {k: assistant[k] for k in ("role", "content", "tool_calls") if k in assistant}
            # The controller validates the proposed call before submitting a
            # synthetic tool result. No provider-supplied code is executed.
            calls = assistant_message.get("tool_calls") or []
            expected = command["expected_call"]
            if len(calls) != 1 or calls[0]["function"]["name"] != expected["name"] or json.loads(calls[0]["function"]["arguments"]) != expected["arguments"]:
                print(json.dumps({"label": label, "status": "unexpected_tool_call"}), flush=True)
                continue
            payload["messages"] = prior["messages"] + [assistant_message, {"role": "tool", "tool_call_id": calls[0]["id"], "content": json.dumps(command["tool_result"], ensure_ascii=False)}]
        started = time.monotonic()
        record = {"label": label, "at_utc": datetime.now(timezone.utc).isoformat(), "endpoint": endpoint, "request": payload}
        counter += 1
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = request.Request(BASE + endpoint, data=data, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "AgenticMinds-MIA-Capability-Probe/1"})
            with opener.open(req, timeout=45) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ValueError("Response size limit exceeded")
                body = json.loads(raw)
                record.update(http_status=response.status, response=body)
                runtime_responses[label] = {"payload": payload, "body": body}
                if endpoint == "/embeddings":
                    ordered = sorted(body["data"], key=lambda item: item["index"])
                    vectors = [item["embedding"] for item in ordered]
                    cosine = lambda a, b: sum(x*y for x, y in zip(a,b)) / (math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)))
                    record["cosine_matrix"] = [[round(cosine(a,b), 6) for b in vectors] for a in vectors]
        except error.HTTPError as exc:
            raw = exc.read(8192).decode("utf-8", errors="replace")
            record.update(http_status=exc.code, error_body=raw)
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error_message=str(exc))
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save(record)
        output = clean({k: value for k, value in record.items() if k != "request"})
        print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
