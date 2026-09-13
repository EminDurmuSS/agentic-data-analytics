"use strict";
// Charts consume the complete saved analysis. Table pagination never supplies data here.
window.AnalysisCharts = (() => {
  const q = (selector) => document.querySelector(selector);
  const node = (tag, text, cls) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (cls) item.className = cls;
    return item;
  };
  const palette = ["#226348", "#5279a8", "#c07a33", "#90649d", "#469397", "#b25c60", "#7d843c", "#607684", "#8c564b", "#b0648c", "#578a61", "#796bb0"];
  const kindNames = { auto: "Otomatik", line: "Çizgi", bar: "Çubuk", area: "Alan", scatter: "Dağılım", heatmap: "Isı haritası" };
  const periodIndex = "__period_index__";
  const seriesKey = (series) => series.series_id || series.column;
  const numeric = (v) => typeof v === "number" && Number.isFinite(v) ? v : null;
  const number = (v) => v === null || v === undefined ? "Gözlem yok" :
    v && typeof v === "object" && /^-?\d+$/.test(v.$integer)
      ? new Intl.NumberFormat("tr-TR").format(BigInt(v.$integer))
      : typeof v === "number" ? new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 }).format(v) : String(v);
  const compact = (v) => {
    const magnitude = Math.abs(v);
    const divisor = magnitude >= 1e9 ? 1e9 : magnitude >= 1e6 ? 1e6 : 1;
    return new Intl.NumberFormat("tr-TR", divisor === 1 ? { maximumSignificantDigits: 6 } : { maximumFractionDigits: 2 }).format(v / divisor)
      + (divisor === 1e9 ? " mlr" : divisor === 1e6 ? " mn" : "");
  };
  let hooks, payload = null, chart = null, generation = 0, controller = null, endpoint = null;
  let observer, resizeFrame, selected = {}, expanded = false, returnFocus = null, saving = false;
  const period = (v) => hooks?.periodLabel(v) || String(v ?? "");
  const seriesLabel = (series) => payload?.group_by ? series.label
    : payload?.presentation?.labels?.[series.column] || hooks?.columnLabel?.(series.column, series.label) || series.label;
  const sources = () => payload?.presentation?.sources || (payload?.sources || []).map((label) => ({ label }));
  const observedPeriodCount = () => new Set((payload?.periods || []).filter((_, index) =>
    payload.series.some((series) => numeric(series.values?.[index]) !== null))).size;
  const wrap = (text, size = 65) => {
    const lines = [""];
    for (const word of String(text || "").split(/\s+/)) {
      if ((lines.at(-1) + " " + word).length > size && lines.at(-1)) lines.push(word);
      else lines[lines.length - 1] += (lines.at(-1) ? " " : "") + word;
    }
    return lines.join("\n");
  };
  function dispose() {
    if (chart) chart.dispose();
    chart = null;
    q("#chart").replaceChildren();
  }
  function clear() {
    generation++;
    controller?.abort();
    controller = null;
    saving = false;
    payload = null;
    endpoint = null;
    selected = {};
    dispose();
    if (expanded) expand(false);
    q("#chart-body").hidden = true;
    q("#chart-status").hidden = false;
    q("#chart-status").textContent = "Bir analiz oluşturduğunuzda grafiği burada inceleyebilirsiniz.";
  }
  function status(message, error = false) {
    const holder = q("#chart-status");
    holder.hidden = !message;
    holder.classList.toggle("error", error);
    holder.replaceChildren();
    if (message) holder.append(node("span", message));
    if (error) {
      const retry = node("button", "Yeniden dene", "text-button");
      retry.type = "button";
      retry.onclick = () => endpoint && request(endpoint);
      holder.append(retry);
    }
  }
  async function request(path, spec) {
    const version = ++generation;
    controller?.abort();
    controller = new AbortController();
    status(spec ? "Grafik görünümü kaydediliyor…" : "Analizin tamamından grafik hazırlanıyor…");
    saving = Boolean(spec);
    updateBusy();
    try {
      const result = await hooks.api(path, { signal: controller.signal,
        ...(spec ? { method: "POST", body: JSON.stringify(spec) } : {}) });
      if (version !== generation || path !== endpoint) return;
      if (result.status !== "ok" || result.complete !== true)
        throw new Error(result.message || result.errors?.map((item) => item.message).join("; ") || "Grafik verisi eksiksiz alınamadı.");
      payload = result;
      selected = {};
      status("");
      renderContent();
    } catch (error) {
      if (version !== generation || error.name === "AbortError") return;
      status(error.message || "Grafik yüklenemedi.", true);
    } finally {
      if (version === generation) { saving = false; updateBusy(); }
    }
  }
  async function load(workspacePath, analysisId, focus = false) {
    const next = workspacePath + "/analyses/" + encodeURIComponent(analysisId) + "/chart";
    if (endpoint !== next) {
      dispose();
      payload = null;
      q("#chart-body").hidden = true;
    }
    endpoint = next;
    if (focus) hooks.showTab("chart");
    await request(next);
  }
  function choices() {
    return (payload.available_columns || payload.series.map((s) => ({ column: s.column, label: s.label, unit: s.unit })))
      .map((item) => ({ ...item, label: seriesLabel(item) }));
  }
  const readableSubtitle = () => String(payload.subtitle || "").replace(/\b\d{4}-(?:Q[1-4]|H[12]|\d{2}(?:-\d{2})?)\b/g, period)
    + (payload.spec.kind === "heatmap" ? " · Değerler özgün analiz birimleriyle gösterilir." : "");
  function setSettingAvailability(control, enabled, reason = "") {
    control.disabled = !enabled;
    const hint = control.parentElement.querySelector(".chart-setting-hint");
    if (hint) {
      hint.hidden = enabled;
      hint.textContent = reason;
    }
  }
  function updateSettingAvailability() {
    const kind = payload.spec.kind;
    setSettingAvailability(q("#chart-layout"), kind !== "heatmap", "Isı haritası satır ve dönem matrisini tek görünümde gösterir.");
    setSettingAvailability(q("#chart-normalize"), observedPeriodCount() > 1 && !payload.group_by && !["scatter", "heatmap"].includes(kind),
      observedPeriodCount() < 2 ? "Başlangıca göre karşılaştırma için en az iki gözlem dönemi gerekir." : "Bu grafik türü yalnız özgün analiz değerleriyle gösterilir.");
    setSettingAvailability(q("#chart-x"), kind === "scatter", "Bu grafik türünde yatay eksen dönemlerden oluşur.");
    setSettingAvailability(q("#chart-orientation"), kind === "bar", "Yön seçimi yalnızca çubuk grafiğinde uygulanır.");
    q("#chart-layout").querySelector('option[value="dual_axis"]').disabled = kind === "scatter" || Boolean(payload.group_by);
  }
  function renderContent() {
    q("#chart-body").hidden = false;
    q("#chart-title").textContent = payload.title || "Verinizin görünümü";
    q("#chart-subtitle").textContent = readableSubtitle();
    q("#chart-custom-title").value = payload.title || "";
    q("#chart-layout").value = payload.spec.layout || "auto";
    q("#chart-normalize").value = payload.spec.normalize || "none";
    q("#chart-orientation").value = payload.spec.orientation || "vertical";
    const columns = q("#chart-columns"), x = q("#chart-x");
    columns.replaceChildren();
    x.replaceChildren();
    for (const item of choices()) {
      const label = node("label"), input = node("input");
      input.type = "checkbox";
      input.value = item.column;
      input.checked = (payload.spec.columns || payload.series.map((s) => s.column)).includes(item.column);
      label.append(input, node("span", item.label + (item.unit ? " · " + item.unit : "")));
      columns.append(label);
      const option = node("option", item.label + (item.unit ? " · " + item.unit : ""));
      option.value = item.column;
      x.append(option);
    }
    if (payload.spec.kind === "scatter") {
      const option = node("option", "Dönem sırası (tek metrik)");
      option.value = periodIndex;
      x.append(option);
    }
    x.value = payload.spec.x || payload.x_column || (payload.x_mode === "period_index" ? periodIndex : choices()[0]?.column || "");
    updateSettingAvailability();
    q("#chart-kinds").replaceChildren();
    for (const [kind, name] of Object.entries(kindNames)) {
      if (kind === "auto") continue;
      const button = node("button", name, "chart-kind");
      button.type = "button";
      button.setAttribute("aria-pressed", String(kind === payload.spec.kind));
      button.dataset.unavailable = "false";
      if (payload.group_by && kind === "scatter") {
        button.dataset.unavailable = "true";
        button.title = "Gruplu verilerde çizgi, çubuk, alan veya ısı haritasını seçin.";
      }
      if (observedPeriodCount() < 2 && ["line", "area"].includes(kind) && kind !== payload.spec.kind) {
        button.dataset.unavailable = "true";
        button.title = "Dönemler arası değişimi göstermek için en az iki gözlem dönemi gerekir.";
      }
      button.onclick = () => {
        const overrides = { kind, ...(kind !== "bar" ? { orientation: "vertical" } : {}) };
        if (kind === "heatmap") {
          // A heatmap shows raw values on one color scale, so drop any inherited
          // normalization/dual-axis from the previous view instead of erroring out.
          overrides.normalize = "none";
          overrides.layout = "auto";
        }
        if (kind === "scatter") {
          const cols = formSpec().columns;
          const xColumn = choices().find((item) => !cols.includes(item.column))?.column || (cols.length > 1 ? cols[0] : periodIndex);
          overrides.x = xColumn;
          overrides.normalize = "none";
          overrides.layout = "auto";
          overrides.columns = xColumn === periodIndex ? cols : cols.filter((col) => col !== xColumn);
          if (!overrides.columns.length) overrides.columns = choices().filter((item) => item.column !== xColumn).slice(0, 1).map((item) => item.column);
        }
        save(overrides);
      };
      q("#chart-kinds").append(button);
    }
    renderKpis();
    renderLegend();
    const guidance = q("#chart-guidance");
    guidance.replaceChildren();
    guidance.hidden = !(observedPeriodCount() === 1 && ["line", "area"].includes(payload.spec.kind));
    if (!guidance.hidden) {
      const text = node("div");
      text.append(node("strong", "Bu analiz tek dönem içeriyor"),
        node("p", "Dönemler arasında çizgi veya alan oluşmaz. Tutarların büyüklüğünü çubuk grafikle karşılaştırabilirsiniz."));
      const button = node("button", "Çubuk grafik göster", "chart-action");
      button.id = "chart-use-bars";
      button.type = "button";
      button.onclick = () => save({ kind: "bar", orientation: "vertical" });
      guidance.append(text, button);
    }
    q("#chart-note").textContent = number(payload.row_count) + " satırın tamamı · Yakınlaştırın, bir noktayı seçip kaynağını inceleyin.";
    const hasSharedScale = sharedPanelRanges().size > 0;
    const warnings = (payload.presentation?.warnings || payload.warnings || []).filter((note) => !(hasSharedScale && note.code === "separate_units"));
    if (hooks.renderChartWarnings) hooks.renderChartWarnings(q("#chart-warnings"), warnings);
    else if (hooks.renderWarnings) hooks.renderWarnings(q("#chart-warnings"), warnings);
    else q("#chart-warnings").replaceChildren(...warnings.map((w) => node("p", w.message || w, w.level === "info" ? "chart-info" : "warning")));
    if (hasSharedScale)
      q("#chart-warnings").prepend(node("p", "Birimi ve ölçüm yöntemi uyumlu tutarlar aynı eksen ölçeğiyle gösterilir. Yüzdeler kendi ölçeğinde okunur.", "chart-info"));
    if (payload.presentation_notice)
      q("#chart-warnings").prepend(node("p", payload.presentation_notice, "chart-info chart-limited-note"));
    if (payload.spec.normalize === "index100")
      q("#chart-warnings").prepend(node("p", "Tüm seriler için ortak başlangıç: " + period(payload.spec.base_period || payload.periods[0]) + " = 100. Kaynak izi özgün analiz değerini gösterir.", "chart-info"));
    if (payload.spec.layout === "dual_axis")
      q("#chart-warnings").prepend(node("p", "İki eksenin ölçeği bağımsızdır; çizgilerin yüksekliği doğrudan büyüklük karşılaştırması değildir.", "chart-info"));
    q("#chart-sources").replaceChildren(node("strong", "KAYNAK"));
    for (const source of sources()) {
      const label = source.label + (source.page ? " · Sayfa " + source.page : "");
      let url;
      try { url = new URL(source.url); } catch (_) { /* A source can have no public URL. */ }
      const item = node(url && ["http:", "https:"].includes(url.protocol) ? "a" : "span", label);
      if (item.tagName === "A") {
        if (Number.isInteger(source.page) && source.page > 0 && /\.pdf$/i.test(url.pathname)) url.hash = "page=" + source.page;
        item.href = url.href; item.target = "_blank"; item.rel = "noopener noreferrer";
      }
      q("#chart-sources").append(item);
    }
    if (!sources().length) q("#chart-sources").append(node("span", "Kaynak ayrıntıları için bir noktayı seçin."));
    q("#chart-point").hidden = true;
    updateBusy();
    draw();
  }
  function renderKpis() {
    const holder = q("#chart-kpis");
    holder.replaceChildren();
    // Grouped payloads carry per-group cells, not per-metric summaries, so a KPI
    // card would render an empty "Gözlem yok" headline; suppress it for groups.
    if (payload.group_by) { holder.hidden = true; return; }
    const normalized = payload.spec.normalize === "index100";
    for (const [i, series] of payload.series.entries()) {
      const summary = series.summary || {}, card = node("article", undefined, "chart-kpi");
      card.style.setProperty("--series-color", palette[i % palette.length]);
      card.append(node("div", seriesLabel(series), "chart-kpi-label"));
      const value = node("div", undefined, "chart-kpi-value");
      value.append(node("strong", number(summary.last)), node("span", series.raw_unit || series.unit || ""));
      card.append(value, node("div", summary.last_period ? period(summary.last_period) : "Son geçerli gözlem", "chart-kpi-period"));
      if (observedPeriodCount() > 1 && numeric(summary.change) !== null) {
        const change = node("div", undefined, "chart-kpi-change");
        const sign = summary.change > 0 ? "+" : "";
        change.append(node("span", sign + number(summary.change) + " " + (summary.change_unit || series.raw_unit || series.unit || "")));
        if (numeric(summary.change_percent) !== null) change.append(node("span", "(" + (summary.change_percent > 0 ? "+" : "") + new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(summary.change_percent) + "%)"));
        card.append(change, node("small", "İlk geçerli gözleme göre" + (summary.first_period ? " · " + period(summary.first_period) : "")));
      }
      if (summary.missing_count) card.append(node("small", number(summary.missing_count) + " eksik gözlem"));
      if (normalized) card.append(node("small", "Özgün değer özeti"));
      holder.append(card);
    }
    holder.hidden = !holder.children.length;
  }
  function renderLegend() {
    const holder = q("#chart-legend");
    holder.replaceChildren();
    for (const [i, series] of payload.series.entries()) {
      const button = node("button", undefined, "chart-legend-item");
      button.type = "button";
      button.title = "Seriyi gizle veya göster";
      button.style.setProperty("--series-color", palette[i % palette.length]);
      button.setAttribute("aria-pressed", "true");
      button.append(node("i"), node("span", seriesLabel(series)), node("small", payload.spec.normalize === "index100" ? period(payload.spec.base_period || payload.periods[0]) + " = 100" : series.unit + (payload.spec.layout === "dual_axis" ? series.axis === "right" ? " · ikinci eksen" : " · ilk eksen" : "")));
      button.onclick = () => {
        const name = seriesKey(series);
        selected[name] = selected[name] === false;
        button.setAttribute("aria-pressed", String(selected[name]));
        if (payload.spec.kind === "heatmap") draw();
        else chart?.dispatchAction({ type: "legendToggleSelect", name });
      };
      holder.append(button);
    }
    holder.hidden = !holder.children.length;
  }
  function formSpec() {
    return { kind: payload.spec.kind, columns: [...q("#chart-columns").querySelectorAll("input:checked")].map((item) => item.value),
      layout: q("#chart-layout").value, normalize: q("#chart-normalize").value,
      title: q("#chart-custom-title").value.trim(), orientation: q("#chart-orientation").value,
      ...(payload.spec.kind === "scatter" ? { x: q("#chart-x").value } : {}) };
  }
  async function save(overrides = {}) {
    if (!payload || !endpoint || saving || hooks.isBusy()) return;
    const spec = { ...formSpec(), ...overrides };
    if (!spec.columns.length) { status("En az bir seri seçin.", true); return; }
    if (spec.kind !== "scatter") delete spec.x;
    if (!spec.title) delete spec.title;
    await request(endpoint, spec);
  }
  function panelGroups() {
    if (payload.spec.kind === "heatmap") return [payload.series];
    const normalized = payload.spec.normalize === "index100";
    if (payload.spec.layout === "panels") return payload.series.map((s) => [s]);
    if (["overlay", "dual_axis"].includes(payload.spec.layout) || normalized) return [payload.series];
    const groups = new Map();
    for (const series of payload.series) {
      const unit = series.unit || "Birim belirtilmemiş";
      if (!groups.has(unit)) groups.set(unit, []);
      groups.get(unit).push(series);
    }
    return [...groups.values()];
  }
  function heightFor() {
    if (!payload) return 430;
    if (payload.spec.kind === "heatmap") return Math.min(850, Math.max(460, (payload.categories?.length || 1) * 28 + 140));
    if (payload.spec.kind === "bar" && payload.spec.orientation === "horizontal")
      return panelGroups().length * Math.min(900, Math.max(280, payload.periods.length * 27 + 100)) + 55;
    return panelGroups().length > 1 ? panelGroups().length * 280 + 55 : expanded ? Math.max(490, Math.min(680, innerHeight - 400)) : 430;
  }
  function sharedPanelRanges() {
    const ranges = new Map();
    if (panelGroups().length < 2 || payload.spec.normalize === "index100" || ["scatter", "heatmap"].includes(payload.spec.kind)) return ranges;
    // Compatibility comes from verified financial metadata, not display labels.
    // Keep the full data range when a legend item is hidden.
    for (const group of payload.presentation?.unit_groups || []) {
      const members = payload.series.filter((series) => group.columns.includes(series.column));
      if (members.length < 2) continue;
      let low = 0, high = 0, count = 0;
      for (const series of members) for (const value of series.values || []) {
        if (numeric(value) === null) continue;
        low = Math.min(low, value); high = Math.max(high, value); count++;
      }
      if (!count) continue;
      const magnitude = Math.max(Math.abs(low), Math.abs(high));
      const step = Math.pow(10, Math.floor(Math.log10(magnitude || 1))) / 2;
      const lower = step > 0 ? Math.floor(low / step) * step : low;
      const upper = step > 0 ? Math.ceil(high / step) * step : high;
      const range = { min: Number.isFinite(lower) ? lower : low,
        max: magnitude === 0 ? 1 : Number.isFinite(upper) ? upper : high, scale: false };
      for (const series of members) ranges.set(series.column, range);
    }
    return ranges;
  }
  function exportFooter(width) {
    const notes = (payload.presentation?.warnings || []).filter((note) => note.level === "warning").map((note) => note.message);
    notes.push("Kaynak: " + sources().map((source) => source.label + (source.page ? " (s. " + source.page + ")" : "")).join(" · "));
    return [...new Set(notes)].map((text) => wrap(text, Math.floor((width - 72) / 6.4))).join("\n");
  }
  function options(width, height, exporting = false) {
    const spec = payload.spec, kind = spec.kind === "auto" ? "line" : spec.kind;
    const normalized = spec.normalize === "index100", unit = (s) => normalized ? period(spec.base_period || payload.periods[0]) + " = 100" : s.unit || "";
    const footer = exporting ? exportFooter(width) : "";
    const top = exporting ? 188 : 20, bottom = exporting ? Math.max(118, footer.split("\n").length * 17 + 50) : 62;
    const titles = [], graphic = [], grids = [], xAxes = [], yAxes = [], series = [], zoom = [];
    if (exporting) {
      titles.push({ text: wrap(payload.title, 96), subtext: wrap(readableSubtitle(), 140), left: 36, top: 24,
        textStyle: { fontSize: 23, lineHeight: 29, fontWeight: 600, color: "#20372d" }, subtextStyle: { color: "#627568", fontSize: 12, lineHeight: 18 } });
      graphic.push({ type: "text", left: 36, bottom: 23, style: { text: footer, font: "11px sans-serif", lineHeight: 17, fill: "#627568" } });
    }
    const valueAxis = (label, index) => ({ type: "value", gridIndex: index, name: label, nameGap: 14,
      nameTextStyle: { color: "#627568", fontSize: 11, align: "left" }, axisLabel: { color: "#738579", fontSize: 11, formatter: compact },
      axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: "#e6ece5", type: "dashed" } },
      scale: kind !== "bar" && kind !== "area" });
    const categoryAxis = (values, index) => ({ type: "category", gridIndex: index, data: values, boundaryGap: kind === "bar" || kind === "heatmap",
      axisLine: { lineStyle: { color: "#ccd9cc" } }, axisTick: { show: false },
      axisLabel: { color: "#738579", fontSize: 10, hideOverlap: true, margin: 13, formatter: (v) => period(v) } });
    if (kind === "heatmap") {
      const visibleSeries = payload.series.filter((series) => selected[series.column] !== false);
      const cats = payload.group_by
        ? payload.categories || payload.series.map((series) => series.label)
        : visibleSeries.map(seriesLabel);
      grids.push({ left: Math.min(185, width * 0.30), right: 34, top: top + 22, bottom: bottom + 64 });
      xAxes.push(categoryAxis(payload.periods, 0));
      yAxes.push({ ...categoryAxis(cats, 0), axisLabel: { color: "#526b5a", fontSize: 11, width: Math.min(165, width * 0.27), overflow: "truncate" }, splitArea: { show: true }, inverse: true });
      const categoryIndexes = new Map(visibleSeries.map((series, index) => [series.column, index]));
      const cells = (payload.cells || payload.series.flatMap((series, categoryIndex) => series.values.map((value, periodIndex) => ({ period_index: periodIndex, category_index: categoryIndex, value, raw_value: series.raw_values?.[periodIndex], period: payload.periods[periodIndex], column: series.column }))))
        .filter((cell) => selected[cell.column] !== false)
        .map((cell) => ({ ...cell,
          category_index: payload.group_by ? cell.category_index : categoryIndexes.get(cell.column),
          category_label: payload.group_by ? (payload.categories?.[cell.category_index] || "") : seriesLabel(visibleSeries[categoryIndexes.get(cell.column)]) }));
      const valid = cells.filter((cell) => numeric(cell.value) !== null);
      series.push({ name: payload.series[0]?.column || "value", type: "heatmap", data: valid.map((cell) => ({ value: [cell.period_index, cell.category_index, cell.value], cell })),
        label: { show: payload.periods.length <= 8 && cats.length <= 18, formatter: (p) => compact(p.value[2]), fontSize: 11 },
        itemStyle: { borderColor: "#fff", borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: "#235a43", borderWidth: 2 } } });
      const vals = valid.map((cell) => cell.value), low = vals.length ? vals.reduce((a, b) => Math.min(a, b), Infinity) : 0, high = vals.length ? vals.reduce((a, b) => Math.max(a, b), -Infinity) : 1;
      zoom.push({ type: "inside", xAxisIndex: 0, filterMode: "none" });
      if (cats.length > 22) zoom.push({ type: "slider", yAxisIndex: 0, right: 1, width: 12, top: top + 22, bottom: bottom + 32, start: 0, end: Math.min(100, 2200 / cats.length), filterMode: "none" });
      return finish({ visualMap: { min: low, max: high === low ? high + 1 : high, calculable: false, formatter: compact, orient: "horizontal", left: "center", bottom: exporting ? bottom - 42 : 12, itemHeight: 150, itemWidth: 10,
        text: [compact(high) + " " + unit(payload.series[0] || {}), compact(low)], textStyle: { color: "#627568", fontSize: 10 }, inRange: { color: low < 0 ? ["#b86c4b", "#f3f0dc", "#226348"] : ["#edf3df", "#9fbe98", "#226348"] } } });
    }
    const groups = panelGroups(), groupHeight = (height - top - bottom) / groups.length;
    const sharedRanges = sharedPanelRanges();
    const panelTitles = groups.map((group) => wrap(group.map(seriesLabel).join(" · "), Math.max(32, Math.floor(width / 8))));
    // Equal ranges also need equal plot heights when panel titles wrap.
    const extraTop = groups.length > 1 ? Math.max(...panelTitles.map((title) => 34 + 17 * title.split("\n").length)) : 24;
    const horizontal = kind === "bar" && spec.orientation === "horizontal";
    for (const [index, group] of groups.entries()) {
      const dual = spec.layout === "dual_axis" && group.length === 2;
      const distinctUnits = [...new Set(group.map(unit))];
      const panelTitle = panelTitles[index];
      const sharedRange = dual ? {} : sharedRanges.get(group[0]?.column) || {};
      const gridTop = top + index * groupHeight + extraTop;
      grids.push({ left: horizontal ? Math.min(175, width * 0.29) : 64, right: dual ? 72 : 28, top: gridTop, height: Math.max(110, groupHeight - extraTop - 36) });
      if (groups.length > 1) titles.push({ text: panelTitle, left: 22, top: top + index * groupHeight - 3,
        textStyle: { color: "#294f38", fontSize: 12, fontWeight: 600, lineHeight: 17 } });
      const xIndex = xAxes.length, yIndex = yAxes.length;
      if (kind === "scatter") {
        xAxes.push({ ...valueAxis((payload.x_label || payload.x_column || "X") + " · " + (payload.x_unit || ""), index), nameLocation: "middle", nameGap: 33, nameTextStyle: { fontSize: 11, color: "#627568", align: "center" } });
        yAxes.push(valueAxis(unit(group[0] || {}), index));
      } else if (horizontal) {
        xAxes.push({ ...valueAxis(unit(group[0] || {}), index), ...sharedRange });
        yAxes.push({ ...categoryAxis(payload.group_mode === "categories" || (payload.group_by && !payload.group_mode && payload.categories?.length === payload.periods.length) ? payload.categories : payload.periods, index), inverse: true,
          axisLabel: { color: "#627568", fontSize: 11, width: Math.min(150, width * 0.25), overflow: "truncate", formatter: (v) => period(v) } });
      } else {
        xAxes.push(categoryAxis(payload.group_mode === "categories" || (payload.group_by && !payload.group_mode && payload.categories?.length === payload.periods.length) ? payload.categories : payload.periods, index));
        yAxes.push({ ...valueAxis(distinctUnits[0], index), ...sharedRange });
      }
      if (dual && horizontal) xAxes.push({ ...valueAxis(unit(group[1]), index), position: "top", splitLine: { show: false }, nameTextStyle: { color: "#627568", fontSize: 11, align: "left" } });
      else if (dual) yAxes.push({ ...valueAxis(unit(group[1]), index), position: "right", splitLine: { show: false }, nameTextStyle: { color: "#627568", fontSize: 11, align: "right" } });
      for (const s of group) {
        const sIndex = payload.series.indexOf(s);
        series.push({ name: seriesKey(s), id: seriesKey(s), type: kind === "area" ? "line" : kind, xAxisIndex: xIndex + (dual && horizontal && group.indexOf(s) === 1 ? 1 : 0),
          yAxisIndex: yIndex + (dual && !horizontal && group.indexOf(s) === 1 ? 1 : 0),
          data: s.values.map((v, i) => ({ value: kind === "scatter" ? [numeric(payload.x_values?.[i]), numeric(v)] : numeric(v), originalIndex: i,
            column: s.column, dimensions: s.dimensions || payload.point_dimensions?.[i] || {}, source_row_available: s.source_row_available?.[i] ?? true })),
          connectNulls: false, smooth: false, showSymbol: s.values.length < 65, symbol: "circle", symbolSize: kind === "scatter" ? 9 : 6,
          lineStyle: { width: 2.5 }, areaStyle: kind === "area" ? { opacity: 0.13 } : undefined,
          itemStyle: { color: palette[sIndex % palette.length], borderRadius: kind === "bar" ? 3 : 0, opacity: kind === "scatter" ? 0.82 : 1 },
          barMaxWidth: 42, emphasis: { focus: "series", scale: 1.4 }, animation: false });
      }
    }
    if (kind !== "scatter") {
      const axis = horizontal ? "yAxisIndex" : "xAxisIndex";
      const range = { start: 0, end: horizontal && !exporting ? Math.min(100, 2500 / payload.periods.length) : 100 };
      zoom.push({ type: "inside", [axis]: groups.map((_, i) => i), filterMode: "none", zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false, ...range });
      zoom.push({ type: "slider", [axis]: groups.map((_, i) => i), filterMode: "none", ...range,
        ...(horizontal ? { orient: "vertical", right: 3, top: grids[0].top, bottom: bottom + 36, width: 12 }
          : { orient: "horizontal", bottom: exporting ? 85 : 12, left: 64, right: 28, height: 20 }),
        borderColor: "#dfe8dd", backgroundColor: "#f0f5ec", fillerColor: "rgba(68,120,81,0.13)", handleStyle: { color: "#8baa8e" }, handleSize: "100%",
        showDataShadow: false, textStyle: { color: "#758b79", fontSize: 10 }, brushSelect: false });
    }
    return finish({});
    function finish(extra) {
      return { backgroundColor: "#ffffff", color: palette, animation: false, aria: { enabled: true, decal: { show: false } },
        textStyle: { fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" },
        title: titles, graphic, grid: grids, xAxis: xAxes, yAxis: yAxes, series, dataZoom: zoom,
        legend: { show: exporting && kind !== "heatmap", data: payload.series.map(seriesKey), selected, left: 36, right: 36, top: 126, itemWidth: 15, itemHeight: 8,
          textStyle: { color: "#52704f", fontSize: 10, lineHeight: 16 },
          formatter: (name) => { const item = payload.series.find((s) => seriesKey(s) === name); return wrap((item ? seriesLabel(item) : name) + " · " + unit(item || {}), 72); } },
        tooltip: { trigger: "item", renderMode: "richText", confine: true, backgroundColor: "#fff", borderColor: "#d3dfd1", padding: [12, 15],
          textStyle: { color: "#20372d", fontSize: 12, lineHeight: 20 }, formatter: tooltip }, ...extra };
    }
  }
  function tooltip(p) {
    if (p.data?.cell) {
      const cell = p.data.cell;
      const series = payload.series.find((item) => item.column === cell.column);
      return (cell.category_label || payload.categories?.[cell.category_index] || "") + "\n" + period(cell.period) + "\n" + number(cell.value) + " " + (series?.unit || "") + "\nKaynak için tıklayın";
    }
    const s = payload.series.find((item) => seriesKey(item) === p.seriesName);
    if (!s) return "";
    const i = p.data?.originalIndex ?? p.dataIndex;
    const lines = [seriesLabel(s), period(payload.periods[i])];
    if (payload.point_dimensions?.[i]) lines.push(Object.values(payload.point_dimensions[i]).join(" · "));
    if (payload.spec.kind === "scatter") lines.push((payload.x_label || payload.x_column) + ": " + number(payload.x_values?.[i]) + " " + payload.x_unit);
    lines.push(number(s.values[i]) + " " + (payload.spec.normalize === "index100" ? "(" + period(payload.spec.base_period || payload.periods[0]) + " = 100)" : s.unit));
    if (payload.spec.normalize === "index100") lines.push("Özgün analiz: " + number(s.raw_values?.[i]) + " " + (s.raw_unit || s.unit));
    lines.push("Kaynak için tıklayın");
    return lines.join("\n");
  }
  function showPoint(p) {
    if (!p.data) return;
    const cell = p.data.cell, i = p.data.originalIndex ?? p.dataIndex;
    // For a heatmap the single ECharts series is named after series[0], so resolve
    // the clicked metric by the cell's own column to show its correct unit.
    const s = payload.series.find((item) => cell ? item.column === cell.column : seriesKey(item) === p.seriesName) || payload.series[0];
    if (!s) return;
    const value = cell ? cell.raw_value : s.raw_values?.[i] ?? s.values[i];
    const sourcePeriod = cell ? cell.period : payload.periods[i];
    const dimensions = cell?.dimensions || p.data.dimensions || s.dimensions || payload.point_dimensions?.[i] || {};
    const column = cell?.column || s.column;
    const holder = q("#chart-point");
    holder.replaceChildren();
    const info = node("div");
    info.append(node("strong", (cell ? cell.category_label || payload.categories?.[cell.category_index] : seriesLabel(s)) + " · " + period(sourcePeriod)));
    info.append(node("span", "Özgün analiz değeri: " + number(value) + " " + (s.raw_unit || s.unit)));
    if (payload.spec.normalize === "index100") info.append(node("small", "Grafikte: " + number(s.values[i]) + ". Kaynak izi, bu endeks dönüşümünden önceki değere aittir."));
    const button = node("button", "Kaynak izini incele ↗", "chart-action");
    button.type = "button";
    if ((cell?.source_row_available ?? p.data.source_row_available) === false) {
      button.disabled = true;
      info.append(node("small", "Bu grup-dönem çifti kayıtlı sorguda yer almıyor; kaynakta eksik veya sıfır olduğu anlamına gelmez."));
    }
    button.onclick = () => hooks.showEvidence(column, { period: sourcePeriod, ...dimensions });
    holder.append(info, button);
    holder.hidden = false;
  }
  function draw() {
    if (!payload || q("#tab-chart").hidden) return;
    const holder = q("#chart");
    if (!window.echarts) { status("Grafik kütüphanesi yüklenemedi. Sayfayı yenileyin.", true); return; }
    if (!payload.series.length || !payload.periods.length) {
      dispose();
      holder.append(node("p", "Bu analizde görselleştirilebilecek sayısal gözlem yok.", "chart-empty"));
      return;
    }
    const height = heightFor();
    holder.style.height = height + "px";
    if (!chart) {
      chart = echarts.init(holder, null, { renderer: "svg" });
      chart.on("click", showPoint);
    }
    chart.resize({ height });
    chart.setOption(options(holder.clientWidth, height), { notMerge: true });
    holder.setAttribute("aria-label", payload.title + ". " + payload.subtitle);
  }
  function resize() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      if (!payload || q("#tab-chart").hidden) return;
      if (!chart) draw();
      else {
        chart.resize();
        const holder = q("#chart"), next = options(holder.clientWidth, holder.clientHeight);
        // Reflow labels and panel spacing while keeping the user's zoom and legend choices.
        chart.setOption({ title: next.title, grid: next.grid, xAxis: next.xAxis, yAxis: next.yAxis });
      }
    });
  }
  function expand(force) {
    expanded = force === undefined ? !expanded : force;
    const studio = q("#chart-studio");
    if (expanded) returnFocus = document.activeElement;
    studio.classList.toggle("is-expanded", expanded);
    document.body.classList.toggle("chart-presenting", expanded);
    studio.setAttribute("role", expanded ? "dialog" : "region");
    if (expanded) studio.setAttribute("aria-modal", "true");
    else studio.removeAttribute("aria-modal");
    q("#chart-expand").replaceChildren(node("i", expanded ? "×" : "⤢", "chart-expand-icon"), node("span", expanded ? "Sunumu kapat" : "Genişlet"));
    q("#chart-expand").setAttribute("aria-label", expanded ? "Sunum görünümünü kapat" : "Grafiği genişlet");
    if (expanded) q("#chart-expand").focus();
    else returnFocus?.focus();
    requestAnimationFrame(draw);
  }
  async function download(format) {
    if (!payload || !window.echarts) return;
    const version = generation, exportPayload = payload;
    const holder = node("div");
    holder.style.cssText = "position:fixed;left:-20000px;top:0;width:1440px;pointer-events:none";
    const height = Math.max(720, heightFor() + 230 + Math.max(0, exportFooter(1440).split("\n").length * 17 + 50 - 118));
    holder.style.height = height + "px";
    document.body.append(holder);
    const exportChart = echarts.init(holder, null, { renderer: "svg", width: 1440, height });
    try {
      exportChart.setOption(options(1440, height, true));
      const svg = exportChart.renderToSVGString();
      let blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
      if (format === "png") {
        const imageData = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = () => reject(new Error("Grafik görseli okunamadı."));
          reader.readAsDataURL(blob);
        });
        const img = new Image();
        await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error("PNG oluşturulamadı.")); img.src = imageData; });
          const canvas = document.createElement("canvas");
          canvas.width = 2880; canvas.height = height * 2;
          const ctx = canvas.getContext("2d");
          ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
          if (!blob) throw new Error("PNG oluşturulamadı.");
      }
      if (version !== generation) return;
      const url = URL.createObjectURL(blob), link = node("a");
      link.href = url;
      link.download = (exportPayload.title || "analiz-grafigi").replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 100) + "." + format;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (version === generation) status(error.message, true); }
    finally { exportChart.dispose(); holder.remove(); }
  }
  function updateBusy() {
    const blocked = saving || Boolean(hooks?.isBusy());
    for (const button of document.querySelectorAll("#chart-kinds button, #chart-apply, #chart-use-bars")) button.disabled = blocked || button.dataset.unavailable === "true";
  }
  function configure(callbacks) {
    hooks = callbacks;
    q("#chart-settings-toggle").onclick = () => {
      const settings = q("#chart-settings");
      settings.hidden = !settings.hidden;
      q("#chart-settings-toggle").setAttribute("aria-expanded", String(!settings.hidden));
    };
    q("#chart-settings").onsubmit = (event) => { event.preventDefault(); save(); };
    q("#chart-expand").onclick = () => expand();
    q("#chart-reset").onclick = () => chart?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
    q("#chart-png").onclick = () => download("png");
    q("#chart-svg").onclick = () => download("svg");
    observer = new ResizeObserver(resize);
    observer.observe(q("#chart"));
    document.addEventListener("keydown", (event) => {
      if (!expanded || document.querySelector("dialog[open]")) return;
      if (event.key === "Escape") expand(false);
      if (event.key === "Tab") {
        const items = [...q("#chart-studio").querySelectorAll("button, input, select, a[href]")].filter((item) => !item.disabled && item.getClientRects().length);
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { last?.focus(); event.preventDefault(); }
        else if (!event.shiftKey && document.activeElement === last) { first?.focus(); event.preventDefault(); }
      }
    });
  }
  return { configure, clear, load, render: draw, resize, updateBusy, isSaving: () => saving };
})();
