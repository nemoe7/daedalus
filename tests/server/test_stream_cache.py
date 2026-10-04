"""The stream cache: a non-stream client reads our own stream, with 1 retry without one."""

import json
import random
from collections.abc import AsyncIterator

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.providers.base import ProviderError
from daedalus.routing import router
from daedalus.server import api, stream
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
SEEN: list[dict] = []
PLAN: dict[str, str] = {}


def data(chunk: dict) -> bytes:
  return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def stream_body(parts: list[dict], done: bool = True) -> bytes:
  body = b"".join(data(part) for part in parts)
  return body + (b"data: [DONE]\n\n" if done else b"")


def whole_body(model: str) -> dict:
  return {
    "id": "raw-1",
    "object": "chat.completion",
    "created": 1,
    "model": model,
    "choices": [
      {
        "index": 0,
        "message": {"role": "assistant", "content": f"raw {model}"},
        "finish_reason": "stop",
      }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
  }


def upstream(request: httpx.Request) -> httpx.Response:
  """One model answers. A plan makes it refuse the stream, deny the key or stop early."""
  body = json.loads(request.content)
  SEEN.append(body)
  model = body["model"]
  plan = PLAN.get(model, "")
  if plan == "denied":
    return httpx.Response(401, json={"error": {"message": "bad key"}})
  if not body.get("stream"):
    return httpx.Response(200, json=whole_body(model))
  if plan == "refuse":
    return httpx.Response(400, json={"error": {"message": "no stream here"}})
  if plan == "cut":
    return httpx.Response(
      200,
      headers={"content-type": "text/event-stream"},
      content=stream_body(
        [{"choices": [{"index": 0, "delta": {"content": "half"}}]}], done=False
      ),
    )
  return httpx.Response(
    200,
    headers={"content-type": "text/event-stream"},
    content=stream_body(
      [
        {
          "id": "s-1",
          "model": model,
          "choices": [{"index": 0, "delta": {"role": "assistant", "content": "he"}}],
        },
        {
          "choices": [
            {"index": 0, "delta": {"content": "llo"}, "finish_reason": "stop"}
          ]
        },
        {
          "choices": [],
          "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
      ]
    ),
  )


@pytest.fixture
async def client():
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "tier": {"TIER-D": ["a", "b"]},
      },
    }
  )
  store.write_store([{"id": "p/a"}, {"id": "p/b"}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  dashboard.HISTORY.clear()
  SEEN.clear()
  PLAN.clear()
  # A request with no stream reads our own stream when it may race, so the race is on here.
  api.PARALLEL_ENABLED = True
  pick = api.PENALTIES.pick
  api.PENALTIES.pick = random.random
  try:
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
      set_client(outer)
      transport = httpx.ASGITransport(app=api.app)
      async with httpx.AsyncClient(
        transport=transport,
        base_url="http://t",
        headers={"Authorization": f"Bearer {MASTER}"},
      ) as inner:
        yield inner
  finally:
    api.PENALTIES.pick = pick
    set_client(None)
    config.set_config(None)


CHAT = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "hi"}]}
DIRECT = {"model": "p/a", "messages": [{"role": "user", "content": "hi"}]}


async def test_a_non_stream_client_reads_our_stream(client: httpx.AsyncClient) -> None:
  """A race-eligible request with no stream asks for one, and the answer comes back whole."""
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 200, response.text
  answer = response.json()
  assert SEEN and all(body.get("stream") is True for body in SEEN), SEEN
  assert answer["object"] == "chat.completion"
  assert answer["choices"][0]["message"] == {"role": "assistant", "content": "hello"}
  assert answer["choices"][0]["finish_reason"] == "stop"
  assert answer["usage"] == {
    "prompt_tokens": 3,
    "completion_tokens": 2,
    "total_tokens": 5,
  }
  row = dashboard.HISTORY.latest()[0]
  assert row["tokens"]["output"] == 2 and row["tokens"]["estimate"] is False, row[
    "tokens"
  ]


async def test_a_refused_stream_retries_without_one(client: httpx.AsyncClient) -> None:
  """A 400 on the stream attempt reads as a compatibility sign, not a fault of the model."""
  api.PENALTIES.pick = lambda: 0.2
  PLAN["a"] = "refuse"
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 200, response.text
  assert [body.get("stream") for body in SEEN] == [True, None], SEEN
  assert response.json()["choices"][0]["message"]["content"] == "raw a"
  results = [attempt["result"] for attempt in dashboard.HISTORY.latest()[0]["attempts"]]
  assert results == ["stream refused", "answered"], results
  # A refusal is not a fault: the weight of the model stays whole.
  assert api.PENALTIES.weights(["p/a"])["p/a"] == 1.0


async def test_a_denied_key_keeps_its_own_path(client: httpx.AsyncClient) -> None:
  """A 401 is not a stream refusal, so a model makes no second attempt without a stream."""
  api.PENALTIES.pick = lambda: 0.2
  PLAN["a"] = PLAN["b"] = "denied"
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 401, response.text
  assert [body.get("stream") for body in SEEN] == [True, True], SEEN
  results = [attempt["result"] for attempt in dashboard.HISTORY.latest()[0]["attempts"]]
  assert results == ["HTTP 401", "HTTP 401"], results


async def test_a_flagged_model_skips_the_stream(client: httpx.AsyncClient) -> None:
  """`streams: false` on the model or the provider reads the whole body at once."""
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "models": {"a": {"streams": False}},
      },
    }
  )
  response = await client.post("/v1/chat/completions", json=DIRECT)
  assert response.status_code == 200, response.text
  assert response.json()["choices"][0]["message"]["content"] == "raw a"
  # The plain body carries no `stream` key at all.
  assert [body.get("stream") for body in SEEN] == [None], SEEN
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "streams": False,
      },
    }
  )
  found = config.get_config()
  assert router.streams_allowed(found, "p/a") is False, "the provider flag counts"
  assert router.streams_allowed(found, "p/b") is False, (
    "the provider flag counts for each model"
  )


async def test_a_stream_without_done_falls_back(client: httpx.AsyncClient) -> None:
  """A stream that stops early hands the request to the next model of the chain."""
  api.PENALTIES.pick = lambda: 0.2
  PLAN["a"] = "cut"
  response = await client.post("/v1/chat/completions", json=CHAT)
  assert response.status_code == 200, response.text
  row = dashboard.HISTORY.latest()[0]
  assert row["via"] == "p/b" and row["fallbacks"] == "1", row
  assert response.json()["choices"][0]["message"]["content"] == "hello"


async def events(*values: str) -> AsyncIterator[str]:
  for value in values:
    yield value


def delta(**values: object) -> str:
  return json.dumps({"choices": [{"index": 0, "delta": values}]})


async def test_cache_merges_the_deltas() -> None:
  """The cache joins the text, the reasoning and the tool calls of one stream."""
  calls = json.dumps(
    {
      "choices": [
        {
          "index": 0,
          "delta": {
            "tool_calls": [
              {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "f", "arguments": '{"a"'},
              }
            ]
          },
        }
      ]
    }
  )
  tail = json.dumps(
    {
      "choices": [
        {
          "index": 0,
          "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]},
          "finish_reason": "tool_calls",
        }
      ],
      "id": "s-9",
      "created": 5,
    }
  )
  answer = await stream.cache(
    [],
    events(
      delta(role="assistant", content="he", reasoning_content="r"),
      delta(content="llo", reasoning_content="r2"),
      calls,
      tail,
      "[DONE]",
    ),
    "p/a",
  )
  assert answer["id"] == "s-9" and answer["created"] == 5 and answer["model"] == "p/a"
  message = answer["choices"][0]["message"]
  assert message["content"] == "hello" and message["reasoning_content"] == "rr2"
  assert message["tool_calls"][0]["id"] == "call_1"
  assert message["tool_calls"][0]["function"] == {"name": "f", "arguments": '{"a"1}'}
  assert answer["choices"][0]["finish_reason"] == "tool_calls"


async def test_cache_needs_the_done_marker() -> None:
  """A stream that stops before [DONE] is a failure, not a short answer."""
  with pytest.raises(ProviderError, match=r"without \[DONE\]"):
    await stream.cache([], events(delta(content="x")), "p/a")


async def test_cache_keeps_the_usage_and_rejects_an_error() -> None:
  """The last usage lands in the answer, and an error chunk stops the cache."""
  usage = json.dumps(
    {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
  )
  answer = await stream.cache([], events(delta(content="x"), usage, "[DONE]"), "p/a")
  assert answer["usage"] == {"prompt_tokens": 1, "completion_tokens": 2}
  with pytest.raises(ProviderError, match="Upstream stream error"):
    await stream.cache([], events(json.dumps({"error": {"message": "boom"}})), "p/a")
