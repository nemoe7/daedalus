"""Check that keep-alive bytes do not hold a request: a model fails after the wait, and the request ends at its limit."""

import asyncio
import json
import os
import random
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.server import api, upstream

os.environ["DAEDALUS_MASTER_KEY"] = "test-master-key-0001"
AUTH = {"Authorization": "Bearer test-master-key-0001"}
CONFIG = {
  "p": {"api_key": "k", "api_base": "https://p.test/v1", "tier": {"TIER-B": ["*"]}}
}


class KeepAlive(httpx.AsyncByteStream):
  """A body that sends only keep-alive bytes."""

  def __init__(self, stream: bool) -> None:
    self.chunk = b": OPENROUTER PROCESSING\n\n" if stream else b"\n"

  async def __aiter__(self) -> AsyncIterator[bytes]:
    while True:
      yield self.chunk
      await asyncio.sleep(0.02)


def answer(request: httpx.Request) -> httpx.Response:
  body = json.loads(request.content)
  if body["model"] == "one" or not ANSWERS:
    return httpx.Response(200, stream=KeepAlive(bool(body.get("stream"))))
  message = {"role": "assistant", "content": "two"}
  if not body.get("stream"):
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    return httpx.Response(200, json={"id": "x", "choices": [choice]})
  choice = {"index": 0, "delta": message, "finish_reason": "stop"}
  data = json.dumps({"id": "x", "choices": [choice]})
  return httpx.Response(200, content=f"data: {data}\n\ndata: [DONE]\n\n".encode())


# The model names that answer. p/one sends only keep-alive bytes.
ANSWERS: set[str] = set()


def ask(client: TestClient, stream: bool) -> httpx.Response:
  body = {
    "model": "daedalus/deinos",
    "stream": stream,
    "messages": [{"role": "user", "content": "hi"}],
  }
  return client.post("/v1/chat/completions", json=body)


def check_limit(client: TestClient) -> None:
  ANSWERS.clear()
  upstream.TIMEOUT_SECONDS = 0.5
  for stream in (False, True):
    started = time.perf_counter()
    result = ask(client, stream)
    took = time.perf_counter() - started
    assert result.status_code == 504 and took < 5, (stream, result.text, took)
    row = dashboard.HISTORY.latest(1)[0]
    assert row["status"] == 504, row
    assert "No answer in 0.5s" in row["attempts"][-1]["error"], row["attempts"]
    assert not dashboard.LIVE.rows, "the live row ends"


def check_wait(client: TestClient) -> None:
  ANSWERS.add("two")
  upstream.TIMEOUT_SECONDS, upstream.WAIT_SECONDS = 30.0, 0.2
  api.PENALTIES.pick = lambda: 0.0
  sent: list[tuple[str, dict]] = []
  send = dashboard.LIVE.send
  dashboard.LIVE.send = lambda kind, data: sent.append((kind, data))
  try:
    for stream in (False, True):
      api.PENALTIES.clear()
      sent.clear()
      started = time.perf_counter()
      result = ask(client, stream)
      took = time.perf_counter() - started
      assert result.status_code == 200 and "two" in result.text, result.text
      assert took < 5, took
      attempts = dashboard.HISTORY.latest(1)[0]["attempts"]
      assert attempts[0]["model"] == "p/one", attempts
      assert "Only keep-alive bytes for 0.2s" in attempts[0]["error"], attempts
      shown = [(d.get("trying"), d.get("fallbacks")) for k, d in sent if k == "update"]
      assert ("p/one", 0) in shown and ("p/two", 1) in shown, shown
  finally:
    dashboard.LIVE.send = send


def main() -> None:
  original = api.get_config, upstream.TIMEOUT_SECONDS, upstream.WAIT_SECONDS
  state = store.MODELS_DB
  with tempfile.TemporaryDirectory() as directory:
    store.MODELS_DB = Path(directory) / "models.sqlite3"
    store.write_store([{"id": "p/one"}, {"id": "p/two"}])
    api.get_config = lambda: CONFIG
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    try:
      client = TestClient(api.app, headers=AUTH)
      check_limit(client)
      check_wait(client)
    finally:
      api.PENALTIES.clear()
      api.PACING.clear()
      api.get_config, upstream.TIMEOUT_SECONDS, upstream.WAIT_SECONDS = original
      store.MODELS_DB = state
      api.PENALTIES.pick = random.random
      upstream.set_client(None)
  print("ok: keep-alive bytes end at the wait and at the request limit")


if __name__ == "__main__":
  main()
