"""Fetch the hook files of a GitHub source, and record what each installed file holds.

A source of the `hooks.sources` group names a repo, a folder inside it and a ref. `update` reads
the commit of the ref from the GitHub API, reads the archive of that commit, and writes the `.py`
files of the folder through a temporary name. A failed fetch keeps the files on disk. The same
holds for an archive that does not read, a block the reader refuses or a write that fails.

The lock in `hooks.lock.json` records the sha256, the version, the repo and the commit of
each file. `daedalus hooks verify` compares the files on disk against that lock.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import posixpath
import re
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import httpx

from daedalus.providers.hooks import meta_check

logger = logging.getLogger("daedalus.config")

# The default folder of the hook files, when a caller names none, and the record of each file.
FOLDER: Final = Path("hooks")
LOCK: Final = Path("hooks.lock.json")
TIMEOUT: Final = 30.0
# The 2 answers of a GitHub source: the commit of a ref, and the archive of a commit.
API: Final = "https://api.github.com/repos/{repo}/commits/{ref}"
ARCHIVE: Final = "https://codeload.github.com/{repo}/tar.gz/{commit}"
# The longest owner or repo name that a source may hold, and the name of a hook file in an archive.
REPO_PART: Final = re.compile(r"[A-Za-z0-9._-]{1,100}")
PY: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.py")
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


def fetch(url: str, timeout: float = TIMEOUT) -> bytes | None:
  """The bytes of 1 URL. A network or status error logs and gives None."""
  try:
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
  except httpx.HTTPError as exc:
    tell(f"remote hook {url} did not load: {exc}; the last copy stays")
    return None
  return response.content


def write(path: Path, body: bytes) -> bool:
  """Write the body through a temporary name, so a reader never sees half a file.

  False names a write that failed, so a caller never records a file that did not land.
  """
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_name(f".{path.name}.part")
  try:
    temporary.write_bytes(body)
    temporary.replace(path)
  except OSError as exc:
    tell(f"remote hook {path} did not write: {exc}")
    return False
  return True


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


def repo_name(value: Any) -> str | None:
  """The `owner/name` of a source value: a plain name or a GitHub URL. None when neither."""
  text = str(value or "").strip()
  if not text:
    return None
  parts = urlparse(text if "://" in text else f"https://github.com/{text}")
  if (parts.hostname or "").lower().removeprefix("www.") != "github.com":
    return None
  segments = [part for part in parts.path.strip("/").split("/") if part]
  if len(segments) < 2:
    return None
  owner = segments[0]
  name = segments[1].removesuffix(".git")
  # Each part is short and simple, so 1 long value never slows the reader.
  if not (REPO_PART.fullmatch(owner) and REPO_PART.fullmatch(name)):
    return None
  return f"{owner}/{name}"


def commit_of(repo: str, ref: str, timeout: float = TIMEOUT) -> str | None:
  """The commit of 1 ref of a repo, from the GitHub API. A failure logs and gives None."""
  url = API.format(repo=repo, ref=ref)
  body = fetch(url, timeout)
  if body is None:
    return None
  try:
    found = json.loads(body)
  except ValueError:
    tell(f"hook source {repo}: {url} did not answer with JSON")
    return None
  commit = found.get("sha") if isinstance(found, Mapping) else None
  if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
    tell(f"hook source {repo}: {url} named no commit")
    return None
  return commit.lower()


def archive_files(body: bytes, path: str) -> dict[str, bytes] | None:
  """The `.py` files directly under 1 folder of a repo archive, by file name.

  None names an archive that does not read: the caller keeps the files on disk.
  """
  folder = path.strip("/")
  found: dict[str, bytes] = {}
  try:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
      for member in archive.getmembers():
        if not member.isfile():
          continue
        parts = member.name.split("/", 1)
        if len(parts) != 2 or posixpath.dirname(parts[1]) != folder:
          continue
        name = posixpath.basename(parts[1])
        if not PY.fullmatch(name):
          continue
        stream = archive.extractfile(member)
        if stream is not None:
          found[name] = stream.read()
  except (tarfile.TarError, OSError, EOFError) as exc:
    tell(f"hook archive did not read: {exc}; the files on disk stay")
    return None
  return {name: found[name] for name in sorted(found)}


def update(
  entries: Iterable[Any],
  folder: Path | None = None,
  lock_path: Path | None = None,
  only_missing: bool = False,
  take: Iterable[str] | None = None,
) -> list[str]:
  """Fetch each source and write its hook files. Return the names whose bytes moved.

  `only_missing` is the rule of a start: a source fetches only when 1 of its recorded files is
  missing from the folder, unless the entry sets `auto_update`. A `take` list writes those names
  alone, which lets the operator leave a file of the source alone. A failed fetch, an archive that
  does not read, a block the reader refuses or a write that fails all keep the files on disk and
  the record that describes them.
  """
  wanted = None if take is None else {str(name) for name in take}
  target = FOLDER if folder is None else folder
  lock = LOCK if lock_path is None else lock_path
  records = read_records(lock)
  moved: list[str] = []
  for entry in entries:
    if not isinstance(entry, Mapping):
      continue
    repo = repo_name(entry.get("repo"))
    if repo is None:
      tell(f"hook source {entry.get('repo')!r} is not an owner/name or a GitHub URL")
      continue
    ref = str(entry.get("ref") or "main").strip()
    path = str(entry.get("path") or "").strip()
    pinned = [name for name, record in records.items() if record["repo"] == repo]
    if (
      only_missing
      and not entry.get("auto_update")
      and pinned
      and all((target / name).is_file() for name in pinned)
    ):
      continue
    commit = commit_of(repo, ref)
    if commit is None:
      continue
    body = fetch(ARCHIVE.format(repo=repo, commit=commit))
    if body is None:
      continue
    files = archive_files(body, path)
    if files is None:
      continue
    fresh: dict[str, dict[str, str]] = {}
    for name, raw in files.items():
      if wanted is not None and name not in wanted:
        continue
      try:
        text = raw.decode("utf-8")
      except UnicodeDecodeError:
        logger.warning("hook %s/%s is not UTF-8 text; the file stays out", repo, name)
        continue
      info, problem = meta_check(text)
      if problem is not None:
        logger.warning("hook %s/%s: %s; the file stays out", repo, name, problem)
        continue
      if info is None:
        logger.warning("hook %s/%s has no frontmatter block", repo, name)
      here = target / name
      if not (here.is_file() and here.read_bytes() == raw):
        if not write(here, raw):
          continue
        moved.append(name)
        logger.info("hook source %s wrote %s", repo, here)
      fresh[name] = {
        "sha256": digest(raw),
        "version": str(info.get("version", "")) if info else "",
        "repo": repo,
        "commit": commit,
      }
    kept = {
      name: record
      for name, record in records.items()
      if record["repo"] == repo and name not in fresh and (target / name).is_file()
    }
    others = {
      name: record for name, record in records.items() if record["repo"] != repo
    }
    found = others | kept | fresh
    if found != records:
      write_records(found, lock)
      records = found
  return moved


def scan(entry: Any) -> dict[str, Any] | None:
  """Read 1 source and answer with its files, without a write.

  The answer names the repo, the folder, the ref, the commit and 1 row per `.py` file of the
  archive: the name, the frontmatter, the sha256 and the problem of a refused block. A source
  that GitHub does not answer gives None.
  """
  if not isinstance(entry, Mapping):
    return None
  repo = repo_name(entry.get("repo"))
  if repo is None:
    tell(f"hook source {entry.get('repo')!r} is not an owner/name or a GitHub URL")
    return None
  ref = str(entry.get("ref") or "main").strip()
  folder = str(entry.get("path") or "").strip().strip("/")
  commit = commit_of(repo, ref)
  if commit is None:
    return None
  body = fetch(ARCHIVE.format(repo=repo, commit=commit))
  if body is None:
    return None
  files = archive_files(body, folder)
  if files is None:
    return None
  rows: list[dict[str, Any]] = []
  for name, raw in sorted(files.items()):
    try:
      text = raw.decode("utf-8")
    except UnicodeDecodeError:
      rows.append(
        {
          "name": name,
          "version": "",
          "scope": "",
          "targets": [],
          "surfaces": [],
          "problem": "the file is not UTF-8 text",
          "sha256": digest(raw),
        }
      )
      continue
    info, problem = meta_check(text)
    rows.append(
      {
        "name": name,
        "version": str(info.get("version", "")) if info else "",
        "scope": str(info.get("scope", "global")) if info else "",
        "targets": [str(target) for target in info["targets"]]
        if info and info.get("targets")
        else [],
        "surfaces": [str(surface) for surface in info["surfaces"]]
        if info and info.get("surfaces")
        else [],
        "problem": problem or "",
        "sha256": digest(raw),
      }
    )
  return {
    "repo": repo,
    "path": folder,
    "ref": ref,
    "commit": commit,
    "files": rows,
  }


def read_records(path: Path | None = None) -> dict[str, dict[str, str]]:
  """The record of each installed file: `sha256`, `version`, `repo` and `commit`.

  A record with no usable sha256 stays out, so 1 bad line never breaks the boot.
  """
  target = LOCK if path is None else path
  try:
    found = json.loads(target.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  if not isinstance(found, dict):
    return {}
  records: dict[str, dict[str, str]] = {}
  for name, record in found.items():
    if not isinstance(record, Mapping):
      continue
    sha = str(record.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
      continue
    records[str(name)] = {
      "sha256": sha,
      "version": str(record.get("version", "")),
      "repo": str(record.get("repo", "")),
      "commit": str(record.get("commit", "")),
    }
  return records


def write_records(
  records: Mapping[str, Mapping[str, str]], path: Path | None = None
) -> None:
  """Write the lock file, in name order, with 1 record per installed file."""
  target = LOCK if path is None else path
  target.parent.mkdir(parents=True, exist_ok=True)
  body = {name: dict(records[name]) for name in sorted(records)}
  target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
