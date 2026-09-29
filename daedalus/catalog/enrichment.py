"""Add LiteLLM catalog metadata and config values to each discovered model."""

from collections.abc import Iterable
from typing import Any

import httpx

from daedalus.catalog.discovery import MAX_PAGES, Fetch, fetch_json, matches, with_param
from daedalus.config import block_for
from daedalus.store import COLUMNS

LITELLM_CATALOG = "https://api.litellm.ai/model_catalog"


LITELLM_PAGE_SIZE = 500


# Provider names that differ in the LiteLLM catalog. Kilo serves OpenRouter slugs.
LITELLM_PROVIDER = {"z-ai": "zai", "kilo": "openrouter"}


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
  return entries


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


def enrich(
  lines: Iterable[str],
  config: dict[str, Any],
  fetch: Fetch = fetch_json,
  native: dict[str, dict[str, Any]] | None = None,
  failed: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
  """Add metadata to each line: config, then provider columns, then the LiteLLM catalog."""
  # 1 read per LiteLLM provider name, for example 1 for Kilo and OpenRouter.
  cache: dict[str, dict[str, dict[str, Any]] | str] = {}
  noted: set[str] = set()
  rows, problems = [], []
  for line in lines:
    provider_name, _, slug = line.partition("/")
    name = LITELLM_PROVIDER.get(provider_name, provider_name)
    if name not in cache:
      try:
        cache[name] = litellm_entries(provider_name, fetch)
      except (httpx.HTTPError, ValueError) as error:
        cache[name] = str(error)
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
    row.update((native or {}).get(line, {}))
    block = block_for(config, provider_name, slug) or {}
    row.update(config_params(block, slug))
    rows.append(row)
  return rows, problems
