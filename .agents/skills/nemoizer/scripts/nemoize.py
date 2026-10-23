# nonemo: NMO100
# nonemo: NMO901
"""Check prose and test candidates, rotate words, and collect exclusions."""

import argparse
import ast
import configparser
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

from prose import COMMENT_SUFFIXES, SPECIAL_CODE_NAMES, prose_spans

# (name, avoided pattern, plain word).
# A stem word that takes its own plain word.
EXCEPTIONS = {
  "picker": "selector",
  "pickers": "selectors",
  "repositories": "repos",
}
ROTATIONS = (
  ("pick", r"(?<![A-Za-z0-9])pick\w*(?![A-Za-z0-9])", "select"),
  ("transmit", r"(?<![A-Za-z0-9])transmit\w*(?![A-Za-z0-9])", "send"),
  ("kind", r"(?<![A-Za-z0-9])kinds?(?![A-Za-z0-9])", "type"),
  ("repository", r"(?<![A-Za-z0-9])repositor(?:y|ies)(?![A-Za-z0-9])", "repo"),
)
RULE_IDS = {
  "transmit": "NMO100",
  "pick": "NMO901",
  "kind": "NMO100",
  "repository": "NMO901",
}
ADVISORY_RULE_IDS = {"NMO901", "STE901", "STE902", "STE903"}
SENTENCE_CAPS = {"instruction": 20, "description": 25}
PARAGRAPH_CAP = 4
PROSE_RULE_IDS = {
  "semicolon": "STE100",
  "sentence": "STE200",
  "passive": "STE901",
  "phrasal": "STE902",
  "ellipsis": "STE903",
  "paragraph": "NMP100",
  "serial_comma": "NMO003",
}
TEST_RULE_IDS = {"output_text": "NMT100", "measurement": "NMT200"}
RESERVED_RULE_IDS = {"HMN000"}
KNOWN_RULE_IDS = {
  *RULE_IDS.values(),
  *PROSE_RULE_IDS.values(),
  *TEST_RULE_IDS.values(),
  *RESERVED_RULE_IDS,
}
MARKER = re.compile(r"\bnonemo:[ \t]*(.*?)(?=-->|\*/|;|\s|$|\bnonemo:)")
MARKER_SPAN = re.compile(r"\bnonemo:[ \t]*.*?(?=-->|\*/|;|\s|$|\bnonemo:)[ \t]*;?")
# The rotation table names the avoided words, so its own rows never flag.
TABLE_ROW = re.compile(r"^\|\s*`[^`]+`\s*\|")
SKIP_DIRS = {
  ".pytest_cache",
  ".git",
  ".venv",
  "__pycache__",
  "build",
  "dist",
  "node_modules",
  "out",
}
SKIP_PATHS = (
  ".agents/skills",
  "arena-state",
)


def text_file(path):
  """Return the decoded text, or None when the file is not UTF-8 text."""
  try:
    raw = path.read_bytes()
  except OSError:
    return None
  if b"\0" in raw[:1024]:
    return None
  try:
    return raw.decode("utf-8")
  except UnicodeDecodeError:
    return None


def skipped(path, root, ignores):
  """True when a path sits under a skipped directory or an ignored prefix."""
  try:
    relative = path.resolve().relative_to(root.resolve())
  except ValueError:
    relative = path
  text = relative.as_posix()
  if any(part in SKIP_DIRS for part in Path(text).parts):
    return True
  if any(text == item or text.startswith(item + "/") for item in SKIP_PATHS):
    return True
  return any(text == item or text.startswith(item + "/") for item in ignores)


def walk(targets, ignores):
  """Yield every text file under the targets that survives the skip rules."""
  for target in targets:
    path = Path(target)
    if path.is_file():
      yield path
      continue
    root = path if path.is_dir() else Path(".")
    for candidate in sorted(root.rglob("*")):
      if candidate.is_file() and not skipped(candidate, root, ignores):
        yield candidate


def head_marker(line, position=None):
  """Identify a header marker with no avoided word before it on the line."""
  marker = MARKER.search(line)
  if marker is None:
    return False
  before = line[: marker.start() if position is None else position]
  return not any(
    re.search(pattern, before, flags=re.IGNORECASE) for _, pattern, _ in ROTATIONS
  )


def exclusions(path, lines):
  """Collect every marker and the known rule IDs it suppresses."""
  file_ids, line_ids, marks = set(), [], []
  for number, line in enumerate(lines, 1):
    suppressed = set()
    for marker in MARKER.finditer(line):
      code = marker[1]
      scope = "unknown"
      if code in KNOWN_RULE_IDS:
        suppressed.add(code)
        scope = "file" if number <= 3 and head_marker(line, marker.start()) else "line"
        if scope == "file":
          file_ids.add(code)
      marks.append(f"{path}:{number}: {code or '<missing>'} {scope}: {line.strip()}")
    line_ids.append(suppressed)
  return file_ids, line_ids, marks


def suppressed(code, ids, name=None):
  """Return whether the active marker set suppresses a rule ID."""
  return code in ids


def plain(word, name, use):
  """Give the matched word its plain word, and keep the old word's case."""
  form = EXCEPTIONS.get(word.lower()) or re.sub(
    re.escape(name), use, word, flags=re.IGNORECASE
  )
  if word.isupper():
    return form.upper()
  if word[:1].isupper():
    return form[:1].upper() + form[1:]
  return form


def rotate(line, suppressed=frozenset()):
  """Swap each matched word for its plain word, and take the exception words first."""
  for name, pattern, use in ROTATIONS:
    if RULE_IDS[name] in suppressed:
      continue
    line = re.sub(
      pattern,
      lambda match, name=name, use=use: plain(match.group(0), name, use),
      line,
      flags=re.IGNORECASE,
    )
  return line


def scan(targets, ignores):
  """Return findings and all exclusion markers with locations and rule IDs."""
  findings, marks = [], []
  for path in walk(targets, ignores):
    text = text_file(path)
    if text is None:
      continue
    lines = text.splitlines()
    file_ids, line_ids, found = exclusions(path, lines)
    marks.extend(found)
    for number, line in enumerate(lines, 1):
      if TABLE_ROW.match(line.strip()):
        continue
      excluded = file_ids | line_ids[number - 1]
      visible = MARKER.sub(lambda match: " " * len(match[0]), line)
      for name, pattern, use in ROTATIONS:
        code = RULE_IDS[name]
        if suppressed(code, excluded, name):
          continue
        for word in sorted(set(re.findall(pattern, visible, flags=re.IGNORECASE))):
          suffix = " (manual review)" if code in ADVISORY_RULE_IDS else ""
          findings.append(f"{path}:{number}: {code} {word} -> {use}{suffix}")
  return findings, marks


def is_advisory(finding):
  return finding.rsplit(": ", 1)[-1].split()[0] in ADVISORY_RULE_IDS


def format_paths(targets, ignores, out=None):
  """Rewrite every rotation in place and print each changed line before and after."""
  out = out if out is not None else sys.stdout
  changed = 0
  for path in walk(targets, ignores):
    text = text_file(path)
    if text is None:
      continue
    lines = text.split("\n")
    file_ids, line_ids, _ = exclusions(path, lines)
    rewritten, touched = [], False
    for number, line in enumerate(lines, 1):
      if TABLE_ROW.match(line.strip()):
        rewritten.append(line)
        continue
      suppressed = file_ids | line_ids[number - 1]
      parts, start = [], 0
      for marker in MARKER.finditer(line):
        parts.extend((rotate(line[start : marker.start()], suppressed), marker[0]))
        start = marker.end()
      parts.append(rotate(line[start:], suppressed))
      rotated = "".join(parts)
      if rotated != line:
        print(f"{path}:{number}: -{line}", file=out)
        print(f"{path}:{number}: +{rotated}", file=out)
        changed += 1
        touched = True
      rewritten.append(rotated)
    if touched:
      path.write_text("\n".join(rewritten), encoding="utf-8")
  return changed


def prose_counts(text):
  """Measure prose outside code fences and within table cells."""
  lines = text.splitlines()
  sentences, paragraphs, block = [], [], []
  fence, width, divider_pending = None, 0, False

  def parts(value):
    value = re.sub(r"(`+)(.*?)\1", lambda match: " " * len(match[0]), value)
    return re.finditer(r"(.*?)(?:[.!?](?:\s+|$)|$)", value, re.DOTALL)

  def lengths(value):
    return [len(match[1].split()) for match in parts(value) if match[1].strip()]

  def cells(value):
    if "|" not in value:
      return []
    row, part, escaped = [], [], False
    for character in value:
      if escaped:
        part.append(character)
        escaped = False
      elif character == "\\":
        escaped = True
      elif character == "|":
        row.append("".join(part))
        part.clear()
      else:
        part.append(character)
    if escaped:
      part.append("\\")
    row.append("".join(part))
    if row and not row[0].strip():
      row.pop(0)
    if row and not row[-1].strip():
      row.pop()
    return [cell.strip() for cell in row] if len(row) > 1 else []

  def flush():
    if not block:
      return
    if re.match(r"(?:[#>*+|-]|\d+[.)]\s)", block[0][1].lstrip()):
      for number, value in block:
        sentences.extend((number, 1, count) for count in lengths(value))
    else:
      offsets, offset, count = [], 0, 0
      for number, value in block:
        offsets.append((offset, number))
        offset += len(value) + 1
      for match in parts(" ".join(value for _, value in block)):
        value = match[1]
        if not value.strip():
          continue
        start = match.start(1) + len(value) - len(value.lstrip())
        number = next(number for offset, number in reversed(offsets) if offset <= start)
        sentences.append((number, 1, len(value.split())))
        count += 1
      paragraphs.append((block[0][0], count))
    block.clear()

  for index, line in enumerate(lines):
    number = index + 1
    marker = re.match(r"^\s*(`{3,}|~{3,})", line)
    if marker:
      flush()
      if fence is None:
        fence = marker[1]
      elif (
        marker[1][0] == fence[0]
        and len(marker[1]) >= len(fence)
        and not line[marker.end() :].strip()
      ):
        fence = None
      width, divider_pending = 0, False
      continue
    if fence is not None:
      continue
    if not line.strip():
      flush()
      width, divider_pending = 0, False
      continue
    if divider_pending:
      divider_pending = False
      continue
    row = cells(line)
    following = cells(lines[index + 1]) if index + 1 < len(lines) else []
    header = (
      row
      and len(row) == len(following)
      and all(re.fullmatch(r":?-{3,}:?", cell) for cell in following)
    )
    if header:
      flush()
      width, divider_pending = len(row), True
    elif not width or len(row) != width:
      width = 0
    if width:
      segments = row
    else:
      block.append((number, line))
      segments = [line]
    if width:
      for segment in segments:
        sentences.extend((number, 1, count) for count in lengths(segment))
  flush()
  return sentences, paragraphs


PASSIVE_VOICE_RE = re.compile(
  r"\b(?:am|is|are|was|were|be|been|being)\s+"
  r"(?:(?:not|never|always|only|also|still|often|already)\s+)?"
  r"([a-z]{3,}ed|written|taken|given|made|done|kept|held|found|chosen|"
  r"known|shown|seen|built|sent|run|broken|spoken|stolen|hidden|driven|drawn|"
  r"forgiven|forgotten|frozen|grown|thrown|worn|torn)\b",
  re.IGNORECASE,
)
PASSIVE_STATE_ADJECTIVES = {
  "able",
  "alleged",
  "armed",
  "ashamed",
  "complicated",
  "concerned",
  "crowded",
  "disabled",
  "enabled",
  "expected",
  "experienced",
  "fixed",
  "interested",
  "isolated",
  "limited",
  "locked",
  "mixed",
  "needed",
  "pleased",
  "prepared",
  "related",
  "required",
  "reserved",
  "restricted",
  "satisfied",
  "scoped",
  "shared",
  "signed",
  "sorted",
  "staged",
  "structured",
  "supported",
  "supposed",
  "tired",
  "tracked",
  "trusted",
  "typed",
  "unchanged",
  "unexpected",
  "unmodified",
  "unsupported",
  "unused",
}


def blank_inline_comments(line):
  """Replace inline HTML comments on one line with spaces to preserve columns."""
  out = []
  cursor = 0
  while True:
    start = line.find("<!--", cursor)
    if start < 0:
      out.append(line[cursor:])
      return "".join(out)
    out.append(line[cursor:start])
    end = line.find("-->", start + 4)
    if end < 0:
      out.append(" " * (len(line) - start))
      return "".join(out)
    out.append(" " * (end + 3 - start))
    cursor = end + 3


def prose_passives(text):
  """Find passive-voice constructions on prose lines outside code fences and code spans."""
  passives = []
  fence = None
  for index, line in enumerate(text.splitlines()):
    number = index + 1
    marker = re.match(r"^\s*(`{3,}|~{3,})", line)
    if marker:
      if fence is None:
        fence = marker[1]
      elif (
        marker[1][0] == fence[0]
        and len(marker[1]) >= len(fence)
        and not line[marker.end() :].strip()
      ):
        fence = None
      continue
    if fence is not None or TABLE_ROW.match(line.strip()):
      continue
    clean = re.sub(r"(`+)(.*?)\1", lambda match: " " * len(match[0]), line)
    clean = blank_inline_comments(clean)
    matches = [
      match
      for match in PASSIVE_VOICE_RE.finditer(clean)
      if match.group(1).lower() not in PASSIVE_STATE_ADJECTIVES
      or re.match(r"\s+by\b", clean[match.end() :], re.IGNORECASE)
    ]
    if matches:
      passives.append((number, matches[0].start() + 1, len(matches)))
  return passives


PHRASAL_VERB_RE = re.compile(
  r"\b(?:carry\s+out|set\s+up|tear\s+down|spin\s+up|hand\s+over|fill\s+out|"
  r"find\s+out|figure\s+out|point\s+out|turn\s+on|turn\s+off|look\s+up|"
  r"back\s+up|clean\s+up|plug\s+in|bail\s+out|opt\s+in|opt\s+out|roll\s+back|"
  r"wire\s+up|hook\s+up|boot\s+up|shut\s+down)\b",
  re.IGNORECASE,
)
ELLIPSIS_RE = re.compile(
  r",\s+(?:and|or)\s+(?:is|are|was|were|has|have|had)\b|"
  r"\b(?:the|a|an)\s+[a-z]{3,}\s+is\s+[a-z]{3,}ed\s+and\s+[a-z]{3,}ed\b",
  re.IGNORECASE,
)
SERIAL_COMMA_RE = re.compile(
  r"\b([A-Za-z0-9_'-]+), ([A-Za-z0-9_'-]+) (?:and|or) "
  r"([A-Za-z0-9_'-]+)(?=[.!?:;)]|\s|$)"
)
CLAUSE_OPENERS = {
  "when",
  "if",
  "after",
  "before",
  "while",
  "because",
  "since",
  "unless",
  "where",
  "once",
  "in",
  "on",
  "for",
  "from",
  "with",
  "without",
  "by",
  "at",
  "to",
  "under",
  "during",
  "and",
  "or",
  "not",
  "the",
  "a",
  "an",
  "that",
  "which",
  "who",
  "whose",
  "whether",
  "never",
  "always",
  "only",
  "also",
  "either",
  "neither",
  "both",
  "such",
  "as",
  "so",
  "yet",
  "but",
  "nor",
  "then",
  "thus",
  "instead",
  "rather",
  "directly",
  "indented",
  "all",
  "them",
}


def prose_line_matches(text, matcher):
  """Collect `(line, column, count)` on prose lines outside fences, code spans, and comments."""
  rows = []
  fence = None
  for index, line in enumerate(text.splitlines()):
    number = index + 1
    marker = re.match(r"^\s*(`{3,}|~{3,})", line)
    if marker:
      if fence is None:
        fence = marker[1]
      elif (
        marker[1][0] == fence[0]
        and len(marker[1]) >= len(fence)
        and not line[marker.end() :].strip()
      ):
        fence = None
      continue
    if fence is not None or TABLE_ROW.match(line.strip()):
      continue
    clean = re.sub(r"(`+)(.*?)\1", lambda match: " " * len(match[0]), line)
    clean = blank_inline_comments(clean)
    hits = matcher(clean)
    if hits:
      rows.append((number, hits[0].start() + 1, len(hits)))
  return rows


def prose_phrasal_verbs(text):
  return prose_line_matches(text, lambda clean: list(PHRASAL_VERB_RE.finditer(clean)))


def prose_ellipsis(text):
  return prose_line_matches(text, lambda clean: list(ELLIPSIS_RE.finditer(clean)))


def prose_serial_commas(text):
  return prose_line_matches(
    text,
    lambda clean: [
      match
      for match in SERIAL_COMMA_RE.finditer(
        re.sub(r"\[[^\]]*\]\([^)]*\)", lambda item: " " * len(item[0]), clean)
      )
      if not (
        {match.group(1).lower(), match.group(2).lower(), match.group(3).lower()}
        & CLAUSE_OPENERS
      )
      and not clean[: match.start()].rstrip().lower().endswith("to")
    ],
  )


def prose_semicolons(text):
  """Find semicolons on prose lines outside code fences, code spans, and comments."""
  semicolons = []
  fence = None
  for index, line in enumerate(text.splitlines()):
    number = index + 1
    marker = re.match(r"^\s*(`{3,}|~{3,})", line)
    if marker:
      if fence is None:
        fence = marker[1]
      elif (
        marker[1][0] == fence[0]
        and len(marker[1]) >= len(fence)
        and not line[marker.end() :].strip()
      ):
        fence = None
      continue
    if fence is not None or TABLE_ROW.match(line.strip()):
      continue
    clean = re.sub(r"(`+)(.*?)\1", lambda match: " " * len(match[0]), line)
    clean = blank_inline_comments(clean)
    clean = re.sub(
      r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);",
      lambda match: " " * len(match[0]),
      clean,
    )
    if ";" in clean:
      semicolons.append((number, clean.index(";") + 1, clean.count(";")))
  return semicolons


def prose_review(path, text, context, paragraph_cap):
  """Return unsuppressed measurable candidates for one explicit text scope."""
  lines = text.splitlines()
  file_ids, line_ids, _ = exclusions(path, lines)
  findings = []
  sentences, paragraphs = prose_counts(text)
  code = PROSE_RULE_IDS["semicolon"]
  if not suppressed(code, file_ids):
    for number, column, count in prose_semicolons(text):
      if not suppressed(code, line_ids[number - 1]):
        findings.append(
          {
            "rule": code,
            "path": str(path),
            "line": number,
            "column": column,
            "count": count,
            "cap": 0,
            "status": "review",
          }
        )
  cap = SENTENCE_CAPS.get(context, SENTENCE_CAPS["instruction"])
  code = PROSE_RULE_IDS["sentence"]
  if not suppressed(code, file_ids):
    for number, column, count in sentences:
      if count > cap and not suppressed(code, line_ids[number - 1]):
        findings.append(
          {
            "rule": code,
            "path": str(path),
            "line": number,
            "column": column,
            "count": count,
            "cap": cap,
            "status": "review",
          }
        )
  for key, detector in (
    ("passive", prose_passives),
    ("phrasal", prose_phrasal_verbs),
    ("ellipsis", prose_ellipsis),
    ("serial_comma", prose_serial_commas),
  ):
    code = PROSE_RULE_IDS[key]
    status = "manual_review" if code in ADVISORY_RULE_IDS else "review"
    if not suppressed(code, file_ids):
      for number, column, count in detector(text):
        if not suppressed(code, line_ids[number - 1]):
          findings.append(
            {
              "rule": code,
              "path": str(path),
              "line": number,
              "column": column,
              "count": count,
              "cap": 0,
              "status": status,
            }
          )
  code = PROSE_RULE_IDS["paragraph"]
  if not suppressed(code, file_ids):
    for number, count in paragraphs:
      if count > paragraph_cap and not suppressed(code, line_ids[number - 1]):
        findings.append(
          {
            "rule": code,
            "path": str(path),
            "line": number,
            "column": 1,
            "count": count,
            "cap": paragraph_cap,
            "status": "review",
          }
        )
  return findings


def source_prose(path, text, context, paragraph_cap):
  """Check extracted code prose while retaining physical source locations."""
  if path.suffix in COMMENT_SUFFIXES or path.name in SPECIAL_CODE_NAMES:
    spans = prose_spans(path, text)
  elif path.suffix in {".json", ".csv", ".lock"}:
    spans = []
  else:
    spans = [(text, 1, len(text.splitlines()))]
  lines = text.splitlines()
  file_ids, line_ids, _ = exclusions(path, lines)
  findings = []
  for value, first, last in spans:
    for row in prose_review(
      path,
      MARKER_SPAN.sub(lambda match: " " * len(match[0]), value),
      context,
      paragraph_cap,
    ):
      if len(value.splitlines()) > last - first + 1:
        row["line"] = first
      else:
        row["line"] += first - 1
      ids = file_ids | line_ids[row["line"] - 1]
      if not suppressed(row["rule"], ids):
        findings.append(row)
  return findings


def prose_main(argv=None):
  parser = argparse.ArgumentParser(description="Review explicitly scoped prose counts.")
  parser.add_argument("targets", nargs="+")
  parser.add_argument(
    "--context", choices=[*SENTENCE_CAPS, "unknown"], default="unknown"
  )
  parser.add_argument("--paragraph-cap", type=int, default=PARAGRAPH_CAP)
  arguments = parser.parse_args(argv)
  if not 0 < arguments.paragraph_cap <= PARAGRAPH_CAP:
    parser.error(f"--paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
  for target in arguments.targets:
    if not Path(target).exists():
      parser.error(f"target does not exist: {target}")
  findings, coverage, errors = [], [], []
  for path in walk(arguments.targets, []):
    text = text_file(path)
    coverage.append(
      {
        "path": str(path),
        "context": arguments.context,
        "sentences": "checked"
        if text is not None and arguments.context in {*SENTENCE_CAPS, "unknown"}
        else "not_checked",
        "paragraphs": "checked" if text is not None else "not_checked",
      }
    )
    if text is not None:
      try:
        findings.extend(
          source_prose(path, text, arguments.context, arguments.paragraph_cap)
        )
      except (
        SyntaxError,
        tokenize.TokenError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        RuntimeError,
      ) as error:
        coverage[-1].update(
          sentences="not_checked", paragraphs="not_checked", reason="parse_error"
        )
        errors.append(f"{path}: {type(error).__name__}: {error}")
  print(json.dumps({"findings": findings, "coverage": coverage}))
  if errors:
    print("\n".join(errors), file=sys.stderr)
    return 2
  return int(bool(findings))


MEASUREMENTS = {
  "width",
  "clientwidth",
  "clientheight",
  "offsetwidth",
  "offsetheight",
  "scrollwidth",
  "scrollheight",
  "bytelength",
  "byte_length",
  "height",
  "timeout",
  "duration",
  "elapsed",
  "measure",
  "token_count",
  "byte_count",
  "size_bytes",
  "poll_wait_seconds",
  "timeout_seconds",
}
CHANNELS = {"stdout", "stderr", "output", "out", "err"}
ASSERT_METHODS = {"assertEqual", "assertNotEqual", "assertIn", "assertNotIn"}
NODE_TIMEOUT = 10
JS_REVIEW = r"""
let acorn;
try { acorn = require(require.resolve('acorn', {paths:[process.argv[1]]})); }
catch { console.log(JSON.stringify({reason:'parser_unavailable'})); process.exit(0); }
const text = require('node:fs').readFileSync(0, 'utf8');
let tree;
try {
  try { tree = acorn.parse(text, {ecmaVersion:'latest', sourceType:'module', locations:true}); }
  catch { tree = acorn.parse(text, {ecmaVersion:'latest', sourceType:'script', locations:true}); }
} catch (error) {
  console.log(JSON.stringify({error:'SyntaxError', line:error.loc.line, column:error.loc.column + 1}));
  process.exit(0);
}
function shape(node) {
  if (node?.type === 'Identifier') return node.name;
  if (node?.type === 'MemberExpression' && !node.computed) return `${shape(node.object)}.${node.property.name}`;
  if (node?.type === 'CallExpression') return `${shape(node.callee)}(${node.arguments.map(shape).join(',')})`;
  return '<expression>';
}
const pairs = [];
let testScope = false;
const lines = text.split("\n");
function pair(left, right, node, operator) {
  for (const [actual, expected] of [[left,right],[right,left]]) {
    if (expected?.type === 'Literal' && ['string','number'].includes(typeof expected.value)) {
      const column = Array.from(lines[node.loc.start.line - 1].slice(0, node.loc.start.column)).length + 1;
      pairs.push({actual:shape(actual), value:expected.value, operator,
        line:node.loc.start.line, column});
    }
  }
}
function checkIncludes(expr, node) {
  if (expr?.type === 'CallExpression' && expr.callee?.type === 'MemberExpression' &&
    expr.callee.property?.name === 'includes') {
    pair(expr.callee.object, expr.arguments[0], node, 'membership');
  }
}
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'ImportDeclaration' && node.source.value === 'node:test') testScope = true;
  if (node.type === 'CallExpression') {
    const name = shape(node.callee);
    if (name === 'require' && node.arguments[0]?.value === 'node:test') testScope = true;
    if (['test','it','describe','suite'].includes(name) && node.arguments.some(arg =>
      ['FunctionExpression','ArrowFunctionExpression'].includes(arg.type))) testScope = true;
    if (['assert.equal','assert.strictEqual','assert.deepEqual',
      'assert.deepStrictEqual','assert.notEqual','assert.notStrictEqual'].includes(name)) {
      pair(node.arguments[0], node.arguments[1], node, 'equality');
      checkIncludes(node.arguments[0], node);
      checkIncludes(node.arguments[1], node);
    } else if (name === 'assert' || name === 'assert.ok') {
      const expression = node.arguments[0];
      if (expression?.type === 'BinaryExpression' && ['==','===','!=','!=='].includes(expression.operator)) {
        pair(expression.left, expression.right, node, 'equality');
      } else {
        checkIncludes(expression, node);
      }
    }
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') visit(value);
  }
}
visit(tree);
console.log(JSON.stringify({pairs, test_scope:testScope}));
"""


def shape(node):
  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    return f"{shape(node.value)}.{node.attr}"
  if isinstance(node, ast.Call):
    return f"{shape(node.func)}({','.join(map(shape, node.args))})"
  return "<expression>"


def python_pairs(text):
  pairs = []
  lines = text.splitlines()

  def pair(left, right, node, operator):
    for actual, expected in ((left, right), (right, left)):
      if isinstance(expected, ast.Constant) and type(expected.value) in (
        str,
        int,
        float,
      ):
        pairs.append(
          {
            "actual": shape(actual),
            "value": expected.value,
            "operator": operator,
            "line": node.lineno,
            "column": len(
              lines[node.lineno - 1].encode("utf-8")[: node.col_offset].decode("utf-8")
            )
            + 1,
          }
        )

  for node in ast.walk(ast.parse(text)):
    if isinstance(node, ast.Assert):
      for expression in ast.walk(node.test):
        if isinstance(expression, ast.Compare):
          left = expression.left
          for operator, right in zip(expression.ops, expression.comparators):
            if isinstance(operator, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
              pair(
                left,
                right,
                expression,
                "equality"
                if isinstance(operator, (ast.Eq, ast.NotEq))
                else "membership",
              )
            left = right
    if (
      isinstance(node, ast.Call)
      and isinstance(node.func, ast.Attribute)
      and shape(node.func.value) in ("self", "cls")
      and node.func.attr in ASSERT_METHODS
      and len(node.args) >= 2
    ):
      pair(
        node.args[0],
        node.args[1],
        node,
        "membership" if node.func.attr in {"assertIn", "assertNotIn"} else "equality",
      )
  return {"pairs": pairs}


def output_channel(name):
  return not name.startswith("len(") and any(
    part.split("(")[0] in CHANNELS for part in name.split(".")
  )


def category(pair):
  value, actual = pair["value"], pair["actual"]
  if (
    isinstance(value, str)
    and output_channel(actual)
    and len(re.findall(r"[A-Za-z]+", value)) >= 3
    and not value.isupper()
    and re.search(r"\s", value)
    and not re.search(r"[<>{}=\\/\n]", value)
  ):
    return "output_text"
  if (
    type(value) in (int, float)
    and value > 0
    and pair["operator"] == "equality"
    and (
      actual.split("(")[0].split(".")[-1].lower() in MEASUREMENTS
      or actual.startswith("px(")
      or actual.startswith("len(")
      and any(word in CHANNELS for word in re.findall(r"\w+", actual))
    )
  ):
    return "measurement"
  return None


def javascript_pairs(text):
  """Parse JavaScript assertions and test declarations through the bundled resolver."""
  if not shutil.which("node"):
    return {"reason": "parser_unavailable"}
  result = subprocess.run(
    ["node", "-e", JS_REVIEW, str(Path(__file__).resolve().parent)],
    input=text,
    capture_output=True,
    text=True,
    timeout=NODE_TIMEOUT,
    check=False,
  )
  if result.returncode:
    raise RuntimeError("parser_process_failed")
  return json.loads(result.stdout)


def review_tests(targets):
  findings, coverage, errors, visited = [], [], [], set()
  for path in walk(targets, []):
    if path.resolve() in visited:
      continue
    visited.add(path.resolve())
    row = {"path": str(path), "status": "not_checked"}
    coverage.append(row)
    text = text_file(path)
    if text is None:
      row["reason"] = "unreadable_or_non_text"
      continue
    try:
      if path.suffix.lower() == ".py":
        parsed = python_pairs(text)
        row["parser"] = "python_ast"
      elif path.suffix.lower() in {".js", ".cjs", ".mjs"}:
        parsed = javascript_pairs(text)
        row["parser"] = "acorn"
      else:
        row["reason"] = "unsupported_language"
        continue
    except SyntaxError as error:
      parsed = {"error": "SyntaxError", "line": error.lineno, "column": error.offset}
    except (
      OSError,
      subprocess.TimeoutExpired,
      ValueError,
      RuntimeError,
      RecursionError,
    ) as error:
      parsed = {"error": type(error).__name__}
    if "reason" in parsed:
      row["reason"] = parsed["reason"]
      continue
    if "error" in parsed:
      row["reason"] = "parse_error"
      errors.append(
        {
          "path": str(path),
          "type": parsed["error"],
          "line": parsed.get("line"),
          "column": parsed.get("column"),
        }
      )
      continue
    row["status"] = "checked"
    file_ids, line_ids, _ = exclusions(path, text.splitlines())
    for pair in parsed["pairs"]:
      role = category(pair)
      if role is None:
        continue
      code = TEST_RULE_IDS[role]
      if suppressed(code, file_ids | line_ids[pair["line"] - 1]):
        continue
      findings.append(
        {
          "rule": code,
          "category": role,
          "path": str(path),
          "line": pair["line"],
          "column": pair["column"],
          "evidence": f"{pair['actual']} {pair['operator']} <literal>",
          "status": "review",
        }
      )
  return {"findings": findings, "coverage": coverage, "errors": errors}


def review_tests_main(argv=None):
  parser = argparse.ArgumentParser(
    description="Review explicitly scoped test assertions."
  )
  parser.add_argument("targets", nargs="+")
  args = parser.parse_args(argv)
  for target in args.targets:
    if not Path(target).exists():
      parser.error(f"target does not exist: {target}")
  result = review_tests(args.targets)
  print(json.dumps(result))
  return 2 if result["errors"] else int(bool(result["findings"]))


def is_test_file(path, text, helpers):
  """Recognize test paths and declarations without running supplied code."""
  if path.resolve() in helpers:
    return True
  suffix = path.suffix.lower()
  if suffix not in {".py", ".js", ".cjs", ".mjs"}:
    return False
  if any(
    fnmatch.fnmatchcase(path.name, pattern)
    for pattern in (
      "test_*.py",
      "*_test.py",
      "test_*.js",
      "test_*.cjs",
      "test_*.mjs",
      "*.test.*",
      "*.spec.*",
      "*.checks.cjs",
      "conftest.py",
    )
  ) or any(part in {"test", "tests"} for part in path.parts[:-1]):
    return True
  if suffix == ".py":
    for parent in path.resolve().parents:
      config = parent / "pytest.ini"
      if not config.is_file():
        continue
      settings = configparser.ConfigParser(interpolation=None)
      settings.read(config, encoding="utf-8")
      patterns = settings.get("pytest", "python_files", fallback="test_*.py").split()
      roots = settings.get("pytest", "testpaths", fallback=".").split()
      if any(fnmatch.fnmatchcase(path.name, pattern) for pattern in patterns) and any(
        path.resolve().is_relative_to((parent / root).resolve()) for root in roots
      ):
        return True
      break
    return any(
      isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
      and node.name.startswith("test_")
      or isinstance(node, ast.ClassDef)
      and any(ast.unparse(base).endswith("TestCase") for base in node.bases)
      for node in ast.walk(ast.parse(text))
    )
  parsed = javascript_pairs(text)
  if "reason" in parsed or "error" in parsed:
    raise ValueError(parsed.get("reason") or parsed["error"])
  return parsed["test_scope"]


def main(argv=None):
  argv = list(sys.argv[1:] if argv is None else argv)
  if argv[:1] == ["prose"]:
    return prose_main(argv[1:])
  if argv[:1] == ["review-tests"]:
    return review_tests_main(argv[1:])
  parser = argparse.ArgumentParser(description="Check Nemoizer prose and test domains.")
  parser.add_argument("targets", nargs="*", default=["."])
  parser.add_argument(
    "--ignore",
    action="append",
    default=[],
    help="skip a directory or file, relative to the target",
  )
  parser.add_argument(
    "--ignores", action="store_true", help="list every nonemo marker instead"
  )
  parser.add_argument(
    "--format", action="store_true", help="apply the rotations in place"
  )
  parser.add_argument(
    "--context", choices=[*SENTENCE_CAPS, "unknown"], default="unknown"
  )
  parser.add_argument("--paragraph-cap", type=int, default=PARAGRAPH_CAP)
  parser.add_argument("--test-helper", action="append", type=Path, default=[])
  arguments = parser.parse_args(argv)
  if not 0 < arguments.paragraph_cap <= PARAGRAPH_CAP:
    parser.error(f"--paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
  for helper in arguments.test_helper:
    if not helper.is_file():
      parser.error(f"test helper does not exist: {helper}")
  targets = arguments.targets or ["."]
  if arguments.format:
    changed = format_paths(targets, arguments.ignore)
    print(f"{changed} lines rotated", file=sys.stderr)
    return 0
  findings, marks = scan(targets, arguments.ignore)
  if arguments.ignores:
    print("\n".join(marks))
    return 0
  errors, tests = [], []
  helpers = {path.resolve() for path in arguments.test_helper}
  for path in walk([*targets, *arguments.test_helper], arguments.ignore):
    text = text_file(path)
    try:
      if is_test_file(path, text or "", helpers):
        tests.append(str(path))
      if text is None:
        continue
      rows = source_prose(path, text, arguments.context, arguments.paragraph_cap)
    except (
      SyntaxError,
      tokenize.TokenError,
      OSError,
      ValueError,
      configparser.Error,
      subprocess.SubprocessError,
      RuntimeError,
      RecursionError,
    ) as error:
      errors.append(f"{path}: {type(error).__name__}: {error}")
      continue
    findings.extend(
      f"{row['path']}:{row['line']}: {row['rule']} {row['count']} -> {row['cap']}"
      + (" (manual review)" if row["rule"] in ADVISORY_RULE_IDS else "")
      for row in rows
    )
  result = review_tests(tests)
  findings.extend(
    f"{row['path']}:{row['line']}: {row['rule']} {row['evidence']}"
    for row in result["findings"]
  )
  for row in result["coverage"]:
    if row["status"] != "checked":
      errors.append(f"{row['path']}: tests not checked: {row['reason']}")
  errors.extend(f"{row['path']}: {row['type']}" for row in result["errors"])
  if tests:
    print(
      "Test scope: static paths and declarations. Dynamic helpers may be unresolved.",
      file=sys.stderr,
    )
  print("\n".join(findings))
  if errors:
    print("\n".join(errors), file=sys.stderr)
    return 2
  return int(any(not is_advisory(finding) for finding in findings))


if __name__ == "__main__":
  sys.exit(main())
