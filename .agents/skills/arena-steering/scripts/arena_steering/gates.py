"""Read named run state for task blocking."""

from __future__ import annotations

import json
import re
from pathlib import Path


class FileGateStatus:
  """Use the run record and exit marker in the state directory."""

  def __init__(self, state_dir: str | Path) -> None:
    self._runs = Path(state_dir) / "runs"

  def is_running(self, name: str) -> bool:
    record_path = self._runs / f"{name}.json"
    if not record_path.is_file():
      return False
    record = json.loads(record_path.read_text(encoding="utf-8"))
    return not Path(record["exit_path"]).is_file()

  def running_names(self) -> tuple[str, ...]:
    """List live named runs without inspecting temporary record files."""
    return tuple(
      path.stem
      for path in sorted(self._runs.glob("*.json"))
      if not path.name.startswith(".") and self.is_running(path.stem)
    )


class RunCompletionWatcher:
  """Watch atomic exit markers created after this poll starts."""

  def __init__(self, state_dir: str | Path) -> None:
    self._runs = Path(state_dir) / "runs"
    self._seen = {path.name for path in self._runs.glob("*.exit")}

  def finished(self) -> list[tuple[str, int]]:
    verdicts: list[tuple[str, int]] = []
    for path in sorted(self._runs.glob("*.exit")):
      if path.name in self._seen or path.is_symlink():
        continue
      code = int(path.read_text(encoding="utf-8").strip())
      self._seen.add(path.name)
      # Strip the generated run ID suffix without changing short legacy names.
      name = re.sub(r"-[0-9a-f]{32}$", "", path.stem)
      verdicts.append((name, code))
    return verdicts
