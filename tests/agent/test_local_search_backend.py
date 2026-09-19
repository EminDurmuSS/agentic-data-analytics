"""Operator-selected local search works without relaxing source URL controls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from agentic_analytics.agent.tools.documents import DocumentError, _public_destination
from agentic_analytics.agent.tools.search_backend import configured_search


def test_local_search_uses_only_configured_endpoint_and_does_not_follow_redirects():
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if parse_qs(urlsplit(self.path).query).get("q") == ["redirect"]:
                self.send_response(302)
                self.send_header("Location", "/unapproved")
                self.end_headers()
                return
            data = json.dumps({"results": [{"title": "Financial report", "url": "https://example.org/report.pdf"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = configured_search(url, "kredi & mevduat")
        assert result[0]["url"] == "https://example.org/report.pdf"
        assert parse_qs(urlsplit(paths[0]).query) == {"q": ["kredi & mevduat"], "format": ["json"]}
        with pytest.raises(OSError):
            configured_search(url, "redirect")
        assert len(paths) == 2
        with pytest.raises(DocumentError):
            _public_destination(url)
        with pytest.raises(ValueError):
            configured_search(url + "?target=other", "query")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _server_payload(value):
    frame = [1, 'a:' + json.dumps(value, ensure_ascii=False) + '\n']
    return ('<script>self.__next_f.push(' + json.dumps(frame, ensure_ascii=False) + ')</script>').encode()


def test_public_financial_registry_resolves_identifiers_and_preserves_scope_from_source():
    from agentic_analytics.agent.tools.search_backend import kap_financial_search
    issuer_id, asset_id = '1' * 32, '2' * 32
    root = 'https://www.kap.org.tr'
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['max_bytes'] <= 4 * 1024**2
        assert kwargs['timeout'] == 15
        if url.endswith('/bist-sirketler'):
            content = [{'kapMemberTitle': 'ACME BANK A.Ş.', 'mkkMemberOid': issuer_id},
                       {'kapMemberTitle': 'OTHER COMPANY', 'mkkMemberOid': '3' * 32}]
        elif 'bildirim-sorgu-sonuc?' in url:
            assert parse_qs(urlsplit(url).query) == {'member': [issuer_id], 'disclosureClass': ['FR']}
            content = [{'disclosureBasic': {'disclosureIndex': index, 'companyTitle': 'ACME BANK A.Ş.',
                        'year': year, 'donem': period, 'disclosureClass': 'FR', 'title': 'Finansal Rapor'}}
                       for index, year, period in [(900, 2026, '3 Aylık'), (901, 2026, '6 Aylık'), (800, 2025, 'Yıllık')]]
        elif url.endswith('/Bildirim/900'):
            content = {'attachments': [{'objId': '4' * 32, 'fileName': 'Acme_2026_Unconsolidated.pdf'},
                                       {'objId': asset_id, 'fileName': 'Acme_2026_Consolidated.pdf'}]}
        else:
            raise AssertionError('Unexpected source URL: ' + url)
        return _server_payload(content), 'text/html', url
    results = kap_financial_search('Acme 31 March 2026 consolidated financial report PDF', fetch)
    assert len(calls) == 3
    assert [item['url'] for item in results] == [root + '/tr/api/file/download/' + asset_id]
    assert results[0]['discovered_from'] == root + '/tr/Bildirim/900'
    assert results[0]['registry_evidence']['issuer_id'] == issuer_id
    assert results[0]['registry_evidence']['reporting_period'] == '3 Aylık'


def test_financial_registry_does_not_guess_ambiguous_issuers_or_widen_sites():
    from agentic_analytics.agent.tools.search_backend import kap_financial_search, kap_search_applicable
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return _server_payload([{'kapMemberTitle': name, 'mkkMemberOid': str(i) * 32}
                                for i, name in enumerate(['ACME BANK', 'ACME HOLDING'], 1)]), 'text/html', url
    assert kap_financial_search('Acme March 2026 consolidated financial report', fetch) == []
    assert len(calls) == 1
    assert kap_financial_search('site:acme.test Acme March 2026 financial report', fetch) == []
    assert kap_financial_search('Acme financial report', fetch) == []
    assert len(calls) == 1
    assert kap_search_applicable('site:kap.org.tr Acme March 2026 financial report')
    assert kap_search_applicable('site:www.kap.org.tr Acme March 2026 financial report')
    assert not kap_search_applicable('site:kap.org.tr site:example.org Acme March 2026 financial report')


def test_dated_issuer_search_keeps_short_name_together_and_rejects_date_only_drift():
    from agentic_analytics.agent.tools.search_backend import rank_search_results
    query = 'İş Bankası 31 Mart 2026 konsolide finansal rapor PDF'
    results, rejected, _ = rank_search_results(query, [
        {'url': 'https://jobs.test/', 'title': 'İş ilanları', 'snippet': 'Bankası iş fırsatları'},
        {'url': 'https://other.test/report.pdf', 'title': 'Diğer Bankası 31 Mart 2026 konsolide finansal rapor', 'snippet': 'İş ortaklıkları'},
        {'url': 'https://number.test/31', 'title': '31 anlamı', 'snippet': '31 sayısı'},
        {'url': 'https://issuer.test/financials', 'title': 'Türkiye İş Bankası 31 Mart 2026 konsolide finansal rapor', 'snippet': ''}])
    assert len(rejected) == 2
    assert next(item for item in results if 'other.test' in item['url'])['entity_verification_required']
    assert not next(item for item in results if 'issuer.test' in item['url'])['entity_verification_required']


def test_server_json_is_read_as_data_and_never_executed():
    from agentic_analytics.agent.tools.search_backend import _flight_objects
    raw = b'<script>self.__next_f.push(invalid);window.location="http://localhost";</script>'
    raw += _server_payload({'attachments': [{'objId': 'a' * 32, 'fileName': 'Report.pdf'}]})
    assert {'objId': 'a' * 32, 'fileName': 'Report.pdf'} in list(_flight_objects(raw))
