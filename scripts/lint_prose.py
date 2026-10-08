"""Lint the prose in Python comments, docstrings and the shipped pages. Run: python scripts/lint_prose.py"""

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

LINTER = Path(".agents/skills/asd-ste100/scripts/ste-lint.py")
TARGETS = ("daedalus", "tests", "scripts")
ADR_REFERENCE = re.compile(r"\bADR[- ]?\d|docs/adr")
FENCE = re.compile(r"^\s*(```|~~~)")
LIST = re.compile(r"^\s*(\d+[.)]|[-*+])\s")
BLOCK_START = ("#", "|", ">", "<")
SHORT_FORMS = ("e.g.", "i.e.", "etc.", "vs.", "cf.")
DOT = "\u2022"
SENTENCE_LIMIT = 4


def prose(path: Path) -> str:
  """Collect the comments and docstrings of one file, one line each."""
  source = path.read_text(encoding="utf-8")
  lines: list[str] = []
  for node in ast.walk(ast.parse(source)):
    if isinstance(
      node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    ):
      body = node.body
      first = body[0] if body else None
      if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
      ):
        lines.extend(first.value.value.strip().splitlines())
  lines.extend(
    token.string.lstrip("# ").strip()
    for token in tokenize.generate_tokens(io.StringIO(source).readline)
    if token.type == tokenize.COMMENT
  )
  return "\n".join(line for line in lines if line)


def page_files() -> list[Path]:
  """The shipped pages: each tracked Markdown file, but the vendored and the license ones."""
  listed = subprocess.run(
    ["git", "ls-files", "*.md"], capture_output=True, text=True, check=True
  ).stdout
  return [
    Path(name)
    for name in listed.split()
    if not name.startswith(".agents/") and name != "LICENSE.md"
  ]


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
      line.lstrip().startswith(BLOCK_START) or LIST.match(line) or line.startswith("    ")
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


def long_blocks() -> int:
  """Count the blocks of the shipped pages that run past the sentence limit."""
  failures = 0
  for path in page_files():
    for number, text in blocks(path):
      count = sentence_count(text)
      if count > SENTENCE_LIMIT:
        failures += 1
        print(f"--- {path}:{number}\n{count} sentences: {text[:80]}")
  return failures


def main() -> int:
  """Lint every target file, and fail when the linter fails."""
  failures = 0
  for folder in TARGETS:
    for path in sorted(Path(folder).rglob("*.py")):
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
  failures += long_blocks()
  if failures:
    print(f"{failures} files carry lint findings")
    return 1
  print("ok: comments, docstrings and paragraphs lint clean")
  return 0


if __name__ == "__main__":
  sys.exit(main())
