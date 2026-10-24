"use strict";

function t(key, values = {}) {
  const catalog = globalThis.DAEDALUS_LOCALE || {};
  const template = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : key;
  return String(template).replace(/\{([a-z0-9_]+)\}/gi, (match, name) => (
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
  ));
}
globalThis.t = t;

const applyLocale = (root = document) => {
  root.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  ["aria-label", "label", "placeholder", "title"].forEach((attribute) => {
    const dataAttribute = `data-i18n-${attribute}`;
    root.querySelectorAll(`[${dataAttribute}]`).forEach((node) => {
      node.setAttribute(attribute, t(node.getAttribute(dataAttribute)));
    });
  });
};

const POOL_NOTES = {
  "daedalus/auto": t("model.pool.auto_text"),
  "daedalus/sophos": t("model.pool.tier_a_text"),
  "daedalus/deinos": t("model.pool.tier_b_text"),
  "daedalus/koinos": t("model.pool.tier_c_text"),
  "daedalus/moros": t("model.pool.tier_d_text"),
  "daedalus/graphos": t("model.pool.graphos_text"),
  "daedalus/photos": t("model.pool.photos_text"),
};
// Load requests in batches of 50, up to the server's 500-row history limit.
const REQUESTS_PAGE = 10;
const REQUESTS_STEP = 50;
const REQUESTS_KEPT = 500;
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const htmlAttrs = (attrs = {}) => Object.entries(attrs).map(([name, value]) => {
  if (value === false || value == null) return "";
  return value === true ? ` ${name}` : ` ${name}="${esc(value)}"`;
}).join("");
const tierLetter = (name) => (name || "").replace("TIER-", "") || "-";
const tokens = (n) => !n ? "-" : n >= 1e6 ? +(n / 1e6).toFixed(1) + "M" : n < 1000 ? String(n) : Math.round(n / 1024) + "K";
// Use personalization.time_format: h23 for 24h and h12 for 12h.
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
const plural = (number, one, other, values = {}) =>
  t(number === 1 ? one : other, { count: number, ...values });

// Format relative time with localized labels.
function relative(seconds) {
  const minutes = (seconds * 1000 - Date.now()) / 60000;
  const ago = minutes <= 0;
  const size = ago ? Math.floor(-minutes) : Math.ceil(minutes);
  if (ago && size === 0) return t("time.just_now_text");
  const dayMinutes = 60 * 24;
  if (ago && size >= dayMinutes * 30) return t("time.long_ago_text");
  if (size < 60) {
    return ago
      ? plural(size, "time.minute_ago.one_text", "time.minute_ago.other_text")
      : plural(size, "time.minute_from_now.one_text", "time.minute_from_now.other_text");
  }
  if (ago && size >= dayMinutes) {
    return plural(Math.floor(size / dayMinutes), "time.day_ago.one_text", "time.day_ago.other_text");
  }
  const hours = ago ? Math.floor(size / 60) : Math.ceil(size / 60);
  return ago
    ? plural(hours, "time.hour_ago.one_text", "time.hour_ago.other_text")
    : plural(hours, "time.hour_from_now.one_text", "time.hour_from_now.other_text");
}

// Render relative time with an exact-time title, leaving layout to the caller.
function timeText(seconds, { tag = "span", variant = "" } = {}) {
  const attrs = htmlAttrs({ class: variant || null, title: stamp(seconds) });
  return `<${tag}${attrs}>${esc(relative(seconds))}</${tag}>`;
}

const state = {
  models: [], tier: "All", mode: "all", sort: { key: "", dir: 1 }, files: [], file: 0, fileOpen: false,
  providerReturnKey: "", saved: [], timers: [],
  view: "form", forms: [], formSaved: [], overrideKeys: [], providerDefaults: {}, settingsView: "form",
  pools: [], requests: [], requestPage: 1, requestLimit: REQUESTS_STEP, requestFetched: 0, keys: [], catalog: {},
  settings: null, settingsSearch: "", legendExtra: [], requestRace: false,
  requestSearch: "", requestStatus: "all", requestHours: 0, limitSearch: "",
  live: new Map(), livePaused: false, liveWaiting: new Set(), liveSeen: new Set(), liveCount: 0,
  source: null, env: [],
};

const fileName = (path) => path.split(/[\\/]/).pop();
function line(left, right, valueAttrs = {}) {
  const attrs = htmlAttrs(valueAttrs);
  const value = attrs ? `<span${attrs}>${right}</span>` : right;
  return `<div class="line"><span>${left}</span><span>${value}</span></div>`;
}
const count = (number, kind) => {
  if (kind === "commit") return plural(number, "update.count.commit.one_text", "update.count.commit.other_text");
  if (kind === "file") return plural(number, "hook.count.file.one_text", "hook.count.file.other_text");
  if (kind === "model") return plural(number, "model.count.one_text", "model.count.other_text");
  if (kind === "request") return plural(number, "request.count.one_text", "request.count.other_text");
  return t(kind, { count: number });
};
const none = (text) => `<div class="more">${text}</div>`;
function actionButton(content, { variant = "", type = "button", attrs = {} } = {}) {
  return `<button${variant ? ` class="${esc(variant)}"` : ""} type="${esc(type)}"${htmlAttrs(attrs)}>${content}</button>`;
}
// Model-name cells use ellipses for long text and keep the full name in the title.
const mobileLabel = (text) => `<span class="mobile-label" aria-hidden="true">${esc(text)}</span>`;
const nameCell = (text, shown = esc(text), label = t("model.column.model_column"), extra = "") =>
  `<td role="cell" class="name" title="${esc(text)}">${label ? mobileLabel(label) : ""}<span class="cell-value">${shown}</span>${extra}</td>`;
// Render `provider/developer/slug` without duplicate provider names, using text when no SVG matches.
const MARK_FILES = new Set([
  "anthropic", "baai", "black-forest-labs", "bytedance", "cloudflare", "cohere", "daedalus",
  "deepseek-ai",
  "dots-studio", "fish-audio", "gemini", "google", "groq", "ibm", "ibm-granite", "inception",
  "kilo", "liquid", "meta", "meta-llama", "microsoft", "mistral", "mistralai", "moonshotai",
  "myshell-ai", "nvidia", "openai", "openrouter", "pollinations", "poolside", "qwen", "runwayml",
  "stabilityai", "stepfun", "z-ai", "zai-org",
]);
const mark = (name, tint = false) => MARK_FILES.has(name)
  ? tint
    ? `<span class="mark tint" style="--mark:url(icons/${esc(name)}.svg)" aria-hidden="true"></span>`
    : `<img class="mark" src="ui/icons/${esc(name)}.svg" alt="" aria-hidden="true">`
  : `<span class="mark slug">${esc(name)}</span>`;
// `provider/dev/model` and `provider/model` both land. The developer is the part before the model,
// with a scope prefix such as `@cf/` dropped.
const parts = (id) => {
  const bits = String(id).split("/");
  const provider = bits.shift();
  const model = bits.length ? bits.pop() : null;
  let dev = bits.length ? bits[bits.length - 1] : null;
  if (dev && dev.toLowerCase() === provider.toLowerCase()) dev = null;
  return { provider, dev, model };
};
// Separate model-ID parts with a slash.
const sep = '<span class="mark-sep" aria-hidden="true">/</span>';
const modelName = (id, pool = "", tint = false) => {
  const { provider, dev, model } = parts(id);
  if (model === null) return esc(id);
  const heads = (dev ? [provider, dev] : [provider]).map((name) => mark(name, tint));
  const tail = pool ? sep + `<span class="model-part">${esc(pool)}</span>` : "";
  return heads.join(sep) + sep + `<span class="model-part">${esc(model)}</span>` + tail;
};

// Use the pool suffix in auto model slugs, such as daedalus/auto/moros.
// A full pool name such as daedalus/moros contributes moros.
const poolOf = (r) => {
  const pool = String(r.pool || "").replace(/^daedalus\//, "");
  return pool && String(r.model || "").startsWith("daedalus/auto") ? pool : "";
};
const cell = (label, inner, cls = "", attrs = "") =>
  `<td role="cell"${cls ? ` class="${cls}"` : ""}${attrs}>${mobileLabel(label)}<span class="cell-value">${inner}</span></td>`;

// Replace window.confirm with a modal that resolves true on Confirm, with an optional name field.
// Read modal-input after confirmation. Tests resolve the dialog through globalThis.__settle.
function ask(title, message, confirm, danger = false, placeholder = null, value = "") {
  return new Promise((resolve) => {
    globalThis.__settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    $("modal-message").hidden = !message;
    $("modal-field").hidden = placeholder === null;
    $("modal-field2").hidden = true;
    $("modal-choice-field").hidden = true;
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

// Resolve a two-field modal to true on Confirm.
// Then read the name from modal-input and its value from modal-input2.
function askPair(title, message, nameLabel, valueLabel, name = "", value = "", confirm = t("action.save_button")) {
  return new Promise((resolve) => {
    globalThis.__settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    $("modal-message").hidden = !message;
    $("modal-field").hidden = false;
    $("modal-field2").hidden = false;
    $("modal-choice-field").hidden = true;
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

// Display a select above a text field, and resolve true on Confirm.
// Then read modal-choice and modal-input2.
function askChoicePair(title, message, choices, placeholder) {
  return new Promise((resolve) => {
    globalThis.__settle = resolve;
    $("modal-title").textContent = title;
    $("modal-message").textContent = message;
    $("modal-message").hidden = !message;
    $("modal-field").hidden = true;
    $("modal-field2").hidden = false;
    $("modal-choice-field").hidden = false;
    const choice = $("modal-choice");
    choice.innerHTML = choices.map((key) => `<option value="${esc(key)}">${esc(key)}</option>`).join("");
    const box = $("modal-input2");
    box.value = "";
    box.placeholder = placeholder ?? "";
    const ok = $("modal-ok");
    ok.textContent = t("action.save_button");
    ok.classList.toggle("danger", false);
    $("modal").returnValue = "";
    $("modal").showModal();
    choice.focus?.();
  });
}

// Edit one row value through the modal.
// Save writes changes, while Cancel or Escape keeps the file unchanged.
async function editValue(input, write) {
  const box = $("modal-input");
  const number = input.type === "number" ? document.createElement("input") : null;
  if (number) {
    number.type = "number";
    number.min = input.min;
    number.max = input.max;
    number.step = input.step;
  }
  box.type = input.type === "password" ? "password" : "text";
  box.inputMode = number ? "decimal" : "";
  const validate = () => {
    if (!number) return;
    number.value = box.value;
    const bounded = number.value !== "" && Number.isFinite(number.valueAsNumber) && number.checkValidity();
    box.pattern = box.value === "" || bounded ? ".*" : "(?!)";
  };
  const done = ask(t("dialog.edit_value.title", { name: input.dataset.value }),
    "", t("action.save_button"), false, input.placeholder ?? "", input.value);
  validate();
  box.addEventListener("input", validate);
  const confirmed = await done;
  const valid = box.checkValidity();
  box.removeEventListener("input", validate);
  box.removeAttribute("pattern");
  box.inputMode = "";
  box.type = "text";
  if (!confirmed || !valid || box.value === input.value) return;
  input.value = box.value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  state.writeAnchor = anchorOf(input);
  const wanted = input.id && document.activeElement === input ? input.id : "";
  await write(input);
  const back = wanted && document.activeElement === document.body ? $(wanted) : null;
  if (back) back.focus();
}

// Open the row-value editor on a click.
function bindValueRows(host, write) {
  const activate = (event) => {
    const input = event.target.closest("[data-value]");
    if (!input || input.disabled) return;
    event.preventDefault();
    editValue(input, write);
  };
  host.addEventListener("click", activate);
  host.addEventListener("keydown", (event) => {
    if (!event.repeat && (event.key === "Enter" || event.key === " ")) activate(event);
  });
}

// Use the control's row as the write anchor so Undo stays inside it.
const anchorOf = (node) => node?.closest(".pills, .tier-row, .override, .input, .field") ?? node ?? null;

// An Overview metric, with its value above its label.
const kpi = (value, label) => `<div class="kpi"><b>${value}</b><small>${esc(label)}</small></div>`;

// One card for each page, from the data that the pages already read.
function renderOverview() {
  const failed = state.requests.filter((r) => ["s4", "s5"].includes(statusClass(r))).length;
  const answered = state.requests.length - failed;
  const share = state.requests.length ? `${Math.round((answered / state.requests.length) * 100)}%` : "-";
  const lanes = activeLimits();
  const left = lanes.length
    ? `${Math.round(Math.min(...lanes.map((r) => (r.limit > 0 ? r.remaining / r.limit : 1))) * 100)}%`
    : "-";
  draw("ov-kpis", [
    kpi(state.live.size, t("overview.kpi.in_flight_label")),
    kpi(share, t("overview.kpi.success_label", { requests: count(state.requests.length, "request") })),
    kpi(left, t("overview.kpi.limit_left_label")),
    kpi(state.models.length || 0, t("overview.kpi.catalog_models_label")),
  ].join(""));
  draw("ov-requests", state.requests.slice(0, 5).map((r) => line(
    `<span class="status ${statusClass(r)}">${statusCell(r)}</span> <span title="${esc(r.via || r.model || "")}">${r.via || r.model ? modelName(r.via || r.model) : "-"}</span>`,
    timeText(r.at),
  )).join("") || none(t("overview.empty.requests_text")));
  $("ov-model-count").textContent = state.models.length || "";
  // Each pool with its mean weight and the model that served it most.
  const served = {};
  for (const r of state.requests) served[r.via || r.model] = (served[r.via || r.model] || 0) + 1;
  draw("ov-models", state.pools.map((pool) => {
    const top = topModel(pool.members, served);
    const health = poolHealth(pool.members);
    return `<div class="pool-line"><div class="line"><span>${esc(pool.shown.replace("daedalus/", ""))}</span>
      <span title="${esc(top?.id)}">${top ? modelName(top.id) : t("model.empty.pool_text")}</span></div>${health === null ? "" : progressMeter(health)}</div>`;
  }).join("") || none(state.models.length ? t("model.pool.no_pools_text") : t("model.empty.catalog_text")));
  draw("ov-limits", overviewLimits(state.limits) || none(t("overview.empty.limits_text")));
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

// Display the header spinner after a 300 ms delay while calls remain active.
// Fast calls finish before the spinner appears.
let calling = 0;
let spinnerTimer = null;
function showSpinner(on) {
  const ring = $("spinner");
  if (on) ring.classList.add("on");
  else ring.classList.remove("on");
}

async function call(path, options = {}) {
  const { spinner = true, ...fetchOptions } = options;
  if (spinner) {
    calling += 1;
    if (calling === 1) spinnerTimer = setTimeout(() => showSpinner(true), 300);
  }
  try {
    let response;
    try {
      response = await fetch("ui/api/" + path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(session() ? { "x-daedalus-session": session() } : {}) },
        ...fetchOptions,
      });
    } catch {
      // Replace browser fetch errors with the dashboard connection error.
      throw new Error(t("error.dashboard_unreachable_text"));
    }
    if (response.status === 401 && path !== "login") {
      keepSession(null);
      throw new LoggedOut();
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const key = body.error?.locale_key;
      const known = typeof key === "string" && Object.prototype.hasOwnProperty.call(globalThis.DAEDALUS_LOCALE || {}, key);
      const message = known ? t(key, body.error.locale_params || {}) : body.error?.message;
      throw new Error(message || t("error.http_without_message", { status: response.status }));
    }
    return body;
  } finally {
    if (spinner) {
      calling -= 1;
      if (!calling) {
        clearTimeout(spinnerTimer);
        showSpinner(false);
      }
    }
  }
}

// Draw placeholder rows during the first load.
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

// Read the default username and master key hint when the environment does not set them.
async function loginHints() {
  const hints = await call("login").catch(() => ({}));
  const form = $("login");
  if (!form.username.value && hints.username) form.username.value = hints.username;
  $("login-hint").hidden = !hints.master;
  $("login-version").textContent = hints.version || "";
  form[form.username.value ? "password" : "username"].focus();
}

// Display the password until the next click or login.
function showPassword(on) {
  const button = $("show-password");
  const label = on ? t("auth.password.hide_label") : t("auth.password.show_label");
  $("login").password.type = on ? "text" : "password";
  button.setAttribute("aria-pressed", String(on));
  button.setAttribute("aria-label", label);
  button.title = label;
}

// A read failure stays visible until the user closes it. A retry keeps the failure text.
function showNotice(message) {
  $("notice-text").textContent = message || t("error.dashboard_unreachable_text");
  const note = $("notice");
  note.classList.remove("leave");
  note.hidden = false;
}

function closeNotice() {
  const note = $("notice");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    note.classList.remove("leave");
    note.hidden = true;
  } else {
    note.classList.add("leave");
  }
}

async function guarded(task) {
  try {
    await task();
  } catch (error) {
    if (error instanceof LoggedOut) showLogin();
    else {
      showNotice(error.message);
      console.error(error);
    }
  }
}

async function rebuildCatalog() {
  // Count the current providers before the rebuild reads their model lists again.
  const providers = new Set(state.models.map((row) => row.provider)).size;
  const go = await ask(t("catalog.action.title"), t("catalog.action.hint", { count: providers }), t("action.rebuild_button"));
  if (!go) return;
  try {
    await call("catalog", { method: "POST" });
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    showNotice(error.message);
  }
  guarded(refreshFast);
}

// The phone status card has a health row and one row per value.
// Its catalog button starts a rebuild.
function renderStatusCard(status) {
  const { built, next, rebuilding } = status.catalog || {};
  const last = rebuilding ? t("catalog.rebuilding_text") : built ? relative(built) : t("catalog.never_text");
  const following = next
    ? t("catalog.next_text", { time: esc(relative(next)) })
    : t("catalog.no_schedule_text");
  const label = rebuilding ? t("catalog.rebuild.running_title") : t("catalog.rebuild.start_title");
  $("card-health").innerHTML =
    `<span class="dot${status.healthy ? "" : " off"}"></span>${status.healthy ? t("state.healthy_text") : t("state.down_text")}`;
  const affinity = status.affinity;
  const affinityLine = affinity?.mode && affinity.mode !== "none"
    ? line(t("common.affinity_label"), affinity.mode === "race"
        ? t("status.affinity.race_text", {
            mode: t("settings.choice.race_option"), count: affinity.count,
            chance: Math.round(affinity.chance * 100), slow: affinity.slow,
          })
        : t("settings.choice.session_option"))
    : "";
  $("card-rows").innerHTML = line(t("common.sessions_label"), status.sessions) + affinityLine
    + actionButton(`<span>${t("catalog.label")}</span><span><b>${esc(last)}</b>${following}</span>`, {
      variant: "line rebuild",
      attrs: { "data-catalog-rebuild": true, title: label, disabled: rebuilding },
    });
}

// Track status availability. Retain the last data and display a chip after a failed request.
let stateKnown = null;

function stateChip() {
  if (stateKnown === null) return `<span class="chip" id="state-loading">${t("state.loading_text")}</span>`;
  if (stateKnown) return "";
  return `<span class="chip bad" id="state-unknown" title="${t("state.unknown.hint")}">${t("state.unknown_text")}</span>`;
}

// The Overview card contains health, sessions, and catalog data. The header displays the state.
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
  markNavEdges();
}

// Filter Models by each pool's tier. Other pools display all tiers.
const POOL_TIERS = {
  "daedalus/auto": "All", "daedalus/sophos": "A", "daedalus/deinos": "B",
  "daedalus/koinos": "C", "daedalus/moros": "D",
};

// Select the member with the most requests in the page history.
// Use the first catalog member if none have requests.
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

// The Models tab filters of a pool.
const poolFilter = (pool) => ({ tier: POOL_TIERS[pool.name] || "All", mode: pool.mode || "chat" });

// A pool card links to its filters. The card of the active filters is on, and its link clears them.
function markPools() {
  const mode = state.tier === "All" ? state.mode : "chat";
  document.querySelectorAll("#pools .pool").forEach((card) => {
    const on = card.dataset.tier === state.tier && card.dataset.mode === mode;
    card.classList.toggle("on", on);
    if (on) card.setAttribute("aria-current", "true");
    else card.removeAttribute("aria-current");
    card.href = on ? "#/models?tier=All&mode=all" : `#/models?tier=${card.dataset.tier}&mode=${card.dataset.mode}&sort=weight`;
  });
}

function renderPools(pools) {
  draw("pools", pools.map((pool) => {
    const { tier, mode } = poolFilter(pool);
    const health = poolHealth(pool.members);
    const bar = health === null ? "" : `<div class="health" title="${t("model.pool.mean_weight_title")}">
      ${progressMeter(health)}<span class="num">${health.toFixed(2)}</span></div>`;
    const context = pool.context
      ? ` <span class="ctx" title="${t("model.context.pool_hint", { count: pool.context.toLocaleString() })}">${tokens(pool.context)}</span>` : "";
    return `<a class="card pool" data-tier="${tier}" data-mode="${esc(mode)}" title="${esc(pool.shown)}: ${esc(POOL_NOTES[pool.name] || "")}">
      <h3>${esc(pool.shown.replace("daedalus/", ""))}${context}</h3>${bar}<div class="sub">${count(pool.members.length, "model")}</div></a>`;
  }).join(""));
  markPools();
}

let shownRequests = null;

const seconds = (value) => value == null ? "" : t("time.seconds.short_text", { value: value.toFixed(3) });

// A cooldown that an attempt started, such as "cooldown 60s backoff".
const coolText = (c) => t("request.chain.cooldown_text", {
  time: timeLeft(Date.now() / 1000 + c.seconds) || t("time.seconds.short_text", { value: 0 }),
  reason: c.reason,
});
const weightText = (a) => t("request.chain.weight_text", {
  from: a.weight_change.from.toFixed(2), to: a.weight_change.to.toFixed(2),
});

function sentText(a, asked) {
  if (a.effort == null) return t("request.effort.dropped_text");
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

// Display the requested effort, then the effort sent to the model that answered.
function effortCell(r) {
  const served = (r.attempts || []).filter((a) => a.result === "answered" && "effort" in a).pop();
  if (!served) return esc(r.effort || "-");
  const sent = sentText(served, r.effort);
  if (sent === r.effort) return esc(r.effort);
  return `${esc(r.effort || "-")} <span class="from">${esc(shortEffort(sent))}</span>`;
}

// Display cancelled instead of the status for a request that the client closed.
function statusText(r) {
  return r.cancelled ? t("request.state.cancelled_text") : r.status;
}

// Explain each status in the cell title.
const STATUS_NOTES = {
  200: t("request.status.ok_text"),
  429: t("request.status.rate_limited_text"),
  500: t("request.status.upstream_error_text"),
  err: t("request.status.failed_text"),
};

// One short state. Its caller decides the meaning, color, and surrounding layout.
function statusBadge(text, { variant = "", title = "" } = {}) {
  const attrs = htmlAttrs({ class: variant || null, title: title || null });
  return `<span${attrs}>${esc(text)}</span>`;
}

// Display status 499 for a request that the client closed.
function statusCell(r) {
  if (r.cancelled) return statusBadge(499, { title: t("request.status.cancelled_text") });
  const note = STATUS_NOTES[r.status];
  return note ? statusBadge(r.status, { title: note }) : esc(r.status);
}

function statusClass(r) {
  if (r.cancelled) return "muted";
  if (r.status === "err") return "s5";
  return `s${String(r.status)[0]}`;
}

function chainAttempts(r) {
  if ((r.attempts || []).length) return r.attempts;
  if (!r.cancelled && (r.status == null || r.status === 200)) return [];
  const result = r.cancelled
    ? t("request.state.cancelled_text")
    : STATUS_NOTES[r.status] || `HTTP ${r.status}`;
  return [{
    model: r.via || r.model || "-",
    result,
    seconds: r.seconds ?? 0,
    ...(r.error ? { error: r.error } : {}),
  }];
}

function chainText(r) {
  const head = [stamp(r.at), r.model, statusText(r),
    r.effort && t("request.chain.field.effort_text", { value: r.effort }),
    r.pool && t("request.chain.field.pool_text", { value: r.pool }),
    r.routed && t("request.chain.field.from_text", { value: r.routed }),
    t("request.chain.field.fallbacks_text", { value: r.fallbacks ?? 0 }),
    r.retry && t("request.chain.field.retry_text", { value: r.retry }),
    r.loop && t("request.chain.field.loop_text", { value: r.loop }),
  ].filter(Boolean).join(" ");
  const steps = chainAttempts(r).map((a, i) =>
    t("request.chain.attempt_text", {
      number: i + 1, model: a.model, result: a.result, time: seconds(a.seconds),
    }).trim() + ("effort" in a
      ? ` ${t("request.chain.field.effort_text", { value: sentText(a, r.effort) })}` : "")
      + (a.race === "won" ? ` ${t("request.chain.won_race_text")}` : "")
      + (a.weight_change ? ` ${weightText(a)}` : "")
      + (a.cooldown ? ` ${coolText(a.cooldown)}` : "")
      + (a.error ? `\n   ${a.error}` : ""));
  return [head, ...steps].join("\n");
}

// A chain is available for multiple attempts or any result other than answered.
const hasChain = (r) => {
  const attempts = chainAttempts(r);
  return attempts.length > 1 || attempts.some((a) => a.result !== "answered");
};

function chainSteps(r) {
  const steps = chainAttempts(r).map((a, i) => `
    <li class="step ${a.result === "answered" ? "good" : "bad"}">
      <span class="num">${i + 1}.</span> <b>${modelName(a.model)}</b>
      <span class="result">${esc(a.result)}</span> <span class="muted num">${seconds(a.seconds)}</span>
      ${"effort" in a ? `<span class="from">${t("request.chain.effort_text", { effort: esc(shortEffort(sentText(a, r.effort))) })}</span>` : ""}
      ${a.weight_change ? `<span class="from">${esc(weightText(a))}</span>` : ""}
      ${a.cooldown ? `<span class="from">${esc(coolText(a.cooldown))}</span>` : ""}
      ${a.race === "won" ? `<span class="from">${t("request.chain.won_race_text")}</span>` : ""}
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return `<ol>${steps}</ol>`;
}

// Explain race decisions with three-letter codes and titles.
// Display race notes only with active affinity.
const RACE_NOTES = {
  off: t("request.race.off.note"),
  pool: t("request.race.pool.note"),
  stream: t("request.race.stream.note"),
  single: t("request.race.single.note"),
  fast: t("request.race.fast.note"),
  slow: t("request.race.slow.note"),
  drawn: t("request.race.drawn.note"),
};
const RACE_CODES = {
  off: t("request.race.off.label"),
  pool: t("request.race.pool.label"),
  stream: t("request.race.stream.label"),
  single: t("request.race.single.label"),
  fast: t("request.race.fast.label"),
  slow: t("request.race.slow.label"),
  drawn: t("request.race.drawn.label"),
};
const raceNote = (r) => (state.status?.affinity?.enabled && RACE_CODES[r.race]
  ? `<p class="chain-race" title="${esc(RACE_NOTES[r.race])}">${esc(RACE_CODES[r.race])}</p>` : "");

function routingCodes(r) {
  const from = r.routed ? ` <span class="from" title="${t("request.route.previous_tier_text", { tier: esc(poolTier(r.routed)) })}">fr${esc(poolTier(r.routed))}</span>` : "";
  const loop = r.loop ? ` <span class="from" title="${t("request.route.tool_loop_text", { count: esc(r.loop) })}">tl${esc(r.loop)}</span>` : "";
  return transitionCell(r.transition) + from + loop;
}

// The chain opens in a dialog: the whole row is the control that opens it.
function openChain(at) {
  const found = splitRequests(state.requests).find((r) => String(r.at) === at);
  if (!found || !hasChain(found)) return;
  const many = fallbackCount(found);
  const title = many
    ? t("request.chain.title_with_count_text", { count: many })
    : t("request.chain.title");
  $("chain-modal-title").textContent = title;
  $("chain-modal-body").innerHTML = `<div class="chain-body">
    <div class="chain-head"><span class="muted">${esc(title)}</span>
      ${actionButton(t("request.chain.copy_button"), { variant: "ghost copy-chain", attrs: { "data-at": found.at } })}</div>
    ${raceNote(found)}${chainSteps(found)}
  </div>`;
  $("chain-modal").showModal();
}

// Put the fallback count in the chain details. Display only the count for live rows.
function fallbackCount(r) {
  return r.fallbacks === undefined || r.fallbacks === null
    ? ""
    : plural(Number(r.fallbacks), "request.chain.fallback.one_text", "request.chain.fallback.other_text");
}

// Live cards read the count alone; finished rows open their chain by row click.
function mobileRequestDetails(r, live = false) {
  if (!live) return "";
  const many = fallbackCount(r);
  return many ? `<span class="mobile-fallback-count">${many}</span>` : "";
}

function fallbackCell(r, classes = "", live = false) {
  const details = mobileRequestDetails(r, live);
  return `<td role="cell" class="${classes} fallbacks-cell${details ? "" : " no-details"}">${details}</td>`;
}

// Round counts down to K, M, or B units. Keep counts below 1,000 as whole numbers.
const FLOORS = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
function floorCount(n) {
  const [size, mark] = FLOORS.find(([step]) => n >= step) || [1, ""];
  return `${Math.floor(n / size)}${mark}`;
}

function tokenCell(count, mark = "", label = t("request.column.input_column")) {
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
const appCell = (r) => `<td role="cell"${r.key ? ` title="${t("request.app.api_key_text", { key: esc(r.key) })}"` : ""}>${mobileLabel(t("request.column.app_column"))}<span class="cell-value">${r.app ? esc(r.app) : dash}</span></td>`;

// Convert server ages to local timestamps for live requests.
function liveRow(r) {
  const now = Date.now();
  const since = now - r.age * 1000;
  const attemptSince = now - r.attempt_age * 1000;
  return { ...r, since, attemptSince, first: r.ttft == null ? null : attemptSince + r.ttft * 1000 };
}

// Pause table updates and display the waiting request count on the Live button.
function renderLiveToggle() {
  const button = $("live-toggle");
  button.setAttribute("aria-pressed", String(state.livePaused));
  const label = state.livePaused
    ? state.liveWaiting.size
      ? t("request.live.paused_new_button", { count: state.liveWaiting.size })
      : t("request.live.paused_button")
    : t("request.live_button");
  button.innerHTML = `${state.livePaused ? icon("pause") : icon("activity")} ${esc(label)}`;
}

// Hold changes until the user resumes the table.
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
  ctx: t("request.route.context_text"),
  hlt: t("request.route.health_text"),
  lmt: t("request.route.limit_text"),
  err: t("request.route.failure_text"),
  rnd: t("request.route.random_text"),
  cls: t("request.route.classifier_text"),
  esc: t("request.route.escalation_text"),
  rce: t("request.route.race_text"),
};

// Display the hook repeat code unchanged, such as rt1.
const REPEAT_CODE = /^rt\d+$/;
const REPEAT_LABEL = t("request.route.repeat_text");

function transitionCell(t) {
  if (!t || !t.reason) return "";
  const label = REPEAT_CODE.test(t.reason)
    ? REPEAT_LABEL
    : TRANSITION_REASONS[t.reason] || t.reason;
  return ` <span class="transition-code" title="${esc(label)}" aria-label="${esc(label)}">${esc(t.reason)}</span>`;
}

// Use catalog tiers, with built-in tiers available before the pool cards load.
const BUILT_IN_TIERS = { sophos: "A", deinos: "B", koinos: "C", moros: "D" };
function poolTier(name) {
  const pool = state.pools.find((p) => p.shown.replace("daedalus/", "") === name);
  const tier = pool?.members.find((m) => m.tier)?.tier;
  return tier ? tierLetter(tier) : BUILT_IN_TIERS[name] || String(name).slice(0, 1).toUpperCase();
}

const raceOn = () => Boolean(
  state.settings && (fileValue("affinity", "mode") ?? state.settings.defaults.affinity.mode) === "race",
);

function renderLegend(extra = state.legendExtra) {
  const rows = [
    ...Object.entries(TRANSITION_REASONS).filter(([code]) => code !== "rce" || raceOn() || state.requestRace),
    ["frX", t("request.legend.frx_text")],
    ["tlN", t("request.legend.tln_text")],
    ...extra,
  ];
  $("legend-body").innerHTML = rows.map(([code, label]) =>
    `<span><code>${esc(code)}</code>${esc(label)}</span>`).join("");
}

// Add hook legend rows below the base rows. Keep the base rows when no hook is available.
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
  // Animate only new rows when the live group is redrawn.
  $("live").innerHTML = rows.map((r) => `
    <tr role="row" class="live-row${state.liveSeen.has(r.id) ? "" : " arrive"}" data-live="${r.id}">
      ${cell(t("request.column.time_column"), `<span class="pulse"></span><span class="live-ago">${relative(r.since / 1000)}</span>`, "num muted",
        ` title="${esc(stamp(r.since / 1000))}"`)}
      ${appCell(r)}
      ${cell(t("request.column.session_column"), esc(r.session || "-"), "hide-sm num mono")}
      ${nameCell(r.model || r.path, modelName(r.model || r.path, poolOf(r)) + routingCodes(r), t("request.column.model_column"))}
      ${nameCell(r.via || r.trying || "", r.via ? modelName(r.via) : `<span class="muted">${r.trying ? t("request.state.trying_text", { model: modelName(r.trying) }) : t("request.state.waiting_text")}</span>`, t("request.column.served_by_column"))}
      ${cell(t("request.column.effort_column"), effortCell(r), "hide-sm")}
      ${cell(t("request.column.status_column"), t("request.state.live_text"), "status muted")}
      ${cell(t("request.column.input_column"), "-", "hide-sm num muted")}
      ${cell(t("request.column.output_column"), "-", "hide-sm num muted")}
      <td role="cell" class="hide-sm num">${mobileLabel(t("request.column.ttft_column"))}<span class="cell-value"><span data-clock="ttft"></span></span></td>
      <td role="cell" class="hide-sm num">${mobileLabel(t("request.column.stream_column"))}<span class="cell-value"><span data-clock="stream"></span></span></td>
      ${fallbackCell(r, "hide-sm num muted", true)}
    </tr>`).join("");
  state.liveSeen = new Set(rows.map((r) => r.id));
  tickLive();
}

// Stop the TTFT clock at the first token. Start the stream clock then.
// For non-streamed replies, count total request time.
function tickLive() {
  if (state.livePaused) return;
  const now = Date.now();
  for (const row of $("live").children) {
    const r = state.live.get(Number(row.dataset.live));
    if (!r) continue;
    const ago = row.querySelector(".live-ago");
    if (ago) ago.textContent = relative(r.since / 1000);
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

// Filter retained requests by model, client, session, status, and time without discarding rows.
function requestMatches(r, { live = false } = {}) {
  if (state.requestSearch) {
    const hay = `${r.model || ""} ${r.via || ""} ${r.app || ""} ${r.session || ""} ${r.status || ""}`;
    if (!hay.toLowerCase().includes(state.requestSearch)) return false;
  }
  if (live) return true; // Live rows use only the text filter.
  const cls = statusClass(r);
  if (state.requestStatus === "ok" && cls !== "s2") return false;
  if (state.requestStatus === "bad" && cls !== "s4" && cls !== "s5") return false;
  if (state.requestHours) {
    const at = Number(r.at) || 0;
    if (!at || Date.now() / 1000 - at > state.requestHours * 3600) return false;
  }
  return true;
}

// Render filtered history and count both retained and live requests.
function renderRequestTable() {
  const rows = state.requests.filter((r) => requestMatches(r));
  const live = [...state.live.values()].filter((r) => requestMatches(r, { live: true })).length;
  const total = state.requests.length + live;
  const pages = Math.max(1, Math.ceil(rows.length / REQUESTS_PAGE));
  state.requestPage = Math.min(Math.max(1, state.requestPage || 1), pages);
  const start = (state.requestPage - 1) * REQUESTS_PAGE;
  const pageRows = rows.slice(start, start + REQUESTS_PAGE);
  renderRequests(pageRows, total && !rows.length && !live
    ? t("request.empty.filter_text")
    : t("request.empty.none_text"), live);
  // Preserve the button space so the count hint does not move.
  $("request-clear").classList.toggle("off",
    !state.requestSearch && state.requestStatus === "all" && !state.requestHours);
  const canLoadMore = state.requestFetched >= state.requestLimit
    && state.requestLimit < REQUESTS_KEPT;
  $("more-requests").hidden = !canLoadMore;
  $("requests-pager").hidden = !rows.length && !canLoadMore;
  $("requests-prev").disabled = state.requestPage <= 1;
  $("requests-next").disabled = state.requestPage >= pages && !canLoadMore;
  $("requests-page").textContent = rows.length
    ? t("request.count.shown_text", {
      shown: `${start + 1}\u2013${start + pageRows.length}`,
      total: count(rows.length, "request"),
    })
    : "";
  const hint = $("request-count");
  hint.textContent = live ? t("request.count.in_flight_text", { count: live }) : "";
  // Pulse the hint when the displayed live count changes.
  if (live !== state.liveCount) {
    state.liveCount = live;
    hint.classList.remove("bump");
    void hint.offsetWidth;
    hint.classList.add("bump");
  }
}

function renderRequests(rows, empty = t("request.empty.none_text"), live = 0) {
  const flatRows = splitRequests(rows);
  const markup = flatRows.length ? flatRows.map((r) => {
    const chain = hasChain(r);
    const time = relative(r.at);
    return `
    <tr role="row" class="request${chain ? " has-chain" : ""}" data-at="${r.at}"${chain ? ` title="${t("request.chain.show_title")}" data-chain-at="${r.at}" tabindex="0"` : ""}>
      ${cell(t("request.column.time_column"), time, "num muted", ` title="${esc(stamp(r.at))}"`)}
      ${appCell(r)}
      ${cell(t("request.column.session_column"), esc(r.session || "-"), "hide-sm num mono")}
      ${nameCell(r.model || "-", modelName(r.model || "-", poolOf(r)) + routingCodes(r), t("request.column.model_column"))}
      ${nameCell(r.via || "", r.via ? modelName(r.via) : `<span class="muted">${t("request.state.none_text")}</span>`, t("request.column.served_by_column"))}
      ${cell(t("request.column.effort_column"), effortCell(r), "hide-sm")}
      ${cell(t("request.column.status_column"), statusCell(r), `status ${statusClass(r)}`)}
      ${tokenCell(r.tokens?.input, r.tokens?.estimate ? "~" : "", t("request.column.input_column"))}
      ${tokenCell(r.tokens?.output, "", t("request.column.output_column"))}
      ${cell(t("request.column.ttft_column"), esc(r.ttft || "-"), "hide-sm num")}
      ${cell(t("request.column.stream_column"), streamCell(r), "hide-sm num")}
      ${fallbackCell(r, "hide-sm num")}
    </tr>`;
  }).join("") : live ? "" : `<tr role="row"><td role="cell" colspan="13" class="empty">${empty}</td></tr>`;
  const selected = getSelection();
  if (markup === shownRequests) return;
  if (!selected.isCollapsed && $("requests").contains(selected.anchorNode)) return;
  const focused = document.activeElement?.closest?.("tr[data-chain-at]")?.dataset.chainAt;
  shownRequests = markup;
  $("requests").innerHTML = markup;
  const race = flatRows.some((row) => row.transition?.reason === "rce");
  if (race !== state.requestRace) {
    state.requestRace = race;
    renderLegend();
  }
  firstDraw($("requests"));
  if (focused) [...$("requests").querySelectorAll("[data-chain-at]")]
    .find((button) => button.dataset.chainAt === focused)?.focus({ preventScroll: true });
}

// The label of each catalog mode.
const MODES = {
  chat: t("model.mode.chat_label"),
  embedding: t("model.mode.embedding_label"),
  audio_transcription: t("model.mode.audio_transcription_label"),
  audio_speech: t("model.mode.audio_speech_label"),
  image_generation: t("model.mode.image_generation_label"),
  video_generation: t("model.mode.video_generation_label"),
  decisions: t("model.mode.decisions_label"),
  rerank: t("model.mode.rerank_label"),
};
// Use one decorative SVG wrapper for consistent size and Lucide attributes.
const ICON_PATHS = {
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z" />',
  brain: '<path d="M12 18V5" /> <path d="M15 13a4.17 4.17 0 01-3-4 4.17 4.17 0 01-3 4" /> <path d="M17.707 6.294A3 3 0 1012 5a3 3 0 10-5.707 1.294" /> <path d="M17.996 5.125a4 4 0 012.526 5.77" /> <path d="M18 18a4 4 0 002-7.465" /> <path d="M19.967 17.482a4 4 0 11-7.967.517 4 4 0 11-7.967-.517" /> <path d="M6 18a4 4 0 01-2-7.465" /> <path d="M6.004 5.125a4 4 0 00-2.526 5.77" />',
  snowflake: '<path d="m10 20-1.25-2.5L6 18" /> <path d="M10 4 8.75 6.5 6 6" /> <path d="m14 20 1.25-2.5L18 18" /> <path d="m14 4 1.25 2.5L18 6" /> <path d="m17 21-3-6h-4" /> <path d="m17 3-3 6 1.5 3" /> <path d="M2 12h6.5L10 9" /> <path d="m20 10-1.5 2 1.5 2" /> <path d="M22 12h-6.5L14 15" /> <path d="m4 10 1.5 2L4 14" /> <path d="m7 21 3-6-1.5-3" /> <path d="m7 3 3 6h4" />',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2" /> <circle cx="9" cy="9" r="2" /> <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />',
  file_text: '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z" /> <path d="M14 2v5a1 1 0 0 0 1 1h5" /> <path d="M10 9H8" /> <path d="M16 13H8" /> <path d="M16 17H8" />',
  mic: '<path d="M12 19v3" /> <path d="M19 10v2a7 7 0 0 1-14 0v-2" /> <rect x="9" y="2" width="6" height="13" rx="3" />',
  volume_2: '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z" /> <path d="M16 9a5 5 0 0 1 0 6" /> <path d="M19.364 18.364a9 9 0 0 0 0-12.728" />',
  activity: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />',
  pause: '<rect x="14" y="3" width="5" height="18" rx="1" /> <rect x="5" y="3" width="5" height="18" rx="1" />',
  plus: '<path d="M5 12h14" /> <path d="M12 5v14" />',
  search: '<path d="m21 21-4.34-4.34" /> <circle cx="11" cy="11" r="8" />',
  info: '<circle cx="12" cy="12" r="10" /> <path d="M12 16v-4" /> <path d="M12 8h.01" />',
  x: '<path d="M18 6 6 18" /> <path d="m6 6 12 12" />',
  chevron_up: '<path d="m18 15-6-6-6 6" />',
  chevron_down: '<path d="m6 9 6 6 6-6" />',
  trash: '<path d="M10 11v6" /> <path d="M14 11v6" /> <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /> <path d="M3 6h18" /> <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />',
  refresh_cw: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /> <path d="M21 3v5h-5" /> <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /> <path d="M8 16H3v5" />',
  download: '<path d="M12 15V3" /> <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /> <path d="m7 10 5 5 5-5" />',
  chevron_right: '<path d="m9 18 6-6-6-6" />',
  route: '<circle cx="6" cy="19" r="3" /> <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" /> <circle cx="18" cy="5" r="3" />',
  link: '<path d="M9 17H7A5 5 0 0 1 7 7h2" /> <path d="M15 7h2a5 5 0 1 1 0 10h-2" /> <line x1="8" x2="16" y1="12" y2="12" />',
  scale: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" /> <path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z" /> <path d="M7 21h10" /> <path d="M12 3v18" /> <path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2" />',
  gauge: '<path d="m12 14 4-4" /> <path d="M3.34 19a10 10 0 1 1 17.32 0" />',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3" /> <path d="M3 5V19A9 3 0 0 0 21 19V5" /> <path d="M3 12A9 3 0 0 0 21 12" />',
  plug: '<path d="M12 22v-5" /> <path d="M9 8V2" /> <path d="M15 8V2" /> <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />',
  palette: '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor" /> <circle cx="17.5" cy="10.5" r=".5" fill="currentColor" /> <circle cx="8.5" cy="7.5" r=".5" fill="currentColor" /> <circle cx="6.5" cy="12.5" r=".5" fill="currentColor" /> <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z" />',
  key: '<path d="m21 2-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4" />',
  code: '<path d="M10 12.5 8 15l2 2.5" /> <path d="m14 12.5 2 2.5-2 2.5" /> <path d="M14 2v4a2 2 0 0 0 2 2h4" /> <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z" />',
  cloud: '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />',
  sparkles: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /> <path d="M20 3v4" /> <path d="M22 5h-4" />',
  undo_2: '<path d="M9 14 4 9l5-5" /> <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" />',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name]}</svg>`;
}
// Flag chips use registered glyphs. A flag without a glyph keeps its text.
const FLAG_ICON_NAMES = { vision: "image", pdf_input: "file_text", audio_input: "mic", audio_output: "volume_2" };
// The label of each media flag chip.
const FLAGS = {
  vision: t("model.flag.vision_label"),
  pdf_input: t("model.flag.pdf_input_label"),
  audio_input: t("model.flag.audio_input_label"),
  audio_output: t("model.flag.audio_output_label"),
};
// The mode already says what these media flags say: Speech is audio out, Transcription is audio in.
const REDUNDANT = { audio_speech: "audio_output", audio_transcription: "audio_input" };
// The Type cell: a chip for the mode, then a chip for each media flag the mode does not carry.
function typeChips(m) {
  return `<span class="chip flag mode">${esc(MODES[m.mode] || m.mode)}</span>`
    + m.flags.filter((f) => f !== REDUNDANT[m.mode])
      .map((f) => (FLAG_ICON_NAMES[f]
      ? `<span class="chip flag" title="${esc(FLAGS[f] || f)}">${icon(FLAG_ICON_NAMES[f])}</span>`
      : `<span class="chip flag">${esc(FLAGS[f] || f)}</span>`)).join("");
}

// Represent hidden model columns as phone chips with Lucide icons.
const CHIP_ICON_NAMES = { tools: "wrench", cool: "snowflake", brain: "brain" };

function phoneChips(m) {
  // Represent hidden desktop columns with chips.
  const tier = m.tier ? `<span class="chip flag" title="${t("model.column.tier_column")}">${t("model.mobile.tier_text", { value: esc(tierLetter(m.tier)) })}</span>` : "";
  const order = m.order ? `<span class="chip flag" title="${t("model.column.order_column")}">${t("model.mobile.order_text", { value: m.order })}</span>` : "";
  const context = m.max_input_tokens
    ? `<span class="chip flag" title="${t("model.column.context_column")}">${t("model.mobile.context_text", { value: `<span class="num">${tokens(m.max_input_tokens)}</span>` })}</span>`
    : "";
  // Omit chips for unsupported capabilities.
  const tools = m.mode === "chat" && m.tools
    ? `<span class="chip flag" title="${t("model.column.tools_column")}">${icon(CHIP_ICON_NAMES.tools)}</span>` : "";
  const reasoning = m.mode === "chat" && m.reasoning
    ? `<span class="chip flag" title="${t("model.column.reasoning_column")}">${icon(CHIP_ICON_NAMES.brain)}${m.effort ? `<span class="num">${esc(m.effort)}</span>` : ""}</span>`
    : "";
  const cool = m.cooldown && m.cooldown > Date.now() / 1000
    ? `<span class="chip flag" title="${t("model.column.cooldown_column")}">${icon(CHIP_ICON_NAMES.cool)}<span class="cool" data-until="${m.cooldown}">${timeLeft(m.cooldown)}</span></span>`
    : "";
  return tier + order + context + tools + reasoning + cool;
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
const EFFORTS = {
  none: t("model.effort.none_label"),
  minimal: t("model.effort.minimal_label"),
  low: t("model.effort.low_label"),
  medium: t("model.effort.medium_label"),
  high: t("model.effort.high_label"),
  xhigh: t("model.effort.xhigh_label"),
  max: t("model.effort.max_label"),
};
function effortChip(name, label = EFFORTS[name] || name, struck = false) {
  const level = name in EFFORTS ? ` e-${name}` : "";
  return `<span class="chip flag effort${level}${struck ? " old" : ""}">${esc(label)}</span>`;
}
// Display the catalog effort for reasoning models, or Yes if the catalog has no effort.
function reasoningCell(m) {
  if (!m.reasoning || !m.effort) return yesNo(m.reasoning);
  const known = m.effort in EFFORTS;
  const label = known ? EFFORTS[m.effort] : m.effort.charAt(0).toUpperCase() + m.effort.slice(1);
  return `<span class="chip flag effort${known ? ` e-${m.effort}` : ""}" title="${t("model.detail.default_effort_label")}">${esc(label)}</span>`;
}
const yesNo = (on) => statusBadge(on ? t("common.boolean.yes_text") : t("common.boolean.no_text"), { variant: on ? "yes" : "muted" });

// Render model identity, capability chips, limits, and reasoning efforts.
// The use rates of one model, counted from the request rows when the dialog opens.
function useStats(m) {
  const served = state.requests.filter((r) => (r.via || r.model) === m.id);
  if (!served.length) return [];
  const pct = (count, total) => `${Math.round((100 * count) / total)}%`;
  const ok = served.filter((r) => !r.cancelled && String(r.status)[0] === "2").length;
  const races = state.requests.filter((r) => (r.attempts || []).some((a) => a.model === m.id)
    && (r.attempts || []).some((a) => a.race));
  const won = races.filter((r) => (r.attempts || []).find((a) => a.race === "won")?.model === m.id).length;
  const fell = served.filter((r) => (r.fallbacks ?? 0) > 0).length;
  const streamed = served.filter((r) => r.stream).length;
  const tokens = served.filter((r) => r.ttft != null).map((r) => parseFloat(r.ttft))
    .sort((a, b) => a - b);
  const mid = tokens.length % 2 ? tokens[(tokens.length - 1) / 2]
    : tokens.length ? (tokens[tokens.length / 2 - 1] + tokens[tokens.length / 2]) / 2 : null;
  return [
    [pct(ok, served.length), t("model.detail.success_label")],
    races.length ? [pct(won, races.length), t("model.detail.races_won_label")] : null,
    [pct(fell, served.length), t("model.detail.fallbacks_label")],
    [pct(streamed, served.length), t("model.detail.streamed_label")],
    mid == null ? null : [seconds(mid), t("model.detail.first_token_label")],
    ...limitRows().filter((r) => r.model === m.id && (r.limit > 0 || r.remaining > 0))
      .map((r) => [t("limits.value.of_text", {
        remaining: r.remaining.toLocaleString(),
        limit: r.limit.toLocaleString(),
      }), t("model.detail.limit_left_label", { unit: limitUnit(r) })]),
  ].filter(Boolean);
}

function openModelModal(m) {
  const { provider, dev, model } = parts(m.id);
  $("model-modal-title").innerHTML =
    `${mark(provider)}<span class="name">${esc(model || m.slug || m.id)}</span>`;
  // Display a mark only when its file exists. Keep plain text for other names.
  const markImg = (name) => (MARK_FILES.has(name) ? mark(name) : "");
  const identity = [
    [t("model.column.model_column"), model || m.slug || m.id, ""],
    ...(dev ? [[t("model.detail.developer_label"), dev, markImg(dev)]] : []),
    [t("model.detail.provider_label"), provider, markImg(provider)],
  ].map(([label, value, icon]) =>
    `<div class="id-row"><span class="label">${esc(label)}</span>${icon}<span class="value">${esc(value)}</span></div>`)
    .join("");
  // Pair chip icons with text so the title is not required.
  const flags = (m.flags || []).filter((f) => f !== REDUNDANT[m.mode]).map((f) =>
    `<span class="chip flag" title="${esc(FLAGS[f] || f)}">${FLAG_ICON_NAMES[f] ? `${icon(FLAG_ICON_NAMES[f])} ` : ""}${esc(FLAGS[f] || f)}</span>`)
    .join("");
  const chips = [
    `<span class="chip flag mode">${esc(MODES[m.mode] || m.mode)}</span>` + flags,
    m.tier ? `<span class="chip flag" title="${t("model.column.tier_column")}">${t("model.mobile.tier_text", { value: esc(tierLetter(m.tier)) })}</span>` : "",
    m.order ? `<span class="chip flag" title="${t("model.column.order_column")}">${t("model.mobile.order_text", { value: m.order })}</span>` : "",
    m.mode === "chat" && m.tools
      ? `<span class="chip flag" title="${t("model.column.tools_column")}">${icon(CHIP_ICON_NAMES.tools)} ${t("model.column.tools_column")}</span>` : "",
    m.mode === "chat" && m.reasoning
      ? `<span class="chip flag" title="${t("model.column.reasoning_column")}">${icon(CHIP_ICON_NAMES.brain)} ${t("model.column.reasoning_column")}</span>`
      : "",
    m.cooldown && m.cooldown > Date.now() / 1000
      ? `<span class="chip flag" title="${t("model.column.cooldown_column")}">${icon(CHIP_ICON_NAMES.cool)}<span class="cool" data-until="${m.cooldown}">${timeLeft(m.cooldown)}</span></span>`
      : "",
  ].join("");
  // Add a bar only for weight. CSS keeps the blocks at one height.
  const stats = [
    m.max_input_tokens ? [m.max_input_tokens.toLocaleString(), t("model.detail.input_tokens_label")] : null,
    m.max_output_tokens ? [m.max_output_tokens.toLocaleString(), t("model.detail.output_tokens_label")] : null,
    m.weight != null ? [m.weight.toFixed(2), t("model.detail.weight_label"), progressMeter(m.weight)] : null,
  ].filter(Boolean).map(([value, label, bar]) =>
    `<div class="stat"><span class="num">${esc(value)}</span><span class="label">${esc(label)}</span>${bar || ""}</div>`)
    .join("");
  const use = useStats(m).map(([value, label]) =>
    `<div class="stat"><span class="num">${esc(value)}</span><span class="label">${esc(label)}</span></div>`)
    .join("");
  const efforts = (m.efforts || []).map((name) => effortChip(name)).join("");
  $("model-modal-body").innerHTML = `<div class="model-id">${identity}</div>`
    + (chips ? `<div class="model-chips">${chips}</div>` : "")
    + (stats ? `<div class="model-stats">${stats}</div>` : "")
    + (use ? `<h4>${t("model.detail.use_title")}</h4><div class="model-stats">${use}</div>` : "")
    + (efforts ? `<h4>${t("model.detail.supported_efforts_label")}</h4><div class="pills">${efforts}</div>` : "");
  $("model-modal").showModal();
}
// Mix red at 0, orange at 0.5, and blue at 1.
function weightColor(weight) {
  const high = weight >= 0.5;
  const share = Math.round((high ? (weight - 0.5) * 2 : weight * 2) * 100);
  return high ? `color-mix(in srgb, var(--accent) ${share}%, var(--orange))`
    : `color-mix(in oklch, var(--orange) ${share}%, var(--red))`;
}
const progressMeter = (value) => `<div class="track"><div class="fill"
  style="width:${Math.round(value * 100)}%;background:${weightColor(value)}"></div></div>`;

// With no column chosen, the rows sort by type, then by model name.
function sortModels(rows) {
  const { dir } = state.sort;
  const key = Object.hasOwn(sortValue, state.sort.key) ? state.sort.key : "mode";
  return [...rows].sort((a, b) => {
    const x = sortValue[key](a), y = sortValue[key](b);
    if (x === y) return a.id.localeCompare(b.id);
    if (x === null) return 1;
    if (y === null) return -1;
    return (x < y ? -1 : 1) * dir;
  });
}

function syncSortFocus() {
  const phone = matchMedia("(max-width: 720px)").matches;
  const heads = [...document.querySelectorAll("#model-head th")];
  const small = $("sort-small");
  heads.forEach((th) => { th.querySelector("button").tabIndex = phone ? -1 : 0; });
  const focused = document.activeElement;
  if (phone && heads.some((th) => th.contains(focused))) small.focus();
  if (!phone && focused === small) {
    const head = heads.find((th) => th.dataset.sort === state.sort.key) || heads[0];
    head?.querySelector("button").focus();
  }
}

function renderSortHeads() {
  syncSortFocus();
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

// Retain unchanged markup to preserve browser search marks and text selection.
// Defer updates while the host contains selected text.
const DRAWN = new Map();
// Animate each host only on its first data render.
const DRAWN_SHOWN = new Set();
function firstDraw(host) {
  if (!host || DRAWN_SHOWN.has(host)) return;
  DRAWN_SHOWN.add(host);
  host.classList.add("drawn");
}
// Replay the animation only for the selected host, not the surrounding page.
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

// Animate only model rows after a filter selection.
function selectRows() {
  renderModels();
  replay($("models"));
}

function renderModels() {
  markPools();
  // Count catalog rows, not routable models from the status response.
  $("nav-models").textContent = state.models.length || "";
  const query = $("search").value.trim().toLowerCase();
  const rows = sortModels(state.models.filter((m) =>
    (state.tier === "All" || tierLetter(m.tier) === state.tier)
    && (state.mode === "all" || m.mode === state.mode || m.flags.includes(state.mode))
    && m.id.toLowerCase().includes(query)));
  const empty = state.models.length ? t("model.empty.filter_text") : t("model.empty.catalog_text");
  draw("models", rows.length ? rows.map((m) => `
    <tr data-model="${esc(m.id)}">
      ${nameCell(m.id, actionButton(modelName(m.id), {
        variant: "model-open",
        attrs: { "aria-label": m.id, "aria-haspopup": "dialog", "aria-controls": "model-modal" },
      }), "", `<span class="types phone-types">${typeChips(m)}</span><span class="phone-chips">${phoneChips(m)}</span>`)}
      <td class="hide-sm"><div class="types">${typeChips(m)}</div></td>
      <td class="mid hide-sm">${mobileLabel(t("model.column.tier_column"))}<span class="cell-value">${m.tier ? `<span class="tier" title="${esc(m.tier)}">${esc(tierLetter(m.tier))}</span>` : dash}</span></td>
      <td class="hide-sm mid num${m.order > 1 ? "" : " muted"}">${m.order ?? dash}</td>
      <td class="hide-sm num muted"><span${m.max_input_tokens ? ` title="${t("model.tokens.title", { count: m.max_input_tokens.toLocaleString() })}"` : ""}>${tokens(m.max_input_tokens)}</span></td>
      <td class="mid hide-sm">${mobileLabel(t("model.column.tools_column"))}<span class="cell-value">${m.mode === "chat" ? yesNo(m.tools) : dash}</span></td>
      <td class="hide-sm mid">${m.mode === "chat" ? reasoningCell(m) : dash}</td>
      <td class="num hide-sm">${mobileLabel(t("model.column.cooldown_column"))}<span class="cell-value">${coolCells(m)}</span></td>
      <td class="num">${mobileLabel(t("model.column.weight_column"))}<span class="cell-value">${m.weight == null ? dash
        : `<div class="weight">${progressMeter(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div>`}</span></td>
    </tr>`).join("") : `<tr><td colspan="9" class="empty">${empty}</td></tr>`);
}

// Format remaining cooldown time, such as 59s, 4m 05s, or 3h 12m.
function timeLeft(until) {
  const left = Math.ceil(until - Date.now() / 1000);
  if (left <= 0) return null;
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h
    ? t("time.duration.hours_minutes_text", { hours: h, minutes: pad(m) })
    : m
      ? t("time.duration.minutes_seconds_text", { minutes: m, seconds: pad(s) })
      : t("time.duration.seconds_text", { seconds: s });
}
const coolCell = (until) => {
  const left = until ? timeLeft(until) : null;
  return left ? `<span class="cool" data-until="${until}" title="${t("model.cooldown.until_text", { time: esc(stamp(until)) })}">${left}</span>` : dash;
};
// Display model cooldowns and cooldowns for clients with their own provider keys.
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

// Read provider key states such as env:NAME and db:NAME.
async function refreshEnv() {
  state.env = await call("env");
  const settings = document.querySelector('section[data-page="settings"]');
  if (!settings.hidden && sectionState($("settings")).key === "providers") renderForm();
}

function renderKeys(rows) {
  draw("keys", rows.length ? rows.map((k) => `
    <tr>
      <td class="name" title="${esc(k.name)}">${esc(k.name)}</td>
      <td class="num muted mono"><span${k.start ? ` title="${t("settings.keys.only_start_title")}"` : ""}>${k.start ? esc(k.start) + "&hellip;" : "-"}</span></td>
      <td class="hide-sm muted">${stamp(k.created)}</td>
      <td class="muted">${mobileLabel(t("settings.keys.last_used_column"))}<span class="cell-value">${k.used ? timeText(k.used) : t("settings.keys.used_never_text")}</span></td>
      <td class="end">${actionButton(icon("trash"), {
        variant: "ghost danger",
        attrs: { "data-key": k.name, "aria-label": t("settings.keys.delete_named_label", { name: k.name }), title: t("action.remove_button") },
      })}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="empty">${t("settings.keys.empty_text")}</td></tr>`);
  markCardScrollEdges();
}

async function refreshKeys() {
  state.keys = await call("keys");
  renderKeys(state.keys);
  renderOverview();
}

// Edit these keys in the form and other keys in YAML.
const FORM_KEYS = ["api_key", "account_id", "client_keys", "api_base", "api_type", "discovery_url", "discovery_match", "exclude", "tier", "models", "hooks"];
// Share one hint across pattern fields that accept the same four shapes.
const PATTERN_HINT = t("provider.pattern.hint");
// The keys that a model override sets but the provider level does not.
const MODEL_ONLY = ["pool", "timeout"];
// The keys that the provider level sets but a model override does not.
const PROVIDER_ONLY = ["hourly_requests"];
const TIERS = ["TIER-A", "TIER-B", "TIER-C", "TIER-D"];

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

// Remove empty form lists and maps. Retain empty model overrides.
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
  const files = state.files.map((file, index) => {
    const mark = fileDirty(index) ? " &bull;" : "";
    const shadow = file.shadow ? t("provider.file.shadow_text", { file: file.shadow }) : "";
    const stem = fileName(file.path).replace(/\.ya?ml$/i, "").toLowerCase();
    return actionButton(`${chipFor("provider-form", stem)}<span>${esc(fileName(file.path))}${mark}</span>`, {
      attrs: { "data-file": index, title: `${file.path}${shadow}` },
    });
  }).join("");
  const host = $("files");
  host.hidden = state.fileOpen;
  host.innerHTML = `<nav class="sections drill provider-files" aria-label="${esc(t("nav.providers_link"))}">${files}</nav>`
    + `<div class="provider-file-actions">${actionButton(t("action.new_provider_button"), {
      variant: "add", attrs: { id: "new-provider" },
    })}</div>`;
  // Save writes YAML text. Form rows save when they close.
  const yamlSave = $("yaml-save");
  if (yamlSave) yamlSave.disabled = state.view !== "yaml" || !dirty();
}

// Store API files with editable form copies and saved copies.
function takeFiles(files) {
  state.files = files;
  state.saved = files.map((file) => file.text);
  state.forms = files.map((file) => clone(file.blocks));
  state.formSaved = files.map((file, index) => formText(index));
}

// Reload one saved file from the API without discarding changes to other files.
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

// Open invalid YAML in the text view and display the error.
function showFileError(index) {
  const error = state.files[index]?.error;
  if (error) showWrite("providers", t("provider.yaml.error_text", { error }), true);
  else clearWrite("providers");
  if (error) state.view = "yaml";
}

function openProviderFile(index) {
  state.file = index;
  state.fileOpen = true;
  state.providerReturnKey = "";
  const section = sectionState("provider-form");
  section.key = "";
  section.open = false;
  renderFiles();
  renderForm();
  setProviderRoute(state.files[index]?.path, null);
  replay($("provider-form"));
}

function captureProviderYamlDraft() {
  const editor = $("editor");
  const file = state.files[state.file];
  if (state.fileOpen && state.view === "yaml" && editor && file) file.text = editor.value;
}

function openFile(index) {
  captureProviderYamlDraft();
  state.file = index;
  state.fileOpen = true;
  state.providerReturnKey = "";
  showFileError(index);
  const section = sectionState("provider-form");
  section.key = "";
  section.open = false;
  const blocks = state.files[index]?.blocks == null ? null : Object.keys(state.files[index].blocks);
  if (blocks == null) return selectProviderSection("yaml");
  state.view = "form";
  // A file of one provider opens its config; a shared file opens its provider list.
  if (blocks.length === 1) return selectProviderSection(blocks[0]);
  openProviderFile(index);
}

// The × button removes the chip value from its list or map.
const pill = (text, path, key) => `<span class="pill">${esc(text)}${actionButton(icon("x"), {
  attrs: {
    "aria-label": t("action.remove_button"),
    title: t("action.remove_button"),
    "data-drop": JSON.stringify([...path, key]),
  },
})}</span>`;

// Replace the Add button with an inline input on click.
const adder = (path, kind, label = t("action.add_button")) => actionButton(esc(label), {
  variant: "add",
  attrs: { "data-add": JSON.stringify(path), "data-kind": kind },
});

function listField(name, values, path) {
  const list = Array.isArray(values) ? values : [];
  return `<div class="pills">${list.map((value, index) => pill(shown(value), path, index)).join("")}${adder(path, "list")}</div>`;
}

function mapPills(values, path, kind, separator) {
  const map = values && typeof values === "object" ? values : {};
  return Object.entries(map).map(([key, value]) => pill(`${key}${separator}${shown(value)}`, path, key)).join("")
    + adder(path, kind, kind === "override" ? t("action.add_key_button") : t("action.add_button"));
}

// Keep edited lists in page state so each list can render without changing other fields.
function listValue(group, key) {
  state.settings.lists ||= {};
  const found = state.settings.lists[`${group}.${key}`] ?? setting(group, key);
  // Convert a single path from an older file to a form list.
  return Array.isArray(found) ? found : found ? [found] : [];
}

function setListValue(group, key, values) {
  state.settings.lists ||= {};
  state.settings.lists[`${group}.${key}`] = values;
}
const settingPill = (group, key, value, index) => `<span class="pill">${esc(value)}${actionButton(icon("x"), {
  attrs: {
    "aria-label": t("action.remove_button"),
    title: t("action.remove_button"),
    "data-setting-drop": JSON.stringify([group, key, index]),
  },
})}</span>`;
const settingAdder = (group, key) => actionButton(t("action.add_button"), {
  variant: "add", attrs: { "data-setting-add": JSON.stringify([group, key]) },
});

// Omit the hooks folder prefix from the displayed file name.
const hookName = (name) => esc(name.replace(/^hooks\//, ""));

// Match installed hooks to global, provider, YAML, or model scope.
// kind identifies the card type, and name identifies the provider or model.
function hookFit(row, kind, name) {
  const scope = row.scope || "global";
  const targets = row.targets || [];
  if (scope === "global") return true;
  if (scope === "provider" || scope === "yaml") return targets.includes(kind === "provider" ? name : String(name).split("/")[0]);
  if (scope === "model") return kind === "model" && targets.includes(name);
  return false;
}

function hookChoices(kind, name) {
  return (state.settings?.hook_rows ?? [])
    .filter((row) => !row.problem && hookFit(row, kind, name))
    .map((row) => row.path);
}

// Each provider or model hook row selects a surface and a matching file.
// Write through the form path with the Settings chip flow.
const HOOK_DEFAULT = "on-upstream";
let openFormHook = null;

const hookFormRow = (entry, path, choices) => {
  const list = entry && typeof entry === "object" ? entry : {};
  const surface = Object.keys(list)[0] ?? HOOK_DEFAULT;
  const value = String(list[surface] ?? "");
  const at = JSON.stringify(path);
  const open = openFormHook === at;
  const menu = open ? `<div class="menu" role="listbox" aria-label="${t("hook.file.label")}">${[""].concat(choices)
    .map((name) => actionButton(name ? hookName(name) : t("hook.empty.file_text"), {
      attrs: {
        role: "option",
        "aria-selected": String(name === value),
        "data-form-hook-choice": JSON.stringify([...path, name]),
      },
    })).join("")}</div>` : "";
  const selector = actionButton(value ? hookName(value) : t("hook.empty.file_text"), {
    variant: "select",
    attrs: {
      "data-form-hook-select": at,
      "aria-haspopup": "listbox",
      "aria-expanded": String(open),
      title: t("hook.action.select_file_title"),
    },
  });
  const drop = actionButton(icon("x"), {
    attrs: {
      "aria-label": t("action.remove_button"),
      title: t("action.remove_button"),
      "data-form-hook-drop": at,
    },
  });
  return `<span class="pill hook"><select data-form-hook-surface='${esc(at)}' aria-label="${t("hook.surface.label")}">${
    HOOK_SURFACES.map(([key, label]) => `<option value="${key}"${key === surface ? " selected" : ""}>${esc(label)}</option>`).join("")
    }</select>${selector}${drop}${menu}</span>`;
};

// List suitable installed hooks for a provider or model override.
function hookFormRows(values, path, kind, name) {
  const choices = hookChoices(kind, name);
  const rows = (Array.isArray(values) ? values : [])
    .map((entry, index) => hookFormRow(entry, [...path, index], choices)).join("")
    || `<em class="none">${t("hook.empty.file_text")}</em>`;
  return `<div class="pills">${rows}${actionButton(t("action.add_file_button"), {
    variant: "add",
    attrs: { "data-form-hook-add": JSON.stringify([path, kind, name]) },
  })}</div>`;
}

// Return the hook list through an extra path index. Create the list if absent.
const formHookList = (path) => parentOf([...path, 0], []);

// Label sources with the repository, optional folder, and non-main reference.
const sourceLabel = (entry) => `${entry.repo || ""}${entry.path ? `/${entry.path}` : ""}${entry.ref && entry.ref !== "main" ? `@${entry.ref}` : ""}`;

// Parse the repository, reference, and folder from source text or a GitHub tree URL.
function sourceParts(value) {
  const tree = String(value).match(/github\.com\/([^/]+\/[^/]+)\/tree\/([^/]+)\/?(.*)$/);
  if (tree) return { repo: tree[1].replace(/\.git$/, ""), ref: tree[2], path: tree[3].replace(/\/$/, "") };
  const plain = String(value).match(/github\.com\/([^/]+)\/([^/?#]+?)(?:\.git)?\/?$/);
  if (plain) return { repo: `${plain[1]}/${plain[2]}`, ref: "main", path: "hooks" };
  return { repo: String(value).trim().replace(/^\/+|\/+$/g, ""), ref: "main", path: "hooks" };
}

const hookVersion = (record) => record?.version || "-";

// The request surfaces of the settings group: the `on-*` keys of its defaults, in surface order. The
// card names every file at the surfaces it runs at, so the settings hold no list of its own.
const requestSurfaces = () => HOOK_SURFACES.filter(([key]) => key in (state.settings?.defaults?.hooks ?? {}));

// Store surface order separately from explicit request-hook lists.
// Order changes precedence, not provider, model, or client scope.
function hookOrderState() {
  if (!state.settings.hookOrder) {
    const saved = setting("hooks", "order");
    state.settings.hookOrder = Object.fromEntries(Object.entries(saved && typeof saved === "object" ? saved : {})
      .map(([surface, files]) => [surface, Array.isArray(files) ? [...files] : []]));
  }
  return state.settings.hookOrder;
}

// List preferred files first, then explicit and installed files that support the surface.
// The server orders rows by file name.
function hookOrderFiles(surface) {
  const saved = hookOrderState()[surface] || [];
  const explicit = surface in (state.settings?.defaults?.hooks ?? {}) ? settingList("hooks", surface) : [];
  const installed = (state.settings?.hook_rows ?? [])
    .filter((row) => !row.problem && ((row.surfaces || []).includes(surface) || (row.runs || []).includes(surface)))
    .map((row) => row.path);
  return [...new Set([...saved, ...explicit, ...installed])];
}

// Keep default hook surfaces visible when empty. Display other surfaces that installed hooks use.
const orderedSurfaces = () => HOOK_SURFACES.filter(([surface]) =>
  surface in (state.settings?.defaults?.hooks ?? {}) || hookOrderFiles(surface).length);

// Display one chip per hook surface. The file, not the card, defines the surfaces.
function hookSurfacesCell(row) {
  const labels = new Map(HOOK_SURFACES.map(([key, label]) => [key, label]));
  const named = new Set(row.runs || []);
  const chips = (row.surfaces || []).map((surface) => {
    const mark = named.has(surface) ? " named" : "";
    return `<span class="pill ${row.enabled ? "on" : "off"}${mark}">${esc(labels.get(surface) || surface)}</span>`;
  }).join("");
  return `<div class="pills">${chips || `<em class="none">${t("hook.empty.surface_text")}</em>`}</div>`;
}

// Runtime filters metadata scopes before it applies this surface order.
// Unlisted files follow in their existing order.
function hookOrderRows() {
  return orderedSurfaces().map(([surface, label]) => {
    const files = hookOrderFiles(surface);
    const rows = files.map((file, index) => `<li><span class="cell-value">${hookName(file)}</span>
      ${actionButton(icon("chevron_up"), {
        variant: "ghost",
        attrs: {
          "data-hook-move": JSON.stringify([surface, index, -1]),
          disabled: !index,
          "aria-label": t("hook.order.up_named_label", { file }),
          title: t("hook.order.up_title"),
        },
      })}
      ${actionButton(icon("chevron_down"), {
        variant: "ghost",
        attrs: {
          "data-hook-move": JSON.stringify([surface, index, 1]),
          disabled: index + 1 >= files.length,
          "aria-label": t("hook.order.down_named_label", { file }),
          title: t("hook.order.down_title"),
        },
      })}</li>`).join("");
    const hint = t("hook.order.hint");
    return field(label, hint, `
      <ul class="hook-order">${rows || `<li><em class="none">${t("hook.empty.file_for_surface_text")}</em></li>`}</ul>`);
  }).join("");
}

// Read the Hooks card order after a move.
function renderHookOrder() {
  const host = $("hooks-order");
  if (host) host.innerHTML = hookOrderRows();
}

// Move a surface file up by -1 or down by 1.
function moveHookFile([surface, index, step]) {
  const values = [...hookOrderFiles(surface)];
  const at = index + step;
  if (at < 0 || at >= values.length) return;
  [values[index], values[at]] = [values[at], values[index]];
  hookOrderState()[surface] = values;
  renderHookOrder();
  renderSettingsSave();
  saveSettings();
}

// Display old and new file versions under the sources after an update.
function hooksNote(text) {
  const note = $("hooks-note");
  if (note) note.textContent = text;
}

function updateNote(answer) {
  const moved = (answer.hooks || []).filter((hook) => hook.moved);
  // Explain why a source fetched and moved no files.
  if (moved.length) {
    const files = moved.map((hook) => t("hook.update.transition_text", {
      name: hook.name, before: hookVersion(hook.before), after: hookVersion(hook.after),
    })).join(", ");
    return t("hook.update.moved_text", { files });
  }
  return t("hook.empty.moved_text");
}

// Display failures in the red floating notice, not under the card.
function updateFails(answer) {
  return (answer.report || []).join(" ");
}

// Manage the hook folder, sources, loaded files, and updates.
function hooksManager() {
  const rows = state.settings.hook_rows || [];
  const sources = listValue("hooks", "sources");
  const at = Math.min(state.hooksSource ?? 0, Math.max(0, sources.length - 1));
  const options = sources.length
    ? sources.map((entry, index) => `<option value="${index}"${index === at ? " selected" : ""}>${esc(sourceLabel(entry))}</option>`).join("")
    : `<option value="">${t("hook.empty.source_text")}</option>`;
  const selected = sources.length ? sourceLabel(sources[at]) : "";
  const list = rows.map((row) => {
    const record = row.record || {};
    const source = record.repo ? `${record.repo}@${(record.commit || "").slice(0, 7)}` : "";
    const title = row.title || row.name;
    const author = row.author ? `<span class="hook-author">${esc(row.author)}</span>` : "";
    const problem = row.problem ? statusBadge(row.problem, { variant: "hook-problem only-sm" }) : "";
    return `<tr><td role="cell" class="name"><span class="cell-value" title="${esc(row.name)}"><span class="hook-title">${esc(title)}</span>${author}${problem}</span></td>
      <td role="cell"><span class="cell-value">${esc(hookVersion(row))}</span></td>
      <td role="cell" class="surfaces">${hookSurfacesCell(row)}</td>
      <td role="cell"><span class="cell-value">${esc(row.scope || t("hook.scope.global_text"))}${(row.targets || []).length ? `: ${esc(row.targets.join(", "))}` : ""}</span></td>
      <td role="cell" class="hide-sm"><span class="cell-value" title="${esc(row.problem || source || t("hook.scope.local_text"))}">${row.problem ? statusBadge(row.problem, { variant: "bad" }) : esc(record.repo || t("hook.scope.local_text"))}${record.commit ? `<span class="hook-sha">${esc(record.commit.slice(0, 7))}</span>` : ""}</span></td>
      <td role="cell"><input type="checkbox" role="switch" class="switch" data-hook-toggle="${esc(row.name)}"
        ${row.enabled ? "checked" : ""} aria-label="${t("hook.state.enabled_named_label", { name: esc(row.name) })}"></td>
      <td class="end">${actionButton(icon("trash"), {
        variant: "ghost danger",
        attrs: { "data-hook-drop": row.path, "data-hook-title": title, "aria-label": t("hook.action.delete_named_label", { name: row.name }), title: t("action.remove_button") },
      })}</td></tr>`;
  }).join("") || `<tr><td role="cell" colspan="7"><em class="none">${t("hook.empty.file_text")}</em></td></tr>`;
  const scan = state.hooksScan;
  const panel = scan ? `<div class="pills" id="hooks-scan">
      <span class="sub">${t("hook.scan.at_text", { source: esc(sourceLabel(scan)), commit: esc((scan.commit || "").slice(0, 7)) })}</span>${scan.files
    .map((file) => `<label class="pill"><input type="checkbox" data-hook-take="${esc(file.name)}"
      ${scan.take[file.name] ? "checked" : ""}>${esc(file.name)}${file.version ? ` ${esc(file.version)}` : ""}</label>`).join("")}
      ${actionButton(`${icon("download")} ${t("action.take_files_button")}`, {
        variant: "primary", attrs: { "data-hook-take-all": true },
      })}</div>` : "";
  return `${field(t("settings.hooks.installed.label"), t("settings.hooks.installed.hint"), `
      <div class="card-scroll scroll-fade"><table class="keys"><thead><tr><th>${t("hook.column.author_title")}</th><th>${t("hook.column.version_column")}</th><th>${t("hook.column.surfaces_column")}</th><th>${t("hook.column.scopes_column")}</th><th class="hide-sm">${t("hook.column.source_column")}</th><th>${t("hook.column.enabled_column")}</th><th></th></tr></thead>
      <tbody>${list}</tbody></table></div>
      ${actionButton(`${icon("refresh_cw")} ${t("action.update_sources_button")}`, {
        variant: "ghost", attrs: { "data-hooks-update": true },
      })}`)}
    <div class="field stack info" id="hooks-order">${hookOrderRows()}</div>
    ${field(t("settings.hooks.folder.label"), t("settings.hooks.folder.hint"), `
      <span class="input"><input type="text" id="set-hooks-dir" aria-haspopup="dialog" aria-controls="modal" data-value="${t("settings.hooks.folder.label")}"
        readonly spellcheck="false" value="${esc(fileValue("hooks", "dir") ?? "")}"
        placeholder="${esc(state.settings.defaults.hooks.dir)}"></span>`)}
    ${field(t("settings.hooks.sources.label"), t("settings.hooks.sources.hint"), `
      <div class="pills"><select id="hook-source" data-hook-source aria-label="${t("settings.hooks.source.label")}">${options}</select>
        ${actionButton(`${icon("plus")} ${t("action.add_repository_button")}`, {
          variant: "add", attrs: { "data-hook-source-add": true },
        })}
        ${sources.length ? actionButton(icon("x"), {
          variant: "ghost",
          attrs: {
            "data-hook-source-drop": true,
            "aria-label": t("hook.action.delete_source_title", { name: selected }),
            title: t("hook.action.delete_source_title", { name: selected }),
          },
        }) : ""}</div>${panel}
      <div class="sub" id="hooks-note" role="status"></div>`)}`;
}

async function dropHookFile(button) {
  const name = button.dataset.hookDrop;
  const title = button.dataset.hookTitle || name;
  if (!(await ask(t("dialog.delete_hook.title"), t("dialog.delete_hook.message", { name: title }), t("action.delete_button"), true))) return;
  await guarded(async () => {
    await call("hooks/file", { method: "DELETE", body: JSON.stringify({ name }) });
    await loadSettings();
  });
}

async function addHookSource() {
  const typed = await ask(t("dialog.source.title"), "",
    t("action.scan_button"), false, t("dialog.source.placeholder"));
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
    hooksNote(t("hook.source.count_text", { files: count(found.files.length, "file"), source: sourceLabel(found) }));
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
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
    const fails = updateFails(answer);
    hooksNote(fails ? "" : updateNote(answer));
    if (fails) showNotice(fails);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
    showWrite("settings", error.message, true);
  }
}

async function runHooksUpdate() {
  hooksNote(t("hook.state.updating_text"));
  try {
    const answer = await call("hooks/update", { method: "POST" });
    await loadSettings();
    const fails = updateFails(answer);
    hooksNote(fails ? "" : updateNote(answer));
    if (fails) showNotice(fails);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
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
  if (on) {
    const matches = (file, value) => [file, file.replace(/\.py$/, ""),
      file.split("/").pop(), file.split("/").pop().replace(/\.py$/, "")].includes(value);
    const removed = [...disabled].filter(value => matches(name, value));
    for (const value of removed) disabled.delete(value);
    for (const row of state.settings.hook_rows || []) {
      if (row.name !== name && removed.some(value => matches(row.name, value))) disabled.add(row.name);
    }
  } else disabled.add(name);
  setListValue("hooks", "disabled", [...disabled]);
  // Change chip colors before saving. The transition needs the same node, which saving replaces.
  const row = [...document.querySelectorAll('.card[data-section="hooks"] .keys tbody tr')].find(
    (tr) => tr.querySelector("input[data-hook-toggle]")?.dataset.hookToggle === name
  );
  row?.querySelectorAll(".surfaces .pill").forEach((pill) => {
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

// Keep a Settings list value on Enter or a click outside the input.
function showSettingAdder(button) {
  const [group, key] = JSON.parse(button.dataset.settingAdd);
  const box = document.createElement("span");
  box.className = "adder";
  box.innerHTML = `<input type="text" spellcheck="false" placeholder="${t("settings.list.placeholder")}">`;
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
  const button = actionButton(icon("info"), {
    variant: "hint",
    attrs: { "aria-describedby": id, "aria-label": t("common.hint") },
  });
  return `<span><b>${esc(label)}</b>${button}<small id="${id}" role="tooltip">${esc(hint)}</small></span>`;
}

function field(label, hint, body) {
  return `<div class="field stack info">${labelSpan(label, hint)}${body}</div>`;
}

// Track the selected section and whether the phone displays its detail or list.
const SECTION_STATE = { settings: { key: "", open: false }, providers: { key: "", open: false } };
const sectionState = (host) => SECTION_STATE[host.id === "settings" ? "settings" : "providers"];

// Each section row and card header carries one colored chip, as the studied reference does.
const SECTION_CHIPS = {
  routing: "route", affinity: "link", balance: "scale", limits: "gauge", optimization: "zap",
  privacy: "shield", catalog: "database", hooks: "plug", personalization: "palette",
  keys: "key", yaml: "code", providers: "cloud", maintenance: "wrench",
};
// The pane summary lines read like the reference header. The locale test needs the
// keys spelled out, so the map lists every section's summary key.
const SECTION_SUMMARIES = {
  routing: t("settings.routing.summary"),
  affinity: t("settings.affinity.summary"),
  balance: t("settings.balance.summary"),
  limits: t("settings.limits.summary"),
  optimization: t("settings.optimization.summary"),
  privacy: t("settings.privacy.summary"),
  catalog: t("settings.catalog.summary"),
  hooks: t("settings.hooks.summary"),
  personalization: t("settings.personalization.summary"),
  providers: t("settings.providers.summary"),
  keys: t("settings.keys.summary"),
  yaml: t("settings.yaml.summary"),
  maintenance: t("settings.maintenance.summary"),
};
// Provider rows share the providers tile: their names are data, not sections.
// A provider with a vendored mark draws that mark in the tile ink.
const PROVIDER_CHIPS = new Set([
  "free", "cloudflare", "gemini", "groq", "kilo", "mistral", "openrouter", "pollinations", "z-ai",
]);
const chipFor = (host, key) => {
  const known = SECTION_CHIPS[key];
  const provider = host === "provider-form" && !known && MARK_FILES.has(key);
  const name = known ?? (key === "free" ? "sparkles" : (host === "provider-form" ? SECTION_CHIPS.providers : ""));
  const tint = known || PROVIDER_CHIPS.has(key) ? key : "providers";
  if (!known && !provider && !name) return "";
  return `<span class="tile" data-chip="${esc(tint)}" aria-hidden="true">${
    provider ? mark(key, true) : icon(name)}</span>`;
};
const chip = (key) => chipFor("settings", key);

// List sections in the rail and display the selected section in the pane. The studied
// reference filters its sidebar with a search field and groups rows with blank breaks.
function sectionList(id, items, active, opts = {}) {
  // The reference groups its lists into stacked cards, one card per group.
  const groups = opts.groups ?? [items];
  const navs = groups.map((group) => `<nav class="sections" aria-label="${t("common.sections_label")}">${group.map(([key, label]) => actionButton(`${chipFor(id, key)}<span>${esc(label)}</span>`, {
    variant: key === active ? "on" : "",
    attrs: {
      "data-section": key,
      "aria-current": key === active ? "true" : null,
      title: id === "settings" ? (SECTION_SUMMARIES[key] ?? null) : null,
    },
  })).join("")}</nav>`).join("");
  const search = opts.search ? `<label class="rail-search">${icon("search")}<input id="settings-search" type="text"
    autocomplete="off" aria-label="${esc(t("settings.search_label"))}" placeholder="${esc(t("settings.search_label"))}"></label>` : "";
  return `<div class="rail">${search}${navs}<p class="rail-empty" hidden>${esc(t("settings.search_empty"))}</p></div>`;
}

// Hide the rail rows the search text excludes, as the reference sidebar search does.
function filterRail(host) {
  const navs = host.querySelectorAll(":scope > .rail > .sections");
  if (!navs.length) return;
  const query = (state.settingsSearch ?? "").trim().toLowerCase();
  let shown = 0;
  for (const nav of navs) {
    for (const button of nav.querySelectorAll("button")) {
      const hit = !query || button.textContent.toLowerCase().includes(query);
      button.hidden = !hit;
      shown += Number(hit);
    }
  }
  const empty = host.querySelector(":scope > .rail > .rail-empty");
  if (empty) empty.hidden = shown > 0;
}

// Name the back button for its destination.
function sectionPane(back, cards, actions = null) {
  const button = actionButton(`<span class="chev" aria-hidden="true">&lsaquo;</span><span class="back-label">${esc(back)}</span>`, {
    variant: "ghost back", attrs: { "data-back": true },
  });
  const tools = actions === null ? "" : `<div class="pane-actions" data-pane-actions>${actions}</div>`;
  // The reference heads a drill page with the back chevron and the page title.
  return `<div class="section-pane"><div class="pane-bar">${button}`
    + `<h1 class="pane-title" data-pane-title hidden></h1>${tools}</div>${cards}</div>`;
}

function providerPaneActions(section = sectionState("provider-form")) {
  const file = state.files[state.file];
  if (!file) return "";
  const names = Object.keys(state.forms[state.file] ?? {});
  const remove = file.main === false
    ? actionButton(t("action.delete_file_button"), { variant: "ghost danger", attrs: { id: "drop-provider" } }) : "";
  const yaml = section.open && names.length === 1 && section.key === names[0]
    ? actionButton(t("yaml.label"), { variant: "ghost", attrs: { "data-provider-section": "yaml" } }) : "";
  return `${remove}${yaml}`;
}

// The final YAML section has its own Save and includes content that the form cannot display.
// It also opens invalid files.
function yamlCard(editor, save, hint) {
  return `<div class="card yaml-view" data-section="yaml"><p class="sub">${esc(hint)}</p>`
    + `<textarea id="${editor}" spellcheck="false" aria-label="${t("common.file_text_label")}"></textarea>`
    + `<div class="editbar">${actionButton(t("action.save_button"), {
      variant: "primary", attrs: { id: save, disabled: true },
    })}`
    + `<span class="hint">${t("settings.yaml.save_hint")}</span></div></div>`;
}

// Slide the phone pane from the right and the list from the left.
function playSlide(host, open) {
  if (!phoneSection()) return;
  // A host without a rail slides the pane both ways.
  const arriving = host.querySelector(open || !host.querySelector(":scope > .rail") ? ".section-pane" : ".rail");
  if (!arriving) return;
  arriving.classList.remove("slide", "slide-back");
  void arriving.offsetWidth;
  arriving.classList.add(open ? "slide" : "slide-back");
}

// Activate one section button and card. On phones, leave the list.
function selectSection(host, key, open = sectionState(host).open) {
  // Settings always shows one card: an entry with no selection takes the first section.
  if (!key && host.id === "settings") {
    key = host.querySelector(":scope > .rail > .sections button")?.dataset.section ?? "";
  }
  const state_ = sectionState(host);
  const moved = state_.open !== open;
  state_.key = key;
  state_.open = open;
  host.dataset.detail = open ? "1" : "0";
  // Scope both queries: the settings host wraps the nested providers host.
  for (const button of host.querySelectorAll(":scope > .rail > .sections button")) {
    const on = button.dataset.section === key;
    button.classList.toggle("on", on);
    if (on) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  const list = host.id === "settings" ? key : "";
  const before = host.querySelector(":scope > .section-pane > [data-section]:not([hidden])")?.dataset.section;
  for (const card of host.querySelectorAll(":scope > .section-pane > [data-section]")) {
    card.hidden = open ? card.dataset.section !== key : card.dataset.section !== list;
  }
  // A desktop section change fades the arriving card in, as a phone slides it.
  const arrived = host.querySelector(":scope > .section-pane > [data-section]:not([hidden])");
  if (arrived && before && arrived.dataset.section !== before && !phoneSection()) replay(arrived);
  // A provider file names its list, while a selected provider names its detail page.
  if (host.id === "provider-form") {
    const file = state.files[state.file];
    const names = Object.keys(state.forms[state.file] ?? {});
    const title = host.querySelector(":scope > .section-pane > .pane-bar > [data-pane-title]");
    if (title) {
      title.hidden = !open && !state.fileOpen;
      title.textContent = open ? (key === "yaml" ? t("yaml.label") : key) : fileName(file?.path ?? "");
    }
    const back = host.querySelector(":scope > .section-pane > .pane-bar .back-label");
    if (back) {
      const label = open
        ? key === "yaml" && state.providerReturnKey ? state.providerReturnKey
          : names.length > 1 ? fileName(file?.path ?? "") : t("nav.providers_link")
        : t("nav.providers_link");
      back.textContent = label;
    }
    const tools = host.querySelector(":scope > .section-pane > .pane-bar > [data-pane-actions]");
    if (tools) tools.innerHTML = providerPaneActions(state_);
  }
  // The reference clears the sidebar mark while a drill page holds the pane.
  if (host.id === "provider-form" && sectionState($("settings")).key === "providers") {
    const outer = $("settings").querySelector(':scope > .rail > .sections button[data-section="providers"]');
    if (outer) {
      outer.classList.toggle("on", !open);
      if (!open) outer.setAttribute("aria-current", "true");
      else outer.removeAttribute("aria-current");
    }
  }
  // A phone drill page is titled by the section it opens, under the back label.
  const label = [...host.querySelectorAll(":scope > .rail > .sections button")]
    .find((button) => button.dataset.section === key)?.querySelector(":scope > span:not(.tile)")?.textContent ?? key;
  if (host.id === "settings") {
    const title = host.querySelector(":scope > .section-pane > .pane-bar > [data-pane-title]");
    if (title) {
      title.hidden = !open;
      title.textContent = open ? label : "";
    }
  }
  markCardScrollEdges();
  markSectionPaneEdges();
  if (!moved) return;
  // Start the slide and retain its flag for a pane rebuild.
  state_.slide = true;
  playSlide(host, open);
}

// Store the phone section in the hash so the back gesture returns to the list.
const phoneSection = () => matchMedia("(max-width: 900px)").matches;

function setProviderRoute(file, provider = null, push = phoneSection()) {
  const [path, query] = location.hash.split("?");
  const params = new URLSearchParams(query ?? "");
  params.set("section", "providers");
  if (file) params.set("file", file);
  else params.delete("file");
  if (provider) params.set("provider", provider);
  else params.delete("provider");
  const url = `${path}?${params.toString()}`;
  if (push) history.pushState({ section: provider ?? "providers", file: file ?? null, provider }, "", url);
  else history.replaceState(null, "", url);
}

function openSection(host, key) {
  if (host.id === "settings") {
    captureProviderYamlDraft();
    state.fileOpen = false;
    state.providerReturnKey = "";
    const inner = sectionState($("provider-form"));
    inner.key = "";
    inner.open = false;
    renderFiles();
    renderForm();
  }
  selectSection(host, key, true);
  // Animate section changes in Providers and Settings.
  replay([...host.querySelectorAll(".section-pane > .card")]
    .find((card) => card.dataset.section === key));
  // A nested pane changes height with its section, so refit the scroll bounds here.
  fitSectionPane();
  if (host.id === "provider-form") {
    setProviderRoute(state.files[state.file]?.path, key, phoneSection());
    return;
  }
  const [path, query] = location.hash.split("?");
  const params = new URLSearchParams(query ?? "");
  params.set("section", key);
  params.delete("provider");
  params.delete("file");
  const url = `${path}?${params.toString()}`;
  if (phoneSection()) history.pushState({ section: key }, "", url);
  else history.replaceState(null, "", url);
}

// Go back one drill level, or fall back to a clean hash on desktop.
function closeSection(host) {
  const section = sectionState(host);
  const key = section.key;
  captureProviderYamlDraft();
  if (host.id === "settings") {
    if (history.state?.section === key) return history.back();
    const [path] = location.hash.split("?");
    state.fileOpen = false;
    state.providerReturnKey = "";
    const inner = sectionState($("provider-form"));
    inner.key = "";
    inner.open = false;
    history.replaceState(null, "", path);
    selectSection(host, key, false);
    renderFiles();
    renderForm();
    return;
  }
  const file = state.files[state.file];
  if (history.state?.section === key || history.state?.provider === key
      || (!section.open && history.state?.file === file?.path)) return history.back();
  const names = Object.keys(state.forms[state.file] ?? {});
  if (section.open && key === "yaml" && state.providerReturnKey && names.includes(state.providerReturnKey)) {
    const provider = state.providerReturnKey;
    state.view = "form";
    state.providerReturnKey = "";
    selectSection(host, provider, true);
    renderFiles();
    renderForm();
    setProviderRoute(file?.path, provider, false);
  } else if (section.open && names.length > 1) {
    state.providerReturnKey = "";
    selectSection(host, "", false);
    renderFiles();
    renderForm();
    setProviderRoute(file?.path, null, false);
  } else {
    state.fileOpen = false;
    state.providerReturnKey = "";
    selectSection(host, "", false);
    renderFiles();
    renderForm();
    setProviderRoute(null, null, false);
  }
}

// Read the section hash for deep links and the phone back gesture.
function applySectionHash() {
  const [path, query] = location.hash.split("?");
  const host = path === "#/settings" ? $("settings") : null;
  if (!host) return;
  captureProviderYamlDraft();
  const params = new URLSearchParams(query ?? "");
  const key = params.get("section");
  const known = [...host.querySelectorAll(".sections button")].some((button) => button.dataset.section === key);
  if (key && known) selectSection(host, key, true);
  else if (key) {
    // Retain the selection until data arrives and the rail renders.
    const state_ = sectionState(host);
    state_.key = key;
    state_.open = true;
  } else selectSection(host, sectionState(host).key, false);
  const inner = $("provider-form");
  if (sectionState(host).key !== "providers") {
    state.fileOpen = false;
    state.view = "form";
    state.providerReturnKey = "";
    const innerState = sectionState(inner);
    innerState.key = "";
    innerState.open = false;
    renderFiles();
    renderForm();
    return;
  }
  // The file list can render before files arrive. The loader reapplies this hash after fetch.
  if (!state.files.length) return;
  const filePath = params.get("file");
  const pkey = params.get("provider");
  let index = filePath ? state.files.findIndex((file) => file.path === filePath) : -1;
  if (index < 0 && !filePath && pkey) {
    index = state.files.findIndex((file) => pkey === "yaml" ? file.error
      : Object.hasOwn(file.blocks ?? {}, pkey));
    if (index < 0 && pkey === "yaml") index = Math.min(state.file, state.files.length - 1);
  }
  if (index < 0) {
    state.fileOpen = false;
    state.view = "form";
    state.providerReturnKey = "";
    const innerState = sectionState(inner);
    innerState.key = "";
    innerState.open = false;
    renderFiles();
    renderForm();
    return;
  }
  state.file = index;
  state.fileOpen = true;
  showFileError(index);
  const blocks = state.forms[index];
  const names = Object.keys(blocks ?? {});
  let selected = pkey && (pkey === "yaml" || names.includes(pkey)) ? pkey : "";
  if (!selected && blocks == null) selected = "yaml";
  else if (!selected && names.length === 1) selected = names[0];
  const innerState = sectionState(inner);
  innerState.key = selected;
  innerState.open = Boolean(selected);
  state.providerReturnKey = selected === "yaml" && names.length === 1 ? names[0] : "";
  state.view = selected === "yaml" ? "yaml" : "form";
  renderFiles();
  renderForm();
  // Old provider-only links gain an explicit file path; single-provider files are canonical too.
  if ((!filePath && pkey) || (filePath && !pkey && selected && selected !== "yaml")) {
    setProviderRoute(state.files[index].path, selected, false);
  }
}

// Replay the phone back slide once after the form rebuild replaces the pane.
function slideAgain() {
  for (const host of [$("settings")]) {
    const state_ = sectionState(host);
    if (!state_.slide) continue;
    state_.slide = false;
    playSlide(host, state_.open);
  }
  // The nested providers host replays its own slide after a rebuild.
  const inner = $("provider-form");
  const nested = inner ? sectionState(inner) : null;
  if (nested?.slide) {
    nested.slide = false;
    playSlide(inner, nested.open);
  }
}

// A group of fields that starts closed, so a long card stays short.
function fold(label, hint, body) {
  const id = `hint-${++HINT_COUNT}`;
  const mark = `<span class="hint" aria-hidden="true">${icon("info")}</span>`;
  return `<details class="fold"><summary aria-describedby="${id}">${esc(label)}${mark}`
    + `<small id="${id}" role="tooltip" aria-hidden="true">${esc(hint)}</small></summary>`
    + `<div class="fold-body">${body}</div></details>`;
}

function providerCard(name, block) {
  const path = [name];
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}">
      <p class="sub">${t("provider.block.invalid_text")}</p></div>`;
  }
  const defaults = state.providerDefaults[name] || state.providerDefaults["*"] || {};
  const tokenInfo = (value) => {
    if (typeof value !== "string") return null;
    if (value.startsWith("env:")) return { type: "env", name: value.slice("env:".length), raw: value };
    if (value.startsWith("db:")) return { type: "db", name: value.slice("db:".length), raw: value };
    return null;
  };
  const effectiveState = (info, row) => {
    if (!row) return "missing";
    const hasSaved = row.has_saved ?? (row.state === "saved");
    const hasEnv = row.has_env ?? (row.state === "env");
    if (info && info.type === "env") return hasEnv ? "env" : "missing";
    if (info && info.type === "db") return hasSaved ? "saved" : "missing";
    return row.state;
  };
  const placeholderFor = (info, row) => {
    if (!info) return "";
    if (info.type === "env") {
      const eff = effectiveState(info, row);
      if (eff === "env") return t("provider.secret.environment_text");
      if (eff === "missing") return t("provider.secret.missing_environment_text");
      return "";
    }
    if (info.type === "db") {
      if (!row) return t("provider.secret.database_text");
      const hasSaved = row.has_saved ?? (row.state === "saved");
      if (hasSaved && row.start && row.length) {
        const rest = Math.max(0, row.length - row.start.length);
        return row.start + "•".repeat(rest);
      }
      return t("provider.secret.database_text");
    }
  };
  const text = (key, label, hint) => {
    let type = "text";
    let value = block[key] ?? "";
    let placeholder = defaults[key] ?? "";
    if (key === "api_key" || key === "account_id") {
      const info = tokenInfo(block[key]);
      if (info) {
        const row = state.env.find((entry) => entry.name === info.name);
        placeholder = placeholderFor(info, row) || defaults[key] || "";
        if (info.type === "env") value = info.raw;
        else {
          type = "password";
          value = "";
        }
      }
    }
    return field(label, hint, `<input class="text" type="${type}" readonly spellcheck="false" autocomplete="off"
      aria-label="${esc(label)}" aria-haspopup="dialog" aria-controls="modal" data-value="${esc(label)}" data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(value)}" placeholder="${esc(placeholder)}">`);
  };
  const tiers = TIERS.map((tier) => `<div class="tier-row"><span class="tier">${tier.slice(-1)}</span>
    ${listField(tier, block.tier?.[tier], [...path, "tier", tier])}</div>`).join("");
  const models = block.models && typeof block.models === "object" ? block.models : {};
  const overrides = Object.entries(models).map(([pattern, values]) => {
    // Put override hooks in separate rows and other keys in value pills.
    const rest = values && typeof values === "object" && !Array.isArray(values) ? { ...values } : values;
    if (rest && typeof rest === "object" && !Array.isArray(rest)) delete rest.hooks;
    return `<div class="override">
      <input class="text" type="text" spellcheck="false" value="${esc(pattern)}" aria-label="${t("provider.model_pattern.label")}" title="${esc(PATTERN_HINT)}"
        data-pattern='${esc(JSON.stringify([...path, "models"]))}' data-key="${esc(pattern)}">
      <div class="pills">${mapPills(rest, [...path, "models", pattern], "override", ": ")}</div>
      ${hookFormRows(values?.hooks, [...path, "models", pattern, "hooks"], "model", pattern)}
      ${actionButton(icon("x"), {
        variant: "ghost",
        attrs: {
          "aria-label": t("provider.pattern.delete_title"),
          title: t("provider.pattern.delete_title"),
          "data-drop": JSON.stringify([...path, "models", pattern]),
        },
      })}
    </div>`;
  }).join("");
  const others = Object.keys(block).filter((key) => !FORM_KEYS.includes(key) && key !== "_file");
  const values = others.map((key) => pill(`${key}: ${shown(block[key])}`, path, key)).join("") + adder(path, "column", t("action.add_key_button"));
  const accountField = name === "cloudflare"
    ? text("account_id", t("provider.account_id.label"), t("provider.account_id.hint"))
    : "";
  return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}">
    <p class="sub">${t("provider.secret.note")}</p>
    ${text("api_key", t("provider.api_key.label"))}
    ${accountField}
    ${field(t("provider.client_keys.label"), t("provider.client_keys.hint"), `<div class="pills">${mapPills(block.client_keys, [...path, "client_keys"], "match", " = ")}</div>`)}
    ${text("api_base", t("provider.api_base.label"), t("provider.api_base.hint"))}
    ${text("api_type", t("provider.api_type.label"), t("provider.api_type.hint"))}
    ${text("discovery_url", t("provider.discovery_url.label"), t("provider.discovery_url.hint"))}
    ${field(t("provider.discovery_match.label"), t("provider.discovery_match.hint"), `<div class="pills">${mapPills(block.discovery_match, [...path, "discovery_match"], "match", " = ")}</div>`)}
    ${field(t("provider.exclude.label"), t("provider.exclude.hint", { pattern_hint: PATTERN_HINT }), listField("exclude", block.exclude, [...path, "exclude"]))}
    ${fold(t("provider.tiers.label"), t("provider.tiers.hint", { pattern_hint: PATTERN_HINT }), tiers)}
    ${fold(t("provider.model_overrides.label"), t("provider.model_overrides.hint", { pattern_hint: PATTERN_HINT }), `${overrides}<div class="pills">${adder([...path, "models"], "pattern", t("action.add_pattern_button"))}</div>`)}
    ${fold(t("provider.hooks.label"), t("provider.hooks.hint"), hookFormRows(block.hooks, [name, "hooks"], "provider", name))}
    ${fold(t("provider.provider_values.label"), t("provider.provider_values.hint"), `<div class="pills">${values}</div>`)}
  </div>`;
}

// List provider files, provider blocks, and the YAML editor as separate drill pages.
function renderForm() {
  const host = $("provider-form");
  const file = state.files[state.file];
  if (!state.fileOpen || !file) {
    host.hidden = true;
    host.dataset.fileOpen = "0";
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  host.dataset.fileOpen = "1";
  const blocks = state.forms[state.file];
  const sameFile = host.dataset.filePath === file.path;
  const typed = state.view === "yaml" && sameFile ? $("editor")?.value : undefined;
  const section = sectionState(host);
  const names = blocks == null ? [] : Object.keys(blocks);
  const actions = providerPaneActions(section);
  const back = section.key === "yaml" && state.providerReturnKey
    ? state.providerReturnKey : names.length > 1 ? fileName(file.path) : t("nav.providers_link");
  host.dataset.filePath = file.path;
  if (blocks == null) {
    host.innerHTML = sectionPane(t("nav.providers_link"), `<div class="provider-file-error">
      <h3>${t("provider.yaml.invalid_text")}</h3>
      <p class="sub">${esc(file.error ?? "")}</p>
      <p class="sub">${t("provider.yaml.fix_text")}</p></div>`
      + yamlCard("editor", "yaml-save", t("provider.yaml.file_hint", { file: fileName(file.path) })), actions);
    selectSection(host, "yaml", true);
    $("editor").value = typed ?? file.text;
    renderFiles();
    placeUndo();
    fitSectionPane();
    return;
  }
  const items = names.map((name) => [name, name]).concat([["yaml", t("yaml.label")]]);
  const selected = items.some(([key]) => key === section.key) ? section.key : "";
  const open = Boolean(section.open && selected);
  const list = `<div class="card list" data-section=""><nav class="sections drill provider-sections"
    aria-label="${esc(t("common.sections_label"))}">${items.map(([key, label]) => actionButton(`${chipFor("provider-form", key)}<span>${esc(label)}</span>`, {
    attrs: { "data-section": key },
  })).join("")}</nav></div>`;
  host.innerHTML = sectionPane(open ? back : t("nav.providers_link"), list
    + names.map((name) => providerCard(name, blocks[name])).join("")
    + yamlCard("editor", "yaml-save", t("provider.yaml.file_hint", { file: fileName(file.path) })), actions);
  selectSection(host, selected, open);
  $("editor").value = typed ?? file.text;
  renderFiles();
  placeUndo();
  fitSectionPane();
}

// Replace the edited card in place and retain its selection.
function renderCard(name) {
  const card = [...document.querySelectorAll("#provider-form [data-provider]")].find((item) => item.dataset.provider === name);
  if (card) card.outerHTML = providerCard(name, state.forms[state.file][name]);
  selectSection($("provider-form"), sectionState($("provider-form")).key);
  renderFiles();
  placeUndo();
}

// Return the path parent and create missing maps and lists.
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
  const choices = state.overrideKeys;
  const keys = choices.map((key) => `<option>${esc(key)}</option>`).join("");
  const placeholder = {
    list: t("provider.adder.pattern_placeholder"),
    pattern: t("provider.adder.model_pattern_placeholder"),
    override: t("provider.adder.value_placeholder"),
  }[kind];
  const hint = kind === "list" || kind === "pattern" ? PATTERN_HINT : "";
  box.innerHTML = `${kind === "override" ? `<select aria-label="${t("provider.key.label")}">${keys}</select>` : ""}
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
    // Ignore repeated values.
    if (!list.some((value) => String(value) === text)) list.push(parsed(text));
  } else if (kind === "pattern") {
    const models = parentOf([...path, text], {});
    if (text in models) {
      showWrite("providers", t("provider.pattern.exists_text", { pattern: text }), true);
      kept = false;
    } else models[text] = {};
  } else {
    parentOf([...path, key], {})[key] = parsed(text);
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
    showWrite("providers", after
      ? t("provider.pattern.exists_text", { pattern: after })
      : t("provider.pattern.required_text"), true);
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

// Keep the previous file text and changed row. Put Undo beside that row.
function rememberWrite(page, restore, anchor = null) {
  lastWrite = {
    page, ...restore,
    at: Date.now(),
    section: anchor?.closest("[data-section]")?.dataset.section ?? "",
    // Retain the row key to restore its Undo icon after rendering.
    key: controlKey(anchor),
  };
  const old = document.querySelector(".undo-text");
  if (old) old.remove();
  if (!anchor) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost undo-text";
  button.title = t("action.undo_write_label");
  button.setAttribute("aria-label", t("action.undo_write_label"));
  button.textContent = t("action.undo_write_label");
  button.addEventListener("click", (event) => {
    // Stop the click before it reaches the switch label.
    event.preventDefault();
    event.stopPropagation();
    undoWrite();
  });
  lastWrite.button = button;
  placeUndo(anchor);
}

// Identify the changed row by its field key after a render.
function controlKey(anchor) {
  if (!anchor) return "";
  const set = anchor.matches?.("[data-set]") ? anchor : anchor.querySelector?.("[data-set]");
  if (set?.dataset?.set) return set.dataset.set;
  const named = anchor.matches?.("input[id], select[id]") ? anchor : anchor.querySelector?.("input[id], select[id]");
  return named?.id ?? "";
}

function writeRow(anchor = null) {
  const again = lastWrite?.key
    ? [...document.querySelectorAll("[data-set]")].find((node) => node.dataset.set === lastWrite.key)?.closest(".field")
      ?? document.getElementById(lastWrite.key)?.closest(".field")
    : null;
  const target = (anchor?.isConnected ? anchor : null) ?? again
    ?? document.querySelector(`.section-pane > .card[data-section="${lastWrite?.section}"]`);
  return target ? (target.closest(".field") ?? target) : null;
}

// The icon sits at the top right of the row that changed. A render of that card replaces the
// row, so the icon falls back to the header of the card.
function placeUndo(anchor = null) {
  if (!lastWrite?.button) return;
  if (lastWrite.page === "settings") return placeSaveLine(anchor);
  const row = writeRow(anchor);
  if (!row) return;
  // Put Undo between the label and control without moving the label or row.
  const control = row.querySelector(".input, .pills, select, input");
  if (control) row.insertBefore(lastWrite.button, control);
  else row.prepend(lastWrite.button);
}

let saveTimer = null;

// Report the write on the changed row, with the time since it landed.
function placeSaveLine(anchor = null) {
  if (lastWrite?.page !== "settings" || !lastWrite.button) return;
  const row = writeRow(anchor);
  if (!row) return;
  let line = row.querySelector(".save-line");
  if (!line) {
    line = document.createElement("span");
    line.className = "save-line";
    const text = document.createElement("span");
    text.className = "saved";
    line.append(text, lastWrite.button);
    const control = row.querySelector(".input, .pills, select, input");
    if (control) row.insertBefore(line, control);
    else row.prepend(line);
  }
  const text = line.querySelector(".saved");
  if (text) text.textContent = t("settings.saved_text", { when: relative(lastWrite.at / 1000) });
  if (saveTimer === null) saveTimer = setInterval(tickSaveLine, 20000);
}

// Age the saved line while the write stays undoable.
function tickSaveLine() {
  const text = document.querySelector(".save-line .saved");
  if (!text || !lastWrite?.at) {
    if (saveTimer !== null) clearInterval(saveTimer);
    saveTimer = null;
    return;
  }
  text.textContent = t("settings.saved_text", { when: relative(lastWrite.at / 1000) });
}

// Remove the Undo control when no write can be undone.
function dropUndo() {
  lastWrite = null;
  document.querySelector(".undo-text")?.remove();
  document.querySelector(".save-line")?.remove();
  if (saveTimer !== null) clearInterval(saveTimer);
  saveTimer = null;
}

async function undoWrite() {
  if (!lastWrite) return;
  const write = lastWrite;
  dropUndo();
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
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
    showWrite(write.page, error.message, true);
  }
}

async function saveForm() {
  if (!dirty()) return clearWrite("providers");
  const index = state.file;
  const file = state.files[index];
  rememberWrite("providers", { index, path: file.path, text: file.text }, state.writeAnchor);
  try {
    await call("providers", { method: "PUT", body: JSON.stringify({ path: file.path, blocks: pruned(state.forms[index]) }) });
    await takeFile(index);
    clearWrite("providers");
    await refreshEnv();
    renderForm();
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
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
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
    showWrite("providers", error.message, true);
  }
  renderFiles();
}

// Use YAML view for the selected YAML card: the settings file or the provider file.
const yamlView = () => {
  const key = sectionState($("settings")).key;
  if (key === "yaml") return state.settingsView === "yaml";
  const provider = sectionState($("provider-form"));
  return key === "providers" && provider.open && provider.key === "yaml" && state.view === "yaml";
};

// Switch between form and YAML views. Confirm before discarding unsaved YAML and restoring the saved file.
async function selectProviderSection(key) {
  const host = $("provider-form");
  const section = sectionState(host);
  const view = key === "yaml" ? "yaml" : "form";
  let returnKey = state.providerReturnKey;
  if (key === "yaml" && section.open && section.key && section.key !== "yaml") returnKey = section.key;
  else if (key !== "yaml") returnKey = "";
  if (view !== state.view) {
    if (dirty() && !(await ask(t("dialog.discard.title"), t("dialog.discard.message"), t("action.discard_button"), true))) return;
    const index = state.file;
    if (state.view === "yaml") state.files[index].text = state.saved[index];
    state.forms[index] = clone(state.files[index].blocks ?? {});
    state.view = view;
    clearWrite("providers");
  }
  state.providerReturnKey = returnKey;
  state.fileOpen = true;
  section.key = key;
  renderFiles();
  renderForm();
  // Build the form before the arrival animation.
  openSection(host, key);
}

// Hook surfaces available in the form, in backend order.
const HOOK_SURFACES = [
  ["on-catalog", t("hook.surface.catalog.label")],
  ["on-request", t("hook.surface.request.label")],
  ["on-prompt", t("hook.surface.prompt.label")],
  ["on-upstream", t("hook.surface.upstream.label")],
  ["on-answer", t("hook.surface.answer.label")],
  ["on-chunk", t("hook.surface.chunk.label")],
];

// The Settings form: [group, title, [[key, label, unit, hint], ...]].
const SETTINGS = [
  ["routing", t("settings.routing.title"), [
    ["threshold", t("settings.routing.threshold.label"), "", t("settings.routing.threshold.hint")],
    ["escalation", t("settings.routing.escalation.label"), "list", t("settings.routing.escalation.hint")],
    ["switch", t("settings.routing.switch.label"), "list", t("settings.routing.switch.hint")],
  ]],
  // The fifth row element lists its visible modes. Always display `mode`.
  ["affinity", t("settings.affinity.title"), [
    ["mode", t("settings.affinity.mode.label"), "choice", t("settings.affinity.mode.hint")],
    ["change_on_draw", t("settings.affinity.change_on_draw.label"), "", t("settings.affinity.change_on_draw.hint"), ["session"]],
    ["idle", t("settings.affinity.idle.label"), "s", t("settings.affinity.idle.hint"), ["session", "race"]],
    ["stay", t("settings.affinity.stay.label"), "", t("settings.affinity.stay.hint"), ["session"]],
    ["count", t("settings.affinity.count.label"), "", t("settings.affinity.count.hint"), ["race"]],
    ["chance", t("settings.affinity.race_chance.label"), "", t("settings.affinity.race_chance.hint"), ["race"]],
    ["slow", t("settings.affinity.race_slow.label"), "s", t("settings.affinity.race_slow.hint"), ["race"]],
    ["penalty", t("settings.affinity.penalty.label"), "x", t("settings.affinity.penalty.hint"), ["race"]],
  ]],
  ["balance", t("settings.balance.title"), [
    ["weights", t("settings.balance.weights.label"), "", t("settings.balance.weights.hint")],
    ["success", t("settings.balance.success.label"), "x", t("settings.balance.success.hint")],
    ["fault", t("settings.balance.fault.label"), "x", t("settings.balance.fault.hint")],
    ["slow", t("settings.balance.slow.label"), "x", t("settings.balance.slow.hint")],
    ["hourly", t("settings.balance.hourly.label"), "x", t("settings.balance.hourly.hint")],
    ["rate_limit", t("settings.balance.rate_limit.label"), "x", t("settings.balance.rate_limit.hint")],
    ["first", t("settings.balance.first.label"), "s", t("settings.balance.first.hint")],
    ["longest", t("settings.balance.longest.label"), "s", t("settings.balance.longest.hint")],
    ["pacing", t("settings.balance.pacing.label"), "", t("settings.balance.pacing.hint")],
  ]],
  ["limits", t("settings.limits.title"), [
    ["request", t("settings.limits.request.label"), "s", t("settings.limits.request.hint")],
    ["wait", t("settings.limits.wait.label"), "s", t("settings.limits.wait.hint")],
    ["slow", t("settings.limits.slow.label"), "s", t("settings.limits.slow.hint")],
    ["calls", t("settings.limits.calls.label"), "", t("settings.limits.calls.hint")],
    ["repeats", t("settings.limits.repeats.label"), "", t("settings.limits.repeats.hint")],
    ["shortest", t("settings.limits.shortest.label"), "", t("settings.limits.shortest.hint")],
    ["longest", t("settings.limits.longest.label"), "", t("settings.limits.longest.hint")],
  ]],
  ["optimization", t("settings.optimization.title"), [
    ["enabled", t("settings.optimization.enabled.label"), "", t("settings.optimization.enabled.hint")],
    ["timeout", t("settings.optimization.timeout.label"), "s", t("settings.optimization.timeout.hint")],
  ]],
  ["privacy", t("settings.privacy.title"), [
    ["forward_owui_chat_id", t("settings.privacy.forward_chat_id.label"), "", t("settings.privacy.forward_chat_id.hint")],
  ]],
  ["catalog", t("settings.catalog.title"), [
    ["every", t("settings.catalog.every.label"), "h", t("settings.catalog.every.hint")],
    ["anchor", t("settings.catalog.anchor.label"), "h", t("settings.catalog.anchor.hint")],
  ]],
  // Keep request surfaces in the hooks table and only the manager in this card.
  ["hooks", t("settings.hooks.title"), []],
  ["personalization", t("settings.personalization.title"), [
    ["tier-a", t("settings.personalization.tier_a.label"), "name", t("settings.personalization.tier_a.hint")],
    ["tier-b", t("settings.personalization.tier_b.label"), "name", t("settings.personalization.tier_b.hint")],
    ["tier-c", t("settings.personalization.tier_c.label"), "name", t("settings.personalization.tier_c.hint")],
    ["tier-d", t("settings.personalization.tier_d.label"), "name", t("settings.personalization.tier_d.hint")],
    ["audio", t("settings.personalization.audio.label"), "name", t("settings.personalization.audio.hint")],
    ["images", t("settings.personalization.image.label"), "name", t("settings.personalization.image.hint")],
    ["theme", t("settings.personalization.theme.label"), "choice", t("settings.personalization.theme.hint")],
    ["time_format", t("settings.personalization.time_format.label"), "choice", t("settings.personalization.time_format.hint")],
  ]],
];

// The Settings cards of each column, from top to bottom.
// The options of each choice field.
const CHOICES = {
  mode: [
    ["none", t("settings.choice.none_option")],
    ["session", t("settings.choice.session_option")],
    ["race", t("settings.choice.race_option")],
  ],
  theme: [
    ["system", t("settings.choice.system_option")],
    ["light", t("settings.choice.light_option")],
    ["dark", t("settings.choice.dark_option")],
  ],
  time_format: [
    ["24h", t("settings.time_format.hour24_option")],
    ["12h", t("settings.time_format.hour12_option")],
  ],
};

// System theme follows the device, including before login.
const darkDevice = matchMedia("(prefers-color-scheme: dark)");
let theme = "system";
function applyTheme(value = theme) {
  theme = value;
  const dark = theme === "dark" || (theme === "system" && darkDevice.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
darkDevice.addEventListener("change", () => applyTheme());
applyTheme();

// These fields accept decimals. Other fields accept whole numbers.
const DECIMALS = new Set([
  "affinity.stay", "affinity.chance", "affinity.penalty",
  "balance.success", "balance.fault", "balance.slow", "balance.hourly", "balance.rate_limit",
  "routing.threshold",
]);
// The server rejects values outside these bounds before saving.
const MINIMA = { "limits.calls": 2, "limits.repeats": 2, "limits.shortest": 1, "limits.longest": 1, "affinity.count": 1 };
const MAXIMA = {
  "catalog.anchor": 23,
  "limits.request": 86400,
  "limits.wait": 86400,
  "limits.slow": 86400,
  "optimization.timeout": 86400,
  "affinity.count": 10,
  "affinity.chance": 1,
  "affinity.penalty": 1,
  "routing.threshold": 1,
  "limits.calls": 100,
  "limits.repeats": 16,
  "limits.shortest": 1000,
  "limits.longest": 10000,
};

// Return the file value, or null if absent.
const fileValue = (group, key) => state.settings.file?.[group]?.[key] ?? null;
const setting = (group, key) => fileValue(group, key) ?? state.settings.defaults[group][key];
// Normalize file lists, including a single path from an older file.
const settingList = (group, key) => {
  const found = setting(group, key);
  return Array.isArray(found) ? found : found ? [found] : [];
};
// The boolean settings, such as `enabled` and `change_on_draw`, are checkboxes in the form.
const isSwitch = (group, key) => typeof state.settings.defaults[group][key] === "boolean";

// The Settings API keys card contains the form, key display, and table.
function keysCard() {
  return `<div class="card" data-section="keys">
    <form class="toolbar" id="new-key">
      <input id="key-name" maxlength="40" placeholder="${t("settings.keys.new_placeholder")}" required>
      ${actionButton(t("action.new_key_button"), { variant: "primary", type: "submit" })}
      <span class="message bad" id="key-message" role="alert"></span>
    </form>
    <div class="reveal" id="reveal" hidden>
      <span>${t("settings.keys.copy_hint", { name: '<b id="reveal-name"></b>' })}</span>
      <code id="reveal-key"></code>
      ${actionButton(t("action.copy_button"), { variant: "ghost", attrs: { id: "copy" } })}
      ${actionButton(t("action.done_button"), { variant: "ghost", attrs: { id: "reveal-close" } })}
    </div>
    <div class="card-scroll scroll-fade"><table class="keys">
      <thead><tr>
        <th>${t("settings.keys.name_column")}</th><th>${t("settings.keys.key_column")}</th><th class="hide-sm">${t("settings.keys.created_column")}</th><th>${t("settings.keys.last_used_column")}</th><th></th>
      </tr></thead>
      <tbody id="keys"></tbody>
    </table></div>
  </div>`;
}

function renderSettings() {
  // Preserve text during an edit. Otherwise, display the saved text.
  const typed = state.settingsView === "yaml" ? $("settings-editor")?.value : undefined;
  // Restore the scroll position after the card rebuild.
  const scroll = settingsScroll();
  const card = ([group, title, fields]) => `
    <div class="card">${fields.map(([key, label, unit, hint]) => {
      const id = `set-${group}-${key}`;
      if (isSwitch(group, key)) {
        // Place the label and hint before the right-aligned switch.
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
            aria-label="${esc(label)}" aria-haspopup="dialog" aria-controls="modal" data-value="${esc(label)}" spellcheck="false"
            value="${esc(fileValue(group, key) ?? "")}" placeholder="${esc(state.settings.defaults[group][key])}"></span></label>`;
      }
      if (unit === "list") {
        const values = listValue(group, key);
        return field(label, hint, `
          <div class="pills" id="${id}" aria-label="${esc(label)}">${values
            .map((value, index) => settingPill(group, key, value, index)).join("")}${settingAdder(group, key)}</div>`);
      }
      const fallback = state.settings.defaults[group][key];
      const value = fileValue(group, key);
      return `<label class="field info" for="${id}">${labelSpan(label, hint)}
        <span class="input"><input type="number" readonly aria-label="${esc(label)}" aria-haspopup="dialog" aria-controls="modal" data-value="${esc(label)}"
          min="${MINIMA[`${group}.${key}`] ?? 0}" id="${id}" value="${value ?? ""}"
          step="${DECIMALS.has(`${group}.${key}`) ? "any" : "1"}" ${MAXIMA[`${group}.${key}`] ? `max="${MAXIMA[`${group}.${key}`]}"` : ""}
          placeholder="${fallback ?? ""}"><i>${esc(unit)}</i></span></label>`;
    }).join("")}${group === "hooks" ? hooksManager() : ""}</div>`;
  // List groups in the rail and put their cards in the pane.
  const groups = SETTINGS;
  const items = groups.map(([group, title]) => [group, title]).concat([
    ["providers", t("nav.providers_link")], ["keys", t("settings.keys.title")], ["yaml", t("yaml.label")],
    ["maintenance", t("settings.maintenance.title")],
  ]);
  const selected = sectionState($("settings")).key;
  const active = items.some(([key]) => key === selected) ? selected : items[0][0];
  // The rail groups like the reference lists: routing, operations, looks, sources, care.
  const bounds = [["routing", "optimization"], ["privacy", "hooks"], ["personalization", "personalization"],
    ["providers", "yaml"], ["maintenance", "maintenance"]];
  const railGroups = bounds.map(([first, last]) => {
    const start = items.findIndex(([key]) => key === first);
    const end = items.findIndex(([key]) => key === last);
    return items.slice(start, end + 1);
  });
  // Grab the live providers nodes first: the wipe detaches the card that holds them.
  const filesHost = $("files");
  const formHost = $("provider-form");
  $("settings").innerHTML = sectionList("settings", items, active, { search: true, groups: railGroups })
    + sectionPane(t("nav.settings_link"), groups.map(([group, title, fields]) => card([group, title, fields]).replace(
      '<div class="card">', `<div class="card" data-section="${esc(group)}">`)).join("")
      + `<div class="provider-flow" data-section="providers"><div id="providers-slot"></div></div>`
      + keysCard()
      + yamlCard("settings-editor", "settings-yaml-save", t("settings.yaml.hint", { file: fileName(state.settings.path) }))
      + maintenanceCard());
  // The providers host keeps its listeners: move the live nodes into the fresh card.
  // Grab them before the wipe, since a rebuild detaches the card that holds them.
  const slot = $("providers-slot");
  if (slot && filesHost && formHost) {
    slot.after(filesHost, formHost);
    slot.remove();
  }
  selectSection($("settings"), active);
  const railSearch = $("settings-search");
  if (railSearch) {
    railSearch.value = state.settingsSearch ?? "";
    railSearch.addEventListener("input", () => {
      state.settingsSearch = railSearch.value;
      filterRail($("settings"));
    });
    filterRail($("settings"));
  }
  renderFiles();
  renderForm();
  $("settings-editor").value = typed ?? state.settings.text;
  // Use the rendered file's mode to select visible rows.
  showAffinityRows(setting("affinity", "mode"));
  showSwitchRows();
  // The rebuild wipes the injected table bodies, so redraw them.
  renderKeys(state.keys ?? []);
  renderSettingsSave();
  placeUndo();
  fitSectionPane();
  keepSettingsScroll(scroll);
  markCardScrollEdges();
}

// Display rows only in their listed modes: race values in race, and pin values in session or race.
// Always display `mode` and retain hidden rows with their values.
function showAffinityRows(mode = $("set-affinity-mode")?.value) {
  for (const [key, , , , show] of SETTINGS.find(([group]) => group === "affinity")[2]) {
    const row = $(`set-affinity-${key}`)?.closest(".field");
    if (row) row.hidden = Boolean(show && !show.includes(mode));
  }
}

// A card switch disables its dependent rows. Pacing is independent of weights.
function showSwitchRows() {
  for (const [group, , fields] of SETTINGS) {
    if (!fields.length) continue;
    const [toggle] = fields[0];
    if (!isSwitch(group, toggle)) continue;
    const off = !$(`set-${group}-${toggle}`)?.checked;
    for (const [key] of fields) {
      if (key === toggle || (group === "balance" && key === "pacing")) continue;
      const field = $(`set-${group}-${key}`)?.closest(".field");
      if (!field) continue;
      field.classList.toggle("off", off);
      field.querySelectorAll("input, select, button").forEach((node) => { node.disabled = off; });
    }
  }
}

function settingsChanges() {
  if (!state.settings) return {};
  const changes = {};
  for (const [group, , fields] of SETTINGS) {
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
  // Include the hook folder, sources, disabled names, and surface order.
  const dir = document.getElementById("set-hooks-dir");
  if (dir && dir.value.trim() && dir.value.trim() !== setting("hooks", "dir")) (changes.hooks ||= {}).dir = dir.value.trim();
  const sources = listValue("hooks", "sources");
  if (JSON.stringify(sources) !== JSON.stringify(settingList("hooks", "sources"))) (changes.hooks ||= {}).sources = sources;
  const disabled = listValue("hooks", "disabled");
  if (JSON.stringify(disabled) !== JSON.stringify(settingList("hooks", "disabled"))) (changes.hooks ||= {}).disabled = disabled;
  const order = hookOrderState();
  if (JSON.stringify(order) !== JSON.stringify(setting("hooks", "order"))) (changes.hooks ||= {}).order = order;
  // The rows of the table write the request surfaces: the list holds the files of that surface.
  for (const [surface] of requestSurfaces()) {
    if (!(state.settings.lists || {})[`hooks.${surface}`]) continue;
    const values = listValue("hooks", surface).filter(Boolean);
    if (JSON.stringify(values) !== JSON.stringify(settingList("hooks", surface))) (changes.hooks ||= {})[surface] = values.length ? values : null;
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
  // Save writes YAML text. Form rows save on change or close.
  const cardSave = $("settings-yaml-save");
  if (cardSave) cardSave.disabled = !yaml || !settingsDirty();
}

async function loadSettings({ render = true } = {}) {
  state.settings = await call("settings");
  state.settings.lists = {};
  renderLegend();
  applyTheme(setting("personalization", "theme"));
  hourCycle = setting("personalization", "time_format") === "12h" ? "h12" : "h23";
  // A saved switch already holds its state in the page: keep the node, so its knob and the row
  // it fades reach the end of the motion instead of being replaced on the first frame.
  if (render) renderSettings();
}

// A selection sets the view of the page. Unsaved changes of the other view go after a confirmation.
async function selectSettingsSection(key) {
  const view = key === "yaml" ? "yaml" : "form";
  if (view !== state.settingsView) {
    if (settingsDirty() && !(await ask(t("dialog.discard.title"), t("dialog.discard.message"), t("action.discard_button"), true))) return;
    state.settingsView = view;
    clearWrite("settings");
    renderSettings();
  }
  openSection($("settings"), key);
}

async function saveSettings(keepForm = false) {
  // Skip unchanged values and remove the previous Undo.
  if (!settingsDirty()) {
    dropUndo();
    return clearWrite("settings");
  }
  const text = $("settings-editor")?.value ?? "";
  const body = state.settingsView === "yaml" ? { text } : { changes: settingsChanges() };
  rememberWrite("settings", { settings: true, text: state.settings.text }, state.writeAnchor ?? $("settings-yaml-save"));
  try {
    await call("settings", { method: "PUT", body: JSON.stringify(body) });
    await loadSettings({ render: !keepForm });
    clearWrite("settings");
    placeSaveLine();
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
    showWrite("settings", error.message, true);
  }
  renderSettingsSave();
}

async function refreshFast() {
  // Keep the last values after a failed call and mark the page state.
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
// Use short counts from 100,000, such as 998.8M, to keep space for the bar.

// The short unit of a rate-limit row, for example tok/min.
const SPANS = {
  minute: t("limits.span.minute.short_text"),
  hour: t("limits.span.hour.short_text"),
  day: t("limits.span.day.short_text"),
  month: t("limits.span.month.short_text"),
};
const unit = (r) => t("limits.unit.rate_short_text", {
  unit: r.kind === "tokens" ? t("limits.unit.tokens.short_text") : t("limits.unit.requests.short_text"),
  span: r.span ? `/${SPANS[r.span] || r.span}` : "",
});
const SPAN_SHORT = {
  minute: t("limits.span.minute.initial_text"),
  hour: t("limits.span.hour.initial_text"),
  day: t("limits.span.day.initial_text"),
  month: t("limits.span.month.initial_text"),
};
const SPAN_WORD = {
  minute: t("limits.span.minute.label"),
  hour: t("limits.span.hour.label"),
  day: t("limits.span.day.label"),
  month: t("limits.span.month.label"),
};
function limitUnit(r) {
  if (r.kind === "neurons") return t("limits.balance.neurons_per_day_text");
  if (!SPAN_SHORT[r.span]) return r.kind;
  return t("limits.unit.rate_initial_text", {
    unit: r.kind === "tokens" ? t("limits.unit.tokens.initial_text") : t("limits.unit.requests.initial_text"),
    span: SPAN_SHORT[r.span],
  });
}
// The full name of a short unit, for the hover text of the Limit column, for example Requests per day.
function limitTitle(r) {
  const kind = r.kind === "neurons"
    ? t("limits.unit.neurons_text")
    : r.kind === "tokens"
      ? t("limits.unit.tokens_text")
      : t("limits.unit.requests_text");
  return r.span ? t("limits.unit.per_text", { unit: kind, span: SPAN_WORD[r.span] || r.span }) : kind;
}

// A time cell of the Limits table: the relative time, with the exact stamp on hover. An empty
// value keeps the dash.
const limitTimeCell = (seconds, cls) => (seconds
  ? timeText(seconds, { tag: "td", variant: cls })
  : `<td class="${cls}">-</td>`);

// The phone line of a limit row: the kind, its reset, and the last check, in one sentence.
const limitMeta = (r) => esc([
  limitUnit(r),
  r.reset
    ? (r.reset * 1000 > Date.now()
      ? t("limits.meta.reset_text", { when: relative(r.reset) })
      : t("limits.meta.reset_past_text", { when: relative(r.reset) }))
    : "",
  r.at ? t("limits.meta.seen_text", { when: relative(r.at) }) : "",
].filter(Boolean).join(" \u00b7 "));

const BALANCE_LABELS = {
  credit: () => t("limits.balance.credit.label"),
  usage: () => t("limits.balance.usage.label"),
  free_requests: () => t("limits.balance.free_requests.label"),
  balance: () => t("limits.balance.balance.label"),
  pollen: () => t("limits.balance.pollen.label"),
  neurons: () => t("limits.balance.neurons.label"),
  hourly_requests: () => t("limits.balance.hourly_requests.label"),
};

const BALANCE_VALUE_LENGTH = 10;

function balanceNumber(value, options = {}, compact = true) {
  const full = value.toLocaleString(undefined, options);
  if (!compact || full.length <= BALANCE_VALUE_LENGTH) return full;
  const shortOptions = { maximumFractionDigits: 1, roundingMode: "trunc", useGrouping: false };
  const short = value.toLocaleString(undefined, { ...shortOptions, notation: "compact" });
  return short.length <= BALANCE_VALUE_LENGTH ? short
    : value.toLocaleString(undefined, { ...shortOptions, notation: "scientific" });
}

function balanceCount(value) {
  const options = { maximumFractionDigits: 0, roundingMode: "halfEven" };
  const shown = value >= 1000 ? floorCount(value) : value.toLocaleString(undefined, options);
  return shown.length <= BALANCE_VALUE_LENGTH ? shown : balanceNumber(value, options);
}

function balanceValue(item, compact = true) {
  const decimal = value => balanceNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 }, compact);
  const money = value => t("limits.value.money_text", { value: decimal(value) });
  if (item.kind === "credit") return t("limits.value.of_text", { remaining: money(item.value), limit: money(item.limit) });
  if (item.kind === "usage" || item.kind === "balance") return money(item.value);
  if (item.kind === "pollen") return decimal(item.value);
  if (item.kind === "hourly_requests") return t("limits.value.of_text", {
    remaining: balanceNumber(BigInt(item.value), {}, compact), limit: balanceNumber(BigInt(item.limit), {}, compact),
  });
  const count = value => compact ? balanceCount(value)
    : value.toLocaleString(undefined, { maximumSignificantDigits: 21 });
  return t("limits.value.remaining_text", { remaining: count(item.value), limit: count(item.limit) });
}

// Display balances, then the three rate-limit rows with the lowest remaining shares.
function overviewLimits(data) {
  if (!data) return "";
  const bar = (left) => (left == null ? "" : progressMeter(left));
  const balances = data.providers.flatMap((p) => p.items.map(item =>
    `<div class="balance">${line(`${esc(p.name)} &middot; ${esc(BALANCE_LABELS[item.kind]())}`, esc(balanceValue(item)), { title: balanceValue(item, false) })}${bar(item.share)}</div>`));
  const share = (r) => (r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0);
  const rows = activeLimits(data)
    .sort((a, b) => share(a) - share(b)).slice(0, 3)
    .map((r) => `<div class="balance">${line(`<span title="${esc(r.model)}">${modelName(r.model)}</span>`,
      `${t("limits.value.of_text", { remaining: floorCount(r.remaining), limit: floorCount(r.limit) })} ${esc(unit(r))}`,
      { title: t("limits.value.of_text", { remaining: r.remaining.toLocaleString(), limit: r.limit.toLocaleString() }) })}${bar(share(r))}</div>`);
  return [...balances, ...rows].join("");
}

// One row for each lane of each model, as the Limits table reads them.
function limitRows(data = state.limits) {
  return (data?.lanes || []).flatMap((lane) =>
    lane.rows.map((row) => ({ ...row, model: lane.model, client: lane.client, at: lane.at })));
}

// Keep only rows that have a catalog home and a non-zero count.
function activeLimits(data = state.limits) {
  const known = new Set(state.models.map((m) => m.id));
  return limitRows(data).filter((r) => known.has(r.model) && (r.limit > 0 || r.remaining > 0));
}

// Filter rows without use or a catalog home, then by model and client text.
function shownLimits() {
  return activeLimits().filter((r) => !state.limitSearch
    || `${r.model} ${r.client || ""}`.toLowerCase().includes(state.limitSearch));
}

function renderLimits(data) {
  state.limits = data;
  limitsHash();
  renderLimitRows(shownLimits());
  draw("limits-checked", data.checked
    ? t("limits.checked_text", { time: timeText(data.checked) })
    : t("limits.not_checked_text"));
  $("balances").hidden = !data.providers.length;
  draw("balances", data.providers.map((p) => `<div class="card"><h3>${MARK_FILES.has(p.name) ? `${mark(p.name)} ` : ""}${esc(p.name)}</h3>
    ${p.items.map(item => `<div class="balance">${line(esc(BALANCE_LABELS[item.kind]()), esc(balanceValue(item)), { title: balanceValue(item, false) })}</div>`).join("")}</div>`).join(""));
}

function renderLimitRows(rows) {
  $("limits-clear").hidden = !state.limitSearch;
  // A bar that is full on every row says nothing, so the table draws bars only once a limit moves.
  const moving = rows.some((r) => r.limit > 0 && r.remaining < r.limit);
  draw("limit-rows", rows.length ? rows.map((r) => `<tr>
      ${nameCell(r.model, `${modelName(r.model)}${r.client ? ` <span class="muted">${esc(r.client)}</span>` : ""}`)}
      <td title="${esc(limitTitle(r))}">${esc(limitUnit(r))}</td>
      <td class="num"><div class="weight left" title="${t("limits.value.of_text", { remaining: r.remaining.toLocaleString(), limit: r.limit.toLocaleString() })}">
        <span class="num${r.remaining > 0 ? "" : " out"}">${t("limits.value.of_text", { remaining: floorCount(r.remaining), limit: floorCount(r.limit) })}</span>
        ${moving ? progressMeter(r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0) : ""}</div></td>
      ${limitTimeCell(r.reset, "hide-sm muted time num")}
      ${limitTimeCell(r.at, "hide-sm muted time num")}
      <td class="limit-meta">${limitMeta(r)}</td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">${t("limits.empty_text")}</td></tr>`);
}

const DIFF_KINDS = [
  ["added", "+", "added"],
  ["removed", "-", "removed"],
  ["moved", "~", "changed"],
];

function rebuildDiff(event) {
  const marks = DIFF_KINDS.map(([kind, mark, key]) => {
    const count = event[key].length;
    if (!count) return "";
    const label = key === "added" ? t("notification.diff_added_text", { count })
      : key === "removed" ? t("notification.diff_removed_text", { count })
        : t("notification.diff_moved_text", { count });
    return `<span class="${kind}">${label}</span>`;
  }).filter(Boolean).join(" · ");
  return marks || '<span class="muted">-</span>';
}

const DIFF_CAPABILITIES = {
  supports_function_calling: [CHIP_ICON_NAMES.tools, t("model.capability.tools_label")],
  supports_tool_choice: [CHIP_ICON_NAMES.tools, t("model.capability.tool_choice_label")],
  supports_parallel_function_calling: [CHIP_ICON_NAMES.tools, t("model.capability.parallel_tools_label")],
  supports_response_schema: ["", t("model.capability.structured_output_label")],
  supports_reasoning: [CHIP_ICON_NAMES.brain, t("model.column.reasoning_column")],
  supports_vision: [FLAG_ICON_NAMES.vision, FLAGS.vision],
  supports_pdf_input: [FLAG_ICON_NAMES.pdf_input, FLAGS.pdf_input],
  supports_audio_input: [FLAG_ICON_NAMES.audio_input, FLAGS.audio_input],
  supports_audio_output: [FLAG_ICON_NAMES.audio_output, FLAGS.audio_output],
  supports_web_search: ["search", t("model.capability.web_search_label")],
};
const DIFF_LABELS = {
  mode: t("model.column.type_column"),
  max_input_tokens: t("model.column.context_column"),
  max_output_tokens: t("model.column.output_column"),
  max_tokens: t("model.column.tokens_column"),
  rpm: t("model.column.rpm_column"),
  tpm: t("model.column.tpm_column"),
  tier: t("model.column.tier_column"),
};

function diffChip(label, iconName = "", kind = "") {
  return `<span class="chip flag${kind ? ` ${kind}` : ""}">${iconName ? `${icon(iconName)} ` : ""}${esc(label)}</span>`;
}

function diffValue(text) {
  if (text === "None") return null;
  if (text === "True") return true;
  if (text === "False") return false;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function diffShown(value) {
  if (typeof value === "boolean") return value ? t("common.boolean.yes_text") : t("common.boolean.no_text");
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function diffField(name, oldText, newText) {
  const before = diffValue(oldText);
  const after = diffValue(newText);
  const capability = DIFF_CAPABILITIES[name];
  if (capability) {
    const wasOn = before === true || before === 1;
    const isOn = after === true || after === 1;
    if (wasOn !== isOn) return [diffChip(capability[1], capability[0])];
  }
  if (name === "supported_efforts"
      && (before == null || Array.isArray(before))
      && (after == null || Array.isArray(after))) {
    const oldEfforts = before || [];
    const newEfforts = after || [];
    const chips = [
      ...oldEfforts.filter((value) => !newEfforts.includes(value)).map((value) => effortChip(value, undefined, true)),
      ...newEfforts.filter((value) => !oldEfforts.includes(value)).map((value) => effortChip(value)),
    ];
    if (chips.length) return chips;
  }
  // The old value of a pair reads struck, so the two chips never look alike.
  const chip = (value, struck = false) => {
    if (name === "reasoning_effort") return effortChip(value, undefined, struck);
    if (name === "mode") return diffChip(MODES[value] || value, "", struck ? "mode old" : "mode");
    const shown = name === "tier" ? tierLetter(value) : diffShown(value);
    return diffChip(`${DIFF_LABELS[name] || name} ${shown}`, "", struck ? "old" : "");
  };
  const chips = [];
  if (before != null) chips.push(chip(before, true));
  if (after != null) chips.push(chip(after));
  if (!chips.length) {
    chips.push(diffChip(`${DIFF_LABELS[name] || name} ${oldText}`, "", "old"));
    chips.push(diffChip(`${DIFF_LABELS[name] || name} ${newText}`));
  }
  return chips;
}

function diffChanges(text) {
  const chips = [];
  for (const entry of text.split(/, (?=[a-z_]+ )/)) {
    const space = entry.indexOf(" ");
    const arrow = entry.indexOf("->", space + 1);
    const name = entry.slice(0, space);
    if (space < 1 || arrow < 0 || !/^[a-z_]+$/.test(name)) return esc(text);
    chips.push(...diffField(name, entry.slice(space + 1, arrow), entry.slice(arrow + 2)));
  }
  return chips.length ? `<span class="diff-chips">${chips.join("")}</span>` : esc(text);
}

// A diff entry starts with its model id. A changed entry adds its field chips.
function diffModel(text, kind = "") {
  const value = String(text);
  const at = value.indexOf(": ");
  const id = at < 0 ? value : value.slice(0, at);
  const change = at < 0 ? "" : `: ${diffChanges(value.slice(at + 2))}`;
  const name = modelName(id, "", Boolean(kind));
  const identity = kind ? `<span class="diff-id ${kind}">${name}</span>` : name;
  return identity + change;
}

// Display the rebuild diff with one line per added, removed, or moved model.
function openDiffModal(event) {
  $("model-modal-title").textContent = `${event.reason} \u00b7 ${stamp(event.at)}`;
  const lines = DIFF_KINDS.flatMap(([kind, mark, key]) =>
    event[key].map((text) => [kind, mark, text]));
  $("model-modal-body").innerHTML = lines.length
    ? lines
        .map(([kind, mark, text]) => `<div class="line diff-line"><span class="${kind}">${mark}</span><span class="diff-model" title="${esc(text)}">${diffModel(text, kind)}</span></div>`)
        .join("")
    : none(t("model.diff.empty_text"));
  $("model-modal").showModal();
}

function renderNotifications(data) {
  state.notifications = data;
  const item = (kind, at, title, body, attrs = null, href = null) => {
    const content = `<span class="notification-mark" aria-hidden="true"></span>
      <span class="notification-copy"><span class="notification-head"><b>${title}</b>${timeText(at, { tag: "time", variant: "muted" })}</span>
      <span class="notification-body">${body}</span></span>`;
    if (href) return `<a class="notification-item ${kind}" href="${esc(href)}">${content}</a>`;
    return attrs
      ? actionButton(content, { variant: `notification-item ${kind}`, attrs })
      : `<div class="notification-item ${kind}">${content}</div>`;
  };
  const notices = [
    ...(data.resets || []).map((event) => ({
      at: event.at,
      html: item(
        "reset",
        event.at,
        event.model
          ? t("notification.model_reset_text", { model: esc(event.model), unit: esc(limitUnit(event)) })
          : t("notification.reset_text", { provider: esc(event.provider), unit: esc(limitUnit(event)) }),
        event.client
          ? t("notification.client_text", { client: esc(event.client) })
          : event.model
            ? t("notification.model_renewed_text")
            : t("notification.provider_renewed_text"),
      ),
    })),
    ...(data.limits || []).map((event) => ({
      at: event.at,
      html: item(
        "warning",
        event.at,
        event.remaining === 0
          ? t("notification.limit_reached_text", { model: esc(event.model) })
          : t("notification.limit_near_text", { model: esc(event.model) }),
        t("notification.limit_left_text", {
          remaining: floorCount(event.remaining),
          limit: floorCount(event.limit),
          unit: esc(limitUnit(event)),
          client: event.client ? ` · ${esc(event.client)}` : "",
        }) + (event.remaining === 0 && event.reset && event.reset * 1000 > Date.now()
          ? ` · ${t("notification.limit_resets_text", { when: relative(event.reset) })}`
          : ""),
        null,
        event.model ? `#/limits?q=${encodeURIComponent(event.model)}` : null,
      ),
    })),
    ...(data.rebuilds || []).map((event, index) => ({
      at: event.at,
      html: item(
        "rebuild",
        event.at,
        t("notification.catalog_rebuild_text", { reason: esc(event.reason) }),
        `${count(event.models, "model")} · ${rebuildDiff(event)}`
          + (event.failed.length
            ? t("notification.catalog_failed_text", { failures: esc(event.failed.join(", ")) })
            : ""),
        { "data-rebuild": index },
      ),
    })),
  ].sort((a, b) => b.at - a.at);
  draw(
    "notification-feed",
    notices.length ? notices.map((notice) => notice.html).join("") : none(t("notification.empty_text"))
  );
}

// The maintenance card lives in Settings: the update check, the catalog rebuild, and the reset.
function maintenanceCard() {
  const update = state.notifications?.update;
  const stateTitle = t("update.state_title");
  const rows = [];
  if (!update) rows.push([stateTitle, esc(t("update.not_checked_text"))]);
  else if (update.error === "no commit in this build's version")
    rows.push([stateTitle, esc(t("update.no_commit_text"))]);
  else if (update.error)
    rows.push([stateTitle, esc(t("update.check_failed_text", { error: update.error }))]);
  else if (update.channel === "release") {
    rows.push(update.update
      ? [stateTitle, `${esc(t("update.available_text"))} · <a href="${esc(update.url)}">${esc(update.latest)}</a>`]
      : [stateTitle, `${esc(t("update.up_to_date_text"))} · ${esc(update.latest || update.current)}`]);
    if (update.update) rows.push([t("update.you_are_on_text"), esc(update.current)]);
  } else if (update.update)
    rows.push([stateTitle, `${esc(t("update.behind_main_text"))} ${count(update.behind, "commit")}`]);
  else rows.push([stateTitle, `${esc(t("update.up_to_date_text"))} · main`]);
  rows.push([t("update.checked_text"), update ? timeText(update.at) : "-"]);
  let body = "";
  body += rows.map(([left, right]) =>
    `<div class="field"><span>${esc(left)}</span><span>${right}</span></div>`).join("");
  const { built, next, rebuilding } = state.status?.catalog || {};
  const last = rebuilding ? t("catalog.rebuilding_text") : built ? relative(built) : t("catalog.never_text");
  const following = next
    ? t("catalog.next_text", { time: esc(relative(next)) })
    : t("catalog.no_schedule_text");
  const label = rebuilding ? t("catalog.rebuild.running_title") : t("catalog.rebuild.start_title");
  body += actionButton(`<span>${t("catalog.label")}</span><span><b>${esc(last)}</b>${following}</span>`, {
    variant: "line rebuild field",
    attrs: { "data-catalog-rebuild": true, title: label, disabled: rebuilding },
  });
  body += `<div class="editbar">` + actionButton(t("dialog.reset.title"), {
    variant: "ghost danger", attrs: { id: "reset-weights", title: t("dialog.reset.message") },
  }) + actionButton(`${icon("refresh_cw")} ${t("action.check_now_button")}`, {
    variant: "ghost", attrs: { id: "update-check" },
  }) + `<span class="message" id="reset-message" role="status"></span>`
    + `<span class="hint">${t("model.reset.hint")}</span></div>`;
  return `<div class="card" data-section="maintenance" id="update-card">${body}</div>`;
}

async function refreshSlow() {
  const [pools, models, limits, notifications] = await Promise.all([
    call("pools"),
    call("models"),
    call("limits"),
    call("notifications"),
    refreshKeys(),
  ]);
  state.pools = pools;
  state.models = models;
  renderLimits(limits);
  renderNotifications(notifications);
  renderPools(pools);
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
  // Load settings before tables to apply the time format.
  await loadSettings();
  await loadLegend();
  // Display the page before the first state response.
  $("login").hidden = true;
  $("app").hidden = false;
  // Size panes after the app becomes visible. Hidden panes have no height.
  fitSectionPane();
  markCardScrollEdges();
  setStateKnown(null);
  skeletons("requests", 13);
  skeletons("models", 9);
  await refreshFast();
  await refreshSlow();
  takeFiles(await call("files"));
  await refreshEnv();
  [state.overrideKeys, state.providerDefaults] = await Promise.all([call("provider-keys"), call("provider-defaults")]);
  state.file = 0;
  state.fileOpen = false;
  showFileError(0);
  renderFiles();
  renderOverview();
  applySectionHash();
  state.timers = [
    // Skip server calls in hidden tabs and refresh when they become visible.
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
  button.classList.add("checking");
  $("limits-message").textContent = "";
  try {
    renderLimits(await call("limits", { method: "POST", spinner: false }));
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    $("limits-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.classList.remove("checking");
  }
});
// Open the diff dialog from a rebuild notification.
$("notification-feed").addEventListener("click", (event) => {
  const row = event.target.closest("[data-rebuild]");
  if (!row || !state.notifications) return;
  const index = Number(row.dataset.rebuild);
  openDiffModal(state.notifications.rebuilds[index]);
});
$("settings").addEventListener("click", async (event) => {
  const button = event.target.closest("#update-check");
  if (button) {
    button.disabled = true;
    button.textContent = t("action.checking_button");
    try {
      const result = await call("updates", { method: "POST" });
      state.notifications = { ...(state.notifications || {}), update: result };
      const card = $("settings").querySelector("#update-card");
      if (card) card.outerHTML = maintenanceCard();
    } catch (error) {
      if (error instanceof LoggedOut) return showLogin();
      button.disabled = false;
      button.innerHTML = `${icon("refresh_cw")} ${t("action.check_now_button")}`;
    }
    return;
  }
  if (!event.target.closest("#reset-weights")) return;
  const message = $("reset-message");
  message.textContent = "";
  const go = await ask(t("dialog.reset.title"), "", t("action.reset_button"), true);
  if (!go) return;
  try {
    await call("reset", { method: "POST" });
    message.className = "message ok";
    message.textContent = t("model.reset.done_text");
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    message.className = "message bad";
    message.textContent = error.message;
  }
});
$("show-password").addEventListener("click", () => showPassword($("login").password.type === "password"));
document.addEventListener("click", async (event) => {
  // Display hints without opening closed groups.
  if (event.target.closest(".fold summary .hint")) {
    event.preventDefault();
    return;
  }
  if (event.target.closest("[data-catalog-rebuild]")) return rebuildCatalog();
  if (!event.target.closest(".logout")) return;
  if (!(await ask(t("action.logout_button"), t("auth.logout.message"), t("action.logout_button")))) return;
  await call("logout", { method: "POST" }).catch(() => {});
  keepSession(null);
  showLogin();
});

// Display the character limit when a field reaches it.
function showLengthLimit(input, message) {
  const limit = t("validation.characters_at_most_text", { count: input.maxLength });
  if (input.maxLength > 0 && input.value.length >= input.maxLength) {
    message.textContent = limit;
  } else if (message.textContent === limit) {
    message.textContent = "";
  }
}

// Delegate API key events to the Settings pane, which survives card renders.
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
      $("copy").textContent = t("action.copied_button");
    } catch {
      getSelection().selectAllChildren($("reveal-key"));
      $("copy").textContent = t("action.press_ctrl_c_button");
    }
    return;
  }
  if (event.target.closest("#reveal-close")) {
    $("reveal").hidden = true;
    $("reveal-key").textContent = "";
    $("copy").textContent = t("action.copy_button");
    return;
  }
  const button = event.target.closest("[data-key]");
  if (!button) return;
  const name = button.dataset.key;
  if (!(await ask(t("dialog.delete_key.title"), t("dialog.delete_key.message", { name }), t("action.delete_button"), true))) return;
  await guarded(async () => {
    await call("keys/" + encodeURIComponent(name), { method: "DELETE" });
    await refreshKeys();
  });
});
$("more-requests").addEventListener("click", () => {
  state.requestLimit = Math.min(state.requestLimit + REQUESTS_STEP, REQUESTS_KEPT);
  guarded(refreshFast);
});
$("requests-prev").addEventListener("click", () => {
  if (state.requestPage > 1) {
    state.requestPage -= 1;
    renderRequestTable();
  }
});
$("requests-next").addEventListener("click", () => {
  const rows = state.requests.filter((r) => requestMatches(r));
  const pages = Math.max(1, Math.ceil(rows.length / REQUESTS_PAGE));
  if (state.requestPage < pages) {
    state.requestPage += 1;
    renderRequestTable();
    return;
  }
  if (state.requestFetched >= state.requestLimit && state.requestLimit < REQUESTS_KEPT) {
    state.requestPage += 1;
    state.requestLimit = Math.min(state.requestLimit + REQUESTS_STEP, REQUESTS_KEPT);
    guarded(refreshFast);
  }
});

// Apply Requests filters without a server call.
$("request-search").addEventListener("input", () => {
  state.requestSearch = $("request-search").value.trim().toLowerCase();
  state.requestPage = 1;
  requestHash();
  renderRequestTable();
});
$("request-status").addEventListener("change", () => {
  state.requestStatus = $("request-status").value;
  state.requestPage = 1;
  requestHash();
  renderRequestTable();
});
$("request-hours").addEventListener("change", () => {
  state.requestHours = Number($("request-hours").value) || 0;
  state.requestPage = 1;
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
  state.requestPage = 1;
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
  refresh();
});
$("notice-close").addEventListener("click", () => {
  closeNotice();
});
// Hide the note after its exit animation. A new failure interrupts the animation.
$("notice").addEventListener("animationend", (event) => {
  if (event.animationName !== "leave") return;
  const note = $("notice");
  note.classList.remove("leave");
  note.hidden = true;
});

$("requests").addEventListener("click", (event) => {
  const open = event.target.closest("[data-chain-at]");
  if (open) openChain(open.dataset.chainAt);
});
// The row is a control, so its keys open the chain like a press does.
$("requests").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest?.("tr[data-chain-at]");
  if (!row) return;
  event.preventDefault();
  openChain(row.dataset.chainAt);
});
// The copy button lives in the chain dialog, outside the table listener.
$("chain-modal").addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-chain");
  if (!button) return;
  const found = state.requests.find((r) => String(r.at) === button.dataset.at);
  try {
    await navigator.clipboard.writeText(chainText(found));
    button.textContent = t("action.copied_button");
  } catch {
    getSelection().selectAllChildren(button.closest(".chain-body"));
    button.textContent = t("action.press_ctrl_c_button");
  }
});
const PAGES = ["overview", "requests", "models", "limits", "notifications", "settings"];
const PAGE_LABELS = {
  overview: t("nav.overview_link"),
  requests: t("nav.requests_link"),
  models: t("nav.models_link"),
  limits: t("nav.limits_link"),
  notifications: t("nav.notifications_link"),
  settings: t("nav.settings_link"),
};

// A Models link such as #/models?tier=C&mode=chat&sort=weight sets the filters.
function applyModelFilters(query) {
  const params = new URLSearchParams(query);
  state.tier = ["All", "A", "B", "C", "D"].includes(params.get("tier")) ? params.get("tier") : "All";
  state.mode = Object.hasOwn(MODES, params.get("mode")) || Object.hasOwn(FLAGS, params.get("mode"))
    ? params.get("mode") : "all";
  if (Object.hasOwn(MODES, state.mode) && state.mode !== "chat") state.tier = "All";
  state.sort = Object.hasOwn(sortValue, params.get("sort")) ? { key: params.get("sort"), dir: -1 } : { key: "", dir: 1 };
  $("search").value = "";
  $("mode").value = state.mode;
  renderSortHeads();
  markPools();
  if (state.models.length) renderModels();
  history.replaceState(null, "", "#/models");
}

// A Requests link such as #/requests?status=bad&hours=24&q=timeout sets the filters.
// Keep filters in the hash so shared links display the same view.
function applyRequestFilters(query) {
  const params = new URLSearchParams(query);
  const asked = params.get("q") || "";
  state.requestSearch = asked.trim().toLowerCase();
  state.requestStatus = ["all", "ok", "bad"].includes(params.get("status")) ? params.get("status") : "all";
  state.requestHours = [0, 1, 24].includes(Number(params.get("hours"))) ? Number(params.get("hours")) : 0;
  state.requestPage = 1;
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

// Update the filter URL only on the Limits page. Polling calls this on every page.
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

// A fade grows over the first stretch of scroll, holds, and shrinks at the far end.
const FADE_GROW = 48;
const fadeLen = (dist) => `${Math.round(Math.min(1, Math.max(0, dist / FADE_GROW)) * 20)}px`;
const setFade = (host, name, dist) => host.style?.setProperty(name, fadeLen(dist));

function markScrollEdges(scroller) {
  const end = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  setFade(scroller, "--fade-left", scroller.scrollLeft);
  setFade(scroller, "--fade-right", end - scroller.scrollLeft);
  return { left: scroller.scrollLeft <= 2, right: scroller.scrollLeft >= end - 2 };
}

function markCardScrollEdges() {
  for (const scroller of document.querySelectorAll(".card-scroll")) markScrollEdges(scroller);
}

function markPaneEdges(pane) {
  pane.classList.toggle("fit-host", pane.querySelector(
    ":scope > .provider-flow:not([hidden]), :scope > .card.yaml-view:not([hidden])") != null);
  const end = Math.max(0, pane.scrollHeight - pane.clientHeight);
  setFade(pane, "--fade-top", pane.scrollTop);
  setFade(pane, "--fade-bottom", end - pane.scrollTop);
  return { top: pane.scrollTop <= 2, bottom: pane.scrollTop >= end - 2 };
}

function markSectionPaneEdges() {
  for (const pane of document.querySelectorAll(".section-pane")) markPaneEdges(pane);
}

document.addEventListener("scroll", (event) => {
  const scroller = event.target;
  if (scroller.matches?.(".card-scroll")) markScrollEdges(scroller);
  if (scroller.matches?.(".section-pane")) markPaneEdges(scroller);
}, true);

// The strip breathes its own clipped edges: fades only, no chevrons.
const nav = $("nav");
function markNavEdges() {
  markScrollEdges(nav);
}
nav.addEventListener("scroll", markNavEdges, { passive: true });
// Replace the history entry so phone back leaves the app instead of retracing tabs.
for (const link of document.querySelectorAll("#nav a")) {
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
    event.preventDefault();
    history.replaceState(null, "", link.getAttribute("href"));
    showPage();
  });
}


// On wide screens, fit each pane below its top edge.
// Scroll forms separately from the fixed header and rail.
function settingsScroll() {
  const pane = $("settings")?.querySelector?.(".section-pane");
  return { pane: pane?.scrollTop ?? 0, top: typeof window.scrollY === "number" ? window.scrollY : 0 };
}

// Restore the scroll position after a card rebuild.
function keepSettingsScroll(at) {
  const pane = $("settings")?.querySelector?.(".section-pane");
  if (pane && at.pane) pane.scrollTop = at.pane;
  if (pane) markPaneEdges(pane);
  if (at.top && typeof window.scrollTo === "function") window.scrollTo(0, at.top);
}

function fitSectionPane() {
  const wide = matchMedia("(min-width: 901px)").matches;
  const main = document.querySelector("main");
  for (const pane of document.querySelectorAll(".section-pane")) {
    if (!wide || !pane.offsetParent || !main) {
      pane.style.maxHeight = "";
      markPaneEdges(pane);
      continue;
    }
    const pad = parseFloat(getComputedStyle(main).paddingBottom) || 0;
    const bottom = main.getBoundingClientRect().top + main.clientHeight - pad;
    pane.style.maxHeight = `${Math.max(240, bottom - pane.getBoundingClientRect().top - 16)}px`;
    markPaneEdges(pane);
  }
}

let shownPage = "";
function showPage() {
  let [path, query] = location.hash.split("?");
  let asked = path.replace("#/", "").replace(/^config$/, "providers").replace(/^pools$/, "models");
  // The providers page lives in Settings now: old links land on its section.
  if (asked === "providers") {
    const inner = new URLSearchParams(query ?? "").get("section");
    const providers = SECTION_STATE.providers;
    providers.key = inner && inner !== "yaml" ? inner : inner === "yaml" ? "yaml" : "";
    providers.open = Boolean(inner);
    SECTION_STATE.settings.key = "providers";
    SECTION_STATE.settings.open = true;
    history.replaceState(null, "", inner
      ? `#/settings?section=providers&provider=${encodeURIComponent(inner)}`
      : "#/settings?section=providers");
    [path, query] = location.hash.split("?");
    asked = "settings";
  }
  const page = PAGES.includes(asked) ? asked : PAGES[0];
  const changed = page !== shownPage;
  shownPage = page;
  // The page layout of main rides on this dataset, so it needs no :has support.
  const main = document.querySelector("main");
  if (main?.dataset) main.dataset.page = page;
  // Animate the table, not the page, when a pool filter targets the current Models page.
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
    if (on) {
      link.setAttribute("aria-current", "page");
      link.scrollIntoView({ block: "nearest", inline: "nearest" });
    } else link.removeAttribute("aria-current");
  });
  // Mark navigation chevrons on the first paint, before the status response.
  markNavEdges();
  // Render before selecting the section to preserve its phone back animation classes.
  if (page === "settings" && state.settings) renderSettings();
  applySectionHash();
  slideAgain();
  document.title = t("page.title", { page: PAGE_LABELS[page] });
  fitSectionPane();
  markCardScrollEdges();
}

window.addEventListener("hashchange", showPage);
window.addEventListener("popstate", applySectionHash);
showPage();
$("search").addEventListener("input", selectRows);
// Open model details from a row or card.
$("models").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-model]");
  if (!row) return;
  const model = state.models.find((item) => item.id === row.dataset.model);
  if (model) openModelModal(model);
});

matchMedia("(max-width: 720px)").addEventListener("change", syncSortFocus);
syncSortFocus();
$("model-head").addEventListener("click", (event) => {
  const th = event.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  // Up, then down, then back to the default order.
  const same = state.sort.key === key;
  state.sort = !same ? { key, dir: 1 } : state.sort.dir > 0 ? { key, dir: -1 } : { key: "", dir: 1 };
  renderSortHeads();
  selectRows();
});
$("sort-small").addEventListener("change", () => {
  state.sort = { key: $("sort-small").value, dir: 1 };
  renderSortHeads();
  selectRows();
});
$("mode").addEventListener("change", () => {
  state.tier = "All";
  state.mode = $("mode").value;
  selectRows();
});
$("files").addEventListener("click", (event) => {
  const button = event.target.closest("[data-file]");
  if (button) openFile(Number(button.dataset.file));
});
async function reloadFiles(keep) {
  captureProviderYamlDraft();
  const previous = new Map(state.files.map((file, index) => [file.path, {
    text: file.text, saved: state.saved[index], blocks: state.forms[index], formSaved: state.formSaved[index],
  }]));
  const fresh = await call("files");
  state.files = fresh.map((file) => ({ ...file, text: previous.get(file.path)?.text ?? file.text }));
  state.saved = state.files.map((file) => previous.get(file.path)?.saved ?? file.text);
  state.forms = state.files.map((file) => previous.get(file.path)?.blocks ?? clone(file.blocks));
  state.formSaved = state.files.map((file, index) => previous.get(file.path)?.formSaved ?? formText(index));
  const index = Math.max(0, state.files.findIndex((file) => file.path === keep));
  if (keep && state.files.length) await openFile(index);
  else {
    state.file = index;
    state.fileOpen = false;
    state.providerReturnKey = "";
    const section = sectionState("provider-form");
    section.key = "";
    section.open = false;
    showFileError(index);
    renderFiles();
    renderForm();
    setProviderRoute(null, null, false);
  }
  guarded(refreshEnv);
}

$("files").addEventListener("click", async (event) => {
  if (!event.target.closest("#new-provider")) return;
  clearWrite("providers");
  const ok = await ask(
    t("dialog.new_provider.title"), t("dialog.new_provider.hint"),
    t("action.create_button"), false, t("dialog.new_provider.placeholder"),
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
async function deleteProviderFile() {
  clearWrite("providers");
  const file = state.files[state.file];
  const label = file ? fileName(file.path).replace(/\.[^.]+$/, "") : "";
  if (!file || !(await ask(t("dialog.delete_file.title"),
    t("dialog.delete_file.message", { name: label }), t("action.delete_button"), true))) return;
  try {
    await call("files", { method: "DELETE", body: JSON.stringify({ path: file.path }) });
    await reloadFiles(null);
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    showWrite("providers", error.message, true);
  }
}
$("provider-form").addEventListener("input", (event) => {
  if (event.target.id === "editor") renderFiles();
});
bindValueRows($("provider-form"), () => saveForm());
bindValueRows($("settings"), () => saveSettings());
$("provider-form").addEventListener("click", (event) => {
  if (event.target.closest("#drop-provider")) return deleteProviderFile();
  const view = event.target.closest("[data-provider-section]");
  if (view) return selectProviderSection(view.dataset.providerSection);
  const section = event.target.closest(".sections button[data-section]");
  if (section) return selectProviderSection(section.dataset.section);
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
    if (add.dataset.kind === "match") return addMapPair(add);
    if (add.dataset.kind === "column") return addProviderValue(add);
    showAdder(add);
  }
});

// Add a map entry with a name and value dialog.
async function addMapPair(button) {
  const path = JSON.parse(button.dataset.add);
  clearWrite("providers");
  const discovery = path[path.length - 1] === "discovery_match";
  const ok = await askPair(
    discovery ? t("dialog.discovery_match.title") : t("dialog.client_key.title"),
    discovery ? t("dialog.discovery_match.message") : t("dialog.client_key.message"),
    discovery ? t("provider.discovery_property_placeholder") : t("provider.client_key_placeholder"),
    discovery ? t("provider.discovery_value_placeholder") : t("provider.provider_key_placeholder"),
  );
  if (!ok) return;
  const name = $("modal-input").value.trim();
  const value = $("modal-input2").value.trim();
  if (!name || !value) {
    const message = discovery
      ? t("provider.validation.discovery_match_text")
      : t("provider.validation.client_key_text");
    return showWrite("providers", message, true);
  }
  parentOf([...path, name], {})[name] = parsed(value);
  renderCard(path[0]);
  saveForm();
}

// Select a catalog key and type its value in the dialog.
async function addProviderValue(button) {
  const path = JSON.parse(button.dataset.add);
  clearWrite("providers");
  const choices = [...state.overrideKeys.filter((key) => !MODEL_ONLY.includes(key)), ...PROVIDER_ONLY].sort();
  const ok = await askChoicePair(
    t("dialog.provider_value.title"), t("dialog.provider_value.message"),
    choices, t("provider.adder.value_placeholder"),
  );
  if (!ok) return;
  const text = $("modal-input2").value.trim();
  if (!text) return showWrite("providers", t("provider.validation.provider_value_text"), true);
  if (addValue(path, "column", text, $("modal-choice").value)) saveForm();
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
  const selection = event.target.closest("[data-form-hook-select]");
  if (selection) {
    const at = selection.dataset.formHookSelect;
    openFormHook = openFormHook === at ? null : at;
    renderCard(JSON.parse(at)[0]);
  }
});
$("provider-form").addEventListener("change", (event) => {
  if (event.target.dataset.pattern) return renamePattern(event.target);
  if (event.target.dataset.formHookSurface) {
    const parts = JSON.parse(event.target.dataset.formHookSurface);
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
  markNavEdges();
  fitSectionPane();
  markCardScrollEdges();
});
$("settings").addEventListener("input", (event) => {
  // The nested providers host handles its own events.
  if (event.composedPath?.().includes($("provider-form"))) return;
  clearWrite("settings");
  showLengthLimit(event.target, $("settings-message"));
  // Change visible affinity rows without changing the selected mode.
  if (event.target.id === "set-affinity-mode") {
    showAffinityRows();
    return renderSettingsSave();
  }
  // Use the switch to enable or disable other card rows.
  if (event.target.type === "checkbox") showSwitchRows();
  renderSettingsSave();
});
$("settings").addEventListener("click", async (event) => {
  // The nested providers host handles its own events.
  if (event.composedPath?.().includes($("provider-form"))) return;
  const section = event.target.closest(".sections button[data-section]");
  if (section) return selectSettingsSection(section.dataset.section);
  if (event.target.closest("[data-back]")) return closeSection($("settings"));
  // Handle hook source edits, removal, updates, and scan results.
  if (event.target.closest("[data-hook-source-add]")) return addHookSource();
  if (event.target.closest("[data-hook-source-drop]")) return dropHookSource();
  const move = event.target.closest("[data-hook-move]");
  if (move) return moveHookFile(JSON.parse(move.dataset.hookMove));
  if (event.target.closest("[data-hook-drop]")) return dropHookFile(event.target.closest("[data-hook-drop]"));
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
  // The nested providers host handles its own events.
  if (event.composedPath?.().includes($("provider-form"))) return;
  const input = event.target;
  // The manager writes at once too: the source selection stays in the page, and the 2 boxes save.
  if (input.dataset.hookSource !== undefined) {
    state.hooksSource = Number(input.value) || 0;
    return;
  }
  if (input.dataset.hookToggle) return toggleHook(input.dataset.hookToggle, input.checked);
  if (input.dataset.hookTake) {
    state.hooksScan.take[input.dataset.hookTake] = input.checked;
    return;
  }
  // Save switch and select values immediately on change. A switch keeps its form: the page
  // already shows the new state, and a redraw would cut its motion off at the first frame.
  if (input.type !== "checkbox" && input.tagName !== "SELECT") return;
  state.writeAnchor = anchorOf(input);
  saveSettings(input.type === "checkbox");
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
  if (sectionState($("settings")).key === "yaml") saveSettings();
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

// Close on a backdrop click outside the dialog bounds, not on clicks inside the box.
// Escape closes the dialog without this handler.
$("modal").addEventListener("click", (event) => {
  const dialog = event.currentTarget;
  if (event.target !== dialog) return;
  const box = dialog.getBoundingClientRect();
  const outside = event.clientX < box.left || event.clientX > box.right
    || event.clientY < box.top || event.clientY > box.bottom;
  if (outside) dialog.close("cancel");
});

// Confirm, Cancel, Escape, and backdrop clicks close the dialog.
$("modal").addEventListener("close", (event) => {
  const done = globalThis.__settle;
  globalThis.__settle = null;
  done?.(event.target.returnValue === "ok");
});

applyLocale();
renderLegend();
guarded(start);
