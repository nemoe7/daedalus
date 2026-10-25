"""Fallback routing prefers another provider serving the same model inside each `(tier, order)` bucket."""

import json

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


@pytest.fixture
def clean_state():
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  api.PACING.clear()
  media.REPEATS.clear()
  dashboard.HISTORY.clear()
  yield
  set_client(None)
  config.set_config(None)


async def test_same_order_fallback_prefers_same_model_on_another_provider(
  clean_state,
) -> None:
  """When OpenRouter fails on Nemotron, Kilo's Nemotron runs before other order-1 models."""
  config.set_config(
    {
      "openrouter": {
        "api_base": "https://or.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
      "groq": {
        "api_base": "https://groq.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["openai/gpt-oss-120b"]},
      },
      "kilo": {
        "api_base": "https://kilo.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
    }
  )
  store.write_store(
    [
      {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"},
      {"id": "groq/openai/gpt-oss-120b"},
      {"id": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"},
    ]
  )
  api.PENALTIES.record("groq/openai/gpt-oss-120b", 0.9)
  api.PENALTIES.record("kilo/nvidia/nemotron-3-ultra-550b-a55b:free", 0.5)
  key = api.session_key(MASTER, [{"role": "user", "content": "hi"}], {})
  api.PENALTIES.pin(
    key,
    "daedalus/sophos",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
  )

  seen: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    model = json.loads(request.content)["model"]
    seen.append(f"{host}:{model}")
    if host == "or.test":
      return httpx.Response(503, json={"error": {"message": "overloaded"}})
    return httpx.Response(
      200,
      json={
        "id": "ok",
        "object": "chat.completion",
        "choices": [
          {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
          }
        ],
      },
    )

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as client:
      old_stay = api.PENALTIES.stay
      api.PENALTIES.stay = 0.999999
      try:
        response = await client.post(
          "/v1/chat/completions",
          json={
            "model": "daedalus/sophos",
            "messages": [{"role": "user", "content": "hi"}],
          },
        )
      finally:
        api.PENALTIES.stay = old_stay
  assert response.status_code == 200, response.text
  assert seen == [
    "or.test:nvidia/nemotron-3-ultra-550b-a55b:free",
    "kilo.test:nvidia/nemotron-3-ultra-550b-a55b:free",
  ]


async def test_configured_order_beats_same_model_on_higher_order(
  clean_state,
) -> None:
  """Order 1 modelB still runs before an order 2 copy of modelA when order 1 modelA fails."""
  config.set_config(
    {
      "openrouter": {
        "api_base": "https://or.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
      "groq": {
        "api_base": "https://groq.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["openai/gpt-oss-120b"]},
      },
      "kilo": {
        "api_base": "https://kilo.test/v1",
        "api_key": "k",
        "order": 2,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
    }
  )
  store.write_store(
    [
      {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"},
      {"id": "groq/openai/gpt-oss-120b"},
      {"id": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"},
    ]
  )
  key = api.session_key(MASTER, [{"role": "user", "content": "hi"}], {})
  api.PENALTIES.pin(
    key,
    "daedalus/sophos",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
  )

  seen: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    model = json.loads(request.content)["model"]
    seen.append(f"{host}:{model}")
    if host == "or.test":
      return httpx.Response(503, json={"error": {"message": "overloaded"}})
    return httpx.Response(
      200,
      json={
        "id": "ok",
        "object": "chat.completion",
        "choices": [
          {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
          }
        ],
      },
    )

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as client:
      old_stay = api.PENALTIES.stay
      api.PENALTIES.stay = 0.999999
      try:
        response = await client.post(
          "/v1/chat/completions",
          json={
            "model": "daedalus/sophos",
            "messages": [{"role": "user", "content": "hi"}],
          },
        )
      finally:
        api.PENALTIES.stay = old_stay
  assert response.status_code == 200, response.text
  assert seen == [
    "or.test:nvidia/nemotron-3-ultra-550b-a55b:free",
    "groq.test:openai/gpt-oss-120b",
  ]


async def test_mid_stream_fallback_prefers_same_model_within_order(
  clean_state,
) -> None:
  """When a stream ends early without [DONE], continuation prefers the same model on another provider."""
  config.set_config(
    {
      "openrouter": {
        "api_base": "https://or.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
      "groq": {
        "api_base": "https://groq.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["openai/gpt-oss-120b"]},
      },
      "kilo": {
        "api_base": "https://kilo.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["nvidia/nemotron-3-ultra-550b-a55b:free"]},
      },
    }
  )
  store.write_store(
    [
      {"id": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"},
      {"id": "groq/openai/gpt-oss-120b"},
      {"id": "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"},
    ]
  )
  api.PENALTIES.record("groq/openai/gpt-oss-120b", 0.9)
  api.PENALTIES.record("kilo/nvidia/nemotron-3-ultra-550b-a55b:free", 0.5)
  key = api.session_key(MASTER, [{"role": "user", "content": "hi"}], {})
  api.PENALTIES.pin(
    key,
    "daedalus/sophos",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
  )

  seen: list[str] = []

  def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    model = json.loads(request.content)["model"]
    seen.append(f"{host}:{model}")
    if host == "or.test":
      body = 'data: {"id":"1","choices":[{"index":0,"delta":{"content":"hel"}}]}\n\n'
      return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=body
      )
    body = (
      'data: {"id":"2","choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
      "data: [DONE]\n\n"
    )
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=AUTH
    ) as client:
      old_stay = api.PENALTIES.stay
      api.PENALTIES.stay = 0.999999
      try:
        response = await client.post(
          "/v1/chat/completions",
          json={
            "model": "daedalus/sophos",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
          },
        )
      finally:
        api.PENALTIES.stay = old_stay
  assert response.status_code == 200, response.text
  assert seen == [
    "or.test:nvidia/nemotron-3-ultra-550b-a55b:free",
    "kilo.test:nvidia/nemotron-3-ultra-550b-a55b:free",
  ]
