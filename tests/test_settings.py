import os
import subprocess
import sys
import tempfile
from pathlib import Path

from daedalus.config import settings
from daedalus.routing import penalties
from daedalus.server import api

os.environ["DAEDALUS_MASTER_KEY"] = "test-master-key-0001"


def expect_error(folder: Path, text: str, message: str) -> None:
  path = folder / "bad.yml"
  path.write_text(text, encoding="utf-8")
  try:
    settings.load(path)
  except settings.SettingsError as exc:
    assert message in str(exc), exc
  else:
    raise AssertionError(f"no error for {text!r}")


def check_load(folder: Path) -> None:
  values = settings.load(folder / "missing.yml")
  assert values["timeouts"] == {"request": 600.0, "wait": 60.0, "slow": 30.0}
  assert values["weights"]["fault"] == penalties.FAULT
  shipped = settings.load(Path(__file__).parent.parent / "config" / "daedalus.yml")
  keywords = shipped.pop("escalation")["keywords"]
  assert "think hard" in keywords and values.pop("escalation") == {"keywords": []}
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
  expect_error(folder, "escalation:\n  keywords: think\n", "list of words or phrases")
  expect_error(folder, "escalation:\n  keywords: [1]\n", "list of words or phrases")
  expect_error(folder, "escalation:\n  keywords: [' ']\n", "list of words or phrases")
  path.write_text(
    "escalation:\n  keywords: [ultrathink, ' think hard ']\n", encoding="utf-8"
  )
  keywords = settings.load(path)["escalation"]["keywords"]
  assert keywords == ["ultrathink", "think hard"], keywords


def check_apply(folder: Path) -> None:
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


def check_cli(folder: Path) -> None:
  command = [sys.executable, "-c", "from daedalus.cli import run; run()"]
  (folder / "config").mkdir()
  (folder / "config" / "daedalus.yml").write_text(
    "weights:\n  x: 1\n", encoding="utf-8"
  )
  wrong = subprocess.run(
    [*command, "serve"], cwd=folder, capture_output=True, text=True, check=False
  )
  assert wrong.returncode == 2 and "weights.x" in wrong.stderr, wrong.stderr
  environment = {**os.environ, "DAEDALUS_PORT": "9100"}
  shown = subprocess.run(
    [*command, "serve", "--help"],
    env=environment,
    capture_output=True,
    text=True,
    check=True,
  )
  assert "default 9100" in shown.stdout, shown.stdout


def main() -> None:
  with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    check_load(folder)
    check_apply(folder)
    check_cli(folder)
  print("ok: settings")


if __name__ == "__main__":
  main()
