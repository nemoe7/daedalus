"""Router settings from `config/daedalus.yml`, over the built-in defaults."""

import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path("config/daedalus.yml")
DEFAULTS: dict[str, dict[str, Any]] = {
  "timeouts": {"request": 600.0, "wait": 60.0, "slow": None},
  "session_affinity": {"enabled": True, "idle": 3600.0, "stay": 0.85},
  "weights": {
    "enabled": True,
    "success": 1.5,
    "fault": 0.5,
    "slow": 0.75,
    "hourly": 1.212,
    "rate_limit": 0.75,
  },
  "cooldown": {"first": 60.0, "longest": 21600.0},
  "pacing": {"enabled": True},
  "catalog": {"every": 6.0, "anchor": 6.0},
  "headroom": {"timeout": 5.0},
}


class SettingsError(ValueError):
  """A settings file with an unknown key or a wrong value."""


def check(group: str, key: str, value: Any) -> Any:
  """The value when its type fits the default, else a `SettingsError`."""
  name = f"{group}.{key}"
  if group == "catalog":
    return schedule_value(name, key, value)
  if key == "enabled":
    if not isinstance(value, bool):
      raise SettingsError(f"{name} must be true or false")
    return value
  if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
    raise SettingsError(f"{name} must be a number above 0")
  if key == "stay" and value >= 1:
    raise SettingsError(f"{name} must be below 1")
  return float(value)


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
  merged = {group: dict(values) for group, values in DEFAULTS.items()}
  raw = yaml.safe_load(text)
  if raw is None:
    raw = {}
  if not isinstance(raw, dict):
    raise SettingsError(f"{target} must hold groups of keys")
  for group, values in raw.items():
    if group not in DEFAULTS:
      raise SettingsError(f"unknown group {group!r} in {target}")
    if not isinstance(values, dict):
      raise SettingsError(f"{group} must hold keys")
    for key, value in values.items():
      if key not in DEFAULTS[group]:
        raise SettingsError(f"unknown key {group}.{key} in {target}")
      merged[group][key] = check(group, key, value)
  timeouts = merged["timeouts"]
  if timeouts["slow"] is None:
    timeouts["slow"] = timeouts["wait"] / 2
  return merged


GROUP_LINE = re.compile(r"^(\w+):\s*(#.*)?$")
KEY_LINE = re.compile(r"^(\s+)(\w+):[ \t]*([^#\n]*?)([ \t]*#.*)?$")


def scalar(value: Any) -> str:
  """One settings value as YAML text."""
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, float) and value.is_integer():
    return str(int(value))
  return str(value)


def update_text(text: str, changes: dict[str, dict[str, Any]]) -> str:
  """The YAML text with new values, and its comments kept. None sets the default."""
  for group, values in changes.items():
    if group not in DEFAULTS or not isinstance(values, dict):
      raise SettingsError(f"unknown group {group!r}")
    for key in values:
      if key not in DEFAULTS[group]:
        raise SettingsError(f"unknown key {group}.{key}")
  lines = text.splitlines()
  # None writes the default, so the line keeps its comment. A key without a default goes away.
  pending = {
    group: {key: DEFAULTS[group][key] if v is None else v for key, v in values.items()}
    for group, values in changes.items()
  }
  output: list[str] = []
  group = None

  def close(name: str | None) -> None:
    # Keys that the group did not have go after its last line.
    for key, value in pending.pop(name, {}).items():
      if value is not None:
        output.append(f"  {key}: {scalar(value)}")

  for line in lines:
    heading = GROUP_LINE.match(line)
    if heading:
      close(group)
      group = heading.group(1)
      output.append(line)
      continue
    found = KEY_LINE.match(line)
    if found and group in pending and found.group(2) in pending[group]:
      value = pending[group].pop(found.group(2))
      if value is not None:
        indent, key, comment = found.group(1), found.group(2), found.group(4) or ""
        output.append(f"{indent}{key}: {scalar(value)}{comment}")
      continue
    output.append(line)
  close(group)
  for name in list(pending):
    if any(value is not None for value in pending[name].values()):
      output.append(f"{name}:")
      close(name)
  return "\n".join(output) + "\n"
