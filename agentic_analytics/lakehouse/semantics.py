"""Reviewed units, frequencies and source policies shared by serving and builds."""
from __future__ import annotations

import re
from typing import Any

CONTRACT_VERSION = "1.0.0"
SEMANTIC_POLICY_VERSION = "1.2.0"
BDDK_WEEKLY_EXPLANATION_URL = "https://www.bddk.org.tr/BultenHaftalik/tr/Home/Aciklama"
FREQUENCIES = {
    "AYLIK": "monthly", "ÜÇ AYLIK": "quarterly", "YILLIK": "yearly",
    "ALTI AYLIK": "half_yearly", "GÜNLÜK": "daily", "İŞ GÜNÜ": "business_daily",
    "HAFTALIK(CUMA)": "weekly_friday", "HAFTALIK(ÇARŞAMBA)": "weekly_wednesday",
    "AYDA İKİ KEZ": "twice_monthly", "weekly": "weekly_friday",
}


MONTHLY_BANK_GROUPS = {
    10001: "Sektör",
    10002: "Mevduat",
    10003: "Katılım",
    10004: "Kalkınma ve Yatırım",
    10005: "Yerli Özel",
    10006: "Kamu",
    10007: "Yabancı",
    10008: "Mevduat-Yerli Özel",
    10009: "Mevduat-Kamu",
    10010: "Mevduat-Yabancı",
}


def apply_semantic_policy(binding: dict[str, Any]) -> dict[str, Any]:
    """Apply reviewed source roles without guessing from an index's label.

    The CPI role is documented in the source acquisition manifest
    data_pipeline/evds/manifests/housing_causality_v1.json. Other indices,
    including industrial production and housing prices, are not general CPI
    deflators. Persisted snapshots retain their original binding version/hash;
    the separate policy version identifies these execution-time constraints.
    """
    binding["semantic_policy_version"] = SEMANTIC_POLICY_VERSION
    if binding.get("source_system") == "BDDK_MONTHLY":
        # Monthly source rows lose group_name in their semantic long format.
        # Share the verified monthly catalog with ingestion. Weekly and FinTurk
        # group codes have different meanings.
        binding["dimension_labels"] = {"group_code": {str(code): label for code, label in MONTHLY_BANK_GROUPS.items()}}
        binding["dimension_labels_evidence"] = {"group_code": {
            "source": "data_pipeline/bddk/monthly_all_groups/request_config.json#groups",
            "source_url": "https://www.bddk.org.tr/BultenAylik/tr/",
            "verified_at": "2026-09-08", "namespace": "BDDK_MONTHLY"}}
    if binding.get("source_system") == "TCMB_EVDS" and binding.get("source_code") == "TP.TUKFIY2025.GENEL":
        binding.update(index_role="price_deflator", deflator_currency="TRY",
                       price_scope="Turkey general consumer price basket",
                       role_evidence="housing_causality_v1 manifest: price_deflator")
    if (binding.get("source_system") == "BDDK_WEEKLY"
            and str(binding.get("source_code") or "").startswith("table289:")):
        # Table 289 is the bulletin's credit-balance table. Keep the actual
        # publication observation date instead of coercing holiday-shifted
        # issues onto a synthetic Friday label.
        binding.update(
            native_frequency="weekly_observed",
            kind="stock",
            aggregation="last",
            additive_over_time=False,
            temporal_semantics="source_date_stock",
            measurement_basis="temporary_regulatory_financial_statement_balance",
            revision_status="provisional_revisable",
            source_url=BDDK_WEEKLY_EXPLANATION_URL,
            source_metadata_url=BDDK_WEEKLY_EXPLANATION_URL,
            source_frequency_evidence=(
                "BDDK Interactive Weekly Bulletin explanation: Bank Reporting "
                "System data are supplied daily or weekly."
            ),
            source_scope_evidence={
                "explanations_url": BDDK_WEEKLY_EXPLANATION_URL,
                "checked_on": "2026-09-19",
                "table_id": 289,
                "table_name": "Krediler",
                "date_policy": "preserve_actual_source_observation_date",
            },
        )
        caveats = list(binding.get("scope_caveats") or [])
        revision_caveat = {
            "code": "provisional_revisable_weekly_bulletin",
            "source_url": BDDK_WEEKLY_EXPLANATION_URL,
            "message": (
                "BDDK states that the weekly bulletin is prepared from temporary "
                "financial statements and a period value may change in later issues."
            ),
        }
        branch_caveat = {
            "code": "domestic_and_foreign_branches",
            "source_url": BDDK_WEEKLY_EXPLANATION_URL,
            "message": "BDDK weekly bank data cover domestic and foreign branches.",
        }
        for caveat in (revision_caveat, branch_caveat):
            if caveat not in caveats:
                caveats.append(caveat)
        binding["scope_caveats"] = caveats
    return binding


def normalized_unit(source_unit: str) -> tuple[str, float, str | None]:
    value = source_unit.strip().casefold().replace("ı", "i")
    units = {
        "milyon tl": ("TRY", 1e6, "TRY"), "million_try": ("TRY", 1e6, "TRY"),
        "bin tl": ("TRY", 1e3, "TRY"), "thousand_try": ("TRY", 1e3, "TRY"),
        "billion_try": ("TRY", 1e9, "TRY"), "billion_usd": ("USD", 1e9, "USD"),
        "tl": ("TRY", 1., "TRY"), "try": ("TRY", 1., "TRY"),
        "adet": ("count", 1., None), "count": ("count", 1., None),
        "person": ("person", 1., None), "kişi": ("person", 1., None),
        "thousand_person": ("person", 1e3, None), "million_person": ("person", 1e6, None),
        "yüzde": ("percent", 1., None), "%": ("percent", 1., None),
        "percent": ("percent", 1., None), "net yüzde değişim": ("percent", 1., None),
        "endeks": ("index", 1., None), "index": ("index", 1., None),
        "day": ("day", 1., None), "gün": ("day", 1., None),
        "tl/m2": ("TRY/m2", 1., "TRY"), "try_per_m2": ("TRY/m2", 1., "TRY"),
        "try_per_person": ("TRY/person", 1., "TRY"),
        "people_per_branch": ("person/branch", 1., None),
        "try_per_gram": ("TRY/gram", 1., "TRY"),
    }
    if value in units:
        return units[value]
    if value.startswith("index_") or re.fullmatch(r"\d{4}=100", value):
        return "index", 1., None
    return source_unit or "unknown", 1., None


KNOWN_UNITS = {"TRY", "USD", "count", "person", "percent", "index", "day",
               "TRY/m2", "TRY/gram", "TRY/kg", "TRY/person", "TRY/branch", "person/branch"}


def infer_evds_semantics(metric: dict[str, Any], code: str) -> tuple[str, float, str | None, str]:
    """Derive (unit, scale, currency, kind) for an unreviewed EVDS series.

    Series in evds.full_catalog carry a per-series unit (unlike coarser
    group-level labels elsewhere), so a small set of high-precision, false
    positive-averse rules can safely resolve many of them without a manual
    review entry. Anything that doesn't match a rule stays kind="unknown"
    (blocked_reason applies, status remains review_required) rather than
    guessing — a wrong fallback is worse than staying blocked.
    """
    unit, scale, currency = normalized_unit(metric.get("unit") or "")
    title = str(metric.get("metric_name_tr") or "")
    name = f"{title} {metric.get('group_name') or ''}".casefold()
    if unit == "percent":
        return "percent", 1., None, "rate"
    if unit == "index":
        return "index", 1., None, "index"
    if unit not in KNOWN_UNITS:
        # normalized_unit() did not recognize the raw source label (it fell
        # through to returning that label unchanged, e.g. an EVDS catalog
        # "unit" field that actually names an aggregation method such as
        # "Ağırlıklı ortalama" rather than a physical unit). Name/title
        # tokens are then the only high-precision signal available.
        if "%" in title or any(token in name for token in ("faiz", "oran")):
            return "percent", 1., None, "rate"
        if "endeks" in name or "index" in name:
            return "index", 1., None, "index"
        return unit, scale, currency, "unknown"
    if unit in {"TRY", "USD"} and any(token in name for token in ("stok", "bakiye", "toplam varlık")):
        return unit, scale, currency, "stock"
    return unit, scale, currency, "unknown"


def kind_for(semantics: str, unit: str) -> str:
    semantics = semantics.casefold()
    if "flow" in semantics or semantics == "monthly_event_count":
        return "flow"
    if "index" in semantics or unit == "index":
        return "index"
    if semantics in {"count", "count_stock"}:
        return "count_stock"
    if "stock" in semantics:
        return "stock" if unit in {"TRY", "USD"} else "count_stock" if unit in {"count", "person"} else "unknown"
    if "ratio" in semantics or "per_capita" in semantics or semantics in {"monetary_per_entity", "duration", "non_additive_rate"}:
        return "ratio"
    if "rate" in semantics or semantics == "quarterly_survey":
        return "rate"
    if "price" in semantics or "/" in unit:
        return "price"
    if semantics in {"period_end_level", "quarter_end_level", "quarterly_level"} and unit in {"TRY", "USD"}:
        return "stock"
    return "unknown"
