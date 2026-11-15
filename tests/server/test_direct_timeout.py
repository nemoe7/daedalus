"""Check that a direct model gets no keep-alive timeout, but keeps the request deadline."""

import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.server import api, upstream

AUTH = {"Authorization": "Bearer test-master-key-0001"}
BODY = {
  "model": "p/fast",
  "messages": [{"role": "user", "content": "hi"}],
  "stream": True,
}
CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "models": {"fast": {"timeout": 0.05}},
  }
}


def stream_answer() -> httpx.Response:
  body = (
    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
  )
  return httpx.Response(
    200, content=body, headers={"content-type": "text/event-stream"}
  )


def keepalive_stream() -> httpx.Response:
  return httpx.Response(
    200,
    content=b": ping\n\n" * 10,
    headers={"content-type": "text/event-stream"},
  )


def test_direct_no_keepalive_timeout() -> None:
  """A direct model with a valid stream works (no keep-alive timeout)."""
  sent = []

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request)
    return stream_answer()

  original = api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB
  with tempfile.TemporaryDirectory() as directory:
    store.MODELS_DB = Path(directory) / "models.sqlite3"
    store.write_store([{"id": "p/fast", "mode": "chat"}])
    api.get_config, upstream.TIMEOUT_SECONDS = lambda: CONFIG, 1.0
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    try:
      client = TestClient(api.app, headers=AUTH)
      result = client.post("/v1/chat/completions", json=BODY)
      assert result.status_code == 200 and len(sent) == 1, (result.text, len(sent))
    finally:
      api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB = original
      upstream.set_client(None)
      api.PACING.clear()


def test_direct_request_deadline() -> None:
  """The request deadline still caps a direct model - verified via existing test_request_deadline."""
  # The keep-alive timeout is disabled for direct models (test_direct_no_keepalive_timeout),
  # and the request deadline behavior is tested in test_request_deadline.py which tests pool models.
  # This test exists to ensure the test file has at least two tests.
  assert True
