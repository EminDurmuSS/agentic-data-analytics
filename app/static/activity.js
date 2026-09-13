"use strict";

// A view of recorded actions. No generated reasoning, inferred progress, or timers.
window.ActivityJourney = (() => {
  const node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text != null) value.textContent = text;
    if (className) value.className = className;
    return value;
  };
  const setText = (element, value) => {
    if (element.textContent !== value) element.textContent = value;
  };
  const select = (id) => document.getElementById(id);
  const shortLabels = { sources: "Kaynaklar", data: "Veriler", calculation: "Hesap", checks: "Kontroller", presentation: "Sunum" };
  const states = { completed: "Tamamlandı", running: "Sürüyor", partial: "Kısmen hazır", needs_input: "Yanıtınız gerekli", blocked: "Devam edemedi", failed: "Tamamlanamadı" };
  const stageStates = { complete: "Tamamlandı", active: "Sürüyor", attention: "Dikkat gerekiyor" };
  const marks = { complete: "✓", active: "", attention: "!" };
  let recordedEvents = [], eventSignature = "";
  const stageNodes = new Map();

  function technicalEvents() {
    if (!select("activity-technical").open) return;
    const signature = JSON.stringify(recordedEvents);
    if (signature === eventSignature) return;
    eventSignature = signature;
    const fragment = document.createDocumentFragment();
    for (const step of recordedEvents) {
      const item = node("li");
      item.append(node("span", step.title || "İşlem kaydı"));
      if (step.detail) item.append(node("small", step.detail));
      fragment.append(item);
    }
    select("events").replaceChildren(fragment);
  }

  function reset() {
    select("activity").hidden = true;
    select("activity").open = false;
    select("activity-technical").open = false;
    select("events").replaceChildren();
    select("journey-content").replaceChildren();
    select("journey-stages").replaceChildren();
    stageNodes.clear();
    recordedEvents = [];
    eventSignature = "";
  }

  function createStage(stage) {
    const chip = node("span", null, "journey-chip");
    const chipMark = node("span", null, "journey-mark");
    chipMark.setAttribute("aria-hidden", "true");
    chip.append(chipMark, node("span", shortLabels[stage.id] || stage.label));
    const details = node("details", null, "journey-stage");
    details.dataset.stage = stage.id;
    const summary = node("summary");
    const mark = node("span", null, "journey-mark");
    mark.setAttribute("aria-hidden", "true");
    const copy = node("span", null, "journey-stage-copy");
    const title = node("span", stage.label, "journey-stage-title");
    const status = node("span", null, "journey-stage-state");
    const description = node("span", null, "journey-stage-description");
    copy.append(title, status, description);
    summary.append(mark, copy, node("span", "＋", "journey-stage-more"));
    summary.lastChild.setAttribute("aria-hidden", "true");
    const items = node("ul", null, "journey-items");
    details.append(summary, items);
    select("journey-stages").append(chip);
    select("journey-content").append(details);
    const parts = { chip, chipMark, details, mark, title, status, description, items, signature: "" };
    stageNodes.set(stage.id, parts);
    return parts;
  }

  function actionList(actions) {
    const list = node("ol", null, "journey-actions");
    for (const action of actions) {
      const state = Object.hasOwn(stageStates, action.status) ? action.status : "attention";
      const row = node("li", null, "journey-action");
      row.dataset.status = state;
      const mark = node("span", marks[state], "journey-action-mark");
      mark.setAttribute("aria-label", stageStates[state]);
      row.append(mark, node("span", action.label));
      list.append(row);
    }
    return list;
  }

  function render(activity, journey) {
    if (!journey && !activity?.length) { reset(); return; }
    journey ||= { status: "partial", title: "Çalışma kaydı", detail: "Bu çalışmanın teknik kayıtlarını inceleyebilirsiniz.", stages: [] };
    const status = Object.hasOwn(states, journey.status) ? journey.status : "partial";
    const card = select("activity");
    card.hidden = false;
    card.dataset.status = status;
    setText(select("activity-title"), journey.title || "Çalışmanın özeti");
    setText(select("journey-status"), states[status]);
    setText(select("journey-description"), journey.detail || journey.title || "");
    const present = new Set();
    let nextStage = select("journey-content").firstChild;
    let nextChip = select("journey-stages").firstChild;
    for (const stage of journey.stages || []) {
      if (present.has(stage.id)) continue;
      present.add(stage.id);
      const parts = stageNodes.get(stage.id) || createStage(stage);
      // Insert newly discovered phases in the summary's order without replacing
      // the existing disclosure (which may currently hold keyboard focus).
      if (parts.details !== nextStage) select("journey-content").insertBefore(parts.details, nextStage);
      if (parts.chip !== nextChip) select("journey-stages").insertBefore(parts.chip, nextChip);
      nextStage = parts.details.nextSibling;
      nextChip = parts.chip.nextSibling;
      const state = Object.hasOwn(stageStates, stage.status) ? stage.status : "attention";
      parts.details.dataset.status = state;
      parts.chip.dataset.status = state;
      parts.chip.setAttribute("aria-label", `${stage.label}: ${stageStates[state]}`);
      setText(parts.chipMark, marks[state]);
      setText(parts.mark, marks[state]);
      setText(parts.title, stage.label);
      setText(parts.status, stageStates[state]);
      setText(parts.description, stage.summary || "");
      if (state === "active") parts.details.setAttribute("aria-current", "step");
      else parts.details.removeAttribute("aria-current");
      const signature = JSON.stringify(stage.items || []);
      if (signature !== parts.signature) {
        parts.signature = signature;
        const previousRows = [...parts.items.children];
        let restoreFocus;
        const children = (stage.items || []).map((item, index) => {
          const row = node("li");
          row.dataset.status = item.status;
          const heading = node("span", item.label, "journey-item-label");
          if (item.status === "attention") heading.append(node("span", "Kontrol gerekli", "journey-item-attention"));
          row.append(heading);
          if (item.detail) row.append(node("span", item.detail, "journey-item-detail"));
          const actions = (item.actions || []).filter(action => typeof action.label === "string" && action.label.trim());
          if (actions.length) row.append(actionList(actions.slice(0, 4)));
          if (actions.length > 4) {
            const more = node("details", null, "journey-actions-more");
            const summary = node("summary", "Diğer adımları göster (" + (actions.length - 4) + ")");
            const previous = previousRows[index]?.querySelector(".journey-actions-more");
            more.open = Boolean(previous?.open);
            if (previous?.firstElementChild === document.activeElement) restoreFocus = summary;
            more.append(summary, actionList(actions.slice(4)));
            row.append(more);
          }
          return row;
        });
        parts.items.replaceChildren(...children);
        restoreFocus?.focus({ preventScroll: true });
      }
    }
    for (const [id, parts] of stageNodes) {
      if (!present.has(id)) { parts.details.remove(); parts.chip.remove(); stageNodes.delete(id); }
    }
    recordedEvents = activity || [];
    setText(select("activity-count"), `${recordedEvents.length} kayıt`);
    select("activity-technical").hidden = !recordedEvents.length;
    technicalEvents();
  }

  function pending(message, journey) {
    const body = message.querySelector(".body");
    let status = body.querySelector(".journey-live");
    if (!status) {
      status = node("div", null, "journey-live");
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      status.setAttribute("aria-atomic", "true");
      status.append(node("span", null, "journey-live-title"), node("span", null, "journey-live-detail"));
      body.replaceChildren(status);
    }
    const active = journey?.stages?.findLast((stage) => stage.status === "active");
    const attention = journey && !["running", "completed"].includes(journey.status);
    status.dataset.status = journey?.status || "running";
    setText(status.firstChild, attention ? (journey.title || states[journey.status]) : (active?.label || journey?.title || "Sorunuz alındı"));
    setText(status.lastChild, attention ? journey.detail : (active?.summary || journey?.detail || "İlgili kaynaklar ve yapılacak hesaplar belirleniyor."));
  }

  select("activity-technical").addEventListener("toggle", technicalEvents);
  return { render, pending, reset };
})();
