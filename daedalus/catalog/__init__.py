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

# The provider names whose model list failed in the last rebuild, for its event.
LAST_FAILED: list[str] = []


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


def _rebuild(cached: bool) -> Path:
  """Build the model store from fetched provider lists or cached snapshots."""
  global LAST_FAILED
  # The default provider file of the repository fills the names that the live config lacks, so a
  # new default provider lands here. The fetch never writes the local file of the operator.
  from daedalus.config import defaults

  config = get_config()
  defaults.fill(config, defaults.refresh())
  failed: list[str] = []
  native, skipped = build_rows(
    config, failed=failed, cached=cached, save_snapshots=not cached
  )
  LAST_FAILED = list(failed)
  lines = list(native)
  unenriched: list[str] = []
  rows, problems = enrich(lines, config, native=native, failed=unenriched)
  target = write_store(with_hooks(config, rows), keep=failed, fill=unenriched)
  for reason in [*skipped, *problems]:
    logger.warning("skipped %s", reason)
  for provider_name in failed:
    logger.warning(
      "kept the old rows of %s: its model list could not be loaded", provider_name
    )
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return target


def refresh() -> Path:
  """Fetch provider lists, save their snapshots, and update the SQLite model store."""
  return _rebuild(cached=False)


def rebuild_cached() -> Path:
  """Rebuild the SQLite model store without making provider requests."""
  return _rebuild(cached=True)
