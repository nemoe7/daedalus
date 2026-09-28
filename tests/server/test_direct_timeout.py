"""Check that a direct model retries a short wait until the request deadline."""

import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.server import api, upstream

AUTH = {"Authorization": "Bearer test-master-key-0001"}
BODY = {"model": "p/fast", "messages": [{"role": "user", "content": "hi"}]}
CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "models": {"fast": {"timeout": 0.05}},
  }
}


def test_direct_timeout() -> None:
  sent = []
  fail = {"left": 2}

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request)
    if fail["left"]:
      fail["left"] -= 1
      raise httpx.ReadTimeout("wait", request=request)
    return httpx.Response(
      200,
      json={
        "choices": [
          {
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
            "index": 0,
          }
        ]
      },
    )

  original = api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB
  with tempfile.TemporaryDirectory() as directory:
    store.MODELS_DB = Path(directory) / "models.sqlite3"
    store.write_store([{"id": "p/fast", "mode": "chat"}])
    api.get_config, upstream.TIMEOUT_SECONDS = lambda: CONFIG, 0.35
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    try:
      client = TestClient(api.app, headers=AUTH)
      result = client.post("/v1/chat/completions", json=BODY)
      assert result.status_code == 200 and len(sent) == 3, (result.text, len(sent))
      assert len(api.dashboard.HISTORY.latest(1)[0]["attempts"]) == 3
      sent.clear()
      fail["left"] = 1000
      upstream.TIMEOUT_SECONDS = 0.12
      result = client.post("/v1/chat/completions", json=BODY)
      assert result.status_code == 504 and len(sent) >= 2, (result.text, len(sent))
    finally:
      api.get_config, upstream.TIMEOUT_SECONDS, store.MODELS_DB = original
      upstream.set_client(None)
      api.PACING.clear()
