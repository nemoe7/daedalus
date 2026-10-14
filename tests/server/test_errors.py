"""Tests of the upstream error classes."""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.providers.base import frame
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
