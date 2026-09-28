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
  pools: [], requests: [], requestLimit: REQUESTS_STEP, keys: [], catalog: {}, settings: null,
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
    `<span class="status s${String(r.status)[0]}">${r.status}</span> ${esc(r.via || r.model || "-")}`,
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

function catalogChip({ built, next }) {
  const last = built ? shortTime(built) : "never";
  const following = next ? ` &middot; next <b>${esc(shortTime(next))}</b>` : " &middot; no schedule";
  return `<span class="chip">Catalog <b>${esc(last)}</b>${following}</span>`;
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
    return `<div class="card"><h3>${esc(pool.name)}</h3>${bar}
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

function chainText(r) {
  const head = [clock(r.at), r.model, r.status, r.effort && `effort=${r.effort}`, r.pool && `pool=${r.pool}`,
    r.routed && `from=${r.routed}`,
    `fallbacks=${r.fallbacks ?? 0}`, r.retry && `retry=${r.retry}`].filter(Boolean).join(" ");
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
  return `<tr class="chain"><td colspan="8"><div class="chain-body">
    <div class="chain-head"><span class="muted">Fallback chain</span>
      <button class="ghost copy-chain" type="button" data-at="${r.at}">Copy</button></div>
    ${steps ? `<ol>${steps}</ol>` : '<p class="muted">No attempt data for this request.</p>'}
  </div></td></tr>`;
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
      <td class="hide-sm muted">${esc(r.pool || "-")}${r.routed ? ` <span class="from">from ${esc(r.routed)}</span>` : ""}${r.retry ? ` <span class="from">try again ${esc(r.retry)}</span>` : ""}</td>
      <td>${r.via ? esc(r.via) : '<span class="muted">none</span>'}</td>
      <td class="status s${String(r.status)[0]}">${r.status}${(r.attempts || []).some((a) => a.cooldown) ? ' <span class="from">cooldown</span>' : ""}</td>
      <td class="hide-sm num">${esc(r.ttft || "-")}</td>
      <td class="hide-sm num">${esc(r.fallbacks ?? "-")}</td>
    </tr>${opened.has(String(r.at)) ? chainRows(r) : ""}`).join("")
    : '<tr><td colspan="8" class="empty">No requests</td></tr>';
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

// The default reasoning effort of a model, when the catalog has one.
const effortText = (effort) => (effort ? ` <span class="muted">${esc(effort)}</span>` : "");
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
      <td class="hide-sm mid">${m.mode === "chat" ? yesNo(m.reasoning) + effortText(m.effort) : dash}</td>
      <td class="num">${coolCell(m.cooldown)}</td>
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

function dirty() {
  return state.files.length > 0 && $("editor").value !== state.saved[state.file];
}

function renderFiles() {
  $("files").innerHTML = state.files.map((file, index) => {
    const mark = index === state.file && dirty() ? " &bull;" : "";
    return `<button type="button" class="tab${index === state.file ? " on" : ""}"
      data-file="${index}" title="${esc(file.path)}">${esc(fileName(file.path))}${mark}</button>`;
  }).join(" ");
  $("save").disabled = !dirty();
  // The main provider file stays: only a {provider}.yml file can go.
  $("drop-provider").hidden = state.files[state.file]?.main !== false;
}

function openFile(index) {
  if (state.files.length) state.files[state.file].text = $("editor").value;
  state.file = index;
  $("editor").value = state.files[index].text;
  $("save-message").textContent = "";
  renderFiles();
}

async function save() {
  if (!dirty()) return;
  const file = state.files[state.file];
  const text = $("editor").value;
  const message = $("save-message");
  try {
    await call("files", { method: "PUT", body: JSON.stringify({ path: file.path, text }) });
    file.text = text;
    state.saved[state.file] = text;
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

// The Settings form: [group, title, [[key, label, unit, hint], ...]].
const SETTINGS = [
  ["timeouts", "Timeouts", [
    ["request", "Request", "s", "The time for one full request."],
    ["wait", "Wait", "s", "The time without bytes from the provider."],
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
];

// The Settings cards of each column, from top to bottom.
const SETTINGS_COLUMNS = [["timeouts", "catalog", "pacing"], ["session_affinity", "headroom", "cooldown"], ["weights"]];

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
    for (const [key] of fields) {
      const input = $(`set-${group}-${key}`);
      let value, before;
      if (key === "enabled") {
        value = input.checked;
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

const settingsDirty = () => Object.keys(settingsChanges()).length > 0;

function renderSettingsSave() {
  $("settings-save").disabled = !settingsDirty();
}

async function loadSettings() {
  state.settings = await call("settings");
  renderSettings();
}

async function saveSettings() {
  const changes = settingsChanges();
  if (!Object.keys(changes).length) return;
  const message = $("settings-message");
  try {
    await call("settings", { method: "PUT", body: JSON.stringify({ changes }) });
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
  state.files = await call("files");
  state.saved = state.files.map((file) => file.text);
  state.file = 0;
  $("editor").value = state.files[0]?.text ?? "";
  renderFiles();
  await loadSettings();
  renderTiers();
  renderOverview();
  $("login").hidden = true;
  $("app").hidden = false;
  state.timers = [
    setInterval(() => guarded(refreshFast), 5000),
    setInterval(() => guarded(refreshSlow), 15000),
    setInterval(tickCooldowns, 1000),
  ];
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
  state.files = await call("files");
  state.saved = state.files.map((file) => file.text);
  const index = Math.max(0, state.files.findIndex((file) => file.path === keep));
  state.file = index;
  $("editor").value = state.files[index]?.text ?? "";
  $("save-message").textContent = "";
  renderFiles();
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
$("settings").addEventListener("input", () => {
  $("settings-message").textContent = "";
  renderSettingsSave();
});
$("settings-save").addEventListener("click", saveSettings);
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
window.addEventListener("beforeunload", (event) => {
  if (dirty() || settingsDirty()) event.preventDefault();
});

guarded(start);
