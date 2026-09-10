"""Validated chart views over complete immutable analyses, without model-generated code."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import pandas as pd

from agentic_analytics.lakehouse.store import StoreError
from agentic_analytics.lakehouse.service import PlanError


class ChartError(PlanError):
    def __init__(self, message, code="INVALID_CHART_REQUEST"):
        super().__init__(message, code=code)


ENUMS = {
    "kind": ("auto", "line", "bar", "area", "scatter", "heatmap"),
    "layout": ("auto", "overlay", "panels", "dual_axis"),
    "normalize": ("none", "index100"),
    "orientation": ("vertical", "horizontal"),
}
SAFE_INTEGER = 2**53 - 1
GROWTH_KINDS = {"stock", "flow", "price", "count", "count_stock", "count_flow"}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _text(value):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()


def _number(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        raise ChartError("Mantıksal değerler sayısal grafik serisi değildir.", "NON_NUMERIC_COLUMN")
    if isinstance(value, int) or pd.api.types.is_integer(value):
        if abs(int(value)) > SAFE_INTEGER:
            raise ChartError("Bu tam sayı tarayıcıda kayıpsız gösterilemez.", "UNSAFE_INTEGER")
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise ChartError("Sonsuz sayılar grafiklerde kullanılamaz.", "NON_FINITE_VALUE")
    if result.is_integer() and abs(result) > SAFE_INTEGER:
        raise ChartError("Bu tam sayı tarayıcıda kayıpsız gösterilemez.", "UNSAFE_INTEGER")
    return result


def _unit(schema):
    unit, scale = schema.get("unit", "unknown"), schema.get("scale", 1)
    if unit in {"TRY", "TL"}:
        return {1: "TL", 1000: "bin TL", 1000000: "milyon TL", 1000000000: "milyar TL", 1000000000000: "trilyon TL"}.get(scale, f"{scale:g} TL")
    label = {"percent": "%", "%": "%", "index": "endeks", "count": "adet", "persons": "kişi",
             "visits": "ziyaret", "percentage_point": "yüzde puan", "TRY/person": "TL/kişi",
             "ratio": "oran", "unknown": "birim incelenmeli"}.get(unit, _text(unit))
    prefix = {1: "", 1000: "bin ", 1000000: "milyon ", 1000000000: "milyar "}.get(scale, f"{scale:g} × ")
    return prefix + label


def _summary(values, periods, meta, *, normalized=False, grouped=False):
    observed = [(i, value) for i, value in enumerate(values) if value is not None]
    summary = {"first": None, "last": None, "min": None, "max": None, "change": None,
               "change_percent": None, "missing_count": len(values) - len(observed),
               "first_period": None, "last_period": None, "change_unit": meta["unit"]}
    if not observed:
        return summary
    summary.update(min=min(value for _, value in observed), max=max(value for _, value in observed))
    if grouped:
        return summary
    first_index, first = observed[0]
    last_index, last = observed[-1]
    summary.update(first=first, last=last, first_period=periods[first_index], last_period=periods[last_index])
    if len(observed) > 1 and meta["kind"] != "unknown" and meta.get("schema", {}).get("status") != "review_required":
        summary["change"] = _number(last - first)
        if meta["kind"] in GROWTH_KINDS and first > 0 and not normalized:
            summary["change_percent"] = _number((last / first - 1) * 100)
    if normalized or meta["kind"] == "index":
        summary["change_unit"] = "endeks puanı"
    elif meta["unit"] == "%":
        summary["change_unit"] = "yüzde puan"
    return summary


class ChartTools:
    """A saved chart changes presentation only, never analysis rows or workspace heads."""

    def __init__(self, store, workspace_id, *, max_rows=10000):
        self.store, self.workspace_id = store, workspace_id
        self.store.workspace(workspace_id)
        if type(max_rows) is not int or max_rows < 1:
            raise ChartError("Grafik satır sınırı pozitif tam sayı olmalıdır.")
        self.max_rows = max_rows
        self.root = self.store._path("charts", workspace_id)

    def _path(self, *parts):
        return self.store._path("charts", self.workspace_id, *parts)

    def _load(self, analysis_id):
        if not isinstance(analysis_id, str) or not re.fullmatch(r"analysis_[a-f0-9]{64}", analysis_id):
            raise ChartError("Geçerli bir kayıtlı analiz seçin.")
        frame, manifest = self.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != self.workspace_id:
            raise ChartError("Analiz başka bir çalışma alanına ait.", "WORKSPACE_MISMATCH")
        if not 1 <= len(frame) <= self.max_rows:
            raise ChartError(f"Grafik 1 ile {self.max_rows} arasında kayıtlı satır gerektirir; veri örneklenmedi.", "ROW_LIMIT")
        if "period" not in frame or frame["period"].isna().any():
            raise ChartError("Eksiksiz dönem anahtarı gerekli.", "AMBIGUOUS_GRAIN")
        if not frame["period"].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise ChartError("Dönem anahtarları metin olmalıdır.", "AMBIGUOUS_GRAIN")
        return frame.copy(deep=True), manifest

    @staticmethod
    def _validate(args):
        allowed = {"analysis_id", "columns", "x", "title", *ENUMS}
        if not isinstance(args, dict) or set(args) - allowed or "analysis_id" not in args:
            raise ChartError("Yalnızca tanımlı grafik seçenekleri ve analysis_id kullanılabilir.")
        for key, options in ENUMS.items():
            if key in args and (not isinstance(args[key], str) or args[key] not in options):
                raise ChartError(f"Geçersiz {key} seçeneği.")
        if "columns" in args:
            columns = args["columns"]
            if not isinstance(columns, list) or not 1 <= len(columns) <= 6 or not all(isinstance(x, str) and x for x in columns) or len(set(columns)) != len(columns):
                raise ChartError("Birbirinden farklı 1 ile 6 sayısal sütun seçin.")
        if "x" in args and (not isinstance(args["x"], str) or not args["x"]):
            raise ChartError("Dağılım grafiği için bir sayısal x sütunu seçin.")
        if "title" in args and (not isinstance(args["title"], str) or not 1 <= len(args["title"].strip()) <= 160 or _text(args["title"]) != args["title"].strip()):
            raise ChartError("Başlık 1 ile 160 karakter arasında düz metin olmalıdır.")

    @staticmethod
    def _sources(manifest):
        lineage = manifest.get("lineage", {})
        sources = dict(lineage.get("sources", {}))
        for group in lineage.get("groups", {}).values():
            for name, proof in group.get("sources", {}).items():
                sources.setdefault(name, proof)
        return sources

    def _metadata(self, manifest, column):
        schema = manifest.get("schema", {}).get(column, {})
        sources = self._sources(manifest)
        proof = sources.get(column, {})
        if not proof:
            proof = next((p for p in sources.values() if p.get("binding", {}).get("metric_id") == schema.get("metric_id") and schema.get("metric_id")), {})
        binding = proof.get("binding", {})
        label = _text(binding.get("title") or schema.get("title") or schema.get("label") or column.replace("_", " "))
        unit = _unit(schema)
        operations = manifest.get("plan", {}).get("operations", [])
        operation = next((op for op in reversed(operations) if op.get("output") == column), {})
        if schema.get("price_basis"):
            label += f" (reel, {schema['price_basis']} fiyatları)"
        if operation.get("op") == "growth":
            periods = operation.get("periods", 1)
            frequency = manifest.get("plan", {}).get("frequency")
            label += " - yıllık değişim" if periods == 12 and frequency == "monthly" else f" - {periods} dönemlik değişim"
        elif operation.get("op") == "difference":
            label += f" - {operation.get('periods', 1)} dönemlik fark"
        elif operation.get("op") == "ratio":
            denominator = operation.get("denominator", "")
            denom_binding = sources.get(denominator, {}).get("binding", {})
            label += " / " + _text(denom_binding.get("title") or denominator.replace("_", " "))
        meta = {"column": column, "label": label, "unit": unit,
                "kind": schema.get("kind", "unknown"), "metric_id": schema.get("metric_id") or binding.get("metric_id"),
                "price_basis": schema.get("price_basis"), "scale": schema.get("scale", 1),
                "schema": schema, "binding": binding}
        return meta

    @staticmethod
    def _compatibility(meta):
        schema = meta["schema"]
        unit = schema.get("unit", "unknown")
        # Different index series do not establish a common index base merely by sharing a unit label.
        basis = meta["metric_id"] or meta["column"] if unit in {"unknown", "index", ""} else None
        return unit, meta["scale"], meta["price_basis"], schema.get("price_scope"), basis

    @staticmethod
    def _values(frame, column):
        if not pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]):
            raise ChartError("Grafik yalnızca kayıtlı sayısal sütunları kullanabilir.", "NON_NUMERIC_COLUMN")
        return [_number(value) for value in frame[column]]

    def _build(self, args):
        self._validate(args)
        frame, manifest = self._load(args["analysis_id"])
        lineage = manifest.get("lineage", {})
        group_by = lineage.get("group_by")
        if manifest.get("plan", {}).get("query_type") == "grouped":
            group_by = group_by or manifest["plan"].get("request", {}).get("group_by")
        if group_by:
            if group_by not in frame or frame[group_by].isna().any() or frame.duplicated(["period", group_by]).any():
                raise ChartError("Grup ve dönem başına tek kayıt gerekli.", "AMBIGUOUS_GRAIN")
            frame = frame.sort_values("period", kind="stable").reset_index(drop=True)
        else:
            if frame["period"].duplicated().any():
                raise ChartError("Tekrarlı dönemler için kayıtlı grup boyutu gerekli.", "AMBIGUOUS_GRAIN")
            frame = frame.sort_values("period", kind="stable").reset_index(drop=True)
        available = [column for column in frame if column not in {"period", group_by, "rank"}
                     and pd.api.types.is_numeric_dtype(frame[column]) and not pd.api.types.is_bool_dtype(frame[column])
                     and manifest.get("schema", {}).get(column, {}).get("kind") not in {"dimension", "rank"}]
        if not available:
            raise ChartError("Gösterilebilecek kayıtlı sayısal sütun yok.", "NO_NUMERIC_COLUMNS")
        metadata = {col: self._metadata(manifest, col) for col in available}
        warnings = []
        selected = args.get("columns")
        if selected is None:
            # Deflators are useful context, but can dominate an otherwise readable default.
            preferred = [col for col in available if metadata[col]["schema"].get("index_role") != "price_deflator"]
            selected = (preferred or available)[:6]
            if len(available) > len(selected):
                warnings.append("İlk görünümde seçili seriler gösteriliyor; diğer sayısal sütunlar sütun seçicisinden eklenebilir.")
        if any(col not in available for col in selected):
            raise ChartError("Seçilen sütun kayıtlı sayısal bir ölçü değil.", "NON_NUMERIC_COLUMN")
        kind = args.get("kind", "auto")
        if kind == "auto":
            if group_by:
                kind = "bar" if frame.period.nunique() == 1 else "heatmap"
            else:
                kind = "bar" if len(frame) <= 24 and all(metadata[col]["kind"] in {"flow", "count_flow"} for col in selected) else "line"
        normalize = args.get("normalize", "none")
        orientation = args.get("orientation", "vertical")
        if orientation == "horizontal" and kind != "bar":
            raise ChartError("Yatay yön yalnızca çubuk grafiğinde kullanılabilir.")
        x = args.get("x")
        if kind == "scatter":
            if group_by:
                raise ChartError("Gruplanmış veriler çubuk veya ısı haritasıyla gösterilir.", "GROUPED_CHART_REQUIRED")
            if x is None:
                x = selected[0] if len(selected) > 1 else next((col for col in available if col not in selected), None)
            if x not in available:
                raise ChartError("Dağılım grafiği için ayrı bir sayısal x sütunu gerekli.")
            if "columns" not in args:
                selected = [col for col in selected if col != x]
            if x in selected or not selected:
                raise ChartError("x sütunu ile y serileri birbirinden farklı olmalıdır.")
            if normalize != "none":
                raise ChartError("Dağılım grafiği özgün sayısal eksenleri kullanır; normalizasyon uygulanamaz.")
        elif x is not None:
            raise ChartError("x sütunu yalnızca dağılım grafiğinde kullanılır.")
        if group_by:
            if kind not in {"bar", "heatmap"} or (kind == "bar" and frame.period.nunique() != 1):
                raise ChartError("Tek dönemli grupları çubuk, çok dönemli grupları ısı haritasıyla gösterin.", "GROUPED_CHART_REQUIRED")
            if len(selected) != 1 or normalize != "none":
                raise ChartError("Gruplu grafik tek ölçünün özgün değerlerini kullanır.")
        elif kind == "heatmap":
            raise ChartError("Isı haritası kayıtlı grup ve dönem boyutları gerektirir.", "GROUPED_CHART_REQUIRED")
        raw = {col: self._values(frame, col) for col in selected}
        normalized = {col: list(values) for col, values in raw.items()}
        periods = frame.period.tolist()
        base_period = None
        if normalize == "index100":
            if any(metadata[col]["kind"] == "unknown" or metadata[col]["schema"].get("status") == "review_required" for col in selected):
                raise ChartError("Anlamı veya birimi inceleme gerektiren seriler normalize edilemez.", "SEMANTICS_REVIEW_REQUIRED")
            if any(values[0] is None or values[0] <= 0 for values in raw.values()):
                raise ChartError("Başlangıç=100 için aynı ilk dönemde tüm seçili seriler pozitif ve dolu olmalıdır; başka bir başlangıç sessizce seçilmez.", "INVALID_NORMALIZATION_BASE")
            base_period = periods[0]
            normalized = {col: [None if value is None else _number(value / values[0] * 100) for value in values] for col, values in raw.items()}
            warnings.append(f"Yalnızca grafik görünümü normalize edildi: {base_period}=100. Özgün analiz değerleri korunuyor.")
        compatibility = {self._compatibility(metadata[col]) for col in selected}
        layout = args.get("layout", "auto")
        if layout == "auto":
            layout = "overlay" if len(compatibility) == 1 or normalize == "index100" else "panels"
        if layout == "overlay" and len(compatibility) > 1 and normalize != "index100":
            raise ChartError("Farklı birimler veya fiyat bazları aynı eksende gösterilemez; ayrı paneller ya da açıkça iki eksen seçin.", "UNIT_MISMATCH")
        if layout == "dual_axis":
            if len(selected) != 2 or group_by or kind == "scatter":
                raise ChartError("İki eksen yalnızca iki zaman serisiyle kullanılabilir.")
            warnings.append("Sol ve sağ eksen farklı ölçekler kullanır; çizgilerin yüksekliği doğrudan karşılaştırılamaz.")
        if layout == "panels" and len(compatibility) > 1:
            warnings.append("Farklı birimler veya fiyat bazları ayrı panellerde gösteriliyor.")
        if kind == "scatter":
            warnings.append("Noktalar aynı döneme ait gözlemleri eşler; görünüm nedensellik göstermez.")
        for col in selected:
            meta = metadata[col]
            if meta["kind"] == "unknown" or meta["schema"].get("status") == "review_required":
                warnings.append(f"{meta['label']}: kaynak birimi veya anlamı inceleme gerektiriyor; değerler dönüştürülmeden okunmalıdır.")
            if any(value is None for value in raw[col]):
                warnings.append(f"{meta['label']}: eksik değerler boş bırakıldı; doldurma yapılmadı.")
        dependencies = set(selected + ([x] if x else []))
        for operation in reversed(manifest.get("plan", {}).get("operations", [])):
            if operation.get("output") in dependencies:
                dependencies.update(operation[key] for key in ("column", "index", "denominator") if key in operation)
        warning_text = {
            "observed_sample_mean": "Yayımlanan gözlemlerin ağırlıksız aritmetik ortalaması kullanılıyor; yayın takviminin eksiksiz olduğu varsayılmıyor.",
            "heterogeneous_scopes_aligned": "Seriler yalnızca döneme göre eşleştirildi; kapsadıkları kurumların veya nüfusların aynı olduğu varsayılmıyor.",
            "partial_period_blocked": "Eksik alt dönemleri bulunan toplamlar hesaplanmadı; ilgili değerler boş bırakıldı.",
            "semantics_unreviewed": "Kaynak serisinin anlamı inceleme gerektiriyor; yalnızca özgün gözlemler gösteriliyor.",
            "native_calendar_unverified": "Yalnızca kaynaktaki gözlem tarihleri kullanılıyor; yayın takviminin eksiksiz olduğu varsayılmıyor.",
            "zero_denominator": "Paydası sıfır olan değişim veya oran hesaplanmadı.",
            "invalid_deflator": "Geçersiz fiyat endeksi bulunan dönemlerde reel değer hesaplanmadı.",
            "cross_scope_comparison": "Farklı kapsamlar açık karşılaştırma amacıyla bir araya getirildi; kapsam eşdeğerliği doğrulanmadı.",
            "missing_result": "Kayıtlı sonuçta eksik gözlemler var; doldurma yapılmadı.",
            "group_missing_observations": "Bazı gruplarda gözlem bulunmuyor; eksik gruplar sıfır kabul edilmiyor.",
            "group_populations_not_summed": "Grup kapsamları korunuyor; grupların toplanabilir olduğu varsayılmıyor.",
        }
        for note in lineage.get("warnings", []):
            if not isinstance(note, dict):
                warnings.append("Kayıtlı kaynakta ek yöntem notları bulunuyor; analiz yöntemini inceleyin.")
                continue
            if note.get("column") and note["column"] not in dependencies:
                continue
            prefix = metadata[note["column"]]["label"] + ": " if note.get("column") in metadata else ""
            warnings.append(prefix + warning_text.get(note.get("code"), "Kaynak verisinin kayıtlı ek yöntem notları incelenmelidir."))
        series = []
        for index, col in enumerate(selected):
            meta = metadata[col]
            unit = f"endeks ({base_period}=100)" if normalize == "index100" else meta["unit"]
            series.append({"column": col, "label": meta["label"], "unit": unit,
                           "axis": "right" if layout == "dual_axis" and index == 1 else "left",
                           "values": normalized[col], "raw_values": raw[col], "raw_unit": meta["unit"],
                           "summary": _summary(raw[col], periods, meta, grouped=bool(group_by)),
                           "display_summary": _summary(normalized[col], periods, {**meta, "unit": unit}, normalized=normalize == "index100", grouped=bool(group_by))})
        spec = {"kind": kind, "columns": selected, "layout": layout, "normalize": normalize,
                "orientation": orientation, "x": x, "base_period": base_period,
                "frequency": manifest.get("plan", {}).get("frequency") or lineage.get("frequency")}
        source_labels = []
        for col in [*selected, *([x] if x else [])]:
            binding = metadata[col]["binding"]
            system = {"TCMB_EVDS": "TCMB EVDS", "BDDK_MONTHLY": "BDDK Aylık Bülten", "BDDK_WEEKLY": "BDDK Haftalık Bülten", "BDDK_FINTURK": "BDDK FinTürk"}.get(binding.get("source_system"), _text(binding.get("source_system")))
            title = _text(binding.get("title"))
            label = ": ".join(part for part in [system, title] if part)
            if not label:
                label = f"Kayıtlı analiz: {metadata[col]['label']} (kaynak açıklaması bulunmuyor)"
            if label not in source_labels:
                source_labels.append(label)
        title = args.get("title") or " · ".join(metadata[col]["label"] for col in selected)
        if len(title) > 160:
            title = title[:157].rstrip() + "..."
        subtitle = f"{periods[0]} - {periods[-1]} · {len(frame)} kayıt"
        if layout == "dual_axis":
            subtitle += f" · Sol: {series[0]['unit']} · Sağ: {series[1]['unit']}"
        result = {"status": "ok", "analysis_id": manifest["analysis_id"], "workspace_id": self.workspace_id,
                  "spec": spec, "title": title, "subtitle": subtitle, "periods": periods, "series": series,
                  "warnings": list(dict.fromkeys(warnings)), "sources": source_labels,
                  "recommendations": [], "row_count": len(frame), "complete": True, "group_by": group_by,
                  "available_columns": [{key: metadata[col][key] for key in ("column", "label", "unit", "kind")} for col in available],
                  "provenance": {"snapshot_id": manifest.get("snapshot_id"), "data_sha256": manifest.get("data_sha256"), "lineage_ref": manifest["analysis_id"]}}
        if kind == "scatter":
            result.update(x_values=self._values(frame, x), x_column=x, x_label=metadata[x]["label"], x_unit=metadata[x]["unit"])
        if group_by:
            self._grouped_payload(result, frame, metadata[selected[0]], group_by)
        result["recommendations"] = self._recommendations(result, raw, metadata)
        return result

    @staticmethod
    def _grouped_payload(result, frame, meta, group_by):
        members = list(dict.fromkeys(frame[group_by].tolist()))
        for member in members:
            if isinstance(member, (int, float)) or pd.api.types.is_integer(member):
                _number(member)
            elif not isinstance(member, str):
                raise ChartError("Grup boyutu metin veya güvenli sayı olmalıdır.")
        labels = meta["binding"].get("dimension_labels", {}).get(group_by, {})
        categories = [_text(labels.get(str(member), member)) for member in members]
        dimensions = [{group_by: int(member) if pd.api.types.is_integer(member) else member} for member in members]
        result["categories"] = categories
        result["category_dimensions"] = dimensions
        if result["spec"]["kind"] == "bar":
            result["point_dimensions"] = dimensions
            result["warnings"].append("Gruplar kendi kaynak kapsamlarıyla gösterilir; toplam veya gruplar arası büyüme hesaplanmaz.")
            return
        periods = list(dict.fromkeys(frame.period.tolist()))
        if len(periods) * len(members) > 10000:
            raise ChartError("Isı haritası 10.000 hücre sınırını aşıyor; grupları veya dönemleri daraltın.", "ROW_LIMIT")
        column = result["spec"]["columns"][0]
        values = {(period, member): _number(value) for period, member, value in frame[["period", group_by, column]].itertuples(index=False, name=None)}
        cells = []
        for pi, period in enumerate(periods):
            for ci, member in enumerate(members):
                value = values.get((period, member))
                cells.append({"period_index": pi, "category_index": ci, "value": value, "raw_value": value,
                              "period": period, "column": column, "dimensions": dimensions[ci],
                              "source_row_available": (period, member) in values})
        result.update(periods=periods, cells=cells)
        result["series"][0].update(values=[cell["value"] for cell in cells], raw_values=[cell["raw_value"] for cell in cells])
        result["series"][0]["summary"] = _summary(result["series"][0]["values"], [], meta, grouped=True)
        result["series"][0]["display_summary"] = result["series"][0]["summary"].copy()
        result["warnings"].append("Her hücre ayrı grup ve dönemi gösterir. Kayıtlı sorguda yer almayan grup-dönem çiftleri boş bırakılır; sıfır kabul edilmez.")

    @staticmethod
    def _recommendations(result, raw, metadata):
        spec, series = result["spec"], result["series"]
        recommendations = []
        if result["group_by"]:
            if spec["kind"] == "bar":
                recommendations.append({"label": "Yatay çubuklar", "prompt": "Bu grafiği grup adları kolay okunacak şekilde yatay çubuk grafiğine dönüştür.", "reason": "Kayıtlı tek dönemli grupları etiketleriyle karşılaştırır."})
            recommendations.append({"label": "Kaynak kaydını incele", "prompt": "Bu grafikteki en yüksek gözlemin dönemini, grubunu ve kaynak kaydını açıkla; nedensellik yorumu yapma.", "reason": "Kayıtlı gözlemin kaynak bağlantısını incelemeyi sağlar."})
            return recommendations
        monetary = next((item for item in series if metadata[item["column"]]["schema"].get("currency")
                         and metadata[item["column"]]["kind"] in {"stock", "flow"}
                         and metadata[item["column"]]["schema"].get("status") == "ready"), None)
        rate = next((item for item in series if metadata[item["column"]]["kind"] == "rate" and item["raw_unit"] == "%"), None)
        annual_pair = False
        if monetary and spec["frequency"] == "monthly":
            observations = dict(zip(result["periods"], raw[monetary["column"]]))
            try:
                annual_pair = any(value is not None and observations.get(str(pd.Period(period, freq="M") - 12)) is not None
                                  and observations[str(pd.Period(period, freq="M") - 12)] > 0
                                  for period, value in observations.items())
            except ValueError:
                annual_pair = False
        if monetary and annual_pair:
            recommendations.append({"label": "Yıllık değişimi incele", "prompt": f"Bu analizdeki {monetary['label']} ({monetary['column']}) sütununun aynı aya göre yıllık yüzde değişimini hesapla ve grafikte göster; önceki yıl gözlemi olmayan dönemleri boş bırak.", "reason": "Kayıtlı aylık parasal seri en az 13 dönem içeriyor."})
        elif rate and rate["summary"]["change"] is not None:
            recommendations.append({"label": "Faiz farkını incele", "prompt": f"{rate['label']} ({rate['column']}) sütununun ilk ve son dolu dönemi arasındaki değişimi yüzde puan olarak açıkla; dönemleri ve kaynak kayıtlarını belirt.", "reason": "Kayıtlı faiz serisinin iki farklı dönemde dolu gözlemi var."})
        if len(series) > 1 and spec["layout"] != "panels":
            recommendations.append({"label": "Ayrı paneller", "prompt": "Bu grafikteki seçili serileri tarih sırasını ve değerleri koruyarak ayrı panellerde göster.", "reason": "Her serinin kendi ölçeğini okunabilir kılar."})
        if len(series) > 1 and spec["normalize"] == "none" and spec["kind"] != "scatter" and all(values[0] is not None and values[0] > 0 for values in raw.values()) and all(metadata[col]["kind"] != "unknown" and metadata[col]["schema"].get("status") != "review_required" for col in raw):
            recommendations.append({"label": "Başlangıcı 100 yap", "prompt": f"Seçili serileri yalnızca grafik görünümünde {result['periods'][0]}=100 olacak şekilde normalize et; özgün analiz değerlerini koru.", "reason": "Seçili serilerin aynı ilk dönemde pozitif gözlemleri var."})
        if len(result["available_columns"]) >= 2 and spec["kind"] != "scatter" and sum(item["summary"]["first"] is not None for item in series) >= 2:
            cols = [item["column"] for item in result["available_columns"][:2]]
            recommendations.append({"label": "Birlikte değişimi gör", "prompt": f"Bu analizde {cols[0]} yatay eksende, {cols[1]} dikey eksende olacak şekilde dağılım grafiği oluştur; dönemleri koru, nedensellik sonucu çıkarma.", "reason": "Aynı dönemlere ait iki kayıtlı sayısal sütun var."})
        if any(item["summary"]["first_period"] is not None and item["summary"]["first_period"] != item["summary"]["last_period"] for item in series):
            recommendations.append({"label": "İlk ve son gözlem", "prompt": "Bu grafikteki serilerin ilk ve son dolu gözlemlerini birimleriyle karşılaştır; eksik dönemleri ve varsa reel fiyat bazını belirt.", "reason": "Gözlenen başlangıç ve bitiş değerlerini, hesaplanmış grafik özetleriyle karşılaştırır."})
        return recommendations[:3]

    def _atomic(self, target, encoded):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def create_chart(self, args):
        payload = self._build(args)
        encoded = _encode(payload)
        chart_id = "chart_" + hashlib.sha256(encoded).hexdigest()
        with self.store._lock(self.workspace_id):
            target = self._path(chart_id + ".json")
            if target.exists():
                if target.read_bytes() != encoded:
                    raise ChartError("Kayıtlı grafik özeti eşleşmiyor.", "ARTIFACT_HASH_MISMATCH")
            else:
                self._atomic(target, encoded)
            self._atomic(self._path("latest", payload["analysis_id"] + ".json"), _encode({"analysis_id": payload["analysis_id"], "chart_id": chart_id}))
        return self._compact(payload, chart_id)

    @staticmethod
    def _compact(payload, chart_id):
        return {key: payload[key] for key in ("status", "analysis_id", "title", "spec", "recommendations", "row_count", "complete")} | {
            "chart_id": chart_id, "artifact_id": chart_id, "artifact_ref": chart_id,
            "summary": [{"column": series["column"], "label": series["label"], "unit": series["raw_unit"],
                         **series["summary"], "display_unit": series["unit"],
                         "display_summary": series["display_summary"]} for series in payload["series"]],
            "warnings": payload["warnings"]}

    def load_artifact(self, chart_id):
        if not isinstance(chart_id, str) or not re.fullmatch(r"chart_[a-f0-9]{64}", chart_id):
            raise ChartError("Geçersiz grafik kimliği.")
        encoded = self._path(chart_id + ".json").read_bytes()
        if hashlib.sha256(encoded).hexdigest() != chart_id.removeprefix("chart_"):
            raise ChartError("Grafik dosyası içerik özetiyle eşleşmiyor.", "ARTIFACT_HASH_MISMATCH")
        payload = json.loads(encoded)
        if payload.get("workspace_id") != self.workspace_id:
            raise ChartError("Grafik başka bir çalışma alanına ait.", "WORKSPACE_MISMATCH")
        _, manifest = self._load(payload["analysis_id"])
        if payload.get("provenance", {}).get("data_sha256") != manifest.get("data_sha256"):
            raise ChartError("Grafik ve analiz kaynak özeti eşleşmiyor.", "ARTIFACT_HASH_MISMATCH")
        return {**payload, "chart_id": chart_id, "artifact_ref": chart_id}

    def get_chart(self, analysis_id):
        self._load(analysis_id)
        pointer = self._path("latest", analysis_id + ".json")
        if pointer.exists():
            latest = json.loads(pointer.read_text())
            if latest.get("analysis_id") != analysis_id:
                raise ChartError("Grafik işaretçisi farklı bir analize ait.", "ARTIFACT_HASH_MISMATCH")
            chart = self.load_artifact(latest.get("chart_id"))
            if chart["analysis_id"] != analysis_id:
                raise ChartError("Kayıtlı grafik farklı bir analize ait.", "ARTIFACT_HASH_MISMATCH")
            return chart
        return self._build({"analysis_id": analysis_id})

    def extra_tools(self):
        properties = {"analysis_id": {"type": "string"},
                      "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6, "uniqueItems": True},
                      "x": {"type": "string"}, "title": {"type": "string", "minLength": 1, "maxLength": 160}}
        properties.update({name: {"type": "string", "enum": list(options)} for name, options in ENUMS.items()})

        def handler(arguments):
            try:
                return self.create_chart(arguments)
            except (ChartError, StoreError, ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
                return {"status": "blocked", "code": getattr(exc, "code", "CHART_ERROR"), "message": str(exc)}

        def recover(arguments, intent):
            try:
                if intent.get("workspace_id") != self.workspace_id:
                    raise ChartError("Kurtarma isteği farklı çalışma alanına ait.", "WORKSPACE_MISMATCH")
                payload = self._build(arguments)
                chart_id = "chart_" + hashlib.sha256(_encode(payload)).hexdigest()
                if self._path(chart_id + ".json").exists():
                    self.load_artifact(chart_id)
                    pointer = self._path("latest", payload["analysis_id"] + ".json")
                    if pointer.exists():
                        latest = json.loads(pointer.read_text())
                        return {**self._compact(payload, chart_id), "recovered": True,
                                "superseded": latest.get("chart_id") != chart_id}
                return {**self.create_chart(arguments), "recovered": True}
            except (ChartError, StoreError, ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
                return {"status": "blocked", "code": getattr(exc, "code", "CHART_ERROR"), "message": str(exc)}

        return {"create_chart": {"schema": {"type": "function", "function": {
            "name": "create_chart", "description": "Create or revise a chart view of a complete saved analysis. Uses original values and source units; mixed units use panels. Never modifies analysis data. For scatter, x and columns (y) must be distinct. Index100 requires positive values for every selected series in the same first period. Grouped data uses one-period bars or group-period heatmaps.",
            "parameters": {"type": "object", "properties": properties, "required": ["analysis_id"], "additionalProperties": False}}},
            "handler": handler, "mutating": True, "recover": recover}}
