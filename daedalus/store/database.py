"""SQLite connections for the state files, with the setup done once for each file."""

import os
import sqlite3
import threading
from pathlib import Path

# The (file, tables) pairs with the setup done: the folder, WAL mode and the tables.
_READY: set[tuple[str, tuple[str, ...]]] = set()
# 1 idle connection for each file. The close of the last connection writes the WAL
# back to the file, so this connection keeps that work out of each request.
_KEEPERS: dict[str, sqlite3.Connection] = {}


class Kept(sqlite3.Connection):
  """A connection that stays open: its close is a no-op."""

  def close(self) -> None:
    pass


# 1 open connection for each thread, file, schema and durability. A request or
# a poll reuses its thread's connection instead of opening a new one.
_OPEN: dict[tuple[int, str, tuple[str, ...], bool], sqlite3.Connection] = {}
# The same for the read-only connections, with the inode they opened.
_READ: dict[tuple[int, str], tuple[sqlite3.Connection, int]] = {}


def prune(path: str) -> None:
  """Drop the cached connections of a file that is gone."""
  for key, held in list(_OPEN.items()):
    if key[1] == path:
      del _OPEN[key]
      sqlite3.Connection.close(held)
  for key, held in list(_READ.items()):
    if key[1] == path:
      del _READ[key]
      sqlite3.Connection.close(held[0])


def connect_read(path: Path | str) -> sqlite3.Connection:
  """The thread's own read-only connection to one state file."""
  target = str(path)
  try:
    inode = os.stat(target).st_ino
  except FileNotFoundError:
    return sqlite3.connect(f"file:{target}?mode=ro", uri=True)
  held = _READ.get((threading.get_ident(), target))
  if held is not None:
    if held[1] == inode:
      return held[0]
    sqlite3.Connection.close(held[0])
  found = sqlite3.connect(f"file:{target}?mode=ro", uri=True, factory=Kept)
  _READ[(threading.get_ident(), target)] = (found, inode)
  return found


def open_db(
  path: Path | str, tables: tuple[str, ...] = (), durable: bool = False
) -> sqlite3.Connection:
  """The thread's own connection to one state file. Without `durable`, a commit does not wait for the disk."""
  target = Path(path)
  held = _OPEN.get((threading.get_ident(), str(target), tables, durable))
  if held is not None:
    if target.exists():
      return held
    prune(str(target))
  key = (str(target), tables)
  if key in _READY and not target.exists():
    _READY.discard(key)
  first = key not in _READY
  if first:
    target.parent.mkdir(parents=True, exist_ok=True)
  database = sqlite3.connect(target, timeout=5, factory=Kept)
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
  _OPEN[(threading.get_ident(), str(target), tables, durable)] = database
  return database
