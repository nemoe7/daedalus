"""Provider config from one YAML file.

Shape: provider name at the top level, in alphabetical order. Per provider:
`api_key`, `api_base`, `discovery_url`, `rpm`, `exclude`, `tier`, `models`.
A string value that starts with `os.environ/` resolves to that environment variable.
An unset variable gives an empty string.

This module holds the config. It does not use it.
"""

import os
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "os.environ/"
DEFAULT_PATH = Path("config.yml")

_config: dict[str, Any] | None = None


def resolve_env(value: str) -> str:
  """Return the environment variable named after `os.environ/`."""
  return os.environ.get(value[len(ENV_PREFIX) :], "")


def expand(node: Any) -> Any:
  """Replace every `os.environ/NAME` string in the tree with its value."""
  if isinstance(node, str):
    if node.startswith(ENV_PREFIX):
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
  """Return the loaded config, loading `config.yml` on the first call."""
  if _config is None:
    return load_config()
  return _config


def set_config(config: dict[str, Any] | None) -> None:
  """Replace the loaded config. Test seam."""
  global _config
  _config = config
