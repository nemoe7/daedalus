"""Lint the prose in Python comments and docstrings. Run: python scripts/lint_prose.py"""

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

LINTER = Path(".agents/skills/asd-ste100/scripts/ste-lint.py")
TARGETS = ("daedalus", "tests", "scripts")


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
  for token in tokenize.generate_tokens(io.StringIO(source).readline):
    if token.type == tokenize.COMMENT:
      lines.append(token.string.lstrip("# ").strip())
  return "\n".join(line for line in lines if line)


def main() -> int:
  """Lint every target file, and fail when the linter fails."""
  failures = 0
  for folder in TARGETS:
    for path in sorted(Path(folder).glob("*.py")):
      text = prose(path)
      if not text:
        continue
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
  if failures:
    print(f"{failures} files carry lint findings")
    return 1
  print("ok: comments and docstrings lint clean")
  return 0


if __name__ == "__main__":
  sys.exit(main())
