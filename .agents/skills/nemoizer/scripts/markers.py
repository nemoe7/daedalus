# nonemo: NMO100
# nonemo: NMO901
"""Collect nonemo exclusion markers and manual-review findings for audits."""

import re
import sys
import tokenize
from pathlib import Path

MARKER = re.compile(r"\bnonemo:[ \t]*(.*?)(?=-->|\*/|;|\s|$|\bnonemo:)")
MARKER_SPAN = re.compile(r"\bnonemo:[ \t]*.*?(?=-->|\*/|;|\s|$|\bnonemo:)[ \t]*;?")
INLINE_CODE = re.compile(r"(?P<fence>`+)(?!`).*?(?P=fence)(?!`)")


def head_marker(line, position=None, rotations=()):
  """Identify a header marker with no avoided word before it on the line."""
  marker = MARKER.search(line)
  if marker is None:
    return False
  before = line[: marker.start() if position is None else position]
  return not any(
    re.search(pattern, before, flags=re.IGNORECASE) for _, pattern, _ in rotations
  )


def test_comment_spans(path, lines):
  """Return real comment spans for test sources, or None outside supported tests."""
  path = Path(path)
  name = path.name
  is_test = (
    "tests" in path.parts
    or name.startswith("test_")
    or ".test." in name
    or ".checks." in name
  )
  if not is_test:
    return None
  source = "\n".join(lines) + "\n"
  if path.suffix.lower() == ".py":
    spans = []
    try:
      tokens = tokenize.generate_tokens(iter(source.splitlines(True)).__next__)
      for token in tokens:
        if token.type == tokenize.COMMENT:
          spans.append((token.start, token.end))
    except (tokenize.TokenError, SyntaxError):
      return None
    return spans
  if path.suffix.lower() in {".js", ".cjs", ".mjs"}:
    from prose import javascript_comment_spans

    return list(javascript_comment_spans(source))
  return None


def in_spans(number, column, spans):
  """Return whether a line and column fall inside one source span."""
  point = (number, column)
  return any(start <= point <= end for start, end in spans)


def collect_markers(path, lines, known_rule_ids, rotations=(), audit=False):
  """Collect every marker and the known rule IDs it suppresses."""
  file_ids, line_ids, marks = set(), [], []
  comment_spans = test_comment_spans(path, lines) if audit else None
  for number, line in enumerate(lines, 1):
    suppressed = set()
    visible = (
      INLINE_CODE.sub(lambda match: " " * len(match[0]), line) if audit else line
    )
    audit_positions = {marker.start() for marker in MARKER.finditer(visible)}
    for marker in MARKER.finditer(line):
      code = marker[1]
      scope = "unknown"
      if code in known_rule_ids:
        suppressed.add(code)
        scope = (
          "file"
          if number <= 3 and head_marker(line, marker.start(), rotations)
          else "line"
        )
        if scope == "file":
          file_ids.add(code)
      if audit and (
        marker.start() not in audit_positions
        or (
          comment_spans is not None
          and not in_spans(number, marker.start(), comment_spans)
        )
      ):
        continue
      marks.append(f"{path}:{number}: {code or '<missing>'} {scope}: {line.strip()}")
    line_ids.append(suppressed)
  return file_ids, line_ids, marks


def is_advisory_finding(finding, advisory_rule_ids):
  """Return whether a formatted finding line belongs to a manual-review rule."""
  first = finding.splitlines()[0]
  parts = first.rsplit(": ", 1)[-1].split()
  token = parts[0].strip("[]") if parts else ""
  return (
    token in advisory_rule_ids or "(advisory)" in first or "(manual review)" in first
  )


def audit_lines(marks, findings, advisory_rule_ids):
  """Return all nonemo markers followed by all manual-review findings."""
  advisory = [
    finding for finding in findings if is_advisory_finding(finding, advisory_rule_ids)
  ]
  return [*marks, *advisory]


def main(argv=None):
  from nemoize import main as nemoize_main

  arguments = list(sys.argv[1:] if argv is None else argv)
  return nemoize_main(["--audit", *arguments])


if __name__ == "__main__":
  sys.exit(main())
