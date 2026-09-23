"""Deterministic query readiness classification over catalog.metric_bindings.

Encodes, as a reusable and queryable function instead of a one-off manual
audit, the classification methodology used by BÖLÜM 4 ("Veri Kaynağı
Doğrulama Tablosu") of
``/home/neo/Downloads/SENARYO ÇIKTILARI VE ASIL PROBLEM/kkb_hackathon_25_demo_senaryolari.md``:
for a natural-language query, is the underlying data already queryable, a
known-but-undownloaded EVDS series, a plausible-but-unconfirmed candidate, a
deliberately out-of-lakehouse (web/PDF) topic, or genuinely absent?

This module adds one state the manual audit did not need to name explicitly
because a human was doing the judgment call: ``near_match_available``, for
the case where ``discover`` found real semantic candidates but none is an
exact/ready hit. Making that state explicit -- instead of leaving the caller
to reformulate the same query over and over against the same near-matches --
is the deterministic hook commit 10 uses to cap retries and resolve from
already-ranked evidence instead of discarding it at NO_PROGRESS.

Every classification here is a pure function of one ``discover()`` call: it
performs no repeated search, no budget consumption beyond that single call,
and a query with zero exact and zero near candidates (e.g. "XBANK", the
BÖLÜM 4 "bilinen risk" row that previously fed a DECISION_BUDGET_EXCEEDED
crash) returns ``unavailable`` immediately rather than leaving the caller to
retry until a budget is exhausted.
"""
from __future__ import annotations

import unicodedata
from typing import Any

# Topics BÖLÜM 4 and docs/eval-set/COMMIT_PLAN_STATUS.md's "Kapsam sınırı"
# section document as *deliberately* outside the lakehouse -- real official
# sources exist, but they are web/PDF-only by design (Hazine DİBS ihaleleri,
# KAP, MKK, SPK bültenleri, Resmi Gazete, ODMD, TCMB haftalık rezerv
# bültenleri as a live web page, KGF/asgari ücret kararları). A query about
# one of these topics that matches nothing in the catalog should be told to
# go to a specific known official source, not treated the same as a term
# that simply has no match anywhere (which is the "unavailable" case this
# commit's acceptance criterion names, using XBANK as the example).
KNOWN_EXTERNAL_TOPICS: dict[str, str] = {
    "dibs": "https://www.hmb.gov.tr/ (Hazine ihale sonuçları)",
    "gosterge tahvil": "https://www.hmb.gov.tr/ (Hazine gösterge tahvil)",
    "kap": "https://www.kap.org.tr/ (Kamuyu Aydınlatma Platformu)",
    "mkk": "https://www.mkk.com.tr/ (Merkezi Kayıt Kuruluşu)",
    "spk": "https://www.spk.gov.tr/ (Sermaye Piyasası Kurulu bültenleri)",
    "odmd": "https://www.odmd.org.tr/ (Otomotiv Distribütörleri Derneği)",
    "protesto": "Resmi Gazete / ilgili ticaret sicili kaynağı (Protestolu Senet lakehouse'ta yok)",
    "kgf": "https://www.kgf.com.tr/ (Kredi Garanti Fonu)",
    "lcr": "BDDK/TCMB likidite karşılama oranı bültenleri (lakehouse'ta LCR yok, 'Likidite' genel eşleşmesiyle karıştırılmamalı)",
    "xbank": None,  # Deliberately NOT here: see the "unavailable" note above.
}


def _fold(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
        if not unicodedata.combining(c)
    )


def _matches_known_external_topic(query: str) -> tuple[str, str] | None:
    folded = _fold(query)
    for topic, source in KNOWN_EXTERNAL_TOPICS.items():
        if source is None:
            continue
        # Substring, not whole-word: Turkish agglutinative suffixes
        # ("protesto" -> "protestolu") must still match the stem.
        if topic in folded:
            return topic, source
    return None


def classify_query_readiness(service: Any, query: str, *, limit: int = 25) -> dict:
    """Classify ``query`` into one of five deterministic readiness states.

    ``service`` is a ``LakehouseService`` (or anything exposing the same
    ``discover(request) -> dict`` contract). Returns a dict with at least
    ``status`` (one of ``ready``, ``acquirable``, ``near_match_available``,
    ``web_required``, ``unavailable``) and ``reason``; callers that want the
    underlying evidence get it back verbatim in ``matches``/``near_matches``.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    result = service.discover({"query": query, "limit": limit})
    metrics = result.get("metrics", [])
    no_confident_match = bool(result.get("no_confident_match"))

    if not no_confident_match and metrics:
        ready = [m for m in metrics if m.get("status") == "ready"]
        if ready:
            return {
                "status": "ready",
                "reason": f"{len(ready)} status=ready metric(s) matched directly.",
                "matches": ready,
                "near_matches": [],
            }
        acquirable = [m for m in metrics if m.get("status") == "metadata_only"]
        if acquirable:
            return {
                "status": "acquirable",
                "reason": (
                    f"{len(acquirable)} known EVDS series matched but not yet downloaded; "
                    "acquire with tools/EVDS_Talep_Uzerine_Indirme_Araci.py (workspace-scoped, commit 7) "
                    "then promote with tools/promote_on_demand_series.py before it is queryable."
                ),
                "matches": acquirable,
                "near_matches": [],
            }
        # A full lexical match exists but every candidate is blocked
        # (review_required/no_numeric): real evidence, not yet a delivered
        # number. Surface it as a near match rather than silently ready.
        return {
            "status": "near_match_available",
            "reason": f"{len(metrics)} matched candidate(s) exist but none is status=ready.",
            "matches": [],
            "near_matches": metrics,
        }

    # discover() found no full lexical match. It may still have surfaced
    # near-match hints (no_confident_match=True with populated metrics).
    if metrics:
        return {
            "status": "near_match_available",
            "reason": f"No exact match; {len(metrics)} semantic near-match candidate(s) available.",
            "matches": [],
            "near_matches": metrics,
        }

    # Zero exact, zero near candidates. This is the deterministic terminus:
    # classify once, do not retry.
    known = _matches_known_external_topic(query)
    if known:
        topic, source = known
        return {
            "status": "web_required",
            "reason": f"'{topic}' is a deliberately out-of-lakehouse topic; official source: {source}",
            "matches": [],
            "near_matches": [],
        }
    return {
        "status": "unavailable",
        "reason": "No exact or near candidate in catalog.metric_bindings for this query.",
        "matches": [],
        "near_matches": [],
    }
