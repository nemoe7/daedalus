// Messages tab view for Arena Steering.

import {
  escapeHtml,
  hydrateMermaidBlocks,
  renderMarkdownBlock,
} from "./markdown.js";
import { relativeTime } from "./timestamps.js";
import { createMarkdownBatch } from "./markdown-dom-cache.js";
import { reconcileElement, renderAnswerBlock, renderParentChip } from "./dom.js";
import { readComposerDraft, writeComposerDraft } from "./state.js";

import {
  ARROW_DOWN_SVG,
  ARROW_UP_SVG as SEND_SVG,
  CHECK_SVG,
  COLLAPSE_SVG,
  EXPAND_SVG,
  EYE_CLOSED_SVG,
  EYE_OPEN_SVG,
  FILE_SVG,
  MARKDOWN_SVG,
  PAPERCLIP_SVG,
  SENDING_SVG,
  SKIP_FORWARD_SVG,
} from "./icons.js";

const AGENT_QUIET_MS = 180_000;
const AGENT_CALL_MS = 1_920_000;
const TOOL_LABELS = {
  read: "Read",
  edit: "Edit",
  write: "Write",
  bash: "Bash",
  commit: "Commit",
  push: "Push",
  inbox: "Inbox",
  run: "Run",
  task: "Task",
  "task-mod": "M Task",
  "task-finish": "F Task",
  pub: "Pub",
};

function connectionStamp(value) {
  if (typeof value !== "string" || !value) return null;
  const stamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value) ? `${value}Z` : value;
  const parsed = Date.parse(stamp);
  return Number.isFinite(parsed) ? parsed : null;
}

export function connectionPresentation(connection = {}, now = Date.now()) {
  if (connection.error) return { state: "down", label: "Disconnected", text: "Connection failed" };
  if (!connection.loaded) return { state: "connecting", label: "Connecting", text: "Connecting…" };
  if (connection.polling) {
    const since = connectionStamp(connection.polling_since);
    const seconds = since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
    const duration = seconds >= 60 ? `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s` : `${seconds}s`;
    return { state: "polling", label: "Connected", text: `Polling… ${duration}` };
  }
  const ended = connectionStamp(connection.turn_ended_at);
  const seen = connectionStamp(connection.agent_seen_at);
  const idle = ended !== null ? now - ended > AGENT_QUIET_MS : seen !== null && now - seen > AGENT_CALL_MS;
  if (idle) return { state: "idle", label: "No agent", text: "Agent 404" };
  // A live connection with no socket names the poll, so the loss shows in the pill.
  if (connection.transport?.mode === "poll" && !connection.transport.socket) {
    return { state: "ok", label: "Connected (poll)", text: "Connected (poll)" };
  }
  // A transport that opened and then died is a fault, so it shows amber.
  if (connection.transport?.mode === "ws" && connection.transport.attempted &&
      !connection.transport.socket && !connection.transport.poll) {
    return { state: "fault", label: "No transport", text: "Something went wrong" };
  }
  return { state: "ok", label: "Connected", text: "Connected" };
}

export function paintConnection(container, connection) {
  const dot = container?.querySelector("#connection-dot");
  const text = container?.querySelector("#connection-text");
  if (!dot || !text) return;
  const current = connectionPresentation(connection);
  dot.dataset.state = current.state;
  if (current.label === current.text) {
    dot.setAttribute("aria-hidden", "true");
    dot.removeAttribute("role");
    dot.removeAttribute("aria-label");
  } else {
    dot.removeAttribute("aria-hidden");
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", current.label);
  }
  text.textContent = current.text;
}

const MESSAGE_PAGE_SIZE = 15;
const OLDER_PAGE_TRIGGER_PX = 320;
const PASTE_ATTACHMENT_CHAR_THRESHOLD = 1500;
const PASTE_ATTACHMENT_LINE_THRESHOLD = 15;
let unreadEditedMsgId = null;
let savedScrollTop = null;
let pinnedToBottom = true;
let skipPollFeedbackUntil = 0;
let skipPollPending = false;
let skipPollFailure = null;
let visibleMessageCount = MESSAGE_PAGE_SIZE;

export function expandMessageHistory() {
  visibleMessageCount = Infinity;
  pinnedToBottom = false;
}
let loadingMoreMessages = null;
let draftRevision = 0;
let draftPreviewActive = false;
let draftPreviewSequence = 0;
let draftPreviewCache = { text: null, html: "" };
let dropAreaAbort = null;
let viewEventAbort = null;
let replyObserver = null;
let historyResizeObserver = null;
let historyScrollTarget = null;
let historyScrollHandler = null;
const renderedMessageSignatures = new WeakMap();
const pendingReplyReceipts = new Set();
const optimisticReplyRevisions = new Map();
let latestTrackedMessages = [];

function isReplySeen(msg) {
  return Boolean(msg.replies_seen) ||
    (Boolean(msg.replies_revision) && optimisticReplyRevisions.get(msg.id) === msg.replies_revision);
}

if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !replyObserver) return;
  for (const target of document.querySelectorAll(".reply-seen-sentinel")) {
    replyObserver.unobserve(target);
    replyObserver.observe(target);
  }
});

function attachmentState(item) {
  if (item.status === "uploading") return "uploading…";
  if (item.status === "failed") return "upload failed";
  const size = Number(item.file?.size || 0);
  if (size < 1000) return `${size} B`;
  if (size < 1000000) return `${Math.round(size / 1000)} kB`;
  return `${(size / 1000000).toFixed(2)} MB`;
}

function renderAttachmentName(name) {
  const text = String(name || "");
  const dot = text.lastIndexOf(".");
  const base = dot > 0 ? text.slice(0, dot) : text;
  const extension = dot > 0 ? text.slice(dot) : "";
  const chars = Array.from(base);
  const tail = chars.splice(Math.max(0, chars.length - 3)).join("") + extension;
  return `<span class="attachment-name-head">${escapeHtml(chars.join(""))}</span><span class="attachment-name-tail">${escapeHtml(tail)}</span>`;
}

function renderComposerAttachment(item, sending) {
  const name = escapeHtml(item.name || "attachment");
  const lead = item.previewUrl
    ? `<img class="composer-attachment-lead" src="${escapeHtml(item.previewUrl)}" alt="${name}" width="32" height="32">`
    : `<span class="composer-attachment-lead" aria-hidden="true">${FILE_SVG}</span>`;
  return `<span class="composer-attachment-chip" data-upload-status="${escapeHtml(item.status)}">
    ${lead}
    <span class="composer-attachment-name" title="${name}">${name} · ${escapeHtml(attachmentState(item))}</span>
    <button type="button" data-remove-composer-attachment="${escapeHtml(item.id)}" aria-label="Remove ${name}" ${sending ? "disabled" : ""}>×</button>
  </span>`;
}



export function messageQueryKey(state) {
  return JSON.stringify([Boolean(state.showHidden), (state.searchQuery || "").trim()]);
}

export function trackMessageAcks(messages) {
  latestTrackedMessages = Array.isArray(messages) ? messages : [];
  for (const msg of latestTrackedMessages) {
    if (msg.replies_seen && optimisticReplyRevisions.get(msg.id) === msg.replies_revision) {
      optimisticReplyRevisions.delete(msg.id);
    }
  }
  const unseen = latestTrackedMessages.filter((msg) => (msg.acks || []).length && !isReplySeen(msg));
  unreadEditedMsgId = unseen.at(-1)?.id || null;
}

function syncUnreadEditedButton(root = typeof document !== "undefined" ? document : null) {
  trackMessageAcks(latestTrackedMessages);
  const button = root?.querySelector?.("#log-edited");
  if (button) button.hidden = !unreadEditedMsgId;
}

function highlightSearchMatches(root, rawQuery) {
  const query = (rawQuery || "").trim();
  if (!root || !query) return;
  const lowerQuery = query.toLowerCase();
  const qLen = lowerQuery.length;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (
        parent.closest("script, style, svg, .mermaid, mark.search-hit") ||
        !parent.closest(".message-text, code.msg-id")
      ) {
        return NodeFilter.FILTER_REJECT;
      }
      return (node.nodeValue || "").toLowerCase().includes(lowerQuery)
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_SKIP;
    },
  });
  const textNodes = [];
  let current = walker.nextNode();
  while (current) {
    textNodes.push(current);
    current = walker.nextNode();
  }
  for (const textNode of textNodes) {
    const text = textNode.nodeValue || "";
    const lower = text.toLowerCase();
    const frag = document.createDocumentFragment();
    let cursor = 0;
    let idx = lower.indexOf(lowerQuery, cursor);
    while (idx !== -1) {
      if (idx > cursor) {
        frag.appendChild(document.createTextNode(text.slice(cursor, idx)));
      }
      const mark = document.createElement("mark");
      mark.className = "search-hit";
      mark.textContent = text.slice(idx, idx + qLen);
      frag.appendChild(mark);
      cursor = idx + qLen;
      idx = lower.indexOf(lowerQuery, cursor);
    }
    if (cursor < text.length) {
      frag.appendChild(document.createTextNode(text.slice(cursor)));
    }
    textNode.parentNode?.replaceChild(frag, textNode);
  }
}

// Keep the scroll container and unchanged message cards connected while new pages
// arrive. Replacing them interrupts a scrollbar drag or an ongoing touch gesture.
function updateHistory(history, nextHistory, signatures, confirmation) {
  const existing = new Map(
    Array.from(history.querySelectorAll(":scope > .message"), (node) => [node.dataset.msgId, node])
  );
  const nextChildren = Array.from(nextHistory.children);
  for (let i = 0; i < 2; i += 1) {
    reconcileElement(history.children[i], nextChildren[i]);
  }
  const wanted = nextChildren.slice(2).map((node) => {
    const signature = signatures.get(node.dataset.msgId);
    let old = existing.get(node.dataset.msgId);
    const pending = !old && confirmation?.serverId === node.dataset.msgId
      ? existing.get(confirmation.pendingId) : null;
    if (pending) {
      pending.dataset.msgId = node.dataset.msgId;
      reconcileElement(pending, node);
      old = pending;
    }
    const use = pending || (old && renderedMessageSignatures.get(old) === signature ? old : node);
    // A transient highlight outlives the render that follows a reference jump.
    if (old && use !== old && old.getAttribute("data-highlight")) {
      use.setAttribute("data-highlight", "true");
    }
    if (old && use === node) {
      const previous = Array.from(old.querySelectorAll("[data-markdown-key]"));
      for (const next of node.querySelectorAll("[data-markdown-key]")) {
        const index = previous.findIndex(item => item.dataset.markdownKey === next.dataset.markdownKey);
        if (index !== -1) next.replaceWith(previous.splice(index, 1)[0]);
      }
    }
    renderedMessageSignatures.set(use, signature);
    return use;
  });
  const retained = new Set(wanted);
  for (const old of existing.values()) {
    if (!retained.has(old)) old.remove();
  }
  wanted.forEach((node, index) => {
    const current = history.children[index + 2] || null;
    if (node !== current) history.insertBefore(node, current);
  });
}

export function renderMessagesView(container, state, handlers) {
  viewEventAbort?.abort();
  viewEventAbort = new AbortController();
  const signal = viewEventAbort.signal;
  const listen = (target, type, callback, options = {}) =>
    target?.addEventListener(type, callback, { ...options, signal });
  if (historyScrollTarget && historyScrollHandler) {
    historyScrollTarget.removeEventListener("scroll", historyScrollHandler);
  }
  replyObserver?.disconnect();
  replyObserver = null;
  historyResizeObserver?.disconnect();
  historyResizeObserver = null;
  const activeEl = document.activeElement;
  const searchWasFocused =
    Boolean(activeEl && activeEl.id === "log-search" && container.contains(activeEl));
  const searchSelStart = searchWasFocused ? activeEl.selectionStart : null;
  const searchSelEnd = searchWasFocused ? activeEl.selectionEnd : null;
  const prevMsgEl = container.querySelector("#msg");
  const draftWasFocused = Boolean(prevMsgEl && activeEl === prevMsgEl);
  const draftValue = prevMsgEl?.value ?? readComposerDraft();
  if (!draftValue.trim()) draftPreviewActive = false;
  const draftSelStart = draftWasFocused ? prevMsgEl.selectionStart : null;
  const draftSelEnd = draftWasFocused ? prevMsgEl.selectionEnd : null;

  let anchorMsgId = null;
  let anchorOffsetTop = 0;
  const prevHistory = container.querySelector("#history");
  if (prevHistory) {
    const dist =
      prevHistory.scrollHeight -
      prevHistory.scrollTop -
      prevHistory.clientHeight;
    pinnedToBottom = dist <= 24;
    savedScrollTop = prevHistory.scrollTop;
    const historyRect = prevHistory.getBoundingClientRect();
    const visibleCards = prevHistory.querySelectorAll(".message:not([data-hidden='true'])");
    for (let i = visibleCards.length - 1; i >= 0; i -= 1) {
      const card = visibleCards[i];
      const rect = card.getBoundingClientRect();
      if (rect.bottom > historyRect.top + 40 && rect.top < historyRect.bottom - 40) {
        anchorMsgId = card.dataset.msgId || null;
        anchorOffsetTop = rect.top - historyRect.top;
        break;
      }
    }
  }

  const showHidden = Boolean(state.showHidden);
  const query = (state.searchQuery || "").trim().toLowerCase();
  const selectedParentRefs = Array.isArray(state.replyParentRefs)
    ? state.replyParentRefs
    : [];
  const isParentSelected = (type, id) =>
    selectedParentRefs.some((ref) => ref.type === type && ref.id === id);
  const sentAttachmentIds = new Set(state.optimisticMessage?.attachmentIds || []);
  const composerAttachments = Array.isArray(state.composerAttachments)
    ? state.composerAttachments.filter(item => !sentAttachmentIds.has(item.id))
    : [];
  const composerExpanded = Boolean(state.composerExpanded);
  const skipPollArmed = Boolean(state.skipPollArmed);
  const skipPollLabel = skipPollArmed
    ? "Skip is armed. Press to cancel it."
    : "Ask the agent to end its wait now";
  const skipPollGood = Date.now() < skipPollFeedbackUntil;
  const skipPollBad = skipPollFailure && Date.now() < skipPollFailure.until;

  const optimistic = state.optimisticMessage;
  const messages = state.messages || [];
  trackMessageAcks(messages.filter((msg) => showHidden || !msg.hidden));
  const optimisticMatches = optimistic && (!query || [optimistic.body, ...(optimistic.parent_ids || []), ...(optimistic.parent_refs || []).map(ref => ref.id)].some(value => String(value || "").toLowerCase().includes(query)));
  const alreadyVisible = optimistic?.serverId && messages.some(message => message.id === optimistic.serverId);
  const visibleMessages = optimisticMatches && !alreadyVisible ? [...messages, optimistic] : messages;
  const placeholder = (visibleMessages.at(-1)?.body || "What should happen next?").split(/\r?\n/, 1)[0];
  const hasOlderMessages = Boolean(state.messagesHasMore);

  const linkedTasksByMsg = new Map();
  for (const task of state.tasks || []) {
    const msgIds = task.links?.msg_ids || task.msg_ids || [];
    for (const mid of msgIds) {
      if (!linkedTasksByMsg.has(mid)) {
        linkedTasksByMsg.set(mid, []);
      }
      linkedTasksByMsg.get(mid).push(task.id);
    }
  }

  const markdownBatch = createMarkdownBatch();
  const messageSignatures = new Map();
  const historyHtml = visibleMessages.length
    ? visibleMessages
        .map((msg) => {
          const acks = msg.acks || [];
          const acksHtmlList = msg.acks_html || [];
          const bodyClass = /\S[ \t]*\r?\n[ \t]*\S/.test(msg.body || "")
            ? " message-text-multiline"
            : "";
          const acksHtml = acks.length
            ? acks
                .map(
                  (_, idx) =>
                    renderAnswerBlock(
                      markdownBatch.slot(acksHtmlList[idx] ?? '<p role="alert">Rendered acknowledgement unavailable.</p>', idx > 0 ? "reply-block" : "", query),
                      { first: idx === 0 }
                    )
                )
                .join("")
            : "";
          const statusText = msg.optimistic ? "Sending" : msg.status === "sent" ? "Sent" : msg.status === "seen" ? "Seen" : "";
          const stateLabel = escapeHtml(`${msg.hidden ? "Hidden message. " : ""}Status: ${msg.optimistic ? "sending" : msg.status}`);
          const iso = escapeHtml(msg.modified_at || "");
          const rel = escapeHtml(relativeTime(msg.modified_at));
          const attachments = Array.isArray(msg.attachments)
            ? msg.attachments
            : [];
          const filesSuffix = attachments.length
            ? `<span class="receipt-file">${attachments
                .map((att) => {
                  const name = escapeHtml(att.name || "");
                  const isLost = Boolean(att.lost) || att.present === false;
                  const preview = !isLost && typeof att.url === "string"
                    && att.url.startsWith("/static/uploads/")
                    && /\.(?:png|jpe?g|gif|webp|avif|bmp|svg)$/i.test(String(att.name || ""));
                  const displayName = renderAttachmentName(att.name);
                  if (preview) {
                    return `<button class="message-attachment-preview" type="button" aria-label="View image ${name}"><img src="${escapeHtml(att.url)}" alt="" loading="lazy" decoding="async"><span class="message-attachment-name">${displayName}</span></button>`;
                  }
                  return `<span class="message-attachment-file"><span class="message-attachment-file-icon" aria-hidden="true">${FILE_SVG}</span><span class="receipt-filename"><span class="message-attachment-name">${displayName}</span>${isLost ? " (lost)" : ""}</span></span>`;
                })
                .join("")}</span>`
            : "";
          const linkedTasks = linkedTasksByMsg.get(msg.id) || [];
          const tasksSuffix = linkedTasks.length
            ? `<span class="receipt-task-links">${linkedTasks.map((tid) => renderParentChip("task", tid)).join(" ")}</span>`
            : "";
          const parentRefs = Array.isArray(msg.parent_refs) && msg.parent_refs.length
            ? msg.parent_refs
            : (msg.parent_ids || []).map((id) => ({ type: "message", id }));
          const parentsSuffix = parentRefs.length
            ? `<span class="message-parents" aria-label="Message parents">${parentRefs.map((ref) => renderParentChip(ref.type, ref.id)).join(" ")}</span>`
            : "";
          const replySelected = isParentSelected("message", msg.id);
          messageSignatures.set(msg.id, JSON.stringify({ msg, linkedTasks, selectedParentRefs, query }));

          return `
            <li class="message" data-msg-id="${escapeHtml(msg.id)}" data-hidden="${msg.hidden ? "true" : "false"}">
              ${filesSuffix ? `<div class="message-attachments">${filesSuffix}</div>` : ""}
              <div class="message-heading">
                ${parentsSuffix}
                ${msg.optimistic ? `<code class="msg-id" aria-label="Pending message ID">xxxxxx</code>` : `<button type="button" class="msg-id receipt-id parent-id-button" data-parent-type="message" data-parent-id="${escapeHtml(msg.id)}" aria-label="Message ${escapeHtml(msg.id)} as message parent" aria-pressed="${replySelected ? "true" : "false"}"><code class="msg-id">${escapeHtml(msg.id)}</code></button>`}
              </div>
              <div class="message-bubble">
                ${markdownBatch.slot(msg.optimistic ? renderMarkdownBlock(msg.body) : (msg.body_html ?? '<p role="alert">Rendered message unavailable.</p>'), `message-text${bodyClass}`, query)}
                ${acksHtml}
              </div>
              ${(acks.length || Boolean(msg.hidden && msg.body)) && !msg.replies_seen ? `<span class="reply-seen-sentinel" data-reply-id="${escapeHtml(msg.id)}" data-revision="${escapeHtml(msg.replies_revision || "")}" aria-hidden="true"></span>` : ""}
              <p class="receipt">
                ${tasksSuffix}
                <time class="receipt-bottom receipt-status" role="img" aria-label="${stateLabel}, ${rel}" data-state-label="${stateLabel}" data-prefix="${statusText}" datetime="${iso}" data-timestamp="${iso}">${statusText ? `${statusText} ` : ""}${rel}</time>
              </p>
            </li>
          `;
        })
        .join("")
    : "";

  const survey = state.survey || {};
  const checkedAt = survey.checked_at || "";
  const lastTool = state.lastTool || null;
  const toolLabel = TOOL_LABELS[lastTool?.tool] || "";
  const toolAt = typeof lastTool?.at === "string" ? lastTool.at : "";
  const rawCalls = Number(state.callsSinceMessage);
  const calls = Number.isFinite(rawCalls) ? Math.max(0, Math.floor(rawCalls)) : 0;
  const tally = lastTool?.tool === "bash" && calls > 0 ? ` ${calls}` : "";
  const lastCheckHtml = toolLabel && toolAt
    ? `${toolLabel}${tally} <time datetime="${escapeHtml(toolAt)}" data-timestamp="${escapeHtml(toolAt)}" data-include-seconds="true" data-compact="true">${escapeHtml(relativeTime(toolAt, { compact: true, includeSeconds: true }))}</time>`
    : checkedAt
      ? `Checked <time datetime="${escapeHtml(checkedAt)}" data-timestamp="${escapeHtml(checkedAt)}">${escapeHtml(relativeTime(checkedAt))}</time>`
      : "";

  const markup = `
    <div class="msgs-layout">
      <section class="card log-card" aria-label="Message log">
        <div class="row log-toolbar">
          <div class="stack">
            <p id="connection" role="status">
              <button id="survey-open" class="quiet-btn" type="button" aria-label="Open survey results" title="Open survey results">
                <span id="connection-dot" class="dot" data-state="connecting" aria-hidden="true"></span>
              </button>
              <span id="connection-text">Connecting…</span>
            </p>
            <p id="last-check" class="muted">${lastCheckHtml}</p>
          </div>
          <span class="row tight">
            <input
              id="log-search"
              type="search"
              value="${escapeHtml(state.searchQuery || "")}"
              placeholder="Search messages"
              aria-label="Search message log"
            />
            <button
              id="skip-poll"
              data-armed="${skipPollArmed}"
              class="icon-button quiet-btn"
              type="button"
              ${skipPollGood ? 'data-state="good"' : skipPollBad ? 'data-state="bad"' : ""}
              ${skipPollPending ? 'disabled aria-busy="true"' : ""}
              aria-label="${escapeHtml(skipPollBad ? skipPollFailure.message : skipPollLabel)}"
              title="${escapeHtml(skipPollBad ? skipPollFailure.message : skipPollLabel)}"
            >
              ${skipPollGood ? CHECK_SVG : SKIP_FORWARD_SVG}
            </button>
            <button
              id="quiet-msgs"
              class="icon-button quiet-btn"
              type="button"
              aria-pressed="${showHidden ? "true" : "false"}"
              aria-label="${showHidden ? "Hide hidden messages" : "Show hidden messages"}"
              title="${showHidden ? "Hide hidden messages" : "Show hidden messages"}"
            >
              ${showHidden ? EYE_OPEN_SVG : EYE_CLOSED_SVG}
            </button>
          </span>
        </div>
        <div class="log-scroll">
          <button id="log-edited" class="scroll-jump-btn log-edited" type="button" aria-live="polite" ${unreadEditedMsgId ? "" : "hidden"}>New reply</button>
          <ol id="history" aria-label="Messages, oldest first">
            <li ${hasOlderMessages ? "" : "hidden"}><button id="load-older-messages" type="button">Show older messages</button></li>
            <li role="alert" ${state.messagesError ? "" : "hidden"}>${escapeHtml(state.messagesError || "")}</li>
            ${historyHtml}
          </ol>
          <button id="log-newest" class="scroll-jump-btn log-newest" type="button" aria-label="Scroll to bottom" hidden>
            ${ARROW_DOWN_SVG}
          </button>
        </div>
        <p id="log-empty" class="receipt" role="status" ${visibleMessages.length || state.messagesError || state.messagesLoading ? "hidden" : ""}>${query ? "No matching messages." : "No messages yet."}</p>
      </section>

      <section class="card compose ${composerExpanded ? "composer-expanded" : ""}" id="compose" aria-label="Send message" ${composerAttachments.length ? 'data-attachments="true"' : ""}>
        <form id="form">
          <div
            id="composer-reply-bar"
            class="composer-reply-bar"
            aria-label="Selected parents"
            ${selectedParentRefs.length ? "" : "hidden"}
          >
            ${selectedParentRefs
              .map(
                (ref) =>
                  `<button type="button" class="composer-reply-chip" data-parent-type="${escapeHtml(ref.type)}" data-parent-id="${escapeHtml(ref.id)}" aria-label="Remove ${escapeHtml(ref.type)} ${escapeHtml(ref.id)} as parent"><code>${escapeHtml(ref.id)}</code></button>`
              )
              .join("")}
          </div>
          ${state.composerError ? `<div class="composer-send-status" role="status">${escapeHtml(state.composerError)}</div>` : ""}
          <div class="composer-shell">
            <div
              id="composer-attachments"
              class="composer-attachments"
              aria-label="Staged attachments"
              ${composerAttachments.length ? "" : "hidden"}
            >${composerAttachments.map((item) => renderComposerAttachment(item, state.sending)).join("")}</div>
            <button
              id="attach-file"
              class="icon-button quiet-btn"
              type="button"
              aria-label="Attach file"
              title="Attach file"
              ${state.sending ? "disabled" : ""}
            >
              ${PAPERCLIP_SVG}
            </button>
            <input id="composer-file-input" type="file" multiple hidden />
            <button
              id="preview-msg"
              class="icon-button quiet-btn"
              type="button"
              aria-pressed="${draftPreviewActive ? "true" : "false"}"
              aria-controls="draft-preview"
              aria-label="Preview Markdown"
              title="Preview Markdown"
            >
              ${MARKDOWN_SVG}
            </button>
            <textarea
              id="msg"
              name="msg"
              rows="1"
              aria-label="Your message"
              placeholder="${escapeHtml(placeholder)}"
              ${draftPreviewActive ? "hidden" : ""}
            ></textarea>
            <div
              id="draft-preview"
              class="message-text draft-preview"
              tabindex="0"
              role="region"
              aria-label="Markdown draft preview, not sent"
              ${draftPreviewActive ? "" : "hidden"}
            >${draftPreviewActive && draftPreviewCache.text === draftValue ? draftPreviewCache.html : ""}</div>
            <button
              id="composer-expand"
              class="icon-button quiet-btn"
              type="button"
              aria-expanded="${composerExpanded ? "true" : "false"}"
              aria-label="${composerExpanded ? "Collapse composer" : "Expand composer"}"
              title="${composerExpanded ? "Collapse composer" : "Expand composer"}"
              aria-controls="msg draft-preview"
              ${composerExpanded ? "" : "hidden"}
            >
              ${composerExpanded ? COLLAPSE_SVG : EXPAND_SVG}
            </button>
            <button
              id="send"
              class="icon-button quiet-btn"
              type="submit"
              aria-label="${state.sending ? "Sending message" : "Send message"}"
              title="${state.sending ? "Sending message" : "Send message"}"
              aria-busy="${state.sending ? "true" : "false"}"
              ${state.sending ? "disabled" : ""}
            >
              ${state.sending ? SENDING_SVG : SEND_SVG}
            </button>
          </div>
        </form>
      </section>
    </div>
  `;

  let reusedHistory = false;
  if (prevHistory) {
    const template = document.createElement("template");
    template.innerHTML = markup;
    markdownBatch.mount(template.content);
    const nextHistory = template.content.querySelector("#history");
    const replaceSelectors = [".log-toolbar", "#log-edited", "#log-newest", "#log-empty", "#compose"];
    const canUpdate = nextHistory && replaceSelectors.every(
      (selector) => container.querySelector(selector) && template.content.querySelector(selector)
    );
    if (canUpdate) {
      for (const selector of replaceSelectors) {
        reconcileElement(container.querySelector(selector), template.content.querySelector(selector));
      }
      const confirmation = optimistic?.serverId
        ? { pendingId: optimistic.id, serverId: optimistic.serverId }
        : state.optimisticConfirmation;
      updateHistory(prevHistory, nextHistory, messageSignatures, confirmation);
      reusedHistory = true;
    } else {
      container.replaceChildren(template.content);
    }
  } else {
    container.innerHTML = markup;
    markdownBatch.mount(container);
  }
  if (!reusedHistory) {
    container.querySelectorAll("#history > .message").forEach((node) => {
      renderedMessageSignatures.set(node, messageSignatures.get(node.dataset.msgId));
    });
  }

  listen(container.querySelector("#load-older-messages"), "click", () => handlers.onLoadOlderMessages?.());
  const historyEl = container.querySelector("#history");
  const logNewestBtn = container.querySelector("#log-newest");
  const logEditedBtn = container.querySelector("#log-edited");

  function syncScrollEdges() {
    const dist =
      historyEl.scrollHeight - historyEl.scrollTop - historyEl.clientHeight;
    historyEl.toggleAttribute("data-scrolled-start", historyEl.scrollTop > 0);
    historyEl.toggleAttribute("data-scrolled-end", dist > 1);
    return dist;
  }

  function syncMessageWidths() {
    for (const message of historyEl.querySelectorAll(":scope > .message")) {
      const bubble = message.querySelector(".message-bubble");
      if (!bubble) continue;
      const ackExpansionCap = 900 * 2 / 3;
      message.toggleAttribute(
        "data-message-wide",
        bubble.getBoundingClientRect().width > ackExpansionCap + 1
      );
    }
  }

  function syncScrollControls() {
    if (!historyEl) return;
    const dist = syncScrollEdges();
    pinnedToBottom = dist <= 24;
    savedScrollTop = historyEl.scrollTop;
    if (logNewestBtn) {
      logNewestBtn.hidden = pinnedToBottom;
    }
    const key = messageQueryKey(state);
    if (hasOlderMessages && !state.messagesError && loadingMoreMessages?.key !== key && historyEl.scrollTop <= OLDER_PAGE_TRIGGER_PX && historyEl.scrollHeight > historyEl.clientHeight) {
      const request = { key };
      loadingMoreMessages = request;
      Promise.resolve(handlers.onLoadOlderMessages?.()).finally(() => {
        if (loadingMoreMessages === request) loadingMoreMessages = null;
      });
    }
  }

  if (historyEl) {
    if (query) {
      highlightSearchMatches(historyEl, state.searchQuery);
    }
    syncMessageWidths();
    if (pinnedToBottom || savedScrollTop === null) {
      historyEl.scrollTop = historyEl.scrollHeight;
    } else {
      historyEl.scrollTop = savedScrollTop;
      if (anchorMsgId) {
        const anchorCard = historyEl.querySelector(
          `.message[data-msg-id="${CSS.escape(anchorMsgId)}"]`
        );
        if (anchorCard) {
          const historyRect = historyEl.getBoundingClientRect();
          const curOffset = anchorCard.getBoundingClientRect().top - historyRect.top;
          historyEl.scrollTop += curOffset - anchorOffsetTop;
        }
      }
    }
    syncScrollControls();
    historyScrollTarget = historyEl;
    historyScrollHandler = syncScrollControls;
    historyEl.addEventListener("scroll", historyScrollHandler, { passive: true });
    if (typeof ResizeObserver === "function") {
      historyResizeObserver = new ResizeObserver(() => {
        syncMessageWidths();
        if (pinnedToBottom) historyEl.scrollTop = historyEl.scrollHeight;
        syncScrollEdges();
      });
      historyResizeObserver.observe(historyEl);
      historyEl.querySelectorAll(".message-bubble").forEach((bubble) => historyResizeObserver.observe(bubble));
    }
  }

  if (historyEl && typeof IntersectionObserver === "function") {
    replyObserver = new IntersectionObserver((entries) => {
      if (document.visibilityState !== "visible" || container.hidden) return;
      let changed = false;
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const id = entry.target.dataset.replyId;
        const revision = entry.target.dataset.revision;
        const key = `${id}:${revision}`;
        if (!id || !revision || pendingReplyReceipts.has(key)) continue;
        pendingReplyReceipts.add(key);
        optimisticReplyRevisions.set(id, revision);
        changed = true;
        Promise.resolve(handlers.onMarkRepliesSeen?.(id, revision))
          .then(() => {
            if (handlers.onMarkRepliesSeen && optimisticReplyRevisions.get(id) === revision) {
              optimisticReplyRevisions.delete(id);
            }
            syncUnreadEditedButton(container.isConnected ? container : document);
          })
          .catch(() => {
            if (optimisticReplyRevisions.get(id) === revision) {
              optimisticReplyRevisions.delete(id);
            }
            syncUnreadEditedButton(container.isConnected ? container : document);
          })
          .finally(() => pendingReplyReceipts.delete(key));
      }
      if (changed) syncUnreadEditedButton(container);
    }, { root: historyEl });
    historyEl.querySelectorAll(".reply-seen-sentinel").forEach((el) => replyObserver.observe(el));
  }

  const searchInput = container.querySelector("#log-search");
  if (searchWasFocused && searchInput) {
    searchInput.focus();
    const len = searchInput.value.length;
    const start = searchSelStart ?? len;
    const end = searchSelEnd ?? len;
    try {
      searchInput.setSelectionRange(start, end);
    } catch {
      // Ignore if input type does not support selection range.
    }
  }

  listen(logNewestBtn, "click", () => {
    if (!historyEl) return;
    pinnedToBottom = true;
    historyEl.scrollTo({ top: historyEl.scrollHeight, behavior: "smooth" });
    logNewestBtn.hidden = true;
  });

  listen(logEditedBtn, "click", async () => {
    const targetId = unreadEditedMsgId;
    if (!targetId) return;
    await handlers.onNavigateReference?.("message", targetId);
    const root = container.isConnected ? container : document;
    const sentinel = root?.querySelector?.(
      `.message[data-msg-id="${CSS.escape(targetId)}"] .reply-seen-sentinel`
    );
    if (sentinel && replyObserver) {
      replyObserver.unobserve(sentinel);
      replyObserver.observe(sentinel);
    }
  });

  for (const chip of container.querySelectorAll(
    "[data-remove-composer-attachment]"
  )) {
    listen(chip, "click", () => {
      handlers.onRemoveComposerAttachment?.(
        chip.dataset.removeComposerAttachment
      );
    });
  }

  const attachBtn = container.querySelector("#attach-file");
  const fileInput = container.querySelector("#composer-file-input");
  listen(attachBtn, "click", () => {
    fileInput?.click();
  });
  listen(fileInput, "change", async () => {
    const files = Array.from(fileInput.files || []);
    fileInput.value = "";
    if (files.length) {
      await handlers.onStageComposerFiles?.(files);
    }
  });

  listen(container.querySelector("#composer-expand"), "click", () => {
    handlers.onToggleComposerExpanded?.();
    container.querySelector(draftPreviewActive ? "#draft-preview" : "#msg")?.focus();
  });

  listen(container.querySelector("#survey-open"), "click", (ev) => {
    handlers.onOpenSurvey?.(ev.currentTarget);
  });

  listen(searchInput, "input", () => {
    handlers.onSearchChange?.(searchInput.value);
  });

  const skipBtn = container.querySelector("#skip-poll");
  listen(skipBtn, "click", async () => {
    if (skipPollPending) return;
    skipPollPending = true;
    skipPollFailure = null;
    skipPollFeedbackUntil = Infinity;
    skipBtn.dataset.state = "good";
    skipBtn.innerHTML = CHECK_SVG;
    skipBtn.title = "Ask the agent to end its wait now";
    skipBtn.setAttribute("aria-label", "Ask the agent to end its wait now");
    skipBtn.disabled = true;
    skipBtn.setAttribute("aria-busy", "true");
    let error = null;
    try {
      await (skipPollArmed ? handlers.onCancelSkipPoll?.() : handlers.onSkipPoll?.());
    } catch (cause) {
      error = cause;
    }
    skipPollPending = false;
    skipPollFeedbackUntil = error ? 0 : Date.now() + 1000;
    if (error && error.message !== "Action canceled") {
      skipPollFailure = { message: `Could not end the wait: ${error.message || error}`, until: Date.now() + 1000 };
    }
    const currentButton = container.querySelector("#skip-poll");
    if (currentButton) {
      currentButton.disabled = false;
      currentButton.removeAttribute("aria-busy");
      if (error) {
        currentButton.innerHTML = SKIP_FORWARD_SVG;
        if (error.message === "Action canceled") delete currentButton.dataset.state;
        else {
          currentButton.dataset.state = "bad";
          currentButton.title = skipPollFailure.message;
          currentButton.setAttribute("aria-label", skipPollFailure.message);
        }
      }
    }
    setTimeout(() => {
      const curBtn = container.querySelector("#skip-poll");
      if (curBtn && !skipPollPending && Date.now() >= skipPollFeedbackUntil && (!skipPollFailure || Date.now() >= skipPollFailure.until)) {
        skipPollFailure = null;
        delete curBtn.dataset.state;
        curBtn.innerHTML = SKIP_FORWARD_SVG;
        const label = curBtn.dataset.armed === "true"
          ? "Skip is armed. Press to cancel it."
          : "Ask the agent to end its wait now";
        curBtn.title = label;
        curBtn.setAttribute("aria-label", label);
      }
    }, 1000);
  });

  listen(container.querySelector("#quiet-msgs"), "click", () => {
    handlers.onToggleHidden?.();
  });

  const msgEl = container.querySelector("#msg");
  if (msgEl && draftValue && !msgEl.value) {
    msgEl.value = draftValue;
    if (draftWasFocused) {
      msgEl.focus();
      try {
        msgEl.setSelectionRange(
          draftSelStart ?? draftValue.length,
          draftSelEnd ?? draftValue.length
        );
      } catch {
        // Ignore if textarea selection is unavailable.
      }
    }
  }
  const previewBtn = container.querySelector("#preview-msg");
  const draftPreview = container.querySelector("#draft-preview");
  const composeSection = container.querySelector("#compose");
  const expandBtn = container.querySelector("#composer-expand");
  let previewTooltipTimer = null;

  function clearPreviewTooltip() {
    clearTimeout(previewTooltipTimer);
    if (!previewBtn) return;
    delete previewBtn.dataset.tooltip;
    previewBtn.setAttribute("aria-label", "Preview Markdown");
    previewBtn.title = "Preview Markdown";
  }

  async function renderDraftPreview() {
    if (!draftPreview || !msgEl) return;
    const text = msgEl.value;
    if (draftPreviewCache.text === text) {
      draftPreview.innerHTML = draftPreviewCache.html;
      hydrateMermaidBlocks(draftPreview);
      return;
    }
    const sequence = ++draftPreviewSequence;
    draftPreview.textContent = "Rendering draft…";
    try {
      const result = await handlers.onRenderDraft(text);
      if (sequence !== draftPreviewSequence || !draftPreviewActive) return;
      const panel = container.querySelector("#draft-preview");
      if (!panel || container.querySelector("#msg")?.value !== text) return;
      // The renderer's newline after each break must not create a second visual line.
      const html = result.html.replace(/<br\s*\/?>\r?\n/gi, "<br>");
      draftPreviewCache = { text, html };
      panel.innerHTML = html;
      hydrateMermaidBlocks(panel);
    } catch (error) {
      if (sequence !== draftPreviewSequence || !draftPreviewActive) return;
      const panel = container.querySelector("#draft-preview");
      if (panel) panel.textContent = `Preview unavailable: ${error.message}. Use Write to continue, as sending still works.`;
    }
  }

  function growComposer() {
    if (!msgEl || !composeSection) return;
    const priorHistoryHeight = historyEl?.clientHeight || 0;
    const priorNoteHeight = parseFloat(msgEl.style.height) || 0;
    const wasMultiline = Boolean(composeSection.dataset.multiline);
    const hasAttachments = Boolean(composeSection.dataset.attachments);
    delete composeSection.dataset.multiline;
    if (!msgEl.value && composerExpanded) {
      handlers.onSetComposerExpanded?.(false);
      return;
    }
    msgEl.style.height = "auto";
    msgEl.style.overflowY = msgEl.value ? "auto" : "hidden";
    const css = window.getComputedStyle(msgEl);
    const single = parseFloat(css.lineHeight) + parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
    const multiline = Boolean(msgEl.value && (wasMultiline || draftPreviewActive || msgEl.value.includes("\n") || msgEl.scrollHeight > Math.ceil(single)));
    if (multiline) composeSection.dataset.multiline = "true";
    const border = parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth);
    const nextHeight = !msgEl.value || composerExpanded || msgEl.hidden ? 0 : msgEl.scrollHeight + border;
    msgEl.style.height = nextHeight ? `${nextHeight}px` : "";
    msgEl.style.overflowY = msgEl.scrollHeight > msgEl.clientHeight ? "auto" : "hidden";
    if (expandBtn) expandBtn.hidden = !multiline && !composerExpanded;
    const nextHistoryHeight = historyEl?.clientHeight || 0;
    const heightDrop = priorHistoryHeight > nextHistoryHeight
      ? priorHistoryHeight - nextHistoryHeight
      : nextHistoryHeight > 0
        ? Math.max(0, nextHeight - priorNoteHeight) + (!wasMultiline && multiline && !hasAttachments ? 38 : 0)
        : 0;
    if (historyEl && heightDrop > 0 && pinnedToBottom && !composerExpanded) {
      historyEl.scrollTop = historyEl.scrollHeight;
      syncScrollControls();
    }
  }

  listen(globalThis.window, "resize", growComposer);
  listen(msgEl, "input", () => {
    draftRevision += 1;
    writeComposerDraft(msgEl.value);
    clearPreviewTooltip();
    growComposer();
  });
  growComposer();
  if (draftPreviewActive && draftPreview) renderDraftPreview();

  listen(previewBtn, "click", () => {
    if (!draftPreview || !msgEl) return;
    if (!draftPreviewActive && !msgEl.value.trim()) {
      clearPreviewTooltip();
      previewBtn.setAttribute("aria-label", "Write something first");
      previewBtn.title = "Write something first";
      previewBtn.dataset.tooltip = "Write something first";
      previewTooltipTimer = setTimeout(clearPreviewTooltip, 1500);
      return;
    }
    clearPreviewTooltip();
    draftPreviewActive = !draftPreviewActive;
    previewBtn.setAttribute("aria-pressed", draftPreviewActive ? "true" : "false");
    draftPreview.hidden = !draftPreviewActive;
    msgEl.hidden = draftPreviewActive;
    growComposer();
    if (draftPreviewActive) renderDraftPreview();
    else {
      draftPreviewSequence += 1;
      msgEl.focus();
    }
  });

  let pasteAsText = false;
  listen(msgEl, "keydown", (event) => {
    pasteAsText = event.key.toLowerCase() === "v" && event.ctrlKey && event.shiftKey;
    if (
      event.key === "Enter" && !event.shiftKey && !event.isComposing &&
      !window.matchMedia?.("(pointer: coarse)")?.matches
    ) {
      event.preventDefault();
      container.querySelector("#form")?.requestSubmit();
    }
  });

  listen(msgEl, "keyup", () => { pasteAsText = false; });
  listen(msgEl, "focusout", () => { pasteAsText = false; });
  listen(msgEl, "paste", async (event) => {
    const plainPaste = pasteAsText;
    pasteAsText = false;
    if (plainPaste || state.sending) return;
    const files = Array.from(event.clipboardData?.files || []);
    const pastedText = event.clipboardData?.getData("text/plain") || "";
    if (files.length) {
      if (!pastedText) event.preventDefault();
      const baseMs = Date.now();
      const unnamedFiles = files.map((file, idx) => {
        const rawName = String(file?.name || "").trim();
        const dot = rawName.lastIndexOf(".");
        const nameExt = dot >= 0 && dot < rawName.length - 1
          ? rawName.slice(dot).replace(/[^A-Za-z0-9.]/g, "").slice(0, 20).toLowerCase()
          : "";
        const mime = String(file?.type || "").split(";", 1)[0].trim().toLowerCase();
        const mimeExt = {
          "image/png": ".png",
          "image/jpeg": ".jpg",
          "image/webp": ".webp",
          "image/gif": ".gif",
          "image/svg+xml": ".svg",
          "image/bmp": ".bmp",
          "image/avif": ".avif",
          "text/plain": ".txt",
          "text/markdown": ".md",
          "application/json": ".json",
          "application/pdf": ".pdf",
        }[mime] || ".bin";
        const ext = nameExt.length > 1 ? nameExt : mimeExt;
        return new File([file], `${baseMs + idx}${ext}`, { type: file.type });
      });
      await handlers.onStageComposerFiles?.(unnamedFiles);
      return;
    }
    const lineCount = pastedText.split(/\r\n|\r|\n/).length;
    if (
      Array.from(pastedText).length > PASTE_ATTACHMENT_CHAR_THRESHOLD ||
      lineCount >= PASTE_ATTACHMENT_LINE_THRESHOLD
    ) {
      event.preventDefault();
      const epochMs = Date.now();
      const pasteFile = new File(
        [pastedText],
        `${lineCount}-pasted-lines-${epochMs}.txt`,
        { type: "text/plain" },
      );
      await handlers.onStageComposerFiles?.([pasteFile]);
    }
  });

  const dropArea = container.querySelector(".msgs-layout");
  dropAreaAbort?.abort();
  if (dropArea) {
    dropAreaAbort = new AbortController();
    const listenerOptions = { signal: dropAreaAbort.signal };
    const showDrop = (event) => {
      if (state.sending || !event.dataTransfer?.types?.includes("Files")) return;
      event.preventDefault();
      composeSection?.classList.add("composer-drop-active");
    };
    dropArea.addEventListener("dragenter", showDrop, listenerOptions);
    dropArea.addEventListener("dragover", showDrop, listenerOptions);
    dropArea.addEventListener("dragleave", (event) => {
      if (!dropArea.contains(event.relatedTarget)) {
        composeSection?.classList.remove("composer-drop-active");
      }
    }, listenerOptions);
    dropArea.addEventListener("drop", async (event) => {
      const files = Array.from(event.dataTransfer?.files || []);
      if (state.sending || !files.length) return;
      event.preventDefault();
      composeSection?.classList.remove("composer-drop-active");
      await handlers.onStageComposerFiles?.(files);
    }, listenerOptions);
  }

  async function submitPayload(payload, submittedDraft) {
    const currentMsgEl = container.querySelector("#msg");
    const clearDraft = Boolean(submittedDraft) && (currentMsgEl ? currentMsgEl.value === submittedDraft : readComposerDraft() === submittedDraft);
    if (clearDraft) {
      writeComposerDraft("");
      draftPreviewActive = false;
      if (currentMsgEl) {
        currentMsgEl.value = "";
        currentMsgEl.hidden = false;
        const currentPreview = container.querySelector("#draft-preview");
        if (currentPreview) currentPreview.hidden = true;
        container.querySelector("#preview-msg")?.setAttribute("aria-pressed", "false");
        currentMsgEl.dispatchEvent(new Event("input", { bubbles: true }));
        if (!container.hidden && currentMsgEl.isConnected) currentMsgEl.focus();
      }
    }
    const clearedRevision = draftRevision;
    try {
      await handlers.onSendMessage(payload);
    } catch (error) {
      if (clearDraft && draftRevision === clearedRevision) {
        const input = container.querySelector("#msg");
        if (!input?.value && !readComposerDraft()) {
          writeComposerDraft(submittedDraft);
          if (input) {
            input.value = submittedDraft;
            input.dispatchEvent(new Event("input", { bubbles: true }));
          }
        }
      }
      console.error(error);
    }
  }

  const form = container.querySelector("#form");
  listen(form, "submit", (event) => {
    event.preventDefault();
    const submittedDraft = msgEl?.value || "";
    const text = submittedDraft.trim();
    if (!text && !composerAttachments.length) return;
    if (state.sending) return;
    const names = composerAttachments.map((item) => item.name);
    const body = text ? submittedDraft : (names.length === 1 ? `File: ${names[0]}` : `Files: ${names.join(", ")}`);
    pinnedToBottom = true;
    submitPayload({
      body,
      parent_ids: selectedParentRefs.filter((ref) => ref.type === "message").map((ref) => ref.id),
      parent_refs: selectedParentRefs.map((ref) => ({ ...ref })),
      attachments: composerAttachments.map((item) => item.attachment),
      attachmentIds: composerAttachments.map((item) => item.id),
      rawDraft: submittedDraft,
    }, submittedDraft);
  });
}
