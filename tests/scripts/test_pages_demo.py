"""Tests for the GitHub Pages build, fixtures, and simulated responses."""

import json
import os
import re
import subprocess
from pathlib import Path

import httpx
import pages_demo
import pytest
import yaml

from daedalus import __version__ as DAEDALUS_VERSION
from daedalus.routing.limits import header_rows

APP_JS = Path("daedalus/dashboard/ui/app.js")
# State-changing calls use page memory.
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
# Read calls use fixture data.
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

NODE_SETUP = """
const JSON_HEADERS = { "Content-Type": "application/json" };
const fs = require("fs");
const vm = require("vm");
const context = {
  location: { origin: "https://demo.test", href: "https://demo.test/" },
  Response, Headers, URL, clearTimeout, console, Promise, Math, JSON, Object, Array, performance,
  network: [], listeners: [], timers: [],
};
context.location.search = process.env.DEMO_QUERY || "";
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
const errorMessage = async (answer, structured = true) => {
  const error = (await answer.json()).error;
  check(typeof error.message === "string" && error.message.trim().length > 0, "nonblank error message");
  if (structured) check(error.code === answer.status && error.type === "invalid_request_error",
    "structured error fields");
};
"""

NODE_CHECK = (
  NODE_SETUP
  + """
(async () => {
  const beforeActions = JSON.stringify(await (await context.fetch("ui/api/models")).json());
  for (const contentType of [null, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]) {
    for (const [method, path] of [["POST", "reset"], ["POST", "logout"], ["POST", "catalog"],
      ["POST", "limits"], ["POST", "updates"], ["POST", "hooks/update"], ["PUT", "settings"],
      ["DELETE", "keys/missing"]]) {
      const headers = contentType === null ? {} : { "Content-Type": contentType };
      const answer = await context.fetch("ui/api/" + path, { method, headers });
      check(answer.status === 400, "reject non-JSON " + method + " " + path);
      const error = (await answer.json()).error;
      check(error.code === 400 && error.type === "invalid_request_error", "JSON boundary error fields");
      check(error.message.includes("application/json"), "name the required content type");
    }
  }
  check(JSON.stringify(await (await context.fetch("ui/api/models")).json()) === beforeActions,
    "rejected actions keep model state");
  check((await (await context.fetch("ui/api/login")).json()).session === true,
    "rejected logout keeps the session");
  const hookSettings = await (await context.fetch("ui/api/settings")).json();
  const targetHook = hookSettings.hook_rows[0];
  const deleteHook = (name) => context.fetch("ui/api/hooks/file", {
    method: "DELETE", headers: JSON_HEADERS, body: JSON.stringify({ name }),
  });
  const missingHook = await deleteHook(targetHook.title);
  check(missingHook.status === 404, "a hook title is not a path");
  const missingError = (await missingHook.json()).error;
  check(missingError.locale_key === "hook.error.file_missing_text", "missing hook locale key");
  check(missingError.locale_params.name === targetHook.title, "missing hook locale parameter");
  check(missingError.code === 404 && missingError.type === "invalid_request_error", "missing hook error fields");
  check(typeof missingError.message === "string" && missingError.message.length > 0, "missing hook message");
  check((await deleteHook(7)).status === 400, "a hook path must be text");
  check((await deleteHook(targetHook.path)).status === 200, "delete the hook path");
  const afterHook = await (await context.fetch("ui/api/settings")).json();
  check(afterHook.hook_rows.length === hookSettings.hook_rows.length - 1, "delete one hook row");
  check(!afterHook.hook_rows.some(row => row.path === targetHook.path), "the deleted hook leaves the list");
  check((await deleteHook(targetHook.path)).status === 404, "a repeated delete finds no file");
  const hookLeaf = targetHook.name.split("/").pop();
  const hookOrder = Object.values(afterHook.file.hooks.order || {}).flat();
  check(!hookOrder.some(name => name.split("/").pop() === hookLeaf), "delete clears the hook order");
  const status = await context.fetch("ui/api/status");
  check(status.status === 200, "status");
  check((await status.json()).healthy === true, "status body");
  const absolute = await context.fetch(new URL("/ui/api/status", context.location.origin).href);
  check(absolute.status === 200 && (await absolute.json()).healthy === true, "absolute API route");
  const notifications = await (await context.fetch("ui/api/notifications")).json();
  check(notifications.update.channel === "dev", "the update fixture uses the development channel");
  check(notifications.update.behind === 2 && notifications.update.update === true,
    "the fixture shows two main commits absent from this build");
  const checkedUpdate = await (await context.fetch("ui/api/updates", { method: "POST", headers: JSON_HEADERS })).json();
  check(checkedUpdate.behind === 2 && checkedUpdate.update === true, "a new check keeps the demo distance");
  check(notifications.resets.length === 1, "the reset fixture reaches Notifications");
  check(notifications.resets[0].kind === "neurons", "the reset fixture keeps its limit");
  check(notifications.limits.length === 1, "the one-time warning fixture reaches Notifications");
  check(Number.isFinite(notifications.limits[0].at), "the warning fixture keeps its event time");
  check(notifications.rebuilds.length === 1, "the rebuild fixture shares the notification feed");
  const first = await (await context.fetch("ui/api/requests?limit=10")).json();
  check(first.length === 0, "the Requests tab starts empty");
  const served = await (await context.fetch("ui/api/models")).json();
  console.log("models " + served.length);
  const all = await (await context.fetch("ui/api/requests")).json();
  check(all.length === 0, "the Overview starts empty too");
  const stray = await context.fetch("ui/api/nothing", { method: "PUT", headers: JSON_HEADERS });
  check(stray.status === 403, "a write with no home is refused");
  await errorMessage(stray, false);
  const missing = await context.fetch("ui/api/nothing");
  check(missing.status === 404, "an unknown call");
  const stateBefore = await (await context.fetch("ui/api/status")).json();
  const start = await context.fetch("ui/api/catalog", { method: "POST", headers: JSON_HEADERS });
  check(start.status === 202, "a rebuild starts");
  const running = await (await context.fetch("ui/api/status")).json();
  check(running.catalog.rebuilding === true, "the chip reads rebuilding");
  const busy = await context.fetch("ui/api/catalog", { method: "POST", headers: JSON_HEADERS });
  check(busy.status === 409, "a rebuild is refused while 1 runs");
  await errorMessage(busy);
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
    method: "PUT", headers: JSON_HEADERS,
    body: JSON.stringify({ changes: { personalization: { theme: "light" } } }),
  });
  check(save.status === 200, "a settings save lands");
  const held = await (await context.fetch("ui/api/settings")).json();
  check(held.file.personalization.theme === "light", "the page shows the saved value");
  check(held.text.includes("theme: light"), "the saved text holds it");
  // Clearing the only explicit field removes its empty group.
  const clearedTheme = await context.fetch("ui/api/settings", {
    method: "PUT", headers: JSON_HEADERS,
    body: JSON.stringify({ changes: { personalization: { theme: null } } }),
  });
  check(clearedTheme.status === 200, "a settings clear lands");
  const afterClear = await (await context.fetch("ui/api/settings")).json();
  check(afterClear.file.personalization === undefined, "the cleared key leaves the file");
  check(!afterClear.text.includes("theme"), "the cleared line leaves the text");
  const env = await (await context.fetch("ui/api/env", {
    method: "PUT", headers: JSON_HEADERS,
    body: JSON.stringify({ name: "CLOUDFLARE_ACCOUNT_ID", value: "abc12345" }),
  })).json();
  check(env.find((row) => row.name === "CLOUDFLARE_ACCOUNT_ID").state === "saved", "an env save lands");
  const made = await context.fetch("ui/api/keys", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ name: "extra-client" }),
  });
  check(made.status === 201, "a key is made");
  const key = await made.json();
  check(key.name === "extra-client" && key.key.startsWith("sk-"), "the key text");
  check((await (await context.fetch("ui/api/keys")).json()).some((row) => row.name === "extra-client"),
    "the key list shows it");
  check((await context.fetch("ui/api/keys/extra-client", { method: "DELETE", headers: JSON_HEADERS })).status === 204,
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
  check(models.length === served.length, "the model count stays unchanged");
  check(models.every((row) => !String(row.id).includes("*")), "no glob reaches the models page");
  const nonChat = models.filter((row) => row.mode !== "chat");
  check(nonChat.length > 0 && nonChat.every((row) => row.tier === null),
    "non-chat models have no routing tier");
  check(models.some((row) => row.mode === "chat" && row.tier),
    "chat models keep their routing tiers");
  const readOnly = async (path, body, method) => {
    const answer = await context.fetch("ui/api/" + path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });
    check(answer.status === 403, `the refusal of ${path}`);
    await errorMessage(answer);
  };
  // The demo takes provider edits in page memory: YAML overlays the cloud blocks, and the form
  // writes only changed whole blocks. A refresh brings the empty local main back.
  const put = async (path, body) => {
    const answer = await context.fetch("ui/api/" + path, {
      method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body),
    });
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
  check(edit.status === 422 && edit.body.error.code === 422
    && edit.body.error.type === "invalid_request_error", "the bad yaml is refused");
  check(typeof edit.body.error.message === "string" && edit.body.error.message.trim(), "explain invalid YAML");
  edit = await put("files", { path: main.path, text: "- 1\\n- 2" });
  check(edit.status === 422 && edit.body.error.code === 422
    && edit.body.error.type === "invalid_request_error", "a list is not provider blocks");
  check(typeof edit.body.error.message === "string" && edit.body.error.message.trim(), "explain invalid shape");
  list = await (await context.fetch("ui/api/files")).json();
  check(list.find((row) => row.path === main.path).text === rewrote.text, "the refusals keep the text");
  await readOnly("files", { name: "extra" }, "POST");
  await readOnly("files", { path: main.path }, "DELETE");
  check((await (await context.fetch("ui/api/files")).json()).length === 3,
    "the files stay as shipped");
  check((await context.fetch("ui/api/reset", {
    method: "POST", headers: JSON_HEADERS,
  })).status === 200, "a reset lands");
  const cleared = await (await context.fetch("ui/api/models")).json();
  check(cleared.every((row) => row.cooldown === null && row.weight === 1),
    "the reset clears the weights");
  // Each write carries the checks of the server: the names and the session.
  const refuse = async (path, body, status, method = "PUT") => {
    const answer = await context.fetch("ui/api/" + path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });
    check(answer.status === status, `the refusal of ${path} (${status})`);
    await errorMessage(answer);
  };
  await refuse("settings", { changes: { personalization: { grid: 1 } } }, 422);
  await refuse("keys", { name: "x".repeat(41) }, 400, "POST");
  await refuse("keys", { name: "openwebui" }, 400, "POST");
  await context.fetch("ui/api/logout", { method: "POST", headers: JSON_HEADERS });
  check((await (await context.fetch("ui/api/login")).json()).session === false, "a logout ends the session");
  const wrong = await context.fetch("ui/api/login", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ username: "nobody", password: "x" }),
  });
  check(wrong.status === 401, "a wrong login is refused");
  await context.fetch("ui/api/login", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ username: "admin", password: "master" }),
  });
  check((await (await context.fetch("ui/api/login")).json()).session === true, "the login opens the page again");
  const fresh = await (await context.fetch("ui/api/limits", { method: "POST", headers: JSON_HEADERS })).json();
  check(fresh.checked > 0 && fresh.lanes[0].at > 0, "the limits check reads a fresh time");
  const balanceKinds = new Set(['credit', 'usage', 'free_requests', 'balance', 'pollen', 'neurons', 'hourly_requests']);
  for (const item of fresh.providers.flatMap(provider => provider.items)) {
    check(balanceKinds.has(item.kind), "the refreshed card has a semantic kind");
    if (item.kind === 'hourly_requests') {
      check(typeof item.value === 'string' && /^[0-9]+$/.test(item.value), "the hourly count has exact digits");
      check(typeof item.limit === 'string' && /^[0-9]+$/.test(item.limit), "the hourly limit has exact digits");
    } else {
      check(Number.isFinite(item.value), "the refreshed card has a numeric value");
      check(item.limit === null || Number.isFinite(item.limit), "the refreshed card has a numeric limit");
    }
    check(item.share === null || Number.isFinite(item.share) && item.share >= 0 && item.share <= 1,
      "the refreshed card has a bounded remaining fraction");
  }
  const limitUnits = new Set(fresh.lanes.flatMap((lane) =>
    lane.rows.map((row) => `${row.kind}/${row.span}`)));
  check(limitUnits.has("requests/day"), "the demo keeps an RPD row");
  check(limitUnits.has("requests/minute"), "the demo keeps an RPM row");
  check(limitUnits.has("tokens/minute"), "the demo keeps a TPM row");
  const seen = [];
  // A scripted run: the harness shrinks the waits, names each wave, and freezes the draws so
  // the escalation template with the cooldown and the weight change always serves.
  context.DEMO_SCALE = 0.005;
  context.DEMO_SCENARIOS = ["race", "chat", "escalate", "agentic", "rag", "kilo", "stray",
    "escalate", "escalate", "escalate", "escalate", "escalate", "escalate", "escalate",
    "escalate"];
  context.Math.random = () => 0.25;
  const stream = new context.EventSource("ui/api/requests/live");
  ["live", "start", "update", "first", "end"].forEach((kind) =>
    stream.addEventListener(kind, (event) => seen.push([kind, JSON.parse(event.data), performance.now()])),
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
      && rows.some((row) => row.race === "drawn" && row.transition?.reason === "rce"
        && row.attempts.length === 2 && row.attempts[0].result === "error"
        && row.attempts[1].race === "won")
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
    "pool", "race", "retry", "routed", "seconds", "session", "status", "stream", "tokens",
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
  const fixturePools = JSON.parse(fs.readFileSync("expected-pools.json", "utf8"));
  const poolOf = new Map(fixturePools.filter(pool => pool.name !== "daedalus/auto")
    .flatMap(pool => pool.members.map(member => [member.id, pool.name.replace("daedalus/", "")])));
  check(autos.length > 0 && autos.every(row => row.pool === poolOf.get(row.via)),
    "an auto row names the pool of its served model");
  // The status and the cards read the same state as the table.
  const liveStatus = await (await context.fetch("ui/api/status")).json();
  check(liveStatus.models === (await (await context.fetch("ui/api/models")).json()).length,
    "the status counts the catalog");
  check(liveStatus.sessions === new Set(grown.map((row) => row.session).filter(Boolean)).size,
    "the status counts the sessions of the table");
  const liveLimits = await (await context.fetch("ui/api/limits", { method: "POST", headers: JSON_HEADERS })).json();
  // The provider that the waves reached has a card, and its traffic moved the number.
  const reached = new Set(grown.map((row) => String(row.via).split("/")[0]));
  const card = liveLimits.providers.find((item) => reached.has(item.name)
    && item.items.some((value) => typeof value.share === "number" && value.share < 1));
  check(!!card, "the traffic moves the balance card");
  check(grown.every(shaped), "each row of the table carries the fields of the server");
  check(grown.every(row => !row.transition || row.transition.to_model === row.via),
    "the transition names the served model");
  check(grown.every(row => row.attempts.filter(attempt => attempt.result === "answered")
    .every(attempt => attempt.model === row.via)), "the successful attempt names the served model");
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
  check(moved.every(row => row.weight > 0 && row.weight <= 1), "weights stay within the server bounds");
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
  for (const [kind, data, clock] of seen) {
    if (!data || data.id == null) continue;
    if (!byId.has(data.id)) byId.set(data.id, []);
    byId.get(data.id).push([kind, data, clock]);
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
  for (const events of traffic) {
    const start = events.find(([kind]) => kind === "start");
    const finish = events.find(([kind]) => kind === "end");
    if (!finish) continue;
    const row = finish[1].row;
    const elapsed = (finish[2] - start[2]) / 1000;
    check(Math.abs(row.seconds - elapsed) < 0.05, "the total uses measured elapsed time");
    const first = events.find(([kind]) => kind === "first")[1];
    check(row.attempts.every(attempt => attempt.seconds === null ||
      attempt.seconds >= 0 && attempt.seconds <= row.seconds), "attempts fit the request total");
    check(row.attempts.filter(attempt => attempt.result === "answered")
      .every(attempt => attempt.seconds === first.ttft), "the successful attempt uses its first token time");
    const duration = row.race === "drawn"
      ? Math.max(...row.attempts.map(attempt => attempt.seconds || 0))
      : row.attempts.reduce((sum, attempt) => sum + (attempt.seconds || 0), 0);
    check(duration <= first.age + 0.001, "the chain fits before the first token");
  }
  check(context.network.length === 0, "no call reaches the network");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
"""
)


def demo_read(out: Path, *paths: str) -> dict:
  script = (
    NODE_SETUP
    + """
(async () => {
  const rows = await Promise.all(process.argv.slice(1).map(async path => {
    const answer = await context.fetch("ui/api/" + path);
    if (answer.status !== 200) throw new Error(path + ": " + answer.status);
    return [path, await answer.json()];
  }));
  console.log(JSON.stringify(Object.fromEntries(rows)));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script, *paths],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
  return json.loads(result.stdout)


def test_the_fixtures_cover_each_call_of_the_page() -> None:
  """The fixtures cover known reads and static page calls."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  assert sorted(fixtures) == sorted(READS), sorted(fixtures)
  calls = set(re.findall(r'call\("([a-z/_-]*)"', APP_JS.read_text(encoding="utf-8")))
  unknown = calls - set(READS) - set(SIMULATED)
  assert not unknown, unknown


def test_the_fixtures_hold_each_settings_row_of_the_page() -> None:
  """Built fixture defaults cover each settings group and key used by the page."""
  fixtures = pages_demo.demo_fixtures("demo.test")
  defaults = fixtures["settings"]["defaults"]
  text = APP_JS.read_text(encoding="utf-8")
  block = text[
    text.index("\nconst SETTINGS = [") : text.index(
      "\n// The options of each choice field."
    )
  ]
  missing, group = [], ""
  for line in block.split("\n"):
    name = re.match(r'\["([a-z_0-9-]+)", ', line.lstrip(" "))
    if not name or not line.startswith(" " * 2):
      continue
    if len(line) - len(line.lstrip(" ")) == 2:
      group = name.group(1)
      if group not in defaults:
        missing.append(group)
    elif group in defaults and name.group(1) not in defaults[group]:
      missing.append(f"{group}.{name.group(1)}")
  assert not missing, missing
  assert "hide_headers" not in defaults["personalization"]


def test_the_fixtures_carry_the_edge_cases() -> None:
  """Fixture data includes long names, cooldowns, and request fields."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  rows = fixtures["models"]
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
  # Attempt fields match the server's response shapes.
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
  # The fixtures name only OWUI and Kilo clients.
  apps = {row["app"] for row in requests if row.get("app")}
  assert apps <= {"OWUI", "Kilo"}, apps
  # Each listed request field has a value in at least one row.
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
  items = [item for card in cards for item in card["items"]]
  assert all(set(item) == {"kind", "value", "limit", "share"} for item in items)
  kinds = {item["kind"] for item in items}
  assert {"free_requests", "balance", "hourly_requests", "pollen", "neurons"} <= kinds
  for item in items:
    if item["kind"] == "hourly_requests":
      assert isinstance(item["value"], str) and item["value"].isdecimal()
      assert isinstance(item["limit"], str) and item["limit"].isdecimal()
    else:
      assert isinstance(item["value"], (int, float))
      assert item["limit"] is None or isinstance(item["limit"], (int, float))
  assert all(item["share"] is None or 0 <= item["share"] <= 1 for item in items)
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
  """Provider fixtures inherit main blocks and keep local main text empty."""
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
  captured = json.loads(pages_demo.MODELS_FILE.read_text(encoding="utf-8"))
  assert {row["id"] for row in fixtures["models"]} == {row["id"] for row in captured}


@pytest.mark.parametrize(
  "query, delay", [("", 0), ("?slow=800", 800), ("?other=1&slow=25", 25)]
)
def test_the_demo_can_slow_its_answers_for_a_reviewer(
  tmp_path: Path, query: str, delay: int
) -> None:
  out = pages_demo.build(tmp_path / "site", "demo.9")
  script = (
    NODE_SETUP
    + """
(async () => {
  const timers = [];
  context.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return 0; };
  const delay = Number(process.argv[1]);
  for (const path of process.argv.slice(2)) {
    let settled = false;
    const answer = context.fetch("ui/api/" + path);
    answer.then(() => { settled = true; });
    await Promise.resolve();
    if (delay) {
      check(!settled, "response waits for its timer");
      check(timers.length === 1 && timers[0].ms === delay, "query sets response delay");
      timers.shift().fn();
    } else {
      check(timers.length === 0, "no delay without the query");
    }
    check((await answer).status === 200, "response completes after delay");
  }
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script, str(delay), *READS],
    cwd=out,
    capture_output=True,
    text=True,
    env={**os.environ, "DEMO_QUERY": query},
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_auto_fixture_contains_only_chat_models() -> None:
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  auto = next(row for row in fixtures["pools"] if row["name"] == "daedalus/auto")
  members = [item["id"] for item in auto["members"]]
  mode = {row["id"]: row["mode"] for row in fixtures["models"]}
  assert members and all(mode.get(name) == "chat" for name in members)


def test_demo_pool_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  fixtures = pages_demo.demo_fixtures("demo.pools")
  template = fixtures["models"][0]
  fixtures["models"] = [
    {**template, "id": f"fixture/{name}", "mode": mode, "tier": None}
    for name, mode in (
      ("alpha-one", "chat"),
      ("alpha-two", "chat"),
      ("beta", "chat"),
      ("image", "image_generation"),
    )
  ]
  fixtures["files"] = [
    {
      "path": "config/providers/fixture.yml",
      "blocks": {
        "fixture": {
          "models": {"*": {}},
          "tier": {"TIER-A": ["alpha-*"], "TIER-B": ["beta"]},
        }
      },
    }
  ]
  fixtures["settings"]["defaults"]["personalization"] = {
    "tier-a": "fixture-strong",
    "tier-b": "fixture-other",
    "images": "fixture-images",
  }
  fixtures["settings"]["file"]["personalization"] = {}
  fixtures["pools"] = [
    {
      "name": "daedalus/auto",
      "members": [{"id": "fixture/alpha-two"}, {"id": "fixture/beta"}],
    }
  ]
  monkeypatch.setattr(pages_demo, "demo_fixtures", lambda version: fixtures)
  out = pages_demo.build(tmp_path / "site", "demo.pools")
  script = (
    NODE_SETUP
    + """
(async () => {
  const pools = await (await context.fetch("ui/api/pools")).json();
  const members = (name) => pools.find(pool => pool.name === "daedalus/" + name).members;
  check(members("fixture-strong").map(row => row.id).join(",") === "fixture/alpha-one,fixture/alpha-two",
    "tier pattern membership");
  check(members("fixture-strong").every(row => row.tier === "TIER-A"), "member tiers");
  check(members("fixture-other").map(row => row.id).join(",") === "fixture/beta", "other tier membership");
  check(members("fixture-images").map(row => row.id).join(",") === "fixture/image", "image membership");
  check(members("auto").map(row => row.id).join(",") === "fixture/alpha-one,fixture/alpha-two,fixture/beta",
    "auto catalog membership");
  context.EventSource.prototype.loop = async () => {};
  context.Math = Object.create(Math);
  const feed = new context.EventSource("ui/api/events");
  for (const [random, expected] of [[0, "fixture/alpha-two"], [0.999999, "fixture/beta"]]) {
    context.Math.random = () => random;
    const calls = [];
    feed.call = async (spec) => calls.push(spec);
    await feed.chat();
    check(calls.length === 3 && calls.slice(1).every(spec =>
      spec.model === "daedalus/auto" && spec.via === expected), "auto traffic uses supplied members");
  }
  feed.close();
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_demo_keeps_every_row_of_the_snapshot(tmp_path: Path) -> None:
  """The demo models response retains each captured model ID."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  ids = [row["id"] for row in fixtures["models"]]
  assert len(ids) == len(set(ids)), "each row appears once"
  out = pages_demo.build(tmp_path / "site", "demo.9")
  models = demo_read(out, "models")["models"]
  assert sorted(row["id"] for row in models) == sorted(ids)


def test_the_demo_states_its_source_and_the_hook_versions(tmp_path: Path) -> None:
  """Demo settings contain the shipped hook source and versions."""
  assert pages_demo.DEMO_SOURCE["repo"] == "nemoe7/daedalus"
  fixtures = pages_demo.demo_fixtures("demo.test")
  hooks_file = fixtures["settings"]["file"]["hooks"]
  assert hooks_file["sources"] == [pages_demo.DEMO_SOURCE], hooks_file["sources"]
  assert hooks_file["order"] == pages_demo.demo_hook_order()
  assert not any(surface in hooks_file for surface in pages_demo.DEMO_HOOK_SURFACES), (
    "the demo exercises metadata-only hooks"
  )
  shown_hooks = yaml.safe_load(fixtures["settings"]["text"])["hooks"]
  assert shown_hooks["sources"] == hooks_file["sources"]
  assert shown_hooks["order"] == hooks_file["order"]
  rows = {row["name"]: row for row in fixtures["settings"]["hook_rows"]}
  # The copy-only example hook is absent from the rows.
  shipped = {
    path.name for path in Path("hooks").glob("*.py") if path.name != "example.py"
  }
  assert set(rows) == shipped, rows
  for name, row in rows.items():
    assert row["version"], f"no version for {name}"
  metadata, problem = pages_demo.hooks.meta_file(Path("hooks/served_model.py"))
  assert problem is None
  assert metadata is not None
  assert rows["served_model.py"]["title"] == metadata["title"]
  assert rows["owui_auto_reasoning_effort.py"]["runs"] == []
  assert rows["served_model.py"]["runs"] == []
  out = pages_demo.build(tmp_path / "site", "demo.test")
  shown = demo_read(out, "settings")["settings"]
  assert shown["file"]["hooks"]["sources"] == [pages_demo.DEMO_SOURCE]
  assert {row["name"]: row["version"] for row in shown["hook_rows"]} == {
    name: row["version"] for name, row in rows.items()
  }


def test_a_capture_without_an_update_block_still_builds(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Fixture generation adds version metadata when the update block is absent."""
  fixtures = json.loads(pages_demo.FIXTURES.read_text(encoding="utf-8"))
  fixtures["notifications"]["update"] = None
  made = tmp_path / "pages_fixtures.json"
  made.write_text(json.dumps(fixtures), encoding="utf-8")
  monkeypatch.setattr(pages_demo, "FIXTURES", made)
  stamped = pages_demo.demo_fixtures("demo.none")
  assert stamped["notifications"]["update"]["current"] == "demo.none"


def test_the_build_copies_the_page_and_loads_the_demo_first(tmp_path: Path) -> None:
  """Built assets include UI, with demo and locale before app."""
  out = pages_demo.build(tmp_path / "site", pages_demo.demo_version())
  page = (out / "index.html").read_text(encoding="utf-8")
  assert page.index("demo.js") < page.index("ui/en.js") < page.index("ui/app.js")
  assert f'src="demo.js?v={pages_demo.demo_version()}"' in page, "the demo script"
  assert f'src="ui/en.js?v={pages_demo.demo_version()}"' in page, "the locale catalog"
  assert f'src="ui/app.js?v={pages_demo.demo_version()}"' in page, (
    "the version rides on the script"
  )
  assert f'href="ui/style.css?v={pages_demo.demo_version()}"' in page, (
    "and on the style"
  )
  for name in (
    "app.js",
    "en.js",
    "index.html",
    "style.css",
    "manifest.json",
    "logo.svg",
  ):
    assert (out / "ui" / name).exists() or (out / name).exists(), name
  fixtures = pages_demo.demo_fixtures(pages_demo.demo_version())
  answers = demo_read(out, *READS)
  for name in ("env", "files", "keys", "provider-defaults", "provider-keys", "hooks"):
    assert answers[name] == fixtures[name], name
  for name in ("file", "defaults"):
    assert answers["settings"][name] == fixtures["settings"][name], name
  assert answers["requests"] == []
  assert answers["status"]["models"] == len(answers["models"])


def test_the_demo_carries_the_version_of_the_build(tmp_path: Path) -> None:
  """Build versions reach status, login, and update responses, with an environment override."""
  out = pages_demo.build(tmp_path / "site", "demo.7")
  script = (
    NODE_SETUP
    + """
(async () => {
  for (const path of ["status", "login"]) {
    const body = await (await context.fetch("ui/api/" + path)).json();
    check(body.version === "demo.7", "build version in " + path);
  }
  const notifications = await (await context.fetch("ui/api/notifications")).json();
  check(notifications.update.current === "demo.7", "update version");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
  assert pages_demo.demo_version() != "", (
    "an environment with no version keeps a fallback"
  )
  os.environ["DEMO_VERSION"] = "demo.99"
  try:
    assert pages_demo.demo_version() == "demo.99", "the environment wins"
  finally:
    del os.environ["DEMO_VERSION"]


def test_demo_timing_and_event_contract(tmp_path: Path) -> None:
  out = pages_demo.build(tmp_path / "site", "demo.timing")
  script = (
    NODE_SETUP
    + """
(async () => {
  let now = 0;
  context.performance = { now: () => now };
  context.Math = Object.create(Math);
  context.setTimeout = (fn, ms) => { now += ms; fn(); return 0; };
  const prototype = context.EventSource.prototype;
  const loop = prototype.loop;
  const makeFeed = () => {
    prototype.loop = async () => {};
    try { return new context.EventSource("ui/api/events"); }
    finally { prototype.loop = loop; }
  };
  const near = (actual, expected) => Math.abs(actual - expected) < 1e-8;
  const age = (elapsed) => Math.max(1, Math.round(elapsed)) / 1000;
  for (const r of [0, 1 / 3, 0.599, 0.6, 0.699, 0.7, 0.849, 0.85, 0.899, 0.9, 0.999999]) {
    context.Math.random = () => r;
    const plan = makeFeed();
    const calls = [];
    plan.call = async (spec) => calls.push(spec);
    await plan.chat();
    check(calls.length === 3 && calls[0].path === "/v1/embeddings"
      && !calls[0].stream && !calls[1].stream && calls[2].stream, "chat phases");
    check(near(calls[0].ttft, 0.1 + r * 0.4), "embedding TTFT");
    check(near(calls[1].ttft, 0.4 + r * 1.1), "title TTFT");
    const chat = r < 0.7 ? 20 + r * 10 : r < 0.9 ? r * 20 : 30 + r * 30;
    check(near(calls[2].ttft, chat), "chat TTFT distribution");
    const wave = makeFeed();
    let waves = 0;
    wave.scenario = async () => { if (++waves === 2) wave.close(); };
    const waveStart = now;
    await wave.loop();
    check(waves === 2 && near(now - waveStart, 1 + 6000 + r * 12000), "wave interval");
    for (const stream of [false, true]) {
      const feed = makeFeed();
      const events = [];
      let finishedRead;
      for (const kind of ["start", "update", "first", "end"]) {
        feed.addEventListener(kind, event => {
          events.push({ kind, data: JSON.parse(event.data), at: now });
          if (kind === "end") finishedRead = context.fetch("ui/api/requests?limit=1");
        });
      }
      const start = now;
      await feed.call({ model: "fixture/clock", via: "fixture/clock", stream, ttft: 0.0026 });
      check(events.map(event => event.kind).join(",") === "start,update,first,end", "event order");
      const attempt = events[1].at;
      for (const event of events.slice(0, 3)) {
        check(event.data.age === age(event.at - start), "whole millisecond age");
        check(event.data.attempt_age === age(event.at - (event.kind === "start" ? start : attempt)),
          "whole millisecond attempt age");
      }
      check(events[0].data.age === 0.001 && events[1].data.attempt_age === 0.001, "positive clock floor");
      const first = events[2];
      const end = events[3];
      const duration = !stream ? 0.2 + r * 1.3
        : r < 0.6 ? 15 + r * 30 : r < 0.85 ? 0.5 + r * 14.5 : 45 + r * 75;
      check(near(end.at - first.at, Math.round(duration * 1000)), "stream duration distribution");
      check(first.data.ttft === first.data.attempt_age, "first token freezes TTFT");
      check(Number.parseFloat(end.data.row.ttft) === first.data.ttft, "finished TTFT");
      check(end.data.row.seconds === age(end.at - start), "finished elapsed time");
      const rows = await (await finishedRead).json();
      check(JSON.stringify(rows[0]) === JSON.stringify(end.data.row), "row exists when end arrives");
      feed.close();
    }
  }
  context.performance.now = () => { const value = now; now += 1.25; return value; };
  const clockFeed = makeFeed();
  clockFeed.addEventListener("start", event => {
    const data = JSON.parse(event.data);
    check(data.age === data.attempt_age, "both initial ages use the same clock sample");
  });
  await clockFeed.call({ model: "fixture/clock", via: "fixture/clock", ttft: 0 });
  clockFeed.close();
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_demo_answers_the_page_without_a_server(tmp_path: Path) -> None:
  """The JavaScript runtime covers catalog reads, rejected writes, and live requests."""
  version = pages_demo.demo_version()
  out = pages_demo.build(tmp_path / "site", version)
  (out / "expected-pools.json").write_text(
    json.dumps(pages_demo.demo_fixtures(version)["pools"]), encoding="utf-8"
  )
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


@pytest.mark.parametrize("mode", ["changes", "text"])
@pytest.mark.parametrize(
  "disabled, enabled",
  [
    (["a/repo/shared.py"], [False, True]),
    (["a/repo/shared"], [False, True]),
    (["b/repo/shared.py"], [True, False]),
    (["shared.py"], [False, False]),
    (["shared"], [False, False]),
    ([], [True, True]),
    (None, [True, True]),
    ("", [True, True]),
    (["a/" + "b" * 100], [True, True]),
    ("shared.py", None),
    ([None], None),
    (["../shared.py"], None),
    (["a//shared.py"], None),
    (["a\\shared.py"], None),
    (["a/shared.py\n"], None),
    (["a/" + "b" * 101], None),
    (["a" * 81], None),
  ],
)
def test_demo_hook_identities(
  tmp_path: Path, monkeypatch, mode, disabled, enabled
) -> None:
  """The demo validates hook identities and returns saved enabled states."""
  fixtures = pages_demo.demo_fixtures("demo.9")
  sample = fixtures["settings"]["hook_rows"][0]
  fixtures["settings"]["hook_rows"] = [
    {**sample, "name": f"{source}/repo/shared.py", "enabled": True, "problem": ""}
    for source in ("a", "b")
  ] + [{**sample, "name": "broken.py", "enabled": False, "problem": "invalid"}]
  fixtures["settings"]["file"].setdefault("hooks", {})["disabled"] = []
  monkeypatch.setattr(pages_demo, "demo_fixtures", lambda version: fixtures)
  out = pages_demo.build(tmp_path / "site", "demo.9")
  values = {"hooks": {"disabled": disabled}}
  body = (
    {"text": "hooks:\n  disabled: " + json.dumps(disabled) + "\n"}
    if mode == "text"
    else {"changes": values}
  )
  (out / "case.json").write_text(json.dumps({"body": body, "enabled": enabled}))
  script = (
    NODE_SETUP
    + """
(async () => {
  const scenario = JSON.parse(fs.readFileSync("case.json", "utf8"));
  const read = async () => (await context.fetch("ui/api/settings")).json();
  const save = (body) => context.fetch("ui/api/settings", {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body),
  });
  const before = await read();
  const answer = await save(scenario.body);
  check(answer.status === (scenario.enabled === null ? 422 : 200), "save status");
  const after = await read();
  if (scenario.enabled === null) {
    check(JSON.stringify(after) === JSON.stringify(before), "invalid save keeps state");
  } else {
    check(JSON.stringify(after.hook_rows.map(row => row.enabled)) ===
      JSON.stringify([...scenario.enabled, false]), "source and legacy switch states");
    const reset = await save({ changes: { hooks: { disabled: [] } } });
    check(reset.status === 200, "clear disabled hooks");
    check(JSON.stringify((await read()).hook_rows.map(row => row.enabled)) ===
      JSON.stringify([true, true, false]), "clear restores valid hooks only");
  }
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_demo_rejected_provider_form_keeps_state(tmp_path: Path) -> None:
  out = pages_demo.build(tmp_path / "site", "demo.test")
  script = (
    NODE_SETUP
    + """
(async () => {
  const read = async path => (await context.fetch("ui/api/" + path)).json();
  const files = await read("files");
  for (const file of [files.find(row => row.main), files.find(row => !row.main)]) {
    const provider = file.main ? "cloudflare" : Object.keys(file.blocks)[0];
    const block = {
      api_key: "new-api-key", account_id: "new-account", client_keys: { first: "new-client-one" },
      hooks: [{ "on-answer": "hooks/provider.py" }],
      models: { "fixture/*": { hooks: [{ "on-answer": "hooks/model.py" }] } },
    };
    const before = await Promise.all(["files", "env", "models"].map(read));
    for (const [invalid, status] of [[{ account_id: "two words" }, 400], [{ models: [] }, 422],
      [{ client_keys: { first: "new-client-one", second: "two words" } }, 400]]) {
      const response = await context.fetch("ui/api/providers", {
        method: "PUT", headers: JSON_HEADERS,
        body: JSON.stringify({ path: file.path, blocks: { [provider]: { ...block, ...invalid } } }),
      });
      check(response.status === status, "reject invalid provider form");
      const after = await Promise.all(["files", "env", "models"].map(read));
      check(JSON.stringify(after) === JSON.stringify(before), "rejected form keeps all state");
    }
    const response = await context.fetch("ui/api/providers", {
      method: "PUT", headers: JSON_HEADERS,
      body: JSON.stringify({ path: file.path, blocks: { [provider]: block } }),
    });
    check(response.status === 200, "valid provider form saves");
    const saved = (await read("files")).find(row => row.path === file.path).blocks[provider];
    check(JSON.stringify(saved.hooks) === JSON.stringify(block.hooks), "provider hook surface saves");
    check(JSON.stringify(saved.models) === JSON.stringify(block.models), "model hook surface saves");
    const text = (await response.json()).text;
    check(["new-api-key", "new-account", "new-client-one"].every(secret => !text.includes(secret)),
      "saved form masks keys");
    const name = provider.toUpperCase().replace(/[^A-Z0-9_]/g, "_") + "_API_KEY";
    const env = await read("env");
    check(env.find(row => row.name === name).has_saved === true, "valid form saves key metadata");
    check(env.find(row => row.name === name + "_FIRST").has_saved === true, "valid form saves client metadata");
  }
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_demo_save_name_shapes(tmp_path: Path) -> None:
  out = pages_demo.build(tmp_path / "site", "demo.test")
  script = (
    NODE_SETUP
    + """
(async () => {
  const read = async path => (await context.fetch("ui/api/" + path)).json();
  const file = (await read("files")).find(row => row.main);
  const env = (await read("env"))[0];
  const before = await Promise.all(["files", "env", "models"].map(read));
  for (const [route, field, name, payload] of [
    ["providers", "path", file.path, { blocks: file.blocks }],
    ["files", "path", file.path, { text: file.text }],
    ["env", "name", env.name, { value: "new-key" }],
  ]) {
    const put = value => context.fetch("ui/api/" + route, {
      method: "PUT", headers: JSON_HEADERS,
      body: JSON.stringify({ ...payload, [field]: value }),
    });
    const control = await put("unknown");
    check(control.status === 400, "reject unknown name");
    const error = JSON.stringify(await control.json());
    for (const value of [[], {}, [name], null, 1, false]) {
      const response = await put(value);
      check(response.status === 400, "reject invalid name shape");
      check(JSON.stringify(await response.json()) === error, "keep error shape");
    }
  }
  const after = await Promise.all(["files", "env", "models"].map(read));
  check(JSON.stringify(after) === JSON.stringify(before), "rejected names keep state");
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_the_capture_builds_the_demo_state() -> None:
  """An in-process capture returns the dashboard version, model IDs, and requests."""
  assert pytest.importorskip("fastapi")
  found = pages_demo.capture()
  assert found["status"]["version"] == DAEDALUS_VERSION, found["status"]["version"]
  captured = json.loads(pages_demo.MODELS_FILE.read_text(encoding="utf-8"))
  assert sorted(row["id"] for row in found["models"]) == sorted(
    row["id"] for row in captured
  )
  assert found["requests"], "the demo requests"


def test_race_demo_shape() -> None:
  """The demo has a race winner, a failed first attempt, and a transition reason."""
  shapes = [row for row in pages_demo.DEMO_SHAPES if row.get("race") == "drawn"]
  fixtures = json.loads(pages_demo.FIXTURES.read_text())
  rows = [row for row in fixtures["requests"] if row.get("race") == "drawn"]
  assert shapes and rows
  for row in [*shapes, *rows]:
    assert (
      row["seconds"]
      >= float(row["ttft"].removesuffix("s"))
      >= row["attempts"][-1]["seconds"]
    )
    transition = row["transition"]
    assert transition["reason"] == "rce"
    assert transition["from_model"] != transition["to_model"]
    attempts = row["attempts"]
    assert attempts[0]["model"] == transition["from_model"]
    assert attempts[0]["result"] == "error" and attempts[0]["error"]
    assert attempts[-1]["model"] == transition["to_model"] == row["via"]
    assert attempts[-1]["result"] == "answered" and attempts[-1]["race"] == "won"


def test_demo_keeps_valid_limits_when_a_header_group_is_invalid() -> None:
  answer = pages_demo.answer(
    httpx.Request(
      "POST", "https://groq.test/v1/chat/completions", json={"model": "synthetic-limit"}
    )
  )
  assert answer.status_code == 200
  assert answer.headers["x-ratelimit-limit-tokens-hour"] == "Infinity"
  rows = header_rows("groq", answer.headers, 0)
  assert [row["kind"] for row in rows] == ["requests", "tokens"]
  assert rows[0]["limit"] == 14400
  json.dumps(rows, allow_nan=False)


def test_demo_request_state_is_consistent() -> None:
  fixtures = json.loads(pages_demo.FIXTURES.read_text())
  for row in [*pages_demo.DEMO_SHAPES, *fixtures["requests"]]:
    if transition := row.get("transition"):
      assert transition["to_model"] == row.get("via")
    for attempt in row.get("attempts", []):
      if attempt["result"] == "answered":
        assert attempt["model"] == row["via"]
      if change := attempt.get("weight_change"):
        assert 0 < change["from"] <= 1
        assert 0 < change["to"] <= 1


@pytest.mark.parametrize("scenario", ["inline_sources", "block_sources", "limits"])
def test_demo_settings_save_contract(tmp_path: Path, monkeypatch, scenario) -> None:
  fixtures = pages_demo.demo_fixtures("demo.test")
  settings = fixtures["settings"]
  settings["defaults"]["limits"].update(shortest=10, longest=1000)
  changes = {
    "limits": {"shortest": 20, "longest": 100},
    "hooks": {
      "sources": [
        {"repo": "sample/tools", "path": "hooks", "ref": "topic", "auto_update": True},
        {
          "repo": "other/hooks",
          "path": "plugins",
          "ref": "stable",
          "auto_update": False,
        },
      ]
    },
  }
  settings["text"] = pages_demo.settings.update_text(settings["text"], changes)
  if scenario == "block_sources":
    settings["text"] = re.sub(
      r"^( *)- repo: (.+)$", r"\1-\n\1  repo: \2", settings["text"], flags=re.MULTILINE
    )
  settings["file"] = yaml.safe_load(settings["text"])
  monkeypatch.setattr(pages_demo, "demo_fixtures", lambda version: fixtures)
  out = pages_demo.build(tmp_path / "site", "demo.test")
  (out / "case.json").write_text(json.dumps({"scenario": scenario}))
  script = (
    NODE_SETUP
    + """
(async () => {
  const { scenario } = JSON.parse(fs.readFileSync("case.json", "utf8"));
  const read = async () => (await context.fetch("ui/api/settings")).json();
  const save = body => context.fetch("ui/api/settings", {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body),
  });
  const before = await read();
  if (scenario !== "limits") {
    const response = await save({ text: before.text });
    check(response.status === 200, "save generated source maps");
    const after = await read();
    check(JSON.stringify(after.file) === JSON.stringify(before.file), "keep parsed settings");
    check(after.text === before.text, "keep saved YAML");
  } else {
    for (const limits of [{ shortest: 500, longest: 1 }, { shortest: 101 }, { longest: 19 },
      { shortest: null, longest: 5 }, { longest: null, shortest: 1001 }]) {
      const response = await save({ changes: { personalization: { theme: "light" }, limits } });
      check(response.status === 422, "reject combined limit bounds");
      const error = (await response.json()).error;
      check(error.code === 422 && error.type === "invalid_request_error", "keep error fields");
      check(typeof error.message === "string" && error.message.length > 0, "explain invalid bounds");
      check(JSON.stringify(await read()) === JSON.stringify(before), "rejected save keeps all settings");
    }
    for (const limits of [{ shortest: 75, longest: 75 }, { longest: 90 }]) {
      check((await save({ changes: { limits } })).status === 200, "save valid combined bounds");
      const after = await read();
      check(Object.entries(limits).every(([key, value]) => after.file.limits[key] === value),
        "keep saved limit values");
    }
    check((await save({ changes: { limits: { shortest: null, longest: null } } })).status === 200,
      "restore default bounds");
    const limits = (await read()).file.limits || {};
    check(!("shortest" in limits) && !("longest" in limits), "clear both overrides");
  }
  check(context.network.length === 0, "no network calls");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
  )
  result = subprocess.run(
    ["node", "-e", script],
    cwd=out,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )
  assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("source", ["shapes", "fixtures"])
def test_demo_template_timing(source) -> None:
  rows = (
    [
      {"seconds": 0.005, "ttft": "0.001s", **shape}
      for shape in pages_demo.DEMO_SHAPES
      if "attempts" in shape
    ]
    if source == "shapes"
    else json.loads(pages_demo.FIXTURES.read_text())["requests"]
  )
  for row in rows:
    if not row.get("attempts") or "seconds" not in row:
      continue
    durations = [attempt["seconds"] or 0 for attempt in row["attempts"]]
    assert all(0 <= seconds <= row["seconds"] for seconds in durations)
    elapsed = max(durations) if row.get("race") == "drawn" else sum(durations)
    assert elapsed <= row["seconds"] + 0.001
    if row.get("ttft"):
      ttft = float(row["ttft"].removesuffix("s"))
      assert 0 <= ttft <= row["seconds"]
      assert all(
        abs(attempt["seconds"] - ttft) <= 0.001
        for attempt in row["attempts"]
        if attempt["result"] == "answered"
      )
