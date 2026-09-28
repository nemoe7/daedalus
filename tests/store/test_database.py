"""Tests of the state file connections."""

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
  router.predict = lambda prompt, table: runs.append(prompt) or original(prompt, table)
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
