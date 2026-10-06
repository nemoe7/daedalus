"""How many served-model lines the rounds of 1 message carry, through the shipped hook."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from daedalus import config, dashboard, store
from daedalus.providers import hooks
from daedalus.server import api, media
from daedalus.server.upstream import set_client

MASTER = "test-master-key-0001"
REAL_HOOK = Path(__file__).resolve().parents[2] / "config" / "hooks" / "served_model.py"
BODY = {"model": "daedalus/deinos", "messages": [{"role": "user", "content": "hi"}]}
CALLS: list[str] = []


def chunk(model: str, **values: object) -> str:
  return (
    "data: "
    + json.dumps({"id": "x", "model": model, "choices": [{"index": 0, **values}]})
    + "\n\n"
  )


def openrouter(request: httpx.Request) -> httpx.Response:
  """An OpenRouter-shaped stream: a role chunk, a text chunk, 1 finish chunk, `[DONE]`."""
  model = json.loads(request.content)["model"]
  CALLS.append(model)
  parts = [
    chunk(model, delta={"role": "assistant", "content": ""}, finish_reason=None),
    chunk(model, delta={"content": "hi"}, finish_reason=None),
    chunk(model, delta={"content": ""}, finish_reason="stop"),
    "data: [DONE]\n\n",
  ]
  return httpx.Response(
    200, headers={"content-type": "text/event-stream"}, text="".join(parts)
  )


@pytest.fixture
async def client(tmp_path: Path):
  root = hooks.CONFIG_DIR / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  shutil.copy(REAL_HOOK, root / "served_model.py")
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "k",
        "tier": {"TIER-B": ["a"]},
      }
    }
  )
  store.write_store([{"id": "p/a"}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  api.PACING.clear()
  media.REPEATS.clear()
  dashboard.HISTORY.clear()
  CALLS.clear()
  api.REQUEST_HOOKS = {"on-chunk": ["hooks/served_model.py"]}
  async with httpx.AsyncClient(transport=httpx.MockTransport(openrouter)) as outer:
    set_client(outer)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(
      transport=transport,
      base_url="http://t",
      headers={"Authorization": f"Bearer {MASTER}"},
    ) as inner:
      yield inner
  set_client(None)
  config.set_config(None)
  api.REQUEST_HOOKS = {}


def lines(text: str) -> list[dict]:
  """The served model picks of one answer body, in order."""
  found = []
  for block in text.split("\n\n"):
    if not block.startswith("data: {") or '"daedalus"' not in block:
      continue
    found.append(json.loads(block[5:])["usage"]["daedalus"])
  return found


async def test_each_round_of_one_message_carries_its_own_line(
  client: httpx.AsyncClient,
) -> None:
  """The 2 tool rounds of 1 message each carry a line, and 1 model makes the 2 lines equal.

  The client sends the same message id for both rounds, so the served-model Filter draws 1 row:
  it drops a line equal to the last line of that message.
  """
  first = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
  assert first.status_code == 200, first.text
  second = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
  assert second.status_code == 200, second.text
  rounds = [lines(first.text), lines(second.text)]
  assert [len(round_) for round_ in rounds] == [1, 1], rounds
  assert rounds[0] == rounds[1], rounds
  assert rounds[0][0]["model"] == "p/a", rounds
