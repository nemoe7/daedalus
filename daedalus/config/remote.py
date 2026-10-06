"""Fetch the hook files of `remote_hooks` and keep the last verified copy.

The operator names each file with a URL and the sha256 of its bytes. A boot fetches an entry only
when the copy in `config/hooks` does not match the pin, so a restart with no network keeps serving.
A failed fetch or a body that misses the pin leaves the last good file and writes 1 error line.

The pin lives in the settings file, never in the fetched file: a file cannot vouch for itself.
The `remote_hook_hosts` group limits the hosts that a URL may name. `daedalus hooks pin` records
the digest of each local hook file in `config/hooks.lock.json`, and `daedalus hooks verify`
compares the files on disk against that lock and against the pins of the settings file.
"""

from __future__ import annotations

import hashlib
import json
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
# The digests of the local hook files, beside the folder so that the loader never reads them.
LOCK: Final = Path("config/hooks.lock.json")
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


def host_of(url: str) -> str:
  """The host of a URL, in lowercase, without a port and without a trailing dot."""
  return (urlparse(url).hostname or "").rstrip(".").lower()


def allowed(url: str, hosts: Iterable[Any]) -> bool:
  """True when the URL host passes the allowlist. An empty allowlist passes every host."""
  listed = [
    str(host).strip().lower().rstrip(".") for host in hosts if str(host).strip()
  ]
  if not listed:
    return True
  host = host_of(url)
  for pattern in listed:
    if pattern.startswith("*."):
      parent = pattern[2:]
      if host == parent or host.endswith("." + parent):
        return True
    elif host == pattern:
      return True
  return False


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


def sync(
  entries: Iterable[Any], folder: Path | None = None, hosts: Iterable[Any] = ()
) -> list[str]:
  """Bring each remote hook file up to date, and return the names that moved.

  `hosts` is the allowlist of `remote_hook_hosts`. An entry from another host never goes to
  the network, and its last copy stays.
  """
  target = FOLDER if folder is None else folder
  moved: list[str] = []
  for entry in entries:
    if not isinstance(entry, Mapping):
      continue
    path = path_of(entry, target)
    if path is None:
      continue
    url = str(entry.get("url", ""))
    if not allowed(url, hosts):
      tell(
        f"remote hook {url}: {host_of(url)!r} is not in remote_hook_hosts; the last copy stays"
      )
      continue
    pin = str(entry.get("sha256", "")).lower()
    if path.is_file() and digest(path.read_bytes()) == pin:
      continue  # the copy already holds the pinned bytes
    body = fetch(url)
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


def compile_check(entries: Iterable[Any], folder: Path | None = None) -> list[str]:
  """Warn 1 line for each remote hook file that does not compile, and return their names.

  The check reads the source and runs none of it, so a body that a truncated download or a bad
  edit broke is caught at the start, without the risk of the file itself. It warns only: the
  file stays, and daedalus starts.
  """
  target = FOLDER if folder is None else folder
  bad: list[str] = []
  for entry in entries:
    if not isinstance(entry, Mapping):
      continue
    name = file_name(entry)
    if not NAME.fullmatch(name):
      continue
    path = target / name
    try:
      source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
      logger.warning("remote hook %s did not read: %s", path, exc)
      bad.append(name)
      continue
    try:
      compile(source, str(path), "exec")
    except (SyntaxError, ValueError) as exc:
      logger.warning("remote hook %s does not compile: %s", path, exc)
      bad.append(name)
  return bad


def on_disk(folder: Path | None = None) -> dict[str, str]:
  """The sha256 of each hook file in the folder, by name, in name order.

  A name that starts with a dot stays out: the writer of a download uses such a name.
  """
  target = FOLDER if folder is None else folder
  if not target.is_dir():
    return {}
  return {
    path.name: digest(path.read_bytes())
    for path in sorted(target.iterdir())
    if path.is_file() and not path.name.startswith(".")
  }


def read_lock(path: Path | None = None) -> dict[str, str]:
  """The recorded digests of `config/hooks.lock.json`. A missing or a broken file gives {}."""
  target = LOCK if path is None else path
  try:
    found = json.loads(target.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  if not isinstance(found, dict):
    return {}
  return {
    str(name): str(pin).lower()
    for name, pin in found.items()
    if isinstance(pin, str) and re.fullmatch(r"[0-9a-fA-F]{64}", pin)
  }


def write_lock(pins: Mapping[str, str], path: Path | None = None) -> None:
  """Write the lock file, in name order, so that a change reads as 1 line of a diff."""
  target = LOCK if path is None else path
  target.parent.mkdir(parents=True, exist_ok=True)
  body = {name: pins[name] for name in sorted(pins)}
  target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
