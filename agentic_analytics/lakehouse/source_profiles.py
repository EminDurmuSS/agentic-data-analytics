"""Source-wide population and measurement contracts, independent of metric names.

BDDK publishes different reporting populations in different monthly tables.
These are documented source-adapter rules, never preferred query answers.
"""

import re


EXPLANATIONS = "https://www.bddk.gov.tr/BultenAylik/tr/Home/Aciklama"
METADATA = "https://www.bddk.org.tr/BultenDosyalari/Home/Index/Aylik-MetaVeri"
POLICY_VERSION = "2026-09-12"
# The publisher explicitly excepts these table categories from exclusions.
_BROAD_TABLES = {"Bilanco", "Kar Zarar", "Likidite Durumu", "Sermaye Yeterliligi", "Yabanci Para Pozisyonu"}
_CATEGORIES = {1: "Bilanco", 2: "Kar Zarar", 3: "Krediler", 4: "Tuketici Kredileri",
               5: "Sektorel Kredi Dagilimi", 6: "KOBI Kredileri", 7: "Sendikasyon Sekuritizasyon Kredileri",
               8: "Menkul Kiymetler", 9: "Mevduat Turler Itibariyla", 10: "Mevduat Vade Itibariyla",
               11: "Likidite Durumu", 12: "Sermaye Yeterliligi", 13: "Yabanci Para Pozisyonu",
               14: "Bilanco Disi Islemler", 15: "Rasyolar", 16: "Diger Bilgiler", 17: "Yurt Disi Sube Rasyolari"}


def apply_source_profile(binding):
    """Enrich a trusted adapter binding without changing any source observation."""
    if binding.get("source_system") != "BDDK_MONTHLY":
        return binding
    match = re.search(r"\.table_(\d+)$", str(binding.get("dataset_id") or ""))
    if not match or int(match[1]) not in _CATEGORIES:
        return binding
    category = _CATEGORIES[int(match[1])]
    broad = category in _BROAD_TABLES
    exclusions = []
    if not broad:
        exclusions = [
            {"institution": "Birleşik Fon Bankası A.Ş.", "start": "2014-02", "end": None},
            {"institution": "Adabank A.Ş.", "start": "2014-02", "end": "2023-10"},
        ]
        if category != "Diger Bilgiler":
            exclusions.append({"institution": "Türk Ticaret Bankası A.Ş.", "start": "2022-04", "end": "2023-09"})
    # Do not assert identical populations merely because both tables say Sektör.
    population_id = ("bddk_monthly_full_reporting_population" if broad else
                     "bddk_monthly_restricted_population_other_information" if category == "Diger Bilgiler" else
                     "bddk_monthly_restricted_reporting_population")
    geographic_restriction = "domestic_resident_customers" if category == "Tuketici Kredileri" else None
    caveats = []
    if exclusions:
        caveats.append({"code": "reporting_population_exclusions", "source_url": EXPLANATIONS,
                        "message": "This table excludes reporting banks for the documented periods. Its Sektör total is not the same population as Bilanço's total.",
                        "exclusions": exclusions})
    if geographic_restriction:
        caveats.append({"code": "domestic_customers_only", "source_url": METADATA,
                        "message": "The consumer-credit table covers domestic resident customers; foreign resident customers are outside its scope."})
    weighted = category == "Likidite Durumu"
    if weighted:
        caveats.append({"code": "regulatory_weighting", "source_url": "https://www.bddk.org.tr/BultenAylik/tr/",
                        "source_locator": "Likidite Durumu table (11), uyari footnote",
                        "message": "Liquidity table amounts use regulatory liquidity weights; they are not interchangeable with unweighted balance-sheet amounts."})
    binding.update(
        population_scope={"id": population_id, "exclusions": exclusions, "customer_residency": geographic_restriction},
        measurement_basis="regulatory_liquidity_weighted" if weighted else "source_reported",
        scope_caveats=caveats, source_table_category=category,
        source_scope_policy_version=POLICY_VERSION,
        source_scope_evidence={"explanations_url": EXPLANATIONS, "metadata_url": METADATA,
                               "checked_on": POLICY_VERSION,
                               "explanations_sha256": "f5b51fd4af846754270d469b1a59e0758ceaf2183b666e85620aac8683fb08c1",
                               "metadata_sha256": "9d4ee709de46a4b627d69129d89838384199b09394ff1192d0a126580afcdd2a",
                               "source_discrepancy": "The metadata PDF does not close the Adabank/Türk Ticaret exclusion intervals. The interactive explanations give explicit end dates, retained here; both agree on the Birleşik Fon exclusion."},
    )
    return binding
