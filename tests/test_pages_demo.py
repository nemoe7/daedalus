"""The GitHub Pages demo: the build, the fixtures, and the script that answers the page."""

import json
import os
import re
import subprocess
from pathlib import Path

import pages_demo
import pytest
import yaml

from daedalus import __version__ as DAEDALUS_VERSION

APP_JS = Path("daedalus/dashboard/ui/app.js")
# The calls that change state. The demo holds each of them in the page memory.
SIMULATED = (
  "catalog",
  "env",
  "files",
  "hooks/file",
  "hooks/scan",
  "hooks/update",
  "keys",
  "keys/",
  "limits",
  "login",
  "logout",
  "providers",
  "reset",
  "settings",
  "updates",
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
  "notifications",
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
  Response, URL, clearTimeout, console, Promise, Math, JSON, Object, Array, performance,
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
  const notifications = await (await context.fetch("ui/api/notifications")).json();
  check(notifications.resets.length === 1, "the reset fixture reaches Notifications");
  check(notifications.resets[0].kind === "neurons", "the reset fixture keeps its limit");
  const first = await (await context.fetch("ui/api/requests?limit=10")).json();
  check(first.length === 0, "the Requests tab starts empty");
  const served = await (await context.fetch("ui/api/models")).json();
  console.log("models " + served.length);
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
  // The empty local main shows the effective cloud blocks, and the demo takes edits in page memory.
  const files = await (await context.fetch("ui/api/files")).json();
  check(files.length === 3
    && files.every((row) => String(row.path).startsWith("config/providers/")),
    "the file list holds the provider files");
  const main = files.find((row) => row.main);
  check(main.path === "config/providers/free.yml" && main.text === ""
    && main.blocks.cloudflare && main.blocks.openrouter,
    "the empty local main shows the cloud blocks");
  check(!files.some((row) => row.shadow),
    "a sibling provider file does not suppress its cloud main block");
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
  // The demo takes provider edits in page memory: YAML overlays the cloud blocks, and the form
  // writes only changed whole blocks. A refresh brings the empty local main back.
  const put = async (path, body) => {
    const answer = await context.fetch("ui/api/" + path, { method: "PUT", body: JSON.stringify(body) });
    return { status: answer.status, body: await answer.json() };
  };
  const added = main.text + "demoprovider:\\n  models:\\n    demo-edit-check:\\n";
  let edit = await put("files", { path: main.path, text: added });
  check(edit.status === 200 && edit.body.ok === true && edit.body.text === added, "the yaml save lands");
  let list = await (await context.fetch("ui/api/files")).json();
  const edited = list.find((row) => row.path === main.path);
  check(edited.blocks.demoprovider && edited.blocks.demoprovider.models["demo-edit-check"],
    "the blocks re-read the text");
  check((await (await context.fetch("ui/api/models")).json()).some((row) => row.id === "demoprovider/demo-edit-check"),
    "the models page follows the text");
  const forms = { ...edited.blocks };
  delete forms.demoprovider;
  forms.cloudflare = { ...forms.cloudflare, order: 3 };
  edit = await put("providers", { path: main.path, blocks: forms });
  check(edit.status === 200 && edit.body.ok === true, "the form save lands");
  list = await (await context.fetch("ui/api/files")).json();
  const rewrote = list.find((row) => row.path === main.path);
  check(rewrote.text.includes("order: 3") && !rewrote.text.includes("demoprovider")
    && !rewrote.text.includes("\\nkilo:") && !rewrote.text.includes("\\nopenrouter:"),
    "the form writes only the changed whole cloud block");
  check(!(await (await context.fetch("ui/api/models")).json()).some((row) => row.id === "demoprovider/demo-edit-check"),
    "the models page follows the form");
  edit = await put("files", { path: main.path, text: rewrote.text });
  check(edit.status === 200 && edit.body.ok === true, "the regenerated text re-reads");
  list = await (await context.fetch("ui/api/files")).json();
  const round = list.find((row) => row.path === main.path);
  check(round.blocks.cloudflare.order === 3 && round.blocks.demoprovider === undefined,
    "the round trip keeps the blocks");
  edit = await put("files", { path: main.path, text: "\\tcloudflare: 2" });
  check(edit.status === 422 && String(edit.body.error.message).includes("cannot start any token"),
    "the bad yaml is refused");
  edit = await put("files", { path: main.path, text: "- 1\\n- 2" });
  check(edit.status === 422 && edit.body.error.message.includes("must hold provider blocks"),
    "a list is not provider blocks");
  list = await (await context.fetch("ui/api/files")).json();
  check(list.find((row) => row.path === main.path).text === rewrote.text, "the refusals keep the text");
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
  // A scripted run: the harness shrinks the waits, names each wave, and freezes the draws so
  // the escalation template with the cooldown and the weight change always serves.
  context.DEMO_SCALE = 0.005;
  context.DEMO_SCENARIOS = ["chat", "escalate", "agentic", "rag", "kilo", "stray",
    "escalate", "escalate", "escalate", "escalate", "escalate", "escalate", "escalate",
    "escalate"];
  context.Math.random = () => 0.25;
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
  // The scripted waves land as fast as the machine allows: the harness waits for the rows
  // its checks read, up to 40 s, so a loaded runner does not fail the run.
  const ready = async () => {
    const rows = await (await context.fetch("ui/api/requests?limit=500")).json();
    const sessions = new Map();
    for (const row of rows) {
      if (!row.session) continue;
      if (!sessions.has(row.session)) sessions.set(row.session, []);
      sessions.get(row.session).push(row);
    }
    const ordered = [...sessions.values()].some((group) => {
      const sorted = [...group].sort((a, b) => a.at - b.at);
      return sorted.length >= 3 && sorted[0].model === "mistral/mistral-embed-2312"
        && sorted[1].stream === false && sorted[2].stream === true;
    });
    const climbing = [...sessions.values()].some((group) => {
      const efforts = [...group].sort((a, b) => a.at - b.at).map((row) => row.effort);
      return efforts.includes("none") && efforts.includes("low");
    });
    const reasons = new Set(rows.filter((row) => row.transition)
      .map((row) => row.transition.reason));
    return ordered && climbing
      && rows.some((row) => row.app === null)
      && rows.some((row) => row.routed === "deinos")
      && rows.some((row) => row.loop === "2")
      && rows.some((row) => (row.attempts || []).some((a) => a.cooldown))
      && ["err", "ctx", "lmt", "hlt", "rnd", "cls", "esc", "rce", "rt1"]
        .every((code) => reasons.has(code));
  };
  let landed = false;
  for (let i = 0; i < 80 && !landed; i++) {
    landed = await ready();
    if (!landed) await new Promise((done) => setTimeout(done, 500));
  }
  stream.close();
  check(landed, "the scripted waves all land");
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
  // An auto row names the pool that answered, as the live server records it.
  const autos = grown.filter((row) => String(row.model).startsWith("daedalus/auto"));
  check(autos.length > 0 && autos.every((row) =>
    ["sophos", "deinos", "koinos", "moros"].includes(row.pool)),
    "an auto row names the pool that answered");
  // The status and the cards read the same state as the table.
  const liveStatus = await (await context.fetch("ui/api/status")).json();
  check(liveStatus.models === (await (await context.fetch("ui/api/models")).json()).length,
    "the status counts the catalog");
  check(liveStatus.sessions === new Set(grown.map((row) => row.session).filter(Boolean)).size,
    "the status counts the sessions of the table");
  const liveLimits = await (await context.fetch("ui/api/limits", { method: "POST" })).json();
  // The provider that the waves reached has a card, and its traffic moved the number.
  const reached = new Set(grown.map((row) => String(row.via).split("/")[0]));
  const card = liveLimits.providers.find((item) => reached.has(item.name)
    && item.items.some((value) => typeof value[2] === "number" && value[2] < 1));
  check(!!card, "the traffic moves the balance card");
  check(grown.every(shaped), "each row of the table carries the fields of the server");
  check(grown.some((row) => Number(row.fallbacks) > 0), "a fallback chain arrives");
  check(grown.some((row) => (row.attempts || []).some((a) => a.cooldown)), "a rate limit");
  // The rows of a scenario share a session, in the order the apps send them.
  const bySession = new Map();
  for (const row of grown) {
    if (!row.session) continue;
    if (!bySession.has(row.session)) bySession.set(row.session, []);
    bySession.get(row.session).push(row);
  }
  const ordered = [...bySession.values()].some((rows) => {
    const sorted = [...rows].sort((a, b) => a.at - b.at);
    return sorted.length >= 3 && sorted[0].model === "mistral/mistral-embed-2312"
      && sorted[1].stream === false && sorted[2].stream === true;
  });
  check(ordered, "an owui chat runs embed, title, chat in order");
  const climbing = [...bySession.values()].some((rows) => {
    const efforts = [...rows].sort((a, b) => a.at - b.at).map((row) => row.effort);
    return efforts.includes("none") && efforts.includes("low");
  });
  check(climbing, "an agent workflow climbs the effort ladder");
  check(grown.some((row) => row.app === null), "a stray client arrives");
  check(grown.some((row) => row.routed === "deinos"), "a row names the pool of the last model");
  check(grown.some((row) => row.loop === "2"), "a tool loop stops a chain");
  const reasons = new Set(grown.filter((row) => row.transition).map((row) => row.transition.reason));
  check(["err", "ctx", "lmt", "hlt", "rnd", "cls", "esc", "rce", "rt1"]
    .every((code) => reasons.has(code)), "each legend code arrives");
  // The cards read the same traffic: the weights and the cooldowns move as the rows land.
  const moved = await (await context.fetch("ui/api/models")).json();
  check(moved.some((row) => row.weight !== 1), "the traffic moves the weights");
  check(moved.some((row) => row.cooldown !== null), "the traffic sets a cooldown");
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
    data.age >= 0 && data.attempt_age >= 0 && data.attempt_age <= data.age
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
  assert not any(file.get("shadow") for file in fixtures["files"]), (
    "a sibling provider file does not shadow an inherited main block"
  )
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


def test_the_demo_main_form_uses_cloud_blocks_with_an_empty_local_file() -> None:
  """The Providers demo shows the shipped main as inherited and keeps its local text empty."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  shipped = pages_demo.ROOT / "config" / "providers"
  names = [Path(row["path"]).name for row in fixtures["files"]]
  assert names == ["free.yml", "openrouter.yml", "pollinations.yml"], names
  main, *singles = fixtures["files"]
  assert main["text"] == "", "the local main file starts empty"
  assert main["blocks"] == yaml.safe_load(
    (shipped / "free.yml").read_text(encoding="utf-8")
  ), "the form shows the cloud main blocks"
  for row in singles:
    assert row["text"] == (shipped / Path(row["path"]).name).read_text(
      encoding="utf-8"
    ), row["path"]
  assert len(fixtures["models"]) > 100, len(fixtures["models"])


def test_the_demo_can_slow_its_answers_for_a_reviewer(tmp_path: Path) -> None:
  """A reviewer sets `?slow=800` to watch the spinner and the skeleton rows of the page."""
  out = pages_demo.build(tmp_path / "site", "demo.9")
  script = (out / "demo.js").read_text(encoding="utf-8")
  assert "const SLOW =" in script and "slow=([0-9]+)" in script, (
    "the knob reads the address bar"
  )
  assert "SLOW ? wait(SLOW) : Promise.resolve()" in script, "every fixture answer waits"


def test_the_demo_serves_auto_only_from_the_members_of_the_auto_pool(
  tmp_path: Path,
) -> None:
  """The auto waves draw the members the auto pool holds, so no stray model answers."""
  out = pages_demo.build(tmp_path / "site", "demo.9")
  script = (out / "demo.js").read_text(encoding="utf-8")
  assert 'const TEXT = MEMBERS["daedalus/auto"]' in script, (
    "the auto pool picks the chat models"
  )
  assert "MEDIA_PATH" not in script, "no name list decides the chat models"
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  auto = next(row for row in fixtures["pools"] if row["name"] == "daedalus/auto")
  members = [item["id"] for item in auto["members"]]
  mode = {row["id"]: row["mode"] for row in fixtures["models"]}
  assert members and all(mode.get(name) == "chat" for name in members), (
    "every member of the auto pool is a chat model"
  )
  assert not any("leonardo/lucid-origin" in name for name in members), (
    "no image model rides the auto pool"
  )


def test_the_demo_keeps_every_row_of_the_snapshot(tmp_path: Path) -> None:
  """The demo shows the captured catalog as it is: each snapshot row reaches the page."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  ids = [row["id"] for row in fixtures["models"]]
  assert len(ids) == len(set(ids)), "each row is listed once"
  out = pages_demo.build(tmp_path / "site", "demo.9")
  script = (out / "demo.js").read_text(encoding="utf-8")
  for model_id in ids:
    assert f'"id": "{model_id}"' in script, f"{model_id} reaches the page"


def test_the_demo_states_its_source_and_the_hook_versions(tmp_path: Path) -> None:
  """The Sources card starts with the repo that ships the hooks, and the table shows its versions."""
  assert pages_demo.DEMO_SOURCE["repo"] == "nemoe7/daedalus"
  fixtures = pages_demo.demo_fixtures("demo.test")
  hooks_file = fixtures["settings"]["file"]["hooks"]
  assert hooks_file["sources"] == [pages_demo.DEMO_SOURCE], hooks_file["sources"]
  assert hooks_file["order"] == pages_demo.demo_hook_order()
  assert not any(surface in hooks_file for surface in pages_demo.DEMO_HOOK_SURFACES), (
    "the demo exercises metadata-only hooks"
  )
  assert "repo: nemoe7/daedalus" in fixtures["settings"]["text"], (
    "the YAML card shows it"
  )
  assert "order:" in fixtures["settings"]["text"], "the YAML card shows the order"
  rows = {row["name"]: row for row in fixtures["settings"]["hook_rows"]}
  # The example of the base repo ships for copy, so the table leaves it out.
  shipped = {
    path.name for path in Path("hooks").glob("*.py") if path.name != "example.py"
  }
  assert set(rows) == shipped, rows
  for name, row in rows.items():
    assert row["version"], f"no version for {name}"
  assert rows["served_model.py"]["title"] == "Served model"
  assert rows["owui_auto_reasoning_effort.py"]["runs"] == []
  assert rows["served_model.py"]["runs"] == []
  out = pages_demo.build(tmp_path / "site", "demo.test")
  script = (out / "demo.js").read_text(encoding="utf-8")
  assert '"repo": "nemoe7/daedalus"' in script, "the source reaches the page"
  assert f'"version": "{rows["served_model.py"]["version"]}"' in script, (
    "the version of a hook reaches the page"
  )


def test_a_capture_without_an_update_block_still_builds(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A fresh capture holds no update block, and the build stamps its version into it."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  fixtures["notifications"]["update"] = None
  made = tmp_path / "pages_fixtures.json"
  made.write_text(json.dumps(fixtures), encoding="utf-8")
  monkeypatch.setattr(pages_demo, "FIXTURES", made)
  stamped = pages_demo.demo_fixtures("demo.none")
  assert stamped["notifications"]["update"]["current"] == "demo.none"


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
  fixtures = pages_demo.demo_fixtures(pages_demo.demo_version())
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


def test_the_live_demo_lands_a_row_before_the_end_event() -> None:
  """The row joins the table state before the end event, and the spreads are the real ones."""
  js = pages_demo.DEMO_JS
  block = js[js.index("// 1 request, as the server lives it") :]
  assert block.index("keep(done);") < block.index('this.send("end"'), (
    "the row lands 1st"
  )
  assert "return r < 0.7 ? gap(20, 30) : r < 0.9 ? gap(0, 20) : gap(30, 60);" in js, (
    "the TTFT"
  )
  assert "r < 0.6 ? gap(15, 45) : r < 0.85 ? gap(0.5, 15) : gap(45, 120)" in js, (
    "the stream spread lands near 30 s, near 0, and up to 120 s"
  )
  assert "const WAVE_MS = [6000, 18000];" in js, "the gap between the waves"


def test_the_demo_clocks_count_in_whole_milliseconds() -> None:
  """The clocks count in whole milliseconds from 1 read of the clock, so a first token always ages above zero."""
  js = pages_demo.DEMO_JS
  block = js[
    js.index("const CLOCKS = new Map()") : js.index(
      "const SEEDED = DEMO_FIXTURES.requests.map"
    )
  ]
  assert "Math.max(1, Math.round(now - clock.at))" in block, "the age floors at 1 ms"
  assert "Math.max(1, Math.round(now - clock.attempt))" in block, (
    "the attempt age floors at 1 ms"
  )
  assert "(now - clock.at) / 1000" not in block, (
    "no fractions of a millisecond in the ages"
  )
  reads = block.count("performance.now()")
  assert reads == 3, "began, attempted, and the 1 read of ages"


def test_the_demo_answers_the_page_without_a_server(tmp_path: Path) -> None:
  """The script in a JavaScript runtime: the catalog count, a refused write and a live request."""
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
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  assert f"models {len(fixtures['models'])}\n" in built.stdout, (
    f"the page serves each row of the snapshot: {built.stdout}"
  )


def test_the_capture_builds_the_demo_state() -> None:
  """The capture runs the dashboard in this process, so the fixtures can be made again."""
  assert pytest.importorskip("fastapi")
  found = pages_demo.capture()
  assert found["status"]["version"] == DAEDALUS_VERSION, found["status"]["version"]
  assert len(found["models"]) >= 10, len(found["models"])
  assert found["requests"], "the demo requests"
