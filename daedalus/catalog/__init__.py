"""Discover provider models, add LiteLLM catalog metadata, and write the store."""

import logging
from pathlib import Path

from daedalus.catalog.discovery import build_rows
from daedalus.catalog.endpoints import endpoint_orders
from daedalus.catalog.enrichment import enrich
from daedalus.config import get_config
from daedalus.store import write_orders, write_store

logger = logging.getLogger("daedalus.catalog")


def refresh() -> Path:
  """Discover the provider models, and update the SQLite store."""
  config = get_config()
  failed: list[str] = []
  native, skipped = build_rows(config, failed=failed)
  lines = list(native)
  unenriched: list[str] = []
  rows, problems = enrich(lines, config, native=native, failed=unenriched)
  target = write_store(rows, keep=failed, fill=unenriched)
  orders, missed = endpoint_orders(config, lines)
  write_orders(orders, keep=missed, path=target)
  for reason in [*skipped, *problems]:
    logger.warning("skipped %s", reason)
  for provider_name in failed:
    logger.warning(
      "kept the old rows of %s: its model list fetch failed", provider_name
    )
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return target
