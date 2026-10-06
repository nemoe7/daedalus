import json
import tempfile
from pathlib import Path

import httpx

from daedalus import config, providers, store
from daedalus.server import api
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
AUTH = {"Authorization": f"Bearer {MASTER}"}


async def test_providers() -> None:
  seen = []
  mode = "text"
  native = {
    "candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
    "usageMetadata": {
      "promptTokenCount": 2,
      "candidatesTokenCount": 3,
      "totalTokenCount": 5,
    },
  }

  def upstream(request):
    body = json.loads(request.content)
    seen.append((request, body))
    if request.url.host == "failed.test":
      return httpx.Response(429, json={"error": {"message": "quota"}})
    if mode == "read-error" and request.url.host == "gemini.test":
      raise httpx.ReadTimeout("timeout", request=request)
    if mode == "invalid-json" and request.url.host == "gemini.test":
      return httpx.Response(200, content=b"invalid")
    if request.url.host == "gemini.test":
      if "streamGenerateContent" in request.url.path:
        event = (
          native
          if mode != "tool"
          else {
            "candidates": [
              {
                "content": {
                  "parts": [
                    {"functionCall": {"name": "weather", "args": {"city": "Manila"}}}
                  ]
                },
                "finishReason": "STOP",
              }
            ]
          }
        )
        raw = providers.frame(event).replace(b"\n", b"\r\n")
        if mode == "stream-error":
          raw = providers.frame({"error": {"message": "broken"}})
        if mode == "late-error":
          raw = providers.frame(
            {"candidates": [{"content": {"parts": [{"text": "partial"}]}}]}
          )
          raw += providers.frame({"error": {"message": "broken"}})
        return httpx.Response(
          200, content=raw, headers={"content-type": "text/event-stream"}
        )
      return httpx.Response(200, json=native)
    if body.get("stream"):
      chunk = {"choices": [{"index": 0, "delta": {"content": "hello"}}]}
      usage = {"choices": [], "usage": {"total_tokens": 5}}
      raw = providers.frame(chunk) + providers.frame(usage) + b"data: [DONE]\n\n"
      return httpx.Response(
        200, content=raw, headers={"content-type": "text/event-stream"}
      )
    return httpx.Response(
      200,
      json={
        "choices": [
          {
            "message": {"role": "assistant", "content": "hello"},
            "finish_reason": "stop",
          }
        ]
      },
    )

  configured = {
    "groq": {
      "api_base": "https://groq.test/v1",
      "api_key": "groq-key",
      "api_type": "openai",
    },
    "gemini": {
      "api_base": "https://gemini.test/v1beta",
      "api_key": "gemini-key",
      "api_type": "gemini",
      "tier": {"TIER-D": ["test"]},
    },
    "backup": {
      "api_base": "https://backup.test/v1",
      "api_key": "backup-key",
      "tier": {"TIER-D": ["test"]},
    },
    "failed": {
      "api_base": "https://failed.test/v1",
      "api_key": "failed-key",
      "tier": {"TIER-D": ["test"]},
    },
  }
  store.write_store([{"id": "groq/org/model"}, {"id": "gemini/test"}])
  config.set_config(configured)
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(upstream)
  ) as upstream_client:
    set_client(upstream_client)
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=api.app), base_url="http://test", headers=AUTH
    ) as client:

      async def send(model, **extra):
        api.PENALTIES.clear()
        api.PENALTIES.pick = lambda: 0.0
        return await client.post(
          "/v1/chat/completions",
          json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            **extra,
          },
          headers={
            "Authorization": f"Bearer {MASTER}",
            "x-goog-api-key": "client-google-key",
          },
        )

      for model in ("groq/org/model", "gemini/test"):
        result = await send(model)
        assert result.status_code == 200, result.text
        assert result.json()["choices"][0]["message"]["content"] == "hello"
      request, body = seen[0]
      assert str(request.url) == "https://groq.test/v1/chat/completions"
      assert request.headers["authorization"] == "Bearer groq-key"
      assert body["model"] == "org/model"
      assert "x-goog-api-key" not in request.headers
      request, body = seen[1]
      assert request.url.path == "/v1beta/models/test:generateContent"
      assert request.headers["x-goog-api-key"] == "gemini-key"
      assert "authorization" not in request.headers
      assert body["contents"][0]["parts"] == [{"text": "hi"}]
      for model in ("gemini/test",):
        response = await send(
          model, stream=True, stream_options={"include_usage": True}
        )
        assert response.status_code == 200, response.text
        data = [
          line[6:] for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert data[-1] == "[DONE]"
        chunks = [json.loads(line) for line in data[:-1]]
        assert chunks[-1]["usage"]["total_tokens"] == 5
      mode = "tool"
      response = await send("gemini/test", stream=True)
      assert '"tool_calls"' in response.text
      mode = "text"
      with tempfile.TemporaryDirectory() as directory:
        original = store.MODELS_DB
        store.MODELS_DB = Path(directory) / "models.sqlite3"
        store.write_store(
          [{"id": "gemini/test"}, {"id": "backup/test"}, {"id": "failed/test"}]
        )
        try:
          for mode in ("read-error", "invalid-json", "stream-error"):
            response = await send("daedalus/moros", stream=mode == "stream-error")
            assert response.status_code == 200, response.text
            assert seen[-1][0].url.host == "backup.test"
          mode = "late-error"
          count = len(seen)
          response = await send("daedalus/moros", stream=True)
          assert "partial" in response.text and "upstream_error" not in response.text
          assert response.text.rstrip().endswith("data: [DONE]")
          assert len(seen) == count + 2
          request, sent = seen[-1]
          assert request.url.host == "backup.test"
          assert sent["messages"][-1] == {"role": "assistant", "content": "partial"}, (
            sent
          )
          mode = "text"
          configured["gemini"]["api_key"] = ""
          response = await send("daedalus/moros")
          assert response.status_code == 200
          assert seen[-1][0].url.host == "backup.test"
          configured["gemini"]["api_key"] = "gemini-key"
          store.write_store([{"id": "failed/test"}])
          assert (await send("daedalus/moros")).status_code == 429
        finally:
          store.MODELS_DB = original
      assert (await send("unknown/test")).status_code == 400
      assert (await send("daedalus/unknown")).status_code == 400
      for invalid in ([], {}, {"model": "gemini/test", "messages": "hi"}):
        assert (
          await client.post("/v1/chat/completions", json=invalid)
        ).status_code == 400
      assert (
        await client.post("/v1/chat/completions", content=b"{")
      ).status_code == 400
      history = [
        {"role": "system", "content": "Be concise"},
        {
          "role": "user",
          "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
          ],
        },
        {
          "role": "assistant",
          "content": None,
          "tool_calls": [
            {
              "id": "call_1",
              "type": "function",
              "function": {"name": "weather", "arguments": '{"city":"Manila"}'},
            }
          ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
      ]
      for model in ("gemini/test",):
        response = await send(
          model,
          messages=history,
          tools=[
            {
              "type": "function",
              "function": {"name": "weather", "parameters": {"type": "object"}},
            }
          ],
          response_format={
            "type": "json_schema",
            "json_schema": {"name": "test", "schema": {"type": "object"}},
          },
          temperature=0.5,
        )
        assert response.status_code == 200, response.text
        sent = seen[-1][1]
        if model.startswith("gemini"):
          assert sent["contents"][0]["parts"][0]["inlineData"]["data"] == "YQ=="
          assert (
            sent["contents"][-1]["parts"][0]["functionResponse"]["name"] == "weather"
          )
          assert sent["generationConfig"]["responseJsonSchema"] == {"type": "object"}
  set_client(None)
  config.set_config(None)
