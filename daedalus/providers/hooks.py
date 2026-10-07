"""Provider hooks: the Python files in the `hooks` list of a provider block, for chat requests and answers."""

import copy
import importlib.machinery
import importlib.util
import logging
import posixpath
import re
from collections.abc import Mapping
from itertools import islice
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from daedalus import __version__
from daedalus.config import block_for

logger = logging.getLogger("daedalus.hooks")

# The hook files must be in this folder. The paths in `hooks` start here.
CONFIG_DIR = Path("config")
# Each hook point, and the function that its file defines.
POINTS = {
  "on-catalog": "on_catalog",
  "on-request": "on_request",
  "on-prompt": "on_prompt",
  "on-http": "on_http",
  "on-upstream": "on_upstream",
  "on-answer": "on_answer",
  "on-chunk": "on_chunk",
}
# An optional extra function of a request hook file: the rows of the code legend.
INIT = "on_init"
# The frontmatter of a hook file: a comment block at the top, and the keys that it may hold.
BLOCK = "# ---"
META_KEYS = ("name", "version", "requires", "points", "scope", "targets")
SCOPES = ("global", "provider", "model")
META_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
# The head of a file that holds the frontmatter block.
HEAD = 60
# 1 comparison of a `requires` value: an operator and a dotted version.
CLAUSE = re.compile(r"(==|>=|<=|>|<) *([0-9]+(?:\.[0-9A-Za-z-]+)*)")

# The loaded module of each file, with the file time. A new file time loads the file again.
_loaded: dict[Path, tuple[float, ModuleType | None]] = {}
# The problems that are already in the log, so that each one shows 1 time.
_told: set[str] = set()


def tell(problem: str) -> None:
  """Log a problem with the hook config 1 time."""
  if problem not in _told:
    _told.add(problem)
    logger.warning("%s", problem)


def version_tuple(text: str) -> tuple[int, ...] | None:
  """The numbers of a dotted version, or None when a part is not a number."""
  parts = text.split(".")
  if not parts or not all(part.isdigit() for part in parts):
    return None
  return tuple(int(part) for part in parts)


def requires_ok(requires: str, version: str) -> bool:
  """True when each comma-separated comparison holds against the running version."""
  here = version_tuple(version)
  if here is None:
    return False
  for item in requires.split(","):
    found = CLAUSE.fullmatch(item.strip())
    if found is None:
      return False
    wanted = version_tuple(found.group(2))
    if wanted is None:
      return False
    size = max(len(here), len(wanted))
    left = here + (0,) * (size - len(here))
    right = wanted + (0,) * (size - len(wanted))
    operator = found.group(1)
    holds = (
      left == right
      if operator == "=="
      else left >= right
      if operator == ">="
      else left <= right
      if operator == "<="
      else left > right
      if operator == ">"
      else left < right
    )
    if not holds:
      return False
  return True


def meta_block(text: str) -> dict[str, Any] | None:
  """The frontmatter block of a file text, or None when the text holds no block."""
  lines = text.splitlines()
  at = 0
  while at < len(lines) and not lines[at].strip():
    at += 1
  if at >= len(lines) or lines[at].strip() != BLOCK:
    return None
  body: list[str] = []
  for line in lines[at + 1 :]:
    if line.strip() == BLOCK:
      found = yaml.safe_load("\n".join(body))
      return found if isinstance(found, dict) else {}
    body.append(line.removeprefix("#").removeprefix(" "))
  return None


def head_text(path: Path) -> str:
  """The first lines of a hook file, and an empty text when the file does not read."""
  try:
    with path.open(encoding="utf-8") as stream:
      return "".join(islice(stream, HEAD))
  except (OSError, UnicodeDecodeError):
    return ""


def meta_check(text: str) -> tuple[dict[str, Any] | None, str | None]:
  """The frontmatter of a file text and the reason it is not usable, 1 of the 2 filled."""
  found = meta_block(text)
  if found is None:
    return None, None
  for key in found:
    if key not in META_KEYS:
      return None, f"unknown frontmatter key {key!r}"
  name = found.get("name")
  if name is not None and (not isinstance(name, str) or not META_NAME.fullmatch(name)):
    return None, "name must be 1 file name"
  version = found.get("version")
  if version is not None:
    if isinstance(version, bool) or not isinstance(version, int | float | str):
      return None, "version must be 1 text"
    found["version"] = str(version)
  requires = found.get("requires")
  if requires is not None:
    if not isinstance(requires, str):
      return None, "requires must be 1 text"
    if not requires_ok(requires, __version__):
      return None, f"requires {requires}, and daedalus is {__version__}"
  points = found.get("points")
  if points is not None:
    if not isinstance(points, list) or not all(
      isinstance(point, str) for point in points
    ):
      return None, "points must be a list of point names"
    for point in points:
      if point not in POINTS:
        return None, f"unknown point {point!r}"
  scope = found.get("scope")
  if scope is not None and scope not in SCOPES:
    return None, f"scope {scope!r} must be 1 of {', '.join(SCOPES)}"
  targets = found.get("targets")
  if targets is not None and (
    not isinstance(targets, list)
    or not all(isinstance(target, str) and target.strip() for target in targets)
  ):
    return None, "targets must be a list of names"
  if scope in ("provider", "model") and not targets:
    return None, f"scope {scope} needs targets"
  return found, None


def meta(path: Path) -> dict[str, Any] | None:
  """The usable frontmatter of a hook file, or None when the file holds none."""
  return meta_check(head_text(path))[0]


def meta_problem(path: Path) -> str | None:
  """The reason the frontmatter of a hook file is not usable, or None."""
  return meta_check(head_text(path))[1]


def resolve(value: Any) -> Path | None:
  """The path of 1 hook file from the folder listing, or None when the folder holds no such name.

  The name picks a file of the listing, so a name outside the folder finds nothing.
  A name with no suffix names the `.py` file, as the docs and the OWUI action write it.
  """
  if not isinstance(value, str) or not value.strip():
    tell(f"hook {value!r} is not a file path")
    return None
  wanted = posixpath.normpath(value.replace("\\", "/")).strip("/")
  names = [wanted] if wanted.endswith(".py") else [wanted, f"{wanted}.py"]
  root = CONFIG_DIR.resolve()
  listing: dict[str, Path] = {}
  for found in (root / "hooks").rglob("*"):
    path = found.resolve()
    if path.is_file() and path.is_relative_to(root):
      listing.setdefault(path.relative_to(root).as_posix(), path)
  for name in names:
    if path := listing.get(name):
      return path
  tell(f"hook {value} is not a file of the config folder")
  return None


def hook_files() -> list[str]:
  """The names of the hook files in the config folder, as paths for the settings group."""
  root = CONFIG_DIR / "hooks"
  if not root.is_dir():
    return []
  return sorted(f"hooks/{path.name}" for path in root.glob("*.py"))


def entries_for(config: Mapping[str, Any], model: str) -> Any:
  """The `hooks` of a model, like `order`: its `models` entry, then the block that owns the model."""
  from daedalus.routing.router import model_setting

  found = model_setting(config, model, "hooks")
  if found is None:
    name, _, slug = model.partition("/")
    found = (block_for(config, name, slug) or {}).get("hooks")
  return found


def files(config: Mapping[str, Any], model: str, point: str) -> list[Path]:
  """The files of one hook point for `model`, in list order."""
  name = model.partition("/")[0]
  entries = entries_for(config, model)
  if entries is None:
    return []
  if not isinstance(entries, list):
    tell(f"hooks of {name} must be a list")
    return []
  found = []
  for entry in entries:
    if not isinstance(entry, dict):
      tell(f"hooks of {name}: each item needs a point and a file")
      continue
    for key in entry:
      if key not in POINTS:
        tell(f"hooks of {name}: unknown point {key}")
    if point in entry and (path := resolve(entry[point])) is not None:
      found.append(path)
  return found


def request_files(entries: Mapping[str, Any] | None, point: str) -> list[Path]:
  """The files of one request-level point, from the `hooks` group of the settings file."""
  value = entries.get(point) if isinstance(entries, Mapping) else None
  if value is None or value == "":
    return []
  items = [value] if isinstance(value, str) else value
  if not isinstance(items, list):
    tell(f"request_hooks.{point} must be a file path or a list of them")
    return []
  found = []
  for item in items:
    path = resolve(item)
    if path is not None:
      found.append(path)
  return found


def init_rows(entries: Mapping[str, Any] | None) -> list[list[str]]:
  """The legend rows of each enabled request hook file that defines `on_init`, in file order."""
  rows: list[list[str]] = []
  if not isinstance(entries, Mapping):
    return rows
  seen: set[Path] = set()
  for value in entries.values():
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
      continue
    for item in items:
      path = resolve(item)
      if path is None or path in seen:
        continue
      seen.add(path)
      hook = getattr(load(path), INIT, None)
      if not callable(hook):
        continue
      try:
        found = hook()
      except Exception:
        logger.exception("on-init hook %s failed", path)
        continue
      if isinstance(found, list):
        rows.extend(
          [str(row[0]), str(row[1])]
          for row in found
          if isinstance(row, (list, tuple)) and len(row) == 2
        )
  return rows


def load(path: Path) -> ModuleType | None:
  """The module of one hook file, or None when the file is not there or does not load."""
  try:
    stamp = path.stat().st_mtime
  except OSError:
    tell(f"hook file {path} is not there")
    return None
  cached = _loaded.get(path)
  if cached is not None and cached[0] == stamp:
    return cached[1]
  found: ModuleType | None = None
  name = "daedalus_hook_" + re.sub(r"\W", "_", str(path))
  # The loader reads Python from a file with any name, also without `.py`.
  spec = importlib.util.spec_from_file_location(
    name, path, loader=importlib.machinery.SourceFileLoader(name, str(path))
  )
  if spec is not None and spec.loader is not None:
    try:
      found = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(found)
    except Exception:
      logger.exception("hook file %s did not load", path)
      return None
  _loaded[path] = (stamp, found)
  return found


def run_files(
  point: str, paths: list[Path], value: dict[str, Any], **context: Any
) -> dict[str, Any]:
  """The value after each hook file of one point, in list order.

  Each hook gets a copy. It returns a new dict, or None to keep its changes to the copy.
  A hook that fails does not change the value.
  """
  if point not in POINTS:
    raise ValueError(f"Unknown hook point: {point}")
  function = POINTS[point]
  for path in paths:
    found = load(path)
    hook = getattr(found, function, None)
    if not callable(hook):
      if found is not None:
        tell(f"hook file {path} has no {function} function")
      continue
    work = copy.deepcopy(value)
    try:
      result = hook(work, **context)
    except Exception:
      logger.exception("%s hook %s for %s failed", point, path, context.get("model"))
      continue
    # The chunk point runs for each chunk of 1 answer, so its run line stays on DEBUG, and a hook
    # file of that point writes the line of the answer.
    logger.log(
      logging.DEBUG if point == "on-chunk" else logging.INFO,
      "%s hook %s for %s: %s",
      point,
      path.name,
      context.get("model") or "-",
      "a new value" if isinstance(result, dict) else "edits in place",
    )
    if result is None:
      value = work
    elif isinstance(result, dict):
      value = result
    else:
      logger.warning(
        "%s hook %s for %s returned no dict", point, path, context.get("model")
      )
  return value


def run(
  point: str,
  config: Mapping[str, Any],
  model: str,
  value: dict[str, Any],
  **context: Any,
) -> dict[str, Any]:
  """The value after each model hook of one point, in list order."""
  return run_files(point, files(config, model, point), value, model=model, **context)


def run_request(
  point: str,
  entries: Mapping[str, Any] | None,
  model: str,
  value: dict[str, Any],
  **context: Any,
) -> dict[str, Any]:
  """The value after each request hook of one point, from the settings file, in list order."""
  return run_files(point, request_files(entries, point), value, model=model, **context)
