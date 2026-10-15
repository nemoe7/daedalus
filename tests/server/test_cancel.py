"""Tests of a request that the client cancels."""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
FIRST = b'data: {"id":"a","choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'


def scope() -> dict:
  return {
    "type": "http",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "1.1",
    "method": "POST",
    "scheme": "http",
    "path": "/v1/chat/completions",
    "raw_path": b"/v1/chat/completions",
    "query_string": b"",
    "root_path": "",
    "headers": [
      (b"authorization", f"Bearer {MASTER}".encode()),
      (b"content-type", b"application/json"),
      (b"host", b"t"),
    ],
    "client": ("t", 1),
    "server": ("t", 80),
  }


async def cancelled_call(stream: bool) -> tuple[list[dict], list[str]]:
  """Send 1 pool request, and disconnect when the upstream holds the answer."""
  gate, calls = asyncio.Event(), []

  async def chunks():
    yield FIRST
    gate.set()
    await asyncio.Event().wait()

  async def upstream(request: httpx.Request) -> httpx.Response:
    calls.append(json.loads(request.content)["model"])
    if stream:
      return httpx.Response(200, content=chunks())
    gate.set()
    await asyncio.Event().wait()
    raise AssertionError("the upstream never answers")

  body = {
    "model": "daedalus/koinos",
    "stream": stream,
    "messages": [{"role": "user", "content": "hi"}],
  }
  waiting = [{"type": "http.request", "body": json.dumps(body).encode()}]

  async def receive() -> dict:
    if waiting:
      return waiting.pop(0)
    await gate.wait()
    return {"type": "http.disconnect"}

  sent: list[dict] = []

  async def send(message: dict) -> None:
    sent.append(message)

  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    try:
      await asyncio.wait_for(api.app(scope(), receive, send), 5)
    finally:
      set_client(None)
  return sent, calls


async def test_cancel() -> None:
  config.set_config(
    {
      "groq": {
        "api_base": "https://groq.test/v1",
        "api_key": "k",
        "tier": {"TIER-C": ["*"]},
      }
    }
  )
  failed: list[str] = []
  original, saved = api.Tracker.failed, store.MODELS_DB
  api.Tracker.failed = lambda self, model, exc=None: failed.append(model)
  try:
    with tempfile.TemporaryDirectory() as folder:
      store.MODELS_DB = Path(folder) / "models.sqlite3"
      store.write_store(
        [{"id": "groq/x", "mode": "chat"}, {"id": "groq/y", "mode": "chat"}]
      )

      sent, calls = await cancelled_call(stream=False)
      row = dashboard.HISTORY.latest(1)[0]
      assert row["cancelled"] is True and row["status"] == 499, row
      assert len(calls) == 1, "no fallback after the client goes"
      assert not [m for m in sent if m["type"] == "http.response.start"], sent

      sent, calls = await cancelled_call(stream=True)
      row = dashboard.HISTORY.latest(1)[0]
      assert row["cancelled"] is True and row["status"] == 200, row
      text = b"".join(m.get("body", b"") for m in sent)
      assert len(calls) == 1 and b'"content": "hi"' in text, sent
      assert failed == [], "a cancel is not a fault"
      assert not dashboard.LIVE.rows, "no request stays in flight"
  finally:
    api.Tracker.failed = original
    store.MODELS_DB = saved
    config.set_config(None)


@pytest.mark.anyio
@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("headers_sent", [False, True])
async def test_handler_cancellation_ends_only_its_live_row(
  monkeypatch: pytest.MonkeyPatch, external: bool, headers_sent: bool
) -> None:
  """Handler cancellation propagates and ends its live row without changing history rules."""
  live = dashboard.Live()
  monkeypatch.setattr(dashboard, "LIVE", live)
  other = live.start("/v1/chat/completions")
  history = dashboard.HISTORY.latest()
  entered = asyncio.Event()
  sent = []

  async def handler(scope, receive, send):
    if headers_sent:
      await send({"type": "http.response.start", "status": 200, "headers": []})
    entered.set()
    if external:
      await asyncio.Event().wait()
    raise asyncio.CancelledError

  async def receive():
    await asyncio.Event().wait()

  async def send(message):
    sent.append(message)

  task = asyncio.create_task(api.RequestLog(handler)(scope(), receive, send))
  try:
    if external:
      await asyncio.wait_for(entered.wait(), 1)
      assert len(live.rows) == 2
      task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
  assert set(live.rows) == {other}
  rows = dashboard.HISTORY.latest()
  if headers_sent:
    assert rows[1:] == history
    assert rows[0]["status"] == 200
    assert not rows[0].get("cancelled")
    assert len(sent) == 1
  else:
    assert rows == history
    assert sent == []
