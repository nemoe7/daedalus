"""Load the provider config from YAML and resolve os.environ/NAME inside any string."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "os.environ/"
ENV_PATTERN = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")
DEFAULT_PATH = Path("config/providers/free.yml")
STATE_DIR = Path(__file__).resolve().parents[2] / ".daedalus-state"
# The key of a provider block that holds the block of its own `{provider}.yml` file.
FILE_KEY = "_file"
# A provider file names at least 1 of these keys. The settings file names none of them.
PROVIDER_KEYS = (
  "api_key",
  "api_base",
  "api_type",
  "discovery_url",
  "discovery_match",
  "exclude",
  "tier",
  "models",
)

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


def read_yaml(path: Path) -> dict[str, Any]:
  """One YAML mapping, with `os.environ/` values resolved. Other content gives an empty mapping."""
  with path.open(encoding="utf-8") as handle:
    raw = yaml.safe_load(handle)
  return expand(raw) if isinstance(raw, dict) else {}


def provider_blocks(
  path: Path | str = DEFAULT_PATH,
) -> list[tuple[Path, dict[str, Any]]]:
  """The `{provider}.yml` files next to the main file, in name order, with their content."""
  main = Path(path)
  found = []
  for file in sorted(main.parent.glob("*.yml")):
    if file.name == main.name:
      continue
    content = read_yaml(file)
    if any(key in content for key in PROVIDER_KEYS):
      found.append((file, content))
  return found


def provider_files(path: Path | str = DEFAULT_PATH) -> list[Path]:
  """The paths of the `{provider}.yml` files next to the main provider file."""
  return [file for file, _ in provider_blocks(path)]


def load_config(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
  """Read the main provider file and each `{provider}.yml` file into memory."""
  global _config
  loaded = read_yaml(Path(path))
  for file, content in provider_blocks(path):
    block = loaded.setdefault(file.stem, {})
    if isinstance(block, dict):
      block[FILE_KEY] = content
  _config = loaded
  return _config


def file_block(provider: Any) -> dict[str, Any] | None:
  """The block of the `{provider}.yml` file of a provider, if it has one."""
  found = provider.get(FILE_KEY) if isinstance(provider, dict) else None
  return found if isinstance(found, dict) else None


def main_block(provider: Any) -> dict[str, Any] | None:
  """The block of a provider in the main file, without its file block."""
  if not isinstance(provider, dict):
    return None
  found = {key: value for key, value in provider.items() if key != FILE_KEY}
  return found or None


def file_takes(block: dict[str, Any], slug: str) -> bool:
  """Tell if a key of the `models` of a file block matches the slug."""
  from daedalus.catalog.discovery import any_match

  return any_match([str(key) for key in block.get("models") or {}], slug)


def block_for(config: Mapping[str, Any], name: str, slug: str) -> dict[str, Any] | None:
  """The block that sets the values of one model: its provider file, else the main file."""
  provider = config.get(name)
  found = file_block(provider)
  if found is not None and file_takes(found, slug):
    return found
  return main_block(provider)


def get_config() -> dict[str, Any]:
  """Return the loaded config, loading `config/providers/free.yml` on the first call."""
  if _config is None:
    return load_config()
  return _config


def set_config(config: dict[str, Any] | None) -> None:
  """Replace the loaded config. Test seam."""
  global _config
  _config = config
