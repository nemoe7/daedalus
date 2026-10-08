"""The models table: the tier that claims 1 model, resolved once at write time."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Add the tier column, if the table does not have it."""
  columns = {
    column["name"] for column in sa.inspect(op.get_bind()).get_columns("models")
  }
  if "tier" in columns:
    return
  with op.batch_alter_table("models") as batch:
    batch.add_column(sa.Column("tier", sa.TEXT()))


def downgrade() -> None:
  """Remove the tier column."""
  with op.batch_alter_table("models") as batch:
    batch.drop_column("tier")
