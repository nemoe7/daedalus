import base64
import os
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import api, dashboard, keys, store

CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "tier": {"TIER-A": ["big"], "TIER-C": ["small"]},
  }
}
ROWS = [
  {
    "id": "p/big",
    "mode": "chat",
    "max_input_tokens": 1000,
    "supports_function_calling": True,
  },
  {"id": "p/small", "mode": "chat"},
  {"id": "p/embed", "mode": "embedding"},
]


def answer(request: httpx.Request) -> httpx.Response:
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def basic(user: str, password: str) -> dict[str, str]:
  token = base64.b64encode(f"{user}:{password}".encode()).decode()
  return {"Authorization": f"Basic {token}"}


def check_data(client: TestClient) -> None:
  models = client.get("/ui/api/models").json()
  assert [row["id"] for row in models] == ["p/big", "p/small"], "routable rows only"
  assert models[0] == {
    "id": "p/big",
    "max_input_tokens": 1000,
    "tools": True,
    "tier": "TIER-A",
    "weight": 1.0,
  }, models[0]
  assert models[1]["tier"] == "TIER-C" and models[1]["tools"] is False
  pools = {pool["name"]: pool["members"] for pool in client.get("/ui/api/pools").json()}
  assert [m["id"] for m in pools["daedalus/auto"]] == ["p/big", "p/small"]
  assert [m["id"] for m in pools["daedalus/praktos"]] == ["p/big"], "tool models only"
  assert [m["id"] for m in pools["daedalus/koinos"]] == ["p/small"]
  assert pools["daedalus/moros"] == [], "a pool with no member"
  status = client.get("/ui/api/status").json()
  assert status == {
    "healthy": True,
    "models": 2,
    "key": False,
    "login": False,
    "sessions": 0,
  }, status
  body = {"model": "daedalus/sophos", "messages": [{"role": "user", "content": "x"}]}
  assert client.post("/v1/chat/completions", json=body).status_code == 200
  assert client.get("/ui/api/status").json()["sessions"] == 1, "the new session model"
  recent = client.get("/ui/api/requests").json()
  assert len(recent) == 1 and recent[0]["via"] == "p/big" and recent[0]["status"] == 200
  assert recent[0]["model"] == "daedalus/sophos" and recent[0]["fallbacks"] == "0"
  client.get("/ui/api/status")
  assert len(client.get("/ui/api/requests").json()) == 1, "chat requests only"
  settings = client.get("/ui/api/settings").json()
  assert "session_affinity:" in settings["text"], "the settings file text"


def check_access(client: TestClient) -> None:
  keys.save_hash(store.MODELS_DB, keys.digest("sk-local-key-0123456789"))
  assert client.get("/ui/api/status").status_code == 401, "a key closes the dashboard"
  token = {"Authorization": "Bearer sk-local-key-0123456789"}
  assert client.get("/ui/api/status", headers=token).json()["key"] is True
  assert client.get("/ui/api/models", headers=basic("u", "p")).status_code == 401
  os.environ["DAEDALUS_USERNAME"], os.environ["DAEDALUS_PASSWORD"] = "u", "p"
  try:
    assert client.get("/ui/api/models", headers=basic("u", "p")).status_code == 200
    assert client.get("/ui/api/models", headers=basic("u", "x")).status_code == 401
    assert client.get("/ui/api/models", headers=basic("x", "p")).status_code == 401
    bad = {"Authorization": "Basic %%%"}
    assert client.get("/ui/api/models", headers=bad).status_code == 401, "bad base64"
    keys.save_hash(store.MODELS_DB, None)
    assert client.get("/ui/api/pools").status_code == 401, "the login alone closes it"
    assert client.get("/ui/api/pools", headers=token).status_code == 401, "no key now"
    del os.environ["DAEDALUS_PASSWORD"]
    assert client.get("/ui/api/pools").status_code == 200, "both env vars or none"
  finally:
    os.environ.pop("DAEDALUS_USERNAME", None)
    os.environ.pop("DAEDALUS_PASSWORD", None)


def main() -> None:
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store(ROWS)
    original = api.get_config
    api.get_config = lambda: CONFIG
    api.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    api.PENALTIES.clear()
    dashboard.RECENT.clear()
    try:
      client = TestClient(api.app)
      check_data(client)
      check_access(client)
    finally:
      api.get_config = original
      api.set_client(None)
  print("ok: dashboard data and access")


if __name__ == "__main__":
  main()
