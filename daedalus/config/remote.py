"""Fetch the hook files of `remote_hooks` and keep the last verified copy.

The operator names each file with a URL and the sha256 of its bytes. A boot fetches an entry only
when the copy in `config/hooks` does not match the pin, so a restart with no network keeps serving.
A failed fetch or a body that misses the pin leaves the last good file and writes 1 error line.

The pin lives in the settings file, never in the fetched file: a file cannot vouch for itself.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("daedalus.config")

FOLDER: Final = Path("config/hooks")
TIMEOUT: Final = 30.0
# A file name of the folder: no separator, so a URL never names a file outside `config/hooks`.
NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
# The problems that are already in the log, so that each one shows 1 time.
_told: set[str] = set()


def tell(problem: str) -> None:
  """Log a problem 1 time."""
  if problem not in _told:
    _told.add(problem)
    logger.error("%s", problem)


def digest(body: bytes) -> str:
  """The sha256 of the bytes, as the settings file writes it."""
  return hashlib.sha256(body).hexdigest()


def file_name(entry: Mapping[str, Any]) -> str:
  """The file name of 1 entry: its `name`, else the last part of the URL path."""
  found = entry.get("name")
  if isinstance(found, str) and found.strip():
    return found.strip()
  return posixpath.basename(urlparse(str(entry.get("url", ""))).path)


def path_of(entry: Mapping[str, Any], folder: Path) -> Path | None:
  """The `config/hooks` path of 1 entry, or None when the name is not a plain file name."""
  name = file_name(entry)
  if not NAME.fullmatch(name):
    tell(f"remote hook {entry.get('url')}: {name!r} is not a file name")
    return None
  return folder / name


def fetch(url: str, timeout: float = TIMEOUT) -> bytes | None:
  """The bytes of 1 URL. A network or status error logs and gives None."""
  try:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
  except httpx.HTTPError as exc:
    tell(f"remote hook {url} did not load: {exc}; the last copy stays")
    return None
  return response.content


def write(path: Path, body: bytes) -> None:
  """Write the body through a temporary name, so a reader never sees half a file."""
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_name(f".{path.name}.part")
  try:
    temporary.write_bytes(body)
    temporary.replace(path)
  except OSError as exc:
    tell(f"remote hook {path} did not write: {exc}")


def sync(entries: Iterable[Any], folder: Path | None = None) -> list[str]:
  """Bring each remote hook file up to date, and return the names that moved."""
  target = FOLDER if folder is None else folder
  moved: list[str] = []
  for entry in entries:
    if not isinstance(entry, Mapping):
      continue
    path = path_of(entry, target)
    if path is None:
      continue
    pin = str(entry.get("sha256", "")).lower()
    if path.is_file() and digest(path.read_bytes()) == pin:
      continue  # the copy already holds the pinned bytes
    body = fetch(str(entry.get("url", "")))
    if body is None:
      continue
    found = digest(body)
    if found != pin:
      tell(
        f"remote hook {entry.get('url')} does not match its sha256 "
        f"({found} != {pin}); the last copy stays"
      )
      continue
    write(path, body)
    moved.append(path.name)
    logger.info("remote hook %s wrote %s", entry.get("url"), path)
  return moved
