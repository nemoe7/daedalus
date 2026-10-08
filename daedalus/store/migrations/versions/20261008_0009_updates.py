"""The updates table: the last read of the update check."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Create the update check table, if it does not exist."""
  if "updates" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "updates",
    sa.Column("name", sa.TEXT(), nullable=False),
    sa.Column("value", sa.TEXT(), nullable=False),
    sa.Column("updated", sa.REAL(), nullable=False),
    sa.PrimaryKeyConstraint("name"),
  )


def downgrade() -> None:
  """Remove the update check table."""
  op.drop_table("updates")
