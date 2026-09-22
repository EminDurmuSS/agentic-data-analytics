"""Build vehicle credit dataset: BDDK monthly stock + TCMB EVDS interest rate.

Run: python build_vehicle_credit_dataset.py

Output files (data_pipeline/catalog/vehicle_credit_v1/):
  vehicle_credit_panel.parquet
  vehicle_credit_panel.csv
  manifest.json
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "catalog" / "vehicle_credit_v1"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# BDDK Tüketici Kredileri - Taşıt [Toplam] (Sektör)
# Kaynak: BDDK Aylık Bülten, Ocak-Haziran 2026
# Birim: milyon TL (stok, dönem sonu)
# --------------------------------------------------------------------------- #
BDDK_VEHICLE_STOCK = {
    "2026-01": 49046,
    "2026-02": 48214,
    "2026-03": 46837,
    "2026-04": 45591,
    "2026-05": 44322,
    "2026-06": 43062,
}

AY_TR = {
    "2026-01": "Ocak 2026",
    "2026-02": "Şubat 2026",
    "2026-03": "Mart 2026",
    "2026-04": "Nisan 2026",
    "2026-05": "Mayıs 2026",
    "2026-06": "Haziran 2026",
}


def build():
    # --- EVDS rate series ---
    rate_file = ROOT / "raw" / "evds_tp_bkr_try_17.json"
    if not rate_file.exists():
        print(f"[error] EVDS rate file not found: {rate_file}")
        print("  Run first: python data_pipeline/evds_on_demand.py --series TP.BKR.TRY.17")
        sys.exit(1)

    raw_rate = json.loads(rate_file.read_bytes())
    rate_sha256 = hashlib.sha256(rate_file.read_bytes()).hexdigest()
    df_rate = pd.DataFrame(raw_rate["items"])
    df_rate = df_rate.rename(columns={"Tarih": "ay", "TP_BKR_TRY_17": "tasit_kredi_faiz_stok_pct"})
    df_rate["tasit_kredi_faiz_stok_pct"] = pd.to_numeric(df_rate["tasit_kredi_faiz_stok_pct"], errors="coerce")
    df_rate = df_rate[["ay", "tasit_kredi_faiz_stok_pct"]]

    # --- BDDK stock ---
    df_bddk = pd.DataFrame([
        {"ay": k, "ay_tr": AY_TR[k], "tasit_kredi_bddk_stok_milyon_tl": v}
        for k, v in BDDK_VEHICLE_STOCK.items()
    ])

    # --- Merge ---
    df = df_bddk.merge(df_rate, on="ay", how="left")

    # Validation
    missing_rate = df["tasit_kredi_faiz_stok_pct"].isna().sum()
    if missing_rate:
        print(f"[warn] {missing_rate} months have no EVDS rate data.")

    # Derived columns
    df["tasit_kredi_bddk_stok_degisim_milyon_tl"] = df["tasit_kredi_bddk_stok_milyon_tl"].diff()
    df["tasit_kredi_bddk_stok_mom_pct"] = (
        df["tasit_kredi_bddk_stok_milyon_tl"].pct_change() * 100
    )

    # Reorder columns
    df = df[[
        "ay_tr", "ay",
        "tasit_kredi_bddk_stok_milyon_tl",
        "tasit_kredi_bddk_stok_degisim_milyon_tl",
        "tasit_kredi_bddk_stok_mom_pct",
        "tasit_kredi_faiz_stok_pct",
    ]]

    # Write outputs
    df.to_parquet(OUT_DIR / "vehicle_credit_panel.parquet", index=False)
    df.to_csv(OUT_DIR / "vehicle_credit_panel.csv", index=False, float_format="%.4f",
              encoding="utf-8-sig")  # utf-8-sig for Excel compatibility

    # Manifest
    manifest = {
        "dataset": "vehicle_credit_v1",
        "description": "BDDK Aylık Bülten Taşıt Kredisi stok + TCMB EVDS faiz oranı panel veri seti",
        "period": "2026-01 to 2026-06",
        "frequency": "monthly",
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": [
            {
                "name": "BDDK Aylık Bülten",
                "series": "Tüketici Kredileri - Taşıt [Toplam] (Sektör)",
                "unit": "milyon TL (stok, dönem sonu)",
                "scope": "Yurt içi yerleşik müşteriler; Birleşik Fon Bankası, Adabank, Türk Ticaret Bankası hariç olabilir",
                "columns": ["tasit_kredi_bddk_stok_milyon_tl"],
                "note": "Bilanço toplamıyla kapsam aynı değildir.",
            },
            {
                "name": "TCMB EVDS",
                "series_code": "TP.BKR.TRY.17",
                "series_name": "Taşıt Kredisi (TL, Stok, %)",
                "group_code": "bie_kt210a",
                "unit": "% (ağırlıklı ortalama, stok)",
                "columns": ["tasit_kredi_faiz_stok_pct"],
                "raw_file": "data_pipeline/raw/evds_tp_bkr_try_17.json",
                "raw_sha256": rate_sha256,
                "observation_count": len(raw_rate["items"]),
            },
        ],
        "columns": {
            "ay_tr": "Ay (Türkçe, örn. Ocak 2026)",
            "ay": "Dönem (YYYY-MM formatı)",
            "tasit_kredi_bddk_stok_milyon_tl": "BDDK Taşıt Kredisi Stok (milyon TL)",
            "tasit_kredi_bddk_stok_degisim_milyon_tl": "Bir önceki aya göre değişim (milyon TL, türetilmiş)",
            "tasit_kredi_bddk_stok_mom_pct": "Aylık değişim oranı (%, türetilmiş)",
            "tasit_kredi_faiz_stok_pct": "EVDS Taşıt Kredisi Faiz Oranı - Stok (%, aylık ağırlıklı ort.)",
        },
        "rows": len(df),
        "missing_rate_months": int(missing_rate),
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=== vehicle_credit_v1 dataset built ===")
    print(df.to_string(index=False))
    print(f"\nFiles written to: {OUT_DIR}")
    print(f"  vehicle_credit_panel.parquet")
    print(f"  vehicle_credit_panel.csv")
    print(f"  manifest.json")
    print(f"\nKaynak: BDDK Aylik Bulten + TCMB EVDS TP.BKR.TRY.17")
    return df


if __name__ == "__main__":
    build()
