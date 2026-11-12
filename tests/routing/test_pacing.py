"""Tests of the rpm and tpm pacing."""

import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.routing import pacing
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"


def test_window() -> None:
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


def test_credit() -> None:
  clock = {"now": 1000.0}
  paced = pacing.Pacing(lambda: clock["now"])
  limits = {"a/1": (None, 50.0)}
  paced.record("a/1", 60)
  assert paced.full("a/1", limits), "60 tokens reach tpm 50"
  paced.credit("a/1", 40)
  assert not paced.full("a/1", limits), "credit 40 brings sum to 20"
  paced.record("a/1", 30)
  assert paced.full("a/1", limits), "50 tokens (20+30) reach tpm 50"
  paced.credit("a/1", 100)
  assert not paced.full("a/1", limits), "credit above the entry floors only that entry"
  paced.enabled = False
  paced.record("b/1", 50)
  assert not paced.full("b/1", limits), "pacing is off ignores requests"
  paced.enabled = True
  paced.clear()
  paced.credit("c/1", 10)
  assert not paced.recent("c/1"), "credit on empty window is no-op"


def test_limits() -> None:
  rows = [
    {"id": "first/a", "rpm": 1},
    {"id": "second/b", "tpm": "500"},
    {"id": "c/1", "rpm": 0},
    {"id": "d/1"},
  ]
  store.write_store(rows)
  limits = store.pace_limits()
  assert limits == {"first/a": (1.0, None), "second/b": (None, 500.0)}, limits


def test_requests() -> None:
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
  api.chain = lambda model, body, config, key="", headers=None: (
    ([[model]], None)
    if "/" in model and "daedalus" not in model
    else ([["first/a", "second/b"]], "daedalus/koinos")
  )
  selection, api.PENALTIES.select = api.PENALTIES.select, lambda: 0.0
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
    assert "Daedalus rate limit (pacing:rpm" in response.json()["error"]["message"]
    assert 59 <= int(response.headers["retry-after"]) <= 60, response.headers
    row = api.dashboard.HISTORY.latest(1)[0]
    assert row["attempts"][0]["model"] == "first/a", row["attempts"]
    assert row["attempts"][0]["result"] == "paced", row["attempts"]
    assert "Daedalus local pacing (rpm): 1/1 in 60s" in row["attempts"][0]["error"]
    api.PACING.enabled = False
    assert client.post("/v1/chat/completions", json=direct).status_code == 200
    assert sent == ["one.test"], "pacing is off"
  finally:
    api.get_config, api.chain = original
    api.PENALTIES.select = selection
    api.PACING.enabled = True
    api.PACING.clear()
    upstream.set_client(None)


def test_cached_tokens_integration() -> None:
  """Non-stream answer with cached_tokens credits the lane at tpm limit."""
  config = {
    "first": {"api_key": "k", "api_base": "https://one.test/v1"},
  }
  saved = store.MODELS_DB
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store([{"id": "first/a", "tpm": 100}])
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
      sent.append(request.url.host)
      message = {"role": "assistant", "content": "hi"}
      choice = {"index": 0, "message": message, "finish_reason": "stop"}
      return httpx.Response(
        200,
        json={
          "id": "x",
          "model": "m",
          "choices": [choice],
          "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 60},
          },
        },
      )

    original = api.get_config, api.chain
    api.get_config = lambda: config
    api.chain = lambda model, body, config, key="", headers=None: (
      ([[model]], None)
      if "/" in model and "daedalus" not in model
      else ([["first/a"]], "daedalus/koinos")
    )
    selection, api.PENALTIES.select = api.PENALTIES.select, lambda: 0.0
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
    body = {"model": "first/a", "messages": [{"role": "user", "content": "hi"}]}
    try:
      # First request: 80 tokens, 60 cached -> 20 effective
      assert client.post("/v1/chat/completions", json=body).status_code == 200
      sent.clear()
      # Second request: 80 tokens, 60 cached -> 20 effective, total 40 < 100
      assert client.post("/v1/chat/completions", json=body).status_code == 200
    finally:
      api.get_config, api.chain = original
      api.PENALTIES.select = selection
      api.PACING.enabled = True
      api.PACING.clear()
      upstream.set_client(None)
      store.MODELS_DB = saved


def test_cached_tokens_function() -> None:
  from daedalus.server.stream import cached_tokens

  # OpenAI format
  assert (
    cached_tokens(
      {
        "prompt_tokens": 80,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 50},
      }
    )
    == 50
  )
  # Missing details
  assert cached_tokens({"prompt_tokens": 80, "completion_tokens": 10}) == 0
  # Non-int cached_tokens
  assert cached_tokens({"prompt_tokens_details": {"cached_tokens": "not int"}}) == 0
  # Negative cached_tokens
  assert cached_tokens({"prompt_tokens_details": {"cached_tokens": -10}}) == 0
  # Gemini format
  assert (
    cached_tokens(
      {
        "usageMetadata": {
          "promptTokenCount": {"cached_tokens": 30},
          "candidatesTokenCount": 10,
        }
      }
    )
    == 30
  )
  # Non-dict input
  assert cached_tokens(None) == 0
  assert cached_tokens("string") == 0
