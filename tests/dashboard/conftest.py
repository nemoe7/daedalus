import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import config, dashboard, store
from daedalus.config import settings
from daedalus.server import api, upstream

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


def _app_js_vm(extra: str) -> str:
  """Return a Node VM script with the DOM stubs needed at startup."""
  return f"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8');
const hosts = [{{ innerHTML: '' }}, {{ innerHTML: '' }}];
const classes = new Set();
const nav = {{
  scrollLeft: 30, clientWidth: 100, scrollWidth: 260,
  classList: {{ add: (name) => classes.add(name), remove: (name) => classes.delete(name),
    toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) }},
  addEventListener: () => {{}},
  style: {{ props: {{}}, setProperty(key, value) {{ this.props[key] = value; }} }},
}};
const byId = new Map();
const el = (id) => {{
  if (!byId.has(id)) byId.set(id, {{ innerHTML: '', textContent: '', addEventListener: () => {{}},
    after: () => {{}}, remove: () => {{}}, style: {{ setProperty() {{}} }},
    append: () => {{}}, appendChild: () => {{}}, prepend: () => {{}}, insertBefore: () => {{}},
    closest: () => null, querySelector: () => null, querySelectorAll: () => [],
    classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }} }});
  return byId.get(id);
}};
const sandbox = {{
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{
    hidden: false,
    documentElement: {{ dataset: {{}} }},
    getElementById: (id) => (id === 'nav' ? nav : el(id)),
    createElement: () => ({{ append: () => {{}}, appendChild: () => {{}}, prepend: () => {{}},
      insertBefore: () => {{}}, setAttribute: () => {{}}, addEventListener: () => {{}},
      remove: () => {{}}, closest: () => null, querySelector: () => null,
      querySelectorAll: () => [] }}),
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }} }}),
    querySelectorAll: (sel) => (sel === '[data-status]' ? hosts : []),
    addEventListener: () => {{}},
  }},
  navigator: {{}},
  location: {{ hash: '' }},
  window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}),
  $: (id) => (id === 'nav' ? nav : el(id)),
  setInterval: () => 0,
  clearInterval: () => {{}},
  __el: el,
  __probe: {{}},
}};
vm.createContext(sandbox);
vm.runInContext(src + String.fromCharCode(10) +
  'globalThis.__probe.askPair = typeof askPair === "function" ? askPair : undefined;', sandbox);
const LOCALE = sandbox.DAEDALUS_LOCALE;
{extra}
"""


def run_app_js(probe: str, body: str) -> None:
  """Run app.js in a Node VM with DOM stubs and named `__probe` exports."""
  code = f"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('daedalus/dashboard/ui/en.js', 'utf8') + "\\n" +
  fs.readFileSync('daedalus/dashboard/ui/app.js', 'utf8') + "\\nglobalThis.__probe = {{{probe}}};";
const nodes = new Map();
const node = (id) => {{
  if (!nodes.has(id)) {{
    const made = {{ id, innerHTML: '', value: '', checked: false, textContent: '', disabled: false, hidden: false,
      dataset: {{}}, handlers: {{}}, shown: false, style: {{ setProperty() {{}} }},
      isConnected: true, title: '', type: '',
      showModal: () => {{ made.shown = true; }},
      addEventListener: (type, fn) => {{ (made.handlers[type] ||= []).push(fn); }},
      remove: () => {{}}, append: () => {{}}, appendChild: () => {{}}, prepend: () => {{}},
      after: () => {{}}, setAttribute: () => {{}},
      classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }},
        closest: (sel) => (sel === '[data-section]' || sel === '#provider-form' ? null : made),
      querySelector: () => null, querySelectorAll: () => [] }};
    nodes.set(id, made);
  }}
  return nodes.get(id);
}};
const documentHandlers = {{}};
const sandbox = {{
  esc: (text) => String(text ?? ''),
  matchMedia: () => ({{ matches: false, addEventListener: () => {{}} }}),
  document: {{
    hidden: false,
    documentElement: {{ dataset: {{}} }},
    getElementById: node,
    createElement: (tag) => node('made-' + tag),
    querySelector: () => ({{ firstChild: {{ textContent: 'Models' }}, closest: () => null,
      querySelector: () => null, append: () => {{}}, appendChild: () => {{}},
      prepend: () => {{}}, remove: () => {{}} }}),
    querySelectorAll: () => ([]),
    addEventListener: (type, fn) => {{ (documentHandlers[type] ||= []).push(fn); }},
  }},
  navigator: {{}},
  location: {{ hash: '' }},
  history: {{ replaceState: () => {{}}, pushState: () => {{}} }},
  URLSearchParams,
  window: {{ addEventListener: () => {{}} }},
  getSelection: () => ({{ isCollapsed: true }}),
  console: {{ error: () => {{}} }},
  $: node,
  setInterval: () => 0,
  clearInterval: () => {{}},
}};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const assert = require('assert');
const LOCALE = sandbox.DAEDALUS_LOCALE;
const probe = sandbox.__probe;
{body}
"""
  subprocess.run(["node", "-e", code], check=True)


def _tar() -> bytes:
  """Return a commit-shaped archive with two hook files."""
  body = io.BytesIO()
  with tarfile.open(fileobj=body, mode="w:gz") as archive:
    for name, version in (("scanned.py", "1.2.0"), ("selected.py", "2.0.0")):
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


def invalid_request(response, status: int = 422) -> dict:
  """Check a rejected request and return its diagnostic fields."""
  assert response.status_code == status
  body = response.json() if hasattr(response, "json") else json.loads(response.body)
  error = body["error"]
  assert error["type"] == "invalid_request_error" and error["code"] == status
  assert isinstance(error["message"], str) and error["message"].strip()
  return body


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
      config,
      "DEFAULT_PATH",
      Path(shutil.copy("tests/fixtures/providers/free.yml", folder)),
    )
    patch.setattr(dashboard, "FILES", (config.DEFAULT_PATH,))
    patch.setattr(api, "get_config", lambda: CONFIG)
    # The bearer header checks the environment on every request, so the
    # fixture owns the key instead of borrowing an earlier test's patch.
    patch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    session = TestClient(api.app, headers=AUTH)
    # The UI endpoints take a session cookie: log in once for the whole module
    # instead of borrowing an earlier test's cookie jar.
    login = session.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    )
    assert login.status_code == 200, login.text
    yield session
  upstream.set_client(None)
  config.set_config(None)


MIN_FAST_SECONDS = 0.15
MAX_FAST_SECONDS = 0.22
MOBILE_TOUCH_MIN_PX = 44
EXPECTED_TILE_PX = 24
EXPECTED_DESKTOP_TILE_GAP_PX = 8
EXPECTED_TABLET_TILE_GAP_PX = 10
EXPECTED_ICON_PX = 16
MIN_WCAG_AA_CONTRAST = 4.5


def _wcag_luminance(hex_color: str) -> float:
  channels = [int(hex_color[index : index + 2], 16) / 255.0 for index in (1, 3, 5)]
  linear = [
    value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    for value in channels
  ]
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
