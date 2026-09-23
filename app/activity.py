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
    "promote_dataset_to_shared_lakehouse": "Doğrulanan veri ortak lakehouse sürümüne ekleniyor",
    "web_search": "Web kaynakları araştırılıyor",
    "research_web": "Resmi web kaynakları araştırılıyor",
    "ask_user": "Kullanıcıya kısa bir soru soruluyor",
    "rolling_anomalies": "Olağandışı dönemler aranıyor",
    "detect_changes": "Değişim noktaları inceleniyor",
    "analyze_relationship": "Değişkenler arasındaki ilişki hesaplanıyor",
    "select_analysis_rows": "Koşullara uyan analiz satırları seçiliyor",
    "save_analysis_bundle": "Farklı frekanstaki analizler birlikte kaydediliyor",
    "create_chart": "Grafik hazırlanıyor",
    "plan_task": "İstenen sonuçlar ve hesap adımları planlanıyor",
    "summarize_analysis": "Dönem toplamları ve karşılaştırmalar doğrulanıyor",
    "aggregate_dataset": "Yeni verinin grupları ve dönemleri hesaplanıyor",
    "prepare_source_table": "Kaynak tablosunun yapısı düzenleniyor",
    "combine_source_tables": "Devam eden kaynak tabloları birleştiriliyor",
    "read_source_table": "Kaynağın özgün tablo satırları okunuyor",
    "find_source_table_rows": "Kaynak tablosunda ilgili satırlar aranıyor",
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
        title = "Model çağrılıyor"
        detail = f"Karar {payload.get('decision', '?')} için bir sonraki fonksiyon seçiliyor."
    elif kind == "model_response":
        if names:
            title = "Model fonksiyon seçti"
            detail = ", ".join(f"{name} — {_tool_description(name)}" for name in names)
        elif payload.get("finish_reason") == "length":
            title, detail = "Qwen yanıtı kesildi", "Çıktı uzunluk sınırına takıldı; fonksiyon çalıştırılmadı."
        else:
            title, detail = "Qwen nihai yanıtı üretti", "Yeni bir fonksiyon çağrısı istemedi."
    elif kind == "provider_error":
        title = "Model sağlayıcısına ulaşılamadı"
        detail = "Geçici bağlantı hatası kaydedildi; tamamlanan adımlar korunuyor."
    elif kind == "run_reopened":
        title = "Kayıtlı çalışma yeniden açıldı"
        detail = "Geçici sağlayıcı hatasından sonraki adımlara aynı çalışma kaydıyla devam ediliyor."
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
    "attach_reference_catalogue": ("sources", "catalogue_attachment", "Ortak veriler", "Yayımlanmış ortak veriler çalışma alanına bağlanıyor"),
    "web_search": ("sources", "web", "Web araştırması", "Web kaynakları aranıyor"),
    "research_web": ("sources", "web", "Web araştırması", "Web kaynakları inceleniyor"),
    "inspect_source": ("sources", "document", "Belge incelemesi", "Belgenin seçili bölümleri inceleniyor"),
    "find_source_pages": ("sources", "document", "Belge incelemesi", "Belgede ilgili sayfalar aranıyor"),
    "read_source_table": ("sources", "document", "Belge incelemesi", "Seçili tablo satırları okunuyor"),
    "find_source_table_rows": ("sources", "document", "Belge incelemesi", "Kaynak tablosunda ilgili satırlar aranıyor"),
    "prepare_source_table": ("data", "preparation", "Tablo düzeni", "Kaynak tablosu düzenleniyor"),
    "combine_source_tables": ("data", "preparation", "Tablo düzeni", "Devam eden tablolar birleştiriliyor"),
    "ingest_source_table": ("data", "publication", "Verinin eklenmesi", "Seçili kaynak verisi hazırlanıyor"),
    "publish_selected_table": ("data", "publication", "Verinin eklenmesi", "Seçili kaynak verisi ekleniyor"),
    "promote_dataset_to_shared_lakehouse": ("data", "shared_publication", "Ortak lakehouse", "Doğrulanan veri kalıcı ortak sürüme ekleniyor"),
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
    "select_analysis_rows": ("calculation", "selection", "Koşullu satır seçimi", "Koşullara uyan analiz satırları seçiliyor"),
    "save_analysis_bundle": ("calculation", "bundle", "Analiz paketi", "Farklı frekanstaki analizler ayrı tablolar olarak birlikte kaydediliyor"),
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
    action = "publication" if attempt["tool"] in {
        "ingest_source_table", "publish_selected_table", "promote_dataset_to_shared_lakehouse",
    } else attempt["tool"]
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
    if tool == "web_search" and (not result.get("results") or result.get("progress", {}).get("new_source_urls") == 0):
        return "attention"
    if tool in {"ingest_source_table", "publish_selected_table"}:
        if not result.get("dataset_id") or result.get("publication_performed") is False:
            return "attention"
    if tool == "promote_dataset_to_shared_lakehouse" and not result.get("shared_release_id"):
        return "attention"
    if tool in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"} and not result.get("analysis_id"):
        return "attention"
    if tool == "select_analysis_rows" and not result.get("selection_id"):
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
        if attempt["tool"] in {"read_source_table", "find_source_table_rows"}:
            for index, row in enumerate(result.get("rows", [])):
                address = row.get("candidate_row") if isinstance(row, dict) else None
                rows.add((source, result.get("table_id") or args.get("table_id"),
                          address if _integer(address) else (attempt["index"], index)))
    return names, pages, rows


def _addresses(values):
    """Compact observed page/row addresses, never arbitrary result values."""
    numbers = sorted({value for value in values if _integer(value) and value > 0}) if isinstance(values, list) else []
    ranges = []
    for value in numbers:
        if ranges and ranges[-1][1] + 1 == value:
            ranges[-1][1] = value
        else:
            ranges.append([value, value])
    label = ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges[:4])
    if len(ranges) > 4:
        label += f" (+{sum(end - start + 1 for start, end in ranges[4:])} diğer)"
    return label, len(numbers)


def _attempt_action(attempt, run_status):
    """One public action per actual attempt, with no raw arguments or values."""
    tool, result, args = attempt["tool"], attempt["result"], attempt["args"]
    outcome = _attempt_outcome(attempt)
    if not attempt["finished"]:
        label = _JOURNEY_TOOLS[tool][3]
        pages, count = _addresses(args.get("page_numbers"))
        if tool == "inspect_source" and pages:
            label = f"Belgenin {pages}. " + ("sayfası" if count == 1 else "sayfaları") + " inceleniyor"
        if run_status != "running":
            return {"label": label + "; çalışma bu adım bitmeden durdu.", "status": "attention"}
        return {"label": label + ".", "status": "active"}

    if outcome == "attention":
        codes = {error.get("code") for error in result.get("errors", []) if isinstance(error, dict)} | {result.get("code")}
        if tool == "find_source_pages" and codes & {"NO_PROGRESS", "SOURCE_READ_REQUIRED"}:
            label = "Sayfa araması tekrarlandı; devam etmek için bulunan sayfaların içeriği incelenmeli."
        elif tool in {"inspect_source", "read_source_table", "find_source_table_rows"} and "SOURCE_READ_REPEATED" in codes:
            label = "Aynı kaynak okuması tekrarlandı; önceki okuma korunarak sonraki adım değerlendiriliyor."
        elif tool == "ask_user":
            label = "Devam etmek için yanıtınız bekleniyor."
        elif tool == "web_search" and result.get("status") == "ok":
            label = "Arama yeni bir kaynak bağlantısı getirmedi."
        elif result.get("import_status") == "unsupported_layout":
            label = "Tabloyu eklemek için ek düzenleme gerekiyor."
        else:
            label = {"inspect_source": "Belge incelemesi tamamlanamadı.",
                     "find_source_pages": "Belgede sayfa araması tamamlanamadı.",
                     "read_source_table": "Seçili tablo satırları okunamadı.",
                     "web_search": "Web araması tamamlanamadı.",
                     "research_web": "Gerekli web kaynağı okunamadı.",
                     "ingest_source_table": "Kaynak verisi çalışma alanına eklenemedi.",
                     "publish_selected_table": "Kaynak verisi çalışma alanına eklenemedi.",
                     "promote_dataset_to_shared_lakehouse": "Veri ortak lakehouse sürümüne eklenemedi.",
                     "explain_value": "Bu değerin kaynak bağlantısı tamamlanamadı.",
                     "create_chart": "Grafik tamamlanamadı.",
                     "validate_plan": "Hesap planı kontrollerden geçemedi."}.get(tool,
                         _JOURNEY_TOOLS[tool][2] + " tamamlanamadı.")
        pages, count = _addresses(args.get("page_numbers"))
        if tool == "inspect_source" and pages and "SOURCE_READ_REPEATED" not in codes:
            label = f"Belgenin {pages}. " + ("sayfası" if count == 1 else "sayfaları") + " incelenemedi."
    elif tool == "inspect_source":
        pages, count = _addresses(result.get("processed_pages"))
        name = _safe_name(result.get("filename"))[:60]
        location = (name + ": ") if name else "Belgenin "
        label = (location + pages + (". sayfası incelendi." if count == 1 else ". sayfaları incelendi.")) if pages else "Kaynağın içeriği incelendi."
    elif tool == "find_source_pages":
        pages, count = _addresses([match.get("page") for match in result.get("matches", []) if isinstance(match, dict)])
        if pages:
            label = f"Arama sözcükleriyle eşleşen {pages}. " + ("sayfa" if count == 1 else "sayfalar") + " bulundu."
        else:
            searched, _ = _addresses(result.get("searched_pages"))
            label = (f"{searched}. sayfalarda arandı; eşleşme bulunamadı." if searched else "Belgede arama yapıldı; eşleşme bulunamadı.")
        if result.get("complete") is False:
            label += " Tarama kısmi."
    elif tool in {"read_source_table", "find_source_table_rows"}:
        rows = result.get("rows", [])
        addresses, count = _addresses([row.get("candidate_row") for row in rows if isinstance(row, dict)])
        page = result.get("page")
        location = f"{page}. sayfadaki tablonun " if _integer(page) and page > 0 else "Tablonun "
        verb = "bulundu" if tool == "find_source_table_rows" else "okundu"
        label = (location + addresses + (f". satırı {verb}." if count == 1 else f". satırları {verb}.") if addresses else
                 f"Tabloda {len(rows)} eşleşen satır bulundu." if tool == "find_source_table_rows" and isinstance(rows, list) else
                 f"Tablodan {len(rows)} satır okundu." if isinstance(rows, list) else "Seçili tablo satırları okundu.")
    elif tool in {"discover", "dimension_values"}:
        count = result.get("total")
        noun = "veri adayı" if tool == "discover" else "karşılaştırma grubu"
        label = (f"Katalogda {count} {noun} bulundu." if _integer(count) and count > 0 else
                 f"Katalogda eşleşen {noun} bulunamadı." if count == 0 else
                 "Katalogdaki veri adayları incelendi." if tool == "discover" else "Verideki karşılaştırma grupları incelendi.")
    elif tool == "describe":
        label = "Seçili verinin birimi, dönemi ve kapsamı incelendi."
    elif tool in {"web_search", "research_web"}:
        label = _journey_detail("web", [attempt])
    elif tool in {"ingest_source_table", "publish_selected_table"}:
        count = result.get("row_count")
        label = f"{count} satır kaynak verisi çalışma alanına eklendi." if _integer(count) else "Kaynak verisi çalışma alanına eklendi."
    elif tool == "promote_dataset_to_shared_lakehouse":
        label = ("Doğrulanmış veri kalıcı ortak lakehouse sürümüne eklendi."
                 if result.get("publication_performed") is not False else
                 "Doğrulanmış veri zaten aktif ortak lakehouse sürümünde.")
    elif tool == "prepare_source_table":
        count = result.get("row_count")
        label = f"{count} satırlık kaynak tablosu analize uygun biçimde düzenlendi." if _integer(count) else "Kaynak tablosunun düzeni hazırlandı."
    elif tool == "combine_source_tables":
        pages, _ = _addresses(result.get("source_pages"))
        label = f"{pages}. sayfalardaki devam tabloları birleştirildi." if pages else "Devam eden kaynak tabloları birleştirildi."
    elif tool in {"execute", "revise_analysis", "query_grouped", "aggregate_dataset"}:
        count = result.get("row_count")
        label = f"{count} satırlık analiz tablosu kaydedildi." if _integer(count) else "Analiz tablosu kaydedildi."
    elif tool == "select_analysis_rows":
        count = result.get("total_match_count")
        label = f"Koşulları karşılayan {count} analiz satırı seçildi." if _integer(count) else "Koşullara uyan analiz satırları seçildi."
    else:
        label = {"attach_reference_catalogue": "Ortak veri kataloğu çalışma alanında kullanıma hazır.",
                 "summarize_analysis": "Kayıtlı analiz için sonuç özeti hazırlandı.",
                 "validate_plan": "Birim, dönem ve hesap kuralları kontrol edildi.",
                 "explain_value": "Seçili değerin özgün kaynak bağlantısı incelendi.",
                 "rolling_anomalies": "Olağandışı dönem taraması tamamlandı.",
                 "detect_changes": "Verideki değişim noktaları incelendi.",
                 "analyze_relationship": "Seçili değişkenlerin birlikte değişimi incelendi.",
                 "select_analysis_rows": "Koşullara uyan kayıtlı analiz satırları seçildi.",
                 "create_chart": "Analiz grafiği kaydedildi."}.get(tool, "Kayıtlı işlem tamamlandı.")
    if attempt.get("recovery_kind") == "tool_reused":
        label = "Önceki sonuç yeniden kullanıldı: " + label
    elif attempt.get("recovery_kind") == "tool_recovered":
        label = "Kesinti öncesindeki sonuç kullanıldı: " + label
    return {"label": label, "status": outcome}


def _journey_detail(key, attempts):
    good = [attempt for attempt in attempts if _attempt_outcome(attempt) == "complete"]
    results = [attempt["result"] for attempt in good]
    if key == "document":
        names, pages, rows = _document_evidence(good)
        detail = ", ".join(names[:2])
        searched = {(attempt["result"].get("source_id") or attempt["args"].get("source_id") or attempt["index"], page)
                    for attempt in good if attempt["tool"] == "find_source_pages"
                    for page in attempt["result"].get("searched_pages", []) if _integer(page) and page > 0}
        facts = ([f"{len(searched)} sayfada metin araması yapıldı"] if searched else [])
        if pages:
            facts.append(f"{len(pages)} sayfanın içeriği incelendi")
        if rows:
            facts.append(f"seçili tablolardan {len(rows)} satır okundu")
        return ((detail + ": ") if detail else "") + (", ".join(facts) if facts else "Belgenin seçili bölümleri incelendi.")
    if key == "publication":
        datasets = {result["dataset_id"]: result for result in results}
        count = sum(result.get("row_count", 0) for result in datasets.values() if _integer(result.get("row_count")))
        return (f"{len(datasets)} veri kümesi, {count} satır eklendi." if count else f"{len(datasets)} veri kümesi eklendi.") if datasets else "Kaynak henüz analiz verisine eklenmedi."
    if key == "shared_publication":
        releases = {result.get("shared_release_id") for result in results if result.get("shared_release_id")}
        return (f"{len(releases)} kalıcı ortak lakehouse sürümü doğrulandı." if releases
                else "Veri henüz kalıcı ortak lakehouse sürümüne eklenmedi.")
    if key == "analysis":
        last = results[-1] if results else {}
        count = last.get("row_count")
        return f"{count} satırlık analiz tablosu kaydedildi." if _integer(count) else "Analiz tablosu kaydedildi."
    if key == "web":
        hosts = sorted({_host(source.get("url") or source.get("source_url")) for result in results
                        for source in result.get("sources", []) if isinstance(source, dict)} - {""})
        if hosts:
            return ", ".join(hosts[:3]) + " kaynakları incelendi."
        urls = {source.get("url") for result in results for source in result.get("results", []) if isinstance(source, dict) and source.get("url")}
        return (f"{len(urls)} kaynak bağlantısı bulundu; içerikleri henüz okunmadı." if urls
                else "İlgili yeni bir kaynak bağlantısı bulunamadı; araştırma tamamlanmadı.")
    if key == "lineage":
        count = len({(attempt["args"].get("analysis_id"), attempt["args"].get("column"), attempt["args"].get("period")) for attempt in good})
        return f"{count} değerin kayıtlı kaynak bağlantısı incelendi." if count else "Kaynak bağlantısı tamamlanamadı."
    return {"catalog": "İlgili veri adayları ve kapsam bilgileri incelendi.",
            "catalogue_attachment": "Yayımlanmış ortak veri kataloğu bu çalışma alanına bağlandı.",
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
        shared = [attempt for attempt in good if _JOURNEY_TOOLS[attempt["tool"]][1] == "shared_publication"]
        if shared:
            return _journey_detail("shared_publication", shared)
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
            attempt.update(result=result, finished=True, recovered=kind in {"tool_recovered", "tool_reused"}, recovery_kind=kind)
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
            detail = _journey_detail(key, group) if has_result or key in {"publication", "question", "web"} else ""
            if active:
                detail = _JOURNEY_TOOLS[active[-1]["tool"]][3] + ("." if status == "running" else "; bu adım sonuçlanmadan çalışma durdu.")
            if failures and key != "question":
                note = ("Veri kalıcı ortak lakehouse sürümüne eklenemedi."
                        if key == "shared_publication" else
                        "Bu tablo düzeni için ek hazırlık gerekiyor." if any(a["result"].get("import_status") == "unsupported_layout" for a in failures)
                        else f"{len(failures)} deneme sonuç vermedi; bu denemelerin tamamlandığı doğrulanmadı.")
                detail = (detail + " " + note).strip()
            if resolved:
                detail += f" {len(resolved)} önceki denemenin sorunu aynı adımda düzeltildi."
            if any(a["recovered"] for a in group):
                detail += " Önceden kaydedilen sonuç yeniden kullanıldı."
            item = {"label": _JOURNEY_TOOLS[group[0]["tool"]][2], "detail": detail.strip(), "status": item_status,
                    "actions": [_attempt_action(attempt, status) for attempt in group]}
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
        stages[-1]["items"].append({"label": "Tamamlanma durumu", "detail": _JOURNEY_STATUS[status][1], "status": "attention", "actions": []})
    title, detail = _JOURNEY_STATUS[status]
    if status == "completed" and any(stage["status"] == "attention" for stage in stages):
        detail = "Sonuç hazır; bazı denemeler sonuç vermedi. Ayrıntıları ilgili aşamada görebilirsiniz."
    return {"status": status, "title": title, "detail": detail, "stages": stages, "event_count": len(events)}


def public_run(run):
    """Return run metadata required by the browser, never its agent state."""
    keys = ("run_id", "workspace_id", "conversation_id", "request_id", "message", "status", "created_at", "updated_at")
    return {key: run.get(key) for key in keys if key in run} | {"result": run.get("result")}
