"""Load the provider config from YAML and resolve os.environ/NAME inside any string."""

import os
import re
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "os.environ/"
ENV_PATTERN = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")
DEFAULT_PATH = Path("config/providers/free.yml")

_config: dict[str, Any] | None = None


def resolve_env(value: str) -> str:
  """Read one environment variable that an os.environ/NAME token names."""
  return ENV_PATTERN.sub(lambda found: os.environ.get(found.group(1), ""), value)


def expand(node: Any) -> Any:
  """Replace every `os.environ/NAME` string in the tree with its value."""
  if isinstance(node, str):
    if ENV_PREFIX in node:
      return resolve_env(node)
    return node
  if isinstance(node, list):
    return [expand(item) for item in node]
  if isinstance(node, dict):
    return {key: expand(value) for key, value in node.items()}
  return node


def load_config(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
  """Read one YAML file into memory, with `os.environ/` values resolved."""
  global _config
  with Path(path).open(encoding="utf-8") as handle:
    raw = yaml.safe_load(handle)
  _config = expand(raw) if isinstance(raw, dict) else {}
  return _config


def get_config() -> dict[str, Any]:
  """Return the loaded config, loading `config/providers/free.yml` on the first call."""
  if _config is None:
    return load_config()
  return _config


def set_config(config: dict[str, Any] | None) -> None:
  """Replace the loaded config. Test seam."""
  global _config
  _config = config
