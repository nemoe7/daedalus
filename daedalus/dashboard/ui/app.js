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
// The Requests page loads 50 rows, and each "Show more" adds 50, up to the 500 that the server keeps.
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
const plural = (number, one, other, values = {}) =>
  t(number === 1 ? one : other, { count: number, ...values });

// A shared relative time: 2 hours ago, in 6 hours, 12 mins ago, 29 days ago.
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

// A relative time with its exact stamp on hover. The caller keeps the surrounding layout local.
function timeText(seconds, { tag = "span", variant = "" } = {}) {
  const attrs = htmlAttrs({ class: variant || null, title: stamp(seconds) });
  return `<${tag}${attrs}>${esc(relative(seconds))}</${tag}>`;
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
// A model name cell that ends in an ellipsis when it is too long. The title shows the full name.
const mobileLabel = (text) => `<span class="mobile-label" aria-hidden="true">${esc(text)}</span>`;
const nameCell = (text, shown = esc(text), label = t("model.column.model_column"), extra = "") =>
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
  let dev = bits.length ? bits[bits.length - 1].replace(/^@[^/]+\//, "") : null;
  if (dev && dev.toLowerCase() === provider.toLowerCase()) dev = null;
  return { provider, dev, model };
};
// The separator of the parts, so the cell reads as the model id does.
const sep = '<span class="mark-sep" aria-hidden="true">/</span>';
const modelName = (id, pool = "", tint = false) => {
  const { provider, dev, model } = parts(id);
  if (model === null) return esc(id);
  const heads = (dev ? [provider, dev] : [provider]).map((name) => mark(name, tint));
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

// The 2-field modal: a name and its value. It resolves true on Confirm,
// and the caller reads modal-input and modal-input2 after a true.
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

// The 2-field modal with a select on top and a text field below. It resolves true on
// Confirm, and the caller reads modal-choice and modal-input2 after a true.
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

// The small editor of a value row, on the modal manager. The close of that dialog writes the
// 1 value: Save writes it, and Cancel or Escape leaves the file alone.
async function editValue(input, write) {
  const box = $("modal-input");
  const number = input.type === "number" ? document.createElement("input") : null;
  if (number) number.type = "number";
  box.type = input.type === "password" ? "password" : "text";
  box.inputMode = number ? "decimal" : "";
  const validate = () => {
    if (!number) return;
    number.value = box.value;
    box.pattern = box.value === "" || (number.value !== "" && Number.isFinite(number.valueAsNumber))
      ? ".*" : "(?!)";
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
  write(input);
}

// A value row: the value shows as text, and a click opens the small editor.
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
    let response;
    try {
      response = await fetch("ui/api/" + path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(session() ? { "x-daedalus-session": session() } : {}) },
        ...options,
      });
    } catch {
      // No answer at all: the line says so in place of the browser wording of a failed fetch.
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
  const label = on ? t("auth.password.hide_label") : t("auth.password.show_label");
  $("login").password.type = on ? "text" : "password";
  button.setAttribute("aria-pressed", String(on));
  button.setAttribute("aria-label", label);
  button.title = label;
}

// A read failure: 1 line in the page and 1 button to ask again. The line stays until the
// user clears it with the x; the retry asks again but keeps the line in place.
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
  // The count is the providers in the catalog now: a rebuild reads their model lists again.
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

// The status card of a phone: the health row, then 1 line for each value. The catalog line starts a rebuild.
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

// The last state answer. It stays in place when a later call fails, and the chip says so.
let stateKnown = null;

function stateChip() {
  if (stateKnown === null) return `<span class="chip" id="state-loading">${t("state.loading_text")}</span>`;
  if (stateKnown) return "";
  return `<span class="chip bad" id="state-unknown" title="${t("state.unknown.hint")}">${t("state.unknown_text")}</span>`;
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
    const bar = health === null ? "" : `<div class="health" title="${t("model.pool.mean_weight_title")}">
      ${progressMeter(health)}<span class="num">${health.toFixed(2)}</span></div>`;
    const context = pool.context
      ? ` <span class="ctx" title="${t("model.context.pool_hint", { count: pool.context.toLocaleString() })}">${tokens(pool.context)}</span>` : "";
    return `<a class="card pool" data-tier="${tier}" data-mode="${esc(mode)}" title="${esc(pool.shown)}: ${esc(POOL_NOTES[pool.name] || "")}">
      <h3>${esc(pool.shown.replace("daedalus/", ""))}${context}</h3>${bar}<div class="sub">${count(pool.members.length, "model")}</div></a>`;
  }).join(""));
  markPools();
}

const opened = new Set();
let shownRequests = "";

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
  return r.cancelled ? t("request.state.cancelled_text") : r.status;
}

// The meaning of each status, for the hover of the status cell.
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

// The status on the page: 499, the nginx code of a request that the client closed.
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

function chainText(r) {
  const head = [stamp(r.at), r.model, statusText(r),
    r.effort && t("request.chain.field.effort_text", { value: r.effort }),
    r.pool && t("request.chain.field.pool_text", { value: r.pool }),
    r.routed && t("request.chain.field.from_text", { value: r.routed }),
    t("request.chain.field.fallbacks_text", { value: r.fallbacks ?? 0 }),
    r.retry && t("request.chain.field.retry_text", { value: r.retry }),
    r.loop && t("request.chain.field.loop_text", { value: r.loop }),
  ].filter(Boolean).join(" ");
  const steps = (r.attempts || []).map((a, i) =>
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

// A request opens its fallback chain only after a fallback, or when an attempt failed and has an error to show.
const hasChain = (r) => (r.attempts || []).length > 1 || (r.attempts || []).some((a) => a.result !== "answered");

function chainSteps(r) {
  const steps = (r.attempts || []).map((a, i) => `
    <li class="step ${a.result === "answered" ? "good" : "bad"}">
      <span class="num">${i + 1}.</span> <b>${esc(a.model)}</b>
      <span class="result">${esc(a.result)}</span> <span class="muted num">${seconds(a.seconds)}</span>
      ${"effort" in a ? `<span class="from">${t("request.chain.effort_text", { effort: esc(shortEffort(sentText(a, r.effort))) })}</span>` : ""}
      ${a.weight_change ? `<span class="from">${esc(weightText(a))}</span>` : ""}
      ${a.cooldown ? `<span class="from">${esc(coolText(a.cooldown))}</span>` : ""}
      ${a.race === "won" ? `<span class="from">${t("request.chain.won_race_text")}</span>` : ""}
      ${a.error ? `<pre>${esc(a.error)}</pre>` : ""}
    </li>`).join("");
  return `<ol>${steps}</ol>`;
}

function chainRows(r) {
  const many = fallbackCount(r);
  const title = many
    ? t("request.chain.title_with_count_text", { count: many })
    : t("request.chain.title");
  return `<tr role="row" class="chain"><td role="cell" colspan="13"><div class="chain-body">
    <div class="chain-head"><span class="muted">${title}</span>
      ${actionButton(t("request.chain.copy_button"), { variant: "ghost copy-chain", attrs: { "data-at": r.at } })}</div>
    ${raceNote(r)}${chainSteps(r)}
  </div></td></tr>`;
}

// The routing codes of the model cell: a transition, a fallback tier, and a stopped loop.
// Why the racers did or did not start: the code wears the same 3 letter shape as the routing
// codes, and the title carries the full note. It shows while the race is on.
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

function mobileFallbackChain(r, tail = "") {
  const summary = tail
    ? t("request.chain.view_with_count_text", { count: tail.replace(/^ · /, "") })
    : t("request.chain.view_text");
  return `<details class="mobile-fallback-chain">
    <summary>${summary}</summary>
    <div class="mobile-chain-head"><span class="muted">${t("request.chain.title")}</span>
      ${actionButton(t("request.chain.copy_button"), { variant: "ghost copy-chain", attrs: { "data-at": r.at } })}</div>
    ${raceNote(r)}${chainSteps(r)}
  </details>`;
}

// The fallback count of a request: the table hides the column, so the details of the chain carry
// the count, and a live row reads the count alone.
function fallbackCount(r) {
  return r.fallbacks === undefined || r.fallbacks === null
    ? ""
    : plural(Number(r.fallbacks), "request.chain.fallback.one_text", "request.chain.fallback.other_text");
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
  const label = state.livePaused
    ? state.liveWaiting.size
      ? t("request.live.paused_new_button", { count: state.liveWaiting.size })
      : t("request.live.paused_button")
    : t("request.live_button");
  button.innerHTML = `${state.livePaused ? icon("pause") : icon("activity")} ${esc(label)}`;
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
  ctx: t("request.route.context_text"),
  hlt: t("request.route.health_text"),
  lmt: t("request.route.limit_text"),
  err: t("request.route.failure_text"),
  rnd: t("request.route.random_text"),
  cls: t("request.route.classifier_text"),
  esc: t("request.route.escalation_text"),
  rce: t("request.route.race_text"),
};

// A repeat carries the code of the hook, such as rt1. The row shows the code as given.
const REPEAT_CODE = /^rt\d+$/;
const REPEAT_LABEL = t("request.route.repeat_text");

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
    ["frX", t("request.legend.frx_text")],
    ["tlN", t("request.legend.tln_text")],
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
    ? t("request.empty.filter_text")
    : t("request.empty.none_text"), live);
  // The button keeps its seat in the bar, so showing it never moves the count hint.
  $("request-clear").classList.toggle("off",
    !state.requestSearch && state.requestStatus === "all" && !state.requestHours);
  $("more-requests").hidden = state.requestFetched < state.requestLimit
    || state.requestLimit >= REQUESTS_KEPT;
  const shown = rows.length + live;
  const head = shown === total
    ? count(total, "request")
    : t("request.count.shown_text", { shown, total: count(total, "request") });
  const hint = $("request-count");
  hint.textContent = total
    ? `${head}${live ? ` · ${t("request.count.in_flight_text", { count: live })}` : ""}`
    : "";
  // The hint pulses when the stream itself changes, not when a filter rewrites the text.
  if (live !== state.liveCount) {
    state.liveCount = live;
    hint.classList.remove("bump");
    void hint.offsetWidth;
    hint.classList.add("bump");
  }
}

function renderRequests(rows, empty = t("request.empty.none_text"), live = 0) {
  const flatRows = splitRequests(rows);
  // The live count rides in the key: a live row hides the empty row, so it changes the markup.
  const text = JSON.stringify([flatRows, live ? 1 : 0]);
  const selected = getSelection();
  if (text === shownRequests) return;
  if (!selected.isCollapsed && $("requests").contains(selected.anchorNode)) return;
  const focused = document.activeElement?.closest?.(".request-toggle")?.dataset.chainToggle;
  shownRequests = text;
  $("requests").innerHTML = flatRows.length ? flatRows.map((r) => {
    const chain = hasChain(r);
    const chainOpen = chain && opened.has(String(r.at));
    const time = `<span class="caret${chain ? "" : " none"}" aria-hidden="true"></span>${relative(r.at)}`;
    const timeControl = chain ? actionButton(time, {
      variant: "request-toggle hide-sm",
      attrs: { "data-chain-toggle": r.at, "aria-expanded": String(chainOpen), title: t("request.chain.show_title") },
    }) + `<span class="only-sm">${time}</span>` : time;
    return `
    <tr role="row" class="request${chain ? " has-chain" : ""}${chainOpen ? " open" : ""}" data-at="${r.at}"${chain ? ` title="${t("request.chain.show_title")}"` : ""}>
      ${cell(t("request.column.time_column"), timeControl, "num muted", ` title="${esc(stamp(r.at))}"`)}
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
    </tr>${chainOpen ? chainRows(r) : ""}`;
  }).join("") : live ? "" : `<tr role="row"><td role="cell" colspan="13" class="empty">${empty}</td></tr>`;
  firstDraw($("requests"));
  if (focused) [...$("requests").querySelectorAll("[data-chain-toggle]")]
    .find((button) => button.dataset.chainToggle === focused)?.focus({ preventScroll: true });
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
// Dynamic glyphs share one decorative SVG wrapper, so their size and Lucide attributes stay aligned.
const ICON_PATHS = {
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z" />',
  brain: '<path d="M12 18V5" /> <path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4" /> <path d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5" /> <path d="M17.997 5.125a4 4 0 0 1 2.526 5.77" /> <path d="M18 18a4 4 0 0 0 2-7.464" /> <path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517" /> <path d="M6 18a4 4 0 0 1-2-7.464" /> <path d="M6.003 5.125a4 4 0 0 0-2.526 5.77" />',
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
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name]}</svg>`;
}
// The flag chips name registered glyphs; a flag without a glyph keeps its text.
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

// The phone folds the Tier, Tools and Cooldown columns into a row of chips under the name.
// The chips draw lucide: a wrench for tools, snow for cooldown and a brain for reasoning.
const CHIP_ICON_NAMES = { tools: "wrench", cool: "snowflake", brain: "brain" };

function phoneChips(m) {
  // The desktop columns a phone drops ride as labelled chips, so the card row stays full.
  const tier = m.tier ? `<span class="chip flag" title="${t("model.column.tier_column")}">${t("model.mobile.tier_text", { value: esc(tierLetter(m.tier)) })}</span>` : "";
  const order = m.order ? `<span class="chip flag" title="${t("model.column.order_column")}">${t("model.mobile.order_text", { value: m.order })}</span>` : "";
  const context = m.max_input_tokens
    ? `<span class="chip flag" title="${t("model.column.context_column")}">${t("model.mobile.context_text", { value: `<span class="num">${tokens(m.max_input_tokens)}</span>` })}</span>`
    : "";
  // A state the model does not support shows no chip at all.
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

function renderTiers() {
  $("tiers").innerHTML = ["All", "A", "B", "C", "D"].map((tier) =>
    actionButton(esc(tier === "All" ? t("model.type.all_button") : tier), {
      variant: `filter${tier === state.tier ? " on" : ""}`,
      attrs: { "data-tier": tier },
    }),
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
const EFFORTS = {
  none: t("model.effort.none_label"),
  minimal: t("model.effort.minimal_label"),
  low: t("model.effort.low_label"),
  medium: t("model.effort.medium_label"),
  high: t("model.effort.high_label"),
  xhigh: t("model.effort.xhigh_label"),
  max: t("model.effort.max_label"),
};
function effortChip(name, label = EFFORTS[name] || name) {
  const level = name in EFFORTS ? ` e-${name}` : "";
  return `<span class="chip flag effort${level}">${esc(label)}</span>`;
}
// A reasoning model shows its default effort from the catalog, else Yes.
function reasoningCell(m) {
  if (!m.reasoning || !m.effort) return yesNo(m.reasoning);
  const known = m.effort in EFFORTS;
  const label = known ? EFFORTS[m.effort] : m.effort.charAt(0).toUpperCase() + m.effort.slice(1);
  return `<span class="chip flag effort${known ? ` e-${m.effort}` : ""}" title="${t("model.detail.default_effort_label")}">${esc(label)}</span>`;
}
const yesNo = (on) => statusBadge(on ? t("common.boolean.yes_text") : t("common.boolean.no_text"), { variant: on ? "yes" : "muted" });

// The modal of 1 model: a card that leads with the model, its developer and its provider.
// The capabilities read as lucide icon chips, the limits as stats, the efforts as chips.
function openModelModal(m) {
  const { provider, dev, model } = parts(m.id);
  $("model-modal-title").innerHTML =
    `${mark(provider)}<span class="name">${esc(model || m.slug || m.id)}</span>`;
  // A lobe mark rides beside a part that owns one; a part without a file keeps plain text.
  const markImg = (name) => (MARK_FILES.has(name) ? mark(name) : "");
  const identity = [
    [t("model.column.model_column"), model || m.slug || m.id, ""],
    ...(dev ? [[t("model.detail.developer_label"), dev, markImg(dev)]] : []),
    [t("model.detail.provider_label"), provider, markImg(provider)],
  ].map(([label, value, icon]) =>
    `<div class="id-row"><span class="label">${esc(label)}</span>${icon}<span class="value">${esc(value)}</span></div>`)
    .join("");
  // The modal chips carry icon and word together, so a chip reads without its title.
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
  // Only the weight draws a bar. A floor in the style keeps the blocks at one height.
  const stats = [
    m.max_input_tokens ? [m.max_input_tokens.toLocaleString(), t("model.detail.input_tokens_label")] : null,
    m.max_output_tokens ? [m.max_output_tokens.toLocaleString(), t("model.detail.output_tokens_label")] : null,
    m.weight != null ? [m.weight.toFixed(2), t("model.detail.weight_label"), progressMeter(m.weight)] : null,
  ].filter(Boolean).map(([value, label, bar]) =>
    `<div class="stat"><span class="num">${esc(value)}</span><span class="label">${esc(label)}</span>${bar || ""}</div>`)
    .join("");
  const efforts = (m.efforts || []).map((name) => effortChip(name)).join("");
  $("model-modal-body").innerHTML = `<div class="model-id">${identity}</div>`
    + (chips ? `<div class="model-chips">${chips}</div>` : "")
    + (stats ? `<div class="model-stats">${stats}</div>` : "")
    + (efforts ? `<h4>${t("model.detail.supported_efforts_label")}</h4><div class="pills">${efforts}</div>` : "");
  $("model-modal").showModal();
}
// Red at 0, orange at 0.5 and blue at 1, mixed in between.
function weightColor(weight) {
  const high = weight >= 0.5;
  const share = Math.round((high ? (weight - 0.5) * 2 : weight * 2) * 100);
  return high ? `color-mix(in oklch, var(--accent) ${share}%, var(--orange))`
    : `color-mix(in oklch, var(--orange) ${share}%, var(--red))`;
}
const progressMeter = (value) => `<div class="track"><div class="fill"
  style="width:${Math.round(value * 100)}%;background:${weightColor(value)}"></div></div>`;

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
// Restart the arrival on 1 host: a filter selection or a section selection moves its own block, and the
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

// A filter selection of the Models tab moves the rows only: the table arrives again, and the page holds
// still, so a pool card, a select or the search box never moves the whole view.
function selectRows() {
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
      <td>${mobileLabel(t("model.column.weight_column"))}<span class="cell-value">${m.weight == null ? dash
        : `<div class="weight">${progressMeter(m.weight)}<span class="num">${m.weight.toFixed(2)}</span></div>`}</span></td>
    </tr>`).join("") : `<tr><td colspan="9" class="empty">${empty}</td></tr>`);
}

// The time left of a cooldown, such as 59s, 4m 05s or 3h 12m.
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
      <td class="num muted mono"><span${k.start ? ` title="${t("settings.keys.only_start_title")}"` : ""}>${k.start ? esc(k.start) + "&hellip;" : "-"}</span></td>
      <td class="hide-sm muted">${stamp(k.created)}</td>
      <td class="muted">${mobileLabel(t("settings.keys.last_used_column"))}<span class="cell-value">${k.used ? stamp(k.used) : t("settings.keys.used_never_text")}</span></td>
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

// The block keys that the form edits. The YAML view edits the other keys.
const FORM_KEYS = ["api_key", "account_id", "client_keys", "api_base", "api_type", "discovery_url", "discovery_match", "exclude", "tier", "models", "hooks"];
// The pattern fields take the same 4 shapes, so 1 hint serves them all.
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
    const shadow = file.shadow ? t("provider.file.shadow_text", { file: file.shadow }) : "";
    return actionButton(`${esc(fileName(file.path))}${mark}`, {
      variant: `tab${index === state.file ? " on" : ""}`,
      attrs: { "data-file": index, title: `${file.path}${shadow}` },
    });
  }).join(" ");
  // The page actions ride in the file row, as 1 more chip. The main provider file stays:
  // only a {provider}.yml file can go.
  const drop = current?.main === false
    ? actionButton(t("action.delete_file_button"), { variant: "tab danger", attrs: { id: "drop-provider" } }) : "";
  $("files").innerHTML = `${chips} ${actionButton(t("action.new_provider_button"), {
    variant: "tab", attrs: { id: "new-provider" },
  })}${drop ? ` ${drop}` : ""}`;
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
  if (error) showWrite("providers", t("provider.yaml.error_text", { error }), true);
  else clearWrite("providers");
  if (error) state.view = "yaml";
}

function openFile(index) {
  const editor = $("editor");
  if (state.files.length && editor) state.files[state.file].text = editor.value;
  state.file = index;
  showFileError(index);
  // The selection of the other file would ride over into this one, and the pane would show the
  // content of the file under a section of the other. The new file opens its first section.
  const section = sectionState("provider-form");
  section.key = "";
  section.open = false;
  renderFiles();
  renderForm();
  // The selected file arrives its form, so a file change reads on the page.
  replay($("provider-form"));
}

// A chip for 1 value. The × button deletes the list item or the map key.
const pill = (text, path, key) => `<span class="pill">${esc(text)}${actionButton(icon("x"), {
  attrs: { title: t("action.remove_button"), "data-drop": JSON.stringify([...path, key]) },
})}</span>`;

// The "+ Add" button. A click puts an inline input in its place.
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
const settingPill = (group, key, value, index) => `<span class="pill">${esc(value)}${actionButton(icon("x"), {
  attrs: { title: t("action.remove_button"), "data-setting-drop": JSON.stringify([group, key, index]) },
})}</span>`;
const settingAdder = (group, key) => actionButton(t("action.add_button"), {
  variant: "add", attrs: { "data-setting-add": JSON.stringify([group, key]) },
});

// The name of a hook file as a chip shows it, without the folder of the config.
const hookName = (name) => esc(name.replace(/^hooks\//, ""));

// The installed hooks whose scope fits a card: a global hook, then 1 that names the provider, its
// file or the model. `kind` is "provider" or "model", and `name` is the provider or the model id.
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

// The form of a provider or a model, where each hook row selects a surface and 1 file that fits the
// card. The rows write through the path of the form, and the same chip flow as the Settings page.
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
    attrs: { title: t("action.remove_button"), "data-form-hook-drop": at },
  });
  return `<span class="pill hook"><select data-form-hook-surface='${esc(at)}' aria-label="${t("hook.surface.label")}">${
    HOOK_SURFACES.map(([key, label]) => `<option value="${key}"${key === surface ? " selected" : ""}>${esc(label)}</option>`).join("")
    }</select>${selector}${drop}${menu}</span>`;
};

// The hook rows of a provider block or of a model override, with the installed hooks that fit.
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

// The request surfaces of the settings group: the `on-*` keys of its defaults, in surface order. The
// card names every file at the surfaces it runs at, so the settings hold no list of its own.
const requestSurfaces = () => HOOK_SURFACES.filter(([key]) => key in (state.settings?.defaults?.hooks ?? {}));

// The saved surface order is separate from the explicit request-hook lists. An order changes only
// precedence, so a metadata hook keeps its provider, model or client scope.
function hookOrderState() {
  if (!state.settings.hookOrder) {
    const saved = setting("hooks", "order");
    state.settings.hookOrder = Object.fromEntries(Object.entries(saved && typeof saved === "object" ? saved : {})
      .map(([surface, files]) => [surface, Array.isArray(files) ? [...files] : []]));
  }
  return state.settings.hookOrder;
}

// The preferred files first, then every explicitly named or installed file that supports the
// surface. The row order from the server is file-name order.
function hookOrderFiles(surface) {
  const saved = hookOrderState()[surface] || [];
  const explicit = surface in (state.settings?.defaults?.hooks ?? {}) ? settingList("hooks", surface) : [];
  const installed = (state.settings?.hook_rows ?? [])
    .filter((row) => !row.problem && ((row.surfaces || []).includes(surface) || (row.runs || []).includes(surface)))
    .map((row) => row.path);
  return [...new Set([...saved, ...explicit, ...installed])];
}

// Request surfaces stay visible when empty. The other surfaces show when an installed hook uses
// them, so an on-catalog or on-upstream hook also gets an order control.
const orderedSurfaces = () => HOOK_SURFACES.filter(([surface]) =>
  surface in (state.settings?.defaults?.hooks ?? {}) || hookOrderFiles(surface).length);

// The surface chips of a hook row: 1 chip per surface the block of the file names. The file defines
// what it hooks into, so the card shows the surfaces and holds no control over them.
function hookSurfacesCell(row) {
  const labels = new Map(HOOK_SURFACES.map(([key, label]) => [key, label]));
  const named = new Set(row.runs || []);
  const chips = (row.surfaces || []).map((surface) => {
    const mark = named.has(surface) ? " named" : "";
    return `<span class="pill ${row.enabled ? "on" : "off"}${mark}">${esc(labels.get(surface) || surface)}</span>`;
  }).join("");
  return `<div class="pills">${chips || `<em class="none">${t("hook.empty.surface_text")}</em>`}</div>`;
}

// The candidate files of each surface. Runtime filters their metadata scope before it applies this
// order, and files not shown here keep their existing order after these files.
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

// The order block of the Hooks card, after a move.
function renderHookOrder() {
  const host = $("hooks-order");
  if (host) host.innerHTML = hookOrderRows();
}

// The move of 1 file in the order of a surface: up is -1, down is 1.
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

// The note under the sources. The update fills it with the old and the new version of each file.
function hooksNote(text) {
  const note = $("hooks-note");
  if (note) note.textContent = text;
}

function updateNote(answer) {
  const moved = (answer.hooks || []).filter((hook) => hook.moved);
  // A source that fetched and moved nothing says why, so the note diagnoses itself.
  if (moved.length) {
    const files = moved.map((hook) => t("hook.update.transition_text", {
      name: hook.name, before: hookVersion(hook.before), after: hookVersion(hook.after),
    })).join(", ");
    return t("hook.update.moved_text", { files });
  }
  return t("hook.empty.moved_text");
}

// A failure rides the floating note in the red of the theme, not the line under the card.
function updateFails(answer) {
  return (answer.report || []).join(" ");
}

// The manager of the Hooks card: the folder, the sources, the files that load and the update.
function hooksManager() {
  const rows = state.settings.hook_rows || [];
  const sources = listValue("hooks", "sources");
  const disabled = listValue("hooks", "disabled");
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
    return `<tr><td role="cell" class="name"><span class="cell-value" title="${esc(row.name)}"><span class="hook-title">${esc(title)}</span>${author}</span></td>
      <td role="cell"><span class="cell-value">${esc(hookVersion(row))}</span></td>
      <td role="cell" class="surfaces">${hookSurfacesCell(row)}</td>
      <td role="cell"><span class="cell-value">${esc(row.scope || t("hook.scope.global_text"))}${(row.targets || []).length ? `: ${esc(row.targets.join(", "))}` : ""}</span></td>
      <td role="cell" class="hide-sm"><span class="cell-value" title="${esc(row.problem || source || t("hook.scope.local_text"))}">${row.problem ? statusBadge(row.problem, { variant: "bad" }) : esc(record.repo || t("hook.scope.local_text"))}${record.commit ? `<span class="hook-sha">${esc(record.commit.slice(0, 7))}</span>` : ""}</span></td>
      <td role="cell"><input type="checkbox" role="switch" class="switch" data-hook-toggle="${esc(row.name)}"
        ${disabled.includes(row.name) ? "" : "checked"} aria-label="${t("hook.state.enabled_named_label", { name: esc(row.name) })}"></td>
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
      <div class="card-scroll"><table class="keys"><thead><tr><th>${t("hook.column.author_title")}</th><th>${t("hook.column.version_column")}</th><th>${t("hook.column.surfaces_column")}</th><th>${t("hook.column.scopes_column")}</th><th class="hide-sm">${t("hook.column.source_column")}</th><th>${t("hook.column.enabled_column")}</th><th></th></tr></thead>
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
          attrs: { "data-hook-source-drop": true, title: t("hook.action.delete_source_title", { name: selected }) },
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
  if (on) disabled.delete(name);
  else disabled.add(name);
  setListValue("hooks", "disabled", [...disabled]);
  // The save re-renders the table after the write. The chips flip in place first, so the color
  // transition runs on the same node instead of a swap that no transition can cross.
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

// The inline input of a Settings chip list. Enter or a click away keeps the value.
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

// The selected section of each list, and whether the phone shows that section or the list.
const SECTION_STATE = { settings: { key: "", open: false }, providers: { key: "", open: false } };
const sectionState = (host) => SECTION_STATE[host.id === "settings" ? "settings" : "providers"];

// A section list: every section in a rail, and the pane shows the selected 1.
function sectionList(id, items, active) {
  const buttons = items.map(([key, label]) => actionButton(esc(label), {
    variant: key === active ? "on" : "",
    attrs: { "data-section": key, "aria-current": key === active ? "true" : null },
  })).join("");
  return `<nav class="sections" aria-label="${t("common.sections_label")}">${buttons}</nav>`;
}

// The pane of a section list. The back button names the page it returns to, as iOS does.
function sectionPane(back, cards) {
  const button = actionButton(`<span class="chev" aria-hidden="true">&lsaquo;</span> ${esc(back)}`, {
    variant: "ghost back", attrs: { "data-back": true },
  });
  return `<div class="section-pane">${button}${cards}</div>`;
}

// The last section of a list: the file text, and its own save. It holds the lines that the form
// cannot show, so a broken file still opens.
function yamlCard(editor, save, hint) {
  return `<div class="card yaml-view" data-section="yaml"><h3>${t("yaml.label")}</h3><p class="sub">${esc(hint)}</p>`
    + `<textarea id="${editor}" spellcheck="false" aria-label="${t("common.file_text_label")}"></textarea>`
    + `<div class="yaml-save">${actionButton(t("action.save_button"), {
      variant: "primary", attrs: { id: save, disabled: true },
    })}`
    + `<span class="hint">${t("settings.yaml.save_hint")}</span></div></div>`;
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

// The selection of a section: 1 button on, 1 card shown, and the phone leaves the list.
function selectSection(host, key, open = sectionState(host).open) {
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
  markCardScrollEdges();
  markSectionPaneEdges();
  if (!moved) return;
  // The slide plays at once, and the flag holds it for a render that builds the pane again.
  state_.slide = true;
  playSlide(host, open);
}

// A phone turns a selection into its own page: the section rides in the hash, so the back gesture returns.
const phoneSection = () => matchMedia("(max-width: 900px)").matches;

function openSection(host, key) {
  selectSection(host, key, true);
  // The selected card arrives: a section change of Providers or Settings reads on the page.
  replay([...host.querySelectorAll(".section-pane > .card")]
    .find((card) => card.dataset.section === key));
  // The section rides in the hash, so a refresh keeps it; a phone also gets a back step.
  const [path] = location.hash.split("?");
  const url = `${path}?section=${encodeURIComponent(key)}`;
  if (phoneSection()) history.pushState({ section: key }, "", url);
  else history.replaceState(null, "", url);
}

// The back button leaves the pushed page when it can, and closes the section either way.
function closeSection(host) {
  const key = sectionState(host).key;
  if (history.state?.section === key) return history.back();
  const [path] = location.hash.split("?");
  history.replaceState(null, "", path);
  selectSection(host, key, false);
}

// The section of the shown page, from the hash: a deep link and the phone back gesture read it.
function applySectionHash() {
  const [path, query] = location.hash.split("?");
  const host = path === "#/settings" ? $("settings") : path === "#/providers" ? $("provider-form") : null;
  if (!host) return;
  const key = new URLSearchParams(query ?? "").get("section");
  const known = [...host.querySelectorAll(".sections button")].some((button) => button.dataset.section === key);
  if (key && known) selectSection(host, key, true);
  else if (key) {
    // The rail renders later, on the first data: the selection rides the state until then.
    const state_ = sectionState(host);
    state_.key = key;
    state_.open = true;
  } else selectSection(host, sectionState(host).key, false);
}

// A phone back moves the section before the page builds its form, and that build replaces the
// pane. The slide of the selection replays once on the fresh markup, so the back reads as a move.
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
  const mark = `<span class="hint" aria-hidden="true">${icon("info")}</span>`;
  return `<details class="fold"><summary aria-describedby="${id}">${esc(label)}${mark}`
    + `<small id="${id}" role="tooltip" aria-hidden="true">${esc(hint)}</small></summary>`
    + `<div class="fold-body">${body}</div></details>`;
}

function providerCard(name, block) {
  const path = [name];
  if (!block || typeof block !== "object" || Array.isArray(block)) {
    return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
      <p class="sub">${t("provider.block.invalid_text")}</p></div>`;
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
    if (hasSaved) return t("provider.secret.database_text");
    if (row.state === "env" || row.has_env) return t("provider.secret.environment_text");
    return "";
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
      if (hasSaved) return t("provider.secret.database_text");
      return t("provider.secret.database_text");
    }
    return maskedPlaceholder(row);
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
      aria-haspopup="dialog" aria-controls="modal" data-value="${esc(label)}" data-set='${esc(JSON.stringify([...path, key]))}' value="${esc(value)}" placeholder="${esc(placeholder)}">`);
  };
  const tiers = TIERS.map((tier) => `<div class="tier-row"><span class="tier">${tier.slice(-1)}</span>
    ${listField(tier, block.tier?.[tier], [...path, "tier", tier])}</div>`).join("");
  const models = block.models && typeof block.models === "object" ? block.models : {};
  const overrides = Object.entries(models).map(([pattern, values]) => {
    // The hooks of the override ride in their own rows, so the value pills show the other keys.
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
  return `<div class="card provider" data-section="${esc(name)}" data-provider="${esc(name)}"><h3>${esc(name)}</h3>
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

// The section list of the current file: every provider of the file, and the selected card.
function renderForm() {
  const host = $("provider-form");
  const blocks = state.forms[state.file];
  // A render keeps the text of an edit in progress. The saved text shows otherwise.
  const typed = state.view === "yaml" ? $("editor")?.value : undefined;
  if (!state.files.length) return (host.innerHTML = "");
  if (blocks == null) {
    host.innerHTML = sectionList("provider-form", [["yaml", t("yaml.label")]], "yaml")
      + sectionPane(t("nav.providers_link"), `<div class="card" data-section="error"><h3>${t("provider.yaml.invalid_text")}</h3>
      <p class="sub">${esc(state.files[state.file].error ?? "")}</p>
      <p class="sub">${t("provider.yaml.fix_text")}</p></div>`
      + yamlCard("editor", "yaml-save", t("provider.yaml.file_hint", { file: fileName(state.files[state.file].path) })));
    selectSection(host, "yaml");
    $("editor").value = typed ?? state.files[state.file].text;
    renderFiles();
    return;
  }
  const names = Object.keys(blocks);
  const items = names.map((name) => [name, name]).concat([["yaml", t("yaml.label")]]);
  const selected = sectionState(host).key;
  const active = items.some(([key]) => key === selected) ? selected : items[0][0];
  host.innerHTML = sectionList("provider-form", items, active)
    + sectionPane(t("nav.providers_link"), names.map((name) => providerCard(name, blocks[name])).join("")
      + yamlCard("editor", "yaml-save", t("provider.yaml.file_hint", { file: fileName(state.files[state.file]?.path ?? "") })));
  selectSection(host, active);
  $("editor").value = typed ?? state.files[state.file]?.text ?? "";
  renderFiles();
  placeUndo();
  fitSectionPane();
}

// 1 card again after an edit, in the same place, and the selection stays.
function renderCard(name) {
  const card = [...document.querySelectorAll("#provider-form [data-provider]")].find((item) => item.dataset.provider === name);
  if (card) card.outerHTML = providerCard(name, state.forms[state.file][name]);
  selectSection($("provider-form"), sectionState($("provider-form")).key);
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
    // A repeat is a no-op, so no double value lands in the list.
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
  button.title = t("action.undo_write_label");
  button.setAttribute("aria-label", t("action.undo_write_label"));
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
  // The row reads {label} {undo} {control}: the icon sits beside the control it undoes, so the
  // label keeps its place and the row never moves.
  const control = row.querySelector(".input, .pills, select, input");
  if (control) row.insertBefore(lastWrite.button, control);
  else row.prepend(lastWrite.button);
}

// No write waits a way back: the icon of the last write leaves the page.
function dropUndo() {
  lastWrite = null;
  document.querySelector(".undo-step")?.remove();
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

// The shown view is the YAML section of the page when its card is the selected 1.
const yamlView = () => (location.hash.startsWith("#/settings")
  ? state.settingsView === "yaml"
  : state.view === "yaml");

// A selection sets the view of the page: the form rows, or the file text. A leave of the text with
// unsaved lines asks first, then shows the saved file again.
async function selectProviderSection(key) {
  const host = $("provider-form");
  const view = key === "yaml" ? "yaml" : "form";
  if (view !== state.view) {
    if (dirty() && !(await ask(t("dialog.discard.title"), t("dialog.discard.message"), t("action.discard_button"), true))) return;
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

// The request-level hook surfaces, in the order of `daedalus/providers/hooks.py`.
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
  // The 5th element of a row lists the modes that show it. Only `mode` always shows.
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
  // The request surfaces live in the hooks table, so the card holds the manager alone.
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
  "balance.success", "balance.fault", "balance.slow", "balance.hourly", "balance.rate_limit",
  "routing.threshold",
]);
// The input bounds. The server also checks them before saving.
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
  return `<div class="card" data-section="keys"><h3>${t("settings.keys.title")}</h3>
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
    <div class="card-scroll"><table class="keys">
      <thead><tr>
        <th>${t("settings.keys.name_column")}</th><th>${t("settings.keys.key_column")}</th><th class="hide-sm">${t("settings.keys.created_column")}</th><th>${t("settings.keys.last_used_column")}</th><th></th>
      </tr></thead>
      <tbody id="keys"></tbody>
    </table></div>
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
  // The section list: the rail names every group, and the pane holds its card.
  const groups = SETTINGS;
  const items = groups.map(([group, title]) => [group, title]).concat([
    ["keys", t("settings.keys.title")], ["yaml", t("yaml.label")],
  ]);
  const selected = sectionState($("settings")).key;
  const active = items.some(([key]) => key === selected) ? selected : items[0][0];
  $("settings").innerHTML = sectionList("settings", items, active)
    + sectionPane(t("nav.settings_link"), groups.map(([group, title, fields]) => card([group, title, fields]).replace(
      '<div class="card">', `<div class="card" data-section="${esc(group)}">`)).join("")
      + keysCard()
      + yamlCard("settings-editor", "settings-yaml-save", t("settings.yaml.hint", { file: fileName(state.settings.path) })));
  selectSection($("settings"), active);
  $("settings-editor").value = typed ?? state.settings.text;
  // The rendered file wins, and the shown rows follow its mode.
  showAffinityRows(setting("affinity", "mode"));
  showSwitchRows();
  renderSettingsSave();
  placeUndo();
  fitSectionPane();
  keepSettingsScroll(scroll);
  markCardScrollEdges();
}

// A row with a mode list shows only under those modes: the race values wait for race, and the pin
// values wait for session or race. Only `mode` always shows. The rows stay in the page, so a selection
// keeps its value and the card moves rows only.
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
      if (!off) replay(field);
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
  // The hook manager sends its own rows: the folder, sources, disabled names and surface order.
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

async function saveSettings() {
  // A change that lands back on the saved value makes no write, so the last Undo goes.
  if (!settingsDirty()) {
    dropUndo();
    return clearWrite("settings");
  }
  const text = $("settings-editor")?.value ?? "";
  const body = state.settingsView === "yaml" ? { text } : { changes: settingsChanges() };
  rememberWrite("settings", { settings: true, text: state.settings.text }, state.writeAnchor ?? $("settings-yaml-save"));
  try {
    await call("settings", { method: "PUT", body: JSON.stringify(body) });
    await loadSettings();
    clearWrite("settings");
    refresh();
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin(t("auth.session_ended"));
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

// The balances, then the 3 rate-limit rows with the least left.
function overviewLimits(data) {
  if (!data) return "";
  const bar = (left) => (left == null ? "" : progressMeter(left));
  const balances = data.providers.flatMap((p) => p.items.map(([label, value, left]) =>
    `<div class="balance">${line(`${esc(p.name)} &middot; ${esc(label)}`, esc(value))}${bar(left)}</div>`));
  const share = (r) => (r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0);
  const rows = data.lanes.flatMap((lane) => lane.rows.map((row) => ({ ...row, model: lane.model })))
    .sort((a, b) => share(a) - share(b)).slice(0, 3)
    .map((r) => `<div class="balance">${line(`<span title="${esc(r.model)}">${modelName(r.model)}</span>`,
      `${t("limits.value.of_text", { remaining: floorCount(r.remaining), limit: floorCount(r.limit) })} ${esc(unit(r))}`,
      { title: t("limits.value.of_text", { remaining: r.remaining.toLocaleString(), limit: r.limit.toLocaleString() }) })}${bar(share(r))}</div>`);
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
  $("limits-checked").textContent = data.checked
    ? t("limits.checked_text", { time: stamp(data.checked) })
    : t("limits.not_checked_text");
  $("balances").hidden = !data.providers.length;
  draw("balances", data.providers.map((p) => `<div class="card"><h3>${esc(p.name)}</h3>
    ${p.items.map(([label, value, left]) => `<div class="balance">${line(esc(label), esc(value))}
      ${left == null ? "" : progressMeter(left)}</div>`).join("")}</div>`).join(""));
}

function renderLimitRows(rows) {
  $("limits-clear").hidden = !state.limitSearch;
  draw("limit-rows", rows.length ? rows.map((r) => `<tr>
      ${nameCell(r.model, `${modelName(r.model)}${r.client ? ` <span class="muted">${esc(r.client)}</span>` : ""}`)}
      <td title="${esc(limitTitle(r))}">${esc(limitUnit(r))}</td>
      <td><div class="weight left" title="${t("limits.value.of_text", { remaining: r.remaining.toLocaleString(), limit: r.limit.toLocaleString() })}">
        ${progressMeter(r.limit > 0 ? Math.min(1, r.remaining / r.limit) : 0)}
        <span class="num${r.remaining > 0 ? "" : " out"}">${t("limits.value.of_text", { remaining: floorCount(r.remaining), limit: floorCount(r.limit) })}</span></div></td>
      ${limitTimeCell(r.reset, "hide-sm muted time")}
      ${limitTimeCell(r.at, "hide-sm muted time")}
    </tr>`).join("") : `<tr><td colspan="5" class="empty">${t("limits.empty_text")}</td></tr>`);
}

const DIFF_KINDS = [
  ["added", "+", "added"],
  ["removed", "-", "removed"],
  ["moved", "~", "changed"],
];

function rebuildDiff(event) {
  const marks = DIFF_KINDS.map(([kind, mark, key]) =>
    event[key].length ? `<span class="${kind}">${mark}${event[key].length}</span>` : "").join(" ");
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
      ...oldEfforts.filter((value) => !newEfforts.includes(value)).map((value) => effortChip(value)),
      ...newEfforts.filter((value) => !oldEfforts.includes(value)).map((value) => effortChip(value)),
    ];
    if (chips.length) return chips;
  }
  const chip = (value) => {
    if (name === "reasoning_effort") return effortChip(value);
    if (name === "mode") return diffChip(MODES[value] || value, "", "mode");
    const shown = name === "tier" ? tierLetter(value) : diffShown(value);
    return diffChip(`${DIFF_LABELS[name] || name} ${shown}`);
  };
  const chips = [];
  if (before != null) chips.push(chip(before));
  if (after != null) chips.push(chip(after));
  if (!chips.length) {
    chips.push(diffChip(`${DIFF_LABELS[name] || name} ${oldText}`));
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

// The modal of 1 rebuild: the full diff, one line per added, removed or moved model.
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
  const item = (kind, at, title, body, attrs = null) => {
    const content = `<span class="notification-mark" aria-hidden="true"></span>
      <span class="notification-copy"><span class="notification-head"><b>${title}</b>${timeText(at, { tag: "time", variant: "muted" })}</span>
      <span class="notification-body">${body}</span></span>`;
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
        t("notification.reset_text", { provider: esc(event.provider), unit: esc(limitUnit(event)) }),
        event.client
          ? t("notification.client_text", { client: esc(event.client) })
          : t("notification.provider_renewed_text"),
      ),
    })),
    ...(data.limits || []).map((event) => ({
      at: event.at,
      html: item(
        "warning",
        event.at,
        t("notification.limit_near_text", { model: esc(event.model) }),
        t("notification.limit_left_text", {
          remaining: floorCount(event.remaining),
          limit: floorCount(event.limit),
          unit: esc(limitUnit(event)),
          client: event.client ? ` · ${esc(event.client)}` : "",
        }),
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
    `<h3>${t("notification.title")}</h3>` +
      (notices.length ? notices.map((notice) => notice.html).join("") : none(t("notification.empty_text")))
  );
  const update = data.update;
  let body = `<h3>${t("update.title")}</h3>`;
  if (!update) body += none(t("update.not_checked_text"));
  else if (update.error === "no commit in this build's version")
    body += none(t("update.no_commit_text"));
  else if (update.error) body += none(t("update.check_failed_text", { error: esc(update.error) }));
  else if (update.channel === "release")
    body += update.update
      ? line(t("update.available_text"), `<a href="${esc(update.url)}">${esc(update.latest)}</a>`) +
        line(t("update.you_are_on_text"), esc(update.current))
      : line(t("update.up_to_date_text"), esc(update.latest || update.current));
  else if (update.update) body += line(t("update.behind_main_text"), count(update.behind, "commit"));
  else body += line(t("update.up_to_date_text"), "main");
  body += line(t("update.checked_text"), update ? stamp(update.at) : "-");
  draw(
    "update-card",
    body + actionButton(`${icon("refresh_cw")} ${t("action.check_now_button")}`, {
      variant: "ghost", attrs: { id: "update-check" },
    })
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
  $("limits-message").textContent = t("action.checking_button");
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
// A rebuild notification opens the modal of its diff.
$("notification-feed").addEventListener("click", (event) => {
  const row = event.target.closest("[data-rebuild]");
  if (!row || !state.notifications) return;
  const index = Number(row.dataset.rebuild);
  openDiffModal(state.notifications.rebuilds[index]);
});
$("update-card").addEventListener("click", async (event) => {
  const button = event.target.closest("#update-check");
  if (!button) return;
  button.disabled = true;
  button.textContent = t("action.checking_button");
  try {
    const result = await call("updates", { method: "POST" });
    if (state.notifications) {
      state.notifications = { ...state.notifications, update: result };
      renderNotifications(state.notifications);
    }
  } catch (error) {
    if (error instanceof LoggedOut) return showLogin();
    button.disabled = false;
    button.innerHTML = `${icon("refresh_cw")} ${t("action.check_now_button")}`;
  }
});
$("show-password").addEventListener("click", () => showPassword($("login").password.type === "password"));
document.addEventListener("click", async (event) => {
  // The info icon of a closed group shows its hint. It never opens the group.
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
$("reset-weights").addEventListener("click", async () => {
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

// A field that stops at its length says the rule, so the cut is not silent.
function showLengthLimit(input, message) {
  const limit = t("validation.characters_at_most_text", { count: input.maxLength });
  if (input.maxLength > 0 && input.value.length >= input.maxLength) {
    message.textContent = limit;
  } else if (message.textContent === limit) {
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
  refresh();
});
$("notice-close").addEventListener("click", () => {
  closeNotice();
});
// The leave plays out, then hides the note; a new failure interrupts the leave.
$("notice").addEventListener("animationend", (event) => {
  if (event.animationName !== "leave") return;
  const note = $("notice");
  note.classList.remove("leave");
  note.hidden = true;
});

$("requests").addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-chain");
  if (button) {
    const found = state.requests.find((r) => String(r.at) === button.dataset.at);
    try {
      await navigator.clipboard.writeText(chainText(found));
      button.textContent = t("action.copied_button");
    } catch {
      getSelection().selectAllChildren(button.closest("td"));
      button.textContent = t("action.press_ctrl_c_button");
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
const PAGES = ["overview", "requests", "models", "limits", "notifications", "providers", "settings"];
const PAGE_LABELS = {
  overview: t("nav.overview_link"),
  requests: t("nav.requests_link"),
  models: t("nav.models_link"),
  limits: t("nav.limits_link"),
  notifications: t("nav.notifications_link"),
  providers: t("nav.providers_link"),
  settings: t("nav.settings_link"),
};

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

function markScrollEdges(scroller) {
  const end = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  const left = scroller.scrollLeft <= 2;
  const right = scroller.scrollLeft >= end - 2;
  scroller.classList.toggle("fade-left", !left);
  scroller.classList.toggle("fade-right", !right);
  return { left, right };
}

function markCardScrollEdges() {
  for (const scroller of document.querySelectorAll(".card-scroll")) markScrollEdges(scroller);
}

function markPaneEdges(pane) {
  const end = Math.max(0, pane.scrollHeight - pane.clientHeight);
  const top = pane.scrollTop <= 2;
  const bottom = pane.scrollTop >= end - 2;
  pane.classList.toggle("fade-top", !top);
  pane.classList.toggle("fade-bottom", !bottom);
  return { top, bottom };
}

function markSectionPaneEdges() {
  for (const pane of document.querySelectorAll(".section-pane")) markPaneEdges(pane);
}

document.addEventListener("scroll", (event) => {
  const scroller = event.target;
  if (scroller.matches?.(".card-scroll")) markScrollEdges(scroller);
  if (scroller.matches?.(".section-pane")) markPaneEdges(scroller);
}, true);

// A chevron at each end of the tab bar shows the tabs that wait off screen, and scrolls to them.
const nav = $("nav");
function markNavSteps() {
  const { left, right } = markScrollEdges(nav);
  $("nav-left").hidden = left;
  $("nav-right").hidden = right;
}
nav.addEventListener("scroll", markNavSteps, { passive: true });
// A tab reuses the entry in place of pushing one, so the phone back leaves the app
// in place of walking the tabs in reverse.
for (const link of document.querySelectorAll("#nav a")) {
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
    event.preventDefault();
    history.replaceState(null, "", link.getAttribute("href"));
    showPage();
  });
}
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
  const [path, query] = location.hash.split("?");
  const asked = path.replace("#/", "").replace(/^config$/, "providers").replace(/^pools$/, "models");
  const page = PAGES.includes(asked) ? asked : PAGES[0];
  const changed = page !== shownPage;
  shownPage = page;
  // A pool card lands on the page it already shows: the table below it arrives, and the page
  // itself holds still, so a filter selection never moves the whole view.
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
  // The page renders before the section selection: a render replaces the markup of the pane, and the
  // slide of a phone back rides on the classes that the selection sets.
  if (page === "providers" && state.view === "form") renderForm();
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
// A row or card of the models list opens the modal of its model.
$("models").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-model]");
  if (!row) return;
  const model = state.models.find((item) => item.id === row.dataset.model);
  if (model) openModelModal(model);
});

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
  state.mode = $("mode").value;
  selectRows();
});
$("tiers").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tier]");
  if (!button) return;
  state.tier = button.dataset.tier;
  renderTiers();
  selectRows();
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
$("files").addEventListener("click", async (event) => {
  if (!event.target.closest("#drop-provider")) return;
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
});
$("provider-form").addEventListener("input", (event) => {
  if (event.target.id === "editor") renderFiles();
});
bindValueRows($("provider-form"), () => saveForm());
bindValueRows($("settings"), () => saveSettings());
$("provider-form").addEventListener("click", (event) => {
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

// The map adder opens the 2-field modal: a name and its value, named for the map it fills.
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

// The provider values adder opens the 2-field modal: a catalog key from the select,
// and its value in the text field below it.
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
  markCardScrollEdges();
});
$("settings").addEventListener("input", (event) => {
  clearWrite("settings");
  showLengthLimit(event.target, $("settings-message"));
  // The mode decides which rows of the affinity card show. The selection stays, so only the rows move.
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
  if (section) return selectSettingsSection(section.dataset.section);
  if (event.target.closest("[data-back]")) return closeSection($("settings"));
  // The manager: the modal of a source, the delete of 1, the update and the take of a scan.
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
  // A switch and a selection list hold 1 valid value, so the change writes it at once.
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

applyLocale();
renderLegend();
guarded(start);
