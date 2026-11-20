import json
import logging
import os
import re
import time
from pathlib import Path

import pytest
from conftest import CONFIG, MASTER, _tar, invalid_request, run_app_js
from fastapi.testclient import TestClient

import daedalus
from daedalus import config, dashboard, store, updates
from daedalus.catalog import schedule
from daedalus.config import remote, settings
from daedalus.dashboard import History
from daedalus.providers import hooks
from daedalus.routing import loops
from daedalus.server import api, headroom


def test_ui_path_sends_the_reader_to_the_page(client: TestClient) -> None:
  """`/ui` and `/ui/` redirect to `/`."""
  for path in ("/ui", "/ui/"):
    answer = client.get(path, follow_redirects=False)
    assert answer.status_code == 307, answer.text
    assert answer.headers["location"] == "/"


def test_model_marks(client: TestClient) -> None:
  """The dashboard serves exact mark filenames and rejects missing or unrelated files."""
  mark = client.get("/ui/icons/cloudflare.svg")
  assert mark.status_code == 200 and mark.headers["content-type"] == "image/svg+xml"
  assert mark.headers["cache-control"] == "no-cache", "an update applies at once"
  assert client.get("/ui/icons/nope.svg").status_code == 404, "a missing mark"
  assert client.get("/ui/icons/logo.svg").status_code == 404, "the page files stay out"
  assert client.get("/ui/icons/CLOUDFLARE.svg").status_code == 404, "the name is exact"


def test_nested_ui_assets_serve_from_manifest(client: TestClient) -> None:
  """Every registered UI_FILES entry, including nested vendor scripts, serves with its media type."""
  for name, media_type in dashboard.UI_FILES.items():
    response = client.get(f"/ui/{name}")
    assert response.status_code == 200, name
    assert response.headers["content-type"].startswith(media_type), name
    assert response.headers["cache-control"] == "no-cache", name
  assert client.get("/ui/vendor/nope.js").status_code == 404
  assert client.get("/ui/vendor/SORTABLE.MIN.JS").status_code == 404


def test_status_card_sits_in_the_card_grid() -> None:
  """Overview markup places the status card before requests in the card column."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  head = html[: html.index('<div class="flow">')]
  assert "status-card" not in head, "the card no longer spans the page"
  flow = html[html.index('<div class="flow">)'.replace(")", "")) :]
  column = flow.split('<div class="column">', 1)[1]
  assert column.index("status-card") < column.index("ov-limits")


def test_overview_columns_share_the_card_load() -> None:
  """Overview markup balances the 2 columns: status and limits beside models and requests."""
  root = Path(__file__).resolve().parent.parent.parent
  html = (root / "daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  start = html.index('<div class="flow">')
  flow = html[start : html.index("</section>", start)]
  left, right = flow.split('<div class="column">')[1:]
  assert left.index("status-card") < left.index("ov-limits")
  assert "ov-requests" not in left, "the short requests card balances the right column"
  assert right.index("ov-models") < right.index("ov-requests")


def test_rebuild_summary_and_modal_rows_share_event_descriptors() -> None:
  """Rebuild summary and modal markup use the same event, sign, and class descriptors."""
  run_app_js(
    "DIFF_KINDS, rebuildDiff, openDiffModal",
    """
assert.strictEqual(
  JSON.stringify(probe.DIFF_KINDS),
  JSON.stringify([
    ['added', '+', 'added'],
    ['removed', '-', 'removed'],
    ['moved', '~', 'changed'],
  ]),
  'the descriptor keeps each class, sign, and event source key',
);
const event = {
  reason: 'scheduled', at: 1791600000, models: 3,
  added: ['p/new'], removed: ['p/old'], changed: ['p/moved'], failed: [],
};
assert.strictEqual(
  probe.rebuildDiff(event),
  '<span class="added">1 added</span> · <span class="removed">1 deleted</span> · <span class="moved">1 changed</span>',
  'the summary reads the shared descriptors',
);
node('modal').showModal = () => {};
probe.openDiffModal(event);
let modal = node('model-modal-body').innerHTML;
for (const [kind, mark] of [['added', '+'], ['removed', '-'], ['moved', '~']]) {
  assert(modal.includes(`<span class="${kind}">${mark}</span>`), `${kind} modal line`);
}
probe.DIFF_KINDS[0] = ['shared-kind', '!', 'added'];
assert(
  probe.rebuildDiff(event).startsWith('<span class="shared-kind">1 added</span>'),
  'the summary reads a descriptor change',
);
probe.openDiffModal(event);
modal = node('model-modal-body').innerHTML;
assert(
  modal.includes('<span class="shared-kind">!</span>'),
  'the modal reads the same descriptor change',
);
""",
  )


def test_pages_not_nested(client: TestClient) -> None:
  """The page contains seven sections with no nested sections."""
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
  assert len(found) == 6 and all(depth == 0 for _, depth in found), found


def test_login_form(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
  """Login hints reflect configured credentials and authenticated session state."""
  env = {dashboard.DAEDALUS_USERNAME: "owner", dashboard.DAEDALUS_PASSWORD: "secret"}
  cases = [
    ({}, "admin", True),
    (env, None, False),
    ({dashboard.DAEDALUS_USERNAME: "owner"}, None, True),
  ]
  for values, username, master in cases:
    for name in env:
      monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
      monkeypatch.setenv(name, value)
    hints = TestClient(api.app)
    found = hints.get("/ui/api/login").json()
    assert found == {
      "username": username,
      "master": master,
      "version": daedalus.__version__,
      "session": False,
    }, (values, found)
    monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
    login = {
      "username": values.get(dashboard.DAEDALUS_USERNAME, "admin"),
      "password": values.get(dashboard.DAEDALUS_PASSWORD, MASTER),
    }
    assert hints.post("/ui/api/login", json=login).status_code == 200, (
      "the cookie is set"
    )
    assert hints.get("/ui/api/login").json()["session"] is True, (
      "the hint sees the cookie"
    )
    monkeypatch.delenv(dashboard.DAEDALUS_MASTER_KEY, raising=False)


def test_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv(dashboard.DAEDALUS_MASTER_KEY, raising=False)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 503, "no master key"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, "short")
  assert client.post("/ui/api/login", json=login).status_code == 503, "a short key"
  assert client.get("/ui/api/status").status_code == 401, "no session"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
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
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER + "-new")
  assert client.get("/ui/api/status").status_code == 401, "a new master key ends it"
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  assert client.post("/ui/api/login", json=login).status_code == 200
  monkeypatch.setenv(dashboard.DAEDALUS_USERNAME, "owner")
  monkeypatch.setenv(dashboard.DAEDALUS_PASSWORD, "ui password")
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
  monkeypatch.delenv(dashboard.DAEDALUS_USERNAME)
  monkeypatch.delenv(dashboard.DAEDALUS_PASSWORD)
  client.post("/ui/api/logout", headers={"Content-Type": "application/json"})
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
  """Provider defaults retain templates and require dashboard authentication."""
  found = client.get("/ui/api/provider-defaults").json()
  assert found["groq"]["api_base"] == "https://api.groq.com/openai/v1", found
  assert found["gemini"]["api_type"] == "gemini" and found["*"] == {
    "api_type": "openai"
  }
  assert "env:CLOUDFLARE_ACCOUNT_ID" in found["cloudflare"]["account_id"], "a token"
  # Cloudflare URL defaults keep the account-ID template.
  account = "https://api.cloudflare.com/client/v4/accounts/{account_id}"
  assert found["cloudflare"]["api_base"] == f"{account}/ai/v1", found["cloudflare"]
  assert (
    found["cloudflare"]["discovery_url"] == f"{account}/ai/models/search?per_page=100"
  )
  assert "cloudflare" not in json.dumps(found["gemini"])
  assert TestClient(api.app).get("/ui/api/provider-defaults").status_code == 401


def test_models_answer_304_until_the_cooling_moves(client: TestClient) -> None:
  """Model requests return 304 for matching ETags until a cooldown changes."""
  first = client.get("/ui/api/models")
  tag = first.headers["etag"]
  assert tag.startswith('"'), first.headers
  same = client.get("/ui/api/models", headers={"if-none-match": tag})
  assert same.status_code == 304, same.text
  assert same.content == b"", "a 304 carries no body"
  api.COOLDOWNS.start("p/small", {"retry-after": "90"}, b"")
  try:
    again = client.get("/ui/api/models", headers={"if-none-match": tag})
  finally:
    api.COOLDOWNS.clear()
  assert again.status_code == 200, again.text
  assert again.headers["etag"] != tag


def test_data(client: TestClient) -> None:
  response = client.get("/ui/api/models")
  assert response.status_code == 200, response.text
  models = response.json()
  assert [row["id"] for row in models] == ["p/big", "p/small", "p/embed"], "all rows"
  assert models[0] == {
    "id": "p/big",
    "provider": "p",
    "slug": "big",
    "name": None,
    "mode": "chat",
    "max_input_tokens": 1000,
    "max_output_tokens": None,
    "rpm": None,
    "tpm": None,
    "tools": True,
    "reasoning": True,
    "effort": "medium",
    "efforts": [],
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
    "affinity": status["affinity"],
  }, status
  assert set(status["affinity"]) == {"mode", "enabled", "count", "chance", "slow"}, (
    status
  )
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
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200
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
  assert (
    client.delete(
      "/ui/api/keys/laptop", headers={"Content-Type": "application/json"}
    ).status_code
    == 204
  )
  assert (
    client.delete(
      "/ui/api/keys/laptop", headers={"Content-Type": "application/json"}
    ).status_code
    == 404
  )
  assert (
    client.post("/v1/chat/completions", json=body, headers=bearer).status_code == 401
  )


def test_hook_legend_rows(client: TestClient, state_folder: Path) -> None:
  """An authenticated hooks request returns the installed hook's legend."""
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200, login.text
  assert TestClient(api.app).get("/ui/api/hooks").status_code == 401, (
    "a session is needed"
  )
  assert client.get("/ui/api/hooks").json() == {"legend": []}, "no hook file yet"
  folder = state_folder / "hooks"
  folder.mkdir(parents=True, exist_ok=True)
  (folder / "owui_auto_reasoning_effort.py").write_text(
    Path("hooks/owui_auto_reasoning_effort.py").read_text(encoding="utf-8"),
    encoding="utf-8",
  )
  body = client.get("/ui/api/hooks").json()
  module = hooks.load(folder / "owui_auto_reasoning_effort.py")
  assert module is not None
  assert body == {"legend": module.on_init()}, body


def test_provider_hook_surface_changes_save() -> None:
  """Provider and model hook surface selections each send one save with the same hook path."""
  run_app_js(
    "state, hookFormRow",
    """
sandbox.setTimeout = () => 0;
sandbox.clearTimeout = () => {};
sandbox.sessionStorage = sandbox.localStorage = { getItem: () => null };
probe.state.files = [{ path: 'providers.yml', text: 'saved fixture data' }];
probe.state.forms = [{ p: {
  hooks: [{ 'on-upstream': 'hooks/provider.py' }],
  models: { 'fixture/*': { hooks: [{ 'on-upstream': 'hooks/model.py' }] } },
} }];
probe.state.formSaved = [JSON.stringify(probe.state.forms[0])];
const sent = [];
sandbox.takeFile = sandbox.refreshEnv = async () => {};
sandbox.renderCard = sandbox.renderForm = sandbox.renderFiles = sandbox.refresh = () => {};
sandbox.fetch = async (url, options) => {
  sent.push({ url, method: options.method, body: JSON.parse(options.body) });
  return { ok: true, status: 200, json: async () => ({}) };
};
(async () => {
  for (const path of [['p', 'hooks', 0], ['p', 'models', 'fixture/*', 'hooks', 0]]) {
    const list = path.slice(0, -1).reduce((value, key) => value[key], probe.state.forms[0]);
    const file = list[0]['on-upstream'];
    const html = probe.hookFormRow(list[0], path, [file]);
    const attr = html.match(/<select ([a-z-]+)=/)[1];
    const key = attr.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    const target = { value: 'on-answer', dataset: { [key]: JSON.stringify(path) } };
    const count = sent.length;
    await node('provider-form').handlers.change[0]({ target });
    assert.strictEqual(JSON.stringify(list[0]), JSON.stringify({ 'on-answer': file }));
    assert.strictEqual(sent.length, count + 1, 'each selection saves once');
    assert.deepStrictEqual(sent.at(-1), {
      url: 'ui/api/providers', method: 'PUT',
      body: { path: 'providers.yml', blocks: JSON.parse(JSON.stringify(probe.state.forms[0])) },
    });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
  )


def test_hook_rows_and_update(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Settings expose hook metadata, and the update endpoint calls the updater."""
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "one.py").write_text(
    "# ---\n# version: 1.2.0\n# author: nemoe7\n# scope: provider\n# targets: [p]\n# ---\n"
    "def on_answer(answer, model):\n  return answer\n",
    encoding="utf-8",
  )
  rows = {
    row["name"]: row for row in client.get("/ui/api/settings").json()["hook_rows"]
  }
  assert rows["one.py"] == {
    "name": "one.py",
    "path": "hooks/one.py",
    "title": "",
    "author": "nemoe7",
    "version": "1.2.0",
    "scope": "provider",
    "targets": ["p"],
    "surfaces": [],
    "runs": [],
    "enabled": True,
    "problem": "",
    "settings": [],
    "values": {},
    "record": {},
  }, rows
  before = {
    "one.py": {
      "sha256": "b" * 64,
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "c" * 40,
    }
  }
  after = {"one.py": {**before["one.py"], "sha256": "d" * 64, "version": "1.2.0"}}
  state = {"records": {}}
  seen: list[object] = []
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])

  def fake_update(
    entries,
    folder=None,
    lock_path=None,
    only_missing=False,
    take=None,
    report=None,
    expected_commit=None,
    expected_hashes=None,
  ):
    seen.append((list(entries), folder, only_missing, take))
    if report is not None:
      report.append("source owner/name at main did not read")
    state["records"] = after
    return ["one.py"]

  monkeypatch.setattr(remote, "update", fake_update)
  sources = [
    {"repo": "owner/name", "path": "hooks", "ref": "main", "auto_update": False}
  ]
  monkeypatch.setattr(
    settings,
    "load",
    lambda path=None: {
      "hooks": {"dir": "hooks", "sources": sources, "disabled": [], "order": {}}
    },
  )
  state["records"] = before
  found = client.post(
    "/ui/api/hooks/update",
    json={"source": sources[0], "expect": {"commit": "c" * 40, "hashes": {}}},
  )
  assert found.status_code == 200, found.text
  assert found.json() == {
    "moved": ["one.py"],
    "report": ["source owner/name at main did not read"],
    "hooks": [
      {
        "name": "one.py",
        "moved": True,
        "before": before["one.py"],
        "after": after["one.py"],
      }
    ],
  }, found.text
  assert seen == [(sources, hooks.ROOT / "hooks", False, None)]
  monkeypatch.setattr(
    settings,
    "load",
    lambda path=None: {
      "hooks": {"dir": "hooks", "sources": [], "disabled": [], "order": {}}
    },
  )
  empty = client.post(
    "/ui/api/hooks/update", headers={"Content-Type": "application/json"}
  )
  assert (
    empty.status_code == 400
    and empty.json()["error"]["type"] == "invalid_request_error"
  ), empty.text
  assert TestClient(api.app).post("/ui/api/hooks/update").status_code == 401, (
    "a session is needed"
  )


def test_hook_file_delete(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Hook deletion removes files and lock records only inside the configured folder."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "drop.py").write_text(
    "# ---\n# version: 1.0.0\n# ---\ndef on_answer(a, m):\n  return a\n",
    encoding="utf-8",
  )
  state = {"records": {}}
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])
  monkeypatch.setattr(
    remote,
    "write_records",
    lambda records, path=None: state.update(records=dict(records)),
  )
  state["records"] = {
    "drop.py": {
      "sha256": "b" * 64,
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "c" * 40,
    }
  }
  assert (
    TestClient(api.app)
    .request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
    .status_code
    == 401
  ), "a session is needed"
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  missing = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "hooks/nope.py"}
  )
  assert missing.status_code == 404, missing.text
  error = missing.json()["error"]
  assert error["type"] == "invalid_request_error" and error["code"] == 404
  assert error["message"]
  assert error["locale_key"] == "hook.error.file_missing_text"
  assert error["locale_params"] == {"name": "hooks/nope.py"}
  escape = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "config/daedalus.yml"}
  )
  assert escape.status_code == 404, escape.text
  found = client.request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
  assert found.status_code == 200, found.text
  assert not (root / "drop.py").exists()
  assert state["records"] == {}


def test_hook_file_delete_with_a_relative_folder(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Hook deletion resolves a relative configured folder."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "drop.py").write_text(
    "# ---\n# version: 1.0.0\n# ---\ndef on_answer(a, m):\n  return a\n",
    encoding="utf-8",
  )
  state = {"records": {}}
  monkeypatch.setattr(remote, "read_records", lambda path=None: state["records"])
  monkeypatch.setattr(
    remote,
    "write_records",
    lambda records, path=None: state.update(records=dict(records)),
  )
  monkeypatch.setattr(hooks, "ROOT", Path("."))
  monkeypatch.chdir(state_folder)
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  found = client.request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
  assert found.status_code == 200, found.text
  assert not (root / "drop.py").exists()


def test_hook_file_delete_clears_the_settings_entries(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Hook deletion clears disabled, order, and on-request references."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "drop.py").write_text(
    "# ---\n# version: 1.0.0\n# ---\ndef on_answer(a, m):\n  return a\n",
    encoding="utf-8",
  )
  path = state_folder / "settings.yml"
  path.write_text(
    "hooks:\n  dir: hooks\n  disabled:\n    - drop.py\n  order:\n    on-request:\n      - drop.py\n"
    "  on-request:\n    - drop.py\n  on-prompt: []\n  on-chunk: []\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(settings, "DEFAULT_PATH", path)
  monkeypatch.setattr(remote, "read_records", lambda path=None: {})
  monkeypatch.setattr(remote, "write_records", lambda records, path=None: None)
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  found = client.request("DELETE", "/ui/api/hooks/file", json={"name": "hooks/drop.py"})
  assert found.status_code == 200, found.text
  group = settings.load(path)["hooks"]
  assert "drop.py" not in group["disabled"], group
  assert "drop.py" not in group["order"]["on-request"], group
  assert "drop.py" not in (group.get("on-request") or []), group


def test_a_hook_file_that_stays_answers_a_clear_message(
  client: TestClient, state_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A simulated unlink failure returns 409 and retains the hook file."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  target = root / "locked.py"
  target.write_text("# ---\n# version: 1.0.0\n# ---\n", encoding="utf-8")
  real = Path.unlink

  def refuse(self: Path, *args: object, **kwargs: object) -> None:
    if self == target:
      raise PermissionError(13, "Permission denied", str(self))
    real(self, *args, **kwargs)

  monkeypatch.setattr(Path, "unlink", refuse)
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  answer = client.request(
    "DELETE", "/ui/api/hooks/file", json={"name": "hooks/locked.py"}
  )
  assert answer.status_code == 409, answer.text
  assert "locked.py" in answer.json()["error"]["message"], answer.json()
  assert target.exists(), "a refused delete leaves the file"
  real(target)


def test_a_provider_file_that_stays_answers_a_clear_message(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A simulated unlink failure returns 409 and retains the provider file."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  made = client.post("/ui/api/files", json={"name": "locked"})
  assert made.status_code == 200, made.text
  target = Path(made.json()["path"])
  real = Path.unlink

  def refuse(self: Path, *args: object, **kwargs: object) -> None:
    if self == target:
      raise PermissionError(13, "Permission denied", str(self))
    real(self, *args, **kwargs)

  monkeypatch.setattr(Path, "unlink", refuse)
  answer = client.request("DELETE", "/ui/api/files", json={"path": str(target)})
  assert answer.status_code == 409, answer.text
  assert "locked" in answer.json()["error"]["message"], answer.json()
  assert target.exists(), "a refused delete leaves the file"
  real(target)


def test_hook_scan_and_take(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Hook scans return archive metadata without writing files, and updates install only selected files."""
  commit = "a" * 40
  pages = {
    remote.API.format(repo="owner/name", ref="main"): json.dumps(
      {"sha": commit}
    ).encode(),
    remote.ARCHIVE.format(repo="owner/name", commit=commit): _tar(),
  }
  monkeypatch.setattr(
    remote, "fetch", lambda url, timeout=30.0, report=None: pages.get(url)
  )
  assert TestClient(api.app).post("/ui/api/hooks/scan", json={}).status_code == 401
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  body = {"repo": "https://github.com/owner/name", "path": "hooks", "ref": "main"}
  found = client.post("/ui/api/hooks/scan", json=body)
  assert found.status_code == 200, found.text
  answer = found.json()
  assert answer["repo"] == "owner/name" and answer["commit"] == commit
  assert [row["name"] for row in answer["files"]] == [
    "owner/name/scanned.py",
    "owner/name/selected.py",
  ]
  assert {row["name"]: row["version"] for row in answer["files"]}[
    "owner/name/scanned.py"
  ] == "1.2.0"
  root = hooks.ROOT / "hooks"
  assert not (root / "owner/name/scanned.py").exists(), "a scan writes nothing"
  bad = client.post("/ui/api/hooks/scan", json={"repo": "owner/other"})
  assert bad.status_code == 400, bad.text
  take = client.post(
    "/ui/api/hooks/update",
    json={
      "source": body,
      "take": ["owner/name/selected.py"],
      "expect": {
        "commit": commit,
        "hashes": {row["name"]: row["sha256"] for row in answer["files"]},
      },
    },
  )
  assert take.status_code == 200, take.text
  assert take.json()["moved"] == ["owner/name/selected.py"], take.text
  assert (root / "owner/name/selected.py").is_file()
  assert not (root / "owner/name/scanned.py").exists()


def test_files(
  client: TestClient, folder: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  source = {
    "repo": "fixture/hooks",
    "path": "hooks",
    "ref": "test-ref",
    "auto_update": False,
  }
  settings_path = tmp_path / "settings.yml"
  settings_path.write_text(
    "# Router settings.\nhooks:\n  sources:\n    - repo: fixture/hooks\n"
    "      path: hooks\n      ref: test-ref\n      auto_update: false\n",
    encoding="utf-8",
  )
  monkeypatch.setattr(settings, "DEFAULT_PATH", settings_path)
  monkeypatch.setitem(settings.DEFAULTS["routing"], "escalation", ["fixture-fallback"])
  monkeypatch.setattr(api, "KEYWORDS", api.KEYWORDS)
  # Log in explicitly instead of reusing another test's cookie or relying on the API bearer key.
  client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  listing = client.get("/ui/api/files")
  assert isinstance(listing.json(), list), listing.text[:400]
  names = [item["path"] for item in listing.json()]
  assert names == [str(path) for path in dashboard.FILES], names
  path = str(settings.DEFAULT_PATH)
  assert (
    client.put("/ui/api/files", json={"path": path, "text": ""}).status_code == 400
  ), "the Settings page owns the settings file"
  shown = client.get("/ui/api/settings").json()
  assert shown["file"] == {"hooks": {"sources": [source]}}, (
    "the settings file supplies the hook source"
  )
  root = folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "selected.py").write_text(
    "def on_answer(value, model, headers):\n  return value\n"
  )
  assert "hooks/selected.py" in client.get("/ui/api/settings").json()["hook_files"], (
    "the settings page lists the hook files, so a row selects its own"
  )
  assert shown["defaults"] == settings.DEFAULTS, shown
  assert shown["headroom_available"] is False, shown

  async def ready() -> bool:
    return True

  monkeypatch.setattr(headroom, "available", ready)
  assert client.get("/ui/api/settings").json()["headroom_available"] is True
  before = settings.DEFAULT_PATH.read_text()
  bad = client.put("/ui/api/settings", json={"changes": {"balance": {"fault": 0}}})
  invalid_request(bad)
  bad = client.put("/ui/api/settings", json={"changes": {"limits": {"calls": 1}}})
  invalid_request(bad)
  huge = client.put(
    "/ui/api/settings",
    json={"changes": {"limits": {"request": 99999999999999999999}}},
  )
  invalid_request(huge)
  assert settings.DEFAULT_PATH.read_text() == before, "a bad value is not written"
  unknown = client.put("/ui/api/settings", json={"changes": {"x": {"y": 1}}})
  assert unknown.status_code == 422, unknown.text
  assert client.put("/ui/api/settings", json={"changes": 1}).status_code == 400
  changes = {
    "limits": {"slow": 12, "calls": 5, "repeats": 6, "shortest": 10, "longest": 3000},
    "catalog": {"every": 0},
  }
  saved = client.put("/ui/api/settings", json={"changes": changes})
  assert saved.status_code == 200, saved.text
  text = settings.DEFAULT_PATH.read_text()
  assert "  slow: 12" in text, "the new value is written"
  assert "# Router settings." in text, "the comments stay"
  assert "  every: 0" in text, text
  assert api.SLOW_SECONDS == 12.0, "the save applies the settings"
  assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (5, 6, 10, 3000)
  cleared = client.put("/ui/api/settings", json={"changes": {"limits": {"slow": None}}})
  assert (
    cleared.status_code == 200 and "  slow:" not in settings.DEFAULT_PATH.read_text()
  ), "a cleared key leaves the file"
  assert api.SLOW_SECONDS == settings.DEFAULTS["limits"]["slow"]
  reset = client.put("/ui/api/settings", json={"changes": {"catalog": {"every": None}}})
  assert reset.status_code == 200, reset.text
  assert "  every:" not in settings.DEFAULT_PATH.read_text(), (
    "a cleared key leaves the file"
  )
  words = {"routing": {"escalation": ["ultrathink", "yes", "think hard"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "escalation:\n    - ultrathink\n    - 'yes'\n    - think hard\n" in text, text
  assert api.KEYWORDS and api.KEYWORDS.search("please ultrathink"), "the save applies"
  words = {"routing": {"escalation": ["audit"]}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "escalation:\n    - audit\n" in text and "ultrathink" not in text, text
  words = {"routing": {"escalation": None}}
  assert client.put("/ui/api/settings", json={"changes": words}).status_code == 200
  text = settings.DEFAULT_PATH.read_text()
  assert "  escalation:" not in text, "a cleared key leaves the file"
  assert api.KEYWORDS and api.KEYWORDS.search("fixture-fallback"), (
    "an empty field uses the supplied default"
  )
  assert not api.KEYWORDS.search("ultrathink")
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"hooks": {"on-request": 3}}}
    ).status_code
    == 422
  ), "a hook path is a string"
  hook = {
    "hooks": {
      "on-request": ["hooks/owui_auto_reasoning_effort.py", "hooks/selected.py"]
    }
  }
  assert client.put("/ui/api/settings", json={"changes": hook}).status_code == 200
  assert api.REQUEST_HOOKS == {
    "on-request": ["hooks/owui_auto_reasoning_effort.py", "hooks/selected.py"],
    "on-prompt": [],
    "on-chunk": [],
  }, "the save applies each request hook of the surface"
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"hooks": {"on-request": []}}}
    ).status_code
    == 200
  )
  assert api.REQUEST_HOOKS == {
    "on-request": [],
    "on-prompt": [],
    "on-chunk": [],
  }, "an empty list turns the hook off"
  dark = {"personalization": {"theme": "dark"}}
  assert client.put("/ui/api/settings", json={"changes": dark}).status_code == 200
  assert (
    client.get("/ui/api/settings").json()["file"]["personalization"]["theme"] == "dark"
  )
  blue = client.put(
    "/ui/api/settings", json={"changes": {"personalization": {"theme": "blue"}}}
  )
  invalid_request(blue)
  assert (
    client.put(
      "/ui/api/settings", json={"changes": {"personalization": {"theme": None}}}
    ).status_code
    == 200
  )
  # The YAML view reads and writes the file text.
  text = client.get("/ui/api/settings").json()["text"]
  assert text == settings.DEFAULT_PATH.read_text(), "the text of the file"
  broken = client.put("/ui/api/settings", json={"text": "balance:\n  fault: 0\n"})
  invalid_request(broken)
  assert settings.DEFAULT_PATH.read_text() == text, "a bad text is not written"
  edited = text + "optimization:\n  timeout: 14 # edited\n"
  edited_timeout = 14.0
  saved = client.put("/ui/api/settings", json={"text": edited})
  assert saved.status_code == 200 and saved.json()["text"] == edited, saved.text
  assert (
    settings.DEFAULT_PATH.read_text() == edited
    and headroom.TIMEOUT_SECONDS == edited_timeout
  )
  providers = str(config.DEFAULT_PATH)
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: ["})
  assert broken.status_code == 422, "a YAML error"
  listed = client.put("/ui/api/files", json={"path": providers, "text": "- a\n"})
  assert listed.status_code == 422, "provider blocks only"
  twice = "q:\n  api_key: k\nq:\n  api_key: j\n"
  doubled = client.put("/ui/api/files", json={"path": providers, "text": twice})
  invalid_request(doubled)
  broken = client.put("/ui/api/files", json={"path": providers, "text": "a: [\n"})
  invalid_request(broken)
  assert "<unicode string>" not in broken.text, broken.text
  doubled = client.put(
    "/ui/api/settings", json={"text": "weights:\n  fault: 0.5\n  fault: 0.25\n"}
  )
  invalid_request(doubled)
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
  listed = {f["path"]: f for f in client.get("/ui/api/files").json()}
  assert listed[made.json()["path"]]["shadow"] is None, (
    "no block of the main file shadows the new file"
  )
  assert (
    client.put(
      "/ui/api/files", json={"path": providers, "text": "openrouter:\n  api_key: k\n"}
    ).status_code
    == 200
  )
  listed = {f["path"]: f for f in client.get("/ui/api/files").json()}
  assert listed[made.json()["path"]]["shadow"] == Path(providers).name, (
    "the block of the main file wins the provider keys"
  )
  gone = client.request("DELETE", "/ui/api/files", json={"path": made.json()["path"]})
  assert gone.status_code == 200, gone.text
  assert config.provider_files(config.DEFAULT_PATH) == [], "the file went"
  assert "_file" not in config.get_config().get("openrouter", {}), "the reload drops it"


def test_provider_file_save_loads_the_main_config(client: TestClient) -> None:
  """Saving a provider file preserves main blocks and nests that file under its provider."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200
  made = client.post("/ui/api/files", json={"name": "probe"})
  assert made.status_code == 200, made.text
  saved = client.put(
    "/ui/api/files", json={"path": made.json()["path"], "text": "api_key: k\n"}
  )
  assert saved.status_code == 200, saved.text
  loaded = config.get_config()
  assert "api_key" not in loaded, "the keys of the file must not become providers"
  main = config.load_yaml(config.DEFAULT_PATH.read_text(encoding="utf-8"))
  assert {k: v for k, v in loaded.items() if k != "probe"} == config.expand(main), (
    "the main file keeps its blocks"
  )
  assert loaded["probe"]["_file"] == {"api_key": "k"}, loaded.get("probe")
  assert (
    client.request(
      "DELETE", "/ui/api/files", json={"path": made.json()["path"]}
    ).status_code
    == 200
  )


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


def test_external_provider_yaml_registers_on_files_read(
  client: TestClient, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Creating `{provider}.yml` on disk outside Daedalus registers it and queues a rebuild."""
  queued = []

  def cached() -> None:
    pass

  monkeypatch.setattr(api, "CATALOG_REBUILD_CACHED", cached)
  monkeypatch.setattr(schedule, "request", lambda task: queued.append(task) or True)
  config.load_config(config.DEFAULT_PATH)
  external = folder / "external.yml"
  try:
    external.write_text("", encoding="utf-8")
    listed = client.get("/ui/api/files")
    assert listed.status_code == 200, listed.text
    found = next(item for item in listed.json() if item["path"] == str(external))
    assert found["blocks"] == {"external": {}}
    assert config.get_config()["external"][config.FILE_KEY] == {}
    assert queued == [cached]
  finally:
    external.unlink(missing_ok=True)
    config.load_config(config.DEFAULT_PATH)


def test_catalog(client: TestClient) -> None:
  assert (
    client.post(
      "/ui/api/catalog", headers={"Content-Type": "application/json"}
    ).status_code
    == 503
  ), "no rebuild in tests"
  api.CATALOG_REFRESH = lambda: None
  try:
    schedule.BUSY = True
    assert client.get("/ui/api/status").json()["catalog"]["rebuilding"] is True
    assert (
      client.post(
        "/ui/api/catalog", headers={"Content-Type": "application/json"}
      ).status_code
      == 409
    ), "1 rebuild at a time"
    schedule.BUSY = False
    assert (
      client.post(
        "/ui/api/catalog", headers={"Content-Type": "application/json"}
      ).status_code
      == 202
    )
  finally:
    api.CATALOG_REFRESH, schedule.BUSY = None, False
  assert TestClient(api.app).post("/ui/api/catalog").status_code == 401


def test_reset(client: TestClient) -> None:
  api.PENALTIES.record("p/big", 0.1)
  api.COOLDOWNS.begin("p/big", {}, b"")
  api.PENALTIES.pin("chat", "koinos", "p/big")
  assert api.PENALTIES.weights(["p/big"])["p/big"] < 1 and api.COOLDOWNS.ends()
  assert (
    client.post(
      "/ui/api/reset", headers={"Content-Type": "application/json"}
    ).status_code
    == 200
  )
  assert api.PENALTIES.weights(["p/big"]) == {"p/big": 1.0}, "the weights go back to 1"
  assert api.COOLDOWNS.ends() == {}, "the cooldowns end"
  assert api.PENALTIES.pinned("chat", "koinos") == "p/big", "the pins stay"
  assert TestClient(api.app).post("/ui/api/reset").status_code == 401


def test_mutations_require_json_before_actions(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  now = time.time()
  monkeypatch.setattr(api.PENALTIES, "clock", lambda: now)
  monkeypatch.setattr(api.COOLDOWNS, "clock", lambda: now)
  browser = TestClient(api.app)
  login = {"username": "admin", "password": MASTER}
  assert browser.post("/ui/api/login", json=login).status_code == 200
  api.PENALTIES.record("p/big", 0.1)
  api.COOLDOWNS.begin("p/big", {}, b"")
  weights = api.PENALTIES.weights(["p/big"])
  cooldowns = api.COOLDOWNS.ends()
  session = browser.cookies.get(dashboard.COOKIE)
  actions = (
    ("POST", "reset"),
    ("POST", "catalog"),
    ("POST", "limits"),
    ("POST", "updates"),
    ("POST", "hooks/update"),
    ("POST", "logout"),
    ("PUT", "settings"),
    ("DELETE", "keys/missing"),
  )
  for content_type in (
    None,
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
  ):
    headers = {"Origin": "https://other.test"}
    if content_type is not None:
      headers["Content-Type"] = content_type
    for method, path in actions:
      answer = browser.request(method, f"/ui/api/{path}", headers=headers)
      assert answer.status_code == 400, (method, path, content_type, answer.text)
      error = answer.json()["error"]
      assert error["code"] == 400 and error["type"] == "invalid_request_error"
      assert "application/json" in error["message"]
    assert api.PENALTIES.weights(["p/big"]) == weights
    assert api.COOLDOWNS.ends() == cooldowns
    assert browser.cookies.get(dashboard.COOKIE) == session
    assert browser.get("/ui/api/status", headers=headers).status_code == 200
    assert TestClient(api.app).post("/ui/api/reset", headers=headers).status_code == 401
  headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://other.test",
  }
  assert browser.post("/ui/api/reset", headers=headers).status_code == 200
  assert api.PENALTIES.weights(["p/big"]) == {"p/big": 1.0}
  assert api.COOLDOWNS.ends() == {}
  browser.cookies.clear()
  headers[dashboard.HEADER] = session
  assert browser.post("/ui/api/reset", headers=headers).status_code == 200
  assert browser.post("/ui/api/logout", headers=headers).status_code == 200
  assert browser.get("/ui/api/status").status_code == 401
  outside = TestClient(api.app)
  assert outside.post("/ui/api/logout").status_code == 400
  assert (
    outside.post(
      "/ui/api/logout", headers={"Content-Type": "application/json"}
    ).status_code
    == 200
  )


def test_a_wrong_shape_is_refused_at_save(client: TestClient) -> None:
  """Invalid provider shapes return 422, and valid shapes save."""
  login = {"username": "admin", "password": MASTER}
  assert client.post("/ui/api/login", json=login).status_code == 200, "the session"
  made = client.post("/ui/api/files", json={"name": "shapetest"})
  assert made.status_code == 200, made.text
  path = made.json()["path"]
  try:
    for text, word in (
      ("api_key: k\ntier: TIER-B\n", "tier"),
      ("api_key: k\nmodels: nope\n", "models"),
      ("api_key: k\nhooks: 7\n", "hooks"),
      ("api_key: k\nexclude: {a: 1}\n", "exclude"),
      ("api_key: k\ndiscovery_match: nope\n", "discovery_match"),
      ("api_key: k\ntier:\n  TIER-B: TIER-B\n", "TIER-B"),
    ):
      reply = client.put("/ui/api/files", json={"path": path, "text": text})
      assert reply.status_code == 422, (word, reply.text)
      invalid_request(reply)
    good = "api_key: k\ntier:\n  TIER-B: [a]\n"
    assert (
      client.put("/ui/api/files", json={"path": path, "text": good}).status_code == 200
    ), good
  finally:
    client.request("DELETE", "/ui/api/files", json={"path": path})


def test_a_failed_login_waits_and_locks(client: TestClient) -> None:
  """Login failures delay responses and enforce a lock until its expiry."""
  os.environ[dashboard.DAEDALUS_MASTER_KEY] = MASTER
  dashboard.LOGIN_FAILURES.clear()
  wrong = {"username": "admin", "password": MASTER + "x"}
  good = {"username": "admin", "password": MASTER}
  started = time.monotonic()
  assert client.post("/ui/api/login", json=wrong).status_code == 401
  assert time.monotonic() - started >= dashboard.LOGIN_DELAY, "a failed try waits"
  delay = dashboard.LOGIN_DELAY
  dashboard.LOGIN_DELAY = 0.0
  try:
    for _ in range(dashboard.LOGIN_LIMIT - 1):
      assert client.post("/ui/api/login", json=wrong).status_code == 401
    locked = client.post("/ui/api/login", json=wrong)
    assert locked.status_code == 429, locked.text
    assert "Wait" in locked.json()["error"]["message"], locked.text
    assert client.post("/ui/api/login", json=good).status_code == 429, "the lock holds"
    host = next(iter(dashboard.LOGIN_FAILURES))
    dashboard.LOGIN_FAILURES[host] = (
      dashboard.LOGIN_LIMIT,
      time.time() - dashboard.LOGIN_LOCK - 1,
    )
    assert client.post("/ui/api/login", json=good).status_code == 200, "the lock ends"
  finally:
    dashboard.LOGIN_DELAY = delay
    dashboard.LOGIN_FAILURES.clear()


def test_notifications_endpoint(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  assert (
    client.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    ).status_code
    == 200
  )
  store.record_rebuild(
    reason="scheduled",
    models=3,
    added=["p/new"],
    removed=["p/old"],
    changed=["p/big"],
    failed=["kilo"],
  )
  store.record_rebuild(
    reason="unchanged",
    models=3,
    added=[],
    removed=[],
    changed=[],
    failed=[],
  )
  store.record_rebuild(
    reason="failed",
    models=3,
    added=[],
    removed=[],
    changed=[],
    failed=["groq"],
  )
  updates.save(
    {
      "repo": "nemoe7/daedalus",
      "current": "v1.0.0",
      "channel": "release",
      "latest": "v1.1.0",
      "url": "https://github.com/nemoe7/daedalus/releases/tag/v1.1.0",
      "behind": None,
      "update": True,
      "error": None,
    }
  )
  resets = [
    {
      "at": 1_791_600_000.0,
      "provider": "groq",
      "client": "owui",
      "kind": "tokens",
      "span": "minute",
    }
  ]
  warnings = [
    {
      "at": 1_791_599_900.0,
      "model": "p/big",
      "client": None,
      "kind": "requests",
      "span": "minute",
      "limit": 100.0,
      "remaining": 5.0,
      "reset": 1_791_600_050.0,
      "share": 0.05,
    }
  ]
  monkeypatch.setattr(api.LIMITS, "resets", lambda: resets)
  monkeypatch.setattr(api.LIMITS, "warnings", lambda: warnings)
  try:
    answer = client.get("/ui/api/notifications")
    assert answer.status_code == 200
    data = answer.json()
    rebuilds = data["rebuilds"]
    assert [event["reason"] for event in rebuilds[:2]] == ["failed", "scheduled"]
    assert all(event["reason"] != "unchanged" for event in rebuilds)
    assert rebuilds[0]["failed"] == ["groq"], "a provider failure still notifies"
    assert rebuilds[1]["added"] == ["p/new"]
    assert rebuilds[1]["changed"] == ["p/big"]
    assert data["update"]["latest"] == "v1.1.0"
    assert data["resets"] == resets
    assert data["resets"][0]["provider"] == "groq"
    assert data["resets"][0]["client"] == "owui"
    assert data["limits"] == warnings, "the remembered warning keeps its event time"
  finally:
    client.cookies.clear()


def test_update_check_now(client: TestClient) -> None:
  assert (
    client.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    ).status_code
    == 200
  )
  with pytest.MonkeyPatch.context() as patch:
    patch.setattr(updates, "__version__", "v1.0.0")
    patch.setattr(
      updates, "_fetch", lambda url: {"tag_name": "v9.9.9", "html_url": "https://x"}
    )
    answer = client.post(
      "/ui/api/updates", headers={"Content-Type": "application/json"}
    )
    assert answer.status_code == 200
    data = answer.json()
    assert data["latest"] == "v9.9.9" and data["update"] is True
    assert updates.read()["latest"] == "v9.9.9"
  client.cookies.clear()


def test_a_failed_update_check_keeps_the_glyph_and_the_word() -> None:
  """A failed update check restores button availability, icon markup, and label text."""
  run_app_js(
    "icon",
    """
const button = node('update-check');
button.closest = () => button;
node('settings').handlers.click[0]({ target: button }).then(() => {
  assert(button.disabled === false, 'the button is live again');
  assert(button.innerHTML.includes(probe.icon('refresh_cw')), 'the glyph returns');
  assert(button.innerHTML.includes(LOCALE['action.check_now_button']), 'the word returns');
});
""",
  )


def test_dashboard_dialog_modes_clear_stale_state() -> None:
  """Dialog helpers reset field visibility, initial values, and danger classes."""
  run_app_js(
    "ask, askPair",
    """
node('modal').showModal = () => {};
const classes = new Set();
node('modal-ok').classList = {
  toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
};
probe.askPair('Client key', 'Write both values.', 'Key name', 'Provider key', 'name', 'value');
assert.strictEqual(node('modal-field').hidden, false, 'a pair shows the first field');
assert.strictEqual(node('modal-field2').hidden, false, 'a pair shows the second field');
assert(!classes.has('danger'), 'a pair is not dangerous');
probe.ask('Delete key', 'The key goes away.', 'Delete', true);
assert.strictEqual(node('modal-field').hidden, true, 'a confirmation hides the first field');
assert.strictEqual(node('modal-field2').hidden, true, 'a confirmation hides the second field');
assert(classes.has('danger'), 'a dangerous confirmation marks its action');
probe.ask('New provider', 'Write a name.', 'Create', false, 'Provider name', 'acme');
assert.strictEqual(node('modal-field').hidden, false, 'an input mode shows the first field');
assert.strictEqual(node('modal-field2').hidden, true, 'an input mode keeps the second field hidden');
assert(!classes.has('danger'), 'a later safe mode clears danger');
assert.strictEqual(node('modal-input').placeholder, 'Provider name');
assert.strictEqual(node('modal-input').value, 'acme');
""",
  )


def test_value_editor_validates_numbers_and_clears_modal_constraints() -> None:
  """Out-of-bounds or invalid numeric text cannot reset a value or leave dialog constraints."""
  run_app_js(
    "editValue",
    """
node('modal').showModal = () => {};
const box = node('modal-input');
box.removeAttribute = (name) => { delete box[name]; };
box.removeEventListener = (type, fn) => { box.handlers[type] = box.handlers[type].filter(item => item !== fn); };
box.checkValidity = () => box.pattern !== '(?!)';
Object.defineProperty(node('made-input'), 'valueAsNumber', { get() { return Number(this.value); } });
node('made-input').checkValidity = function check() {
  if (this.value === '') return true;
  const value = Number(this.value);
  if (this.min && value < Number(this.min)) return false;
  if (this.max && value > Number(this.max)) return false;
  if (this.step && this.step !== 'any') return (value - Number(this.min || 0)) % Number(this.step) === 0;
  return true;
};
sandbox.Event = class { constructor(type) { this.type = type; } };
const input = node('synthetic-value');
input.dataset.value = 'synthetic';
input.dispatchEvent = () => {};
(async () => {
  for (const [type, value, confirm, expected, count, min, max, step] of [
    ['number', 'abc', true, '120', 0, '', '', ''],
    ['number', '1e999', true, '120', 0, '', '', ''],
    ['number', '1.25', true, '1.25', 1, '', '', ''],
    ['number', '', true, '', 1, '', '', ''],
    ['number', '', false, '120', 0, '', '', ''],
    ['number', '2', true, '120', 0, '0', '1', 'any'],
    ['number', '-0.5', true, '120', 0, '0', '1', 'any'],
    ['number', '0.5', true, '0.5', 1, '0', '1', 'any'],
    ['password', 'synthetic-secret', false, '120', 0, '', '', ''],
  ]) {
    input.type = type;
    input.value = '120';
    input.min = min;
    input.max = max;
    input.step = step;
    let writes = 0;
    const done = probe.editValue(input, () => { writes += 1; });
    assert.strictEqual(box.type, type === 'password' ? 'password' : 'text');
    assert.strictEqual(box.inputMode, type === 'number' ? 'decimal' : '');
    box.value = value;
    box.handlers.input.forEach(fn => fn());
    sandbox.__settle(confirm);
    await done;
    assert.strictEqual(input.value, expected);
    assert.strictEqual(writes, count);
    assert.strictEqual(box.type, 'text');
    assert.strictEqual(box.pattern, undefined);
    assert.strictEqual(box.inputMode, '');
    assert.strictEqual(box.handlers.input.length, 0);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
  )
  page = Path("daedalus/dashboard/ui/index.html").read_text(encoding="utf-8")
  assert re.search(
    r'<button\b(?=[^>]*value="cancel")(?=[^>]*\bformnovalidate\b)[^>]*>', page
  )


def test_login_rejects_surrogates_without_skipping_failure_controls(
  client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Malformed credentials follow the failed-login path, while valid Unicode works."""
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  monkeypatch.setenv(dashboard.DAEDALUS_USERNAME, "utilisateur-é")
  monkeypatch.setenv(dashboard.DAEDALUS_PASSWORD, "mot-de-passe-秘密")
  monkeypatch.setattr(dashboard, "LOGIN_FAILURES", {})
  monkeypatch.setattr(dashboard, "LOGIN_DELAY", 0.001)
  credentials = {"username": "utilisateur-é", "password": "mot-de-passe-秘密"}
  for field in ("username", "password"):
    for surrogate in ("\ud800", "\udfff"):
      malformed = {**credentials, field: surrogate}
      started = time.monotonic()
      response = client.post(
        "/ui/api/login",
        content=json.dumps(malformed),
        headers={"Content-Type": "application/json"},
      )
      assert response.status_code == 401
      assert time.monotonic() - started >= dashboard.LOGIN_DELAY
      assert "set-cookie" not in response.headers
  host = next(iter(dashboard.LOGIN_FAILURES))
  assert dashboard.LOGIN_FAILURES[host][0] == 4
  assert client.post("/ui/api/login", json=credentials).status_code == 200
  assert not dashboard.LOGIN_FAILURES
  dashboard.LOGIN_FAILURES[host] = (dashboard.LOGIN_LIMIT, time.time())
  assert client.post("/ui/api/login", json=credentials).status_code == 429


def test_numeric_metadata_names_rendered_settings() -> None:
  """Numeric constraints refer to settings defined in the current form metadata."""
  run_app_js(
    "SETTINGS, DECIMALS, MINIMA, MAXIMA",
    """
const names = new Set(probe.SETTINGS.flatMap(([group, title, fields]) =>
  fields.map(([key]) => `${group}.${key}`)));
for (const key of [...probe.DECIMALS, ...Object.keys(probe.MINIMA), ...Object.keys(probe.MAXIMA)]) {
  assert(names.has(key), key);
}
for (const key of ['success', 'fault', 'slow', 'hourly', 'rate_limit']) {
  assert(probe.DECIMALS.has(`balance.${key}`));
}
assert.strictEqual(probe.MINIMA['limits.calls'], 2);
assert.strictEqual(probe.MAXIMA['limits.calls'], 100);
assert.strictEqual(probe.MAXIMA['optimization.timeout'], 86400);
""",
  )


@pytest.mark.parametrize("mode", [None, "session", "race"])
def test_race_legend_covers_visible_history(mode) -> None:
  run_app_js(
    "state, renderLegend, renderRequestTable",
    f"const mode = {json.dumps(mode)};\n"
    + r"""
probe.state.settings = mode === null ? null : {
  defaults: { affinity: { mode } }, file: {},
};
probe.state.legendExtra = [['rtN', 'synthetic <label>']];
const raceShown = () => node('legend-body').innerHTML.includes('<code>rce</code>');
probe.renderLegend();
assert.strictEqual(raceShown(), mode === 'race');
const row = { at: Date.now() / 1000, model: 'p/synthetic', status: 200,
  transition: { reason: 'rce' } };
probe.state.requests = [row];
probe.renderRequestTable();
assert(raceShown());
assert(node('legend-body').innerHTML.includes(LOCALE['request.route.race_text']));
assert(node('legend-body').innerHTML.includes('synthetic &lt;label&gt;'));
probe.state.requestSearch = 'no-match';
probe.renderRequestTable();
assert.strictEqual(raceShown(), mode === 'race');
probe.state.requestSearch = '';
probe.renderRequestTable();
assert(raceShown());
probe.state.requestStatus = 'bad';
probe.renderRequestTable();
assert.strictEqual(raceShown(), mode === 'race');
probe.state.requestStatus = 'all';
row.at -= 7200;
probe.state.requestHours = 1;
probe.renderRequestTable();
assert.strictEqual(raceShown(), mode === 'race');
probe.state.requestHours = 0;
probe.renderRequestTable();
assert(raceShown());
const selection = { isCollapsed: false, anchorNode: {} };
sandbox.getSelection = () => selection;
node('requests').contains = () => true;
probe.state.requests = [];
probe.renderRequestTable();
assert(raceShown(), 'a deferred redraw keeps the meaning of the visible code');
selection.isCollapsed = true;
probe.renderRequestTable();
assert.strictEqual(raceShown(), mode === 'race');
assert(node('legend-body').innerHTML.includes('synthetic &lt;label&gt;'));
""",
  )


@pytest.mark.parametrize(
  "text",
  [
    "limits: [private-source-marker",
    'limits:\n  wait: "private-source-marker',
    "limits:\n  wait: private-source-marker\n  wait: another-value\n",
    "limits:\n  wait: private-source-marker\x00\n",
  ],
  ids=["parser", "scanner", "duplicate", "reader"],
)
def test_settings_form_rejects_malformed_existing_yaml(
  client, tmp_path, monkeypatch, text
):
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200
  path = tmp_path / "broken-settings.yml"
  original = text.encode("utf-8")
  path.write_bytes(original)
  monkeypatch.setattr(settings, "DEFAULT_PATH", path)
  before = api.SLOW_SECONDS
  response = client.put("/ui/api/settings", json={"changes": {"limits": {"slow": 12}}})
  assert response.status_code == 422
  error = response.json()["error"]
  assert error["type"] == "invalid_request_error"
  assert "private-source-marker" not in error["message"]
  assert "another-value" not in error["message"]
  assert "\n" not in error["message"]
  assert path.read_bytes() == original
  assert api.SLOW_SECONDS == before


def test_key_use_and_update_check_times_keep_empty_states() -> None:
  """Status times share relative markup without changing creation dates or empty states."""
  run_app_js(
    "state, renderKeys, maintenanceCard, timeText, stamp, line",
    """
const checked = Date.now() / 1000 - 120;
const key = {name: 'synthetic', created: checked - 3600, used: checked};
probe.renderKeys([key]);
assert(node('keys').innerHTML.includes(probe.timeText(checked)));
assert(node('keys').innerHTML.includes(probe.stamp(key.created)));
probe.renderKeys([{...key, used: null}]);
assert(node('keys').innerHTML.includes(LOCALE['settings.keys.used_never_text']));
assert(!node('keys').innerHTML.includes(probe.timeText(checked)));
probe.state.notifications = {update: {at: checked, channel: 'release', latest: '1', current: '1'}};
assert(probe.maintenanceCard().includes(probe.timeText(checked)));
probe.state.notifications = {update: null};
const checkedRow = '<div class="field"><span>' + LOCALE['update.checked_text']
  + '</span><span>-</span></div>';
assert(probe.maintenanceCard().includes(checkedRow),
  'the checked row rides the shared settings row');
const card = probe.maintenanceCard();
assert(card.includes('<div class="field"><span>' + LOCALE['update.state_title'] + '</span>'),
  'the update state reads as a labelled settings row');
assert(!card.includes('<div class="line">'), 'no overview row rhythm remains in the card');
assert(card.includes('class="line rebuild field"'),
  'the catalog control keeps the shared row rhythm');
""",
  )


def test_balance_count_format_keeps_rounding_and_unit_boundaries() -> None:
  """Counts keep whole-number rounding below 1,000 and floor larger positive values."""
  run_app_js(
    "balanceCount",
    """
const values = [0, 999, 1234, 998765432, 1000000000, 2.5, 3.5, 999.9, -2.5, -0.5];
const expected = [0, 999, '1K', '998M', '1B', 2, 4, 1000, -2, -0];
for (let i = 0; i < values.length; i++) {
  const wanted = typeof expected[i] === 'number' ? expected[i].toLocaleString() : expected[i];
  assert.strictEqual(probe.balanceCount(values[i]), wanted);
}
""",
  )


def test_long_balance_values_are_compact_with_exact_hover_text() -> None:
  """Large-balance markup uses compact values and full-value titles."""
  run_app_js(
    "balanceValue, overviewLimits, renderLimits",
    """
LOCALE["limits.value.of_text"] = "{remaining}|{limit}";
const items = [
  {kind: 'hourly_requests', value: '9007199254740993', limit: '9007199254740995', share: 1},
  {kind: 'hourly_requests', value: String(10n ** 400n - 1n), limit: String(10n ** 400n), share: 1},
  {kind: 'balance', value: 1234567890123.45, limit: null, share: null},
  {kind: 'pollen', value: -1234567890123.45, limit: null, share: null},
];
for (const item of items) {
  const shown = probe.balanceValue(item);
  const full = probe.balanceValue(item, false);
  assert(shown.length < full.length);
  assert(!shown.includes('Infinity'));
  const data = {checked: null, lanes: [], providers: [{name: 'p', items: [item]}]};
  const overview = probe.overviewLimits(data);
  probe.renderLimits(data);
  for (const markup of [overview, node('balances').innerHTML]) {
    assert(markup.includes(shown));
    assert(markup.includes(`title="${full}"`));
  }
}
const hourly = items[0];
assert.strictEqual(probe.balanceValue(hourly, false),
  `${BigInt(hourly.value).toLocaleString()}|${BigInt(hourly.limit).toLocaleString()}`);
const ordinary = {kind: 'hourly_requests', value: '198', limit: '200', share: .99};
assert.strictEqual(probe.balanceValue(ordinary), probe.balanceValue(ordinary, false));
""",
  )


@pytest.mark.parametrize("contract", ["render", "toggle"])
@pytest.mark.parametrize(
  "disabled, remaining",
  [
    (["shared.py", "missing.py"], ["missing.py", "b/repo/shared.py"]),
    (["shared", "missing.py"], ["missing.py", "b/repo/shared.py"]),
    (["a/repo/shared.py", "missing.py"], ["missing.py"]),
    (
      ["a/repo/shared", "b/repo/shared.py", "missing.py"],
      ["b/repo/shared.py", "missing.py"],
    ),
  ],
)
def test_hook_switch_effective_state(
  tmp_path, monkeypatch, contract, disabled, remaining
) -> None:
  monkeypatch.setattr(hooks, "DISABLED", set(disabled))
  monkeypatch.setattr(hooks, "folder", lambda: tmp_path)
  names = ["a/repo/shared.py", "b/repo/shared.py", "other.py", "broken.py"]
  payload = {
    "defaults": settings.DEFAULTS,
    "file": {"hooks": {"disabled": disabled}},
    "hook_rows": [
      {
        "name": name,
        "path": f"hooks/{name}",
        "enabled": hooks.enabled(tmp_path / name) and name != "broken.py",
        "problem": "invalid" if name == "broken.py" else "",
      }
      for name in names
    ],
  }
  run_app_js(
    "state, hooksManager, toggleHook, listValue, settingsChanges",
    "probe.state.settings = "
    + json.dumps(payload)
    + ";\n"
    + "const contract = "
    + json.dumps(contract)
    + ";\n"
    + "const remaining = "
    + json.dumps(remaining)
    + ";\n"
    + r"""
vm.runInContext('saveSettings = () => {};', sandbox);
if (contract === 'render') {
  const switches = new Map([...probe.hooksManager().matchAll(/<input\b[^>]*data-hook-toggle="([^"]+)"[^>]*>/g)]
    .map(match => [match[1], /\schecked(?:\s|>)/.test(match[0])]));
  assert.strictEqual(switches.size, probe.state.settings.hook_rows.length);
  for (const row of probe.state.settings.hook_rows) assert.strictEqual(switches.get(row.name), row.enabled);
} else {
  const target = 'a/repo/shared.py';
  probe.toggleHook(target, true);
  assert.deepStrictEqual([...probe.listValue('hooks', 'disabled')].sort(), remaining.sort());
  assert.deepStrictEqual([...probe.settingsChanges().hooks.disabled].sort(), remaining.sort());
  probe.toggleHook(target, false);
  assert.deepStrictEqual([...probe.listValue('hooks', 'disabled')].sort(), [...remaining, target].sort());
  probe.toggleHook(target, true);
  assert.deepStrictEqual([...probe.listValue('hooks', 'disabled')].sort(), remaining.sort());
}
""",
  )


@pytest.mark.parametrize(
  "operation,error_type,status",
  [
    ("write-yaml", dashboard.yaml.YAMLError, 422),
    ("write-settings", settings.SettingsError, 422),
    ("hook", settings.SettingsError, 422),
    ("key", dashboard.keys.KeyNameError, 400),
    ("provider", ValueError, 400),
    ("settings", settings.SettingsError, 422),
  ],
)
def test_validation_errors_keep_exception_details_private(
  client, tmp_path, monkeypatch, caplog, operation, error_type, status
) -> None:
  """Validation failures keep exception details out of responses and logs without saving changes."""
  path = tmp_path / "settings.yml"
  path.write_text("hooks:\n  disabled: [blocked.py]\n", encoding="utf-8")
  monkeypatch.setattr(settings, "DEFAULT_PATH", path)
  assert (
    client.post(
      "/ui/api/login", json={"username": "admin", "password": MASTER}
    ).status_code
    == 200
  )
  paths = {path, *dashboard.FILES}
  before = {p: p.read_bytes() if p.exists() else None for p in paths}
  before_keys = dashboard.keys.listing(store.MODELS_DB)
  before_config = json.dumps(config.get_config(), sort_keys=True)
  before_slow = api.SLOW_SECONDS
  writes = []
  marker = "private-exception-marker"

  def fail(*args, **kwargs):
    raise error_type(marker)

  def changed(*args, **kwargs):
    writes.append((args, kwargs))

  monkeypatch.setattr(dashboard.saved_env, "save", changed)
  caplog.set_level(logging.WARNING, logger=dashboard.logger.name)
  caplog.clear()
  if operation.startswith("write-"):
    monkeypatch.setattr(dashboard, "check_file", fail)
    response = dashboard.write_config(
      path, "replacement", changed, changed, key_values={"FIXTURE_KEY": marker}
    )
  elif operation == "hook":
    monkeypatch.setattr(settings, "update_text", fail)
    response = dashboard.clear_hook_entries("blocked.py", changed, changed)
  elif operation == "key":
    monkeypatch.setattr(dashboard.keys, "add", fail)
    response = client.post("/ui/api/keys", json={"name": "fixture-key"})
  elif operation == "provider":
    monkeypatch.setattr(dashboard, "stage_keys", fail)
    response = client.put(
      "/ui/api/providers",
      json={"path": str(dashboard.FILES[0]), "blocks": {"p": {"api_key": marker}}},
    )
  else:
    monkeypatch.setattr(settings, "update_text", fail)
    response = client.put(
      "/ui/api/settings", json={"changes": {"limits": {"slow": 12}}}
    )
  body = invalid_request(response, status)
  assert marker not in json.dumps(body) and "\n" not in body["error"]["message"]
  records = [r for r in caplog.records if r.name == dashboard.logger.name]
  assert len(records) == 1
  assert records[0].levelno == logging.WARNING
  assert records[0].args == (error_type.__name__,)
  assert records[0].exc_info is None and records[0].stack_info is None
  assert marker not in caplog.text
  assert writes == []
  assert {p: p.read_bytes() if p.exists() else None for p in paths} == before
  assert dashboard.keys.listing(store.MODELS_DB) == before_keys
  assert json.dumps(config.get_config(), sort_keys=True) == before_config
  assert api.SLOW_SECONDS == before_slow


def test_model_cards_show_registered_db_limits_and_exclude_provider_only_limits(
  client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """`/ui/api/models` and `openModelModal` surface per-model DB limits and omit provider-only limits."""
  db_path = tmp_path / "models.sqlite3"
  store.write_store(
    [
      {
        "id": "gemini/gemini-3.8-flash",
        "provider": "gemini",
        "slug": "gemini-3.8-flash",
        "mode": "chat",
        "max_input_tokens": 1048576,
        "rpm": 5,
        "tpm": 250000,
      },
      {
        "id": "groq/openai/gpt-oss-120b",
        "provider": "groq",
        "slug": "openai/gpt-oss-120b",
        "mode": "chat",
        "rpm": 30,
        "tpm": 8000,
      },
      {
        "id": "groq/whisper-large-v3",
        "provider": "groq",
        "slug": "whisper-large-v3",
        "mode": "audio_transcription",
        "rpm": 20,
        "tpm": 8000,
      },
      {
        "id": "cloudflare/@cf/google/gemma-4-26b-a4b-it",
        "provider": "cloudflare",
        "slug": "@cf/google/gemma-4-26b-a4b-it",
        "mode": "chat",
        "rpm": 300,
      },
    ],
    db_path,
  )
  monkeypatch.setattr(store, "MODELS_DB", db_path)
  monkeypatch.setitem(
    CONFIG,
    "gemini",
    {
      "api_key": "k",
      "models": {"gemini-3.8-flash": {"rpm": 5, "tpm": 250000}},
    },
  )
  monkeypatch.setitem(
    CONFIG,
    "groq",
    {
      "api_key": "k",
      "rpm": 30,
      "tpm": 8000,
      "models": {"whisper-*": {"mode": "audio_transcription", "rpm": 20}},
    },
  )
  monkeypatch.setitem(CONFIG, "cloudflare", {"api_key": "k"})
  res = client.get("/ui/api/models")
  assert res.status_code == 200
  by_id = {row["id"]: row for row in res.json()}
  assert (
    by_id["gemini/gemini-3.8-flash"]["rpm"] == 5
    and by_id["gemini/gemini-3.8-flash"]["tpm"] == 250000
  )
  assert (
    by_id["groq/openai/gpt-oss-120b"]["rpm"] is None
    and by_id["groq/openai/gpt-oss-120b"]["tpm"] is None
  )
  assert (
    by_id["groq/whisper-large-v3"]["rpm"] == 20
    and by_id["groq/whisper-large-v3"]["tpm"] is None
  )
  assert (
    by_id["cloudflare/@cf/google/gemma-4-26b-a4b-it"]["rpm"] == 300
    and by_id["cloudflare/@cf/google/gemma-4-26b-a4b-it"]["tpm"] is None
  )

  run_app_js(
    "state, openModelModal, useStats",
    """
node('modal').showModal = () => {};
probe.state.requests = [];
probe.state.limits = {
  providers: [
    { name: 'groq', items: [{ kind: 'hourly_requests', value: '180', limit: '200', share: 0.9 }] },
    { name: 'cloudflare', items: [{ kind: 'neurons', value: 9000, limit: 10000, share: 0.9 }] },
  ],
  lanes: [
    {
      model: 'groq/whisper-large-v3',
      rows: [{ kind: 'requests', span: 'day', limit: 14400, remaining: 14312 }],
    },
    {
      model: 'cloudflare/@cf/google/gemma-4-26b-a4b-it',
      rows: [{ kind: 'neurons', span: 'day', limit: 10000, remaining: 9000 }],
    },
  ],
};
probe.openModelModal({
  id: 'groq/whisper-large-v3',
  slug: 'whisper-large-v3',
  mode: 'audio_transcription',
  rpm: 20,
  tpm: null,
  flags: [],
  efforts: [],
});
let body = node('model-modal-body').innerHTML;
assert(body.includes('>20<') && body.includes('>RPM<'), body);
assert(!body.includes('>TPM<') && !body.includes('8,000'), 'provider-only TPM stays out');
assert(body.includes('14,312 of 14,400'), 'DB lane limit renders even with zero served requests');
assert(!body.includes('180') && !body.includes('200'), 'provider-only hourly limit stays out');

probe.openModelModal({
  id: 'cloudflare/@cf/google/gemma-4-26b-a4b-it',
  slug: '@cf/google/gemma-4-26b-a4b-it',
  mode: 'chat',
  rpm: 300,
  tpm: null,
  flags: [],
  efforts: [],
});
body = node('model-modal-body').innerHTML;
assert(body.includes('>300<') && body.includes('>RPM<'), body);
assert(!body.includes('Neurons') && !body.includes('9,000'), 'provider-only neurons stay out');
""",
  )


def test_section_search_matches_content_and_config_keys_with_highlights() -> None:
  """Section search indexes section content and config keys and wraps matches safely."""
  run_app_js(
    "state, highlightText, highlightSnippet, sectionSearchTerms, filterRail",
    """
assert.strictEqual(
  probe.highlightText('Forward <OWUI> chat ID', 'owui'),
  'Forward &lt;<mark class="search-hit">OWUI</mark>&gt; chat ID',
);
assert(probe.highlightSnippet('privacy.forward_owui_chat_id', 'owui')
  .includes('<mark class="search-hit">owui</mark>'));

const host = {
  id: 'settings',
  querySelector: () => null,
  querySelectorAll: () => [],
};
const privacyTerms = probe.sectionSearchTerms(host, 'privacy');
assert(privacyTerms.includes('privacy.forward_owui_chat_id'), 'config key indexed');
assert(privacyTerms.some((term) => term.includes('Forward OWUI chat ID')), 'field label indexed');
const optTerms = probe.sectionSearchTerms(host, 'optimization');
assert(optTerms.some((term) => term.includes('Headroom')), 'section content indexed');

const makeButton = (section, label) => {
  const labelNode = { dataset: { label }, textContent: label, innerHTML: label };
  const matchNode = { hidden: true, innerHTML: '' };
  return {
    dataset: { section },
    textContent: label,
    hidden: false,
    labelNode,
    matchNode,
    querySelector: (sel) => (sel === '.rail-label' ? labelNode : sel === '.rail-match' ? matchNode : null),
  };
};
const nav = {
  hidden: false,
  buttons: [makeButton('routing', 'Routing'), makeButton('privacy', 'Privacy'), makeButton('yaml', 'YAML')],
  querySelectorAll: () => nav.buttons,
};
const empty = { hidden: true };
const settingsHost = {
  id: 'settings',
  dataset: {},
  querySelectorAll: (sel) => (sel === ':scope > .rail > .sections' ? [nav] : []),
  querySelector: (sel) => (sel === ':scope > .rail > .rail-empty' ? empty : null),
};
sandbox.matchMedia = () => ({ matches: true, addEventListener: () => {} });
probe.state.settings = { text: 'privacy: { forward_owui_chat_id: false }', defaults: {} };
probe.state.settingsSearch = 'forward_owui_chat_id';
probe.filterRail(settingsHost);
assert.deepStrictEqual(nav.buttons.map((b) => b.hidden), [true, false, false]);
assert.strictEqual(nav.buttons[1].matchNode.hidden, true, 'UI section hides config key in rail');
assert.strictEqual(nav.buttons[1].matchNode.innerHTML, '');
assert.strictEqual(nav.buttons[2].matchNode.hidden, false, 'YAML section shows config key snippet');
assert(nav.buttons[2].matchNode.innerHTML.includes('<mark class="search-hit">forward_owui_chat_id</mark>'));
""",
  )


def test_hook_settings_save_leaves_base_config_unchanged(
  client: TestClient, state_folder: Path
) -> None:
  """A hook page save writes the hook JSON file, and never the base settings file."""
  root = state_folder / "hooks"
  root.mkdir(parents=True, exist_ok=True)
  (root / "fields.py").write_text(
    "# ---\n"
    "# settings:\n"
    "#   - key: target\n"
    "#     type: choice\n"
    "#     choices: [kilo, opencode]\n"
    "#     default: opencode\n"
    "# ---\n"
    "def on_answer(answer, model):\n  return answer\n",
    encoding="utf-8",
  )
  base = settings.DEFAULT_PATH
  before = base.read_bytes()
  config = hooks.config_file(root / "fields.py")
  assert config == state_folder / "hooks" / "fields" / "settings.json"
  assert not config.exists()
  answer = client.put(
    "/ui/api/hooks/settings", json={"name": "fields.py", "values": {"target": "kilo"}}
  )
  assert answer.status_code == 200, answer.text
  assert base.read_bytes() == before
  assert not (root / "fields.json").exists()
  assert json.loads(config.read_text(encoding="utf-8")) == {"target": "kilo"}
  rows = {
    row["name"]: row for row in client.get("/ui/api/settings").json()["hook_rows"]
  }
  assert rows["fields.py"]["values"] == {"target": "kilo"}
  invalid = client.put(
    "/ui/api/hooks/settings", json={"name": "fields.py", "values": {"target": "curl"}}
  )
  assert invalid.status_code == 422, invalid.text
  assert json.loads(config.read_text(encoding="utf-8")) == {"target": "kilo"}
  assert base.read_bytes() == before
  outside = client.put(
    "/ui/api/hooks/settings",
    json={"name": "../fields.py", "values": {"target": "kilo"}},
  )
  assert outside.status_code == 404, outside.text
  assert not (state_folder / "fields.json").exists()
  assert not (state_folder / "hooks" / "fields.json").exists()
  assert base.read_bytes() == before


def test_relative_timestamps_emit_utc_datetime_and_unit_boundary_delays() -> None:
  """Relative timestamps carry UTC metadata and schedule unit-boundary refresh delays."""
  run_app_js(
    "isoUtc, nextRelativeDelay, refreshRelativeNodes, timeText, stamp",
    """
const baseNow = Date.UTC(2026, 10, 2, 12, 0, 15);
assert.strictEqual(probe.nextRelativeDelay(baseNow - 10_000, baseNow), 50_000);
assert.strictEqual(probe.nextRelativeDelay(baseNow - 7_200_000, baseNow), 3_600_000);
const stampSec = (baseNow - 120_000) / 1000;
const iso = probe.isoUtc(stampSec);
const item = {
  textContent: "stale",
  title: "",
  getAttribute: (name) => (name === "datetime" ? iso : null),
};
const delay = probe.refreshRelativeNodes(
  { querySelectorAll: () => [item] },
  { now: baseNow, schedule: false },
);
assert.strictEqual(item.textContent, "2 minutes ago");
assert.strictEqual(item.title, probe.stamp(stampSec));
assert.strictEqual(delay, 60_000);
""",
  )
