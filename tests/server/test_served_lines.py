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
HOOKS = Path(__file__).resolve().parents[2] / "hooks"
REAL_HOOKS = ("served_model.py", "owui_auto_reasoning_effort.py")
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
  root = hooks.ROOT / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  for name in REAL_HOOKS:
    shutil.copy(HOOKS / name, root / name)
  config.set_config(
    {
      "p": {
        "api_base": "https://p.test/v1",
        "api_key": "k",
        "tier": {"TIER-B": ["a"]},
      }
    }
  )
  store.write_store([{"id": "p/a", "name": "Alpha", "supports_reasoning": 1}])
  api.PENALTIES.clear()
  api.COOLDOWNS.clear()
  api.PACING.clear()
  media.REPEATS.clear()
  dashboard.HISTORY.clear()
  CALLS.clear()
  api.REQUEST_HOOKS = {"on-chunk": [f"hooks/{name}" for name in REAL_HOOKS]}
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
  """The served model selections of one answer body, in order."""
  found = []
  for block in text.split("\n\n"):
    if not block.startswith("data: {") or '"daedalus"' not in block:
      continue
    found.append(json.loads(block[5:])["usage"]["daedalus"])
  return found


async def test_each_round_of_one_message_carries_its_own_line(
  client: httpx.AsyncClient,
) -> None:
  """Two calls each carry one served-model line, and equal models give equal lines."""
  first = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
  assert first.status_code == 200, first.text
  second = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
  assert second.status_code == 200, second.text
  rounds = [lines(first.text), lines(second.text)]
  assert [len(round_) for round_ in rounds] == [1, 1], rounds
  assert rounds[0] == rounds[1], rounds
  assert rounds[0][0]["model"] == "p/a", rounds


async def test_the_line_carries_the_routed_reasoning(
  client: httpx.AsyncClient,
) -> None:
  """The reasoning hook overrides the line, and a reasoning of none leaves it bare."""
  for asked, wanted in (("high", "Alpha · high"), ("none", "Alpha")):
    answer = await client.post(
      "/v1/chat/completions",
      json={**BODY, "stream": True, "reasoning_effort": asked},
    )
    assert answer.status_code == 200, answer.text
    selections = lines(answer.text)
    assert len(selections) == 1, selections
    assert selections[0]["line"] == wanted, (asked, selections)


async def test_the_line_follows_the_installed_files(
  client: httpx.AsyncClient,
) -> None:
  """The reasoning hook alone draws no line, and the served model hook alone draws a bare one."""
  both = [f"hooks/{name}" for name in REAL_HOOKS]
  cases = (
    (["served_model.py"], both[1:], None),
    (["owui_auto_reasoning_effort.py"], both[:1], "Alpha"),
    ([], both, "Alpha · high"),
  )
  for off, paths, wanted in cases:
    hooks.set_installed("hooks", off)
    api.REQUEST_HOOKS = {"on-chunk": paths}
    answer = await client.post(
      "/v1/chat/completions",
      json={**BODY, "stream": True, "reasoning_effort": "high"},
    )
    assert answer.status_code == 200, answer.text
    selections = lines(answer.text)
    if wanted is None:
      assert selections == [], (off, selections)
    else:
      assert [selection["line"] for selection in selections] == [wanted], (
        off,
        selections,
      )
  hooks.set_installed("hooks", [])


async def test_the_client_scope_gates_the_line_on_the_request(
  client: httpx.AsyncClient,
) -> None:
  """An installed file with the client scope runs for its client alone."""
  hooks.set_installed("hooks", [])
  api.REQUEST_HOOKS = {}
  body = {**BODY, "stream": True, "reasoning_effort": "high"}
  answer = await client.post(
    "/v1/chat/completions", json=body, headers={"x-openwebui-chat-id": "t1"}
  )
  assert answer.status_code == 200, answer.text
  # Name order of folder puts served model hook last, so line holds no effort.
  assert [selection["line"] for selection in lines(answer.text)] == ["Alpha"]
  other = await client.post("/v1/chat/completions", json=body)
  assert other.status_code == 200, other.text
  assert lines(other.text) == []


@pytest.mark.anyio
@pytest.mark.parametrize("previous", ["", "p/a"])
async def test_chunk_hook_receives_previous_model(
  client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, previous: str
) -> None:
  """A custom chunk hook receives the empty or saved previous-model value."""
  path = hooks.ROOT / "hooks" / "previous_context.py"
  path.write_text(
    "seen = []\n"
    "def on_chunk(value, *, context, **kwargs):\n"
    "  seen.append((context['previous'], context))\n"
    "  return value\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(api, "AFFINITY", True)
  monkeypatch.setattr(api, "REQUEST_HOOKS", {"on-chunk": ["hooks/previous_context.py"]})
  if previous:
    key = api.session_key(MASTER, BODY["messages"], {})
    api.PENALTIES.pin(key, BODY["model"], previous)
  response = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
  assert response.status_code == 200, response.text
  seen = hooks.load(path).seen
  assert seen and {value for value, _ in seen} == {previous}
  assert all("plan.previous" not in context for _, context in seen)
  assert CALLS == ["a"]
  chunks = [
    json.loads(block[5:])
    for block in response.text.split("\n\n")
    if block.startswith("data: {")
  ]
  assert "hi" in [
    choice.get("delta", {}).get("content")
    for chunk in chunks
    for choice in chunk.get("choices", [])
  ]
