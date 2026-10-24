"""Add the catalog metadata and config values to each discovered model."""

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from daedalus.catalog.discovery import (
  DUMP_DIR,
  MAX_PAGES,
  Fetch,
  cached_snapshot_record,
  dump_record,
  fetch_json,
  is_enrichment_stem,
  matches,
  merge_pages,
  read_each,
  save_snapshot,
  with_param,
  write_csv,
)
from daedalus.config import block_for, get_config
from daedalus.providers.base import clean_display_name, humanize_slug
from daedalus.routing.router import EFFORT_RANKS, claiming_tier, efforts
from daedalus.store import COLUMNS

logger = logging.getLogger("daedalus.catalog")

LITELLM_CATALOG = (
  "https://raw.githubusercontent.com/BerriAI/litellm/main/"
  "model_prices_and_context_window.json"
)


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


class SourceShapeError(TypeError):
  pass


def litellm_url(provider_name: str) -> str:
  """The base LiteLLM catalog URL for one provider."""
  name = LITELLM_PROVIDER.get(provider_name, provider_name)
  return with_param(
    with_param(LITELLM_CATALOG, "provider", name), "page_size", LITELLM_PAGE_SIZE
  )


def modelschemas_url(provider_name: str) -> str:
  """The modelschemas catalog URL for one provider."""
  name = MODELSCHEMAS_PROVIDER.get(provider_name, provider_name)
  return with_param(MODELSCHEMAS_URL, "provider", name)


def litellm_entries(
  provider_name: str, fetch: Fetch = fetch_json
) -> dict[str, dict[str, Any]]:
  """Read one provider from the LiteLLM catalog, page by page, keyed by slug."""
  name = LITELLM_PROVIDER.get(provider_name, provider_name)
  url = litellm_url(provider_name)
  entries: dict[str, dict[str, Any]] = {}
  pages: list[dict[str, Any]] = []
  for page in range(1, MAX_PAGES + 1):
    payload = fetch(with_param(url, "page", page), {})
    if "data" in payload or not payload:
      data = payload.get("data", [])
      if not isinstance(data, list):
        raise SourceShapeError("LiteLLM data must be a list")
      pages.append(payload)
      for entry in data:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
          entries[entry["id"].removeprefix(f"{name}/")] = entry
      if payload.get("has_more") is not True:
        break
      continue
    rows: list[dict[str, Any]] = []
    for model_id, entry in payload.items():
      if model_id == "sample_spec":
        continue
      if not isinstance(entry, dict):
        raise SourceShapeError("LiteLLM data must be a list")
      if (
        isinstance(model_id, str)
        and model_id
        and (entry.get("litellm_provider") == name or model_id.startswith(f"{name}/"))
      ):
        row = {"id": model_id, **entry}
        rows.append(row)
        entries[model_id.removeprefix(f"{name}/")] = row
    pages.append({"data": rows})
    break
  merged = merge_pages(pages)
  merged.pop("has_more", None)
  save_snapshot(url, merged)
  logger.info("%s: read %d LiteLLM entries", name, len(entries))
  return entries


def modelschemas_entries(
  provider_name: str, fetch: Fetch = fetch_json
) -> dict[str, dict[str, Any]]:
  """Read one provider from the modelschemas service, keyed by the raw id of each row."""
  name = MODELSCHEMAS_PROVIDER.get(provider_name, provider_name)
  url = modelschemas_url(provider_name)
  payload = fetch(url, {})
  entries: dict[str, dict[str, Any]] = {}
  models = payload.get("models", [])
  if not isinstance(models, list):
    raise SourceShapeError("modelschemas models must be a list")
  for row in models:
    if isinstance(row, dict) and isinstance(row.get("rawId"), str) and row["rawId"]:
      entries[row["rawId"]] = row
  save_snapshot(url, payload)
  logger.info("%s: read %d modelschemas rows", name, len(entries))
  return entries


def _enrichment_rows(
  prefix: str, provider_name: str, payload: dict[str, Any]
) -> list[dict[str, Any]]:
  """Extract CSV rows from one cached enrichment payload."""
  if prefix == "litellm":
    name = LITELLM_PROVIDER.get(provider_name, provider_name)
    data = payload.get("data")
    if not isinstance(data, list):
      return []
    return [
      {**row, "id": row["id"].removeprefix(f"{name}/")}
      for row in data
      if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
    ]
  models = payload.get("models")
  if not isinstance(models, list):
    return []
  return [
    {**row, "id": str(row.get("rawId") or row.get("id"))}
    for row in models
    if isinstance(row, dict)
    and isinstance(row.get("rawId") or row.get("id"), str)
    and (row.get("rawId") or row.get("id"))
  ]


def dump(
  config: dict[str, Any] | None = None,
  folder: Path | str = DUMP_DIR,
  file_format: str = "json",
  since: float | None = None,
) -> list[Path]:
  """Write cached enrichment snapshots to `litellm-{provider}.{ext}` and `modelschemas-{provider}.{ext}`."""
  if file_format not in {"json", "csv"}:
    raise ValueError("file_format must be json or csv")
  target = Path(folder)
  providers = get_config() if config is None else config
  names = [name for name, raw in providers.items() if isinstance(raw, dict)]
  if any(
    not name
    or Path(name).name != name
    or (target / f"litellm-{name}.{file_format}").parent != target
    for name in names
  ):
    raise ValueError("a provider name is not an export basename")
  target.mkdir(parents=True, exist_ok=True)
  for old in target.iterdir():
    if (
      old.is_file() and old.suffix in {".json", ".csv"} and is_enrichment_stem(old.stem)
    ):
      old.unlink()
  paths: list[Path] = []
  for provider_name in names:
    for prefix, url in (
      ("litellm", litellm_url(provider_name)),
      ("modelschemas", modelschemas_url(provider_name)),
    ):
      record = cached_snapshot_record(url)
      if record is None:
        logger.warning(
          "skipped %s-%s: no cached enrichment snapshot", prefix, provider_name
        )
        continue
      payload, fetched_at = record
      if since is not None and fetched_at < since:
        raise ValueError("a selected enrichment snapshot was not refreshed")
      path = target / f"{prefix}-{provider_name}.{file_format}"
      if file_format == "json":
        wrapped = dump_record(payload, "fetched_at", fetched_at)
        path.write_text(
          json.dumps(wrapped, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )
      else:
        write_csv(path, _enrichment_rows(prefix, provider_name, payload))
      paths.append(path)
  if since is not None and not paths:
    raise ValueError("no enrichment snapshot was refreshed")
  logger.info("wrote %d enrichment dumps to %s", len(paths), target)
  return paths


def modelschemas_columns(row: dict[str, Any], slug: str = "") -> dict[str, Any]:
  """The store columns of one modelschemas row: display name, reasoning flag, and effort ladder."""
  found: dict[str, Any] = {}
  name = clean_display_name(row.get("displayName"), slug or str(row.get("rawId") or ""))
  if name:
    found["name"] = name
  reasoning = row.get("reasoning")
  if isinstance(reasoning, Mapping):
    found["supports_reasoning"] = True
    mandatory = reasoning.get("mandatory") is True
    if reasoning.get("mode") == "effort":
      values = reasoning.get("efforts")
      listed = (
        [name for name in values if name in EFFORT_RANKS]
        if isinstance(values, list)
        else []
      )
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
  found = {k: v for k, v in column_values(provider).items() if k != "name"}
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
  except (httpx.HTTPError, ValueError, SourceShapeError) as error:
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
    unread = isinstance(entries, str)
    if unread:
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
      for key, value in modelschemas_columns(ms.get(slug) or {}, slug).items():
        if row.get(key) is None:
          row[key] = value
    row.update((native or {}).get(line, {}))
    block = block_for(config, provider_name, slug) or {}
    row.update(config_params(block, slug))
    row["name"] = row.get("name") or humanize_slug(slug)
    # No catalog match gives no mode. The row makes text, so it takes the chat mode. A provider
    # whose catalog read failed keeps no mode, so the store keeps the mode it holds.
    if not unread:
      row["mode"] = row["mode"] or "chat"
    row["supported_efforts"] = supported_ladder(config, line, row)
    row["tier"] = (
      claiming_tier(block, slug)
      if block and row.get("mode") in (None, "chat")
      else None
    )
    rows.append(row)
  return rows, problems
