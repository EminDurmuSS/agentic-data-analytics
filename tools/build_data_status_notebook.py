#!/usr/bin/env python3
"""Build the current data-status notebook from the validated DuckDB file."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data_pipeline" / "lakehouse" / "analytics.duckdb"
OUTPUTS = (
    ROOT / "notebooks" / "KKB_Verileri_Dogrulanmis.ipynb",
    ROOT / "data_pipeline" / "KKB_Verileri_Dogrulanmis.ipynb",
)

EVDS_COVERAGE_SQL = """
WITH observations AS (
    SELECT series_code,value,period_start,period_end FROM evds.housing_observations
    UNION ALL SELECT series_code,value,period_start,period_end FROM evds.housing_controls_observations
    UNION ALL SELECT series_code,value,period_start,period_end FROM evds.market_controls_observations
    UNION ALL SELECT series_code,value,period_start,period_end FROM evds.regional_housing_observations
    UNION ALL SELECT series_code,value,period_start,period_end FROM evds.household_finance_observations
    UNION ALL SELECT series_code,value,period_start,period_end FROM evds.legacy_observations
), coverage AS (
    SELECT count(DISTINCT series_code) AS fiziksel_seri,
           count(DISTINCT CASE WHEN value IS NOT NULL
               AND CAST(period_start AS DATE) <= DATE '2026-06-30'
               AND CAST(period_end AS DATE) >= DATE '2021-01-01'
               THEN series_code END) AS hedef_donemde_sayisal_seri
    FROM observations
)
SELECT (SELECT count(*) FROM evds.series_catalog) AS metadata_serisi,
       fiziksel_seri, hedef_donemde_sayisal_seri,
       fiziksel_seri - hedef_donemde_sayisal_seri AS hedef_donemde_sayisal_degeri_olmayan_fiziksel_seri,
       (SELECT count(*) FROM evds.series_catalog) - fiziksel_seri AS yalniz_metadata_serisi
FROM coverage
"""

BINDING_STATUS_SQL = """
SELECT status AS metrik_sozlesmesi_durumu, count(*) AS metrik
FROM catalog.metric_bindings
GROUP BY status
ORDER BY status
"""


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
               WHERE observation_available) AS gozlemi_bildirilen_katalog_metrigi,
              (SELECT count(*) FROM information_schema.tables
               WHERE table_schema NOT IN ('information_schema','pg_catalog')) AS duckdb_tablo_ve_gorunum,
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
               WHERE analysis_ready_source) AS kaynakla_analize_hazir_bolgesel_satir,
              (SELECT count(*) FROM regional.housing_quarterly
               WHERE analysis_ready_with_price_proxy) AS proxy_izinli_analize_hazir_bolgesel_satir
            """
        ).fetchdf()
        evds_coverage = connection.execute(EVDS_COVERAGE_SQL).fetchdf()
        binding_status = connection.execute(BINDING_STATUS_SQL).fetchdf()
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
                   round(housing_unit_price_try_per_m2, 2) AS resmi_birim_fiyat,
                   round(housing_unit_price_with_proxy_try_per_m2, 2)
                       AS proxy_izinli_birim_fiyat,
                   housing_unit_price_proxy_origin AS fiyat_kokeni,
                   analysis_ready_source AS kaynakla_analize_hazir,
                   analysis_ready_with_price_proxy AS proxy_izinli_analize_hazir
            FROM regional.housing_quarterly
            WHERE quarter = '2026Q2'
              AND province_name IN ('ANKARA', 'İSTANBUL', 'İZMİR')
            ORDER BY province_name
            """
        ).fetchdf()
    finally:
        connection.close()

    generated_date = datetime.now(ZoneInfo("Europe/Istanbul")).date().isoformat()
    title = f"""# KKB Agentic Data Analytics: güncel veri doğrulama notebook'u

**Oluşturulma tarihi: {generated_date}. Hedef gözlem dönemi: Ocak 2021-Haziran 2026.**

Bu notebook eski 25 serilik başlangıç snapshot'ı değildir. Repodaki güncel,
self-contained DuckDB dosyasını salt okunur açar ve BDDK aylık, BDDK haftalık,
BDDK FinTürk, seçilmiş ulusal ve bölgesel TCMB EVDS serileri, TÜİK il konut
satışları, TBB tüketici kredileri, TBB Risk Merkezi aylık bültenleri,
hanehalkı finansmanı ve resmî bağlam
belgelerinin birleşik durumunu gösterir.

BDDK haftalık, aylık ve FinTürk kaynakları hedef dönem için yereldedir.
EVDS'nin tüm serilerinin gözlem kapsamı henüz tamamlanmadı. Metadata kataloğu,
fiziksel gözlem, sayısal gözlem ve seçilen hesap için hazır olma ayrı kavramlardır.
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
| TCMB EVDS | {format(int(evds_coverage.iloc[0]['metadata_serisi']), ',').replace(',', '.')} metadata serisi, {int(evds_coverage.iloc[0]['fiziksel_seri'])} fiziksel kaynak serisi, hedef dönemde {int(evds_coverage.iloc[0]['hedef_donemde_sayisal_seri'])} seride sayısal değer; 1 türetilmiş altın serisi ayrıca tutulur |
| TÜİK il konut satışları | 81 il, Ocak 2020-Haziran 2026, 5 aylık satış metriği ve EVDS çapraz doğrulaması |
| İl bazlı konut paneli | {int(summary.iloc[0]['il_sayisi'])} il, 22 çeyrek, {int(summary.iloc[0]['bolgesel_satir'])} tekil satır, {int(summary.iloc[0]['kaynakla_analize_hazir_bolgesel_satir'])} genel kaynak-hazır bayrağı ve {int(summary.iloc[0]['proxy_izinli_analize_hazir_bolgesel_satir'])} fiyat-proxy izinli bayrak; seçilen ölçü ayrıca kontrol edilir |
| TBB | Mart 2021-Mart 2026 arasında yayımlanmış 21 rapor, 252 ürün ölçümü |
| TBB Risk Merkezi | Ocak 2021-Haziran 2026, 66 ay, 5 konut kredisi metriği, 6 Haziran bülteni ve tüm kaynak vintageları |
| Resmî belgeler | 4 BDDK kararı ve 4 TCMB yöntem veya destek belgesi |

52.696 serilik EVDS katalog kaydı, bütün gözlemlerin indirildiği anlamına gelmez.
`observation_available` fiziksel gözlem varlığını bildirir; sayısal değer,
tam dönem kapsamı veya belirli bir işlem için kullanılabilirlik garantisi değildir.
Hedef dönemde {int(evds_coverage.iloc[0]['hedef_donemde_sayisal_degeri_olmayan_fiziksel_seri'])} fiziksel seride sayısal değer bulunmaz.
Sayısal değeri bulunan bir serinin de tüm hedef dönemleri dolu olmak zorunda değildir.
Genel bölgesel hazır bayrağı, kira veya fiyat gibi her sütunun dolu olduğunu göstermez.
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
       WHERE observation_available) AS gozlemi_bildirilen_katalog_metrigi,
      (SELECT count(*) FROM information_schema.tables
       WHERE table_schema NOT IN ('information_schema','pg_catalog')) AS duckdb_tablo_ve_gorunum,
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
       WHERE analysis_ready_source) AS kaynakla_analize_hazir_bolgesel_satir,
      (SELECT count(*) FROM regional.housing_quarterly
       WHERE analysis_ready_with_price_proxy) AS proxy_izinli_analize_hazir_bolgesel_satir
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
           round(housing_unit_price_try_per_m2, 2) AS resmi_birim_fiyat,
           round(housing_unit_price_with_proxy_try_per_m2, 2)
               AS proxy_izinli_birim_fiyat,
           housing_unit_price_proxy_origin AS fiyat_kokeni,
           analysis_ready_source AS kaynakla_analize_hazir,
           analysis_ready_with_price_proxy AS proxy_izinli_analize_hazir
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
- Aylık akım yalnız aynı tanım ve yıl içindeki hemen önceki takvim ayı varsa hesaplanır; eksik zincir ayrıca işaretlenir.
- Metrik etiketi ve sütun birimi tablo başlığından önceliklidir. Müşteri ve mudi sayıları bankalar arasında tekilleştirilmiş kişi sayısı değildir.
- Eksik değerler tahminle doldurulmaz. Sıfır yalnız aynı resmî kaynak içindeki
  kesin bir toplamsal kimlikle kanıtlanırsa, ham null korunarak ayrı provenance
  ile kullanılabilir.
- Kaynakta yayımlanmayan il fiyatları resmî sütunda null kalır. Ayrı analiz
  proxy'si yalnız aynı KFE bölgesi ve aynı çeyrekteki resmî il fiyatlarının
  medyanını kullanır, kökeni ve akran il sayısı açıkça tutulur.
- FinTürk'teki 1.328 kaynak-null şube değeri, 1.782 il-çeyreğin tamamında sıfır
  farkla geçen `SEKTÖR = MEVDUAT + KATILIM + KALKINMA VE YATIRIM` kimliğiyle
  ayrı `usable_value=0` ve audit kaydı olarak tutulur.
- BDDK haftalık kaynak boşlukları ile FinTürk yapısal boşlukları ayrı denetim
  tablolarında tutulur.
- Çeyreklik gözlem ara aylara forward fill edilmez.
- İl bazlı panelde eksik aylı çeyrekler kısmi toplamla doldurulmaz.
- EVDS akım toplamları eksik yerel dönem varsa null kalır; ortalama ve son değer için kapsam, seçilen tarih ve eskilik audit alanlarında tutulur.
- İpoteksiz satış, nakit satış olarak yorumlanmaz.
- Birlikte hareket nedensellik kanıtı sayılmaz. Olay belgeleri yalnız araştırma bağlamıdır.
"""

    known_gaps = """## Açık kalite notları

EVDS tüm-seri gözlem kapsamı tamamlanmadı. Kalıcı indirme kuyruğu metadata,
fiziksel seri, sayısal gözlem ve eksik dönemleri ayrı sayar. Yeni indirilen
dosyalar doğrulanıp yayımlanmadan lakehouse içinde sorgulanabilir sayılmaz.

1. TBB Haziran 2026 çeyreklik tüketici kredileri raporu 9 Eylül 2026
   kontrolünde kaynakta yayımlanmamıştır. Ayrı bir yayın ailesi olan Risk
   Merkezi Haziran 2026 aylık bülteni mevcuttur, ancak parasal kullandırım
   tutarı yayımlamaz.
2. EVDS `TP.MK.KUL.YTL` serisinin Haziran 2026 gözlemi kaynak cevabında yoktur.
3. Eski EVDS `TP.ALTINPIYASA.KAP05` serisi seyrektir ve 24 Kasım 2025'te biter.
4. Aktif altın kontrolü `TP.ALTINPIYASA.KAP02` 30 Haziran 2026'ya kadar doludur; TL/gram serisi açık `0.001` dönüşümüyle türetilir.
5. Ağustos 2025 EVDS ve BDDK konut kredisi kapsam farkı otomatik düzeltilmez.
6. TBB raporlayan banka kapsamı BDDK sektör toplamından daha dardır.
7. Beş ilin konut birim fiyatı serisi tamamen boştur. Altı ilin serisi 2023'te,
   Şırnak serisi 2022'de başlar. Toplam 162 il-çeyrek resmî birim fiyatı null
   kalır. Ayrı `with_proxy` alanı aynı bölge ve çeyrekteki resmî il fiyatı
   medyanını kullanır; resmî alanın yerine yazılmaz.
8. Ham EVDS'deki 9 yarışma dönemi ipotekli satış null değeri, TÜİK'teki
   `toplam satış = diğer satış` özdeşliğiyle sıfır olarak doğrulanıp 8
   il-çeyrek toplamında açık fallback provenance ile kullanılır.
9. Yedi ilin konut birim kira serisi tamamen boştur, altı ilin serisi ise
   kısmi kapsama sahiptir. Kira kontrolü eksik satış fiyatının yerine konmaz.

Bu boşluklar veri kaybı gibi gizlenmez. Katalog ve kalite tablolarında açıkça
görülebilir.
"""

    finish = """## Devam eden kapsam ve kullanım

Lakehouse metrik sözleşmeleri ve doğrulanan veri üzerinden kullanılır. Agent,
bir metriği kullanmadan önce birim, dönem, kapsam ve izinli işlemleri denetlemelidir.
`catalog.metric_bindings` durumu, genel `observation_available` bayrağından ayrıdır;
belirli bir sorunun hazır olması ayrıca talep edilen dönem ve hesapla doğrulanır.

EVDS'nin tüm gözlem kapsamını tamamlama işi sürüyor. `tools/evds_collection_queue.py`
ile yerel kapsam planlanabilir, durum görülebilir ve sınırlı sayıda indirme işi
devam ettirilebilir. Bu notebook Kloudeks modellerinin plan seçme başarısını veya
istatistiksel nedenselliği doğrulayan bir model değerlendirmesi değildir.
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
            new_markdown_cell("## EVDS metadata, fiziksel gözlem ve sayısal kapsam\n\nSayısal kapsam sayısı, tüm dönemlerin eksiksiz olduğu anlamına gelmez."),
            query_cell(f'connection.execute("""{EVDS_COVERAGE_SQL}""").fetchdf()', evds_coverage),
            new_markdown_cell("## Agent metrik sözleşmelerinin durumu\n\nBu durumlar seçilen soru için dönem ve işlem kontrolleriyle birlikte kullanılır."),
            query_cell(f'connection.execute("""{BINDING_STATUS_SQL}""").fetchdf()', binding_status),
            new_markdown_cell("## Kaynak varlıklarının yükleme durumu\n\nAynı kaynak farklı işleme katmanlarında birden fazla varlık olarak listelenebilir; toplam kayıt sayısı bağımsız gözlem sayısı değildir."),
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
