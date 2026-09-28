"""Runnable check of the rpm and tpm pacing. Run: python tests/routing/test_pacing.py"""

import os
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.routing import pacing
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
os.environ["DAEDALUS_MASTER_KEY"] = MASTER


def check_window() -> None:
  clock = {"now": 1000.0}
  paced = pacing.Pacing(lambda: clock["now"])
  limits = {"a/1": (2.0, None), "b/1": (None, 100.0)}
  assert not paced.full("a/1", limits)
  paced.record("a/1", 10)
  paced.record("a/1", 10)
  assert paced.full("a/1", limits), "2 requests reach rpm 2"
  assert paced.wait(["a/1"]) == 60.0
  clock["now"] += 30
  assert paced.full("a/1", limits) and paced.wait(["a/1"]) == 30.0
  clock["now"] += 30
  assert not paced.full("a/1", limits), "the requests left the window"
  paced.record("b/1", 60)
  assert not paced.full("b/1", limits)
  paced.record("b/1", 40)
  assert paced.full("b/1", limits), "100 tokens reach tpm 100"
  assert not paced.full("c/1", limits), "no limits"
  paced.enabled = False
  assert not paced.full("b/1", limits), "pacing is off"
  paced.record("c/1")
  assert not paced.recent("c/1"), "no counts when off"


def check_limits() -> None:
  rows = [
    {"id": "first/a", "rpm": 1},
    {"id": "second/b", "tpm": "500"},
    {"id": "c/1", "rpm": 0},
    {"id": "d/1"},
  ]
  store.write_store(rows)
  limits = store.pace_limits()
  assert limits == {"first/a": (1.0, None), "second/b": (None, 500.0)}, limits


def check_requests() -> None:
  config = {
    "first": {"api_key": "k", "api_base": "https://one.test/v1"},
    "second": {"api_key": "k", "api_base": "https://two.test/v1"},
  }
  sent: list[str] = []

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request.url.host)
    message = {"role": "assistant", "content": "hi"}
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})

  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": (
    ([[model]], None)
    if "/" in model and "daedalus" not in model
    else ([["first/a", "second/b"]], "daedalus/koinos")
  )
  pick, api.PENALTIES.pick = api.PENALTIES.pick, lambda: 0.0
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
  body = {"model": "daedalus/koinos", "messages": [{"role": "user", "content": "hi"}]}
  try:
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert sent == ["one.test"], sent
    sent.clear()
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert sent == ["two.test"], "a model at its rpm leaves the chain"
    weight = api.PENALTIES.weights(["first/a"])["first/a"]
    assert weight == 1.0, "a pacing skip does not change the weight"
    sent.clear()
    direct = {**body, "model": "first/a"}
    response = client.post("/v1/chat/completions", json=direct)
    assert response.status_code == 429 and sent == [], "no upstream request"
    assert response.json()["error"]["type"] == "rate_limit_exceeded", response.text
    assert 59 <= int(response.headers["retry-after"]) <= 60, response.headers
    api.PACING.enabled = False
    assert client.post("/v1/chat/completions", json=direct).status_code == 200
    assert sent == ["one.test"], "pacing is off"
  finally:
    api.get_config, api.chain = original
    api.PENALTIES.pick = pick
    api.PACING.enabled = True
    api.PACING.clear()
    upstream.set_client(None)


def main() -> None:
  with tempfile.TemporaryDirectory() as folder:
    original, store.MODELS_DB = store.MODELS_DB, Path(folder) / "models.sqlite3"
    try:
      check_window()
      check_limits()
      check_requests()
    finally:
      store.MODELS_DB = original
  print("ok: rpm and tpm pacing")


if __name__ == "__main__":
  main()
