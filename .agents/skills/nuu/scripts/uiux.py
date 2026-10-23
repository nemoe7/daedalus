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

from measure import check as check_measurements
from measure import parse_sheet

VERSION = "0.1.4"
HOME = Path(__file__).resolve().parent.parent
CATALOG = HOME / "data" / "vendor" / "ui-ux-pro-max"
WIDTHS = (1280, 360)
SKIP = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}
MODES = {
  "critique": ("hierarchy", "alignment", "task clarity", "evidence for each judgment"),
  "polish": ("spacing", "type", "icons", "focus", "state consistency"),
  "harden": ("validation", "failure", "retry", "offline", "long content"),
  "normalize": ("active design authority", "token drift", "component variants"),
  "animate": ("motion purpose", "timing", "interruption", "reduced motion"),
  "delight": ("useful feedback", "task relevance", "motion preference"),
  "distill": ("primary task", "redundant controls", "retained facts and recovery"),
  "onboard": ("first task", "empty state", "contextual help", "return path"),
}
RECORD_SECTIONS = (
  "Purpose and status",
  "Users, tasks, and language",
  "Journeys and information architecture",
  "Screens and states",
  "Design system and tokens",
  "Components and contracts",
  "Accessibility and inclusion",
  "Content and assets",
  "Behavior and data",
  "Evidence and handoff",
  "Decisions and history",
)


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
  tokens, components = [], []
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
    "components": components,
    "coverage": {
      "formats": ["CSS custom properties", "HTML class patterns"],
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
      self.finding("UIX001", "1.1.1", "Image has no alt attribute", self.getpos())
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
          "UIX002", "4.1.2", "Control has no source naming evidence", position
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
            "rule": "UIX003",
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
      ignored = set(re.findall(r"\bnoui:\s*([A-Za-z0-9_-]+)", line))
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


def brief(args):
  result = load(args.input) if args.input else {}
  if not isinstance(result, dict):
    raise TypeError("brief must be a JSON object")
  for name in ("audience", "task", "goal", "stack"):
    supplied = getattr(args, name, None)
    if supplied:
      result[name] = supplied
    result.setdefault(name, "unknown")
  result.setdefault("constraints", [])
  result.setdefault("acceptance", [])
  result.setdefault("unknowns", [])
  if not isinstance(result["unknowns"], list):
    raise TypeError("brief unknowns must be an array")
  for name in ("audience", "task", "goal", "stack"):
    if result[name] == "unknown" and name not in result["unknowns"]:
      result["unknowns"].append(name)
  return result


def system(root, source):
  context = load(source)
  if not isinstance(context, dict):
    raise TypeError("brief must be a JSON object")
  query = " ".join(str(context.get(name, "")) for name in ("task", "goal", "audience"))
  existing = extract(root)
  return {
    "status": "proposed",
    "brief": context,
    "existing_authority": existing,
    "recommendations": {
      name: search(query, name, context.get("stack"), 3)
      for name in ("styles", "colors", "typography", "products", "ui-reasoning")
    },
    "writes": [],
    "unknowns": context.get("unknowns", []),
  }


def teach(root, record, create=False):
  destination = contained(root, record or "docs/design.md")
  if destination.exists():
    text = destination.read_text(encoding="utf-8")
    return {
      "status": "existing",
      "authority": str(destination),
      "writes": [],
      "headings": re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE),
      "mapping_required": list(RECORD_SECTIONS),
    }
  template = (HOME / "references/design-document.md").read_text(encoding="utf-8")
  fields = re.findall(
    r"^\d+\. \*\*(.+?)\*\*:\s*(?:Record|Link) (.+)$", template, re.MULTILINE
  )
  text = (
    "# Design record\n\n"
    + "\n\n".join(
      f"## {heading}\n\n- Fields: {description}\n- Status: unknown.\n- Evidence: not run."
      for heading, description in fields
    )
    + "\n"
  )
  if create:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
      stream.write(text)
  return {
    "status": "created" if create else "proposed",
    "authority": str(destination),
    "markdown": text,
    "writes": [str(destination)] if create else [],
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
      result.setdefault("findings", []).extend(
        {
          "rule": "UIX201",
          "severity": "review",
          "evidence": finding,
          "location": {
            "width": capture["width"],
            "scheme": capture["scheme"],
            "motion": capture["motion"],
          },
        }
        for finding in capture["house_measurements"]
      )
    if result.get("findings"):
      result["status"] = "findings"
  return result


def mode_report(root, mode, evidence):
  rendered = load(evidence) if evidence else {}
  if not isinstance(rendered, dict):
    raise TypeError("evidence must be a JSON report object")
  nested = rendered.get("rendered", {})
  if not isinstance(nested, dict):
    raise TypeError("rendered evidence must be an object")
  captures = rendered.get("captures", nested.get("captures", []))
  if not isinstance(captures, list) or any(
    not isinstance(row, dict) for row in captures
  ):
    raise ValueError("evidence captures must be an array of objects")
  properties = {}
  actions, motion = [], []
  for capture in captures:
    if not isinstance(capture.get("flow", {}), dict) or not isinstance(
      capture.get("measurements", {}), dict
    ):
      raise TypeError("capture flow and measurements must be objects")
    steps = capture.get("flow", {}).get("steps", [])
    elements = capture.get("measurements", {}).get("elements", [])
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
      raise TypeError("flow steps must be an array of objects")
    if not isinstance(elements, list) or any(
      not isinstance(element, dict) for element in elements
    ):
      raise TypeError("measured elements must be an array of objects")
    actions.extend(steps)
    for element in elements:
      values = element.get("values", {})
      if not isinstance(values, dict):
        raise TypeError("computed values must be an object")
      for name, value in values.items():
        properties.setdefault(name, Counter())[str(value)] += 1
        if (
          mode == "animate"
          and capture.get("motion") == "reduce"
          and name in {"animation-duration", "transition-duration"}
          and any(float(part) > 0 for part in re.findall(r"[\d.]+", str(value)))
        ):
          motion.append(
            {
              "selector": element["selector"],
              "property": name,
              "value": value,
              "status": "review",
            }
          )
  summary = {
    "status": "measured"
    if any(row.get("measurements") for row in captures)
    else "not_checked",
    "captures": [
      {
        "width": row.get("width"),
        "scheme": row.get("scheme"),
        "motion": row.get("motion"),
        "status": row.get("status"),
      }
      for row in captures
    ],
    "values": {name: dict(counts) for name, counts in properties.items()},
    "actions": actions,
    "reduced_motion_candidates": motion,
  }
  source = lint(root)
  return {
    "mode": mode,
    "source": source,
    "findings": source["findings"],
    "design": extract(root),
    "rendered_evidence": summary,
    "review_dimensions": [
      {"name": name, "status": "needs_contextual_review"} for name in MODES[mode]
    ],
    "writes": [],
    "visual_judgment": "not_run",
    "real_device": "not_run",
  }


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
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  for name in (
    "brief",
    "search",
    "system",
    "teach",
    "assets",
    "lint",
    "extract",
    "doctor",
    "inspect",
    "flow",
    "audit",
    "live",
    *MODES,
  ):
    command = commands.add_parser(name)
    command.add_argument("--project", type=Path, default=Path.cwd())
    command.add_argument("--output", type=Path)
    if name == "brief":
      command.add_argument("--input", type=Path)
      for field in ("audience", "task", "goal", "stack"):
        command.add_argument(f"--{field}")
    if name == "search":
      command.add_argument("query")
      command.add_argument("--dataset")
      command.add_argument("--stack")
      command.add_argument("--limit", type=int, default=10)
    if name in MODES:
      command.add_argument("--evidence", type=Path)
    if name == "system":
      command.add_argument("--brief", type=Path, required=True)
    if name == "teach":
      command.add_argument("--record")
      command.add_argument("--create", action="store_true")
    if name == "doctor":
      command.add_argument("--manifest", type=Path)
      command.add_argument("--config", type=Path)
    if name == "assets":
      command.add_argument("--manifest", type=Path)
      command.add_argument("--fetch")
      command.add_argument("--destination")
      command.add_argument("--authorize-fetch", action="store_true")
    if name in {"inspect", "flow", "audit", "live"}:
      command.add_argument("--url", required=name != "audit")
      command.add_argument("--artifacts", default="ui-evidence")
      command.add_argument("--widths", nargs="*", type=int, default=[])
      command.add_argument(
        "--selector", default="button,input,textarea,select,a,h1,h2,img,main"
      )
      command.add_argument("--engine", default="auto")
      command.add_argument("--executable")
      command.add_argument("--timeout", type=int, default=20)
      command.add_argument("--flow", type=Path, required=name == "flow")
      command.add_argument("--profile", choices=("project", "house"), default="project")
      command.add_argument("--edit", type=Path)
      command.add_argument("--authorize-edit", action="store_true")
      command.add_argument("--overlaps", action="store_true")
  args = parser.parse_args(argv)
  root = args.project.resolve()
  if not root.is_dir():
    parser.error("project directory does not exist")
  try:
    if args.command == "brief":
      result = brief(args)
    elif args.command == "search":
      if not 0 < args.limit <= 100:
        raise ValueError("limit must be from 1 through 100")
      result = search(args.query, args.dataset, args.stack, args.limit)
    elif args.command == "system":
      result = system(root, args.brief)
    elif args.command == "teach":
      result = teach(root, args.record, args.create)
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
    elif args.command == "doctor":
      result = doctor(root, args.manifest, args.config)
    elif args.command in {"inspect", "flow", "live"}:
      if args.timeout <= 0 or any(width <= 0 for width in args.widths):
        raise ValueError("timeout and widths must be positive")
      result = browser(args)
    elif args.command == "audit":
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
    else:
      result = mode_report(root, args.command, args.evidence)
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
