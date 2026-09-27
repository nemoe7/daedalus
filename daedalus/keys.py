"""The local API key, kept as one SHA-256 hash in the model store."""

import hashlib
import secrets
import sqlite3
from pathlib import Path

TABLE = "api_key"
MIN_LENGTH = 16


def generate() -> str:
  """A new key: `sk-` and 43 URL-safe characters."""
  return "sk-" + secrets.token_urlsafe(32)


def valid(key: str) -> bool:
  """Accept a custom key of 16 or more characters, with no spaces."""
  return len(key) >= MIN_LENGTH and not any(char.isspace() for char in key)


def bearer(header: str) -> str:
  """The token of an `Authorization: Bearer` header, or an empty string."""
  return header[7:].strip() if header.lower().startswith("bearer ") else ""


def digest(key: str) -> str:
  return hashlib.sha256(key.encode()).hexdigest()


def stored_hash(path: Path | str) -> str | None:
  """The stored key hash, or None when the store has no key."""
  if not Path(path).exists():
    return None
  database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
  try:
    row = database.execute(f"SELECT hash FROM {TABLE}").fetchone()
  except sqlite3.OperationalError:
    return None
  finally:
    database.close()
  return row[0] if row else None


def save_hash(path: Path | str, value: str | None) -> None:
  """Replace the stored key hash. None removes the key."""
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  with sqlite3.connect(path) as database:
    database.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (hash TEXT NOT NULL)")
    database.execute(f"DELETE FROM {TABLE}")
    if value:
      database.execute(f"INSERT INTO {TABLE} (hash) VALUES (?)", (value,))
  database.close()


def matches(path: Path | str, token: str) -> bool | None:
  """Test one bearer token. None means that no key is set."""
  stored = stored_hash(path)
  if stored is None:
    return None
  return secrets.compare_digest(digest(token), stored)
