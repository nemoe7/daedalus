"""Measure live pages and PNG captures against the Nuu stylesheet and spacing scale."""

from __future__ import annotations

import argparse
import math
import re
from collections.abc import Sequence
from pathlib import Path

DEFAULT_SHEET = Path(__file__).resolve().parent.parent / "references" / "style.css"
DEFAULT_SCALE = 4
DEFAULT_TOLERANCE = 30
DEFAULT_REDLINE_HEX = "#ff3366"
MIN_REDLINE_CONTRAST = 3.0
FALLBACK_CYAN_RGBA = (0, 229, 255, 255)
FALLBACK_INK_RGBA = (17, 24, 39, 255)
HALO_LIGHT_RGBA = (255, 255, 255, 255)
HALO_DARK_RGBA = (17, 24, 39, 255)

DEVTOOLS_TINTS = {
  "content": (111, 168, 220, 115),
  "padding": (147, 196, 125, 115),
  "border": (255, 217, 102, 140),
  "margin": (246, 178, 107, 115),
}

MIN_INK_RATIO = 0.75
MAIN_MIN_INK_RATIO = 0.40
MAX_SIBLING_GAP = 320.0
MAX_ROW_SLACK = 24.0
TOOLBAR_TAGS = {"header", "nav", "footer"}
TOOLBAR_ROLES = {"banner", "toolbar", "navigation", "menubar", "tablist"}
TOOLBAR_NAME_RE = re.compile(
  r"(?:^|[-_.#\s])(topbar|toolbar|header|navbar|menubar|statusbar|action-bar|tab-bar)(?:$|[-_.\s:])",
  re.IGNORECASE,
)
STRETCH_EXEMPT_INPUTS = {
  "hidden",
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "image",
}
PX = re.compile(r"^(-?\d+(?:\.\d+)?)px$")
POINT_RE = re.compile(r"^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$")
CUSTOM_PROPERTY = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")
TYPE_PROPERTIES = ("font-size",)
SPACING_PROPERTIES = ("padding", "margin", "gap", "row-gap", "column-gap")
LAYOUT_PROPERTIES = ("border-radius",)
RADIUS_SIZES = {4.0, 6.0, 10.0, 9999.0}
LIST_INDENT = "2ch"
LIST_INDENT_PX_TOLERANCE = 1.0
LIST_WRAP_PX_TOLERANCE = 1.0
RULE_PREFIX_RE = re.compile(r"^([A-Z]{3}\d{3})\s+(.+)$")


def parse_sheet(text: str) -> dict[str, str]:
  """Return every custom property in a stylesheet, keyed without leading dashes."""
  return {prop: value.strip() for prop, value in CUSTOM_PROPERTY.findall(text)}


def named_sizes(tokens: dict[str, str], *, ramp: bool) -> set[float]:
  """Return pixel sizes declared by the stylesheet type ramp or layout tokens."""
  sizes = set()
  for prop, value in tokens.items():
    match = PX.match(value)
    if not match:
      continue
    is_type = prop.startswith("size-") or prop == "eyebrow"
    if is_type is ramp:
      sizes.add(float(match.group(1)))
  return sizes


def on_scale(value: float, scale: float) -> bool:
  """Return True when value is an integer multiple of scale."""
  return math.isclose(value / scale, round(value / scale), abs_tol=1e-9)


def off_scale(prop: str, value: str, tokens: dict[str, str], scale: float) -> bool:
  """Check spacing against scale, font sizes against the ramp, and radii against tokens."""
  match = PX.match(value.strip())
  if not match:
    return False
  number = float(match.group(1))
  if prop in TYPE_PROPERTIES:
    return number not in named_sizes(tokens, ramp=True)
  if prop.startswith(LAYOUT_PROPERTIES):
    return number not in (RADIUS_SIZES | {0.0})
  if number == 0 or on_scale(number, scale):
    return False
  return prop.startswith(SPACING_PROPERTIES)


def _numeric_value(value):
  if isinstance(value, bool):
    return None
  if isinstance(value, (int, float)):
    return float(value) if math.isfinite(value) else None
  if isinstance(value, str):
    match = PX.match(value.strip())
    if match:
      return float(match.group(1))
    try:
      number = float(value)
    except ValueError:
      return None
    return number if math.isfinite(number) else None
  return None


def _list_indent_status(element: dict):
  data = element.get("list_indent")
  if data is None:
    return None
  if not isinstance(data, dict):
    return {}, ["list indentation metrics are malformed"], False
  errors = []
  indent = str(data.get("indent") or "").strip().lower()
  ch_px = _numeric_value(data.get("ch_px"))
  padding = _numeric_value(data.get("padding_inline_start"))
  slot_verified = (
    indent == LIST_INDENT and ch_px is not None and ch_px > 0 and padding is not None
  )
  if indent != LIST_INDENT:
    errors.append(f"--indent is {indent or 'unset'}, expected {LIST_INDENT}")
  if ch_px is None or ch_px <= 0 or padding is None:
    slot_verified = False
    errors.append("2ch list padding could not be measured")
  elif not math.isclose(padding, 2 * ch_px, abs_tol=LIST_INDENT_PX_TOLERANCE):
    slot_verified = False
    errors.append(
      f"padding-inline-start is {padding:g}px, expected {2 * ch_px:g}px for 2ch"
    )
  if str(data.get("list_style_position") or "").strip().lower() != "outside":
    errors.append("list-style-position is not outside")
  items = data.get("items", [])
  if isinstance(items, list):
    for item in items:
      if not isinstance(item, dict):
        continue
      raw_starts = item.get("line_starts")
      if not isinstance(raw_starts, list) or len(raw_starts) < 2:
        continue
      starts = [_numeric_value(value) for value in raw_starts]
      if any(value is None for value in starts):
        errors.append("wrapped-line positions are not numeric")
        break
      first = starts[0]
      if any(abs(value - first) > LIST_WRAP_PX_TOLERANCE for value in starts[1:]):
        errors.append(
          f"{item.get('selector', 'list item')}: wrapped lines do not align with the first line"
        )
        break
  else:
    errors.append("wrapped-line positions are missing")
  return data, errors, slot_verified


def _verified_list_padding(element: dict, prop: str, value: str, status) -> bool:
  if status is None or prop not in {
    "padding-inline-start",
    "padding-left",
    "padding-right",
  }:
    return False
  data, _, slot_verified = status
  if not slot_verified:
    return False
  direction = str(data.get("direction") or "ltr").strip().lower()
  physical_start = "padding-right" if direction == "rtl" else "padding-left"
  if prop not in {"padding-inline-start", physical_start}:
    return False
  observed = _numeric_value(value)
  expected = _numeric_value(data.get("padding_inline_start"))
  return (
    observed is not None
    and expected is not None
    and math.isclose(observed, expected, abs_tol=LIST_INDENT_PX_TOLERANCE)
  )


HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
HEADING_SELECTOR_RE = re.compile(
  r"(?:^|[\s>+~])h[1-6](?:$|[.#\[:])|\.(?:title|panel-title)(?:$|[.#\[:\s])",
  re.IGNORECASE,
)
COMPACT_LINE_HEIGHT_TAGS = {
  "button",
  "input",
  "select",
  "svg",
  "path",
  "circle",
  "rect",
  "line",
  "polyline",
  "polygon",
  "img",
  "hr",
  "br",
}


def is_heading_element(element: dict) -> bool:
  """Return whether an element represents a heading (h1 to h6)."""
  tag = str(element.get("tag") or "").strip().lower()
  if tag in HEADING_TAGS:
    return True
  if str(element.get("role") or "").strip().lower() == "heading":
    return True
  selector = str(element.get("selector") or "").strip()
  return bool(HEADING_SELECTOR_RE.search(selector))


def _line_height_finding(element: dict) -> str | None:
  values = element.get("values") or {}
  raw_lh = str(values.get("line-height") or "").strip().lower()
  raw_fs = str(values.get("font-size") or "").strip().lower()
  if not raw_lh or raw_lh == "normal" or not raw_fs:
    return None
  tag = str(element.get("tag") or "").strip().lower()
  if tag in COMPACT_LINE_HEIGHT_TAGS or bool(element.get("icon_only")):
    return None
  text = element.get("text")
  if isinstance(text, str) and not text.strip():
    return None
  selector = str(element.get("selector") or "?")
  if re.search(r"\b(?:icon-button|close-button|badge|pill)\b", selector, re.IGNORECASE):
    return None
  fs_px = _numeric_value(raw_fs)
  if fs_px is None or fs_px <= 0:
    return None
  if raw_lh.endswith("px"):
    lh_px = _numeric_value(raw_lh)
    if lh_px is None:
      return None
    ratio = lh_px / fs_px
  else:
    ratio = _numeric_value(raw_lh)
    if ratio is None:
      return None
  heading = is_heading_element(element)
  low, high = (1.1, 1.3) if heading else (1.3, 1.5)
  if ratio < low - 1e-6 or ratio > high + 1e-6:
    band = "1.1-1.3x" if heading else "1.3-1.5x"
    return (
      f"TYP101 {selector}: line-height {values['line-height']} ({ratio:.2f}x) "
      f"is outside {band}"
    )
  return None


def _letter_spacing_finding(element: dict) -> str | None:
  values = element.get("values") or {}
  if "letter-spacing" not in values:
    return None
  raw_ls = str(values.get("letter-spacing") or "").strip().lower()
  if not raw_ls:
    return None
  selector = str(element.get("selector") or "?")
  classes = element.get("classes") or []
  if "eyebrow" in classes or re.search(r"\beyebrow\b", selector, re.IGNORECASE):
    return None
  text = element.get("text")
  if isinstance(text, str) and not text.strip():
    return None
  heading = is_heading_element(element)
  if raw_ls in {"normal", "0", "0px", "0em", "0%"}:
    em_ratio = 0.0
  elif raw_ls.endswith("em"):
    num = _numeric_value(raw_ls[:-2])
    if num is None:
      return None
    em_ratio = num
  elif raw_ls.endswith("%"):
    num = _numeric_value(raw_ls[:-1])
    if num is None:
      return None
    em_ratio = num / 100.0
  elif raw_ls.endswith("px"):
    ls_px = _numeric_value(raw_ls)
    fs_px = _numeric_value(str(values.get("font-size") or ""))
    if ls_px is None or fs_px is None or fs_px <= 0:
      return None
    em_ratio = ls_px / fs_px
  else:
    return None
  if heading:
    if em_ratio < -0.03 - 1e-4 or em_ratio > -0.005 + 1e-4:
      return (
        f"TYP102 {selector}: heading letter-spacing {values['letter-spacing']} "
        f"({em_ratio:.4f}em) is outside -0.005em to -0.03em"
      )
    return None
  if abs(em_ratio) > 1e-4:
    return f"TYP102 {selector}: non-heading letter-spacing {values['letter-spacing']} must be 0"
  return None


def _element_line_count(element: dict) -> int | None:
  explicit = element.get("line_count")
  if isinstance(explicit, (int, float)) and explicit > 0:
    return round(explicit)
  values = element.get("values") or {}
  lh_px = _numeric_value(str(values.get("line-height") or ""))
  if lh_px is None or lh_px <= 0:
    return None
  bounds = element.get("bounds") or {}
  raw_h = bounds.get("height")
  h_px = (
    float(raw_h)
    if isinstance(raw_h, (int, float))
    else _numeric_value(str(values.get("height") or ""))
  )
  if h_px is None or h_px <= 0:
    return None
  pad_top = _numeric_value(str(values.get("padding-top") or "0px")) or 0.0
  pad_bot = _numeric_value(str(values.get("padding-bottom") or "0px")) or 0.0
  content_h = max(0.0, h_px - pad_top - pad_bot)
  return max(1, round(content_h / lh_px)) if content_h > 0 else None


def _normalize_text_align(raw: str) -> str:
  cleaned = raw.strip().lower()
  if cleaned in {"left", "start"}:
    return "start"
  if cleaned in {"right", "end"}:
    return "end"
  return cleaned


def _text_alignment_finding(element: dict) -> str | None:
  values = element.get("values") or {}
  align = str(values.get("text-align") or "").strip().lower()
  selector = str(element.get("selector") or "?")
  mismatch = element.get("heading_body_alignment")
  if isinstance(mismatch, dict):
    h_align = _normalize_text_align(str(mismatch.get("heading_align") or align))
    b_align = _normalize_text_align(str(mismatch.get("body_align") or ""))
    if h_align and b_align and h_align != b_align:
      return (
        f"TYP103 {selector}: heading text-align ({h_align}) "
        f"mismatches body text-align ({b_align})"
      )
  if not align:
    return None
  if align == "justify":
    return f"TYP103 {selector}: text-align justify creates uneven word gaps"
  if align == "center":
    tag = str(element.get("tag") or "").strip().lower()
    if tag in COMPACT_LINE_HEIGHT_TAGS or tag in {"th", "td"}:
      return None
    lines = _element_line_count(element)
    if lines is not None and lines > 3:
      return (
        f"TYP103 {selector}: centered prose spans {lines} lines (at most 3 allowed)"
      )
  return None


PROSE_BLOCK_TAGS = {"p", "li", "blockquote", "dd"}


def _body_width_finding(element: dict, report_width: float) -> str | None:
  if report_width < 1024:
    return None
  selector = str(element.get("selector") or "?")
  tag = str(element.get("tag") or "").strip().lower()
  classes = element.get("classes") or []
  is_prose_container = (
    tag == "article"
    or "prose" in classes
    or bool(re.search(r"\bprose\b", selector, re.IGNORECASE))
  )
  lines = _element_line_count(element)
  is_multiline_prose = tag in PROSE_BLOCK_TAGS and lines is not None and lines >= 2
  if not (is_prose_container or is_multiline_prose):
    return None
  values = element.get("values") or {}
  standalone = not (
    _is_card_or_dialog(element) or bool(element.get("in_card_or_dialog"))
  )
  raw_max = str(values.get("max-width") or "").strip().lower()
  if raw_max.endswith("ch"):
    max_ch = _numeric_value(raw_max[:-2])
    if max_ch is not None and (max_ch > 75.5 or (standalone and max_ch < 49.5)):
      return f"TYP201 {selector}: max-width {values['max-width']} is outside 50 to 75ch"
  bounds = element.get("bounds") or {}
  raw_w = bounds.get("width")
  w_px = (
    float(raw_w)
    if isinstance(raw_w, (int, float))
    else _numeric_value(str(values.get("width") or ""))
  )
  if w_px is None or w_px <= 0:
    return None
  pad_l = _numeric_value(str(values.get("padding-left") or "0px")) or 0.0
  pad_r = _numeric_value(str(values.get("padding-right") or "0px")) or 0.0
  content_w = max(0.0, w_px - pad_l - pad_r)
  raw_ch = element.get("ch_px")
  ch_px = float(raw_ch) if isinstance(raw_ch, (int, float)) and raw_ch > 0 else None
  if ch_px is None:
    fs_px = _numeric_value(str(values.get("font-size") or ""))
    if fs_px is not None and fs_px > 0:
      ch_px = 0.5 * fs_px
  if ch_px is None or ch_px <= 0:
    return None
  ch_width = content_w / ch_px
  if ch_width > 75.5 or (standalone and ch_width < 49.5):
    return f"TYP201 {selector}: prose width {ch_width:.1f}ch is outside 50 to 75ch"
  return None


def _is_card_or_dialog(element: dict) -> bool:
  tag = str(element.get("tag") or "").strip().lower()
  role = str(element.get("role") or "").strip().lower()
  classes = element.get("classes") or []
  selector = str(element.get("selector") or "")
  return (
    tag == "dialog"
    or role in {"dialog", "alertdialog"}
    or "card" in classes
    or bool(re.search(r"\.(?:card|dialog)\b|^dialog\b", selector, re.IGNORECASE))
  )


def _card_font_hierarchy_finding(element: dict, all_elements: list[dict]) -> str | None:
  selector = str(element.get("selector") or "?")
  hier = element.get("heading_hierarchy")
  if isinstance(hier, dict):
    h_fs = _numeric_value(hier.get("heading_size"))
    s_fs = _numeric_value(hier.get("sub_size"))
    h_wt = _numeric_value(hier.get("heading_weight")) or 400.0
    s_wt = _numeric_value(hier.get("sub_weight")) or 400.0
    if (
      h_fs is not None
      and s_fs is not None
      and (h_fs < s_fs - 0.1 or (abs(h_fs - s_fs) <= 0.1 and h_wt <= s_wt))
    ):
      return (
        f"HRC101 {selector}: heading ({h_fs:g}px/{h_wt:g}) lacks visual "
        f"hierarchy over subordinate text ({s_fs:g}px/{s_wt:g})"
      )
  explicit = element.get("card_font_sizes")
  if explicit is None and not _is_card_or_dialog(element):
    return None
  sizes: set[float] = set()
  if isinstance(explicit, list):
    for item in explicit:
      val = float(item) if isinstance(item, (int, float)) else _numeric_value(str(item))
      if val is not None and val > 0:
        sizes.add(round(val, 2))
  else:
    prefix_space = f"{selector} "
    prefix_child = f"{selector}>"
    prefix_child_sp = f"{selector} >"
    for other in all_elements:
      other_sel = str(other.get("selector") or "")
      if not other_sel.startswith((prefix_space, prefix_child, prefix_child_sp)):
        continue
      fs = _numeric_value(str((other.get("values") or {}).get("font-size") or ""))
      if fs is not None and fs > 0:
        sizes.add(round(fs, 2))
  if len(sizes) > 2:
    ordered = ", ".join(f"{val:g}px" for val in sorted(sizes))
    return (
      f"HRC101 {selector}: card or dialog mixes {len(sizes)} font sizes "
      f"({ordered}, at most 2 allowed)"
    )
  return None


def _proximity_spacing_finding(element: dict) -> str | None:
  prox = element.get("group_proximity")
  inter_px: float | None = None
  intra_px: float | None = None
  if isinstance(prox, dict):
    raw_inter = prox.get("inter_px")
    raw_intra = prox.get("intra_px")
    if isinstance(raw_inter, (int, float)) and isinstance(raw_intra, (int, float)):
      inter_px, intra_px = float(raw_inter), float(raw_intra)
  else:
    raw_inter = element.get("inter_group_gap")
    raw_intra = element.get("intra_group_gap")
    if raw_inter is not None and raw_intra is not None:
      inter_px = (
        float(raw_inter)
        if isinstance(raw_inter, (int, float))
        else _numeric_value(str(raw_inter))
      )
      intra_px = (
        float(raw_intra)
        if isinstance(raw_intra, (int, float))
        else _numeric_value(str(raw_intra))
      )
  if inter_px is None or intra_px is None or intra_px < 0:
    return None
  selector = str(element.get("selector") or "?")
  if inter_px <= 0:
    return f"SPC101 {selector}: inter-group gap is 0px (elements touch)"
  if intra_px > 0 and inter_px + 1e-6 < 1.5 * intra_px:
    ratio = inter_px / intra_px
    return (
      f"SPC101 {selector}: inter-group gap {inter_px:g}px is {ratio:.2f}x "
      f"intra-group gap {intra_px:g}px (at least 1.5x required)"
    )
  return None


HOUSE_FONT_FALLBACKS = frozenset(
  {
    "system-ui",
    "-apple-system",
    "blinkmacsystemfont",
    "segoe ui",
    "roboto",
    "helvetica neue",
    "arial",
    "sans-serif",
    "ui-monospace",
    "sfmono-regular",
    "menlo",
    "monaco",
    "consolas",
    "liberation mono",
    "monospace",
    "inter",
    "inherit",
  }
)


def _approved_font_families(tokens: dict[str, str]) -> set[str]:
  allowed = set(HOUSE_FONT_FALLBACKS)
  for key in ("font-sans", "font-mono"):
    raw = tokens.get(key) or ""
    for part in raw.split(","):
      cleaned = part.strip().strip("\"'").lower()
      if cleaned:
        allowed.add(cleaned)
  return allowed


def _paired_icon_finding(element: dict) -> str | None:
  icon_h = _numeric_value(element.get("paired_icon_height"))
  if icon_h is None or icon_h <= 0:
    return None
  values = element.get("values") or {}
  lh_px = _numeric_value(str(values.get("line-height") or ""))
  if lh_px is None or lh_px <= 0:
    return None
  if abs(icon_h - lh_px) > 1.0:
    selector = str(element.get("selector") or "?")
    return (
      f"ICN102 {selector}: paired icon height {icon_h:g}px differs from "
      f"text line-height {lh_px:g}px"
    )
  return None


def _core_principles_findings(element: dict, tokens: dict[str, str]) -> list[str]:
  out: list[str] = []
  selector = str(element.get("selector") or "?")
  tag = str(element.get("tag") or "").strip().lower()
  classes = element.get("classes") or []
  values = element.get("values") or {}
  insets = element.get("card_inline_insets")
  if isinstance(insets, list):
    nums = [float(x) for x in insets if isinstance(x, (int, float))]
    if len(nums) >= 2 and max(nums) - min(nums) > 2.0:
      joined = ", ".join(f"{val:g}px" for val in nums)
      out.append(
        f"SPC102 {selector}: stacked card children have ragged inline-start insets ({joined})"
      )
  if _is_card_or_dialog(element):
    pad_l = _numeric_value(str(values.get("padding-left") or ""))
    pad_r = _numeric_value(str(values.get("padding-right") or ""))
    if pad_l is not None and pad_r is not None and abs(pad_l - pad_r) > 2.0:
      out.append(
        f"SPC102 {selector}: asymmetric card inline padding ({values['padding-left']} vs {values['padding-right']})"
      )
  weight = str(values.get("font-weight") or "").strip().lower()
  if weight and weight not in {"normal", "bold", "400", "500", "600", "700"}:
    out.append(f"TYP001 {selector}: font-weight {weight} is outside the house weights")
  raw_ff = str(values.get("font-family") or "").strip()
  if raw_ff:
    families = [
      part.strip().strip("\"'").lower() for part in raw_ff.split(",") if part.strip()
    ]
    allowed = _approved_font_families(tokens)
    if families and not any(fam in allowed for fam in families):
      out.append(
        f"TYP002 {selector}: font-family {raw_ff} is outside the house font tokens"
      )
  if tag in PROSE_BLOCK_TAGS:
    font_style = str(values.get("font-style") or "").strip().lower()
    if font_style in {"italic", "oblique"}:
      out.append(
        f"TYP002 {selector}: font-style {font_style} on body block reduces readability"
      )
  transform = str(values.get("text-transform") or "").strip().lower()
  if transform == "uppercase" and not (
    "eyebrow" in classes or bool(re.search(r"\beyebrow\b", selector, re.IGNORECASE))
  ):
    out.append(f"TYP003 {selector}: text-transform uppercase is reserved for .eyebrow")
  fills = element.get("button_fill_colors")
  if (
    isinstance(fills, list)
    and len({str(x).strip().lower() for x in fills if str(x).strip()}) >= 2
  ):
    unique_fills = sorted({str(x).strip().lower() for x in fills if str(x).strip()})
    out.append(
      f"HRC001 {selector}: multiple distinct colored action buttons ({', '.join(unique_fills)})"
    )
  return out


def check(
  report: dict, tokens: dict[str, str], scale: float = DEFAULT_SCALE
) -> list[str]:
  """Return off-scale values and measured list-indent, icon, and typography findings."""
  findings = []
  report_width = float(report.get("width") or 0)
  elements = report.get("elements", [])
  for element in elements:
    selector = element.get("selector", "?")
    if selector.startswith("::-webkit-scrollbar"):
      continue
    list_status = _list_indent_status(element)
    if list_status and list_status[1]:
      findings.append(f"TYP203 {selector}: {'; '.join(list_status[1])}")
    for extra in (
      _paired_icon_finding(element),
      _line_height_finding(element),
      _letter_spacing_finding(element),
      _text_alignment_finding(element),
      _body_width_finding(element, report_width),
      _card_font_hierarchy_finding(element, elements),
      _proximity_spacing_finding(element),
      *_core_principles_findings(element, tokens),
    ):
      if extra:
        findings.append(extra)
    for prop, value in element.get("values", {}).items():
      if _verified_list_padding(element, prop, str(value), list_status):
        continue
      if off_scale(prop, str(value), tokens, scale):
        if prop in TYPE_PROPERTIES:
          findings.append(
            f"TYP001 {selector}: {prop} is {value}, outside the type ramp"
          )
        elif prop.startswith(LAYOUT_PROPERTIES):
          findings.append(
            f"SPC002 {selector}: {prop} is {value}, outside the allowed radii"
          )
        else:
          findings.append(f"{selector}: {prop} is {value}, off the {scale:g} px scale")
  return findings


def is_main_landmark(element: dict) -> bool:
  """Return whether an element is a <main> or [role='main'] landmark."""
  tag = str(element.get("tag") or "").lower()
  if tag == "main":
    return True
  role = str(element.get("role") or "").lower()
  if role == "main":
    return True
  selector = str(element.get("selector") or "").strip().lower()
  return bool(re.match(r"^main(?=$|[.#\[:\s])", selector))


def is_toolbar_or_header_bar(element: dict, height: float) -> bool:
  """Return whether an element is a header or toolbar bar exempt from SPC201."""
  if bool(element.get("toolbar_exempt")):
    return True
  tag = str(element.get("tag") or "").lower()
  if tag in TOOLBAR_TAGS:
    return True
  role = str(element.get("role") or "").lower()
  if role in TOOLBAR_ROLES:
    return True
  selector = str(element.get("selector") or "")
  if TOOLBAR_NAME_RE.search(selector):
    return True
  values = element.get("values") or {}
  justify = str(values.get("justify-content") or "").lower()
  return 0 < height <= 64 and justify == "space-between"


def _clean_bounds(bounds: dict) -> dict | None:
  if not isinstance(bounds, dict) or ("width" not in bounds and "height" not in bounds):
    return None
  return {
    "x": round(float(bounds.get("x", 0) or 0), 2),
    "y": round(float(bounds.get("y", 0) or 0), 2),
    "width": round(float(bounds.get("width", 0) or 0), 2),
    "height": round(float(bounds.get("height", 0) or 0), 2),
  }


def check_density(report: dict) -> list[dict]:
  """Return density, slack, clipping, button-group, and overlap findings."""
  findings: list[dict] = []
  viewport_width = float(report.get("width", 1280) or 1280)
  for element in report.get("elements", []):
    selector = element.get("selector", "?")
    bounds = element.get("bounds") or {}
    clean_b = _clean_bounds(bounds)
    b_extra = {"bounds": clean_b} if clean_b is not None else {}
    width = float(bounds.get("width", element.get("width", 0)) or 0)
    height = float(bounds.get("height", element.get("height", 0)) or 0)
    if (
      viewport_width >= 900
      and width >= 320
      and not is_toolbar_or_header_bar(element, height)
    ):
      raw_ratio = element.get("ink_ratio")
      if raw_ratio is None:
        raw_ink = element.get("ink_width")
        if raw_ink is not None and width > 0:
          raw_ratio = float(raw_ink) / width
      if raw_ratio is None and element.get("children_area") is not None and height > 0:
        raw_ratio = float(element["children_area"]) / (width * height)
      min_ratio = MAIN_MIN_INK_RATIO if is_main_landmark(element) else MIN_INK_RATIO
      if raw_ratio is not None and float(raw_ratio) < min_ratio:
        ratio = float(raw_ratio)
        findings.append(
          {
            "rule": "SPC201",
            "selector": selector,
            "evidence": (
              f"{selector}: ink ratio is {ratio:.2f} (< {min_ratio:.2f})"
              f" across {width:g} px"
            ),
            **b_extra,
          }
        )
    gap = float(element.get("max_sibling_gap", element.get("sibling_gap", 0)) or 0)
    if gap > MAX_SIBLING_GAP and (width <= 0 or gap > 0.4 * width):
      findings.append(
        {
          "rule": "SPC202",
          "selector": selector,
          "evidence": f"{selector}: sibling gap is {gap:g} px (> {MAX_SIBLING_GAP:g} px)",
          **b_extra,
        }
      )
    tag = str(element.get("tag") or "").lower()
    input_type = str(element.get("input_type") or "text").lower()
    slack = float(element.get("row_slack", 0) or 0)
    if (
      tag == "select" or (tag == "input" and input_type not in STRETCH_EXEMPT_INPUTS)
    ) and slack > MAX_ROW_SLACK:
      findings.append(
        {
          "rule": "SPC203",
          "selector": selector,
          "evidence": f"{selector}: input ends {slack:g} px before its row edge",
          **b_extra,
        }
      )
    icon_only = bool(element.get("icon_only"))
    if icon_only and width > 0 and height > 0 and abs(width - height) > 1.0:
      findings.append(
        {
          "rule": "HRC102",
          "selector": selector,
          "evidence": (
            f"{selector}: icon-only button is {width:g}x{height:g} px (not symmetric)"
          ),
          **b_extra,
        }
      )
    group_sizes = element.get("button_group_sizes")
    if isinstance(group_sizes, list) and len(group_sizes) >= 2:
      widths: list[float] = []
      heights: list[float] = []
      for item in group_sizes:
        if isinstance(item, dict):
          w = float(item.get("width", 0) or 0)
          h = float(item.get("height", 0) or 0)
          if w > 0 and h > 0:
            widths.append(w)
            heights.append(h)
      if len(widths) >= 2 and (
        max(widths) - min(widths) > 1.0 or max(heights) - min(heights) > 1.0
      ):
        dims = ", ".join(
          f"{w:g}x{h:g}px" for w, h in zip(widths, heights, strict=False)
        )
        findings.append(
          {
            "rule": "HRC102",
            "selector": selector,
            "evidence": (
              f"{selector}: buttons in group do not share identical size ({dims})"
            ),
            **b_extra,
          }
        )
    values = element.get("values") or {}
    overflow_x = str(values.get("overflow-x") or values.get("overflow") or "").lower()
    overflow_y = str(values.get("overflow-y") or values.get("overflow") or "").lower()
    scroll_w = float(element.get("scroll_width", 0) or 0)
    client_w = float(element.get("client_width", 0) or 0)
    scroll_h = float(element.get("scroll_height", 0) or 0)
    client_h = float(element.get("client_height", 0) or 0)
    is_ellipsis = str(values.get("text-overflow") or "").lower() == "ellipsis"
    clipped = bool(element.get("clipped_content"))
    if not clipped and not is_ellipsis:
      if (
        overflow_x in {"hidden", "clip"} and client_w > 0 and scroll_w > client_w + 1.0
      ):
        clipped = True
      if (
        overflow_y in {"hidden", "clip"} and client_h > 0 and scroll_h > client_h + 1.0
      ):
        clipped = True
    if clipped:
      findings.append(
        {
          "rule": "SPC204",
          "selector": selector,
          "evidence": f"{selector}: non-scrolling container clips child content",
          **b_extra,
        }
      )
    overlap_target = element.get("sibling_overlap")
    if overlap_target:
      findings.append(
        {
          "rule": "SPC205",
          "selector": selector,
          "evidence": f"{selector}: overlaps sibling {overlap_target}",
          **b_extra,
        }
      )
  overlaps = report.get("overlaps") or []
  if isinstance(overlaps, dict):
    overlaps = overlaps.get("pairs") or []
  for pair in overlaps:
    if isinstance(pair, dict) and pair.get("relation") == "separate":
      first = str(pair.get("first") or "?")
      second = str(pair.get("second") or "?")
      findings.append(
        {
          "rule": "SPC205",
          "selector": first,
          "evidence": f"{first}: overlaps sibling {second}",
        }
      )
  return findings


def ruler(
  start: tuple[float, float], end: tuple[float, float], scale: float = DEFAULT_SCALE
) -> dict:
  """Return axis spans, Euclidean distance, and scale conformance between two points."""
  dx = end[0] - start[0]
  dy = end[1] - start[1]
  return {
    "dx": dx,
    "dy": dy,
    "distance": math.hypot(dx, dy),
    "axes": {"x": on_scale(dx, scale), "y": on_scale(dy, scale)},
    "on_scale": on_scale(dx, scale) and on_scale(dy, scale),
  }


def point(text: str) -> tuple[float, float]:
  """Parse one `x,y` coordinate pair."""
  try:
    x_raw, y_raw = text.split(",")
    x, y = float(x_raw), float(y_raw)
  except ValueError as error:
    raise argparse.ArgumentTypeError(f"a guide reads as x,y, not {text!r}") from error
  if not (math.isfinite(x) and math.isfinite(y)):
    raise argparse.ArgumentTypeError(f"guide coordinates must be finite: {text!r}")
  return x, y


def parse_drop_spec(raw: str) -> tuple[int, int, str]:
  """Parse `-d, --drop <x,y[,xy|x|y]>` into `(x, y, axis)`."""
  parts = [part.strip() for part in str(raw or "").split(",")]
  if len(parts) not in {2, 3}:
    raise ValueError(f"invalid --drop {raw!r}: expected x,y[,xy|x|y]")
  try:
    x = int(parts[0])
    y = int(parts[1])
  except ValueError as exc:
    raise ValueError(f"invalid --drop {raw!r}: x and y must be integers") from exc
  if x < 0 or y < 0:
    raise ValueError(f"invalid --drop {raw!r}: x and y must be non-negative")
  axis = parts[2].lower() if len(parts) == 3 else "xy"
  if axis not in {"xy", "x", "y"}:
    raise ValueError(f"invalid --drop axis {axis!r}: expected xy, x, or y")
  return x, y, axis


def relative_luminance(rgb: Sequence[int]) -> float:
  """Return WCAG 2.x relative luminance for an sRGB triple."""
  channels = []
  for comp in rgb[:3]:
    srgb = max(0.0, min(1.0, float(comp) / 255.0))
    if srgb <= 0.04045:
      channels.append(srgb / 12.92)
    else:
      channels.append(((srgb + 0.055) / 1.055) ** 2.4)
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(fg: Sequence[int], bg: Sequence[int]) -> float:
  """Return WCAG 2.x contrast ratio between two sRGB triples."""
  l1 = relative_luminance(fg)
  l2 = relative_luminance(bg)
  bright, dark = (l1, l2) if l1 >= l2 else (l2, l1)
  return (bright + 0.05) / (dark + 0.05)


def sample_segment_rgb(
  rgba: bytes | bytearray,
  width: int,
  height: int,
  x1: int,
  y1: int,
  x2: int,
  y2: int,
) -> list[tuple[int, int, int]]:
  """Sample RGB pixels along `(x1, y1) -> (x2, y2)` inside image bounds."""
  samples: list[tuple[int, int, int]] = []
  if width <= 0 or height <= 0:
    return samples
  steps = max(abs(x2 - x1), abs(y2 - y1), 1)
  seen: set[tuple[int, int]] = set()
  for step in range(steps + 1):
    t = step / steps
    px = round(x1 + (x2 - x1) * t)
    py = round(y1 + (y2 - y1) * t)
    if 0 <= px < width and 0 <= py < height and (px, py) not in seen:
      seen.add((px, py))
      idx = (py * width + px) * 4
      samples.append((rgba[idx], rgba[idx + 1], rgba[idx + 2]))
  return samples


def select_redline_ink(
  bg_samples: Sequence[tuple[int, int, int]],
  redline: str = "",
) -> dict:
  """Choose redline ink via `([measure].redline or #ff3366) -> #00e5ff -> #111827`."""
  from annotate import css_hex, parse_color

  primary = parse_color((redline or "").strip() or DEFAULT_REDLINE_HEX)
  if not bg_samples:
    return {
      "ink": primary,
      "halo": None,
      "hex": css_hex(primary),
      "min_contrast": 21.0,
      "fallback": False,
    }

  def _min_c(candidate: tuple[int, int, int, int]) -> float:
    return min(contrast_ratio(candidate[:3], sample) for sample in bg_samples)

  primary_c = _min_c(primary)
  if primary_c >= MIN_REDLINE_CONTRAST:
    return {
      "ink": primary,
      "halo": None,
      "hex": css_hex(primary),
      "min_contrast": primary_c,
      "fallback": False,
    }

  cyan_c = _min_c(FALLBACK_CYAN_RGBA)
  if cyan_c >= MIN_REDLINE_CONTRAST:
    chosen = FALLBACK_CYAN_RGBA
    chosen_c = cyan_c
  else:
    chosen = FALLBACK_INK_RGBA
    chosen_c = _min_c(FALLBACK_INK_RGBA)

  halo = HALO_DARK_RGBA if relative_luminance(chosen[:3]) >= 0.35 else HALO_LIGHT_RGBA
  return {
    "ink": chosen,
    "halo": halo,
    "hex": css_hex(chosen),
    "min_contrast": chosen_c,
    "fallback": True,
  }


def cast_drop_rays(
  rgba: bytes | bytearray,
  width: int,
  height: int,
  x: int,
  y: int,
  axis: str = "xy",
  tolerance: int = DEFAULT_TOLERANCE,
) -> dict:
  """Sample seed `RGBA(x, y)` and march `1 px` outward within `tolerance`."""
  if not (0 <= x < width and 0 <= y < height):
    raise RuntimeError(
      f"drop point ({x},{y}) is outside {width}x{height}px image bounds"
    )
  seed_idx = (y * width + x) * 4
  sr, sg, sb, sa = rgba[seed_idx : seed_idx + 4]

  def _within(px: int, py: int) -> bool:
    idx = (py * width + px) * 4
    return (
      max(
        abs(rgba[idx] - sr),
        abs(rgba[idx + 1] - sg),
        abs(rgba[idx + 2] - sb),
        abs(rgba[idx + 3] - sa),
      )
      <= tolerance
    )

  left = x
  while left > 0 and _within(left - 1, y):
    left -= 1
  right = x
  while right + 1 < width and _within(right + 1, y):
    right += 1
  top = y
  while top > 0 and _within(x, top - 1):
    top -= 1
  bottom = y
  while bottom + 1 < height and _within(x, bottom + 1):
    bottom += 1

  return {
    "x": x,
    "y": y,
    "axis": axis,
    "seed": (sr, sg, sb, sa),
    "left": left,
    "right": right,
    "top": top,
    "bottom": bottom,
    "width": right - left + 1,
    "height": bottom - top + 1,
  }


def _draw_1px_segment(
  rgba: bytearray,
  width: int,
  height: int,
  x1: int,
  y1: int,
  x2: int,
  y2: int,
  color: tuple[int, int, int, int],
  expand: int = 0,
) -> None:
  from annotate import blend_pixel

  steps = max(abs(x2 - x1), abs(y2 - y1), 1)
  for step in range(steps + 1):
    t = step / steps
    cx = round(x1 + (x2 - x1) * t)
    cy = round(y1 + (y2 - y1) * t)
    for dy in range(-expand, expand + 1):
      for dx in range(-expand, expand + 1):
        blend_pixel(rgba, width, height, cx + dx, cy + dy, color)


def _draw_endcap(
  rgba: bytearray,
  width: int,
  height: int,
  cx: int,
  cy: int,
  *,
  horizontal_ray: bool,
  inward_step: int,
  color: tuple[int, int, int, int],
  halo: tuple[int, int, int, int] | None = None,
) -> None:
  from annotate import blend_pixel

  # 7 px perpendicular cap with 2 px stroke thickness along the ray axis.
  if horizontal_ray:
    xs = [cx, cx + inward_step]
    ys = list(range(cy - 3, cy + 4))
  else:
    xs = list(range(cx - 3, cx + 4))
    ys = [cy, cy + inward_step]
  if halo is not None:
    for py in range(min(ys) - 1, max(ys) + 2):
      for px in range(min(xs) - 1, max(xs) + 2):
        blend_pixel(rgba, width, height, px, py, halo)
  for py in ys:
    for px in xs:
      blend_pixel(rgba, width, height, px, py, color)


def _draw_clamped_pill(
  rgba: bytearray,
  width: int,
  height: int,
  anchor_x: int,
  anchor_y: int,
  label: str,
  border_color: tuple[int, int, int, int],
  font_path: str | None = None,
) -> None:
  from annotate import COLORS, _rasterize_text, blit_rgba, draw_box, fill_rect

  tw, th, t_rgba = _rasterize_text(label, COLORS["white"], scale=2, font_path=font_path)
  pad_x, pad_y = 6, 4
  box_w = min(width, tw + pad_x * 2)
  box_h = min(height, th + pad_y * 2)
  px = max(2, min(max(2, width - box_w - 2), anchor_x - box_w // 2))
  py = max(2, min(max(2, height - box_h - 2), anchor_y - box_h // 2))
  fill_rect(rgba, width, height, px, py, box_w, box_h, (17, 24, 39, 238))
  draw_box(rgba, width, height, px, py, box_w, box_h, border_color, thickness=1)
  blit_rgba(rgba, width, height, t_rgba, tw, th, px + pad_x, py + pad_y)


def draw_redline_span(
  base_rgba: bytes | bytearray,
  out_rgba: bytearray,
  width: int,
  height: int,
  x1: int,
  y1: int,
  x2: int,
  y2: int,
  *,
  label_text: str | None = None,
  redline: str = "",
  font_path: str | None = None,
) -> dict:
  """Draw a contrast-checked `1 px` redline ray with `7 px` (`2 px` stroke) end-caps."""
  samples = sample_segment_rgb(base_rgba, width, height, x1, y1, x2, y2)
  ink_info = select_redline_ink(samples, redline)
  ink = ink_info["ink"]
  halo = ink_info["halo"]
  horizontal_ray = abs(x2 - x1) >= abs(y2 - y1)

  if halo is not None:
    _draw_1px_segment(out_rgba, width, height, x1, y1, x2, y2, halo, expand=1)
  _draw_1px_segment(out_rgba, width, height, x1, y1, x2, y2, ink, expand=0)

  if horizontal_ray:
    step_start = 1 if x2 >= x1 else -1
    step_end = -step_start if x2 != x1 else 0
  else:
    step_start = 1 if y2 >= y1 else -1
    step_end = -step_start if y2 != y1 else 0

  _draw_endcap(
    out_rgba,
    width,
    height,
    x1,
    y1,
    horizontal_ray=horizontal_ray,
    inward_step=step_start,
    color=ink,
    halo=halo,
  )
  _draw_endcap(
    out_rgba,
    width,
    height,
    x2,
    y2,
    horizontal_ray=horizontal_ray,
    inward_step=step_end,
    color=ink,
    halo=halo,
  )

  if label_text:
    mid_x = round((x1 + x2) / 2)
    mid_y = round((y1 + y2) / 2)
    offset_x = 0 if horizontal_ray else 28
    offset_y = -16 if horizontal_ray else 0
    _draw_clamped_pill(
      out_rgba,
      width,
      height,
      mid_x + offset_x,
      mid_y + offset_y,
      label_text,
      ink,
      font_path=font_path,
    )
  return ink_info


def tint_box_ring(
  rgba: bytearray,
  width: int,
  height: int,
  outer_box: tuple[float, float, float, float],
  inner_box: tuple[float, float, float, float] | None,
  color: tuple[int, int, int, int],
) -> None:
  """Tint pixels inside `outer_box` (`x0, y0, x1, y1`) and outside `inner_box`."""
  from annotate import blend_pixel

  ox0 = max(0, min(width, round(outer_box[0])))
  oy0 = max(0, min(height, round(outer_box[1])))
  ox1 = max(ox0, min(width, round(outer_box[2])))
  oy1 = max(oy0, min(height, round(outer_box[3])))
  if inner_box is None:
    ix0 = iy0 = ix1 = iy1 = -1
  else:
    ix0 = round(inner_box[0])
    iy0 = round(inner_box[1])
    ix1 = round(inner_box[2])
    iy1 = round(inner_box[3])
  for py in range(oy0, oy1):
    in_inner_y = iy0 <= py < iy1
    for px in range(ox0, ox1):
      if in_inner_y and ix0 <= px < ix1:
        continue
      blend_pixel(rgba, width, height, px, py, color)


_LIVE_MEASURE_PROBE_JS = """
({ selector, toSelector }) => {
  const el = document.querySelector(selector);
  if (!el) return { error: `Selector not found: ${selector}` };
  const rect = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  if ((rect.width <= 0 && rect.height <= 0) || cs.display === 'none' || cs.visibility === 'hidden') {
    return { error: `Selector is hidden or zero-sized: ${selector}` };
  }
  const num = (v) => {
    const parsed = parseFloat(v);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const margin = {
    top: num(cs.marginTop),
    right: num(cs.marginRight),
    bottom: num(cs.marginBottom),
    left: num(cs.marginLeft),
  };
  const border = {
    top: num(cs.borderTopWidth),
    right: num(cs.borderRightWidth),
    bottom: num(cs.borderBottomWidth),
    left: num(cs.borderLeftWidth),
  };
  const padding = {
    top: num(cs.paddingTop),
    right: num(cs.paddingRight),
    bottom: num(cs.paddingBottom),
    left: num(cs.paddingLeft),
  };
  const borderBox = [rect.left, rect.top, rect.right, rect.bottom];
  const marginBox = [
    rect.left - margin.left,
    rect.top - margin.top,
    rect.right + margin.right,
    rect.bottom + margin.bottom,
  ];
  const paddingBox = [
    rect.left + border.left,
    rect.top + border.top,
    rect.right - border.right,
    rect.bottom - border.bottom,
  ];
  const contentBox = [
    paddingBox[0] + padding.left,
    paddingBox[1] + padding.top,
    paddingBox[2] - padding.right,
    paddingBox[3] - padding.bottom,
  ];

  let parentInner = [0, 0, window.innerWidth, window.innerHeight];
  const parent = el.parentElement;
  if (parent && parent !== document.body && parent !== document.documentElement) {
    const pr = parent.getBoundingClientRect();
    const pcs = getComputedStyle(parent);
    parentInner = [
      pr.left + num(pcs.borderLeftWidth) + num(pcs.paddingLeft),
      pr.top + num(pcs.borderTopWidth) + num(pcs.paddingTop),
      pr.right - num(pcs.borderRightWidth) - num(pcs.paddingRight),
      pr.bottom - num(pcs.borderBottomWidth) - num(pcs.paddingBottom),
    ];
  }

  let toBox = null;
  if (toSelector) {
    const target = document.querySelector(toSelector);
    if (!target) return { error: `--to selector not found: ${toSelector}` };
    const tr = target.getBoundingClientRect();
    toBox = [tr.left, tr.top, tr.right, tr.bottom];
  }

  let pairedIconHeight = null;
  const directText = Array.from(el.childNodes)
    .filter((n) => n.nodeType === 3)
    .map((n) => (n.nodeValue || '').trim())
    .join(' ')
    .trim();
  const childSvg = el.querySelector(':scope > svg');
  if (childSvg && (directText || (el.innerText || '').trim())) {
    pairedIconHeight = childSvg.getBoundingClientRect().height;
  }

  const svgs = el.querySelectorAll(':scope > svg');
  const isInteractive = el.matches('a[href], button, input, select, textarea, summary, [role="button"]');
  const iconOnly = isInteractive && svgs.length > 0 && !(el.innerText || '').trim();

  return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    boxes: { margin: marginBox, border: borderBox, padding: paddingBox, content: contentBox },
    parent_inner: parentInner,
    to_box: toBox,
    element: {
      selector,
      tag: el.tagName.toLowerCase(),
      role: (el.getAttribute('role') || '').toLowerCase(),
      classes: Array.from(el.classList || []),
      text: directText,
      icon_only: iconOnly,
      paired_icon_height: pairedIconHeight,
      in_card_or_dialog: Boolean(el.closest('.card, dialog, [role="dialog"], [role="alertdialog"]')),
      bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      scroll_width: el.scrollWidth,
      client_width: el.clientWidth,
      scroll_height: el.scrollHeight,
      client_height: el.clientHeight,
      values: {
        'font-size': cs.fontSize,
        'font-weight': cs.fontWeight,
        'font-family': cs.fontFamily,
        'font-style': cs.fontStyle,
        'line-height': cs.lineHeight,
        'letter-spacing': cs.letterSpacing,
        'text-transform': cs.textTransform,
        'text-align': cs.textAlign,
        'padding-top': cs.paddingTop,
        'padding-right': cs.paddingRight,
        'padding-bottom': cs.paddingBottom,
        'padding-left': cs.paddingLeft,
        'padding-inline-start': cs.paddingInlineStart,
        'margin-top': cs.marginTop,
        'margin-right': cs.marginRight,
        'margin-bottom': cs.marginBottom,
        'margin-left': cs.marginLeft,
        'gap': cs.gap,
        'row-gap': cs.rowGap,
        'column-gap': cs.columnGap,
        'border-radius': cs.borderTopLeftRadius,
        'overflow-x': cs.overflowX,
        'overflow-y': cs.overflowY,
        'text-overflow': cs.textOverflow,
      },
    },
  };
}
"""


def check_severities(
  project: Path | None = None,
  config_path: Path | None = None,
) -> dict[str, str]:
  from nuu import check_severities as nuu_check_severities

  return nuu_check_severities(project, config_path)


def _classify_check_finding(item: str) -> tuple[str, str]:
  match = RULE_PREFIX_RE.match(item.strip())
  if match:
    return match.group(1), match.group(2)
  return "SPC001", item.strip()


def _guide_segments_for_boxes(
  border_box: Sequence[float],
  parent_inner: Sequence[float],
  to_target: str | None,
  to_box: Sequence[float] | None,
) -> list[tuple[str, float, int, int, int, int]]:
  bx0, by0, bx1, by1 = [float(v) for v in border_box]
  mid_x = round((bx0 + bx1) / 2.0)
  mid_y = round((by0 + by1) / 2.0)
  segments: list[tuple[str, float, int, int, int, int]] = []

  if to_target and POINT_RE.match(to_target.strip()):
    tx, ty = point(to_target.strip())
    anchor_x = bx1 if tx >= bx1 else max(bx0, tx)
    anchor_y = by1 if ty >= by1 else max(by0, ty)
    dx = abs(tx - anchor_x)
    dy = abs(ty - anchor_y)
    if dx >= 0.5:
      segments.append(
        ("to-x", round(dx, 2), round(anchor_x), round(ty), round(tx), round(ty))
      )
    if dy >= 0.5:
      segments.append(
        ("to-y", round(dy, 2), round(tx), round(anchor_y), round(tx), round(ty))
      )
    return segments

  if to_box is not None:
    tx0, ty0, tx1, ty1 = [float(v) for v in to_box]
    if tx0 >= bx1:
      gap_x = tx0 - bx1
      if gap_x >= 0.5:
        gy = (
          round((max(by0, ty0) + min(by1, ty1)) / 2.0)
          if min(by1, ty1) > max(by0, ty0)
          else mid_y
        )
        segments.append(("pair-x", round(gap_x, 2), round(bx1), gy, round(tx0), gy))
    elif bx0 >= tx1:
      gap_x = bx0 - tx1
      if gap_x >= 0.5:
        gy = (
          round((max(by0, ty0) + min(by1, ty1)) / 2.0)
          if min(by1, ty1) > max(by0, ty0)
          else mid_y
        )
        segments.append(("pair-x", round(gap_x, 2), round(tx1), gy, round(bx0), gy))

    if ty0 >= by1:
      gap_y = ty0 - by1
      if gap_y >= 0.5:
        gx = (
          round((max(bx0, tx0) + min(bx1, tx1)) / 2.0)
          if min(bx1, tx1) > max(bx0, tx0)
          else mid_x
        )
        segments.append(("pair-y", round(gap_y, 2), gx, round(by1), gx, round(ty0)))
    elif by0 >= ty1:
      gap_y = by0 - ty1
      if gap_y >= 0.5:
        gx = (
          round((max(bx0, tx0) + min(bx1, tx1)) / 2.0)
          if min(bx1, tx1) > max(bx0, tx0)
          else mid_x
        )
        segments.append(("pair-y", round(gap_y, 2), gx, round(ty1), gx, round(by0)))
    return segments

  px0, py0, px1, py1 = [float(v) for v in parent_inner]
  if by0 - py0 >= 0.5:
    segments.append(
      ("parent-top", round(by0 - py0, 2), mid_x, round(py0), mid_x, round(by0))
    )
  if px1 - bx1 >= 0.5:
    segments.append(
      ("parent-right", round(px1 - bx1, 2), round(bx1), mid_y, round(px1), mid_y)
    )
  if py1 - by1 >= 0.5:
    segments.append(
      ("parent-bottom", round(py1 - by1, 2), mid_x, round(by1), mid_x, round(py1))
    )
  if bx0 - px0 >= 0.5:
    segments.append(
      ("parent-left", round(bx0 - px0, 2), round(px0), mid_y, round(bx0), mid_y)
    )
  return segments


def run_measure_cli(args) -> int:
  """Execute `nuu measure` across `--from/--to`, live `-u`, and PNG `-i` modes."""
  from annotate import decode_png, encode_png, resolve_project_typography
  from nuu import (
    chromium_launch_kwargs,
    enrich_finding_rule,
    load_nuu_config,
    parse_viewport_spec,
    settle_page,
    validated_url,
  )

  cfg_info = load_nuu_config(None, args.config)
  measure_cfg = cfg_info["config"]["measure"]
  severities = cfg_info["severities"]
  scale_val = int(
    args.scale if args.scale is not None else measure_cfg.get("scale", DEFAULT_SCALE)
  )
  tolerance = int(
    args.tolerance
    if args.tolerance is not None
    else measure_cfg.get("tolerance", DEFAULT_TOLERANCE)
  )
  if not (0 <= tolerance <= 255):
    raise ValueError("--tolerance must be between 0 and 255")
  redline_cfg = str(measure_cfg.get("redline", "") or "")

  if args.url and args.input:
    raise ValueError("measure accepts either -u/--url or -i/--input, not both")

  # Pure coordinate ruler mode (`--from x,y --to x,y` without `-u` or `-i`).
  if not args.url and not args.input:
    if not (args.from_point and args.to_target):
      raise ValueError(
        "measure requires (-u/--url | -i/--input) or both --from <x,y> and --to <x,y>"
      )
    try:
      start_pt = point(args.from_point)
      end_pt = point(args.to_target)
    except argparse.ArgumentTypeError as exc:
      raise ValueError(str(exc)) from exc
    gap = ruler(start_pt, end_pt, scale_val)
    status = "pass" if gap["on_scale"] else "findings"
    code = 0 if gap["on_scale"] else 1
    print(
      f"# Measurement\n\n"
      f"- Status: `{status}` (exit {code})\n"
      f"- Span: `{gap['dx']:g} x {gap['dy']:g} px` (distance `{gap['distance']:g} px`)\n"
      f"- X on `{scale_val}px` scale: `{gap['axes']['x']}`\n"
      f"- Y on `{scale_val}px` scale: `{gap['axes']['y']}`\n",
      end="",
    )
    return code

  if args.output is None:
    raise ValueError("measure with -u/--url or -i/--input requires -o/--output <png>")
  if not args.selector and not args.drop:
    raise ValueError("measure requires -s/--selector or -d/--drop")
  if args.input and args.selector:
    raise ValueError("measure -s/--selector requires -u/--url")
  if args.from_point:
    raise ValueError("--from cannot be combined with -u/--url or -i/--input")

  drop_specs = [parse_drop_spec(spec) for spec in (args.drop or [])]
  typo = resolve_project_typography(cfg_info["dir"], args.config)
  raster_font = typo.get("raster_font_path")
  sheet_tokens = parse_sheet(cfg_info["stylesheet_path"].read_text(encoding="utf-8"))

  spans_rows: list[str] = []
  structured_findings: list[dict] = []

  if args.input:
    in_path = Path(args.input)
    if not in_path.is_file():
      raise FileNotFoundError(f"Input PNG not found: {in_path}")
    if in_path.resolve() == Path(args.output).resolve():
      raise RuntimeError("measure refuses to overwrite the input PNG (-o must differ)")
    width, height, base_rgba = decode_png(in_path.read_bytes())
    out_rgba = bytearray(base_rgba)
    source_label = str(in_path)
  else:
    validated_url(args.url)
    vp = parse_viewport_spec(args.viewport, cfg_info["config"]["audit"]["viewports"])
    vw, vh = int(vp["width"]), int(vp["height"])
    try:
      from playwright.sync_api import sync_playwright
    except ImportError as exc:
      raise RuntimeError(f"RUN102: playwright is not installed: {exc}") from exc

    launch_kwargs = chromium_launch_kwargs(args.executable)
    to_selector = None
    if args.to_target and not POINT_RE.match(args.to_target.strip()):
      to_selector = args.to_target.strip()

    with sync_playwright() as pw:
      try:
        browser = pw.chromium.launch(**launch_kwargs)
      except Exception as exc:
        raise RuntimeError(f"RUN102: Failed to launch Chromium: {exc}") from exc
      try:
        ctx = browser.new_context(
          viewport={"width": vw, "height": vh},
          color_scheme="dark",
          is_mobile=bool(vp.get("mobile")),
          has_touch=bool(vp.get("touch")),
        )
        page = ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
        settle_page(page, 20)
        probe = (
          page.evaluate(
            _LIVE_MEASURE_PROBE_JS,
            {"selector": args.selector, "toSelector": to_selector},
          )
          if args.selector
          else None
        )
        png_bytes = page.screenshot(type="png")
        ctx.close()
      finally:
        browser.close()

    if probe and probe.get("error"):
      raise RuntimeError(str(probe["error"]))

    width, height, base_rgba = decode_png(png_bytes)
    out_rgba = bytearray(base_rgba)
    source_label = f"{args.url} ({vw}x{vh} dark)"

    if probe:
      boxes = probe["boxes"]
      tint_box_ring(
        out_rgba,
        width,
        height,
        tuple(boxes["margin"]),
        tuple(boxes["border"]),
        DEVTOOLS_TINTS["margin"],
      )
      tint_box_ring(
        out_rgba,
        width,
        height,
        tuple(boxes["border"]),
        tuple(boxes["padding"]),
        DEVTOOLS_TINTS["border"],
      )
      tint_box_ring(
        out_rgba,
        width,
        height,
        tuple(boxes["padding"]),
        tuple(boxes["content"]),
        DEVTOOLS_TINTS["padding"],
      )
      tint_box_ring(
        out_rgba,
        width,
        height,
        tuple(boxes["content"]),
        None,
        DEVTOOLS_TINTS["content"],
      )

      elem = probe["element"]
      bx0, by0, bx1, by1 = boxes["border"]
      bw = round(bx1 - bx0, 2)
      bh = round(by1 - by0, 2)
      spans_rows.append(
        f"| `selector` | `{args.selector}` | `{bw:g} x {bh:g} px` | "
        f"`{on_scale(bw, scale_val) and on_scale(bh, scale_val)}` | `devtools-tint` |"
      )

      for raw_f in check(
        {"width": vw, "elements": [elem]}, sheet_tokens, scale=scale_val
      ):
        rule_id, msg = _classify_check_finding(raw_f)
        structured_findings.append(
          enrich_finding_rule(
            {"rule": rule_id, "location": source_label, "evidence": msg},
            severities,
          )
        )

      guides = _guide_segments_for_boxes(
        boxes["border"], probe["parent_inner"], args.to_target, probe.get("to_box")
      )
      for guide_kind, dist_px, gx1, gy1, gx2, gy2 in guides:
        ink_info = draw_redline_span(
          base_rgba,
          out_rgba,
          width,
          height,
          gx1,
          gy1,
          gx2,
          gy2,
          label_text=f"{dist_px:g}px",
          redline=redline_cfg,
          font_path=raster_font,
        )
        axis_ok = on_scale(dist_px, scale_val)
        spans_rows.append(
          f"| `{guide_kind}` | `{args.selector}` | `{dist_px:g} px` | "
          f"`{axis_ok}` | `{ink_info['hex']}` |"
        )
        if args.to_target and not axis_ok:
          structured_findings.append(
            enrich_finding_rule(
              {
                "rule": "SPC001",
                "location": source_label,
                "evidence": (
                  f"{args.selector} -> {args.to_target} ({guide_kind}): "
                  f"{dist_px:g}px is off the {scale_val}px scale"
                ),
              },
              severities,
            )
          )

  for dx, dy, axis in drop_specs:
    ray = cast_drop_rays(
      base_rgba, width, height, dx, dy, axis=axis, tolerance=tolerance
    )
    ink_used = DEFAULT_REDLINE_HEX
    if "x" in axis:
      lbl_x = (
        f"{ray['width']} x {ray['height']} px" if axis == "xy" else f"{ray['width']} px"
      )
      ink_x = draw_redline_span(
        base_rgba,
        out_rgba,
        width,
        height,
        ray["left"],
        dy,
        ray["right"],
        dy,
        label_text=lbl_x,
        redline=redline_cfg,
        font_path=raster_font,
      )
      ink_used = ink_x["hex"]
      if not on_scale(ray["width"], scale_val):
        structured_findings.append(
          enrich_finding_rule(
            {
              "rule": "SPC001",
              "location": source_label,
              "evidence": (
                f"drop({dx},{dy},{axis}): width {ray['width']}px is off the {scale_val}px scale"
              ),
            },
            severities,
          )
        )
    if "y" in axis:
      lbl_y = None if axis == "xy" else f"{ray['height']} px"
      ink_y = draw_redline_span(
        base_rgba,
        out_rgba,
        width,
        height,
        dx,
        ray["top"],
        dx,
        ray["bottom"],
        label_text=lbl_y,
        redline=redline_cfg,
        font_path=raster_font,
      )
      ink_used = ink_y["hex"]
      if not on_scale(ray["height"], scale_val):
        structured_findings.append(
          enrich_finding_rule(
            {
              "rule": "SPC001",
              "location": source_label,
              "evidence": (
                f"drop({dx},{dy},{axis}): height {ray['height']}px is off the {scale_val}px scale"
              ),
            },
            severities,
          )
        )
    span_desc = (
      f"{ray['width']} x {ray['height']} px"
      if axis == "xy"
      else (f"{ray['width']} px" if axis == "x" else f"{ray['height']} px")
    )
    ok_flag = (on_scale(ray["width"], scale_val) if "x" in axis else True) and (
      on_scale(ray["height"], scale_val) if "y" in axis else True
    )
    spans_rows.append(
      f"| `drop` | `{dx},{dy},{axis}` | `{span_desc}` | `{ok_flag}` | `{ink_used}` |"
    )

  out_path = Path(args.output)
  out_path.parent.mkdir(parents=True, exist_ok=True)
  out_path.write_bytes(encode_png(width, height, out_rgba))

  has_blocking = any(f.get("severity") == "error" for f in structured_findings)
  exit_code = 1 if has_blocking else 0
  status_str = "findings" if exit_code else "pass"

  md_lines = [
    "# Measurement",
    "",
    f"- Status: `{status_str}` (exit {exit_code})",
    f"- Source: `{source_label}`",
    f"- Scale: `{scale_val}px`",
    f"- Output: `{out_path}`",
    "",
    "## Spans",
    "| Kind | Target | Span | On Scale | Ink |",
    "| --- | --- | --- | --- | --- |",
    *(spans_rows or ["| `none` | `-` | `-` | `True` | `-` |"]),
    "",
    "## Findings",
  ]
  if structured_findings:
    for idx, f_item in enumerate(structured_findings, 1):
      md_lines.append(
        f"{idx}. `{f_item['rule']}` ({f_item['severity']}) `{f_item['location']}`: {f_item['evidence']}"
      )
  else:
    md_lines.append("- None")
  print("\n".join(md_lines) + "\n", end="")
  return exit_code
