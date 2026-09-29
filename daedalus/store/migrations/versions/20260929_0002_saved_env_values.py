"""The saved_env table: values for os.environ/NAME that the dashboard saves."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Make the saved_env table, if the file does not have it."""
  if "saved_env" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "saved_env",
    sa.Column("name", sa.TEXT(), nullable=False),
    sa.Column("value", sa.TEXT(), nullable=False),
    sa.Column("updated", sa.REAL(), nullable=False),
    sa.PrimaryKeyConstraint("name"),
  )


def downgrade() -> None:
  """Remove the saved_env table."""
  op.drop_table("saved_env")
