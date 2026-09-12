"""Canonical imported unit metadata, without changing source numeric cells."""
from __future__ import annotations

import math
import re
import unicodedata


KINDS = {"stock", "flow", "count", "count_stock", "count_flow", "ratio", "rate", "index", "price", "dimension", "unknown"}
CURRENCY_WORDS = {"TRY": ["tl", "try", "türk lirasi", "turkish lira"], "USD": ["usd", "dollar", "dollars", "dolar", "abd dolari"],
                  "EUR": ["eur", "euro", "euros", "avro"], "GBP": ["gbp", "sterlin", "pound", "pounds"]}
MULTIPLIER_WORDS = {1000: ["bin", "thousand", "thousands", "000"],
                    1000000: ["milyon", "million", "millions", "mn"],
                    1000000000: ["milyar", "billion", "billions", "bn"]}
UNIT_WORDS = {"percent": ["%", "yüzde", "percent"], "person": ["kişi", "person", "persons", "people"],
              "visits": ["ziyaret", "visits"], "count": ["adet", "count", "counts", "sayi"], "index": ["endeks", "index"]}


def _quote_identity(quote):
    lowered = unicodedata.normalize("NFKC", quote).casefold().replace("ı", "i").replace("_", " ")
    contains = lambda term: re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", lowered) is not None
    currencies = {unit for unit, words in CURRENCY_WORDS.items() if any(contains(word) for word in words)}
    scales = {scale for scale, words in MULTIPLIER_WORDS.items() if any(contains(word) for word in words)}
    units = {unit for unit, words in UNIT_WORDS.items() if any(contains(word.replace("ı", "i")) for word in words)}
    return currencies, scales, units, contains


def unit_header_conflict(unit, scale, header):
    """Only reject explicit local contradictions; a unitless header is neutral."""
    currencies, scales, units, _ = _quote_identity(header)
    canonical = "person" if unit == "persons" else unit
    if unit in CURRENCY_WORDS and currencies and currencies != {unit}:
        return True
    if canonical in UNIT_WORDS and units and canonical not in units:
        return True
    if unit in CURRENCY_WORDS and units or canonical in UNIT_WORDS and currencies:
        return True
    return bool(scales and scales != {scale})


def normalize_column(spec):
    """Return normalized metadata and proof; ambiguous declarations are rejected.

    An embedded multiplier and an explicit nonidentity scale are intentionally
    rejected rather than multiplied or silently discarded.
    """
    result = dict(spec)
    if result.get("kind") not in KINDS:
        raise ValueError("Choose a supported kind or unknown; arbitrary semantic labels cannot enable analytics.")
    aggregation = result.get("aggregation")
    if aggregation is not None:
        aliases = {"period_end_stock": "last", "period_end": "last", "stock": "last",
                   "monthly_flow": "sum", "period_flow": "sum", "flow": "sum",
                   "average": "mean", "non_additive": "none", "native": "none"}
        aggregation = aliases.get(aggregation, aggregation)
        allowed = {"stock": {"none", "last"}, "count_stock": {"none", "last"},
                   "flow": {"none", "sum"}, "count_flow": {"none", "sum"},
                   "rate": {"none", "mean", "last"}, "ratio": {"none", "mean", "last"},
                   "price": {"none", "mean", "last"}, "index": {"none", "mean", "last"}}.get(result["kind"], {"none"})
        if aggregation not in allowed:
            raise ValueError("Declared temporal aggregation conflicts with the metric kind or is unsupported.")
        result["aggregation"] = aggregation
    scale = result.get("scale", 1)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Unit scale must be a finite positive number.")
    unit = unicodedata.normalize("NFKC", result["unit"]).strip()
    folded = unit.casefold().replace("ı", "i")
    key = re.sub(r"[\s_-]+", "_", folded)
    currencies = {"try": "TRY", "tl": "TRY", "türk_lirasi": "TRY", "usd": "USD", "dolar": "USD", "dollar": "USD", "eur": "EUR", "euro": "EUR", "gbp": "GBP"}
    multipliers = {"thousand": 1000, "bin": 1000, "000": 1000, "million": 1000000, "milyon": 1000000, "mn": 1000000,
                   "billion": 1000000000, "milyar": 1000000000, "bn": 1000000000}
    canonical, embedded_scale = currencies.get(key), 1
    for multiplier, value in multipliers.items():
        for alias, currency in currencies.items():
            if key in {f"{multiplier}_{alias}", f"{alias}_{multiplier}"}:
                canonical, embedded_scale = currency, value
    if canonical is not None:
        if embedded_scale != 1 and scale != 1:
            raise ValueError("A scaled unit alias requires scale=1. Use the base currency with an explicit scale instead; do not apply the multiplier twice.")
        if result.get("currency") not in {None, canonical}:
            raise ValueError("Declared currency conflicts with the monetary unit.")
        result.update(unit=canonical, scale=embedded_scale if embedded_scale != 1 else scale, currency=canonical)
    else:
        result["unit"] = unit
    proof = None
    if any(result.get(key) != spec.get(key) for key in ("unit", "scale", "currency", "aggregation")):
        proof = {"declared": {key: spec.get(key) for key in ("unit", "scale", "currency", "aggregation")},
                 "canonical": {key: result.get(key) for key in ("unit", "scale", "currency", "aggregation")},
                 "values_changed": False}
    return result, proof


def unit_quote_matches(unit, scale, quote):
    currencies, scales, units, contains = _quote_identity(quote)
    if scales != ({scale} if scale != 1 else set()):
        return False
    if unit in CURRENCY_WORDS:
        return currencies == {unit}
    canonical = "person" if unit == "persons" else unit
    return canonical in units if canonical in UNIT_WORDS else contains(unit.casefold().replace("_", " "))
