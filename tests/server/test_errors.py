"""Tests of the upstream error classes."""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import providers, store
from daedalus.providers.base import error_detail, error_text, frame
from daedalus.server import api, upstream

CONFIG = {"groq": {"api_key": "q", "api_base": "https://groq.test/openai/v1"}}
BODY = {"messages": [{"role": "user", "content": "hi"}]}
EXPECTED = {
  400: upstream.BadRequestError,
  401: upstream.AuthenticationError,
  403: upstream.AuthenticationError,
  404: upstream.NotFoundError,
  409: upstream.UpstreamStatus,
  422: upstream.BadRequestError,
  429: upstream.RateLimitError,
  500: upstream.ServerError,
  503: upstream.ServerError,
}


def raised(call) -> upstream.UpstreamStatus:
  """The upstream error that one call raises."""
  try:
    asyncio.run(call())
  except upstream.UpstreamStatus as exc:
    return exc
  raise AssertionError("no upstream error")


def test_a_rejected_body_reaches_limit_tracking(monkeypatch) -> None:
  """A streamed and a plain 429 give their body to limit tracking."""
  seen: list[tuple[str, bytes]] = []

  class Recorder:
    def observe(self, lane: str, _headers: httpx.Headers, body: bytes = b"") -> None:
      seen.append((lane, body))

  def answer(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"error": {"details": ["quota"]}})

  monkeypatch.setattr(upstream, "LIMITS", Recorder())
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  try:
    raised(lambda: upstream.attempt("groq/m", BODY, CONFIG))
    raised(lambda: upstream.post("groq/m", "https://groq.test/x", {}))
  finally:
    upstream.set_client(None)
  expected = b'{"error":{"details":["quota"]}}'
  assert seen == [("groq/m", expected), ("groq/m", expected)]


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize(
  "choices", [[1], [{"message": "bad", "delta": "bad"}], {"bad": "shape"}]
)
def test_malformed_answer_keeps_healthy_fallback(monkeypatch, streamed, choices):
  """Malformed plain answers and first stream events do not stop fallback."""
  setup = {
    "first": {"api_key": "k", "api_base": "https://invalid.test/v1"},
    "second": {"api_key": "k", "api_base": "https://answers.test/v1"},
  }
  sent = []

  def answer(request):
    sent.append(request.url.host)
    if request.url.host == "invalid.test":
      data = {"id": "bad", "choices": choices}
    else:
      key = "delta" if streamed else "message"
      data = {
        "id": "good",
        "choices": [
          {
            "index": 0,
            key: {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
          }
        ],
      }
    if streamed:
      return httpx.Response(
        200,
        content=frame(data) + b"data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
      )
    return httpx.Response(200, json=data)

  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  api.PACING.clear()
  store.write_store([{"id": "first/a"}, {"id": "second/b"}])
  monkeypatch.setattr(api, "get_config", lambda: setup)
  monkeypatch.setattr(
    api, "chain", lambda *args, **kwargs: ([["first/a", "second/b"]], "daedalus/koinos")
  )
  monkeypatch.setattr(api.PENALTIES, "select", lambda: 0.0)
  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  try:
    with TestClient(
      api.app, headers={"Authorization": "Bearer test-master-key-0001"}
    ) as client:
      response = client.post(
        "/v1/chat/completions",
        json={
          "model": "daedalus/koinos",
          "messages": [{"role": "user", "content": "hi"}],
          "stream": streamed,
        },
      )
    assert response.status_code == 200
    assert sent == ["invalid.test", "answers.test"]
    if streamed:
      lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
      ]
      assert lines[-1] == "[DONE]"
      assert json.loads(lines[0])["choices"][0]["delta"]["content"] == "ok"
    else:
      assert response.json()["choices"][0]["message"]["content"] == "ok"
  finally:
    upstream.set_client(None)


def test_errors() -> None:
  status = {"code": 200}

  def answer(request: httpx.Request) -> httpx.Response:
    headers = {"retry-after": "7"}
    return httpx.Response(status["code"], json={"error": "no"}, headers=headers)

  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  original = store.MODELS_DB
  folder = tempfile.TemporaryDirectory()
  store.MODELS_DB = Path(folder.name) / "models.sqlite3"
  try:
    for code, kind in EXPECTED.items():
      status["code"] = code
      streamed = raised(lambda: upstream.attempt("groq/m", BODY, CONFIG))
      direct = raised(lambda: upstream.post("groq/m", "https://groq.test/x", {}))
      for exc in (streamed, direct):
        assert type(exc) is kind, (code, type(exc))
        assert exc.status == code and exc.headers["Retry-After"] == "7", exc.headers
        assert exc.body == b'{"error":"no"}', exc.body
  finally:
    upstream.set_client(None)
    store.MODELS_DB = original
    folder.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["read-error", "cancel", "complete-error", "success"])
async def test_attempt_response_ownership(monkeypatch, mode) -> None:
  """Rejected responses close after read failure or cancellation, while successful responses remain caller-owned."""
  entered = asyncio.Event()
  failed = httpx.ReadError("synthetic body failure")
  raw = b'{"error":"no"}'
  observed = []

  class Body(httpx.AsyncByteStream):
    def __init__(self):
      self.closed = 0

    async def __aiter__(self):
      yield raw
      if mode == "read-error":
        raise failed
      if mode == "cancel":
        entered.set()
        await asyncio.Event().wait()

    async def aclose(self):
      self.closed += 1

  body = Body()
  response = httpx.Response(
    200 if mode == "success" else 429,
    stream=body,
    headers={"retry-after": "7"},
  )
  monkeypatch.setattr(
    upstream,
    "observe",
    lambda lane, headers, raw=b"": observed.append((lane, raw)),
  )
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(lambda request: response)
  ) as client:
    upstream.set_client(client)
    try:
      if mode == "cancel":
        task = asyncio.create_task(upstream.attempt("groq/m", BODY, CONFIG))
        try:
          await asyncio.wait_for(entered.wait(), 5)
        finally:
          task.cancel()
          with pytest.raises(asyncio.CancelledError):
            await task
        assert observed == []
      elif mode == "read-error":
        with pytest.raises(httpx.ReadError) as caught:
          await upstream.attempt("groq/m", BODY, CONFIG)
        assert caught.value is failed
        assert observed == []
      elif mode == "complete-error":
        with pytest.raises(upstream.RateLimitError) as caught:
          await upstream.attempt("groq/m", BODY, CONFIG)
        assert caught.value.status == 429 and caught.value.body == raw
        assert caught.value.headers["retry-after"] == "7"
        assert observed == [("groq/m", raw)]
      else:
        _, returned = await upstream.attempt("groq/m", BODY, CONFIG)
        assert returned is response and not response.is_closed and body.closed == 0
        assert observed == [("groq/m", b"")]
      if mode != "success":
        assert response.is_closed and body.closed == 1
    finally:
      await response.aclose()
      upstream.set_client(None)


GEMINI = (
  b'{"error": {"code": 429, "message": "You exceeded your current quota, please check your plan'
  b' and billing details.\\nPlease retry in 10.328642999s.", '
  b'"status": "RESOURCE_EXHAUSTED", "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",'
  b' "retryDelay": "10s"}]}}'
)
DAILY = (
  b'{"error": {"message": "Rate limit exceeded: limit_rpd/m. Daily limit reached for m via P.'
  b' Credits don\'t affect this cap.", "code": 429, "metadata": {"headers":'
  b' {"X-RateLimit-Reset": "1792368000000"}, "remedy_hint": "Retry shortly"}}}'
)
HARNESS = (
  b'{"error": {"message": "m is only available on agentic harnesses. Try plugging it into a coding'
  b' agent", "code": 403, "metadata": {"routing_funnel": [{"step": "Initial Endpoints"}],'
  b' "failed_routing_step": "Gate Free Endpoints by Agentic Harness"}}}'
)
NVIDIA = "Upstream error from Nvidia: Service temporarily overloaded"


def test_mapped_provider_behaviors_keep_safe_lines() -> None:
  """Known provider failures map to one behavior each, with a safe user line."""
  behavior = upstream.mapped_behavior(429, {}, GEMINI)
  assert isinstance(behavior, upstream.RateLimited), behavior
  assert behavior.retry_seconds == 10
  assert behavior.safe_line == "Rate limited by the provider. Retry in 10s."
  behavior = upstream.mapped_behavior(429, {}, DAILY)
  assert isinstance(behavior, upstream.DailyCap), behavior
  assert behavior.safe_line == (
    "Daily limit reached. Resets "
    + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(1792368000))
    + "."
  )
  behavior = upstream.mapped_behavior(403, {}, HARNESS)
  assert isinstance(behavior, upstream.HarnessGate), behavior
  assert behavior.safe_line == error_text(HARNESS)
  behavior = upstream.mapped_behavior(503, {}, json.dumps({"error": NVIDIA}).encode())
  assert isinstance(behavior, providers.UpstreamOverload), behavior
  assert behavior.safe_line == "The Nvidia upstream is temporarily overloaded."
  mistral_capacity = json.dumps(
    {
      "object": "error",
      "message": "Not enough capacity available for this request, please retry later.",
      "type": "backend_out_of_capacity",
      "param": None,
      "code": "3505",
      "raw_status_code": 429,
    }
  ).encode()
  behavior = upstream.mapped_behavior(429, {}, mistral_capacity)
  assert isinstance(behavior, providers.UpstreamOverload), behavior
  assert behavior.safe_line == "The upstream provider is temporarily overloaded."
  kilo_tools = json.dumps(
    {
      "error": {
        "message": "Invalid value for 'tools': maximum number of items is 128.",
        "type": "AI_APICallError",
        "param": {"tools": ["fn"]},
      },
      "providerMetadata": {
        "gateway": {
          "routing": {
            "originalModelId": "reflection/beam-free",
            "resolvedProvider": "reflection",
            "canonicalSlug": "reflection/beam",
            "providerRequestId": "req_secret_123",
          }
        }
      },
    }
  ).encode()
  behavior = upstream.mapped_behavior(400, {}, kilo_tools)
  assert isinstance(behavior, upstream.GatewayError), behavior
  assert behavior.safe_line == (
    "Upstream reflection rejected the request: "
    "Invalid value for 'tools': maximum number of items is 128."
  )
  assert "req_secret_123" not in behavior.safe_line
  assert "providerMetadata" not in behavior.safe_line
  openrouter_raw = json.dumps(
    {
      "error": {
        "message": "Provider returned error",
        "code": 400,
        "metadata": {
          "raw": '{"code":400,"msg":"bad request","request_id":"req_or_456"}',
          "provider_name": "AtlasCloud",
          "is_byok": False,
        },
      },
      "user_id": "user_secret_789",
    }
  ).encode()
  behavior = upstream.mapped_behavior(400, {}, openrouter_raw)
  assert isinstance(behavior, upstream.GatewayError), behavior
  assert behavior.safe_line == "Upstream AtlasCloud returned an error: bad request"
  assert "user_secret_789" not in behavior.safe_line
  assert "req_or_456" not in behavior.safe_line
  assert (
    upstream.mapped_behavior(418, {}, b'{"error": {"message": "something new"}}')
    is None
  )


def test_failure_notes_hide_bodies_only_for_mapped_behaviors() -> None:
  """Mapped behaviors use safe lines. Unknown behaviors keep the full relay message."""
  started = time.perf_counter()
  mapped = upstream.status_error(
    429, error_detail(GEMINI, upstream.DETAIL_LIMIT), {}, GEMINI
  )
  note = upstream.failure_note("groq/m", started, mapped)
  assert note["error"] == "Rate limited by the provider. Retry in 10s.", note
  assert "RESOURCE_EXHAUSTED" not in note["error"]
  raw = b'{"error": {"message": "something new", "code": 418}}'
  unknown = upstream.status_error(
    418, error_detail(raw, upstream.DETAIL_LIMIT), {}, raw
  )
  assert unknown.behavior is None
  assert upstream.failure_note("groq/m", started, unknown)["error"] == error_detail(
    raw, upstream.DETAIL_LIMIT
  )


def test_stream_refusal_notes_use_the_same_safe_line() -> None:
  """The stream-refused attempt note rides the shared status line, not the body."""
  api_source = (
    Path(__file__).resolve().parent.parent.parent / "daedalus/server/api.py"
  ).read_text(encoding="utf-8")
  assert 'upstream.note(model, "stream refused", began, upstream.status_line(exc))' in (
    api_source
  ), "the refusal note shares the behavior line"


def test_overloaded_stream_chunks_raise_the_overload_behavior() -> None:
  """A Nvidia-style overload chunk stops reading the stream as its own class."""
  from daedalus.server import stream

  with pytest.raises(providers.UpstreamOverload) as caught:
    stream.has_content(json.dumps({"error": {"message": NVIDIA}}))
  assert caught.value.safe_line == "The Nvidia upstream is temporarily overloaded."
  with pytest.raises(providers.ProviderError) as plain:
    stream.has_content(json.dumps({"error": {"message": "boom"}}))
  assert "Upstream stream error: boom" in str(plain.value)


def test_rejected_logs_safe_gateway_summary_without_raw_metadata(caplog) -> None:
  """Structured gateway rejections log the safe summary line instead of raw metadata or user IDs."""
  raw = json.dumps(
    {
      "error": {
        "message": "Provider returned error",
        "code": 400,
        "metadata": {
          "raw": '{"code":400,"msg":"bad request","request_id":"req_or_456"}',
          "provider_name": "AtlasCloud",
        },
      },
      "user_id": "user_secret_789",
    }
  ).encode()
  response = httpx.Response(400, content=raw)
  with caplog.at_level("WARNING"):
    err = upstream.rejected(
      "openrouter/qwen/qwen3-coder-next:free",
      response,
      time.perf_counter(),
      raw,
    )
  assert (
    upstream.status_line(err) == "Upstream AtlasCloud returned an error: bad request"
  )
  assert "Upstream AtlasCloud returned an error: bad request" in caplog.text
  assert "user_secret_789" not in caplog.text
  assert "req_or_456" not in caplog.text
