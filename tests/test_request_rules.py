import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx

from daedalus import config, dashboard, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
os.environ["DAEDALUS_MASTER_KEY"] = MASTER

TIER = {"TIER-D": ["x"]}
SEEN: list[tuple[str, dict]] = []


def upstream(request: httpx.Request) -> httpx.Response:
  SEEN.append((request.url.host, json.loads(request.content)))
  answer = {
    "id": "r1",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
  }
  return httpx.Response(200, json=answer)


async def send(client: httpx.AsyncClient, model: str) -> httpx.Response:
  SEEN.clear()
  body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
  return await client.post("/v1/chat/completions", json=body)


async def check_missing_key(client: httpx.AsyncClient) -> None:
  """A provider with no API key leaves every chain, with no attempt and no fault."""
  api.PENALTIES.clear()
  response = await send(client, "daedalus/moros")
  assert response.status_code == 200, response.text
  assert [host for host, _ in SEEN] == ["a.test"], SEEN
  attempts = dashboard.HISTORY.latest(1)[0]["attempts"]
  assert [a["model"] for a in attempts] == ["a/x"], attempts
  assert api.PENALTIES.weights(["b/x"]) == {"b/x": 1.0}, "no fault"
  response = await send(client, "b/x")
  assert response.status_code == 400, response.text
  assert response.json()["error"]["message"] == "Missing api_key for b", response.text
  assert SEEN == [], "no upstream call"


async def check_default_effort(client: httpx.AsyncClient) -> None:
  """A request with no effort gets the stored effort of the model."""
  response = await send(client, "a/thinks")
  assert response.status_code == 200, response.text
  assert SEEN[0][1]["reasoning_effort"] == "high", SEEN
  SEEN.clear()
  body = {
    "model": "a/thinks",
    "messages": [{"role": "user", "content": "hi"}],
    "reasoning_effort": "low",
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert SEEN[0][1]["reasoning_effort"] == "low", "the request value wins"
  await send(client, "a/plain")
  assert "reasoning_effort" not in SEEN[0][1], "a model that does not reason"


async def check_output_limit(client: httpx.AsyncClient) -> None:
  """An output limit above the limit of the model drops to that limit."""
  for field in ("max_tokens", "max_completion_tokens"):
    for asked, sent in ((9000, 4000), (100, 100)):
      SEEN.clear()
      body = {
        "model": "a/thinks",
        "messages": [{"role": "user", "content": "hi"}],
        field: asked,
      }
      response = await client.post("/v1/chat/completions", json=body)
      assert response.status_code == 200, response.text
      assert SEEN[0][1][field] == sent, (field, SEEN)
  SEEN.clear()
  body = {"model": "a/x", "messages": [{"role": "user", "content": "hi"}]}
  await client.post("/v1/chat/completions", json={**body, "max_tokens": 9000})
  assert SEEN[0][1]["max_tokens"] == 9000, "no stored limit, no change"


async def main() -> None:
  config.set_config(
    {
      "a": {"api_base": "https://a.test/v1", "api_key": "k", "tier": TIER},
      "b": {"api_base": "https://b.test/v1", "api_key": "", "tier": TIER},
    }
  )
  saved = store.MODELS_DB
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store(
      [
        {"id": "a/x"},
        {"id": "b/x"},
        {
          "id": "a/thinks",
          "supports_reasoning": True,
          "reasoning_effort": "high",
          "max_output_tokens": 4000,
        },
        {"id": "a/plain", "supports_reasoning": False, "reasoning_effort": "high"},
      ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outside:
      set_client(outside)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=app, base_url="http://t", headers=AUTH
      ) as client:
        await check_missing_key(client)
        await check_default_effort(client)
        await check_output_limit(client)
  store.MODELS_DB = saved
  set_client(None)
  config.set_config(None)
  print("ok: request rules")


if __name__ == "__main__":
  asyncio.run(main())
