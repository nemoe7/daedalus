"""A chat request carries the stored endpoint order and passes through the hooks of its provider."""

import json

import httpx
import pytest

from daedalus import config, store
from daedalus.providers import hooks
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
SEEN: list[httpx.Request] = []


def upstream(request: httpx.Request) -> httpx.Response:
  SEEN.append(request)
  message = {"role": "assistant", "content": "hi"}
  return httpx.Response(
    200,
    json={
      "id": "x",
      "object": "chat.completion",
      "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    },
  )


@pytest.fixture
async def client():
  block = {"api_base": "https://p.test/v1", "api_key": "k", "tier": {"TIER-D": ["x"]}}
  config.set_config({"p": block})
  store.write_store([{"id": "p/x"}])
  store.write_orders({"p/x": ["cheap/fp4", "cloud"]})
  api.PENALTIES.clear()
  SEEN.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=api.app),
      base_url="http://t",
      headers={"Authorization": f"Bearer {MASTER}"},
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


CHAT = {"model": "p/x", "messages": [{"role": "user", "content": "hi"}]}


async def test_order(client: httpx.AsyncClient) -> None:
  """The stored order goes out as `provider.order`, and a client `provider` object wins."""
  assert (await client.post("/v1/chat/completions", json=CHAT)).status_code == 200
  assert json.loads(SEEN[-1].content)["provider"] == {"order": ["cheap/fp4", "cloud"]}
  own = {**CHAT, "provider": {"sort": "latency"}}
  assert (await client.post("/v1/chat/completions", json=own)).status_code == 200
  assert json.loads(SEEN[-1].content)["provider"] == {"sort": "latency"}


async def test_hooks(client: httpx.AsyncClient) -> None:
  """The request hook changes the body and headers, and the answer hook changes the answer."""
  hooks.HOOKS_DIR.mkdir(parents=True, exist_ok=True)
  (hooks.HOOKS_DIR / "p.py").write_text(
    "def request(body, model, headers):\n"
    "  body['provider']['allow_fallbacks'] = False\n"
    "  headers['x-hook'] = model\n"
    "def answer(answer, model):\n"
    "  answer['choices'][0]['message']['content'] += '!'\n"
  )
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.json()["choices"][0]["message"]["content"] == "hi!"
  sent = json.loads(SEEN[-1].content)
  assert sent["provider"] == {"order": ["cheap/fp4", "cloud"], "allow_fallbacks": False}
  assert SEEN[-1].headers["x-hook"] == "p/x"
