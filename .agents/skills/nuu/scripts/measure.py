"""Measure a rendered page against the Nemo UI/UX sheet.

Two commands:

- `check REPORT`: read a JSON report of computed values and name every value that
  leaves the sheet. A value passes when it sits on the scale, when it is a size
  from the type ramp on a type property, or when the sheet names it as a layout
  size, such as the 10 px radius.
- `ruler --from X,Y --to X,Y`: read the gap between two guides on the page, the
  way a screen ruler does, and say whether each axis sits on the scale.

The report shape is one object per width:

    {"width": 1280,
     "elements": [{"selector": ".row", "values": {"height": "40px", "gap": "16px"}}]}

`references/measure.md` holds the browser snippet that writes it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

DEFAULT_SHEET = Path(__file__).resolve().parent.parent / "references" / "style.css"
DEFAULT_SCALE = 4
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
CUSTOM_PROPERTY = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")
TYPE_PROPERTIES = ("font-size",)
SPACING_PROPERTIES = ("padding", "margin", "gap", "row-gap", "column-gap")
LAYOUT_PROPERTIES = ("border-radius",)
RADIUS_SIZES = {4.0, 6.0, 10.0, 9999.0}
LIST_INDENT = "2ch"
LIST_INDENT_PX_TOLERANCE = 1.0
LIST_WRAP_PX_TOLERANCE = 1.0


def parse_sheet(text: str) -> dict[str, str]:
  """Return every custom property in a stylesheet, keyed by its name without the dashes."""
  return {name: value.strip() for name, value in CUSTOM_PROPERTY.findall(text)}


def named_sizes(tokens: dict[str, str], *, ramp: bool) -> set[float]:
  """Return the px sizes the sheet names, from its type ramp or from its layout tokens.

  A type size names a font size, and never excuses a padding. A layout token, such
  as `--radius: 10px`, names a size that a component may take.
  """
  sizes = set()
  for name, value in tokens.items():
    match = PX.match(value)
    if not match:
      continue
    is_type = name.startswith("size-") or name == "eyebrow"
    if is_type is ramp:
      sizes.add(float(match.group(1)))
  return sizes


def on_scale(value: float, scale: float) -> bool:
  """True when a value is a whole multiple of the scale."""
  return math.isclose(value / scale, round(value / scale), abs_tol=1e-9)


def off_scale(name: str, value: str, tokens: dict[str, str], scale: float) -> bool:
  """True when a px value is neither zero, on the scale, nor a size the sheet names for it.

  A spacing property reads the scale alone. A type size names a font size, and never
  excuses a padding. A layout property such as border-radius may take a size the sheet names.
  Fluid box dimensions (width, height, line-height, outline-width) are not scale tokens.
  """
  match = PX.match(value.strip())
  if not match:
    return False
  number = float(match.group(1))
  if number == 0 or on_scale(number, scale):
    return False
  if name.startswith(SPACING_PROPERTIES):
    return True
  if name in TYPE_PROPERTIES:
    return number not in named_sizes(tokens, ramp=True)
  if name.startswith(LAYOUT_PROPERTIES):
    return number not in (named_sizes(tokens, ramp=False) | RADIUS_SIZES)
  return False


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
  data = element.get("list_indent", element.get("listIndent"))
  if data is None:
    return None
  if not isinstance(data, dict):
    return {}, ["list indentation metrics are malformed"], False
  errors = []
  indent = str(data.get("indent") or "").strip().lower()
  ch_px = _numeric_value(data.get("ch_px", data.get("chPx")))
  padding = _numeric_value(
    data.get("padding_inline_start", data.get("paddingInlineStart"))
  )
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
      raw_starts = item.get("line_starts", item.get("lineStarts"))
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


def _verified_list_padding(element: dict, name: str, value: str, status) -> bool:
  if status is None or name not in {
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
  if name not in {"padding-inline-start", physical_start}:
    return False
  observed = _numeric_value(value)
  expected = _numeric_value(
    data.get("padding_inline_start", data.get("paddingInlineStart"))
  )
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
  if tag in COMPACT_LINE_HEIGHT_TAGS or bool(
    element.get("icon_only", element.get("iconOnly"))
  ):
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
      f"NUM212 {selector}: line-height {values['line-height']} ({ratio:.2f}x) "
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
    if em_ratio < -0.02 - 1e-4 or em_ratio > -0.005 + 1e-4:
      return (
        f"NUM213 {selector}: heading letter-spacing {values['letter-spacing']} "
        f"({em_ratio:.4f}em) is outside -0.005em to -0.02em"
      )
    return None
  if abs(em_ratio) > 1e-4:
    return f"NUM213 {selector}: non-heading letter-spacing {values['letter-spacing']} must be 0"
  return None


def _element_line_count(element: dict) -> int | None:
  explicit = element.get("line_count", element.get("lineCount"))
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


def _text_alignment_finding(element: dict) -> str | None:
  values = element.get("values") or {}
  align = str(values.get("text-align") or "").strip().lower()
  if not align:
    return None
  selector = str(element.get("selector") or "?")
  if align == "justify":
    return f"NUM214 {selector}: text-align justify creates uneven word gaps"
  if align == "center":
    tag = str(element.get("tag") or "").strip().lower()
    if tag in COMPACT_LINE_HEIGHT_TAGS or tag in {"th", "td"}:
      return None
    lines = _element_line_count(element)
    if lines is not None and lines > 3:
      return (
        f"NUM214 {selector}: centered prose spans {lines} lines (at most 3 allowed)"
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
  raw_max = str(values.get("max-width") or "").strip().lower()
  if raw_max.endswith("ch"):
    max_ch = _numeric_value(raw_max[:-2])
    if max_ch is not None and (max_ch < 49.5 or max_ch > 75.5):
      return f"NUM215 {selector}: max-width {values['max-width']} is outside 50 to 75ch"
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
  standalone = is_prose_container or not bool(element.get("in_card_or_dialog"))
  if ch_width > 75.5 or (standalone and ch_width < 49.5):
    return f"NUM215 {selector}: prose width {ch_width:.1f}ch is outside 50 to 75ch"
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
  explicit = element.get("card_font_sizes", element.get("cardFontSizes"))
  if explicit is None and not _is_card_or_dialog(element):
    return None
  selector = str(element.get("selector") or "?")
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
  if len(sizes) > 3:
    ordered = ", ".join(f"{val:g}px" for val in sorted(sizes))
    return (
      f"NUM216 {selector}: card or dialog mixes {len(sizes)} font sizes "
      f"({ordered}, at most 3 allowed)"
    )
  return None


def _proximity_spacing_finding(element: dict) -> str | None:
  prox = element.get("group_proximity", element.get("groupProximity"))
  inter_px: float | None = None
  intra_px: float | None = None
  if isinstance(prox, dict):
    raw_inter = prox.get("inter_px", prox.get("interPx"))
    raw_intra = prox.get("intra_px", prox.get("intraPx"))
    if isinstance(raw_inter, (int, float)) and isinstance(raw_intra, (int, float)):
      inter_px, intra_px = float(raw_inter), float(raw_intra)
  else:
    raw_inter = element.get("inter_group_gap", element.get("interGroupGap"))
    raw_intra = element.get("intra_group_gap", element.get("intraGroupGap"))
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
  if inter_px is None or intra_px is None or intra_px <= 0:
    return None
  selector = str(element.get("selector") or "?")
  if inter_px + 1e-6 < 1.5 * intra_px:
    ratio = inter_px / intra_px
    return (
      f"NUM217 {selector}: inter-group gap {inter_px:g}px is {ratio:.2f}x "
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


def _core_principles_findings(element: dict, tokens: dict[str, str]) -> list[str]:
  out: list[str] = []
  selector = str(element.get("selector") or "?")
  classes = element.get("classes") or []
  values = element.get("values") or {}
  insets = element.get("card_inline_insets", element.get("cardInlineInsets"))
  if isinstance(insets, list):
    nums = [float(x) for x in insets if isinstance(x, (int, float))]
    if len(nums) >= 2 and max(nums) - min(nums) > 2.0:
      joined = ", ".join(f"{val:g}px" for val in nums)
      out.append(
        f"NUM218 {selector}: stacked card children have ragged inline-start insets ({joined})"
      )
  raw_ff = str(values.get("font-family") or "").strip()
  if raw_ff:
    families = [
      part.strip().strip("\"'").lower() for part in raw_ff.split(",") if part.strip()
    ]
    allowed = _approved_font_families(tokens)
    if families and not any(fam in allowed for fam in families):
      out.append(
        f"NUM218 {selector}: font-family {raw_ff} is outside the house font tokens"
      )
  transform = str(values.get("text-transform") or "").strip().lower()
  if transform == "uppercase" and not (
    "eyebrow" in classes or bool(re.search(r"\beyebrow\b", selector, re.IGNORECASE))
  ):
    out.append(f"NUM218 {selector}: text-transform uppercase is reserved for .eyebrow")
  return out


def check(
  report: dict, tokens: dict[str, str], scale: float = DEFAULT_SCALE
) -> list[str]:
  """Return off-scale values and measured list-indent and typography findings."""
  findings = []
  report_width = float(report.get("width") or 0)
  elements = report.get("elements", [])
  for element in elements:
    selector = element.get("selector", "?")
    list_status = _list_indent_status(element)
    if list_status and list_status[1]:
      findings.append(f"NUM211 {selector}: {'; '.join(list_status[1])}")
    for extra in (
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
    for name, value in element.get("values", {}).items():
      if _verified_list_padding(element, name, str(value), list_status):
        continue
      if off_scale(name, str(value), tokens, scale):
        findings.append(f"{selector}: {name} is {value}, off the {scale:g} px scale")
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
  """Return whether an element is a header or toolbar bar exempt from NUM205."""
  if bool(element.get("toolbar_exempt", element.get("toolbarExempt"))):
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
  """Return whitespace and layout-density findings (NUM205, NUM206, NUM207, NUM208)."""
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
      raw_ratio = element.get("ink_ratio", element.get("inkRatio"))
      if raw_ratio is None:
        raw_ink = element.get("ink_width", element.get("inkWidth"))
        if raw_ink is not None and width > 0:
          raw_ratio = float(raw_ink) / width
      if raw_ratio is None:
        raw_ratio = None
      min_ratio = MAIN_MIN_INK_RATIO if is_main_landmark(element) else MIN_INK_RATIO
      if raw_ratio is not None and float(raw_ratio) < min_ratio:
        ratio = float(raw_ratio)
        findings.append(
          {
            "rule": "NUM205",
            "selector": selector,
            "evidence": (
              f"{selector}: ink ratio is {ratio:.2f} (< {min_ratio:.2f})"
              f" across {width:g} px"
            ),
            **b_extra,
          }
        )
    gap = float(
      element.get(
        "max_sibling_gap", element.get("maxSiblingGap", element.get("sibling_gap", 0))
      )
      or 0
    )
    if gap > MAX_SIBLING_GAP and (width <= 0 or gap > 0.4 * width):
      findings.append(
        {
          "rule": "NUM206",
          "selector": selector,
          "evidence": f"{selector}: sibling gap is {gap:g} px (> {MAX_SIBLING_GAP:g} px)",
          **b_extra,
        }
      )
    tag = str(element.get("tag") or "").lower()
    input_type = str(
      element.get("input_type") or element.get("inputType") or "text"
    ).lower()
    slack = float(element.get("row_slack", element.get("rowSlack", 0)) or 0)
    if (
      tag == "select" or (tag == "input" and input_type not in STRETCH_EXEMPT_INPUTS)
    ) and slack > MAX_ROW_SLACK:
      findings.append(
        {
          "rule": "NUM207",
          "selector": selector,
          "evidence": (f"{selector}: input ends {slack:g} px before its row edge"),
          **b_extra,
        }
      )
    icon_only = bool(element.get("icon_only", element.get("iconOnly")))
    if icon_only and width > 0 and height > 0 and abs(width - height) > 1.0:
      findings.append(
        {
          "rule": "NUM208",
          "selector": selector,
          "evidence": (
            f"{selector}: icon-only button is {width:g}x{height:g} px (not symmetric)"
          ),
          **b_extra,
        }
      )
    values = element.get("values") or {}
    overflow_x = str(values.get("overflow-x") or values.get("overflow") or "").lower()
    overflow_y = str(values.get("overflow-y") or values.get("overflow") or "").lower()
    scroll_w = float(element.get("scroll_width", element.get("scrollWidth", 0)) or 0)
    client_w = float(element.get("client_width", element.get("clientWidth", 0)) or 0)
    scroll_h = float(element.get("scroll_height", element.get("scrollHeight", 0)) or 0)
    client_h = float(element.get("client_height", element.get("clientHeight", 0)) or 0)
    is_ellipsis = str(values.get("text-overflow") or "").lower() == "ellipsis"
    clipped = bool(element.get("clipped_content", element.get("clippedContent")))
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
          "rule": "NUM209",
          "selector": selector,
          "evidence": f"{selector}: non-scrolling container clips child content",
          **b_extra,
        }
      )
    overlap_target = element.get("sibling_overlap", element.get("siblingOverlap"))
    if overlap_target:
      findings.append(
        {
          "rule": "NUM210",
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
          "rule": "NUM210",
          "selector": first,
          "evidence": f"{first}: overlaps sibling {second}",
        }
      )
  return findings


def ruler(
  start: tuple[float, float], end: tuple[float, float], scale: float = DEFAULT_SCALE
) -> dict:
  """Return the gap between two guides and whether each axis sits on the scale."""
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
  """Read one guide as `x,y`."""
  try:
    x, y = (float(part) for part in text.split(","))
  except ValueError as error:
    raise argparse.ArgumentTypeError(f"a guide reads as x,y, not {text!r}") from error
  return x, y


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("-s", "--sheet", type=Path, default=DEFAULT_SHEET)
  parser.add_argument("-k", "--scale", type=float, default=DEFAULT_SCALE)
  commands = parser.add_subparsers(dest="command", required=True)

  check_command = commands.add_parser(
    "check", help="Check a JSON report against the sheet"
  )
  check_command.add_argument("report", type=Path)
  check_command.add_argument("-s", "--sheet", dest="sub_sheet", type=Path, default=None)
  check_command.add_argument(
    "-k", "--scale", dest="sub_scale", type=float, default=None
  )
  check_command.add_argument(
    "-d", "--density", action="store_true", help="Include whitespace and density checks"
  )
  check_command.add_argument(
    "-C", "--clipping", action="store_true", help="Include container clipping checks"
  )
  check_command.add_argument(
    "-O", "--overlaps", action="store_true", help="Include sibling overlap checks"
  )

  density_command = commands.add_parser(
    "density",
    help="Check row/card ink ratio, sibling gaps, and form inputs that leave row space unused",
  )
  density_command.add_argument("report", type=Path)
  density_command.add_argument(
    "-C", "--clipping", action="store_true", help="Include container clipping checks"
  )
  density_command.add_argument(
    "-O", "--overlaps", action="store_true", help="Include sibling overlap checks"
  )

  ruler_command = commands.add_parser(
    "ruler", help="Measure the gap between two guides"
  )
  ruler_command.add_argument("-f", "--from", dest="start", type=point, required=True)
  ruler_command.add_argument("-t", "--to", dest="end", type=point, required=True)
  ruler_command.add_argument("-s", "--sheet", dest="sub_sheet", type=Path, default=None)
  ruler_command.add_argument(
    "-k", "--scale", dest="sub_scale", type=float, default=None
  )

  arguments = parser.parse_args(argv)
  if arguments.command == "density":
    report = json.loads(arguments.report.read_text(encoding="utf-8"))
    density_rows = check_density(report)
    for row in density_rows:
      print(f"{report.get('width')} px {row['rule']} {row['evidence']}")
    if density_rows:
      return 1
    print(f"{report.get('width')} px: density checks passed")
    return 0

  sheet_path = arguments.sub_sheet or arguments.sheet
  scale_val = (
    arguments.sub_scale if arguments.sub_scale is not None else arguments.scale
  )
  tokens = parse_sheet(sheet_path.read_text(encoding="utf-8"))

  if arguments.command == "ruler":
    gap = ruler(arguments.start, arguments.end, scale_val)
    print(f"gap {gap['dx']:g} x {gap['dy']:g} px, distance {gap['distance']:g} px")
    for axis, fits in gap["axes"].items():
      print(f"{axis}: {'on' if fits else 'off'} the {scale_val:g} px scale")
    return 0 if gap["on_scale"] else 1

  report = json.loads(arguments.report.read_text(encoding="utf-8"))
  findings = check(report, tokens, scale_val)
  if (
    getattr(arguments, "density", False)
    or getattr(arguments, "clipping", False)
    or getattr(arguments, "overlaps", False)
  ):
    findings.extend(f"{row['rule']} {row['evidence']}" for row in check_density(report))
  for finding in findings:
    print(f"{report.get('width')} px {finding}")
  if findings:
    return 1
  print(f"{report.get('width')} px: every value sits on the sheet")
  return 0


if __name__ == "__main__":
  sys.exit(main())
