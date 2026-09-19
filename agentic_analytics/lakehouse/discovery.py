"""Explainable discovery hints, never authority to change a metric's contract.

Quantity semantics come from stored contracts. Product/recipient qualifiers are
title cues; a total currency slice is not proof of an overall population measure.
No metric identifiers, institution names or fixed question lists are used here.
"""

from __future__ import annotations

import re
import unicodedata


def _fold(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
                   if not unicodedata.combining(char))


QUALIFIERS = {
    "bank_recipient": r"\b(?:bankalara|bankaya|interbank)\b|\bloans?\s+to\s+banks?\b",
    "housing": r"\b(?:konut\w*|mortgage\w*|housing)\b",
    "vehicle": r"\b(?:tasit\w*|vehicle\w*|auto\s+loans?)\b",
    "consumer": r"\b(?:tuketici\w*|consumer\w*)\b",
    "personal": r"\b(?:ihtiyac\w*|personal\s+loans?)\b",
    "sme": r"\b(?:kobi\w*|sme\w*)\b",
    "commercial": r"\b(?:ticari\w*|commercial|business)\b",
    "guarantee": r"\bteminat\s+mektup\w*\b|\bguarantee\w*\b",
    "card": r"\b(?:kart\w*|cards?)\b",
    "overdue": r"\b(?:takip\w*|nonperforming|npl|sorunlu\w*)\b",
    "foreign_currency": r"\b(?:doviz\w*|foreign\s+currency|fx)\b",
    "per_capita": r"\b(?:kisi\s+basi\w*|per\s+(?:capita|person))\b",
}
FAMILIES = {
    "credit": r"\b(?:kredi\w*|loans?|credit)\b",
    "deposits": r"\b(?:mevduat\w*|deposits?)\b",
    "profit": r"\b(?:kar|kari|karin\w*|zarar\w*|profit\w*|loss\w*)\b",
    "employment": r"\b(?:calisan\w*|personel\w*|employee\w*|istihdam\w*)\b",
    "visits": r"\b(?:ziyaret\w*|visits?)\b",
    "sales": r"\b(?:satis\w*|sales?)\b",
}


def _hits(patterns, text):
    return sorted(name for name, expression in patterns.items() if re.search(expression, text))


def initial_query(message):
    """Project task instructions onto one source-metric request, before truncation.

    This only seeds catalogue discovery. It neither resolves company identities
    nor certifies that the suggested metric answers the complete user request.
    Unknown substantive words in the chosen clause deliberately remain visible.
    """
    text = str(message or "")
    opaque = r"\b(?:source|dataset|analysis|workspace)_[a-z0-9]{8,}\b|https?://\S+|\b[\w.-]+\.(?:csv|tsv|xlsx?|pdf|parquet|json|docx?)\b"
    has_artifact = bool(re.search(opaque, text, re.I))
    # Short focused catalogue queries retain all their original meaning.
    if len(text) <= 300 and len(text.split()) <= 18 and not has_artifact:
        return text
    clean = re.sub(opaque, " ", text, flags=re.I)
    clauses = [part.strip() for part in re.split(r"[.;!?\n]+", clean) if part.strip()]
    candidates = []
    for order, clause in enumerate(clauses):
        folded = _fold(clause)
        families = _hits(FAMILIES, folded)
        if not families:
            continue
        # Retrieval/comparison language distinguishes a desired catalogue
        # metric from an earlier description of the uploaded document.
        request = bool(re.search(r"\b(?:karsilastir\w*|getir\w*|bul\w*|goster\w*|compare\w*|retrieve\w*|find|show)\b", folded))
        description = bool(re.search(r"\b(?:iceriyor\w*|icermekte\w*|contains?|uploaded|kurgusal\w*|synthetic)\b", folded))
        candidates.append((int(request) * 4 - int(description) * 3, len(families), -order, clause))
    if not candidates:
        return clean.strip()[:300]
    selected = max(candidates)[-1]
    # Temporal labels and execution instructions are not metric names. This
    # vocabulary is independent of institutions, filenames and metric IDs.
    ignored = (r"\b(?:ayni|same|bu|this|that|the|a|an|bir|tek|bana|lutfen|please|"
               r"veriyi|verileri|data|dosya\w*|file\w*|kaynaklari|sources?|"
               r"calisma|alanina|workspaces?|kat|ekle\w*|import\w*|yukle\w*|upload\w*|"
               r"tabloda|tabloyu|tablosunu|table|karsilastir\w*|compare\w*|getir\w*|retrieve\w*|"
               r"goster\w*|show|bul|find|hesapla\w*|calculate\w*|olustur\w*|create\w*|"
               r"aylarin|aylar|ay|sonu|month\s+end|ocak|subat|mart|nisan|mayis|haziran|"
               r"temmuz|agustos|eylul|ekim|kasim|aralik|january|february|march|april|june|july|"
               r"august|september|october|november|december)\b")
    selected = re.sub(r"\baylardaki\b|\bay sonu\b|\bmonth end\b", "aylik", _fold(selected))
    selected = re.sub(ignored, " ", selected)
    selected = re.sub(r"\b\d{4}(?:[-/]\d{1,2}){0,2}\b|[,\-]", " ", selected)
    selected = re.sub(r"\bbakiy\w*\b", "bakiye", selected)
    selected = re.sub(r"\s+", " ", selected).strip()
    return selected[:300].rsplit(" ", 1)[0] if len(selected) > 300 else selected


def _meaning_terms(text):
    """Residual title context catches previously unseen product/recipient qualifiers.

This is a ranking hint, never a certificate of population equivalence.
"""
    ignored = {"toplam", "total", "overall", "genel", "tum", "butun", "ve", "and", "ile", "icin", "of", "the",
               "bankacilik", "banking", "sektor", "sektoru", "sektorunun", "banka", "banks", "bank", "bddk",
               "aylik", "monthly", "haftalik", "weekly", "annual", "yillik", "bakiye", "bakiyesi", "stok", "stoku",
               "stock", "balance", "outstanding", "nakdi", "cash", "tutar", "tutari", "amount", "miktar", "hacim",
               "kullandirilan", "verilen", "donem", "donemin", "tl", "try", "tp", "yp"}
    result = set()
    for word in re.findall(r"[a-z]+", text):
        if len(word) < 2 or word in ignored:
            continue
        result.add(next((name for name, pattern in {**FAMILIES, **QUALIFIERS}.items() if re.fullmatch(pattern, word)), word))
    return sorted(result)


def query_intent(query):
    text = _fold(query)
    measure = None
    if re.search(r"\b(?:adet\w*|sayi\w*|sayim\w*|count|number)\b", text):
        measure = "count"
    elif re.search(r"\b(?:oran\w*|ratio|rate|pay\w*)\b", text) or (
        re.search(r"\bfaiz\w*\b", text) and not re.search(r"\b(?:gelir\w*|gider\w*|income|expense)\b", text)
    ):
        measure = "rate_or_ratio"
    elif re.search(r"\b(?:tutar\w*|hacim\w*|miktar\w*|amount|volume|tl|try|usd|eur|lira\w*)\b", text):
        measure = "money"
    basis = "stock" if re.search(r"\b(?:bakiye\w*|stok\w*|stock|balance|outstanding)\b", text) else None
    if re.search(r"\b(?:akim\w*|flow|disbursements?)\b|\b(?:yeni|new)\s+(?:kredi\w*|loans?|kullandirilan\w*|acilan\w*)\b", text):
        basis = "flow"
    first_published = bool(re.search(
        r"\b(?:ilk\s+(?:yayin\w*|yayim\w*|yayinlan\w*|yayimlan\w*)|"
        r"orijinal\s+(?:yayin\w*|bulten\w*|deger\w*)|"
        r"first\s+(?:published|publication|release)|originally\s+published|as\s+published|contemporaneous)\b",
        text,
    ))
    current_revised = bool(re.search(
        r"\b(?:guncel\w*|revize\w*|duzeltil\w*|son\s+(?:surum\w*|revizyon\w*|vintage)|"
        r"current|latest|revised|restated)\b",
        text,
    ))
    vintage_preference = (
        "conflicting"
        if first_published and current_revised
        else "first_published"
        if first_published
        else "current_revised"
        if current_revised
        else "unspecified"
    )
    reference_years = sorted({int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", text)})
    return {"families": _hits(FAMILIES, text), "qualifiers": _hits(QUALIFIERS, text),
            "meaning_terms": _meaning_terms(text),
            "measure": measure, "time_basis": basis,
            "measurement_basis": "regulatory_liquidity_weighted" if re.search(r"\blikidite\w*\b|\bliquidity\b", text) else "source_reported" if basis == "stock" or measure == "money" else None,
            "overall": bool(re.search(r"\b(?:toplam\w*|total|overall|genel|tum|butun)\b", text)),
            "vintage_preference": vintage_preference,
            "reference_years": reference_years,
            "cash_class": "non_cash" if re.search(r"\bgayrinakdi\w*\b|\bnon[ -]?cash\b", text)
            else "cash" if re.search(r"\bnakdi\w*\b|\bcash\b", text) else None}


def semantic_profile(binding):
    # Bracketed slice labels are deliberately excluded from product meaning.
    title = _fold(re.sub(r"\[[^\]]*\]", " ", str(binding.get("title") or "")) + " " + str(binding.get("title_en") or ""))
    kind, unit = binding.get("kind"), binding.get("unit")
    measure = ("count" if kind in {"count", "count_stock", "count_flow"} or unit in {"count", "persons", "person", "visits"}
               else "rate_or_ratio" if kind in {"rate", "ratio"} or unit in {"percent", "%", "ratio", "percentage_point"}
               else "money" if binding.get("currency") and kind in {"stock", "flow"} else "other")
    families = _hits(FAMILIES, title)
    cash_class, inferred_cash = None, False
    slice_label = _fold(binding.get("value_dimension"))
    if re.search(r"\bgayrinakdi\w*\b|\bnon[ -]?cash\b", title) or "gayrinakdi" in slice_label:
        cash_class = "non_cash"
    elif re.search(r"\bnakdi\w*\b|\bcash\b", title) or "nakdi" in slice_label:
        cash_class = "cash"
    elif "credit" in families and measure == "money":
        cash_class, inferred_cash = "cash", True
    policy = _fold(binding.get("vintage_policy") or "")
    revision = _fold(binding.get("revision_status") or "")
    temporal = _fold(binding.get("temporal_semantics") or "")
    # Structured source fields outrank prose notes. A current-series warning
    # may literally say "not the first published series" and must never be
    # classified from that negated phrase.
    if re.search(r"first.?(?:official.)?(?:publication|published)|historical.first.publication", policy + " " + revision):
        vintage_class = "first_published"
    elif re.search(r"latest.official|current.official|current.revised|methodology.revision", policy + " " + revision):
        vintage_class = "current_revised"
    elif re.search(r"first.publication", temporal):
        vintage_class = "first_published"
    else:
        vintage_class = "unspecified"
    return {"measure": measure, "quantity_kind": kind, "time_basis": "stock" if kind in {"stock", "count_stock"} else "flow" if kind in {"flow", "count_flow"} else None,
            "families": families, "title_qualifiers": _hits(QUALIFIERS, title),
            "meaning_terms": _meaning_terms(title),
            "population_scope": binding.get("population_scope"),
            "measurement_basis": binding.get("measurement_basis", "source_reported"),
            "cash_class": cash_class, "cash_class_inferred": inferred_cash,
            "vintage_class": vintage_class,
            "meaning_title": re.sub(r"\s+", " ", title).strip(),
            "slice_label": binding.get("value_dimension"),
            "interpretation": "Title hints support candidate selection; a total slice does not certify total population coverage."}


def compare_intent(intent, profile):
    matches, conflicts, cautions = [], [], []
    for facet in ("measure", "time_basis", "cash_class", "measurement_basis"):
        expected, actual = intent.get(facet), profile.get(facet)
        if expected is None:
            continue
        if expected == actual:
            matches.append(facet)
        elif actual in {None, "other"}:
            cautions.append({"code": "semantic_facet_unverified", "facet": facet, "expected": expected})
        else:
            conflicts.append({"code": "semantic_facet_mismatch", "facet": facet, "expected": expected, "actual": actual})
    wanted, observed = set(intent["qualifiers"]), set(profile["title_qualifiers"])
    missing = sorted(wanted - observed)
    # A housing/vehicle/personal product can explicitly be labelled as consumer credit.
    allowed_parents = {"consumer"} if wanted & {"housing", "vehicle", "personal"} else set()
    extras = sorted(observed - wanted - allowed_parents) if set(intent["families"]) & set(profile["families"]) else []
    if missing:
        cautions.append({"code": "requested_qualifier_not_in_title", "qualifiers": missing})
    if extras:
        cautions.append({"code": "unrequested_metric_subset", "qualifiers": extras,
                         "message": "This title describes a narrower product, recipient or definition; do not substitute it for the requested overall measure."})
    common_family = set(intent["families"]) & set(profile["families"])
    additional_context = sorted(set(profile["meaning_terms"]) - set(intent["meaning_terms"]) - allowed_parents) if common_family else []
    if additional_context:
        cautions.append({"code": "additional_title_context", "terms": additional_context,
                         "message": "The source title has context absent from the request. Check whether this changes the population or definition."})
    absent_family = bool(intent["families"] and not common_family)
    if absent_family:
        cautions.append({"code": "requested_concept_not_in_title", "families": intent["families"]})
    if intent["cash_class"] == "cash" and profile["cash_class_inferred"]:
        cautions.append({"code": "cash_class_inferred", "message": "Monetary credit semantics suggest cash lending; inspect source footnotes before asserting exact scope equivalence."})
    restricted = bool(intent["overall"] and (profile.get("population_scope") or {}).get("exclusions"))
    if restricted:
        cautions.append({"code": "restricted_reporting_population", "message": "Publisher metadata documents excluded reporting institutions. This total cannot silently substitute for the complete reporting population; inspect population_scope and source_scope_evidence."})
    expected_vintage = intent.get("vintage_preference")
    observed_vintage = profile.get("vintage_class")
    if expected_vintage in {"first_published", "current_revised"}:
        if observed_vintage == expected_vintage:
            matches.append("vintage_policy")
        elif observed_vintage == "unspecified":
            cautions.append({"code": "vintage_policy_unverified", "expected": expected_vintage})
        else:
            conflicts.append({"code": "vintage_policy_mismatch", "expected": expected_vintage,
                              "actual": observed_vintage})
    elif expected_vintage == "conflicting":
        cautions.append({"code": "conflicting_vintage_request",
                         "message": "The request names both first-published and current/revised vintages; keep them separate."})
    penalty = (len(conflicts) * 100 + len(missing) * 30 + len(extras) * (35 if intent["overall"] else 25)
               + len(additional_context) * (12 if intent["overall"] else 8) + int(absent_family) * 40 + int(restricted) * 35)
    unverified = [warning["facet"] for warning in cautions if warning["code"] == "semantic_facet_unverified"]
    return {"status": "mismatch" if conflicts else "review" if missing or extras or additional_context or absent_family or unverified or restricted else "compatible",
            "matched_facets": matches, "conflicts": conflicts, "warnings": cautions,
            "unverified_facets": unverified, "unrequested_qualifiers": extras, "penalty": penalty}


def semantic_search_text(profile):
    terms = []
    if profile["families"] and set(profile["meaning_terms"]).issubset(profile["families"]) and not profile["title_qualifiers"]:
        terms.extend(["genel", "toplam"])
    if profile["measure"] == "count":
        terms.extend(["sayi", "adet", "count", "number"])
    elif profile["measure"] == "money":
        terms.extend(["tutar", "miktar", "hacim", "amount", "volume"])
    elif profile["measure"] == "rate_or_ratio":
        terms.extend(["oran", "ratio", "rate"])
        if profile["quantity_kind"] == "rate" and set(profile["families"]) & {"credit", "deposits"}:
            terms.extend(["faiz", "interest"])
    if profile["time_basis"] == "flow":
        terms.extend(["akim", "flow"])
    if profile["cash_class"] == "cash":
        terms.append("nakdi")
    return " ".join(terms)
