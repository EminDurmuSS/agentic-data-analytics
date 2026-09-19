"""One bounded decision-maker over typed, durable analytics tools.

The LLM does not receive a database connection, arbitrary SQL, local filesystem
paths or a shell. Tool results are journaled before advancing the conversation.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import json
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

import duckdb
import jsonschema

from agentic_analytics.agent.context import _compact, _model_tool_result, model_messages, workspace_context
from agentic_analytics.agent.prompts import INSTITUTIONAL_REPAIR_PROMPT, SOURCE_READING_PROMPT, SOURCE_READ_REPAIR_PROMPT
from agentic_analytics.agent.delivery import (
    _analysis_confirmation, _cell_confirmation, _chart_confirmation, _requests_chart, _requests_table,
    _scope_confirmation, _source_scope_confirmation, _statistics_confirmation, _selection_confirmation,
    _bundle_confirmation, _display_label, _source_confirmation, _published_source_ids, _successful_bundle,
    _verified_source_table_confirmation,
)
from agentic_analytics.agent.run_store import AgentRunStore, canonical, fingerprint
from agentic_analytics.agent.schemas import COLUMN_NAME, obj
from agentic_analytics.agent.tools.documents import _consolidation_scope, _document_type, _requested_document_type
from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
from agentic_analytics.lakehouse.discovery import initial_query
from agentic_analytics.lakehouse.service import LakehouseService, PlanError, error_envelope
from agentic_analytics.providers.mia import MiaError


def _blocked(code, message):
    return {"status": "blocked", "errors": [{"code": code, "message": message}]}


def _successful_selection(state):
    return any(item.get("tool") == "select_analysis_rows"
               and item.get("result", {}).get("status") == "ok"
               and item.get("result", {}).get("artifact_id")
               for item in state.get("tool_results", []))


def _requests_relationship_statistics(message):
    text = _fact_text(message)
    return bool(re.search(
        r"\b(?:korelasyon\w*|correlation\w*|pearson\w*|spearman\w*|granger\w*|"
        r"iliski\w*|relationship\w*|association\w*|regresyon\w*|regression\w*|"
        r"gecikmeli\s+(?:iliski\w*|baglanti\w*)|lagged\s+(?:relationship|association))\b",
        text,
    ))


_SEARCH_FAILURES = {"SEARCH_NO_PROGRESS", "SEARCH_STRATEGY_EXHAUSTED", "SEARCH_UNAVAILABLE",
                    "WEB_SEARCH_UNCONFIGURED", "SEARCH_INVALID_RESPONSE", "NO_READABLE_SOURCES",
                    "OFFICIAL_SOURCE_NOT_FOUND", "SEARCH_NO_RELEVANT_RESULTS", "SEARCH_BUDGET_EXHAUSTED"}
_RECOVERABLE_SEARCH_ERRORS = _SEARCH_FAILURES | {"INVALID_TOOL_ARGUMENTS", "RESEARCH_QUERY_SCOPE_MISMATCH"}
_INSTITUTIONAL_REPAIR_TOOLS = {"research_web", "web_search", "inspect_source", "find_source_pages",
                               "read_source_table", "find_source_table_rows", "describe"}


def _fact_text(value):
    return str(value or "").casefold().replace("ı", "i").replace("i\u0307", "i")


def _ownership_subject(message):
    # Only explicit possessive names/acronyms are reliable here. Ambiguous
    # references remain for the model to resolve, rather than guessing an entity.
    match = re.search(r"\b([\w&.-]+)(?:['’]\s*|\s+)(?:nin|in|nun|un|s)\s+", _fact_text(message))
    return match.group(1) if match and match.group(1) not in {"bu", "onun", "şirket", "kurum", "company"} else None


def _ownership_source(result, subject=None, relationship=False):
    if result.get("status") != "ok":
        return False
    sources = result.get("sources") if isinstance(result.get("sources"), list) else [result]
    for source in sources:
        if not isinstance(source, dict):
            continue
        article = source.get("article") or {}
        # Read text and table cells only; search snippets and navigation links
        # mentioning shareholders do not establish an ownership statement.
        text = " ".join(str(value or "") for value in [source.get("content"), source.get("text"),
            article.get("article_body"), *[page.get("text") for page in source.get("pages", []) if isinstance(page, dict)],
            *[passage.get("text") for passage in source.get("passages", []) if isinstance(passage, dict)],
            *[canonical(table) for table in source.get("tables", []) if isinstance(table, dict)],
            canonical({key: source[key] for key in ("columns", "original_columns", "rows") if key in source})])
        text = _fact_text(text)
        roles = r"ortaklar\w*|ortaklik\s+yap\w*|hissedar\w*|pay\s+sahip\w*|shareholders?|shareholding|ownership|owned\s+by"
        if relationship:
            roles += r"|kurucu\w*|kuruluş\w*|kurulan|kuruldu\w*|üye(?:ler\w*|si|lik\w*)?|iş\s*birliği\w*|founded|established|founders?|members?|partners?"
        if not re.search(r"\b(?:" + roles + r")\b", text):
            continue
        identity = text + " " + _fact_text(source.get("title")) + " " + _fact_text(article.get("title"))
        try:
            hostname = urlsplit(source.get("url") or source.get("source_url") or "").hostname or ""
            identity += " " + " ".join(hostname.split(".")[:-1])
        except ValueError:
            pass
        if not subject or re.search(r"(?<!\w)" + re.escape(subject) + r"(?!\w)", identity):
            return True
    return False


def _permission_to_research(question):
    text = _fact_text(question)
    return bool(re.search(r"kontrol|teyit|doğrula|araştir|incele|verify|check|research", text)
                and re.search(r"ister\s+mi|edeyim\s+mi|yapayim\s+mi|onay|should\s+i|would\s+you\s+like|shall\s+i", text))


def _external_research_forbidden(messages):
    """Respect explicit user source restrictions, including earlier user turns."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _fact_text(message.get("content"))
        if (re.search(r"\b(?:internet\w*|web\w*)\s+(?:kullanma|arama|araştirma|çikma|bağlanma)\b|"
                      r"\b(?:internet|web|diş kaynak)\s+araştirmasi\s+yapma|"
                      r"\b(?:no\s+(?:internet|web)|do\s+not\s+(?:browse|search\s+(?:the\s+)?(?:web|internet)))\b", text)
                or re.search(r"\b(?:yalnizca|sadece|only)\b.{0,65}(?:yükle\w*|uploaded|local\s+files?|yerel\s+dosya)", text)):
            return True
        if re.search(r"\b(?:internette|webde|web'de)\s+ara|\binterneti\s+kullan\b|"
                     r"\bweb\s+ara(?:ma|ştirma)si\s+yap\b|"
                     r"\b(?:search|browse)\s+(?:the\s+)?(?:web|internet)\b", text):
            return False
    return False


def _source_permission_question(question):
    text = _fact_text(question)
    # Keep actual entity, period, consolidation and business choices available.
    if re.search(r"\bhangi\s+(?:banka|şirket|kurum|dönem|tarih|kapsam|para birimi)|"
                 r"\b(?:konsolide|solo|bireysel)\s+mi\b|\bwhich\s+(?:company|bank|period|scope|currency)\b", text):
        return False
    if not re.search(r"kaynak|rapor|resmi|source|report|official", text):
        return False
    return bool(_permission_to_research(question)
                or re.search(r"hangi\s+kaynaktan|which\s+source", text)
                or re.search(r"kullan(?:ayim|mam)i?\s+mi|kullanmami\s+ister|may\s+i\s+use", text)
                or (re.search(r"url|link|bağlanti|dosya|file", text)
                    and re.search(r"paylaş|yükle|gönder|provide|upload|share", text)))


def _ownership_question(message):
    """Identify concrete institutional ownership questions, not general lessons."""
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    if re.fullmatch(r"\s*(?:(?:what is|explain)\s+(?:the\s+)?(?:ownership structure|shareholding|shareholders?)|"
                    r"(?:ortaklik yapisi|hissedarlik)\s+(?:nedir|ne demek|açikla))(?:[?.!\s]*)", text):
        return False
    if re.search(r"(?:ortaklik|hissedarlik|ownership|shareholding)\s+(?:(?:yapisi|structure)\s+)?(?:nedir|ne demek|ne anlama|means|meaning)", text):
        # A possessive named entity makes the same wording a factual question.
        if not re.search(r"(?:['’](?:nin|in|un)|\b\w+(?:nin|nın)\b).{0,20}(?:ortaklik|hissedarlik)", text):
            return False
    return bool(re.search(r"\b(?:ortaklari\w*|hissedarlari\w*|shareholders?\b|owners?\b|owned\s+by\b|ownership\s+structure\b)", text)
                or re.search(r"\b(?:ortaklik|hissedarlik)\s+yapisi\b", text))


def _institutional_fact_kind(message):
    if _ownership_question(message):
        return "ownership"
    text = _fact_text(message)
    # Personal recommendations and general lessons do not assert a named
    # institution's historical or current relationships.
    if re.search(r"çalişmali(?:yim|yiz)|çaliş(?:ayim|alim)|which\s+.+should\s+(?:i|we)\s+(?:work|partner)|"
                 r"hangi\s+(?:banka|kurum)\w*.{0,35}(?:önerirsin|tavsiye\s+edersin)", text):
        return None
    if re.fullmatch(r"\s*(?:(?:kuruculuk|kuruluş|üyelik)(?:\s+(?:nedir|ne demek|anlat|açikla))|"
                    r"(?:what is|explain|describe)\s+(?:founding|membership|a founder))(?:[?.!\s]*)", text):
        return None
    asking = re.search(r"\b(?:hangi\w*|kim\w*|who|which|listele\w*|anlat\w*|list|describe)\b", text)
    relationship = re.search(r"\b(?:kurucu\w*|kuruluş\w*|kurul\w*|üye\w*|founders?|founded|established|members?|partners?)\b", text)
    working = re.search(r"çaliş\w*|başla\w*|iş\s*birli\w*|work\w*\s+with|start\w*\s+with", text)
    institutions = re.search(r"\b(?:banka\w*|kurum\w*|şirket\w*|kimlerle|banks?|institutions?|companies|who)\b", text)
    return "relationship" if asking and (relationship or working and institutions) else None


def _institutional_followup_kind(message):
    """Only naming/format continuations, never arbitrary short commands."""
    text = _fact_text(message).strip(" .?!,;\n")
    if len(text) > 240 or _ownership_subject(message):
        return None
    if re.fullmatch(r"(?:(?:peki|tamam|evet)\s+)?hangi\s+(?:banka|kurum)\w*\s+(?:ortak|hissedar)", text):
        return "ownership"
    words = re.findall(r"\w+", text)
    allowed = r"tamam|peki|evet|bana|bun\w*|onlar\w*|hangi|kimler\w*|banka\w*|kurum\w*|liste\w*|olarak|ver\w*|göster\w*|yaz\w*|bir|lütfen|please|okay|ok|list|them|these|those|which|ones|banks|companies|show|give|me"
    if (words and all(re.fullmatch(allowed, word) for word in words)
            and any(re.fullmatch(r"liste\w*|list", word) for word in words)):
        return "continue"
    return None


def _requests_ownership_percentages(message):
    text = re.sub(r"https?://[^\s<>]+", "", _fact_text(message))
    return bool(re.search(r"%|\b(?:yüzde\w*|oran\w*|pay(?:i|ini|inin|lar(?:i|ini|inin)?)?|"
                          r"hisse(?:si|leri)?|dağilim\w*|percent\w*|proportions?|stakes?|shares?)\b", text))


def _ownership_percentage_errors(state, content):
    if (not state.get("external_facts_required") or state.get("ownership_percentages_requested")
            or state.get("institutional_fact_kind") == "relationship"):
        return []
    # Percent-encoded citation URLs are navigation, not numerical claims.
    text = re.sub(r"https?://[^\s<>]+", "", _fact_text(content))
    if not re.search(r"%\s*\d|\d[\d., ]*\s*%|\b(?:yüzde|percent)\s*\d|\d[\d., ]*\s*\bpercent\b", text):
        return []
    return [{"code": "UNSOLICITED_OWNERSHIP_PERCENTAGES", "message":
        "The current ownership question asks for names/recommendations, not ownership percentages. "
        "Rewrite using the complete source-verified owner names and a concrete next analysis action. "
        "Do not add percentages or justify recommendations using unrequested ownership size/rank; "
        "do not merely remove percent signs while retaining those numerical claims. Keep source citations."}]


def _institutional_role_errors(state, content, sources):
    """Keep a current owner table from being relabeled as a founder list.

    This checks explicit role claims and readable source structure, not every
    factual implication in prose. A founding date/count alone supplies no names.
    """
    if not state.get("external_facts_required"):
        return []
    text = re.sub(r"https?://[^\s<>]+", "", _fact_text(content))
    founder_role = r"kurucu(?:lar\w*|\s*[/&]\s*ortak\w*|\s+(?:ortak|banka|kurum|üye)\w*)?|found(?:er|ing)(?:\s+(?:banks?|members?|partners?))?s?"
    denials = (r"doğrula\w*ma|kanıtla\w*ma|kanitla\w*ma|bulamad|ulaşamad|belirsiz|değil|ayni\s+say|ayri|farkli|distinct|separate|"
               r"teyit\s+(?:edemed|edilemed|edilmedi)|yer\s+almiyor|belirtilmiyor|"
               r"not\s+(?:verified|confirmed|the\s+same)|(?:does|do|did)\s+not\s+identify|cannot\s+verify|unverified|uncertain")
    def named_list(value):
        # Pure dates, counts or generic 'nine banks' do not identify founders.
        value = re.sub(r"\b\d{1,2}\s+\w+\s+(?:19|20)\d{2}\b|\b\w+\s+\d{1,2},?\s+(?:19|20)\d{2}\b", "", value)
        words = re.findall(r"\b[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü&.-]{2,}\b", value)
        generic = {"bank", "banks", "banka", "bankalar", "dokuz", "nine", "kurucu", "kurucular", "founders", "members", "ortaklar", "kuruluşunda", "başlangiçta"}
        return any(_fact_text(word) not in generic | {state.get("ownership_subject")} for word in words)

    count = r"(?:\d+|bir|iki|üç|dört|beş|alti|yedi|sekiz|dokuz|on|one|two|three|four|five|six|seven|eight|nine|ten)"
    def identity_uncertainty(line):
        identity = re.search(r"isim\w*|adlar\w*|kimlik\w*|ayni\s+olup\s+olmadi\w*|names?|identities|whether.{0,20}same", line)
        negative_read = re.search(r"\b(?:listele|belirtil|doğrula)\w*(?:miyor|amiyor|madi|amadi)\w*\b|"
                                  r"\bnot\s+(?:listed|identified|confirmed|provided)\b", line)
        return bool(identity and negative_read)
    founder_claim = False
    for original in re.split(r"[.!?;\n]", re.sub(r"https?://[^\s<>]+", "", content)):
        line = _fact_text(original)
        counted = re.search(r"\b" + count + r"\s+(?:kurucu\s+(?:banka|kurum)\w*|founders?\b|founding\s+banks?\b)", line)
        explicit_role = re.search(r"\b(?:" + founder_role + r")\b", line)
        historical_names = re.search(r"kuruluşunda\s+yer\s+alan|başlangiçta.{0,200}ile\s+çalişmaya\s+başla", line) and named_list(original)
        if (explicit_role or historical_names) and not re.search(denials, line) and not identity_uncertainty(line) and not (counted and not named_list(original)):
            founder_claim = True

    def founder_names(source):
        chunks = [source.get("content"), source.get("text"), (source.get("article") or {}).get("article_body")]
        chunks += [part.get("text") for key in ("pages", "passages") for part in source.get(key, []) if isinstance(part, dict)]
        for chunk in chunks:
            if not isinstance(chunk, str):
                continue
            # Explicit labels or an attribution sentence link names to founding.
            for match in re.finditer(r"(?:kurucu(?:lar[ıi]?)?(?:\s+(?:bankalar[ıi]?|ortaklar[ıi]?|üyeler[ıi]?))?|"
                    r"founders?|founding\s+(?:banks|members|partners))\s*(?::|\n)\s*([^\n]+(?:\n[^\n]+){0,4})", chunk, re.I):
                if named_list(match.group(1)):
                    return True
            for match in re.finditer(r"(?:founded|established)\s+by\s+([^.!?\n]+)|([^.!?\n]{3,200})\s+taraf[ıi]ndan\s+kurul", chunk, re.I):
                if named_list(match.group(1) or match.group(2)):
                    return True
        for table in [source, *source.get("tables", [])]:
            if not isinstance(table, dict):
                continue
            for column in table.get("columns", []):
                if re.search(r"kurucu|founder|founding", _fact_text(column)):
                    for row in table.get("rows", table.get("preview", [])):
                        values = row.get("values", row) if isinstance(row, dict) else {}
                        if isinstance(values, dict) and named_list(str(values.get(column, ""))):
                            return True
        return False

    errors = []
    for attribution in re.finditer(r"(?:founded|established)\s+by\s+([^.!?\n]+)|([^.!?\n]{3,200})\s+taraf[ıi]ndan\s+kurul", content, re.I):
        founder_claim |= named_list(attribution.group(1) or attribution.group(2)) and not re.search(denials, _fact_text(attribution.group(0)))
    if founder_claim and not any(founder_names(source) for source in sources):
        errors.append({"code": "INSTITUTIONAL_ROLE_UNVERIFIED", "message":
            "Do not label current/report-period shareholders as founders or combine the labels as founder/owner. "
            "The read source does not explicitly identify founder names; a founding date or number of banks is insufficient. "
            "Use the verified role and source period for the full name list, explain that historical founder identities are not established, "
            "and give 2-3 concrete bank-analysis suggestions. If an explicit named-founder list is available, read that section first."})
    if state.get("institutional_fact_kind") == "relationship" and not state.get("ownership_percentages_requested"):
        request = _fact_text(state.get("institutional_request"))
        count_requested = re.search(r"kaç|sayisi|sayilari|how\s+many|count|number\s+of", request)
        member_counts = re.search(r"\b\d+\s+üye\w*|\büye\s+(?:sayisi|dağilimi)\b|\b\d+\s+members?\b", text)
        ownership_table = re.search(r"\|[^\n]*(?:ortaklik\s+payi|hisse|ownership|shareholding)[^\n]*\|", text) or re.search(
            r"\|[^\n]*\bortak\w*[^\n]*\|\s*pay\s*\|", text)
        ownership_percent = any(re.search(r"ortaklik|hisse|shareholding|ownership", line)
            and re.search(r"%\s*\d|\d[\d., ]*\s*%|yüzde\s*\d", line) for line in text.splitlines())
        if ownership_table or ownership_percent or member_counts and not count_requested:
            errors.append({"code": "UNSOLICITED_INSTITUTIONAL_STATISTICS", "message":
                "The user asks which institutions and what to add to the analysis, not shareholder percentages or membership-count tables. "
                "Answer with a short source-labeled name list and 2-3 concrete analysis suggestions. Omit unsolicited ownership-size rankings, percentages and member-count breakdowns. "
                "Keep any explicitly requested quantitative analysis separate and grounded in its existing evidence."})
    return errors


def _source_read(result):
    """Search snippets never satisfy a read; each read still needs interpretation."""
    return (result.get("status") == "ok" and bool(
        result.get("text") or result.get("tables") or result.get("rows")
        or any(page.get("text") for page in result.get("pages", []) if isinstance(page, dict))
        or any(source.get("content") or source.get("text") or source.get("tables") or source.get("passages")
               for source in result.get("sources", []) if isinstance(source, dict))))


_NUMERIC_LITERAL = re.compile(r"(?<![\w])[-+]?\d(?:[\d\s.,'’]*\d)?(?![\w])")


def _numeric_literal_values(literal):
    """Return conservative locale-independent identities for one literal."""
    raw = re.sub(r"[\s'’]", "", str(literal)).strip(".,")
    if not raw or not re.search(r"\d", raw):
        return set()
    sign = ""
    if raw[0] in "+-":
        sign, raw = raw[0], raw[1:]
    if not raw:
        return set()
    values = set()

    def add(value):
        try:
            decimal = Decimal(("-" if sign == "-" else "") + value)
        except InvalidOperation:
            return
        rendered = format(decimal, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        values.add("0" if rendered in {"-0", ""} else rendered)

    separators = [character for character in raw if character in ".,"]
    if not separators:
        add(raw)
        return values
    if "." in raw and "," in raw:
        decimal_separator = "." if raw.rfind(".") > raw.rfind(",") else ","
        whole, fraction = raw.rsplit(decimal_separator, 1)
        add(whole.replace(".", "").replace(",", "") + "." + fraction)
        return values
    separator = separators[0]
    parts = raw.split(separator)
    if len(parts) > 2 and all(len(part) == 3 for part in parts[1:]):
        add("".join(parts))
        return values
    if len(parts) == 2 and all(parts):
        if len(parts[1]) <= 3:
            add(parts[0] + "." + parts[1])
        if len(parts[1]) == 3:
            add(parts[0] + parts[1])
        return values
    add("".join(parts))
    return values


def _material_numeric_literals(text):
    """Extract answer quantities while excluding navigation and date notation."""
    scrubbed = re.sub(r"https?://[^\s<>\])]+", " ", str(text or ""))
    scrubbed = re.sub(r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b", " ", scrubbed)
    month = (r"ocak|şubat|subat|mart|nisan|mayıs|mayis|haziran|temmuz|ağustos|agustos|eylül|eylul|"
             r"ekim|kasım|kasim|aralık|aralik|january|february|march|april|may|june|july|august|"
             r"september|october|november|december")
    scrubbed = re.sub(r"\b(?:0?[1-9]|[12]\d|3[01])\s+(?:" + month + r")\s+(?:19|20)\d{2}\b", " ", scrubbed, flags=re.I)
    scrubbed = re.sub(r"\b(?:19|20)\d{2}\s*(?:Q[1-4]|/[1-4]\s*Q|[1-4]\.\s*çeyrek)\b", " ", scrubbed, flags=re.I)
    scrubbed = re.sub(r"\b(?:sayfa|page|s\.|satır|satir|row|tablo|table)\s*#?\s*\d+\b", " ", scrubbed, flags=re.I)
    found = []
    for match in _NUMERIC_LITERAL.finditer(scrubbed):
        literal = match.group(0).strip()
        compact = re.sub(r"\D", "", literal)
        if re.fullmatch(r"(?:19|20)\d{2}", compact) and not re.search(r"[.,]", literal):
            continue
        values = _numeric_literal_values(literal)
        if values:
            found.append((literal, values))
    return found


def _direct_source_numeric_values(state):
    """Collect numbers only from fetched content and literal table cells."""
    texts = []

    def add_tables(tables):
        for table in tables or []:
            if not isinstance(table, dict):
                continue
            for row in table.get("rows", table.get("preview", [])) or []:
                values = row.get("values", row) if isinstance(row, dict) else row
                if isinstance(values, dict):
                    texts.extend(str(value) for value in values.values() if value is not None)
                elif isinstance(values, list):
                    texts.extend(str(value) for value in values if value is not None)

    read = False
    for item in state.get("tool_results", []):
        result = item.get("result", {})
        if result.get("status") != "ok":
            continue
        tool = item.get("tool")
        if tool == "research_web":
            for source in result.get("sources", []):
                if not isinstance(source, dict) or source.get("source_role") == "discovery_index":
                    continue
                source_texts = [source.get("content"), source.get("text"),
                                (source.get("article") or {}).get("article_body")]
                source_texts.extend(page.get("text") for page in source.get("pages", []) if isinstance(page, dict))
                source_texts.extend(passage.get("text") for passage in source.get("passages", []) if isinstance(passage, dict))
                if any(isinstance(value, str) and value.strip() for value in source_texts) or source.get("tables"):
                    read = True
                texts.extend(value for value in source_texts if isinstance(value, str))
                add_tables(source.get("tables"))
        elif tool == "inspect_source":
            source_texts = [result.get("text"), (result.get("article") or {}).get("article_body")]
            source_texts.extend(page.get("text") for page in result.get("pages", []) if isinstance(page, dict))
            if any(isinstance(value, str) and value.strip() for value in source_texts) or result.get("tables"):
                read = True
            texts.extend(value for value in source_texts if isinstance(value, str))
            add_tables(result.get("tables"))
        elif tool in {"read_source_table", "find_source_table_rows"}:
            if result.get("rows"):
                read = True
                add_tables([result])
    values = set()
    for text in texts:
        for _, variants in _material_numeric_literals(text):
            values.update(variants)
    return read, values


def _research_scope_error(state, name, args):
    """Do not turn an unanswered institutional question into a product search."""
    if (name not in {"discover", "research_web", "web_search"} or not state.get("external_facts_required")
            or any(_ownership_source(item.get("result", {}), state.get("ownership_subject"),
                                     state.get("institutional_fact_kind") == "relationship")
                   for item in state.get("tool_results", []) if item.get("tool") in {
                       "research_web", "inspect_source", "read_source_table", "find_source_table_rows"})):
        return None
    request, query = _fact_text(state.get("institutional_request")), _fact_text(args.get("query"))
    facets = (r"kredi\s+karti|credit\s+cards?", r"konut\s+kred\w*|mortgage\w*", r"taşit\s+kred\w*|vehicle\s+loans?",
              r"tüketici\s+kred\w*|consumer\s+loans?", r"ticari\s+kred\w*|commercial\s+loans?")
    invented = any(re.search(pattern, query) and not re.search(pattern, request) for pattern in facets)
    invented |= bool(set(re.findall(r"\b(?:19|20)\d{2}\b", query)) - set(re.findall(r"\b(?:19|20)\d{2}\b", request)))
    if invented:
        return {"code": "RESEARCH_QUERY_SCOPE_MISMATCH", "message":
            "The unanswered institutional question does not request the product or year added to this query. "
            "Research the institution's actual relationships/founding/members/owners first, using the original request. "
            "Do not invent a product, reporting year or membership claim. Afterwards, research justified analysis suggestions separately.",
            "original_request": state.get("institutional_request", "")}
    return None


def _search_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))
    except (TypeError, ValueError):
        return None


def _schema_validation_error(error, tool_schema):
    """Explain the invalid shape without echoing submitted values or objects."""
    def brief(value):
        return str(value)[:96]

    def expected_value(value):
        return value if value is None or isinstance(value, (bool, int, float)) else brief(value) if isinstance(value, str) else type(value).__name__

    def locations(field):
        found = []
        def walk(schema, path=(), depth=0):
            if not isinstance(schema, dict) or depth > 8 or len(found) >= 3:
                return
            for name, child in schema.get("properties", {}).items():
                if name == field:
                    found.append(".".join((*path, name)))
                walk(child, (*path, name), depth+1)
            if isinstance(schema.get("items"), dict):
                walk(schema["items"], (*path, "[]"), depth+1)
            if isinstance(schema.get("additionalProperties"), dict):
                walk(schema["additionalProperties"], (*path, "*"), depth+1)
            for keyword in ("oneOf", "anyOf", "allOf"):
                for child in schema.get(keyword, []):
                    walk(child, path, depth+1)
        walk(tool_schema)
        return list(dict.fromkeys(found))[:3]

    errors = [(error, ())]
    if error.validator in {"oneOf", "anyOf"} and isinstance(error.instance, dict):
        # A tagged operation such as op=ratio should receive its own required
        # fields, not errors from every other operation in the union.
        variants = error.validator_value
        compatible = [variant for variant in variants if isinstance(variant, dict) and not any(
            "const" in spec and name in error.instance and error.instance[name] != spec["const"]
            for name, spec in variant.get("properties", {}).items())]
        if len(compatible) == 1:
            errors = [(item, tuple(error.absolute_path)) for item in
                      list(jsonschema.Draft202012Validator(compatible[0]).iter_errors(error.instance))[:3]] or errors
    details = []
    for item, prefix in errors:
        path = ".".join(brief(part) for part in (*prefix, *item.absolute_path))[:240] or "<root>"
        info = {"path": path, "validator": item.validator}
        schema, instance = item.schema, item.instance
        properties = schema.get("properties", {})
        if item.validator in {"additionalProperties", "required"}:
            info["allowed_properties"] = [brief(key) for key in list(properties)[:24]]
            if isinstance(instance, dict):
                missing = [key for key in schema.get("required", []) if key not in instance]
                if missing:
                    info["missing_fields"] = [brief(key) for key in missing[:16]]
                if item.validator == "additionalProperties":
                    unknown = [key for key in instance if key not in properties and not any(
                        re.search(pattern, key) for pattern in schema.get("patternProperties", {}))]
                    info["unexpected_fields"] = [brief(key) for key in unknown[:16]]
                    suggestions = {brief(key): locations(key) for key in unknown[:4]}
                    if any(suggestions.values()):
                        info["accepted_field_locations"] = {key: value for key, value in suggestions.items() if value}
        elif item.validator in {"enum", "const", "type", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems", "minLength", "maxLength"}:
            expected = item.validator_value
            info["allowed_values" if item.validator in {"enum", "const"} else "expected"] = (
                [expected_value(value) for value in expected[:24]] if isinstance(expected, list) else expected_value(expected))
        elif item.validator == "not" and isinstance(item.validator_value, dict) and "const" in item.validator_value:
            info["forbidden_value"] = expected_value(item.validator_value["const"])
        elif item.validator in {"oneOf", "anyOf"}:
            tags = {}
            for variant in item.validator_value:
                if not isinstance(variant, dict):
                    continue
                for name, spec in variant.get("properties", {}).items():
                    if "const" in spec:
                        tags.setdefault(name, []).append(brief(spec["const"]))
            if tags:
                info["allowed_variant_tags"] = {name: list(dict.fromkeys(values))[:24] for name, values in list(tags.items())[:4]}
        details.append(info)
    encoded = canonical(details)
    if len(encoded) > 3000:
        details = details[:1]
        for key in ("allowed_properties", "unexpected_fields", "missing_fields"):
            if key in details[0]:
                details[0][key] = details[0][key][:8]
        details[0].pop("accepted_field_locations", None)
        encoded = canonical(details)
    return _blocked("INVALID_TOOL_ARGUMENTS", "Tool arguments violate schema; no tool was executed. "
                    + encoded[:3500] + ". The full permitted schema is supplied with this tool.")


def _refines_task_plan(previous, proposed, path=()):
    """Allow added source-known detail while retaining every prior constraint."""
    if isinstance(previous, dict):
        return isinstance(proposed, dict) and all(
            key in proposed and _refines_task_plan(value, proposed[key], (*path, key))
            for key, value in previous.items())
    if isinstance(previous, list):
        if path in {("deliverables",), ("summary", "statistics"), ("summary", "columns"), ("statistics", "methods"), ("normalization", "columns")}:
            return isinstance(proposed, list) and set(previous).issubset(proposed)
        # Window order determines the comparison's base; retain it exactly.
        return previous == proposed
    if path == ("summary", "compare_windows") and previous is False:
        return isinstance(proposed, bool)
    return previous == proposed


def _requests_shared_scale(message):
    """Recognize explicit normalization commands, not general finance intent."""
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    for clause in re.split(r"[.;\n]", text):
        if re.search(r"eşitleme(?:yin|n|z)?\b|eşitlenmesin|getirme(?:yin)?\b|gerek\s+yok|do\s+not|don't|without|not\s+(?:the\s+)?same", clause):
            continue
        if re.search(r"(?:ne demek|nedir|ne anlama|nasil|what (?:is|does)|how (?:to|can i|do i)|explain (?:the )?(?:concept|meaning))", clause):
            continue
        if re.search(r"\b(?:ayni|ortak)\s+(?:para\s+birimi\s+ve\s+|birim(?:de)?\s+ve\s+)?ölçe[kğ]\w*", clause):
            return True
        if re.search(r"\bölçe[kğ]\w*\s+eşitle(?:yin|yiniz|meni|menizi)?\b", clause):
            return True
        if re.search(r"\b(?:same|common)\s+(?:(?:currency|units?)\s+and\s+)?scale\b", clause):
            return True
    return False


def _unreadable(content):
    """Flag a final answer that is garbled: replacement characters, non-Latin/Turkish
    script, or degenerate repetition with almost no coherent words, so it is
    regenerated rather than delivered."""
    if "�" in content:
        return True
    letters = [c for c in content if c.isalpha()]
    stripped = content.strip()
    # Degenerate model output: a long answer carrying almost no real words (repetitive
    # digits/symbols) is garbage, not a Turkish sentence or a numeric table answer.
    if (len(stripped) >= 100 and len(re.findall(r"[A-Za-zçğıöşüÇĞİÖŞÜ]{2,}", content)) <= 3
            and len(letters) / len(stripped) < 0.10):
        return True
    if not letters:
        return False
    def foreign(c):
        return ("一" <= c <= "鿿" or "぀" <= c <= "ヿ"
                or "가" <= c <= "힣" or "Ѐ" <= c <= "ӿ"
                or "؀" <= c <= "ۿ")
    foreign_count = sum(foreign(c) for c in letters)
    return foreign_count > 3 and foreign_count / len(letters) > 0.10


def _grounded_refusal(barren):
    """A completed, grounded 'not found' answer built from the barren-discovery
    signal, so a genuinely absent concept ends as a stated refusal rather than a
    budget-death blocked non-answer."""
    terms = ", ".join(t for t in (barren.get("uncovered_terms") or []) if t) or "istenen seri"
    message = f"İstenen '{terms}' için kaynakta eşleşen bir seri bulunamadı; değer uydurulmaz."
    near = barren.get("near_titles") or []
    if near:
        message += " Kaynaktaki en yakın seriler: " + "; ".join(near) + "."
    return message + " Farklı bir seri, kapsam veya dönem belirtirseniz analizi ona göre yapabilirim."


def _row_not_found_refusal(barren):
    """A completed 'row not found' answer for a run that repeatedly looked up a
    dimension value the series does not contain (e.g. a national/aggregate row in a
    province-only series) and would otherwise die on the budget with no answer."""
    value = (barren.get("query") or "").strip() or "istenen satır"
    dimension = f" ({barren['dimension']} boyutunda)" if barren.get("dimension") else ""
    return (f"'{value}' değeri bu seride{dimension} bulunamadı; il/kalem bazlı bir seride ulusal ya da toplam "
            "bir satır her zaman yer almaz ve mevcut olmayan bir satır türetilmez. "
            "Var olan bir değer, kapsam veya dönem belirtirseniz analizi ona göre yapabilirim.")


def _normalize_result(result):
    """Preserve tool payloads while giving every failure one error contract."""
    if not isinstance(result, dict):
        raise PlanError("Tool result must be a JSON object")
    result = copy.deepcopy(result)
    if result.get("status") in {"blocked", "error", "failed", "unavailable"} and not result.get("errors"):
        code = result.get("code", "TOOL_UNAVAILABLE" if result["status"] == "unavailable" else "TOOL_FAILED")
        if code == "WRITE_OUTCOME_UNKNOWN":
            code = "UNKNOWN_MUTATION_OUTCOME"
        result["errors"] = [{"code": code, "message": result.get("message", "Tool could not complete the request.")}]
    return result


def _web_research_message(result, content=""):
    """Compact citations for read sources; extracted previews stay in the ledger.

    A page's opening text or first parsed table is not a summary of the user's
    question. In particular, report covers and SVG labels must not replace the
    model's source-backed explanation and recommendations.
    """
    links, seen = [], set()
    for source in result.get("sources", []):
        url = source.get("url") or source.get("source_url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            safe = (parsed and parsed.scheme in {"http", "https"} and parsed.hostname
                    and not parsed.username and not parsed.password and not any(ord(char) < 32 for char in url))
        except ValueError:
            safe = False
        if not safe:
            continue
        key = _search_url(url)
        if key in seen or url in content:
            continue
        seen.add(key)
        title = source.get("title") or source.get("filename") or parsed.hostname
        links.append(f"[{_display_label(title)}]({quote(url, safe=':/?#&=%+@')})")
    return "Okunan kaynaklar: " + " · ".join(links[:8]) + "." if links else ""


def _web_research_failure_message(result):
    code = result.get("code")
    if code == "OFFICIAL_SOURCE_NOT_FOUND":
        return "İstenen resmi kurum alanında konuya uygun ve okunabilir bir kaynak bulunamadı. İlgisiz web siteleri kaynak olarak kullanılmadı."
    if code == "NO_READABLE_SOURCES":
        return "Arama sonuçları bulundu ancak doğrudan okunabilen ve konuya uygun bir kaynak bulunamadı. Arama snippet'leri kanıt olarak kullanılmadı."
    return result.get("message") or "Web araştırması güvenilir bir kaynak okuyamadı."


class AgentRuntime:
    def __init__(self, store, workspace_id, client, run_store: AgentRunStore,
                 extra_tools=None, *, max_decisions=10, max_repairs=2,
                 service=None, max_context_chars=75000, max_elapsed_seconds=240, available_catalogues=None):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 30 or type(max_repairs) is not int or not 0 <= max_repairs <= 5:
            raise ValueError("Invalid agent decision/repair budget")
        if type(max_context_chars) is not int or not 8000 <= max_context_chars <= 500000 or not 10 <= max_elapsed_seconds <= 3600:
            raise ValueError("Invalid context or elapsed-time budget")
        self.store, self.workspace_id, self.client, self.run_store = store, workspace_id, client, run_store
        self.service = service or LakehouseService(store, workspace_id)
        self.max_decisions, self.max_repairs, self.max_context_chars = max_decisions, max_repairs, max_context_chars
        self.max_elapsed_seconds = max_elapsed_seconds
        self.available_catalogues = copy.deepcopy(available_catalogues or [])
        self.tools = self._tools()
        for name, definition in (extra_tools or {}).items():
            if name in self.tools or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                raise ValueError("Duplicate or invalid extra tool name")
            if not callable(definition.get("handler")) or definition.get("schema", {}).get("function", {}).get("name") != name:
                raise ValueError("Extra tool requires matching schema and callable handler")
            jsonschema.Draft202012Validator.check_schema(definition["schema"]["function"]["parameters"])
            self.tools[name] = dict(definition)

    def _tools(self):
        definitions = lakehouse_tools(self.service)
        text_field = {"type": "string", "minLength": 1, "maxLength": 100}
        summary = obj({
            "columns": {"type": "array", "minItems": 1, "maxItems": 6, "uniqueItems": True, "items": text_field},
            "statistics": {"type": "array", "minItems": 1, "maxItems": 9, "uniqueItems": True,
                           "items": {"enum": ["first", "last", "min", "max", "sum", "mean", "count", "change", "growth"]}},
            "windows": {"type": "array", "minItems": 1, "maxItems": 4,
                        "items": obj({"start": text_field, "end": text_field})},
            "compare_windows": {"type": "boolean"},
        }, ["statistics"])
        summary["description"] = "Omit before a saved analysis exists. Later specify only user-requested statistics on actual saved columns/native periods. Initial example: {deliverables:[analysis,chart,sources]}."
        parameters = obj({
            "deliverables": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                             "items": {"enum": ["analysis", "bundle", "selection", "chart", "sources", "dataset", "statistics", "summary", "explanation"]}},
            "summary": summary,
            "normalization": obj({
                "same_unit_scale": {"const": True},
                "columns": {"type":"array", "minItems":2, "maxItems":12, "uniqueItems":True, "items":COLUMN_NAME,
                    "description":"Independent saved monetary amount columns to present in one unit/currency/scale. Only bind after a saved analysis exists. A recorded scale-only descendant can satisfy each column; raw originals and percentage ratios are exempt. Exactly two monetary source roots can be selected automatically."},
                "target_scale": {"type":"number", "exclusiveMinimum":0,
                    "description":"Optional user-requested absolute output scale: 1=base units, 1000=thousands, 1000000=millions. This never authorizes currency conversion."},
            }, ["same_unit_scale"]),
            "statistics": obj({"methods": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                                            "items": {"enum": ["rolling_anomalies", "detect_changes", "analyze_relationship"]}}}),
        }, ["deliverables"])
        definitions["plan_task"] = {
            "schema": {"type": "function", "function": {"name": "plan_task", "parameters": parameters,
                "description": "Declare only user-requested outputs. For an explicit common unit/scale request, include normalization:{same_unit_scale:true}; source-known columns and a requested target_scale may be added later. Before an analysis is saved, summary details are rejected until actual saved columns/native periods exist. Same-row ratios are analysis outputs, not period summaries. Retain all previous requirements, never weaken constraints. Use explanation alone only for educational examples."}},
            "handler": lambda args: {"status": "ok", "task_plan": copy.deepcopy(args)},
        }
        return definitions

    def _context(self, state):
        context = workspace_context(self.store, self.workspace_id, state,
                                    max_decisions=self.max_decisions,
                                    charts_enabled="create_chart" in self.tools)
        if self.available_catalogues:
            context["available_catalogues"] = [dict(card, status="attached")
                if card.get("snapshot_id") == context["snapshot_id"] else copy.deepcopy(card)
                for card in self.available_catalogues]
            context["catalogue_navigation_hint"] = "A source added to an empty workspace does not include shared reference data. If requested reference metrics are absent, inspect available_catalogues and use attach_reference_catalogue with the current workspace_version before repeating discover or searching the web. This preserves registered sources and published datasets. Never attach an unavailable catalogue or assume its contents without discovery."
        if state.get("request_normalization") or (state.get("task_plan") or {}).get("normalization"):
            context["current_task"]["normalization_requirement"] = (
                (state.get("task_plan") or {}).get("normalization") or state["request_normalization"])
        if "ingest_source_table" in self.tools:
            context["source_workflow"] = {"primary_tool": "ingest_source_table",
                "advanced_tools_require": "explicit unsupported_layout for the exact source and table",
                "authorized_tables": [{"source_id": source, "table_id": table, "root_table_id": root}
                    for source, tables in state.get("advanced_source_tables", {}).items() for table, root in tables.items()]}
        if state.get("external_facts_required"):
            context["current_task"]["external_facts_requirement"] = (
                "Read an authoritative source for the requested institution's relationships, founding, members or owners. "
                "Memory, previous assistant lists and search snippets are not evidence. "
                "Do not substitute the active financial table for an institutional fact answer. "
                "Do not add a product or year to the user's question. Founders, current owners and members are distinct roles: "
                "label the verified role and source period; a current owner list does not prove the historical founders. "
                "When historical identities are uncertain, answer the supported current/report-period role with that limitation. "
                "Read suggested_inspection pages when relevant passages are truncated. Do not ask permission to repeat research already requested.")
            context["current_task"]["institutional_request"] = state.get("institutional_request")
        progress = state.get("search_progress", {})
        if progress.get("paused"):
            context["source_recovery"] = self._search_recovery(state)
        navigation = [self._source_page_recovery(state, source_id) for source_id, progress in state.get("source_page_progress", {}).items()
                      if progress.get("searches_since_read", 0) >= 2 and self._unread_source_pages(progress)]
        if navigation:
            context["source_page_recovery"] = navigation[:6]
        if any(progress.get("candidate_pages") or progress.get("read_pages") for progress in state.get("source_page_progress", {}).values()):
            context["source_reading_requirement"] = SOURCE_READING_PROMPT
        return context

    def _model_tool_schemas(self, state):
        if state.get("source_final_review"):
            return []
        hidden = ({"prepare_source_table", "publish_selected_table"}
                  if "ingest_source_table" in self.tools and not any(state.get("advanced_source_tables", {}).values()) else set())
        if state.get("search_progress", {}).get("paused"):
            hidden.add("web_search")
        if state.get("institutional_delivery_repair"):
            hidden.update(set(self.tools) - _INSTITUTIONAL_REPAIR_TOOLS)
        return [definition["schema"] for name, definition in self.tools.items() if name not in hidden]

    def _search_recovery(self, state):
        return {"reason": "Searches are not finding new source URLs; changing query wording alone is not progress.",
                "available_tools": [name for name in ("research_web", "inspect_source", "find_source_pages",
                                                       "find_source_table_rows", "read_source_table") if name in self.tools],
                "candidate_urls": state.get("search_progress", {}).get("urls", [])[-8:],
                "next_step": "Read a relevant official result and follow its discovered report links, or use research_web with the institution's domain. Do not guess URLs, dates, values or treat snippets as evidence. If no source is readable, explain what is missing and retain the existing analysis."}

    def _track_search_progress(self, state, name, result):
        """Track source novelty across query rewrites, durably with each step."""
        progress = state.setdefault("search_progress", {"urls": [], "stale_calls": 0})
        if name == "web_search" and result.get("status") == "ok":
            urls = list(dict.fromkeys(url for item in result.get("results", []) if isinstance(item, dict)
                                     if (url := _search_url(item.get("url")))))
            seen = set(progress["urls"])
            fresh = [url for url in urls if url not in seen]
            progress["urls"] = [*progress["urls"], *fresh][-300:]
            progress["stale_calls"] = 0 if fresh else progress["stale_calls"] + 1
            result["progress"] = {"new_source_urls": len(fresh), "repeated_result_sets": progress["stale_calls"]}
            if progress["stale_calls"] >= 2:
                progress["paused"] = True
                if not any(warning.get("code") == "SEARCH_RESULTS_REPEATED" for warning in result.get("warnings", [])):
                    result.setdefault("warnings", []).append({"code": "SEARCH_RESULTS_REPEATED",
                        "message": "Successive searches produced no new source URLs. Raw search is paused until a source is read."})
                result["recovery"] = self._search_recovery(state)
        elif name in {"research_web", "inspect_source", "read_source_table", "find_source_table_rows"} and _source_read(result):
            # Only a successful source read reopens discovery. A duplicate read
            # cannot reset the stall repeatedly or erase unrelated tool errors.
            read_key = fingerprint({key: result.get(key) for key in ("source_id", "source_url", "text", "pages", "tables", "rows", "sources")})
            if read_key not in progress.get("reads", []):
                progress.setdefault("reads", []).append(read_key)
                progress["paused"] = False
                progress["stale_calls"] = 0
                unresolved = state.setdefault("unresolved_errors", {})
                # Dispatch tracks progress before appending the current result.
                # For ownership research, the replacement read must also meet
                # the existing topic/institution gate, not merely open a page.
                evidence = {**state, "tool_results": [*state.get("tool_results", []), {"tool": name, "result": result}]}
                if not self._external_fact_errors(evidence, ""):
                    for navigation in ("web_search", "research_web", "discover"):
                        remaining = [error for error in unresolved.get(navigation, [])
                                     if error.get("code") not in (_RECOVERABLE_SEARCH_ERRORS if navigation != "discover"
                                                                   else {"RESEARCH_QUERY_SCOPE_MISMATCH"})]
                        if remaining:
                            unresolved[navigation] = remaining
                        else:
                            unresolved.pop(navigation, None)

    @staticmethod
    def _unread_source_pages(progress):
        return [page for page in progress.get("candidate_pages", []) if page not in progress.get("read_pages", [])]

    def _source_page_recovery(self, state, source_id):
        progress = state.get("source_page_progress", {}).get(source_id, {})
        return {"source_id": source_id, "navigation_only": True,
            "suggested_inspection": {"source_id": source_id, "page_numbers": self._unread_source_pages(progress)[:3]},
            "search_complete": progress.get("complete_search", False), "next_start_page": progress.get("next_start_page"),
            "next_step": "Read the suggested physical pages with inspect_source, then locate a line item with find_source_table_rows or read relevant rows with read_source_table. "
                "Search excerpts locate pages; they do not verify a table or its absence. Changing query wording without reading is not progress. "
                "Continue an incomplete search using its next_start_page when necessary; do not invent PDF page offsets."}

    def _track_source_pages(self, state, name, args, result, call_id):
        if name not in {"find_source_pages", "inspect_source", "read_source_table", "find_source_table_rows"} or result.get("status") != "ok":
            return
        source_id = result.get("source_id") or args.get("source_id")
        if not source_id:
            return
        progress = state.setdefault("source_page_progress", {}).setdefault(source_id, {
            "searches_since_read": 0, "candidate_pages": [], "read_pages": [], "reads": [], "events": []})
        if call_id in progress["events"]:
            return
        progress["events"] = [*progress["events"], call_id][-100:]
        if name == "find_source_pages":
            pages = [*(result.get("suggested_inspection") or {}).get("page_numbers", []),
                     *(match.get("page") for match in result.get("matches", []) if isinstance(match, dict))]
            pages = [page for page in pages if type(page) is int and page > 0]
            progress["candidate_pages"] = list(dict.fromkeys([*pages, *progress["candidate_pages"]]))[:40]
            progress["searches_since_read"] += 1
            progress["complete_search"] = progress.get("complete_search", False) or result.get("complete") is True
            progress["next_start_page"] = result.get("next_start_page")
            progress["last_query"] = args.get("query")
            if progress["searches_since_read"] >= 2 and self._unread_source_pages(progress):
                result["recovery"] = self._source_page_recovery(state, source_id)
            return
        if name in {"read_source_table", "find_source_table_rows"}:
            if not result.get("rows"):
                return
            pages = [result.get("page"), *(result.get("source_pages") or [])]
        else:
            pages = [page.get("page") for page in result.get("pages", []) if isinstance(page, dict) and str(page.get("text") or "").strip()]
        pages = [page for page in pages if type(page) is int and page > 0]
        candidates = set(progress["candidate_pages"])
        progress["read_pages"] = sorted(set([*progress["read_pages"], *pages]))
        if result.get("source_url"):
            progress["source_url"] = result["source_url"]
        if not candidates.intersection(pages):
            return
        read_key = fingerprint({key: result.get(key) for key in ("source_id", "raw_sha256", "table_id", "pages", "text", "tables", "rows")})
        if read_key in progress["reads"]:
            return
        progress["reads"].append(read_key)
        progress["searches_since_read"] = 0
        progress["candidate_read_after_search"] = True
        unresolved = state.setdefault("unresolved_errors", {})
        remaining = [error for error in unresolved.get("find_source_pages", [])
                     if error.get("code") != "SOURCE_READ_REQUIRED" or error.get("source_id") != source_id]
        if remaining:
            unresolved["find_source_pages"] = remaining
        else:
            unresolved.pop("find_source_pages", None)
        # A fresh candidate-page/table read replaces the repeated-read stall
        # for that source only. Keep the one-shot allowance consumed, and keep
        # unrelated execution or other-source failures visible.
        for tool in ("inspect_source", "read_source_table", "find_source_table_rows"):
            remaining = [error for error in unresolved.get(tool, [])
                         if error.get("code") != "SOURCE_READ_REPEATED" or error.get("source_id") != source_id]
            if remaining:
                unresolved[tool] = remaining
            else:
                unresolved.pop(tool, None)

    def _source_read_required(self, state, name, args):
        if name != "find_source_pages":
            return None
        source_id = args.get("source_id")
        progress = state.get("source_page_progress", {}).get(source_id, {})
        if (progress.get("searches_since_read", 0) < 2 or not self._unread_source_pages(progress)
                or (progress.get("next_start_page") == args.get("start_page")
                    and args.get("start_page", 1) > 1 and args.get("query") == progress.get("last_query"))):
            return None
        return {"status": "blocked", "errors": [{"code": "SOURCE_READ_REQUIRED", "source_id": source_id,
            "message": "Several searches located source pages, but those pages have not been read. Inspect the suggested pages before rewording this source search."}],
            "recovery": self._source_page_recovery(state, source_id)}

    @staticmethod
    def _source_final_evidence(state, *, stalled_source=None):
        """Reserve the last existing decision only for a read, explicit gap.

        Search results alone and unrelated failed actions cannot justify this
        closeout. A published source still needs the normal calculation path.
        """
        if (state.get("analysis_id") or state.get("analysis_updated") or state.get("chart_updated")
                or _successful_bundle(state)
                or state.get("external_facts_required") or _published_source_ids(state)
                or any(error.get("code") != "SOURCE_READ_REPEATED"
                       and not (stalled_source and error.get("code") == "NO_PROGRESS" and error.get("source_id") == stalled_source)
                       for errors in state.get("unresolved_errors", {}).values() for error in errors)):
            return []
        from agentic_analytics.agent.tools.source_index import _term_key
        def terms(text):
            return {_term_key(word) for word in re.findall(r"\w+", str(text or ""))
                    if len(word) > 2 and word.casefold() not in {"the", "and", "for", "ile", "veya"}}
        evidence, seen = [], set()
        for item in reversed(state.get("tool_results", [])):
            result = item.get("result", {})
            if item.get("tool") != "inspect_source" or result.get("status") != "ok":
                continue
            source_id = result.get("source_id")
            if stalled_source and source_id != stalled_source:
                continue
            progress = state.get("source_page_progress", {}).get(source_id, {})
            url = result.get("source_url") or progress.get("source_url")
            if not (url and progress.get("complete_search") and progress.get("candidate_read_after_search")):
                continue
            queries = [progress.get("last_query"), *[entry.get("result", {}).get("query") for entry in state.get("tool_results", [])
                if entry.get("tool") == "find_source_pages" and entry.get("result", {}).get("status") == "ok"
                and entry.get("result", {}).get("source_id") == source_id]]
            topic_terms = set().union(*(terms(query) for query in queries))
            for page in result.get("pages", []):
                number = page.get("page")
                if number not in progress.get("candidate_pages", []):
                    continue
                lines = str(page.get("text") or "").splitlines()
                for index, line in enumerate(lines):
                    if not re.match(r"\s*(?:Not (?:prepared|provided|disclosed|presented)|Hazırlanmamıştır|Sunulmamıştır|Açıklanmamıştır)\b", line, re.I):
                        continue
                    key = (source_id, number, line.strip())
                    if key in seen:
                        continue
                    seen.add(key)
                    label = next((text.strip()[:180] for text in reversed(lines[:index]) if text.strip()), "Okunan dipnot")
                    relevance = len(terms(label) & topic_terms)
                    if not relevance:
                        continue  # An omitted unrelated note is not the requested-data gap.
                    sentence = re.split(r"(?<=[.!?])\s+", " ".join(lines[index:index + 2]).strip(), maxsplit=1)[0]
                    words = sentence.split()
                    evidence.append({"source_id": source_id, "page": number, "label": label,
                        "url": url.split("#", 1)[0] + f"#page={number}",
                        "quote": " ".join(words[:25]), "quote_truncated": len(words) > 25,
                        "raw_sha256": result.get("raw_sha256"), "topic_overlap": relevance})
        # Source words are only a relevance ranking. The receipt states what
        # was not verified, never that a matching heading proves global absence.
        return sorted(evidence, key=lambda item: -item["topic_overlap"])[:1]

    @staticmethod
    def _source_final_receipt(evidence):
        notes = "\n".join(f"- [{item['label']}, s. {item['page']}]({item['url']}): “{item['quote']}{'…' if item.get('quote_truncated') else ''}”" for item in evidence)
        return ("İstenen dağılımı okunan kaynak bölümlerinden doğrulayamadım. İlgili dipnotlarda şu açıklamalar yer alıyor:\n\n"
            + notes + "\n\nBu nedenle istenen tutar tablosu, oranlar ve grafik oluşturulmadı. "
            "Bu sonuç okunan bölümlerin sınırını gösterir; belgenin incelenmeyen bölümleri için kesin yokluk iddiası değildir.")

    @staticmethod
    def _sourced_limitation(state, content, errors):
        """A read, cited limitation can preserve prose, never fulfill artifacts."""
        if state.get("analysis_id") or state.get("analysis_updated") or state.get("chart_updated") or _successful_bundle(state):
            return False  # Computed results keep the verified-artifact renderer.
        if not errors or any(error.get("code") not in {
                "TASK_DELIVERABLE_MISSING", "CHART_NOT_CREATED", "TABLE_NOT_CREATED",
                "NORMALIZATION_ANALYSIS_MISSING", "SOURCE_READ_REPEATED"} for error in errors):
            return False
        if any(error.get("code") != "SOURCE_READ_REPEATED" for failures in state.get("unresolved_errors", {}).values() for error in failures):
            return False
        limitation = re.search(r"bulamad[ıi]m|bulunamad[ıi]|bulunmuyor|doğrulayamad[ıi]m|doğrulanam[ıi]yor|doğrulanamad[ıi]|"
            r"yer alm[ıi]yor|sunulmam[ıi]ş|haz[ıi]rlanmam[ıi]ş|yay[ıi]mlanmam[ıi]ş|not (?:provided|prepared|disclosed|available)|could not (?:find|verify)", content, re.I)
        if not limitation:
            return False
        citations = {_search_url(url.rstrip(".,;")) for url in re.findall(r"https?://[^\s<>\])]+", content)}
        repeated_sources = {error.get("source_id") for error in errors if error.get("code") == "SOURCE_READ_REPEATED"}
        return any(progress.get("complete_search") and progress.get("candidate_read_after_search")
                   and progress.get("source_url") and _search_url(progress["source_url"]) in citations
                   and (not repeated_sources or repeated_sources == {source_id})
                   for source_id, progress in state.get("source_page_progress", {}).items())

    @staticmethod
    def _external_fact_errors(state, content):
        if not state.get("external_facts_required"):
            return []
        # A short clarification may ask the user to name the institution; it
        # makes no ownership assertion and should not require an arbitrary search.
        if len(content) < 240 and re.match(r"\s*(?:Hangi|Hangisini|Which)\b", content, re.I) and content.rstrip().endswith("?"):
            return []
        percentage_errors = _ownership_percentage_errors(state, content)
        relationship = state.get("institutional_fact_kind") == "relationship"
        reads = [item for item in state.get("tool_results", []) if item.get("tool") in {
            "research_web", "inspect_source", "read_source_table", "find_source_table_rows"}]
        identities = {}
        relevant, relevant_evidence, complete_read = [], [], False
        for item in reads:
            result = item.get("result", {})
            if result.get("status") != "ok":
                continue
            for source in result.get("sources", [result]):
                if source.get("source_id") and (source.get("url") or source.get("source_url")):
                    identities[source["source_id"]] = {"source_url": source.get("url") or source["source_url"],
                        "title": source.get("title") or source.get("article", {}).get("title"), "raw_sha256": source.get("raw_sha256")}
        for item in reads:
            result = item.get("result", {})
            identity = identities.get(result.get("source_id"))
            if item["tool"] in {"read_source_table", "find_source_table_rows"} and identity and (
                    not identity.get("raw_sha256") or not result.get("raw_sha256") or identity["raw_sha256"] == result["raw_sha256"]):
                # Row reads carry their registered source ID; use the URL from
                # that same successful read, never infer identity from ID text.
                result = {**identity, **result}
            if _ownership_source(result, state.get("ownership_subject"), relationship):
                projected = _model_tool_result(item["tool"], result)
                for original, source in zip(result.get("sources", [result]), projected.get("sources", [projected])):
                    if not _ownership_source({"status": "ok", **original}, state.get("ownership_subject"), relationship):
                        continue
                    passages = source.get("passages", [])
                    partial = (any(p.get("content_truncated") for p in passages) or source.get("model_passages_truncated")) if passages else any(
                        source.get(key) for key in ("content_truncated", "text_truncated", "model_rows_truncated"))
                    partial |= not _ownership_source({"status": "ok", **source}, state.get("ownership_subject"), relationship)
                    complete_read |= not partial
                    relevant_evidence.append(original)
                    relevant.append({key: source[key] for key in ("source_id", "source_url", "url", "matched_pages", "suggested_inspection") if key in source})
        if relevant:
            role_errors = _institutional_role_errors(state, content, relevant_evidence)
            # Do not accept a claim that research failed after matching source
            # text was read. Historical founder uncertainty is still legitimate.
            sentences = re.split(r"[.!?\n]", _fact_text(content))
            denied_read = any(re.search(r"(?:kaynak|belge|rapor).{0,70}(?:bulamad|ulaşamad|okuyamad)|"
                r"(?:could\s+not|couldn't|unable\s+to)\s+(?:find|read|access).{0,40}(?:source|report|document)", sentence)
                and not (re.search(r"kurucu|tarihsel|founder|historical", sentence)
                         and re.search(r"(?:rapor\w*|güncel|current|reported).{0,35}(?:ortak|üye|owner|shareholder|member)", _fact_text(content)))
                for sentence in sentences)
            if denied_read:
                return [*percentage_errors, *role_errors, {"code": "READ_SOURCE_ANSWER_REQUIRED" if complete_read else "SOURCE_READING_INCOMPLETE",
                    "message": "Relevant institutional source text was already read; do not claim that no source was found. "
                    + ("Answer the verified facts and requested analysis suggestions, with source role/period labels. " if complete_read else
                       "Read the suggested physical pages or matched source table to finish the truncated relevant section, then answer. ")
                    + "Historical founder identities may remain uncertain; label current/report-period owners separately and explain that limit without discarding supported facts.",
                    "read_sources": relevant[-3:]}]
            return [*percentage_errors, *role_errors]
        return [*percentage_errors, {"code": "EXTERNAL_FACTS_UNVERIFIED", "message":
                 "Institutional facts require matching relationship/founding/member/ownership content read in this turn, for the explicitly named institution when present. Read the authoritative relevant page or table; unrelated reports, navigation links, search snippets and remembered lists do not verify these roles. Current owners are not automatically historical founders."}]

    def _failure_message(self, state, errors):
        codes = {error.get("code") for error in errors}
        if "SOURCE_READ_REPEATED" in codes:
            return "İlgili sayfalar okundu, ancak aynı okuma tekrarlandığı için kaynak incelemesi sonuçlandırılamadı. İstenen tablo ve hesaplar henüz doğrulanamadı."
        if "SOURCE_READ_REQUIRED" in codes or ("NO_PROGRESS" in codes and (state.get("tool_results") or [{}])[-1].get("tool") == "find_source_pages"):
            return "Belgede arama yapıldı, ancak ilgili sayfaların incelemesi tamamlanamadı. Bu nedenle istenen tablo ve hesaplar kaynak üzerinden doğrulanamadı."
        if "UNSOLICITED_OWNERSHIP_PERCENTAGES" in codes:
            return "Yanıt, istenmeyen ve kaynakla tutarlılığı doğrulanmamış ortaklık oranları içerdiği için sunulamadı. Ortak adları ve karşılaştırmanın sonraki adımıyla sınırlı bir yanıt gerekiyor."
        if "EXTERNAL_FACTS_UNVERIFIED" in codes:
            return "Kurumun ortak, üye veya kurucularını doğrulayacak ilgili kaynak bölümünü okuyamadım; doğrulanmamış bir kurum listesi veremiyorum."
        if codes & {"READ_SOURCE_ANSWER_REQUIRED", "SOURCE_READING_INCOMPLETE"}:
            return "İlgili kaynağa ulaştım, ancak istenen kurum listesini ve önerileri kaynakla tutarlı biçimde açıklama adımı tamamlanamadı."
        if codes & {"INSTITUTIONAL_ROLE_UNVERIFIED", "UNSOLICITED_INSTITUTIONAL_STATISTICS"}:
            return "İlgili kaynak okundu, ancak yanıt ortak, üye ve kurucu rollerini veya istenen kapsamı doğru ayırmadığı için tamamlanamadı."
        if codes & _SEARCH_FAILURES or state.get("search_progress", {}).get("paused"):
            return ("Arama sonuçlarından istenen rapora ulaşıp gerekli veriyi doğrulayamadım. "
                    + ("Yeni kaynak getirmeyen aramalar durduruldu. " if state.get("search_progress", {}).get("paused") else "")
                    + ("Mevcut analiz korundu. " if self.store.workspace(self.workspace_id).get("analysis_head") else "Henüz analiz tablosu veya grafik oluşturulmadı. ")
                    + "Arama özetleri doğrulanmış veri olarak kullanılmadı. "
                    "Devam etmek için ilgili dönemin resmi rapor bağlantısını paylaşabilir veya dosyayı yükleyebilirsiniz.")
        return "Analiz güvenilir biçimde tamamlanamadı. Araç hata ayrıntıları kaydedildi."

    def _advanced_source_allowed(self, state, args):
        return ("ingest_source_table" not in self.tools or
                args.get("table_id") in state.get("advanced_source_tables", {}).get(args.get("source_id"), {}))

    def _premature_source_question(self, record, state, question):
        if (state.get("source_clarification_repair") or state["decisions"] >= self.max_decisions
                or not _source_permission_question(question)
                or not re.search(r"\b(?:ekle|ekleyelim|ekleyebilir\w*|dahil\s+et|add|include|extend)\b", _fact_text(record["message"]))
                or _external_research_forbidden(state.get("messages", []))
                or not self.store.workspace(self.workspace_id).get("analysis_head")
                or not any(name in self.tools for name in ("research_web", "web_search"))):
            return False
        results = state.get("tool_results", [])
        if any(item.get("tool") in {"web_search", "research_web", "inspect_source", "find_source_pages",
                                    "read_source_table", "find_source_table_rows"}
               for item in results):
            return False
        return any((item.get("tool") == "discover" and item.get("result", {}).get("no_confident_match"))
                   or (item.get("tool") == "dimension_values" and item.get("result", {}).get("status") == "ok"
                       and item["result"].get("total") == 0)
                   for item in results)

    @staticmethod
    def _clear_source_workflow_errors(state, source_id, table_id):
        # These calls were denied before execution. A successful replacement
        # path resolves only the capability error, never a source/semantic error.
        unresolved = state.get("unresolved_errors", {})
        for name in ("prepare_source_table", "publish_selected_table"):
            remaining = [error for error in unresolved.get(name, []) if not (
                error.get("code") == "SOURCE_WORKFLOW_REQUIRED" and error.get("source_id") == source_id
                and error.get("table_id") == table_id)]
            if remaining:
                unresolved[name] = remaining
            else:
                unresolved.pop(name, None)

    def _update_source_capabilities(self, state, name, args, result):
        if "ingest_source_table" not in self.tools or name not in {"ingest_source_table", "prepare_source_table", "publish_selected_table"}:
            return
        source_id, table_id = args.get("source_id"), args.get("table_id")
        if not source_id or not table_id:
            return
        grants = state.setdefault("advanced_source_tables", {})
        tables = grants.setdefault(source_id, {})
        if name == "ingest_source_table":
            unsupported = (result.get("status") == "ok" and result.get("import_status") == "unsupported_layout"
                           and result.get("publication_performed") is False and not result.get("dataset_id"))
            if unsupported:
                tables[table_id] = table_id
                self._clear_source_workflow_errors(state, source_id, table_id)
            else:
                root = tables.get(table_id, table_id)
                family = {table_id, root, *(candidate for candidate, parent in tables.items() if parent == root)}
                for candidate in family:
                    tables.pop(candidate, None)
                if result.get("status") == "ok" and result.get("dataset_id"):
                    for candidate in family:
                        self._clear_source_workflow_errors(state, source_id, candidate)
        elif result.get("status") == "ok" and table_id in tables:
            if name == "prepare_source_table" and result.get("source_id") == source_id and result.get("table_id"):
                if result.get("preparation", {}).get("source_table_id") == table_id:
                    tables[result["table_id"]] = tables[table_id]
                    self._clear_source_workflow_errors(state, source_id, result["table_id"])
            elif name == "publish_selected_table" and result.get("dataset_id"):
                root = tables[table_id]
                for candidate in {root, *(candidate for candidate, parent in tables.items() if parent == root)}:
                    self._clear_source_workflow_errors(state, source_id, candidate)
        if not tables:
            grants.pop(source_id, None)

    def _messages(self, state):
        if state.get("source_final_review"):
            context = self._context(state)
            context.pop("initial_metric_candidates", None)
            context["read_source_limitation_evidence"] = state["source_final_review"]
            question = next(message for message in reversed(state["messages"]) if message.get("role") == "user")
            return model_messages({**state, "messages": [copy.deepcopy(question)]}, context_factory=lambda _: context,
                charts_enabled=False, max_context_chars=self.max_context_chars,
                system_prompt=SOURCE_READ_REPAIR_PROMPT + "\nBu son kararın kaynak okuması tamamlandı ve yeni araç çağrısı yoktur. "
                    "Yalnız read_source_limitation_evidence içindeki bölüm adı, fiziksel sayfa, birebir kısa alıntı ve bağlantıları kullan. "
                    "İstenen dağılımın okunan bölümlerden doğrulanamadığını, tablo, oran ve grafiğin oluşturulmadığını açıkça söyle. "
                    "Bütün PDF hakkında kesin yokluk iddiası veya başka finansal sayı verme. Kısa, kaynaklı bir sınırlama yanıtı yaz.")
        if state.get("source_read_repair_pending"):
            # One focused response to a repeated read, retaining actual source
            # bodies and obligations rather than speculative assistant prose.
            current = max(index for index, message in enumerate(state["messages"]) if message.get("role") == "user")
            messages = state["messages"][current:]
            calls = {call["id"]: call for message in messages for call in message.get("tool_calls", [])}
            reads, searches, keep = {}, [], set()
            for message in messages:
                call = calls.get(message.get("tool_call_id"))
                if not call:
                    continue
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                result = json.loads(message["content"])
                if name == "inspect_source" and result.get("status") == "ok":
                    key = canonical({"source_id": result.get("source_id"), "pages": sorted(args.get("page_numbers") or []), "strategy": args.get("table_strategy", "lines")})
                    reads.pop(key, None)
                    reads[key] = call["id"]
                elif name == "find_source_pages":
                    searches.append(call["id"])
                elif name in {"read_source_table", "find_source_table_rows"} or result.get("errors"):
                    keep.add(call["id"])
            keep.update(list(reads.values())[-3:])
            keep.update(searches[-2:])
            selected = []
            for message in messages:
                if message.get("role") == "user" or message.get("tool_call_id") in keep:
                    selected.append(copy.deepcopy(message))
                elif message.get("tool_calls"):
                    retained = [call for call in message["tool_calls"] if call["id"] in keep]
                    if retained:
                        selected.append({"role": "assistant", "content": None, "tool_calls": copy.deepcopy(retained)})
            context = self._context(state)
            context.pop("initial_metric_candidates", None)
            context["source_read_repair"] = state["source_read_repair_used"]
            return model_messages({**state, "messages": selected}, context_factory=lambda _: context,
                charts_enabled=False, max_context_chars=self.max_context_chars, system_prompt=SOURCE_READ_REPAIR_PROMPT)
        if state.get("institutional_delivery_repair"):
            # Reuse the existing one-shot correction with a focused model view.
            # The complete conversation and original tool ledger stay durable.
            current_turn = max(index for index, message in enumerate(state["messages"]) if message.get("role") == "user")
            messages = [message for message in state["messages"][current_turn:]
                        if message.get("role") != "assistant" or message.get("tool_calls")]
            view = {**state, "messages": messages}
            context = self._context(state)
            context = {key: context[key] for key in ("workspace_id", "snapshot_id", "workspace_version", "active_analysis_id", "active_plan", "active_schema", "current_task", "remaining_decisions") if key in context}
            context["correction_errors"] = state["institutional_delivery_repair"]
            context["coverage_note"] = "active_plan is the saved analysis window, not proof of additional report periods or monthly source coverage. New peer-bank observations require the corresponding reports."
            return model_messages(view, context_factory=lambda _: context, charts_enabled=False,
                max_context_chars=self.max_context_chars, system_prompt=INSTITUTIONAL_REPAIR_PROMPT)
        return model_messages(state, context_factory=self._context,
                              charts_enabled="create_chart" in self.tools,
                              max_context_chars=self.max_context_chars)

    def run(self, message, conversation_id=None, request_id=None, source_ids=None):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 16000:
            raise ValueError("message must be a nonempty string of at most 16000 characters")
        with self.run_store.workspace_lock(self.workspace_id):
            from agentic_analytics.agent.source_context import validate_sources
            selected = validate_sources(self.store, self.workspace_id, source_ids)
            record = self.run_store.start(self.workspace_id, message, conversation_id, request_id, source_ids=selected)
            return self._run(record)

    def resume(self, run_id, *, retry_terminal=False):
        with self.run_store.workspace_lock(self.workspace_id):
            record = self.run_store.get(run_id)
            if record["workspace_id"] != self.workspace_id:
                raise ValueError("Run belongs to another workspace")
            if retry_terminal and record["result"] is not None:
                record = self.run_store.reopen_retryable(run_id)
            return self._run(record)

    def _institutional_intent(self, record, state):
        message = record["message"]
        kind, subject = _institutional_fact_kind(message), _ownership_subject(message)
        intent = {"institutional_fact_kind": kind, "institutional_request": message,
                  "institutional_origin_request": message, "ownership_subject": subject}
        followup = _institutional_followup_kind(message)
        if not followup:
            return intent
        users = [item.get("content") for item in state["messages"] if item.get("role") == "user"]
        if len(users) < 2:
            return intent
        previous = next((item for item in self.run_store.list(self.workspace_id, record["conversation_id"], limit=2)
                         if item["run_id"] != record["run_id"]), None)
        # The immediately preceding user turn is the boundary. Do not search
        # older institution topics across intervening financial requests.
        if not previous or previous["message"] != users[-2]:
            return intent
        prior = previous["state"]
        if not prior.get("external_facts_required") or not prior.get("institutional_fact_kind"):
            return intent
        origin = prior.get("institutional_origin_request") or previous["message"]
        intent.update(institutional_fact_kind=prior["institutional_fact_kind"] if followup == "continue" else followup,
                      ownership_subject=prior.get("ownership_subject"), institutional_origin_request=origin,
                      institutional_request=origin + "\nBu turdaki kullanıcı isteği: " + message)
        return intent

    def _run(self, record):
        if record["result"] is not None:
            return record["result"]
        # Elapsed time is bounded per active invocation, using a monotonic clock.
        # Application downtime never consumes this budget. The total decision
        # count remains durable across every resume and is never reset here.
        invocation_started = time.monotonic()
        state, run_id = record["state"], record["run_id"]
        try:
            if "institutional_fact_kind" not in state:
                state.update(self._institutional_intent(record, state))
            state.setdefault("institutional_request", record["message"])
            state.setdefault("external_facts_required", bool(state["institutional_fact_kind"]))
            state.setdefault("ownership_subject", _ownership_subject(record["message"]))
            state.setdefault("ownership_percentages_requested", _requests_ownership_percentages(record["message"]))
            if "request_normalization" not in state:
                state["request_normalization"] = {"same_unit_scale": True} if _requests_shared_scale(record["message"]) else None
            if state.get("delivery_pending") and not state["pending"]:
                return self._complete(record, state, **state["delivery_pending"])
            if "initial_candidates" not in state:
                # The model can refine this bounded natural-language search.
                state["initial_candidates"] = ({"status": "not_requested", "metrics": [],
                    "next_step": "This is an institutional fact question. Read a relevant authoritative source before exploring financial metric suggestions."}
                    if state.get("external_facts_required") else _model_tool_result("discover", self.service.discover({"query": initial_query(record["message"]), "limit": 5})))
                self.run_store.event(run_id, "run_started", {"workspace_id": self.workspace_id, "conversation_id": record["conversation_id"], "request_id": record["request_id"]})
                self.run_store.checkpoint(run_id, state)
            while state["decisions"] < self.max_decisions or state["pending"]:
                if state["pending"]:
                    call = state["pending"][0]
                    result = self._dispatch(run_id, state, call)
                    state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_model_tool_result(call["function"]["name"], result))})
                    state["tool_results"].append({"tool": call["function"]["name"], "call_id": call["id"], "result": _compact(result)})
                    if call["function"]["name"] in {"ingest_source_table", "prepare_source_table", "publish_selected_table"}:
                        try:
                            capability_args = json.loads(call["function"]["arguments"])
                        except (ValueError, TypeError):
                            capability_args = {}
                        if isinstance(capability_args, dict):
                            self._update_source_capabilities(state, call["function"]["name"], capability_args, result)
                    if (self.tools.get(call["function"]["name"], {}).get("mutating")
                            and result.get("status") == "ok" and result.get("publication_performed") is not False):
                        # A compiler can successfully inspect an unsupported
                        # layout without publishing. This capability outcome
                        # must not be remembered as a committed write.
                        write_key = fingerprint({"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])})
                        state.setdefault("successful_writes", {})[write_key] = result
                    state["pending"].pop(0)
                    if call["function"]["name"] == "plan_task" and result.get("status") == "ok":
                        state["task_plan"] = result["task_plan"]
                    # Reading a web page is an intermediate result. The same turn
                    # may still need to inspect another URL, publish a table,
                    # calculate, summarize, or create a chart.
                    if result.get("analysis_id") and result.get("status") in {"ok", "valid"}:
                        state["analysis_id"] = result["analysis_id"]
                        if call["function"]["name"] in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"}:
                            state["analysis_updated"] = True
                            if state.get("chart_analysis_id") != result["analysis_id"]:
                                state["chart_updated"] = False
                                state["chart_id"] = None
                                state["chart_columns"] = []
                                state["recommendations"] = []
                    if result.get("bundle_id") and result.get("status") == "ok":
                        state["analysis_bundle_id"] = result["bundle_id"]
                        state["bundle_updated"] = True
                    if call["function"]["name"] == "create_chart" and result.get("status") == "ok":
                        state["chart_id"] = result.get("chart_id")
                        state["chart_analysis_id"] = result.get("analysis_id")
                        state["chart_updated"] = bool(result.get("chart_id"))
                        state["chart_columns"] = result.get("spec", {}).get("columns", [])
                        state["recommendations"] = result.get("recommendations", [])[:3]
                    if call["function"]["name"] == "discover" and isinstance(result, dict):
                        # Remember an unresolved discovery so a genuinely absent concept can end
                        # as a grounded refusal. Guard: once any ready candidate has been seen,
                        # a later barren search is a mid-analysis stall, not a missing series.
                        if any(isinstance(m, dict) and m.get("status") == "ready" for m in (result.get("metrics") or [])):
                            state["saw_ready_candidate"] = True
                            state.pop("discovery_barren", None)
                        elif result.get("no_confident_match"):
                            state["discovery_barren"] = {"uncovered_terms": result.get("uncovered_terms") or [],
                                                         "near_titles": [m.get("title") for m in (result.get("near_matches") or []) if isinstance(m, dict) and m.get("title")][:3]}
                    elif call["function"]["name"] in {"describe", "dimension_values", "validate_plan"} and result.get("status") in {"ok", "valid"}:
                        state.pop("discovery_barren", None)
                        # A dimension lookup that returns nothing for a named value means the
                        # requested row (e.g. a national/aggregate row in province-only data)
                        # does not exist; remember it so a looping run refuses by naming the
                        # missing value instead of dying blank. A later non-empty lookup clears it.
                        if call["function"]["name"] == "dimension_values":
                            lookup = (json.loads(call["function"]["arguments"] or "{}").get("query") or "").strip()
                            if lookup and not result.get("total"):
                                state["dimension_barren"] = {"query": lookup, "dimension": result.get("dimension")}
                            elif result.get("total"):
                                state.pop("dimension_barren", None)
                    failed = result.get("status") in {"blocked", "error", "failed", "unavailable"}
                    unresolved = state.setdefault("unresolved_errors", {})
                    tool_name = call["function"]["name"]
                    if failed:
                        retained = [error for error in unresolved.get(tool_name, [])
                                    if error.get("code") in {"SOURCE_READ_REQUIRED", "SOURCE_READ_REPEATED"}
                                    and not any(replacement.get("code") == error.get("code")
                                                and replacement.get("source_id") == error.get("source_id")
                                                for replacement in result.get("errors", []))]
                        unresolved[tool_name] = [*retained, *result.get("errors", [])]
                    elif result.get("status") in {"ok", "valid"}:
                        retained = [error for error in unresolved.get(tool_name, [])
                                    if error.get("code") in {"SOURCE_READ_REQUIRED", "SOURCE_READ_REPEATED"}]
                        if retained:
                            unresolved[tool_name] = retained
                        else:
                            unresolved.pop(tool_name, None)
                        if tool_name == "research_web" and result.get("sources"):
                            state["web_research_completed"] = True
                            # A web result can recover a failed search, but does
                            # not repair an invalid calculation or failed chart.
                            search_errors = [error for error in unresolved.get("web_search", []) if error.get("code") not in _RECOVERABLE_SEARCH_ERRORS]
                            if search_errors:
                                unresolved["web_search"] = search_errors
                            else:
                                unresolved.pop("web_search", None)
                            for lookup in ("discover", "describe", "dimension_values"):
                                if unresolved.get(lookup) and all(error.get("code") in {
                                        "METRIC_NOT_FOUND", "DIMENSION_VALUE_NOT_FOUND", "SOURCE_NOT_FOUND", "RESEARCH_QUERY_SCOPE_MISMATCH"}
                                        for error in unresolved[lookup]):
                                    unresolved.pop(lookup, None)
                        if tool_name in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"}:
                            for resolved_name in ("execute", "revise_analysis", "query_grouped", "aggregate_dataset", "validate_plan"):
                                unresolved.pop(resolved_name, None)
                        if tool_name == "select_analysis_rows":
                            remaining = [error for error in unresolved.get("analyze_relationship", [])
                                         if error.get("code") != "UNREQUESTED_STATISTICAL_METHOD"]
                            if remaining:
                                unresolved["analyze_relationship"] = remaining
                            else:
                                unresolved.pop("analyze_relationship", None)
                    for key in ("artifact_ref", "artifact_id", "source_id"):
                        if result.get(key):
                            item = {"kind": key, "id": result[key]}
                            if item not in state["artifacts"]:
                                state["artifacts"].append(item)
                    self.run_store.checkpoint(run_id, state)
                    if state.get("delivery_pending") and call["id"] == state.get("automatic_summary_call_id"):
                        if failed:
                            state.setdefault("delivery_warnings", []).extend(result.get("errors", []))
                            unresolved.pop(tool_name, None)
                        return self._complete(record, state, **state["delivery_pending"])
                    if result.get("status") == "needs_input":
                        if tool_name == "ask_user":
                            remaining = [error for error in unresolved.get(tool_name, []) if error.get("code") != "INVALID_TOOL_ARGUMENTS"]
                            if remaining:
                                unresolved[tool_name] = remaining
                            else:
                                unresolved.pop(tool_name, None)
                        if tool_name == "ask_user" and self._premature_source_question(record, state, result.get("message", "")):
                            state["source_clarification_repair"] = True
                            state["messages"].append({"role": "assistant", "content":
                                "Kullanıcı mevcut analize ekleme yapılmasını istedi. Katalogda bulunmaması, resmi kaynak araştırması için yeniden izin istemeyi gerektirmez. "
                                "Bu turda henüz dış kaynak araştırılmadı: mevcut araçlarla resmi kaynağı ara ve oku. "
                                "Dönem, ölçü, birim ve kurum kapsamını güncel active_plan ve active_schema üzerinden koru; güncel workspace_version değerini kullan. "
                                "Kullanıcının adını verdiği tek bir kurum yerine onu da içeren toplu bir sektör grubunu seçenek olarak sunma. "
                                "Kaynak adresi, tutar veya yeni bir kapsam uydurma. Uygun kaynağa ulaşamazsan eksikliği açıklayıp bağlantı iste; gerçek kapsam veya kurum belirsizliğinde seçim sorabilirsin."})
                            self.run_store.event(run_id, "delivery_repair", {"reason": "premature_source_request"})
                            self.run_store.checkpoint(run_id, state)
                            continue
                        missing_facts = self._external_fact_errors(state, "")
                        if missing_facts and _permission_to_research(result.get("message", "")):
                            if (not state.get("ownership_clarification_repair") and state["decisions"] < self.max_decisions
                                    and not _external_research_forbidden(state.get("messages", []))
                                    and any(name in self.tools for name in ("research_web", "inspect_source"))):
                                state["ownership_clarification_repair"] = True
                                state["messages"].append({"role": "assistant", "content":
                                    "Kullanıcı kurumun ilişkilerini zaten sordu; araştırmak için yeniden izin isteme. "
                                    "Önce kurumun ilgili resmi kaynağını oku; kurucu, ortak ve üye rollerini ayırarak doğrulanmış bilgiyi sun. "
                                    "Sonrasında gerekiyorsa yalnız analize eklenecek kurum seçimini sor."})
                                self.run_store.event(run_id, "delivery_repair", {"errors": missing_facts, "reason": "unnecessary_research_permission"})
                                self.run_store.checkpoint(run_id, state)
                                continue
                            return self._finish(record, state, "blocked", self._failure_message(state, missing_facts), errors=missing_facts)
                        # A request to change the question after a full PDF search
                        # must not replace the answer to the original question. If
                        # the requested numbers cannot be verified and the relevant
                        # omission was actually read, close with that exact source
                        # evidence. This also avoids repeating unsupported claims
                        # from a model-generated clarification.
                        if tool_name == "ask_user" and not (state.get("analysis_updated") or state.get("chart_updated") or _successful_bundle(state)):
                            evidence = self._source_final_evidence(state)
                            if evidence:
                                errors = self._task_delivery_errors(state)
                                errors.append({"code": "SOURCE_DATA_NOT_VERIFIED",
                                    "message": "The requested numerical outputs could not be verified from the read source sections."})
                                return self._finish(record, state, "partial", self._source_final_receipt(evidence), errors=errors)
                        # A clarifying question asked AFTER a result was produced this turn
                        # must not bury it behind a dead-end needs_input; present the saved
                        # analysis/chart and surface the question with it instead.
                        if state.get("analysis_updated") or state.get("chart_updated") or _successful_bundle(state):
                            return self._complete(record, state, result["message"], followup=True,
                                                warnings=[{"code": "CLARIFICATION_AFTER_RESULT", "message": "A result was produced this turn; the model's follow-up question is surfaced alongside it rather than pausing for input."}])
                        return self._finish(record, state, "needs_input", result["message"])
                    if result.get("status") in {"blocked", "error", "failed", "unavailable"}:
                        state["repairs"] += 1
                        self.run_store.checkpoint(run_id, state)
                        if state["repairs"] > self.max_repairs or any(e.get("code") in {"UNKNOWN_MUTATION_OUTCOME", "NO_PROGRESS"} for e in result.get("errors", [])):
                            source_stall = next((error.get("source_id") for error in result.get("errors", [])
                                if error.get("code") == "NO_PROGRESS" and error.get("source_id")), None)
                            evidence = self._source_final_evidence(state, stalled_source=source_stall) if source_stall else []
                            if evidence:
                                errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
                                errors.extend(self._task_delivery_errors(state))
                                errors.append({"code": "SOURCE_DATA_NOT_VERIFIED", "message": "Repeated navigation stopped after an explicit source limitation was read; requested numerical outputs remain unproduced."})
                                return self._finish(record, state, "partial", self._source_final_receipt(evidence), errors=errors)
                            refusal = self._barren_refusal(state)
                            if refusal:
                                return self._finish(record, state, "completed", refusal[0], warnings=[refusal[1]])
                            return self._finish(record, state, "blocked", self._failure_message(state, result.get("errors", [])), errors=result.get("errors", []))
                    continue

                if state["decisions"] == self.max_decisions - 1:
                    evidence = self._source_final_evidence(state)
                    if evidence:
                        state["source_final_review"] = evidence
                messages = self._messages(state)
                elapsed = time.monotonic() - invocation_started
                if elapsed >= self.max_elapsed_seconds:
                    return self._finish(record, state, "blocked", "Bu çalışmanın süre sınırına ulaşıldı; istenen adımların tamamı bitirilemedi.", errors=[{"code": "TIME_BUDGET_EXCEEDED", "message": "No further provider request was started after this active invocation's deadline; the total decision budget remains durable."}])
                # Persist the budget debit before network I/O, so a crash cannot
                # reset provider-call limits or pretend a request was free.
                state["decisions"] += 1
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "model_request", {"decision": state["decisions"], "message_count": len(messages)})
                # The MIA live probe confirmed this server option avoids
                # exhausting the bounded output on private reasoning alone.
                tool_schemas = self._model_tool_schemas(state)
                response = self.client.chat(messages, tools=tool_schemas, temperature=0, max_tokens=4096, enable_thinking=False)
                state.pop("source_read_repair_pending", None)
                calls = response.get("tool_calls") or []
                content = response.get("content")
                usage = response.get("usage") or {}
                state["usage"].append(usage)
                self.run_store.event(run_id, "model_response", {"decision": state["decisions"], "finish_reason": response.get("finish_reason"), "tool_names": [c["function"]["name"] for c in calls], "usage": usage, "response_id": response.get("response_id"), "request_meta": response.get("request_meta", {})})
                if state.get("source_final_review"):
                    # This reserved decision cannot reopen the exhausted search.
                    # The bounded receipt contains only literal read evidence;
                    # it never promotes an absent artifact into a completed one.
                    evidence = state["source_final_review"]
                    errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
                    errors.extend(self._task_delivery_errors(state))
                    errors.append({"code": "SOURCE_DATA_NOT_VERIFIED", "message": "The requested data was not verified from the read source sections; requested numerical outputs remain unproduced."})
                    return self._finish(record, state, "partial", self._source_final_receipt(evidence), errors=errors)
                if response.get("finish_reason") == "length":
                    state["repairs"] += 1
                    state["messages"].append({"role": "assistant", "content": "Önceki model çıktısı kesildi; hiçbir araç çalıştırılmadı."})
                    self.run_store.checkpoint(run_id, state)
                    if state["repairs"] > self.max_repairs:
                        return self._finish(record, state, "blocked", "Model çıktısı izin verilen uzunlukta tamamlanamadı.", errors=[{"code": "TRUNCATED_MODEL_OUTPUT", "message": "No truncated tool call was executed."}])
                    continue
                if calls:
                    state["messages"].append({"role": "assistant", "content": content, "tool_calls": copy.deepcopy(calls)})
                    state["pending"] = copy.deepcopy(calls)
                    self.run_store.checkpoint(run_id, state)
                    continue
                if isinstance(content, str) and content.strip():
                    if _unreadable(content):
                        state["repairs"] += 1
                        state["messages"].append({"role": "assistant", "content": "Önceki yanıt okunaksız veya yanlış dildeydi; yalnızca Türkçe, okunabilir bir son cevap gerekiyor."})
                        self.run_store.checkpoint(run_id, state)
                        if state["repairs"] > self.max_repairs:
                            return self._finish(record, state, "blocked", "Model okunabilir bir Türkçe cevap üretemedi.", errors=[{"code": "UNREADABLE_MODEL_OUTPUT", "message": "Final answer failed the charset/language readability check."}])
                        continue
                    missing_outputs = (self._task_delivery_errors(state) + self._numeric_evidence_errors(state, content)
                                       + self._source_semantic_errors(state, content) + self._external_fact_errors(state, content))
                    corrective_errors = self._corrective_delivery_errors(state)
                    if ((missing_outputs or corrective_errors)
                            and not self._sourced_limitation(state, content, missing_outputs + corrective_errors)
                            and (not state.get("unresolved_errors") or corrective_errors)
                            and state.get("delivery_repairs", 0) < 1 and state["decisions"] < self.max_decisions):
                        state["delivery_repairs"] = state.get("delivery_repairs", 0) + 1
                        institutional = [error for error in missing_outputs if error.get("code") in {
                            "INSTITUTIONAL_ROLE_UNVERIFIED", "UNSOLICITED_INSTITUTIONAL_STATISTICS", "UNSOLICITED_OWNERSHIP_PERCENTAGES"}]
                        # Quantitative delivery keeps its normal context/tools.
                        if (institutional and not state.get("analysis_updated") and not state.get("chart_updated")
                                and not set((state.get("task_plan") or {}).get("deliverables", [])) - {"sources", "explanation"}):
                            state["institutional_delivery_repair"] = institutional
                        state["messages"].append({"role": "assistant", "content":
                            "Teslim kontrolü: gerekli kaynak kanıtı veya belirtilen çıktılar henüz oluşmadı. "
                            "Son cevap yerine eksik araç adımlarını tamamla; gereksinimleri azaltma. "
                            + canonical(missing_outputs + corrective_errors)})
                        self.run_store.event(run_id, "delivery_repair", {"errors": missing_outputs + corrective_errors})
                        self.run_store.checkpoint(run_id, state)
                        continue
                    return self._complete(record, state, content)
                state["repairs"] += 1
                state["messages"].append({"role": "assistant", "content": "Model boş yanıt verdi; geçerli bir araç çağrısı veya son cevap gerekiyor."})
                self.run_store.checkpoint(run_id, state)
                if state["repairs"] > self.max_repairs:
                    return self._finish(record, state, "blocked", "Model geçerli bir cevap üretmedi.", errors=[{"code": "EMPTY_MODEL_RESPONSE", "message": "No content or tool calls."}])
            # The last permitted call may itself finish a required source read.
            # Recheck its evidence after pending results have been processed;
            # no additional provider decision or source read is made here.
            evidence = self._source_final_evidence(state)
            if evidence:
                errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
                errors.extend(self._task_delivery_errors(state))
                errors.append({"code": "SOURCE_DATA_NOT_VERIFIED", "message": "Source reading ended within the decision budget with an explicit relevant limitation; requested numerical outputs remain unproduced."})
                return self._finish(record, state, "partial", self._source_final_receipt(evidence), errors=errors)
            if (state.get("analysis_updated") or state.get("chart_updated") or _successful_selection(state)
                    or _successful_bundle(state)) and not state.get("unresolved_errors"):
                return self._complete(record, state, "Analiz kaydedildi; son yanıtı üretme sınırına ulaşıldı.", terminal_status="partial", warnings=[{"code": "FINAL_RESPONSE_BUDGET_EXCEEDED", "message": "Verified analysis is available; no additional provider call was made for prose synthesis."}])
            refusal = self._barren_refusal(state)
            if refusal:
                return self._finish(record, state, "completed", refusal[0], warnings=[refusal[1]])
            if state.get("search_progress", {}).get("paused"):
                errors = [{"code": "SEARCH_STRATEGY_EXHAUSTED", "message": "Search result novelty was exhausted; no successful source-reading recovery completed within the decision budget."}]
                return self._finish(record, state, "blocked", self._failure_message(state, errors), errors=errors)
            return self._finish(record, state, "blocked", "İşlem sınırına ulaşıldığı için analiz tamamlanamadı.", errors=[{"code": "DECISION_BUDGET_EXCEEDED", "message": "Bounded agent decision budget reached."}])
        except MiaError as exc:
            error = {"code": exc.code, "message": str(exc), "retryable": exc.retryable,
                     "attempts": exc.attempts, "usage_unknown": True}
            self.run_store.event(run_id, "provider_error", {key: error[key] for key in ("code", "retryable", "attempts")})
            source_receipt = _verified_source_table_confirmation(state)
            selection_receipt = _selection_confirmation(self.store, self.workspace_id, state)
            bundle_receipt = _bundle_confirmation(self.store, self.workspace_id, state)
            if bundle_receipt:
                return self._finish(record, state, "partial",
                    bundle_receipt + "\n\nSon açıklama adımı sağlayıcı bağlantısı kesildiği için tamamlanamadı. "
                    "Kayıtlı analiz paketi korunuyor ve çalışma yeniden sürdürülebilir.", errors=[error])
            if selection_receipt:
                return self._finish(record, state, "partial",
                    "Son açıklama adımı sağlayıcı bağlantısı kesildiği için tamamlanamadı. "
                    "Kayıtlı satır seçimi korunuyor ve çalışma yeniden sürdürülebilir.", errors=[error])
            if source_receipt and not (state.get("analysis_updated") or state.get("chart_updated")):
                return self._finish(record, state, "partial",
                    source_receipt + "\n\nSon açıklama adımı sağlayıcı bağlantısı kesildiği için tamamlanamadı. "
                    "Kayıtlı kaynak hücreleri korunuyor ve çalışma yeniden sürdürülebilir.", errors=[error])
            return self._finish(record, state, "failed", str(exc), errors=[error])
        except (ValueError, OSError, duckdb.Error) as exc:
            error = error_envelope(exc)
            return self._finish(record, state, "blocked", "Çalışma alanı veya plan doğrulaması tamamlanamadı.", errors=error["errors"])

    def _complete(self, record, state, content, *, followup=False, warnings=None, terminal_status="completed"):
        """One delivery boundary for prose and clarification-after-result exits.

        A model's stop reason is not evidence that the requested artifact exists.
        Numerical analysis prose is built from saved bytes before it reaches the
        user or becomes context for the next turn.
        """
        self._close_pending(state)
        if not state.get("analysis_id") and not state.get("tool_results") and re.search(r"\d", content):
            active_analysis = self.store.workspace(self.workspace_id).get("analysis_head")
            if active_analysis:
                # Numerical followups must read the current saved result rather
                # than reconstructing cells from compact conversation memory.
                state["analysis_id"] = active_analysis
                state["analysis_observed"] = True
        state["delivery_pending"] = {"content": content, "followup": followup,
                                     "warnings": warnings, "terminal_status": terminal_status}
        self.run_store.checkpoint(record["run_id"], state)
        self._default_summary(record, state)
        warnings = [*(warnings or []), *state.get("delivery_warnings", [])]
        errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
        errors.extend(self._task_delivery_errors(state))
        errors.extend(self._numeric_evidence_errors(state, content))
        errors.extend(self._source_semantic_errors(state, content))
        errors.extend(self._external_fact_errors(state, content))
        if state.get("search_progress", {}).get("paused"):
            errors.append({"code": "SEARCH_STRATEGY_EXHAUSTED", "message": "Repeated search results remain unresolved; a relevant source has not been read."})
        if "create_chart" in self.tools and _requests_chart(record["message"]) and not state.get("chart_updated"):
            errors.append({"code": "CHART_NOT_CREATED", "message": "This turn requested a chart but produced no saved chart artifact."})
        source_table = any(
            item.get("result", {}).get("status") == "ok" and (
                item["result"].get("tables") or any(source.get("tables") for source in item["result"].get("sources", [])))
            for item in state.get("tool_results", []))
        if (_requests_table(record["message"]) and not state.get("analysis_updated") and not source_table
                and not _successful_selection(state) and not _successful_bundle(state)):
            # A chart-only edit can legitimately retain the existing table.
            if not state.get("chart_updated"):
                errors.append({"code": "TABLE_NOT_CREATED", "message": "This turn requested a table but produced no saved analysis or inspected source table."})
        quantitative = _analysis_confirmation(self.store, self.workspace_id, state)
        selection = _selection_confirmation(self.store, self.workspace_id, state)
        statistics = _statistics_confirmation(self.store, self.workspace_id, state)
        cells = _cell_confirmation(self.store, self.workspace_id, state)
        chart = _chart_confirmation(state, record["message"])
        scope = _scope_confirmation(self.store, self.workspace_id, state)
        source_scope = _source_scope_confirmation(self.store, self.workspace_id, state, record["message"])
        source_cells = _verified_source_table_confirmation(state, targeted_only=True, provider_outage=False)
        bundle = _bundle_confirmation(self.store, self.workspace_id, state)
        grounded = "\n\n".join(part for part in (
            selection or chart or quantitative, statistics, cells, scope, source_scope, source_cells, bundle) if part)
        web_sources, seen_urls = [], set()
        for item in state.get("tool_results", []):
            result = item.get("result", {})
            if item.get("tool") not in {"research_web", "inspect_source", "read_source_table", "find_source_table_rows"} or result.get("status") != "ok":
                continue
            sources = result.get("sources", []) if item.get("tool") == "research_web" else [result]
            for source in sources:
                readable = _source_read({"status": "ok", **source}) or source.get("content") or (source.get("article") or {}).get("article_body")
                url = source.get("url") or source.get("source_url")
                if readable and url and _search_url(url) not in seen_urls:
                    seen_urls.add(_search_url(url))
                    web_sources.append(source)
        if errors:
            if self._sourced_limitation(state, content, errors):
                return self._finish(record, state, "partial", content, errors=errors,
                                    **({"warnings": warnings} if warnings else {}))
            facts_missing = any(error.get("code") in {"EXTERNAL_FACTS_UNVERIFIED", "UNSOLICITED_OWNERSHIP_PERCENTAGES"} for error in errors)
            has_output = (state.get("analysis_updated") or state.get("chart_updated") or _successful_selection(state)
                          or _successful_bundle(state) or (bool(web_sources) and not facts_missing))
            message = "Bazı istenen adımlar tamamlanamadı; kaydedilen sonuçlar ve hata ayrıntıları korundu."
            if not has_output:
                message = "İstenen işlem tamamlanamadı; yeni bir analiz veya grafik sonucu üretilmedi."
            if any(error.get("code") in _SEARCH_FAILURES | {"EXTERNAL_FACTS_UNVERIFIED", "UNSOLICITED_OWNERSHIP_PERCENTAGES", "SOURCE_READING_INCOMPLETE", "READ_SOURCE_ANSWER_REQUIRED", "INSTITUTIONAL_ROLE_UNVERIFIED", "UNSOLICITED_INSTITUTIONAL_STATISTICS"} for error in errors):
                message = self._failure_message(state, errors)
            source_message = "" if facts_missing else _web_research_message({"sources": web_sources}, grounded)
            partial_receipt = "\n\n".join(part for part in (grounded, source_message) if part)
            if partial_receipt:
                message += "\n\n" + partial_receipt
            return self._finish(record, state, "partial" if has_output else "blocked", message, errors=errors,
                                **({"warnings": warnings} if warnings else {}))
        message = grounded or content
        if set((state.get("task_plan") or {}).get("deliverables", [])) == {"explanation"} and not grounded and not web_sources:
            message = "Genel açıklama (kaynak verilerden hesaplanmış bir sonuç değildir):\n\n" + content
        if followup and grounded:
            # Keep a clearly labeled question separate from computed facts.
            message += "\n\nDevam için soru: " + content
        source_message = _web_research_message({"sources": web_sources}, message)
        if source_message:
            message += "\n\n" + source_message
        return self._finish(record, state, terminal_status, message,
                            **({"warnings": warnings} if warnings else {}))

    def _default_summary(self, record, state):
        """Journal a bounded factual receipt without spending a provider decision.

        Custom calculations stay explicit model tool calls. This fallback never
        chooses a sum, custom period window, or cross-period comparison for the
        user, and cannot satisfy a task contract that requires those operations.
        """
        if "summarize_analysis" not in self.tools or not state.get("analysis_updated"):
            return
        analysis_id = state.get("analysis_id")
        if any(item.get("tool") == "summarize_analysis" and item.get("result", {}).get("analysis_id") == analysis_id
               for item in state.get("tool_results", [])):
            return
        args = {"analysis_id": analysis_id, "statistics": ["first", "last"]}
        call = {"id": "runtime_summary_" + fingerprint(args)[:16], "type": "function", "function": {
            "name": "summarize_analysis", "arguments": canonical(args)}}
        state["automatic_summary_call_id"] = call["id"]
        state["messages"].append({"role": "assistant", "content": None, "tool_calls": [call]})
        state["pending"] = [call]
        self.run_store.checkpoint(record["run_id"], state)
        result = self._dispatch(record["run_id"], state, call)
        state["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                  "content": canonical(_model_tool_result("summarize_analysis", result))})
        state["tool_results"].append({"tool": "summarize_analysis", "call_id": call["id"],
                                      "automatic": True, "result": _compact(result)})
        state["pending"] = []
        if result.get("status") == "ok":
            for key in ("artifact_id", "artifact_ref"):
                if result.get(key):
                    item = {"kind": key, "id": result[key]}
                    if item not in state["artifacts"]:
                        state["artifacts"].append(item)
        else:
            # A default receipt failure does not make a valid table disappear.
            # The failure is visible and cannot satisfy a required summary.
            state.setdefault("delivery_warnings", []).extend(result.get("errors", []))
        self.run_store.checkpoint(record["run_id"], state)

    def _remember_chart_capacity_selection(self, state, args):
        """A capacity error cannot silently remove a proven saved selection."""
        columns = args.get("columns")
        if (not isinstance(columns, list) or not columns or not all(isinstance(name, str) for name in columns)
                or len(columns) != len(set(columns)) or not isinstance(args.get("analysis_id"), str)):
            return
        try:
            from pandas.api.types import is_bool_dtype, is_numeric_dtype
            frame, manifest = self.store.load_analysis(args["analysis_id"])
            group_by = manifest.get("lineage", {}).get("group_by")
            available = {name for name in frame if name not in {"period", "rank", group_by}
                         and is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])
                         and manifest.get("schema", {}).get(name, {}).get("kind") not in {"dimension", "rank"}}
            if manifest.get("workspace_id") != self.workspace_id or not set(columns) <= available:
                return
        except (ValueError, OSError, duckdb.Error):
            return
        required = state.setdefault("chart_capacity_selections", {}).setdefault(args["analysis_id"], [])
        required.extend(name for name in columns if name not in required)

    @staticmethod
    def _chart_coverage_errors(state):
        selections = state.get("chart_capacity_selections", {})
        analysis_id = state.get("analysis_id") or state.get("chart_analysis_id")
        if analysis_id is None and len(selections) == 1:
            analysis_id = next(iter(selections))
        required = selections.get(analysis_id, [])
        actual = (set(state.get("chart_columns", [])) if state.get("chart_updated")
                  and state.get("chart_analysis_id") == analysis_id else set())
        missing = [name for name in required if name not in actual]
        return ([{"code": "CHART_SELECTION_INCOMPLETE", "analysis_id": analysis_id,
                  "missing_columns": missing,
                  "message": "The earlier chart request contained these saved numeric columns, but they were omitted after a capacity error. Restore the selection or report partial completion; a smaller chart does not complete that request."}]
                if missing else [])

    def _task_delivery_errors(self, state):
        plan = state.get("task_plan") or {}
        required = plan.get("deliverables", [])
        chart_coverage_errors = self._chart_coverage_errors(state) + self._normalization_errors(state)
        if not required:
            return chart_coverage_errors
        successful = [item for item in state.get("tool_results", []) if item.get("result", {}).get("status") == "ok"]
        results = [item["result"] for item in successful]
        analysis_sources = False
        analysis_id = state.get("analysis_id")
        if isinstance(analysis_id, str) and state.get("analysis_updated"):
            try:
                _, manifest = self.store.load_analysis(analysis_id)
                analysis_sources = bool((manifest.get("lineage") or {}).get("sources"))
            except (OSError, ValueError, duckdb.Error):
                pass
        evidence = {
            "analysis": bool(state.get("analysis_updated")),
            "bundle": _successful_bundle(state),
            "selection": _successful_selection(state),
            "chart": bool(state.get("chart_updated")),
            "sources": analysis_sources or any(result.get("sources") or result.get("source_id") for result in results),
            "dataset": any(result.get("dataset_id") for result in results),
            "statistics": any(item["tool"] in {"rolling_anomalies", "detect_changes", "analyze_relationship", "summarize_analysis"}
                              and item["result"].get("analysis_id") == state.get("analysis_id") for item in successful),
            "summary": False,
            "explanation": True,
        }
        summary_requirement = plan.get("summary") or {}
        required_methods = (plan.get("statistics") or {}).get("methods", [])
        if required_methods:
            actual_methods = {item["tool"] for item in successful
                              if item["result"].get("analysis_id") == state.get("analysis_id")}
            evidence["statistics"] = set(required_methods).issubset(actual_methods)
        if "summary" in required or summary_requirement:
            from agentic_analytics.agent.tools.summary import SummaryTools
            for item in successful:
                result = item["result"]
                if item["tool"] != "summarize_analysis" or result.get("analysis_id") != state.get("analysis_id"):
                    continue
                summary = SummaryTools(self.store, self.workspace_id).load_artifact(result["artifact_id"])
                parameters = summary["parameters"]
                required_statistics = set(summary_requirement.get("statistics", []))
                required_columns = set(summary_requirement.get("columns", []))
                actual_statistics = {fact["statistic"] for fact in summary["facts"]}
                requested_windows = [(window["start"], window["end"]) for window in summary_requirement.get("windows", [])]
                actual_windows = [(window["start"], window["end"]) for window in parameters.get("windows", [])]
                if (not required_statistics.issubset(actual_statistics)
                        or not required_columns.issubset(parameters.get("columns", []))
                        or (requested_windows and requested_windows != actual_windows)
                        or (summary_requirement.get("compare_windows") and not parameters.get("compare_windows"))):
                    continue
                relevant = [fact for fact in summary["facts"] if fact["statistic"] in required_statistics
                            or (summary_requirement.get("compare_windows") and fact["statistic"].startswith("window_"))]
                if relevant and any(fact.get("value") is None for fact in relevant):
                    continue
                evidence["summary"] = True
                break
        return [{"code": "TASK_DELIVERABLE_MISSING", "deliverable": name,
                 "message": f"The declared task requires {name}; no matching completed output was produced."}
                for name in required if not evidence[name]] + chart_coverage_errors

    def _normalization_errors(self, state):
        """Verify requested common-scale amounts against immutable analysis.

        Only an explicit task/request condition activates this gate. Scale-only
        descendants can fulfill a source amount; ratios, differences and a
        duplicate copy of the same source cannot stand in for another amount.
        """
        requirement = (state.get("task_plan") or {}).get("normalization") or state.get("request_normalization")
        if not requirement:
            return []
        aid = state.get("analysis_id")
        if not aid:
            return [{"code":"NORMALIZATION_ANALYSIS_MISSING", "message":"The requested common-unit/scale comparison has no saved analysis."}]
        from pandas.api.types import is_bool_dtype, is_numeric_dtype
        from agentic_analytics.lakehouse.presentation import column_quantity_lineage
        frame, manifest = self.store.load_analysis(aid)
        if manifest.get("workspace_id") != self.workspace_id:
            raise PlanError("Normalization analysis belongs to another workspace")
        schema, lineage = manifest.get("schema", {}), manifest.get("lineage", {})
        quantities, _, _, _ = column_quantity_lineage(frame, lineage.get("operations", []))
        available = {name for name in frame if is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])
                     and schema.get(name, {}).get("currency") and schema.get(name, {}).get("kind") not in {"ratio", "rate", "growth", "index", "dimension", "rank"}}
        sources = lineage.get("sources", {})
        def identity(name):
            kind, original = quantities[name]
            if kind != "source":
                return canonical({"operation":original})
            meta, source = schema.get(original, {}), sources.get(original, {})
            metric = meta.get("metric_id") or source.get("binding", {}).get("metric_id")
            return canonical({"metric_id":metric, "scope":meta.get("scope"),
                              "dimensions":source.get("dimensions")}) if metric else original
        columns = requirement.get("columns")
        if columns is None:
            candidates = [name for name in sources if name in available and quantities[name] == ("source", name)]
            columns = list({identity(name):name for name in candidates}.values())
            if len(columns) != 2:
                return [{"code":"NORMALIZATION_SELECTION_REQUIRED", "analysis_id":aid, "available_columns":sorted(available),
                         "message":"Bind normalization.columns to the independent amount series requested by the user. More or fewer than two source roots cannot be selected implicitly; exclude percentage ratios and raw/scaled duplicates."}]
        missing = [name for name in columns if name not in available]
        if missing:
            return [{"code":"NORMALIZATION_COLUMNS_INVALID", "analysis_id":aid, "columns":missing,
                     "available_columns":sorted(available), "message":"Normalization columns must be saved monetary amount columns, excluding percent/rate/ratio outputs."}]
        if len({identity(name) for name in columns}) != len(columns):
            return [{"code":"NORMALIZATION_DUPLICATE_SOURCE", "analysis_id":aid,
                     "message":"An original amount and its scaled copy represent one source series, not two independent comparison amounts."}]
        candidates = [[candidate for candidate in available if quantities[candidate] == quantities[name]] for name in columns]
        signatures = [{(schema[name].get("unit"),schema[name].get("currency"),schema[name].get("scale",1))
                       for name in group} for group in candidates]
        common = set.intersection(*signatures)
        if requirement.get("target_scale") is not None:
            common = {signature for signature in common if signature[2] == requirement["target_scale"]}
        if common:
            return []
        metadata = {name:{key:schema[name].get(key) for key in ("unit","currency","scale")} for group in candidates for name in sorted(group)}
        currencies = {schema[name].get("currency") for name in columns}
        return [{"code":"NORMALIZATION_NOT_SATISFIED", "analysis_id":aid, "columns":columns, "saved_units":metadata,
                 "target_scale":requirement.get("target_scale"), "currency_conversion_required":len(currencies)>1,
                 "message":("The saved independent amounts do not share the requested unit, currency and scale. "
                            "Currency conversion is not authorized or performed by scale; obtain an explicit supported conversion or report the limitation."
                            if len(currencies)>1 else
                            "The saved amounts still have different units/scales. Use revise_analysis with scale operations and fresh output aliases at one absolute target_scale, then rebuild the requested chart and source proofs on the revised analysis. Original source values stay unchanged; a correct percentage ratio alone does not satisfy this request.")}]

    @staticmethod
    def _corrective_delivery_errors(state):
        """Offer one final-stage repair for explicit representation errors only.

        No evidence gap, unsafe semantic request or unknown write outcome is
        softened here. Actual corrective tool calls retain the ordinary repair,
        decision, time and no-progress limits.
        """
        hints = {
            "INVALID_UNIT_SEMANTICS": "Kaynak birimini koru: TRY_thousand gibi ölçekli birim için scale=1, ya da temel TRY birimi için scale=1000 kullan; ikisini birlikte uygulama. Kaynak kanıtını değiştirme.",
            "INVALID_UNPIVOT": "Dönemi tahmin etme. Kaynaktaki gerçek başlık satırını ve hücreyi oku; birleşik başlıklarda unpivot.period_sources ile her değer sütununu kaynak row/columns adreslerine eşle veya doğru sütunları seç.",
            "INVALID_SOURCE_DATE_FORMAT": "Kaynak başlığındaki gerçek tarihleri kullan. Hatanın recovery.suggested_unpivot_update önerisini ve kaynak hücre adreslerini incele; bir hücrede birden çok tam tarih varsa uygun date_index seçimini kaynak üzerinden doğrula, tarih uydurma.",
            "INVALID_COLUMN_MAPPING": "Gerçek candidate_columns ve sözleşme sütunlarını kullan. column_mapping kaynak sütunu -> çıktı sütunu yönünde eksiksiz birebir eşleme olmalı; gereksiz yeniden adlandırmada identity mapping kullan.",
            "INVALID_DATASET_GROUP": "Hata içindeki gerçek kategori sütununu group_by olarak seç; value/period gibi ayrılmış çıktı adını grup adı yapma. Tarih ekseni için gereken time_bucket koşullarını koru.",
            "TASK_PLAN_REQUIRES_ANALYSIS": "Henüz gerçek analiz şeması yok. Kullanıcının istediği çıktı türlerini koruyan ayrıntısız plan_task çağır; analizi kaydet, sonra yalnız istenen ve şemaya uygun özet koşullarını ekle.",
            "TASK_PLAN_INVALID_SUMMARY": "Geçersiz yeni özet koşulları kaydedilmedi. Gerçek sütun türlerini ve dönem etiketlerini oku; mevcut plan koşullarını koruyarak yalnız istenen, geçerli hesapları ekle.",
            "TASK_PLAN_INVALID_NORMALIZATION": "Yeni ölçek koşulları kaydedilmedi. Kayıtlı bağımsız tutar sütunlarını seç; oran sütununu veya aynı tutarın ölçekli kopyasını ikinci kaynak sayma. Önceki koşulları koru.",
        }
        errors = [error for failures in state.get("unresolved_errors", {}).values() for error in failures]
        if not errors or any(error.get("code") not in hints for error in errors):
            return []
        return [{**error, "recovery_hint": hints[error["code"]]} for error in errors]

    def _validate_task_summary(self, state, requirement):
        """Never lock a guessed or semantically impossible summary obligation."""
        from pandas.api.types import is_bool_dtype, is_numeric_dtype
        from agentic_analytics.agent.tools.summary import GROWTH_KINDS
        from agentic_analytics.lakehouse.service import _label, _period
        aid = state.get("analysis_id") or self.store.workspace(self.workspace_id).get("analysis_head")
        if not aid:
            raise PlanError("Declare abstract deliverables before a saved analysis exists. Summary details may be added after reading actual saved columns and native periods; no guessed constraints were saved.", code="TASK_PLAN_REQUIRES_ANALYSIS")
        frame, manifest = self.store.load_analysis(aid)
        if manifest.get("workspace_id") != self.workspace_id:
            raise PlanError("Summary plan analysis belongs to another workspace")
        schema = manifest.get("schema", {})
        group = manifest.get("lineage", {}).get("group_by")
        available = [name for name in frame if name not in {"period", "rank", group}
                     and is_numeric_dtype(frame[name]) and not is_bool_dtype(frame[name])]
        columns = requirement.get("columns", available[:6])
        def invalid(message):
            raise PlanError(message + " No new summary constraints were saved.", code="TASK_PLAN_INVALID_SUMMARY")
        if not columns or any(name not in available for name in columns):
            invalid("Summary columns must exist in the saved numeric schema: " + ", ".join(available))
        for name in columns:
            meta = schema.get(name, {})
            for statistic in requirement["statistics"]:
                if statistic in {"sum", "mean", "change", "growth"} and meta.get("status") != "ready":
                    invalid(f"{name}: calculated summaries require reviewed semantics")
                if statistic == "sum" and (meta.get("kind") not in {"flow", "count_flow"} or meta.get("additive_over_time") is False):
                    invalid(f"{name}: period sums require additive, noncumulative flows")
                if statistic == "growth" and meta.get("kind") not in GROWTH_KINDS:
                    invalid(f"{name}: percentage growth is incompatible with {meta.get('kind')}; use a change only if the user requested it, and keep same-period column ratios in analysis")
        windows = requirement.get("windows", [])
        frequency = manifest.get("plan", {}).get("frequency") or manifest.get("lineage", {}).get("frequency")
        labels = frame["period"].astype(str)
        for window in windows:
            for bound in ("start", "end"):
                value = window[bound]
                try:
                    native = value in set(labels) if frequency == "static" else _label(_period(value, frequency)) == value
                except PlanError:
                    native = False
                if not native or value < labels.min() or value > labels.max():
                    invalid(f"Window {bound} must use the saved {frequency} calendar inside {labels.min()} to {labels.max()}; extend the analysis first when needed")
            if window["start"] > window["end"]:
                invalid("Window start must not follow its end")
        if requirement.get("compare_windows") and (len(windows) != 2 or not set(requirement["statistics"]).intersection({"sum", "mean", "first", "last"})):
            invalid("Window comparison needs exactly two native windows and a sum, mean, first or last basis")

    def _numeric_evidence_errors(self, state, content):
        """Reject model-written quantities absent from analysis or direct source bytes."""
        initial = state.get("initial_candidates") or {}
        claims = _material_numeric_literals(content)
        if (not claims or state.get("analysis_id")
                or self.store.workspace(self.workspace_id).get("analysis_head")):
            return []
        source_tools = {"web_search", "research_web", "inspect_source", "find_source_pages",
                        "read_source_table", "find_source_table_rows"}
        source_attempted = any(item.get("tool") in source_tools for item in state.get("tool_results", []))
        if set((state.get("task_plan") or {}).get("deliverables", [])) == {"explanation"} and not source_attempted:
            return []
        for item in state.get("tool_results", []):
            result = item.get("result", {})
            if result.get("status") != "ok":
                continue
            if item.get("tool") in {"summarize_analysis", "explain_value", "rolling_anomalies", "detect_changes", "analyze_relationship"}:
                return []
        source_read, evidence = _direct_source_numeric_values(state)
        if source_read:
            unsupported = [literal for literal, variants in claims if not variants.intersection(evidence)]
            unsupported = list(dict.fromkeys(unsupported))
            if not unsupported:
                return []
            return [{"code": "EXTERNAL_NUMERIC_CLAIM_UNVERIFIED",
                     "message": "The answer contains quantities not present in directly read source text or table cells. "
                                "Read the exact source row, or publish and calculate a transformed value before answering.",
                     "unsupported_numbers": unsupported[:20]}]
        if not source_attempted and (initial.get("no_confident_match") or not initial.get("metrics")):
            return []
        return [{"code": "NUMERICAL_EVIDENCE_MISSING", "message":
                 "The numerical answer has no supporting calculation or directly read source result. Search snippets and metadata are not evidence."}]

    def _source_semantic_errors(self, state, content):
        """Keep document type, financial scope and policy instrument attached to source facts."""
        request = next((str(message.get("content") or "") for message in reversed(state.get("messages", []))
                        if message.get("role") == "user"), "")
        expected_type = _requested_document_type(request)
        requested_scope = _consolidation_scope(request) if expected_type == "financial_report" else None
        if not expected_type and not requested_scope:
            return []

        identities = {}
        documents = []
        row_reads = []

        def add(source, tool):
            if not isinstance(source, dict) or source.get("source_role") == "discovery_index":
                return
            source_id = source.get("source_id")
            if source_id:
                current = identities.setdefault(source_id, {})
                for key in ("source_url", "url", "title", "filename", "raw_sha256", "document_type",
                            "reporting_period", "consolidation_scope", "unit_caption", "publisher"):
                    if source.get(key) and not current.get(key):
                        current[key] = source[key]
            readable = _source_read({"status": "ok", **source})
            if readable:
                documents.append((tool, source))

        for item in state.get("tool_results", []):
            result = item.get("result", {})
            if result.get("status") != "ok":
                continue
            if item.get("tool") == "research_web":
                for source in result.get("sources", []):
                    add(source, item["tool"])
            elif item.get("tool") in {"inspect_source", "read_source_table", "find_source_table_rows"}:
                add(result, item["tool"])
                if item.get("tool") in {"read_source_table", "find_source_table_rows"} and result.get("rows"):
                    row_reads.append(result)

        merged = []
        for tool, source in documents:
            identity = identities.get(source.get("source_id"), {})
            value = {**identity, **source}
            text = " ".join(str(part or "") for part in (
                value.get("title"), value.get("filename"), value.get("source_url") or value.get("url"),
                value.get("text"), value.get("content"), (value.get("article") or {}).get("article_body")))
            value["document_type"] = value.get("document_type") or _document_type(text)
            value["consolidation_scope"] = value.get("consolidation_scope") or _consolidation_scope(text)
            merged.append((tool, value))
        if not merged:
            return []

        matching = [value for _, value in merged if value.get("document_type") == expected_type]
        observed_types = sorted({value.get("document_type") for _, value in merged if value.get("document_type")})
        if expected_type and not matching:
            return [{"code": "SOURCE_DOCUMENT_TYPE_MISMATCH" if observed_types else "SOURCE_DOCUMENT_TYPE_UNVERIFIED",
                     "message": "The directly read source does not establish the requested document type.",
                     "expected_document_type": expected_type, "observed_document_types": observed_types}]

        if requested_scope:
            observed_scopes = sorted({value.get("consolidation_scope") for value in matching
                                      if value.get("consolidation_scope")})
            if requested_scope not in observed_scopes:
                return [{"code": "SOURCE_SCOPE_MISMATCH" if observed_scopes else "SOURCE_SCOPE_UNVERIFIED",
                         "message": "The directly read financial report does not establish the requested consolidation scope.",
                         "expected_scope": requested_scope, "observed_scopes": observed_scopes}]

        if expected_type == "policy_decision" and _material_numeric_literals(content):
            answer = _fact_text(content)
            if re.search(r"ticari\s+kredi|konut\s+kred|ihtiyac\s+kred|commercial\s+loan|mortgage\s+rate|consumer\s+loan", answer):
                return [{"code": "POLICY_RATE_INSTRUMENT_MISMATCH", "message":
                         "The policy decision concerns the one-week repo auction rate, not a commercial or consumer loan rate."}]
            if not re.search(r"bir\s+hafta\s+vadeli\s+repo|one[ -]week\s+repo", answer):
                return [{"code": "POLICY_RATE_INSTRUMENT_UNVERIFIED", "message":
                         "Name the policy-rate instrument as the one-week repo auction rate when reporting this decision."}]

        request_text = _fact_text(request)
        if expected_type == "financial_report" and re.search(r"toplam\s+aktif|total\s+assets?", request_text):
            valid_proof = False
            for result in row_reads:
                identity = identities.get(result.get("source_id"), {})
                proof = {**identity, **result}
                values = " ".join(str(value) for row in result.get("rows", []) if isinstance(row, dict)
                                  for value in (row.get("values") or {}).values() if value is not None)
                if (re.search(r"toplam\s+aktif|total\s+assets?", _fact_text(values))
                        and isinstance(proof.get("page"), int) and proof.get("table_id")
                        and proof.get("raw_sha256") and proof.get("unit_caption") and proof.get("reporting_period")):
                    valid_proof = True
                    break
            if not valid_proof:
                return [{"code": "FINANCIAL_REPORT_CELL_PROOF_INCOMPLETE", "message":
                         "A total-assets answer requires a directly read matching row with physical page, table_id, "
                         "raw source hash, reporting period and explicit unit/scale evidence."}]
        return []

    def _barren_refusal(self, state):
        """Pick a grounded refusal for a run terminating with no saved analysis because
        it looped on a missing row or a genuinely absent concept. Returns
        (message, warning) or None to fall through to the plain terminal. Loop errors
        (NO_PROGRESS) are non-blocking here; any real tool failure suppresses the refusal."""
        if (state.get("analysis_updated") or state.get("chart_updated") or _successful_selection(state)
                or _successful_bundle(state) or state.get("external_facts_required") or _published_source_ids(state)):
            return None
        if any(e.get("code") not in {"NO_PROGRESS"}
               for errors in (state.get("unresolved_errors") or {}).values() for e in errors):
            return None
        if state.get("dimension_barren"):
            return (_row_not_found_refusal(state["dimension_barren"]),
                    {"code": "DIMENSION_VALUE_NOT_FOUND", "message": "Completed as a grounded refusal: the requested dimension value/row was repeatedly not found."})
        if state.get("discovery_barren") and not state.get("saw_ready_candidate"):
            return (_grounded_refusal(state["discovery_barren"]),
                    {"code": "METRIC_NOT_FOUND", "message": "Completed as a grounded refusal: no metric matched the requested concept within the decision budget."})
        return None

    def _expected_plan(self, name, args):
        if name == "execute":
            return copy.deepcopy(args)
        if name == "query_grouped":
            return {"query_type": "grouped", "request": copy.deepcopy(args)}
        if name == "revise_analysis":
            _, parent = self.store.load_analysis(args["analysis_id"])
            if parent.get("workspace_id") != self.workspace_id:
                raise PlanError("Analysis belongs to another workspace")
            if parent["plan"].get("query_type") == "grouped":
                if args.get("add_columns"):
                    raise PlanError("Grouped source additions require an explicit per-group mapping", code="GROUPED_SOURCE_MAPPING_REQUIRED")
                if args.get("start") or args.get("end"):
                    raise PlanError("Grouped period changes require a new grouped query", code="GROUPED_WINDOW_CHANGE_REQUIRED")
                plan = copy.deepcopy(parent["plan"])
                plan["request"].setdefault("operations", []).extend(copy.deepcopy(args.get("operations", [])))
                return plan
            if parent["plan"].get("query_type") == "dataset":
                raise PlanError("Dataset aggregates require a new explicit aggregation", code="DATASET_REVISION_REQUIRED")
            plan = copy.deepcopy(parent["plan"])
            for bound in ("start", "end"):
                if bound in args:
                    plan[bound] = args[bound]
            plan["columns"].extend(copy.deepcopy(args.get("add_columns", [])))
            plan.setdefault("operations", []).extend(copy.deepcopy(args.get("operations", [])))
            return plan
        return None

    def _recover(self, step, definition):
        intent, args, name = step["intent"], step["args"], step["name"]
        if definition.get("recover"):
            return definition["recover"](args, intent)
        if not definition.get("mutating"):
            return None
        if name not in {"execute", "revise_analysis", "query_grouped"}:
            return _blocked("UNKNOWN_MUTATION_OUTCOME", "Interrupted custom write has no reconciliation handler; it will not be repeated.")
        current = self.store.workspace(self.workspace_id)
        if current["revision_id"] == intent["input_revision"]:
            return None
        if current.get("analysis_head"):
            frame, manifest = self.store.load_analysis(current["analysis_head"])
            if manifest.get("workspace_revision_id") == intent["input_revision"] and manifest.get("plan") == intent["expected_plan"]:
                result = self.service._envelope(frame, manifest, [{"code": "RECOVERED_RESULT", "detail": "Previously committed result recovered after interruption; original transient warnings were not journaled."}])
                result["recovered"] = True
                return result
        return _blocked("UNKNOWN_MUTATION_OUTCOME", "Workspace changed after an interrupted tool; automatic replay is blocked.")

    def _dispatch(self, run_id, state, call):
        name = call["function"]["name"]
        step_id = f"{state['decisions']}:{call['id']}"
        definition = self.tools.get(name)
        if definition is None:
            result = _blocked("UNKNOWN_TOOL", "Requested tool is not registered.")
            self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
            return result
        try:
            raw_args = call["function"]["arguments"]
            if not isinstance(raw_args, str) or len(raw_args) > 48000:
                raise PlanError("Tool arguments exceed the request budget")
            args = json.loads(raw_args, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            if name == "plan_task" and isinstance(args, dict) and args.get("summary") == {}:
                # An empty optional container asserts no summary constraints.
                # Preserve the raw call in history and normalize only its
                # executable meaning to the same abstract plan as omission.
                args = {key: value for key, value in args.items() if key != "summary"}
            jsonschema.Draft202012Validator(definition["schema"]["function"]["parameters"]).validate(args)
            if state.get("institutional_delivery_repair") and name not in _INSTITUTIONAL_REPAIR_TOOLS:
                raise PlanError("This institutional answer repair permits source reading only. Explain the verified role/name list and requested analysis suggestions; do not change the analysis or request permission to repeat the same research.", code="INSTITUTIONAL_REPAIR_READ_ONLY")
            if name == "analyze_relationship":
                current_request = next((str(message.get("content") or "") for message in reversed(state.get("messages", []))
                                        if message.get("role") == "user"), "")
                if not _requests_relationship_statistics(current_request):
                    raise PlanError(
                        "The user requested row filtering or ranking, not a correlation, regression or Granger method. "
                        "Use select_analysis_rows with explicit predicates and sorting.",
                        code="UNREQUESTED_STATISTICAL_METHOD",
                    )
            scope_error = _research_scope_error(state, name, args)
            if scope_error:
                result = {"status": "blocked", "errors": [scope_error]}
                self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                return result
            if name == "plan_task" and "normalization" not in args:
                normalization = (state.get("task_plan") or {}).get("normalization") or state.get("request_normalization")
                if normalization:
                    args = {**args, "normalization":copy.deepcopy(normalization)}
            if name in {"prepare_source_table", "publish_selected_table"} and not self._advanced_source_allowed(state, args):
                result = {"status": "blocked", "errors": [{"code": "SOURCE_WORKFLOW_REQUIRED",
                    "source_id": args["source_id"], "table_id": args["table_id"],
                    "message": "Use ingest_source_table for this source/table. Advanced preparation/publication is available only after its explicit unsupported_layout result. For review or semantic refusal, follow the compiler's source-based recovery; low-level ETL cannot bypass it."}]}
                self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                return result
            if name == "plan_task":
                if args.get("summary") and "summary" not in args["deliverables"]:
                    raise PlanError("Summary requirements must include the summary deliverable", code="INVALID_TASK_PLAN")
                if args.get("statistics") and "statistics" not in args["deliverables"]:
                    raise PlanError("Statistical method requirements must include the statistics deliverable", code="INVALID_TASK_PLAN")
                if state.get("task_plan") and not _refines_task_plan(state["task_plan"], args):
                    raise PlanError("The current task contract may only gain detail or stronger requirements; existing constraints and fixed windows cannot be removed or replaced", code="TASK_PLAN_LOCKED")
                if args.get("summary"):
                    self._validate_task_summary(state, args["summary"])
                if args.get("normalization", {}).get("columns"):
                    aid = state.get("analysis_id") or self.store.workspace(self.workspace_id).get("analysis_head")
                    if not aid:
                        raise PlanError("Declare normalization:{same_unit_scale:true} before analysis. Bind columns only after actual saved monetary columns exist; no guessed aliases were locked.", code="TASK_PLAN_REQUIRES_ANALYSIS")
                    invalid = [error for error in self._normalization_errors({**state,"analysis_id":aid,
                        "task_plan":{"normalization":args["normalization"]}})
                        if error["code"] in {"NORMALIZATION_COLUMNS_INVALID","NORMALIZATION_DUPLICATE_SOURCE"}]
                    if invalid:
                        raise PlanError(canonical(invalid) + " No new normalization constraints were saved.", code="TASK_PLAN_INVALID_NORMALIZATION")
            write_key = fingerprint({"name": name, "args": args})
            if definition.get("mutating") and write_key in state.get("successful_writes", {}):
                result = {**state["successful_writes"][write_key], "idempotent_replay": True}
                self.run_store.event(run_id, "tool_reused", {"tool": name, "call_id": call["id"], "result": result})
                return result
            step = self.run_store.step(run_id, step_id)
            if step:
                if step["args"] != args or step["name"] != name:
                    raise PlanError("Persisted call identity changed", code="CALL_ID_CONFLICT")
                if step["result"] is not None:
                    if name in {"web_search", "research_web", "inspect_source", "read_source_table", "find_source_table_rows"}:
                        self._track_search_progress(state, name, step["result"])
                    self._track_source_pages(state, name, args, step["result"], step_id)
                    return step["result"]
                recovered = self._recover(step, definition)
                if recovered is not None:
                    recovered = _normalize_result(recovered)
                    self.run_store.complete_step(run_id, step_id, recovered)
                    self.run_store.event(run_id, "tool_recovered", {"tool": name, "call_id": call["id"], "result": recovered})
                    return recovered
            else:
                navigation_error = self._source_read_required(state, name, args)
                if navigation_error:
                    self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": navigation_error})
                    return navigation_error
                identity = {"name": name, "args": args, "revision": self.store.workspace(self.workspace_id)["revision_id"]}
                if name == "find_source_pages":
                    identity["source_read_epoch"] = len(state.get("source_page_progress", {}).get(args.get("source_id"), {}).get("reads", []))
                key = fingerprint(identity)
                if name == "web_search" and state.get("search_progress", {}).get("paused"):
                    result = _blocked("SEARCH_STRATEGY_EXHAUSTED", "Raw searches are paused because they yielded no new source URLs. Read or research a source instead of rewording the same query.")
                    result["recovery"] = self._search_recovery(state)
                    self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                    return result
                if state["seen"].get(key, 0) >= 2:
                    source_id = args.get("source_id")
                    if (name in {"inspect_source", "read_source_table", "find_source_table_rows"} and not state.get("source_read_repair_used")
                            and state["decisions"] < self.max_decisions
                            and state.get("source_page_progress", {}).get(source_id, {}).get("candidate_read_after_search")):
                        state["source_read_repair_used"] = {"tool": name, "arguments": args, "source_id": source_id}
                        state["source_read_repair_pending"] = True
                        result = {"status": "blocked", "errors": [{"code": "SOURCE_READ_REPEATED", "source_id": source_id,
                            "message": "These exact source pages or rows were already read twice. The repeated call was not executed. Use the retained read evidence to answer, or choose a new source heading/table needed to resolve the question. A search-only absence claim is not evidence."}]}
                        self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                        return result
                    if name == "web_search":
                        state.setdefault("search_progress", {"urls": [], "stale_calls": 0})["paused"] = True
                        result = _blocked("SEARCH_NO_PROGRESS", "The same search has already run twice without new evidence. Continue with a source-reading tool; the repeated search was not executed.")
                        result["recovery"] = self._search_recovery(state)
                        self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                        return result
                    result = _blocked("NO_PROGRESS", "The same tool request has already been attempted twice without a workspace change.")
                    if name in {"find_source_pages", "inspect_source", "read_source_table", "find_source_table_rows"} and source_id:
                        result["errors"][0]["source_id"] = source_id
                    self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
                    return result
                state["seen"][key] = state["seen"].get(key, 0) + 1
                current = self.store.workspace(self.workspace_id)
                intent = {"workspace_id": self.workspace_id, "input_revision": current["revision_id"], "workspace_version": current["version"], "expected_plan": self._expected_plan(name, args)}
                self.run_store.begin_step(run_id, step_id, name, args, intent)
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "tool_started", {"tool": name, "call_id": call["id"], "arguments": args})
            if name in {"execute", "revise_analysis"}:
                plan = step["intent"]["expected_plan"] if step else intent["expected_plan"]
                if plan.get("query_type") == "grouped":
                    # The grouped service validates each member under the same
                    # scalar semantics before committing one grouped revision.
                    result = definition["handler"](args)
                else:
                    validation = self.service.validate_plan(plan)
                    self.run_store.event(run_id, "plan_validation", {"tool": name, "call_id": call["id"], "plan": plan, "validation": validation})
                    if validation.get("status") != "valid":
                        result = validation
                    else:
                        result = definition["handler"](args)
            else:
                result = definition["handler"](args)
            result = _normalize_result(result)
            if name in {"web_search", "research_web", "inspect_source", "read_source_table", "find_source_table_rows"}:
                self._track_search_progress(state, name, result)
            self._track_source_pages(state, name, args, result, step_id)
            if name == "create_chart" and any(error.get("code") == "CHART_SERIES_LIMIT" for error in result.get("errors", [])):
                self._remember_chart_capacity_selection(state, args)
            canonical(result)
        except jsonschema.ValidationError as exc:
            if name == "create_chart" and exc.validator == "maxItems" and list(exc.absolute_path) == ["columns"]:
                self._remember_chart_capacity_selection(state, args)
            result = _schema_validation_error(exc, definition["schema"]["function"]["parameters"])
        except json.JSONDecodeError:
            result = _blocked("INVALID_TOOL_ARGUMENTS", "Tool arguments are incomplete or invalid JSON. No tool was executed; submit one complete JSON object.")
        except (ValueError, OSError, duckdb.Error) as exc:
            result = error_envelope(exc)
        # Only a persisted intent gets a persisted result. Invalid calls still
        # get an event and a matching tool response for provider round trips.
        if self.run_store.step(run_id, step_id):
            self.run_store.complete_step(run_id, step_id, result)
        self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
        return result

    @staticmethod
    def _close_pending(state):
        # Close every tool call before retaining history for the next user turn.
        for call in state["pending"]:
            state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_blocked("TOOL_NOT_EXECUTED", "Run stopped before this call."))})
        state["pending"] = []

    def _finish(self, record, state, status, message, **extra):
        if status in {"blocked", "failed", "partial"} and (state.get("analysis_updated") or state.get("chart_updated")
                or _successful_selection(state) or _successful_bundle(state)):
            status = "partial"
            if not state.get("delivery_pending"):
                try:
                    parts = [_selection_confirmation(self.store, self.workspace_id, state),
                             _analysis_confirmation(self.store, self.workspace_id, state),
                             _statistics_confirmation(self.store, self.workspace_id, state),
                             _cell_confirmation(self.store, self.workspace_id, state),
                             _scope_confirmation(self.store, self.workspace_id, state),
                             _source_scope_confirmation(self.store, self.workspace_id, state, record["message"]),
                             _bundle_confirmation(self.store, self.workspace_id, state)]
                    message += "\n\n" + "\n\n".join(part for part in parts if part)
                except (ValueError, OSError, duckdb.Error) as exc:
                    # An integrity/read failure must never relabel unverified
                    # bytes as a usable partial calculation.
                    status = "blocked"
                    extra["errors"] = [*extra.get("errors", []), *error_envelope(exc)["errors"]]
        if status == "completed" and state.get("task_plan"):
            # Grounded absence/refusal exits must also respect any compound
            # task's declared outputs. They cannot bypass the delivery boundary.
            missing = self._task_delivery_errors(state)
            if missing:
                status = "partial" if (state.get("analysis_updated") or state.get("chart_updated")
                                       or _successful_selection(state) or _successful_bundle(state)) else "blocked"
                extra["errors"] = [*extra.get("errors", []), *missing]
        if status in {"blocked", "failed", "partial"} and not state.get("analysis_updated"):
            try:
                source_receipt = _source_confirmation(self.store, self.workspace_id, state)
                if source_receipt:
                    status = "partial"
                    message = source_receipt + "\n\n" + message
                    message += "\n\nBu kaynaktan istenen analiz ve grafik henüz tamamlanmadı."
                elif not self.store.workspace(self.workspace_id).get("analysis_head") and "Henüz analiz tablosu" not in message:
                    message += "\n\nHenüz analiz tablosu veya grafik oluşturulmadı."
            except (ValueError, OSError, duckdb.Error) as exc:
                status = "blocked"
                extra["errors"] = [*extra.get("errors", []), *error_envelope(exc)["errors"]]
        self._close_pending(state)
        state.pop("delivery_pending", None)
        state.pop("automatic_summary_call_id", None)
        if not state["messages"] or state["messages"][-1].get("role") != "assistant" or state["messages"][-1].get("content") != message:
            state["messages"].append({"role": "assistant", "content": message})
        workspace = self.store.workspace(self.workspace_id)
        result = {"run_id": record["run_id"], "conversation_id": record["conversation_id"], "request_id": record["request_id"],
                  "workspace_id": self.workspace_id, "status": status, "message": message,
                  "analysis_id": state.get("analysis_id"), "analysis_updated": state.get("analysis_updated", False),
                  "active_analysis_id": workspace.get("analysis_head"),
                  "analysis_bundle_id": state.get("analysis_bundle_id"),
                  "bundle_updated": state.get("bundle_updated", False),
                  "chart_id": state.get("chart_id"), "chart_updated": state.get("chart_updated", False),
                  "recommendations": state.get("recommendations", []),
                  "artifacts": state["artifacts"], "tool_results": state["tool_results"],
                  "decisions": state["decisions"], "repairs": state["repairs"], "usage": state["usage"], **extra}
        self.run_store.finish(record["run_id"], state, result)
        self.run_store.event(record["run_id"], "run_finished", {"status": status, "analysis_id": result["analysis_id"], "decisions": state["decisions"]})
        return result
