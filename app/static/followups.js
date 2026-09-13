"use strict";
// Conversation suggestions belong to a completed run, never to a chart view.
window.ContextualFollowups = (() => {
  const fields = ["workspace_id", "conversation_id", "run_id", "analysis_id"];
  const normalized = (value) => Object.fromEntries(fields.map((key) => [key, value?.[key] ?? null]));
  const equal = (left, right) => fields.every((key) => (left?.[key] ?? null) === (right?.[key] ?? null));
  const node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  };
  let hooks, generation = 0, controller = null, owner = null, anchor = null, digest = null, analysisBinding, section = null;

  function clear() {
    generation++;
    controller?.abort();
    controller = null;
    owner = null;
    anchor = null;
    digest = null;
    analysisBinding = undefined;
    section?.remove();
    section = null;
  }
  function current(version) {
    return version === generation && owner && anchor?.isConnected && equal(owner, hooks.context());
  }
  function container() {
    if (!section) {
      section = node("section", undefined, "conversation-followups");
      section.id = "conversation-followups";
      section.setAttribute("aria-label", "Bu konuşmaya özel takip soruları");
      section.dataset.runId = owner.run_id;
      anchor.after(section);
    }
    return section;
  }
  function pending() {
    const holder = container();
    holder.dataset.status = "pending";
    const text = node("p", "Sonuca göre yeni sorular hazırlanıyor", "followups-pending");
    text.setAttribute("role", "status");
    holder.replaceChildren(text);
  }
  function hide(version) {
    if (!current(version)) return;
    section?.remove();
    section = null;
  }
  function render(items, version) {
    const seen = new Set();
    const valid = (Array.isArray(items) ? items : []).filter((item) => {
      if (!item || typeof item.id !== "string" || seen.has(item.id)
          || typeof item.prompt !== "string" || !item.prompt.trim() || item.prompt.length > 2000) return false;
      seen.add(item.id);
      return true;
    }).slice(0, 3);
    if (!valid.length) { hide(version); return; }
    const messages = document.querySelector("#messages");
    const readingPosition = messages?.scrollTop;
    const holder = container();
    holder.dataset.status = "ready";
    const heading = node("div", undefined, "followups-heading");
    heading.append(node("h3", "Buradan devam edebiliriz"));
    const choices = node("div", undefined, "followups-list");
    for (const item of valid) {
      const card = node("article", undefined, "followup-card");
      const button = node("button", undefined, "followup-question");
      button.type = "button";
      button.dataset.followupId = item.id;
      // Keep the actual question readable; execution still uses the complete
      // original prompt, whose qualifiers remain available in the details.
      const questionEnd = item.prompt.indexOf("?");
      const coreQuestion = questionEnd < 0 ? item.prompt : item.prompt.slice(0, questionEnd + 1);
      const question = node("span", coreQuestion, "followup-prompt");
      const arrow = node("span", "↗", "followup-arrow");
      arrow.setAttribute("aria-hidden", "true");
      button.append(question, arrow);
      button.onclick = () => {
        if (!current(version) || hooks.isBusy()) return;
        hooks.prefill(item.prompt);
      };
      const details = node("details", undefined, "followup-details");
      const summary = node("summary");
      summary.append(node("span", "Ayrıntılar"));
      if (item.requires_new_data === true)
        summary.append(node("span", "Ek veri gerekebilir", "followup-data-hint"));
      const fullPrompt = node("p", item.prompt, "followup-full-prompt");
      details.append(summary, fullPrompt);
      if (typeof item.reason === "string" && item.reason.trim())
        details.append(node("p", item.reason, "followup-reason"));
      card.append(button, details);
      choices.append(card);
    }
    holder.replaceChildren(heading, choices);
    updateBusy();
    // Optional suggestions arrive after the answer. Never push the answer out
    // of view or pull someone away from the passage they are reading.
    if (messages) messages.scrollTop = readingPosition;
  }
  function updateBusy() {
    for (const button of section?.querySelectorAll("button") || []) button.disabled = Boolean(hooks.isBusy());
  }
  function pause(milliseconds, signal) {
    return new Promise((resolve) => {
      const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
      const timer = setTimeout(finish, milliseconds);
      signal.addEventListener("abort", finish, { once: true });
      if (signal.aborted) finish();
    });
  }
  async function load(context, response) {
    clear();
    if (!context?.workspace_id || !context.run_id || !context.conversation_id || !response?.isConnected) return;
    owner = normalized(context);
    anchor = response;
    const version = generation;
    if (!current(version)) return;
    const requestController = new AbortController();
    controller = requestController;
    const signal = requestController.signal;
    const deadline = setTimeout(() => requestController.abort(), 60000);
    const endpoint = "/api/workspaces/" + encodeURIComponent(owner.workspace_id) + "/runs/" + encodeURIComponent(owner.run_id) + "/followups";
    pending();
    try {
      // One idempotent request starts or retrieves the cached background job.
      // Subsequent GETs only observe it; neither a timer nor a page poll creates
      // an unbounded sequence of new model generations.
      let result = await hooks.api(endpoint, { method: "POST", signal });
      for (let attempt = 0; attempt < 40; attempt++) {
        if (!current(version)) return;
        const returnedAnalysis = result?.analysis_id ?? null;
        // The public run may omit an analysis reference while the backend can
        // prove one from this run's successful tools. Accept that first binding,
        // then keep it fixed; never substitute the workspace's old chart head.
        if (!fields.slice(0, 3).every((key) => owner[key] === result?.[key])
            || (owner.analysis_id !== null && owner.analysis_id !== returnedAnalysis)
            || (analysisBinding !== undefined && analysisBinding !== returnedAnalysis)
            || typeof result.context_digest !== "string"
            || !result.context_digest || (digest && digest !== result.context_digest)) { hide(version); return; }
        analysisBinding = returnedAnalysis;
        digest = result.context_digest;
        if (result.status === "ready") { render(result.items, version); return; }
        if (result.status !== "pending") { hide(version); return; }
        await pause(1500, signal);
        if (!current(version) || signal.aborted) { hide(version); return; }
        result = await hooks.api(endpoint, { signal });
      }
      hide(version);
    } catch (_) {
      // Optional follow-ups must never turn a successful answer into an error.
      hide(version);
    } finally {
      clearTimeout(deadline);
      if (version === generation) controller = null;
    }
  }
  return { configure: (callbacks) => { hooks = callbacks; }, load, clear, updateBusy };
})();
