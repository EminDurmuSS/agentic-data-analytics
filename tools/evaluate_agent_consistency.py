"""Collect repeatable live-agent evidence from the local application.

Runs real provider calls through the app, each trial in a fresh workspace.
This collector records outcomes, not correctness grades. Independent source
oracles and final-answer review are required before reporting success rates.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "confidence": [
        "Mevsimsellikten arındırılmamış Reel Kesim Güven Endeksi için Nisan, Mayıs ve Haziran 2026 değerlerini aylık tabloda göster. Dönüşüm uygulama. Haziran değerinin kaynak kaydını da belirt."
    ],
    "monthly_profit": [
        "Bankacılık sektörünün Ocak, Şubat ve Mart 2026 aylarında ayrı ayrı elde ettiği net kâr ne kadar? Aylık tutarları milyon TL olarak tabloya koy ve ilk çeyrek toplamını da söyle. Yılbaşından itibaren biriken tutarlarla aylık kârı karıştırma."
    ],
    "kobi_growth": [
        "Bankacılık sektörü toplam KOBİ nakdi kredi bakiyesi Haziran 2026'da Haziran 2025'e göre nominal ve TÜFE ile enflasyondan arındırılmış olarak yüzde kaç büyüdü? İki dönemin kredi tutarlarını milyon TL olarak, kullandığın TÜFE değerlerini ve iki büyüme oranını göster."
    ],
    "invalid_count_deflation": [
        "Bankacılık sektörü toplam KOBİ nakdi kredi müşteri sayılarını Haziran 2025 ve Haziran 2026 için al. Müşteri sayılarını TÜFE ile Haziran 2025 fiyatlarına indirip reel müşteri sayısı tablosu oluştur."
    ],
    "housing_followup": [
        "Ocak 2021-Aralık 2025 arasında bankacılık sektörü toplam konut kredisi bakiyesiyle konut kredisi faiz oranını aylık bir tabloda göster. Kredi tutarı milyon TL olsun; haftalık faizi aylık ortalamaya çevir. Kullandığın kaynakları belirt.",
        "Bu tablodaki kredi tutarı sütununu TÜFE kullanarak Ocak 2021 fiyatlarına dönüştürüp mevcut kredi sütununun yerine koy. Faiz sütunu ve tarih sırası aynı kalsın.",
        "Aynı tabloya Türkiye konut fiyat endeksini de ekle. Önceki bütün sütunları ve değerlerini koru."
    ],
    "web_research": [
        "TCMB'nin 6 Mart 2025 tarihli Para Politikası Kurulu kararını web'de araştır. Bir hafta vadeli repo ihale faiz oranının önceki ve yeni değerlerini, karar tarihini ve resmi TCMB kaynak bağlantısını ver. Resmi karar sayfasını açarak doğrula."
    ],
}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    staged.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8870")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--workers", type=int, default=2, choices=[1, 2])
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    target = urlsplit(base)
    if target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1", "::1"} or target.username or target.password:
        parser.error("Use a local application URL without credentials.")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "manifest.json").exists():
        parser.error("Output already has a run manifest; select a new directory to preserve previous trials.")
    print_lock = threading.Lock()

    def emit(event):
        with print_lock:
            print(json.dumps(event, ensure_ascii=False), flush=True)

    def call(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        request = Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    status = call("/api/status")
    if not status.get("provider_ready") or not status.get("finance_available"):
        raise RuntimeError("The local application needs a configured provider and finance snapshot.")
    product_files = ["app/server.py", "tools/agent_runtime.py", "tools/agent_run_store.py", "tools/agent_documents.py", "tools/agent_statistics.py", "tools/mia_client.py", "tools/lakehouse_service.py", "tools/lakehouse_store.py"]
    db = ROOT / "data_pipeline/lakehouse/analytics.duckdb"
    manifest = {
        "suite_id": uuid.uuid4().hex,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "backend_sha256": {name: digest(ROOT / name) for name in product_files},
        "database_sha256": digest(db),
        "models": status.get("models"), "base_url": base,
        "cases": {name: CASES[name] for name in args.cases},
        "repeats": args.repeats, "workers": args.workers,
        "rules": "Exact repeated prompts; fresh workspace/request IDs; no evaluator hints or repairs; same conversation only within housing follow-ups; preserve failed trials; final correctness graded independently.",
    }
    write_json(out / "manifest.json", manifest)

    def trial(case_id, repeat):
        trial_id = f"{case_id}_{repeat}"
        destination = out / trial_id
        report = {"trial_id": trial_id, "case_id": case_id, "repeat": repeat, "turns": []}
        started = time.monotonic()
        try:
            workspace = call("/api/workspaces", {"name": f"Tutarlılık {case_id} {repeat}", "profile": "finance"})
            report["workspace"] = workspace
            wid = workspace["workspace_id"]
            report["url"] = base + "/?workspace=" + wid
            conversation = None
            write_json(destination / "trial.json", report)
            for index, prompt in enumerate(CASES[case_id], 1):
                turn = {"index": index, "prompt": prompt}
                report["turns"].append(turn)
                body = {"message": prompt, "request_id": "eval_" + uuid.uuid4().hex}
                if conversation:
                    body["conversation_id"] = conversation
                job = call(f"/api/workspaces/{wid}/runs", body)
                turn["job"] = job
                write_json(destination / "trial.json", report)
                emit({"event": "started", "trial": trial_id, "turn": index, "job_id": job["job_id"]})
                began = time.monotonic()
                while True:
                    state = call("/api/jobs/" + job["job_id"])
                    if state["status"] not in {"queued", "running"}:
                        break
                    if time.monotonic() - began > 480:
                        raise TimeoutError("Trial polling deadline; original application job is preserved, not cancelled or retried.")
                    time.sleep(2)
                turn["elapsed_seconds"] = round(time.monotonic() - began, 3)
                turn["final_job"] = state
                result = state.get("result") or {}
                conversation = result.get("conversation_id") or (state.get("run") or {}).get("conversation_id")
                aid = result.get("analysis_id")
                if aid:
                    turn["analysis"] = call(f"/api/workspaces/{wid}/analyses/{aid}?limit=2000")
                write_json(destination / f"turn-{index}.json", turn)
                emit({"event": "finished", "trial": trial_id, "turn": index, "transport": state["status"], "runtime": result.get("status"), "seconds": turn["elapsed_seconds"], "decisions": result.get("decisions"), "analysis_id": aid})
                if state["status"] != "finished" or not conversation:
                    report["remaining_turns_not_run"] = len(CASES[case_id]) - index
                    break
            report["final_workspace"] = call(f"/api/workspaces/{wid}")
        except Exception as exc:
            report["collector_error"] = {"type": type(exc).__name__, "message": str(exc)}
            emit({"event": "collector_error", "trial": trial_id, "type": type(exc).__name__})
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(destination / "trial.json", report)
        return {"trial_id": trial_id, "workspace_id": report.get("workspace", {}).get("workspace_id"), "elapsed_seconds": report["elapsed_seconds"], "statuses": [t.get("final_job", {}).get("result", {}).get("status") for t in report["turns"]], "collector_error": report.get("collector_error")}

    summaries = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(trial, case_id, repeat) for repeat in range(1, args.repeats + 1) for case_id in args.cases]
        for future in as_completed(futures):
            summaries.append(future.result())
            write_json(out / "collection.json", {"trials": summaries, "scheduled": len(futures), "completed": len(summaries)})
    final_hashes = {name: digest(ROOT / name) for name in product_files}
    manifest.update(finished_at=datetime.now(timezone.utc).isoformat(), database_unchanged=digest(db) == manifest["database_sha256"], backend_unchanged=final_hashes == manifest["backend_sha256"])
    write_json(out / "manifest.json", manifest)
    emit({"event": "collection_complete", "trials": len(summaries), "output": str(out), "database_unchanged": manifest["database_unchanged"], "backend_unchanged": manifest["backend_unchanged"]})


if __name__ == "__main__":
    main()
