"""Bounded access to an operator-configured SearXNG instance.

The endpoint is application configuration, never a model tool argument.
Public result URLs still use the source reader's DNS and redirect controls.
"""
import json
from html.parser import HTMLParser
import re
import unicodedata
from urllib import parse, request


def search_text(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value).casefold().replace("ı", "i"))
                   if not unicodedata.combining(char))


def search_domains(query):
    """Keep explicit site operators as constraints, even if a provider ignores them."""
    return list(dict.fromkeys(re.findall(r"(?<![\w-])site:([a-z0-9.-]+\.[a-z]{2,})(?:/[^\s]*)?", query.casefold())))


def rank_search_results(query, items):
    """Reject query drift and distinguish navigation leads from report evidence.

    This is a conservative retrieval filter, not factual source verification.
    Dates by themselves never make a result relevant. Institution pages remain
    available as navigation leads when the report is absent from the index.
    """
    domains = search_domains(query)
    excluded = re.findall(r"-site:([a-z0-9.-]+\.[a-z]{2,})", query.casefold())
    normalized = search_text(re.sub(r"-?(?:site|filetype):\S+", " ", query, flags=re.I))
    months = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
              "ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik"}
    topic = {"financial", "finansal", "report", "reports", "rapor", "raporu", "raporlar", "konsolide", "consolidated", "statements", "statement",
             "results", "sonuclar", "earnings", "assets", "aktif", "aktifler", "total", "toplam", "investor", "relations", "yatirimci", "iliskileri"}
    stop = months | topic | {"pdf", "xlsx", "csv", "the", "and", "for", "of", "in", "as", "at", "or", "ve", "ile", "icin", "bir", "public", "latest", "guncel", "com", "org", "gov", "www"}
    words = set(re.findall(r"[a-z][a-z0-9]+", normalized))
    distinctive = {word for word in words - stop if len(word) >= 3}
    short_names = [search_text(word) for word in re.findall(r"[^\W\d_]+", query)
                   if len(word) == 2 and any(ord(char) > 127 for char in word)]
    protected_phrases = [search_text(phrase) for phrase in re.findall(r'"([^"\d]+)"', query)
                         if len(phrase.split()) >= 2]
    # Keep short proper names attached to their neighbour. Matching "İş" and
    # "Bankası" anywhere in a 100-page report also matches unrelated banks.
    for short_name in short_names:
        protected_phrases.extend(re.findall(r"(?<!\w)" + re.escape(short_name) + r"\s+[a-z]+", normalized))
    financial_request = bool(words & topic)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", normalized))
    financial_pattern = r"financ|finans|konsolid|consolid|earnings|\breports?\b|\brapor\w*|\bstatements?\b|bilan[cç]|balance.sheet|total.assets|toplam.aktif|investor.relations|yatirimci.ilisk|mali.tablo"
    kept, rejected = [], []
    seen = set()
    for item in items:
        url = item["url"]
        target = parse.urlsplit(url)
        host = (target.hostname or "").casefold()
        domain_match = lambda domain: host == domain or host.endswith("." + domain)
        reason = None
        if domains and not any(domain_match(domain) for domain in domains) or any(domain_match(domain) for domain in excluded):
            reason = "outside_requested_domain"
        label = search_text(item.get("title", "") + " " + item.get("snippet", "") + " " + parse.unquote(url))
        matched = [word for word in distinctive if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", label)]
        entity_match = (not distinctive or bool(matched)) and (not short_names or all(
            re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", label) for word in short_names)) and all(
                phrase in " ".join(label.split()) for phrase in protected_phrases)
        # Search abstracts can omit the issuer entirely. A dated report is a
        # candidate worth opening, but the issuer must then be established in
        # the fetched document before research can return it as evidence.
        dated_report = (financial_request and bool(years.intersection(re.findall(r"\b(?:19|20)\d{2}\b", label)))
                        and bool(re.search(r"(?:konsolid|consolid|financ|finans).*(?:rapor|report|tabl|statement)|(?:rapor|report).*(?:konsolid|consolid|financ|finans)", label)))
        if reason is None and not entity_match and not dated_report:
            reason = "query_terms_missing"
        if reason:
            rejected.append({"url": url, "reason": reason})
            continue
        canonical = parse.urlunsplit((target.scheme, target.netloc, target.path.rstrip("/"), target.query, ""))
        if canonical in seen:
            continue
        seen.add(canonical)
        is_document = bool(re.search(r"\.(?:pdf|xlsx?|csv)(?:$|[?#])", url, re.I))
        on_topic = bool(re.search(financial_pattern, label)) or is_document
        root_page = not target.path.strip("/") or bool(re.fullmatch(r"/[a-z]{2}(?:-[a-z]{2})?/?", target.path))
        discovery = root_page or financial_request and not on_topic
        kept.append({**item, "discovery_only": discovery,
                     "entity_verification_required": not entity_match,
                     "relevance": "navigation_lead" if discovery else "query_match"})
    kept.sort(key=lambda item: (item["discovery_only"], -sum(year in item["title"] + item.get("snippet", "") for year in years)))
    return kept, rejected, domains


class _LiteSearchHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result-link" in attrs.get("class", "").split() and len(self.items) < 30:
            link = parse.urlsplit(parse.urljoin("https://lite.duckduckgo.com", attrs.get("href", "")))
            # Decode only the known redirect wrapper. Never visit a tracking URL.
            url = parse.parse_qs(link.query).get("uddg", [""])[0] if link.hostname == "duckduckgo.com" and link.path == "/l/" else parse.urlunsplit(link)
            self.items.append({"url": url, "title": "", "content": ""})
            self.capture = ("a", "title")
        elif tag == "td" and "result-snippet" in attrs.get("class", "").split() and self.items:
            self.capture = ("td", "content")

    def handle_data(self, value):
        if self.capture:
            field = self.capture[1]
            self.items[-1][field] = (self.items[-1][field] + value)[:2400]

    def handle_endtag(self, tag):
        if self.capture and tag == self.capture[0]:
            self.capture = None


def public_search_fallback(query, fetch):
    """One bounded, key-free alternate index; fetch retains public URL controls."""
    url = "https://lite.duckduckgo.com/lite/?" + parse.urlencode({"q": query})
    raw, _, _ = fetch(url, max_bytes=1024**2, timeout=15)
    parser = _LiteSearchHTML()
    parser.feed(raw.decode("utf-8", errors="replace"))
    if not parser.items:
        raise ValueError("Alternate search returned no readable result entries (possibly a challenge page).")
    return [{**item, "title": " ".join(item["title"].split()), "content": " ".join(item["content"].split())} for item in parser.items]


def _flight_objects(raw):
    """Read JSON already delivered in a React server response, without JS execution."""
    text = raw.decode("utf-8", errors="replace")
    decoder, frames = json.JSONDecoder(), []
    for match in re.finditer(r"self\.__next_f\.push\(", text):
        try:
            frame, _ = decoder.raw_decode(text, match.end())
            if isinstance(frame, list) and len(frame) == 2 and frame[0] == 1 and isinstance(frame[1], str):
                frames.append(frame[1])
        except (ValueError, RecursionError):
            continue
    pending = []
    for line in "".join(frames).splitlines():
        match = re.match(r"[0-9a-f]+:", line)
        if match:
            try:
                value, _ = decoder.raw_decode(line, match.end())
                pending.append(value)
            except (ValueError, RecursionError):
                continue
    visited = 0
    while pending and visited < 100000:
        value = pending.pop()
        visited += 1
        if isinstance(value, dict):
            yield value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def kap_financial_search(query, fetch):
    """Resolve issuer and reporting period against KAP's public source registry.

    Only platform routes are configured here. Issuer identifiers, disclosure
    identifiers and attachment identifiers must all come from fetched records.
    Returned links are discovery candidates; actual financial facts still go
    through the ordinary source reader and publication contract.
    """
    normalized = search_text(query)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", normalized))
    if len(years) != 1 or not re.search(r"financ|finans|konsolid|consolid|bilan[cç]|financial.report", normalized):
        return []
    if search_domains(query):
        return []  # An explicit site restriction is never silently widened.
    year = int(next(iter(years)))
    months = {
        "ocak": 1, "january": 1, "subat": 2, "february": 2, "mart": 3, "march": 3,
        "nisan": 4, "april": 4, "mayis": 5, "may": 5, "haziran": 6, "june": 6,
        "temmuz": 7, "july": 7, "agustos": 8, "august": 8, "eylul": 9, "september": 9,
        "ekim": 10, "october": 10, "kasim": 11, "november": 11, "aralik": 12, "december": 12}
    selected_months = {number for word, number in months.items() if re.search(r"\b" + word + r"\b", normalized)}
    for day, month, observed_year in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})\b", normalized):
        if int(observed_year) == year:
            selected_months.add(int(month))
    if not selected_months:
        quarter = re.search(r"(?:\bq([1-4])\b|\b([1-4])\.?\s*(?:ceyrek|quarter))", normalized)
        if quarter:
            selected_months.add(int(quarter.group(1) or quarter.group(2)) * 3)
        elif re.search(r"\bilk\s+ceyrek\b|\bfirst\s+quarter\b", normalized):
            selected_months.add(3)
    if len(selected_months) > 1:
        return []
    requested_month = next(iter(selected_months), None)
    root = "https://www.kap.org.tr"
    registry_url = root + "/tr/bist-sirketler"
    raw, _, final = fetch(registry_url, max_bytes=4 * 1024**2, timeout=15)
    if (parse.urlsplit(final).hostname or "").removeprefix("www.") != "kap.org.tr":
        raise ValueError("The KAP registry redirected outside its official domain.")
    companies = {}
    for value in _flight_objects(raw):
        title, oid = value.get("kapMemberTitle"), value.get("mkkMemberOid")
        if not isinstance(title, str) or not isinstance(oid, str) or not re.fullmatch(r"[a-fA-F0-9]{24,40}", oid):
            continue
        candidate = {"title": title, "url": registry_url, "snippet": str(value.get("stockCode", ""))}
        matches, _, _ = rank_search_results(query, [candidate])
        if matches and not matches[0]["entity_verification_required"]:
            companies[oid] = title
    if len(companies) != 1:
        return []  # No unique issuer identity; never pick the first similar bank.
    oid, issuer = next(iter(companies.items()))
    disclosures_url = root + "/tr/bildirim-sorgu-sonuc?" + parse.urlencode({"member": oid, "disclosureClass": "FR"})
    raw, _, final = fetch(disclosures_url, max_bytes=4 * 1024**2, timeout=15)
    if (parse.urlsplit(final).hostname or "").removeprefix("www.") != "kap.org.tr":
        raise ValueError("The KAP disclosure list redirected outside its official domain.")
    disclosures = {}
    for value in _flight_objects(raw):
        basic = value.get("disclosureBasic", {})
        if not isinstance(basic, dict) or basic.get("year") != year or basic.get("disclosureClass") != "FR":
            continue
        if search_text(basic.get("companyTitle", "")) != search_text(issuer):
            continue
        title = search_text(basic.get("title", ""))
        if title not in {"finansal rapor", "financial report"}:
            continue
        period = search_text(basic.get("donem", ""))
        month = 12 if period in {"yillik", "annual"} else int(match.group(1)) if (match := re.match(r"(3|6|9|12)\s", period)) else None
        if requested_month and month != requested_month:
            continue
        index = basic.get("disclosureIndex")
        if isinstance(index, int) and index > 0:
            disclosures[index] = basic
    results = []
    wants_unconsolidated = bool(re.search(r"konsolide\s+olmayan|unconsolidated|bank.only|standalone|solo", normalized))
    wants_consolidated = not wants_unconsolidated and bool(re.search(r"konsolid|consolid", normalized))
    for index in sorted(disclosures, reverse=True)[:4]:
        detail_url = root + f"/tr/Bildirim/{index}"
        raw, _, final = fetch(detail_url, max_bytes=4 * 1024**2, timeout=15)
        if (parse.urlsplit(final).hostname or "").removeprefix("www.") != "kap.org.tr":
            raise ValueError("The KAP report redirected outside its official domain.")
        for value in _flight_objects(raw):
            attachments = value.get("attachments", [])
            if not isinstance(attachments, list):
                continue
            for attachment in attachments[:10]:
                if not isinstance(attachment, dict):
                    continue
                file_id, name = attachment.get("objId"), attachment.get("fileName", "")
                if not isinstance(file_id, str) or not re.fullmatch(r"[a-fA-F0-9]{24,40}", file_id) or not isinstance(name, str) or not name.lower().endswith(".pdf"):
                    continue
                label = search_text(name)
                unconsolidated = bool(re.search(r"konsolide[ _-]*olmayan|unconsolidated|bank.only|standalone", label))
                consolidated = bool(re.search(r"konsolid|consolid|cons[_.-]", label)) and not unconsolidated
                if wants_consolidated and not consolidated or wants_unconsolidated and not unconsolidated:
                    continue
                results.append({"title": issuer + " | " + name, "url": root + "/tr/api/file/download/" + file_id,
                                "content": f"{issuer}. {year}, {disclosures[index].get('donem', '')}. {name}",
                                "discovered_from": detail_url,
                                "registry_evidence": {"registry_url": registry_url, "issuer_id": oid,
                                    "disclosures_url": disclosures_url, "disclosure_index": index,
                                    "reporting_year": year, "reporting_period": disclosures[index].get("donem")}})
        if results:
            break
    return list({item["url"]: item for item in results}.values())[:4]


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
