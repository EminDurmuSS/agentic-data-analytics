"""Provider failures cannot silently end research or weaken source identity."""
import io
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from unittest.mock import Mock

import pytest

from agentic_analytics.agent.tools import search_backend as backend
from agentic_analytics.agent.tools import search_pipeline as pipeline
from agentic_analytics.agent.tools.documents import DocumentTools


QUERY = "Example Bank March 2026 consolidated financial report"
REPORT = {"url": "https://example.org/2026/report.pdf", "title": "Example Bank March 2026 consolidated financial report"}


def rss(items):
    import xml.etree.ElementTree as ET
    root = ET.Element("rss")
    channel = ET.SubElement(root, "channel")
    for result in items:
        item = ET.SubElement(channel, "item")
        for name, value in (("title", result["title"]), ("link", result["url"]), ("description", result.get("content", ""))):
            ET.SubElement(item, name).text = value
    return ET.tostring(root), "text/xml", "https://www.bing.com/search"


@pytest.mark.parametrize("configured", [[], [None], OSError("SearX offline"),
    [{"url": "https://jobs.test/", "title": "Jobs"}],
    [{"url": "https://example.org/", "title": "Example Bank"}],
    [{"url": "https://unrelated.test/2026.pdf", "title": "Other Bank March 2026 consolidated financial report"}]])
def test_configured_failure_or_weak_results_try_an_independent_provider(monkeypatch, configured):
    primary = Mock(side_effect=configured) if isinstance(configured, Exception) else Mock(return_value=configured)
    monkeypatch.setattr(backend, "configured_search", primary)
    unused_registry, unused_alternate = Mock(), Mock()
    monkeypatch.setattr(backend, "kap_financial_search", unused_registry)
    monkeypatch.setattr(backend, "public_search_fallback", unused_alternate)
    fetch = Mock(return_value=rss([REPORT]))
    result = pipeline.run_search(QUERY, 5, configured_url="http://127.0.0.1:8888", fetch=fetch)
    assert result["status"] == "ok"
    assert result["results"][0]["url"] == REPORT["url"]
    assert result["source_backend"] == "Bing RSS"
    assert [a["provider"] for a in result["provider_attempts"]] == ["Google via SearXNG", "Bing RSS"]
    assert 0 < primary.call_args.kwargs["timeout"] <= 10
    assert primary.call_args.kwargs["engines"] == ("google",)
    assert 0 < fetch.call_args.kwargs["timeout"] <= 10
    assert not result["sources_verified"]
    unused_registry.assert_not_called()
    unused_alternate.assert_not_called()


def test_bing_challenge_continues_to_registry_without_losing_error(monkeypatch):
    monkeypatch.setattr(backend, "kap_financial_search", Mock(return_value=[REPORT]))
    fetch = Mock(return_value=(b"<html>Challenge</html>", "text/html", "https://bing.com"))
    result = pipeline.run_search(QUERY, 5, configured_url=None, fetch=fetch)
    assert result["results"][0]["url"] == REPORT["url"]
    assert result["provider_attempts"][0]["code"] == "SEARCH_INVALID_RESPONSE"
    assert result["provider_attempts"][1]["status"] == "success"


def test_all_providers_preserve_domain_filter_and_each_failure_diagnostic(monkeypatch):
    query = "site:example.org -site:blocked.example.org " + QUERY
    navigation = {"url": "https://example.org/", "title": "Example Bank"}
    outside = {**REPORT, "url": "https://example.org.evil.test/2026.pdf"}
    excluded = {**REPORT, "url": "https://blocked.example.org/2026.pdf"}
    monkeypatch.setattr(backend, "configured_search", Mock(return_value=[navigation, outside]))
    monkeypatch.setattr(backend, "public_search_fallback", Mock(return_value=[excluded, outside]))
    registry = Mock()
    monkeypatch.setattr(backend, "kap_financial_search", registry)
    result = pipeline.run_search(query, 5, configured_url="http://localhost:8888", fetch=Mock(side_effect=OSError("Bing offline")))
    assert [r["url"] for r in result["results"]] == [navigation["url"]]
    assert result["code"] == "SEARCH_DISCOVERY_ONLY"
    assert result["recovery"]["arguments"]["domains"] == ["example.org"]
    assert [a["status"] for a in result["provider_attempts"]] == ["navigation_only", "unavailable", "empty"]
    assert result["provider_attempts"][2]["rejected_count"] == 2
    registry.assert_not_called()
    monkeypatch.setattr(backend, "kap_financial_search", Mock(side_effect=OSError("Registry offline")))
    monkeypatch.setattr(backend, "public_search_fallback", Mock(side_effect=ValueError("Alternate challenge")))
    result = pipeline.run_search(QUERY, 5, configured_url=None, fetch=Mock(return_value=rss([navigation])))
    assert [a["message"] for a in result["provider_attempts"] if "message" in a] == ["Registry offline", "Alternate challenge"]
    assert [w["provider"] for w in result["warnings"] if "provider" in w] == ["KAP Public Financial Registry", "DuckDuckGo Lite"]


def test_uncertain_report_does_not_hide_better_candidates(monkeypatch):
    other = {"url": "https://other.test/report.pdf", "title": "Other Bank March 2026 consolidated financial report"}
    monkeypatch.setattr(backend, "kap_financial_search", Mock(return_value=[]))
    monkeypatch.setattr(backend, "public_search_fallback", Mock(return_value=[REPORT]))
    result = pipeline.run_search(QUERY, 5, configured_url=None, fetch=Mock(return_value=rss([other])))
    assert result["provider_attempts"][0]["status"] == "issuer_unverified"
    assert result["results"][0]["url"] == REPORT["url"]
    assert next(r for r in result["results"] if r["url"] == other["url"])["entity_verification_required"]
    monkeypatch.setattr(backend, "public_search_fallback", Mock(return_value=[]))
    result = pipeline.run_search(QUERY, 5, configured_url=None, fetch=Mock(return_value=rss([other])))
    assert result["code"] == "SEARCH_ENTITY_UNVERIFIED"
    assert "domains" not in result["recovery"]["arguments"]


@pytest.mark.parametrize("bing_reason,ddg_reason", [("HTTP 403", "CAPTCHA"), ("Too many requests", "Suspended: too many requests")])
def test_reported_engine_block_is_not_retried_through_another_frontend(monkeypatch, bing_reason, ddg_reason):
    diagnostics = [{"engine": "bing", "reason": bing_reason}, {"engine": "duckduckgo", "reason": ddg_reason}]
    monkeypatch.setattr(backend, "configured_search", Mock(return_value={"results": [], "diagnostics": diagnostics}))
    registry = Mock(return_value=[])
    monkeypatch.setattr(backend, "kap_financial_search", registry)
    alternate = Mock()
    monkeypatch.setattr(backend, "public_search_fallback", alternate)
    public_fetch = Mock()
    result = pipeline.run_search(QUERY, 5, configured_url="http://localhost:8888", fetch=public_fetch)
    assert [a["status"] for a in result["provider_attempts"]] == ["empty", "skipped", "empty", "skipped"]
    assert result["provider_attempts"][0]["diagnostics"] == diagnostics
    registry.assert_called_once()
    public_fetch.assert_not_called()
    alternate.assert_not_called()


def test_nested_registry_requests_share_the_overall_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        clock[0] += 4
        return rss([])
    def registry(query, fetch):
        fetch("https://www.kap.org.tr/registry", timeout=15)
        fetch("https://www.kap.org.tr/disclosures", timeout=15)
        fetch("https://www.kap.org.tr/report", timeout=15)
        return []
    monkeypatch.setattr(backend, "kap_financial_search", registry)
    alternate = Mock()
    monkeypatch.setattr(backend, "public_search_fallback", alternate)
    result = pipeline.run_search(QUERY, 5, configured_url=None, fetch=fetch, deadline=110)
    assert [timeout for _, timeout in calls] == [10, 6, 2]
    assert len(calls) == 3  # No third registry request and no alternate index.
    assert result["budget_exhausted"]
    assert result["provider_attempts"][-1]["code"] == "SEARCH_TIMEOUT"
    alternate.assert_not_called()
    fetch = Mock()
    empty = pipeline.run_search(QUERY, 5, configured_url=None, fetch=fetch, deadline=clock[0])
    assert empty["code"] == "SEARCH_BUDGET_EXHAUSTED"
    fetch.assert_not_called()


@pytest.mark.parametrize("query,wrong,right", [
    ("Yapı Kredi 2026 konsolide finansal rapor", "Diğer Banka 2026 konsolide finansal rapor: kredi hacmi", "YAPI VE KREDİ BANKASI 2026 konsolide finansal rapor"),
    (QUERY, "Other Bank 2026 consolidated financial report", REPORT["title"]),
    ("New York Community Bank 2026 consolidated financial report", "Other York Community Bank 2026 consolidated financial report", "New York Community Bank 2026 consolidated financial report"),
    ("İş Bankası 31 Mart 2026 konsolide finansal rapor", "Diğer Bankası 2026 konsolide finansal rapor: İş ortakları", "Türkiye İş Bankası 31 Mart 2026 konsolide finansal rapor"),
])
def test_general_bank_words_never_certify_requested_name(query, wrong, right):
    candidates = [{"url": "https://other.test/2026.pdf", "title": wrong, "snippet": ""},
                  {"url": "https://issuer.test/2026.pdf", "title": right, "snippet": ""}]
    kept, _, _ = backend.rank_search_results(query, candidates)
    assert kept[0]["url"] == candidates[1]["url"]
    assert not kept[0]["entity_verification_required"]
    assert kept[1]["entity_verification_required"]


@pytest.mark.parametrize("query", ["KKB ortaklık yapısı bankalar", "Kredi Kayıt Bürosu ortaklık yapısı bankalar"])
def test_corporate_role_words_cannot_substitute_for_the_requested_institution(query):
    items = [{"url": "https://kgf.test/ortaklar", "title": "Kredi Garanti Fonu ortaklık yapısı bankalar", "snippet": ""},
             {"url": "https://kkb.test/ortaklar", "title": "KKB Kredi Kayıt Bürosu ortakları", "snippet": ""}]
    kept, rejected, _ = backend.rank_search_results(query, items)
    assert [item["url"] for item in kept] == [items[1]["url"]]
    assert rejected[0]["url"] == items[0]["url"]
    assert not kept[0]["entity_verification_required"]


def test_search_publication_dates_are_bounded_unverified_metadata(monkeypatch):
    monkeypatch.setattr(backend, "configured_search", Mock(return_value=[{**REPORT, "publishedDate": "2026-04-30" + "x" * 200}]))
    result = pipeline.run_search(QUERY, 5, configured_url="http://localhost:8888", fetch=Mock())
    assert len(result["results"][0]["published_at"]) == 100
    assert result["results"][0]["publication_date_basis"] == "search_metadata_unverified"
    assert not result["sources_verified"]


@pytest.mark.parametrize("query,conflict", [
    ("İş Bankası 31 Mart 2026 konsolide finansal rapor", "İş Bankası 31 Mart 2026 Konsolide Olmayan Finansal Rapor"),
    ("Example Bank March 2026 unconsolidated financial report", "Example Bank March 2026 consolidated financial report"),
])
def test_explicit_reporting_scope_conflicts_are_rejected_but_unspecified_candidates_remain(query, conflict):
    candidates = [{"url": "https://issuer.test/wrong.pdf", "title": conflict, "snippet": ""},
                  {"url": "https://issuer.test/reports", "title": "İş Bankası Example Bank 2026 financial report", "snippet": ""}]
    kept, rejected, _ = backend.rank_search_results(query, candidates)
    assert [r["url"] for r in kept] == [candidates[1]["url"]]
    assert rejected[0]["reason"] == "conflicting_consolidation_scope"


def test_research_search_variants_and_source_reads_use_one_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    docs = object.__new__(DocumentTools)
    searches, reads = [], []
    def search(query, **kwargs):
        searches.append(kwargs)
        clock[0] = kwargs["_deadline"]
        return {"status": "ok", "results": [], "provider_attempts": [{"provider": "Bing RSS", "status": "empty"}], "budget_exhausted": True}
    def inspect(**kwargs):
        reads.append(kwargs)
        return {"source_id": "root", "source_url": kwargs["url"], "text": "KKB root", "article": {}}
    docs.web_search, docs.inspect_source = search, inspect
    result = docs.research_web("KKB ortakları", limit=1)
    assert len(searches) == 1
    assert searches[0]["_deadline"] == 145
    assert reads[0]["_deadline"] == 220
    assert result["searches"][0]["provider_attempts"][0]["provider"] == "Bing RSS"


def test_configured_http_diagnostics_and_slow_body_are_bounded():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if "slow" in self.path:
                time.sleep(0.25)
            try:
                self.wfile.write(json.dumps({"results": [REPORT], "unresponsive_engines": [["bing", "HTTP connection error"], ["duckduckgo", "CAPTCHA"]]}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        result = backend.configured_search(url, "query", with_diagnostics=True)
        assert result["results"][0]["url"] == REPORT["url"]
        assert result["diagnostics"] == [{"engine": "bing", "reason": "HTTP connection error"}, {"engine": "duckduckgo", "reason": "CAPTCHA"}]
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            backend.configured_search(url, "slow", timeout=0.05)
        assert time.monotonic() - start < 0.2
        assert backend.read_bounded_response(io.BytesIO(b"abc"), max_bytes=3, deadline=time.monotonic() + 1) == b"abc"
        with pytest.raises(ValueError):
            backend.read_bounded_response(io.BytesIO(b"abcd"), max_bytes=3, deadline=time.monotonic() + 1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
