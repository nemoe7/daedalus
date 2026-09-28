"""Check that `reasoning_effort` goes only to models that reason."""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from daedalus import store
from daedalus.server import upstream

CONFIG = {"groq": {"api_key": "q", "api_base": "https://groq.test/openai/v1"}}
ROWS = [
  {"id": "groq/thinker", "mode": "chat", "supports_reasoning": 1},
  {"id": "groq/plain", "mode": "chat", "supports_reasoning": 0},
  {"id": "groq/unknown", "mode": "chat"},
]
BODY = {"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"}


def sent_body(sent: list[httpx.Request], model: str) -> dict:
  """Send 1 attempt for the model, and return the JSON that went upstream."""
  asyncio.run(upstream.attempt(model, BODY, CONFIG))
  return json.loads(sent[-1].content)


def test_reasoning() -> None:
  sent: list[httpx.Request] = []

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request)
    return httpx.Response(200, json={"choices": []})

  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  with tempfile.TemporaryDirectory() as name:
    original, store.MODELS_DB = store.MODELS_DB, Path(name) / "models.sqlite3"
    try:
      store.write_store(ROWS)
      assert sent_body(sent, "groq/thinker")["reasoning_effort"] == "high"
      assert "reasoning_effort" not in sent_body(sent, "groq/plain"), "no reasoning"
      assert "reasoning_effort" not in sent_body(sent, "groq/unknown"), "no value"
      kept = sent_body(sent, "groq/not-stored")
      assert kept["reasoning_effort"] == "high", "a model outside the store keeps it"
      assert BODY["reasoning_effort"] == "high", "the client body does not change"
    finally:
      store.MODELS_DB = original
      upstream.set_client(None)
