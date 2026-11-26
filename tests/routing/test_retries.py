"""An Open WebUI try again on daedalus/auto moves up 1 tier, and at tier A it moves to another model."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedalus import dashboard
from daedalus import store as model_store
from daedalus.providers import hooks
from daedalus.routing import retries, router
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
CHAT = {"X-OpenWebUI-Chat-Id": "chat-1"}
CONFIG = {
  name: {
    "api_key": "k",
    "api_base": f"https://{name}.test/v1",
    "tier": {tier: ["*"]},
  }
  for name, tier in (("a", "TIER-A"), ("b", "TIER-B"), ("c", "TIER-C"), ("d", "TIER-D"))
}
ROWS = [
  {"id": m} for m in ("a/alpha", "a/beta", "a/gamma", "b/bravo", "c/charlie", "d/delta")
]
FIRST = [{"role": "user", "content": "hi"}]


def answer(request: httpx.Request) -> httpx.Response:
  model = f"{request.url.host[0]}/{json.loads(request.content)['model']}"
  message = {"role": "assistant", "content": model}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": model, "choices": [choice]})


def ask(client: TestClient, messages: list[dict], headers: dict | None = None) -> str:
  body = {"model": router.RESERVED_MODEL, "messages": messages}
  response = client.post("/v1/chat/completions", json=body, headers=headers or {})
  assert response.status_code == 200, response.text
  return response.json()["choices"][0]["message"]["content"]


def test_steps(client: TestClient) -> None:
  assert ask(client, FIRST, CHAT) == "c/charlie", "the classifier tier"
  assert dashboard.HISTORY.latest(1)[0]["retry"] is None, (
    "a first attempt is not a retry"
  )
  assert ask(client, FIRST, CHAT) == "b/bravo", "1 tier up"
  assert dashboard.HISTORY.latest(1)[0]["retry"] == "1", dashboard.HISTORY.latest(1)[0]
  first = ask(client, FIRST, CHAT)
  assert first.startswith("a/"), "tier A"
  key = api.session_key(MASTER, FIRST, CHAT)
  assert api.PENALTIES.pinned(key, "daedalus/auto:TIER-A") == first, (
    "the new session model"
  )
  assert api.PENALTIES.weights(["c/charlie", "b/bravo"]) == {
    "c/charlie": 1.0,
    "b/bravo": 1.0,
  }, "no fault"
  second, third = ask(client, FIRST, CHAT), ask(client, FIRST, CHAT)
  assert len({first, second, third}) == 3, "each tier A model once"
  again = ask(client, FIRST, CHAT)
  assert again.startswith("a/") and again != third, "the list starts again"
  assert dashboard.HISTORY.latest(1)[0]["retry"] == "5", dashboard.HISTORY.latest(1)[0]


def test_new_message(client: TestClient) -> None:
  later = [
    *FIRST,
    {"role": "assistant", "content": "a/alpha"},
    {"role": "user", "content": "ok"},
  ]
  assert ask(client, later, CHAT) == "c/charlie", (
    "the next message uses the session tier"
  )
  assert ask(client, later, CHAT) == "b/bravo", "a try again of the new message"


def test_digest_ignores_trailing_answer() -> None:
  """A regenerate sends the thread with the answer it repeats, and still matches."""
  asked = [{"role": "user", "content": "hi"}]
  answered = [*asked, {"role": "assistant", "content": "c/charlie"}]
  assert retries.digest(asked) == retries.digest(answered), (
    "a regenerate repeats the turn"
  )
  versions = [*answered, {"role": "assistant", "content": "b/bravo"}]
  assert retries.digest(asked) == retries.digest(versions), (
    "each version repeats the turn"
  )
  tool_step = [
    *asked,
    {"role": "assistant", "content": "", "tool_calls": [{}]},
    {"role": "tool", "content": "{}"},
  ]
  assert retries.digest(asked) != retries.digest(tool_step), "a tool step is no retry"
  later = [
    *asked,
    {"role": "assistant", "content": "x"},
    {"role": "user", "content": "ok"},
  ]
  assert retries.digest(asked) != retries.digest(later), "a new user turn is no retry"


def test_regenerate_payload(client: TestClient) -> None:
  """A regenerate request ends with the answer it repeats, and counts as a try again."""
  retry_chat = {"X-OpenWebUI-Chat-Id": "chat-regenerate"}
  assert ask(client, FIRST, retry_chat) == "c/charlie"
  regenerate = [*FIRST, {"role": "assistant", "content": "c/charlie"}]
  assert ask(client, regenerate, retry_chat) == "b/bravo", "a regenerate is a try again"
  assert dashboard.HISTORY.latest(1)[0]["retry"] == "1"


def test_task_between(client: TestClient) -> None:
  message = [{"role": "user", "content": "task between"}]
  task = [{"role": "user", "content": "### Task: suggest follow-ups"}]
  assert ask(client, message, CHAT) == "c/charlie"
  ask(client, task, CHAT)
  assert ask(client, message, CHAT) == "b/bravo", (
    "a task request does not end the retry"
  )


def test_other_requests(client: TestClient) -> None:
  alone = [{"role": "user", "content": "no header"}]
  assert ask(client, alone) == ask(client, alone) == "c/charlie", "no chat id, no retry"
  body = {"model": "daedalus/sophos", "messages": FIRST}
  pool = {"X-OpenWebUI-Chat-Id": "p"}
  first = client.post("/v1/chat/completions", json=body, headers=pool)
  assert first.status_code == 200, first.text
  row1 = dashboard.HISTORY.latest(1)[0]
  second = client.post("/v1/chat/completions", json=body, headers=pool)
  assert second.status_code == 200, second.text
  row2 = dashboard.HISTORY.latest(1)[0]
  assert row2["retry"] == "1", "a named pool counts on a repeat"
  assert row2["via"] != row1["via"], "the pool selects another model"
  change = row2["transition"]
  assert (change["from_pool"], change["to_pool"]) == ("sophos", "sophos"), change
  timed = [{"role": "system", "content": "10:00"}, {"role": "user", "content": "time"}]
  other = {"X-OpenWebUI-Chat-Id": "chat-2"}
  assert ask(client, timed, other) == "c/charlie"
  timed[0]["content"] = "10:01"
  assert ask(client, timed, other) == "b/bravo", (
    "a system message change is still a retry"
  )


def test_no_hook_no_retry(client: TestClient) -> None:
  """Without a request hook file, a repeated message is a new request."""
  off, disabled = api.REQUEST_HOOKS, hooks.DISABLED
  api.REQUEST_HOOKS = {}
  hooks.DISABLED = {"owui_auto_reasoning_effort.py"}
  try:
    assert ask(client, FIRST, CHAT) == "c/charlie"
    assert ask(client, FIRST, CHAT) == "c/charlie", "the hook owns the rule"
    assert dashboard.HISTORY.latest(1)[0]["retry"] is None, "no try code"
  finally:
    api.REQUEST_HOOKS, hooks.DISABLED = off, disabled


def test_shipped_hook_legend(client: TestClient) -> None:
  """The shipped hook names its row of the code legend."""
  path = "hooks/owui_auto_reasoning_effort.py"
  module = hooks.load(hooks.ROOT / path)
  assert module is not None
  assert hooks.init_rows({"on-request": path}) == module.on_init()


def test_metadata_hook_keeps_client_scope_on_retry(client, monkeypatch) -> None:
  """An automatically attached client hook runs again with the retry count."""
  monkeypatch.setattr(api, "REQUEST_HOOKS", {})
  headers = {"X-OpenWebUI-Chat-Id": "metadata-retry"}
  assert ask(client, FIRST, headers) == "c/charlie"
  assert ask(client, FIRST, headers) == "b/bravo"
  row = dashboard.HISTORY.latest(1)[0]
  assert row["retry"] == "1"
  assert row["transition"]["reason"] == "rt1"


def test_expiry() -> None:
  now = [0.0]
  found = retries.Retries(idle=60, clock=lambda: now[0])
  retries.record(found.start("x", FIRST), 2, "c/charlie")
  assert found.start("x", FIRST).count == 1
  now[0] = 120.0
  assert found.start("x", FIRST).count == 0, "an idle chat expires"


def test_fresh_groups_excludes_same_slug_in_later_tiers() -> None:
  """Later tiers of a try again chain also drop copies of answered models."""
  turn = retries.Turn(0.0)
  retries.record(turn, 2, "c/charlie")
  found = retries.fresh_groups(
    turn, [["b/bravo", "b/charlie"], ["c/charlie", "d/delta"]]
  )
  assert found == [["b/bravo"], ["d/delta"]], found


def test_retry_skips_same_slug_on_other_provider(monkeypatch) -> None:
  """A try again of an answered model skips its copy on another provider."""
  config = {
    "e": {"api_key": "k", "api_base": "https://e.test/v1", "tier": {"TIER-A": ["*"]}},
    "a": {"api_key": "k", "api_base": "https://a.test/v1", "tier": {"TIER-A": ["*"]}},
  }
  model_store.write_store([{"id": m} for m in ("e/alpha", "a/alpha", "a/beta")])
  headers = {"X-OpenWebUI-Chat-Id": "chat-slug"}
  body = {"model": "daedalus/sophos", "messages": FIRST}
  with monkeypatch.context() as patch:
    patch.setattr(api, "get_config", lambda: config)
    client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
    first = client.post("/v1/chat/completions", json=body, headers=headers)
    assert first.json()["choices"][0]["message"]["content"] == "e/alpha", first.text
    again = client.post("/v1/chat/completions", json=body, headers=headers)
    content = again.json()["choices"][0]["message"]["content"]
    assert (content, dashboard.HISTORY.latest(1)[0]["retry"]) == ("a/beta", "1"), (
      again.text
    )


def shipped_hook() -> str:
  """The shipped Open WebUI hook, copied into config folder of the test."""
  folder = hooks.ROOT / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  target = folder / "owui_auto_reasoning_effort.py"
  target.write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  return "hooks/owui_auto_reasoning_effort.py"


@pytest.fixture(scope="module")
def client():
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api, "REQUEST_HOOKS", {"on-request": shipped_hook()})
    patch.setattr(api, "get_config", lambda: CONFIG)
    patch.setattr(router, "required_tier", lambda text: 2)
    patch.setattr(api.PENALTIES, "select", lambda: 0.0)
    model_store.write_store(ROWS)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
  upstream.set_client(None)
