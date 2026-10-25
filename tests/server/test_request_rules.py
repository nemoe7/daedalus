import json

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}

TIER = {"TIER-D": ["x"]}
SEEN: list[tuple[str, dict]] = []


def upstream(request: httpx.Request) -> httpx.Response:
  SEEN.append((request.url.host, json.loads(request.content)))
  answer = {
    "id": "r1",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
  }
  return httpx.Response(200, json=answer)


async def send(client: httpx.AsyncClient, model: str) -> httpx.Response:
  SEEN.clear()
  body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
  return await client.post("/v1/chat/completions", json=body)


async def test_missing_key(client: httpx.AsyncClient) -> None:
  """A provider with no API key leaves every chain, with no attempt and no fault."""
  api.PENALTIES.clear()
  response = await send(client, "daedalus/moros")
  assert response.status_code == 200, response.text
  assert [host for host, _ in SEEN] == ["a.test"], SEEN
  attempts = dashboard.HISTORY.latest(1)[0]["attempts"]
  assert [a["model"] for a in attempts] == ["a/x"], attempts
  assert api.PENALTIES.weights(["b/x"]) == {"b/x": 1.0}, "no fault"
  response = await send(client, "b/x")
  assert response.status_code == 400, response.text
  assert response.json()["error"]["message"] == "Missing api_key for b.", response.text
  assert SEEN == [], "no upstream call"


async def test_default_effort(client: httpx.AsyncClient) -> None:
  """A request with no effort gets the stored effort of the model."""
  response = await send(client, "a/thinks")
  assert response.status_code == 200, response.text
  assert SEEN[0][1]["reasoning_effort"] == "high", SEEN
  SEEN.clear()
  body = {
    "model": "a/thinks",
    "messages": [{"role": "user", "content": "hi"}],
    "reasoning_effort": "low",
  }
  response = await client.post("/v1/chat/completions", json=body)
  assert SEEN[0][1]["reasoning_effort"] == "low", "the request value wins"
  await send(client, "a/plain")
  assert "reasoning_effort" not in SEEN[0][1], "a model that does not reason"


async def test_output_limit(client: httpx.AsyncClient) -> None:
  """An output limit above the limit of the model drops to that limit."""
  for field in ("max_tokens", "max_completion_tokens"):
    for asked, sent in ((9000, 4000), (100, 100)):
      SEEN.clear()
      body = {
        "model": "a/thinks",
        "messages": [{"role": "user", "content": "hi"}],
        field: asked,
      }
      response = await client.post("/v1/chat/completions", json=body)
      assert response.status_code == 200, response.text
      assert SEEN[0][1][field] == sent, (field, SEEN)
  SEEN.clear()
  body = {"model": "a/x", "messages": [{"role": "user", "content": "hi"}]}
  await client.post("/v1/chat/completions", json={**body, "max_tokens": 9000})
  assert SEEN[0][1]["max_tokens"] == 9000, "no stored limit, no change"


def test_vision() -> None:
  """An image request skips the models with a false or no vision value. A text request keeps them."""
  config_now = config.get_config()
  text = {"messages": [{"role": "user", "content": "hi"}]}
  image = {
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "what is this?"},
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ],
      },
      {"role": "assistant", "content": "a cat"},
      {"role": "user", "content": "and now?"},
    ]
  }
  every = ["v/eye", "v/blind", "v/unknown"]
  assert api.chain("daedalus/koinos", text, config_now)[0][0] == every
  first = api.chain("daedalus/koinos", image, config_now)[0][0]
  assert first == ["v/eye"], ("an image in an older turn", first)
  assert api.chain("v/blind", image, config_now)[0] == [["v/blind"]], "a direct request"


def test_beam_tool_limit() -> None:
  """Reflection beam models stay in tool chains up to 128 tools and leave above 128."""
  config_now = config.get_config()
  tool = {"type": "function", "function": {"name": "fn"}}
  at_cap = {
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [tool] * api.BEAM_TOOL_LIMIT,
  }
  over_cap = {
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [tool] * (api.BEAM_TOOL_LIMIT + 1),
  }
  assert api.chain("daedalus/sophos", at_cap, config_now)[0][0] == [
    "kilo/reflection/beam-free",
    "kilo/other/coder",
  ]
  assert api.chain("daedalus/sophos", over_cap, config_now)[0][0] == [
    "kilo/other/coder"
  ]


async def test_inkling_requires_supported_harness(client: httpx.AsyncClient) -> None:
  """Inkling models leave pool chains and reject direct calls unless headers name a supported harness."""
  config_now = config.get_config()
  body = {"messages": [{"role": "user", "content": "hi"}]}
  plain = api.chain("daedalus/sophos", body, config_now)[0][0]
  assert "openrouter/thinkingmachines/inkling:free" not in plain
  assert "openrouter/thinkingmachines/inkling-small:free" not in plain
  owui = api.chain(
    "daedalus/sophos",
    body,
    config_now,
    headers={"HTTP-Referer": "https://openwebui.com", "X-Title": "Open WebUI"},
  )[0][0]
  assert "openrouter/thinkingmachines/inkling:free" not in owui
  for harness_headers in (
    {"HTTP-Referer": "https://claude.ai", "X-Title": "Claude Code"},
    {"HTTP-Referer": "https://nousresearch.com", "X-OpenRouter-Title": "Hermes Agent"},
    {"HTTP-Referer": "https://kilocode.ai"},
    {"HTTP-Referer": "https://agent.example", "X-OpenRouter-Categories": "cli-agent"},
    {"User-Agent": "claude-cli/1.0"},
  ):
    allowed = api.chain("daedalus/sophos", body, config_now, headers=harness_headers)[
      0
    ][0]
    assert "openrouter/thinkingmachines/inkling:free" in allowed, harness_headers
    assert "openrouter/thinkingmachines/inkling-small:free" in allowed, harness_headers
  SEEN.clear()
  denied = await client.post(
    "/v1/chat/completions",
    json={"model": "openrouter/thinkingmachines/inkling:free", **body},
  )
  assert denied.status_code == 403, denied.text
  assert "agentic harness" in denied.json()["error"]["message"]
  assert SEEN == []
  served = await client.post(
    "/v1/chat/completions",
    json={"model": "openrouter/thinkingmachines/inkling:free", **body},
    headers={"HTTP-Referer": "https://claude.ai", "X-Title": "Claude Code"},
  )
  assert served.status_code == 200, served.text
  assert [host for host, _ in SEEN] == ["or.test"]


@pytest.fixture(scope="module", autouse=True)
def catalog():
  config.set_config(
    {
      "a": {"api_base": "https://a.test/v1", "api_key": "k", "tier": TIER},
      "b": {"api_base": "https://b.test/v1", "api_key": "", "tier": TIER},
      "kilo": {
        "api_base": "https://kilo.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {"TIER-A": ["reflection/beam-free", "other/coder"]},
      },
      "openrouter": {
        "api_base": "https://or.test/v1",
        "api_key": "k",
        "order": 1,
        "tier": {
          "TIER-A": [
            "thinkingmachines/inkling:free",
            "thinkingmachines/inkling-small:free",
          ]
        },
      },
      "v": {
        "api_base": "https://v.test/v1",
        "api_key": "k",
        "tier": {"TIER-C": ["eye", "blind", "unknown"]},
      },
    }
  )
  store.write_store(
    [
      {"id": "a/x"},
      {"id": "b/x"},
      {
        "id": "a/thinks",
        "supports_reasoning": True,
        "reasoning_effort": "high",
        "max_output_tokens": 4000,
      },
      {"id": "a/plain", "supports_reasoning": False, "reasoning_effort": "high"},
      {"id": "kilo/reflection/beam-free", "supports_function_calling": True},
      {"id": "kilo/other/coder", "supports_function_calling": True},
      {"id": "openrouter/thinkingmachines/inkling:free"},
      {"id": "openrouter/thinkingmachines/inkling-small:free"},
      {"id": "v/eye", "supports_vision": True},
      {"id": "v/blind", "supports_vision": False},
      {"id": "v/unknown"},
    ]
  )
  yield
  config.set_config(None)


@pytest.fixture
async def client():
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outside:
    set_client(outside)
    app = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=app, base_url="http://t", headers=AUTH
    ) as client:
      yield client
  set_client(None)
