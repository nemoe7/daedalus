import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import store as model_store
from daedalus.routing import penalties, router
from daedalus.server import api, stream, upstream
from daedalus.store import keys

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
LOCAL = "test-local-key-0002"

FAILING: set[str] = set()


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host in FAILING:
    return httpx.Response(500, json={"error": "down"})
  message = {"role": "assistant", "content": request.url.host}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_weights(folder: Path) -> None:
  now, picks = [0.0], [0.0]
  store = penalties.Penalties(
    lambda: folder / "w.sqlite3", clock=lambda: now[0], pick=lambda: picks[0]
  )
  assert store.weights(["a"]) == {"a": 1.0}, "all models start at 1"
  assert store.record_change("a", penalties.FAULT) == (1.0, 0.5)
  assert store.record("a", penalties.FAULT) == 0.25
  assert store.record("a", penalties.SUCCESS) == 0.375
  assert store.record("b", penalties.SUCCESS) == 1.0, "1 at most"
  assert store.record("b", penalties.SLOW) == 0.75, "a slow success"
  now[0] = 3600
  assert abs(store.weights(["a"])["a"] - 0.4545) < 1e-9, "x1.212 after 1 hour"
  now[0] = 3600 * 30
  assert store.weights(["a"])["a"] == 1.0, "back to 1"
  # A restart: a new store on the same file counts the hours of the downtime.
  store.record("down", penalties.FAULT)
  now[0] += 3 * 3600
  restarted = penalties.Penalties(lambda: folder / "w.sqlite3", clock=lambda: now[0])
  assert abs(restarted.weights(["down"])["down"] - 0.5 * 1.212**3) < 1e-9, "catch up"
  for _ in range(2000):
    store.record("f", penalties.FAULT)
  assert store.weights(["f"])["f"] == penalties.FLOOR, "the floor"
  database = sqlite3.connect(folder / "w.sqlite3")
  with database:
    database.execute("UPDATE weights SET weight = 0 WHERE model = 'f'")
  database.close()
  assert store.weights(["f"])["f"] == penalties.FLOOR, "an old weight of 0"
  assert store.record("f", penalties.SUCCESS) == penalties.FLOOR * 1.5, "it recovers"
  now[0] = 0
  store.clear()
  store.record("a", penalties.FAULT)
  groups = [["a", "b", "c"], ["d", "e"]]
  store.record("d", penalties.FAULT)
  assert store.order(groups) == ["a", "b", "c", "e", "d"], (
    "a draw at 0 takes the first model"
  )
  picks[0] = 0.99
  assert store.order(groups)[0] == "c", "a draw near 1 takes the last model"
  picks[0] = 0.45
  assert store.order(groups)[0] == "b", "the weights set the draw: a has 0.5 of 2.5"
  picks[0] = 0.1
  assert store.order(groups)[0] == "a"
  assert store.order([["x", "y"], ["x"]]) == ["x", "y"], "a model shows once"
  assert store.order([[], ["z"]]) == ["z"], "an empty tier"


def test_pins(folder: Path) -> None:
  now = [0.0]
  store = penalties.Penalties(lambda: folder / "p.sqlite3", clock=lambda: now[0])
  store.pick = lambda: 0.0
  groups = [["a/1", "b/1"], ["c/1"]]
  assert store.pin("k", "pool", "c/1") == "new"
  order = store.order(groups, "k", "pool")
  assert order == ["a/1", "b/1", "c/1"], "a lower-tier session model does not go first"
  store.pin("k", "pool", "b/1")
  assert store.last_pin("k") == ("pool", "b/1")
  store.pin("k", "other-slot", "z/1")
  assert store.last_pin("k") == ("other-slot", "z/1")
  assert store.last_pin("other") is None
  store.pick = lambda: 0.2
  assert store.order(groups, "k", "pool") == ["b/1", "a/1", "c/1"], "the session model"
  assert store.order(groups, "other", "pool")[0] == "a/1", (
    "each key has its own session"
  )
  assert store.order(groups, "k", "other")[0] == "a/1", "each slot has its own session"
  store.pick = lambda: 0.14
  assert store.order(groups, "k", "pool")[0] == "a/1", "a/1 gets 15% of the draws"
  assert store.order([["x/1"], ["a/1", "b/1"]], "k", "pool") == ["x/1", "b/1", "a/1"], (
    "after the first model, the session model goes first in its tier"
  )
  store.pin("k", "pool", "a/1")
  for tier in (["a/1", "b/1"], ["a/1", "b/1", "d/1", "e/1", "f/1"]):
    store.pick = lambda: 0.84
    assert store.order([tier], "k", "pool")[0] == "a/1", "85% for the session model"
    store.pick = lambda: 0.86
    assert store.order([tier], "k", "pool")[0] == "b/1", (
      "the share ignores the tier size"
    )
  store.pin("k", "pool", "b/1")
  store.enabled = False
  assert store.order(groups, "k", "pool")[0] == "b/1", "no draw without weights"
  store.enabled = True
  store.pin("k", "pool", "c/1")
  assert store.pin("k", "pool", "c/1") == "hit"
  assert store.order([["a/1"]], "k", "pool") == ["a/1"], "a pin not in the chain"
  assert store.unpin("k", "pool", "a/1") is False, "only the pinned model"
  assert store.unpin("k", "pool", "c/1") is True
  assert store.pinned("k", "pool") is None
  store.pin("k", "pool", "b/1")
  now[0] = penalties.IDLE_SECONDS + 1
  assert store.pinned("k", "pool") is None, "a pin expires after the idle time"


def test_transition_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
  config = {
    "p": {
      "api_key": "k",
      "tier": {
        "TIER-A": ["a", "b"],
        "TIER-B": ["c"],
        "TIER-D": ["d"],
      },
    }
  }
  raw = [["p/a", "p/b"], ["p/c"], ["p/d"]]
  previous = {"pool": "sophos", "model": "p/a"}
  messages = [{"role": "user", "content": "hello"}]
  monkeypatch.setattr(api.PENALTIES, "enabled", True)
  monkeypatch.setattr(api, "KEYWORDS", None)
  monkeypatch.setattr(api.router, "required_tier", lambda _: 1)

  def reason(
    candidate: str,
    *,
    old: dict[str, str] = previous,
    turn: object = None,
    code: str | None = None,
    user_messages: list = messages,
    raw_groups: list[list[str]] = raw,
    sized: list[list[str]] | None = None,
    cooled: list[list[str]] | None = None,
    paced: list[list[str]] | None = None,
  ) -> str | None:
    sized = raw_groups if sized is None else sized
    cooled = sized if cooled is None else cooled
    paced = cooled if paced is None else paced
    return api.initial_transition_reason(
      config,
      old,
      candidate,
      turn,
      code,
      user_messages,
      raw_groups,
      sized,
      cooled,
      paced,
      False,
    )

  assert reason("p/b") == "rnd"
  assert reason("p/b", sized=[["p/b"], ["p/c"], ["p/d"]]) == "ctx"
  assert reason("p/b", cooled=[["p/b"], ["p/c"], ["p/d"]]) == "lmt"
  assert reason("p/c", raw_groups=[["p/c"], ["p/d"]]) == "hlt"
  low = {"pool": "moros", "model": "p/d"}
  monkeypatch.setattr(api.router, "required_tier", lambda _: 3)
  assert reason("p/c", old=low) == "cls"
  monkeypatch.setattr(api, "KEYWORDS", re.compile("bigger"))
  assert (
    reason("p/c", old=low, user_messages=[{"role": "user", "content": "bigger"}])
    == "esc"
  )
  assert reason("p/c", old=low, turn=SimpleNamespace(count=1), code="rt1") == "rt1"
  assert reason("p/c", old=low, turn=SimpleNamespace(count=1)) is None
  monkeypatch.setattr(api, "KEYWORDS", None)
  monkeypatch.setattr(api.router, "required_tier", lambda _: 1)
  assert reason("p/c", old=low) is None


def test_rebuild(folder: Path) -> None:
  database = folder / "models.sqlite3"
  store = penalties.Penalties(lambda: database)
  model_store.write_store([], database)
  store.record("a", penalties.FAULT)
  store.pin("k", "pool", "a")
  model_store.write_store([{"id": "p/x"}], database)
  assert store.weights(["a"])["a"] < 1 and store.pinned("k", "pool") == "a", "kept"


def test_highest(folder: Path) -> None:
  now = [0.0]
  store = penalties.Penalties(lambda: folder / "t.sqlite3", clock=lambda: now[0])
  assert store.highest("k", 3) == 3 and store.highest("k", 1) == 3, "no way down"
  assert store.highest("k", 4) == 4 and store.highest("other", 1) == 1, "per key"
  now[0] = 3599.0
  assert store.highest("k", 1) == 4, "each request resets the idle time"
  now[0] = 7200.0
  assert store.highest("k", 2) == 2, "an idle conversation starts again"
  store.clear()
  assert store.highest("other", 1) == 1, "clear removes the tiers"


def test_session_key() -> None:
  first = [{"role": "system", "content": "s"}, {"role": "user", "content": "a"}]
  later = [
    *first,
    {"role": "assistant", "content": "b"},
    {"role": "user", "content": "c"},
  ]
  other = [{"role": "system", "content": "s"}, {"role": "user", "content": "z"}]
  key = api.session_key("t", first)
  assert key == api.session_key("t", later), "one conversation keeps its key"
  assert key != api.session_key("t", other), "each first user message has its own key"
  assert key != api.session_key("u", first), "each token has its own key"


def test_slots() -> None:
  body = {"messages": [{"role": "user", "content": "hi"}]}
  _, slot = api.chain("daedalus/auto", body, {})
  assert slot.startswith("daedalus/auto:TIER-"), "auto pins per tier"
  assert api.routed_pool(slot) in {"moros", "koinos", "deinos", "sophos"}
  assert api.routed_pool("daedalus/auto:TIER-A") == "sophos"
  assert api.chain("daedalus/praktos", body, {}) is None, "no praktos"
  assert api.chain("daedalus/sophos", body, {})[1] == "daedalus/sophos"
  assert api.chain("x/y", body, {"x": {}}) == ([["x/y"]], None), "no pin for one model"


def test_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abc"
  }
  original = api.get_config, api.chain
  affinity = api.PENALTIES.enabled, api.PENALTIES.change_on_draw, api.PENALTIES.stay
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": (
    [["a/1", "b/1"], ["c/1"]],
    "daedalus/deinos",
  )
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  api.PENALTIES.clear()
  client = TestClient(api.app, headers=AUTH)

  keys.add(model_store.MODELS_DB, "second", LOCAL)

  def ask(token: str = MASTER, pick: float = 0.5) -> str:
    api.PENALTIES.pick = lambda: pick
    body = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "x"}]}
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["choices"][0]["message"]["content"]

  try:
    FAILING.add("a.test")
    assert ask(pick=0.0) == "b.test", "the first model that answers"
    assert api.PENALTIES.weights(["a/1"])["a/1"] < 0.51, "a fault lowers the weight"
    FAILING.clear()
    assert ask() == "b.test", "the session model keeps the turn"
    assert ask(LOCAL, pick=0.4) == "b.test", "a/1 has a lower weight for all keys"
    FAILING.add("b.test")
    assert ask() == "a.test", "a failed session model moves to the next model"
    FAILING.clear()
    assert ask() == "a.test", "the new session model"

    api.PENALTIES.clear()
    api.PENALTIES.enabled = True
    api.PENALTIES.stay = 0.0
    api.PENALTIES.change_on_draw = False
    assert ask(pick=0.0) == "a.test", "the initial pin"
    key = api.session_key(MASTER, [{"role": "user", "content": "x"}])
    slot = "daedalus/deinos"
    assert api.PENALTIES.pinned(key, slot) == "a/1"
    assert ask(pick=0.99) == "b.test", "a weighted draw still serves the new model"
    assert api.PENALTIES.pinned(key, slot) == "a/1", "the old eligible pin stays"
    api.PENALTIES.change_on_draw = True
    assert ask(pick=0.99) == "b.test"
    assert api.PENALTIES.pinned(key, slot) == "b/1", "the default replaces the pin"
  finally:
    api.get_config, api.chain = original
    api.PENALTIES.enabled, api.PENALTIES.change_on_draw, api.PENALTIES.stay = affinity
    upstream.set_client(None)
    api.PENALTIES.clear()


def test_ttft() -> None:
  role = {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
  text = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
  tool = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0}]}}]}
  assert not stream.has_content(json.dumps(role)), (
    "a role chunk does not stop the clock"
  )
  assert stream.has_content(json.dumps(text)) and stream.has_content(json.dumps(tool))
  assert stream.has_content("[DONE]")
  config = {"a": {"api_key": "k", "api_base": "https://a.test/v1"}}
  original = api.get_config, api.chain, api.SLOW_SECONDS
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": ([["a/1"]], "daedalus/deinos")
  frames = [role, text, "[DONE]"]
  sse = "".join(
    f"data: {json.dumps(item) if item != '[DONE]' else item}\n\n" for item in frames
  )
  transport = httpx.MockTransport(lambda request: httpx.Response(200, text=sse))
  upstream.set_client(httpx.AsyncClient(transport=transport))
  client = TestClient(api.app, headers=AUTH)
  body = {
    "model": "daedalus/deinos",
    "stream": True,
    "messages": [{"role": "user", "content": "x"}],
  }
  key = api.session_key(MASTER, body["messages"])
  try:
    for limit, expected in ((60.0, 1.0), (0.0, 0.75)):
      api.PENALTIES.clear()
      api.SLOW_SECONDS = limit
      api.PENALTIES.pin(key, "daedalus/deinos", "a/1")
      response = client.post("/v1/chat/completions", json=body)
      assert response.status_code == 200 and '"hi"' in response.text, response.text
      weight = api.PENALTIES.weights(["a/1"])["a/1"]
      assert abs(weight - expected) < 1e-3, (limit, weight)
      pinned = api.PENALTIES.pinned(key, "daedalus/deinos")
      assert pinned == (None if limit == 0.0 else "a/1"), (
        "a slow success removes the pin"
      )
  finally:
    api.get_config, api.chain, api.SLOW_SECONDS = original
    upstream.set_client(None)
    api.PENALTIES.clear()


def test_session_tier() -> None:
  seen: list[str] = []
  tiers = [3, 1, 1, 1]
  original = router.required_tier, router.chain_groups, model_store.read_models
  router.required_tier = lambda text: seen.append(text) or tiers.pop(0)
  router.chain_groups = lambda config, lines, order: [[str(tier)] for tier in order]
  model_store.read_models = lambda **_: []
  api.PENALTIES.clear()
  messages = [
    {"role": "system", "content": "rules"},
    {"role": "user", "content": "first"},
    {"role": "assistant", "content": "x"},
    {"role": "user", "content": [{"type": "text", "text": "continue"}]},
  ]
  body = {"model": router.RESERVED_MODEL, "messages": messages}
  try:
    groups, slot = api.chain(router.RESERVED_MODEL, body, {}, "k")
    assert seen == ["first\ncontinue"], "all user turns, and no system prompt"
    assert groups[0] == ["3"] and slot == "daedalus/auto:TIER-B", (groups, slot)
    groups, slot = api.chain(router.RESERVED_MODEL, body, {}, "k")
    assert groups[0] == ["3"] and slot == "daedalus/auto:TIER-B", "the tier stays up"
    assert api.chain(router.RESERVED_MODEL, body, {}, "new")[0][0] == ["1"], "per key"
    assert api.chain(router.RESERVED_MODEL, body, {}, "")[0][0] == ["1"], "no key"
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]}
    for extra in (call, {"role": "tool", "tool_call_id": "c", "content": "42"}):
      tiers.append(1)
      found = api.chain(router.RESERVED_MODEL, {"messages": [*messages, extra]}, {}, "")
      assert found[0][0] == ["2"], "koinos or higher after a tool call"
    tiers.append(4)
    found = api.chain(router.RESERVED_MODEL, {"messages": [*messages, call]}, {}, "")
    assert found[0][0] == ["4"], "a higher tier stays"
  finally:
    router.required_tier, router.chain_groups, model_store.read_models = original
    api.PENALTIES.clear()


def test_keyword_tier() -> None:
  """A keyword in the last user message moves the tier 1 step above the session tier."""
  tiers: list[int] = []
  original = router.required_tier, router.chain_groups, model_store.read_models
  router.required_tier = lambda text: tiers.pop(0)
  router.chain_groups = lambda config, lines, order: [[str(tier)] for tier in order]
  model_store.read_models = lambda **_: []
  call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c"}]}
  result = {"role": "tool", "tool_call_id": "c", "content": "42"}

  def first(*turns: str | dict, system: str = "", key: str = "") -> str:
    messages = [{"role": "system", "content": system}]
    messages += [
      turn if isinstance(turn, dict) else {"role": "user", "content": turn}
      for turn in turns
    ]
    tiers.append(1)
    return api.chain(router.RESERVED_MODEL, {"messages": messages}, {}, key)[0][0][0]

  api.PENALTIES.clear()
  try:
    assert first("Think hard about it") == "1", "no keywords, no change"
    api.KEYWORDS = api.keyword_pattern(["think hard", "ultrathink"])
    assert first("Please THINK\n hard.") == "2", "any case"
    assert first("think hard", "continue") == "1", "only the last user message"
    assert first("rethink hardware", system="think hard") == "1", "whole words"
    assert first("ultrathink and think hard") == "2", "2 keywords give +1"
    assert first("hello") == "1", "no match"
    assert first("ultrathink", call) == "2", "a tool call of the same turn: no bump"
    assert first("ultrathink", call, result) == "2", "a tool result: no bump"
    assert first("go", call, result, "ultrathink") == "3", "tool rule, then +1"
    assert first("go", call, key="s") == "2", "the session is at koinos"
    assert first("go", call, result, "ultrathink", key="s") == "3", "koinos to deinos"
    turn = ("go", call, result, "ultrathink", call)
    assert first(*turn, key="s") == "3", "the session keeps deinos"
    assert first(*turn, result, "ultrathink", key="s") == "4", "deinos to sophos"
    assert first(*turn, result, "ultrathink", key="s") == "4", "the cap is sophos"
    scans: list[int] = []
    scan = api.used_tools
    api.used_tools = lambda messages: scans.append(1) or scan(messages)
    try:
      assert first("go", call, key="t") == "2" and len(scans) == 1, "1 scan"
      assert first("go", call, result, "go", key="t") == "2", "the session keeps koinos"
      assert len(scans) == 1, "a session at koinos skips the scan"
      assert first("go", call) == "2" and len(scans) == 2, "no key: a scan each time"
    finally:
      api.used_tools = scan
  finally:
    router.required_tier, router.chain_groups, model_store.read_models = original
    api.KEYWORDS = None
    api.PENALTIES.clear()


def test_pinned_never_writes_and_prune_drops_the_idle(
  folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A request reads the pins: only `prune` writes, so a lock cannot fail a read."""
  now = [0.0]
  store = penalties.Penalties(lambda: folder / "r.sqlite3", clock=lambda: now[0])
  store.pin("k", "pool", "m/1")
  now[0] = penalties.IDLE_SECONDS + 1
  database = sqlite3.connect(folder / "r.sqlite3")
  before = database.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
  database.close()
  assert store.pinned("k", "pool") is None, "an idle pin is invisible"
  database = sqlite3.connect(folder / "r.sqlite3")
  after = database.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
  database.close()
  assert after == before == 1, "the read path leaves the row"
  store.prune()
  database = sqlite3.connect(folder / "r.sqlite3")
  left = database.execute("SELECT COUNT(*) FROM pins").fetchone()[0]
  database.close()
  assert left == 0, "the timer drops it"


def test_a_busy_store_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
  """A lock on the state store gives a clear 503, in place of a raw 500."""
  config = {"a": {"api_key": "k", "api_base": "https://a.test/v1"}}
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": (([["a/1"]]), "daedalus/deinos")
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  client = TestClient(api.app, headers=AUTH)
  body = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "x"}]}
  broken = sqlite3.OperationalError("database is locked")
  monkeypatch.setattr(api.PENALTIES, "pinned", lambda *_: (_ for _ in ()).throw(broken))
  try:
    sent = client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert sent.status_code == 503, sent.text
    assert sent.json()["error"]["message"] == "The state store is busy. Try again."
    corrupt = sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(api.COOLDOWNS, "ends", lambda: (_ for _ in ()).throw(corrupt))
    monkeypatch.setattr(api.PENALTIES, "pinned", lambda *_: None)
    sent = client.post("/v1/chat/completions", json=body, headers=AUTH)
    assert sent.status_code == 503, sent.text
    assert sent.json()["error"]["message"] == (
      "The model store is not readable. Run `daedalus catalog`."
    )
  finally:
    api.get_config, api.chain = original
    upstream.set_client(None)


@pytest.fixture(scope="module")
def folder(state_folder: Path) -> Path:
  return state_folder
