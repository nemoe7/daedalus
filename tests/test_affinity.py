import httpx
from fastapi.testclient import TestClient

from daedalus import affinity, api

FAILING: set[str] = set()


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host in FAILING:
    return httpx.Response(500, json={"error": "down"})
  message = {"role": "assistant", "content": request.url.host}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def check_pins() -> None:
  now = [0.0]
  pins = affinity.Pins(idle=10, clock=lambda: now[0])
  chain = ["a/1", "b/1", "c/1"]
  assert pins.order("k", "pool", chain) == chain, "no pin keeps the chain"
  assert pins.answered("k", "pool", "b/1") == "new"
  assert pins.order("k", "pool", chain) == ["b/1", "a/1", "c/1"]
  assert pins.order("other", "pool", chain) == chain, "each key has its own pins"
  assert pins.order("k", "other", chain) == chain, "each slot has its own pin"
  assert pins.answered("k", "pool", "b/1") == "hit"
  assert pins.order("k", "pool", ["a/1", "c/1"]) == ["a/1", "c/1"], (
    "a pin not in the chain"
  )
  assert pins.failed("k", "pool", "a/1") is False
  assert pins.pinned("k", "pool") == "b/1", "only the pinned model removes the pin"
  assert pins.failed("k", "pool", "b/1") is True
  assert pins.pinned("k", "pool") is None
  pins.answered("k", "pool", "c/1")
  assert pins.answered("k", "pool", "a/1") == "moved"
  now[0] = 11
  assert pins.pinned("k", "pool") is None, "a pin expires after the idle time"


def check_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abc"
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config: (["a/1", "b/1", "c/1"], "daedalus/deinos")
  api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  api.PINS.pins.clear()
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
    FAILING.clear()
    assert ask() == "b.test", "the pin stays while it answers"
    assert ask("key-two") == "a.test", "another key has no pin"
    FAILING.add("b.test")
    assert ask() == "a.test", "a failed pin moves to the next model that answers"
    FAILING.clear()
    assert ask() == "a.test", "the new pin"
  finally:
    api.get_config, api.chain = original
    api.set_client(None)
    api.PINS.pins.clear()


def check_slots() -> None:
  body = {"messages": [{"role": "user", "content": "hi"}]}
  _, slot = api.chain("daedalus/auto", body, {})
  assert slot.startswith("daedalus/auto:TIER-"), "auto pins per tier"
  assert api.chain("daedalus/sophos", body, {})[1] == "daedalus/sophos"
  assert api.chain("x/y", body, {"x": {}}) == (["x/y"], None), "no pin for one model"


def main() -> None:
  check_pins()
  check_slots()
  check_requests()
  print("ok: session affinity")


if __name__ == "__main__":
  main()
