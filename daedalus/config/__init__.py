"""Load the provider config from YAML and resolve env:NAME and db:NAME inside any string (legacy os.environ/NAME still works)."""

import os
import re
from collections.abc import Hashable, Mapping
from pathlib import Path
from typing import Any

import yaml

LEGACY_ENV_PREFIX = "os.environ/"
ENV_PREFIX = "env:"
SAVED_PREFIX = "db:"
LEGACY_ENV_PATTERN = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")
ENV_PATTERN = re.compile(r"env:([A-Za-z_][A-Za-z0-9_]*)")
SAVED_PATTERN = re.compile(r"db:([A-Za-z_][A-Za-z0-9_]*)")
DEFAULT_PATH = Path("config/providers/free.yml")
STATE_DIR = Path(__file__).resolve().parents[2] / ".daedalus-state"
# The key of a provider block that holds the block of its own `{provider}.yml` file.
FILE_KEY = "_file"
# A provider file names at least 1 of these keys. The settings file names none of them.
PROVIDER_KEYS = (
  "api_key",
  "client_keys",
  "api_base",
  "api_type",
  "discovery_url",
  "discovery_match",
  "exclude",
  "tier",
  "models",
  "hooks",
)

_config: dict[str, Any] | None = None
# The values that the dashboard saved for db:NAME, env:NAME (and legacy os.environ/NAME). Saved wins for legacy for backward compat.
SAVED: dict[str, str] = {}


def env_value(name: str) -> str:
  """The value of 1 name: the environment variable, else empty. Used for env:NAME."""
  return os.environ.get(name, "")


def legacy_env_value(name: str) -> str:
  """The value of 1 name: the saved value (backward compat), else the environment variable, else empty. Used for os.environ/NAME."""
  return SAVED.get(name) or os.environ.get(name, "")


def saved_value(name: str) -> str:
  """The saved value of 1 name, else empty. Used for db:NAME (DB-only, no env fallback)."""
  return SAVED.get(name, "")


def resolve_env(value: str) -> str:
  """Replace each env:NAME token with its environment variable."""
  return ENV_PATTERN.sub(lambda found: env_value(found.group(1)), value)


def resolve_legacy_env(value: str) -> str:
  """Replace each os.environ/NAME token with its saved value (compat) or environment variable."""
  return LEGACY_ENV_PATTERN.sub(lambda found: legacy_env_value(found.group(1)), value)


def resolve_saved(value: str) -> str:
  """Replace each db:NAME token with its saved value or environment variable."""
  return SAVED_PATTERN.sub(lambda found: saved_value(found.group(1)), value)


def load_saved() -> dict[str, str]:
  """Read the saved values from the state file into memory."""
  from daedalus import store
  from daedalus.store import saved_env

  SAVED.clear()
  SAVED.update(saved_env.read(store.MODELS_DB))
  return SAVED


def expand(node: Any) -> Any:
  """Replace every `env:NAME` and `db:NAME` string in the tree with its value (legacy `os.environ/NAME` still supported)."""
  if isinstance(node, str):
    if SAVED_PREFIX in node:
      node = resolve_saved(node)
    if ENV_PREFIX in node:
      node = resolve_env(node)
    if LEGACY_ENV_PREFIX in node:
      node = resolve_legacy_env(node)
    return node
  if isinstance(node, list):
    return [expand(item) for item in node]
  if isinstance(node, dict):
    return {key: expand(value) for key, value in node.items()}
  return node


class _UniqueKeys(yaml.SafeLoader):
  """A safe loader that refuses a key that a mapping already holds."""

  def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: dict[Any, yaml.Node] = {}
    for key_node, _ in node.value:
      key = self.construct_object(key_node, deep=deep)
      if not isinstance(key, Hashable):
        continue
      if key in seen:
        raise yaml.constructor.ConstructorError(
          "first",
          seen[key].start_mark,
          f"duplicate key {key!r}",
          key_node.start_mark,
        )
      seen[key] = key_node
    return super().construct_mapping(node, deep)


def load_yaml(text: Any) -> Any:
  """The content of one YAML text or stream. A duplicate key raises a `yaml.YAMLError`."""
  return yaml.load(text, _UniqueKeys)


def error_text(exc: Exception) -> str:
  """One line for a config error, with the line numbers of a YAML error and no stream name."""
  mark = getattr(exc, "problem_mark", None)
  if not isinstance(exc, yaml.MarkedYAMLError) or mark is None:
    return " ".join(str(exc).split())
  text = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
  if exc.context and exc.context_mark:
    text += f" ({exc.context} at line {exc.context_mark.line + 1})"
  return text


def read_yaml(path: Path) -> dict[str, Any]:
  """One YAML mapping, with `env:` / `db:` values resolved. Other content gives an empty mapping."""
  with path.open(encoding="utf-8") as handle:
    raw = load_yaml(handle)
  return expand(raw) if isinstance(raw, dict) else {}


def provider_blocks(
  path: Path | str = DEFAULT_PATH,
) -> list[tuple[Path, dict[str, Any]]]:
  """The `{provider}.yml` files next to the main file, in name order, with their content. Not valid YAML raises."""
  return [(file, read_yaml(file)) for file in provider_files(path)]


def provider_files(path: Path | str = DEFAULT_PATH) -> list[Path]:
  """The paths of the `{provider}.yml` files next to the main provider file, also the files with YAML that is not valid."""
  main = Path(path)
  found = []
  for file in sorted(main.parent.glob("*.yml")):
    if file.name == main.name:
      continue
    try:
      content = read_yaml(file)
    except yaml.YAMLError:
      found.append(file)
      continue
    if any(key in content for key in PROVIDER_KEYS):
      found.append(file)
  return found


def load_config(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
  """Read the main provider file and each `{provider}.yml` file into memory."""
  global _config
  load_saved()
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


def client_key(block: Any, client: str | None) -> str | None:
  """The provider key that the `client_keys` of a block give a client, or None."""
  keys = block.get("client_keys") if isinstance(block, dict) and client else None
  found = keys.get(client) if isinstance(keys, dict) else None
  return found if isinstance(found, str) and found else None


def get_config() -> dict[str, Any]:
  """Return the loaded config, loading `config/providers/free.yml` on the first call."""
  if _config is None:
    return load_config()
  return _config


def set_config(config: dict[str, Any] | None) -> None:
  """Replace the loaded config. Test seam."""
  global _config
  _config = config
