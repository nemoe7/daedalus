"""Check the parallel race: the first content wins, and the loser stops."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard, store
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
CONFIG = {
  "b": {"api_key": "k", "api_base": "https://b.test/v1", "tier": {"TIER-B": ["*"]}}
}
ROWS = [{"id": m} for m in ("b/1", "b/2", "b/3")]
FIRST = {"role": "user", "content": "hi"}
# The seconds before the first content of each model, the seconds before its end, and the
# models that the client called.
PLAN: dict[str, float] = {}
TAIL: dict[str, float] = {}
CALLS: list[str] = []


class Feed(httpx.AsyncByteStream):
  """The stream of 1 model: a wait, then 1 chunk with text, then the end."""

  def __init__(self, model: str) -> None:
    self.model = model

  async def __aiter__(self):
    await asyncio.sleep(PLAN.get(self.model, 0.0))
    chunk = {
      "id": self.model,
      "choices": [{"index": 0, "delta": {"content": self.model}}],
    }
    yield b"data: " + json.dumps(chunk).encode() + b"\n\n"
    await asyncio.sleep(TAIL.get(self.model, 0.0))
    yield b"data: [DONE]\n\n"


def answer(request: httpx.Request) -> httpx.Response:
  """The upstream answer of 1 model: a stream, or 1 whole answer without a stream."""
  body = json.loads(request.content)
  model = f"{request.url.host.split('.')[0]}/{body['model']}"
  CALLS.append(model)
  if not body.get("stream"):
    choice = {"index": 0, "message": {"role": "assistant", "content": model}}
    return httpx.Response(200, json={"id": model, "choices": [choice]})
  return httpx.Response(200, stream=Feed(model))


class Lines(logging.Handler):
  """The log lines of one test."""

  def __init__(self) -> None:
    super().__init__()
    self.lines: list[str] = []

  def emit(self, record: logging.LogRecord) -> None:
    self.lines.append(record.getMessage())


LINES = Lines()


def stream(client: TestClient, model: str) -> str:
  """Send 1 stream request and return the model that answered, from its log line."""
  body = {"model": model, "messages": [FIRST], "stream": True}
  with client.stream(
    "POST",
    "/v1/chat/completions",
    json=body,
    headers={"Authorization": f"Bearer {MASTER}"},
  ) as response:
    assert response.status_code == 200, response.read()
    for line in response.iter_lines():
      assert '"error"' not in line, line
  return LINES.lines[-1].split("via=")[1].split()[0]


def attempts() -> list[dict[str, Any]]:
  """The logged attempts of the newest request."""
  return dashboard.HISTORY.latest(1)[0]["attempts"]


@pytest.fixture(scope="module")
def client():
  store.write_store(ROWS)
  logger = logging.getLogger("daedalus")
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api, "get_config", lambda: CONFIG)
    patch.setattr(api, "AFFINITY", True)
    patch.setattr(api, "PARALLEL_ENABLED", True)
    patch.setattr(api, "PARALLEL_CHANCE", 0.0)
    patch.setattr(api, "PARALLEL_SLOW_SECONDS", 0.05)
    logger.addHandler(LINES)
    logger.setLevel(logging.INFO)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    yield TestClient(api.app)
    logger.removeHandler(LINES)
  upstream.set_client(None)


@pytest.fixture(autouse=True)
def fresh() -> None:
  """Each test starts with no pin, no weight change and no call list."""
  api.PENALTIES.clear()
  api.PENALTIES.race = True
  api.PENALTIES.pick = lambda: 0.0
  dashboard.HISTORY.clear()
  PLAN.clear()
  TAIL.clear()
  CALLS.clear()
  LINES.lines.clear()


def test_slow_first_token_starts_the_second_model(client: TestClient) -> None:
  """A first model with no content at `affinity.slow` loses the race to the model that answers."""
  PLAN.update({"b/1": 0.5, "b/2": 0.0})
  assert stream(client, "daedalus/deinos") == "b/2", "the first content wins"
  assert CALLS == ["b/1", "b/2"], (
    "the draw starts b/1, and the slow first token starts b/2"
  )
  # The hourly recovery moves the weight by a few parts per million, so the check is loose.
  assert api.PENALTIES.weights(["b/1"])["b/1"] == pytest.approx(0.9, abs=1e-3), (
    "the loser takes the factor"
  )
  key = api.session_key(MASTER, [FIRST])
  assert api.PENALTIES.pinned(key, "daedalus/deinos") == "b/2", (
    "the winner takes the pin"
  )
  assert stream(client, "daedalus/deinos") == "b/2", (
    "a fast session model keeps the pin"
  )
  assert CALLS == ["b/1", "b/2", "b/2"], "the fast pin answers alone"


def test_chance_starts_the_second_model(client: TestClient) -> None:
  """A draw below `affinity.chance` starts the second model with the first one."""
  api.PARALLEL_CHANCE = 1.0
  try:
    PLAN.update({"b/1": 0.5, "b/2": 0.0})
    assert stream(client, "daedalus/deinos") == "b/2", "the first content wins"
    assert CALLS == ["b/1", "b/2"], "both models start at once"
    assert {(a["model"], a["result"]) for a in attempts()} == {
      ("b/1", "lost race"),
      ("b/2", "answered"),
    }, attempts()
  finally:
    api.PARALLEL_CHANCE = 0.0


def test_a_lost_racer_reads_slower_than_the_winner(client: TestClient) -> None:
  """The winner's row reads its first content, so a loser that stops later reads the larger time."""
  api.PARALLEL_CHANCE = 1.0
  try:
    PLAN.update({"b/1": 1.0, "b/2": 0.0})
    TAIL.update({"b/2": 0.05})
    body = {"model": "daedalus/deinos", "messages": [FIRST]}
    response = client.post(
      "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {MASTER}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "b/2", response.text
    seconds = {attempt["result"]: attempt["seconds"] for attempt in attempts()}
    assert seconds["lost race"] >= seconds["answered"], seconds
  finally:
    api.PARALLEL_CHANCE = 0.0


def test_count_races_that_many_models(client: TestClient) -> None:
  """`affinity.count` 2 starts 2 models beside the original one."""
  api.PARALLEL_COUNT = 2
  try:
    PLAN.update({"b/1": 0.5, "b/2": 0.5, "b/3": 0.0})
    assert stream(client, "daedalus/deinos") == "b/3", "the first content wins"
    assert CALLS == ["b/1", "b/2", "b/3"], CALLS
    results = {(a["model"], a["result"]) for a in attempts()}
    assert results == {
      ("b/1", "lost race"),
      ("b/2", "lost race"),
      ("b/3", "answered"),
    }, results
    weights = api.PENALTIES.weights(["b/1", "b/2"])
    assert weights["b/1"] == pytest.approx(0.9, abs=1e-3), weights
    assert weights["b/2"] == pytest.approx(0.9, abs=1e-3), weights
  finally:
    api.PARALLEL_COUNT = 1


def test_off_keeps_the_session_model(client: TestClient) -> None:
  """`affinity.mode` session keeps the session model, and no second model starts."""
  api.PARALLEL_ENABLED = False
  api.PENALTIES.race = False
  try:
    PLAN.update({"b/1": 0.0, "b/2": 0.0})
    assert stream(client, "daedalus/deinos") == "b/1"
    CALLS.clear()
    assert stream(client, "daedalus/deinos") == "b/1", "the session model stays"
    assert CALLS == ["b/1"], CALLS
  finally:
    api.PARALLEL_ENABLED = True
    api.PENALTIES.race = True


def test_direct_model_does_not_race(client: TestClient) -> None:
  """A `provider/slug` request starts 1 model only."""
  PLAN.update({"b/1": 0.5, "b/2": 0.0})
  with client.stream(
    "POST",
    "/v1/chat/completions",
    json={"model": "b/1", "messages": [FIRST], "stream": True},
    headers={"Authorization": f"Bearer {MASTER}"},
  ) as response:
    assert response.status_code == 200
    response.read()
  assert CALLS == ["b/1"], CALLS


def test_direct_model_repeat_does_not_race(
  client: TestClient, state_folder: Path
) -> None:
  """A repeated `provider/slug` request stays direct when the affinity race is on."""
  path = state_folder / "hooks" / "retry.py"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  api.REQUEST_HOOKS = {"on-request": "hooks/retry.py"}
  api.PARALLEL_CHANCE = 1.0
  api.RETRIES.clear()
  try:
    body = {"model": "b/1", "messages": [FIRST], "stream": True}
    headers = {
      "Authorization": f"Bearer {MASTER}",
      "X-OpenWebUI-Chat-Id": "direct-chat",
    }
    for _ in range(2):
      with client.stream(
        "POST", "/v1/chat/completions", json=body, headers=headers
      ) as response:
        assert response.status_code == 200
        response.read()
    assert CALLS == ["b/1", "b/1"], CALLS
  finally:
    api.REQUEST_HOOKS = {}
    api.PARALLEL_CHANCE = 0.0
    api.RETRIES.clear()


def test_no_stream_reads_our_stream_and_races(client: TestClient) -> None:
  """A request without a stream races too, and its answer comes back as 1 whole body."""
  PLAN.update({"b/1": 0.5, "b/2": 0.0})
  body = {"model": "daedalus/deinos", "messages": [FIRST]}
  response = client.post(
    "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {MASTER}"}
  )
  assert response.status_code == 200, response.text
  assert CALLS == ["b/1", "b/2"], CALLS
  answer = response.json()
  assert answer["object"] == "chat.completion", answer
  assert answer["choices"][0]["message"]["content"] == "b/2", answer
  assert dashboard.HISTORY.latest(1)[0]["stream"] is False


def test_tie_keeps_the_first_model() -> None:
  """Content from both models in the same batch gives the pin to the model that started first."""

  async def work() -> None:
    first = asyncio.create_task(asyncio.sleep(0, result="b/1"))
    second = asyncio.create_task(asyncio.sleep(0, result="b/2"))
    await asyncio.sleep(0)
    takes = [api.Try("b/1", {}, first), api.Try("b/2", {}, second)]
    winner = await api.first_winner(takes)
    assert winner is not None
    assert winner.model == "b/1", "the model of the request keeps a tie"

  asyncio.run(work())


def test_race_starts_the_session_model() -> None:
  """With the race on, the session model starts the request and the draw keeps its share."""
  store_penalties = api.PENALTIES
  store_penalties.clear()
  store_penalties.pin("key", "slot", "b/2")
  assert store_penalties.race is True
  assert store_penalties.order([["b/1", "b/2"]], "key", "slot")[0] == "b/2", (
    "the pin starts"
  )
  store_penalties.race = False
  assert store_penalties.order([["b/1", "b/2"]], "key", "slot")[0] == "b/1", (
    "the draw decides"
  )
  store_penalties.race = True
