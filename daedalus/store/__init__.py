"""The SQLite model store: the model table, and the tables of the other modules."""

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from daedalus.config import STATE_DIR
from daedalus.store import keys

MODELS_DB = STATE_DIR / "models.sqlite3"
# Metadata columns, in table order. Config values win over the catalog.
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


def write_store(
  rows: Iterable[dict[str, Any]],
  path: Path | str | None = None,
  keep: Iterable[str] = (),
  fill: Iterable[str] = (),
) -> Path:
  """Update the model rows in place in 1 transaction, and delete the stale rows."""
  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
  migrate(target)
  kept, filled = set(keep), set(fill)
  names = ("id", "provider", "slug", *COLUMNS)
  database = sqlite3.connect(target, isolation_level=None, timeout=10)
  try:
    database.execute("BEGIN IMMEDIATE")
    columns = ", ".join(
      f"{key} {'TEXT' if key in TEXT_COLUMNS else 'NUMERIC'}" for key in COLUMNS
    )
    database.execute(
      f"CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, provider TEXT, slug TEXT, {columns})"
    )
    present = {row[1] for row in database.execute("PRAGMA table_info(models)")}
    for key in names[1:]:
      if key not in present:
        kind = "TEXT" if key in {"provider", "slug", *TEXT_COLUMNS} else "NUMERIC"
        database.execute(f"ALTER TABLE models ADD COLUMN {key} {kind}")
    database.row_factory = sqlite3.Row
    old = {row["id"]: dict(row) for row in database.execute("SELECT * FROM models")}
    database.row_factory = None
    values = []
    for row in rows:
      provider, _, slug = row["id"].partition("/")
      found = {key: row.get(key) for key in COLUMNS}
      if provider in filled and row["id"] in old:
        previous = old[row["id"]]
        found = {
          key: previous.get(key) if value is None else value
          for key, value in found.items()
        }
      values.append((row["id"], provider, slug, *found.values()))
    updates = ", ".join(f"{key} = excluded.{key}" for key in names[1:])
    database.executemany(
      f"INSERT INTO models ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
      f" ON CONFLICT(id) DO UPDATE SET {updates}",
      values,
    )
    fresh = {value[0] for value in values}
    stale = [
      (key,)
      for key, row in old.items()
      if key not in fresh and row["provider"] not in kept
    ]
    database.executemany("DELETE FROM models WHERE id = ?", stale)
    database.execute("CREATE TABLE IF NOT EXISTS catalog (built REAL)")
    database.execute("DELETE FROM catalog")
    database.execute("INSERT INTO catalog VALUES (?)", (time.time(),))
    database.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    database.execute("COMMIT")
  except BaseException:
    if database.in_transaction:
      database.execute("ROLLBACK")
    raise
  finally:
    database.close()
  return target


def read_models(
  routable_only: bool = True, tools_only: bool = False, vision_only: bool = False
) -> list[str]:
  """The stored model ids, chat or unmatched rows by default, and tool or image rows on request."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    try:
      rows = database.execute(
        "SELECT id, mode, supports_function_calling, supports_vision"
        " FROM models ORDER BY rowid"
      )
    except sqlite3.OperationalError:
      return []
    return [
      key
      for key, mode, tools, vision in rows
      if (not routable_only or mode in ROUTABLE_MODES)
      and (not tools_only or tools)
      and (not vision_only or vision)
    ]
  finally:
    database.close()


def mode_models(mode: str, vision_only: bool = False) -> list[str]:
  """The stored models of one catalog mode in table order, only image input models on request."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(
      "SELECT id FROM models WHERE mode = ? AND (? OR supports_vision) ORDER BY rowid",
      (mode, not vision_only),
    ).fetchall()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  return [key for (key,) in rows]


# The input and output flags that the Models page shows as chips.
MEDIA_FLAGS = ("vision", "pdf_input", "audio_input", "audio_output")


def model_rows() -> list[dict[str, Any]]:
  """All rows, with the mode, the limits, the tool and reasoning flags, the effort and the media flags."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(
      "SELECT id, mode, max_input_tokens, supports_function_calling, supports_reasoning,"
      " reasoning_effort, "
      + ", ".join(f"supports_{flag}" for flag in MEDIA_FLAGS)
      + " FROM models"
      " ORDER BY rowid"
    ).fetchall()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  return [
    {
      "id": key,
      "mode": mode or "chat",
      "max_input_tokens": limit,
      "tools": bool(tools),
      "reasoning": bool(thinks),
      "effort": effort or None,
      "flags": [flag for flag, on in zip(MEDIA_FLAGS, media, strict=True) if on],
    }
    for key, mode, limit, tools, thinks, effort, *media in rows
  ]


# The catalog fields that `/v1/models` sends for each chat model.
INFO_NUMBERS = ("max_input_tokens", "max_output_tokens")
INFO_FLAGS = ("supports_function_calling", "supports_reasoning", "supports_vision")


def model_info() -> dict[str, dict[str, int | bool]]:
  """The token limits and the feature flags of each stored model, without empty values."""
  if not Path(MODELS_DB).exists():
    return {}
  names = (*INFO_NUMBERS, *INFO_FLAGS)
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(f"SELECT id, {', '.join(names)} FROM models").fetchall()
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()
  info: dict[str, dict[str, int | bool]] = {}
  for key, *values in rows:
    fields: dict[str, int | bool] = {}
    for name, value in zip(names, values, strict=True):
      if value is None:
        continue
      if name in INFO_FLAGS:
        fields[name] = bool(value)
        continue
      try:
        number = int(float(value))
      except (TypeError, ValueError):
        continue
      if number > 0:
        fields[name] = number
    info[key] = fields
  return info


def model_limits(model: str) -> dict[str, Any]:
  """The stored `reasoning_effort` and `max_output_tokens` of one model, without empty values."""
  if not Path(MODELS_DB).exists():
    return {}
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    row = database.execute(
      "SELECT reasoning_effort, max_output_tokens FROM models WHERE id = ?", (model,)
    ).fetchone()
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()
  if row is None:
    return {}
  effort, output = row
  found: dict[str, Any] = {}
  if isinstance(effort, str) and effort:
    found["reasoning_effort"] = effort
  try:
    number = int(float(output))
  except (TypeError, ValueError):
    number = 0
  if number > 0:
    found["max_output_tokens"] = number
  return found


def reasoning_flags() -> dict[str, bool]:
  """The reasoning flag of each stored model. No catalog value counts as false."""
  if not Path(MODELS_DB).exists():
    return {}
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute("SELECT id, supports_reasoning FROM models").fetchall()
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()
  return {key: bool(value) for key, value in rows}


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


def positive(value: object) -> float | None:
  """The number of a stored value, or None when it is not above 0."""
  try:
    number = float(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return None
  return number if number > 0 else None


def pace_limits() -> dict[str, tuple[float | None, float | None]]:
  """The `rpm` and `tpm` of each stored model that has one of them."""
  if not Path(MODELS_DB).exists():
    return {}
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute(
      "SELECT id, rpm, tpm FROM models WHERE rpm IS NOT NULL OR tpm IS NOT NULL"
    ).fetchall()
  except sqlite3.OperationalError:
    return {}
  finally:
    database.close()
  found = {key: (positive(rpm), positive(tpm)) for key, rpm, tpm in rows}
  return {key: pair for key, pair in found.items() if pair != (None, None)}


ORDERS_SCHEMA = (
  "CREATE TABLE IF NOT EXISTS provider_orders (id TEXT PRIMARY KEY, endpoints TEXT)"
)


def write_orders(
  orders: dict[str, list[str]], keep: Iterable[str] = (), path: Path | str | None = None
) -> None:
  """Replace the endpoint order of each model. A model in `keep` keeps its old order."""
  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
  kept = set(keep)
  with sqlite3.connect(target, timeout=10) as database:
    database.execute(ORDERS_SCHEMA)
    old = [row[0] for row in database.execute("SELECT id FROM provider_orders")]
    database.executemany(
      "DELETE FROM provider_orders WHERE id = ?",
      [(key,) for key in old if key not in orders and key not in kept],
    )
    database.executemany(
      "INSERT INTO provider_orders VALUES (?, ?)"
      " ON CONFLICT(id) DO UPDATE SET endpoints = excluded.endpoints",
      [(key, json.dumps(value)) for key, value in orders.items()],
    )
  database.close()


def provider_order(model: str) -> list[str]:
  """The upstream endpoints of one model in the order to try, or an empty list."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    row = database.execute(
      "SELECT endpoints FROM provider_orders WHERE id = ?", (model,)
    ).fetchone()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  try:
    found = json.loads(row[0]) if row else []
  except (TypeError, ValueError):
    return []
  return [str(item) for item in found] if isinstance(found, list) else []


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
