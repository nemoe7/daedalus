"""Read pinned PR Git objects as data without checking out PR code."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SHA = re.compile(r"[0-9a-f]{40}")


def _git(*args: str) -> bytes:
  return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def fetch_head(number: int, expected: str) -> None:
  """Fetch PR objects and reject a changed head."""
  if number < 1 or not SHA.fullmatch(expected):
    raise ValueError("Use a positive PR number and a full lowercase SHA.")
  ref = f"refs/pr-check/{number}"
  subprocess.run(
    [
      "git",
      "-c",
      "credential.helper=",
      "-c",
      "credential.helper=!gh auth git-credential",
      "fetch",
      "--no-tags",
      "origin",
      f"+refs/pull/{number}/head:{ref}",
    ],
    check=True,
  )
  actual = _git("rev-parse", ref).decode("ascii").strip()
  if actual != expected:
    raise ValueError(f"The PR head changed. Expected {expected}, found {actual}.")


def export_tree(sha: str, directory: Path) -> None:
  """Write regular Git blobs into a new directory without filters or hooks."""
  if not SHA.fullmatch(sha):
    raise ValueError("Use a full lowercase SHA.")
  entries: list[tuple[str, str]] = []
  for record in _git("ls-tree", "-rz", "--full-tree", sha).split(b"\0"):
    if not record:
      continue
    metadata, raw_name = record.split(b"\t", 1)
    mode, kind, blob = metadata.split()
    name = os.fsdecode(raw_name)
    path = PurePosixPath(name)
    if mode not in (b"100644", b"100755") or kind != b"blob":
      raise ValueError(f"PR scan input must be a regular file: {name!r}")
    if path.is_absolute() or ".." in path.parts:
      raise ValueError(f"PR scan path escapes its directory: {name!r}")
    entries.append((name, blob.decode("ascii")))
  directory.mkdir()
  root = directory.resolve()
  for name, blob in entries:
    path = directory / name
    if not path.resolve().is_relative_to(root):
      raise ValueError(f"PR scan path escapes its directory: {name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
      subprocess.run(["git", "cat-file", "blob", blob], stdout=output, check=True)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("number", type=int, help="PR number")
  parser.add_argument("sha", help="full PR head SHA")
  parser.add_argument("--output", type=Path, help="new directory for regular file data")
  args = parser.parse_args(argv)
  try:
    fetch_head(args.number, args.sha)
    if args.output is not None:
      export_tree(args.sha, args.output)
  except (OSError, ValueError, subprocess.CalledProcessError) as error:
    print(f"PR input: {error}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
