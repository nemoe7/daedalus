"""Add LiteLLM catalog metadata and config values to each discovered model."""

from collections.abc import Iterable
from typing import Any

import httpx

from daedalus.discovery import MAX_PAGES, Fetch, fetch_json, matches, with_param
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
  """Provider-level values, then every matching `models` entry, in config order."""
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
  cache: dict[str, dict[str, dict[str, Any]]] = {}
  rows, problems = [], []
  for line in lines:
    provider_name, _, slug = line.partition("/")
    if provider_name not in cache:
      try:
        cache[provider_name] = litellm_entries(provider_name, fetch)
      except (httpx.HTTPError, ValueError) as error:
        problems.append(f"{provider_name}: LiteLLM catalog failed: {error}")
        if failed is not None:
          failed.append(provider_name)
        cache[provider_name] = {}
    entry = cache[provider_name].get(slug) or {}
    row: dict[str, Any] = {"id": line, "provider": provider_name, "slug": slug}
    row.update({key: entry.get(key) for key in COLUMNS})
    row.update((native or {}).get(line, {}))
    provider = config.get(provider_name)
    row.update(config_params(provider if isinstance(provider, dict) else {}, slug))
    rows.append(row)
  return rows, problems
