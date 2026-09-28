import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.routing import context
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


class Lines(logging.Handler):
  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(record.getMessage())


def answer(request: httpx.Request) -> httpx.Response:
  if request.url.host == "c.test":
    return httpx.Response(429, json={"error": {"message": "Rate limit for c"}})
  message = {"role": "assistant", "content": request.url.host}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_estimate() -> None:
  image = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64," + "A" * 9000},
  }
  text = {"type": "text", "text": "abcd" * 10}
  body = {"messages": [{"role": "user", "content": [text, image]}]}
  assert context.input_tokens(body) == 15, (
    "57 characters; the image data does not count"
  )
  tools = [{"type": "function", "function": {"name": "abc"}}]
  assert context.input_tokens({"messages": [], "tools": tools}) == 3, "tools count"
  assert context.input_tokens({"messages": [{"content": "abcde"}]}) == 2, "round up"


def test_limits(database: Path) -> None:
  rows = [
    {"id": "a/1", "max_input_tokens": 10},
    {"id": "b/1"},
    {"id": "c/1", "max_input_tokens": "1000"},
    {"id": "d/1", "max_input_tokens": 0},
  ]
  store.write_store(rows, database)
  assert store.input_limits() == {"a/1": 10, "c/1": 1000}, store.input_limits()


def test_requests() -> None:
  config = {
    name: {"api_key": "k", "api_base": f"https://{name}.test/v1"} for name in "abcd"
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  api.PENALTIES.clear()
  lines = Lines()
  api.logger.addHandler(lines)
  level = api.logger.level
  api.logger.setLevel(logging.INFO)
  client = TestClient(api.app, headers=AUTH)
  large = {
    "model": "daedalus/deinos",
    "messages": [{"role": "user", "content": "x" * 400}],
  }
  try:
    api.chain = lambda model, body, config, key="": ([["a/1", "b/1"]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "b.test"
    assert not any(line.startswith("skip") for line in lines.lines), "no skip line"
    assert api.PENALTIES.weights(["a/1"])["a/1"] == 1.0, "a skip is not a fault"
    steps = [
      (s["model"], s["result"]) for s in dashboard.HISTORY.latest(1)[0]["attempts"]
    ]
    assert steps == [("b/1", "answered")], "a skip is silent"
    assert "fallbacks=0" in lines.lines[-1], lines.lines[-1]
    api.chain = lambda model, body, config, key="": ([["c/1", "b/1"]], None)
    small = {**large, "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/v1/chat/completions", json=small).status_code == 200
    failed = dashboard.HISTORY.latest(1)[0]["attempts"][0]
    assert failed["result"] == "HTTP 429" and "Rate limit for c" in failed["error"], (
      failed
    )
    assert isinstance(failed["seconds"], float), failed
    api.chain = lambda model, body, config, key="": ([["a/1", "b/1"]], None)
    response = client.post("/v1/chat/completions", json=small)
    assert response.json()["choices"][0]["message"]["content"] == "a.test"
    api.chain = lambda model, body, config, key="": ([["a/1"]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "context_length_exceeded"
    api.chain = lambda model, body, config, key="": ([[]], None)
    response = client.post("/v1/chat/completions", json=large)
    assert response.status_code == 502, "an empty chain is not a context error"
    api.PENALTIES.clear()
    key = api.session_key(MASTER, large["messages"])
    api.PENALTIES.pin(key, "deinos", "a/1")
    api.PENALTIES.record("b/1", 0.5)
    api.chain = lambda model, body, config, key="": ([["a/1", "b/1", "d/1"]], "deinos")
    response = client.post("/v1/chat/completions", json=large)
    assert response.json()["choices"][0]["message"]["content"] == "b.test", (
      "the draw skips a too-small pinned model"
    )
    steps = [
      (s["model"], s["result"]) for s in dashboard.HISTORY.latest(1)[0]["attempts"]
    ]
    assert steps == [("b/1", "answered")], "a skip is silent"
    assert api.PENALTIES.pinned(key, "deinos") == "b/1", "the pin moves"
  finally:
    api.get_config, api.chain = original
    upstream.set_client(None)
    api.logger.removeHandler(lines)
    api.logger.setLevel(level)


def test_served() -> None:
  config = {"p": {"tier": {"TIER-C": ["x"], "TIER-D": ["y"]}}}
  request = SimpleNamespace(state=SimpleNamespace(pool="moros"))
  api.served(request, config, "p/y")
  assert request.state.pool == "moros" and not hasattr(request.state, "routed")
  api.served(request, config, "p/x")
  assert (request.state.pool, request.state.routed) == ("koinos", "moros"), "the ladder"
  plain = SimpleNamespace(state=SimpleNamespace())
  api.served(plain, config, "p/x")
  assert not hasattr(plain.state, "pool"), "only daedalus/auto has a pool"
  api.served(request, config, "other/z")
  assert request.state.pool == "koinos", "a model without a tier keeps the pool"


@pytest.fixture(scope="module", autouse=True)
def first_pick():
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api.PENALTIES, "pick", lambda: 0.0)
    yield


@pytest.fixture
def database() -> Path:
  return store.MODELS_DB
