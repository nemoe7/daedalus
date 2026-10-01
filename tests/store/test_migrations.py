import sqlite3
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import daedalus
from daedalus import dashboard, store
from daedalus.catalog import discovery
from daedalus.providers import signatures
from daedalus.routing import cooldowns, loops, penalties
from daedalus.store import keys, saved_env
from daedalus.store.database import open_db
from daedalus.store.schema import METADATA

# The CREATE statements of the request code, which runs without Alembic.
STATEMENTS = (
  discovery.SNAPSHOT_SCHEMA,
  dashboard.TABLE,
  signatures.SCHEMA,
  cooldowns.TABLE,
  loops.SCHEMA,
  *penalties.TABLES,
  keys.SCHEMA,
  saved_env.SCHEMA,
)


def layout(path: Path) -> dict[str, tuple]:
  """The columns and unique indexes of each table, in a form that ignores the SQL text."""
  database = sqlite3.connect(path)
  found = {}
  names = database.execute(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'alembic_version'"
  )
  for (name,) in names.fetchall():
    columns = tuple(
      (column, kind, bool(notnull or pk), pk)
      for _, column, kind, notnull, _, pk in database.execute(
        f"PRAGMA table_info({name})"
      )
    )
    unique = sorted(
      tuple(row[2] for row in database.execute(f"PRAGMA index_info('{index}')"))
      for _, index, is_unique, origin, _ in database.execute(
        f"PRAGMA index_list({name})"
      )
      if is_unique and origin == "u"
    )
    found[name] = (columns, tuple(unique))
  database.close()
  return found


def test_head_matches_schema(tmp_path: Path) -> None:
  """The Alembic steps give the layout of schema.py, so a change needs a new step."""
  target = tmp_path / "models.sqlite3"
  store.migrate(target)
  engine = create_engine(f"sqlite:///{target}")
  with engine.connect() as connection:
    changes = compare_metadata(MigrationContext.configure(connection), METADATA)
  engine.dispose()
  assert changes == [], changes


def test_code_matches_head(tmp_path: Path) -> None:
  """The CREATE statements of the request code give the same layout as the Alembic steps."""
  steps, code = tmp_path / "steps.sqlite3", tmp_path / "code.sqlite3"
  store.migrate(steps)
  store.write_store([], code)
  open_db(code, STATEMENTS).close()
  assert layout(code) == layout(steps)
  assert set(layout(code)) == set(METADATA.tables), "each table is in schema.py"
  source = Path(daedalus.__file__).parent
  made = sum(
    path.read_text(encoding="utf-8").count("CREATE TABLE IF NOT EXISTS")
    for path in source.rglob("*.py")
  )
  # The 2 more are the models and catalog tables of write_store.
  assert made == len(STATEMENTS) + 2, "a new CREATE statement goes into STATEMENTS"


def test_old_file(tmp_path: Path) -> None:
  """A file from before named keys and the newer model columns comes to the newest layout."""
  target = tmp_path / "models.sqlite3"
  with sqlite3.connect(target) as old:
    old.execute("CREATE TABLE api_key (hash TEXT NOT NULL)")
    old.execute("INSERT INTO api_key VALUES (?)", (keys.digest("old-local-key-0001"),))
    old.execute("CREATE TABLE models (id TEXT PRIMARY KEY)")
    old.execute("INSERT INTO models VALUES ('groq/a')")
  old.close()
  store.migrate(target)
  assert keys.find(target, "old-local-key-0001") == "default", "the old key stays"
  assert keys.listing(target)[0]["start"] == "", "the old key start is not known"
  fresh = tmp_path / "fresh.sqlite3"
  store.migrate(fresh)
  assert layout(target) == layout(fresh), "the missing tables and columns are there"
  with sqlite3.connect(target) as database:
    assert database.execute("SELECT id FROM models").fetchall() == [("groq/a",)]
    version = database.execute("SELECT * FROM alembic_version").fetchall()
  database.close()
  with sqlite3.connect(fresh) as database:
    assert database.execute("SELECT * FROM alembic_version").fetchall() == version
  database.close()
  store.migrate(target)
  assert [row["name"] for row in keys.listing(target)] == ["default"], "a second run"


def test_current_file(tmp_path: Path) -> None:
  """A file from before Alembic has the layout of the first step, and keeps its rows."""
  target = tmp_path / "models.sqlite3"
  store.migrate(target, "0001")
  with sqlite3.connect(target) as database:
    database.execute("DROP TABLE alembic_version")
    database.execute("INSERT INTO models (id, mode) VALUES ('groq/a', 'chat')")
    database.execute(
      "INSERT INTO api_keys (name, hash, start, created) VALUES ('laptop', 'h', 'sk-', 1)"
    )
  database.close()
  store.migrate(target)
  assert [row["name"] for row in keys.listing(target)] == ["laptop"]
  with sqlite3.connect(target) as database:
    rows = database.execute("SELECT id, mode FROM models").fetchall()
  database.close()
  assert rows == [("groq/a", "chat")], rows
