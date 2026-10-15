"""Fresh CLI dumps use completed server output and preserve files on failure."""

import errno
import io
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from daedalus import catalog, cli, store
from daedalus.catalog import discovery
from daedalus.server import api


def test_fresh_dump_writes_server_files_without_a_local_rebuild(tmp_path, monkeypatch):
  """A live export uses server bytes instead of the local model store."""
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path)
  monkeypatch.setenv(cli.DAEDALUS_URL, "https://remote.test")
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  monkeypatch.setattr(catalog, "refresh", lambda: pytest.fail("local rebuild"))
  monkeypatch.setattr(store, "migrate", lambda: pytest.fail("local migration"))
  content = json.dumps({"data": [{"id": "server/fresh"}]})
  calls = []

  def open_url(request, timeout):
    calls.append((request.get_method(), urlsplit(request.full_url), timeout))
    response = io.BytesIO(json.dumps({"files": {"models.json": content}}).encode())
    response.status = 200
    return response

  monkeypatch.setattr(cli.urllib.request, "urlopen", open_url)
  cli.run(["dump", "models", "--refresh"])
  assert (tmp_path / "models.json").read_text() == content
  assert len(calls) == 1 and calls[0][0] == "POST"
  assert calls[0][1].path == "/v1/catalog"
  assert parse_qs(calls[0][1].query) == {"dump": ["models"], "format": ["json"]}
  assert calls[0][2] > cli.ASK_SECONDS


@pytest.mark.parametrize("failure", ["refusal", "timeout", "connection"])
def test_failed_remote_export_never_falls_back_or_overwrites(
  tmp_path, monkeypatch, failure
):
  """A remote refusal or uncertain completion leaves existing output intact."""
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path)
  monkeypatch.setenv(cli.DAEDALUS_URL, "https://remote.test")
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  monkeypatch.setattr(catalog, "refresh", lambda: pytest.fail("local fallback"))
  existing = tmp_path / "models.json"
  existing.write_text("old output")
  calls = []

  def open_url(request, timeout):
    calls.append(request)
    if failure == "refusal":
      raise urllib.error.HTTPError(request.full_url, 409, "busy", {}, None)
    if failure == "timeout":
      raise TimeoutError("timeout")
    raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))

  monkeypatch.setattr(cli.urllib.request, "urlopen", open_url)
  with pytest.raises(SystemExit) as error:
    cli.run(["dump", "models", "--refresh"])
  assert error.value.code != 0
  assert len(calls) == 1
  assert existing.read_text() == "old output"


def test_absent_implicit_local_server_allows_an_offline_refresh(tmp_path, monkeypatch):
  """Only a refused implicit local connection permits a local fresh export."""
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path / "dump")
  monkeypatch.delenv(cli.DAEDALUS_URL, raising=False)
  monkeypatch.delenv(api.DAEDALUS_HOST, raising=False)
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  calls = []

  def absent(*args, **kwargs):
    raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))

  def refresh():
    calls.append("refresh")
    store.write_store([{"id": "local/fresh"}])

  monkeypatch.setattr(cli.urllib.request, "urlopen", absent)
  monkeypatch.setattr(catalog, "refresh", refresh)
  cli.run(["dump", "models", "--refresh"])
  data = json.loads((tmp_path / "dump" / "models.json").read_text())["data"]
  assert [row["id"] for row in data] == ["local/fresh"]
  assert calls == ["refresh"]


@pytest.mark.parametrize(
  "name",
  ["../escape.json", "..\\escape.json", "C:escape.json", "/escape.json", "models.csv"],
)
def test_remote_export_rejects_paths_before_changing_output(
  tmp_path, monkeypatch, name
):
  """The client validates every returned filename before any existing file changes."""
  folder = tmp_path / "dump"
  folder.mkdir()
  existing = folder / "models.json"
  existing.write_text("old output")
  monkeypatch.setattr(discovery, "DUMP_DIR", folder)
  monkeypatch.setenv(cli.DAEDALUS_URL, "https://remote.test")
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  calls = []

  def open_url(request, timeout):
    calls.append(request)
    response = io.BytesIO(
      json.dumps({"files": {"models.json": "new", name: "bad"}}).encode()
    )
    response.status = 200
    return response

  monkeypatch.setattr(cli.urllib.request, "urlopen", open_url)
  with pytest.raises(SystemExit) as error:
    cli.run(["dump", "models", "--refresh"])
  assert error.value.code != 0
  assert len(calls) == 1
  assert existing.read_text() == "old output"
  assert not (tmp_path / "escape.json").exists()


def test_failed_offline_fetch_keeps_existing_export_and_cached_rows(
  tmp_path, monkeypatch
):
  """Retained model rows remain readable but cannot be exported as a fresh fetch."""
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  folder = tmp_path / "dump"
  folder.mkdir()
  existing = folder / "models.json"
  existing.write_text("old output")
  monkeypatch.setattr(discovery, "DUMP_DIR", folder)
  monkeypatch.delenv(cli.DAEDALUS_URL, raising=False)
  monkeypatch.delenv(api.DAEDALUS_HOST, raising=False)
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  store.write_store([{"id": "p/retained"}])
  monkeypatch.setattr(catalog, "LAST_FAILED", [])
  calls = []

  def absent(*args, **kwargs):
    raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))

  def refresh():
    calls.append("refresh")
    catalog.LAST_FAILED = ["p"]

  monkeypatch.setattr(cli.urllib.request, "urlopen", absent)
  monkeypatch.setattr(catalog, "refresh", refresh)
  with pytest.raises(SystemExit) as error:
    cli.run(["dump", "models", "--refresh"])
  assert error.value.code != 0
  assert calls == ["refresh"]
  assert existing.read_text() == "old output"
  assert [row["id"] for row in store.stored_rows()] == ["p/retained"]


def test_implicit_remote_host_refusal_does_not_trigger_a_local_build(monkeypatch):
  """A host setting for another server is not proof that the local server is absent."""
  monkeypatch.delenv(cli.DAEDALUS_URL, raising=False)
  monkeypatch.setenv(api.DAEDALUS_HOST, "remote.test")
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)
  monkeypatch.setattr(catalog, "refresh", lambda: pytest.fail("local fallback"))
  calls = []

  def refused(request, timeout):
    calls.append(request)
    raise urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))

  monkeypatch.setattr(cli.urllib.request, "urlopen", refused)
  with pytest.raises(SystemExit) as error:
    cli.run(["dump", "all", "--refresh"])
  assert error.value.code != 0
  assert len(calls) == 1


@pytest.mark.parametrize(
  "body", [[], {"files": []}, {"files": {}}, {"files": {"models.json": None}}]
)
def test_malformed_export_keeps_existing_output(tmp_path, monkeypatch, body):
  """A malformed server response fails before output changes."""
  existing = tmp_path / "models.json"
  existing.write_text("old output")
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path)
  monkeypatch.setenv(cli.DAEDALUS_URL, "https://remote.test")
  monkeypatch.setattr(cli.logs, "setup_logging", lambda: None)

  def open_url(request, timeout):
    response = io.BytesIO(json.dumps(body).encode())
    response.status = 200
    return response

  monkeypatch.setattr(cli.urllib.request, "urlopen", open_url)
  with pytest.raises(SystemExit) as error:
    cli.run(["dump", "models", "--refresh"])
  assert error.value.code != 0
  assert existing.read_text() == "old output"
