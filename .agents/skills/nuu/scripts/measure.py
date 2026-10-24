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
DEFAULT_SCALE = 8
MIN_INK_RATIO = 0.75
MAX_SIBLING_GAP = 320.0
MAX_CONTROL_WIDTH = 480.0
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
TYPE_PROPERTIES = ("font-size", "line-height", "letter-spacing")


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


SPACING_PROPERTIES = ("padding", "margin", "gap", "row-gap", "column-gap")


def off_scale(name: str, value: str, tokens: dict[str, str], scale: float) -> bool:
  """True when a px value is neither zero, on the scale, nor a size the sheet names for it.

  A spacing property reads the scale alone. A type size names a font size, and never
  excuses a padding. Any other property may also take a layout size the sheet names.
  """
  match = PX.match(value.strip())
  if not match:
    return False
  number = float(match.group(1))
  if number == 0 or on_scale(number, scale):
    return False
  if name.startswith(SPACING_PROPERTIES):
    return True
  return number not in named_sizes(tokens, ramp=name in TYPE_PROPERTIES)


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


def check_density(report: dict) -> list[dict]:
  """Return whitespace and layout-density findings (NUM205, NUM206, NUM207, NUM208)."""
  findings: list[dict] = []
  viewport_width = float(report.get("width", 1280) or 1280)
  for element in report.get("elements", []):
    selector = element.get("selector", "?")
    bounds = element.get("bounds") or {}
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
      if raw_ratio is not None and float(raw_ratio) < MIN_INK_RATIO:
        ratio = float(raw_ratio)
        findings.append(
          {
            "rule": "NUM205",
            "selector": selector,
            "evidence": (
              f"{selector}: ink ratio is {ratio:.2f} (< {MIN_INK_RATIO:g})"
              f" across {width:g} px"
            ),
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
        }
      )
    tag = str(element.get("tag") or "").lower()
    input_type = str(
      element.get("input_type") or element.get("inputType") or "text"
    ).lower()
    if (
      tag == "select" or (tag == "input" and input_type not in STRETCH_EXEMPT_INPUTS)
    ) and width > MAX_CONTROL_WIDTH:
      findings.append(
        {
          "rule": "NUM207",
          "selector": selector,
          "evidence": (
            f"{selector}: control width is {width:g} px (> {MAX_CONTROL_WIDTH:g} px)"
          ),
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

  density_command = commands.add_parser(
    "density", help="Check row/card ink ratio, sibling gaps, and control width caps"
  )
  density_command.add_argument("report", type=Path)

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
  if getattr(arguments, "density", False):
    findings.extend(f"{row['rule']} {row['evidence']}" for row in check_density(report))
  for finding in findings:
    print(f"{report.get('width')} px {finding}")
  if findings:
    return 1
  print(f"{report.get('width')} px: every value sits on the sheet")
  return 0


if __name__ == "__main__":
  sys.exit(main())
