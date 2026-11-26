"""Report dump icon slugs with no SVG that lobe-icons could fill.

Reads the resolved catalog dump at `.daedalus-state/dump/models.json`. Takes each provider
and developer slug the way the dashboard does. Lists every uncovered slug with its
lobe-icons mono origin, at the pinned commit in the icons README.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from parse_dump import DUMP_DIR, rows_of

ROOT = Path(__file__).resolve().parents[1]
ICONS_DIR = ROOT / "daedalus" / "dashboard" / "ui" / "icons"
LOBE_COMMIT = "cfd0e17"
LOBE_API = (
  f"https://api.github.com/repos/lobehub/lobe-icons/contents/"
  f"packages/static-svg/icons?ref={LOBE_COMMIT}"
)

# The rename map of the icons README, origin -> file name here.
RENAMED = {
  "aionlabs": "aion-labs",
  "bfl": "black-forest-labs",
  "deepseek": "deepseek-ai",
  "dotsstudio": "dots-studio",
  "fishaudio": "fish-audio",
  "ibm": "ibm-granite",
  "ling": "inclusionai",
  "inference": "inference-net",
  "meta": "meta-llama",
  "runway": "runwayml",
  "kilocode": "kilo",
  "mistral": "mistralai",
  "moonshot": "moonshotai",
  "myshell": "myshell-ai",
  "reka": "rekaai",
  "stability": "stabilityai",
  "together": "togethercomputer",
  "typesafeai": "typesafe",
  "xai": "x-ai",
  "xiaomimimo": "xiaomi",
  "zai": "z-ai",
}


def icon_names(row: dict) -> list[str]:
  """The icon slugs of one row: its provider slug and its developer slug."""
  bits = str(row.get("id") or "").split("/")
  if len(bits) < 2:
    return []
  provider, middle = bits[0], bits[1:-1]
  names = [provider]
  if middle:
    dev = middle[-1]
    if dev.lower() != provider.lower():
      names.append(dev)
  # Novita serves the Ling series of inclusionAI, so the developer icon is inclusionai.
  if provider == "novita" and bits[-1].startswith("ling-"):
    names.append("inclusionai")
  return names


def covered(names: set[str], icons_dir: Path) -> set[str]:
  """The slugs that already hold an SVG file."""
  return {name for name in names if (icons_dir / f"{name}.svg").exists()}


def lobe_origins(lobe_dir: Path | None) -> dict[str, None]:
  """The stem of each lobe mono icon, from a local folder or the pinned GitHub tree."""
  if lobe_dir is not None:
    return {path.stem: None for path in lobe_dir.glob("*.svg")}
  with urllib.request.urlopen(LOBE_API) as response:
    listing = json.load(response)
  return {str(entry["name"]).removesuffix(".svg"): None for entry in listing}


def origin_of(name: str, lobes: set[str]) -> str | None:
  """The lobe origin of one uncovered slug, through the rename map when needed."""
  if name in lobes:
    return name
  for origin, target in RENAMED.items():
    if target == name and origin in lobes:
      return origin
  return None


def report(
  rows: list[dict], icons_dir: Path, lobe_dir: Path | None
) -> list[tuple[str, int, str | None]]:
  """One uncovered slug with its row count and lobe origin, sorted by name."""
  uses: dict[str, int] = {}
  for row in rows:
    for name in icon_names(row):
      uses[name] = uses.get(name, 0) + 1
  missing = sorted(set(uses) - covered(set(uses), icons_dir))
  lobes = set(lobe_origins(lobe_dir))
  return [(name, uses[name], origin_of(name, lobes)) for name in missing]


def main(argv: list[str] | None = None) -> int:
  """Print the uncovered icon slugs of a dump with their lobe candidates."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("path", nargs="?", type=Path, help="dump file to read")
  parser.add_argument("--icons", type=Path, default=ICONS_DIR, help="icons folder")
  parser.add_argument("--lobe", type=Path, default=None, help="local lobe icons folder")
  parser.add_argument(
    "--format", choices=("text", "json"), default="text", help="output format"
  )
  args = parser.parse_args(argv)
  path = args.path or DUMP_DIR / "models.json"
  if not path.exists():
    print(f"no dump file at {path}", file=sys.stderr)
    return 1
  rows = rows_of(json.loads(path.read_text(encoding="utf-8")))
  found = report(rows, args.icons, args.lobe)
  if args.format == "json":
    print(
      json.dumps(
        [
          {"name": name, "rows": count, "lobe": origin} for name, count, origin in found
        ],
        indent=2,
      )
    )
    return 0
  print(f"{len(found)} uncovered icon slugs in {path.name}:")
  for name, count, origin in found:
    tail = origin if origin else "\u2014"
    print(f"  {name:24} {count:4} rows  {tail}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
