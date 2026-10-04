"""The parallel race: the ladder shows it, and a runner that wins takes the pin as rce."""

import json

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
SEEN: list[str] = []
STREAMS: list[bool] = []
FAIL: set[str] = set()


def upstream(request: httpx.Request) -> httpx.Response:
  """One model answers with a stream, and a model in FAIL gives a 500."""
  body = json.loads(request.content)
  model = body["model"]
  SEEN.append(model)
  STREAMS.append(bool(body.get("stream")))
  if model in FAIL:
    return httpx.Response(500, json={"error": {"message": "boom"}})
  if not body.get("stream"):
    message = {"role": "assistant", "content": "hi"}
    return httpx.Response(
      200,
      json={
        "id": "x",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
      },
    )
  delta = {
    "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]
  }
  body = f"data: {json.dumps(delta)}\n\ndata: [DONE]\n\n"
  return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


@pytest.fixture
async def client():
  block = {
    "api_base": "https://p.test/v1",
    "api_key": "provider-key",
    "tier": {"TIER-D": ["a", "b"]},
  }
  config.set_config({"p": block})
  store.write_store([{"id": "p/a"}, {"id": "p/b"}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  media.REPEATS.clear()
  dashboard.HISTORY.clear()
  SEEN.clear()
  STREAMS.clear()
  FAIL.clear()
  api.PARALLEL_ENABLED = True
  api.PARALLEL_COUNT = 1
  api.PARALLEL_CHANCE = 1.0
  api.PENALTIES.race = True
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport,
      base_url="http://t",
      headers={"Authorization": f"Bearer {MASTER}"},
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


CHAT = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "hi"}]}


async def test_the_race_runs_and_the_row_shows_it(client: httpx.AsyncClient) -> None:
  """A stream to a pool starts the next model, and the loser lands in the ladder."""
  await client.post("/v1/chat/completions", json=CHAT)
  assert dashboard.HISTORY.latest()[0]["race"] == "drawn", (
    "a non-stream request reads our own stream, so its race runs too"
  )
  SEEN.clear()
  response = await client.post("/v1/chat/completions", json={**CHAT, "stream": True})
  assert response.status_code == 200, response.text
  assert sorted(SEEN) == ["a", "b"], SEEN
  row = dashboard.HISTORY.latest()[0]
  assert row["race"] == "drawn", row
  results = sorted(attempt["result"] for attempt in row["attempts"])
  assert results == ["answered", "lost race"], row["attempts"]
  assert [
    attempt.get("race")
    for attempt in row["attempts"]
    if attempt["result"] == "answered"
  ] == ["won"]


async def test_a_non_stream_request_races_and_reads_our_stream(
  client: httpx.AsyncClient,
) -> None:
  """A request with no stream reads our own stream, so the race runs for it too."""
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 200, response.text
  assert {"a", "b"} <= set(SEEN), SEEN
  assert all(STREAMS), STREAMS
  assert response.json()["choices"][0]["message"]["content"] == "hi"
  row = dashboard.HISTORY.latest()[0]
  assert row["race"] == "drawn", row
  results = sorted(attempt["result"] for attempt in row["attempts"])
  assert results == ["answered", "lost race"], row["attempts"]


async def test_a_flagged_pool_holds_the_race(client: httpx.AsyncClient) -> None:
  """With each model flagged `streams: false`, one whole body answers and the gate holds."""
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "tier": {"TIER-D": ["a", "b"]},
        "models": {"a": {"streams": False}, "b": {"streams": False}},
      },
    }
  )
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 200, response.text
  row = dashboard.HISTORY.latest()[0]
  assert row["race"] == "stream", row
  assert len(SEEN) == 1 and STREAMS == [False], (SEEN, STREAMS)


async def test_a_runner_that_wins_takes_the_pin(client: httpx.AsyncClient) -> None:
  """A racing model that answers first reads as rce, the new pin code."""
  api.PARALLEL_ENABLED = False
  await client.post("/v1/chat/completions", json=CHAT)
  first = dashboard.HISTORY.latest()[0]["via"]
  assert dashboard.HISTORY.latest()[0]["race"] == "off", (
    "a plain request names the gate"
  )
  api.PARALLEL_ENABLED = True
  dashboard.HISTORY.clear()
  FAIL.add(first.partition("/")[2])
  response = await client.post("/v1/chat/completions", json={**CHAT, "stream": True})
  assert response.status_code == 200, response.text
  row = dashboard.HISTORY.latest()[0]
  assert row["via"] != first and row["transition"] == "rce", row


async def test_a_first_model_that_wins_keeps_the_pin(client: httpx.AsyncClient) -> None:
  """When the first model answers first, the row keeps its usual transition."""
  api.PARALLEL_ENABLED = False
  await client.post("/v1/chat/completions", json=CHAT)
  first = dashboard.HISTORY.latest()[0]["via"]
  api.PARALLEL_ENABLED = True
  dashboard.HISTORY.clear()
  runner = "p/b" if first == "p/a" else "p/a"
  FAIL.add(runner.partition("/")[2])
  response = await client.post("/v1/chat/completions", json={**CHAT, "stream": True})
  assert response.status_code == 200, response.text
  row = dashboard.HISTORY.latest()[0]
  assert row["via"] == first and row["transition"] is None, row
