"""Tests of the upstream error classes."""

import asyncio
import tempfile
from pathlib import Path

import httpx

from daedalus import store
from daedalus.server import upstream

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
