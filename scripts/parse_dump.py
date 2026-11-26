"""Read daedalus dump files and print their model rows.

With no path, lists the dump files in `.daedalus-state/dump`. With a path, prints
the model rows of that file. Handles every dump shape: the resolved catalog
(`data` list), provider and litellm dumps (`data.data` list), and modelschemas
dumps (`data.models` list). Filter with --provider, --tier, or --mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DUMP_DIR = Path(".daedalus-state/dump")

TIER_ORDER = ("TIER-A", "TIER-B", "TIER-C", "TIER-D")


def tier_key(tier: str | None) -> int:
  """Sort key that puts null tiers last and keeps pool order."""
  return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def rows_of(data: dict) -> list[dict]:
  """The model rows of one dump payload, whatever wrapper shape it uses."""
  found = data.get("data")
  if isinstance(found, list):
    return found
  if isinstance(found, dict):
    for key in ("data", "models", "result"):
      inner = found.get(key)
      if isinstance(inner, list):
        return inner
  return []


def label(row: dict) -> str:
  """The display text of one row: its name, title, or id."""
  return str(
    row.get("name") or row.get("displayName") or row.get("title") or row.get("id") or ""
  )


def filter_rows(
  rows: list[dict], provider: str | None, tier: str | None, mode: str | None
) -> list[dict]:
  """Keep only rows that match every given filter."""
  kept = []
  for row in rows:
    if (
      provider is not None
      and row.get("provider") != provider
      and row.get("litellm_provider") != provider
    ):
      continue
    if tier is not None and row.get("tier") != tier:
      continue
    if mode is not None and row.get("mode") != mode and row.get("activity") != mode:
      continue
    kept.append(row)
  return kept


def slug_of(row: dict) -> str:
  """The model slug of one row: the catalog slug, raw id, name, or id."""
  return str(
    row.get("slug") or row.get("rawId") or row.get("name") or row.get("id") or ""
  )


def print_rows(rows: list[dict], kind: str) -> None:
  """Print one row per model, shortest useful fields first."""
  if kind == "json":
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return
  order = sorted(
    rows,
    key=lambda row: (
      str(row.get("provider") or row.get("litellm_provider") or ""),
      tier_key(row.get("tier")),
      slug_of(row),
    ),
  )
  width = max((len(slug_of(row)) for row in order), default=0) + 2
  for row in order:
    tier = row.get("tier") or "—"
    mode = row.get("mode") or row.get("activity") or "—"
    provider = row.get("provider") or row.get("litellm_provider") or "—"
    slug = slug_of(row)
    print(f"{provider:14} {tier:8} {mode:7} {slug:<{width}}  {label(row)}")


def list_dumps() -> int:
  """Print the dump files that exist, with their row counts."""
  if not DUMP_DIR.exists():
    print(f"no dump folder at {DUMP_DIR}", file=sys.stderr)
    return 1
  for path in sorted(DUMP_DIR.glob("*.json")):
    try:
      count = len(rows_of(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, ValueError):
      count = -1
    print(f"{path.name:32} {count:6} rows")
  return 0


def main(argv: list[str] | None = None) -> int:
  """List dump files, or print the model rows of one dump file."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("path", nargs="?", help="dump file to read; omit to list dumps")
  parser.add_argument("--provider", help="keep rows of this provider")
  parser.add_argument("--tier", help="keep rows of this tier")
  parser.add_argument("--mode", help="keep rows of this mode")
  parser.add_argument(
    "--format", choices=("text", "json"), default="text", help="output format"
  )
  args = parser.parse_args(argv)

  if args.path is None:
    return list_dumps()
  path = Path(args.path)
  if not path.exists():
    print(f"no dump file at {path}", file=sys.stderr)
    return 1
  data = json.loads(path.read_text(encoding="utf-8"))
  rows = filter_rows(rows_of(data), args.provider, args.tier, args.mode)
  print_rows(rows, args.format)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
