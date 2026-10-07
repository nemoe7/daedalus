"""The GitHub Pages demo: the build, the fixtures, and the script that answers the page."""

import json
import os
import re
import subprocess
from pathlib import Path

import pages_demo
import pytest

from daedalus import __version__ as DAEDALUS_VERSION

APP_JS = Path("daedalus/dashboard/ui/app.js")
# The calls that change state. The demo holds each of them in the page memory.
SIMULATED = (
  "catalog",
  "env",
  "files",
  "keys",
  "keys/",
  "limits",
  "login",
  "logout",
  "providers",
  "reset",
  "settings",
)
# The calls that the demo answers from the fixtures.
READS = (
  "env",
  "files",
  "hooks",
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
  Response, URL, clearTimeout, console, Promise, Math, JSON, Object, Array,
  network: [], listeners: [], timers: [],
};
context.window = context;
context.addEventListener = (kind) => context.listeners.push(kind);
// The rebuild waits 26 s. The test shortens only that timer.
context.setTimeout = (fn, ms, ...rest) => {
  context.timers.push(ms);
  return setTimeout(fn, ms === 26000 ? 30 : ms, ...rest);
};
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
  const first = await (await context.fetch("ui/api/requests?limit=10")).json();
  check(first.length === 0, "the Requests tab starts empty");
  const all = await (await context.fetch("ui/api/requests")).json();
  check(all.length === 0, "the Overview starts empty too");
  const stray = await context.fetch("ui/api/nothing", { method: "PUT" });
  check(stray.status === 403, "a write with no home is refused");
  check((await stray.json()).error.message.includes("static demo"), "the write text");
  const missing = await context.fetch("ui/api/nothing");
  check(missing.status === 404, "an unknown call");
  const stateBefore = await (await context.fetch("ui/api/status")).json();
  const start = await context.fetch("ui/api/catalog", { method: "POST" });
  check(start.status === 202, "a rebuild starts");
  const running = await (await context.fetch("ui/api/status")).json();
  check(running.catalog.rebuilding === true, "the chip reads rebuilding");
  const busy = await context.fetch("ui/api/catalog", { method: "POST" });
  check(busy.status === 409, "a rebuild is refused while 1 runs");
  check((await busy.json()).error.message.includes("rebuild runs now"), "the busy text");
  check(context.timers.includes(26000), "the rebuild takes the real 26 s");
  await new Promise((done) => setTimeout(done, 300));
  const stateAfter = await (await context.fetch("ui/api/status")).json();
  check(stateAfter.catalog.rebuilding === false, "the rebuild finishes");
  check(stateAfter.catalog.built !== stateBefore.catalog.built, "a fresh built time");
  context.addEventListener("beforeunload", () => {});
  context.addEventListener("click", () => {});
  check(!context.listeners.includes("beforeunload"), "the reload warning is dropped");
  check(context.listeners.includes("click"), "the other listeners still register");
  // A simulated save lands in the page memory, and a reload brings the fixtures back.
  const save = await context.fetch("ui/api/settings", {
    method: "PUT",
    body: JSON.stringify({ changes: { personalization: { theme: "light" } } }),
  });
  check(save.status === 200, "a settings save lands");
  const held = await (await context.fetch("ui/api/settings")).json();
  check(held.file.personalization.theme === "light", "the page shows the saved value");
  check(held.text.includes("theme: light"), "the saved text holds it");
  // An empty field writes the default, and the group stays whole.
  const clearedTheme = await context.fetch("ui/api/settings", {
    method: "PUT",
    body: JSON.stringify({ changes: { personalization: { theme: null } } }),
  });
  check(clearedTheme.status === 200, "a settings clear lands");
  const afterClear = await (await context.fetch("ui/api/settings")).json();
  check(afterClear.file.personalization === undefined, "the cleared key leaves the file");
  check(!afterClear.text.includes("theme"), "the cleared line leaves the text");
  const env = await (await context.fetch("ui/api/env", {
    method: "PUT",
    body: JSON.stringify({ name: "CLOUDFLARE_ACCOUNT_ID", value: "abc12345" }),
  })).json();
  check(env.find((row) => row.name === "CLOUDFLARE_ACCOUNT_ID").state === "saved", "an env save lands");
  const made = await context.fetch("ui/api/keys", {
    method: "POST",
    body: JSON.stringify({ name: "extra-client" }),
  });
  check(made.status === 201, "a key is made");
  const key = await made.json();
  check(key.name === "extra-client" && key.key.startsWith("sk-"), "the key text");
  check((await (await context.fetch("ui/api/keys")).json()).some((row) => row.name === "extra-client"),
    "the key list shows it");
  check((await context.fetch("ui/api/keys/extra-client", { method: "DELETE" })).status === 204,
    "a key is dropped");
  check(!(await (await context.fetch("ui/api/keys")).json()).some((row) => row.name === "extra-client"),
    "the key list drops it");
  // The shipped provider files are display-only: the list shows them as-is, writes refuse.
  const files = await (await context.fetch("ui/api/files")).json();
  check(files.length === 3
    && files.every((row) => String(row.path).startsWith("config/providers/")),
    "the file list holds the shipped files");
  const main = files.find((row) => row.main);
  check(main.path === "config/providers/free.yml" && main.text.includes("tier:"),
    "the main file is verbatim");
  check(files.some((row) => row.shadow), "a shadowed file keeps its note");
  const models = await (await context.fetch("ui/api/models")).json();
  check(models.length > 100, "the models are the captured snapshot");
  check(models.every((row) => !String(row.id).includes("*")), "no glob reaches the models page");
  const sophos = (await (await context.fetch("ui/api/pools")).json())
    .find((pool) => pool.name === "daedalus/sophos");
  check(sophos.members.length > 10 && sophos.members.every((row) => row.tier === "TIER-A"),
    "a pool reads its tier patterns");
  const readOnly = async (path, body, method) => {
    const answer = await context.fetch("ui/api/" + path, { method, body: JSON.stringify(body) });
    check(answer.status === 403, `the refusal of ${path}`);
    check((await answer.json()).error.message.includes("read-only"), `the text of ${path}`);
  };
  await readOnly("providers", { path: main.path, blocks: main.blocks }, "PUT");
  await readOnly("files", { path: main.path, text: main.text }, "PUT");
  await readOnly("files", { name: "extra" }, "POST");
  await readOnly("files", { path: main.path }, "DELETE");
  check((await (await context.fetch("ui/api/files")).json()).length === 3,
    "the files stay as shipped");
  check((await context.fetch("ui/api/reset", { method: "POST" })).status === 200, "a reset lands");
  const cleared = await (await context.fetch("ui/api/models")).json();
  check(cleared.every((row) => row.cooldown === null && row.weight === 1),
    "the reset clears the weights");
  // Each write carries the checks of the server: the names and the session.
  const refuse = async (path, body, status, text, method = "PUT") => {
    const answer = await context.fetch("ui/api/" + path, { method, body: JSON.stringify(body) });
    check(answer.status === status, `the refusal of ${path} (${status})`);
    check((await answer.json()).error.message.includes(text), `the text of ${path}`);
  };
  await refuse("settings", { changes: { personalization: { grid: 1 } } }, 422, "unknown key");
  await refuse("keys", { name: "x".repeat(41) }, 400, "1 to 40 characters", "POST");
  await refuse("keys", { name: "openwebui" }, 400, "is in use", "POST");
  await context.fetch("ui/api/logout", { method: "POST" });
  check((await (await context.fetch("ui/api/login")).json()).session === false, "a logout ends the session");
  const wrong = await context.fetch("ui/api/login", {
    method: "POST",
    body: JSON.stringify({ username: "nobody", password: "x" }),
  });
  check(wrong.status === 401, "a wrong login is refused");
  await context.fetch("ui/api/login", {
    method: "POST",
    body: JSON.stringify({ username: "admin", password: "master" }),
  });
  check((await (await context.fetch("ui/api/login")).json()).session === true, "the login opens the page again");
  const fresh = await (await context.fetch("ui/api/limits", { method: "POST" })).json();
  check(fresh.checked > 0 && fresh.lanes[0].at > 0, "the limits check reads a fresh time");
  const seen = [];
  const stream = new context.EventSource("ui/api/requests/live");
  ["live", "start", "update", "first", "end"].forEach((kind) =>
    stream.addEventListener(kind, (event) => seen.push([kind, JSON.parse(event.data)])),
  );
  let endRead = null;
  stream.addEventListener("end", (event) => {
    const row = JSON.parse(event.data).row;
    // The page refreshes the table from this very call: the row must ride in its read.
    context.fetch("ui/api/requests?limit=50").then((answer) => answer.json()).then((rows) => {
      endRead = rows.some((item) => item.model === row.model && item.at === row.at);
    });
  });
  // The pace of the demo is slow: 2 replayed rows, the shortest path to a fallback chain,
  // take up to 17.4 s.
  await new Promise((done) => setTimeout(done, 20000));
  stream.close();
  check(endRead === true, "the end refresh reads the finished row");
  const kinds = seen.map(([kind]) => kind);
  check(kinds[0] === "live" && Array.isArray(seen[0][1]), "the stream starts with the live rows");
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
  // The status and the cards read the same state as the table.
  const liveStatus = await (await context.fetch("ui/api/status")).json();
  check(liveStatus.models === (await (await context.fetch("ui/api/models")).json()).length,
    "the status counts the catalog");
  check(liveStatus.sessions === new Set(grown.map((row) => row.session).filter(Boolean)).size,
    "the status counts the sessions of the table");
  const liveLimits = await (await context.fetch("ui/api/limits", { method: "POST" })).json();
  check(liveLimits.providers.find((card) => card.name === "cloudflare").items[0][1]
    !== "10K of 10K left", "the traffic moves the balance card");
  check(grown.every(shaped), "each row of the table carries the fields of the server");
  check(grown.some((row) => Number(row.fallbacks) > 0), "a fallback chain arrives");
  check(grown.some((row) => (row.attempts || []).some((a) => a.cooldown)), "a rate limit");
  const seven = (value) => typeof value === "string" && /^[0-9a-f]{7}$/.test(value);
  check(grown.filter((row) => row.session).every((row) => seven(row.session)),
    "each session reads like the server sends it");
  const live = seen.find(([kind]) => kind === "start")[1];
  check(live.id && live.path && live.ttft === null, "the start row of a live request");
  const updates = seen.filter(([kind]) => kind === "update");
  check(updates.length > 0 && updates.every(([, data]) => data.key === "master"),
    "the live row names a key");
  check(updates.every(([, data]) => !data.session || seven(data.session)),
    "the live session reads like the server sends it");
  // The clocks of a row in flight: TTFT counts first, then it freezes at the first token and
  // the stream clock counts from 0. A row holds the ages of the moment it sent them.
  const byId = new Map();
  for (const [kind, data] of seen) {
    if (!data || data.id == null) continue;
    if (!byId.has(data.id)) byId.set(data.id, []);
    byId.get(data.id).push([kind, data]);
  }
  const traffic = [...byId.values()];
  const aged = traffic.map((events) => events.filter(([, data]) => typeof data.age === "number"));
  check(aged.every((events) => events.every(([, data], i) =>
    data.age >= 0 && data.attempt_age >= 0 && data.attempt_age <= data.age + 0.001
    && (i === 0 || data.age >= events[i - 1][1].age))), "the clocks of a request only grow");
  const paired = traffic
    .filter((events) => events.some(([kind]) => kind === "end"))
    .map((events) => [
      (events.find(([kind]) => kind === "first") || [])[1],
      events.find(([kind]) => kind === "end")[1].row,
    ]);
  check(paired.length > 0 && paired.every(([token, row]) => token
    && token.ttft === token.attempt_age && token.ttft > 0
    && `${token.ttft.toFixed(3)}s` === row.ttft && row.seconds >= token.ttft),
    "TTFT freezes at the first token, and the stream time follows it");
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
  unknown = calls - set(READS) - set(SIMULATED)
  assert not unknown, unknown


def test_the_fixtures_hold_each_settings_row_of_the_page() -> None:
  """A settings group or key added to the page fails here, so the capture runs again."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  defaults = fixtures["settings"]["defaults"]
  text = APP_JS.read_text(encoding="utf-8")
  block = text[
    text.index("\nconst SETTINGS = [") : text.index(
      "\n// The options of each choice field."
    )
  ]
  missing, group = [], ""
  for line in block.split("\n"):
    name = re.match(r'\["([a-z_0-9-]+)", "', line.lstrip(" "))
    if not name or not line.startswith(" " * 2):
      continue
    if len(line) - len(line.lstrip(" ")) == 2:
      group = name.group(1)
      if group not in defaults:
        missing.append(group)
    elif group in defaults and name.group(1) not in defaults[group]:
      missing.append(f"{group}.{name.group(1)}")
  assert not missing, missing


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
  # The attempt fields carry the shapes of the server, so the page can render each chain.
  for row in requests:
    for attempt in row.get("attempts") or []:
      change = attempt.get("weight_change")
      if change is not None:
        assert isinstance(change.get("from"), (int, float)) and isinstance(
          change.get("to"), (int, float)
        ), ("a weight change is a pair of weights", change)
      cooldown = attempt.get("cooldown")
      if cooldown is not None:
        assert isinstance(cooldown["seconds"], (int, float)) and cooldown["reason"], (
          "a cooldown holds the seconds and the reason",
          cooldown,
        )
  # The demo names only the 2 client apps that daedalus supports.
  apps = {row["app"] for row in requests if row.get("app")}
  assert apps <= {"OWUI", "Kilo"}, apps
  # The max shape: each column of the table carries a value in 1 row at least.
  for field in ("app", "effort", "pool", "routed", "retry", "loop", "transition"):
    assert any(row.get(field) for row in requests), field
  assert any((row.get("tokens") or {}).get("estimate") for row in requests), (
    "an estimate"
  )
  cards = fixtures["limits"]["providers"]
  assert [card["name"] for card in cards] == [
    "openrouter",
    "kilo",
    "pollinations",
    "cloudflare",
  ], "each balance card of the page"
  assert all(len(item) == 3 for card in cards for item in card["items"]), (
    "the card items"
  )
  labels = {item[0] for card in cards for item in card["items"]}
  assert {
    "Free requests today",
    "Balance",
    "Requests left this hour",
    "Pollen",
  } <= labels, labels
  assert fixtures["limits"]["checked"], "the checked time"
  assert any(len(key["name"]) == 40 for key in fixtures["keys"]), (
    "a key name at its limit"
  )
  assert any(file.get("shadow") for file in fixtures["files"]), "a shadowed file"
  sessions = {row["session"] for row in requests if row.get("session")}
  assert sessions and all(re.fullmatch(r"[0-9a-f]{7}", value) for value in sessions), (
    sessions
  )
  named = (
    [row["id"] for row in rows]
    + [row["model"] for row in requests]
    + [key["name"] for key in fixtures["keys"]]
  )
  assert not [value for value in named if "demo" in value], (
    "the values carry real names"
  )


def test_the_demo_files_are_the_shipped_provider_files() -> None:
  """The Providers tab of the demo shows the shipped ymls verbatim, and nothing else."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  shipped = pages_demo.ROOT / "config" / "providers"
  names = [Path(row["path"]).name for row in fixtures["files"]]
  assert names == ["free.yml", "openrouter.yml", "pollinations.yml"], names
  for row in fixtures["files"]:
    assert row["text"] == (shipped / Path(row["path"]).name).read_text(
      encoding="utf-8"
    ), row["path"]
  assert len(fixtures["models"]) > 100, len(fixtures["models"])


def test_the_build_copies_the_page_and_loads_the_demo_first(tmp_path: Path) -> None:
  """`--out` serves the real UI, with the demo script before it."""
  out = pages_demo.build(tmp_path / "site", pages_demo.demo_version())
  page = (out / "index.html").read_text(encoding="utf-8")
  assert page.index("demo.js") < page.index("ui/app.js")
  assert f'src="demo.js?v={pages_demo.demo_version()}"' in page, "the demo script"
  assert f'src="ui/app.js?v={pages_demo.demo_version()}"' in page, (
    "the version rides on the script"
  )
  assert f'href="ui/style.css?v={pages_demo.demo_version()}"' in page, (
    "and on the style"
  )
  for name in ("app.js", "index.html", "style.css", "manifest.json", "logo.svg"):
    assert (out / "ui" / name).exists() or (out / name).exists(), name
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  fixtures["login"]["version"] = pages_demo.demo_version()
  fixtures["status"]["version"] = pages_demo.demo_version()
  assert json.dumps(fixtures) in (out / "demo.js").read_text(encoding="utf-8")
  assert 'const MARKER = "ui/api/";' in (out / "demo.js").read_text(encoding="utf-8"), (
    "the demo matches the call path"
  )


def test_the_demo_carries_the_version_of_the_build(tmp_path: Path) -> None:
  """A build stamps its version into the header, and every change bumps it."""
  out = pages_demo.build(tmp_path / "site", "demo.7")
  demo = (out / "demo.js").read_text(encoding="utf-8")
  assert demo.count('"version": "demo.7"') == 2, (
    "the header and the login hint carry it"
  )
  assert pages_demo.demo_version() != "", (
    "an environment with no version keeps a fallback"
  )
  os.environ["DEMO_VERSION"] = "demo.99"
  try:
    assert pages_demo.demo_version() == "demo.99", "the environment wins"
  finally:
    del os.environ["DEMO_VERSION"]


def test_the_live_demo_converts_a_live_row_promptly() -> None:
  """A live row finishes within about 2 s, so the table gains it without a long wait."""
  found = {
    name: (float(low), float(high))
    for name, low, high in re.findall(
      r"const (WAVE_MS|TTFT_S|STREAM_S) = \[([\d.]+), ([\d.]+)\];",
      pages_demo.DEMO_JS,
    )
  }
  assert sorted(found) == ["STREAM_S", "TTFT_S", "WAVE_MS"], found
  assert "const WAVE = 1;" in pages_demo.DEMO_JS, "1 request per wave"
  assert all(low < high for low, high in found.values()), found
  assert found["WAVE_MS"][0] >= 5000, found
  assert found["TTFT_S"][1] <= 1.0, found
  assert found["STREAM_S"][1] <= 1.5, found


def test_the_demo_answers_the_page_without_a_server(tmp_path: Path) -> None:
  """The script in a JavaScript runtime: the fixtures, a refused write and a live request."""
  out = pages_demo.build(tmp_path / "site", pages_demo.demo_version())
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
