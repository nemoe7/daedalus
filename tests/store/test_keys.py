import sqlite3
import time
from pathlib import Path

import pytest

from daedalus import store
from daedalus.store import keys


def test_keys(database: Path) -> None:
  assert keys.listing(database) == [] and keys.find(database, "x") is None, "no store"
  first = keys.add(database, "  laptop ")
  assert first.startswith("sk-") and len(first) == 46, first
  assert first not in database.read_bytes().decode("latin-1"), "no plain key"
  second = keys.add(database, "ci")
  shown = keys.listing(database)
  assert [row["name"] for row in shown] == ["laptop", "ci"], shown
  assert shown[0]["start"] == first[:7] and shown[0]["used"] is None, shown
  assert "hash" not in shown[0], "the list has no hash"
  assert keys.find(database, first) == "laptop"
  assert keys.find(database, second) == "ci"
  assert keys.find(database, first + "x") is None and keys.find(database, "") is None
  used = keys.listing(database)[0]["used"]
  assert used is not None and used <= time.time(), "the last use"
  keys.find(database, first)
  assert keys.listing(database)[0]["used"] == used, "at most 1 write in a minute"
  for name in ("laptop", "", "   ", "x" * 41, None, 3):
    try:
      keys.add(database, name)
    except keys.KeyNameError:
      continue
    raise AssertionError(f"a wrong or used name: {name!r}")
  assert keys.add(database, "x" * 40), "40 characters"
  store.write_store([], database)
  assert keys.find(database, second) == "ci", "a rebuild keeps the keys"
  assert keys.delete(database, "ci") is True and keys.find(database, second) is None
  assert keys.delete(database, "ci") is False, "no key with that name"


def test_migrate(database: Path) -> None:
  with sqlite3.connect(database) as old:
    old.execute("CREATE TABLE api_key (hash TEXT NOT NULL)")
    old.execute("INSERT INTO api_key VALUES (?)", (keys.digest("old-local-key-0001"),))
    old.execute("CREATE TABLE models (id TEXT PRIMARY KEY)")
  old.close()
  store.write_store([], database)
  assert keys.find(database, "old-local-key-0001") == "default", "the old key stays"
  assert keys.listing(database)[0]["start"] == "", "the old key start is not known"
  with sqlite3.connect(database) as new:
    version = new.execute("PRAGMA user_version").fetchone()[0]
    tables = {row[0] for row in new.execute("SELECT name FROM sqlite_master")}
  new.close()
  assert version == store.SCHEMA_VERSION and "api_key" not in tables, tables
  store.migrate(database)
  assert [row["name"] for row in keys.listing(database)] == ["default"], "a second run"


@pytest.fixture
def database(tmp_path: Path) -> Path:
  return tmp_path / "models.sqlite3"
