"""Fetch the hook files of `remote_hooks` and keep the last verified copy.

The operator names each file with a URL and the sha256 of its bytes. A boot fetches an entry only
when the copy in `config/hooks` does not match the pin, so a restart with no network keeps serving.
A failed fetch or a body that misses the pin leaves the last good file and writes 1 error line.

The pin lives in the settings file, never in the fetched file: a file cannot vouch for itself.
The `remote_hook_hosts` group limits the hosts that a URL may name. `daedalus hooks pin` records
the digest of each local hook file in `config/hooks.lock.json`, and `daedalus hooks verify`
compares the files on disk against that lock and against the pins of the settings file.

A source of the `hooks.sources` group names a GitHub repo, a folder inside it and a ref. `update`
reads the commit of the ref, reads the archive of that commit, and writes the `.py` files of the
folder through a temporary name. The lock then holds the sha256, the version, the repo and the
commit of each file.
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

FOLDER: Final = Path("config/hooks")
# The digests of the local hook files, beside the folder so that the loader never reads them.
LOCK: Final = Path("config/hooks.lock.json")
TIMEOUT: Final = 30.0
# The 2 answers of a GitHub source: the commit of a ref, and the archive of a commit.
API: Final = "https://api.github.com/repos/{repo}/commits/{ref}"
ARCHIVE: Final = "https://codeload.github.com/{repo}/tar.gz/{commit}"
# The `owner/name` of a source, and the name of a hook file inside the archive of a source.
REPO: Final = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
PY: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.py")
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
  found = f"{segments[0]}/{segments[1].removesuffix('.git')}"
  return found if REPO.fullmatch(found) else None


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
) -> list[str]:
  """Fetch each source and write its hook files. Return the names whose bytes moved.

  `only_missing` is the rule of a start: a source fetches only when 1 of its recorded files is
  missing from the folder, unless the entry sets `auto_update`. A failed fetch, an archive that
  does not read, a block the reader refuses or a write that fails all keep the files on disk and
  the record that describes them.
  """
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
