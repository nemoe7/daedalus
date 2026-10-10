import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from daedalus.config import settings
from daedalus.providers import hooks
from daedalus.routing import loops, penalties
from daedalus.server import api, upstream


def expect_error(folder: Path, text: str, message: str) -> None:
  path = folder / "bad.yml"
  path.write_text(text, encoding="utf-8")
  try:
    settings.load(path)
  except settings.SettingsError as exc:
    assert message in str(exc), exc
  else:
    raise AssertionError(f"no error for {text!r}")


def test_load(folder: Path) -> None:
  values = settings.load(folder / "missing.yml")
  limits = values["limits"]
  assert (limits["request"], limits["wait"], limits["slow"]) == (600.0, 60.0, 30.0)
  assert values["balance"]["fault"] == penalties.FAULT
  assert "think hard" in values["routing"]["escalation"], "the default keywords"
  assert values["routing"]["switch"] == ["clanker"]
  shipped_path = Path(__file__).parents[2] / "config" / "daedalus.yml"
  shipped = yaml.safe_load(shipped_path.read_text(encoding="utf-8")) or {}
  assert set(shipped) <= {"hooks"}, (
    "the shipped file holds the initial hook source alone"
  )
  assert set(shipped.get("hooks") or {}) == {"sources"}, (
    "no hook runs at the first start. The dashboard writes its other changes into the file"
  )
  loaded = settings.load(shipped_path)
  assert loaded["hooks"]["sources"] == [
    {"repo": "nemoe7/daedalus", "path": "hooks", "ref": "main", "auto_update": False}
  ], "the shipped file names the repo of the hooks"
  loaded["hooks"]["sources"] = values["hooks"]["sources"]
  assert loaded == values, "the shipped file leaves every other default in place"
  path = folder / "daedalus.yml"
  path.write_text("limits:\n  wait: 120\nbalance:\n  fault: 0.25\n", encoding="utf-8")
  values = settings.load(path)
  assert values["limits"]["slow"] == 30.0, "slow keeps its own default"
  assert values["balance"]["fault"] == 0.25 and values["balance"]["success"] == 1.5
  path.write_text("", encoding="utf-8")
  assert settings.load(path)["affinity"]["mode"] == "session", "an empty file"
  assert settings.load(path)["affinity"]["change_on_draw"] is True
  expect_error(folder, "affinity:\n  change_on_draw: 1\n", "true or false")
  expect_error(folder, "server:\n  port: 1\n", "unknown group 'server'")
  expect_error(folder, "balance:\n  factor: 2\n", "unknown key balance.factor")
  expect_error(
    folder, "hooks:\n  on-request: 3\n", "must be a hook file path, or a list"
  )
  expect_error(
    folder,
    "hooks:\n  on-request: [hooks/x.py, 2]\n",
    "must be a hook file path",
  )
  expect_error(folder, "hooks:\n  on-later: a.py\n", "unknown key hooks.on-later")
  hooks = settings.parse('hooks:\n  on-request: ""\n')["hooks"]
  assert hooks["on-request"] == [] and hooks["sources"] == []
  assert hooks["order"] == {}
  hooks = settings.parse("hooks:\n  on-request: hooks/x.py\n")["hooks"]
  assert hooks["on-request"] == ["hooks/x.py"], "1 path on its own works"
  hooks = settings.parse("hooks:\n  on-request: [hooks/x.py, hooks/y.py]\n")["hooks"]
  assert hooks["on-request"] == ["hooks/x.py", "hooks/y.py"]
  hooks = settings.parse(
    "hooks:\n  order:\n    on-request: [hooks/y.py, hooks/x.py]\n"
  )["hooks"]
  assert hooks["order"] == {"on-request": ["hooks/y.py", "hooks/x.py"]}
  expect_error(folder, "hooks:\n  order: []\n", "order must hold surfaces")
  expect_error(
    folder,
    "hooks:\n  order:\n    on-later: [hooks/x.py]\n",
    "unknown surface on-later",
  )
  text = settings.update_text(
    "",
    {"hooks": {"on-request": ["hooks/x.py", "hooks/y.py"]}},
  )
  (folder / "hooks.yml").write_text(text, encoding="utf-8")
  assert settings.load(folder / "hooks.yml")["hooks"]["on-request"] == [
    "hooks/x.py",
    "hooks/y.py",
  ]
  text = settings.update_text(
    "hooks:\n  on-request: hooks/a.py # keep\n",
    {"hooks": {"on-request": "hooks/b.py"}},
  )
  assert text == "hooks:\n  on-request: hooks/b.py # keep\n", text
  expect_error(folder, "balance:\n  weights: 1\n", "true or false")
  expect_error(folder, "limits:\n  wait: true\n", "above 0")
  expect_error(folder, "limits:\n  wait: 0\n", "above 0")
  expect_error(folder, "affinity:\n  stay: 1\n", "below 1")
  expect_error(folder, "- a\n", "groups of keys")
  (folder / "twice.yml").write_text("balance:\n  fault: 0.5\n  fault: 0.25\n")
  try:
    settings.load(folder / "twice.yml")
  except yaml.YAMLError as exc:
    assert "duplicate key 'fault'" in str(exc) and "line 3" in str(exc), exc
  else:
    raise AssertionError("no error for a duplicate key")
  expect_error(folder, "routing:\n  escalation: think\n", "list of words or phrases")
  expect_error(folder, "routing:\n  escalation: [1]\n", "list of words or phrases")
  expect_error(folder, "routing:\n  switch: clanker\n", "list of words or phrases")
  expect_error(folder, "personalization:\n  theme: blue\n", "system, light or dark")
  path.write_text("personalization:\n  theme: dark\n", encoding="utf-8")
  assert settings.load(path)["personalization"]["theme"] == "dark"
  assert settings.load(path)["personalization"]["time_format"] == "24h", (
    "24h by default"
  )
  expect_error(folder, "personalization:\n  time_format: 25h\n", "24h or 12h")
  path.write_text("personalization:\n  time_format: 12h\n", encoding="utf-8")
  assert settings.load(path)["personalization"]["time_format"] == "12h"
  expect_error(folder, "routing:\n  escalation: [' ']\n", "list of words or phrases")
  path.write_text(
    "routing:\n  escalation: [ultrathink, ' think hard ']\n", encoding="utf-8"
  )
  keywords = settings.load(path)["routing"]["escalation"]
  assert keywords == ["ultrathink", "think hard"], "a config list replaces the default"


def test_loop_settings() -> None:
  limits = settings.parse("")["limits"]
  assert (
    limits["calls"],
    limits["repeats"],
    limits["shortest"],
    limits["longest"],
  ) == (
    3,
    4,
    20,
    2000,
  )
  moved = settings.parse(
    "limits:\n  calls: 100\n  repeats: 16\n  shortest: 1000\n  longest: 10000\n"
  )["limits"]
  assert (moved["calls"], moved["repeats"], moved["shortest"], moved["longest"]) == (
    100,
    16,
    1000,
    10000,
  )
  for text, message in (
    ("limits:\n  calls: true\n", "whole number"),
    ("limits:\n  calls: 1\n", "between 2 and 100"),
    ("limits:\n  repeats: 17\n", "between 2 and 16"),
    ("limits:\n  shortest: 1001\n", "between 1 and 1000"),
    ("limits:\n  longest: 10001\n", "between 1 and 10000"),
    ("limits:\n  shortest: 21\n  longest: 20\n", "at most limits.longest"),
  ):
    with pytest.raises(settings.SettingsError, match=message):
      settings.parse(text)


def test_affinity_settings(folder: Path) -> None:
  """The affinity defaults, the 3 modes, and the 2 bounds of the race numbers."""
  assert settings.parse("")["affinity"] == {
    "mode": "session",
    "idle": 3600.0,
    "stay": 0.85,
    "change_on_draw": True,
    "count": 1,
    "chance": 0.05,
    "slow": 30.0,
    "penalty": 0.9,
  }
  expect_error(folder, "affinity:\n  mode: fast\n", "none, session or race")
  expect_error(folder, "affinity:\n  count: 0\n", "between 1 and 10")
  expect_error(folder, "affinity:\n  count: 11\n", "between 1 and 10")
  expect_error(folder, "affinity:\n  count: 1.5\n", "whole number")
  assert settings.parse("affinity:\n  count: 3\n")["affinity"]["count"] == 3
  expect_error(folder, "affinity:\n  chance: 2\n", "from 0 to 1")
  expect_error(folder, "affinity:\n  chance: -0.1\n", "from 0 to 1")
  expect_error(folder, "affinity:\n  penalty: 2\n", "at most 1")
  expect_error(folder, "affinity:\n  penalty: 0\n", "above 0")
  expect_error(folder, "affinity:\n  slow: 0\n", "above 0")
  # The 2 groups of the old shape stop, and each names the mode that replaces it.
  expect_error(folder, "session_affinity:\n  enabled: true\n", "affinity.mode: session")
  expect_error(folder, "parallel:\n  enabled: true\n", "affinity.mode: race")
  path = folder / "affinity.yml"
  path.write_text("affinity:\n  mode: race\n  chance: 0\n", encoding="utf-8")
  values = settings.load(path)
  assert values["affinity"]["mode"] == "race" and values["affinity"]["chance"] == 0.0
  api.apply_settings(values)
  try:
    assert api.AFFINITY_MODE == "race" and api.PARALLEL_ENABLED is True
    assert api.AFFINITY is True and api.PENALTIES.race is True
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
  assert api.PARALLEL_ENABLED is False and api.PENALTIES.race is False
  assert api.AFFINITY_MODE == "session" and api.AFFINITY is True
  values = settings.parse("affinity:\n  mode: none\n")
  api.apply_settings(values)
  try:
    assert api.AFFINITY is False and api.PARALLEL_ENABLED is False
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))


def test_privacy_settings(folder: Path) -> None:
  """OWUI chat IDs stay private by default, and the privacy switch can forward them."""
  assert settings.parse("")["privacy"] == {"forward_owui_chat_id": False}
  expect_error(
    folder,
    "privacy:\n  forward_owui_chat_id: 1\n",
    "true or false",
  )
  api.apply_settings(settings.parse("privacy:\n  forward_owui_chat_id: true\n"))
  try:
    assert upstream.FORWARD_OWUI_CHAT_ID is True
  finally:
    api.apply_settings(settings.parse(""))
  assert upstream.FORWARD_OWUI_CHAT_ID is False


def test_apply(folder: Path) -> None:
  path = folder / "off.yml"
  path.write_text(
    "affinity:\n  mode: none\n  change_on_draw: false\n"
    "balance:\n  weights: false\n"
    "limits:\n  calls: 5\n  repeats: 6\n  shortest: 10\n  longest: 3000\n",
    encoding="utf-8",
  )
  store = api.PENALTIES
  original = store.path
  store.path = lambda: folder / "models.sqlite3"
  try:
    api.apply_settings(settings.load(path))
    assert store.change_on_draw is False
    assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (
      5,
      6,
      10,
      3000,
    )
    tool_calls = [
      {
        "role": "assistant",
        "tool_calls": [
          {"id": f"c{index}", "function": {"name": "run", "arguments": "{}"}}
        ],
      }
      for index in range(5)
    ]
    assert loops.repeated_call([{"role": "user", "content": "go"}, *tool_calls]) == (
      "c4",
      5,
    )
    unit = "abcdefghij"
    assert loops.text_loop(unit * 5) is None
    assert loops.text_loop(unit * 6) == 10
    assert api.Tracker("key", "daedalus/deinos").slot is None, "no pins"
    store.pick = lambda: 0.99
    assert store.record("a", store.fault) == 1.0, "no weights"
    assert store.order([["a", "b", "c"], ["d"]]) == ["a", "b", "c", "d"], "usual order"
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
    store.path = original
  assert (
    api.AFFINITY is True
    and store.enabled is True
    and store.change_on_draw is True
    and api.SLOW_SECONDS == 30.0
  )
  assert (loops.CALLS, loops.REPEATS, loops.SHORTEST, loops.LONGEST) == (3, 4, 20, 2000)


# The CLI runs with a temporary state folder, so it never touches .daedalus-state.
CLI = """
import tempfile
from pathlib import Path
from daedalus import store
from daedalus.catalog import discovery
from daedalus.cli import run
with tempfile.TemporaryDirectory() as folder:
  store.MODELS_DB = Path(folder) / "models.sqlite3"
  discovery.DUMP_DIR = Path(folder) / "dump"
  run()
"""


def test_cli(folder: Path) -> None:
  command = [sys.executable, "-c", CLI]
  (folder / "config").mkdir()
  (folder / "config" / "daedalus.yml").write_text(
    "balance:\n  x: 1\n", encoding="utf-8"
  )
  wrong = subprocess.run(
    [*command, "serve"], cwd=folder, capture_output=True, text=True, check=False
  )
  assert wrong.returncode == 2 and "balance.x" in wrong.stderr, wrong.stderr
  (folder / "config" / "daedalus.yml").write_text("balance:\n  fault: 0.5\n")
  (folder / "config" / "providers").mkdir()
  (folder / "config" / "providers" / "free.yml").write_text(
    "groq:\n  api_key: a\ngroq:\n  api_key: b\n", encoding="utf-8"
  )
  twice = subprocess.run(
    [*command, "serve"], cwd=folder, capture_output=True, text=True, check=False
  )
  assert twice.returncode == 2 and "duplicate key 'groq'" in twice.stderr, twice.stderr
  environment = {**os.environ, "DAEDALUS_PORT": "9100"}
  shown = subprocess.run(
    [*command, "serve", "--help"],
    env=environment,
    capture_output=True,
    text=True,
    check=True,
  )
  assert "default 9100" in shown.stdout, shown.stdout


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
  return tmp_path_factory.mktemp("settings")


def test_timeout_cap() -> None:
  """A timeout stops at 1 day, in the file and in the form."""
  assert settings.check("limits", "request", 86400) == 86400.0
  assert settings.check("optimization", "timeout", 86400) == 86400.0
  for group, key, value in (
    ("limits", "request", 86401),
    ("limits", "wait", 1e20),
    ("optimization", "timeout", 86401),
  ):
    with pytest.raises(settings.SettingsError, match="at most 86400 seconds"):
      settings.check(group, key, value)
  with pytest.raises(settings.SettingsError, match="at most 86400 seconds"):
    settings.parse("limits:\n  request: 90000\n")


def test_headroom_switch() -> None:
  """The Headroom switch is a boolean, in the settings file and in the form."""
  assert settings.check("optimization", "enabled", True) is True
  assert settings.check("optimization", "enabled", False) is False
  assert (
    settings.parse("optimization:\n  enabled: false\n")["optimization"]["enabled"]
    is False
  )
  with pytest.raises(settings.SettingsError, match="true or false"):
    settings.parse("optimization:\n  enabled: 1\n")


def test_routing_threshold() -> None:
  """The bar of the classifier is a number from 0 to 1, and its default is the shipped one."""
  assert settings.parse("")["routing"]["threshold"] == 0.75
  assert settings.parse("routing:\n  threshold: 0.95\n")["routing"]["threshold"] == 0.95
  assert settings.parse("routing:\n  threshold: 0\n")["routing"]["threshold"] == 0.0
  assert settings.parse("routing:\n  threshold: 1\n")["routing"]["threshold"] == 1.0
  for bad in ("-0.1", "1.1", "true", "'high'", "[1]"):
    with pytest.raises(settings.SettingsError, match="from 0 to 1"):
      settings.parse(f"routing:\n  threshold: {bad}\n")
  with pytest.raises(settings.SettingsError, match="unknown key"):
    settings.parse("routing:\n  bar: 0.5\n")


def test_apply_takes_the_threshold(folder: Path) -> None:
  """A save moves the bar of the router, and the shipped default returns it."""
  path = folder / "threshold.yml"
  path.write_text("routing:\n  threshold: 0.91\n", encoding="utf-8")
  try:
    api.apply_settings(settings.load(path))
    assert api.router.THRESHOLD == 0.91
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
  assert api.router.THRESHOLD == 0.75


def test_pool_names() -> None:
  """The key of each pool is generic, its default is the built-in name, and a save keeps a number-like name."""
  assert settings.parse("")["personalization"]["tier-a"] == "sophos"
  assert (
    settings.parse("personalization:\n  tier-a: fast\n")["personalization"]["tier-a"]
    == "fast"
  )
  assert (
    settings.parse("personalization:\n  images: pics\n")["personalization"]["images"]
    == "pics"
  )
  for bad in ("a/b", "auto", "''", "x" * 41, "[a]"):
    with pytest.raises(settings.SettingsError):
      settings.parse(f"personalization:\n  tier-a: {bad}\n")
  with pytest.raises(settings.SettingsError, match="own name"):
    settings.parse("personalization:\n  tier-a: koinos\n")
  swapped = settings.parse("personalization:\n  tier-a: koinos\n  tier-c: sophos\n")[
    "personalization"
  ]
  assert (swapped["tier-a"], swapped["tier-c"]) == ("koinos", "sophos"), swapped
  text = settings.update_text(
    "personalization:\n  tier-a: sophos\n", {"personalization": {"tier-a": "123"}}
  )
  assert settings.parse(text)["personalization"]["tier-a"] == "123", text


def test_update_text_drops_an_empty_group() -> None:
  """The last key of a group takes the group with it."""
  text = settings.update_text(
    "limits:\n  slow: 30\ncatalog:\n  every: 6\n", {"limits": {"slow": None}}
  )
  assert text == "catalog:\n  every: 6\n", text


def test_update_text_writes_into_a_file_of_comments_only() -> None:
  """A file of comment lines holds no group, and a save writes the changes into it."""
  text = settings.update_text("# Router settings.\n", {"limits": {"slow": 12}})
  assert text == "# Router settings.\nlimits:\n  slow: 12\n", text


def test_update_text_keeps_a_comment_of_a_written_file() -> None:
  """A save keeps the comment lines of a file that holds keys."""
  text = settings.update_text(
    "# Router settings.\nlimits:\n  slow: 30\n", {"limits": {"slow": 12}}
  )
  assert "# Router settings." in text and "  slow: 12" in text, text


def test_hooks_dir_names_the_folder() -> None:
  """`hooks.dir` takes 1 plain folder name, and another value is refused."""
  assert settings.parse("")["hooks"]["dir"] == "hooks"
  assert (
    settings.parse("hooks:\n  dir: mine-2.hooks\n")["hooks"]["dir"] == "mine-2.hooks"
  )
  for bad in ("a/b", "../hooks", "", " hooks", ".", ".."):
    with pytest.raises(settings.SettingsError, match="dir must be 1 folder name"):
      settings.parse(f'hooks:\n  dir: "{bad}"\n')


def test_hooks_sources_take_a_repo_path_ref_and_flag() -> None:
  """`hooks.sources` holds 1 entry per repo, with the folder, the ref and the auto_update flag."""
  assert settings.parse("")["hooks"]["sources"] == []
  text = (
    "hooks:\n  sources:\n    - repo: owner/name\n"
    "      path: hooks/\n      ref: v1.2\n      auto_update: true\n"
  )
  assert settings.parse(text)["hooks"]["sources"] == [
    {"repo": "owner/name", "path": "hooks", "ref": "v1.2", "auto_update": True}
  ]
  url = settings.parse(
    "hooks:\n  sources:\n    - repo: https://github.com/owner/name/tree/main\n"
  )["hooks"]["sources"]
  assert url == [
    {"repo": "owner/name", "path": "", "ref": "main", "auto_update": False}
  ]
  for text, message in (
    ("hooks:\n  sources: nope\n", "must be a list"),
    ("hooks:\n  sources:\n    - path: hooks\n", "owner/name or a GitHub URL"),
    ("hooks:\n  sources:\n    - repo: owner\n", "owner/name or a GitHub URL"),
    ("hooks:\n  sources:\n    - repo: owner/name\n      ref: ''\n", "ref must be"),
    (
      "hooks:\n  sources:\n    - repo: owner/name\n      auto_update: 1\n",
      "auto_update must be",
    ),
    ("hooks:\n  sources:\n    - repo: owner/name\n      extra: 1\n", "unknown key"),
  ):
    with pytest.raises(settings.SettingsError, match=message):
      settings.parse(text)


def test_hooks_disabled_names_stay_off() -> None:
  """`hooks.disabled` holds the names that stay on disk and do not load."""
  assert settings.parse("")["hooks"]["disabled"] == []
  assert settings.parse("hooks:\n  disabled: [one.py, two]\n")["hooks"]["disabled"] == [
    "one.py",
    "two",
  ]
  for bad in ("nope", "[one.py, 7]", "[a/b]"):
    with pytest.raises(settings.SettingsError, match="list of hook names"):
      settings.parse(f"hooks:\n  disabled: {bad}\n")


def test_hooks_remote_is_gone() -> None:
  """The settings file of the old shape names `hooks.sources`, on a load and on a save."""
  for key in ("remote", "remote_hosts"):
    with pytest.raises(settings.SettingsError, match="hooks.sources"):
      settings.parse(f"hooks:\n  {key}: []\n")
  with pytest.raises(settings.SettingsError, match="hooks.sources"):
    settings.update_text("", {"hooks": {"remote": []}})


def test_apply_wires_the_hook_settings(monkeypatch: pytest.MonkeyPatch) -> None:
  """A save takes the hook folder, disabled names, order and sources of a start."""
  seen: list[tuple[list[dict[str, object]], bool, Path | None]] = []

  def record(
    entries: object,
    folder: Path | None = None,
    lock_path: Path | None = None,
    only_missing: bool = False,
  ) -> list[str]:
    seen.append((list(entries), only_missing, folder))  # type: ignore[arg-type]
    return []

  monkeypatch.setattr(api.remote, "update", record)
  api.apply_settings(
    settings.parse(
      "hooks:\n  dir: mine\n  disabled: [one.py]\n"
      "  order:\n    on-request: [mine/two.py, mine/one.py]\n"
      "  sources:\n    - repo: owner/name\n"
    )
  )
  try:
    assert hooks.DIR == "mine" and hooks.DISABLED == {"one.py"}
    assert hooks.ORDER == {"on-request": ["mine/two.py", "mine/one.py"]}
    assert seen == [([], True, hooks.ROOT / "mine")], (
      "a start leaves the file to the Take button"
    )
    api.apply_settings(
      settings.parse(
        "hooks:\n  sources:\n    - repo: owner/name\n      auto_update: true\n"
      )
    )
    assert seen[-1] == (
      [{"repo": "owner/name", "path": "", "ref": "main", "auto_update": True}],
      True,
      hooks.ROOT / "hooks",
    ), "a source with auto_update reads the ref at a start"
  finally:
    api.apply_settings(settings.parse(""))
  assert hooks.DIR == "hooks" and hooks.DISABLED == set() and hooks.ORDER == {}


def test_updates_repo_names_the_repository() -> None:
  """`updates.repo` holds the GitHub repository the check reads, as owner/name."""
  assert settings.parse("")["updates"]["repo"] == "nemoe7/daedalus"
  assert (
    settings.parse("updates:\n  repo: mine/daedalus\n")["updates"]["repo"]
    == "mine/daedalus"
  )
  for bad in ("daedalus", "a/b/c", "", "o//r", "/o/r"):
    with pytest.raises(settings.SettingsError, match="repository"):
      settings.parse(f'updates:\n  repo: "{bad}"\n')
