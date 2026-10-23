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
  parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
  parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
  commands = parser.add_subparsers(dest="command", required=True)

  check_command = commands.add_parser(
    "check", help="Check a JSON report against the sheet"
  )
  check_command.add_argument("report", type=Path)

  ruler_command = commands.add_parser(
    "ruler", help="Measure the gap between two guides"
  )
  ruler_command.add_argument("--from", dest="start", type=point, required=True)
  ruler_command.add_argument("--to", dest="end", type=point, required=True)

  arguments = parser.parse_args(argv)
  tokens = parse_sheet(arguments.sheet.read_text(encoding="utf-8"))

  if arguments.command == "ruler":
    gap = ruler(arguments.start, arguments.end, arguments.scale)
    print(f"gap {gap['dx']:g} x {gap['dy']:g} px, distance {gap['distance']:g} px")
    for axis, fits in gap["axes"].items():
      print(f"{axis}: {'on' if fits else 'off'} the {arguments.scale:g} px scale")
    return 0 if gap["on_scale"] else 1

  report = json.loads(arguments.report.read_text(encoding="utf-8"))
  findings = check(report, tokens, arguments.scale)
  for finding in findings:
    print(f"{report.get('width')} px {finding}")
  if findings:
    return 1
  print(f"{report.get('width')} px: every value sits on the sheet")
  return 0


if __name__ == "__main__":
  sys.exit(main())
