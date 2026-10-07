"""Load provider YAML and resolve env:NAME and db:NAME values."""

import logging
import os
import re
from collections.abc import Hashable, Mapping
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("daedalus.config")

ENV_PREFIX = "env:"
SAVED_PREFIX = "db:"
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
)

_config: dict[str, Any] | None = None
SAVED: dict[str, str] = {}

# The shape of each known key of a provider block: the type of the value, and the words of a refusal.
BLOCK_SHAPES: dict[str, tuple[type, str]] = {
  "api_key": (str, "a string"),
  "api_base": (str, "a string"),
  "api_type": (str, "a string"),
  "discovery_url": (str, "a string"),
  "discovery_match": (dict, "a mapping of a property to its value"),
  "client_keys": (dict, "a mapping of a client to its key"),
  "tier": (dict, "a mapping of a tier name to a list of patterns"),
  "models": (dict, "a mapping of a pattern to its values"),
  "hooks": (list, "a list of hook file names"),
  "exclude": (list, "a list of patterns"),
}
# The shape of the value of a key that holds a mapping.
NESTED_SHAPES: dict[str, tuple[type, str]] = {
  "tier": (list, "a list of patterns"),
  "client_keys": (str, "a string"),
}


def env_value(name: str) -> str:
  """The value of 1 name: the environment variable, else empty. Used for env:NAME."""
  return os.environ.get(name, "")


def saved_value(name: str) -> str:
  """The saved value of 1 name, else empty. Used for db:NAME (DB-only, no env fallback)."""
  return SAVED.get(name, "")


def resolve_env(value: str) -> str:
  """Replace each env:NAME token with its environment variable."""
  return ENV_PATTERN.sub(lambda found: env_value(found.group(1)), value)


def resolve_saved(value: str) -> str:
  """Replace each db:NAME token with its saved value."""
  return SAVED_PATTERN.sub(lambda found: saved_value(found.group(1)), value)


def load_saved() -> dict[str, str]:
  """Read the saved values from the state file into memory."""
  from daedalus import store
  from daedalus.store import saved_env

  SAVED.clear()
  SAVED.update(saved_env.read(store.MODELS_DB))
  return SAVED


def expand(node: Any) -> Any:
  """Replace every `env:NAME` and `db:NAME` string in the tree with its value."""
  if isinstance(node, str):
    if SAVED_PREFIX in node:
      node = resolve_saved(node)
    if ENV_PREFIX in node:
      node = resolve_env(node)
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
  loader = _UniqueKeys(text)
  try:
    return loader.get_single_data()
  finally:
    loader.dispose()


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


def file_shadows(path: Path | str = DEFAULT_PATH) -> dict[str, str]:
  """Each `{provider}.yml` whose block the main file also sets, with the name of the main file."""
  main = Path(path)
  loaded = read_yaml(main)
  return {
    file.name: main.name
    for file in provider_files(path)
    if isinstance(loaded.get(file.stem), dict)
  }


def block_shape_problems(block: Mapping[str, Any]) -> list[tuple[tuple[str, ...], str]]:
  """Each known key of a provider block with a wrong shape, as its path and the reason."""
  found: list[tuple[tuple[str, ...], str]] = []
  for key, (kind, wanted) in BLOCK_SHAPES.items():
    value = block.get(key)
    if value is not None and not isinstance(value, kind):
      found.append(((key,), f"must be {wanted}"))
  for key, (kind, wanted) in NESTED_SHAPES.items():
    values = block.get(key)
    if not isinstance(values, dict):
      continue
    for name, value in values.items():
      if value is not None and not isinstance(value, kind):
        found.append(((key, str(name)), f"must be {wanted}"))
  return found


def file_shape_problems(loaded: Mapping[str, Any], stem: str = "") -> list[str]:
  """Each known key of a provider file with a wrong shape, as `<block>.<key> must be ...`."""
  own = f"{stem}." if stem else ""
  found = [
    f"{own}{'.'.join(path)} {reason}" for path, reason in block_shape_problems(loaded)
  ]
  for name, value in loaded.items():
    if name in BLOCK_SHAPES or not isinstance(value, Mapping):
      continue
    found += [
      f"{name}.{'.'.join(path)} {reason}"
      for path, reason in block_shape_problems(value)
    ]
  return found


def drop_wrong_shapes(loaded: dict[str, Any], where: str, stem: str) -> None:
  """Drop each known key of a provider file with a wrong shape, and name it in the log."""
  from daedalus.config import settings

  blocks: list[tuple[str, dict[str, Any]]] = [(stem, loaded)]
  blocks += [
    (name, value)
    for name, value in loaded.items()
    if name not in BLOCK_SHAPES and isinstance(value, dict)
  ]
  for name, block in blocks:
    for path, reason in block_shape_problems(block):
      # The settings groups of daedalus.yml share the top level with the provider blocks.
      if block is loaded and path[0] in settings.DEFAULTS:
        continue
      if len(path) == 2:
        block[path[0]].pop(path[1], None)
      else:
        block.pop(path[0], None)
      logger.warning(
        "%s: the %s.%s key %s, so it drops out", where, name, ".".join(path), reason
      )


def load_config(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
  """Read the main provider file and each `{provider}.yml` file into memory."""
  global _config
  load_saved()
  source = Path(path)
  loaded = read_yaml(source)
  drop_wrong_shapes(loaded, source.name, source.stem)
  for file, content in provider_blocks(path):
    block = loaded.setdefault(file.stem, {})
    if isinstance(block, dict):
      drop_wrong_shapes(content, file.name, file.stem)
      block[FILE_KEY] = content
  for name, owner in file_shadows(path).items():
    logger.warning(
      "%s: the %s block of %s also sets provider keys, and its keys win",
      name,
      Path(name).stem,
      owner,
    )
  from daedalus.config import defaults

  defaults.fill(loaded)
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
