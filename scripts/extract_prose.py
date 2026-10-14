"""Collect first-party prose with source anchors and explicit manual-review limits."""

import argparse
import ast
import fnmatch
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDES = (
  ".agents/**",
  ".github/workflows/gemini-release.yml",
  ".github/workflows/gemini-release-evidence.json",
  ".github/workflows/gemini_release.py",
  "node_modules/**",
  "**/node_modules/**",
  "vendor/**",
  "**/vendor/**",
  "ARENA.md",
  "AGENTS.md",
  "**/AGENTS.md",
  "LICENSE*",
  "**/LICENSE*",
  "uv.lock",
  "daedalus/routing/classifier.py",
  "daedalus/routing/artifacts/**",
  "daedalus/dashboard/ui/icons/**",
  "*.png",
  "*.jpg",
  "*.jpeg",
  "*.gif",
  "*.ico",
  "*.woff*",
)

LOCALE_PREFIX = '"use strict";\nglobalThis.DAEDALUS_LOCALE = '

LOCALE_KEY = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*:', re.MULTILINE)


def tracked_files(excludes: list[str] | tuple[str, ...] = ()) -> list[Path]:
  """Select tracked paths by exclusion, including files in new folders."""
  listed = subprocess.run(
    ["git", "-C", str(ROOT), "ls-files", "-z"],
    capture_output=True,
    text=True,
    check=True,
  ).stdout
  return [
    Path(name)
    for name in listed.split("\0")
    if name
    and not any(
      fnmatch.fnmatchcase(name, pattern) for pattern in (*EXCLUDES, *excludes)
    )
  ]


def extract(
  path: Path, *, strings: bool = True
) -> tuple[list[tuple[int, str, str]], list[str]]:
  """Extract text without execution, with source anchors and coverage limits."""
  if path.is_symlink():
    return [], ["Symbolic link. Review its target manually."]
  if path.suffix not in (".py", ".md") and path.name != "en.js":
    return [], ["Unsupported format. Review its prose manually."]
  source = path.read_text(encoding="utf-8")
  if path.suffix == ".md":
    return [(1, "markdown", source)], []
  if path.name == "en.js":
    if not source.startswith(LOCALE_PREFIX) or not source.endswith(";\n"):
      raise ValueError("Unsupported locale wrapper.")
    pairs = json.loads(source[len(LOCALE_PREFIX) : -2], object_pairs_hook=list)
    if not isinstance(pairs, list) or not all(
      isinstance(pair, tuple)
      and len(pair) == 2
      and all(isinstance(value, str) for value in pair)
      for pair in pairs
    ):
      raise ValueError("Locale keys and values must be strings.")
    names = [key for key, _ in pairs]
    matches = list(LOCALE_KEY.finditer(source))
    if len(set(names)) != len(names) or [json.loads(m[1]) for m in matches] != names:
      raise ValueError("Locale entries need unique keys, one per line.")
    return [
      (source.count("\n", 0, match.start(1)) + 1, "locale", value)
      for match, (_, value) in zip(matches, pairs, strict=True)
    ], []
  tree = ast.parse(source, filename=str(path))
  parts = []
  seen = set()
  for node in ast.walk(tree):
    if isinstance(
      node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ):
      first = node.body[0] if node.body else None
      if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
      ):
        parts.append((first.lineno, "docstring", first.value.value))
        seen.add(id(first.value))
  for token in tokenize.generate_tokens(io.StringIO(source).readline):
    if token.type == tokenize.COMMENT:
      parts.append((token.start[0], "comment", token.string.lstrip("# ")))
  if strings:
    for node in ast.walk(tree):
      if isinstance(node, ast.JoinedStr) and id(node) not in seen:
        text = "".join(
          part.value if isinstance(part, ast.Constant) else "{value}"
          for part in node.values
        )
        parts.append((node.lineno, "candidate", text))
        seen.update(id(part) for part in ast.walk(node))
    for node in ast.walk(tree):
      if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in seen
      ):
        parts.append((node.lineno, "candidate", node.value))
  manual = (
    ["Runtime string candidates need context review."]
    if any(kind == "candidate" for _, kind, _ in parts)
    else []
  )
  return sorted(parts), manual


def prose(path: Path) -> str:
  """Collect Python comments and docstrings for the existing structural check."""
  return "\n".join(text for _, _, text in extract(path, strings=False)[0])


def page_files() -> list[Path]:
  """The tracked Markdown files after the shared exclusions."""
  return [ROOT / path for path in tracked_files() if path.suffix == ".md"]


def collect(paths: list[Path]) -> list[dict]:
  """Return prose parts and coverage limits without running source files."""
  records = []
  for relative in paths:
    path = ROOT / relative
    try:
      parts, manual = extract(path)
    except (OSError, SyntaxError, UnicodeError, ValueError, TypeError) as exc:
      parts, manual = [], [f"Extraction error: {exc}"]
    records.append(
      {
        "path": str(path),
        "parts": [
          {"line": line, "kind": kind, "text": text} for line, kind, text in parts
        ],
        "manual": manual,
      }
    )
  return records


def main() -> int:
  """Write a JSON inventory and return 2 when files need manual review."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--exclude",
    action="append",
    default=[],
    metavar="GLOB",
    help="Exclude another tracked path pattern.",
  )
  args = parser.parse_args()
  records = collect(tracked_files(args.exclude))
  manual = sum(bool(record["manual"]) for record in records)
  print(json.dumps({"files": records, "manual_files": manual}, indent=2))
  return 2 if manual else 0


if __name__ == "__main__":
  sys.exit(main())
