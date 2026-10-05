import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import daedalus
from daedalus import config, dashboard, store
from daedalus.catalog import schedule
from daedalus.config import settings
from daedalus.dashboard import History
from daedalus.providers import base
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


def test_page(client: TestClient) -> None:
  page = client.get("/")
  assert page.status_code == 200 and "text/html" in page.headers["content-type"]
  assert 'src="ui/app.js?v=' in page.text, "relative asset paths with a content hash"
  assert 'href="ui/style.css?v=' in page.text, "the style link has a content hash"
  assert (
    '<th scope="col" class="hide-sm hide-md" role="columnheader"'
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
    "keys",
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
  assert '["headroom", "Headroom"' in script.text, "the Headroom card is in Settings"
  assert '["enabled", "Enabled"' in script.text, "the Headroom switch is a checkbox"
  assert '["loops", "Loop detection"' in script.text, (
    "the loop thresholds are in Settings"
  )
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
    getElementById: () => ({ innerHTML: '', addEventListener: () => {}, classList: { toggle: () => {} } }),
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
  classList: {{ toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) }},
  addEventListener: () => {{}},
}};
const byId = new Map();
const el = (id) => {{
  if (!byId.has(id)) byId.set(id, {{ innerHTML: '', textContent: '', addEventListener: () => {{}} }});
  return byId.get(id);
}};
const sandbox = {{
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{
    hidden: false,
    documentElement: {{ dataset: {{}} }},
    getElementById: (id) => (id === 'nav' ? nav : el(id)),
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }} }}),
    querySelectorAll: (sel) => (sel === '[data-status]' ? hosts : []),
    addEventListener: () => {{}},
  }},
  navigator: {{}},
  location: {{ hash: '' }},
  window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}),
  $: (id) => (id === 'nav' ? nav : el(id)),
}};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
{extra}
"""


def test_app_js_tab_fades() -> None:
  """The tab bar fades mark the scroll ends, so a cut tab still shows."""
  code = _app_js_vm(
    """
assert(classes.has('fade-left') && classes.has('fade-right'), 'the first paint marks the fades');
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert(classes.has('fade-left') && classes.has('fade-right'));
nav.scrollLeft = 0;
sandbox.markNavFades();
assert(!classes.has('fade-left') && classes.has('fade-right'));
nav.scrollLeft = 160;
sandbox.markNavFades();
assert(classes.has('fade-left') && !classes.has('fade-right'));
nav.scrollLeft = 0;
nav.scrollWidth = 100;
sandbox.markNavFades();
assert(!classes.has('fade-left') && !classes.has('fade-right'));
"""
  )
  subprocess.run(["node", "-e", code], check=True)


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
  assert ".phone-types { display: flex; flex-wrap: wrap;" in mobile_css, (
    "the chips of a dropped Type column wrap under the name"
  )


def test_app_js_status_hosts() -> None:
  """The status chips fill every host, and no id repeats across them."""
  code = _app_js_vm(
    """
sandbox.renderStatus({
  healthy: true, sessions: 2, models: 80, version: 'v1',
  catalog: { built: 1, next: 2, rebuilding: false },
});
assert.strictEqual(hosts[0].innerHTML, hosts[1].innerHTML);
assert(hosts[0].innerHTML.includes('Healthy'));
assert(hosts[0].innerHTML.includes('chip rebuild'));
assert(!hosts[0].innerHTML.includes('catalog-rebuild'), 'no id the two hosts would share');
assert(byId.get('card-health').innerHTML.includes('Healthy'), 'the phone card shows the health');
const card = byId.get('card-rows').innerHTML;
assert(card.includes('Sessions') && card.includes('>2<'), 'the phone card shows the sessions');
assert(card.includes('Catalog') && card.includes('chip rebuild') === false, 'no chip in the card');
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
      classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), contains: (n) => classes.has(n) },
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
assert(html.includes('>Image in<'), 'the media chip shows');
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
"""
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
  if (!byId.has(id)) byId.set(id, { innerHTML: '', textContent: '', value: '', addEventListener: () => {}, classList: { toggle: () => {}, contains: () => false } });
  return byId.get(id);
};
const sandbox = {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: el, querySelector: () => ({ firstChild: { textContent: 'Models' } }), querySelectorAll: () => [], addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {}, matchMedia: () => ({ matches: false }) },
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
assert(modelName('cloudflare/@cf/cloudflare/clef').includes('<span class="model-part">clef</span>'), 'the model part of a scope');
assert(modelName('cloudflare/@cf/openai/gpt-oss-120b').includes('<span class="model-part">gpt-oss-120b</span>'), 'a scope keeps the developer');
assert.strictEqual(modelName('bare'), 'bare', 'a name with no slash stays text');
const bare = modelName('p/m');
assert(bare.includes('class="mark slug"') && bare.includes('>p<'), 'the text of a name with no file stands in for a mark');
assert.strictEqual(seps(bare), 1, 'the slash before the slug');
assert(bare.includes('<span class="model-part">m</span>'), 'the slug of a bare name');
assert(modelName('cloudflare/@cf/meta/llama-3.1-8b-instruct').includes('<img class="mark" src="ui/icons/cloudflare.svg"'), 'a shipped mark file');
assert(modelName('cloudflare/@cf/inclusionai/ling-3.0-flash').includes('class="mark slug">inclusionai<'), 'a name with no file shows its text');
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
assert.strictEqual(cell({ cancelled: false, status: 429 }), '<span title="Too many requests">429</span>', 'a limited request');
assert.strictEqual(cell({ cancelled: false, status: 'err' }), '<span title="The attempt failed">err</span>', 'a failed request');
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
assert(hosts[0].innerHTML.includes('Healthy'), 'the good answer shows');
assert(!hosts[0].innerHTML.includes('State unknown'), 'the good answer clears the note');
assert(!hosts[0].innerHTML.includes('Loading the state'), 'the loading note goes away');
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
  assert len(labels) == 12, labels
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
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = {{ renderRequests, renderLive, poolTier, state, modelName, poolOf, routingCodes }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false, children: [], listeners: {{}}, contains: () => false, addEventListener(type, handler) {{ this.listeners[type] = handler; }}, classList: {{ toggle: () => {{}} }} }});
  return nodes.get(id);
}};
const sandbox = {{
  esc: (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }})[c]),
  seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{ hidden: false, documentElement: {{ dataset: {{}} }}, getElementById: node, querySelector: () => ({{ firstChild: {{ textContent: 'M' }} }}), querySelectorAll: () => [], addEventListener: () => {{}} }},
  navigator: {{}}, location: {{ hash: '' }}, window: {{ addEventListener: () => {{}}, matchMedia: () => ({{ matches: false }}) }},
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
assert.strictEqual((html.match(/class="cell-value"/g) || []).length, 12, 'each request cell keeps its value');
const mobileLabels = (markup) => [...markup.matchAll(new RegExp('<span class="mobile-label" aria-hidden="true">([^<]*)</span>', 'g'))].map((m) => m[1]);
assert.deepStrictEqual(mobileLabels(html), {json.dumps(labels)}, 'the cells show their column names in table order');
assert(html.includes('hide-sm hide-md num mono'), 'the session id reads in the mono font');
assert(html.includes('class="request has-chain"'), 'requests with fallbacks expose their chain');
assert(html.includes('More · session, effort · 1 fallback'), 'request details disclose their fallback count');
// The pool of the auto model rides in the slug, and the daedalus head carries its own mark.
const autoCell = probe.modelName('daedalus/auto', probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}));
assert.strictEqual(probe.poolOf({{ model: 'daedalus/auto', pool: 'moros' }}), 'moros', 'the auto pool joins the slug');
assert(autoCell.includes('<span class="model-part">auto</span>') && autoCell.includes('<span class="model-part">moros</span>'), 'the slug reads daedalus/auto/moros');
assert(autoCell.includes('ui/icons/daedalus.svg'), 'the daedalus head carries its own mark');
assert.strictEqual(probe.poolOf({{ model: 'p/big', pool: 'daedalus/deinos' }}), '', 'a direct model keeps its own slug');
assert.strictEqual(probe.routingCodes({{ routed: 'deinos' }}).includes('fr'), true, 'the fallback tier code stays');
for (const value of ['View fallback chain', 's1', 'high <span class="from">xhi</span>', 'rate limited', 'frA', 'tl3']) assert(html.includes(value), 'the details keep ' + value);
assert(!html.includes('rt2') && !html.includes('tool loop'), 'the frX and tlN codes replace the long forms');
const mobileSelectors = [];
const mobileTap = {{ target: {{ closest: (selector) => {{ mobileSelectors.push(selector); return null; }} }} }};
sandbox.window.matchMedia = () => ({{ matches: true }});
node('requests').listeners.click(mobileTap);
assert(!mobileSelectors.includes('tr.request'), 'a mobile row tap does not open a second chain');
const detailSelectors = [];
const detailTap = {{ target: {{ closest: (selector) => {{ detailSelectors.push(selector); return selector === '.mobile-request-more' ? {{}} : null; }} }} }};
sandbox.window.matchMedia = () => ({{ matches: false }});
node('requests').listeners.click(detailTap);
assert(!detailSelectors.includes('tr.request'), 'a detail disclosure does not toggle the desktop chain');
probe.state.live.set(2, {{ id: 2, since: 100000, attemptSince: 100000, first: null, stream: false, session: 's2', app: 'OWUI', model: 'daedalus/auto', effort: 'medium', pool: 'moros', via: 'p/live', fallbacks: 0 }});
probe.renderLive();
const liveHtml = node('live').innerHTML;
const liveCells = [...liveHtml.matchAll(/<td[^>]*>/g)].map((m) => m[0]);
assert.strictEqual(liveCells.length, 12, 'every live column is a cell');
assert(liveCells.every((td) => td.includes('role="cell"')), 'each live request cell keeps its table role');
assert.strictEqual((liveHtml.match(/class="cell-value"/g) || []).length, 12, 'each live request cell keeps its value');
assert.deepStrictEqual(mobileLabels(liveHtml), {json.dumps(labels)}, 'live request cells show their column names');
assert(liveHtml.includes('More · session, effort · 0 fallbacks'), 'live row details stay compact');
for (const value of ['s2', 'medium', '<span class="model-part">moros</span>']) assert(liveHtml.includes(value), 'live details keep ' + value);
assert(!liveHtml.includes('mobile-fallback-chain'), 'live rows do not show a fallback chain');
"""
  subprocess.run(["node", "-e", code], check=True)


def test_app_js_live_clocks_wait_for_the_first_token() -> None:
  """The stream clock stays empty until the first token, also on a request without a stream."""
  code = """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = { tickLive, state };";
const nodes = new Map();
const node = (id) => {
  if (!nodes.has(id)) nodes.set(id, { innerHTML: '', children: [], listeners: {}, classList: { toggle: () => {} }, addEventListener(type, handler) { this.listeners[type] = handler; } });
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
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: liveNode, querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [], addEventListener: () => {} },
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
assert(stream.textContent.endsWith('s'), 'a plain request counts its whole time after the first token: ' + stream.textContent);
probe.state.live.set(2, row({ first: Date.now(), stream: true }));
probe.tickLive();
assert(stream.textContent.endsWith('s'), 'a streaming request counts the time after the first token: ' + stream.textContent);
"""
  subprocess.run(["node", "-e", code], check=True)


def test_phone_panels_clear_the_last_row() -> None:
  """The bottom of a panel clears its last row on a phone."""
  root = Path(__file__).resolve().parent.parent.parent
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  mobile = css.split("@media (max-width: 720px) {", 1)[1].split("\n}", 1)[0]
  assert "#app > main { padding-bottom: 76px; }" in mobile, "the screen bottom clears"
  assert ".panel { padding-bottom: 8px; }" in mobile, "the panel edge clears"


def test_keys_page_holds_its_labels_on_one_line() -> None:
  """The keys table keeps the key value and the Delete label whole on a phone."""
  root = Path(__file__).resolve().parent.parent.parent
  page = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert '<table class="keys">' in page, "the keys table carries its class"
  css = (root / "daedalus/dashboard/ui/style.css").read_text(encoding="utf-8")
  assert ".keys td:nth-child(2) { white-space: nowrap; }" in css, (
    "the key value stays on one line"
  )
  assert "button { font: inherit; cursor: pointer; white-space: nowrap; }" in css, (
    "a button label never wraps"
  )
  app = (root / "daedalus/dashboard/ui/app.js").read_text(encoding="utf-8")
  assert '<td class="num muted mono">' in app, "the key value reads in the mono font"


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
  assert head.count('title="') == 12, "each Requests head carries a hover text"
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
    "How many models the fallback chain tried",
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
  assert ".requests .mobile-request-more { display: none; }" in base_css
  assert "tr.request.has-chain { cursor: pointer; }" in base_css
  assert "tr.request.has-chain.open td { background: var(--field); }" in base_css
  assert (
    "@media (hover: hover) {\n  tr.request.has-chain:hover td "
    "{ background: var(--field); }\n}" in base_css
  ), "the row hover needs a pointer, a touch tap keeps no background"
  assert ".requests .mobile-request-more { display: block; width: 100%; }" in mobile_css
  assert ".requests tr.request, .requests tr.live-row {" in mobile_css
  assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in mobile_css
  assert (
    ".requests tr.request > td:nth-child(4), .requests tr.live-row > td:nth-child(4) "
    "{ grid-column: 1 / 3; grid-row: 2; }"
  ) in mobile_css
  assert "grid-column: 1 / -1; grid-row: 4;" in mobile_css
  assert ".requests .mobile-request-meta" in mobile_css
  assert (
    ".requests .mobile-fallback-chain ol { list-style: none; margin: 5px 0 0; "
    "padding: 0; }"
  ) in mobile_css
  assert ".requests tr.request .caret { display: none; }" in mobile_css
  assert "font-size: 11px; white-space: nowrap;" in narrow_css
  assert ".requests tr.live-row > td:nth-child(1) .pulse" in narrow_css
  assert (
    "td:nth-child(9), .requests tr.live-row > td:nth-child(9) "
    "{ grid-column: 1 / 3; grid-row: 3; }"
  ) in narrow_css
  assert (
    "td:nth-child(12), .requests tr.live-row > td:nth-child(12) "
    "{ grid-column: 3 / 5; grid-row: 4; }"
  ) in narrow_css
  assert (
    ".requests tr.request > td.fallbacks-cell, .requests tr.live-row > "
    "td.fallbacks-cell { grid-row: 5; }"
  ) in narrow_css


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
    classList: { toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node, querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [], addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {} },
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
  assert "&middot; Next <b>" in source, "the catalog label reads Next"
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
    classList: { toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node, querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [], addEventListener: () => {} },
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
    classList: { toggle: () => {} } });
  return nodes.get(id);
};
const sandbox = {
  esc: (text) => String(text ?? ''), seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value), toLocaleString: (value) => String(value),
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  document: { hidden: false, documentElement: { dataset: {} }, getElementById: node, querySelector: () => ({ firstChild: { textContent: 'M' } }), querySelectorAll: () => [], addEventListener: () => {} },
  navigator: {}, location: { hash: '' }, window: { addEventListener: () => {} },
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
const src = fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = {{ renderLimits, state }};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) nodes.set(id, {{ innerHTML: '', value: '', checked: false, textContent: '', hidden: false, addEventListener: () => {{}}, classList: {{ toggle: () => {{}} }} }});
  return nodes.get(id);
}};
const sandbox = {{
  esc: (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }})[c]),
  seconds: (value) => value.toFixed(3) + 's',
  floorCount: (value) => String(value),
  toLocaleString: (value) => String(value),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{ hidden: false, documentElement: {{ dataset: {{}} }}, getElementById: node, querySelector: () => ({{ firstChild: {{ textContent: 'M' }} }}), querySelectorAll: () => [], addEventListener: () => {{}} }},
  navigator: {{}}, location: {{ hash: '' }}, window: {{ addEventListener: () => {{}} }},
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
    const made = {{ id, innerHTML: '', value: '', checked: false, textContent: '', disabled: false, hidden: false, handlers: {{}},
      addEventListener: (type, fn) => {{ (made.handlers[type] ||= []).push(fn); }},
      classList: {{ toggle: () => {{}} }}, closest: () => made }};
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
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }} }}),
    querySelectorAll: () => [],
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


def test_app_js_settings_switches() -> None:
  """Every boolean setting renders as a checkbox, so a loaded file reports no change."""
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
// The hint of a check row sits behind the info icon on a desktop, and the icon names it for a reader.
const hint = html.slice(at, at + 600);
assert(hint.includes('class="hint"') && hint.includes('aria-describedby="set-affinity-change_on_draw-hint"'), 'the check row carries its hint icon');
assert(hint.includes('role="tooltip"'), 'the hint text serves as the tooltip');
for (const id of ['set-affinity-change_on_draw', 'set-weights-enabled', 'set-pacing-enabled']) node(id).checked = true;
node('set-affinity-mode').value = 'session';
node('set-dashboard-theme').value = 'system';
node('set-dashboard-time_format').value = '24h';
assert.strictEqual(JSON.stringify(probe.settingsChanges()), '{{}}', 'a loaded file reports no change');
// The keyword fields are chip lists with a + adder, not a text box.
const list = html.slice(Math.max(0, html.indexOf('id="set-escalation-keywords"') - 40), html.indexOf('id="set-escalation-keywords"') + 2600);
assert(list.includes('class="pills"'), 'the keyword field is a chip list');
assert(list.includes('class="pill"'), 'each keyword is a chip');
const adder = html.indexOf('data-setting-add=');
assert(adder > 0, 'the + adder is in the form');
assert(html.slice(adder, adder + 80).includes('escalation'), 'the + adder names its list');
assert(!html.includes('<textarea id="set-escalation-keywords"'), 'no text box for the keywords');
// A dropped chip leaves the list, and the change reaches the save payload.
const first = probe.listValue('escalation', 'keywords')[0];
probe.dropSetting(['escalation', 'keywords', 0]);
assert(!probe.listValue('escalation', 'keywords').includes(first), 'the chip left the list');
assert.deepStrictEqual(probe.settingsChanges().escalation.keywords, probe.listValue('escalation', 'keywords'), 'the change reaches the save payload');
// A new value joins the list 1 time.
probe.setListValue('switch', 'keywords', [...probe.listValue('switch', 'keywords'), 'clanker', 'clanker']);
assert.strictEqual(probe.listValue('switch', 'keywords').length, probe.state.settings.defaults.switch.keywords.length + 2, 'the raw list takes both');
""",
  )


def test_app_js_hook_rows() -> None:
  """A request point holds 1 row per hook file, picked from the files of the config folder."""
  payload = json.dumps(
    {
      "path": "config/daedalus.yml",
      "headroom_available": False,
      "text": "",
      "hook_files": [
        "hooks/openwebui_retry.py",
        "hooks/pick.py",
        "hooks/cheapest_output.py",
      ],
      "defaults": settings.DEFAULTS,
      "file": {
        "request_hooks": {"on-request": ["hooks/openwebui_retry.py", "hooks/pick.py"]}
      },
    }
  )
  run_app_js(
    "state, renderSettings, settingsChanges, setListValue",
    f"""
probe.state.settings = {payload};
probe.renderSettings();
const html = node('settings').innerHTML;
assert(html.includes('>On request<') && html.includes('>On chunk<'), 'each point has a row');
const at = html.indexOf('id="set-request_hooks-on-request"');
assert(at > 0, 'the on-request row names its host');
const box = html.slice(at, at + 1400);
assert(box.includes('data-hook-select='), 'a row picks a file');
assert(box.includes('data-hook-add='), 'the point takes another file');
assert(box.includes('hooks/openwebui_retry.py') && box.includes('hooks/cheapest_output.py'), 'the picker lists the hook files');
assert(box.includes('value="hooks/openwebui_retry.py" selected'), 'the saved file shows as picked');
assert(!('request_hooks' in probe.settingsChanges()), 'a loaded hook file reports no change');
// A new row reaches the save payload, and an empty point clears the key.
probe.setListValue('request_hooks', 'on-chunk', ['hooks/pick.py']);
assert.deepStrictEqual(probe.settingsChanges().request_hooks['on-chunk'], ['hooks/pick.py'], 'the new row reaches the save payload');
probe.setListValue('request_hooks', 'on-request', []);
assert.strictEqual(probe.settingsChanges().request_hooks['on-request'], null, 'an empty point clears the key');
""",
  )


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
const ids = ['set-affinity-mode', 'set-affinity-change_on_draw', 'set-affinity-idle', 'set-affinity-stay', 'set-affinity-count', 'set-affinity-chance', 'set-affinity-slow', 'set-affinity-penalty'];
const shown = () => ids.filter((id) => !node(id).hidden);
// The 2 pin-draw values come with session only: under race the pinned model leads, so no draw runs.
const expect = {{
  none: ['set-affinity-mode'],
  session: ['set-affinity-mode', 'set-affinity-change_on_draw', 'set-affinity-idle', 'set-affinity-stay'],
  race: ['set-affinity-mode', 'set-affinity-idle', 'set-affinity-count', 'set-affinity-chance', 'set-affinity-slow', 'set-affinity-penalty'],
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
// A pick that differs from the file turns the Save button on and reaches the payload.
node('settings-save').disabled = true;
const select = node('set-affinity-mode');
select.value = 'race';
node('settings').handlers.input.forEach((fn) => fn({{ target: select }}));
assert.strictEqual(node('settings-save').disabled, false, 'the Save button wakes');
assert.strictEqual(probe.settingsChanges().affinity.mode, 'race', 'the pick reaches the save payload');
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


def test_login_form(client: TestClient) -> None:
  """The login hints fill in admin, show the master key note, and say whether the cookie is live."""
  env = {dashboard.DAEDALUS_USERNAME: "owner", dashboard.DAEDALUS_PASSWORD: "secret"}
  cases = [
    ({}, "admin", True),
    (env, None, False),
    ({dashboard.DAEDALUS_USERNAME: "owner"}, None, True),
  ]
  for values, username, master in cases:
    for name in env:
      os.environ.pop(name, None)
    os.environ.update(values)
    hints = TestClient(api.app)
    found = hints.get("/ui/api/login").json()
    assert found == {
      "username": username,
      "master": master,
      "version": daedalus.__version__,
      "session": False,
    }, (values, found)
    os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER
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
    os.environ.pop(dashboard.DAEDALUS_MASTER_KEY, None)
  for name in env:
    os.environ.pop(name, None)


def test_login(client: TestClient) -> None:
  os.environ.pop(dashboard.DAEDALUS_MASTER_KEY, None)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 503, "no master key"
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = "short"
  assert client.post("/ui/api/login", json=login).status_code == 503, "a short key"
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER
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
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER + "-new"
  assert client.get("/ui/api/status").status_code == 401, "a new master key ends it"
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER
  assert client.post("/ui/api/login", json=login).status_code == 200
  os.environ[dashboard.DAEDALUS_USERNAME] = "owner"
  os.environ[dashboard.DAEDALUS_PASSWORD] = "ui password"
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
  del os.environ[dashboard.DAEDALUS_USERNAME], os.environ[dashboard.DAEDALUS_PASSWORD]
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
  assert TestClient(api.app).get("/ui/api/provider-defaults").status_code == 401


def test_data(client: TestClient) -> None:
  response = client.get("/ui/api/models")
  assert response.status_code == 200, response.text
  models = response.json()
  assert [row["id"] for row in models] == ["p/big", "p/small", "p/embed"], "all rows"
  assert models[0] == {
    "id": "p/big",
    "mode": "chat",
    "max_input_tokens": 1000,
    "tools": True,
    "reasoning": True,
    "effort": "medium",
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
  folder = state_folder / "config" / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  (folder / "openwebui_retry.py").write_text(
    Path("config/hooks/openwebui_retry.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  body = client.get("/ui/api/hooks").json()
  assert body == {"legend": [["rtN", "A repeat picked another model, N times"]]}, body


def test_files(
  client: TestClient, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  listing = client.get("/ui/api/files")
  assert isinstance(listing.json(), list), listing.text[:400]
  names = [item["path"] for item in listing.json()]
  assert names == [str(path) for path in dashboard.FILES], names
  path = str(settings.DEFAULT_PATH)
  assert (
    client.put("/ui/api/files", json={"path": path, "text": ""}).status_code == 400
  ), "the Settings page owns the settings file"
  shown = client.get("/ui/api/settings").json()
  assert shown["file"] == {
    "request_hooks": {
      "on-request": ["hooks/openwebui_retry.py"],
      "on-chunk": ["hooks/pick.py"],
    }
  }, "the shipped file holds the changes only"
  root = folder / "config" / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "picked.py").write_text(
    "def on_answer(value, model, headers):\n  return value\n"
  )
  assert "hooks/picked.py" in client.get("/ui/api/settings").json()["hook_files"], (
    "the settings page lists the hook files, so a row picks its own"
  )
  assert shown["defaults"]["timeouts"]["slow"] == 30.0, shown
  assert shown["defaults"]["weights"]["fault"] == 0.5, shown
  assert shown["defaults"]["loops"] == {
    "calls": 3,
    "repeats": 4,
    "shortest": 20,
    "longest": 2000,
  }, shown
  assert shown["headroom_available"] is False, shown

  async def ready() -> bool:
    return True

  monkeypatch.setattr(headroom, "available", ready)
  assert client.get("/ui/api/settings").json()["headroom_available"] is True
  before = settings.DEFAULT_PATH.read_text()
  bad = client.put("/ui/api/settings", json={"changes": {"weights": {"fault": 0}}})
  assert bad.status_code == 422 and "above 0" in bad.text, bad.text
  bad = client.put("/ui/api/settings", json={"changes": {"loops": {"calls": 1}}})
  assert bad.status_code == 422 and "between 2 and 100" in bad.text, bad.text
  huge = client.put(
    "/ui/api/settings",
    json={"changes": {"timeouts": {"request": 99999999999999999999}}},
  )
  assert huge.status_code == 422 and "at most 86400 seconds" in huge.text, huge.text
  assert settings.DEFAULT_PATH.read_text() == before, "a bad value is not written"
  unknown = client.put("/ui/api/settings", json={"changes": {"x": {"y": 1}}})
  assert unknown.status_code == 422, unknown.text
  assert client.put("/ui/api/settings", json={"changes": 1}).status_code == 400
  changes = {
    "timeouts": {"slow": 12},
    "catalog": {"every": 0},
    "loops": {"calls": 5, "repeats": 6, "shortest": 10, "longest": 3000},
  }
  saved = client.put("/ui/api/settings", json={"changes": changes})
  assert saved.status_code == 200, saved.text
  text = settings.DEFAULT_PATH.read_text()
  assert "  slow: 12" in text, "the new value is written"
  assert "# on-request sets the key of the turn." in text, "the comments stay"
  assert "  every: 0" in text, text
  assert api.SLOW_SECONDS == 12.0, "the save applies the settings"
  assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (5, 6, 10, 3000)
  cleared = client.put(
    "/ui/api/settings", json={"changes": {"timeouts": {"slow": None}}}
  )
  assert (
    cleared.status_code == 200 and "  slow:" not in settings.DEFAULT_PATH.read_text()
  ), "a cleared key leaves the file"
  assert api.SLOW_SECONDS == 30.0, "the default slow time"
  reset = client.put("/ui/api/settings", json={"changes": {"catalog": {"every": None}}})
  assert reset.status_code == 200, reset.text
  assert "  every:" not in settings.DEFAULT_PATH.read_text(), (
    "a cleared key leaves the file"
  )
  words = {"escalation": {"keywords": ["ultrathink", "yes", "think hard"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "keywords:\n    - ultrathink\n    - 'yes'\n    - think hard\n" in text, text
  assert api.KEYWORDS and api.KEYWORDS.search("please ultrathink"), "the save applies"
  words = {"escalation": {"keywords": ["audit"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "keywords:\n    - audit\n" in text and "ultrathink" not in text, text
  words = {"escalation": {"keywords": None}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "  keywords:" not in text, "a cleared key leaves the file"
  assert api.KEYWORDS and api.KEYWORDS.search("ultrathink"), (
    "an empty field falls back to the code default"
  )
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"request_hooks": {"on-request": 3}}}
    ).status_code
    == 422
  ), "a hook path is a string"
  hook = {
    "request_hooks": {"on-request": ["hooks/openwebui_retry.py", "hooks/picked.py"]}
  }
  assert client.put("/ui/api/settings", json={"changes": hook}).status_code == 200
  assert api.REQUEST_HOOKS == {
    "on-request": ["hooks/openwebui_retry.py", "hooks/picked.py"],
    "on-chunk": ["hooks/pick.py"],
  }, "the save applies each request hook of the point"
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"request_hooks": {"on-request": []}}}
    ).status_code
    == 200
  )
  assert api.REQUEST_HOOKS == {
    "on-request": [],
    "on-chunk": ["hooks/pick.py"],
  }, "an empty list turns the hook off"
  dark = {"dashboard": {"theme": "dark"}}
  assert client.put("/ui/api/settings", json={"changes": dark}).status_code == 200
  assert client.get("/ui/api/settings").json()["file"]["dashboard"]["theme"] == "dark"
  blue = client.put(
    "/ui/api/settings", json={"changes": {"dashboard": {"theme": "blue"}}}
  )
  assert blue.status_code == 422 and "system, light or dark" in blue.text, blue.text
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"dashboard": {"theme": None}}}
    ).status_code
    == 200
  )
  # The YAML view reads and writes the file text.
  text = client.get("/ui/api/settings").json()["text"]
  assert text == settings.DEFAULT_PATH.read_text(), "the text of the file"
  broken = client.put("/ui/api/settings", json={"text": "weights:\n  fault: 0\n"})
  assert broken.status_code == 422 and "above 0" in broken.text, broken.text
  assert settings.DEFAULT_PATH.read_text() == text, "a bad text is not written"
  edited = text + "timeouts:\n  slow: 14 # edited\n"
  saved = client.put("/ui/api/settings", json={"text": edited})
  assert saved.status_code == 200 and saved.json()["text"] == edited, saved.text
  assert settings.DEFAULT_PATH.read_text() == edited and api.SLOW_SECONDS == 14.0
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
