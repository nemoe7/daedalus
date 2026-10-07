"""Router settings from `config/daedalus.yml`, over the built-in defaults."""

import re
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import SingleQuotedScalarString

from daedalus.config import error_text, load_yaml
from daedalus.config.provider_edit import round_trip

DEFAULT_PATH = Path("config/daedalus.yml")
DEFAULTS: dict[str, dict[str, Any]] = {
  # How a request picks a model: the classifier bar and the keyword rules.
  "routing": {
    "threshold": 0.75,
    "escalation": [
      "ultrathink",
      "think hard",
      "think harder",
      "think deeply",
      "think longer",
      "root cause",
      "race condition",
      "memory leak",
      "deadlock",
      "security review",
      "performance regression",
      "audit",
      "refactor",
      "investigate",
      "diagnose",
      "code review",
      "system design",
      "optimize",
      "debug",
      "design",
      "architecture",
    ],
    "switch": ["clanker"],
  },
  # 1 mode names the session pin and the race: none is neither, session is the pin, race is both.
  "affinity": {
    "mode": "session",
    "idle": 3600.0,
    "stay": 0.85,
    "change_on_draw": True,
    "count": 1,
    "chance": 0.05,
    "slow": 30.0,
    "penalty": 0.9,
  },
  # The load balance after a fault, a slow token or a rate limit.
  "balance": {
    "weights": True,
    "success": 1.5,
    "fault": 0.5,
    "slow": 0.75,
    "hourly": 1.212,
    "rate_limit": 0.75,
    "first": 60.0,
    "longest": 21600.0,
    "pacing": True,
  },
  # The budgets on time and on repeats.
  "limits": {
    "request": 600.0,
    "wait": 60.0,
    "slow": 30.0,
    "calls": 3,
    "repeats": 4,
    "shortest": 20,
    "longest": 2000,
  },
  # The message compression through Headroom. A block or a model entry turns it off for 1 model.
  "optimization": {"enabled": True, "timeout": 5.0},
  "catalog": {"every": 6.0, "anchor": 6.0},
  # The request hook files, and the rules for the hook files that come from a URL.
  "hooks": {
    "on-request": [],
    "on-prompt": [],
    "on-chunk": [],
    "remote": [],
    "remote_hosts": [],
  },
  # The names and the view that suit the owner, not the router.
  "personalization": {
    "tier-a": "sophos",
    "tier-b": "deinos",
    "tier-c": "koinos",
    "tier-d": "moros",
    "audio": "graphos",
    "images": "photos",
    "theme": "system",
    "time_format": "24h",
  },
}
# The modes of `affinity.mode`. The 2 old groups each map to 1 of them.
MODES = ("none", "session", "race")
MIGRATED = {"session_affinity": "session", "parallel": "race"}
# The old group names and the group that holds their keys now.
RENAMED = {
  "timeouts": "limits",
  "loops": "limits",
  "weights": "balance",
  "cooldown": "balance",
  "pacing": "balance",
  "headroom": "optimization",
  "escalation": "routing",
  "switch": "routing",
  "request_hooks": "hooks",
  "remote_hooks": "hooks",
  "remote_hook_hosts": "hooks",
  "dashboard": "personalization",
  "pools": "personalization",
}
POOL_KEYS = ("tier-a", "tier-b", "tier-c", "tier-d", "audio", "images")
LOOP_LIMITS = {
  "calls": (2, 100),
  "repeats": (2, 16),
  "shortest": (1, 1000),
  "longest": (1, 10000),
}
# The longest a timeout waits: 1 day. A longer wait never gives an answer.
TIMEOUT_MAX = 86400.0
THEMES = ("system", "light", "dark")
TIME_FORMATS = ("24h", "12h")
# A host of the allowlist: letters, digits and dashes, in labels of a name. No port, no path.
HOST = re.compile(
  r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*"
)
POOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")


class SettingsError(ValueError):
  """A settings file with an unknown key or a wrong value."""


def unit_interval(name: str, value: Any) -> float:
  """A number from 0 to 1, else a `SettingsError`."""
  if (
    isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1
  ):
    raise SettingsError(f"{name} must be a number from 0 to 1")
  return float(value)


def check(group: str, key: str, value: Any) -> Any:
  """The value when its type fits the default, else a `SettingsError`."""
  name = f"{group}.{key}"
  if group == "routing":
    if key in ("escalation", "switch"):
      return keyword_list(name, value)
    return unit_interval(name, value)
  if group == "limits" and key in LOOP_LIMITS:
    if isinstance(value, bool) or not isinstance(value, int):
      raise SettingsError(f"{name} must be a whole number")
    minimum, maximum = LOOP_LIMITS[key]
    if not minimum <= value <= maximum:
      raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value
  if group == "catalog":
    return schedule_value(name, key, value)
  if group == "hooks":
    if key == "remote":
      return remote_list(name, value)
    if key == "remote_hosts":
      return host_list(name, value)
    return hook_paths(name, value)
  if group == "personalization":
    if key in POOL_KEYS:
      if (
        not isinstance(value, str) or not POOL_NAME.fullmatch(value) or value == "auto"
      ):
        raise SettingsError(
          f"{name} must be 1 to 40 letters, digits, dots, dashes or underscores, and not auto"
        )
      return value
    if key == "theme":
      if value not in THEMES:
        raise SettingsError(f"{name} must be system, light or dark")
      return value
    if key == "time_format":
      if value not in TIME_FORMATS:
        raise SettingsError(f"{name} must be 24h or 12h")
      return value
  if key == "mode":
    if value not in MODES:
      raise SettingsError(f"{name} must be none, session or race")
    return value
  if key in ("enabled", "change_on_draw", "weights", "pacing"):
    if not isinstance(value, bool):
      raise SettingsError(f"{name} must be true or false")
    return value
  if key == "count":
    if isinstance(value, bool) or not isinstance(value, int):
      raise SettingsError(f"{name} must be a whole number")
    if not 1 <= value <= 10:
      raise SettingsError(f"{name} must be between 1 and 10")
    return value
  if key == "chance":
    return unit_interval(name, value)
  if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
    raise SettingsError(f"{name} must be a number above 0")
  if group in ("limits", "optimization") and value > TIMEOUT_MAX:
    raise SettingsError(f"{name} must be at most {int(TIMEOUT_MAX)} seconds")
  if key == "stay" and value >= 1:
    raise SettingsError(f"{name} must be below 1")
  if key == "penalty" and value > 1:
    raise SettingsError(f"{name} must be at most 1")
  return float(value)


def keyword_list(name: str, value: Any) -> list[str]:
  """A list of keywords, each a word or phrase with text."""
  if not isinstance(value, list) or not all(
    isinstance(item, str) and item.strip() for item in value
  ):
    raise SettingsError(f"{name} must be a list of words or phrases")
  return [item.strip() for item in value]


def remote_list(name: str, value: Any) -> list[dict[str, str]]:
  """The remote hook files: each entry carries a URL, the sha256 of its bytes, and an optional name."""
  if value in (None, ""):
    return []
  if not isinstance(value, list):
    raise SettingsError(f"{name} must be a list of remote hook files")
  found: list[dict[str, str]] = []
  seen: set[str] = set()
  for item in value:
    if not isinstance(item, dict):
      raise SettingsError(f"{name}: each item needs a url and a sha256")
    for key in item:
      if key not in ("url", "sha256", "name"):
        raise SettingsError(f"{name}: unknown key {key!r}")
    url = item.get("url")
    pin = item.get("sha256")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
      raise SettingsError(f"{name}: url must start with http:// or https://")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin):
      raise SettingsError(f"{name}: sha256 must be 64 hex characters")
    name_value = item.get("name")
    if name_value is not None and not isinstance(name_value, str):
      raise SettingsError(f"{name}: name must be a file name")
    entry = {"url": url, "sha256": pin.lower()}
    if isinstance(name_value, str) and name_value.strip():
      entry["name"] = name_value.strip()
      if entry["name"] in seen:
        raise SettingsError(f"{name}: each file needs its own name")
      seen.add(entry["name"])
    found.append(entry)
  return found


def host_list(name: str, value: Any) -> list[str]:
  """The hosts of the `remote_hook_hosts` allowlist: 1 host each, or `*.` and a host."""
  if not isinstance(value, list) or not all(
    isinstance(item, str) and item.strip() for item in value
  ):
    raise SettingsError(f"{name} must be a list of hosts")
  found: list[str] = []
  for item in value:
    host = item.strip().lower().rstrip(".")
    if not HOST.fullmatch(host.removeprefix("*.")):
      raise SettingsError(f"{name}: {item!r} is not a host")
    found.append(host)
  return found


def hook_path(name: str, value: Any) -> str:
  """A hook file path inside the config folder, or an empty value for no hook."""
  if value in (None, ""):
    return ""
  if not isinstance(value, str) or value != value.strip() or not value:
    raise SettingsError(f"{name} must be a hook file path, or empty")
  return value


def hook_paths(name: str, value: Any) -> list[str]:
  """The hook files of 1 request point: 1 path, a list of paths, or empty for no hook."""
  if value in (None, "", []):
    return []
  if isinstance(value, str):
    return [hook_path(name, value)]
  if not isinstance(value, list):
    raise SettingsError(f"{name} must be a hook file path, or a list of them")
  return [hook_path(name, item) for item in value]


def schedule_value(name: str, key: str, value: Any) -> float:
  """A catalog hour value: `every` is 0 or a part of 24, and `anchor` is an hour of the day."""
  if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
    raise SettingsError(f"{name} must be a number of hours, 0 or more")
  if key == "anchor" and (value >= 24 or value != int(value)):
    raise SettingsError(f"{name} must be a whole hour from 0 to 23")
  if key == "every" and value and abs(24 / value - round(24 / value)) > 1e-9:
    raise SettingsError(f"{name} must divide 24 hours, or be 0 to stop the rebuilds")
  return float(value)


def load(path: Path | str = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
  """The settings: each value in the file replaces its default."""
  target = Path(path)
  return parse(target.read_text(encoding="utf-8") if target.exists() else "", target)


def parse(text: str, target: Path | str = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
  """The settings from YAML text, over the defaults."""
  merged = {
    group: dict(values) if isinstance(values, dict) else list(values)
    for group, values in DEFAULTS.items()
  }
  raw = load_yaml(text)
  if raw is None:
    raw = {}
  if not isinstance(raw, dict):
    raise SettingsError(f"{target} must hold groups of keys")
  for group, values in raw.items():
    if group in MIGRATED:
      raise SettingsError(f"{group} is gone. Use affinity.mode: {MIGRATED[group]}")
    if group in RENAMED:
      raise SettingsError(f"{group} is gone. Use {RENAMED[group]}")
    if group not in DEFAULTS:
      raise SettingsError(f"unknown group {group!r} in {target}")
    if not isinstance(DEFAULTS[group], dict):
      # A group that holds a list, such as the remote hook files, takes the list as its value.
      merged[group] = check(group, group, values)
      continue
    if not isinstance(values, dict):
      raise SettingsError(f"{group} must hold keys")
    for key, value in values.items():
      if key not in DEFAULTS[group]:
        raise SettingsError(f"unknown key {group}.{key} in {target}")
      merged[group][key] = check(group, key, value)
  pool_values = [merged["personalization"][key] for key in POOL_KEYS]
  if len(set(pool_values)) < len(pool_values):
    raise SettingsError("each pool in personalization must have its own name")
  limit_values = merged["limits"]
  if limit_values["shortest"] > limit_values["longest"]:
    raise SettingsError("limits.shortest must be at most limits.longest")
  return merged


def written(value: Any) -> Any:
  """A new value, with a string that YAML could read as another type quoted."""
  if isinstance(value, list):
    return [written(item) for item in value]
  if isinstance(value, dict):
    node = CommentedMap()
    for key, inner in value.items():
      node[key] = written(inner)
    return node
  if isinstance(value, str) and yaml.safe_dump(value).startswith(("'", '"')):
    return SingleQuotedScalarString(value)
  return value


def update_text(text: str, changes: dict[str, dict[str, Any]]) -> str:
  """The YAML text with new values, and its comments kept. None removes the key."""
  for group, values in changes.items():
    if group in MIGRATED:
      raise SettingsError(f"{group} is gone. Use affinity.mode: {MIGRATED[group]}")
    if group in RENAMED:
      raise SettingsError(f"{group} is gone. Use {RENAMED[group]}")
    if group not in DEFAULTS or not isinstance(values, dict):
      raise SettingsError(f"unknown group {group!r}")
    for key in values:
      if key not in DEFAULTS[group]:
        raise SettingsError(f"unknown key {group}.{key}")
  writer = round_trip()
  try:
    document = writer.load(text) if text.strip() else None
  except yaml.YAMLError as exc:
    raise SettingsError(error_text(exc)) from exc
  if not isinstance(document, CommentedMap):
    if text.strip():
      raise SettingsError("the settings file must hold groups of keys")
    document = CommentedMap()
  for group, values in changes.items():
    block = document.get(group)
    if block is None:
      block = document[group] = CommentedMap()
    elif not isinstance(block, CommentedMap):
      raise SettingsError(f"{group} must hold keys")
    for key, value in values.items():
      if value is None:
        block.pop(key, None)
      else:
        block[key] = written(value)
    if not block:
      del document[group]
  if not document:
    return ""
  output = StringIO()
  writer.dump(document, output)
  return output.getvalue()
