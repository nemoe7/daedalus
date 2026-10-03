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
DEMO_FREE = """# The shipped provider names and model slugs, with demo keys and no upstream calls.
cloudflare:
  api_key: db:CLOUDFLARE_API_KEY
  api_base: https://api.cloudflare.com/client/v4/accounts/00000000000000000000000000000000/ai/v1
  tier:
    TIER-A:
      - "@cf/openai/gpt-oss-120b"
      - "@cf/mistralai/mistral-small-3.2-24b-instruct"
    TIER-B:
      - "@cf/zai-org/glm-4.7-flash"
      - "@cf/qwen/qwq-32b"
    TIER-C:
      - "@cf/meta/llama-4-scout-17b-16e-instruct"
    TIER-D:
      - "@cf/meta/llama-3.1-8b-instruct"
  models:
    "@cf/openai/gpt-oss-120b":
      max_input_tokens: 128000
      max_output_tokens: 16384
      tools: true
    "@cf/mistralai/mistral-small-3.2-24b-instruct":
      max_input_tokens: 128000
      max_output_tokens: 16384
      tools: true
      supports_vision: true
    "@cf/zai-org/glm-4.7-flash":
      max_input_tokens: 128000
      max_output_tokens: 16384
      tools: true
    "@cf/qwen/qwq-32b":
      max_input_tokens: 20000
      max_output_tokens: 4000
      tools: true
    "@cf/meta/llama-4-scout-17b-16e-instruct":
      max_input_tokens: 128000
      max_output_tokens: 16384
      tools: true
    "@cf/meta/llama-3.1-8b-instruct":
      max_input_tokens: 32000
      max_output_tokens: 4000
    "@cf/openai/whisper-large-v3-turbo":
      mode: audio_transcription
    "@cf/black-forest-labs/flux-1-schnell":
      mode: image_generation
    "@cf/baai/bge-m3":
      mode: embedding
openrouter:
  api_key: db:OPENROUTER_API_KEY
  api_base: https://openrouter.ai/api/v1
  tier:
    TIER-A:
      - "z-ai/glm-5.3-flash"
  models:
    "z-ai/glm-5.3-flash":
      max_input_tokens: 1310720
      max_output_tokens: 131072
      reasoning_effort: low
      tools: true
"""
# A file of a provider that the main file also sets, so the tab shows the shadow note.
DEMO_ATLAS = """api_key: db:OPENROUTER_API_KEY
api_base: https://openrouter.ai/api/v1
tier:
  TIER-A: ['z-ai/glm-5.3-flash']
"""
DEMO_ROWS: tuple[dict[str, Any], ...] = (
  {
    "id": "cloudflare/@cf/openai/gpt-oss-120b",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "supports_function_calling": True,
  },
  {
    "id": "cloudflare/@cf/mistralai/mistral-small-3.2-24b-instruct",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "supports_function_calling": True,
    "supports_vision": True,
  },
  {
    "id": "cloudflare/@cf/zai-org/glm-4.7-flash",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "supports_function_calling": True,
  },
  {
    "id": "cloudflare/@cf/qwen/qwq-32b",
    "max_input_tokens": 20000,
    "max_output_tokens": 4000,
    "supports_function_calling": True,
  },
  {
    "id": "cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "supports_function_calling": True,
  },
  {
    "id": "cloudflare/@cf/meta/llama-3.1-8b-instruct",
    "max_input_tokens": 32000,
    "max_output_tokens": 4000,
  },
  {
    "id": "cloudflare/@cf/openai/whisper-large-v3-turbo",
    "mode": "audio_transcription",
  },
  {
    "id": "cloudflare/@cf/black-forest-labs/flux-1-schnell",
    "mode": "image_generation",
  },
  {"id": "cloudflare/@cf/baai/bge-m3", "mode": "embedding"},
  {
    "id": "openrouter/z-ai/glm-5.3-flash",
    "max_input_tokens": 1310720,
    "max_output_tokens": 131072,
    "supports_function_calling": True,
    "supports_reasoning": True,
  },
)
TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {}}}
# The models that the demo makes fail or rate limit, for 1 row each.
DOWN = set()
LIMITED = set()
# The prompts of the captured rows, in the order that the table shows them.
DEMO_CALLS = (
  ("daedalus/moros", "Count to three."),
  ("daedalus/deinos", "Write a haiku about a slow deployment."),
  ("daedalus/koinos", "Name 5 fruit trees."),
  ("daedalus/sophos", "What is the weather in Manila?"),
  (
    "cloudflare/@cf/mistralai/mistral-small-3.2-24b-instruct",
    "Summarize this log line: upstream timeout after 30 s.",
  ),
  ("openrouter/z-ai/glm-5.3-flash", "Say hello in Japanese."),
  ("daedalus/koinos", "Draft a short release note for version 0.3."),
)
DEMO_STREAM = DEMO_CALLS[2][0]
DEMO_TOOLS = DEMO_CALLS[3][0]
DEMO_DOWN = DEMO_CALLS[0][0]
DEMO_LIMITED = DEMO_CALLS[6][0]
DEMO_KEYS = (
  "openwebui",
  "kilo-code",
  "openwebui-home-assistant-integration-001",
)
# The demo has no provider key. The page shows the value masked, as a saved value does.
DEMO_VALUES = (
  ("CLOUDFLARE_API_KEY", "not-a-real-cloudflare-key-0123"),
  ("OPENROUTER_API_KEY", "not-a-real-openrouter-key-0123"),
)
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
  path = request.url.path
  # Cloudflare transcribes on its native `run` endpoint, not on the OpenAI path.
  native = "/run/" in path
  if native or path.endswith("/audio/transcriptions"):
    text = "A short transcript of the recording."
    # The native answer wraps the text: the provider reads `result.text`.
    if native or "cloudflare" in request.url.host:
      return httpx.Response(200, json={"result": {"text": text}}, headers=DEMO_HEADERS)
    return httpx.Response(200, json={"text": text}, headers=DEMO_HEADERS)
  try:
    body = json.loads(request.content)
  except ValueError:
    return httpx.Response(400, json={"error": "a JSON body is required"})
  model = body.get("model", "z-ai/glm-5.3-flash")
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
  shadowed = provider.with_name("openrouter.yml")
  shadowed.write_text(DEMO_ATLAS, encoding="utf-8")
  settings_file = folder / "config" / "daedalus.yml"
  settings_file.write_text(settings.DEFAULT_PATH.read_text(encoding="utf-8"))
  return provider, shadowed


def seed_saved() -> None:
  """Save the demo values before the config load, which resolves `db:` keys."""
  for name, value in DEMO_VALUES:
    saved_env.save(store.MODELS_DB, name, value)
  for name in DEMO_KEYS:
    keys.add(store.MODELS_DB, name)


def seed_state() -> None:
  """Set the states that the pages show, after the requests moved the weights."""
  penalties, cooldowns = api.PENALTIES, api.COOLDOWNS
  penalties.record_change("cloudflare/@cf/openai/gpt-oss-120b", penalties.fault)
  penalties.highest("5d7e3b6", 3)
  penalties.highest("a57a308", 2)
  cooldowns.hold(
    "cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",
    cooldowns.clock() + 26 * 3600,
    "reset",
  )


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
      DOWN.add("@cf/meta/llama-3.1-8b-instruct")
    if model == DEMO_LIMITED:
      LIMITED.add("@cf/meta/llama-4-scout-17b-16e-instruct")
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
// The Requests tab starts empty, and the simulated live requests fill and rotate it.
const DEMO_FIXTURES = __FIXTURES__;
(() => {
  // The server keeps 500 rows. The demo does the same, and drops the oldest.
  const KEPT = 500;
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  const round = (value) => Math.round(value * 1000) / 1000;
  // The media models answer on their own paths.
  const MEDIA_PATH = {
    "cloudflare/@cf/openai/whisper-large-v3-turbo": "/v1/audio/transcriptions",
    "cloudflare/@cf/black-forest-labs/flux-1-schnell": "/v1/images/generations",
    "cloudflare/@cf/baai/bge-m3": "/v1/embeddings",
  };
  // A session reads like the server sends it: the first 7 characters of the key hash.
  const session = (n) => ((n * 2654435761) % 0xfffffff).toString(16).padStart(7, "0");
  const gap = (min, max) => min + Math.random() * (max - min);
  const pick = (rows) => rows[Math.floor(Math.random() * rows.length)];
  // The Requests tab starts empty. The captured rows come back as live traffic, so the
  // fallback chain and the rate limit show, and the waves continue after them.
  const SEEDED = DEMO_FIXTURES.requests.map((row) => ({ ...row }));
  DEMO_FIXTURES.requests = [];
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
      for (const row of SEEDED) {
        if (this.closed) return;
        await this.replay(row);
      }
      while (!this.closed) {
        const wave = [];
        const count = 1 + Math.floor(Math.random() * 3);
        for (let n = 0; n < count; n++) wave.push(this.one(++this.next));
        await Promise.all(wave);
        await wait(gap(1200, 6000));
      }
    }
    // One captured row, as a request that just finished, with the fields unchanged.
    async replay(row) {
      const id = ++this.next;
      const served = row.via || row.model;
      const live = {
        id, path: MEDIA_PATH[row.model] || "/v1/chat/completions",
        age: 0, attempt_age: 0, ttft: null,
      };
      this.send("start", live);
      await wait(gap(200, 700));
      Object.assign(live, {
        app: row.app, session: row.session, key: row.key, model: row.model, effort: row.effort,
        pool: row.pool, stream: row.stream, trying: served, via: null, attempts: [], tokens: null,
        fallbacks: "0", age: round(gap(0.3, 1.1)), attempt_age: round(gap(0.3, 1.1)),
      });
      this.send("update", live);
      await wait(gap(150, 500));
      const ttft = row.ttft ? parseFloat(row.ttft) : round(gap(0.4, 2.4));
      Object.assign(live, { trying: null, via: served, ttft });
      this.send("first", live);
      await wait(gap(120, 400));
      const done = { ...row, at: Date.now() / 1000 };
      this.send("end", { id, row: done });
      keep(done);
    }
    async one(id) {
      // A captured row is the template, so each field keeps the shape and the type of the server.
      const template = pick(SEEDED);
      const served = template.via;
      const path = MEDIA_PATH[template.model] || "/v1/chat/completions";
      const row = { id, path, age: 0, attempt_age: 0, ttft: null };
      this.send("start", row);
      await wait(gap(250, 1100));
      Object.assign(row, {
        app: pick(["OWUI", "Kilo"]), session: session(id), key: "master",
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
