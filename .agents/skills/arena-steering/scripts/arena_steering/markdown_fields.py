"""Markdown report form parser and task-detail answer formatter for Arena Steering."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import ReportField, validate_entity_id

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
ANCHOR_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INLINE_ANCHOR_RE = re.compile(r"\{#([a-z0-9]+(?:-[a-z0-9]+)*)\}\s*$")

CHOICE_ITEM_RE = re.compile(r"^\s*[-*]\s+\(([ xX])\)\s+(.+?)\s*$")
CHECKBOX_ITEM_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")


def _split_comments(line: str) -> tuple[str, str | None]:
  """Strip inline comment spans and extract the first valid anchor slug."""
  anchor: str | None = None
  parts: list[str] = []
  pos = 0
  while True:
    start = line.find("<!--", pos)
    if start < 0:
      parts.append(line[pos:])
      break
    end = line.find("-->", start + 4)
    if end < 0:
      parts.append(line[pos:])
      break
    parts.append(line[pos:start])
    inner = line[start + 4 : end].strip()
    if anchor is None and ANCHOR_SLUG_RE.match(inner):
      anchor = inner
    pos = end + 3
  return "".join(parts), anchor


def strip_md_comments(line: str) -> str:
  """Remove inline HTML comments from one line."""
  cleaned, _ = _split_comments(line)
  return cleaned


def clean_label_text(raw: str) -> str:
  """Clean a Markdown prompt line into a plain field label."""
  without_comments = strip_md_comments(raw).strip()
  without_anchor = INLINE_ANCHOR_RE.sub("", without_comments).strip()
  without_heading = HEADING_PREFIX_RE.sub("", without_anchor).strip()
  if without_heading.startswith(("- ", "* ")):
    without_heading = without_heading[2:].strip()
  return without_heading


QUESTION_COMMENT_RE = re.compile(r"^\s*<!--\s*question\s+(\{.*\})\s*-->\s*$")
OPTION_COMMENT_RE = re.compile(
  r"^\s*[-*]\s+(\([ xX]\)|\[[ xX]\])\s+(.+?)\s+<!--\s*option\s+(\{.*\})\s*-->\s*$"
)


def parse_annotated_report(
  markdown: str,
) -> tuple[list[tuple[str, Any]], list[ReportField]]:
  lines = markdown.splitlines()
  blocks: list[tuple[str, Any]] = []
  fields: list[ReportField] = []
  chunk: list[str] = []
  used: set[str] = set()
  fence: str | None = None
  idx = 0
  while idx < len(lines):
    line = lines[idx]
    if fence:
      chunk.append(line)
      if line.strip().startswith(fence):
        fence = None
      idx += 1
      continue
    match_fence = FENCE_RE.match(line)
    if match_fence:
      fence = match_fence.group(1)
      chunk.append(line)
      idx += 1
      continue
    match = QUESTION_COMMENT_RE.fullmatch(line)
    if not match:
      if "<!-- question" in line or "<!-- option" in line:
        raise ValueError(f"Invalid question or option comment on line {idx + 1}")
      chunk.append(line)
      idx += 1
      continue
    if "--" in match.group(1):
      raise ValueError(f"Invalid HTML comment on line {idx + 1}")
    try:
      meta = json.loads(match.group(1))
    except json.JSONDecodeError as error:
      raise ValueError(f"Invalid question JSON on line {idx + 1}") from error
    if (
      not isinstance(meta, dict)
      or set(meta) != {"id", "label"}
      or not all(isinstance(meta[k], str) and meta[k].strip() for k in meta)
    ):
      raise ValueError(f"Question needs an id and label on line {idx + 1}")
    validate_entity_id(meta["id"], "ReportField.id")
    if meta["id"] in used:
      raise ValueError(f"Duplicate question id: {meta['id']}")
    used.add(meta["id"])
    idx += 1
    while idx < len(lines) and not lines[idx].strip():
      idx += 1
    if idx >= len(lines) or lines[idx].lstrip().startswith(("<!--", "- ", "* ")):
      raise ValueError("Question text is required")
    question = clean_label_text(lines[idx])
    idx += 1
    while idx < len(lines) and not lines[idx].strip():
      idx += 1
    options, recommended, labels = [], [], {}
    marker = None
    while idx < len(lines):
      option = OPTION_COMMENT_RE.fullmatch(lines[idx])
      if not option:
        if (
          CHOICE_ITEM_RE.match(lines[idx])
          or CHECKBOX_ITEM_RE.match(lines[idx])
          or "<!-- option" in lines[idx]
        ):
          raise ValueError(f"Invalid option comment on line {idx + 1}")
        break
      current = "choice" if option.group(1).startswith("(") else "checkbox"
      if marker is not None and marker != current:
        raise ValueError("Question has mixed option markers")
      marker = current
      if "--" in option.group(3):
        raise ValueError(f"Invalid HTML comment on line {idx + 1}")
      try:
        data = json.loads(option.group(3))
      except json.JSONDecodeError as error:
        raise ValueError(f"Invalid option JSON on line {idx + 1}") from error
      if (
        not isinstance(data, dict)
        or set(data) != {"label"}
        or not isinstance(data["label"], str)
        or not data["label"].strip()
      ):
        raise ValueError(f"Option needs a label on line {idx + 1}")
      visible = option.group(2).strip()
      options.append(visible)
      labels[visible] = data["label"].strip()
      if option.group(1)[1].lower() == "x":
        recommended.append(visible)
      idx += 1
    item = ReportField(
      id=meta["id"],
      label=meta["label"],
      question=question,
      type=marker or "text",
      options=options,
      recommended=recommended,
      option_labels=labels,
      annotated=True,
    )
    blocks.extend([("markdown", "\n".join(chunk)), ("field", item)])
    chunk = []
    fields.append(item)
  blocks.append(("markdown", "\n".join(chunk)))
  return blocks, fields


def parse_report_markdown(
  markdown: str,
) -> tuple[list[tuple[str, Any]], list[ReportField]]:
  """Parse annotated form fields while preserving other Markdown as prose."""
  return parse_annotated_report(markdown)


def format_answer_for_task_detail(field: ReportField | str, value: Any) -> str:
  """Format a field or its saved label and value as one task detail."""
  label = (field if isinstance(field, str) else field.label).strip()
  if isinstance(value, dict):
    selected = value.get("selected")
    custom = str(value.get("custom", "")).strip()
    parts: list[str] = []
    if isinstance(selected, list):
      parts.extend(str(item).strip() for item in selected if str(item).strip())
    elif selected:
      parts.append(str(selected).strip())
    if custom:
      parts.append(custom)
    answer_str = ", ".join(parts)
  elif isinstance(value, list):
    answer_str = ", ".join(str(item).strip() for item in value if str(item).strip())
  else:
    answer_str = str(value or "").strip()

  if not answer_str:
    answer_str = "(none)"
  return f"{label}: {answer_str}"
