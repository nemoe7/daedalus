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
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from annotate import annotate_image
from measure import (
  DEFAULT_SCALE,
  DEFAULT_SHEET,
  check_density,
  parse_sheet,
  point,
)
from measure import check as check_measurements
from measure import main as measure_main

VERSION = "0.1.9"
NAMED_BREAKPOINTS = ("75em", "60em", "40em", "30em")
MEDIA_BREAKPOINT_RE = re.compile(
  r"@media\b[^{]*?\(\s*(?:min|max)-(?:width|height)\s*:\s*([0-9.]+(?:em|rem|px))\s*\)",
  re.IGNORECASE,
)
HOME = Path(__file__).resolve().parent.parent
CATALOG = HOME / "data" / "vendor" / "ui-ux-pro-max"
WIDTHS = (1280, 360)
SKIP = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


def load(path):
  return json.loads(Path(path).read_text(encoding="utf-8"))


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
  def __init__(self, path):
    super().__init__(convert_charrefs=True)
    self.path, self.findings = path, []
    self.labels, self.ids, self.controls = set(), set(), []
    self.label_depth = 0

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

  def handle_starttag(self, tag, attributes):
    attrs = dict(attributes)
    if attrs.get("id"):
      self.ids.add(attrs["id"])
    if tag == "label":
      self.label_depth += 1
      if attrs.get("for"):
        self.labels.add(attrs["for"])
    if tag == "img" and "alt" not in attrs:
      self.finding("NUL001", "1.1.1", "Image has no alt attribute", self.getpos())
    if tag in {"input", "select", "textarea"}:
      self.controls.append((attrs, bool(self.label_depth), self.getpos()))

  def handle_endtag(self, tag):
    if tag == "label":
      self.label_depth = max(0, self.label_depth - 1)

  def finish(self):
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
          "NUL002", "4.1.2", "Control has no source naming evidence", position
        )


def lint(root):
  findings, coverage = [], []
  for path in files(root, {".html", ".css", ".js", ".jsx", ".ts", ".tsx"}):
    text = path.read_text(encoding="utf-8")
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
      for match in re.finditer(
        r"outline\s*:\s*(?:none|0(?:px)?)\s*(?:!important\s*)?[;}]", clean
      ):
        current.append(
          {
            "rule": "NUL003",
            "path": str(path),
            "line": text.count("\n", 0, match.start()) + 1,
            "column": 1,
            "severity": "review",
            "status": "source_candidate",
            "evidence": match[0],
            "criterion": "2.4.7",
          }
        )
    for finding in current:
      line = text.splitlines()[finding["line"] - 1]
      ignored = set(re.findall(r"\bnonuu:\s*([A-Za-z0-9_-]+)", line))
      if finding["rule"] not in ignored:
        findings.append(finding)
  return {
    "findings": findings,
    "coverage": coverage,
    "limits": [
      "Source patterns do not prove accessible names or WCAG conformance.",
      "Visual quality and task success need rendered and interaction evidence.",
    ],
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
  request = {
    "command": args.command,
    "url": args.url,
    "output": str(Path(args.artifacts).resolve()),
    "widths": sorted({*WIDTHS, *args.widths}),
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
      capture["density_findings"] = check_density(capture.get("measurements", {}))
      location = {
        "width": capture["width"],
        "scheme": capture["scheme"],
        "motion": capture["motion"],
      }
      result.setdefault("findings", []).extend(
        {
          "rule": "NUM201",
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
  configuration = load(config) if config else {}
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
      "status": "checked" if config else "not_checked",
      "values": configuration,
    },
    "findings": mismatches + asset_report.get("findings", []),
  }


def main(argv=None):
  raw_args = list(sys.argv[1:] if argv is None else argv)
  if raw_args and raw_args[0] in {"check", "ruler", "density"}:
    return measure_main(raw_args)
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  for name in (
    "search",
    "assets",
    "lint",
    "extract",
    "annotate",
    "doctor",
    "inspect",
    "flow",
    "audit",
    "a11y",
    "live",
    "check",
    "density",
    "ruler",
  ):
    command = commands.add_parser(name)
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
    command.add_argument("-p", "--project", type=Path, default=Path.cwd())
    command.add_argument("-o", "--output", type=Path, required=name == "annotate")
    if name == "annotate":
      command.add_argument("-i", "--input", type=Path, required=True)
      command.add_argument("-b", "--box", action="append", default=[])
      command.add_argument("-l", "--label", action="append", default=[])
      command.add_argument("-a", "--arrow", action="append", default=[])
      command.add_argument("-c", "--callout", action="append", default=[])
      command.add_argument("-s", "--spec", type=Path)
      command.add_argument("-S", "--scale", type=int)
    if name == "search":
      command.add_argument("query")
      command.add_argument("-d", "--dataset")
      command.add_argument("-s", "--stack")
      command.add_argument("-l", "--limit", type=int, default=10)
    if name == "doctor":
      command.add_argument("-m", "--manifest", type=Path)
      command.add_argument("-c", "--config", type=Path)
    if name == "assets":
      command.add_argument("-m", "--manifest", type=Path)
      command.add_argument("-f", "--fetch")
      command.add_argument("-d", "--destination")
      command.add_argument("-A", "--authorize-fetch", action="store_true")
    if name in {"inspect", "flow", "audit", "a11y", "live"}:
      command.add_argument("-u", "--url", required=name != "audit")
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
  args = parser.parse_args(raw_args)
  root = args.project.resolve()
  if not root.is_dir():
    parser.error("project directory does not exist")
  try:
    if args.command == "search":
      if not 0 < args.limit <= 100:
        raise ValueError("limit must be from 1 through 100")
      result = search(args.query, args.dataset, args.stack, args.limit)
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
      result = lint(root)
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
      )
      args.output = None
    elif args.command == "doctor":
      result = doctor(root, args.manifest, args.config)
    elif args.command in {"inspect", "flow", "a11y", "live"}:
      if args.timeout <= 0 or any(width <= 0 for width in args.widths):
        raise ValueError("timeout and widths must be positive")
      result = browser(args)
    else:
      result = {
        "source": lint(root),
        "design": extract(root),
        "rendered": browser(args)
        if args.url
        else {"status": "not_checked", "reason": "url_missing"},
        "visual_judgment": "not_run",
        "participant_research": "not_run",
        "real_device": "not_run",
      }
    if args.command == "audit":
      result["findings"] = result["source"]["findings"] + result["rendered"].get(
        "findings", []
      )
      result["status"] = result["rendered"].get("status")
    result = {
      "tool": args.command,
      "version": VERSION,
      "timestamp": datetime.now(timezone.utc).isoformat(),
      **result,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
      with args.output.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
    print(serialized, end="")
    if result.get("status") == "error":
      return 2
    if result.get("findings"):
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
