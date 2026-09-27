import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from daedalus import api, config, dashboard, settings, store, upstream

CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "tier": {"TIER-A": ["big"], "TIER-C": ["small"]},
  }
}
MASTER = "master-key-0123456789"
AUTH = {"Authorization": f"Bearer {MASTER}"}
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
    "sessions": 0,
    "catalog": status["catalog"],
  }, status
  assert status["catalog"]["built"] <= time.time() < status["catalog"]["next"], status
  body = {"model": "daedalus/sophos", "messages": [{"role": "user", "content": "x"}]}
  assert client.post("/v1/chat/completions", json=body).status_code == 200
  assert client.get("/ui/api/status").json()["sessions"] == 1, "the new session model"
  recent = client.get("/ui/api/requests").json()
  assert len(recent) == 1 and recent[0]["via"] == "p/big" and recent[0]["status"] == 200
  assert recent[0]["model"] == "daedalus/sophos" and recent[0]["fallbacks"] == "0"
  client.get("/ui/api/status")
  assert len(client.get("/ui/api/requests").json()) == 1, "chat requests only"


def check_page(client: TestClient) -> None:
  page = client.get("/")
  assert page.status_code == 200 and "text/html" in page.headers["content-type"]
  assert 'src="ui/app.js?v=' in page.text, "relative asset paths with a content hash"
  assert 'href="ui/style.css?v=' in page.text, "the style link has a content hash"
  for name in (
    "overview",
    "pools",
    "requests",
    "models",
    "keys",
    "providers",
    "settings",
  ):
    assert f'<section data-page="{name}"' in page.text, f"the {name} page"
  script = client.get("/ui/app.js")
  assert script.status_code == 200 and "javascript" in script.headers["content-type"]
  assert client.get("/ui/style.css").status_code == 200
  assert script.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/index.html").status_code == 404, "listed assets only"


def check_login(client: TestClient) -> None:
  os.environ.pop(dashboard.MASTER_ENV, None)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 503, "no master key"
  os.environ[dashboard.MASTER_ENV] = "short"
  assert client.post("/ui/api/login", json=login).status_code == 503, "a short key"
  os.environ[dashboard.MASTER_ENV] = MASTER
  assert client.get("/ui/api/status").status_code == 401, "no session"
  wrong = {"username": "admin", "password": MASTER + "x"}
  assert client.post("/ui/api/login", json=wrong).status_code == 401
  other = {"username": "root", "password": MASTER}
  assert client.post("/ui/api/login", json=other).status_code == 401
  assert client.post("/ui/api/login", content=b"[").status_code == 400
  answer = client.post("/ui/api/login", json=login)
  assert answer.status_code == 200, answer.text
  header = answer.headers["set-cookie"].lower()
  assert "httponly" in header and "samesite=strict" in header, header
  assert "max-age" not in header, "a browser-session cookie without remember me"
  assert client.get("/ui/api/status").status_code == 200, "the session cookie"
  value = client.cookies.get(dashboard.COOKIE)
  expires, _, signed = value.partition(".")
  forged = f"{int(expires) + 60}.{signed}"
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: forged}).status_code == 401
  )
  old = dashboard.cookie(MASTER, time.time() - dashboard.SESSION_SECONDS - 1)
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: old}).status_code == 401
  )
  os.environ[dashboard.MASTER_ENV] = MASTER + "-new"
  assert client.get("/ui/api/status").status_code == 401, "a new master key ends it"
  os.environ[dashboard.MASTER_ENV] = MASTER
  client.post("/ui/api/logout")
  assert client.get("/ui/api/status").status_code == 401, "logout ends the session"
  https = {"Origin": "https://3357-box.example.app"}
  framed = client.post("/ui/api/login", json=login, headers=https).headers["set-cookie"]
  assert "samesite=none" in framed.lower() and "secure" in framed.lower(), framed
  assert "partitioned" in framed.lower(), "the cookie works in a preview frame"
  value = client.post("/ui/api/login", json=login).json()["session"]
  client.cookies.clear()
  header = {dashboard.HEADER: value}
  assert client.get("/ui/api/status", headers=header).status_code == 200, (
    "a frame without cookies"
  )
  assert (
    client.get("/ui/api/status", headers={dashboard.HEADER: "1.x"}).status_code == 401
  )
  stale = {dashboard.COOKIE: "1.x"}
  assert client.get("/ui/api/status", headers=header, cookies=stale).status_code == 200
  remember = client.post("/ui/api/login", json={**login, "remember": True})
  assert (
    f"max-age={dashboard.REMEMBER_SECONDS}" in remember.headers["set-cookie"].lower()
  )
  expires = int(client.cookies.get(dashboard.COOKIE).partition(".")[0])
  assert expires > time.time() + dashboard.REMEMBER_SECONDS - 60, "a 30-day session"


def check_keys(client: TestClient) -> None:
  assert client.get("/ui/api/keys").json() == []
  made = client.post("/ui/api/keys", json={"name": " laptop "})
  assert made.status_code == 201 and made.json()["name"] == "laptop", made.text
  key = made.json()["key"]
  listed = client.get("/ui/api/keys").json()
  assert [row["name"] for row in listed] == ["laptop"] and "key" not in listed[0], (
    listed
  )
  assert client.post("/ui/api/keys", json={"name": "laptop"}).status_code == 400, (
    "in use"
  )
  assert client.post("/ui/api/keys", json={"name": ""}).status_code == 400
  body = {"model": "daedalus/sophos", "messages": [{"role": "user", "content": "k"}]}
  bearer = {"Authorization": f"Bearer {key}"}
  sent = client.post("/v1/chat/completions", json=body, headers=bearer)
  assert sent.status_code == 200, sent.text
  outside = TestClient(api.app)
  assert outside.get("/ui/api/keys", headers=bearer).status_code == 401, "no dashboard"
  assert client.delete("/ui/api/keys/laptop").status_code == 204
  assert client.delete("/ui/api/keys/laptop").status_code == 404
  assert (
    client.post("/v1/chat/completions", json=body, headers=bearer).status_code == 401
  )


def check_files(client: TestClient, folder: Path) -> None:
  names = [item["path"] for item in client.get("/ui/api/files").json()]
  assert names == [str(path) for path in dashboard.FILES], names
  path = str(settings.DEFAULT_PATH)
  assert (
    client.put("/ui/api/files", json={"path": path, "text": ""}).status_code == 400
  ), "the Settings page owns the settings file"
  shown = client.get("/ui/api/settings").json()
  assert shown["file"]["timeouts"]["slow"] == 30, shown
  assert shown["defaults"]["weights"]["fault"] == 0.5, shown
  bad = client.put("/ui/api/settings", json={"changes": {"weights": {"fault": 0}}})
  assert bad.status_code == 422 and "above 0" in bad.text, bad.text
  assert "slow: 30" in settings.DEFAULT_PATH.read_text(), "a bad value is not written"
  unknown = client.put("/ui/api/settings", json={"changes": {"x": {"y": 1}}})
  assert unknown.status_code == 422, unknown.text
  assert client.put("/ui/api/settings", json={"changes": 1}).status_code == 400
  changes = {"timeouts": {"slow": 12}, "catalog": {"every": 0}}
  saved = client.put("/ui/api/settings", json={"changes": changes})
  assert saved.status_code == 200, saved.text
  text = settings.DEFAULT_PATH.read_text()
  assert "  slow: 12 # a first token after this is slow" in text, "the comments stay"
  assert "# Router settings" in text and "every: 0 #" in text, text
  assert api.SLOW_SECONDS == 12.0, "the save applies the settings"
  cleared = client.put(
    "/ui/api/settings", json={"changes": {"timeouts": {"slow": None}}}
  )
  assert (
    cleared.status_code == 200 and "slow: 12" not in settings.DEFAULT_PATH.read_text()
  )
  assert api.SLOW_SECONDS == 30.0, "half of wait"
  reset = client.put("/ui/api/settings", json={"changes": {"catalog": {"every": None}}})
  assert reset.status_code == 200, reset.text
  assert "  every: 6 # hours between" in settings.DEFAULT_PATH.read_text(), (
    "an empty field writes the default and keeps the comment"
  )
  providers = str(config.DEFAULT_PATH)
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: ["})
  assert broken.status_code == 422, "a YAML error"
  listed = client.put("/ui/api/files", json={"path": providers, "text": "- a\n"})
  assert listed.status_code == 422, "provider blocks only"
  new = "q:\n  api_key: k\n"
  assert (
    client.put("/ui/api/files", json={"path": providers, "text": new}).status_code
    == 200
  )
  assert config.get_config() == {"q": {"api_key": "k"}}, "the save reloads the config"
  form = {"Content-Type": "text/plain"}
  body = json.dumps({"path": providers, "text": "a: 1\n"})
  posted = client.put("/ui/api/files", content=body, headers=form)
  assert posted.status_code == 400, "a cross-site form cannot write a file"
  outside = str(folder / "other.yml")
  assert (
    client.put("/ui/api/files", json={"path": outside, "text": ""}).status_code == 400
  )
  assert (
    client.put("/ui/api/files", json={"path": providers, "text": 1}).status_code == 400
  )


def main() -> None:
  with tempfile.TemporaryDirectory() as folder:
    store.MODELS_DB = Path(folder) / "models.sqlite3"
    store.write_store(ROWS)
    original = (
      api.get_config,
      settings.DEFAULT_PATH,
      config.DEFAULT_PATH,
      dashboard.FILES,
    )
    settings.DEFAULT_PATH = Path(shutil.copy("config/daedalus.yml", folder))
    config.DEFAULT_PATH = Path(shutil.copy("config/providers/free.yml", folder))
    dashboard.FILES = (config.DEFAULT_PATH,)
    api.get_config = lambda: CONFIG
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    api.PENALTIES.clear()
    dashboard.RECENT.clear()
    try:
      client = TestClient(api.app, headers=AUTH)
      check_page(client)
      check_login(client)
      check_data(client)
      check_keys(client)
      check_files(client, Path(folder))
    finally:
      api.get_config, settings.DEFAULT_PATH, config.DEFAULT_PATH, dashboard.FILES = (
        original
      )
      upstream.set_client(None)
      config.set_config(None)
      api.apply_settings(settings.load())
      os.environ.pop(dashboard.MASTER_ENV, None)
  print("ok: dashboard login, data and config files")


if __name__ == "__main__":
  main()
