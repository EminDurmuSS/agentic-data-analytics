"""Provider recovery with one request budget and consistent result filtering."""
import time
import re
from urllib import parse
import xml.etree.ElementTree as ET

from agentic_analytics.agent.tools import search_backend as backend


SEARCH_BUDGET_SECONDS = 45


class SearchError(ValueError):
    def __init__(self, message, code="SEARCH_INVALID_RESPONSE"):
        super().__init__(message)
        self.code = code


def _normalize(entries):
    if not isinstance(entries, list):
        raise SearchError("Search results must be an array.")
    results, skipped = [], 0
    for item in entries[:50]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not 1 <= len(item["url"]) <= 4096:
            skipped += 1
            continue
        url = item["url"]
        try:
            target = parse.urlsplit(url)
            valid = (target.scheme in {"http", "https"} and bool(target.hostname)
                     and not target.username and not target.password and not any(ord(char) < 32 for char in url))
        except ValueError:
            valid = False
        if not valid:
            skipped += 1
            continue
        published = next((item[key] for key in ("published_at", "publishedDate")
                          if isinstance(item.get(key), str) and item[key].strip()), None)
        results.append({"url": url, "title": str(item.get("title", ""))[:300],
                        "snippet": str(item.get("content", ""))[:1200],
                        **({"published_at": published[:100], "publication_date_basis": "search_metadata_unverified"} if published else {}),
                        **{key: item[key] for key in ("discovered_from", "registry_evidence") if key in item}})
    return results, skipped


def _bing(query, fetch):
    url = "https://www.bing.com/search?" + parse.urlencode({"format": "rss", "q": query})
    raw, _, _ = fetch(url, max_bytes=1024**2, timeout=15)
    if b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
        raise SearchError("Search response contains unsupported XML declarations.")
    rss = ET.fromstring(raw)
    if rss.tag != "rss":
        raise SearchError("Search did not return RSS results.")
    return [{"title": item.findtext("title", ""), "url": item.findtext("link", ""),
             "content": item.findtext("description", "")} for item in rss.findall("./channel/item")]


def _state(results):
    if not results:
        return "empty"
    if all(item["discovery_only"] for item in results):
        return "navigation_only"
    if not any(not item["discovery_only"] and not item["entity_verification_required"] for item in results):
        return "issuer_unverified"
    return "success"


def run_search(query, limit, *, configured_url, fetch, deadline=None):
    """Try each provider once; even registry subrequests share the deadline.

    Private addresses remain permitted only for the operator-configured search
    service. Every public provider and result uses the supplied public fetcher.
    """
    started = time.monotonic()
    deadline = min(deadline, started + SEARCH_BUDGET_SECONDS) if deadline is not None else started + SEARCH_BUDGET_SECONDS
    # Google is the first, bounded discovery index.  Do not ask SearXNG to
    # aggregate every enabled engine: a CAPTCHA or slow unrelated engine can
    # otherwise consume the entire request budget before Google responds.
    kap_applicable = backend.kap_search_applicable(query)
    providers = (["Google via SearXNG"] if configured_url else []) + ["Bing RSS"]
    if kap_applicable:
        providers.append("KAP Public Financial Registry")
    providers.append("DuckDuckGo Lite")
    attempts, warnings, results = [], [], []
    domains = backend.search_domains(query)
    winning_provider, skipped_total, rejected_total = None, 0, 0
    budget_exhausted, blocked_providers, kap_tried = False, {}, not kap_applicable
    for provider in providers:
        # Free-text search snippets can rank a merely entity-matched,
        # on-topic page as "success" even when it is a homepage or stub, not
        # the actual report. Before the authoritative BIST registry has had
        # its turn, only an actual document is confident enough to skip it;
        # once the registry has been consulted, its absence no longer holds
        # up the loop.
        if _state(results) == "success" and (kap_tried or any(
                not item["discovery_only"] and not item["entity_verification_required"] and item.get("is_document")
                for item in results)):
            break
        if provider == "KAP Public Financial Registry":
            kap_tried = True
        if provider in blocked_providers:
            attempts.append({"provider": provider, "status": "skipped", "code": "SEARCH_PROVIDER_BLOCKED",
                             "diagnostics": blocked_providers[provider], "elapsed_ms": 0,
                             "raw_count": 0, "accepted_count": 0, "rejected_count": 0})
            continue
        now = time.monotonic()
        if now >= deadline:
            budget_exhausted = True
            break
        provider_deadline = min(deadline, now + (15 if provider == "KAP Public Financial Registry" else 10))
        attempt = {"provider": provider, "raw_count": 0, "accepted_count": 0, "rejected_count": 0}

        def remaining():
            value = provider_deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("Search provider time budget exceeded.")
            return value

        def bounded_fetch(url, **kwargs):
            kwargs["timeout"] = min(kwargs.get("timeout", 15), remaining())
            value = fetch(url, **kwargs)
            remaining()
            return value

        try:
            if provider == "Google via SearXNG":
                response = backend.configured_search(
                    configured_url, query, timeout=remaining(), engines=("google",), with_diagnostics=True
                )
                # A list is retained for custom adapters implementing the older
                # contract. The built-in provider also returns engine diagnostics.
                entries = response.get("results") if isinstance(response, dict) else response
                if isinstance(response, dict) and response.get("diagnostics"):
                    attempt["diagnostics"] = response["diagnostics"][:12]
                    for diagnostic in attempt["diagnostics"]:
                        engine = str(diagnostic.get("engine", "")).casefold()
                        alternative = {"bing": "Bing RSS", "duckduckgo": "DuckDuckGo Lite"}.get(engine)
                        if alternative and re.search(r"captcha|\b403\b|\b429\b|forbidden|access.denied|rate.limit|too.many.requests|bot.challenge",
                                                     str(diagnostic.get("reason", "")), re.I):
                            blocked_providers.setdefault(alternative, []).append(diagnostic)
            elif provider == "Bing RSS":
                entries = _bing(query, bounded_fetch)
            elif provider == "KAP Public Financial Registry":
                entries = backend.kap_financial_search(query, bounded_fetch)
            else:
                entries = backend.public_search_fallback(query, bounded_fetch)
            remaining()
            attempt["raw_count"] = len(entries) if isinstance(entries, list) else 0
            normalized, skipped = _normalize(entries)
            skipped_total += skipped
            attempt["rejected_count"] = skipped
            if skipped and not normalized:
                raise SearchError("Search returned only malformed result entries.")
            accepted, rejected, _ = backend.rank_search_results(query, normalized)
            accepted = [{**item, "search_provider": provider} for item in accepted]
            rejected_total += len(rejected)
            attempt.update(status=_state(accepted), accepted_count=len(accepted), rejected_count=len(rejected) + skipped)
            if accepted:
                results, _, _ = backend.rank_search_results(query, [*accepted, *results])
                winning_provider = provider
        except (ValueError, OSError, ET.ParseError) as exc:
            attempt.update(status="unavailable", code=getattr(exc, "code", "SEARCH_TIMEOUT" if isinstance(exc, TimeoutError)
                                                               else "SEARCH_INVALID_RESPONSE" if isinstance(exc, (ValueError, ET.ParseError))
                                                               else "SEARCH_PROVIDER_UNAVAILABLE"), message=str(exc)[:240])
            warnings.append({"code": "SEARCH_FALLBACK_UNAVAILABLE" if attempts else "SEARCH_PROVIDER_UNAVAILABLE",
                             "provider": provider, "cause": attempt["code"], "message": attempt["message"]})
        attempt["elapsed_ms"] = round(max(0, time.monotonic() - now) * 1000)
        attempts.append(attempt)
    budget_exhausted = budget_exhausted or time.monotonic() >= deadline
    warnings = ([{"code": "MALFORMED_SEARCH_RESULTS_SKIPPED", "count": skipped_total}] if skipped_total else []) + warnings
    if rejected_total:
        warnings.append({"code": "IRRELEVANT_SEARCH_RESULTS_SKIPPED", "count": rejected_total})
    if budget_exhausted:
        warnings.append({"code": "SEARCH_BUDGET_EXHAUSTED"})
    results = results[:limit]
    state = _state(results)
    recovery = {"tool": "research_web", "arguments": {"query": query, "limit": 2}}
    # An issuer-unverified report cannot establish the institution's domain.
    # Otherwise recovery would be trapped on the very candidate being checked.
    recovery_domains = domains or list(dict.fromkeys(parse.urlsplit(item["url"]).hostname for item in results
                                                    if not item["entity_verification_required"]))[:3]
    if recovery_domains:
        recovery["arguments"]["domains"] = recovery_domains
    all_failed = not attempts or all(attempt["status"] in {"unavailable", "skipped"} for attempt in attempts)
    code = ("SEARCH_BUDGET_EXHAUSTED" if budget_exhausted and not results else attempts[-1].get("code", "SEARCH_INVALID_RESPONSE")) if all_failed else {
        "empty": "SEARCH_NO_RELEVANT_RESULTS", "navigation_only": "SEARCH_DISCOVERY_ONLY",
        "issuer_unverified": "SEARCH_ENTITY_UNVERIFIED"}.get(state)
    last_attempted = next((attempt["provider"] for attempt in reversed(attempts) if attempt["status"] != "skipped"), providers[0])
    return {"status": "unavailable" if all_failed and not results else "ok", "query": query,
            "results": results, "source_backend": (results[0].get("search_provider") if results else None) or winning_provider or last_attempted,
            "provider_attempts": attempts, "budget_exhausted": budget_exhausted, "warnings": warnings,
            **({"code": code, "recovery": recovery} if code else {}),
            "content_is_untrusted_data": True, "sources_verified": False,
            "next_step": ("Inspect result URLs before relying on them as citation evidence." if state == "success" else
                          "No verified report identity was established. Use research_web with the supplied recovery arguments to inspect candidates or follow official source links; do not repeat query variants or answer from navigation leads.")}
