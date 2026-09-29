import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from daedalus.config import settings
from daedalus.routing import penalties
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
  shipped = settings.load(Path(__file__).parents[2] / "config" / "daedalus.yml")
  keywords = shipped.pop("escalation")["keywords"]
  assert "think hard" in keywords and values.pop("escalation") == {"keywords": []}
  assert shipped.pop("switch") == {"keywords": ["clanker"]}
  assert values.pop("switch") == {"keywords": []}
  assert shipped == values, "the shipped file holds the defaults"
  path = folder / "daedalus.yml"
  path.write_text("timeouts:\n  wait: 120\nweights:\n  fault: 0.25\n", encoding="utf-8")
  values = settings.load(path)
  assert values["timeouts"]["slow"] == 60.0, "slow is half of wait when missing"
  assert values["weights"]["fault"] == 0.25 and values["weights"]["success"] == 1.5
  path.write_text("", encoding="utf-8")
  assert settings.load(path)["session_affinity"]["enabled"] is True, "an empty file"
  expect_error(folder, "server:\n  port: 1\n", "unknown group 'server'")
  expect_error(folder, "weights:\n  factor: 2\n", "unknown key weights.factor")
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
  expect_error(folder, "escalation:\n  keywords: [' ']\n", "list of words or phrases")
  path.write_text(
    "escalation:\n  keywords: [ultrathink, ' think hard ']\n", encoding="utf-8"
  )
  keywords = settings.load(path)["escalation"]["keywords"]
  assert keywords == ["ultrathink", "think hard"], keywords


def test_apply(folder: Path) -> None:
  path = folder / "off.yml"
  path.write_text(
    "session_affinity:\n  enabled: false\nweights:\n  enabled: false\n",
    encoding="utf-8",
  )
  store = api.PENALTIES
  original = store.path
  store.path = lambda: folder / "models.sqlite3"
  try:
    api.apply_settings(settings.load(path))
    assert api.Tracker("key", "daedalus/deinos").slot is None, "no pins"
    store.pick = lambda: 0.99
    assert store.record("a", store.fault) == 1.0, "no weights"
    assert store.order([["a", "b", "c"], ["d"]]) == ["a", "b", "c", "d"], "usual order"
  finally:
    api.apply_settings(settings.load(folder / "missing.yml"))
    store.path = original
  assert api.AFFINITY is True and store.enabled is True and api.SLOW_SECONDS == 30.0


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


def test_pool_names() -> None:
  """Each pool name is a short name of its own, and a save keeps a name that looks like a number."""
  assert settings.parse("")["pools"]["moros"] == "moros"
  assert settings.parse("pools:\n  moros: fast\n")["pools"]["moros"] == "fast"
  for bad in ("a/b", "auto", "''", "x" * 41, "[a]"):
    with pytest.raises(settings.SettingsError):
      settings.parse(f"pools:\n  moros: {bad}\n")
  with pytest.raises(settings.SettingsError, match="own name"):
    settings.parse("pools:\n  moros: koinos\n")
  swapped = settings.parse("pools:\n  moros: koinos\n  koinos: moros\n")["pools"]
  assert (swapped["moros"], swapped["koinos"]) == ("koinos", "moros"), swapped
  text = settings.update_text("pools:\n  moros: moros\n", {"pools": {"moros": "123"}})
  assert settings.parse(text)["pools"]["moros"] == "123", text
