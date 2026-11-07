// REST and WebSocket client for Arena Steering endpoints.
// Relative paths keep the preview proxy working from any browser origin.

let liveSocket = null;
let socketSequence = 0;
let lastSocketMessage = 0;
let socketRetry = null;
const socketCalls = new Map();
let changeHandler = () => {};
let reloadHandler = () => {};
let reportAckedHandler = () => {};
let disconnectPollTimer = null;

function startFallbackPoll() {
  if (disconnectPollTimer) return;
  disconnectPollTimer = setInterval(() => {
    if (!liveSocket || liveSocket.readyState !== 1) {
      changeHandler();
    }
  }, 3000);
}

function stopFallbackPoll() {
  if (disconnectPollTimer) {
    clearInterval(disconnectPollTimer);
    disconnectPollTimer = null;
  }
}

export function connectWebSocket({ onChange, onReload, onReportAcked } = {}) {
  if (typeof onChange === "function") changeHandler = onChange;
  if (typeof onReload === "function") reloadHandler = onReload;
  if (typeof onReportAcked === "function") reportAckedHandler = onReportAcked;
  if (
    typeof WebSocket !== "function" ||
    (liveSocket && liveSocket.readyState < 2)
  ) {
    startFallbackPoll();
    return;
  }
  const url = new URL(window.location.href);
  if (url.searchParams.get("transport") === "poll") {
    startFallbackPoll();
    return;
  }
  url.pathname = "/api/ws";
  url.search = "";
  url.hash = "";
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  let socket;
  try {
    socket = new WebSocket(url);
  } catch {
    startFallbackPoll();
    return;
  }
  liveSocket = socket;
  const deadline = setTimeout(() => socket.close(), 10000);
  socket.addEventListener("open", () => {
    clearTimeout(deadline);
    lastSocketMessage = Date.now();
    stopFallbackPoll();
    changeHandler();
  });
  socket.addEventListener("message", (event) => {
    if (liveSocket !== socket) return;
    try {
      const value = JSON.parse(event.data);
      lastSocketMessage = Date.now();
      if (value.type === "heartbeat") {
        socket.send(JSON.stringify({ type: "pong" }));
        return;
      }
      if (value.type === "change") {
        changeHandler();
        return;
      }
      if (value.type === "report.acked") {
        reportAckedHandler(value);
        return;
      }
      if (value.type === "reload") {
        reloadHandler();
        return;
      }
      if (value.type !== "response") {
        throw new Error("Unknown WebSocket message");
      }
      const pending = socketCalls.get(value.id);
      if (!pending) return;
      socketCalls.delete(value.id);
      clearTimeout(pending.timer);
      const payload =
        typeof value.body === "string" && value.body
          ? JSON.parse(value.body)
          : {};
      if (value.status < 200 || value.status >= 300) {
        pending.reject(
          new Error(payload.error || `Request failed (${value.status})`)
        );
      } else {
        pending.resolve(payload);
      }
    } catch {
      socket.close(1002);
    }
  });
  socket.addEventListener("error", () => socket.close());
  socket.addEventListener("close", () => {
    clearTimeout(deadline);
    if (liveSocket !== socket) return;
    liveSocket = null;
    for (const pending of socketCalls.values()) {
      clearTimeout(pending.timer);
      pending.reject(new TypeError("WebSocket closed before a reply"));
    }
    socketCalls.clear();
    startFallbackPoll();
    clearTimeout(socketRetry);
    socketRetry = setTimeout(
      () =>
        connectWebSocket({
          onChange: changeHandler,
          onReload: reloadHandler,
          onReportAcked: reportAckedHandler,
        }),
      1000
    );
  });
}

setInterval(() => {
  if (liveSocket?.readyState === 1 && Date.now() - lastSocketMessage > 30000) {
    liveSocket.close();
  }
}, 5000);

function socketRequest(method, path, bodyStr, timeoutMs = 15000) {
  const socket = liveSocket;
  const id = String(++socketSequence);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      socketCalls.delete(id);
      reject(new TypeError("WebSocket reply timed out"));
      socket.close();
    }, timeoutMs);
    socketCalls.set(id, { resolve, reject, timer });
    try {
      socket.send(
        JSON.stringify({
          id,
          path,
          method,
          headers: { "Content-Type": "application/json" },
          body: bodyStr || "",
        })
      );
    } catch (error) {
      socketCalls.delete(id);
      clearTimeout(timer);
      reject(error);
      socket.close();
    }
  });
}

const PENDING_WRITES_KEY = "steer:pendingWrites";
let choosePendingWrite = async () => "cancel";

export function setPendingWriteChooser(chooser) {
  choosePendingWrite = chooser;
}

const activeWrites = new Set();
const pendingChoices = new Set();
let pendingWrites = [];
let pendingStorageAvailable = true;
try {
  const saved = JSON.parse(sessionStorage.getItem(PENDING_WRITES_KEY) || "[]");
  if (Array.isArray(saved)) pendingWrites = saved;
} catch {
  pendingStorageAvailable = false;
}

function savePendingWrites() {
  try {
    sessionStorage.setItem(PENDING_WRITES_KEY, JSON.stringify(pendingWrites));
  } catch {
    pendingStorageAvailable = false;
    throw new Error("Cannot safely send: browser session storage is unavailable");
  }
}

async function beginWrite(key, interactive = false) {
  if (!pendingStorageAvailable) throw new Error("Cannot safely send: browser session storage is unavailable");
  if (pendingChoices.has(key)) throw new Error("A retry decision is already open");
  const pending = pendingWrites.find((item) => item.key === key && !activeWrites.has(item.id));
  let id = pending?.id;
  if (id && interactive) {
    activeWrites.add(id);
    pendingChoices.add(key);
    let decision;
    try {
      decision = await choosePendingWrite();
    } finally {
      activeWrites.delete(id);
      pendingChoices.delete(key);
    }
    if (decision === "cancel" || !["retry", "new"].includes(decision)) {
      throw new Error("Action canceled");
    }
    if (decision === "new") id = null;
  }
  if (!id) {
    id = crypto.randomUUID();
    pendingWrites.push({ key, id });
    savePendingWrites();
  }
  activeWrites.add(id);
  return { id, retry: Boolean(pending && pending.id === id) };
}

function finishWrite(id, confirmed) {
  activeWrites.delete(id);
  if (confirmed) {
    pendingWrites = pendingWrites.filter((item) => item.id !== id);
    savePendingWrites();
  }
}

async function requestJson(method, path, body = undefined, interactive = false, onRequestId = null) {
  const write = ["POST", "PATCH", "DELETE"].includes(method) && path !== "/markdown";
  const writeInfo = write ? await beginWrite(JSON.stringify([method, path, body || {}]), interactive) : null;
  const id = writeInfo?.id || null;
  const requestBody = write ? { ...(body || {}), _request_id: id } : body;
  const bodyStr = requestBody !== undefined ? JSON.stringify(requestBody) : undefined;
  let confirmed = false;
  try {
    if (id && onRequestId) await onRequestId(id, writeInfo.retry);
    if (liveSocket?.readyState === 1) {
      try {
        const payload = await socketRequest(method, path, bodyStr);
        confirmed = true;
        return payload;
      } catch {
        // A lost response can mean the write succeeded. Reuse the request ID.
      }
    }
    const options = { method, headers: { "Content-Type": "application/json" } };
    if (bodyStr !== undefined) options.body = bodyStr;
    const response = await fetch(path, options);
    const payload = await response.json();
    if (!response.ok) {
      confirmed = response.status < 500;
      throw new Error(payload.error || `Request failed (${response.status})`);
    }
    confirmed = true;
    return payload;
  } finally {
    if (id) finishWrite(id, confirmed);
  }
}

async function requestForm(path, formData, file, { automaticRetry = false } = {}) {
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const digest = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const { id } = await beginWrite(JSON.stringify(["POST", path, file.name, digest]), !automaticRetry);
  formData.append("_request_id", id);
  let confirmed = false;
  try {
    const response = await fetch(path, { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) {
      confirmed = response.status < 500;
      throw new Error(payload.error || `Request failed (${response.status})`);
    }
    confirmed = true;
    return payload;
  } finally {
    finishWrite(id, confirmed);
  }
}

async function blockedBashRequest(method, path) {
  const response = await fetch(path, { method, cache: "no-store", mode: "same-origin" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

export const api = {
  getHealth() {
    return requestJson("GET", "/health");
  },
  setTimezone(timezone) {
    return requestJson("POST", "/timezone", { timezone });
  },
  listMessages({ all = true, hidden = false, query = "", before = "", requestId = "" } = {}) {
    const q = new URLSearchParams({
      all: all ? "1" : "0",
      hidden: hidden ? "1" : "0",
    });
    if (query) q.set("q", query);
    if (before) q.set("before", before);
    if (requestId) q.set("request_id", requestId);
    return requestJson("GET", `/messages?${q.toString()}`);
  },
  getMessage(id) {
    return requestJson("GET", `/messages/${encodeURIComponent(id)}`);
  },
  sendMessage({ body, hidden = false, blocking = true, parent_ids = [], parent_refs, request_id }) {
    return requestJson("POST", "/messages", { body, hidden, blocking, parent_ids, parent_refs, request_id });
  },
  markRepliesSeen(id, revision) {
    return requestJson("POST", `/messages/${encodeURIComponent(id)}/replies-seen`, { revision });
  },
  async uploadFile(file, options = {}) {
    const formData = new FormData();
    formData.append("file", file, file.name || "upload.bin");
    const payload = await requestForm("/uploads", formData, file, options);
    return payload.attachment;
  },
  sendMessageWithFile({
    body,
    hidden = false,
    blocking = true,
    parent_ids = [],
    parent_refs = undefined,
    attachments = [],
    pasted_text = undefined,
    request_id = undefined,
  }) {
    return requestJson("POST", "/messages/with-file", {
      body,
      hidden,
      blocking,
      parent_ids,
      parent_refs,
      attachments,
      pasted_text,
      request_id,
    });
  },
  skipPoll() {
    return requestJson("POST", "/poll/skip", {}, true);
  },
  listTasks({ full = true } = {}) {
    const q = new URLSearchParams({ full: full ? "1" : "0" });
    return requestJson("GET", `/tasks?${q.toString()}`);
  },
  getTask(id) {
    return requestJson("GET", `/tasks/${encodeURIComponent(id)}`);
  },
  createTask(task) {
    return requestJson("POST", "/tasks", task, true);
  },
  patchTask(taskId, changes) {
    return requestJson("PATCH", `/tasks/${encodeURIComponent(taskId)}`, changes, true);
  },
  deleteTask(taskId) {
    return requestJson("DELETE", `/tasks/${encodeURIComponent(taskId)}`, undefined, true);
  },
  listReports({ stale = false, summary = false } = {}) {
    const params = { stale: stale ? "1" : "0" };
    if (summary) params.summary = "1";
    const q = new URLSearchParams(params);
    return requestJson("GET", `/reports?${q.toString()}`);
  },
  getReport(reportId) {
    return requestJson("GET", `/reports/${encodeURIComponent(reportId)}`);
  },
  renderMarkdown(markdown, { breaks = true, preWrap = false } = {}) {
    const payload = { markdown, breaks };
    if (preWrap) payload.pre_wrap = true;
    return requestJson("POST", "/markdown", payload);
  },
  dismissReport(reportId) {
    return requestJson("DELETE", `/reports/${encodeURIComponent(reportId)}`, undefined, true);
  },
  markReportSeen(reportId) {
    return requestJson(
      "POST",
      `/reports/${encodeURIComponent(reportId)}/seen`,
      {}
    );
  },
  markReportAckSeen(reportId) {
    return requestJson(
      "POST",
      `/reports/${encodeURIComponent(reportId)}/ack-seen`,
      {}
    );
  },
  submitReportAnswer(reportId, values, onRequestId) {
    return requestJson(
      "POST",
      `/reports/${encodeURIComponent(reportId)}/answers`,
      { values },
      true,
      onRequestId
    );
  },
  getReportAnswerRequest(reportId, requestId) {
    return requestJson("GET", `/reports/${encodeURIComponent(reportId)}/answer-requests/${encodeURIComponent(requestId)}`);
  },
  getSurvey() {
    return requestJson("GET", "/survey");
  },
  submitSurvey(answers) {
    return requestJson("POST", "/survey", { answers }, true);
  },
  triggerBibleCheck(active = undefined) {
    const body = active === undefined ? {} : { active: Boolean(active) };
    return requestJson("POST", "/survey/interrupt", body, true);
  },
  async listBlockedBash() {
    // Keep the unredacted local audit off the WebSocket and state-sync paths.
    return blockedBashRequest("GET", "/blocked-bash");
  },
  async dismissBlockedBash(entryId) {
    return blockedBashRequest("DELETE", `/blocked-bash/${encodeURIComponent(entryId)}`);
  },
  flagViolation(payload) {
    return requestJson("POST", "/agent-interrupt", payload, true);
  },
};
