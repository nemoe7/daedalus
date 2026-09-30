"""Provider hooks: the Python files in the `hooks` list of a provider block, for chat requests and answers."""

import copy
import importlib.machinery
import importlib.util
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from daedalus.config import block_for

logger = logging.getLogger("daedalus.hooks")

# The hook files must be in this folder. The paths in `hooks` start here.
CONFIG_DIR = Path("config")
# Each hook point, and the function that its file defines.
POINTS = {
  "on-catalog": "on_catalog",
  "on-upstream": "on_upstream",
  "on-answer": "on_answer",
}

# The loaded module of each file, with the file time. A new file time loads the file again.
_loaded: dict[Path, tuple[float, ModuleType | None]] = {}
# The problems that are already in the log, so that each one shows 1 time.
_told: set[str] = set()


def tell(problem: str) -> None:
  """Log a problem with the hook config 1 time."""
  if problem not in _told:
    _told.add(problem)
    logger.warning("%s", problem)


def resolve(value: Any) -> Path | None:
  """The full path of one hook file, with any name, or None when it is not in the config folder."""
  if not isinstance(value, str) or not value.strip():
    tell(f"hook {value!r} is not a file path")
    return None
  root = CONFIG_DIR.resolve()
  path = (root / value).resolve()
  if not path.is_relative_to(root):
    tell(f"hook {value} is outside the config folder")
    return None
  return path


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


def run(
  point: str,
  config: Mapping[str, Any],
  model: str,
  value: dict[str, Any],
  **context: Any,
) -> dict[str, Any]:
  """The value after each hook of one point, in list order.

  Each hook gets a copy. It returns a new dict, or None to keep its changes to the copy.
  A hook that fails does not change the value.
  """
  if point not in POINTS:
    raise ValueError(f"Unknown hook point: {point}")
  function = POINTS[point]
  for path in files(config, model, point):
    found = load(path)
    hook = getattr(found, function, None)
    if not callable(hook):
      if found is not None:
        tell(f"hook file {path} has no {function} function")
      continue
    work = copy.deepcopy(value)
    try:
      result = hook(work, model=model, **context)
    except Exception:
      logger.exception("%s hook %s for %s failed", point, path, model)
      continue
    if result is None:
      value = work
    elif isinstance(result, dict):
      value = result
    else:
      logger.warning("%s hook %s for %s returned no dict", point, path, model)
  return value
