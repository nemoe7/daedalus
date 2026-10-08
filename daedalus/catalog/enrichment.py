"""Add the catalog metadata and config values to each discovered model."""

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx

from daedalus.catalog.discovery import (
  MAX_PAGES,
  Fetch,
  fetch_json,
  matches,
  read_each,
  save_snapshot,
  with_param,
)
from daedalus.config import block_for
from daedalus.routing.router import EFFORT_RANKS, claiming_tier, efforts
from daedalus.store import COLUMNS

logger = logging.getLogger("daedalus.catalog")

LITELLM_CATALOG = "https://api.litellm.ai/model_catalog"


LITELLM_PAGE_SIZE = 500


# Provider names that differ in the LiteLLM catalog. Kilo serves OpenRouter slugs.
LITELLM_PROVIDER = {"z-ai": "zai", "kilo": "openrouter"}

# The modelschemas service: live per-provider facts, read anonymously, 1 list per provider.
MODELSCHEMAS_URL = "https://modelschemas.com/v1/models"


# Provider names that differ in the modelschemas service. Kilo serves OpenRouter slugs.
MODELSCHEMAS_PROVIDER = {
  "cloudflare": "cloudflare-workers-ai",
  "kilo": "openrouter",
  "z-ai": "zai",
}


def litellm_entries(
  provider_name: str, fetch: Fetch = fetch_json
) -> dict[str, dict[str, Any]]:
  """Read one provider from the LiteLLM catalog, page by page, keyed by slug."""
  name = LITELLM_PROVIDER.get(provider_name, provider_name)
  url = with_param(
    with_param(LITELLM_CATALOG, "provider", name), "page_size", LITELLM_PAGE_SIZE
  )
  entries: dict[str, dict[str, Any]] = {}
  for page in range(1, MAX_PAGES + 1):
    payload = fetch(with_param(url, "page", page), {})
    for entry in payload.get("data") or []:
      if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        entries[entry["id"].removeprefix(f"{name}/")] = entry
    if payload.get("has_more") is not True:
      break
  logger.info("%s: read %d LiteLLM entries", name, len(entries))
  return entries


def modelschemas_entries(
  provider_name: str, fetch: Fetch = fetch_json
) -> dict[str, dict[str, Any]]:
  """Read one provider from the modelschemas service, keyed by the raw id of each row."""
  name = MODELSCHEMAS_PROVIDER.get(provider_name, provider_name)
  url = with_param(MODELSCHEMAS_URL, "provider", name)
  payload = fetch(url, {})
  entries: dict[str, dict[str, Any]] = {}
  for row in payload.get("models") or []:
    if isinstance(row, dict) and isinstance(row.get("rawId"), str) and row["rawId"]:
      entries[row["rawId"]] = row
  save_snapshot(url, payload)
  logger.info("%s: read %d modelschemas rows", name, len(entries))
  return entries


def modelschemas_columns(row: dict[str, Any]) -> dict[str, Any]:
  """The store columns of one modelschemas row: the reasoning flag and the effort ladder."""
  found: dict[str, Any] = {}
  reasoning = row.get("reasoning")
  if isinstance(reasoning, Mapping):
    found["supports_reasoning"] = True
    mandatory = reasoning.get("mandatory") is True
    if reasoning.get("mode") == "effort":
      listed = [name for name in reasoning.get("efforts") or () if name in EFFORT_RANKS]
      if mandatory:
        listed = [name for name in listed if name != "none"]
      if listed:
        found["supported_efforts"] = sorted(listed, key=EFFORT_RANKS.index)
    elif reasoning.get("mode") == "toggle":
      found["supported_efforts"] = ["max"] if mandatory else ["none", "max"]
  capabilities = row.get("capabilities")
  if isinstance(capabilities, Mapping):
    flag = capabilities.get("reasoning")
    if isinstance(flag, bool):
      found["supports_reasoning"] = flag
  return found


def column_values(values: dict[str, Any]) -> dict[str, Any]:
  """The stored columns that one config block sets, with `tools` as the tool flag."""
  found = {key: values[key] for key in COLUMNS if values.get(key) is not None}
  if values.get("tools") is not None:
    found["supports_function_calling"] = bool(values["tools"])
  return found


def config_params(provider: dict[str, Any], slug: str) -> dict[str, Any]:
  """The provider-level values, then every matching `models` entry, in file order."""
  found = column_values(provider)
  for pattern, values in (provider.get("models") or {}).items():
    if isinstance(values, dict) and matches(str(pattern), slug):
      found.update(column_values(values))
  return found


def supported_ladder(
  config: dict[str, Any], model: str, row: dict[str, Any]
) -> list[str] | None:
  """The resolved ladder the store keeps for a model, or None for a model that does not reason."""
  if not row.get("supports_reasoning"):
    return None
  return efforts(config, model, row.get("supported_efforts"))


def entries_or_reason(
  reader: Callable[[str, Fetch], dict[str, dict[str, Any]]], name: str, fetch: Fetch
) -> dict[str, dict[str, Any]] | str:
  """Read 1 source for 1 name, or the reason it failed."""
  try:
    return reader(name, fetch)
  except (httpx.HTTPError, ValueError) as error:
    return str(error)


def enrich(
  lines: Iterable[str],
  config: dict[str, Any],
  fetch: Fetch = fetch_json,
  native: dict[str, dict[str, Any]] | None = None,
  failed: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
  """Add metadata to each line: config, then provider columns, then the catalogs."""
  lines = list(lines)
  # 1 read per provider name per source, for example 1 for Kilo and OpenRouter alike.
  # The names come first, so that each source reads its providers at the same time.
  names: list[str] = []
  schema_names: list[str] = []
  for line in lines:
    provider_name = line.partition("/")[0]
    for known, keep in (
      (LITELLM_PROVIDER.get(provider_name, provider_name), names),
      (MODELSCHEMAS_PROVIDER.get(provider_name, provider_name), schema_names),
    ):
      if known not in keep:
        keep.append(known)
  cache: dict[str, dict[str, dict[str, Any]] | str] = read_each(
    names, lambda name: entries_or_reason(litellm_entries, name, fetch)
  )
  schemas: dict[str, dict[str, dict[str, Any]] | str] = read_each(
    schema_names, lambda name: entries_or_reason(modelschemas_entries, name, fetch)
  )
  noted: set[str] = set()
  noted_schemas: set[str] = set()
  rows, problems = [], []
  for line in lines:
    provider_name, _, slug = line.partition("/")
    name = LITELLM_PROVIDER.get(provider_name, provider_name)
    entries = cache[name]
    if isinstance(entries, str):
      if provider_name not in noted:
        noted.add(provider_name)
        problems.append(f"{provider_name}: LiteLLM catalog failed: {entries}")
        if failed is not None:
          failed.append(provider_name)
      entries = {}
    entry = entries.get(slug) or {}
    row: dict[str, Any] = {"id": line, "provider": provider_name, "slug": slug}
    row.update({key: entry.get(key) for key in COLUMNS})
    ms_name = MODELSCHEMAS_PROVIDER.get(provider_name, provider_name)
    ms = schemas[ms_name]
    if isinstance(ms, str):
      if provider_name not in noted_schemas:
        noted_schemas.add(provider_name)
        problems.append(f"{provider_name}: modelschemas source failed: {ms}")
        if failed is not None and provider_name not in failed:
          failed.append(provider_name)
    else:
      # The service fills only what the earlier sources left empty.
      for key, value in modelschemas_columns(ms.get(slug) or {}).items():
        if row.get(key) is None:
          row[key] = value
    row.update((native or {}).get(line, {}))
    block = block_for(config, provider_name, slug) or {}
    row.update(config_params(block, slug))
    row["supported_efforts"] = supported_ladder(config, line, row)
    row["tier"] = (
      claiming_tier(block, slug)
      if block and row.get("mode") in (None, "chat")
      else None
    )
    rows.append(row)
  return rows, problems
