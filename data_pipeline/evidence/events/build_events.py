#!/usr/bin/env python3
"""Build the verified housing-credit policy and methodology event dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
RESEARCH_AS_OF = "2026-09-07"


def document(
    source_url: str,
    local_file: str,
    pages: list[int],
) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "local_file": local_file,
        "http_status": 200,
        "pdf_pages_1based": pages,
        "full_document_downloaded": True,
    }


BDDK_10249_URL = "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1126"
BDDK_10525_URL = "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1164"
BDDK_10656_URL = "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1191"
BDDK_11364_URL = "https://www.bddk.org.tr/Mevzuat/DokumanGetir/1327"
KFE_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/"
    "e7fe7b68-74a3-4162-ae3b-bbf40d0b26fd/"
    "KFE-Uygulama-Degisiklikleri.pdf?MOD=AJPERES"
)
IR_2022_URL = (
    "https://tcmb.gov.tr/wps/wcm/connect/"
    "5d6d752d-ef72-4d2a-afd6-a132c2f6d7b9/"
    "enftemmuz22_iii_tam.pdf?MOD=AJPERES"
)
FSR_2025_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/"
    "72d745d3-f8b1-404d-bc65-04b9469084f3/"
    "Tam%2BMetin.pdf?MOD=AJPERES"
)
IR_2024_URL = (
    "https://www.tcmb.gov.tr/wps/wcm/connect/"
    "ee67cf46-654c-449a-8acc-d8bbcca93f45/"
    "Kutu_2_4_2024_i.pdf?MOD=AJPERES"
)
KFE_ANNOUNCEMENT_URL = (
    "https://tcmb.gov.tr/wps/wcm/connect/TR/TCMB%2BTR/"
    "Main%2BMenu/Duyurular/Basin/2024/DUY2024-44"
)


EVENTS: list[dict[str, Any]] = [
    {
        "event_id": "BDDK_10249_20220623",
        "decision_date": "2022-06-23",
        "effective_date": None,
        "publication_date": None,
        "annotation_date": "2022-06-23",
        "annotation_date_type": "decision_date",
        "event_type": "housing_credit_regulation",
        "source_url": BDDK_10249_URL,
        "title": "10249 sayılı konut kredisi kredi/değer oranı düzenlemesi",
        "verified_summary_tr": (
            "23 Haziran 2022 tarihli 10249 sayılı karar, konut kredisi ve konut "
            "teminatlı kredilerde azami kredi tutarını konut değeri, enerji sınıfı "
            "ve birinci veya ikinci el niteliğine göre farklılaştırdı. Karardaki "
            "iki oran ve tutar tablosu resmî tam metinden doğrulandı."
        ),
        "analysis_hypothesis_tr": (
            "Daha yüksek peşinat ihtiyacı bazı alıcılarda faiz düşüşünün kredi "
            "kullanımı üzerindeki etkisini sınırlayabilir. Bu bir araştırma "
            "hipotezidir; toplam zaman serisinden kararın nedensel etkisi "
            "hesaplanmış değildir."
        ),
        "verification_level": "downloaded_primary_fulltext_and_official_corroboration",
        "evidence_access": {
            "decision_document": document(BDDK_10249_URL, "bddk_10249.pdf", [1, 2]),
            "downloaded_corroboration": [
                document(IR_2022_URL, "cb_ir2022iii.pdf", [15, 26])
            ],
        },
        "timing_note_tr": (
            "Karar tarihi tam metinden doğrulandı. Belgede ayrı bir yürürlük tarihi "
            "belirtilmediği için effective_date boştur."
        ),
    },
    {
        "event_id": "BDDK_10525_20230224",
        "decision_date": "2023-02-24",
        "effective_date": None,
        "publication_date": None,
        "annotation_date": "2023-02-24",
        "annotation_date_type": "decision_date",
        "event_type": "housing_credit_regulation",
        "source_url": BDDK_10525_URL,
        "title": "10525 sayılı konut kredisi oran ve azami tutar kararı",
        "verified_summary_tr": (
            "24 Şubat 2023 tarihli 10525 sayılı karar, birinci ve ikinci el "
            "konutlarda değer ve enerji sınıfına göre uygulanacak kredi/değer "
            "oranlarını ve bazı dilimlerde azami kredi tutarlarını belirledi. Yeni "
            "Konut Finansmanı Programı kapsamındaki kredileri karar dışında tuttu. "
            "Tablolar ve istisna resmî tam metinden doğrulandı."
        ),
        "analysis_hypothesis_tr": (
            "Değer eşikleri ve kredi oranları değişirken aynı faiz düzeyi farklı "
            "konut segmentlerinde farklı finansman erişimine karşılık gelebilir. "
            "Nedensel etki için yalnız olay tarihi yeterli değildir."
        ),
        "verification_level": "downloaded_primary_fulltext",
        "evidence_access": {
            "decision_document": document(BDDK_10525_URL, "bddk_10525.pdf", [1, 2])
        },
        "timing_note_tr": (
            "Karar tarihi tam metinden doğrulandı. Belgede ayrı bir yürürlük tarihi "
            "belirtilmediği için effective_date boştur."
        ),
    },
    {
        "event_id": "BDDK_10656_20230824",
        "decision_date": "2023-08-24",
        "effective_date": None,
        "publication_date": None,
        "annotation_date": "2023-08-24",
        "annotation_date_type": "decision_date",
        "event_type": "housing_credit_regulation",
        "source_url": BDDK_10656_URL,
        "title": "10656 sayılı mevcut konut sahipliği durumunda kredi/değer oranı azaltımı",
        "verified_summary_tr": (
            "24 Ağustos 2023 tarihli 10656 sayılı karar, tüketicinin kendisinin, "
            "eşinin veya 18 yaş altındaki çocuklarının malik olduğu en az bir konut "
            "varsa 10525 sayılı karardaki kredi/değer oranlarının yüzde 75 "
            "azaltılarak uygulanmasını öngördü. Köy niteliğindeki bazı konutlar ve "
            "belirli hisseli tapular için istisnalar da tam metinde yer alıyor."
        ),
        "analysis_hypothesis_tr": (
            "Mevcut konut sahipleri için özkaynak ihtiyacı artışı, faiz düşüşüne "
            "rağmen kredi kullanımını sınırlayan bir kanal olabilir. Buradaki "
            "veriyle bu etkinin varlığı veya büyüklüğü nedensel olarak kanıtlanmaz."
        ),
        "verification_level": "downloaded_primary_fulltext_and_official_corroboration",
        "evidence_access": {
            "decision_document": document(BDDK_10656_URL, "bddk_10656.pdf", [1]),
            "downloaded_corroboration": [
                document(FSR_2025_URL, "cb_fsr2025may.pdf", [14]),
                document(IR_2024_URL, "cb_ir2024i_box2_4.pdf", [4]),
            ],
        },
        "timing_note_tr": (
            "Karar tarihi tam metinden doğrulandı. Belgede ayrı bir yürürlük tarihi "
            "belirtilmediği için effective_date boştur. Sonraki kararlar ayrıca "
            "izlenmelidir."
        ),
    },
    {
        "event_id": "TCMB_KFE_REVISION_20240816",
        "decision_date": None,
        "effective_date": None,
        "publication_date": "2024-08-16",
        "announcement_date": "2024-08-15",
        "reference_period": "2024-07",
        "annotation_date": "2024-08-16",
        "annotation_date_type": "first_publication_of_revised_series",
        "event_type": "statistical_methodology_revision",
        "source_url": KFE_ANNOUNCEMENT_URL,
        "title": "KFE yöntem değişikliği ve geçmiş serilerin yeniden hesaplanması",
        "verified_summary_tr": (
            "15 Ağustos 2024 duyurusu, yeni yöntemli KFE serilerinin 16 Ağustos "
            "2024 tarihinde Temmuz 2024 verisiyle yayımlanacağını açıklar. Ocak "
            "2010 itibarıyla başlayan geçmiş yeniden hesaplandı, üç aylık veri "
            "yerine aylık veri kullanıldı, yayın gecikmesi t+45 günden t+15 güne "
            "indirildi ve baz yıl 2023 oldu."
        ),
        "analysis_hypothesis_tr": (
            "Bu kayıt ekonomik şok göstergesi değildir. Eski yayımlardan alınan "
            "seviyeler yeni seriyle uç uca eklenmemeli, aynı veri sürümü "
            "kullanılmalıdır."
        ),
        "verification_level": "downloaded_primary_fulltext_and_official_announcement",
        "evidence_access": {
            "official_announcement": "official_announcement_fulltext_verified",
            "downloaded_documents": [document(KFE_URL, "kfe_method_change.pdf", [3])],
        },
        "timing_note_tr": (
            "Duyuru günü, ilk yayımlanma günü ve referans ayı ayrı tutuldu. "
            "Hukuki yürürlük tarihi uygulanabilir olmadığından effective_date boştur."
        ),
    },
    {
        "event_id": "BDDK_11364_20260129",
        "decision_date": "2026-01-29",
        "effective_date": None,
        "publication_date": "2026-01-30",
        "annotation_date": "2026-01-29",
        "annotation_date_type": "decision_date",
        "event_type": "housing_credit_regulation",
        "source_url": BDDK_11364_URL,
        "title": "11364 sayılı konut kredisi kararı ve tek oran tablosu",
        "verified_summary_tr": (
            "29 Ocak 2026 tarihli 11364 sayılı karar, konut alımı ve konut "
            "teminatlı krediler için birinci ve ikinci el ayrımı içermeyen tek bir "
            "değer ve enerji sınıfı tablosu belirledi. 10656 sayılı karardaki mevcut "
            "konut sahipliği azaltımının yeni oranlar üzerinden devam edeceğini "
            "açıkça düzenledi."
        ),
        "analysis_hypothesis_tr": (
            "Krediye erişim koşullarındaki değişim, konut kredisi ile ipotekli satış "
            "ilişkisini dönemler arasında değiştirebilir. Olay tarihi tek başına "
            "nedensel etki tahmini değildir."
        ),
        "verification_level": "downloaded_primary_fulltext",
        "evidence_access": {
            "decision_document": document(BDDK_11364_URL, "bddk_11364.pdf", [1]),
            "official_press_sources": [
                "https://www.bddk.org.tr/Duyuru/EkGetir/2157?ekId=889",
                "https://www.bddk.org.tr/Duyuru/EkGetir/2158?ekId=890",
            ],
        },
        "timing_note_tr": (
            "Karar tarihi tam metinden doğrulandı. 30 Ocak 2026 kamuoyu duyurusu "
            "karar tarihinden ayrı tutuldu. Belgede ayrı bir yürürlük tarihi "
            "belirtilmediği için effective_date boştur."
        ),
    },
]


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_pdf_text(pdf_path: Path) -> tuple[Path, int]:
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text_path = pdf_path.with_suffix(".txt")
    text_path.write_text("\n\n".join(pages).strip() + "\n", encoding="utf-8")
    return text_path, len(reader.pages)


def build() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = []
    for source in EVENTS:
        event = dict(source)
        event["research_asof_utc"] = RESEARCH_AS_OF
        event["recommended_use"] = "descriptive_annotation_only"
        event["causal_effect_estimated"] = False
        event["monthly_annotation_key"] = event["annotation_date"][:7]
        events.append(event)

    (BASE_DIR / "events.json").write_text(
        json.dumps(events, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    manifest = []
    for pdf_path in sorted(BASE_DIR.glob("*.pdf")):
        text_path, page_count = extract_pdf_text(pdf_path)
        manifest.append(
            {
                "file": pdf_path.name,
                "bytes": pdf_path.stat().st_size,
                "sha256": sha256_path(pdf_path),
                "page_count": page_count,
                "extracted_text_file": text_path.name,
                "extracted_text_sha256": sha256_path(text_path),
            }
        )
    (BASE_DIR / "source_documents_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return events, manifest


if __name__ == "__main__":
    built_events, built_manifest = build()
    print(
        f"Saved {len(built_events)} events; "
        f"{len(built_manifest)} downloaded official PDFs"
    )
