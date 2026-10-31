"""Collect UI evidence, design proposals, records, and assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from annotate import (
  annotate_image,
  build_board,
  build_design_boards,
  find_nuu_config,
  load_nuu_config,
  resolve_project_typography,
)
from measure import (
  DEFAULT_SCALE,
  DEFAULT_SHEET,
  check_density,
  check_severities,
  parse_sheet,
  point,
  ruler,
)
from measure import check as check_measurements
from measure import main as measure_main
from walk import main as walk_main

NAMED_BREAKPOINTS = ("68em", "60em", "30em", "24em")
DEVICE_BREAKPOINTS = NAMED_BREAKPOINTS
MEDIA_BREAKPOINT_RE = re.compile(
  r"@media\b[^{]*?\(\s*(?:min|max)-(?:width|height)\s*:\s*([0-9.]+(?:em|rem|px))\s*\)",
  re.IGNORECASE,
)
HOME = Path(__file__).resolve().parent.parent
CATALOG = HOME / "data" / "vendor" / "ui-ux-pro-max"
WIDTHS = (1920, 1080, 958, 855, 411)
REPORT_WIDTHS = (1920, 411)
SKIP = {
  ".agents",
  ".git",
  ".hg",
  ".svn",
  ".venv",
  "venv",
  "node_modules",
  "dist",
  "build",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
}
RULE_CODE_RE = re.compile(
  r"\b(?:TYP|SPC|HRC|ACC|ICN|DOC|RUN|NUL|NUI|NUB|NUM|NUA)\d{3}\b"
)
LEGACY_RULE_ALIASES = {
  "NUI001": "ICN001",
  "NUI002": "ICN002",
  "NUI003": "ICN101",
  "NUL001": "ACC001",
  "NUL002": "ACC002",
  "NUL003": "ACC201",
  "NUL004": "DOC001",
  "NUL005": "ACC204",
  "NUL006": "HRC201",
  "NUL007": "TYP202",
  "NUL008": "HRC001",
  "NUB101": "SPC206",
  "NUB102": "RUN001",
  "NUB103": "RUN101",
  "NUB104": "RUN102",
  "NUM201": "SPC001",
  "NUM202": "TYP001",
  "NUM203": "SPC002",
  "NUM205": "SPC201",
  "NUM206": "SPC202",
  "NUM207": "SPC203",
  "NUM208": "HRC102",
  "NUM209": "SPC204",
  "NUM210": "SPC205",
  "NUM211": "TYP203",
  "NUM212": "TYP101",
  "NUM213": "TYP102",
  "NUM214": "TYP103",
  "NUM215": "TYP201",
  "NUM216": "HRC101",
  "NUM217": "SPC101",
  "NUM218": "SPC102",
  "NUA301": "ACC003",
  "NUA302": "ACC004",
  "NUA303": "ACC005",
  "NUA304": "ACC006",
  "NUA305": "ACC007",
  "NUA306": "ACC101",
  "NUA307": "ACC202",
  "NUA308": "ACC203",
  "NUA309": "ACC205",
  "NUA310": "HRC202",
}
REVERSE_RULE_ALIASES = {v: k for k, v in LEGACY_RULE_ALIASES.items()}


def expand_rule_aliases(code: str) -> set[str]:
  aliases = {code}
  if code in LEGACY_RULE_ALIASES:
    aliases.add(LEGACY_RULE_ALIASES[code])
    if code == "NUM218":
      aliases.update({"TYP002", "TYP003"})
  if code in REVERSE_RULE_ALIASES:
    aliases.add(REVERSE_RULE_ALIASES[code])
  if code in {"TYP002", "TYP003"}:
    aliases.add("NUM218")
  return aliases


def _load_version(home: Path = HOME) -> str:
  pyproject = home / "pyproject.toml"
  if pyproject.is_file():
    try:
      data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
      ver = data.get("project", {}).get("version")
      if isinstance(ver, str) and ver.strip():
        return ver.strip()
    except (OSError, tomllib.TOMLDecodeError):
      pass
  try:
    from importlib.metadata import version

    return version("nuu")
  except Exception:
    return "0.0.0"


def _clean_md(text: str) -> str:
  cleaned = text.replace("`", "").strip().rstrip(".")
  cleaned = re.sub(r"^(?:,\s*)?(?:or|and)\s+", "", cleaned, flags=re.IGNORECASE).strip()
  if cleaned and cleaned[0].islower():
    cleaned = cleaned[0].upper() + cleaned[1:]
  return cleaned


def load_rule_descriptions(home: Path = HOME) -> dict[str, str]:
  descriptions: dict[str, str] = {}
  nuu_md = home / "references" / "nuu.md"
  if nuu_md.is_file():
    for raw_line in nuu_md.read_text(encoding="utf-8").splitlines():
      line = raw_line.strip()
      if not (line.startswith("|") and line.endswith("|")):
        continue
      cells = [cell.strip() for cell in line.strip("|").split("|")]
      if len(cells) < 3:
        continue
      code_cell, condition_cell = cells[0].replace("`", ""), cells[2]
      codes_in_cell = RULE_CODE_RE.findall(code_cell)
      if not codes_in_cell:
        continue
      if len(codes_in_cell) == 1:
        descriptions[codes_in_cell[0]] = _clean_md(condition_cell)
        continue
      tagged = list(
        re.finditer(
          r"(.*?)\(\s*`?((?:TYP|SPC|HRC|ACC|ICN|DOC|RUN|NUL|NUI|NUB|NUM|NUA)\d{3})`?\s*\)",
          condition_cell,
        )
      )
      if tagged:
        for match in tagged:
          phrase = match.group(1).strip().lstrip(",").strip()
          descriptions[match.group(2)] = _clean_md(phrase)
        continue
      range_match = re.match(
        r"^([A-Z]{3})(\d{3})\s*[–-]\s*[A-Z]{3}(\d{3})$", code_cell.strip()
      )
      if range_match:
        prefix, start_n, end_n = (
          range_match.group(1),
          int(range_match.group(2)),
          int(range_match.group(3)),
        )
        expanded = [f"{prefix}{n:03d}" for n in range(start_n, end_n + 1)]
        parts = [
          _clean_md(p)
          for p in re.split(r",\s+or\s+|,\s+|\s+or\s+", condition_cell)
          if p.strip()
        ]
        if len(parts) == len(expanded):
          for code, part in zip(expanded, parts):
            descriptions[code] = part
  skill_md = home / "SKILL.md"
  if skill_md.is_file():
    clauses: dict[int, str] = {}
    for raw_line in skill_md.read_text(encoding="utf-8").splitlines():
      clause_match = re.match(r"^(\d+)\.\s+(.+)$", raw_line.strip())
      if not clause_match:
        continue
      num = int(clause_match.group(1))
      body = clause_match.group(2).strip()
      clauses.setdefault(num, body)
      first_sentence = re.sub(r"\s+via\s+\[.*$", "", body.split(". ")[0])
      clean_sentence = _clean_md(re.sub(r"\s*\([^)]*\)", "", first_sentence))
      for code in RULE_CODE_RE.findall(body):
        descriptions.setdefault(code, clean_sentence)
    if 1 in clauses:
      sentences = [s.strip() for s in clauses[1].split(". ") if s.strip()]
      if len(sentences) >= 2:
        descriptions.setdefault("ICN001", _clean_md(sentences[1]))
        descriptions.setdefault("ICN002", _clean_md(sentences[0]))
    if 2 in clauses:
      descriptions.setdefault("ICN101", _clean_md(clauses[2]))
  descriptions.setdefault(
    "HRC201", "Raw ISO or unadjusted UTC/offset timestamp in UI text or source"
  )
  descriptions.setdefault(
    "HRC202", "Visible raw ISO or unadjusted UTC/offset timestamp in rendered UI text"
  )
  return descriptions


VERSION = _load_version()
RULE_DESCRIPTIONS = load_rule_descriptions()
for _leg, _sem in LEGACY_RULE_ALIASES.items():
  if _sem in RULE_DESCRIPTIONS:
    RULE_DESCRIPTIONS.setdefault(_leg, RULE_DESCRIPTIONS[_sem])
KNOWN_NUU_RULES = {
  *LEGACY_RULE_ALIASES.keys(),
  *LEGACY_RULE_ALIASES.values(),
  "TYP002",
  "TYP003",
  *RULE_DESCRIPTIONS,
}
RAW_UI_TIMESTAMP_RE = re.compile(
  r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
  r"|\s+\d{2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|[+-]\d{2}:?\d{2}))?)\b"
  r"|\b\d{2}:\d{2}(?::\d{2})?\s*(?:UTC|GMT)\b"
)
UNADJUSTED_JS_TIMESTAMP_RE = re.compile(
  r"\.(?:textContent|innerText)\s*=\s*(?:(?![^\n;]*\b(?:clockTime|relativeTime|time|format\w*)\s*\()[^;\n]*\.(?:toISOString|toUTCString)\s*\("
  r"|[A-Za-z0-9_$.]+\.(?:at|updated_at|created_at)\s*(?:\|\|\s*['\"][^\n'\"]*['\"])?\s*;)"
  r"|\btimeZone\s*:\s*['\"](?:UTC|GMT|Etc/UTC)['\"]"
)
NONUU_MARKER_RE = re.compile(r"\bnonuu:[ \t]*(.*?)(?=-->|\*/|;|\s|$|\bnonuu:)")
DEVIATION_MARKER_RE = re.compile(
  r"\bnuu-deviation:[ \t]*(?:([A-Za-z0-9_.-]+):[ \t]*)?(.*?)(?=-->|\*/|$)"
)


def enrich_finding_rule(finding: dict) -> dict:
  code = finding.get("rule") or finding.get("id") or ""
  if code and "rule_description" not in finding:
    finding["rule_description"] = RULE_DESCRIPTIONS.get(str(code), f"House rule {code}")
  return finding


def collect_deviation_markers(path, lines):
  deviations = []
  for number, line in enumerate(lines, 1):
    for match in DEVIATION_MARKER_RE.finditer(line):
      dev_id = (match[1] or "").strip()
      reason = (match[2] or "").strip()
      if not dev_id and reason and re.fullmatch(r"[A-Za-z0-9_.-]+", reason):
        dev_id, reason = reason, ""
      if not dev_id and not reason:
        continue
      label = (
        f"{dev_id}: {reason or '<no-reason>'}" if dev_id else (reason or "<no-reason>")
      )
      mark_str = f"{path}:{number}: {label}"
      deviations.append(
        {
          "id": dev_id,
          "reason": reason,
          "path": str(path),
          "line": number,
          "source": line.strip(),
          "mark": mark_str,
        }
      )
  return deviations


def collect_nonuu_markers(path, lines):
  file_ids, line_ids, marks, suppressions = set(), [], [], []
  for number, line in enumerate(lines, 1):
    suppressed = set()
    for marker in NONUU_MARKER_RE.finditer(line):
      code = marker[1]
      scope = "unknown"
      if code in KNOWN_NUU_RULES:
        aliases = expand_rule_aliases(code)
        suppressed.update(aliases)
        before = line[: marker.start()].strip()
        scope = (
          "file"
          if number <= 3 and not re.sub(r"^(?:#|/\*|<!--|//)\s*", "", before).strip()
          else "line"
        )
        if scope == "file":
          file_ids.update(aliases)
      mark_str = f"{path}:{number}: {code or '<missing>'} {scope}: {line.strip()}"
      marks.append(mark_str)
      suppressions.append(
        {
          "path": str(path),
          "line": number,
          "rule": code or "<missing>",
          "scope": scope,
          "source": line.strip(),
          "mark": mark_str,
        }
      )
    line_ids.append(suppressed)
  return file_ids, line_ids, marks, suppressions


def load(path):
  target = Path(path)
  if target.suffix.lower() == ".toml":
    return tomllib.loads(target.read_text(encoding="utf-8"))
  return json.loads(target.read_text(encoding="utf-8"))


def contained(root, name):
  path = (root / name).resolve()
  if not path.is_relative_to(root.resolve()):
    raise ValueError("path leaves the selected project")
  return path


def files(root, suffixes):
  for path in sorted(root.rglob("*")):
    relative = path.relative_to(root)
    if any(part in SKIP for part in relative.parts) or path.is_symlink():
      continue
    if path.is_file() and path.suffix.lower() in suffixes:
      yield path


def terms(text):
  return re.findall(r"\w+", text.casefold())


def search(query, dataset=None, stack=None, limit=10):
  """Rank catalog records with explicit term matches and fallback coverage."""
  rows = []
  for path in sorted(CATALOG.rglob("*.csv")):
    name = path.relative_to(CATALOG).with_suffix("").as_posix()
    if dataset and name != dataset:
      continue
    if stack and name.startswith("stacks/") and name != f"stacks/{stack}":
      continue
    with path.open(encoding="utf-8-sig", newline="") as stream:
      for line, row in enumerate(csv.DictReader(stream), 2):
        values = " ".join(str(value) for value in row.values() if value is not None)
        rows.append((name, line, row, Counter(terms(values))))
  requested = set(terms(query))
  frequencies = Counter(word for _, _, _, words in rows for word in words)
  average = sum(sum(words.values()) for _, _, _, words in rows) / max(len(rows), 1)
  ranked = []
  for name, line, row, words in rows:
    score = 0.0
    for word in requested & words.keys():
      frequency = words[word]
      inverse = math.log(
        1 + (len(rows) - frequencies[word] + 0.5) / (frequencies[word] + 0.5)
      )
      denominator = frequency + 1.2 * (
        0.25 + 0.75 * sum(words.values()) / max(average, 1)
      )
      score += inverse * frequency * 2.2 / denominator
    ranked.append(
      {
        "dataset": name,
        "line": line,
        "score": score,
        "matched_terms": sorted(requested & words.keys()),
        "record": row,
      }
    )
  ranked.sort(key=lambda row: (-row["score"], row["dataset"], row["line"]))
  matched = [row for row in ranked if row["score"] > 0]
  selected = (matched or ranked)[:limit]
  return {
    "query": query,
    "fallback": not bool(matched),
    "results": selected,
    "source": load(CATALOG / "source.json"),
    "coverage": {"records": len(rows)},
  }


def extract(root):
  tokens, components, breakpoints = [], [], []
  for path in files(root, {".css", ".html"}):
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+)", text):
      tokens.append(
        {
          "name": match[1],
          "value": match[2].strip(),
          "path": str(path),
          "line": text.count("\n", 0, match.start()) + 1,
        }
      )
    if path.suffix.lower() == ".css":
      for match in MEDIA_BREAKPOINT_RE.finditer(text):
        breakpoints.append(
          {
            "value": match[1],
            "path": str(path),
            "line": text.count("\n", 0, match.start()) + 1,
          }
        )
    if path.suffix.lower() == ".html":
      for match in re.finditer(r'<([\w-]+)\b[^>]*\bclass=["\']([^"\']+)', text):
        components.append(
          {
            "tag": match[1],
            "classes": match[2].split(),
            "path": str(path),
            "line": text.count("\n", 0, match.start()) + 1,
          }
        )
  return {
    "tokens": tokens,
    "breakpoints": breakpoints,
    "components": components,
    "coverage": {
      "formats": [
        "CSS custom properties",
        "CSS media breakpoints",
        "HTML class patterns",
      ],
      "dynamic_styles": "not_checked",
    },
    "status": "proposal",
  }


class SourcePage(HTMLParser):
  RAW_TEXT_TAGS = ("script", "style", "code", "pre", "textarea", "template")
  VAGUE_BUTTON_LABELS = frozenset({"yes", "no", "okay", "ok"})
  PRIMARY_BUTTON_CLASSES = frozenset(
    {
      "primary",
      "button--accent",
      "btn-primary",
      "button-primary",
    }
  )
  VOID_TAGS = frozenset(
    {
      "area",
      "base",
      "br",
      "col",
      "embed",
      "hr",
      "img",
      "input",
      "link",
      "meta",
      "param",
      "source",
      "track",
      "wbr",
    }
  )

  def __init__(self, path):
    super().__init__(convert_charrefs=True)
    self.path, self.findings = path, []
    self.labels, self.ids, self.controls = set(), set(), []
    self.label_depth = 0
    self.raw_text_depth = 0
    self.tag_depth = 0
    self.card_scopes = []
    self.button_stack = []

  def finding(self, rule, criterion, evidence, position):
    line, column = position
    self.findings.append(
      {
        "rule": rule,
        "path": str(self.path),
        "line": line,
        "column": column + 1,
        "severity": "review",
        "evidence": evidence,
        "criterion": criterion,
        "status": "source_candidate",
      }
    )

  @classmethod
  def _is_primary_button(cls, attrs: dict) -> bool:
    classes = set((attrs.get("class") or "").split())
    if classes & cls.PRIMARY_BUTTON_CLASSES:
      return True
    return (attrs.get("data-variant") or "").strip().lower() == "primary"

  def _check_button_action(self, attrs: dict, label_text: str, pos: tuple[int, int]):
    cleaned = re.sub(r"[.!?\s]+$", "", label_text.strip())
    if cleaned and cleaned.casefold() in self.VAGUE_BUTTON_LABELS:
      self.finding(
        "HRC001",
        "action-hierarchy",
        f"Button uses vague label '{label_text.strip()}' instead of an explicit action",
        pos,
      )
    if self.card_scopes and self._is_primary_button(attrs):
      self.card_scopes[-1][2].append(pos)

  def handle_starttag(self, tag, attributes):
    attrs = dict(attributes)
    pos = self.getpos()
    classes = set((attrs.get("class") or "").split())
    role = (attrs.get("role") or "").strip().lower()
    if tag not in self.VOID_TAGS:
      self.tag_depth += 1
      if tag == "dialog" or role in {"dialog", "alertdialog"} or "card" in classes:
        self.card_scopes.append((tag, self.tag_depth, []))
    if tag in self.RAW_TEXT_TAGS:
      self.raw_text_depth += 1
    if attrs.get("id"):
      self.ids.add(attrs["id"])
    if tag == "label":
      self.label_depth += 1
      if attrs.get("for"):
        self.labels.add(attrs["for"])
    if tag == "img" and "alt" not in attrs:
      self.finding("ACC001", "1.1.1", "Image has no alt attribute", pos)
    if tag == "button":
      self.button_stack.append((attrs, pos, []))
    if tag in {"input", "select", "textarea"}:
      self.controls.append((attrs, bool(self.label_depth), pos))
      if tag == "input" and (attrs.get("type") or "").lower() in {
        "button",
        "submit",
        "reset",
      }:
        self._check_button_action(
          attrs, attrs.get("value") or attrs.get("aria-label") or "", pos
        )

  def handle_endtag(self, tag):
    if tag == "button" and self.button_stack:
      attrs, pos, chunks = self.button_stack.pop()
      label = "".join(chunks).strip() or (attrs.get("aria-label") or "").strip()
      self._check_button_action(attrs, label, pos)
    if (
      self.card_scopes
      and self.card_scopes[-1][0] == tag
      and self.card_scopes[-1][1] == self.tag_depth
    ):
      _, _, primaries = self.card_scopes.pop()
      if len(primaries) > 1:
        self.finding(
          "HRC001",
          "action-hierarchy",
          f"Card or dialog contains {len(primaries)} primary buttons (at most 1 allowed)",
          primaries[1],
        )
    if tag not in self.VOID_TAGS:
      self.tag_depth = max(0, self.tag_depth - 1)
    if tag in self.RAW_TEXT_TAGS:
      self.raw_text_depth = max(0, self.raw_text_depth - 1)
    if tag == "label":
      self.label_depth = max(0, self.label_depth - 1)

  def handle_data(self, data):
    if self.button_stack and data:
      self.button_stack[-1][2].append(data)
    if self.raw_text_depth > 0 or not data:
      return
    match = RAW_UI_TIMESTAMP_RE.search(data)
    if match:
      self.finding(
        "HRC201",
        "relative-timestamps",
        f"Raw or unadjusted timestamp in UI text: {match[0]}",
        self.getpos(),
      )

  def finish(self):
    while self.card_scopes:
      _, _, primaries = self.card_scopes.pop()
      if len(primaries) > 1:
        self.finding(
          "HRC001",
          "action-hierarchy",
          f"Card or dialog contains {len(primaries)} primary buttons (at most 1 allowed)",
          primaries[1],
        )
    for attrs, nested, position in self.controls:
      direct = any((attrs.get(name) or "").strip() for name in ("aria-label", "title"))
      references = set((attrs.get("aria-labelledby") or "").split()) & self.ids
      native = (attrs.get("type") or "").lower() in {"hidden", "submit", "reset"}
      value = attrs.get("type") == "button" and attrs.get("value")
      image = attrs.get("type") == "image" and attrs.get("alt")
      if not (
        direct
        or references
        or native
        or value
        or image
        or nested
        or attrs.get("id") in self.labels
      ):
        self.finding(
          "ACC002", "4.1.2", "Control has no source naming evidence", position
        )


def _has_custom_focus_ring(clean_css: str) -> bool:
  for rule_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean_css):
    selector, body = rule_match[1], rule_match[2]
    if not re.search(r":focus(?:-visible|-within)?\b", selector) or ":not(" in selector:
      continue
    for decl in body.split(";"):
      if ":" not in decl:
        continue
      prop, val = (part.strip().lower() for part in decl.split(":", 1))
      val_clean = re.sub(r"\s*!important\s*$", "", val).strip()
      if prop in {"outline", "box-shadow"} and val_clean not in {
        "",
        "none",
        "0",
        "0px",
      }:
        return True
      if prop in {"border", "border-color"} and val_clean not in {
        "",
        "none",
        "0",
        "0px",
        "transparent",
      }:
        return True
  return False


def _resolve_design_doc_rel(root: Path) -> str:
  try:
    cfg = load_nuu_config(root).get("config") or {}
  except Exception:
    cfg = {}
  if isinstance(cfg, dict):
    design_tbl = cfg.get("design") if isinstance(cfg.get("design"), dict) else {}
    audit_tbl = cfg.get("audit") if isinstance(cfg.get("audit"), dict) else {}
    for candidate in (
      design_tbl.get("path"),
      design_tbl.get("doc"),
      design_tbl.get("file"),
      audit_tbl.get("design_doc"),
      audit_tbl.get("design_path"),
      cfg.get("design_doc"),
      cfg.get("design_path"),
    ):
      if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
  return "docs/design.md"


def _has_design_doc(root: Path, design_doc_rel: str | None = None) -> bool:
  rel = (design_doc_rel or _resolve_design_doc_rel(root)).strip() or "docs/design.md"
  rel_path = Path(rel)
  if rel_path.is_absolute():
    return rel_path.is_file()
  cur = Path(root).resolve()
  if cur.is_file():
    cur = cur.parent
  while True:
    if (cur / rel).is_file():
      return True
    if rel == "docs/design.md":
      docs_dir = cur / "docs"
      if docs_dir.is_dir() and any(
        p.is_file() and not p.is_symlink() for p in docs_dir.rglob("design.md")
      ):
        return True
    if (
      (cur / ".git").exists()
      or cur.parent == cur
      or cur == Path("/tmp")
      or cur.parent == Path("/tmp")
    ):
      break
    cur = cur.parent
  return False


def lint(
  root,
  *,
  manifest=None,
  ruler_start=None,
  ruler_end=None,
  scale=DEFAULT_SCALE,
):
  findings, coverage, marks, suppressions, deviations = [], [], [], [], []
  nonuu_by_path = {}
  list_rules = []
  list_reset = {"indent": False, "padding": False, "position": False}
  for path in files(root, {".html", ".css", ".js", ".jsx", ".ts", ".tsx"}):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    file_ids, line_ids, file_marks, file_supps = collect_nonuu_markers(path, lines)
    nonuu_by_path[path] = (file_ids, line_ids)
    marks.extend(file_marks)
    suppressions.extend(file_supps)
    deviations.extend(collect_deviation_markers(path, lines))
    supported = path.suffix.lower() in {".html", ".css"}
    coverage.append(
      {
        "path": str(path),
        "status": "checked" if supported else "not_checked",
        "scope": "selected source patterns" if supported else "rendered check required",
      }
    )
    current = []
    if path.suffix.lower() == ".html":
      parser = SourcePage(path)
      parser.feed(text)
      parser.close()
      parser.finish()
      current.extend(parser.findings)
    if path.suffix.lower() == ".css":
      clean = re.sub(
        r"/\*.*?\*/",
        lambda match: "".join("\n" if char == "\n" else " " for char in match[0]),
        text,
        flags=re.DOTALL,
      )
      list_selector = re.compile(
        r"(?:^|[\s>+~,:()])(?:ul|ol|li)(?=$|[\s.#:\[>+~),])", re.IGNORECASE
      )
      list_container = re.compile(
        r"(?:^|[\s>+~,:()])(?:ul|ol)(?=$|[\s.#:\[>+~),])", re.IGNORECASE
      )
      for list_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean):
        selector_text = list_match[1]
        if not list_selector.search(selector_text):
          continue
        declarations = {}
        for declaration in list_match[2].split(";"):
          if ":" not in declaration:
            continue
          name, value = declaration.split(":", 1)
          declarations[name.strip().lower()] = re.sub(
            r"\s*!important\s*$", "", value.strip(), flags=re.IGNORECASE
          ).lower()
        list_properties = {
          "list-style",
          "list-style-position",
          "list-style-type",
          "padding",
          "padding-inline-start",
          "padding-left",
          "padding-right",
          "--indent",
        }
        if not list_properties.intersection(declarations):
          continue
        rule_line = text.count("\n", 0, list_match.start(1)) + 1
        list_rules.append((path, rule_line))
        if list_container.search(selector_text):
          list_reset["indent"] |= declarations.get("--indent") == "2ch"
          list_reset["padding"] |= declarations.get("padding-inline-start") == "2ch"
          list_reset["position"] |= declarations.get("list-style-position") == "outside"
        inside_match = re.search(
          r"(?:list-style-position\s*:\s*inside\b|list-style\s*:[^;}]*\binside\b)",
          list_match[2],
          re.IGNORECASE,
        )
        if inside_match:
          line = text.count("\n", 0, list_match.start(2) + inside_match.start()) + 1
          current.append(
            {
              "rule": "TYP202",
              "path": str(path),
              "line": line,
              "column": 1,
              "severity": "review",
              "status": "source_candidate",
              "evidence": "list-style-position: inside",
              "criterion": "clause-15",
            }
          )
        for indent_match in re.finditer(
          r"(?<![\w-])(--indent|padding-inline-start)\s*:\s*([^;}]+)",
          list_match[2],
          re.IGNORECASE,
        ):
          value = re.sub(
            r"\s*!important\s*$", "", indent_match[2].strip(), flags=re.IGNORECASE
          ).lower()
          if value == "2ch":
            continue
          line = text.count("\n", 0, list_match.start(2) + indent_match.start(1)) + 1
          current.append(
            {
              "rule": "TYP202",
              "path": str(path),
              "line": line,
              "column": 1,
              "severity": "review",
              "status": "source_candidate",
              "evidence": f"{indent_match[1]}: {indent_match[2].strip()}",
              "criterion": "clause-15",
            }
          )
      if not _has_custom_focus_ring(clean):
        for match in re.finditer(
          r"outline\s*:\s*(?:none|0(?:px)?)\s*(?:!important\s*)?[;}]", clean
        ):
          current.append(
            {
              "rule": "ACC201",
              "path": str(path),
              "line": text.count("\n", 0, match.start()) + 1,
              "column": 1,
              "severity": "review",
              "status": "source_candidate",
              "evidence": match[0],
              "criterion": "2.4.7",
            }
          )
      for rule_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean):
        selector_text = rule_match[1]
        if not re.search(
          r"(?::hover|:focus(?:-visible|-within)?|:active|:disabled|\[disabled\b|\[aria-busy\b)",
          selector_text,
        ):
          continue
        body_start = rule_match.start(2)
        for prop_match in re.finditer(
          r"(?:^|[;\s])((?:(?:min|max)-)?(?:width|height)|padding(?:-(?:top|right|bottom|left|inline|block))?|border(?:-(?:top|right|bottom|left))?-width|font-size|line-height)\s*:\s*([^;}]+)",
          rule_match[2],
        ):
          prop_pos = body_start + prop_match.start(1)
          current.append(
            {
              "rule": "ACC204",
              "path": str(path),
              "line": text.count("\n", 0, prop_pos) + 1,
              "column": 1,
              "severity": "review",
              "status": "source_candidate",
              "evidence": f"{prop_match[1]}: {prop_match[2].strip()}",
              "criterion": "clause-9",
            }
          )
    if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
      clean_js = re.sub(
        r"/\*.*?\*/",
        lambda match: "".join("\n" if char == "\n" else " " for char in match[0]),
        text,
        flags=re.DOTALL,
      )
      for number, raw_line in enumerate(clean_js.splitlines(), 1):
        code_part = raw_line.split("//", 1)[0]
        ts_match = UNADJUSTED_JS_TIMESTAMP_RE.search(code_part)
        if ts_match:
          current.append(
            {
              "rule": "HRC201",
              "path": str(path),
              "line": number,
              "column": ts_match.start() + 1,
              "severity": "review",
              "status": "source_candidate",
              "evidence": f"Unadjusted or raw timestamp assignment: {ts_match[0].strip()}",
              "criterion": "relative-timestamps",
            }
          )
    for finding in current:
      ln = finding["line"]
      ignored = file_ids | (line_ids[ln - 1] if 0 < ln <= len(line_ids) else set())
      if finding["rule"] not in ignored:
        findings.append(finding)
  if list_rules and not all(list_reset.values()):
    reported_paths = {
      finding["path"] for finding in findings if finding.get("rule") == "TYP202"
    }
    for list_path, list_line in list_rules:
      if str(list_path) in reported_paths:
        continue
      file_ids, line_ids = nonuu_by_path[list_path]
      ignored = file_ids | (
        line_ids[list_line - 1] if 0 < list_line <= len(line_ids) else set()
      )
      if "TYP202" in ignored:
        continue
      findings.append(
        {
          "rule": "TYP202",
          "path": str(list_path),
          "line": list_line,
          "column": 1,
          "severity": "review",
          "status": "source_candidate",
          "evidence": (
            "List CSS lacks --indent: 2ch, padding-inline-start: 2ch, "
            "or list-style-position: outside"
          ),
          "criterion": "clause-15",
        }
      )
      break
  design_doc_rel = _resolve_design_doc_rel(root)
  design_doc_path = (
    Path(design_doc_rel)
    if Path(design_doc_rel).is_absolute()
    else (root / design_doc_rel)
  )
  if not _has_design_doc(root, design_doc_rel):
    findings.append(
      {
        "rule": "DOC001",
        "path": str(design_doc_path),
        "line": 1,
        "column": 1,
        "severity": "review",
        "status": "source_candidate",
        "evidence": f"Missing {design_doc_rel}",
        "criterion": "clause-11",
      }
    )
  icons_res = check_icons(root)
  for icon_finding in icons_res.get("findings", []):
    if isinstance(icon_finding, dict):
      enrich_finding_rule(icon_finding)
  design_res = extract(root)
  extra: dict[str, object] = {}
  if manifest is not None:
    asset_res = assets(root, manifest)
    extra["assets"] = asset_res
    findings.extend(asset_res.get("findings", []))
  if ruler_start is not None and ruler_end is not None:
    gap = ruler(ruler_start, ruler_end, scale)
    extra["ruler"] = gap
    if not gap["on_scale"]:
      findings.append(
        {
          "rule": "SPC001",
          "severity": "review",
          "evidence": f"gap {gap['dx']:g} x {gap['dy']:g} px is off the {scale:g} px scale",
        }
      )
  for finding in findings:
    if isinstance(finding, dict):
      enrich_finding_rule(finding)
  return {
    "findings": findings,
    "icons": icons_res,
    "design": design_res,
    "tokens": design_res["tokens"],
    "breakpoints": design_res["breakpoints"],
    "components": design_res["components"],
    "marks": marks,
    "suppressions": suppressions,
    "deviations": deviations,
    "coverage": coverage,
    "limits": [
      "Source patterns do not prove accessible names or WCAG conformance.",
      "Visual quality and task success need rendered and interaction evidence.",
    ],
    **extra,
  }


def check_icons(root):
  counts = Counter()
  findings = []
  license_files = [
    path.relative_to(root).as_posix()
    for path in sorted(root.rglob("*"))
    if path.is_file()
    and not path.is_symlink()
    and not any(part in SKIP for part in path.relative_to(root).parts)
    and re.search(r"(?:licen[cs]e|copying)", path.name, re.IGNORECASE)
  ]
  for path in files(root, {".html", ".js", ".jsx", ".ts", ".tsx"}):
    text = path.read_text(encoding="utf-8")
    current = []
    sizes = {}
    for svg_match in re.finditer(
      r"<svg\b([^>]*)>([\s\S]*?)</svg>", text, re.IGNORECASE
    ):
      attrs = svg_match[1]
      body = svg_match[2]
      line_no = text.count("\n", 0, svg_match.start()) + 1
      class_match = re.search(
        r'\bclass\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE
      )
      classes = class_match[1].lower() if class_match else ""
      viewbox_match = re.search(
        r'\bviewBox\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE
      )
      viewbox = viewbox_match[1].strip() if viewbox_match else ""
      if "lucide" in classes or (
        viewbox == "0 0 24 24" and "stroke=" in attrs and "stroke-linecap" in attrs
      ):
        icon_source = "lucide"
      elif (
        "simple-icon" in classes
        or "markdown-icon" in classes
        or (viewbox == "0 0 24 24" and "<path" in body and "stroke=" not in attrs)
      ):
        icon_source = "simple-icons"
      elif "phosphor" in classes or viewbox == "0 0 256 256":
        icon_source = "phosphor"
      else:
        icon_source = "custom"
        current.append(
          {
            "rule": "ICN002",
            "path": str(path),
            "line": line_no,
            "column": 1,
            "severity": "advisory",
            "blocking": False,
            "evidence": "Inline <svg> outside the Lucide -> Simple Icons -> Phosphor ladder",
            "criterion": "clause-1",
          }
        )
      counts[icon_source] += 1
      w_match = re.search(r'\bwidth\s*=\s*["\']([0-9.]+)["\']', attrs, re.IGNORECASE)
      h_match = re.search(r'\bheight\s*=\s*["\']([0-9.]+)["\']', attrs, re.IGNORECASE)
      if w_match and h_match:
        sizes.setdefault(f"{w_match[1]}x{h_match[1]}", line_no)
    if len(sizes) > 1:
      first_line = min(sizes.values())
      current.append(
        {
          "rule": "ICN101",
          "path": str(path),
          "line": first_line,
          "column": 1,
          "severity": "advisory",
          "blocking": False,
          "evidence": f"Multiple inline icon sizes in one source file: {', '.join(sorted(sizes))}; review each surface",
          "criterion": "clause-2",
        }
      )
    lines = text.splitlines()
    for finding in current:
      line = lines[finding["line"] - 1] if 0 < finding["line"] <= len(lines) else ""
      ignored = set(re.findall(r"\bnonuu:\s*([A-Za-z0-9_-]+)", line))
      if finding["rule"] not in ignored:
        findings.append(finding)
  for path in files(root, {".css"}):
    text = path.read_text(encoding="utf-8")
    clean = re.sub(
      r"/\*[\s\S]*?\*/",
      lambda m: "\n" * m.group(0).count("\n"),
      text,
    )
    lines = text.splitlines()
    for rule_match in re.finditer(r"([^{};]+)\{([^}]*)\}", clean):
      selector = rule_match[1].strip()
      body = rule_match[2]
      is_control = bool(
        re.search(
          r"(?:^|[\s,>+~:])(?:select|button|input|summary)\b|\[role=",
          selector,
          re.IGNORECASE,
        )
      )
      prop_match = re.search(
        r"\b(?:background(?:-image)?\s*:[^;]*(?:(?:linear|radial|conic)-gradient|url)\s*\(|(?:-webkit-)?mask(?:-image)?\s*:[^;]*url\s*\()",
        body,
        re.IGNORECASE,
      )
      if not prop_match:
        continue
      if not is_control and not re.search(
        r"(?:-webkit-)?mask(?:-image)?\s*:[^;]*url\s*\(",
        prop_match.group(0),
        re.IGNORECASE,
      ):
        continue
      line_no = clean.count("\n", 0, rule_match.start(2) + prop_match.start()) + 1
      rule_line = clean.count("\n", 0, rule_match.start(1)) + 1
      ignored = set()
      for check_ln in range(max(1, rule_line), min(len(lines), line_no) + 1):
        ignored.update(re.findall(r"\bnonuu:\s*([A-Za-z0-9_-]+)", lines[check_ln - 1]))
      counts["custom"] += 1
      if "ICN002" not in ignored and "NUI002" not in ignored:
        findings.append(
          {
            "rule": "ICN002",
            "path": str(path),
            "line": line_no,
            "column": 1,
            "severity": "advisory",
            "blocking": False,
            "evidence": (
              "CSS background-image/mask-image control icon outside the "
              "Lucide -> Simple Icons -> Phosphor ladder"
            ),
            "criterion": "clause-1",
          }
        )
  if sum(counts.values()) > 0 and not license_files:
    findings.append(
      {
        "rule": "ICN001",
        "path": str(root),
        "line": 1,
        "column": 1,
        "severity": "advisory",
        "blocking": False,
        "evidence": "Inline icons present without a companion license file in project",
        "criterion": "clause-1",
      }
    )
  return {
    "status": "checked",
    "blocking": False,
    "ladder": ["lucide", "simple-icons", "phosphor"],
    "counts": dict(counts),
    "licenses": license_files,
    "findings": findings,
  }


def assets(root, manifest):
  rows = load(manifest)
  if not isinstance(rows, list):
    raise TypeError("asset manifest must be an array")
  findings = []
  for index, row in enumerate(rows):
    if not isinstance(row, dict):
      raise TypeError("asset record must be an object")
    missing = [
      name
      for name in ("source", "license", "attribution", "path")
      if not isinstance(row.get(name), str) or not row[name].strip()
    ]
    if missing:
      findings.append({"asset": index, "reason": "missing_metadata", "fields": missing})
      continue
    path = contained(root, row["path"])
    if not path.is_file():
      findings.append({"asset": index, "reason": "missing_file", "path": str(path)})
      continue
    if path.suffix.lower() == ".svg":
      text = path.read_text(encoding="utf-8")
      if re.search(
        r"<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|\bon\w+\s*=",
        text,
        re.IGNORECASE,
      ):
        findings.append({"asset": index, "reason": "active_svg", "path": str(path)})
  return {
    "assets": rows,
    "findings": findings,
    "coverage": "local files and supplied metadata",
  }


def fetch_asset(root, url, destination, authorized):
  if not authorized:
    raise ValueError("asset fetch requires --authorize-fetch")
  validated_url(url)
  path = contained(root, destination)
  if path.exists():
    raise ValueError("asset destination already exists")
  with urllib.request.build_opener(AssetRedirect()).open(url, timeout=20) as response:
    content = response.read(10 * 1024 * 1024 + 1)
  if len(content) > 10 * 1024 * 1024:
    raise ValueError("asset exceeds the 10 MiB fetch limit")
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("xb") as stream:
    stream.write(content)
  return {
    "path": str(path),
    "source": url,
    "sha256": hashlib.sha256(content).hexdigest(),
    "license": "unknown",
    "attribution": "unknown",
    "status": "metadata_required",
  }


def validated_url(url):
  parsed = urllib.parse.urlsplit(url)
  if (
    parsed.scheme not in {"http", "https"}
    or not parsed.hostname
    or parsed.username
    or parsed.password
  ):
    raise ValueError("asset URL must be HTTP(S) without credentials")
  return url


class AssetRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, request, fp, code, message, headers, newurl):
    validated_url(newurl)
    return super().redirect_request(request, fp, code, message, headers, newurl)


def browser(args):
  if args.timeout <= 0 or any(width <= 0 for width in args.widths):
    raise ValueError("timeout and widths must be positive")
  validated_url(args.url)
  if args.edit and (args.command != "live" or not args.authorize_edit):
    raise ValueError("live edits require live mode and --authorize-edit")
  if not shutil.which("node"):
    return {"status": "not_checked", "reason": "node_unavailable", "coverage": []}
  project_root = getattr(args, "project", None) or Path.cwd()
  cfg_info = load_nuu_config(project_root)
  cfg_audit = (
    cfg_info["config"].get("audit")
    if isinstance(cfg_info["config"].get("audit"), dict)
    else {}
  )
  cfg_viewports = (
    cfg_audit.get("viewports") or cfg_info["config"].get("viewports") or []
  )
  configured_heights: dict[int, int] = {}
  viewport_widths: list[int] = []
  if isinstance(cfg_viewports, list):
    for item in cfg_viewports:
      m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(item), re.IGNORECASE)
      if m:
        vw, vh = int(m.group(1)), int(m.group(2))
        if vw > 0 and vh > 0:
          viewport_widths.append(vw)
          configured_heights[vw] = vh
  cfg_widths = cfg_audit.get("widths") or cfg_info["config"].get("widths") or []
  configured_widths = [w for w in cfg_widths if isinstance(w, int) and w > 0]
  if not configured_widths and viewport_widths:
    configured_widths = viewport_widths
  cfg_report_widths = (
    cfg_audit.get("report_widths") or cfg_info["config"].get("report_widths") or []
  )
  report_widths = sorted(
    {int(w) for w in cfg_report_widths if isinstance(w, int) and w > 0}
    or REPORT_WIDTHS,
    reverse=True,
  )
  selected_widths = (
    sorted({int(w) for w in args.widths if w > 0})
    if args.widths
    else sorted(set(configured_widths or WIDTHS))
  )
  typography = resolve_project_typography(project_root)
  request = {
    "command": args.command,
    "url": args.url,
    "output": str(Path(args.artifacts).resolve()),
    "widths": selected_widths,
    "heights": configured_heights,
    "report_widths": report_widths,
    "selector": args.selector,
    "ready": getattr(args, "ready", None),
    "a11y": bool(getattr(args, "a11y", False) or args.command in {"a11y", "audit"}),
    "engine": args.engine,
    "executable": args.executable,
    "timeout": args.timeout,
    "flow": load(args.flow) if args.flow else [],
    "edit": load(args.edit) if args.edit else None,
    "authorized_edit": args.authorize_edit,
    "overlaps": args.overlaps,
    "wireframe": bool(
      getattr(args, "wireframe", False)
      or cfg_audit.get("wireframe")
      or cfg_info["config"].get("wireframe")
    ),
    "typography": {
      "font_sans": typography["font_sans"],
      "font_mono": typography["font_mono"],
      "font_faces_css": typography["font_faces_css"],
    },
  }
  process = subprocess.run(
    ["node", str(HOME / "scripts" / "browser.cjs")],
    input=json.dumps(request),
    capture_output=True,
    text=True,
    timeout=args.timeout * max(len(request["widths"]), 1) * 8 + 10,
    check=False,
  )
  if not process.stdout:
    return {
      "status": "not_checked",
      "reason": "browser_process_failed",
      "exit": process.returncode,
      "coverage": [],
    }
  result = json.loads(process.stdout)
  if not isinstance(result, dict):
    raise TypeError("browser report must be an object")
  if process.returncode and result.get("status") != "error":
    raise ValueError("browser process failed")
  if args.profile == "house":
    sheet = parse_sheet((HOME / "references/style.css").read_text(encoding="utf-8"))
    for capture in result.get("captures", []):
      capture["house_measurements"] = check_measurements(
        capture.get("measurements", {}), sheet
      )
      density_report = dict(capture.get("measurements") or {})
      density_report["overlaps"] = capture.get("overlaps")
      capture["density_findings"] = check_density(density_report)
      location = {
        "width": capture["width"],
        "scheme": capture["scheme"],
        "motion": capture["motion"],
      }
      result.setdefault("findings", []).extend(
        {
          "rule": (
            finding.split()[0]
            if re.match(r"^(?:TYP|SPC|HRC|ACC|ICN|DOC|RUN|NUM|NUL)\d{3}\b", finding)
            else "TYP001"
            if ": font-size is " in finding
            else "SPC002"
            if ": border-radius" in finding
            else "SPC001"
          ),
          "severity": "review",
          "evidence": finding,
          "location": location,
        }
        for finding in capture["house_measurements"]
      )
      result.setdefault("findings", []).extend(
        {
          "rule": row["rule"],
          "severity": "review",
          "selector": row["selector"],
          "evidence": row["evidence"],
          "location": location,
        }
        for row in capture["density_findings"]
      )
    if result.get("findings"):
      result["status"] = "findings"
  for finding in result.get("findings", []):
    if isinstance(finding, dict):
      enrich_finding_rule(finding)
  return result


def doctor(root, manifest=None, config=None):
  source = load(CATALOG / "source.json")
  mismatches = [
    row["path"]
    for row in source["files"]
    if not (CATALOG / row["path"]).is_file()
    or hashlib.sha256((CATALOG / row["path"]).read_bytes()).hexdigest() != row["sha256"]
  ]
  runtime = {"status": "not_checked", "reason": "node_unavailable"}
  if shutil.which("node"):
    process = subprocess.run(
      ["node", str(HOME / "scripts/browser.cjs")],
      input=json.dumps({"command": "doctor"}),
      capture_output=True,
      text=True,
      timeout=10,
      check=False,
    )
    runtime = (
      json.loads(process.stdout)
      if process.stdout
      else {
        "status": "not_checked",
        "reason": "browser_process_failed",
        "exit": process.returncode,
      }
    )
  resolved_config = config or find_nuu_config(root)
  configuration = load(resolved_config) if resolved_config else {}
  if not isinstance(configuration, dict):
    raise TypeError("configuration must be an object")
  if configuration.get("url"):
    validated_url(configuration["url"])
  widths = configuration.get("widths", [])
  if not isinstance(widths, list) or any(
    type(width) is not int or width <= 0 for width in widths
  ):
    raise ValueError("configured widths must be positive integers")
  asset_report = (
    assets(root, manifest)
    if manifest
    else {"status": "not_checked", "reason": "manifest_missing"}
  )
  return {
    "version": VERSION,
    "python": sys.version.split()[0],
    "node": shutil.which("node"),
    "catalog": {
      "source": source["repository"],
      "commit": source["commit"],
      "files": len(source["files"]),
      "mismatches": mismatches,
    },
    "browser": runtime,
    "assets": asset_report,
    "configuration": {
      "status": "checked" if resolved_config else "not_checked",
      "path": str(resolved_config) if resolved_config else None,
      "values": configuration,
    },
    "findings": mismatches + asset_report.get("findings", []),
  }


def main(argv=None):
  raw_args = list(sys.argv[1:] if argv is None else argv)
  if raw_args and raw_args[0] in {"check", "ruler", "density"}:
    return measure_main(raw_args)
  if raw_args and raw_args[0] == "walk":
    return walk_main(raw_args[1:])
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  for name in (
    "search",
    "assets",
    "lint",
    "extract",
    "annotate",
    "board",
    "walk",
    "doctor",
    "inspect",
    "flow",
    "audit",
    "collect",
    "a11y",
    "live",
    "check",
    "density",
    "ruler",
  ):
    command = commands.add_parser(name)
    if name == "walk":
      command.add_argument("target", nargs="?", default="http://127.0.0.1:1500")
      command.add_argument("-s", "--script", type=Path, default=None)
      command.add_argument("-V", "--views", action="store_true")
      command.add_argument("-o", "--out", type=Path, default=Path("/tmp/walk"))
      continue
    if name == "check":
      command.add_argument("report", type=Path)
      command.add_argument("-s", "--sheet", type=Path, default=DEFAULT_SHEET)
      command.add_argument("-k", "--scale", type=float, default=DEFAULT_SCALE)
      command.add_argument("-d", "--density", action="store_true")
      continue
    if name == "density":
      command.add_argument("report", type=Path)
      continue
    if name == "ruler":
      command.add_argument("-f", "--from", dest="start", type=point, required=True)
      command.add_argument("-t", "--to", dest="end", type=point, required=True)
      command.add_argument("-s", "--sheet", type=Path, default=DEFAULT_SHEET)
      command.add_argument("-k", "--scale", type=float, default=DEFAULT_SCALE)
      continue
    if name == "board":
      command.add_argument("--project", type=Path, default=Path.cwd())
    else:
      command.add_argument("-p", "--project", type=Path, default=Path.cwd())
    command.add_argument(
      "-o", "--output", type=Path, required=name in {"annotate", "board"}
    )
    if name == "annotate":
      command.add_argument("-i", "--input", type=Path, required=True)
      command.add_argument("-b", "--box", action="append", default=[])
      command.add_argument("-l", "--label", action="append", default=[])
      command.add_argument("-a", "--arrow", action="append", default=[])
      command.add_argument("-c", "--callout", action="append", default=[])
      command.add_argument("-C", "--crop")
      command.add_argument("-s", "--spec", type=Path)
      command.add_argument("-S", "--scale", type=int)
    if name == "board":
      command.add_argument(
        "-T",
        "--type",
        choices=("panels", "design"),
        default=None,
      )
      command.add_argument("-O", "--only", action="append", default=[])
      command.add_argument("-t", "--title")
      command.add_argument("-p", "--panel", action="append", default=[])
      command.add_argument("-c", "--callout", action="append", default=[])
      command.add_argument("-s", "--spec", type=Path)
      command.add_argument("-S", "--scale", type=int)
      command.add_argument("-C", "--columns", type=int)
      command.add_argument("-W", "--wireframe", action="store_true")
    if name == "search":
      command.add_argument("query", nargs="?")
      command.add_argument("-d", "--dataset", "--destination", dest="dataset")
      command.add_argument("-s", "--stack")
      command.add_argument("-l", "--limit", type=int, default=10)
      command.add_argument("-f", "--fetch")
      command.add_argument("-A", "--authorize-fetch", action="store_true")
    if name == "doctor":
      command.add_argument("-m", "--manifest", type=Path)
      command.add_argument("-c", "--config", type=Path)
    if name == "lint":
      command.add_argument("-m", "--manifest", type=Path)
      command.add_argument("-f", "--from", dest="ruler_start", type=point)
      command.add_argument("-t", "--to", dest="ruler_end", type=point)
      command.add_argument("-s", "--sheet", type=Path, default=DEFAULT_SHEET)
      command.add_argument("-k", "--scale", type=float, default=DEFAULT_SCALE)
    if name == "assets":
      command.add_argument("-m", "--manifest", type=Path)
      command.add_argument("-f", "--fetch")
      command.add_argument("-d", "--destination")
      command.add_argument("-A", "--authorize-fetch", action="store_true")
    if name in {"inspect", "flow", "audit", "collect", "a11y", "live"}:
      command.add_argument("-u", "--url", required=name not in {"audit", "collect"})
      command.add_argument("-M", "--measured", type=Path)
      command.add_argument("-d", "--artifacts", default="ui-evidence")
      command.add_argument("-w", "--widths", nargs="*", type=int, default=[])
      command.add_argument(
        "-s", "--selector", default="button,input,textarea,select,a,h1,h2,img,main"
      )
      command.add_argument("-r", "--ready")
      command.add_argument("-a", "--a11y", action="store_true")
      command.add_argument("-e", "--engine", default="auto")
      command.add_argument("-x", "--executable")
      command.add_argument("-t", "--timeout", type=int, default=20)
      command.add_argument("-f", "--flow", type=Path, required=name == "flow")
      command.add_argument(
        "-P", "--profile", choices=("project", "house"), default="project"
      )
      command.add_argument("-E", "--edit", type=Path)
      command.add_argument("-A", "--authorize-edit", action="store_true")
      command.add_argument("-O", "--overlaps", action="store_true")
      command.add_argument("-W", "--wireframe", action="store_true")
      command.add_argument("-D", "--deviations", action="store_true")
  args = parser.parse_args(raw_args)
  root = args.project.resolve()
  if not root.is_dir():
    parser.error("project directory does not exist")
  try:
    severities = (
      check_severities(root) if args.command in {"lint", "audit", "collect"} else {}
    )
    if args.command == "search":
      if args.fetch:
        if not args.dataset:
          raise ValueError("fetch requires --destination (-d)")
        result = fetch_asset(root, args.fetch, args.dataset, args.authorize_fetch)
      elif args.query:
        if not 0 < args.limit <= 100:
          raise ValueError("limit must be from 1 through 100")
        result = search(args.query, args.dataset, args.stack, args.limit)
      else:
        raise ValueError("search requires a query or --fetch")
    elif args.command == "assets":
      if args.fetch:
        if not args.destination:
          raise ValueError("fetch requires --destination")
        result = fetch_asset(root, args.fetch, args.destination, args.authorize_fetch)
      elif args.manifest:
        result = assets(root, args.manifest)
      else:
        raise ValueError("assets requires --manifest or --fetch")
    elif args.command == "lint":
      if (args.ruler_start is None) ^ (args.ruler_end is None):
        raise ValueError("ruler requires both --from and --to")
      result = lint(
        root,
        manifest=args.manifest,
        ruler_start=args.ruler_start,
        ruler_end=args.ruler_end,
        scale=args.scale,
      )
    elif args.command == "extract":
      result = extract(root)
    elif args.command == "annotate":
      result = annotate_image(
        args.input,
        args.output,
        boxes=args.box,
        labels=args.label,
        arrows=args.arrow,
        callouts=args.callout,
        spec_path=args.spec,
        scale=args.scale,
        project=root,
        crop=args.crop,
      )
      args.output = None
    elif args.command == "board":
      board_type = args.type
      if board_type is None and args.spec is not None and Path(args.spec).is_file():
        try:
          spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8"))
          if isinstance(spec_obj, dict) and (
            "boards" in spec_obj or "stylesheet" in spec_obj
          ):
            board_type = "design"
        except (OSError, json.JSONDecodeError):
          pass
      if board_type == "design":
        result = build_design_boards(
          args.output,
          spec_path=args.spec,
          project=root,
          wireframe=bool(args.wireframe),
          only=args.only,
        )
      else:
        result = build_board(
          args.output,
          title=args.title,
          panels=args.panel,
          callouts=args.callout,
          spec_path=args.spec,
          columns=args.columns,
          scale=args.scale,
          project=root,
          wireframe=bool(args.wireframe),
        )
      args.output = None
    elif args.command == "doctor":
      result = doctor(root, args.manifest, args.config)
    elif args.command in {"inspect", "flow", "a11y", "live"}:
      if args.timeout <= 0 or any(width <= 0 for width in args.widths):
        raise ValueError("timeout and widths must be positive")
      result = browser(args)
    else:
      source_res = lint(root)
      deviations = list(source_res.get("deviations", []))
      if getattr(args, "deviations", False):
        for extra_path in files(root, {".md", ".toml", ".py"}):
          extra_lines = extra_path.read_text(encoding="utf-8").splitlines()
          deviations.extend(collect_deviation_markers(extra_path, extra_lines))
      rendered_res = (
        browser(args)
        if args.url
        else {"status": "not_checked", "reason": "url_missing"}
      )
      if getattr(args, "measured", None):
        measured_data = load(args.measured)
        sheet = parse_sheet((HOME / "references/style.css").read_text(encoding="utf-8"))
        house_m = check_measurements(measured_data, sheet)
        density_m = check_density(measured_data)
        measured_findings = [
          {
            "rule": (
              item.split()[0]
              if re.match(r"^(?:TYP|SPC|HRC|ACC|ICN|DOC|RUN|NUM|NUL)\d{3}\b", item)
              else "TYP001"
              if ": font-size is " in item
              else "SPC002"
              if ": border-radius" in item
              else "SPC001"
            ),
            "severity": "review",
            "evidence": item,
          }
          for item in house_m
        ] + [
          {
            "rule": row["rule"],
            "severity": "review",
            "selector": row["selector"],
            "evidence": row["evidence"],
          }
          for row in density_m
        ]
        rendered_res.setdefault("findings", []).extend(measured_findings)
        if not args.url:
          rendered_res["status"] = "findings" if measured_findings else "checked"
      for finding in rendered_res.get("findings", []):
        if isinstance(finding, dict):
          enrich_finding_rule(finding)
      result = {
        "source": source_res,
        "icons": source_res.get("icons") or check_icons(root),
        "design": source_res.get("design") or extract(root),
        "marks": source_res.get("marks", []),
        "suppressions": source_res.get("suppressions", []),
        "deviations": deviations,
        "deviation_marks": [d["mark"] for d in deviations],
        "report_sections": [
          "Status",
          "Counts",
          "Unrun Checks",
          "Automated Findings",
          "Visual Judgments",
          "Deviations",
          "Captures",
        ],
        "rendered": rendered_res,
        "visual_judgment": "not_run",
        "participant_research": "not_run",
        "real_device": "not_run",
      }
    if args.command in {"audit", "collect"}:
      if (
        args.command == "collect"
        and getattr(args, "deviations", False)
        and not args.url
      ):
        result["findings"] = []
        result["status"] = "checked"
      else:
        result["findings"] = result["source"]["findings"] + result["rendered"].get(
          "findings", []
        )
        result["status"] = result["rendered"].get("status")
    for finding in result.get("findings", []):
      if isinstance(finding, dict) and finding.get("rule") in severities:
        finding["severity"] = severities[finding["rule"]]
    result = {
      "tool": args.command,
      "version": VERSION,
      "timestamp": datetime.now(UTC).isoformat(),
      **result,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
      with args.output.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
    print(serialized, end="")
    if result.get("status") == "error":
      return 2
    if any(
      not isinstance(finding, dict)
      or finding.get("rule") not in severities
      or finding.get("severity") == "error"
      for finding in result.get("findings", [])
    ):
      return 1
    if result.get("status") == "not_checked":
      return 3
    return 0
  except (
    OSError,
    ValueError,
    TypeError,
    csv.Error,
    subprocess.TimeoutExpired,
  ) as error:
    print(json.dumps({"tool": args.command, "status": "error", "error": str(error)}))
    return 2


if __name__ == "__main__":
  sys.exit(main())
