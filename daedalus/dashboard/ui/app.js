"use strict";

const POOL_NOTES = {
  "daedalus/auto": "Picks a pool from the prompt",
  "daedalus/sophos": "Tier A",
  "daedalus/deinos": "Tier B",
  "daedalus/koinos": "Tier C",
  "daedalus/moros": "Tier D",
  "daedalus/graphos": "Transcription",
  "daedalus/photos": "Image",
};
// The Requests page loads 50 rows, and each "Show more" adds 50, up to the 500 that the server keeps.
const REQUESTS_STEP = 50;
const REQUESTS_KEPT = 500;
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const tierLetter = (name) => (name || "").replace("TIER-", "") || "-";
const tokens = (n) => !n ? "-" : n >= 1e6 ? +(n / 1e6).toFixed(1) + "M" : n < 1000 ? String(n) : Math.round(n / 1024) + "K";
// The hour cycle of each shown time, from dashboard.time_format: h23 for 24h, h12 for 12h.
let hourCycle = "h23";
const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(
  [], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle });

const state = {
  models: [], tier: "All", mode: "all", sort: { key: "", dir: 1 }, files: [], file: 0, saved: [], timers: [],
  view: "form", forms: [], formSaved: [], overrideKeys: [], providerDefaults: {}, settingsView: "form",
  pools: [], requests: [], requestLimit: REQUESTS_STEP, keys: [], catalog: {}, settings: null,
  live: new Map(), source: null, env: [],
};

const fileName = (path) => path.split(/[\\/]/).pop();
const line = (left, right) => `<div class="line"><span>${left}</span><span>${right}</span></div>`;
const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const none = (text) => `<div class="more">${text}</div>`;
// A model name cell that ends in an ellipsis when it is too long. The title shows the full name.
const mobileLabel = (text) => `<span class="mobile-label" aria-hidden="true">${esc(text)}</span>`;
const nameCell = (text, shown = esc(text), label = "Model") =>
  `<td role="cell" class="name" title="${esc(text)}">${mobileLabel(label)}<span class="cell-value">${shown}</span></td>`;
const cell = (label, inner, cls = "") =>
  `<td role="cell"${cls ? ` class="${cls}"` : ""}>${mobileLabel(label)}<span class="cell-value">${inner}</span></td>`;

// A confirmation modal in place of window.confirm. It resolves true on Confirm.
let settle = null;
function ask(title, message, confirm = "Confirm", danger = false) {
  return new Promise((resolve) => {
    settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    const ok = $("modal-ok");
    ok.textContent = confirm;
    ok.classList.toggle("danger", danger);
    $("modal").returnValue = "";
    $("modal").showModal();
  });
}

// One card for each page, from the data that the pages already read.
function renderOverview() {
  $("ov-requests").innerHTML = state.requests.slice(0, 10).map((r) => line(
    `<span class="status ${statusClass(r)}">${statusCell(r)}</span> <span title="${esc(r.via || r.model || "")}">${esc(r.via || r.model || "-")}</span>`,
    clock(r.at),
  )).join("") || none("No requests");
  $("ov-model-count").textContent = state.models.length || "";
  // Each pool with its mean weight and the model that served it most.
  const served = {};
  for (const r of state.requests) served[r.via || r.model] = (served[r.via || r.model] || 0) + 1;
  $("ov-models").innerHTML = state.pools.map((pool) => {
    const top = topModel(pool.members, served);
    const health = poolHealth(pool.members);
    return `<div class="pool-line"><div class="line"><span>${esc(pool.shown.replace("daedalus/", ""))}</span>
      <span title="${esc(top?.id)}">${top ? esc(top.id) : "no models"}</span></div>${health === null ? "" : weightBar(health)}</div>`;
  }).join("") || none(state.models.length ? "No pools" : "No models. Run daedalus catalog.");
  $("ov-limits").innerHTML = overviewLimits(state.limits) || none("No limits yet");
}

class LoggedOut extends Error {}

// The session value also goes in a header: some frames, such as a preview, block cookies.
const SESSION = "daedalus-session";
const session = () => sessionStorage.getItem(SESSION) || localStorage.getItem(SESSION);
function keepSession(value, remember) {
  sessionStorage.removeItem(SESSION);
  localStorage.removeItem(SESSION);
  if (value) (remember ? localStorage : sessionStorage).setItem(SESSION, value);
}

async function call(path, options = {}) {
  const response = await fetch("ui/api/" + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(session() ? { "x-daedalus-session": session() } : {}) },
    ...options,
  });
  if (response.status === 401 && path !== "login") {
    keepSession(null);
    throw new LoggedOut();
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || `HTTP ${response.status}`);
  return body;
}

function showLogin(message = "") {
  state.timers.forEach(clearInterval);
  state.timers = [];
  closeLive();
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-message").textContent = message;
  showPassword(false);
  loginHints();
}

// The username to fill in and the master key hint, for the values that the environment does not set.
async function loginHints() {
  const hints = await call("login").catch(() => ({}));
  const form = $("login");
  if (!form.username.value && hints.username) form.username.value = hints.username;
  $("login-hint").hidden = !hints.master;
  $("login-version").textContent = hints.version || "";
  form[form.username.value ? "password" : "username"].focus();
}

// The eye button shows the password until the next click or login.
function showPassword(on) {
  const button = $("show-password");
  const label = on ? "Hide the password" : "Show the password";
  $("login").password.type = on ? "text" : "password";
  button.setAttribute("aria-pressed", String(on));
  button.setAttribute("aria-label", label);
  button.title = label;
}

async function guarded(task) {
  try {
    await task();
  } catch (error) {
    if (error instanceof LoggedOut) showLogin();
    else console.error(error);
  }
}

// Hour and minute, with the weekday when the time is not today.
function shortTime(seconds) {
  const date = new Date(seconds * 1000);
  const today = date.toDateString() === new Date().toDateString();
  const options = { hour: "2-digit", minute: "2-digit", hourCycle, ...(today ? {} : { weekday: "short" }) };
  return date.toLocaleString([], options);
}

// The catalog chip. A click starts a rebuild.
function catalogChip({ built, next, rebuilding }) {
  const last = rebuilding ? "rebuilding" : built ? shortTime(built) : "never";
  const following = next ? ` &middot; next <b>${esc(shortTime(next))}</b>` : " &middot; no schedule";
  return `<button type="button" class="chip rebuild" ${rebuilding ? "disabled" : ""}
    title="${rebuilding ? "A catalog rebuild runs now" : "Rebuild the catalog now"}">Catalog <b>${esc(last)}</b>${following}</button>`;
}

async function rebuildCatalog() {
  const go = await ask("Rebuild the catalog", "daedalus reads the model list of each provider again.", "Rebuild");
  if (!go) return;
  try {
    await call("catalog", { method: "POST" });
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    alert(error.message);
  }
  guarded(refreshFast);
}

// The status card of a phone: the health row, then 1 line for each value. The catalog line starts a rebuild.
function renderStatusCard(status) {
  const { built, next, rebuilding } = status.catalog || {};
  const last = rebuilding ? "rebuilding" : built ? shortTime(built) : "never";
  const following = next ? ` &middot; next ${esc(shortTime(next))}` : " &middot; no schedule";
  const label = rebuilding ? "A catalog rebuild runs now" : "Rebuild the catalog now";
  $("card-health").innerHTML =
    `<span class="dot${status.healthy ? "" : " off"}"></span>${status.healthy ? "Healthy" : "Down"}`;
  $("card-rows").innerHTML = line("Sessions", status.sessions)
    + `<button class="line rebuild" type="button" title="${label}" ${rebuilding ? "disabled" : ""}>
      <span>Catalog</span><span><b>${esc(last)}</b>${following}</span></button>`;
}

function renderStatus(status) {
  const chips = [
    `<span class="chip"><span class="dot${status.healthy ? "" : " off"}"></span>` +
      `<b>${status.healthy ? "Healthy" : "Down"}</b></span>`,
    `<span class="chip" title="Conversations with a session model and a request in the last hour"><b>${status.sessions}</b> sessions</span>`,
    catalogChip(status.catalog),
  ];
  document.querySelectorAll("[data-status]").forEach((host) => { host.innerHTML = chips.join(""); });
  renderStatusCard(status);
  $("version").textContent = status.version;
  markNavFades();
}

// The tier filter of the Models tab for each pool. The other pools show all tiers.
const POOL_TIERS = {
  "daedalus/auto": "All", "daedalus/sophos": "A", "daedalus/deinos": "B",
  "daedalus/koinos": "C", "daedalus/moros": "D",
};

// A pool row names the member that served the most requests the page holds.
// A pool without those requests keeps its catalog first model.
function topModel(members, served) {
  const counts = members.map((member) => served[member.id] || 0);
  const best = Math.max(...counts);
  return best > 0 ? members[counts.indexOf(best)] : members[0];
}

// The mean weight of a pool. A model in a cooldown counts as 0.
function poolHealth(members) {
  if (!members.length) return null;
  const now = Date.now() / 1000;
  return members.reduce((sum, m) => sum + (m.cooldown > now ? 0 : m.weight), 0) / members.length;
}

// The highest weight first. A tie keeps the chain order.
const byWeight = (members) => [...members].sort((a, b) => b.weight - a.weight);

// The Models tab filters of a pool.
const poolFilter = (pool) => ({ tier: POOL_TIERS[pool.name] || "All", mode: pool.mode || "chat" });

// A pool card links to its filters. The card of the active filters is on, and its link clears them.
function markPools() {
  document.querySelectorAll("#pools .pool").forEach((card) => {
    const on = card.dataset.tier === state.tier && card.dataset.mode === state.mode;
    card.classList.toggle("on", on);
    card.href = on ? "#/models?tier=All&mode=all" : `#/models?tier=${card.dataset.tier}&mode=${card.dataset.mode}&sort=weight`;
  });
}

function renderPools(pools) {
  $("pools").innerHTML = pools.map((pool) => {
    const { tier, mode } = poolFilter(pool);
    const health = poolHealth(pool.members);
    const bar = health === null ? "" : `<div class="health" title="Mean weight. A model in a cooldown counts as 0.">
      ${weightBar(health)}<span class="num">${health.toFixed(2)}</span></div>`;
    const context = pool.context
      ? ` <span class="ctx" title="The largest context of a pool model">${tokens(pool.context)}</span>` : "";
    return `<a class="card pool" data-tier="${tier}" data-mode="${esc(mode)}" title="${esc(pool.shown)}: ${esc(POOL_NOTES[pool.name] || "")}">
      <h3>${esc(pool.shown.replace("daedalus/", ""))}${context}</h3>${bar}<div class="sub">${count(pool.members.length, "model")}</div></a>`;
  }).join("");
  markPools();
}

const opened = new Set();
let shownRequests = "";

const seconds = (value) => value == null ? "" : `${value.toFixed(3)}s`;

// A cooldown that an attempt started, such as "cooldown 60s backoff".
const coolText = (c) => `cooldown ${timeLeft(Date.now() / 1000 + c.seconds) || "0s"} ${c.reason}`;
const weightText = (a) => `weight ${a.weight_change.from.toFixed(2)} → ${a.weight_change.to.toFixed(2)}`;

function sentText(a, asked) {
  if (a.effort == null) return "dropped";
  const shown = String(a.effort).replace(/^thinkingLevel=/, "");
  const level = shown.split("=").pop().toLowerCase();
  return asked && level === String(asked).toLowerCase() ? asked : shown;
}

const EFFORT_SHORT = { minimal: "min", low: "low", medium: "med", high: "hi", xhigh: "xhi" };
function shortEffort(value) {
  const shown = String(value).replace(/^thinkingLevel=/, "");
  const level = shown.split("=").pop().toLowerCase();
  return EFFORT_SHORT[level] || shown;
}

// The effort that the client asked for, then the effort that went to the model that answered.
function effortCell(r) {
  const served = (r.attempts || []).filter((a) => a.result === "answered" && "effort" in a).pop();
  if (!served) return esc(r.effort || "-");
  const sent = sentText(served, r.effort);
  if (sent === r.effort) return esc(r.effort);
  return `${esc(r.effort || "-")} <span class="from">${esc(shortEffort(sent))}</span>`;
}

// A request that the client closed shows "cancelled" in place of its status.
function statusText(r) {
  return r.cancelled ? "cancelled" : r.status;
}

// The status on the page: a stop square for a request that the client closed.
function statusCell(r) {
  return r.cancelled ? '<span class="stop" role="img" title="Cancelled" aria-label="Cancelled"></span>' : esc(r.status);
}

function statusClass(r) {
  if (r.cancelled) return "muted";
  if (r.status === "err") return "s5";
  return `s${String(r.status)[0]}`;
}

function chainText(r) {
  const head = [clock(r.at), r.model, statusText(r), r.effort && `effort=${r.effort}`, r.pool && `pool=${r.pool}`,
    r.routed && `from=${r.routed}`,
    `fallbacks=${r.fallbacks ?? 0}`, r.retry && `retry=${r.retry}`, r.loop && `loop=${r.loop}`].filter(Boolean).join(" ");
  const steps = (r.attempts || []).map((a, i) =>
    `${i + 1}. ${a.model} ${a.result} ${seconds(a.seconds)}`.trim() + ("effort" in a ? ` effort=${sentText(a, r.effort)}` : "")
      + (a.weight_change ? ` ${weightText(a)}` : "")
      + (a.cooldown ? ` ${coolText(a.cooldown)}` : "")
      + (a.error ? `\n   ${a.error}` : ""));
  return [head, ...steps].join("\n");
}

// A request opens its fallback chain only after a fallback, or when an attempt failed and has an error to show.
const hasChain = (r) => (r.attempts || []).length > 1 || (r.attempts || []).some((a) => a.result !== "answered");

function chainSteps(r) {
  const steps = (r.attempts || []).map((a, i) => `
    <li class="step ${a.result === "answered" ? "good" : "bad"}">
      <span class="num">${i + 1}.</span> <b>${esc(a.model)}</b>
      <span class="result">${esc(a.result)}</span> <span class="muted num">${seconds(a.seconds)}</span>
      ${"effort" in a ? `<span class="from">effort ${esc(shortEffort(sentText(a, r.effort)))}</span>` : ""}
      ${a.weight_change ? `<span class="from">${esc(weightText(a))}</span>` : ""}
      ${a.cooldown ? `<span class="from">${esc(coolText(a.cooldown))}</span>` : ""}
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return steps ? `<ol>${steps}</ol>` : '<p class="muted">No attempt data for this request.</p>';
}

function chainRows(r) {
  return `<tr role="row" class="chain"><td role="cell" colspan="13"><div class="chain-body">
    <div class="chain-head"><span class="muted">Fallback chain</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${chainSteps(r)}
  </div></td></tr>`;
}

function poolText(r) {
  const from = r.routed ? ` <span class="from" title="Tier ${esc(poolTier(r.routed))} of the previous model">fr${esc(poolTier(r.routed))}</span>` : "";
  const loop = r.loop ? ` <span class="from" title="${esc(r.loop)} equal tool calls stopped the chain">tl${esc(r.loop)}</span>` : "";
  return `${esc(r.pool || "-")}${transitionCell(r.transition)}${from}${loop}`;
}

function mobileFallbackChain(r) {
  return `<details class="mobile-fallback-chain">
    <summary>View fallback chain</summary>
    <div class="mobile-chain-head"><span class="muted">Fallback chain</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${chainSteps(r)}
  </details>`;
}

function mobileRequestDetails(r, live = false) {
  const fallbacks = r.fallbacks ?? "-";
  const count = r.fallbacks === undefined || r.fallbacks === null
    ? ""
    : ` · ${esc(r.fallbacks)} ${Number(r.fallbacks) === 1 ? "fallback" : "fallbacks"}`;
  const chain = !live && hasChain(r) ? mobileFallbackChain(r) : "";
  return `<details class="mobile-request-more">
    <summary>More · session, effort, pool${count}</summary>
    <dl class="mobile-request-meta">
      <div><dt>Session</dt><dd>${esc(r.session || "-")}</dd></div>
      <div><dt>Effort</dt><dd>${effortCell(r)}</dd></div>
      <div><dt>Pool</dt><dd>${poolText(r)}</dd></div>
      <div><dt>Fallbacks</dt><dd>${esc(fallbacks)}</dd></div>
    </dl>
    ${chain}
  </details>`;
}

function fallbackCell(r, classes = "", live = false) {
  return `<td role="cell" class="${classes} fallbacks-cell">
    ${mobileLabel("Fallbacks")}<span class="cell-value">${esc(r.fallbacks ?? "-")}</span>
    ${mobileRequestDetails(r, live)}
  </td>`;
}

// A token count in whole thousands from 1,000, such as 79K, with the exact count on hover.
// A count floored to K, M or B, for example 79K. Below 1,000 it stays whole.
const FLOORS = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
function floorCount(n) {
  const [size, mark] = FLOORS.find(([step]) => n >= step) || [1, ""];
  return `${Math.floor(n / size)}${mark}`;
}

function tokenCell(count, mark = "", label = "Input") {
  if (typeof count !== "number") return cell(label, "-", "hide-sm num");
  const shown = floorCount(count);
  return `<td role="cell" class="hide-sm num" title="${mark}${count.toLocaleString()}">${mobileLabel(label)}<span class="cell-value">${mark}${shown}</span></td>`;
}

// The stream time of a finished request: after the first token, or the total time without a stream.
function streamCell(r) {
  if (r.seconds == null) return "-";
  if (!r.stream) return seconds(r.seconds);
  return r.ttft ? seconds(Math.max(0, r.seconds - parseFloat(r.ttft))) : "-";
}

// The short app name from the client headers, with the API key name on hover.
const appCell = (r) => `<td role="cell" class="hide-md"${r.key ? ` title="API key: ${esc(r.key)}"` : ""}>${mobileLabel("App")}<span class="cell-value">${r.app ? esc(r.app) : dash}</span></td>`;

// A live request with local times, because the server sends ages and not clock times.
function liveRow(r) {
  const now = Date.now();
  const since = now - r.age * 1000;
  const attemptSince = now - r.attempt_age * 1000;
  return { ...r, since, attemptSince, first: r.ttft == null ? null : attemptSince + r.ttft * 1000 };
}

// The requests in flight, from the dashboard event stream.
function openLive() {
  closeLive();
  const value = session();
  const source = new EventSource(`ui/api/requests/live${value ? `?session=${encodeURIComponent(value)}` : ""}`);
  const put = (event) => {
    const r = JSON.parse(event.data);
    state.live.set(r.id, liveRow(r));
    renderLive();
  };
  source.addEventListener("live", (event) => {
    state.live = new Map(JSON.parse(event.data).map((r) => [r.id, liveRow(r)]));
    renderLive();
  });
  ["start", "update", "first"].forEach((kind) => source.addEventListener(kind, put));
  source.addEventListener("end", (event) => {
    state.live.delete(JSON.parse(event.data).id);
    renderLive();
    guarded(refreshFast);
  });
  state.source = source;
}

function closeLive() {
  state.source?.close();
  state.source = null;
  state.live = new Map();
}

const TRANSITION_REASONS = {
  ctx: "Previous model exceeded the context limit",
  hlt: "No healthy deployments in the previous pool",
  lmt: "Quota or cooldown blocked the previous pool",
  err: "Previous upstream attempt failed",
  rnd: "Weighted random draw selected another model",
  cls: "Prompt classifier chose a different tier",
  esc: "Escalation keyword raised the tier",
  try: "OpenWebUI retry changed the route",
};

function transitionCell(t) {
  if (!t || !t.reason) return "";
  const label = TRANSITION_REASONS[t.reason] || t.reason;
  return ` <span class="transition-code" title="${esc(label)}" aria-label="${esc(label)}">${esc(t.reason)}</span>`;
}

// The tier letter of a pool short name: sophos gives A. The pool cards give it, and the
// built-in names cover the first paint, before the cards arrive.
const BUILT_IN_TIERS = { sophos: "A", deinos: "B", koinos: "C", moros: "D" };
function poolTier(name) {
  const pool = state.pools.find((p) => p.shown.replace("daedalus/", "") === name);
  const tier = pool?.members.find((m) => m.tier)?.tier;
  return tier ? tierLetter(tier) : BUILT_IN_TIERS[name] || String(name).slice(0, 1).toUpperCase();
}

// The code legend of the Requests page: each short code with its meaning.
function renderLegend() {
  const rows = [
    ...Object.entries(TRANSITION_REASONS),
    ["frX", "the tier of the previous model"],
    ["tlN", "N equal tool calls stopped the chain"],
  ];
  $("legend-body").innerHTML = rows.map(([code, label]) =>
    `<span><code>${esc(code)}</code>${esc(label)}</span>`).join("");
}

function renderLive() {
  const rows = [...state.live.values()].sort((a, b) => b.since - a.since);
  $("live").innerHTML = rows.map((r) => `
    <tr role="row" class="live-row" data-live="${r.id}">
      ${cell("Time", `<span class="pulse"></span>${clock(r.since / 1000)}`, "num muted")}
      ${appCell(r)}
      ${cell("Session", esc(r.session || "-"), "hide-sm hide-md num")}
      ${nameCell(r.model || r.path)}
      ${cell("Effort", effortCell(r), "hide-sm")}
      ${cell("Pool", poolText(r), "hide-sm muted")}
      ${nameCell(r.via || r.trying || "", r.via ? esc(r.via) : `<span class="muted">${r.trying ? `trying ${esc(r.trying)}` : "waiting"}</span>`, "Served by")}
      ${cell("Status", "live", "status muted")}
      ${cell("Input", "-", "hide-sm num muted")}
      ${cell("Output", "-", "hide-sm num muted")}
      <td role="cell" class="hide-sm num">${mobileLabel("TTFT")}<span class="cell-value"><span data-clock="ttft"></span></span></td>
      <td role="cell" class="hide-sm hide-md num">${mobileLabel("Stream")}<span class="cell-value"><span data-clock="stream"></span></span></td>
      ${fallbackCell(r, "hide-sm hide-md num muted", true)}
    </tr>`).join("");
  tickLive();
}

// The live clocks: TTFT until the first token, then the stream time. Without a stream, both count the total.
function tickLive() {
  const now = Date.now();
  for (const row of $("live").children) {
    const r = state.live.get(Number(row.dataset.live));
    if (!r) continue;
    const ttft = ((r.first ?? now) - r.attemptSince) / 1000;
    const stream = !r.stream ? (now - r.since) / 1000 : r.first == null ? null : (now - r.first) / 1000;
    row.querySelector('[data-clock="ttft"]').textContent = seconds(ttft);
    row.querySelector('[data-clock="stream"]').textContent = seconds(stream);
  }
}

function splitRequest(r) {
  const attempts = r.attempts || [];
  const answered = [];
  attempts.forEach((a, i) => {
    if (a.result === "answered") answered.push(i);
  });
  if (answered.length <= 1) return [r];

  const result = [];
  let prevCut = 0;
  for (let j = 0; j < answered.length; j++) {
    const isLast = j === answered.length - 1;
    let nextCut;
    if (isLast) {
      nextCut = attempts.length;
    } else {
      const ansIdx = answered[j];
      const model = attempts[ansIdx].model;
      let failIdx = ansIdx + 1;
      while (failIdx < answered[j + 1] && attempts[failIdx].model === model && attempts[failIdx].result !== "answered") {
        failIdx++;
      }
      nextCut = failIdx;
    }

    const slice = attempts.slice(prevCut, nextCut);
    const ansAttempt = attempts[answered[j]];
    const at = r.at + (j === 0 ? 0 : (attempts[answered[0]]?.seconds || 0.001) * j);

    if (!isLast) {
      result.push({
        ...r,
        at,
        via: ansAttempt.model,
        status: "err",
        attempts: slice,
        fallbacks: Math.max(0, slice.length - 1),
        ttft: ansAttempt.seconds != null ? seconds(ansAttempt.seconds) : r.ttft,
        seconds: ansAttempt.seconds ?? r.seconds,
        tokens: r.tokens ? { input: r.tokens.input } : null,
        transition: null,
      });
    } else {
      result.push({
        ...r,
        at,
        via: ansAttempt.model,
        status: r.status,
        attempts: slice,
        fallbacks: Math.max(0, slice.length - 1),
        ttft: ansAttempt.seconds != null ? seconds(ansAttempt.seconds) : r.ttft,
        seconds: r.seconds,
        transition: r.transition,
      });
    }
    prevCut = nextCut;
  }
  return result.reverse();
}

const splitRequests = (requests) => (requests || []).flatMap(splitRequest);

function renderRequests(rows) {
  const flatRows = splitRequests(rows);
  const text = JSON.stringify(flatRows);
  const selected = getSelection();
  if (text === shownRequests) return;
  if (!selected.isCollapsed && $("requests").contains(selected.anchorNode)) return;
  shownRequests = text;
  $("requests").innerHTML = flatRows.length ? flatRows.map((r) => {
    const chain = hasChain(r);
    const chainOpen = chain && opened.has(String(r.at));
    return `
    <tr role="row" class="request${chain ? " has-chain" : ""}${chainOpen ? " open" : ""}" data-at="${r.at}"${chain ? ' title="Show the fallback chain"' : ""}>
      ${cell("Time", `<span class="caret${chain ? "" : " none"}"></span>${clock(r.at)}`, "num muted")}
      ${appCell(r)}
      ${cell("Session", esc(r.session || "-"), "hide-sm hide-md num")}
      ${nameCell(r.model || "-")}
      ${cell("Effort", effortCell(r), "hide-sm")}
      ${cell("Pool", poolText(r), "hide-sm muted")}
      ${nameCell(r.via || "", r.via ? esc(r.via) : '<span class="muted">none</span>', "Served by")}
      ${cell("Status", statusCell(r), `status ${statusClass(r)}`)}
      ${tokenCell(r.tokens?.input, r.tokens?.estimate ? "~" : "", "Input")}
      ${tokenCell(r.tokens?.output, "", "Output")}
      ${cell("TTFT", esc(r.ttft || "-"), "hide-sm num")}
      ${cell("Stream", streamCell(r), "hide-sm hide-md num")}
      ${fallbackCell(r, "hide-sm hide-md num")}
    </tr>${chainOpen ? chainRows(r) : ""}`;
  }).join("") : '<tr role="row"><td role="cell" colspan="13" class="empty">No requests</td></tr>';
}

// The label of each catalog mode.
const MODES = {
  chat: "Chat", embedding: "Embedding", audio_transcription: "Transcription",
  audio_speech: "Speech", image_generation: "Image", video_generation: "Video",
  decisions: "Decisions", rerank: "Rerank",
};
// The label of each media flag chip.
const FLAGS = { vision: "Image in", pdf_input: "PDF in", audio_input: "Audio in", audio_output: "Audio out" };
// The Type cell: a chip for the mode, then a chip for each media flag.
const typeChips = (m) => `<span class="chip flag mode">${esc(MODES[m.mode] || m.mode)}</span>`
  + m.flags.map((f) => `<span class="chip flag">${esc(FLAGS[f] || f)}</span>`).join("");

function renderTiers() {
  $("tiers").innerHTML = ["All", "A", "B", "C", "D"].map((t) =>
    `<button type="button" class="filter${t === state.tier ? " on" : ""}" data-tier="${t}">${t}</button>`,
  ).join(" ");
  $("mode").value = state.mode;
}

// The sort value of each column. Null goes last in both directions.
const sortValue = {
  id: (m) => m.id.toLowerCase(),
  mode: (m) => MODES[m.mode] || m.mode,
  tier: (m) => (tierLetter(m.tier) === "-" ? null : tierLetter(m.tier)),
  order: (m) => m.order ?? null,
  context: (m) => m.max_input_tokens ?? null,
  tools: (m) => (m.mode !== "chat" ? null : m.tools ? 1 : 0),
  reasoning: (m) => (m.mode !== "chat" ? null : m.reasoning ? 1 : 0),
  weight: (m) => m.weight ?? null,
  cooldown: (m) => (m.cooldown && m.cooldown > Date.now() / 1000 ? m.cooldown : null),
};

// The label of each reasoning effort. The chip color grows with the effort.
const EFFORTS = { none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "X-High", max: "Max" };
// A reasoning model shows its default effort from the catalog, else Yes.
function reasoningCell(m) {
  if (!m.reasoning || !m.effort) return yesNo(m.reasoning);
  const known = m.effort in EFFORTS;
  const label = known ? EFFORTS[m.effort] : m.effort.charAt(0).toUpperCase() + m.effort.slice(1);
  return `<span class="chip flag effort${known ? ` e-${m.effort}` : ""}" title="Default reasoning effort">${esc(label)}</span>`;
}
const yesNo = (on) => (on ? '<span class="yes">Yes</span>' : '<span class="muted">No</span>');
// Red at 0, orange at 0.5 and blue at 1, mixed in between.
function weightColor(weight) {
  const high = weight >= 0.5;
  const share = Math.round((high ? (weight - 0.5) * 2 : weight * 2) * 100);
  return high ? `color-mix(in oklch, var(--accent) ${share}%, var(--orange))`
    : `color-mix(in oklch, var(--orange) ${share}%, var(--red))`;
}
const weightBar = (weight) => `<div class="track"><div class="fill"
  style="width:${Math.round(weight * 100)}%;background:${weightColor(weight)}"></div></div>`;

// With no column chosen, the rows sort by type, then by model name.
function sortModels(rows) {
  const { dir } = state.sort;
  const key = sortValue[state.sort.key] ? state.sort.key : "mode";
  return [...rows].sort((a, b) => {
    const x = sortValue[key](a), y = sortValue[key](b);
    if (x === y) return a.id.localeCompare(b.id);
    if (x === null) return 1;
    if (y === null) return -1;
    return (x < y ? -1 : 1) * dir;
  });
}

function renderSortHeads() {
  document.querySelectorAll("#model-head th").forEach((th) => {
    const on = th.dataset.sort === state.sort.key;
    th.setAttribute("aria-sort", on ? (state.sort.dir > 0 ? "ascending" : "descending") : "none");
  });
}

const dash = '<span class="muted">-</span>';

function renderModels() {
  markPools();
  // The badge counts the rows of this page, not the routable models of the status answer.
  $("nav-models").textContent = state.models.length || "";
  const query = $("search").value.trim().toLowerCase();
  const rows = sortModels(state.models.filter((m) =>
    (state.tier === "All" || tierLetter(m.tier) === state.tier)
    && (state.mode === "all" || m.mode === state.mode || m.flags.includes(state.mode))
    && m.id.toLowerCase().includes(query)));
  const empty = state.models.length ? "No models match" : "No models. Run daedalus catalog.";
  $("models").innerHTML = rows.length ? rows.map((m) => `
    <tr>
      ${nameCell(m.id)}
      <td class="hide-sm"><div class="types">${typeChips(m)}</div></td>
      <td class="mid">${m.tier ? `<span class="tier">${esc(tierLetter(m.tier))}</span>` : dash}</td>
      <td class="hide-sm mid num${m.order > 1 ? "" : " muted"}">${m.order ?? dash}</td>
      <td class="hide-sm num muted">${tokens(m.max_input_tokens)}</td>
      <td class="mid">${m.mode === "chat" ? yesNo(m.tools) : dash}</td>
      <td class="hide-sm mid">${m.mode === "chat" ? reasoningCell(m) : dash}</td>
      <td class="num">${coolCells(m)}</td>
      <td>${m.weight == null ? dash
        : `<div class="weight">${weightBar(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div>`}</td>
    </tr>`).join("") : `<tr><td colspan="9" class="empty">${empty}</td></tr>`;
}

// The time left of a cooldown, such as 59s, 4m 05s or 3h 12m.
function timeLeft(until) {
  const left = Math.ceil(until - Date.now() / 1000);
  if (left <= 0) return null;
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}h ${pad(m)}m` : m ? `${m}m ${pad(s)}s` : `${s}s`;
}
const coolCell = (until) => {
  const left = until ? timeLeft(until) : null;
  return left ? `<span class="cool" data-until="${until}" title="Until ${esc(dateTime(until))}">${left}</span>` : dash;
};
// The cooldown of the model, and of each client with its own provider key, such as "kilo 4m 05s".
function coolCells(m) {
  const clients = Object.entries(m.client_cooldowns || {}).filter(([, until]) => timeLeft(until))
    .map(([client, until]) => `<div><small>${esc(client)}</small> ${coolCell(until)}</div>`);
  if (!clients.length) return coolCell(m.cooldown);
  return (timeLeft(m.cooldown || 0) ? `<div>${coolCell(m.cooldown)}</div>` : "") + clients.join("");
}
// The live clock of each cooldown cell.
function tickCooldowns() {
  for (const cell of document.querySelectorAll(".cool[data-until]")) {
    const left = timeLeft(Number(cell.dataset.until));
    if (left) cell.textContent = left;
    else cell.outerHTML = dash;
  }
}

const dateTime = (seconds) => new Date(seconds * 1000).toLocaleString(
  [], { dateStyle: "medium", timeStyle: "short", hourCycle });

// The value states of the Keys and values panel. Only the end of a long saved value shows.
const ENV_STATES = {
  saved: (row) => (row.end ? `saved, ends in ${row.end}` : "saved"),
  env: () => "from the environment",
  missing: () => "missing",
};

function renderEnv(rows) {
  $("env-rows").innerHTML = rows.length ? rows.map((r) => `
    <tr>
      <td><code>${esc(r.name)}</code></td>
      <td class="hide-sm muted">${esc(r.used.join(", "))}</td>
      <td class="${r.state === "missing" ? "out" : r.state === "env" ? "muted" : ""}">${esc(ENV_STATES[r.state](r))}</td>
      <td><form class="env-save" data-env="${esc(r.name)}">
        <input type="password" autocomplete="off" placeholder="Paste a value" aria-label="New value of ${esc(r.name)}" required>
        <button class="ghost" type="submit">Save</button>
      </form></td>
      <td class="end">${r.state === "saved" ? `<button type="button" class="ghost danger" data-env-clear="${esc(r.name)}">Clear</button>` : ""}</td>
    </tr>`).join("") : '<tr><td colspan="5" class="empty">No provider file uses env:NAME or db:NAME.</td></tr>';
}

async function refreshEnv() {
  state.env = await call("env");
  renderEnv(state.env);
  if (!document.querySelector('section[data-page="providers"]').hidden) renderForm();
}

function renderKeys(rows) {
  $("keys").innerHTML = rows.length ? rows.map((k) => `
    <tr>
      <td>${esc(k.name)}</td>
      <td class="num muted">${k.start ? esc(k.start) + "&hellip;" : "-"}</td>
      <td class="hide-sm muted">${dateTime(k.created)}</td>
      <td class="muted">${k.used ? dateTime(k.used) : "never"}</td>
      <td class="end"><button type="button" class="ghost danger" data-key="${esc(k.name)}">Delete</button></td>
    </tr>`).join("") : '<tr><td colspan="5" class="empty">No API keys. The master key opens /v1.</td></tr>';
}

async function refreshKeys() {
  state.keys = await call("keys");
  renderKeys(state.keys);
  renderOverview();
}

// The block keys that the form edits. The YAML view edits the other keys.
const FORM_KEYS = ["api_key", "account_id", "client_keys", "api_base", "api_type", "discovery_url", "discovery_match", "exclude", "tier", "models"];
// The keys that a model override sets but the provider level does not.
const MODEL_ONLY = ["pool", "timeout"];
// The keys that the provider level sets but a model override does not.
const PROVIDER_ONLY = ["hourly_requests"];
const TIERS = ["TIER-A", "TIER-B", "TIER-C", "TIER-D"];
// The width of 1 column of provider cards.
const CARD_WIDTH = 460;

const clone = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
const shown = (value) => (value !== null && typeof value === "object" ? JSON.stringify(value) : String(value));

// A typed value from the text of a chip: true, false, a number, JSON such as a hooks list, or a string.
function parsed(text) {
  const value = text.trim();
  if (/^[[{]/.test(value)) {
    try {
      return JSON.parse(value);
    } catch {
      // Not JSON: the text stays a string.
    }
  }
  if (value === "true" || value === "false") return value === "true";
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value.replace(/^(["'])(.*)\1$/, "$2");
}

// The blocks without the empty lists and maps that the form leaves. An empty override stays.
function pruned(blocks) {
  const out = clone(blocks);
  const empty = (value) => value == null || (typeof value === "object" && !Object.keys(value).length);
  for (const block of Object.values(out || {})) {
    if (!block || typeof block !== "object" || Array.isArray(block)) continue;
    if (block.tier && typeof block.tier === "object") {
      for (const tier of Object.keys(block.tier)) if (empty(block.tier[tier])) delete block.tier[tier];
    }
    for (const key of ["client_keys", "discovery_match", "exclude", "tier", "models"]) if (key in block && empty(block[key])) delete block[key];
  }
  return out;
}

const formText = (index) => JSON.stringify(pruned(state.forms[index]));

function fileDirty(index) {
  if (state.view === "form") return state.forms[index] != null && formText(index) !== state.formSaved[index];
  const text = index === state.file ? $("editor").value : state.files[index].text;
  return text !== state.saved[index];
}

const dirty = () => state.files.length > 0 && fileDirty(state.file);

function renderFiles() {
  $("files").innerHTML = state.files.map((file, index) => {
    const mark = fileDirty(index) ? " &bull;" : "";
    return `<button type="button" class="tab${index === state.file ? " on" : ""}"
      data-file="${index}" title="${esc(file.path)}">${esc(fileName(file.path))}${mark}</button>`;
  }).join(" ");
  $("save").disabled = !dirty();
  // The main provider file stays: only a {provider}.yml file can go.
  $("drop-provider").hidden = state.files[state.file]?.main !== false;
  document.querySelectorAll("#views [data-view]").forEach((tab) => tab.classList.toggle("on", tab.dataset.view === state.view));
  $("provider-form").hidden = state.view !== "form";
  $("yaml-view").hidden = state.view !== "yaml";
}

// The files, the form copies and the saved copies, from the API.
function takeFiles(files) {
  state.files = files;
  state.saved = files.map((file) => file.text);
  state.forms = files.map((file) => clone(file.blocks));
  state.formSaved = files.map((file, index) => formText(index));
}

// 1 file again from the API, after a save. The other files keep their changes.
async function takeFile(index) {
  const fresh = (await call("files")).find((file) => file.path === state.files[index].path);
  if (!fresh) return;
  state.files[index] = fresh;
  state.saved[index] = fresh.text;
  state.forms[index] = clone(fresh.blocks);
  state.formSaved[index] = formText(index);
  if (index === state.file) $("editor").value = fresh.text;
}

// A file with YAML that is not valid opens in the YAML view, with the error line.
function showFileError(index) {
  const error = state.files[index]?.error;
  $("save-message").textContent = error ? `Not valid YAML: ${error}` : "";
  if (error) state.view = "yaml";
}

function openFile(index) {
  if (state.files.length) state.files[state.file].text = $("editor").value;
  state.file = index;
  $("editor").value = state.files[index].text;
  showFileError(index);
  renderFiles();
  renderForm();
}

// A chip for 1 value. The × button deletes the list item or the map key.
const pill = (text, path, key) => `<span class="pill">${esc(text)}<button type="button" title="Delete"
  data-drop='${esc(JSON.stringify([...path, key]))}'>&times;</button></span>`;

// The "+ Add" button. A click puts an inline input in its place.
const adder = (path, kind, label = "+ Add") => `<button type="button" class="add"
  data-add='${esc(JSON.stringify(path))}' data-kind="${kind}">${esc(label)}</button>`;

function listField(name, values, path) {
  const list = Array.isArray(values) ? values : [];
  return `<div class="pills">${list.map((value, index) => pill(shown(value), path, index)).join("")}${adder(path, "list")}</div>`;
}

function mapPills(values, path, kind, separator) {
  const map = values && typeof values === "object" ? values : {};
  return Object.entries(map).map(([key, value]) => pill(`${key}${separator}${shown(value)}`, path, key)).join("")
    + adder(path, kind, kind === "override" ? "+ key" : "+ Add");
}

function field(label, hint, body) {
  return `<div class="field stack"><span><b>${esc(label)}</b>${hint ? `<small>${esc(hint)}</small>` : ""}</span>${body}</div>`;
}

function providerCard(name, block) {
  const path = [name];
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return `<div class="card provider" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
      <p class="sub">This block is not a map. Edit it in the YAML view.</p></div>`;
  }
  const defaults = state.providerDefaults[name] || state.providerDefaults["*"] || {};
  const tokenInfo = (value) => {
    if (typeof value !== "string") return null;
    if (value.startsWith("env:")) return { type: "env", name: value.slice("env:".length), raw: value };
    if (value.startsWith("db:")) return { type: "db", name: value.slice("db:".length), raw: value };
    return null;
  };
  const tokenName = (value) => {
    const info = tokenInfo(value);
    return info ? info.name : null;
  };
  const effectiveState = (info, row) => {
    if (!row) return "missing";
    const hasSaved = row.has_saved ?? (row.state === "saved");
    const hasEnv = row.has_env ?? (row.state === "env");
    if (info && info.type === "env") return hasEnv ? "env" : "missing";
    if (info && info.type === "db") return hasSaved ? "saved" : "missing";
    return row.state;
  };
  const envHint = () => {
    const info = tokenInfo(block.api_key);
    if (!info) return "";
    const row = state.env.find((r) => r.name === info.name);
    if (!row) return "";
    const eff = effectiveState(info, row);
    const label = ENV_STATES[eff](row);
    return `<small class="${eff === "missing" ? "out" : eff === "env" ? "muted" : ""}">${esc(label)}</small>`;
  };
  const maskedPlaceholder = (row) => {
    if (!row) return "";
    const hasSaved = row.has_saved ?? (row.state === "saved");
    if (hasSaved && row.start && row.length) {
      const rest = Math.max(0, row.length - row.start.length);
      return row.start + "•".repeat(rest);
    }
    if (hasSaved && row.end) return `${row.start || ""}••••`.slice(0, 4) + "•".repeat(Math.max(0, (row.length || 8) - 4));
    if (hasSaved) return "From database";
    if (row.state === "env" || row.has_env) return "From environment";
    return "";
  };
  const placeholderFor = (info, row) => {
    if (!info) return "";
    if (info.type === "env") {
      const eff = effectiveState(info, row);
      if (eff === "env") return "From environment";
      if (eff === "missing") return "Missing — set env var";
      return "";
    }
    if (info.type === "db") {
      if (!row) return "From database";
      const hasSaved = row.has_saved ?? (row.state === "saved");
      if (hasSaved && row.start && row.length) {
        const rest = Math.max(0, row.length - row.start.length);
        return row.start + "•".repeat(rest);
      }
      if (hasSaved) return "From database";
      return "From database";
    }
    return maskedPlaceholder(row);
  };
  const text = (key, label, hint) => {
    if (key === "api_key" || key === "account_id") {
      const info = tokenInfo(block[key]);
      if (info) {
        const row = state.env.find((r) => r.name === info.name);
        const ph = placeholderFor(info, row) || defaults[key] || "";
        const extra = "";
        if (info.type === "env") {
          const shown = info.raw;
          return field(label, hint, `<input class="text" type="text" spellcheck="false" autocomplete="off"
          data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(shown)}" placeholder="${esc(ph)}">${extra}`);
        }
        return field(label, hint, `<input class="text" type="password" spellcheck="false" autocomplete="off"
        data-set='${esc(JSON.stringify([...path, key]))}' value="" placeholder="${esc(ph)}">${extra}`);
      }
    }
    const extra = "";
    return field(label, hint, `<input class="text" type="text" spellcheck="false" autocomplete="off"
    data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(block[key] ?? "")}" placeholder="${esc(defaults[key] ?? "")}">${extra}`);
  };
  const tiers = TIERS.map((tier) => `<div class="tier-row"><span class="tier">${tier.slice(-1)}</span>
    ${listField(tier, block.tier?.[tier], [...path, "tier", tier])}</div>`).join("");
  const models = block.models && typeof block.models === "object" ? block.models : {};
  const overrides = Object.entries(models).map(([pattern, values]) => `<div class="override">
      <input class="text" type="text" spellcheck="false" value="${esc(pattern)}" aria-label="Model pattern"
        data-pattern='${esc(JSON.stringify([...path, "models"]))}' data-key="${esc(pattern)}">
      <div class="pills">${mapPills(values, [...path, "models", pattern], "override", ": ")}</div>
      <button type="button" class="ghost" title="Delete the pattern"
        data-drop='${esc(JSON.stringify([...path, "models", pattern]))}'>&times;</button>
    </div>`).join("");
  const others = Object.keys(block).filter((key) => !FORM_KEYS.includes(key) && key !== "_file");
  const values = others.map((key) => pill(`${key}: ${shown(block[key])}`, path, key)).join("") + adder(path, "column", "+ key");
  const accountField = name === "cloudflare" ? text("account_id", "Account ID", "For Cloudflare: env:NAME or db:NAME. Pasting your ID stores it in the database.") : "";
  return `<div class="card provider" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
    ${text("api_key", "API key", "env:NAME reads the environment variables, pasting your key stores it in the database.")}
    ${accountField}
    ${field("Client keys", "env:NAME reads the environment variables, pasting your key stores it in the database.", `<div class="pills">${mapPills(block.client_keys, [...path, "client_keys"], "match", " = ")}</div>`)}
    ${text("api_base", "API base", "Empty: the default, in gray")}
    ${text("api_type", "API type", "openai or gemini. Empty: the default, in gray")}
    ${text("discovery_url", "Discovery URL", "The model list URL. Empty: the default, in gray")}
    ${field("Discovery match", "The catalog keeps a model when each key matches", `<div class="pills">${mapPills(block.discovery_match, [...path, "discovery_match"], "match", " = ")}</div>`)}
    ${field("Exclude", "Model patterns that never route", listField("exclude", block.exclude, [...path, "exclude"]))}
    ${field("Tiers", "Model patterns for each tier", tiers)}
    ${field("Model overrides", "A pattern and the catalog values that it sets", `${overrides}<div class="pills">${adder([...path, "models"], "pattern", "+ Pattern")}</div>`)}
    ${field("Provider values", "Catalog values for each model of the provider. A model override has priority.", `<div class="pills">${values}</div>`)}
  </div>`;
}

const formColumns = () => Math.max(1, Math.floor($("provider-form").clientWidth / CARD_WIDTH));

// The cards in snug columns: each card goes to the shortest column.
function renderForm() {
  const host = $("provider-form");
  const blocks = state.forms[state.file];
  if (!state.files.length) return (host.innerHTML = "");
  if (blocks == null) {
    host.innerHTML = `<div class="column"><div class="card"><h3>The YAML is not valid</h3>
      <p class="sub">${esc(state.files[state.file].error ?? "")}</p>
      <p class="sub">Fix the file in the YAML view. Then the form opens it.</p></div></div>`;
    return;
  }
  const names = Object.keys(blocks);
  const count = Math.min(formColumns(), Math.max(names.length, 1));
  host.dataset.columns = count;
  host.innerHTML = Array.from({ length: count }, () => `<div class="column"></div>`).join("");
  const columns = [...host.children];
  for (const name of names) {
    const shortest = columns.reduce((best, column) => (column.offsetHeight < best.offsetHeight ? column : best));
    shortest.insertAdjacentHTML("beforeend", providerCard(name, blocks[name]));
  }
}

// 1 card again after an edit, in the same place, so that the columns stay.
function renderCard(name) {
  const card = [...document.querySelectorAll("#provider-form [data-provider]")].find((item) => item.dataset.provider === name);
  if (card) card.outerHTML = providerCard(name, state.forms[state.file][name]);
  renderFiles();
}

// The parent object of a path, with the empty maps and lists that it needs.
function parentOf(path, leaf) {
  let node = state.forms[state.file];
  path.slice(0, -1).forEach((key, index) => {
    const next = index === path.length - 2 ? leaf : {};
    if (node[key] == null || typeof node[key] !== "object") node[key] = next;
    node = node[key];
  });
  return node;
}

function dropAt(path) {
  const parent = path.slice(0, -1).reduce((node, key) => node?.[key], state.forms[state.file]);
  const key = path[path.length - 1];
  if (Array.isArray(parent)) parent.splice(key, 1);
  else if (parent) delete parent[key];
  renderCard(path[0]);
}

function showAdder(button) {
  const path = JSON.parse(button.dataset.add);
  const kind = button.dataset.kind;
  const box = document.createElement("span");
  box.className = "adder";
  const choices = kind === "column" ? [...state.overrideKeys.filter((key) => !MODEL_ONLY.includes(key)), ...PROVIDER_ONLY].sort() : state.overrideKeys;
  const keys = choices.map((key) => `<option>${esc(key)}</option>`).join("");
  const placeholder = { list: "pattern", match: "key = value", pattern: "model pattern", override: "value", column: "value" }[kind];
  box.innerHTML = `${kind === "override" || kind === "column" ? `<select aria-label="Key">${keys}</select>` : ""}
    <input type="text" spellcheck="false" placeholder="${placeholder}">`;
  button.replaceWith(box);
  const input = box.querySelector("input");
  (box.querySelector("select") || input).focus();
  const done = (keep) => {
    const text = input.value.trim();
    if (keep && text) addValue(path, kind, text, box.querySelector("select")?.value);
    else renderCard(path[0]);
  };
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.tagName === "SELECT") return input.focus();
    if (event.key === "Enter") done(true);
    if (event.key === "Escape") done(false);
  });
  box.addEventListener("focusout", () => setTimeout(() => {
    if (box.isConnected && !box.contains(document.activeElement)) done(true);
  }));
}

function addValue(path, kind, text, key) {
  const message = $("save-message");
  message.textContent = "";
  if (kind === "list") {
    parentOf([...path, 0], []).push(parsed(text));
  } else if (kind === "pattern") {
    const models = parentOf([...path, text], {});
    if (text in models) {
      message.className = "message bad";
      message.textContent = `The pattern ${text} is already there.`;
    } else models[text] = {};
  } else {
    const [name, value] = kind === "match" ? text.split(/\s*=\s*(.*)/s) : [key, text];
    if (!name || value === undefined) {
      message.className = "message bad";
      message.textContent = "Write the value as key = value.";
    } else parentOf([...path, name], {})[name] = parsed(value);
  }
  renderCard(path[0]);
}

function renamePattern(input) {
  const path = JSON.parse(input.dataset.pattern);
  const models = path.reduce((node, key) => node[key], state.forms[state.file]);
  const before = input.dataset.key;
  const after = input.value.trim();
  if (after === before) return;
  if (!after || after in models) {
    input.value = before;
    const message = $("save-message");
    message.className = "message bad";
    message.textContent = after ? `The pattern ${after} is already there.` : "A pattern cannot be empty.";
    return;
  }
  const renamed = Object.fromEntries(Object.entries(models).map(([key, value]) => [key === before ? after : key, value]));
  parentOf(path, {})[path[path.length - 1]] = renamed;
  renderCard(path[0]);
}

function setText(input) {
  const path = JSON.parse(input.dataset.set);
  const parent = parentOf(path, {});
  const key = path[path.length - 1];
  const trimmed = input.value.trim();
  const cur = parent[key];
  const curInfo = (() => {
    if (typeof cur !== "string") return null;
    if (cur.startsWith("env:")) return { type: "env", name: cur.slice("env:".length) };
    if (cur.startsWith("db:")) return { type: "db", name: cur.slice("db:".length) };
    return null;
  })();
  if (trimmed) {
    if (key === "api_key") {
      if (trimmed.startsWith("env:")) {
        const name = trimmed.slice("env:".length).trim();
        if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
          parent[key] = `env:${name}`;
        } else {
          parent[key] = trimmed;
        }
      } else if (trimmed.startsWith("db:")) {
        parent[key] = trimmed;
      } else {
        parent[key] = trimmed;
      }
    } else {
      parent[key] = trimmed;
    }
  } else {
    if (key === "api_key" && curInfo) return;
    delete parent[key];
  }
  renderFiles();
}

async function saveForm() {
  if (!dirty()) return;
  const index = state.file;
  const file = state.files[index];
  const message = $("save-message");
  const isToken = (v) => typeof v === "string" && (v.startsWith("env:") || v.startsWith("db:"));
  const hadRaw = Object.values(pruned(state.forms[index]) || {}).some((block) =>
    block && typeof block === "object" && (
      (typeof block.api_key === "string" && block.api_key.trim() && !isToken(block.api_key)) ||
      (block.client_keys && typeof block.client_keys === "object" && Object.values(block.client_keys).some((v) => typeof v === "string" && v.trim() && !isToken(v)))
    ));
  try {
    await call("providers", { method: "PUT", body: JSON.stringify({ path: file.path, blocks: pruned(state.forms[index]) }) });
    await takeFile(index);
    message.className = "message ok";
    message.textContent = hadRaw ? "Saved — key moved to database" : "Saved and reloaded";
    await refreshEnv();
    renderForm();
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    message.className = "message bad";
    message.textContent = error.message;
  }
  renderFiles();
}

async function saveYaml() {
  if (!dirty()) return;
  const index = state.file;
  const file = state.files[index];
  const text = $("editor").value;
  const message = $("save-message");
  try {
    await call("files", { method: "PUT", body: JSON.stringify({ path: file.path, text }) });
    await takeFile(index);
    message.className = "message ok";
    message.textContent = "Saved and reloaded";
    refresh();
    guarded(refreshEnv);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    message.className = "message bad";
    message.textContent = error.message;
  }
  renderFiles();
}

const save = () => (state.view === "form" ? saveForm() : saveYaml());

// The other view shows the saved file. Unsaved changes go after a confirmation.
async function switchView(view) {
  if (view === state.view) return;
  if (dirty() && !(await ask("Discard changes", "The unsaved changes of this file go away.", "Discard", true))) return;
  const index = state.file;
  state.forms[index] = clone(state.files[index].blocks);
  $("editor").value = state.files[index].text = state.saved[index];
  state.view = view;
  $("save-message").textContent = "";
  renderFiles();
  if (view === "form") renderForm();
}

// The Settings form: [group, title, [[key, label, unit, hint], ...]].
const SETTINGS = [
  ["timeouts", "Timeouts", [
    ["request", "Request", "s", "The time to wait for an answer, for all attempts. A stream that started does not stop at this limit."],
    ["wait", "Wait", "s", "The time without data from the provider. Keep-alive bytes do not count."],
    ["slow", "Slow first token", "s", "A first token after this time is slow. Empty: half of Wait."],
  ]],
  ["session_affinity", "Session affinity", [
    ["enabled", "On", "", "Each conversation stays on 1 model."],
    ["change_on_draw", "Change pin on draw", "", "Off: keep the current pin while it remains eligible after a weighted draw."],
    ["idle", "Idle expiry", "s", "The session model expires after this time without a request."],
    ["stay", "Stay share", "", "The share of first-tier draws for the session model. Below 1."],
  ]],
  ["parallel", "Parallel queries", [
    ["enabled", "On", "", "The next models of the chain race the first token, and the session model starts each request."],
    ["count", "Racing models", "", "The models that race the original one. 1 to 10."],
    ["chance", "Race chance", "", "The chance to start the racing models with the original one. 0 to 1."],
    ["slow", "Slow first token", "s", "Seconds with no content from the first model. Then the racing models start."],
    ["penalty", "Loser factor", "x", "The weight factor for the model that loses the race. At most 1."],
  ]],
  ["weights", "Weights", [
    ["enabled", "On", "", "Off: all weights stay at 1, and the chain keeps the usual order."],
    ["success", "Success", "x", "The weight factor for a success."],
    ["fault", "Fault", "x", "The weight factor for a fault."],
    ["slow", "Slow success", "x", "The weight factor for a slow first token."],
    ["hourly", "Hourly recovery", "x", "The weight factor for each hour."],
    ["rate_limit", "Rate limit", "x", "The weight factor for an HTTP 429."],
  ]],
  ["cooldown", "Cooldown", [
    ["first", "First backoff", "s", "The cooldown of a first 429 with no reset time."],
    ["longest", "Longest backoff", "s", "Each next 429 doubles the cooldown, up to this time."],
  ]],
  ["loops", "Loop detection", [
    ["calls", "Tool calls", "", "Repeated identical calls since the last user message; 2–100."],
    ["repeats", "Text repeats", "", "Consecutive copies of a text passage; 2–16."],
    ["shortest", "Shortest passage", "", "Minimum period in characters; 1–1,000 and no greater than Longest."],
    ["longest", "Longest passage", "", "Maximum period in characters; 1–10,000."],
  ]],
  ["pacing", "Pacing", [
    ["enabled", "On", "", "A model at its rpm or tpm for the last minute leaves the chains."],
  ]],
  ["catalog", "Catalog", [
    ["every", "Rebuild interval", "h", "The hours between rebuilds. 0 stops them."],
    ["anchor", "Anchor hour", "h", "The local hour (TZ) that the rebuild times start from."],
  ]],
  ["headroom", "Headroom", [
    ["timeout", "Timeout", "s", "After this time, the original messages go to the provider."],
  ]],
  ["escalation", "Escalation", [
    ["keywords", "Keywords", "list", "1 word or phrase on each line. A match in the last user message moves the daedalus/auto session tier 1 step up."],
  ]],
  ["switch", "Switch", [
    ["keywords", "Keywords", "list", "1 word or phrase on each line. A match in the last user message gives the pool session another model of the same tier."],
  ]],
  ["dashboard", "Dashboard", [
    ["theme", "Theme", "choice", "System follows the light or dark setting of the device."],
    ["time_format", "Time format", "choice", "The clock of each time on the dashboard."],
  ]],
  ["request_hooks", "Request hooks", [
    ["on-request", "On request", "path", "The Python file of the config folder that sets the key of a turn. Empty: no hook."],
  ]],
  ["pools", "Pool names", [
    ["moros", "Tier D", "name", "The client name of the tier D pool. The old name gets HTTP 400."],
    ["koinos", "Tier C", "name", "The client name of the tier C pool."],
    ["deinos", "Tier B", "name", "The client name of the tier B pool."],
    ["sophos", "Tier A", "name", "The client name of the tier A pool."],
    ["graphos", "Transcription", "name", "The client name of the transcription pool."],
    ["photos", "Image", "name", "The client name of the image pool."],
  ]],
];

// The Settings cards of each column, from top to bottom.
const SETTINGS_COLUMNS = [["timeouts", "catalog", "pacing", "pools"], ["session_affinity", "headroom", "cooldown", "loops", "dashboard"], ["weights", "parallel", "escalation", "switch", "request_hooks"]];
// The options of each choice field.
const CHOICES = {
  theme: [["system", "System"], ["light", "Light"], ["dark", "Dark"]],
  time_format: [["24h", "24 h"], ["12h", "12 h"]],
};

// The theme of the page. System follows the device, also before the login.
const darkDevice = matchMedia("(prefers-color-scheme: dark)");
let theme = "system";
function applyTheme(value = theme) {
  theme = value;
  const dark = theme === "dark" || (theme === "system" && darkDevice.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
darkDevice.addEventListener("change", () => applyTheme());
applyTheme();

// The fields that take decimals. The other fields take whole numbers.
const DECIMALS = new Set([
  "session_affinity.stay", "parallel.chance", "parallel.penalty",
  "weights.success", "weights.fault", "weights.slow", "weights.hourly", "weights.rate_limit",
]);
// The input bounds. The server also checks them before saving.
const MINIMA = { "loops.calls": 2, "loops.repeats": 2, "loops.shortest": 1, "loops.longest": 1, "parallel.count": 1 };
const MAXIMA = {
  "catalog.anchor": 23,
  "timeouts.request": 86400,
  "timeouts.wait": 86400,
  "timeouts.slow": 86400,
  "headroom.timeout": 86400,
  "parallel.count": 10,
  "parallel.chance": 1,
  "parallel.penalty": 1,
  "loops.calls": 100,
  "loops.repeats": 16,
  "loops.shortest": 1000,
  "loops.longest": 10000,
};

// The value in the file, or null when the file does not set it.
const fileValue = (group, key) => state.settings.file?.[group]?.[key] ?? null;
const setting = (group, key) => fileValue(group, key) ?? state.settings.defaults[group][key];
// The boolean settings, such as `enabled` and `change_on_draw`, are checkboxes in the form.
const isSwitch = (group, key) => typeof state.settings.defaults[group][key] === "boolean";

function renderSettings() {
  $("settings-path").textContent = `${fileName(state.settings.path)} · Ctrl+S saves and reloads`;
  const card = ([group, title, fields]) => `
    <div class="card"><h3>${esc(title)}</h3>${fields.map(([key, label, unit, hint]) => {
      const id = `set-${group}-${key}`;
      if (isSwitch(group, key)) {
        return `<label class="field check" for="${id}"><input type="checkbox" id="${id}"
          ${setting(group, key) ? "checked" : ""}><span><b>${esc(label)}</b><small>${esc(hint)}</small></span></label>`;
      }
      if (unit === "choice") {
        return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <select id="${id}">${CHOICES[key].map(([name, text]) => `<option value="${name}"${setting(group, key) === name ? " selected" : ""}>${text}</option>`).join("")}</select></label>`;
      }
      if (unit === "name") {
        return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <span class="input"><i class="prefix">daedalus/</i><input type="text" id="${id}" maxlength="40" spellcheck="false"
            value="${esc(fileValue(group, key) ?? "")}" placeholder="${esc(state.settings.defaults[group][key])}"></span></label>`;
      }
      if (unit === "path") {
        const value = fileValue(group, key);
        return `<label class="field stack" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <span class="input"><input type="text" id="${id}" spellcheck="false" value="${esc(value ?? "")}"
            placeholder="${esc(state.settings.defaults[group][key] || "No hook")}"></span></label>`;
      }
      if (unit === "list") {
        return `<label class="field stack" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <textarea id="${id}" rows="8" spellcheck="false" placeholder="No keywords">${esc(setting(group, key).join("\n"))}</textarea></label>`;
      }
      const fallback = state.settings.defaults[group][key];
      const value = fileValue(group, key);
      return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
        <span class="input"><input type="number" min="${MINIMA[`${group}.${key}`] ?? 0}" id="${id}" value="${value ?? ""}"
          step="${DECIMALS.has(`${group}.${key}`) ? "any" : "1"}" ${MAXIMA[`${group}.${key}`] ? `max="${MAXIMA[`${group}.${key}`]}"` : ""}
          placeholder="${fallback ?? "half of Wait"}"><i>${esc(unit)}</i></span></label>`;
    }).join("")}</div>`;
  const cards = Object.fromEntries(SETTINGS.map((item) => [item[0], card(item)]));
  $("settings").innerHTML = SETTINGS_COLUMNS
    .map((column) => column.filter((group) => group !== "headroom" || state.settings.headroom_available))
    .map((groups) => `<div class="column">${groups.map((group) => cards[group]).join("")}</div>`)
    .join("");
  renderSettingsSave();
}

function settingsChanges() {
  if (!state.settings) return {};
  const changes = {};
  for (const [group, , fields] of SETTINGS) {
    if (group === "headroom" && !state.settings.headroom_available) continue;
    for (const [key, , unit] of fields) {
      const input = $(`set-${group}-${key}`);
      if (!input) continue;
      let value, before;
      if (unit === "list") {
        value = input.value.split("\n").map((text) => text.trim()).filter(Boolean);
        if (JSON.stringify(value) !== JSON.stringify(setting(group, key))) (changes[group] ||= {})[key] = value;
        continue;
      }
      if (isSwitch(group, key)) {
        value = input.checked;
        before = setting(group, key);
      } else if (unit === "choice") {
        value = input.value;
        before = setting(group, key);
      } else if (unit === "name") {
        value = input.value.trim() || null;
        before = fileValue(group, key);
      } else if (unit === "path") {
        value = input.value.trim();
        before = fileValue(group, key) ?? "";
      } else {
        value = input.value.trim() === "" ? null : Number(input.value);
        before = fileValue(group, key);
      }
      if (value !== before) (changes[group] ||= {})[key] = value;
    }
  }
  return changes;
}

function settingsDirty() {
  if (!state.settings) return false;
  if (state.settingsView === "yaml") return $("settings-editor").value !== state.settings.text;
  return Object.keys(settingsChanges()).length > 0;
}

function renderSettingsSave() {
  $("settings-save").disabled = !settingsDirty();
  document.querySelectorAll("#settings-views [data-view]")
    .forEach((tab) => tab.classList.toggle("on", tab.dataset.view === state.settingsView));
  $("settings").hidden = state.settingsView !== "form";
  $("settings-yaml").hidden = state.settingsView !== "yaml";
}

async function loadSettings() {
  state.settings = await call("settings");
  applyTheme(setting("dashboard", "theme"));
  hourCycle = setting("dashboard", "time_format") === "12h" ? "h12" : "h23";
  $("settings-editor").value = state.settings.text;
  renderSettings();
}

// The other view shows the saved file. Unsaved changes go after a confirmation.
async function switchSettingsView(view) {
  if (view === state.settingsView) return;
  if (settingsDirty() && !(await ask("Discard changes", "The unsaved settings changes go away.", "Discard", true))) return;
  state.settingsView = view;
  $("settings-editor").value = state.settings.text;
  $("settings-message").textContent = "";
  renderSettings();
}

async function saveSettings() {
  if (!settingsDirty()) return;
  const body = state.settingsView === "yaml" ? { text: $("settings-editor").value } : { changes: settingsChanges() };
  const message = $("settings-message");
  try {
    await call("settings", { method: "PUT", body: JSON.stringify(body) });
    await loadSettings();
    message.className = "message ok";
    message.textContent = "Saved and reloaded";
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    message.className = "message bad";
    message.textContent = error.message;
  }
  renderSettingsSave();
}

async function refreshFast() {
  const [status, requests] = await Promise.all([call("status"), call(`requests?limit=${state.requestLimit}`)]);
  renderStatus(status);
  state.requests = splitRequests(requests);
  renderRequests(state.requests);
  $("more-requests").hidden = requests.length < state.requestLimit || state.requestLimit >= REQUESTS_KEPT;
  // After a rebuild, the pools and models change too.
  if (state.catalog.rebuilding && !status.catalog?.rebuilding) guarded(refreshSlow);
  state.catalog = status.catalog || {};
  renderOverview();
}

// The Limits page: the balances of the provider keys, and the last rate-limit headers of each model.
// Counts from 100,000 show short, for example 998.8M, so the bar keeps its room.

// The short unit of a rate-limit row, for example tok/min.
const SPANS = { minute: "min", hour: "h", day: "day", month: "mo" };
const unit = (r) => `${r.kind === "tokens" ? "tok" : "req"}${r.span ? `/${SPANS[r.span] || r.span}` : ""}`;
const SPAN_SHORT = { minute: "M", hour: "H", day: "D", month: "MO" };
const SPAN_WORD = { minute: "minute", hour: "hour", day: "day", month: "month" };
function limitUnit(r) {
  if (!SPAN_SHORT[r.span]) return r.kind;
  return `${r.kind === "tokens" ? "T" : "R"}P${SPAN_SHORT[r.span]}`;
}
// The full name of a short unit, for the hover text of the Limit column, for example Requests per day.
function limitTitle(r) {
  const kind = r.kind === "tokens" ? "Tokens" : "Requests";
  return r.span ? `${kind} per ${SPAN_WORD[r.span] || r.span}` : kind;
}

// The balances, then the 3 rate-limit rows with the least left.
function overviewLimits(data) {
  if (!data) return "";
  const bar = (left) => (left == null ? "" : weightBar(left));
  const balances = data.providers.flatMap((p) => p.items.map(([label, value, left]) =>
    `<div class="balance">${line(`${esc(p.name)} &middot; ${esc(label)}`, esc(value))}${bar(left)}</div>`));
  const share = (r) => (r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0);
  const rows = data.lanes.flatMap((lane) => lane.rows.map((row) => ({ ...row, model: lane.model })))
    .sort((a, b) => share(a) - share(b)).slice(0, 3)
    .map((r) => `<div class="balance">${line(`<span title="${esc(r.model)}">${esc(r.model)}</span>`,
      `${floorCount(r.remaining)} of ${floorCount(r.limit)} ${esc(unit(r))}`)}${bar(share(r))}</div>`);
  return [...balances, ...rows].join("");
}

function renderLimits(data) {
  state.limits = data;
  $("limits-checked").textContent = data.checked ? `Checked ${shortTime(data.checked)} · each hour` : "Not checked yet";
  $("balances").hidden = !data.providers.length;
  $("balances").innerHTML = data.providers.map((p) => `<div class="card"><h3>${esc(p.name)}</h3>
    ${p.items.map(([label, value, left]) => `<div class="balance">${line(esc(label), esc(value))}
      ${left == null ? "" : weightBar(left)}</div>`).join("")}</div>`).join("");
  const rows = data.lanes.flatMap((lane) => lane.rows.map((row) => ({ ...row, model: lane.model, client: lane.client, at: lane.at })));
  $("limit-rows").innerHTML = rows.length ? rows.map((r) => `<tr>
      ${nameCell(r.model, `${esc(r.model)}${r.client ? ` <span class="muted">${esc(r.client)}</span>` : ""}`)}
      <td title="${esc(limitTitle(r))}">${esc(limitUnit(r))}</td>
      <td><div class="weight left" title="${r.remaining.toLocaleString()} of ${r.limit.toLocaleString()}">
        ${weightBar(r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0)}
        <span class="num${r.remaining > 0 ? "" : " out"}">${floorCount(r.remaining)} of ${floorCount(r.limit)}</span></div></td>
      <td class="hide-sm muted time">${r.reset ? shortTime(r.reset) : "-"}</td>
      <td class="hide-sm muted time">${shortTime(r.at)}</td>
    </tr>`).join("") : '<tr><td colspan="5" class="empty">No rate-limit headers yet. Groq and Mistral send them with each answer.</td></tr>';
}

async function refreshSlow() {
  const [pools, models, limits] = await Promise.all([call("pools"), call("models"), call("limits"), refreshKeys()]);
  renderLimits(limits);
  renderPools(pools);
  state.pools = pools;
  state.models = models;
  renderModels();
  renderOverview();
}

function refresh() {
  guarded(refreshFast);
  guarded(refreshSlow);
}

async function start() {
  // The settings come first, so that the first tables use the time format.
  await loadSettings();
  await refreshFast();
  await refreshSlow();
  takeFiles(await call("files"));
  await refreshEnv();
  [state.overrideKeys, state.providerDefaults] = await Promise.all([call("provider-keys"), call("provider-defaults")]);
  state.file = 0;
  $("editor").value = state.files[0]?.text ?? "";
  showFileError(0);
  renderFiles();
  renderTiers();
  renderOverview();
  $("login").hidden = true;
  $("app").hidden = false;
  if (!document.querySelector(`section[data-page="providers"]`).hidden) renderForm();
  state.timers = [
    // A hidden tab asks the server for nothing. It refreshes when it shows again.
    setInterval(() => document.hidden || guarded(refreshFast), 5000),
    setInterval(() => document.hidden || guarded(refreshSlow), 15000),
    setInterval(tickCooldowns, 1000),
    setInterval(tickLive, 100),
  ];
  openLive();
}

$("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    const answer = await call("login", {
      method: "POST",
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
        remember: form.remember.checked,
      }),
    });
    keepSession(answer.session, form.remember.checked);
    form.password.value = "";
    showPassword(false);
    await guarded(start);
  } catch (error) {
    $("login-message").textContent = error.message;
  }
});

$("limits-check").addEventListener("click", async () => {
  const button = $("limits-check");
  button.disabled = true;
  $("limits-message").textContent = "Checking";
  try {
    renderLimits(await call("limits", { method: "POST" }));
    $("limits-message").textContent = "";
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    $("limits-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("show-password").addEventListener("click", () => showPassword($("login").password.type === "password"));
document.addEventListener("click", async (event) => {
  if (event.target.closest(".rebuild")) return rebuildCatalog();
  if (!event.target.closest(".logout")) return;
  if (!(await ask("Log out", "The dashboard session ends.", "Log out"))) return;
  await call("logout", { method: "POST" }).catch(() => {});
  keepSession(null);
  showLogin();
});
$("reset-weights").addEventListener("click", async () => {
  const message = $("reset-message");
  message.textContent = "";
  const go = await ask("Reset weights and cooldowns", "All weights go back to 1. All cooldowns end. Session models stay.", "Reset", true);
  if (!go) return;
  try {
    await call("reset", { method: "POST" });
    message.className = "message ok";
    message.textContent = "All weights are 1. No cooldowns.";
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    message.className = "message bad";
    message.textContent = error.message;
  }
});

// A field that stops at its length says the rule, so the cut is not silent.
function showLengthLimit(input, message) {
  if (input.maxLength > 0 && input.value.length >= input.maxLength) {
    message.textContent = `${input.maxLength} characters at most`;
  } else if (message.textContent.endsWith("characters at most")) {
    message.textContent = "";
  }
}

$("new-key").addEventListener("input", () => showLengthLimit($("key-name"), $("key-message")));

$("new-key").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("key-message");
  message.textContent = "";
  try {
    const made = await call("keys", { method: "POST", body: JSON.stringify({ name: $("key-name").value }) });
    $("key-name").value = "";
    $("reveal-name").textContent = made.name;
    $("reveal-key").textContent = made.key;
    $("reveal").hidden = false;
    guarded(refreshKeys);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    message.textContent = error.message;
  }
});
$("keys").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-key]");
  if (!button) return;
  const name = button.dataset.key;
  if (!(await ask("Delete key", `Clients with the key "${name}" get 401 at once.`, "Delete", true))) return;
  await guarded(async () => {
    await call("keys/" + encodeURIComponent(name), { method: "DELETE" });
    await refreshKeys();
  });
});
$("env-rows").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-env]");
  if (!form) return;
  event.preventDefault();
  const input = form.querySelector("input");
  $("env-message").textContent = "";
  try {
    renderEnv(await call("env", { method: "PUT", body: JSON.stringify({ name: form.dataset.env, value: input.value }) }));
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    $("env-message").textContent = error.message;
  }
});
$("env-rows").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-env-clear]");
  if (!button) return;
  const name = button.dataset.envClear;
  if (!(await ask("Clear the saved value", `${name} then reads the environment variable.`, "Clear", true))) return;
  $("env-message").textContent = "";
  try {
    renderEnv(await call("env", { method: "DELETE", body: JSON.stringify({ name }) }));
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    $("env-message").textContent = error.message;
  }
});
$("more-requests").addEventListener("click", () => {
  state.requestLimit = Math.min(state.requestLimit + REQUESTS_STEP, REQUESTS_KEPT);
  guarded(refreshFast);
});

$("requests").addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-chain");
  if (button) {
    const found = state.requests.find((r) => String(r.at) === button.dataset.at);
    try {
      await navigator.clipboard.writeText(chainText(found));
      button.textContent = "Copied";
    } catch {
      getSelection().selectAllChildren(button.closest("td"));
      button.textContent = "Press Ctrl+C";
    }
    return;
  }
  if (event.target.closest(".mobile-request-more") || window.matchMedia("(max-width: 720px)").matches) return;
  const row = event.target.closest("tr.request");
  if (!row || !row.classList.contains("has-chain") || !getSelection().isCollapsed) return;
  const at = row.dataset.at;
  opened.has(at) ? opened.delete(at) : opened.add(at);
  shownRequests = "";
  renderRequests(state.requests);
});
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("reveal-key").textContent);
    $("copy").textContent = "Copied";
  } catch {
    getSelection().selectAllChildren($("reveal-key"));
    $("copy").textContent = "Press Ctrl+C";
  }
});
$("reveal-close").addEventListener("click", () => {
  $("reveal").hidden = true;
  $("reveal-key").textContent = "";
  $("copy").textContent = "Copy";
});
const PAGES = ["overview", "requests", "models", "keys", "providers", "limits", "settings"];

// A Models link such as #/models?tier=C&mode=chat&sort=weight sets the filters.
function applyModelFilters(query) {
  const params = new URLSearchParams(query);
  state.tier = ["All", "A", "B", "C", "D"].includes(params.get("tier")) ? params.get("tier") : "All";
  state.mode = MODES[params.get("mode")] || FLAGS[params.get("mode")] ? params.get("mode") : "all";
  state.sort = sortValue[params.get("sort")] ? { key: params.get("sort"), dir: -1 } : { key: "", dir: 1 };
  $("search").value = "";
  renderTiers();
  renderSortHeads();
  markPools();
  if (state.models.length) renderModels();
  history.replaceState(null, "", "#/models");
}

// A fade at an edge of the tab bar shows the tabs that wait off screen.
const nav = $("nav");
function markNavFades() {
  const end = nav.scrollWidth - nav.clientWidth;
  nav.classList.toggle("fade-left", nav.scrollLeft > 2);
  nav.classList.toggle("fade-right", nav.scrollLeft < end - 2);
}
nav.addEventListener("scroll", markNavFades, { passive: true });

function showPage() {
  const [path, query] = location.hash.split("?");
  const asked = path.replace("#/", "").replace(/^config$/, "providers").replace(/^pools$/, "models");
  if (asked === "models" && query !== undefined) applyModelFilters(query);
  const page = PAGES.includes(asked) ? asked : PAGES[0];
  document.querySelectorAll("section[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  document.querySelectorAll("#nav a").forEach((link) => {
    link.classList.toggle("on", link.dataset.page === page);
  });
  // The first paint marks the fades too, so a cut tab shows before the first status answer.
  markNavFades();
  document.title = `daedalus · ${document.querySelector(`#nav a[data-page="${page}"]`).firstChild.textContent}`;
  if (page === "providers" && state.view === "form") renderForm();
}

window.addEventListener("hashchange", showPage);
showPage();
$("search").addEventListener("input", renderModels);
$("model-head").addEventListener("click", (event) => {
  const th = event.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  // Up, then down, then back to the default order.
  const same = state.sort.key === key;
  state.sort = !same ? { key, dir: 1 } : state.sort.dir > 0 ? { key, dir: -1 } : { key: "", dir: 1 };
  renderSortHeads();
  renderModels();
});
$("mode").addEventListener("change", () => {
  state.mode = $("mode").value;
  renderModels();
});
$("tiers").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tier]");
  if (!button) return;
  state.tier = button.dataset.tier;
  renderTiers();
  renderModels();
});
$("files").addEventListener("click", (event) => {
  const button = event.target.closest("[data-file]");
  if (button) openFile(Number(button.dataset.file));
});
async function reloadFiles(keep) {
  takeFiles(await call("files"));
  const index = Math.max(0, state.files.findIndex((file) => file.path === keep));
  state.file = index;
  $("editor").value = state.files[index]?.text ?? "";
  showFileError(index);
  renderFiles();
  renderForm();
  guarded(refreshEnv);
}

$("new-provider").addEventListener("click", async () => {
  const message = $("save-message");
  message.textContent = "";
  const name = prompt("Provider name: lowercase letters, digits and dashes.");
  if (!name) return;
  try {
    const made = await call("files", { method: "POST", body: JSON.stringify({ name }) });
    await reloadFiles(made.path);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    message.textContent = error.message;
  }
});
$("drop-provider").addEventListener("click", async () => {
  const message = $("save-message");
  message.textContent = "";
  const file = state.files[state.file];
  if (!file || !(await ask("Delete file", `${file.path} goes away. The main file stays.`, "Delete", true))) return;
  try {
    await call("files", { method: "DELETE", body: JSON.stringify({ path: file.path }) });
    await reloadFiles(null);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    message.textContent = error.message;
  }
});
$("editor").addEventListener("input", renderFiles);
$("save").addEventListener("click", save);
$("views").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-view]");
  if (tab) switchView(tab.dataset.view);
});
$("provider-form").addEventListener("click", (event) => {
  const drop = event.target.closest("[data-drop]");
  if (drop) return dropAt(JSON.parse(drop.dataset.drop));
  const add = event.target.closest("[data-add]");
  if (add) showAdder(add);
});
$("provider-form").addEventListener("input", (event) => {
  if (event.target.dataset.set) setText(event.target);
});
$("provider-form").addEventListener("change", (event) => {
  if (event.target.dataset.pattern) renamePattern(event.target);
});
$("provider-form").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.dataset.pattern) event.target.blur();
});
window.addEventListener("resize", () => {
  markNavFades();
  const host = $("provider-form");
  if (!host.hidden && host.clientWidth && Number(host.dataset.columns) !== formColumns()) renderForm();
});
$("settings").addEventListener("input", (event) => {
  $("settings-message").textContent = "";
  showLengthLimit(event.target, $("settings-message"));
  renderSettingsSave();
});
$("settings-save").addEventListener("click", saveSettings);
$("settings-views").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-view]");
  if (tab) switchSettingsView(tab.dataset.view);
});
$("settings-editor").addEventListener("input", () => {
  $("settings-message").textContent = "";
  renderSettingsSave();
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (location.hash === "#/settings") saveSettings();
    else save();
  }
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.timers?.length) refresh();
});
window.addEventListener("beforeunload", (event) => {
  if (dirty() || settingsDirty()) event.preventDefault();
});

// The dialog closes on Confirm, on Cancel, on Escape and on a backdrop click.
$("modal").addEventListener("close", (event) => {
  const done = settle;
  settle = null;
  done?.(event.target.returnValue === "ok");
});

renderLegend();
guarded(start);
