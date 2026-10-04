"""The rate-limit headers of each answer show on the Limits page, and 0 left of a day starts a cooldown."""

import httpx
import pytest

from daedalus import config, store
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}
LEFT = {"requests": "3"}
# A failed answer, for the rate-limit headers of a 429.
FAIL = {"on": False}


def upstream(request: httpx.Request) -> httpx.Response:
  if FAIL["on"]:
    # OpenRouter sends the bare names, and the error body repeats them.
    bare = {
      "x-ratelimit-limit": "1000",
      "x-ratelimit-remaining": "0",
      "x-ratelimit-reset": "1791158400000",
    }
    body = {
      "error": {
        "message": "Rate limit exceeded",
        "code": 429,
        "metadata": {"headers": {"X-RateLimit-Remaining": "0"}},
      }
    }
    return httpx.Response(429, json=body, headers=bare)
  headers = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": LEFT["requests"],
    "x-ratelimit-reset-requests": "1m",
  }
  if request.url.path.endswith("/images/generations"):
    body = {"created": 1, "data": [{"b64_json": "AA=="}]}
    return httpx.Response(200, json=body, headers=headers)
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  body = {"id": "x", "object": "chat.completion", "choices": [choice]}
  return httpx.Response(200, json=body, headers=headers)


@pytest.fixture
async def client():
  block = {
    "api_base": "https://groq.test/v1",
    "api_key": "k",
    "tier": {"TIER-D": ["x"]},
  }
  config.set_config({"groq": block})
  store.write_store([{"id": "groq/x"}, {"id": "groq/img", "mode": "image_generation"}])
  api.COOLDOWNS.clear()
  api.LIMITS.clear()
  media.REPEATS.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


async def test_a_429_keeps_its_headers(client: httpx.AsyncClient) -> None:
  """The bare rate-limit headers of a 429 land on the Limits page, also when the answer fails."""
  FAIL["on"] = True
  try:
    chat = {"model": "groq/x", "messages": [{"role": "user", "content": "hi"}]}
    response = await client.post("/v1/chat/completions", json=chat)
    assert response.status_code != 200, response.text
  finally:
    FAIL["on"] = False
  login = {"username": "admin", "password": MASTER}
  session = (await client.post("/ui/api/login", json=login)).json()["session"]
  view = (
    await client.get("/ui/api/limits", headers={"x-daedalus-session": session})
  ).json()
  rows = {lane["model"]: lane["rows"][0] for lane in view["lanes"]}
  assert rows["groq/x"]["remaining"] == 0, rows
  assert (rows["groq/x"]["kind"], rows["groq/x"]["span"]) == ("requests", "day"), rows


async def test_headers(client: httpx.AsyncClient) -> None:
  chat = {"model": "groq/x", "messages": [{"role": "user", "content": "hi"}]}
  response = await client.post("/v1/chat/completions", json=chat)
  assert response.status_code == 200, response.text
  image = {"model": "groq/img", "prompt": "a cat"}
  response = await client.post("/v1/images/generations", json=image)
  assert response.status_code == 200, response.text
  login = {"username": "admin", "password": MASTER}
  session = (await client.post("/ui/api/login", json=login)).json()["session"]
  view = (
    await client.get("/ui/api/limits", headers={"x-daedalus-session": session})
  ).json()
  rows = {lane["model"]: lane["rows"][0] for lane in view["lanes"]}
  assert set(rows) == {"groq/x", "groq/img"}, rows
  assert (rows["groq/x"]["remaining"], rows["groq/x"]["span"]) == (3, "day"), rows
  assert view["providers"] == [], "no balance endpoint for groq"
  LEFT["requests"] = "0"
  try:
    response = await client.post("/v1/chat/completions", json=chat)
    assert response.status_code == 200, "the answer with 0 left still goes out"
    assert "groq/x" in api.COOLDOWNS.ends(), "0 left of a day starts a cooldown"
  finally:
    LEFT["requests"] = "3"
