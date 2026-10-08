"""The client headers go on to the provider, except credentials, transport and proxy headers."""

import json
from pathlib import Path

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.server import access, api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
SEEN: list[httpx.Headers] = []
SENT = {
  "Authorization": f"Bearer {MASTER}",
  "HTTP-Referer": "https://kilocode.ai",
  "X-Title": "Kilo Code",
  "User-Agent": "Kilo-Code/7.0",
  "X-Session-Affinity": "s1",
  "Cookie": "a=b",
  "X-Api-Key": "secret",
  "X-Forwarded-For": "100.64.0.9",
  "Tailscale-User-Login": "owner@example.com",
  "X-OpenWebUI-User-Email": "owner@example.com",
  "X-OpenWebUI-Chat-Id": "c1",
  "Accept-Encoding": "br",
}


def upstream(request: httpx.Request) -> httpx.Response:
  SEEN.append(request.headers)
  if request.url.path.endswith("/images/generations"):
    return httpx.Response(200, json={"created": 1, "data": [{"b64_json": "AA=="}]})
  if json.loads(request.content).get("stream"):
    delta = {
      "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]
    }
    body = f"data: {json.dumps(delta)}\n\ndata: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
  message = {"role": "assistant", "content": "hi"}
  return httpx.Response(
    200,
    json={
      "id": "x",
      "object": "chat.completion",
      "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    },
  )


@pytest.fixture
async def client():
  block = {
    "api_base": "https://p.test/v1",
    "api_key": "provider-key",
    "tier": {"TIER-D": ["x"]},
  }
  config.set_config({"p": block})
  store.write_store([{"id": "p/x"}, {"id": "p/img", "mode": "image_generation"}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  media.REPEATS.clear()
  dashboard.HISTORY.clear()
  SEEN.clear()
  async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport, base_url="http://t", headers=SENT
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)


def check(headers: httpx.Headers) -> None:
  assert headers["authorization"] == "Bearer provider-key", headers
  assert headers["http-referer"] == "https://kilocode.ai", headers
  assert headers["x-title"] == "Kilo Code", headers
  assert headers["user-agent"] == "Kilo-Code/7.0", headers
  assert headers["x-session-affinity"] == "s1", headers
  assert headers["x-openwebui-chat-id"] == "c1", headers
  hidden = (
    "cookie",
    "x-api-key",
    "x-forwarded-for",
    "tailscale-user-login",
    "x-openwebui-user-email",
  )
  for name in hidden:
    assert name not in headers, (name, headers)
  assert headers.get("accept-encoding") != "br", headers
  assert headers["content-type"] == "application/json", headers


async def test_client_headers(client: httpx.AsyncClient) -> None:
  """Chat, stream and media requests carry the app headers of the client, and the provider key."""
  chat = {"model": "daedalus/moros", "messages": [{"role": "user", "content": "hi"}]}
  response = await client.post("/v1/chat/completions", json=chat)
  assert response.status_code == 200, response.text
  response = await client.post("/v1/chat/completions", json={**chat, "stream": True})
  assert response.status_code == 200, response.text
  assert "hi" in response.text, response.text
  response = await client.post(
    "/v1/images/generations", json={"model": "p/img", "prompt": "a"}
  )
  assert response.status_code == 200, response.text
  assert len(SEEN) == 3, SEEN
  for headers in SEEN:
    check(headers)
  rows = dashboard.HISTORY.latest()
  assert [(row["app"], row["key"]) for row in rows] == [("Kilo", "master")] * 3, rows


def test_app_name() -> None:
  """The short app name comes from the header map, then from the title header."""
  found = [
    access.app_name(httpx.Headers(headers))
    for headers in (
      {"X-OpenWebUI-User-Name": "a", "User-Agent": "aiohttp"},
      {"HTTP-Referer": "https://kilocode.ai", "X-Title": "Kilo Code"},
      {"User-Agent": "Kilo-Code/7.0"},
      {"X-Title": "Some App With A Very Long Name Indeed"},
      {"User-Agent": "curl/8"},
    )
  ]
  assert found == ["OWUI", "Kilo", "Kilo", "Some App With A Very Lon", None], found


async def test_the_hook_writes_the_repeat_code(
  client: httpx.AsyncClient, state_folder
) -> None:
  """A repeat runs the hook again with the count, and the row shows the code of the hook."""
  api.RETRIES.clear()
  path = state_folder / "hooks" / "retry.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  api.REQUEST_HOOKS = {"on-request": "hooks/retry.py"}
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "tier": {"TIER-A": ["*"]},
      }
    }
  )
  store.write_store([{"id": "p/x"}, {"id": "p/y"}])
  body = {"model": "daedalus/auto", "messages": [{"role": "user", "content": "hi"}]}
  first = await client.post("/v1/chat/completions", json=body)
  assert first.status_code == 200, first.text
  second = await client.post("/v1/chat/completions", json=body)
  assert second.status_code == 200, second.text
  row = dashboard.HISTORY.latest(1)[0]
  assert row["transition"]["reason"] == "rt1", row
  assert row["retry"] == "1", row


async def test_a_named_pool_repeat_picks_another_model(
  client: httpx.AsyncClient, state_folder
) -> None:
  """A repeat of a named pool keeps the pool, drops the model that answered, and shows its code."""
  api.RETRIES.clear()
  path = state_folder / "hooks" / "retry.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  api.REQUEST_HOOKS = {"on-request": "hooks/retry.py"}
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "provider-key",
        "tier": {"TIER-A": ["*"]},
      }
    }
  )
  store.write_store([{"id": "p/x"}, {"id": "p/y"}])
  body = {
    "model": "daedalus/sophos",
    "messages": [{"role": "user", "content": "hi pool"}],
  }
  first = await client.post("/v1/chat/completions", json=body)
  assert first.status_code == 200, first.text
  row1 = dashboard.HISTORY.latest(1)[0]
  second = await client.post("/v1/chat/completions", json=body)
  assert second.status_code == 200, second.text
  row2 = dashboard.HISTORY.latest(1)[0]
  change = row2["transition"]
  assert change["from_model"] == row1["via"], (row1, row2)
  assert change["from_model"] != change["to_model"], change
  assert change["from_pool"] == change["to_pool"], change
  assert change["reason"] == "rt1", change
