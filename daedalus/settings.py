"""Router settings from `config/daedalus.yml`, over the built-in defaults."""

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path("config/daedalus.yml")
DEFAULTS: dict[str, dict[str, Any]] = {
  "timeouts": {"request": 600.0, "wait": 60.0, "slow": None},
  "session_affinity": {"enabled": True, "idle": 3600.0},
  "weights": {
    "enabled": True,
    "success": 1.5,
    "fault": 0.5,
    "slow": 0.75,
    "hourly": 1.2,
  },
}


class SettingsError(ValueError):
  """A settings file with an unknown key or a wrong value."""


def check(group: str, key: str, value: Any) -> Any:
  """The value when its type fits the default, else a `SettingsError`."""
  name = f"{group}.{key}"
  if key == "enabled":
    if not isinstance(value, bool):
      raise SettingsError(f"{name} must be true or false")
    return value
  if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
    raise SettingsError(f"{name} must be a number above 0")
  return float(value)


def load(path: Path | str = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
  """The settings: each value in the file replaces its default."""
  merged = {group: dict(values) for group, values in DEFAULTS.items()}
  target = Path(path)
  raw = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
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
