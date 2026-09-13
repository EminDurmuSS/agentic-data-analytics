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


def _published_source_ids(state):
    return list(dict.fromkeys(result["dataset_id"] for item in state.get("tool_results", [])
        if item.get("tool") in {"ingest_source_table", "publish_selected_table"}
        and (result := item.get("result", {})).get("status") == "ok"
        and result.get("dataset_id") and result.get("publication_performed") is not False))


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


def _requests_table(message):
    """Recognize explicit table production without blocking questions about tables.

    This gate only establishes that an output must exist. It does not interpret
    financial terms, select metrics, or attempt to grade arbitrary language.
    """
    text = message.casefold().replace("ı", "i").replace("i\u0307", "i")
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
    columns = [name for name in presentation["columns"] if name != "period" and pd.api.types.is_numeric_dtype(frame[name])
               and schema.get(name, {}).get("kind") not in {"dimension", "rank"} and name != "rank"]
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
    elif "period" in frame and not frame["period"].duplicated().any() and len(frame):
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
        elif params.get("method") == "pearson":
            lines.append(f"{_display_label(params['x'])} ve {_display_label(params['y'])}: {values['sample_size']} eşleşmiş gözlemde Pearson korelasyonu {_display_number(values['correlation'])}; gecikme {params['lag']} dönem. Bu ilişki nedensellik kanıtı değildir. Zaman serisindeki bağımlılık, bağımsız gözlem varsayımına dayalı anlamlılık hesabını sınırlayabilir.")
        else:
            lines.append(f"Granger öngörü testi: {values['sample_size']} gözlem, gecikme {params['lag']} dönem, p değeri {_display_number(values['p_value'])}. Test, geçmiş {_display_label(params['x'])} değerlerinin {_display_label(params['y'])} için ek öngörü bilgisiyle ilişkisini ölçer; ekonomik nedensellik kanıtlamaz.")
    return "\n\n".join(lines) or None


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
