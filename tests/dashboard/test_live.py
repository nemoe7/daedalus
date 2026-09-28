"""Tests of the live request list and its event stream."""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx
from starlette.requests import Request

from daedalus import config, dashboard, store
from daedalus.dashboard.live import Live
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}

STREAM = (
  b'data: {"id":"a","choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
  b"data: [DONE]\n\n"
)
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def parsed(text: str) -> tuple[str, object]:
  """The kind and the data of 1 event."""
  lines = dict(line.split(": ", 1) for line in text.strip().splitlines())
  return lines["event"], json.loads(lines["data"])


def drained(queue: asyncio.Queue) -> list[tuple[str, object]]:
  found = []
  while not queue.empty():
    found.append(parsed(queue.get_nowait()))
  return found


def upstream(request: httpx.Request) -> httpx.Response:
  if json.loads(request.content).get("stream"):
    return httpx.Response(200, content=STREAM)
  return httpx.Response(200, json=ANSWER)


def test_live() -> None:
  live = Live()
  queue: asyncio.Queue = asyncio.Queue()
  live.queues.add(queue)
  key = live.start("/v1/chat/completions")
  live.update(key, model="groq/x", stream=True)
  live.rows[key]["started"] -= 2
  live.first(key, via="groq/x", pool=None)
  shown = live.view(key)
  assert 1.9 < shown["ttft"] <= shown["age"] < 3, shown
  assert "started" not in shown and "first" not in shown, shown
  live.end(key, {"status": 200})
  live.end(key, {"status": 200})
  kinds = [kind for kind, _ in drained(queue)]
  assert kinds == ["start", "update", "first", "end"], "a 2nd end sends nothing"
  live.update(key, model="late")
  assert queue.empty() and not live.rows, "a finished request gets no update"

  small = Live()
  full: asyncio.Queue = asyncio.Queue()
  small.queues.add(full)
  for _ in range(1001):
    small.start("/v1/x")
  assert full not in small.queues, "a stream that falls behind stops"


async def test_events() -> None:
  live = Live()
  live.start("/v1/x")
  calls = 0

  async def gone() -> bool:
    nonlocal calls
    calls += 1
    return calls > 2

  stream = live.events(gone, keepalive=0.01)
  kind, rows = parsed(await anext(stream))
  assert kind == "live" and len(rows) == 1, rows
  live.start("/v1/y")
  kind, row = parsed(await anext(stream))
  assert kind == "start" and row["path"] == "/v1/y", row
  assert await anext(stream) == ": keepalive\n\n"
  assert [text async for text in stream] == [], "the stream ends when the page goes"
  assert not live.queues, "a closed stream leaves no queue"


def test_url_session() -> None:
  session = dashboard.cookie(MASTER, time.time())

  def request(query: str) -> Request:
    scope = {"type": "http", "headers": [], "query_string": query.encode()}
    return Request(scope)

  assert dashboard.allowed(request(f"session={session}"), query=True)
  assert not dashboard.allowed(request(f"session={session}")), "only with query"
  assert not dashboard.allowed(request("session=1.bad"), query=True)


async def test_requests() -> None:
  config.set_config({"groq": {"api_base": "https://groq.test/v1", "api_key": "k"}})
  saved = store.MODELS_DB
  queue: asyncio.Queue = asyncio.Queue()
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store([{"id": "groq/x", "mode": "chat"}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
      set_client(outer)
      app = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=app, base_url="http://t", headers=AUTH
      ) as client:
        response = await client.get("/ui/api/requests/live")
        assert response.status_code == 401, "the stream needs a session"
        dashboard.LIVE.queues.add(queue)
        for stream in (True, False):
          body = {
            "model": "groq/x",
            "stream": stream,
            "reasoning_effort": "low",
            "messages": [{"role": "user", "content": "hi"}],
          }
          response = await client.post("/v1/chat/completions", json=body)
          assert response.status_code == 200, response.text
          events = drained(queue)
          kinds = [kind for kind, _ in events]
          assert kinds == ["start", "update", "update", "first", "end"], kinds
          update, trying, first, end = (data for _, data in events[1:])
          assert trying["trying"] == "groq/x" and trying["fallbacks"] == 0, trying
          assert update["model"] == "groq/x" and update["stream"] is stream, update
          assert update["effort"] == "low", update
          assert first["via"] == "groq/x" and first["ttft"] is not None, first
          row = end["row"]
          assert row["stream"] is stream and row["seconds"] >= 0, row
          assert row == dashboard.HISTORY.latest(1)[0], "the end row is the history row"
        await client.get("/ui/api/requests", headers={"X-Daedalus-Session": "x"})
        assert queue.empty(), "only API posts go on the live list"
        assert not dashboard.LIVE.rows, "no request stays in flight"
      dashboard.LIVE.queues.discard(queue)
      set_client(None)
    store.MODELS_DB = saved
