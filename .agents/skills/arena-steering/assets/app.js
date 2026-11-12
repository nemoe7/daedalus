import { createComposerUploads } from "./composer-uploads.js";
import { copyText } from "./copy.js";
// Main ES module controller for Arena Steering.

import { api, connectWebSocket, transportState } from "./api.js";
import { expandMessageHistory, messageQueryKey, paintConnection, renderMessagesView, trackMessageAcks } from "./messages-view.js";
import {
  escapeHtml,
  highlightCodeBlocks,
  hydrateMermaidBlocks,
  toggleCodeMermaid,
} from "./markdown.js";
import { createModalManager } from "./modal.js";
import { renderReportsView } from "./reports-view.js";
import { createStore } from "./state.js";
import { expandTaskTarget, renderTasksView } from "./tasks-view.js";
import { mountRelativeTimers, relativeTime } from "./timestamps.js";

import {
  CHECK_14_SVG as CHECK_SVG,
  SURVEY_BAD_SVG,
  SURVEY_OK_SVG,
  SURVEY_WARN_SVG,
} from "./icons.js";
const CAPABILITY_LABELS = {
  R3S0N: "Reasoning",
  V1S0N: "Vision",
  S0R7D: "Sorted",
};

function formatSurveyFieldValue(val) {
  if (val && typeof val === "object" && !Array.isArray(val)) {
    const op = String(val.operator || "");
    if (op === "unknown") return "unknown";
    const symbol = op === "gt" ? ">" : op === "lt" ? "<" : "=";
    return `${symbol} ${val.value}`;
  }
  return String(val);
}

function renderAnswerTooltip(answer) {
  const entries = answer && typeof answer === "object" ? Object.entries(answer) : [];
  if (!entries.length) return "";
  return `<div class="survey-answer-tooltip" role="tooltip">${entries
    .map(
      ([k, v]) =>
        `<div class="survey-answer-row"><span class="survey-answer-key">${escapeHtml(k)}</span><span class="survey-answer-val">${escapeHtml(formatSurveyFieldValue(v))}</span></div>`,
    )
    .join("")}</div>`;
}

function renderSurveyModalContent(survey) {
  const results = Array.isArray(survey.results) ? survey.results : [];
  const checkedAt = survey.checked_at || survey.answered_at || "";
  if (results.length > 0) {
    const passed = results.filter((r) => r.correct === true).length;
    const capItems = results.filter((r) => r.id in CAPABILITY_LABELS);
    const calItems = results.filter((r) => !(r.id in CAPABILITY_LABELS));
    const subtitle = checkedAt
      ? `<p class="survey-subtitle muted">Answered <time datetime="${escapeHtml(checkedAt)}" data-timestamp="${escapeHtml(checkedAt)}">${escapeHtml(relativeTime(checkedAt))}</time> · ${passed} of ${results.length} verified</p>`
      : `<p class="survey-subtitle muted">${passed} of ${results.length} verified</p>`;
    const capHtml = capItems
      .map((item) => {
        const state = item.correct === true ? "ok" : item.correct === false ? "bad" : "warn";
        const icon = item.correct === true ? SURVEY_OK_SVG : item.correct === false ? SURVEY_BAD_SVG : SURVEY_WARN_SVG;
        const label = CAPABILITY_LABELS[item.id] || item.id;
        return `<div class="survey-cap-pill ${state}"><span class="survey-status ${state}">${icon}</span><div class="survey-cap-text"><code>${escapeHtml(item.id)}</code><span class="survey-cap-label">${escapeHtml(label)}</span></div></div>`;
      })
      .join("");
    const calRows = calItems
      .map((item) => {
        const state = item.correct === true ? "ok" : item.correct === false ? "bad" : "warn";
        const icon = item.correct === true ? SURVEY_OK_SVG : item.correct === false ? SURVEY_BAD_SVG : SURVEY_WARN_SVG;
        const pct = Math.max(0, Math.min(100, Math.round(Number(item.confidence || 0) * 100)));
        return `<tr>
          <td>
            <div class="survey-result-id">
              <span class="survey-status ${state}">${icon}</span>
              <span class="survey-id-wrap">
                <button type="button" class="survey-id-trigger quiet-btn"><code>${escapeHtml(item.id)}</code></button>
                ${renderAnswerTooltip(item.answer)}
              </span>
            </div>
          </td>
          <td>
            <div class="survey-confidence" style="--confidence-pct: ${pct}%">
              <div class="survey-confidence-track"><span class="survey-confidence-fill"></span></div>
              <span class="survey-confidence-value">${pct}%</span>
            </div>
          </td>
        </tr>`;
      })
      .join("");
    return `${subtitle}
      <div>
        <p class="eyebrow survey-section-label">Deterministic checks</p>
        <div class="survey-cap-grid">${capHtml}</div>
      </div>
      <div>
        <p class="eyebrow survey-section-label">Knowledge calibration</p>
        <table class="survey-results">
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Confidence</th>
            </tr>
          </thead>
          <tbody>${calRows}</tbody>
        </table>
      </div>`;
  }
  return `<p class="muted">No survey submission recorded yet.</p>`;
}

const store = createStore();
const modal = createModalManager();
const SURVEY_RELOAD_KEY = "steer-survey-open-on-reload";
let surveyOpen = false;
let surveyOpenRevision = 0;

function reloadPreservingSurvey() {
  if (store.getState().composerPersistenceError) {
    store.setState({ composerError: "Cannot reload safely: browser storage could not save selected parents or files." });
    return;
  }
  try {
    if (surveyOpen) sessionStorage.setItem(SURVEY_RELOAD_KEY, "1");
    else sessionStorage.removeItem(SURVEY_RELOAD_KEY);
  } catch {
    // Reload normally if session storage is unavailable.
  }
  window.location.reload();
}
const reportCache = new Map();
const pendingReportLoads = new Map();
const ackSeenRequests = new Set();
let cleanupTimers = () => {};
let knownAssetVersion = null;
let reportedTimezone = null;
let lastDataSignature = "";
let lastReportRenderSignature = null;
let lastViewportHeight = 0;
const MOBILE_KEYBOARD_MIN_SHRINK = 120;
const REPORT_VIEW_INTERVAL = 30_000;
let lastReportView = { id: null, at: 0 };
let reportViewTimer = null;
let healthRequestId = 0;
let appliedHealthRequestId = 0;
let optimisticViolation = null;
let confirmedViolation = { agent_interrupt_pending: false };

let pollStateRecipient = null;

function sendPollState() {
  const marker = document.getElementById("steering-poll-state");
  if (!pollStateRecipient || !marker) return;
  pollStateRecipient.source.postMessage({
    type: "clankers:steering-poll-state",
    ...marker.dataset,
  }, pollStateRecipient.origin);
}

window.addEventListener("message", (event) => {
  if (event.source !== window.parent || event.data?.type !== "clankers:steering-poll-request" ||
      !/^https?:\/\//.test(event.origin)) return;
  pollStateRecipient = { source: event.source, origin: event.origin };
  getHealthWithConnection().catch(() => {});
});

function applyConnection(requestId, health, error = false, startedAt = Date.now()) {
  if (requestId < appliedHealthRequestId) return;
  appliedHealthRequestId = requestId;
  const pollState = document.getElementById("steering-poll-state");
  if (pollState) {
    pollState.dataset.updatedAt = String(startedAt);
    pollState.dataset.available = String(!error);
    pollState.dataset.polling = String(!error && Boolean(health.polling));
    pollState.dataset.pollingGate = String(!error && Boolean(health.polling_gate));
    pollState.dataset.pollCompleted = String(!error && Boolean(health.poll_completed));
    pollState.dataset.turnEndedAt = error ? "" : (health.turn_ended_at || "");
    sendPollState();
  }
  const connection = error ? { error: true } : {
    loaded: true,
    polling: Boolean(health.polling),
    polling_since: health.polling_since,
    agent_seen_at: health.agent_seen_at,
    turn_ended_at: health.turn_ended_at,
    transport: transportState(),
  };
  store.getState().connection = connection;
  if (!error && store.getState().skipPollArmed !== Boolean(health.skip_poll)) {
    store.setState({ skipPollArmed: Boolean(health.skip_poll) });
  }
  paintConnection(document.getElementById("view-messages"), connection);
}

function paintViolationFlag(health) {
  const button = document.getElementById("agent-interrupt");
  if (!button) return;
  health = optimisticViolation || health;
  const pending = Boolean(health.agent_interrupt_pending);
  const label = pending
    ? `Flagged${health.violation_type ? `: ${health.violation_type.replaceAll("-", " ")}` : ""}. Click to cancel.`
    : "Flag violation";
  button.setAttribute("aria-pressed", String(pending));
  button.setAttribute("aria-label", label);
  button.title = label;
}

function setViolationStatus(message) {
  const status = document.getElementById("violation-status");
  if (!status) return;
  status.textContent = message;
  status.hidden = !message;
}

async function getHealthWithConnection() {
  const requestId = ++healthRequestId;
  const startedAt = Date.now();
  try {
    const health = await api.getHealth();
    healthSeenRevision = health.message_revision;
    healthSeenVisibleRevision = health.visible_message_revision;
    healthSeenMessageTotal = health.message_total;
    applyConnection(requestId, health, false, startedAt);
    if (requestId === appliedHealthRequestId) {
      confirmedViolation = health;
      paintViolationFlag(health);
    }
    return health;
  } catch (error) {
    applyConnection(requestId, null, true, startedAt);
    throw error;
  }
}

function isNewerReportVersion(candidate, baseline) {
  const candidateMs = Date.parse(candidate);
  const baselineMs = Date.parse(baseline);
  if (!Number.isFinite(candidateMs) || !Number.isFinite(baselineMs)) return false;
  if (candidateMs !== baselineMs) return candidateMs > baselineMs;
  // Date.parse drops the microseconds that the store uses to break write ties.
  const remainingMicros = (stamp) => Number((stamp.match(/T\d\d:\d\d:\d\d\.(\d+)/)?.[1] || "").padEnd(6, "0").slice(3, 6));
  return remainingMicros(candidate) > remainingMicros(baseline);
}

async function ensureReportCached(reportId, expectedModifiedAt = null, { quiet = false } = {}) {
  if (!reportId) return null;
  const cached = reportCache.get(reportId);
  if (
    cached &&
    (!expectedModifiedAt || cached.modified_at === expectedModifiedAt || isNewerReportVersion(cached.modified_at, expectedModifiedAt)) &&
    Array.isArray(cached.report?.blocks)
  ) {
    return cached.report;
  }
  const loadKey = `${reportId}:${expectedModifiedAt || ""}`;
  if (pendingReportLoads.has(loadKey)) {
    const existing = pendingReportLoads.get(loadKey);
    if (!quiet && existing.quiet) {
      existing.quiet = false;
      setLoading(true);
    }
    return existing.promise;
  }
  const entry = { quiet, promise: null };
  entry.promise = (async () => {
    if (!entry.quiet) setLoading(true);
    try {
      const res = await api.getReport(reportId);
      if (res?.report) {
        const current = reportCache.get(reportId);
        if (current && Array.isArray(current.report?.blocks) && isNewerReportVersion(current.modified_at, res.report.modified_at)) {
          return current.report;
        }
        reportCache.set(reportId, {
          modified_at: res.report.modified_at,
          report: res.report,
        });
        return res.report;
      }
      return null;
    } finally {
      if (!entry.quiet) setLoading(false);
      pendingReportLoads.delete(loadKey);
    }
  })();
  pendingReportLoads.set(loadKey, entry);
  return entry.promise;
}

function hydrateReportsWithCache(summaries) {
  const activeIds = new Set();
  const merged = (summaries || []).map((summary) => {
    activeIds.add(summary.id);
    const cached = reportCache.get(summary.id);
    if (cached && isNewerReportVersion(cached.modified_at, summary.modified_at)) {
      return cached.report;
    }
    if (Array.isArray(summary.blocks)) {
      reportCache.set(summary.id, {
        modified_at: summary.modified_at,
        report: summary,
      });
      return summary;
    }
    if (cached && cached.modified_at === summary.modified_at) {
      return {
        ...cached.report,
        ...summary,
        body: cached.report.body,
        blocks: cached.report.blocks,
      };
    }
    return summary;
  });
  for (const key of reportCache.keys()) {
    if (!activeIds.has(key)) {
      reportCache.delete(key);
    }
  }
  return merged;
}

function syncReportRailTop() {
  const panel = document.getElementById("view-reports");
  if (!panel || panel.hidden) return;
  document.documentElement.style?.setProperty?.(
    "--reports-rail-top", `${Math.max(0, panel.getBoundingClientRect().top)}px`
  );
}

function syncViewportHeight() {
  const vv = window.visualViewport;
  const vvScale = vv?.scale > 0 ? vv.scale : 1;
  const vvHeight = (vv?.height || 0) * vvScale;
  const innerHeight = window.innerHeight || 0;
  const docHeight = document.documentElement?.clientHeight || 0;
  const heights = [vvHeight, innerHeight, docHeight].filter((h) => h > 0);
  const nextHeight = heights.length ? Math.round(Math.min(...heights)) : 0;
  if (!nextHeight) return;
  if (nextHeight !== lastViewportHeight) {
    lastViewportHeight = nextHeight;
    document.documentElement.style?.setProperty?.(
      "--app-height",
      `${nextHeight}px`
    );
  }
  const focused = document.activeElement;
  const editing = focused?.matches?.(
    "input:not([type='hidden']):not([readonly]), textarea:not([readonly]), [contenteditable='true']"
  );
  const mobile = window.matchMedia?.("(max-width: 60em)")?.matches;
  const keyboardOpen = Boolean(
    mobile && editing && vvScale <= 1.01 &&
    Math.max(innerHeight, docHeight) - vvHeight >= MOBILE_KEYBOARD_MIN_SHRINK
  );
  document.documentElement.toggleAttribute("data-mobile-keyboard-open", keyboardOpen);
  document.documentElement.style?.setProperty?.(
    "--reports-keyboard-inset",
    `${keyboardOpen ? Math.max(0, innerHeight - ((vv?.offsetTop || 0) + vvHeight)) : 0}px`
  );
  // A panned visual viewport starts below the layout viewport. Keep the shell
  // within that visible region rather than leaving an offset-sized bottom gap.
  document.documentElement.style?.setProperty?.(
    "--app-offset-top", `${keyboardOpen ? Math.max(0, vv?.offsetTop || 0) : 0}px`
  );
  syncReportRailTop();
  if (
    ((vvScale <= 1.01 && vv?.offsetTop) || window.scrollY) &&
    typeof window.scrollTo === "function"
  ) {
    window.scrollTo(0, 0);
  }
}

let reportEpoch = 0;
const pendingReportDismissals = new Set();
let loadingCount = 0;
let loadingStartAt = 0;
let loadingDoneTimer = null;

function setLoading(active) {
  const panel = document.getElementById("view-reports");
  if (!panel) return;
  if (active) {
    loadingCount += 1;
    if (loadingDoneTimer) {
      clearTimeout(loadingDoneTimer);
      loadingDoneTimer = null;
    }
    if (panel.getAttribute("data-loading") !== "true") {
      loadingStartAt = Date.now();
      panel.setAttribute("data-loading", "true");
      panel.setAttribute("aria-busy", "true");
    }
  } else {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount === 0) {
      panel.setAttribute("aria-busy", "false");
      const elapsed = Date.now() - loadingStartAt;
      if (elapsed >= 200) {
        panel.setAttribute("data-loading", "done");
        loadingDoneTimer = setTimeout(() => {
          if (
            loadingCount === 0 &&
            panel.getAttribute("data-loading") === "done"
          ) {
            panel.setAttribute("data-loading", "false");
          }
        }, 220);
      } else {
        panel.setAttribute("data-loading", "false");
      }
    }
  }
}

let messageEpoch = 0;
let messageRequests = 0;
let olderPageRequest = null;
let loadedMessageKey = null;
let olderPageErrorKey = null;
let historyCache = null;
let hiddenMessageCache = null;
let visibleWindowCache = null;
let healthSeenRevision = undefined;
let healthSeenVisibleRevision = undefined;
let healthSeenMessageTotal = undefined;
let visibleQueryRevision = undefined;
let loadedMessageRevision = null;
let searchBase = null;
const confirmedSeenRevisions = new Map();

function preserveConfirmedRepliesSeen(messages) {
  return (messages || []).map((item) => {
    const confirmedRev = confirmedSeenRevisions.get(item.id);
    if (!confirmedRev) return item;
    if (item.replies_revision !== confirmedRev) {
      confirmedSeenRevisions.delete(item.id);
      return item;
    }
    return item.replies_seen ? item : { ...item, replies_seen: true };
  });
}

function markMessageRevisionSeenInList(list, id, revision, modifiedAt) {
  let changed = false;
  const next = (list || []).map((item) => {
    if (item.id !== id || item.replies_revision !== revision || item.replies_seen) return item;
    changed = true;
    return {
      ...item,
      replies_seen: true,
      ...(modifiedAt && modifiedAt > (item.modified_at || "") ? { modified_at: modifiedAt } : {}),
    };
  });
  return changed ? next : list;
}

export function messageMatchesQuery(message, query) {
  const needle = String(query || "").trim().toLowerCase();
  if (!needle) return true;
  const haystacks = [message.id, message.body, ...(message.parent_refs || []).map((ref) => ref.id), ...(message.parent_ids || []), ...(message.acks || [])];
  return haystacks.some((item) => String(item || "").toLowerCase().includes(needle));
}

function cachedVisibleWindow(key) {
  // The cached window serves polls until the server reports a newer revision.
  const cached = visibleWindowCache;
  if (!cached || cached.key !== key || !cached.revision) return null;
  return cached;
}

async function fetchMessagePage({ before = "", reset = false, preserve = false } = {}) {
  const state = store.getState();
  const key = messageQueryKey(state);
  if (!before && !reset && !preserve && !state.showHidden) {
    const cached = cachedVisibleWindow(key);
    // Only a visible revision may expire the cache, so a hidden write cannot refetch it.
    const passRevision = healthSeenVisibleRevision !== undefined
      ? healthSeenVisibleRevision
      : visibleQueryRevision;
    if (cached && passRevision !== undefined && cached.revision === passRevision) {
      loadedMessageKey = key;
      loadedMessageRevision = cached.revision;
      // Merge so a message written after the cache write still shows.
      const live = state.messages || [];
      const merged = [
        ...cached.messages,
        ...live.filter((item) => !cached.messages.some((message) => message.id === item.id)),
      ];
      if (JSON.stringify(state.messages) !== JSON.stringify(merged)) {
        store.setState({ messages: merged, messagesHasMore: cached.hasMore, messagesError: "", messagesLoading: false });
      }
      return merged;
    }
  }
  // Refresh the latest page now. Keep loaded history until the older-page retry completes.
  const refreshDuringOlderPage = !before && !reset && olderPageRequest?.key === key;
  if (refreshDuringOlderPage) olderPageRequest.refreshRequested = true;
  const epoch = ++messageEpoch;
  const previous = !preserve && (reset || loadedMessageKey !== key) ? [] : [...(state.messages || [])];
  if (reset || loadedMessageKey !== key) {
    loadedMessageKey = key;
    olderPageErrorKey = null;
    store.setState({ ...(preserve ? {} : { messages: [], messagesHasMore: false }), messagesError: "", messagesLoading: true });
  }
  messageRequests += 1;
  try {
    const options = { all: true, hidden: Boolean(state.showHidden), query: (state.searchQuery || "").trim(), requestId: state.optimisticMessage?.requestId || "" };
    const result = await api.listMessages({ ...options, before });
    if (epoch !== messageEpoch || messageQueryKey(store.getState()) !== key) return null;
    if (hiddenMessageCache && hiddenMessageCache.revision !== result.revision) hiddenMessageCache.dirty = true;
    if (!state.showHidden) visibleQueryRevision = result.revision;
    let fresh = result.messages || [];
    let hasMore = result.has_more;
    if (!before && previous.length && !refreshDuringOlderPage) {
      while (hasMore && (preserve
        ? !fresh.some((message) => message.id === previous[0].id)
        : fresh.length < previous.length)) {
        const older = await api.listMessages({ ...options, before: fresh[0].id });
        if (epoch !== messageEpoch || messageQueryKey(store.getState()) !== key) return null;
        fresh = [...(older.messages || []), ...fresh];
        hasMore = older.has_more;
      }
    }
    const ids = new Set(fresh.map((item) => item.id));
    const previousIds = new Set(previous.map((item) => item.id));
    const latestById = new Map(fresh.map((item) => [item.id, item]));
    const merged = preserveConfirmedRepliesSeen(before
      ? [...fresh, ...previous.filter((item) => !ids.has(item.id))]
      : refreshDuringOlderPage
        ? [
          ...previous.map((item) => latestById.get(item.id) || item),
          ...fresh.filter((item) => !previousIds.has(item.id)),
        ]
        : fresh);
    const current = store.getState();
    if (!before) loadedMessageRevision = result.revision;
    if (result.pending_message_id && current.optimisticMessage?.requestId === options.requestId) {
      current.optimisticMessage.serverId = result.pending_message_id;
    }
    const retainedError = !before && olderPageErrorKey === key ? current.messagesError : "";
    if (before) olderPageErrorKey = null;
    if (current.messagesHasMore !== hasMore || current.messagesError !== retainedError || current.messagesLoading ||
        JSON.stringify(current.messages) !== JSON.stringify(merged)) {
      store.setState({ messages: merged, messagesHasMore: hasMore, messagesError: retainedError, messagesLoading: false });
    }
    const activeQuery = (store.getState().searchQuery || "").trim().toLowerCase();
    if (activeQuery && !before) {
      searchBase = { query: activeQuery, messages: merged, hasMore };
    }
    return merged;
  } catch (error) {
    if (epoch === messageEpoch && messageQueryKey(store.getState()) === key) {
      if (before) olderPageErrorKey = key;
      store.setState({ messagesError: `Could not load messages: ${error.message}`, messagesLoading: false });
    }
    return null;
  } finally {
    messageRequests -= 1;
  }
}

let taskAgeTimer = null;

function scheduleTaskAgeRefresh(tasks) {
  clearTimeout(taskAgeTimer);
  const now = Date.now();
  const deadlines = tasks.filter(task => task.status === "upcoming" && task.priority !== "P1" &&
    task.priority === task.effective_priority && task.created_at)
    .map(task => Date.parse(task.created_at) + 3600000)
    .filter(Number.isFinite);
  if (!deadlines.length) return;
  const delay = Math.min(2147483647, Math.max(1000, Math.min(...deadlines) - now + 1000));
  taskAgeTimer = setTimeout(() => { void refreshAll(true); }, delay);
}

let refreshEpoch = 0;

async function refreshAll(force = false) {
  const epoch = ++refreshEpoch;
  try {
    const curState = store.getState();
    const currentReportEpoch = reportEpoch;
    const [health, firstMsgRes, taskRes, repRes] = await Promise.all([
      getHealthWithConnection(),
      fetchMessagePage(),
      api.listTasks({ full: true }),
      api.listReports({ stale: false, summary: true }),
    ]);
    // The fetch races the health fetch, so refetch when this poll reports a
    // newer visible revision than the rendered window.
    const pollRevision = health.visible_message_revision !== undefined
      ? health.visible_message_revision
      : health.message_revision;
    // A count change means an insert the cache cannot hold, so expire it too.
    const countChanged = health.message_total !== undefined &&
      healthSeenMessageTotal !== undefined &&
      health.message_total !== healthSeenMessageTotal;
    const msgRes =
      countChanged || (pollRevision && loadedMessageRevision && pollRevision !== loadedMessageRevision)
        ? await fetchMessagePage()
        : firstMsgRes;
    if (epoch !== refreshEpoch || msgRes === null) return;
    if (health.asset_version) {
      if (knownAssetVersion && knownAssetVersion !== health.asset_version) {
        reloadPreservingSurvey();
        return;
      }
      knownAssetVersion = health.asset_version;
    }
    if (health.repo) {
      const expectedTitle = `${health.repo} - Steering`;
      if (document.title !== expectedTitle) {
        document.title = expectedTitle;
      }
    }
    const browserTimezone =
      Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    if (browserTimezone && reportedTimezone !== browserTimezone) {
      reportedTimezone = browserTimezone;
      api.setTimezone(browserTimezone).catch(() => {
        reportedTimezone = null;
      });
    }
    const messages = msgRes;
    const tasks = taskRes.tasks || [];
    scheduleTaskAgeRefresh(tasks);
    if (epoch !== refreshEpoch || currentReportEpoch !== reportEpoch) return;
    const reportSummaries = (repRes.reports || []).filter((report) => !pendingReportDismissals.has(report.id));
    const survey = health.survey || {};
    const lastTool = health.last_tool || null;
    const callsSinceMessage = Number(health.calls_since_message || 0);
    trackMessageAcks(messages.filter((msg) => curState.showHidden || !msg.hidden));
    const selectedReportId =
      curState.selectedReportId &&
      reportSummaries.some((r) => r.id === curState.selectedReportId)
        ? curState.selectedReportId
        : reportSummaries[0]?.id || null;
    if (selectedReportId) {
      const selectedSummary = reportSummaries.find(
        (r) => r.id === selectedReportId
      );
      await ensureReportCached(selectedReportId, selectedSummary?.modified_at);
    }
    const pendingAnswers = store.getState().pendingReportAnswers || {};
    for (const pendingAnswer of Object.values(pendingAnswers)) {
      const result = await api.getReportAnswerRequest(pendingAnswer.reportId, pendingAnswer.requestId);
      const current = store.getState().pendingReportAnswers || {};
      if (current[pendingAnswer.reportId] === pendingAnswer && result.answer_id) {
        store.setState({ pendingReportAnswers: {
          ...current, [pendingAnswer.reportId]: { ...pendingAnswer, answerId: result.answer_id },
        } });
      }
    }
    if (epoch !== refreshEpoch || currentReportEpoch !== reportEpoch) return;
    const reports = hydrateReportsWithCache(reportSummaries);
    const latestSelection = store.getState().selectedReportId;
    const currentSelectedReportId = reports.some((report) => report.id === latestSelection)
      ? latestSelection : selectedReportId;
    const replyParentRefs = curState.replyParentRefs || [];
    const nextSignature = JSON.stringify({
      messages,
      tasks,
      reports,
      survey,
      lastTool,
      callsSinceMessage,
      selectedReportId: currentSelectedReportId,
      replyParentRefs,
      showHidden: Boolean(curState.showHidden),
    });
    if (!force && nextSignature === lastDataSignature) {
      return;
    }
    lastDataSignature = nextSignature;
    store.setState({
      tasks,
      reports,
      survey,
      lastTool,
      callsSinceMessage,
      selectedReportId: currentSelectedReportId,
      replyParentRefs,
    });
  } catch (err) {
    console.error(err);
  }
}

const composerUploads = createComposerUploads(store, (file) => api.uploadFile(file));
let replySelectionRevision = 0;
let reportSelectionRevision = 0;
let confirmedReportId = null;
let referenceNavigationRevision = 0;
const PENDING_MESSAGE_RETRY_KEY = "steer:pendingSend";
let pendingMessageRetry = null;
try {
  const saved = sessionStorage.getItem(PENDING_MESSAGE_RETRY_KEY);
  if (saved) {
    const parsed = JSON.parse(saved);
    if (typeof parsed?.payloadKey !== "string" || typeof parsed?.request?.request_id !== "string") {
      throw new Error("Invalid pending message recovery data");
    }
    pendingMessageRetry = parsed;
  }
} catch {
  pendingMessageRetry = { payloadKey: null };
}

const handlers = {
  onRenderDraft(markdown) {
    return api.renderMarkdown(markdown, { breaks: true, preWrap: true });
  },
  async onSendMessage(payload) {
    if (store.getState().sending) throw new Error("A send is already in progress");
    const payloadKey = JSON.stringify(payload);
    if (pendingMessageRetry && pendingMessageRetry.payloadKey !== payloadKey) {
      const error = new Error("Resolve the previous message before sending a different one.");
      store.setState({ composerError: error.message });
      throw error;
    }
    const pendingId = `pending-${crypto.randomUUID()}`;
    // A page requested before this send cannot reconcile its optimistic row.
    messageEpoch += 1;
    const selectedRefs = store.getState().replyParentRefs || [];
    const clearRefs = JSON.stringify(selectedRefs) === JSON.stringify(payload.parent_refs || []);
    const selectedRevision = replySelectionRevision;
    const staged = store.getState().composerAttachments || [];
    const optimisticMessage = {
      id: pendingId, requestId: null, optimistic: true,
      body: payload.body, parent_ids: payload.parent_ids || [], parent_refs: payload.parent_refs || [],
      attachments: (payload.attachmentIds || []).map(id => {
        const item = staged.find(entry => entry.id === id);
        return item?.attachment || { name: item?.name || "Attachment" };
      }),
      attachmentIds: payload.attachmentIds || [],
      status: "sending", blocking: true, modified_at: new Date().toISOString(), acks: [],
    };
    store.setState({ sending: true, composerError: "", optimisticMessage, optimisticConfirmation: null, ...(clearRefs ? { replyParentRefs: [] } : {}) });
    try {
      const requestId = pendingMessageRetry?.request.request_id || await api.newRequestId();
      optimisticMessage.requestId = requestId;
      store.setState({ optimisticMessage: { ...optimisticMessage } });
      const attachmentIds = payload.attachmentIds || [];
      const attachments = pendingMessageRetry?.request.attachments || await composerUploads.readyForSend(attachmentIds);
      const parentRefs = payload.parent_refs || [];
      const request = {
        body: payload.body,
        parent_ids: parentRefs.length
          ? parentRefs.filter((ref) => ref.type === "message").map((ref) => ref.id)
          : payload.parent_ids || [],
        attachments,
        request_id: requestId,
      };
      if (parentRefs.length) request.parent_refs = parentRefs;
      const pending = { payloadKey, request };
      sessionStorage.setItem(PENDING_MESSAGE_RETRY_KEY, JSON.stringify(pending));
      pendingMessageRetry = pending;
      let response;
      if (request.attachments.length) {
        response = await api.sendMessageWithFile(request);
        composerUploads.clearSent(attachmentIds);
      } else {
        response = await api.sendMessage(request);
      }
      sessionStorage.removeItem(PENDING_MESSAGE_RETRY_KEY);
      pendingMessageRetry = null;
      // Ignore page responses started before this confirmed send.
      messageEpoch += 1;
      const current = store.getState();
      const message = response.message;
      const existing = (current.messages || []).find(item => item.id === message?.id);
      const confirmed = existing && existing.modified_at >= message.modified_at
        ? existing : message;
      const messages = confirmed && messageMatchesQuery(confirmed, current.searchQuery) && !confirmed.hidden
        ? [...(current.messages || []).filter(item => item.id !== confirmed.id), confirmed]
        : current.messages;
      store.setState({ messages, optimisticMessage: null, optimisticConfirmation: message ? { pendingId: optimisticMessage.id, serverId: message.id } : null, sending: false, composerError: response.reconciliation_error ? "Message sent, but its current state could not be loaded." : null });
    } catch (error) {
      const restoreRefs = clearRefs && replySelectionRevision === selectedRevision;
      // No retries remain, so the recovery record would replay a send the
      // page already reports as failed.
      sessionStorage.removeItem(PENDING_MESSAGE_RETRY_KEY);
      pendingMessageRetry = null;
      store.setState({ sending: false, optimisticMessage: null, composerError: String(error.message || error), ...(restoreRefs ? { replyParentRefs: selectedRefs } : {}) });
      throw error;
    }
    // Do not keep a confirmed draft editable while an unrelated refresh waits.
    void refreshAll(true);
  },
  async onNavigateReference(type, id) {
    if (!id) return;
    const navigationRevision = ++referenceNavigationRevision;
    try {
      const current = store.getState();
      if (type === "message") {
        let target = (current.messages || []).find((item) => item.id === id);
        if (!target) target = (await api.getMessage(id)).message;
        if (navigationRevision !== referenceNavigationRevision) return;
        if (!target) throw new Error("Message not found");
        expandMessageHistory();
        const latest = store.getState();
        store.setState({ activeTab: "messages", searchQuery: "", showHidden: latest.showHidden || target.hidden });
        const key = messageQueryKey(store.getState());
        let messages = loadedMessageKey === key && (latest.messages || []).some((item) => item.id === id)
          ? latest.messages : await fetchMessagePage({ reset: true });
        while (navigationRevision === referenceNavigationRevision && messageQueryKey(store.getState()) === key && store.getState().activeTab === "messages") {
          if (messages?.some((item) => item.id === id)) break;
          if (!messages && store.getState().messagesError) throw new Error(store.getState().messagesError);
          if (!messages) {
            messages = await fetchMessagePage({ reset: true });
            continue;
          }
          if (!store.getState().messagesHasMore || !messages.length) throw new Error("Message not found in history");
          messages = await fetchMessagePage({ before: messages[0].id });
        }
        if (navigationRevision !== referenceNavigationRevision || messageQueryKey(store.getState()) !== key || store.getState().activeTab !== "messages") return;
      } else if (type === "task") {
        let target = (current.tasks || []).find((item) => item.id === id);
        if (!target) target = (await api.getTask(id)).task;
        if (!target) throw new Error("Task not found");
        expandTaskTarget(id);
        const latest = store.getState();
        store.setState({ activeTab: "tasks", tasks: (latest.tasks || []).some((item) => item.id === id) ? latest.tasks : [...(latest.tasks || []), target] });
      } else if (type === "report") {
        let target = (current.reports || []).find((item) => item.id === id);
        if (!target) target = (await api.getReport(id)).report;
        if (!target) throw new Error("Report not found");
        if (!(current.reports || []).some((item) => item.id === id)) {
          store.setState({ reports: [...(current.reports || []), target] });
        }
        store.setState({ activeTab: "reports" });
        await handlers.onSelectReport(id);
        return;
      } else return;
      const selector = type === "message" ? `.message[data-msg-id="${CSS.escape(id)}"]` : `.task[data-task-id="${CSS.escape(id)}"]`;
      const row = document.querySelector(selector);
      row?.scrollIntoView({ behavior: type === "message" ? "instant" : "smooth", block: "center" });
      if (row) {
        row.dataset.highlight = "true";
        setTimeout(() => { if (row.isConnected) delete row.dataset.highlight; }, 1200);
      }
    } catch (error) {
      console.error(error);
      await modal.open("navigation-error", {
        title: "Could not open reference",
        slots: { message: `Could not open ${type} ${id}.` },
      });
    }
  },
  async onMarkRepliesSeen(id, revision) {
    try {
      const response = await api.markRepliesSeen(id, revision);
      const confirmedMsg = response?.message;
      if (!confirmedMsg || confirmedMsg.replies_revision === revision) {
        confirmedSeenRevisions.set(id, revision);
        const modifiedAt = confirmedMsg?.replies_revision === revision ? confirmedMsg.modified_at : "";
        if (historyCache) {
          historyCache = {
            ...historyCache,
            messages: markMessageRevisionSeenInList(historyCache.messages, id, revision, modifiedAt),
          };
        }
        if (hiddenMessageCache) {
          hiddenMessageCache = {
            ...hiddenMessageCache,
            messages: markMessageRevisionSeenInList(hiddenMessageCache.messages, id, revision, modifiedAt),
          };
        }
        if (searchBase) {
          searchBase = {
            ...searchBase,
            messages: markMessageRevisionSeenInList(searchBase.messages, id, revision, modifiedAt),
          };
        }
        const current = store.getState().messages || [];
        const nextMessages = markMessageRevisionSeenInList(current, id, revision, modifiedAt);
        if (nextMessages !== current) {
          lastDataSignature = "";
          store.setState({ messages: nextMessages });
        }
      }
    } catch (error) {
      if (String(error.message).includes("(409)")) {
        await fetchMessagePage().catch(() => {});
      } else {
        console.error(error);
      }
      throw error;
    }
  },
  onStageComposerFiles(files) {
    return composerUploads.stage(files);
  },
  onRemoveComposerAttachment(id) {
    composerUploads.remove(id);
  },
  onToggleComposerExpanded() {
    store.setState({
      composerExpanded: !store.getState().composerExpanded,
    });
  },
  onSetComposerExpanded(expanded) {
    if (store.getState().composerExpanded !== expanded) {
      store.setState({ composerExpanded: expanded });
    }
  },
  onToggleReplyParent(type, id) {
    if (!id || !["message", "task", "report"].includes(type)) return;
    const current = store.getState().replyParentRefs || [];
    const next = current.some((ref) => ref.type === type && ref.id === id)
      ? current.filter((ref) => ref.type !== type || ref.id !== id)
      : [...current, { type, id }];
    replySelectionRevision += 1;
    store.setState({ replyParentRefs: next });
  },
  async onSkipPoll() {
    const result = await api.skipPoll();
    appliedHealthRequestId = ++healthRequestId;
    store.setState({ skipPollArmed: result.skip_poll });
  },
  async onCancelSkipPoll() {
    const result = await api.cancelSkipPoll();
    appliedHealthRequestId = ++healthRequestId;
    store.setState({ skipPollArmed: result.skip_poll });
  },
  async onToggleHidden() {
    const state = store.getState();
    const nextHidden = !state.showHidden;
    const query = (state.searchQuery || "").trim();
    historyCache = null;
    searchBase = null;
    const epoch = ++messageEpoch;
    if (!nextHidden) {
      // Keep the hidden window so the next toggle reuses it instead of refetching.
      hiddenMessageCache = {
        query,
        messages: state.messages || [],
        hasMore: state.messagesHasMore,
        revision: loadedMessageRevision,
        dirty: Boolean(state.messagesLoading || messageRequests) || loadedMessageRevision == null,
      };
      // The visible revision gates visibility, not the hidden one.
      if (visibleQueryRevision !== undefined && !state.messagesLoading && !messageRequests) {
        const visibleRevision = healthSeenVisibleRevision !== undefined
          ? healthSeenVisibleRevision
          : visibleQueryRevision;
        healthSeenMessageTotal = state.messages?.length;
        visibleWindowCache = {
          key: messageQueryKey({ ...state, showHidden: false }),
          messages: (state.messages || []).filter(message => !message.hidden),
          hasMore: state.messagesHasMore,
          revision: visibleRevision,
        };
      }
      // Reuse the window the toggle-off already holds instead of refetching it.
      const visibleKey = messageQueryKey({ ...state, showHidden: false });
      const cached = visibleWindowCache?.key === visibleKey ? visibleWindowCache : null;
      if (!cached) visibleWindowCache = null;
      // The cache misses later writes, so merge the live window into it.
      const live = (state.messages || []).filter(message => !message.hidden);
      const visible = cached
        ? [...live, ...cached.messages.filter(
          (item) => !live.some((message) => message.id === item.id)
        )]
        : live;
      store.setState({
        showHidden: false,
        messages: visible,
        messagesHasMore: cached ? cached.hasMore : state.messagesHasMore,
        messagesLoading: false,
      });
      loadedMessageKey = messageQueryKey(store.getState());
      return;
    }
    visibleWindowCache = {
      key: messageQueryKey(state),
      messages: state.messages || [],
      hasMore: state.messagesHasMore,
      revision: visibleQueryRevision,
    };
    const cached = hiddenMessageCache?.query === query ? hiddenMessageCache : null;
    if (cached) loadedMessageRevision = cached.revision;
    // The cache misses later writes, so merge it into the current window.
    const seed = cached
      ? [...(state.messages || []), ...cached.messages.filter(
        (item) => !(state.messages || []).some((message) => message.id === item.id)
      )]
      : null;
    store.setState({
      showHidden: true,
      ...(seed ? { messages: seed, messagesHasMore: cached.hasMore || state.messagesHasMore } : {}),
      messagesLoading: false,
    });
    loadedMessageKey = messageQueryKey(store.getState());
    if (cached && !cached.dirty && !messageRequests) return;
    const messages = state.messages || [];
    if (!messages.length) return;
    try {
      const result = await api.hiddenMessageWindow(messages[0].id, messages[messages.length - 1].id, query);
      if (epoch !== messageEpoch || !store.getState().showHidden) return;
      const current = store.getState().messages || [];
      const byId = new Map(current.map(message => [message.id, message]));
      for (const message of preserveConfirmedRepliesSeen(result.messages)) byId.set(message.id, message);
      const order = new Map((result.order || []).map((id, index) => [id, index]));
      const merged = [...byId.values()].sort((a, b) => {
        if (order.has(a.id) && order.has(b.id)) return order.get(a.id) - order.get(b.id);
        if (order.has(a.id)) return -1;
        if (order.has(b.id)) return 1;
        return current.indexOf(a) - current.indexOf(b);
      });
      store.setState({ messages: merged, messagesError: "" });
      if (epoch === messageEpoch && store.getState().showHidden) {
        await fetchMessagePage({ preserve: true });
      }
    } catch (error) {
      if (epoch === messageEpoch) store.setState({ messagesError: error.message });
    }
  },
  onSearchChange(query) {
    const state = store.getState();
    const wasSearching = Boolean((state.searchQuery || "").trim());
    const searching = Boolean(query.trim());
    if (!wasSearching && searching) {
      historyCache = { messages: [...(state.messages || [])], hasMore: state.messagesHasMore };
    }
    if (wasSearching && !searching && historyCache) {
      searchBase = null;
      loadedMessageKey = messageQueryKey({ ...state, searchQuery: query });
      store.setState({ searchQuery: query, messages: historyCache.messages, messagesHasMore: historyCache.hasMore, messagesError: "" });
      historyCache = null;
      fetchMessagePage();
      return;
    }
    const needle = query.trim().toLowerCase();
    if (searchBase && needle.startsWith(searchBase.query)) {
      messageEpoch += 1;
      loadedMessageKey = messageQueryKey({ ...state, searchQuery: query });
      store.setState({
        searchQuery: query,
        messages: searchBase.messages.filter((item) => messageMatchesQuery(item, needle)),
        messagesHasMore: searchBase.hasMore,
        messagesError: "",
        messagesLoading: false,
      });
      return;
    }
    searchBase = null;
    store.setState({ searchQuery: query, messages: [], messagesHasMore: false, messagesError: "", messagesLoading: true });
    fetchMessagePage({ reset: true });
  },
  async onLoadOlderMessages() {
    const state = store.getState();
    const key = messageQueryKey(state);
    if (olderPageRequest?.key === key || !state.messagesHasMore || !state.messages.length) return;
    const request = { key, refreshRequested: false };
    olderPageRequest = request;
    try {
      const before = state.messages[0].id;
      while (messageQueryKey(store.getState()) === key) {
        const loading = fetchMessagePage({ before });
        const startedEpoch = messageEpoch;
        const result = await loading;
        if (result || messageEpoch === startedEpoch) break;
      }
    } finally {
      if (olderPageRequest === request) olderPageRequest = null;
      if (request.refreshRequested && messageQueryKey(store.getState()) === key) {
        refreshAll(true);
      }
    }
  },
  async onOpenSurvey(trigger) {
    const cached = store.getState().survey || {};
    const revision = ++surveyOpenRevision;
    const initialHtml = cached.checked_at || cached.answered_at ? renderSurveyModalContent(cached) : '<p role="status">Loading survey…</p>';
    surveyOpen = true;
    const closed = modal.open("survey-results", {
      title: "Survey Verification",
      htmlSlots: { content: initialHtml },
      trigger,
    });
    const content = document.querySelector("#modal-shell[open] .survey-modal-content");
    const isCurrent = () => content?.isConnected && document.querySelector("#modal-shell[open] .survey-modal-content") === content;
    api.getSurvey().then((response) => {
      const updatedHtml = renderSurveyModalContent(response.survey || {});
      if (isCurrent() && updatedHtml !== initialHtml) content.innerHTML = updatedHtml;
    }).catch((error) => {
      if (!isCurrent()) return;
      if (!cached.checked_at && !cached.answered_at) content.replaceChildren();
      content.insertAdjacentHTML("beforeend", `<p role="alert">Could not refresh survey: ${escapeHtml(error.message || error)}</p>`);
    });
    const decision = await closed;
    if (revision === surveyOpenRevision) surveyOpen = false;
    if (decision === "bible-check") {
      await api.triggerBibleCheck(true).catch(() => {});
      await refreshAll(true);
    }
  },
  async onSelectReport(reportId) {
    const current = store.getState();
    const target = (current.reports || []).find((r) => r.id === reportId);
    if (!target) return;
    if (!current.loadingReportId) confirmedReportId = current.selectedReportId;
    const revision = ++reportSelectionRevision;
    store.setState({ selectedReportId: reportId, loadingReportId: reportId, reportSelectionError: "" });
    try {
      const report = await ensureReportCached(reportId, target.modified_at);
      if (!report) throw new Error("Report unavailable");
      if (revision !== reportSelectionRevision) return;
      confirmedReportId = reportId;
      const hydrated = hydrateReportsWithCache(store.getState().reports || []);
      store.setState({ reports: hydrated, loadingReportId: null });
    } catch (error) {
      if (revision !== reportSelectionRevision) return;
      store.setState({ selectedReportId: confirmedReportId, loadingReportId: null, reportSelectionError: `Could not load report: ${error.message || error}` });
    }
  },
  async onDismissReport(reportId, trigger) {
    const decision = await modal.open("confirm-delete", {
      title: `Dismiss report ${reportId}?`,
      slots: { message: `Mark report "${reportId}" as dismissed?` },
      trigger,
    });
    if (decision !== "confirm") return;
    const state = store.getState();
    const index = (state.reports || []).findIndex((report) => report.id === reportId);
    if (index < 0 || pendingReportDismissals.has(reportId)) return;
    const removed = state.reports[index];
    const reports = state.reports.filter((report) => report.id !== reportId);
    const wasSelected = state.selectedReportId === reportId;
    const revision = ++reportSelectionRevision;
    pendingReportDismissals.add(reportId);
    reportEpoch += 1;
    const selectedReportId = wasSelected ? reports[0]?.id || null : state.selectedReportId;
    const fallback = reports.find((report) => report.id === selectedReportId);
    const loadFallback = wasSelected && fallback && !Array.isArray(fallback.blocks);
    if (wasSelected) confirmedReportId = selectedReportId;
    store.setState({ reports, selectedReportId, loadingReportId: loadFallback ? selectedReportId : null, reportSelectionError: "", reportDismissError: "" });
    if (loadFallback) {
      ensureReportCached(selectedReportId, fallback.modified_at).then((report) => {
        const current = store.getState();
        if (!report || current.selectedReportId !== selectedReportId || reportSelectionRevision !== revision) return;
        store.setState({ reports: hydrateReportsWithCache(current.reports || []), loadingReportId: null });
      }).catch((error) => {
        if (store.getState().selectedReportId === selectedReportId && reportSelectionRevision === revision) {
          store.setState({ loadingReportId: null, reportSelectionError: `Could not load report: ${error.message || error}` });
        }
      });
    }
    try {
      await api.dismissReport(reportId);
      reportCache.delete(reportId);
      pendingReportDismissals.delete(reportId);
      reportEpoch += 1;
      void refreshAll(true);
    } catch (error) {
      pendingReportDismissals.delete(reportId);
      reportEpoch += 1;
      const current = store.getState();
      const restored = [...(current.reports || [])];
      if (!restored.some((report) => report.id === reportId)) {
        restored.splice(Math.min(index, restored.length), 0, removed);
      }
      const restoreSelection = wasSelected && reportSelectionRevision === revision;
      if (restoreSelection) confirmedReportId = reportId;
      store.setState({ reports: restored,
        selectedReportId: restoreSelection ? reportId : current.selectedReportId,
        loadingReportId: restoreSelection ? null : current.loadingReportId,
        reportDismissError: `Could not dismiss report: ${error.message}`,
      });
      void refreshAll(true);
    }
  },
  async onSubmitReportAnswer(reportId, values) {
    try {
      const ansRes = await api.submitReportAnswer(reportId, values, async (requestId, retry) => {
        const answerId = retry ? (await api.getReportAnswerRequest(reportId, requestId)).answer_id : null;
        const reportAnswerErrors = { ...(store.getState().reportAnswerErrors || {}) };
        delete reportAnswerErrors[reportId];
        store.setState({ reportAnswerErrors, pendingReportAnswers: {
          ...(store.getState().pendingReportAnswers || {}),
          [reportId]: { reportId, values, requestId, answerId },
        } });
      });
      if (ansRes?.report) {
        const cached = reportCache.get(reportId);
        const confirmed = cached && isNewerReportVersion(cached.modified_at, ansRes.report.modified_at)
          ? cached.report : ansRes.report;
        reportCache.set(reportId, {
          modified_at: confirmed.modified_at,
          report: confirmed,
        });
        const reports = (store.getState().reports || []).map((report) =>
          report.id === reportId ? confirmed : report
        );
        const pendingReportAnswers = { ...(store.getState().pendingReportAnswers || {}) };
        delete pendingReportAnswers[reportId];
        store.setState({ reports, pendingReportAnswers });
      }
      void refreshAll(true);
    } catch (err) {
      const pendingReportAnswers = { ...(store.getState().pendingReportAnswers || {}) };
      delete pendingReportAnswers[reportId];
      store.setState({ pendingReportAnswers, reportAnswerErrors: {
        ...(store.getState().reportAnswerErrors || {}), [reportId]: err.message,
      } });
      throw err;
    }
  },
};

function maybeMarkAckSeen(report) {
  if (!report || report.ack_seen) return;
  const answers = report.answers || [];
  const latest = answers[answers.length - 1];
  if (!latest || latest.status !== "acked") return;
  const key = `${report.id}:${latest.id}:${(latest.acks || []).length}`;
  if (ackSeenRequests.has(key)) return;
  ackSeenRequests.add(key);
  api
    .markReportAckSeen(report.id)
    .then((res) => {
      if (res?.report) {
        reportCache.set(report.id, {
          modified_at: res.report.modified_at,
          report: res.report,
        });
        const hydrated = hydrateReportsWithCache(store.getState().reports || []);
        store.setState({ reports: hydrated });
      }
    })
    .catch(() => {
      ackSeenRequests.delete(key);
    });
}

function handleReportAcked(event) {
  const report = event?.report;
  if (!report?.id) {
    refreshAll(false);
    return;
  }
  reportCache.set(report.id, {
    modified_at: report.modified_at,
    report,
  });
  const reports = store.getState().reports || [];
  const nextReports = reports.some((r) => r.id === report.id)
    ? reports.map((r) => (r.id === report.id ? report : r))
    : [...reports, report];
  lastDataSignature = "";
  store.setState({ reports: nextReports });
}

function markVisibleReport(state) {
  const id = state.activeTab === "reports" && !document.hidden && state.loadingReportId !== state.selectedReportId
    ? state.selectedReportId : null;
  if (!id) {
    if (reportViewTimer !== null) window.clearTimeout(reportViewTimer);
    reportViewTimer = null;
    lastReportView = { id: null, at: 0 };
    return;
  }
  if (reportViewTimer === null) {
    reportViewTimer = window.setTimeout(() => {
      reportViewTimer = null;
      markVisibleReport(store.getState());
    }, REPORT_VIEW_INTERVAL);
  }
  const now = Date.now();
  if (lastReportView.id === id && now - lastReportView.at < REPORT_VIEW_INTERVAL) return;
  lastReportView = { id, at: now };
  const requestedVersion = (state.reports || []).find((report) => report.id === id)?.modified_at;
  api.markReportSeen(id).then((result) => {
    if (!result?.report) return;
    const current = store.getState();
    const existing = (current.reports || []).find((report) => report.id === id);
    if (!existing || existing.modified_at !== requestedVersion) return;
    reportCache.set(id, { modified_at: result.report.modified_at, report: result.report });
    const reports = current.reports.map((report) => report.id === id ? result.report : report);
    store.setState({ reports });
  }).catch(() => {
    if (lastReportView.id === id && lastReportView.at === now) lastReportView = { id: null, at: 0 };
  });
}

function reportNeedsAnswer(report) {
  return Boolean(
    report.needs_answer ??
      ((report.fields || []).length > 0 && (report.answers || []).length === 0)
  );
}

function renderApp(state) {
  cleanupTimers();

  const reports = state.reports || [];
  const activeReports = reports.filter((report) => report && report.status !== "dismissed");
  const hasUnreadReport = activeReports.some((report) => !report.seen);
  const hasUnseenAck = activeReports.some((report) => {
    const answers = report.answers || [];
    return answers[answers.length - 1]?.status === "acked" && !report.ack_seen;
  });
  const needsAnswer = activeReports.some(reportNeedsAnswer);
  const reportQuestion = document.getElementById("report-needs-answer");
  if (reportQuestion) {
    reportQuestion.hidden = !needsAnswer;
  }

  const reportPip = document.getElementById("report-pip");
  if (reportPip) {
    reportPip.hidden = needsAnswer || (!hasUnreadReport && !hasUnseenAck);
    reportPip.classList.toggle("is-unseen-ack", !hasUnreadReport && hasUnseenAck);
    const label = hasUnreadReport
      ? "Unread report"
      : hasUnseenAck ? "Unseen answer acknowledgement" : "No unread reports";
    reportPip.setAttribute("aria-label", label);
  }

  for (const btn of document.querySelectorAll(".tab-btn")) {
    const active = btn.dataset.tab === state.activeTab;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  }

  const messagesEl = document.getElementById("view-messages");
  const tasksEl = document.getElementById("view-tasks");
  const reportsEl = document.getElementById("view-reports");

  if (messagesEl) messagesEl.hidden = state.activeTab !== "messages";
  if (tasksEl) tasksEl.hidden = state.activeTab !== "tasks";
  if (reportsEl) reportsEl.hidden = state.activeTab !== "reports";
  if (state.activeTab === "reports") syncReportRailTop();
  markVisibleReport(state);

  if (state.activeTab !== "reports") lastReportRenderSignature = null;
  if (state.activeTab === "messages" && messagesEl) {
    renderMessagesView(messagesEl, state, handlers);
    paintConnection(messagesEl, state.connection);
    highlightCodeBlocks(messagesEl);
    hydrateMermaidBlocks(messagesEl);
  } else if (state.activeTab === "tasks" && tasksEl) {
    renderTasksView(tasksEl, state, handlers);
  } else if (state.activeTab === "reports" && reportsEl) {
    const visibleReports = reports.map((report) => {
      const visible = { ...report };
      delete visible.modified_at;
      delete visible.last_viewed_at;
      return visible;
    });
    const reportId = reports.some(report => report.id === state.selectedReportId) ? state.selectedReportId : reports[0]?.id;
    const linkedTaskIds = (state.tasks || []).filter(task =>
      (task.links?.report_ids || task.report_ids || []).includes(reportId)
    ).map(task => task.id);
    const signature = JSON.stringify([state.selectedReportId, state.loadingReportId, state.reportSelectionError, state.reportDismissError, visibleReports, state.replyParentRefs, state.pendingReportAnswers, state.reportAnswerErrors, linkedTaskIds]);
    if (signature !== lastReportRenderSignature) {
      lastReportRenderSignature = signature;
      renderReportsView(reportsEl, state, handlers);
      highlightCodeBlocks(reportsEl);
      hydrateMermaidBlocks(reportsEl);
    }
    maybeMarkAckSeen(reports.find((r) => r.id === state.selectedReportId));
  }

  cleanupTimers = mountRelativeTimers(document);
}

function initImageModal() {
  const dialog = document.getElementById("image-modal");
  const stage = document.getElementById("image-modal-stage");
  const close = document.getElementById("image-modal-close");
  if (!dialog || !stage || !close) return;
  let scale = 1;
  let x = 0;
  let y = 0;
  let drag = null;
  let panDistance = 0;
  let viewerSeq = 0;
  const pointers = new Map();
  let pinch = null;
  const pinchDistance = () => {
    const [a, b] = [...pointers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y) || 1;
  };
  const update = () => {
    const media = stage.firstElementChild;
    if (media) media.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  };
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    stage.replaceChildren();
    drag = null;
    pointers.clear();
    pinch = null;
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || (event.target === stage && panDistance <= 4)) dialog.close();
  });
  stage.addEventListener("dragstart", (event) => event.preventDefault());
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    scale = Math.min(8, Math.max(0.25, scale * Math.exp(-event.deltaY * 0.001)));
    update();
  }, { passive: false });
  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      drag = null;
      pinch = { distance: pinchDistance(), scale };
      panDistance = Number.POSITIVE_INFINITY;
      return;
    }
    drag = {
      pointer: event.pointerId,
      x: event.clientX - x,
      y: event.clientY - y,
      startX: event.clientX,
      startY: event.clientY,
    };
    panDistance = 0;
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch && pointers.size >= 2) {
      scale = Math.min(8, Math.max(0.25, (pinch.scale * pinchDistance()) / pinch.distance));
      update();
      return;
    }
    if (!drag || drag.pointer !== event.pointerId) return;
    panDistance = Math.max(
      panDistance,
      Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY),
    );
    x = event.clientX - drag.x;
    y = event.clientY - drag.y;
    update();
  });
  const endDrag = (event) => {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinch = null;
    if (drag?.pointer === event.pointerId) drag = null;
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!target.closest?.("#view-messages, #view-reports")) return;
    const media = target.closest(".mermaid-diagram svg, .mermaid-block svg, img")
      || target.closest(".mermaid-diagram")?.querySelector("svg")
      || target.closest("button.message-attachment-preview")?.querySelector("img");
    if (!media) return;
    event.preventDefault();
    scale = 1;
    x = 0;
    y = 0;
    const clone = media.cloneNode(true);
    const sourceId = clone.getAttribute("id");
    clone.removeAttribute("id");
    clone.setAttribute("draggable", "false");
    if (media.tagName.toLowerCase() === "svg") {
      const sourceBox = media.getBoundingClientRect();
      const existingClass = clone.getAttribute("class") || "";
      clone.setAttribute(
        "class",
        existingClass.split(/\s+/).includes("mermaid-svg")
          ? existingClass
          : `mermaid-svg ${existingClass}`.trim(),
      );
      const viewBox = (clone.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
      const naturalWidth = viewBox.length === 4 && viewBox[2] > 0 ? viewBox[2] : sourceBox.width;
      if (viewBox.length === 4 && viewBox[2] > 0) {
        if (clone.style.removeProperty) clone.style.removeProperty("max-width");
        else delete clone.style.maxWidth;
      }
      if (naturalWidth) clone.style.width = `${Math.round(naturalWidth)}px`;
      // Mermaid scopes styles and marker references to diagram IDs, so each viewer copy rewrites its IDs.
      if (sourceId) {
        const viewerId = `${sourceId}-viewer-${++viewerSeq}`;
        const ids = new Map([[sourceId, viewerId]]);
        const inner = clone.querySelectorAll ? [...clone.querySelectorAll("[id]")] : [];
        for (const element of inner) {
          const id = element.getAttribute?.("id") || element.id;
          if (id) ids.set(id, `${viewerId}-${id}`);
        }
        const rewriteRefs = (value) =>
          String(value).replace(/#([\w-]+)/g, (match, id) => (ids.has(id) ? `#${ids.get(id)}` : match));
        clone.setAttribute("id", viewerId);
        if (clone.querySelectorAll) {
          for (const element of [clone, ...clone.querySelectorAll("*")]) {
            for (const attribute of [...(element.attributes || [])]) {
              if (attribute.name === "id" && ids.has(attribute.value)) {
                element.setAttribute("id", ids.get(attribute.value));
              } else {
                element.setAttribute(attribute.name, rewriteRefs(attribute.value));
              }
            }
            if (element.localName === "style") {
              element.textContent = rewriteRefs(element.textContent || "");
            }
          }
        } else {
          const style = clone.querySelector("style");
          if (style) style.textContent = rewriteRefs(style.textContent || "");
        }
      }
    }
    stage.replaceChildren(clone);
    update();
    dialog.showModal();
  });
}

function renderBlockedBashEntry(entry, onDismiss, canDismiss) {
  const article = document.createElement("article");
  article.className = "blocked-bash-entry";
  const fields = document.createElement("dl");
  for (const [key, label] of [
    ["at", "Time (UTC)"],
    ["line", "Full line"],
    ["command", "Command"],
    ["blocked_by", "Reason"],
  ]) {
    const name = document.createElement("dt");
    name.textContent = label;
    const value = document.createElement("dd");
    value.dataset.field = key;
    value.textContent = entry[key];
    fields.append(name, value);
  }
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "btn btn-secondary audit-row-dismiss";
  dismiss.textContent = "Dismiss";
  dismiss.disabled = !canDismiss;
  dismiss.setAttribute("aria-label", `Dismiss blocked Bash call at ${entry.at}`);
  dismiss.addEventListener("click", () => void onDismiss(entry, article));
  article.append(fields, dismiss);
  return article;
}

async function openBlockedBashLog(trigger) {
  const closed = modal.open("blocked-bash-log", { title: "Blocked Bash log", trigger });
  const status = document.getElementById("blocked-bash-status");
  const log = document.getElementById("blocked-bash-log");
  const shell = document.getElementById("modal-shell");
  void closed.then(() => log.replaceChildren());

  const refresh = async (focusAfter = false) => {
    const { entries } = await api.listBlockedBash();
    if (!log.isConnected || !shell.open) return;
    const canDismiss = entries.every((entry) => /^[0-9a-f]{64}$/.test(entry.id));
    status.textContent = !canDismiss
      ? "Log entries are readable, but dismissal requires a preview server restart."
      : entries.length
        ? `${entries.length} blocked Bash ${entries.length === 1 ? "call" : "calls"} recorded locally.`
        : "No blocked Bash calls recorded locally.";
    log.replaceChildren(...entries.map((entry) => renderBlockedBashEntry(entry, dismissEntry, canDismiss)));
    if (focusAfter) (log.querySelector(".audit-row-dismiss") || log).focus();
  };
  const dismissEntry = async (entry, row) => {
    const buttons = log.querySelectorAll(".audit-row-dismiss");
    const next = row.nextElementSibling;
    buttons.forEach((button) => { button.disabled = true; });
    row.remove();
    status.textContent = "Dismissing local log entry…";
    log.focus();
    let deleted = false;
    try {
      await api.dismissBlockedBash(entry.id);
      deleted = true;
      await refresh(true);
    } catch (error) {
      if (status.isConnected && shell.open) {
        status.textContent = deleted
          ? `Entry dismissed, but the log could not refresh: ${error.message}. Close and reopen.`
          : `Could not dismiss the local log entry: ${error.message}. Click to retry.`;
        if (!deleted) {
          log.insertBefore(row, next?.parentNode === log ? next : null);
          buttons.forEach((button) => { button.disabled = false; });
          row.querySelector(".audit-row-dismiss")?.focus();
        }
      }
    }
  };
  try {
    await refresh();
  } catch (error) {
    if (status.isConnected && shell.open) {
      status.textContent = `Could not read the local log: ${error.message}. Close and reopen to retry.`;
    }
  }
}

function openViolationForm(health, trigger, draft = null, failure = null) {
  const tab = store.getState().activeTab;
  const types = health.violation_types?.[tab];
  if (!Array.isArray(types) || !types.length) {
    throw new Error("Violation types are unavailable. Click to retry.");
  }
  const reportId = tab === "reports" ? store.getState().selectedReportId : null;
  modal.open("violation-flag", { title: "Flag violation", trigger });
  const form = document.getElementById("violation-form");
  const reason = document.getElementById("violation-reason");
  const clause = document.getElementById("violation-clause-file");
  const msg = document.getElementById("violation-msg");
  const submit = document.getElementById("violation-submit");
  const errorText = document.getElementById("violation-error");
  reason.replaceChildren(...types.map((type) =>
    new Option(type.replaceAll("-", " ").replace(/^./, (char) => char.toUpperCase()), type)
  ));
  clause.replaceChildren(
    new Option("No clause file", ""),
    ...(Array.isArray(health.read_set) ? health.read_set : []).map((path) => new Option(path, path)),
  );
  if (draft) {
    reason.value = draft.reason;
    clause.value = draft.clause_file || "";
    msg.value = draft.msg || "";
  }
  if (failure) {
    errorText.textContent = `Could not send flag: ${failure.message}. Click to retry.`;
    errorText.hidden = false;
  }
  submit.addEventListener("click", async () => {
    if (submit.disabled) return;
    errorText.hidden = true;
    if (!reason.value) {
      errorText.textContent = "Choose a violation type.";
      errorText.hidden = false;
      return;
    }
    const payload = {
      armed: true,
      tab,
      reason: reason.value,
      clause_file: clause.value || null,
      msg: msg.value.trim() || null,
    };
    if (tab === "reports" && reportId) payload.report_id = reportId;
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.textContent = "Sending flag…";
    optimisticViolation = { agent_interrupt_pending: true, violation_type: payload.reason };
    paintViolationFlag(optimisticViolation);
    try {
      const response = await api.flagViolation(payload);
      optimisticViolation = null;
      confirmedViolation = response;
      appliedHealthRequestId = ++healthRequestId;
      paintViolationFlag(response);
      const shell = document.getElementById("modal-shell");
      if (shell.open && shell.querySelector("#violation-form") === form) shell.close("confirm");
      void refreshAll(true);
    } catch (error) {
      optimisticViolation = null;
      paintViolationFlag(confirmedViolation);
      if (!form.isConnected && !document.getElementById("modal-shell").open) {
        openViolationForm(health, trigger, payload, error);
      } else {
        errorText.textContent = `Could not send flag: ${error.message}. Click to retry.`;
        errorText.hidden = false;
      }
    } finally {
      submit.disabled = false;
      submit.setAttribute("aria-busy", "false");
      submit.textContent = "Send flag";
    }
  });
}

function lockMobilePortrait() {
  if (!window.matchMedia?.("(pointer: coarse), (max-width: 48em)")?.matches) return;
  const requestLock = () => {
    try {
      const result = window.screen?.orientation?.lock?.("portrait");
      if (result && typeof result.catch === "function") result.catch(() => {});
    } catch {
      // Browsers outside installed or fullscreen mobile contexts reject orientation locks.
    }
  };
  requestLock();
  window.addEventListener("pointerdown", requestLock, { once: true, passive: true });
}

function initEvents() {
  lockMobilePortrait();
  initImageModal();
  syncViewportHeight();
  window.addEventListener("resize", syncViewportHeight);
  window.visualViewport?.addEventListener("resize", syncViewportHeight);
  window.visualViewport?.addEventListener("scroll", syncViewportHeight);
  document.addEventListener("visibilitychange", () => markVisibleReport(store.getState()));

  const isEditableTarget = (target) =>
    Boolean(
      target?.isContentEditable ||
        target?.closest?.("input, textarea, select, [contenteditable='true']")
    );
  window.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Tab") {
        document.documentElement.setAttribute("data-keyboard-nav", "true");
        return;
      }
      if (isEditableTarget(event.target)) return;
      if (
        event.key.startsWith("Arrow") ||
        event.key === "Enter" ||
        event.key === " "
      ) {
        document.documentElement.setAttribute("data-keyboard-nav", "true");
      }
    },
    true
  );
  window.addEventListener(
    "pointerdown",
    () => {
      document.documentElement.removeAttribute("data-keyboard-nav");
    },
    true
  );

  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.addEventListener("click", () => {
      store.setState({ activeTab: btn.dataset.tab });
    });
  }

  document.getElementById("blocked-bash-open")?.addEventListener("click", (event) => {
    void openBlockedBashLog(event.currentTarget);
  });

  const interruptBtn = document.getElementById("agent-interrupt");
  interruptBtn?.addEventListener("click", async () => {
    if (interruptBtn.disabled) return;
    interruptBtn.disabled = true;
    interruptBtn.setAttribute("aria-busy", "true");
    setViolationStatus("");
    const wasFlagged = interruptBtn.getAttribute("aria-pressed") === "true";
    if (wasFlagged) {
      optimisticViolation = { agent_interrupt_pending: false };
      paintViolationFlag(optimisticViolation);
    }
    try {
      const health = await getHealthWithConnection();
      if (health.agent_interrupt_pending) {
        const response = await api.flagViolation({ armed: false });
        optimisticViolation = null;
        confirmedViolation = response;
        appliedHealthRequestId = ++healthRequestId;
        paintViolationFlag(response);
        await refreshAll(true);
      } else if (wasFlagged) {
        optimisticViolation = null;
        paintViolationFlag(health);
      } else {
        openViolationForm(health, interruptBtn);
      }
    } catch (error) {
      optimisticViolation = null;
      paintViolationFlag(confirmedViolation);
      setViolationStatus(`Could not update flag: ${error.message}. Click to retry.`);
    } finally {
      interruptBtn.disabled = false;
      interruptBtn.setAttribute("aria-busy", "false");
    }
  });

  const preloadReport = (event) => {
    const target = event.target.closest?.(".report-picker-link[data-report-id], [data-reference-type=report]");
    const reportId = target?.dataset.reportId || target?.dataset.referenceId;
    if (!reportId) return;
    const summary = (store.getState().reports || []).find((item) => item.id === reportId);
    ensureReportCached(reportId, summary?.modified_at, { quiet: true }).catch(console.error);
  };
  document.addEventListener("pointerover", preloadReport);
  document.addEventListener("focusin", preloadReport);

  document.addEventListener("click", async (event) => {
    const reference = event.target.closest?.("[data-reference-type]");
    if (reference) {
      await handlers.onNavigateReference(reference.dataset.referenceType, reference.dataset.referenceId);
      return;
    }
    const parent = event.target.closest?.("button[data-parent-type][data-parent-id]");
    if (parent) {
      const { parentType, parentId } = parent.dataset;
      const idCode = parent.querySelector("code.msg-id");
      if (event.shiftKey && idCode) {
        const copied = copyText(parentId, parent);
        const originalLabel = parent.dataset.copyOriginalLabel ?? parent.getAttribute("aria-label") ?? "";
        parent.dataset.copyOriginalLabel = originalLabel;
        idCode.dataset.state = copied ? "good" : "bad";
        parent.setAttribute("aria-label", copied ? "ID copied" : "Copy ID failed");
        clearTimeout(parent.copyResetTimer);
        parent.copyResetTimer = setTimeout(() => {
          if (!idCode.isConnected) return;
          delete idCode.dataset.state;
          if (originalLabel) parent.setAttribute("aria-label", originalLabel);
          else parent.removeAttribute("aria-label");
          delete parent.dataset.copyOriginalLabel;
        }, 1000);
        return;
      }
      handlers.onToggleReplyParent(parentType, parentId);
      if (parentType !== "message") {
        store.setState({ activeTab: "messages" });
        document.querySelector("#view-messages #msg")?.focus();
      }
      return;
    }
    const mermaidBtn = event.target.closest?.(".preview-code-mermaid");
    if (mermaidBtn) {
      toggleCodeMermaid(mermaidBtn);
      return;
    }
    const copyBtn = event.target.closest?.(".copy-code");
    if (copyBtn) {
      const code = (copyBtn.dataset.code || "").replace(/&#10;/g, "\n");
      const originalHtml = copyBtn.copyOriginalHtml ?? copyBtn.innerHTML;
      copyBtn.copyOriginalHtml = originalHtml;
      const copied = copyText(code, copyBtn);
      copyBtn.dataset.state = copied ? "good" : "bad";
      copyBtn.setAttribute("aria-label", copied ? "Code copied" : "Copy code failed");
      if (copied) copyBtn.innerHTML = CHECK_SVG;
      else copyBtn.innerHTML = originalHtml;
      clearTimeout(copyBtn.copyResetTimer);
      copyBtn.copyResetTimer = setTimeout(() => {
        if (copyBtn.isConnected) {
          delete copyBtn.dataset.state;
          copyBtn.innerHTML = originalHtml;
          copyBtn.setAttribute("aria-label", "Copy code");
          delete copyBtn.copyOriginalHtml;
        }
      }, 1400);
      return;
    }
  });
}

let activeTooltip = null;
let pendingTooltip = null;
let lastTooltipClosedAt = -Infinity;
const tooltipCloseTimers = new WeakMap();
const TOOLTIP_DELAY = 500;
const TOOLTIP_ANIMATION = 150;

function cancelPendingTooltip() {
  if (!pendingTooltip) return;
  clearTimeout(pendingTooltip.timer);
  pendingTooltip = null;
}

function hideTitleTooltip() {
  cancelPendingTooltip();
  if (!activeTooltip) return;
  const { target, title, describedBy, addedLabel, tip, kind } = activeTooltip;
  activeTooltip = null;
  lastTooltipClosedAt = performance.now();
  if (kind === "title") {
    if (!target.hasAttribute("title")) target.setAttribute("title", title);
    if (addedLabel && target.getAttribute("aria-label") === title) target.removeAttribute("aria-label");
    if (describedBy === null) target.removeAttribute("aria-describedby");
    else target.setAttribute("aria-describedby", describedBy);
    tip.removeAttribute("id");
    tip.removeAttribute("role");
    tip.setAttribute("aria-hidden", "true");
  } else {
    tip.classList.remove("tooltip-active");
  }
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    if (kind === "title") tip.remove();
    return;
  }
  tip.classList.add("tooltip-closing");
  const timer = setTimeout(() => {
    tip.classList.remove("tooltip-closing");
    if (kind === "title") tip.remove();
    tooltipCloseTimers.delete(tip);
  }, TOOLTIP_ANIMATION);
  tooltipCloseTimers.set(tip, timer);
}

function positionTitleTooltip(target, tip) {
  const rect = target.getBoundingClientRect();
  const box = tip.getBoundingClientRect();
  const left = Math.max(8, Math.min(rect.left + rect.width / 2 - box.width / 2, innerWidth - box.width - 8));
  const below = rect.bottom + 8;
  const fitsAbove = rect.top - box.height - 8 >= 8;
  const fitsBelow = below + box.height <= innerHeight - 8;
  const above = fitsAbove || (!fitsBelow && rect.top > innerHeight - rect.bottom);
  tip.classList.toggle("above", above);
  tip.style.left = `${left}px`;
  tip.style.top = `${above ? Math.max(8, rect.top - box.height - 8) : below}px`;
  tip.style.setProperty("--arrow-left", `${Math.max(8, Math.min(box.width - 8, rect.left + rect.width / 2 - left))}px`);
}

function showTitleTooltip(target) {
  if (!target || target.dataset.tooltip || activeTooltip?.target === target) return;
  hideTitleTooltip();
  const title = target.getAttribute("title")?.trim();
  if (!title) return;
  const describedBy = target.getAttribute("aria-describedby");
  const addedLabel = !target.getAttribute("aria-label") && !target.getAttribute("aria-labelledby") &&
    !target.textContent.trim() && !target.getAttribute("alt") && !target.querySelector("img[alt]") &&
    !target.labels?.length && !(target instanceof HTMLInputElement && target.value);
  if (addedLabel) target.setAttribute("aria-label", title);
  const tip = document.createElement("div");
  tip.id = "arena-title-tooltip";
  tip.className = "arena-tooltip";
  tip.setAttribute("role", "tooltip");
  tip.textContent = title;
  target.removeAttribute("title");
  target.setAttribute("aria-describedby", [describedBy, tip.id].filter(Boolean).join(" "));
  (target.closest("dialog[open]") || document.body).append(tip);
  positionTitleTooltip(target, tip);
  activeTooltip = { target, title, describedBy, addedLabel, tip, kind: "title" };
}

function positionSurveyTooltip(trigger) {
  const tip = trigger?.querySelector(".survey-answer-tooltip");
  const body = trigger?.closest(".modal-body");
  if (!tip || !body) return;
  tip.style.left = "";
  tip.classList.add("above");
  const box = tip.getBoundingClientRect();
  const bodyRect = body.getBoundingClientRect();
  if (box.top < bodyRect.top) tip.classList.remove("above");
  const nextBox = tip.getBoundingClientRect();
  const maxRight = Math.min(bodyRect.right, window.innerWidth - 8);
  const minLeft = Math.max(bodyRect.left, 8);
  if (nextBox.right > maxRight) {
    tip.style.left = `${Math.round(maxRight - nextBox.right)}px`;
  } else if (nextBox.left < minLeft) {
    tip.style.left = `${Math.round(minLeft - nextBox.left)}px`;
  }
}

function showSurveyTooltip(target) {
  const tip = target?.querySelector(".survey-answer-tooltip");
  if (!tip) return;
  const timer = tooltipCloseTimers.get(tip);
  if (timer) clearTimeout(timer);
  tooltipCloseTimers.delete(tip);
  tip.classList.remove("tooltip-closing");
  tip.classList.add("tooltip-active");
  positionSurveyTooltip(target);
  activeTooltip = { target, tip, kind: "survey" };
}

function requestTooltip(target, kind, immediate = false) {
  if (!target) return;
  if (activeTooltip?.target === target) {
    cancelPendingTooltip();
    return;
  }
  if (pendingTooltip?.target === target && !immediate) return;
  const showNow = immediate || Boolean(activeTooltip) || performance.now() - lastTooltipClosedAt <= TOOLTIP_DELAY;
  hideTitleTooltip();
  const show = () => {
    pendingTooltip = null;
    if (!target.isConnected) return;
    if (kind === "survey") showSurveyTooltip(target);
    else showTitleTooltip(target);
  };
  if (showNow) show();
  else pendingTooltip = { target, timer: setTimeout(show, TOOLTIP_DELAY) };
}

function tooltipTarget(node) {
  const survey = node?.closest?.(".survey-id-wrap");
  if (survey?.querySelector(".survey-answer-tooltip")) return { target: survey, kind: "survey" };
  const title = node?.closest?.("[title]");
  if (title && !title.dataset.tooltip) return { target: title, kind: "title" };
  if (activeTooltip?.target.contains(node)) return { target: activeTooltip.target, kind: activeTooltip.kind };
  return null;
}

let tooltipPointerType = "mouse";
for (const name of ["pointerdown", "pointermove"]) {
  document.addEventListener(name, event => { tooltipPointerType = event.pointerType; });
}
document.addEventListener("mouseover", (event) => {
  if (tooltipPointerType === "touch") return;
  const match = tooltipTarget(event.target);
  if (match) requestTooltip(match.target, match.kind);
  else if (activeTooltip && !activeTooltip.target.contains(event.target)) hideTitleTooltip();
});
document.addEventListener("mouseout", (event) => {
  const target = pendingTooltip?.target || activeTooltip?.target;
  if (!target) return;
  if (event.relatedTarget instanceof Node && target.contains(event.relatedTarget)) return;
  if (target.contains(document.activeElement)) return;
  hideTitleTooltip();
});
document.addEventListener("focusin", (event) => {
  if (!event.target.matches?.(":focus-visible")) return;
  const match = tooltipTarget(event.target);
  if (match) requestTooltip(match.target, match.kind, true);
});
document.addEventListener("focusout", () => hideTitleTooltip());
document.addEventListener("click", (event) => {
  const trigger = event.target.closest?.(".survey-id-trigger");
  const target = trigger?.closest(".survey-id-wrap");
  if (target?.querySelector(".survey-answer-tooltip")) {
    const wasOpen = activeTooltip?.target === target;
    hideTitleTooltip();
    if (!wasOpen) requestTooltip(target, "survey", true);
    return;
  }
  hideTitleTooltip();
}, true);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") hideTitleTooltip(); });
window.addEventListener("resize", hideTitleTooltip);
document.addEventListener("scroll", hideTitleTooltip, true);
new MutationObserver(() => {
  if (pendingTooltip && !pendingTooltip.target.isConnected) cancelPendingTooltip();
  if (!activeTooltip) return;
  const { target, tip, addedLabel, kind } = activeTooltip;
  if (!target.isConnected) {
    hideTitleTooltip();
    return;
  }
  if (kind !== "title" || !target.hasAttribute("title")) return;
  const title = target.getAttribute("title").trim();
  if (!title) {
    hideTitleTooltip();
    return;
  }
  activeTooltip.title = title;
  if (addedLabel) target.setAttribute("aria-label", title);
  target.removeAttribute("title");
  tip.textContent = title;
  positionTitleTooltip(target, tip);
}).observe(document.body, { attributes: true, attributeFilter: ["title"], childList: true, subtree: true });

store.subscribe(renderApp);
initEvents();
refreshAll(true);
try {
  if (sessionStorage.getItem(SURVEY_RELOAD_KEY) === "1") {
    sessionStorage.removeItem(SURVEY_RELOAD_KEY);
    void handlers.onOpenSurvey(null);
  }
} catch {
  // A reload without session storage starts with the modal closed.
}
// Agent heartbeats live in a separate process, so refresh only this small health signal.
setInterval(() => { void getHealthWithConnection().catch(() => {}); }, 3000);

connectWebSocket({
  onChange: () => refreshAll(false),
  onReload: reloadPreservingSurvey,
  onReportAcked: handleReportAcked,
});
