"""Read named run state for task blocking."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path


def process_identity(pid: int) -> tuple[str, str, str] | None:
  """Return Linux process state, start time, and boot ID when available."""
  try:
    fields = (
      Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    )
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    return fields[0], fields[19], boot
  except (OSError, IndexError):
    return None


def run_is_running(record: dict) -> bool:
  """Mark a missing verdict as failed when its recorded process is dead."""
  exit_path = Path(record["exit_path"])
  if exit_path.is_file():
    return False
  pid = record.get("pid")
  if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
    return True
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    pass
  except PermissionError:
    return True
  else:
    identity = process_identity(pid)
    recorded = record.get("process_identity")
    if identity is None or (
      identity[0] != "Z" and (not recorded or list(identity[1:]) == recorded)
    ):
      return True
  # Link a fully written marker without replacing a verdict from the runner.
  fd, name = tempfile.mkstemp(prefix=".dead-run-", dir=exit_path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as marker:
      marker.write("1\n")
    try:
      os.link(name, exit_path)
    except FileExistsError:
      pass
  finally:
    os.unlink(name)
  return False


RUN_RETENTION_SECONDS = 30 * 60


def prune_finished_runs(
  state_dir: str | Path, *, now: float, retention: float = RUN_RETENTION_SECONDS
) -> tuple[str, ...]:
  """Delete finished run files older than the retention window.

  Returns the names whose files went away, so the caller can drop the task
  entry that pointed at them.
  """
  runs = Path(state_dir) / "runs"
  pruned: list[str] = []
  for record_path in sorted(runs.glob("*.json")):
    if record_path.name.startswith("."):
      continue
    record = json.loads(record_path.read_text(encoding="utf-8"))
    exit_path = Path(str(record.get("exit_path", "")))
    if not exit_path.is_file():
      continue
    if now - exit_path.stat().st_mtime < retention:
      continue
    for path in (record.get("log_path"), record.get("exit_path")):
      if path:
        Path(str(path)).unlink(missing_ok=True)
    record_path.unlink(missing_ok=True)
    pruned.append(record_path.stem)
  return tuple(pruned)


class FileGateStatus:
  """Use the run record and exit marker in the state directory."""

  def __init__(self, state_dir: str | Path) -> None:
    self._runs = Path(state_dir) / "runs"

  def is_running(self, name: str) -> bool:
    record_path = self._runs / f"{name}.json"
    if not record_path.is_file():
      return False
    record = json.loads(record_path.read_text(encoding="utf-8"))
    return run_is_running(record)

  def running_run_ids(self) -> tuple[tuple[str, str], ...]:
    """List live run names and their stable exit-marker identifiers."""
    active: list[tuple[str, str]] = []
    for path in sorted(self._runs.glob("*.json")):
      if path.name.startswith("."):
        continue
      record = json.loads(path.read_text(encoding="utf-8"))
      if not run_is_running(record):
        continue
      exit_name = Path(str(record.get("exit_path", ""))).name
      if exit_name:
        active.append((path.stem, exit_name))
    return tuple(active)

  def running_names(self) -> tuple[str, ...]:
    """List live named runs without inspecting temporary record files."""
    return tuple(name for name, _run_id in self.running_run_ids())


class RunCompletionWatcher:
  """Watch atomic exit markers created after this poll starts."""

  def __init__(self, state_dir: str | Path) -> None:
    self._runs = Path(state_dir) / "runs"
    self._seen = {path.name for path in self._runs.glob("*.exit")}

  def finished(self) -> list[tuple[str, int, str]]:
    verdicts: list[tuple[str, int, str]] = []
    for path in sorted(self._runs.glob("*.exit")):
      if path.name in self._seen or path.is_symlink():
        continue
      code = int(path.read_text(encoding="utf-8").strip())
      self._seen.add(path.name)
      # Strip the generated run ID suffix without changing short legacy names.
      name = re.sub(r"-[0-9a-f]{32}$", "", path.stem)
      verdicts.append((name, code, path.stem))
    return verdicts
