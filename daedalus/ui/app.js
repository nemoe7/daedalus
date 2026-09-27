"use strict";

const POOL_NOTES = {
  "daedalus/auto": "Picks a pool from the prompt",
  "daedalus/praktos": "Tool-capable, tier A then B",
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

const state = { models: [], tier: "All", files: [], file: 0, saved: [], timers: [] };

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

function renderStatus(status) {
  const chips = [
    `<span class="chip"><span class="dot${status.healthy ? "" : " off"}"></span>` +
      `<b>${status.healthy ? "Healthy" : "Down"}</b></span>`,
    `<span class="chip"><b>${status.models}</b> models</span>`,
    `<span class="chip">Local key <b>${status.key ? "on" : "off"}</b></span>`,
    `<span class="chip"><b>${status.sessions}</b> sessions</span>`,
  ];
  $("status").outerHTML = `<span id="status" class="chips">${chips.join("")}</span>`;
}

function renderPools(pools) {
  $("pools").innerHTML = pools.map((pool) => {
    const members = pool.members;
    const shown = members.slice(0, SHOWN).map((m) => `
      <div class="member" title="${esc(m.id)} (${esc(m.tier)})">
        <div class="name">${esc(m.id)}</div><div class="w">${m.weight.toFixed(2)}</div>
        <div class="track"><div class="fill${m.weight < 0.5 ? " low" : ""}"
          style="width:${Math.round(m.weight * 100)}%"></div></div>
      </div>`).join("");
    const rest = members.length > SHOWN ? `<div class="more">+${members.length - SHOWN} more</div>` : "";
    return `<div class="card"><h3>${esc(pool.name)}</h3>
      <div class="sub">${esc(POOL_NOTES[pool.name] || "")} &middot; ${members.length} models</div>
      ${shown || '<div class="more">No models</div>'}${rest}</div>`;
  }).join("");
}

function renderRequests(rows) {
  $("requests").innerHTML = rows.length ? rows.map((r) => `
    <tr>
      <td class="num muted">${clock(r.at)}</td>
      <td>${esc(r.model || "-")}</td>
      <td class="hide-sm muted">${esc(r.pool || "-")}</td>
      <td>${r.via ? esc(r.via) : '<span class="muted">none</span>'}</td>
      <td class="status s${String(r.status)[0]}">${r.status}</td>
      <td class="hide-sm num">${esc(r.ttft || "-")}</td>
      <td class="hide-sm num">${esc(r.fallbacks ?? "-")}</td>
    </tr>`).join("") : '<tr><td colspan="7" class="empty">No chat requests since the start</td></tr>';
}

function renderTiers() {
  $("tiers").innerHTML = ["All", "A", "B", "C", "D"].map((t) =>
    `<button type="button" class="filter${t === state.tier ? " on" : ""}" data-tier="${t}">${t}</button>`,
  ).join(" ");
}

function renderModels() {
  const query = $("search").value.trim().toLowerCase();
  const rows = state.models.filter((m) =>
    (state.tier === "All" || tierLetter(m.tier) === state.tier) && m.id.toLowerCase().includes(query));
  const empty = state.models.length ? "No models match" : "No models. Run daedalus catalog.";
  $("models").innerHTML = rows.length ? rows.map((m) => `
    <tr>
      <td>${esc(m.id)}</td>
      <td><span class="tier">${esc(tierLetter(m.tier))}</span></td>
      <td class="hide-sm num muted">${tokens(m.max_input_tokens)}</td>
      <td>${m.tools ? '<span class="yes">Yes</span>' : '<span class="muted">No</span>'}</td>
      <td class="num">${m.weight.toFixed(2)}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="empty">${empty}</td></tr>`;
}

function dirty() {
  return state.files.length > 0 && $("editor").value !== state.saved[state.file];
}

function renderFiles() {
  $("files").innerHTML = state.files.map((file, index) => {
    const mark = index === state.file && dirty() ? " &bull;" : "";
    return `<button type="button" class="tab${index === state.file ? " on" : ""}"
      data-file="${index}">${esc(file.path)}${mark}</button>`;
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

async function refreshFast() {
  const [status, requests] = await Promise.all([call("status"), call("requests")]);
  renderStatus(status);
  renderRequests(requests);
}

async function refreshSlow() {
  const [pools, models] = await Promise.all([call("pools"), call("models")]);
  renderPools(pools);
  state.models = models;
  renderModels();
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
  renderTiers();
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

$("search").addEventListener("input", renderModels);
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
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty()) event.preventDefault();
});

guarded(start);
