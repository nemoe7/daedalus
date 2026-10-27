"""Check the version of each module that a change touched."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Folders that hold a versioned module: Open WebUI plugins and hook files.
FOLDERS = ("integrations/openwebui/", "hooks/")

# The folders whose `.py` file always carries a version, so a new file there needs a line.
REQUIRED = (
  "integrations/openwebui/tools/",
  "integrations/openwebui/functions/",
  "hooks/",
)

# A plugin frontmatter holds `version: 1.2.3`, and a hook frontmatter holds `# version: 1.2.3`.
VERSION = re.compile(
  r"^(?:#\s*)?version:[ \t]*([0-9]+(?:\.[0-9]+)*)[ \t]*$", re.MULTILINE
)


def watched(path: str) -> bool:
  """True when the path sits in a folder that holds a versioned module."""
  return path.startswith(FOLDERS) and path.endswith((".py", ".md"))


def requires_version(path: str) -> bool:
  """True when a new file at this path must carry a version line."""
  return path.startswith(REQUIRED) and path.endswith(".py")


def version_of(text: str | None) -> tuple[int, ...] | None:
  """The dotted version of a file text, or None when it holds no version line."""
  if not text:
    return None
  found = VERSION.search(text)
  return tuple(int(part) for part in found[1].split(".")) if found else None


def dotted(found: tuple[int, ...] | None) -> str:
  """The text of a version tuple, or `none` for no version line."""
  return ".".join(str(part) for part in found) if found else "none"


def failures(
  rows: list[tuple[str, tuple[int, ...] | None, tuple[int, ...] | None]],
) -> list[str]:
  """The complaint of each changed module whose version did not move up."""
  out: list[str] = []
  for path, before, after in rows:
    if before is None:
      if after is None and requires_version(path):
        out.append(f"{path} is new and holds no version line")
    elif after is None:
      out.append(f"{path} lost its version line")
    elif after == before:
      out.append(f"{path} changed and its version stayed at {dotted(after)}")
    elif after < before:
      out.append(f"{path} changed and its version went back to {dotted(after)}")
  return out


def git(*args: str) -> str:
  """The stdout of a checked Git call. A failed read raises an error."""
  return subprocess.run(
    ["git", *args], capture_output=True, text=True, check=True
  ).stdout


def rows_for(
  base: str, head: str
) -> list[tuple[str, tuple[int, ...] | None, tuple[int, ...] | None]]:
  """Version of each changed module at merge base and at head."""
  merge = git("merge-base", base, head).strip()
  rows: list[tuple[str, tuple[int, ...] | None, tuple[int, ...] | None]] = []
  # A rename is a deletion and an addition. NUL separators keep each path intact.
  changed = git(
    "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", merge, head, "--"
  ).split("\0")[:-1]
  for status, path in zip(changed[::2], changed[1::2], strict=True):
    if status == "D" or not watched(path):
      continue
    before = None if status == "A" else version_of(git("show", f"{merge}:{path}"))
    after = version_of(git("show", f"{head}:{path}"))
    rows.append((path, before, after))
  return rows


def main(argv: list[str] | None = None) -> int:
  """Print each changed module with its two versions, and fail on a version that did not move up."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--base", default="origin/main", help="the base branch or commit")
  parser.add_argument("--head", default="HEAD", help="the head branch or commit")
  args = parser.parse_args(argv)
  try:
    rows = rows_for(args.base, args.head)
  except (OSError, subprocess.CalledProcessError) as error:
    detail = (getattr(error, "stderr", None) or str(error)).strip()
    print(f"Git read failed: {detail}", file=sys.stderr)
    return 1
  for path, before, after in rows:
    print(f"{path}: {dotted(before)} -> {dotted(after)}")
  if not rows:
    print("no versioned module changed")
  found = failures(rows)
  for line in found:
    print(line, file=sys.stderr)
  return 1 if found else 0


if __name__ == "__main__":
  raise SystemExit(main())
