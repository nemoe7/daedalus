"""Hook updates preview first and install only after confirmation."""

from fastapi.testclient import TestClient
from test_dashboard import MASTER, run_app_js

from daedalus import dashboard
from daedalus.config import remote, settings
from daedalus.server import api


def test_update_endpoint_requires_scanned_commit_and_hashes(
  state_folder, monkeypatch
) -> None:
  """The dashboard install endpoint refuses an unpreviewed write."""
  monkeypatch.setenv(dashboard.DAEDALUS_MASTER_KEY, MASTER)
  monkeypatch.setattr(
    settings,
    "load",
    lambda path=None: {
      "hooks": {"dir": "hooks", "sources": [], "disabled": [], "order": {}}
    },
  )
  client = TestClient(api.app)
  login = client.post("/ui/api/login", json={"username": "admin", "password": MASTER})
  assert login.status_code == 200, login.text
  seen: list[dict] = []
  monkeypatch.setattr(
    remote, "update", lambda *args, **kwargs: seen.append(kwargs) or []
  )
  body = {"repo": "owner/name", "path": "hooks", "ref": "main"}
  missing = client.post(
    "/ui/api/hooks/update", json={"source": body, "take": ["owner/name/one.py"]}
  )
  assert missing.status_code == 400
  assert seen == []
  ok = client.post(
    "/ui/api/hooks/update",
    json={
      "source": body,
      "take": ["owner/name/one.py"],
      "expect": {"commit": "a" * 40, "hashes": {"owner/name/one.py": "b" * 64}},
    },
  )
  assert ok.status_code == 200, ok.text
  assert seen[0]["expected_commit"] == "a" * 40
  assert seen[0]["expected_hashes"] == {"owner/name/one.py": "b" * 64}


def test_check_for_updates_previews_and_waits_for_confirmation() -> None:
  """Check scans configured sources, then installs only after a confirmed dialog."""
  run_app_js(
    "state, runHooksUpdate, takeHooks",
    """
(async () => {
  const calls = [];
  let confirmations = 0;
  let answer = false;
  sandbox.call = async (url, options = {}) => {
    calls.push({url, method: options.method, body: options.body ? JSON.parse(options.body) : null});
    if (url === "hooks/scan") return {repo: "owner/name", path: "hooks", ref: "main", commit: "a".repeat(40),
      files: [{name: "owner/name/one.py", version: "2.0.0", sha256: "b".repeat(64), problem: ""}]};
    return {moved: ["owner/name/one.py"], hooks: [], report: []};
  };
  sandbox.loadSettings = async () => {};
  sandbox.ask = async () => { confirmations += 1; return answer; };
  sandbox.hooksNote = () => {};
  sandbox.showNotice = () => {};
  sandbox.showWrite = () => {};
  sandbox.renderSettings = () => {};
  sandbox.listValue = () => [{repo: "owner/name", path: "hooks", ref: "main", auto_update: false}];
  sandbox.setListValue = () => {};
  sandbox.settingsChanges = () => ({});
  sandbox.settingsDirty = () => false;
  probe.state.settings = {hook_rows: [{name: "owner/name/one.py", version: "1.0.0", record: {sha256: "c".repeat(64)}}]};
  const button = {disabled: false, setAttribute() {}, removeAttribute() {}};
  await probe.runHooksUpdate(button);
  assert.strictEqual(confirmations, 0, "the check itself does not install");
  assert.deepStrictEqual(calls.map(c => c.url), ["hooks/scan"]);
  assert(probe.state.hooksScan, "the scan stays visible for selection");
  await probe.takeHooks();
  assert.strictEqual(confirmations, 1, "install asks once");
  assert.deepStrictEqual(calls.map(c => c.url), ["hooks/scan"], "cancel writes nothing");
  answer = true;
  await probe.takeHooks();
  assert.strictEqual(confirmations, 2);
  const install = calls.find(c => c.url === "hooks/update");
  assert(install, "confirm installs");
  assert.strictEqual(install.body.expect.commit, "a".repeat(40));
  assert.deepStrictEqual(install.body.expect.hashes, {"owner/name/one.py": "b".repeat(64)});
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
  )
