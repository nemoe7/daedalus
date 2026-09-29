"""Values for `os.environ/NAME` that the dashboard saves in the state file."""

import sqlite3
import time
from pathlib import Path

from daedalus.store.database import open_db

TABLE = "saved_env"
SCHEMA = (
  f"CREATE TABLE IF NOT EXISTS {TABLE} (name TEXT PRIMARY KEY, value TEXT NOT NULL,"
  " updated REAL NOT NULL)"
)


def read(path: Path | str) -> dict[str, str]:
  """All saved values by name, without a write, and empty when the file or table is not there."""
  target = Path(path)
  if not target.exists():
    return {}
  database = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
  try:
    return dict(database.execute(f"SELECT name, value FROM {TABLE}").fetchall())
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()


def save(path: Path | str, name: str, value: str) -> None:
  """Keep 1 value, and wait for the disk."""
  database = open_db(path, (SCHEMA,), durable=True)
  try:
    with database:
      database.execute(
        f"INSERT INTO {TABLE} (name, value, updated) VALUES (?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated = excluded.updated",
        (name, value, time.time()),
      )
  finally:
    database.close()


def clear(path: Path | str, name: str) -> bool:
  """Remove 1 value, and tell if there was one."""
  database = open_db(path, (SCHEMA,), durable=True)
  try:
    with database:
      gone = database.execute(f"DELETE FROM {TABLE} WHERE name = ?", (name,)).rowcount
  finally:
    database.close()
  return gone > 0
