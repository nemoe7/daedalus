"""Tests for `POST /v1/catalog`: the master-key rebuild route of a live daedalus."""

import threading

from fastapi.testclient import TestClient

from daedalus import dashboard
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
