"""The key check for `/v1`: no key, a short key, a key with spaces, a named key, a master key."""

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from daedalus import store
from daedalus.server import access
from daedalus.store import keys

MASTER = "test-master-key-0001"


@pytest.fixture
def guard() -> FastAPI:
  """One route behind the key check, so the test covers the gate and not the router."""
  app = FastAPI()

  @app.get("/v1/guarded")
  async def guarded(request: Request) -> JSONResponse:
    denied = access.check_api_key(request)
    return denied if denied is not None else JSONResponse({"key": request.state.key})

  return app


async def get(app: FastAPI, token: str | None) -> httpx.Response:
  headers = {} if token is None else {"Authorization": f"Bearer {token}"}
  async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app=app), base_url="http://t", headers=headers
  ) as client:
    return await client.get("/v1/guarded")


async def test_no_key(guard: FastAPI) -> None:
  """A request with no Authorization header gets 401."""
  assert (await get(guard, None)).status_code == 401


async def test_short_key(guard: FastAPI) -> None:
  """A key of 15 characters matches neither the master key nor a stored key."""
  assert (await get(guard, "0123456789abcde")).status_code == 401


async def test_key_with_a_space(guard: FastAPI) -> None:
  """The space keeps the token off the master key, and the store holds no key with a space."""
  assert (await get(guard, f"{MASTER} extra")).status_code == 401


async def test_master_key(guard: FastAPI) -> None:
  """The master key opens the route as `master`."""
  response = await get(guard, MASTER)
  assert response.status_code == 200 and response.json() == {"key": "master"}


async def test_named_key(guard: FastAPI) -> None:
  """A key from the store opens the route under its own name."""
  token = keys.add(store.MODELS_DB, "kilo")
  response = await get(guard, token)
  assert response.status_code == 200 and response.json() == {"key": "kilo"}
