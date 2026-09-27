"""Discover the models each provider serves and write them to models.txt."""

import argparse
import logging
import re
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from daedalus.config import STATE_DIR, get_config

logger = logging.getLogger("daedalus.catalog")

MODELS_TXT = STATE_DIR / "models.txt"
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
  query = urlencode(params, safe="=")
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


def discover_provider(
  provider_name: str,
  provider: dict[str, Any],
  fetch: Fetch = fetch_json,
) -> list[str]:
  """Read every page of one provider, and return its kept slugs."""
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
  full = merge_pages(pages)
  return select(provider, extract_slugs(full))


def build_catalog(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
) -> tuple[list[str], list[str]]:
  """Build the catalog, and return its lines with one reason per skipped provider."""
  providers = get_config() if config is None else config
  lines: list[str] = []
  skipped: list[str] = []
  for provider_name, provider in providers.items():
    if not isinstance(provider, dict):
      skipped.append(f"{provider_name}: not a mapping")
      continue
    if not (provider.get("api_key") or ""):
      skipped.append(f"{provider_name}: no api_key")
      continue
    try:
      slugs = discover_provider(provider_name, provider, fetch)
    except (httpx.HTTPError, ValueError) as error:
      skipped.append(f"{provider_name}: {error}")
      continue
    lines.extend(f"{provider_name}/{slug}" for slug in slugs)
  return lines, skipped


def read_models_txt() -> list[str]:
  """The lines the last catalog run wrote, or an empty list before the first run."""
  if not MODELS_TXT.exists():
    return []
  return [line for line in MODELS_TXT.read_text(encoding="utf-8").splitlines() if line]


def write_models_txt(lines: Iterable[str], path: Path | str = MODELS_TXT) -> Path:
  """Write one `provider/slug` per line."""
  target = Path(path)
  target.parent.mkdir(parents=True, exist_ok=True)
  with target.open("w", encoding="utf-8", newline="\n") as handle:
    handle.write("".join(f"{line}\n" for line in lines))
  return target


def main() -> int:
  argparse.ArgumentParser(
    prog="python -m daedalus.catalog",
    description="Discover provider models and write them to .daedalus-state/models.txt.",
  ).parse_args()
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
  lines, skipped = build_catalog()
  target = write_models_txt(lines)
  for reason in skipped:
    logger.warning("skipped %s", reason)
  providers = len({line.split("/", 1)[0] for line in lines})
  logger.info("wrote %d models from %d providers to %s", len(lines), providers, target)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
