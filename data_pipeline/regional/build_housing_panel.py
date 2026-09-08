#!/usr/bin/env python3
"""Build a validated province-quarter housing and household-finance panel.

The panel joins official province sales and unit-price observations from EVDS
with BDDK FinTurk sector totals. Regional KFE and YKKE values are attached to
each province through an explicit province-to-region dimension. Source nulls
remain null. "Non-mortgaged" sales are never described as cash sales.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVDS_CATALOG = PROJECT_ROOT / "data_pipeline" / "catalog" / "evds_series_catalog.parquet"
REGIONAL_EVDS = PROJECT_ROOT / "data_pipeline" / "evds" / "regional_housing_v1"
CORE_EVDS = PROJECT_ROOT / "data_pipeline" / "evds" / "housing_causality_v1"
FINTURK = (
    PROJECT_ROOT
    / "data_pipeline"
    / "bddk"
    / "processed"
    / "finturk_all_groups_all_cities"
    / "measurements_long.parquet"
)
TUIK_SALES = (
    PROJECT_ROOT
    / "data_pipeline"
    / "tuik"
    / "province_housing_sales_v1"
    / "processed"
    / "monthly_sales_long.parquet"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data_pipeline" / "regional" / "processed"

TARGET_START = "2021Q1"
TARGET_END = "2026Q2"
SECTOR_GROUP_CODE = 10001

SALES_GROUPS = {
    "bie_akonutsat1": "housing_sales_total_count",
    "bie_akonutsat2": "housing_sales_mortgaged_count",
    "bie_akonutsat3": "housing_sales_first_hand_count",
    "bie_akonutsat4": "housing_sales_second_hand_count",
}

FINTURK_METRICS = {
    "KonutKredisi": "housing_credit_stock_thousand_try",
    "AltinDepoGercek": "real_person_gold_deposit_stock_thousand_try",
    "AltinDepoToplam": "gold_deposit_stock_thousand_try",
    "TasarrufMevduati": "savings_deposit_stock_thousand_try",
    "NakdiKrediler": "cash_credit_stock_thousand_try",
    "KisiBasiNakdiKredi": "cash_credit_per_capita_try",
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def normalize_key(value: Any) -> str:
    text = str(value).strip().replace("İ", "I").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def safe_ratio(numerator: pd.Series, denominator: pd.Series, factor: float) -> pd.Series:
    valid = numerator.notna() & denominator.notna() & denominator.gt(0)
    result = pd.Series(np.nan, index=numerator.index, dtype="float64")
    result.loc[valid] = factor * numerator.loc[valid] / denominator.loc[valid]
    return result


def target_quarters() -> list[str]:
    return pd.period_range(TARGET_START, TARGET_END, freq="Q").astype(str).tolist()


def build_province_dimension(
    catalog: pd.DataFrame, finturk_cities: list[str]
) -> pd.DataFrame:
    canonical_by_key = {normalize_key(city): city for city in finturk_cities}
    if len(canonical_by_key) != 81:
        raise ValueError("FinTurk il adlari normalize edildiginde 81 tekil il bekleniyor.")

    dimension = pd.DataFrame(
        {
            "province_name": finturk_cities,
            "province_key": [normalize_key(city).lower() for city in finturk_cities],
        }
    )

    for group_code, output_column in SALES_GROUPS.items():
        rows = catalog.loc[
            catalog["group_code"].eq(group_code)
            & catalog["series_code"].astype(str).str.match(r"^TP\.AKONUTSAT[1-4]\.KTR")
            & ~catalog["series_code"].astype(str).str.endswith("KTRTOPLAM")
            & catalog["series_name_tr"].fillna("").str.contains("_Konut_")
            & ~catalog["is_archive"].astype(bool)
        ].copy()
        rows["province_name"] = (
            rows["series_name_tr"].astype(str).str.split("_Konut_", n=1).str[0]
        )
        rows["province_name"] = rows["province_name"].map(normalize_key).map(
            canonical_by_key
        )
        if len(rows) != 81 or rows["province_name"].isna().any():
            raise ValueError(f"{group_code} icin 81 il serisi eslenemedi.")
        mapping = rows[["province_name", "series_code"]].rename(
            columns={"series_code": f"{output_column}_series_code"}
        )
        dimension = dimension.merge(mapping, on="province_name", validate="one_to_one")

    unit_prices = catalog.loc[
        catalog["group_code"].eq("bie_birimfiyat")
        & ~catalog["series_code"].eq("TP.BIRIMFIYAT.TR")
        & ~catalog["is_archive"].astype(bool)
    ].copy()
    unit_prices["province_name"] = unit_prices["series_name_tr"].astype(str).str.replace(
        " Konut Birim Fiyatları", "", regex=False
    )
    unit_prices["province_name"] = unit_prices["province_name"].map(normalize_key).map(
        canonical_by_key
    )
    if len(unit_prices) != 81 or unit_prices["province_name"].isna().any():
        raise ValueError("Konut birim fiyatinda 81 il serisi eslenemedi.")
    dimension = dimension.merge(
        unit_prices[["province_name", "series_code"]].rename(
            columns={"series_code": "housing_unit_price_series_code"}
        ),
        on="province_name",
        validate="one_to_one",
    )

    region_rows: list[dict[str, str]] = []
    kfe = catalog.loc[
        catalog["group_code"].eq("bie_kfe")
        & ~catalog["series_code"].eq("TP.KFE.TR")
        & ~catalog["is_archive"].astype(bool)
    ]
    for row in kfe.to_dict("records"):
        name = str(row["series_name_tr"])
        match = re.search(r"^([^ ]+) \((.+)\)$", name)
        if not match:
            raise ValueError(f"KFE bolge adi okunamadi: {name}")
        region_code, province_list = match.groups()
        for source_province in province_list.split(","):
            canonical = canonical_by_key.get(normalize_key(source_province))
            if canonical is None:
                raise ValueError(
                    f"KFE bolgesindeki il FinTurk ile eslesmedi: {source_province}"
                )
            suffix = str(row["series_code"]).removeprefix("TP.KFE.")
            region_rows.append(
                {
                    "province_name": canonical,
                    "housing_price_region_code": region_code,
                    "housing_price_region_name": name,
                    "regional_kfe_series_code": str(row["series_code"]),
                    "regional_ykke_series_code": f"TP.YKKE.{suffix}",
                }
            )
    region_map = pd.DataFrame(region_rows)
    if len(region_map) != 81 or region_map["province_name"].duplicated().any():
        raise ValueError("KFE bolge haritasi 81 ili tekil olarak kapsamiyor.")
    dimension = dimension.merge(region_map, on="province_name", validate="one_to_one")

    available_ykke = set(
        catalog.loc[
            catalog["group_code"].eq("bie_ykke") & ~catalog["is_archive"].astype(bool),
            "series_code",
        ].astype(str)
    )
    missing_ykke = sorted(set(dimension["regional_ykke_series_code"]) - available_ykke)
    if missing_ykke:
        raise ValueError(f"KFE bolgeleri icin YKKE serisi eksik: {missing_ykke}")
    return dimension.sort_values("province_key", kind="stable").reset_index(drop=True)


def build_sales_panel(
    observations: pd.DataFrame,
    dimension: pd.DataFrame,
    tuik_sales: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int], pd.DataFrame]:
    mapping_rows = []
    for output_column in SALES_GROUPS.values():
        source_column = f"{output_column}_series_code"
        mapping_rows.extend(
            {
                "series_code": row[source_column],
                "province_name": row["province_name"],
                "metric": output_column,
            }
            for row in dimension.to_dict("records")
        )
    mapping = pd.DataFrame(mapping_rows)
    rows = observations.merge(mapping, on="series_code", how="inner", validate="many_to_one")
    rows["month"] = rows["period"].astype(str)
    rows["quarter"] = pd.to_datetime(rows["period_end"]).dt.to_period("Q").astype(str)
    rows = rows.loc[rows["quarter"].isin(target_quarters())]
    rows["evds_value"] = rows["value"]

    fallback = tuik_sales.loc[
        tuik_sales["metric_code"].eq("housing_sales_mortgaged_count")
        & tuik_sales["is_identity_derived"],
        [
            "province_name",
            "month",
            "metric_code",
            "value",
            "source_csv_sha256",
            "value_origin",
        ],
    ].rename(
        columns={
            "value": "tuik_fallback_value",
            "source_csv_sha256": "tuik_source_csv_sha256",
            "value_origin": "tuik_value_origin",
        }
    )
    rows = rows.merge(
        fallback,
        left_on=["province_name", "month", "metric"],
        right_on=["province_name", "month", "metric_code"],
        how="left",
        validate="many_to_one",
    )
    fallback_used = (
        rows["metric"].eq("housing_sales_mortgaged_count")
        & rows["evds_value"].isna()
        & rows["tuik_fallback_value"].notna()
        & rows["tuik_value_origin"].eq(
            "official_identity_total_equals_other_implies_zero_mortgaged"
        )
    )
    rows["fallback_used"] = fallback_used
    rows["usable_value"] = rows["evds_value"]
    rows.loc[fallback_used, "usable_value"] = rows.loc[
        fallback_used, "tuik_fallback_value"
    ]
    rows["fallback_month"] = rows["month"].where(fallback_used)
    rows["fallback_source_sha256"] = rows["tuik_source_csv_sha256"].where(
        fallback_used
    )

    grouped = rows.groupby(["province_name", "quarter", "metric"], sort=True)
    audit = grouped.agg(
        source_observation_count=("value", "size"),
        evds_non_null_observation_count=("evds_value", "count"),
        usable_non_null_observation_count=("usable_value", "count"),
        value=("usable_value", lambda values: values.sum(min_count=len(values))),
        fallback_month_count=("fallback_used", "sum"),
        fallback_months=(
            "fallback_month",
            lambda values: "|".join(sorted(values.dropna().astype(str))),
        ),
        fallback_source_sha256=(
            "fallback_source_sha256",
            lambda values: "|".join(sorted(set(values.dropna().astype(str)))),
        ),
    ).reset_index()
    audit.loc[
        audit["source_observation_count"].ne(3)
        | audit["usable_non_null_observation_count"].ne(3),
        "value",
    ] = np.nan
    incomplete_before_fallback = int(
        (
            audit["source_observation_count"].ne(3)
            | audit["evds_non_null_observation_count"].ne(3)
        ).sum()
    )
    incomplete_after_fallback = int(
        (
            audit["source_observation_count"].ne(3)
            | audit["usable_non_null_observation_count"].ne(3)
        ).sum()
    )
    panel = audit.pivot(
        index=["province_name", "quarter"], columns="metric", values="value"
    ).reset_index()
    panel.columns.name = None
    sales_completeness = (
        audit.assign(
            evds_metric_complete=lambda frame: frame[
                "source_observation_count"
            ].eq(3)
            & frame["evds_non_null_observation_count"].eq(3),
            usable_metric_complete=lambda frame: frame[
                "source_observation_count"
            ].eq(3)
            & frame["usable_non_null_observation_count"].eq(3),
        )
        .groupby(["province_name", "quarter"], as_index=False)
        .agg(
            sales_evds_source_complete=("evds_metric_complete", "all"),
            sales_source_complete=("usable_metric_complete", "all"),
        )
    )
    panel = panel.merge(
        sales_completeness,
        on=["province_name", "quarter"],
        how="left",
        validate="one_to_one",
    )
    mortgage_audit = audit.loc[
        audit["metric"].eq("housing_sales_mortgaged_count"),
        [
            "province_name",
            "quarter",
            "source_observation_count",
            "evds_non_null_observation_count",
            "usable_non_null_observation_count",
            "fallback_month_count",
            "fallback_months",
            "fallback_source_sha256",
        ],
    ].copy()
    mortgage_audit["mortgaged_sales_fallback_used"] = mortgage_audit[
        "fallback_month_count"
    ].gt(0)
    mortgage_audit["mortgaged_sales_source"] = np.where(
        mortgage_audit["mortgaged_sales_fallback_used"],
        "TCMB_EVDS+TUIK_DATA_PORTAL_IDENTITY_FALLBACK",
        "TCMB_EVDS",
    )
    mortgage_audit = mortgage_audit.rename(
        columns={
            "fallback_month_count": "mortgaged_sales_fallback_month_count",
            "fallback_months": "mortgaged_sales_fallback_months",
            "fallback_source_sha256": "mortgaged_sales_tuik_source_sha256",
        }
    )
    panel = panel.merge(
        mortgage_audit[
            [
                "province_name",
                "quarter",
                "mortgaged_sales_fallback_used",
                "mortgaged_sales_fallback_month_count",
                "mortgaged_sales_fallback_months",
                "mortgaged_sales_source",
                "mortgaged_sales_tuik_source_sha256",
            ]
        ],
        on=["province_name", "quarter"],
        how="left",
        validate="one_to_one",
    )
    stats = {
        "incomplete_before_fallback": incomplete_before_fallback,
        "incomplete_after_fallback": incomplete_after_fallback,
        "fallback_month_count": int(rows["fallback_used"].sum()),
        "fallback_province_quarter_count": int(
            mortgage_audit["mortgaged_sales_fallback_used"].sum()
        ),
    }
    return panel, stats, mortgage_audit


def build_quarter_end_series(
    observations: pd.DataFrame,
    dimension: pd.DataFrame,
    code_column: str,
    value_column: str,
) -> pd.DataFrame:
    mapping = dimension[["province_name", code_column]].rename(
        columns={code_column: "series_code"}
    )
    rows = observations.merge(mapping, on="series_code", how="inner", validate="many_to_many")
    rows["period_end_date"] = pd.to_datetime(rows["period_end"])
    rows = rows.loc[rows["period_end_date"].dt.month.mod(3).eq(0)].copy()
    rows["quarter"] = rows["period_end_date"].dt.to_period("Q").astype(str)
    rows = rows.loc[rows["quarter"].isin(target_quarters())]
    result = rows[["province_name", "quarter", "value"]].rename(
        columns={"value": value_column}
    )
    if result.duplicated(["province_name", "quarter"]).any():
        raise ValueError(f"{value_column} il-ceyrek anahtari tekrarlaniyor.")
    return result


def build_finturk_panel(measurements: pd.DataFrame) -> pd.DataFrame:
    rows = measurements.loc[
        measurements["group_code"].eq(SECTOR_GROUP_CODE)
        & ~measurements["city"].eq("YURT DIŞI")
        & measurements["measure_code"].isin(FINTURK_METRICS)
    ].copy()
    rows["quarter"] = pd.PeriodIndex(
        pd.to_datetime(rows["quarter"]), freq="Q"
    ).astype(str)
    rows = rows.loc[rows["quarter"].isin(target_quarters())]
    if rows.duplicated(["province_name" if "province_name" in rows else "city", "quarter", "measure_code"]).any():
        raise ValueError("FinTurk il-ceyrek-metrik anahtari tekrarlaniyor.")
    panel = rows.pivot(
        index=["city", "quarter"], columns="measure_code", values="value"
    ).reset_index()
    panel.columns.name = None
    panel = panel.rename(columns={"city": "province_name", **FINTURK_METRICS})
    return panel


def metric_dictionary() -> pd.DataFrame:
    rows = [
        ("housing_sales_total_count", "Toplam konut satışı", "count", "quarterly_flow", "EVDS aylık il satışlarının üç aylık toplamı", ""),
        ("housing_sales_mortgaged_count", "İpotekli konut satışı", "count", "quarterly_flow", "EVDS aylık il satışlarının üç aylık toplamı", "İpotekli satış finansman türünü gösterir, kredi kullandırım tutarı değildir."),
        ("housing_sales_non_mortgaged_count", "İpoteksiz konut satışı", "count", "derived_quarterly_flow", "toplam satış - ipotekli satış", "Nakit satış olarak etiketlenemez."),
        ("housing_sales_first_hand_count", "İlk el konut satışı", "count", "quarterly_flow", "EVDS aylık il satışlarının üç aylık toplamı", ""),
        ("housing_sales_second_hand_count", "İkinci el konut satışı", "count", "quarterly_flow", "EVDS aylık il satışlarının üç aylık toplamı", ""),
        ("mortgaged_sales_share_pct", "İpotekli satış payı", "percent", "derived_ratio", "100 * ipotekli satış / toplam satış", ""),
        ("non_mortgaged_sales_share_pct", "İpoteksiz satış payı", "percent", "derived_ratio", "100 * (toplam satış - ipotekli satış) / toplam satış", "Nakit satış olarak etiketlenemez."),
        ("first_hand_sales_share_pct", "İlk el satış payı", "percent", "derived_ratio", "100 * ilk el satış / toplam satış", ""),
        ("second_hand_sales_share_pct", "İkinci el satış payı", "percent", "derived_ratio", "100 * ikinci el satış / toplam satış", ""),
        ("housing_unit_price_try_per_m2", "Konut birim fiyatı", "try_per_m2", "quarter_end_level", "EVDS il bazlı kaynak gözlemi", ""),
        ("regional_kfe_index", "Bölgesel konut fiyat endeksi", "index", "quarter_end_index", "İlin bağlı olduğu EVDS KFE bölgesinin çeyrek sonu gözlemi", "İl özelinde değil, bölgesel değerdir."),
        ("regional_ykke_index", "Bölgesel yeni kiracı kira endeksi", "index", "quarter_end_index", "İlin bağlı olduğu EVDS YKKE bölgesinin çeyrek sonu gözlemi", "İl özelinde değil, bölgesel değerdir."),
        ("housing_credit_stock_thousand_try", "Konut kredisi bakiyesi", "thousand_try", "period_end_stock", "BDDK FinTurk sektör toplamı", "Yeni kullandırım akımı değildir."),
        ("cash_credit_stock_thousand_try", "Nakdi krediler", "thousand_try", "period_end_stock", "BDDK FinTurk sektör toplamı", "Kişi başı konut kredisi paydasını türetmek için kullanılır."),
        ("cash_credit_per_capita_try", "Kişi başı nakdi kredi", "try_per_person", "per_capita_level", "BDDK FinTurk sektör toplamı", ""),
        ("real_person_gold_deposit_stock_thousand_try", "Gerçek kişi altın mevduatı", "thousand_try", "period_end_stock", "BDDK FinTurk sektör toplamı", ""),
        ("gold_deposit_stock_thousand_try", "Toplam altın mevduatı", "thousand_try", "period_end_stock", "BDDK FinTurk sektör toplamı", ""),
        ("savings_deposit_stock_thousand_try", "Tasarruf mevduatı", "thousand_try", "period_end_stock", "BDDK FinTurk sektör toplamı", ""),
        ("real_person_gold_deposit_share_pct", "Altın mevduatında gerçek kişi payı", "percent", "derived_ratio", "100 * gerçek kişi altın mevduatı / toplam altın mevduatı", ""),
        ("implied_population_person", "FinTurk metriklerinden türetilmiş nüfus", "person", "derived_denominator", "nakdi krediler * 1000 / kişi başı nakdi kredi", "Resmî nüfus serisi değil, yayımlanmış iki FinTurk metriğinden ters hesaplanan yaklaşık paydadır."),
        ("housing_credit_per_capita_try", "Kişi başı konut kredisi", "try_per_person", "derived_ratio", "konut kredisi * 1000 / türetilmiş nüfus", "Nüfus paydası FinTurk metriklerinden ters hesaplanır."),
        ("housing_credit_to_savings_deposit_pct", "Konut kredisi / tasarruf mevduatı", "percent", "derived_ratio", "100 * konut kredisi / tasarruf mevduatı", ""),
        ("housing_unit_price_yoy_pct", "Konut birim fiyatı yıllık değişimi", "percent", "derived_growth", "Dört çeyrek önceki il birim fiyatına göre değişim", "Kaynak gözlem yoksa hesaplanmaz."),
        ("regional_kfe_yoy_pct", "Bölgesel KFE yıllık değişimi", "percent", "derived_growth", "Dört çeyrek önceki bölgesel KFE değerine göre değişim", "İl özelinde değil, bölgesel değerdir."),
        ("regional_ykke_yoy_pct", "Bölgesel YKKE yıllık değişimi", "percent", "derived_growth", "Dört çeyrek önceki bölgesel YKKE değerine göre değişim", "İl özelinde değil, bölgesel değerdir."),
        ("housing_credit_yoy_pct", "Konut kredisi bakiyesi yıllık değişimi", "percent", "derived_growth", "Dört çeyrek önceki FinTurk konut kredisi bakiyesine göre değişim", "Yeni kullandırım akımı değildir."),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "metric_code",
            "metric_name_tr",
            "unit",
            "temporal_semantics",
            "derivation",
            "caution",
        ],
    )


def build(output_dir: Path) -> dict[str, Any]:
    catalog = pd.read_parquet(EVDS_CATALOG)
    regional_observations = pd.read_parquet(REGIONAL_EVDS / "observations_long.parquet")
    core_observations = pd.read_parquet(CORE_EVDS / "observations_long.parquet")
    finturk = pd.read_parquet(FINTURK)
    tuik_sales = pd.read_parquet(TUIK_SALES)
    finturk_cities = sorted(set(finturk["city"].dropna()) - {"YURT DIŞI"})
    dimension = build_province_dimension(catalog, finturk_cities)

    base = dimension[[
        "province_key",
        "province_name",
        "housing_price_region_code",
        "housing_price_region_name",
    ]].merge(pd.DataFrame({"quarter": target_quarters()}), how="cross")

    sales, sales_stats, mortgage_audit = build_sales_panel(
        regional_observations, dimension, tuik_sales
    )
    unit_price = build_quarter_end_series(
        regional_observations,
        dimension,
        "housing_unit_price_series_code",
        "housing_unit_price_try_per_m2",
    )

    kfe_observations = pd.concat(
        [
            core_observations.loc[core_observations["series_code"].str.startswith("TP.KFE.")],
            regional_observations.loc[
                regional_observations["series_code"].str.startswith("TP.KFE.")
            ],
        ],
        ignore_index=True,
    )
    if kfe_observations["series_code"].nunique() != 20:
        raise ValueError("Bolgesel panel icin 20 KFE serisi bulunmadi.")
    kfe = build_quarter_end_series(
        kfe_observations,
        dimension,
        "regional_kfe_series_code",
        "regional_kfe_index",
    )
    ykke = build_quarter_end_series(
        regional_observations,
        dimension,
        "regional_ykke_series_code",
        "regional_ykke_index",
    )
    finturk_panel = build_finturk_panel(finturk)

    panel = base
    for source in [sales, unit_price, kfe, ykke, finturk_panel]:
        panel = panel.merge(
            source, on=["province_name", "quarter"], how="left", validate="one_to_one"
        )
    panel["quarter_end"] = pd.PeriodIndex(panel["quarter"], freq="Q").end_time.date

    panel["housing_sales_non_mortgaged_count"] = (
        panel["housing_sales_total_count"] - panel["housing_sales_mortgaged_count"]
    )
    panel["mortgaged_sales_share_pct"] = safe_ratio(
        panel["housing_sales_mortgaged_count"], panel["housing_sales_total_count"], 100
    )
    panel["non_mortgaged_sales_share_pct"] = safe_ratio(
        panel["housing_sales_non_mortgaged_count"], panel["housing_sales_total_count"], 100
    )
    panel["first_hand_sales_share_pct"] = safe_ratio(
        panel["housing_sales_first_hand_count"], panel["housing_sales_total_count"], 100
    )
    panel["second_hand_sales_share_pct"] = safe_ratio(
        panel["housing_sales_second_hand_count"], panel["housing_sales_total_count"], 100
    )
    panel["real_person_gold_deposit_share_pct"] = safe_ratio(
        panel["real_person_gold_deposit_stock_thousand_try"],
        panel["gold_deposit_stock_thousand_try"],
        100,
    )
    panel["implied_population_person"] = safe_ratio(
        panel["cash_credit_stock_thousand_try"],
        panel["cash_credit_per_capita_try"],
        1000,
    )
    panel["housing_credit_per_capita_try"] = safe_ratio(
        panel["housing_credit_stock_thousand_try"],
        panel["implied_population_person"],
        1000,
    )
    panel["housing_credit_to_savings_deposit_pct"] = safe_ratio(
        panel["housing_credit_stock_thousand_try"],
        panel["savings_deposit_stock_thousand_try"],
        100,
    )

    panel = panel.sort_values(["province_key", "quarter"], kind="stable").reset_index(
        drop=True
    )
    for source_column, output_column in [
        ("housing_unit_price_try_per_m2", "housing_unit_price_yoy_pct"),
        ("regional_kfe_index", "regional_kfe_yoy_pct"),
        ("regional_ykke_index", "regional_ykke_yoy_pct"),
        ("housing_credit_stock_thousand_try", "housing_credit_yoy_pct"),
    ]:
        panel[output_column] = (
            panel.groupby("province_key", sort=False)[source_column]
            .pct_change(4, fill_method=None)
            .mul(100)
        )

    if not panel["sales_source_complete"].eq(
        panel[list(SALES_GROUPS.values())].notna().all(axis=1)
    ).all():
        raise ValueError("Satış kullanılabilirlik bayrağı veri sütunlarıyla tutarsız.")
    panel["regional_price_source_complete"] = panel[
        ["housing_unit_price_try_per_m2", "regional_kfe_index", "regional_ykke_index"]
    ].notna().all(axis=1)
    panel["finturk_source_complete"] = panel[list(FINTURK_METRICS.values())].notna().all(
        axis=1
    )
    panel["analysis_ready"] = panel[
        ["sales_source_complete", "regional_price_source_complete", "finturk_source_complete"]
    ].all(axis=1)

    first_second_difference = (
        panel["housing_sales_total_count"]
        - panel["housing_sales_first_hand_count"]
        - panel["housing_sales_second_hand_count"]
    )
    first_second_violations = int(first_second_difference.abs().gt(0).sum())
    mortgaged_over_total = int(
        panel["housing_sales_mortgaged_count"].gt(panel["housing_sales_total_count"]).sum()
    )
    negative_non_mortgaged = int(panel["housing_sales_non_mortgaged_count"].lt(0).sum())
    unit_price_coverage = panel.groupby("province_name", sort=True)[
        "housing_unit_price_try_per_m2"
    ].agg(non_null="count", total="size")
    unit_price_no_observation_provinces = unit_price_coverage.loc[
        unit_price_coverage["non_null"].eq(0)
    ].index.tolist()
    unit_price_partial_coverage_provinces = unit_price_coverage.loc[
        unit_price_coverage["non_null"].gt(0)
        & unit_price_coverage["non_null"].lt(unit_price_coverage["total"])
    ].index.tolist()

    output_dir.mkdir(parents=True, exist_ok=True)
    dimension.to_csv(output_dir / "province_dimension.csv", index=False, encoding="utf-8-sig")
    dimension.to_parquet(output_dir / "province_dimension.parquet", index=False)
    panel.to_csv(output_dir / "province_quarter_housing_panel.csv", index=False, encoding="utf-8-sig")
    panel.to_parquet(output_dir / "province_quarter_housing_panel.parquet", index=False)
    mortgage_audit.to_csv(
        output_dir / "mortgaged_sales_fallback_audit.csv",
        index=False,
        encoding="utf-8-sig",
    )
    mortgage_audit.to_parquet(
        output_dir / "mortgaged_sales_fallback_audit.parquet", index=False
    )
    dictionary = metric_dictionary()
    dictionary.to_csv(output_dir / "metric_dictionary.csv", index=False, encoding="utf-8-sig")
    dictionary.to_parquet(output_dir / "metric_dictionary.parquet", index=False)

    expected_rows = 81 * len(target_quarters())
    duplicate_keys = int(panel.duplicated(["province_key", "quarter"]).sum())
    hard_failures = any(
        [
            len(panel) != expected_rows,
            panel["province_key"].nunique() != 81,
            panel["quarter"].nunique() != 22,
            duplicate_keys,
            first_second_violations,
            mortgaged_over_total,
            negative_non_mortgaged,
        ]
    )
    explicit_source_gaps = sales_stats["incomplete_after_fallback"] or panel[
        [
            "housing_unit_price_try_per_m2",
            "regional_kfe_index",
            "regional_ykke_index",
            *FINTURK_METRICS.values(),
        ]
    ].isna().any().any()
    status = (
        "failed"
        if hard_failures
        else "passed_with_source_gaps"
        if explicit_source_gaps
        else "passed"
    )
    validation = {
        "status": status,
        "coverage_start": TARGET_START,
        "coverage_end": TARGET_END,
        "province_count": int(panel["province_key"].nunique()),
        "quarter_count": int(panel["quarter"].nunique()),
        "row_count": len(panel),
        "expected_row_count": expected_rows,
        "duplicate_province_quarter_keys": duplicate_keys,
        "regional_evds_source_series_count": int(
            regional_observations["series_code"].nunique()
        ),
        "kfe_source_series_count": int(kfe_observations["series_code"].nunique()),
        "ykke_source_series_count": int(
            regional_observations.loc[
                regional_observations["series_code"].str.startswith("TP.YKKE."),
                "series_code",
            ].nunique()
        ),
        "incomplete_province_quarter_sales_aggregations_before_fallback": sales_stats[
            "incomplete_before_fallback"
        ],
        "incomplete_province_quarter_sales_aggregations_after_fallback": sales_stats[
            "incomplete_after_fallback"
        ],
        "tuik_identity_fallback_month_count": sales_stats["fallback_month_count"],
        "tuik_identity_fallback_province_quarter_count": sales_stats[
            "fallback_province_quarter_count"
        ],
        "total_equals_first_plus_second_violations": first_second_violations,
        "mortgaged_sales_over_total_violations": mortgaged_over_total,
        "negative_non_mortgaged_sales_violations": negative_non_mortgaged,
        "analysis_ready_rows": int(panel["analysis_ready"].sum()),
        "housing_unit_price_no_observation_provinces": unit_price_no_observation_provinces,
        "housing_unit_price_partial_coverage_provinces": unit_price_partial_coverage_provinces,
        "source_null_counts": {
            column: int(panel[column].isna().sum())
            for column in [
                *SALES_GROUPS.values(),
                "housing_unit_price_try_per_m2",
                "regional_kfe_index",
                "regional_ykke_index",
                *FINTURK_METRICS.values(),
            ]
        },
        "quality_policy": [
            "The panel contains exactly one row per province and quarter.",
            "Monthly sales are summed only when all three source months are present and non-null.",
            "EVDS mortgaged-sales nulls use a TÜİK fallback only when official total sales equals official other sales and therefore proves a zero.",
            "Raw EVDS nulls remain unchanged; fallback provenance and source SHA-256 are stored in separate panel columns.",
            "KFE and YKKE use the observed quarter-end month and are not forward filled.",
            "Quarterly unit prices remain quarter-end levels and are not copied into monthly periods.",
            "FinTurk uses the official SEKTOR total and excludes YURT DISI from the 81-province panel.",
            "Non-mortgaged sales are not labelled as cash sales.",
            "Implied population is an explicit approximation from two published FinTurk metrics.",
            "All divisions return null for missing or non-positive denominators.",
        ],
    }
    atomic_json(output_dir / "validation.json", validation)
    if status == "failed":
        raise ValueError(f"Bolgesel konut paneli dogrulamasi gecmedi: {validation}")
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
