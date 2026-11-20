import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import (
  MASTER,
  MAX_FAST_SECONDS,
  MIN_FAST_SECONDS,
  MOBILE_TOUCH_MIN_PX,
  _app_js_vm,
  run_app_js,
)
from fastapi.testclient import TestClient

from daedalus import dashboard
from daedalus.config import settings
from daedalus.providers import base
from daedalus.server import api


def test_page(client: TestClient) -> None:
  page = client.get("/")
  assert page.status_code == 200 and "text/html" in page.headers["content-type"]
  assert 'src="ui/app.js?v=' in page.text, "relative asset paths with a content hash"
  assert 'href="ui/style.css?v=' in page.text, "the style link has a content hash"
  assert (
    '<th scope="col" class="hide-sm" role="columnheader"'
    ' title="request.column.session.hint"'
    ' data-i18n-title="request.column.session.hint"'
    ' data-i18n="request.column.session_column">request.column.session_column</th>'
    in page.text
  ), "the localized Requests session column"
  assert '<div class="card legend" id="requests-legend">' in page.text, (
    "the code legend is a card of its own beside the table"
  )
  assert "<details" not in page.text and "<summary" not in page.text, (
    "the legend keeps no expand button"
  )
  assert "env-rows" not in page.text, "the provider block owns the keys"
  assert '<dialog id="modal"' in page.text, "the confirmation modal"
  assert "<code>frX</code>" not in page.text, "the legend body is filled by app.js"
  for block in ("ov-providers", "ov-keys", "ov-settings"):
    assert block not in page.text, f"the Overview keeps state only: no {block} block"
  for name in (
    "overview",
    "requests",
    "models",
    "limits",
    "settings",
  ):
    assert f'<section data-page="{name}"' in page.text, f"the {name} page"
  assert 'id="providers-holder"' in page.text, "the providers host waits for its card"
  assert 'name="remember" type="checkbox" checked' in page.text, "remember me starts on"
  script = client.get("/ui/app.js")
  assert script.status_code == 200 and "javascript" in script.headers["content-type"]
  reasons = {
    "ctx": "context",
    "hlt": "health",
    "lmt": "limit",
    "err": "failure",
    "rnd": "random",
    "cls": "classifier",
    "esc": "escalation",
  }
  for reason, key in reasons.items():
    assert f'  {reason}: t("request.route.{key}_text")' in script.text, reason
  assert "const REPEAT_CODE = " in script.text, "the repeat code family"
  assert 'title="${esc(label)}"' in script.text, "reason codes have hover labels"
  assert (
    'const EFFORT_SHORT = { minimal: "min", low: "low", medium: "med", high: "hi", xhigh: "xhi" }'
    in script.text
  )
  for token in (
    "action.logout_button",
    "auth.logout.message",
    "shortEffort",
    "sentText",
    "change_on_draw",
    "settings.affinity.change_on_draw.label",
    "settings.optimization.title",
    "settings.optimization.enabled.label",
    "settings.limits.title",
    "settings.privacy.title",
    "forward_owui_chat_id",
    "settings.privacy.forward_chat_id.label",
    "settings.routing.title",
  ):
    assert token in script.text, token
  assert client.get("/ui/style.css").status_code == 200
  assert script.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/index.html").status_code == 404, "listed assets only"
  manifest = client.get("/ui/manifest.json")
  assert manifest.headers["content-type"] == "application/manifest+json"
  for icon in manifest.json()["icons"]:
    assert client.get(f"/ui/{icon['src']}").status_code == 200, icon["src"]
  assert (
    'href="ui/manifest.json?v=' in page.text and 'href="ui/logo.svg?v=' in page.text
  )


def test_icon_only_remove_buttons_carry_aria_labels_and_square_boxes() -> None:
  """Icon-only action buttons share iconButton, aria-label attributes, and square .icon-btn boxes."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "function iconButton(" in app
  for marker in (
    '"data-drop": JSON.stringify([...path, key])',
    '"data-form-hook-drop": at',
  ):
    block = app.split(marker, 1)[0].rsplit("actionButton(icon", 1)[1]
    assert '"aria-label": t("action.remove_button")' in block, marker
  for marker in (
    '"data-key": k.name',
    '"data-setting-drop": JSON.stringify([group, key, index])',
    '"data-hook-drop": row.path',
    '"data-hook-source-drop": true',
    '"data-hook-rule-remove": JSON.stringify([row.name, entry.key, index])',
    '"data-drop": JSON.stringify([...path, "models", pattern])',
  ):
    block = app.split(marker, 1)[0].rsplit('iconButton("trash"', 1)[1]
    assert "danger: true" in block, marker
  assert ".pill > button:not(.select) {" in css
  assert ".icon-btn {" in css and "width: 32px; height: 32px;" in css
  assert "width: 44px; height: 44px; min-width: 44px; min-height: 44px;" in css
  assert "td.end > .ghost + .ghost { margin-left: 8px; }" in css
  hook_settings = app.split('"data-hook-open"', 1)[0].rsplit("iconButton(", 1)[1]
  assert '"settings"' in hook_settings
  assert "settings: '<path" in app
  assert "translateY(-6px) scale" not in css
  assert "openModelModal(model, row)" in app
  brand_rule = css.split(".brand {", 1)[1].split("}", 1)[0]
  assert "letter-spacing" not in brand_rule


def test_app_js_split_requests() -> None:
  """The script splits answered attempts and formats transition codes."""
  code = """
const fs = require('fs');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8');
const vm = require('vm');
const sandbox = {
  TRANSITION_REASONS: { err: 'Previous upstream attempt failed', lmt: 'Rate limit' },
  esc: s => s,
  seconds: s => s == null ? '' : s.toFixed(3) + 's',
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: {
    hidden: false,
    documentElement: { dataset: {} },
    getElementById: () => ({ innerHTML: '', addEventListener: () => {}, classList: { add: () => {},
      remove: () => {}, toggle: () => {} } }),
    querySelector: () => ({ firstChild: { textContent: 'Models' } }),
    querySelectorAll: () => [],
    addEventListener: () => {},
  },
  navigator: {},
  location: { hash: '' },
  window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }),
  $: () => ({ innerHTML: '', addEventListener: () => {} }),
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const cell = sandbox.transitionCell({ reason: 'err' });
assert(cell.includes('transition-code'));
assert(!cell.includes('previous'));
assert(!cell.includes('next'));

const r = {
  at: 100,
  status: 200,
  attempts: [
    { model: 'm1', result: 'answered', seconds: 1.0 },
    { model: 'm1', result: 'loop' },
    { model: 'm2', result: 'answered', seconds: 2.0 },
  ],
};
const split = sandbox.splitRequest(r);
assert.strictEqual(split.length, 2);
assert.strictEqual(split[0].via, 'm2');
assert.strictEqual(split[0].status, 200);
assert.strictEqual(split[1].via, 'm1');
assert.strictEqual(split[1].status, 'err');
assert.strictEqual(sandbox.statusClass({ status: 'err' }), 's5');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_model_queries_reject_inherited_keys() -> None:
  """Model queries accept defined keys and retain defaults for inherited keys."""
  code = _app_js_vm(
    """
sandbox.URLSearchParams = URLSearchParams;
sandbox.history = { replaceState() {} };
sandbox.__el("sort-small").options = [];
const probe = vm.runInContext('({state, applyModelFilters, sortModels, sortValue, MODES, FLAGS})', sandbox);
const rows = [{id: 'fixture/b', mode: 'chat'}, {id: 'fixture/a', mode: 'chat'}];
probe.applyModelFilters('');
const defaultMode = probe.state.mode;
const defaultSort = JSON.stringify(probe.state.sort);
const defaultOrder = JSON.stringify(probe.sortModels(rows));
const defaultDirection = probe.state.sort.dir;
for (const key of Object.keys(probe.sortValue)) {
  probe.applyModelFilters(new URLSearchParams({sort: key}).toString());
  assert.strictEqual(probe.state.sort.key, key);
  assert.strictEqual(probe.state.sort.dir, -defaultDirection);
}
for (const key of [...Object.keys(probe.MODES), ...Object.keys(probe.FLAGS)]) {
  probe.applyModelFilters(new URLSearchParams({mode: key}).toString());
  assert.strictEqual(probe.state.mode, key);
}
for (const key of [...Object.getOwnPropertyNames(Object.prototype), 'unknown-key', '']) {
  probe.applyModelFilters(new URLSearchParams({sort: key, mode: key}).toString());
  assert.strictEqual(probe.state.mode, defaultMode, key);
  assert.strictEqual(JSON.stringify(probe.state.sort), defaultSort, key);
  probe.state.sort = {key, dir: defaultDirection};
  assert.strictEqual(JSON.stringify(probe.sortModels(rows)), defaultOrder, key);
}
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_tab_steps() -> None:
  """The tab strip carries no chevrons and leaves scroll fades to CSS timelines."""
  code = _app_js_vm(
    """
assert(!byId.has('nav-left') && !byId.has('nav-right'), 'no chevron buttons remain');
nav.scrollLeft = 24;
nav.scrollWidth = 400;
nav.clientWidth = 200;
sandbox.markNavEdges();
assert.strictEqual(nav.style.props['--fade-left'], undefined, 'no JS inline fade property is written');
assert.strictEqual(nav.style.props['--fade-right'], undefined, 'CSS scroll timeline owns the edge mask');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_boots_app_with_server_settings(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A stored session sets app visibility, theme, and time format."""
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200, login.text
  found = client.get("/ui/api/settings")
  assert found.status_code == 200, found.text
  payload = found.json()
  payload["file"] = {"personalization": {"theme": "dark", "time_format": "12h"}}
  monkeypatch.setenv("DAE_TEST_SETTINGS", json.dumps(payload))
  code = _app_js_vm(
    """
// The boot path needs nodes that hold dataset, closest and attributes.
const enrich = (node) => Object.assign(node, {
  dataset: node.dataset || {}, value: node.value ?? "", checked: node.checked ?? false,
  hidden: node.hidden ?? false, style: node.style || {},
  closest: node.closest || (() => null),
  querySelector: node.querySelector || (() => null),
  querySelectorAll: node.querySelectorAll || (() => []),
  setAttribute: node.setAttribute || (() => {}),
  removeAttribute: node.removeAttribute || (() => {}),
  focus: node.focus || (() => {}),
});
for (const node of byId.values()) enrich(node);
const getElement = sandbox.document.getElementById;
sandbox.document.getElementById = (id) => enrich(getElement(id));
sandbox.document.querySelector = (sel) =>
  (sel.includes("data-page") ? { hidden: true } : { firstChild: { textContent: "Models" } });
sandbox.EventSource = class { addEventListener() {} close() {} };
sandbox.setInterval = setInterval;
sandbox.clearInterval = clearInterval;
sandbox.sessionStorage = { getItem: () => "boot-session", setItem() {}, removeItem() {} };
sandbox.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const answers = {
  settings: JSON.parse(process.env.DAE_TEST_SETTINGS),
  login: { session: true }, hooks: { legend: [] },
  status: { healthy: true, sessions: 0, models: 1, version: "v1",
    catalog: { built: null, next: null, rebuilding: false }, affinity: { mode: "none" } },
  pools: [], models: [], keys: [], env: [], limits: { providers: [], lanes: [], checked: null },
  notifications: { resets: [], rebuilds: [], update: null, limits: [] },
  files: [{ path: "config/daedalus.yml", text: "", blocks: {}, error: null, main: true }],
  "provider-keys": [], "provider-defaults": {},
};
sandbox.call = async (path) => {
  if (path.startsWith("requests")) return [];
  const key = path.split("?")[0];
  if (!(key in answers)) throw new Error(`no stub for ${path}`);
  return answers[key];
};
(async () => {
  await sandbox.start();
  assert.strictEqual(el("app").hidden, false, "the app shows");
  assert.strictEqual(el("login").hidden, true, "the login form hides");
  assert.strictEqual(vm.runInContext("hourCycle", sandbox), "h12", "time_format read");
  assert.strictEqual(sandbox.document.documentElement.dataset.theme, "dark", "theme read");
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)
  client.cookies.clear()


def test_request_empty_message_changes_with_retained_history() -> None:
  """Empty-request markup follows history and live-row changes under a filter."""
  code = _app_js_vm(
    """
const probe = vm.runInContext("({state, renderRequestTable})", sandbox);
probe.state.requests = [];
probe.state.live = new Map();
probe.state.requestSearch = "fixture-no-match";
probe.renderRequestTable();
assert(el("requests").innerHTML.includes(LOCALE["request.empty.none_text"]));
probe.state.requests = [{model: "fixture/model", status: 200, at: 1}];
probe.renderRequestTable();
assert(el("requests").innerHTML.includes(LOCALE["request.empty.filter_text"]));
probe.state.requests = [];
probe.renderRequestTable();
assert(el("requests").innerHTML.includes(LOCALE["request.empty.none_text"]));
probe.state.live.set(1, {id: 1, model: "fixture-no-match"});
probe.renderRequestTable();
assert(!el("requests").innerHTML);
probe.state.live.clear();
probe.renderRequestTable();
assert(el("requests").innerHTML.includes(LOCALE["request.empty.none_text"]));
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_hides_empty_row_when_request_goes_live(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A live start event clears the empty-request markup."""
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200, login.text
  found = client.get("/ui/api/settings")
  assert found.status_code == 200, found.text
  monkeypatch.setenv("DAE_TEST_SETTINGS", json.dumps(found.json()))
  code = _app_js_vm(
    """
const sources = [];
const enrich = (node) => Object.assign(node, {
  dataset: node.dataset || {}, value: node.value ?? "", checked: node.checked ?? false,
  hidden: node.hidden ?? false, style: node.style || {}, children: node.children || [],
  closest: node.closest || (() => null),
  querySelector: node.querySelector || (() => null),
  querySelectorAll: node.querySelectorAll || (() => []),
  setAttribute: node.setAttribute || (() => {}),
  removeAttribute: node.removeAttribute || (() => {}),
  focus: node.focus || (() => {}),
});
for (const node of byId.values()) enrich(node);
const getElement = sandbox.document.getElementById;
sandbox.document.getElementById = (id) => enrich(getElement(id));
sandbox.document.querySelector = (sel) =>
  (sel.includes("data-page") ? { hidden: true } : { firstChild: { textContent: "Models" } });
sandbox.EventSource = class {
  constructor() {
    this.listeners = new Map();
    sources.push(this);
  }
  addEventListener(kind, call) {
    if (!this.listeners.has(kind)) this.listeners.set(kind, []);
    this.listeners.get(kind).push(call);
  }
  close() {}
  fire(kind, data) {
    for (const call of this.listeners.get(kind) || []) call({ data: JSON.stringify(data) });
  }
};
sandbox.setInterval = setInterval;
sandbox.clearInterval = clearInterval;
sandbox.sessionStorage = { getItem: () => "boot-session", setItem() {}, removeItem() {} };
sandbox.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const answers = {
  settings: JSON.parse(process.env.DAE_TEST_SETTINGS),
  login: { session: true }, hooks: { legend: [] },
  status: { healthy: true, sessions: 0, models: 1, version: "v1",
    catalog: { built: null, next: null, rebuilding: false }, affinity: { mode: "none" } },
  pools: [], models: [], keys: [], env: [], limits: { providers: [], lanes: [], checked: null },
  notifications: { resets: [], rebuilds: [], update: null, limits: [] },
  files: [{ path: "config/daedalus.yml", text: "", blocks: {}, error: null, main: true }],
  "provider-keys": [], "provider-defaults": {},
};
sandbox.call = async (path) => {
  if (path.startsWith("requests")) return [];
  const key = path.split("?")[0];
  if (!(key in answers)) throw new Error(`no stub for ${path}`);
  return answers[key];
};
(async () => {
  await sandbox.start();
  assert.ok(sources.length === 1, "the page opens the stream");
  assert.ok(el("requests").innerHTML.includes(LOCALE["request.empty.none_text"]), "the empty row shows 1st");
  sources[0].fire("start", {
    id: 7, path: "/v1/chat/completions", age: 0, attempt_age: 0, ttft: null,
    app: "OWUI", session: "60e8c22", key: "master", model: "daedalus/auto", effort: "none",
    pool: null, stream: true, trying: "mistral/ministral-8b-2512", via: null, attempts: [],
    tokens: null, fallbacks: "0",
  });
  assert.strictEqual(el("requests").innerHTML, "", "the live row hides the empty row");
  process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)
  client.cookies.clear()


def test_app_js_settings_groups_ship_in_defaults() -> None:
  """Static app.js settings groups exist in `settings.DEFAULTS`."""
  source = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  reads = set(re.findall(r'(?:setting|fileValue)\("([a-z-]+)"', source))
  reads |= set(re.findall(r'^  \["([a-z-]+)", "[^"]+", \[', source, re.MULTILINE))
  assert reads, "the settings reads of app.js"
  missing = sorted(reads - set(settings.DEFAULTS))
  assert not missing, f"app.js reads settings groups the server never sends: {missing}"


def test_app_js_length_limit_message() -> None:
  """A field at its length limit gets a localized message."""
  code = _app_js_vm(
    """
const field = sandbox.$('key-name');
const message = sandbox.$('key-message');
field.maxLength = 40;
field.value = 'k'.repeat(39);
sandbox.showLengthLimit(field, message);
assert.strictEqual(message.textContent, '', '39 characters fit');
field.value = 'k'.repeat(40);
sandbox.showLengthLimit(field, message);
assert.strictEqual(message.textContent, LOCALE["validation.characters_at_most_text"].replace("{count}", "40"));
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_each_screen_starts_with_named_heading() -> None:
  """The header tabs name each screen, and document title tracks the active view."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert "page-title" not in html
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  labels = app.partition("const PAGE_LABELS = {")[2].partition("};")[0]
  for page, key in (
    ("overview", "nav.overview_link"),
    ("requests", "nav.requests_link"),
    ("models", "nav.models_link"),
    ("limits", "nav.limits_link"),
    ("notifications", "nav.notifications_link"),
    ("settings", "nav.settings_link"),
  ):
    assert f'{page}: t("{key}")' in labels, f"{page} reuses the {key} label"
  assert 'document.title = t("page.title", { page: PAGE_LABELS[page] });' in app


def test_notification_warnings_open_limits_page() -> None:
  """A limit warning links to Limits filtered by its model."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  start = app.index("function renderNotifications")
  feed = app[start : app.index("\nfunction ", start + 10)]
  assert "event.model ? `#/limits?q=${encodeURIComponent(event.model)}` : null" in feed
  assert (
    'if (href) return `<a class="notification-item ${kind}" href="${esc(href)}">'
    in feed
  )


def test_rebuild_counts_carry_label() -> None:
  """A rebuild row names each count instead of printing a bare sign."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  en = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert 't("notification.diff_added_text", { count })' in app
  for kind in ("added", "moved", "removed"):
    assert f'"notification.diff_{kind}_text"' in en


def test_limit_bars_wait_for_limit_to_move() -> None:
  """The Limits table draws a bar only when a row sits below its cap."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  rows = app[app.index("function renderLimitRows") : app.index("const DIFF_KINDS")]
  assert (
    "const moving = rows.some((r) => r.limit > 0 && r.remaining < r.limit);" in rows
  )
  assert "${moving ? progressMeter(" in rows
  assert rows.index('<span class="num') < rows.index("${moving ? progressMeter(")


def test_balance_cards_read_as_numbers() -> None:
  """Every balance card carries a label and a value, and no bar."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  balances = app[
    app.index("function renderLimits") : app.index("function renderLimitRows")
  ]
  assert "progressMeter" not in balances


def test_requests_reads_model_served_effort() -> None:
  """Served by precedes Effort in header markup and both row renderers."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert html.index('data-i18n="request.column.served_by_column"') < html.index(
    'data-i18n="request.column.effort_column"'
  )
  js = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  rows = js[js.index("function renderRequests") : js.index("const MODES")]
  assert rows.index('t("request.column.served_by_column")') < rows.index(
    'cell(t("request.column.effort_column")'
  )
  live = js[js.index("function renderLive") : js.index("function tickLive")]
  assert live.index('t("request.column.served_by_column")') < live.index(
    'cell(t("request.column.effort_column")'
  )


def test_requests_table_keeps_app_and_session() -> None:
  """Request markup and CSS contain no narrow-desktop hiding class."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = html.split('class="requests"', 1)[1].split("</thead>", 1)[0]
  assert "hide-md" not in head, "no request head column hides"
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert app.count('"hide-sm hide-md num') == 0, (
    "no request cell hides on a narrow desktop"
  )
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".hide-md" not in css, "the narrow desktop band keeps every request column"


def test_requests_stream_column_survives_narrow_desktops() -> None:
  """Stream-column markup uses only the phone hiding class."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = html.split('data-i18n-title="request.column.stream.hint"', 1)[0].rsplit(
    "<th", 1
  )[1]
  assert 'class="hide-sm"' in head, head
  js = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    js.count('cell(t("request.column.stream_column"), streamCell(r), "hide-sm num")')
    == 1
  )
  assert "hide-md" not in js, "no width band drops the stream column"


def test_narrow_desktop_header_keeps_one_row() -> None:
  """Narrow-header CSS hides status without hiding the Overview card or changing its padding."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  base, rest = css.split("@media (max-width: 68em) {", 1)
  narrow = rest.split("\n}", 1)[0]
  assert "#status { display: none; }" in narrow, "a narrow header drops the state"
  assert ".status-card { display: none" not in css, "the card shows at every width"
  assert ".status-card { padding" not in css, (
    "the card keeps the card padding at every width"
  )
  assert "button.line {" in base, "the catalog line is a button at every width"
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "catalogChip" not in app, "the header keeps no catalog chip"
  brand = base.split(".brand {", 1)[1].split("}", 1)[0]
  assert "padding: 6px 0;" in brand, "the brand keeps vertical room"
  bar = base.split(".tab-bar {", 1)[1].split("}", 1)[0]
  assert "align-items: stretch;" in bar, "the tab underline reaches the header line"


def test_app_js_type_chips_drop_redundant_media_flag() -> None:
  """Type chip markup omits media flags already implied by the mode."""
  code = _app_js_vm(
    """
const speech = sandbox.typeChips({ mode: 'audio_speech', flags: ['audio_output'] });
assert(!speech.includes('title="' + LOCALE["model.flag.audio_output_label"] + '"'), speech);
assert(speech.includes(LOCALE["model.mode.audio_speech_label"]), speech);
const transcription = sandbox.typeChips({ mode: 'audio_transcription', flags: ['audio_input'] });
assert(!transcription.includes('title="' + LOCALE["model.flag.audio_input_label"] + '"'), transcription);
assert(transcription.includes(LOCALE["model.mode.audio_transcription_label"]), transcription);
const chat = sandbox.typeChips({ mode: 'chat', flags: ['vision', 'audio_input', 'audio_output'] });
assert(chat.includes('title="' + LOCALE["model.flag.vision_label"] + '"')
  && chat.includes('title="' + LOCALE["model.flag.audio_input_label"] + '"'), chat);
assert(chat.includes('title="' + LOCALE["model.flag.audio_output_label"] + '"'), chat);
const mixed = sandbox.typeChips({ mode: 'audio_speech', flags: ['vision', 'audio_output'] });
assert(mixed.includes('title="' + LOCALE["model.flag.vision_label"] + '"')
  && !mixed.includes('title="' + LOCALE["model.flag.audio_output_label"] + '"'), mixed);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_status_hosts() -> None:
  """Status markup puts health, sessions, and catalog details in the Overview card."""
  code = _app_js_vm(
    """
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert.strictEqual(hosts[0].innerHTML, hosts[1].innerHTML);
assert(!hosts[0].innerHTML.includes(LOCALE["state.healthy_text"]), 'the header drops the health');
assert(!hosts[0].innerHTML.includes('chip rebuild'), 'the header drops the catalog chip');
assert(byId.get('card-health').innerHTML.includes(LOCALE["state.healthy_text"]), 'the card shows the health');
const card = byId.get('card-rows').innerHTML;
assert(card.includes(LOCALE["common.sessions_label"]) && card.includes('>2<'), 'the card shows the sessions');
assert(card.includes(LOCALE["catalog.label"]), 'the card shows the catalog');
assert(card.includes('class="line rebuild"'), 'the catalog line starts a rebuild');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_model_rows_keep_type_on_phone() -> None:
  """Model-row markup retains mode and capability chips for phone layouts."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + String.fromCharCode(10) + 'globalThis.__probe = { state, renderModels, $ };'
const byId = new Map();
const el = (id) => {
  if (!byId.has(id)) {
    const classes = new Set();
    byId.set(id, {
      innerHTML: '', textContent: '', value: '', addEventListener: () => {},
      classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name),
      toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), contains: (n) => classes.has(n) },
    });
  }
  return byId.get(id);
};
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: {
    hidden: false,
    documentElement: { dataset: {} },
    getElementById: (id) => el(id),
    querySelector: () => ({ firstChild: { textContent: 'Models' } }),
    querySelectorAll: () => [],
    addEventListener: () => {},
  },
  navigator: {},
  location: { hash: '' },
  window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }),
  $: (id) => el(id),
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const probe = sandbox.__probe;
probe.state.models = [{
  id: 'p/m', mode: 'chat', flags: ['vision'], tier: 'TIER-A', order: 1,
  max_input_tokens: 1000, supports_function_calling: true, supports_reasoning: false,
  weight: 1, cooldown: 0, client_cooldowns: {},
}];
probe.renderModels();
const html = el('models').innerHTML;
assert(html.includes('</span><span class="types phone-types">'), 'the chips sit outside the clipped name');
assert(html.includes('>' + LOCALE["model.mode.chat_label"] + '<'), 'the mode chip shows');
assert(html.includes('title="' + LOCALE["model.flag.vision_label"] + '"'), 'the media chip shows');
assert(html.includes('<div class="types">'), 'the Type column stays for a desktop');
assert(html.includes('<span class="cell-value">'), 'the name cell keeps its value span');
assert(html.includes('class="mark slug"') && html.includes('>p<'), 'a name with no file shows its text');
assert(html.includes('<span class="mark-sep" aria-hidden="true">/</span>'), 'the separator shows');
assert(html.includes('<span class="model-part">m</span>'), 'the model part shows');
assert(html.includes('title="p/m"'), 'the title keeps the full id');
sandbox.matchMedia = () => ({ matches: true });
probe.renderModels();
const phone = el('models').innerHTML;
assert(phone.includes('class="mark slug"'), 'a phone keeps the provider text');
assert(phone.includes('<span class="model-part">m</span>'), 'a phone keeps the model part');
    const chipRow = phone.split('class="phone-chips"')[1].split('</td>')[0];
    assert(chipRow.includes('title="' + LOCALE["model.column.context_column"] + '"'),
      'the context rides as a phone chip');
    assert(!chipRow.includes('1.00'), 'the weight chip leaves the chip row');
    assert(phone.includes('1.00'), 'the weight row carries the weight');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_client_key_modal() -> None:
  """The client-key modal sets name and value fields, placeholders, and initial values."""
  code = _app_js_vm(
    """
const modal = el('modal');
modal.returnValue = 'ok';
modal.showModal = () => modal.close('ok');
modal.close = (value) => {
  modal.returnValue = value || modal.returnValue;
  const done = sandbox.__settle;
  sandbox.__settle = null;
  done?.(modal.returnValue === 'ok');
};
const askPair = sandbox.__probe.askPair;
const shown = askPair('Client key', 'The key and its value go to the file.', 'name', 'value', 'k', 'v');
assert.strictEqual(typeof shown.then, 'function', 'askPair resolves');
assert.strictEqual(el('modal-title').textContent, 'Client key', 'the title');
assert.strictEqual(el('modal-field').hidden, false, 'the name field shows');
assert.strictEqual(el('modal-field2').hidden, false, 'the value field shows');
assert.strictEqual(el('modal-input').placeholder, 'name', 'the name placeholder');
assert.strictEqual(el('modal-input2').placeholder, 'value', 'the value placeholder');
assert.strictEqual(el('modal-input').value, 'k', 'the name keeps its value');
assert.strictEqual(el('modal-input2').value, 'v', 'the value keeps its value');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_discovery_match_add_does_not_use_client_key_dialog() -> None:
  """Discovery-match code uses its own title and property placeholder keys."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  catalog = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert 't("dialog.discovery_match.title")' in app
  assert 't("provider.discovery_property_placeholder")' in app
  assert '"dialog.discovery_match.title":' in catalog
  assert '"provider.discovery_property_placeholder":' in catalog


def test_provider_values_add_opens_two_field_modal() -> None:
  """Provider-value code includes a modal select, an add handler, and a title key."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  catalog = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert '<select id="modal-choice"' in page
  assert 'if (add.dataset.kind === "column") return addProviderValue(add);' in app
  assert 't("dialog.provider_value.title")' in app
  assert '"dialog.provider_value.title":' in catalog


def test_app_js_model_marks() -> None:
  """Model-name markup separates provider, developer, and model parts."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + String.fromCharCode(10) + 'globalThis.__probe = { modelName, MARK_FILES, state };';
const byId = new Map();
const el = (id) => {
  if (!byId.has(id)) byId.set(id, { innerHTML: '', textContent: '', value: '', addEventListener: () => {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false } });
  return byId.get(id);
};
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: el,
    querySelector: () => ({ firstChild: { textContent: 'Models' } }), querySelectorAll: () => [],
      addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, history: { replaceState: () => {} },
    window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: el,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const modelName = sandbox.__probe.modelName;
const marks = (html) => (html.match(/class="mark[ "]/g) || []).length;
const seps = (html) => (html.match(/class="mark-sep"/g) || []).length;
const full = modelName('openrouter/dots-studio/dots-3-note-preview:free');
assert.strictEqual(marks(full), 2, 'provider and developer');
assert.strictEqual(seps(full), 2, 'a slash between each part');
assert(full.includes('<span class="model-part">dots-3-note-preview:free</span>'), 'the model part');
assert.strictEqual(marks(modelName('cloudflare/@cf/cloudflare/clef')), 1, '1 icon when the provider is the developer');
assert.strictEqual(seps(modelName('cloudflare/@cf/cloudflare/clef')), 1, '1 slash for 1 head');
assert(modelName('cloudflare/@cf/cloudflare/clef').includes('<span class="model-part">clef</span>'),
  'the model part of a scope');
assert(modelName('cloudflare/@cf/openai/gpt-oss-120b').includes('<span class="model-part">gpt-oss-120b</span>'),
  'a scope keeps the developer');
assert.strictEqual(modelName('bare'), 'bare', 'a name with no slash stays text');
const bare = modelName('p/m');
assert(bare.includes('class="mark slug"') && bare.includes('>p<'),
  'the text of a name with no file stands in for a mark');
assert.strictEqual(seps(bare), 1, 'the slash before the slug');
assert(bare.includes('<span class="model-part">m</span>'), 'the slug of a bare name');
assert(modelName('cloudflare/@cf/meta/llama-3.1-8b-instruct')
  .includes('<img class="mark" src="ui/icons/cloudflare.svg"'), 'a shipped mark file');
assert(modelName('cloudflare/@cf/inclusionai/ling-3.0-flash').includes('class="mark slug">inclusionai<'),
  'a name with no file shows its text');
assert(sandbox.__probe.MARK_FILES.has('cloudflare') && sandbox.__probe.MARK_FILES.has('z-ai'), 'the shipped mark set');
sandbox.__probe.state.models = [{ id: 'openrouter/baseten/mammoth-1:free', name: 'Mammoth 1' }];
const named = modelName('openrouter/baseten/mammoth-1:free');
assert(named.includes('ui/icons/openrouter.svg') && named.includes('ui/icons/baseten.svg'),
  'real-name rendering preserves provider and developer marks');
assert(named.includes('<span class="model-part">Mammoth 1</span>'),
  'real-name rendering replaces the raw model slug with the human-readable name');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_cancelled_status() -> None:
  """Cancelled requests produce 499 markup and localized status text."""
  code = _app_js_vm(
    """
const cell = sandbox.statusCell;
const text = sandbox.statusText;
assert.strictEqual(cell({ cancelled: true }),
  '<span title="' + LOCALE["request.status.cancelled_text"] + '">499</span>', 'the code of a closed request');
assert.strictEqual(cell({ cancelled: false, status: 200 }),
  '<span title="' + LOCALE["request.status.ok_text"] + '">200</span>', 'a normal code');
assert.strictEqual(cell({ cancelled: false, status: 429 }),
  '<span title="' + LOCALE["request.status.rate_limited_text"] + '">429</span>', 'a limited request');
assert.strictEqual(cell({ cancelled: false, status: 'err' }),
  '<span title="' + LOCALE["request.status.failed_text"] + '">err</span>', 'a failed request');
assert.strictEqual(cell({ cancelled: false, status: 302 }), '302', 'a code without a note stays plain');
assert.strictEqual(text({ cancelled: true }), LOCALE["request.state.cancelled_text"], 'the word in the chain text');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_mark_files_match_icon_folder() -> None:
  """The app.js mark set matches shipped SVG filenames."""
  source = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  block = source.partition("const MARK_FILES = new Set([")[2].partition("]);")[0]
  assert block, "the mark set is a literal"
  names = set(re.findall(r'"([a-z0-9-]+)"', block))
  files = {path.stem for path in Path("daedalus/dashboard/ui/icons").glob("*.svg")}
  assert files == names, "the set and the folder hold the same names"


def test_app_js_state_chip() -> None:
  """Status markup flags loading and unknown states, then clears them after a response."""
  code = _app_js_vm(
    """
sandbox.setStateKnown(null);
assert(hosts[0].innerHTML.includes(LOCALE["state.loading_text"]), hosts[0].innerHTML);
assert(!hosts[0].innerHTML.includes('chip bad'), 'no alarm before the first answer');
sandbox.setStateKnown(false);
assert(hosts[0].innerHTML.includes(LOCALE["state.unknown_text"]), hosts[0].innerHTML);
assert(hosts[0].innerHTML.includes('chip bad'), hosts[0].innerHTML);
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert.strictEqual(hosts[0].innerHTML, '', 'the good answer clears the header');
assert(!hosts[0].innerHTML.includes(LOCALE["state.unknown_text"]), 'the good answer clears the note');
assert(!hosts[0].innerHTML.includes(LOCALE["state.loading_text"]), 'the loading note goes away');
assert(byId.get('card-health').innerHTML.includes(LOCALE["state.healthy_text"]), 'the card shows the health');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_top_model() -> None:
  """The top model has the most served requests, with catalog order breaking ties."""
  code = _app_js_vm(
    """
const members = [{ id: 'p/a' }, { id: 'p/b' }];
assert.strictEqual(sandbox.topModel(members, { 'p/b': 3, 'p/a': 1 }).id, 'p/b');
assert.strictEqual(sandbox.topModel(members, { 'p/a': 2, 'p/b': 2 }).id, 'p/a');
assert.strictEqual(sandbox.topModel(members, {}).id, 'p/a');
assert.strictEqual(sandbox.topModel([], {}), undefined);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_request_cards() -> None:
  """Request renderers include column labels and table roles in finished and live rows."""
  page = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/index.html"
  ).read_text(encoding="utf-8")
  header = re.search(
    r'<table class="requests" aria-label="nav.requests_link"[^>]*>.*?'
    r"<thead[^>]*>(.*?)</thead>",
    page,
    re.DOTALL,
  )
  assert header, "the localized Requests table header"
  keys = re.findall(r'data-i18n="([^"]+)"', header.group(1))
  assert keys == [
    "request.column.time_column",
    "request.column.app_column",
    "request.column.session_column",
    "request.column.model_column",
    "request.column.served_by_column",
    "request.column.effort_column",
    "request.column.status_column",
    "request.column.input_column",
    "request.column.output_column",
    "request.column.ttft_column",
    "request.column.stream_column",
  ]
  row = {
    "at": 100,
    "app": "OWUI",
    "session": "s1",
    "model": "p/big",
    "effort": "high",
    "pool": "daedalus/deinos",
    "via": "p/big",
    "status": 200,
    "tokens": {"input": 12, "output": 34},
    "ttft": "0.500s",
    "stream": True,
    "seconds": 3,
    "fallbacks": 1,
    "routed": "deinos",
    "retry": "2",
    "loop": "3",
    "transition": {"reason": "err"},
    "attempts": [
      {"model": "p/first", "result": "error", "seconds": 0.4, "error": "rate limited"},
      {"model": "p/big", "result": "answered", "seconds": 1.0, "effort": "xhigh"},
    ],
  }
  code = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') +
  "\\nglobalThis.__probe = {{ renderRequests, renderLive," +
  " poolTier, state, modelName, poolOf, routingCodes, openChain }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false,
    showModal: () => {{}},
    children: [], listeners: {{}}, contains: () => false, addEventListener(type,
      handler) {{ this.listeners[type] = handler; }}, classList: {{ add: () => {{}}, remove: () => {{}},
      toggle: () => {{}} }} }});
  return nodes.get(id);
}};
const sandbox = {{
  esc: (text) => String(text ??
    '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }})[c]),
  seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{ hidden: false, documentElement: {{ dataset: {{}} }}, getElementById: node,
    querySelector: () => ({{ firstChild: {{ textContent: 'M' }} }}), querySelectorAll: () => [],
      addEventListener: () => {{}} }},
  navigator: {{}}, location: {{ hash: '' }}, window: {{ addEventListener: () => {{}},
    matchMedia: () => ({{ matches: false }}) }},
  getSelection: () => ({{ isCollapsed: true }}), console: {{ error: () => {{}} }}, $: node,
}};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const probe = sandbox.__probe;
// The pool cards give the tier letter of the `frX` code. The built-in names cover the first paint.
probe.state.pools = [];
assert(probe.poolTier('deinos') === 'B', 'a built-in pool name gives its tier letter');
probe.state.pools = [{{ shown: "daedalus/renamed", members: [{{ tier: "TIER-C" }}] }}];
assert(probe.poolTier('renamed') === 'C', 'a renamed pool takes the tier of its cards');
assert(probe.poolTier('opaque') === 'O', 'an unknown pool keeps a one-letter code');
probe.state.pools = [{{ shown: "daedalus/deinos", members: [{{ tier: "TIER-A" }}] }}];
probe.renderRequests([{json.dumps(row)}]);
const html = node('requests').innerHTML;
const cells = [...html.matchAll(/<td[^>]*>/g)].map((m) => m[0]);
assert.strictEqual(cells.length, 12, 'every column of the row is a cell: ' + cells.length);
assert(cells.every((td) => td.includes('role="cell"')), 'each request cell keeps its table role');
assert.strictEqual((html.match(/class="cell-value"/g) || []).length, 11, 'every value cell keeps its value span');
const mobileLabels = (markup) => [...markup.matchAll(new
  RegExp('<span class="mobile-label" aria-hidden="true">([^<]*)</span>', 'g'))].map((m) => m[1]);
const columnNames = {json.dumps(keys)}.map((key) => LOCALE[key]);
const fallbackCount = (count, key) => LOCALE[key].replace("{{count}}", String(count));
const chainTitle = (count, key) => LOCALE["request.chain.title_with_count_text"]
  .replace("{{count}}", fallbackCount(count, key));
assert.deepStrictEqual(mobileLabels(html), columnNames,
  'the cells show their column names in table order');
assert(html.includes('hide-sm num mono'), 'the session id reads in the mono font');
assert(html.includes('class="request has-chain"'), 'requests with fallbacks expose their chain');
assert(!html.includes('chain-open'), 'the row carries no chain pill');
const ROW_AT = {json.dumps(row["at"])};
assert(html.includes('data-chain-at="' + ROW_AT + '"'), 'the chained row is the control');
assert(html.includes('tabindex="0"'), 'the chained row takes focus');
const CHAIN_ROW = {json.dumps(row)};
probe.state.requests = [CHAIN_ROW];
probe.openChain(String(CHAIN_ROW.at));
const chainHtml = node('chain-modal-body').innerHTML;
assert.strictEqual(node('modal-title').textContent,
  chainTitle(1, "request.chain.fallback.one_text"),
  'the dialog head carries the count');
assert(!chainHtml.includes(chainTitle(1, "request.chain.fallback.one_text")),
  'the dialog body omits the duplicate title');
assert(html.includes(LOCALE["request.chain.show_title"]), 'the chained row explains itself on hover');
// The pool of the auto model rides in the slug, and the daedalus head carries its own mark.
const autoCell = probe.modelName('daedalus/auto', probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}));
assert.strictEqual(probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}), 'moros', 'the auto pool joins the slug');
assert(autoCell.includes('<span class="model-part">auto</span>') &&
  autoCell.includes('<span class="model-part">moros</span>'), 'the slug reads daedalus/auto/moros');
assert(autoCell.includes('ui/icons/daedalus.svg'), 'the daedalus head carries its own mark');
assert.strictEqual(probe.poolOf({{ model: 'p/big', pool: 'daedalus/deinos' }}), '',
  'a direct model keeps its own slug');
assert.strictEqual(probe.routingCodes({{ routed: 'deinos' }}).includes('fr'), true, 'the fallback tier code stays');
assert(!html.includes('class="caret'), 'no chevron sits in the time cell');
for (const value of ['s1', 'high <span class="from">xhi</span>', 'frA', 'tl3'])
  assert(html.includes(value), 'the table keeps ' + value);
for (const value of ['<span class="mark slug">p</span>', '<span class="model-part">first</span>',
  '<pre>rate limited</pre>', '<span class="from">effort xhi</span>'])
  assert(chainHtml.includes(value), 'the dialog keeps ' + value);
assert(!html.includes('rt2') && !html.includes('tool loop'), 'the frX and tlN codes replace the long forms');
const openSelectors = [];
const openTap = {{ target: {{ closest: (selector) => {{ openSelectors.push(selector);
  return selector === '[data-chain-at]'
    ? {{ dataset: {{ chainAt: String(CHAIN_ROW.at) }} }} : null; }} }} }};
node('requests').listeners.click(openTap);
assert(openSelectors.includes('[data-chain-at]'), 'the table listens for the chain button');
assert(node('chain-modal-body').innerHTML.includes('rate limited'), 'a chain button opens the dialog');
probe.state.live.set(2, {{ id: 2, since: 100000, attemptSince: 100000, first: null, stream: false, session: 's2',
  app: 'OWUI', model: 'daedalus/auto', effort: 'medium', pool: 'moros', via: 'p/live', fallbacks: 0 }});
probe.renderLive();
const liveHtml = node('live').innerHTML;
const liveCells = [...liveHtml.matchAll(/<td[^>]*>/g)].map((m) => m[0]);
assert.strictEqual(liveCells.length, 12, 'every live column is a cell');
assert(liveCells.every((td) => td.includes('role="cell"')), 'each live request cell keeps its table role');
assert.strictEqual((liveHtml.match(/class="cell-value"/g) || []).length, 11,
  'every live value cell keeps its value span');
assert.deepStrictEqual(mobileLabels(liveHtml), columnNames,
  'live request cells show their column names');
assert(liveHtml.includes('<span class="mobile-fallback-count">'
  + fallbackCount(0, "request.chain.fallback.other_text") + '</span>'),
  'the live row reads the fallback count alone');
for (const value of ['s2', 'medium', '<span class="model-part">moros</span>']) assert(liveHtml.includes(value),
  'live details keep ' + value);
assert(!liveHtml.includes('data-chain-at'), 'live rows carry no chain button');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_live_served_by_shows_trying_marks() -> None:
  """Live Served by markup uses provider and developer marks for the attempted model."""
  run_app_js(
    "state, renderLive",
    """
node('live').children = [];
probe.state.live.set(7, {
  id: 7, since: 100000, attemptSince: 100000, first: null, stream: true, session: 's7',
  app: 'OWUI', model: 'daedalus/auto', effort: null, pool: 'moros', via: null,
  trying: 'cloudflare/@cf/zai-org/glm-4.7-flash', fallbacks: 0,
});
probe.renderLive();
const html = node('live').innerHTML;
const servedBy = html.slice(html.indexOf(LOCALE["request.column.served_by_column"]));
assert(servedBy.includes('trying'), 'the cell keeps the trying word');
assert(servedBy.includes('ui/icons/cloudflare.svg'), 'the trying model shows its provider mark');
assert(servedBy.includes('ui/icons/zai-org.svg'), 'the trying model shows its developer mark');
assert(servedBy.includes('<span class="model-part">glm-4.7-flash</span>'), 'the model part reads short');
assert(!servedBy.includes('trying cloudflare/@cf/zai-org/glm-4.7-flash'),
  'the raw slug never stands in place of the marks');
""",
  )


def test_app_js_live_clocks_wait_for_first_token() -> None:
  """Stream-time fields stay empty until the first token, with or without streaming."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { tickLive, state };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', children: [], listeners: {}, classList: { add: () => {},
    remove: () => {}, toggle: () => {} }, addEventListener(type, handler) { this.listeners[type] = handler; } });
  return nodes.get(id);
};
const ago = { textContent: '' };
const ttft = { textContent: '' };
const stream = { textContent: '' };
const live = {
  innerHTML: '', children: [{ dataset: { live: '2' }, querySelector: (selector) =>
    selector.includes('live-ago') ? ago : selector.includes('ttft') ? ttft : stream }],
};
const liveNode = (id) => (id === 'live' ? live : node(id));
const sandbox = {
  esc: (text) => String(text ?? ''),
  seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: liveNode,
    querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [],
      addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: liveNode,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const probe = sandbox.__probe;
const row = (fields) => ({ id: 2, since: Date.now(), attemptSince: Date.now(), first: null, stream: false, ...fields });
probe.state.live.set(2, row({}));
probe.tickLive();
assert.strictEqual(ago.textContent, 'just now', 'the live age updates on each tick');
assert.strictEqual(stream.textContent, '', 'a plain request shows no stream time before the first token');
assert(ttft.textContent.endsWith('s'), 'the TTFT clock counts while the first token is pending: ' + ttft.textContent);
probe.state.live.set(2, row({ stream: true }));
probe.tickLive();
assert.strictEqual(stream.textContent, '', 'a streaming request waits the same way');
probe.state.live.set(2, row({ first: Date.now() }));
probe.tickLive();
assert(stream.textContent.endsWith('s'),
  'a plain request counts its whole time after the first token: ' + stream.textContent);
probe.state.live.set(2, row({ first: Date.now(), stream: true }));
probe.tickLive();
assert(stream.textContent.endsWith('s'),
  'a streaming request counts the time after the first token: ' + stream.textContent);
"""
  subprocess.run(["node", "-e", code], check=True)


def test_header_lists_six_tabs_with_settings_last(client: TestClient) -> None:
  """Header markup and page routes list six tabs, and Providers lives inside Settings."""
  page = client.get("/")
  nav = page.text[
    page.text.index('<nav class="tabs scroll-fade" id="nav">') : page.text.index(
      "</nav>"
    )
  ]
  assert re.findall(r'data-page="([a-z]+)"', nav) == [
    "overview",
    "requests",
    "models",
    "limits",
    "notifications",
    "settings",
  ]
  app = client.get("/ui/app.js").text
  assert (
    'const PAGES = ["overview", "requests", "models", "limits",'
    ' "notifications", "settings"];'
  ) in app
  assert 'data-page="providers"' not in page.text


def test_phone_limits_row_keeps_every_field() -> None:
  """A phone limit row draws the reset and last-seen values that the desk columns hide."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  meta = app.partition("const limitMeta = (r) =>")[2].partition("\n\n")[0]
  assert '"limits.meta.reset_text"' in meta, "the reset time reaches the phone line"
  assert '"limits.meta.reset_past_text"' in meta, (
    "a past reset uses the past-tense phone line"
  )
  assert '"limits.meta.seen_text"' in meta, "the last check reaches the phone line"
  assert 'class="limit-meta"' in app, "each row carries the phone line"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  phone = css.split("@media (max-width: 30em) {", 1)[1]
  assert ".limit-meta { display: none; }" in css, "the desk row hides the phone line"
  assert "#limit-rows .limit-meta { display: block;" in phone, (
    "the phone row shows the line"
  )
  assert "#limit-rows td.hide-sm { display: none; }" in phone, (
    "the phone keeps the desk columns out of the row"
  )


def test_feed_and_log_name_own_state() -> None:
  """The notification feed opens on its first event, with no count strip over it."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'class="feed-meta"' not in app, "no count strip heads the feed"
  assert "notification.feed." not in app, "no count or oldest-event label survives"
  assert (
    'notices.length ? notices.map((notice) => notice.html).join("") '
    ': none(t("notification.empty_text"))'
  ) in app, "the feed draws its events, and its empty note, directly"
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'id="pool-note"' not in html and 'class="strip-note"' not in html, (
    "the pool strip omits the top hint note because the bar tooltip carries it"
  )


def test_rebuild_notification_and_catalog_action_have_distinct_targets() -> None:
  """A rebuild-notification click opens only its diff modal, not the catalog action."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  status = app[app.index("function renderStatusCard") : app.index("let stateKnown")]
  assert "data-catalog-rebuild" in status, "the catalog line owns the rebuild action"

  notifications = app[
    app.index("function renderNotifications") : app.index(
      "// The maintenance card lives"
    )
  ]
  assert '{ "data-rebuild": index }' in notifications, (
    "a notification owns the diff action"
  )
  assert "data-catalog-rebuild" not in notifications, (
    "a diff notification never carries the catalog action"
  )

  click = app[
    app.index('document.addEventListener("click"') : app.index(
      "function showLengthLimit"
    )
  ]
  assert 'event.target.closest("[data-catalog-rebuild]")' in click
  assert 'event.target.closest(".rebuild")' not in click, (
    "the document handler does not treat the notification style as an action"
  )

  run_app_js(
    "state",
    """
let shown = 0;
node('modal').showModal = () => { shown += 1; };
probe.state.notifications = {
  rebuilds: [{
    at: 1791600000, reason: 'scheduled', models: 2,
    added: ['p/new'], removed: [], changed: [], failed: [],
  }],
};
const row = { dataset: { rebuild: '0' } };
const event = {
  target: {
    closest: (selector) => selector === '[data-rebuild]' || selector === '.rebuild' ? row : null,
  },
};
node('notification-feed').handlers.click[0](event);
for (const handler of documentHandlers.click || []) handler(event);
assert.strictEqual(shown, 1, 'the bubbling click opens only the diff modal');
assert(node('modal-title').textContent.startsWith('scheduled ·'),
  'the rebuild confirmation never replaces the diff title');
""",
  )


def test_rebuild_row_opens_diff_modal() -> None:
  """Rebuild-row code uses shared diff descriptors and opens a modal instead of an inline row."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "function openDiffModal(event) {" in app
  assert "openDiffModal(state.notifications.rebuilds[index])" in app
  assert "const lines = DIFF_KINDS.flatMap(([kind, mark, key]) =>" in app
  assert "event[key].map((text) => [kind, mark, text])" in app
  assert 'mountModalView("model")' in app
  assert "rebuild-detail" not in app, "the diff rides in the modal, not an inline row"


def test_app_js_diff_rows_use_model_marks() -> None:
  """Catalog-diff markup uses provider and developer marks with field chips."""
  run_app_js(
    "diffModel, icon, openDiffModal",
    """
const added = probe.diffModel('openrouter/dots-studio/dots-3-note-preview:free');
assert(added.includes('ui/icons/openrouter.svg'), 'the provider mark shows');
assert(added.includes('ui/icons/dots-studio.svg'), 'the developer mark shows');
assert(added.includes('<span class="model-part">dots-3-note-preview:free</span>'),
  'a colon in the model slug stays in the model part');
assert(!added.includes('openrouter/dots-studio/'), 'the full provider path leaves the row');
const entry = 'z-ai/glm-4.7-flash: supports_function_calling 0->1, supports_reasoning None->1, '
  + 'supported_efforts ["low", "high", "max"]->["none", "max"], '
  + 'max_input_tokens 4096->8192';
const changed = probe.diffModel(entry);
assert(changed.includes('ui/icons/z-ai.svg'), 'a changed row keeps the provider mark');
assert(changed.includes(`class="chip flag">${probe.icon('wrench')} ${LOCALE["model.column.tools_column"]}</span>`),
  'the tools chip keeps its normal color');
assert(changed.includes(`class="chip flag">${probe.icon('brain')} ${LOCALE["model.column.reasoning_column"]}</span>`),
  'the reasoning chip keeps its normal color');
assert(changed.includes('class="chip flag effort e-low old">' + LOCALE["model.effort.low_label"] + '</span>'),
  'a removed effort reads struck');
assert(changed.includes('class="chip flag effort e-none">' + LOCALE["model.effort.none_label"] + '</span>'),
  'an added effort keeps its normal effort color');
assert(changed.includes('class="chip flag old">' + LOCALE["model.column.context_column"] + ' 4096</span>'),
  'the old scalar chip reads struck');
assert(changed.includes('class="chip flag">' + LOCALE["model.column.context_column"] + ' 8192</span>'),
  'the new scalar chip keeps its normal color');
assert(!changed.includes('>+') && !changed.includes('>-'), 'field chips carry no diff signs');
assert(!changed.includes('supported_efforts'), 'the raw field names leave the row');
assert(!changed.includes('->'), 'raw arrows leave the changed row');
node('modal').showModal = () => {};
probe.openDiffModal({
  reason: 'scheduled', at: 1791600000, models: 3,
  added: ['openrouter/dots-studio/dots-3-note-preview:free'],
  removed: ['groq/llama-3.1-8b-instant'], changed: [entry], failed: [],
});
const modal = node('model-modal-body').innerHTML;
assert(modal.includes('class="diff-id added"'), 'an added model identity uses the + color');
assert(modal.includes('class="diff-id removed"'), 'a removed model identity uses the - color');
assert(modal.includes('class="diff-id moved"'), 'a changed model identity uses the ~ color');
assert(modal.includes('class="mark tint"'), 'the provider icon can inherit the identity color');
assert(modal.includes('--mark:url(icons/openrouter.svg)'),
  'the tinted provider mark resolves beside the dashboard stylesheet');
assert(modal.includes('<span class="mark-sep" aria-hidden="true">/</span>'),
  'the slash separator stays beside the provider mark');
""",
  )
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  rule = re.search(r"\.diff-line > \.diff-model \{([^}]*)\}", css)
  assert rule and "white-space: normal" in rule.group(1), "a long row wraps"
  assert "overflow-wrap: anywhere" in rule.group(1), "a long slug can wrap"
  tinted = re.search(r"\.mark\.tint \{([^}]*)\}", css)
  assert tinted and "background: currentColor" in tinted.group(1), (
    "a diff provider mark takes the identity color"
  )
  assert "mask: var(--mark)" in tinted.group(1), (
    "the colored mark keeps its provider icon"
  )
  chips = re.search(r"\.diff-chips \{([^}]*)\}", css)
  assert chips and "display: inline-flex" in chips.group(1), (
    "the change chips share a row"
  )
  assert "width: max-content" in chips.group(1), (
    "the chip group moves as 1 unit when it does not fit"
  )
  assert "max-width: 100%" in chips.group(1), "the chip group stays inside the modal"
  assert "flex-wrap: wrap" in chips.group(1), "the chips wrap after moving as a group"


def test_settings_rebuild_redraws_keys_rows() -> None:
  """The keys table survives a settings rebuild, such as the YAML round trip."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  body = app.partition("function renderSettings() {")[2].partition("\n}\n")[0]
  assert "renderKeys(state.keys ?? []);" in body, (
    "a rebuild redraws the stored keys into the fresh table"
  )


def test_select_section_scopes_queries_to_own_host() -> None:
  """A host selection leaves the nested providers rail and cards alone."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  body = app.partition("function selectSection(host, key,")[2].partition("\n}\n")[0]
  assert ":scope > .rail > .sections button" in body, (
    "the rail buttons of this host alone"
  )
  assert ":scope > .section-pane > [data-section]" in body, (
    "the pane sections of this host alone"
  )


def test_provider_tiles_carry_vendored_marks() -> None:
  """Provider rows in section rail draw vendored mark in tile."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  block = app.partition("const chipFor = (host, key) => {")[2].partition("};")[0]
  assert "MARK_FILES.has(key)" in block and "mark(key, true)" in block, (
    "a provider tile draws its vendored mark"
  )
  rail = app.partition("function sectionList(id, items,")[2].partition("\n}\n")[0]
  assert "chipFor(id, key)" in rail, "the section rail draws the provider tile"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  tile = css.partition(".tile .mark {")[2].partition("}")[0]
  assert "width: 16px" in tile, "the tile sizes the mark like its glyph"


def test_page_scroll_does_not_wait_on_has_support() -> None:
  """The page layout of main rides on a dataset page, so one scrollbar survives old browsers."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert ":has(> section" not in css, (
    "a browser without :has support would scroll main beside the pane"
  )
  for page in ("requests", "providers", "settings"):
    assert f'main[data-page="{page}"]' in css, f"the {page} layout rides on the dataset"
  assert "if (main?.dataset) main.dataset.page = page;" in app, (
    "the page switch writes the dataset the CSS reads"
  )


def test_model_dialog_counts_use_rates_on_open() -> None:
  """The model dialog derives use rates from the request rows when it opens."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  locale = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  for key in (
    "model.detail.use_title",
    "model.detail.success_label",
    "model.detail.races_won_label",
    "model.detail.fallbacks_label",
    "model.detail.streamed_label",
    "model.detail.first_token_label",
    "model.detail.limit_left_label",
  ):
    assert f'"{key}"' in locale, key
  assert "function useStats(m) {" in app, "one helper derives the rates"
  assert "useStats(m)" in app, "the dialog draws them on open"
  run_app_js(
    "state, useStats, limitUnit",
    """
probe.state.requests = [
  {model: 'p/m', status: 200, stream: true, ttft: '0.4', fallbacks: 0,
   attempts: [{model: 'p/m', race: 'won', result: 'answered'}]},
  {model: 'p/m', status: 500, fallbacks: 1,
   attempts: [{model: 'p/m', result: 'error'}, {model: 'q/n', race: 'won', result: 'answered'}]},
  {model: 'q/n', status: 200, fallbacks: 0,
   attempts: [{model: 'p/m', result: 'error'}, {model: 'q/n', race: 'won', result: 'answered'}]},
];
const use = probe.useStats({id: 'p/m'}).map(([value, label]) => value + ' ' + label).join(', ');
assert(use.includes('50%'), use);
assert(use.includes('33%'), use);
assert(use.includes('0.400s'), use);
assert(!use.includes('of 60'), 'a model without known limits adds no limit row');
const row = {kind: 'requests', span: 'minute', limit: 60, remaining: 12};
probe.state.limits = {lanes: [{model: 'p/m', rows: [row]}]};
const withLimits = probe.useStats({id: 'p/m'}).map(([value, label]) => value + ' ' + label)
  .join(', ');
assert(withLimits.includes('12 of 60'), withLimits);
assert(withLimits.includes(LOCALE['model.detail.limit_left_label']
  .replace('{unit}', probe.limitUnit(row))), withLimits);
assert(!probe.useStats({id: 'q/n'}).map(([value, label]) => value + ' ' + label).join(', ')
  .includes('of 60'), 'siblings of a limited model stay silent');
""",
  )


def test_provider_files_are_drilldown_steps() -> None:
  """Multi-provider files list their providers. Single-provider files open their config."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  render_files = app.partition("function renderFiles() {")[2].partition("\n}\n")[0]
  assert 'class="sections drill provider-files"' in render_files
  assert '"data-file": index' in render_files
  assert 'variant: "tab"' not in render_files
  assert "state.fileOpen" in render_files
  open_file = app.partition("function openFile(index) {")[2].partition("\n}\n")[0]
  assert "state.fileOpen = true" in open_file
  assert "blocks.length === 1" in open_file
  assert "selectProviderSection(blocks[0])" in open_file
  assert "openProviderFile(index)" in open_file
  settings = app.partition("function renderSettings() {")[2].partition("\n}\n")[0]
  assert 'class="provider-flow" data-section="providers"' in settings
  assert 'class="card nested"' not in settings
  assert 'params.set("file"' in app and 'params.get("file")' in app
  run_app_js(
    "state, renderFiles, openFile, applySectionHash, selectProviderSection, "
    "formText, providerPaneActions, SECTION_STATE",
    """
sandbox.history.pushState = (value, _, hash) => {
  sandbox.history.state = value;
  sandbox.location.hash = hash;
};
sandbox.history.replaceState = sandbox.history.pushState;
probe.state.files = [
  {path: 'config/providers/free.yml', main: true, text: 'free',
   blocks: {openrouter: {api_base: 'https://openrouter.ai'}, pollinations: {api_base: 'https://text.pollinations.ai'}}},
  {path: 'config/providers/solo.yml', main: false, text: 'solo',
   blocks: {openrouter: {api_base: 'https://openrouter.ai'}}},
];
probe.state.forms = probe.state.files.map(file => JSON.parse(JSON.stringify(file.blocks)));
probe.state.formSaved = probe.state.forms.map((_, index) => probe.formText(index));
probe.state.saved = probe.state.files.map(file => file.text);
probe.state.view = 'form';
probe.state.file = 0;
probe.renderFiles();
const html = node('files').innerHTML;
assert(html.includes('class=\"sections drill provider-files\"'));
assert(html.includes('data-file=\"0\"') && html.includes('free.yml'));
assert(html.includes('data-file=\"1\"') && html.includes('solo.yml'));
assert(html.includes('id=\"new-provider\"'));
assert(!html.includes('class=\"tab'));
(async () => {
  await probe.openFile(0);
  assert(probe.state.fileOpen, 'the shared file opens');
  assert.strictEqual(probe.SECTION_STATE.providers.open, false, 'the shared file shows its list');
  assert.strictEqual(probe.SECTION_STATE.providers.key, '', 'the file has no selected provider yet');
  assert.strictEqual(new URLSearchParams(sandbox.location.hash.split('?')[1]).get('file'),
    'config/providers/free.yml', 'the file route is shareable');
  assert.strictEqual(new URLSearchParams(sandbox.location.hash.split('?')[1]).get('provider'), null);
  node('editor').value = 'free YAML draft';
  probe.state.view = 'yaml';
  await probe.openFile(1);
  assert.strictEqual(probe.state.view, 'form', 'a valid one-provider file opens its config from YAML mode');
  assert.strictEqual(probe.state.files[0].text, 'free YAML draft', 'switching files keeps the earlier YAML draft');
  assert(probe.state.fileOpen, 'the single-provider file opens');
  assert.strictEqual(probe.SECTION_STATE.providers.open, true, 'the provider config opens directly');
  assert.strictEqual(probe.SECTION_STATE.providers.key, 'openrouter');
  let route = new URLSearchParams(sandbox.location.hash.split('?')[1]);
  assert.strictEqual(route.get('file'), 'config/providers/solo.yml');
  assert.strictEqual(route.get('provider'), 'openrouter');
  const actions = probe.providerPaneActions(probe.SECTION_STATE.providers);
  assert(actions.includes('id=\"drop-provider\"'), 'delete stays available');
  assert(actions.includes('data-provider-section=\"yaml\"'), 'YAML stays available');

  probe.SECTION_STATE.settings.key = 'providers';
  probe.SECTION_STATE.settings.open = true;
  probe.state.file = 0;
  probe.state.fileOpen = true;
  probe.state.view = 'yaml';
  node('editor').value = 'free deep-link draft';
  sandbox.location.hash = '#/settings?section=providers&file=config/providers/solo.yml';
  probe.applySectionHash();
  assert.strictEqual(probe.state.view, 'form', 'a provider deep link restores the config view');
  assert.strictEqual(probe.state.file, 1, 'the route selects its provider file');
  assert.strictEqual(probe.state.files[0].text, 'free deep-link draft', 'a route change preserves the YAML draft');
  assert.strictEqual(probe.SECTION_STATE.providers.key, 'openrouter');
  route = new URLSearchParams(sandbox.location.hash.split('?')[1]);
  assert.strictEqual(route.get('file'), 'config/providers/solo.yml');
  assert.strictEqual(route.get('provider'), 'openrouter');

  probe.state.view = 'yaml';
  probe.state.providerReturnKey = 'openrouter';
  probe.SECTION_STATE.providers.key = 'yaml';
  probe.SECTION_STATE.providers.open = true;
  probe.state.saved[1] = 'solo';
  node('editor').value = 'unsaved YAML';
  const switchBack = probe.selectProviderSection('openrouter');
  sandbox.__settle(false);
  await switchBack;
  assert.strictEqual(probe.state.providerReturnKey, 'openrouter', 'cancel keeps the YAML return route');
  assert.strictEqual(probe.state.view, 'yaml', 'cancel keeps the YAML editor open');
})().catch(error => { console.error(error); process.exit(1); });
""",
  )


def test_provider_yaml_draft_is_captured_before_settings_section_changes() -> None:
  """Changing Settings sections preserves the provider YAML draft."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  helper = app.partition("function captureProviderYamlDraft() {")[2].partition("\n}\n")[
    0
  ]
  assert helper, "the capture helper exists"
  section = app.partition("function openSection(host, key) {")[2].partition("\n}\n")[0]
  capture = section.index("captureProviderYamlDraft();")
  reset = section.index("state.fileOpen = false;")
  assert capture < reset, "the helper runs before the provider view resets"
  close = app.partition("function closeSection(host) {")[2].partition("\n}\n")[0]
  close_capture = close.index("captureProviderYamlDraft();")
  back = close.index("if (history.state?.section === key ||")
  assert close_capture < back, "the helper runs before the back route changes"
  run_app_js(
    "state, captureProviderYamlDraft",
    """
probe.state.files = [{path: 'config/providers/free.yml', text: 'saved'}];
probe.state.file = 0;
probe.state.fileOpen = true;
probe.state.view = 'yaml';
node('editor').value = 'unsaved desktop draft';
probe.captureProviderYamlDraft();
assert.strictEqual(probe.state.files[0].text, 'unsaved desktop draft',
  'the helper copies the editor text to its file');
probe.state.fileOpen = false;
node('editor').value = 'unrelated editor value';
probe.captureProviderYamlDraft();
assert.strictEqual(probe.state.files[0].text, 'unsaved desktop draft',
  'a hidden editor cannot overwrite the draft');
""",
  )


def test_provider_flow_keeps_one_scroll_owner() -> None:
  """The provider flow fills the pane without nesting a second card around its form."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    ".section-pane.fit-host { overflow: hidden; mask-image: none; display: flex; flex-direction: column; }"
    in css
  ), "a pane that holds the provider flow stops scrolling"
  assert (
    ".section-pane.fit-host > .card,\n"
    "  .section-pane.fit-host > .provider-flow { flex: 1 1 auto; "
    "min-height: 0; display: flex; flex-direction: column; }" in css
  ), "the provider flow fills the pane height"
  assert (
    ".provider-flow > .settings { flex: 1 1 auto; min-width: 0; min-height: 0; margin: 0; }"
    in css
  )
  assert "#provider-form > .section-pane { grid-template-rows: auto 1fr; }" in css, (
    "the drill header stays compact while its content fills"
  )
  assert ".section-pane:not(.fit-host) > .card { align-self: start; }" in css, (
    "other sections keep content-height cards"
  )
  assert 'pane.classList.toggle("fit-host"' in app, "the pane learns what it holds"


def test_maintenance_actions_share_one_row() -> None:
  """Reset and check buttons ride one row, with hint at right."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  card = app[
    app.index("function maintenanceCard") : app.index("async function refreshSlow")
  ]
  assert card.index('class="editbar"') < card.index('id: "update-check"'), (
    "the check button rides in the action row"
  )
  assert ".card > .editbar { padding-inline: 0; }" in css, (
    "the shared row class fits the card padding"
  )
  assert "maint-reset" not in card and ".maint-reset" not in css, (
    "the one-off row class is gone"
  )
  assert ".yaml-save {" not in css, "the yaml row rides the shared class too"


def test_fallback_chain_opens_in_modal() -> None:
  """The chain cell opens a dialog instead of expanding a list in place."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  index = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert 'data-modal-template="chain"' in index and 'id="chain-modal-body"' in index
  assert "mobile-fallback-chain" not in app and "mobile-fallback-chain" not in css, (
    "the expand list leaves the page"
  )
  assert "chainRows(" not in app, "the hidden chain row leaves the table"
  assert "function openChain(" in app and "data-chain-at" in app, (
    "the chain cell opens the dialog"
  )
  assert ".chain-modal .chain-body { width: auto;" in css, (
    "the chain body spans the dialog"
  )
  desktop_css = css.split("@media (max-width:", 1)[0]
  assert (
    "--indent: 2ch; list-style-type: decimal; list-style-position: outside;"
    in desktop_css
    and "padding-inline-start: 2ch;" in desktop_css
    and '<span class="num">${i + 1}.</span>' not in app
  ), "chain modal uses Clause 15 outside decimal markers without inline ordinals"


def test_keys_page_holds_labels_on_one_line() -> None:
  """Keys and button CSS prevent key values and labels from wrapping."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '<table class="keys">' in app, "the keys card of Settings carries the table"
  assert '<td class="num muted mono">' in app, "the key value reads in the mono font"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".keys td:nth-child(2) { white-space: nowrap; }" in css, (
    "the key value stays on one line"
  )
  assert "button { font: inherit; cursor: pointer; white-space: nowrap; }" in css, (
    "a button label never wraps"
  )


def test_short_values_carry_full_text() -> None:
  """Request headers and short-value code include full-text titles."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = page.split('<table class="requests"', 1)[1].split("</thead>", 1)[0]
  assert head.count(' title="') == 11, "each Requests head carries a hover text"
  for key in (
    "time",
    "app",
    "session",
    "model",
    "effort",
    "served_by",
    "status",
    "input",
    "output",
    "ttft",
    "stream",
  ):
    assert f'title="request.column.{key}.hint"' in head, key
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    't("model.tokens.title", { count: m.max_input_tokens.toLocaleString() })' in app
  ), "the context count"
  assert 'title="${esc(m.tier)}"' in app, "the tier name"
  assert (
    't("model.context.pool_hint", { count: pool.context.toLocaleString() })' in app
  ), "the pool context count"
  assert 'title="${t("settings.keys.only_start_title")}"' in app, "the key start"
  for key in ("ok", "rate_limited", "upstream_error", "failed"):
    assert f't("request.status.{key}_text")' in app, key
  assert (
    't("limits.value.of_text", { remaining: r.remaining.toLocaleString(), limit: r.limit.toLocaleString() })'
    in app
  ), "the exact rate-limit counts"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    ".ghost.danger:hover { color: var(--bad); border-color: var(--bad); }" in css
  ), "a filled danger button keeps its label on hover"
  assert ".primary:hover:not(:disabled) { filter: brightness(1.2);" in css, (
    "the primary button answers the mouse"
  )
  assert ".tab:hover { color: var(--text); border-color: var(--muted); }" in css, (
    "the view tabs answer the mouse"
  )
  assert (
    ".tabs a {\n"
    "  display: flex; align-items: center; min-height: 48px; color: var(--muted);\n"
    "  text-decoration: none; font-size: 14px; font-weight: 500; white-space: nowrap;\n"
    "  flex: none; border-top: 2px solid transparent; border-bottom: 2px solid transparent;\n"
    "}" in css
  ), "desktop tabs keep their intrinsic width; narrow headers wrap the strip instead"
  assert (
    "grid-template-columns: minmax(max-content, 1fr) minmax(0, auto) minmax(max-content, 1fr);"
    in css
  ), "the desktop header balances outer tracks around the centered tab bar"
  assert (
    'header[data-wrapped="1"] { grid-template-columns: 1fr auto; grid-template-areas: "brand chips" "tabs tabs"; }'
    in css
  ), "the header wraps tabs to row 2 only when items cannot fit on one row"
  assert (
    "#ov-limits .line > :first-child { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }"
    in css
  ), "Overview Limits snippet rows truncate long model labels on one line"
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert (
    '<span><span data-i18n="nav.models_link">nav.models_link</span><span class="count" id="nav-models"></span></span>'
    in page
  ), "the tab badge aligns with the label baseline"
  assert ".tabs a .count" not in css
  assert "text-decoration: underline; }" in css, "a sort head marks its own click"
  assert "button.line.rebuild:hover { color: var(--accent-text); }" in css, (
    "the phone catalog line answers the mouse"
  )


def test_parallel_race_reads_on_page() -> None:
  """Race output includes the legend, winner, and chain labels."""
  app = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert 'rce: t("request.route.race_text")' in app, "the race code"
  assert 'code !== "rce" || raceOn()' in app, "the setting keeps race in the legend"
  assert 'a.race === "won"' in app and 't("request.chain.won_race_text")' in app, (
    "the winning step carries the mark"
  )
  assert "affinity?.mode" in app and 'line(t("common.affinity_label")' in app, (
    "the status card shows the applied affinity mode"
  )
  assert (
    "RACE_NOTES" in app and 'class="chain-race"' in app and "raceCode" not in app
  ), "the race decision reads inside the fallback chain, not in the model cell"
  assert 'slow: t("request.race.slow.label")' in app
  assert 'drawn: t("request.race.drawn.label")' in app, (
    "the chain note reads as the short label"
  )


def test_model_numbers_align_right() -> None:
  """Model numeric cells and sortable headers share right alignment."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = page.split('<tr id="model-head">', 1)[1].split("</tr>", 1)[0]
  for key in ("order", "context", "cooldown", "weight"):
    assert f'data-sort="{key}" class="' in head
    column = head.split(f'data-sort="{key}"', 1)[1].split(">", 1)[0]
    assert "num" in column, f"the {key} heading is numeric"
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  models = app.split("function renderModels() {", 1)[1].split("function timeLeft", 1)[0]
  assert 'class="hide-sm mid num${m.order > 1' in models
  assert 'class="hide-sm num muted"' in models
  assert 'class="num hide-sm"' in models
  assert '<td class="num">${mobileLabel(t("model.column.weight_column"))}' in models
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".models td.num, .models th.num { text-align: right; }" in css
  assert ".models .mid { text-align: center; }" in css


def test_every_screen_names_itself_in_one_page_title() -> None:
  """The header tabs name each screen, and Settings section buttons carry summary tooltips."""
  root = Path(__file__).resolve().parents[2]
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert "page-title" not in html
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "settings-list-title" not in app
  assert "page-title" not in app
  assert "data-pane-summary" not in app
  assert '<h1 class="pane-title" data-pane-title hidden></h1>' in app
  assert 'title: id === "settings" ? (SECTION_SUMMARIES[key] ?? null) : null' in app


def test_provider_list_omits_settings_hero_headers() -> None:
  """The provider list and Settings sections omit hero headers in the pane."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  files = app.partition("function renderFiles() {")[2].partition("\n}\n")[0]
  assert "provider-files-head" not in files
  section = app.partition("function selectSection(host, key,")[2].partition("\n}\n")[0]
  assert "data-pane-summary" not in section
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".pane-summary" not in css


def test_header_wraps_only_when_items_exceed_available_width() -> None:
  """syncHeaderWrap wraps tabs to row 2 only when brand, tabs, and chips overflow."""
  code = _app_js_vm(
    """
el("app").hidden = false;
const brandIcon = { hidden: false, offsetWidth: 28, scrollWidth: 28 };
const brandText = { hidden: false, offsetWidth: 80, scrollWidth: 80 };
const brand = { children: [brandIcon, brandText], _gap: "8px" };
const statusChip = { hidden: true, offsetWidth: 0, scrollWidth: 0 };
const logoutBtn = { hidden: false, offsetWidth: 66, scrollWidth: 66 };
const chips = { children: [statusChip, logoutBtn], _gap: "12px" };
const header = {
  clientWidth: 768,
  dataset: {},
  _pad: "24px",
  _colGap: "16px",
  querySelector(sel) {
    if (sel === ".brand") return brand;
    if (sel === ".chips") return chips;
    return null;
  },
};
nav.scrollWidth = 480;
const origQS = sandbox.document.querySelector;
sandbox.document.querySelector = (sel) => (sel === "#app > header" ? header : origQS(sel));
sandbox.getComputedStyle = (node) => {
  if (node === header) return { paddingLeft: header._pad, paddingRight: header._pad, columnGap: header._colGap };
  if (node === brand) return { columnGap: brand._gap, gap: brand._gap, display: "flex" };
  if (node === chips) return { columnGap: chips._gap, gap: chips._gap, display: "flex" };
  return { display: node.hidden ? "none" : "block" };
};
sandbox.syncHeaderWrap();
assert.strictEqual(header.dataset.wrapped, undefined, "fits on one row at 768px");
header.clientWidth = 720;
sandbox.syncHeaderWrap();
assert.strictEqual(header.dataset.wrapped, "1", "wraps to row 2 at 720px");
header.clientWidth = 768;
sandbox.syncHeaderWrap();
assert.strictEqual(header.dataset.wrapped, undefined, "unwraps back to one row at 768px");
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_overview_kpis_wrap_only_when_items_exceed_equal_column_width() -> None:
  """syncKpiWrap keeps 4 equal columns until any KPI exceeds a column track."""
  code = _app_js_vm(
    """
el("app").hidden = false;
const makeItem = (w) => ({
  children: [
    { scrollWidth: 40, offsetWidth: 40 },
    { scrollWidth: w, offsetWidth: w },
  ],
});
const kpis = el("ov-kpis");
kpis.dataset = {};
kpis.clientWidth = 702;
kpis.children = [makeItem(84), makeItem(78), makeItem(128), makeItem(102)];
const prevCS = sandbox.getComputedStyle;
sandbox.getComputedStyle = (node) => {
  if (node === kpis) return { paddingLeft: "20px", paddingRight: "20px", columnGap: "20px", gap: "14px 20px" };
  return prevCS ? prevCS(node) : { display: "block" };
};
sandbox.syncKpiWrap();
assert.strictEqual(kpis.dataset.wrapped, undefined, "4 equal columns fit at 702px");
kpis.clientWidth = 600;
sandbox.syncKpiWrap();
assert.strictEqual(kpis.dataset.wrapped, "1", "wraps to 2x2 when 128px exceeds column width");
kpis.clientWidth = 702;
sandbox.syncKpiWrap();
assert.strictEqual(kpis.dataset.wrapped, undefined, "unwraps back to 1 row at 702px");
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_provider_back_controls_have_room_and_touch_target() -> None:
  """Back controls have a measured gap, padded glyph, and phone touch target."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '<span class="back-label">${esc(back)}</span>' in app
  assert "back.textContent = label;" in app
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".pane-bar { display: flex; align-items: center; gap: 8px; }" in css
  assert "background: var(--bg); padding: 6px 0; margin: 0;" in css
  assert "padding: 4px 8px;" in css and "gap: 6px;" in css
  assert ".section-pane .back, #nav a { min-height: 44px; min-width: 44px; }" in css


def test_app_js_modal() -> None:
  """Only the confirmation action resolves the modal to true."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "confirm(" not in source, "the dashboard uses the modal, not window.confirm"
  assert "prompt(" not in source, (
    "the New provider flow uses the modal, not window.prompt"
  )
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { ask, state };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', value: '', hidden: false, returnValue: '',
    shown: false, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    showModal() { this.shown = true; },
    classList: { add: () => {}, remove: () => {}, toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node,
    querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [],
      addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, history: { replaceState: () => {} }, window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const probe = sandbox.__probe;
const modal = node('modal');
const answer = probe.ask('Log out', 'The dashboard session ends.', 'Log out');
assert(modal.shown, 'the modal opens');
assert.strictEqual(node('modal-title').textContent, 'Log out');
assert.strictEqual(node('modal-message').textContent, 'The dashboard session ends.');
assert.strictEqual(node('modal-ok').textContent, 'Log out');
assert.strictEqual(node('modal-field').hidden, true, 'a confirmation hides the name field');
modal.listeners.close({ target: { returnValue: 'ok' } });
const asked = probe.ask('New provider', 'Lowercase letters, digits and dashes.', 'Create', false, 'Provider name');
assert.strictEqual(node('modal-field').hidden, false, 'the name field shows');
assert.strictEqual(node('modal-input').placeholder, 'Provider name');
node('modal-input').value = 'acme';
modal.listeners.close({ target: { returnValue: 'ok' } });
asked.then((value) => assert.strictEqual(value, true, 'a name modal resolves true'));
const refused = probe.ask('Delete key', 'Clients with the key get 401 at once.', 'Delete', true);
modal.listeners.close({ target: { returnValue: 'cancel' } });
answer.then((value) => {
  assert.strictEqual(value, true, 'Confirm resolves true');
  return refused;
}).then((value) => {
  assert.strictEqual(value, false, 'Cancel resolves false');
  const third = probe.ask('Reset', 'All weights go back to 1.', 'Reset', true);
  modal.listeners.close({ target: { returnValue: '' } });
  return third;
}).then((value) => assert.strictEqual(value, false, 'a close without a value resolves false'));
// A click on the backdrop closes the dialog, and a click on the box keeps it.
modal.getBoundingClientRect = () => ({ left: 100, top: 100, right: 300, bottom: 200 });
let closed = null;
modal.close = (value) => { closed = value; modal.listeners.close({ target: { returnValue: value } }); };
const inside = probe.ask('Reset', 'All weights go back to 1.', 'Reset', true);
modal.listeners.click({ target: { tagName: 'FORM' }, currentTarget: modal, clientX: 10, clientY: 10 });
assert.strictEqual(closed, null, 'a click on a child of the dialog does not close it');
modal.listeners.click({ target: modal, currentTarget: modal, clientX: 200, clientY: 150 });
assert.strictEqual(closed, null, 'a click on the box keeps the dialog');
modal.listeners.click({ target: modal, currentTarget: modal, clientX: 10, clientY: 10 });
assert.strictEqual(closed, 'cancel', 'a click on the backdrop closes the dialog');
inside.then((value) => assert.strictEqual(value, false, 'a backdrop click resolves false'));
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_time_stamps() -> None:
  """Time helpers return dated stamps and localized relative labels."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "shortTime(" not in source and "dateTime(" not in source, (
    "one helper stamps each time"
  )
  assert 't("catalog.next_text"' in source, "the catalog label reads its Next key"
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { stamp, relative };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', value: '', hidden: false, returnValue: '',
    shown: false, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    showModal() { this.shown = true; },
    classList: { add: () => {}, remove: () => {}, toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node,
    querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [],
      addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const probe = sandbox.__probe;
const when = new Date(2026, 9, 4, 12, 30, 46);
assert.strictEqual(probe.stamp(when.getTime() / 1000), '2026-10-04 12:30:46', 'the stamp of an absolute time');
const now = Math.floor(Date.now() / 1000);
assert.strictEqual(probe.relative(now - 7200), LOCALE["time.hour_ago.other_text"].replace("{count}", "2"));
assert.strictEqual(probe.relative(now + 7200), LOCALE["time.hour_from_now.other_text"].replace("{count}", "2"));
assert.strictEqual(probe.relative(now - 600), LOCALE["time.minute_ago.other_text"].replace("{count}", "10"));
assert.strictEqual(probe.relative(now + 600), LOCALE["time.minute_from_now.other_text"].replace("{count}", "10"));
assert.strictEqual(probe.relative(now - 10), LOCALE["time.just_now_text"]);
assert.strictEqual(probe.relative(now - 60), LOCALE["time.minute_ago.one_text"]);
assert.strictEqual(probe.relative(now - 3600), LOCALE["time.hour_ago.one_text"]);
assert.strictEqual(probe.relative(now - 23 * 3600),
  LOCALE["time.hour_ago.other_text"].replace("{count}", "23"), 'hours stop below 1 day');
assert.strictEqual(probe.relative(now - 24 * 3600), LOCALE["time.day_ago.one_text"],
  '1 day uses yesterday');
assert.strictEqual(probe.relative(now - 2 * 86400),
  LOCALE["time.day_ago.other_text"].replace("{count}", "2"), 'later days are plural');
assert.strictEqual(probe.relative(now - 6 * 86400),
  LOCALE["time.day_ago.other_text"].replace("{count}", "6"), 'day labels stop below 7 days');
assert.strictEqual(probe.relative(now - 7 * 86400), LOCALE["time.week_ago.one_text"],
  '7 days uses last week');
assert.strictEqual(probe.relative(now - 21 * 86400),
  LOCALE["time.week_ago.other_text"].replace("{count}", "3"), 'weeks are plural below 30 days');
assert.strictEqual(probe.relative(now - 30 * 86400), LOCALE["time.month_ago.one_text"],
  '30 days uses last month');
assert.strictEqual(probe.relative(now - 90 * 86400),
  LOCALE["time.month_ago.other_text"].replace("{count}", "3"), 'months are plural below 365 days');
assert.strictEqual(probe.relative(now - 365 * 86400), LOCALE["time.year_ago.one_text"],
  '365 days uses last year');
assert.strictEqual(probe.relative(now - 730 * 86400),
  LOCALE["time.year_ago.other_text"].replace("{count}", "2"), 'years are plural');
sandbox.matchMedia = () => ({ matches: true, addEventListener: () => {} });
assert.strictEqual(probe.relative(now - 10), LOCALE["time.just_now.compact_text"]);
assert.strictEqual(probe.relative(now - 600), LOCALE["time.minute_ago.compact_text"].replace("{count}", "10"));
assert.strictEqual(probe.relative(now - 7200), LOCALE["time.hour_ago.compact_text"].replace("{count}", "2"));
assert.strictEqual(probe.relative(now - 86400), LOCALE["time.day_ago.compact_text"].replace("{count}", "1"));
assert.strictEqual(probe.relative(now - 14 * 86400), LOCALE["time.week_ago.compact_text"].replace("{count}", "2"));
assert.strictEqual(probe.relative(now - 90 * 86400), LOCALE["time.month_ago.compact_text"].replace("{count}", "3"));
assert.strictEqual(probe.relative(now - 730 * 86400), LOCALE["time.year_ago.compact_text"].replace("{count}", "2"));
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_retry_code() -> None:
  """Transition markup retains hook repeat codes, and legend code includes hook entries."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "const REPEAT_CODE = " in source, "the repeat code family"
  assert "function renderLegend(extra = state.legendExtra) {" in source, (
    "the hook rows join the base rows"
  )
  assert "...extra," in source, "the hook rows show below the base rows"
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { transitionCell };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', textContent: '', value: '', hidden: false, returnValue: '',
    shown: false, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
    showModal() { this.shown = true; },
    classList: { add: () => {}, remove: () => {}, toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node,
    querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [],
      addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, history: { replaceState: () => {} }, window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const cell = sandbox.__probe.transitionCell;
assert.ok(cell({ reason: 'rt2' }).includes('>rt2<'), 'the hook code shows as given');
assert.ok(cell({ reason: 'rt2' }).includes(LOCALE["request.route.repeat_text"]), 'the tooltip is generic');
assert.ok(cell({ reason: 'lmt' }).includes('>lmt<'), 'another reason keeps its own code');
assert.strictEqual(cell(null), '', 'no transition, no code');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_limit_units() -> None:
  """Limit-row markup includes full model titles and short units with expanded titles."""
  payload = {
    "checked": 0,
    "providers": [],
    "lanes": [
      {
        "model": "mistral/mistral-embed",
        "client": None,
        "at": 0,
        "rows": [
          {
            "kind": "tokens",
            "span": "minute",
            "remaining": 623000,
            "limit": 625000,
            "reset": 0,
          },
          {
            "kind": "requests",
            "span": "day",
            "remaining": 999,
            "limit": 1000,
            "reset": 0,
          },
          {
            "kind": "requests",
            "span": "minute",
            "remaining": 59,
            "limit": 60,
            "reset": 0,
          },
          {"kind": "requests", "span": None, "remaining": 5, "limit": 10, "reset": 0},
        ],
      }
    ],
  }
  code = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js',
  'utf8') + "\\nglobalThis.__probe = {{ renderLimits, state }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false,
    showModal: () => {{}},
    addEventListener: () => {{}}, classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }});
  return nodes.get(id);
}};
const sandbox = {{
  esc: (text) => String(text ??
    '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }})[c]),
  seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value),
  toLocaleString: (value) => String(value),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{ hidden: false, documentElement: {{ dataset: {{}} }}, getElementById: node,
    querySelector: () => ({{ firstChild: {{ textContent: 'M' }} }}), querySelectorAll: () => [],
      addEventListener: () => {{}} }},
  navigator: {{}}, location: {{ hash: '' }}, history: {{ replaceState: () => {{}} }},
    window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}), console: {{ error: () => {{}} }}, $: node,
}};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
sandbox.__probe.state.models = [{{id: 'mistral/mistral-embed'}}];
sandbox.__probe.renderLimits({json.dumps(payload)});
const html = node('limit-rows').innerHTML;
const rate = (unit, span) => LOCALE["limits.unit.rate_initial_text"]
  .replace("{{unit}}", LOCALE[unit]).replace("{{span}}", LOCALE[span]);
const tokensPerMinute = LOCALE["limits.unit.per_text"]
  .replace("{{unit}}", LOCALE["limits.unit.tokens_text"])
  .replace("{{span}}", LOCALE["limits.span.minute.label"]);
assert(html.includes('<td role="cell" class="name" title="mistral/mistral-embed">'), 'the model cell keeps 1 line');
assert(html.includes('>' + rate("limits.unit.tokens.initial_text",
  "limits.span.minute.initial_text") + '</td>'), 'tokens per minute');
assert(html.includes('>' + rate("limits.unit.requests.initial_text",
  "limits.span.minute.initial_text") + '</td>'), 'requests per minute');
assert(html.includes('>' + rate("limits.unit.requests.initial_text",
  "limits.span.day.initial_text") + '</td>'), 'requests per day');
assert(html.includes('>requests</td>'), 'a row without a window keeps its kind');
assert(html.includes('title="' + tokensPerMinute + '"'),
  'the hover text spells the unit out');
assert(html.includes('title="' + LOCALE["limits.unit.requests_text"] + '"'),
  'a row without a window gets the kind alone');
assert(!html.includes('>' + tokensPerMinute + '</td>'), 'the cell text stays short');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_overview_pool_rows_draw_marks_of_top_model() -> None:
  """Overview pool-row code calls the shared model-name formatter."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  row = re.search(
    r'draw\("ov-models", state\.pools\.map\(\(pool\) => \{.*?'
    r'top \? modelName\(top\.id\) : t\("model\.empty\.pool_text"\)',
    app,
    re.DOTALL,
  )
  assert row, "the pool row draws the marks of the top model"
  assert "top ? esc(top.id)" not in app, "no pool row keeps the plain model id"


def test_each_model_filter_selection_moves_rows_only() -> None:
  """Model filter code routes input, sort, and mode changes through selectRows."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "function selectRows() {" in app and 'replay($("models"));' in app, (
    "the selection replays the table"
  )
  for handler in (
    '$("search").addEventListener("input", selectRows);',
    '$("sort-small").addEventListener("change", () => {',
    '$("mode").addEventListener("change", () => {',
  ):
    assert handler in app, handler
  # Model filter handlers use selectRows rather than calling renderModels directly.
  toolbar = app[app.index('$("search").addEventListener') :]
  toolbar = toolbar[: toolbar.index("$('files')") if "$('files')" in toolbar else 4000]
  assert "renderModels();" not in toolbar, "each selection goes through selectRows"


def test_motion_reaches_modal_sections_and_switches() -> None:
  """Modal, section, file, and switch code includes shared motion hooks and CSS."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".modal[open] { opacity: 1; transform: none; }" in css, "the modal arrives"
  assert "@starting-style { .modal[open] {" in css, "and the same way on the open"
  assert (
    "transition: opacity var(--soft) var(--ease), transform var(--soft) var(--ease),"
    in css
  )
  assert ".modal[open]::backdrop { background: rgb(0 0 0 / 0.45); }" in css, (
    "the backdrop fades in"
  )
  assert (
    "if (arrived && (moved || (before && arrived.dataset.section !== before)) && !phoneSection()) replay(arrived);"
    in app
  ), "desktop section and drill swaps replay once without duplicating phone slides"
  assert 'replay([...host.querySelectorAll(".section-pane > .card")]' not in app, (
    "openSection does not fire a second competing replay"
  )
  assert (
    'const inPaneCard = Boolean(host?.matches?.(".section-pane > .card, .section-pane > [data-section], .hook-page"));'
    in app
  ), "section cards, provider flows, and hook pages fade in GSAP without a y offset"
  assert ".tile, .rail-search {" in css, (
    "rail tiles and search follow the fast ease token"
  )
  assert "function animateHookSwap(open)" in app, (
    "hook drill-downs share Settings swap motion"
  )
  assert 'replay($("provider-form"));' in app, "a file change arrives its form"
  assert "if (!off) replay(field);" not in app, "a switch toggle replays no row fade"
  assert (
    "tweenFromTo(arriving, { opacity: 0, x: open ? 14 : -14 }, { opacity: 1, x: 0 });"
    in app
  ), "the phone slides the pane and rail with GSAP"


def test_settings_sections_carry_rule() -> None:
  """Settings CSS defines header separators, and section code uses localized titles."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "padding: 8px 0; border-top: 1px solid var(--line);" in css, (
    "every settings row carries the rule that separates it from the row above"
  )
  assert ".card > h3 + .field, .card > .field:first-child { border-top: 0; }" in css, (
    "the first row of a headerless card keeps no rule of its own"
  )
  assert ".field:first-of-type" not in css, (
    "the rule follows the row order, not the tag"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '["balance", t("settings.balance.title"), [' in app, (
    "the group reads from the Load Distribution key"
  )
  assert 'keys: t("settings.keys.summary")' in app, (
    "the pane summary reads the keys summary key"
  )
  assert '["keys", t("settings.keys.title")], ["yaml", t("yaml.label")]' in app, (
    "the keys section reads its title keys"
  )


def test_settings_back_keeps_slide() -> None:
  """Settings-page code renders the provider cards before selection and slide replay."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  body = app[
    app.index("function showPage()") : app.index('window.addEventListener("hashchange"')
  ]
  render = body.index('if (page === "settings" && state.settings) renderSettings();')
  selection = body.index("applySectionHash();")
  assert render < selection, "the cards render before the section selection"
  assert "slideAgain();" in body[selection:], (
    "the slide of the selection replays on the fresh markup"
  )
  assert "function playSlide(host, open) {" in app, "1 helper plays the arriving slide"
  assert "state_.slide = true;" in app, (
    "a selection holds its slide for the render that follows"
  )
  assert 'for (const host of [$("settings")]) {' in app, (
    "the settings page replays its slide"
  )


def test_keyboard_nav_dataset_toggles_between_keys_and_pointer() -> None:
  """Navigation keys enable keyboard focus rings, and pointer input clears them."""
  code = """
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8');
const docEvents = {};
const el = () => ({ innerHTML: '', textContent: '', addEventListener: () => {},
  classList: { add: () => {}, remove: () => {}, toggle: () => {} } });
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: {
    hidden: false,
    documentElement: { dataset: {} },
    getElementById: el,
    querySelector: () => ({ firstChild: { textContent: 'Models' } }),
    querySelectorAll: () => [],
    addEventListener: (type, fn) => { (docEvents[type] ??= []).push(fn); },
  },
  navigator: {},
  location: { hash: '' },
  window: { addEventListener: () => {} },
  getSelection: () => ({ isCollapsed: true }),
  $: el,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
for (const fn of docEvents.keydown) fn({ key: 'Tab', ctrlKey: false, metaKey: false });
assert.strictEqual(sandbox.document.documentElement.dataset.keyboardNav, 'true');
for (const fn of docEvents.pointerdown) fn({});
assert.strictEqual(sandbox.document.documentElement.dataset.keyboardNav, undefined);
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_keeps_scroll_of_settings_pane() -> None:
  """Settings helpers restore pane scroll, and render code saves it before replacing markup."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "defaults": settings.DEFAULTS,
      "file": {},
    }
  )
  run_app_js(
    "state, renderSettings, settingsScroll, keepSettingsScroll",
    f"""
probe.state.settings = {payload};
const pane = {{
  scrollTop: 0, clientHeight: 200, scrollHeight: 600,
  classList: {{ toggle: () => {{}} }}, querySelector: () => null, style: {{ setProperty() {{}} }},
}};
node('settings').querySelector = (sel) => (sel === '.section-pane' ? pane : null);
pane.scrollTop = 240;
const at = probe.settingsScroll();
assert(at.pane === 240, 'the helper reads the pane of the settings');
pane.scrollTop = 0;
probe.keepSettingsScroll(at);
assert(pane.scrollTop === 240, 'the pane lands where it was');
""",
  )
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  body = app[
    app.index("function renderSettings()") : app.index("function renderSettingsSave()")
  ]
  assert body.index("const scroll = settingsScroll();") < body.index(
    '$("settings").innerHTML'
  ), "the scroll comes from before the rebuild"
  assert "keepSettingsScroll(scroll);" in body, "the rebuild puts the pane back"


def test_app_js_settings_section_buttons_carry_summary_tooltips() -> None:
  """Settings rail buttons carry the section description in their title attribute."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "defaults": settings.DEFAULTS,
      "file": {},
    }
  )
  run_app_js(
    "state, renderSettings, SECTION_SUMMARIES",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(!html.includes('id="set-personalization-hide_headers"'), 'hide_headers is removed');
assert(!html.includes('data-pane-summary'), 'hero summary header is removed');
for (const [key, text] of Object.entries(probe.SECTION_SUMMARIES)) {{
  assert(
    html.includes(`data-section="${{key}}"`) && html.includes(`title="${{text}}"`),
    `section ${{key}} carries its summary in title`,
  );
}}
""",
  )
  assert "hide_headers" not in settings.DEFAULTS["personalization"]


def test_app_js_settings_switches() -> None:
  """Settings markup includes the affinity switch, and unchanged fields produce no save changes."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "defaults": settings.DEFAULTS,
      "file": {"affinity": {"mode": "session", "change_on_draw": True}},
    }
  )
  run_app_js(
    "state, renderSettings, settingsChanges, listValue, setListValue, dropSetting",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
const at = html.indexOf('id="set-affinity-change_on_draw"');
assert(at > 0, 'the change_on_draw field is in the form');
assert(html.slice(Math.max(0, at - 120), at).includes('type="checkbox"'), 'change_on_draw is a checkbox');
assert(html.slice(Math.max(0, at - 120), at).includes('role="switch"'), 'the row draws a switch');
// The label and its hint ride at the left of the row, before the switch.
const row = html.slice(html.lastIndexOf('<label', at), at);
assert(row.includes('class="hint"') && row.includes('aria-describedby="hint-'), 'the row carries its hint icon');
assert(row.includes('role="tooltip"'), 'the hint text serves as the tooltip');
for (const id of ['set-affinity-change_on_draw', 'set-balance-weights', 'set-balance-pacing',
  'set-optimization-enabled']) node(id).checked = true;
node('set-affinity-mode').value = 'session';
node('set-personalization-theme').dataset = {{ themeValue: 'system' }};
node('set-personalization-time_format').value = '24h';
assert.strictEqual(JSON.stringify(probe.settingsChanges()), '{{}}', 'a loaded file reports no change');
// The keyword fields are stacked lists with a + adder, not a text box.
const list = html.slice(Math.max(0, html.indexOf('id="set-routing-escalation"') - 40),
  html.indexOf('id="set-routing-escalation"') + 3400);
assert(list.includes('class="setting-list"'), 'the keyword field is a list');
assert(list.includes('class="setting-list-row"'), 'each keyword is a list row');
const adder = html.indexOf('data-setting-add=');
assert(adder > 0, 'the + adder is in the form');
assert(html.slice(adder, adder + 80).includes('escalation'), 'the + adder names its list');
assert(!html.includes('<textarea id="set-routing-escalation"'), 'no text box for the keywords');
// A dropped chip leaves the list, and the change reaches the save payload.
const first = probe.listValue('routing', 'escalation')[0];
probe.dropSetting(['routing', 'escalation', 0]);
assert(!probe.listValue('routing', 'escalation').includes(first), 'the chip left the list');
assert.deepStrictEqual(probe.settingsChanges().routing.escalation, probe.listValue('routing', 'escalation'),
  'the change reaches the save payload');
// The raw list retains duplicate values.
probe.setListValue('routing', 'switch', [...probe.listValue('routing', 'switch'), 'clanker', 'clanker']);
assert.strictEqual(probe.listValue('routing', 'switch').length,
  probe.state.settings.defaults.routing.switch.length + 2, 'the raw list takes both');
""",
  )


def test_app_js_provider_rows_draw_providers_glyph() -> None:
  """The nested rail rows carry their mark or glyph, and every chip name exists."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  block = app.partition("const chipFor = (host, key) => {")[2].partition("};")[0]
  assert (
    'const name = known ?? (key === "free" ? "sparkles" : (host === "provider-form" ? SECTION_CHIPS.providers : ""));'
    in block
  ), "free.yml uses sparkles and unknown provider rows draw the providers glyph"
  assert "MARK_FILES.has(key)" in block, "a provider row with a mark draws that mark"
  assert (
    'const tint = known || PROVIDER_CHIPS.has(key) ? key : "providers";' in block
  ), "known providers and free.yml get their own distinct tile color"
  render_files = app.partition("function renderFiles() {")[2].partition("\n}\n")[0]
  assert 'chipFor("provider-form", stem)' in render_files, (
    "provider file rows derive their slug from the filename"
  )
  chips = app.partition("const SECTION_CHIPS = {")[2].partition("};")[0]
  for glyph in re.findall(r': "([a-z_0-9]+)"', chips):
    assert re.search(rf"^  {glyph}: '", app, re.MULTILINE), (
      f"the glyph {glyph} is registered"
    )
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "[data-chip=" not in css, (
    "category tiles use semantic role tokens without hex literals"
  )
  assert (
    "background: transparent; color: var(--muted); border: 1px solid transparent;"
    in css
  )
  assert (
    "background: var(--accent); color: var(--on-accent); border-color: var(--accent);"
    in css
  )


def test_app_js_hook_point_chips() -> None:
  """Hook-table markup lists declared surfaces without surface-edit controls."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "hook_files": ["hooks/one.py", "hooks/off.py", "hooks/bare.py"],
      "defaults": settings.DEFAULTS,
      "file": {
        "hooks": {
          "on-request": ["hooks/one.py"],
          "on-chunk": ["hooks/served_model.py"],
        }
      },
      "hook_rows": [
        {
          "name": "one.py",
          "path": "hooks/one.py",
          "version": "1.2.0",
          "scope": "global",
          "targets": [],
          "surfaces": ["on-request"],
          "runs": ["on-request"],
          "enabled": True,
          "problem": "",
        },
        {
          "name": "off.py",
          "path": "hooks/off.py",
          "version": "1.0.0",
          "scope": "global",
          "targets": [],
          "surfaces": ["on-chunk"],
          "runs": ["on-chunk"],
          "enabled": False,
          "problem": "",
        },
        {
          "name": "bare.py",
          "path": "hooks/bare.py",
          "version": "1.0.0",
          "scope": "global",
          "targets": [],
          "surfaces": [],
          "runs": [],
          "enabled": True,
          "problem": "",
        },
      ],
    }
  )
  run_app_js(
    "state, renderSettings, HOOK_SURFACES",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(probe.HOOK_SURFACES.every((surface) => surface.length === 2),
  'surface metadata holds only the key and rendered label');
assert(!html.includes('id="set-hooks-on-request"'), 'the surface section left the card body');
assert(html.includes('<th>' + LOCALE["hook.column.surfaces_column"] + '</th>'), 'the table names its surface column');
assert(html.includes('class="pill on named">' + LOCALE["hook.surface.request.label"]),
  'the named surface reads on without a title');
assert(html.includes('class="pill off named">' + LOCALE["hook.surface.chunk.label"]),
  'the off file reads dim without a title');
assert(html.includes('<em class="none">' + LOCALE["hook.empty.surface_text"] + '</em>'),
  'a file that names no surface reads none');
assert(!html.includes('data-hook-surface-select'), 'nothing adds a surface');
assert(!html.includes('data-hook-surface-drop'), 'nothing takes a surface off');
assert(!html.includes('class="menu"'), 'no surface list opens');
assert(html.includes('>' + LOCALE["hook.scope.local_text"] + '<'), 'a file with no source reads local');
""",
  )


def test_app_js_hook_order_includes_installed_files() -> None:
  """Installed hooks enter surface order, and reordering saves only an order hint."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "hook_files": ["hooks/auto.py", "hooks/served.py"],
      "defaults": settings.DEFAULTS,
      "file": {"hooks": {"sources": []}},
      "hook_rows": [
        {
          "name": "auto.py",
          "path": "hooks/auto.py",
          "scope": "client",
          "targets": ["OWUI"],
          "surfaces": ["on-request", "on-chunk"],
          "runs": [],
          "enabled": True,
          "problem": "",
        },
        {
          "name": "served.py",
          "path": "hooks/served.py",
          "scope": "client",
          "targets": ["OWUI"],
          "surfaces": ["on-chunk"],
          "runs": [],
          "enabled": True,
          "problem": "",
        },
      ],
    }
  )
  run_app_js(
    "state, renderSettings, hookOrderFiles, moveHookFile, settingsChanges",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
const start = html.indexOf('id="hooks-order"');
const end = html.indexOf('>Folder<', start);
const order = html.slice(start, end);
assert(order.includes('auto.py'), 'an automatically installed hook appears in its surface order');
assert(order.includes('served.py'), 'the other installed hook appears too');
assert(order.indexOf('auto.py') < order.lastIndexOf('served.py'), 'the default is file-name order');
assert(order.includes('data-hook-drag'), 'the installed hooks carry drag controls');
probe.moveHookFile(['on-chunk', 0, 1]);
assert.deepStrictEqual(Array.from(probe.hookOrderFiles('on-chunk')),
  ['hooks/served.py', 'hooks/auto.py'], 'the move changes the effective surface order');
assert.deepStrictEqual(JSON.parse(JSON.stringify(probe.settingsChanges().hooks)),
  {{order: {{'on-chunk': ['hooks/served.py', 'hooks/auto.py']}}}},
  'the save writes only the order hint');
""",
  )


def test_app_js_one_provider_file_opens_provider_page() -> None:
  """A chip of a one-provider file drills straight into that provider page."""
  files = [
    {
      "path": "config/providers/openrouter.yml",
      "text": "openrouter: {}",
      "blocks": {"openrouter": {"api_key": "env:O"}},
      "main": False,
    },
    {
      "path": "config/providers/free.yml",
      "text": "free",
      "blocks": {
        "cloudflare": {"api_key": "env:C"},
        "openrouter": {"api_key": "env:F"},
      },
      "main": True,
    },
  ]
  run_app_js(
    "state, takeFiles, openFile",
    f"""
probe.takeFiles({json.dumps(files)});
probe.state.file = 1;
probe.openFile(0);
assert.strictEqual(node('provider-form').dataset.detail, '1',
  'the one-provider file opens its provider page at once');
probe.openFile(1);
assert.strictEqual(node('provider-form').dataset.detail, '0',
  'a shared file keeps the provider list');
""",
  )


def test_app_js_settings_entry_shows_first_section_card() -> None:
  """A tab switch into Settings with no section in the hash shows one card."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "hook_files": [],
      "defaults": settings.DEFAULTS,
      "file": {"hooks": {"sources": []}},
      "hook_rows": [],
    }
  )
  run_app_js(
    "state, renderSettings, selectSection",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const settings = node('settings');
const html = settings.innerHTML;
const cards = [...html.matchAll(/<div class="card[^"]*" data-section="([a-z]+)"/g)]
  .map((m) => ({{ dataset: {{ section: m[1] }}, hidden: false }}));
const buttons = [...html.matchAll(/<button[^>]*data-section="([a-z]+)"/g)]
  .map((m) => {{
    const button = {{ dataset: {{ section: m[1] }}, on: false,
      setAttribute: () => {{}}, removeAttribute: () => {{}}, querySelector: () => null }};
    button.classList = {{ toggle: (name, on) => {{ if (name === 'on') button.on = on; }} }};
    return button;
  }});
const summary = {{ innerHTML: '' }};
settings.querySelectorAll = (sel) => (sel.includes('> [data-section]') ? cards : buttons);
settings.querySelector = (sel) => {{
  if (sel.includes('data-pane-summary')) return summary;
  const want = sel.match(/data-section="([a-z]+)"/);
  if (want) return buttons.find((button) => button.dataset.section === want[1]) ?? null;
  return sel.includes('button') ? buttons[0] : null;
}};
// The tab switch lands on Settings with no section in the hash.
probe.selectSection(settings, '', false);
probe.selectSection(node('provider-form'), '', false);
const marked = buttons.filter((button) => button.on).map((button) => button.dataset.section);
assert.deepStrictEqual(marked, ['routing'], 'only the shown section keeps its rail mark');
const shown = cards.filter((card) => !card.hidden);
assert.strictEqual(shown.length, 1, 'exactly one settings card shows on entry');
assert.strictEqual(shown[0].dataset.section, buttons[0].dataset.section,
  'the shown card is the first rail section');
""",
  )


def test_app_js_file_change_leaves_selection_of_other_file() -> None:
  """File changes select the new first section and reset the phone detail state."""
  files = [
    {
      "path": "config/providers/openrouter.yml",
      "text": "openrouter: {}",
      "blocks": {"openrouter": {"api_key": "env:O"}},
      "main": True,
    },
    {
      "path": "config/providers/free.yml",
      "text": "free",
      "blocks": {
        "cloudflare": {"api_key": "env:C"},
        "openrouter": {"api_key": "env:F"},
      },
      "main": True,
    },
  ]
  run_app_js(
    "state, takeFiles, renderForm, selectSection, openFile",
    f"""
probe.takeFiles({json.dumps(files)});
probe.state.file = 0;
probe.renderForm();
// The user of a phone opens the openrouter section of openrouter.yml as a page.
probe.selectSection(node('provider-form'), 'openrouter', true);
// Then the user switches the file to free.yml.
probe.openFile(1);
const html = node('provider-form').innerHTML;
const list = html.slice(html.indexOf('class="card list"'), html.indexOf('class="card provider"'));
assert(list.includes('data-section="cloudflare"'), 'the list names the first host of the new file');
assert(list.includes('data-section="yaml"'), 'the list keeps the yaml row');
assert(!list.includes('aria-current'), 'no list row carries a selected mark over');
assert.strictEqual(node('provider-form').dataset.detail, '0', 'the phone returns to the section list');
assert(html.includes(LOCALE["provider.yaml.file_hint"].replace("{{file}}", "free.yml")),
  "the yaml card names the new file");
""",
  )


def test_hook_chip_matches_keyword_chips() -> None:
  """Hook markup uses shared pills, themed menu CSS, and listbox roles."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  chip = re.search(r"\.pill\.hook \.select \{([^}]*)\}", css)
  assert chip and "border: 0" in chip.group(1)
  assert "padding: 0" in chip.group(1) and "background: transparent" in chip.group(1)
  assert "font: 12px/20px var(--mono)" in chip.group(1), "the keyword chip font"
  ring = re.search(r"\.pill\.hook:focus-within \{([^}]*)\}", css)
  assert ring and "border-color: var(--accent)" in ring.group(1), (
    "the focus ring wraps the chip"
  )
  assert re.search(r"\.keys td:nth-child\(3\) \{ white-space: nowrap; \}", css), (
    "the scope column keeps its word on one line"
  )
  menu = re.search(r"\.pill\.hook \.menu \{([^}]*)\}", css)
  assert menu and "background: var(--panel)" in menu.group(1), (
    "the list follows the page theme"
  )
  assert "position: absolute" in menu.group(1), "the list hangs under the chip"
  assert "border: 1px solid var(--line)" in menu.group(1)
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    'const settingListRow = (group, key, value, index) => `<li class="setting-list-row">'
    in app
  )
  markup = re.search(r"const hookFormRow = .*?\n};", app, re.DOTALL)
  assert markup, "the hook row builds 1 chip"
  assert 'class="pill hook"' in markup.group(0), "the row rides in a chip"
  assert 'title: t("hook.action.select_file_title")' in markup.group(0), (
    "the chip names its selector"
  )
  assert 'role="listbox"' in markup.group(0), "the list names its role"
  assert 'role: "option"' in app and '"aria-selected":' in app, (
    "each choice is an option"
  )
  assert "data-hook-select" not in app, "no system menu on a chip"


def test_app_js_affinity_modes_hide_rows() -> None:
  """Affinity mode selects its own row visibility without changing saved settings."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "defaults": settings.DEFAULTS,
      "file": {"affinity": {"mode": "session"}},
    }
  )
  run_app_js(
    "state, renderSettings, settingsChanges",
    f"""
probe.state.settings = {payload};
const ids = ['set-affinity-mode', 'set-affinity-change_on_draw', 'set-affinity-idle', 'set-affinity-stay',
  'set-affinity-count', 'set-affinity-chance', 'set-affinity-slow', 'set-affinity-penalty'];
const shown = () => ids.filter((id) => !node(id).hidden);
// The 2 pin-draw values come with session only: under race the pinned model leads, so no draw runs.
const expect = {{
  none: ['set-affinity-mode'],
  session: ['set-affinity-mode', 'set-affinity-change_on_draw', 'set-affinity-idle', 'set-affinity-stay'],
  race: ['set-affinity-mode', 'set-affinity-idle', 'set-affinity-count', 'set-affinity-chance',
    'set-affinity-slow', 'set-affinity-penalty'],
}};
const rendered = (mode) => {{
  node('set-affinity-mode').value = mode;
  probe.state.settings.file.affinity = {{ mode }};
  probe.renderSettings();
  assert.deepStrictEqual(shown(), expect[mode], mode + ' shows its own rows');
}};
rendered('none');
rendered('session');
rendered('race');
// A new selection moves the rows of the card at once, and the file keeps its own mode.
probe.state.settings.file.affinity = {{ mode: 'session' }};
for (const selection of ['none', 'race', 'session']) {{
  const select = node('set-affinity-mode');
  select.value = selection;
  node('settings').handlers.input.forEach((fn) => fn({{ target: select }}));
  assert.strictEqual(select.value, selection, 'the selection stays after a change to ' + selection);
  assert.deepStrictEqual(shown(), expect[selection], selection + ' moves the rows');
  assert.strictEqual(probe.state.settings.file.affinity.mode, 'session', 'the file is not written back');
}}
// A selection that differs from the file reaches the write payload of that 1 row.
const select = node('set-affinity-mode');
select.value = 'race';
node('settings').handlers.input.forEach((fn) => fn({{ target: select }}));
assert.strictEqual(probe.settingsChanges().affinity.mode, 'race', 'the selection reaches the save payload');
""",
  )


def test_app_js_switch_rows_disable_dependent_controls() -> None:
  """A switch disables only its dependent controls."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": True,
      "text": "",
      "defaults": settings.DEFAULTS,
      "file": {},
    }
  )
  run_app_js(
    "state, renderSettings, showSwitchRows",
    f"""
probe.state.settings = {payload};
// The rows of the Weights and Headroom cards, with the controls that the grey keeps in step.
const rows = ['set-balance-success', 'set-balance-fault', 'set-balance-slow', 'set-balance-hourly',
  'set-balance-rate_limit', 'set-optimization-timeout', 'set-balance-pacing'];
for (const id of rows) {{
  const made = node(id);
  made.off = new Set();
  made.controls = [{{ disabled: false }}, {{ disabled: false }}];
  made.added = new Set();
  made.classList = {{ add: (name) => made.added.add(name),
    remove: (name) => made.added.delete(name),
    toggle: (name, on) => (on ? made.off.add(name) : made.off.delete(name)) }};
  made.querySelectorAll = () => made.controls;
}}
const off = (id) => node(id).off.has('off');
const disabled = (id) => node(id).controls.every((control) => control.disabled);
// The first render greys the factors of an off Weights switch and leaves Headroom live.
node('set-balance-weights').checked = false;
node('set-optimization-enabled').checked = true;
probe.renderSettings();
assert(rows.slice(0, 5).every(off), 'every weights factor greys');
assert(rows.slice(0, 5).every(disabled), 'and every factor control goes disabled');
assert(!off('set-balance-pacing') && !disabled('set-balance-pacing'), 'pacing stays independent');
assert(!off('set-optimization-timeout') && !disabled('set-optimization-timeout'), 'the other card stays live');
// The switch keeps its own row live, so it can be turned back on.
assert(!node('set-balance-weights').disabled, 'the switch stays usable');
// An on switch clears its card, and the off one greys only its own card.
node('set-balance-weights').checked = true;
node('set-optimization-enabled').checked = false;
probe.showSwitchRows();
assert(!off('set-balance-success') && !disabled('set-balance-success'), 'the on switch clears the weights');
assert(off('set-optimization-timeout') && disabled('set-optimization-timeout'), 'the headroom timeout greys');
// The other boolean rows of a card, such as Affinity's Change pin on draw, grey nothing.
const idle = node('set-affinity-idle');
idle.off = new Set();
idle.controls = [{{ disabled: false }}];
idle.classList = {{ toggle: (name, on) => (on ? idle.off.add(name) : idle.off.delete(name)) }};
idle.querySelectorAll = () => idle.controls;
node('set-affinity-change_on_draw').checked = false;
probe.showSwitchRows();
assert(!off('set-affinity-idle') && !disabled('set-affinity-idle'), 'the affinity pin selection greys nothing');
// The form listens for the flip, so the card greys on the click without a save.
node('set-balance-weights').checked = true;
node('set-optimization-enabled').checked = true;
probe.showSwitchRows();
// The fade belongs to the section arrival alone: a toggle back on animates nothing.
assert(!node('set-optimization-timeout').added.has('drawn'), 'the headroom toggle plays no row fade');
assert(!node('set-balance-success').added.has('drawn'), 'the weights toggle plays no row fade');
node('set-balance-weights').checked = false;
node('set-balance-weights').type = 'checkbox';
node('settings').handlers.input.forEach((fn) => fn({{ target: node('set-balance-weights') }}));
assert(off('set-balance-success'), 'the flip greys the card at once');
assert(!off('set-optimization-timeout'), 'and leaves the other card alone');
""",
  )


def test_mode_conventions() -> None:
  """Mode definitions include localized script labels and filter option values."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  html = Path("daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  for mode in sorted({"chat", *base.OUTPUT_MODES.values()}):
    assert re.search(rf'\b{mode}: t\("model\.mode\.{mode}_label"\)', app), (
      f"{mode} has no localized label"
    )
    assert f'value="{mode}"' in html, f"{mode} has no Type filter option"


def test_app_js_hooks_section() -> None:
  """Settings code includes hook folder, source, selection, and update controls."""
  script = TestClient(api.app).get("/ui/app.js")
  assert script.status_code == 200
  for piece in (
    'id: "set-hooks-dir"',
    "data-hook-source-add",
    "data-hook-source-drop",
    "data-hook-source",
    "data-hook-toggle",
    "data-hooks-update",
    "data-hook-take",
    "data-hook-take-all",
    '${icon("download")} ${t("action.take_files_button")}',
    'ask(t("dialog.source.title")',
    "hooks/scan",
    'listValue("hooks", "sources")',
    'listValue("hooks", "disabled")',
    'fileValue("hooks", "dir")',
    "hook_rows",
    'setting("hooks", "dir")',
  ):
    assert piece in script.text, piece


def test_app_js_hooks_manager_renders() -> None:
  """Hook-manager markup lists metadata and controls, and edits reach the settings payload."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "hook_files": ["hooks/one.py"],
      "hook_rows": [
        {
          "name": "one.py",
          "path": "hooks/one.py",
          "title": "One hook",
          "version": "1.2.0",
          "scope": "provider",
          "targets": ["openrouter"],
          "surfaces": ["on-answer"],
          "enabled": True,
          "problem": "",
          "record": {
            "sha256": "d" * 64,
            "version": "1.2.0",
            "repo": "owner/name",
            "commit": "c" * 40,
          },
        },
        {
          "name": "bad.py",
          "path": "hooks/bad.py",
          "version": "",
          "scope": "",
          "targets": [],
          "surfaces": [],
          "enabled": False,
          "problem": "unknown surface 'on-later'",
          "record": {},
        },
      ],
      "defaults": settings.DEFAULTS,
      "file": {
        "hooks": {
          "dir": "hooks",
          "sources": [
            {
              "repo": "owner/name",
              "path": "hooks",
              "ref": "main",
              "auto_update": False,
            }
          ],
          "disabled": ["bad.py"],
        }
      },
    }
  )
  run_app_js(
    "state, renderSettings, settingsChanges, setListValue, iconSlot",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(html.includes('id="set-hooks-dir"'), 'the folder field');
assert(html.includes('value="hooks"'), 'the folder shows the saved value');
assert(html.includes('id="hook-source"') && html.includes('>owner/name/hooks<'), 'the sources show the saved repo');
assert(html.includes('data-hook-source-add') && html.includes('data-hook-source-drop') &&
  html.includes('data-hooks-update'), 'the source controls');
assert(html.includes(probe.iconSlot('refresh_cw') + '<span>' + LOCALE['action.update_sources_button'] + '</span>'),
  'the update button leads with the refresh glyph slot');
assert(html.includes('<th>' + LOCALE["hook.column.author_title"] + '</th>'),
  'the table names its author and title column');
assert(html.includes('One hook'), 'the title of the file shows');
assert(html.includes('data-hook-toggle="one.py"') && html.includes('data-hook-toggle="bad.py"'), '1 switch per file');
assert(html.includes('>1.2.0<') && html.includes('provider: openrouter'), 'the version and the scope of a file');
assert(html.includes('owner/name@ccccccc'), 'the source of an installed file');
assert(html.includes('unknown surface'), 'a bad block shows its problem');
const phoneError = html.match(/<span class="hook-problem only-sm">([^<]+)/);
assert(phoneError && phoneError[1].includes('unknown surface'), 'the phone card keeps the file error');
assert(!html.includes('data-hook-take'), 'no scan before a repo is selected');
probe.setListValue('hooks', 'disabled', []);
assert(JSON.stringify(probe.settingsChanges().hooks.disabled) === '[]', 'the off names reach the payload');
probe.setListValue('hooks', 'sources', []);
assert(JSON.stringify(probe.settingsChanges().hooks.sources) === '[]', 'the sources reach the payload');
node('set-hooks-dir').value = 'mine';
assert(probe.settingsChanges().hooks.dir === 'mine', 'the folder reaches the payload');
""",
  )


def test_app_js_capability_chip_icons() -> None:
  """One renderer gives each registered chip icon its decorative SVG contract."""
  run_app_js(
    "ICON_PATHS, icon",
    """
const names = Object.keys(probe.ICON_PATHS);
assert(names.length > 0, 'the icon registry has callers');
for (const name of names) {
  const path = probe.ICON_PATHS[name];
  const glyph = probe.icon(name);
  assert(!path.includes('<svg') && !path.includes('<style') && !path.includes('<g'), name);
  assert(glyph.startsWith('<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"'), name);
  assert(glyph.includes('fill="none" stroke="currentColor" stroke-width="2"'), name);
  assert(glyph.endsWith(`${path}</svg>`), name);
}
assert(probe.ICON_PATHS.wrench.includes('M14.7 6.3'), 'the lucide wrench path');
assert(probe.ICON_PATHS.brain.includes('M12 18V5'), 'the lucide brain path');
assert(probe.ICON_PATHS.refresh_cw.includes('M3 12a9 9'), 'the lucide refresh path');
assert(probe.ICON_PATHS.download.includes('M12 15V3'), 'the lucide download path');
""",
  )
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    'title="${t("model.column.tools_column")}">${icon(CHIP_ICON_NAMES.tools)}' in app
  ), "the wrench draws in the tools chip"
  assert (
    'title="${t("model.column.reasoning_column")}">${icon(CHIP_ICON_NAMES.brain)}'
    in app
  ), "the brain draws in the reasoning chip"


def test_app_js_hook_fit() -> None:
  """Hook scope matching checks provider and model targets, with global scope as the default."""
  code = _app_js_vm(
    """
const rows = [
  { name: 'any.py', scope: 'global', targets: [] },
  { name: 'or.py', scope: 'provider', targets: ['openrouter'] },
  { name: 'gpt.py', scope: 'model', targets: ['gpt-4o'] },
  { name: 'bad.py', scope: 'global', targets: [], problem: 'unknown surface' },
];
assert.strictEqual(sandbox.hookFit(rows[0], 'provider', 'groq'), true);
assert.strictEqual(sandbox.hookFit(rows[1], 'provider', 'openrouter'), true);
assert.strictEqual(sandbox.hookFit(rows[1], 'provider', 'groq'), false);
assert.strictEqual(sandbox.hookFit(rows[1], 'model', 'openrouter/gpt-4o'), true);
assert.strictEqual(sandbox.hookFit(rows[1], 'model', 'groq/llama'), false);
assert.strictEqual(sandbox.hookFit(rows[2], 'model', 'gpt-4o'), true);
assert.strictEqual(sandbox.hookFit(rows[2], 'model', 'gpt-4o-mini'), false);
assert.strictEqual(sandbox.hookFit(rows[2], 'provider', 'openai'), false);
assert.strictEqual(sandbox.hookFit({}, 'provider', 'groq'), true, 'no scope is global');
const parts = (value) => JSON.stringify(sandbox.sourceParts(value));
assert.strictEqual(parts('https://github.com/owner/name/tree/dev/hooks'),
  '{"repo":"owner/name","ref":"dev","path":"hooks"}');
assert.strictEqual(parts('owner/name'), '{"repo":"owner/name","ref":"main","path":"hooks"}');
assert.strictEqual(parts('https://github.com/owner/name'), '{"repo":"owner/name","ref":"main","path":"hooks"}');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_hook_page_rejects_value_out_of_range() -> None:
  """A page save checks each value against its field, so a wrong 1 names how to set it."""
  code = _app_js_vm(
    """
const row = { settings: [
  { key: 'chars', label: 'Answer characters', type: 'number', minimum: 100, maximum: 4000 },
  { key: 'mode', label: 'Mode', type: 'choice', choices: ['fast', 'slow'] },
  { key: 'target', label: 'Target', type: 'text' },
] };
assert.strictEqual(sandbox.hookPageProblem(row, { chars: 500, mode: 'fast', target: 'kilo' }), null);
assert.strictEqual(sandbox.hookPageProblem(row, { chars: 50, mode: 'fast' }).key, 'chars');
assert.strictEqual(sandbox.hookPageProblem(row, { chars: 50, mode: 'fast' }).text,
  'Set Answer characters to at least 100.');
assert.strictEqual(sandbox.hookPageProblem(row, { chars: 5000, mode: 'fast' }).text,
  'Set Answer characters to at most 4000.');
assert.strictEqual(sandbox.hookPageProblem(row, { chars: 500, mode: 'later' }).text,
  'Pick Mode from the list.');
assert.strictEqual(sandbox.hookPageProblem({ settings: [] }, {}), null, 'no field is no problem');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_hook_page_validates_rule_list() -> None:
  """A rule list checks each column, keeps unique sources, and draws add and remove controls."""
  code = _app_js_vm(
    """
const entry = { key: 'rules', label: 'Client rules', type: 'rules', columns: [
  { key: 'source', label: 'From', type: 'choice', choices: ['owui', 'kilo', 'opencode'], unique: true },
  { key: 'target', label: 'To', type: 'choice', choices: ['owui', 'kilo', 'opencode'] },
  { key: 'version', label: 'Version', type: 'text', default: 'latest', options: true },
] };
const row = { name: 'client_spoof.py', settings: [entry], values: { rules: [] } };
const good = [{ source: 'kilo', target: 'opencode', version: 'latest' }];
assert.strictEqual(sandbox.hookPageProblem(row, { rules: good }), null);
assert.strictEqual(sandbox.hookPageProblem(row, { rules: [{ source: 'curl', target: 'opencode', version: '1' }] }).text,
  'Pick From from the list.');
assert.strictEqual(sandbox.hookPageProblem(row, { rules: [
  { source: 'kilo', target: 'opencode', version: '1' },
  { source: 'kilo', target: 'owui', version: '1' },
] }).text, 'Each From must be unique.');
assert.strictEqual(sandbox.hookPageProblem(row, { rules: [{ source: 'kilo', target: 'opencode', version: '' }] }).text,
  'Set Version to a value.');
sandbox.setHookRuleCell('client_spoof.py', 'rules', 0, 'source', 'kilo');
sandbox.setHookRuleCell('client_spoof.py', 'rules', 0, 'target', 'opencode');
sandbox.setHookRuleCell('client_spoof.py', 'rules', 0, 'version', 'latest');
const markup = sandbox.hookRulesTable(row, entry);
assert.ok(markup.includes('data-hook-rule-add'), 'the table offers an add control');
assert.ok(markup.includes('data-hook-rule-remove'), 'the table offers a remove control');
assert.ok(markup.includes('<datalist'), 'the version column offers discovered choices in one picker');
assert.ok(markup.includes('<select'), 'the choice columns draw selects');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_redraw_needs_new_markup() -> None:
  """Shared draw code skips unchanged markup and waits for an active selection."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert re.search(
    r"function draw\(id, markup\) \{\n  const host = \$\(id\);\n"
    r"  if \(DRAWN\.get\(host\) === markup\) return;\n"
    r"  const selected = getSelection\(\);\n"
    r"  if \(!selected\.isCollapsed && host\.contains\(selected\.anchorNode\)\) return;",
    app,
  ), (
    "the draw helper keeps the DOM of an unchanged table, and waits for a live selection"
  )
  for host in (
    "ov-requests",
    "ov-models",
    "ov-limits",
    "models",
    "keys",
    "balances",
    "limit-rows",
    "pools",
  ):
    assert f'draw("{host}",' in app, host
    assert f'$("{host}").innerHTML =' not in app, f"{host} still redraws every time"


def test_app_js_draw_waits_for_live_selection() -> None:
  """An active selection delays markup replacement until the selection collapses."""
  code = _app_js_vm(
    """
const host = sandbox.$('models');
host.contains = () => true;
host.innerHTML = 'old';
sandbox.getSelection = () => ({ isCollapsed: false, anchorNode: 'node' });
sandbox.draw('models', 'new');
assert(host.innerHTML === 'old', 'a live selection holds the redraw back');
sandbox.getSelection = () => ({ isCollapsed: true });
sandbox.draw('models', 'new');
assert(host.innerHTML === 'new', 'the redraw lands once the selection is gone');
sandbox.draw('models', 'new');
assert(host.innerHTML === 'new', 'an unchanged table is not built again');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_nav_marks_current_page_for_screen_reader() -> None:
  """Route changes expose the active tab without changing focus or history."""
  run_app_js(
    "state, showPage, PAGES",
    """
sandbox.history = {
  replaceState: () => { throw new Error('a route change rewrote history'); },
  pushState: () => { throw new Error('a route change pushed history'); },
};
const focused = { id: 'focused-before' };
sandbox.document.activeElement = focused;
const links = Array.from(probe.PAGES, (page) => ({
  dataset: { page },
  attrs: {},
  scrolled: [],
  classList: { toggle: () => {} },
  setAttribute(name, value) { this.attrs[name] = value; },
  removeAttribute(name) { delete this.attrs[name]; },
  scrollIntoView(options) { this.scrolled.push(options); },
}));
sandbox.document.querySelectorAll = (sel) => (sel === '#nav a' ? links : []);
sandbox.URLSearchParams = URLSearchParams;
for (const page of probe.PAGES) {
  for (const link of links) link.scrolled.length = 0;
  sandbox.location.hash = `#/${page}`;
  probe.showPage();
  const active = links.filter((link) => link.dataset.page === page);
  assert.strictEqual(active.length, 1, 'one tab matches the route');
  const marked = links.filter((link) => link.attrs['aria-current'] === 'page');
  assert.deepStrictEqual(marked, active, 'only the matching tab carries aria-current');
  const scrolled = links.filter((link) => link.scrolled.length > 0);
  assert.deepStrictEqual(scrolled, active, 'only the matching tab scrolls into view');
  const [options] = active[0].scrolled;
  assert.strictEqual(options.block, 'nearest', 'the tab keeps its vertical place');
  assert.strictEqual(options.inline, 'nearest', 'the tab takes the short horizontal move');
}
assert.strictEqual(sandbox.document.activeElement, focused, 'focus stays put');
const last = probe.PAGES[probe.PAGES.length - 1];
assert.strictEqual(sandbox.location.hash, `#/${last}`, 'the hash stays as routed');
""",
  )


def test_app_js_requests_toolbar_narrows_table() -> None:
  """Request filtering changes row markup and counts by text, status, and age."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + "\\nglobalThis.__probe = {"
  + " state, renderRequestTable, requestMatches,"
  + " checkRequestsScroll, resetRequestsScroll, REQUESTS_PAGE, REQUESTS_STEP"
  + " };\\n";
const html = fs.readFileSync('daedalus/dashboard/ui/index.html', 'utf8');
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', value: '', textContent: '', hidden: false,
    listeners: {}, contains: () => false, addEventListener(type, handler) { this.listeners[type] = handler; },
    classList: { added: [], add(name) { this.added.push(name); }, remove(name) {}, toggle: () => {} } });
  return nodes.get(id);
};
let requestedUrls = [];
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node,
    querySelector: () => ({ firstChild: { textContent: 'R' } }), querySelectorAll: () => [],
    addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
  setTimeout: () => 0, clearTimeout: () => {},
  sessionStorage: { getItem: () => null }, localStorage: { getItem: () => null },
  fetch: async (url) => {
    requestedUrls.push(String(url));
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => [] };
  },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const probe = sandbox.__probe;
assert(!html.includes('id="requests-pager"'), 'index.html omits the requests pager');
assert(!html.includes('id="requests-prev"'), 'index.html omits the previous page button');
assert(!html.includes('id="requests-next"'), 'index.html omits the next page button');
assert(html.includes('id="requests-sentinel"'), 'index.html carries the scroll sentinel');
const now = Math.floor(Date.now() / 1000);
probe.state.requests = [
  { at: now, model: 'p/big', via: 'p/big', app: 'OWUI', session: 's1', status: 200 },
  { at: now, model: 'p/small', via: 'p/small', app: 'Kilo', session: 's2', status: 500 },
  { at: now - 7200, model: 'p/old', via: 'p/old', app: 'OWUI', session: 's3', status: 200 },
];
probe.state.requestFetched = 3;
const rows = () => (node('requests').innerHTML.match(/class="request/g) || []).length;
probe.renderRequestTable();
assert.strictEqual(rows(), 3, 'no filter shows every kept row');
assert.strictEqual(node('request-count').textContent, '', 'the top-right bar omits the total requests count');
assert.strictEqual(node('requests').classList.added.filter((n) => n === 'drawn').length, 1,
  'the first data of the table arrives once');
assert.strictEqual(node('request-count').classList.added.filter((n) => n === 'bump').length, 0,
  'no pulse without a change of the stream');
probe.state.live = new Map([[1, { id: 1 }]]);
probe.renderRequestTable();
assert.strictEqual(node('request-count').textContent, '1 in flight', 'the top-right hint reports in-flight requests');
assert(!node('requests').innerHTML.includes(LOCALE["request.empty.none_text"]), 'a live row keeps the empty row away');
probe.renderRequestTable();
probe.state.live.clear();
probe.state.requestSearch = 'kilo';
probe.renderRequestTable();
assert.strictEqual(rows(), 1, 'the text match reads the client');
assert(node('requests').innerHTML.includes('p/small'), 'the match keeps its row');
probe.state.requestSearch = '';
probe.state.requestStatus = 'ok';
probe.renderRequestTable();
assert.strictEqual(rows(), 2, '2xx only drops the 500');
probe.state.requestStatus = 'bad';
probe.renderRequestTable();
assert.strictEqual(rows(), 1, 'errors only keeps the 500');
probe.state.requestStatus = 'all';
probe.state.requestHours = 1;
probe.renderRequestTable();
assert.strictEqual(rows(), 2, 'the range drops the 2 hour old row');
probe.state.requestSearch = 'nothing at all';
probe.renderRequestTable();
assert(node('requests').innerHTML.includes(LOCALE["request.empty.filter_text"]), 'the empty table names the filter');

assert.strictEqual(probe.requestMatches({ status: 200, at: now }, { live: true }), false,
  'a live row follows the text too');
probe.state.requestSearch = '';
assert.strictEqual(probe.requestMatches({ status: 200, at: now }, { live: true }), true, 'a live row needs no status');
probe.state.requests = Array.from({ length: 13 }, (_, i) => ({
  at: now - i, model: `p/m${i + 1}`, via: `p/m${i + 1}`, app: 'OWUI', session: `s${i + 1}`, status: 200,
}));
probe.resetRequestsScroll();
probe.renderRequestTable();
assert.strictEqual(rows(), probe.REQUESTS_PAGE, 'initial slice renders REQUESTS_PAGE rows');
assert.strictEqual(probe.checkRequestsScroll({ scrollTop: 0, clientHeight: 200, scrollHeight: 600 }), false,
  'scrolling near the top keeps the initial slice');
assert.strictEqual(rows(), probe.REQUESTS_PAGE, 'slice stays unchanged above threshold');
assert.strictEqual(probe.checkRequestsScroll({ scrollTop: 320, clientHeight: 200, scrollHeight: 600 }), true,
  'scrolling near the bottom auto-loads the next slice');
assert.strictEqual(rows(), 13, 'auto-scroll reveals the remaining rows');
assert(node('requests').innerHTML.includes('p/m13'), 'oldest row renders without page buttons');

probe.state.requests = Array.from({ length: probe.REQUESTS_STEP }, (_, i) => ({
  at: now - i, model: `p/b${i + 1}`, via: `p/b${i + 1}`, app: 'OWUI', session: `b${i + 1}`, status: 200,
}));
probe.state.requestFetched = probe.REQUESTS_STEP;
probe.state.requestLimit = probe.REQUESTS_STEP;
probe.state.requestShown = probe.REQUESTS_STEP;
requestedUrls = [];
assert.strictEqual(probe.checkRequestsScroll({ scrollTop: 900, clientHeight: 200, scrollHeight: 1180 }), true,
  'scrolling past the fetched batch requests older server history');
assert.strictEqual(probe.state.requestLimit, probe.REQUESTS_STEP * 2,
  'requestLimit advances by REQUESTS_STEP');
assert(requestedUrls.some((u) => u.includes(`requests?limit=${probe.REQUESTS_STEP * 2}`)),
  'refreshFast fetches the expanded history limit');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_overview_leads_with_4_numbers() -> None:
  """Overview markup contains four metrics and five recent-request rows."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + "\\nglobalThis.__probe = { state, renderOverview, stamp };\\n";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', value: '', textContent: '', hidden: false,
    listeners: {}, contains: () => false, addEventListener(type, handler) { this.listeners[type] = handler; },
    classList: { added: [], add(name) { this.added.push(name); }, remove(name) {}, toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node,
    querySelector: () => ({ firstChild: { textContent: 'O' } }), querySelectorAll: () => [],
    addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
const assert = require('assert');
const probe = sandbox.__probe;
const now = Math.floor(Date.now() / 1000);
probe.state.requests = [{ at: now - 600, status: 200 }, { at: now, status: 500 },
  { at: now, status: 200 }, { at: now, status: 200 }, { at: now, status: 200 },
  { at: now, status: 200 }, { at: now, status: 200 }];
probe.state.live = new Map([[1, { id: 1 }], [2, { id: 2 }]]);
probe.state.models = [1, 2, 3];
probe.state.pools = [];
probe.state.limits = { providers: [], lanes: [{ rows: [{ limit: 100, remaining: 25 }] }] };
probe.renderOverview();
const kpis = node('ov-kpis').innerHTML;
assert.strictEqual((kpis.match(/class="kpi"/g) || []).length, 4, 'the strip holds 4 numbers');
assert(kpis.includes('<b>2</b><small>' + LOCALE["overview.kpi.in_flight_label"]
  + '</small>'), 'the strip counts the live requests');
assert(kpis.includes('<b>86%</b><small>' + LOCALE["overview.kpi.success_label"]
  + '</small>'), 'the strip names the success share');
assert(kpis.includes('<b>25%</b><small>' + LOCALE["overview.kpi.limit_left_label"]
  + '</small>'), 'the strip takes the lowest limit left');
assert(kpis.includes('<b>3</b><small>' + LOCALE["overview.kpi.catalog_models_label"]
  + '</small>'), 'the strip counts the catalog');
assert.strictEqual(LOCALE["overview.kpi.catalog_models_label"], "Models in catalog");
assert.strictEqual(LOCALE["overview.kpi.success_label"], "Success rate");
const overviewRequests = node('ov-requests').innerHTML;
const rows = (overviewRequests.match(/class="line"/g) || []).length;
assert.strictEqual(rows, 5, 'the request card holds 5 rows, not 10');
const exact = probe.stamp(now - 600);
assert(overviewRequests.includes(`title="${exact}">10 minutes ago</span>`),
  'the visible Overview time is relative and its hover keeps the exact stamp');
assert(!overviewRequests.includes(`>${exact}</span>`), 'the exact stamp is not visible');
probe.state.limits = null;
probe.state.requests = [];
probe.renderOverview();
assert(node('ov-kpis').innerHTML.includes('<b>-</b>'), 'an unknown share reads as a dash');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_tab_bar_fades_clipped_edges() -> None:
  """The tab bar uses one shared edge mask. The header has no chevron controls."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  fade = re.search(r"\n\.scroll-fade \{([^}]*)\}", css)
  assert fade and "overflow-x: auto;" in fade.group(1), (
    "the shared class owns its overflow"
  )
  assert "scroll-padding-inline: var(--edge-fade);" in fade.group(1)
  assert ".tabs.scroll-fade { padding-inline: 4px; }" in css
  assert "#nav," not in css, (
    "the strip joins the scrollers through the class, not an id"
  )
  assert (
    ".tab-step" not in css and "#nav-left" not in css and "#nav-right" not in css
  ), "no chevron control or slot remains in the css"
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'class="tabs scroll-fade" id="nav"' in html, "the strip wears the shared class"
  assert "nav-left" not in html and "nav-right" not in html, (
    "the header carries no step buttons"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "setFade" not in app and "markScrollEdges" not in app, (
    "no legacy JavaScript scroll-fade listener remains"
  )
  assert app.count('class="card-scroll scroll-fade"') == 3, (
    "the card scrollers wear the same class"
  )
  assert "tab-step" not in app and "nav-left" not in app, (
    "no chevron code remains in the app"
  )


def test_card_tables_own_horizontal_overflow_and_fade_only_clipped_edges() -> None:
  """Card-table code keeps controls outside scrolling, edge-masked wrappers."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  keys = app[app.index("function keysCard()") : app.index("function renderSettings()")]
  hooks = app[
    app.index("function hooksManager()") : app.index("async function dropHookFile")
  ]

  assert re.search(
    r'<div class="card-scroll scroll-fade">\s*<table class="keys">.*?</table>\s*</div>',
    keys,
    re.DOTALL,
  ), "the API Keys table scrolls without masking its form or reveal controls"
  assert keys.index('<form class="toolbar"') < keys.index(
    '<div class="card-scroll scroll-fade">'
  )
  table_end = hooks.index("</table></div>")
  update = hooks.index(
    'actionButton(`${iconSlot("refresh_cw")}<span>${t("action.update_sources_button")}</span>`',
    table_end,
  )
  assert (
    update > table_end and '"data-hooks-update": true' in hooks[update : update + 200]
  ), "the Hooks table scrolls without masking its Update control"
  card_scroll = re.search(r"\n\.scroll-fade \{([^}]*)\}", css)
  assert card_scroll and "overflow-x: auto;" in card_scroll.group(1), (
    "the table region, not the card or settings pane, owns horizontal overflow"
  )
  assert ".card-scroll.fade-left" not in css


def test_wide_section_pane_fades_only_clipped_vertical_edges() -> None:
  """Pane code toggles fit-host cleanly while CSS scroll timelines handle vertical masks."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  wide = re.search(r"@media not \(max-width: 60em\) \{(.*?)\n\}", css, re.DOTALL)
  assert wide, "the wide section layout"
  pane = re.search(r"\.section-pane \{([^}]*)\}", wide.group(1))
  assert pane and "overflow-y: auto;" in pane.group(1)
  assert "padding: 4px;" in pane.group(
    1
  ) and "scroll-padding-block: var(--edge-fade);" in pane.group(1)
  assert ".section-pane.fade-top" not in css and "#nav.fade-left" not in css
  assert "function markPaneEdges(pane)" in app
  assert "--fade-top" not in app and "--fade-top" not in css

  run_app_js(
    "markPaneEdges",
    """
let fitHost = false;
const pane = {
  classList: { toggle: (name, on) => { if (name === 'fit-host') fitHost = on; } },
  querySelector: () => ({}),
};
probe.markPaneEdges(pane);
assert.strictEqual(fitHost, true, 'a filling child marks fit-host');
pane.querySelector = () => null;
probe.markPaneEdges(pane);
assert.strictEqual(fitHost, false, 'normal cards clear fit-host');
""",
  )


def test_app_js_fit_section_pane_spares_page_padding() -> None:
  """Pane-height calculation subtracts page padding, pane offset, and the gap."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { fitSectionPane };";
const pane = {
  offsetParent: {}, style: { setProperty() {} }, scrollTop: 0, clientHeight: 200, scrollHeight: 600,
  classList: { toggle: () => {} }, getBoundingClientRect: () => ({ top: 128 }),
  querySelector: () => null,
};
const main = { clientHeight: 851, getBoundingClientRect: () => ({ top: 49 }) };
const bare = () => ({
  addEventListener: () => {}, classList: { add: () => {}, remove: () => {}, toggle: () => {} },
  style: { setProperty() {} }, dataset: {},
  children: [], scrollWidth: 0, clientWidth: 0, scrollLeft: 0, querySelectorAll: () => [],
});
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  innerHeight: 900,
  getComputedStyle: () => ({ paddingBottom: '28px' }),
  document: {
    addEventListener: () => {},
    documentElement: { dataset: {} },
    getElementById: bare,
    querySelector: (sel) => (sel === 'main' ? main
      : sel.startsWith('#nav a') ? { firstChild: { textContent: 'Providers' } } : null),
    querySelectorAll: (sel) => (sel === '.section-pane' ? [pane] : []),
  },
  window: { addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, getSelection: () => ({ isCollapsed: true }),
  console: { error: () => {} },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
sandbox.__probe.fitSectionPane();
// 49 + 851 - 28 padding - 128 pane top - 16 gap: the pane ends inside the scrollport.
assert.strictEqual(pane.style.maxHeight, '728px', 'the cap spares the page padding: ' + pane.style.maxHeight);
"""
  subprocess.run(["node", "-e", code], check=True)


def test_every_note_of_write_is_live_region() -> None:
  """Write-message markup includes status or alert roles."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  for name, role in (
    ("limits-message", "status"),
    ("save-message", "status"),
    ("settings-message", "status"),
    ("login-message", "alert"),
    ("notice", "alert"),
  ):
    tag = re.search(rf"<[^>]*id=\"{name}\"[^>]*>", page)
    assert tag and f'role="{role}"' in tag.group(0), (name, tag and tag.group(0))
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'id="key-message" role="alert"' in app, "the key note is a live region too"
  assert 'id="reset-message" role="status"' in app, (
    "the reset note rides in the rendered maintenance card"
  )


def test_failed_read_shows_line_in_page() -> None:
  """Failure-handling code preserves notices across reads and retries until explicit dismissal."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert re.search(
    r"async function guarded\(task\) \{\n  try \{\n    await task\(\);\n  \} catch \(error\) \{",
    app,
  ), "a good read keeps the failure line"
  assert "await task();\n    clearNotice();" not in app, (
    "the failure line only goes on the x, never on a read"
  )
  assert "showNotice(error.message);\n      console.error(error);" in app, (
    "a failed read shows its message"
  )
  assert '$("notice-retry").addEventListener("click", () => {' in app, (
    "the retry button"
  )
  assert (
    '$("notice-retry").addEventListener("click", () => {\n  clearNotice();' not in app
  ), "the retry asks again but keeps the line in place"
  assert (
    '$("notice-close").addEventListener("click", () => {\n  closeNotice();\n});' in app
  ), "the x clears the line"
  assert "alert(" not in app, "no native dialog is left"


def test_undo_never_moves_row() -> None:
  """Undo CSS places the text button beside controls without adding left padding."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert ".field > .undo-text { margin-left: auto; margin-right: 8px; }" in css, (
    "the button rides in the free space of the row, before the control"
  )
  assert (
    ".field.stack > .undo-text { position: absolute; right: 0; top: 10px; }" in css
  ), "a row of a column holds the button on the label line"
  assert ".field:has(> .undo-text) { padding-left: 34px; }" not in css, (
    "the row keeps its width in place of a shift"
  )
  assert ".undo-step" not in css, "the round icon leaves the sheet"
  app = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert 'button.textContent = t("action.undo_write_label");' in app, (
    "the Undo names its action in words"
  )
  assert 'button.innerHTML = icon("undo_2");' not in app, "no icon-only Undo remains"


def test_app_js_places_undo_beside_control() -> None:
  """Provider and settings writes place Undo and save status in the changed row."""
  code = _app_js_vm(
    """
vm.runInContext('globalThis.__probe.rememberWrite = rememberWrite; globalThis.__probe.placeUndo = placeUndo;'
  + ' globalThis.__probe.lastWrite = () => lastWrite; globalThis.__probe.dropUndo = dropUndo;', sandbox);
let placed = '';
let inserted = null;
const control = { id: 'set-catalog-every', dataset: { set: 'catalog.every' }, isConnected: true,
  matches: (sel) => sel === '[data-set]',
  closest: (sel) => (sel === '[data-section]' ? { dataset: { section: 'catalog' } } : row),
  querySelector: () => null };
const row = {
  children: [control],
  insertBefore: (node, before) => { inserted = node; placed = 'insertBefore ' + (before === control); },
  prepend: () => { placed = 'prepend'; },
  closest: () => row,
  querySelector: (sel) => (sel === '.save-line' ? null : control),
};
sandbox.document.querySelector = () => null;
sandbox.document.getElementById = () => ({ closest: () => null,
  classList: { add: () => {}, remove: () => {} } });
sandbox.document.createElement = (tag) => {
  const made = { tag, children: [], className: '', addEventListener: () => {},
    remove: () => {}, setAttribute: () => {},
    querySelector: (sel) => (sel === '.saved' ? made.children[0] : null),
    append: (...kids) => made.children.push(...kids) };
  return made;
};
sandbox.__probe.rememberWrite('providers', { path: 'p', text: 'a' }, control);
assert.strictEqual(placed, 'insertBefore true', 'the provider Undo joins the row before its control: ' + placed);
assert.strictEqual(sandbox.__probe.lastWrite().key, 'catalog.every', 'the key of the row');
sandbox.__probe.dropUndo();
placed = '';
inserted = null;
sandbox.__probe.rememberWrite('settings', { settings: true, text: 'b' }, control);
assert.strictEqual(placed, 'insertBefore true', 'the settings write reports in the changed row');
assert.strictEqual(inserted.className, 'save-line', 'the row takes the saved line');
assert.strictEqual(inserted.children[1], sandbox.__probe.lastWrite().button,
  'the Undo rides with the line');
let dropped = 0;
const gone = { remove: () => { dropped += 1; } };
sandbox.document.querySelector = (sel) => (sel === '.undo-text' || sel === '.save-line' ? gone : null);
sandbox.__probe.dropUndo();
assert.strictEqual(dropped, 2, 'the Undo control and its line leave the page');
assert.strictEqual(sandbox.__probe.lastWrite(), null, 'no write waits a way back');
sandbox.setInterval = () => 0;
sandbox.clearInterval = () => {};
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_clears_undo_when_change_lands_back() -> None:
  """A clean settings state removes Undo without sending a request."""
  code = _app_js_vm(
    """
vm.runInContext('globalThis.__probe.saveSettings = saveSettings;', sandbox);
vm.runInContext('settingsDirty = () => false;', sandbox);
const store = { getItem: () => null, removeItem: () => {}, setItem: () => {} };
sandbox.sessionStorage = store;
sandbox.localStorage = store;
let removed = 0;
let sent = 0;
sandbox.fetch = () => {
  sent += 1;
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
};
sandbox.setInterval = () => 0;
sandbox.clearInterval = () => {};
sandbox.document.querySelector = (sel) =>
  (sel === '.undo-text' || sel === '.save-line' ? { remove: () => { removed += 1; } } : null);
(async () => {
  await sandbox.__probe.saveSettings();
  assert.strictEqual(removed, 2, 'the Undo of the write and its line go');
  assert.strictEqual(sent, 0, 'a change back to the saved value writes nothing');
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_hook_delete_keeps_title_out_of_request() -> None:
  """Hook deletion uses file path and keeps title in dialog."""
  code = _app_js_vm(
    """
sandbox.setTimeout = () => 0;
sandbox.clearTimeout = () => {};
const store = { getItem: () => null, removeItem: () => {}, setItem: () => {} };
sandbox.sessionStorage = store;
sandbox.localStorage = store;
const sent = [], dialogs = [];
let accepted = false, loaded = 0;
sandbox.ask = async (...args) => { dialogs.push(args); return accepted; };
sandbox.loadSettings = async () => { loaded += 1; };
sandbox.fetch = async (url, options) => {
  sent.push({ url, method: options.method, body: JSON.parse(options.body) });
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
(async () => {
  for (const title of ['Display title', '', 'hooks/other.py']) {
    const path = 'hooks/owner/repo/one.py';
    const button = { dataset: { hookDrop: path, hookTitle: title } };
    accepted = false;
    const before = sent.length;
    await sandbox.dropHookFile(button);
    assert.strictEqual(sent.length, before, 'cancel sends nothing');
    assert.strictEqual(loaded, before, 'cancel does not reload settings');
    accepted = true;
    await sandbox.dropHookFile(button);
    assert.deepStrictEqual(sent.at(-1), {
      url: 'ui/api/hooks/file', method: 'DELETE', body: { name: path },
    });
    assert.strictEqual(dialogs.at(-1)[1],
      LOCALE['dialog.delete_hook.message'].replaceAll('{name}', title || path));
    assert.strictEqual(loaded, sent.length, 'success reloads settings');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_names_failure_with_no_message() -> None:
  """Request errors use server messages, HTTP status, or a localized network-failure message."""
  code = _app_js_vm(
    """
sandbox.setTimeout = () => 0;
sandbox.clearTimeout = () => {};
const store = { getItem: () => null, removeItem: () => {}, setItem: () => {} };
sandbox.sessionStorage = store;
sandbox.localStorage = store;
vm.runInContext('globalThis.__probe.call = call;', sandbox);
const answer = (status, body) => ({ ok: status < 400, status, json: () => Promise.resolve(body) });
(async () => {
  sandbox.fetch = () => Promise.resolve(answer(500, {}));
  const bare = await sandbox.__probe.call('catalog').then(() => '', (error) => error.message);
  assert.strictEqual(bare, LOCALE["error.http_without_message"].replace("{status}", "500"), bare);
  sandbox.fetch = () => Promise.resolve(answer(409, { error: { message: 'The file could not be deleted.' } }));
  const said = await sandbox.__probe.call('hooks/file').then(() => '', (error) => error.message);
  assert.strictEqual(said, 'The file could not be deleted.', said);
  sandbox.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
  const offline = await sandbox.__probe.call('catalog').then(() => '', (error) => error.message);
  assert.strictEqual(offline, LOCALE["error.dashboard_unreachable_text"], offline);
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_requests_rows_show_relative_time() -> None:
  """Time-cell markup contains relative text and an exact timestamp title."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'const cell = (label, inner, cls = "", attrs = "") =>' in app, (
    "the cell takes attributes"
  )
  run_app_js(
    "renderRequests, relative, stamp, esc",
    r"""
const sample = JSON.parse(fs.readFileSync('scripts/pages_fixtures.json', 'utf8')).requests
  .find(item => item.attempts?.length > 1);
let now = Date.now();
const realNow = Date.now;
Date.now = () => now;
try {
  const row = {...sample, at: now / 1000 - 30};
  probe.renderRequests([row]);
  const timeCell = () => node('requests').innerHTML.match(/<td\b[^>]*title="([^"]*)"[^>]*>([\s\S]*?)<\/td>/);
  assert(timeCell());
  assert.strictEqual(timeCell()[1], probe.esc(probe.stamp(row.at)));
  assert(timeCell()[2].includes(probe.esc(probe.relative(row.at))));
  now += 180000;
  probe.renderRequests([row]);
  assert(timeCell()[2].includes(probe.esc(probe.relative(row.at))),
    'the relative time updates when the clock moves while rows stay unchanged');
} finally {
  Date.now = realNow;
}
""",
  )


def test_requests_table_has_live_switch() -> None:
  """Live-switch code tracks waiting rows and guards paused rendering and clocks."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'id="live-toggle"' in page and 'aria-pressed="false"' in page, page[
    page.index("live-toggle") - 60 :
  ][:120]
  assert "function renderLiveToggle()" in app and "state.liveWaiting.add(id)" in app, (
    "the button draws its state and the held rows"
  )
  assert "function tickLive() {\n  if (state.livePaused) return;" in app, (
    "a paused table holds still"
  )
  assert "function renderLive() {\n  if (state.livePaused) return;" in app, (
    "a paused table redraws no row"
  )


def test_limits_filter_rides_in_hash() -> None:
  """Limit-filter source includes hash parsing, hash updates, and clear-button markup."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'id="limits-search"' in page and 'id="limits-clear"' in page, page
  check = page.split('id="limits-check"')[1].split("</button>")[0]
  assert 'data-icon="refresh_cw"' in check, (
    "the check button hydrates the lucide refresh glyph from ICON_PATHS"
  )
  assert '<span data-i18n="action.check_now_button">' in check, (
    "the word stays for a screen reader, and the locale fill keeps the glyph"
  )
  assert 'id="request-clear"' in page, "the Requests bar holds a Clear filters button"
  assert (
    'if (asked === "limits" && query !== undefined) applyLimitFilters(query);' in app
  )
  assert re.search(
    r"function limitsHash\(\) \{[\s\S]*?history\.replaceState\(null, \"\", `#/limits",
    app,
  ), "the Limits filter writes its own hash"
  assert (
    "function shownLimits()" in app
    and app.count("renderLimitRows(shownLimits());") == 4
  )
  assert (
    '$("request-clear").classList.toggle("off",\n    !state.requestSearch'
    ' && state.requestStatus === "all" && !state.requestHours);' in app
  ), "the Clear filters button shows only with a live filter"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    'section[data-page="requests"] .requests-bar .hint { margin-left: auto;'
    " white-space: nowrap; }" in css
  ), "the hint pins right without reserving dead Clear filters width"
  assert (
    'section[data-page="requests"] .requests-bar #request-clear.off { display:'
    " none; }" in css
  )


def test_requests_filters_ride_in_hash() -> None:
  """Request-filter code reads queries and updates the hash."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert (
    'if (asked === "requests" && query !== undefined) applyRequestFilters(query);'
    in app
  )
  assert re.search(
    r"function requestHash\(\) \{[\s\S]*?history\.replaceState\(null, \"\", `#/requests",
    app,
  ), "the filters write their own hash"
  assert app.count("  requestHash();\n") == 4, app.count("  requestHash();\n")


def test_phone_model_cards_carry_desktop_columns() -> None:
  """Phone model code includes context, reasoning, tier, and order chips without a Model label."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  chips = app.split("function phoneChips(m) {", 1)[1].split("\n}", 1)[0]
  assert "nameCell(m.id, actionButton(modelName(m.id), {" in app
  assert '}), "", `<span class="types phone-types">' in app, (
    "the card drops the Model label"
  )
  assert 'title="${t("model.column.context_column")}"' in chips, (
    "the context column rides as a chip"
  )
  assert 'title="${t("model.column.reasoning_column")}"' in chips, (
    "the reasoning column rides as a chip"
  )
  assert "model.column.weight_column" not in chips, (
    "the weight row carries the weight alone"
  )
  assert (
    't("model.mobile.tier_text"' in chips and 't("model.mobile.order_text"' in chips
  ), "the ambiguous chips carry localized labels"
  assert "brain:" in app, "the reasoning chip is a brain"


def test_page_carries_motion_base() -> None:
  """Shared motion CSS defines duration tokens, easing, and reduced-motion rules."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "--fast:" in css and "--soft:" in css and "--ease:" in css, "the motion tokens"
  assert "animation: pulse 1.4s var(--ease) infinite" in css, (
    "the live dot uses the nuu 1.4 s pulse"
  )
  assert "a, button, .snippet, .card, .chip, .pill, tr.request td {" in css, (
    "the controls and the rows glide between their states"
  )
  assert "transition: background-color var(--" in css, "the switch reads the token"
  assert ".field, .fold { transition: opacity var(--fast) var(--ease); }" in css, (
    "a settings row fades between enabled and disabled"
  )
  assert ".field input:not(.switch), .field select {" in css, (
    "the controls inside the row keep the same beat"
  )
  assert "replay(arrived)" in app, "a desktop section change fades the arriving card in"
  assert 'saveSettings(input.type === "checkbox");' in app, (
    "a saved switch keeps its node, so the knob finishes its slide"
  )
  assert "await loadSettings({ render: !keepForm });" in app, (
    "the switch save reloads the data without redrawing the form"
  )
  block = css.split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("\n}", 1)[
    0
  ]
  for rule in ("transition: none", "animation: none"):
    assert rule in block, rule
  assert "tweenFromTo(section, { opacity: 0, y: 4 }, { opacity: 1, y: 0 });" in app
  assert 'classList.add("enter")' in app and 'classList.remove("enter")' in app, (
    "the shown section replays the enter"
  )


def test_live_row_arrives_and_stream_count_pulses() -> None:
  """Live-row source tracks arrivals and count changes with GSAP motion hooks."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "tweenFromTo(row, { opacity: 0, y: -4 }, { opacity: 1, y: 0 });" in app
  assert "tweenFromTo(hint, { opacity: 0.45 }, { opacity: 1 }" in app
  assert "liveSeen" in app and "liveCount" in app, (
    "the rows already drawn, and the last count"
  )
  assert '? "" : " arrive"' in app, "the class only on a new row"
  assert 'classList.add("bump")' in app and 'classList.remove("bump")' in app, (
    "the pulse retrigger"
  )


def test_first_data_of_card_arrives_once() -> None:
  """First-draw source tracks marked hosts and triggers GSAP arrival motion."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "function animateArrival(host)" in app, "the GSAP enter of the first data"
  assert "const DRAWN_SHOWN = new Set();" in app, "the hosts that already arrived"
  assert "DRAWN_SHOWN.has(host)" in app and "DRAWN_SHOWN.add(host)" in app, (
    "the once mark"
  )
  assert "firstDraw(host);" in app, "the shared draw path marks the host"
  assert 'firstDraw($("requests"));' in app, "the requests table marks itself too"


def test_app_js_pool_selection_moves_models_table_only() -> None:
  """Model-filter hash changes add table animation without adding page-entry classes."""
  code = _app_js_vm(
    """
sandbox.URLSearchParams = URLSearchParams;
sandbox.history = { replaceState: () => {} };
sandbox.__el("sort-small").options = [];
const sections = ["overview", "models"].map((page) => ({
  dataset: { page }, hidden: true, offsetWidth: 0,
  classList: {
    added: [], add(name) { this.added.push(name); },
    remove(name) { this.added = this.added.filter((item) => item !== name); },
    toggle() {},
  },
}));
sandbox.document.querySelectorAll = (sel) => (sel === "section[data-page]" ? sections : []);
const models = sandbox.__el("models");
const drawn = [];
models.classList = { add: (name) => drawn.push(name), remove: () => {}, toggle: () => {} };
sandbox.location.hash = "#/models";
sandbox.showPage();
sandbox.location.hash = "#/models?tier=C&mode=chat&sort=weight";
sandbox.showPage();
const on = sections.find((row) => row.dataset.page === "models");
assert.strictEqual(on.classList.added.includes("enter"), false, "the page holds still");
assert.strictEqual(drawn.includes("drawn"), true, "the models table arrives");
process.exit(0);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_pending_call_spins_and_first_load_waits_on_skeletons() -> None:
  """Loading source renders the nuu delayed header edge bar and fixed-slot button spin."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  brand = next(line for line in page.splitlines() if 'class="brand"' in line)
  assert "spinner" not in brand and 'id="spinner"' not in page
  assert "nuu-edge-loading" in css and "nuu-edge-loading-done" in css
  assert "tr.skeleton" not in css and "function skeletons(" not in app
  assert "@keyframes spin" not in css and "@keyframes nuu-spin" in css
  assert 'classList.add("checking")' not in app
  assert (
    'setAttribute?.("aria-busy", "true")' in app
    and 'setAttribute?.("aria-busy", "false")' in app
  )
  assert 'active.dataset.loading = "true"' in app
  assert 'region.dataset.loading = "done"' in app


def test_page_memory_version_cache_client_id_and_draft_persistence() -> None:
  """Page memory caches versioned tabs, deduplicates clientId retries, and persists drafts."""
  code = _app_js_vm(
    r"""
const storeMap = new Map();
sandbox.sessionStorage = { getItem: () => null };
sandbox.localStorage = {
  getItem: (k) => (storeMap.has(k) ? storeMap.get(k) : null),
  setItem: (k, v) => storeMap.set(k, String(v)),
  removeItem: (k) => storeMap.delete(k),
};
sandbox.crypto = { randomUUID: () => "uuid-fixed-1" };
const probe = vm.runInContext(
  "({ state, readPageCache, submitApiKey, readDraft, writeDraft })",
  sandbox,
);
(async () => {
  let loads = 0;
  const first = await probe.readPageCache("models", 1700000000, async () => {
    loads += 1;
    return [{ id: "demo/m1" }];
  });
  const second = await probe.readPageCache("models", 1700000000, async () => {
    loads += 1;
    return [{ id: "demo/m2" }];
  });
  assert.strictEqual(loads, 1, "matching catalog version hits cache");
  assert.strictEqual(second[0].id, first[0].id);
  await probe.readPageCache("models", 1700000000, async () => {
    loads += 1;
    return [{ id: "demo/m2" }];
  }, true);
  assert.strictEqual(loads, 2, "fresh reload bypasses cache");
  await probe.readPageCache("models", 1700000005, async () => {
    loads += 1;
    return [{ id: "demo/m3" }];
  });
  assert.strictEqual(loads, 3, "new catalog version misses stale cache");

  probe.writeDraft("keyName", "laptop");
  assert.strictEqual(probe.readDraft("keyName"), "laptop");

  let postBodies = [];
  sandbox.fetch = async (url, init = {}) => {
    postBodies.push(JSON.parse(init.body));
    return {
      ok: true,
      status: 200,
      json: async () => ({ name: "laptop", key: "sk-test" }),
    };
  };
  probe.state.keys = [{ clientId: "uuid-fixed-1", name: "laptop", status: "failed" }];
  await probe.submitApiKey("laptop", "uuid-fixed-1");
  assert.strictEqual(probe.state.keys.length, 1, "retrying same clientId never duplicates row");
  assert.strictEqual(probe.state.keys[0].status, "ok");
  assert.strictEqual(postBodies[0].clientId, "uuid-fixed-1");
  assert.strictEqual(probe.readDraft("keyName"), undefined, "successful submit clears draft");
})().catch((err) => { console.error(err); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_shared_back_button_uses_lucide_chevron_left() -> None:
  """All drill-down back controls render through one backButton helper with chevron-left."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "function backButton(" in app
  assert (
    "lsaquo" not in app and "ghost hook-back" not in app and ".hook-back" not in css
  )


def test_shared_add_button_uses_lucide_plus() -> None:
  """All inline add buttons render through one addButton helper with Lucide plus."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  locale = Path("daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert "function addButton(" in app
  assert app.count('variant: "add"') == 1
  assert '"+ Add' not in locale and '"+ New' not in locale
  run_app_js(
    "addButton",
    """
const html = probe.addButton("Add rule", { "data-hook-rule-add": "[1]" });
assert(html.includes('class="add"'), html);
assert(html.includes("M5 12h14") && html.includes("<span>Add rule</span>"), html);
""",
  )


def test_shared_info_hint_and_pane_bar() -> None:
  """Field labels and folds share infoHint(), and drill-down headers share paneBar()."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert "function infoHint(" in app and "function paneBar(" in app
  assert "hook-bar" not in app and ".hook-bar" not in css and ".hook-heading" not in css
  assert ".field.info .hint, .fold summary .hint {" in css


def test_shared_static_icons_and_modal_value_input() -> None:
  """Static index.html buttons hydrate from ICON_PATHS and modal inputs share modalValueInput()."""
  page = Path("daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert page.count('data-icon="x"') == 2
  assert 'data-icon="rotate_cw"' in page
  assert 'data-icon="activity"' in page
  assert 'data-icon="refresh_cw"' in page
  assert app.count("modalValueInput({") >= 4
  run_app_js(
    "hydrateIcons, modalValueInput",
    """
const target = { innerHTML: "", getAttribute: (k) => (k === "data-icon" ? "x" : null) };
probe.hydrateIcons({ querySelectorAll: () => [target] });
assert(target.innerHTML.includes("M18 6 6 18"), target.innerHTML);
const input = probe.modalValueInput({ id: "demo", label: "Folder", value: "hooks", placeholder: "hooks" });
assert(input.includes('aria-haspopup="dialog" aria-controls="modal"') && input.includes('id="demo"'), input);
""",
  )
  run_app_js(
    "backButton, icon",
    """
const rendered = probe.backButton('Settings', { 'data-back': true });
assert(rendered.includes('class="ghost back"'));
assert(rendered.includes('data-back'));
assert(rendered.includes(probe.icon('chevron_left')));
assert(rendered.includes('<span class="back-label">Settings</span>'));
""",
  )


def test_phone_keeps_numbers_and_cards() -> None:
  """Phone CSS defines metric and card layouts, and tab code replaces history entries."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  phone = css.split("@media (max-width: 30em) {", 1)[1]
  for rule in (
    ".kpis { grid-template-columns: 1fr 1fr;",
    ".models tr > td.name { grid-column: 1 / -1; max-width: none; }",
    ".models tr > td.hide-sm { display: none; }",
    ".keys tr > td.name { grid-column: 1; grid-row: 1; max-width: none; }",
    (
      ".keys tr > td { display: flex; align-items: baseline; gap: 5px; min-width: 0;"
      " padding: 0; border: 0; width: auto; }"
    ),
    ".keys tr > td:nth-child(4) { grid-column: 1; grid-row: 2; align-items: center; }",
    "#new-key { padding: 3px 3px 8px; }",
    'section[data-page="models"] .toolbar {',
    "border: 1px solid var(--line); border-radius: var(--radius); box-shadow: none;",
    ".notification-item { align-items: start; min-height: 44px; padding: 10px 14px; }",
    ".toolbar input:focus-visible, .toolbar select:focus-visible,",
  ):
    assert rule in phone, rule
  assert ".sections button.on" in css and ".section-pane {" in css, (
    "the Settings page holds a section list and the pane"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'history.replaceState(null, "", link.getAttribute("href"));' in app, (
    "a tab reuses the entry, so the phone back leaves the app"
  )


def test_each_page_holds_section_list() -> None:
  """Settings and Provider source includes a section rail, detail pane, and back controls."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function sectionList(",
    "function selectSection(",
    "selectSettingsSection(section.dataset.section)",
    "selectProviderSection(section.dataset.section)",
    'class="section-pane"',
    'variant: "ghost back"',
    "function yamlCard(editor, save, hint) {",
    'data-section="yaml"',
  ):
    assert needle in app, needle
  assert "const CARD_WIDTH" not in app, "the hand-balanced columns are gone"
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  for needle in (
    ".sections button.on",
    ".section-pane {",
    '.settings[data-detail="1"] > .section-pane',
    ".sections button { min-height: 44px; padding: 10px 12px 10px 8px; gap: 10px; }",
  ):
    assert needle in style, needle


def test_notice_leaves_on_own_keyframes() -> None:
  """Notice-dismissal source uses GSAP exit tweening, a labeled button, and reduced-motion bypass."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  close = re.search(r'<button[^>]*id="notice-close"[^>]*>', page)
  assert close and 'aria-label="notice.dismiss_label"' in close.group(0), (
    "the x has a localized dismiss label for a screen reader"
  )
  assert 'data-i18n-aria-label="notice.dismiss_label"' in close.group(0)
  body = page.split('id="notice-close"')[1].split("</button>")[0]
  assert 'data-icon="x"' in body, "the x hydrates the lucide glyph from ICON_PATHS"
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'note.classList.add("leave");' in app
  assert 'clearProps: "opacity,transform"' in app
  assert "onComplete: () => {" in app


def test_model_modal_reads_as_card() -> None:
  """Model-modal source includes identity fields, labeled capability chips, and responsive stat CSS."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '<dl class="model-detail">' not in app, "the table dump is gone"
  assert "const { provider, dev, model } = parts(m.id);" in app, (
    "the card splits the id into its parts"
  )
  assert '[t("model.column.model_column"), model || m.slug || m.id' in app, (
    "the model leads"
  )
  assert '[t("model.detail.developer_label"), dev, markImg(dev)]' in app, (
    "the developer rides when present"
  )
  assert '[t("model.detail.provider_label"), provider,' in app, (
    "the provider closes the identity"
  )
  assert 'const markImg = (name) => (MARK_FILES.has(name) ? mark(name) : "");' in app, (
    "the lobe marks ride beside the parts that own one"
  )
  assert "CHIP_ICON_NAMES.tools" in app and "CHIP_ICON_NAMES.brain" in app, (
    "the capabilities read as icon chips"
  )
  modal = app.split("function openModelModal")[1].split("\nfunction ")[0]
  assert (
    '${icon(CHIP_ICON_NAMES.brain)} ${t("model.column.reasoning_column")}</span>'
    in modal
  ), "the reasoning chip draws the brain and the word together"
  assert (
    '${icon(CHIP_ICON_NAMES.tools)} ${t("model.column.tools_column")}</span>' in modal
  ), "the tools chip draws the wrench and the word together"
  assert (
    '${FLAG_ICON_NAMES[f] ? `${icon(FLAG_ICON_NAMES[f])} ` : ""}${esc(FLAGS[f] || f)}'
    in modal
  ), "the modality chips draw their icon and their label together"
  assert "reasoningCell" not in modal, "no bare Yes rides in the modal"
  assert "progressMeter(m.weight)" in app, "the weight draws its shared meter"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".model-detail" not in css, "the dump rule is gone"
  assert ".model-id {" in css and ".model-stats {" in css and ".model-chips {" in css, (
    "the card keeps its blocks"
  )
  assert (
    '[m.max_input_tokens.toLocaleString(), t("model.detail.input_tokens_label")] : null,'
    in app
  ), "the token stats keep no bar"
  assert ".model-stats .track { margin-top: auto; }" in css, (
    "the bar pins to the floor of its block"
  )
  assert "min-height: 60px;" in css, "a floor holds the blocks at one height"
  assert "grid-template-columns: repeat(3, 1fr);" in css, "the blocks ride one row"
  phone_stats = css.split("@media (max-width: 30em) {", 1)[1].split("\n}", 1)[0]
  assert ".model-stats { grid-template-columns: 1fr; }" in phone_stats, (
    "a narrow page folds them to one column, so no wrap leaves an orphan"
  )
  assert "width: min(560px, calc(100vw - 32px));" in css, (
    "the modal keeps one width on every screen"
  )
  assert ".model-id .value { min-width: 0; overflow-wrap: anywhere; }" in css, (
    "the identity values read in the regular face"
  )


def test_providers_page_folds_long_groups() -> None:
  """Provider source uses closed detail groups with tooltip markup and handlers."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '${fold(t("provider.tiers.label")' in app
  assert '${fold(t("provider.model_overrides.label")' in app, "the fold groups"
  assert (
    "function fold(label, hint, body) {" in app and '<details class="fold">' in app
  ), "a closed group wraps its fields"
  assert 'variant: "hint"' in app
  assert '"aria-label": t("common.hint")' in app, (
    "the field hint rides behind the info icon"
  )
  assert 'role="tooltip"' in app, "the hint text serves as the tooltip"
  assert 'closest(".fold summary .hint")' in app, (
    "a click on the hint of a group never opens the group"
  )
  assert '<summary aria-describedby="${id}">' in app, "the summary names its tooltip"
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".settings > .column {" not in style and "column-count" not in style, (
    "no page splits a card across a column"
  )
  assert ".fold summary .hint:hover ~ small" in style, (
    "the hint of a group shows its tooltip on hover and on focus"
  )


def test_information_hovers_open_above_without_growing_settings_pane() -> None:
  """Information-tooltip CSS positions hints above their triggers outside normal layout."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  starts = (".field.info .hint:hover ~ small", ".fold summary .hint:hover ~ small")
  for start in starts:
    found = re.search(re.escape(start) + r"[^{}]*\{([^}]*)\}", style)
    assert found, start
    rule = found.group(1)
    assert "position: absolute;" in rule, "the tooltip stays outside normal layout"
    assert "top: auto;" in rule and "bottom: calc(100% + 4px);" in rule, (
      "every information tooltip opens above its trigger"
    )
  assert (
    ".floating-info-tip {" in style
    and "position: fixed; top: auto; z-index: 95; pointer-events: none;" in style
    and "function placeInfoTip(" in app
  ), (
    "floating body-level tooltip escapes masked scroll-pane clipping while opening above the icon"
  )
  assert "small.up" not in style and "hintFlip" not in app, (
    "the tooltip never opens below before a script flips it"
  )


def test_limits_poll_keeps_out_of_address_bar() -> None:
  """Limit hash updates require the Limits route, while clear-button state follows the filter."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'if (!location.hash.startsWith("#/limits")) return;' in app, (
    "the filter writes the hash on the Limits page alone"
  )
  assert '$("limits-clear").hidden = !state.limitSearch;' in app, (
    "the clear button follows the filter, and not the hash write"
  )


def test_yaml_editor_ends_each_list_and_saves_itself() -> None:
  """Section-list code ends with YAML and binds separate provider and settings save handlers."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function yamlCard(editor, save, hint) {",
    'const items = names.map((name) => [name, name]).concat([["yaml", t("yaml.label")]]);',
    '["keys", t("settings.keys.title")], ["yaml", t("yaml.label")],',
    'if (event.target.closest("#yaml-save")) return saveYaml();',
    'if (event.target.closest("#settings-yaml-save")) saveSettings();',
    'const typed = state.view === "yaml" && sameFile ? $("editor")?.value : undefined;',
    'const typed = state.settingsView === "yaml" ? $("settings-editor")?.value : undefined;',
  ):
    assert needle in app, needle
  assert 'data-section="yaml"' in app and 'yamlCard("editor", "yaml-save"' in app, (
    "the card holds the selected section and the editor"
  )
  assert 'yamlCard("settings-editor", "settings-yaml-save"' in app, (
    "the settings card carries its own pair"
  )
  assert '$("editor").addEventListener("input", renderFiles);' not in app, (
    "the editor lives in a rendered card, so the listener rides on the pane"
  )


def test_row_writes_itself_and_bar_is_gone() -> None:
  """Value-row source replaces Save bars, and provider saves track errors and Undo data."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'id="save"' not in page and 'id="settings-save"' not in page, "no bar Save"
  assert 'id="save-note"' in page and 'id="settings-note"' in page, "1 note per page"
  assert 'id="save-undo"' not in page and 'id="settings-undo"' not in page, (
    "the Undo rides beside the row that changed, not in the page"
  )
  for needle in (
    "async function editValue(input, write) {",
    "function bindValueRows(host, write) {",
    'bindValueRows($("provider-form"), () => saveForm());',
    'bindValueRows($("settings"), () => saveSettings());',
    'if (input.type !== "checkbox" && input.tagName !== "SELECT") return;',
    'showWrite("providers", t("provider.yaml.error_text", { error }), true);',
    "async function undoWrite() {",
    "function rememberWrite(page, restore, anchor = null) {",
    "function placeUndo(anchor = null) {",
    'button.className = "ghost undo-text";',
    'readonly spellcheck="false" autocomplete="off"',
  ):
    assert needle in app, needle
  assert '$("save")' not in app and '$("settings-save")' not in app, (
    "the bar Save is gone"
  )
  assert 'clearWrite("providers")' in app and 'clearWrite("settings")' in app, (
    "a quiet page stays quiet"
  )
  run_app_js(
    "state, saveForm, showWrite, lastWrite: () => lastWrite",
    """
sandbox.setTimeout = () => 0;
sandbox.clearTimeout = () => {};
sandbox.sessionStorage = sandbox.localStorage = { getItem: () => null };
const before = { path: 'providers.yml', text: 'saved fixture data' };
probe.state.file = 0;
probe.state.view = 'form';
probe.state.files = [before];
probe.state.forms = [{ p: { api_key: 'db:FIXTURE_KEY' } }];
probe.state.formSaved = ['{}'];
probe.state.writeAnchor = null;
let taken = 0;
sandbox.takeFile = async () => { taken += 1; };
sandbox.refreshEnv = async () => {};
sandbox.renderForm = sandbox.renderFiles = sandbox.refresh = () => {};
const sent = [];
const failure = 'fixture-save-error';
let accepted = true;
sandbox.fetch = async (url, options) => {
  sent.push({ url, method: options.method, body: JSON.parse(options.body) });
  return { ok: accepted, status: accepted ? 200 : 422,
    json: async () => accepted ? {} : { error: { message: failure } } };
};
probe.showWrite('providers', failure, true);
(async () => {
  for (const success of [true, false, true]) {
    accepted = success;
    await probe.saveForm();
    assert.strictEqual(node('save-message').textContent, success ? '' : failure);
    assert.strictEqual(node('save-note').hidden, success);
    assert.deepStrictEqual(sent.at(-1), {
      url: 'ui/api/providers', method: 'PUT',
      body: { path: before.path, blocks: probe.state.forms[0] },
    });
    if (success) {
      assert.strictEqual(probe.lastWrite().path, before.path);
      assert.strictEqual(probe.lastWrite().text, before.text);
    }
  }
  assert.strictEqual(sent.length, 3);
  assert.strictEqual(taken, 2, 'only successful saves reload the file');
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
  )


def test_phone_opens_section_as_own_page() -> None:
  """Section navigation code updates history and defines phone detail panes with named back controls."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function sectionPane(back, cards, actions = null) {",
    "function openSection(host, key) {",
    "function closeSection(host) {",
    "function applySectionHash() {",
    "history.pushState({ section: key }",
    'window.addEventListener("popstate", applySectionHash);',
    "applySectionHash();",
  ):
    assert needle in app, needle
  assert 'sectionPane(t("nav.settings_link")' in app
  assert 'sectionPane(t("nav.providers_link")' in app, (
    "each pane names the page its back button returns to"
  )
  assert '<h1 class="pane-title" data-pane-title hidden></h1>' in app, (
    "the pane bar carries the drill page title"
  )
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    '#provider-form[data-detail="1"] > .section-pane > .pane-bar,\n'
    '  #provider-form[data-file-open="1"] > .section-pane > .pane-bar {' in style
  ), "a desktop file or provider drill page shows the bar"
  assert ".card.list { padding: 0; background: none; border: 0; }" in style, (
    "the drill list page keeps its rows flush and paints no card of its own"
  )
  phone = style.split("@media (max-width: 60em) {", 1)[1]
  assert '.sections button::after { content: "\\203a";' in phone, (
    "a phone row carries a chevron"
  )
  assert '.settings[data-detail="1"] > .section-pane { display: grid; }' in phone, (
    "a phone shows the selected section alone"
  )
  assert '#provider-form:not([data-detail="1"]):not([data-file-open="1"])' in phone, (
    "the file list keeps its back bar, and the top-level providers list does not"
  )
  assert (
    '#settings .section-pane:has(.provider-flow #provider-form[data-file-open="1"])'
    " > .pane-bar > .back { display: none; }" in phone
  ), "the outer back yields while a provider file is open"
  assert (
    "[data-hook-drop], .section-pane .back, #nav a { min-height: 44px;"
    " min-width: 44px; }" in phone
  ), "the phone lifts the boxed controls to the touch floor"
  assert (
    '.pills [data-drop]::after, .pills [data-setting-drop]::after { content: "";'
    " position: absolute; inset: -10px -2px -10px -18px; }" in phone
  ), "keyword pills stay compact while expanding their delete hit area"
  assert (
    '.pills [data-add]::after, .pills [data-setting-add]::after { content: "";'
    " position: absolute; inset: -7px 0; }" in phone
  ), "pill add triggers stay compact while expanding their vertical hit area"
  assert "button.hint, .pills" not in phone, (
    "the round icon buttons keep their drawn size on a phone"
  )
  assert (
    "button.hint { position: relative; }\n"
    '  button.hint::after { content: ""; position: absolute; inset: -14px; }' in phone
  ), "the round icon buttons grow their hit area outside the drawn box"
  assert ".undo-text { min-height: 44px; }" in phone, (
    "the text Undo meets the touch floor"
  )


def test_hook_table_columns() -> None:
  """Hook-table source declares author/title, version, surfaces, scopes, source, and enabled columns."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  columns = (
    '<th>${t("hook.column.author_title")}</th>'
    '<th>${t("hook.column.version_column")}</th>'
    '<th>${t("hook.column.surfaces_column")}</th>'
    '<th class="hook-scope">${t("hook.column.scopes_column")}</th>'
  )
  assert columns in app, "the hooks table names its columns in the note order"
  assert '<th class="hook-source hide-sm">${t("hook.column.source_column")}</th>' in app
  assert '<th>${t("hook.column.enabled_column")}</th>' in app


def test_table_delete_buttons_use_trash_icon() -> None:
  """Key and hook deletion code uses trash icons instead of button text."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert app.count('iconButton("trash"') >= 6, (
    "keys, lists, overrides, and hooks share iconButton with trash"
  )
  assert 'data-key="${esc(k.name)}">Delete</button>' not in app, "the key row"
  assert 'data-hook-drop="${esc(row.path)}">Delete</button>' not in app, "the hook row"


def test_hook_rows_wrap_title_and_show_sha() -> None:
  """Hook-row code includes title, author, and source-SHA spans with a shortened commit ID."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert 'class="hook-title"' in app, "the title sits on its own line"
  assert 'class="hook-author"' in app, "the author sits under the title"
  assert 'class="hook-sha"' in app, "the short SHA sits under the repo"
  assert "record.commit.slice(0, 7)" in app, "the SHA is cut to 7 characters"


def test_hooks_card_orders_files_of_surface() -> None:
  """Hook-order code provides movement controls without changing explicit surface activation."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert "data-hook-drag" in app, "a file row carries its drag handle"
  assert "function moveHookFile(" in app, "the move writes the new order"
  assert "hookOrderState()[surface] = values" in app, (
    "the new precedence stays separate from explicit hook activation"
  )
  assert 'setListValue("hooks", surface, values)' not in app, (
    "an order change does not make a metadata hook explicit"
  )
  assert 'id="hooks-order"' in app, "the card holds the order block"
  assert "grip_vertical" in app, "the drag handle draws a grip"


def test_dashboard_form_rows_keep_contracts() -> None:
  """Form rows share one wrapper and keep each provider value state."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert app.count('<div class="field stack info">${labelSpan') == 1, (
    "every stacked field uses the shared wrapper"
  )
  provider = app[
    app.index("function providerCard(") : app.index("function renderForm()")
  ]
  assert app.count('aria-haspopup="dialog" aria-controls="modal"') == 1, (
    "all click-to-edit modal fields share modalValueInput"
  )
  assert provider.count("modalValueInput(") == 1, (
    "provider text fields share modalValueInput"
  )

  run_app_js(
    "state, field, providerCard",
    """
const wrapped = probe.field('<Label>', 'Hint <&', '<input data-trusted="yes">');
assert(wrapped.startsWith('<div class="field stack info"><span><b>&lt;Label&gt;</b>'), wrapped);
assert(wrapped.includes('role="tooltip">Hint &lt;&amp;</small>'), 'the hint stays escaped');
assert(wrapped.endsWith('<input data-trusted="yes"></div>'), 'the trusted body stays intact');

probe.state.providerDefaults = {
  cloudflare: {
    api_base: 'https://default.example/v1', api_type: 'openai',
    discovery_url: 'https://default.example/models', account_id: 'env:CF_ACCOUNT',
  },
  '*': {},
};
probe.state.env = [
  { name: 'CF_TOKEN', state: 'env', has_env: true },
  { name: 'CF_ACCOUNT', state: 'saved', has_saved: true, start: 'acct', length: 8 },
];
probe.state.settings = { hook_rows: [] };
const html = probe.providerCard('cloudflare', {
  api_key: 'env:CF_TOKEN', account_id: 'db:CF_ACCOUNT',
  api_base: 'https://custom.example/v1', api_type: '',
  client_keys: {}, discovery_match: {}, exclude: [], tier: {}, models: {}, hooks: [],
});
const inputFor = (label) => {
  const at = html.indexOf(`data-value="${label}"`);
  assert(at > 0, `the ${label} input exists`);
  return html.slice(html.lastIndexOf('<input', at), html.indexOf('>', at) + 1);
};
const apiKey = inputFor(LOCALE["provider.api_key.label"]);
assert(apiKey.includes('type="text"') && apiKey.includes('readonly'), apiKey);
assert(apiKey.includes('value="env:CF_TOKEN"'), 'an environment token stays visible');
assert(apiKey.includes('placeholder="' + LOCALE["provider.secret.environment_text"] + '"'), apiKey);
assert(apiKey.includes('data-set="[&quot;cloudflare&quot;,&quot;api_key&quot;]"'), apiKey);
const account = inputFor(LOCALE["provider.account_id.label"]);
assert(account.includes('type="password"') && account.includes('value=""'), account);
assert(account.includes('placeholder="acct••••"'), 'a database token stays masked');
const base = inputFor(LOCALE["provider.api_base.label"]);
assert(base.includes('type="text"') && base.includes('value="https://custom.example/v1"'), base);
assert(base.includes('placeholder="https://default.example/v1"'), base);
assert.strictEqual((html.match(/<input class="text" type="(?:text|password)" readonly/g) || []).length, 5,
  'each provider text value keeps the shared readonly contract');
""",
  )


def test_dashboard_display_primitives_keep_contracts() -> None:
  """Shared value renderers preserve trusted markup, escaped data, and exact wrappers."""
  app = (
    Path(__file__).resolve().parents[2] / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert app.count("timeText(") >= 4, (
    "the overview, limits, and notifications share time markup"
  )
  assert app.count("progressMeter(") >= 6, (
    "every weight and allowance bar shares one meter"
  )
  assert app.count("statusBadge(") >= 5, (
    "request, boolean, and validation states share one badge"
  )
  assert "weightBar(" not in app, "the domain-named progress duplicate is gone"

  run_app_js(
    (
      "line, none, mobileLabel, nameCell, cell, kpi, stamp, timeText, "
      "progressMeter, statusBadge, statusCell, yesNo"
    ),
    """
assert.strictEqual(probe.line('<b>left</b>', '<i>right</i>'),
  '<div class="line"><span><b>left</b></span><span><i>right</i></span></div>');
assert.strictEqual(probe.line('<b>left</b>', '<i>right</i>', { title: '<value & "x">' }),
  '<div class="line"><span><b>left</b></span><span>'
  + '<span title="&lt;value &amp; &quot;x&quot;&gt;"><i>right</i></span></span></div>');
assert.strictEqual(probe.none('No requests'), '<div class="more">No requests</div>');
assert.strictEqual(probe.mobileLabel('<label & "x">'),
  '<span class="mobile-label" aria-hidden="true">&lt;label &amp; &quot;x&quot;&gt;</span>');
assert.strictEqual(probe.nameCell('p/"<&', '<b>trusted</b>', '<Model>'),
  '<td role="cell" class="name" title="p/&quot;&lt;&amp;">'
  + '<span class="mobile-label" aria-hidden="true">&lt;Model&gt;</span>'
  + '<span class="cell-value"><b>trusted</b></span></td>');
assert.strictEqual(probe.cell('<Time>', '<b>trusted</b>', 'num', ' title="exact"'),
  '<td role="cell" class="num" title="exact">'
  + '<span class="mobile-label" aria-hidden="true">&lt;Time&gt;</span>'
  + '<span class="cell-value"><b>trusted</b></span></td>');
assert.strictEqual(probe.kpi('<strong>7</strong>', '<models>'),
  '<div class="kpi"><b><strong>7</strong></b><small>&lt;models&gt;</small></div>');
const at = Date.now() / 1000 - 600;
const tenMinutesAgo = LOCALE["time.minute_ago.other_text"].replace("{count}", "10");
const atIso = new Date(at * 1000).toISOString();
assert.strictEqual(probe.timeText(at),
  `<span data-timestamp="${atIso}" title="${probe.stamp(at)}">${tenMinutesAgo}</span>`);
assert.strictEqual(probe.timeText(at, { tag: 'time', variant: 'muted' }),
  `<time class="muted" datetime="${atIso}" title="${probe.stamp(at)}">${tenMinutesAgo}</time>`);
assert.strictEqual(probe.statusBadge('<bad>', { variant: 'bad', title: '<reason & "x">' }),
  '<span class="bad" title="&lt;reason &amp; &quot;x&quot;&gt;">&lt;bad&gt;</span>');
assert.strictEqual(probe.statusCell({ status: '<bad>' }), '&lt;bad&gt;');
assert.strictEqual(probe.statusCell({ status: 200 }),
  '<span title="' + LOCALE["request.status.ok_text"] + '">200</span>');
assert.strictEqual(probe.yesNo(true), '<span class="yes">' + LOCALE["common.boolean.yes_text"] + '</span>');
assert.strictEqual(probe.yesNo(false), '<span class="muted">' + LOCALE["common.boolean.no_text"] + '</span>');
const meter = probe.progressMeter(0.25);
assert(meter.includes('class="track"') && meter.includes('class="fill"'), meter);
assert(meter.includes('width:25%') && meter.includes('var(--orange) 50%'), meter);
const healthyMeter = probe.progressMeter(0.9);
assert(healthyMeter.includes('color-mix(in srgb, var(--accent) 80%'), healthyMeter);
assert(!healthyMeter.includes('oklch, var(--accent)'), 'the healthy half keeps blue out of violet');
""",
  )


def test_dashboard_action_buttons_keep_contracts() -> None:
  """One action renderer keeps native types, variants, states, and selectors."""
  root = Path(__file__).resolve().parents[2]
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  static_tags = re.findall(r"<button\b[^>]*>", page, re.DOTALL)
  assert static_tags and all('type="' in tag for tag in static_tags), static_tags
  assert all(tag.index('class="') < tag.index('type="') for tag in static_tags), (
    "static actions use the class-then-type contract"
  )
  assert app.count("<button") == 1, (
    "only the shared renderer writes a dynamic button tag"
  )
  assert app.count("actionButton(") >= 25, (
    "every dynamic action uses the shared renderer"
  )

  run_app_js(
    "actionButton",
    """
const rendered = probe.actionButton('<b>trusted</b>', {
  variant: 'ghost danger',
  type: 'submit',
  attrs: { value: 'ok', 'data-name': '<&"', disabled: true, hidden: false, title: null },
});
assert.strictEqual(rendered,
  '<button class="ghost danger" type="submit" value="ok" data-name="&lt;&amp;&quot;" disabled>'
  + '<b>trusted</b></button>');
assert.strictEqual(probe.actionButton('Paused', {
  variant: 'ghost live-toggle', attrs: { 'aria-pressed': 'false' },
}), '<button class="ghost live-toggle" type="button" aria-pressed="false">Paused</button>');
assert.strictEqual(probe.actionButton('Select', {
  attrs: { role: 'option', 'aria-selected': 'false' },
}), '<button type="button" role="option" aria-selected="false">Select</button>');
""",
  )

  for fragment in (
    'class="primary" type="submit"',
    'class="ghost modal-close" type="submit" value="cancel"',
    'class="ghost live-toggle" type="button" id="live-toggle" aria-pressed="false"',
    'class="tabs scroll-fade" id="nav"',
    'class="sort" type="button"',
  ):
    assert fragment in page, fragment
  for fragment in (
    'variant: "ghost danger", attrs: { id: "drop-provider" }',
    'variant: "add"',
    'variant: "ghost danger", attrs: { id: "reset-weights", title: t("dialog.reset.message") }',
  ):
    assert fragment in app, fragment

  contracts = (
    ("catalog", '"data-catalog-rebuild"', 'closest("[data-catalog-rebuild]")'),
    ("notification", '"data-rebuild"', 'closest("[data-rebuild]")'),
    ("file", '"data-file"', 'closest("[data-file]")'),
    ("settings add", '"data-setting-add"', 'closest("[data-setting-add]")'),
    ("settings delete", '"data-setting-drop"', 'closest("[data-setting-drop]")'),
    ("hook add", '"data-hook-source-add"', 'closest("[data-hook-source-add]")'),
    ("hook delete", '"data-hook-drop"', 'closest("[data-hook-drop]")'),
    ("hook update", '"data-hooks-update"', 'closest("[data-hooks-update]")'),
  )
  for name, markup, selector in contracts:
    assert markup in app, f"{name} markup"
    assert selector in app, f"{name} handler"
  assert 'value="cancel"' in page and 'value="ok"' in page
  assert '$("modal").addEventListener("close"' in app
  assert 'event.target.returnValue === "ok"' in app


def test_dashboard_icons_and_feedback_keep_accessible_names() -> None:
  """Icon and feedback source includes hidden-SVG attributes, action labels, and status roles."""
  root = Path(__file__).resolve().parents[2]
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  inline_icons = re.findall(r"<svg\b[^>]*>", page, re.DOTALL)
  assert inline_icons and all('aria-hidden="true"' in tag for tag in inline_icons)
  for fragment in (
    'aria-label="auth.password.show_label"',
    'type="button" id="notice-close" aria-label="notice.dismiss_label"',
    '"data-key": k.name, "aria-label": t("settings.keys.delete_named_label", { name: k.name })',
    (
      '"data-hook-drop": row.path, "data-hook-title": title, '
      '"aria-label": t("hook.action.delete_named_label", { name: row.name })'
    ),
    'button.setAttribute("aria-label", t("action.undo_write_label"))',
  ):
    assert fragment in page or fragment in app, fragment
  for name, role in (
    ("login-message", "alert"),
    ("notice", "alert"),
    ("limits-message", "status"),
    ("save-message", "status"),
    ("settings-message", "status"),
  ):
    tag = re.search(rf'<[^>]*id="{name}"[^>]*>', page)
    assert tag and f'role="{role}"' in tag.group(0), name
  assert 'id="key-message" role="alert"' in app
  assert 'id="reset-message" role="status"' in app
  assert '<dialog id="modal" class="modal" aria-labelledby="modal-title">' in page
  assert '<template id="modal-model-template" data-modal-template="model">' in page


def test_dashboard_rows_keep_responsive_cell_order() -> None:
  """Model and request renderer source preserves the cell order used by phone CSS."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")

  def in_order(source: str, needles: tuple[str, ...]) -> None:
    cursor = 0
    for needle in needles:
      found = source.find(needle, cursor)
      assert found >= 0, needle
      cursor = found + len(needle)

  models = app.split("function renderModels() {", 1)[1].split(
    "\n}\n\n// The time left", 1
  )[0]
  in_order(
    models,
    (
      "${nameCell(m.id, actionButton(modelName(m.id), {",
      '<td class="hide-sm"><div class="types">',
      '<td class="mid hide-sm">${mobileLabel(t("model.column.tier_column"))}',
      '<td class="hide-sm mid num${m.order > 1 ? "" : " muted"}">',
      '<td class="hide-sm num muted"><span${m.max_input_tokens',
      '<td class="mid hide-sm">${mobileLabel(t("model.column.tools_column"))}',
      '<td class="hide-sm mid">${m.mode === "chat" ? reasoningCell(m) : dash}</td>',
      '<td class="num hide-sm">${mobileLabel(t("model.column.cooldown_column"))}',
      '<td class="num">${mobileLabel(t("model.column.weight_column"))}',
    ),
  )

  requests = app.split("function renderRequests(", 1)[1].split(
    "\n}\n\n// The label of each catalog mode", 1
  )[0]
  in_order(
    requests,
    (
      '${cell(t("request.column.time_column"),',
      "${appCell(r)}",
      '${cell(t("request.column.session_column"),',
      "${nameCell(r.model",
      "${nameCell(r.via",
      '${cell(t("request.column.effort_column"),',
      '${cell(t("request.column.status_column"),',
      "${tokenCell(r.tokens?.input",
      "${tokenCell(r.tokens?.output",
      '${cell(t("request.column.ttft_column"),',
      '${cell(t("request.column.stream_column"),',
      "${fallbackCell(r,",
    ),
  )

  live = app.split("function renderLive() {", 1)[1].split(
    "\n}\n\n// The live clocks", 1
  )[0]
  in_order(
    live,
    (
      '${cell(t("request.column.time_column"),',
      "${appCell(r)}",
      '${cell(t("request.column.session_column"),',
      "${nameCell(r.model",
      "${nameCell(r.via",
      '${cell(t("request.column.effort_column"),',
      '${cell(t("request.column.status_column"),',
      '${cell(t("request.column.input_column"),',
      '${cell(t("request.column.output_column"),',
      'data-clock="ttft"',
      'data-clock="stream"',
      "${fallbackCell(r,",
    ),
  )


def test_limits_rows_without_use_or_catalog_home_stay_hidden() -> None:
  """A row with no use and no catalog home hides, while an exhausted row stays."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert app.index("state.models = models;") < app.index("renderLimits(limits);"), (
    "the catalog list lands before the limits rows render"
  )
  run_app_js(
    "state, shownLimits, overviewLimits",
    """
const row = (limit, remaining) => [{kind: 'requests', span: 'minute', limit, remaining, reset: null}];
probe.state.limits = {providers: [], lanes: [
  {model: 'quiet', client: '', at: null, rows: row(0, 0)},
  {model: 'gone', client: '', at: null, rows: row(5, 1)},
  {model: 'live', client: '', at: null, rows: row(5, 0)},
]};
probe.state.models = [{id: 'live'}, {id: 'quiet'}];
const rows = probe.shownLimits();
assert(rows.length === 1 && rows[0].model === 'live', 'only the exhausted catalog row stays');
const overview = probe.overviewLimits(probe.state.limits);
assert(overview.includes('live'), 'the Overview card keeps the active catalog row');
assert(!overview.includes('quiet') && !overview.includes('gone'),
  'the Overview card drops 0 of 0 and uncataloged rows just like the Limits tab');
""",
  )


def test_limit_check_time_is_relative_with_exact_hover() -> None:
  """Check status uses shared time markup, stacks below Check now, and omits hourly text."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  locale = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert '"limits.checked_text": "Checked {time}"' in locale
  assert 'class="limits-check-stack"' in page
  assert page.index('id="limits-search"') < page.index('class="limits-check-stack"')
  stack = page.split('class="limits-check-stack">', 1)[1].split("</div>", 1)[0]
  assert stack.index('id="limits-check"') < stack.index('id="limits-checked"')
  assert 'call("limits", { method: "POST", spinner: false })' in app
  assert (
    "if (prevLimits) renderLimits({ ...prevLimits, checked: Date.now() / 1000 });"
    in app
  )
  assert 'button.classList.add("checking")' not in app
  assert "align-items: center; justify-content: center;" in css
  assert "#limits-check { min-height: 0; padding: 2px 12px; }" in css
  run_app_js(
    "renderLimits, timeText",
    """
const checked = Date.now() / 1000 - 120;
probe.renderLimits({checked, providers: [], lanes: []});
assert(node('limits-checked').innerHTML.includes(probe.timeText(checked)));
assert(!node('limits-checked').innerHTML.includes('hourly'));
probe.renderLimits({checked: null, providers: [], lanes: []});
assert.strictEqual(node('limits-checked').innerHTML, LOCALE['limits.not_checked_text']);
""",
  )


def test_maintenance_card_lives_in_settings() -> None:
  """The update check, rebuild, and reset live in a Settings section."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  locale = (root / "daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")
  assert 'id="update-card"' not in page and 'id="reset-weights"' not in page, (
    "the notifications page and the models page keep no maintenance hosts"
  )
  assert '"settings.maintenance.title"' in locale, "the section carries its own label"
  assert '"update.state_title"' in locale, "the update state row carries a label"
  assert "function maintenanceCard()" in app, "one builder draws the card"
  assert '["maintenance", t("settings.maintenance.title")]' in app, (
    "the rail lists the section"
  )
  assert "+ maintenanceCard())" in app, "the pane carries the card"
  assert '$("settings").addEventListener("click"' in app, (
    "the settings host owns the check and the reset"
  )
  notifications = app[
    app.index("function renderNotifications") : app.index(
      "// The maintenance card lives"
    )
  ]
  assert "update-card" not in notifications, "the feed renderer draws no update card"


def test_house_breakpoints_rail_search_and_responsive_folds() -> None:
  """Dashboard CSS and JS use only nuu house breakpoints and keep rail/fold contracts."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  allowed = {"68em", "60em", "30em", "24em"}
  media_re = re.compile(r"\(\s*(?:min|max)-(?:width|height)\s*:\s*([^)]+?)\s*\)")
  media_css = "\n".join(
    line for line in css.splitlines() if not line.lstrip().startswith("@container")
  )
  css_breakpoints = set(media_re.findall(media_css))
  js_breakpoints = set(media_re.findall(app))
  assert css_breakpoints == allowed
  assert js_breakpoints <= allowed
  assert "(max-height: 24em)" in css
  assert (
    "grid-template-columns: minmax(0, 1fr);"
    in css.partition(".rail {")[2].partition("}")[0]
  )
  assert ".rail-search input:focus-visible { outline: none" not in css
  assert ':root[data-keyboard-nav="true"] .rail-search:has(input:focus-visible)' in css
  narrow_desk = css.split("@media (max-width: 68em) {", 1)[1].split("\n}", 1)[0]
  assert (
    'section[data-page="requests"] > .flow > .column:not(.wide) { display: none; }'
    in narrow_desk
  )
  assert 'section[data-page="requests"] .legend-open {' in narrow_desk
  tablet = css.split("@media (max-width: 60em) {", 1)[1].split("\n}", 1)[0]
  assert (
    'section[data-page="limits"] > .flow > .column.wide { flex: none; width: 100%; min-height: 0; }'
    in tablet
  )
  phone = css.split("@media (max-width: 30em) {", 1)[1].split("\n}", 1)[0]
  assert (
    ".requests tbody {" in phone
    and "repeat(auto-fill, minmax(min(280px, 100%), 1fr))" in phone
  )


def test_nuu_scrollbars_and_live_dot_recipes() -> None:
  """Stylesheet and live rows follow nuu scrollbar and live-dot recipes."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "--scrollbar-thumb: #767682;" in css
  assert "--scrollbar-thumb: #6e6e7a;" in css
  assert "::-webkit-scrollbar { width: 10px; height: 10px; }" in css
  assert "border: 2px solid var(--bg);" in css
  assert "background-clip: padding-box; border-radius: 9999px;" in css
  assert ':root[data-keyboard-nav="true"] ::-webkit-scrollbar-thumb:hover' in css
  assert "::-webkit-scrollbar-button { display: none; width: 0; height: 0; }" in css
  assert "@supports not selector(::-webkit-scrollbar)" in css
  assert ".dot.live { background: var(--orange); }" in css
  assert (
    ".ico-svg > .dot, .dot.live, .pill.warn .dot { animation: pulse 1.4s var(--ease) infinite; }"
    in css
  )
  assert ".ico-slot > svg, .btn-spinner { width: 14px; height: 14px; }" in css
  assert "animation: nuu-spin 0.8s linear infinite;" in css
  assert "const liveDot =" in app and '${liveDot()}<span class="live-ago">' in app
  assert "const iconSlot =" in app and 'iconSlot("refresh_cw")' in app


def test_nuu_modal_manager_and_delete_confirmations() -> None:
  """Modals expose top-right close buttons, action Cancel, focus restore, and delete gates."""
  root = Path(__file__).resolve().parents[2]
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert page.count("<dialog ") == 1
  assert page.count('class="ghost modal-close"') == 1
  assert page.count("data-modal-template=") == 3
  assert page.count('class="modal-actions"') == 1
  assert 'id="modal-close"' in page
  assert page.count('id="modal-cancel"') == 1
  for marker in (
    "mountModalView(",
    "confirmDeleteEntry(",
    "restoreModalTrigger(",
    "rememberModalTrigger(",
    't("hook.action.delete_source_title", { name: selected })',
  ):
    assert marker in app, marker
  run_app_js(
    "ask, openLegend, restoreModalTrigger",
    r"""
let restored = 0;
let cancelFocused = 0;
const trigger = { focus: () => { restored += 1; } };
sandbox.document.activeElement = trigger;
node("modal").open = false;
node("modal").showModal = function () { this.open = true; };
node("modal-close").focus = () => { cancelFocused += 1; };
node("modal-ok").classList = { toggle: () => {} };
probe.ask("Delete entry", "Delete \"test\"?", "Delete", true);
assert.strictEqual(cancelFocused, 1, "confirmation modals focus Cancel");
node("modal").open = false;
probe.restoreModalTrigger(node("modal"));
assert.strictEqual(restored, 1, "closing the modal restores trigger focus");

let legendRestored = 0;
const legendBtn = { focus: () => { legendRestored += 1; } };
node("legend-body").innerHTML = "<span><code>fr</code>fallback</span>";
probe.openLegend({ currentTarget: legendBtn });
assert.strictEqual(node("modal").open, true, "openLegend opens shared modal");
node("modal").open = false;
probe.restoreModalTrigger(node("modal"));
assert.strictEqual(legendRestored, 1, "closing legend restores #legend-open focus");
""",
  )


def test_nuu_loading_bar_and_page_memory_recipes() -> None:
  """API calls drive data-loading and aria-busy, and page memory skips unchanged redraws."""
  code = _app_js_vm(
    r"""
const active = {
  dataset: { page: "settings" },
  hidden: false,
  attrs: {},
  setAttribute(name, value) { this.attrs[name] = value; },
};
const header = { dataset: {} };
const scheduled = [];
sandbox.setTimeout = (fn, ms) => {
  scheduled.push({ fn, ms });
  return scheduled.length;
};
sandbox.clearTimeout = () => {};
sandbox.document.querySelector = (sel) => {
  if (sel === "#app > header") return header;
  if (sel === "section[data-page]:not([hidden])") return active;
  return null;
};
let seenBusyDuringFetch = null;
let seenLoadingDuringFetch = null;
const payload = {
  values: {
    personalization: { theme: "dark", time_format: "24h" },
    optimization: { parallel: { mode: "off" } },
  },
  defaults: {
    affinity: { mode: "sticky" },
    personalization: { theme: "dark", time_format: "24h" },
  },
  files: {},
  text: "a: 1\n",
  hook_rows: [],
};
sandbox.localStorage = { getItem: () => null };
sandbox.sessionStorage = { getItem: () => null };
sandbox.fetch = async () => {
  seenBusyDuringFetch = active.attrs["aria-busy"];
  seenLoadingDuringFetch = active.dataset.loading;
  return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(payload)) };
};
vm.runInContext("globalThis.__renders = 0; renderSettings = () => { globalThis.__renders += 1; };", sandbox);
(async () => {
  await sandbox.loadSettings();
  assert.strictEqual(seenBusyDuringFetch, "true", "active section is aria-busy while fetching");
  assert.strictEqual(seenLoadingDuringFetch, "true", "active section sets data-loading=true while fetching");
  assert.strictEqual(active.attrs["aria-busy"], "false", "finally resets aria-busy to false");
  assert.strictEqual(active.dataset.loading, "done", "finally marks data-loading=done for completion fade");
  while (scheduled.length) scheduled.shift().fn();
  assert.strictEqual(active.dataset.loading, undefined, "220 ms timer clears data-loading");
  assert.strictEqual(header.dataset.loading, undefined, "header carries no imperative dataset state");
  assert.strictEqual(sandbox.__renders, 1, "first settings load renders");
  await sandbox.loadSettings();
  assert.strictEqual(sandbox.__renders, 1, "unchanged settings payload skips redraw");
  await sandbox.loadSettings({ fresh: true });
  assert.strictEqual(sandbox.__renders, 2, "fresh reload forces redraw");
  process.exit(0);
})().catch((err) => { console.error(err); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_nuu_audit_repairs_for_focus_spin_switch_skiplink_and_scroll_fade() -> None:
  """Audit repairs gate focus outlines, use compositor switches, and wire skip link."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  html = Path("daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  locale = Path("daedalus/dashboard/ui/en.js").read_text(encoding="utf-8")

  fast_match = re.search(r"--fast:\s*(\.?\d+(?:\.\d+)?)s;", css)
  assert fast_match is not None
  fast_seconds = float(fast_match.group(1))
  assert MIN_FAST_SECONDS <= fast_seconds <= MAX_FAST_SECONDS

  assert ':root[data-keyboard-nav="true"] .field input.switch:focus-visible' in css
  assert ':root[data-keyboard-nav="true"] .fold summary:focus-visible' in css
  switch_rule = css.split(".field input.switch {", 1)[1].split("}", 1)[0]
  assert "background-position" not in switch_rule
  assert ".field input.switch::before {" in css
  assert "transform: translateX(18px);" in css
  assert ".settings .field.stack > button { align-self: flex-start; }" in css
  assert "@keyframes spin" not in css
  assert (
    f'.card[data-section="keys"] .keys .ghost {{ width: {MOBILE_TOUCH_MIN_PX}px; '
    f"height: {MOBILE_TOUCH_MIN_PX}px; min-width: {MOBILE_TOUCH_MIN_PX}px; }}"
  ) in css
  assert ".tabs.scroll-fade," in css and ".card-scroll.scroll-fade," in css
  assert '"nav.skip_to_main_link": "Skip to main content"' in locale
  assert '<a class="skip-link" href="#main" data-i18n="nav.skip_to_main_link">' in html
  assert '<main id="main" tabindex="-1">' in html
  assert 'setAttribute?.("aria-busy", "true")' in app
  assert "action.checking_button" not in app and "action.checking_button" not in locale


def test_vendored_gsap_serves_offline_and_drives_motion_with_reduced_motion_guard(
  client: TestClient,
) -> None:
  """Register and serve offline gsap.min.js, and skip tweens under reduced motion."""
  root = Path(__file__).resolve().parents[2]
  gsap_path = root / "daedalus/dashboard/ui/gsap.min.js"
  assert gsap_path.is_file()
  resp = client.get("/ui/gsap.min.js")
  assert resp.status_code == 200
  assert "gsap" in resp.text
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert html.index('src="ui/gsap.min.js"') < html.index('src="ui/en.js"')
  assert (
    '<script src="ui/en.js"></script>\n'
    '<script src="ui/vendor/sortable.min.js"></script>\n'
    '<script src="ui/app.js"></script>' in html
  )

  code = _app_js_vm(
    r"""
Object.assign(sandbox, sandbox.window);
sandbox.window = sandbox;
sandbox.document.documentElement.style = {};
const baseCreate = sandbox.document.createElement;
sandbox.document.createElement = (...args) => ({ style: {}, ...baseCreate(...args) });
sandbox.setTimeout = () => 0;
sandbox.clearTimeout = () => {};
vm.runInContext(fs.readFileSync("daedalus/dashboard/ui/gsap.min.js", "utf8"), sandbox);
assert(typeof sandbox.gsap?.fromTo === "function", "vendored GSAP attaches to globalThis");
const calls = [];
sandbox.gsap.fromTo = (target, fromVars, toVars) => {
  calls.push({ target, fromVars, toVars });
};
const box = { style: {} };
sandbox.matchMedia = (q) => ({ matches: false, addEventListener() {} });
vm.runInContext("tweenFromTo", sandbox)(box, { opacity: 0, y: 4 }, { opacity: 1, y: 0 });
assert.strictEqual(calls.length, 1, "motion runs when reduced-motion is off");
assert.strictEqual(calls[0].toVars.ease, "power3.out");
assert.strictEqual(calls[0].toVars.clearProps, "opacity,transform");
sandbox.matchMedia = (q) => ({ matches: q === "(prefers-reduced-motion: reduce)", addEventListener() {} });
vm.runInContext("tweenFromTo", sandbox)(box, { opacity: 0 }, { opacity: 1 });
assert.strictEqual(calls.length, 1, "reduced-motion skips GSAP tweens");
process.exit(0);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_hooks_table_to_cards_and_single_version_datalist() -> None:
  """Hooks manager and rule tables use table-to-cards responsive CSS and a single datalist input."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert '<table class="keys hook-table">' in app
  assert "hookRuleVersion" not in app
  assert '.card[data-section="hooks"] .keys.hook-table tbody {' in css
  assert ".hook-group { container: hooks / inline-size; }" in css
  assert css.index("@container hooks (max-width: 50rem) {") < css.index(
    ".keys.hook-table tbody {"
  )
  assert ".hook-rules td .input input[list] {" in css
