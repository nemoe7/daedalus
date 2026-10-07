"""The models table: the efforts that the gateway lists for 1 model."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Add the supported_efforts column, if the table does not have it."""
  columns = {
    column["name"] for column in sa.inspect(op.get_bind()).get_columns("models")
  }
  if "supported_efforts" in columns:
    return
  with op.batch_alter_table("models") as batch:
    batch.add_column(sa.Column("supported_efforts", sa.TEXT()))


def downgrade() -> None:
  """Remove the supported_efforts column."""
  with op.batch_alter_table("models") as batch:
    batch.drop_column("supported_efforts")
