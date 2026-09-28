"""A model with a higher `order` gets a request only when no lower order model answers."""

import httpx
import pytest

from daedalus import config, store
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
SEEN: list[str] = []
FAILING: set[str] = set()


def upstream(request: httpx.Request) -> httpx.Response:
  name = request.url.host.split(".")[0]
  SEEN.append(name)
  if name in FAILING:
    return httpx.Response(500, json={"error": {"message": "down"}})
  if request.url.path.endswith("/images/generations"):
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": "AA=="}]})
  message = {"role": "assistant", "content": f"from {name}"}
  return httpx.Response(
    200,
    json={
      "id": "x",
      "object": "chat.completion",
      "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    },
  )


def block(name: str, **values: object) -> dict:
  return {
    "api_base": f"https://{name}.test/v1",
    "api_key": "k",
    "tier": {"TIER-D": ["x"]},
    **values,
  }


@pytest.fixture
async def client():
  # The order 2 provider comes first, so only the order moves it back.
  config.set_config({"cf": block("cf", order=2), "groq": block("groq")})
  store.write_store(
    [{"id": "cf/x"}, {"id": "groq/x"}]
    + [{"id": f"{name}/img", "mode": "image_generation"} for name in ("cf", "groq")]
  )
  api.PENALTIES.clear()
  api.PENALTIES.pick = lambda: 0.0
  media.REPEATS.clear()
  SEEN.clear()
  FAILING.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


def chat(text: str) -> dict:
  return {"model": "daedalus/moros", "messages": [{"role": "user", "content": text}]}


async def test_order_first(client: httpx.AsyncClient) -> None:
  """The order 1 model gets the request, and the order 2 model only after it fails."""
  response = await client.post("/v1/chat/completions", json=chat("hi"))
  assert response.status_code == 200, response.text
  assert SEEN == ["groq"], SEEN
  SEEN.clear()
  FAILING.add("groq")
  response = await client.post("/v1/chat/completions", json=chat("hello"))
  assert response.status_code == 200, response.text
  assert SEEN == ["groq", "cf"], SEEN


async def test_order_beats_pin(client: httpx.AsyncClient) -> None:
  """A session model with order 2 waits for the order 1 models of its tier."""
  FAILING.add("groq")
  first = chat("same conversation")
  response = await client.post("/v1/chat/completions", json=first)
  assert response.json()["choices"][0]["message"]["content"] == "from cf"
  SEEN.clear()
  FAILING.clear()
  first["messages"] += [
    {"role": "assistant", "content": "from cf"},
    {"role": "user", "content": "more"},
  ]
  response = await client.post("/v1/chat/completions", json=first)
  assert response.status_code == 200, response.text
  assert SEEN == ["groq"], SEEN


async def test_media_order(client: httpx.AsyncClient) -> None:
  """The media pools use the order too."""
  body = {"model": "daedalus/photos", "prompt": "a cat"}
  response = await client.post("/v1/images/generations", json=body)
  assert response.status_code == 200, response.text
  assert SEEN == ["groq"], SEEN
