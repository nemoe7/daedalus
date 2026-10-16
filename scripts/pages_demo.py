"""Build the static dashboard demo and capture its fixtures."""

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
from daedalus.config import defaults, settings
from daedalus.providers import hooks
from daedalus.routing import router
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
  "notifications",
  "hooks",
  "keys",
  "files",
  "env",
  "provider-keys",
  "provider-defaults",
  "settings",
  "login",
)
# The provider files of the demo. Its local main stays empty, and the shipped main supplies the
# cached cloud blocks that the Providers form shows.
DEMO_FILES = ("free.yml", "openrouter.yml", "pollinations.yml")
# The models of the demo: a captured snapshot of the live catalog, 126 rows.
MODELS_FILE = ROOT / "scripts" / "pages_models.json"
TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {}}}
# The models that the demo makes fail or rate limit, for 1 row each.
DOWN = set()
LIMITED = set()
# The source of the demo: the repo that ships its hook files. The Sources card starts with it.
DEMO_SOURCE = {
  "repo": "nemoe7/daedalus",
  "path": "hooks",
  "ref": "main",
  "auto_update": False,
}
# The request-level surfaces of the demo file, in the order of the settings defaults.
DEMO_HOOK_SURFACES = ("on-request", "on-prompt", "on-chunk")
# The prompts of the captured rows, in the order that the table shows them.
DEMO_CALLS = (
  ("daedalus/moros", "Count to three."),
  ("daedalus/deinos", "Write a haiku about a slow deployment."),
  ("daedalus/koinos", "Name 5 fruit trees."),
  ("daedalus/sophos", "What is the weather in Manila?"),
  (
    "cloudflare/@cf/mistralai/mistral-small-3.1-24b-instruct",
    "Summarize this log line: upstream timeout after 30 s.",
  ),
  ("openrouter/z-ai/glm-5.3-flash", "Say hello in Japanese."),
  ("daedalus/koinos", "Draft a short release note for version 0.3."),
  # The auto pool: the row carries the pool that the route selected.
  ("daedalus/auto", "Sort these 3 words by length."),
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
  ("CLOUDFLARE_ACCOUNT_ID", "00000000000000000000000000000000"),
  ("OPENROUTER_API_KEY", "not-a-real-openrouter-key-0123"),
  ("GEMINI_API_KEY", "not-a-real-gemini-key-0123"),
  ("GROQ_API_KEY", "not-a-real-groq-key-0123"),
  ("KILO_API_KEY", "not-a-real-kilo-key-0123"),
  ("MISTRAL_API_KEY", "not-a-real-mistral-key-0123"),
  ("ZAI_API_KEY", "not-a-real-z-ai-key-0123"),
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
  "x-ratelimit-limit-tokens-hour": "Infinity",
  "x-ratelimit-remaining-tokens-hour": "NaN",
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


def demo_hook_entries() -> dict[str, list[str]]:
  """The request surfaces of the demo files, from their frontmatter."""
  found: dict[str, list[str]] = {}
  for surface in DEMO_HOOK_SURFACES:
    names = [
      f"hooks/{path.name}"
      for path in sorted((ROOT / "hooks").glob("*.py"))
      if (info := hooks.meta(path)) and surface in (info.get("surfaces") or [])
    ]
    if names:
      found[surface] = names
  return found


def demo_hook_order() -> dict[str, list[str]]:
  """One visible metadata-only order for the Hooks card of the demo."""
  names = demo_hook_entries().get("on-chunk", [])
  return {"on-chunk": list(reversed(names))} if names else {}


def demo_settings_text() -> str:
  """The repo settings file with the demo source and metadata-only hook order."""
  text = settings.DEFAULT_PATH.read_text(encoding="utf-8")
  return settings.update_text(
    text,
    {
      "hooks": {
        **{surface: None for surface in DEMO_HOOK_SURFACES},
        "order": demo_hook_order(),
        "sources": [dict(DEMO_SOURCE)],
      }
    },
  )


def demo_files(folder: Path) -> tuple[Path, ...]:
  """Make an empty local main beside the shipped single-provider files of the Providers tab."""
  target = folder / "config" / "providers"
  target.mkdir(parents=True)
  shipped = ROOT / "config" / "providers"
  files = tuple(shipped / name for name in DEMO_FILES)
  for file in files:
    if file.name == "free.yml":
      (target / file.name).write_text("", encoding="utf-8")
    else:
      shutil.copyfile(file, target / file.name)
  settings_file = folder / "config" / "daedalus.yml"
  settings_file.write_text(demo_settings_text())
  # The shipped hooks of the root folder: the legend rows of the page come from them.
  hooks_dir = ROOT / "hooks"
  if hooks_dir.is_dir():
    shutil.copytree(hooks_dir, folder / "hooks", dirs_exist_ok=True)
  return tuple(target / name for name in DEMO_FILES)


def seed_saved() -> None:
  """Save the demo values before the config load, which resolves `db:` keys."""
  for name, value in DEMO_VALUES:
    saved_env.save(store.MODELS_DB, name, value)
  for name in DEMO_KEYS:
    keys.add(store.MODELS_DB, name)


# Add synthetic request fields that the mock provider does not emit.
DEMO_SHAPES = (
  {
    "app": "OWUI",
    "effort": "high",
    "via": "cloudflare/@cf/zai-org/glm-4.7-flash",
    "attempts": [
      {
        "model": "cloudflare/@cf/zai-org/glm-4.7-flash",
        "result": "answered",
        "seconds": 0.001,
        "error": "",
      },
    ],
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
    "via": "cloudflare/@cf/zai-org/glm-4.7-flash",
    "retry": "1",
    "fallbacks": "1",
    "seconds": 1.3,
    "ttft": "0.884s",
    "status": 200,
    "transition": {
      "from_model": "cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",
      "from_pool": "koinos",
      "reason": "err",
      "to_model": "cloudflare/@cf/zai-org/glm-4.7-flash",
      "to_pool": "koinos",
    },
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
  {
    "app": "OWUI",
    "model": "daedalus/deinos",
    "pool": "deinos",
    "via": "kilo/nvidia/nemotron-3.5-lightning:free",
    "race": "drawn",
    "seconds": 2.824,
    "ttft": "0.297s",
    "stream": True,
    "routed": None,
    "retry": None,
    "loop": None,
    "fallbacks": "0",
    "status": 200,
    "transition": {
      "from_model": "z-ai/glm-4.5-flash",
      "from_pool": "deinos",
      "reason": "rce",
      "to_model": "kilo/nvidia/nemotron-3.5-lightning:free",
      "to_pool": "deinos",
    },
    "attempts": [
      {
        "model": "z-ai/glm-4.5-flash",
        "result": "error",
        "seconds": 0.08,
        "error": "HTTP 500: upstream failed",
        "weight_change": {"from": 1.0, "to": 0.5},
      },
      {
        "model": "kilo/nvidia/nemotron-3.5-lightning:free",
        "result": "answered",
        "seconds": 0.297,
        "error": "",
        "race": "won",
      },
    ],
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
  # The shapes carry chat-only fields, so they start from the newest chat row: a media
  # row can never hold the app, the effort, the loop, the retry or the routed pool.
  rows = [row for row in dashboard.HISTORY.latest(50) if row["model"] in router.POOLS]
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
      DOWN.add("@cf/meta/llama-3.1-8b-instruct-fp8")
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
    files = demo_files(folder)
    provider = files[0]
    saved = (
      store.MODELS_DB,
      discovery.DUMP_DIR,
      hooks.ROOT,
      dashboard.FILES,
      config.DEFAULT_PATH,
      settings.DEFAULT_PATH,
      defaults.PATH,
    )
    environ = dict(os.environ)
    try:
      store.MODELS_DB = folder / "state" / "models.sqlite3"
      discovery.DUMP_DIR = folder / "dump"
      hooks.ROOT = folder
      dashboard.FILES = files
      config.DEFAULT_PATH = provider
      settings.DEFAULT_PATH = folder / "config" / "daedalus.yml"
      defaults.PATH = folder / "state" / "free.defaults.yml"
      defaults.PATH.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(ROOT / "config" / "providers" / "free.yml", defaults.PATH)
      os.environ[dashboard.DAEDALUS_MASTER_KEY] = DEMO_MASTER
      store.migrate()
      store.write_store(json.loads(MODELS_FILE.read_text(encoding="utf-8")))
      seed_saved()
      # The shipped files read their keys from the environment, which the capture fakes.
      os.environ.update(dict(DEMO_VALUES))
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
      client.post(
        "/ui/api/limits", headers={**auth, "Content-Type": "application/json"}
      ).raise_for_status()
      found = {
        name.split("?")[0]: client.get(f"/ui/api/{name}").json() for name in ENDPOINTS
      }
    finally:
      store.MODELS_DB, discovery.DUMP_DIR, hooks.ROOT, dashboard.FILES = saved[:4]
      config.DEFAULT_PATH, settings.DEFAULT_PATH, defaults.PATH = saved[4:]
      os.environ.clear()
      os.environ.update(environ)
      upstream.set_client(None)
      config.set_config(None)
  # The temp folder of the capture never reaches the fixtures.
  return json.loads(json.dumps(found).replace(str(folder) + os.sep, ""))


DEMO_JS = """\
"use strict";
// Serve API data from page memory and fill the initially empty request list.
const DEMO_FIXTURES = __FIXTURES__;
(() => {
  // Demo changes stay in page memory, so suppress the reload warning.
  const listen = window.addEventListener.bind(window);
  window.addEventListener = (kind, ...rest) =>
    kind === "beforeunload" ? undefined : listen(kind, ...rest);
  // Keep 500 request rows, as the server does, and discard older rows.
  const KEPT = 500;
  // Count requests by model for the limits check.
  const USED = new Map();
  // Tests reduce waits with `window.DEMO_SCALE`. The shipped page uses 1.
  // Keep each reduced wait at or above timer resolution.
  const wait = (ms) => new Promise((done) => setTimeout(done, Math.max(1, ms * (window.DEMO_SCALE || 1))));
  // Use ?slow=800 to delay fixture responses by 800 ms.
  const SLOW = (() => {
    const found = /[?&]slow=([0-9]+)/.exec(location.search || "");
    return found ? Number(found[1]) : 0;
  })();
  const round = (value) => Math.round(value * 1000) / 1000;
  const EMBED = "mistral/mistral-embed-2312";
  // Map each pool to the model IDs that the router can select.
  const MEMBERS = Object.fromEntries(
    DEMO_FIXTURES.pools.map((pool) => [pool.name, pool.members.map((item) => item.id)])
  );
  const TEXT = MEMBERS["daedalus/auto"] || [];
  // Map each model to its tier pool for live rows.
  const POOL_OF = {};
  for (const pool of DEMO_FIXTURES.pools) {
    if (pool.name === "daedalus/auto") continue;
    for (const item of pool.members) POOL_OF[item.id] = pool.name.replace("daedalus/", "");
  }
  const POOL_NAMES = ["sophos", "deinos", "koinos", "moros"];
  // Increase auto reasoning effort once per agent step.
  const LADDER = ["none", "low", "medium", "high"];
  // Cycle retry rows through these legend transition codes.
  const REASONS = ["err", "ctx", "lmt", "hlt", "rnd", "cls", "esc", "rce", "rt1"];
  let reasonN = 0;
  // Show the first 7 key-hash characters as the session, as the server does.
  const session = (n) => ((n * 2654435761) % 0xfffffff).toString(16).padStart(7, "0");
  const gap = (min, max) => min + Math.random() * (max - min);
  // Use DEMO_SCENARIOS to script waves without changing the timing ranges.
  const WAVE_MS = [6000, 18000];
  const ttftFor = (kind) => {
    if (kind === "embed") return gap(0.1, 0.5);
    if (kind === "title") return gap(0.4, 1.5);
    const r = Math.random();
    return r < 0.7 ? gap(20, 30) : r < 0.9 ? gap(0, 20) : gap(30, 60);
  };
  const streamFor = (streaming) => {
    if (!streaming) return gap(0.2, 1.5);
    const r = Math.random();
    return r < 0.6 ? gap(15, 45) : r < 0.85 ? gap(0.5, 15) : gap(45, 120);
  };
  const select = (rows) => rows[Math.floor(Math.random() * rows.length)];
  // Read the clock once and clamp elapsed time to whole milliseconds above zero.
  const CLOCKS = new Map();
  const began = (id) => {
    const now = performance.now();
    CLOCKS.set(id, { at: now, attempt: now });
  };
  const attempted = (id) => (CLOCKS.get(id).attempt = performance.now());
  const ages = (id) => {
    const clock = CLOCKS.get(id);
    const now = performance.now();
    return {
      age: Math.max(1, Math.round(now - clock.at)) / 1000,
      attempt_age: Math.max(1, Math.round(now - clock.attempt)) / 1000,
    };
  };
  // Start Requests empty. Use captured rows as templates to keep server fallback chains, rate limits, and codes.
  const SEEDED = DEMO_FIXTURES.requests.map((row) => ({ ...row }));
  DEMO_FIXTURES.requests = [];
  const TEMPLATES = SEEDED.filter((row) => row.transition);
  // Shift the catalog clock once: the previous build was 10 minutes ago, and the next is in 2 hours.
  const opened = Date.now() / 1000;
  DEMO_FIXTURES.status.catalog.built = opened - 600;
  DEMO_FIXTURES.status.catalog.next = opened + 7200;
  const json = (body, status = 200) =>
    (SLOW ? wait(SLOW) : Promise.resolve()).then(() => new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    }));
  const slice = (url) => {
    const limit = +(new URL(url, location.origin).searchParams.get("limit") || 0);
    return limit > 0 ? DEMO_FIXTURES.requests.slice(0, limit) : DEMO_FIXTURES.requests;
  };
  // The simulated rebuild waits 26 seconds and reads no provider.
  const REBUILD_MS = 26000;
  const rebuild = () => {
    if (DEMO_FIXTURES.status.catalog.rebuilding) {
      const error = { message: "A catalog rebuild runs now.", type: "invalid_request_error", code: 409 };
      return json({ error }, 409);
    }
    DEMO_FIXTURES.status.catalog.rebuilding = true;
    const before = new Map(state.models.map((row) => [row.id, JSON.stringify(row)]));
    setTimeout(() => {
      DEMO_FIXTURES.status.catalog.rebuilding = false;
      DEMO_FIXTURES.status.catalog.built = Date.now() / 1000;
      state.models = catalog();
      // Report added, removed, or changed rows in the Notifications rebuild event.
      const after = new Map(state.models.map((row) => [row.id, JSON.stringify(row)]));
      DEMO_FIXTURES.notifications.rebuilds.unshift({
        at: Date.now() / 1000,
        reason: "manual",
        models: state.models.length,
        added: [...after.keys()].filter((id) => !before.has(id)),
        removed: [...before.keys()].filter((id) => !after.has(id)),
        changed: [...after.keys()].filter((id) => before.has(id) && before.get(id) !== after.get(id)),
        failed: [],
      });
      DEMO_FIXTURES.notifications.rebuilds.length = Math.min(DEMO_FIXTURES.notifications.rebuilds.length, 50);
    }, REBUILD_MS);
    return json({ ok: true }, 202);
  };
  // Save changes in page memory. Reload restores captured fixtures.
  // Keep server response text and codes.
  const body_of = (init) => {
    try {
      const value = JSON.parse(init.body || "null");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  };
  const bad = (message, code, locale = {}) =>
    json({ error: { message, type: "invalid_request_error", code, ...locale } }, code);
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
  // Read one-level flow lists of scalar values, such as effort ladders.
  const flow_list = (text) => {
    const inner = text.slice(1, -1).trim();
    if (!inner) return [];
    const out = [];
    for (const part of inner.split(",")) out.push(unscale(part));
    return out;
  };
  const flow_value = (raw) => {
    const value = raw.trim();
    return value.startsWith("[") ? flow_list(value) : unscale(value);
  };
  // Read one-level flow maps with scalar or flow-list values.
  // Keep list commas together to preserve effort ladders inside maps.
  const flow_map = (text, where) => {
    const out = {};
    const inner = text.slice(1, -1).trim();
    if (!inner) return out;
    const parts = [];
    let depth = 0;
    let start = 0;
    for (let i = 0; i < inner.length; i += 1) {
      const char = inner[i];
      if (char === "[" || char === "{") depth += 1;
      else if (char === "]" || char === "}") depth -= 1;
      else if (char === "," && depth === 0) {
        parts.push(inner.slice(start, i));
        start = i + 1;
      }
    }
    parts.push(inner.slice(start));
    for (const part of parts) {
      const at = part.indexOf(":");
      if (at < 0) throw new Error(`expected ':' in the flow mapping at ${where}`);
      out[unscale(part.slice(0, at))] = flow_value(part.slice(at + 1));
    }
    return out;
  };
  const DEFAULTS = DEMO_FIXTURES.settings.defaults || {};
  const SETTINGS_PATH = DEMO_FIXTURES.settings.path;
  const name_of = (path) => String(path).split("/").pop();
  const stem_of = (path) => name_of(path).replace(/\\.yml$/, "");
  // Start with an empty local main file. These effective blocks hold its cached cloud copy.
  const CLOUD_MAIN = JSON.parse(JSON.stringify(
    DEMO_FIXTURES.files.find((file) => file.main)?.blocks || {},
  ));
  // Read the config YAML subset: maps, lists, and scalars.
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
      let depth = indent.length;
      let body = raw.trim().replace(/\\s+#.*$/, "");
      while (stack.length > 1 && depth <= stack[stack.length - 1].indent) stack.pop();
      let top = stack[stack.length - 1];
      if (body === "-" || body.startsWith("- ")) {
        // Accept top-level YAML lists. Callers require blocks where necessary.
        if (stack.length === 1 && !Array.isArray(top.node)) {
          top.node = [];
          box.node = top.node;
        }
        if (!Array.isArray(top.node))
          return { error: `sequence entries are not allowed here at line ${n + 1}, column ${depth + 1}` };
        const item = body.slice(1).trim();
        if (!item) {
          const follows = next_body(n + 1, depth);
          const child = follows === "-" || follows.startsWith("- ") ? [] : {};
          top.node.push(child);
          stack.push({ indent: depth, node: child });
          continue;
        }
        if (/^["']/.test(item) || !/:(?:\\s|$)/.test(item)) {
          top.node.push(unscale(item));
          continue;
        }
        const child = {};
        top.node.push(child);
        top = { indent: depth, node: child };
        stack.push(top);
        body = item;
        depth += 2;
      }
      const at = body.indexOf(":");
      if (at < 0)
        return { error: `could not find expected ':' at line ${n + 1}, column ${depth + body.length}` };
      const value = body.slice(at + 1).trim();
      // Read flow maps before the guard because their braces contain `: ` pairs.
      if (!value.startsWith("{") && value.includes(": "))
        return { error: `mapping values are not allowed here at line ${n + 1}, column ${depth + at + 3}` };
      if (!top.node || typeof top.node !== "object" || Array.isArray(top.node))
        return { error: `mapping values are not allowed here at line ${n + 1}, column ${depth + 1}` };
      // Remove quotes from parsed keys, as the server does.
      const key = body.slice(0, at).trim().replace(/^(["'])(.*)\\1$/, "$2");
      if (!value) {
        const follows = next_body(n + 1, depth);
        const child = follows === "-" || follows.startsWith("- ") ? [] : {};
        top.node[key] = child;
        stack.push({ indent: depth, node: child });
      } else if (value.startsWith("{") && value.endsWith("}")) {
        try {
          top.node[key] = flow_map(value, `line ${n + 1}`);
        } catch (problem) {
          return { error: String(problem.message) };
        }
      } else if (value.startsWith("[") && value.endsWith("]")) {
        top.node[key] = flow_list(value);
      } else {
        top.node[key] = unscale(value);
      }
    }
    return { value: box.node };
  };
  const hook_name = (value) => {
    if (typeof value !== "string" || value.length > 512 || /[^A-Za-z0-9._/-]/.test(value)) return false;
    const parts = value.split("/");
    if (parts.length === 1) return value.length <= 80 && /^[A-Za-z0-9]/.test(value);
    return parts.every(part => part.length > 0 && part.length <= 100 && part !== "." && part !== "..");
  };
  const hook_names_error = (value) => {
    if (value === null || value === undefined || value === "") return "";
    return Array.isArray(value) && value.every(hook_name) ? "" : "hooks.disabled must be a list of hook names";
  };
  const limits_error = (values) => {
    const limits = { ...DEFAULTS.limits, ...values };
    return limits.shortest > limits.longest ? "limits.shortest must be at most limits.longest" : "";
  };
  // Match `settings.parse`: return the settings error text or an empty string.
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
      const error = group === "hooks" ? hook_names_error(values.disabled) : "";
      if (error) return error;
    }
    const pools = { ...DEFAULTS.personalization, ...(value.personalization || {}) };
    if (new Set(Object.values(pools)).size !== Object.keys(pools).length)
      return "each pool in personalization must have its own name";
    return limits_error(value.limits);
  };
  // Provider forms send parsed blocks. The files tab reads text.
  // Plain keys start with a letter, digit, or _ and slugs such as @cf/... need quotes.
  const key_of = (key) => (/^[A-Za-z0-9_][A-Za-z0-9_./@+-]*$/.test(String(key)) ? key : JSON.stringify(String(key)));
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
            ? `${pad}${key_of(key)}:\\n${yaml_of(item, pad + "  ")}`
            : `${pad}${key_of(key)}: ${scald(item)}\\n`
        )
        .join("");
    return `${pad}${scald(value)}\\n`;
  };
  // Build a line tree to preserve line comments when values merge.
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
    return root;
  };
  const tail_of = (line) => (line.match(/\\s+#.*$/) || [""])[0];
  // Find the next line at this indent or above, excluding blank lines.
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
      return lines.slice(0, found ? found.line : at).concat(block, lines.slice(at));
    }
    if (value && typeof value === "object") {
      if (found) return put_map(lines, found, value);
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
  const put_map = (lines, node, document) => {
    for (const [key, value] of Object.entries(document)) lines = put_value(lines, node, key, value);
    return lines;
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
    // Append a missing group, as the server does.
    if (!node) {
      if (value === null || value === undefined) return text;
      lines.push(`${group}:`);
      node = { line: lines.length - 1, indent: 0, key: group, children: [] };
    }
    const found = node.children.find((child) => child.key === key);
    // Remove keys without defaults. Remove a group when its last key goes.
    if (value === null || value === undefined) {
      if (!found) return text;
      const rest = lines.slice(node_end(found, lines));
      return prune_groups(lines.slice(0, found.line).concat(rest)).join("\\n");
    }
    return prune_groups(put_value(lines, node, key, value)).join("\\n");
  };
  const saved_fields = secret => ({
    state: "saved", has_saved: true, length: secret.length,
    start: secret.slice(0, 4), end: secret.length >= 12 ? secret.slice(-4) : null,
  });
  const env_saved = async (init) => {
    const { name, value } = await body_of(init);
    const row = DEMO_FIXTURES.env.find((item) => item.name === name);
    if (!row) return bad("No provider file uses this name.", 400);
    const secret = word(value);
    if (!secret || /[\\s]/.test(secret))
      return bad("The value must be 1 word with no spaces.", 400);
    Object.assign(row, saved_fields(secret));
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
  // Match `keys.check_name`: require a free name of 1 to 40 characters.
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
  // Open the demo without login. After logout, accept `admin` and any password.
  const log_in = async (init) => {
    const body = await body_of(init);
    const user = word(body.username);
    if (user !== DEMO_FIXTURES.login.username || !word(body.password))
      return bad("Wrong username or password.", 401);
    DEMO_FIXTURES.login.session = true;
    return json({ ok: true, session: "demo" });
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
      const error = group === "hooks" ? hook_names_error(values.disabled) : "";
      if (error) return bad(error, 422);
    }
    const limits = { ...DEMO_FIXTURES.settings.file.limits };
    for (const [key, value] of Object.entries(changes.limits || {})) {
      if (value === null || value === undefined) delete limits[key];
      else limits[key] = value;
    }
    const error = limits_error(limits);
    if (error) return bad(error, 422);
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
  // Simulate provider plans, caps, and balances with captured values from real plans.
  const PLAN = {
    cloudflare: { requests: 14400, tokens: 4000000, neurons: 10000 },
    kilo: { requests: 200, tokens: 1000000 },
    openrouter: { requests: 1000, tokens: 4000000, free: 1000 },
    pollinations: { requests: 600, tokens: 1000000 },
  };
  const plan_of = (provider, block) => {
    const plan = { ...(PLAN[provider] || { requests: 1000, tokens: 1000000 }) };
    if (block) {
      // Let blocks override caps, such as kilo hourly_requests.
      if (block.hourly_requests) plan.requests = block.hourly_requests;
      if (block.models && typeof block.models === "object") plan.token_cap = null;
    }
    return plan;
  };
  // Rebuild catalog rows from provider blocks and captured metadata.
  const MODEL_KEYS = Object.keys(DEMO_FIXTURES.models[0]);
  const weights = new Map();
  const cooldowns = new Map();
  // Merge shadow files into their named blocks, key by key, as config does.
  const merge = (into = {}, from = {}) => {
    const out = { ...into };
    for (const [key, value] of Object.entries(from)) {
      const both = value && typeof value === "object" && !Array.isArray(value)
        && into[key] && typeof into[key] === "object" && !Array.isArray(into[key]);
      out[key] = both ? merge(into[key], value) : value;
    }
    return out;
  };
  const blocks = () => {
    const out = new Map();
    for (const file of DEMO_FIXTURES.files) {
      if (!String(file.path).startsWith("config/providers/")) continue;
      const names = file.main ? Object.keys(file.blocks || {}) : [stem_of(file.path)];
      for (const name of names) {
        const block = (file.blocks || {})[name];
        if (!block || typeof block !== "object" || Array.isArray(block)) continue;
        out.set(name, merge(out.get(name), block));
      }
    }
    return out;
  };
  const catalog = () => {
    const known = new Map(DEMO_FIXTURES.models.map((row) => [row.id, row]));
    // Match exact names, globs, regexes, and negations, as the server does.
    const BACKSLASH = String.fromCharCode(92);
    const esc = (text) => String(text).split("").map((char) =>
      (".+^$()|[]{}".includes(char) || char === BACKSLASH ? BACKSLASH + char : char)).join("");
    const glob = (pattern) => new RegExp("^" + pattern.split("*").map((part) =>
      part.split("?").map(esc).join(".")).join(".*") + "$");
    const hit = (pattern, slug) => {
      if (pattern.startsWith("!")) return !hit(pattern.slice(1), slug);
      if (pattern.startsWith("^")) return new RegExp(pattern).test(slug);
      if (pattern.includes("*") || pattern.includes("?")) return glob(pattern).test(slug);
      return pattern === slug;
    };
    const spec = (pattern) => {
      if (pattern.startsWith("!")) return [0, 0];
      if (pattern.startsWith("^")) return [1, pattern.length];
      if (pattern.includes("*") || pattern.includes("?"))
        return [2, pattern.replace(/[*?]/g, "").length];
      return [3, pattern.length];
    };
    // Return the value of the most specific matching pattern, as config does.
    const take = (map, slug) => {
      let best = null, bestSpec = null;
      for (const [pattern, value] of Object.entries(map)) {
        if (!hit(pattern, slug)) continue;
        const rank = spec(pattern);
        if (!bestSpec || rank[0] > bestSpec[0] || (rank[0] === bestSpec[0] && rank[1] > bestSpec[1])) {
          best = value;
          bestSpec = rank;
        }
      }
      return best;
    };
    const rows = [];
    for (const [name, block] of blocks()) {
      const declared = Object.keys(block.models || {});
      const tiers = {};
      for (const [tier, ids] of Object.entries(block.tier || {}))
        if (Array.isArray(ids)) for (const id of ids) tiers[String(id)] = tier;
      // Include every captured catalog row and any exact slug declared in the models block.
      const slugs = new Set();
      for (const id of known.keys())
        if (id.startsWith(`${name}/`)) slugs.add(id.slice(name.length + 1));
      for (const pattern of declared)
        if (spec(pattern)[0] === 3) slugs.add(pattern);
      for (const slug of slugs) {
        const id = `${name}/${slug}`;
        const meta = take(block.models || {}, slug) || {};
        const cap = known.get(id);
        const row = {};
        for (const key of MODEL_KEYS)
          row[key] = cap ? cap[key] : DEMO_FIXTURES.models[0][key];
        row.id = id;
        row.mode = meta.mode || (cap ? cap.mode : "chat");
        row.tier = row.mode === "chat" ? take(tiers, slug) || (cap ? cap.tier : null) : null;
        row.max_input_tokens = meta.max_input_tokens ?? (cap ? cap.max_input_tokens : null);
        row.max_output_tokens = meta.max_output_tokens ?? (cap ? cap.max_output_tokens : null);
        row.tools = meta.tools ?? (cap ? cap.tools : false);
        row.reasoning = meta.supports_reasoning ?? (cap ? cap.reasoning : false);
        row.effort = meta.reasoning_effort ?? (cap ? cap.effort : null);
        row.flags = cap ? cap.flags : meta.supports_vision ? ["vision"] : [];
        row.weight = weights.get(id) ?? (cap ? cap.weight : 1);
        row.cooldown = cooldowns.get(id) ?? null;
        row.client_cooldowns = cap ? cap.client_cooldowns : {};
        rows.push(row);
      }
    }
    return rows;
  };
  const state = { models: catalog() };
  const model_of = (id) => state.models.find((row) => row.id === id);
  // Apply finished request weights and cooldowns to each attempt, as the server does.
  const apply = (row) => {
    for (const attempt of row.attempts || []) {
      const target = model_of(attempt.model);
      if (!target) continue;
      if (attempt.cooldown) {
        const until = Date.now() / 1000 + (attempt.cooldown.seconds || 0);
        cooldowns.set(target.id, until);
        target.cooldown = until;
      }
      if (attempt.weight_change) {
        weights.set(target.id, attempt.weight_change.to);
        target.weight = attempt.weight_change.to;
      }
    }
  };
  // Settings name each tier pool. The auto pool includes all chat models.
  const pools = () => {
    const file = DEMO_FIXTURES.settings.file.personalization || {};
    const names = { ...DEMO_FIXTURES.settings.defaults.personalization, ...file };
    const out = [];
    const members = (rows) => rows.map((row) => ({
      cooldown: row.cooldown, id: row.id, tier: row.tier, weight: row.weight,
    }));
    const entry = (name, rows, mode) => {
      const contexts = rows.map((row) => row.max_input_tokens).filter((n) => typeof n === "number");
      const pool = {
        context: contexts.length ? Math.max(...contexts) : null,
        members: members(rows),
        name: `daedalus/${name}`,
        shown: `daedalus/${name}`,
      };
      if (mode) pool.mode = mode;
      return pool;
    };
    const tier_rows = (key) => {
      const tier = key.toUpperCase();
      return state.models.filter((row) => row.tier === tier);
    };
    const chat = state.models.filter((row) => row.mode === "chat");
    if (chat.length) out.push(entry("auto", chat));
    for (const key of Object.keys(names).filter((key) => key.startsWith("tier-")).sort()) {
      const rows = tier_rows(key);
      if (rows.length) out.push(entry(names[key], rows));
    }
    if (names.audio) {
      const rows = state.models.filter((row) => row.mode === "audio_transcription");
      if (rows.length) out.push(entry(names.audio, rows, "audio_transcription"));
    }
    if (names.images) {
      const rows = state.models.filter((row) => row.mode === "image_generation");
      if (rows.length) out.push(entry(names.images, rows, "image_generation"));
    }
    return out;
  };
  // Limits checks read each provider again. Simulated plans account for requests from this page.
  const served_by = (provider) =>
    [...USED].filter(([id]) => String(id).startsWith(provider + "/"))
      .reduce((sum, [, count]) => sum + count, 0);
  const lane_of = (model, template, now) => {
    const provider = String(model).split("/")[0];
    const plan = plan_of(provider, blocks().get(provider));
    const count = USED.get(model) || 0;
    const waited = template ? Math.max(1, (template.rows[0]?.reset || now + 120) - template.at) : 120;
    const span_of = (kind) => template?.rows.find((row) => row.kind === kind)?.span ?? null;
    return {
      at: now,
      client: null,
      model,
      rows: [
        {
          kind: "requests", limit: plan.requests, span: span_of("requests"),
          remaining: Math.max(0, plan.requests - count), reset: now + waited,
        },
        {
          kind: "tokens", limit: plan.tokens, span: span_of("tokens"),
          remaining: Math.max(0, plan.tokens - count * 40), reset: now + waited,
        },
      ],
    };
  };
  const cards = () => {
    const out = [];
    for (const card of DEMO_FIXTURES.limits.providers) {
      const provider = card.name;
      const count = served_by(provider);
      const plan = plan_of(provider, blocks().get(provider));
      if (provider === "openrouter") {
        const left = Math.max(0, plan.free - count);
        out.push({ items: [{ kind: "free_requests", value: left, limit: plan.free, share: left / plan.free }],
          name: provider });
      } else if (provider === "kilo") {
        const left = Math.max(0, plan.requests - count);
        out.push({ items: [{ kind: "balance", value: 0, limit: null, share: null },
          { kind: "hourly_requests", value: String(left), limit: String(plan.requests),
            share: left / plan.requests }], name: provider });
      } else if (provider === "pollinations") {
        out.push({ items: [{ kind: "pollen", value: 0.25, limit: null, share: null }], name: provider });
      } else if (provider === "cloudflare") {
        const left = Math.max(0, plan.neurons - count * 40);
        out.push({ items: [{ kind: "neurons", value: left, limit: plan.neurons, share: left / plan.neurons }],
          name: provider });
      }
    }
    return out;
  };
  const check_limits = () => {
    const now = Date.now() / 1000;
    const lanes = [];
    for (const lane of DEMO_FIXTURES.limits.lanes) {
      if (!model_of(lane.model)) continue;
      lanes.push(lane_of(lane.model, lane, now));
    }
    // Report a new lane for chat models absent from the capture.
    const captured = new Set(DEMO_FIXTURES.models.map((row) => row.id));
    for (const row of state.models) {
      if (!captured.has(row.id) && row.mode === "chat") lanes.push(lane_of(row.id, null, now));
    }
    return json({ checked: now, lanes, providers: cards() });
  };
  // Rebuild the catalog after provider writes to update Models, pools, and Limits.
  const reload = (job) =>
    job && job.then((answer) => {
      if (answer.status === 200 || answer.status === 201) state.models = catalog();
      return answer;
    });
  // Parse saved provider YAML into file blocks.
  const file_saved = (init) => {
    const asked = body_of(init);
    const file = DEMO_FIXTURES.files.find((item) => item.path === asked.path);
    if (!file) return bad("Unknown config file.", 400);
    const text = String(asked.text ?? "");
    const stem = stem_of(file.path);
    const parsed = yaml_read(text);
    if (parsed.error) return bad(parsed.error, 422);
    if (!parsed.value || typeof parsed.value !== "object" || Array.isArray(parsed.value))
      return bad(`${file.path} must hold provider blocks`, 422);
    file.text = text;
    if (file.main) {
      file.local = parsed.value;
      file.blocks = { ...CLOUD_MAIN, ...parsed.value };
    } else file.blocks = { [stem]: parsed.value };
    file.error = null;
    return reload(json({ ok: true, text }));
  };
  const PROVIDER_SHAPES = __PROVIDER_SHAPES__;
  const provider_map = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const provider_shape = (value, kind) => kind === "dict" ? provider_map(value)
    : kind === "list" ? Array.isArray(value) : typeof value === "string";
  const provider_error = block => {
    for (const [key, [kind, wanted]] of Object.entries(PROVIDER_SHAPES.block)) {
      if (block[key] != null && !provider_shape(block[key], kind)) return `${key} must be ${wanted}`;
    }
    for (const [key, [kind, wanted]] of Object.entries(PROVIDER_SHAPES.nested)) {
      if (!provider_map(block[key])) continue;
      for (const [name, value] of Object.entries(block[key])) {
        if (value != null && !provider_shape(value, kind)) return `${key}.${name} must be ${wanted}`;
      }
    }
    return "";
  };
  const provider_env_name = (provider, client = "") =>
    `${provider}_API_KEY${client ? "_" + client : ""}`.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  // Write provider form blocks to file text.
  const form_saved = (init) => {
    const asked = body_of(init);
    const file = DEMO_FIXTURES.files.find((item) => item.path === asked.path);
    if (!file) return bad("Unknown config file.", 400);
    const sent = asked.blocks;
    if (!provider_map(sent) || !Object.values(sent).every(provider_map))
      return bad("Each provider block must be a map.", 400);
    const stem = stem_of(file.path);
    let document = file.main ? sent : sent[stem];
    if (!provider_map(document)) return bad(`${stem} must hold the block ${stem}.`, 400);
    const pending = new Map();
    for (const [provider, block] of Object.entries(file.main ? document : { [stem]: document })) {
      const name = provider_env_name(provider);
      const fields = [[block, "api_key", name], [block, "account_id",
        provider === "cloudflare" ? "CLOUDFLARE_ACCOUNT_ID" : name + "_ACCOUNT_ID"]];
      if (provider_map(block.client_keys)) {
        for (const client of Object.keys(block.client_keys))
          fields.push([block.client_keys, client, provider_env_name(provider, client)]);
      }
      for (const [target, key, name] of fields) {
        const value = target[key];
        if (typeof value !== "string") continue;
        const raw = value.trim();
        if (raw.startsWith("env:")) {
          const found = raw.match(/env:([A-Za-z_][A-Za-z0-9_]*)/);
          if (!found) return bad("The env: value must be env:NAME with a valid name.", 400);
          target[key] = "env:" + found[1];
        } else if (raw && !value.includes("env:") && !value.includes("db:")) {
          if (raw.length > PROVIDER_SHAPES.max_value || /\\s/.test(raw))
            return bad("The value must be 1 word with no spaces.", 400);
          pending.set(name, raw);
          target[key] = "db:" + name;
        }
      }
    }
    if (file.main) {
      const parsed = file.local || yaml_read(file.text).value || {};
      document = Object.fromEntries(Object.entries(sent).filter(([name, block]) =>
        Object.prototype.hasOwnProperty.call(parsed, name)
        || JSON.stringify(block) !== JSON.stringify(CLOUD_MAIN[name])));
    }
    let problem = provider_error(document);
    for (const [name, block] of Object.entries(document)) {
      if (!problem && !Object.prototype.hasOwnProperty.call(PROVIDER_SHAPES.block, name) && provider_map(block)) {
        const error = provider_error(block);
        if (error) problem = `${name}.${error}`;
      }
    }
    if (problem) return bad(`${file.path}: ${problem}`, 422);
    const text = yaml_of(document);
    for (const [name, secret] of pending) {
      let row = DEMO_FIXTURES.env.find(item => item.name === name);
      if (!row) {
        row = { name, used: [file.path], has_env: false };
        DEMO_FIXTURES.env.push(row);
      }
      Object.assign(row, saved_fields(secret));
    }
    if (file.main) {
      file.local = document;
      file.blocks = { ...CLOUD_MAIN, ...document };
    } else file.blocks = { [stem]: document };
    file.text = text;
    file.error = null;
    return reload(json({ ok: true, text }));
  };
  const hook_dropped = (init) => {
    const { name } = body_of(init);
    if (typeof name !== "string" || !name.trim()) return bad("A file name is needed.", 400);
    const saved = DEMO_FIXTURES.settings;
    const at = saved.hook_rows.findIndex(row => row.path === name || row.path === `${name}.py`);
    if (at < 0) return bad(`The hook ${name} is not a file of the folder.`, 404, {
      locale_key: "hook.error.file_missing_text", locale_params: { name },
    });
    const leaf = saved.hook_rows[at].name.split("/").pop();
    const group = { ...DEFAULTS.hooks, ...saved.file.hooks };
    const keep = (items) => (items || []).filter(item => String(item).split("/").pop() !== leaf);
    const changes = {
      disabled: keep(group.disabled),
      order: Object.fromEntries(Object.entries(group.order || {}).map(([surface, files]) => [surface, keep(files)])),
    };
    for (const [key, value] of Object.entries(group)) {
      if (key.startsWith("on-") && Array.isArray(value)) changes[key] = keep(value);
    }
    saved.hook_rows.splice(at, 1);
    return settings_saved({ body: JSON.stringify({ changes: { hooks: changes } }) });
  };
  // Reject writes that the demo does not support.
  const save = (path, method, init) => {
    if (path === "catalog" && method === "POST") return rebuild();
    if (path === "limits" && method === "POST") return check_limits();
    if (path === "updates" && method === "POST") {
      // Keep the captured answer and update its check time.
      DEMO_FIXTURES.notifications.update = { ...DEMO_FIXTURES.notifications.update, at: Date.now() / 1000 };
      return json(DEMO_FIXTURES.notifications.update);
    }
    if (path === "reset" && method === "POST") {
      // Reset captured model weights to 1.
      for (const row of DEMO_FIXTURES.models) {
        weights.set(row.id, 1);
        cooldowns.set(row.id, null);
      }
      for (const row of state.models)
        Object.assign(row, { weight: 1, cooldown: null, client_cooldowns: {} });
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
    if (path === "files" && method === "PUT") return file_saved(init);
    if (path === "providers" && method === "PUT") return form_saved(init);
    if (path === "files" && method !== "GET")
      return bad("The demo keeps the shipped file list read-only.", 403);
    if (path === "settings" && method === "PUT") return settings_saved(init);
    if (path === "hooks/file" && method === "DELETE") return hook_dropped(init);
    // Hook scans name the demo repository. Selected files stay in page memory.
    if (path === "hooks/scan" && method === "POST") {
      const asked = body_of(init);
      return json({
        repo: word(asked.repo) || DEMO_HOOKS.repo,
        path: word(asked.path) || "hooks",
        ref: word(asked.ref) || "main",
        commit: DEMO_HOOKS.commit,
        files: DEMO_HOOKS.files,
      });
    }
    if (path === "hooks/update" && method === "POST") return json(DEMO_HOOKS.answer);
    return null;
  };
  // Simulate hook scans with 2 files and report version changes on update.
  const DEMO_HOOKS = {
    repo: "nemoe7/daedalus",
    commit: "3f9c1ab4d2e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9",
    files: [
      { name: "served_model.py", version: "1.3.0", scope: "global", targets: [], surfaces: ["on-chunk"], problem: "" },
      { name: "owui_auto_reasoning_effort.py", version: "1.1.0", scope: "model", targets: ["gpt-5"],
      surfaces: ["on-prompt"], problem: "" },
    ],
    answer: {
      moved: ["served_model.py"],
      hooks: [
        { name: "served_model.py", moved: true, before: { version: "1.2.0" }, after: { version: "1.3.0" } },
        { name: "owui_auto_reasoning_effort.py", moved: false, before: { version: "1.1.0" },
        after: { version: "1.1.0" } },
      ],
    },
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
      const contentType = new Headers(init.headers).get("content-type") || "";
      if (["POST", "PUT", "PATCH", "DELETE"].includes(method)
        && contentType.split(";")[0].trim() !== "application/json")
        return bad("Send application/json content.", 400);
      const job = save(path, method, init);
      if (job) return job;
      return json({ error: { message: "This is a static demo. The change is not saved." } }, 403);
    }
    if (path === "requests") return json(slice(url));
    if (path === "models") return json(state.models.map((row) => ({
      ...row,
      weight: weights.has(row.id) ? weights.get(row.id) : row.weight,
      cooldown: cooldowns.has(row.id) ? cooldowns.get(row.id) : row.cooldown ?? null,
    })));
    if (path === "pools") return json(pools());
    if (path === "settings") {
      const saved = DEMO_FIXTURES.settings;
      const disabled = saved.file.hooks?.disabled || [];
      const hook_rows = saved.hook_rows.map(row => {
        const names = [row.name, row.name.split("/").pop()];
        const enabled = !row.problem && !names.some(name =>
          disabled.includes(name) || disabled.includes(name.slice(0, -3)));
        return { ...row, enabled };
      });
      return json({ ...saved, hook_rows });
    }
    if (path === "limits") return check_limits();
    if (path === "notifications") return json(DEMO_FIXTURES.notifications);
    if (path === "status")
      return json({ ...DEMO_FIXTURES.status, models: state.models.length,
        sessions: new Set(DEMO_FIXTURES.requests.map((row) => row.session).filter(Boolean)).size });
    if (!(path in DEMO_FIXTURES)) return json({}, 404);
    return json(DEMO_FIXTURES[path]);
  };
  const keep = (row) => {
    DEMO_FIXTURES.requests.unshift(row);
    DEMO_FIXTURES.requests.length = Math.min(DEMO_FIXTURES.requests.length, KEPT);
    const served = row.via || row.model;
    USED.set(served, (USED.get(served) || 0) + 1);
    apply(row);
  };
  // Emit server events for one request per wave, with separate timings and random gaps between waves.
  window.EventSource = class {
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      this.next = 0;
      this.sessions = 0;
      // Send requests in progress first, then each change, as the server does.
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
      // Keep requests in progress for the next page connection, as the server does.
      if (kind === "start" || kind === "update" || kind === "first") this.running.set(data.id, data);
      if (kind === "end") this.running.delete(data.id);
      for (const call of this.listeners.get(kind) || []) call({ data: JSON.stringify(data) });
    }
    async loop() {
      // Let the page attach listeners before the server response on a later tick.
      await wait(0);
      this.send("live", [...this.running.values()]);
      const queue = window.DEMO_SCENARIOS;
      while (!this.closed) {
        const kind = queue && queue.length ? queue.shift() : this.select();
        await this.scenario(kind);
        if (this.closed) return;
        await wait(gap(...WAVE_MS));
      }
    }
    // Simulate mostly OWUI chats, frequent agents, and rare unknown clients.
    select() {
      const r = Math.random();
      if (r < 0.06) return "stray";
      if (r < 0.18) return "kilo";
      if (r < 0.34) return "agentic";
      if (r < 0.46) return "rag";
      if (r < 0.62) return "escalate";
      if (r < 0.72) return "race";
      return "chat";
    }
    async scenario(kind) {
      if (kind === "stray") return this.stray();
      if (kind === "kilo") return this.agentic("Kilo", 1 + Math.floor(Math.random() * 4));
      if (kind === "agentic") return this.agentic("OWUI", 2 + Math.floor(Math.random() * 4));
      if (kind === "rag") return this.rag();
      if (kind === "escalate") return this.escalate();
      if (kind === "race") return this.race();
      return this.chat();
    }
    // Run OWUI chat requests in order: embedding, title, and chat.
    async chat() {
      const s = session(++this.sessions * 7919);
      await this.call({
        app: "OWUI", session: s, model: EMBED, via: EMBED,
        path: "/v1/embeddings", stream: false, ttft: ttftFor("embed"),
      });
      await this.call({
        app: "OWUI", session: s, model: "daedalus/auto", via: select(TEXT),
        stream: false, ttft: ttftFor("title"),
      });
      await this.call({
        app: "OWUI", session: s, model: "daedalus/auto", via: select(TEXT),
        stream: true, ttft: ttftFor("chat"), effort: "none", routed: "deinos",
        tokens: { estimate: false, input: 14 + this.sessions, output: 5 + this.sessions },
      });
    }
    // Run embeddings for RAG search, then answer the chat from documents.
    async rag() {
      const s = session(++this.sessions * 7919);
      await this.call({
        app: "OWUI", session: s, model: EMBED, via: EMBED,
        path: "/v1/embeddings", stream: false, ttft: ttftFor("embed"),
      });
      await this.call({
        app: "OWUI", session: s, model: "daedalus/auto", via: select(TEXT),
        stream: true, ttft: ttftFor("chat"), effort: "low",
        tokens: { estimate: false, input: 480 + this.sessions, output: 90 },
      });
    }
    // Increase agent effort once per call, with an optional stop on a tool loop.
    async agentic(app, steps) {
      const s = session(++this.sessions * 7919);
      for (let i = 0; i < steps; i++) {
        if (this.closed) return;
        await this.call({
          app, session: s, model: "daedalus/auto", via: select(TEXT),
          stream: true, ttft: ttftFor("chat"), effort: LADDER[Math.min(i, LADDER.length - 1)],
          loop: i === steps - 1 ? "2" : null,
          tokens: { estimate: true, input: 1200 + 90 * i, output: 240 + 30 * i },
        });
      }
    }
    // Retry at a higher tier and keep the chain, cooldown, and weight.
    async escalate() {
      // Use a captured cooldown template, if available, to show the rate limit.
      const cooled = TEMPLATES.find((row) => (row.attempts || []).some((a) => a.cooldown));
      const template = cooled || select(TEMPLATES);
      const reason = REASONS[reasonN++ % REASONS.length];
      const s = session(++this.sessions * 7919);
      await this.call({
        app: template.app, session: s, model: template.model, pool: template.pool,
        via: template.via, stream: template.stream === true, effort: template.effort,
        ttft: ttftFor("chat"), transition: { ...template.transition, reason },
        fallbacks: template.fallbacks, retry: template.retry, loop: template.loop,
        routed: template.routed, tokens: template.tokens,
        attempts: (template.attempts || []).map((item) => ({ ...item })),
      });
    }
    async race() {
      const template = TEMPLATES.find((row) => row.race === "drawn");
      await this.call({
        ...template, session: session(++this.sessions * 7919), stream: true, ttft: 0.297,
      });
    }
    // Simulate rare requests with no known app or session.
    async stray() {
      const pooled = Math.random() < 0.5;
      const model = pooled ? `daedalus/${select(POOL_NAMES)}` : select(TEXT);
      await this.call({
        app: null, session: null, model, via: pooled ? select(MEMBERS[model] || TEXT) : model,
        stream: Math.random() < 0.5, ttft: ttftFor("chat"),
      });
    }
    // Emit each request in four stages: start, model selection, first token, and end.
    async call(spec) {
      const id = ++this.next;
      // Include the answering pool in auto rows, as `dashboard.record` does.
      const pool = spec.pool ?? (String(spec.model).startsWith("daedalus/auto")
        ? POOL_OF[spec.via] || null : null);
      began(id);
      const live = { id, path: spec.path || "/v1/chat/completions", ...ages(id), ttft: null };
      this.send("start", live);
      await wait(gap(60, 200));
      // Restart the attempt clock when the simulation selects the model.
      attempted(id);
      Object.assign(live, {
        app: spec.app ?? null, session: spec.session ?? null, key: "master", model: spec.model,
        effort: spec.effort ?? null, pool, stream: spec.stream === true,
        trying: spec.via, via: null, attempts: [], tokens: null,
        fallbacks: spec.fallbacks || "0", ...ages(id),
      });
      this.send("update", live);
      await wait(round(spec.ttft) * 1000);
      // Freeze TTFT at the first token and start the stream clock at zero.
      const frozen = ages(id);
      Object.assign(live, { trying: null, via: spec.via, ttft: frozen.attempt_age, ...frozen });
      this.send("first", live);
      const streamed = round(streamFor(spec.stream === true));
      await wait(streamed * 1000);
      const elapsed = ages(id);
      const source = spec.attempts || [{ model: spec.via, result: "answered", error: "" }];
      const parallel = spec.race === "drawn";
      const span = parallel ? Math.max(...source.map(item => item.seconds || 0))
        : source.reduce((sum, item) => sum + (item.result === "answered" ? 0 : item.seconds || 0), 0);
      const budget = parallel ? frozen.attempt_age : round(frozen.age - frozen.attempt_age);
      const attempts = source.map(item => ({ ...item, seconds: item.result === "answered"
        ? frozen.attempt_age : item.seconds == null ? null
        : Math.floor((span ? item.seconds * budget / span : 0) * 1000) / 1000 }));
      // Include `dashboard.record` fields: status code, text times, and answer.
      const done = {
        at: Date.now() / 1000, status: 200, seconds: elapsed.age,
        app: spec.app ?? null, session: spec.session ?? null, key: "master", model: spec.model,
        effort: spec.effort ?? null, pool, routed: spec.routed ?? null,
        transition: spec.transition ?? null, race: spec.race ?? null,
        retry: spec.retry ?? null, loop: spec.loop ?? null,
        via: spec.via, ttft: `${frozen.attempt_age.toFixed(3)}s`, stream: spec.stream === true,
        fallbacks: spec.fallbacks || "0", tokens: spec.tokens ?? null,
        attempts,
      };
      // Store the row before the end event so page refreshes read it at once.
      keep(done);
      this.send("end", { id, row: done });
      CLOCKS.delete(id);
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


def demo_fixtures(version: str) -> dict[str, Any]:
  """The fixtures of 1 build: the rows the provider files keep, with the version stamps."""
  fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
  # The snapshot is the actual catalog state, so every row stays.
  kept = {row["id"] for row in fixtures["models"]}
  for pool in fixtures["pools"]:
    pool["members"] = [item for item in pool["members"] if item["id"] in kept]
  fixtures["login"]["version"] = version
  fixtures["status"]["version"] = version
  update = fixtures["notifications"]["update"] or {}
  update["current"] = version
  fixtures["notifications"]["update"] = update
  # The hook rows come from the files of this repo, so the table shows the version each 1 carries.
  # The capture holds an older copy, and a version bump lands here without a new capture.
  settings_file = fixtures["settings"].setdefault("file", {})
  hooks_file = settings_file.setdefault("hooks", {})
  for surface in DEMO_HOOK_SURFACES:
    hooks_file.pop(surface, None)
  hooks_file["order"] = demo_hook_order()
  hooks_file["sources"] = [dict(DEMO_SOURCE)]
  saved = hooks.ROOT
  try:
    # The build reads the shipped folder, whatever root folder the running process holds.
    hooks.ROOT = ROOT
    rows = hooks.rows({})
  finally:
    hooks.ROOT = saved
  fixtures["settings"]["hook_rows"] = [{**row, "record": {}} for row in rows]
  # The YAML card shows the same metadata-only order and source.
  fixtures["settings"]["text"] = settings.update_text(
    fixtures["settings"]["text"],
    {
      "hooks": {
        **{surface: None for surface in DEMO_HOOK_SURFACES},
        "order": demo_hook_order(),
        "sources": [dict(DEMO_SOURCE)],
      }
    },
  )
  return fixtures


def build(out: Path, version: str) -> Path:
  """Copy the UI into `out`, add the demo script, and load it before the page."""
  fixtures = demo_fixtures(version)
  if out.exists():
    shutil.rmtree(out)
  shutil.copytree(UI, out / "ui")
  shapes = {
    name: {key: [kind.__name__, wanted] for key, (kind, wanted) in group.items()}
    for name, group in (
      ("block", config.BLOCK_SHAPES),
      ("nested", config.NESTED_SHAPES),
    )
  }
  shapes["max_value"] = dashboard.MAX_VALUE
  (out / "demo.js").write_text(
    DEMO_JS.replace("__FIXTURES__", json.dumps(fixtures)).replace(
      "__PROVIDER_SHAPES__", json.dumps(shapes)
    ),
    encoding="utf-8",
  )
  page = (UI / "index.html").read_text(encoding="utf-8")
  marker = '<script src="ui/en.js"></script>\n<script src="ui/app.js"></script>'
  if marker not in page:
    raise SystemExit(
      "index.html does not load ui/en.js before ui/app.js: the demo build cannot patch it"
    )
  # The version rides on each asset, so a browser loads a new build and not the cache.
  stamped = (
    f'<script src="demo.js?v={version}"></script>\n'
    f'<script src="ui/en.js?v={version}"></script>\n'
    f'<script src="ui/app.js?v={version}"></script>'
  )
  page = page.replace(marker, stamped)
  page = page.replace(
    '<link rel="stylesheet" href="ui/style.css">',
    f'<link rel="stylesheet" href="ui/style.css?v={version}">',
  )
  (out / "index.html").write_text(page, encoding="utf-8")
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
