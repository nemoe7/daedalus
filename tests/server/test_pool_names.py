"""Clients use the pool names of the `pools` settings, and a replaced name gets HTTP 400."""

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.config import settings
from daedalus.routing import router
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def upstream(request: httpx.Request) -> httpx.Response:
  if request.url.path.endswith("/images/generations"):
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": "AA=="}]})
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(
    200, json={"id": "x", "object": "chat.completion", "choices": [choice]}
  )


@pytest.fixture
async def client():
  block = {"api_base": "https://p.test/v1", "api_key": "k", "tier": {"TIER-D": ["x"]}}
  config.set_config({"p": block})
  store.write_store([{"id": "p/x"}, {"id": "p/img", "mode": "image_generation"}])
  media.REPEATS.clear()
  router.set_pool_names({"moros": "fast", "koinos": "moros", "photos": "pics"})
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as inner:
      yield inner
  router.set_pool_names({})
  set_client(None)
  config.set_config(None)


def chat(model: str) -> dict:
  return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


async def test_generic_keys(client: httpx.AsyncClient) -> None:
  """A generic config key renames its pool, and the default value keeps the built-in name."""
  defaults = {
    "tier-a": "sophos",
    "tier-b": "deinos",
    "tier-c": "koinos",
    "tier-d": "moros",
    "audio": "graphos",
    "images": "photos",
  }
  assert settings.DEFAULTS["pools"] == defaults
  try:
    api.apply_settings(settings.parse(""))
    assert router.pool_name("daedalus/sophos") == "daedalus/sophos"
    ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert "daedalus/sophos" in ids and "daedalus/tier-a" not in ids, ids
    api.apply_settings(settings.parse("pools:\n  tier-a: best\n"))
    assert router.pool_name("daedalus/sophos") == "daedalus/best"
    ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert "daedalus/best" in ids and "daedalus/sophos" not in ids, ids
    response = await client.post("/v1/chat/completions", json=chat("daedalus/sophos"))
    assert response.status_code == 400, response.text
    # The settings call drops the upstream client, so the mock comes back for the last call.
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as again:
      set_client(again)
      response = await client.post("/v1/chat/completions", json=chat("daedalus/best"))
      assert response.status_code == 200, response.text
  finally:
    router.set_pool_names({})
    api.apply_settings(settings.parse(""))


def test_names() -> None:
  """A new name gives the built-in name, and a replaced name that no pool took gives None."""
  router.set_pool_names({"moros": "koinos", "koinos": "moros", "photos": "pics"})
  try:
    assert router.built_in("daedalus/koinos") == "daedalus/moros"
    assert router.built_in("daedalus/photos") is None
    assert router.built_in("p/x") == "p/x"
    assert router.pool_name("daedalus/photos") == "daedalus/pics"
    assert router.pool_name("moros") == "koinos"
    assert router.pool_name("daedalus/auto") == "daedalus/auto"
  finally:
    router.set_pool_names({})


async def test_renamed(client: httpx.AsyncClient) -> None:
  """The new names answer and show on /v1/models and the dashboard, and the old names get HTTP 400."""
  ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
  assert ids[:5] == [
    "daedalus/auto",
    "daedalus/fast",
    "daedalus/moros",
    "daedalus/deinos",
    "daedalus/sophos",
  ], ids
  assert "daedalus/pics" in ids and "daedalus/photos" not in ids, ids
  response = await client.post("/v1/chat/completions", json=chat("daedalus/fast"))
  assert response.status_code == 200, response.text
  response = await client.post("/v1/chat/completions", json=chat("daedalus/auto"))
  assert response.status_code == 200, response.text
  assert dashboard.HISTORY.latest(1)[0]["pool"] == "fast", dashboard.HISTORY.latest(1)
  image = {"model": "daedalus/pics", "prompt": "a cat"}
  response = await client.post("/v1/images/generations", json=image)
  assert response.status_code == 200, response.text
  image = {"model": "daedalus/photos", "prompt": "a cat"}
  response = await client.post("/v1/images/generations", json=image)
  assert response.status_code == 400 and "Unknown provider or pool" in response.text
  response = await client.post("/v1/chat/completions", json=chat("daedalus/unused"))
  assert response.status_code == 400, response.text
