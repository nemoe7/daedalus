"""Extract code prose without executing source files."""

import ast
import io
import json
import re
import subprocess
import tokenize
from functools import lru_cache
from pathlib import Path

COMMENT_BLOCK_CAP = 2
DIRECTIVE_MARKER = re.compile(
  r"^\s*(?:#|//)\s*(?:arena-preview-[\w-]+|case:\s*[\w-]+|shellcheck\s+disable=\S+|"
  r"nonemo:\s*[A-Za-z0-9_-]+|noqa(?::.*)?|type:\s*ignore(?:\[.*\])?|pragma:\s*no cover|region|endregion)\s*$",
  re.IGNORECASE,
)
USERSCRIPT_SUFFIX = ".user.js"
USERSCRIPT_HEADER_START = "==UserScript=="
USERSCRIPT_HEADER_END = "==/UserScript=="
METADATA_PROSE_KEYS = {"name", "description"}
COMMENT_SUFFIXES = {
  ".bat",
  ".cmd",
  ".cjs",
  ".css",
  ".html",
  ".ini",
  ".js",
  ".mjs",
  ".jsonc",
  ".py",
  ".sh",
  ".svg",
  ".toml",
  ".yml",
  ".yaml",
}
JAVASCRIPT_SUFFIXES = {".cjs", ".js", ".mjs"}
SLASH_SUFFIXES = {".css", ".jsonc"}
HTML_SUFFIXES = {".html", ".svg"}
HASH_SUFFIXES = {".ini", ".sh", ".toml", ".yml", ".yaml"}
SPECIAL_CODE_NAMES = {"Dockerfile", "arena-preview"}


def python_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect Python comments with their source line spans."""
  comments: list[tuple[str, int, int]] = []
  for token in tokenize.generate_tokens(io.StringIO(source).readline):
    if token.type == tokenize.COMMENT:
      comments.append(
        (token.string.lstrip("# ").strip(), token.start[0], token.start[0])
      )
  return comments


def python_docstrings(source: str) -> list[tuple[str, int, int]]:
  """Collect Python docstrings with their source line spans."""
  sections = []
  lines = source.splitlines(True)
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
        text = first.value.value.strip()
        if text:
          head = (
            lines[first.value.lineno - 1]
            .encode("utf-8")[first.value.col_offset :]
            .decode("utf-8")
          )
          tail = "".join(lines[first.value.lineno : first.value.end_lineno])
          literal = (head + tail).lstrip("rRuU")
          width = 3 if literal.startswith(('"""', "'''")) else 1
          inner = literal[width:]
          leading = inner[: len(inner) - len(inner.lstrip())]
          sections.append(
            (text, first.value.lineno + leading.count("\n"), first.value.end_lineno)
          )
  return sections


def python_sections(source: str) -> list[str]:
  """Collect Python docstrings and individual comments."""
  return [
    text for text, _, _ in python_docstrings(source) + python_comments(source) if text
  ]


@lru_cache(maxsize=128)
def javascript_comments(
  source: str, skip_metadata: bool = False
) -> tuple[tuple[str, int, int], ...]:
  """Collect JavaScript comments and their source lines with the parser."""
  result = subprocess.run(
    ["node", str(Path(__file__).with_name("javascript-comments.cjs"))],
    input=source,
    capture_output=True,
    text=True,
    check=True,
  )
  comments = []
  in_metadata = False
  for text, first, last in json.loads(result.stdout):
    clean = "\n".join(
      line.strip().lstrip("*").strip() for line in text.splitlines()
    ).strip()
    if skip_metadata and clean == USERSCRIPT_HEADER_START:
      in_metadata = True
    elif skip_metadata and clean == USERSCRIPT_HEADER_END:
      in_metadata = False
    elif not in_metadata:
      comments.append((clean, first, last))
  return tuple(comments)


def clean_comment(text: str) -> str:
  """Strip line decoration and outer whitespace from one comment block."""
  return "\n".join(
    line.strip().lstrip("*").strip() for line in text.splitlines()
  ).strip()


def slash_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect slash and block comments with their source line spans."""
  comments: list[tuple[str, int, int]] = []
  state = "code"
  quote = ""
  buffer = ""
  line = 1
  start_line = 1
  index = 0

  while index < len(source):
    char = source[index]
    next_char = source[index + 1] if index + 1 < len(source) else ""
    consumed = 1
    if state == "string":
      if char == "\\":
        consumed = 2
      elif char == quote:
        state = "code"
    elif state == "line-comment":
      if char == "\n":
        comments.append((clean_comment(buffer), start_line, line))
        buffer = ""
        state = "code"
      else:
        buffer += char
    elif state == "block-comment":
      if char == "*" and next_char == "/":
        comments.append((clean_comment(buffer), start_line, line))
        buffer = ""
        state = "code"
        consumed = 2
      else:
        buffer += char
    elif char in "'\"`":
      state = "string"
      quote = char
    elif char == "/" and next_char == "/":
      state = "line-comment"
      start_line = line
      buffer = ""
      consumed = 2
    elif char == "/" and next_char == "*":
      state = "block-comment"
      start_line = line
      buffer = ""
      consumed = 2
    line += source[index : index + consumed].count("\n")
    index += consumed

  if state == "line-comment":
    comments.append((clean_comment(buffer), start_line, line))
  return comments


def hash_comments(
  source: str, suffix: str, skip_shebang: bool = False
) -> list[tuple[str, int, int]]:
  """Collect hash and INI semicolon comments with their source line spans."""
  comments: list[tuple[str, int, int]] = []
  for number, line in enumerate(source.splitlines(), start=1):
    if skip_shebang and line.startswith("#!"):
      continue
    quote = ""
    escaped = False
    for index, char in enumerate(line):
      if quote:
        if escaped:
          escaped = False
        elif char == "\\":
          escaped = True
        elif char == quote:
          quote = ""
        continue
      if char in "'\"":
        quote = char
        continue
      starts_comment = char == "#" and (
        suffix == ".toml" or index == 0 or line[index - 1].isspace()
      )
      starts_comment = starts_comment or (
        suffix == ".ini" and char == ";" and (index == 0 or line[index - 1].isspace())
      )
      if starts_comment:
        comments.append((line[index + 1 :].strip(), number, number))
        break
  return comments


def html_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect HTML and XML comment blocks with their source line spans."""
  comments: list[tuple[str, int, int]] = []
  cursor = 0
  while True:
    start = source.find("<!--", cursor)
    if start < 0:
      break
    end = source.find("-->", start + 4)
    if end < 0:
      break
    first = source.count("\n", 0, start) + 1
    last = source.count("\n", 0, end) + 1
    comments.append((source[start + 4 : end].strip(), first, last))
    cursor = end + 3
  return comments


def batch_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect batch REM and double-colon comments with their source line spans."""
  comments: list[tuple[str, int, int]] = []
  for number, line in enumerate(source.splitlines(), start=1):
    stripped = line.lstrip()
    if stripped.startswith("@"):
      stripped = stripped[1:].lstrip()
    upper = stripped.upper()
    if upper == "REM" or upper.startswith("REM "):
      comments.append((stripped[3:].lstrip(), number, number))
    elif stripped.startswith("::"):
      comments.append((stripped[2:].strip(), number, number))
  return comments


def span_comments(
  path: Path, source: str, skip_shebang: bool = False
) -> list[tuple[str, int, int]] | None:
  """Return the comment spans for one code path, or None when no scanner covers it."""
  if path.suffix in JAVASCRIPT_SUFFIXES:
    return list(javascript_comments(source, path.name.endswith(USERSCRIPT_SUFFIX)))
  if path.suffix in SLASH_SUFFIXES:
    return slash_comments(source)
  if path.suffix in HTML_SUFFIXES:
    return html_comments(source)
  if path.suffix in HASH_SUFFIXES or path.name in SPECIAL_CODE_NAMES:
    return hash_comments(source, path.suffix, skip_shebang)
  if path.suffix in {".bat", ".cmd"}:
    return batch_comments(source)
  return None


def userscript_metadata_sections(source: str) -> list[str]:
  """Collect prose values from required userscript metadata."""
  sections: list[str] = []
  in_metadata = False
  for line in source.splitlines():
    stripped = line.lstrip()
    if not stripped.startswith("//"):
      continue
    comment = stripped[2:].strip()
    if comment == USERSCRIPT_HEADER_START:
      in_metadata = True
      continue
    if comment == USERSCRIPT_HEADER_END:
      break
    if not in_metadata or not comment.startswith("@"):
      continue
    key, _, value = comment[1:].partition(" ")
    if key in METADATA_PROSE_KEYS and value.strip():
      sections.append(value.strip())
  return sections


def source_sections(path: Path, source: str) -> list[str]:
  """Collect ordinary comments and docstrings from one tracked code file."""
  if path.suffix == ".py":
    return python_sections(source)
  spans = span_comments(path, source)
  return [text for text, _, _ in spans if text] if spans is not None else []


def python_comment_lines(source: str) -> set[int]:
  """Collect Python comment lines beside their spans, never a shebang."""
  lines = source.splitlines()
  return {
    first
    for _, first, _ in python_comments(source)
    if not lines[first - 1].startswith("#!")
  }


def comment_lines(path: Path, source: str) -> set[int]:
  """Collect physical lines that hold ordinary code comments."""
  if path.suffix == ".py":
    return python_comment_lines(source)
  spans = span_comments(path, source, skip_shebang=True)
  return (
    {line for _, first, last in spans for line in range(first, last + 1)}
    if spans is not None
    else set()
  )


def directive_marker_lines(source: str, path: Path | None = None) -> set[int]:
  """Return comment lines that carry required tool directives."""
  numbers = {
    number
    for number, line in enumerate(source.splitlines(), start=1)
    if DIRECTIVE_MARKER.match(line)
  }
  if path is not None:
    comments = (
      python_comments(source)
      if path.suffix == ".py"
      else span_comments(path, source) or []
    )
    numbers.update(
      first
      for text, first, last in comments
      if first == last and DIRECTIVE_MARKER.fullmatch("# " + text)
    )
  return numbers


def comment_blocks(path: Path, source: str) -> list[tuple[int, int]]:
  """Return (start_line, line_count) for contiguous non-directive code comment blocks."""
  blocks: list[tuple[int, int]] = []
  start = None
  previous = None
  numbers = sorted(comment_lines(path, source) - directive_marker_lines(source, path))
  for number in [*numbers, None]:
    if number is not None and (previous is None or number == previous + 1):
      start = number if start is None else start
      previous = number
      continue
    if start is not None and previous is not None:
      blocks.append((start, previous - start + 1))
    start = number
    previous = number
  return blocks


def prose_spans(path: Path, source: str) -> list[tuple[str, int, int]]:
  """Extract prose with source lines, joining adjacent comment lines."""
  if path.suffix == ".py":
    docs = python_docstrings(source)
    comments = [
      row
      for row in python_comments(source)
      if not source.splitlines()[row[1] - 1].startswith("#!")
    ]
  else:
    docs = []
    comments = span_comments(path, source, skip_shebang=True) or []
  grouped = []
  source_lines = source.splitlines()
  for text, first, last in comments:
    if text.strip():
      needle = text.splitlines()[0].strip()
      for offset, line in enumerate(source_lines[first - 1 : last]):
        if needle in line:
          first += offset
          break
    if grouped and first == grouped[-1][2] + 1:
      previous, start, _ = grouped[-1]
      gap = max(1, first - start - previous.count("\n"))
      grouped[-1] = (previous + "\n" * gap + text, start, last)
    else:
      grouped.append((text, first, last))
  return sorted(docs + grouped, key=lambda row: row[1])
