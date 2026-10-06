"""Tests for the discovery step of the CLI: a host command and a live daedalus."""

import contextlib
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from daedalus import catalog, cli, dashboard
from daedalus.server import api

MASTER = "test-master-key-0005"


class Live(BaseHTTPRequestHandler):
  """A small daedalus: `/health`, and `/v1/catalog` with a chosen status."""

  status = 202
  seen: ClassVar[list[tuple[str, str | None]]] = []

  def do_GET(self) -> None:
    Live.seen.append(("GET", self.path))
    self.answer(200, b'{"status": "ok"}')

  def do_POST(self) -> None:
    Live.seen.append(("POST", self.headers.get("Authorization")))
    self.answer(Live.status, b'{"ok": true}')

  def answer(self, status: int, body: bytes) -> None:
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *args: object) -> None:
    """Keep the test output clean."""


@pytest.fixture
def live():
  """The address of a running `Live` server, on a free port."""
  Live.status, Live.seen = 202, []
  server = ThreadingHTTPServer(("127.0.0.1", 0), Live)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  yield f"http://127.0.0.1:{server.server_address[1]}"
  server.shutdown()
  thread.join()
  server.server_close()


def test_probe_answers_over_the_socket(live: str) -> None:
  assert cli.probe(f"{live}/health") is True
  assert cli.probe("http://127.0.0.1:1") is False


def test_live_url_from_the_address_the_host_and_the_port(
  monkeypatch, live: str
) -> None:
  monkeypatch.setenv(cli.DAEDALUS_URL, live)
  assert cli.live_url() == live
  monkeypatch.setenv(cli.DAEDALUS_URL, f"{live}/")
  assert cli.live_url() == live, "a trailing slash is not part of the address"
  asked: list[str] = []
  monkeypatch.setattr(cli, "probe", lambda url: bool(asked.append(url)))
  monkeypatch.delenv(cli.DAEDALUS_URL)
  monkeypatch.setenv(api.DAEDALUS_HOST, "0.0.0.0")
  monkeypatch.setenv(api.DAEDALUS_PORT, "3457")
  assert cli.live_url() is None
  assert asked == ["http://127.0.0.1:3457"], "a wildcard host probes the loopback"
  monkeypatch.setenv(api.DAEDALUS_HOST, "daedalus.internal")
  assert cli.live_url() is None
  assert asked[-1] == "http://daedalus.internal:3457"


def test_ask_rebuild(monkeypatch, live: str) -> None:
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  assert cli.ask_rebuild(live) is True
  assert Live.seen == [("POST", f"Bearer {MASTER}")]
  Live.status = 401
  assert cli.ask_rebuild(live) is False
  assert cli.ask_rebuild("http://127.0.0.1:1") is False
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, "too-short")
  assert cli.ask_rebuild(live) is False, "no key means no request"


def test_catalog_uses_the_live_server(monkeypatch, live: str) -> None:
  """The discovery line names the server, and the server does the rebuild."""
  calls: list[str] = []
  monkeypatch.setattr(catalog, "refresh", lambda: calls.append("local"))
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  monkeypatch.setenv(cli.DAEDALUS_URL, live)
  shown = io.StringIO()
  with contextlib.redirect_stdout(shown):
    cli.run(["catalog"])
  assert calls == [], "the live server did the work"
  assert shown.getvalue() == f"using the daedalus at {live}\n"
  assert Live.seen == [("GET", "/health"), ("POST", f"Bearer {MASTER}")]


def test_catalog_runs_here_with_no_server(monkeypatch) -> None:
  calls: list[str] = []
  monkeypatch.setattr(catalog, "refresh", lambda: calls.append("local"))
  monkeypatch.setattr(cli, "live_url", lambda: None)
  shown = io.StringIO()
  with contextlib.redirect_stdout(shown):
    cli.run(["catalog"])
  assert calls == ["local"]
  assert shown.getvalue() == "", "no discovery line with no server"


def test_catalog_runs_here_when_the_server_refuses(monkeypatch, live: str) -> None:
  calls: list[str] = []
  Live.status = 401
  monkeypatch.setattr(catalog, "refresh", lambda: calls.append("local"))
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  monkeypatch.setenv(cli.DAEDALUS_URL, live)
  cli.run(["catalog"])
  assert calls == ["local"], "a refused ask falls back to the local rebuild"
