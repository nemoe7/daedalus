"""The default provider file of the repository, filled in for the names that the local file lacks."""

import logging
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger("daedalus.config")

# The shipped provider file on `main`. The fetch never writes the local file of the operator.
URL = "https://raw.githubusercontent.com/nemoe7/daedalus/main/config/providers/free.yml"
PATH = Path(__file__).resolve().parents[2] / ".daedalus-state" / "free.defaults.yml"
TIMEOUT = 10.0


def fetch(url: str = URL, timeout: float = TIMEOUT) -> str:
  """The bytes of the default file. A network or status error raises."""
  response = httpx.get(url, timeout=timeout, follow_redirects=True)
  response.raise_for_status()
  return response.text


def parse(text: str) -> dict[str, Any]:
  """The provider blocks of the text, with `env:` and `db:` values resolved."""
  from daedalus.config import expand

  raw = yaml.safe_load(text)
  return expand(raw) if isinstance(raw, dict) else {}


def refresh() -> dict[str, Any]:
  """Fetch the default file into the cache and return it. A failure logs 1 line and gives the cache."""
  try:
    blocks = parse(fetch())
  except (httpx.HTTPError, yaml.YAMLError) as exc:
    logger.warning("the default provider file did not load: %s", exc)
    return cached()
  if not blocks:
    logger.warning("the default provider file holds no block")
    return cached()
  try:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(yaml.safe_dump(blocks, sort_keys=False), encoding="utf-8")
  except OSError as exc:
    logger.warning("the default provider cache did not write: %s", exc)
  return blocks


def cached() -> dict[str, Any]:
  """The default blocks of the last good fetch, or an empty mapping."""
  try:
    text = PATH.read_text(encoding="utf-8")
  except OSError:
    return {}
  raw = yaml.safe_load(text)
  return raw if isinstance(raw, dict) else {}


def fill(
  loaded: dict[str, Any], defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
  """Add each default provider block that `loaded` lacks, in place. A local name wins whole."""
  for name, block in (defaults if defaults is not None else cached()).items():
    if name not in loaded and isinstance(block, dict):
      loaded[name] = block
  return loaded
