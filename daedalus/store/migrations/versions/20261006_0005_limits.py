"""The limits table: the lane rows and the balance cards of the last look."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Create the limits table, if the file does not have it."""
  if "limits" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "limits",
    sa.Column("name", sa.TEXT(), nullable=False),
    sa.Column("payload", sa.TEXT(), nullable=False),
    sa.Column("updated", sa.REAL(), nullable=False),
    sa.PrimaryKeyConstraint("name"),
  )


def downgrade() -> None:
  """Remove the limits table."""
  op.drop_table("limits")
