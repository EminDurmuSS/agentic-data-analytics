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
