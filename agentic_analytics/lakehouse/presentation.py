"""A readable default view over saved columns, without changing stored values."""

import re


_WORDS = {"sektor":"sektör", "orani":"oranı", "degisim":"değişim", "buyume":"büyüme",
          "aylik":"aylık", "yillik":"yıllık", "karsilastirma":"karşılaştırma", "kar":"kâr"}
_GENERIC = {"amount", "value", "analysis value", "source value", "result"}


def _readable(value):
    text = " ".join(str(value or "").replace("_", " ").split())
    match = re.search(r"\s*\[([^]]+)\]$", text)
    if match and match[1].casefold() in text[:match.start()].casefold().split():
        text = text[:match.start()]
    words = [_WORDS.get(word.casefold(), word) for word in text.split()]
    text = " ".join(words)
    if text and text.isupper():
        text = text.lower().replace("i\u0307", "i")
    return (text[:1].upper() + text[1:])[:180]


def column_quantity_lineage(columns, operations, source_columns=()):
    """Track the current quantity represented by each alias in operation order."""
    # Alias names can be overwritten in place. Scale inherits the input's
    # current quantity identity; every other operation creates a new one.
    # Older descendants retain their identity when an input alias is replaced.
    identities = {name:("source", name) for name in columns}
    origins = {identity:{"column":name, "operation":None} for name, identity in identities.items()}
    scaled, order = {}, {}
    for index, operation in enumerate(operations):
        source, output = operation.get("column"), operation.get("output")
        if source not in identities or not output:
            continue
        if operation.get("op") == "scale":
            identities[output] = identities[source]
            scaled[output] = True
        else:
            overwritten = source == output or output in order or output in source_columns
            label_column = origins[identities[source]]["column"] if overwritten else output
            identity = ("operation", index)
            identities[output] = identity
            origins[identity] = {"column":label_column, "operation":operation.get("op"), "overwritten":overwritten}
            scaled[output] = False
        order[output] = index
    return identities, origins, scaled, order


def analysis_presentation(frame, manifest):
    """Return actual default column keys and plain-text labels for every key.

    Only recorded scale operations form duplicate families. Their values are
    never recalculated here. Prefer a scale shared by independent amount series;
    keep ratio, difference and other analytical outputs as separate columns.
    """
    schema, lineage = manifest.get("schema", {}), manifest.get("lineage", {})
    operations = lineage.get("operations") or manifest.get("plan", {}).get("operations") or manifest.get("plan", {}).get("request", {}).get("operations", [])
    sources = lineage.get("sources", {})
    if not sources and lineage.get("groups"):
        sources = next(iter(lineage["groups"].values())).get("sources", {})
    identities, origins, scaled, order = column_quantity_lineage(frame, operations, sources)
    families = {}
    for column in frame:
        families.setdefault(identities[column], []).append(column)
    def signature(name):
        meta = schema.get(name, {})
        return meta.get("unit"), meta.get("currency"), meta.get("scale", 1)
    coverage = {}
    for identity, family in families.items():
        if origins[identity]["operation"]:
            continue
        for sign in {signature(name) for name in family if schema.get(name, {}).get("currency")}:
            coverage[sign] = coverage.get(sign, 0) + 1
    chosen = {identity:max(family, key=lambda name:(coverage.get(signature(name), 0), scaled.get(name, False), order.get(name, -1)))
              for identity, family in families.items()}
    columns = list(chosen.values())
    def source_label(column):
        origin = origins[identities[column]]
        original = origin["column"]
        source = sources.get(original, {})
        binding = source.get("binding", {})
        title = binding.get("title") or schema.get(original, {}).get("label") or ""
        # Imported bindings often have machine table/column names as titles.
        technical = binding.get("source_system") == "SESSION_DATASET" or re.match(r"^[a-z][a-z0-9_]*\s*:\s*[a-z][a-z0-9_]*$", title)
        if not title or technical:
            title = original
        if _readable(title).casefold() in _GENERIC:
            dimensions = source.get("dimensions", {})
            title = dimensions.get("line_item") or ("Tutar" if schema.get(column, {}).get("currency") else "Değer")
        label = _readable(title)
        dimensions = source.get("dimensions") or (schema.get(original, {}).get("scope") or {}).get("dimensions", {})
        labels = binding.get("dimension_labels") or {}
        shown = [str(labels.get(key, {}).get(str(value), value)) for key, value in dimensions.items()
                 if key not in {"line_item", lineage.get("group_by")} and str(labels.get(key, {}).get(str(value), value)).casefold() not in label.casefold()]
        if shown:
            label += " (" + ", ".join(shown) + ")"
        system = str(binding.get("source_system", ""))
        if not technical and system and system not in {"TEST", "FIXTURE", "SESSION_DATASET"}:
            publisher = system.split("_")[0]
            if publisher.casefold() not in label.casefold():
                label = publisher + " " + label[:1].lower() + label[1:]
        if schema.get(column, {}).get("unit") == "percent":
            label = re.sub(r"\s+(?:yuzde|yüzde|pct|percent)$", "", label, flags=re.I)
        if origin.get("overwritten"):
            transformation = {"growth":"büyüme", "difference":"değişim", "deflate":"reel değer", "ratio":"oran"}.get(origin["operation"])
            if transformation and transformation not in label.casefold():
                label += f" ({transformation})"
        return label
    labels = {column:({"period":"Dönem", "line_item":"Kalem", "rank":"Sıra"}.get(column) or source_label(column)) for column in frame}
    return {"columns":columns, "labels":labels}
