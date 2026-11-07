"""Server-side CommonMark renderer powered by markdown-it-py for Arena Steering."""

from __future__ import annotations

import html
import re
from functools import lru_cache
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_inline.state_inline import Delimiter
from markdown_it.token import Token

ICON_COPY = (
  '<svg class="lucide lucide-copy" width="14" height="14" viewBox="0 0 24 24" '
  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
  'stroke-linejoin="round" aria-hidden="true" focusable="false">'
  '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect>'
  '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>'
)
ICON_MARKDOWN = (
  '<svg class="markdown-icon" width="14" height="14" viewBox="0 0 24 24" '
  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
  'stroke-linejoin="round" aria-hidden="true" focusable="false">'
  '<path d="M22.27 19.385H1.73A1.73 1.73 0 0 1 0 17.655V6.345a1.73 1.73 0 0 1 '
  "1.73-1.73h20.54A1.73 1.73 0 0 1 24 6.345v11.308a1.73 1.73 0 0 1-1.73 1.731Z"
  "m-16.501-3.462v-4.5l2.308 2.885 2.307-2.885v4.5h2.308V8.078h-2.308l-2.307 "
  "2.885-2.308-2.885H3.46v7.847Zm15.463-3.923h-2.309V8.077h-2.307V12h-2.308"
  'l3.461 4.039Z" fill="currentColor" stroke="none"></path></svg>'
)
ICON_BRACES = (
  '<svg class="lucide lucide-braces" width="14" height="14" viewBox="0 0 24 24" '
  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
  'stroke-linejoin="round" aria-hidden="true" focusable="false">'
  '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"></path>'
  '<path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"></path></svg>'
)

CODE_LANG_NAMES = {
  "bash": "Bash",
  "c": "C",
  "cjs": "JavaScript",
  "cpp": "C++",
  "cs": "C#",
  "css": "CSS",
  "diff": "Diff",
  "go": "Go",
  "html": "HTML",
  "java": "Java",
  "javascript": "JavaScript",
  "js": "JavaScript",
  "json": "JSON",
  "jsx": "JSX",
  "kt": "Kotlin",
  "markdown": "Markdown",
  "md": "Markdown",
  "mermaid": "Mermaid",
  "mjs": "JavaScript",
  "php": "PHP",
  "patch": "Diff",
  "plaintext": "Text",
  "py": "Python",
  "python": "Python",
  "rb": "Ruby",
  "rs": "Rust",
  "rust": "Rust",
  "sh": "Shell",
  "shell": "Shell",
  "sql": "SQL",
  "svg": "SVG",
  "swift": "Swift",
  "text": "Text",
  "toml": "TOML",
  "ts": "TypeScript",
  "tsx": "TSX",
  "txt": "Text",
  "typescript": "TypeScript",
  "xml": "XML",
  "yaml": "YAML",
  "yml": "YAML",
  "zsh": "Zsh",
}

SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify_heading(text: str) -> str:
  """Build a stable `sec-...` anchor slug from a heading's plain text."""
  slug = SLUG_NON_ALNUM_RE.sub("-", str(text or "").lower()).strip("-")
  return f"sec-{slug}" if slug else "sec-heading"


def format_code_language(raw_lang: str) -> str:
  """Return a human-readable display label for a fenced code block language."""
  key = str(raw_lang or "").strip().split(None, 1)[0].lower() if raw_lang else ""
  if not key:
    return "Code"
  return CODE_LANG_NAMES.get(key, key[:1].upper() + key[1:])


def highlight_diff_code(raw_code: str) -> str:
  """Highlight unified diff / patch lines with semantic token spans."""
  lines = raw_code.split("\n")
  if lines and lines[-1] == "":
    lines = lines[:-1]
  out: list[str] = []
  for line in lines:
    escaped = html.escape(line, quote=False)
    if line.startswith(("diff ", "index ", "--- ", "+++ ")):
      out.append(f'<span class="token comment">{escaped}</span>')
    elif line.startswith("@@"):
      out.append(f'<span class="token coord">{escaped}</span>')
    elif line.startswith("+"):
      out.append(f'<span class="token inserted">{escaped}</span>')
    elif line.startswith("-"):
      out.append(f'<span class="token deleted">{escaped}</span>')
    else:
      out.append(escaped)
  return "\n".join(out)


def _highlight_mark_plugin(parser: MarkdownIt) -> None:
  """Register `==highlight==` inline `<mark>` delimiters on `parser`."""

  def tokenize(state: Any, silent: bool) -> bool:
    if silent or state.src[state.pos] != "=":
      return False
    scanned = state.scanDelims(state.pos, True)
    length = scanned.length
    if length < 2:
      return False
    if length % 2:
      state.push("text", "", 0).content = "="
      length -= 1
    for _ in range(0, length, 2):
      state.push("text", "", 0).content = "=="
      if scanned.can_open or scanned.can_close:
        state.delimiters.append(
          Delimiter(
            marker=ord("="),
            length=0,
            token=len(state.tokens) - 1,
            end=-1,
            open=scanned.can_open,
            close=scanned.can_close,
          )
        )
    state.pos += scanned.length
    return True

  def post_process(state: Any) -> None:
    groups = [state.delimiters] + [
      meta["delimiters"] for meta in state.tokens_meta if meta and "delimiters" in meta
    ]
    for delimiters in groups:
      lone: list[int] = []
      for start in delimiters:
        if start.marker != ord("=") or start.end == -1:
          continue
        end = delimiters[start.end]
        for index, nesting in ((start.token, 1), (end.token, -1)):
          token = state.tokens[index]
          token.type = "mark_open" if nesting == 1 else "mark_close"
          token.tag, token.nesting = "mark", nesting
          token.markup, token.content = "==", ""
        previous = state.tokens[end.token - 1]
        if previous.type == "text" and previous.content == "=":
          lone.append(end.token - 1)
      while lone:
        index = lone.pop()
        end_idx = index + 1
        while (
          end_idx < len(state.tokens) and state.tokens[end_idx].type == "mark_close"
        ):
          end_idx += 1
        end_idx -= 1
        if index != end_idx:
          state.tokens[index], state.tokens[end_idx] = (
            state.tokens[end_idx],
            state.tokens[index],
          )

  parser.inline.ruler.before("emphasis", "mark", tokenize)
  parser.inline.ruler2.before("emphasis", "mark", post_process)


AUTOLINK_URL_RE = re.compile(r"(?<![\"'=])(https?://[^\s<>()]+)")
TASK_ITEM_RE = re.compile(r"^\[([ xX])\]\s+(.*)$", re.DOTALL)


def _gfm_extensions_plugin(parser: MarkdownIt) -> None:
  """Register GFM task-list checkboxes and bare-URL autolinks on `parser`."""

  def transform(state: Any) -> None:
    tokens = state.tokens
    for idx, token in enumerate(tokens):
      if token.type != "inline":
        continue
      if (
        idx >= 2
        and tokens[idx - 1].type == "paragraph_open"
        and tokens[idx - 2].type == "list_item_open"
      ):
        match = TASK_ITEM_RE.match(token.content)
        if match and token.children:
          checked = match.group(1).lower() == "x"
          token.content = match.group(2)
          first_child = token.children[0]
          if first_child.type == "text":
            first_child.content = TASK_ITEM_RE.sub(r"\2", first_child.content, count=1)
          label_text = "".join(
            " " if child.type in {"softbreak", "hardbreak"} else child.content
            for child in token.children
            if child.type
            in {
              "text",
              "text_special",
              "code_inline",
              "image",
              "softbreak",
              "hardbreak",
            }
          )
          label_text = " ".join(label_text.split())
          checkbox = Token("html_inline", "", 0)
          checked_attr = " checked" if checked else ""
          checkbox.content = (
            '<input type="checkbox" class="task-list-item-checkbox" '
            f'disabled{checked_attr} aria-label="{html.escape(label_text, quote=True)}">'
          )
          token.children.insert(0, checkbox)
          li_tok = tokens[idx - 2]
          existing_cls = li_tok.attrGet("class") or ""
          li_tok.attrSet("class", f"{existing_cls} task-list-item".strip())
      if not token.children:
        continue
      new_children = []
      in_link = 0
      for child in token.children:
        if child.type == "link_open":
          in_link += 1
          new_children.append(child)
          continue
        if child.type == "link_close":
          in_link = max(0, in_link - 1)
          new_children.append(child)
          continue
        if in_link == 0 and child.type == "text" and "http" in child.content:
          last_end = 0
          text_val = child.content
          for m in AUTOLINK_URL_RE.finditer(text_val):
            start, end = m.span(1)
            url = m.group(1).rstrip(".,;:!?")
            end = start + len(url)
            if start > last_end:
              before_tok = Token("text", "", 0)
              before_tok.content = text_val[last_end:start]
              new_children.append(before_tok)
            open_tok = Token("link_open", "a", 1)
            open_tok.attrSet("href", url)
            open_tok.attrSet("target", "_blank")
            open_tok.attrSet("rel", "noopener noreferrer")
            text_tok = Token("text", "", 0)
            text_tok.content = url
            close_tok = Token("link_close", "a", -1)
            new_children.extend([open_tok, text_tok, close_tok])
            last_end = end
          if last_end > 0:
            if last_end < len(text_val):
              after_tok = Token("text", "", 0)
              after_tok.content = text_val[last_end:]
              new_children.append(after_tok)
            continue
        new_children.append(child)
      token.children = new_children

  parser.core.ruler.after("inline", "gfm_extensions", transform)


def render_code_block(raw_lang: str, raw_code: str) -> str:
  """Render a fenced or indented code block with header, copy button, and diff tokens."""
  lang_key = str(raw_lang or "").strip().split(None, 1)[0].lower() if raw_lang else ""
  code_without_trailing = raw_code.removesuffix("\n")
  payload = html.escape(code_without_trailing, quote=True).replace("\n", "&#10;")

  if lang_key in {"diff", "patch"}:
    inner_code = highlight_diff_code(code_without_trailing)
  else:
    inner_code = html.escape(code_without_trailing, quote=False)

  lang_attr = (
    f' class="language-{html.escape(lang_key, quote=True)}"' if lang_key else ""
  )
  md_button = (
    f'<button type="button" class="preview-code-md" aria-label="Preview Markdown" '
    f'title="Preview Markdown" aria-pressed="false" data-code="{payload}">{ICON_MARKDOWN}</button>'
    if lang_key in {"markdown", "md"}
    else ""
  )
  mermaid_button = (
    f'<button type="button" class="preview-code-mermaid" aria-label="Show Mermaid code" '
    f'title="Show Mermaid code" aria-pressed="false" data-code="{payload}">{ICON_BRACES}</button>'
    if lang_key == "mermaid"
    else ""
  )
  mermaid_state_attr = ' data-mermaid-state="pending"' if lang_key == "mermaid" else ""
  copy_button = (
    f'<button type="button" class="copy-code" aria-label="Copy code" '
    f'data-code="{payload}">{ICON_COPY}</button>'
  )
  lang_label = html.escape(format_code_language(lang_key), quote=True)
  header = (
    f'<div class="code-block-header"><span class="code-block-lang">'
    f"{ICON_BRACES}<span>{lang_label}</span></span></div>"
  )
  return (
    f'<div class="code-block"{mermaid_state_attr}>{md_button}{mermaid_button}{copy_button}{header}'
    f"<pre><code{lang_attr}>{inner_code}</code></pre></div>"
  )


def _two_space_code(state: Any, start: int, end: int, silent: bool) -> bool:
  if state.blkIndent != 0 or not 2 <= state.sCount[start] < 4:
    return False
  content = state.src[state.bMarks[start] + state.tShift[start] : state.eMarks[start]]
  if re.match(
    r"(?:[-+*](?:\s|$)|\d{1,9}[.)](?:\s|$)|#{1,6}(?:\s|$)|>|`{3,}|~{3,}|<|\|)", content
  ):
    return False
  if silent:
    return True
  last = next_line = start + 1
  while next_line < end:
    if state.isEmpty(next_line):
      next_line += 1
      continue
    if state.sCount[next_line] >= 2:
      next_line += 1
      last = next_line
      continue
    break
  state.line = last
  token = state.push("code_block", "code", 0)
  token.content = state.getLines(start, last, 2, False) + "\n"
  token.map = [start, last]
  return True


def _create_parser(*, breaks: bool = False, report: bool = False) -> MarkdownIt:
  parser = MarkdownIt("commonmark", {"html": report, "breaks": breaks}).enable(
    ["table", "strikethrough"]
  )
  parser.block.ruler.before("code", "two_space_code", _two_space_code)
  if not report:
    _highlight_mark_plugin(parser)
  _gfm_extensions_plugin(parser)

  def _render_link_open(
    renderer: Any, tokens: Any, idx: int, options: Any, env: Any
  ) -> str:
    token = tokens[idx]
    href = token.attrGet("href") or ""
    if href and not href.startswith("#"):
      token.attrSet("target", "_blank")
      token.attrSet("rel", "noopener noreferrer")
    return str(renderer.renderToken(tokens, idx, options, env))

  def _render_heading_open(
    renderer: Any, tokens: Any, idx: int, options: Any, env: Any
  ) -> str:
    token = tokens[idx]
    inline_token = tokens[idx + 1] if idx + 1 < len(tokens) else None
    raw_text = (inline_token.content if inline_token is not None else "").strip()
    heading_id = slugify_heading(raw_text)
    token.attrSet("id", heading_id)
    headings = env.get("headings") if isinstance(env, dict) else None
    if isinstance(headings, list) and token.tag.startswith("h"):
      try:
        level = int(token.tag[1:])
      except ValueError:
        level = 2
      headings.append({"id": heading_id, "level": level, "text": raw_text})
    return str(renderer.renderToken(tokens, idx, options, env))

  def _render_fence(
    _renderer: Any, tokens: Any, idx: int, _options: Any, _env: Any
  ) -> str:
    token = tokens[idx]
    info = html.unescape(token.info.strip()) if token.info else ""
    return render_code_block(info, token.content)

  def _render_code_block(
    _renderer: Any, tokens: Any, idx: int, _options: Any, _env: Any
  ) -> str:
    token = tokens[idx]
    return render_code_block("", token.content)

  parser.add_render_rule("link_open", _render_link_open)
  parser.add_render_rule("heading_open", _render_heading_open)
  parser.add_render_rule("fence", _render_fence)
  parser.add_render_rule("code_block", _render_code_block)
  return parser


_PARSER_STANDARD = _create_parser(breaks=False)
_PARSER_BREAKS = _create_parser(breaks=True)
_PARSER_REPORT = _create_parser(report=True)
_PARAGRAPH_HTML = re.compile(r"<p>.*?</p>", re.DOTALL)
_PARAGRAPH_BREAK = re.compile(r"<br\s*/?>")


_MARKDOWN_CACHE_SIZE = 256
_MARKDOWN_CACHE_TEXT_LIMIT = 32768


def _render_cached_value(
  text: str, report: bool, breaks: bool, pre_wrap: bool
) -> tuple[str, tuple[tuple[str, int, str], ...]]:
  parser = _PARSER_REPORT if report else _PARSER_BREAKS if breaks else _PARSER_STANDARD
  headings: list[dict[str, Any]] = []
  rendered = str(parser.render(text, {"headings": headings})).strip()
  if pre_wrap:
    rendered = _PARAGRAPH_HTML.sub(
      lambda match: _PARAGRAPH_BREAK.sub("", match.group(0)), rendered
    )
  return rendered, tuple((item["id"], item["level"], item["text"]) for item in headings)


_render_cached = lru_cache(maxsize=_MARKDOWN_CACHE_SIZE)(_render_cached_value)


def _render_with_cache(
  markdown: str,
  report: bool,
  breaks: bool,
  pre_wrap: bool,
  headings: list[dict[str, Any]] | None,
) -> str:
  text = str(markdown or "")
  render = (
    _render_cached if len(text) <= _MARKDOWN_CACHE_TEXT_LIMIT else _render_cached_value
  )
  rendered, cached_headings = render(text, report, breaks, pre_wrap)
  if headings is not None:
    headings.extend(
      {"id": id_, "level": level, "text": title}
      for id_, level, title in cached_headings
    )
  return rendered


def render_markdown(
  markdown: str,
  *,
  breaks: bool = False,
  pre_wrap: bool = False,
  headings: list[dict[str, Any]] | None = None,
) -> str:
  """Render CommonMark text to safe HTML using markdown-it-py."""
  return _render_with_cache(markdown, False, breaks, pre_wrap, headings)


def render_report_markdown(
  markdown: str, *, headings: list[dict[str, Any]] | None = None
) -> str:
  """Render trusted report Markdown with raw HTML and ordinary equals signs."""
  return _render_with_cache(markdown, True, False, False, headings)


def unparsed_strike_lines(markdown: str) -> list[int]:
  """Locate paired strike markers left as text outside code."""
  parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
  parser.disable("text_join")
  lines = markdown.splitlines()
  findings: set[int] = set()
  for token in parser.parse(markdown):
    if token.type != "inline" or not token.map:
      continue
    start, end = token.map
    raw = "\n".join(lines[start:end])
    cursor = 0
    text = ""
    offsets: list[int] = []
    for child in token.children or ():
      if child.type == "code_inline":
        opening = raw.find(child.markup, cursor)
        closing = (
          raw.find(child.markup, opening + len(child.markup)) if opening >= 0 else -1
        )
        cursor = closing + len(child.markup) if closing >= 0 else len(raw)
        text += "X"
        offsets.append(cursor)
        continue
      if child.type in {"softbreak", "hardbreak"}:
        newline = raw.find("\n", cursor)
        cursor = newline + 1 if newline >= 0 else cursor
        text += "\n"
        offsets.append(cursor)
        continue
      content = (
        child.markup
        if child.type in {"text_special", "s_open", "s_close"}
        else child.content
      )
      if not content:
        continue
      offset = raw.find(content, cursor)
      if offset < 0 and end - start > 1:
        continue
      offset = max(0, offset)
      cursor = offset + len(content)
      text += content if child.type == "text" else "X" * len(content)
      offsets.extend(offset + i for i in range(len(content)))
    for match in re.finditer(r"~~(.+?)~~", text):
      findings.add(start + 1 + raw.count("\n", 0, offsets[match.start()]))
  return sorted(findings)
