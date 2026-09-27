"""Discover provider models, add LiteLLM catalog metadata, and store them."""

import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from daedalus.config import STATE_DIR, get_config
from daedalus.providers import settings

logger = logging.getLogger("daedalus.catalog")

MODELS_TSV = STATE_DIR / "models.tsv"
MODELS_DB = STATE_DIR / "models.sqlite3"
DUMP_DIR = STATE_DIR / "dump"
LITELLM_CATALOG = "https://api.litellm.ai/model_catalog"
LITELLM_PAGE_SIZE = 500
# Provider names that differ in the LiteLLM catalog. Kilo serves OpenRouter slugs.
LITELLM_PROVIDER = {"z-ai": "zai", "kilo": "openrouter"}
# Metadata columns, in table and models.tsv order. Config values win over the catalog.
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
TEXT_COLUMNS = frozenset({"mode", "reasoning_effort"})
# Rows that enter the chat chains. No catalog match gives no mode.
ROUTABLE_MODES = (None, "chat")
TIMEOUT_SECONDS = 60.0
MAX_PAGES = 50

# Row list key per response shape, and the slug key order for that shape.
ROW_KEYS = {
  "data": ("id", "name"),
  "models": ("name", "id"),
  "result": ("name", "id"),
}

# Gemini names its rows `models/{slug}`.
STRIPPED_SHAPE = "models"

# Providers that do not take a bearer key.
AUTH_HEADER = {"gemini": "x-goog-api-key"}

Fetch = Callable[[str, dict[str, str]], dict[str, Any]]


def matches(pattern: str, slug: str) -> bool:
  """Test one slug against one pattern."""
  if pattern.startswith("!"):
    return not matches(pattern[1:], slug)
  if pattern.startswith("^"):
    return re.search(pattern, slug) is not None
  if "*" in pattern or "?" in pattern:
    return fnmatchcase(slug, pattern)
  return slug == pattern


def any_match(patterns: Iterable[str], slug: str) -> bool:
  return any(matches(pattern, slug) for pattern in patterns)


def auth_headers(provider_name: str, provider: dict[str, Any]) -> dict[str, str]:
  """Build the auth header for one provider. No key gives no header."""
  api_key = provider.get("api_key") or ""
  if not api_key:
    return {}
  header = AUTH_HEADER.get(provider_name)
  if header is not None:
    return {header: api_key}
  return {"Authorization": f"Bearer {api_key}"}


def row_shape(payload: dict[str, Any]) -> str | None:
  """Name the response shape, or None when no row list is present."""
  for key in ROW_KEYS:
    if isinstance(payload.get(key), list):
      return key
  return None


def extract_slugs(payload: dict[str, Any]) -> list[str]:
  """Take the slug from each row of one payload."""
  shape = row_shape(payload)
  if shape is None:
    return []
  slugs: list[str] = []
  for row in payload[shape]:
    if not isinstance(row, dict):
      continue
    for key in ROW_KEYS[shape]:
      value = row.get(key)
      if isinstance(value, str) and value:
        slugs.append(
          value.removeprefix("models/") if shape == STRIPPED_SHAPE else value
        )
        break
  return slugs


def merge_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
  """Merge paged rows into one payload. Keeps the first page's other fields."""
  if not pages:
    return {}
  merged = dict(pages[0])
  shape = row_shape(merged)
  if shape is not None and len(pages) > 1:
    rows: list[Any] = []
    for page in pages:
      rows.extend(page.get(shape) or [])
    merged[shape] = rows
  merged.pop("nextPageToken", None)
  return merged


def failure(payload: dict[str, Any]) -> str | None:
  """Return the message of a failed envelope, so a bad URL is not an empty catalog."""
  if payload.get("success") is not False:
    return None
  for error in payload.get("errors") or []:
    if isinstance(error, dict) and error.get("message"):
      return str(error["message"])
  return "the upstream reported a failure"


def next_page_url(url: str, payload: dict[str, Any]) -> str | None:
  """Build the next page URL, or None when the page is the last one."""
  token = payload.get("nextPageToken")
  if isinstance(token, str) and token:
    return with_param(url, "pageToken", token)
  links = payload.get("links")
  if isinstance(links, dict) and isinstance(links.get("next"), str) and links["next"]:
    return links["next"]
  info = payload.get("result_info")
  if not isinstance(info, dict):
    return None
  page = info.get("page")
  if not isinstance(page, int):
    return None
  total_pages = info.get("total_pages")
  if isinstance(total_pages, int):
    return with_param(url, "page", page + 1) if page < total_pages else None
  # Cloudflare names a row total, not a page total.
  total_count = info.get("total_count")
  per_page = info.get("per_page")
  if isinstance(total_count, int) and isinstance(per_page, int) and per_page > 0:
    return with_param(url, "page", page + 1) if page * per_page < total_count else None
  return None


def with_param(url: str, key: str, value: str | int) -> str:
  """Add or replace one query parameter in a URL."""
  parts = urlsplit(url)
  params = [(name, text) for name, text in parse_qsl(parts.query) if name != key]
  params.append((key, str(value)))
  query = urlencode(params, safe="=", quote_via=quote)
  return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
  """Read one discovery page."""
  response = httpx.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
  response.raise_for_status()
  payload = response.json()
  return payload if isinstance(payload, dict) else {}


def declared_ids(provider: dict[str, Any]) -> list[str]:
  """Take the model ids that a models block names exactly."""
  keys = (provider.get("models") or {}).keys()
  return [
    key for key in keys if isinstance(key, str) and not any(c in key for c in "*?^")
  ]


def select(provider: dict[str, Any], slugs: Iterable[str]) -> list[str]:
  """Keep the slugs that exclude does not drop, plus every exact declared id."""
  exclude = provider.get("exclude") or []
  declared = list((provider.get("models") or {}).keys())
  kept = set(declared_ids(provider))
  kept.update(
    slug for slug in slugs if any_match(declared, slug) or not any_match(exclude, slug)
  )
  return sorted(kept)


def read_pages(
  provider_name: str, provider: dict[str, Any], fetch: Fetch = fetch_json
) -> dict[str, Any]:
  """Read every discovery page of one provider, and merge them into one payload."""
  url = provider.get("discovery_url")
  if not isinstance(url, str) or not url:
    raise ValueError("no discovery_url")
  headers = auth_headers(provider_name, provider)
  pages: list[dict[str, Any]] = []
  page = 0
  while url is not None and page < MAX_PAGES:
    payload = fetch(url, headers)
    problem = failure(payload)
    if problem is not None:
      raise ValueError(problem)
    pages.append(payload)
    url = next_page_url(url, payload)
    page += 1
  return merge_pages(pages)


def discover_provider(
  provider_name: str,
  provider: dict[str, Any],
  fetch: Fetch = fetch_json,
) -> list[str]:
  """Read every page of one provider, and return its kept slugs."""
  return select(provider, extract_slugs(read_pages(provider_name, provider, fetch)))


def read_providers(
  config: dict[str, Any] | None = None, fetch: Fetch = fetch_json
) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any]]], list[str]]:
  """Read each provider, and return its settings and payload, with one reason per skip."""
  providers = get_config() if config is None else config
  found: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
  skipped: list[str] = []
  for provider_name, provider in providers.items():
    if not isinstance(provider, dict):
      skipped.append(f"{provider_name}: not a mapping")
      continue
    provider = settings(provider_name, provider)
    if not (provider.get("api_key") or ""):
      skipped.append(f"{provider_name}: no api_key")
      continue
    try:
      payload = read_pages(provider_name, provider, fetch)
    except (httpx.HTTPError, ValueError) as error:
      skipped.append(f"{provider_name}: {error}")
      continue
    found.append((provider_name, provider, payload))
  return found, skipped


def build_catalog(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
) -> tuple[list[str], list[str]]:
  """Build the catalog, and return its lines with one reason per skipped provider."""
  found, skipped = read_providers(config, fetch)
  lines = [
    f"{provider_name}/{slug}"
    for provider_name, provider, payload in found
    for slug in select(provider, extract_slugs(payload))
  ]
  return lines, skipped


def dump(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
  folder: Path | str = DUMP_DIR,
) -> list[Path]:
  """Write each provider's raw discovery payload to one JSON file, before `exclude`."""
  target = Path(folder)
  target.mkdir(parents=True, exist_ok=True)
  for old in target.glob("*.json"):
    old.unlink()
  found, skipped = read_providers(config, fetch)
  paths = []
  for provider_name, _, payload in found:
    path = target / f"{provider_name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    paths.append(path)
  for reason in skipped:
    logger.warning("skipped %s", reason)
  logger.info("wrote %d provider catalogs to %s", len(paths), target)
  return paths


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


def config_params(provider: dict[str, Any], slug: str) -> dict[str, Any]:
  """Provider-level values, then every matching `models` entry, in config order."""
  found = {key: provider[key] for key in COLUMNS if provider.get(key) is not None}
  for pattern, values in (provider.get("models") or {}).items():
    if isinstance(values, dict) and matches(str(pattern), slug):
      found.update({key: values[key] for key in COLUMNS if values.get(key) is not None})
  return found


def enrich(
  lines: Iterable[str], config: dict[str, Any], fetch: Fetch = fetch_json
) -> tuple[list[dict[str, Any]], list[str]]:
  """Add catalog metadata and config values to each line, config first."""
  cache: dict[str, dict[str, dict[str, Any]]] = {}
  rows, problems = [], []
  for line in lines:
    provider_name, _, slug = line.partition("/")
    if provider_name not in cache:
      try:
        cache[provider_name] = litellm_entries(provider_name, fetch)
      except (httpx.HTTPError, ValueError) as error:
        problems.append(f"{provider_name}: LiteLLM catalog failed: {error}")
        cache[provider_name] = {}
    entry = cache[provider_name].get(slug) or {}
    row: dict[str, Any] = {"id": line, "provider": provider_name, "slug": slug}
    row.update({key: entry.get(key) for key in COLUMNS})
    provider = config.get(provider_name)
    row.update(config_params(provider if isinstance(provider, dict) else {}, slug))
    rows.append(row)
  return rows, problems


def text(value: Any) -> str:
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value).replace("\t", " ").replace("\n", " ")


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


def write_store(rows: Iterable[dict[str, Any]], path: Path | str | None = None) -> Path:
  """Replace the model table in one transaction-safe file swap."""
  target = Path(path or MODELS_DB)
  target.parent.mkdir(parents=True, exist_ok=True)
  temporary = target.with_suffix(".tmp")
  temporary.unlink(missing_ok=True)
  columns = ", ".join(
    f"{key} {'TEXT' if key in TEXT_COLUMNS else 'NUMERIC'}" for key in COLUMNS
  )
  with sqlite3.connect(temporary) as database:
    database.execute(
      f"CREATE TABLE models (id TEXT PRIMARY KEY, provider TEXT, slug TEXT, {columns})"
    )
    names = ("id", "provider", "slug", *COLUMNS)
    values = []
    for row in rows:
      provider, _, slug = row["id"].partition("/")
      values.append((row["id"], provider, slug, *(row.get(key) for key in COLUMNS)))
    marks = ", ".join("?" * len(names))
    database.executemany(
      f"INSERT INTO models ({', '.join(names)}) VALUES ({marks})", values
    )
  database.close()
  temporary.replace(target)
  return target


def read_models(routable_only: bool = True) -> list[str]:
  """The stored model ids, chat or unmatched rows only by default."""
  if not Path(MODELS_DB).exists():
    return []
  database = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
  try:
    rows = database.execute("SELECT id, mode FROM models ORDER BY rowid")
    return [key for key, mode in rows if not routable_only or mode in ROUTABLE_MODES]
  finally:
    database.close()


def refresh() -> Path:
  """Discover the provider models, and rewrite the SQLite store and models.tsv."""
  config = get_config()
  lines, skipped = build_catalog(config)
  rows, problems = enrich(lines, config)
  write_store(rows)
  target = write_models_tsv(rows)
  for reason in [*skipped, *problems]:
    logger.warning("skipped %s", reason)
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return target
