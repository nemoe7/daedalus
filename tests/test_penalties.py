import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import api, catalog, penalties

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
  assert store.record("a", success=False) == 0.5
  assert store.record("a", success=False) == 0.25
  assert store.record("a", success=True) == 0.375
  assert store.record("b", success=True) == 1.0, "1 at most"
  now[0] = 3600
  assert abs(store.weights(["a"])["a"] - 0.45) < 1e-9, "x1.2 after 1 hour"
  now[0] = 3600 * 30
  assert store.weights(["a"])["a"] == 1.0, "back to 1"
  now[0] = 0
  store.clear()
  store.record("a", success=False)
  groups = [["a", "b", "c"], ["d", "e"]]
  store.record("d", success=False)
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
  assert store.order(groups, "k", "pool") == ["c/1", "a/1", "b/1"], "the pin goes first"
  assert store.order(groups, "other", "pool")[0] == "a/1", "each key has its own pins"
  assert store.order(groups, "k", "other")[0] == "a/1", "each slot has its own pin"
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
  catalog.write_store([], database)
  store.record("a", success=False)
  store.pin("k", "pool", "a")
  catalog.write_store([{"id": "p/x"}], database)
  assert store.weights(["a"])["a"] < 1 and store.pinned("k", "pool") == "a", "kept"


def check_slots() -> None:
  body = {"messages": [{"role": "user", "content": "hi"}]}
  _, slot = api.chain("daedalus/auto", body, {})
  assert slot.startswith("daedalus/auto:TIER-"), "auto pins per tier"
  assert api.chain("daedalus/sophos", body, {})[1] == "daedalus/sophos"
  assert api.chain("x/y", body, {"x": {}}) == ([["x/y"]], None), "no pin for one model"


def check_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abc"
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config: ([["a/1", "b/1"], ["c/1"]], "daedalus/deinos")
  api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  api.PENALTIES.clear()
  api.PENALTIES.pick = lambda: 0.0
  client = TestClient(api.app)

  def ask(token: str = "key-one") -> str:
    body = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "x"}]}
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["choices"][0]["message"]["content"]

  try:
    FAILING.add("a.test")
    assert ask() == "b.test", "the first model that answers"
    assert api.PENALTIES.weights(["a/1"])["a/1"] < 0.51, "a fault lowers the weight"
    FAILING.clear()
    assert ask() == "b.test", "the pin stays while it answers"
    api.PENALTIES.pick = lambda: 0.4
    assert ask("key-two") == "b.test", "a/1 has a lower weight for all keys"
    api.PENALTIES.pick = lambda: 0.0
    FAILING.add("b.test")
    assert ask() == "a.test", "a failed pin moves to the next model that answers"
    FAILING.clear()
    assert ask() == "a.test", "the new pin"
  finally:
    api.get_config, api.chain = original
    api.set_client(None)
    api.PENALTIES.clear()


def main() -> None:
  with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    check_weights(folder)
    check_pins(folder)
    check_rebuild(folder)
    catalog.MODELS_DB = folder / "models.sqlite3"
    check_slots()
    check_requests()
  print("ok: penalties and session affinity")


if __name__ == "__main__":
  main()
