"""Check commit subjects, PR titles, PR bodies, and prose without changing input."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STE_LINT = ROOT / ".agents" / "skills" / "asd-ste100" / "scripts" / "ste-lint.py"

ALLOWED_TYPES = (
  "feat",
  "fix",
  "refactor",
  "perf",
  "style",
  "docs",
  "test",
  "build",
  "chore",
  "ci",
  "revert",
)

SUBJECT_LIMIT = 72


SUBJECT_RE = re.compile(
  r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\s]+)\))?(?P<breaking>!)?: (?P<description>.+)$"
)
H2_RE = re.compile(r"^##\s+(?P<title>.*\S)\s*$")
H3_RE = re.compile(r"^#{3,}\s+\S")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HTML_COMMENT_RE = re.compile(r"<!--|--!?>")
PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")
LIST_ITEM_RE = re.compile(r"^-\s+(?P<body>\S.*)$")
CHECKLIST_RE = re.compile(r"^-\s+\[(?P<mark>[x ])\]\s+(?P<body>\S.*)$")

# The last two headings may stay out of a body when their section holds nothing.
BODY_HEADINGS = ("Summary", "Changes", "Validation", "Breaking Changes", "Related")


@dataclass(frozen=True)
class Failure:
  """One contract violation with enough location data to name it precisely."""

  where: str
  message: str
  line: int | None = None

  def __str__(self) -> str:
    location = self.where if self.line is None else f"{self.where}:{self.line}"
    return f"{location}: {self.message}"


def validate_subject(subject: str, where: str) -> list[Failure]:
  """Check one Conventional Commit subject against the inline rules."""
  failures: list[Failure] = []
  if subject != subject.strip():
    failures.append(Failure(where, "subject cannot start or end with whitespace"))
  if len(subject) > SUBJECT_LIMIT:
    failures.append(
      Failure(
        where, f"subject is {len(subject)} characters. The limit is {SUBJECT_LIMIT}"
      )
    )
  match = SUBJECT_RE.fullmatch(subject)
  if not match:
    failures.append(
      Failure(
        where,
        "subject must match <type>[optional scope][!]: <description>. "
        f"allowed types: {' '.join(ALLOWED_TYPES)}",
      )
    )
    return failures
  commit_type = match.group("type")
  description = match.group("description")
  if commit_type not in ALLOWED_TYPES:
    failures.append(Failure(where, f"unknown type {commit_type!r}"))
  if not description[0].islower():
    failures.append(Failure(where, "description must start with a lowercase letter"))
  if description.endswith("."):
    failures.append(Failure(where, "description must not end with a period"))
  return failures


def validate_commit(message: str, where: str = "commit") -> list[Failure]:
  """Check one commit message against the subject contract."""
  lines = [line for line in message.splitlines() if line.strip()]
  if len(lines) != 1:
    return [Failure(where, "use one subject line and no commit body")]
  return validate_subject(lines[0], where)


def _section_lines(lines: list[str], start: int, end: int) -> list[tuple[int, str]]:
  return [(number, lines[number - 1]) for number in range(start, end + 1)]


def _non_blank(entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
  return [(number, text) for number, text in entries if text.strip()]


def validate_pr_body(text: str) -> list[Failure]:
  """Check the locked PR body heading order and report each failing line."""
  failures: list[Failure] = []
  lines = text.splitlines()
  in_fence = False
  for number, line in enumerate(lines, start=1):
    if FENCE_RE.match(line):
      in_fence = not in_fence
      continue
    if HTML_COMMENT_RE.search(line):
      failures.append(Failure("body", "HTML comments are not allowed", number))
    if not in_fence and PLACEHOLDER_RE.search(line):
      failures.append(Failure("body", "placeholder text is not allowed", number))
    if H3_RE.match(line):
      failures.append(Failure("body", "H3 headings are not allowed", number))

  headings = [
    (number, match.group("title").strip())
    for number, line in enumerate(lines, start=1)
    if (match := H2_RE.match(line))
  ]
  titles = [title for _, title in headings]

  first_heading = headings[0][0] if headings else len(lines) + 1
  if _non_blank([(number, lines[number - 1]) for number in range(1, first_heading)]):
    failures.append(Failure("body", "content before ## Summary", 1))

  optional = [title for title in BODY_HEADINGS[3:] if title in titles]
  if titles != list(BODY_HEADINGS[:3]) + optional:
    failures.append(
      Failure(
        "body",
        f"expected the headings {list(BODY_HEADINGS)} in order, with the last two "
        f"optional when they hold nothing. Found {titles}",
      )
    )
    return failures

  bounds = [number for number, _ in headings]
  sections: dict[str, list[tuple[int, str]]] = {}
  for index, (number, title) in enumerate(headings):
    end = bounds[index + 1] - 1 if index + 1 < len(bounds) else len(lines)
    sections[title] = _section_lines(lines, number + 1, end)

  failures.extend(_check_summary(sections["Summary"]))
  failures.extend(_check_changes(sections["Changes"]))
  failures.extend(_check_validation(sections["Validation"]))
  if "Breaking Changes" in sections:
    failures.extend(
      _check_none_or_bullets("Breaking Changes", sections["Breaking Changes"])
    )
  if "Related" not in sections:
    return failures
  failures.extend(_check_none_or_bullets("Related", sections["Related"]))
  related = _non_blank(sections["Related"])
  if related and related[0][1].strip() == "None" and len(related) > 1:
    failures.append(
      Failure("body", "content after the final ## Related content", related[1][0])
    )
  return failures


def _check_summary(entries: list[tuple[int, str]]) -> list[Failure]:
  failures: list[Failure] = []
  content = _non_blank(entries)
  if not content:
    return [Failure("body: Summary", "the section is empty")]
  paragraphs = 0
  in_paragraph = False
  for number, line in entries:
    if not line.strip():
      in_paragraph = False
      continue
    if not in_paragraph:
      paragraphs += 1
      in_paragraph = True
    if LIST_ITEM_RE.match(line) or H3_RE.match(line) or H2_RE.match(line):
      failures.append(
        Failure("body: Summary", "use one paragraph and no list or heading", number)
      )
  if paragraphs != 1:
    failures.append(
      Failure(
        "body: Summary",
        f"use exactly one paragraph. Found {paragraphs}",
        content[0][0],
      )
    )
  return failures


def _check_changes(entries: list[tuple[int, str]]) -> list[Failure]:
  failures: list[Failure] = []
  bullets = [(number, text) for number, text in entries if text.strip()]
  if not bullets:
    return [Failure("body: Changes", "the section is empty")]
  for number, line in bullets:
    if not LIST_ITEM_RE.match(line):
      failures.append(
        Failure("body: Changes", "each change needs a '- ' bullet", number)
      )
  return failures


def _check_validation(entries: list[tuple[int, str]]) -> list[Failure]:
  failures: list[Failure] = []
  items = [(number, text) for number, text in entries if text.strip()]
  if not items:
    return [Failure("body: Validation", "the section is empty")]
  for number, line in items:
    if not CHECKLIST_RE.match(line):
      failures.append(
        Failure(
          "body: Validation", "each item uses '- [x]' or '- [ ]', lowercase x", number
        )
      )
  return failures


def _check_none_or_bullets(title: str, entries: list[tuple[int, str]]) -> list[Failure]:
  failures: list[Failure] = []
  items = _non_blank(entries)
  if not items:
    return [Failure(f"body: {title}", "the section is empty")]
  if len(items) == 1 and items[0][1].strip() == "None":
    return [
      Failure(
        f"body: {title}",
        "omit the heading when the section holds nothing",
        items[0][0],
      )
    ]
  for number, line in items:
    if not LIST_ITEM_RE.match(line):
      failures.append(
        Failure(
          f"body: {title}", "use one '- ' bullet per entry, or omit the heading", number
        )
      )
  if not any(LIST_ITEM_RE.match(line) for _, line in items):
    failures.append(
      Failure(
        f"body: {title}",
        "use one '- ' bullet per entry, or omit the heading",
        items[0][0],
      )
    )
  return failures


def run_ste_lint(text: str) -> tuple[bool, str]:
  """Check prose with the repository linter and return its result."""
  result = subprocess.run(
    [sys.executable, str(STE_LINT)],
    input=text,
    capture_output=True,
    text=True,
    check=False,
  )
  return result.returncode == 0, (result.stdout + result.stderr).strip()


def _git(*arguments: str) -> str:
  result = subprocess.run(
    ["git", *arguments],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
  )
  if result.returncode:
    raise SystemExit(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
  return result.stdout


def commits_in_range(revision_range: str) -> list[tuple[str, str]]:
  """Read each real commit message without treating its bytes as record markers."""
  shas = _git("rev-list", "--reverse", revision_range).splitlines()
  return [
    (sha, _git("show", "--no-patch", "--format=%B", sha).rstrip("\n")) for sha in shas
  ]


def breaking_section_holds_nothing(text: str) -> bool:
  """Say whether the body omits the Breaking Changes heading or keeps it at None."""
  lines = text.splitlines()
  headings = [
    (number, match.group("title").strip())
    for number, line in enumerate(lines, start=1)
    if (match := H2_RE.match(line))
  ]
  titles = [title for _, title in headings]
  if "Breaking Changes" not in titles:
    return True
  index = titles.index("Breaking Changes")
  start = headings[index][0] + 1
  end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
  entries = _non_blank(_section_lines(lines, start, end))
  return not entries or (len(entries) == 1 and entries[0][1].strip() == "None")


def validate_breaking_crosscheck(subjects: list[str], body: str) -> list[Failure]:
  """Fail a breaking-marked commit that meets an empty Breaking Changes section."""
  banged = [
    subject
    for subject in subjects
    if (match := SUBJECT_RE.fullmatch(subject)) and match.group("breaking")
  ]
  if banged and breaking_section_holds_nothing(body):
    return [
      Failure(
        "PR body",
        "a commit carries the breaking marker, so Breaking Changes must list it",
      )
    ]
  return []


def check(
  revision_range: str | None,
  pr_title: str | None,
  body_file: str | None,
  ste: bool,
) -> list[Failure]:
  """Collect every contract failure for the given inputs."""
  failures: list[Failure] = []
  subjects: list[str] = []
  if revision_range:
    for sha, message in commits_in_range(revision_range):
      lines = [line for line in message.splitlines() if line.strip()]
      subjects.append(lines[0] if lines else message)
      failures.extend(validate_commit(message, where=f"commit {sha[:7]}"))
  if pr_title is not None:
    failures.extend(validate_subject(pr_title, "PR title"))
  if body_file is not None:
    text = Path(body_file).read_text(encoding="utf-8")
    failures.extend(validate_pr_body(text))
    failures.extend(validate_breaking_crosscheck(subjects, text))
    if ste:
      passed, output = run_ste_lint(text)
      if not passed:
        failures.append(Failure("PR body", f"STE lint failed:\n{output}"))
  return failures


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--range", dest="revision_range", help="commit range, BASE..HEAD")
  parser.add_argument("--pr-title", help="PR title to validate as a commit subject")
  parser.add_argument("--body-file", help="file holding the complete PR body")
  parser.add_argument(
    "--no-ste",
    action="store_true",
    help="skip the ASD-STE lint of the PR body",
  )
  arguments = parser.parse_args(argv)
  if not (arguments.revision_range or arguments.pr_title or arguments.body_file):
    parser.error("give --range, --pr-title, --body-file, or several")
  failures = check(
    arguments.revision_range,
    arguments.pr_title,
    arguments.body_file,
    not arguments.no_ste,
  )
  for failure in failures:
    print(f"::error::{failure}")
  if failures:
    print(f"{len(failures)} contract violation(s)")
    return 1
  print("Pull request contract passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
