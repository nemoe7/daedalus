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
const SHOWN = 4;
// The Requests page loads 50 rows, and each "Show more" adds 50, up to the 500 that the server keeps.
const REQUESTS_STEP = 50;
const REQUESTS_KEPT = 500;
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const tierLetter = (name) => (name || "").replace("TIER-", "") || "-";
const tokens = (n) => !n ? "-" : n >= 1e6 ? +(n / 1e6).toFixed(1) + "M" : Math.round(n / 1024) + "k";
const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(
  [], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const state = {
  models: [], tier: "All", mode: "all", sort: { key: "", dir: 1 }, files: [], file: 0, saved: [], timers: [],
  view: "form", forms: [], formSaved: [], overrideKeys: [], providerDefaults: {}, settingsView: "form",
  pools: [], requests: [], requestLimit: REQUESTS_STEP, keys: [], catalog: {}, settings: null,
  live: new Map(), source: null,
};

const fileName = (path) => path.split(/[\\/]/).pop();
const line = (left, right) => `<div class="line"><span>${left}</span><span>${right}</span></div>`;
const count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const none = (text) => `<div class="more">${text}</div>`;

// One card for each page, from the data that the pages already read.
function renderOverview() {
  $("ov-pools").innerHTML = state.pools.map((pool) => line(
    esc(pool.name.replace("daedalus/", "")), count(pool.members.length, "model"),
  )).join("") || none("No pools");
  $("ov-requests").innerHTML = state.requests.slice(0, 10).map((r) => line(
    `<span class="status ${statusClass(r)}">${statusText(r)}</span> ${esc(r.via || r.model || "-")}`,
    clock(r.at),
  )).join("") || none("No requests");
  const tiers = ["A", "B", "C", "D"].map((t) => [t, state.models.filter((m) => tierLetter(m.tier) === t).length]);
  const tools = state.models.filter((m) => m.tools).length;
  $("ov-models").innerHTML = state.models.length
    ? tiers.map(([t, n]) => line(`<span class="tier">${t}</span> Tier ${t}`, count(n, "model"))).join("")
      + line("Tool calls", `${tools} of ${state.models.length}`)
    : none("No models. Run daedalus catalog.");
  const used = state.keys.filter((k) => k.used).sort((a, b) => b.used - a.used)[0];
  $("ov-keys").innerHTML = state.keys.length
    ? line("Keys", count(state.keys.length, "key")) + line("Last used", used ? `${esc(used.name)} &middot; ${dateTime(used.used)}` : "never")
    : none("No API keys. The master key opens /v1.");
  const { built, next } = state.catalog;
  $("ov-providers").innerHTML = state.files.map((f) => line(esc(fileName(f.path)), "YAML")).join("")
    + line("Catalog built", built ? shortTime(built) : "never")
    + line("Next rebuild", next ? shortTime(next) : "no schedule");
  if (state.settings) {
    const on = (group) => (setting(group, "enabled") ? "On" : "Off");
    $("ov-settings").innerHTML = line("Session affinity", on("session_affinity"))
      + line("Weights", on("weights"))
      + line("Pacing", on("pacing"))
      + line("Request timeout", `${setting("timeouts", "request")} s`);
  }
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
    headers: { "Content-Type": "application/json", ...(session() ? { "X-Daedalus-Session": session() } : {}) },
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
  $("login").password.focus();
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
  const options = { hour: "2-digit", minute: "2-digit", ...(today ? {} : { weekday: "short" }) };
  return date.toLocaleString([], options);
}

// The catalog chip. A click starts a rebuild.
function catalogChip({ built, next, rebuilding }) {
  const last = rebuilding ? "rebuilding" : built ? shortTime(built) : "never";
  const following = next ? ` &middot; next <b>${esc(shortTime(next))}</b>` : " &middot; no schedule";
  return `<button type="button" class="chip rebuild" id="catalog-rebuild" ${rebuilding ? "disabled" : ""}
    title="${rebuilding ? "A catalog rebuild runs now" : "Rebuild the catalog now"}">Catalog <b>${esc(last)}</b>${following}</button>`;
}

async function rebuildCatalog() {
  if (!confirm("Rebuild the catalog now? Daedalus gets the model list of each provider again.")) return;
  try {
    await call("catalog", { method: "POST" });
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    alert(error.message);
  }
  guarded(refreshFast);
}

function renderStatus(status) {
  const chips = [
    `<span class="chip"><span class="dot${status.healthy ? "" : " off"}"></span>` +
      `<b>${status.healthy ? "Healthy" : "Down"}</b></span>`,
    `<span class="chip"><b>${status.models}</b> models</span>`,
    `<span class="chip" title="Conversations with a session model and a request in the last hour"><b>${status.sessions}</b> sessions</span>`,
    catalogChip(status.catalog),
  ];
  $("status").outerHTML = `<span id="status" class="chips">${chips.join("")}</span>`;
}

// The tier filter of the Models tab for each pool.
const POOL_TIERS = {
  "daedalus/auto": "All", "daedalus/sophos": "A", "daedalus/deinos": "B",
  "daedalus/koinos": "C", "daedalus/moros": "D",
};

// The mean weight of a pool. A model in a cooldown counts as 0.
function poolHealth(members) {
  if (!members.length) return null;
  const now = Date.now() / 1000;
  return members.reduce((sum, m) => sum + (m.cooldown > now ? 0 : m.weight), 0) / members.length;
}

function renderPools(pools) {
  $("pools").innerHTML = pools.map((pool) => {
    // The highest weight first. A tie keeps the chain order.
    const members = [...pool.members].sort((a, b) => b.weight - a.weight);
    const shown = members.slice(0, SHOWN).map((m) => `
      <div class="member" title="${esc(m.id)}${m.tier ? ` (${esc(m.tier)})` : ""}">
        <div class="name">${esc(m.id)}</div><div class="w">${m.weight.toFixed(2)}</div>
        ${weightBar(m.weight)}
      </div>`).join("");
    const filters = `tier=${POOL_TIERS[pool.name] || "All"}&mode=${pool.mode || "chat"}&sort=weight`;
    const rest = members.length > SHOWN
      ? `<a class="more" href="#/models?${filters}">+${members.length - SHOWN} more</a>` : "";
    const health = poolHealth(members);
    const bar = health === null ? "" : `<div class="health" title="Mean weight. A model in a cooldown counts as 0.">
      ${weightBar(health)}<span class="num">${health.toFixed(2)}</span></div>`;
    const context = pool.context
      ? ` <span class="ctx" title="The largest context of a pool model">${tokens(pool.context)}</span>` : "";
    return `<div class="card"><h3>${esc(pool.name)}${context}</h3>${bar}
      <div class="sub">${esc(POOL_NOTES[pool.name] || "")} &middot; ${members.length} models</div>
      ${shown || '<div class="more">No models</div>'}${rest}</div>`;
  }).join("");
}

const opened = new Set();
let shownRequests = "";

const seconds = (value) => value == null ? "" : `${value.toFixed(3)}s`;

// A cooldown that an attempt started, such as "cooldown 60s backoff".
const coolText = (c) => `cooldown ${timeLeft(Date.now() / 1000 + c.seconds) || "0s"} ${c.reason}`;

// The effort that went to one model, where null means that Daedalus dropped it.
const sentText = (a) => (a.effort == null ? "not sent" : a.effort);

// The effort that the client asked for, then the effort that went to the model that answered.
function effortCell(r) {
  const served = (r.attempts || []).filter((a) => a.result === "answered" && "effort" in a).pop();
  if (!served) return esc(r.effort || "-");
  const sent = sentText(served);
  if (sent === r.effort) return esc(sent);
  return `${esc(r.effort || "-")} <span class="from">to ${esc(sent)}</span>`;
}

// A request that the client closed shows "cancelled" in place of its status.
function statusText(r) {
  return r.cancelled ? "cancelled" : r.status;
}

function statusClass(r) {
  return r.cancelled ? "muted" : `s${String(r.status)[0]}`;
}

function chainText(r) {
  const head = [clock(r.at), r.model, statusText(r), r.effort && `effort=${r.effort}`, r.pool && `pool=${r.pool}`,
    r.routed && `from=${r.routed}`,
    `fallbacks=${r.fallbacks ?? 0}`, r.retry && `retry=${r.retry}`, r.loop && `loop=${r.loop}`].filter(Boolean).join(" ");
  const steps = (r.attempts || []).map((a, i) =>
    `${i + 1}. ${a.model} ${a.result} ${seconds(a.seconds)}`.trim() + ("effort" in a ? ` effort=${sentText(a)}` : "")
      + (a.cooldown ? ` ${coolText(a.cooldown)}` : "")
      + (a.error ? `\n   ${a.error}` : ""));
  return [head, ...steps].join("\n");
}

function chainRows(r) {
  const steps = (r.attempts || []).map((a, i) => `
    <li class="step ${a.result === "answered" ? "good" : "bad"}">
      <span class="num">${i + 1}.</span> <b>${esc(a.model)}</b>
      <span class="result">${esc(a.result)}</span> <span class="muted num">${seconds(a.seconds)}</span>
      ${"effort" in a ? `<span class="from">effort ${esc(sentText(a))}</span>` : ""}
      ${a.cooldown ? `<span class="from">${esc(coolText(a.cooldown))}</span>` : ""}
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return `<tr class="chain"><td colspan="10"><div class="chain-body">
    <div class="chain-head"><span class="muted">Fallback chain</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${steps ? `<ol>${steps}</ol>` : '<p class="muted">No attempt data for this request.</p>'}
  </div></td></tr>`;
}

// The input tokens: the provider count, or the Daedalus estimate with a ~ mark.
function inputCell(r) {
  const input = r.tokens?.input;
  if (typeof input !== "number") return "-";
  return `${r.tokens.estimate ? "~" : ""}${input.toLocaleString()}`;
}

// The stream time of a finished request: after the first token, or the total time without a stream.
function streamCell(r) {
  if (r.seconds == null) return "-";
  if (!r.stream) return seconds(r.seconds);
  return r.ttft ? seconds(Math.max(0, r.seconds - parseFloat(r.ttft))) : "-";
}

// A live request with local times, because the server sends ages and not clock times.
function liveRow(r) {
  const since = Date.now() - r.age * 1000;
  return { ...r, since, first: r.ttft == null ? null : since + r.ttft * 1000 };
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

function renderLive() {
  const rows = [...state.live.values()].sort((a, b) => b.since - a.since);
  $("live").innerHTML = rows.map((r) => `
    <tr class="live-row" data-live="${r.id}">
      <td class="num muted"><span class="pulse"></span>${clock(r.since / 1000)}</td>
      <td>${esc(r.model || r.path)}</td>
      <td class="hide-sm">${esc(r.effort || "-")}</td>
      <td class="hide-sm muted">${esc(r.pool || "-")}</td>
      <td>${r.via ? esc(r.via) : `<span class="muted">${r.trying ? `trying ${esc(r.trying)}` : "waiting"}</span>`}</td>
      <td class="status muted">live</td>
      <td class="hide-sm num muted">-</td>
      <td class="hide-sm num" data-clock="ttft"></td>
      <td class="hide-sm num" data-clock="stream"></td>
      <td class="hide-sm num muted">${r.fallbacks ?? "-"}</td>
    </tr>`).join("");
  tickLive();
}

// The live clocks: TTFT until the first token, then the stream time. Without a stream, both count the total.
function tickLive() {
  const now = Date.now();
  for (const row of $("live").children) {
    const r = state.live.get(Number(row.dataset.live));
    if (!r) continue;
    const ttft = ((r.first ?? now) - r.since) / 1000;
    const stream = !r.stream ? (now - r.since) / 1000 : r.first == null ? null : (now - r.first) / 1000;
    row.querySelector('[data-clock="ttft"]').textContent = `${ttft.toFixed(1)}s`;
    row.querySelector('[data-clock="stream"]').textContent = stream == null ? "-" : `${stream.toFixed(1)}s`;
  }
}

function renderRequests(rows) {
  const text = JSON.stringify(rows);
  const selected = getSelection();
  if (text === shownRequests) return;
  if (!selected.isCollapsed && $("requests").contains(selected.anchorNode)) return;
  shownRequests = text;
  $("requests").innerHTML = rows.length ? rows.map((r) => `
    <tr class="request${opened.has(String(r.at)) ? " open" : ""}" data-at="${r.at}" title="Show the fallback chain">
      <td class="num muted"><span class="caret"></span>${clock(r.at)}</td>
      <td>${esc(r.model || "-")}</td>
      <td class="hide-sm">${effortCell(r)}</td>
      <td class="hide-sm muted">${esc(r.pool || "-")}${r.routed ? ` <span class="from">from ${esc(r.routed)}</span>` : ""}${r.retry ? ` <span class="from">try again ${esc(r.retry)}</span>` : ""}${r.loop ? ` <span class="from">tool loop ${esc(r.loop)}</span>` : ""}</td>
      <td>${r.via ? esc(r.via) : '<span class="muted">none</span>'}</td>
      <td class="status ${statusClass(r)}">${statusText(r)}</td>
      <td class="hide-sm num">${inputCell(r)}</td>
      <td class="hide-sm num">${esc(r.ttft || "-")}</td>
      <td class="hide-sm num">${streamCell(r)}</td>
      <td class="hide-sm num">${esc(r.fallbacks ?? "-")}</td>
    </tr>${opened.has(String(r.at)) ? chainRows(r) : ""}`).join("")
    : '<tr><td colspan="10" class="empty">No requests</td></tr>';
}

// The label of each catalog mode.
const MODES = {
  chat: "Chat", embedding: "Embedding", audio_transcription: "Transcription",
  audio_speech: "Speech", image_generation: "Image",
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
  const query = $("search").value.trim().toLowerCase();
  const rows = sortModels(state.models.filter((m) =>
    (state.tier === "All" || tierLetter(m.tier) === state.tier)
    && (state.mode === "all" || m.mode === state.mode || m.flags.includes(state.mode))
    && m.id.toLowerCase().includes(query)));
  const empty = state.models.length ? "No models match" : "No models. Run daedalus catalog.";
  $("models").innerHTML = rows.length ? rows.map((m) => `
    <tr>
      <td>${esc(m.id)}</td>
      <td class="hide-sm"><div class="types">${typeChips(m)}</div></td>
      <td class="mid">${m.tier ? `<span class="tier">${esc(tierLetter(m.tier))}</span>` : dash}</td>
      <td class="hide-sm num muted">${tokens(m.max_input_tokens)}</td>
      <td class="mid">${m.mode === "chat" ? yesNo(m.tools) : dash}</td>
      <td class="hide-sm mid">${m.mode === "chat" ? reasoningCell(m) : dash}</td>
      <td class="num">${coolCells(m)}</td>
      <td>${m.weight == null ? dash
        : `<div class="weight">${weightBar(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div>`}</td>
    </tr>`).join("") : `<tr><td colspan="8" class="empty">${empty}</td></tr>`;
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
  [], { dateStyle: "medium", timeStyle: "short" });

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
const FORM_KEYS = ["api_key", "client_keys", "api_base", "api_type", "discovery_url", "discovery_match", "exclude", "tier", "models"];
// The keys that a model override sets but the provider level does not.
const MODEL_ONLY = ["pool", "timeout"];
const TIERS = ["TIER-A", "TIER-B", "TIER-C", "TIER-D"];
// The width of 1 column of provider cards.
const CARD_WIDTH = 460;

const clone = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
const shown = (value) => (value !== null && typeof value === "object" ? JSON.stringify(value) : String(value));

// A typed value from the text of a chip: true, false, a number or a string.
function parsed(text) {
  const value = text.trim();
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
  const text = (key, label, hint) => field(label, hint, `<input class="text" type="text" spellcheck="false"
    data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(block[key] ?? "")}" placeholder="${esc(defaults[key] ?? "")}">`);
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
  return `<div class="card provider" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
    ${text("api_key", "API key", "os.environ/NAME reads an environment variable")}
    ${field("Client keys", "Daedalus key name = provider key. That client uses this key, with its own cooldowns and rpm counts.", `<div class="pills">${mapPills(block.client_keys, [...path, "client_keys"], "match", " = ")}</div>`)}
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
  const choices = kind === "column" ? state.overrideKeys.filter((key) => !MODEL_ONLY.includes(key)) : state.overrideKeys;
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
  if (input.value.trim()) parent[key] = input.value.trim();
  else delete parent[key];
  renderFiles();
}

async function saveForm() {
  if (!dirty()) return;
  const index = state.file;
  const file = state.files[index];
  const message = $("save-message");
  try {
    await call("providers", { method: "PUT", body: JSON.stringify({ path: file.path, blocks: pruned(state.forms[index]) }) });
    await takeFile(index);
    message.className = "message ok";
    message.textContent = "Saved and reloaded";
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
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin("The session ended. Log in to save again.");
    message.className = "message bad";
    message.textContent = error.message;
  }
  renderFiles();
}

const save = () => (state.view === "form" ? saveForm() : saveYaml());

// The other view shows the saved file. Unsaved changes go after a confirmation.
function switchView(view) {
  if (view === state.view) return;
  if (dirty() && !confirm("Discard the unsaved changes of this file?")) return;
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
    ["idle", "Idle expiry", "s", "The session model expires after this time without a request."],
    ["stay", "Stay share", "", "The share of first-tier draws for the session model. Below 1."],
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
  ]],
];

// The Settings cards of each column, from top to bottom.
const SETTINGS_COLUMNS = [["timeouts", "catalog", "pacing"], ["session_affinity", "headroom", "cooldown", "dashboard"], ["weights", "escalation", "switch"]];
const THEMES = [["system", "System"], ["light", "Light"], ["dark", "Dark"]];

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
  "session_affinity.stay", "weights.success", "weights.fault", "weights.slow", "weights.hourly", "weights.rate_limit",
]);
// The highest value of a field, when it has one.
const MAXIMA = { "catalog.anchor": 23 };

// The value in the file, or null when the file does not set it.
const fileValue = (group, key) => state.settings.file?.[group]?.[key] ?? null;
const setting = (group, key) => fileValue(group, key) ?? state.settings.defaults[group][key];

// The value after 1 wheel step. A decimal field steps its last decimal digit.
function wheelStep(input, direction) {
  const text = input.value || input.placeholder;
  const places = input.step === "any" ? Math.max((text.split(".")[1] || "").length, 1) : 0;
  const size = 10 ** -places;
  const max = input.max === "" ? Infinity : Number(input.max);
  const next = Math.min(max, Math.max(0, (Number(text) || 0) + direction * size));
  return next.toFixed(places);
}

function renderSettings() {
  $("settings-path").textContent = `${fileName(state.settings.path)} · Ctrl+S saves and reloads`;
  const card = ([group, title, fields]) => `
    <div class="card"><h3>${esc(title)}</h3>${fields.map(([key, label, unit, hint]) => {
      const id = `set-${group}-${key}`;
      if (key === "enabled") {
        return `<label class="field check" for="${id}"><input type="checkbox" id="${id}"
          ${setting(group, key) ? "checked" : ""}><span><b>${esc(label)}</b><small>${esc(hint)}</small></span></label>`;
      }
      if (unit === "choice") {
        return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <select id="${id}">${THEMES.map(([name, text]) => `<option value="${name}"${setting(group, key) === name ? " selected" : ""}>${text}</option>`).join("")}</select></label>`;
      }
      if (unit === "list") {
        return `<label class="field stack" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
          <textarea id="${id}" rows="8" spellcheck="false" placeholder="No keywords">${esc(setting(group, key).join("\n"))}</textarea></label>`;
      }
      const fallback = state.settings.defaults[group][key];
      const value = fileValue(group, key);
      return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
        <span class="input"><input type="number" min="0" id="${id}" value="${value ?? ""}"
          step="${DECIMALS.has(`${group}.${key}`) ? "any" : "1"}" ${MAXIMA[`${group}.${key}`] ? `max="${MAXIMA[`${group}.${key}`]}"` : ""}
          placeholder="${fallback ?? "half of Wait"}"><i>${esc(unit)}</i></span></label>`;
    }).join("")}</div>`;
  const cards = Object.fromEntries(SETTINGS.map((item) => [item[0], card(item)]));
  $("settings").innerHTML = SETTINGS_COLUMNS
    .map((groups) => `<div class="column">${groups.map((group) => cards[group]).join("")}</div>`)
    .join("");
  renderSettingsSave();
}

function settingsChanges() {
  if (!state.settings) return {};
  const changes = {};
  for (const [group, , fields] of SETTINGS) {
    for (const [key, , unit] of fields) {
      const input = $(`set-${group}-${key}`);
      let value, before;
      if (unit === "list") {
        value = input.value.split("\n").map((text) => text.trim()).filter(Boolean);
        if (JSON.stringify(value) !== JSON.stringify(setting(group, key))) (changes[group] ||= {})[key] = value;
        continue;
      }
      if (key === "enabled") {
        value = input.checked;
        before = setting(group, key);
      } else if (unit === "choice") {
        value = input.value;
        before = setting(group, key);
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
  $("settings-editor").value = state.settings.text;
  renderSettings();
}

// The other view shows the saved file. Unsaved changes go after a confirmation.
function switchSettingsView(view) {
  if (view === state.settingsView) return;
  if (settingsDirty() && !confirm("Discard the unsaved settings changes?")) return;
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
  state.requests = requests;
  renderRequests(requests);
  $("more-requests").hidden = requests.length < state.requestLimit || state.requestLimit >= REQUESTS_KEPT;
  // After a rebuild, the pools and models change too.
  if (state.catalog.rebuilding && !status.catalog?.rebuilding) guarded(refreshSlow);
  state.catalog = status.catalog || {};
  renderOverview();
}

async function refreshSlow() {
  const [pools, models] = await Promise.all([call("pools"), call("models"), refreshKeys()]);
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
  await refreshFast();
  await refreshSlow();
  takeFiles(await call("files"));
  [state.overrideKeys, state.providerDefaults] = await Promise.all([call("provider-keys"), call("provider-defaults")]);
  state.file = 0;
  $("editor").value = state.files[0]?.text ?? "";
  showFileError(0);
  renderFiles();
  await loadSettings();
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
    await guarded(start);
  } catch (error) {
    $("login-message").textContent = error.message;
  }
});

document.addEventListener("click", (event) => {
  if (event.target.closest("#catalog-rebuild")) rebuildCatalog();
});
$("reset-weights").addEventListener("click", async () => {
  const message = $("reset-message");
  message.textContent = "";
  if (!confirm("Set all weights back to 1 and end all cooldowns?")) return;
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
$("logout").addEventListener("click", async () => {
  await call("logout", { method: "POST" }).catch(() => {});
  keepSession(null);
  showLogin();
});

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
  if (!confirm(`Delete the key "${name}"? Clients with it get 401 at once.`)) return;
  await guarded(async () => {
    await call("keys/" + encodeURIComponent(name), { method: "DELETE" });
    await refreshKeys();
  });
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
  const row = event.target.closest("tr.request");
  if (!row || !getSelection().isCollapsed) return;
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
const PAGES = ["overview", "pools", "requests", "models", "keys", "providers", "settings"];

// A Models link such as #/models?tier=C&mode=chat&sort=weight sets the filters.
function applyModelFilters(query) {
  const params = new URLSearchParams(query);
  state.tier = ["All", "A", "B", "C", "D"].includes(params.get("tier")) ? params.get("tier") : "All";
  state.mode = MODES[params.get("mode")] || FLAGS[params.get("mode")] ? params.get("mode") : "all";
  state.sort = sortValue[params.get("sort")] ? { key: params.get("sort"), dir: -1 } : { key: "", dir: 1 };
  $("search").value = "";
  renderTiers();
  renderSortHeads();
  if (state.models.length) renderModels();
  history.replaceState(null, "", "#/models");
}

function showPage() {
  const [path, query] = location.hash.split("?");
  const asked = path.replace("#/", "").replace(/^config$/, "providers");
  if (asked === "models" && query !== undefined) applyModelFilters(query);
  const page = PAGES.includes(asked) ? asked : PAGES[0];
  document.querySelectorAll("section[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  document.querySelectorAll("#nav a").forEach((link) => {
    link.classList.toggle("on", link.dataset.page === page);
  });
  document.title = `Daedalus · ${document.querySelector(`#nav a[data-page="${page}"]`).textContent}`;
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
  if (!file || !confirm(`Delete ${file.path}?`)) return;
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
  const host = $("provider-form");
  if (!host.hidden && host.clientWidth && Number(host.dataset.columns) !== formColumns()) renderForm();
});
$("settings").addEventListener("input", () => {
  $("settings-message").textContent = "";
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
$("settings").addEventListener("wheel", (event) => {
  const input = event.target.closest("input[type=number]");
  if (!input || input !== document.activeElement) return;
  event.preventDefault();
  input.value = wheelStep(input, event.deltaY < 0 ? 1 : -1);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}, { passive: false });
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

guarded(start);
