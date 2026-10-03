"""Build the static demo of the dashboard for GitHub Pages, and capture its fixtures.

`--capture` runs the dashboard against a demo state, in this process, and writes the JSON
fixtures. `--out` copies the UI, adds `demo.js` that answers each call from the fixtures,
and patches the page to load it. The demo fills the Requests tab and simulates live
requests, so the pages move as they do on a server.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from daedalus import config, dashboard, store
from daedalus.catalog import discovery
from daedalus.config import settings
from daedalus.providers import hooks
from daedalus.server import api, upstream
from daedalus.store import keys, saved_env

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "daedalus" / "dashboard" / "ui"
FIXTURES = ROOT / "scripts" / "pages_fixtures.json"
# The master key of the capture session. It never leaves the capture process.
DEMO_MASTER = "daedalus-demo-master-key"
# The calls that the page reads, with the query that the page sends.
ENDPOINTS = (
  "status",
  "models",
  "pools",
  "requests?limit=50",
  "limits",
  "keys",
  "files",
  "env",
  "provider-keys",
  "provider-defaults",
  "settings",
  "login",
)
DEMO_FREE = """demo:
  api_key: db:DEMO_API_KEY
  api_base: https://demo.invalid/v1
  tier:
    TIER-A: ['atlas-2', 'lumen-vision', 'atlas-2-ultra-long-context-experimental-2026-10']
    TIER-B: ['atlas-1']
    TIER-C: ['beacon-2']
    TIER-D: ['echo-mini']
  models:
    atlas-2:
      max_input_tokens: 200000
      max_output_tokens: 32000
      tools: true
      supports_reasoning: true
    atlas-1:
      max_input_tokens: 131072
      max_output_tokens: 16000
      tools: true
    beacon-2:
      max_input_tokens: 128000
      max_output_tokens: 8000
    echo-mini:
      max_input_tokens: 32000
      max_output_tokens: 4000
    lumen-vision:
      max_input_tokens: 128000
      tools: true
      supports_vision: true
    atlas-2-ultra-long-context-experimental-2026-10:
      max_input_tokens: 1048576
      max_output_tokens: 64000
      tools: true
    scribe-audio:
      mode: audio_transcription
    canvas-image:
      mode: image_generation
    index-embed:
      mode: embedding
atlas:
  api_key: db:DEMO_API_KEY
  api_base: https://demo.invalid/v1
  tier:
    TIER-A: ['model-slug-with-a-fairly-long-name-2026']
  models:
    model-slug-with-a-fairly-long-name-2026:
      max_input_tokens: 262144
      max_output_tokens: 32768
      tools: true
demo-long-provider-name:
  api_key: db:DEMO_API_KEY
  api_base: https://demo.invalid/v1
  models:
    '*': {}
"""
# A file of a provider that the main file also sets, so the tab shows the shadow note.
DEMO_ATLAS = """api_key: db:DEMO_API_KEY
api_base: https://demo.invalid/v1
tier:
  TIER-A: ['model-slug-with-a-fairly-long-name-2026']
"""
DEMO_ROWS: tuple[dict[str, Any], ...] = (
  {
    "id": "demo/atlas-2",
    "max_input_tokens": 200000,
    "max_output_tokens": 32000,
    "supports_function_calling": True,
    "supports_reasoning": True,
  },
  {
    "id": "demo/atlas-2-ultra-long-context-experimental-2026-10",
    "max_input_tokens": 1048576,
    "max_output_tokens": 64000,
    "supports_function_calling": True,
  },
  {
    "id": "demo/atlas-1",
    "max_input_tokens": 131072,
    "max_output_tokens": 16000,
    "supports_function_calling": True,
  },
  {"id": "demo/beacon-2", "max_input_tokens": 128000, "max_output_tokens": 8000},
  {"id": "demo/echo-mini", "max_input_tokens": 32000, "max_output_tokens": 4000},
  {
    "id": "demo/lumen-vision",
    "max_input_tokens": 128000,
    "supports_function_calling": True,
    "supports_vision": True,
  },
  {"id": "demo/scribe-audio", "mode": "audio_transcription"},
  {"id": "demo/canvas-image", "mode": "image_generation"},
  {"id": "demo/index-embed", "mode": "embedding"},
  {
    "id": "atlas/model-slug-with-a-fairly-long-name-2026",
    "max_input_tokens": 262144,
    "max_output_tokens": 32768,
    "supports_function_calling": True,
  },
  # A model that the config gives no tier: the table shows it, and no pool holds it.
  {"id": "demo-long-provider-name/another-model-with-a-long-slug-2026"},
)
TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {}}}
# The models that the demo makes fail or rate limit, for 1 row each.
DOWN = set()
LIMITED = set()
# The prompts of the captured rows, in the order that the table shows them.
DEMO_CALLS = (
  ("daedalus/auto", "Draft a short release note for version 0.3."),
  ("daedalus/deinos", "Write a haiku about a slow deployment."),
  ("daedalus/koinos", "Name 5 fruit trees."),
  ("daedalus/sophos", "What is the weather in Manila?"),
  ("demo/atlas-2", "Summarize this log line: upstream timeout after 30 s."),
  ("daedalus/moros", "Count to three."),
  ("daedalus/koinos", "Say hello in Japanese."),
)
DEMO_STREAM = DEMO_CALLS[2][0]
DEMO_TOOLS = DEMO_CALLS[3][0]
DEMO_DOWN = DEMO_CALLS[5][0]
DEMO_LIMITED = DEMO_CALLS[6][0]
DEMO_KEYS = (
  "demo-client",
  "demo-openwebui",
  "demo-integration-name-at-the-40-char-max",
)
# The demo has no provider key. The page shows the value masked, as a saved value does.
DEMO_VALUE = ("DEMO_API_KEY", "demo-value-0123456789")
# The rate-limit headers of a provider answer: the Limits page reads them.
DEMO_HEADERS = {
  "x-ratelimit-limit-requests": "14400",
  "x-ratelimit-remaining-requests": "14312",
  "x-ratelimit-limit-tokens": "4000000",
  "x-ratelimit-remaining-tokens": "3980123",
  "x-ratelimit-reset-requests": "37s",
  "x-ratelimit-reset-tokens": "12s",
}


def answer(request: httpx.Request) -> httpx.Response:
  """Answer 1 upstream call as an OpenAI-compatible provider does, with its failures."""
  if request.url.path.endswith("/audio/transcriptions"):
    return httpx.Response(200, json={"text": "Demo transcript."}, headers=DEMO_HEADERS)
  try:
    body = json.loads(request.content)
  except ValueError:
    return httpx.Response(400, json={"error": "a JSON body is required"})
  model = body.get("model", "demo/atlas-1")
  # The upstream body carries the slug of the provider, not the full model id.
  slug = model.split("/")[-1]
  if model in DOWN or slug in DOWN:
    return httpx.Response(503, json={"error": "the deployment is down"})
  if model in LIMITED or slug in LIMITED:
    return httpx.Response(
      429, json={"error": "rate limited"}, headers={"retry-after": "1800"}
    )
  if body.get("stream"):
    frames = [
      {"choices": [{"delta": {"role": "assistant"}, "index": 0}]},
      {"choices": [{"delta": {"content": "One\\n"}, "index": 0}]},
      {"choices": [{"delta": {"content": "Two\\n"}, "index": 0}]},
      {
        "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
        "usage": {"prompt_tokens": 14, "completion_tokens": 5},
      },
    ]
    text = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    return httpx.Response(200, text=f"{text}data: [DONE]\n\n", headers=DEMO_HEADERS)
  return httpx.Response(
    200,
    headers=DEMO_HEADERS,
    json={
      "id": "chatcmpl-demo",
      "object": "chat.completion",
      "model": model,
      "choices": [
        {
          "index": 0,
          "message": {"role": "assistant", "content": "Demo answer."},
          "finish_reason": "stop",
        }
      ],
      "usage": {"prompt_tokens": 24, "completion_tokens": 9},
    },
  )


def demo_files(folder: Path) -> tuple[Path, ...]:
  """Write the config of the demo, and return the files of the Providers tab."""
  provider = folder / "config" / "providers" / "free.yml"
  provider.parent.mkdir(parents=True)
  provider.write_text(DEMO_FREE, encoding="utf-8")
  atlas = provider.with_name("atlas.yml")
  atlas.write_text(DEMO_ATLAS, encoding="utf-8")
  settings_file = folder / "config" / "daedalus.yml"
  settings_file.write_text(settings.DEFAULT_PATH.read_text(encoding="utf-8"))
  return provider, atlas


def seed_saved() -> None:
  """Save the demo value before the config load, which resolves `db:DEMO_API_KEY`."""
  saved_env.save(store.MODELS_DB, *DEMO_VALUE)
  for name in DEMO_KEYS:
    keys.add(store.MODELS_DB, name)


def seed_state() -> None:
  """Set the states that the pages show, after the requests moved the weights."""
  penalties, cooldowns = api.PENALTIES, api.COOLDOWNS
  penalties.record_change("demo/atlas-1", penalties.fault)
  penalties.highest("demo-conversation-1", 3)
  penalties.highest("demo-conversation-2", 2)
  cooldowns.hold("demo/beacon-2", cooldowns.clock() + 26 * 3600, "reset")


def seed_requests(client: TestClient, auth: dict[str, str]) -> None:
  """Run the requests of the demo, 1 per row of the Requests tab."""
  for model, prompt in DEMO_CALLS:
    DOWN.clear()
    LIMITED.clear()
    body: dict[str, Any] = {
      "model": model,
      "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt},
      ],
    }
    if model == DEMO_STREAM:
      body["stream"] = True
    if model == DEMO_TOOLS:
      body["tools"] = [TOOL]
    if model == DEMO_DOWN:
      DOWN.add("echo-mini")
    if model == DEMO_LIMITED:
      LIMITED.add("beacon-2")
    client.post("/v1/chat/completions", json=body, headers=auth)
  DOWN.clear()
  LIMITED.clear()
  client.post(
    "/v1/audio/transcriptions",
    data={"model": "daedalus/graphos", "response_format": "text"},
    files={"file": ("demo.wav", b"RIFF-demo-audio", "audio/wav")},
    headers=auth,
  )


def capture() -> dict[str, Any]:
  """Run the dashboard against a demo state, and return the answer of each call."""
  with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    provider, _ = demo_files(folder)
    saved = (
      store.MODELS_DB,
      discovery.DUMP_DIR,
      hooks.CONFIG_DIR,
      dashboard.FILES,
      config.DEFAULT_PATH,
      settings.DEFAULT_PATH,
    )
    environ = dict(os.environ)
    try:
      store.MODELS_DB = folder / "state" / "models.sqlite3"
      discovery.DUMP_DIR = folder / "dump"
      hooks.CONFIG_DIR = folder / "hooks"
      dashboard.FILES = (provider,)
      config.DEFAULT_PATH = provider
      settings.DEFAULT_PATH = folder / "config" / "daedalus.yml"
      os.environ[dashboard.DAEDALUS_MASTER_KEY] = DEMO_MASTER
      store.migrate()
      store.write_store(DEMO_ROWS)
      seed_saved()
      config.set_config(config.load_config(provider))
      upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
      client = TestClient(api.app)
      auth = {"Authorization": f"Bearer {DEMO_MASTER}"}
      client.post(
        "/ui/api/login", json={"username": "admin", "password": DEMO_MASTER}
      ).raise_for_status()
      seed_requests(client, auth)
      seed_state()
      found = {
        name.split("?")[0]: client.get(f"/ui/api/{name}").json() for name in ENDPOINTS
      }
    finally:
      store.MODELS_DB, discovery.DUMP_DIR, hooks.CONFIG_DIR, dashboard.FILES = saved[:4]
      config.DEFAULT_PATH, settings.DEFAULT_PATH = saved[4:]
      os.environ.clear()
      os.environ.update(environ)
      upstream.set_client(None)
      config.set_config(None)
  # The temp folder of the capture never reaches the fixtures.
  return json.loads(json.dumps(found).replace(str(folder) + os.sep, ""))


DEMO_JS = """\
"use strict";
// The demo of the daedalus dashboard. The page is the file that the server serves;
// this script answers each `ui/api/` call from the fixtures, with no server and no login.
// It pre-fills the Requests tab, then keeps it rotating with simulated live requests.
const DEMO_FIXTURES = __FIXTURES__;
(() => {
  // The server keeps 500 rows. The demo does the same, and drops the oldest.
  const KEPT = 500;
  const PREFILL = 60;
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  // The media pools answer on their own paths.
  const MEDIA_PATH = {
    "daedalus/graphos": "/v1/audio/transcriptions",
    "daedalus/photos": "/v1/images/generations",
  };
  const gap = (min, max) => min + Math.random() * (max - min);
  const pick = (rows) => rows[Math.floor(Math.random() * rows.length)];
  const spread = (rows) => {
    // The captured rows share 1 second: the demo gives them a timeline of the last hours.
    const now = Date.now() / 1000;
    const found = [];
    for (let index = 0; found.length < PREFILL; index++) {
      const source = rows[index % rows.length];
      const at = now - index * gap(24, 96);
      found.push({
        ...source, at, session: index % 4 ? source.session : `demo-session-${index}`,
        attempts: (source.attempts || []).map((a) => ({ ...a })),
      });
    }
    return found;
  };
  DEMO_FIXTURES.requests = spread(DEMO_FIXTURES.requests);
  const json = (body, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    }));
  const slice = (url) => {
    const limit = +(new URL(url, location.origin).searchParams.get("limit") || 0);
    return limit > 0 ? DEMO_FIXTURES.requests.slice(0, limit) : DEMO_FIXTURES.requests;
  };
  const MARKER = "ui/api/";
  const real = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const at = url.indexOf(MARKER);
    if (at < 0) return real(input, init);
    const found = url.slice(at + MARKER.length).split(/[?#]/)[0];
    const path = found.endsWith("/") ? found.slice(0, -1) : found;
    if ((init.method || "GET").toUpperCase() !== "GET") {
      return json({ error: { message: "This is a static demo. The change is not saved." } }, 403);
    }
    if (path === "requests") return json(slice(url));
    if (!(path in DEMO_FIXTURES)) return json({}, 404);
    return json(DEMO_FIXTURES[path]);
  };
  const keep = (row) => {
    DEMO_FIXTURES.requests.unshift(row);
    DEMO_FIXTURES.requests.length = Math.min(DEMO_FIXTURES.requests.length, KEPT);
    DEMO_FIXTURES.status.sessions += 1;
  };
  // The live stream of a server: the demo sends the same events, in waves of 1 to 3 requests,
  // each with its own timings and a random gap before the next wave.
  window.EventSource = class {
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      this.next = 0;
      this.loop();
    }
    addEventListener(kind, call) {
      if (!this.listeners.has(kind)) this.listeners.set(kind, []);
      this.listeners.get(kind).push(call);
    }
    close() {
      this.closed = true;
    }
    send(kind, data) {
      if (this.closed) return;
      for (const call of this.listeners.get(kind) || []) call({ data: JSON.stringify(data) });
    }
    async loop() {
      await wait(gap(400, 900));
      this.send("live", []);
      while (!this.closed) {
        const wave = [];
        const count = 1 + Math.floor(Math.random() * 3);
        for (let n = 0; n < count; n++) wave.push(this.one(++this.next));
        await Promise.all(wave);
        await wait(gap(1200, 6000));
      }
    }
    async one(id) {
      // A captured row is the template, so each field keeps the shape and the type of the server.
      const template = pick(DEMO_FIXTURES.requests);
      const served = template.via;
      const round = (value) => Math.round(value * 1000) / 1000;
      const path = MEDIA_PATH[template.model] || "/v1/chat/completions";
      const row = { id, path, age: 0, attempt_age: 0, ttft: null };
      this.send("start", row);
      await wait(gap(250, 1100));
      Object.assign(row, {
        app: pick(["demo", "kilo", "owui"]), session: `demo-session-${id % 7}`, key: null,
        model: template.model, effort: template.effort, pool: template.pool, stream: template.stream,
        trying: served, via: null, attempts: [], tokens: null, fallbacks: "0",
        age: round(gap(0.3, 1.1)), attempt_age: round(gap(0.3, 1.1)),
      });
      this.send("update", row);
      await wait(gap(200, 900));
      const ttft = round(gap(0.4, 2.4));
      Object.assign(row, { trying: null, via: served, ttft });
      this.send("first", row);
      await wait(gap(150, 700));
      // The finished row carries the fields of `dashboard.record`: the status code, the text
      // times, and the answer.
      const done = {
        at: Date.now() / 1000, status: 200, seconds: round(ttft + gap(0.2, 2.0)),
        app: row.app, session: row.session, key: row.key, model: row.model, effort: row.effort,
        pool: row.pool, routed: null, transition: null, retry: null, loop: null,
        via: served, ttft: `${ttft.toFixed(3)}s`, stream: row.stream, fallbacks: "0",
        tokens: { estimate: false, input: 40 + id * 3, output: 12 + (id % 9) },
        attempts: [{ model: served, result: "answered", seconds: ttft, error: "" }],
      };
      this.send("end", { id, row: done });
      keep(done);
    }
  };
})();
"""


def build(out: Path) -> Path:
  """Copy the UI into `out`, add the demo script, and load it before the page."""
  fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
  if out.exists():
    shutil.rmtree(out)
  shutil.copytree(UI, out / "ui")
  (out / "demo.js").write_text(
    DEMO_JS.replace("__FIXTURES__", json.dumps(fixtures)), encoding="utf-8"
  )
  page = (UI / "index.html").read_text(encoding="utf-8")
  marker = '<script src="ui/app.js"></script>'
  if marker not in page:
    raise SystemExit(
      "index.html does not load ui/app.js: the demo build cannot patch it"
    )
  (out / "index.html").write_text(
    page.replace(marker, f'<script src="demo.js"></script>\n{marker}'), encoding="utf-8"
  )
  return out


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--capture", action="store_true", help="write the fixtures again")
  parser.add_argument("--out", default="_site", help="folder of the built demo")
  arguments = parser.parse_args(argv)
  if arguments.capture:
    FIXTURES.write_text(
      json.dumps(capture(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURES}")
  out = build(Path(arguments.out))
  print(f"built {out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
