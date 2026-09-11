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
  const palette = ["#226348", "#5279a8", "#c07a33", "#90649d", "#469397", "#b25c60", "#7d843c", "#607684"];
  const kindNames = { auto: "Otomatik", line: "Çizgi", bar: "Çubuk", area: "Alan", scatter: "Dağılım", heatmap: "Isı haritası" };
  const periodIndex = "__period_index__";
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
    q("#conversation-followups")?.remove();
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
    return payload.available_columns || payload.series.map((s) => ({ column: s.column, label: s.label, unit: s.unit }));
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
    setSettingAvailability(q("#chart-normalize"), !["scatter", "heatmap"].includes(kind), "Bu grafik türü yalnız özgün analiz değerleriyle gösterilir.");
    setSettingAvailability(q("#chart-x"), kind === "scatter", "Bu grafik türünde yatay eksen dönemlerden oluşur.");
    setSettingAvailability(q("#chart-orientation"), kind === "bar", "Yön seçimi yalnızca çubuk grafiğinde uygulanır.");
    q("#chart-layout").querySelector('option[value="dual_axis"]').disabled = kind === "scatter";
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
      button.onclick = () => {
        const overrides = { kind, ...(kind !== "bar" ? { orientation: "vertical" } : {}) };
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
    q("#chart-note").textContent = number(payload.row_count) + " satırın tamamı · Yakınlaştırın, bir noktayı seçip kaynağını inceleyin.";
    q("#chart-warnings").replaceChildren(...(payload.warnings || []).map((w) => node("p", w, "warning")));
    if (payload.spec.normalize === "index100")
      q("#chart-warnings").prepend(node("p", "Tüm seriler için ortak başlangıç: " + period(payload.spec.base_period || payload.periods[0]) + " = 100. Kaynak izi özgün analiz değerini gösterir.", "chart-info"));
    if (payload.spec.layout === "dual_axis")
      q("#chart-warnings").prepend(node("p", "İki eksenin ölçeği bağımsızdır; çizgilerin yüksekliği doğrudan büyüklük karşılaştırması değildir.", "chart-info"));
    q("#chart-sources").replaceChildren(node("strong", "KAYNAK"));
    for (const source of payload.sources || []) q("#chart-sources").append(node("span", source));
    if (!payload.sources?.length) q("#chart-sources").append(node("span", "Kaynak ayrıntıları için bir noktayı seçin."));
    q("#chart-point").hidden = true;
    renderRecommendations();
    updateBusy();
    draw();
  }
  function renderKpis() {
    const holder = q("#chart-kpis");
    holder.replaceChildren();
    const normalized = payload.spec.normalize === "index100";
    for (const [i, series] of payload.series.entries()) {
      const summary = series.summary || {}, card = node("article", undefined, "chart-kpi");
      card.style.setProperty("--series-color", palette[i % palette.length]);
      card.append(node("div", series.label, "chart-kpi-label"));
      const value = node("div", undefined, "chart-kpi-value");
      value.append(node("strong", number(summary.last)), node("span", series.raw_unit || series.unit || ""));
      card.append(value, node("div", summary.last_period ? period(summary.last_period) : "Son geçerli gözlem", "chart-kpi-period"));
      if (numeric(summary.change) !== null) {
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
      button.append(node("i"), node("span", series.label), node("small", payload.spec.normalize === "index100" ? period(payload.spec.base_period || payload.periods[0]) + " = 100" : series.unit + (payload.spec.layout === "dual_axis" ? series.axis === "right" ? " · ikinci eksen" : " · ilk eksen" : "")));
      button.onclick = () => {
        const name = series.column;
        selected[name] = selected[name] === false;
        button.setAttribute("aria-pressed", String(selected[name]));
        if (payload.spec.kind === "heatmap") draw();
        else chart?.dispatchAction({ type: "legendToggleSelect", name });
      };
      holder.append(button);
    }
    holder.hidden = !holder.children.length;
  }
  function prefill(prompt) {
    if (expanded) expand(false);
    hooks.prefill(prompt);
  }
  function renderRecommendations() {
    const recommendations = payload.recommendations || [];
    const fill = (holder, max) => {
      holder.replaceChildren();
      for (const item of recommendations.slice(0, max)) {
        if (!item.prompt) continue;
        const button = node("button", undefined, "chart-suggestion");
        button.type = "button";
        button.append(node("span", item.label || item.prompt), node("span", "↗", "chart-suggestion-arrow"));
        if (item.reason) button.append(node("small", item.reason));
        button.title = item.prompt;
        button.onclick = () => prefill(item.prompt);
        holder.append(button);
      }
    };
    fill(q("#chart-recommendations"), 6);
    q("#chart-recommendations").parentElement.hidden = !recommendations.length;
    q("#conversation-followups")?.remove();
    if (recommendations.length) {
      const section = node("section", undefined, "conversation-followups");
      section.id = "conversation-followups";
      section.append(node("div", "ANALİZİ BİR ADIM İLERİ TAŞIYIN", "eyebrow"));
      const holder = node("div", undefined, "chart-recommendations");
      fill(holder, 3);
      section.append(holder);
      q("#messages").append(section);
    }
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
    if (payload.spec.kind === "bar" && payload.spec.orientation === "horizontal") return Math.min(900, Math.max(450, payload.periods.length * 27 + 100));
    return panelGroups().length > 1 ? panelGroups().length * 280 + 55 : expanded ? Math.max(490, Math.min(680, innerHeight - 400)) : 430;
  }
  function options(width, height, exporting = false) {
    const spec = payload.spec, kind = spec.kind === "auto" ? "line" : spec.kind;
    const normalized = spec.normalize === "index100", unit = (s) => normalized ? period(spec.base_period || payload.periods[0]) + " = 100" : s.unit || "";
    const top = exporting ? 188 : 20, bottom = exporting ? 118 : 62;
    const titles = [], graphic = [], grids = [], xAxes = [], yAxes = [], series = [], zoom = [];
    if (exporting) {
      titles.push({ text: wrap(payload.title, 96), subtext: wrap(readableSubtitle(), 140), left: 36, top: 24,
        textStyle: { fontSize: 23, lineHeight: 29, fontWeight: 600, color: "#20372d" }, subtextStyle: { color: "#627568", fontSize: 12, lineHeight: 18 } });
      const sourceText = "Kaynak: " + (payload.sources || []).join(" · ");
      graphic.push({ type: "text", left: 36, bottom: 23, style: { text: wrap(sourceText, Math.floor(width / 6.4)), font: "11px sans-serif", lineHeight: 17, fill: "#627568" } });
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
        : visibleSeries.map((series) => series.label);
      grids.push({ left: Math.min(185, width * 0.30), right: 34, top: top + 22, bottom: bottom + 64 });
      xAxes.push(categoryAxis(payload.periods, 0));
      yAxes.push({ ...categoryAxis(cats, 0), axisLabel: { color: "#526b5a", fontSize: 11, width: Math.min(165, width * 0.27), overflow: "truncate" }, splitArea: { show: true }, inverse: true });
      const categoryIndexes = new Map(visibleSeries.map((series, index) => [series.column, index]));
      const cells = (payload.cells || payload.series.flatMap((series, categoryIndex) => series.values.map((value, periodIndex) => ({ period_index: periodIndex, category_index: categoryIndex, value, raw_value: series.raw_values?.[periodIndex], period: payload.periods[periodIndex], column: series.column }))))
        .filter((cell) => selected[cell.column] !== false)
        .map((cell) => ({ ...cell,
          category_index: payload.group_by ? cell.category_index : categoryIndexes.get(cell.column),
          category_label: payload.group_by ? (payload.categories?.[cell.category_index] || "") : visibleSeries[categoryIndexes.get(cell.column)]?.label }));
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
    const horizontal = kind === "bar" && spec.orientation === "horizontal";
    for (const [index, group] of groups.entries()) {
      const dual = spec.layout === "dual_axis" && group.length === 2;
      const distinctUnits = [...new Set(group.map(unit))];
      const panelTitle = wrap(group.map((s) => s.label).join(" · "), Math.max(32, Math.floor(width / 8)));
      const extraTop = groups.length > 1 ? 34 + 17 * panelTitle.split("\n").length : 24;
      const gridTop = top + index * groupHeight + extraTop;
      grids.push({ left: horizontal ? Math.min(175, width * 0.29) : 64, right: dual ? 72 : 28, top: gridTop, height: Math.max(110, groupHeight - extraTop - 36) });
      if (groups.length > 1) titles.push({ text: panelTitle, left: 22, top: top + index * groupHeight - 3,
        textStyle: { color: "#294f38", fontSize: 12, fontWeight: 600, lineHeight: 17 } });
      const xIndex = xAxes.length, yIndex = yAxes.length;
      if (kind === "scatter") {
        xAxes.push({ ...valueAxis((payload.x_label || payload.x_column || "X") + " · " + (payload.x_unit || ""), index), nameLocation: "middle", nameGap: 33, nameTextStyle: { fontSize: 11, color: "#627568", align: "center" } });
        yAxes.push(valueAxis(unit(group[0] || {}), index));
      } else if (horizontal) {
        xAxes.push(valueAxis(unit(group[0] || {}), index));
        yAxes.push({ ...categoryAxis(payload.categories?.length === payload.periods.length ? payload.categories : payload.periods, index), inverse: true,
          axisLabel: { color: "#627568", fontSize: 11, width: Math.min(150, width * 0.25), overflow: "truncate", formatter: (v) => period(v) } });
      } else {
        xAxes.push(categoryAxis(payload.categories?.length === payload.periods.length && payload.group_by ? payload.categories : payload.periods, index));
        yAxes.push(valueAxis(distinctUnits[0], index));
      }
      if (dual && horizontal) xAxes.push({ ...valueAxis(unit(group[1]), index), position: "top", splitLine: { show: false }, nameTextStyle: { color: "#627568", fontSize: 11, align: "left" } });
      else if (dual) yAxes.push({ ...valueAxis(unit(group[1]), index), position: "right", splitLine: { show: false }, nameTextStyle: { color: "#627568", fontSize: 11, align: "right" } });
      for (const s of group) {
        const sIndex = payload.series.indexOf(s);
        series.push({ name: s.column, type: kind === "area" ? "line" : kind, xAxisIndex: xIndex + (dual && horizontal && group.indexOf(s) === 1 ? 1 : 0),
          yAxisIndex: yIndex + (dual && !horizontal && group.indexOf(s) === 1 ? 1 : 0),
          data: s.values.map((v, i) => ({ value: kind === "scatter" ? [numeric(payload.x_values?.[i]), numeric(v)] : numeric(v), originalIndex: i, column: s.column })),
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
        legend: { show: exporting && kind !== "heatmap", data: payload.series.map((s) => s.column), selected, left: 36, right: 36, top: 126, itemWidth: 15, itemHeight: 8,
          textStyle: { color: "#52704f", fontSize: 10, lineHeight: 16 },
          formatter: (name) => { const item = payload.series.find((s) => s.column === name); return wrap((item?.label || name) + " · " + unit(item || {}), 72); } },
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
    const s = payload.series.find((item) => item.column === p.seriesName);
    if (!s) return "";
    const i = p.data?.originalIndex ?? p.dataIndex;
    const lines = [s.label, period(payload.periods[i])];
    if (payload.point_dimensions?.[i]) lines.push(Object.values(payload.point_dimensions[i]).join(" · "));
    if (payload.spec.kind === "scatter") lines.push((payload.x_label || payload.x_column) + ": " + number(payload.x_values?.[i]) + " " + payload.x_unit);
    lines.push(number(s.values[i]) + " " + (payload.spec.normalize === "index100" ? "(" + period(payload.spec.base_period || payload.periods[0]) + " = 100)" : s.unit));
    if (payload.spec.normalize === "index100") lines.push("Özgün analiz: " + number(s.raw_values?.[i]) + " " + (s.raw_unit || s.unit));
    lines.push("Kaynak için tıklayın");
    return lines.join("\n");
  }
  function showPoint(p) {
    const s = payload.series.find((item) => item.column === p.seriesName) || payload.series[0];
    if (!s || !p.data) return;
    const cell = p.data.cell, i = p.data.originalIndex ?? p.dataIndex;
    const value = cell ? cell.raw_value : s.raw_values?.[i] ?? s.values[i];
    const sourcePeriod = cell ? cell.period : payload.periods[i];
    const dimensions = cell?.dimensions || payload.point_dimensions?.[i] || {};
    const column = cell?.column || s.column;
    const holder = q("#chart-point");
    holder.replaceChildren();
    const info = node("div");
    info.append(node("strong", (cell ? cell.category_label || payload.categories?.[cell.category_index] : s.label) + " · " + period(sourcePeriod)));
    info.append(node("span", "Özgün analiz değeri: " + number(value) + " " + (s.raw_unit || s.unit)));
    if (payload.spec.normalize === "index100") info.append(node("small", "Grafikte: " + number(s.values[i]) + ". Kaynak izi, bu endeks dönüşümünden önceki değere aittir."));
    const button = node("button", "Kaynak izini incele ↗", "chart-action");
    button.type = "button";
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
    const height = Math.max(720, heightFor() + 230);
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
    for (const button of document.querySelectorAll("#chart-kinds button, #chart-apply")) button.disabled = blocked || button.dataset.unavailable === "true";
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
