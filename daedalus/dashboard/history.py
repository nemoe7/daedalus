"""The API requests that the dashboard shows, kept in the model store after a restart."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

KEEP = 500
SHOWN = 50
TABLE = (
  "CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, row TEXT NOT NULL)"
)


class History:
  """The last requests, newest first, in the model store."""

  def __init__(self, path: Callable[[], Path], keep: int = KEEP) -> None:
    self.path, self.keep = path, keep

  def connect(self) -> sqlite3.Connection:
    target = Path(self.path())
    target.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(target, timeout=5)
    database.execute(TABLE)
    return database

  def add(self, row: dict[str, Any]) -> None:
    """Keep one request, and drop the rows older than the last `keep` rows."""
    database = self.connect()
    with database:
      cursor = database.execute(
        "INSERT INTO requests (row) VALUES (?)", (json.dumps(row, default=str),)
      )
      database.execute(
        "DELETE FROM requests WHERE id <= ?", (cursor.lastrowid - self.keep,)
      )
    database.close()

  def latest(self, limit: int = SHOWN) -> list[dict[str, Any]]:
    """The newest rows, at most `limit` and at most `keep`."""
    database = self.connect()
    try:
      rows = database.execute(
        "SELECT row FROM requests ORDER BY id DESC LIMIT ?",
        (max(0, min(limit, self.keep)),),
      ).fetchall()
    finally:
      database.close()
    return [json.loads(row) for (row,) in rows]

  def clear(self) -> None:
    database = self.connect()
    with database:
      database.execute("DELETE FROM requests")
    database.close()
