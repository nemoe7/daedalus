"""Vendored nemoizer linter, run way workflow runs it."""

import re
import subprocess
from pathlib import Path

from check_pr import BODY_HEADINGS, validate_pr_body

LINT = Path(".agents/skills/nemoizer/scripts/nemoizer")
TEMPLATE = Path(".github/PULL_REQUEST_TEMPLATE.md")


def test_the_vendored_linter_exists() -> None:
  """The repo copy is the one CI uses. No skill install, no external package."""
  assert LINT.is_file()


def test_the_template_uses_the_exact_canonical_format() -> None:
  """The template carries the three required headings in order, and nothing else."""
  text = TEMPLATE.read_text(encoding="utf-8")
  found = [
    found.group(1) for found in re.finditer(r"^## (.+?)\s*$", text, re.MULTILINE)
  ]
  assert found == list(BODY_HEADINGS[:3])


def test_an_unfilled_template_is_rejected() -> None:
  """A PR that ships the template untouched fails instead of passing."""
  problems = validate_pr_body(TEMPLATE.read_text(encoding="utf-8"))
  assert any("placeholder" in problem.message for problem in problems)


def test_python_comments_and_docstrings_do_not_reference_adrs() -> None:
  """Code comments, docstrings, and tests do not cite architecture decision records."""
  import ast
  import io
  import tokenize

  adr = re.compile(r"\bADR[- ]?\d|docs/adr")
  root = Path(__file__).resolve().parents[2]
  listed = subprocess.run(
    ["git", "-C", str(root), "ls-files", "-z", "*.py"],
    capture_output=True,
    text=True,
    check=True,
  ).stdout
  offenders: list[str] = []
  for name in listed.split("\0"):
    if not name or name.startswith(".agents/") or not (root / name).is_file():
      continue
    source = (root / name).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source, filename=name)):
      if isinstance(
        node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
      ):
        first = node.body[0] if node.body else None
        if (
          isinstance(first, ast.Expr)
          and isinstance(first.value, ast.Constant)
          and isinstance(first.value.value, str)
          and adr.search(first.value.value)
        ):
          offenders.append(f"{name}:{first.lineno}")
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
      if tok.type == tokenize.COMMENT and adr.search(tok.string):
        offenders.append(f"{name}:{tok.start[0]}")
  assert offenders == [], offenders
