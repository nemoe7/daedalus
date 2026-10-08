import contextlib
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from daedalus import catalog, cli, config, store
from daedalus.catalog import discovery
from daedalus.server import api
from daedalus.store import keys

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
DAEDALUS = [sys.executable, "-c", CLI]


def test_help() -> None:
  shown = subprocess.run(
    [*DAEDALUS, "--help"], capture_output=True, text=True, timeout=10, check=False
  )
  assert shown.returncode == 0, shown.stderr
  assert shown.stdout.startswith("usage:"), shown.stdout
  for command in ("serve", "catalog", "dump"):
    assert f"\n    {command} " in shown.stdout, shown.stdout
  dump_help = subprocess.run(
    [*DAEDALUS, "dump", "--help"],
    capture_output=True,
    text=True,
    timeout=10,
    check=False,
  )
  assert dump_help.returncode == 0, dump_help.stderr
  for option in ("catalog", "models", "all", "--format", "--fmt", "-f", "csv"):
    assert option in dump_help.stdout, dump_help.stdout
  assert "--init" not in shown.stdout and "-c," not in shown.stdout, "no flags"
  wrong = subprocess.run(
    [*DAEDALUS, "--wrong"], capture_output=True, text=True, timeout=10, check=False
  )
  assert wrong.returncode == 2, wrong.stderr
  assert not hasattr(catalog, "main"), "python -m daedalus.catalog has no entry point"


def test_version() -> None:
  """--version shows DAEDALUS_VERSION, or dev without it."""
  for value, shown in (("v1.2.3", "daedalus v1.2.3"), ("", "daedalus dev")):
    env = {**os.environ, "DAEDALUS_VERSION": value}
    found = subprocess.run(
      [*DAEDALUS, "--version"],
      capture_output=True,
      text=True,
      timeout=10,
      check=False,
      env=env,
    )
    assert (found.returncode, found.stdout.strip()) == (0, shown), found.stderr


def test_commands() -> None:
  calls: list[str] = []
  original = (
    store.MODELS_DB,
    catalog.refresh,
    discovery.dump,
    api.CATALOG_REFRESH,
    api.CATALOG_REBUILD_CACHED,
    cli.live_url,
    sys.modules.get("uvicorn"),
  )
  catalog.refresh = lambda: calls.append("catalog")
  discovery.dump = lambda **_: calls.append("dump")
  # No live server changes the path of a test, whatever runs on the machine.
  cli.live_url = lambda: None
  sys.modules["uvicorn"] = SimpleNamespace(
    run=lambda *_, port, **__: calls.append(f"listen:{port}")
  )
  try:
    with tempfile.TemporaryDirectory() as folder:
      store.MODELS_DB = Path(folder) / "models.sqlite3"
      shown = io.StringIO()
      with contextlib.redirect_stdout(shown):
        cli.run([])
      assert calls == [] and shown.getvalue().startswith("usage:"), calls
      cli.run(["dump"])
      assert calls == ["dump"], "a dump does not build the store"
      calls.clear()
      cli.run(["serve"])
      assert calls == ["catalog", "listen:3357"], "serve builds a missing store"
      assert api.CATALOG_REFRESH is catalog.refresh, "serve starts the rebuild schedule"
      assert api.CATALOG_REBUILD_CACHED is catalog.rebuild_cached
      store.MODELS_DB.touch()
      calls.clear()
      cli.run(["serve"])
      assert calls == ["catalog", "listen:3357"], "a file without the model table"
      layout = sqlite3.connect(store.MODELS_DB)
      tables = {row[0] for row in layout.execute("SELECT name FROM sqlite_master")}
      layout.close()
      assert {"alembic_version", "models"} <= tables, "serve runs the Alembic steps"
      store.MODELS_DB.unlink()
      cli.run(["catalog"])
      assert store.MODELS_DB.exists(), "catalog runs the Alembic steps"
      calls.clear()
      cli.run(["serve"])
      assert calls == ["catalog", "listen:3357"], "empty tables are not a built store"
      store.write_store([], store.MODELS_DB)
      calls.clear()
      cli.run(["serve"])
      assert calls == ["listen:3357"], calls
      store.MODELS_DB.write_text("not a database")
      calls.clear()
      cli.run(["serve"])
      assert calls == ["catalog", "listen:3357"], "an unreadable store is rebuilt"
      assert store.MODELS_DB.with_name("models.sqlite3.broken").exists(), (
        "the bad file went aside"
      )
      # A built store again, for the checks below.
      store.write_store([], store.MODELS_DB)
      calls.clear()
      cli.run(["catalog"])
      assert calls == ["catalog"], calls
      calls.clear()
      cli.run(["serve", "--catalog"])
      assert calls == ["catalog", "listen:3357"], calls
      calls.clear()
      cli.run(["serve", "--catalog", "9000"])
      assert calls == ["catalog", "listen:9000"], calls
      calls.clear()
      cli.run(["serve", "9000"])
      assert calls == ["listen:9000"], calls
      assert_key_command(calls)
  finally:
    (
      store.MODELS_DB,
      catalog.refresh,
      discovery.dump,
      api.CATALOG_REFRESH,
      api.CATALOG_REBUILD_CACHED,
      cli.live_url,
      uvicorn,
    ) = original
    if uvicorn is None:
      sys.modules.pop("uvicorn", None)
    else:
      sys.modules["uvicorn"] = uvicorn


def test_dump_modes(tmp_path: Path, monkeypatch) -> None:
  calls: list[tuple[str, str] | str] = []
  rows = [{"id": "p/a", "mode": "chat", "flags": ["vision"]}]
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path)
  monkeypatch.setattr(
    discovery, "dump", lambda *, file_format: calls.append(("catalog", file_format))
  )
  monkeypatch.setattr(store, "migrate", lambda: calls.append("migrate"))
  monkeypatch.setattr(catalog, "refresh", lambda: calls.append("refresh"))
  monkeypatch.setattr(store, "stored_rows", lambda: rows)
  # The tier column has its own test: this 1 reads the format switch alone.
  monkeypatch.setattr(config, "get_config", dict)

  cli.run(["dump", "catalog", "--fmt", "csv"])
  assert calls == ["migrate", ("catalog", "csv")], calls
  cli.run(["dump", "models", "--format", "json"])
  assert calls == ["migrate", ("catalog", "csv"), "migrate"], calls
  assert json.loads((tmp_path / "models.json").read_text("utf-8")) == [
    {**rows[0], "tier": None, "supported_efforts": None}
  ]
  cli.run(["dump", "all", "-f", "csv"])
  assert calls == [
    "migrate",
    ("catalog", "csv"),
    "migrate",
    "migrate",
    ("catalog", "csv"),
  ], calls
  assert not (tmp_path / "models.json").exists(), "the old format is removed"
  with (tmp_path / "models.csv").open(encoding="utf-8", newline="") as source:
    assert list(csv.DictReader(source)) == [
      {"id": "p/a", "flags": '["vision"]', "mode": "chat", "tier": "", "supported_efforts": ""}
    ]


def test_dump_models_names_the_tier_of_each_row(tmp_path: Path, monkeypatch) -> None:
  """The dump carries the tier that claims a row, so no reader matches slugs to patterns by hand."""
  monkeypatch.setattr(
    config,
    "get_config",
    lambda: {
      "p": {
        "api_key": "k",
        "api_base": "https://p.test/v1",
        "tier": {"TIER-A": ["one*"], "TIER-B": ["*"]},
      }
    },
  )
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path / "dump")
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  store.write_store([{"id": "p/one"}, {"id": "q/two"}])
  cli.dump_models("json")
  rows = json.loads((tmp_path / "dump" / "models.json").read_text("utf-8"))
  assert {row["id"]: row["tier"] for row in rows} == {
    "p/one": "TIER-A",
    "q/two": None,
  }, rows
  cli.dump_models("csv")
  with (tmp_path / "dump" / "models.csv").open(encoding="utf-8", newline="") as source:
    found = {row["id"]: row["tier"] for row in csv.DictReader(source)}
  assert found == {"p/one": "TIER-A", "q/two": ""}, found


def test_dump_models_carries_the_evaluated_efforts(tmp_path: Path, monkeypatch) -> None:
  """The dump carries the efforts a row names, evaluated to a list, and null when it names none."""
  monkeypatch.setattr(config, "get_config", dict)
  monkeypatch.setattr(discovery, "DUMP_DIR", tmp_path / "dump")
  monkeypatch.setattr(store, "MODELS_DB", tmp_path / "models.sqlite3")
  store.write_store(
    [
      {
        "id": "p/one",
        "supported_efforts": json.dumps(["max", "high", "low"]),
      },
      {"id": "q/two"},
      {"id": "r/three", "supported_efforts": json.dumps(["high", 7, "", "low"])},
    ]
  )
  cli.dump_models("json")
  rows = json.loads((tmp_path / "dump" / "models.json").read_text("utf-8"))
  assert {row["id"]: row["supported_efforts"] for row in rows} == {
    "p/one": ["max", "high", "low"],
    "q/two": None,
    "r/three": ["high", "low"],
  }, rows
  cli.dump_models("csv")
  with (tmp_path / "dump" / "models.csv").open(encoding="utf-8", newline="") as source:
    found = {row["id"]: row["supported_efforts"] for row in csv.DictReader(source)}
  assert found == {
    "p/one": '["max","high","low"]',
    "q/two": "",
    "r/three": '["high","low"]',
  }, found


def test_master() -> None:
  env = {
    key: value for key, value in os.environ.items() if key != "DAEDALUS_MASTER_KEY"
  }
  for value in (None, "short"):
    if value is not None:
      env["DAEDALUS_MASTER_KEY"] = value
    stopped = subprocess.run(
      [*DAEDALUS, "serve"],
      capture_output=True,
      text=True,
      check=False,
      env=env,
      timeout=30,
    )
    assert stopped.returncode == 2, stopped.stderr
    assert "set DAEDALUS_MASTER_KEY" in stopped.stderr, stopped.stderr


def assert_key_command(calls: list[str]) -> None:
  wrong = subprocess.run(
    [*DAEDALUS, "key"], capture_output=True, text=True, check=False
  )
  assert wrong.returncode == 2, "the dashboard manages the API keys"
  store.MODELS_DB.unlink()
  keys.add(store.MODELS_DB, "ci")
  calls.clear()
  cli.run(["serve"])
  assert calls == ["catalog", "listen:3357"], "a key-only store is not a model store"
