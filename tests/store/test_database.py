"""Tests of the state file connections."""

import sqlite3
from pathlib import Path

import pytest

from daedalus.routing import router
from daedalus.store import database

TABLE = ("CREATE TABLE IF NOT EXISTS t (a INTEGER)",)


def test_open(folder: Path) -> None:
  """WAL mode, a fast commit by default, and the setup again for a new file."""
  path = folder / "sub" / "state.sqlite3"
  first = database.open_db(path, TABLE)
  assert first.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
  assert first.execute("PRAGMA synchronous").fetchone()[0] == 1, "NORMAL"
  with first:
    first.execute("INSERT INTO t VALUES (1)")
  first.close()
  durable = database.open_db(path, TABLE, durable=True)
  assert durable.execute("PRAGMA synchronous").fetchone()[0] == 2, "FULL"
  assert durable.execute("SELECT a FROM t").fetchall() == [(1,)]
  durable.close()
  database._KEEPERS.pop(str(path)).close()
  for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm"):
    (path.parent / name).unlink(missing_ok=True)
  again = database.open_db(path, TABLE)
  assert again.execute("SELECT count(*) FROM t").fetchone() == (0,), "the table again"
  again.close()


def test_prompt_cache() -> None:
  """The same prompt runs the classifier 1 time."""
  runs: list[str] = []
  original = router.predict
  router.predict = lambda prompt, table, **rest: (
    runs.append(prompt) or original(prompt, table, **rest)
  )
  try:
    prompt = "Write a proof that the square root of 2 is not rational."
    tier = router.required_tier(prompt)
    assert router.required_tier(prompt) == tier and runs == [prompt], runs
    router.required_tier(prompt + " Now.")
    assert len(runs) == 2, "a new prompt runs again"
  finally:
    router.predict = original


@pytest.fixture
def folder(tmp_path: Path) -> Path:
  return tmp_path


@pytest.mark.parametrize(
  "name",
  [
    "plain.sqlite3",
    "hash#state.sqlite3",
    "query?state.sqlite3",
    "percent%20state.sqlite3",
  ],
)
@pytest.mark.parametrize("relative", [False, True])
def test_read_uri_path(tmp_path: Path, monkeypatch, name: str, relative: bool) -> None:
  monkeypatch.chdir(tmp_path)
  path = Path(name) if relative else tmp_path / name
  with sqlite3.connect(path) as source:
    source.execute(TABLE[0])
    source.execute("INSERT INTO t VALUES (7)")
  source.close()
  found = database.connect_read(path)
  assert found.execute("SELECT a FROM t").fetchall() == [(7,)]
  with pytest.raises(sqlite3.OperationalError):
    found.execute("INSERT INTO t VALUES (8)")
  found.close()
  assert database.connect_read(path) is found
  assert {item.name for item in tmp_path.iterdir()} == {name}


@pytest.mark.parametrize(
  "name",
  [
    "plain.sqlite3",
    "hash#state.sqlite3",
    "query?state.sqlite3",
    "percent%20state.sqlite3",
  ],
)
@pytest.mark.parametrize("relative", [False, True])
def test_read_missing_uri_path(
  tmp_path: Path, monkeypatch, name: str, relative: bool
) -> None:
  monkeypatch.chdir(tmp_path)
  path = Path(name) if relative else tmp_path / name
  with pytest.raises(sqlite3.OperationalError):
    database.connect_read(path)
  assert not list(tmp_path.iterdir())
