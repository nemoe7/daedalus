import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from daedalus import catalog, cli, keys, store

DAEDALUS = [sys.executable, "-c", "from daedalus.cli import run; run()"]


def check_help() -> None:
  shown = subprocess.run(
    [*DAEDALUS, "--help"], capture_output=True, text=True, timeout=10, check=False
  )
  assert shown.returncode == 0, shown.stderr
  assert shown.stdout.startswith("usage:"), shown.stdout
  for command in ("serve", "catalog", "dump", "key"):
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
    catalog.dump,
    sys.modules.get("uvicorn"),
  )
  catalog.refresh = lambda: calls.append("catalog")
  catalog.dump = lambda: calls.append("dump")
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
    store.MODELS_DB, catalog.refresh, catalog.dump, uvicorn = original
    if uvicorn is None:
      sys.modules.pop("uvicorn", None)
    else:
      sys.modules["uvicorn"] = uvicorn


def check_key(calls: list[str]) -> None:
  database = store.MODELS_DB
  shown = io.StringIO()
  with contextlib.redirect_stdout(shown):
    cli.run(["key"])
  key = shown.getvalue().strip()
  assert key.startswith("sk-") and len(key) == 46, key
  assert keys.matches(database, key) is True, "the store keeps the hash"
  assert key not in database.read_bytes().decode("latin-1"), "no plain key"
  store.write_store([], database)
  assert keys.matches(database, key) is True, "a rebuild keeps the key"
  calls.clear()
  cli.run(["key", "my-custom-key-0001"])
  assert calls == [], "key does not start the router"
  assert keys.matches(database, "my-custom-key-0001") is True
  assert keys.matches(database, key) is False, "a new key replaces the old key"
  cli.run(["key", "off"])
  assert keys.matches(database, key) is None, "off removes the key"
  for bad in ("short-key", "has a space in it!"):
    wrong = subprocess.run(
      [*DAEDALUS, "key", bad], capture_output=True, text=True, check=False
    )
    assert wrong.returncode == 2 and "16 or more" in wrong.stderr, wrong.stderr
  database.unlink()
  cli.run(["key"])
  calls.clear()
  cli.run(["serve"])
  assert calls == ["catalog", "listen:3357"], "a key-only store is not a model store"


def main() -> None:
  check_help()
  check_commands()
  print("ok: cli subcommands")


if __name__ == "__main__":
  main()
