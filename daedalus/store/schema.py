"""The current table layout of the state file, as SQLAlchemy Core tables for Alembic."""

from sqlalchemy import INTEGER, NUMERIC, REAL, TEXT, Column, MetaData, Table

from daedalus.store import COLUMNS, TEXT_COLUMNS

METADATA = MetaData()

Table(
  "models",
  METADATA,
  Column("id", TEXT, primary_key=True),
  Column("provider", TEXT),
  Column("slug", TEXT),
  *(Column(key, TEXT if key in TEXT_COLUMNS else NUMERIC) for key in COLUMNS),
)
Table("catalog", METADATA, Column("built", REAL))
Table(
  "api_keys",
  METADATA,
  Column("name", TEXT, primary_key=True),
  Column("hash", TEXT, nullable=False, unique=True),
  Column("start", TEXT, nullable=False),
  Column("created", REAL, nullable=False),
  Column("used", REAL),
)
Table(
  "requests",
  METADATA,
  Column("id", INTEGER, primary_key=True),
  Column("row", TEXT, nullable=False),
)
Table(
  "signatures",
  METADATA,
  Column("call", TEXT, primary_key=True),
  Column("model", TEXT, primary_key=True),
  Column("signature", TEXT, nullable=False),
  Column("used", REAL, nullable=False),
)
Table(
  "calls",
  METADATA,
  Column("call", TEXT, primary_key=True),
  Column("model", TEXT, nullable=False),
  Column("used", REAL, nullable=False),
)
Table(
  "cooldowns",
  METADATA,
  Column("model", TEXT, primary_key=True),
  Column("until", REAL, nullable=False),
  Column("span", REAL, nullable=False),
  Column("reason", TEXT, nullable=False),
)
Table(
  "weights",
  METADATA,
  Column("model", TEXT, primary_key=True),
  Column("weight", REAL, nullable=False),
  Column("updated", REAL, nullable=False),
)
Table(
  "pins",
  METADATA,
  Column("key", TEXT, primary_key=True),
  Column("slot", TEXT, primary_key=True),
  Column("model", TEXT, nullable=False),
  Column("used", REAL, nullable=False),
)
Table(
  "tiers",
  METADATA,
  Column("key", TEXT, primary_key=True),
  Column("tier", INTEGER, nullable=False),
  Column("used", REAL, nullable=False),
)
