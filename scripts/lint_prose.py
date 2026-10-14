"""Check first-party prose. Use --scan for STE findings and manual-review coverage."""

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
LINTER = ROOT / ".agents/skills/asd-ste100/scripts/ste-lint.py"
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
ADR_REFERENCE = re.compile(r"\bADR[- ]?\d|docs/adr")
FENCE = re.compile(r"^\s*(```|~~~)")
LIST = re.compile(r"^\s*(\d+[.)]|[-*+])\s")
BLOCK_START = ("#", "|", ">", "<")
SHORT_FORMS = ("e.g.", "i.e.", "etc.", "vs.", "cf.")
DOT = "\u2022"
SENTENCE_LIMIT = 4
WORD_LIMIT = 25
INLINE_CODE = re.compile(r"`[^`]*`")


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


def scan_file(path: Path) -> dict:
  """Keep every STE finding, including advisories, and name manual-review work."""
  result = {"path": str(path), "findings": [], "manual": [], "words": 0}
  try:
    parts, result["manual"] = extract(path)
    lines, anchors = [], []
    for start, kind, text in parts:
      for offset, line in enumerate(text.splitlines()):
        lines.append(line)
        anchors.append((start + offset if kind == "markdown" else start, kind))
    if not lines:
      return result
    run = subprocess.run(
      [sys.executable, str(LINTER), "--json"],
      input="\n".join(lines),
      capture_output=True,
      text=True,
      check=False,
    )
    if run.returncode not in (0, 1):
      raise ValueError(
        f"STE process failed with exit {run.returncode}: {run.stderr.strip()}"
      )
    report = json.loads(run.stdout)
    result["words"] = report["words"]
    for finding in report["violations"]:
      line, kind = anchors[finding["line"] - 1]
      result["findings"].append(
        {
          "line": line,
          "kind": kind,
          "rule": finding["rule"],
          "level": finding["level"],
          "message": finding["message"],
        }
      )
  except (
    OSError,
    SyntaxError,
    UnicodeError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
  ) as exc:
    result["manual"].append(f"Scan error: {exc}")
  return result


def scan(paths: list[Path], as_json: bool = False) -> int:
  """Report findings and incomplete coverage without treating either as clean."""
  results = [scan_file(ROOT / path) for path in paths]
  findings = sum(len(row["findings"]) for row in results)
  manual = sum(bool(row["manual"]) for row in results)
  if as_json:
    print(
      json.dumps(
        {"files": results, "findings": findings, "manual_files": manual}, indent=2
      )
    )
  else:
    for row in results:
      for finding in row["findings"]:
        print(
          f"{row['path']}:{finding['line']}: {finding['rule']} [{finding['kind']}]: {finding['message']}"
        )
      for reason in row["manual"]:
        print(f"{row['path']}: manual review: {reason}")
    print(
      f"{len(results)} files scanned. {findings} findings. {manual} files need manual review."
    )
  return 1 if findings else 2 if manual else 0


def blocks(path: Path) -> list[tuple[int, str]]:
  """The prose paragraphs of 1 page: the first line number and the text, with the rest out."""
  found: list[tuple[int, str]] = []
  held: list[str] = []
  start, fence, skip = 0, False, False
  for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if FENCE.match(line):
      fence = not fence
      held = []
      continue
    if fence:
      continue
    if not line.strip():
      if held:
        found.append((start, " ".join(held)))
        held = []
      skip = False
      continue
    if skip:
      continue
    if not held and (
      line.lstrip().startswith(BLOCK_START)
      or LIST.match(line)
      or line.startswith("    ")
    ):
      # A heading, a table row, a list item or an indented block is not a paragraph.
      skip = True
      continue
    if not held:
      start = number
    held.append(line.strip())
  if held:
    found.append((start, " ".join(held)))
  return found


def sentence_count(text: str) -> int:
  """The sentences of 1 block, with the code spans and the short forms out."""
  clean = re.sub(r"`[^`]*`", "code", text)
  clean = re.sub(r"(\d)\.(\d)", rf"\1{DOT}\2", clean)
  for short in SHORT_FORMS:
    clean = clean.replace(short, short.replace(".", DOT))
  return len([part for part in re.split(r"[.!?]+(?=\s|$)", clean) if part.strip()])


def long_blocks(paths: list[Path] | None = None) -> int:
  """Count the blocks of the shipped pages that run past the sentence limit."""
  failures = 0
  for path in page_files() if paths is None else paths:
    for number, text in blocks(path):
      count = sentence_count(text)
      if count > SENTENCE_LIMIT:
        failures += 1
        print(f"--- {path}:{number}\n{count} sentences: {text[:80]}")
  return failures


def long_sentences(paths: list[Path] | None = None) -> int:
  """Count the shipped-page sentences that run past the word limit of the linter."""
  failures = 0
  for path in page_files() if paths is None else paths:
    for number, text in blocks(path):
      for sentence in re.split(r"(?<=[.!?])\s+", INLINE_CODE.sub("", text)):
        count = len(sentence.split())
        if count > WORD_LIMIT:
          failures += 1
          print(f"--- {path}:{number}\n{count} words: {sentence.strip()[:90]}")
  return failures


def main() -> int:
  """Keep the structural command and offer a full STE inventory with --scan."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--scan",
    action="store_true",
    help="Scan prose and string candidates. List coverage limits.",
  )
  parser.add_argument(
    "--exclude",
    action="append",
    default=[],
    metavar="GLOB",
    help="Exclude another tracked path pattern.",
  )
  parser.add_argument(
    "--json", action="store_true", help="Write the full scan as JSON. Requires --scan."
  )
  args = parser.parse_args()
  if args.json and not args.scan:
    parser.error("--json requires --scan")
  paths = tracked_files(args.exclude)
  if args.scan:
    return scan(paths, args.json)
  failures = 0
  for relative in paths:
    if relative.suffix == ".py":
      path = ROOT / relative
      text = prose(path)
      if not text:
        continue
      if ADR_REFERENCE.search(text):
        failures += 1
        print(f"--- {path}\nA comment or docstring refers to an ADR.")
      run = subprocess.run(
        [sys.executable, str(LINTER)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
      )
      if run.returncode != 0:
        failures += 1
        print(f"--- {path}")
        print(run.stdout.strip())
  pages = [ROOT / path for path in paths if path.suffix == ".md"]
  failures += long_blocks(pages) + long_sentences(pages)
  if failures:
    print(f"Structural check failures: {failures}.")
    return 1
  print("Structural checks passed. Use --scan for STE findings and coverage limits.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
