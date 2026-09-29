"""The layout before Alembic: add the missing tables and columns, and name the old key."""

import time

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# The models metadata columns at this step, in table order.
TEXT_COLUMNS = ("mode", "reasoning_effort")
COLUMNS = (
  "mode",
  "max_input_tokens",
  "max_output_tokens",
  "max_tokens",
  "rpm",
  "tpm",
  "reasoning_effort",
  "supports_function_calling",
  "supports_tool_choice",
  "supports_parallel_function_calling",
  "supports_response_schema",
  "supports_reasoning",
  "supports_vision",
  "supports_pdf_input",
  "supports_audio_input",
  "supports_audio_output",
  "supports_web_search",
)


def models_columns() -> list[sa.Column]:
  """The models columns at this step."""
  return [
    sa.Column("id", sa.TEXT, primary_key=True),
    sa.Column("provider", sa.TEXT),
    sa.Column("slug", sa.TEXT),
    *(
      sa.Column(key, sa.TEXT if key in TEXT_COLUMNS else sa.NUMERIC) for key in COLUMNS
    ),
  ]


TABLES = {
  "models": models_columns,
  "catalog": lambda: [sa.Column("built", sa.REAL)],
  "api_keys": lambda: [
    sa.Column("name", sa.TEXT, primary_key=True),
    sa.Column("hash", sa.TEXT, nullable=False, unique=True),
    sa.Column("start", sa.TEXT, nullable=False),
    sa.Column("created", sa.REAL, nullable=False),
    sa.Column("used", sa.REAL),
  ],
  "requests": lambda: [
    sa.Column("id", sa.INTEGER, primary_key=True),
    sa.Column("row", sa.TEXT, nullable=False),
  ],
  "signatures": lambda: [
    sa.Column("call", sa.TEXT, primary_key=True),
    sa.Column("model", sa.TEXT, primary_key=True),
    sa.Column("signature", sa.TEXT, nullable=False),
    sa.Column("used", sa.REAL, nullable=False),
  ],
  "calls": lambda: [
    sa.Column("call", sa.TEXT, primary_key=True),
    sa.Column("model", sa.TEXT, nullable=False),
    sa.Column("used", sa.REAL, nullable=False),
  ],
  "cooldowns": lambda: [
    sa.Column("model", sa.TEXT, primary_key=True),
    sa.Column("until", sa.REAL, nullable=False),
    sa.Column("span", sa.REAL, nullable=False),
    sa.Column("reason", sa.TEXT, nullable=False),
  ],
  "weights": lambda: [
    sa.Column("model", sa.TEXT, primary_key=True),
    sa.Column("weight", sa.REAL, nullable=False),
    sa.Column("updated", sa.REAL, nullable=False),
  ],
  "pins": lambda: [
    sa.Column("key", sa.TEXT, primary_key=True),
    sa.Column("slot", sa.TEXT, primary_key=True),
    sa.Column("model", sa.TEXT, nullable=False),
    sa.Column("used", sa.REAL, nullable=False),
  ],
  "tiers": lambda: [
    sa.Column("key", sa.TEXT, primary_key=True),
    sa.Column("tier", sa.INTEGER, nullable=False),
    sa.Column("used", sa.REAL, nullable=False),
  ],
}


def upgrade() -> None:
  """Make each table of this step, and add the models columns that an old file does not have."""
  inspector = sa.inspect(op.get_bind())
  present = set(inspector.get_table_names())
  for name, columns in TABLES.items():
    if name not in present:
      op.create_table(name, *columns())
  if "models" in present:
    have = {column["name"] for column in inspector.get_columns("models")}
    for column in models_columns()[1:]:
      if column.name not in have:
        op.add_column("models", column)
  if "api_key" in present:
    # The one old local key of a file before named keys gets the name "default".
    op.execute(
      sa.text(
        "INSERT OR IGNORE INTO api_keys (name, hash, start, created)"
        " SELECT 'default', hash, '', :now FROM api_key"
      ).bindparams(now=time.time())
    )
    op.drop_table("api_key")


def downgrade() -> None:
  """The first step has no way back."""
  raise NotImplementedError("the baseline step has no downgrade")
