"""Runnable check for Headroom compression. Run: python tests/test_headroom.py"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.server import api, headroom, upstream

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
os.environ["DAEDALUS_MASTER_KEY"] = MASTER
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
    self.compress: list[httpx.Request] = []
    self.provider: list[dict] = []

  def __call__(self, request: httpx.Request) -> httpx.Response:
    if request.url.host == "headroom":
      self.compress.append(request)
      if self.status != 200:
        return httpx.Response(self.status, text="not here")
      return httpx.Response(200, json={"messages": SHORT, "tokens_saved": 42})
    self.provider.append(json.loads(request.content))
    message = {"role": "assistant", "content": "ok"}
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def check_compress(sidecar: Sidecar, lines: Lines) -> None:
  body = {"model": "daedalus/auto", "messages": [{"role": "user", "content": "long"}]}
  os.environ.pop(headroom.URL_ENV, None)
  assert asyncio.run(headroom.compress(body, "a/1")) == (body, None), (
    "off without the URL"
  )
  assert not sidecar.compress, "no call when off"
  os.environ[headroom.URL_ENV] = "http://headroom:8787/"
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


def check_request(sidecar: Sidecar, lines: Lines) -> None:
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


def main() -> None:
  sidecar = Sidecar()
  lines = Lines()
  api.logger.addHandler(lines)
  api.logger.setLevel(logging.INFO)
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sidecar)))
  try:
    with tempfile.TemporaryDirectory() as name:
      store.MODELS_DB = Path(name) / "models.sqlite3"
      check_compress(sidecar, lines)
      check_request(sidecar, lines)
  finally:
    upstream.set_client(None)
    api.logger.removeHandler(lines)
    os.environ.pop(headroom.URL_ENV, None)
  print("ok: Headroom compression, fail open, and the saved field")


if __name__ == "__main__":
  main()
