"""Tests for Headroom compression."""

import asyncio
import json
import logging
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus.server import api, headroom, upstream

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
SHORT = [{"role": "user", "content": "short"}]


class Lines(logging.Handler):
  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(record.getMessage())


class Sidecar:
  def __init__(self) -> None:
    self.status = 200
    self.health: list[httpx.Request] = []
    self.compress: list[httpx.Request] = []
    self.provider: list[dict] = []

  def __call__(self, request: httpx.Request) -> httpx.Response:
    if request.url.host == "headroom":
      if request.url.path == "/health":
        self.health.append(request)
        return httpx.Response(
          self.status,
          json={
            "status": "healthy" if self.status == 200 else "unhealthy",
            "ready": self.status == 200,
          },
        )
      self.compress.append(request)
      if self.status != 200:
        return httpx.Response(self.status, text="not here")
      return httpx.Response(200, json={"messages": SHORT, "tokens_saved": 42})
    self.provider.append(json.loads(request.content))
    message = {"role": "assistant", "content": "ok"}
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_compress(sidecar: Sidecar, lines: Lines) -> None:
  body = {"model": "daedalus/auto", "messages": [{"role": "user", "content": "long"}]}
  os.environ.pop(headroom.HEADROOM_URL, None)
  assert asyncio.run(headroom.compress(body, "a/1")) == (body, None), (
    "off without the URL"
  )
  assert not sidecar.compress, "no call when off"
  os.environ[headroom.HEADROOM_URL] = "http://headroom:8787/"
  result, saved = asyncio.run(headroom.compress(body, "a/1"))
  assert result == {**body, "messages": SHORT} and saved == 42, (result, saved)
  sent = sidecar.compress[-1]
  assert str(sent.url) == "http://headroom:8787/v1/compress", sent.url
  payload = json.loads(sent.content)
  assert payload == {
    "messages": body["messages"],
    "model": "a/1",
    "config": {"mode": "lossy_inline"},
  }, payload
  sidecar.status = 404
  for _ in range(2):
    assert asyncio.run(headroom.compress(body, "a/1")) == (body, None), "fail open"
  warnings = [line for line in lines.lines if line.startswith("headroom failed")]
  assert warnings == ["headroom failed, sending the original messages: HTTP 404"], (
    warnings
  )
  sidecar.status = 200
  asyncio.run(headroom.compress(body, "a/1"))
  assert "headroom answers again" in lines.lines, lines.lines


def test_available(sidecar: Sidecar, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv(headroom.HEADROOM_URL, raising=False)
  assert not asyncio.run(headroom.available()), "off without a URL"
  monkeypatch.setenv(headroom.HEADROOM_URL, "http://headroom:8787")
  assert asyncio.run(headroom.available()), "a healthy sidecar answers /health"
  sidecar.status = 503
  assert not asyncio.run(headroom.available()), (
    "a non-success health check is unavailable"
  )
  sidecar.status = 200


class Trickle(httpx.AsyncByteStream):
  """A body that never ends."""

  async def __aiter__(self):
    while True:
      yield b" "
      await asyncio.sleep(0.02)


def test_total_limit(
  sidecar: Sidecar, lines: Lines, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A sidecar that keeps sending bytes gets the timeout in total, then the original messages go."""
  body = {"model": "m", "messages": [{"role": "user", "content": "long"}]}
  trickle = httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle()))
  kept = upstream.get_client()
  monkeypatch.setenv(headroom.HEADROOM_URL, "http://headroom:8787")
  monkeypatch.setattr(headroom, "TIMEOUT_SECONDS", 0.3)
  monkeypatch.setattr(headroom, "_down", False)
  upstream.set_client(httpx.AsyncClient(transport=trickle))
  lines.lines.clear()
  try:
    started = time.perf_counter()
    assert asyncio.run(headroom.compress(body, "a/1")) == (body, None)
    assert time.perf_counter() - started < 2, "the limit is for the whole answer"
    assert lines.lines == [
      "headroom failed, sending the original messages: no answer in 0.3s"
    ], lines.lines
  finally:
    upstream.set_client(kept)


def test_request(sidecar: Sidecar, lines: Lines) -> None:
  config = {"a": {"api_key": "k", "api_base": "https://a.test/v1"}}
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": ([["a/1"]], None)
  client = TestClient(api.app, headers=AUTH)
  body = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "long"}]}
  try:
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, response.text
    assert sidecar.provider[-1]["messages"] == SHORT, "the provider gets the short text"
    assert any(line.endswith("saved=42") for line in lines.lines), lines.lines
    sidecar.status = 503
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, "fail open"
    assert sidecar.provider[-1]["messages"] == body["messages"], "the original text"
    assert "saved=" not in lines.lines[-1], lines.lines[-1]
  finally:
    api.get_config, api.chain = original
    sidecar.status = 200


def test_request_switch(sidecar: Sidecar, lines: Lines) -> None:
  """A model entry with `headroom: false` turns the compression off for its own calls."""
  config = {
    "a": {
      "api_key": "k",
      "api_base": "https://a.test/v1",
      "models": {"1": {"headroom": False}},
    }
  }
  original = api.get_config, api.chain
  api.get_config = lambda: config
  api.chain = lambda model, body, config, key="": ([["a/1"]], None)
  client = TestClient(api.app, headers=AUTH)
  body = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "long"}]}
  lines.lines.clear()
  before = len(sidecar.compress)
  try:
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, response.text
    assert len(sidecar.compress) == before, "no call for a model with the switch off"
    assert sidecar.provider[-1]["messages"] == body["messages"], "the original text"
    assert not [line for line in lines.lines if "saved=" in line], lines.lines
  finally:
    api.get_config, api.chain = original


@pytest.fixture(scope="module")
def lines():
  lines = Lines()
  api.logger.addHandler(lines)
  api.logger.setLevel(logging.INFO)
  yield lines
  api.logger.removeHandler(lines)


@pytest.fixture(autouse=True, scope="module")
def headroom_url():
  """The sidecar address of the module, so a test passes in any run order."""
  kept = os.environ.get(headroom.HEADROOM_URL)
  os.environ[headroom.HEADROOM_URL] = "http://headroom:8787"
  yield
  if kept is None:
    os.environ.pop(headroom.HEADROOM_URL, None)
  else:
    os.environ[headroom.HEADROOM_URL] = kept


@pytest.fixture(scope="module")
def sidecar():
  sidecar = Sidecar()
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sidecar)))
  yield sidecar
  upstream.set_client(None)
