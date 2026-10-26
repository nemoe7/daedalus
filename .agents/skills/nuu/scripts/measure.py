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
TYPE_PROPERTIES = ("font-size", "letter-spacing")
SPACING_PROPERTIES = ("padding", "margin", "gap", "row-gap", "column-gap")
LAYOUT_PROPERTIES = ("border-radius",)
RADIUS_SIZES = {4.0, 6.0, 10.0, 9999.0}


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


def check(
  report: dict, tokens: dict[str, str], scale: float = DEFAULT_SCALE
) -> list[str]:
  """Return one finding per computed value that leaves the sheet."""
  findings = []
  for element in report.get("elements", []):
    selector = element.get("selector", "?")
    for name, value in element.get("values", {}).items():
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
    is_ellipsis = (
      str(values.get("text-overflow") or "").lower() == "ellipsis"
      or str(values.get("white-space") or "").lower() == "nowrap"
    )
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
  for pair in report.get("overlaps") or []:
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
