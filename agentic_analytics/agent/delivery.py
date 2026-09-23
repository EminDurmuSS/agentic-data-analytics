"""Delivery checks and deterministic receipts from immutable analysis artifacts.

The model chooses tools and their arguments. User-visible calculated numbers
are rendered here from verified saved results, never copied from model prose.
"""

import numbers
import json
import re
from urllib.parse import quote, urlsplit

import pandas as pd

from agentic_analytics.lakehouse.presentation import analysis_presentation
from agentic_analytics.agent.tools.documents import _source_dates


def _published_source_ids(state):
    return list(dict.fromkeys(result["dataset_id"] for item in state.get("tool_results", [])
        if item.get("tool") in {"ingest_source_table", "publish_selected_table"}
        and (result := item.get("result", {})).get("status") == "ok"
        and result.get("dataset_id") and result.get("publication_performed") is not False))


def _successful_bundle(state):
    return any(item.get("tool") == "save_analysis_bundle"
               and item.get("result", {}).get("status") == "ok"
               and item.get("result", {}).get("bundle_id")
               for item in state.get("tool_results", []))


def _bundle_confirmation(store, workspace_id, state):
    """Render a compact receipt from a verified multi-analysis bundle."""
    bundle_id = next((item.get("result", {}).get("bundle_id")
                      for item in reversed(state.get("tool_results", []))
                      if item.get("tool") == "save_analysis_bundle"
                      and item.get("result", {}).get("status") == "ok"), None)
    if not bundle_id:
        return ""
    from agentic_analytics.agent.tools.bundles import AnalysisBundleTools
    bundle = AnalysisBundleTools(store, workspace_id).load_bundle(bundle_id)
    lines = [f"Kaydedilen çok frekanslı analiz paketi: {_display_label(bundle['title'])}."]
    for component in bundle["components"]:
        source_labels = []
        for source in component.get("sources", []):
            identity = source.get("metric_id") or source.get("title") or source.get("source_system")
            if not identity:
                continue
            transition = source.get("alignment", "native")
            if source.get("native_frequency") and source.get("output_frequency"):
                transition = f"{source['native_frequency']} -> {source['output_frequency']}, {transition}"
            label = f"{identity} ({transition})"
            if label not in source_labels:
                source_labels.append(label)
        lines.append(
            f"- {_display_label(component['label'])}: {component['frequency']}, "
            f"{_display_period(component['period_start'])} - {_display_period(component['period_end'])}, "
            f"{component['row_count']} satır"
            + (f"; kaynaklar: {', '.join(source_labels)}" if source_labels else "")
            + "."
        )
    allowed, forbidden = [], []
    for component in bundle["components"]:
        for column in component.get("columns", []):
            if not column.get("numeric"):
                continue
            target = f"{component['label']}/{column['name']}"
            (allowed if column.get("additive_over_time") else forbidden).append(target)
    if allowed:
        lines.append("Zaman boyunca toplamaya yalnız metadata tarafından toplamsal olduğu doğrulanan sütunlar uygundur: "
                     + ", ".join(allowed) + ".")
    if forbidden:
        lines.append("Stok, oran veya toplamsallığı doğrulanmamış sütunlar zaman boyunca toplanmaz: "
                     + ", ".join(forbidden) + ".")
    lines.append(
        "Frekans politikası: aylık, haftalık ve çeyreklik bileşenler ayrı immutable analizlerde tutulur; "
        "çeyreklik değer aylara kopyalanmaz. Eksikler sıfır, önceki değer, interpolasyon veya tahminle doldurulmaz."
    )
    return "\n".join(lines)


def _source_confirmation(store, workspace_id, state):
    """Report published source facts without implying a comparison was saved."""
    datasets = set(store.workspace(workspace_id).get("datasets", []))
    lines = []
    for dataset_id in _published_source_ids(state)[-3:]:
        if dataset_id not in datasets:
            raise ValueError("Published source dataset is not part of this workspace")
        manifest = store.dataset_manifest(dataset_id)
        contract = manifest["contract"]
        provenance = contract.get("document_provenance") or {}
        if not provenance.get("source_id") or not {"line_item", "period", "amount"}.issubset(contract["columns"]):
            continue
        frame = pd.read_parquet(store.overlay_path(dataset_id), columns=["line_item", "period", "amount"])
        unit = _display_unit(contract["columns"]["amount"])
        for row in frame.head(3).to_dict("records"):
            lines.append(f"{_display_label(row['line_item'])}, {_display_period(row['period'])}: {_display_number(row['amount'])} {unit}.")
        page = provenance.get("page")
        label = "Özgün kaynak" + (f", s. {page}" if type(page) is int else "")
        url = provenance.get("source_url")
        if isinstance(url, str) and urlsplit(url).scheme in {"http", "https"} and urlsplit(url).hostname:
            url = url.split("#")[0] + (f"#page={page}" if type(page) is int else "")
            lines.append(f"[{label}]({quote(url, safe=':/?&=#%._~-')})")
        else:
            lines.append(label + ": çalışma alanına yüklenen belge.")
        if len(frame) > 3:
            lines.append("Diğer kaynak satırları çalışma alanında kayıtlıdır.")
    return "Kaynakta doğrulanan ve çalışma alanına eklenen değerler (özgün birimleriyle):\n\n" + "\n\n".join(lines) if lines else ""


def _verified_source_table_confirmation(state, *, targeted_only=False, provider_outage=True):
    """Render only cells returned by a successful direct source read.

    This is a provider-outage fallback, not an analytical result. Search result
    snippets and page prose are intentionally ignored. No parsing, arithmetic,
    unit conversion or inferred period is performed here.
    """
    identities = {}
    candidates = []

    def remember(source):
        if not isinstance(source, dict) or not source.get("source_id"):
            return
        source_id = source["source_id"]
        article = source.get("article") or {}
        current = identities.setdefault(source_id, {})
        for key, value in {
            "url": source.get("url") or source.get("source_url"),
            "title": source.get("title") or article.get("title") or source.get("filename"),
            "raw_sha256": source.get("raw_sha256"),
            "publisher": source.get("publisher"),
            "document_type": source.get("document_type"),
            "date_published": source.get("date_published") or article.get("date_published"),
            "reporting_period": source.get("reporting_period"),
            "consolidation_scope": source.get("consolidation_scope"),
            "unit_caption": source.get("unit_caption"),
        }.items():
            if value and not current.get(key):
                current[key] = value

    def add_tables(source, tables, origin):
        if not isinstance(source, dict) or source.get("source_role") == "discovery_index":
            return
        remember(source)
        for table in tables or []:
            if not isinstance(table, dict):
                continue
            rows = table.get("rows") if origin in {"read_source_table", "find_source_table_rows"} else table.get("preview")
            if not isinstance(rows, list) or not rows:
                continue
            normalized = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = row.get("values") if origin in {"read_source_table", "find_source_table_rows"} else row
                if not isinstance(values, dict):
                    continue
                record = dict(values)
                if origin in {"read_source_table", "find_source_table_rows"} and isinstance(row.get("candidate_row"), int):
                    record = {"kaynak_satırı": row["candidate_row"], **record}
                normalized.append(record)
            if normalized:
                candidates.append({
                    "source_id": source.get("source_id"),
                    "raw_sha256": source.get("raw_sha256"),
                    "table_id": table.get("table_id") or source.get("table_id"),
                    "page": table.get("page") or source.get("page"),
                    "columns": table.get("columns") or source.get("columns"),
                    "rows": normalized,
                    "origin": origin,
                })

    for item in state.get("tool_results", []):
        result = item.get("result") or {}
        if result.get("status") != "ok":
            continue
        if item.get("tool") == "research_web":
            for source in result.get("sources", []):
                if isinstance(source, dict):
                    add_tables(source, source.get("tables"), "research_web")
        elif item.get("tool") == "inspect_source":
            add_tables(result, result.get("tables"), "inspect_source")
        elif item.get("tool") in {"read_source_table", "find_source_table_rows"}:
            add_tables(result, [result], item["tool"])

    blocks, seen = [], set()
    # Prefer explicit row reads over short previews, then prefer the latest read.
    priority = {"find_source_table_rows": 3, "read_source_table": 2, "inspect_source": 1, "research_web": 0}
    candidates.sort(key=lambda item: priority[item["origin"]])
    for candidate in reversed(candidates):
        if targeted_only and candidate["origin"] != "find_source_table_rows":
            continue
        source_id = candidate.get("source_id")
        identity = identities.get(source_id, {})
        if (candidate.get("raw_sha256") and identity.get("raw_sha256")
                and candidate["raw_sha256"] != identity["raw_sha256"]):
            continue
        signature = (source_id, candidate.get("table_id"), json.dumps(candidate["rows"], sort_keys=True, ensure_ascii=False))
        if signature in seen:
            continue
        seen.add(signature)
        rows = candidate["rows"][:8]
        declared = candidate.get("columns") if isinstance(candidate.get("columns"), list) else []
        columns = (["kaynak_satırı"] if any("kaynak_satırı" in row for row in rows) else [])
        columns.extend(column for column in declared if column not in columns)
        columns.extend(key for row in rows for key in row if key not in columns)
        columns = columns[:8]
        if not columns:
            continue
        title = identity.get("title") or "Doğrudan okunan kaynak tablosu"
        page = candidate.get("page")
        table_id = candidate.get("table_id")
        label = _display_label(title) + (f", s. {page}" if isinstance(page, int) else "")
        if table_id:
            label += ", tablo " + _display_label(table_id)
        url = identity.get("url")
        try:
            safe_url = isinstance(url, str) and urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).hostname)
        except ValueError:
            safe_url = False
        if safe_url:
            target = url.split("#", 1)[0] + (f"#page={page}" if isinstance(page, int) else "")
            label = f"[{label}]({quote(target, safe=':/?&=#%._~-')})"
        header = "| " + " | ".join(_display_label(column) for column in columns) + " |"
        separator = "| " + " | ".join("---" for _ in columns) + " |"
        body = ["| " + " | ".join(_display_label(row.get(column)) if row.get(column) is not None else "eksik"
                                      for column in columns) + " |" for row in rows]
        facts = [("Yayımlayan", identity.get("publisher")), ("Belge türü", identity.get("document_type")),
                 ("Yayın tarihi", identity.get("date_published")), ("Raporlama dönemi", identity.get("reporting_period")),
                 ("Kapsam", identity.get("consolidation_scope")), ("Birim/ölçek", identity.get("unit_caption"))]
        metadata = "; ".join(f"{name}: {_display_label(value)}" for name, value in facts if value)
        blocks.append(label + ("\n\n" + metadata if metadata else "") + "\n\n" + "\n".join([header, separator, *body]))
        if len(blocks) == 2:
            break
    if not blocks:
        return None
    prefix = ("Sağlayıcı kesintisinden önce " if provider_outage else "")
    return (prefix + "doğrudan kaynak aracının okuduğu hücreler aşağıdadır. "
            "Değerler kaynak metninden aynen taşındı; yeni hesap, yuvarlama veya eksik değer doldurma yapılmadı.\n\n"
            + "\n\n".join(blocks))


def _verified_policy_decision_confirmation(state):
    """Render source-owned policy-decision fields as a presentation table.

    A short user-facing table is not an analytical dataset. This receipt is
    deliberately narrow: it requires a directly read policy-decision document
    and one sentence that binds the one-week repo instrument to both literal
    rates. Search snippets, arithmetic and model-written values are ignored.
    """
    sources = []
    for item in state.get("tool_results", []):
        result = item.get("result") or {}
        if result.get("status") != "ok":
            continue
        if item.get("tool") == "research_web":
            sources.extend(source for source in result.get("sources", []) if isinstance(source, dict))
        elif item.get("tool") == "inspect_source":
            sources.append(result)
    latest_user_message = next((item.get("content", "") for item in reversed(state.get("messages", []))
                                if item.get("role") == "user" and isinstance(item.get("content"), str)), "")
    requested_dates = _source_dates(latest_user_message)

    rate = r"\d{1,3}(?:[.,]\d+)?"
    turkish = re.compile(
        rf"bir\s+hafta\s+vadeli\s+repo(?:\s+ihale)?\s+faiz\s+oran\w*"
        rf".{{0,120}}?(?:y[uü]zde\s*|%\s*)?({rate})\s*[’']?(?:ten|tan|den|dan)"
        rf"\s+(?:y[uü]zde\s*|%\s*)?({rate})\s*[’']?(?:e|a)\b",
        re.I | re.S,
    )
    english = re.compile(
        rf"one[- ]week\s+repo(?:\s+auction)?\s+rate.{{0,120}}?from\s+(?:percent\s*|%\s*)?({rate})"
        rf"\s+to\s+(?:percent\s*|%\s*)?({rate})\b",
        re.I | re.S,
    )
    date_pattern = re.compile(
        r"\b(?:[0-3]?\d)\s+(?:Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|"
        r"Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+(?:19|20)\d{2}\b",
        re.I,
    )

    for source in reversed(sources):
        if source.get("source_role") == "discovery_index" or source.get("document_type") != "policy_decision":
            continue
        article = source.get("article") if isinstance(source.get("article"), dict) else {}
        text = "\n".join(str(value) for value in (
            source.get("text"), source.get("content"), article.get("article_body"),
            *(page.get("text") for page in source.get("pages", []) if isinstance(page, dict)),
        ) if isinstance(value, str) and value.strip())
        source_identity_dates = _source_dates(" ".join(str(value) for value in (
            source.get("reporting_period"), source.get("date_published"), article.get("date_published"),
        ) if value))
        source_dates = source_identity_dates or _source_dates(text)
        if requested_dates and not requested_dates.intersection(source_dates):
            continue
        match = turkish.search(text) or english.search(text)
        if not match:
            continue
        previous, current = (value.replace(".", ",") for value in match.groups())
        decision_date = source.get("reporting_period") or source.get("date_published")
        if not decision_date:
            found_date = date_pattern.search(text)
            decision_date = found_date.group(0) if found_date else None
        rows = []
        if decision_date:
            rows.append(("Karar tarihi", decision_date))
        rows.extend([
            ("Politika aracı", "Bir hafta vadeli repo ihale faiz oranı"),
            ("Önceki oran", "%" + previous),
            ("Yeni oran", "%" + current),
        ])
        table = ["| Öğe | Değer |", "| --- | --- |", *[
            f"| {_display_label(label)} | {_display_label(value)} |" for label, value in rows
        ]]
        url = source.get("source_url") or source.get("url")
        try:
            safe_url = isinstance(url, str) and urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).hostname)
        except ValueError:
            safe_url = False
        citation = (f"\n\n[Resmî politika kararı]({quote(url, safe=':/?&=#%._~-')})"
                    if safe_url else "")
        return ("Doğrudan okunan resmî karar metninden doğrulanan alanlar:\n\n"
                + "\n".join(table) + citation
                + "\n\nBu oran politika faizidir; ticari, konut veya ihtiyaç kredisi faizi değildir.")
    return ""


def _requests_table(message):
    """Recognize explicit table production without blocking questions about tables.

    This gate only establishes that an output must exist. It does not interpret
    financial terms, select metrics, or attempt to grade arbitrary language.
    """
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    # In catalogue requests, "kaynak tablo" / "source table" is commonly one
    # metadata field alongside code, unit and frequency. It does not ask for a
    # newly materialized result table. Keep object forms such as "kaynak
    # tablosunu göster" intact, because those do request the table itself.
    text = re.sub(r"\b(?:kaynak tablo|source table)\b(?=\s*[,;/]|\s+ve\b)", "source_metadata", text)
    noun = re.search(r"\btablo\w*|\btable\b", text)
    action = re.search(r"göster|oluştur|hazirla|getir|listele|istiyorum|isterim|\bshow\b|\bcreate\b|\bproduce\b", text)
    question = re.search(r"ne demek|nedir|nasil (?:okun|yorumlan)|what (?:is|does)|how (?:to|do i) read", text)
    return bool(noun and action and not question)


def _display_number(value):
    """Preserve integers, distinguish nulls from zero, and label display rounding."""
    if value is None or pd.isna(value):
        return "eksik"
    if isinstance(value, numbers.Integral):
        return f"{int(value):,}".replace(",", ".")
    if isinstance(value, numbers.Real):
        if float(value).is_integer():
            return f"{int(value):,}".replace(",", ".")
        if 0 < abs(float(value)) < 0.000001:
            rounded = f"{float(value):.6g}"
            return ("≈ " if float(rounded) != float(value) else "") + rounded.replace(".", ",")
        rendered = f"{float(value):,.6f}".rstrip("0").rstrip(".")
        return ("≈ " if float(f"{float(value):.6f}") != float(value) else "") + rendered.translate(str.maketrans({",": ".", ".": ","}))
    return _display_label(value)


def _display_label(value):
    # Source labels remain literal text instead of injecting markdown structure.
    text = " ".join(str(value).split())[:180]
    return re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", text)


def _display_unit(schema):
    unit = {"TRY": "TL", "percent": "%", "percentage_points": "yüzde puan", "percentage_point": "yüzde puan",
            "person": "kişi", "persons": "kişi", "index": "endeks", "observations": "gözlem", "count": "adet"}.get(schema.get("unit"), schema.get("unit") or "birim belirtilmemiş")
    scale = schema.get("scale") or 1
    prefix = {1: "", 1000: "bin ", 1000000: "milyon ", 1000000000: "milyar "}.get(scale)
    return _display_label((prefix if prefix is not None else f"{_display_number(scale)} × ") + unit)


def _display_period(value):
    text = str(value)
    months = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
    matched = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if matched and 1 <= int(matched[2]) <= 12:
        return (f"{int(matched[3])} " if matched[3] else "") + months[int(matched[2])-1] + " " + matched[1]
    matched = re.fullmatch(r"(\d{4})-Q([1-4])", text)
    return f"{matched[1]} {matched[2]}. çeyrek" if matched else _display_label(text)


def _summary_confirmation(payload, manifest, labels=None):
    """Group verified facts into readable lines without inventing new arithmetic."""
    from agentic_analytics.agent.tools.summary import LABELS
    grouped = {}
    for fact in payload.get("facts", []):
        key = (fact["column"], fact["window"], fact["period_start"], fact["period_end"],
               json.dumps(fact.get("dimensions", {}), sort_keys=True, ensure_ascii=False))
        grouped.setdefault(key, []).append(fact)
    lines = []
    for (column, window, first, last, dimensions_json), facts in list(grouped.items())[:16]:
        dimensions = json.loads(dimensions_json)
        lineage = manifest.get("lineage", {})
        if lineage.get("group_by") and dimensions:
            group_key = json.dumps(dimensions.get(lineage["group_by"]), sort_keys=True, ensure_ascii=False)
            lineage = lineage.get("groups", {}).get(group_key, {})
        binding = lineage.get("sources", {}).get(column, {}).get("binding", {})
        label = _display_label((labels or {}).get(column) or binding.get("title") or column.replace("_", " "))
        if dimensions:
            dimension_labels = binding.get("dimension_labels", {})
            group_labels = [str(dimension_labels.get(name, {}).get(str(value), value)) for name, value in dimensions.items()]
            label += " (" + ", ".join(_display_label(value) for value in group_labels) + ")"
        period = _display_period(first) if first == last else f"{_display_period(first)} - {_display_period(last)}"
        rendered = []
        if first == last and {fact["statistic"] for fact in facts} <= {"first", "last"}:
            facts = facts[:1]
        for fact in facts:
            value = "hesaplanamadı" if fact.get("value") is None else _display_number(fact["value"])
            statistic = "" if first == last and fact["statistic"] in {"first", "last"} else LABELS.get(fact["statistic"], fact["statistic"]) + ": "
            rendered.append(f"{statistic}{value} {_display_unit(fact)}")
        prefix = f"{label}, {_display_label(window)} ({period})"
        lines.append(prefix + ": " + "; ".join(rendered) + ".")
    if len(grouped) > 16:
        lines.append(f"İlk 16 ölçü/dönem grubu gösterildi; {len(grouped)} grubun tamamı kayıtlı özet dosyasında.")
    if payload.get("warnings"):
        lines.append("Eksik veriler sıfır kabul edilmedi; özetin kapsam uyarıları ayrıntılarda yer alıyor.")
    return "\n\n".join(lines)


def _analysis_confirmation(store, workspace_id, state):
    """Read saved bytes again before producing a bounded numerical receipt.

    Summary artifacts can carry requested period totals and comparisons. The
    fallback intentionally gives cell facts, without silently calculating an
    unrequested sum or substituting a last value for a missing period.
    """
    analysis_id = state.get("analysis_id")
    if not analysis_id:
        return None
    frame, manifest = store.load_analysis(analysis_id)
    if manifest.get("workspace_id") != workspace_id:
        raise ValueError("Delivery analysis belongs to another workspace")
    if not state.get("analysis_updated") and not state.get("analysis_observed") and not any(
            item.get("tool") == "summarize_analysis" and item.get("result", {}).get("analysis_id") == analysis_id
            and item.get("result", {}).get("status") == "ok" for item in state.get("tool_results", [])):
        return None
    presentation = analysis_presentation(frame, manifest)
    schema = manifest.get("schema") or {}
    value_columns = [name for name in presentation["columns"] if name not in {"period", "rank"}
                     and schema.get(name, {}).get("kind") not in {"dimension", "rank"}]
    # Prefer numeric summaries; fall back to source values kept verbatim as
    # strings (e.g. a rate stored as "%39.67") so the receipt renders those
    # values instead of an empty period-range header.
    columns = [name for name in value_columns if pd.api.types.is_numeric_dtype(frame[name])] or value_columns
    lines = []
    summaries, seen = [], set()
    for item in state.get("tool_results", []):
        result = item.get("result", {})
        automatic = item.get("automatic") or str(item.get("call_id", "")).startswith("runtime_summary_")
        if item.get("tool") != "summarize_analysis" or automatic or result.get("status") != "ok" or result.get("analysis_id") != analysis_id:
            continue
        artifact_id = result.get("artifact_id") or result.get("artifact_ref")
        if not artifact_id or artifact_id in seen:
            continue
        from agentic_analytics.agent.tools.summary import SummaryTools
        payload = SummaryTools(store, workspace_id).load_artifact(artifact_id)
        if payload.get("analysis_id") != analysis_id or payload.get("workspace_id") != workspace_id:
            raise ValueError("Delivery summary does not belong to the selected analysis")
        seen.add(artifact_id)
        if payload.get("facts"):
            summaries.append(_summary_confirmation(payload, manifest, presentation["labels"]))
    if summaries:
        lines.extend(summaries)
    elif columns and "period" in frame and not frame["period"].duplicated().any() and len(frame):
        ordered = frame.sort_values("period", kind="stable")
        if len(ordered) == 1:
            lines.append(f"{_display_period(ordered.iloc[0]['period'])} için:")
            lines.append("\n".join(f"- {_display_label(presentation['labels'][name])}: **{_display_number(ordered.iloc[0][name])} {_display_unit(schema.get(name, {}))}**."
                                   for name in columns[:8]))
        else:
            first, last = ordered.iloc[0], ordered.iloc[-1]
            lines.append(f"{_display_period(first['period'])} - {_display_period(last['period'])} aralığında:")
            lines.append("\n".join(f"- {_display_label(presentation['labels'][name])}: {_display_number(first[name])} → {_display_number(last[name])} {_display_unit(schema.get(name, {}))}."
                                   for name in columns[:8]))
        if len(columns) > 8:
            lines.append("Diğer ölçüler tabloda yer alıyor.")
    else:
        periods = frame["period"].astype(str) if "period" in frame else None
        period_note = f"{_display_period(periods.iloc[0])}: " if periods is not None and periods.nunique() == 1 else ""
        lines.append(f"{period_note}{len(frame)} kayıt, grup ve dönemleriyle tabloda gösteriliyor.")
    missing = sum(int(frame[name].isna().sum()) for name in columns)
    if missing:
        lines.append(f"{missing} eksik değer var; sıfır veya önceki değerle doldurulmadı.")
    return "\n\n".join(lines)


def _scope_confirmation(store, workspace_id, state):
    """Render saved scope limitations without trusting free-text model claims."""
    if not state.get("analysis_id"):
        return None
    _, manifest = store.load_analysis(state["analysis_id"])
    if manifest.get("workspace_id") != workspace_id:
        raise ValueError("Delivery analysis belongs to another workspace")
    warnings = manifest.get("lineage", {}).get("warnings", [])
    if any(isinstance(note, dict) and note.get("code") == "cross_scope_comparison" for note in warnings):
        return "Oran, seçilen pay ve paydanın sayısal karşılaştırmasıdır. Kaynak kapsamları ayrıca incelenmelidir; resmî sektör veya pazar payı olduğu varsayılmaz."
    if any(isinstance(note, dict) and note.get("code") == "heterogeneous_scopes_aligned" for note in warnings):
        return "Kaynakların dönemleri eşleştirildi; kurum ve raporlama kapsamlarının aynı olduğu varsayılmadı."
    return None


def _source_scope_confirmation(store, workspace_id, state, request):
    """State a requested PDF scope exclusion only from the published table's evidence."""
    question = request.casefold().replace("ı", "i").replace("i\u0307", "i")
    if not re.search(r"takipteki kredi|non.performing loan|\bnpl\b", question) or not state.get("analysis_id"):
        return None
    _, manifest = store.load_analysis(state["analysis_id"])
    if manifest.get("workspace_id") != workspace_id:
        raise ValueError("Delivery analysis belongs to another workspace")
    for source in manifest.get("lineage", {}).get("sources", {}).values():
        provenance = source.get("binding", {}).get("document_provenance") or {}
        for note in provenance.get("source_scope_evidence") or []:
            if not isinstance(note, dict) or note.get("basis") != "selected_pdf_table_footnote":
                continue
            if any(note.get(key) != provenance.get(key) for key in ("source_id", "source_url", "raw_sha256", "page")):
                continue
            if note.get("source_table_id") != (provenance.get("preparation") or {}).get("source_table_id"):
                continue
            quote_text = " ".join(str(note.get("source_quote") or "").split()).casefold()
            if not re.search(r"\bnon.performing loans are not included\b", quote_text):
                continue
            page, url = note["page"], note["source_url"]
            if type(page) is not int or not isinstance(url, str):
                continue
            try:
                safe = urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).hostname) and not any(ord(char) < 32 for char in url)
            except ValueError:
                safe = False
            reference = f"[kaynak, s. {page}]({quote(url.split('#', 1)[0] + f'#page={page}', safe=':/?#&=%+@')})" if safe else f"kaynak, s. {page}"
            return f"Seçilen kredi tablosunun dipnotuna göre takipteki krediler bu tutarlara dahil değil ({reference})."
    return None


def _statistics_confirmation(store, workspace_id, state):
    """Render statistical findings from their hash-verified complete artifacts."""
    tools = {"rolling_anomalies", "detect_changes", "analyze_relationship"}
    lines, seen = [], set()
    for item in state.get("tool_results", []):
        result = item.get("result", {})
        artifact = result.get("artifact_id")
        if item.get("tool") not in tools or result.get("status") != "ok" or not artifact or artifact in seen:
            continue
        from agentic_analytics.agent.tools.statistics import StatisticsTools
        payload = StatisticsTools(store, workspace_id).load_artifact(artifact)
        if payload.get("workspace_id") != workspace_id:
            raise ValueError("Delivery statistics belong to another workspace")
        # Revisions invalidate earlier statistical interpretations in this turn.
        if state.get("analysis_id") and payload.get("analysis_id") != state["analysis_id"]:
            continue
        seen.add(artifact)
        values, params = payload.get("results", {}), payload.get("parameters", {})
        column = _display_label(params.get("column", "seri"))
        if item["tool"] == "rolling_anomalies":
            rows = values.get("rows", [])
            flagged = [str(row["period"]) for row in rows if row.get("anomaly") is True]
            line = f"{column}: geçmiş gözlemlere dayalı yöntem {values.get('anomaly_count', 0)} noktasal anomali işaretledi."
            if flagged:
                line += " İlk işaretlenen dönemler: " + ", ".join(flagged[:8]) + "."
            lines.append(line + " Anomali işareti neden açıklaması değildir.")
        elif item["tool"] == "detect_changes":
            changes = values.get("changes", [])
            lines.append(f"{column}: komşu dönem pencerelerinde {len(changes)} kalıcı medyan değişimi adayı bulundu. Bu geriye dönük tarama istatistiksel anlamlılık veya neden kanıtı üretmez.")
            for change in changes[:5]:
                lines.append(f"{_display_label(change['period'])}: önceki pencere medyanı {_display_number(change['median_before'])}, sonraki pencere medyanı {_display_number(change['median_after'])}.")
        elif params.get("method") in {"pearson", "spearman"}:
            method = "Pearson" if params["method"] == "pearson" else "Spearman"
            period = ""
            if values.get("sample_start_period") and values.get("sample_end_period"):
                period = (f", {_display_label(values['sample_start_period'])} ile "
                          f"{_display_label(values['sample_end_period'])} arasında")
            excluded = values.get("excluded_missing_pairs", 0)
            transform = "birinci farklar" if params.get("transform") == "difference" else "özgün seviyeler"
            lines.append(
                f"{_display_label(params['x'])} ve {_display_label(params['y'])}: "
                f"{values['sample_size']} eşleşmiş gözlemde{period} {method} korelasyonu "
                f"{_display_number(values['correlation'])}; gecikme {params['lag']} dönem, dönüşüm {transform}. "
                f"Eksik olduğu için dışlanan eşleşme: {excluded}. Bu ilişki nedensellik kanıtı değildir. "
                "Zaman serisindeki bağımlılık, bağımsız gözlem varsayımına dayalı anlamlılık hesabını sınırlayabilir."
            )
        else:
            period = ""
            if values.get("sample_start_period") and values.get("sample_end_period"):
                period = (f", {_display_label(values['sample_start_period'])} ile "
                          f"{_display_label(values['sample_end_period'])} arasında")
            lines.append(f"Granger öngörü testi: {values['sample_size']} gözlem{period}, gecikme {params['lag']} dönem, F istatistiği {_display_number(values['f_statistic'])}, p değeri {_display_number(values['p_value'])}. Sıfır hipotezi: geçmiş {_display_label(params['x'])} değerleri {_display_label(params['y'])} için ek öngörü bilgisi sağlamaz. Test ekonomik nedensellik kanıtlamaz.")
    return "\n\n".join(lines) or None


_SELECTION_OPERATORS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "ne": "≠"}


def _selection_conditions(payload, labels):
    """The filters that chose the rows, so a misread condition is visible to the user."""
    def name(column):
        return _display_label(labels.get(column, column.replace("_", " ")))
    parts = []
    for item in (payload.get("parameters") or {}).get("filters") or []:
        if item.get("op") in {"is_null", "not_null"}:
            parts.append(name(item["column"]) + (" boş" if item["op"] == "is_null" else " boş değil"))
        elif item.get("op") in _SELECTION_OPERATORS:
            value = item.get("value")
            right = (name(item["other_column"]) if "other_column" in item else _display_number(value)
                     if isinstance(value, numbers.Real) and not isinstance(value, bool) else _display_label(value))
            parts.append(f"{name(item['column'])} {_SELECTION_OPERATORS[item['op']]} {right}")
    return "Uygulanan koşullar: " + "; ".join(parts) + "." if parts else None


def _selection_confirmation(store, workspace_id, state):
    """Render exact selected rows from hash-verified selection artifacts."""
    lines, seen = [], set()
    for item in state.get("tool_results", []):
        result = item.get("result", {})
        artifact = result.get("selection_id") or result.get("artifact_id")
        if item.get("tool") != "select_analysis_rows" or result.get("status") != "ok" or not artifact or artifact in seen:
            continue
        from agentic_analytics.agent.tools.selection import AnalysisSelectionTools
        payload = AnalysisSelectionTools(store, workspace_id).load_artifact(artifact)
        if state.get("analysis_id") and payload.get("analysis_id") != state["analysis_id"]:
            continue
        frame, manifest = store.load_analysis(payload["analysis_id"])
        if manifest.get("workspace_id") != workspace_id:
            raise ValueError("Selected rows belong to another workspace")
        seen.add(artifact)
        rows = payload.get("rows", [])
        total = payload.get("total_match_count", len(rows))
        comparison_caveat = any(
            isinstance(warning, dict)
            and warning.get("code") == "NUMERIC_EQUALITY_ONLY_ACROSS_DISTINCT_SOURCE_CONTRACTS"
            for warning in payload.get("warnings", [])
        )
        presentation = analysis_presentation(frame, manifest)
        conditions = _selection_conditions(payload, presentation.get("labels", {}))
        if not rows:
            lines.append("Belirtilen koşulların tümünü karşılayan kayıt bulunmadı. Eksik değerler karşılaştırmada sıfır veya farklı değer sayılmadı.")
            if conditions:
                lines.append(conditions)
            if comparison_caveat:
                lines.append("Sütunlar arasında yalnız kayıtlı sayısal eşitlik denetlendi; farklı kaynak kapsamları veya ölçüm temelleri eşdeğer sayılmadı.")
            continue
        columns = payload.get("columns", [])
        headers = []
        for column in columns:
            label = "Dönem" if column == "period" else presentation.get("labels", {}).get(column, column.replace("_", " "))
            meta = payload.get("schema", {}).get(column, {})
            unit = _display_unit(meta) if column != "period" and meta.get("unit") else ""
            headers.append(_display_label(label) + (f" ({unit})" if unit else ""))
        lines.append(f"Koşulları karşılayan kayıtlar ({total}):")
        if conditions:
            lines.append(conditions)
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows[:20]:
            rendered = []
            for column in columns:
                value = row.get(column)
                if value is None:
                    rendered.append("")
                elif column == "period":
                    rendered.append(_display_period(value))
                elif isinstance(value, numbers.Real) and not isinstance(value, bool):
                    rendered.append(_display_number(value))
                else:
                    rendered.append(_display_label(value))
            lines.append("| " + " | ".join(rendered) + " |")
        if len(rows) > 20 or payload.get("truncated"):
            lines.append(f"İlk {min(20, len(rows))} satır gösterildi; tam seçim kaydı indirilebilir.")
        if comparison_caveat:
            lines.append("Sütunlar arasında yalnız kayıtlı sayısal eşitlik denetlendi; farklı kaynak kapsamları veya ölçüm temelleri eşdeğer sayılmadı.")
    return "\n".join(lines) or None


def _cell_confirmation(store, workspace_id, state):
    lines, references, seen = [], [], set()
    incomplete = False
    for item in state.get("tool_results", []):
        result = item.get("result", {})
        if item.get("tool") != "explain_value" or result.get("status") != "ok":
            continue
        if state.get("analysis_id") and result.get("analysis_id") != state["analysis_id"]:
            continue
        frame, manifest = store.load_analysis(result["analysis_id"])
        if manifest.get("workspace_id") != workspace_id:
            raise ValueError("Explained value belongs to another workspace")
        labels = analysis_presentation(frame, manifest)["labels"]
        if not state.get("analysis_updated") and not state.get("analysis_observed"):
            lines.append(f"{_display_label(labels.get(result['column'], result['column']))}, {_display_period(result['period'])}: "
                         f"{_display_number(result['value'])} {_display_unit(result.get('schema') or {})}.")
        sources = manifest.get("lineage", {}).get("sources", {})
        def visit(node):
            if isinstance(node, list):
                for child in node:
                    visit(child)
                return
            if not isinstance(node, dict):
                return
            document = node.get("document_provenance")
            if isinstance(document, dict):
                url, page = document.get("source_url"), document.get("page")
                key = (url or document.get("source_id"), page)
                if key not in seen:
                    seen.add(key)
                    label = f"Kaynak rapor, s. {page}" if page else labels.get(result["column"], "Yüklenen veri") + " kaynağı"
                    references.append((label, url, page))
            elif node.get("source_cells"):
                source = next((entry for entry in sources.values() if entry.get("binding", {}).get("metric_id") == node.get("metric_id")
                               and entry.get("dimensions", {}) == node.get("dimensions", {})), {})
                binding = source.get("binding", {})
                url = binding.get("source_url") or next((entry["source_url"] for entry in binding.get("dimension_labels_evidence", {}).values()
                                                        if isinstance(entry, dict) and entry.get("source_url")), None)
                publisher = str(binding.get("source_system") or "Kayıtlı veri kaynağı").replace("_", " ")
                label = publisher.replace("MONTHLY", "aylık bülten").replace("WEEKLY", "haftalık bülten")
                key = (url or publisher, node.get("dataset_id"))
                if key not in seen:
                    seen.add(key)
                    references.append((label, url, None))
            for key, value in node.items():
                if key not in {"document_provenance", "source_cells"} and isinstance(value, (dict, list)):
                    visit(value)
        visit(result.get("lineage"))
        incomplete |= not result.get("source_references_complete", False)
    if references:
        rendered = []
        for label, url, page in references[:8]:
            try:
                safe = isinstance(url, str) and urlsplit(url).scheme in {"http", "https"} and not any(ord(char) < 32 for char in url)
            except ValueError:
                safe = False
            if safe:
                target = url.split("#", 1)[0] + (f"#page={page}" if page else "")
                rendered.append(f"[{_display_label(label)}]({quote(target, safe=':/?#&=%+@')})")
            else:
                rendered.append(_display_label(label))
        lines.append("Kaynaklar: " + " · ".join(rendered) + ".")
    if incomplete:
        lines.append("Bazı değerlerin kaynak izi eksik; ayrıntılar kaynak panelinde belirtiliyor.")
    return "\n\n".join(lines) or None


def _requests_chart(message):
    """Conservative delivery gate for explicit chart creation/change commands.

    This is not a chart parser: the model still selects the validated spec.
    Questions about an existing chart can be answered without creating a chart.
    """
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
    noun = re.search(r"grafi[kğ]|\bplot\b|\bchart\b|görselleştir|visuali[sz]", text)
    action = re.search(r"çiz|göster|oluştur|hazirla|yap|istiyorum|isterim|çevir|değiştir|dönüştür|eksen|\bbar\b|\bscatter\b|\bdraw\b|\bplot\b|\bcreate\b|\bmake\b|\bshow\b|\bchange\b|görselleştir|visuali[sz]", text)
    return bool(noun and action)


def _chart_confirmation(state, request):
    """Pure display edits acknowledge the saved view without inventing analysis.

    Analytical requests retain their synthesis. Display-only requests get a
    concise receipt from the validated chart spec and grounded recommendations.
    """
    if not state.get("chart_updated") or state.get("analysis_updated"):
        return None
    if not _requests_chart(request) or re.search(r"yorum|neden|analiz et|açıkla|acikla", request.casefold()):
        return None
    allowed = {"create_chart", "discover", "describe", "dimension_values", "explain_value"}
    if any(item["tool"] not in allowed for item in state["tool_results"]):
        return None
    saved = next((item["result"] for item in reversed(state["tool_results"])
                  if item["tool"] == "create_chart" and item["result"].get("status") == "ok"), None)
    if not saved:
        return None
    spec = saved["spec"]
    kind = {"line": "Çizgi", "bar": "Çubuk", "area": "Alan", "scatter": "Dağılım", "heatmap": "Isı haritası"}.get(spec["kind"], "Analiz")
    lines = [f"{kind} grafiği kaydedildi: {saved['title']}. {saved['row_count']} kayıt kullanıldı."]
    if spec.get("normalize") == "index100":
        lines.append(f"Seçili seriler aynı {spec['base_period']} döneminde 100 kabul edilerek karşılaştırılıyor. Bu görünüm enflasyondan arındırma değildir.")
    elif spec.get("layout") == "panels":
        lines.append("Seriler ayrı panellerde, kendi birimleriyle gösteriliyor.")
    elif spec.get("layout") == "dual_axis":
        lines.append("İki eksen ayrı birim ve ölçeklerle etiketlendi; serileri kendi eksenlerinden okuyun.")
    if spec["kind"] == "scatter":
        lines.append("Noktalar aynı döneme ait gözlemleri eşler; bu görünüm nedensellik kanıtı değildir.")
    lines.append("Kayıtlı tablonun değerleri korundu.")
    return "\n\n".join(lines)
