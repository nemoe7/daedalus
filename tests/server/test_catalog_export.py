"""Fresh catalog exports share the rebuild guard and preserve raw-data access."""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from daedalus import catalog, config, store
from daedalus.catalog import discovery, schedule
from daedalus.server import api
from daedalus.store import keys

MASTER = "test-master-export-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


@pytest.mark.parametrize("kind", ["catalog", "models", "enrichments", "all"])
@pytest.mark.parametrize("file_format", ["json", "csv"])
def test_export_returns_the_completed_server_build(monkeypatch, kind, file_format):
  """The response contains the requested fresh files without using the server dump folder."""
  from daedalus.catalog import enrichment

  url = "https://export.test/models"
  monkeypatch.setattr(config, "SAVED", {"p": {"discovery_url": url, "api_key": "key"}})
  monkeypatch.setattr(discovery, "get_config", lambda: config.SAVED)
  monkeypatch.setattr(enrichment, "get_config", lambda: config.SAVED)
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  monkeypatch.setattr(schedule, "BUSY", False)
  observed = []

  def refresh():
    observed.append(schedule.BUSY)
    discovery.save_snapshot(url, {"data": [{"id": "fresh"}]})
    discovery.save_snapshot(enrichment.litellm_url("p"), {"data": [{"id": "p/fresh"}]})
    discovery.save_snapshot(
      enrichment.modelschemas_url("p"),
      {"models": [{"id": "fresh", "rawId": "fresh"}]},
    )
    store.write_store([{"id": "p/fresh"}])

  monkeypatch.setattr(api, "CATALOG_REFRESH", refresh)
  folder = discovery.DUMP_DIR
  folder.mkdir(parents=True, exist_ok=True)
  existing = folder / "keep.json"
  existing.write_text("unchanged")
  response = TestClient(api.app, headers=AUTH).post(
    "/v1/catalog", params={"dump": kind, "format": file_format}
  )
  assert response.status_code == 200, response.text
  assert observed == [True]
  assert not schedule.BUSY
  files = response.json()["files"]
  expected = {
    "catalog": {"p"},
    "models": {"models"},
    "enrichments": {"litellm-p", "modelschemas-p"},
    "all": {"p", "models", "litellm-p", "modelschemas-p"},
  }[kind]
  assert set(files) == {f"{name}.{file_format}" for name in expected}
  for name, content in files.items():
    rows = (
      json.loads(content)["data"]
      if file_format == "json"
      else list(csv.DictReader(io.StringIO(content)))
    )
    if isinstance(rows, dict):
      rows = rows.get("data") or rows.get("models")
    expected_id = (
      "p/fresh"
      if name.startswith("models.")
      or (file_format == "json" and name.startswith("litellm-"))
      else "fresh"
    )
    assert rows[0]["id"] == expected_id
  assert existing.read_text() == "unchanged"


def test_export_rejects_a_named_key_even_when_its_name_is_master(monkeypatch):
  """A named key cannot acquire raw-data access through its display name."""
  token = keys.add(store.MODELS_DB, "master")
  calls = []
  monkeypatch.setattr(api, "CATALOG_REFRESH", lambda: calls.append("refresh"))
  response = TestClient(api.app).post(
    "/v1/catalog?dump=all", headers={"Authorization": f"Bearer {token}"}
  )
  assert response.status_code == 403
  assert calls == []


def test_export_refuses_a_busy_or_failed_refresh(monkeypatch):
  """A refusal or a failed provider fetch cannot return a fresh export."""
  monkeypatch.setattr(api, "CATALOG_REFRESH", lambda: None)
  monkeypatch.setattr(schedule, "BUSY", True)
  client = TestClient(api.app, headers=AUTH)
  assert client.post("/v1/catalog?dump=all").status_code == 409
  monkeypatch.setattr(schedule, "BUSY", False)
  monkeypatch.setattr(catalog, "LAST_FAILED", ["p"])
  response = client.post("/v1/catalog?dump=all")
  assert response.status_code == 502
  assert "files" not in response.json()
  assert not schedule.BUSY


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_export_keeps_the_guard_until_capture_finishes(monkeypatch, cancel):
  """Output capture stays off-loop and holds the guard even when its requester leaves."""
  import asyncio
  import threading

  import httpx

  url = "https://guard.test/models"
  monkeypatch.setattr(discovery, "get_config", lambda: {"p": {"discovery_url": url}})
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  monkeypatch.setattr(schedule, "BUSY", False)
  monkeypatch.setattr(schedule, "TASKS", set())
  loop = asyncio.get_running_loop()
  started = asyncio.Event()
  release = threading.Event()
  threads = []
  original = discovery.dump

  def refresh():
    discovery.save_snapshot(url, {"data": [{"id": "fresh"}]})
    store.write_store([{"id": "p/fresh"}])

  def capture(*args, **kwargs):
    threads.append(threading.get_ident())
    loop.call_soon_threadsafe(started.set)
    assert release.wait(5)
    return original(*args, **kwargs)

  monkeypatch.setattr(api, "CATALOG_REFRESH", refresh)
  monkeypatch.setattr(discovery, "dump", capture)
  async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app=api.app), base_url="http://test", headers=AUTH
  ) as client:
    request = asyncio.create_task(client.post("/v1/catalog?dump=catalog"))
    try:
      await asyncio.wait_for(started.wait(), 1)
      if cancel:
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
          await request
      assert schedule.BUSY
      assert (await client.get("/health")).status_code == 200
      assert (await client.post("/v1/catalog")).status_code == 409
    finally:
      release.set()
      if not request.cancelled():
        response = await request
      await asyncio.gather(*schedule.TASKS)
  if not cancel:
    assert response.status_code == 200
  assert not schedule.BUSY
  assert threads and threads[0] != threading.get_ident()


def test_export_refuses_a_snapshot_not_updated_by_the_refresh(monkeypatch):
  """An old snapshot from a provider without credentials cannot be called fresh."""
  import sqlite3

  url = "https://stale.test/models"
  discovery.save_snapshot(url, {"data": [{"id": "old"}]})
  with sqlite3.connect(store.MODELS_DB) as database:
    database.execute("UPDATE provider_snapshots SET updated = 1")
  monkeypatch.setattr(discovery, "get_config", lambda: {"p": {"discovery_url": url}})
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  monkeypatch.setattr(api, "CATALOG_REFRESH", lambda: store.write_store([]))
  response = TestClient(api.app, headers=AUTH).post("/v1/catalog?dump=catalog")
  assert response.status_code == 502
  assert "files" not in response.json()
  assert discovery.cached_snapshot(url) == {"data": [{"id": "old"}]}


@pytest.mark.parametrize("query", ["dump=wrong", "dump=all&format=wrong", "dump="])
def test_invalid_export_options_do_not_start_a_rebuild(monkeypatch, query):
  """Reject invalid output options before a catalog can change."""
  calls = []
  monkeypatch.setattr(api, "CATALOG_REFRESH", lambda: calls.append("refresh"))
  response = TestClient(api.app, headers=AUTH).post(f"/v1/catalog?{query}")
  assert response.status_code == 400
  assert calls == []


def test_export_rejects_a_provider_path_before_writing(tmp_path, monkeypatch):
  """A configured provider name cannot send a fresh export outside its staging folder."""
  url = "https://path.test/models"
  name = str(tmp_path / "escape")
  monkeypatch.setattr(discovery, "get_config", lambda: {name: {"discovery_url": url}})
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  monkeypatch.setattr(
    api, "CATALOG_REFRESH", lambda: discovery.save_snapshot(url, {"data": []})
  )
  response = TestClient(api.app, headers=AUTH).post("/v1/catalog?dump=catalog")
  assert response.status_code == 502
  assert not (tmp_path / "escape.json").exists()
