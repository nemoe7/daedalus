"""Walk scan targets and read text files, honoring skip rules and gitignore."""

import re
import subprocess
from pathlib import Path

import config
from config import match_glob

SKIP_DIRS = {
  ".pytest_cache",
  ".git",
  ".venv",
  "__pycache__",
  "build",
  "dist",
  "node_modules",
  "out",
}


SKIP_PATHS = (
  ".agents",
  "arena-state",
  "AGENTS.md",
  "**/AGENTS.md",
  "ARENA.md",
  "**/ARENA.md",
)


SKIP_SUFFIXES = {
  ".json",
  ".webmanifest",
}


LICENSE_FILE_RE = re.compile(
  r"^(?:(?:un)?licen[cs]e(?:[-._].*)?|.*\.licen[cs]e|copying(?:[-._].*)?|"
  r"notice(?:[-._].*)?|(?:thirdparty|copyright)notices?(?:\..*)?|"
  r"patents?(?:[-._].*)?)$",
  re.IGNORECASE,
)


def is_license_file(path):
  """True when a path has a common license, copying, or notice filename."""
  return bool(LICENSE_FILE_RE.match(Path(path).name))


def text_file(path):
  """Return the decoded text, or None when the file is not UTF-8 text."""
  try:
    raw = path.read_bytes()
  except OSError:
    return None
  if b"\0" in raw[:1024]:
    return None
  try:
    return raw.decode("utf-8")
  except UnicodeDecodeError:
    return None


def _matches_ignore_item(text, name, item):
  """True when relative POSIX path `text` or filename `name` matches `item`."""
  return text == item or text.startswith(item + "/") or match_glob(text, name, item)


def skipped(path, root, ignores):
  """True when a path sits under a skipped directory or an ignored prefix."""
  if is_license_file(path):
    return True
  custom_exclude = config.ACTIVE_CONFIG.get("exclude")
  if custom_exclude is None and path.suffix.lower() in SKIP_SUFFIXES:
    return True
  try:
    relative = path.resolve().relative_to(root.resolve())
  except ValueError:
    relative = path
  text = relative.as_posix()
  if custom_exclude is None and any(part in SKIP_DIRS for part in Path(text).parts):
    return True
  base_skips = custom_exclude if custom_exclude is not None else SKIP_PATHS
  all_ignores = (
    *base_skips,
    *config.ACTIVE_CONFIG.get("extend_exclude", ()),
    *ignores,
  )
  excluded = False
  for raw in all_ignores:
    item = str(raw).strip()
    if not item:
      continue
    if item.startswith("!"):
      target = item[1:].strip()
      if target and _matches_ignore_item(text, path.name, target):
        excluded = False
    elif _matches_ignore_item(text, path.name, item):
      excluded = True
  return excluded


def git_ignored(root, candidates):
  """Return the subset of candidate Paths that git check-ignore marks as ignored."""
  if not candidates or not config.ACTIVE_CONFIG.get("respect_gitignore", True):
    return set()
  base = root.resolve()
  rel_map = {}
  for candidate in candidates:
    try:
      rel = candidate.resolve().relative_to(base).as_posix()
    except ValueError:
      rel = candidate.as_posix()
    rel_map[rel] = candidate
  try:
    proc = subprocess.run(
      ["git", "-C", str(base), "check-ignore", "-z", "--stdin"],
      input="\0".join(rel_map),
      capture_output=True,
      text=True,
      encoding="utf-8",
      errors="replace",
      check=False,
    )
  except (OSError, UnicodeError):
    return set()
  if proc.returncode not in (0, 1):
    return set()
  return {rel_map[item] for item in proc.stdout.split("\0") if item in rel_map}


def walk(targets, ignores):
  """Yield every text file under the targets that survives the skip rules."""
  for target in targets:
    path = Path(target)
    if not path.exists():
      raise ValueError(f"target does not exist: {target}")
    if path.is_file():
      if path.suffix.lower() not in SKIP_SUFFIXES and not is_license_file(path):
        yield path
      continue
    root = path
    # Exclusion patterns start at the config's project root, never at the walk target.
    # A subdirectory target keeps the same relative paths.
    skip_root = config.ACTIVE_CONFIG.get("root") or root
    candidates = [
      candidate
      for candidate in sorted(root.rglob("*"))
      if candidate.is_file() and not skipped(candidate, skip_root, ignores)
    ]
    ignored_set = git_ignored(root, candidates)
    for candidate in candidates:
      if candidate not in ignored_set:
        yield candidate
