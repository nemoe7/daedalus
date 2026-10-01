"""The declared state schema: the tables, the keys and the column types."""

from sqlalchemy import NUMERIC, TEXT, create_engine, text

from daedalus import store
from daedalus.store import schema

TABLES = schema.METADATA.tables


def test_the_declared_schema_creates_every_table() -> None:
  """Every table the schema declares comes out of `create_all`."""
  engine = create_engine("sqlite://")
  schema.METADATA.create_all(engine)
  with engine.connect() as link:
    made = {
      row[0]
      for row in link.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    }
  assert set(TABLES) <= made


def test_every_keyed_table_names_a_primary_key() -> None:
  """Every table but `catalog` has a key. `catalog` holds one row, the build time."""
  for name, table in TABLES.items():
    if name == "catalog":
      continue
    assert len(table.primary_key.columns) > 0, name
  assert [column.name for column in TABLES["catalog"].columns] == ["built"]


def test_models_holds_the_id_the_provider_the_slug_and_the_columns() -> None:
  """The model table is the three fixed columns plus every column of the catalog."""
  assert [column.name for column in TABLES["models"].columns][:3] == [
    "id",
    "provider",
    "slug",
  ]
  assert set(TABLES["models"].columns.keys()) == {"id", "provider", "slug"} | set(
    store.COLUMNS
  )


def test_the_column_types_follow_the_text_columns() -> None:
  """A text column of the catalog is TEXT, and any other one is NUMERIC."""
  columns = TABLES["models"].columns
  for key in store.COLUMNS:
    kind = TEXT if key in store.TEXT_COLUMNS else NUMERIC
    assert isinstance(columns[key].type, kind), key


def test_the_id_of_models_is_the_key() -> None:
  """One primary key of one column, so Alembic can alter the table."""
  assert [column.name for column in TABLES["models"].primary_key.columns] == ["id"]


def test_a_composite_key_table_names_every_key_column() -> None:
  """`signatures` and `pins` key on two columns."""
  for name, keys in (("signatures", ["call", "model"]), ("pins", ["key", "slot"])):
    assert [column.name for column in TABLES[name].primary_key.columns] == keys
