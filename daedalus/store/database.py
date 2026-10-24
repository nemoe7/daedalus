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
_INODES: dict[str, int] = {}


class Kept(sqlite3.Connection):
  """A connection that stays open: its close is a no-op."""

  def close(self) -> None:
    pass


# 1 open connection for each thread, file, schema, and durability. A request or
# a poll reuses its thread's connection instead of opening a new one.
_OPEN: dict[tuple[int, str, tuple[str, ...], bool], sqlite3.Connection] = {}
# The same for the read-only connections, with the inode they opened.
_READ: dict[tuple[int, str], tuple[sqlite3.Connection, int]] = {}


def _stat_inode(target: str | Path) -> int | None:
  """Return the file inode, or None when the file is missing."""
  try:
    return os.stat(target).st_ino
  except OSError:
    return None


def _alive(connection: sqlite3.Connection) -> bool:
  """Tell whether a cached connection can still read its SQLite schema version."""
  try:
    connection.execute("PRAGMA schema_version").fetchall()
  except sqlite3.Error:
    return False
  return True


def _close_quietly(connection: sqlite3.Connection) -> None:
  """Close a SQLite connection and ignore close-time SQLite errors."""
  try:
    sqlite3.Connection.close(connection)
  except sqlite3.Error:
    pass


def prune(path: str) -> None:
  """Drop the cached connections and setup state of a file that is gone or stale."""
  for key, held in list(_OPEN.items()):
    if key[1] == path:
      del _OPEN[key]
      _close_quietly(held)
  for key, held in list(_READ.items()):
    if key[1] == path:
      del _READ[key]
      _close_quietly(held[0])
  keeper = _KEEPERS.pop(path, None)
  if keeper is not None:
    _close_quietly(keeper)
  _INODES.pop(path, None)
  for ready in list(_READY):
    if ready[0] == path:
      _READY.discard(ready)


def _reset_broken(target: Path) -> None:
  """Close cached connections and move a broken state file and its WAL sidecars aside."""
  prune(str(target))
  if target.exists():
    broken = target.with_name(target.name + ".broken")
    broken.unlink(missing_ok=True)
    target.replace(broken)
  for suffix in ("-wal", "-shm"):
    Path(f"{target}{suffix}").unlink(missing_ok=True)


def connect_read(path: Path | str) -> sqlite3.Connection:
  """The thread's own read-only connection to one state file."""
  target = str(path)
  try:
    inode = os.stat(target).st_ino
  except FileNotFoundError:
    return sqlite3.connect(f"{Path(target).resolve().as_uri()}?mode=ro", uri=True)
  held = _READ.get((threading.get_ident(), target))
  if held is not None:
    if held[1] == inode and _alive(held[0]):
      return held[0]
    _close_quietly(held[0])
  found = sqlite3.connect(
    f"{Path(target).resolve().as_uri()}?mode=ro",
    uri=True,
    factory=Kept,
    check_same_thread=False,
  )
  _READ[(threading.get_ident(), target)] = (found, inode)
  return found


def _connect_db(
  target: Path, tables: tuple[str, ...], durable: bool
) -> sqlite3.Connection:
  """Open one state connection, initialize its schema on first open, and cache it."""
  key = (str(target), tables)
  if key in _READY and not target.exists():
    _READY.discard(key)
  first = key not in _READY
  if first:
    target.parent.mkdir(parents=True, exist_ok=True)
  database = sqlite3.connect(target, timeout=5, factory=Kept, check_same_thread=False)
  try:
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
        try:
          # A read joins the WAL index. Only then does the keeper count as an open connection.
          keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
        except BaseException:
          keeper.close()
          raise
        _KEEPERS[key[0]] = keeper
    if not durable:
      database.execute("PRAGMA synchronous=NORMAL")
  except BaseException:
    sqlite3.Connection.close(database)
    raise
  inode = _stat_inode(target)
  if inode is not None:
    _INODES[str(target)] = inode
  _OPEN[(threading.get_ident(), str(target), tables, durable)] = database
  return database


def open_db(
  path: Path | str, tables: tuple[str, ...] = (), durable: bool = False
) -> sqlite3.Connection:
  """The thread's own connection to one state file. Without `durable`, a commit does not wait for the disk."""
  target = Path(path)
  inode = _stat_inode(target)
  held = _OPEN.get((threading.get_ident(), str(target), tables, durable))
  if held is not None:
    if inode is not None and _INODES.get(str(target)) == inode and _alive(held):
      return held
    prune(str(target))
  elif inode is None or (str(target) in _INODES and _INODES[str(target)] != inode):
    prune(str(target))
  try:
    return _connect_db(target, tables, durable)
  except sqlite3.DatabaseError:
    _reset_broken(target)
    return _connect_db(target, tables, durable)
