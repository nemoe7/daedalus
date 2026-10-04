import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from daedalus.config import settings
from daedalus.routing import loops, penalties
from daedalus.server import api


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
  assert values["timeouts"] == {"request": 600.0, "wait": 60.0, "slow": 30.0}
  assert values["weights"]["fault"] == penalties.FAULT
  assert "think hard" in values["escalation"]["keywords"], "the default keywords"
  assert values["switch"]["keywords"] == ["clanker"]
  shipped_path = Path(__file__).parents[2] / "config" / "daedalus.yml"
  raw = yaml.safe_load(shipped_path.read_text(encoding="utf-8"))
  assert "escalation" not in raw and "switch" not in raw, (
    "the keyword lists stay defaults"
  )
  for group, entries in raw.items():
    for key, value in entries.items():
      assert value != settings.DEFAULTS[group][key], (
        f"{group}.{key} copies the code default"
      )
  shipped = settings.load(shipped_path)
  assert shipped.pop("request_hooks") == {"on-request": "hooks/openwebui_retry.py"}
  assert values.pop("request_hooks") == {"on-request": ""}
  assert shipped == values, "the shipped file holds no default copy"
  path = folder / "daedalus.yml"
  path.write_text("timeouts:\n  wait: 120\nweights:\n  fault: 0.25\n", encoding="utf-8")
  values = settings.load(path)
  assert values["timeouts"]["slow"] == 30.0, "slow keeps its own default"
  assert values["weights"]["fault"] == 0.25 and values["weights"]["success"] == 1.5
  path.write_text("", encoding="utf-8")
  assert settings.load(path)["session_affinity"]["enabled"] is True, "an empty file"
  assert settings.load(path)["session_affinity"]["change_on_draw"] is True
  expect_error(folder, "session_affinity:\n  change_on_draw: 1\n", "true or false")
  expect_error(folder, "server:\n  port: 1\n", "unknown group 'server'")
  expect_error(folder, "weights:\n  factor: 2\n", "unknown key weights.factor")
  expect_error(folder, "request_hooks:\n  on-request: 3\n", "must be a hook file path")
  expect_error(
    folder, "request_hooks:\n  on-later: a.py\n", "unknown key request_hooks.on-later"
  )
  hooks = settings.parse('request_hooks:\n  on-request: ""\n')["request_hooks"]
  assert hooks == {"on-request": ""}
  hooks = settings.parse("request_hooks:\n  on-request: hooks/x.py\n")["request_hooks"]
  assert hooks == {"on-request": "hooks/x.py"}
  text = settings.update_text(
    "request_hooks:\n  on-request: hooks/a.py # keep\n",
    {"request_hooks": {"on-request": "hooks/b.py"}},
  )
  assert text == "request_hooks:\n  on-request: hooks/b.py # keep\n", text
  expect_error(folder, "weights:\n  enabled: 1\n", "true or false")
  expect_error(folder, "timeouts:\n  wait: true\n", "above 0")
  expect_error(folder, "timeouts:\n  wait: 0\n", "above 0")
  expect_error(folder, "session_affinity:\n  stay: 1\n", "below 1")
  expect_error(folder, "- a\n", "groups of keys")
  (folder / "twice.yml").write_text("weights:\n  fault: 0.5\n  fault: 0.25\n")
  try:
    settings.load(folder / "twice.yml")
  except yaml.YAMLError as exc:
    assert "duplicate key 'fault'" in str(exc) and "line 3" in str(exc), exc
  else:
    raise AssertionError("no error for a duplicate key")
  expect_error(folder, "escalation:\n  keywords: think\n", "list of words or phrases")
  expect_error(folder, "escalation:\n  keywords: [1]\n", "list of words or phrases")
  expect_error(folder, "switch:\n  keywords: clanker\n", "list of words or phrases")
  expect_error(folder, "dashboard:\n  theme: blue\n", "system, light or dark")
  path.write_text("dashboard:\n  theme: dark\n", encoding="utf-8")
  assert settings.load(path)["dashboard"]["theme"] == "dark"
  assert settings.load(path)["dashboard"]["time_format"] == "24h", "24h by default"
  expect_error(folder, "dashboard:\n  time_format: 25h\n", "24h or 12h")
  path.write_text("dashboard:\n  time_format: 12h\n", encoding="utf-8")
  assert settings.load(path)["dashboard"]["time_format"] == "12h"
  expect_error(folder, "escalation:\n  keywords: [' ']\n", "list of words or phrases")
  path.write_text(
    "escalation:\n  keywords: [ultrathink, ' think hard ']\n", encoding="utf-8"
  )
  keywords = settings.load(path)["escalation"]["keywords"]
  assert keywords == ["ultrathink", "think hard"], "a config list replaces the default"


def test_loop_settings() -> None:
  assert settings.parse("")["loops"] == {
    "calls": 3,
    "repeats": 4,
    "shortest": 20,
    "longest": 2000,
  }
  assert settings.parse(
    "loops:\n  calls: 100\n  repeats: 16\n  shortest: 1000\n  longest: 10000\n"
  )["loops"] == {"calls": 100, "repeats": 16, "shortest": 1000, "longest": 10000}
  for text, message in (
    ("loops:\n  calls: true\n", "whole number"),
    ("loops:\n  calls: 1\n", "between 2 and 100"),
    ("loops:\n  repeats: 17\n", "between 2 and 16"),
    ("loops:\n  shortest: 1001\n", "between 1 and 1000"),
    ("loops:\n  longest: 10001\n", "between 1 and 10000"),
    ("loops:\n  shortest: 21\n  longest: 20\n", "at most loops.longest"),
  ):
    with pytest.raises(settings.SettingsError, match=message):
      settings.parse(text)


def test_parallel_settings(folder: Path) -> None:
  """The race defaults, and the 2 bounds of its numbers."""
  assert settings.parse("")["parallel"] == {
    "enabled": False,
    "count": 1,
    "chance": 0.05,
    "slow": 30.0,
    "penalty": 0.9,
  }
  expect_error(folder, "parallel:\n  count: 0\n", "between 1 and 10")
  expect_error(folder, "parallel:\n  count: 11\n", "between 1 and 10")
  expect_error(folder, "parallel:\n  count: 1.5\n", "whole number")
  assert settings.parse("parallel:\n  count: 3\n")["parallel"]["count"] == 3
  expect_error(folder, "parallel:\n  chance: 2\n", "from 0 to 1")
  expect_error(folder, "parallel:\n  chance: -0.1\n", "from 0 to 1")
  expect_error(folder, "parallel:\n  penalty: 2\n", "at most 1")
  expect_error(folder, "parallel:\n  penalty: 0\n", "above 0")
  expect_error(folder, "parallel:\n  slow: 0\n", "above 0")
  path = folder / "parallel.yml"
  path.write_text("parallel:\n  enabled: true\n  chance: 0\n", encoding="utf-8")
  values = settings.load(path)
  assert values["parallel"]["enabled"] is True and values["parallel"]["chance"] == 0.0
  api.apply_settings(values)
  try:
    assert api.PARALLEL_ENABLED is True and api.PENALTIES.race is True
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
  assert api.PARALLEL_ENABLED is False and api.PENALTIES.race is False


def test_apply(folder: Path) -> None:
  path = folder / "off.yml"
  path.write_text(
    "session_affinity:\n  enabled: false\n  change_on_draw: false\n"
    "weights:\n  enabled: false\n"
    "loops:\n  calls: 5\n  repeats: 6\n  shortest: 10\n  longest: 3000\n",
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
    "weights:\n  x: 1\n", encoding="utf-8"
  )
  wrong = subprocess.run(
    [*command, "serve"], cwd=folder, capture_output=True, text=True, check=False
  )
  assert wrong.returncode == 2 and "weights.x" in wrong.stderr, wrong.stderr
  (folder / "config" / "daedalus.yml").write_text("weights:\n  fault: 0.5\n")
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
  assert settings.check("timeouts", "request", 86400) == 86400.0
  assert settings.check("headroom", "timeout", 86400) == 86400.0
  for group, key, value in (
    ("timeouts", "request", 86401),
    ("timeouts", "wait", 1e20),
    ("headroom", "timeout", 86401),
  ):
    with pytest.raises(settings.SettingsError, match="at most 86400 seconds"):
      settings.check(group, key, value)
  with pytest.raises(settings.SettingsError, match="at most 86400 seconds"):
    settings.parse("timeouts:\n  request: 90000\n")


def test_pool_names() -> None:
  """The key of each pool is generic, its default is the built-in name, and a save keeps a number-like name."""
  assert settings.parse("")["pools"]["tier-a"] == "sophos"
  assert settings.parse("pools:\n  tier-a: fast\n")["pools"]["tier-a"] == "fast"
  assert settings.parse("pools:\n  images: pics\n")["pools"]["images"] == "pics"
  for bad in ("a/b", "auto", "''", "x" * 41, "[a]"):
    with pytest.raises(settings.SettingsError):
      settings.parse(f"pools:\n  tier-a: {bad}\n")
  with pytest.raises(settings.SettingsError, match="own name"):
    settings.parse("pools:\n  tier-a: koinos\n")
  swapped = settings.parse("pools:\n  tier-a: koinos\n  tier-c: sophos\n")["pools"]
  assert (swapped["tier-a"], swapped["tier-c"]) == ("koinos", "sophos"), swapped
  text = settings.update_text(
    "pools:\n  tier-a: sophos\n", {"pools": {"tier-a": "123"}}
  )
  assert settings.parse(text)["pools"]["tier-a"] == "123", text


def test_update_text_drops_an_empty_group() -> None:
  """The last key of a group takes the group with it."""
  text = settings.update_text(
    "timeouts:\n  slow: 30\ncatalog:\n  every: 6\n", {"timeouts": {"slow": None}}
  )
  assert text == "catalog:\n  every: 6\n", text
