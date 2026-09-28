"""Check that a switch keyword gives the pool session another model of the same tier."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import store
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
CONFIG = {
  "b": {"api_key": "k", "api_base": "https://b.test/v1", "tier": {"TIER-B": ["*"]}}
}
ROWS = [{"id": m} for m in ("b/1", "b/2", "b/3")]
FIRST = {"role": "user", "content": "hi"}


class Lines(logging.Handler):
  """The log lines of one test."""

  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(record.getMessage())


LINES = Lines()


def answer(request: httpx.Request) -> httpx.Response:
  model = f"b/{json.loads(request.content)['model']}"
  message = {"role": "assistant", "content": model}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": model, "choices": [choice]})


def ask(
  client: TestClient, text: str | None = None, model: str = "daedalus/deinos"
) -> str:
  messages = [FIRST]
  if text:
    messages += [
      {"role": "assistant", "content": "ok"},
      {"role": "user", "content": text},
    ]
  body = {"model": model, "messages": messages}
  response = client.post("/v1/chat/completions", json=body)
  assert response.status_code == 200, response.text
  return response.json()["choices"][0]["message"]["content"]


def test_switch(client: TestClient) -> None:
  first = ask(client)
  assert ask(client, "go on") == first, "the session keeps its model"
  second = ask(client, "You CLANKER, again")
  assert second != first, "a switch keyword gives another model"
  assert "pin=switched" in LINES.lines[-1], LINES.lines[-1]
  assert ask(client, "go on") == second, "the session keeps the new model"
  assert ask(client, "clankers") == second, "only a whole word matches"
  assert ask(client, "clanker", "b/1") == "b/1", "a direct model does not change"


def test_last_fallback(client: TestClient) -> None:
  api.PENALTIES.clear()
  store.write_store([{"id": "b/1"}])
  first = ask(client)
  assert ask(client, "clanker") == first, "a tier of 1 model keeps that model"


@pytest.fixture(scope="module")
def client():
  store.write_store(ROWS)
  logger = logging.getLogger("daedalus")
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api, "get_config", lambda: CONFIG)
    patch.setattr(api, "AFFINITY", True)
    patch.setattr(api, "SWITCH", api.keyword_pattern(["clanker"]))
    patch.setattr(api.PENALTIES, "pick", lambda: 0.5)
    patch.setattr(logger, "level", logger.level)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    logger.addHandler(LINES)
    logger.setLevel(logging.INFO)
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
    logger.removeHandler(LINES)
  upstream.set_client(None)
