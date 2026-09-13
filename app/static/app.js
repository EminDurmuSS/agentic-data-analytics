"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {
  workspace: null,
  conversation: null,
  analysis: null,
  analysisWarnings: [],
  busy: false,
  offset: 0,
  job: null,
  review: null,
  workspaceRequest: 0,
  analysisRequest: 0,
  fullColumns: false,
  followupContext: null,
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
async function fetchJob(jobId) {
  // Absorb transient network blips (a dropped poll, a brief server restart) during a
  // long-running job so one failed request does not abort the run's UI and activity.
  let lastError;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      return await api("/api/jobs/" + jobId);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 800 * (attempt + 1)));
    }
  }
  throw lastError;
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
      prefillQuestion(prompt);
    });
    holder.append(button);
  }
}
let messageSequence = 0;
function revealMessage(item) {
  if (!item?.isConnected) return;
  const messages = $("#messages");
  const top = item.getBoundingClientRect().top - messages.getBoundingClientRect().top + messages.scrollTop;
  messages.scrollTop = Math.max(0, top - 8);
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
  if (role === "user" && (String(text).length > 340 || String(text).split("\n").length > 5)) {
    const body = item.querySelector(".body");
    body.id = "question-message-" + (++messageSequence);
    item.classList.add("is-collapsed");
    const toggle = el("button", "Sorunun tamamını göster", "message-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", body.id);
    toggle.addEventListener("click", () => {
      const collapsed = item.classList.toggle("is-collapsed");
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.textContent = collapsed ? "Sorunun tamamını göster" : "Soruyu daralt";
      if (collapsed) revealMessage(item);
    });
    item.append(toggle);
  }
  $("#message-list").append(item);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return item;
}

// Render a small Markdown subset through DOM nodes; model HTML stays plain text.
function inlineText(parent, text) {
  const pattern =
    /\\([\\`*{}\[\]()#+\-.!_|>])|\*\*((?:\\.|[^*])+)\*\*|`([^`]+)`|\[((?:\\.|[^\]\\])+)\]\((https?:\/\/[^\s)]+)\)/g;
  const unescape = (value) => value.replace(/\\([\\`*{}\[\]()#+\-.!_|>])/g, "$1");
  text = String(text);
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    if (match[1]) {
      parent.append(document.createTextNode(match[1]));
      cursor = match.index + match[0].length;
      continue;
    }
    const node = el(
      match[2] ? "strong" : match[3] ? "code" : "a",
      match[3] || unescape(match[2] || match[4]),
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
// Serialization is deferred until the user explicitly opens technical details.
function technicalDetails(label, value) {
  const details = el("details", null, "technical-details");
  details.append(el("summary", label));
  let built = false;
  details.addEventListener("toggle", () => {
    if (!details.open || built) return;
    built = true;
    details.append(el("pre", JSON.stringify(typeof value === "function" ? value() : value, null, 2)));
  });
  return details;
}
function columnLabel(column, fallback) {
  return state.analysis?.presentation?.labels?.[column] ||
    { period: "Dönem", line_item: "Kalem", rank: "Sıra", value: "Değer", amount: "Tutar" }[column] ||
    fallback || String(column || "").replaceAll("_", " ");
}
function visibleColumns() {
  const a = state.analysis;
  const preferred = [...new Set(a.presentation?.columns || [])].filter((column) => a.columns.includes(column));
  return !state.fullColumns && preferred.length ? preferred : a.columns;
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
    const group = el("div", null, "workspace-group");
    const button = el(
      "button",
      null,
      "workspace-item" +
        (workspace.workspace_id === state.workspace?.workspace_id
          ? " active"
          : ""),
    );
    const chevron = el("span", workspace.workspace_id === state.workspace?.workspace_id ? "▾" : "▸", "workspace-chevron");
    button.append(chevron);
    const label = el("span", workspace.name, "workspace-label");
    button.append(label);

    const deleteBtn = el("span", "✕", "workspace-delete");
    deleteBtn.title = "Çalışma alanını sil";
    deleteBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (state.busy) return;
      if (!confirm(`"${workspace.name}" çalışma alanını ve tüm sohbetlerini silmek istediğinize emin misiniz?`)) return;
      try {
        await api("/api/workspaces/" + workspace.workspace_id, { method: "DELETE" });
        if (state.workspace?.workspace_id === workspace.workspace_id) {
          state.workspace = null;
          state.conversation = null;
          clearFollowups();
          localStorage.removeItem("agentic-workspace");
          $("#message-list").replaceChildren();
          window.ActivityJourney.reset();
          clearResult();
          $("#workspace-title").textContent = "Çalışma alanınız";
          $("#profile-label").textContent = "VERİ ANALİZİ";
          $("#workspace-version").textContent = "Yeni çalışma";
        }
        const remaining = await refreshWorkspaces();
        if (!state.workspace && remaining.length) {
          await selectWorkspace(remaining[0].workspace_id);
        }
      } catch (err) {
        notice(err.message);
      }
    });
    button.append(deleteBtn);

    const meta = el(
      "small",
      workspace.profile === "generic" ? "Kendi veriniz" : "KKB finans verileri",
    );
    button.append(meta);
    button.addEventListener("click", () =>
      selectWorkspace(workspace.workspace_id).catch((e) => notice(e.message)),
    );
    group.append(button);
    list.append(group);
  }
  return workspaces;
}
function clearResult() {
  state.analysisRequest++;
  window.AnalysisCharts?.clear();
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
  const request = ++state.workspaceRequest;
  clearFollowups();
  state.analysisRequest++;
  window.AnalysisCharts?.clear();
  const workspace = await api("/api/workspaces/" + id);
  if (request !== state.workspaceRequest) return;
  state.workspace = workspace;
  state.conversation = null;
  localStorage.setItem("agentic-workspace", id);
  $("#workspace-title").textContent = workspace.name;
  $("#profile-label").textContent =
    workspace.profile === "generic" ? "KENDİ VERİNİZ" : "KKB FİNANS VERİLERİ";
  $("#workspace-version").textContent = "Sürüm " + workspace.version;
  $("#message-list").replaceChildren();
  window.ActivityJourney.reset();
  clearResult();
  const runs = [...(workspace.runs || [])].reverse();
  let latestAssistant = null;
  for (const run of runs) {
    appendMessage("user", run.message);
    if (run.result) {
      latestAssistant = appendMessage("assistant", run.result.display_message || run.result.message || run.result.status);
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
    $("#message-list").append(welcome);
    examples();
  }
  if (workspace.analysis_head) await loadAnalysis(workspace.analysis_head);
  if (request !== state.workspaceRequest) return;
  const recent = runs.at(-1);
  if (recent) {
    showEvents(workspace.latest_activity, workspace.latest_journey);
    if (recent.result) showExtraResults(recent.result);
    if (!recent.result) showResume(workspace.pending_job_id);
    showFollowups(recent, latestAssistant);
    revealMessage(recent.result ? latestAssistant : $("#message-list").lastElementChild);
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
function showEvents(activity, journey) {
  window.ActivityJourney.render(activity, journey);
}
function showPendingActivity(pending, activity, journey) {
  window.ActivityJourney.pending(pending, journey);
}
function clearFollowups() {
  state.followupContext = null;
  window.ContextualFollowups?.clear();
}
function showFollowups(run, response) {
  const result = run?.result || {};
  if (run?.status !== "completed" || result.status !== "completed"
      || !run.run_id || !run.conversation_id || run.conversation_id !== state.conversation
      || (run.workspace_id && run.workspace_id !== state.workspace?.workspace_id)
      || (result.errors || []).length
      || /Devam için soru:/.test(result.message || "")
      || (result.warnings || []).some((warning) => warning?.code === "CLARIFICATION_AFTER_RESULT")
      || (result.tool_results || []).some((tool) => (tool.tool || tool.name) === "ask_user")) {
    clearFollowups();
    return;
  }
  state.followupContext = {
    workspace_id: state.workspace.workspace_id,
    conversation_id: run.conversation_id,
    run_id: run.run_id,
    analysis_id: result.analysis_id || null,
  };
  void window.ContextualFollowups.load(state.followupContext, response);
}
function resizeComposer() {
  const question = $("#question");
  const ceiling = Math.max(72, Math.min(160, innerHeight * .22));
  question.style.height = "auto";
  question.style.height = Math.min(question.scrollHeight, ceiling) + "px";
  question.style.overflowY = question.scrollHeight > ceiling ? "auto" : "hidden";
}
function prefillQuestion(prompt) {
  if (state.busy) return;
  $("#question").value = prompt;
  resizeComposer();
  $("#question").focus();
  $("#composer").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
async function submitQuestion(event) {
  event?.preventDefault();
  const message = $("#question").value.trim();
  if (!message || state.busy) return;
  if (window.AnalysisCharts.isSaving()) { notice("Grafik görünümü kaydediliyor. Ardından sorunuzu gönderebilirsiniz."); return; }
  notice("");
  if (!state.workspace) {
    await createWorkspace("İlk analiz", "finance");
  }
  state.busy = true;
  clearFollowups();
  window.AnalysisCharts.updateBusy();
  $("#send").disabled = true;
  $("#question").value = "";
  resizeComposer();
  window.ActivityJourney.reset();
  appendMessage("user", message);
  const pending = appendMessage(
    "assistant",
    "Agent çalışması başlatılıyor…",
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
      job = await fetchJob(state.job);
      showEvents(job.activity, job.journey);
      showPendingActivity(pending, job.activity, job.journey);
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
      job.run?.result?.display_message || result.display_message || result.message ||
        result.errors?.map((x) => x.message).join("\n") ||
        "Çalışma " + result.status,
    );
    state.conversation =
      result.conversation_id || job.run?.conversation_id || state.conversation;
    if (result.analysis_id) await loadAnalysis(result.analysis_id, result);
    else if (state.analysis) await window.AnalysisCharts.load(base(), state.analysis.analysis_id, Boolean(result.chart_updated || result.chart_id));
    if (result.status === "blocked" || result.status === "failed")
      notice(result.message);
    showExtraResults(result);
    const workspace = await api(base());
    state.workspace = { ...state.workspace, ...workspace };
    $("#workspace-version").textContent = "Sürüm " + workspace.version;
    showFollowups(job.run || { workspace_id: state.workspace.workspace_id,
      run_id: result.run_id, conversation_id: state.conversation, status: result.status, result }, pending);
  } catch (error) {
    pending.classList.remove("pending");
    pending.querySelector(".body").textContent = error.message;
    notice(error.message);
  } finally {
    state.busy = false;
    window.AnalysisCharts.updateBusy();
    window.ContextualFollowups.updateBusy();
    $("#send").disabled = false;
    revealMessage(pending);
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
  $("#message-list").append(button);
}
async function pollExisting(jobId) {
  state.busy = true;
  clearFollowups();
  window.AnalysisCharts.updateBusy();
  $("#send").disabled = true;
  try {
    for (let i = 0; i < 480; i++) {
      const job = await fetchJob(jobId);
      showEvents(job.activity, job.journey);
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
    window.AnalysisCharts.updateBusy();
    window.ContextualFollowups.updateBusy();
    $("#send").disabled = false;
  }
}
async function selectAfterRun(job) {
  state.busy = false;
  window.AnalysisCharts.updateBusy();
  await selectWorkspace(state.workspace.workspace_id);
  showEvents(job.activity, job.journey);
  showExtraResults(job.result || {});
  if (job.result?.chart_updated || job.result?.chart_id) showTab("chart");
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
  return columnLabel(selection.name, selectionSource(selection).title);
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
  $("#analysis-plan").textContent = "";
  $("#analysis-technical").ontoggle = () => {
    if ($("#analysis-technical").open && !$("#analysis-plan").textContent)
      $("#analysis-plan").textContent = JSON.stringify(plan, null, 2);
  };
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
        BDDK_WEEKLY: "BDDK · Haftalık", BDDK_FINTURK: "BDDK · FinTürk", SESSION_DATASET: "Eklenen kaynak" }[source.source_system] || source.source_system;
      if (system) meta.append(el("span", system));
      if (sourceUnitLabel(source)) meta.append(el("span", sourceUnitLabel(source)));
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
        period_end: "Kaynakta belirtilen dönem sonu, çıktı döneminin son günüyle eşleştirildi; diğer tarihlerden değer taşınmadı.",
      };
      for (const selection of selections)
        alignment.append(el("p", columnLabel(selection.name) + ": " + (labels[selection.alignment || "native"] ||
          "Dönem eşleme ayrıntıları kayıtlı hesap planında yer alıyor."), "method-description"));
    }
    if (selections.length > 1)
      alignment.append(el("p", "Sütunlar dönem üzerinden eşleştirildi. Kaynakların kapsamları aynı kabul edilmedi.", "method-caption"));
  } else step("Veri seçimi ayrıntısı bulunamadı", "Kaydedilen planı teknik ayrıntılardan inceleyebilirsiniz.");
  for (const op of operations) {
    const current = columnLabel(op.column), output = columnLabel(op.output), periods = op.periods ?? 1;
    const labels = { growth: "Yüzde değişim hesaplandı", difference: "Dönem farkı hesaplandı",
      deflate: "Sabit fiyatlara dönüştürüldü", scale: "Ölçek dönüştürüldü", ratio: "Oran hesaplandı" };
    let description = "", formula = "";
    if (op.op === "growth" || op.op === "difference") {
      description = current + " sütunu, " + periods + " dönem önceki değeriyle karşılaştırıldı.";
      formula = op.op === "growth"
        ? output + " = (" + current + " / " + current + "[" + periods + " dönem önce] - 1) × 100"
        : output + " = " + current + " - " + current + "[" + periods + " dönem önce]";
    } else if (op.op === "deflate") {
      description = current + " sütunu, " + columnLabel(op.index) + " endeksiyle " + periodLabel(op.base_period) + " fiyatlarına getirildi.";
      formula = output + " = " + current + " × " + columnLabel(op.index) + "[" + periodLabel(op.base_period) + "] / " + columnLabel(op.index);
    } else if (op.op === "scale") {
      description = "Hedef ölçek: " + fmt(op.target_scale) + ". Sayısal gösterim bu ölçeğe çevrildi.";
      formula = output + " = " + current + " × girdi ölçeği / " + fmt(op.target_scale);
    } else if (op.op === "ratio") {
      description = "Pay ve payda kendi ölçekleriyle ortak birime getirildi.";
      formula = output + " = (" + current + " × pay ölçeği) / (" + columnLabel(op.denominator) + " × payda ölçeği) × " + (op.multiplier ?? 100);
    } else description = "Bu işlem için açıklama bulunmuyor. Kayıtlı parametreler teknik ayrıntılarda yer alıyor.";
    const body = step(labels[op.op] || "Kayıtlı işlem: " + op.op, description);
    if (formula) body.append(el("div", formula, "method-formula"));
    if (op.scope_policy === "explicit_comparison")
      body.append(el("p", scopeWarning, "method-caption"));
    if (op.output) body.append(el("p", op.output === op.column ? output + " sütununun değeri bu işlemle güncellendi."
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
  const request = ++state.analysisRequest, workspacePath = base();
  state.offset = 0;
  const analysis = await api(workspacePath + "/analyses/" + id + "?limit=250");
  if (request !== state.analysisRequest || workspacePath !== base()) return;
  state.analysis = analysis;
  state.fullColumns = false;
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
  const toolResults = (result.tool_results || []).map((t) => t.result || {});
  const chartTools = (result.tool_results || []).filter((tool) =>
    tool.name === "create_chart" || tool.tool === "create_chart" || tool.result?.chart_id);
  const chartWarnings = new Set(chartTools.flatMap((tool) => tool.result?.warnings || []).map((warning) => JSON.stringify(warning)));
  state.analysisWarnings = [
    ...(state.analysis.warnings || []),
    ...(result.warnings || []).filter((warning) => !chartWarnings.has(JSON.stringify(warning))),
    ...(result.tool_results || []).filter((tool) => !chartTools.includes(tool)).flatMap((tool) => tool.result?.warnings || []),
    ...(result.errors || []),
  ];
  renderWarnings($("#warnings"), state.analysisWarnings);
  const preserved =
    result.preserved_columns ||
    state.analysis.preserved_columns ||
    toolResults.find((t) => t.preserved_columns)?.preserved_columns ||
    [];
  $("#preserved").hidden = !preserved.length;
  $("#preserved").textContent = preserved.length
    ? "Korunan sütunlar: " + preserved.map((column) => columnLabel(column)).join(", ")
    : "";
  $("#more-rows").hidden = state.analysis.row_count <= 250;
  await window.AnalysisCharts.load(workspacePath, id, Boolean(result.chart_updated || result.chart_id));
}
const scopeWarning = "Bu oran farklı kurum veya raporlama kapsamlarını karşılaştırır. Kaynakların aynı nüfusu veya geçerli bir pay-payda ilişkisini temsil ettiği doğrulanmamıştır; resmi sektör/pazar payı değildir.";
function warningText(w) {
  if (typeof w === "string") {
    if (/cross_scope_comparison|different (?:reporting )?(?:populations|scopes)|Farklı kapsamlar açık karşılaştırma/i.test(w)) return scopeWarning;
    if (/aligned.*(?:period|scope)|scopes.*aligned/i.test(w)) return warningText({code: "heterogeneous_scopes_aligned"});
    if (/exact.*period.end|period.end.*exact/i.test(w)) return warningText({code: "exact_event_period_end"});
    // Source warnings already written in Turkish remain readable; opaque codes
    // and untranslated diagnostics are retained in the technical disclosure.
    return /[çğıöşüÇĞİÖŞÜ]|\b(?:Kaynak|Eksik|Seriler|Oran|Gruplar|Dönem|Veri)\b/.test(w) ? w : null;
  }
  if (!w || typeof w !== "object") return null;
  if (w.code === "semantics_unreviewed") {
    const selection = analysisSelections(state.analysis || {}).find((item) => item.name === w.column);
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
    cross_scope_comparison: scopeWarning,
    exact_event_period_end: "Kaynak tarihi dönem sonuyla birebir eşleştirildi; başka tarihten değer taşınmadı.",
    event_period_end_exact: "Kaynak tarihi dönem sonuyla birebir eşleştirildi; başka tarihten değer taşınmadı.",
    native_calendar_unverified: "Kaynak gözlem tarihleri korunuyor; yayın takviminin eksiksiz olduğu doğrulanmadı.",
    zero_denominator: "Paydası sıfır olan oran veya değişim hesaplanmadı.",
    invalid_deflator: "Geçersiz fiyat endeksi bulunan dönemlerde reel değer hesaplanmadı.",
    group_missing_observations: "Bazı gruplarda gözlem eksik; boş hücreler sıfır kabul edilmez.",
    RANK_PRESERVED_FROM_PARENT: "Sıralar önceki analizin özgün ölçüsüne aittir; yeni değerler yeniden sıralanmadı.",
    reporting_population_exclusions: "Kaynağın kapsadığı banka grubu sınırlıdır; tüm bankaların bilanço toplamıyla eşdeğer değildir.",
    domestic_customers_only: "Bu kaynak yalnızca yurt içi yerleşik müşterileri kapsıyor.",
    regulatory_weighting: "Düzenleyici ağırlık içeren değerler, ağırlıksız bilanço tutarlarıyla eşdeğer değildir.",
    source_value_passthrough: "Değerler kaynakta bildirilen dönem ve birimleriyle gösteriliyor; dönemler arasında toplam alınmadı.",
    SEARCH_NO_PROGRESS: "Aramalar aynı bağlantılara döndüğü için durduruldu. Devam etmek için resmi rapor bağlantısını paylaşabilir veya dosyayı yükleyebilirsiniz.",
    SEARCH_STRATEGY_EXHAUSTED: "Aramalar aynı bağlantılara döndüğü için durduruldu. Devam etmek için resmi rapor bağlantısını paylaşabilir veya dosyayı yükleyebilirsiniz.",
    SEARCH_NO_RELEVANT_RESULTS: "Aramada konuya uygun bir kaynak bulunamadı. İlgisiz bağlantılar analize alınmadı.",
    SEARCH_NO_RESULTS: "Aramada kullanılabilir bir bağlantı bulunamadı. Resmi kaynak bağlantısını paylaşabilir veya dosyayı yükleyebilirsiniz.",
    SEARCH_DISCOVERY_ONLY: "Kurumun sayfaları bulundu; istenen rapor henüz bulunamadı.",
    NO_READABLE_SOURCES: "Bulunan bağlantılardan gerekli içerik okunamadı. Resmi rapor bağlantısıyla veya yükleyeceğiniz dosyayla devam edebilirsiniz.",
    OFFICIAL_SOURCE_NOT_FOUND: "İstenen kurumun resmi sitesinde uygun bir kaynak bulunamadı.",
    EXTERNAL_FACTS_UNVERIFIED: "Ortaklık bilgileri kaynak üzerinden doğrulanamadı; doğrulanmamış bir kurum listesi kullanılmadı.",
    WEB_SEARCH_UNCONFIGURED: "Web araması şu anda kullanılamıyor. Kaynağı bağlantı veya dosya olarak ekleyebilirsiniz.",
    SEARCH_UNAVAILABLE: "Web araması şu anda yanıt vermiyor. Kaynağı bağlantı veya dosya olarak ekleyebilirsiniz.",
    SEARCH_INVALID_RESPONSE: "Arama hizmetinden kullanılabilir bir sonuç alınamadı. Kaynağı bağlantı veya dosya olarak ekleyebilirsiniz.",
  };
  return map[w.code] || warningText(w.detail || w.message || "");
}
function renderWarnings(holder, warnings) {
  holder.replaceChildren();
  const messages = new Map(), technical = [];
  for (const warning of warnings || []) {
    // Successful date matching belongs in the calculation method, not in an
    // amber warning. The original diagnostic remains in the saved tool ledger.
    if (["exact_event_period_end", "event_period_end_exact", "calendar_period_end_alignment"].includes(warning?.code)) continue;
    const message = warningText(warning);
    if (message) messages.set(message, messages.get(message) === "warning" || warning?.level !== "info" ? "warning" : "chart-info");
    else technical.push(warning);
  }
  if (messages.has(scopeWarning)) messages.delete(warningText({code: "heterogeneous_scopes_aligned"}));
  // Scope disclosures cannot be lost behind a limit on minor method notes.
  for (const [message, className] of [...messages].sort(([a], [b]) => Number(b === scopeWarning) - Number(a === scopeWarning)))
    holder.append(el("div", message, className));
  if (technical.length) holder.append(technicalDetails("Ek yöntem notları (" + technical.length + ")", technical));
}
function renderChartWarnings(holder, warnings) {
  // A single disclosure area stays visible on every result tab. Chart changes
  // replace only chart notes, while analysis warnings remain attached to data.
  holder.replaceChildren();
  renderWarnings($("#warnings"), [...state.analysisWarnings, ...warnings]);
}
function renderTable() {
  const a = state.analysis;
  const columns = visibleColumns();
  const toggle = $("#toggle-columns");
  toggle.hidden = !a.presentation?.columns?.length || a.presentation.columns.length === a.columns.length;
  toggle.textContent = state.fullColumns ? "Özet sütunlara dön" : "Özgün değerler dahil tüm sütunlar";
  toggle.setAttribute("aria-pressed", String(state.fullColumns));
  $("#row-count").textContent = a.row_count + " satır · " + columns.length + " sütun";
  const table = el("table");
  const head = el("tr");
  for (const col of columns) {
    const th = el("th", columnLabel(col));
    if (schemaLabel(col)) th.append(el("small", schemaLabel(col)));
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  for (const row of a.rows) {
    const tr = el("tr");
    for (const col of columns) {
      const value = row[col],
        td = el("td", col === "period" ? periodLabel(value) : fmt(value), value === null ? "missing-cell" : "");
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
    card.append(el("strong", columnLabel(column)));
    if (source.title && source.source_system !== "SESSION_DATASET" && source.title !== columnLabel(column))
      card.append(el("div", source.title));
    card.append(
      el("span", source.source_system === "SESSION_DATASET" ? "Eklenen kaynak" : source.source_system?.replaceAll("_", " · ") || "Eklenen veri", "source-tag"),
    );
    if (source.unit)
      card.append(el("span", schemaLabel(column) || source.unit, "source-tag"));
    card.append(technicalDetails("Kaynak kimliği ve kapsamı", source));
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
async function showEvidence(column, row) {
  const dialog = $("#evidence-dialog");
  $("#evidence-title").textContent = columnLabel(column) + " · " + periodLabel(row.period);
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
    if (data.lineage_issues?.length)
      holder.append(el("p", "Kaynak izinde kontrol gerektiren kayıtlar var. Ayrıntıları aşağıdan inceleyebilirsiniz.", "warning"));
    holder.append(technicalDetails("Teknik kaynak izi ve doğrulama kaydı", data));
  } catch (error) {
    $("#evidence-content").replaceChildren(el("p", error.message, "warning"));
  }
}
function showExtraResults(result) {
  const holder = $("#extra-result");
  holder.replaceChildren();
  const records = result.tool_results || [], sources = new Map();
  for (const step of records) {
    if (!["inspect_source", "ingest_source_table", "publish_selected_table"].includes(step.tool)) continue;
    const value = step.result || {}, source = value.source || value;
    const id = value.source_id || source.source_id;
    if (!id) continue;
    const current = sources.get(id) || { id, publications: new Map() };
    if (step.tool === "inspect_source") current.inspection = source;
    else if (value.status === "ok" && value.dataset_id) current.publications.set(value.dataset_id, value);
    else current.pending = true;
    sources.set(id, current);
  }
  for (const source of sources.values()) {
    const inspection = source.inspection || {}, card = el("article", null, "source-card source-summary");
    card.dataset.sourceId = source.id;
    card.append(el("strong", inspection.article?.title || inspection.filename?.replaceAll("_", " ") || "Eklenen kaynak"));
    const publications = [...source.publications.values()];
    if (publications.length) {
      const count = publications.reduce((sum, item) => sum + (Number(item.row_count) || 0), 0);
      card.append(el("p", fmt(count) + " kaynak gözlemi özgün dönem ve birimleriyle analize hazırlandı."));
      const periods = [...new Set(publications.flatMap((item) => (item.available_series || []).flatMap((series) => series.observed_periods || [])))];
      if (periods.length) card.append(el("small", "Kaynak dönemi: " + periods.slice(0, 4).map(periodLabel).join(" · ") + (periods.length > 4 ? " · …" : "")));
      if (publications.some((item) => item.available_series?.some((series) => series.status === "review_required")))
        card.append(el("p", "Kaynak değerleri okunabilir; dönemler arası hesaplama için anlam ve dönem kapsamı incelemesi gerekiyor.", "warning"));
    } else card.append(el("p", inspection.status === "ok" ? "Kaynak incelendi; tablo seçimi ve analize hazırlama kaydı henüz yok." : "Kaynak incelemesi tamamlanmadı."));
    const raw = el("a", "Özgün kaynağı indir", "text-button");
    raw.href = base() + "/sources/" + encodeURIComponent(source.id) + "/raw";
    card.append(raw);
    holder.append(card);
  }
  const searches = records.filter((step) => step.tool === "web_search");
  if (searches.length) {
    const searchLinks = new Map(), rejectedLinks = new Set();
    const normalizeLink = (value) => {
      try {
        const url = new URL(value);
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
        url.hash = "";
        return url.href.replace(/\/$/, "");
      } catch { return null; }
    };
    for (const step of searches)
      for (const item of step.result?.rejected_results || []) {
        const url = normalizeLink(typeof item === "string" ? item : item.url);
        if (url) rejectedLinks.add(url);
      }
    for (const step of searches) {
      if (step.result?.status !== "ok") continue;
      for (const item of step.result.results || []) {
        const url = normalizeLink(item.url);
        if (!url || rejectedLinks.has(url) || item.rejected === true) continue;
        if (!searchLinks.has(url)) searchLinks.set(url, { title: item.title || new URL(url).hostname, href: item.url });
      }
    }
    if (searchLinks.size) {
      const navigation = el("details", null, "source-card search-navigation");
      navigation.append(el("summary", "Arama bağlantıları (" + searchLinks.size + ")"));
      navigation.append(el("p", "Bunlar aramada bulunan bağlantılardır. İçerikleri okunmadan kaynak kanıtı sayılmazlar."));
      for (const item of searchLinks.values()) {
        const link = el("a", item.title, "text-button");
        link.href = item.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const paragraph = el("p");
        paragraph.append(link);
        navigation.append(paragraph);
      }
      holder.append(navigation);
    }
  }
  if (["blocked", "failed", "partial"].includes(result.status)) {
    const lastSearch = records.findLast((step) => ["web_search", "research_web"].includes(step.tool))?.result || {};
    const failures = result.errors?.length ? result.errors : lastSearch.errors?.length ? lastSearch.errors : [lastSearch];
    const note = failures.find((item) => /^(?:SEARCH_|WEB_SEARCH_|NO_READABLE_SOURCES|OFFICIAL_SOURCE_NOT_FOUND|EXTERNAL_FACTS_UNVERIFIED)/.test(item.code || ""));
    if (note && warningText(note)) holder.append(el("p", warningText(note), "warning search-recovery"));
  }
  for (const step of records) {
    const name = step.tool;
    const toolResult = step.result;
    if (
      !toolResult ||
      ![
        "rolling_anomalies",
        "detect_changes",
        "analyze_relationship",
        "web_search",
        "research_web",
      ].includes(name)
    )
      continue;
    if (toolResult.status === "ok" && toolResult.method) {
      const card = el("div", null, "source-card");
      const values = toolResult.results || {};
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
      if (toolResult.artifact_id) {
        const link = el("a", "Tam istatistik kaydı ↓", "text-button");
        link.href = base() + "/statistics/" + toolResult.artifact_id;
        link.target = "_blank";
        link.rel = "noopener";
        card.append(link);
      }
      holder.append(card);
    }
    if (name === "summarize_analysis" && toolResult.status === "ok") {
      const card = el("div", null, "source-card");
      card.append(el("strong", "Doğrulanmış hesap özeti"));
      const summary = el("p", toolResult.summary_text || "Kayıtlı tablodan hesaplanan sonuçlar hazır.");
      summary.style.whiteSpace = "pre-line";
      card.append(summary);
      if (toolResult.summary_id) {
        const link = el("a", "Hesaplar ve kaynak kaydı ↓", "text-button");
        link.href = base() + "/summaries/" + toolResult.summary_id;
        link.target = "_blank";
        link.rel = "noopener";
        card.append(link);
      }
      holder.append(card);
    }
    if (name === "research_web" && toolResult.status === "ok") {
      const card = el("div", null, "source-card");
      card.append(el("strong", "Okunan web kaynakları"));
      for (const source of toolResult.sources || []) {
        if (!/^https?:\/\//i.test(source.url)) continue;
        const link = el("a", source.title || source.domain || source.url, "text-button");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        card.append(link);
        if (source.date_published) card.append(el("small", "Yayın: " + source.date_published));
      }
      holder.append(card);
    }
  }
  if (records.length || result.message) {
    const ledger = el("details", null, "technical-details tool-ledger");
    ledger.append(el("summary", "Teknik işlem kayıtları (" + records.length + ")"));
    let built = false;
    ledger.addEventListener("toggle", () => {
      if (!ledger.open || built) return;
      built = true;
      if (result.message) ledger.append(technicalDetails("Özgün yanıt ve tamamlanma durumu", {message: result.message, status: result.status, errors: result.errors}));
      for (const [index, step] of records.entries())
        ledger.append(technicalDetails((index + 1) + ". " + step.tool, step));
    });
    holder.append(ledger);
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
    holder.append(el("p", "Kaynak kaydı oluşturulamadı. İşlem ayrıntılarını inceleyebilirsiniz."), technicalDetails("Teknik işlem ayrıntıları", result));
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
    prefillQuestion(
      "Eklediğim " +
      id +
      " kaynağını incele. İçindeki verinin dönemini, birimini ve uygun analizleri göster.");
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
      table.layout_review_required ||
      table.missing_formula_cache?.length ||
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
$("#question").addEventListener("input", resizeComposer);
window.addEventListener("resize", resizeComposer);
let composerWidth = 0;
new ResizeObserver(([entry]) => {
  if (entry.contentRect.width !== composerWidth) {
    composerWidth = entry.contentRect.width;
    resizeComposer();
  }
}).observe($("#composer"));
resizeComposer();
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
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
  clearFollowups();
  $("#message-list").replaceChildren();
  window.ActivityJourney.reset();
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
function showTab(name) {
  for (const button of document.querySelectorAll(".tab")) {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  for (const panel of document.querySelectorAll(".tab-panel"))
    panel.hidden = panel.id !== "tab-" + name;
  if (name === "chart") window.AnalysisCharts.render();
}
for (const button of document.querySelectorAll(".tab"))
  button.addEventListener("click", () => showTab(button.dataset.tab));
window.ContextualFollowups.configure({ api, context: () => state.followupContext,
  isBusy: () => state.busy, prefill: prefillQuestion });
window.AnalysisCharts.configure({ api, periodLabel, showTab, showEvidence, columnLabel, renderWarnings, renderChartWarnings,
  isBusy: () => state.busy,
  prefill: prefillQuestion,
});
$("#toggle-columns").addEventListener("click", () => {
  state.fullColumns = !state.fullColumns;
  renderTable();
});
$("#more-rows").addEventListener("click", async () => {
  try {
    const analysisId = state.analysis.analysis_id, workspacePath = base();
    state.offset =
      state.offset + 250 >= state.analysis.row_count ? 0 : state.offset + 250;
    const result = await api(
      workspacePath +
        "/analyses/" +
        analysisId +
        "?offset=" +
        state.offset +
        "&limit=250",
    );
    if (workspacePath !== base() || analysisId !== state.analysis?.analysis_id) return;
    state.analysis.rows = result.rows;
    renderTable();
    $("#more-rows").textContent =
      state.offset + result.rows.length >= state.analysis.row_count
        ? "İlk satırlar"
        : "Diğer satırlar";
  } catch (error) {
    notice(error.message);
  }
});
function configurePaneDivider() {
  const grid = $(".workspace-grid"), divider = $("#workspace-divider");
  const desktop = matchMedia("(min-width: 931px)");
  const storageKey = "agentic-pane-ratio", initialRatio = 0.396;
  let ratio = initialRatio, pointer = null, resizeFrame = null, previousWidth = null;
  try {
    const saved = localStorage.getItem(storageKey), value = Number(saved);
    if (saved !== null && Number.isFinite(value) && value > 0 && value < 1) ratio = value;
  } catch { /* Layout remains usable when browser storage is unavailable. */ }
  const bounds = () => {
    const available = Math.max(1, grid.clientWidth - divider.getBoundingClientRect().width);
    const min = Math.min(320, available * 0.44);
    return { available, min, max: available - Math.min(420, available * 0.56) };
  };
  function update() {
    divider.tabIndex = desktop.matches ? 0 : -1;
    if (!desktop.matches) return;
    const { available, min, max } = bounds();
    const width = Math.max(min, Math.min(max, ratio * available));
    grid.style.setProperty("--conversation-width", width + "px");
    const percent = Math.round(width / available * 100);
    divider.setAttribute("aria-valuemin", String(Math.round(min / available * 100)));
    divider.setAttribute("aria-valuemax", String(Math.round(max / available * 100)));
    divider.setAttribute("aria-valuenow", String(percent));
    divider.setAttribute("aria-valuetext", "Konuşma %" + percent + ", analiz %" + (100 - percent));
  }
  function persist() {
    try { localStorage.setItem(storageKey, String(ratio)); } catch { /* Optional preference. */ }
  }
  function setWidth(width) {
    const { available, min, max } = bounds();
    ratio = Math.max(min, Math.min(max, width)) / available;
    update();
  }
  function finish() {
    if (pointer === null) return;
    const captured = pointer;
    pointer = null;
    divider.classList.remove("is-dragging");
    document.body.classList.remove("pane-resizing");
    if (divider.hasPointerCapture(captured)) divider.releasePointerCapture(captured);
    persist();
  }
  divider.addEventListener("pointerdown", (event) => {
    if (!desktop.matches || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    pointer = event.pointerId;
    divider.focus({ preventScroll: true });
    divider.setPointerCapture(pointer);
    divider.classList.add("is-dragging");
    document.body.classList.add("pane-resizing");
  });
  divider.addEventListener("pointermove", (event) => {
    if (pointer !== event.pointerId) return;
    setWidth(event.clientX - grid.getBoundingClientRect().left - divider.getBoundingClientRect().width / 2);
  });
  divider.addEventListener("pointerup", finish);
  divider.addEventListener("pointercancel", finish);
  divider.addEventListener("lostpointercapture", finish);
  divider.addEventListener("dblclick", () => { ratio = initialRatio; update(); persist(); });
  divider.addEventListener("keydown", (event) => {
    if (!desktop.matches || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const { available, min, max } = bounds();
    const current = Math.max(min, Math.min(max, ratio * available));
    setWidth(event.key === "Home" ? min : event.key === "End" ? max
      : current + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 40 : 12));
    persist();
  });
  new ResizeObserver((entries) => {
    const width = entries[0].contentRect.width;
    if (width === previousWidth) return;
    previousWidth = width;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(update);
  }).observe(grid);
  desktop.addEventListener("change", () => { finish(); update(); });
  update();
}
configurePaneDivider();
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
