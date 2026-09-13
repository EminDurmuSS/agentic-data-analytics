"""Locate source-owned text on PDF pages before bounded table extraction."""
from __future__ import annotations

import io
import re
import time
import unicodedata

import pdfplumber

from agentic_analytics.agent.schemas import obj
from agentic_analytics.agent.tools.documents import DocumentError, DocumentTools


def _normalized(text):
    return " ".join("".join(character for character in unicodedata.normalize(
        "NFKD", text.casefold().replace("ı", "i")) if not unicodedata.combining(character)).split())


def _term_key(word):
    """Conservative word forms for retrieval, never replacement source text."""
    word = _normalized(word)
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    # Adjectival forms and common heading variants, not issuer-specific terms.
    return {"sectoral": "sector", "sektorel": "sektor", "breakdown": "distribution"}.get(word, word)


def _line_terms(line):
    result = {}
    for word in re.findall(r"\w+", line):
        result.setdefault(_term_key(word), set()).add(word)
    return result


def _heading(line):
    value = line.strip()
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", value)
    # Numeric table rows and long prose are not headings. A section number is
    # useful structure; values in its adjacent rows remain excerpt data only.
    if (not words or len(value) > 160 or len(words) > 18 or len(re.findall(r"\d[\d,.]*", value)) > 2
            or re.search(r"\d[.,]\d{3}(?:[.,]\d{3})*", value)):
        return False
    return (bool(re.match(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+", value))
            or value.isupper() or len(words) <= 12 and not value.endswith((".", ";", ",")))


def _page_match(page_text, phrase, terms):
    """Prefer co-occurring heading terms over words scattered across a page."""
    lines = page_text.splitlines()
    token_lines = [_line_terms(line) for line in lines]
    keys = {_term_key(term) for term in terms}
    page_keys = set().union(*(set(tokens) for tokens in token_lines))
    found_keys = keys & page_keys
    exact = phrase in _normalized(page_text)
    if not exact and len(found_keys) < max(1, (len(keys) + 1) // 2):
        return None
    best = None
    for index, tokens in enumerate(token_lines):
        line_found = keys & set(tokens)
        if not line_found:
            continue
        # Three adjacent lines support wrapped headings and short explanations;
        # text elsewhere on the page does not receive this proximity score.
        nearby = keys & set().union(*(set(part) for part in token_lines[index:index + 3]))
        heading_found = line_found if _heading(lines[index]) else set()
        line_exact = phrase in _normalized(lines[index])
        rank = (len(nearby), len(heading_found), len(line_found), line_exact)
        if best is None or rank > best[0]:
            best = (rank, index, nearby, heading_found, line_found)
    if best is None:
        return None
    _, position, nearby, heading_found, line_found = best
    first, last = max(0, position - 2), min(len(lines), position + 9)
    excerpt = "\n".join(lines[first:last])
    # A single contiguous excerpt retains the actual section boundaries. Do
    # not stitch unrelated paragraphs into an apparent table heading.
    source_terms = {}
    for tokens in token_lines:
        for key in found_keys & set(tokens):
            source_terms.setdefault(key, set()).update(tokens[key])
    coverage = len(nearby) / len(keys)
    score = (80 * coverage + 10 * len(found_keys) / len(keys) + 15 * len(heading_found) / len(keys)
             + 5 * len(line_found) / len(keys) + 10 * int(exact))
    contents = bool(re.search(r"\b(?:table of contents|contents|icindekiler)\b|section\s+one\s+page\s+no",
                              _normalized("\n".join(lines[:8]))))
    if contents:
        score *= 0.65  # Keep the index as navigation, after the actual section.
    shown_excerpt = excerpt[:2400]
    return {"matched_terms": [term for term in terms if _term_key(term) in found_keys],
            "local_matched_terms": [term for term in terms if _term_key(term) in nearby],
            "term_variants": {term: sorted(source_terms[_term_key(term)])[:8] for term in terms if _term_key(term) in source_terms},
            "exact_phrase": exact, "all_query_terms": found_keys == keys,
            "match_type": ("contents" if contents else "heading" if heading_found == keys else "local_context" if nearby == keys else
                           "scattered_terms" if found_keys == keys else "partial_terms"),
            "excerpt": shown_excerpt, "excerpt_truncated": len(excerpt) > len(shown_excerpt),
            "line_start": first + 1, "line_end": first + len(shown_excerpt.splitlines()), "score": round(score, 6)}


class SourceIndexTools:
    def __init__(self, store, workspace_id):
        self.documents = DocumentTools(store, workspace_id)

    def find_source_pages(self, source_id, query, limit=6, start_page=1, page_limit=250):
        if (not isinstance(query, str) or not 2 <= len(query.strip()) <= 300
                or type(limit) is not int or not 1 <= limit <= 10
                or type(start_page) is not int or start_page < 1
                or type(page_limit) is not int or not 1 <= page_limit <= 1000):
            raise DocumentError("Provide a source phrase and bounded page search parameters.", "INVALID_SOURCE_SEARCH")
        source = self.documents.source(source_id)
        if source["mime_type"] != "application/pdf" and not source["filename"].lower().endswith(".pdf"):
            raise DocumentError("Page search applies to PDFs; inspect_source reads other source formats.", "SOURCE_SEARCH_FORMAT")
        raw = self.documents.raw_source_bytes(source_id)
        phrase = _normalized(query)
        terms = list(dict.fromkeys(re.findall(r"[\w]+", phrase)))
        terms = [term for term in terms if len(term) > 2 and term not in {"and", "the", "for", "ile", "veya"}]
        if not terms:
            raise DocumentError("Search for a heading or a meaningful source term.", "INVALID_SOURCE_SEARCH")
        matches, empty, processed = [], [], []
        deadline = time.monotonic() + 30
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            total_pages = len(pdf.pages)
            if start_page > total_pages:
                raise DocumentError("Search starts after the last PDF page.", "INVALID_PAGE_SELECTION")
            for index in range(start_page - 1, min(total_pages, start_page - 1 + page_limit)):
                if time.monotonic() >= deadline:
                    break
                page_text = pdf.pages[index].extract_text() or ""
                processed.append(index + 1)
                if not page_text.strip():
                    empty.append(index + 1)
                    continue
                match = _page_match(page_text, phrase, terms)
                if match:
                    matches.append({"page": index + 1, **match})
        matches.sort(key=lambda item: (-item["score"], item["page"]))
        last = processed[-1] if processed else start_page - 1
        return {"status": "ok", "source_id": source_id, "raw_sha256": source["raw_sha256"],
                "query": query, "total_pages": total_pages, "searched_pages": processed,
                "complete": start_page == 1 and last == total_pages and not empty,
                "next_start_page": last + 1 if last < total_pages else None,
                "image_only_pages": empty, "matches": matches[:limit], "total_matches": len(matches),
                "page_number_basis": "physical_pdf_1_based",
                "suggested_inspection": {"source_id": source_id, "page_numbers": [match["page"] for match in matches[:min(limit, 3)]]},
                **({"recovery": {"tool": "inspect_source", "arguments": {
                    "source_id": source_id, "page_numbers": [match["page"] for match in matches[:min(limit, 3)]]}}} if matches else {}),
                "content_is_untrusted_data": True,
                "warnings": ([{"code": "SOURCE_TEXT_SEARCH_INCOMPLETE", "message": "Some pages remain unsearched or contain no extractable text. A missing match does not prove absence."}]
                             if start_page != 1 or last < total_pages or empty else []),
                "next_step": "Inspect the suggested physical 1-based pages before another query variant. Partial or scattered matches may describe a different topic; matching words do not establish the requested table or its scope. Use next_start_page to continue an incomplete page window. Use table_strategy=text if border extraction merges cells. Search excerpts identify pages, not published analytical values."}

    def extra_tools(self):
        def handler(arguments):
            try:
                return self.find_source_pages(**arguments)
            except (DocumentError, OSError, ValueError, TypeError) as exc:
                return {"status": "blocked", "code": getattr(exc, "code", "SOURCE_SEARCH_FAILED"), "message": str(exc)}
        return {"find_source_pages": {"schema": {"type": "function", "function": {
            "name": "find_source_pages", "description": "Search the text of a stored PDF for headings, dates or metric names, returning actual page numbers and excerpts. Locates financial notes beyond the first30 pages. This does not OCR image-only pages or invent table cells.",
            "parameters": obj({"source_id": {"type": "string"}, "query": {"type": "string", "minLength": 2, "maxLength": 300},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "start_page": {"type": "integer", "minimum": 1}, "page_limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
                ["source_id", "query"])}}, "handler": handler}}
