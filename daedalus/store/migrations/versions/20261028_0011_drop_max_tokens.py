"""The models table: drop the retired max_tokens column."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Remove the max_tokens column if the table still holds it."""
  columns = {
    column["name"] for column in sa.inspect(op.get_bind()).get_columns("models")
  }
  if "max_tokens" not in columns:
    return
  with op.batch_alter_table("models") as batch:
    batch.drop_column("max_tokens")


def downgrade() -> None:
  """Restore the max_tokens column."""
  with op.batch_alter_table("models") as batch:
    batch.add_column(sa.Column("max_tokens", sa.NUMERIC()))
