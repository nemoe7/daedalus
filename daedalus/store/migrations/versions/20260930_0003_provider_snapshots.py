"""Persist raw provider model-list responses for cached catalog rebuilds."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Create the raw provider snapshot table, if it does not exist."""
  if "provider_snapshots" in sa.inspect(op.get_bind()).get_table_names():
    return
  op.create_table(
    "provider_snapshots",
    sa.Column("url_hash", sa.TEXT(), nullable=False),
    sa.Column("payload", sa.TEXT(), nullable=False),
    sa.Column("updated", sa.REAL(), nullable=False),
    sa.PrimaryKeyConstraint("url_hash"),
  )


def downgrade() -> None:
  """Remove persisted provider snapshots."""
  op.drop_table("provider_snapshots")
