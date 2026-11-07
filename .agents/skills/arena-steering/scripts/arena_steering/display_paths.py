"""Render notice paths relative to the current user's home directory."""

from __future__ import annotations

import os
from pathlib import Path


def home_relative(path: Path) -> str:
  """Use the runtime home, including for a checkout outside that directory."""
  relative = os.path.relpath(path, Path.home())
  return "~" if relative == "." else f"~/{Path(relative).as_posix()}"
