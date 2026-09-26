"""Model catalog from the provider config.

For each provider in the config: read its `discovery_url`, merge_pages the pages, write the
full response to `{provider}.yml`, keep the rows the config patterns allow, and write the
kept `provider/slug` lines to `models.txt`.

Pattern rules, in `matches`: exact, `*` and `?` are glob, `^` at the head is regex.
A pattern matches the slug alone: the block key already supplies the provider, so a
`provider/` head on a pattern is dropped before the match.
"""

import logging
import re
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import yaml

from daedalus.config import get_config

logger = logging.getLogger("daedalus.catalog")

MODELS_TXT = Path("models.txt")
PROVIDER_DIR = Path("config/providers")
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
  """Match one config pattern against one slug."""
  if pattern.startswith("^"):
    return re.search(pattern, slug) is not None
  if "*" in pattern or "?" in pattern:
    return fnmatchcase(slug, pattern)
  return slug == pattern


def any_match(patterns: Iterable[str], slug: str) -> bool:
  return any(matches(pattern, slug) for pattern in patterns)


def strip_provider_head(pattern: str, provider_name: str) -> str:
  """Drop a `provider/` head. The block key already names the provider."""
  return pattern.removeprefix(f"{provider_name}/")


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
  """Take the slug from each row of one payload.

  `data[].id`: groq, kilo, mistral, openrouter, zai. `models[].name` without the
  `models/` head: gemini. `result[].name`: cloudflare, where `id` is a UUID.
  """
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
  """Set one query parameter, and keep the others.

  `safe="="` keeps a base64 page token verbatim: gemini's `nextPageToken` ends with `=`.
  """
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


def select(
  provider_name: str,
  provider: dict[str, Any],
  slugs: Iterable[str],
) -> list[str]:
  """Keep the slugs that `exclude` does not drop.

  An excluded slug stays excluded: `tier` is routing metadata, and the catalog does not
  read it.
  """
  exclude = [
    strip_provider_head(pattern, provider_name)
    for pattern in provider.get("exclude") or []
  ]
  return sorted({slug for slug in slugs if not any_match(exclude, slug)})


def discover_provider(
  provider_name: str,
  provider: dict[str, Any],
  fetch: Fetch = fetch_json,
) -> tuple[list[str], dict[str, Any]]:
  """Read every page of one provider. Returns kept slugs and the merged payload."""
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
  return select(provider_name, provider, extract_slugs(full)), full


def build_catalog(
  config: dict[str, Any] | None = None,
  fetch: Fetch = fetch_json,
) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
  """Build the catalog.

  Returns the `provider/slug` lines, the merged payload per provider, and one reason
  per skipped provider.
  """
  providers = get_config() if config is None else config
  lines: list[str] = []
  payloads: dict[str, dict[str, Any]] = {}
  skipped: list[str] = []
  for provider_name, provider in providers.items():
    if not isinstance(provider, dict):
      skipped.append(f"{provider_name}: not a mapping")
      continue
    if not (provider.get("api_key") or ""):
      skipped.append(f"{provider_name}: no api_key")
      continue
    try:
      slugs, full = discover_provider(provider_name, provider, fetch)
    except (httpx.HTTPError, ValueError) as error:
      skipped.append(f"{provider_name}: {error}")
      continue
    payloads[provider_name] = full
    lines.extend(f"{provider_name}/{slug}" for slug in slugs)
  return lines, payloads, skipped


def write_models_txt(lines: Iterable[str], path: Path | str = MODELS_TXT) -> Path:
  """Write one `provider/slug` per line."""
  target = Path(path)
  target.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
  return target


def write_provider_yml(
  provider_name: str,
  payload: dict[str, Any],
  directory: Path | str = PROVIDER_DIR,
) -> Path:
  """Write one provider's merged response as YAML."""
  target = Path(directory) / f"{provider_name}.yml"
  target.write_text(
    yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
  )
  return target


def main() -> int:
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
  lines, payloads, skipped = build_catalog()
  target = write_models_txt(lines)
  written = [write_provider_yml(name, payload) for name, payload in payloads.items()]
  for reason in skipped:
    logger.warning("skipped %s", reason)
  print(f"wrote {len(lines)} models to {target}")
  for path in written:
    print(f"wrote {path}")
  if skipped:
    print(f"skipped {len(skipped)}: " + "; ".join(skipped))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
