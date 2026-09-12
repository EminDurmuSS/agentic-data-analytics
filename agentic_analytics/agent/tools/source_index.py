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
                normalized = _normalized(page_text)
                found = [term for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized)]
                exact = phrase in normalized
                if not exact and len(found) < max(1, (len(terms) + 1) // 2):
                    continue
                # Excerpts consist only of literal source lines. A table of
                # contents remains a valid match, never an asserted value.
                lines = page_text.splitlines()
                positions = [i for i, line in enumerate(lines) if phrase in _normalized(line)]
                if not positions:
                    positions = sorted(range(len(lines)), key=lambda i: sum(term in _normalized(lines[i]) for term in found), reverse=True)[:2]
                windows = set()
                for position in positions[:3]:
                    windows.update(range(max(0, position - 2), min(len(lines), position + 7)))
                excerpt = "\n".join(lines[i] for i in sorted(windows))[:2400]
                matches.append({"page": index + 1, "matched_terms": found, "exact_phrase": exact,
                                "excerpt": excerpt, "score": 10 * int(exact) + len(found) / len(terms)})
        matches.sort(key=lambda item: (-item["score"], item["page"]))
        last = processed[-1] if processed else start_page - 1
        return {"status": "ok", "source_id": source_id, "raw_sha256": source["raw_sha256"],
                "query": query, "total_pages": total_pages, "searched_pages": processed,
                "complete": start_page == 1 and last == total_pages and not empty,
                "next_start_page": last + 1 if last < total_pages else None,
                "image_only_pages": empty, "matches": matches[:limit], "total_matches": len(matches),
                "content_is_untrusted_data": True,
                "warnings": ([{"code": "SOURCE_TEXT_SEARCH_INCOMPLETE", "message": "Some pages remain unsearched or contain no extractable text. A missing match does not prove absence."}]
                             if start_page != 1 or last < total_pages or empty else []),
                "next_step": "Inspect matching 1-based pages using inspect_source(source_id, page_numbers=[...]). Use table_strategy=text if border extraction merges cells. Search excerpts identify pages, not published analytical values."}

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
