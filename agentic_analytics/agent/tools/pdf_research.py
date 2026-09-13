"""Select bounded, contiguous passages from already inspected PDF pages."""
from __future__ import annotations

import re
import unicodedata


def _fold(value):
    normalized = "".join(character for character in unicodedata.normalize(
        "NFKD", str(value).casefold().replace("ı", "i")) if not unicodedata.combining(character))
    return " ".join(normalized.split())


def pdf_title(metadata, pages, filename):
    title = metadata.get("title")
    if isinstance(title, str) and title.strip() and _fold(title) not in {"untitled", "document", "blank document"}:
        return {"title": " ".join(title.split())[:240], "title_basis": "pdf_metadata"}
    cover = next((page for page in pages if page.get("page") == 1), {})
    lines = [line.strip() for line in cover.get("text", "").splitlines() if line.strip()]
    if lines:
        return {"title": " ".join(lines[:4])[:240], "title_basis": "pdf_cover", "title_page": 1}
    return {"title": filename, "title_basis": "registered_filename"}


def pdf_passages(pages, topic_matches, *, limit=3, max_chars=3000):
    """Rank body pages above tables of contents, preserving source line order.

    Page matches are navigation, not evidence that a complete list or a whole
    document was read. Every shortened passage identifies its explicit bounds.
    """
    candidates = []
    for page in pages:
        lines = str(page.get("text", "")).splitlines()
        score = topic_matches("\n".join(lines))
        if not score:
            continue
        headings = [line.strip() for line in lines if 3 <= len(line.strip()) <= 120 and line.strip().isupper()]
        score += 2 * max((topic_matches(line) for line in headings), default=0)
        is_contents = any(re.fullmatch(r"(?:table of )?contents|icindekiler", _fold(line))
                          for line in lines[:8])
        if is_contents:
            score /= 4
        candidates.append((score, page, lines))
    candidates.sort(key=lambda item: (-item[0], item[1]["page"]))
    passages = []
    for _, page, lines in candidates[:limit]:
        start = 0
        if len("\n".join(lines)) > max_chars:
            strongest = max(range(len(lines)), key=lambda index: topic_matches(lines[index]))
            start = max(0, strongest - 2)
        chosen, size = [], 0
        for line in lines[start:]:
            cost = len(line) + bool(chosen)
            if size + cost > max_chars:
                if not chosen:
                    chosen.append(line[:max_chars])
                break
            chosen.append(line)
            size += cost
        text = "\n".join(chosen)
        passages.append({"page": page["page"], "text": text,
                         "line_start": start + 1, "line_end": start + len(chosen),
                         "content_truncated": start != 0 or text != "\n".join(lines),
                         "extraction_method": page.get("extraction_method", "pdf_text"),
                         "page_number_basis": "physical_pdf_1_based"})
    return passages
