"""The levels table: the reasoning level of the last answer of a session key."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Create the levels table, if the file does not have it."""
  if "levels" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "levels",
    sa.Column("key", sa.TEXT(), nullable=False),
    sa.Column("effort", sa.TEXT(), nullable=False),
    sa.Column("used", sa.REAL(), nullable=False),
    sa.PrimaryKeyConstraint("key"),
  )


def downgrade() -> None:
  """Remove the levels table."""
  op.drop_table("levels")
