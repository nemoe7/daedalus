"""SQLite connections for the state files, with the setup done once for each file."""

import sqlite3
from pathlib import Path

# The (file, tables) pairs with the setup done: the folder, WAL mode and the tables.
_READY: set[tuple[str, tuple[str, ...]]] = set()
# 1 idle connection for each file. The close of the last connection writes the WAL
# back to the file, so this connection keeps that work out of each request.
_KEEPERS: dict[str, sqlite3.Connection] = {}


def open_db(
  path: Path | str, tables: tuple[str, ...] = (), durable: bool = False
) -> sqlite3.Connection:
  """A connection to one state file. Without `durable`, a commit does not wait for the disk."""
  target = Path(path)
  key = (str(target), tables)
  if key in _READY and not target.exists():
    _READY.discard(key)
  first = key not in _READY
  if first:
    target.parent.mkdir(parents=True, exist_ok=True)
  database = sqlite3.connect(target, timeout=5)
  if first:
    try:
      # WAL lets readers and 1 writer work at the same time, with less disk work for each commit.
      database.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
      pass
    for statement in tables:
      database.execute(statement)
    database.commit()
    _READY.add(key)
    if key[0] not in _KEEPERS:
      keeper = sqlite3.connect(target, check_same_thread=False)
      # A read joins the WAL index. Only then does the keeper count as an open connection.
      keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
      _KEEPERS[key[0]] = keeper
  if not durable:
    database.execute("PRAGMA synchronous=NORMAL")
  return database
