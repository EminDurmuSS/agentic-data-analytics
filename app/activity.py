"""Safe, user-facing projections of durable agent journal events."""
from __future__ import annotations

import json
from pathlib import PurePath
import re
from urllib.parse import urlsplit


TOOL_DESCRIPTIONS = {
    "discover": "İlgili veriler aranıyor",
    "describe": "Verinin anlamı ve kapsamı inceleniyor",
    "dimension_values": "İl ve kurum değerleri bulunuyor",
    "validate_plan": "Hesap planı denetleniyor",
    "execute": "Veri sorgulanıyor ve hesaplanıyor",
    "revise_analysis": "Önceki analiz güncelleniyor",
    "explain_value": "Kaynak izi okunuyor",
    "query_grouped": "Gruplar karşılaştırılıyor",
    "inspect_source": "Yeni kaynak inceleniyor",
    "ingest_source_table": "Finansal tablo kaynak hücrelerinden doğrulanıp ekleniyor",
    "publish_selected_table": "Doğrulanan tablo ekleniyor",
    "web_search": "Web kaynakları araştırılıyor",
    "research_web": "Resmi web kaynakları araştırılıyor",
    "ask_user": "Kullanıcıya kısa bir soru soruluyor",
    "rolling_anomalies": "Olağandışı dönemler aranıyor",
    "detect_changes": "Değişim noktaları inceleniyor",
    "analyze_relationship": "Değişkenler arasındaki ilişki hesaplanıyor",
    "create_chart": "Grafik hazırlanıyor",
    "plan_task": "İstenen sonuçlar ve hesap adımları planlanıyor",
    "summarize_analysis": "Dönem toplamları ve karşılaştırmalar doğrulanıyor",
    "aggregate_dataset": "Yeni verinin grupları ve dönemleri hesaplanıyor",
    "prepare_source_table": "Kaynak tablosunun yapısı düzenleniyor",
    "combine_source_tables": "Devam eden kaynak tabloları birleştiriliyor",
    "read_source_table": "Kaynağın özgün tablo satırları okunuyor",
    "find_source_pages": "Belgede ilgili sayfalar aranıyor",
}

# Turkish titles for the terminal run status, so a blocked/failed run is not
# mislabeled as "tamamlandı".
_RUN_STATUS_TITLES = {
    "completed": "Agent çalışması tamamlandı",
    "partial": "Agent çalışması kısmen tamamlandı",
    "needs_input": "Agent kullanıcı girdisi bekliyor",
    "blocked": "Agent çalışması engellendi",
    "failed": "Agent çalışması tamamlanamadı",
}


def _tool_description(name):
    return TOOL_DESCRIPTIONS.get(name, "Kayıtlı agent fonksiyonu uygulanıyor")


def _activity(event):
    """Project one stored event without exposing arguments, SQL, or model text."""
    kind = event.get("kind", "unknown")
    payload = event.get("payload") or {}
    names = payload.get("tool_names") or []
    tool = payload.get("tool")
    status = None
    if isinstance(payload.get("result"), dict):
        status = payload["result"].get("status")
    if kind == "run_started":
        title, detail = "Agent çalışması başlatıldı", "Soru için kayıtlı analiz akışı açıldı."
    elif kind == "model_request":
        title = "Qwen modeli çağrılıyor"
        detail = f"Karar {payload.get('decision', '?')} için bir sonraki fonksiyon seçiliyor."
    elif kind == "model_response":
        if names:
            title = "Qwen fonksiyon seçti"
            detail = ", ".join(f"{name} — {_tool_description(name)}" for name in names)
        elif payload.get("finish_reason") == "length":
            title, detail = "Qwen yanıtı kesildi", "Çıktı uzunluk sınırına takıldı; fonksiyon çalıştırılmadı."
        else:
            title, detail = "Qwen nihai yanıtı üretti", "Yeni bir fonksiyon çağrısı istemedi."
    elif kind == "tool_started":
        title, detail = f"{tool} çalıştırılıyor", _tool_description(tool)
    elif kind == "plan_validation":
        if isinstance(payload.get("validation"), dict) and payload["validation"].get("status") not in {"valid", "ok", None}:
            title, detail = f"{tool} planı reddedildi", "Hesap planı veri sözleşmelerini geçemedi."
        else:
            title, detail = f"{tool} planı doğrulanıyor", "Hesap planı veri sözleşmelerine göre kontrol ediliyor."
    elif kind == "tool_result":
        if status in {"blocked", "error", "failed", "unavailable"}:
            title, detail = f"{tool} tamamlanamadı", "Fonksiyon hata veya güvenlik engeliyle sonuçlandı."
        else:
            title, detail = f"{tool} tamamlandı", _tool_description(tool)
    elif kind == "tool_reused":
        title, detail = f"{tool} kayıtlı sonuçtan kullanıldı", "Aynı yazma işlemi tekrar uygulanmadı."
    elif kind == "tool_recovered":
        title, detail = f"{tool} kayıtlı sonuçtan kurtarıldı", "Kesintiden önceki doğrulanmış sonuç kullanıldı."
    elif kind == "run_finished":
        run_status = payload.get("status")
        title = _RUN_STATUS_TITLES.get(run_status, "Agent çalışması sona erdi")
        detail = f"Son durum: {run_status or 'bilinmiyor'}."
    else:
        title, detail = "Kayıtlı agent olayı", f"Olay türü: {kind}."
    return {
        "seq": event.get("seq"),
        "kind": kind,
        "title": title,
        "detail": detail,
        "tool": tool,
        "tool_names": names,
        "status": status,
        "created_at": event.get("created_at"),
    }


def activity_feed(events):
    """Return one visible, safe activity item for every durable journal event."""
    return [_activity(event) for event in events]


_STAGES = {"sources": "Kaynaklar", "data": "Veri hazırlığı", "calculation": "Hesaplama",
           "checks": "Kontroller", "presentation": "Sunum"}
# Tool names and payload text remain in the separate technical ledger. These
# labels describe observable work, never the model's private reasoning.
_JOURNEY_TOOLS = {
    "discover": ("sources", "catalog", "Veri seçimi", "İlgili veriler aranıyor"),
    "describe": ("sources", "catalog", "Veri seçimi", "Verinin kapsamı inceleniyor"),
    "dimension_values": ("sources", "catalog", "Veri seçimi", "Karşılaştırma grupları inceleniyor"),
    "web_search": ("sources", "web", "Web araştırması", "Web kaynakları aranıyor"),
    "research_web": ("sources", "web", "Web araştırması", "Web kaynakları inceleniyor"),
    "inspect_source": ("sources", "document", "Belge incelemesi", "Belgenin seçili bölümleri inceleniyor"),
    "find_source_pages": ("sources", "document", "Belge incelemesi", "Belgede ilgili sayfalar aranıyor"),
    "read_source_table": ("sources", "document", "Belge incelemesi", "Seçili tablo satırları okunuyor"),
    "prepare_source_table": ("data", "preparation", "Tablo düzeni", "Kaynak tablosu düzenleniyor"),
    "combine_source_tables": ("data", "preparation", "Tablo düzeni", "Devam eden tablolar birleştiriliyor"),
    "ingest_source_table": ("data", "publication", "Verinin eklenmesi", "Seçili kaynak verisi hazırlanıyor"),
    "publish_selected_table": ("data", "publication", "Verinin eklenmesi", "Seçili kaynak verisi ekleniyor"),
    "execute": ("calculation", "analysis", "Analiz tablosu", "Veri sorgulanıyor ve hesaplanıyor"),
    "revise_analysis": ("calculation", "analysis", "Analiz tablosu", "Analiz tablosu güncelleniyor"),
    "query_grouped": ("calculation", "analysis", "Analiz tablosu", "Gruplar karşılaştırılıyor"),
    "aggregate_dataset": ("calculation", "analysis", "Analiz tablosu", "Seçili veriler tabloya aktarılıyor"),
    "summarize_analysis": ("calculation", "summary", "Sonuç özeti", "Kayıtlı sonuçlar özetleniyor"),
    "validate_plan": ("checks", "validation", "Hesap kuralları", "Birim ve dönem kuralları kontrol ediliyor"),
    "explain_value": ("checks", "lineage", "Kaynak bağlantıları", "Değerlerin kaynak bağlantıları inceleniyor"),
    "rolling_anomalies": ("checks", "statistics", "İstatistiksel inceleme", "Olağandışı dönemler inceleniyor"),
    "detect_changes": ("checks", "statistics", "İstatistiksel inceleme", "Değişim noktaları inceleniyor"),
    "analyze_relationship": ("checks", "statistics", "İstatistiksel inceleme", "Değişkenler arasındaki ilişki hesaplanıyor"),
    "create_chart": ("presentation", "chart", "Grafik", "Grafik hazırlanıyor"),
    "ask_user": ("presentation", "question", "Sizin tercihiniz", "Devam etmek için bilginiz bekleniyor"),
}
_JOURNEY_STATUS = {
    "running": ("Çalışma sürüyor", "Tamamlanan adımlar burada görünür; sıradaki işlem hazırlanıyor."),
    "completed": ("Sonuç hazır", "Kaynak, hesap ve çıktı adımlarının özeti."),
    "partial": ("Sonuç kısmen hazır", "Elde edilen sonuçlar korunuyor; isteğin tamamı karşılanamadı."),
    "needs_input": ("Bilginiz gerekiyor", "Devam etmek için yanıtınız bekleniyor."),
    "blocked": ("Çalışma tamamlanamadı", "Açık kalan koşullar nedeniyle bu noktada duruldu."),
    "failed": ("Çalışma kesildi", "Tamamlanan adımlar korunuyor; işlem sona ulaşamadı."),
}


def _safe_name(value):
    """Allow bounded source metadata, never paths, URLs or arbitrary prose."""
    if not isinstance(value, str):
        return ""
    value = PurePath(value.replace("\\", "/")).name
    value = " ".join(re.sub(r"[\x00-\x1f\x7f<>]", "", value).split())[:90]
    return "" if re.match(r"^(?:source_|analysis_|dataset_|[a-f0-9]{32})", value) else value


def _host(value):
    try:
        host = urlsplit(value).hostname if isinstance(value, str) else None
        return (host or "")[:90]
    except ValueError:
        return ""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000000000


def _attempt_scope(attempt, parents):
    args, result = attempt["args"], attempt["result"]
    action = "publication" if attempt["tool"] in {"ingest_source_table", "publish_selected_table"} else attempt["tool"]
    source = args.get("source_id") or result.get("source_id")
    table = args.get("table_id") or result.get("table_id")
    seen = set()
    while (source, table) in parents and table not in seen:
        seen.add(table)
        table = parents[source, table]
    if source:
        # A later read of a different range is not proof that a failed range
        # was recovered. Publication retries may refine the selected cells.
        ranges = {key: args[key] for key in ("pages", "page_numbers", "row_start", "limit", "start_page", "page_limit", "query") if key in args}
        return (action, "source", source, table, json.dumps(ranges, sort_keys=True))
    aid = args.get("analysis_id") or result.get("analysis_id")
    if aid:
        return (action, "analysis", aid, args.get("column"), args.get("period"))
    selection = {key: args[key] for key in ("metric_id", "query", "start", "end", "frequency", "columns", "group_by") if key in args}
    if selection:
        return (action, "selection", json.dumps(selection, sort_keys=True, default=str))
    # Missing arguments, including schema-rejected calls, cannot establish
    # that a different successful call resolved this particular attempt.
    return (action, "call", attempt["call_id"], attempt["index"])


def _attempt_outcome(attempt):
    if not attempt["finished"]:
        return "active"
    tool, result = attempt["tool"], attempt["result"]
    if tool == "ask_user":
        return "attention"
    if result.get("status") not in {"ok", "valid"}:
        return "attention"
    if tool in {"ingest_source_table", "publish_selected_table"}:
        if not result.get("dataset_id") or result.get("publication_performed") is False:
            return "attention"
    if tool in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"} and not result.get("analysis_id"):
        return "attention"
    if tool == "create_chart" and (not (result.get("chart_id") or result.get("artifact_id")) or result.get("complete") is False):
        return "attention"
    if tool == "explain_value" and result.get("source_references_complete") is False:
        return "attention"
    return "complete"


def _document_evidence(attempts):
    names, pages, rows = [], set(), set()
    for attempt in attempts:
        result, args = attempt["result"], attempt["args"]
        name = _safe_name(result.get("filename")) or _host(result.get("source_url"))
        if name and name not in names:
            names.append(name)
        source = result.get("source_id") or args.get("source_id") or attempt["index"]
        if attempt["tool"] == "inspect_source":
            for page in result.get("processed_pages", []):
                if _integer(page):
                    pages.add((source, page))
        if attempt["tool"] == "read_source_table":
            for index, row in enumerate(result.get("rows", [])):
                address = row.get("candidate_row") if isinstance(row, dict) else None
                rows.add((source, result.get("table_id") or args.get("table_id"),
                          address if _integer(address) else (attempt["index"], index)))
    return names, pages, rows


def _journey_detail(key, attempts):
    good = [attempt for attempt in attempts if _attempt_outcome(attempt) == "complete"]
    results = [attempt["result"] for attempt in good]
    if key == "document":
        names, pages, rows = _document_evidence(good)
        detail = ", ".join(names[:2])
        facts = ([f"{len(pages)} sayfanın içeriği incelendi"] if pages else [])
        if rows:
            facts.append(f"seçili tablolardan {len(rows)} satır okundu")
        return ((detail + ": ") if detail else "") + (", ".join(facts) if facts else "Belgenin seçili bölümleri incelendi.")
    if key == "publication":
        datasets = {result["dataset_id"]: result for result in results}
        count = sum(result.get("row_count", 0) for result in datasets.values() if _integer(result.get("row_count")))
        return (f"{len(datasets)} veri kümesi, {count} satır eklendi." if count else f"{len(datasets)} veri kümesi eklendi.") if datasets else "Kaynak henüz analiz verisine eklenmedi."
    if key == "analysis":
        last = results[-1] if results else {}
        count = last.get("row_count")
        return f"{count} satırlık analiz tablosu kaydedildi." if _integer(count) else "Analiz tablosu kaydedildi."
    if key == "web":
        hosts = sorted({_host(source.get("url") or source.get("source_url")) for result in results
                        for source in result.get("sources", []) if isinstance(source, dict)} - {""})
        return (", ".join(hosts[:3]) + " kaynakları incelendi.") if hosts else "Web araması tamamlandı; bulunan kaynaklar değerlendirildi."
    if key == "lineage":
        count = len({(attempt["args"].get("analysis_id"), attempt["args"].get("column"), attempt["args"].get("period")) for attempt in good})
        return f"{count} değerin kayıtlı kaynak bağlantısı incelendi." if count else "Kaynak bağlantısı tamamlanamadı."
    return {"catalog": "İlgili veri adayları ve kapsam bilgileri incelendi.",
            "preparation": "Seçili tablo düzeni hazırlandı; verinin eklenmesi ayrı adımda izlenir.",
            "plan": "İstenen çıktılar için işlem planı kaydedildi.",
            "summary": "Özet, kayıtlı analiz değerlerinden hazırlandı.",
            "validation": "Hesap planı birim ve dönem kurallarından geçti.",
            "statistics": "Seçilen istatistiksel yöntemler uygulandı.",
            "chart": "Kayıtlı analizden grafik hazırlandı.",
            "question": "Devam etmek için yanıtınız bekleniyor."}.get(key, "Kayıtlı işlem sonuçlandı.")


def _stage_summary(stage, stage_status, attempts):
    active = [attempt for attempt in attempts if _attempt_outcome(attempt) == "active"]
    if stage_status == "active" and active:
        return _JOURNEY_TOOLS[active[-1]["tool"]][3] + "."
    good = [attempt for attempt in attempts if _attempt_outcome(attempt) == "complete"]
    if stage_status == "attention":
        return "Bazı denemeler sonuçlanmadı; ayrıntılar bu aşamada yer alıyor."
    if stage == "sources":
        _, pages, _ = _document_evidence(good)
        return f"{len(pages)} seçili sayfa ve ilgili kaynak bilgileri incelendi." if pages else "İlgili kaynaklar ve kapsam bilgileri incelendi."
    if stage == "data":
        published = [attempt for attempt in good if _JOURNEY_TOOLS[attempt["tool"]][1] == "publication"]
        return _journey_detail("publication", published) if published else "Seçili kaynak tablosunun düzeni hazırlandı."
    if stage == "calculation":
        analysis = [attempt for attempt in good if _JOURNEY_TOOLS[attempt["tool"]][1] == "analysis"]
        if analysis:
            return _journey_detail("analysis", analysis)
        return "Kayıtlı sonuçlar özetlendi." if any(a["tool"] == "summarize_analysis" for a in good) else "İstenen çıktılar için işlem planı kaydedildi."
    if stage == "checks":
        keys = {_JOURNEY_TOOLS[attempt["tool"]][1] for attempt in good}
        if keys == {"lineage"}:
            return _journey_detail("lineage", good)
        return "Seçilen hesap ve kaynak kontrolleri uygulandı."
    return "Analiz sonuçları grafiğe aktarıldı."


def activity_journey(events, run_status=None):
    """Summarize observable attempts without altering or exposing raw evidence."""
    events = list(events)
    terminal = next((event.get("payload", {}).get("status") for event in reversed(events)
                     if event.get("kind") == "run_finished"), None)
    status = run_status or terminal or "running"
    status = {"queued": "running", "interrupted": "failed", "finished": terminal or "completed"}.get(status, status)
    if status not in _JOURNEY_STATUS:
        status = "failed"
    attempts, parents = [], {}
    for index, event in enumerate(events):
        payload = event.get("payload") or {}
        kind, tool = event.get("kind"), payload.get("tool")
        if tool not in _JOURNEY_TOOLS:
            continue
        if kind == "plan_validation":
            tool = "validate_plan"
            attempt = {"tool": tool, "call_id": payload.get("call_id"), "index": index,
                       "args": payload.get("plan") or {}, "result": payload.get("validation") or {}, "finished": True, "recovered": False}
            attempts.append(attempt)
            continue
        if kind not in {"tool_started", "tool_result", "tool_reused", "tool_recovered"}:
            continue
        call_id = payload.get("call_id")
        attempt = next((item for item in reversed(attempts) if item["tool"] == tool
                        and item["call_id"] == call_id and not item["finished"]), None)
        if attempt is None:
            attempt = {"tool": tool, "call_id": call_id, "index": index,
                       "args": payload.get("arguments") or {}, "result": {}, "finished": False, "recovered": False}
            attempts.append(attempt)
        if kind != "tool_started":
            result = payload.get("result") or {}
            attempt.update(result=result, finished=True, recovered=kind in {"tool_recovered", "tool_reused"})
            if tool == "prepare_source_table" and result.get("status") == "ok":
                parent = (result.get("preparation") or {}).get("source_table_id")
                if result.get("source_id") and result.get("table_id") and parent:
                    parents[result["source_id"], result["table_id"]] = parent
    groups = {}
    for attempt in attempts:
        stage, key, label, active = _JOURNEY_TOOLS[attempt["tool"]]
        groups.setdefault((stage, key), []).append(attempt)
    stages = []
    for stage, label in _STAGES.items():
        items = []
        for (item_stage, key), group in groups.items():
            if item_stage != stage:
                continue
            failures, resolved, active = [], [], []
            for position, attempt in enumerate(group):
                outcome = _attempt_outcome(attempt)
                if outcome == "attention":
                    later = group[position + 1:]
                    fixed = any(_attempt_outcome(other) == "complete" and _attempt_scope(other, parents) == _attempt_scope(attempt, parents) for other in later)
                    (resolved if fixed else failures).append(attempt)
                elif outcome == "active":
                    active.append(attempt)
            item_status = "attention" if failures else "active" if active and status == "running" else "attention" if active else "complete"
            has_result = any(_attempt_outcome(attempt) == "complete" for attempt in group)
            detail = _journey_detail(key, group) if has_result or key in {"publication", "question"} else ""
            if active:
                detail = _JOURNEY_TOOLS[active[-1]["tool"]][3] + ("." if status == "running" else "; bu adım sonuçlanmadan çalışma durdu.")
            if failures and key != "question":
                note = ("Bu tablo düzeni için ek hazırlık gerekiyor." if any(a["result"].get("import_status") == "unsupported_layout" for a in failures)
                        else f"{len(failures)} deneme sonuç vermedi; bu denemelerin tamamlandığı doğrulanmadı.")
                detail = (detail + " " + note).strip()
            if resolved:
                detail += f" {len(resolved)} önceki denemenin sorunu aynı adımda düzeltildi."
            if any(a["recovered"] for a in group):
                detail += " Önceden kaydedilen sonuç yeniden kullanıldı."
            item = {"label": _JOURNEY_TOOLS[group[0]["tool"]][2], "detail": detail.strip(), "status": item_status}
            if len(group) > 1:
                item["count"] = len(group)
            items.append(item)
        if items:
            stage_status = "attention" if any(item["status"] == "attention" for item in items) else "active" if any(item["status"] == "active" for item in items) else "complete"
            stage_attempts = [attempt for attempt in attempts if _JOURNEY_TOOLS[attempt["tool"]][0] == stage]
            stages.append({"id": stage, "label": label, "status": stage_status,
                           "summary": _stage_summary(stage, stage_status, stage_attempts), "items": items})
    if stages and status in {"partial", "needs_input", "blocked", "failed"} and all(stage["status"] == "complete" for stage in stages):
        # A saved chart or successful tool does not prove that all requested
        # outputs were delivered. Retain this terminal condition visibly.
        stages[-1]["status"] = "attention"
        stages[-1]["summary"] = _JOURNEY_STATUS[status][1]
        stages[-1]["items"].append({"label": "Tamamlanma durumu", "detail": _JOURNEY_STATUS[status][1], "status": "attention"})
    title, detail = _JOURNEY_STATUS[status]
    if status == "completed" and any(stage["status"] == "attention" for stage in stages):
        detail = "Sonuç hazır; bazı denemeler sonuç vermedi. Ayrıntıları ilgili aşamada görebilirsiniz."
    return {"status": status, "title": title, "detail": detail, "stages": stages, "event_count": len(events)}


def public_run(run):
    """Return run metadata required by the browser, never its agent state."""
    keys = ("run_id", "workspace_id", "conversation_id", "request_id", "message", "status", "created_at", "updated_at")
    return {key: run.get(key) for key in keys if key in run} | {"result": run.get("result")}
