"""Discover provider models, add LiteLLM catalog metadata, and write the store."""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from daedalus.catalog.discovery import build_rows
from daedalus.catalog.enrichment import enrich
from daedalus.config import get_config
from daedalus.providers import ProviderError, hooks, provider_for
from daedalus.store import write_store

logger = logging.getLogger("daedalus.catalog")


def with_hooks(
  config: dict[str, Any], rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
  """Each catalog row after the on-catalog hooks of its provider. A hook cannot change the model id."""
  found = []
  for row in rows:
    model = row["id"]
    if hooks.files(config, model, "on-catalog"):
      try:
        provider, _ = provider_for(model, config)
      except ProviderError as exc:
        logger.warning("no on-catalog hooks for %s: %s", model, exc)
      else:
        row = hooks.run(
          "on-catalog",
          config,
          model,
          row,
          api_base=provider.base,
          headers=provider.headers(),
        )
        row = {**row, "id": model}
    found.append(row)
  return found


def refresh() -> Path:
  """Discover the provider models, and update the SQLite store."""
  config = get_config()
  failed: list[str] = []
  native, skipped = build_rows(config, failed=failed)
  lines = list(native)
  unenriched: list[str] = []
  rows, problems = enrich(lines, config, native=native, failed=unenriched)
  target = write_store(with_hooks(config, rows), keep=failed, fill=unenriched)
  for reason in [*skipped, *problems]:
    logger.warning("skipped %s", reason)
  for provider_name in failed:
    logger.warning(
      "kept the old rows of %s: its model list fetch failed", provider_name
    )
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return target
