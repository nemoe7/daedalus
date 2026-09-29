"""The Alembic setup: a connection from `store.migrate`, or the URL of alembic.ini."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from daedalus.store.schema import METADATA


def run(connection) -> None:
  """Run the steps on 1 connection, in batch mode so that SQLite can change columns."""
  context.configure(
    connection=connection, target_metadata=METADATA, render_as_batch=True
  )
  with context.begin_transaction():
    context.run_migrations()


given = context.config.attributes.get("connection")
if given is not None:
  run(given)
else:
  engine = engine_from_config(
    context.config.get_section(context.config.config_ini_section, {}),
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
  )
  with engine.connect() as connection:
    run(connection)
