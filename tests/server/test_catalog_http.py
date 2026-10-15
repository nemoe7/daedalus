"""Tests for `POST /v1/catalog`: the master-key rebuild route of a live daedalus."""

import asyncio
import threading

import httpx
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.catalog import schedule
from daedalus.server import api

MASTER = "test-master-key-0006"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def test_catalog_route_starts_a_rebuild(monkeypatch) -> None:
  """A master-key POST starts the rebuild that the dashboard chip starts."""
  done = threading.Event()
  monkeypatch.setattr(api, "CATALOG_REFRESH", done.set)
  response = TestClient(api.app, headers=AUTH).post("/v1/catalog")
  assert response.status_code == 202
  assert response.json() == {"ok": True}
  assert done.wait(5.0), "the rebuild did not start"


def test_catalog_route_needs_the_master_key() -> None:
  response = TestClient(api.app).post("/v1/catalog")
  assert response.status_code == 401
  assert response.json()["error"]["type"] == "authentication_error"


def test_catalog_route_refuses_a_second_rebuild(monkeypatch) -> None:
  monkeypatch.setattr(api, "CATALOG_REFRESH", lambda: None)
  monkeypatch.setattr(schedule, "BUSY", True)
  response = TestClient(api.app, headers=AUTH).post("/v1/catalog")
  assert response.status_code == 409
  assert response.json()["error"]["type"] == "invalid_request_error"


def test_catalog_route_without_a_rebuild(monkeypatch) -> None:
  monkeypatch.setattr(api, "CATALOG_REFRESH", None)
  response = TestClient(api.app, headers=AUTH).post("/v1/catalog")
  assert response.status_code == 503
  assert response.json()["error"]["type"] == "server_error"


def test_the_control_calls_leave_no_request_row() -> None:
  """The Requests page holds the model calls alone: a refused control call leaves no row."""
  client = TestClient(api.app)
  assert client.post("/v1/catalog").status_code == 401
  assert client.post("/v1/hook/example.py").status_code == 401
  assert dashboard.HISTORY.latest() == [], "a control call is not a model request"


async def test_startup_build_keeps_the_ui_and_api_available(monkeypatch) -> None:
  """A blocked startup worker leaves the event loop and dashboard routes available."""
  store.write_store([])
  loop = asyncio.get_running_loop()
  loop_thread = threading.get_ident()
  started = asyncio.Event()
  release = threading.Event()
  threads, snapshot_threads = [], []
  rows_by_id = store.rows_by_id

  def snapshot():
    snapshot_threads.append(threading.get_ident())
    return rows_by_id()

  monkeypatch.setattr(store, "rows_by_id", snapshot)

  def rebuild():
    threads.append(threading.get_ident())
    loop.call_soon_threadsafe(started.set)
    assert release.wait(5)

  monkeypatch.setattr(api, "CATALOG_INITIAL", True, raising=False)
  monkeypatch.setattr(api, "CATALOG_REFRESH", rebuild)
  monkeypatch.setattr(api, "LIMIT_CHECKS", False)
  monkeypatch.setattr(schedule, "EVERY", 0)
  monkeypatch.setattr(schedule, "BUSY", False)
  monkeypatch.setattr(schedule, "PENDING", None)
  monkeypatch.setattr(schedule, "TASKS", set())
  async with api.lifespan(api.app):
    try:
      await asyncio.wait_for(started.wait(), 1)
      assert threads and threads[0] != loop_thread
      assert schedule.BUSY
      async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test", headers=AUTH
      ) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ui/", follow_redirects=True)).status_code == 200
        assert (await client.get("/v1/models")).status_code == 200
        login = await client.post(
          "/ui/api/login", json={"username": "admin", "password": MASTER}
        )
        assert login.status_code == 200
        session = {"x-daedalus-session": login.json()["session"]}
        assert (await client.get("/ui/api/models", headers=session)).status_code == 200
    finally:
      release.set()
      await asyncio.gather(*schedule.TASKS)
  assert not schedule.BUSY
  assert snapshot_threads and set(snapshot_threads) == set(threads)
