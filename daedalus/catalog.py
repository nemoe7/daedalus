"""Discover provider models, add LiteLLM catalog metadata, and write the store."""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from daedalus.config import STATE_DIR, get_config
from daedalus.discovery import build_rows, text
from daedalus.enrichment import enrich
from daedalus.store import COLUMNS, ROUTABLE_MODES, write_store

logger = logging.getLogger("daedalus.catalog")

MODELS_TSV = STATE_DIR / "models.tsv"


def write_models_tsv(
  rows: Iterable[dict[str, Any]], path: Path | str = MODELS_TSV
) -> Path:
  """Write the routable rows as a tab-separated table with a header row."""
  target = Path(path)
  target.parent.mkdir(parents=True, exist_ok=True)
  header = ("id", *COLUMNS)
  lines = ["\t".join(header)]
  lines.extend(
    "\t".join(text(row.get(key)) for key in header)
    for row in rows
    if row.get("mode") in ROUTABLE_MODES
  )
  with target.open("w", encoding="utf-8", newline="\n") as handle:
    handle.write("".join(f"{line}\n" for line in lines))
  return target


def refresh() -> Path:
  """Discover the provider models, and rewrite the SQLite store and models.tsv."""
  config = get_config()
  native, skipped = build_rows(config)
  lines = list(native)
  rows, problems = enrich(lines, config, native=native)
  write_store(rows)
  target = write_models_tsv(rows)
  for reason in [*skipped, *problems]:
    logger.warning("skipped %s", reason)
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return target
