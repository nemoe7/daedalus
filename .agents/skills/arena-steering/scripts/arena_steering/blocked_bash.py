"""Append, read, and dismiss entries in the local blocked Bash audit."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LOG_NAME = "blocked-bash.jsonl"
LOCK_NAME = ".blocked-bash.lock"


@contextmanager
def _locked_log(state_dir: Path) -> Iterator[None]:
  """Keep a separate lock inode stable across atomic log replacements."""
  fd = -1
  try:
    flags = (
      os.O_RDWR
      | os.O_CREAT
      | getattr(os, "O_NONBLOCK", 0)
      | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(state_dir / LOCK_NAME, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise RuntimeError("Blocked Bash log is unavailable")
    os.fchmod(fd, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  except OSError as exc:
    raise RuntimeError("Blocked Bash log is unavailable") from exc
  finally:
    if fd >= 0:
      os.close(fd)


def _read_lines(state_dir: Path) -> list[bytes]:
  flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
  try:
    fd = os.open(state_dir / LOG_NAME, flags)
  except FileNotFoundError:
    return []
  except OSError as exc:
    raise RuntimeError("Blocked Bash log is unavailable") from exc
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise RuntimeError("Blocked Bash log is not a regular file")
    with os.fdopen(fd, "rb") as log:
      fd = -1
      return log.readlines()
  except OSError as exc:
    raise RuntimeError("Blocked Bash log is unavailable") from exc
  finally:
    if fd >= 0:
      os.close(fd)


def _entry_id(number: int, raw_line: bytes) -> str:
  return hashlib.sha256(number.to_bytes(8, "big") + raw_line).hexdigest()


def _parse_entries(lines: list[bytes]) -> list[dict[str, str]]:
  entries: list[dict[str, str]] = []
  for number, raw_line in enumerate(lines, start=1):
    try:
      entry = json.loads(raw_line.decode("utf-8"))
    except UnicodeError as exc:
      raise RuntimeError("Blocked Bash log is not UTF-8") from exc
    except json.JSONDecodeError as exc:
      raise RuntimeError(f"Invalid blocked Bash log entry at line {number}") from exc
    if not isinstance(entry, dict) or any(
      not isinstance(entry.get(field), str)
      for field in ("at", "line", "command", "blocked_by")
    ):
      raise RuntimeError(f"Invalid blocked Bash log entry at line {number}")
    entry["id"] = _entry_id(number, raw_line)
    entries.append(entry)
  return entries


def append_blocked_bash(
  state_dir: Path, *, line: str, command: str, blocked_by: str
) -> None:
  """Record the full line and blocked command without altering the guard's verdict."""
  state_dir.mkdir(parents=True, exist_ok=True)
  row = {
    "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    "line": line,
    "command": command,
    "blocked_by": blocked_by,
  }
  with _locked_log(state_dir):
    flags = (
      os.O_WRONLY
      | os.O_CREAT
      | os.O_APPEND
      | getattr(os, "O_NONBLOCK", 0)
      | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(state_dir / LOG_NAME, flags, 0o600)
    try:
      if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise RuntimeError("Blocked Bash log is not a regular file")
      os.fchmod(fd, 0o600)
      with os.fdopen(fd, "a", encoding="utf-8") as log:
        fd = -1
        log.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
      if fd >= 0:
        os.close(fd)


def read_blocked_bash(state_dir: Path) -> list[dict[str, str]]:
  """Read every local audit entry without following links or hiding bad rows."""
  if not state_dir.is_dir():
    return []
  with _locked_log(state_dir):
    return _parse_entries(_read_lines(state_dir))


def dismiss_blocked_bash(state_dir: Path, entry_id: str) -> None:
  """Atomically remove exactly one validated JSONL row, preserving other raw lines."""
  if not isinstance(entry_id, str) or re.fullmatch(r"[0-9a-f]{64}", entry_id) is None:
    raise ValueError("Invalid blocked Bash entry ID")
  if not state_dir.is_dir():
    raise KeyError("Blocked Bash entry no longer present")
  with _locked_log(state_dir):
    lines = _read_lines(state_dir)
    entries = _parse_entries(lines)
    index = next(
      (i for i, entry in enumerate(entries) if entry["id"] == entry_id), None
    )
    if index is None:
      raise KeyError("Blocked Bash entry no longer present")
    fd = -1
    temp_path: str | None = None
    try:
      fd, temp_path = tempfile.mkstemp(prefix=".blocked-bash-", dir=state_dir)
      os.fchmod(fd, 0o600)
      with os.fdopen(fd, "wb") as replacement:
        fd = -1
        replacement.writelines(lines[:index] + lines[index + 1 :])
        replacement.flush()
        os.fsync(replacement.fileno())
      os.replace(temp_path, state_dir / LOG_NAME)
      temp_path = None
      dir_fd = os.open(state_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
      try:
        os.fsync(dir_fd)
      finally:
        os.close(dir_fd)
    except OSError as exc:
      raise RuntimeError("Blocked Bash log is unavailable") from exc
    finally:
      if fd >= 0:
        os.close(fd)
      if temp_path is not None:
        Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
  import sys

  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  if len(sys.argv) != 4:
    raise SystemExit("Expected state directory, full line, and blocked command")
  append_blocked_bash(
    Path(sys.argv[1]),
    line=sys.argv[2],
    command=sys.argv[3],
    blocked_by="hook-unavailable",
  )
