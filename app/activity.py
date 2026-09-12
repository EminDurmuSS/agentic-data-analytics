"""Safe, user-facing projections of durable agent journal events."""
from __future__ import annotations


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
    "publish_selected_table": "Doğrulanan tablo ekleniyor",
    "web_search": "Web kaynakları araştırılıyor",
    "rolling_anomalies": "Olağandışı dönemler aranıyor",
    "detect_changes": "Değişim noktaları inceleniyor",
    "analyze_relationship": "Değişkenler arasındaki ilişki hesaplanıyor",
    "create_chart": "Grafik hazırlanıyor",
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
        else:
            title, detail = "Qwen nihai yanıtı üretti", "Yeni bir fonksiyon çağrısı istemedi."
    elif kind == "tool_started":
        title, detail = f"{tool} çalıştırılıyor", _tool_description(tool)
    elif kind == "plan_validation":
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
        title, detail = "Agent çalışması tamamlandı", f"Son durum: {payload.get('status', 'bilinmiyor')}."
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


def public_run(run):
    """Return run metadata required by the browser, never its agent state."""
    keys = ("run_id", "workspace_id", "conversation_id", "request_id", "message", "status", "created_at", "updated_at")
    return {key: run.get(key) for key in keys if key in run} | {"result": run.get("result")}
