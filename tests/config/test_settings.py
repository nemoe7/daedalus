import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ruamel.yaml.error import YAMLError

from daedalus.config import settings
from daedalus.providers import hooks
from daedalus.routing import loops
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
  assert values == settings.DEFAULTS
  shipped_path = Path(__file__).parents[2] / "config" / "daedalus.yml"
  assert isinstance(settings.load(shipped_path), dict)
  path = folder / "daedalus.yml"
  source = {
    "repo": "example/fixture",
    "path": "plugins",
    "ref": "v-test",
    "auto_update": False,
  }
  path.write_text(yaml.safe_dump({"hooks": {"sources": [source]}}), encoding="utf-8")
  loaded = settings.load(path)
  assert loaded["hooks"]["sources"] == [source]
  loaded["hooks"]["sources"] = values["hooks"]["sources"]
  assert loaded == values
  path.write_text("limits:\n  wait: 120\nbalance:\n  fault: 0.25\n", encoding="utf-8")
  values = settings.load(path)
  assert values["limits"]["slow"] == settings.DEFAULTS["limits"]["slow"]
  assert (
    values["balance"]["fault"] == 0.25
    and values["balance"]["success"] == settings.DEFAULTS["balance"]["success"]
  )
  path.write_text("", encoding="utf-8")
  assert (
    settings.load(path)["affinity"]["mode"] == settings.DEFAULTS["affinity"]["mode"]
  )
  assert (
    settings.load(path)["affinity"]["change_on_draw"]
    == settings.DEFAULTS["affinity"]["change_on_draw"]
  )
  expect_error(folder, "affinity:\n  change_on_draw: 1\n", "affinity.change_on_draw")
  expect_error(folder, "server:\n  port: 1\n", "'server'")
  expect_error(folder, "balance:\n  factor: 2\n", "balance.factor")
  expect_error(folder, "hooks:\n  on-request: 3\n", "hooks.on-request")
  expect_error(
    folder,
    "hooks:\n  on-request: [hooks/x.py, 2]\n",
    "hooks.on-request",
  )
  expect_error(folder, "hooks:\n  on-later: a.py\n", "hooks.on-later")
  hooks = settings.parse('hooks:\n  on-request: ""\n')["hooks"]
  assert hooks["on-request"] == []
  assert hooks["sources"] == settings.DEFAULTS["hooks"]["sources"]
  assert hooks["order"] == settings.DEFAULTS["hooks"]["order"]
  hooks = settings.parse("hooks:\n  on-request: hooks/x.py\n")["hooks"]
  assert hooks["on-request"] == ["hooks/x.py"], "1 path on its own works"
  hooks = settings.parse("hooks:\n  on-request: [hooks/x.py, hooks/y.py]\n")["hooks"]
  assert hooks["on-request"] == ["hooks/x.py", "hooks/y.py"]
  hooks = settings.parse(
    "hooks:\n  order:\n    on-request: [hooks/y.py, hooks/x.py]\n"
  )["hooks"]
  assert hooks["order"] == {"on-request": ["hooks/y.py", "hooks/x.py"]}
  expect_error(folder, "hooks:\n  order: []\n", "hooks.order")
  expect_error(
    folder,
    "hooks:\n  order:\n    on-later: [hooks/x.py]\n",
    "on-later",
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
  expect_error(folder, "balance:\n  weights: 1\n", "balance.weights")
  expect_error(folder, "limits:\n  wait: true\n", "limits.wait")
  expect_error(folder, "limits:\n  wait: 0\n", "limits.wait")
  expect_error(folder, "affinity:\n  stay: 1\n", "affinity.stay")
  expect_error(folder, "- a\n", "bad.yml")
  (folder / "twice.yml").write_text("balance:\n  fault: 0.5\n  fault: 0.25\n")
  try:
    settings.load(folder / "twice.yml")
  except yaml.constructor.ConstructorError as exc:
    assert "'fault'" in exc.problem
    assert exc.problem_mark.line == 2
  else:
    raise AssertionError("no error for a duplicate key")
  expect_error(folder, "routing:\n  escalation: think\n", "routing.escalation")
  expect_error(folder, "routing:\n  escalation: [1]\n", "routing.escalation")
  expect_error(folder, "routing:\n  switch: clanker\n", "routing.switch")
  expect_error(folder, "personalization:\n  theme: blue\n", "personalization.theme")
  path.write_text("personalization:\n  theme: dark\n", encoding="utf-8")
  assert settings.load(path)["personalization"]["theme"] == "dark"
  assert (
    settings.load(path)["personalization"]["time_format"]
    == settings.DEFAULTS["personalization"]["time_format"]
  )
  expect_error(
    folder, "personalization:\n  time_format: 25h\n", "personalization.time_format"
  )
  path.write_text("personalization:\n  time_format: 12h\n", encoding="utf-8")
  assert settings.load(path)["personalization"]["time_format"] == "12h"
  expect_error(folder, "routing:\n  escalation: [' ']\n", "routing.escalation")
  path.write_text(
    "routing:\n  escalation: [ultrathink, ' think hard ']\n", encoding="utf-8"
  )
  keywords = settings.load(path)["routing"]["escalation"]
  assert keywords == ["ultrathink", "think hard"], "a config list replaces the default"


@pytest.mark.parametrize(
  "group,key",
  [
    (group, key)
    for group, values in settings.DEFAULTS.items()
    for key, value in values.items()
    if isinstance(value, float)
  ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_settings(group: str, key: str, value: float) -> None:
  changes = {group: {key: value}}
  with pytest.raises(settings.SettingsError):
    settings.parse(yaml.safe_dump(changes))
  with pytest.raises(settings.SettingsError):
    settings.parse(settings.update_text("", changes))


@pytest.mark.parametrize("value", [1e-7, 1 / 120, 5e-324, 1e12, 25.0, 7.0])
def test_catalog_interval_rejects_unsupported_range(value: float) -> None:
  with pytest.raises(settings.SettingsError):
    settings.parse(yaml.safe_dump({"catalog": {"every": value}}))


@pytest.mark.parametrize("value", [0.0, 1 / 60, 0.5, 6.0, 24.0])
def test_catalog_interval_supported_bounds(value: float) -> None:
  values = settings.parse(yaml.safe_dump({"catalog": {"every": value}}))
  assert values["catalog"]["every"] == value


@pytest.mark.parametrize(
  "group,key",
  [
    ("affinity", "idle"),
    ("balance", "hourly"),
    ("limits", "request"),
    ("catalog", "every"),
  ],
)
def test_settings_integer_overflow(group: str, key: str) -> None:
  changes = {group: {key: 10**400}}
  with pytest.raises(settings.SettingsError):
    settings.parse(yaml.safe_dump(changes))
  with pytest.raises(settings.SettingsError):
    settings.parse(settings.update_text("", changes))


def test_loop_settings() -> None:
  limits = settings.parse("")["limits"]
  assert limits == settings.DEFAULTS["limits"]
  moved = settings.parse(
    "limits:\n  calls: 100\n  repeats: 16\n  shortest: 1000\n  longest: 10000\n"
  )["limits"]
  assert (moved["calls"], moved["repeats"], moved["shortest"], moved["longest"]) == (
    100,
    16,
    1000,
    10000,
  )
  for text, identifiers in (
    ("limits:\n  calls: true\n", ("limits.calls",)),
    ("limits:\n  calls: 1\n", ("limits.calls",)),
    ("limits:\n  repeats: 17\n", ("limits.repeats",)),
    ("limits:\n  shortest: 1001\n", ("limits.shortest",)),
    ("limits:\n  longest: 10001\n", ("limits.longest",)),
    ("limits:\n  shortest: 21\n  longest: 20\n", ("limits.shortest", "limits.longest")),
  ):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(text)
    assert all(identifier in str(error.value) for identifier in identifiers)


def test_affinity_settings(folder: Path) -> None:
  """Affinity settings accept three modes and enforce numeric bounds."""
  assert settings.parse("")["affinity"] == settings.DEFAULTS["affinity"]
  expect_error(folder, "affinity:\n  mode: fast\n", "affinity.mode")
  expect_error(folder, "affinity:\n  count: 0\n", "affinity.count")
  expect_error(folder, "affinity:\n  count: 11\n", "affinity.count")
  expect_error(folder, "affinity:\n  count: 1.5\n", "affinity.count")
  assert settings.parse("affinity:\n  count: 3\n")["affinity"]["count"] == 3
  expect_error(folder, "affinity:\n  chance: 2\n", "affinity.chance")
  expect_error(folder, "affinity:\n  chance: -0.1\n", "affinity.chance")
  expect_error(folder, "affinity:\n  penalty: 2\n", "affinity.penalty")
  expect_error(folder, "affinity:\n  penalty: 0\n", "affinity.penalty")
  expect_error(folder, "affinity:\n  slow: 0\n", "affinity.slow")
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
    api.apply_settings(settings.parse("affinity:\n  mode: session\n"))
  assert api.PARALLEL_ENABLED is False and api.PENALTIES.race is False
  assert api.AFFINITY_MODE == "session" and api.AFFINITY is True
  values = settings.parse("affinity:\n  mode: none\n")
  api.apply_settings(values)
  try:
    assert api.AFFINITY is False and api.PARALLEL_ENABLED is False
  finally:
    api.apply_settings(settings.parse("affinity:\n  mode: session\n"))


def test_privacy_settings(folder: Path) -> None:
  """OWUI chat IDs stay private by default, and the privacy switch can forward them."""
  assert settings.parse("")["privacy"] == {"forward_owui_chat_id": False}
  expect_error(
    folder,
    "privacy:\n  forward_owui_chat_id: 1\n",
    "privacy.forward_owui_chat_id",
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
  baseline = settings.parse(
    "affinity:\n  mode: session\n  change_on_draw: true\n"
    "balance:\n  weights: true\n"
    "limits:\n  slow: 30\n  calls: 3\n  repeats: 4\n  shortest: 20\n  longest: 2000\n"
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
    store.select = lambda: 0.99
    assert store.record("a", store.fault) == 1.0, "no weights"
    assert store.order([["a", "b", "c"], ["d"]]) == ["a", "b", "c", "d"], "usual order"
  finally:
    api.apply_settings(baseline)
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
  assert twice.returncode == 2 and "groq" in twice.stderr, twice.stderr
  environment = {**os.environ, "DAEDALUS_PORT": "9100"}
  shown = subprocess.run(
    [*command, "serve", "--help"],
    env=environment,
    capture_output=True,
    text=True,
    check=True,
  )
  assert shown.stdout
  served_folder = folder / "port-check"
  served_folder.mkdir()
  server = (
    """
import json
import uvicorn
from daedalus import catalog
catalog.refresh = lambda: None
uvicorn.run = lambda *args, **kwargs: print(json.dumps({"port": kwargs["port"]}))
"""
    + CLI
  )
  for args, expected in (([], 9100), (["9200"], 9200)):
    served = subprocess.run(
      [sys.executable, "-c", server, "serve", *args],
      cwd=served_folder,
      env=environment,
      capture_output=True,
      text=True,
      check=True,
    )
    assert json.loads(served.stdout) == {"port": expected}


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
    with pytest.raises(settings.SettingsError) as error:
      settings.check(group, key, value)
    assert f"{group}.{key}" in str(error.value)
  with pytest.raises(settings.SettingsError) as error:
    settings.parse("limits:\n  request: 90000\n")
  assert "limits.request" in str(error.value)


def test_headroom_switch() -> None:
  """The Headroom switch is a boolean, in the settings file and in the form."""
  assert settings.check("optimization", "enabled", True) is True
  assert settings.check("optimization", "enabled", False) is False
  assert (
    settings.parse("optimization:\n  enabled: false\n")["optimization"]["enabled"]
    is False
  )
  with pytest.raises(settings.SettingsError) as error:
    settings.parse("optimization:\n  enabled: 1\n")
  assert "optimization.enabled" in str(error.value)


def test_routing_threshold() -> None:
  """The classifier threshold accepts numbers from zero through one."""
  assert (
    settings.parse("")["routing"]["threshold"]
    == settings.DEFAULTS["routing"]["threshold"]
  )
  assert settings.parse("routing:\n  threshold: 0.95\n")["routing"]["threshold"] == 0.95
  assert settings.parse("routing:\n  threshold: 0\n")["routing"]["threshold"] == 0.0
  assert settings.parse("routing:\n  threshold: 1\n")["routing"]["threshold"] == 1.0
  for bad in ("-0.1", "1.1", "true", "'high'", "[1]"):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(f"routing:\n  threshold: {bad}\n")
    assert "routing.threshold" in str(error.value)
  with pytest.raises(settings.SettingsError) as error:
    settings.parse("routing:\n  bar: 0.5\n")
  assert "routing.bar" in str(error.value)


def test_apply_takes_the_threshold(folder: Path) -> None:
  """Apply the saved threshold and restore the current default when the file is missing."""
  path = folder / "threshold.yml"
  path.write_text("routing:\n  threshold: 0.91\n", encoding="utf-8")
  try:
    api.apply_settings(settings.load(path))
    assert api.router.THRESHOLD == 0.91
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
  assert api.router.THRESHOLD == settings.DEFAULTS["routing"]["threshold"]


def test_pool_names() -> None:
  """Pool keys accept valid names, reject collisions and preserve number-like names on save."""
  assert (
    settings.parse("")["personalization"]["tier-a"]
    == settings.DEFAULTS["personalization"]["tier-a"]
  )
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
  with pytest.raises(settings.SettingsError) as error:
    settings.parse("personalization:\n  tier-a: koinos\n  tier-c: koinos\n")
  assert "personalization" in str(error.value)
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
  """`hooks.dir` accepts one plain folder name and rejects other values."""
  assert settings.parse("")["hooks"]["dir"] == settings.DEFAULTS["hooks"]["dir"]
  assert (
    settings.parse("hooks:\n  dir: mine-2.hooks\n")["hooks"]["dir"] == "mine-2.hooks"
  )
  for bad in ("a/b", "../hooks", "", " hooks", ".", ".."):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(f'hooks:\n  dir: "{bad}"\n')
    assert "hooks.dir" in str(error.value)


def test_hooks_sources_take_a_repo_path_ref_and_flag() -> None:
  """`hooks.sources` holds 1 entry per repo, with the folder, the ref and the auto_update flag."""
  assert settings.parse("")["hooks"]["sources"] == settings.DEFAULTS["hooks"]["sources"]
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
  for text, identifiers in (
    ("hooks:\n  sources: nope\n", ("hooks.sources",)),
    ("hooks:\n  sources:\n    - path: hooks\n", ("hooks.sources", "repo")),
    ("hooks:\n  sources:\n    - repo: owner\n", ("hooks.sources", "repo")),
    (
      "hooks:\n  sources:\n    - repo: owner/name\n      ref: ''\n",
      ("hooks.sources", "ref"),
    ),
    (
      "hooks:\n  sources:\n    - repo: owner/name\n      auto_update: 1\n",
      ("hooks.sources", "auto_update"),
    ),
    (
      "hooks:\n  sources:\n    - repo: owner/name\n      extra: 1\n",
      ("hooks.sources", "extra"),
    ),
  ):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(text)
    assert all(identifier in str(error.value) for identifier in identifiers)


@pytest.mark.parametrize(
  "name",
  [
    "one.py",
    "one",
    "owner/repo/one.py",
    "owner/repo/one",
    "nested/one.py",
    "owner/_repo/one.py",
    f"{'o' * 100}/{'r' * 100}/{'f' * 80}.py",
  ],
)
def test_disabled_hook_identity_round_trip(name):
  """Safe relative identities survive settings validation and serialization."""
  text = settings.update_text("", {"hooks": {"disabled": [name]}})
  assert settings.parse(text)["hooks"]["disabled"] == [name]


@pytest.mark.parametrize(
  "name",
  [
    None,
    7,
    True,
    "",
    "/owner/repo/one.py",
    "../one.py",
    "owner/../one.py",
    "owner/./one.py",
    "owner//one.py",
    "owner/repo/",
    "owner\\repo\\one.py",
    "C:/one.py",
    "owner/repo/one\x00.py",
    f"owner/{'r' * 101}/one.py",
    "owner/" + "r/" * 256 + "one.py",
  ],
)
def test_unsafe_disabled_hook_identities_fail(name):
  with pytest.raises(settings.SettingsError):
    settings.parse(yaml.safe_dump({"hooks": {"disabled": [name]}}))


def test_hooks_disabled_names_stay_off() -> None:
  """`hooks.disabled` holds the names that stay on disk and do not load."""
  assert (
    settings.parse("")["hooks"]["disabled"] == settings.DEFAULTS["hooks"]["disabled"]
  )
  assert settings.parse("hooks:\n  disabled: [one.py, two]\n")["hooks"]["disabled"] == [
    "one.py",
    "two",
  ]
  for bad in ("nope", "[one.py, 7]", "[a/../b]"):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(f"hooks:\n  disabled: {bad}\n")
    assert "hooks.disabled" in str(error.value)


def test_hooks_remote_is_gone() -> None:
  """The settings file of the old shape names `hooks.sources`, on a load and on a save."""
  for key in ("remote", "remote_hosts"):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(f"hooks:\n  {key}: []\n")
    assert f"hooks.{key}" in str(error.value)
    assert "hooks.sources" in str(error.value)
  with pytest.raises(settings.SettingsError) as error:
    settings.update_text("", {"hooks": {"remote": []}})
  assert "hooks.remote" in str(error.value)
  assert "hooks.sources" in str(error.value)


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
        "hooks:\n  dir: fetched\n  sources:\n    - repo: owner/name\n      auto_update: true\n"
      )
    )
    assert seen[-1] == (
      [{"repo": "owner/name", "path": "", "ref": "main", "auto_update": True}],
      True,
      hooks.ROOT / "fetched",
    ), "a source with auto_update reads the ref at a start"
  finally:
    api.apply_settings(settings.parse(""))
  assert hooks.DIR == settings.DEFAULTS["hooks"]["dir"]
  assert hooks.DISABLED == set(settings.DEFAULTS["hooks"]["disabled"])
  assert hooks.ORDER == settings.DEFAULTS["hooks"]["order"]


def test_updates_repo_names_the_repository() -> None:
  """`updates.repo` holds the GitHub repository the check reads, as owner/name."""
  assert settings.parse("")["updates"]["repo"] == settings.DEFAULTS["updates"]["repo"]
  assert (
    settings.parse("updates:\n  repo: mine/daedalus\n")["updates"]["repo"]
    == "mine/daedalus"
  )
  for bad in ("daedalus", "a/b/c", "", "o//r", "/o/r"):
    with pytest.raises(settings.SettingsError) as error:
      settings.parse(f'updates:\n  repo: "{bad}"\n')
    assert "updates.repo" in str(error.value)


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
def test_update_text_wraps_parser_errors_without_source_text(text):
  with pytest.raises(settings.SettingsError) as caught:
    settings.update_text(text, {"limits": {"slow": 12}})
  cause = caught.value.__cause__
  assert isinstance(cause, YAMLError)
  message = str(caught.value)
  assert "private-source-marker" not in message
  assert "another-value" not in message
  assert "\n" not in message
  mark = getattr(cause, "problem_mark", None)
  if mark is not None:
    assert str(mark.line + 1) in message
    assert str(mark.column + 1) in message
