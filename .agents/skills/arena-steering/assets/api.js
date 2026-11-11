// REST and WebSocket client for Arena Steering endpoints.
// Relative paths keep the preview proxy working from any browser origin.

let liveSocket = null;
let socketSequence = 0;
let lastSocketMessage = 0;
let socketRetry = null;
// Bumped on every connect attempt so a superseded attempt cannot retry again.
let connectAttempt = 0;
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
    // Retry the upgrade whenever no socket is open or connecting. A closing
    // socket still counts as present, so `!liveSocket` alone would stall on poll.
    if (!liveSocket || liveSocket.readyState > 1) connectWebSocket();
  }, 3000);
}

function stopFallbackPoll() {
  if (disconnectPollTimer) {
    clearInterval(disconnectPollTimer);
    disconnectPollTimer = null;
  }
}

// Background tabs throttle timers, so a dead socket can leave the pill stale.
// Returning to the tab refreshes at once and retries the upgrade.
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    changeHandler();
    if (!liveSocket || liveSocket.readyState > 1) connectWebSocket();
  });
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
  // From here a new attempt supersedes any earlier one.
  const attempt = ++connectAttempt;
  clearTimeout(socketRetry);
  socketRetry = null;
  liveSocket = socket;
  // A half-open socket must not outlive the attempt, or the poll timer skips retrying.
  const deadline = setTimeout(() => socket.close(), 10000);
  socket.addEventListener("open", () => {
    clearTimeout(deadline);
    if (liveSocket !== socket) {
      socket.close();
      return;
    }
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
        const error = new Error(payload.error || `Request failed (${value.status})`);
        error.status = value.status;
        pending.reject(error);
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
    if (attempt !== connectAttempt) return;
    clearTimeout(socketRetry);
    socketRetry = setTimeout(() => {
      if (attempt === connectAttempt) connectWebSocket();
    }, 1000);
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

export function transportState() {
  // `attempted` keeps a page that never opened a transport out of the fault state.
  const mode = new URL(window.location.href).searchParams.get("transport") === "poll" ? "poll" : "ws";
  return {
    socket: Boolean(liveSocket && liveSocket.readyState === 1),
    poll: Boolean(disconnectPollTimer),
    attempted: Boolean(liveSocket || disconnectPollTimer),
    mode,
  };
}

export const AUTOMATIC_WRITE_RETRIES = 3;
const WRITE_RETRY_DELAYS_MS = [50, 100, 200];

function isRetryableWriteFailure(status) {
  return status == null || status === 408 || status === 429 || status >= 500;
}

function waitRetryDelay(attempt) {
  const delayMs = WRITE_RETRY_DELAYS_MS[attempt - 1] ?? WRITE_RETRY_DELAYS_MS[WRITE_RETRY_DELAYS_MS.length - 1];
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function parseJsonPayload(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function issueWriteToken() {
  const response = await fetch("/browser-write-token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const payload = await parseJsonPayload(response);
  if (!response.ok || typeof payload.request_id !== "string") {
    const error = new Error(payload.error || "Could not obtain a browser write token");
    error.status = response.status;
    throw error;
  }
  return payload.request_id;
}

async function performJsonRequest(method, path, body = undefined, retryOnFailure = false, onRequestId = null) {
  const write = ["POST", "PATCH", "DELETE"].includes(method) && path !== "/markdown";
  const id = write ? (body?.request_id || await issueWriteToken()) : null;
  const requestBody = write
    ? { ...(body || {}), ...(body && "request_id" in body ? { request_id: id } : {}), _request_id: id }
    : body;
  const bodyStr = requestBody !== undefined ? JSON.stringify(requestBody) : undefined;
  const maxAttempts = write && retryOnFailure ? AUTOMATIC_WRITE_RETRIES + 1 : 1;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) await waitRetryDelay(attempt);
    if (id && onRequestId) {
      try {
        await onRequestId(id, attempt > 0);
      } catch (error) {
        if (attempt === 0) throw error;
      }
    }
    if (liveSocket?.readyState === 1) {
      try {
        return await socketRequest(method, path, bodyStr);
      } catch (error) {
        if (typeof error?.status === "number") {
          if (!isRetryableWriteFailure(error.status) || attempt + 1 >= maxAttempts) {
            throw error;
          }
          continue;
        }
        // A lost WebSocket reply falls back to HTTP with the same request ID.
      }
    }
    const options = { method, headers: { "Content-Type": "application/json" } };
    if (bodyStr !== undefined) options.body = bodyStr;
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      if (attempt + 1 >= maxAttempts) throw error;
      continue;
    }
    const payload = await parseJsonPayload(response);
    if (response.ok) return payload;
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.status = response.status;
    if (!isRetryableWriteFailure(response.status) || attempt + 1 >= maxAttempts) {
      throw error;
    }
  }
  throw new Error("Request failed");
}

async function requestJson(method, path, body = undefined, retryOnFailure = false, onRequestId = null) {
  const receipt = await performJsonRequest(method, path, body, retryOnFailure, onRequestId);
  if (!receipt._receipt) return receipt;
  const result = { ...receipt, _operation_result: receipt };
  for (const [key, route] of [["message", "messages"], ["task", "tasks"], ["report", "reports"]]) {
    const id = receipt[key]?.id;
    if (!id) continue;
    try {
      const current = await performJsonRequest("GET", `/${route}/${encodeURIComponent(id)}`);
      result[key] = current[key] || null;
    } catch (error) {
      result[key] = null;
      result.reconciliation_error = String(error.message || error);
    }
  }
  return result;
}

async function requestForm(path, buildFormData) {
  const id = await issueWriteToken();
  const maxAttempts = AUTOMATIC_WRITE_RETRIES + 1;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) await waitRetryDelay(attempt);
    const formData = buildFormData();
    formData.append("_request_id", id);
    let response;
    try {
      response = await fetch(path, { method: "POST", body: formData });
    } catch (error) {
      if (attempt + 1 >= maxAttempts) throw error;
      continue;
    }
    const payload = await parseJsonPayload(response);
    if (response.ok) return payload;
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.status = response.status;
    if (!isRetryableWriteFailure(response.status) || attempt + 1 >= maxAttempts) {
      throw error;
    }
  }
  throw new Error("Upload failed");
}

async function blockedBashRequest(method, path) {
  const maxAttempts = method === "DELETE" ? AUTOMATIC_WRITE_RETRIES + 1 : 1;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) await waitRetryDelay(attempt);
    let response;
    try {
      response = await fetch(path, { method, cache: "no-store", mode: "same-origin" });
    } catch (error) {
      if (attempt + 1 >= maxAttempts) throw error;
      continue;
    }
    const payload = await parseJsonPayload(response);
    if (response.ok) return payload;
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.status = response.status;
    if (!isRetryableWriteFailure(response.status) || attempt + 1 >= maxAttempts) {
      throw error;
    }
  }
  throw new Error("Request failed");
}

export const api = {
  newRequestId: issueWriteToken,
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
  hiddenMessageWindow(first, last, query = "") {
    const q = new URLSearchParams({ first, last });
    if (query) q.set("q", query);
    return requestJson("GET", `/messages/hidden?${q.toString()}`);
  },
  getMessage(id) {
    return requestJson("GET", `/messages/${encodeURIComponent(id)}`);
  },
  sendMessage({ body, hidden = false, blocking = true, parent_ids = [], parent_refs, request_id }) {
    return requestJson("POST", "/messages", { body, hidden, blocking, parent_ids, parent_refs, request_id }, true);
  },
  markRepliesSeen(id, revision) {
    return requestJson("POST", `/messages/${encodeURIComponent(id)}/replies-seen`, { revision });
  },
  async uploadFile(file) {
    const payload = await requestForm("/uploads", () => {
      const formData = new FormData();
      formData.append("file", file, file.name || "upload.bin");
      return formData;
    });
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
    }, true);
  },
  skipPoll() {
    return requestJson("POST", "/poll/skip", {}, true);
  },
  cancelSkipPoll() {
    return requestJson("DELETE", "/poll/skip", {}, true);
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
