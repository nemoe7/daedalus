"""Clients use the pool names of the `pools` settings, and a replaced name gets HTTP 400."""

from types import SimpleNamespace

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


async def test_auto_pool_shows_while_live(client: httpx.AsyncClient) -> None:
  """The live row of a daedalus/auto request carries the pool of the model it tries."""
  sent: list[tuple[str, dict]] = []
  send = dashboard.LIVE.send
  dashboard.LIVE.send = lambda kind, data: sent.append((kind, data))
  try:
    response = await client.post("/v1/chat/completions", json=chat("daedalus/auto"))
    assert response.status_code == 200, response.text
  finally:
    dashboard.LIVE.send = send
  updates = [data for kind, data in sent if kind == "update"]
  assert any(row.get("trying") == "p/x" and row.get("pool") for row in updates), updates
  row = dashboard.HISTORY.latest(1)[0]
  assert any(live.get("pool") == row["pool"] for live in updates), (updates, row)


def test_the_tier_the_store_holds_beats_the_on_call_resolution(
  tmp_path, monkeypatch
) -> None:
  """The stored tier of a model beats the tier the config claims, and the claim answers when the store has none."""
  database = tmp_path / "models.sqlite3"
  store.write_store([{"id": "p/one", "tier": "TIER-B"}, {"id": "p/two"}], database)
  monkeypatch.setattr(store, "MODELS_DB", database)
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "k",
        "tier": {"TIER-A": ["*"]},
      }
    }
  )
  try:
    found = config.get_config()
    # the stored TIER-B beats the claimed TIER-A
    assert api.model_pool(found, "p/one") == "deinos"
    assert api.attempted_pool(found, "p/one") == "deinos"
    # no stored tier: the claim answers
    assert api.model_pool(found, "p/two") == "sophos"
    assert api.attempted_pool(found, "p/two") == "sophos"
    request = SimpleNamespace(state=SimpleNamespace(pool="sophos"))
    api.served(request, found, "p/one")
    assert (request.state.pool, request.state.routed) == ("deinos", "sophos")
  finally:
    config.set_config(None)


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
  assert {
    key: settings.DEFAULTS["personalization"][key] for key in defaults
  } == defaults
  try:
    api.apply_settings(settings.parse(""))
    assert router.pool_name("daedalus/sophos") == "daedalus/sophos"
    ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert "daedalus/sophos" in ids and "daedalus/tier-a" not in ids, ids
    api.apply_settings(settings.parse("personalization:\n  tier-a: best\n"))
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
