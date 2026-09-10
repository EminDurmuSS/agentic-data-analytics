"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {
  workspace: null,
  conversation: null,
  analysis: null,
  busy: false,
  offset: 0,
  job: null,
  review: null,
};
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const isExactInteger = (value) =>
  value && typeof value === "object" && /^-?\d+$/.test(value.$integer);
const fmt = (value) =>
  value === null || value === undefined
    ? "-"
    : isExactInteger(value)
      ? new Intl.NumberFormat("tr-TR").format(BigInt(value.$integer))
      : typeof value === "number"
        ? new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 }).format(
            value,
          )
        : String(value);
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : body.errors?.map((x) => x.message).join("; ") ||
          "İstek tamamlanamadı.",
    );
  return body;
}
function notice(message) {
  $("#notice").textContent = message || "";
  $("#notice").hidden = !message;
}
function base() {
  if (!state.workspace) throw new Error("Önce bir çalışma alanı açın.");
  return "/api/workspaces/" + state.workspace.workspace_id;
}
function examples() {
  const holder = $("#examples");
  holder.replaceChildren();
  const prompts =
    state.workspace?.profile === "generic"
      ? [
          "Eklediğim verinin dönemini, birimini ve eksik gözlemlerini incele.",
          "Ziyaret sayısının aylık değişimini tablo olarak göster.",
        ]
      : [
          "2026 ilk çeyrekte bankacılık sektörünün aylık net kârını tablo olarak göster.",
          "İstanbul altın mevduatını 2025 ilk çeyrekten 2026 ikinci çeyreğe kadar göster.",
        ];
  for (const prompt of prompts) {
    const button = el("button", prompt, "example");
    button.type = "button";
    button.addEventListener("click", () => {
      $("#question").value = prompt;
      $("#question").focus();
    });
    holder.append(button);
  }
}
function appendMessage(role, text, pending = false) {
  $("#welcome")?.remove();
  const item = el("div", null, "message " + role + (pending ? " pending" : ""));
  item.append(
    el("div", role === "user" ? "Siz" : "Agentic Minds", "role"),
    el("div", text, "body"),
  );
  if (role === "assistant" && !pending)
    renderMessage(item.querySelector(".body"), text);
  $("#messages").append(item);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return item;
}

// Render a small Markdown subset through DOM nodes; model HTML stays plain text.
function inlineText(parent, text) {
  const pattern =
    /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
  let cursor = 0;
  for (const match of String(text).matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const node = el(
      match[2] ? "strong" : match[3] ? "code" : "a",
      match[2] || match[3] || match[4],
    );
    if (match[5]) {
      node.href = match[5];
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
    parent.append(node);
    cursor = match.index + match[0].length;
  }
  parent.append(document.createTextNode(String(text).slice(cursor)));
}
function renderMessage(parent, text) {
  parent.replaceChildren();
  const lines = String(text || "")
    .replaceAll("\u2014", "-")
    .split("\n");
  let list = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) {
      list = null;
      continue;
    }
    if (line.startsWith("```")) {
      const code = [];
      while (++i < lines.length && !lines[i].trim().startsWith("```"))
        code.push(lines[i]);
      parent.append(el("pre", code.join("\n")));
      list = null;
      continue;
    }
    if (
      line.startsWith("|") &&
      /^\s*\|?[\s:|\-]+\|\s*$/.test(lines[i + 1] || "")
    ) {
      const table = el("table"),
        header = el("tr");
      const cells = (row) =>
        row
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|");
      for (const cell of cells(line)) {
        const th = el("th");
        inlineText(th, cell.trim());
        header.append(th);
      }
      table.append(header);
      i += 1;
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith("|")) {
        const row = el("tr");
        for (const cell of cells(lines[++i])) {
          const td = el("td");
          inlineText(td, cell.trim());
          row.append(td);
        }
        table.append(row);
      }
      const wrap = el("div", null, "message-table");
      wrap.append(table);
      parent.append(wrap);
      list = null;
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+)/),
      bullet = line.match(/^(?:[-*]|\d+\.)\s+(.+)/);
    if (bullet) {
      if (!list) {
        list = el("ul");
        parent.append(list);
      }
      const li = el("li");
      inlineText(li, bullet[1]);
      list.append(li);
      continue;
    }
    list = null;
    const block = el(heading ? "h3" : "p");
    inlineText(block, heading ? heading[1] : line);
    parent.append(block);
  }
}
async function refreshWorkspaces() {
  const { workspaces } = await api("/api/workspaces");
  const list = $("#workspace-list");
  list.replaceChildren();
  for (const workspace of workspaces) {
    const button = el(
      "button",
      workspace.name,
      "workspace-item" +
        (workspace.workspace_id === state.workspace?.workspace_id
          ? " active"
          : ""),
    );
    button.append(
      el(
        "small",
        workspace.profile === "generic"
          ? "Kendi veriniz"
          : "KKB finans verileri",
      ),
    );
    button.addEventListener("click", () =>
      selectWorkspace(workspace.workspace_id).catch((e) => notice(e.message)),
    );
    list.append(button);
  }
  return workspaces;
}
function clearResult() {
  state.analysis = null;
  $("#result-content").hidden = true;
  $("#result-empty").hidden = false;
  $("#csv-download").hidden = true;
  $("#extra-result").hidden = true;
  $("#result-title").textContent = "Hesabın tamamı, tek yerde.";
}
async function selectWorkspace(id) {
  if (state.busy) {
    notice("Çalışma sürerken alan değiştirmek için sonucunu bekleyin.");
    return;
  }
  const workspace = await api("/api/workspaces/" + id);
  state.workspace = workspace;
  state.conversation = null;
  localStorage.setItem("agentic-workspace", id);
  $("#workspace-title").textContent = workspace.name;
  $("#profile-label").textContent =
    workspace.profile === "generic" ? "KENDİ VERİNİZ" : "KKB FİNANS VERİLERİ";
  $("#workspace-version").textContent = "Sürüm " + workspace.version;
  $("#messages").replaceChildren();
  $("#activity").hidden = true;
  clearResult();
  const runs = [...(workspace.runs || [])].reverse();
  for (const run of runs) {
    appendMessage("user", run.message);
    if (run.result) {
      appendMessage("assistant", run.result.message || run.result.status);
      state.conversation = run.conversation_id;
    }
  }
  if (!runs.length) {
    const welcome = el("div", null, "welcome");
    welcome.id = "welcome";
    welcome.append(
      el("span", "↗", "welcome-icon"),
      el("h2", "Veriyi birlikte inceleyelim."),
      el(
        "p",
        "Bir soru sorun. Hesabı, tablosu ve kaynaklarıyla birlikte takip edin.",
      ),
    );
    const cards = el("div", null, "examples");
    cards.id = "examples";
    welcome.append(cards);
    $("#messages").append(welcome);
    examples();
  }
  if (workspace.analysis_head) await loadAnalysis(workspace.analysis_head);
  const recent = runs.at(-1);
  if (recent) {
    showEvents(workspace.latest_events);
    if (recent.result) showExtraResults({ events: workspace.latest_events });
    if (!recent.result) showResume(workspace.pending_job_id);
  }
  await refreshWorkspaces();
}
async function createWorkspace(name, profile) {
  notice("Çalışma alanı hazırlanıyor…");
  const result = await api("/api/workspaces", {
    method: "POST",
    body: JSON.stringify({ name, profile }),
  });
  notice("");
  await selectWorkspace(result.workspace_id);
}
const toolLabels = {
  discover: "İlgili veriler aranıyor",
  describe: "Verinin anlamı ve kapsamı inceleniyor",
  dimension_values: "İl ve kurum değerleri bulunuyor",
  validate_plan: "Hesap planı denetleniyor",
  execute: "Veri sorgulanıyor ve hesaplanıyor",
  revise_analysis: "Önceki analiz güncelleniyor",
  explain_value: "Kaynak izi okunuyor",
  query_grouped: "Gruplar karşılaştırılıyor",
  inspect_source: "Yeni kaynak inceleniyor",
  publish_selected_table: "Doğrulanan tablo ekleniyor",
  web_search: "Web kaynakları araştırılıyor",
  rolling_anomalies: "Olağandışı dönemler aranıyor",
  detect_changes: "Değişim noktaları inceleniyor",
  analyze_relationship: "Değişkenler arasındaki ilişki hesaplanıyor",
};
function showEvents(events) {
  if (!events?.length) return;
  $("#activity").hidden = false;
  $("#activity-count").textContent = events.length + " adım";
  const holder = $("#events");
  holder.replaceChildren();
  for (const event of events) {
    const payload = event.payload || {};
    const name = payload.name || payload.tool || payload.tool_name;
    let label =
      toolLabels[name] ||
      {
        run_started: "Analiz başladı",
        model_response: "Sonraki adım belirlendi",
        tool_completed: "Araç sonucu kaydedildi",
        run_finished: "Analiz tamamlandı",
        tool_error: "Hesap kontrolü bir sorun bildirdi",
        recovered: "Kayıtlı sonuçtan devam edildi",
      }[event.kind];
    if (!label) continue;
    const item = el("li", label);
    if (payload.error?.message) item.append(el("small", payload.error.message));
    holder.append(item);
  }
}
async function submitQuestion(event) {
  event?.preventDefault();
  const message = $("#question").value.trim();
  if (!message || state.busy) return;
  notice("");
  if (!state.workspace) {
    await createWorkspace("İlk analiz", "finance");
  }
  state.busy = true;
  $("#send").disabled = true;
  $("#question").value = "";
  appendMessage("user", message);
  const pending = appendMessage(
    "assistant",
    "Veriyi bulup hesap planını hazırlıyorum…",
    true,
  );
  try {
    const body = await api(base() + "/runs", {
      method: "POST",
      body: JSON.stringify({
        message,
        conversation_id: state.conversation,
        request_id: "request_" + crypto.randomUUID().replaceAll("-", ""),
      }),
    });
    state.job = body.job_id;
    let job;
    for (let i = 0; i < 480; i++) {
      job = await api("/api/jobs/" + state.job);
      showEvents(job.events);
      if (["finished", "failed", "interrupted"].includes(job.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    if (job?.status === "interrupted") {
      showResume(state.job);
      throw new Error(
        "Çalışma yarıda kaldı. Aynı isteği yeniden başlatarak kayıtlı adımlardan devam edebilirsiniz.",
      );
    }
    if (!job?.result)
      throw new Error(
        "Çalışma sürüyor. Sayfayı yenilediğinizde kaydına ulaşabilirsiniz.",
      );
    const result = job.result;
    pending.classList.remove("pending");
    renderMessage(
      pending.querySelector(".body"),
      result.message ||
        result.errors?.map((x) => x.message).join("\n") ||
        "Çalışma " + result.status,
    );
    state.conversation =
      result.conversation_id || job.run?.conversation_id || state.conversation;
    if (result.analysis_id) await loadAnalysis(result.analysis_id, result);
    if (result.status === "blocked" || result.status === "failed")
      notice(result.message);
    showExtraResults(job);
    const workspace = await api(base());
    state.workspace = { ...state.workspace, ...workspace };
    $("#workspace-version").textContent = "Sürüm " + workspace.version;
  } catch (error) {
    pending.classList.remove("pending");
    pending.querySelector(".body").textContent = error.message;
    notice(error.message);
  } finally {
    state.busy = false;
    $("#send").disabled = false;
    $("#messages").scrollTop = $("#messages").scrollHeight;
    await refreshWorkspaces();
  }
}
function showResume(jobId) {
  if (!jobId) return;
  state.job = jobId;
  const button = el("button", "Kaydedilmiş çalışmaya devam et", "quiet");
  button.addEventListener("click", async () => {
    if (state.busy) return;
    button.disabled = true;
    try {
      await api("/api/jobs/" + jobId + "/resume", { method: "POST" });
      notice("Çalışma kayıtlı adımlardan devam ediyor.");
      await pollExisting(jobId);
    } catch (error) {
      notice(error.message);
    } finally {
      button.disabled = false;
    }
  });
  $("#messages").append(button);
}
async function pollExisting(jobId) {
  state.busy = true;
  $("#send").disabled = true;
  try {
    for (let i = 0; i < 480; i++) {
      const job = await api("/api/jobs/" + jobId);
      showEvents(job.events);
      if (job.result) {
        await selectAfterRun(job);
        return;
      }
      if (job.status === "interrupted") break;
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
    notice("Çalışma kaydı korunuyor. Devam etmek için yeniden deneyin.");
  } finally {
    state.busy = false;
    $("#send").disabled = false;
  }
}
async function selectAfterRun(job) {
  state.busy = false;
  await selectWorkspace(state.workspace.workspace_id);
  showEvents(job.events);
  showExtraResults(job);
}
function schemaLabel(column) {
  const s = state.analysis?.schema?.[column];
  if (!s) return "";
  const scale =
    s.scale === 1e6
      ? "milyon "
      : s.scale === 1e9
        ? "milyar "
        : s.scale === 1e3
          ? "bin "
          : "";
  const units = {
    TRY: "TL",
    percent: "%",
    index: "endeks",
    persons: "kişi",
    visits: "ziyaret",
    count: "adet",
    "TRY/person": "TL/kişi",
    percentage_point: "yüzde puan",
  };
  return (
    scale +
    (units[s.unit] || s.unit || "") +
    (s.price_basis ? " (" + s.price_basis + " fiyatları)" : "")
  );
}
const frequencyLabels = {
  monthly: "Aylık",
  quarterly: "Üç aylık",
  yearly: "Yıllık",
  annual: "Yıllık",
  half_yearly: "Altı aylık",
  twice_monthly: "Ayda iki kez",
  weekly: "Haftalık",
  weekly_friday: "Haftalık (cuma)",
  weekly_wednesday: "Haftalık (çarşamba)",
  daily: "Günlük",
  business_daily: "İş günü",
};
function periodLabel(value) {
  const text = String(value ?? "Belirtilmemiş");
  const quarter = text.match(/^(\d{4})-Q([1-4])$/);
  if (quarter) return quarter[1] + " · " + quarter[2] + ". çeyrek";
  const half = text.match(/^(\d{4})-H([12])$/);
  if (half) return half[1] + (half[2] === "1" ? " · İlk yarı" : " · İkinci yarı");
  if (/^\d{4}-\d{2}(?:-\d{2})?$/.test(text)) {
    const date = new Date(text.length === 7 ? text + "-01T00:00:00Z" : text + "T00:00:00Z");
    if (!Number.isNaN(date.getTime()))
      return new Intl.DateTimeFormat("tr-TR", {
        year: "numeric", month: "long", timeZone: "UTC",
        ...(text.length === 10 ? { day: "numeric" } : {}),
      }).format(date);
  }
  return text;
}
function dimensionLabel(value) {
  return { group_code: "Kurum grubu kodu", city: "İl", province: "İl", province_name: "İl",
    province_code: "İl kodu", city_code: "İl kodu", currency: "Para birimi" }[value] || value;
}
function analysisSelections(analysis) {
  const plan = analysis.plan || {};
  return plan.query_type === "grouped"
    ? [{ ...plan.request, name: "value" }]
    : plan.columns || [];
}
function selectionSource(selection) {
  const sources = state.analysis.sources || {};
  return sources[selection.name] || Object.values(sources).find(
    (source) => source.metric_id === selection.metric_id,
  ) || {};
}
function selectionTitle(selection) {
  return selectionSource(selection).title || selection.metric_id || selection.name;
}
function sourceUnitLabel(source) {
  if (!source.unit) return "";
  if (source.unit === "unknown") return "Birim tanımlanmamış";
  const units = { TRY: "TL", percent: "%", index: "endeks", persons: "kişi",
    visits: "ziyaret", count: "adet", "TRY/person": "TL/kişi",
    percentage_point: "yüzde puan", percentage_points: "yüzde puan" };
  const scale = source.scale === 1e6 ? "milyon " : source.scale === 1e9 ? "milyar "
    : source.scale === 1e3 ? "bin " : source.scale && source.scale !== 1 ? fmt(source.scale) + " × " : "";
  return scale + (units[source.unit] || source.unit);
}
function renderAnalysisMethod() {
  const a = state.analysis, plan = a.plan || {}, grouped = plan.query_type === "grouped";
  const request = grouped ? plan.request || {} : plan;
  const selections = analysisSelections(a), operations = request.operations || [];
  const holder = $("#analysis-method");
  holder.replaceChildren();
  $("#analysis-technical").open = false;
  $("#analysis-plan").textContent = JSON.stringify(plan, null, 2);
  const intro = el("div", null, "method-intro");
  intro.append(el("h3", "Bu tablo nasıl oluştu?"),
    el("p", "Kaydedilen analizde kullanılan veriler ve uygulanan işlemler."));
  const facts = el("dl", null, "method-facts");
  const fact = (label, value) => {
    const item = el("div");
    item.append(el("dt", label), el("dd", value));
    facts.append(item);
  };
  fact("Dönem", request.start === request.end ? periodLabel(request.start)
    : periodLabel(request.start) + " → " + periodLabel(request.end));
  fact("Tablo sıklığı", frequencyLabels[request.frequency] || request.frequency || "Belirtilmemiş");
  holder.append(intro, facts);
  const steps = el("ol", null, "method-steps");
  const step = (title, description) => {
    const item = el("li", null, "method-step");
    const number = el("span", String(steps.children.length + 1).padStart(2, "0"), "method-step-number");
    number.setAttribute("aria-hidden", "true");
    const body = el("div", null, "method-step-body");
    body.append(el("h4", title));
    if (description) body.append(el("p", description, "method-description"));
    item.append(number, body);
    steps.append(item);
    return body;
  };
  if (selections.length) {
    const data = step(selections.length === 1 ? "Veri seçildi" : selections.length + " veri sütunu seçildi");
    for (const selection of selections) {
      const source = selectionSource(selection);
      const card = el("div", null, "method-source");
      card.append(el("strong", selectionTitle(selection)));
      const meta = el("div", null, "method-source-meta");
      const system = { TCMB_EVDS: "TCMB · EVDS", BDDK: "BDDK", BDDK_MONTHLY: "BDDK · Aylık",
        BDDK_WEEKLY: "BDDK · Haftalık", BDDK_FINTURK: "BDDK · FinTürk" }[source.source_system] || source.source_system;
      if (system) meta.append(el("span", system));
      if (sourceUnitLabel(source)) meta.append(el("span", sourceUnitLabel(source)));
      if (!grouped) meta.append(el("span", "Tablo sütunu: " + selection.name));
      card.append(meta);
      for (const [key, value] of Object.entries(selection.dimensions || {})) {
        const label = key.endsWith("_code")
          ? String(isExactInteger(value) ? value.$integer : value)
          : fmt(value);
        card.append(el("div", dimensionLabel(key) + ": " + label, "method-filter"));
      }
      data.append(card);
    }
    const native = selections.every((selection) => !selection.alignment || selection.alignment === "native");
    const alignment = step(native ? "Kaynak dönemleri korundu" : "Dönemler eşlendi",
      native ? "Seçilen veriler kendi sıklığında gösterildi; dönemler arasında toplam veya ortalama alınmadı." : null);
    if (!native) {
      const labels = {
        native: "Kaynağın kendi dönemleri korundu.",
        last: "Her çıktı döneminde son gözlenen değer alındı.",
        mean: "Her çıktı döneminde gözlenen değerlerin ortalaması alındı.",
        sum: "Her çıktı dönemi için alt dönem değerleri toplandı; eksik alt dönem varsa toplam üretilmedi.",
      };
      for (const selection of selections)
        alignment.append(el("p", selection.name + ": " + (labels[selection.alignment || "native"] ||
          "Kayıtlı dönem eşleme yöntemi: " + selection.alignment), "method-description"));
    }
    if (selections.length > 1)
      alignment.append(el("p", "Sütunlar dönem üzerinden eşleştirildi. Kaynakların kapsamları aynı kabul edilmedi.", "method-caption"));
  } else step("Veri seçimi ayrıntısı bulunamadı", "Kaydedilen planı teknik ayrıntılardan inceleyebilirsiniz.");
  for (const op of operations) {
    const current = op.column, output = op.output, periods = op.periods ?? 1;
    const labels = { growth: "Yüzde değişim hesaplandı", difference: "Dönem farkı hesaplandı",
      deflate: "Sabit fiyatlara dönüştürüldü", scale: "Ölçek dönüştürüldü", ratio: "Oran hesaplandı" };
    let description = "", formula = "";
    if (op.op === "growth" || op.op === "difference") {
      description = current + " sütunu, " + periods + " dönem önceki değeriyle karşılaştırıldı.";
      formula = op.op === "growth"
        ? output + " = (" + current + " / " + current + "[" + periods + " dönem önce] - 1) × 100"
        : output + " = " + current + " - " + current + "[" + periods + " dönem önce]";
    } else if (op.op === "deflate") {
      description = current + " sütunu, " + op.index + " endeksiyle " + periodLabel(op.base_period) + " fiyatlarına getirildi.";
      formula = output + " = " + current + " × " + op.index + "[" + periodLabel(op.base_period) + "] / " + op.index;
    } else if (op.op === "scale") {
      description = "Hedef ölçek: " + fmt(op.target_scale) + ". Sayısal gösterim bu ölçeğe çevrildi.";
      formula = output + " = " + current + " × girdi ölçeği / " + fmt(op.target_scale);
    } else if (op.op === "ratio") {
      description = "Pay ve payda kendi ölçekleriyle ortak birime getirildi.";
      formula = output + " = (" + current + " × pay ölçeği) / (" + op.denominator + " × payda ölçeği) × " + (op.multiplier ?? 100);
    } else description = "Bu işlem için açıklama bulunmuyor. Kayıtlı parametreler teknik ayrıntılarda yer alıyor.";
    const body = step(labels[op.op] || "Kayıtlı işlem: " + op.op, description);
    if (formula) body.append(el("div", formula, "method-formula"));
    if (op.scope_policy === "explicit_comparison")
      body.append(el("p", "Farklı kapsamların karşılaştırma gerekçesi: " + op.scope_reason, "method-caption"));
    if (output) body.append(el("p", output === current ? output + " sütununun değeri bu işlemle güncellendi."
      : "Sonuç sütunu: " + output, "method-caption"));
  }
  if (grouped) {
    step("Gruplar sıralandı", dimensionLabel(request.group_by) + " bazında, her dönem için " +
      (request.order === "asc" ? "küçükten büyüğe" : "büyükten küçüğe") + " sıralama yapıldı. En fazla " +
      (request.limit ?? 10) + " satır gösterildi; eşit değerler aynı sırayı paylaştı.");
  } else if (!operations.length && selections.length) {
    step("Ek hesaplama uygulanmadı", "Bu planda büyüme, fark, oran veya fiyat dönüşümü yer almıyor.");
  }
  if (a.parent_analysis_id)
    holder.append(el("p", "Bu tablo önceki analizden türetildi. Aşağıdaki adımlar güncel planın tamamını gösterir.", "method-revision"));
  holder.append(steps);
}
async function loadAnalysis(id, result = {}) {
  state.offset = 0;
  state.analysis = await api(base() + "/analyses/" + id + "?limit=250");
  $("#result-content").hidden = false;
  $("#result-empty").hidden = true;
  $("#result-title").textContent = state.analysis.parent_analysis_id
    ? "Analiz güncellendi"
    : "Analiz tablonuz";
  $("#row-count").textContent =
    state.analysis.row_count +
    " satır · " +
    state.analysis.columns.length +
    " sütun";
  $("#analysis-version").textContent = state.analysis.parent_analysis_id
    ? "Önceki analizden türetildi"
    : "Kaynak veriden hesaplandı";
  $("#csv-download").hidden = false;
  $("#csv-download").href = base() + "/analyses/" + id + "/csv";
  renderAnalysisMethod();
  renderTable();
  renderSources();
  const select = $("#chart-column");
  select.replaceChildren();
  for (const c of state.analysis.columns) {
    if (c === "period") continue;
    if (state.analysis.rows.some((row) => typeof row[c] === "number")) {
      const option = el("option", c);
      option.value = c;
      select.append(option);
    }
  }
  renderChart();
  const toolResults = (result.tool_results || []).map((t) => t.result || {});
  const warnings = [
    ...(state.analysis.warnings || []),
    ...(result.warnings || []),
    ...toolResults.flatMap((t) => t.warnings || []),
    ...(result.errors || []),
  ];
  $("#warnings").replaceChildren(
    ...warnings
      .filter(
        (w, i, all) =>
          all.findIndex(
            (other) => JSON.stringify(other) === JSON.stringify(w),
          ) === i,
      )
      .slice(0, 10)
      .map((w) =>
        el("div", typeof w === "string" ? w : warningText(w), "warning"),
      ),
  );
  const preserved =
    result.preserved_columns ||
    state.analysis.preserved_columns ||
    toolResults.find((t) => t.preserved_columns)?.preserved_columns ||
    [];
  $("#preserved").hidden = !preserved.length;
  $("#preserved").textContent = preserved.length
    ? "Korunan sütunlar: " + preserved.join(", ")
    : "";
  $("#more-rows").hidden = state.analysis.row_count <= 250;
}
function warningText(w) {
  if (w.code === "semantics_unreviewed") {
    const selection = analysisSelections(state.analysis).find((item) => item.name === w.column);
    if (selection && (!selection.alignment || selection.alignment === "native"))
      return selectionTitle(selection) + ": kaynak değerleri kendi dönemlerinde gösteriliyor. " +
        "Bu verinin dönüşüm kuralları henüz incelenmediği için büyüme, oran ve fiyat dönüşümü gibi hesaplar kullanılamıyor.";
  }
  const map = {
    missing_result: "Bazı dönemlerde gözlem eksik.",
    heterogeneous_scopes_aligned:
      "Sütunlar dönem üzerinden eşlendi; kaynakların kapsadığı kurumlar farklı olabilir.",
    semantics_unreviewed: "Bu ölçünün dönüşüm kuralları inceleme gerektiriyor.",
    incomplete_period: "Eksik alt dönemler nedeniyle toplam hesaplanmadı.",
    partial_period_blocked: "Eksik alt dönemler nedeniyle toplam hesaplanmadı.",
    observed_sample_mean:
      "Ortalama gözlenen değerlerden hesaplandı; yayın takviminin tam olduğu doğrulanmadı.",
    group_populations_not_summed:
      "Gruplar kaynak kapsamlarıyla gösterilir; gruplar arasında toplam alınmaz.",
    group_membership_scope:
      "Kaynak kapsamındaki coğrafya üyeleri birlikte gösteriliyor.",
  };
  return (
    map[w.code] ||
    w.detail ||
    w.message ||
    w.code ||
    "Kaynak kısıtını inceleyin."
  );
}
function renderTable() {
  const a = state.analysis;
  const table = el("table");
  const head = el("tr");
  for (const col of a.columns) {
    const th = el("th", col === "period" ? "Dönem" : col);
    if (schemaLabel(col)) th.append(el("small", schemaLabel(col)));
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  for (const row of a.rows) {
    const tr = el("tr");
    for (const col of a.columns) {
      const value = row[col],
        td = el("td", fmt(value), value === null ? "missing-cell" : "");
      if (
        (typeof value === "number" || isExactInteger(value)) &&
        col !== "rank"
      ) {
        td.classList.add("value-cell");
        td.tabIndex = 0;
        td.title = "Kaynağı incele";
        const handler = () => showEvidence(col, row);
        td.addEventListener("click", handler);
        td.addEventListener("keydown", (event) => {
          if (event.key === "Enter") handler();
        });
      }
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  $("#table-container").replaceChildren(table);
}
function renderSources() {
  const container = $("#source-cards");
  container.replaceChildren();
  for (const [column, source] of Object.entries(state.analysis.sources || {})) {
    const card = el("div", null, "source-card");
    card.append(
      el("strong", column),
      el("div", source.title || "Kaynak ölçüm"),
      el("small", source.metric_id || ""),
    );
    card.append(
      el("span", source.source_system || "Eklenen veri", "source-tag"),
    );
    if (source.unit)
      card.append(el("span", schemaLabel(column) || source.unit, "source-tag"));
    container.append(card);
  }
  if (!container.children.length)
    container.append(
      el(
        "p",
        "Kaynak ayrıntısı için tablodaki bir hücreyi seçin.",
        "small-muted",
      ),
    );
}
function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}
function renderChart() {
  const a = state.analysis,
    c = $("#chart-column").value,
    container = $("#chart");
  container.replaceChildren();
  if (!a) return;
  if (a.rows.some((row) => Object.values(row).some(isExactInteger)))
    container.append(
      el(
        "p",
        "Büyük tam sayılar tabloda eksiksiz gösterilir; yuvarlama yapmamak için grafiğe alınmaz.",
        "small-muted",
      ),
    );
  if (!c) return;
  $("#chart-unit").textContent = schemaLabel(c);
  const values = a.rows.map((r, i) => ({
      i,
      value: r[c],
      label: r.period || String(i + 1),
    })),
    valid = values.filter(
      (x) => typeof x.value === "number" && Number.isFinite(x.value),
    );
  if (a.plan?.query_type === "grouped") {
    renderGroupedChart(a, c, container);
    return;
  }
  if (!valid.length) {
    container.append(el("p", "Grafik için sayısal gözlem yok."));
    return;
  }
  const svg = svgEl("svg", {
    viewBox: "0 0 720 360",
    role: "img",
    "aria-label": c + " grafiği",
  });
  const low = Math.min(...valid.map((x) => x.value)),
    high = Math.max(...valid.map((x) => x.value)),
    pad =
      high === low ? Math.max(Math.abs(high) * 0.05, 1) : (high - low) * 0.12,
    min = low - pad,
    max = high + pad;
  const x = (i) => 68 + (i / Math.max(values.length - 1, 1)) * 624,
    y = (v) => 292 - ((v - min) / (max - min)) * 248;
  for (let i = 0; i < 5; i++) {
    const value = min + ((max - min) * i) / 4;
    svg.append(
      svgEl("line", {
        x1: 68,
        x2: 692,
        y1: y(value),
        y2: y(value),
        stroke: "#e3e9de",
      }),
    );
    svg.append(
      svgEl(
        "text",
        {
          x: 59,
          y: y(value) + 4,
          "text-anchor": "end",
          fill: "#84907d",
          "font-size": 10,
        },
        new Intl.NumberFormat("tr-TR", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(value),
      ),
    );
  }
  let path = "";
  for (const v of values) {
    if (typeof v.value !== "number" || !Number.isFinite(v.value)) {
      path += "|";
      continue;
    }
    path +=
      (path === "" || path.endsWith("|") ? "M" : "L") +
      x(v.i) +
      "," +
      y(v.value) +
      " ";
  }
  svg.append(
    svgEl("path", {
      d: path.replaceAll("|", ""),
      fill: "none",
      stroke: "#28634a",
      "stroke-width": 2.5,
    }),
  );
  for (const v of valid) {
    const circle = svgEl("circle", {
      cx: x(v.i),
      cy: y(v.value),
      r: valid.length > 120 ? 1.5 : 3,
      fill: "#28634a",
    });
    circle.append(
      svgEl("title", {}, v.label + ": " + fmt(v.value) + " " + schemaLabel(c)),
    );
    svg.append(circle);
  }
  for (const i of [
    ...new Set([0, Math.floor((values.length - 1) / 2), values.length - 1]),
  ])
    svg.append(
      svgEl(
        "text",
        {
          x: x(i),
          y: 320,
          "text-anchor":
            i === 0 ? "start" : i === values.length - 1 ? "end" : "middle",
          fill: "#84907d",
          "font-size": 10,
        },
        values[i].label,
      ),
    );
  container.append(svg);
}
function renderGroupedChart(a, column, container) {
  const group = a.plan.request.group_by;
  const rows = a.rows.filter((r) => typeof r[column] === "number").slice(0, 30);
  if (!rows.length) return;
  const height = rows.length * 30 + 35,
    svg = svgEl("svg", {
      viewBox: "0 0 720 " + height,
      role: "img",
      "aria-label": column + " grup karşılaştırması",
    }),
    low = Math.min(0, ...rows.map((r) => r[column])),
    high = Math.max(0, ...rows.map((r) => r[column])),
    x = (v) => 160 + ((v - low) / (high - low || 1)) * 470;
  svg.append(
    svgEl("line", {
      x1: x(0),
      x2: x(0),
      y1: 5,
      y2: height - 20,
      stroke: "#b9c7c1",
    }),
  );
  rows.forEach((row, i) => {
    const y = 10 + i * 30;
    svg.append(
      svgEl(
        "text",
        {
          x: 150,
          y: y + 13,
          "text-anchor": "end",
          fill: "#607666",
          "font-size": 11,
        },
        row[group] + " · " + row.period,
      ),
    );
    const bar = svgEl("rect", {
      x: Math.min(x(0), x(row[column])),
      y,
      width: Math.max(1, Math.abs(x(row[column]) - x(0))),
      height: 19,
      rx: 3,
      fill: "#28634a",
    });
    bar.append(
      svgEl("title", {}, fmt(row[column]) + " " + schemaLabel(column)),
    );
    svg.append(bar);
  });
  container.append(svg);
  if (a.rows.length > 30)
    container.append(el("p", "İlk 30 grup gösteriliyor.", "small-muted"));
}
async function showEvidence(column, row) {
  const dialog = $("#evidence-dialog");
  $("#evidence-title").textContent = column + " · " + (row.period || "");
  $("#evidence-content").replaceChildren(
    el("p", "Kaynak izi yükleniyor…", "small-muted"),
  );
  dialog.showModal();
  try {
    const query = new URLSearchParams({ column, period: row.period });
    const grouped = state.analysis.plan?.query_type === "grouped";
    if (grouped) {
      const dimension = state.analysis.plan.request.group_by;
      query.set("dimensions", JSON.stringify({ [dimension]: row[dimension] }));
    }
    const data = await api(
      base() + "/analyses/" + state.analysis.analysis_id + "/explain?" + query,
    );
    const holder = $("#evidence-content");
    holder.replaceChildren();
    const top = el("div", null, "evidence-item");
    top.append(
      el("strong", fmt(data.value) + " " + schemaLabel(column)),
      el(
        "div",
        data.source_references_complete
          ? "Kaynak referansları mevcut."
          : "Kaynak referanslarında eksik var.",
      ),
      el(
        "small",
        data.source_files_verified
          ? "Kaynak dosya baytları doğrulandı."
          : "Bu görünüm kayıtlı kaynak referanslarını gösterir.",
      ),
    );
    holder.append(top);
    for (const issue of data.lineage_issues || [])
      holder.append(el("p", issue, "warning"));
    const proof = el("pre", JSON.stringify(data.lineage, null, 2));
    holder.append(proof);
  } catch (error) {
    $("#evidence-content").replaceChildren(el("p", error.message, "warning"));
  }
}
function showExtraResults(job) {
  const holder = $("#extra-result");
  holder.replaceChildren();
  const events = job.events || [];
  for (const event of events) {
    const payload = event.payload || {},
      result = payload.result || payload.output;
    const name = payload.name || payload.tool || payload.tool_name;
    if (
      !result ||
      ![
        "rolling_anomalies",
        "detect_changes",
        "analyze_relationship",
        "web_search",
        "inspect_source",
        "publish_selected_table",
      ].includes(name)
    )
      continue;
    if (result.status === "ok" && result.method) {
      const card = el("div", null, "source-card");
      const values = result.results || {};
      card.append(
        el(
          "strong",
          {
            rolling_anomalies: "Olağandışı gözlemler",
            detect_changes: "Kalıcı değişimler",
            analyze_relationship: "Değişkenler arasındaki ilişki",
          }[name],
        ),
      );
      if (name === "rolling_anomalies") {
        card.append(
          el(
            "p",
            fmt(values.anomaly_count) +
              " gözlem işaretlendi. Karşılaştırma yalnız önceki dönemleri kullanır.",
          ),
        );
        for (const row of (values.flagged_rows || values.rows || [])
          .filter((r) => r.anomaly)
          .slice(0, 20))
          card.append(el("div", row.period + ": " + fmt(row.value)));
      } else if (name === "detect_changes") {
        card.append(
          el(
            "p",
            (values.change_count ?? values.changes?.length ?? 0) +
              " değişim adayı. Öncesi ve sonrası dönemler birlikte incelenir.",
          ),
        );
        for (const row of values.changes || [])
          card.append(
            el(
              "div",
              row.period +
                ": " +
                fmt(row.median_before) +
                " → " +
                fmt(row.median_after),
            ),
          );
      } else {
        card.append(
          el(
            "p",
            values.correlation !== undefined
              ? "Korelasyon: " + fmt(values.correlation)
              : "Granger testi p değeri: " + fmt(values.p_value),
          ),
        );
        card.append(el("div", "Eşleşen gözlem: " + fmt(values.sample_size)));
        card.append(
          el("p", "Bu sonuç tek başına nedensellik göstermez.", "warning"),
        );
      }
      if (result.artifact_id) {
        const link = el("a", "Tam istatistik kaydı ↓", "text-button");
        link.href = base() + "/statistics/" + result.artifact_id;
        link.target = "_blank";
        link.rel = "noopener";
        card.append(link);
      }
      holder.append(card);
    }
    if (name === "web_search" && result.status === "ok") {
      const card = el("div", null, "source-card");
      card.append(el("strong", "Bulunan web kaynakları"));
      for (const item of result.results || []) {
        if (!/^https?:\/\//i.test(item.url)) continue;
        const link = el("a", item.title, "text-button");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const paragraph = el("p");
        paragraph.append(link);
        card.append(paragraph);
      }
      holder.append(card);
    }
    const details = el("details");
    details.append(
      el("summary", toolLabels[name] || name),
      el("pre", JSON.stringify(result, null, 2)),
    );
    holder.append(details);
  }
  holder.hidden = !holder.children.length;
}
async function sourceResult(result) {
  const holder = $("#source-feedback");
  holder.replaceChildren();
  if (result.status === "blocked") {
    holder.append(
      el(
        "p",
        result.errors?.map((x) => x.message).join("\n") ||
          result.message ||
          "Kaynak incelenemedi.",
        "warning",
      ),
    );
    return;
  }
  const id = result.source_id || result.source?.source_id;
  if (!id) {
    holder.append(el("pre", JSON.stringify(result, null, 2)));
    return;
  }
  const raw = el("a", "Özgün dosyayı indir ↓", "quiet");
  raw.href = base() + "/sources/" + id + "/raw";
  holder.append(raw);
  holder.append(
    el("p", "Kaynak kaydedildi. Analiz sorunuza bu kaynağı bağlayabilirsiniz."),
  );
  const button = el("button", "Bu kaynağı konuşmada kullan →");
  button.addEventListener("click", () => {
    $("#source-dialog").close();
    $("#question").value =
      "Eklediğim " +
      id +
      " kaynağını incele. İçindeki verinin dönemini, birimini ve uygun analizleri göster.";
    $("#question").focus();
  });
  holder.append(button);
  let inspected = result;
  if (!result.tables) {
    try {
      inspected = await api(base() + "/sources/" + id);
    } catch (error) {
      holder.append(el("p", error.message, "small-muted"));
    }
  }
  for (const table of inspected.tables || inspected.source?.tables || []) {
    const card = el("div", null, "source-card");
    card.append(
      el("strong", table.title || table.table_id || "Tablo"),
      el("small", (table.row_count || table.rows?.length || 0) + " satır"),
    );
    if (
      table.requires_review ||
      table.extraction_method?.includes("ocr") ||
      table.origin === "ocr" ||
      inspected.machine_extracted
    ) {
      const review = el("button", "Çıkarılan hücreleri kontrol et");
      review.addEventListener("click", () =>
        openReview(id, table).catch((e) => notice(e.message)),
      );
      card.append(review);
    }
    holder.append(card);
  }
}
async function openReview(sourceId, table) {
  table = await api(
    base() + "/sources/" + sourceId + "/tables/" + table.table_id + "/review",
  );
  state.review = { sourceId, table };
  const holder = $("#review-table");
  holder.replaceChildren();
  const grid = el("table"),
    head = el("tr");
  for (const col of table.columns || [])
    head.append(el("th", typeof col === "string" ? col : col.name));
  grid.append(head);
  const rows = table.rows || table.preview || [];
  const columns = (table.columns || Object.keys(rows[0] || {})).map((c) =>
    typeof c === "string" ? c : c.name,
  );
  state.review.columns = columns;
  for (const row of rows) {
    const tr = el("tr");
    for (const [i, col] of columns.entries()) {
      const td = el("td"),
        input = el("input");
      input.className = "review-input";
      input.value = Array.isArray(row) ? (row[i] ?? "") : (row[col] ?? "");
      td.append(input);
      tr.append(td);
    }
    grid.append(tr);
  }
  holder.append(grid);
  const units = $("#review-units");
  units.replaceChildren();
  for (const col of columns) {
    const label = el("label", col + " için birim (sayısal sütunsa)");
    const input = el("input");
    input.dataset.column = col;
    input.placeholder = "Örn. milyon TL, kişi, yüzde";
    label.append(input);
    units.append(label);
  }
  $("#review-dialog").showModal();
}
$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const review = state.review;
  const rows = [...$("#review-table").querySelectorAll("tr")]
    .slice(1)
    .map((tr) => [...tr.querySelectorAll("input")].map((input) => input.value));
  const unit_evidence = {};
  for (const input of $("#review-units").querySelectorAll("input"))
    if (input.value.trim())
      unit_evidence[input.dataset.column] = input.value.trim();
  try {
    const result = await api(
      base() + "/sources/" + review.sourceId + "/review",
      {
        method: "POST",
        body: JSON.stringify({
          table_id: review.table.table_id,
          reviewed_rows: rows,
          unit_evidence,
        }),
      },
    );
    if (result.status === "blocked")
      throw new Error(
        result.errors?.map((x) => x.message).join("; ") ||
          "Kontrol tamamlanamadı.",
      );
    $("#review-dialog").close();
    $("#source-feedback").append(
      el(
        "p",
        "Hücre kontrolü kaydedildi. Kaynağı konuşmada kullanabilirsiniz.",
      ),
    );
  } catch (error) {
    notice(error.message);
  }
});
$("#composer").addEventListener("submit", (event) =>
  submitQuestion(event).catch((error) => notice(error.message)),
);
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submitQuestion().catch((error) => notice(error.message));
  }
});
$("#new-workspace").addEventListener("click", () =>
  $("#new-dialog").showModal(),
);
$("#new-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#create-button").disabled = true;
  try {
    await createWorkspace($("#new-name").value, $("#new-profile").value);
    $("#new-dialog").close();
  } catch (error) {
    notice(error.message);
  } finally {
    $("#create-button").disabled = false;
  }
});
$("#new-conversation").addEventListener("click", () => {
  if (state.busy) return;
  state.conversation = null;
  $("#messages").replaceChildren();
  appendMessage(
    "assistant",
    "Yeni konuşma başladı. Çalışma alanınızdaki veri ve son analiz kullanılabilir.",
  );
});
$("#sources-button").addEventListener("click", async () => {
  if (!state.workspace) {
    notice("Önce bir çalışma alanı oluşturun.");
    return;
  }
  $("#source-dialog").showModal();
  try {
    const result = await api(base() + "/sources");
    const holder = $("#source-feedback");
    holder.replaceChildren();
    for (const source of result.sources) {
      const button = el("button", source.filename);
      button.addEventListener("click", () => sourceResult(source));
      holder.append(button);
    }
  } catch (error) {
    notice(error.message);
  }
});
$("#upload").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  $("#source-feedback").textContent = "Dosya inceleniyor…";
  const form = new FormData();
  form.append("file", file);
  try {
    await sourceResult(
      await api(base() + "/sources/upload", { method: "POST", body: form }),
    );
  } catch (error) {
    $("#source-feedback").textContent = error.message;
  }
});
$("#url-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#url-button").disabled = true;
  $("#source-feedback").textContent = "Kaynak indiriliyor ve inceleniyor…";
  try {
    await sourceResult(
      await api(base() + "/sources/url", {
        method: "POST",
        body: JSON.stringify({ url: $("#source-url").value }),
      }),
    );
  } catch (error) {
    $("#source-feedback").textContent = error.message;
  } finally {
    $("#url-button").disabled = false;
  }
});
for (const button of document.querySelectorAll("[data-close]"))
  button.addEventListener("click", () => $("#" + button.dataset.close).close());
for (const button of document.querySelectorAll(".tab"))
  button.addEventListener("click", () => {
    for (const b of document.querySelectorAll(".tab")) {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-selected", b === button ? "true" : "false");
    }
    for (const panel of document.querySelectorAll(".tab-panel"))
      panel.hidden = panel.id !== "tab-" + button.dataset.tab;
    if (button.dataset.tab === "chart") renderChart();
  });
$("#chart-column").addEventListener("change", renderChart);
$("#more-rows").addEventListener("click", async () => {
  try {
    state.offset =
      state.offset + 250 >= state.analysis.row_count ? 0 : state.offset + 250;
    const result = await api(
      base() +
        "/analyses/" +
        state.analysis.analysis_id +
        "?offset=" +
        state.offset +
        "&limit=250",
    );
    state.analysis.rows = result.rows;
    renderTable();
    $("#more-rows").textContent =
      state.offset + result.rows.length >= state.analysis.row_count
        ? "İlk satırlar"
        : "Diğer satırlar";
    renderChart();
  } catch (error) {
    notice(error.message);
  }
});
(async () => {
  try {
    const status = await api("/api/status");
    $("#provider-dot").className =
      "status-dot " + (status.provider_ready ? "ready" : "offline");
    $("#provider-label").textContent = status.provider_ready
      ? "Kloudeks bağlı"
      : "Kloudeks ayarı gerekli";
    if (!status.provider_ready)
      notice(
        "Canlı analiz için sunucuyu MIA_API_KEY ile veya --prompt-key seçeneğiyle başlatın.",
      );
    const workspaces = await refreshWorkspaces();
    const saved =
      new URLSearchParams(location.search).get("workspace") ||
      localStorage.getItem("agentic-workspace");
    if (workspaces.length)
      await selectWorkspace(
        workspaces.find((w) => w.workspace_id === saved)?.workspace_id ||
          workspaces[0].workspace_id,
      );
    else {
      examples();
      $("#new-dialog").showModal();
    }
  } catch (error) {
    notice(error.message);
  }
})();
