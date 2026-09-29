"""Each client can use its own provider key, with its own cooldowns."""

import httpx
import pytest

from daedalus import config, store
from daedalus.server import api, media
from daedalus.server.upstream import set_client
from daedalus.store import keys

MASTER = "test-master-key-0001"
SEEN: list[tuple[str, str]] = []
# The provider keys that get a 429.
LIMITED: set[str] = set()


def upstream(request: httpx.Request) -> httpx.Response:
  name = request.url.host.split(".")[0]
  key = request.headers["Authorization"].removeprefix("Bearer ")
  SEEN.append((name, key))
  if key in LIMITED:
    return httpx.Response(
      429, headers={"retry-after": "60"}, json={"error": {"message": "limit"}}
    )
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
    "api_key": f"{name}-main",
    "tier": {"TIER-D": ["x"]},
    **values,
  }


@pytest.fixture
async def tokens():
  config.set_config(
    {
      "alpha": block("alpha", client_keys={"kilo": "alpha-kilo"}),
      "groq": block("groq", order=2),
    }
  )
  store.write_store(
    [
      {"id": "alpha/x"},
      {"id": "groq/x"},
      {"id": "alpha/img", "mode": "image_generation"},
    ]
  )
  api.PENALTIES.clear()
  api.PENALTIES.pick = lambda: 0.0
  api.COOLDOWNS.clear()
  api.PACING.clear()
  media.REPEATS.clear()
  SEEN.clear()
  LIMITED.clear()
  found = {name: keys.add(store.MODELS_DB, name) for name in ("kilo", "owui")}
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    yield found
  set_client(None)
  config.set_config(None)


async def post(token: str, path: str, body: dict) -> httpx.Response:
  transport = httpx.ASGITransport(app=api.app)
  headers = {"Authorization": f"Bearer {token}"}
  async with httpx.AsyncClient(
    transport=transport, base_url="http://t", headers=headers
  ) as inner:
    return await inner.post(path, json=body)


def chat(text: str) -> dict:
  return {"model": "daedalus/moros", "messages": [{"role": "user", "content": text}]}


async def test_client_keys(tokens: dict[str, str]) -> None:
  """Kilo uses its own alpha key, the other clients use the api_key, and a Kilo 429 cools only the Kilo lane."""
  response = await post(tokens["kilo"], "/v1/chat/completions", chat("hi"))
  assert response.status_code == 200, response.text
  assert SEEN == [("alpha", "alpha-kilo")], SEEN
  SEEN.clear()
  response = await post(tokens["owui"], "/v1/chat/completions", chat("hello"))
  assert response.status_code == 200, response.text
  assert SEEN == [("alpha", "alpha-main")], SEEN
  SEEN.clear()
  response = await post(
    MASTER, "/v1/images/generations", {"model": "alpha/img", "prompt": "a"}
  )
  assert response.status_code == 200, response.text
  response = await post(
    tokens["kilo"], "/v1/images/generations", {"model": "alpha/img", "prompt": "b"}
  )
  assert response.status_code == 200, response.text
  assert SEEN == [("alpha", "alpha-main"), ("alpha", "alpha-kilo")], SEEN
  SEEN.clear()
  LIMITED.add("alpha-kilo")
  response = await post(tokens["kilo"], "/v1/chat/completions", chat("again"))
  assert response.status_code == 200, response.text
  assert SEEN == [("alpha", "alpha-kilo"), ("groq", "groq-main")], SEEN
  ends = api.COOLDOWNS.ends()
  assert set(ends) == {"alpha/x#kilo"}, ends
  SEEN.clear()
  response = await post(tokens["owui"], "/v1/chat/completions", chat("still"))
  assert response.status_code == 200, response.text
  assert SEEN == [("alpha", "alpha-main")], SEEN
  SEEN.clear()
  response = await post(tokens["kilo"], "/v1/chat/completions", chat("later"))
  assert response.status_code == 200, response.text
  assert SEEN == [("groq", "groq-main")], SEEN
