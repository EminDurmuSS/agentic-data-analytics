"""Bounded, read-only evidence and feasible questions for contextual follow-ups.

The model ranks these questions; it does not invent their financial claims,
columns or dates. Source documents are never inserted into this context.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import unicodedata
from decimal import Decimal
from urllib.parse import urlsplit

import pandas as pd

from agentic_analytics.lakehouse.financial_semantics import cumulative_evidence
from agentic_analytics.lakehouse.presentation import analysis_presentation, column_quantity_lineage


MAX_CONTEXT_CHARS = 19500
MAX_CANDIDATES = 12
_CALENDARS = {"monthly": "M", "quarterly": "Q", "annual": "Y", "yearly": "Y",
              "daily": "D", "business_daily": "B", "weekly": "W-FRI",
              "weekly_observed": "D", "weekly_friday": "W-FRI", "weekly_wednesday": "W-WED"}
_GROWTH_KINDS = {"stock", "flow", "count", "count_stock", "count_flow", "price"}


def _text(value, limit=300):
    return " ".join(str(value or "").replace("\u2014", "-").split())[:limit]


def _fold(value):
    text = unicodedata.normalize("NFKD", _text(value, 12000).casefold().replace("ı", "i"))
    return "".join(char for char in text if not unicodedata.combining(char))


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _number(value):
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _number(value.item())
    return None


def _result(run):
    value = run.get("result") or {}
    return value if isinstance(value, dict) else {}


def _run_tools(run):
    return [item for item in _result(run).get("tool_results", [])
            if isinstance(item, dict) and isinstance(item.get("result"), dict)
            and item["result"].get("status") == "ok"]


def _analysis_reference(run):
    result = _result(run)
    if result.get("analysis_id"):
        return result["analysis_id"]
    # A read-only question can observe a saved result without creating a new
    # head. Only successful tools from this turn establish that association.
    observed = {"read_analysis", "summarize_analysis", "explain_value", "create_chart",
                "analyze_relationship", "rolling_anomalies", "detect_changes"}
    return next((item["result"]["analysis_id"] for item in reversed(_run_tools(run))
                 if item.get("tool") in observed and item["result"].get("analysis_id")), None)


def _sources(manifest):
    lineage = manifest.get("lineage", {})
    sources = dict(lineage.get("sources") or {})
    for group in (lineage.get("groups") or {}).values():
        for column, proof in (group.get("sources") or {}).items():
            sources.setdefault(column, proof)
    return sources


def _source_refs(store, workspace_id, manifest):
    documents = []
    for column, proof in _sources(manifest).items():
        binding = proof.get("binding") or {}
        documents.append(([column], binding.get("document_provenance") or {}, binding))
    dataset = (manifest.get("lineage", {}).get("dataset_query") or {})
    if dataset.get("document_provenance"):
        cell_columns = next(iter((dataset.get("cells") or {}).values()), {})
        documents.append((list(cell_columns), dataset["document_provenance"], {}))
    refs = []
    for columns, document, binding in documents:
        source = {}
        source_id = document.get("source_id")
        if isinstance(source_id, str) and re.fullmatch(r"source_[a-f0-9]{64}", source_id):
            try:
                candidate = json.loads(store._path("document_sources", workspace_id, source_id, "manifest.json").read_text())
                if candidate.get("raw_sha256") == document.get("raw_sha256"):
                    source = candidate
            except (OSError, ValueError, TypeError):
                pass
        record = {key: _text(document[key], 180) for key in
                  ("source_id", "raw_sha256", "table_id", "filename") if isinstance(document.get(key), str)}
        if source.get("filename"):
            record["filename"] = _text(source["filename"], 180)
        if isinstance(document.get("page"), int) and not isinstance(document["page"], bool):
            record["page"] = document["page"]
        url = document.get("source_url") or binding.get("source_url") or source.get("source_url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            if parsed and parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                record["domain"] = parsed.hostname
                record["path"] = _text(parsed.path, 180)
        except ValueError:
            pass
        if binding.get("source_system"):
            record["publisher"] = _text(binding["source_system"], 80)
        if record:
            record["columns"] = columns[:16]
        if record and record not in refs:
            refs.append(record)
    return refs[:6]


def _regular(periods, frequency):
    if not periods or frequency not in _CALENDARS:
        return False
    pattern = r"\d{4}-\d{2}" if frequency == "monthly" else r"\d{4}-Q[1-4]" if frequency == "quarterly" else r"\d{4}" if frequency in {"annual", "yearly"} else r"\d{4}-\d{2}-\d{2}"
    if not all(re.fullmatch(pattern, value) for value in periods):
        return False
    try:
        if frequency == "weekly_observed":
            dates = pd.DatetimeIndex(periods)
            gaps = dates.to_series().diff().dropna().dt.days
            return bool(len(dates) == len(set(periods)) and (gaps.between(4, 10)).all())
        index = pd.PeriodIndex(periods, freq=_CALENDARS[frequency])
        if frequency.startswith("weekly") and list(index.end_time.strftime("%Y-%m-%d")) != periods:
            return False
        if frequency == "business_daily" and any(pd.Timestamp(period).dayofweek > 4 for period in periods):
            return False
        return all(right - left == 1 for left, right in zip(index.asi8, index.asi8[1:]))
    except (ValueError, TypeError):
        return False


def _period_label(value):
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        months = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
        month = int(text[5:])
        if 1 <= month <= 12:
            return f"{months[month - 1]} {text[:4]}"
    return text


def _requested(question, intent):
    expressions = {
        "source_check": r"kayna[kg].{0,25}(?:incele|dogrula|goster)|nereden|kaynak izi",
        "missingness": r"eksik|bos (?:hucre|donem)|missing",
        "growth": r"buyume|yuzde degisim|oransal degisim",
        "difference": r"(?:donem|aylik|yillik|puan).{0,15}fark|degisim|ilk.{0,15}son",
        "annual_growth": r"yillik|onceki yil|gecen yil|ayni ay",
        "period_sum": r"(?:donem|ceyrek|yil|ay|kari).{0,15}toplam|topla|ilk alti ay",
        "relationship": r"korelasyon|birlikte degisim|iliski|relationship",
        "anomalies": r"anomali|olagandisi|uc deger|aykiri",
        "focus_change": r"en (?:buyuk|sert|yuksek).{0,20}(?:degisim|dus|art)|donum noktasi",
        "source_components": r"alt kalem|bilesen|olusturan kalem|detay kirilim",
        "matched_scope": r"uyumlu kapsam|ayni kapsam|kapsamlari eslestir|ayni raporlama",
        "find_comparison_period": r"onceki donem|onceki rapor|yeni donem|karsilastirma donemi",
        "ratio": r"oran|payi|pay-payda",
        "explain_limits": r"sinir|kisit|hangi durumda",
        "apply_explanation": r"ornek|uygula|adim adim",
    }
    return bool(re.search(expressions.get(intent, r"(?!)"), _fold(question)))


def _verified_artifacts(store, workspace_id, analysis_id, data_sha256, runs, tool_names, prefix, directory):
    seen = set()
    for run in runs:
        for item in _run_tools(run):
            if item.get("tool") not in tool_names:
                continue
            result = item["result"]
            identifier = result.get("summary_id") or result.get("artifact_id")
            if not isinstance(identifier, str) or not re.fullmatch(prefix + r"_[a-f0-9]{64}", identifier) or identifier in seen:
                continue
            seen.add(identifier)
            try:
                path = store._path(directory, workspace_id, identifier + ".json")
                if path.stat().st_size > 4_000_000:
                    continue
                encoded = path.read_bytes()
                if hashlib.sha256(encoded).hexdigest() != identifier[len(prefix) + 1:]:
                    continue
                payload = json.loads(encoded)
                if payload.get("workspace_id") != workspace_id or payload.get("analysis_id") != analysis_id or payload.get("provenance", {}).get("data_sha256") != data_sha256:
                    continue
                yield payload
            except (OSError, ValueError, TypeError):
                continue


def _verified_facts(store, workspace_id, analysis_id, data_sha256, runs):
    facts = []
    for payload in _verified_artifacts(store, workspace_id, analysis_id, data_sha256, runs,
                                      {"summarize_analysis"}, "summary", "summaries"):
        for fact in payload.get("facts", []):
            facts.append({key: fact[key] for key in ("column", "statistic", "value", "period_start", "period_end", "unit", "scale") if key in fact})
    return facts[:80]


def build_followup_context(store, workspace_id, run, history):
    """Read only this run's verified evidence and produce at most 12 questions."""
    result = _result(run)
    run_id = run.get("run_id") or result.get("run_id")
    conversation_id = run.get("conversation_id") or result.get("conversation_id")
    question = _text(run.get("message") or run.get("question"), 2200)
    answer = _text(result.get("display_message") or result.get("message"), 2800)
    previous = [item for item in (history or []) if isinstance(item, dict)
                and item.get("workspace_id", workspace_id) == workspace_id
                and item.get("conversation_id", conversation_id) == conversation_id
                and (item.get("run_id") or _result(item).get("run_id")) != run_id][-6:]
    context = {"workspace_id": workspace_id, "run_id": run_id, "conversation_id": conversation_id,
               "analysis_id": None, "current_question": question, "answer": answer,
               "history": [{"question": _text(item.get("message") or item.get("question"), 500),
                            "answer": _text(_result(item).get("display_message") or _result(item).get("message"), 650)} for item in previous],
               "analysis": {}, "candidates": []}
    requested_text = " ".join([question, *(entry["question"] for entry in context["history"][-3:])])

    def add(intent, label, prompt, reason, *, requires_new_data=False, **metadata):
        if sum(item["intent"] == intent for item in context["candidates"]) >= 2 or _requested(requested_text, intent):
            return
        identity = {"analysis_id": context["analysis_id"], "intent": intent, **metadata}
        candidate = {"id": "followup_" + hashlib.sha256(_json(identity).encode()).hexdigest()[:20],
                     "intent": intent, "label": _text(label, 100), "prompt": _text(prompt, 550),
                     "reason": _text(reason, 200), "requires_new_data": requires_new_data, **metadata}
        if all(item["id"] != candidate["id"] for item in context["candidates"]):
            context["candidates"].append(candidate)

    if run.get("workspace_id", workspace_id) != workspace_id:
        context["analysis"] = {"status": "unavailable", "reason": "workspace_mismatch"}
        return _bounded(context)
    analysis_id = _analysis_reference(run)
    if not analysis_id:
        if question and answer and result.get("status") == "completed":
            add("apply_explanation", "Bir örnekle uygula", "Bu açıklamadaki yöntemi, varsayımsal olduğunu belirttiğin küçük bir örnek üzerinde adım adım gösterebilir misin?",
                "Bu yanıtta kayıtlı bir sayısal analiz yok; örnek gerçek kaynak bulgusu olarak sunulmaz.")
            add("explain_limits", "Yöntemin sınırlarını incele", "Bu açıklamadaki yöntemin hangi durumlarda uygulanamayacağını ve hangi ek bilgilere ihtiyaç duyduğunu açıklar mısın?",
                "Açıklamanın varsayımlarını ve uygulanabilirlik sınırlarını netleştirir.")
        return _bounded(context)
    try:
        frame, manifest = store.load_analysis(analysis_id)
        if manifest.get("workspace_id") != workspace_id:
            raise ValueError("workspace mismatch")
        if not 1 <= len(frame) <= 10000 or "period" not in frame or frame["period"].isna().any():
            raise ValueError("unsupported saved grain")
    except (ValueError, OSError, KeyError, TypeError):
        context["analysis"] = {"status": "unavailable", "reason": "analysis_not_verified"}
        return _bounded(context)
    context["analysis_id"] = analysis_id
    lineage, plan, schemas = manifest.get("lineage", {}), manifest.get("plan", {}), manifest.get("schema", {})
    operations = lineage.get("operations") or plan.get("operations") or plan.get("request", {}).get("operations", [])
    frequency = plan.get("frequency") or plan.get("request", {}).get("frequency") or lineage.get("frequency")
    group_by = lineage.get("group_by") or (plan.get("request", {}).get("group_by") if plan.get("query_type") == "grouped" else None)
    periods = sorted(set(frame["period"].astype(str)))
    if group_by and group_by not in frame:
        context["analysis"] = {"status": "unavailable", "reason": "ambiguous_grain"}
        return _bounded(context)
    if frame.duplicated(["period", group_by] if group_by and group_by in frame else ["period"]).any():
        context["analysis"] = {"status": "unavailable", "reason": "ambiguous_grain"}
        return _bounded(context)
    view = analysis_presentation(frame, manifest)
    available = [column for column in view["columns"] if column not in {"period", group_by, "rank"}
                 and pd.api.types.is_numeric_dtype(frame[column]) and not pd.api.types.is_bool_dtype(frame[column])]
    sources = _sources(manifest)
    identities, origins, _, _ = column_quantity_lineage(frame, operations, sources)
    refs = _source_refs(store, workspace_id, manifest)
    facts = _verified_facts(store, workspace_id, analysis_id, manifest["data_sha256"], [*previous, run])
    statistics = [{"method": artifact["method"], "parameters": artifact["parameters"]}
                  for artifact in _verified_artifacts(store, workspace_id, analysis_id, manifest["data_sha256"], [*previous, run],
                    {"rolling_anomalies", "analyze_relationship", "detect_changes"}, "statistic", "statistics")][:12]
    warning_codes = sorted({str(item.get("code")) for item in lineage.get("warnings", []) if isinstance(item, dict) and item.get("code")})
    analysis = {"status": "verified", "data_sha256": manifest["data_sha256"], "frequency": frequency,
                "row_count": len(frame), "period_count": len(periods), "start": periods[0], "end": periods[-1],
                "observed_periods": periods if len(periods) <= 16 else periods[:4] + periods[-4:],
                "periods_truncated": len(periods) > 16, "group_by": group_by,
                "group_count": int(frame[group_by].nunique()) if group_by and group_by in frame else 0,
                "regular_calendar": _regular(periods, frequency), "series": [], "sources": refs,
                "warning_codes": warning_codes, "completed_operations": [{key: item[key] for key in
                    ("op", "column", "output", "denominator", "periods", "prior_scope", "factor", "start", "end") if key in item} for item in operations[:20]],
                "verified_summary_facts": facts[:10], "completed_statistics": statistics,
                "observed_changes": []}
    context["analysis"] = analysis
    safe_ops = frequency in _CALENDARS and analysis["regular_calendar"]
    frame = frame.sort_values(["period", group_by] if group_by else ["period"], kind="stable")
    groups = list(frame.groupby(group_by, sort=False)) if group_by else [(None, frame)]
    column_info = {}
    for column in available[:16]:
        meta = schemas.get(column, {})
        ready = meta.get("status") == "ready" and meta.get("kind") not in {None, "unknown"} and not meta.get("cumulative_evidence") and not cumulative_evidence(meta)
        values = frame[column]
        valid = values.notna()
        if any(not math.isfinite(value) for value in values[valid]):
            ready = False
        observed = frame.loc[valid, "period"].astype(str)
        label = _text(view["labels"].get(column, column.replace("_", " ")), 120)
        counts = [int(part[column].notna().sum()) for _, part in groups]
        information = {"column": column, "label": label, "kind": meta.get("kind"), "status": "ready" if ready else "review_required",
                       "unit": _text(meta.get("unit"), 40), "scale": meta.get("scale", 1), "currency": _text(meta.get("currency"), 20) or None,
                       "price_basis": _text(meta.get("price_basis"), 120) or None, "measurement_basis": _text(meta.get("measurement_basis", "source_reported"), 80),
                       "additive_over_time": meta.get("additive_over_time"), "interval_review_required": bool(cumulative_evidence(meta) or meta.get("cumulative_evidence")),
                       "observed_count": int(valid.sum()), "observed_period_count": int(observed.nunique()),
                       "missing_count": int((~valid).sum()), "min_observations_per_group": min(counts),
                       "first_period": str(observed.iloc[0]) if len(observed) else None,
                       "last_period": str(observed.iloc[-1]) if len(observed) else None,
                       "first_value": _number(values[valid].iloc[0]) if valid.any() and not group_by else None,
                       "last_value": _number(values[valid].iloc[-1]) if valid.any() and not group_by else None,
                       "scope_id": hashlib.sha256(_json(meta.get("scope", {})).encode()).hexdigest()[:16]}
        analysis["series"].append(information)
        column_info[column] = (meta, information, ready)
    analysis["series_truncated"] = len(available) > len(column_info)
    first_label = next(iter(column_info.values()))[1]["label"] if column_info else "kayıtlı ölçü"
    span = _period_label(periods[0]) if len(periods) == 1 else f"{_period_label(periods[0])} - {_period_label(periods[-1])}"

    def done(op, column, lag=None):
        if any(item.get("op") == op and (item.get("column") == column or item.get("output") == column or
               identities.get(item.get("column")) == identities.get(column)) and (lag is None or item.get("periods", 1) == lag) for item in operations):
            return True
        statistic = {"difference": "change", "growth": "growth", "sum": "sum"}.get(op)
        return any(fact.get("column") == column and fact.get("statistic") == statistic and
                   fact.get("period_start") == periods[0] and fact.get("period_end") == periods[-1] for fact in facts)

    if "cross_scope_comparison" in warning_codes:
        add("matched_scope", "Uyumlu kapsamla karşılaştır", "Bu oranı aynı kurumları ve raporlama kapsamını temsil eden kaynaklarla yeniden hesaplayabilir miyiz? Uyumlu kaynak bulunamazsa eksikleri belirt.",
            "Mevcut kaynakların kapsamları farklı; geçerli bir pay karşılaştırması henüz doğrulanmış değil.", requires_new_data=True)
    explained = any(item.get("tool") == "explain_value" and item["result"].get("analysis_id") == analysis_id for item in _run_tools(run))
    if refs and not explained:
        add("source_check", "Kaynak hücresini incele", f"{span} için «{first_label}» değerinin özgün kaynak hücresini, birimini ve uygulanan dönüşümleri açıklar mısın?",
            "Değerin özgün belgedeki yerini ve hesaba nasıl girdiğini gösterir.", columns=available[:1])
    pdf_columns = {column for item in refs for column in item.get("columns", [])
                   if str(item.get("filename", "")).lower().endswith(".pdf") or str(item.get("path", "")).lower().endswith(".pdf")}
    pdf_column = next((column for column in column_info if origins[identities[column]]["column"] in pdf_columns
                       and not origins[identities[column]]["operation"]), None)
    if len(periods) == 1 and pdf_column:
        pdf_label = column_info[pdf_column][1]["label"]
        add("find_comparison_period", "Önceki dönemle karşılaştır", f"«{pdf_label}» önceki döneme göre nasıl değişmiş? Raporda {span} ile karşılaştırılabilir bir dönem varsa birim ve kapsamı kontrol ederek farkı hesapla; yoksa belirt.",
            "Şu anda tek dönem var; raporda karşılaştırılabilir başka bir dönem bulunabilir.", requires_new_data=True, columns=[pdf_column])
    total = next(((column, info[1]["label"]) for column, info in column_info.items()
                  if not origins[identities[column]]["operation"] and info[0].get("kind") not in {"rate", "ratio"}
                  and re.search(r"\b(?:toplam|total)\b", _fold(info[1]["label"]))), None)
    if not total and group_by and column_info:
        group_total = next((str(value) for value in frame[group_by].unique()
                            if re.search(r"\b(?:toplam|total)\b", _fold(value))), None)
        if group_total:
            total = (next(iter(column_info)), f"{first_label}, {_text(group_total, 100)}")
    if refs and total:
        add("source_components", "Toplamın alt kalemlerini bul", f"«{total[1]}» hangi kalemlerden oluşuyor? Kaynaktaki alt kalemleri varsa kaynaklarıyla göster. Birim ve kapsamları uygunsa toplamla tutarlılığını kontrol et.",
            "Toplamın hangi kalemlerden oluştuğu bu sonuçta henüz görünmüyor.", requires_new_data=True, columns=[total[0]])
    if any(info[1]["missing_count"] for info in column_info.values()) or "group_missing_observations" in warning_codes:
        add("missingness", "Eksik gözlemleri açıkla", f"{span} analizindeki boş gözlemleri ve varsa grup-sıralama sınırından kaynaklanan eksikleri gösterir misin? Sıfır veya tahmini değerle doldurma.",
            "Kaydedilen tabloda eksik değerler veya grup üyeliği sınırı var.")

    for column, (meta, info, ready) in column_info.items():
        label = info["label"]
        if not ready or not safe_ops or info["min_observations_per_group"] < 2:
            continue
        adjacent = []
        for _, part in groups:
            observations = dict(zip(part["period"].astype(str), part[column]))
            adjacent.extend((observations.get(a), observations.get(b)) for a, b in zip(periods, periods[1:])
                            if pd.notna(observations.get(a)) and pd.notna(observations.get(b)))
        if not group_by and info["observed_count"] >= 3 and meta.get("kind") in _GROWTH_KINDS and not origins[identities[column]]["operation"]:
            observations = dict(zip(frame["period"].astype(str), frame[column]))
            changes = [(a, b, Decimal(str(observations[b])) - Decimal(str(observations[a])))
                       for a, b in zip(periods, periods[1:]) if pd.notna(observations.get(a)) and pd.notna(observations.get(b))]
            if changes:
                before, after, delta = max(changes, key=lambda item: abs(item[2]))
                if delta:
                    if len(analysis["observed_changes"]) < 6:
                        analysis["observed_changes"].append({"column": column, "previous_period": before, "period": after,
                            "previous_value": _number(observations[before]), "value": _number(observations[after]),
                            "change": int(delta) if delta == delta.to_integral() else float(delta),
                            "selection": "largest_absolute_adjacent_change_within_same_series", "unit": info["unit"], "scale": info["scale"]})
                    add("focus_change", f"{_period_label(after)} değişimini incele",
                        f"«{label}» serisinde {_period_label(before)} ile {_period_label(after)} arasındaki en büyük mutlak dönem değişimini kaynak değerleri ve birimiyle açıklar mısın? Bu gözlemlerden kanıtlanmayan bir neden çıkarma.",
                        "Bu seride en büyük dönem farkı burada görülüyor.",
                        columns=[column], start=before, end=after)
        if adjacent and not done("difference", column):
            suffix = "yüzde puan" if meta.get("unit") in {"percent", "%"} and meta.get("kind") in {"rate", "ratio"} else "kendi birimi"
            add("difference", f"{label}: dönem farkı", f"{span} aralığında «{label}» için ardışık dönem farklarını {suffix} ile gösterir misin?" + (" Her grubu ayrı hesapla." if group_by else ""),
                "Dönemler arasındaki artış veya azalışı kendi birimiyle gösterir.", columns=[column], periods=1)
        if meta.get("kind") in _GROWTH_KINDS and not done("growth", column, 1):
            positive_pairs = any(a > 0 for a, _ in adjacent)
            if positive_pairs:
                add("growth", f"{label}: yüzde değişim", f"{span} aralığında «{label}» için önceki döneme göre yüzde değişimi hesaplar mısın? Eksik veya geçersiz tabanlı dönemleri boş bırak." + (" Grupları ayrı tut." if group_by else ""),
                    "Değişimin başlangıç değerine göre büyüklüğünü karşılaştırır.", columns=[column], periods=1)
        if frequency == "monthly" and meta.get("kind") in _GROWTH_KINDS and not done("growth", column, 12):
            annual_pair = False
            for _, part in groups:
                observations = dict(zip(part["period"].astype(str), part[column]))
                for period, value in observations.items():
                    prior = observations.get(str(pd.Period(period, freq="M") - 12))
                    if pd.notna(value) and prior is not None and pd.notna(prior) and prior > 0:
                        annual_pair = True
            if annual_pair:
                add("annual_growth", f"{label}: yıllık değişim", f"«{label}» için aynı aya göre yıllık yüzde değişimi hesaplar mısın? Yalnız kayıtlı önceki-yıl gözlemi olan dönemleri kullan; diğerlerini boş bırak.",
                    "Aynı aya ait iki yılın verisi mevcut.", columns=[column], periods=12)
        if meta.get("kind") in {"flow", "count_flow"} and meta.get("additive_over_time") is not False and not done("sum", column):
            if all(len(part) == len(periods) and part[column].notna().all() for _, part in groups):
                add("period_sum", f"{label}: dönem toplamı", f"{span} aralığı için «{label}» dönem toplamını hesaplar mısın?" + (" Her grubun toplamını ayrı göster; grupları birbirine ekleme." if group_by else ""),
                    "Bu aralıktaki veriler eksiksiz ve dönemler arasında toplanabilir.", columns=[column], start=periods[0], end=periods[-1])
        anomaly_done = any(item["method"] == "past_only_rolling_mad" and item["parameters"].get("column") == column for item in statistics)
        if not group_by and info["observed_count"] >= 7 and not anomaly_done:
            eligible = any(pd.notna(frame[column].iloc[index]) and frame[column].iloc[max(0, index - 12):index].notna().sum() >= 6
                           and frame[column].iloc[max(0, index - 12):index].notna().mean() >= .8 for index in range(6, len(frame)))
            if eligible:
                add("anomalies", f"{label}: olağandışı gözlemler", f"«{label}» serisinde yalnız önceki gözlemleri kullanarak olağandışı dönemleri işaretler misin? Bunu bir neden veya tahmin kanıtı olarak yorumlama.",
                    "Yakın geçmişten ayrılan dönemleri incelemek için yeterli gözlem var.", columns=[column], window=12, min_history=6)

    if not group_by and safe_ops:
        for x, y in itertools.combinations(column_info, 2):
            xmeta, xinfo, xready = column_info[x]
            ymeta, yinfo, yready = column_info[y]
            if not xready or not yready or identities[x] == identities[y]:
                continue
            # Derived/source pairs can share their arithmetic inputs, producing
            # a mechanical association. Offer a relationship only between two
            # independently selected original quantities.
            if origins[identities[x]]["operation"] or origins[identities[y]]["operation"]:
                continue
            def native_key(column, meta):
                source = sources.get(origins[identities[column]]["column"], {})
                metric_id = source.get("binding", {}).get("metric_id") or meta.get("metric_id")
                return (metric_id, _json(meta.get("scope", {})), _json(source.get("dimensions", {}))) if metric_id else None
            if native_key(x, xmeta) and native_key(x, xmeta) == native_key(y, ymeta):
                continue
            pair_key = {identities[x], identities[y]}
            ratio_done = any(operation.get("op") == "ratio" and
                             {identities.get(operation.get("column")), identities.get(operation.get("denominator"))} == pair_key
                             for operation in operations)
            compatible = bool(xmeta.get("scope")) and xmeta.get("scope") == ymeta.get("scope") and xmeta.get("currency") and all(
                xmeta.get(key) == ymeta.get(key) for key in ("kind", "unit", "currency", "price_basis", "price_scope", "measurement_basis"))
            if compatible and not ratio_done and any(pd.notna(a) and pd.notna(b) and b != 0 for a, b in zip(frame[x], frame[y])):
                add("ratio", "İki ölçünün oranını incele", f"{span} için «{xinfo['label']}» değerinin «{yinfo['label']}» değerine oranını hesaplar mısın? Birimleri eşitle; sıfır veya eksik paydalarda sonuç üretme.",
                    "Ölçülerin birim, fiyat bazı ve kapsam bilgileri bu karşılaştırmaya uygun.", columns=[x, y])
            pairs = frame[[x, y]].dropna()
            relationship_done = any(item["method"] == "lagged_pearson" and
                {item["parameters"].get("x"), item["parameters"].get("y")} == {x, y} and
                item["parameters"].get("lag", 0) == 0 and item["parameters"].get("transform", "none") == "none" for item in statistics)
            if not relationship_done and len(pairs) >= 12 and len(pairs) >= len(frame) * .8 and (pairs.nunique() >= 2).all():
                add("relationship", "Birlikte değişimi incele", f"«{xinfo['label']}» ile «{yinfo['label']}» arasındaki dönemsel birlikte değişimi korelasyonla inceleyebilir misin? Nedensellik sonucu çıkarma; eksik gözlemleri ve sınırlamaları belirt.",
                    "İki serinin birlikte hareketini incelemek için en az 12 ortak gözlem var.", columns=[x, y], min_samples=12, method="pearson")
                break
    return _bounded(context)


def _bounded(context):
    """Keep deterministic identity and complete candidate questions inside 20k."""
    primary, remaining, intents = [], [], set()
    for candidate in context["candidates"]:
        (remaining if candidate["intent"] in intents else primary).append(candidate)
        intents.add(candidate["intent"])
    context["candidates"] = (primary + remaining)[:MAX_CANDIDATES]
    while len(_json(context)) > MAX_CONTEXT_CHARS and context["history"]:
        context["history"].pop(0)
    analysis = context.get("analysis", {})
    for field in ("verified_summary_facts", "completed_operations", "completed_statistics"):
        while len(_json(context)) > MAX_CONTEXT_CHARS and analysis.get(field):
            analysis[field].pop()
            analysis["context_truncated"] = True
    while len(_json(context)) > MAX_CONTEXT_CHARS and context["candidates"]:
        context["candidates"].pop()
        analysis["context_truncated"] = True
    while len(_json(context)) > MAX_CONTEXT_CHARS and len(analysis.get("series", [])) > 1:
        analysis["series"].pop()
        analysis["series_truncated"] = True
    if len(_json(context)) > MAX_CONTEXT_CHARS:
        context["current_question"] = _text(context["current_question"], 700)
        context["answer"] = _text(context["answer"], 1000)
    if len(_json(context)) > MAX_CONTEXT_CHARS:
        # Unexpectedly long imported labels must not make optional suggestions
        # fail the main answer or cause unbounded provider input.
        context["analysis"] = {"status": "unavailable", "reason": "context_budget_exceeded"}
        context["candidates"] = []
    return context
