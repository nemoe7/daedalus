"""Check UI labels for padding words and byte-unit labels for divisor mismatches."""

import re

NMU200_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".htm"}


NMU200_LABEL_RE = re.compile(r"\b(KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b")


NMU200_BINARY_RE = re.compile(
  r"/\s*\(*\s*(?:1024|1_024|1048576|1_048_576|1073741824|1_073_741_824)(?![\w.])"
  r"|/\s*\(*\s*1\s*<<\s*(?:10|20|30)(?!\w)"
  r"|/\s*\(*\s*1024\s*(?:\*\*|\^)\s*(?:2|3)(?!\w)"
)


NMU200_DECIMAL_RE = re.compile(
  r"/\s*\(*\s*(?:1000|1_000|1000000|1_000_000|1000000000|1_000_000_000)(?![\w.])"
  r"|/\s*\(*\s*1[eE][+]?0*(?:3|6|9)(?!\w)"
  r"|/\s*\(*\s*10\s*(?:\*\*|\^)\s*(?:3|6|9)(?!\w)"
  r"|/\s*\(*\s*1000\s*(?:\*\*|\^)\s*(?:2|3)(?!\w)"
)


NMU200_INTERPOLATION_RE = re.compile(r"\{([^{}]*)\}")


NMU100_MAX_WORDS = 6


PADDING = re.compile(r"\b(the|this|these|those|a|an|please|here|just)\b", re.IGNORECASE)


SENTENCE_END = re.compile(r"[.!?]$")


def nmu100_findings(spans, cap=NMU100_MAX_WORDS):
  """A label of cap words or fewer must not carry an article or a filler."""
  for text, line, _ in spans:
    words = text.split()
    if len(words) > cap or SENTENCE_END.search(text):
      continue
    hit = PADDING.search(text)
    if hit:
      yield line, text, hit.group(1), len(words)


def nmu200_string_contents(line, suffix, in_block_comment=False):
  contents = []
  index = 0
  javascript = suffix in {".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx"}
  while index < len(line):
    if in_block_comment:
      end_marker = "-->" if suffix in {".html", ".htm"} else "*/"
      end = line.find(end_marker, index)
      if end < 0:
        return contents, True
      index = end + len(end_marker)
      in_block_comment = False
      continue
    if suffix == ".py" and line[index] == "#":
      break
    if javascript and line.startswith("//", index):
      break
    if javascript and line.startswith("/*", index):
      in_block_comment = True
      index += 2
      continue
    if suffix in {".html", ".htm"} and line.startswith("<!--", index):
      in_block_comment = True
      index += 4
      continue
    quote = line[index]
    if quote not in {"'", '"', "`"}:
      index += 1
      continue
    delimiter = (
      quote * 3 if suffix == ".py" and line.startswith(quote * 3, index) else quote
    )
    start = index + len(delimiter)
    index = start
    while index < len(line):
      if line[index] == "\\":
        index += 2
      elif line.startswith(delimiter, index):
        contents.append(line[start:index])
        index += len(delimiter)
        break
      else:
        index += 1
    else:
      contents.append(line[start:])
  return contents, in_block_comment


def nmu200_findings(path, text):
  suffix = path.suffix.lower()
  if suffix not in NMU200_SUFFIXES:
    return
  decimal_labels = {"KB", "MB", "GB", "TB"}
  binary_labels = {"KiB", "MiB", "GiB", "TiB"}
  in_block_comment = False
  for line_number, line in enumerate(text.splitlines(), 1):
    literals, in_block_comment = nmu200_string_contents(line, suffix, in_block_comment)
    for literal in literals:
      for expression in NMU200_INTERPOLATION_RE.finditer(literal):
        tail = literal[expression.end() :]
        next_expression = tail.find("{")
        if next_expression >= 0:
          tail = tail[:next_expression]
        binary = bool(NMU200_BINARY_RE.search(expression.group(1)))
        decimal = bool(NMU200_DECIMAL_RE.search(expression.group(1)))
        for label_match in NMU200_LABEL_RE.finditer(tail):
          label = label_match.group(1)
          if binary and label in decimal_labels:
            yield line_number, label, "binary"
          elif decimal and label in binary_labels:
            yield line_number, label, "decimal"
