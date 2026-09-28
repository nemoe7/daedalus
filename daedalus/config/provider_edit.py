"""Provider file edits from the dashboard form that keep the comments and the key order."""

import re
from io import StringIO
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.tokens import CommentToken

# A key line with a one-line map as its value, such as `slug: {rpm: 5}`.
FLOW_LINE = re.compile(
  r"^(\s*[^#\n]*?: )\{([^{}\s][^{}\n]*?)\}([ \t]*)(#.*)?$", re.MULTILINE
)


def spaced(found: re.Match[str]) -> str:
  """The one-line map with a space inside each brace. A comment keeps its column."""
  key, inner, gap, comment = found.groups()
  if comment and len(gap) > 2:
    gap = gap[:-2]
  return f"{key}{{ {inner} }}{gap}{comment or ''}"


def round_trip() -> YAML:
  """A YAML reader and writer with the indents of the provider files."""
  found = YAML()
  found.indent(mapping=2, sequence=4, offset=2)
  found.preserve_quotes = True
  found.width = 4096
  return found


def same(old: Any, new: Any) -> bool:
  """Equal values of the same kind, so that 1 does not stand for true."""
  kinds = (bool, str)
  return old == new and all(isinstance(old, k) == isinstance(new, k) for k in kinds)


def merged(old: Any, new: Any, flow: bool = False) -> Any:
  """The old node with the new values. Unchanged parts keep their comments and quotes."""
  if isinstance(old, CommentedMap) and isinstance(new, dict):
    for key in [key for key in old if key not in new]:
      del old[key]
    for key, value in new.items():
      if key in old:
        old[key] = merged(old[key], value, key == "models")
      else:
        old[fresh(key)] = fresh(value, flow, key == "models")
    return old
  if isinstance(old, CommentedSeq) and isinstance(new, list):
    if len(old) == len(new) and all(map(same, old, new)):
      return old
    return relisted(old, new)
  return old if same(old, new) else fresh(new, flow)


def comment_parts(token: CommentToken | None) -> tuple[str, str]:
  """The end-of-line comment of a list item, and the lines that follow the item."""
  if token is None:
    return "", ""
  value = token.value
  if value.startswith("#"):
    end = value.index("\n") + 1 if "\n" in value else len(value)
    return value[:end], value[end:]
  return "", value[1:]


def relisted(old: CommentedSeq, new: list[Any]) -> CommentedSeq:
  """The list with the new items. Old items keep their quotes and end-of-line comments."""
  tokens = {index: item[0] for index, item in old.ca.items.items() if item and item[0]}
  last = len(old) - 1
  tail = comment_parts(tokens.get(last))[1]
  used: set[int] = set()
  kept, notes = [], {}
  for position, value in enumerate(new):
    index = next(
      (i for i, item in enumerate(old) if i not in used and same(item, value)), None
    )
    if index is None:
      kept.append(fresh(value))
      continue
    used.add(index)
    kept.append(old[index])
    token = tokens.get(index)
    if token is not None:
      notes[position] = (
        token,
        comment_parts(token)[0] if index == last else token.value,
      )
  old.clear()
  old.extend(kept)
  old.ca.items.clear()
  if kept and tail:
    token, text = notes.get(len(kept) - 1, (None, ""))
    notes[len(kept) - 1] = (token, (text or "\n") + tail)
  for position, (token, text) in notes.items():
    if not text:
      continue
    column = token.column if token is not None else 0
    old.ca.items[position] = [CommentToken(text, CommentMark(column)), None, None, None]
  return old


def fresh(value: Any, flow: bool = False, children: bool = False) -> Any:
  """A new node. A new pattern under `models` gets a one-line map, as in `free.yml`."""
  if isinstance(value, list):
    return [fresh(item) for item in value]
  if isinstance(value, str) and yaml.safe_dump(value).startswith(("'", '"')):
    return DoubleQuotedScalarString(value)
  if not isinstance(value, dict):
    return value
  node = CommentedMap()
  for key, inner in value.items():
    node[fresh(key)] = fresh(inner, children, key == "models")
  if flow:
    node.fa.set_flow_style()
  return node


def merge_text(text: str, document: dict[str, Any]) -> str:
  """The file text with the values of the form, and its comments kept."""
  writer = round_trip()
  data = writer.load(text) if text.strip() else None
  if not isinstance(data, CommentedMap):
    data = CommentedMap()
  merged(data, document)
  output = StringIO()
  writer.dump(data, output)
  # The files put a space inside the braces, and ruamel.yaml has no option for it.
  return FLOW_LINE.sub(spaced, output.getvalue())
