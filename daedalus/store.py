"""The SQLite model store: the model table and the tables that a rebuild keeps."""

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from daedalus import keys
from daedalus.config import STATE_DIR

MODELS_DB = STATE_DIR / "models.sqlite3"
# Metadata columns, in table and models.tsv order. Config values win over the catalog.
COLUMNS = (
  "mode",
  "max_input_tokens",
  "max_output_tokens",
  "max_tokens",
  "rpm",
  "tpm",
  "reasoning_effort",
  "supports_function_calling",
  "supports_tool_choice",
  "supports_parallel_function_calling",
  "supports_response_schema",
  "supports_reasoning",
  "supports_vision",
  "supports_pdf_input",
  "supports_audio_input",
  "supports_audio_output",
  "supports_web_search",
)
# Tables that a store rebuild keeps: the API keys, the model weights, the pins and the signatures.
KEPT_TABLES = frozenset({keys.TABLE, "weights", "pins", "signatures"})
# Each step of `migrate` raises the file version by 1.
SCHEMA_VERSION = 1
TEXT_COLUMNS = frozenset({"mode", "reasoning_effort"})
# Rows that enter the chat chains. No catalog match gives no mode.
ROUTABLE_MODES = (None, "chat")


def migrate(path: Path | str | None = None) -> None:
  """Bring an older store file to the current table layout, one version step at a time."""
  target = Path(path or MODELS_DB)
  if not target.exists():
    return
  with sqlite3.connect(target) as database:
    version = database.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
      # Version 1: named API keys. The one old local key gets the name "default".
      tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
      if "api_key" in tables:
        database.execute(keys.SCHEMA)
        for (value,) in database.execute("SELECT hash FROM api_key").fetchall():
          database.execute(
            f"INSERT OR IGNORE INTO {keys.TABLE} (name, hash, start, created) VALUES (?, ?, ?, ?)",
            ("default", value, "", time.time()),
          )
        database.execute("DROP TABLE api_key")
      database.execute("PRAGMA user_version = 1")
  database.close()


def keep_tables(database: sqlite3.Connection, old: Path) -> None:
  """Copy the key, weight, pin and signature tables of the old store into a new store."""
  database.execute("ATTACH DATABASE ? AS old", (str(old),))
  found = database.execute(
    "SELECT name, sql FROM old.sqlite_master WHERE type = 'table'"
  ).fetchall()
  for name, sql in found:
    if name in KEPT_TABLES:
      database.execute(sql)
      database.execute(f"INSERT INTO {name} SELECT * FROM old.{name}")
  database.commit()
  database.execute("DETACH DATABASE old")


def write_store(rows: Iterable[dict[str, Any]], path: Path | str | None = None) -> Path:
  """Replace the model table in one transaction-safe file swap, and record the build time."""
  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
  temporary = target.with_suffix(".tmp")
  temporary.unlink(missing_ok=True)
  columns = ", ".join(
    f"{key} {'TEXT' if key in TEXT_COLUMNS else 'NUMERIC'}" for key in COLUMNS
  )
  with sqlite3.connect(temporary) as database:
    database.execute(
      f"CREATE TABLE models (id TEXT PRIMARY KEY, provider TEXT, slug TEXT, {columns})"
    )
    database.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    if target.exists():
      migrate(target)
      keep_tables(database, target)
    names = ("id", "provider", "slug", *COLUMNS)
    values = []
    for row in rows:
      provider, _, slug = row["id"].partition("/")
      values.append((row["id"], provider, slug, *(row.get(key) for key in COLUMNS)))
    marks = ", ".join("?" * len(names))
    database.executemany(
      f"INSERT INTO models ({', '.join(names)}) VALUES ({marks})", values
    )
    database.execute("CREATE TABLE catalog (built REAL)")
    database.execute("INSERT INTO catalog VALUES (?)", (time.time(),))
  database.close()
  temporary.replace(target)
  return target


def read_models(routable_only: bool = True, tools_only: bool = False) -> list[str]:
  """The stored model ids, chat or unmatched rows by default, and tool rows on request."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    try:
      rows = database.execute(
        "SELECT id, mode, supports_function_calling FROM models ORDER BY rowid"
      )
    except sqlite3.OperationalError:
      return []
    return [
      key
      for key, mode, tools in rows
      if (not routable_only or mode in ROUTABLE_MODES) and (not tools_only or tools)
    ]
  finally:
    database.close()


def model_rows() -> list[dict[str, Any]]:
  """The routable rows, with the input limit and the tool flag."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(
      "SELECT id, mode, max_input_tokens, supports_function_calling FROM models"
      " ORDER BY rowid"
    ).fetchall()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  return [
    {"id": key, "max_input_tokens": limit, "tools": bool(tools)}
    for key, mode, limit, tools in rows
    if mode in ROUTABLE_MODES
  ]


def input_limits() -> dict[str, int]:
  """The `max_input_tokens` of each stored model that has one."""
  if not Path(MODELS_DB).exists():
    return {}
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(
      "SELECT id, max_input_tokens FROM models WHERE max_input_tokens IS NOT NULL"
    ).fetchall()
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()
  limits = {}
  for key, value in rows:
    try:
      limit = int(float(value))
    except (TypeError, ValueError):
      continue
    if limit > 0:
      limits[key] = limit
  return limits


def built() -> float | None:
  """The time of the last catalog rebuild. None without a store or an older store."""
  if not Path(MODELS_DB).exists():
    return None
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    row = database.execute("SELECT built FROM catalog").fetchone()
  except sqlite3.OperationalError:
    return None
  finally:
    database.close()
  return row[0] if row else None


def has_store() -> bool:
  """Tell if the store file exists and has the model table."""
  if not Path(MODELS_DB).exists():
    return False
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    found = database.execute(
      "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'models'"
    ).fetchone()
  finally:
    database.close()
  return found is not None
