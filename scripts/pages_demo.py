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
import subprocess
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
# The 2 providers of the balance cards, with the `hourly_requests` cap of the Limits page.
kilo:
  api_key: db:KILO_API_KEY
  api_base: https://api.kilo.ai/api/gateway
  hourly_requests: 200
pollinations:
  api_key: db:POLLINATIONS_API_KEY
  api_base: https://gen.pollinations.ai/v1
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
  ("KILO_API_KEY", "not-a-real-kilo-key-0123"),
  ("POLLINATIONS_API_KEY", "not-a-real-pollinations-key-0123"),
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
  host = request.url.host
  # The balance endpoints of the Limits page: the shape of each provider answer.
  if host == "openrouter.ai" and path.endswith("/key"):
    return httpx.Response(
      200,
      json={"data": {"free_model_daily_requests": {"remaining": 991, "limit": 1000}}},
    )
  if host == "api.kilo.ai":
    return httpx.Response(200, json={"balance": 0.0})
  if host == "gen.pollinations.ai":
    return httpx.Response(200, json={"balance": 0.25})
  if host == "api.cloudflare.com" and path.endswith("/graphql"):
    return httpx.Response(
      200,
      json={
        "data": {
          "viewer": {
            "accounts": [{"aiInferenceAdaptiveGroups": [{"sum": {"totalNeurons": 0}}]}]
          }
        },
        "errors": [],
      },
    )
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


# The rows that the mock provider cannot reach: the client app, the effort, a retry, a loop
# and a routed pool. Each row starts from a real row of the capture, so every field keeps the
# shape of `dashboard.record`.
DEMO_SHAPES = (
  {
    "app": "OWUI",
    "effort": "high",
    "pool": "koinos",
    "routed": "moros",
    "transition": {
      "from_model": "cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",
      "from_pool": "koinos",
      "reason": "esc",
      "to_model": "cloudflare/@cf/zai-org/glm-4.7-flash",
      "to_pool": "deinos",
    },
  },
  {
    "app": "Kilo",
    "retry": "1",
    "fallbacks": "1",
    "status": 200,
    "attempts": [
      {
        "model": "cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",
        "result": "error",
        "seconds": 0.412,
        "error": "the deployment is down",
        "weight_change": {"from": 1.0, "to": 0.5},
      },
      {
        "model": "cloudflare/@cf/zai-org/glm-4.7-flash",
        "result": "answered",
        "seconds": 0.884,
        "error": "",
        "cooldown": {"seconds": 1800, "reason": "retry-after"},
        "weight_change": {"from": 0.5, "to": 0.75},
      },
    ],
  },
  {
    "app": "Kilo",
    "effort": "low",
    "loop": "2",
    "pool": "sophos",
    "tokens": {"estimate": True, "input": 1200, "output": 240},
  },
)


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
  rows = dashboard.HISTORY.latest(1)
  if not rows:
    return
  for shape in DEMO_SHAPES:
    row = dict(rows[0])
    row.update(shape)
    row["at"] = cooldowns.clock()
    row["seconds"] = round(row.get("seconds") or 1.0, 3)
    dashboard.HISTORY.add(row)


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
      # The Check now button of the Limits page: the balances and the checked time.
      client.post("/ui/api/limits", headers=auth).raise_for_status()
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
  // A demo cannot save, so the reload warning of the page never applies here.
  const listen = window.addEventListener.bind(window);
  window.addEventListener = (kind, ...rest) =>
    kind === "beforeunload" ? undefined : listen(kind, ...rest);
  // The server keeps 500 rows. The demo does the same, and drops the oldest.
  const KEPT = 500;
  // The requests of this page, by model. The limits check reads the count.
  const USED = new Map();
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
  // The times of a row in flight, as `Live` keeps them: the request time and the attempt time.
  const CLOCKS = new Map();
  const began = (id) => {
    const now = Date.now();
    CLOCKS.set(id, { at: now, attempt: now });
  };
  const attempted = (id) => (CLOCKS.get(id).attempt = Date.now());
  const ages = (id) => {
    const clock = CLOCKS.get(id);
    const now = Date.now();
    return {
      age: round((now - clock.at) / 1000),
      attempt_age: round((now - clock.attempt) / 1000),
    };
  };
  // The Requests tab starts empty. The captured rows come back as live traffic, so the
  // fallback chain and the rate limit show, and the waves continue after them.
  const SEEDED = DEMO_FIXTURES.requests.map((row) => ({ ...row }));
  DEMO_FIXTURES.requests = [];
  // The captured catalog clock shifts to the viewer once: the last build 10 minutes ago,
  // and the next in 2 hours.
  const opened = Date.now() / 1000;
  DEMO_FIXTURES.status.catalog.built = opened - 600;
  DEMO_FIXTURES.status.catalog.next = opened + 7200;
  const json = (body, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    }));
  const slice = (url) => {
    const limit = +(new URL(url, location.origin).searchParams.get("limit") || 0);
    return limit > 0 ? DEMO_FIXTURES.requests.slice(0, limit) : DEMO_FIXTURES.requests;
  };
  // A catalog rebuild is a page job in the demo. The chip reads rebuilding, and the
  // wait matches the real run. No provider is read.
  const REBUILD_MS = 26000;
  const rebuild = () => {
    if (DEMO_FIXTURES.status.catalog.rebuilding) {
      const error = { message: "A catalog rebuild runs now.", type: "invalid_request_error", code: 409 };
      return json({ error }, 409);
    }
    DEMO_FIXTURES.status.catalog.rebuilding = true;
    setTimeout(() => {
      DEMO_FIXTURES.status.catalog.rebuilding = false;
      DEMO_FIXTURES.status.catalog.built = Date.now() / 1000;
    }, REBUILD_MS);
    return json({ ok: true }, 202);
  };
  // A simulated save lands in the page memory: the page shows it, and a reload brings the
  // captured fixtures back. Each answer carries the text and the code of the server.
  const body_of = (init) => {
    try {
      const value = JSON.parse(init.body || "null");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  };
  const bad = (message, code) => json({ error: { message, type: "invalid_request_error", code } }, code);
  const word = (value) => String(value ?? "").trim();
  const noise = (size) => Math.random().toString(36).slice(2, 2 + size);
  const scald = (value) => {
    if (value === null || value === undefined) return "null";
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    const text = String(value);
    if (/^[A-Za-z0-9_./@+-]+$/.test(text)) return text;
    // A colon inside a word is plain YAML: `db:OPENROUTER_API_KEY`.
    return /^[A-Za-z0-9_./@+-]+:[^\\s]/.test(text) ? text : JSON.stringify(text);
  };
  const unscale = (raw) => {
    const text = raw.trim();
    if (!text || text === "null" || text === "~") return null;
    if (text === "true") return true;
    if (text === "false") return false;
    if (/^-?\\d+$/.test(text)) return Number(text);
    if (/^-?\\d*\\.\\d+$/.test(text)) return Number(text);
    const quoted = text.match(/^(["'])(.*)\\1$/);
    return quoted ? quoted[2] : text;
  };
  const DEFAULTS = DEMO_FIXTURES.settings.defaults || {};
  const SETTINGS_PATH = DEMO_FIXTURES.settings.path;
  const name_of = (path) => String(path).split("/").pop();
  const stem_of = (path) => name_of(path).replace(/\\.yml$/, "");
  const head_of = (path) => String(path).replace(/[^/]*$/, "");
  // The values of the YAML subset of the config files: maps, lists and scalars.
  const yaml_read = (text) => {
    const box = { node: {} };
    const stack = [{ indent: -1, node: box.node }];
    const lines = text.split("\\n");
    const next_body = (from, depth) => {
      for (let at = from; at < lines.length; at += 1) {
        const raw = lines[at];
        if (!raw.trim() || raw.trim().startsWith("#")) continue;
        const indent = raw.match(/^\\s*/)[0].length;
        if (indent <= depth) return "";
        return raw.trim();
      }
      return "";
    };
    for (let n = 0; n < lines.length; n += 1) {
      const raw = lines[n];
      if (!raw.trim() || raw.trim().startsWith("#")) continue;
      const indent = raw.match(/^\\s*/)[0];
      const column = indent.indexOf("\\t");
      if (column >= 0)
        return { error: `found character '\\t' that cannot start any token at line ${n + 1}, column ${column + 1}` };
      const depth = indent.length;
      const body = raw.trim().replace(/\\s+#.*$/, "");
      while (stack.length > 1 && depth <= stack[stack.length - 1].indent) stack.pop();
      const top = stack[stack.length - 1];
      if (body.startsWith("- ")) {
        // A top-level list is valid YAML. The caller says that a file needs blocks.
        if (stack.length === 1) {
          top.node = [];
          box.node = top.node;
        }
        if (!Array.isArray(top.node))
          return { error: `sequence entries are not allowed here at line ${n + 1}, column ${depth + 1}` };
        top.node.push(unscale(body.slice(2)));
        continue;
      }
      const at = body.indexOf(":");
      if (at < 0)
        return { error: `could not find expected ':' at line ${n + 1}, column ${depth + body.length}` };
      const value = body.slice(at + 1).trim();
      if (value.includes(": "))
        return { error: `mapping values are not allowed here at line ${n + 1}, column ${depth + at + 3}` };
      if (!top.node || typeof top.node !== "object" || Array.isArray(top.node))
        return { error: `mapping values are not allowed here at line ${n + 1}, column ${depth + 1}` };
      const key = body.slice(0, at).trim();
      if (!value) {
        const follows = next_body(n + 1, depth);
        const child = follows.startsWith("- ") ? [] : {};
        top.node[key] = child;
        stack.push({ indent: depth, node: child });
      } else {
        top.node[key] = unscale(value);
      }
    }
    return { value: box.node };
  };
  // `form_blocks` of the server: the block map of a file and its error line.
  const form_blocks = (row) => {
    const empty = !row.text.trim();
    const read = empty ? { value: {} } : yaml_read(row.text);
    if (read.error) {
      row.blocks = null;
      row.error = read.error;
      return;
    }
    if (!read.value || typeof read.value !== "object" || Array.isArray(read.value)) {
      row.blocks = null;
      row.error = `${name_of(row.path)} must hold provider blocks`;
      return;
    }
    row.blocks = row.main ? read.value : { [stem_of(row.path)]: read.value };
    row.error = null;
  };
  // `settings.parse` of the server: the error line of a settings text, or an empty string.
  const settings_error = (value) => {
    if (!value || typeof value !== "object" || Array.isArray(value))
      return `${SETTINGS_PATH} must hold groups of keys`;
    for (const [group, values] of Object.entries(value)) {
      if (!(group in DEFAULTS)) return `unknown group '${group}' in ${SETTINGS_PATH}`;
      if (!values || typeof values !== "object" || Array.isArray(values))
        return `${group} must hold keys`;
      for (const key of Object.keys(values)) {
        if (!(key in DEFAULTS[group])) return `unknown key ${group}.${key} in ${SETTINGS_PATH}`;
      }
    }
    const pools = { ...DEFAULTS.pools, ...(value.pools || {}) };
    if (new Set(Object.values(pools)).size !== Object.keys(pools).length)
      return "each pool in pools must have its own name";
    const loops = { ...DEFAULTS.loops, ...(value.loops || {}) };
    if (loops.shortest > loops.longest) return "loops.shortest must be at most loops.longest";
    return "";
  };
  // The provider form sends parsed blocks. The files tab reads the text of a file.
  const yaml_of = (value, pad = "") => {
    if (Array.isArray(value))
      return value
        .map((item) =>
          item && typeof item === "object"
            ? `${pad}-\\n${yaml_of(item, pad + "  ")}`
            : `${pad}- ${scald(item)}\\n`
        )
        .join("");
    if (value && typeof value === "object")
      return Object.entries(value)
        .map(([key, item]) =>
          item && typeof item === "object"
            ? `${pad}${key}:\\n${yaml_of(item, pad + "  ")}`
            : `${pad}${key}: ${scald(item)}\\n`
        )
        .join("");
    return `${pad}${scald(value)}\\n`;
  };
  // The lines of a file as a tree, so a merged value keeps the comments of its line.
  const line_tree = (text) => {
    const root = { line: -1, indent: -1, children: [] };
    const stack = [root];
    text.split("\\n").forEach((raw, n) => {
      if (!raw.trim() || raw.trim().startsWith("#")) return;
      const body = raw.trim();
      if (body.startsWith("- ") || !body.includes(":")) return;
      const indent = raw.match(/^\\s*/)[0].length;
      const node = { key: body.slice(0, body.indexOf(":")).trim(), line: n, indent, children: [] };
      while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop();
      stack[stack.length - 1].children.push(node);
      stack.push(node);
    });
    const ends = (node, last) => {
      node.children.forEach((child, at) => {
        ends(child, at + 1 < node.children.length ? node.children[at + 1].line : last);
      });
    };
    ends(root, text.split("\\n").length);
    return root;
  };
  const tail_of = (line) => (line.match(/\\s+#.*$/) || [""])[0];
  // The line after the block of a key: the next line at its indent or above, blanks aside.
  const node_end = (node, lines) => {
    let at = node.line + 1;
    while (at < lines.length) {
      const raw = lines[at];
      if (!raw.trim()) {
        at += 1;
        continue;
      }
      if (raw.match(/^\\s*/)[0].length <= node.indent) break;
      at += 1;
    }
    while (at > node.line + 1 && !lines[at - 1].trim()) at -= 1;
    return at;
  };
  const put_value = (lines, node, key, value) => {
    const pad = " ".repeat(node.indent + 2);
    const found = node.children.find((child) => child.key === key);
    const at = found ? node_end(found, lines) : node_end(node, lines);
    if (Array.isArray(value)) {
      const block = [`${pad}${key}:`].concat(value.map((item) => `${pad}  - ${scald(item)}`));
      return lines.slice(0, found ? found.line : at).concat(block, lines.slice(found ? at : at));
    }
    if (value && typeof value === "object") {
      if (found) return put_map(lines, found, value, pad + "  ");
      const block = [`${pad}${key}:`].concat(yaml_of(value, pad + "  ").split("\\n").filter(Boolean));
      return lines.slice(0, at).concat(block, lines.slice(at));
    }
    if (found && !found.children.length) {
      const text = `${" ".repeat(found.indent)}${key}: ${scald(value)}${tail_of(lines[found.line])}`;
      return lines.slice(0, found.line).concat([text], lines.slice(at));
    }
    if (found) return lines.slice(0, found.line).concat([`${pad}${key}: ${scald(value)}`], lines.slice(at));
    return lines.slice(0, at).concat([`${pad}${key}: ${scald(value)}`], lines.slice(at));
  };
  const put_map = (lines, node, document, pad) => {
    for (const [key, value] of Object.entries(document)) lines = put_value(lines, node, key, value);
    return lines;
  };
  const yaml_merge = (text, document) => {
    const lines = text.length ? text.split("\\n") : [];
    const tree = line_tree(text);
    return put_map(lines, tree, document, "").join("\\n");
  };
  const prune_groups = (lines) => {
    const kept = [];
    for (let at = 0; at < lines.length; at += 1) {
      const line = lines[at];
      const head = line.endsWith(":") && line[0] !== " " && line[0] !== "#";
      const next = lines.slice(at + 1).find((item) => item.trim().length > 0);
      if (head && (!next || next[0] !== " ")) continue;
      kept.push(line);
    }
    return kept;
  };
  const text_saved = (text, group, key, value) => {
    const lines = text.length ? text.split("\\n") : [];
    const tree = line_tree(text);
    let node = tree.children.find((child) => child.key === group);
    // The server appends a group that the file does not have yet.
    if (!node) {
      if (value === null || value === undefined) return text;
      lines.push(`${group}:`);
      node = { line: lines.length - 1, indent: 0, key: group, children: [] };
    }
    const found = node.children.find((child) => child.key === key);
    // A key without a default goes away, and the last key of a group takes the group.
    if (value === null || value === undefined) {
      if (!found) return text;
      const rest = lines.slice(node_end(found, lines));
      return prune_groups(lines.slice(0, found.line).concat(rest)).join("\\n");
    }
    return prune_groups(put_value(lines, node, key, value)).join("\\n");
  };
  const env_saved = async (init) => {
    const { name, value } = await body_of(init);
    const row = DEMO_FIXTURES.env.find((item) => item.name === name);
    if (!row) return bad("No provider file uses this name.", 400);
    const secret = word(value);
    if (!secret || /[\\s]/.test(secret))
      return bad("The value must be 1 word with no spaces.", 400);
    Object.assign(row, {
      state: "saved", has_saved: true, length: secret.length,
      start: secret.slice(0, 4), end: secret.length >= 12 ? secret.slice(-4) : null,
    });
    return json(DEMO_FIXTURES.env);
  };
  const env_cleared = async (init) => {
    const { name } = await body_of(init);
    const row = DEMO_FIXTURES.env.find((item) => item.name === name);
    if (!row || !row.has_saved) return bad("No saved value with this name.", 400);
    Object.assign(row, {
      state: row.has_env ? "env" : "missing", has_saved: false, length: 0, start: null, end: null,
    });
    return json(DEMO_FIXTURES.env);
  };
  // `keys.check_name` of the server: 1 to 40 characters, and a name that is free.
  const key_made = async (init) => {
    const raw = (await body_of(init)).name;
    const name = typeof raw === "string" ? raw.trim() : "";
    if (!name || name.length > 40) return bad("A key name has 1 to 40 characters.", 400);
    if (DEMO_FIXTURES.keys.some((row) => row.name === name))
      return bad(`The name '${name}' is in use.`, 400);
    const key = `sk-${noise(28)}`;
    DEMO_FIXTURES.keys.push({
      created: Date.now() / 1000, name, start: key.slice(0, 7), used: null,
    });
    return json({ name, key }, 201);
  };
  const key_dropped = (name) => {
    const at = DEMO_FIXTURES.keys.findIndex((row) => row.name === name);
    if (at < 0) return bad(`No key has the name '${name}'.`, 404);
    DEMO_FIXTURES.keys.splice(at, 1);
    return new Response(null, { status: 204 });
  };
  // The demo opens without a login. After a logout, `admin` and any password open it again.
  const log_in = async (init) => {
    const body = await body_of(init);
    const user = word(body.username);
    if (user !== DEMO_FIXTURES.login.username || !word(body.password))
      return bad("Wrong username or password.", 401);
    DEMO_FIXTURES.login.session = true;
    return json({ ok: true, session: "demo" });
  };
  const file_row = (path) => DEMO_FIXTURES.files.find((row) => row.path === path);
  const file_text = async (init) => {
    const { path, text } = await body_of(init);
    const row = file_row(path);
    if (!row) return bad("Unknown config file.", 400);
    if (typeof text !== "string") return bad("The file text must be a string.", 400);
    const read = yaml_read(text);
    if (read.error) return bad(read.error, 422);
    if (path === SETTINGS_PATH) {
      const error = settings_error(read.value);
      if (error) return bad(error, 422);
    } else if (!read.value || typeof read.value !== "object" || Array.isArray(read.value)) {
      return bad(`${path} must hold provider blocks`, 422);
    }
    row.text = text;
    form_blocks(row);
    return json({ ok: true, text });
  };
  const file_made = async (init) => {
    const name = word((await body_of(init)).name);
    if (!/^[a-z0-9-]+$/.test(name))
      return bad("The provider name must use lowercase letters, digits and dashes.", 400);
    const main = DEMO_FIXTURES.files.find((row) => row.main) || DEMO_FIXTURES.files[0];
    const path = head_of(main.path) + `${name}.yml`;
    if (file_row(path)) return bad(`${path} exists.`, 400);
    const text = `${name}:\\n  api_base: https://example.test/v1\\n`;
    const made = { path, text, blocks: null, error: null, main: false, shadow: null };
    form_blocks(made);
    DEMO_FIXTURES.files.push(made);
    return json({ path, text });
  };
  const file_dropped = async (init) => {
    const { path } = await body_of(init);
    const at = DEMO_FIXTURES.files.findIndex((row) => row.path === path && !row.main);
    if (at < 0) return bad("Only a {provider}.yml file can go.", 400);
    DEMO_FIXTURES.files.splice(at, 1);
    return json({ ok: true });
  };
  // `providers` PUT: the blocks of 1 file, merged into its text, with the comments kept.
  const providers_saved = async (init) => {
    const { path, blocks } = await body_of(init);
    const row = file_row(path);
    if (!row) return bad("Unknown config file.", 400);
    if (!blocks || typeof blocks !== "object" || Array.isArray(blocks))
      return bad("Each provider block must be a map.", 400);
    for (const value of Object.values(blocks)) {
      if (!value || typeof value !== "object" || Array.isArray(value))
        return bad("Each provider block must be a map.", 400);
    }
    const stem = stem_of(path);
    const document = row.main ? blocks : blocks[stem];
    if (!document || typeof document !== "object" || Array.isArray(document))
      return bad(`${path} needs the block ${stem}.`, 400);
    row.text = yaml_merge(row.text, document);
    form_blocks(row);
    return json({ ok: true, text: row.text });
  };
  const settings_saved = async (init) => {
    const { text, changes } = await body_of(init);
    if (typeof text === "string") {
      const read = yaml_read(text);
      if (read.error) return bad(read.error, 422);
      const error = settings_error(read.value);
      if (error) return bad(error, 422);
      DEMO_FIXTURES.settings.text = text;
      DEMO_FIXTURES.settings.file = read.value || {};
      return json({ ok: true, text });
    }
    if (!changes || typeof changes !== "object") return bad("The changes must be an object.", 400);
    for (const [group, values] of Object.entries(changes)) {
      if (!(group in DEFAULTS)) return bad(`unknown group '${group}'`, 422);
      if (!values || typeof values !== "object" || Array.isArray(values))
        return bad(`${group} must hold keys`, 422);
      for (const key of Object.keys(values)) {
        if (!(key in DEFAULTS[group])) return bad(`unknown key ${group}.${key}`, 422);
      }
    }
    for (const [group, values] of Object.entries(changes)) {
      const file = DEMO_FIXTURES.settings.file;
      file[group] ||= {};
      for (const [key, value] of Object.entries(values)) {
        const cleared = value === null || value === undefined;
        if (cleared) delete file[group][key];
        else file[group][key] = value;
        DEMO_FIXTURES.settings.text = text_saved(
          DEMO_FIXTURES.settings.text, group, key, cleared ? null : value,
        );
      }
    }
    const file = DEMO_FIXTURES.settings.file;
    for (const group of Object.keys(file)) {
      if (!Object.keys(file[group]).length) delete file[group];
    }
    return json({ ok: true, text: DEMO_FIXTURES.settings.text });
  };
  // The limits check of the server reads each provider again. The demo shifts the captured
  // clock to now and counts the requests that it served.
  const check_limits = () => {
    const now = Date.now() / 1000;
    const seen = DEMO_FIXTURES.limits;
    seen.checked = now;
    for (const lane of seen.lanes) {
      const waited = Math.max(0, (lane.rows[0]?.reset || now) - lane.at);
      lane.at = now;
      for (const item of lane.rows) {
        const used = USED.get(lane.model) || 0;
        if (item.kind === "requests") item.remaining = Math.max(0, item.remaining - used);
        if (item.kind === "tokens") item.remaining = Math.max(0, item.remaining - used * 40);
        item.reset = now + waited;
      }
    }
    return json(seen);
  };
  // A write with no home in the demo stays refused.
  const save = (path, method, init) => {
    if (path === "catalog" && method === "POST") return rebuild();
    if (path === "limits" && method === "POST") return check_limits();
    if (path === "reset" && method === "POST") {
      DEMO_FIXTURES.models.forEach((row) => Object.assign(row, { weight: 1, cooldown: null }));
      return json({ ok: true });
    }
    if (path === "login" && method === "POST") return log_in(init);
    if (path === "logout" && method === "POST") {
      DEMO_FIXTURES.login.session = false;
      return json({ ok: true });
    }
    if (path === "env")
      return method === "PUT" ? env_saved(init) : method === "DELETE" ? env_cleared(init) : null;
    if (path === "keys" && method === "POST") return key_made(init);
    if (path.startsWith("keys/") && method === "DELETE") return key_dropped(decodeURIComponent(path.slice(5)));
    if (path === "files")
      return method === "PUT"
        ? file_text(init)
        : method === "POST"
          ? file_made(init)
          : method === "DELETE"
            ? file_dropped(init)
            : null;
    if (path === "providers" && method === "PUT") return providers_saved(init);
    if (path === "settings" && method === "PUT") return settings_saved(init);
    return null;
  };
  const MARKER = "ui/api/";
  const real = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const at = url.indexOf(MARKER);
    if (at < 0) return real(input, init);
    const found = url.slice(at + MARKER.length).split(/[?#]/)[0];
    const path = found.endsWith("/") ? found.slice(0, -1) : found;
    const method = (init.method || "GET").toUpperCase();
    if (method !== "GET") {
      const job = save(path, method, init);
      if (job) return job;
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
    USED.set(row.model, (USED.get(row.model) || 0) + 1);
  };
  // The live stream of a server: the demo sends the same events, in waves of 1 to 3 requests,
  // each with its own timings and a random gap before the next wave.
  window.EventSource = class {
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      this.next = 0;
      // The server sends the requests in flight 1st, then each change.
      this.running = new Map();
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
      // The rows in flight, as the server keeps them for the next page that connects.
      if (kind === "start" || kind === "update" || kind === "first") this.running.set(data.id, data);
      if (kind === "end") this.running.delete(data.id);
      for (const call of this.listeners.get(kind) || []) call({ data: JSON.stringify(data) });
    }
    async loop() {
      // The page attaches its listeners 1st: the server answers on a later tick.
      await wait(0);
      this.send("live", [...this.running.values()]);
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
    // One captured row, with the fields of the server and the times of the page.
    async replay(row) {
      const id = ++this.next;
      const served = row.via || row.model;
      began(id);
      const live = {
        id, path: MEDIA_PATH[row.model] || "/v1/chat/completions", ...ages(id), ttft: null,
      };
      this.send("start", live);
      await wait(gap(60, 200));
      // The attempt starts when the model is known, as `Live.update` restarts its clock.
      attempted(id);
      Object.assign(live, {
        app: row.app, session: row.session, key: row.key, model: row.model, effort: row.effort,
        pool: row.pool, stream: row.stream, trying: served, via: null, attempts: [], tokens: null,
        fallbacks: "0", ...ages(id),
      });
      this.send("update", live);
      await wait(round(gap(0.4, 2.4)) * 1000);
      // The first token: TTFT freezes at the wait, and the stream clock counts from 0.
      const frozen = ages(id);
      Object.assign(live, { trying: null, via: served, ttft: frozen.attempt_age, ...frozen });
      this.send("first", live);
      const streamed = round(gap(0.2, 1.4));
      await wait(streamed * 1000);
      const done = {
        ...row, at: Date.now() / 1000, seconds: round(frozen.attempt_age + streamed),
        ttft: `${frozen.attempt_age.toFixed(3)}s`,
      };
      this.send("end", { id, row: done });
      CLOCKS.delete(id);
      keep(done);
    }
    async one(id) {
      // A captured row is the template, so each field keeps the shape and the type of the server.
      const template = pick(SEEDED);
      const served = template.via;
      const path = MEDIA_PATH[template.model] || "/v1/chat/completions";
      began(id);
      const row = { id, path, ...ages(id), ttft: null };
      this.send("start", row);
      await wait(gap(60, 200));
      // The attempt starts when the model is known, as `Live.update` restarts its clock.
      attempted(id);
      const stream = path === "/v1/chat/completions" ? true : template.stream === true;
      Object.assign(row, {
        app: pick(["OWUI", "Kilo"]), session: session(id), key: "master",
        model: template.model, effort: template.effort, pool: template.pool, stream,
        trying: served, via: null, attempts: [], tokens: null, fallbacks: "0", ...ages(id),
      });
      this.send("update", row);
      await wait(round(gap(0.4, 2.4)) * 1000);
      // The first token: TTFT freezes at the wait, and the stream clock counts from 0.
      const frozen = ages(id);
      Object.assign(row, { trying: null, via: served, ttft: frozen.attempt_age, ...frozen });
      this.send("first", row);
      const streamed = round(gap(0.2, 1.4));
      await wait(streamed * 1000);
      // The finished row carries the fields of `dashboard.record`: the status code, the text
      // times, and the answer.
      const done = {
        at: Date.now() / 1000, status: 200, seconds: round(frozen.attempt_age + streamed),
        app: row.app, session: row.session, key: row.key, model: row.model, effort: row.effort,
        pool: row.pool, routed: template.routed || null, transition: template.transition || null,
        retry: template.retry || null, loop: template.loop || null,
        via: served, ttft: `${frozen.attempt_age.toFixed(3)}s`, stream: row.stream,
        status: template.status || 200, fallbacks: template.fallbacks || "0",
        tokens: template.tokens ? { ...template.tokens } : null,
        attempts: (template.attempts || []).length
          ? template.attempts.map((item) => ({ ...item }))
          : [{ model: served, result: "answered", seconds: frozen.attempt_age, error: "" }],
      };
      this.send("end", { id, row: done });
      CLOCKS.delete(id);
      keep(done);
    }
  };
})();
"""


def demo_version() -> str:
  """The version of the build: `DEMO_VERSION`, else `demo.<commit count>`, else `demo`."""
  if version := os.environ.get("DEMO_VERSION"):
    return version
  try:
    count = subprocess.run(
      ["git", "rev-list", "--count", "HEAD"],
      capture_output=True,
      text=True,
      check=True,
      cwd=Path(__file__).resolve().parent,
    ).stdout.strip()
  except (OSError, subprocess.SubprocessError):
    count = ""
  return f"demo.{count}" if count.isdigit() else "demo"


def build(out: Path, version: str) -> Path:
  """Copy the UI into `out`, add the demo script, and load it before the page."""
  fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
  fixtures["login"]["version"] = version
  fixtures["status"]["version"] = version
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
  parser.add_argument(
    "--version", help="the version in the demo header, else the build one"
  )
  arguments = parser.parse_args(argv)
  if arguments.capture:
    FIXTURES.write_text(
      json.dumps(capture(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {FIXTURES}")
  version = arguments.version or demo_version()
  out = build(Path(arguments.out), version)
  print(f"built {out} version {version}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
