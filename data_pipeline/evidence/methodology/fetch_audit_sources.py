"""Download public official methodology and record response provenance."""
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = {
    "kfe_metadata.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/b4628fa9-11a7-4426-aee6-dae67fc56200/KFE-Metaveri.pdf?MOD=AJPERES",
    "kfe_changes.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/KFE-Uygulama-Degisiklikleri.pdf?CACHEID=ROOTWORKSPACE-e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd-pNGpxCX&MOD=AJPERES",
    "housing_rate_metadata.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/33e09fa9-51fb-412f-b38d-0b7cdbaea493/Metaveri_Kredi_Ag%C4%B1rl%C4%B1kl%C4%B1_T%C3%BCrkce.pdf?CACHEID=ROOTWORKSPACE-33e09fa9-51fb-412f-b38d-0b7cdbaea493-pTj0rnn&MOD=AJPERES",
    "housing_rate_changes.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/85c6d28a-d4c0-4df2-8c13-e05e111984ff/Uygulama%2BDe%C4%9Fi%C5%9Fiklikleri_FaizIst_Kredi.pdf?CACHEID=ROOTWORKSPACE-85c6d28a-d4c0-4df2-8c13-e05e111984ff-pku1hVJ&MOD=AJPERES",
    "housing_rate_revision.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/3bcdf883-96d8-4395-9048-d948e6981eab/Revizyon%2BPolitikas%C4%B1.pdf?CACHEID=ROOTWORKSPACE-3bcdf883-96d8-4395-9048-d948e6981eab-pkTRZUO&MOD=AJPERES",
    "cpi_rebase_announcement.pdf": "https://www.tuik.gov.tr/media/announcements/TUFE_Duyuru30102025.pdf",
    "kfe_latest_report.pdf": "https://www.tcmb.gov.tr/wps/wcm/connect/8bbac42a-c854-4c58-8b0c-e7e55c35ec2d/KFE.pdf?CACHEID=ROOTWORKSPACE-8bbac42a-c854-4c58-8b0c-e7e55c35ec2d-q0jGUg5&MOD=AJPERES",
    "cpi_official_changes.html": "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/Main%2BMenu/Istatistikler/Enflasyon%2BVerileri/Tuketici%2BFiyatlari",
}

def fetch(entry):
    filename, url = entry
    result = {"filename": filename, "url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (public-data research)"})
        with urllib.request.urlopen(req, timeout=20) as response:
            data = response.read()
            result.update(status=response.status, content_type=response.headers.get("Content-Type"), final_url=response.url)
        if filename.endswith(".pdf") and not data.startswith(b"%PDF"):
            raise ValueError("Unexpected non-PDF response")
        (ROOT / filename).write_bytes(data)
        result.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    except Exception as exc:
        result["error"] = str(exc)
    return result

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, SOURCES.items()))
    (ROOT / "source_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))
