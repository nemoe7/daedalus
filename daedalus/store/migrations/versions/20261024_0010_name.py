"""The models table: the human-readable display name of each model."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Add the name column if the table lacks it."""
  columns = {
    column["name"] for column in sa.inspect(op.get_bind()).get_columns("models")
  }
  if "name" in columns:
    return
  with op.batch_alter_table("models") as batch:
    batch.add_column(sa.Column("name", sa.TEXT()))


def downgrade() -> None:
  """Remove the name column."""
  with op.batch_alter_table("models") as batch:
    batch.drop_column("name")
