"""Read the model lists of the providers and keep the models that the config selects."""

import json
import logging
import re
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from daedalus.config import STATE_DIR, file_block, file_takes, get_config, main_block
from daedalus.providers import PROVIDERS, OpenAIProvider, settings
from daedalus.store import COLUMNS

logger = logging.getLogger("daedalus.catalog")


DUMP_DIR = STATE_DIR / "dump"


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


Fetch = Callable[[str, dict[str, str]], dict[str, Any]]


def text(value: Any) -> str:
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value).replace("\t", " ").replace("\n", " ")


def matches(pattern: str, slug: str) -> bool:
  """Test one slug against one pattern."""
  if pattern.startswith("!"):
    return not matches(pattern[1:], slug)
  if pattern.startswith("^"):
    return re.search(pattern, slug) is not None
  if "*" in pattern or "?" in pattern:
    return fnmatchcase(slug, pattern)
  return slug == pattern


def specificity(pattern: str) -> tuple[int, int]:
  """Rank a pattern: exact names first, then globs, then regexes, then negations."""
  if pattern.startswith("!"):
    return (0, 0)
  if pattern.startswith("^"):
    return (1, len(pattern))
  if "*" in pattern or "?" in pattern:
    return (2, len(pattern.replace("*", "").replace("?", "")))
  return (3, len(pattern))


def any_match(patterns: Iterable[str], slug: str) -> bool:
  return any(matches(pattern, slug) for pattern in patterns)


def auth_headers(provider_name: str, provider: dict[str, Any]) -> dict[str, str]:
  """Build the auth header for one provider. No key gives no header."""
  api_key = provider.get("api_key") or ""
  if not api_key:
    return {}
  return PROVIDERS.get(provider_name, OpenAIProvider).auth(api_key)


def row_shape(payload: dict[str, Any]) -> str | None:
  """Name the response shape, or None when no row list is present."""
  for key in ROW_KEYS:
    if isinstance(payload.get(key), list):
      return key
  return None


def row_matches(row: dict[str, Any], match: dict[str, Any]) -> bool:
  """Test one discovered row against `discovery_match`. A missing property matches."""
  properties = {
    item.get("property_id"): item.get("value")
    for item in row.get("properties") or []
    if isinstance(item, dict)
  }
  for key, expected in match.items():
    value = row.get(key, properties.get(key))
    if value is not None and text(value).lower() != text(expected).lower():
      return False
  return True


def extract_rows(
  payload: dict[str, Any], match: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
  """Map the slug of each row that `match` keeps to that row."""
  shape = row_shape(payload)
  if shape is None:
    return {}
  rows: dict[str, dict[str, Any]] = {}
  for row in payload[shape]:
    if not isinstance(row, dict) or not row_matches(row, match or {}):
      continue
    for key in ROW_KEYS[shape]:
      value = row.get(key)
      if isinstance(value, str) and value:
        slug = value.removeprefix("models/") if shape == STRIPPED_SHAPE else value
        rows.setdefault(slug, row)
        break
  return rows


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


def provider_rows(
  provider_name: str, provider: dict[str, Any], payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
  """The rows of one payload that `discovery_match` and the provider class keep."""
  rows = extract_rows(payload, provider.get("discovery_match"))
  kind = PROVIDERS.get(provider_name, OpenAIProvider)
  return {slug: row for slug, row in rows.items() if kind.discoverable(row)}


def read_providers(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
  failed: list[str] | None = None,
) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any], bool]], list[str]]:
  """Read each provider, and return its settings and payload, with one reason per skip."""
  providers = get_config() if config is None else config
  found: list[tuple[str, dict[str, Any], dict[str, Any], bool]] = []
  skipped: list[str] = []
  downloads: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
  for provider_name, raw in providers.items():
    if not isinstance(raw, dict):
      skipped.append(f"{provider_name}: not a mapping")
      continue
    blocks = [(settings(provider_name, main_block(raw) or {}), False)]
    file = file_block(raw)
    if file is not None:
      # A provider file discovers with its own settings, and takes its own models.
      blocks.append((settings(provider_name, file), True))
    for provider, is_file in blocks:
      if not (provider.get("api_key") or ""):
        skipped.append(f"{provider_name}: no api_key")
        continue
      if not provider.get("discovery_url"):
        skipped.append(f"{provider_name}: no discovery_url")
        continue
      # A provider file with the same URL and key as its main block reads the list 1 time.
      source = (
        provider["discovery_url"],
        tuple(sorted(auth_headers(provider_name, provider).items())),
      )
      try:
        payload = downloads.get(source) or read_pages(provider_name, provider, fetch)
      except (httpx.HTTPError, ValueError) as error:
        skipped.append(f"{provider_name}: {error}")
        if failed is not None:
          failed.append(provider_name)
        continue
      downloads[source] = payload
      found.append((provider_name, provider, payload, is_file))
  return found, skipped


def native_columns(provider_name: str, row: dict[str, Any]) -> dict[str, Any]:
  """The known, non-empty store columns that one provider row gives."""
  values = PROVIDERS.get(provider_name, OpenAIProvider).columns(row)
  return {
    key: value for key, value in values.items() if key in COLUMNS and value is not None
  }


def build_rows(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
  failed: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
  """Map each kept catalog line to its provider columns, with one reason per skip."""
  found, skipped = read_providers(config, fetch, failed)
  lines: dict[str, dict[str, Any]] = {}
  for provider_name, provider, payload, is_file in found:
    rows = provider_rows(provider_name, provider, payload)
    slugs = (
      [slug for slug in select(provider, list(rows)) if file_takes(provider, slug)]
      if is_file
      else select(provider, list(rows))
    )
    for slug in slugs:
      lines[f"{provider_name}/{slug}"] = native_columns(
        provider_name, rows.get(slug, {})
      )
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
  written: list[dict[str, Any]] = []
  for provider_name, _, payload, is_file in found:
    if any(payload is seen for seen in written):
      continue
    written.append(payload)
    path = target / f"{provider_name}{'-file' if is_file else ''}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    paths.append(path)
  for reason in skipped:
    logger.warning("skipped %s", reason)
  logger.info("wrote %d provider catalogs to %s", len(paths), target)
  return paths
