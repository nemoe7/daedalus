import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from daedalus import catalog, cli, store
from daedalus.catalog import discovery
from daedalus.server import api
from daedalus.store import keys

os.environ["DAEDALUS_MASTER_KEY"] = "test-master-key-0001"

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


def check_help() -> None:
  shown = subprocess.run(
    [*DAEDALUS, "--help"], capture_output=True, text=True, timeout=10, check=False
  )
  assert shown.returncode == 0, shown.stderr
  assert shown.stdout.startswith("usage:"), shown.stdout
  for command in ("serve", "catalog", "dump"):
    assert f"\n    {command} " in shown.stdout, shown.stdout
  assert "--init" not in shown.stdout and "-c," not in shown.stdout, "no flags"
  wrong = subprocess.run(
    [*DAEDALUS, "--wrong"], capture_output=True, text=True, timeout=10, check=False
  )
  assert wrong.returncode == 2, wrong.stderr
  assert not hasattr(catalog, "main"), "python -m daedalus.catalog has no entry point"


def check_commands() -> None:
  calls: list[str] = []
  original = (
    store.MODELS_DB,
    catalog.refresh,
    discovery.dump,
    sys.modules.get("uvicorn"),
  )
  catalog.refresh = lambda: calls.append("catalog")
  discovery.dump = lambda: calls.append("dump")
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
      store.MODELS_DB.touch()
      calls.clear()
      cli.run(["serve"])
      assert calls == ["catalog", "listen:3357"], "a file without the model table"
      store.write_store([], store.MODELS_DB)
      calls.clear()
      cli.run(["serve"])
      assert calls == ["listen:3357"], calls
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
      check_key(calls)
  finally:
    store.MODELS_DB, catalog.refresh, discovery.dump, uvicorn = original
    api.CATALOG_REFRESH = None
    if uvicorn is None:
      sys.modules.pop("uvicorn", None)
    else:
      sys.modules["uvicorn"] = uvicorn


def check_master() -> None:
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


def check_key(calls: list[str]) -> None:
  wrong = subprocess.run(
    [*DAEDALUS, "key"], capture_output=True, text=True, check=False
  )
  assert wrong.returncode == 2, "the dashboard manages the API keys"
  store.MODELS_DB.unlink()
  keys.add(store.MODELS_DB, "ci")
  calls.clear()
  cli.run(["serve"])
  assert calls == ["catalog", "listen:3357"], "a key-only store is not a model store"


def main() -> None:
  check_help()
  check_master()
  check_commands()
  print("ok: cli subcommands")


if __name__ == "__main__":
  main()
