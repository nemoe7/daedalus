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
// The hour cycle of each shown time, from personalization.time_format: h23 for 24h, h12 for 12h.
let hourCycle = "h23";
// The stamp of an absolute time: 2026-10-04 12:30:46.
const stamp = (seconds) => {
  const date = new Date(seconds * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const time = date.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle,
  });
  return `${day} ${time}`;
};
// A relative time for the catalog: 2 hours ago, in 6 hours, 12 mins ago, in 3 mins.
function relative(seconds) {
  const minutes = (seconds * 1000 - Date.now()) / 60000;
  const ago = minutes <= 0;
  const size = ago ? Math.floor(-minutes) : Math.ceil(minutes);
  if (ago && size === 0) return "just now";
  const [count, unit] = size < 60 ? [size, "min"] : [ago ? Math.floor(size / 60) : Math.ceil(size / 60), "hour"];
  return `${ago ? `${count} ${unit}${count === 1 ? "" : "s"} ago` : `in ${count} ${unit}${count === 1 ? "" : "s"}`}`;
}

const state = {
  models: [], tier: "All", mode: "all", sort: { key: "", dir: 1 }, files: [], file: 0, saved: [], timers: [],
  view: "form", forms: [], formSaved: [], overrideKeys: [], providerDefaults: {}, settingsView: "form",
  pools: [], requests: [], requestLimit: REQUESTS_STEP, requestFetched: 0, keys: [], catalog: {},
  settings: null, legendExtra: [],
  requestSearch: "", requestStatus: "all", requestHours: 0, limitSearch: "",
  live: new Map(), livePaused: false, liveWaiting: new Set(), liveSeen: new Set(), liveCount: 0,
  source: null, env: [],
};

const fileName = (path) => path.split(/[\\/]/).pop();
const line = (left, right) => `<div class="line"><span>${left}</span><span>${right}</span></div>`;
const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const none = (text) => `<div class="more">${text}</div>`;
// A model name cell that ends in an ellipsis when it is too long. The title shows the full name.
const mobileLabel = (text) => `<span class="mobile-label" aria-hidden="true">${esc(text)}</span>`;
const nameCell = (text, shown = esc(text), label = "Model", extra = "") =>
  `<td role="cell" class="name" title="${esc(text)}">${label ? mobileLabel(label) : ""}<span class="cell-value">${shown}</span>${extra}</td>`;
// The marks beside a model name: `provider/developer/slug`. The provider mark drops when the
// developer carries the same name. The set names the SVG files that ship under `ui/icons/`; a name
// with no file shows its own text in place of a mark.
const MARK_FILES = new Set([
  "anthropic", "baai", "black-forest-labs", "bytedance", "cloudflare", "cohere", "daedalus",
  "deepseek-ai",
  "dots-studio", "fish-audio", "gemini", "google", "groq", "ibm", "ibm-granite", "inception",
  "kilo", "liquid", "meta", "meta-llama", "microsoft", "mistral", "mistralai", "moonshotai",
  "myshell-ai", "nvidia", "openai", "openrouter", "pollinations", "poolside", "qwen", "runwayml",
  "stabilityai", "stepfun", "z-ai", "zai-org",
]);
const mark = (name) => MARK_FILES.has(name)
  ? `<img class="mark" src="ui/icons/${esc(name)}.svg" alt="" aria-hidden="true">`
  : `<span class="mark slug">${esc(name)}</span>`;
// `provider/dev/model` and `provider/model` both land. The developer is the part before the model,
// with a scope prefix such as `@cf/` dropped.
const parts = (id) => {
  const bits = String(id).split("/");
  const provider = bits.shift();
  const model = bits.length ? bits.pop() : null;
  let dev = bits.length ? bits[bits.length - 1].replace(/^@[^/]+\//, "") : null;
  if (dev && dev.toLowerCase() === provider.toLowerCase()) dev = null;
  return { provider, dev, model };
};
// The separator of the parts, so the cell reads as the model id does.
const sep = '<span class="mark-sep" aria-hidden="true">/</span>';
const modelName = (id, pool = "") => {
  const { provider, dev, model } = parts(id);
  if (model === null) return esc(id);
  const heads = (dev ? [provider, dev] : [provider]).map(mark);
  const tail = pool ? sep + `<span class="model-part">${esc(pool)}</span>` : "";
  return heads.join(sep) + sep + `<span class="model-part">${esc(model)}</span>` + tail;
};

// The pool rides in the slug of the auto model: daedalus/auto/moros. The field may carry the
// full pool name, such as daedalus/moros, and the slug keeps the last part.
const poolOf = (r) => {
  const pool = String(r.pool || "").replace(/^daedalus\//, "");
  return pool && String(r.model || "").startsWith("daedalus/auto") ? pool : "";
};
const cell = (label, inner, cls = "", attrs = "") =>
  `<td role="cell"${cls ? ` class="${cls}"` : ""}${attrs}>${mobileLabel(label)}<span class="cell-value">${inner}</span></td>`;

// A confirmation modal in place of window.confirm. It resolves true on Confirm.
// A placeholder shows the name field: the caller reads modal-input after a true.
// The settle promise lives on the sandbox so a test can resolve the dialog.
function ask(title, message, confirm = "Confirm", danger = false, placeholder = null, value = "") {
  return new Promise((resolve) => {
    globalThis.__settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    $("modal-field").hidden = placeholder === null;
    $("modal-field2").hidden = true;
    const box = $("modal-input");
    box.value = value;
    box.placeholder = placeholder ?? "";
    const ok = $("modal-ok");
    ok.textContent = confirm;
    ok.classList.toggle("danger", danger);
    $("modal").returnValue = "";
    $("modal").showModal();
    if (placeholder !== null) box.focus?.();
    if (value) box.select?.();
  });
}

// The 2-field modal: a name and its value, for a client key. It resolves true on Confirm,
// and the caller reads modal-input and modal-input2 after a true.
function askPair(title, message, nameLabel, valueLabel, name = "", value = "", confirm = "Save") {
  return new Promise((resolve) => {
    globalThis.__settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    $("modal-field").hidden = false;
    $("modal-field2").hidden = false;
    const nameBox = $("modal-input");
    nameBox.value = name;
    nameBox.placeholder = nameLabel;
    const valueBox = $("modal-input2");
    valueBox.value = value;
    valueBox.placeholder = valueLabel;
    const ok = $("modal-ok");
    ok.textContent = confirm;
    ok.classList.toggle("danger", false);
    $("modal").returnValue = "";
    $("modal").showModal();
    nameBox.focus?.();
    if (name) nameBox.select?.();
  });
}

// The small editor of a value row, on the modal manager. The close of that dialog writes the
// 1 value: Save writes it, and Cancel or Escape leaves the file alone.
async function editValue(input, write) {
  const box = $("modal-input");
  box.type = input.type === "password" ? "password" : "text";
  const done = ask(`Edit ${input.dataset.value}`, "The new value goes to the file when this dialog closes.",
    "Save", false, input.placeholder ?? "", input.value);
  if (!(await done)) {
    box.type = "text";
    return;
  }
  box.type = "text";
  if (box.value === input.value) return;
  input.value = box.value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  state.writeAnchor = anchorOf(input);
  write(input);
}

// A value row: the value shows as text, and a click opens the small editor.
function bindValueRows(host, write) {
  host.addEventListener("click", (event) => {
    const input = event.target.closest("[data-value]");
    if (!input) return;
    event.preventDefault();
    editValue(input, write);
  });
}

// The anchor of a write: the row of the control that caused it. A value row holds its editor,
// so the icon lands inside that row.
const anchorOf = (node) => node?.closest(".pills, .tier-row, .override, .input, .field") ?? node ?? null;

// A number of the Overview strip: the value on top, its name below.
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><small>${esc(label)}</small></div>`;

// One card for each page, from the data that the pages already read.
function renderOverview() {
  const failed = state.requests.filter((r) => ["s4", "s5"].includes(statusClass(r))).length;
  const answered = state.requests.length - failed;
  const share = state.requests.length ? `${Math.round((answered / state.requests.length) * 100)}%` : "-";
  const lanes = state.limits ? state.limits.lanes.flatMap((lane) => lane.rows) : [];
  const left = lanes.length
    ? `${Math.round(Math.min(...lanes.map((r) => (r.limit > 0 ? r.remaining / r.limit : 1))) * 100)}%`
    : "-";
  draw("ov-kpis", [
    kpi(state.live.size, "In flight now"),
    kpi(share, `Success of last ${count(state.requests.length, "request")}`),
    kpi(left, "Lowest limit left"),
    kpi(state.models.length || 0, "Models in the catalog"),
  ].join(""));
  draw("ov-requests", state.requests.slice(0, 5).map((r) => line(
    `<span class="status ${statusClass(r)}">${statusCell(r)}</span> <span title="${esc(r.via || r.model || "")}">${r.via || r.model ? modelName(r.via || r.model) : "-"}</span>`,
    stamp(r.at),
  )).join("") || none("No requests"));
  $("ov-model-count").textContent = state.models.length || "";
  // Each pool with its mean weight and the model that served it most.
  const served = {};
  for (const r of state.requests) served[r.via || r.model] = (served[r.via || r.model] || 0) + 1;
  draw("ov-models", state.pools.map((pool) => {
    const top = topModel(pool.members, served);
    const health = poolHealth(pool.members);
    return `<div class="pool-line"><div class="line"><span>${esc(pool.shown.replace("daedalus/", ""))}</span>
      <span title="${esc(top?.id)}">${top ? modelName(top.id) : "no models"}</span></div>${health === null ? "" : weightBar(health)}</div>`;
  }).join("") || none(state.models.length ? "No pools" : "No models. Run daedalus catalog."));
  draw("ov-limits", overviewLimits(state.limits) || none("No limits yet"));
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

// A call that runs longer than a blink shows the header spinner, so a slow answer never reads as a
// dead page. The count holds the ring while any call of the page is open, and a fast call shows
// nothing: the timer waits out the grace period first.
let calling = 0;
let spinnerTimer = null;
function showSpinner(on) {
  const ring = $("spinner");
  if (on) ring.classList.add("on");
  else ring.classList.remove("on");
}

async function call(path, options = {}) {
  calling += 1;
  if (calling === 1) spinnerTimer = setTimeout(() => showSpinner(true), 300);
  try {
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
  } finally {
    calling -= 1;
    if (!calling) {
      clearTimeout(spinnerTimer);
      showSpinner(false);
    }
  }
}

// The first load draws placeholder rows, so a table shows its shape while the answer is on the way.
function skeletons(id, cols, rows = 3) {
  $(id).innerHTML = Array.from({ length: rows }, () =>
    `<tr class="skeleton" aria-hidden="true">${`<td><span></span></td>`.repeat(cols)}</tr>`).join("");
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

// A read failure: 1 line in the page and 1 button to ask again. A later read of the server hides it.
function showNotice(message) {
  $("notice-text").textContent = message || "The dashboard cannot read the server.";
  $("notice").hidden = false;
}

function clearNotice() {
  $("notice").hidden = true;
}

async function guarded(task) {
  try {
    await task();
    clearNotice();
  } catch (error) {
    if (error instanceof LoggedOut) showLogin();
    else {
      showNotice(error.message);
      console.error(error);
    }
  }
}

async function rebuildCatalog() {
  const go = await ask("Rebuild the catalog", "daedalus reads the model list of each provider again.", "Rebuild");
  if (!go) return;
  try {
    await call("catalog", { method: "POST" });
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    showNotice(error.message);
  }
  guarded(refreshFast);
}

// The status card of a phone: the health row, then 1 line for each value. The catalog line starts a rebuild.
function renderStatusCard(status) {
  const { built, next, rebuilding } = status.catalog || {};
  const last = rebuilding ? "rebuilding" : built ? relative(built) : "never";
  const following = next ? ` &middot; Next ${esc(relative(next))}` : " &middot; no schedule";
  const label = rebuilding ? "A catalog rebuild runs now" : "Rebuild the catalog now";
  $("card-health").innerHTML =
    `<span class="dot${status.healthy ? "" : " off"}"></span>${status.healthy ? "Healthy" : "Down"}`;
  const affinity = status.affinity;
  const affinityLine = affinity?.mode && affinity.mode !== "none"
    ? line("Affinity", affinity.mode === "race"
        ? `race &middot; ${affinity.count} &middot; ${Math.round(affinity.chance * 100)}% &middot; ${affinity.slow}s`
        : affinity.mode)
    : "";
  $("card-rows").innerHTML = line("Sessions", status.sessions) + affinityLine
    + `<button class="line rebuild" type="button" title="${label}" ${rebuilding ? "disabled" : ""}>
      <span>Catalog</span><span><b>${esc(last)}</b>${following}</span></button>`;
}

// The last state answer. It stays in place when a later call fails, and the chip says so.
let stateKnown = null;

function stateChip() {
  if (stateKnown === null) return '<span class="chip" id="state-loading">Loading the state</span>';
  if (stateKnown) return "";
  return '<span class="chip bad" id="state-unknown" title="The last state call failed. The page shows the last good answer.">State unknown</span>';
}

// The Overview card holds the health, the sessions and the catalog; the header keeps the state.
function renderChips() {
  document.querySelectorAll("[data-status]").forEach((host) => { host.innerHTML = stateChip(); });
}

function setStateKnown(value) {
  stateKnown = value;
  const app = $("app");
  if (app?.dataset) app.dataset.loading = value === null ? "1" : "";
  renderChips();
}

function renderStatus(status) {
  state.status = status;
  setStateKnown(true);
  renderStatusCard(status);
  $("version").textContent = status.version;
  markNavSteps();
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
  draw("pools", pools.map((pool) => {
    const { tier, mode } = poolFilter(pool);
    const health = poolHealth(pool.members);
    const bar = health === null ? "" : `<div class="health" title="Mean weight. A model in a cooldown counts as 0.">
      ${weightBar(health)}<span class="num">${health.toFixed(2)}</span></div>`;
    const context = pool.context
      ? ` <span class="ctx" title="The largest context of a pool model, ${pool.context.toLocaleString()} tokens">${tokens(pool.context)}</span>` : "";
    return `<a class="card pool" data-tier="${tier}" data-mode="${esc(mode)}" title="${esc(pool.shown)}: ${esc(POOL_NOTES[pool.name] || "")}">
      <h3>${esc(pool.shown.replace("daedalus/", ""))}${context}</h3>${bar}<div class="sub">${count(pool.members.length, "model")}</div></a>`;
  }).join(""));
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

// The meaning of each status, for the hover of the status cell.
const STATUS_NOTES = { 200: "OK", 429: "Too many requests", 500: "Upstream error", err: "The attempt failed" };

// The status on the page: 499, the nginx code of a request that the client closed.
function statusCell(r) {
  if (r.cancelled) return '<span title="Cancelled">499</span>';
  const note = STATUS_NOTES[r.status];
  return note ? `<span title="${esc(note)}">${esc(r.status)}</span>` : esc(r.status);
}

function statusClass(r) {
  if (r.cancelled) return "muted";
  if (r.status === "err") return "s5";
  return `s${String(r.status)[0]}`;
}

function chainText(r) {
  const head = [stamp(r.at), r.model, statusText(r), r.effort && `effort=${r.effort}`, r.pool && `pool=${r.pool}`,
    r.routed && `from=${r.routed}`,
    `fallbacks=${r.fallbacks ?? 0}`, r.retry && `retry=${r.retry}`, r.loop && `loop=${r.loop}`].filter(Boolean).join(" ");
  const steps = (r.attempts || []).map((a, i) =>
    `${i + 1}. ${a.model} ${a.result} ${seconds(a.seconds)}`.trim() + ("effort" in a ? ` effort=${sentText(a, r.effort)}` : "")
      + (a.race === "won" ? " won race" : "")
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
      ${a.race === "won" ? '<span class="from">won race</span>' : ""}
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return steps ? `<ol>${steps}</ol>` : '<p class="muted">No attempt data for this request.</p>';
}

function chainRows(r) {
  const many = fallbackCount(r);
  return `<tr role="row" class="chain"><td role="cell" colspan="13"><div class="chain-body">
    <div class="chain-head"><span class="muted">Fallback chain${many ? ` · ${many}` : ""}</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${raceNote(r)}${chainSteps(r)}
  </div></td></tr>`;
}

// The routing codes of the model cell: a transition, a fallback tier, and a stopped loop.
// Why the racers did or did not start: the code wears the same 3 letter shape as the routing
// codes, and the title carries the full note. It shows while the race is on.
const RACE_NOTES = {
  off: "No race: the affinity mode is not race",
  pool: "No race: the request is not a pool or auto route",
  stream: "No race: the request is not a stream",
  single: "No race: the pool holds 1 model",
  fast: "No race: the first model answered before the slow seconds",
  slow: "Raced the pin: the racers started after the slow seconds",
  drawn: "Raced the pin: the draw started the racers with the first model",
};
const RACE_CODES = {
  off: "race off", pool: "not a pool", stream: "not a stream", single: "one model",
  fast: "fast pin", slow: "slow pin", drawn: "on draw",
};
const raceNote = (r) => (state.status?.affinity?.enabled && RACE_CODES[r.race]
  ? `<p class="chain-race" title="${esc(RACE_NOTES[r.race])}">${esc(RACE_CODES[r.race])}</p>` : "");

function routingCodes(r) {
  const from = r.routed ? ` <span class="from" title="Tier ${esc(poolTier(r.routed))} of the previous model">fr${esc(poolTier(r.routed))}</span>` : "";
  const loop = r.loop ? ` <span class="from" title="${esc(r.loop)} equal tool calls stopped the chain">tl${esc(r.loop)}</span>` : "";
  return transitionCell(r.transition) + from + loop;
}

function mobileFallbackChain(r, tail = "") {
  return `<details class="mobile-fallback-chain">
    <summary>View fallback chain${tail}</summary>
    <div class="mobile-chain-head"><span class="muted">Fallback chain</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${raceNote(r)}${chainSteps(r)}
  </details>`;
}

// The fallback count of a request: the table hides the column, so the details of the chain carry
// the count, and a live row reads the count alone.
function fallbackCount(r) {
  return r.fallbacks === undefined || r.fallbacks === null
    ? ""
    : `${esc(r.fallbacks)} ${Number(r.fallbacks) === 1 ? "fallback" : "fallbacks"}`;
}

function mobileRequestDetails(r, live = false) {
  const many = fallbackCount(r);
  if (live) return many ? `<span class="mobile-fallback-count">${many}</span>` : "";
  return hasChain(r) ? mobileFallbackChain(r, many ? ` · ${many}` : "") : "";
}

function fallbackCell(r, classes = "", live = false) {
  const details = mobileRequestDetails(r, live);
  return `<td role="cell" class="${classes} fallbacks-cell${details ? "" : " no-details"}">${details}</td>`;
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
const appCell = (r) => `<td role="cell"${r.key ? ` title="API key: ${esc(r.key)}"` : ""}>${mobileLabel("App")}<span class="cell-value">${r.app ? esc(r.app) : dash}</span></td>`;

// A live request with local times, because the server sends ages and not clock times.
function liveRow(r) {
  const now = Date.now();
  const since = now - r.age * 1000;
  const attemptSince = now - r.attempt_age * 1000;
  return { ...r, since, attemptSince, first: r.ttft == null ? null : attemptSince + r.ttft * 1000 };
}

// The Live switch: the paused table holds still, and the button counts the requests that wait.
function renderLiveToggle() {
  const button = $("live-toggle");
  button.setAttribute("aria-pressed", String(state.livePaused));
  button.textContent = state.livePaused
    ? `Paused${state.liveWaiting.size ? ` · ${state.liveWaiting.size} new` : ""}`
    : "Live";
}

// A change while the table is paused. It waits for the resume.
function holdLive(id) {
  if (!state.livePaused) return false;
  state.liveWaiting.add(id);
  renderLiveToggle();
  return true;
}

// The requests in flight, from the dashboard event stream.
function openLive() {
  closeLive();
  const value = session();
  const source = new EventSource(`ui/api/requests/live${value ? `?session=${encodeURIComponent(value)}` : ""}`);
  const put = (event) => {
    const r = JSON.parse(event.data);
    if (holdLive(r.id)) return;
    state.live.set(r.id, liveRow(r));
    renderLive();
    // The table reads the live count: a new row hides the empty row without a refresh.
    renderRequestTable();
  };
  source.addEventListener("live", (event) => {
    const rows = JSON.parse(event.data);
    if (state.livePaused) {
      for (const r of rows) if (!state.live.has(r.id)) holdLive(r.id);
      return;
    }
    state.live = new Map(rows.map((r) => [r.id, liveRow(r)]));
    renderLive();
    renderRequestTable();
  });
  ["start", "update", "first"].forEach((kind) => source.addEventListener(kind, put));
  source.addEventListener("end", (event) => {
    const id = JSON.parse(event.data).id;
    if (holdLive(id)) return;
    state.live.delete(id);
    renderLive();
    renderRequestTable();
    guarded(refreshFast);
  });
  state.source = source;
  renderLiveToggle();
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
  rce: "A racing model took the pin",
};

// A repeat carries the code of the hook, such as rt1. The row shows the code as given.
const REPEAT_CODE = /^rt\d+$/;
const REPEAT_LABEL = "A repeat picked another model";

function transitionCell(t) {
  if (!t || !t.reason) return "";
  const label = REPEAT_CODE.test(t.reason)
    ? REPEAT_LABEL
    : TRANSITION_REASONS[t.reason] || t.reason;
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
// The parallel row of the legend joins it only while the setting is on.
const raceOn = () => Boolean(
  state.settings && (fileValue("affinity", "mode") ?? state.settings.defaults.affinity.mode) === "race",
);

function renderLegend(extra = state.legendExtra) {
  const rows = [
    ...Object.entries(TRANSITION_REASONS).filter(([code]) => code !== "rce" || raceOn()),
    ["frX", "the tier of the previous model"],
    ["tlN", "N equal tool calls stopped the chain"],
    ...extra,
  ];
  $("legend-body").innerHTML = rows.map(([code, label]) =>
    `<span><code>${esc(code)}</code>${esc(label)}</span>`).join("");
}

// The legend rows of the hook files. They show below the base rows, and a page with no hook keeps
// the base rows.
async function loadLegend() {
  try {
    const body = await call("hooks");
    const extra = Array.isArray(body?.legend) ? body.legend : [];
    state.legendExtra = extra;
    if (extra.length) renderLegend();
  } catch {
    // The base rows stand.
  }
}

function renderLive() {
  if (state.livePaused) return;
  const rows = [...state.live.values()].filter((r) => requestMatches(r, { live: true }))
    .sort((a, b) => b.since - a.since);
  // The whole group is redrawn on each event, so only a row that the page has not drawn yet
  // carries the glide: a redraw of a row already on the page stays still.
  $("live").innerHTML = rows.map((r) => `
    <tr role="row" class="live-row${state.liveSeen.has(r.id) ? "" : " arrive"}" data-live="${r.id}">
      ${cell("Time", `<span class="pulse"></span><span class="live-ago">${relative(r.since / 1000)}</span>`, "num muted",
        ` title="${esc(stamp(r.since / 1000))}"`)}
      ${appCell(r)}
      ${cell("Session", esc(r.session || "-"), "hide-sm num mono")}
      ${nameCell(r.model || r.path, modelName(r.model || r.path, poolOf(r)) + routingCodes(r))}
      ${nameCell(r.via || r.trying || "", r.via ? modelName(r.via) : `<span class="muted">${r.trying ? `trying ${modelName(r.trying)}` : "waiting"}</span>`, "Served by")}
      ${cell("Effort", effortCell(r), "hide-sm")}
      ${cell("Status", "live", "status muted")}
      ${cell("Input", "-", "hide-sm num muted")}
      ${cell("Output", "-", "hide-sm num muted")}
      <td role="cell" class="hide-sm num">${mobileLabel("TTFT")}<span class="cell-value"><span data-clock="ttft"></span></span></td>
      <td role="cell" class="hide-sm num">${mobileLabel("Stream")}<span class="cell-value"><span data-clock="stream"></span></span></td>
      ${fallbackCell(r, "hide-sm num muted", true)}
    </tr>`).join("");
  state.liveSeen = new Set(rows.map((r) => r.id));
  tickLive();
}

// The live clocks: TTFT until the first token. The stream clock starts at the first token,
// and it counts the whole request when the answer does not stream.
function tickLive() {
  if (state.livePaused) return;
  const now = Date.now();
  for (const row of $("live").children) {
    const r = state.live.get(Number(row.dataset.live));
    if (!r) continue;
    const ttft = ((r.first ?? now) - r.attemptSince) / 1000;
    const stream = r.first == null ? null : (r.stream ? now - r.first : now - r.since) / 1000;
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

// The Requests toolbar: a text match over the model, the client and the session, a status class and
// a time range. The kept rows are the window; a filter narrows the view of it.
function requestMatches(r, { live = false } = {}) {
  if (state.requestSearch) {
    const hay = `${r.model || ""} ${r.via || ""} ${r.app || ""} ${r.session || ""} ${r.status || ""}`;
    if (!hay.toLowerCase().includes(state.requestSearch)) return false;
  }
  if (live) return true; // a running request has no final status and no age
  const cls = statusClass(r);
  if (state.requestStatus === "ok" && cls !== "s2") return false;
  if (state.requestStatus === "bad" && cls !== "s4" && cls !== "s5") return false;
  if (state.requestHours) {
    const at = Number(r.at) || 0;
    if (!at || Date.now() / 1000 - at > state.requestHours * 3600) return false;
  }
  return true;
}

// The kept rows the toolbar shows, and the count beside the controls. The count and the empty row
// read both lists: the requests in flight at the top, and the kept ones below.
function renderRequestTable() {
  const rows = state.requests.filter((r) => requestMatches(r));
  const live = [...state.live.values()].filter((r) => requestMatches(r, { live: true })).length;
  const total = state.requests.length + live;
  renderRequests(rows, total && !rows.length && !live
    ? "No requests match the filter, or the kept window holds no such request"
    : "No requests", live);
  // The button keeps its seat in the bar, so showing it never moves the count hint.
  $("request-clear").classList.toggle("off",
    !state.requestSearch && state.requestStatus === "all" && !state.requestHours);
  $("more-requests").hidden = state.requestFetched < state.requestLimit
    || state.requestLimit >= REQUESTS_KEPT;
  const shown = rows.length + live;
  const head = shown === total ? count(total, "request") : `${shown} of ${count(total, "request")}`;
  const hint = $("request-count");
  hint.textContent = total ? `${head}${live ? ` · ${live} in flight` : ""}` : "";
  // The hint pulses when the stream itself changes, not when a filter rewrites the text.
  if (live !== state.liveCount) {
    state.liveCount = live;
    hint.classList.remove("bump");
    void hint.offsetWidth;
    hint.classList.add("bump");
  }
}

function renderRequests(rows, empty = "No requests", live = 0) {
  const flatRows = splitRequests(rows);
  // The live count rides in the key: a live row hides the empty row, so it changes the markup.
  const text = JSON.stringify([flatRows, live ? 1 : 0]);
  const selected = getSelection();
  if (text === shownRequests) return;
  if (!selected.isCollapsed && $("requests").contains(selected.anchorNode)) return;
  shownRequests = text;
  $("requests").innerHTML = flatRows.length ? flatRows.map((r) => {
    const chain = hasChain(r);
    const chainOpen = chain && opened.has(String(r.at));
    return `
    <tr role="row" class="request${chain ? " has-chain" : ""}${chainOpen ? " open" : ""}" data-at="${r.at}"${chain ? ' title="Show the fallback chain"' : ""}>
      ${cell("Time", `<span class="caret${chain ? "" : " none"}"></span>${relative(r.at)}`, "num muted", ` title="${esc(stamp(r.at))}"`)}
      ${appCell(r)}
      ${cell("Session", esc(r.session || "-"), "hide-sm num mono")}
      ${nameCell(r.model || "-", modelName(r.model || "-", poolOf(r)) + routingCodes(r))}
      ${nameCell(r.via || "", r.via ? modelName(r.via) : '<span class="muted">none</span>', "Served by")}
      ${cell("Effort", effortCell(r), "hide-sm")}
      ${cell("Status", statusCell(r), `status ${statusClass(r)}`)}
      ${tokenCell(r.tokens?.input, r.tokens?.estimate ? "~" : "", "Input")}
      ${tokenCell(r.tokens?.output, "", "Output")}
      ${cell("TTFT", esc(r.ttft || "-"), "hide-sm num")}
      ${cell("Stream", streamCell(r), "hide-sm num")}
      ${fallbackCell(r, "hide-sm num")}
    </tr>${chainOpen ? chainRows(r) : ""}`;
  }).join("") : live ? "" : `<tr role="row"><td role="cell" colspan="13" class="empty">${empty}</td></tr>`;
  firstDraw($("requests"));
}

// The label of each catalog mode.
const MODES = {
  chat: "Chat", embedding: "Embedding", audio_transcription: "Transcription",
  audio_speech: "Speech", image_generation: "Image", video_generation: "Video",
  decisions: "Decisions", rerank: "Rerank",
};
// The label of each media flag chip.
const FLAGS = { vision: "Image in", pdf_input: "PDF in", audio_input: "Audio in", audio_output: "Audio out" };
// The mode already says what these media flags say: Speech is audio out, Transcription is audio in.
const REDUNDANT = { audio_speech: "audio_output", audio_transcription: "audio_input" };
// The Type cell: a chip for the mode, then a chip for each media flag the mode does not carry.
function typeChips(m) {
  return `<span class="chip flag mode">${esc(MODES[m.mode] || m.mode)}</span>`
    + m.flags.filter((f) => f !== REDUNDANT[m.mode])
      .map((f) => `<span class="chip flag">${esc(FLAGS[f] || f)}</span>`).join("");
}

// The phone folds the Tier, Tools and Cooldown columns into a row of chips under the name.
// The tools chip holds a command terminal and the reasoning chip a brain, in the chip color.
const CHIP_ICONS = {
  tools: '<svg viewBox="0 0 122.88 103.53" width="12" height="10" aria-hidden="true" fill="currentColor"><path fill-rule="evenodd" clip-rule="evenodd" d="M5.47,0h111.93c3.01,0,5.47,2.46,5.47,5.47v92.58c0,3.01-2.46,5.47-5.47,5.47H5.47 c-3.01,0-5.47-2.46-5.47-5.47V5.47C0,2.46,2.46,0,5.47,0L5.47,0z M31.84,38.55l17.79,18.42l2.14,2.13l-2.12,2.16L31.68,80.31 l-5.07-5l15.85-16.15L26.81,43.6L31.84,38.55L31.84,38.55z M94.1,79.41H54.69v-6.84H94.1V79.41L94.1,79.41z M38.19,9.83 c3.19,0,5.78,2.59,5.78,5.78s-2.59,5.78-5.78,5.78c-3.19,0-5.78-2.59-5.78-5.78S35,9.83,38.19,9.83L38.19,9.83z M18.95,9.83 c3.19,0,5.78,2.59,5.78,5.78s-2.59,5.78-5.78,5.78c-3.19,0-5.78-2.59-5.78-5.78S15.75,9.83,18.95,9.83L18.95,9.83z M7.49,5.41 h107.91c1.15,0,2.09,0.94,2.09,2.09v18.32H5.4V7.5C5.4,6.35,6.34,5.41,7.49,5.41L7.49,5.41z"/></svg>',
  cool: '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2v20M4 7l16 10M20 7L4 17"/></svg>',
  brain: '<svg viewBox="0 0 122.88 115.23" width="12" height="11" aria-hidden="true" fill="currentColor"><path d="M60.89,8.37c2.99-4.67,6.96-7.16,11.12-8.03c3.95-0.82,8-0.11,11.49,1.65c3.44,1.74,6.39,4.52,8.2,7.88 c0.68,1.26,1.21,2.6,1.55,3.98c2.03-0.04,4.1,0.31,6.11,0.99c3.82,1.29,7.49,3.82,10.33,7.17c2.85,3.37,4.88,7.62,5.4,12.33 c0.24,2.17,0.15,4.43-0.32,6.74c2.09,1.87,3.83,3.98,5.14,6.23c2.03,3.47,3.09,7.32,2.98,11.23c-0.11,3.94-1.41,7.88-4.09,11.51 c-1.69,2.29-3.92,4.44-6.73,6.37c0.26,6.02-1.52,11.42-4.4,15.6c-3.26,4.72-7.98,7.92-12.88,8.78c-1.08,4.19-3.86,7.88-7.45,10.45 c-4.54,3.25-10.46,4.78-15.87,3.3c-3.99-1.09-7.62-3.73-10.11-8.38c-3.03,5.11-7.19,7.81-11.55,8.72 c-5.23,1.09-10.64-0.52-14.72-3.7c-3.26-2.54-5.71-6.11-6.59-10.16c-1.71-0.07-3.44-0.41-5.13-0.98c-3.73-1.26-7.32-3.7-10.13-6.95 c-2.82-3.25-4.87-7.34-5.51-11.89c-0.33-2.37-0.28-4.85,0.24-7.4c-1.83-1.66-3.4-3.53-4.65-5.56C1.14,64.78-0.05,60.86,0,56.83 c0.05-4.06,1.34-8.18,4.14-12c1.63-2.22,3.78-4.34,6.5-6.28c-0.03-0.74-0.03-1.48-0.01-2.21c0.22-5.93,2.4-11.14,5.59-15.03 c3.3-4.03,7.72-6.67,12.26-7.31l0.02,0c0.16-0.67,0.35-1.32,0.59-1.96c1.46-3.97,4.47-7.34,8.16-9.49 c3.71-2.16,8.17-3.11,12.51-2.21C53.93,1.2,57.9,3.7,60.89,8.37L60.89,8.37z M93.02,21.89c-0.66,2.18-1.84,4.37-3.64,6.5 c-1.37,1.63-3.8,1.84-5.43,0.47c-1.63-1.37-1.84-3.8-0.47-5.43c2.9-3.45,2.93-7.07,1.4-9.9c-1.06-1.96-2.81-3.6-4.87-4.64 c-2.01-1.01-4.28-1.44-6.44-0.99c-2.88,0.6-5.67,2.83-7.6,7.36c0.03,0.2,0.05,0.41,0.05,0.62v79.69c0.06,0.19,0.11,0.4,0.15,0.6 c1.11,6.75,4.03,10,7.31,10.9c3.03,0.83,6.52-0.14,9.3-2.14c2.73-1.96,4.68-4.82,4.7-7.83c0.03-3.43-2.49-7.31-9.23-10.75 c-1.91-0.97-2.66-3.31-1.69-5.22c0.97-1.91,3.31-2.66,5.22-1.69c7.44,3.8,11.36,8.46,12.8,13.17c2.39-0.78,4.71-2.6,6.47-5.14 c2.21-3.19,3.46-7.48,2.86-12.3c-0.35-1.65,0.37-3.41,1.9-4.3c2.92-1.71,5.07-3.6,6.53-5.59c1.66-2.24,2.46-4.62,2.53-6.96 c0.07-2.36-0.61-4.74-1.91-6.96c-1.19-2.03-2.87-3.9-4.98-5.48c-1.47-0.98-2.17-2.87-1.57-4.62c0.71-2.1,0.9-4.14,0.69-6.06 c-0.33-3.02-1.67-5.77-3.54-8c-1.89-2.24-4.31-3.92-6.78-4.75C95.5,22.01,94.22,21.81,93.02,21.89L93.02,21.89z M28.85,93.03 c1.54-4.84,5.6-9.67,13.26-13.58c1.91-0.97,4.24-0.22,5.22,1.69c0.97,1.91,0.22,4.24-1.69,5.22c-7.1,3.63-9.77,7.79-9.77,11.42 c0,2.83,1.61,5.48,3.96,7.31c2.38,1.85,5.46,2.81,8.36,2.21c3.62-0.75,7.11-4.1,8.92-11.39V19.22C55.3,11.98,51.81,8.65,48.2,7.9 c-2.38-0.5-4.91,0.07-7.07,1.33c-2.18,1.27-3.94,3.22-4.77,5.47c-1.11,3-0.45,6.69,3.3,10.16c1.56,1.45,1.66,3.88,0.21,5.44 c-1.45,1.56-3.88,1.66-5.44,0.21c-2.92-2.7-4.72-5.57-5.62-8.43c-2.3,0.55-4.57,2.07-6.4,4.3c-2.15,2.62-3.62,6.16-3.77,10.23 c-0.04,1.08,0.02,2.22,0.18,3.39l-0.01,0c0.21,1.52-0.47,3.09-1.86,3.95c-2.81,1.73-4.89,3.63-6.34,5.61 c-1.76,2.4-2.57,4.92-2.6,7.35c-0.03,2.47,0.73,4.92,2.09,7.13c1.15,1.87,2.73,3.57,4.65,5c1.44,0.99,2.12,2.85,1.53,4.59 c-0.76,2.25-0.93,4.44-0.64,6.47c0.41,2.93,1.77,5.6,3.63,7.75c1.87,2.16,4.23,3.77,6.65,4.59C26.9,92.8,27.89,92.99,28.85,93.03 L28.85,93.03z M29.73,38.54c1.52-1.49,3.96-1.47,5.46,0.05c1.49,1.52,1.47,3.96-0.05,5.46c-3.31,3.26-5.04,7.46-5.22,11.76 c-0.18,4.43,1.26,8.98,4.28,12.73c1.34,1.66,1.07,4.09-0.59,5.43c-1.66,1.34-4.09,1.07-5.43-0.59c-4.23-5.24-6.24-11.62-5.98-17.87 C22.47,49.29,24.96,43.23,29.73,38.54L29.73,38.54z M84.51,42.25c-1.73-1.25-2.11-3.67-0.86-5.4c1.25-1.73,3.67-2.12,5.4-0.86 c0.77,0.56,1.5,1.15,2.18,1.77c5.03,4.54,7.78,10.53,8.28,16.73c0.5,6.14-1.21,12.48-5.08,17.8c-0.57,0.78-1.18,1.54-1.84,2.27 c-1.43,1.59-3.87,1.72-5.46,0.29c-1.59-1.43-1.72-3.87-0.29-5.46c0.48-0.53,0.92-1.08,1.33-1.64c2.76-3.8,3.98-8.3,3.63-12.66 c-0.35-4.29-2.25-8.44-5.74-11.58C85.57,43.07,85.06,42.65,84.51,42.25L84.51,42.25z"/></svg>',
};
function phoneChips(m) {
  // The desktop columns a phone drops ride as labelled chips, so the card row stays full.
  const tier = m.tier ? `<span class="chip flag" title="Tier">Tier ${esc(tierLetter(m.tier))}</span>` : "";
  const order = m.order ? `<span class="chip flag" title="Order">Order ${m.order}</span>` : "";
  const context = m.max_input_tokens
    ? `<span class="chip flag" title="Context">Context <span class="num">${tokens(m.max_input_tokens)}</span></span>`
    : "";
  // A state the model does not support shows no chip at all.
  const tools = m.mode === "chat" && m.tools
    ? `<span class="chip flag" title="Tools">${CHIP_ICONS.tools}</span>` : "";
  const reasoning = m.mode === "chat" && m.reasoning
    ? `<span class="chip flag" title="Reasoning">${CHIP_ICONS.brain}${m.effort ? `<span class="num">${esc(m.effort)}</span>` : ""}</span>`
    : "";
  const cool = m.cooldown && m.cooldown > Date.now() / 1000
    ? `<span class="chip flag" title="Cooldown">${CHIP_ICONS.cool}<span class="cool" data-until="${m.cooldown}">${timeLeft(m.cooldown)}</span></span>`
    : "";
  return tier + order + context + tools + reasoning + cool;
}

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
  const small = $("sort-small");
  if ([...small.options].some((option) => option.value === state.sort.key)) {
    small.value = state.sort.key;
  }
}

const dash = '<span class="muted">-</span>';

// A redraw that keeps the DOM when the markup did not change. The browser find marks and the text
// selection of the page survive, because an identical table is not built again. A live selection
// holds the redraw of its own host back, as the Requests table does.
const DRAWN = new Map();
// A host that already arrived: the first data of a card or a table fades in, and a later redraw
// of a host that holds data stays still, so a refresh or a live tick never blinks the panel.
const DRAWN_SHOWN = new Set();
function firstDraw(host) {
  if (!host || DRAWN_SHOWN.has(host)) return;
  DRAWN_SHOWN.add(host);
  host.classList.add("drawn");
}
// Restart the arrival on 1 host: a filter pick or a section pick moves its own block, and the
// page around it holds still. A host that never arrived keeps its own draw path.
function replay(host) {
  if (!host) return;
  host.classList.remove("drawn");
  void host.offsetWidth;
  host.classList.add("drawn");
  DRAWN_SHOWN.add(host);
}
function draw(id, markup) {
  const host = $(id);
  if (DRAWN.get(host) === markup) return;
  const selected = getSelection();
  if (!selected.isCollapsed && host.contains(selected.anchorNode)) return;
  DRAWN.set(host, markup);
  host.innerHTML = markup;
  firstDraw(host);
}

// A filter pick of the Models tab moves the rows only: the table arrives again, and the page holds
// still, so a pool card, a select or the search box never moves the whole view.
function pickRows() {
  renderModels();
  replay($("models"));
}

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
  draw("models", rows.length ? rows.map((m) => `
    <tr>
      ${nameCell(m.id, modelName(m.id), "", `<span class="types phone-types">${typeChips(m)}</span><span class="phone-chips">${phoneChips(m)}</span>`)}
      <td class="hide-sm"><div class="types">${typeChips(m)}</div></td>
      <td class="mid hide-sm">${mobileLabel("Tier")}<span class="cell-value">${m.tier ? `<span class="tier" title="${esc(m.tier)}">${esc(tierLetter(m.tier))}</span>` : dash}</span></td>
      <td class="hide-sm mid num${m.order > 1 ? "" : " muted"}">${m.order ?? dash}</td>
      <td class="hide-sm num muted"><span${m.max_input_tokens ? ` title="${m.max_input_tokens.toLocaleString()} tokens"` : ""}>${tokens(m.max_input_tokens)}</span></td>
      <td class="mid hide-sm">${mobileLabel("Tools")}<span class="cell-value">${m.mode === "chat" ? yesNo(m.tools) : dash}</span></td>
      <td class="hide-sm mid">${m.mode === "chat" ? reasoningCell(m) : dash}</td>
      <td class="num hide-sm">${mobileLabel("Cooldown")}<span class="cell-value">${coolCells(m)}</span></td>
      <td>${mobileLabel("Weight")}<span class="cell-value">${m.weight == null ? dash
        : `<div class="weight">${weightBar(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div>`}</span></td>
    </tr>`).join("") : `<tr><td colspan="9" class="empty">${empty}</td></tr>`);
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
  return left ? `<span class="cool" data-until="${until}" title="Until ${esc(stamp(until))}">${left}</span>` : dash;
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

// The saved values of the provider keys. A key field shows the state: env:NAME or db:NAME.
async function refreshEnv() {
  state.env = await call("env");
  if (!document.querySelector('section[data-page="providers"]').hidden) renderForm();
}

function renderKeys(rows) {
  draw("keys", rows.length ? rows.map((k) => `
    <tr>
      <td class="name" title="${esc(k.name)}">${esc(k.name)}</td>
      <td class="num muted mono"><span${k.start ? ` title="Only the start of a saved key is kept"` : ""}>${k.start ? esc(k.start) + "&hellip;" : "-"}</span></td>
      <td class="hide-sm muted">${stamp(k.created)}</td>
      <td class="muted">${mobileLabel("Last used")}<span class="cell-value">${k.used ? stamp(k.used) : "never"}</span></td>
      <td class="end"><button type="button" class="ghost danger" data-key="${esc(k.name)}">Delete</button></td>
    </tr>`).join("") : '<tr><td colspan="5" class="empty">No API keys. The master key opens /v1.</td></tr>');
}

async function refreshKeys() {
  state.keys = await call("keys");
  renderKeys(state.keys);
  renderOverview();
}

// The block keys that the form edits. The YAML view edits the other keys.
const FORM_KEYS = ["api_key", "account_id", "client_keys", "api_base", "api_type", "discovery_url", "discovery_match", "exclude", "tier", "models", "hooks"];
// The pattern fields take the same 4 shapes, so 1 hint serves them all.
const PATTERN_HINT = "Exact: the whole name. Glob: a * or a ?. Regex: a leading ^. A leading ! refuses.";
// The keys that a model override sets but the provider level does not.
const MODEL_ONLY = ["pool", "timeout"];
// The keys that the provider level sets but a model override does not.
const PROVIDER_ONLY = ["hourly_requests"];
const TIERS = ["TIER-A", "TIER-B", "TIER-C", "TIER-D"];
// The width of 1 column of provider cards.

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
  const editor = $("editor");
  const text = index === state.file && editor ? editor.value : state.files[index].text;
  return text !== state.saved[index];
}

const dirty = () => state.files.length > 0 && fileDirty(state.file);

function renderFiles() {
  const current = state.files[state.file];
  const chips = state.files.map((file, index) => {
    const mark = fileDirty(index) ? " &bull;" : "";
    // The provider keys of a shadowed file do nothing, so the tab says which file wins.
    const shadow = file.shadow ? ` (provider keys: ${esc(file.shadow)} wins)` : "";
    return `<button type="button" class="tab${index === state.file ? " on" : ""}"
      data-file="${index}" title="${esc(file.path)}${shadow}">${esc(fileName(file.path))}${mark}</button>`;
  }).join(" ");
  // The page actions ride in the file row, as 1 more chip. The main provider file stays:
  // only a {provider}.yml file can go.
  const drop = current?.main === false
    ? '<button type="button" class="tab danger" id="drop-provider">Delete file</button>' : "";
  $("files").innerHTML = `${chips} <button type="button" class="tab" id="new-provider">+ New provider</button>${drop ? ` ${drop}` : ""}`;
  // The card Save writes the text. The form rows write themselves, on the close.
  const yamlSave = $("yaml-save");
  if (yamlSave) yamlSave.disabled = state.view !== "yaml" || !dirty();
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
  const editor = $("editor");
  if (index === state.file && editor) editor.value = fresh.text;
}

// A file with YAML that is not valid opens in the YAML view, with the error line.
function showFileError(index) {
  const error = state.files[index]?.error;
  if (error) showWrite("providers", `Not valid YAML: ${error}`, true);
  else clearWrite("providers");
  if (error) state.view = "yaml";
}

function openFile(index) {
  const editor = $("editor");
  if (state.files.length && editor) state.files[state.file].text = editor.value;
  state.file = index;
  showFileError(index);
  // The pick of the other file would ride over into this one, and the pane would show the
  // content of the file under a section of the other. The new file opens its first section.
  const section = sectionState("provider-form");
  section.key = "";
  section.open = false;
  renderFiles();
  renderForm();
  // The picked file arrives its form, so a file change reads on the page.
  replay($("provider-form"));
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

// The chip list of a Settings field. The edited values live in the page state, so a
// re-render of 1 list never touches the other fields.
function listValue(group, key) {
  state.settings.lists ||= {};
  const found = state.settings.lists[`${group}.${key}`] ?? setting(group, key);
  // An older file holds 1 path where the form holds a list.
  return Array.isArray(found) ? found : found ? [found] : [];
}

function setListValue(group, key, values) {
  state.settings.lists ||= {};
  state.settings.lists[`${group}.${key}`] = values;
}
const settingPill = (group, key, value, index) => `<span class="pill">${esc(value)}<button type="button" title="Delete"
  data-setting-drop='${esc(JSON.stringify([group, key, index]))}'>&times;</button></span>`;
const settingAdder = (group, key) => `<button type="button" class="add"
  data-setting-add='${esc(JSON.stringify([group, key]))}'>+ Add</button>`;

// The name of a hook file as a chip shows it, without the folder of the config.
const hookName = (name) => esc(name.replace(/^hooks\//, ""));

// The installed hooks whose scope fits a card: a global hook, then 1 that names the provider or the
// model of the card. `kind` is "provider" or "model", and `name` is the provider or the model id.
function hookFit(row, kind, name) {
  const scope = row.scope || "global";
  const targets = row.targets || [];
  if (scope === "global") return true;
  if (scope === "provider") return targets.includes(kind === "provider" ? name : String(name).split("/")[0]);
  if (scope === "model") return kind === "model" && targets.includes(name);
  return false;
}

function hookChoices(kind, name) {
  return (state.settings?.hook_rows ?? [])
    .filter((row) => !row.problem && hookFit(row, kind, name))
    .map((row) => row.path);
}

// The form of a provider or a model, where each hook row picks a point and 1 file that fits the
// card. The rows write through the path of the form, and the same chip flow as the Settings page.
const HOOK_DEFAULT = "on-upstream";
let openFormHook = null;

const hookFormRow = (entry, path, choices) => {
  const list = entry && typeof entry === "object" ? entry : {};
  const point = Object.keys(list)[0] ?? HOOK_DEFAULT;
  const value = String(list[point] ?? "");
  const at = JSON.stringify(path);
  const open = openFormHook === at;
  const menu = open ? `<div class="menu" role="listbox" aria-label="Hook file">${[""].concat(choices)
    .map((name) => `<button type="button" role="option" aria-selected="${name === value}"
      data-form-hook-choice='${esc(JSON.stringify([...path, name]))}'
      >${name ? hookName(name) : "No file"}</button>`).join("")}</div>` : "";
  return `<span class="pill hook"><select data-form-hook-point='${esc(at)}' aria-label="Hook point">${
    HOOK_POINTS.map(([key, label]) => `<option value="${key}"${key === point ? " selected" : ""}>${esc(label)}</option>`).join("")
    }</select><button type="button" class="pick" data-form-hook-pick='${esc(at)}'
    aria-haspopup="listbox" aria-expanded="${open}" title="Pick a hook file"
    >${value ? hookName(value) : "No file"}</button><button type="button" title="Delete"
    data-form-hook-drop='${esc(at)}'>&times;</button>${menu}</span>`;
};

// The hook rows of a provider block or of a model override, with the installed hooks that fit.
function hookFormRows(values, path, kind, name) {
  const choices = hookChoices(kind, name);
  const rows = (Array.isArray(values) ? values : [])
    .map((entry, index) => hookFormRow(entry, [...path, index], choices)).join("")
    || '<em class="none">No hook file</em>';
  return `<div class="pills">${rows}<button type="button" class="add"
    data-form-hook-add='${esc(JSON.stringify([path, kind, name]))}'>+ Add file</button></div>`;
}

// The list of the form at a hook path, created when it is missing. The path of a row ends with the
// index, so the parent list arrives from 1 extra step.
const formHookList = (path) => parentOf([...path, 0], []);

// The label of a source: the repo, then the folder and the ref that differ from the usual 1s.
const sourceLabel = (entry) => `${entry.repo || ""}${entry.path ? `/${entry.path}` : ""}${entry.ref && entry.ref !== "main" ? `@${entry.ref}` : ""}`;

// The parts of a source from the typed text: owner/name, and the ref and the folder of a /tree URL.
function sourceParts(value) {
  const tree = String(value).match(/github\.com\/([^/]+\/[^/]+)\/tree\/([^/]+)\/?(.*)$/);
  if (tree) return { repo: tree[1].replace(/\.git$/, ""), ref: tree[2], path: tree[3].replace(/\/$/, "") };
  const plain = String(value).match(/github\.com\/([^/]+)\/([^/?#]+?)(?:\.git)?\/?$/);
  if (plain) return { repo: `${plain[1]}/${plain[2]}`, ref: "main", path: "hooks" };
  return { repo: String(value).trim().replace(/^\/+|\/+$/g, ""), ref: "main", path: "hooks" };
}

const hookVersion = (record) => record?.version || "-";

// The request points of the settings group: the `on-*` keys of its defaults, in point order. The
// card names every file at the points it runs at, so the settings hold no list of its own.
const requestPoints = () => HOOK_POINTS.filter(([key]) => key in (state.settings?.defaults?.hooks ?? {}));

// The point chips of a hook row: 1 chip per point the block of the file names. The file defines
// what it hooks into, so the card shows the points and holds no control over them.
function hookPointsCell(row) {
  const labels = new Map(HOOK_POINTS.map(([key, label]) => [key, label]));
  const chips = (row.points || []).map((point) =>
    `<span class="pill ${row.enabled ? "on" : "off"}">${esc(labels.get(point) || point)}</span>`).join("");
  return `<div class="pills">${chips || '<em class="none">No point</em>'}</div>`;
}

// The note under the sources. The update fills it with the old and the new version of each file.
function hooksNote(text) {
  const note = $("hooks-note");
  if (note) note.textContent = text;
}

function updateNote(answer) {
  const moved = (answer.hooks || []).filter((hook) => hook.moved);
  if (!moved.length) return "No file moved.";
  return `Moved ${moved.map((hook) => `${hook.name}: ${hookVersion(hook.before)} to ${hookVersion(hook.after)}`).join(", ")}.`;
}

// The manager of the Hooks card: the folder, the sources, the files that load and the update.
function hooksManager() {
  const rows = state.settings.hook_rows || [];
  const sources = listValue("hooks", "sources");
  const disabled = listValue("hooks", "disabled");
  const at = Math.min(state.hooksSource ?? 0, Math.max(0, sources.length - 1));
  const options = sources.length
    ? sources.map((entry, index) => `<option value="${index}"${index === at ? " selected" : ""}>${esc(sourceLabel(entry))}</option>`).join("")
    : '<option value="">No source</option>';
  const list = rows.map((row) => {
    const record = row.record || {};
    const source = record.repo ? `${record.repo}@${(record.commit || "").slice(0, 7)}` : "";
    return `<tr><td role="cell" class="name"><span class="cell-value" title="${esc(row.name)}">${esc(row.name)}</span></td>
      <td role="cell"><span class="cell-value">${esc(hookVersion(row))}</span></td>
      <td role="cell"><span class="cell-value">${esc(row.scope || "global")}${(row.targets || []).length ? `: ${esc(row.targets.join(", "))}` : ""}</span></td>
      <td role="cell" class="points">${hookPointsCell(row)}</td>
      <td role="cell" class="hide-sm"><span class="cell-value" title="${esc(row.problem || source || "bundled")}">${row.problem ? `<span class="bad">${esc(row.problem)}</span>` : esc(source || "bundled")}</span></td>
      <td role="cell"><input type="checkbox" role="switch" class="switch" data-hook-toggle="${esc(row.name)}"
        ${disabled.includes(row.name) ? "" : "checked"} aria-label="Load ${esc(row.name)}"></td></tr>`;
  }).join("") || '<tr><td role="cell" colspan="6"><em class="none">No hook file</em></td></tr>';
  const scan = state.hooksScan;
  const panel = scan ? `<div class="pills" id="hooks-scan">
      <span class="sub">${esc(sourceLabel(scan))} at ${esc((scan.commit || "").slice(0, 7))}</span>${scan.files
    .map((file) => `<label class="pill"><input type="checkbox" data-hook-take="${esc(file.name)}"
      ${scan.take[file.name] ? "checked" : ""}>${esc(file.name)}${file.version ? ` ${esc(file.version)}` : ""}</label>`).join("")}
      <button type="button" class="primary" data-hook-take-all>Take the picked files</button></div>` : "";
  return `<div class="field stack info">${labelSpan("Installed hooks", "The files of the hook folder. A point is a stage of a request, and the block of the file names the points it runs at. The switch leaves a file on disk and out of the run.")}
      <table class="keys"><thead><tr><th>File</th><th>Version</th><th>Scope</th><th>Points</th><th class="hide-sm">Source</th><th>Load</th></tr></thead>
      <tbody>${list}</tbody></table>
      <button type="button" class="ghost" data-hooks-update>Update from the sources</button></div>
    <div class="field stack info">${labelSpan("Folder", "The folder under config that holds the hook files.")}
      <span class="input"><i class="prefix">config/</i><input type="text" id="set-hooks-dir" data-value="Folder"
        readonly spellcheck="false" value="${esc(fileValue("hooks", "dir") ?? "")}"
        placeholder="${esc(state.settings.defaults.hooks.dir)}"></span></div>
    <div class="field stack info">${labelSpan("Sources", "Each source names a GitHub repo, a folder in it and a ref.")}
      <div class="pills"><select id="hook-source" data-hook-source aria-label="Source">${options}</select>
        <button type="button" class="add" data-hook-source-add>+ Add a repo</button>
        <button type="button" class="ghost" data-hook-source-drop title="Delete the source">&times;</button></div>${panel}
      <div class="sub" id="hooks-note" role="status"></div></div>`;
}

async function addHookSource() {
  const typed = await ask("Add a source", "The GitHub repo of the hooks. A /tree URL also names the ref and the folder.",
    "Scan", false, "owner/name or a GitHub URL");
  if (!typed) return;
  const parts = sourceParts($("modal-input").value.trim());
  try {
    const found = await call("hooks/scan", { method: "POST", body: JSON.stringify(parts) });
    const installed = new Map((state.settings.hook_rows || []).map((row) => [row.name, row.version]));
    state.hooksScan = {
      ...found,
      take: Object.fromEntries(found.files.map((file) => [file.name, installed.get(file.name) !== file.version])),
    };
    renderSettings();
    hooksNote(`${found.files.length} file(s) in ${sourceLabel(found)}.`);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to add the source again.");
    showWrite("settings", error.message, true);
  }
}

async function takeHooks() {
  const scan = state.hooksScan;
  if (!scan) return;
  const source = { repo: scan.repo, path: scan.path, ref: scan.ref, auto_update: false };
  const sources = [...listValue("hooks", "sources")];
  if (!sources.some((entry) => entry.repo === source.repo && entry.path === source.path && entry.ref === source.ref)) {
    sources.push(source);
  }
  setListValue("hooks", "sources", sources);
  const take = scan.files.map((file) => file.name).filter((name) => scan.take[name]);
  state.hooksScan = null;
  try {
    await call("settings", { method: "PUT", body: JSON.stringify({ changes: settingsChanges() }) });
    const answer = await call("hooks/update", { method: "POST", body: JSON.stringify({ source, take }) });
    await loadSettings();
    hooksNote(updateNote(answer));
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in and take the files again.");
    showWrite("settings", error.message, true);
  }
}

async function runHooksUpdate() {
  hooksNote("Update");
  try {
    const answer = await call("hooks/update", { method: "POST" });
    await loadSettings();
    hooksNote(updateNote(answer));
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to update again.");
    hooksNote(error.message);
  }
}

function dropHookSource() {
  const sources = [...listValue("hooks", "sources")];
  if (!sources.length) return;
  sources.splice(Math.min(state.hooksSource ?? 0, sources.length - 1), 1);
  setListValue("hooks", "sources", sources);
  state.hooksSource = 0;
  renderSettings();
  renderSettingsSave();
  saveSettings();
}

function toggleHook(name, on) {
  const disabled = new Set(listValue("hooks", "disabled"));
  if (on) disabled.delete(name);
  else disabled.add(name);
  setListValue("hooks", "disabled", [...disabled]);
  // The save re-renders the table after the write. The chips flip in place first, so the color
  // transition runs on the same node instead of a swap that no transition can cross.
  const row = [...document.querySelectorAll('.card[data-section="hooks"] .keys tbody tr')].find(
    (tr) => tr.querySelector("input[data-hook-toggle]")?.dataset.hookToggle === name
  );
  row?.querySelectorAll(".points .pill").forEach((pill) => {
    pill.classList.toggle("on", on);
    pill.classList.toggle("off", !on);
  });
  renderSettingsSave();
  saveSettings();
}

function renderSettingList(group, key) {
  const host = $(`set-${group}-${key}`);
  if (!host) return;
  host.innerHTML = listValue(group, key)
    .map((value, index) => settingPill(group, key, value, index)).join("") + settingAdder(group, key);
}

// The inline input of a Settings chip list. Enter or a click away keeps the value.
function showSettingAdder(button) {
  const [group, key] = JSON.parse(button.dataset.settingAdd);
  const box = document.createElement("span");
  box.className = "adder";
  box.innerHTML = '<input type="text" spellcheck="false" placeholder="word or phrase">';
  button.replaceWith(box);
  const input = box.querySelector("input");
  input.focus();
  const done = (keep) => {
    const text = input.value.trim();
    if (keep && text) {
      const values = [...listValue(group, key)];
      if (!values.includes(text)) values.push(text);
      setListValue(group, key, values);
    }
    renderSettingList(group, key);
    renderSettingsSave();
    if (keep && text) saveSettings();
  };
  box.addEventListener("keydown", (event) => {
    if (event.key === "Enter") done(true);
    if (event.key === "Escape") done(false);
  });
  box.addEventListener("focusout", () => setTimeout(() => {
    if (box.isConnected && !box.contains(document.activeElement)) done(true);
  }));
}

function dropSetting(path) {
  const [group, key, index] = path;
  const values = [...listValue(group, key)];
  values.splice(index, 1);
  setListValue(group, key, values);
  renderSettingList(group, key);
  renderSettingsSave();
  saveSettings();
}

// The label of a field, with its hint behind an info icon on a desktop and under the label on a phone.
let HINT_COUNT = 0;
function labelSpan(label, hint) {
  if (!hint) return `<span><b>${esc(label)}</b></span>`;
  const id = `hint-${++HINT_COUNT}`;
  return `<span><b>${esc(label)}</b><button type="button" class="hint" aria-describedby="${id}"`
    + ` aria-label="Hint">i</button><small id="${id}" role="tooltip">${esc(hint)}</small></span>`;
}

function field(label, hint, body) {
  return `<div class="field stack info">${labelSpan(label, hint)}${body}</div>`;
}

// The picked section of each list, and whether the phone shows that section or the list.
const SECTION_STATE = { settings: { key: "", open: false }, providers: { key: "", open: false } };
const sectionState = (host) => SECTION_STATE[host.id === "settings" ? "settings" : "providers"];

// A section list: every section in a rail, and the pane shows the picked 1.
function sectionList(id, items, active) {
  const buttons = items.map(([key, label]) => `<button type="button" data-section="${esc(key)}"`
    + `${key === active ? ' class="on" aria-current="true"' : ""}>${esc(label)}</button>`).join("");
  return `<nav class="sections" aria-label="Sections">${buttons}</nav>`;
}

// The pane of a section list. The back button names the page it returns to, as iOS does.
function sectionPane(back, cards) {
  return `<div class="section-pane"><button class="ghost back" type="button" data-back><span class="chev" aria-hidden="true">&lsaquo;</span> ${esc(back)}</button>${cards}</div>`;
}

// The last section of a list: the file text, and its own save. It holds the lines that the form
// cannot show, so a broken file still opens.
function yamlCard(editor, save, hint) {
  return `<div class="card yaml-view" data-section="yaml"><h3>YAML</h3><p class="sub">${esc(hint)}</p>`
    + `<textarea id="${editor}" spellcheck="false" aria-label="File text"></textarea>`
    + `<div class="yaml-save"><button class="primary" id="${save}" type="button" disabled>Save</button>`
    + `<span class="hint">Ctrl+S saves and reloads</span></div></div>`;
}

// The phone slide of the view that arrives: the pane from the right, and the list back from the left.
function playSlide(host, open) {
  if (!phoneSection()) return;
  const arriving = host.querySelector(open ? ".section-pane" : ".sections");
  if (!arriving) return;
  arriving.classList.remove("slide");
  void arriving.offsetWidth;
  arriving.classList.add("slide");
}

// The pick of a section: 1 button on, 1 card shown, and the phone leaves the list.
function pickSection(host, key, open = sectionState(host).open) {
  const state_ = sectionState(host);
  const moved = state_.open !== open;
  state_.key = key;
  state_.open = open;
  host.dataset.detail = open ? "1" : "0";
  for (const button of host.querySelectorAll(".sections button")) {
    const on = button.dataset.section === key;
    button.classList.toggle("on", on);
    if (on) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  for (const card of host.querySelectorAll(".section-pane > .card")) {
    card.hidden = card.dataset.section !== key;
  }
  if (!moved) return;
  // The slide plays at once, and the flag holds it for a render that builds the pane again.
  state_.slide = true;
  playSlide(host, open);
}

// A phone turns a pick into its own page: the section rides in the hash, so the back gesture returns.
const phoneSection = () => matchMedia("(max-width: 900px)").matches;

function openSection(host, key) {
  pickSection(host, key, true);
  // The picked card arrives: a section change of Providers or Settings reads on the page.
  replay([...host.querySelectorAll(".section-pane > .card")]
    .find((card) => card.dataset.section === key));
  if (!phoneSection()) return;
  const [path] = location.hash.split("?");
  history.pushState({ section: key }, "", `${path}?section=${encodeURIComponent(key)}`);
}

// The back button leaves the pushed page when it can, and closes the section either way.
function closeSection(host) {
  const key = sectionState(host).key;
  if (history.state?.section === key) return history.back();
  const [path] = location.hash.split("?");
  history.replaceState(null, "", path);
  pickSection(host, key, false);
}

// The section of the shown page, from the hash: a deep link and the phone back gesture read it.
function applySectionHash() {
  const [path, query] = location.hash.split("?");
  const host = path === "#/settings" ? $("settings") : path === "#/providers" ? $("provider-form") : null;
  if (!host) return;
  const key = new URLSearchParams(query ?? "").get("section");
  const known = [...host.querySelectorAll(".sections button")].some((button) => button.dataset.section === key);
  if (key && known) pickSection(host, key, true);
  else pickSection(host, sectionState(host).key, false);
}

// A phone back moves the section before the page builds its form, and that build replaces the
// pane. The slide of the pick replays once on the fresh markup, so the back reads as a move.
function slideAgain() {
  for (const host of [$("settings"), $("provider-form")]) {
    const state_ = sectionState(host);
    if (!state_.slide) continue;
    state_.slide = false;
    playSlide(host, state_.open);
  }
}

// A group of fields that starts closed, so a long card stays short.
function fold(label, hint, body) {
  const id = `hint-${++HINT_COUNT}`;
  return `<details class="fold"><summary>${esc(label)}<button type="button" class="hint" aria-describedby="${id}"`
    + ` aria-label="Hint">i</button><small id="${id}" role="tooltip">${esc(hint)}</small></summary>`
    + `<div class="fold-body">${body}</div></details>`;
}

// The hint of a row near the bottom of the pane would spill past the pane, and the spill
// raises the pane's scrollbar and shifts the text. Such a hint flips above its icon.
function hintFlip(button) {
  const tip = button?.nextElementSibling;
  const pane = button?.closest(".section-pane");
  if (!tip || !pane || getComputedStyle(tip).display === "none") return;
  const over = tip.getBoundingClientRect().bottom > pane.getBoundingClientRect().bottom;
  tip.classList.toggle("up", over);
}
["mouseover", "focusin"].forEach((kind) =>
  document.addEventListener(kind, (event) => hintFlip(event.target.closest?.(".hint")))
);
["mouseout", "focusout"].forEach((kind) =>
  document.addEventListener(kind, (event) => {
    event.target.closest?.(".hint")?.nextElementSibling?.classList.remove("up");
  })
);

function providerCard(name, block) {
  const path = [name];
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
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
    const row = `data-value="${esc(label)}"`;
    if (key === "api_key" || key === "account_id") {
      const info = tokenInfo(block[key]);
      if (info) {
        const row_ = state.env.find((r) => r.name === info.name);
        const ph = placeholderFor(info, row_) || defaults[key] || "";
        const extra = "";
        if (info.type === "env") {
          const shown = info.raw;
          return field(label, hint, `<input class="text" type="text" readonly spellcheck="false" autocomplete="off"
          ${row} data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(shown)}" placeholder="${esc(ph)}">${extra}`);
        }
        return field(label, hint, `<input class="text" type="password" readonly spellcheck="false" autocomplete="off"
        ${row} data-set='${esc(JSON.stringify([...path, key]))}' value="" placeholder="${esc(ph)}">${extra}`);
      }
    }
    const extra = "";
    return field(label, hint, `<input class="text" type="text" readonly spellcheck="false" autocomplete="off"
    ${row} data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(block[key] ?? "")}" placeholder="${esc(defaults[key] ?? "")}">${extra}`);
  };
  const tiers = TIERS.map((tier) => `<div class="tier-row"><span class="tier">${tier.slice(-1)}</span>
    ${listField(tier, block.tier?.[tier], [...path, "tier", tier])}</div>`).join("");
  const models = block.models && typeof block.models === "object" ? block.models : {};
  const overrides = Object.entries(models).map(([pattern, values]) => {
    // The hooks of the override ride in their own rows, so the value pills show the other keys.
    const rest = values && typeof values === "object" && !Array.isArray(values) ? { ...values } : values;
    if (rest && typeof rest === "object" && !Array.isArray(rest)) delete rest.hooks;
    return `<div class="override">
      <input class="text" type="text" spellcheck="false" value="${esc(pattern)}" aria-label="Model pattern" title="${esc(PATTERN_HINT)}"
        data-pattern='${esc(JSON.stringify([...path, "models"]))}' data-key="${esc(pattern)}">
      <div class="pills">${mapPills(rest, [...path, "models", pattern], "override", ": ")}</div>
      ${hookFormRows(values?.hooks, [...path, "models", pattern, "hooks"], "model", pattern)}
      <button type="button" class="ghost" title="Delete the pattern"
        data-drop='${esc(JSON.stringify([...path, "models", pattern]))}'>&times;</button>
    </div>`;
  }).join("");
  const others = Object.keys(block).filter((key) => !FORM_KEYS.includes(key) && key !== "_file");
  const values = others.map((key) => pill(`${key}: ${shown(block[key])}`, path, key)).join("") + adder(path, "column", "+ key");
  const accountField = name === "cloudflare" ? text("account_id", "Account ID", "The Cloudflare account of the URL templates") : "";
  return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
    <p class="sub">env:NAME reads the environment variables. A pasted key goes to the database.</p>
    ${text("api_key", "API key")}
    ${accountField}
    ${field("Client keys", "A provider key for each daedalus key name", `<div class="pills">${mapPills(block.client_keys, [...path, "client_keys"], "match", " = ")}</div>`)}
    ${text("api_base", "API base", "Empty: the default, in gray")}
    ${text("api_type", "API type", "openai or gemini. Empty: the default, in gray")}
    ${text("discovery_url", "Discovery URL", "The model list URL. Empty: the default, in gray")}
    ${field("Discovery match", "The catalog keeps a model when each key matches", `<div class="pills">${mapPills(block.discovery_match, [...path, "discovery_match"], "match", " = ")}</div>`)}
    ${field("Exclude", `Model patterns that never route. ${PATTERN_HINT}`, listField("exclude", block.exclude, [...path, "exclude"]))}
    ${fold("Tiers", `Model patterns for each tier. ${PATTERN_HINT}`, tiers)}
    ${fold("Model overrides", `A pattern and the catalog values that it sets. ${PATTERN_HINT}`, `${overrides}<div class="pills">${adder([...path, "models"], "pattern", "+ Pattern")}</div>`)}
    ${fold("Hooks", "The hook files of this provider, for each point. The installed files that fit the provider come first.", hookFormRows(block.hooks, [name, "hooks"], "provider", name))}
    ${fold("Provider values", "Catalog values for each model of the provider. A model override has priority.", `<div class="pills">${values}</div>`)}
  </div>`;
}

// The section list of the current file: every provider of the file, and the picked card.
function renderForm() {
  const host = $("provider-form");
  const blocks = state.forms[state.file];
  // A render keeps the text of an edit in progress. The saved text shows otherwise.
  const typed = state.view === "yaml" ? $("editor")?.value : undefined;
  if (!state.files.length) return (host.innerHTML = "");
  if (blocks == null) {
    host.innerHTML = sectionList("provider-form", [["yaml", "YAML"]], "yaml")
      + sectionPane("Providers", `<div class="card" data-section="error"><h3>The YAML is not valid</h3>
      <p class="sub">${esc(state.files[state.file].error ?? "")}</p>
      <p class="sub">Fix the text in the YAML section. Then the form opens the file.</p></div>`
      + yamlCard("editor", "yaml-save", `The file text of ${fileName(state.files[state.file].path)}. A block that the form cannot show still opens here.`));
    pickSection(host, "yaml");
    $("editor").value = typed ?? state.files[state.file].text;
    renderFiles();
    return;
  }
  const names = Object.keys(blocks);
  const items = names.map((name) => [name, name]).concat([["yaml", "YAML"]]);
  const picked = sectionState(host).key;
  const active = items.some(([key]) => key === picked) ? picked : items[0][0];
  host.innerHTML = sectionList("provider-form", items, active)
    + sectionPane("Providers", names.map((name) => providerCard(name, blocks[name])).join("")
      + yamlCard("editor", "yaml-save", `The file text of ${fileName(state.files[state.file]?.path ?? "")}. A block that the form cannot show still opens here.`));
  pickSection(host, active);
  $("editor").value = typed ?? state.files[state.file]?.text ?? "";
  renderFiles();
  placeUndo();
  fitSectionPane();
}

// 1 card again after an edit, in the same place, and the pick stays.
function renderCard(name) {
  const card = [...document.querySelectorAll("#provider-form [data-provider]")].find((item) => item.dataset.provider === name);
  if (card) card.outerHTML = providerCard(name, state.forms[state.file][name]);
  pickSection($("provider-form"), sectionState($("provider-form")).key);
  renderFiles();
  placeUndo();
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
  saveForm();
}

function showAdder(button) {
  const path = JSON.parse(button.dataset.add);
  const kind = button.dataset.kind;
  const box = document.createElement("span");
  box.className = "adder";
  const choices = kind === "column" ? [...state.overrideKeys.filter((key) => !MODEL_ONLY.includes(key)), ...PROVIDER_ONLY].sort() : state.overrideKeys;
  const keys = choices.map((key) => `<option>${esc(key)}</option>`).join("");
  const placeholder = { list: "pattern", match: "key = value", pattern: "model pattern", override: "value", column: "value" }[kind];
  const hint = kind === "list" || kind === "pattern" ? PATTERN_HINT : "";
  box.innerHTML = `${kind === "override" || kind === "column" ? `<select aria-label="Key">${keys}</select>` : ""}
    <input type="text" spellcheck="false" placeholder="${placeholder}" title="${esc(hint)}">`;
  button.replaceWith(box);
  const input = box.querySelector("input");
  (box.querySelector("select") || input).focus();
  const done = (keep) => {
    const text = input.value.trim();
    if (keep && text) {
      if (addValue(path, kind, text, box.querySelector("select")?.value)) saveForm();
    } else renderCard(path[0]);
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
  let kept = true;
  clearWrite("providers");
  if (kind === "list") {
    const list = parentOf([...path, 0], []);
    // A repeat is a no-op, so no double value lands in the list.
    if (!list.some((value) => String(value) === text)) list.push(parsed(text));
  } else if (kind === "pattern") {
    const models = parentOf([...path, text], {});
    if (text in models) {
      showWrite("providers", `The pattern ${text} is already there.`, true);
      kept = false;
    } else models[text] = {};
  } else {
    const [name, value] = kind === "match" ? text.split(/\s*=\s*(.*)/s) : [key, text];
    if (!name || value === undefined) {
      showWrite("providers", "Write the value as key = value.", true);
      kept = false;
    } else parentOf([...path, name], {})[name] = parsed(value);
  }
  renderCard(path[0]);
  return kept;
}

function renamePattern(input) {
  state.writeAnchor = anchorOf(input);
  const path = JSON.parse(input.dataset.pattern);
  const models = path.reduce((node, key) => node[key], state.forms[state.file]);
  const before = input.dataset.key;
  const after = input.value.trim();
  if (after === before) return;
  if (!after || after in models) {
    input.value = before;
    showWrite("providers", after ? `The pattern ${after} is already there.` : "A pattern cannot be empty.", true);
    return;
  }
  const renamed = Object.fromEntries(Object.entries(models).map(([key, value]) => [key === before ? after : key, value]));
  parentOf(path, {})[path[path.length - 1]] = renamed;
  renderCard(path[0]);
  saveForm();
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

// The status of a write. A good write says nothing: the Undo icon next to its own row is the
// sign. A failure keeps its line, because an error has to say what went wrong.
const WRITE_UI = {
  providers: { message: $("save-message"), note: $("save-note") },
  settings: { message: $("settings-message"), note: $("settings-note") },
};
let lastWrite = null;

function showWrite(page, text, bad = false) {
  const ui = WRITE_UI[page];
  if (!bad) return clearWrite(page);
  ui.message.className = "message bad";
  ui.message.textContent = text;
  ui.note.hidden = false;
}

function clearWrite(page) {
  const ui = WRITE_UI[page];
  ui.message.textContent = "";
  ui.note.hidden = true;
}

// The text of the file before a write, with the row that caused it. The Undo icon rides beside
// that row, so the way back sits next to the change.
function rememberWrite(page, restore, anchor = null) {
  lastWrite = {
    page, ...restore,
    section: anchor?.closest("[data-section]")?.dataset.section ?? "",
    // The row of the write, so a render of its card can put the icon back in that row.
    key: controlKey(anchor),
  };
  const old = document.querySelector(".undo-step");
  if (old) old.remove();
  if (!anchor) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost undo-step";
  button.title = "Undo this write";
  button.setAttribute("aria-label", "Undo this write");
  button.textContent = "\u21b6";
  button.addEventListener("click", (event) => {
    // The row of a switch is a label, so the click must not reach it.
    event.preventDefault();
    event.stopPropagation();
    undoWrite();
  });
  lastWrite.button = button;
  placeUndo(anchor);
}

// The control of a row: the key of its field, so the row of a fresh render matches the row of
// the write that just landed.
function controlKey(anchor) {
  if (!anchor) return "";
  const set = anchor.matches?.("[data-set]") ? anchor : anchor.querySelector?.("[data-set]");
  if (set?.dataset?.set) return set.dataset.set;
  const named = anchor.matches?.("input[id], select[id]") ? anchor : anchor.querySelector?.("input[id], select[id]");
  return named?.id ?? "";
}

// The icon sits at the top right of the row that changed. A render of that card replaces the
// row, so the icon falls back to the header of the card.
function placeUndo(anchor = null) {
  if (!lastWrite?.button) return;
  // The same row of the fresh render, from the key of the write.
  const again = lastWrite.key
    ? [...document.querySelectorAll("[data-set]")].find((node) => node.dataset.set === lastWrite.key)?.closest(".field")
      ?? document.getElementById(lastWrite.key)?.closest(".field")
    : null;
  const target = (anchor?.isConnected ? anchor : null) ?? again
    ?? document.querySelector(`.section-pane > .card[data-section="${lastWrite.section}"]`);
  if (!target) return;
  const row = target.closest(".field") ?? target;
  // The row reads {undo} {field}: the icon sits at the left of the row it changed.
  row.prepend(lastWrite.button);
}

async function undoWrite() {
  if (!lastWrite) return;
  const write = lastWrite;
  lastWrite = null;
  const old = document.querySelector(".undo-step");
  if (old) old.remove();
  try {
    if (write.settings) {
      await call("settings", { method: "PUT", body: JSON.stringify({ text: write.text }) });
      await loadSettings();
    } else {
      await call("files", { method: "PUT", body: JSON.stringify({ path: write.path, text: write.text }) });
      await takeFile(write.index);
      renderForm();
    }
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in again.");
    showWrite(write.page, error.message, true);
  }
}

async function saveForm() {
  if (!dirty()) return clearWrite("providers");
  const index = state.file;
  const file = state.files[index];
  rememberWrite("providers", { index, path: file.path, text: file.text }, state.writeAnchor);
  const isToken = (v) => typeof v === "string" && (v.startsWith("env:") || v.startsWith("db:"));
  const hadRaw = Object.values(pruned(state.forms[index]) || {}).some((block) =>
    block && typeof block === "object" && (
      (typeof block.api_key === "string" && block.api_key.trim() && !isToken(block.api_key)) ||
      (block.client_keys && typeof block.client_keys === "object" && Object.values(block.client_keys).some((v) => typeof v === "string" && v.trim() && !isToken(v)))
    ));
  try {
    await call("providers", { method: "PUT", body: JSON.stringify({ path: file.path, blocks: pruned(state.forms[index]) }) });
    await takeFile(index);
    clearWrite("providers");
    await refreshEnv();
    renderForm();
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    showWrite("providers", error.message, true);
  }
  renderFiles();
}

async function saveYaml() {
  if (!dirty()) return clearWrite("providers");
  const index = state.file;
  const file = state.files[index];
  const text = $("editor")?.value ?? "";
  rememberWrite("providers", { index, path: file.path, text: file.text }, $("yaml-save"));
  try {
    await call("files", { method: "PUT", body: JSON.stringify({ path: file.path, text }) });
    await takeFile(index);
    clearWrite("providers");
    refresh();
    guarded(refreshEnv);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    showWrite("providers", error.message, true);
  }
  renderFiles();
}

// The shown view is the YAML section of the page when its card is the picked 1.
const yamlView = () => (location.hash.startsWith("#/settings")
  ? state.settingsView === "yaml"
  : state.view === "yaml");

// A pick sets the view of the page: the form rows, or the file text. A leave of the text with
// unsaved lines asks first, then shows the saved file again.
async function pickProviderSection(key) {
  const host = $("provider-form");
  const view = key === "yaml" ? "yaml" : "form";
  if (view !== state.view) {
    if (dirty() && !(await ask("Discard changes", "The unsaved changes of this file go away.", "Discard", true))) return;
    const index = state.file;
    if (state.view === "yaml") state.files[index].text = state.saved[index];
    state.forms[index] = clone(state.files[index].blocks ?? {});
    state.view = view;
    clearWrite("providers");
  }
  renderFiles();
  renderForm();
  // The build comes first: the arrival lands on the form that the page now shows.
  openSection(host, key);
}

// The request-level hook points, in the order of `daedalus/providers/hooks.py`.
const HOOK_POINTS = [
  ["on-catalog", "On catalog", "hooks", "At each catalog build, as the row goes to the store."],
  ["on-request", "On request", "hooks", "Before the chain of a chat request, and on the repeat with the count. It sets the key of a turn."],
  ["on-prompt", "On prompt", "hooks", "Before the first attempt, when the chain holds a reasoning model. It sets the reasoning effort."],
  ["on-upstream", "On upstream", "hooks", "On the request body, before it goes to the provider."],
  ["on-answer", "On answer", "hooks", "On the non-streamed answer of a provider."],
  ["on-chunk", "On chunk", "hooks", "On each streamed chunk of a chat request."],
];

// The Settings form: [group, title, [[key, label, unit, hint], ...]].
const SETTINGS = [
  ["routing", "Routing", [
    ["threshold", "Success threshold", "", "The odds a tier needs to take a request. 0.75 is the value of the shipped table."],
    ["escalation", "Escalation keywords", "list", "1 word or phrase per chip. A match in the last user message moves the daedalus/auto session tier 1 step up."],
    ["switch", "Switch keywords", "list", "1 word or phrase per chip. A match in the last user message gives the pool session another model of the same tier."],
  ]],
  // The 5th element of a row lists the modes that show it. Only `mode` always shows.
  ["affinity", "Affinity", [
    ["mode", "Mode", "choice", "none: no pin and no race. session: each conversation stays on 1 model. race: the next models also race the first content."],
    ["change_on_draw", "Change pin on draw", "", "Off: keep the current pin while it remains eligible after a weighted draw.", ["session"]],
    ["idle", "Idle expiry", "s", "The session model expires after this time without a request.", ["session", "race"]],
    ["stay", "Stay share", "", "The share of first-tier draws for the session model. Below 1.", ["session"]],
    ["count", "Racing models", "", "The models that race the original one. 1 to 10.", ["race"]],
    ["chance", "Race chance", "", "The chance to start the racing models with the original one. 0 to 1.", ["race"]],
    ["slow", "Race slow token", "s", "Seconds with no content from the first model. Then the racing models start.", ["race"]],
    ["penalty", "Loser factor", "x", "The weight factor for the model that loses the race. At most 1.", ["race"]],
  ]],
  ["balance", "Load Distribution", [
    ["weights", "Weights on", "", "Off: all weights stay at 1, and the chain keeps the usual order."],
    ["success", "Success", "x", "The weight factor for a success."],
    ["fault", "Fault", "x", "The weight factor for a fault."],
    ["slow", "Slow success", "x", "The weight factor for a slow first token."],
    ["hourly", "Hourly recovery", "x", "The weight factor for each hour."],
    ["rate_limit", "Rate limit", "x", "The weight factor for an HTTP 429."],
    ["first", "First backoff", "s", "The cooldown of a first 429 with no reset time."],
    ["longest", "Longest backoff", "s", "Each next 429 doubles the cooldown, up to this time."],
    ["pacing", "Pacing on", "", "A model at its rpm or tpm for the last minute leaves the chains."],
  ]],
  ["limits", "Limits", [
    ["request", "Request", "s", "The time to wait for an answer, for all attempts. A stream that started does not stop at this limit."],
    ["wait", "Wait", "s", "The time without data from the provider. Keep-alive bytes do not count."],
    ["slow", "Slow first token", "s", "A first token after this time is slow."],
    ["calls", "Tool calls", "", "Repeated identical calls since the last user message; 2–100."],
    ["repeats", "Text repeats", "", "Consecutive copies of a text passage; 2–16."],
    ["shortest", "Shortest passage", "", "Minimum period in characters; 1–1,000 and no greater than Longest."],
    ["longest", "Longest passage", "", "Maximum period in characters; 1–10,000."],
  ]],
  ["optimization", "Optimization", [
    ["enabled", "Headroom on", "", "Off: the messages of every model go to the provider unchanged."],
    ["timeout", "Headroom timeout", "s", "After this time, the original messages go to the provider."],
  ]],
  ["catalog", "Catalog", [
    ["every", "Rebuild interval", "h", "The hours between rebuilds. 0 stops them."],
    ["anchor", "Anchor hour", "h", "The local hour (TZ) that the rebuild times start from."],
  ]],
  // The request points live in the hooks table, so the card holds the manager alone.
  ["hooks", "Hooks", []],
  ["personalization", "Personalization", [
    ["tier-a", "Tier A", "name", "The client name of the tier A pool: sophos by default."],
    ["tier-b", "Tier B", "name", "The client name of the tier B pool: deinos by default."],
    ["tier-c", "Tier C", "name", "The client name of the tier C pool: koinos by default."],
    ["tier-d", "Tier D", "name", "The client name of the tier D pool: moros by default."],
    ["audio", "Transcription", "name", "The client name of the transcription pool: graphos by default."],
    ["images", "Image", "name", "The client name of the image pool: photos by default."],
    ["theme", "Theme", "choice", "System follows the light or dark setting of the device."],
    ["time_format", "Time format", "choice", "The hour of each shown time. Every time carries its date."],
  ]],
];

// The Settings cards of each column, from top to bottom.
// The options of each choice field.
const CHOICES = {
  mode: [["none", "None"], ["session", "Session"], ["race", "Race"]],
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
  "affinity.stay", "affinity.chance", "affinity.penalty",
  "weights.success", "weights.fault", "weights.slow", "weights.hourly", "weights.rate_limit",
  "routing.threshold",
]);
// The input bounds. The server also checks them before saving.
const MINIMA = { "loops.calls": 2, "loops.repeats": 2, "loops.shortest": 1, "loops.longest": 1, "affinity.count": 1 };
const MAXIMA = {
  "catalog.anchor": 23,
  "timeouts.request": 86400,
  "timeouts.wait": 86400,
  "timeouts.slow": 86400,
  "headroom.timeout": 86400,
  "affinity.count": 10,
  "affinity.chance": 1,
  "affinity.penalty": 1,
  "routing.threshold": 1,
  "loops.calls": 100,
  "loops.repeats": 16,
  "loops.shortest": 1000,
  "loops.longest": 10000,
};

// The value in the file, or null when the file does not set it.
const fileValue = (group, key) => state.settings.file?.[group]?.[key] ?? null;
const setting = (group, key) => fileValue(group, key) ?? state.settings.defaults[group][key];
// A setting that holds a list of files: an older file holds 1 path.
const settingList = (group, key) => {
  const found = setting(group, key);
  return Array.isArray(found) ? found : found ? [found] : [];
};
// The boolean settings, such as `enabled` and `change_on_draw`, are checkboxes in the form.
const isSwitch = (group, key) => typeof state.settings.defaults[group][key] === "boolean";

// The API keys card of the Settings page: the form, the reveal and the table ride in it.
function keysCard() {
  return `<div class="card" data-section="keys"><h3>API Keys</h3>
    <form class="toolbar" id="new-key">
      <input id="key-name" maxlength="40" placeholder="Name of the new key" required>
      <button class="primary" type="submit">New key</button>
      <span class="message bad" id="key-message" role="alert"></span>
    </form>
    <div class="reveal" id="reveal" hidden>
      <span>Copy the key <b id="reveal-name"></b> now. daedalus shows it only this time.</span>
      <code id="reveal-key"></code>
      <button class="ghost" id="copy" type="button">Copy</button>
      <button class="ghost" id="reveal-close" type="button">Done</button>
    </div>
    <table class="keys">
      <thead><tr>
        <th>Name</th><th>Key</th><th class="hide-sm">Created</th><th>Last used</th><th></th>
      </tr></thead>
      <tbody id="keys"></tbody>
    </table>
  </div>`;
}

function renderSettings() {
  // A render keeps the text of an edit in progress. The saved text shows otherwise.
  const typed = state.settingsView === "yaml" ? $("settings-editor")?.value : undefined;
  // The rebuild of the card leaves the pane at its top: the scroll comes back after it.
  const scroll = settingsScroll();
  const card = ([group, title, fields]) => `
    <div class="card"><h3>${esc(title)}</h3>${fields.map(([key, label, unit, hint]) => {
      const id = `set-${group}-${key}`;
      if (isSwitch(group, key)) {
        // A boolean row reads as a row: the label with its hint, then the switch at the right edge.
        return `<label class="field info" for="${id}">${labelSpan(label, hint)}
          <input type="checkbox" role="switch" class="switch" id="${id}" ${setting(group, key) ? "checked" : ""}></label>`;
      }
      if (unit === "choice") {
        return `<label class="field info" for="${id}">${labelSpan(label, hint)}
          <select id="${id}">${CHOICES[key].map(([name, text]) => `<option value="${name}"${setting(group, key) === name ? " selected" : ""}>${text}</option>`).join("")}</select></label>`;
      }
      if (unit === "name") {
        return `<label class="field info" for="${id}">${labelSpan(label, hint)}
          <span class="input"><i class="prefix">daedalus/</i><input type="text" id="${id}" maxlength="40" readonly
            aria-label="${esc(label)}" data-value="${esc(label)}" spellcheck="false"
            value="${esc(fileValue(group, key) ?? "")}" placeholder="${esc(state.settings.defaults[group][key])}"></span></label>`;
      }
      if (unit === "list") {
        const values = listValue(group, key);
        return `<div class="field stack info">${labelSpan(label, hint)}
          <div class="pills" id="${id}" aria-label="${esc(label)}">${values
            .map((value, index) => settingPill(group, key, value, index)).join("")}${settingAdder(group, key)}</div></div>`;
      }
      const fallback = state.settings.defaults[group][key];
      const value = fileValue(group, key);
      return `<label class="field info" for="${id}">${labelSpan(label, hint)}
        <span class="input"><input type="number" readonly aria-label="${esc(label)}" data-value="${esc(label)}"
          min="${MINIMA[`${group}.${key}`] ?? 0}" id="${id}" value="${value ?? ""}"
          step="${DECIMALS.has(`${group}.${key}`) ? "any" : "1"}" ${MAXIMA[`${group}.${key}`] ? `max="${MAXIMA[`${group}.${key}`]}"` : ""}
          placeholder="${fallback ?? ""}"><i>${esc(unit)}</i></span></label>`;
    }).join("")}${group === "hooks" ? hooksManager() : ""}</div>`;
  // The section list: the rail names every group, and the pane holds its card.
  const groups = SETTINGS.filter(([group]) => group !== "headroom" || state.settings.headroom_available);
  const items = groups.map(([group, title]) => [group, title]).concat([["keys", "API Keys"], ["yaml", "YAML"]]);
  const picked = sectionState($("settings")).key;
  const active = items.some(([key]) => key === picked) ? picked : items[0][0];
  $("settings").innerHTML = sectionList("settings", items, active)
    + sectionPane("Settings", groups.map(([group, title, fields]) => card([group, title, fields]).replace(
      '<div class="card">', `<div class="card" data-section="${esc(group)}">`)).join("")
      + keysCard()
      + yamlCard("settings-editor", "settings-yaml-save", `The file text of ${fileName(state.settings.path)}. A key that the form cannot show still opens here.`));
  pickSection($("settings"), active);
  $("settings-editor").value = typed ?? state.settings.text;
  // The rendered file wins, and the shown rows follow its mode.
  showAffinityRows(setting("affinity", "mode"));
  showSwitchRows();
  renderSettingsSave();
  placeUndo();
  fitSectionPane();
  keepSettingsScroll(scroll);
}

// A row with a mode list shows only under those modes: the race values wait for race, and the pin
// values wait for session or race. Only `mode` always shows. The rows stay in the page, so a pick
// keeps its value and the card moves rows only.
function showAffinityRows(mode = $("set-affinity-mode")?.value) {
  for (const [key, , , , show] of SETTINGS.find(([group]) => group === "affinity")[2]) {
    const row = $(`set-affinity-${key}`)?.closest(".field");
    if (row) row.hidden = Boolean(show && !show.includes(mode));
  }
}

// A card whose first row is an On switch greys its other rows while the switch is off. The other
// boolean rows, such as `change_on_draw` of Affinity, belong to their own card and grey nothing.
function showSwitchRows() {
  for (const [group, , fields] of SETTINGS) {
    if (!fields.length) continue;
    const [toggle] = fields[0];
    if (!isSwitch(group, toggle)) continue;
    const off = !$(`set-${group}-${toggle}`)?.checked;
    for (const [key] of fields) {
      if (key === toggle) continue;
      const field = $(`set-${group}-${key}`)?.closest(".field");
      if (!field) continue;
      field.classList.toggle("off", off);
      field.querySelectorAll("input, select, button").forEach((node) => { node.disabled = off; });
      if (!off) replay(field);
    }
  }
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
        value = listValue(group, key);
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
      } else {
        value = input.value.trim() === "" ? null : Number(input.value);
        before = fileValue(group, key);
      }
      if (value !== before) (changes[group] ||= {})[key] = value;
    }
  }
  // The hook manager sends its own rows: the folder, the sources and the names that stay off.
  const dir = document.getElementById("set-hooks-dir");
  if (dir && dir.value.trim() && dir.value.trim() !== setting("hooks", "dir")) (changes.hooks ||= {}).dir = dir.value.trim();
  const sources = listValue("hooks", "sources");
  if (JSON.stringify(sources) !== JSON.stringify(settingList("hooks", "sources"))) (changes.hooks ||= {}).sources = sources;
  const disabled = listValue("hooks", "disabled");
  if (JSON.stringify(disabled) !== JSON.stringify(settingList("hooks", "disabled"))) (changes.hooks ||= {}).disabled = disabled;
  // The rows of the table write the request points: the list holds the files of that point.
  for (const [point] of requestPoints()) {
    if (!(state.settings.lists || {})[`hooks.${point}`]) continue;
    const values = listValue("hooks", point).filter(Boolean);
    if (JSON.stringify(values) !== JSON.stringify(settingList("hooks", point))) (changes.hooks ||= {})[point] = values.length ? values : null;
  }
  return changes;
}

function settingsDirty() {
  if (!state.settings) return false;
  if (state.settingsView === "yaml") return ($("settings-editor")?.value ?? state.settings.text) !== state.settings.text;
  return Object.keys(settingsChanges()).length > 0;
}

function renderSettingsSave() {
  const yaml = state.settingsView === "yaml";
  // The card Save writes the text. The rows write themselves, on the change or the close.
  const cardSave = $("settings-yaml-save");
  if (cardSave) cardSave.disabled = !yaml || !settingsDirty();
}

async function loadSettings() {
  state.settings = await call("settings");
  state.settings.lists = {};
  renderLegend();
  applyTheme(setting("personalization", "theme"));
  hourCycle = setting("personalization", "time_format") === "12h" ? "h12" : "h23";
  renderSettings();
}

// A pick sets the view of the page. Unsaved changes of the other view go after a confirmation.
async function pickSettingsSection(key) {
  const view = key === "yaml" ? "yaml" : "form";
  if (view !== state.settingsView) {
    if (settingsDirty() && !(await ask("Discard changes", "The unsaved settings changes go away.", "Discard", true))) return;
    state.settingsView = view;
    clearWrite("settings");
    renderSettings();
  }
  openSection($("settings"), key);
}

async function saveSettings() {
  if (!settingsDirty()) return clearWrite("settings");
  const text = $("settings-editor")?.value ?? "";
  const body = state.settingsView === "yaml" ? { text } : { changes: settingsChanges() };
  rememberWrite("settings", { settings: true, text: state.settings.text }, state.writeAnchor ?? $("settings-yaml-save"));
  try {
    await call("settings", { method: "PUT", body: JSON.stringify(body) });
    await loadSettings();
    clearWrite("settings");
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    showWrite("settings", error.message, true);
  }
  renderSettingsSave();
}

async function refreshFast() {
  // A failed call keeps the last values and marks the page, so the page never lies in silence.
  const [answer, rows] = await Promise.allSettled([
    call("status"),
    call(`requests?limit=${state.requestLimit}`),
  ]);
  for (const part of [answer, rows]) {
    if (part.status === "rejected" && part.reason instanceof LoggedOut) throw part.reason;
  }
  if (answer.status === "rejected") setStateKnown(false);
  else {
    const status = answer.value;
    // After a rebuild, the pools and models change too.
    if (state.catalog.rebuilding && !status.catalog?.rebuilding) guarded(refreshSlow);
    state.catalog = status.catalog || {};
    renderStatus(status);
  }
  if (rows.status === "fulfilled") {
    state.requests = splitRequests(rows.value);
    state.requestFetched = rows.value.length;
    renderRequestTable();
  }
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

// A time cell of the Limits table: the relative time, with the exact stamp on hover. An empty
// value keeps the dash.
const limitTimeCell = (seconds, cls) => (seconds
  ? `<td class="${cls}" title="${esc(stamp(seconds))}">${esc(relative(seconds))}</td>`
  : `<td class="${cls}">-</td>`);

// The balances, then the 3 rate-limit rows with the least left.
function overviewLimits(data) {
  if (!data) return "";
  const bar = (left) => (left == null ? "" : weightBar(left));
  const balances = data.providers.flatMap((p) => p.items.map(([label, value, left]) =>
    `<div class="balance">${line(`${esc(p.name)} &middot; ${esc(label)}`, esc(value))}${bar(left)}</div>`));
  const share = (r) => (r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0);
  const rows = data.lanes.flatMap((lane) => lane.rows.map((row) => ({ ...row, model: lane.model })))
    .sort((a, b) => share(a) - share(b)).slice(0, 3)
    .map((r) => `<div class="balance">${line(`<span title="${esc(r.model)}">${modelName(r.model)}</span>`,
      `<span title="${r.remaining.toLocaleString()} of ${r.limit.toLocaleString()}">${floorCount(r.remaining)} of ${floorCount(r.limit)} ${esc(unit(r))}</span>`)}${bar(share(r))}</div>`);
  return [...balances, ...rows].join("");
}

// One row for each lane of each model, as the Limits table reads them.
function limitRows() {
  return (state.limits?.lanes || []).flatMap((lane) =>
    lane.rows.map((row) => ({ ...row, model: lane.model, client: lane.client, at: lane.at })));
}

// The rows that the filter keeps: a text match over the model and the client.
function shownLimits() {
  return limitRows().filter((r) => !state.limitSearch
    || `${r.model} ${r.client || ""}`.toLowerCase().includes(state.limitSearch));
}

function renderLimits(data) {
  state.limits = data;
  limitsHash();
  renderLimitRows(shownLimits());
  $("limits-checked").textContent = data.checked ? `Checked ${stamp(data.checked)} · each hour` : "Not checked yet";
  $("balances").hidden = !data.providers.length;
  draw("balances", data.providers.map((p) => `<div class="card"><h3>${esc(p.name)}</h3>
    ${p.items.map(([label, value, left]) => `<div class="balance">${line(esc(label), esc(value))}
      ${left == null ? "" : weightBar(left)}</div>`).join("")}</div>`).join(""));
}

function renderLimitRows(rows) {
  $("limits-clear").hidden = !state.limitSearch;
  draw("limit-rows", rows.length ? rows.map((r) => `<tr>
      ${nameCell(r.model, `${modelName(r.model)}${r.client ? ` <span class="muted">${esc(r.client)}</span>` : ""}`)}
      <td title="${esc(limitTitle(r))}">${esc(limitUnit(r))}</td>
      <td><div class="weight left" title="${r.remaining.toLocaleString()} of ${r.limit.toLocaleString()}">
        ${weightBar(r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0)}
        <span class="num${r.remaining > 0 ? "" : " out"}">${floorCount(r.remaining)} of ${floorCount(r.limit)}</span></div></td>
      ${limitTimeCell(r.reset, "hide-sm muted time")}
      ${limitTimeCell(r.at, "hide-sm muted time")}
    </tr>`).join("") : '<tr><td colspan="5" class="empty">No rate-limit headers yet. Groq and Mistral send them with each answer.</td></tr>');
}

function rebuildDiff(event) {
  const marks = [
    event.added.length ? `<span class="added">+${event.added.length}</span>` : "",
    event.removed.length ? `<span class="removed">-${event.removed.length}</span>` : "",
    event.changed.length ? `<span class="moved">~${event.changed.length}</span>` : "",
  ].join(" ");
  return marks || '<span class="muted">-</span>';
}

function renderNotifications(data) {
  state.notifications = data;
  const open = state.notificationOpen || (state.notificationOpen = new Set());
  draw(
    "rebuild-rows",
    data.rebuilds.length
      ? data.rebuilds
          .map((event, index) => {
            const detail = [
              ...event.added.map((model) => ["added", "+", model]),
              ...event.removed.map((model) => ["removed", "-", model]),
              ...event.changed.map((model) => ["moved", "~", model]),
            ];
            return `<tr class="rebuild-row" data-rebuild="${index}" tabindex="0">
      <td class="muted time">${stamp(event.at)}</td>
      <td>${esc(event.reason)}</td>
      <td class="num">${event.models}</td>
      <td>${rebuildDiff(event)}</td>
      <td class="hide-sm">${event.failed.length ? esc(event.failed.join(", ")) : '<span class="muted">-</span>'}</td>
    </tr>` +
              (detail.length
                ? `<tr class="rebuild-detail" hidden="${open.has(index) ? "" : "hidden"}"><td colspan="5">
        ${detail
          .map(([kind, mark, model]) => `<div class="line"><span class="${kind}">${mark}</span><span>${esc(model)}</span></div>`)
          .join("")}
      </td></tr>`
                : "");
          })
          .join("")
      : '<tr><td colspan="5" class="empty">No rebuilds yet. The first one lands with the next catalog build.</td>');
  const update = data.update;
  let body = "<h3>Update</h3>";
  if (!update) body += none("Not checked yet");
  else if (update.error === "no commit in this build's version")
    body += none("This build's version names no commit, so the check has nothing to compare.");
  else if (update.error) body += none(`Check failed: ${esc(update.error)}`);
  else if (update.channel === "release")
    body += update.update
      ? line("Available", `<a href="${esc(update.url)}">${esc(update.latest)}</a>`) +
        line("You are on", esc(update.current))
      : line("Up to date", esc(update.latest || update.current));
  else if (update.update) body += line("Behind main", `${update.behind} ${update.behind === 1 ? "commit" : "commits"}`);
  else body += line("Up to date", "main");
  body += line("Checked", update ? stamp(update.at) : "-");
  draw(
    "update-card",
    body + '<button class="ghost" id="update-check" type="button">Check now</button>'
  );
  draw(
    "limit-warnings-card",
    "<h3>Limit warnings</h3>" +
      (data.limits.length
        ? data.limits
            .map(
              (row) => `<div class="balance">
      ${line(
        `${esc(row.model)}${row.client ? ` <span class="muted">${esc(row.client)}</span>` : ""}`,
        `${floorCount(row.remaining)} of ${floorCount(row.limit)} ${esc(row.kind)}${row.span ? ` ${esc(row.span)}` : ""}`
      )}
      ${weightBar(row.share)}
    </div>`
            )
            .join("")
        : none("No row is near its limit."))
  );
}

async function refreshSlow() {
  const [pools, models, limits, notifications] = await Promise.all([
    call("pools"),
    call("models"),
    call("limits"),
    call("notifications"),
    refreshKeys(),
  ]);
  renderLimits(limits);
  renderNotifications(notifications);
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
  // With no stored session, 1 hint call says whether the cookie is live, so the start asks for no 401.
  if (!session() && !(await call("login").catch(() => ({}))).session) return showLogin();
  // The settings come first, so that the first tables use the time format.
  await loadSettings();
  await loadLegend();
  // The page shows before the first state answer, so a slow answer paints the shape of the page.
  $("login").hidden = true;
  $("app").hidden = false;
  // The pane sizes once the app shows: a pane built while the app is hidden has no height yet.
  fitSectionPane();
  setStateKnown(null);
  skeletons("requests", 13);
  skeletons("models", 9);
  skeletons("rebuild-rows", 5);
  await refreshFast();
  await refreshSlow();
  takeFiles(await call("files"));
  await refreshEnv();
  [state.overrideKeys, state.providerDefaults] = await Promise.all([call("provider-keys"), call("provider-defaults")]);
  state.file = 0;
  showFileError(0);
  renderFiles();
  renderTiers();
  renderOverview();
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
// A rebuild row opens its list of added, removed and moved models, and closes it on the second pick.
$("rebuild-rows").addEventListener("click", (event) => {
  const row = event.target.closest("tr.rebuild-row");
  if (!row || !state.notifications) return;
  const open = state.notificationOpen || (state.notificationOpen = new Set());
  const index = Number(row.dataset.rebuild);
  if (open.has(index)) open.delete(index);
  else open.add(index);
  renderNotifications(state.notifications);
});
$("update-card").addEventListener("click", async (event) => {
  const button = event.target.closest("#update-check");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Checking";
  try {
    const result = await call("updates", { method: "POST" });
    if (state.notifications) {
      state.notifications = { ...state.notifications, update: result };
      renderNotifications(state.notifications);
    }
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    button.disabled = false;
    button.textContent = "Check now";
  }
});
$("show-password").addEventListener("click", () => showPassword($("login").password.type === "password"));
document.addEventListener("click", async (event) => {
  // The info icon of a closed group shows its hint. It never opens the group.
  if (event.target.closest(".fold summary .hint")) {
    event.preventDefault();
    return;
  }
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

// The API keys card rides in the rendered Settings pane, so its events delegate to the pane.
$("settings").addEventListener("input", (event) => {
  if (event.target.id === "key-name") showLengthLimit($("key-name"), $("key-message"));
});
$("settings").addEventListener("submit", async (event) => {
  if (event.target.id !== "new-key") return;
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
$("settings").addEventListener("click", async (event) => {
  if (event.target.closest("#copy")) {
    try {
      await navigator.clipboard.writeText($("reveal-key").textContent);
      $("copy").textContent = "Copied";
    } catch {
      getSelection().selectAllChildren($("reveal-key"));
      $("copy").textContent = "Press Ctrl+C";
    }
    return;
  }
  if (event.target.closest("#reveal-close")) {
    $("reveal").hidden = true;
    $("reveal-key").textContent = "";
    $("copy").textContent = "Copy";
    return;
  }
  const button = event.target.closest("[data-key]");
  if (!button) return;
  const name = button.dataset.key;
  if (!(await ask("Delete key", `Clients with the key "${name}" get 401 at once.`, "Delete", true))) return;
  await guarded(async () => {
    await call("keys/" + encodeURIComponent(name), { method: "DELETE" });
    await refreshKeys();
  });
});
$("more-requests").addEventListener("click", () => {
  state.requestLimit = Math.min(state.requestLimit + REQUESTS_STEP, REQUESTS_KEPT);
  guarded(refreshFast);
});

// The Requests toolbar narrows the table at once, with no new call to the server.
$("request-search").addEventListener("input", () => {
  state.requestSearch = $("request-search").value.trim().toLowerCase();
  requestHash();
  renderRequestTable();
});
$("request-status").addEventListener("change", () => {
  state.requestStatus = $("request-status").value;
  requestHash();
  renderRequestTable();
});
$("request-hours").addEventListener("change", () => {
  state.requestHours = Number($("request-hours").value) || 0;
  requestHash();
  renderRequestTable();
});

$("live-toggle").addEventListener("click", () => {
  state.livePaused = !state.livePaused;
  if (!state.livePaused) {
    state.liveWaiting.clear();
    renderLive();
    guarded(refreshFast);
  }
  renderLiveToggle();
});

$("request-clear").addEventListener("click", () => {
  $("request-search").value = "";
  $("request-status").value = "all";
  $("request-hours").value = "0";
  state.requestSearch = "";
  state.requestStatus = "all";
  state.requestHours = 0;
  requestHash();
  renderRequestTable();
});

$("limits-search").addEventListener("input", () => {
  state.limitSearch = $("limits-search").value.trim().toLowerCase();
  limitsHash();
  renderLimitRows(shownLimits());
});

$("limits-clear").addEventListener("click", () => {
  $("limits-search").value = "";
  state.limitSearch = "";
  limitsHash();
  renderLimitRows(shownLimits());
});

$("notice-retry").addEventListener("click", () => {
  clearNotice();
  refresh();
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
  if (event.target.closest(".mobile-fallback-chain") || window.matchMedia("(max-width: 720px)").matches) return;
  const row = event.target.closest("tr.request");
  if (!row || !row.classList.contains("has-chain") || !getSelection().isCollapsed) return;
  const at = row.dataset.at;
  opened.has(at) ? opened.delete(at) : opened.add(at);
  shownRequests = "";
  renderRequestTable();
});
const PAGES = ["overview", "requests", "models", "notifications", "providers", "limits", "settings"];

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

// A Requests link such as #/requests?status=bad&hours=24&q=timeout sets the filters. The hash keeps
// them, so a shared link shows the same view.
function applyRequestFilters(query) {
  const params = new URLSearchParams(query);
  const asked = params.get("q") || "";
  state.requestSearch = asked.trim().toLowerCase();
  state.requestStatus = ["all", "ok", "bad"].includes(params.get("status")) ? params.get("status") : "all";
  state.requestHours = [0, 1, 24].includes(Number(params.get("hours"))) ? Number(params.get("hours")) : 0;
  $("request-search").value = asked;
  $("request-status").value = state.requestStatus;
  $("request-hours").value = String(state.requestHours);
  renderRequestTable();
}

// A Limits link such as #/limits?q=llama filters the table by model.
function applyLimitFilters(query) {
  const asked = new URLSearchParams(query).get("q") || "";
  state.limitSearch = asked.trim().toLowerCase();
  $("limits-search").value = asked;
  renderLimitRows(shownLimits());
}

// The filter owns the address bar only on the Limits page: the poll calls this on every page.
function limitsHash() {
  if (!location.hash.startsWith("#/limits")) return;
  const asked = $("limits-search").value.trim();
  history.replaceState(null, "", `#/limits${asked ? `?q=${encodeURIComponent(asked)}` : ""}`);
}

function requestHash() {
  const params = new URLSearchParams();
  if ($("request-search").value.trim()) params.set("q", $("request-search").value.trim());
  if (state.requestStatus !== "all") params.set("status", state.requestStatus);
  if (state.requestHours) params.set("hours", String(state.requestHours));
  const query = params.toString();
  history.replaceState(null, "", `#/requests${query ? `?${query}` : ""}`);
}

// A chevron at each end of the tab bar shows the tabs that wait off screen, and scrolls to them.
const nav = $("nav");
function markNavSteps() {
  const end = nav.scrollWidth - nav.clientWidth;
  const left = nav.scrollLeft <= 2;
  const right = nav.scrollLeft >= end - 2;
  $("nav-left").hidden = left;
  $("nav-right").hidden = right;
  nav.classList.toggle("fade-left", !left);
  nav.classList.toggle("fade-right", !right);
}
nav.addEventListener("scroll", markNavSteps, { passive: true });
for (const [id, step] of [["nav-left", -1], ["nav-right", 1]]) {
  $(id).addEventListener("click", () => nav.scrollBy({ left: (step * nav.clientWidth) / 2, behavior: "smooth" }));
}

// A wide screen holds the page still: each pane of a section takes the height that its own top
// leaves on the screen, so the form scrolls on its own under a still header and rail.
function settingsScroll() {
  const pane = $("settings")?.querySelector?.(".section-pane");
  return { pane: pane?.scrollTop ?? 0, top: typeof window.scrollY === "number" ? window.scrollY : 0 };
}

// A rebuild of the card lands under the same scroll, so an update from the sources holds its place.
function keepSettingsScroll(at) {
  const pane = $("settings")?.querySelector?.(".section-pane");
  if (pane && at.pane) pane.scrollTop = at.pane;
  if (at.top && typeof window.scrollTo === "function") window.scrollTo(0, at.top);
}

function fitSectionPane() {
  const wide = matchMedia("(min-width: 901px)").matches;
  const main = document.querySelector("main");
  for (const pane of document.querySelectorAll(".section-pane")) {
    if (!wide || !pane.offsetParent || !main) {
      pane.style.maxHeight = "";
      continue;
    }
    const pad = parseFloat(getComputedStyle(main).paddingBottom) || 0;
    const bottom = main.getBoundingClientRect().top + main.clientHeight - pad;
    pane.style.maxHeight = `${Math.max(240, bottom - pane.getBoundingClientRect().top - 16)}px`;
  }
}

let shownPage = "";
function showPage() {
  const [path, query] = location.hash.split("?");
  const asked = path.replace("#/", "").replace(/^config$/, "providers").replace(/^pools$/, "models");
  const page = PAGES.includes(asked) ? asked : PAGES[0];
  const changed = page !== shownPage;
  shownPage = page;
  // A pool card lands on the page it already shows: the table below it arrives, and the page
  // itself holds still, so a filter pick never moves the whole view.
  if (asked === "models" && query !== undefined) {
    applyModelFilters(query);
    if (!changed) replay($("models"));
  }
  if (asked === "requests" && query !== undefined) applyRequestFilters(query);
  if (asked === "limits" && query !== undefined) applyLimitFilters(query);
  document.querySelectorAll("section[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
    // The arriving page fades in on a page change, and the reflow read restarts the animation.
    section.classList.remove("enter");
    if (section.dataset.page === page && changed) {
      void section.offsetWidth;
      section.classList.add("enter");
    }
  });
  document.querySelectorAll("#nav a").forEach((link) => {
    const on = link.dataset.page === page;
    link.classList.toggle("on", on);
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  // The first paint marks the chevrons too, so a cut tab shows before the first status answer.
  markNavSteps();
  // The page renders before the section pick: a render replaces the markup of the pane, and the
  // slide of a phone back rides on the classes that the pick sets.
  if (page === "providers" && state.view === "form") renderForm();
  applySectionHash();
  slideAgain();
  document.title = `daedalus · ${document.querySelector(`#nav a[data-page="${page}"]`).firstChild.textContent}`;
  fitSectionPane();
}

window.addEventListener("hashchange", showPage);
window.addEventListener("popstate", applySectionHash);
showPage();
$("search").addEventListener("input", pickRows);
$("model-head").addEventListener("click", (event) => {
  const th = event.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  // Up, then down, then back to the default order.
  const same = state.sort.key === key;
  state.sort = !same ? { key, dir: 1 } : state.sort.dir > 0 ? { key, dir: -1 } : { key: "", dir: 1 };
  renderSortHeads();
  pickRows();
});
$("sort-small").addEventListener("change", () => {
  state.sort = { key: $("sort-small").value, dir: 1 };
  renderSortHeads();
  pickRows();
});
$("mode").addEventListener("change", () => {
  state.mode = $("mode").value;
  pickRows();
});
$("tiers").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tier]");
  if (!button) return;
  state.tier = button.dataset.tier;
  renderTiers();
  pickRows();
});
$("files").addEventListener("click", (event) => {
  const button = event.target.closest("[data-file]");
  if (button) openFile(Number(button.dataset.file));
});
async function reloadFiles(keep) {
  takeFiles(await call("files"));
  const index = Math.max(0, state.files.findIndex((file) => file.path === keep));
  state.file = index;
  showFileError(index);
  renderFiles();
  renderForm();
  guarded(refreshEnv);
}

$("files").addEventListener("click", async (event) => {
  if (!event.target.closest("#new-provider")) return;
  clearWrite("providers");
  const ok = await ask(
    "New provider", "Lowercase letters, digits and dashes.", "Create", false, "Provider name",
  );
  if (!ok) return;
  const name = $("modal-input").value.trim();
  if (!name) return;
  try {
    const made = await call("files", { method: "POST", body: JSON.stringify({ name }) });
    await reloadFiles(made.path);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    showWrite("providers", error.message, true);
  }
});
$("files").addEventListener("click", async (event) => {
  if (!event.target.closest("#drop-provider")) return;
  clearWrite("providers");
  const file = state.files[state.file];
  if (!file || !(await ask("Delete file", `${file.path} goes away. The main file stays.`, "Delete", true))) return;
  try {
    await call("files", { method: "DELETE", body: JSON.stringify({ path: file.path }) });
    await reloadFiles(null);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    showWrite("providers", error.message, true);
  }
});
$("provider-form").addEventListener("input", (event) => {
  if (event.target.id === "editor") renderFiles();
});
bindValueRows($("provider-form"), () => saveForm());
bindValueRows($("settings"), () => saveSettings());
$("provider-form").addEventListener("click", (event) => {
  const section = event.target.closest(".sections button[data-section]");
  if (section) return pickProviderSection(section.dataset.section);
  if (event.target.closest("[data-back]")) return closeSection($("provider-form"));
  if (event.target.closest("#yaml-save")) return saveYaml();
  const drop = event.target.closest("[data-drop]");
  if (drop) {
    state.writeAnchor = anchorOf(drop);
    return dropAt(JSON.parse(drop.dataset.drop));
  }
  const add = event.target.closest("[data-add]");
  if (add) {
    state.writeAnchor = anchorOf(add);
    if (add.dataset.kind === "match") return addClientKey(add);
    showAdder(add);
  }
});

// The client key adder opens the 2-field modal: the key name and its value.
async function addClientKey(button) {
  const path = JSON.parse(button.dataset.add);
  clearWrite("providers");
  const ok = await askPair(
    "Client key", "The key and its value go to the file when this dialog closes.",
    "Key name", "Provider key",
  );
  if (!ok) return;
  const name = $("modal-input").value.trim();
  const value = $("modal-input2").value.trim();
  if (!name || !value) return showWrite("providers", "Write the key name and its value.", true);
  parentOf([...path, name], {})[name] = parsed(value);
  renderCard(path[0]);
  saveForm();
}
$("provider-form").addEventListener("input", (event) => {
  if (event.target.dataset.set) setText(event.target);
});
$("provider-form").addEventListener("click", (event) => {
  const add = event.target.closest("[data-form-hook-add]");
  if (add) {
    const [path] = JSON.parse(add.dataset.formHookAdd);
    formHookList(path).push({ [HOOK_DEFAULT]: "" });
    renderCard(path[0]);
    return saveForm();
  }
  const drop = event.target.closest("[data-form-hook-drop]");
  if (drop) {
    const parts = JSON.parse(drop.dataset.formHookDrop);
    const index = parts.pop();
    formHookList(parts).splice(index, 1);
    renderCard(parts[0]);
    return saveForm();
  }
  const choice = event.target.closest("[data-form-hook-choice]");
  if (choice) {
    const parts = JSON.parse(choice.dataset.formHookChoice);
    const file = parts.pop();
    const index = parts.pop();
    const list = formHookList(parts);
    const entry = list[index] && typeof list[index] === "object" ? list[index] : {};
    list[index] = { [Object.keys(entry)[0] ?? HOOK_DEFAULT]: file };
    openFormHook = null;
    renderCard(parts[0]);
    return saveForm();
  }
  const pick = event.target.closest("[data-form-hook-pick]");
  if (pick) {
    const at = pick.dataset.formHookPick;
    openFormHook = openFormHook === at ? null : at;
    renderCard(JSON.parse(at)[0]);
  }
});
$("provider-form").addEventListener("change", (event) => {
  if (event.target.dataset.pattern) return renamePattern(event.target);
  if (event.target.dataset.formHookPoint) {
    const parts = JSON.parse(event.target.dataset.formHookPoint);
    const index = parts.pop();
    const list = formHookList(parts);
    const entry = list[index] && typeof list[index] === "object" ? list[index] : {};
    list[index] = { [event.target.value]: String(Object.values(entry)[0] ?? "") };
    renderCard(parts[0]);
    return saveForm();
  }
});
$("provider-form").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.dataset.pattern) event.target.blur();
});
window.addEventListener("resize", () => {
  markNavSteps();
  fitSectionPane();
});
$("settings").addEventListener("input", (event) => {
  clearWrite("settings");
  showLengthLimit(event.target, $("settings-message"));
  // The mode decides which rows of the affinity card show. The pick stays, so only the rows move.
  if (event.target.id === "set-affinity-mode") {
    showAffinityRows();
    return renderSettingsSave();
  }
  // A switch decides whether its card keeps its other rows usable.
  if (event.target.type === "checkbox") showSwitchRows();
  renderSettingsSave();
});
$("settings").addEventListener("click", async (event) => {
  const section = event.target.closest(".sections button[data-section]");
  if (section) return pickSettingsSection(section.dataset.section);
  if (event.target.closest("[data-back]")) return closeSection($("settings"));
  // The manager: the modal of a source, the delete of 1, the update and the take of a scan.
  if (event.target.closest("[data-hook-source-add]")) return addHookSource();
  if (event.target.closest("[data-hook-source-drop]")) return dropHookSource();
  if (event.target.closest("[data-hooks-update]")) return runHooksUpdate();
  if (event.target.closest("[data-hook-take-all]")) return takeHooks();
  const drop = event.target.closest("[data-setting-drop]");
  if (drop) {
    state.writeAnchor = anchorOf(drop);
    return dropSetting(JSON.parse(drop.dataset.settingDrop));
  }
  const add = event.target.closest("[data-setting-add]");
  if (add) {
    state.writeAnchor = anchorOf(add);
    showSettingAdder(add);
  }
});
$("settings").addEventListener("change", (event) => {
  const input = event.target;
  // The manager writes at once too: the source pick stays in the page, and the 2 boxes save.
  if (input.dataset.hookSource !== undefined) {
    state.hooksSource = Number(input.value) || 0;
    return;
  }
  if (input.dataset.hookToggle) return toggleHook(input.dataset.hookToggle, input.checked);
  if (input.dataset.hookTake) {
    state.hooksScan.take[input.dataset.hookTake] = input.checked;
    return;
  }
  // A switch and a pick list hold 1 valid value, so the change writes it at once.
  if (input.type !== "checkbox" && input.tagName !== "SELECT") return;
  state.writeAnchor = anchorOf(input);
  saveSettings();
});
$("settings").addEventListener("input", (event) => {
  if (event.target.id !== "settings-editor") return;
  clearWrite("settings");
  renderSettingsSave();
});
$("settings").addEventListener("click", (event) => {
  if (event.target.closest("#settings-yaml-save")) saveSettings();
});
document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
  if (!yamlView()) return;
  event.preventDefault();
  if (location.hash.startsWith("#/settings")) saveSettings();
  else saveYaml();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.timers?.length) refresh();
});
window.addEventListener("beforeunload", (event) => {
  if (dirty() || settingsDirty()) event.preventDefault();
});

// Enter in the name field confirms, as the first submit button is Cancel.
$("modal-input").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  $("modal-ok").click();
});

// A click on the backdrop closes the dialog. A click on the box itself does not, so the check
// tests the box. Escape closes a dialog on its own.
$("modal").addEventListener("click", (event) => {
  const dialog = event.currentTarget;
  if (event.target !== dialog) return;
  const box = dialog.getBoundingClientRect();
  const outside = event.clientX < box.left || event.clientX > box.right
    || event.clientY < box.top || event.clientY > box.bottom;
  if (outside) dialog.close("cancel");
});

// The dialog closes on Confirm, on Cancel, on Escape and on a backdrop click.
$("modal").addEventListener("close", (event) => {
  const done = globalThis.__settle;
  globalThis.__settle = null;
  done?.(event.target.returnValue === "ok");
});

renderLegend();
guarded(start);
