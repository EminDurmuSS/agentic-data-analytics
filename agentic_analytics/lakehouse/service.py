"""Deterministic, typed agent tools over immutable lakehouse snapshots.

The request language contains metric identifiers and operations, never SQL or paths.
Model integration can call these methods directly without owning database access.
"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator
from urllib.parse import urlsplit

import duckdb
import numpy as np
import pandas as pd

from agentic_analytics.lakehouse.store import LakehouseStore, StoreError, VersionConflict


class PlanError(ValueError):
    """A machine-readable failure of a semantic or structural request contract."""

    def __init__(self, message: str, *, code: str = "INVALID_PLAN"):
        super().__init__(message)
        self.code = code


def error_envelope(error: Exception) -> dict:
    """Keep errors actionable for an agent without parsing human message text."""
    if isinstance(error, PlanError):
        code = error.code
    elif isinstance(error, VersionConflict):
        code = "VERSION_CONFLICT"
    elif isinstance(error, json.JSONDecodeError):
        code = "INVALID_JSON"
    elif isinstance(error, StoreError):
        code = "STORE_CONTRACT_ERROR"
    elif isinstance(error, duckdb.Error):
        code = "DATA_ACCESS_ERROR"
    elif isinstance(error, OSError):
        code = "IO_ERROR"
    else:
        code = "INVALID_INPUT"
    return {"status": "blocked", "errors": [{"code": code, "message": str(error)}]}


def _object(value: Any, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown or missing:
        raise PlanError(f"{label}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value) or value == "period":
        raise PlanError("Column names must be identifiers of at most 64 characters, excluding period")
    return value


def _identifier(value: str) -> str:
    # Registry identifiers are trusted application configuration, still quote them.
    # Up to 3 parts: a plain column/schema.table, or database.schema.table for an
    # attached database (the on-demand EVDS overlay is addressed this way; see
    # agentic_analytics/lakehouse/registry.py ON_DEMAND_DB_PATH).
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}", value):
        raise PlanError("Invalid identifier in metric binding")
    return ".".join('"' + part + '"' for part in value.split("."))


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, pd.Period, date, datetime)):
        return str(value)
    return value


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold().replace("ı", "i")) if not unicodedata.combining(c))


def _search_terms(value: str) -> list[str]:
    # Small, explicit vocabulary only. Search proposes candidates; it never
    # changes metric definitions, dimension values or readiness.
    aliases = {"unemployment": "issiz", "inflation": "enflasyon", "cpi": "tufe",
               "deposits": "mevduat", "deposit": "mevduat", "gold": "altin",
               "npl": "takip", "nonperforming": "takip", "takipteki": "takip",
               "mortgage": "konut", "housing": "konut", "ratio": "oran", "orani": "oran",
               "share": "pay", "payi": "pay", "profit": "kar", "profitability": "kar",
               "loan": "kredi",
               "credit": "kredi", "loans": "kredi", "capital": "sermaye",
               "cash": "nakdi", "total": "toplam", "overall": "toplam", "balance": "bakiye", "outstanding": "bakiye",
               "adequacy": "yeterli", "yeterlilik": "yeterli", "yeterliligi": "yeterli",
               "issizlik": "issiz", "mevduati": "mevduat",
               "kredisi": "kredi", "kredileri": "kredi", "krediler": "kredi",
               "kredisinin": "kredi", "kredisini": "kredi", "kredilerinin": "kredi", "kredilerini": "kredi",
               "stock": "bakiye", "stok": "bakiye", "stoku": "bakiye", "stoklari": "bakiye",
               "bakiyesi": "bakiye", "bakiyeleri": "bakiye", "bakiyesini": "bakiye", "bakiyesinin": "bakiye", "bakiyeleriyle": "bakiye", "bakiyelerini": "bakiye",
               "bulteni": "bulten", "bultenleri": "bulten", "bulletin": "bulten",
               "kari": "kar", "karin": "kar", "karini": "kar", "karinin": "kar", "karlari": "kar",
               "bank": "banka", "banks": "banka", "banking": "banka", "bankalar": "banka",
               "bankalarin": "banka", "bankalarinin": "banka", "bankacilik": "banka", "bankaciligi": "banka",
               "bankaciligin": "banka", "bankaciliginin": "banka", "sektoru": "sektor", "sektorunun": "sektor",
               "satis": "satis", "satisi": "satis", "satislari": "satis", "satislarini": "satis",
               "satislarinin": "satis", "satislar": "satis", "sales": "satis",
               "endeksi": "endeks", "indices": "endeks", "index": "endeks",
               "imkb": "bist", "ise": "bist", "il": "province", "iller": "province",
               "illeri": "province", "ilinde": "province", "illerde": "province"}
    # Dots and colons are valid inside a metric code, but sentence punctuation
    # at a token edge must not become an impossible catalogue search term.
    words = [word.strip(".:") for word in re.findall(r"[a-z0-9_:.]+", _fold(value))]
    return [aliases.get(word, word) for word in words if word]


# Only the non-"toplam"/"total"-prefixed aggregate token needs listing; the rest
# are caught by the startswith checks in _is_total_slice.
_TOTAL_SLICE = {"tumvarlikyukumluluk"}


def _is_total_slice(card: dict) -> bool:
    # The aggregate ('Toplam'/'TOTAL') slice is the analyst default among sibling
    # metrics that differ only by a currency/size/maturity/type slice token.
    value_dimension = _fold(str(card.get("value_dimension") or ""))
    if not value_dimension or value_dimension in {"toplamtp", "toplamyp"}:
        # A currency-split total (Total-TRY/Total-FX) is not the grand aggregate default.
        return False
    return value_dimension in _TOTAL_SLICE or value_dimension.startswith("toplam") or value_dimension.startswith("total")


def _term_matches(term: str, text: str, *, whole_word: bool = False) -> bool:
    # Dimension values are a closed proper-noun vocabulary: match them as whole tokens
    # so a concept fragment ('gumus') never spuriously satisfies a longer value
    # ('gumushane'). Free-text titles keep agglutinative substring recall.
    if len(term) <= 3 or whole_word:
        # Full token first so a proper noun ending in 'i' (Kocaeli) still matches
        # itself, then its suffix-stemmed form as a fallback.
        if re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text):
            return True
        if whole_word and len(term) >= 5 and term.endswith("i"):
            return re.search(r"(?<![a-z0-9_])" + re.escape(term[:-1]) + r"(?![a-z0-9_])", text) is not None
        return False
    return term in text or (len(term) >= 5 and term.endswith("i") and term[:-1] in text)


def _query_terms(value: str) -> tuple[list[str], set[str]]:
    """Separate query intent/calendar hints from searchable metric meaning."""
    stopwords = {"lutfen", "bana", "bir", "ve", "ile", "icin", "mi", "nedir", "ne", "kadar",
                 "hesapla", "hesaplayabilir", "goster", "analiz", "et", "eder", "misin", "istiyorum",
                 "ver", "veri", "verileri", "sonuc", "sonuclari", "tablo", "tablosu", "tum", "butun",
                 "ilk", "son", "ikinci", "ucuncu", "dorduncu", "yil", "yili", "yilin", "ay", "ayi",
                 "yilina", "yilinda", "ayin", "ceyrek", "ceyrekte", "ceyreginde", "en", "yuksek", "dusuk",
                 "sirala", "olarak", "ait", "da", "de", "bu", "simdi", "pekala", "tabloya", "tabloyu",
                 "ekle", "ekleyin", "ekler", "serisi", "serisini", "verisi", "verisini", "deger", "degeri",
                 "degerini", "ariyorum", "isterim", "istiyoruz", "ocak", "subat", "mart", "nisan", "mayis",
                 "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik",
                 "bazinda", "ortak", "bulunan", "anlam", "anlami", "birim", "birimi", "gercekten",
                 "eslesen", "eslesme", "gosterge", "gostergeler", "gostergeleri", "katalog", "katalogda",
                 "kataloglar", "kataloglarda", "kataloglarinda", "iki", "kaynak", "kaynakta",
                 "kaynaklarda", "kaynaktaki", "kaynaklardaki", "kod", "kodu", "kodlar", "kodlari",
                 "kodlariyla", "liste", "listele", "listeleyebilir", "ayri", "sutun", "sutunlar",
                 "sutunlarda"}
    frequency_hints = {"aylik": "monthly", "monthly": "monthly", "ceyreklik": "quarterly",
                       "quarterly": "quarterly", "haftalik": "weekly", "weekly": "weekly",
                       "gunluk": "daily", "daily": "daily", "yillik": "annual", "annual": "annual"}
    terms = _search_terms(value)
    return (list(dict.fromkeys(term for term in terms if term not in stopwords and term not in frequency_hints and not term.isdecimal())),
            {frequency_hints[term] for term in terms if term in frequency_hints})


_SOURCE_ALIASES = {
    "bddk": {"source_system": ("bddk_",), "organization": ("bddk",), "domains": ("bddk.org.tr",)},
    "finturk": {"source_system": ("bddk_finturk",), "organization": ("finturk",)},
    "tcmb": {"source_system": ("tcmb_evds", "tcmb_evds_derived"), "organization": ("tcmb",), "domains": ("tcmb.gov.tr",)},
    "evds": {"source_system": ("tcmb_evds", "tcmb_evds_derived"), "organization": ("evds",), "domains": ("evds2.tcmb.gov.tr", "evds3.tcmb.gov.tr")},
    "tuik": {"source_system": ("tuik_data_portal",), "organization": ("tuik",), "domains": ("tuik.gov.tr",)},
    "tbb": {"source_system": ("tbb_",), "organization": ("turkiye bankalar birligi",), "domains": ("tbb.org.tr",)},
    "bist": {"source_system": (), "organization": ("borsa istanbul", "bist"), "domains": ("borsaistanbul.com",)},
}


def _requested_sources(value: str) -> list[str]:
    """Extract explicit source names separately from metric meaning.

    Source names are routing constraints, not words that every candidate title
    must contain. This matters for requests comparing two official catalogues.
    """
    folded = _fold(value)
    aliases = []
    for alias in _SOURCE_ALIASES:
        patterns = {
            "tuik": r"\btuik\b|\bturkiye\s+istatistik\s+kurumu\b",
            "tbb": r"\btbb\b|\bturkiye\s+bankalar\s+birligi\b",
            "bist": r"\bbist\b|\bborsa\s+istanbul\b|\bimkb\b|\bise\b",
        }
        pattern = patterns.get(alias, rf"\b{re.escape(alias)}\b")
        match = re.search(pattern, folded)
        if match:
            aliases.append((match.start(), alias))
    return [alias for _, alias in sorted(aliases)]


def _source_match(binding: dict, requested: list[str]) -> dict | None:
    if not requested:
        return None
    system = _fold(binding.get("source_system") or "")
    organization = _fold(binding.get("source_organization") or "")
    # Official web overlays keep SESSION_DATASET identity. Route them by the
    # persisted origin URL, never by a user-controlled title that mentions an
    # institution. Routing does not certify the values or change their scope.
    source_host = ""
    if system == "session_dataset":
        try:
            source_url = binding.get("source_url") or ""
            if not isinstance(source_url, str):
                source_url = ""
            parsed = urlsplit(source_url)
            if (parsed.scheme in {"http", "https"} and parsed.hostname
                    and parsed.username is None and parsed.password is None
                    and parsed.port in {None, 80, 443}
                    and not any(ord(character) < 32 for character in source_url)):
                source_host = parsed.hostname.lower().rstrip(".")
        except (ValueError, TypeError):
            pass
    matches = []
    for alias in requested:
        spec = _SOURCE_ALIASES[alias]
        direct = any(system == token or system.startswith(token) for token in spec["source_system"])
        indirect = any(re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", organization)
                       for token in spec["organization"])
        origin = any(source_host == domain or source_host.endswith("." + domain)
                     for domain in spec.get("domains", ()))
        if direct or indirect or origin:
            matches.append({"requested_source": alias,
                            "basis": "source_system" if direct else "source_organization" if indirect else "source_url_domain",
                            "strength": 3 if direct else 1})
    if not matches:
        return None
    strongest = max(matches, key=lambda item: (item["strength"], -requested.index(item["requested_source"])))
    return {**strongest, "matched_sources": [item["requested_source"] for item in matches],
            "requested_source_count": len(requested)}


def _coverage_includes_years(card: dict, years: list[int]) -> bool:
    """Return whether a discovery card covers every explicitly requested year."""
    if not years:
        return True
    try:
        start = int(str(card.get("coverage_start") or "")[:4])
        end = int(str(card.get("coverage_end") or "")[:4])
    except ValueError:
        return False
    return start <= min(years) and max(years) <= end


def _vintage_rank(intent: dict, card: dict) -> int:
    """Rank source vintages without hiding a competing official definition.

    Explicit user wording always wins. For a closed historical period whose
    first-publication and later-revised series are both available, the
    contemporaneous publication is ranked first for reproducibility. Discovery
    also emits the competing current series and a mandatory disclosure below.
    """
    observed = (card.get("semantic_profile") or {}).get("vintage_class", "unspecified")
    requested = intent.get("vintage_preference", "unspecified")
    if requested in {"first_published", "current_revised"}:
        return 0 if observed == requested else 1 if observed == "unspecified" else 2
    years = intent.get("reference_years") or []
    if years and _coverage_includes_years(card, years):
        return 0 if observed == "first_published" else 1 if observed == "current_revised" else 2
    return 0 if observed == "current_revised" else 1 if observed == "unspecified" else 2


def _vintage_selection(intent: dict, cards: list[dict]) -> dict | None:
    """Describe competing official vintages that share one source metric code."""
    groups: dict[tuple, list[dict]] = {}
    years = intent.get("reference_years") or []
    for card in cards:
        vintage = (card.get("semantic_profile") or {}).get("vintage_class")
        source_code = card.get("source_code")
        if vintage not in {"first_published", "current_revised"} or not source_code:
            continue
        if years and not _coverage_includes_years(card, years):
            continue
        key = (
            card.get("source_system"),
            source_code,
            tuple(sorted((card.get("dimensions") or {}).items())),
            card.get("kind"),
            card.get("unit"),
            card.get("scale"),
        )
        groups.setdefault(key, []).append(card)
    competing = []
    for grouped in groups.values():
        classes = {(card.get("semantic_profile") or {}).get("vintage_class") for card in grouped}
        if classes != {"first_published", "current_revised"}:
            continue
        competing.append({
            "source_metric_code": grouped[0].get("source_code"),
            "candidates": [{
                "metric_id": card["metric_id"],
                "vintage_class": (card.get("semantic_profile") or {}).get("vintage_class"),
                "vintage_policy": card.get("vintage_policy"),
                "revision_status": card.get("revision_status"),
                "coverage_start": card.get("coverage_start"),
                "coverage_end": card.get("coverage_end"),
            } for card in sorted(grouped, key=lambda item: _vintage_rank(intent, item))],
        })
    if not competing:
        return None
    requested = intent.get("vintage_preference", "unspecified")
    historical = bool(years)
    recommended = requested if requested in {"first_published", "current_revised"} else (
        "first_published" if historical else "current_revised"
    )
    return {
        "status": "explicit" if requested in {"first_published", "current_revised"} else "requires_disclosure",
        "requested": requested,
        "recommended": recommended,
        "reference_years": years,
        "candidate_groups": competing,
        "message": (
            "The requested concept has distinct official first-published and current-revised vintages. "
            "Do not mix their cells. Preserve the chosen metric on follow-up turns and disclose the alternative."
        ),
    }


def _flow_period_count(native: str, target: str) -> int | None:
    return {("monthly", "quarterly"): 3, ("monthly", "annual"): 12,
            ("monthly", "yearly"): 12, ("quarterly", "annual"): 4,
            ("quarterly", "yearly"): 4}.get((native, target))


_FREQUENCIES = {"monthly": "M", "quarterly": "Q", "weekly_observed": "D", "weekly_friday": "W-FRI", "weekly_wednesday": "W-WED", "weekly": "W-FRI", "daily": "D", "business_daily": "B", "annual": "Y", "yearly": "Y", "half_yearly": "2Q-DEC", "twice_monthly": "D"}
_FREQUENCY_RANK = {"daily": 0, "business_daily": 0, "weekly_observed": 1, "weekly_friday": 1, "weekly_wednesday": 1, "weekly": 1, "twice_monthly": 1, "monthly": 2, "quarterly": 3, "half_yearly": 4, "annual": 5, "yearly": 5}
_NATIVE_ONLY_FREQUENCIES = {"half_yearly", "twice_monthly"}


def _period(value: Any, frequency: str) -> pd.Period:
    pattern = r"\d{4}-H[12]" if frequency == "half_yearly" else r"\d{4}-\d{2}" if frequency == "monthly" else r"\d{4}-Q[1-4]" if frequency == "quarterly" else r"\d{4}" if frequency in {"annual", "yearly"} else r"\d{4}-\d{2}-\d{2}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PlanError(f"Expected {frequency} period, got {value!r}")
    try:
        if frequency == "half_yearly":
            # Anchor on Q1 or Q3 explicitly; a generic six-month stride can
            # otherwise start in any month and silently shift source periods.
            quarter = 1 if value.endswith("H1") else 3
            return pd.Period(f"{value[:4]}Q{quarter}", freq="2Q-DEC")
        period = pd.Period(value, freq=_FREQUENCIES[frequency])
        if frequency in {"weekly", "weekly_friday", "weekly_wednesday"} and period.end_time.date().isoformat() != value:
            raise PlanError("Weekly bounds must be native week-ending dates")
        return period
    except (ValueError, KeyError) as exc:
        raise PlanError(f"Invalid period {value}") from exc


def _label(period: pd.Period) -> str:
    if period.freqstr == "2Q-DEC":
        return f"{period.year:04d}-H{1 if period.quarter == 1 else 2}"
    if period.freqstr.startswith("Q"):
        return str(period).replace("Q", "-Q")
    if period.freqstr.startswith("W"):
        return period.end_time.date().isoformat()
    return str(period)


class LakehouseService:
    """Typed discovery, dimension, scalar, grouped, revision and evidence tools."""

    def __init__(self, store: LakehouseStore, workspace_id: str):
        self.store = store
        self.workspace_id = workspace_id

    @contextmanager
    def _context(self, workspace: dict | None = None) -> Iterator[tuple[Any, dict, dict]]:
        from agentic_analytics.lakehouse.registry import get_bindings

        workspace = workspace or self.store.workspace(self.workspace_id)
        connection = duckdb.connect(str(self.store.snapshot_path(workspace["snapshot_id"])), read_only=True)
        try:
            bindings = get_bindings(connection)
            # Overlays are application-validated manifests, not model-provided SQL.
            for dataset_id in workspace.get("datasets", []):
                manifest = self.store.dataset_manifest(dataset_id)
                contract = manifest["contract"]
                provenance = contract.get("document_provenance")
                table = "overlay_" + re.sub(r"[^A-Za-z0-9_]", "_", dataset_id)
                connection.register(table, pd.read_parquet(self.store.overlay_path(dataset_id)))
                for column, definition in contract["columns"].items():
                    if definition["dtype"] not in {"integer", "float"} or column in contract["grain"]:
                        continue
                    metric_id = f"overlay:{dataset_id}:{column}"
                    from agentic_analytics.lakehouse.units import normalize_column
                    from agentic_analytics.lakehouse.financial_semantics import cumulative_evidence
                    try:
                        definition, unit_normalization = normalize_column(definition)
                    except ValueError:
                        # Older stored contracts remain readable, but invalid semantic
                        # declarations must not become permission for arithmetic.
                        definition = {**definition, "kind": "unknown"}
                        unit_normalization = None
                    kind = definition.get("kind", "unknown")
                    if kind not in {"stock", "flow", "count", "count_stock", "count_flow", "ratio", "rate", "index", "price"}:
                        kind = "unknown"
                    cumulative = cumulative_evidence(definition)
                    price_basis = definition.get("price_basis")
                    invalid_price_basis = price_basis is not None and (not isinstance(price_basis, str) or not price_basis.strip())
                    if isinstance(price_basis, str) and _fold(price_basis).strip() in {"nominal", "current_prices", "current prices", "cari", "cari fiyatlar"}:
                        price_basis = None
                    declared_review = definition.get("status") == "review_required"
                    review_required = kind == "unknown" or bool(cumulative) or invalid_price_basis or declared_review
                    reason = ("Imported cumulative/YTD observations remain source values. Convert them with source-verified year boundaries before arithmetic or period aggregation."
                              if cumulative else "The declared price basis must be a nonempty source-backed label or null."
                              if invalid_price_basis else "The imported metric semantics require review." if kind == "unknown" or declared_review else None)
                    bindings[metric_id] = {
                        "metric_id": metric_id, "title": f"{contract['name']}: {column}",
                        "source_system": "SESSION_DATASET", "table": table,
                        "time_column": contract.get("date_column"), "value_column": column,
                        "filters": {}, "dimensions": {key: key for key in contract["grain"] if key != contract.get("date_column")},
                        "native_frequency": contract["frequency"], "kind": kind,
                        "unit": definition["unit"], "scale": definition.get("scale", 1),
                        "currency": definition.get("currency"), "aggregation": definition.get("aggregation", "none"),
                        "source_base": "", "provenance_columns": [], "status": "review_required" if review_required else "ready",
                        "blocked_reason": reason, "cumulative_evidence": cumulative,
                        "temporal_semantics": definition.get("temporal_semantics") or definition.get("temporal_basis") or definition.get("source_semantics"),
                        "price_basis": price_basis, "additive_over_time": False if review_required else definition.get("additive_over_time"),
                        "measurement_basis": definition.get("measurement_basis", "source_reported"),
                        "notes": ["User-supplied explicit data contract"], "contract_version": "1",
                        "dataset_id": dataset_id, "source_sha256": manifest["source_sha256"],
                        # A declared origin is provenance, not proof that two
                        # datasets cover the same population. Keep execution
                        # scope tied to the immutable ingested dataset.
                        "scope_namespace": dataset_id,
                        "source_namespace": contract.get("source_namespace"),
                        "document_provenance": copy.deepcopy(contract.get("document_provenance")),
                        "source_url": provenance.get("source_url") if isinstance(provenance, dict) else None,
                        "index_role": definition.get("index_role"),
                        "deflator_currency": definition.get("deflator_currency"),
                        "price_scope": definition.get("price_scope"),
                        "unit_normalization": unit_normalization,
                    }
            yield connection, bindings, workspace
        finally:
            connection.close()

    @staticmethod
    def _card(binding: dict) -> dict:
        from agentic_analytics.lakehouse.discovery import semantic_profile

        fields = ("metric_id", "dataset_id", "source_code", "title", "title_en", "group_name", "value_dimension", "is_archive", "temporal_semantics", "quality_status", "source_system", "source_organization", "competition_scope", "source_namespace", "native_frequency", "kind", "unit", "scale", "currency", "status", "dimensions", "institution_scope", "geography_scope", "notes", "index_role", "deflator_currency", "price_scope", "price_basis", "blocked_reason", "cumulative_evidence", "semantic_policy_version", "coverage_start", "coverage_end", "observation_count", "missing_observation_count", "vintage_policy", "release_date", "revision_status", "methodology_revision_date", "canonical_series_code", "historical_name", "current_name", "name_change_effective_date", "name_change_source_url", "scale_revision_effective_date", "scale_revision_factor", "scale_revision_source_url", "historical_archive_source_url", "historical_original_scale_included", "methodology_source_url", "official_series_url", "source_metadata_url", "source_url", "source_frequency_evidence")
        fields += ("population_scope", "measurement_basis", "scope_caveats", "source_table_category", "source_scope_evidence", "source_scope_policy_version")
        return {key: binding.get(key) for key in fields} | {"semantic_profile": semantic_profile(binding)}

    def discover(self, request: dict) -> dict:
        from agentic_analytics.lakehouse.discovery import compare_intent, initial_query, query_intent, semantic_profile, semantic_search_text

        _object(request, {"query", "limit", "status"}, {"query"}, "discover")
        if not isinstance(request["query"], str) or len(request["query"]) > 300:
            raise PlanError("query must be a string of at most 300 characters")
        limit = request.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise PlanError("limit must be between 1 and 25")
        if request.get("status") is not None and (not isinstance(request["status"], str) or request["status"] not in {"ready", "review_required", "metadata_only", "no_numeric"}):
            raise PlanError("Unknown readiness status")
        searched_query = initial_query(request["query"])
        terms, frequencies = _query_terms(searched_query)
        requested_sources = _requested_sources(searched_query)
        terms = [term for term in terms if term not in set(requested_sources)]
        intent = query_intent(searched_query)
        with self._context() as (connection, bindings, workspace):
            matches, near, dimension_cache = [], [], {}
            for binding in bindings.values():
                if request.get("status") and binding["status"] != request["status"]:
                    continue
                source_match = _source_match(binding, requested_sources)
                if requested_sources and source_match is None:
                    continue
                searchable = " ".join(_search_terms(" ".join(str(binding.get(key) or "") for key in ("metric_id", "title", "title_en", "searchable_text", "group_name", "source_system", "source_organization"))))
                title = " ".join(_search_terms(str(binding.get("title") or "") + " " + str(binding.get("title_en") or "")))
                profile = semantic_profile(binding)
                searchable += " " + semantic_search_text(profile)
                semantic_match = compare_intent(intent, profile)
                if str(binding.get("source_system", "")).startswith("BDDK_"):
                    searchable += " banka sektor bddk"
                if binding.get("source_system") in {"BDDK_MONTHLY", "BDDK_WEEKLY"}:
                    searchable += " bulten"
                if binding.get("kind") == "stock" and binding.get("currency"):
                    searchable += " bakiye"
                if binding.get("index_role") == "price_deflator":
                    searchable += " tufe enflasyon inflation cpi"
                missing = [term for term in terms if not _term_matches(term, searchable)]
                matched_dimensions = {}
                # Examine dimensions only for plausible metric candidates; a
                # city-only query should use dimension_values after discovery.
                if missing and len(missing) < len(terms) and binding.get("table"):
                    for dimension, physical in binding.get("dimensions", {}).items():
                        cache_key = (binding["table"], physical)
                        if cache_key not in dimension_cache:
                            values = connection.execute(f"SELECT DISTINCT {_identifier(physical)} FROM {_identifier(binding['table'])} WHERE {_identifier(physical)} IS NOT NULL LIMIT 1001").fetchall()
                            dimension_cache[cache_key] = [row[0] for row in values] if len(values) <= 1000 else []
                        found = [value for value in dimension_cache[cache_key] if any(_term_matches(term, _fold(str(value)), whole_word=True) for term in missing)]
                        if found:
                            matched_dimensions[dimension] = found[:10]
                            missing = [term for term in missing if not any(_term_matches(term, _fold(str(value)), whole_word=True) for value in found)]
                if not missing:
                    card = self._card(binding)
                    # A repeated Total label is a slice marker, not stronger
                    # evidence of the requested metric's population scope.
                    title_matches = sum(_term_matches(term, title) for term in terms if term != "toplam")
                    concept_terms = [term for term in terms if term not in {"banka", "sektor", "bddk", "toplam"}]
                    title_words = title.split()
                    adjacent_matches = sum(any(_term_matches(left, first) and _term_matches(right, second)
                                               for first, second in zip(title_words, title_words[1:]))
                                           for left, right in zip(concept_terms, concept_terms[1:]))
                    reversed_matches = sum(any(_term_matches(right, first) and _term_matches(left, second)
                                               for first, second in zip(title_words, title_words[1:]))
                                           for left, right in zip(concept_terms, concept_terms[1:]))
                    native = binding.get("native_frequency", "")
                    frequency_match = any(native == hint or native.startswith(hint + "_") for hint in frequencies)
                    compatible = all(_FREQUENCY_RANK.get(native, 999) <= _FREQUENCY_RANK[hint] for hint in frequencies)
                    # Exact title meaning outranks incidental terms in a long
                    # source/group description. No metric ID gets a special score.
                    score = title_matches * 10 + (adjacent_matches + reversed_matches) * 30 + int(frequency_match) * 3 + len(matched_dimensions) * 2
                    if frequencies:
                        card["frequency_hint_requires_upsampling"] = not compatible
                    if intent.get("reference_years"):
                        card["requested_period_covered"] = _coverage_includes_years(
                            card, intent["reference_years"]
                        )
                    if matched_dimensions:
                        card["matched_dimensions"] = _json(matched_dimensions)
                    if source_match:
                        card["source_match"] = source_match
                    card["semantic_match"] = semantic_match
                    source_strength = source_match["strength"] if source_match else 0
                    source_coverage = len(source_match["matched_sources"]) if source_match else 0
                    directness = 0 if str(binding.get("competition_scope") or "").startswith(("explicit_", "supporting_official")) else 1
                    matches.append((compatible, source_strength, source_coverage, directness, score, card))
                elif terms and len(missing) < len(terms):
                    # Partial match: store only cheap sort scalars plus references; the
                    # card is materialized later, and only if no full match is found.
                    near.append((binding["status"] != "ready", len(missing), title, matched_dimensions, missing, binding, semantic_match, source_match))
            # Structural canonical ordering: after semantics and frequency, use
            # the explicit vintage request. A closed historical query ranks its
            # first official publication ahead of a later methodology revision,
            # while the result keeps both candidates and an ambiguity receipt.
            matches.sort(key=lambda item: (
                not item[0], len(item[5]["semantic_match"]["conflicts"]),
                not item[5].get("requested_period_covered", True),
                _vintage_rank(intent, item[5]),
                len(item[5]["semantic_match"]["unverified_facets"]),
                item[5]["semantic_match"]["penalty"], item[5]["status"] != "ready",
                -item[1], item[3], -item[2], -item[4],
                1 if item[5].get("is_archive") else 0,
                0 if _is_total_slice(item[5]) else 1,
                0 if str(item[5].get("quality_status") or "").startswith("passed") else 1,
                item[5]["metric_id"]))
            all_cards = [card for _, _, _, _, _, card in matches]
            result = {"status": "ok", "snapshot_id": workspace["snapshot_id"], "total": len(matches), "metrics": all_cards[:limit], "query_intent": intent}
            if requested_sources:
                represented = list(dict.fromkeys(
                    source for card in all_cards for source in (card.get("source_match") or {}).get("matched_sources", [])
                ))
                result["source_selection"] = {
                    "requested": requested_sources,
                    "represented": represented,
                    "mode": "compare_sources" if len(requested_sources) > 1 else "single_source",
                    "message": (
                        "Source names are routing constraints. For a comparison, inspect and select a source-native metric "
                        "for each requested source; a derived multi-source panel must not silently replace them."
                    ),
                }
            vintage_selection = _vintage_selection(intent, all_cards)
            if vintage_selection:
                result["vintage_selection"] = vintage_selection
            if searched_query != request["query"]:
                result["searched_query"] = searched_query
                result["query_projection_note"] = "A metric-bearing clause was used for candidate discovery. This does not resolve the rest of the task or certify source equivalence."
            # When nothing fully matches, do not return a bare empty result: name
            # the unresolved terms and surface the nearest real series as hints.
            if terms and not matches:
                near.sort(key=lambda item: (not all(_FREQUENCY_RANK.get(item[5].get("native_frequency"), 999) <= _FREQUENCY_RANK[hint] for hint in frequencies),
                                            len(item[6]["conflicts"]), len(item[6]["unverified_facets"]),
                                            item[6]["penalty"], item[1], item[0],
                                            -(item[7] or {}).get("strength", 0),
                                            -sum(_term_matches(term, item[2]) for term in terms if term != "toplam"),
                                            not _is_total_slice(item[5]), item[5]["metric_id"]))
                near_cards = []
                for _, _, _, matched_dims, missing_terms, binding, semantic_match, source_match in near[:min(limit, 6)]:
                    card = self._card(binding)
                    card["semantic_match"] = semantic_match
                    if source_match:
                        card["source_match"] = source_match
                    if frequencies:
                        card["frequency_hint_requires_upsampling"] = not all(_FREQUENCY_RANK.get(binding.get("native_frequency"), 999) <= _FREQUENCY_RANK[hint] for hint in frequencies)
                    if intent.get("reference_years"):
                        card["requested_period_covered"] = _coverage_includes_years(
                            card, intent["reference_years"]
                        )
                    if matched_dims:
                        card["matched_dimensions"] = _json(matched_dims)
                    card["missing_terms"] = missing_terms
                    near_cards.append(card)
                result["no_confident_match"] = True
                result["near_matches"] = near_cards
                if near_cards:
                    common_missing = set(terms)
                    for card in near_cards:
                        common_missing &= set(card.get("missing_terms", []))
                    result["uncovered_terms"] = [term for term in terms if term in common_missing]
                else:
                    result["uncovered_terms"] = terms
            return result

    def _dimension_rows(self, connection: Any, binding: dict, dimension: str, dimensions: dict | None = None) -> list[dict]:
        if not isinstance(dimension, str) or dimension not in binding.get("dimensions", {}):
            raise PlanError("Unknown dimension", code="DIMENSION_NOT_FOUND")
        if not binding.get("table"):
            hint = (f" For a TCMB_EVDS series, call acquire_evds_series with series_codes=['{binding['source_code']}']"
                    " to fetch it, then retry." if binding.get("source_system") == "TCMB_EVDS" else "")
            raise PlanError("Metric has no observation data." + hint, code="METADATA_ONLY")
        column = binding["dimensions"][dimension]
        label_column = binding.get("dimension_label_columns", {}).get(dimension)
        selected = _identifier(column) + " AS value"
        if label_column:
            selected += f", list(DISTINCT {_identifier(label_column)} ORDER BY {_identifier(label_column)}) AS labels"
        predicates, params = [f"{_identifier(column)} IS NOT NULL"], []
        filters = {**binding.get("filters", {}), **{binding["dimensions"][key]: value for key, value in (dimensions or {}).items()}}
        for key, value in filters.items():
            values = value if isinstance(value, list) else [value]
            if not values:
                raise PlanError("Empty registry filter")
            predicates.append(f"{_identifier(key)} IN ({','.join('?' for _ in values)})")
            params.extend(values)
        try:
            cursor = connection.execute(f"SELECT {selected}, count(*) AS observation_count FROM {_identifier(binding['table'])} WHERE {' AND '.join(predicates)} GROUP BY {_identifier(column)} ORDER BY {_identifier(column)} LIMIT 1001", params)
        except duckdb.Error as error:
            raise PlanError("Dimension values or labels do not match the source contract", code="INVALID_DIMENSION_VALUE") from error
        rows = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        if len(rows) > 1000:
            raise PlanError("Dimension has more than 1000 values; a narrower dimension contract is required", code="DIMENSION_CARDINALITY_LIMIT")
        declared_labels = binding.get("dimension_labels", {}).get(dimension, {})
        for row in rows:
            if label_column:
                row["labels"] = [label for label in row["labels"] if label is not None]
                row["label_evidence"] = {"source_column": label_column, "source_system": binding.get("source_system")}
            elif declared_labels:
                label = declared_labels.get(str(row["value"]))
                row["labels"] = [] if label is None else [label]
                row["label_evidence"] = copy.deepcopy(binding.get("dimension_labels_evidence", {}).get(dimension))
            if "labels" in row:
                row["label"] = row["labels"][0] if len(row["labels"]) == 1 else None
                if binding.get("source_system", "").startswith("BDDK_") and any(_fold(label) == "sektor" for label in row["labels"]):
                    row["aliases"] = ["tüm bankalar", "bankacılık sektörü", "all banks"]
            row["scope"] = binding.get("institution_scope") if dimension == "group_code" else binding.get("geography_scope")
        return rows

    def dimension_values(self, request: dict) -> dict:
        _object(request, {"metric_id", "dimension", "query", "limit"}, {"metric_id", "dimension"}, "dimension_values")
        query, limit = request.get("query", ""), request.get("limit", 100)
        if not isinstance(query, str) or len(query) > 300:
            raise PlanError("query must be a string of at most 300 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 250:
            raise PlanError("limit must be between 1 and 250")
        with self._context() as (connection, bindings, workspace):
            if not isinstance(request["metric_id"], str) or request["metric_id"] not in bindings:
                raise PlanError("Unknown metric_id", code="METRIC_NOT_FOUND")
            rows = self._dimension_rows(connection, bindings[request["metric_id"]], request["dimension"])
            terms = _search_terms(query)
            rows = [row for row in rows if all(_term_matches(term, _fold(str(row["value"]) + " " + " ".join(row.get("labels", []) + row.get("aliases", [])))) for term in terms)]
            return _json({"status": "ok", "snapshot_id": workspace["snapshot_id"], "metric_id": request["metric_id"], "dimension": request["dimension"], "total": len(rows), "values": rows[:limit], "truncated": len(rows) > limit})

    def describe(self, request: dict) -> dict:
        _object(request, {"metric_id"}, {"metric_id"}, "describe")
        if not isinstance(request["metric_id"], str):
            raise PlanError("metric_id must be a string")
        with self._context() as (_, bindings, _):
            if request["metric_id"] not in bindings:
                raise PlanError("Unknown metric_id", code="METRIC_NOT_FOUND")
            return {"status": "ok", "metric": copy.deepcopy(bindings[request["metric_id"]])}

    def _validate(self, plan: dict, bindings: dict) -> dict:
        _object(plan, {"start", "end", "frequency", "columns", "operations"}, {"start", "end", "frequency", "columns"}, "plan")
        frequency = plan["frequency"]
        if not isinstance(frequency, str) or frequency not in _FREQUENCIES:
            raise PlanError("Unsupported output frequency")
        start, end = _period(plan["start"], frequency), _period(plan["end"], frequency)
        if start > end or end.ordinal - start.ordinal > 5000:
            raise PlanError("Invalid or excessive period range")
        if not isinstance(plan["columns"], list) or not 1 <= len(plan["columns"]) <= 25:
            raise PlanError("Select between 1 and 25 metric columns")
        if not isinstance(plan.get("operations", []), list) or len(plan.get("operations", [])) > 50:
            raise PlanError("At most 50 operations are supported")
        schemas: dict[str, dict] = {}
        for selection in plan["columns"]:
            _object(selection, {"name", "metric_id", "dimensions", "alignment"}, {"name", "metric_id"}, "column")
            name = _name(selection["name"])
            if name in schemas:
                raise PlanError(f"Duplicate column {name}")
            metric_id = selection["metric_id"]
            if not isinstance(metric_id, str) or metric_id not in bindings:
                raise PlanError(f"Unknown metric_id {metric_id!r}", code="METRIC_NOT_FOUND")
            binding = bindings[metric_id]
            if binding["status"] in {"metadata_only", "no_numeric"}:
                hint = (f" For a TCMB_EVDS series, call acquire_evds_series with series_codes=['{binding['source_code']}'] "
                        "and a suitable start_date/end_date, then retry this exact plan." if binding.get("source_system") == "TCMB_EVDS" else "")
                raise PlanError(f"{metric_id}: {binding['status']}; observations must be acquired first." + hint,
                                 code="METADATA_ONLY" if binding["status"] == "metadata_only" else "NO_NUMERIC_VALUES")
            if binding.get("binding_available") is False or not binding.get("table") or not binding.get("time_column") or not binding.get("value_column"):
                raise PlanError(f"{metric_id}: no executable physical binding", code="NO_PHYSICAL_BINDING")
            dimensions = selection.get("dimensions", {})
            if not isinstance(dimensions, dict) or set(dimensions) != set(binding.get("dimensions", {})):
                raise PlanError(f"{metric_id}: specify exactly these dimensions: {sorted(binding.get('dimensions', {}))}")
            if any(value is None or isinstance(value, (dict, list, bool)) or not isinstance(value, (str, int, float)) or (isinstance(value, float) and not math.isfinite(value)) for value in dimensions.values()):
                raise PlanError("Dimension values must be finite scalar strings or numbers")
            alignment = selection.get("alignment", "native")
            native = binding["native_frequency"]
            if alignment == "period_end":
                if native != "event" or binding["kind"] not in {"stock", "count_stock"}:
                    raise PlanError("period_end requires dated event stock or count_stock observations; it cannot reinterpret a flow or a calendar series", code="INVALID_TEMPORAL_AGGREGATION")
                if binding["status"] != "ready":
                    raise PlanError("Review the event stock's financial semantics before period-end comparison", code="SEMANTICS_REVIEW_REQUIRED")
                if frequency not in {"daily", "monthly", "quarterly", "annual", "yearly"}:
                    raise PlanError("Event period_end supports daily, monthly, quarterly and calendar-year endpoints only", code="INVALID_TEMPORAL_AGGREGATION")
            elif native == "event":
                raise PlanError("Dated event observations have no inferred publication frequency. For ready stocks use alignment='period_end' at the exact source date's calendar endpoint; for raw document display use aggregate_dataset with source_value.", code="INVALID_TEMPORAL_AGGREGATION")
            if (native in _NATIVE_ONLY_FREQUENCIES or frequency in _NATIVE_ONLY_FREQUENCIES) and native != frequency:
                raise PlanError("Half-year and twice-monthly sources currently support native selection only; frequency conversion is unavailable", code="NATIVE_FREQUENCY_CONVERSION_UNSUPPORTED")
            rank = _FREQUENCY_RANK
            if native not in rank and alignment != "period_end":
                raise PlanError(f"Unsupported native frequency {native}")
            if alignment != "period_end" and rank[native] > rank[frequency]:
                raise PlanError("Upsampling is forbidden; choose the source's native or a coarser frequency", code="INVALID_TEMPORAL_AGGREGATION")
            if not isinstance(alignment, str) or alignment not in {"native", "last", "mean", "sum", "period_end"}:
                raise PlanError("Unknown alignment operation")
            if native != frequency and alignment == "native":
                raise PlanError("Frequency conversion requires an explicit alignment", code="INVALID_TEMPORAL_AGGREGATION")
            if native == frequency and alignment != "native":
                raise PlanError(f"Column {name!r}: source frequency={native}, output frequency={frequency}. Set THIS column's alignment to 'native'. Aggregation is only for changing frequency; keep other columns' explicit conversions.", code="REDUNDANT_ALIGNMENT")
            if alignment == "last" and binding["kind"] in {"flow", "count_flow"}:
                raise PlanError("The last subperiod flow is not the whole-period flow; use a complete sum", code="INVALID_TEMPORAL_AGGREGATION")
            if alignment == "sum" and (binding["kind"] not in {"flow", "count_flow"} or binding.get("additive_over_time") is False and binding["status"] == "ready" or _flow_period_count(native, frequency) is None):
                raise PlanError("Only complete monthly flows to quarters/years or quarterly flows to years may be summed", code="INVALID_TEMPORAL_AGGREGATION")
            if alignment == "mean" and binding["kind"] not in {"rate", "ratio", "index", "price"}:
                raise PlanError("Mean alignment requires a rate, ratio, index or price", code="INVALID_TEMPORAL_AGGREGATION")
            if native != frequency and binding["status"] != "ready":
                raise PlanError("Unreviewed semantics permit native raw selection only", code="SEMANTICS_REVIEW_REQUIRED")
            schemas[name] = {
                key: binding.get(key)
                for key in (
                    "kind",
                    "unit",
                    "scale",
                    "currency",
                    "status",
                    "index_role",
                    "deflator_currency",
                    "price_scope",
                    "price_basis",
                    "cumulative_evidence",
                    "additive_over_time",
                    "semantic_policy_version",
                    "vintage_policy",
                    "release_date",
                    "revision_status",
                )
            }
            # Index identity evidence is exceptional metadata, not part of the
            # fixed schema footprint for every metric. Keeping absent values out
            # prevents large multi-metric analyses from spending context on
            # repeated null keys while preserving the evidence for XU100 and
            # any other series that actually carries it.
            for key in (
                "canonical_series_code",
                "historical_name",
                "current_name",
                "name_change_effective_date",
                "name_change_source_url",
                "scale_revision_effective_date",
                "scale_revision_factor",
                "scale_revision_source_url",
                "historical_archive_source_url",
                "historical_original_scale_included",
                "methodology_source_url",
                "official_series_url",
            ):
                value = binding.get(key)
                if value not in (None, ""):
                    schemas[name][key] = value
            schemas[name]["measurement_basis"] = binding.get("measurement_basis", "source_reported")
            schemas[name]["metric_id"] = metric_id
            schemas[name]["scope"] = {
                "namespace": binding.get("scope_namespace") or binding.get("dataset_id") or binding.get("source_system"),
                "institution_scope": binding.get("institution_scope") or "unspecified",
                "geography_scope": binding.get("geography_scope") or "unspecified",
                "dimensions": copy.deepcopy(dimensions),
            }
            if binding.get("population_scope"):
                schemas[name]["scope"]["population"] = copy.deepcopy(binding["population_scope"])
        warmup = 0
        operation_schemas = []
        for operation in plan.get("operations", []):
            if not isinstance(operation, dict):
                raise PlanError("operation must be an object")
            op = operation.get("op")
            fields = {
                "growth": {"op", "column", "output", "periods", "prior_scope"},
                "difference": {"op", "column", "output", "periods", "prior_scope"},
                "deflate": {"op", "column", "index", "base_period", "output"},
                "scale": {"op", "column", "output", "target_scale"},
                "ratio": {"op", "column", "denominator", "output", "multiplier", "scope_policy", "scope_reason"},
            }
            if not isinstance(op, str) or op not in fields:
                raise PlanError(f"Unsupported operation {op!r}")
            required = fields[op] - ({"periods", "prior_scope"} if op in {"growth", "difference"} else {"multiplier", "scope_policy", "scope_reason"} if op == "ratio" else set())
            _object(operation, fields[op], required, "operation")
            output = _name(operation["output"])
            column = operation["column"]
            if not isinstance(column, str) or column not in schemas:
                raise PlanError("Operation references an unknown column")
            source = schemas[column]
            if source["status"] != "ready" or source["kind"] == "unknown":
                raise PlanError("Unreviewed semantics permit raw selection only", code="SEMANTICS_REVIEW_REQUIRED")
            if frequency in _NATIVE_ONLY_FREQUENCIES:
                raise PlanError("Transformations for half-year and twice-monthly selections are not implemented; select native source values", code="NATIVE_PERIOD_OPERATIONS_UNSUPPORTED")
            schema = copy.deepcopy(source)
            schema.pop("index_role", None)
            schema.pop("deflator_currency", None)
            if op in {"growth", "difference"}:
                periods = operation.get("periods", 1)
                if isinstance(periods, bool) or not isinstance(periods, int) or not 1 <= periods <= 120:
                    raise PlanError("periods must be an integer between 1 and 120")
                prior_scope = operation.get("prior_scope", "available_history")
                if prior_scope not in {"available_history", "selected_window"}:
                    raise PlanError("prior_scope must be available_history or selected_window")
                # Sum is conservative for chained operations and preserves
                # warmup unless the user explicitly wants the first visible
                # row to remain empty.
                if prior_scope == "available_history":
                    warmup += periods
                if op == "growth":
                    if source["kind"] in {"rate", "ratio"} and source["unit"] in {"percent", "%", "ratio"}:
                        raise PlanError("Use difference for rates and ratios to obtain percentage points", code="UNIT_MISMATCH")
                    schema.update(kind="ratio", unit="percent", scale=1, currency=None)
                elif source["kind"] in {"rate", "ratio"} and source["unit"] in {"percent", "%"}:
                    schema.update(kind="difference", unit="percentage_points", scale=1, currency=None)
                elif source["kind"] in {"stock", "count_stock"} and periods == 1:
                    # S(t) - S(t-1) is the net change during period t; overlapping
                    # multi-period changes are not additive and stay as they were.
                    schema.update(kind="flow" if source["kind"] == "stock" else "count_flow", additive_over_time=True)
            elif op == "deflate":
                index = operation["index"]
                if not isinstance(index, str) or index not in schemas or schemas[index]["kind"] != "index" or schemas[index]["status"] != "ready":
                    raise PlanError("Deflation requires a reviewed index column", code="UNIT_MISMATCH")
                if source["kind"] not in {"stock", "flow", "price", "ratio"} or not source.get("currency"):
                    raise PlanError("Only monetary stocks, flows and prices may be deflated", code="UNIT_MISMATCH")
                if source.get("price_basis") is not None:
                    raise PlanError("This monetary input already has a constant-price basis. Deflating it again would double-apply the price adjustment; select the nominal source or use an explicit verified rebasing method.", code="UNIT_MISMATCH")
                if schemas[index].get("index_role") != "price_deflator" or schemas[index].get("deflator_currency") != source.get("currency"):
                    raise PlanError("Deflation requires an explicitly reviewed price deflator for the monetary input's currency", code="INVALID_DEFLATOR_ROLE")
                base = _period(operation["base_period"], frequency)
                if base > end:
                    raise PlanError("Base period must not be after the selected end")
                warmup = max(warmup, start.ordinal - base.ordinal)
                schema["price_basis"] = operation["base_period"]
                schema["price_scope"] = schemas[index].get("price_scope")
            elif op == "scale":
                scale = operation["target_scale"]
                if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                    raise PlanError("target_scale must be finite and positive")
                if source["kind"] not in {"stock", "flow", "price", "count", "count_stock", "count_flow"}:
                    raise PlanError("Scale conversion is unavailable for this metric kind")
                schema["scale"] = scale
            elif op == "ratio":
                other = operation["denominator"]
                if not isinstance(other, str) or other not in schemas:
                    raise PlanError("Unknown denominator")
                denominator = schemas[other]
                if denominator["status"] != "ready" or source["kind"] != denominator["kind"] or source["unit"] != denominator["unit"] or source.get("currency") != denominator.get("currency") or source.get("price_basis") != denominator.get("price_basis") or source.get("measurement_basis") != denominator.get("measurement_basis") or (source.get("price_basis") is not None and source.get("price_scope") != denominator.get("price_scope")):
                    details = {label: {key: item.get(key) for key in ("kind", "unit", "scale", "currency", "status", "price_basis", "price_scope", "measurement_basis")}
                               for label, item in ((operation["column"], source), (other, denominator))}
                    raise PlanError("Ratios require compatible reviewed metric kinds, units, currencies, price bases and measurement bases. "
                                    "Inspect and correct source contracts or select the intended measure: " + json.dumps(details, ensure_ascii=False), code="UNIT_MISMATCH")
                policy = operation.get("scope_policy", "same_scope")
                reason = operation.get("scope_reason")
                if not isinstance(policy, str) or policy not in {"same_scope", "explicit_comparison"}:
                    raise PlanError("scope_policy must be same_scope or explicit_comparison")
                if policy == "explicit_comparison" and (not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500):
                    raise PlanError("Explicit scope comparison requires a scope_reason of 10 to 500 characters")
                if policy == "same_scope" and reason is not None:
                    raise PlanError("scope_reason requires scope_policy=explicit_comparison")
                if source["scope"] != denominator["scope"]:
                    if policy != "explicit_comparison":
                        raise PlanError("Ratio inputs have different source populations, geography or selected dimensions; an intentional comparison must name its scope_policy and scope_reason", code="SCOPE_MISMATCH")
                    schema["scope"] = {"comparison": {"numerator": copy.deepcopy(source["scope"]), "denominator": copy.deepcopy(denominator["scope"])}, "scope_reason": reason}
                multiplier = operation.get("multiplier", 100)
                if isinstance(multiplier, bool) or multiplier not in {1, 100}:
                    raise PlanError("Ratio multiplier must be 1 or 100")
                schema.update(kind="ratio", unit="percent" if multiplier == 100 else "ratio", scale=1, currency=None)
            schemas[output] = schema
            operation_schemas.append(copy.deepcopy(schema))
        if warmup > 1200:
            raise PlanError("Excessive calculation warmup")
        return {"start": start, "end": end, "warmup": warmup, "schema": schemas, "operation_schemas": operation_schemas}

    def validate_plan(self, plan: dict) -> dict:
        try:
            with self._context() as (connection, bindings, _):
                validated = self._validate(plan, bindings)
                _, _, warnings = self._evaluate(connection, bindings, plan, validated)
                return {"status": "valid", "schema": validated["schema"], "warnings": warnings}
        except PlanError as exc:
            return error_envelope(exc)

    def _read_column(self, connection: Any, binding: dict, selection: dict, calendar: pd.PeriodIndex) -> tuple[pd.Series, dict, list[dict]]:
        time, value = binding["time_column"], binding["value_column"]
        provenance = list(dict.fromkeys(binding.get("provenance_columns", [])))
        selected = list(dict.fromkeys([time, value, *provenance]))
        predicates, params = [], []
        for key, val in {**binding.get("filters", {}), **{binding["dimensions"][key]: val for key, val in selection.get("dimensions", {}).items()}}.items():
            if isinstance(val, list):
                if not val:
                    raise PlanError("Empty registry filter")
                predicates.append(f"{_identifier(key)} IN ({','.join('?' for _ in val)})")
                params.extend(val)
            else:
                predicates.append(f"{_identifier(key)} = ?")
                params.append(val)
        sql = f"SELECT {', '.join(_identifier(x) for x in selected)} FROM {_identifier(binding['table'])}"
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        sql += " LIMIT 100001"
        try:
            frame = connection.execute(sql, params).fetchdf()
        except duckdb.Error as exc:
            raise PlanError("Metric binding or dimension types do not match the selected data") from exc
        if len(frame) > 100000:
            raise PlanError("Metric selection exceeds 100000 source rows; narrow the dimensions")
        if frame.empty:
            raise PlanError(f"No observations for {selection['name']} and the selected dimensions", code="MISSING_OBSERVATIONS")
        raw_dates = frame[time].astype(str)
        try:
            if binding["native_frequency"] in _NATIVE_ONLY_FREQUENCIES:
                native_periods = pd.PeriodIndex([_period(item, binding["native_frequency"]) for item in raw_dates],
                                               freq=_FREQUENCIES[binding["native_frequency"]])
                frame["_period"] = native_periods
                frame["_native_period"] = native_periods
            elif binding["native_frequency"] == "quarterly":
                dates = pd.PeriodIndex(raw_dates.str.replace("-Q", "Q", regex=False), freq="Q").to_timestamp(how="end")
            else:
                dates = pd.to_datetime(raw_dates, format="mixed", errors="raise")
            if binding["native_frequency"] not in _NATIVE_ONLY_FREQUENCIES:
                frame["_period"] = pd.PeriodIndex(dates, freq=calendar.freqstr)
                native_calendar = _FREQUENCIES.get(binding["native_frequency"])
                frame["_native_period"] = pd.PeriodIndex(dates, freq=native_calendar or "D")
        except (ValueError, TypeError) as exc:
            raise PlanError(f"Invalid source period for {selection['name']}") from exc
        frame["_date"] = raw_dates
        source_rows = {str(when): group for when, group in frame.groupby("_date")}
        frame = frame[frame["_period"].between(calendar[0], calendar[-1])].sort_values("_date")
        if frame.empty:
            raise PlanError(f"No observations in the requested window for {selection['name']}", code="MISSING_OBSERVATIONS")
        alignment = selection.get("alignment", "native")
        if alignment == "period_end":
            # Event dates describe actual observations, not a publication cadence.
            # Require exact calendar endpoints without selecting a nearby value,
            # dropping a conflicting date, carrying values or reducing records.
            expected_dates = frame["_period"].dt.end_time.dt.normalize()
            actual_dates = pd.to_datetime(frame["_date"], format="mixed", errors="raise")
            mismatched = actual_dates != expected_dates
            if mismatched.any():
                offending = frame.loc[mismatched, "_date"].tolist()[:12]
                raise PlanError(f"{selection['name']}: event dates {offending} are not exact output calendar endpoints. No observations were moved or discarded; select their actual dates for raw display.", code="PERIOD_END_MISMATCH")
        if frame["_native_period"].duplicated().any():
            raise PlanError(f"Ambiguous grain for {selection['name']}: more than one row per native period", code="AMBIGUOUS_GRAIN")
        numeric = pd.to_numeric(frame[value], errors="coerce")
        if (frame[value].notna() & numeric.isna()).any() or np.isinf(numeric).any():
            raise PlanError("Source contains invalid numeric observations")
        frame[value] = numeric
        integer_source = pd.api.types.is_integer_dtype(numeric.dtype)
        if integer_source:
            frame[value] = frame[value].astype("Int64")
            if alignment not in {"native", "last", "period_end"} and ((frame[value] > 2**53) | (frame[value] < -(2**53))).any():
                raise PlanError("Integer aggregation exceeds the exact floating-point range; select native values", code="NUMERIC_PRECISION_UNSUPPORTED")
        grouped = frame.groupby("_period", sort=True)
        warnings = []
        if alignment == "period_end":
            warnings.append({"code": "exact_event_period_end", "column": selection["name"],
                             "detail": "Actual event dates match calendar endpoints exactly. No publication frequency, carry-forward or temporal aggregation is inferred."})
        if alignment in {"native", "period_end"}:
            if frame["_period"].duplicated().any():
                raise PlanError("Native selection would fan out the requested period", code="AMBIGUOUS_GRAIN")
            series = frame.set_index("_period")[value]
        elif alignment == "last":
            series = grouped.tail(1).set_index("_period")[value]
        elif alignment == "mean":
            series = grouped[value].mean()
            warnings.append({"code": "observed_sample_mean", "column": selection["name"], "detail": "Arithmetic mean of observed native values; a complete publication calendar is not asserted"})
        else:
            target = "quarterly" if calendar.freqstr.startswith("Q") else "annual"
            expected_count = _flow_period_count(binding["native_frequency"], target)
            if expected_count is None:
                raise PlanError("Unsupported flow aggregation", code="INVALID_TEMPORAL_AGGREGATION")
            sizes, numeric_sizes = grouped.size(), grouped[value].count()
            complete = (sizes == expected_count) & (numeric_sizes == expected_count)
            # Counts alone cannot certify the calendar when native periods are
            # mislabeled, duplicated, or missing. Compare the actual period set.
            for period, group in grouped:
                expected = set(pd.period_range(period.start_time, period.end_time, freq=_FREQUENCIES[binding["native_frequency"]]))
                complete.loc[period] = bool(complete.loc[period] and set(group["_native_period"]) == expected)
            series = grouped[value].sum(min_count=expected_count).where(complete)
            if (~complete).any():
                warnings.append({"code": "partial_period_blocked", "column": selection["name"], "periods": [_label(p) for p in complete.index[~complete]]})
        cells = {}
        def source_cell(row: pd.Series) -> dict:
            return _json({"native_period": row[time], "computed_value": row[value], "source_value": row[value], **{key: row[key] for key in provenance}})

        for period, group in grouped:
            period_cells = []
            for _, row in group.iterrows():
                cell = source_cell(row)
                if row.get("transformation") == "difference_within_calendar_year" and str(row[time])[5:7] != "01":
                    prior = row.get("prior_source_month")
                    predecessors = source_rows.get(str(prior))
                    cell["previous_cumulative_source_cells"] = [] if predecessors is None else [source_cell(previous) for _, previous in predecessors.iterrows()]
                period_cells.append(cell)
            cells[_label(period)] = period_cells
        if integer_source and alignment != "mean":
            series = series.astype("Int64")
        return series.reindex(calendar), {"binding": copy.deepcopy(binding), "dimensions": selection.get("dimensions", {}), "alignment": alignment, "cells": cells}, warnings

    def _evaluate(self, connection: Any, bindings: dict, plan: dict, validated: dict) -> tuple[pd.DataFrame, dict, list[dict]]:
        warmup = validated["warmup"] * 7 if plan["frequency"] == "weekly_observed" else validated["warmup"]
        calendar = pd.period_range(validated["start"] - warmup, validated["end"], freq=validated["start"].freq)
        frame = pd.DataFrame(index=calendar)
        lineage: dict[str, Any] = {"sources": {}, "operations": [], "frequency": plan["frequency"]}
        warnings = []
        # These are independent scalar series aligned on period, not a claim of
        # identical covered populations or a row-level entity join.
        raw_scopes = {selection["name"]: {
            "namespace": bindings[selection["metric_id"]].get("scope_namespace") or bindings[selection["metric_id"]].get("dataset_id") or bindings[selection["metric_id"]].get("source_system"),
            "institution_scope": bindings[selection["metric_id"]].get("institution_scope") or "unspecified",
            "geography_scope": bindings[selection["metric_id"]].get("geography_scope") or "unspecified",
            "dimensions": copy.deepcopy(selection.get("dimensions", {})),
        } for selection in plan["columns"]}
        for selection in plan["columns"]:
            population = bindings[selection["metric_id"]].get("population_scope")
            if population:
                raw_scopes[selection["name"]]["population"] = copy.deepcopy(population)
        if len({json.dumps(scope, sort_keys=True) for scope in raw_scopes.values()}) > 1:
            warnings.append({"code": "heterogeneous_scopes_aligned", "detail": "Columns align by period only; identical source populations are not asserted", "column_scopes": raw_scopes})
        lineage["join_contract"] = {"key": "period", "cardinality": "one_value_per_column_per_period", "population_equivalence_asserted": False}
        for selection in plan["columns"]:
            binding = bindings[selection["metric_id"]]
            series, proof, notes = self._read_column(connection, binding, selection, calendar)
            frame[selection["name"]] = series
            lineage["sources"][selection["name"]] = proof
            warnings.extend(notes)
            warnings.extend({**copy.deepcopy(note), "column": selection["name"]} for note in binding.get("scope_caveats", []))
            if binding["status"] != "ready":
                warnings.append({"code": "semantics_unreviewed", "column": selection["name"], "detail": binding.get("blocked_reason", "Raw values only; calculations are blocked")})
            definitions = binding.get("definition_periods", [])
            overlapping = [item for item in definitions if str(item.get("start", "0000")) <= str(calendar[-1].end_time.date()) and str(item.get("end") or "9999") >= str(calendar[0].start_time.date())]
            if len({item.get("definition_id", item.get("label")) for item in overlapping}) > 1 and any(selection["name"] in (op.get("column"), op.get("index"), op.get("denominator")) for op in plan.get("operations", [])):
                raise PlanError(f"{selection['name']}: calculation crosses a source definition change", code="DEFINITION_BREAK")
        if plan["frequency"] in {"twice_monthly", "weekly_observed"}:
            # The metadata does not certify 15th/month-end publication dates.
            # Keep exact observed date keys, including source-null rows, across
            # the selected series. Unobserved dates never become synthetic rows.
            observed = sorted({_period(label, "twice_monthly")
                               for proof in lineage["sources"].values() for label in proof["cells"]})
            frame = frame.loc[pd.PeriodIndex(observed, freq="D")]
            lineage["calendar_policy"] = "union_of_observed_native_dates"
            if plan["frequency"] == "weekly_observed":
                warnings.append({"code": "actual_weekly_source_dates", "detail": "Weekly output retains actual source observation dates, including holiday-shifted issues; no synthetic weekday labels are created"})
            else:
                warnings.append({"code": "native_calendar_unverified", "detail": "Twice-monthly output retains observed source dates only; no fixed publication days or complete calendar are assumed"})
        elif plan["frequency"] == "half_yearly":
            lineage["calendar_policy"] = "calendar_half_years_january_june_and_july_december"
        schemas = {s["name"]: {key: bindings[s["metric_id"]].get(key) for key in ("kind", "unit", "scale", "currency")} for s in plan["columns"]}
        for operation_index, operation in enumerate(plan.get("operations", [])):
            op, column, output = operation["op"], operation["column"], operation["output"]
            source = frame[column].copy()
            for input_name in (column, operation.get("index"), operation.get("denominator")):
                if input_name is None:
                    continue
                values = frame[input_name]
                if pd.api.types.is_integer_dtype(values.dtype) and ((values > 2**53) | (values < -(2**53))).any():
                    raise PlanError(f"{input_name}: exact integer arithmetic beyond 2**53 is not supported; native values remain available", code="NUMERIC_PRECISION_UNSUPPORTED")
            if op in {"growth", "difference"}:
                previous = source.shift(operation.get("periods", 1))
                result = (source / previous.where(previous != 0) - 1) * 100 if op == "growth" else source - previous
                if operation.get("prior_scope", "available_history") == "selected_window":
                    visible = result.index[result.index >= validated["start"]]
                    result.loc[visible[: operation.get("periods", 1)]] = np.nan
                if op == "growth" and (previous == 0).any():
                    warnings.append({"code": "zero_denominator", "column": output})
            elif op == "deflate":
                index = frame[operation["index"]]
                base_period = _period(operation["base_period"], plan["frequency"])
                base = index.get(base_period, np.nan)
                if pd.isna(base) or base <= 0:
                    raise PlanError("Deflator base observation must exist and be positive", code="MISSING_OBSERVATIONS")
                result = source / index.where(index > 0) * base
                if ((index <= 0) & index.notna()).any():
                    warnings.append({"code": "invalid_deflator", "column": output})
            elif op == "scale":
                result = source * schemas[column].get("scale", 1) / operation["target_scale"]
            else:
                if "comparison" in validated["operation_schemas"][operation_index].get("scope", {}):
                    warnings.append({"code": "cross_scope_comparison", "column": output, "detail": "Explicit numerical comparison across different scopes; population compatibility is not certified", "scope_reason": operation["scope_reason"], "scopes": validated["operation_schemas"][operation_index]["scope"]["comparison"]})
                denominator = frame[operation["denominator"]] * schemas[operation["denominator"]].get("scale", 1)
                result = source * schemas[column].get("scale", 1) / denominator.where(denominator != 0) * operation.get("multiplier", 100)
                if (denominator == 0).any():
                    warnings.append({"code": "zero_denominator", "column": output})
            frame[output] = result.replace([np.inf, -np.inf], np.nan)
            schemas[output] = copy.deepcopy(validated["operation_schemas"][operation_index])
            lineage["operations"].append(copy.deepcopy(operation))
        frame = frame.loc[validated["start"]:validated["end"]].copy()
        for column in frame:
            missing = frame[column].isna()
            if missing.any():
                warnings.append({"code": "missing_result", "column": column, "periods": [_label(p) for p in frame.index[missing]][:12], "count": int(missing.sum())})
        frame.insert(0, "period", [_label(p) for p in frame.index])
        frame = frame.reset_index(drop=True)
        return frame, lineage, warnings

    @staticmethod
    def _envelope(frame: pd.DataFrame, manifest: dict, warnings: list) -> dict:
        # Reload and interrupted-write recovery use the same durable warnings
        # as the original response. Recovery-specific notes may be appended.
        durable_warnings = copy.deepcopy(manifest.get("lineage", {}).get("warnings", []))
        for warning in warnings:
            if warning not in durable_warnings:
                durable_warnings.append(copy.deepcopy(warning))
        warnings = durable_warnings
        return _json({"status": "ok", "analysis_id": manifest["analysis_id"], "parent_analysis_id": manifest.get("parent_analysis_id"), "snapshot_id": manifest["snapshot_id"], "workspace_version": manifest["new_workspace_version"], "row_count": len(frame), "columns": list(frame), "schema": manifest.get("schema"), "preview": (frame if len(frame) <= 10 else pd.concat([frame.head(5), frame.tail(5)])).to_dict("records"), "preview_truncated": len(frame) > 10, "warnings": warnings, "result_ref": manifest["analysis_id"], "join_contract": manifest["lineage"].get("join_contract")})

    def execute(self, plan: dict) -> dict:
        with self._context() as (connection, bindings, workspace):
            validated = self._validate(plan, bindings)
            frame, lineage, warnings = self._evaluate(connection, bindings, plan, validated)
            lineage["warnings"] = copy.deepcopy(warnings)
            manifest = self.store.save_analysis(self.workspace_id, frame, copy.deepcopy(plan), _json(lineage), schema=validated["schema"], expected_version=workspace["version"])
            return self._envelope(frame, manifest, warnings)

    def query_grouped(self, request: dict) -> dict:
        """Rank independently validated dimension members within each period.

        This does not sum bank populations, countries or cities together. Every
        member retains the same native-read, temporal and source-cell contract
        used by scalar analyses. Limits apply per period after ranking.
        """
        _object(request, {"metric_id", "group_by", "dimensions", "start", "end", "frequency", "alignment", "order", "limit", "operations"},
                {"metric_id", "group_by", "dimensions", "start", "end", "frequency"}, "query_grouped")
        group_by, limit, order = request["group_by"], request.get("limit", 10), request.get("order", "desc")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise PlanError("limit must be between 1 and 100")
        if not isinstance(order, str) or order not in {"asc", "desc"}:
            raise PlanError("order must be asc or desc")
        if not isinstance(group_by, str) or group_by in {"period", "value", "rank"}:
            raise PlanError("Grouping dimension conflicts with reserved result columns")
        with self._context() as (connection, bindings, workspace):
            metric_id = request["metric_id"]
            if not isinstance(metric_id, str) or metric_id not in bindings:
                raise PlanError("Unknown metric_id", code="METRIC_NOT_FOUND")
            binding = bindings[metric_id]
            if binding["status"] != "ready":
                raise PlanError("Ranking requires reviewed numeric semantics", code="SEMANTICS_REVIEW_REQUIRED")
            if not isinstance(group_by, str) or group_by not in binding.get("dimensions", {}):
                raise PlanError("Unknown grouping dimension", code="DIMENSION_NOT_FOUND")
            fixed = request["dimensions"]
            if not isinstance(fixed, dict) or set(fixed) != set(binding["dimensions"]) - {group_by}:
                raise PlanError("Fix exactly every other dimension; the grouped dimension must be left free")
            # Validate all scalar values and semantic operations before querying
            # distinct dimension members or persisting any result.
            seed = {"start": request["start"], "end": request["end"], "frequency": request["frequency"],
                    "columns": [{"name": "value", "metric_id": metric_id,
                                 "dimensions": {**fixed, group_by: "__validation_only__"},
                                 "alignment": request.get("alignment", "native")}]}
            if request.get("operations"):
                seed["operations"] = copy.deepcopy(request["operations"])
            self._validate(seed, bindings)
            members = self._dimension_rows(connection, binding, group_by, fixed)
            if not members:
                raise PlanError("No groups match the fixed dimensions", code="MISSING_OBSERVATIONS")
            if len(members) > 250:
                raise PlanError("Grouped query exceeds 250 dimension members", code="DIMENSION_CARDINALITY_LIMIT")
            first, last = _period(request["start"], request["frequency"]), _period(request["end"], request["frequency"])
            if len(members) * (last.ordinal - first.ordinal + 1) > 10000:
                raise PlanError("Grouped query exceeds 10000 result cells; narrow the period window")
            frames, groups, warnings, schema = [], {}, [], {}
            for member in members:
                plan = copy.deepcopy(seed)
                plan["columns"][0]["dimensions"][group_by] = member["value"]
                validated = self._validate(plan, bindings)
                try:
                    frame, lineage, notes = self._evaluate(connection, bindings, plan, validated)
                except PlanError as error:
                    if error.code != "MISSING_OBSERVATIONS":
                        raise
                    warnings.append({"code": "group_missing_observations", "dimensions": {group_by: member["value"]}, "detail": str(error)})
                    continue
                frame.insert(1, group_by, member["value"])
                frames.append(frame)
                groups[json.dumps(_json(member["value"]), ensure_ascii=False, sort_keys=True)] = lineage
                warnings.extend({**note, "dimensions": {group_by: member["value"]}} for note in notes)
                schema = validated["schema"]
            if not frames:
                raise PlanError("No groups have observations in the requested window", code="MISSING_OBSERVATIONS")
            frame = pd.concat(frames, ignore_index=True)
            frame = frame.sort_values(["period", "value", group_by], ascending=[True, order == "asc", True], na_position="last", kind="stable")
            # Rank exact integers without coercing them through float64. Native
            # counters above 2**53 remain distinct, including tied values.
            frame["rank"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
            for _, group in frame.groupby("period", sort=False):
                previous, rank = None, 0
                for position, (index, value) in enumerate(group["value"].items(), start=1):
                    if pd.isna(value):
                        continue
                    if previous is None or value != previous:
                        rank = position
                    frame.loc[index, "rank"] = rank
                    previous = value
            total_rows = len(frame)
            frame = frame.groupby("period", sort=False).head(limit).reset_index(drop=True)
            for numeric_schema in schema.values():
                if "scope" in numeric_schema:
                    numeric_schema["scope"]["dimensions"] = {**fixed, group_by: "per_result_row"}
            schema[group_by] = {"kind": "dimension", "unit": "label", "status": "ready"}
            schema["rank"] = {"kind": "rank", "unit": "ordinal", "status": "ready", "ties": "minimum_rank", "nulls": "unranked"}
            warnings.append({"code": "group_populations_not_summed", "detail": "Members retain source scopes and labels, including any abroad or aggregate member; no cross-group additive total is asserted"})
            lineage = {"frequency": request["frequency"], "group_by": group_by, "groups": groups,
                       "warnings": copy.deepcopy(warnings),
                       "join_contract": {"key": ["period", group_by], "cardinality": "one_value_per_dimension_member_per_period", "population_equivalence_asserted": False}}
            stored_plan = {"query_type": "grouped", "request": copy.deepcopy(request)}
            manifest = self.store.save_analysis(self.workspace_id, frame, stored_plan, _json(lineage), schema=_json(schema), expected_version=workspace["version"])
            result = self._envelope(frame, manifest, warnings)
            result.update(group_by=group_by, group_count=len(frames), order=order, limit_per_period=limit,
                          total_rows_before_limit=total_rows, truncated=total_rows > len(frame))
            return result

    def _revise_grouped(self, request, parent, parent_manifest):
        if request.get("add_columns"):
            raise PlanError("Adding an external measure to a grouped result requires an explicit group mapping; use scalar columns or a separate query", code="GROUPED_SOURCE_MAPPING_REQUIRED")
        if request.get("start") or request.get("end"):
            raise PlanError("Changing the time window of a ranked grouped result requires a new explicit grouped query", code="GROUPED_WINDOW_CHANGE_REQUIRED")
        original = copy.deepcopy(parent_manifest["plan"]["request"])
        combined = copy.deepcopy(original.get("operations", [])) + copy.deepcopy(request.get("operations", []))
        plan = {"query_type": "grouped", "request": {**original, "operations": combined}}
        group_by = original["group_by"]
        replaced = {operation.get("output") for operation in request.get("operations", [])}
        if any(name in replaced for name in {"period", group_by, "rank"}):
            raise PlanError("Revision cannot replace grouped keys or ranking metadata")
        preserved = [name for name in parent if name not in replaced]
        result = parent.copy(deep=True)
        groups, warnings, schemas = {}, [], copy.deepcopy(parent_manifest["schema"])
        with self._context() as (connection, bindings, workspace):
            if parent_manifest["snapshot_id"] != workspace["snapshot_id"] or not set(parent_manifest.get("datasets", [])).issubset(workspace.get("datasets", [])):
                raise PlanError("Revision must retain its original snapshot and dataset versions")
            for member, selected in parent.groupby(group_by, sort=False):
                scalar = {"start": original["start"], "end": original["end"], "frequency": original["frequency"],
                          "columns": [{"name": "value", "metric_id": original["metric_id"],
                                       "dimensions": {**original["dimensions"], group_by: _json(member)},
                                       "alignment": original.get("alignment", "native")}], "operations": combined}
                validated = self._validate(scalar, bindings)
                calculated, lineage, notes = self._evaluate(connection, bindings, scalar, validated)
                lookup = calculated.set_index("period")
                for name in calculated:
                    if name == "period" or name in preserved:
                        continue
                    if name not in result:
                        result[name] = pd.Series(pd.NA, index=result.index, dtype="Float64")
                    result.loc[selected.index, name] = selected["period"].map(lookup[name]).values
                    schemas[name] = copy.deepcopy(validated["schema"][name])
                    schemas[name]["scope"]["dimensions"] = {**original["dimensions"], group_by: "per_result_row"}
                groups[json.dumps(_json(member), ensure_ascii=False, sort_keys=True)] = lineage
                warnings.extend({**note, "dimensions": {group_by: _json(member)}} for note in notes)
            for name in preserved:
                if not result[name].equals(parent[name]):
                    raise PlanError("Grouped revision changed a preserved source cell")
            warnings = copy.deepcopy(parent_manifest["lineage"].get("warnings", [])) + warnings
            # A revision retains the parent's selected population and order.
            # Replacing its measure must not imply ranks were recalculated.
            if "rank" in schemas:
                schemas["rank"]["ranked_analysis_id"] = parent_manifest["schema"].get("rank", {}).get("ranked_analysis_id", request["analysis_id"])
                schemas["rank"]["ranked_column"] = "value"
                if "value" in replaced:
                    warnings.append({"code": "RANK_PRESERVED_FROM_PARENT", "message": "Ranks and selected groups retain the original measure; revised values have not been reranked.", "analysis_id": schemas["rank"]["ranked_analysis_id"]})
            lineage = {"frequency": original["frequency"], "group_by": group_by, "groups": groups, "warnings": warnings,
                       "preserved_columns": {name: {"analysis_id": request["analysis_id"], "column": name}
                                             for name in preserved if name not in {"period", group_by, "rank"}},
                       "join_contract": copy.deepcopy(parent_manifest["lineage"]["join_contract"])}
            saved = self.store.save_analysis(self.workspace_id, result, plan, _json(lineage), schema=_json(schemas),
                                             parent_analysis_id=request["analysis_id"], expected_version=workspace["version"])
            envelope = self._envelope(result, saved, warnings)
            envelope.update(preserved_columns=preserved, group_by=group_by)
            return envelope

    def revise_analysis(self, request: dict) -> dict:
        _object(request, {"analysis_id", "add_columns", "operations", "start", "end"}, {"analysis_id"}, "revision")
        if not isinstance(request["analysis_id"], str):
            raise PlanError("analysis_id must be a string")
        if not isinstance(request.get("add_columns", []), list) or not isinstance(request.get("operations", []), list):
            raise PlanError("Revision columns and operations must be arrays")
        if not request.get("add_columns") and not request.get("operations") and not request.get("start") and not request.get("end"):
            raise PlanError("An empty revision has no effect")
        parent, parent_manifest = self.store.load_analysis(request["analysis_id"])
        if parent_manifest["workspace_id"] != self.workspace_id:
            raise PlanError("Analysis belongs to another workspace")
        if parent_manifest["plan"].get("query_type") == "grouped":
            return self._revise_grouped(request, parent, parent_manifest)
        if parent_manifest["plan"].get("query_type") == "dataset":
            raise PlanError("Dataset aggregates require a new explicit aggregate_dataset request", code="DATASET_REVISION_REQUIRED")
        def _series_key(column):
            return (column.get("metric_id"), json.dumps(column.get("dimensions") or {}, sort_keys=True), column.get("alignment", "native"))
        existing_series = {_series_key(column) for column in parent_manifest["plan"].get("columns", []) if isinstance(column, dict)}
        for selection in request.get("add_columns", []):
            if isinstance(selection, dict) and selection.get("name") in parent.columns:
                raise PlanError("An added column must have a new name; use an operation for explicit replacement")
            # A time series is extended by a wider start/end and re-execution, not by adding
            # the same metric again: a duplicate (metric, dimensions, alignment) column would
            # just repeat identical values.
            if isinstance(selection, dict) and _series_key(selection) in existing_series:
                raise PlanError("Bu seri (aynı metrik, boyut ve hizalama) tabloda zaten var; başka bir dönemi görmek için start/end aralığını genişletip yeniden hesaplayın, aynı seriyi ikinci kolon olarak eklemeyin.", code="DUPLICATE_COLUMN")
        plan = copy.deepcopy(parent_manifest["plan"])
        for bound in ("start", "end"):
            if bound in request:
                plan[bound] = request[bound]
        expanded = any(plan[key] != parent_manifest["plan"][key] for key in ("start", "end"))
        if expanded:
            if (_period(plan["start"], plan["frequency"]) > _period(parent_manifest["plan"]["start"], plan["frequency"])
                    or _period(plan["end"], plan["frequency"]) < _period(parent_manifest["plan"]["end"], plan["frequency"])):
                raise PlanError("A revision may extend the time window, not remove existing periods")
        plan["columns"].extend(copy.deepcopy(request.get("add_columns", [])))
        plan.setdefault("operations", []).extend(copy.deepcopy(request.get("operations", [])))
        with self._context() as (connection, bindings, workspace):
            if parent_manifest["snapshot_id"] != workspace["snapshot_id"] or not set(parent_manifest.get("datasets", [])).issubset(workspace.get("datasets", [])):
                raise PlanError("Revision must retain its original snapshot and dataset versions")
            validated = self._validate(plan, bindings)
            frame, lineage, warnings = self._evaluate(connection, bindings, plan, validated)
            if not expanded and not frame["period"].equals(parent["period"]):
                raise PlanError("Revision changed the original period keys")
            if expanded and not set(parent["period"]).issubset(set(frame["period"])):
                raise PlanError("Extended revision lost an existing period")
            replaced = {op["output"] for op in request.get("operations", [])}
            preserved = [column for column in parent if column not in replaced]
            for column in preserved:
                # Preserve the saved parent's cells, even if a new operation reuses
                # a source name. Revision semantics are explicit column replacement.
                if expanded:
                    original_cells = parent.set_index("period")[column] if column != "period" else None
                    if original_cells is not None:
                        mask = frame["period"].isin(parent["period"])
                        frame.loc[mask, column] = frame.loc[mask, "period"].map(original_cells).values
                else:
                    frame[column] = parent[column]
                if column in parent_manifest.get("schema", {}):
                    validated["schema"][column] = copy.deepcopy(parent_manifest["schema"][column])
            # Preserve the evidence used for preserved cells even if a future
            # source-policy version or added warmup changes reevaluated lineage.
            lineage["preserved_columns"] = {column: {"analysis_id": request["analysis_id"], "column": column}
                                            for column in preserved if column != "period"}
            if expanded:
                for inherited in lineage["preserved_columns"].values():
                    inherited["periods"] = parent["period"].tolist()
            if "warnings" in parent_manifest["lineage"]:
                warnings = [warning for warning in warnings if warning.get("column") not in preserved]
                for warning in parent_manifest["lineage"]["warnings"]:
                    if (not warning.get("column") or warning["column"] in preserved) and warning not in warnings:
                        warnings.append(copy.deepcopy(warning))
            lineage["warnings"] = copy.deepcopy(warnings)
            manifest = self.store.save_analysis(self.workspace_id, frame, plan, _json(lineage), schema=validated["schema"], parent_analysis_id=request["analysis_id"], expected_version=workspace["version"])
            result = self._envelope(frame, manifest, warnings)
            result["preserved_columns"] = preserved
            return result

    def explain_value(self, request: dict) -> dict:
        """Return stored reference lineage, without claiming raw-file byte verification.

        Snapshot/result hashes are verified by the store on read. Source file
        locators and hashes are preserved references; this tool does not reopen
        the original external files. ``lineage_complete`` is a compatibility
        alias for ``source_references_complete``.
        """
        _object(request, {"analysis_id", "column", "period", "dimensions"}, {"analysis_id", "column", "period"}, "explanation")
        if not all(isinstance(request[key], str) for key in ("analysis_id", "column", "period")):
            raise PlanError("Explanation fields must be strings")
        frame, manifest = self.store.load_analysis(request["analysis_id"])
        if manifest["workspace_id"] != self.workspace_id:
            raise PlanError("Analysis belongs to another workspace")
        column = request["column"]
        if column == "period" or column not in frame:
            raise PlanError("Unknown result column")
        selected = frame[frame["period"] == request["period"]]
        lineage = manifest["lineage"]
        if "dataset_query" in lineage:
            proof = lineage["dataset_query"]
            keys = proof["output_keys"]
            group_by = lineage.get("group_by")
            dimensions = request.get("dimensions", {})
            if set(dimensions) != ({group_by} if group_by else set()):
                raise PlanError("Supply exactly the saved grouping dimension for this dataset result")
            if group_by:
                selected = selected[selected[group_by] == dimensions[group_by]]
            if len(selected) != 1 or column in keys:
                raise PlanError("Select one saved numeric result cell")
            key = json.dumps([_json(selected.iloc[0][k]) for k in keys], ensure_ascii=False)
            cell = proof["cells"].get(key, {}).get(column)
            if cell is None:
                raise PlanError("Source rows are not bound to this aggregate")
            # Reopen immutable uploaded CSV and Parquet through hash-verifying
            # store methods; original external PDF bytes are a separate check.
            dataset = self.store.dataset_manifest(proof["dataset_id"])
            self.store.overlay_path(proof["dataset_id"])
            self.store.raw_source_path(proof["dataset_id"])
            if dataset["source_sha256"] != proof["source_sha256"]:
                raise PlanError("Dataset source hash changed", code="SOURCE_INTEGRITY_ERROR")
            return _json({"status": "ok", "analysis_id": request["analysis_id"], "snapshot_id": manifest["snapshot_id"],
                          "column": column, "period": request["period"], "value": selected.iloc[0][column],
                          "schema": manifest["schema"].get(column), "lineage": {**cell, "dataset_id": proof["dataset_id"],
                          "source_sha256": proof["source_sha256"], "row_index_basis": proof["row_index_basis"],
                          "document_provenance": proof.get("document_provenance")},
                          "lineage_complete": True, "source_references_complete": True,
                          "dataset_files_verified": True, "source_files_verified": False, "lineage_issues": []})
        inherited = lineage.get("preserved_columns", {}).get(column)
        if inherited and request["period"] in inherited.get("periods", [request["period"]]):
            inherited_request = {"analysis_id": inherited["analysis_id"], "column": inherited["column"], "period": request["period"]}
            if request.get("dimensions"):
                inherited_request["dimensions"] = request["dimensions"]
                for dimension, value in request["dimensions"].items():
                    if dimension not in selected:
                        raise PlanError("Unknown grouping dimension")
                    selected = selected[selected[dimension] == value]
            if len(selected) != 1:
                raise PlanError("Unknown result period")
            result = self.explain_value(inherited_request)
            if _json(selected.iloc[0][column]) != result["value"]:
                raise PlanError("Preserved cell differs from its parent evidence", code="PRESERVED_VALUE_MISMATCH")
            result.update(analysis_id=request["analysis_id"], inherited_from_analysis_id=inherited["analysis_id"],
                          schema=manifest.get("schema", {}).get(column))
            return result
        if "group_by" in lineage:
            group_by = lineage["group_by"]
            dimensions = request.get("dimensions")
            if not isinstance(dimensions, dict) or set(dimensions) != {group_by}:
                raise PlanError(f"Grouped explanation requires dimensions containing exactly {group_by}")
            if column in {group_by, "rank"}:
                raise PlanError("Explain a grouped numeric measure; ranking policy is recorded in its result schema")
            group_value = dimensions[group_by]
            if isinstance(group_value, (dict, list, bool)) or group_value is None:
                raise PlanError("Grouping dimension must be a scalar value")
            selected = selected[selected[group_by] == group_value]
            key = json.dumps(_json(group_value), ensure_ascii=False, sort_keys=True)
            if key not in lineage["groups"]:
                raise PlanError("Unknown result group")
            lineage = lineage["groups"][key]
        elif request.get("dimensions"):
            raise PlanError("Scalar analysis explanations do not accept grouping dimensions")
        if len(selected) != 1:
            raise PlanError("Unknown result period")
        period = _period(request["period"], lineage["frequency"])
        operations = lineage["operations"]
        explanation_nodes = 0
        lineage_issues: set[str] = set()

        def explain(name: str, at: pd.Period, before: int) -> dict:
            nonlocal explanation_nodes
            explanation_nodes += 1
            if explanation_nodes > 512:
                lineage_issues.add("Explanation exceeds the node limit; the stored recipe is available in the analysis manifest")
                return {"column": name, "period": _label(at), "status": "truncated", "recipe_operation_limit": before}
            for idx in range(before - 1, -1, -1):
                operation = operations[idx]
                if operation["output"] != name:
                    continue
                inputs = [explain(operation["column"], at, idx)]
                if operation["op"] in {"growth", "difference"}:
                    inputs.append(explain(operation["column"], at - operation.get("periods", 1), idx))
                elif operation["op"] == "deflate":
                    inputs.extend([explain(operation["index"], at, idx), explain(operation["index"], _period(operation["base_period"], lineage["frequency"]), idx)])
                elif operation["op"] == "ratio":
                    inputs.append(explain(operation["denominator"], at, idx))
                return {"column": name, "period": _label(at), "operation": operation, "inputs": inputs}
            proof = lineage["sources"][name]
            binding = proof["binding"]
            source_cells = proof["cells"].get(_label(at), [])
            if not source_cells:
                lineage_issues.add(f"{binding['metric_id']} at {_label(at)}: no native observation is available")
            for cell in source_cells:
                has_hash = any(cell.get(key) for key in ("source_sha256", "source_response_sha256", "source_csv_sha256"))
                has_locator = any(cell.get(key) for key in ("source_file", "source_response_file", "source_csv_file"))
                overlay_source = bool(binding.get("dataset_id") and binding.get("source_sha256"))
                if not (has_hash and has_locator or overlay_source):
                    lineage_issues.add(f"{binding['metric_id']}: raw source hash and locator are not both bound")
                if "previous_cumulative_source_cells" in cell and not cell["previous_cumulative_source_cells"]:
                    lineage_issues.add(f"{binding['metric_id']} at {_label(at)}: previous cumulative source cell is unavailable")
            explanation = {"column": name, "period": _label(at), "metric_id": binding["metric_id"], "contract_version": binding.get("contract_version"), "unit": binding.get("unit"), "scale": binding.get("scale"), "source_base": binding.get("source_base"), "hash_basis": binding.get("hash_basis", "file_bytes"), "source_sha256": binding.get("source_sha256"), "dataset_id": binding.get("dataset_id"), "source_namespace": binding.get("source_namespace"), "document_provenance": copy.deepcopy(binding.get("document_provenance")), "dimensions": proof["dimensions"], "alignment": proof["alignment"], "source_cells": source_cells}
            if binding.get("source_cell_locator_policy"):
                explanation["source_cell_locator_policy"] = copy.deepcopy(binding["source_cell_locator_policy"])
            return explanation

        proof = explain(column, period, len(operations))
        return _json({"status": "ok", "analysis_id": request["analysis_id"], "snapshot_id": manifest["snapshot_id"], "column": column, "period": request["period"], "value": selected.iloc[0][column], "schema": manifest.get("schema", {}).get(column), "lineage": proof, "lineage_complete": not lineage_issues, "source_references_complete": not lineage_issues, "source_files_verified": False, "lineage_issues": sorted(lineage_issues)})
