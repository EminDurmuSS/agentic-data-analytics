"""Actual Kloudeks acceptance trials, isolated from the user's workspaces.

This collector preserves failures and records evidence, not correctness grades.
Run with a new output directory; independent oracles live beside its results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.context import AppContext
from agentic_analytics.providers.mia import MiaClient
from evals.consistency import backend_manifest, digest, write_json


CASES = {
    "pdf_sector": ["Şu yeni PDF raporunu çalışma alanına al: https://www.garantibbvainvestorrelations.com/en/images/pdf/31_March_2026_Consolidated_Financial_Report.pdf . Garanti grubunun 31 Mart 2026 konsolide toplam aktiflerini bul ve içerideki BDDK Mart 2026 bankacılık sektörü toplam aktifleriyle aynı analizde karşılaştır. Para birimi ve bin/milyon ölçeklerini eşitle, iki tutarı ve Garanti konsolide toplamının sektör toplamına oranını yüzde olarak hesapla ve grafik göster. Konsolide grup ile BDDK sektör kapsamının birebir eşdeğer olmadığını açıkça belirt; bu oranı resmi pazar payı olarak adlandırma. İki rakamı da kendi özgün kaynak hücrelerine bağla."],
    "profit": ["Bankacılık sektörünün Ocak-Haziran 2026 toplam net kârını, 2025'in aynı dönemiyle karşılaştır. İki yılın altı aylık ayrı aylık kârlarını milyon TL olarak tabloya koy, ilk altı ay toplamlarını ve yıllık yüzde değişimi söyle. Yılbaşından itibaren birikimli tutarları aylık kârla karıştırma."],
    "new_source": ["Yüklediğim {source_id} kaynak kimlikli deneme_bankasi.csv dosyası, tamamen kurgusal Deneme Bankası'nın Ocak-Mart 2026 ay sonu toplam nakdi kredi bakiyelerini içeriyor. Dosyadaki birim milyon TL. Bu yeni dosyayı çalışma alanına kat; aynı aylardaki BDDK bankacılık sektörü toplam nakdi kredi bakiyeleriyle tek tabloda karşılaştır. Banka bakiyesinin sektör bakiyesine oranını yüzde olarak hesapla ve bu payın çizgi grafiğini oluştur. Kaynakları ve bunun kurgusal örnek olduğunu belirt. Kapsamı farklı banka ve sektör verisini bu açık karşılaştırma amacıyla kullanıyorum."],
    "grouped": ["2026 Ocak-Mart için BDDK banka gruplarının ayrı aylık net kârlarını milyon TL olarak tabloya koy ve her grubun ayrı serisi olduğu çizgi grafiği oluştur. Birikimli değerleri ayrı aylık kâra çevir; grupları toplama.",
                "Aynı tablodaki bütün kaynak sütunları ve satırları koruyarak, her grubun bir önceki aya göre kâr farkını ayrı bir sütuna ekle. Bu fark sütununu grupların ayrı serileriyle çubuk grafikte göster.",
                "Aynı fark değerlerini bu kez ısı haritası olarak göster. Tablo, gruplar, dönemler ve değerler aynen kalsın."],
    "web": ["TCMB'nin 6 Mart 2025 tarihli Para Politikası Kurulu kararını web'de araştır. Bir hafta vadeli repo ihale faiz oranının önceki ve yeni değerlerini, karar tarihini ve resmi TCMB kaynak bağlantısını ver. Resmi karar sayfasını açarak doğrula."],
    "garanti_pdf": ["Bu raporu sisteme al: https://www.garantibbvainvestorrelations.com/en/images/pdf/31_March_2026_Consolidated_Financial_Report.pdf . Konsolide bilançodaki FINANCIAL ASSETS (Net) ve Cash and Cash Equivalents kalemlerinin 31 Mart 2026 ile 31 Aralık 2025 TOTAL değerlerini kaynak tablodan çıkar. TL ve FC alt sütunlarını toplam sanma. Raporun birimini koru, karşılaştırmayı kaydedilmiş tablo ve çubuk grafik olarak göster. Dönemleri ve sayfa kaynaklarını belirt."],
    "sisecam_pdf": ["Bu finansal raporu yeni veri kaynağı olarak ekle: https://www.sisecam.com/en/s-investor-relations/AuditedReports/Sisecam%2031.12.2025%20Consolidated.pdf . Revenue and Cost of Sales dipnotundaki 2025 ve 2024 revenue ile sales discounts değerlerini kaynak tablodan al, dönem karşılaştırma tablosu ve çubuk grafik oluştur. Rapordaki para birimi, ölçek ve satın alma gücü tarihini koru. İndirimlerin negatif işaretini ve dipnotun kaynak sayfasını doğrula."],
    "tupras_pdf": ["Bu raporu yeni veri kaynağı olarak sisteme ekle: https://www.tupras.com.tr/assets/uploads/financial-reports/tupras-consolited-cmb-30062025.pdf . Konsolide bilançodaki 30 Haziran 2025 tarihli Cash and cash equivalents ile Total assets tutarlarını kaynak tablodan çıkar. İki kalemi ayrı satırlarda içeren kaydedilmiş tablo ve çubuk grafik oluştur. Kaynaktaki para birimini, ölçeği ve satın alma gücü tarihini koru; kaynak sayfasını belirt."],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=["profit", "new_source"])
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "manifest.json").exists():
        parser.error("Choose a new output directory to preserve earlier evidence.")
    key = os.environ.get("MIA_API_KEY")
    if not key and (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.strip().startswith("MIA_API_KEY="):
                key = line.strip().split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
                break
    if not key:
        parser.error("MIA_API_KEY is not configured.")
    db = ROOT / "data_pipeline/lakehouse/analytics.duckdb"
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "backend_sha256": backend_manifest(ROOT),
        "database_sha256": digest(db), "cases": {key: CASES[key] for key in args.cases}, "repeats": args.repeats,
        "mode": "Actual AppContext and Kloudeks client. Fresh isolated workspaces; no browser claim. No evaluator hints or repairs.",
        "max_decisions": 18, "max_repairs": 4}
    write_json(out / "manifest.json", manifest)
    context = AppContext(out / "runtime", db, MiaClient(key))

    def trial(name, repeat):
        destination = out / f"{name}_{repeat}"
        report = {"case": name, "repeat": repeat, "turns": []}
        workspace = context.create_workspace(f"Mentor acceptance {name} {repeat}", "generic" if name.endswith("_pdf") else "finance")
        report["workspace"] = workspace
        wid = workspace["workspace_id"]
        substitutions = {}
        if name == "new_source":
            docs = context.documents(wid)
            uploaded = docs.upload_root / "deneme_bankasi.csv"
            uploaded.write_text("month,bank_credit (million TL)\n2026-01,100000\n2026-02,120000\n2026-03,150000\n")
            source = docs.register_upload(uploaded)
            report["upload"] = source
            substitutions["source_id"] = source["source_id"]
        conversation = None
        for number, template in enumerate(CASES[name], 1):
            prompt = template.format(**substitutions)
            turn = {"index": number, "prompt": prompt}
            report["turns"].append(turn)
            write_json(destination / "trial.json", report)
            started = time.monotonic()
            print(json.dumps({"event": "started", "case": name, "repeat": repeat, "turn": number}), flush=True)
            try:
                runtime = context.runtime(wid)
                turn["effective_limits"] = {name: getattr(runtime, name) for name in (
                    "max_decisions", "max_repairs", "max_context_chars", "max_elapsed_seconds")}
                result = runtime.run(prompt, conversation_id=conversation, request_id=f"mentor_{name}_{repeat}_{number}")
                turn["result"] = result
                conversation = result.get("conversation_id")
                write_json(destination / f"turn-{number}-journal.json", context.run_store.get(result["run_id"]))
                analyses = {}
                for item in result.get("tool_results", []):
                    aid = item.get("result", {}).get("analysis_id")
                    if aid and aid not in analyses:
                        frame, saved = context.store.load_analysis(aid)
                        analyses[aid] = {"manifest": saved, "rows": json.loads(frame.to_json(orient="records", force_ascii=False))}
                turn["analyses"] = analyses
            except Exception as exc:
                turn["collector_error"] = {"type": type(exc).__name__, "message": str(exc)}
            turn["elapsed_seconds"] = round(time.monotonic() - started, 3)
            write_json(destination / f"turn-{number}.json", turn)
            write_json(destination / "trial.json", report)
            result = turn.get("result", {})
            print(json.dumps({"event": "finished", "case": name, "repeat": repeat, "turn": number,
                "seconds": turn["elapsed_seconds"], "status": result.get("status"), "decisions": result.get("decisions"),
                "analysis": bool(result.get("analysis_id")), "chart": bool(result.get("chart_id")), "error": turn.get("collector_error", {}).get("type")}), flush=True)
            if not conversation:
                break
        return {"case": name, "repeat": repeat, "statuses": [t.get("result", {}).get("status") for t in report["turns"]]}

    summaries = []
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(trial, name, repeat) for name in args.cases for repeat in range(1, args.repeats + 1)]):
                summaries.append(future.result())
                write_json(out / "summary.json", summaries)
    finally:
        context.pool.shutdown(wait=True)
        manifest.update(finished_at=datetime.now(timezone.utc).isoformat(), backend_unchanged=backend_manifest(ROOT) == manifest["backend_sha256"],
                        database_unchanged=digest(db) == manifest["database_sha256"])
        write_json(out / "manifest.json", manifest)


if __name__ == "__main__":
    main()
