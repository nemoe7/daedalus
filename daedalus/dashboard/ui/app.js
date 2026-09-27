"use strict";

const POOL_NOTES = {
  "daedalus/auto": "Picks a pool from the prompt",
  "daedalus/sophos": "Tier A",
  "daedalus/deinos": "Tier B",
  "daedalus/koinos": "Tier C",
  "daedalus/moros": "Tier D",
};
const SHOWN = 4;
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const tierLetter = (name) => (name || "").replace("TIER-", "") || "-";
const tokens = (n) => !n ? "-" : n >= 1e6 ? +(n / 1e6).toFixed(1) + "M" : Math.round(n / 1024) + "k";
const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString(
  [], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const state = {
  models: [], tier: "All", sort: { key: "", dir: 1 }, files: [], file: 0, saved: [], timers: [],
  pools: [], requests: [], keys: [], catalog: {}, settings: null,
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
  $("ov-requests").innerHTML = state.requests.slice(0, 5).map((r) => line(
    `<span class="status s${String(r.status)[0]}">${r.status}</span> ${esc(r.via || r.model || "-")}`,
    clock(r.at),
  )).join("") || none("No chat requests since the start");
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

function renderPools(pools) {
  $("pools").innerHTML = pools.map((pool) => {
    const members = pool.members;
    const shown = members.slice(0, SHOWN).map((m) => `
      <div class="member" title="${esc(m.id)} (${esc(m.tier)})">
        <div class="name">${esc(m.id)}</div><div class="w">${m.weight.toFixed(2)}</div>
        ${weightBar(m.weight)}
      </div>`).join("");
    const rest = members.length > SHOWN ? `<div class="more">+${members.length - SHOWN} more</div>` : "";
    return `<div class="card"><h3>${esc(pool.name)}</h3>
      <div class="sub">${esc(POOL_NOTES[pool.name] || "")} &middot; ${members.length} models</div>
      ${shown || '<div class="more">No models</div>'}${rest}</div>`;
  }).join("");
}

const opened = new Set();
let shownRequests = "";

const seconds = (value) => value == null ? "" : `${value.toFixed(3)}s`;

function chainText(r) {
  const head = [clock(r.at), r.model, r.status, r.pool && `pool=${r.pool}`, r.routed && `from=${r.routed}`,
    `fallbacks=${r.fallbacks ?? 0}`].filter(Boolean).join(" ");
  const steps = (r.attempts || []).map((a, i) =>
    `${i + 1}. ${a.model} ${a.result} ${seconds(a.seconds)}`.trim() + (a.error ? `\n   ${a.error}` : ""));
  return [head, ...steps].join("\n");
}

function chainRows(r) {
  const steps = (r.attempts || []).map((a, i) => `
    <li class="step ${a.result === "answered" ? "good" : "bad"}">
      <span class="num">${i + 1}.</span> <b>${esc(a.model)}</b>
      <span class="result">${esc(a.result)}</span> <span class="muted num">${seconds(a.seconds)}</span>
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return `<tr class="chain"><td colspan="7"><div class="chain-body">
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
      <td class="hide-sm muted">${esc(r.pool || "-")}${r.routed ? ` <span class="from">from ${esc(r.routed)}</span>` : ""}</td>
      <td>${r.via ? esc(r.via) : '<span class="muted">none</span>'}</td>
      <td class="status s${String(r.status)[0]}">${r.status}</td>
      <td class="hide-sm num">${esc(r.ttft || "-")}</td>
      <td class="hide-sm num">${esc(r.fallbacks ?? "-")}</td>
    </tr>${opened.has(String(r.at)) ? chainRows(r) : ""}`).join("")
    : '<tr><td colspan="7" class="empty">No chat requests since the start</td></tr>';
}

function renderTiers() {
  $("tiers").innerHTML = ["All", "A", "B", "C", "D"].map((t) =>
    `<button type="button" class="filter${t === state.tier ? " on" : ""}" data-tier="${t}">${t}</button>`,
  ).join(" ");
}

// The sort value of each column. Null goes last in both directions.
const sortValue = {
  id: (m) => m.id.toLowerCase(),
  tier: (m) => (tierLetter(m.tier) === "-" ? null : tierLetter(m.tier)),
  context: (m) => m.max_input_tokens ?? null,
  tools: (m) => (m.tools ? 1 : 0),
  reasoning: (m) => (m.reasoning ? 1 : 0),
  weight: (m) => m.weight,
};

const yesNo = (on) => (on ? '<span class="yes">Yes</span>' : '<span class="muted">No</span>');
const weightBar = (weight) => `<div class="track"><div class="fill${weight < 0.5 ? " low" : ""}"
  style="width:${Math.round(weight * 100)}%"></div></div>`;

function sortModels(rows) {
  const { key, dir } = state.sort;
  if (!sortValue[key]) return rows;
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

function renderModels() {
  const query = $("search").value.trim().toLowerCase();
  const rows = sortModels(state.models.filter((m) =>
    (state.tier === "All" || tierLetter(m.tier) === state.tier) && m.id.toLowerCase().includes(query)));
  const empty = state.models.length ? "No models match" : "No models. Run daedalus catalog.";
  $("models").innerHTML = rows.length ? rows.map((m) => `
    <tr>
      <td>${esc(m.id)}</td>
      <td><span class="tier">${esc(tierLetter(m.tier))}</span></td>
      <td class="hide-sm num muted">${tokens(m.max_input_tokens)}</td>
      <td>${yesNo(m.tools)}</td>
      <td>${yesNo(m.reasoning)}</td>
      <td><div class="weight">${weightBar(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">${empty}</td></tr>`;
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
  ]],
  ["catalog", "Catalog", [
    ["every", "Rebuild interval", "h", "The hours between rebuilds. 0 stops them."],
    ["anchor", "Anchor hour", "h", "The local hour (TZ) that the rebuild times start from."],
  ]],
  ["headroom", "Headroom", [
    ["timeout", "Timeout", "s", "After this time, the original messages go to the provider."],
  ]],
];

// The value in the file, or null when the file does not set it.
const fileValue = (group, key) => state.settings.file?.[group]?.[key] ?? null;
const setting = (group, key) => fileValue(group, key) ?? state.settings.defaults[group][key];

function renderSettings() {
  $("settings-path").textContent = `${fileName(state.settings.path)} · Ctrl+S saves and reloads`;
  $("settings").innerHTML = SETTINGS.map(([group, title, fields]) => `
    <div class="card"><h3>${esc(title)}</h3>${fields.map(([key, label, unit, hint]) => {
      const id = `set-${group}-${key}`;
      if (key === "enabled") {
        return `<label class="field check" for="${id}"><input type="checkbox" id="${id}"
          ${setting(group, key) ? "checked" : ""}><span><b>${esc(label)}</b><small>${esc(hint)}</small></span></label>`;
      }
      const fallback = state.settings.defaults[group][key];
      const value = fileValue(group, key);
      return `<label class="field" for="${id}"><span><b>${esc(label)}</b><small>${esc(hint)}</small></span>
        <span class="input"><input type="number" step="any" min="0" id="${id}" value="${value ?? ""}"
          placeholder="${fallback ?? "half of Wait"}"><i>${esc(unit)}</i></span></label>`;
    }).join("")}</div>`).join("");
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
  const [status, requests] = await Promise.all([call("status"), call("requests")]);
  renderStatus(status);
  state.requests = requests;
  renderRequests(requests);
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

function showPage() {
  const asked = location.hash.replace("#/", "").replace(/^config$/, "providers");
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
$("editor").addEventListener("input", renderFiles);
$("save").addEventListener("click", save);
$("settings").addEventListener("input", () => {
  $("settings-message").textContent = "";
  renderSettingsSave();
});
$("settings-save").addEventListener("click", saveSettings);
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
