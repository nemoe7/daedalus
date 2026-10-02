"""The vendored ASD-STE linter, run the way the workflow runs it."""

import re
import subprocess
import sys
from pathlib import Path

from check_pr import BODY_HEADINGS, validate_pr_body

LINT = Path(".agents/skills/asd-ste100/scripts/ste-lint.py")
TEMPLATE = Path(".github/PULL_REQUEST_TEMPLATE.md")

CLEAN = "The workflow builds the image. The job pushes the image to GHCR.\n"
DIRTY = (
  "The images that were built by the workflow are being pushed to the registry "
  "by the job in order to make the release available to the users.\n"
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    [sys.executable, str(LINT), *args],
    capture_output=True,
    text=True,
    check=False,
  )


def test_the_vendored_linter_exists() -> None:
  """The repository copy is the one CI uses. No skill install, no external package."""
  assert LINT.is_file()


def test_the_vendored_linter_passes_its_own_self_test() -> None:
  """The self-test runs when the linter offers one."""
  assert run("--selftest").returncode == 0


def test_the_vendored_linter_accepts_clean_prose(tmp_path: Path) -> None:
  """Short sentences in the active voice pass."""
  target = tmp_path / "clean.md"
  target.write_text(CLEAN, encoding="utf-8")
  assert run(str(target)).returncode == 0


def test_the_vendored_linter_rejects_long_passive_prose(tmp_path: Path) -> None:
  """A long sentence in the passive voice fails the check."""
  target = tmp_path / "dirty.md"
  target.write_text(DIRTY, encoding="utf-8")
  result = run(str(target))
  assert result.returncode == 1
  assert "violation" in result.stdout.lower() or "violation" in result.stderr.lower()


def test_the_template_uses_the_exact_canonical_format() -> None:
  """The template carries the five headings in order, and nothing else."""
  text = TEMPLATE.read_text(encoding="utf-8")
  found = [
    found.group(1) for found in re.finditer(r"^## (.+?)\s*$", text, re.MULTILINE)
  ]
  assert found == list(BODY_HEADINGS[:3])


def test_an_unfilled_template_is_rejected() -> None:
  """A pull request that ships the template untouched fails instead of passing."""
  problems = validate_pr_body(TEMPLATE.read_text(encoding="utf-8"))
  assert any("placeholder" in problem.message for problem in problems)
