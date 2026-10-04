"""The SQLite model store: the model table, and the tables of the other modules."""

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from daedalus.config import STATE_DIR
from daedalus.store.database import connect_read

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
# The Alembic steps: env.py, and 1 file in versions/ for each layout change.
MIGRATIONS = Path(__file__).with_name("migrations")
TEXT_COLUMNS = frozenset({"mode", "reasoning_effort"})
# Rows that enter the chat chains. No catalog match gives no mode.
ROUTABLE_MODES = (None, "chat")


def migrate(path: Path | str | None = None, revision: str = "head") -> None:
  """Bring the state file to the newest table layout, or to 1 step, with the Alembic steps."""
  # SQLAlchemy and Alembic load only here, at the start, so the requests do not load them.
  from alembic import command
  from alembic.config import Config
  from sqlalchemy import create_engine, pool

  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
  setup = Config()
  setup.set_main_option("script_location", str(MIGRATIONS))
  engine = create_engine(f"sqlite:///{target}", poolclass=pool.NullPool)
  try:
    with engine.begin() as connection:
      setup.attributes["connection"] = connection
      command.upgrade(setup, revision)
  finally:
    engine.dispose()


def write_store(
  rows: Iterable[dict[str, Any]],
  path: Path | str | None = None,
  keep: Iterable[str] = (),
  fill: Iterable[str] = (),
) -> Path:
  """Update the model rows in place in 1 transaction, and delete the stale rows."""
  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
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
  database = connect_read(MODELS_DB)
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
  database = connect_read(MODELS_DB)
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


# The names of a model row: its own key, the parts of it, and every stored column.
STORED_COLUMNS = ("id", "provider", "slug", *COLUMNS)


def stored_rows() -> list[dict[str, Any]]:
  """Every stored model row, with every column of the models table.

  The column names come from `STORED_COLUMNS`, so a new column appears here, and in
  `daedalus dump models`, without another edit.
  """
  if not Path(MODELS_DB).exists():
    return []
  database = connect_read(MODELS_DB)
  try:
    rows = database.execute(
      f"SELECT {', '.join(STORED_COLUMNS)} FROM models ORDER BY rowid"
    ).fetchall()
  except sqlite3.OperationalError:
    return []
  finally:
    database.close()
  return [
    {
      key: bool(value) if key.startswith("supports_") else value
      for key, value in zip(STORED_COLUMNS, row, strict=True)
    }
    for row in rows
  ]


def model_rows() -> list[dict[str, Any]]:
  """The pages' view of the rows: mode, the input limit, the tool and reasoning facts, the media flags."""
  return [
    {
      "id": row["id"],
      "mode": row["mode"] or "chat",
      "max_input_tokens": row["max_input_tokens"],
      "tools": row["supports_function_calling"],
      "reasoning": row["supports_reasoning"],
      "effort": row["reasoning_effort"] or None,
      "flags": [flag for flag in MEDIA_FLAGS if row[f"supports_{flag}"]],
    }
    for row in stored_rows()
  ]


# The catalog fields that `/v1/models` sends for each chat model.
INFO_NUMBERS = ("max_input_tokens", "max_output_tokens")
INFO_FLAGS = ("supports_function_calling", "supports_reasoning", "supports_vision")


def model_info() -> dict[str, dict[str, int | bool]]:
  """The token limits and the feature flags of each stored model, without empty values."""
  if not Path(MODELS_DB).exists():
    return {}
  names = (*INFO_NUMBERS, *INFO_FLAGS)
  database = connect_read(MODELS_DB)
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
  database = connect_read(MODELS_DB)
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
  database = connect_read(MODELS_DB)
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
  database = connect_read(MODELS_DB)
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
  database = connect_read(MODELS_DB)
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


def built() -> float | None:
  """The time of the last catalog rebuild. None without a store or an older store."""
  if not Path(MODELS_DB).exists():
    return None
  database = connect_read(MODELS_DB)
  try:
    row = database.execute("SELECT built FROM catalog").fetchone()
  except sqlite3.OperationalError:
    return None
  finally:
    database.close()
  return row[0] if row else None


def readable() -> bool:
  """Tell if the model store can be read. A missing file is readable: it holds no rows."""
  if not Path(MODELS_DB).exists():
    return True
  database = connect_read(MODELS_DB)
  try:
    database.execute("SELECT count(*) FROM models").fetchall()
  except sqlite3.DatabaseError:
    return False
  finally:
    database.close()
  return True


def set_aside() -> Path | None:
  """Move an unreadable store out of the way, with its write-ahead files, for a new build."""
  target = Path(MODELS_DB)
  if not target.exists():
    return None
  broken = target.with_name(target.name + ".broken")
  broken.unlink(missing_ok=True)
  target.replace(broken)
  for suffix in ("-wal", "-shm"):
    Path(f"{target}{suffix}").unlink(missing_ok=True)
  return broken


def has_store() -> bool:
  """Tell if a catalog build wrote the store, because the Alembic steps make only empty tables."""
  if not Path(MODELS_DB).exists():
    return False
  database = connect_read(MODELS_DB)
  try:
    found = database.execute(
      "SELECT EXISTS (SELECT 1 FROM catalog) OR EXISTS (SELECT 1 FROM models)"
    ).fetchone()[0]
  except sqlite3.OperationalError:
    found = False
  finally:
    database.close()
  return bool(found)
