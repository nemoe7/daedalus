"""Gemini thought signatures by tool call id and model, kept in the model store."""

import sqlite3
import time

from daedalus import store
from daedalus.store.database import open_db

TABLE = "signatures"
# A signature with no use for this long expires. The session idle setting replaces it.
IDLE_SECONDS = 3600.0


SCHEMA = (
  f"CREATE TABLE IF NOT EXISTS {TABLE} (call TEXT NOT NULL, model TEXT NOT NULL,"
  " signature TEXT NOT NULL, used REAL NOT NULL, PRIMARY KEY (call, model))"
)


def connect() -> sqlite3.Connection:
  return open_db(store.MODELS_DB, (SCHEMA,))


def save(call: str, model: str, signature: str) -> None:
  """Store one signature, and remove the expired ones."""
  now = time.time()
  database = connect()
  with database:
    database.execute(f"DELETE FROM {TABLE} WHERE used < ?", (now - IDLE_SECONDS,))
    database.execute(
      f"INSERT OR REPLACE INTO {TABLE} VALUES (?, ?, ?, ?)",
      (call, model, signature, now),
    )
  database.close()


def find(call: str, model: str) -> str | None:
  """The live signature that this model gave for this call, or None."""
  now = time.time()
  database = connect()
  with database:
    row = database.execute(
      f"SELECT signature FROM {TABLE} WHERE call = ? AND model = ? AND used >= ?",
      (call, model, now - IDLE_SECONDS),
    ).fetchone()
    if row:
      database.execute(
        f"UPDATE {TABLE} SET used = ? WHERE call = ? AND model = ?", (now, call, model)
      )
  database.close()
  return row[0] if row else None
