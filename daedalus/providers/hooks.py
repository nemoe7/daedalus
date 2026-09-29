"""Provider hook files: Python functions in `config/hooks/{provider}.py` that change chat requests and answers."""

import copy
import importlib.util
import logging
import re
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger("daedalus.hooks")

# The folder of the hook files, next to the provider files.
HOOKS_DIR = Path("config/hooks")
# The hook points. A hook file can define each one as a function, and all are optional.
POINTS = ("request", "answer")

# The loaded module of each hook file, with the file time. A new file time loads the file again.
_loaded: dict[Path, tuple[float, ModuleType | None]] = {}


def module(provider: str) -> ModuleType | None:
  """The hook module of one provider, or None without a file or when the file does not load."""
  path = HOOKS_DIR / f"{provider}.py"
  try:
    stamp = path.stat().st_mtime
  except OSError:
    return None
  cached = _loaded.get(path)
  if cached is not None and cached[0] == stamp:
    return cached[1]
  found: ModuleType | None = None
  name = "daedalus_hook_" + re.sub(r"\W", "_", provider)
  spec = importlib.util.spec_from_file_location(name, path)
  if spec is not None and spec.loader is not None:
    try:
      found = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(found)
    except Exception:
      logger.exception("hook file %s did not load", path)
      found = None
  _loaded[path] = (stamp, found)
  return found


def run(
  point: str, model: str, value: dict[str, Any], **context: Any
) -> dict[str, Any]:
  """The value after the hook of one point for the provider of `model`.

  The hook gets a copy. It returns a new value, or None to keep its changes to the copy.
  An error in the hook keeps the value that came in.
  """
  if point not in POINTS:
    raise ValueError(f"Unknown hook point: {point}")
  found = module(model.partition("/")[0])
  hook = getattr(found, point, None)
  if not callable(hook):
    return value
  work = copy.deepcopy(value)
  try:
    result = hook(work, model=model, **context)
  except Exception:
    logger.exception("%s hook for %s failed", point, model)
    return value
  if result is None:
    return work
  if not isinstance(result, dict):
    logger.warning("%s hook for %s returned no dict", point, model)
    return value
  return result
