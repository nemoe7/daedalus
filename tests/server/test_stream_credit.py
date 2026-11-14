import json
import tempfile
from pathlib import Path

import httpx
import pytest

from daedalus import config, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}

TIER = {"TIER-D": ["x"]}
SEEN: list[tuple[str, dict]] = []
PLAN: dict[str, tuple[str, int | list[bytes]]] = {}


def data(chunk: dict) -> bytes:
  return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def text(identifier: str, content: str) -> bytes:
  return data(
    {"id": identifier, "choices": [{"index": 0, "delta": {"content": content}}]}
  )


def upstream(request: httpx.Request) -> httpx.Response:
  host = request.url.host
  SEEN.append((host, json.loads(request.content)))
  kind, parts = PLAN[host.split(".")[0]]
  if kind == "status":
    return httpx.Response(parts, json={"error": {"message": "no"}})
  if kind == "drop":
    return httpx.Response(200, stream=Dropped(parts))
  return httpx.Response(200, content=b"".join(parts))


def output(raw: str) -> list[str]:
  return [line[6:] for line in raw.splitlines() if line.startswith("data: ")]


async def send(client: httpx.AsyncClient) -> list[str]:
  api.PENALTIES.clear()
  api.PENALTIES.select = lambda: 0.0
  body = {
    "model": "daedalus/auto",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  return output(response.text)


class Dropped(httpx.AsyncByteStream):
  def __init__(self, parts: list[bytes]) -> None:
    self.parts = parts

  async def __aiter__(self):
    for part in self.parts:
      yield part
    raise httpx.ReadError("connection dropped")


async def test_stream_cached_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
  """A usage chunk with cached_tokens credits the served lane at [DONE]."""
  setup = {
    name: {"api_base": f"https://{name}.test/v1", "api_key": "k", "tier": TIER}
    for name in ("a", "b")
  }
  monkeypatch.setattr(api, "get_config", lambda: setup)
  saved = store.MODELS_DB
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store([{"id": "a/x", "tpm": 100}, {"id": "b/x"}])
    api.PACING.clear()
    api.COOLDOWNS.clear()
    SEEN.clear()
    PLAN.clear()
    usage = {
      "id": "a1",
      "choices": [{"index": 0, "finish_reason": "stop"}],
      "usage": {
        "prompt_tokens": 80,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 50},
      },
    }
    PLAN.update(
      a=(
        "ok",
        [
          text("a1", "Hel"),
          text("a1", "lo"),
          b"data: " + json.dumps(usage).encode() + b"\n\n",
          b"data: [DONE]\n\n",
        ],
      ),
    )
    try:
      async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
      ) as upstream_client:
        set_client(upstream_client)
        app = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(
          transport=app, base_url="http://t", headers=AUTH
        ) as client:
          lines = await send(client)
        assert lines[-1] == "[DONE]", lines
        assert [host for host, _ in SEEN] == ["a.test"], SEEN
        # The recorded entry for a/x was credited by the cached count.
        tokens = sum(count for _, count in api.PACING.recent("a/x"))
        assert tokens == 0, tokens
    finally:
      set_client(None)
      api.PACING.clear()
      api.COOLDOWNS.clear()
      SEEN.clear()
      PLAN.clear()
      config.set_config(None)
      store.MODELS_DB = saved
