"""Extract code prose without executing source files."""

import ast
import io
import json
import re
import subprocess
import tokenize
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

COMMENT_BLOCK_CAP = 2
DIRECTIVE_MARKER = re.compile(
  r"^\s*(?:#|//|--)\s*(?:arena-preview-[\w-]+|case:\s*[\w-]+|shellcheck\s+disable=\S+|"
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
  ".dbml",
  ".env",
  ".html",
  ".ini",
  ".j2",
  ".jinja",
  ".jinja2",
  ".js",
  ".mjs",
  ".jsonc",
  ".py",
  ".sh",
  ".sql",
  ".svg",
  ".toml",
  ".yml",
  ".yaml",
}
JAVASCRIPT_SUFFIXES = {".cjs", ".js", ".mjs"}
SLASH_SUFFIXES = {".css", ".dbml", ".jsonc"}
SQL_SUFFIXES = {".sql"}
JINJA_SUFFIXES = {".j2", ".jinja", ".jinja2"}
HTML_SUFFIXES = {".html", ".svg"}
HASH_SUFFIXES = {".env", ".ini", ".sh", ".toml", ".yml", ".yaml"}
SPECIAL_CODE_NAMES = {"Dockerfile", "arena-preview", ".env"}


def is_env_file(path: Path) -> bool:
  """Return whether path is a dotenv file such as .env or .env.example."""
  name = path.name.lower()
  return name == ".env" or name.startswith(".env.") or path.suffix.lower() == ".env"


def is_jinja_file(path: Path) -> bool:
  """Return whether path is a Jinja template such as .jinja, .j2, or .jinja2."""
  return any(suffix.lower() in JINJA_SUFFIXES for suffix in path.suffixes)


def is_code_file(path: Path) -> bool:
  """Return whether path uses comment extraction rather than full-file prose scanning."""
  return (
    path.suffix.lower() in COMMENT_SUFFIXES
    or path.name in SPECIAL_CODE_NAMES
    or is_env_file(path)
    or is_jinja_file(path)
  )


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


@lru_cache(maxsize=128)
def javascript_comment_spans(
  source: str,
) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
  """Collect exact JavaScript comment spans with the parser."""
  result = subprocess.run(
    ["node", str(Path(__file__).with_name("javascript-comments.cjs")), "--spans"],
    input=source,
    capture_output=True,
    text=True,
    check=True,
  )
  return tuple(
    ((first_line, first_column), (last_line, last_column))
    for first_line, first_column, last_line, last_column in json.loads(result.stdout)
  )


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


HTML_INLINE_TAGS = {
  "a",
  "b",
  "code",
  "em",
  "i",
  "kbd",
  "mark",
  "samp",
  "small",
  "span",
  "strong",
  "sub",
  "sup",
}
HTML_SKIP_TAGS = {"script", "style", "svg"}
HTML_PROSE_ATTRS = {"aria-label", "title", "placeholder"}


class _HTMLProseExtractor(HTMLParser):
  """Extract visible HTML text nodes and prose attributes with line spans."""

  def __init__(self) -> None:
    super().__init__(convert_charrefs=True)
    self.spans: list[tuple[str, int, int]] = []
    self.attr_spans: list[tuple[str, int, int]] = []
    self._skip_depth = 0
    self._buf: list[str] = []
    self._buf_start: int | None = None
    self._buf_end: int | None = None

  def flush(self) -> None:
    if self._buf and self._buf_start is not None and self._buf_end is not None:
      text = " ".join("".join(self._buf).split()).strip()
      if (
        text
        and re.search(r"[A-Za-z]{2,}", text)
        and not re.fullmatch(r"__[A-Z0-9_]+__", text)
      ):
        self.spans.append((text, self._buf_start, self._buf_end))
    self._buf = []
    self._buf_start = None
    self._buf_end = None

  def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
    lower = tag.lower()
    if lower not in HTML_INLINE_TAGS:
      self.flush()
    if lower in HTML_SKIP_TAGS:
      self._skip_depth += 1
      return
    if self._skip_depth == 0:
      line = self.getpos()[0]
      seen: set[str] = set()
      for key, value in attrs:
        if key and key.lower() in HTML_PROSE_ATTRS and value:
          clean = " ".join(value.split()).strip()
          if clean and clean not in seen and re.search(r"[A-Za-z]{2,}", clean):
            seen.add(clean)
            self.spans.append((clean, line, line))
            self.attr_spans.append((clean, line, line))

  def handle_endtag(self, tag: str) -> None:
    lower = tag.lower()
    if lower in HTML_SKIP_TAGS:
      if self._skip_depth > 0:
        self._skip_depth -= 1
      return
    if lower not in HTML_INLINE_TAGS:
      self.flush()

  def handle_data(self, data: str) -> None:
    if self._skip_depth > 0:
      return
    if not data.strip():
      if self._buf:
        self._buf.append(" ")
      return
    line = self.getpos()[0]
    if self._buf_start is None:
      self._buf_start = line
    self._buf_end = line + data.count("\n")
    self._buf.append(data)


def html_visible_prose(source: str) -> list[tuple[str, int, int]]:
  """Collect visible element text nodes and prose attributes from HTML."""
  extractor = _HTMLProseExtractor()
  extractor.feed(source)
  extractor.flush()
  return extractor.spans


def html_attr_spans(source: str) -> list[tuple[str, int, int]]:
  """Collect only HTML prose attribute values (aria-label, title, placeholder) with line spans."""
  extractor = _HTMLProseExtractor()
  extractor.feed(source)
  extractor.flush()
  return extractor.attr_spans


LABEL_KEYS = r'(?:"aria-label"|aria-label|title|placeholder|label)'
LABEL_LITERAL = re.compile(
  LABEL_KEYS
  + r'\s*[:=]\s*(?:"([^"\n]{2,120})"|\'([^\'\n]{2,120})\'|`([^`\n$]{2,120})`)'
)


def ui_label_spans(path: Path, source: str) -> list[tuple[str, int, int]]:
  """Collect short label literals that scripts hand to title, aria-label, placeholder, or label."""
  if path.suffix.lower() not in {".js", ".mjs", ".cjs", ".html", ".htm"}:
    return []
  spans: list[tuple[str, int, int]] = []
  for match in LABEL_LITERAL.finditer(source):
    text = next(group for group in match.groups() if group is not None).strip()
    if re.search(r"[A-Za-z]{2,}", text):
      line = source.count("\n", 0, match.start()) + 1
      spans.append((text, line, line))
  return spans


def sql_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect SQL -- line comments and /* ... */ block comments with their source line spans."""
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
      if char == quote and next_char == quote or char == "\\":
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
    elif char in "'\"":
      state = "string"
      quote = char
    elif char == "-" and next_char == "-":
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


def jinja_comments(source: str) -> list[tuple[str, int, int]]:
  """Collect Jinja {# ... #} and HTML <!-- ... --> comment blocks with their source line spans."""
  comments: list[tuple[str, int, int]] = list(html_comments(source))
  cursor = 0
  while True:
    start = source.find("{#", cursor)
    if start < 0:
      break
    end = source.find("#}", start + 2)
    if end < 0:
      break
    first = source.count("\n", 0, start) + 1
    last = source.count("\n", 0, end) + 1
    inner = source[start + 2 : end].lstrip("-").rstrip("-").strip()
    comments.append((inner, first, last))
    cursor = end + 2
  return sorted(comments, key=lambda row: row[1])


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
  suffix = path.suffix.lower()
  if suffix in JAVASCRIPT_SUFFIXES:
    return list(javascript_comments(source, path.name.endswith(USERSCRIPT_SUFFIX)))
  if suffix in SLASH_SUFFIXES:
    return slash_comments(source)
  if suffix in SQL_SUFFIXES:
    return sql_comments(source)
  if suffix in JINJA_SUFFIXES or is_jinja_file(path):
    return jinja_comments(source)
  if suffix in HTML_SUFFIXES:
    return html_comments(source)
  if suffix in HASH_SUFFIXES or path.name in SPECIAL_CODE_NAMES or is_env_file(path):
    return hash_comments(source, suffix, skip_shebang)
  if suffix in {".bat", ".cmd"}:
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


WORKFLOW_SKIP_PARENTS = {
  "with",
  "env",
  "environment",
  "inputs",
  "outputs",
  "secrets",
  "matrix",
  "strategy",
  "permissions",
  "concurrency",
  "defaults",
  "services",
  "container",
}


def is_workflow_yaml(path: Path, source: str = "") -> bool:
  """Return True when path is a GitHub Actions workflow YAML file."""
  if path.suffix.lower() not in {".yml", ".yaml"}:
    return False
  posix = path.as_posix().replace("\\", "/")
  if (
    ".github/workflows/" in posix
    or "workflows" in path.parts
    or "workflow" in path.stem.lower()
  ):
    return True
  return bool(source and re.search(r"(?m)^(?:on|jobs|steps)\s*:", source))


def _strip_yaml_comment(line: str) -> str:
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
    if char == "#" and (index == 0 or line[index - 1].isspace()):
      return line[:index]
  return line


def workflow_name_spans(source: str) -> list[tuple[str, int, int]]:
  """Extract root, job, and step prose name: scalars from workflow YAML."""
  spans: list[tuple[str, int, int]] = []
  stack: list[tuple[int, str]] = []
  block_indent: int | None = None
  for number, raw_line in enumerate(source.splitlines(), start=1):
    clean = _strip_yaml_comment(raw_line)
    if not clean.strip():
      continue
    indent = len(raw_line) - len(raw_line.lstrip(" "))
    if block_indent is not None:
      if indent > block_indent:
        continue
      block_indent = None
    match = re.match(r"^(\s*)(-\s+)?([A-Za-z0-9_.-]+)\s*:\s*(.*)$", clean)
    if not match:
      continue
    lead, dash, key, rest = match.groups()
    base_indent = len(lead)
    key_indent = base_indent + (len(dash) if dash else 0)
    if dash:
      while stack and (
        stack[-1][0] > base_indent
        or (stack[-1][0] == base_indent and stack[-1][1] != "steps")
      ):
        stack.pop()
    else:
      while stack and stack[-1][0] >= key_indent:
        stack.pop()
    val = rest.strip()
    if re.match(r"^[|>][-+]?\s*$", val):
      block_indent = base_indent
      stack.append((key_indent, key))
      continue
    if not val:
      stack.append((key_indent, key))
      continue
    if key != "name":
      continue
    parents = [parent_key for _, parent_key in stack]
    if any(parent in WORKFLOW_SKIP_PARENTS for parent in parents):
      continue
    is_root = not parents and not dash
    is_job = len(parents) == 2 and parents[0] == "jobs" and not dash
    is_step = "steps" in parents
    if not (is_root or is_job or is_step):
      continue
    if val.startswith(("{", "[")):
      continue
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
      val = val[1:-1]
    val = re.sub(r"\$\{\{.*?\}\}", " ", val)
    val = " ".join(val.split()).strip()
    if " " not in val or len(re.findall(r"[A-Za-z]{2,}", val)) < 2:
      continue
    spans.append((val, number, number))
  return spans


JS_TEST_SUFFIXES = (
  ".test.js",
  ".test.cjs",
  ".test.mjs",
  ".spec.js",
  ".spec.cjs",
  ".spec.mjs",
)
JS_UI_SINK_RE = re.compile(
  r"(?:\.\s*(?:textContent|innerText|title|placeholder|label|statusText|connectionBase)\s*[+]?="
  r"|[\"\']?(?:aria-label|title|placeholder|label)[\"\']?\s*:"
  r"|\bsetAttribute\s*\(\s*[\"\'](?:aria-label|title|placeholder)[\"\']\s*,"
  r"|\b(?:setConnection|reportStatusLine|copyFrom|label|setStatus|showStatus|toast|alert|confirm)\s*\()"
)
JS_UI_HELPER_VAR_RE = re.compile(
  r"\b(?:const|let|var)\s+(?:blocked|message|status|hint|summary|warning|reason)\s*="
)
JS_STRING_LITERAL_RE = re.compile(
  r'"([^"\\\n]*(?:\\.[^"\\\n]*)*)"'
  r"|'([^'\\\n]*(?:\\.[^'\\\n]*)*)'"
  r"|`([^`\\]*(?:\\.[^`\\]*)*)`"
)
JS_HTML_PROSE_TAG_RE = re.compile(
  r"<(?:p|div|span|button|a|h[1-6]|label|li|td|th|strong|em|time|article|section|header|footer|aside|main)\b",
  re.IGNORECASE,
)


def _iter_js_masked_templates(source: str) -> list[tuple[str, int]]:
  """Extract JS template literals with `${...}` replaced by `item` and line breaks preserved."""
  results: list[tuple[str, int]] = []
  length = len(source)

  def scan_code(idx: int, stop_brace: bool = False) -> int:
    depth = 1 if stop_brace else 0
    while idx < length:
      ch = source[idx]
      nxt = source[idx + 1] if idx + 1 < length else ""
      if ch == "/" and nxt == "/":
        nl = source.find("\n", idx + 2)
        idx = length if nl < 0 else nl + 1
        continue
      if ch == "/" and nxt == "*":
        end = source.find("*/", idx + 2)
        idx = length if end < 0 else end + 2
        continue
      if ch in {"'", '"'}:
        quote = ch
        idx += 1
        while idx < length:
          if source[idx] == "\\":
            idx += 2
          elif source[idx] == quote:
            idx += 1
            break
          else:
            idx += 1
        continue
      if ch == "`":
        idx = scan_template(idx + 1)
        continue
      if stop_brace:
        if ch == "{":
          depth += 1
        elif ch == "}":
          depth -= 1
          if depth == 0:
            return idx + 1
      idx += 1
    return idx

  def scan_template(start_idx: int) -> int:
    base_line = source.count("\n", 0, start_idx - 1) + 1
    buf: list[str] = []
    idx = start_idx
    while idx < length:
      ch = source[idx]
      nxt = source[idx + 1] if idx + 1 < length else ""
      if ch == "\\":
        buf.append(source[idx : idx + 2])
        idx += 2
        continue
      if ch == "$" and nxt == "{":
        expr_start = idx
        idx = scan_code(idx + 2, stop_brace=True)
        newlines = source.count("\n", expr_start, idx)
        buf.append("item" + ("\n" * newlines))
        continue
      if ch == "`":
        results.append(("".join(buf), base_line))
        return idx + 1
      buf.append(ch)
      idx += 1
    return idx

  scan_code(0, stop_brace=False)
  return results


def js_ui_prose_spans(path: Path, source: str) -> list[tuple[str, int, int]]:
  """Collect UI labels and DOM prose strings from JS files for full prose checks.

  NMU100 keeps its <=6-word cap in ui_label_spans so filler-article checks apply
  only to compact button/tab labels rather than full sentences, whereas this
  extractor feeds all UI labels and longer UI messages into prose_review.
  """
  lower_name = path.name.lower()
  if path.suffix.lower() not in JAVASCRIPT_SUFFIXES or lower_name.endswith(
    JS_TEST_SUFFIXES
  ):
    return []
  spans: list[tuple[str, int, int]] = []
  seen: set[tuple[str, int]] = set()
  for label_text, first, last in ui_label_spans(path, source):
    if (label_text, first) not in seen and "{" not in label_text:
      seen.add((label_text, first))
      spans.append((label_text, first, last))
  lines = source.splitlines()
  for idx, raw_line in enumerate(lines):
    if not (JS_UI_SINK_RE.search(raw_line) or JS_UI_HELPER_VAR_RE.search(raw_line)):
      continue
    chunk = raw_line
    end_idx = idx
    while (
      end_idx + 1 < len(lines)
      and end_idx - idx < 6
      and not chunk.rstrip().endswith((";", "{", "}"))
    ):
      end_idx += 1
      chunk += "\n" + lines[end_idx]
    for match in JS_STRING_LITERAL_RE.finditer(chunk):
      raw = next(group for group in match.groups() if group is not None)
      clean = re.sub(r"\$\{[^}]*\}", "item", raw)
      clean = re.sub(r"^[·•|\-\s:]+", "", clean).strip()
      if (
        len(clean) < 4
        or "<" in clean
        or "{" in clean
        or "}" in clean
        or "://" in clean
        or re.match(r"^[a-z]+/[a-z0-9.+-]+;", clean)
      ):
        continue
      if " " not in clean or len(re.findall(r"[A-Za-z]{2,}", val := clean)) < 2:
        continue
      line_no = idx + 1 + chunk[: match.start()].count("\n")
      end_line = line_no + raw.count("\n")
      if (val, line_no) not in seen:
        seen.add((val, line_no))
        spans.append((val, line_no, end_line))
  for masked, base_line in _iter_js_masked_templates(source):
    if not JS_HTML_PROSE_TAG_RE.search(masked):
      continue
    for text, local_first, local_last in html_visible_prose(masked):
      clean = re.sub(r"^[·•|\-\s:]+", "", text).strip()
      if (
        len(clean) < 4
        or "{" in clean
        or "}" in clean
        or "://" in clean
        or " " not in clean
        or len(re.findall(r"[A-Za-z]{2,}", clean)) < 2
      ):
        continue
      line_no = base_line + local_first - 1
      end_line = base_line + local_last - 1
      if (clean, line_no) not in seen:
        seen.add((clean, line_no))
        spans.append((clean, line_no, end_line))
  return sorted(spans, key=lambda row: row[1])


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
    if path.suffix == ".html" and not is_jinja_file(path):
      docs = html_visible_prose(source)
    elif path.suffix.lower() in JAVASCRIPT_SUFFIXES:
      docs = js_ui_prose_spans(path, source)
    elif is_workflow_yaml(path, source):
      docs = workflow_name_spans(source)
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
