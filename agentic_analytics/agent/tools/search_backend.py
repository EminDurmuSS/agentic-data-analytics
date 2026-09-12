"""Bounded access to an operator-configured SearXNG instance.

The endpoint is application configuration, never a model tool argument.
Public result URLs still use the source reader's DNS and redirect controls.
"""
import json
from urllib import parse, request


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configured_search(base_url, query):
    if not isinstance(base_url, str) or not 1 <= len(base_url) <= 4096 or any(ord(char) < 32 for char in base_url):
        raise ValueError("Invalid configured SearXNG URL.")
    endpoint = parse.urlsplit(base_url)
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError("SearXNG configuration must be an HTTP(S) base URL without credentials, query or fragment.")
    if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
        raise ValueError("Invalid configured SearXNG port.")
    url = base_url.rstrip("/") + "/search?" + parse.urlencode({"q": query, "format": "json"})
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    query_request = request.Request(url, headers={"User-Agent": "AgenticMinds-SourceReader/1", "Accept": "application/json", "Accept-Encoding": "identity"})
    with opener.open(query_request, timeout=15) as response:
        size = response.headers.get("Content-Length")
        if size and (not size.isdecimal() or int(size) > 1024**2):
            raise ValueError("Configured search response exceeds its size limit.")
        raw = response.read(1024**2 + 1)
    if len(raw) > 1024**2:
        raise ValueError("Configured search response exceeds its size limit.")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Configured search did not return a JSON results array.")
    return payload["results"]
