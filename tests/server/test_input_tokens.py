import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx

from daedalus import config, dashboard, store
from daedalus.routing import context
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
os.environ["DAEDALUS_MASTER_KEY"] = MASTER

SEEN: list[dict] = []
MESSAGES = [{"role": "user", "content": "hello there"}]


def data(chunk: dict) -> bytes:
  return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def upstream(request: httpx.Request) -> httpx.Response:
  body = json.loads(request.content)
  SEEN.append(body)
  if not body.get("stream"):
    answer = {
      "id": "n1",
      "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
      "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
    }
    return httpx.Response(200, json=answer)
  parts = [
    data({"id": "s1", "choices": [{"index": 0, "delta": {"content": "hi"}}]}),
    data({"id": "s1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
  ]
  if (body.get("stream_options") or {}).get("include_usage"):
    usage = {"prompt_tokens": 42, "completion_tokens": 1, "total_tokens": 43}
    parts.append(data({"id": "s1", "choices": [], "usage": usage}))
  return httpx.Response(200, content=b"".join([*parts, b"data: [DONE]\n\n"]))


async def send(client: httpx.AsyncClient, model: str, **extra: object) -> list[str]:
  SEEN.clear()
  body = {"model": model, "messages": MESSAGES, **extra}
  response = await client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  return [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]


def last_tokens() -> dict:
  return dashboard.HISTORY.latest(1)[0]["tokens"]


async def main() -> None:
  config.set_config(
    {
      name: {"api_base": f"https://{name}.test/v1", "api_key": "k"}
      for name in ("groq", "plain")
    }
  )
  saved = store.MODELS_DB
  estimate = context.input_tokens({"messages": MESSAGES})
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    async with httpx.AsyncClient(
      transport=httpx.MockTransport(upstream)
    ) as upstream_client:
      set_client(upstream_client)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=app, base_url="http://t", headers=AUTH
      ) as client:
        lines = await send(client, "groq/x", stream=True)
        assert SEEN[0]["stream_options"] == {"include_usage": True}, SEEN
        assert not any('"usage"' in line for line in lines), "the client did not ask"
        assert lines[-1] == "[DONE]", lines
        assert last_tokens() == {"input": 42, "estimate": False}, last_tokens()

        options = {"include_usage": True}
        lines = await send(client, "groq/x", stream=True, stream_options=options)
        assert json.loads(lines[-2])["usage"]["prompt_tokens"] == 42, lines

        lines = await send(client, "plain/x", stream=True)
        assert "stream_options" not in SEEN[0], "no documented support"
        assert last_tokens() == {"input": estimate, "estimate": True}, last_tokens()

        await send(client, "plain/x")
        assert last_tokens() == {"input": 7, "estimate": False}, last_tokens()
  store.MODELS_DB = saved
  set_client(None)
  config.set_config(None)
  print("ok: input tokens")


if __name__ == "__main__":
  asyncio.run(main())
