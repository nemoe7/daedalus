"""Split Markdown text into fence states, table cells, prose lines, and prose blocks."""

import re

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# The rotation table lists the avoided words, so its own rows never flag.
TABLE_ROW = re.compile(r"^\|\s*`[^`]+`\s*\|")
CODE_SPAN = re.compile(r"(`+)(.*?)\1")


def fence_states(lines):
  """Yield `(index, line, state)` where state is "fence", "inside", or "text"."""
  fence = None
  for index, line in enumerate(lines):
    marker = FENCE.match(line)
    if marker:
      if fence is None:
        fence = marker[1]
      elif (
        marker[1][0] == fence[0]
        and len(marker[1]) >= len(fence)
        and not line[marker.end() :].strip()
      ):
        fence = None
      yield index, line, "fence"
    elif fence is not None:
      yield index, line, "inside"
    else:
      yield index, line, "text"


def fenced_lines(lines):
  """Return physical line numbers inside Markdown fences, including delimiters."""
  return {index + 1 for index, _, state in fence_states(lines) if state != "text"}


def mask_code_spans(value):
  """Replace inline code spans with spaces to keep columns."""
  return CODE_SPAN.sub(lambda match: " " * len(match[0]), value)


def blank_inline_comments(line):
  """Replace inline HTML comments on one line with spaces to keep columns."""
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


def table_cells(value):
  """Return the stripped cells of one pipe table row, or an empty list."""
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


def prose_lines(text):
  """Yield `(line, clean)` for prose lines outside fences and code table rows."""
  for index, line, state in fence_states(text.splitlines()):
    if state != "text" or TABLE_ROW.match(line.strip()):
      continue
    yield index + 1, blank_inline_comments(mask_code_spans(line))


def prose_line_matches(text, matcher):
  """Collect `(line, column, count)` on prose lines outside fences, code spans, and comments."""
  rows = []
  for number, clean in prose_lines(text):
    hits = matcher(clean)
    if hits:
      rows.append((number, hits[0].start() + 1, len(hits)))
  return rows


def prose_counts(text):
  """Measure prose outside code fences and within table cells."""
  lines = text.splitlines()
  sentences, paragraphs, block = [], [], []
  block_kind = "paragraph"
  width, divider_pending = 0, False

  def parts(value):
    return re.finditer(r"(.*?)(?:[.!?](?:\s+|$)|$)", mask_code_spans(value), re.DOTALL)

  def lengths(value):
    return [len(match[1].split()) for match in parts(value) if match[1].strip()]

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

  for index, line, state in fence_states(lines):
    number = index + 1
    if state == "fence":
      flush()
      width, divider_pending = 0, False
      continue
    if state == "inside":
      continue
    if not line.strip():
      flush()
      width, divider_pending = 0, False
      continue
    if divider_pending:
      divider_pending = False
      continue
    row = table_cells(line)
    following = table_cells(lines[index + 1]) if index + 1 < len(lines) else []
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


def prose_blocks(text):
  """Return `(start_line, kind, clean_text, line_numbers)` for each prose block."""
  lines = text.splitlines()
  blocks, block = [], []
  block_kind = "paragraph"
  width, divider_pending = 0, False

  def clean_line(value):
    return blank_inline_comments(mask_code_spans(value))

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

  for index, line, state in fence_states(lines):
    number = index + 1
    if state == "fence":
      flush()
      width, divider_pending = 0, False
      continue
    if state == "inside":
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
    row = table_cells(line)
    following = table_cells(lines[index + 1]) if index + 1 < len(lines) else []
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
