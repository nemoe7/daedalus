"""The GitHub Pages demo: the build, the fixtures, and the script that answers the page."""

import json
import re
import subprocess
from pathlib import Path

import pages_demo
import pytest

from daedalus import __version__ as DAEDALUS_VERSION

APP_JS = Path("daedalus/dashboard/ui/app.js")
# The calls that change state. The demo answers each of them with 403.
WRITES = (
  "catalog",
  "env",
  "files",
  "keys",
  "keys/",
  "logout",
  "providers",
  "reset",
  "settings",
)
# The calls that the demo answers from the fixtures.
READS = (
  "env",
  "files",
  "keys",
  "limits",
  "login",
  "models",
  "pools",
  "provider-defaults",
  "provider-keys",
  "requests",
  "settings",
  "status",
)

NODE_CHECK = """
const fs = require("fs");
const vm = require("vm");
const context = {
  location: { origin: "https://demo.test", href: "https://demo.test/" },
  Response, URL, setTimeout, clearTimeout, console, Promise, Math, JSON, Object, Array,
  network: [],
};
context.window = context;
context.fetch = (...args) => {
  context.network.push(args);
  throw new Error("the demo called the network");
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("demo.js", "utf8"), context);
const check = (ok, text) => {
  if (!ok) throw new Error(text);
  console.log("ok " + text);
};
(async () => {
  const status = await context.fetch("ui/api/status");
  check(status.status === 200, "status");
  check((await status.json()).healthy === true, "status body");
  const short = await (await context.fetch("ui/api/requests?limit=10")).json();
  check(short.length === 10, "the limit of the request page");
  const all = await (await context.fetch("ui/api/requests")).json();
  check(all.length >= 60, "the filled Requests tab");
  const write = await context.fetch("ui/api/env", { method: "PUT" });
  check(write.status === 403, "a write is refused");
  check((await write.json()).error.message.includes("static demo"), "the write text");
  const missing = await context.fetch("ui/api/nothing");
  check(missing.status === 404, "an unknown call");
  const seen = [];
  const stream = new context.EventSource("ui/api/requests/live");
  ["live", "start", "update", "first", "end"].forEach((kind) =>
    stream.addEventListener(kind, (event) => seen.push([kind, JSON.parse(event.data)])),
  );
  await new Promise((done) => setTimeout(done, 3600));
  stream.close();
  const kinds = seen.map(([kind]) => kind);
  check(kinds.includes("start") && kinds.includes("end"), "a live request");
  const end = seen.find(([kind]) => kind === "end")[1];
  check(end.row.model && end.row.at, "the finished row");
  const KEYS = ["app", "at", "attempts", "effort", "fallbacks", "key", "loop", "model",
    "pool", "retry", "routed", "seconds", "session", "status", "stream", "tokens",
    "transition", "ttft", "via"];
  const stamp = (row) => row.ttft == null || (
    typeof row.ttft === "string" && row.ttft.endsWith("s") && row.ttft.split(".")[1]?.length === 4
  );
  // The rows of the table carry the fields and the types of `dashboard.record`.
  const shaped = (row) => KEYS.every((key) => key in row) && typeof row.status === "number"
    && typeof row.fallbacks === "string" && stamp(row)
    && (row.tokens == null || typeof row.tokens.estimate === "boolean")
    && (row.attempts || []).every((a) => a.result && "error" in a);
  check(shaped(end.row), "the finished row carries the fields of the server");
  const grown = await (await context.fetch("ui/api/requests?limit=500")).json();
  check(grown.length > all.length, "the finished row joins the table");
  check(grown.every(shaped), "each row of the table carries the fields of the server");
  const live = seen.find(([kind]) => kind === "start")[1];
  check(live.id && live.path && live.ttft === null, "the start row of a live request");
  check(context.network.length === 0, "no call reaches the network");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
"""


def test_the_fixtures_cover_each_call_of_the_page() -> None:
  """A new call on the page fails here, so the capture runs again."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  assert sorted(fixtures) == sorted(READS), sorted(fixtures)
  calls = set(re.findall(r'call\("([a-z/_-]*)"', APP_JS.read_text(encoding="utf-8")))
  unknown = calls - set(READS) - set(WRITES)
  assert not unknown, unknown


def test_the_fixtures_carry_the_edge_cases() -> None:
  """The demo shows the states that the page must fit: a long name, a cooldown and a failure."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  rows = fixtures["models"]
  assert len(rows) >= 10, len(rows)
  assert any(len(row["id"]) > 50 for row in rows), "a long model name"
  assert any(row.get("cooldown") for row in rows), "a cooldown"
  assert any(row.get("weight") is not None and row["weight"] < 1 for row in rows), (
    "a weight below 1"
  )
  assert {row["mode"] for row in rows} >= {
    "chat",
    "audio_transcription",
    "image_generation",
    "embedding",
  }, "each mode"
  requests = fixtures["requests"]
  assert len(requests) >= 8, len(requests)
  assert any(row.get("fallbacks") for row in requests), "a fallback chain"
  assert any(
    attempt.get("cooldown") for row in requests for attempt in row.get("attempts") or []
  ), "a rate limit with a cooldown"
  assert any(row.get("stream") for row in requests), "a stream"
  assert any(len(key["name"]) == 40 for key in fixtures["keys"]), (
    "a key name at its limit"
  )
  assert any(file.get("shadow") for file in fixtures["files"]), "a shadowed file"
  assert fixtures["login"]["session"] is True, "the demo opens without a login"


def test_the_build_copies_the_page_and_loads_the_demo_first(tmp_path: Path) -> None:
  """`--out` serves the real UI, with the demo script before it."""
  out = pages_demo.build(tmp_path / "site")
  page = (out / "index.html").read_text(encoding="utf-8")
  assert page.index("demo.js") < page.index("ui/app.js")
  assert 'src="demo.js"' in page
  for name in ("app.js", "index.html", "style.css", "manifest.json", "logo.svg"):
    assert (out / "ui" / name).exists() or (out / name).exists(), name
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  assert json.dumps(fixtures) in (out / "demo.js").read_text(encoding="utf-8")
  assert 'const MARKER = "ui/api/";' in (out / "demo.js").read_text(encoding="utf-8"), (
    "the demo matches the call path"
  )


def test_the_demo_answers_the_page_without_a_server(tmp_path: Path) -> None:
  """The script in a JavaScript runtime: the fixtures, a refused write and a live request."""
  out = pages_demo.build(tmp_path / "site")
  built = subprocess.run(
    ["node", "-e", NODE_CHECK],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert built.returncode == 0, f"{built.stdout}\n{built.stderr}"


def test_the_capture_builds_the_demo_state() -> None:
  """The capture runs the dashboard in this process, so the fixtures can be made again."""
  assert pytest.importorskip("fastapi")
  found = pages_demo.capture()
  assert found["status"]["version"] == DAEDALUS_VERSION, found["status"]["version"]
  assert len(found["models"]) >= 10, len(found["models"])
  assert found["requests"], "the demo requests"
