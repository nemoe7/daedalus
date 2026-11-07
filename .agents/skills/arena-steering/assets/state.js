// Reactive UI store for Arena Steering.

const TAB_STORAGE_KEY = "steer:activeTab";
const REPORT_STORAGE_KEY = "steer:selectedReportId";
const DRAFT_STORAGE_KEY = "steer:messageDraft";
const PENDING_SEND_KEY = "steer:pendingSend";
const COMPOSER_STATE_KEY = "steer:composerState";
const VALID_TABS = new Set(["messages", "tasks", "reports"]);

function readSessionValue(key) {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage.getItem(key) : null;
  } catch {
    return null;
  }
}

function writeSessionValue(key, value) {
  try {
    if (typeof sessionStorage === "undefined") return false;
    if (value == null || value === "") {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, String(value));
    }
    return true;
  } catch {
    return false;
  }
}

export function readComposerDraft() {
  try {
    if (typeof localStorage !== "undefined") {
      const draft = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (draft !== null) return draft;
    }
  } catch {
    // Fall back to the current tab when local storage is unavailable.
  }
  return readSessionValue(DRAFT_STORAGE_KEY) || "";
}

export function writeComposerDraft(value) {
  try {
    if (typeof localStorage !== "undefined") {
      if (value) localStorage.setItem(DRAFT_STORAGE_KEY, String(value));
      else localStorage.removeItem(DRAFT_STORAGE_KEY);
      writeSessionValue(DRAFT_STORAGE_KEY, null);
      return;
    }
  } catch {
    // Fall back to the current tab when local storage is unavailable.
  }
  writeSessionValue(DRAFT_STORAGE_KEY, value);
}

export function readPendingSend() {
  try {
    const value = JSON.parse(readSessionValue(PENDING_SEND_KEY) || "null");
    return value && typeof value.request_id === "string" && value.content && typeof value.rawDraft === "string" && Array.isArray(value.attachmentIds) ? value : null;
  } catch {
    return null;
  }
}

export function writePendingSend(value) {
  writeSessionValue(PENDING_SEND_KEY, value ? JSON.stringify(value) : null);
}

function readComposerState() {
  try {
    const saved = JSON.parse(readSessionValue(COMPOSER_STATE_KEY) || "{}");
    const parents = Array.isArray(saved.parents) ? saved.parents.filter(ref =>
      ref && ["message", "task", "report"].includes(ref.type) && typeof ref.id === "string" && ref.id) : [];
    const attachments = Array.isArray(saved.attachments) ? saved.attachments.filter(item =>
      item && typeof item.id === "string" && typeof item.name === "string" &&
      item.attachment && typeof item.attachment.path === "string"
    ).map(item => ({ ...item, status: "ready", file: { size: item.attachment.size_bytes || 0 },
      previewUrl: /^image\//i.test(item.type || "") && item.attachment.url?.startsWith("/static/uploads/") ? item.attachment.url : "" })) : [];
    return { parents, attachments };
  } catch {
    return { parents: [], attachments: [] };
  }
}

export function createStore(initial = {}) {
  const savedTab = readSessionValue(TAB_STORAGE_KEY);
  const savedReportId = readSessionValue(REPORT_STORAGE_KEY);
  const composer = readComposerState();
  let composerHadSavedState = composer.parents.length > 0 || composer.attachments.length > 0;
  const state = {
    activeTab: VALID_TABS.has(savedTab) ? savedTab : "messages",
    messages: [],
    searchQuery: "",
    messagesHasMore: false,
    messagesError: "",
    messagesLoading: false,
    tasks: [],
    reports: [],
    replyParentRefs: composer.parents,
    composerAttachments: composer.attachments,
    composerExpanded: false,
    composerError: "",
    pendingSend: readPendingSend(),
    optimisticMessage: null,
    sending: false,
    selectedReportId: savedReportId || null,
    loadingReportId: null,
    reportSelectionError: "",
    ...initial,
  };
  const listeners = new Set();

  return {
    getState() {
      return state;
    },
    setState(patch) {
      const change = typeof patch === "function" ? patch(state) : patch;
      Object.assign(state, change);
      if ("replyParentRefs" in change || "composerAttachments" in change) {
        const attachments = state.composerAttachments.filter(item => item.status === "ready").map(item => ({
          id: item.id, name: item.name, type: item.file?.type || item.type || "", attachment: item.attachment,
        }));
        const saved = { parents: state.replyParentRefs, attachments };
        const hasSavedState = saved.parents.length > 0 || attachments.length > 0;
        const savedSuccessfully = writeSessionValue(COMPOSER_STATE_KEY, hasSavedState ? JSON.stringify(saved) : null);
        state.composerPersistenceError = !savedSuccessfully && (hasSavedState || composerHadSavedState);
        if (savedSuccessfully) composerHadSavedState = hasSavedState;
      }
      if (VALID_TABS.has(state.activeTab)) {
        writeSessionValue(TAB_STORAGE_KEY, state.activeTab);
      }
      if (state.selectedReportId !== undefined) {
        writeSessionValue(REPORT_STORAGE_KEY, state.selectedReportId);
      }
      for (const listener of listeners) {
        listener(state);
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
