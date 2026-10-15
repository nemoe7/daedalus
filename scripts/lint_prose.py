"""Check first-party prose, with --scan for string candidates and coverage limits."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if __package__:
  from . import extract_prose
  from .check_pr import run_ste_lint
else:
  import extract_prose
  from check_pr import run_ste_lint

ROOT = extract_prose.ROOT
EXCLUDES = extract_prose.EXCLUDES
LOCALE_PREFIX = extract_prose.LOCALE_PREFIX
LOCALE_KEY = extract_prose.LOCALE_KEY
extract = extract_prose.extract
page_files = extract_prose.page_files
prose = extract_prose.prose
tracked_files = extract_prose.tracked_files

LINTER = ROOT / ".agents/skills/asd-ste100/scripts/ste-lint.py"
ADR_REFERENCE = re.compile(r"\bADR[- ]?\d|docs/adr")
FENCE = re.compile(r"^\s*(```|~~~)")
LIST = re.compile(r"^\s*(\d+[.)]|[-*+])\s")
BLOCK_START = ("#", "|", ">", "<")
SHORT_FORMS = ("e.g.", "i.e.", "etc.", "vs.", "cf.")
DOT = "\u2022"
SENTENCE_LIMIT = 4
WORD_LIMIT = 25
INLINE_CODE = re.compile(r"`[^`]*`")


def scan_file(path: Path) -> dict:
  """Keep every STE finding, including advisories, and name manual-review work."""
  record = extract_prose.collect([path])[0]
  result = {
    "path": record["path"],
    "findings": [],
    "manual": record["manual"],
    "words": 0,
  }
  try:
    lines, anchors = [], []
    for part in record["parts"]:
      start, kind, text = part["line"], part["kind"], part["text"]
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
  """Check comments and Markdown, or scan string candidates with --scan."""
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
      passed, output = run_ste_lint(text)
      if not passed:
        failures += 1
        print(f"--- {path}")
        print(output)
  pages = [ROOT / path for path in paths if path.suffix == ".md"]
  failures += scan(pages) + long_blocks(pages) + long_sentences(pages)
  if failures:
    print(f"Structural check failures: {failures}.")
    return 1
  print("Default checks passed. Use --scan for string candidates and coverage limits.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
