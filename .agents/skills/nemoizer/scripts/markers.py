# nonemo: NMO100
# nonemo: NMO901
"""Collect nonemo exclusion markers and manual-review findings for audits."""

import re
import sys

MARKER = re.compile(r"\bnonemo:[ \t]*(.*?)(?=-->|\*/|;|\s|$|\bnonemo:)")
MARKER_SPAN = re.compile(r"\bnonemo:[ \t]*.*?(?=-->|\*/|;|\s|$|\bnonemo:)[ \t]*;?")


def head_marker(line, position=None, rotations=()):
  """Identify a header marker with no avoided word before it on the line."""
  marker = MARKER.search(line)
  if marker is None:
    return False
  before = line[: marker.start() if position is None else position]
  return not any(
    re.search(pattern, before, flags=re.IGNORECASE) for _, pattern, _ in rotations
  )


def collect_markers(path, lines, known_rule_ids, rotations=()):
  """Collect every marker and the known rule IDs it suppresses."""
  file_ids, line_ids, marks = set(), [], []
  for number, line in enumerate(lines, 1):
    suppressed = set()
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
