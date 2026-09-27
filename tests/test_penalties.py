import json
import os
import sqlite3
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import api, keys, penalties, stream, upstream
from daedalus import store as model_store

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
os.environ["DAEDALUS_MASTER_KEY"] = MASTER
LOCAL = "test-local-key-0002"

FAILING: set[str] = set()


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host in FAILING:
    return httpx.Response(500, json={"error": "down"})
  message = {"role": "assistant", "content": request.url.host}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def check_weights(folder: Path) -> None:
  now, picks = [0.0], [0.0]
  store = penalties.Penalties(
    lambda: folder / "w.sqlite3", clock=lambda: now[0], pick=lambda: picks[0]
  )
  assert store.weights(["a"]) == {"a": 1.0}, "all models start at 1"
  assert store.record("a", penalties.FAULT) == 0.5
  assert store.record("a", penalties.FAULT) == 0.25
  assert store.record("a", penalties.SUCCESS) == 0.375
  assert store.record("b", penalties.SUCCESS) == 1.0, "1 at most"
  assert store.record("b", penalties.SLOW) == 0.75, "a slow success"
  now[0] = 3600
  assert abs(store.weights(["a"])["a"] - 0.45) < 1e-9, "x1.2 after 1 hour"
  now[0] = 3600 * 30
  assert store.weights(["a"])["a"] == 1.0, "back to 1"
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


def check_pins(folder: Path) -> None:
  now = [0.0]
  store = penalties.Penalties(lambda: folder / "p.sqlite3", clock=lambda: now[0])
  store.pick = lambda: 0.0
  groups = [["a/1", "b/1"], ["c/1"]]
  assert store.pin("k", "pool", "c/1") == "new"
  order = store.order(groups, "k", "pool")
  assert order == ["a/1", "b/1", "c/1"], "a lower-tier session model does not go first"
  store.pin("k", "pool", "b/1")
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


def check_rebuild(folder: Path) -> None:
  database = folder / "models.sqlite3"
  store = penalties.Penalties(lambda: database)
  model_store.write_store([], database)
  store.record("a", penalties.FAULT)
  store.pin("k", "pool", "a")
  model_store.write_store([{"id": "p/x"}], database)
  assert store.weights(["a"])["a"] < 1 and store.pinned("k", "pool") == "a", "kept"


def check_session_key() -> None:
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


def check_slots() -> None:
  body = {"messages": [{"role": "user", "content": "hi"}]}
  _, slot = api.chain("daedalus/auto", body, {})
  assert slot.startswith("daedalus/auto:TIER-"), "auto pins per tier"
  assert api.routed_pool(slot) in {"moros", "koinos", "deinos", "sophos"}
  assert api.routed_pool("daedalus/auto:TIER-A") == "sophos"
  assert api.chain("daedalus/praktos", body, {}) is None, "no praktos"
  assert api.chain("daedalus/sophos", body, {})[1] == "daedalus/sophos"
  assert api.chain("x/y", body, {"x": {}}) == ([["x/y"]], None), "no pin for one model"


def check_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abc"
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config: ([["a/1", "b/1"], ["c/1"]], "daedalus/deinos")
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
  finally:
    api.get_config, api.chain = original
    upstream.set_client(None)
    api.PENALTIES.clear()


def check_ttft() -> None:
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
  api.chain = lambda model, body, config: ([["a/1"]], "daedalus/deinos")
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


def main() -> None:
  with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    check_weights(folder)
    check_pins(folder)
    check_rebuild(folder)
    model_store.MODELS_DB = folder / "models.sqlite3"
    check_session_key()
    check_slots()
    check_requests()
    check_ttft()
  print("ok: penalties and session affinity")


if __name__ == "__main__":
  main()
