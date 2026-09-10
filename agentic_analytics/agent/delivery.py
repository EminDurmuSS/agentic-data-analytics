"""Delivery checks and receipts for completed chart presentation requests."""

import re


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
    suggestions = saved.get("recommendations", [])[:2]
    if suggestions:
        lines.append("\nİsterseniz şu incelemelerle devam edebiliriz:\n" + "\n".join("- " + item["label"] for item in suggestions))
    return "\n\n".join(lines)
