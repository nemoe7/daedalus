"""Named API keys for `/v1`, kept as SHA-256 hashes in the model store."""

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

TABLE = "api_keys"
SCHEMA = (
  f"CREATE TABLE IF NOT EXISTS {TABLE} (name TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE, "
  "start TEXT NOT NULL, created REAL NOT NULL, used REAL)"
)
MIN_LENGTH = 16
NAME_LENGTH = 40
START_LENGTH = 7
# The last use time changes at most once in this time, so requests do not write each time.
USE_SECONDS = 60.0


class KeyNameError(ValueError):
  """A wrong key name, or a name that is in use."""


def generate() -> str:
  """A new key: `sk-` and 43 URL-safe characters."""
  return "sk-" + secrets.token_urlsafe(32)


def valid(key: str) -> bool:
  """Accept a key of 16 or more characters, with no spaces."""
  return len(key) >= MIN_LENGTH and not any(char.isspace() for char in key)


def bearer(header: str) -> str:
  """The token of an `Authorization: Bearer` header, or an empty string."""
  return header[7:].strip() if header.lower().startswith("bearer ") else ""


def digest(key: str) -> str:
  return hashlib.sha256(key.encode()).hexdigest()


def check_name(name: object) -> str:
  """The name without outer spaces, when it has 1 to 40 characters."""
  if not isinstance(name, str) or not 0 < len(name.strip()) <= NAME_LENGTH:
    raise KeyNameError(f"A key name has 1 to {NAME_LENGTH} characters.")
  return name.strip()


def listing(path: Path | str) -> list[dict[str, Any]]:
  """The keys, oldest first, without their hashes."""
  if not Path(path).exists():
    return []
  database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
  try:
    rows = database.execute(
      f"SELECT name, start, created, used FROM {TABLE} ORDER BY created"
    ).fetchall()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  return [
    dict(zip(("name", "start", "created", "used"), row, strict=True)) for row in rows
  ]


def add(path: Path | str, name: object, key: str | None = None) -> str:
  """Keep a new named key and return it. Without `key`, daedalus makes one."""
  name, key = check_name(name), key or generate()
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  database = sqlite3.connect(path)
  try:
    with database:
      database.execute(SCHEMA)
      database.execute(
        f"INSERT INTO {TABLE} (name, hash, start, created) VALUES (?, ?, ?, ?)",
        (name, digest(key), key[:START_LENGTH], time.time()),
      )
  except sqlite3.IntegrityError as exc:
    raise KeyNameError(f"The name {name!r} is in use.") from exc
  finally:
    database.close()
  return key


def delete(path: Path | str, name: str) -> bool:
  """Delete the named key. False when no key has that name."""
  if not Path(path).exists():
    return False
  with sqlite3.connect(path) as database:
    database.execute(SCHEMA)
    found = database.execute(f"DELETE FROM {TABLE} WHERE name = ?", (name,)).rowcount
  database.close()
  return found > 0


def find(path: Path | str, token: str) -> str | None:
  """The name of the key that matches the bearer token, and a new last use time."""
  if not token or not Path(path).exists():
    return None
  with sqlite3.connect(path) as database:
    try:
      row = database.execute(
        f"SELECT name, used FROM {TABLE} WHERE hash = ?", (digest(token),)
      ).fetchone()
    except sqlite3.OperationalError:
      row = None
    now = time.time()
    if row is not None and (row[1] is None or now - row[1] > USE_SECONDS):
      database.execute(f"UPDATE {TABLE} SET used = ? WHERE name = ?", (now, row[0]))
  database.close()
  return row[0] if row else None
