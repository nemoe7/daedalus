"""The catalog_rebuilds table: the diff of each catalog rebuild."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Create the rebuild event table, if it does not exist."""
  if "catalog_rebuilds" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "catalog_rebuilds",
    sa.Column("id", sa.INTEGER(), primary_key=True),
    sa.Column("at", sa.REAL(), nullable=False),
    sa.Column("reason", sa.TEXT(), nullable=False),
    sa.Column("models", sa.INTEGER(), nullable=False),
    sa.Column("added", sa.TEXT(), nullable=False),
    sa.Column("removed", sa.TEXT(), nullable=False),
    sa.Column("changed", sa.TEXT(), nullable=False),
    sa.Column("failed", sa.TEXT(), nullable=False),
  )


def downgrade() -> None:
  """Remove the rebuild event table."""
  op.drop_table("catalog_rebuilds")
