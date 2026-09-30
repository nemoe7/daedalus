import json
import os
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import daedalus
from daedalus import config, dashboard, store
from daedalus.catalog import schedule
from daedalus.config import settings
from daedalus.dashboard.history import History
from daedalus.routing import loops
from daedalus.server import api, headroom, upstream

CONFIG = {
  "p": {
    "api_key": "k",
    "api_base": "https://p.test/v1",
    "tier": {"TIER-A": ["big"], "TIER-C": ["small"]},
    "models": {"small": {"order": 3}},
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
    "supports_reasoning": True,
    "reasoning_effort": "medium",
    "supports_vision": True,
    "supports_audio_input": True,
  },
  {"id": "p/small", "mode": "chat"},
  {"id": "p/embed", "mode": "embedding"},
]


def answer(request: httpx.Request) -> httpx.Response:
  message = {"role": "assistant", "content": "hi"}
  choice = {"index": 0, "message": message, "finish_reason": "stop"}
  return httpx.Response(200, json={"id": "x", "model": "m", "choices": [choice]})


def test_page(client: TestClient) -> None:
  page = client.get("/")
  assert page.status_code == 200 and "text/html" in page.headers["content-type"]
  assert 'src="ui/app.js?v=' in page.text, "relative asset paths with a content hash"
  assert 'href="ui/style.css?v=' in page.text, "the style link has a content hash"
  assert '<th class="hide-sm">Session</th>' in page.text, "the Requests session column"
  for name in (
    "overview",
    "requests",
    "models",
    "keys",
    "providers",
    "limits",
    "settings",
  ):
    assert f'<section data-page="{name}"' in page.text, f"the {name} page"
  assert 'name="remember" type="checkbox" checked' in page.text, "remember me starts on"
  script = client.get("/ui/app.js")
  assert script.status_code == 200 and "javascript" in script.headers["content-type"]
  for reason in ("ctx", "hlt", "lmt", "err", "rnd", "cls", "esc", "try"):
    assert f'  {reason}: "' in script.text, reason
  assert 'title="${esc(label)}"' in script.text, "reason codes have hover labels"
  assert (
    'const EFFORT_SHORT = { minimal: "min", low: "low", medium: "med", high: "hi", xhigh: "xhi" }'
    in script.text
  )
  assert "shortEffort(sentText(a, r.effort))" in script.text
  assert '["change_on_draw", "Change pin on draw"' in script.text
  assert '["loops", "Loop detection"' in script.text, (
    "the loop thresholds are in Settings"
  )
  assert client.get("/ui/style.css").status_code == 200
  assert script.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/index.html").status_code == 404, "listed assets only"
  manifest = client.get("/ui/manifest.json")
  assert manifest.headers["content-type"] == "application/manifest+json"
  for icon in manifest.json()["icons"]:
    assert client.get(f"/ui/{icon['src']}").status_code == 200, icon["src"]
  assert (
    'href="ui/manifest.json?v=' in page.text and 'href="ui/logo.svg?v=' in page.text
  )


def test_pages_not_nested(client: TestClient) -> None:
  """No page section is inside another, because a hidden parent hides the child."""
  from html.parser import HTMLParser

  found: list[tuple[str, int]] = []

  class Sections(HTMLParser):
    depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
      if tag == "section":
        found.append((dict(attrs).get("data-page") or "", self.depth))
        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
      if tag == "section":
        self.depth -= 1

  Sections().feed(client.get("/").text)
  assert len(found) == 7 and all(depth == 0 for _, depth in found), found


def test_login_form(client: TestClient) -> None:
  """The login form fills in admin and shows the master key hint only for values not in the environment."""
  env = {dashboard.USER_ENV: "owner", dashboard.PASSWORD_ENV: "secret"}
  cases = [
    ({}, "admin", True),
    (env, None, False),
    ({dashboard.USER_ENV: "owner"}, None, True),
  ]
  for values, username, master in cases:
    for name in env:
      os.environ.pop(name, None)
    os.environ.update(values)
    found = TestClient(api.app).get("/ui/api/login").json()
    assert found == {
      "username": username,
      "master": master,
      "version": daedalus.__version__,
    }, (values, found)
  for name in env:
    os.environ.pop(name, None)


def test_login(client: TestClient) -> None:
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
  status = client.get("/ui/api/status")
  assert status.status_code == 200, "the session cookie"
  assert status.json()["version"] == daedalus.__version__, status.json()
  value = client.cookies.get(dashboard.COOKIE)
  expires, _, signed = value.partition(".")
  forged = f"{int(expires) + 60}.{signed}"
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: forged}).status_code == 401
  )
  old = dashboard.cookie(
    dashboard.secret(), time.time() - dashboard.SESSION_SECONDS - 1
  )
  assert (
    client.get("/ui/api/status", cookies={dashboard.COOKIE: old}).status_code == 401
  )
  os.environ[dashboard.MASTER_ENV] = MASTER + "-new"
  assert client.get("/ui/api/status").status_code == 401, "a new master key ends it"
  os.environ[dashboard.MASTER_ENV] = MASTER
  assert client.post("/ui/api/login", json=login).status_code == 200
  os.environ[dashboard.USER_ENV] = "owner"
  os.environ[dashboard.PASSWORD_ENV] = "ui password"
  assert client.get("/ui/api/status").status_code == 401, (
    "a new login ends the sessions"
  )
  assert client.post("/ui/api/login", json=login).status_code == 401, "no admin login"
  mine = {"username": "owner", "password": "ui password"}
  assert client.post("/ui/api/login", json=mine).status_code == 200
  assert client.get("/ui/api/status").status_code == 200, "the env login"
  models = client.get("/v1/models", headers={"Authorization": "Bearer ui password"})
  assert models.status_code == 401, "the password is for the dashboard only"
  assert client.get("/v1/models").status_code == 200, "the master key stays the /v1 key"
  del os.environ[dashboard.USER_ENV], os.environ[dashboard.PASSWORD_ENV]
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


def test_defaults(client: TestClient) -> None:
  """The provider defaults for the form placeholders, with no values from the environment."""
  found = client.get("/ui/api/provider-defaults").json()
  assert found["groq"]["api_base"] == "https://api.groq.com/openai/v1", found
  assert found["gemini"]["api_type"] == "gemini" and found["*"] == {
    "api_type": "openai"
  }
  assert "env:CLOUDFLARE_ACCOUNT_ID" in found["cloudflare"]["account_id"], "a token"
  assert TestClient(api.app).get("/ui/api/provider-defaults").status_code == 401


def test_data(client: TestClient) -> None:
  response = client.get("/ui/api/models")
  assert response.status_code == 200, response.text
  models = response.json()
  assert [row["id"] for row in models] == ["p/big", "p/small", "p/embed"], "all rows"
  assert models[0] == {
    "id": "p/big",
    "mode": "chat",
    "max_input_tokens": 1000,
    "tools": True,
    "reasoning": True,
    "effort": "medium",
    "flags": ["vision", "audio_input"],
    "tier": "TIER-A",
    "weight": 1.0,
    "cooldown": None,
    "client_cooldowns": {},
    "order": 1,
  }, models[0]
  assert models[1]["order"] == 3, "the order of each model"
  previous = CONFIG["p"]["models"]
  CONFIG["p"]["models"] = {**previous, "big": {"pool": False}}
  try:
    assert client.get("/ui/api/models").json()[0]["tier"] is None, (
      "pool false hides an explicit provider tier"
    )
  finally:
    CONFIG["p"]["models"] = previous
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  api.COOLDOWNS.start("p/small#kilo", {"retry-after": "30"}, b"")
  row = client.get("/ui/api/models").json()[1]
  until = row["cooldown"]
  api.COOLDOWNS.clear()
  assert list(row["client_cooldowns"]) == ["kilo"], (
    "the cooldown end of each client lane"
  )
  assert 80 < until - time.time() <= 90, "the cooldown end of each model"
  assert models[1]["tier"] == "TIER-C" and models[1]["tools"] is False
  assert models[1]["reasoning"] is False, models[1]
  embed = models[2]
  assert (embed["mode"], embed["tier"], embed["weight"]) == ("embedding", None, None), (
    embed
  )
  pools = {pool["name"]: pool["members"] for pool in client.get("/ui/api/pools").json()}
  assert [m["id"] for m in pools["daedalus/auto"]] == ["p/big", "p/small"]
  assert "daedalus/praktos" not in pools
  assert [m["id"] for m in pools["daedalus/koinos"]] == ["p/small"]
  assert pools["daedalus/moros"] == [], "a pool with no member"
  context = {
    pool["name"]: pool["context"] for pool in client.get("/ui/api/pools").json()
  }
  assert context["daedalus/auto"] == 1000, "the largest context of the members"
  assert context["daedalus/koinos"] is None and context["daedalus/moros"] is None, (
    context
  )
  assert pools["daedalus/koinos"][0]["cooldown"] is None, "no cooldown"
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  pools = {pool["name"]: pool["members"] for pool in client.get("/ui/api/pools").json()}
  api.COOLDOWNS.clear()
  cooled = pools["daedalus/koinos"][0]["cooldown"]
  assert cooled and cooled > time.time(), "a pool member shows its cooldown end"
  status = client.get("/ui/api/status").json()
  assert status == {
    "healthy": True,
    "version": daedalus.__version__,
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


def test_history(client: TestClient) -> None:
  restarted = History(lambda: store.MODELS_DB, keep=3)
  assert restarted.latest(1)[0]["model"] == dashboard.HISTORY.latest(1)[0]["model"], (
    "a new history object reads the rows of the file, as after a restart"
  )
  restarted.clear()
  for number in range(5):
    restarted.add({"at": number, "model": f"m/{number}"})
  kept = [row["model"] for row in restarted.latest(10)]
  assert kept == ["m/4", "m/3", "m/2"], "the newest rows first, at most keep"
  database = restarted.connect()
  count = database.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
  database.close()
  assert count == 3, "the file drops the older rows"
  for number in range(60):
    dashboard.HISTORY.add({"at": number, "model": "m/x"})
  assert len(client.get("/ui/api/requests").json()) == 50, "50 rows by default"
  assert len(client.get("/ui/api/requests?limit=55").json()) == 55
  assert len(client.get("/ui/api/requests?limit=x").json()) == 50, "a bad limit"
  dashboard.HISTORY.clear()


def test_keys(client: TestClient) -> None:
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


def test_files(
  client: TestClient, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  names = [item["path"] for item in client.get("/ui/api/files").json()]
  assert names == [str(path) for path in dashboard.FILES], names
  path = str(settings.DEFAULT_PATH)
  assert (
    client.put("/ui/api/files", json={"path": path, "text": ""}).status_code == 400
  ), "the Settings page owns the settings file"
  shown = client.get("/ui/api/settings").json()
  assert shown["file"]["timeouts"]["slow"] == 30, shown
  assert shown["defaults"]["weights"]["fault"] == 0.5, shown
  assert shown["defaults"]["loops"] == {
    "calls": 3,
    "repeats": 4,
    "shortest": 20,
    "longest": 2000,
  }, shown
  assert shown["headroom_available"] is False, shown

  async def ready() -> bool:
    return True

  monkeypatch.setattr(headroom, "available", ready)
  assert client.get("/ui/api/settings").json()["headroom_available"] is True
  bad = client.put("/ui/api/settings", json={"changes": {"weights": {"fault": 0}}})
  assert bad.status_code == 422 and "above 0" in bad.text, bad.text
  bad = client.put("/ui/api/settings", json={"changes": {"loops": {"calls": 1}}})
  assert bad.status_code == 422 and "between 2 and 100" in bad.text, bad.text
  assert "slow: 30" in settings.DEFAULT_PATH.read_text(), "a bad value is not written"
  unknown = client.put("/ui/api/settings", json={"changes": {"x": {"y": 1}}})
  assert unknown.status_code == 422, unknown.text
  assert client.put("/ui/api/settings", json={"changes": 1}).status_code == 400
  changes = {
    "timeouts": {"slow": 12},
    "catalog": {"every": 0},
    "loops": {"calls": 5, "repeats": 6, "shortest": 10, "longest": 3000},
  }
  saved = client.put("/ui/api/settings", json={"changes": changes})
  assert saved.status_code == 200, saved.text
  text = settings.DEFAULT_PATH.read_text()
  assert "  slow: 12 # a first token after this is slow" in text, "the comments stay"
  assert "# Router settings" in text and "every: 0 #" in text, text
  assert api.SLOW_SECONDS == 12.0, "the save applies the settings"
  assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (5, 6, 10, 3000)
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
  words = {"escalation": {"keywords": ["ultrathink", "yes", "think hard"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "keywords:\n    - ultrathink\n    - 'yes'\n    - think hard\n" in text, text
  assert api.KEYWORDS and api.KEYWORDS.search("please ultrathink"), "the save applies"
  words = {"escalation": {"keywords": ["audit"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "keywords:\n    - audit\n" in text and "ultrathink" not in text, text
  words = {"escalation": {"keywords": None}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  assert "keywords: []" in settings.DEFAULT_PATH.read_text() and api.KEYWORDS is None
  dark = {"dashboard": {"theme": "dark"}}
  assert client.put("/ui/api/settings", json={"changes": dark}).status_code == 200
  assert client.get("/ui/api/settings").json()["file"]["dashboard"]["theme"] == "dark"
  blue = client.put(
    "/ui/api/settings", json={"changes": {"dashboard": {"theme": "blue"}}}
  )
  assert blue.status_code == 422 and "system, light or dark" in blue.text, blue.text
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"dashboard": {"theme": None}}}
    ).status_code
    == 200
  )
  # The YAML view reads and writes the file text.
  text = client.get("/ui/api/settings").json()["text"]
  assert text == settings.DEFAULT_PATH.read_text(), "the text of the file"
  broken = client.put("/ui/api/settings", json={"text": "weights:\n  fault: 0\n"})
  assert broken.status_code == 422 and "above 0" in broken.text, broken.text
  assert settings.DEFAULT_PATH.read_text() == text, "a bad text is not written"
  edited = text.replace("timeouts:\n", "timeouts:\n  slow: 14 # edited\n", 1)
  saved = client.put("/ui/api/settings", json={"text": edited})
  assert saved.status_code == 200 and saved.json()["text"] == edited, saved.text
  assert settings.DEFAULT_PATH.read_text() == edited and api.SLOW_SECONDS == 14.0
  providers = str(config.DEFAULT_PATH)
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: ["})
  assert broken.status_code == 422, "a YAML error"
  listed = client.put("/ui/api/files", json={"path": providers, "text": "- a\n"})
  assert listed.status_code == 422, "provider blocks only"
  twice = "q:\n  api_key: k\nq:\n  api_key: j\n"
  doubled = client.put("/ui/api/files", json={"path": providers, "text": twice})
  assert doubled.status_code == 422 and doubled.json()["error"]["message"] == (
    "duplicate key 'q' at line 3, column 1 (first at line 1)"
  ), doubled.text
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: [\n"})
  assert "<unicode string>" not in broken.text and "line 2" in broken.text, broken.text
  doubled = client.put(
    "/ui/api/settings", json={"text": "weights:\n  fault: 0.5\n  fault: 0.25\n"}
  )
  assert doubled.status_code == 422 and "duplicate key 'fault'" in doubled.text, (
    doubled.text
  )
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
  # The main file stays. A {provider}.yml file comes and goes.
  listed = client.get("/ui/api/files").json()
  assert [(f["path"], f["main"]) for f in listed] == [(providers, True)], listed
  made = client.post("/ui/api/files", json={"name": "openrouter"})
  assert made.status_code == 200, made.text
  assert "OPENROUTER_API_KEY" in made.json()["text"], made.json()
  assert config.provider_files(config.DEFAULT_PATH) == [Path(made.json()["path"])]
  loaded = config.get_config()
  assert loaded["openrouter"]["_file"]["models"] == {"*": {}}, loaded["openrouter"]
  assert client.post("/ui/api/files", json={"name": "openrouter"}).status_code == 400
  assert client.post("/ui/api/files", json={"name": "Open Router"}).status_code == 400
  assert client.post("/ui/api/files", json={"name": "../x"}).status_code == 400
  assert (
    client.request("DELETE", "/ui/api/files", json={"path": providers}).status_code
    == 400
  )
  assert (
    client.request("DELETE", "/ui/api/files", json={"path": str(folder)}).status_code
    == 400
  )
  gone = client.request("DELETE", "/ui/api/files", json={"path": made.json()["path"]})
  assert gone.status_code == 200, gone.text
  assert config.provider_files(config.DEFAULT_PATH) == [], "the file went"
  assert "_file" not in config.get_config().get("openrouter", {}), "the reload drops it"


def test_provider_edits_rebuild_from_cache(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  queued = []

  def cached() -> None:
    pass

  monkeypatch.setattr(api, "CATALOG_REBUILD_CACHED", cached)
  monkeypatch.setattr(schedule, "request", lambda task: queued.append(task) or True)

  response = client.put(
    "/ui/api/files",
    json={"path": str(config.DEFAULT_PATH), "text": "q:\n  api_key: k\n"},
  )
  assert response.status_code == 200, response.text
  assert queued == [cached], "a provider YAML save queues a cache-only rebuild"

  text = settings.DEFAULT_PATH.read_text(encoding="utf-8")
  response = client.put("/ui/api/settings", json={"text": text})
  assert response.status_code == 200, response.text
  assert queued == [cached], "settings saves do not rebuild the provider catalog"


def test_broken_file(client: TestClient, folder: Path) -> None:
  broken = folder / "groq.yml"
  for text in ("api_key: k\nmodels: [\n", "api_key: k\napi_key: j\n"):
    broken.write_text(text)
    listed = client.get("/ui/api/files")
    assert listed.status_code == 200, listed.text
    found = next(f for f in listed.json() if f["path"] == str(broken))
    assert found["blocks"] is None and "line" in found["error"], found
    assert found["text"] == text, "the YAML view gets the file as it is"
  fixed = client.put(
    "/ui/api/files",
    json={"path": str(broken), "text": 'api_key: k\nmodels:\n  "*": {}\n'},
  )
  assert fixed.status_code == 200, fixed.text
  found = next(
    f for f in client.get("/ui/api/files").json() if f["path"] == str(broken)
  )
  assert found["error"] is None and "groq" in found["blocks"], found
  gone = client.request("DELETE", "/ui/api/files", json={"path": str(broken)})
  assert gone.status_code == 200, gone.text


def test_catalog(client: TestClient) -> None:
  assert client.post("/ui/api/catalog").status_code == 503, "no rebuild in tests"
  api.CATALOG_REFRESH = lambda: None
  try:
    schedule.BUSY = True
    assert client.get("/ui/api/status").json()["catalog"]["rebuilding"] is True
    assert client.post("/ui/api/catalog").status_code == 409, "1 rebuild at a time"
    schedule.BUSY = False
    assert client.post("/ui/api/catalog").status_code == 202
  finally:
    api.CATALOG_REFRESH, schedule.BUSY = None, False
  assert TestClient(api.app).post("/ui/api/catalog").status_code == 401


def test_reset(client: TestClient) -> None:
  api.PENALTIES.record("p/big", 0.1)
  api.COOLDOWNS.begin("p/big", {}, b"")
  api.PENALTIES.pin("chat", "koinos", "p/big")
  assert api.PENALTIES.weights(["p/big"])["p/big"] < 1 and api.COOLDOWNS.ends()
  assert client.post("/ui/api/reset").status_code == 200
  assert api.PENALTIES.weights(["p/big"]) == {"p/big": 1.0}, "the weights go back to 1"
  assert api.COOLDOWNS.ends() == {}, "the cooldowns end"
  assert api.PENALTIES.pinned("chat", "koinos") == "p/big", "the pins stay"
  assert TestClient(api.app).post("/ui/api/reset").status_code == 401


@pytest.fixture(scope="module")
def folder(state_folder: Path) -> Path:
  return state_folder


@pytest.fixture(scope="module")
def client(folder: Path):
  store.write_store(ROWS)
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(
      settings, "DEFAULT_PATH", Path(shutil.copy("config/daedalus.yml", folder))
    )
    patch.setattr(
      config, "DEFAULT_PATH", Path(shutil.copy("config/providers/free.yml", folder))
    )
    patch.setattr(dashboard, "FILES", (config.DEFAULT_PATH,))
    patch.setattr(api, "get_config", lambda: CONFIG)
    upstream.set_client(httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    yield TestClient(api.app, headers=AUTH)
  upstream.set_client(None)
  config.set_config(None)
