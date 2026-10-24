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

import tomllib

try:
  from markers import (
    MARKER,
    MARKER_SPAN,
    audit_lines,
    collect_markers,
    is_advisory_finding,
  )
  from markers import (
    head_marker as marker_head,
  )
except ImportError:
  MARKER = re.compile(r"\bnonemo:[ \t]*(.*?)(?=-->|\*/|;|\s|$|\bnonemo:)")
  MARKER_SPAN = re.compile(r"\bnonemo:[ \t]*.*?(?=-->|\*/|;|\s|$|\bnonemo:)[ \t]*;?")

  def marker_head(line, position=None, rotations=()):
    marker = MARKER.search(line)
    if marker is None:
      return False
    before = line[: marker.start() if position is None else position]
    return not any(
      re.search(pattern, before, flags=re.IGNORECASE) for _, pattern, _ in rotations
    )

  def collect_markers(path, lines, known_rule_ids, rotations=()):
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
            if number <= 3 and marker_head(line, marker.start(), rotations)
            else "line"
          )
          if scope == "file":
            file_ids.add(code)
        marks.append(f"{path}:{number}: {code or '<missing>'} {scope}: {line.strip()}")
      line_ids.append(suppressed)
    return file_ids, line_ids, marks

  def is_advisory_finding(finding, advisory_rule_ids):
    first = finding.splitlines()[0]
    parts = first.rsplit(": ", 1)[-1].split()
    token = parts[0].strip("[]") if parts else ""
    return (
      token in advisory_rule_ids or "(advisory)" in first or "(manual review)" in first
    )

  def audit_lines(marks, findings, advisory_rule_ids):
    return [
      *marks,
      *(
        finding
        for finding in findings
        if is_advisory_finding(finding, advisory_rule_ids)
      ),
    ]


from prose import (
  COMMENT_BLOCK_CAP,
  COMMENT_SUFFIXES,
  SPECIAL_CODE_NAMES,
  comment_blocks,
  prose_spans,
  slash_comments,
)

# (name, avoided pattern, plain word).
# A stem word that takes its own plain word.
EXCEPTIONS = {
  "picker": "selector",
  "pickers": "selectors",
  "repositories": "repos",
  "pull request": "PR",
  "pull requests": "PRs",
}
NEMOIZER_MODIFIED_NOUNS = (
  r"rules?|lists?|linters?|prose|skills?|configs?|checks?|scripts?|"
  r"parsers?|suites?|tests?|plugins?|domains?|targets?|markers?"
)
KEEP_LOWER = {"nemoizer"}
ROTATIONS = (
  ("pick", r"(?<![A-Za-z0-9])pick\w*(?![A-Za-z0-9])", "select"),
  ("transmit", r"(?<![A-Za-z0-9])transmit\w*(?![A-Za-z0-9])", "send"),
  ("kind", r"(?<![A-Za-z0-9])kinds?(?![A-Za-z0-9])", "type"),
  ("repository", r"(?<![A-Za-z0-9])repositor(?:y|ies)(?![A-Za-z0-9])", "repo"),
  ("pull request", r"(?<![A-Za-z0-9])pull[ \t]+requests?(?![A-Za-z0-9])", "PR"),
  (
    "nemoizer",
    (
      rf"(?<![A-Za-z0-9_./:-])(?:the[ \t]+(?!nemoizer[ \t]+(?:{NEMOIZER_MODIFIED_NOUNS})\b))?"
      r"nemoizer(?![A-Za-z0-9_/-]|\.[A-Za-z0-9_])"
    ),
    "nemoizer",
  ),
)
RULE_IDS = {
  "transmit": "NMO100",
  "pick": "NMO901",
  "kind": "NMO100",
  "repository": "NMO901",
  "pull request": "NMO901",
  "nemoizer": "NMO100",
}
ADVISORY_RULE_IDS = {
  "NMO901",
  "NMO902",
  "STE901",
  "STE902",
  "STE903",
  "STE904",
  "STE905",
  "STE906",
  "STE908",
  "STE909",
  "STE910",
  "HMN901",
  "HMN902",
  "HMN903",
  "HMN904",
  "HMN905",
  "HMN906",
  "HMN907",
  "HMN908",
  "HMN909",
  "HMN910",
}
SENTENCE_CAPS = {"instruction": 20, "description": 25}
PARAGRAPH_CAP = 4
LIST_SENTENCE_CAP = 2
HEADING_SENTENCE_CAP = 1
THE_DENSITY_CAP = 20
MIN_THE_COUNT = 3
PROSE_RULE_IDS = {
  "dangling_conjunction": "STE001",
  "semicolon": "STE100",
  "sentence": "STE200",
  "nominalization": "STE300",
  "wordy_phrase": "STE400",
  "archaic_connector": "STE401",
  "passive": "STE901",
  "phrasal": "STE902",
  "ellipsis": "STE903",
  "compound_instruction": "STE904",
  "noun_cluster": "STE905",
  "synonym_cluster": "STE906",
  "compound_tense": "STE908",
  "inline_list": "STE909",
  "double_negative": "STE910",
  "staged_frame": "HMN100",
  "stacked_qualifier": "HMN200",
  "ai_vocabulary": "HMN300",
  "arrow": "HMN400",
  "chatbot_courtesy": "HMN500",
  "curly_quote": "HMN600",
  "negative_contrast": "HMN901",
  "aphorism": "HMN902",
  "rhetorical_setup": "HMN903",
  "repeated_opener": "HMN904",
  "prose_dash": "HMN905",
  "vague_attribution": "HMN906",
  "copula_avoidance": "HMN907",
  "bold_label_bullet": "HMN908",
  "consecutive_emoji": "HMN909",
  "self_narration": "HMN910",
  "paragraph": "NMP100",
  "comment_block": "NMP101",
  "serial_comma": "NMO003",
  "the_density": "NMO902",
}
RULE_DESCRIPTIONS = {
  "STE001": "dangling conjunction",
  "STE100": "semicolon in prose",
  "STE200": "sentence has {count} words (cap {cap})",
  "STE300": "nominalized verb",
  "STE400": "wordy phrase",
  "STE401": "archaic or legalistic connector",
  "STE901": "passive voice",
  "STE902": "phrasal verb",
  "STE903": "omitted passive auxiliary",
  "STE904": "compound instruction",
  "STE905": "noun cluster",
  "STE906": "synonym cluster",
  "STE908": "compound tense",
  "STE909": "inline list in prose",
  "STE910": "double negative",
  "HMN100": "staged frame",
  "HMN200": "stacked qualifier",
  "HMN300": "AI vocabulary",
  "HMN400": "arrow in prose",
  "HMN500": "chatbot courtesy",
  "HMN600": "curly quotation mark",
  "HMN901": "negative contrast",
  "HMN902": "aphorism or importance claim",
  "HMN903": "rhetorical setup",
  "HMN904": "repeated sentence opener",
  "HMN905": "em or en dash in prose",
  "HMN906": "vague attribution",
  "HMN907": "copula avoidance",
  "HMN908": "bold-label bullet",
  "HMN909": "consecutive emojis",
  "HMN910": "self-narration",
  "NMP100": "paragraph has {count} sentences (cap {cap})",
  "NMP101": "comment block has {count} lines (cap {cap})",
  "NMO003": "missing Oxford comma",
  "NMO902": (
    '"the" density is {count}% (cap {cap}%); '
    'collapse list "the" articles (--format) or drop "the" before proper names'
  ),
}


NMP100_LABELS = {
  "paragraph": "paragraph",
  "table": "table cell",
  "list": "list item",
  "heading": "heading",
  "blockquote": "blockquote",
}


def describe_finding(row):
  """Format a human-readable violation description for one prose or comment finding."""
  if row["rule"] == "NMP100" and row.get("kind") in NMP100_LABELS:
    label = NMP100_LABELS[row["kind"]]
    return f"{label} has {row['count']} sentences (cap {row['cap']})"
  template = RULE_DESCRIPTIONS.get(row["rule"], "{count} (cap {cap})")
  if "{count}" in template:
    return template.format(count=row["count"], cap=row["cap"])
  if row["count"] > 1:
    return f"{template} ({row['count']} matches)"
  return template


TEST_RULE_IDS = {"output_text": "NMT100", "measurement": "NMT200"}
RESERVED_RULE_IDS = {"HMN000"}
KNOWN_RULE_IDS = {
  *RULE_IDS.values(),
  *PROSE_RULE_IDS.values(),
  *TEST_RULE_IDS.values(),
  *RESERVED_RULE_IDS,
}
CURLY_QUOTES = str.maketrans(
  {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"}
)
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
SKIP_SUFFIXES = {
  ".json",
  ".webmanifest",
}
DEFAULT_CONFIG = {
  "root": None,
  "exclude": None,
  "extend_exclude": (),
  "respect_gitignore": True,
  "context": None,
  "instruction_sentence_cap": SENTENCE_CAPS["instruction"],
  "description_sentence_cap": SENTENCE_CAPS["description"],
  "paragraph_cap": PARAGRAPH_CAP,
  "list_sentence_cap": LIST_SENTENCE_CAP,
  "heading_sentence_cap": HEADING_SENTENCE_CAP,
  "comment_block_cap": COMMENT_BLOCK_CAP,
  "the_density_cap": THE_DENSITY_CAP,
  "min_the_count": MIN_THE_COUNT,
  "test_helpers": (),
  "select": ("ALL",),
  "ignore": (),
  "per_file_ignores": {},
}
ACTIVE_CONFIG = dict(DEFAULT_CONFIG)
LICENSE_FILE_RE = re.compile(
  r"^(?:(?:un)?licen[cs]e(?:[-._].*)?|.*\.licen[cs]e|copying(?:[-._].*)?|"
  r"notice(?:[-._].*)?|(?:thirdparty|copyright)notices?(?:\..*)?|"
  r"patents?(?:[-._].*)?)$",
  re.IGNORECASE,
)


def _match_selectors(code, selectors):
  """True when a rule ID matches any configured rule ID, prefix, or ALL."""
  for item in selectors:
    token = str(item).strip().upper()
    if token == "ALL" or code == token or code.startswith(token):
      return True
  return False


def _match_glob(rel_posix, name, pattern):
  """Match a path against a Ruff-style file or directory glob pattern."""
  pat = pattern.strip().replace("\\", "/")
  if pat.endswith("/**"):
    prefix = pat[:-3].rstrip("/")
    return rel_posix == prefix or rel_posix.startswith(prefix + "/")
  if "/" not in pat:
    return fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(rel_posix, pat)
  return fnmatch.fnmatchcase(rel_posix, pat)


def load_config(targets=(), config_path=None, no_config=False):
  """Discover and parse nemoizer.toml or [tool.nemoizer] in pyproject.toml."""
  config = dict(DEFAULT_CONFIG)
  if no_config:
    return config
  root = None
  table = None
  if config_path is not None:
    candidate = Path(config_path)
    if not candidate.is_file():
      raise ValueError(f"config file does not exist: {candidate}")
    raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
    table = (
      raw.get("tool", {}).get("nemoizer", {})
      if candidate.name == "pyproject.toml"
      else raw
    )
    root = candidate.resolve().parent
  else:
    first = Path(targets[0]).resolve() if targets else Path.cwd().resolve()
    start = first if first.is_dir() else first.parent
    for directory in (start, *start.parents):
      for name in ("nemoizer.toml", ".nemoizer.toml"):
        candidate = directory / name
        if candidate.is_file():
          table = tomllib.loads(candidate.read_text(encoding="utf-8"))
          root = directory
          break
      if table is not None:
        break
      pyproject = directory / "pyproject.toml"
      if pyproject.is_file():
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        section = raw.get("tool", {}).get("nemoizer")
        if isinstance(section, dict):
          table = section
          root = directory
          break
      if (directory / ".git").exists():
        break
  if not isinstance(table, dict):
    return config
  config["root"] = root
  if "exclude" in table:
    config["exclude"] = tuple(str(item) for item in table["exclude"])
  if "extend-exclude" in table:
    config["extend_exclude"] = tuple(str(item) for item in table["extend-exclude"])
  if "respect-gitignore" in table:
    if not isinstance(table["respect-gitignore"], bool):
      raise ValueError("respect-gitignore must be a boolean")
    config["respect_gitignore"] = table["respect-gitignore"]
  if "context" in table:
    context = str(table["context"])
    if context not in {*SENTENCE_CAPS, "unknown"}:
      raise ValueError(f"invalid context in config: {context}")
    config["context"] = context
  for toml_key, cfg_key in (
    ("instruction-sentence-cap", "instruction_sentence_cap"),
    ("description-sentence-cap", "description_sentence_cap"),
    ("list-sentence-cap", "list_sentence_cap"),
    ("heading-sentence-cap", "heading_sentence_cap"),
    ("comment-block-cap", "comment_block_cap"),
    ("min-the-count", "min_the_count"),
  ):
    if toml_key in table:
      val = int(table[toml_key])
      if val < 1:
        raise ValueError(f"{toml_key} must be at least 1")
      config[cfg_key] = val
  if "the-density-cap" in table:
    density_cap = int(table["the-density-cap"])
    if not 1 <= density_cap <= 100:
      raise ValueError("the-density-cap must be between 1 and 100")
    config["the_density_cap"] = density_cap
  if "paragraph-cap" in table:
    paragraph_cap = int(table["paragraph-cap"])
    if not 0 < paragraph_cap <= PARAGRAPH_CAP:
      raise ValueError(f"paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
    config["paragraph_cap"] = paragraph_cap
  if "test-helpers" in table:
    config["test_helpers"] = tuple(
      (root / str(item)).resolve() if root else Path(str(item)).resolve()
      for item in table["test-helpers"]
    )
  lint = table.get("lint", {})
  if isinstance(lint, dict):
    if "select" in lint:
      config["select"] = tuple(str(item) for item in lint["select"])
    if "ignore" in lint:
      config["ignore"] = tuple(str(item) for item in lint["ignore"])
    per_file = lint.get("per-file-ignores", {})
    if isinstance(per_file, dict):
      config["per_file_ignores"] = {
        str(pattern): tuple(str(rule) for rule in rules)
        for pattern, rules in per_file.items()
      }
  return config


def config_file_exclusions(path, config=None):
  """Return rule IDs suppressed for path by select, ignore, and per-file-ignores."""
  cfg = ACTIVE_CONFIG if config is None else config
  select = cfg.get("select", ("ALL",))
  ignore = cfg.get("ignore", ())
  per_file = cfg.get("per_file_ignores", {})
  disabled = {
    code
    for code in KNOWN_RULE_IDS
    if not _match_selectors(code, select) or _match_selectors(code, ignore)
  }
  if per_file:
    candidate = Path(path)
    root = cfg.get("root")
    try:
      rel = (
        candidate.resolve().relative_to(root.resolve()).as_posix()
        if root is not None
        else candidate.as_posix()
      )
    except ValueError:
      rel = candidate.as_posix()
    for pattern, rules in per_file.items():
      if _match_glob(rel, candidate.name, pattern):
        disabled.update(
          code for code in KNOWN_RULE_IDS if _match_selectors(code, rules)
        )
  return disabled


def is_license_file(path):
  """True when a path has a common license, copying, or notice filename."""
  return bool(LICENSE_FILE_RE.match(Path(path).name))


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
  if path.suffix.lower() in SKIP_SUFFIXES or is_license_file(path):
    return True
  try:
    relative = path.resolve().relative_to(root.resolve())
  except ValueError:
    relative = path
  text = relative.as_posix()
  if any(part in SKIP_DIRS for part in Path(text).parts):
    return True
  base_skips = (
    ACTIVE_CONFIG["exclude"] if ACTIVE_CONFIG.get("exclude") is not None else SKIP_PATHS
  )
  all_ignores = (*ignores, *ACTIVE_CONFIG.get("extend_exclude", ()))
  if any(text == item or text.startswith(item + "/") for item in base_skips):
    return True
  return any(
    text == item or text.startswith(item + "/") or _match_glob(text, path.name, item)
    for item in all_ignores
  )


def git_ignored(root, candidates):
  """Return the subset of candidate Paths that git check-ignore marks as ignored."""
  if not candidates or not ACTIVE_CONFIG.get("respect_gitignore", True):
    return set()
  base = root.resolve()
  rel_map = {}
  for candidate in candidates:
    try:
      rel = candidate.resolve().relative_to(base).as_posix()
    except ValueError:
      rel = candidate.as_posix()
    rel_map[rel] = candidate
  try:
    proc = subprocess.run(
      ["git", "-C", str(base), "check-ignore", "-z", "--stdin"],
      input="\0".join(rel_map),
      capture_output=True,
      text=True,
      check=False,
    )
  except OSError:
    return set()
  if proc.returncode not in (0, 1):
    return set()
  return {rel_map[item] for item in proc.stdout.split("\0") if item in rel_map}


def walk(targets, ignores):
  """Yield every text file under the targets that survives the skip rules."""
  for target in targets:
    path = Path(target)
    if path.is_file():
      if path.suffix.lower() not in SKIP_SUFFIXES and not is_license_file(path):
        yield path
      continue
    root = path if path.is_dir() else Path(".")
    candidates = [
      candidate
      for candidate in sorted(root.rglob("*"))
      if candidate.is_file() and not skipped(candidate, root, ignores)
    ]
    ignored_set = git_ignored(root, candidates)
    for candidate in candidates:
      if candidate not in ignored_set:
        yield candidate


def head_marker(line, position=None):
  """Identify a header marker with no avoided word before it on the line."""
  return marker_head(line, position, ROTATIONS)


def exclusions(path, lines):
  """Collect every marker and the known rule IDs it suppresses."""
  file_ids, line_ids, marks = collect_markers(path, lines, KNOWN_RULE_IDS, ROTATIONS)
  return file_ids | config_file_exclusions(path), line_ids, marks


def suppressed(code, ids, name=None):
  """Return whether the active marker set suppresses a rule ID."""
  return code in ids


def plain(word, name, use):
  """Give the matched word its plain word, and keep the old word's case."""
  if name in KEEP_LOWER:
    return use
  normalized = " ".join(word.lower().split())
  form = EXCEPTIONS.get(normalized) or re.sub(
    re.escape(name), use, word, flags=re.IGNORECASE
  )
  if word.isupper():
    return form.upper()
  if word[:1].isupper():
    return form[:1].upper() + form[1:]
  return form


LIST_THE_SPAN = re.compile(
  r"\b(the[ \t]+[^,.;:!?()\n]+)"
  r"((?:,[ \t]+(?:(?:and|or)[ \t]+)?the[ \t]+[^,.;:!?()\n]+)+"
  r"(?:\s+(?:and|or)[ \t]+the[ \t]+[^,.;:!?()\n]+)?)",
  re.IGNORECASE,
)
SUBSEQUENT_LIST_THE = re.compile(
  r"(,[ \t]+(?:(?:and|or)[ \t]+)?|[ \t]+(?:and|or)[ \t]+)the[ \t]+",
  re.IGNORECASE,
)


def collapse_list_articles(line):
  """Collapse repeated leading 'the' articles across comma-separated list items."""
  return LIST_THE_SPAN.sub(
    lambda match: match.group(1) + SUBSEQUENT_LIST_THE.sub(r"\1", match.group(2)),
    line,
  )


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
  if PROSE_RULE_IDS["curly_quote"] not in suppressed:
    line = line.translate(CURLY_QUOTES)
  return line


def format_finding(path, line, code, message, source_line=None, status=None):
  """Format one finding in STE-linter style with an indented source snippet."""
  advisory = code in ADVISORY_RULE_IDS or status == "manual_review"
  level = "advisory" if advisory else "advisory-free"
  suffix = " (manual review)" if advisory and "(manual review)" not in message else ""
  header = f"{path}:{line}: [{code}] ({level}) {message}{suffix}"
  if source_line is None:
    return header
  snippet = source_line.strip()
  if len(snippet) > 100:
    snippet = snippet[:97] + "..."
  return f"{header}\n    {snippet}" if snippet else header


def format_summary(findings):
  """Format the STE-linter summary footer for a non-empty finding list."""
  if not findings:
    return ""
  hard = sum(1 for item in findings if not is_advisory(item))
  advisory = len(findings) - hard
  files = len({item.splitlines()[0].rsplit(":", 2)[0] for item in findings})
  return (
    f"\nSummary: {hard} hard violation(s), "
    f"{advisory} advisory finding(s) across {files} file(s)."
  )


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
    if path.suffix.lower() == ".jsonc":
      comment_map = {}
      for comment_text, first, _ in slash_comments(text):
        for offset, comment_line in enumerate(comment_text.splitlines()):
          comment_map.setdefault(first + offset, []).append(comment_line)
      line_pairs = sorted(
        (number, "\n".join(parts)) for number, parts in comment_map.items()
      )
    else:
      line_pairs = list(enumerate(lines, 1))
    for number, line in line_pairs:
      if TABLE_ROW.match(line.strip()):
        continue
      excluded = file_ids | line_ids[number - 1]
      visible = MARKER.sub(lambda match: " " * len(match[0]), line)
      for name, pattern, use in ROTATIONS:
        code = RULE_IDS[name]
        if suppressed(code, excluded, name):
          continue
        for word in sorted(set(re.findall(pattern, visible, flags=re.IGNORECASE))):
          if name in KEEP_LOWER and word == use:
            continue
          findings.append(format_finding(path, number, code, f"{word} -> {use}", line))
  return findings, marks


def is_advisory(finding):
  return is_advisory_finding(finding, ADVISORY_RULE_IDS)


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
    the_cap = ACTIVE_CONFIG.get("the_density_cap", THE_DENSITY_CAP)
    min_the = ACTIVE_CONFIG.get("min_the_count", MIN_THE_COUNT)
    block_lines = {}
    for start_line, _, _, line_numbers in prose_blocks(text):
      block_lines.setdefault(start_line, set()).update(line_numbers)
    dense_lines = set()
    for row_line, _, _ in prose_the_density(text, cap=the_cap, min_the=min_the):
      group = block_lines.get(row_line, {row_line})
      if not any("NMO902" in (file_ids | line_ids[ln - 1]) for ln in group):
        dense_lines.update(group)
    rewritten, touched = [], False
    for number, line in enumerate(lines, 1):
      if TABLE_ROW.match(line.strip()):
        rewritten.append(line)
        continue
      suppressed = file_ids | line_ids[number - 1]
      collapse_the = number in dense_lines and "NMO902" not in suppressed
      parts, start = [], 0
      for marker in MARKER.finditer(line):
        seg = rotate(line[start : marker.start()], suppressed)
        if collapse_the:
          seg = collapse_list_articles(seg)
        parts.extend((seg, marker[0]))
        start = marker.end()
      tail = rotate(line[start:], suppressed)
      if collapse_the:
        tail = collapse_list_articles(tail)
      parts.append(tail)
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
  block_kind = "paragraph"
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
    nonlocal block_kind
    if not block:
      return
    if block_kind in {"list", "blockquote"}:
      for number, raw_line, _ in block:
        sentences.extend((number, 1, count) for count in lengths(raw_line))
      joined = " ".join(clean_line for _, _, clean_line in block)
      count = sum(1 for match in parts(joined) if match[1].strip())
      if count:
        paragraphs.append((block[0][0], count, block_kind))
    elif re.match(r"(?:[#>*+|-]|\d+[.)]\s)", block[0][1].lstrip()):
      for number, raw_line, _ in block:
        sentences.extend((number, 1, count) for count in lengths(raw_line))
    else:
      offsets, offset, count = [], 0, 0
      for number, raw_line, _ in block:
        offsets.append((offset, number))
        offset += len(raw_line) + 1
      for match in parts(" ".join(raw_line for _, raw_line, _ in block)):
        value = match[1]
        if not value.strip():
          continue
        start = match.start(1) + len(value) - len(value.lstrip())
        number = next(number for offset, number in reversed(offsets) if offset <= start)
        sentences.append((number, 1, len(value.split())))
        count += 1
      paragraphs.append((block[0][0], count, "paragraph"))
    block.clear()
    block_kind = "paragraph"

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
      for segment in row:
        li_items = re.findall(
          r"<li\b[^>]*>(.*?)</li>", segment, flags=re.IGNORECASE | re.DOTALL
        )
        if li_items:
          for item in li_items:
            item_lengths = lengths(item)
            sentences.extend((number, 1, count) for count in item_lengths)
            if item_lengths:
              paragraphs.append((number, len(item_lengths), "list"))
          rest_cell = re.sub(
            r"<(?:ul|ol)\b[^>]*>.*?</(?:ul|ol)>",
            " ",
            segment,
            flags=re.IGNORECASE | re.DOTALL,
          )
          rest_lengths = lengths(rest_cell)
          sentences.extend((number, 1, count) for count in rest_lengths)
          if rest_lengths:
            paragraphs.append((number, len(rest_lengths), "table"))
        else:
          cell_lengths = lengths(segment)
          sentences.extend((number, 1, count) for count in cell_lengths)
          if cell_lengths:
            paragraphs.append((number, len(cell_lengths), "table"))
      continue
    stripped = line.lstrip()
    if re.match(r"^#{1,6}(?:\s+|$)", stripped):
      flush()
      sentences.extend((number, 1, count) for count in lengths(line))
      heading_body = re.sub(r"^#{1,6}\s*(?:\d+[.)]\s+)?", "", stripped)
      heading_count = len(lengths(heading_body))
      if heading_count:
        paragraphs.append((number, heading_count, "heading"))
      continue
    if stripped.startswith(">"):
      if block and block_kind != "blockquote":
        flush()
      inner = re.sub(r"^\s*>\s?", "", line)
      if not inner.strip():
        flush()
        continue
      block_kind = "blockquote"
      block.append((number, line, inner))
      continue
    if re.match(r"^(?:[*+-]|\d+[.)])\s+", stripped):
      flush()
      item_body = re.sub(r"^(?:[*+-]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", stripped)
      block_kind = "list"
      block.append((number, line, item_body))
      continue
    if block and block_kind == "blockquote":
      flush()
    block.append((number, line, line))
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


def prose_blocks(text):
  """Yield `(start_line, kind, clean_text, line_numbers)` for each prose block."""
  lines = text.splitlines()
  blocks, block = [], []
  block_kind = "paragraph"
  fence, width, divider_pending = None, 0, False

  def clean_line(value):
    clean = re.sub(r"(`+)(.*?)\1", lambda match: " " * len(match[0]), value)
    return blank_inline_comments(clean)

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
    nonlocal block_kind
    if not block:
      return
    joined = " ".join(part for _, part in block)
    if joined.strip():
      blocks.append(
        (block[0][0], block_kind, joined, tuple(number for number, _ in block))
      )
    block.clear()
    block_kind = "paragraph"

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
    if TABLE_ROW.match(line.strip()):
      flush()
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
      for segment in row:
        li_items = re.findall(
          r"<li\b[^>]*>(.*?)</li>", segment, flags=re.IGNORECASE | re.DOTALL
        )
        if li_items:
          for item in li_items:
            cleaned_item = clean_line(item)
            if cleaned_item.strip():
              blocks.append((number, "list", cleaned_item, (number,)))
          rest_cell = re.sub(
            r"<(?:ul|ol)\b[^>]*>.*?</(?:ul|ol)>",
            " ",
            segment,
            flags=re.IGNORECASE | re.DOTALL,
          )
          cleaned_rest = clean_line(rest_cell)
          if cleaned_rest.strip():
            blocks.append((number, "table", cleaned_rest, (number,)))
        else:
          cleaned_cell = clean_line(segment)
          if cleaned_cell.strip():
            blocks.append((number, "table", cleaned_cell, (number,)))
      continue
    stripped = line.lstrip()
    if re.match(r"^#{1,6}(?:\s+|$)", stripped):
      flush()
      heading_body = re.sub(r"^#{1,6}\s*(?:\d+[.)]\s+)?", "", stripped)
      cleaned_heading = clean_line(heading_body)
      if cleaned_heading.strip():
        blocks.append((number, "heading", cleaned_heading, (number,)))
      continue
    if stripped.startswith(">"):
      if block and block_kind != "blockquote":
        flush()
      inner = re.sub(r"^\s*>\s?", "", line)
      if not inner.strip():
        flush()
        continue
      block_kind = "blockquote"
      block.append((number, clean_line(inner)))
      continue
    if re.match(r"^(?:[*+-]|\d+[.)])\s+", stripped):
      flush()
      item_body = re.sub(r"^(?:[*+-]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", stripped)
      block_kind = "list"
      block.append((number, clean_line(item_body)))
      continue
    if block and block_kind == "blockquote":
      flush()
    block.append((number, clean_line(line)))
  flush()
  return blocks


def prose_block_matches(text, matcher, skip_kinds=frozenset()):
  """Collect `(start_line, column, count)` on prose blocks outside fences, code spans, and comments."""
  rows = []
  for start_line, kind, clean, _ in prose_blocks(text):
    if kind in skip_kinds:
      continue
    hits = matcher(clean)
    if hits:
      rows.append((start_line, hits[0].start() + 1, len(hits)))
  return rows


def prose_passives(text):
  """Find passive-voice constructions across prose blocks outside code fences and code spans."""
  return prose_block_matches(
    text,
    lambda clean: [
      match
      for match in PASSIVE_VOICE_RE.finditer(clean)
      if match.group(1).lower() not in PASSIVE_STATE_ADJECTIVES
      or re.match(r"\s+by\b", clean[match.end() :], re.IGNORECASE)
    ],
  )


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
  return prose_block_matches(text, lambda clean: list(PHRASAL_VERB_RE.finditer(clean)))


def prose_ellipsis(text):
  return prose_block_matches(text, lambda clean: list(ELLIPSIS_RE.finditer(clean)))


def prose_serial_commas(text):
  return prose_block_matches(
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


DANGLING_CONJUNCTION_RE = re.compile(
  r"^\s*(?:[-*+]|\d+[.)])\s+.*[,;]\s+(?:and|or)[.;:]?\s*$",
  re.IGNORECASE,
)
NOMINALIZATION_RE = re.compile(
  r"\b(?:make|made|makes|making)\s+(?:a\s+|an\s+|the\s+)?"
  r"(?:decision|determination|adjustment|modification|selection|recommendation|assumption)\b|"
  r"\b(?:perform|performed|performs|performing|conduct|conducted|conducts|conducting)\s+"
  r"(?:a\s+|an\s+|the\s+)?"
  r"(?:validation|verification|investigation|analysis|assessment|evaluation|examination|inspection|comparison)\b|"
  r"\b(?:provide|provided|provides|providing|give|gave|gives|giving)\s+"
  r"(?:a\s+|an\s+|the\s+)?(?:description|explanation|indication|illustration)\b|"
  r"\b(?:is|are|was|were)\s+indicative\s+of\b|"
  r"\b(?:for\s+the\s+purpose\s+of|with\s+the\s+aim\s+of)\b",
  re.IGNORECASE,
)
COMPOUND_INSTRUCTION_RE = re.compile(
  r"\b(?:click|press|tap|select|open|close|save|delete|run|execute)\s+"
  r"[^.!?;,]{2,40},\s+and\s+then\s+"
  r"(?:click|press|tap|select|open|close|save|delete|run|execute)\b",
  re.IGNORECASE,
)
NOUN_CLUSTER_WORD = (
  r"(?:system|server|client|user|interface|configuration|management|process|service|"
  r"module|component|pipeline|workflow|database|connection|request|response|handler|"
  r"controller|policy|validation|execution|parameter|environment|storage|session|"
  r"token|state|schema|gateway|proxy|adapter|provider|registry)"
)
NOUN_CLUSTER_RE = re.compile(
  rf"\b{NOUN_CLUSTER_WORD}(?:\s+{NOUN_CLUSTER_WORD}){{3,}}\b",
  re.IGNORECASE,
)
SYNONYM_CLUSTER_RE = re.compile(
  r"\b(?:rules?\s+(?:and|or)\s+regulations?|null\s+and\s+void|each\s+and\s+every|"
  r"first\s+and\s+foremost|basic\s+and\s+fundamental|various\s+and\s+sundry|"
  r"checks?\s+and\s+validations?)\b",
  re.IGNORECASE,
)
COMPOUND_TENSE_RE = re.compile(
  r"\b(?:will\s+have\s+[a-z]{3,}ed|would\s+have\s+[a-z]{3,}ed|"
  r"(?:has|have|had)\s+been\s+[a-z]{3,}ing)\b",
  re.IGNORECASE,
)
STAGED_FRAME_RE = re.compile(
  r"\b(?:let's\s+dive\s+in|without\s+further\s+ado|in\s+today's\s+fast-paced\s+world|"
  r"in\s+conclusion,\s+by\s+leveraging|all\s+things\s+considered,\s+it\s+is\s+clear|"
  r"hope\s+this\s+helps)\b",
  re.IGNORECASE,
)
STACKED_QUALIFIER_RE = re.compile(
  r"\b(?:very\s+unique|completely\s+essential|absolutely\s+critical|"
  r"truly\s+remarkable|undeniably\s+important|extremely\s+crucial|highly\s+pivotal)\b",
  re.IGNORECASE,
)
AI_VOCABULARY_RE = re.compile(
  r"\b(?:delve|delves|delved|delving|tapestry|testament\s+to|beacon\s+of|"
  r"paradigm\s+shift|game-changer|groundbreaking|synergy|unleash(?:ing)?|"
  r"elevate\s+your|foster(?:ing)?\s+innovation|realm\s+of)\b",
  re.IGNORECASE,
)
ARROW_RE = re.compile(r"\u2192")
CHATBOT_COURTESY_RE = re.compile(
  r"\b(?:as\s+an\s+ai(?:\s+language\s+model)?|"
  r"as\s+of\s+my\s+last\s+(?:knowledge\s+)?(?:update|cutoff)|"
  r"i\s+don't\s+have\s+access\s+to\s+real-time|certainly!\s+here\s+is|"
  r"sure\s+thing!\s+here)\b",
  re.IGNORECASE,
)
CURLY_QUOTE_RE = re.compile(r"[\u201c\u201d\u2018\u2019]")
NEGATIVE_CONTRAST_RE = re.compile(
  r"\bnot\s+(?:just|merely|simply)\s+[^,.!?;]{2,40},\s+but\s+"
  r"(?:rather|instead|also)?\s*[A-Za-z]",
  re.IGNORECASE,
)
APHORISM_RE = re.compile(
  r"\b(?:at\s+the\s+end\s+of\s+the\s+day\b|the\s+bottom\s+line\s+is\b|"
  r"it\s+goes\s+without\s+saying\b|key\s+takeaway:|why\s+this\s+matters:)",
  re.IGNORECASE,
)
RHETORICAL_SETUP_RE = re.compile(
  r"\b(?:you\s+might\s+be\s+wondering|have\s+you\s+ever\s+wondered|"
  r"some\s+critics\s+might\s+argue|what\s+if\s+i\s+told\s+you)\b",
  re.IGNORECASE,
)
PROSE_DASH_RE = re.compile(
  r"(?:[A-Za-z0-9]\u2014[A-Za-z0-9]|\s\u2013\s|\\u201[34])",
  re.IGNORECASE,
)
UI_STRING_DASH_RE = re.compile(
  r"""(['"`])(?:\\.|(?!\1)[^\r\n])*?(?:[\u2013\u2014]|\\u201[34])(?:\\.|(?!\1)[^\r\n])*?\1""",
  re.IGNORECASE,
)
VAGUE_ATTRIBUTION_RE = re.compile(
  r"\b(?:experts\s+agree\s+that|studies\s+have\s+shown\s+that|"
  r"researchers\s+say\s+that|industry\s+leaders\s+believe|"
  r"many\s+people\s+think\s+that)\b",
  re.IGNORECASE,
)
COPULA_AVOIDANCE_RE = re.compile(
  r"\b(?:serves\s+as\s+a|stands\s+as\s+a|boasts\s+a|"
  r"features\s+a\s+wide\s+array\s+of)\b",
  re.IGNORECASE,
)
BOLD_LABEL_BULLET_RE = re.compile(
  r"^\s*[-*+]\s+\*\*[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)?:\*\*"
)
EMOJI_CHAR = r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
CONSECUTIVE_EMOJI_RE = re.compile(rf"{EMOJI_CHAR}\s*{EMOJI_CHAR}")
SELF_NARRATION_RE = re.compile(
  r"\b(?:this\s+section\s+(?:covers|explains|describes|outlines)|"
  r"in\s+this\s+(?:document|section|guide),\s+we\s+will)\b",
  re.IGNORECASE,
)
WORDY_PHRASE_RE = re.compile(
  r"\b(?:in\s+order\s+to\s+[a-z]{2,}|prior\s+to|subsequent\s+to|"
  r"due\s+to\s+the\s+fact\s+that|in\s+the\s+event\s+that|by\s+means\s+of|"
  r"at\s+this\s+point\s+in\s+time|at\s+the\s+present\s+time|"
  r"(?:is|are|was|were)\s+(?:able\s+to|capable\s+of))\b",
  re.IGNORECASE,
)
ARCHAIC_CONNECTOR_RE = re.compile(
  r"\b(?:heretofore|herein(?:after)?|there(?:of|in|by)|where(?:by|in|of)|"
  r"aforementioned|pursuant\s+to|inter\s+alia)\b",
  re.IGNORECASE,
)
INLINE_LIST_RE = re.compile(
  r"(?:(?<!\w)\(1\)\s+\S.*?\s+\(2\)\s+\S.*?\s+\(3\)\s+\S|"
  r"(?<!\w)\(a\)\s+\S.*?\s+\(b\)\s+\S.*?\s+\(c\)\s+\S)",
  re.IGNORECASE,
)
DOUBLE_NEGATIVE_RE = re.compile(
  r"\b(?:not\s+(?:uncommon|unlikely|impossible|insignificant|unreasonable|"
  r"infrequent(?:ly)?|unavailable|unlike|unaware)|never\s+fails\s+to)\b",
  re.IGNORECASE,
)


def prose_dangling_conjunctions(text):
  return prose_line_matches(
    text, lambda clean: list(DANGLING_CONJUNCTION_RE.finditer(clean))
  )


def prose_nominalizations(text):
  return prose_block_matches(
    text, lambda clean: list(NOMINALIZATION_RE.finditer(clean))
  )


def prose_wordy_phrases(text):
  return prose_block_matches(
    text,
    lambda clean: list(WORDY_PHRASE_RE.finditer(clean)),
    skip_kinds=frozenset({"blockquote"}),
  )


def prose_archaic_connectors(text):
  return prose_block_matches(
    text, lambda clean: list(ARCHAIC_CONNECTOR_RE.finditer(clean))
  )


def prose_inline_lists(text):
  return prose_block_matches(text, lambda clean: list(INLINE_LIST_RE.finditer(clean)))


def prose_double_negatives(text):
  return prose_block_matches(
    text, lambda clean: list(DOUBLE_NEGATIVE_RE.finditer(clean))
  )


def prose_compound_instructions(text):
  return prose_block_matches(
    text, lambda clean: list(COMPOUND_INSTRUCTION_RE.finditer(clean))
  )


def prose_noun_clusters(text):
  return prose_block_matches(text, lambda clean: list(NOUN_CLUSTER_RE.finditer(clean)))


def prose_synonym_clusters(text):
  return prose_block_matches(
    text, lambda clean: list(SYNONYM_CLUSTER_RE.finditer(clean))
  )


def prose_compound_tenses(text):
  return prose_block_matches(
    text, lambda clean: list(COMPOUND_TENSE_RE.finditer(clean))
  )


def prose_staged_frames(text):
  return prose_block_matches(text, lambda clean: list(STAGED_FRAME_RE.finditer(clean)))


def prose_stacked_qualifiers(text):
  return prose_block_matches(
    text, lambda clean: list(STACKED_QUALIFIER_RE.finditer(clean))
  )


def prose_ai_vocabulary(text):
  return prose_block_matches(text, lambda clean: list(AI_VOCABULARY_RE.finditer(clean)))


def prose_arrows(text):
  return prose_line_matches(text, lambda clean: list(ARROW_RE.finditer(clean)))


def prose_chatbot_courtesy(text):
  return prose_block_matches(
    text, lambda clean: list(CHATBOT_COURTESY_RE.finditer(clean))
  )


def prose_curly_quotes(text):
  return prose_line_matches(text, lambda clean: list(CURLY_QUOTE_RE.finditer(clean)))


def prose_negative_contrasts(text):
  return prose_block_matches(
    text, lambda clean: list(NEGATIVE_CONTRAST_RE.finditer(clean))
  )


def prose_aphorisms(text):
  return prose_block_matches(text, lambda clean: list(APHORISM_RE.finditer(clean)))


def prose_rhetorical_setups(text):
  return prose_block_matches(
    text, lambda clean: list(RHETORICAL_SETUP_RE.finditer(clean))
  )


def prose_repeated_openers(text):
  """Find three consecutive sentences in a prose block with the same two-word opener."""
  rows = []
  for start_line, _, clean, _ in prose_blocks(text):
    sentences = [
      part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()
    ]
    openers = []
    for sentence in sentences:
      words = re.findall(r"[A-Za-z]+", sentence)
      openers.append(
        f"{words[0].lower()} {words[1].lower()}" if len(words) >= 2 else ""
      )
    for pos in range(len(openers) - 2):
      if openers[pos] and openers[pos] == openers[pos + 1] == openers[pos + 2]:
        rows.append((start_line, 1, 1))
        break
  return rows


def prose_dashes(text):
  return prose_line_matches(text, lambda clean: list(PROSE_DASH_RE.finditer(clean)))


def prose_vague_attributions(text):
  return prose_block_matches(
    text, lambda clean: list(VAGUE_ATTRIBUTION_RE.finditer(clean))
  )


def prose_copula_avoidance(text):
  return prose_block_matches(
    text, lambda clean: list(COPULA_AVOIDANCE_RE.finditer(clean))
  )


def prose_bold_label_bullets(text):
  return prose_line_matches(
    text, lambda clean: list(BOLD_LABEL_BULLET_RE.finditer(clean))
  )


def prose_consecutive_emojis(text):
  return prose_line_matches(
    text, lambda clean: list(CONSECUTIVE_EMOJI_RE.finditer(clean))
  )


def prose_self_narration(text):
  return prose_block_matches(
    text, lambda clean: list(SELF_NARRATION_RE.finditer(clean))
  )


def prose_the_density(text, cap=THE_DENSITY_CAP, min_the=MIN_THE_COUNT):
  """Find prose blocks where 'the' appears at least min_the times and >= cap% of words."""
  rows = []
  for start_line, _, clean, _ in prose_blocks(text):
    words = re.findall(r"[A-Za-z0-9_'-]+", clean)
    hits = list(re.finditer(r"\bthe\b", clean, re.IGNORECASE))
    if len(hits) >= min_the and words:
      density = round(len(hits) * 100 / len(words))
      if density >= cap:
        rows.append((start_line, hits[0].start() + 1, density))
  return rows


LINE_PROSE_RULE_IDS = {
  "STE001",
  "STE100",
  "STE200",
  "HMN400",
  "HMN600",
  "HMN905",
  "HMN908",
  "HMN909",
}


def prose_review(path, text, context, paragraph_cap):
  """Return unsuppressed measurable candidates for one explicit text scope."""
  lines = text.splitlines()
  file_ids, line_ids, _ = exclusions(path, lines)
  block_lines = {}
  for start_line, _, _, line_numbers in prose_blocks(text):
    block_lines.setdefault(start_line, set()).update(line_numbers)

  def rule_suppressed(code, number):
    if code in LINE_PROSE_RULE_IDS:
      return 1 <= number <= len(line_ids) and suppressed(code, line_ids[number - 1])
    return any(
      suppressed(code, line_ids[ln - 1])
      for ln in block_lines.get(number, (number,))
      if 1 <= ln <= len(line_ids)
    )

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
  sentence_caps = {
    "instruction": ACTIVE_CONFIG.get(
      "instruction_sentence_cap", SENTENCE_CAPS["instruction"]
    ),
    "description": ACTIVE_CONFIG.get(
      "description_sentence_cap", SENTENCE_CAPS["description"]
    ),
  }
  cap = sentence_caps.get(context, sentence_caps["instruction"])
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
    ("dangling_conjunction", prose_dangling_conjunctions),
    ("nominalization", prose_nominalizations),
    ("wordy_phrase", prose_wordy_phrases),
    ("archaic_connector", prose_archaic_connectors),
    ("passive", prose_passives),
    ("phrasal", prose_phrasal_verbs),
    ("ellipsis", prose_ellipsis),
    ("compound_instruction", prose_compound_instructions),
    ("noun_cluster", prose_noun_clusters),
    ("synonym_cluster", prose_synonym_clusters),
    ("compound_tense", prose_compound_tenses),
    ("inline_list", prose_inline_lists),
    ("double_negative", prose_double_negatives),
    ("staged_frame", prose_staged_frames),
    ("stacked_qualifier", prose_stacked_qualifiers),
    ("ai_vocabulary", prose_ai_vocabulary),
    ("arrow", prose_arrows),
    ("chatbot_courtesy", prose_chatbot_courtesy),
    ("curly_quote", prose_curly_quotes),
    ("negative_contrast", prose_negative_contrasts),
    ("aphorism", prose_aphorisms),
    ("rhetorical_setup", prose_rhetorical_setups),
    ("repeated_opener", prose_repeated_openers),
    ("prose_dash", prose_dashes),
    ("vague_attribution", prose_vague_attributions),
    ("copula_avoidance", prose_copula_avoidance),
    ("bold_label_bullet", prose_bold_label_bullets),
    ("consecutive_emoji", prose_consecutive_emojis),
    ("self_narration", prose_self_narration),
    ("serial_comma", prose_serial_commas),
  ):
    code = PROSE_RULE_IDS[key]
    status = "manual_review" if code in ADVISORY_RULE_IDS else "review"
    if not suppressed(code, file_ids):
      for number, column, count in detector(text):
        if not rule_suppressed(code, number):
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
    for item in paragraphs:
      number, count, kind = item if len(item) == 3 else (*item, "paragraph")
      if kind == "heading":
        cap = ACTIVE_CONFIG.get("heading_sentence_cap", HEADING_SENTENCE_CAP)
        status = "review"
      elif kind == "list":
        cap = ACTIVE_CONFIG.get("list_sentence_cap", LIST_SENTENCE_CAP)
        status = "review"
      elif kind == "blockquote":
        cap = paragraph_cap
        status = "manual_review"
      else:
        cap = paragraph_cap
        status = "review"
      if count > cap and not rule_suppressed(code, number):
        findings.append(
          {
            "rule": code,
            "path": str(path),
            "line": number,
            "column": 1,
            "count": count,
            "cap": cap,
            "kind": kind,
            "status": status,
          }
        )
  code = PROSE_RULE_IDS["the_density"]
  the_cap = ACTIVE_CONFIG.get("the_density_cap", THE_DENSITY_CAP)
  min_the = ACTIVE_CONFIG.get("min_the_count", MIN_THE_COUNT)
  if not suppressed(code, file_ids):
    for number, column, density in prose_the_density(
      text, cap=the_cap, min_the=min_the
    ):
      if not rule_suppressed(code, number):
        findings.append(
          {
            "rule": code,
            "path": str(path),
            "line": number,
            "column": column,
            "count": density,
            "cap": the_cap,
            "status": "manual_review",
          }
        )
  return findings


def source_prose(path, text, context, paragraph_cap):
  """Check extracted code prose while retaining physical source locations."""
  if context == "unknown" and (
    path.name in {"README.md", "CHANGELOG.md", "INSTALL.md"} or "docs" in path.parts
  ):
    context = "description"
  if path.suffix in COMMENT_SUFFIXES or path.name in SPECIAL_CODE_NAMES:
    spans = prose_spans(path, text)
  elif path.suffix in {".json", ".webmanifest", ".csv", ".lock"}:
    spans = []
  else:
    spans = [(text, 1, len(text.splitlines()))]
  lines = text.splitlines()
  file_ids, line_ids, _ = exclusions(path, lines)
  findings = []
  if path.suffix in COMMENT_SUFFIXES or path.name in SPECIAL_CODE_NAMES:
    code = PROSE_RULE_IDS["comment_block"]
    comment_cap = ACTIVE_CONFIG.get("comment_block_cap", COMMENT_BLOCK_CAP)
    if not suppressed(code, file_ids):
      for start, count in comment_blocks(path, text):
        if count > comment_cap and not suppressed(
          code, set().union(*line_ids[start - 1 : start - 1 + count])
        ):
          findings.append(
            {
              "rule": code,
              "path": str(path),
              "line": start,
              "column": 1,
              "count": count,
              "cap": comment_cap,
              "status": "review",
            }
          )
  for value, first, last in spans:
    cleaned_span = MARKER_SPAN.sub(lambda match: " " * len(match[0]), value)
    span_blocks = {}
    for start_line, _, _, line_numbers in prose_blocks(cleaned_span):
      span_blocks.setdefault(start_line, set()).update(line_numbers)
    for row in prose_review(path, cleaned_span, context, paragraph_cap):
      local_line = row["line"]
      if len(value.splitlines()) > last - first + 1:
        row["line"] = first
        phys_lines = range(first, last + 1)
      elif row["rule"] in LINE_PROSE_RULE_IDS:
        row["line"] = local_line + first - 1
        phys_lines = (row["line"],)
      else:
        row["line"] = local_line + first - 1
        phys_lines = [
          ln + first - 1 for ln in span_blocks.get(local_line, (local_line,))
        ]
      ids = set(file_ids)
      for ln in phys_lines:
        if 1 <= ln <= len(line_ids):
          ids |= line_ids[ln - 1]
      if not suppressed(row["rule"], ids):
        findings.append(row)
  if path.name.endswith(".user.js"):
    code = PROSE_RULE_IDS["prose_dash"]
    if not suppressed(code, file_ids):
      seen_lines = {row["line"] for row in findings if row["rule"] == code}
      for number, line in enumerate(lines, 1):
        if number in seen_lines or suppressed(code, line_ids[number - 1]):
          continue
        visible = MARKER_SPAN.sub(lambda match: " " * len(match[0]), line)
        matches = list(UI_STRING_DASH_RE.finditer(visible))
        if matches:
          findings.append(
            {
              "rule": code,
              "path": str(path),
              "line": number,
              "column": matches[0].start() + 1,
              "count": len(matches),
              "cap": 0,
              "status": "manual_review",
            }
          )
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
      pyproject = parent / "pyproject.toml"
      patterns, roots = None, None
      if config.is_file():
        settings = configparser.ConfigParser(interpolation=None)
        settings.read(config, encoding="utf-8")
        patterns = settings.get("pytest", "python_files", fallback="test_*.py").split()
        roots = settings.get("pytest", "testpaths", fallback=".").split()
      elif pyproject.is_file():
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        ini = raw.get("tool", {}).get("pytest", {}).get("ini_options")
        if isinstance(ini, dict):
          pf = ini.get("python_files", ["test_*.py"])
          tp = ini.get("testpaths", ["."])
          patterns = [pf] if isinstance(pf, str) else list(pf)
          roots = [tp] if isinstance(tp, str) else list(tp)
      if patterns is not None and roots is not None:
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
  global ACTIVE_CONFIG
  argv = list(sys.argv[1:] if argv is None else argv)
  if argv[:1] == ["prose"]:
    return prose_main(argv[1:])
  if argv[:1] == ["review-tests"]:
    return review_tests_main(argv[1:])
  if argv[:1] in (["audit"], ["collect"]):
    argv = ["--audit", *argv[1:]]
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
    "--audit",
    action="store_true",
    help="list every nonemo marker and manual-review finding",
  )
  parser.add_argument(
    "--format", action="store_true", help="apply the rotations in place"
  )
  parser.add_argument(
    "--selftest", action="store_true", help="run built-in self-tests and exit"
  )
  parser.add_argument("--context", choices=[*SENTENCE_CAPS, "unknown"], default=None)
  parser.add_argument("--paragraph-cap", type=int, default=None)
  parser.add_argument("--test-helper", action="append", type=Path, default=[])
  parser.add_argument("--config", type=Path, default=None)
  parser.add_argument("--no-config", action="store_true")
  arguments = parser.parse_args(argv)
  if arguments.selftest:
    assert rotate("Transmit the kind.") == "Send the type."
    sample = prose_review(
      Path("sample.md"), "Start; stop.\n", "description", PARAGRAPH_CAP
    )
    assert any(row["rule"] == "STE100" for row in sample)
    return 0
  targets = arguments.targets or ["."]
  try:
    ACTIVE_CONFIG = load_config(targets, arguments.config, arguments.no_config)
  except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
    parser.error(str(error))
  context = (
    arguments.context
    if arguments.context is not None
    else (ACTIVE_CONFIG.get("context") or "unknown")
  )
  paragraph_cap = (
    arguments.paragraph_cap
    if arguments.paragraph_cap is not None
    else (ACTIVE_CONFIG.get("paragraph_cap") or PARAGRAPH_CAP)
  )
  if not 0 < paragraph_cap <= PARAGRAPH_CAP:
    parser.error(f"--paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
  test_helpers = [
    *ACTIVE_CONFIG.get("test_helpers", ()),
    *arguments.test_helper,
  ]
  for helper in test_helpers:
    if not Path(helper).is_file():
      parser.error(f"test helper does not exist: {helper}")
  if arguments.format:
    changed = format_paths(targets, arguments.ignore)
    print(f"{changed} lines rotated", file=sys.stderr)
    return 0
  findings, marks = scan(targets, arguments.ignore)
  if arguments.ignores:
    print("\n".join(marks))
    return 0
  errors, tests = [], []
  helpers = {Path(path).resolve() for path in test_helpers}
  for path in walk([*targets, *test_helpers], arguments.ignore):
    text = text_file(path)
    try:
      if not arguments.audit and is_test_file(path, text or "", helpers):
        tests.append(str(path))
      if text is None:
        continue
      rows = source_prose(path, text, context, paragraph_cap)
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
    lines = text.splitlines()
    findings.extend(
      format_finding(
        row["path"],
        row["line"],
        row["rule"],
        describe_finding(row),
        lines[row["line"] - 1] if 0 < row["line"] <= len(lines) else None,
        status=row.get("status"),
      )
      for row in rows
    )
  if arguments.audit:
    print("\n".join(audit_lines(marks, findings, ADVISORY_RULE_IDS)))
    if errors:
      print("\n".join(errors), file=sys.stderr)
      return 2
    return 0
  result = review_tests(tests)
  for row in result["findings"]:
    test_lines = (text_file(Path(row["path"])) or "").splitlines()
    findings.append(
      format_finding(
        row["path"],
        row["line"],
        row["rule"],
        row["evidence"],
        test_lines[row["line"] - 1] if 0 < row["line"] <= len(test_lines) else None,
      )
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
  print("\n".join(findings) + ("\n" + format_summary(findings) if findings else ""))
  if errors:
    print("\n".join(errors), file=sys.stderr)
    return 2
  return int(any(not is_advisory(finding) for finding in findings))


if __name__ == "__main__":
  sys.exit(main())
