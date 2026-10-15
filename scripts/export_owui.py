"""Generate Open WebUI JSON imports from the Python plugins."""

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def payload(source: Path) -> str:
  content = source.read_text(encoding="utf-8")
  header = ast.get_docstring(ast.parse(content)) or ""
  metadata = {}
  for line in header.splitlines():
    name, separator, value = line.partition(":")
    if separator:
      metadata[name.strip()] = value.strip()
  if not metadata.get("title") or not metadata.get("description"):
    raise ValueError(f"Missing title or description: {source}")
  item = {
    "id": source.stem,
    "name": metadata["title"],
    "content": content,
    "meta": {"description": metadata["description"]},
  }
  return json.dumps([item], ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--check", action="store_true", help="check without writing files"
  )
  args = parser.parse_args(argv)
  plugins = ROOT / "integrations/openwebui"
  output = plugins / "imports"
  expected = set()
  failed = False
  for kind in ("tools", "functions"):
    for source in sorted((plugins / kind).glob("*.py")):
      target = output / kind / source.with_suffix(".json").name
      expected.add(target)
      content = payload(source)
      if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
          print(f"Stale or missing import: {target.relative_to(ROOT)}", file=sys.stderr)
          failed = True
      else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
  for extra in sorted(set(output.rglob("*.json")) - expected):
    print(f"Import has no source: {extra.relative_to(ROOT)}", file=sys.stderr)
    failed = True
  return int(failed)


if __name__ == "__main__":
  raise SystemExit(main())
