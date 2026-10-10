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


def test_the_effort_lands_on_a_name_of_the_model_list() -> None:
  """A model that lists its efforts takes the nearest name of the list, and never a stray one."""
  sent: list[httpx.Request] = []

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request)
    return httpx.Response(200, json={"choices": []})

  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  rows = [
    {
      "id": "groq/proxy",
      "mode": "chat",
      "supports_reasoning": 1,
      "supported_efforts": ["max", "high", "low"],
    },
    {
      "id": "groq/narrow",
      "mode": "chat",
      "supports_reasoning": 1,
      "supported_efforts": ["low", "high"],
    },
    {
      "id": "groq/stray",
      "mode": "chat",
      "supports_reasoning": 1,
      "supported_efforts": ["default"],
    },
    {"id": "groq/nolist", "mode": "chat", "supports_reasoning": 1},
  ]
  with tempfile.TemporaryDirectory() as name:
    original, store.MODELS_DB = store.MODELS_DB, Path(name) / "models.sqlite3"
    try:
      store.write_store(rows)
      assert sent_body(sent, "groq/proxy")["reasoning_effort"] == "high", (
        "the client asked high, and the list holds it"
      )
      for asked, wanted, why in (
        ("medium", "low", "a tie steps down"),
        ("minimal", "low", "minimal is closer to low than to high"),
        ("none", "low", "none sits under the list, so the lowest listed name"),
      ):
        asyncio.run(
          upstream.attempt("groq/narrow", {**BODY, "reasoning_effort": asked}, CONFIG)
        )
        assert json.loads(sent[-1].content)["reasoning_effort"] == wanted, (why, asked)
      asyncio.run(
        upstream.attempt("groq/stray", {**BODY, "reasoning_effort": "medium"}, CONFIG)
      )
      assert json.loads(sent[-1].content)["reasoning_effort"] == "medium", (
        "a list that the order does not know stays out of it"
      )
      asyncio.run(
        upstream.attempt("groq/nolist", {**BODY, "reasoning_effort": "max"}, CONFIG)
      )
      assert json.loads(sent[-1].content)["reasoning_effort"] == "max", (
        "a model with no list keeps the client value"
      )
      assert BODY["reasoning_effort"] == "high", "the client body does not change"
    finally:
      store.MODELS_DB = original
      upstream.set_client(None)


def test_the_none_rung_answers_only_a_none_request() -> None:
  """A model that lists `none` and a real rung maps a real level to the real rung."""
  sent: list[httpx.Request] = []

  def answer(request: httpx.Request) -> httpx.Response:
    sent.append(request)
    return httpx.Response(200, json={"choices": []})

  upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
  rows = [
    {
      "id": "groq/switch",
      "mode": "chat",
      "supports_reasoning": 1,
      "supported_efforts": ["none", "max"],
    },
    {
      "id": "groq/off",
      "mode": "chat",
      "supports_reasoning": 1,
      "supported_efforts": ["none"],
    },
  ]
  with tempfile.TemporaryDirectory() as name:
    original, store.MODELS_DB = store.MODELS_DB, Path(name) / "models.sqlite3"
    try:
      store.write_store(rows)
      for asked, wanted, why in (
        ("low", "max", "a real level takes the real rung, not the off switch"),
        ("medium", "max", "max is the only rung above none"),
        ("none", "none", "none maps to none when the model lists it"),
      ):
        asyncio.run(
          upstream.attempt("groq/switch", {**BODY, "reasoning_effort": asked}, CONFIG)
        )
        assert json.loads(sent[-1].content)["reasoning_effort"] == wanted, (why, asked)
      asyncio.run(
        upstream.attempt("groq/off", {**BODY, "reasoning_effort": "high"}, CONFIG)
      )
      assert json.loads(sent[-1].content)["reasoning_effort"] == "none", (
        "a list of none alone still answers none"
      )
    finally:
      store.MODELS_DB = original
      upstream.set_client(None)
