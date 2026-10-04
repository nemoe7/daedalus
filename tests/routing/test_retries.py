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
ROWS = [{"id": m} for m in ("a/1", "a/2", "a/3", "b/1", "c/1", "d/1")]
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
  assert ask(client, FIRST, CHAT) == "c/1", "the classifier tier"
  assert dashboard.HISTORY.latest(1)[0]["retry"] is None, (
    "a first attempt is not a retry"
  )
  assert ask(client, FIRST, CHAT) == "b/1", "1 tier up"
  assert dashboard.HISTORY.latest(1)[0]["retry"] == "1", dashboard.HISTORY.latest(1)[0]
  first = ask(client, FIRST, CHAT)
  assert first.startswith("a/"), "tier A"
  key = api.session_key(MASTER, FIRST)
  assert api.PENALTIES.pinned(key, "daedalus/auto:TIER-A") == first, (
    "the new session model"
  )
  assert api.PENALTIES.weights(["c/1", "b/1"]) == {"c/1": 1.0, "b/1": 1.0}, "no fault"
  second, third = ask(client, FIRST, CHAT), ask(client, FIRST, CHAT)
  assert len({first, second, third}) == 3, "each tier A model once"
  again = ask(client, FIRST, CHAT)
  assert again.startswith("a/") and again != third, "the list starts again"
  assert dashboard.HISTORY.latest(1)[0]["retry"] == "5", dashboard.HISTORY.latest(1)[0]


def test_new_message(client: TestClient) -> None:
  later = [
    *FIRST,
    {"role": "assistant", "content": "a/1"},
    {"role": "user", "content": "ok"},
  ]
  assert ask(client, later, CHAT) == "c/1", "the next message uses the session tier"
  assert ask(client, later, CHAT) == "b/1", "a try again of the new message"


def test_task_between(client: TestClient) -> None:
  message = [{"role": "user", "content": "task between"}]
  task = [{"role": "user", "content": "### Task: suggest follow-ups"}]
  assert ask(client, message, CHAT) == "c/1"
  ask(client, task, CHAT)
  assert ask(client, message, CHAT) == "b/1", "a task request does not end the retry"


def test_other_requests(client: TestClient) -> None:
  alone = [{"role": "user", "content": "no header"}]
  assert ask(client, alone) == ask(client, alone) == "c/1", "no chat id, no retry"
  body = {"model": "daedalus/sophos", "messages": FIRST}
  pool = {"X-OpenWebUI-Chat-Id": "p"}
  first = client.post("/v1/chat/completions", json=body, headers=pool)
  assert first.status_code == 200, first.text
  row1 = dashboard.HISTORY.latest(1)[0]
  second = client.post("/v1/chat/completions", json=body, headers=pool)
  assert second.status_code == 200, second.text
  row2 = dashboard.HISTORY.latest(1)[0]
  assert row2["retry"] == "1", "a named pool counts on a repeat"
  assert row2["via"] != row1["via"], "the pool picks another model"
  change = row2["transition"]
  assert (change["from_pool"], change["to_pool"]) == ("sophos", "sophos"), change
  timed = [{"role": "system", "content": "10:00"}, {"role": "user", "content": "time"}]
  other = {"X-OpenWebUI-Chat-Id": "chat-2"}
  assert ask(client, timed, other) == "c/1"
  timed[0]["content"] = "10:01"
  assert ask(client, timed, other) == "b/1", "a system message change is still a retry"


def test_no_hook_no_retry(client: TestClient) -> None:
  """Without a request hook, a repeated message is a new request."""
  off = api.REQUEST_HOOKS
  api.REQUEST_HOOKS = {}
  try:
    assert ask(client, FIRST, CHAT) == "c/1"
    assert ask(client, FIRST, CHAT) == "c/1", "the hook owns the rule"
    assert dashboard.HISTORY.latest(1)[0]["retry"] is None, "no try code"
  finally:
    api.REQUEST_HOOKS = off


def test_shipped_hook_legend(client: TestClient) -> None:
  """The shipped hook names its row of the code legend."""
  assert hooks.init_rows({"on-request": "hooks/openwebui_retry.py"}) == [
    ["rtN", "A repeat picked another model, N times"]
  ]


def test_expiry() -> None:
  now = [0.0]
  found = retries.Retries(idle=60, clock=lambda: now[0])
  retries.record(found.start("x", FIRST), 2, "c/1")
  assert found.start("x", FIRST).count == 1
  now[0] = 120.0
  assert found.start("x", FIRST).count == 0, "an idle chat expires"


def shipped_hook() -> str:
  """The shipped Open WebUI hook, copied into the config folder of the test."""
  folder = hooks.CONFIG_DIR / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  target = folder / "openwebui_retry.py"
  target.write_text(Path("config/hooks/openwebui_retry.py").read_text(encoding="utf-8"))
  return "hooks/openwebui_retry.py"


@pytest.fixture(scope="module")
def client():
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(api, "REQUEST_HOOKS", {"on-request": shipped_hook()})
    patch.setattr(api, "get_config", lambda: CONFIG)
    patch.setattr(router, "required_tier", lambda text: 2)
    patch.setattr(api.PENALTIES, "pick", lambda: 0.0)
    model_store.write_store(ROWS)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    yield TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
  upstream.set_client(None)
