"""Check that keep-alive bytes with no answer end at the request limit."""

import asyncio
import os
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
  stream = b'"stream":true' in request.content.replace(b" ", b"")
  return httpx.Response(200, stream=KeepAlive(stream))


def main() -> None:
  original = api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB
  with tempfile.TemporaryDirectory() as directory:
    store.MODELS_DB = Path(directory) / "models.sqlite3"
    store.write_store([{"id": "p/one"}, {"id": "p/two"}])
    api.get_config, upstream.TIMEOUT_SECONDS = lambda: CONFIG, 0.5
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    try:
      client = TestClient(api.app, headers=AUTH)
      for stream in (False, True):
        body = {
          "model": "daedalus/deinos",
          "stream": stream,
          "messages": [{"role": "user", "content": "hi"}],
        }
        started = time.perf_counter()
        result = client.post("/v1/chat/completions", json=body)
        took = time.perf_counter() - started
        assert result.status_code == 504 and took < 5, (stream, result.text, took)
        row = dashboard.HISTORY.latest(1)[0]
        assert row["status"] == 504, row
        assert "No answer in 0.5s" in row["attempts"][-1]["error"], row["attempts"]
        assert not dashboard.LIVE.rows, "the live row ends"
    finally:
      api.PENALTIES.clear()
      api.PACING.clear()
      api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB = original
      upstream.set_client(None)
  print("ok: keep-alive bytes end at the request limit")


if __name__ == "__main__":
  main()
