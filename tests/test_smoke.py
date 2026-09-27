"""Runnable check for the proxy. Run: python tests/test_smoke.py"""

import asyncio
import json
import pathlib
import tempfile

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from daedalus import api, catalog, config, keys

UPSTREAM_BASE = "http://upstream.test"
UPSTREAM_KEY = "upstream-secret"
LOCAL_KEY = "local-secret"
SEEN: list[dict] = []


def make_upstream() -> FastAPI:
  """Stub upstream. Routes carry the `/v1` prefix, as a real upstream does."""
  stub = FastAPI()

  @stub.post("/v1/chat/completions")
  async def chat(request: Request) -> JSONResponse:
    payload = await request.json()
    SEEN.append(
      {
        "path": request.url.path,
        "query": request.url.query,
        "model": payload.get("model", ""),
        "messages": payload.get("messages"),
        "authorization": request.headers.get("authorization", ""),
        "user_agent": request.headers.get("user-agent", ""),
      }
    )
    if payload.get("model") == "bad":
      return JSONResponse({"error": {"message": "slow down"}}, status_code=429)
    if payload.get("stream"):

      async def chunks():
        for token in ("Hel", "lo"):
          data = {"choices": [{"delta": {"content": token}}]}
          yield f"data: {json.dumps(data)}\n\n".encode()
        yield b"data: [DONE]\n\n"

      return StreamingResponse(chunks(), media_type="text/event-stream")
    return JSONResponse(
      {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": payload["model"],
        "choices": [
          {
            "index": 0,
            "message": {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
          }
        ],
      }
    )

  @stub.post("/v1/models")
  async def models_error() -> JSONResponse:
    return JSONResponse(
      status_code=429,
      content={"error": {"message": "rate limited", "type": "rate_limit_error"}},
    )

  return stub


def use_config(api_key: str = UPSTREAM_KEY) -> None:
  """Route `stub/*` models to the stub upstream."""
  config.set_config(
    {
      "stub": {
        "api_base": UPSTREAM_BASE + "/v1",
        "api_key": api_key,
        "api_type": "openai",
      }
    }
  )


def use_upstream(api_key: str = UPSTREAM_KEY, local_key: str = "") -> None:
  """Point the app at the stub upstream."""
  use_config(api_key)
  keys.save_hash(catalog.MODELS_DB, keys.digest(local_key) if local_key else None)
  api.set_client(
    httpx.AsyncClient(
      transport=httpx.ASGITransport(app=make_upstream()),
      timeout=30.0,
    )
  )


def make_client() -> httpx.AsyncClient:
  return httpx.AsyncClient(
    transport=httpx.ASGITransport(app=api.app),
    base_url="http://testserver",
  )


def chat_body(**extra: object) -> dict:
  return {
    "model": "stub/gpt-test",
    "messages": [{"role": "user", "content": "hi"}],
    **extra,
  }


async def check_health(client: httpx.AsyncClient) -> None:
  response = await client.get("/health")
  assert response.status_code == 200, response.text
  body = response.json()
  assert body["status"] == "ok", body
  assert body == {"status": "ok"}, body


async def check_non_stream(client: httpx.AsyncClient) -> None:
  response = await client.post(
    "/v1/chat/completions",
    json=chat_body(),
    headers={"Authorization": "Bearer client-secret", "User-Agent": "daedalus-test"},
  )
  assert response.status_code == 200, response.text
  body = response.json()
  assert body["object"] == "chat.completion", body
  assert body["choices"][0]["message"]["content"] == "hi", body
  last = SEEN[-1]
  assert last["path"] == "/v1/chat/completions", last
  assert last["authorization"] == f"Bearer {UPSTREAM_KEY}", last
  assert last["user_agent"] != "daedalus-test", last
  assert last["model"] == "gpt-test", last


async def check_routed_model(client: httpx.AsyncClient) -> None:
  """A reserved name resolves to a provider model, and any other name passes on."""
  config.set_config(
    {
      "gemini": {
        "api_base": UPSTREAM_BASE + "/v1",
        "api_key": UPSTREAM_KEY,
        "api_type": "openai",
        "tier": {
          "TIER-C": ["gemini-3.5-flash"],
          "TIER-B": ["gemini-3.6-flash"],
          "TIER-A": ["gemini-3.5-pro"],
        },
      }
    }
  )
  saved = catalog.MODELS_DB
  try:
    with tempfile.TemporaryDirectory() as folder:
      catalog.MODELS_DB = pathlib.Path(folder) / "models.sqlite3"
      catalog.write_store(
        [{"id": "gemini/gemini-3.5-flash"}, {"id": "gemini/gemini-3.5-pro"}]
      )
      response = await client.post(
        "/v1/chat/completions",
        json=chat_body(
          model="daedalus/auto",
          messages=[
            {"role": "user", "content": "write a python function to parse a csv file"}
          ],
        ),
      )
      assert response.status_code == 200, response.text
      assert SEEN[-1]["model"] == "gemini-3.5-flash", SEEN[-1]
      # A pool skips the classifier and goes to its own tier.
      pooled = await client.post(
        "/v1/chat/completions",
        json=chat_body(model="daedalus/sophos"),
      )
      assert pooled.status_code == 200, pooled.text
      assert SEEN[-1]["model"] == "gemini-3.5-pro", SEEN[-1]
      # ADR 2: a tool request goes to praktos, and an empty praktos falls back.
      tool = {"type": "function", "function": {"name": "f", "parameters": {}}}
      for model in ("daedalus/praktos", "daedalus/auto"):
        empty = await client.post(
          "/v1/chat/completions", json=chat_body(model=model, tools=[tool])
        )
        assert empty.status_code == 200, empty.text
      catalog.write_store(
        [
          {"id": "gemini/gemini-3.5-flash", "supports_function_calling": True},
          {"id": "gemini/gemini-3.5-pro"},
          {"id": "gemini/gemini-3.6-flash", "supports_function_calling": True},
        ]
      )
      for model in ("daedalus/praktos", "daedalus/auto"):
        tooled = await client.post(
          "/v1/chat/completions", json=chat_body(model=model, tools=[tool])
        )
        assert tooled.status_code == 200, tooled.text
        assert SEEN[-1]["model"] == "gemini-3.6-flash", (model, SEEN[-1])
  finally:
    catalog.MODELS_DB = saved
    use_config()


async def check_reroute(client: httpx.AsyncClient) -> None:
  """A failed model logs internally, and the next model in the chain answers."""
  config.set_config(
    {
      "gemini": {
        "api_base": UPSTREAM_BASE + "/v1",
        "api_key": UPSTREAM_KEY,
        "api_type": "openai",
        "tier": {"TIER-B": ["bad", "gemini-3.5-flash"]},
      }
    }
  )
  saved = catalog.MODELS_DB
  try:
    with tempfile.TemporaryDirectory() as folder:
      catalog.MODELS_DB = pathlib.Path(folder) / "models.sqlite3"
      catalog.write_store([{"id": "gemini/bad"}, {"id": "gemini/gemini-3.5-flash"}])
      response = await client.post(
        "/v1/chat/completions", json=chat_body(model="daedalus/sophos")
      )
      assert response.status_code == 200, response.text
      assert SEEN[-2]["model"] == "bad", SEEN[-2]
      assert SEEN[-1]["model"] == "gemini-3.5-flash", SEEN[-1]

      # When the last model in the chain fails, the client sees that error.
      config.set_config(
        {
          "gemini": {
            "api_base": UPSTREAM_BASE + "/v1",
            "api_key": UPSTREAM_KEY,
            "api_type": "openai",
            "tier": {"TIER-B": ["bad"]},
          }
        }
      )
      catalog.write_store([{"id": "gemini/bad"}])
      doomed = await client.post(
        "/v1/chat/completions", json=chat_body(model="daedalus/sophos")
      )
      assert doomed.status_code == 429, doomed.text
  finally:
    catalog.MODELS_DB = saved
    use_config()


def check_wait_cap() -> None:
  """The wait for one answer is capped, and the other timeouts are not."""
  api.set_client(None)
  client = api.get_client()
  assert client.timeout.read == api.WAIT_SECONDS, client.timeout
  assert client.timeout.connect == api.TIMEOUT_SECONDS, client.timeout
  api.set_client(None)


async def check_models(client: httpx.AsyncClient) -> None:
  saved = catalog.MODELS_DB
  try:
    with tempfile.TemporaryDirectory() as folder:
      catalog.MODELS_DB = pathlib.Path(folder) / "models.sqlite3"
      catalog.write_store([{"id": "stub/gpt-test"}])
      response = await client.get("/v1/models")
  finally:
    catalog.MODELS_DB = saved
  assert response.status_code == 200, response.text
  names = [row["id"] for row in response.json()["data"]]
  assert names == [
    "daedalus/auto",
    "daedalus/moros",
    "daedalus/koinos",
    "daedalus/deinos",
    "daedalus/sophos",
    "daedalus/praktos",
    "stub/gpt-test",
  ], names


async def check_stream(client: httpx.AsyncClient) -> None:
  async with client.stream(
    "POST",
    "/v1/chat/completions",
    json=chat_body(stream=True),
  ) as response:
    assert response.status_code == 200, response.status_code
    assert response.headers["content-type"].startswith("text/event-stream")
    text = "".join([chunk async for chunk in response.aiter_text()])
  assert "Hel" in text, text
  assert "lo" in text, text
  assert "[DONE]" in text, text


async def check_unknown_models(client: httpx.AsyncClient) -> None:
  count = len(SEEN)
  for model in ("gpt-test", "unknown/gpt-test", "daedalus/unknown"):
    response = await client.post("/v1/chat/completions", json=chat_body(model=model))
    assert response.status_code == 400, response.text
  assert (await client.post("/v1/embeddings", json={})).status_code == 404
  assert len(SEEN) == count, SEEN


async def check_local_key() -> None:
  use_upstream(local_key=LOCAL_KEY)
  async with make_client() as client:
    wrong = await client.post(
      "/v1/chat/completions",
      json=chat_body(),
      headers={"Authorization": "Bearer wrong"},
    )
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["error"]["type"] == "authentication_error", wrong.text
    missing = await client.post("/v1/chat/completions", json=chat_body())
    assert missing.status_code == 401, missing.text
    allowed = await client.post(
      "/v1/chat/completions",
      json=chat_body(),
      headers={"Authorization": f"Bearer {LOCAL_KEY}"},
    )
    assert allowed.status_code == 200, allowed.text
  assert SEEN[-1]["authorization"] == f"Bearer {UPSTREAM_KEY}", SEEN[-1]


def broken(request: httpx.Request) -> httpx.Response:
  raise httpx.ConnectError("no route to upstream", request=request)


async def check_upstream_unreachable() -> None:
  use_upstream()
  api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(broken), timeout=5.0))
  async with make_client() as client:
    response = await client.post("/v1/chat/completions", json=chat_body())
  assert response.status_code == 502, response.text
  assert response.json()["error"]["type"] == "upstream_error", response.text


async def check_removed_routes(client: httpx.AsyncClient) -> None:
  for path in (
    "/v1beta/models/test:generateContent",
    "/v1beta/models/test:streamGenerateContent",
    "/v1beta/models/test:countTokens",
    "/v1beta/interactions",
  ):
    response = await client.post(path, json={})
    assert response.status_code == 404, response.text


async def run_checks() -> None:
  use_upstream()
  await run_client_checks()
  check_wait_cap()
  await check_local_key()
  await check_upstream_unreachable()


async def run_client_checks() -> None:
  async with make_client() as client:
    await check_health(client)
    await check_non_stream(client)
    await check_routed_model(client)
    await check_reroute(client)
    await check_models(client)
    await check_stream(client)
    await check_removed_routes(client)
    await check_unknown_models(client)


def main() -> None:
  with tempfile.TemporaryDirectory() as folder:
    catalog.MODELS_DB = pathlib.Path(folder) / "models.sqlite3"
    asyncio.run(run_checks())
  print(f"ok: {len(SEEN)} upstream requests, all checks passed")


if __name__ == "__main__":
  main()
