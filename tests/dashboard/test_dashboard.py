import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from xml.etree import ElementTree

import httpx
import pytest
from fastapi.testclient import TestClient

import daedalus
from daedalus import config, dashboard, store, updates
from daedalus.catalog import schedule
from daedalus.config import remote, settings
from daedalus.dashboard import History
from daedalus.providers import base, hooks
from daedalus.routing import loops
from daedalus.server import api, headroom, upstream

CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "tier": {"TIER-A": ["big"], "TIER-C": ["small"]},
    "models": {"small": {"order": 3}},
  }
}
MASTER = "master-key-0123456789"
AUTH = {"Authorization": f"Bearer {MASTER}"}
ROWS = [
  {
    "id": "p/big",
    "mode": "chat",
    "max_input_tokens": 1000,
    "supports_function_calling": True,
    "supports_reasoning": True,
    "reasoning_effort": "medium",
    "supports_vision": True,
    "supports_audio_input": True,
  },
  {"id": "p/small", "mode": "chat"},
  {"id": "p/embed", "mode": "embedding"},
]


def answer(request: httpx.Request) -> httpx.Response:
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_ui_path_sends_the_reader_to_the_page(client: TestClient) -> None:
  """`/ui` and `/ui/` answer a redirect to the page at `/`."""
  for path in ("/ui", "/ui/"):
    answer = client.get(path, follow_redirects=False)
    assert answer.status_code == 307, answer.text
    assert answer.headers["location"] == "/"


def test_page(client: TestClient) -> None:
  page = client.get("/")
  assert page.status_code == 200 and "text/html" in page.headers["content-type"]
  assert 'src="ui/app.js?v=' in page.text, "relative asset paths with a content hash"
  assert 'href="ui/style.css?v=' in page.text, "the style link has a content hash"
  assert (
    '<th scope="col" class="hide-sm" role="columnheader"'
    ' title="The chat session that picked the model">Session</th>' in page.text
  ), "the Requests session column"
  assert '<div class="card legend" id="requests-legend">' in page.text, (
    "the code legend is a card of its own beside the table"
  )
  assert "<details" not in page.text and "<summary" not in page.text, (
    "the legend keeps no expand button"
  )
  assert "Keys and values" not in page.text and "env-rows" not in page.text, (
    "the provider block owns the keys: no Keys and values card"
  )
  assert '<dialog id="modal"' in page.text, "the confirmation modal"
  assert "<code>frX</code>" not in page.text, "the legend body is filled by app.js"
  for block in ("ov-providers", "ov-keys", "ov-settings"):
    assert block not in page.text, f"the Overview keeps state only: no {block} block"
  for name in (
    "overview",
    "requests",
    "models",
    "providers",
    "limits",
    "settings",
  ):
    assert f'<section data-page="{name}"' in page.text, f"the {name} page"
  assert 'name="remember" type="checkbox" checked' in page.text, "remember me starts on"
  script = client.get("/ui/app.js")
  assert script.status_code == 200 and "javascript" in script.headers["content-type"]
  for reason in ("ctx", "hlt", "lmt", "err", "rnd", "cls", "esc"):
    assert f'  {reason}: "' in script.text, reason
  assert "const REPEAT_CODE = " in script.text, "the repeat code family"
  assert 'title="${esc(label)}"' in script.text, "reason codes have hover labels"
  assert (
    'const EFFORT_SHORT = { minimal: "min", low: "low", medium: "med", high: "hi", xhigh: "xhi" }'
    in script.text
  )
  assert (
    'await ask("Log out", "The dashboard session ends.", "Log out")' in script.text
  ), "a logout asks first, in the modal"
  assert "shortEffort(sentText(a, r.effort))" in script.text
  assert '["change_on_draw", "Change pin on draw"' in script.text
  assert '["optimization", "Optimization"' in script.text, (
    "the Optimization card is in Settings"
  )
  assert '["enabled", "Headroom on"' in script.text, "the Headroom switch is a checkbox"
  assert '["limits", "Limits"' in script.text, "the loop thresholds are in Settings"
  assert '["routing", "Routing"' in script.text, "the Routing card is in Settings"
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


def test_model_marks(client: TestClient) -> None:
  """The dashboard serves a chosen mark file, and no other name of the folder."""
  mark = client.get("/ui/icons/cloudflare.svg")
  assert mark.status_code == 200 and mark.headers["content-type"] == "image/svg+xml"
  assert mark.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/icons/nope.svg").status_code == 404, "a missing mark"
  assert client.get("/ui/icons/logo.svg").status_code == 404, "the page files stay out"
  assert client.get("/ui/icons/CLOUDFLARE.svg").status_code == 404, "the name is exact"


def test_app_js_split_requests() -> None:
  """The dashboard script splits requests with multiple answers and formats transition codes."""
  code = """
const fs = require('fs');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8');
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


def _app_js_vm(extra: str) -> str:
  """The dashboard script in a node vm, with the DOM stubs the top level needs."""
  return f"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8');
const hosts = [{{ innerHTML: '' }}, {{ innerHTML: '' }}];
const classes = new Set();
const nav = {{
  scrollLeft: 30, clientWidth: 100, scrollWidth: 260,
  classList: {{ add: (name) => classes.add(name), remove: (name) => classes.delete(name),
    toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) }},
  addEventListener: () => {{}},
}};
const byId = new Map();
const el = (id) => {{
  if (!byId.has(id)) byId.set(id, {{ innerHTML: '', textContent: '', addEventListener: () => {{}},
    classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }});
  return byId.get(id);
}};
const sandbox = {{
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{
    hidden: false,
    documentElement: {{ dataset: {{}} }},
    getElementById: (id) => (id === 'nav' ? nav : el(id)),
    createElement: () => ({{ append: () => {{}}, prepend: () => {{}}, setAttribute: () => {{}},
      addEventListener: () => {{}}, remove: () => {{}} }}),
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }} }}),
    querySelectorAll: (sel) => (sel === '[data-status]' ? hosts : []),
    addEventListener: () => {{}},
  }},
  navigator: {{}},
  location: {{ hash: '' }},
  window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}),
  $: (id) => (id === 'nav' ? nav : el(id)),
  __el: el,
  __probe: {{}},
}};
vm.createContext(sandbox);
vm.runInContext(src + String.fromCharCode(10) +
  'globalThis.__probe.askPair = typeof askPair === "function" ? askPair : undefined;', sandbox);
{extra}
"""


def test_app_js_tab_steps() -> None:
  """A chevron shows at each end of the tab bar that holds tabs off screen, and hides at the end."""
  code = _app_js_vm(
    """
const left = byId.get('nav-left'), right = byId.get('nav-right');
assert(!left.hidden && !right.hidden, 'both chevrons show on the first paint');
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert(!left.hidden && !right.hidden);
nav.scrollLeft = 0;
sandbox.markNavSteps();
assert(left.hidden && !right.hidden, 'the left chevron goes at the start');
nav.scrollLeft = 160;
sandbox.markNavSteps();
assert(!left.hidden && right.hidden, 'the right chevron goes at the end');
nav.scrollLeft = 0;
nav.scrollWidth = 100;
sandbox.markNavSteps();
assert(left.hidden && right.hidden, 'no chevron when every tab fits');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_boots_the_app_with_the_server_settings(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A stored session boots the page: the app shows and the login form hides."""
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
  notifications: { rebuilds: [], update: null, limits: [] },
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


def test_app_js_hides_the_empty_row_when_a_request_goes_live(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The live start event re-renders the table, so the no-requests row leaves at once."""
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
  notifications: { rebuilds: [], update: null, limits: [] },
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
  assert.ok(el("requests").innerHTML.includes("No requests"), "the empty row shows 1st");
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


def test_app_js_settings_groups_ship_in_the_defaults() -> None:
  """Every settings group that app.js reads ships in `settings.DEFAULTS`."""
  source = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  reads = set(re.findall(r'(?:setting|fileValue)\("([a-z-]+)"', source))
  reads |= set(re.findall(r'^  \["([a-z-]+)", "[^"]+", \[', source, re.MULTILINE))
  assert reads, "the settings reads of app.js"
  missing = sorted(reads - set(settings.DEFAULTS))
  assert not missing, f"app.js reads settings groups the server never sends: {missing}"


def test_app_js_length_limit_message() -> None:
  """A field that stops at its length tells the rule, so the cut is not silent."""
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
assert(message.textContent.includes('40 characters at most'), message.textContent);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_phone_model_head_keeps_a_tap_area() -> None:
  """A phone head cell is a 44 px sort target, and the whole cell reads as one."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  base_css, mobile_css = css.split("@media (max-width: 720px) {", 1)
  mobile_css = mobile_css.split("\n}", 1)[0]
  assert "th[data-sort] { cursor: pointer; }" in base_css
  assert ".models th { padding: 13px 8px; }" in mobile_css
  assert ".phone-types, .phone-chips { display: contents; }" in mobile_css, (
    "the chips of a dropped Type column join the capability chips"
  )
  assert ".models td.name .cell-value { flex-basis: 100%; }" in mobile_css, (
    "the name keeps its row above the chips"
  )


def test_status_card_sits_in_the_card_grid() -> None:
  """The Overview status card rides in the card grid, at the width of its neighbours."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = html[: html.index('<div class="flow">')]
  assert "status-card" not in head, "the card no longer spans the page"
  flow = html[html.index('<div class="flow">)'.replace(")", "")) :]
  column = flow.split('<div class="column">', 1)[1]
  assert column.index("status-card") < column.index("ov-requests")


def test_requests_reads_model_served_effort() -> None:
  """The Requests table reads Model, Served by, Effort, in the head and in both row kinds."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert html.index(">Served by</th>") < html.index(">Effort</th>")
  js = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  rows = js[js.index("function renderRequests") : js.index("function renderTiers")]
  assert rows.index('"Served by")') < rows.index('cell("Effort"')
  live = js[js.index("function renderLive") : js.index("function tickLive")]
  assert live.index('"Served by")') < live.index('cell("Effort"')


def test_the_requests_table_keeps_the_app_and_the_session() -> None:
  """App and Session show at every width: no band drops a request column."""
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
  """The Stream column hides on phones only, so a desktop table keeps its stream time."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = html.split('title="The stream time after the first token"', 1)[0].rsplit(
    "<th", 1
  )[1]
  assert 'class="hide-sm"' in head, head
  js = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert js.count('cell("Stream", streamCell(r), "hide-sm num")') == 1
  assert "hide-md" not in js, "no width band drops the stream column"


def test_narrow_desktop_header_keeps_one_row() -> None:
  """The Overview card holds the state at every width. A narrow header drops it."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  base, rest = css.split("@media (max-width: 1320px) {", 1)
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


def test_phone_chips_share_one_row() -> None:
  """The modality and capability chips of a card ride one row, at one size."""
  css = Path("daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".phone-types, .phone-chips { display: contents; }" in css, "one row"
  assert ".models td.name .chip {" in css, "one size"
  assert ".phone-types { display: flex" not in css, "no row of its own"
  assert ".phone-chips {\n    display: flex" not in css, "no row of its own"


def test_app_js_type_chips_drop_the_redundant_media_flag() -> None:
  """Speech already says audio out and Transcription says audio in, so that chip goes."""
  code = _app_js_vm(
    """
const speech = sandbox.typeChips({ mode: 'audio_speech', flags: ['audio_output'] });
assert(!speech.includes('title="Audio out"'), speech);
assert(speech.includes('Speech'), speech);
const transcription = sandbox.typeChips({ mode: 'audio_transcription', flags: ['audio_input'] });
assert(!transcription.includes('title="Audio in"'), transcription);
assert(transcription.includes('Transcription'), transcription);
const chat = sandbox.typeChips({ mode: 'chat', flags: ['vision', 'audio_input', 'audio_output'] });
assert(chat.includes('title="Image in"') && chat.includes('title="Audio in"'), chat);
assert(chat.includes('title="Audio out"'), chat);
const mixed = sandbox.typeChips({ mode: 'audio_speech', flags: ['vision', 'audio_output'] });
assert(mixed.includes('title="Image in"') && !mixed.includes('title="Audio out"'), mixed);
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_status_hosts() -> None:
  """The header hosts keep the state chip. The Overview card holds the health, sessions and catalog."""
  code = _app_js_vm(
    """
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert.strictEqual(hosts[0].innerHTML, hosts[1].innerHTML);
assert(!hosts[0].innerHTML.includes('Healthy'), 'the header drops the health');
assert(!hosts[0].innerHTML.includes('chip rebuild'), 'the header drops the catalog chip');
assert(byId.get('card-health').innerHTML.includes('Healthy'), 'the card shows the health');
const card = byId.get('card-rows').innerHTML;
assert(card.includes('Sessions') && card.includes('>2<'), 'the card shows the sessions');
assert(card.includes('Catalog'), 'the card shows the catalog');
assert(card.includes('class="line rebuild"'), 'the catalog line starts a rebuild');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_model_rows_keep_the_type_on_a_phone() -> None:
  """A phone head drops the Type column, so each model row carries its type chips."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
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
assert(html.includes('>Chat<'), 'the mode chip shows');
assert(html.includes('title="Image in"'), 'the media chip shows');
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
    assert(chipRow.includes('title="Context"'), 'the context rides as a phone chip');
    assert(!chipRow.includes('1.00'), 'the weight chip leaves the chip row');
    assert(phone.includes('1.00'), 'the weight row carries the weight');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_client_key_modal() -> None:
  """The client key adder opens the modal with 2 fields: the key name and its value."""
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


def test_app_js_model_marks() -> None:
  """A model name reads `provider/dev/slug`: a mark for each head, then the model part."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + String.fromCharCode(10) + 'globalThis.__probe = { modelName, MARK_FILES };';
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
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_cancelled_status() -> None:
  """A request the client closed shows 499, the nginx code for that case."""
  code = _app_js_vm(
    """
const cell = sandbox.statusCell;
const text = sandbox.statusText;
assert.strictEqual(cell({ cancelled: true }), '<span title="Cancelled">499</span>', 'the code of a closed request');
assert.strictEqual(cell({ cancelled: false, status: 200 }), '<span title="OK">200</span>', 'a normal code');
assert.strictEqual(cell({ cancelled: false, status: 429 }), '<span title="Too many requests">429</span>',
  'a limited request');
assert.strictEqual(cell({ cancelled: false, status: 'err' }), '<span title="The attempt failed">err</span>',
  'a failed request');
assert.strictEqual(cell({ cancelled: false, status: 302 }), '302', 'a code without a note stays plain');
assert.strictEqual(text({ cancelled: true }), 'cancelled', 'the word in the chain text');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_the_mark_files_match_the_icon_folder() -> None:
  """The app.js mark set holds each shipped SVG name, and no other name."""
  source = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  block = source.partition("const MARK_FILES = new Set([")[2].partition("]);")[0]
  assert block, "the mark set is a literal"
  names = set(re.findall(r'"([a-z0-9-]+)"', block))
  files = {path.stem for path in Path("daedalus/dashboard/ui/icons").glob("*.svg")}
  assert files == names, "the set and the folder hold the same names"


def test_app_js_state_chip() -> None:
  """The header says when the state is loading or unknown, and clears the note after an answer."""
  code = _app_js_vm(
    """
sandbox.setStateKnown(null);
assert(hosts[0].innerHTML.includes('Loading the state'), hosts[0].innerHTML);
assert(!hosts[0].innerHTML.includes('chip bad'), 'no alarm before the first answer');
sandbox.setStateKnown(false);
assert(hosts[0].innerHTML.includes('State unknown'), hosts[0].innerHTML);
assert(hosts[0].innerHTML.includes('chip bad'), hosts[0].innerHTML);
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert.strictEqual(hosts[0].innerHTML, '', 'the good answer clears the header');
assert(!hosts[0].innerHTML.includes('State unknown'), 'the good answer clears the note');
assert(!hosts[0].innerHTML.includes('Loading the state'), 'the loading note goes away');
assert(byId.get('card-health').innerHTML.includes('Healthy'), 'the card shows the health');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_top_model() -> None:
  """A pool row names the member that served most, else its catalog first model."""
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
  """Each request cell names its column, so the card layout of a narrow screen shows every value."""
  page = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/index.html"
  ).read_text(encoding="utf-8")
  header = re.search(
    r'<table class="requests" aria-label="Requests" role="table">.*?<thead[^>]*>(.*?)</thead>',
    page,
    re.DOTALL,
  )
  assert header, "the Requests table header"
  labels = [
    re.sub(r"<[^>]+>", "", text).strip()
    for text in re.findall(r"<th[^>]*>(.*?)</th>", header.group(1))
  ]
  assert len(labels) == 11, labels
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
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') +
  "\\nglobalThis.__probe = {{ renderRequests, renderLive," +
  " poolTier, state, modelName, poolOf, routingCodes, chainRows }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false,
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
assert.deepStrictEqual(mobileLabels(html), {json.dumps(labels)}, 'the cells show their column names in table order');
assert(html.includes('hide-sm num mono'), 'the session id reads in the mono font');
assert(html.includes('class="request has-chain"'), 'requests with fallbacks expose their chain');
assert(html.includes('View fallback chain · 1 fallback'), 'the chain details disclose the fallback count');
assert(probe.chainRows({json.dumps(row)}).includes('Fallback chain · 1 fallback'),
  'the desktop chain details carry the count');
assert(probe.chainRows({json.dumps({**row, "fallbacks": 3})}).includes('Fallback chain · 3 fallbacks'),
  'the count keeps its plural');
assert(!probe.chainRows({json.dumps({**row, "fallbacks": None})}).includes('Fallback chain ·'),
  'a row with no count keeps the plain head');
// The pool of the auto model rides in the slug, and the daedalus head carries its own mark.
const autoCell = probe.modelName('daedalus/auto', probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}));
assert.strictEqual(probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}), 'moros', 'the auto pool joins the slug');
assert(autoCell.includes('<span class="model-part">auto</span>') &&
  autoCell.includes('<span class="model-part">moros</span>'), 'the slug reads daedalus/auto/moros');
assert(autoCell.includes('ui/icons/daedalus.svg'), 'the daedalus head carries its own mark');
assert.strictEqual(probe.poolOf({{ model: 'p/big', pool: 'daedalus/deinos' }}), '',
  'a direct model keeps its own slug');
assert.strictEqual(probe.routingCodes({{ routed: 'deinos' }}).includes('fr'), true, 'the fallback tier code stays');
for (const value of ['View fallback chain', 's1', 'high <span class="from">xhi</span>', 'rate limited', 'frA',
  'tl3']) assert(html.includes(value), 'the details keep ' + value);
assert(!html.includes('rt2') && !html.includes('tool loop'), 'the frX and tlN codes replace the long forms');
const mobileSelectors = [];
const mobileTap = {{ target: {{ closest: (selector) => {{ mobileSelectors.push(selector); return null; }} }} }};
sandbox.window.matchMedia = () => ({{ matches: true }});
node('requests').listeners.click(mobileTap);
assert(!mobileSelectors.includes('tr.request'), 'a mobile row tap does not open a second chain');
const detailSelectors = [];
const detailTap = {{ target: {{ closest: (selector) => {{ detailSelectors.push(selector); return selector ===
  '.mobile-fallback-chain' ? {{}} : null; }} }} }};
sandbox.window.matchMedia = () => ({{ matches: false }});
node('requests').listeners.click(detailTap);
assert(!detailSelectors.includes('tr.request'), 'a detail disclosure does not toggle the desktop chain');
probe.state.live.set(2, {{ id: 2, since: 100000, attemptSince: 100000, first: null, stream: false, session: 's2',
  app: 'OWUI', model: 'daedalus/auto', effort: 'medium', pool: 'moros', via: 'p/live', fallbacks: 0 }});
probe.renderLive();
const liveHtml = node('live').innerHTML;
const liveCells = [...liveHtml.matchAll(/<td[^>]*>/g)].map((m) => m[0]);
assert.strictEqual(liveCells.length, 12, 'every live column is a cell');
assert(liveCells.every((td) => td.includes('role="cell"')), 'each live request cell keeps its table role');
assert.strictEqual((liveHtml.match(/class="cell-value"/g) || []).length, 11,
  'every live value cell keeps its value span');
assert.deepStrictEqual(mobileLabels(liveHtml), {json.dumps(labels)}, 'live request cells show their column names');
assert(liveHtml.includes('<span class="mobile-fallback-count">0 fallbacks</span>'),
  'the live row reads the fallback count alone');
for (const value of ['s2', 'medium', '<span class="model-part">moros</span>']) assert(liveHtml.includes(value),
  'live details keep ' + value);
assert(!liveHtml.includes('mobile-fallback-chain'), 'live rows do not show a fallback chain');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_live_served_by_shows_the_trying_marks() -> None:
  """A live row that still tries a model shows its marks, not the raw slug."""
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
const servedBy = html.slice(html.indexOf('Served by'));
assert(servedBy.includes('trying'), 'the cell keeps the trying word');
assert(servedBy.includes('ui/icons/cloudflare.svg'), 'the trying model shows its provider mark');
assert(servedBy.includes('ui/icons/zai-org.svg'), 'the trying model shows its developer mark');
assert(servedBy.includes('<span class="model-part">glm-4.7-flash</span>'), 'the model part reads short');
assert(!servedBy.includes('trying cloudflare/@cf/zai-org/glm-4.7-flash'),
  'the raw slug never stands in place of the marks');
""",
  )


def test_app_js_live_clocks_wait_for_the_first_token() -> None:
  """The stream clock stays empty until the first token, also on a request without a stream."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { tickLive, state };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', children: [], listeners: {}, classList: { add: () => {},
    remove: () => {}, toggle: () => {} }, addEventListener(type, handler) { this.listeners[type] = handler; } });
  return nodes.get(id);
};
const ttft = { textContent: '' };
const stream = { textContent: '' };
const live = {
  innerHTML: '', children: [{ dataset: { live: '2' }, querySelector: (selector) =>
    selector.includes('ttft') ? ttft : stream }],
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


def test_phone_panels_clear_the_last_row() -> None:
  """The bottom of a panel clears its last row on a phone."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  mobile = css.split("@media (max-width: 720px) {", 1)[1].split("\n}", 1)[0]
  assert "#app > main { padding-bottom: 76px; }" in mobile, "the screen bottom clears"
  assert ".panel { padding-bottom: 8px; }" in mobile, "the panel edge clears"


def test_a_card_closes_on_the_room_of_its_rows() -> None:
  """The bottom of a Settings or Provider card keeps the room of 1 row, not the card padding."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    'section[data-page="settings"] .card,\n'
    'section[data-page="providers"] .card { padding-bottom: 10px; }'
  ) in css, "the last row of a card keeps the room of a row at the bottom edge"


def test_keys_page_holds_its_labels_on_one_line() -> None:
  """The keys table keeps the key value and the Delete label whole on a phone."""
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


def test_the_version_reads_in_the_mono_font() -> None:
  """The version in the header and on the login screen uses the mono font."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".mono { font-family: var(--mono); }" in css, "the mono class exists"
  brand = css.split(".brand small {", 1)[1].split("}", 1)[0]
  assert "font-family: var(--mono);" in brand, "the header version"
  assert (
    "font-family: var(--mono);"
    in css.split(".login-head small {", 1)[1].split("}", 1)[0]
  ), "the login version"


def test_the_short_values_carry_their_full_text() -> None:
  """Every Requests head and each short value keeps its full text on hover."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = page.split('<table class="requests"', 1)[1].split("</thead>", 1)[0]
  assert head.count('title="') == 11, "each Requests head carries a hover text"
  for note in (
    "When the request started",
    "The client app, from its request headers",
    "The chat session that picked the model",
    "The model asked for; the pool rides in the slug of an auto route",
    "The reasoning effort asked of the model",
    "The model that answered, after any fallback",
    "The status; 499 marks a request the client closed",
    "Input tokens. ~ marks an estimate.",
    "Output tokens from the provider",
    "Time to the first token",
    "The stream time after the first token",
  ):
    assert f'title="{note}"' in head, note
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'title="${m.max_input_tokens.toLocaleString()} tokens"' in app, (
    "the context count"
  )
  assert 'title="${esc(m.tier)}"' in app, "the tier name"
  assert (
    "The largest context of a pool model, ${pool.context.toLocaleString()} tokens"
    in app
  ), "the pool context count"
  assert 'title="Only the start of a saved key is kept"' in app, "the key start"
  assert (
    'const STATUS_NOTES = { 200: "OK", 429: "Too many requests", 500: "Upstream error", err: "The attempt failed" };'
    in app
  ), "the status meanings"
  assert (
    'title="${r.remaining.toLocaleString()} of ${r.limit.toLocaleString()}"' in app
  ), "the exact rate-limit counts"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    ".ghost.danger:hover { color: var(--bad); border-color: var(--bad); }" in css
  ), "a filled danger button keeps its label on hover"
  assert ".primary:hover:not(:disabled) { filter: brightness(1.2);" in css, (
    "the primary button answers the mouse"
  )
  assert (
    ".filter:hover, .tab:hover { color: var(--text); border-color: var(--muted); }"
    in css
  ), "the tier filters and the view tabs answer the mouse"
  assert "text-decoration: underline; }" in css, "a sort head marks its own click"
  assert "button.line.rebuild:hover { color: var(--accent); }" in css, (
    "the phone catalog line answers the mouse"
  )
  assert ".mobile-fallback-chain > summary:hover" in css, (
    "the phone chain summary answers the mouse"
  )


def test_the_parallel_race_reads_on_the_page() -> None:
  """The race carries a code and a ladder mark, and its legend row waits for the setting."""
  app = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert 'rce: "A racing model took the pin"' in app, "the race code"
  assert 'code !== "rce" || raceOn()' in app, "its legend row waits for the setting"
  assert 'a.race === "won"' in app and '<span class="from">won race</span>' in app, (
    "the winning step carries the mark"
  )
  assert "affinity?.mode" in app and 'line("Affinity"' in app, (
    "the status card shows the applied affinity mode"
  )
  assert (
    "RACE_NOTES" in app and 'class="chain-race"' in app and "raceCode" not in app
  ), "the race decision reads inside the fallback chain, not in the model cell"
  assert 'slow: "slow pin"' in app and 'drawn: "on draw"' in app, (
    "the chain note reads as the short label"
  )


def test_the_requests_head_keeps_its_rule_while_it_sticks() -> None:
  """A collapsed border leaves a sticky head, so an inset shadow keeps the rule."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\.requests thead th \{([^}]*)\}", css)
  assert rule and "position: sticky" in rule.group(1), "the head sticks"
  assert "border-bottom: 0" in rule.group(1), "the collapsed border goes"
  assert "box-shadow: inset 0 -1px 0 var(--line)" in rule.group(1), (
    "the rule rides on the inset shadow"
  )


def test_model_heads_align_left() -> None:
  """Every Models head aligns left, while its cells keep the center."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = page.split('<tr id="model-head">', 1)[1].split("</tr>", 1)[0]
  assert "mid" not in head, "no Models head centers or right-aligns"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".models .mid { text-align: center; }" in css, "the cells keep the center"


def test_mobile_request_cards_use_route_first_grid() -> None:
  """Compact mobile cards keep route, metrics, and details on separate rows."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  base_css, mobile_css = css.split("@media (max-width: 720px) {", 1)
  mobile_css = mobile_css.split("\n}", 1)[0]
  narrow_css = css.split("@media (max-width: 360px) {", 1)[1].split("\n}", 1)[0]
  assert ".requests td.fallbacks-cell { display: none; }" in base_css, (
    "the desktop table reads the fallback count in the chain of the row"
  )
  assert "tr.request.has-chain { cursor: pointer; }" in base_css
  assert "tr.request.has-chain.open td { background: var(--field); }" in base_css
  assert (
    "@media (hover: hover) {\n  tr.request.has-chain:hover td "
    "{ background: var(--field); }\n}" in base_css
  ), "the row hover needs a pointer, a touch tap keeps no background"
  assert ".requests tr.request, .requests tr.live-row {" in mobile_css
  assert (
    "grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr) auto" in mobile_css
  ), (
    "the stamp fits its text, the client tag fills the rest, the status badge fits its own width"
  )
  assert (
    ".requests tr.request > td:nth-child(4), .requests tr.live-row > td:nth-child(4) "
    "{ grid-column: 1 / -1; grid-row: 2; }"
  ) in mobile_css
  assert (
    ".requests tr.request > td:nth-child(6), .requests tr.live-row > td:nth-child(6) "
    "{ grid-column: 1 / -1; grid-row: 3; }"
  ) in mobile_css, "the served model takes its own row under the model"
  assert (
    ".requests tr.request > td:nth-child(7), .requests tr.live-row > td:nth-child(7) {\n"
    "    grid-column: 4; grid-row: 1; justify-content: flex-end;\n"
    "  }"
  ) in mobile_css, "the status is the badge of the first row"
  assert (
    ".requests tr.request > td:nth-child(8), .requests tr.live-row > td:nth-child(8) "
    "{ grid-column: 1 / 3; grid-row: 4; }"
  ) in mobile_css, "the input count pairs with the output count"
  assert (
    ".requests tr.request > td:nth-child(9), .requests tr.live-row > td:nth-child(9) "
    "{ grid-column: 3 / 5; grid-row: 4; }"
  ) in mobile_css, "the output count shares the row with the input count"
  assert (
    ".requests tr.request > td:nth-child(10), .requests tr.live-row > td:nth-child(10) "
    "{ grid-column: 1 / 3; grid-row: 5; }"
  ) in mobile_css, "the TTFT pairs with the stream time"
  assert (
    ".requests tr.request > td:nth-child(3), .requests tr.live-row > td:nth-child(3) "
    "{ grid-column: 1 / 3; grid-row: 6; }"
  ) in mobile_css, "the session id takes the first column of its own row"
  assert (
    ".requests tr.request > td:nth-child(5), .requests tr.live-row > td:nth-child(5) "
    "{ grid-column: 3 / 5; grid-row: 6; }"
  ) in mobile_css, "the effort follows the session"
  assert (
    ".requests tr.request > td.fallbacks-cell, .requests tr.live-row > td.fallbacks-cell {\n"
    "    display: block; grid-column: 1 / -1; grid-row: 7;\n"
    "  }"
  ) in mobile_css, "the chain details take the last row"
  assert (
    ".requests tr.request > td.fallbacks-cell.no-details,\n"
    "  .requests tr.live-row > td.fallbacks-cell.no-details { display: none; }"
  ) in mobile_css, "a card with no details keeps no empty row"
  assert (
    ".requests tr.request:active td, .requests tr.live-row:active td "
    "{ background: transparent; }"
  ) in mobile_css, "a press paints no patch behind a selection"
  assert (
    ".requests tr.request:active, .requests tr.live-row:active { background: var(--field); }"
  ) in mobile_css, "a press paints the field of the whole card"
  assert (
    ".requests tr.request > td:nth-child(1) .cell-value,\n"
    "  .requests tr.live-row > td:nth-child(1) .cell-value "
    "{ font-size: inherit; white-space: nowrap; }"
  ) in mobile_css, "the stamp holds 1 line at its own width"
  assert (
    ".models tr, .keys tr, .requests tr.request, .requests tr.live-row "
    "{ font-size: 13px; }"
  ) in mobile_css, "1 text size holds every phone card"
  assert "user-select" not in mobile_css, "the phone card keeps the text selection"
  assert (
    ".requests .mobile-fallback-chain ol { list-style: none; margin: 5px 0 0; "
    "padding: 0; }"
  ) in mobile_css
  assert ".requests tr.request .caret { display: none; }" in mobile_css
  assert ".requests .mobile-request-meta" not in mobile_css, (
    "the details drop the 3 row list"
  )
  assert ".requests .mobile-request-more" not in mobile_css, (
    "the more disclosure is gone"
  )
  assert ".requests tr.live-row > td:nth-child(1) .pulse" in narrow_css
  assert "grid-column" not in narrow_css, (
    "the pair rows already fit a narrow card, so no row steps down again"
  )


def test_requests_hover_rules_need_a_pointer() -> None:
  """A tap on a phone keeps no hover look: each Requests hover rule waits for a pointer."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  gated = "\n".join(
    re.findall(r"@media \(hover: hover\) \{\n(.*?)\n *\}", css, re.DOTALL)
  )
  bare = re.sub(r"@media \(hover: hover\) \{\n.*?\n *\}", "", css, flags=re.DOTALL)
  rules = (
    ".ghost:hover { color: var(--text); border-color: var(--muted); }",
    "tr.request.has-chain:hover td { background: var(--field); }",
    ".requests .mobile-fallback-chain > summary:hover",
  )
  for rule in rules:
    assert rule in gated, f"{rule} needs a pointer device"
    assert rule not in bare, f"{rule} stays outside the pointer gate"


def test_app_js_modal() -> None:
  """A confirmation opens the modal: Confirm resolves true, and any other close resolves false."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "confirm(" not in source, "the dashboard uses the modal, not window.confirm"
  assert "prompt(" not in source, (
    "the New provider flow uses the modal, not window.prompt"
  )
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { ask, state };";
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
  """Each absolute time carries its date, and the catalog times read relative."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "shortTime(" not in source and "dateTime(" not in source, (
    "one helper stamps each time"
  )
  assert "&middot; Next" in source, "the catalog label reads Next"
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { stamp, relative };";
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
const assert = require('assert');
const probe = sandbox.__probe;
const when = new Date(2026, 9, 4, 12, 30, 46);
assert.strictEqual(probe.stamp(when.getTime() / 1000), '2026-10-04 12:30:46', 'the stamp of an absolute time');
const now = Math.floor(Date.now() / 1000);
assert.strictEqual(probe.relative(now - 7200), '2 hours ago');
assert.strictEqual(probe.relative(now + 7200), 'in 2 hours');
assert.strictEqual(probe.relative(now - 600), '10 mins ago');
assert.strictEqual(probe.relative(now + 600), 'in 10 mins');
assert.strictEqual(probe.relative(now - 10), 'just now');
assert.strictEqual(probe.relative(now - 3600), '1 hour ago');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_retry_code() -> None:
  """The row shows the repeat code of the hook as given, and the legend lists rtN."""
  source = Path("daedalus/dashboard/ui/app.js").read_text()
  assert "const REPEAT_CODE = " in source, "the repeat code family"
  assert "function renderLegend(extra = state.legendExtra) {" in source, (
    "the hook rows join the base rows, and the race row waits for the setting"
  )
  assert "...extra," in source, "the hook rows show below the base rows"
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { transitionCell };";
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
const cell = sandbox.__probe.transitionCell;
assert.ok(cell({ reason: 'rt2' }).includes('>rt2<'), 'the hook code shows as given');
assert.ok(cell({ reason: 'rt2' }).includes('A repeat picked another model'), 'the tooltip is generic');
assert.ok(cell({ reason: 'lmt' }).includes('>lmt<'), 'another reason keeps its own code');
assert.strictEqual(cell(null), '', 'no transition, no code');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_limit_units() -> None:
  """A limit row names its model on 1 line and shows a short unit such as TPM."""
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
const src = fs.readFileSync('daedalus/dashboard/ui/app.js',
  'utf8') + "\\nglobalThis.__probe = {{ renderLimits, state }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false,
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
const assert = require('assert');
sandbox.__probe.renderLimits({json.dumps(payload)});
const html = node('limit-rows').innerHTML;
assert(html.includes('<td role="cell" class="name" title="mistral/mistral-embed">'), 'the model cell keeps 1 line');
assert(html.includes('>TPM</td>'), 'tokens per minute');
assert(html.includes('>RPM</td>'), 'requests per minute');
assert(html.includes('>RPD</td>'), 'requests per day');
assert(html.includes('>requests</td>'), 'a row without a window keeps its kind');
assert(html.includes('title="Tokens per minute"'), 'the hover text spells the unit out');
assert(html.includes('title="Requests"'), 'a row without a window gets the kind alone');
assert(!html.includes('>per '), 'the cell text stays short');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_limit_columns_stay_compact() -> None:
  """Limit model names ellipsize, while short unit labels stay on one line."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  model_cell = re.search(r"#limit-rows td\.name\s*\{([^}]*)\}", css)
  limit_cell = re.search(r"#limit-rows td:nth-child\(2\)\s*\{([^}]*)\}", css)
  mobile_css = css.split("@media (max-width: 720px) {", 1)[1]
  mobile_weight = re.search(r"#limit-rows \.weight\.left\s*\{([^}]*)\}", mobile_css)
  assert model_cell and "text-overflow: ellipsis" in model_cell.group(1)
  assert model_cell and "white-space: nowrap" in model_cell.group(1)
  assert limit_cell and "white-space: nowrap" in limit_cell.group(1)
  assert mobile_weight and "min-width: 0" in mobile_weight.group(1)


def test_the_overview_pool_rows_draw_the_marks_of_the_top_model() -> None:
  """The Overview Models card draws the top model with the mark markup of the Requests table."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  row = re.search(
    r'draw\("ov-models", state\.pools\.map\(\(pool\) => \{.*?'
    r'top \? modelName\(top\.id\) : "no models"',
    app,
    re.DOTALL,
  )
  assert row, "the pool row draws the marks of the top model"
  assert "top ? esc(top.id)" not in app, "no pool row keeps the plain model id"


def test_requests_time_column_keeps_its_width() -> None:
  """The time column holds 19 chars, and Model and Served by land on 1 width."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  desktop = css.split("@media (min-width: 721px) {", 1)[1]
  hint = re.search(
    r"\.requests tr:not\(\.chain\) > th, \.requests tr:not\(\.chain\) > td \{ width: 1%; \}",
    desktop,
  )
  assert hint, "the other columns keep the width of their content"
  free = re.search(
    r"\.requests tr:not\(\.chain\) > th:nth-child\(4\), \.requests tr:not\(\.chain\) > td:nth-child\(4\),\n"
    r"\s+\.requests tr:not\(\.chain\) > th:nth-child\(5\), \.requests tr:not\(\.chain\) > td:nth-child\(5\) \{\n"
    r"\s+width: 30%; max-width: 30%;",
    css,
  )
  assert free, "the model and served by columns share the free width"
  shared = re.search(
    r"\.requests tr:not\(\.chain\) > td\.name > \.cell-value \{\n"
    r"\s+display: inline-block; width: 28ch; overflow: hidden; text-overflow: ellipsis;",
    desktop,
  )
  assert shared, "both name columns cap the name at the same width"
  assert (
    ".requests td.name { max-width: 16rem; overflow: hidden; text-overflow: ellipsis; }"
    in css
  ), "a name past its share ends in an ellipsis"
  phone = css.split("@media (max-width: 720px) {", 1)[1]
  assert (
    "display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr) auto;"
    in phone
  ), "the phone row stays a grid"
  assert "width: 1%" not in phone, "the phone cells keep their grid tracks"


def test_each_model_filter_pick_moves_the_rows_only() -> None:
  """A search, a select or a tier of the Models tab replays the table, not the page."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "function pickRows() {" in app and 'replay($("models"));' in app, (
    "the pick replays the table"
  )
  for handler in (
    '$("search").addEventListener("input", pickRows);',
    '$("sort-small").addEventListener("change", () => {',
    '$("mode").addEventListener("change", () => {',
    '$("tiers").addEventListener("click", (event) => {',
  ):
    assert handler in app, handler
  # The handlers of this tab never call the plain draw, so no pick moves the page.
  toolbar = app[app.index('$("search").addEventListener') :]
  toolbar = toolbar[: toolbar.index("$('files')") if "$('files')" in toolbar else 4000]
  assert "renderModels();" not in toolbar, "each pick goes through pickRows"


def test_the_live_switch_reads_blue_live_and_gray_paused() -> None:
  """The switch says Paused, and blue marks the live stream while gray marks the pause."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert (
    '? `Paused${state.liveWaiting.size ? ` · ${state.liveWaiting.size} new` : ""}`'
    in app
  )
  assert "Live: paused" not in app, "the label is just Paused"
  assert (
    '.live-toggle[aria-pressed="false"] { border-color: var(--accent); color: var(--accent); }'
    in css
  ), "blue reads Live"
  assert '.live-toggle[aria-pressed="true"]' not in css, (
    "gray reads Paused, from the ghost look"
  )
  assert 'aria-pressed="false"' in page, "the first paint reads Live"


def test_the_motion_reaches_the_modal_the_sections_and_the_switches() -> None:
  """The modal, a section change, a file change and a switch carry the page motion."""
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
  assert 'replay([...host.querySelectorAll(".section-pane > .card")]' in app, (
    "the picked section card arrives"
  )
  assert (
    ".section-pane > .card.drawn { animation: fade var(--soft) var(--ease); }" in css
  ), "the card fades, so the scroll pane never clips its top"
  assert 'replay($("provider-form"));' in app, "a file change arrives its form"
  assert "if (!off) replay(field);" in app, "the rows of a switch arrive"
  assert (
    ".section-pane.slide { animation: slide-in var(--soft) var(--ease); }" in css
  ), "the phone slides the pane in"
  assert ".sections.slide { animation: slide-back var(--soft) var(--ease); }" in css, (
    "and the list back"
  )
  assert "@keyframes slide-in" in css and "@keyframes slide-back" in css


def test_the_settings_sections_carry_a_rule() -> None:
  """Each section of a Settings card separates from the next, the title header included."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert 'section[data-page="settings"] .card > h3, .card.provider > h3 {' in css, (
    "the title header carries a rule too"
  )
  assert (
    "padding-bottom: 8px; border-bottom: 1px solid var(--line); margin-bottom: 0;"
    in css
  )
  assert ".card > h3 + .field { border-top: 0; }" in css, (
    "the first row of a card keeps no rule of its own"
  )
  assert ".field:first-of-type" not in css, (
    "the rule follows the row order, not the tag"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '["balance", "Load Distribution", [' in app, (
    "the group reads as Load Distribution"
  )
  assert "<h3>API Keys</h3>" in app, "the keys card is title case"
  assert '[["keys", "API Keys"], ["yaml", "YAML"]]' in app, (
    "the keys section is title case"
  )


def test_the_providers_back_keeps_its_slide() -> None:
  """A phone back from a Providers section slides: the form renders before the pick lands."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  body = app[
    app.index("function showPage()") : app.index('window.addEventListener("hashchange"')
  ]
  render = body.index(
    'if (page === "providers" && state.view === "form") renderForm();'
  )
  pick = body.index("applySectionHash();")
  assert render < pick, "the form renders before the section pick"
  assert "slideAgain();" in body[pick:], (
    "the slide of the pick replays on the fresh markup"
  )
  assert "function playSlide(host, open) {" in app, "1 helper plays the arriving slide"
  assert "state_.slide = true;" in app, (
    "a pick holds its slide for the render that follows"
  )
  assert 'for (const host of [$("settings"), $("provider-form")]) {' in app, (
    "both pages replay their slide"
  )


def test_the_requests_bar_keeps_its_line_on_a_filter_pick() -> None:
  """A filter pick holds the toolbar: the count hint never drops to a second row."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  desktop = css.split("@media (min-width: 721px) {", 1)[1]
  assert (
    'section[data-page="requests"] .requests-bar { flex-wrap: nowrap; }' in desktop
  ), "the bar keeps 1 row"
  assert (
    'section[data-page="requests"] .requests-bar input[type="search"] '
    "{ flex: 1 1 8rem; min-width: 0; }" in desktop
  ), "the search field gives up its width first"
  assert (
    'section[data-page="requests"] .requests-bar .hint { white-space: nowrap; }'
    in desktop
  ), "the count hint holds its own line"


def test_header_state_matches_the_page_switcher() -> None:
  """The header holds plain text: no chip border, and the step of the page switcher."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\.chips \.chip, \.chips \.ghost \{([^}]*)\}", css)
  assert rule and "border: 0" in rule.group(1)
  assert "border-radius: 0" in rule.group(1) and "font-size: 14px" in rule.group(1)
  assert ".tabs { grid-area: tabs; display: flex; gap: 24px" in css
  assert "#status { padding: 0; gap: 24px; }" in css
  assert "grid-area: chips; display: flex; gap: 32px" in css, (
    "Log out stands a step apart from the status group"
  )


def run_app_js(probe: str, body: str) -> None:
  """Run a node snippet over `app.js` with a small DOM stand-in, and the named probes on `__probe`."""
  code = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = {{{probe}}};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) {{
    const made = {{ id, innerHTML: '', value: '', checked: false, textContent: '', disabled: false, hidden: false,
      dataset: {{}}, handlers: {{}},
      isConnected: true, title: '', type: '',
      addEventListener: (type, fn) => {{ (made.handlers[type] ||= []).push(fn); }},
      remove: () => {{}}, append: () => {{}}, prepend: () => {{}}, after: () => {{}}, setAttribute: () => {{}},
      classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }},
        closest: (sel) => (sel === '[data-section]' ? null : made),
      querySelector: () => null, querySelectorAll: () => [] }};
    nodes.set(id, made);
  }}
  return nodes.get(id);
}};
const sandbox = {{
  esc: (text) => String(text ?? ''),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{
    hidden: false,
    documentElement: {{ dataset: {{}} }},
    getElementById: node,
    createElement: (tag) => node('made-' + tag),
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }}, closest: () => null,
      querySelector: () => null, append: () => {{}}, prepend: () => {{}}, remove: () => {{}} }}),
    querySelectorAll: () => ([]),
    addEventListener: () => {{}},
  }},
  navigator: {{}},
  location: {{ hash: '' }},
  window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}),
  console: {{ error: () => {{}} }},
  $: node,
}};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const probe = sandbox.__probe;
{body}
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_keeps_the_scroll_of_the_settings_pane() -> None:
  """A rebuild of the settings card puts the pane back, so an update holds the reader's place."""
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
const pane = {{ scrollTop: 0 }};
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


def test_app_js_settings_switches() -> None:
  """Every boolean setting renders as a switch, so a loaded file reports no change."""
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
node('set-personalization-theme').value = 'system';
node('set-personalization-time_format').value = '24h';
assert.strictEqual(JSON.stringify(probe.settingsChanges()), '{{}}', 'a loaded file reports no change');
// The keyword fields are chip lists with a + adder, not a text box.
const list = html.slice(Math.max(0, html.indexOf('id="set-routing-escalation"') - 40),
  html.indexOf('id="set-routing-escalation"') + 2600);
assert(list.includes('class="pills"'), 'the keyword field is a chip list');
assert(list.includes('class="pill"'), 'each keyword is a chip');
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
// A new value joins the list 1 time.
probe.setListValue('routing', 'switch', [...probe.listValue('routing', 'switch'), 'clanker', 'clanker']);
assert.strictEqual(probe.listValue('routing', 'switch').length,
  probe.state.settings.defaults.routing.switch.length + 2, 'the raw list takes both');
""",
  )


def test_app_js_hook_point_chips() -> None:
  """The hooks table shows the surfaces each file names in its block, and nothing edits them."""
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
    "state, renderSettings",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(!html.includes('id="set-hooks-on-request"'), 'the surface section left the card body');
assert(html.includes('<th>Surfaces</th>'), 'the table names its surface column');
assert(html.includes('class="pill on">On request'), 'the surface the file names reads on');
assert(html.includes('class="pill off">On chunk'), 'a file that the switch turned off reads dim');
assert(html.includes('<em class="none">No surface</em>'), 'a file that names no surface reads none');
assert(!html.includes('data-hook-surface-pick'), 'nothing adds a surface');
assert(!html.includes('data-hook-surface-drop'), 'nothing takes a surface off');
assert(!html.includes('class="menu"'), 'no surface list opens');
assert(html.includes('>bundled<'), 'a file with no source reads bundled');
""",
  )


def test_app_js_a_file_change_leaves_the_pick_of_the_other_file() -> None:
  """A file switch opens the first section of the new file, so the pick of the other file does not ride over."""
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
    "state, takeFiles, renderForm, pickSection, openFile",
    f"""
probe.takeFiles({json.dumps(files)});
probe.state.file = 0;
probe.renderForm();
// The user of a phone opens the openrouter section of openrouter.yml as a page.
probe.pickSection(node('provider-form'), 'openrouter', true);
// Then the user switches the file to free.yml.
probe.openFile(1);
const html = node('provider-form').innerHTML;
const list = html.slice(html.indexOf('class="sections"'), html.indexOf('class="section-pane"'));
assert(list.includes('data-section="cloudflare" class="on"'), 'the new file opens its first section');
assert(!list.includes('data-section="openrouter" class="on"'), 'the pick of the other file does not ride over');
assert.strictEqual(node('provider-form').dataset.detail, '0', 'the phone returns to the section list');
assert(html.includes('The file text of free.yml'), 'the yaml card names the new file');
""",
  )


def test_hook_chip_matches_the_keyword_chips() -> None:
  """A hook row uses the keyword pill, and its file list draws in the page, on the page theme."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  chip = re.search(r"\.pill\.hook \.pick \{([^}]*)\}", css)
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
  assert 'const settingPill = (group, key, value, index) => `<span class="pill">' in app
  markup = re.search(r"const hookFormRow = .*?\n};", app, re.DOTALL)
  assert markup, "the hook row builds 1 chip"
  assert 'class="pill hook"' in markup.group(0), "the row rides in a chip"
  assert 'title="Pick a hook file"' in markup.group(0), "the chip names its picker"
  assert 'role="listbox"' in markup.group(0), "the list names its role"
  assert 'role="option"' in app and "aria-selected=" in app, "each choice is an option"
  assert "data-hook-select" not in app, "no system menu on a chip"


def test_app_js_affinity_modes_hide_their_rows() -> None:
  """The affinity card shows the pin rows under session and race, and the race rows only under race."""
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
// A new pick moves the rows of the card at once, and the file keeps its own mode.
probe.state.settings.file.affinity = {{ mode: 'session' }};
for (const pick of ['none', 'race', 'session']) {{
  const select = node('set-affinity-mode');
  select.value = pick;
  node('settings').handlers.input.forEach((fn) => fn({{ target: select }}));
  assert.strictEqual(select.value, pick, 'the pick stays after a change to ' + pick);
  assert.deepStrictEqual(shown(), expect[pick], pick + ' moves the rows');
  assert.strictEqual(probe.state.settings.file.affinity.mode, 'session', 'the file is not written back');
}}
// A pick that differs from the file reaches the write payload of that 1 row.
const select = node('set-affinity-mode');
select.value = 'race';
node('settings').handlers.input.forEach((fn) => fn({{ target: select }}));
assert.strictEqual(probe.settingsChanges().affinity.mode, 'race', 'the pick reaches the save payload');
""",
  )


def test_app_js_switch_rows_grey_the_card() -> None:
  """A card whose first row is an On switch greys and disables its other rows while the switch is off."""
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
  'set-balance-rate_limit', 'set-optimization-timeout'];
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
assert(!off('set-affinity-idle') && !disabled('set-affinity-idle'), 'the affinity pin pick greys nothing');
// The form listens for the flip, so the card greys on the click without a save.
node('set-balance-weights').checked = true;
node('set-optimization-enabled').checked = true;
probe.showSwitchRows();
node('set-balance-weights').checked = false;
node('set-balance-weights').type = 'checkbox';
node('settings').handlers.input.forEach((fn) => fn({{ target: node('set-balance-weights') }}));
assert(off('set-balance-success'), 'the flip greys the card at once');
assert(!off('set-optimization-timeout'), 'and leaves the other card alone');
""",
  )


def test_mode_conventions() -> None:
  """Each mode has a label in the Models table and an option in the Type filter."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  html = Path("daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  for mode in sorted({"chat", *base.OUTPUT_MODES.values()}):
    assert re.search(rf"\b{mode}: \"", app), f"{mode} has no label"
    assert f'value="{mode}"' in html, f"{mode} has no Type filter option"


def test_pages_not_nested(client: TestClient) -> None:
  """No page section is inside another, because a hidden parent hides the child."""
  from html.parser import HTMLParser

  found: list[tuple[str, int]] = []

  class Sections(HTMLParser):
    depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
      if tag == "section":
        found.append((dict(attrs).get("data-page") or "", self.depth))
        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
      if tag == "section":
        self.depth -= 1

  Sections().feed(client.get("/").text)
  assert len(found) == 7 and all(depth == 0 for _, depth in found), found


def test_login_form(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
  """The login hints fill in admin, show the master key note, and say whether the cookie is live."""
  env = {dashboard.DAEDALUS_USERNAME: "owner", dashboard.DAEDALUS_PASSWORD: "secret"}
  cases = [
    ({}, "admin", True),
    (env, None, False),
    ({dashboard.DAEDALUS_USERNAME: "owner"}, None, True),
  ]
  for values, username, master in cases:
    for name in env:
      monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
      monkeypatch.setenv(name, value)
    hints = TestClient(api.app)
    found = hints.get("/ui/api/login").json()
    assert found == {
      "username": username,
      "master": master,
      "version": daedalus.__version__,
      "session": False,
    }, (values, found)
    monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
    login = {
      "username": values.get(dashboard.DAEDALUS_USERNAME, "admin"),
      "password": values.get(dashboard.DAEDALUS_PASSWORD, MASTER),
    }
    assert hints.post("/ui/api/login", json=login).status_code == 200, (
      "the cookie is set"
    )
    assert hints.get("/ui/api/login").json()["session"] is True, (
      "the hint sees the cookie"
    )
    monkeypatch.delenv(dashboard.DAEDALUS_MASTER_KEY, raising=False)


def test_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv(dashboard.DAEDALUS_MASTER_KEY, raising=False)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 503, "no master key"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, "short")
  assert client.post("/ui/api/login", json=login).status_code == 503, "a short key"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  assert client.get("/ui/api/status").status_code == 401, "no session"
  wrong = {"username": "admin", "password": MASTER + "x"}
  assert client.post("/ui/api/login", json=wrong).status_code == 401
  other = {"username": "root", "password": MASTER}
  assert client.post("/ui/api/login", json=other).status_code == 401
  assert client.post("/ui/api/login", content=b"[").status_code == 400
  answer = client.post("/ui/api/login", json=login)
  assert answer.status_code == 200, answer.text
  header = answer.headers["set-cookie"].lower()
  assert "httponly" in header and "samesite=strict" in header, header
  assert "max-age" not in header, "a browser-session cookie without remember me"
  status = client.get("/ui/api/status")
  assert status.status_code == 200, "the session cookie"
  assert status.json()["version"] == daedalus.__version__, status.json()
  value = client.cookies.get(dashboard.COOKIE)
  expires, _, signed = value.partition(".")
  forged = f"{int(expires) + 60}.{signed}"
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: forged}).status_code == 401
  )
  old = dashboard.cookie(
    dashboard.secret(), time.time() - dashboard.SESSION_SECONDS - 1
  )
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: old}).status_code == 401
  )
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER + "-new")
  assert client.get("/ui/api/status").status_code == 401, "a new master key ends it"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  assert client.post("/ui/api/login", json=login).status_code == 200
  monkeypatch.setenv(dashboard.DAEDALUS_USERNAME, "owner")
  monkeypatch.setenv(dashboard.DAEDALUS_PASSWORD, "ui password")
  assert client.get("/ui/api/status").status_code == 401, (
    "a new login ends the sessions"
  )
  assert client.post("/ui/api/login", json=login).status_code == 401, "no admin login"
  mine = {"username": "owner", "password": "ui password"}
  assert client.post("/ui/api/login", json=mine).status_code == 200
  assert client.get("/ui/api/status").status_code == 200, "the env login"
  models = client.get("/v1/models", headers={"Authorization": "Bearer ui password"})
  assert models.status_code == 401, "the password is for the dashboard only"
  assert client.get("/v1/models").status_code == 200, "the master key stays the /v1 key"
  monkeypatch.delenv(dashboard.DAEDALUS_USERNAME)
  monkeypatch.delenv(dashboard.DAEDALUS_PASSWORD)
  client.post("/ui/api/logout")
  assert client.get("/ui/api/status").status_code == 401, "logout ends the session"
  https = {"Origin": "https://3357-box.example.app"}
  framed = client.post("/ui/api/login", json=login, headers=https).headers["set-cookie"]
  assert "samesite=none" in framed.lower() and "secure" in framed.lower(), framed
  assert "partitioned" in framed.lower(), "the cookie works in a preview frame"
  value = client.post("/ui/api/login", json=login).json()["session"]
  client.cookies.clear()
  header = {dashboard.HEADER: value}
  assert client.get("/ui/api/status", headers=header).status_code == 200, (
    "a frame without cookies"
  )
  assert (
    client.get("/ui/api/status", headers={dashboard.HEADER: "1.x"}).status_code == 401
  )
  stale = {dashboard.COOKIE: "1.x"}
  assert client.get("/ui/api/status", headers=header, cookies=stale).status_code == 200
  remember = client.post("/ui/api/login", json={**login, "remember": True})
  assert (
    f"max-age={dashboard.REMEMBER_SECONDS}" in remember.headers["set-cookie"].lower()
  )
  expires = int(client.cookies.get(dashboard.COOKIE).partition(".")[0])
  assert expires > time.time() + dashboard.REMEMBER_SECONDS - 60, "a 30-day session"


def test_defaults(client: TestClient) -> None:
  """The provider defaults for the form placeholders, with no values from the environment."""
  found = client.get("/ui/api/provider-defaults").json()
  assert found["groq"]["api_base"] == "https://api.groq.com/openai/v1", found
  assert found["gemini"]["api_type"] == "gemini" and found["*"] == {
    "api_type": "openai"
  }
  assert "env:CLOUDFLARE_ACCOUNT_ID" in found["cloudflare"]["account_id"], "a token"
  # Cloudflare derives its 2 URLs from the account ID, so the card shows the template.
  account = "https://api.cloudflare.com/client/v4/accounts/{account_id}"
  assert found["cloudflare"]["api_base"] == f"{account}/ai/v1", found["cloudflare"]
  assert (
    found["cloudflare"]["discovery_url"] == f"{account}/ai/models/search?per_page=100"
  )
  assert "cloudflare" not in json.dumps(found["gemini"])
  assert TestClient(api.app).get("/ui/api/provider-defaults").status_code == 401


def test_models_answer_304_until_the_cooling_moves(client: TestClient) -> None:
  """An unchanged model list answers 304, and a new cooldown sends the rows again."""
  first = client.get("/ui/api/models")
  tag = first.headers["etag"]
  assert tag.startswith('"'), first.headers
  same = client.get("/ui/api/models", headers={"if-none-match": tag})
  assert same.status_code == 304, same.text
  assert same.content == b"", "a 304 carries no body"
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  try:
    again = client.get("/ui/api/models", headers={"if-none-match": tag})
  finally:
    api.COOLDOWNS.clear()
  assert again.status_code == 200, again.text
  assert again.headers["etag"] != tag


def test_data(client: TestClient) -> None:
  response = client.get("/ui/api/models")
  assert response.status_code == 200, response.text
  models = response.json()
  assert [row["id"] for row in models] == ["p/big", "p/small", "p/embed"], "all rows"
  assert models[0] == {
    "id": "p/big",
    "provider": "p",
    "slug": "big",
    "mode": "chat",
    "max_input_tokens": 1000,
    "max_output_tokens": None,
    "tools": True,
    "reasoning": True,
    "effort": "medium",
    "efforts": [],
    "flags": ["vision", "audio_input"],
    "tier": "TIER-A",
    "weight": 1.0,
    "cooldown": None,
    "client_cooldowns": {},
    "order": 1,
  }, models[0]
  assert models[1]["order"] == 3, "the order of each model"
  previous = CONFIG["p"]["models"]
  CONFIG["p"]["models"] = {**previous, "big": {"pool": False}}
  try:
    assert client.get("/ui/api/models").json()[0]["tier"] is None, (
      "pool false hides an explicit provider tier"
    )
  finally:
    CONFIG["p"]["models"] = previous
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  api.COOLDOWNS.start("p/small#kilo", {"retry-after": "30"}, b"")
  row = client.get("/ui/api/models").json()[1]
  until = row["cooldown"]
  api.COOLDOWNS.clear()
  assert list(row["client_cooldowns"]) == ["kilo"], (
    "the cooldown end of each client lane"
  )
  assert 80 < until - time.time() <= 90, "the cooldown end of each model"
  assert models[1]["tier"] == "TIER-C" and models[1]["tools"] is False
  assert models[1]["reasoning"] is False, models[1]
  embed = models[2]
  assert (embed["mode"], embed["tier"], embed["weight"]) == ("embedding", None, None), (
    embed
  )
  pools = {pool["name"]: pool["members"] for pool in client.get("/ui/api/pools").json()}
  assert [m["id"] for m in pools["daedalus/auto"]] == ["p/big", "p/small"]
  assert "daedalus/praktos" not in pools
  assert [m["id"] for m in pools["daedalus/koinos"]] == ["p/small"]
  assert pools["daedalus/moros"] == [], "a pool with no member"
  context = {
    pool["name"]: pool["context"] for pool in client.get("/ui/api/pools").json()
  }
  assert context["daedalus/auto"] == 1000, "the largest context of the members"
  assert context["daedalus/koinos"] is None and context["daedalus/moros"] is None, (
    context
  )
  assert pools["daedalus/koinos"][0]["cooldown"] is None, "no cooldown"
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  pools = {pool["name"]: pool["members"] for pool in client.get("/ui/api/pools").json()}
  api.COOLDOWNS.clear()
  cooled = pools["daedalus/koinos"][0]["cooldown"]
  assert cooled and cooled > time.time(), "a pool member shows its cooldown end"
  status = client.get("/ui/api/status").json()
  assert status == {
    "healthy": True,
    "version": daedalus.__version__,
    "models": 2,
    "sessions": 0,
    "catalog": status["catalog"],
    "affinity": status["affinity"],
  }, status
  assert set(status["affinity"]) == {"mode", "enabled", "count", "chance", "slow"}, (
    status
  )
  assert status["catalog"]["built"] <= time.time() < status["catalog"]["next"], status
  body = {"model": "daedalus/sophos", "messages": [{"role": "user", "content": "x"}]}
  assert client.post("/v1/chat/completions", json=body).status_code == 200
  assert client.get("/ui/api/status").json()["sessions"] == 1, "the new session model"
  recent = client.get("/ui/api/requests").json()
  assert len(recent) == 1 and recent[0]["via"] == "p/big" and recent[0]["status"] == 200
  assert recent[0]["model"] == "daedalus/sophos" and recent[0]["fallbacks"] == "0"
  client.get("/ui/api/status")
  assert len(client.get("/ui/api/requests").json()) == 1, "chat requests only"


def test_history(client: TestClient) -> None:
  restarted = History(lambda: store.MODELS_DB, keep=3)
  assert restarted.latest(1)[0]["model"] == dashboard.HISTORY.latest(1)[0]["model"], (
    "a new history object reads the rows of the file, as after a restart"
  )
  restarted.clear()
  for number in range(5):
    restarted.add({"at": number, "model": f"m/{number}"})
  kept = [row["model"] for row in restarted.latest(10)]
  assert kept == ["m/4", "m/3", "m/2"], "the newest rows first, at most keep"
  database = restarted.connect()
  count = database.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
  database.close()
  assert count == 3, "the file drops the older rows"
  for number in range(60):
    dashboard.HISTORY.add({"at": number, "model": "m/x"})
  assert len(client.get("/ui/api/requests").json()) == 50, "50 rows by default"
  assert len(client.get("/ui/api/requests?limit=55").json()) == 55
  assert len(client.get("/ui/api/requests?limit=x").json()) == 50, "a bad limit"
  dashboard.HISTORY.clear()


def test_keys(client: TestClient) -> None:
  assert client.get("/ui/api/keys").json() == []
  made = client.post("/ui/api/keys", json={"name": " laptop "})
  assert made.status_code == 201 and made.json()["name"] == "laptop", made.text
  key = made.json()["key"]
  listed = client.get("/ui/api/keys").json()
  assert [row["name"] for row in listed] == ["laptop"] and "key" not in listed[0], (
    listed
  )
  assert client.post("/ui/api/keys", json={"name": "laptop"}).status_code == 400, (
    "in use"
  )
  assert client.post("/ui/api/keys", json={"name": ""}).status_code == 400
  body = {"model": "daedalus/sophos", "messages": [{"role": "user", "content": "k"}]}
  bearer = {"Authorization": f"Bearer {key}"}
  sent = client.post("/v1/chat/completions", json=body, headers=bearer)
  assert sent.status_code == 200, sent.text
  outside = TestClient(api.app)
  assert outside.get("/ui/api/keys", headers=bearer).status_code == 401, "no dashboard"
  assert client.delete("/ui/api/keys/laptop").status_code == 204
  assert client.delete("/ui/api/keys/laptop").status_code == 404
  assert (
    client.post("/v1/chat/completions", json=body, headers=bearer).status_code == 401
  )


def test_hook_legend_rows(client: TestClient, state_folder: Path) -> None:
  """The page reads the legend rows of the enabled hook files."""
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200, login.text
  assert TestClient(api.app).get("/ui/api/hooks").status_code == 401, (
    "a session is needed"
  )
  assert client.get("/ui/api/hooks").json() == {"legend": []}, "no hook file yet"
  folder = state_folder / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  (folder / "owui_auto_reasoning_effort.py").write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  body = client.get("/ui/api/hooks").json()
  assert body == {"legend": [["rtN", "A repeat picked another model, N times"]]}, body


def _tar() -> bytes:
  """The archive of 1 commit, with 2 hook files under `hooks`."""
  body = io.BytesIO()
  with tarfile.open(fileobj=body, mode="w:gz") as archive:
    for name, version in (("scanned.py", "1.2.0"), ("picked.py", "2.0.0")):
      text = (
        "# ---\n"
        f"# version: {version}\n"
        "# surfaces: [on-answer]\n"
        "# ---\n"
        "def on_answer(answer, model):\n"
        "  return answer\n"
      ).encode()
      info = tarfile.TarInfo(f"name-{'a' * 7}/hooks/{name}")
      info.size = len(text)
      archive.addfile(info, io.BytesIO(text))
  return body.getvalue()


def test_app_js_hooks_section() -> None:
  """The Settings page holds the folder, the sources and the update of the hook files."""
  script = TestClient(api.app).get("/ui/app.js")
  assert script.status_code == 200
  for piece in (
    'id="set-hooks-dir"',
    "data-hook-source-add",
    "data-hook-source-drop",
    "data-hook-source",
    "data-hook-toggle",
    "data-hooks-update",
    "data-hook-take",
    "data-hook-take-all",
    'ask("Add a source"',
    "hooks/scan",
    'listValue("hooks", "sources")',
    'listValue("hooks", "disabled")',
    'fileValue("hooks", "dir")',
    "hook_rows",
    'setting("hooks", "dir")',
  ):
    assert piece in script.text, piece


def test_app_js_hooks_manager_renders() -> None:
  """The Hooks card lists the installed files, the folder and the sources, and its rows save."""
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
    "state, renderSettings, settingsChanges, setListValue",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(html.includes('id="set-hooks-dir"'), 'the folder field');
assert(html.includes('value="hooks"'), 'the folder shows the saved value');
assert(html.includes('id="hook-source"') && html.includes('>owner/name/hooks<'), 'the sources show the saved repo');
assert(html.includes('data-hook-source-add') && html.includes('data-hook-source-drop') &&
  html.includes('data-hooks-update'), 'the source controls');
assert(html.includes('data-hook-toggle="one.py"') && html.includes('data-hook-toggle="bad.py"'), '1 switch per file');
assert(html.includes('>1.2.0<') && html.includes('provider: openrouter'), 'the version and the scope of a file');
assert(html.includes('owner/name@ccccccc'), 'the source of an installed file');
assert(html.includes('unknown surface'), 'a bad block shows its problem');
assert(!html.includes('data-hook-take'), 'no scan before a repo is picked');
probe.setListValue('hooks', 'disabled', []);
assert(JSON.stringify(probe.settingsChanges().hooks.disabled) === '[]', 'the off names reach the payload');
probe.setListValue('hooks', 'sources', []);
assert(JSON.stringify(probe.settingsChanges().hooks.sources) === '[]', 'the sources reach the payload');
node('set-hooks-dir').value = 'mine';
assert(probe.settingsChanges().hooks.dir === 'mine', 'the folder reaches the payload');
""",
  )


def test_app_js_capability_chip_icons() -> None:
  """The Tools and Reasoning chips carry the 2 icons the note picked, in the chip color."""
  app = Path("daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  block = app[app.index("const LUCIDE = {") : app.index("const CHIP_ICONS")]
  glyphs = dict(re.findall(r"(\w+): '(<svg.*?</svg>)'", block, re.DOTALL))
  icons = {
    "tools": glyphs["wrench"],
    "cool": glyphs["snowflake"],
    "brain": glyphs["brain"],
  }
  wrench, brain = icons["tools"], icons["brain"]
  assert 'viewBox="0 0 24 24"' in wrench and 'viewBox="0 0 24 24"' in brain, (
    "the lucide glyph box"
  )
  assert "M14.7 6.3" in wrench, "the lucide wrench path"
  assert "M12 18V5" in brain, "the lucide brain path"
  for name, icon in (("wrench", wrench), ("brain", brain)):
    assert 'width="12"' in icon and 'aria-hidden="true"' in icon, name
    assert 'stroke="currentColor"' in icon and 'fill="none"' in icon, (
      f"the chip color of the {name}"
    )
    assert "<style" not in icon and "<g" not in icon, f"the plain markup of the {name}"
    ElementTree.fromstring(icon)
  assert 'title="Tools">${CHIP_ICONS.tools}' in app, (
    "the wrench draws in the tools chip"
  )
  assert 'title="Reasoning">${CHIP_ICONS.brain}' in app, (
    "the brain draws in the reasoning chip"
  )


def test_app_js_hook_fit() -> None:
  """A hook fits a card when its scope names the provider or the model of that card."""
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


def test_hook_rows_and_update(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The settings name the rows of the hook folder, and the update endpoint fetches now."""
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "one.py").write_text(
    "# ---\n# version: 1.2.0\n# scope: provider\n# targets: [p]\n# ---\n"
    "def on_answer(answer, model):\n  return answer\n",
    encoding="utf-8",
  )
  rows = {
    row["name"]: row for row in client.get("/ui/api/settings").json()["hook_rows"]
  }
  assert rows["one.py"] == {
    "name": "one.py",
    "path": "hooks/one.py",
    "version": "1.2.0",
    "scope": "provider",
    "targets": ["p"],
    "surfaces": [],
    "runs": [],
    "enabled": True,
    "problem": "",
    "record": {},
  }, rows
  before = {
    "one.py": {
      "sha256": "b" * 64,
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "c" * 40,
    }
  }
  after = {"one.py": {**before["one.py"], "sha256": "d" * 64, "version": "1.2.0"}}
  state = {"records": {}}
  seen: list[object] = []
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])

  def fake_update(
    entries, folder=None, lock_path=None, only_missing=False, take=None, report=None
  ):
    seen.append((list(entries), folder, only_missing, take))
    if report is not None:
      report.append("source owner/name at main did not read")
    state["records"] = after
    return ["one.py"]

  monkeypatch.setattr(remote, "update", fake_update)
  sources = [
    {"repo": "owner/name", "path": "hooks", "ref": "main", "auto_update": False}
  ]
  monkeypatch.setattr(
    settings,
    "load",
    lambda path=None: {"hooks": {"dir": "hooks", "sources": sources, "disabled": []}},
  )
  state["records"] = before
  found = client.post("/ui/api/hooks/update")
  assert found.status_code == 200, found.text
  assert found.json() == {
    "moved": ["one.py"],
    "report": ["source owner/name at main did not read"],
    "hooks": [
      {
        "name": "one.py",
        "moved": True,
        "before": before["one.py"],
        "after": after["one.py"],
      }
    ],
  }, found.text
  assert seen == [(sources, hooks.ROOT / "hooks", False, None)]
  monkeypatch.setattr(
    settings,
    "load",
    lambda path=None: {"hooks": {"dir": "hooks", "sources": [], "disabled": []}},
  )
  empty = client.post("/ui/api/hooks/update")
  assert empty.status_code == 400 and "No source" in empty.text, empty.text
  assert TestClient(api.app).post("/ui/api/hooks/update").status_code == 401, (
    "a session is needed"
  )


def test_hook_file_delete(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The delete leaves the file and its lock record out, and stays inside the folder."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "drop.py").write_text(
    "# ---\n# version: 1.0.0\n# ---\ndef on_answer(a, m):\n  return a\n",
    encoding="utf-8",
  )
  state = {"records": {}}
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])
  monkeypatch.setattr(
    remote,
    "write_records",
    lambda records, path=None: state.update(records=dict(records)),
  )
  state["records"] = {
    "drop.py": {
      "sha256": "b" * 64,
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "c" * 40,
    }
  }
  assert (
    TestClient(api.app)
    .request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
    .status_code
    == 401
  ), "a session is needed"
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  missing = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "hooks/nope.py"}
  )
  assert missing.status_code == 404, missing.text
  escape = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "config/daedalus.yml"}
  )
  assert escape.status_code == 404, escape.text
  found = client.request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
  assert found.status_code == 200, found.text
  assert not (root / "drop.py").exists()
  assert state["records"] == {}


def test_hook_file_delete_with_a_relative_folder(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A relative hooks dir in the settings still deletes the file, as in a server run."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "drop.py").write_text(
    "# ---\n# version: 1.0.0\n# ---\ndef on_answer(a, m):\n  return a\n",
    encoding="utf-8",
  )
  state = {"records": {}}
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])
  monkeypatch.setattr(
    remote,
    "write_records",
    lambda records, path=None: state.update(records=dict(records)),
  )
  monkeypatch.setattr(hooks, "ROOT", Path("."))
  monkeypatch.chdir(state_folder)
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  found = client.request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
  assert found.status_code == 200, found.text
  assert not (root / "drop.py").exists()


def test_a_hook_file_that_stays_answers_a_clear_message(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file the OS refuses answers 409 with its name, and the file stays."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  target = root / "locked.py"
  target.write_text("# ---\n# version: 1.0.0\n# ---\n", encoding="utf-8")
  real = Path.unlink

  def refuse(self: Path, *args: object, **kwargs: object) -> None:
    if self == target:
      raise PermissionError(13, "Permission denied", str(self))
    real(self, *args, **kwargs)

  monkeypatch.setattr(Path, "unlink", refuse)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  answer = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "hooks/locked.py"}
  )
  assert answer.status_code == 409, answer.text
  assert "locked.py" in answer.json()["error"]["message"], answer.json()
  assert target.exists(), "a refused delete leaves the file"
  real(target)


def test_a_provider_file_that_stays_answers_a_clear_message(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A provider file the OS refuses answers 409, and the file stays."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  made = client.post("/ui/api/files", json={"name": "locked"})
  assert made.status_code == 200, made.text
  target = Path(made.json()["path"])
  real = Path.unlink

  def refuse(self: Path, *args: object, **kwargs: object) -> None:
    if self == target:
      raise PermissionError(13, "Permission denied", str(self))
    real(self, *args, **kwargs)

  monkeypatch.setattr(Path, "unlink", refuse)
  answer = client.request("DELETE", "/ui/api/files", json={"path": str(target)})
  assert answer.status_code == 409, answer.text
  assert "locked" in answer.json()["error"]["message"], answer.json()
  assert target.exists(), "a refused delete leaves the file"
  real(target)


def test_hook_scan_and_take(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The scan answers with the files of a repo, and an update takes the picked names."""
  commit = "a" * 40
  pages = {
    remote.API.format(repo="owner/name", ref="main"): json.dumps(
      {"sha": commit}
    ).encode(),
    remote.ARCHIVE.format(repo="owner/name", commit=commit): _tar(),
  }
  monkeypatch.setattr(
    remote, "fetch", lambda url, timeout=30.0, report=None: pages.get(url)
  )
  assert TestClient(api.app).post("/ui/api/hooks/scan", json={}).status_code == 401
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  body = {"repo": "https://github.com/owner/name", "path": "hooks", "ref": "main"}
  found = client.post("/ui/api/hooks/scan", json=body)
  assert found.status_code == 200, found.text
  answer = found.json()
  assert answer["repo"] == "owner/name" and answer["commit"] == commit
  assert [row["name"] for row in answer["files"]] == ["picked.py", "scanned.py"]
  assert {row["name"]: row["version"] for row in answer["files"]}[
    "scanned.py"
  ] == "1.2.0"
  root = hooks.ROOT / "hooks"
  assert not (root / "scanned.py").exists(), "a scan writes nothing"
  bad = client.post("/ui/api/hooks/scan", json={"repo": "owner/other"})
  assert bad.status_code == 400, bad.text
  take = client.post(
    "/ui/api/hooks/update", json={"source": body, "take": ["picked.py"]}
  )
  assert take.status_code == 200, take.text
  assert take.json()["moved"] == ["picked.py"], take.text
  assert (root / "picked.py").is_file() and not (root / "scanned.py").exists()


def test_files(
  client: TestClient, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The dashboard session is a cookie, and the bearer header does not carry it, so the
  # test logs in rather than ride a cookie that an earlier test in the worker left.
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  listing = client.get("/ui/api/files")
  assert isinstance(listing.json(), list), listing.text[:400]
  names = [item["path"] for item in listing.json()]
  assert names == [str(path) for path in dashboard.FILES], names
  path = str(settings.DEFAULT_PATH)
  assert (
    client.put("/ui/api/files", json={"path": path, "text": ""}).status_code == 400
  ), "the Settings page owns the settings file"
  shown = client.get("/ui/api/settings").json()
  source = {
    "repo": "nemoe7/daedalus",
    "path": "hooks",
    "ref": "main",
    "auto_update": False,
  }
  assert shown["file"] == {"hooks": {"sources": [source]}}, (
    "the shipped file holds the initial hook source alone"
  )
  root = folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "picked.py").write_text(
    "def on_answer(value, model, headers):\n  return value\n"
  )
  assert "hooks/picked.py" in client.get("/ui/api/settings").json()["hook_files"], (
    "the settings page lists the hook files, so a row picks its own"
  )
  assert shown["defaults"]["limits"]["slow"] == 30.0, shown
  assert shown["defaults"]["balance"]["fault"] == 0.5, shown
  limits = shown["defaults"]["limits"]
  assert (
    limits["calls"],
    limits["repeats"],
    limits["shortest"],
    limits["longest"],
  ) == (
    3,
    4,
    20,
    2000,
  ), shown
  assert shown["headroom_available"] is False, shown

  async def ready() -> bool:
    return True

  monkeypatch.setattr(headroom, "available", ready)
  assert client.get("/ui/api/settings").json()["headroom_available"] is True
  before = settings.DEFAULT_PATH.read_text()
  bad = client.put("/ui/api/settings", json={"changes": {"balance": {"fault": 0}}})
  assert bad.status_code == 422 and "above 0" in bad.text, bad.text
  bad = client.put("/ui/api/settings", json={"changes": {"limits": {"calls": 1}}})
  assert bad.status_code == 422 and "between 2 and 100" in bad.text, bad.text
  huge = client.put(
    "/ui/api/settings",
    json={"changes": {"limits": {"request": 99999999999999999999}}},
  )
  assert huge.status_code == 422 and "at most 86400 seconds" in huge.text, huge.text
  assert settings.DEFAULT_PATH.read_text() == before, "a bad value is not written"
  unknown = client.put("/ui/api/settings", json={"changes": {"x": {"y": 1}}})
  assert unknown.status_code == 422, unknown.text
  assert client.put("/ui/api/settings", json={"changes": 1}).status_code == 400
  changes = {
    "limits": {"slow": 12, "calls": 5, "repeats": 6, "shortest": 10, "longest": 3000},
    "catalog": {"every": 0},
  }
  saved = client.put("/ui/api/settings", json={"changes": changes})
  assert saved.status_code == 200, saved.text
  text = settings.DEFAULT_PATH.read_text()
  assert "  slow: 12" in text, "the new value is written"
  assert "# Router settings." in text, "the comments stay"
  assert "  every: 0" in text, text
  assert api.SLOW_SECONDS == 12.0, "the save applies the settings"
  assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (5, 6, 10, 3000)
  cleared = client.put("/ui/api/settings", json={"changes": {"limits": {"slow": None}}})
  assert (
    cleared.status_code == 200 and "  slow:" not in settings.DEFAULT_PATH.read_text()
  ), "a cleared key leaves the file"
  assert api.SLOW_SECONDS == 30.0, "the default slow time"
  reset = client.put("/ui/api/settings", json={"changes": {"catalog": {"every": None}}})
  assert reset.status_code == 200, reset.text
  assert "  every:" not in settings.DEFAULT_PATH.read_text(), (
    "a cleared key leaves the file"
  )
  words = {"routing": {"escalation": ["ultrathink", "yes", "think hard"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "escalation:\n    - ultrathink\n    - 'yes'\n    - think hard\n" in text, text
  assert api.KEYWORDS and api.KEYWORDS.search("please ultrathink"), "the save applies"
  words = {"routing": {"escalation": ["audit"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "escalation:\n    - audit\n" in text and "ultrathink" not in text, text
  words = {"routing": {"escalation": None}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "  escalation:" not in text, "a cleared key leaves the file"
  assert api.KEYWORDS and api.KEYWORDS.search("ultrathink"), (
    "an empty field falls back to the code default"
  )
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"hooks": {"on-request": 3}}}
    ).status_code
    == 422
  ), "a hook path is a string"
  hook = {
    "hooks": {"on-request": ["hooks/owui_auto_reasoning_effort.py", "hooks/picked.py"]}
  }
  assert client.put("/ui/api/settings", json={"changes": hook}).status_code == 200
  assert api.REQUEST_HOOKS == {
    "on-request": ["hooks/owui_auto_reasoning_effort.py", "hooks/picked.py"],
    "on-prompt": [],
    "on-chunk": [],
  }, "the save applies each request hook of the surface"
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"hooks": {"on-request": []}}}
    ).status_code
    == 200
  )
  assert api.REQUEST_HOOKS == {
    "on-request": [],
    "on-prompt": [],
    "on-chunk": [],
  }, "an empty list turns the hook off"
  dark = {"personalization": {"theme": "dark"}}
  assert client.put("/ui/api/settings", json={"changes": dark}).status_code == 200
  assert (
    client.get("/ui/api/settings").json()["file"]["personalization"]["theme"] == "dark"
  )
  blue = client.put(
    "/ui/api/settings", json={"changes": {"personalization": {"theme": "blue"}}}
  )
  assert blue.status_code == 422 and "system, light or dark" in blue.text, blue.text
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"personalization": {"theme": None}}}
    ).status_code
    == 200
  )
  # The YAML view reads and writes the file text.
  text = client.get("/ui/api/settings").json()["text"]
  assert text == settings.DEFAULT_PATH.read_text(), "the text of the file"
  broken = client.put("/ui/api/settings", json={"text": "balance:\n  fault: 0\n"})
  assert broken.status_code == 422 and "above 0" in broken.text, broken.text
  assert settings.DEFAULT_PATH.read_text() == text, "a bad text is not written"
  edited = text + "optimization:\n  timeout: 14 # edited\n"
  saved = client.put("/ui/api/settings", json={"text": edited})
  assert saved.status_code == 200 and saved.json()["text"] == edited, saved.text
  assert (
    settings.DEFAULT_PATH.read_text() == edited and headroom.TIMEOUT_SECONDS == 14.0
  )
  providers = str(config.DEFAULT_PATH)
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: ["})
  assert broken.status_code == 422, "a YAML error"
  listed = client.put("/ui/api/files", json={"path": providers, "text": "- a\n"})
  assert listed.status_code == 422, "provider blocks only"
  twice = "q:\n  api_key: k\nq:\n  api_key: j\n"
  doubled = client.put("/ui/api/files", json={"path": providers, "text": twice})
  assert doubled.status_code == 422 and doubled.json()["error"]["message"] == (
    "duplicate key 'q' at line 3, column 1 (first at line 1)"
  ), doubled.text
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: [\n"})
  assert "<unicode string>" not in broken.text and "line 2" in broken.text, broken.text
  doubled = client.put(
    "/ui/api/settings", json={"text": "weights:\n  fault: 0.5\n  fault: 0.25\n"}
  )
  assert doubled.status_code == 422 and "duplicate key 'fault'" in doubled.text, (
    doubled.text
  )
  new = "q:\n  api_key: k\n"
  assert (
    client.put("/ui/api/files", json={"path": providers, "text": new}).status_code
    == 200
  )
  assert config.get_config() == {"q": {"api_key": "k"}}, "the save reloads the config"
  form = {"Content-Type": "text/plain"}
  body = json.dumps({"path": providers, "text": "a: 1\n"})
  posted = client.put("/ui/api/files", content=body, headers=form)
  assert posted.status_code == 400, "a cross-site form cannot write a file"
  outside = str(folder / "other.yml")
  assert (
    client.put("/ui/api/files", json={"path": outside, "text": ""}).status_code == 400
  )
  assert (
    client.put("/ui/api/files", json={"path": providers, "text": 1}).status_code == 400
  )
  # The main file stays. A {provider}.yml file comes and goes.
  listed = client.get("/ui/api/files").json()
  assert [(f["path"], f["main"]) for f in listed] == [(providers, True)], listed
  made = client.post("/ui/api/files", json={"name": "openrouter"})
  assert made.status_code == 200, made.text
  assert "OPENROUTER_API_KEY" in made.json()["text"], made.json()
  assert config.provider_files(config.DEFAULT_PATH) == [Path(made.json()["path"])]
  loaded = config.get_config()
  assert loaded["openrouter"]["_file"]["models"] == {"*": {}}, loaded["openrouter"]
  assert client.post("/ui/api/files", json={"name": "openrouter"}).status_code == 400
  assert client.post("/ui/api/files", json={"name": "Open Router"}).status_code == 400
  assert client.post("/ui/api/files", json={"name": "../x"}).status_code == 400
  assert (
    client.request("DELETE", "/ui/api/files", json={"path": providers}).status_code
    == 400
  )
  assert (
    client.request("DELETE", "/ui/api/files", json={"path": str(folder)}).status_code
    == 400
  )
  listed = {f["path"]: f for f in client.get("/ui/api/files").json()}
  assert listed[made.json()["path"]]["shadow"] is None, (
    "no block of the main file shadows the new file"
  )
  assert (
    client.put(
      "/ui/api/files", json={"path": providers, "text": "openrouter:\n  api_key: k\n"}
    ).status_code
    == 200
  )
  listed = {f["path"]: f for f in client.get("/ui/api/files").json()}
  assert listed[made.json()["path"]]["shadow"] == Path(providers).name, (
    "the block of the main file wins the provider keys"
  )
  gone = client.request("DELETE", "/ui/api/files", json={"path": made.json()["path"]})
  assert gone.status_code == 200, gone.text
  assert config.provider_files(config.DEFAULT_PATH) == [], "the file went"
  assert "_file" not in config.get_config().get("openrouter", {}), "the reload drops it"


def test_provider_file_save_loads_the_main_config(client: TestClient) -> None:
  """A save of 1 `{provider}.yml` file must not read that file as the main file."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  made = client.post("/ui/api/files", json={"name": "probe"})
  assert made.status_code == 200, made.text
  saved = client.put(
    "/ui/api/files", json={"path": made.json()["path"], "text": "api_key: k\n"}
  )
  assert saved.status_code == 200, saved.text
  loaded = config.get_config()
  assert "api_key" not in loaded, "the keys of the file must not become providers"
  main = config.load_yaml(config.DEFAULT_PATH.read_text(encoding="utf-8"))
  assert {k: v for k, v in loaded.items() if k != "probe"} == main, (
    "the main file keeps its blocks"
  )
  assert loaded["probe"]["_file"] == {"api_key": "k"}, loaded.get("probe")
  assert (
    client.request(
      "DELETE", "/ui/api/files", json={"path": made.json()["path"]}
    ).status_code
    == 200
  )


def test_provider_edits_rebuild_from_cache(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  queued = []

  def cached() -> None:
    pass

  monkeypatch.setattr(api, "CATALOG_REBUILD_CACHED", cached)
  monkeypatch.setattr(schedule, "request", lambda task: queued.append(task) or True)

  response = client.put(
    "/ui/api/files",
    json={"path": str(config.DEFAULT_PATH), "text": "q:\n  api_key: k\n"},
  )
  assert response.status_code == 200, response.text
  assert queued == [cached], "a provider YAML save queues a cache-only rebuild"

  text = settings.DEFAULT_PATH.read_text(encoding="utf-8")
  response = client.put("/ui/api/settings", json={"text": text})
  assert response.status_code == 200, response.text
  assert queued == [cached], "settings saves do not rebuild the provider catalog"


def test_broken_file(client: TestClient, folder: Path) -> None:
  broken = folder / "groq.yml"
  for text in ("api_key: k\nmodels: [\n", "api_key: k\napi_key: j\n"):
    broken.write_text(text)
    listed = client.get("/ui/api/files")
    assert listed.status_code == 200, listed.text
    found = next(f for f in listed.json() if f["path"] == str(broken))
    assert found["blocks"] is None and "line" in found["error"], found
    assert found["text"] == text, "the YAML view gets the file as it is"
  fixed = client.put(
    "/ui/api/files",
    json={"path": str(broken), "text": 'api_key: k\nmodels:\n  "*": {}\n'},
  )
  assert fixed.status_code == 200, fixed.text
  found = next(
    f for f in client.get("/ui/api/files").json() if f["path"] == str(broken)
  )
  assert found["error"] is None and "groq" in found["blocks"], found
  gone = client.request("DELETE", "/ui/api/files", json={"path": str(broken)})
  assert gone.status_code == 200, gone.text


def test_catalog(client: TestClient) -> None:
  assert client.post("/ui/api/catalog").status_code == 503, "no rebuild in tests"
  api.CATALOG_REFRESH = lambda: None
  try:
    schedule.BUSY = True
    assert client.get("/ui/api/status").json()["catalog"]["rebuilding"] is True
    assert client.post("/ui/api/catalog").status_code == 409, "1 rebuild at a time"
    schedule.BUSY = False
    assert client.post("/ui/api/catalog").status_code == 202
  finally:
    api.CATALOG_REFRESH, schedule.BUSY = None, False
  assert TestClient(api.app).post("/ui/api/catalog").status_code == 401


def test_reset(client: TestClient) -> None:
  api.PENALTIES.record("p/big", 0.1)
  api.COOLDOWNS.begin("p/big", {}, b"")
  api.PENALTIES.pin("chat", "koinos", "p/big")
  assert api.PENALTIES.weights(["p/big"])["p/big"] < 1 and api.COOLDOWNS.ends()
  assert client.post("/ui/api/reset").status_code == 200
  assert api.PENALTIES.weights(["p/big"]) == {"p/big": 1.0}, "the weights go back to 1"
  assert api.COOLDOWNS.ends() == {}, "the cooldowns end"
  assert api.PENALTIES.pinned("chat", "koinos") == "p/big", "the pins stay"
  assert TestClient(api.app).post("/ui/api/reset").status_code == 401


@pytest.fixture(scope="module")
def folder(state_folder: Path) -> Path:
  return state_folder


@pytest.fixture(scope="module")
def client(folder: Path):
  store.write_store(ROWS)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(
      settings, "DEFAULT_PATH", Path(shutil.copy("config/daedalus.yml", folder))
    )
    patch.setattr(
      config, "DEFAULT_PATH", Path(shutil.copy("config/providers/free.yml", folder))
    )
    patch.setattr(dashboard, "FILES", (config.DEFAULT_PATH,))
    patch.setattr(api, "get_config", lambda: CONFIG)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    yield TestClient(api.app, headers=AUTH)
  upstream.set_client(None)
  config.set_config(None)


def test_app_js_redraw_needs_new_markup() -> None:
  """The tables draw again only when the markup changed, so the find marks and the selection stay."""
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


def test_app_js_draw_waits_for_a_live_selection() -> None:
  """A live selection inside a host holds its redraw back, so the text and the find marks stay."""
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


def test_app_js_nav_marks_the_current_page_for_a_screen_reader() -> None:
  """The current tab carries `aria-current="page"`, so a screen reader names the page."""
  app = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/app.js"
  ).read_text(encoding="utf-8")
  assert (
    'if (on) link.setAttribute("aria-current", "page");\n'
    '    else link.removeAttribute("aria-current");'
  ) in app, "the page switcher marks the current tab"


def test_app_js_requests_toolbar_narrows_the_table() -> None:
  """The toolbar narrows by text, status and age, and the hint counts the shown rows."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + "\\nglobalThis.__probe = { state, renderRequestTable, requestMatches };\\n";
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
    querySelector: () => ({ firstChild: { textContent: 'R' } }), querySelectorAll: () => [],
    addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
  getSelection: () => ({ isCollapsed: true }), console: { error: () => {} }, $: node,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const probe = sandbox.__probe;
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
assert.strictEqual(node('request-count').textContent, '3 requests', 'the hint counts the shown rows');
assert.strictEqual(node('requests').classList.added.filter((n) => n === 'drawn').length, 1,
  'the first data of the table arrives once');
assert.strictEqual(node('request-count').classList.added.filter((n) => n === 'bump').length, 0,
  'no pulse without a change of the stream');
probe.state.live = new Map([[1, { id: 1 }]]);
probe.renderRequestTable();
assert.strictEqual(node('request-count').textContent, '4 requests · 1 in flight', 'the count reads both lists');
assert(!node('requests').innerHTML.includes('No requests'), 'a live row keeps the empty row away');
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
assert(node('requests').innerHTML.includes('No requests match the filter'), 'the empty table names the filter');

assert.strictEqual(probe.requestMatches({ status: 200, at: now }, { live: true }), false,
  'a live row follows the text too');
probe.state.requestSearch = '';
assert.strictEqual(probe.requestMatches({ status: 200, at: now }, { live: true }), true, 'a live row needs no status');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_overview_leads_with_4_numbers() -> None:
  """The Overview draws 4 numbers, and its request card holds 5 rows."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8')
  + "\\nglobalThis.__probe = { state, renderOverview };\\n";
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
const assert = require('assert');
const probe = sandbox.__probe;
const now = Math.floor(Date.now() / 1000);
probe.state.requests = [{ at: now, status: 200 }, { at: now, status: 500 },
  { at: now, status: 200 }, { at: now, status: 200 }, { at: now, status: 200 },
  { at: now, status: 200 }, { at: now, status: 200 }];
probe.state.live = new Map([[1, { id: 1 }], [2, { id: 2 }]]);
probe.state.models = [1, 2, 3];
probe.state.pools = [];
probe.state.limits = { providers: [], lanes: [{ rows: [{ limit: 100, remaining: 25 }] }] };
probe.renderOverview();
const kpis = node('ov-kpis').innerHTML;
assert.strictEqual((kpis.match(/class="kpi"/g) || []).length, 4, 'the strip holds 4 numbers');
assert(kpis.includes('<b>2</b><small>In flight now</small>'), 'the strip counts the live requests');
assert(kpis.includes('Success of last 7 requests</small>'), 'the strip names the success share');
assert(kpis.includes('<b>86%</b>'), '6 answered of 7 reads 86 percent');
assert(kpis.includes('<b>25%</b><small>Lowest limit left</small>'), 'the strip takes the lowest limit left');
assert(kpis.includes('<b>3</b><small>Models in the catalog</small>'), 'the strip counts the catalog');
const rows = (node('ov-requests').innerHTML.match(/class="line"/g) || []).length;
assert.strictEqual(rows, 5, 'the request card holds 5 rows, not 10');
probe.state.limits = null;
probe.state.requests = [];
probe.renderOverview();
assert(node('ov-kpis').innerHTML.includes('<b>-</b>'), 'an unknown share reads as a dash');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_a_phone_keeps_the_tap_size_of_its_controls() -> None:
  """Every filter control reaches 44px on a phone."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    ".toolbar input, .toolbar select, .requests-bar input, .requests-bar select,\n"
    "  .filter, .tab { min-height: 44px; }"
  ) in css, "the phone keeps the 44px target of the filter controls"


def test_the_confirm_dialog_keeps_the_phone_gutter() -> None:
  """The dialog takes its 420px cap, or the phone width less the 2 page gutters."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\n\.modal \{([^}]*)\}", css)
  assert rule and "max-width: min(420px, calc(100vw - 32px));" in rule.group(1), (
    "the dialog stops at the page gutter on a phone, in place of edge to edge"
  )


def test_the_tab_bar_fades_its_clipped_edges() -> None:
  """A clipped tab edge fades toward the bar center, so the cut never reads hard."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  nav = re.search(r"#nav \{([^}]*)\}", css)
  assert nav and "mask-image: linear-gradient(90deg, transparent 0," in nav.group(1), (
    "the tab scroller masks its edges"
  )
  assert "#nav.fade-left { --fade-left: 20px; }" in css, "the left fade rides a class"
  assert "#nav.fade-right { --fade-right: 20px; }" in css, (
    "the right fade rides a class"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'nav.classList.toggle("fade-left"' in app, "the bar fades only while it clips"
  assert ".tab-step span { display: block; transform: translateY(-2px); }" in css, (
    "the angle glyph centers on the tab text"
  )
  assert "#nav-left:not([hidden]) { margin-left: -12px; }" in css, (
    "a shown chevron sits at the page edge"
  )
  assert ".tab-step[hidden] { display: grid !important; visibility: hidden; }" in css, (
    "a hidden chevron keeps its slot, so the scroll never snaps"
  )


def test_a_phone_keeps_one_rhythm_around_the_bar_rules() -> None:
  """Every phone bar keeps 8px over its rule, and the flow gap keeps 16px under it."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  phone = re.search(r"@media \(max-width: 720px\) \{(.*?)\n\}", css, re.DOTALL)
  assert phone, "the phone media query holds the bar rules"
  bar = re.search(
    r'section\[data-page="requests"\] \.requests-bar \{([^}]*)\}', phone.group(1)
  )
  assert bar and "padding: 3px 3px 8px;" in bar.group(1), (
    "the requests bar keeps the same room over its rule as the keys and models bars"
  )
  panel = re.search(r"\.panel:has\(> \.requests\) \{([^}]*)\}", phone.group(1))
  assert panel and "margin-top: 0;" in panel.group(1), (
    "the flow gap alone keeps the 16px under the requests rule"
  )
  bodies = re.search(r"\.models tbody, \.keys tbody \{([^}]*)\}", phone.group(1))
  assert bodies and "margin-top: 16px;" in bodies.group(1), (
    "the first card row keeps the flow gap under the bar rule"
  )
  assert not re.search(r'\[data-page="limits"\] > \.editbar \{[^}]*padding', css), (
    "a page-scoped editbar padding breaks the shared border rhythm"
  )


def test_the_save_bars_keep_no_sticky_rule() -> None:
  """The Save bar of Providers and Settings scrolls with its form, so no row hides behind it."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  for page in ("providers", "settings"):
    rules = re.findall(rf'section\[data-page="{page}"\][^{{}}]*\{{([^}}]*)\}}', css)
    assert not any("position: sticky" in body for body in rules), (
      f"a sticky Save bar hides the rows of the {page} form"
    )


def test_the_wide_rail_sticky_never_pushes_the_rail_down() -> None:
  """The rail sticks at the scrollport top, so a page at rest keeps rail and pane aligned."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  wide = re.search(r"@media \(min-width: 901px\) \{(.*?)\n\}", css, re.DOTALL)
  assert wide, "the wide media query holds the still-header layout"
  rail = re.search(r"\.sections \{([^}]*)\}", wide.group(1))
  assert rail and "position: sticky" in rail.group(1), (
    "the rail sticks on a wide screen"
  )
  assert "top: 0;" in rail.group(1), (
    "a top above the scrollport top pushes the rail below the pane at rest"
  )


def test_app_js_fit_section_pane_spares_the_page_padding() -> None:
  """The pane cap ends above the page padding, so a wide page holds no scroll of its own."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { fitSectionPane };";
const pane = { offsetParent: {}, style: {}, getBoundingClientRect: () => ({ top: 128 }) };
const main = { clientHeight: 851, getBoundingClientRect: () => ({ top: 49 }) };
const bare = () => ({
  addEventListener: () => {}, classList: { add: () => {}, remove: () => {}, toggle: () => {} }, style: {}, dataset: {},
  children: [], scrollWidth: 0, clientWidth: 0, scrollLeft: 0, querySelectorAll: () => [],
});
const sandbox = {
  matchMedia: () => ({ matches: true, addEventListener: () => {} }),
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


def test_the_requests_bar_keeps_its_own_height() -> None:
  """Only the table panel of the Requests page scrolls, so the toolbar stays in view."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert (
    'section[data-page="requests"] > .flow > .column > .panel:not(.editbar) '
    "{ flex: 1; min-height: 0; overflow: auto; }"
  ) in css, "the table panel keeps the free height"
  assert (
    'section[data-page="requests"] > .flow > .column > .requests-bar { flex: none; }'
  ) in css, "the toolbar keeps its own height"


def test_every_note_of_a_write_is_a_live_region() -> None:
  """A save or a failure lands in a status or an alert region, so a screen reader reads it."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  for name, role in (
    ("reset-message", "status"),
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


def test_a_failed_read_shows_a_line_in_the_page() -> None:
  """`guarded` shows the failure with a retry button. The line stays until the x clears it."""
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


def test_the_undo_never_moves_the_row() -> None:
  """The Undo sits beside the control, and a row of a column keeps it on the label line."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert ".field > .undo-step { margin-left: auto; margin-right: 8px; }" in css, (
    "the icon rides in the free space of the row, before the control"
  )
  assert (
    ".field.stack > .undo-step { position: absolute; right: 0; top: 10px; }" in css
  ), "a row of a column holds the icon on the label line"
  assert ".field:has(> .undo-step) { padding-left: 34px; }" not in css, (
    "the row keeps its width in place of a shift"
  )


def test_app_js_places_the_undo_beside_the_control() -> None:
  """The Undo of a row sits before the control, so the label and the row keep their place."""
  code = _app_js_vm(
    """
vm.runInContext('globalThis.__probe.rememberWrite = rememberWrite; globalThis.__probe.placeUndo = placeUndo;'
  + ' globalThis.__probe.lastWrite = () => lastWrite; globalThis.__probe.dropUndo = dropUndo;', sandbox);
let placed = '';
const control = { id: 'set-catalog-every', dataset: { set: 'catalog.every' }, isConnected: true,
  matches: (sel) => sel === '[data-set]',
  closest: (sel) => (sel === '[data-section]' ? { dataset: { section: 'catalog' } } : row),
  querySelector: () => null };
const row = {
  children: [control],
  insertBefore: (button, before) => { placed = 'insertBefore ' + (before === control); },
  prepend: () => { placed = 'prepend'; },
  closest: () => row,
  querySelector: () => control,
};
sandbox.document.querySelector = () => null;
sandbox.document.getElementById = () => ({ closest: () => null,
  classList: { add: () => {}, remove: () => {} } });
sandbox.document.createElement = () => ({ addEventListener: () => {}, remove: () => {}, setAttribute: () => {} });
sandbox.__probe.rememberWrite('settings', { settings: true, text: 'a' }, control);
assert.strictEqual(placed, 'insertBefore true', 'the icon joins the row before its control: ' + placed);
assert.strictEqual(sandbox.__probe.lastWrite().key, 'catalog.every', 'the key of the row');
let dropped = 0;
control.remove = () => { dropped += 1; };
sandbox.document.querySelector = (sel) => (sel === '.undo-step' ? control : null);
sandbox.__probe.dropUndo();
assert.strictEqual(dropped, 1, 'the icon leaves the page');
assert.strictEqual(sandbox.__probe.lastWrite(), null, 'no write waits a way back');
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_clears_the_undo_when_a_change_lands_back() -> None:
  """A change that returns to the saved value writes nothing, and takes the last Undo away."""
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
sandbox.document.querySelector = (sel) => (sel === '.undo-step' ? { remove: () => { removed += 1; } } : null);
(async () => {
  await sandbox.__probe.saveSettings();
  assert.strictEqual(removed, 1, 'the Undo of the write goes');
  assert.strictEqual(sent, 0, 'a change back to the saved value writes nothing');
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_names_a_failure_with_no_message() -> None:
  """A failure with no server message names the status, and the message of the server shows."""
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
  assert.strictEqual(bare, 'The server answered HTTP 500 with no message.', bare);
  sandbox.fetch = () => Promise.resolve(answer(409, { error: { message: 'The file could not be deleted.' } }));
  const said = await sandbox.__probe.call('hooks/file').then(() => '', (error) => error.message);
  assert.strictEqual(said, 'The file could not be deleted.', said);
  sandbox.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
  const offline = await sandbox.__probe.call('catalog').then(() => '', (error) => error.message);
  assert.strictEqual(offline, 'The dashboard cannot reach the server.', offline);
})().catch((error) => { console.error(error); process.exit(1); });
"""
  )
  subprocess.run(["node", "-e", code], check=True)


def test_the_requests_rows_show_a_relative_time() -> None:
  """A row reads `4 mins ago`, and the title holds the exact stamp."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'const cell = (label, inner, cls = "", attrs = "") =>' in app, (
    "the cell takes attributes"
  )
  assert '${relative(r.at)}`, "num muted", ` title="${esc(stamp(r.at))}"`)}' in app, (
    "the Time cell keeps the relative text and the exact stamp"
  )


def test_the_requests_table_has_a_live_switch() -> None:
  """The switch pauses the in-flight rows and counts the requests that wait."""
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


def test_the_limits_filter_rides_in_the_hash() -> None:
  """`#/limits?q=llama` filters the table, and Clear filters empties the field and the hash."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert 'id="limits-search"' in page and 'id="limits-clear"' in page, page
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
  assert "#request-clear.off { visibility: hidden; }" in css, (
    "the button holds its seat, so the hint never moves"
  )


def test_the_requests_filters_ride_in_the_hash() -> None:
  """`#/requests?status=bad&hours=24&q=timeout` sets the filters, and a change writes the hash."""
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


def test_a_wrong_shape_is_refused_at_save(client: TestClient) -> None:
  """A known key with a wrong shape gives 422 with its name, and a good shape lands."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200, "the session"
  made = client.post("/ui/api/files", json={"name": "shapetest"})
  assert made.status_code == 200, made.text
  path = made.json()["path"]
  try:
    for text, word in (
      ("api_key: k\ntier: TIER-B\n", "tier"),
      ("api_key: k\nmodels: nope\n", "models"),
      ("api_key: k\nhooks: 7\n", "hooks"),
      ("api_key: k\nexclude: {a: 1}\n", "exclude"),
      ("api_key: k\ndiscovery_match: nope\n", "discovery_match"),
      ("api_key: k\ntier:\n  TIER-B: TIER-B\n", "TIER-B"),
    ):
      reply = client.put("/ui/api/files", json={"path": path, "text": text})
      assert reply.status_code == 422, reply.text
      assert word in reply.json()["error"]["message"], reply.text
    good = "api_key: k\ntier:\n  TIER-B: [a]\n"
    assert (
      client.put("/ui/api/files", json={"path": path, "text": good}).status_code == 200
    ), good
  finally:
    client.request("DELETE", "/ui/api/files", json={"path": path})


def test_a_failed_login_waits_and_locks(client: TestClient) -> None:
  """A failed try waits, 5 failures lock the client out, and a good try lands after the lock."""
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER
  dashboard.LOGIN_FAILURES.clear()
  wrong = {"username": "admin", "password": MASTER + "x"}
  good = {"username": "admin", "password": MASTER}
  started = time.monotonic()
  assert client.post("/ui/api/login", json=wrong).status_code == 401
  assert time.monotonic() - started >= dashboard.LOGIN_DELAY, "a failed try waits"
  delay = dashboard.LOGIN_DELAY
  dashboard.LOGIN_DELAY = 0.0
  try:
    for _ in range(dashboard.LOGIN_LIMIT - 1):
      assert client.post("/ui/api/login", json=wrong).status_code == 401
    locked = client.post("/ui/api/login", json=wrong)
    assert locked.status_code == 429, locked.text
    assert "Wait" in locked.json()["error"]["message"], locked.text
    assert client.post("/ui/api/login", json=good).status_code == 429, "the lock holds"
    host = next(iter(dashboard.LOGIN_FAILURES))
    dashboard.LOGIN_FAILURES[host] = (
      dashboard.LOGIN_LIMIT,
      time.time() - dashboard.LOGIN_LOCK - 1,
    )
    assert client.post("/ui/api/login", json=good).status_code == 200, "the lock ends"
  finally:
    dashboard.LOGIN_DELAY = delay
    dashboard.LOGIN_FAILURES.clear()


def test_the_phone_model_cards_carry_the_desktop_columns() -> None:
  """The phone model card drops the Model label and fills its row with the desktop columns."""
  root = Path(__file__).resolve().parents[2]
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  chips = app.split("function phoneChips(m) {", 1)[1].split("\n}", 1)[0]
  assert 'nameCell(m.id, modelName(m.id), "",' in app, "the card drops the Model label"
  assert 'title="Context"' in chips, "the context column rides as a chip"
  assert 'title="Reasoning"' in chips, "the reasoning column rides as a chip"
  assert 'title="Weight"' not in chips, "the weight row carries the weight alone"
  assert ">Tier " in chips and ">Order " in chips, "the ambiguous chips carry labels"
  assert "brain:" in app, "the reasoning chip is a brain"


def test_the_page_carries_a_motion_base() -> None:
  """The page names 1 duration and 1 easing, and 1 block that drops the motion on request."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "--fast:" in css and "--soft:" in css and "--ease:" in css, "the motion tokens"
  assert "animation: pulse 1s var(--ease)" in css, "the state dot reads the easing"
  assert "a, button, .snippet, .card, .chip, .pill, tr.request td {" in css, (
    "the controls and the rows glide between their states"
  )
  assert "transition: background-color var(--" in css, "the switch reads the token"
  block = css.split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("\n}", 1)[
    0
  ]
  for rule in ("transition: none", "animation: none"):
    assert rule in block, rule
  assert "@keyframes enter" in css, "the page enter of a tab switch"
  assert 'classList.add("enter")' in app and 'classList.remove("enter")' in app, (
    "the shown section replays the enter"
  )


def test_a_live_row_arrives_and_the_stream_count_pulses() -> None:
  """A new live row glides in, an old one does not replay it, and the count pulses on a change."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert "@keyframes arrive" in css and "tr.live-row.arrive {" in css, (
    "the glide of a new live row"
  )
  assert "@keyframes bump" in css and ".hint.bump {" in css, "the pulse of the count"
  assert "liveSeen" in app and "liveCount" in app, (
    "the rows already drawn, and the last count"
  )
  assert '? "" : " arrive"' in app, "the class only on a new row"
  assert 'classList.add("bump")' in app and 'classList.remove("bump")' in app, (
    "the pulse retrigger"
  )


def test_the_first_data_of_a_card_arrives_once() -> None:
  """The drawn helper marks a host once, and the redraw of a host that holds data stays still."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert ".drawn { animation: arrive" in css, "the enter of the first data"
  assert "const DRAWN_SHOWN = new Set();" in app, "the hosts that already arrived"
  assert "DRAWN_SHOWN.has(host)" in app and "DRAWN_SHOWN.add(host)" in app, (
    "the once mark"
  )
  assert "firstDraw(host);" in app, "the shared draw path marks the host"
  assert 'firstDraw($("requests"));' in app, "the requests table marks itself too"


def test_app_js_a_pool_pick_moves_the_models_table_only() -> None:
  """A pool card holds the page still: the models table arrives, and nothing else moves."""
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


def test_a_pending_call_spins_and_the_first_load_waits_on_skeletons() -> None:
  """A slow call shows a spinner in the header, and the first load draws placeholder rows."""
  root = Path(__file__).resolve().parents[2]
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  brand = next(line for line in page.splitlines() if 'class="brand"' in line)
  chips = page[page.index('<div class="chips">') : page.index('id="status"')]
  assert 'id="spinner"' in chips, "the ring sits at the header right"
  assert "spinner" not in brand, "the ring left the brand row, so no tab moves"
  assert "@keyframes spin" in css and ".spinner {" in css, "the ring"
  assert "@keyframes shimmer" in css and "tr.skeleton td span {" in css, (
    "the placeholder bar"
  )
  assert "calling" in app and "setTimeout(() =>" in app, (
    "the grace period of a slow call"
  )
  assert 'ring.classList.add("on");' in app and 'ring.classList.remove("on");' in app, (
    "the ring follows"
  )
  assert ".spinner.on {" in css and "visibility: visible;" in css, (
    "the seat holds the width, so the ring shows without a shift"
  )
  assert "function skeletons(" in app and 'skeletons("requests"' in app, (
    "the placeholder rows"
  )
  assert 'skeletons("models"' in app, "the models table waits too"


def test_the_phone_keeps_the_numbers_and_the_cards() -> None:
  """The phone band, the Models cards, the key cards and the tab chevrons carry their CSS."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  phone = css.split("@media (max-width: 720px) {", 1)[1]
  for rule in (
    ".kpis { grid-template-columns: 1fr 1fr;",
    ".models tr > td.name { grid-column: 1 / -1; max-width: none; }",
    ".models tr > td.hide-sm { display: none; }",
    ".keys tr > td.name { grid-column: 1; grid-row: 1; max-width: none; }",
    ".keys tr > td:nth-child(4) { grid-column: 1; grid-row: 2; align-items: center; }",
    'section[data-page="requests"] .requests-bar { padding: 3px 3px 8px; }',
    "#new-key { padding: 3px 0 8px 3px; }",
    ".toolbar input:focus-visible, .toolbar select:focus-visible,",
  ):
    assert rule in phone, rule
  assert ".sections button.on" in css and ".section-pane {" in css, (
    "the Settings page holds a section list and the pane"
  )


def test_each_page_holds_a_section_list() -> None:
  """Every section sits in a rail, and the pane shows the picked 1."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function sectionList(",
    "function pickSection(",
    "pickSettingsSection(section.dataset.section)",
    "pickProviderSection(section.dataset.section)",
    'class="section-pane"',
    'class="ghost back"',
    "function yamlCard(editor, save, hint) {",
    'data-section="yaml"',
  ):
    assert needle in app, needle
  assert "const CARD_WIDTH" not in app, "the hand-balanced columns are gone"
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  for needle in (
    ".sections button.on",
    ".section-pane {",
    '.settings[data-detail="1"] .section-pane',
    ".sections button { min-height: 44px; }",
  ):
    assert needle in style, needle


def test_a_section_row_answers_the_hold() -> None:
  """A section row a finger holds takes the field background, beside the opacity fade."""
  style = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  assert ".sections button:active { background: var(--field); }" in style, (
    "the hold of a section row shows as a background"
  )
  assert ".sections button:hover { background: var(--field); }" in style, (
    "a pointer keeps the same look"
  )


def test_the_notice_takes_the_red_and_wraps() -> None:
  """A failure line reads in our red, wraps a long message, and arrives on the page tokens."""
  css = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/dashboard/ui/style.css"
  ).read_text(encoding="utf-8")
  rule = re.search(r"\n\.notice \{([^}]*)\}", css)
  assert rule, "the notice keeps its rule"
  assert "color: var(--red);" in rule.group(1), "the failure line takes our red"
  assert "animation: arrive var(--soft) var(--ease);" in rule.group(1), (
    "the line arrives as the modal does"
  )
  assert "#notice-text { min-width: 0; overflow-wrap: anywhere; }" in css, (
    "a long message wraps in place of a clipped line"
  )


def test_the_notice_leaves_on_its_own_keyframes() -> None:
  """The x clears the line on a leave that mirrors the arrival, then hides the note."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert (
    "@keyframes leave { from { opacity: 1; transform: none; }"
    " to { opacity: 0; transform: translateY(8px); } }"
  ) in css, "the leave plays the arrival backwards"
  assert (
    ".notice.leave { animation: leave var(--soft) var(--ease) forwards; }" in css
  ), "the leave keeps the page tokens of the arrival"
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  close = re.search(r'<button[^>]*id="notice-close"[^>]*>', page)
  assert close and 'aria-label="Dismiss the notice"' in close.group(0), (
    "the x reads as a dismiss for a screen reader"
  )
  body = page.split('id="notice-close"')[1].split("</button>")[0]
  assert '<path d="M18 6 6 18" />' in body and '<path d="m6 6 12 12" />' in body, (
    "the x draws the lucide glyph"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert re.search(
    r'\$\("notice"\)\.addEventListener\("animationend", \(event\) => \{\n'
    r'  if \(event\.animationName !== "leave"\) return;',
    app,
  ), "the note hides only when its own leave played out"


def test_the_model_modal_reads_as_a_card() -> None:
  """The model modal leads with the identity, and the capabilities ride as icon chips."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '<dl class="model-detail">' not in app, "the table dump is gone"
  assert "const { provider, dev, model } = parts(m.id);" in app, (
    "the card splits the id into its parts"
  )
  assert '["Model", model || m.slug || m.id' in app, "the model leads"
  assert '["Developer", dev, markImg(dev)]' in app, "the developer rides when present"
  assert '["Provider", provider,' in app, "the provider closes the identity"
  assert 'const markImg = (name) => (MARK_FILES.has(name) ? mark(name) : "");' in app, (
    "the lobe marks ride beside the parts that own one"
  )
  assert "CHIP_ICONS.tools" in app and "CHIP_ICONS.brain" in app, (
    "the capabilities read as icon chips"
  )
  modal = app.split("function openModelModal")[1].split("\nfunction ")[0]
  assert "${CHIP_ICONS.brain} Reasoning</span>" in modal, (
    "the reasoning chip draws the brain and the word together"
  )
  assert "${CHIP_ICONS.tools} Tools</span>" in modal, (
    "the tools chip draws the wrench and the word together"
  )
  assert '${FLAG_ICONS[f] ? `${FLAG_ICONS[f]} ` : ""}${esc(FLAGS[f] || f)}' in modal, (
    "the modality chips draw their icon and their label together"
  )
  assert "reasoningCell" not in modal, "no bare Yes rides in the modal"
  assert "weightBar(m.weight)" in app, "the weight draws its bar"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".model-detail" not in css, "the dump rule is gone"
  assert ".model-id {" in css and ".model-stats {" in css and ".model-chips {" in css, (
    "the card keeps its blocks"
  )
  assert '[m.max_input_tokens.toLocaleString(), "input tokens"] : null,' in app, (
    "the token stats keep no bar"
  )
  assert ".model-stats .track { margin-top: auto; }" in css, (
    "the bar pins to the floor of its block"
  )
  assert "min-height: 60px;" in css, "a floor holds the blocks at one height"
  assert "grid-template-columns: repeat(3, 1fr);" in css, "the blocks ride one row"
  assert (
    "@media (max-width: 479px) { .model-stats { grid-template-columns: 1fr; } }" in css
  ), "a narrow page folds them to one column, so no wrap leaves an orphan"
  assert ".model-modal h3 .name { overflow-wrap: anywhere; }" in css, (
    "the modal name reads in the regular face"
  )
  assert "width: min(560px, calc(100vw - 32px));" in css, (
    "the modal keeps one width on every screen"
  )
  assert ".model-id .value { min-width: 0; overflow-wrap: anywhere; }" in css, (
    "the identity values read in the regular face"
  )


def test_the_providers_page_folds_the_long_groups() -> None:
  """Tiers, Model overrides and Provider values start closed, so a card stays short."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '${fold("Tiers"' in app and '${fold("Model overrides"' in app, (
    "the fold groups"
  )
  assert (
    "function fold(label, hint, body) {" in app and '<details class="fold">' in app
  ), "a closed group wraps its fields"
  assert app.count('class="hint"') >= 2 and 'aria-label="Hint"' in app, (
    "the field hint rides behind the info icon"
  )
  assert 'role="tooltip"' in app, "the hint text serves as the tooltip"
  assert 'closest(".fold summary .hint")' in app, (
    "a click on the hint of a group never opens the group"
  )
  assert 'aria-describedby="${id}"' in app, "the hint of a group names its tooltip"
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".settings > .column {" not in style and "column-count" not in style, (
    "no page splits a card across a column"
  )
  assert ".fold summary .hint:hover ~ small" in style, (
    "the hint of a group shows its tooltip on hover and on focus"
  )


def test_the_limits_poll_keeps_out_of_the_address_bar() -> None:
  """The limits answer arrives on every page, so it never writes the hash."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert 'if (!location.hash.startsWith("#/limits")) return;' in app, (
    "the filter writes the hash on the Limits page alone"
  )
  assert '$("limits-clear").hidden = !state.limitSearch;' in app, (
    "the clear button follows the filter, and not the hash write"
  )


def test_the_yaml_editor_ends_each_list_and_saves_itself() -> None:
  """The YAML view is the last section of both lists, with its own Save."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function yamlCard(editor, save, hint) {",
    'const items = names.map((name) => [name, name]).concat([["yaml", "YAML"]]);',
    'const items = groups.map(([group, title]) => [group, title]).concat([["keys", "API Keys"], ["yaml", "YAML"]]);',
    'if (event.target.closest("#yaml-save")) return saveYaml();',
    'if (event.target.closest("#settings-yaml-save")) saveSettings();',
    'const typed = state.view === "yaml" ? $("editor")?.value : undefined;',
    'const typed = state.settingsView === "yaml" ? $("settings-editor")?.value : undefined;',
  ):
    assert needle in app, needle
  assert 'data-section="yaml"' in app and 'yamlCard("editor", "yaml-save"' in app, (
    "the card holds the picked section and the editor"
  )
  assert 'yamlCard("settings-editor", "settings-yaml-save"' in app, (
    "the settings card carries its own pair"
  )
  assert '$("editor").addEventListener("input", renderFiles);' not in app, (
    "the editor lives in a rendered card, so the listener rides on the pane"
  )


def test_a_row_writes_itself_and_the_bar_is_gone() -> None:
  """No Save bar: a value row opens the editor dialog, and its close writes that 1 value."""
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
    'showWrite("providers", `Not valid YAML: ${error}`, true);',
    "async function undoWrite() {",
    "function rememberWrite(page, restore, anchor = null) {",
    "function placeUndo(anchor = null) {",
    'button.className = "ghost undo-step";',
    'class="text" type="text" readonly spellcheck="false"',
  ):
    assert needle in app, needle
  assert '$("save")' not in app and '$("settings-save")' not in app, (
    "the bar Save is gone"
  )
  assert 'clearWrite("providers")' in app and 'clearWrite("settings")' in app, (
    "a quiet page stays quiet"
  )
  assert 'showWrite("providers", "Saved and reloaded")' not in app, (
    "a good write says nothing"
  )


def test_a_phone_opens_a_section_as_its_own_page() -> None:
  """A pick pushes the section into the hash, and the back button names its page."""
  root = Path(__file__).resolve().parent.parent.parent
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  for needle in (
    "function sectionPane(back, cards) {",
    "function openSection(host, key) {",
    "function closeSection(host) {",
    "function applySectionHash() {",
    "history.pushState({ section: key }",
    'window.addEventListener("popstate", applySectionHash);',
    "applySectionHash();",
  ):
    assert needle in app, needle
  assert 'sectionPane("Settings"' in app and 'sectionPane("Providers"' in app, (
    "each pane names the page its back button returns to"
  )
  style = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  phone = style.split("@media (max-width: 900px) {", 1)[1]
  assert '.sections button::after { content: "\\203a";' in phone, (
    "a phone row carries a chevron"
  )
  assert '.settings[data-detail="1"] .section-pane { display: grid; }' in phone, (
    "a phone shows the picked section alone"
  )


def test_notifications_endpoint(client: TestClient) -> None:
  assert (
    client.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    ).status_code
    == 200
  )
  store.record_rebuild(
    reason="scheduled",
    models=3,
    added=["p/new"],
    removed=["p/old"],
    changed=["p/big"],
    failed=["kilo"],
  )
  updates.save(
    {
      "repo": "nemoe7/daedalus",
      "current": "v1.0.0",
      "channel": "release",
      "latest": "v1.1.0",
      "url": "https://github.com/nemoe7/daedalus/releases/tag/v1.1.0",
      "behind": None,
      "update": True,
      "error": None,
    }
  )
  api.LIMITS.lanes["p/big"] = {
    "at": time.time(),
    "rows": [
      {
        "kind": "requests",
        "span": "minute",
        "limit": 100,
        "remaining": 5,
        "reset": time.time() + 50,
      },
      {
        "kind": "tokens",
        "span": "minute",
        "limit": 10000,
        "remaining": 9000,
        "reset": None,
      },
    ],
  }
  try:
    answer = client.get("/ui/api/notifications")
    assert answer.status_code == 200
    data = answer.json()
    assert data["rebuilds"][0]["reason"] == "scheduled"
    assert data["rebuilds"][0]["added"] == ["p/new"]
    assert data["rebuilds"][0]["changed"] == ["p/big"]
    assert data["update"]["latest"] == "v1.1.0"
    assert len(data["limits"]) == 1, "only the row near its limit warns"
    warning = data["limits"][0]
    assert warning["model"] == "p/big"
    assert warning["remaining"] == 5 and warning["limit"] == 100
  finally:
    api.LIMITS.lanes.pop("p/big", None)
    client.cookies.clear()


def test_update_check_now(client: TestClient) -> None:
  assert (
    client.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    ).status_code
    == 200
  )
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(updates, "__version__", "v1.0.0")
    patch.setattr(
      updates, "_fetch", lambda url: {"tag_name": "v9.9.9", "html_url": "https://x"}
    )
    answer = client.post("/ui/api/updates")
    assert answer.status_code == 200
    data = answer.json()
    assert data["latest"] == "v9.9.9" and data["update"] is True
    assert updates.read()["latest"] == "v9.9.9"
  client.cookies.clear()
