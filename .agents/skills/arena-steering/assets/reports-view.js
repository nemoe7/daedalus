// Reports tab view with left TOC rail, center report column, and right report selector rail.

import { reconcileView } from "./dom.js";
import { escapeHtml } from "./markdown.js";
import { relativeTime } from "./timestamps.js";
import { createMarkdownBatch } from "./markdown-dom-cache.js";
import { copyText } from "./copy.js";

const COPY_SVG = `<svg class="lucide lucide-copy" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>`;
const CHECK_SVG = `<svg class="lucide lucide-check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 6 9 17l-5-5"></path></svg>`;
const DISMISS_SVG = `<svg class="lucide lucide-x" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>`;
const SEND_SVG = `<svg class="lucide lucide-send" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"></path><path d="m21.854 2.147-10.94 10.939"></path></svg>`;
const UNANSWERED_SVG = `<svg class="lucide lucide-circle-help report-picker-help-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path></svg>`;
const CHEVRON_DOWN_SVG = `<svg class="lucide lucide-chevron-down" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m6 9 6 6 6-6"></path></svg>`;
const ARROW_UP_SVG = `<svg class="lucide lucide-arrow-up" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m5 12 7-7 7 7"></path><path d="M12 19V5"></path></svg>`;

const CUSTOM_SENTINEL = "__custom__";
const TOC_TOP_THRESHOLD_PX = 48;
const TOC_BOTTOM_CLAMP_PX = 8;
const TOC_CLICK_LOCK_MS = 650;

let lastRenderedReportId = null;
const pendingReportSubmissions = new Set();
const DRAFT_KEY = "steer-report-drafts";

function readDrafts() {
  try {
    return JSON.parse(window.localStorage.getItem(DRAFT_KEY) || "{}");
  } catch {
    return {};
  }
}

function readDraft(reportId, fieldId) {
  const drafts = readDrafts();
  const key = `${reportId}:${fieldId}`;
  return Object.hasOwn(drafts, key) ? drafts[key] : undefined;
}

function writeDrafts(reportId, values) {
  try {
    const drafts = readDrafts();
    for (const [fieldId, value] of Object.entries(values)) {
      drafts[`${reportId}:${fieldId}`] = value;
    }
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(drafts));
  } catch {
    /* storage can be unavailable, so drafts stay in memory for this render */
  }
}

function clearDrafts(reportId) {
  try {
    const drafts = readDrafts();
    for (const key of Object.keys(drafts)) {
      if (key.startsWith(`${reportId}:`)) delete drafts[key];
    }
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(drafts));
  } catch {
    /* nothing to clear when storage is unavailable */
  }
}

function storedValuesFor(report) {
  const merged = {};
  for (const answer of report.answers || []) {
    for (const [fieldId, value] of Object.entries(answer.values || {})) {
      merged[fieldId] = value;
    }
  }
  return merged;
}

let mobilePickerCollapsed = false;
let pickerScrollTop = 0;
let activeScrollCleanup = null;
let reportRenderEvents = null;

function renderFieldBlock(field, headings, stored) {
  const fieldId = escapeHtml(field.id);
  const label = escapeHtml(field.question || field.label);
  const anchorId = `field-${field.id}`;
  if (Array.isArray(headings)) {
    headings.push({ id: anchorId, level: 3, text: field.question || field.label });
  }
  const recommended = new Set(field.recommended || []);
  const options = field.options || [];
  const storedList = Array.isArray(stored)
    ? stored.map(String)
    : stored === null || stored === undefined || stored === ""
      ? []
      : [String(stored)];
  const storedSet = new Set(storedList);
  const knownAnswers = new Set([...options, ...Object.values(field.option_labels || {})]);
  const customStored = storedList.find((item) => !knownAnswers.has(item)) || "";
  const suggestedDefault = stored === undefined && recommended.size > 0;
  const firstSuggested = options.find((option) => recommended.has(option));

  if (field.type === "text") {
    const textValue = storedList[0] || "";
    return `
      <div class="field-card question" id="${escapeHtml(anchorId)}" data-field-id="${fieldId}" data-field-type="text">
        <div class="field-header">
          <span class="field-label">${label}</span>
          <code class="field-id-chip msg-id">${fieldId}</code>
        </div>
        <textarea class="input field-text-input" name="field-${fieldId}" rows="1" placeholder="Something else..." aria-label="${label}">${escapeHtml(textValue)}</textarea>
      </div>
    `;
  }

  const inputType = field.type === "choice" ? "radio" : "checkbox";
  const optionsHtml = options
    .map((opt) => {
      const preselected = suggestedDefault && recommended.has(opt) &&
        (inputType === "checkbox" || opt === firstSuggested);
      const savedLabel = field.option_labels?.[opt];
      const isChecked = storedSet.has(opt) ||
        (savedLabel && storedSet.has(savedLabel)) || preselected;
      const checked = isChecked ? "checked" : "";
      const star = recommended.has(opt)
        ? '<span class="option-star" aria-label="Recommended by agent">*</span> '
        : "";
      return `
        <label class="option-row${recommended.has(opt) ? " is-recommended" : ""}">
          <input type="${inputType}" name="field-${fieldId}" value="${escapeHtml(opt)}" ${checked}>
          <span>${star}${escapeHtml(opt)}</span>
        </label>
      `;
    })
    .join("");

  return `
    <div class="field-card question" id="${escapeHtml(anchorId)}" data-field-id="${fieldId}" data-field-type="${escapeHtml(field.type)}" data-suggested-default="${suggestedDefault}">
      <div class="field-header">
        <span class="field-label">${label}</span>
        <code class="field-id-chip msg-id">${fieldId}</code>
      </div>
      <div class="options">
        ${optionsHtml}
        <label class="option-row option-custom">
          <input type="${inputType}" name="field-${fieldId}" value="${CUSTOM_SENTINEL}" class="custom-toggle" ${customStored ? "checked" : ""} aria-label="Something else for ${label}">
          <textarea class="input custom-text-input" rows="1" placeholder="Something else..." aria-label="Custom response for ${label}">${escapeHtml(customStored)}</textarea>
        </label>
      </div>
    </div>
  `;
}

export function renderReportsView(container, state, handlers) {
  reportRenderEvents?.abort();
  reportRenderEvents = new AbortController();
  const signal = reportRenderEvents.signal;
  const listen = (target, type, callback, options = {}) =>
    target?.addEventListener(type, callback, { ...options, signal });
  if (typeof activeScrollCleanup === "function") {
    activeScrollCleanup();
    activeScrollCleanup = null;
  }
  const previousPicker = container.querySelector("#report-picker");
  if (previousPicker?.clientHeight) pickerScrollTop = previousPicker.scrollTop;

  const reports = state.reports || [];
  if (!reports.length) {
    lastRenderedReportId = null;
    pickerScrollTop = 0;
    container.dataset.scrollable = "false";
    container.innerHTML = `<div class="reports-layout">${state.reportDismissError ? `<p role="alert">${escapeHtml(state.reportDismissError)}</p>` : ""}<p class="muted" id="report-status">No report published yet.</p></div>`;
    return;
  }

  const selectedId =
    state.selectedReportId && reports.some((r) => r.id === state.selectedReportId)
      ? state.selectedReportId
      : reports[0].id;
  let activeReport = reports.find((r) => r.id === selectedId) || reports[0];
  const pending = (state.pendingReportAnswers || {})[activeReport.id];
  if (pending?.reportId === activeReport.id &&
      !(activeReport.answers || []).some((answer) => answer.id === pending.answerId)) {
    const fields = activeReport.fields || [];
    const fieldLabels = Object.fromEntries(fields.map((field) => [field.id, field.label]));
    const values = { ...pending.values };
    for (const field of fields) {
      const value = values[field.id];
      const label = (item) => field.option_labels?.[item] || item;
      if (typeof value === "string") values[field.id] = label(value);
      else if (Array.isArray(value)) values[field.id] = value.map(label);
    }
    activeReport = { ...activeReport, answers: [...(activeReport.answers || []), {
      id: "Sending…", values, field_labels: fieldLabels, pending: true,
      modified_at: new Date().toISOString(),
    }] };
  }
  const loadingReport = state.loadingReportId === activeReport.id && !Array.isArray(activeReport.blocks);
  const swappedReport =
    lastRenderedReportId !== null && lastRenderedReportId !== activeReport.id;
  lastRenderedReportId = activeReport.id;

  const blocks = activeReport.blocks || (loadingReport ? [] : [{ type: "markdown" }]);
  const hasFields = !loadingReport && (activeReport.fields || []).length > 0;
  const submitBusyAttrs = pendingReportSubmissions.has(activeReport.id)
    ? 'disabled aria-busy="true"'
    : "";
  const headings = [];
  const markdownBatch = createMarkdownBatch();

  const answerValues = storedValuesFor(activeReport);
  const draftValues = readDrafts();
  const fieldValues = { ...answerValues };
  for (const [key, value] of Object.entries(draftValues)) {
    const prefix = `${activeReport.id}:`;
    if (key.startsWith(prefix)) fieldValues[key.slice(prefix.length)] = value;
  }

  const updatedAt = activeReport.updated_at || activeReport.modified_at;
  const modIso = escapeHtml(updatedAt || "");
  const modRel = escapeHtml(relativeTime(updatedAt));
  const reportParentSelected = (state.replyParentRefs || []).some(
    (ref) => ref.type === "report" && ref.id === activeReport.id
  );
  const linkedTasks = (state.tasks || []).filter(task =>
    (task.links?.report_ids || task.report_ids || []).includes(activeReport.id)
  );
  const taskLinksHtml = linkedTasks.length
    ? `<span class="report-task-links">${linkedTasks.map(task => `<button type="button" class="reply-parent-chip" data-reference-type="task" data-reference-id="${escapeHtml(task.id)}" aria-label="Open task ${escapeHtml(task.id)}"><code>${escapeHtml(task.id)}</code></button>`).join(" ")}</span>`
    : "";
  const statusBlockHtml = `
    <div id="report-status" class="report-meta muted" role="status">
      ${taskLinksHtml}
      <button type="button" class="parent-id-button" data-parent-type="report" data-parent-id="${escapeHtml(activeReport.id)}" aria-label="Report ${escapeHtml(activeReport.id)} as message parent" aria-pressed="${reportParentSelected ? "true" : "false"}"><code class="msg-id">${escapeHtml(activeReport.id)}</code></button>
      <span>Updated <time datetime="${modIso}" data-timestamp="${modIso}">${modRel}</time></span>
    </div>
  `;
  let insertedStatus = false;
  const reportProse = (html) => {
    if (!insertedStatus && html.includes("</h1>")) {
      insertedStatus = true;
      html = html.replace("</h1>", `</h1>${statusBlockHtml}`);
    }
    return markdownBatch.slot(html, "prose-block");
  };

  const renderedBlocks = blocks
    .map((block) => {
      if (block.type === "field") {
        return renderFieldBlock(block.field, headings, fieldValues[block.field.id]);
      }
      if (typeof block.html === "string") {
        if (Array.isArray(block.headings)) {
          for (const h of block.headings) {
            headings.push(h);
          }
        }
        return reportProse(block.html);
      }
      return reportProse('<p role="alert">Rendered report unavailable.</p>');
    })
    .join("");

  const tocLinksHtml = headings
    .map(
      (h, idx) => `
      <a
        href="#${escapeHtml(h.id)}"
        class="report-toc-link ${idx === 0 ? "active" : ""}"
        data-toc-target="${escapeHtml(h.id)}"
        data-level="${h.level}"
        aria-current="${idx === 0 ? "true" : "false"}"
      >${escapeHtml(h.text)}</a>
    `
    )
    .join("");

  const pickerItemsHtml = reports
    .map((rep, idx) => {
      const isActive = rep.id === activeReport.id;
      const isUnseen = !rep.seen;
      const answers = rep.answers || [];
      const hasUnseenAck = answers[answers.length - 1]?.status === "acked" && !rep.ack_seen;
      const needsAnswer = Boolean(
        rep.needs_answer ??
          ((rep.fields || []).length > 0 && (rep.answers || []).length === 0)
      );
      const unseenLabel = isUnseen ? "Unseen report" : "Unseen answer acknowledgement";
      const markersHtml =
        isUnseen || hasUnseenAck || needsAnswer
          ? `<span class="report-picker-markers">${needsAnswer ? `<span class="report-picker-unanswered" aria-label="Needs answers">${UNANSWERED_SVG}</span>` : ""}${isUnseen || hasUnseenAck ? `<span class="report-picker-unseen-dot ${!isUnseen && hasUnseenAck ? "is-unseen-ack" : ""}" aria-label="${unseenLabel}"></span>` : ""}</span>`
          : "";
      return `
        <button
          type="button"
          class="report-picker-link report-nav-item ${isActive ? "is-active active" : ""} ${isUnseen ? "is-unseen" : "is-seen"} ${needsAnswer ? "needs-answer" : ""}"
          data-report-id="${escapeHtml(rep.id)}"
          aria-current="${isActive ? "true" : "false"}"
        >
          <span class="report-picker-title">${idx + 1}. ${escapeHtml(rep.title)}</span>
          ${markersHtml}
        </button>
      `;
    })
    .join("");

  const answersHtml = (activeReport.answers || []).length
    ? `<div id="report-ack-history">${activeReport.answers
        .map((ans) => {
          const iso = escapeHtml(ans.modified_at || "");
          const rel = escapeHtml(relativeTime(ans.modified_at));
          const entries = Object.entries(ans.values || {})
            .map(([k, v]) => {
              const label = ans.field_labels?.[k] || k;
              if (v && typeof v === "object" && !Array.isArray(v)) {
                const selected = Array.isArray(v.selected) ? v.selected : (v.custom ? [] : [v.selected]);
                v = [...selected.filter(Boolean), ...(v.custom ? [v.custom] : [])];
              }
              return `${label}: ${Array.isArray(v) ? v.join(", ") : v}`;
            })
            .join(" · ");
          const status = ans.pending ? "Sending" : ans.status === "sent" ? "Sent" : ans.status === "seen" ? "Seen" : "";
          const stateLabel = escapeHtml(`Status: ${ans.pending ? "sending" : ans.status}`);
          return `
            <div class="message report-answer-entry" data-answer-id="${escapeHtml(ans.id)}">
              <div class="message-heading"><strong><code class="msg-id" aria-label="Answer ${escapeHtml(ans.id)}">${escapeHtml(ans.id)}</code></strong></div>
              <div class="message-bubble">
                <div class="message-text"><p>${escapeHtml(entries)}</p></div>
                ${ans.pending ? `<div class="answer reply" role="status">Sending answer…</div>` : ans.ack ? `<div class="answer reply message-text">${markdownBatch.slot(ans.ack_html ?? '<p role="alert">Rendered acknowledgement unavailable.</p>', "report-ack-markdown")}</div>` : ""}
              </div>
              <p class="receipt"><time role="img" aria-label="${stateLabel}, ${rel}" data-state-label="${stateLabel}" datetime="${iso}" data-timestamp="${iso}" data-prefix="${status}">${status ? `${status} ` : ""}${rel}</time></p>
            </div>
          `;
        })
        .join("")}</div>`
    : "";

  const reportBody = loadingReport ? '<p role="status">Loading report…</p>' : renderedBlocks;
  const selectionError = state.reportSelectionError ? `<p role="alert">${escapeHtml(state.reportSelectionError)}</p>` : "";
  const renderedWithMeta = (insertedStatus && !loadingReport
    ? reportBody
    : `<div class="prose-block"><h1>${escapeHtml(activeReport.title)}</h1>${statusBlockHtml}</div>${reportBody}`) + selectionError;

  const markup = `
    <div class="reports-layout">
      <aside id="report-toc" class="report-toc scroll-panel" aria-label="Report contents" ${headings.length ? "" : "hidden"}>
        <div class="report-toc-list">
          ${tocLinksHtml}
        </div>
      </aside>

      <aside id="report-rail" class="report-rail" aria-label="Report selector and actions" data-mobile-collapsed="${mobilePickerCollapsed ? "true" : "false"}">
        <div class="report-rail-bar">
          <button
            type="button"
            id="report-picker-toggle"
            class="report-picker-toggle"
            aria-expanded="${mobilePickerCollapsed ? "false" : "true"}"
            aria-controls="report-picker"
          >
            ${CHEVRON_DOWN_SVG}
            <span class="report-toc-title">Reports (${reports.length})</span>
          </button>
          <div class="report-rail-actions">
            <button
              type="button"
              class="icon-button"
              id="copy-report-btn"
              data-copy-report="${escapeHtml(activeReport.id)}"
              aria-label="Copy report ${escapeHtml(activeReport.id)}"
              ${loadingReport ? "disabled" : ""}
            >${COPY_SVG}</button>
            <button
              type="button"
              class="icon-button"
              id="dismiss-report-btn"
              data-dismiss-report="${escapeHtml(activeReport.id)}"
              aria-label="Dismiss report ${escapeHtml(activeReport.id)}"
              ${loadingReport ? "disabled" : ""}
            >${DISMISS_SVG}</button>
          </div>
        </div>
        <div id="report-picker" class="report-picker scroll-panel" role="region" aria-label="Published reports">
          ${pickerItemsHtml}
        </div>
        ${answersHtml}
      </aside>

      <div id="report-main" class="report-main">
        ${state.reportDismissError ? `<p role="alert">${escapeHtml(state.reportDismissError)}</p>` : ""}
        <form id="report-answer-form" data-report-id="${escapeHtml(activeReport.id)}">
          ${state.reportAnswerErrors?.[activeReport.id] ? `<p role="alert">${escapeHtml(state.reportAnswerErrors[activeReport.id])}</p>` : ""}
          <article id="report" class="report" aria-label="Selected report">
            ${renderedWithMeta}
          </article>
          ${
            hasFields
              ? `<div class="report-submit-row"><button type="submit" id="report-submit" class="btn btn-primary" ${submitBusyAttrs}>${SEND_SVG} Submit</button></div>`
              : ""
          }
        </form>
      </div>
      ${
        hasFields
          ? `<button type="button" id="report-submit-float" class="btn btn-primary report-submit-float" ${submitBusyAttrs}>${SEND_SVG} Submit</button>`
          : ""
      }
      <button
        type="button"
        id="report-scroll-top"
        class="report-scroll-top"
        aria-label="Scroll to top"
        hidden
      >${ARROW_UP_SVG}</button>
    </div>
  `;
  // Keep the rail and picker mounted, but reset each report's form and
  // acknowledgement scroll position when switching between reports.
  if (swappedReport) {
    container.querySelector("#report-main")?.replaceChildren();
    container.querySelector("#report-ack-history")?.remove();
  }
  reconcileView(container, markup, root => markdownBatch.mount(root));

  if (swappedReport) container.scrollTop = 0;

  for (const input of container.querySelectorAll(".field-text-input, .custom-text-input")) {
    const resize = () => {
      const border = input.offsetHeight - input.clientHeight;
      input.style.height = "auto";
      input.style.height = `${input.scrollHeight + border}px`;
    };
    resize();
    listen(input, "input", resize);
  }

  const railEl = container.querySelector("#report-rail");
  const pickerEl = container.querySelector("#report-picker");
  const ackEl = container.querySelector("#report-ack-history");
  const tocEl = container.querySelector("#report-toc");
  const syncScrollEdges = (element) => {
    if (!element?.isConnected || !element.clientHeight) return;
    element.toggleAttribute("data-scrolled-start", element.scrollTop > 0);
    element.toggleAttribute(
      "data-scrolled-end",
      element.scrollHeight - element.clientHeight - element.scrollTop > 1
    );
  };
  const syncPickerScroll = () => {
    if (!pickerEl?.isConnected || !pickerEl.clientHeight) return;
    pickerScrollTop = pickerEl.scrollTop;
    syncScrollEdges(pickerEl);
  };
  const restorePickerScroll = () => {
    if (!pickerEl?.clientHeight) return;
    pickerEl.scrollTop = pickerScrollTop;
    syncPickerScroll();
  };
  const keepSelectedPickerItemVisible = () => {
    if (!pickerEl?.clientHeight) return;
    const selected = pickerEl.querySelector('[aria-current="true"]');
    if (!selected) return;
    const pickerBounds = pickerEl.getBoundingClientRect();
    const selectedBounds = selected.getBoundingClientRect();
    if (selectedBounds.top < pickerBounds.top) {
      pickerEl.scrollTop += selectedBounds.top - pickerBounds.top;
    } else if (selectedBounds.bottom > pickerBounds.bottom) {
      pickerEl.scrollTop += selectedBounds.bottom - pickerBounds.bottom;
    }
    syncPickerScroll();
  };
  listen(pickerEl, "scroll", syncPickerScroll, { passive: true });
  listen(ackEl, "scroll", () => syncScrollEdges(ackEl), { passive: true });
  listen(tocEl, "scroll", () => syncScrollEdges(tocEl), { passive: true });
  listen(window, "resize", () => syncScrollEdges(tocEl), { passive: true });
  restorePickerScroll();
  if (swappedReport || !previousPicker) keepSelectedPickerItemVisible();
  if (swappedReport && tocEl) tocEl.scrollTop = 0;
  syncScrollEdges(ackEl);
  syncScrollEdges(tocEl);

  const pickerToggleBtn = container.querySelector("#report-picker-toggle");
  listen(pickerToggleBtn, "click", () => {
    mobilePickerCollapsed = !mobilePickerCollapsed;
    if (railEl) {
      railEl.dataset.mobileCollapsed = mobilePickerCollapsed ? "true" : "false";
    }
    pickerToggleBtn.setAttribute(
      "aria-expanded",
      mobilePickerCollapsed ? "false" : "true"
    );
    restorePickerScroll();
    if (!mobilePickerCollapsed) keepSelectedPickerItemVisible();
    syncScrollState();
  });

  const scrollTopBtn = container.querySelector("#report-scroll-top");
  listen(scrollTopBtn, "click", () => {
    if (typeof container.scrollTo === "function") {
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      container.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    } else {
      container.scrollTop = 0;
    }
  });

  let tocClickLockId = null;
  let tocClickLockUntil = 0;
  let tocFlashTimer = null;

  const setActiveTocLink = (targetId) => {
    for (const item of container.querySelectorAll(".report-toc-link")) {
      const isMatch = item.dataset.tocTarget === targetId;
      item.classList.toggle("active", isMatch);
      item.setAttribute("aria-current", isMatch ? "true" : "false");
    }
  };

  const syncScrollState = () => {
    const scrollable =
      (container.scrollHeight || 0) > (container.clientHeight || 0) + 1;
    container.dataset.scrollable = scrollable ? "true" : "false";
    if (scrollTopBtn) {
      const toc = container.querySelector("#report-toc");
      const tocAvailable = toc && !toc.hidden && toc.getClientRects().length > 0;
      scrollTopBtn.hidden = Boolean(tocAvailable) || (container.scrollTop || 0) <= 160;
    }
    // Picker scrolling cannot trigger the top fade.
    // It appears only after report content passes the panel edge.
    const main = container.querySelector("#report-main");
    if (main) {
      const panelTop = container.getBoundingClientRect().top;
      main.toggleAttribute(
        "data-scrolled-start",
        main.getBoundingClientRect().top < panelTop - 2
      );
    }
    if (!headings.length) return;
    if (tocClickLockId && Date.now() < tocClickLockUntil) {
      setActiveTocLink(tocClickLockId);
      return;
    }
    tocClickLockId = null;
    if (
      scrollable &&
      container.scrollTop + container.clientHeight >=
        container.scrollHeight - TOC_BOTTOM_CLAMP_PX
    ) {
      setActiveTocLink(headings[headings.length - 1].id);
      return;
    }
    const containerTop = container.getBoundingClientRect?.().top || 0;
    let activeId = headings[0].id;
    for (const h of headings) {
      const el = container.querySelector(`#${CSS.escape(h.id)}`);
      if (!el?.getBoundingClientRect) continue;
      const relTop = el.getBoundingClientRect().top - containerTop;
      if (relTop <= TOC_TOP_THRESHOLD_PX) {
        activeId = h.id;
      } else {
        break;
      }
    }
    setActiveTocLink(activeId);
  };

  container.addEventListener?.("scroll", syncScrollState, { passive: true });
  listen(window, "resize", syncScrollState, { passive: true });
  activeScrollCleanup = () => {
    container.removeEventListener?.("scroll", syncScrollState);
    if (tocFlashTimer) {
      clearTimeout(tocFlashTimer);
      tocFlashTimer = null;
    }
  };
  syncScrollState();

  for (const btn of container.querySelectorAll(".report-picker-link[data-report-id]")) {
    listen(btn, "click", () => {
      handlers.onSelectReport(btn.dataset.reportId);
    });
  }

  for (const link of container.querySelectorAll("[data-toc-target]")) {
    listen(link, "click", (event) => {
      event.preventDefault();
      const targetId = link.dataset.tocTarget;
      tocClickLockId = targetId;
      tocClickLockUntil = Date.now() + TOC_CLICK_LOCK_MS;
      setActiveTocLink(targetId);
      const targetEl = container.querySelector(`#${CSS.escape(targetId)}`);
      if (targetEl) {
        targetEl.scrollIntoView?.({ behavior: "smooth", block: "start" });
        for (const flashed of container.querySelectorAll("[data-toc-flash]")) {
          delete flashed.dataset.tocFlash;
        }
        targetEl.dataset.tocFlash = "true";
        if (tocFlashTimer) clearTimeout(tocFlashTimer);
        tocFlashTimer = setTimeout(() => {
          if (targetEl.isConnected) {
            delete targetEl.dataset.tocFlash;
          }
        }, 1200);
      }
    });
  }

  for (const question of container.querySelectorAll(".question")) {
    const customToggle = question.querySelector(".custom-toggle");
    const customInput = question.querySelector(".custom-text-input");
    if (!customToggle || !customInput) continue;
    listen(customInput, "focus", () => {
      customToggle.checked = true;
    });
    listen(customInput, "input", () => {
      if (customInput.value.trim()) {
        customToggle.checked = true;
      }
    });
  }

  const copyBtn = container.querySelector("#copy-report-btn");
  let copyResetTimer;
  listen(copyBtn, "click", () => {
    const copied = copyText(activeReport.body || "", copyBtn);
    const originalLabel = `Copy report ${activeReport.id}`;
    const resultLabel = copied
      ? "Report Markdown copied"
      : "Copy failed. Browser did not copy report Markdown.";
    clearTimeout(copyResetTimer);
    copyBtn.dataset.state = copied ? "good" : "bad";
    copyBtn.innerHTML = copied ? CHECK_SVG : DISMISS_SVG;
    copyBtn.setAttribute("aria-label", resultLabel);
    copyResetTimer = setTimeout(() => {
      if (!copyBtn.isConnected) return;
      delete copyBtn.dataset.state;
      copyBtn.innerHTML = COPY_SVG;
      copyBtn.setAttribute("aria-label", originalLabel);
    }, 1400);
  });
  signal.addEventListener("abort", () => clearTimeout(copyResetTimer), { once: true });

  const dismissBtn = container.querySelector("#dismiss-report-btn");
  listen(dismissBtn, "click", async () => {
    await handlers.onDismissReport(activeReport.id, dismissBtn);
  });

  const form = container.querySelector("#report-answer-form");
  const submitBtn = container.querySelector("#report-submit");
  const submitFloat = container.querySelector("#report-submit-float");
  listen(submitFloat, "click", () => {
    if (pendingReportSubmissions.has(activeReport.id)) return;
    if (typeof form?.requestSubmit === "function") {
      form.requestSubmit(submitBtn || undefined);
    } else {
      submitBtn?.click();
    }
  });
  const collectValues = (includeSuggestedDefaults = false) => {
    const values = {};
    for (const question of container.querySelectorAll(
      ".question[data-field-id]"
    )) {
      const fid = question.dataset.fieldId;
      const ftype = question.dataset.fieldType;
      if (
        !includeSuggestedDefaults &&
        question.dataset.suggestedDefault === "true" &&
        question.dataset.userTouched !== "true"
      ) continue;
      if (ftype === "text") {
        const val =
          question.querySelector(".field-text-input")?.value.trim() || "";
        if (val) values[fid] = val;
      } else if (ftype === "choice") {
        const checked = question.querySelector('input[type="radio"]:checked');
        if (!checked) continue;
        if (checked.value === CUSTOM_SENTINEL) {
          const customVal =
            question.querySelector(".custom-text-input")?.value.trim() || "";
          if (customVal) values[fid] = customVal;
        } else {
          values[fid] = checked.value;
        }
      } else if (ftype === "checkbox") {
        const checkedList = [
          ...question.querySelectorAll('input[type="checkbox"]:checked'),
        ];
        const selected = [];
        for (const box of checkedList) {
          if (box.value === CUSTOM_SENTINEL) {
            const customVal =
              question.querySelector(".custom-text-input")?.value.trim() || "";
            if (customVal) selected.push(customVal);
          } else {
            selected.push(box.value);
          }
        }
        if (selected.length) values[fid] = selected;
      }
    }
    return values;
  };

  const saveOwnerDraft = (event) => {
    if (event.type === "click" && !event.target.matches('input[type="radio"], input[type="checkbox"]')) return;
    const question = event.target.closest(".question[data-field-id]");
    if (!question) return;
    question.dataset.userTouched = "true";
    const values = collectValues();
    for (const field of form.querySelectorAll(".question[data-user-touched='true']")) {
      if (!Object.hasOwn(values, field.dataset.fieldId)) {
        values[field.dataset.fieldId] = field.dataset.fieldType === "checkbox" ? [] : "";
      }
    }
    writeDrafts(activeReport.id, values);
  };
  listen(form, "input", saveOwnerDraft);
  listen(form, "click", saveOwnerDraft);

  listen(form, "submit", async (event) => {
    event.preventDefault();
    if (pendingReportSubmissions.has(activeReport.id)) return;
    pendingReportSubmissions.add(activeReport.id);
    for (const button of [submitBtn, submitFloat]) {
      if (!button) continue;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    try {
      const values = collectValues(true);
      const draftsAtSubmit = JSON.stringify(readDrafts());
      await handlers.onSubmitReportAnswer(activeReport.id, values);
      if (JSON.stringify(readDrafts()) === draftsAtSubmit) clearDrafts(activeReport.id);
    } catch {
      // The handler displays the failure and leaves drafts intact.
    } finally {
      pendingReportSubmissions.delete(activeReport.id);
      if (container.querySelector("#report-answer-form")?.dataset.reportId === activeReport.id) {
        for (const selector of ["#report-submit", "#report-submit-float"]) {
          const button = container.querySelector(selector);
          if (!button) continue;
          button.disabled = false;
          button.removeAttribute("aria-busy");
        }
      }
    }
  });
}
