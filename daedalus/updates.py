"""The update check: whether a newer daedalus is out, from the version of this build."""

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any

import httpx

from daedalus import __version__, store
from daedalus.store.database import connect_read, open_db

logger = logging.getLogger("daedalus.updates")

# The repository the check reads when the settings name none.
DEFAULT_REPO = "nemoe7/daedalus"
# The idle between two checks, in hours. The schedule tick calls the check at most that often.
EVERY_HOURS = 6.0
TIMEOUT_SECONDS = 10.0
# A source build's version carries its commit: `dev-COMMIT`.
_DEV = re.compile(r"^dev-([0-9a-f]{7,40})$")
UPDATES_TABLE = (
  "CREATE TABLE IF NOT EXISTS updates "
  "(name TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)"
)


def channel(version: str) -> str:
  """release for a build from a v tag, dev for a build from the source."""
  return "release" if version.startswith("v") else "dev"


def repo() -> str:
  """The repository from the settings, over the default."""
  from daedalus.config import settings

  return settings.load().get("updates", {}).get("repo") or DEFAULT_REPO


def _fetch(url: str) -> Any:
  """The JSON of a GitHub API read."""
  response = httpx.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "daedalus"})
  response.raise_for_status()
  return response.json()


def _tag_rank(tag: str) -> tuple[int, ...]:
  """The number parts of a version tag, so v1.10.0 outranks v1.9.9."""
  found = tuple(int(part) for part in re.findall(r"\d+", tag.lstrip("v")))
  return found or (0,)


def check(
  repository: str, version: str, fetch: Callable[[str], Any] | None = None
) -> dict[str, Any]:
  """Ask the repository for the version that goes after this build's."""
  found: dict[str, Any] = {
    "repo": repository,
    "current": version,
    "channel": channel(version),
    "latest": None,
    "url": None,
    "behind": None,
    "update": False,
    "error": None,
  }
  fetch = fetch or _fetch
  try:
    if found["channel"] == "release":
      data = fetch(f"https://api.github.com/repos/{repository}/releases/latest")
      tag = str(data.get("tag_name", ""))
      if tag:
        found["latest"] = tag
        found["url"] = str(data.get("html_url") or "") or None
        found["update"] = _tag_rank(tag) > _tag_rank(version)
      else:
        found["error"] = "the repository has no release"
      return found
    match = _DEV.match(version)
    if match is None:
      found["error"] = "no commit in this build's version"
      return found
    base = match.group(1)
    head = str(
      fetch(f"https://api.github.com/repos/{repository}/commits/main").get("sha", "")
    )
    if not head or head.startswith(base):
      return found
    compare = fetch(
      f"https://api.github.com/repos/{repository}/compare/{base}...{head}"
    )
    found["behind"] = int(compare.get("behind_by", 0))
    found["update"] = found["behind"] > 0
  except (httpx.HTTPError, ValueError, AttributeError, KeyError) as exc:
    found["error"] = str(exc) or type(exc).__name__
  return found


def save(result: dict[str, Any]) -> None:
  """The last check in the store, for the next start and the tab."""
  database = open_db(store.MODELS_DB, (UPDATES_TABLE,))
  try:
    database.execute(
      "INSERT INTO updates (name, value, updated) VALUES ('check', ?, ?) "
      "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated = excluded.updated",
      (json.dumps(result), time.time()),
    )
    database.commit()
  finally:
    database.close()


def read() -> dict[str, Any] | None:
  """The last check of the store, with its time as `at`. None without a check yet."""
  if not store.MODELS_DB.exists():
    return None
  database = connect_read(store.MODELS_DB)
  try:
    row = database.execute(
      "SELECT value, updated FROM updates WHERE name = 'check'"
    ).fetchone()
  except sqlite3.OperationalError:
    return None
  finally:
    database.close()
  if row is None:
    return None
  found = json.loads(row[0])
  found["at"] = row[1]
  return found


def last() -> float | None:
  """The time of the last check. None without a check yet."""
  got = read()
  return got["at"] if got else None


def due(now: float, last_check: float | None) -> bool:
  """When the check asks again: without a check, or after the period."""
  return last_check is None or now - last_check >= EVERY_HOURS * 3600


def tick(now: float) -> dict[str, Any] | None:
  """The check when it is due, written to the store. None while it is not due."""
  if not due(now, last()):
    return None
  result = check(repo(), __version__)
  save(result)
  return result


def check_now() -> dict[str, Any]:
  """A forced check, for the check button of the tab, written to the store."""
  save(check(repo(), __version__))
  return read()
