"""An Open WebUI try again on daedalus/auto moves up 1 tier, and at tier A it moves to another model."""

import json
import os
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import dashboard
from daedalus import store as model_store
from daedalus.routing import retries, router
from daedalus.server import api, upstream

MASTER = "test-master-key-0001"
os.environ["DAEDALUS_MASTER_KEY"] = MASTER
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


def check_steps(client: TestClient) -> None:
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


def check_new_message(client: TestClient) -> None:
  later = [
    *FIRST,
    {"role": "assistant", "content": "a/1"},
    {"role": "user", "content": "ok"},
  ]
  assert ask(client, later, CHAT) == "c/1", "the next message uses the session tier"
  assert ask(client, later, CHAT) == "b/1", "a try again of the new message"


def check_other_requests(client: TestClient) -> None:
  alone = [{"role": "user", "content": "no header"}]
  assert ask(client, alone) == ask(client, alone) == "c/1", "no chat id, no retry"
  body = {"model": "daedalus/koinos", "messages": FIRST}
  for _ in range(2):
    client.post("/v1/chat/completions", json=body, headers={"X-OpenWebUI-Chat-Id": "p"})
    assert dashboard.HISTORY.latest(1)[0]["retry"] is None, "pools do not count"
  timed = [{"role": "system", "content": "10:00"}, {"role": "user", "content": "time"}]
  other = {"X-OpenWebUI-Chat-Id": "chat-2"}
  assert ask(client, timed, other) == "c/1"
  timed[0]["content"] = "10:01"
  assert ask(client, timed, other) == "b/1", "a system message change is still a retry"


def check_expiry() -> None:
  now = [0.0]
  found = retries.Retries(idle=60, clock=lambda: now[0])
  retries.record(found.start("x", FIRST), 2, "c/1")
  assert found.start("x", FIRST).count == 1
  now[0] = 120.0
  assert found.start("x", FIRST).count == 0, "an idle chat expires"


def main() -> None:
  original = api.get_config, router.required_tier, model_store.MODELS_DB
  with tempfile.TemporaryDirectory() as name:
    model_store.MODELS_DB = Path(name) / "models.sqlite3"
    api.get_config, router.required_tier = (lambda: CONFIG), (lambda text: 2)
    model_store.write_store(ROWS)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    api.PENALTIES.clear()
    api.PENALTIES.pick = lambda: 0.0
    api.RETRIES.clear()
    client = TestClient(api.app, headers={"Authorization": f"Bearer {MASTER}"})
    try:
      check_steps(client)
      check_new_message(client)
      check_other_requests(client)
      check_expiry()
    finally:
      api.PENALTIES.clear()
      api.get_config, router.required_tier, model_store.MODELS_DB = original
      upstream.set_client(None)
  print("ok: try again escalation")


if __name__ == "__main__":
  main()
