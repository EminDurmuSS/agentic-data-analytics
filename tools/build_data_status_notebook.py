#!/usr/bin/env python3
"""Build the current data-status notebook from the validated DuckDB file."""

from __future__ import annotations

from pathlib import Path

import duckdb
import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data_pipeline" / "lakehouse" / "analytics.duckdb"
OUTPUTS = (
    ROOT / "notebooks" / "KKB_Verileri_Dogrulanmis.ipynb",
    ROOT / "data_pipeline" / "KKB_Verileri_Dogrulanmis.ipynb",
)


def dataframe_output(frame) -> dict:
    return new_output(
        output_type="execute_result",
        data={
            "text/plain": frame.to_string(index=False),
            "text/html": frame.to_html(index=False, border=1),
        },
        metadata={},
        execution_count=None,
    )


def query_cell(source: str, frame) -> dict:
    return new_code_cell(
        source=source,
        execution_count=None,
        outputs=[dataframe_output(frame)],
    )


def build_notebook() -> dict:
    connection = duckdb.connect(str(DATABASE), read_only=True)
    try:
        summary = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM catalog.data_assets) AS veri_varligi,
              (SELECT count(*) FROM catalog.metrics) AS katalog_metrigi,
              (SELECT count(*) FROM catalog.metrics
               WHERE observation_available) AS sorgulanabilir_metrik,
              (SELECT count(*) FROM evds.series_catalog) AS evds_katalog_serisi,
              (SELECT count(*) FROM catalog.metrics
               WHERE source_system = 'TCMB_EVDS'
                 AND observation_available) AS yerel_evds_serisi,
              (SELECT count(*) FROM analysis.housing_credit_monthly) AS aylik_donem,
              (SELECT count(*) FROM analysis.housing_credit_quarterly) AS ceyreklik_donem,
              (SELECT count(*) FROM risk_center.housing_credit_monthly) AS risk_merkezi_ayi,
              (SELECT count(*) FROM regional.housing_quarterly) AS bolgesel_satir,
              (SELECT count(DISTINCT province_key)
               FROM regional.housing_quarterly) AS il_sayisi,
              (SELECT count(*) FROM regional.housing_quarterly
               WHERE analysis_ready) AS analize_hazir_bolgesel_satir
            """
        ).fetchdf()
        source_status = connection.execute(
            """
            SELECT source_system AS kaynak,
                   string_agg(DISTINCT status, ', ' ORDER BY status) AS durumlar,
                   count(*) AS varlik,
                   CAST(sum(row_count) AS BIGINT) AS kayit
            FROM catalog.data_assets
            GROUP BY source_system
            ORDER BY source_system
            """
        ).fetchdf()
        monthly_sample = connection.execute(
            """
            SELECT month AS ay,
                   bddk_housing_credit_stock_million_tl AS bddk_konut_kredisi_milyon_tl,
                   round(TP_KTF12, 2) AS konut_kredisi_faizi_yuzde,
                   round(TP_TUKFIY2025_GENEL, 2) AS tufe_endeksi,
                   round(TP_KFE_TR, 2) AS konut_fiyat_endeksi,
                   round(mortgaged_sales_share_pct, 2) AS ipotekli_satis_payi_yuzde,
                   risk_center_housing_credit_balance_billion_try
                       AS risk_merkezi_bakiyesi_milyar_tl,
                   risk_center_first_time_housing_credit_users_thousand_person
                       AS ilk_kullanan_bin_kisi
            FROM analysis.housing_credit_monthly
            ORDER BY month DESC
            LIMIT 12
            """
        ).fetchdf()
        demo_rows = connection.execute(
            """
            SELECT month AS ay,
                   round(housing_rate_change_pp, 3) AS faiz_degisimi_puan,
                   round(housing_credit_real_mom_pct, 3) AS reel_kredi_aylik_yuzde,
                   source_scope_review_required AS kaynak_kapsami_incele
            FROM analysis.housing_credit_monthly
            WHERE rate_down_real_stock_not_up_quality_screened
            ORDER BY month
            """
        ).fetchdf()
        reconciliation = connection.execute(
            """
            SELECT quarter AS ceyrek,
                   bddk_housing_credit_stock_million_tl AS bddk_milyon_tl,
                   round(finturk_domestic_minus_bddk_pct, 4) AS finturk_81_il_farki_yuzde,
                   round(tbb_minus_bddk_pct, 2) AS tbb_kapsam_farki_yuzde,
                   disbursement_amount_million_try AS tbb_kullandirim_milyon_tl
            FROM analysis.housing_credit_quarterly
            ORDER BY quarter DESC
            LIMIT 8
            """
        ).fetchdf()
        missing_metrics = connection.execute(
            """
            SELECT source_system AS kaynak,
                   count(*) AS eksik_gozlemi_olan_metrik,
                   sum(missing_observation_count) AS kaynak_bos_gozlem
            FROM catalog.metrics
            WHERE observation_available
              AND missing_observation_count > 0
            GROUP BY source_system
            ORDER BY source_system
            """
        ).fetchdf()
        regional_sample = connection.execute(
            """
            SELECT province_name AS il,
                   quarter AS ceyrek,
                   housing_sales_total_count AS toplam_konut_satisi,
                   round(mortgaged_sales_share_pct, 2) AS ipotekli_satis_payi_yuzde,
                   round(housing_credit_per_capita_try, 2) AS kisi_basi_konut_kredisi_tl,
                   round(housing_credit_yoy_pct, 2) AS konut_kredisi_yillik_yuzde,
                   analysis_ready AS analize_hazir
            FROM regional.housing_quarterly
            WHERE quarter = '2026Q2'
              AND province_name IN ('ANKARA', 'İSTANBUL', 'İZMİR')
            ORDER BY province_name
            """
        ).fetchdf()
    finally:
        connection.close()

    title = """# KKB Agentic Data Analytics: güncel veri doğrulama notebook'u

**8 Eylül 2026 veri kapanış sürümü**

Bu notebook eski 25 serilik başlangıç snapshot'ı değildir. Repodaki güncel,
self-contained DuckDB dosyasını salt okunur açar ve BDDK aylık, BDDK haftalık,
BDDK FinTürk, seçilmiş ulusal ve bölgesel TCMB EVDS serileri, TÜİK il konut
satışları, TBB tüketici kredileri, TBB Risk Merkezi aylık bültenleri,
hanehalkı finansmanı ve resmî bağlam
belgelerinin birleşik durumunu gösterir.

Ana sonuç: Zorunlu kaynak ailelerinin yayımlanmış veri kapsamı tamamlandı.
Kaynakta henüz yayımlanmayan TBB Haziran 2026 çeyreklik tüketici kredileri
raporu tahmin edilmedi ve açık boşluk olarak korunuyor. Ayrı Risk Merkezi
Haziran 2026 aylık bülteni kendi metrikleriyle sisteme eklendi.
"""

    scope = f"""## Kapsam

| Kaynak | Yerel kapsam |
| --- | --- |
| BDDK aylık | 66 ay, 10 resmî banka grubu, 17 tablonun tamamı, 1.334.850 semantik ölçüm |
| BDDK haftalık | 286 hafta, 7 resmî banka grubu, 9 tablonun tamamı, 1.025.974 ölçüm |
| BDDK FinTürk | 22 çeyrek, 7 tablo, 7 banka grubu, 81 il ve `YURT DIŞI`, 936.512 ölçüm, 1.328 kaynak-null şube değeri için auditli analitik sıfır |
| TCMB EVDS | 52.696 serilik metadata kataloğu, analitik değeri yüksek {int(summary.iloc[0]['yerel_evds_serisi'])} kaynak serinin yerel gözlemi ve 1 türetilmiş altın serisi |
| TÜİK il konut satışları | 81 il, Ocak 2020-Haziran 2026, 5 aylık satış metriği ve EVDS çapraz doğrulaması |
| İl bazlı konut paneli | {int(summary.iloc[0]['il_sayisi'])} il, 22 çeyrek, {int(summary.iloc[0]['bolgesel_satir'])} tekil satır ve {int(summary.iloc[0]['analize_hazir_bolgesel_satir'])} analize hazır satır |
| TBB | Mart 2021-Mart 2026 arasında yayımlanmış 21 rapor, 252 ürün ölçümü |
| TBB Risk Merkezi | Ocak 2021-Haziran 2026, 66 ay, 5 konut kredisi metriği, 6 Haziran bülteni ve tüm kaynak vintageları |
| Resmî belgeler | 4 BDDK kararı ve 4 TCMB yöntem veya destek belgesi |

EVDS katalog kaydı, gözlemin indirildiği anlamına gelmez. Bu ayrım
`observation_available` alanında açıkça tutulur.
"""

    setup_source = """from pathlib import Path
import duckdb
import pandas as pd
from IPython.display import display

pd.set_option("display.max_columns", 30)

candidates = [Path.cwd(), *Path.cwd().parents]
ROOT = next(
    path for path in candidates
    if (path / "data_pipeline" / "lakehouse" / "analytics.duckdb").exists()
)
DB_PATH = ROOT / "data_pipeline" / "lakehouse" / "analytics.duckdb"
connection = duckdb.connect(str(DB_PATH), read_only=True)
print(f"DuckDB: {DB_PATH}")
print(f"Boyut: {DB_PATH.stat().st_size / 1024 / 1024:.2f} MiB")
"""

    summary_source = """connection.execute(
    \"\"\"
    SELECT
      (SELECT count(*) FROM catalog.data_assets) AS veri_varligi,
      (SELECT count(*) FROM catalog.metrics) AS katalog_metrigi,
      (SELECT count(*) FROM catalog.metrics
       WHERE observation_available) AS sorgulanabilir_metrik,
      (SELECT count(*) FROM evds.series_catalog) AS evds_katalog_serisi,
      (SELECT count(*) FROM catalog.metrics
       WHERE source_system = 'TCMB_EVDS'
         AND observation_available) AS yerel_evds_serisi,
      (SELECT count(*) FROM analysis.housing_credit_monthly) AS aylik_donem,
      (SELECT count(*) FROM analysis.housing_credit_quarterly) AS ceyreklik_donem,
      (SELECT count(*) FROM risk_center.housing_credit_monthly) AS risk_merkezi_ayi,
      (SELECT count(*) FROM regional.housing_quarterly) AS bolgesel_satir,
      (SELECT count(DISTINCT province_key)
       FROM regional.housing_quarterly) AS il_sayisi,
      (SELECT count(*) FROM regional.housing_quarterly
       WHERE analysis_ready) AS analize_hazir_bolgesel_satir
    \"\"\"
).fetchdf()
"""

    source_status_source = """connection.execute(
    \"\"\"
    SELECT source_system AS kaynak,
           string_agg(DISTINCT status, ', ' ORDER BY status) AS durumlar,
           count(*) AS varlik,
           CAST(sum(row_count) AS BIGINT) AS kayit
    FROM catalog.data_assets
    GROUP BY source_system
    ORDER BY source_system
    \"\"\"
).fetchdf()
"""

    monthly_source = """connection.execute(
    \"\"\"
    SELECT month AS ay,
           bddk_housing_credit_stock_million_tl AS bddk_konut_kredisi_milyon_tl,
           round(TP_KTF12, 2) AS konut_kredisi_faizi_yuzde,
           round(TP_TUKFIY2025_GENEL, 2) AS tufe_endeksi,
           round(TP_KFE_TR, 2) AS konut_fiyat_endeksi,
           round(mortgaged_sales_share_pct, 2) AS ipotekli_satis_payi_yuzde,
           risk_center_housing_credit_balance_billion_try
               AS risk_merkezi_bakiyesi_milyar_tl,
           risk_center_first_time_housing_credit_users_thousand_person
               AS ilk_kullanan_bin_kisi
    FROM analysis.housing_credit_monthly
    ORDER BY month DESC
    LIMIT 12
    \"\"\"
).fetchdf()
"""

    demo_source = """demo = connection.execute(
    \"\"\"
    SELECT month AS ay,
           round(housing_rate_change_pp, 3) AS faiz_degisimi_puan,
           round(housing_credit_real_mom_pct, 3) AS reel_kredi_aylik_yuzde,
           source_scope_review_required AS kaynak_kapsami_incele
    FROM analysis.housing_credit_monthly
    WHERE rate_down_real_stock_not_up_quality_screened
    ORDER BY month
    \"\"\"
).fetchdf()
print(f\"Koşulu sağlayan kalite taramalı ay sayısı: {len(demo)}\")
display(demo)
"""

    reconciliation_source = """connection.execute(
    \"\"\"
    SELECT quarter AS ceyrek,
           bddk_housing_credit_stock_million_tl AS bddk_milyon_tl,
           round(finturk_domestic_minus_bddk_pct, 4) AS finturk_81_il_farki_yuzde,
           round(tbb_minus_bddk_pct, 2) AS tbb_kapsam_farki_yuzde,
           disbursement_amount_million_try AS tbb_kullandirim_milyon_tl
    FROM analysis.housing_credit_quarterly
    ORDER BY quarter DESC
    LIMIT 8
    \"\"\"
).fetchdf()
"""

    missing_source = """connection.execute(
    \"\"\"
    SELECT source_system AS kaynak,
           count(*) AS eksik_gozlemi_olan_metrik,
           sum(missing_observation_count) AS kaynak_bos_gozlem
    FROM catalog.metrics
    WHERE observation_available
      AND missing_observation_count > 0
    GROUP BY source_system
    ORDER BY source_system
    \"\"\"
).fetchdf()
"""

    regional_source = """connection.execute(
    \"\"\"
    SELECT province_name AS il,
           quarter AS ceyrek,
           housing_sales_total_count AS toplam_konut_satisi,
           round(mortgaged_sales_share_pct, 2) AS ipotekli_satis_payi_yuzde,
           round(housing_credit_per_capita_try, 2) AS kisi_basi_konut_kredisi_tl,
           round(housing_credit_yoy_pct, 2) AS konut_kredisi_yillik_yuzde,
           analysis_ready AS analize_hazir
    FROM regional.housing_quarterly
    WHERE quarter = '2026Q2'
      AND province_name IN ('ANKARA', 'İSTANBUL', 'İZMİR')
    ORDER BY province_name
    \"\"\"
).fetchdf()
"""

    quality = """## Analitik sözleşme

- Kredi stoku, stok değişimi ve yeni kullandırım akımı farklı ölçülerdir.
- Risk Merkezi ilk kez konut kredisi kullanan kişi sayısı, parasal kredi
  kullandırım tutarı değildir ve onun boşluğunu doldurmaz.
- Haftalık faiz aylığa çevrilirken kullanılan yöntem metadata ile birlikte saklanır.
- Kümülatif BDDK kâr-zarar değerleri kaynak hâliyle korunur, türetilmiş aylık akım ayrıca tutulur.
- Eksik değerler tahminle doldurulmaz. Sıfır yalnız aynı resmî kaynak içindeki
  kesin bir toplamsal kimlikle kanıtlanırsa, ham null korunarak ayrı provenance
  ile kullanılabilir.
- FinTürk'teki 1.328 kaynak-null şube değeri, 1.782 il-çeyreğin tamamında sıfır
  farkla geçen `SEKTÖR = MEVDUAT + KATILIM + KALKINMA VE YATIRIM` kimliğiyle
  ayrı `usable_value=0` ve audit kaydı olarak tutulur.
- BDDK haftalık kaynak boşlukları ile FinTürk yapısal boşlukları ayrı denetim
  tablolarında tutulur.
- Çeyreklik gözlem ara aylara forward fill edilmez.
- İl bazlı panelde eksik aylı çeyrekler kısmi toplamla doldurulmaz.
- İpoteksiz satış, nakit satış olarak yorumlanmaz.
- Birlikte hareket nedensellik kanıtı sayılmaz. Olay belgeleri yalnız araştırma bağlamıdır.
"""

    known_gaps = """## Açık kalite notları

1. TBB Haziran 2026 çeyreklik tüketici kredileri raporu 8 Eylül 2026
   kontrolünde kaynakta yayımlanmamıştır. Ayrı bir yayın ailesi olan Risk
   Merkezi Haziran 2026 aylık bülteni mevcuttur, ancak parasal kullandırım
   tutarı yayımlamaz.
2. EVDS `TP.MK.KUL.YTL` serisinin Haziran 2026 gözlemi kaynak cevabında yoktur.
3. Eski EVDS `TP.ALTINPIYASA.KAP05` serisi seyrektir ve 24 Kasım 2025'te biter.
4. Aktif altın kontrolü `TP.ALTINPIYASA.KAP02` 30 Haziran 2026'ya kadar doludur; TL/gram serisi açık `0.001` dönüşümüyle türetilir.
5. Ağustos 2025 EVDS ve BDDK konut kredisi kapsam farkı otomatik düzeltilmez.
6. TBB raporlayan banka kapsamı BDDK sektör toplamından daha dardır.
7. Beş ilin konut birim fiyatı serisi tamamen boştur. Altı ilin serisi 2023'te,
   Şırnak serisi 2022'de başlar. Bu nedenle toplam 162 il-çeyrek birim fiyatı
   kaynakta yayımlanmadığı için null kalır.
8. Ham EVDS'deki 9 yarışma dönemi ipotekli satış null değeri, TÜİK'teki
   `toplam satış = diğer satış` özdeşliğiyle sıfır olarak doğrulanıp 8
   il-çeyrek toplamında açık fallback provenance ile kullanılır.
9. Yedi ilin konut birim kira serisi tamamen boştur, altı ilin serisi ise
   kısmi kapsama sahiptir. Kira kontrolü eksik satış fiyatının yerine konmaz.

Bu boşluklar veri kaybı gibi gizlenmez. Katalog ve kalite tablolarında açıkça
görülebilir.
"""

    finish = """## Sonuç

Veri toplama ve doğrulama aşaması, kaynakta yayımlanmış zorunlu kapsam için
tamamlandı. Bir sonraki ürün aşaması bu katalog ve DuckDB üzerinde generic
ingestion, güvenli sorgulama, analiz durumu, nedensellik iş akışları ve CloudX
agent katmanını kurmaktır.
"""

    notebook = new_notebook(
        cells=[
            new_markdown_cell(title),
            new_markdown_cell(scope),
            new_code_cell(
                setup_source,
                execution_count=None,
                outputs=[
                    new_output(
                        output_type="stream",
                        name="stdout",
                        text=(
                            "DuckDB: data_pipeline/lakehouse/analytics.duckdb\n"
                            f"Boyut: {DATABASE.stat().st_size / 1024 / 1024:.2f} MiB\n"
                        ),
                    )
                ],
            ),
            new_markdown_cell("## Birleşik katalog özeti"),
            query_cell(summary_source, summary),
            new_markdown_cell("## Kaynak varlıklarının yükleme durumu"),
            query_cell(source_status_source, source_status),
            new_markdown_cell(quality),
            new_markdown_cell("## Güncel aylık analiz tablosundan örnek"),
            query_cell(monthly_source, monthly_sample),
            new_markdown_cell(
                "## Demo koşulu: faiz düşerken reel kredi stokunun artmadığı aylar\n\n"
                "Bu filtre betimleyici bir eş hareket bulgusudur, tek başına nedensellik kanıtı değildir."
            ),
            query_cell(demo_source, demo_rows),
            new_markdown_cell("## Kaynak kapsamlarının çeyreklik karşılaştırması"),
            query_cell(reconciliation_source, reconciliation),
            new_markdown_cell("## İl bazlı konut panelinden örnek"),
            query_cell(regional_source, regional_sample),
            new_markdown_cell(known_gaps),
            query_cell(missing_source, missing_metrics),
            new_code_cell("connection.close()\nprint('Bağlantı kapatıldı.')"),
            new_markdown_cell(finish),
        ],
        metadata={
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.12",
            },
        },
    )
    return notebook


def main() -> None:
    notebook = build_notebook()
    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        nbformat.write(notebook, output)
        print(f"Wrote {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
