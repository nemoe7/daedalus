"""Sole 9-subcommand CLI dispatcher, config loader, static scanner, and Markdown auditor for nuu."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tomllib
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from collections.abc import Iterable, Mapping, Sequence
  from typing import Any

HOME = Path(__file__).resolve().parent.parent
DEFAULT_NUU_TOML = HOME / "references" / "nuu.toml"
DEFAULT_STYLESHEET = HOME / "references" / "style.css"
CATALOG = HOME / "data" / "vendor" / "ui-ux-pro-max"
RECIPES_DIR = HOME / "references" / "recipes"

NAMED_BREAKPOINTS = ("68em", "60em", "30em", "24em")
DEVICE_BREAKPOINTS = NAMED_BREAKPOINTS

DEFAULT_VIEWPORTS: tuple[dict[str, Any], ...] = (
  {"width": 1920, "height": 957, "mobile": False, "touch": False, "report": True},
  {"width": 1080, "height": 2437, "mobile": False, "touch": False, "report": False},
  {"width": 958, "height": 958, "mobile": False, "touch": False, "report": False},
  {"width": 855, "height": 325, "mobile": True, "touch": True, "report": False},
  {"width": 411, "height": 799, "mobile": True, "touch": True, "report": True},
)
WIDTHS = tuple(vp["width"] for vp in DEFAULT_VIEWPORTS)
REPORT_WIDTHS = tuple(vp["width"] for vp in DEFAULT_VIEWPORTS if vp["report"])

VALID_DATASETS = frozenset(
  {
    "app-interface",
    "charts",
    "colors",
    "google-fonts",
    "icons",
    "landing",
    "motion",
    "products",
    "react-performance",
    "styles",
    "typography",
    "ui-reasoning",
    "ux-guidelines",
  }
)
VALID_STACKS = frozenset(
  {
    "angular",
    "astro",
    "avalonia",
    "flutter",
    "html-tailwind",
    "javafx",
    "jetpack-compose",
    "laravel",
    "nextjs",
    "nuxt-ui",
    "nuxtjs",
    "react",
    "react-native",
    "shadcn",
    "svelte",
    "swiftui",
    "threejs",
    "uno",
    "uwp",
    "vue",
    "winui",
    "wpf",
  }
)

SKIP = {
  ".agents",
  ".git",
  ".hg",
  ".svn",
  ".venv",
  "venv",
  "node_modules",
  "dist",
  "build",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
  "nuu-artifacts",
}
MARKER_SCAN_SUFFIXES = {
  ".html",
  ".css",
  ".svg",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".md",
  ".toml",
  ".py",
}

DEFAULT_RULE_SEVERITIES: dict[str, str] = {
  "DOC001": "review",
  "ICN001": "review",
  "ICN002": "review",
  "ICN101": "review",
  "ICN102": "error",
  "TYP001": "error",
  "TYP002": "error",
  "TYP003": "error",
  "TYP101": "review",
  "TYP102": "error",
  "TYP103": "error",
  "TYP201": "review",
  "TYP202": "error",
  "TYP203": "error",
  "SPC001": "error",
  "SPC002": "error",
  "SPC101": "error",
  "SPC102": "error",
  "SPC201": "review",
  "SPC202": "review",
  "SPC203": "review",
  "SPC204": "error",
  "SPC205": "error",
  "SPC206": "error",
  "HRC001": "error",
  "HRC101": "error",
  "HRC102": "error",
  "HRC201": "error",
  "HRC202": "error",
  "HRC203": "error",
  "ACC001": "error",
  "ACC002": "error",
  "ACC003": "error",
  "ACC004": "error",
  "ACC005": "error",
  "ACC006": "error",
  "ACC007": "error",
  "ACC101": "error",
  "ACC201": "error",
  "ACC202": "error",
  "ACC203": "error",
  "ACC204": "error",
  "ACC205": "error",
  "ACC206": "review",
  "ACC207": "error",
  "RUN001": "error",
  "RUN101": "error",
  "RUN102": "error",
  "RCP001": "error",
}

BUILTIN_RULE_DESCRIPTIONS: dict[str, str] = {
  "DOC001": "Missing design doc at [paths].doc (docs/design.md default)",
  "ICN001": "Inline <svg> icons present without a companion license file or inline license comment",
  "ICN002": "Icon outside the Lucide -> Simple Icons -> Phosphor ladder or off the 24x24 stroke-2 grid",
  "ICN101": "Mixed inline <svg> pixel sizes on the same surface",
  "ICN102": "Rendered icon paired with text has box height differing (> 1 px) from paired text line-height",
  "TYP001": "Computed font-size outside the 9-step ramp or font-weight outside {400, 500, 600, 700}",
  "TYP002": "Computed font-family misses configured sans/mono stacks or uses italic body blocks",
  "TYP003": "text-transform: uppercase on any element other than 11 px .eyebrow",
  "TYP101": "h1-h6 line-height outside 1.1-1.3x or non-heading prose outside 1.3-1.5x",
  "TYP102": "h1-h6 letter-spacing outside -0.03em to -0.005em or non-zero tracking on non-headings",
  "TYP103": "Centered prose over 3 lines, text-align: justify, or mismatched heading vs body alignment",
  "TYP201": "Multi-line desktop prose wider than 75ch or standalone prose container narrower than 50ch",
  "TYP202": "List CSS sets list-style-position: inside or omits the 2ch outside hanging-indent reset",
  "TYP203": "Rendered list markers miss the 2ch outside slot or wrapped lines misalign with line 1",
  "SPC001": "Padding, margin, gap, control height, or drop-ray span off the selected spacing scale",
  "SPC002": "Computed border-radius outside {0, 4, 6, 10, 9999}px or non-concentric nested radius",
  "SPC101": "Inter-group gap < 1.5x intra-group gap, 0 px touching controls, or mismatched group axis gaps",
  "SPC102": "Ragged inline start edges across stacked card children or asymmetric card inline padding",
  "SPC201": "Low row ink ratio on wide desktop bands without structural media",
  "SPC202": "Excessive empty gap between adjacent flow siblings inside a component or card",
  "SPC203": "Excess trailing slack inside a row or fixed-height container",
  "SPC204": "Non-scrolling container (overflow: hidden|clip) clips child ink or text box",
  "SPC205": "Visible sibling bounding boxes overlap unintentionally (> 1 px)",
  "SPC206": "Horizontal viewport overflow at a configured audit viewport",
  "HRC001": "More than 1 primary button in one card/dialog or vague Yes/No/Ok/Okay action label",
  "HRC101": "More than 2 ramp sizes in a card/dialog, > 3 per view, or body larger/bolder than heading",
  "HRC102": (
    "Buttons in the same group differ in width or height (> 1 px), "
    "or icon-only button is < 24x24 px or non-square"
  ),
  "HRC201": "Raw ISO-8601 or unlocalized UTC/numeric-offset timestamp literal in UI markup",
  "HRC202": "Rendered visible UI text exposes raw ISO-8601 or unlocalized UTC/+00:00 timestamp",
  "HRC203": "Placeholder Lorem ipsum copy in source or rendered DOM",
  "ACC001": "<img> missing alt attribute in source HTML",
  "ACC002": "<button>, <input>, <select>, or <textarea> lacks source label or accessible-name evidence",
  "ACC003": "Visible interactive control resolves an empty accessible name",
  "ACC004": "aria-labelledby, aria-describedby, aria-controls, or aria-owns references a missing DOM id",
  "ACC005": "Interactive control nested inside another interactive control",
  "ACC006": "Duplicate id attribute across elements in the document",
  "ACC007": "Rendered heading outline skips one or more heading levels",
  "ACC101": "Rendered text contrast < 4.5:1 (< 3:1 large text) or scrollbar/slider thumb contrast < 3:1",
  "ACC201": "CSS suppresses focus ring without a >= 2 px :focus-visible replacement",
  "ACC202": "Pointer click shows focus ring before keyboard Tab, or keyboard :focus-visible lacks >= 2 px ring",
  "ACC203": "Clipped :focus-visible ring, clipped dialog/overlay content, or transparent tooltip/popover surface",
  "ACC204": ":hover, :focus, :focus-visible, :active, or [aria-busy] rule mutates box dimensions",
  "ACC205": "Control bounding box changes width or height across interactive states",
  "ACC206": "Safe action click produces no visible DOM text, aria-live, state attribute, or route change",
  "ACC207": (
    "Write action fails to render immediate non-duplicating optimistic DOM state "
    "under held network + mocked clock"
  ),
  "RUN001": "Uncaught pageerror or console.error during page load, settle, or interaction",
  "RUN101": "Walk step, selector lookup, or assertion raised an unhandled timeout or action error",
  "RUN102": "Target URL navigation timeout, missing board shot/spec file, or Chromium launch failure",
  "RCP001": "nuurecipe: <slug> references a slug with no matching references/recipes/<slug>.md",
}

RULE_CODE_RE = re.compile(r"\b(?:DOC|ICN|TYP|SPC|HRC|ACC|RUN|RCP)\d{3}\b")
MEDIA_BREAKPOINT_RE = re.compile(
  r"@media\b[^{]*?\(\s*(?:min|max)-(?:width|height)\s*:\s*([0-9.]+(?:em|rem|px))\s*\)",
  re.IGNORECASE,
)
RAW_UI_TIMESTAMP_RE = re.compile(
  r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
  r"|\s+\d{2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|[+-]\d{2}:?\d{2}))?)\b"
  r"|\b\d{2}:\d{2}(?::\d{2})?\s*(?:UTC|GMT)\b"
)
UNADJUSTED_JS_TIMESTAMP_RE = re.compile(
  r"\.(?:textContent|innerText)\s*=\s*"
  r"(?:(?![^\n;]*\b(?:clockTime|relativeTime|time|format\w*)\s*\()[^;\n]*\.(?:toISOString|toUTCString)\s*\("
  r"|[A-Za-z0-9_$.]+\.(?:at|updated_at|created_at)\s*(?:\|\|\s*['\"][^\n'\"]*['\"])?\s*;)"
  r"|\btimeZone\s*:\s*['\"](?:UTC|GMT|Etc/UTC)['\"]"
)
PLACEHOLDER_COPY_RE = re.compile(
  r"\b(?:lorem\s+ipsum|dolor\s+sit\s+amet|consectetur\s+adipiscing)\b",
  re.IGNORECASE,
)
NONUU_MARKER_RE = re.compile(
  r"\bnonuu:[ \t]*([A-Za-z0-9_,\t -]+?)(?=-->|\*/|;|$|\bnonuu:|\bnuurecipe:|\bnuu-deviation:)"
)
DEVIATION_MARKER_RE = re.compile(r"\bnuu-deviation:[ \t]*(.*?)(?=-->|\*/|$)")
RECIPE_MARKER_RE = re.compile(
  r"\bnuurecipe:[ \t]*([a-zA-Z0-9_,\t -]+?)(?=-->|\*/|;|$|\bnonuu:|\bnuurecipe:|\bnuu-deviation:)"
)

KNOWN_CONFIG_SCHEMA: dict[str, frozenset[str]] = {
  "paths": frozenset({"doc", "stylesheet"}),
  "fonts": frozenset({"prefer_project_css", "sans", "mono", "files"}),
  "annotate": frozenset({"scale", "crop"}),
  "measure": frozenset({"scale", "tolerance", "redline"}),
  "board": frozenset({"type", "columns", "scale", "wireframe", "shots", "boards"}),
  "audit": frozenset({"breakpoints", "viewports"}),
  "checks": frozenset({"severity"}),
}


class NuuConfigError(ValueError):
  """Raised when nuu.toml fails TOML syntax or schema validation."""


def _load_version(home: Path = HOME) -> str:
  pyproject = home / "pyproject.toml"
  if pyproject.is_file():
    try:
      data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
      ver = data.get("project", {}).get("version")
      if isinstance(ver, str) and ver.strip():
        return ver.strip()
    except (OSError, tomllib.TOMLDecodeError):
      pass
  try:
    from importlib.metadata import version

    return version("nuu")
  except Exception:
    return "0.2.0"


def _clean_md(text: str) -> str:
  cleaned = text.replace("`", "").strip().rstrip(".")
  cleaned = re.sub(r"^(?:,\s*)?(?:or|and)\s+", "", cleaned, flags=re.IGNORECASE).strip()
  if cleaned and cleaned[0].islower():
    cleaned = cleaned[0].upper() + cleaned[1:]
  return cleaned


def load_rule_descriptions(home: Path = HOME) -> dict[str, str]:
  descriptions = dict(BUILTIN_RULE_DESCRIPTIONS)
  nuu_md = home / "references" / "nuu.md"
  if nuu_md.is_file():
    for raw_line in nuu_md.read_text(encoding="utf-8").splitlines():
      line = raw_line.strip()
      if not (line.startswith("|") and line.endswith("|")):
        continue
      cells = [cell.strip() for cell in line.strip("|").split("|")]
      if len(cells) < 3:
        continue
      codes_in_cell = RULE_CODE_RE.findall(cells[0].replace("`", ""))
      if len(codes_in_cell) == 1:
        condition_cell = cells[-1]
        cleaned = _clean_md(condition_cell)
        if cleaned and cleaned.lower() not in {"condition", "exact trigger condition"}:
          descriptions[codes_in_cell[0]] = cleaned
  return descriptions


VERSION = _load_version()
KNOWN_NUU_RULES = frozenset(DEFAULT_RULE_SEVERITIES)
RULE_DESCRIPTIONS = load_rule_descriptions()


def resolve_renderer(explicit: str | None = None) -> str:
  raw = (explicit or os.environ.get("NUU_RENDERER") or "auto").strip().lower()
  if raw not in {"auto", "chromium", "bitmap"}:
    raise ValueError(
      f"Invalid $NUU_RENDERER={raw!r} (expected 'auto', 'chromium', or 'bitmap')"
    )
  return raw


def _executable_or_none(raw: str) -> str | None:
  """Return a resolved executable for a path or PATH name."""
  candidate = Path(raw)
  if candidate.is_file() and os.access(candidate, os.X_OK):
    return str(candidate.resolve())
  return shutil.which(raw)


def resolve_chromium_executable(explicit: str | Path | None = None) -> str | None:
  """Resolve Chromium via -x -> Playwright default -> $CHROMIUM_BIN."""
  if explicit is not None and str(explicit).strip():
    raw = str(explicit).strip()
    resolved = _executable_or_none(raw)
    if resolved:
      return resolved
    raise FileNotFoundError(f"RUN102: Chromium executable not found: {raw}")

  default_bin = playwright_chromium_executable()
  if default_bin:
    return default_bin

  env_bin = (os.environ.get("CHROMIUM_BIN") or "").strip()
  if env_bin:
    resolved = _executable_or_none(env_bin)
    if resolved:
      return resolved
    raise FileNotFoundError(
      f"RUN102: $CHROMIUM_BIN is not an executable file: {env_bin}"
    )
  return None


def playwright_chromium_executable() -> str | None:
  """Return the Playwright default Chromium binary, or None when absent."""
  try:
    from playwright.sync_api import sync_playwright
  except ImportError:
    return None
  try:
    with sync_playwright() as pw:
      candidate = Path(pw.chromium.executable_path)
  except Exception:
    return None
  if candidate.is_file() and os.access(candidate, os.X_OK):
    return str(candidate)
  if sys.platform == "win32":
    for suffix in (".exe", ".cmd", ".bat"):
      sibling = candidate.with_name(candidate.name + suffix)
      if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
  return None


def require_chromium_executable(explicit: str | Path | None = None) -> str:
  resolved = resolve_chromium_executable(explicit)
  if not resolved:
    raise RuntimeError(
      "RUN102: No Chromium binary found via -x, the Playwright default, or "
      "$CHROMIUM_BIN. Run `playwright install chromium`."
    )
  return resolved


def chromium_launch_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
  env = dict(os.environ if base_env is None else base_env)
  lib_dir = (env.get("CHROMIUM_LIB") or "").strip()
  if lib_dir:
    existing = (env.get("LD_LIBRARY_PATH") or "").strip()
    env["LD_LIBRARY_PATH"] = f"{lib_dir}:{existing}" if existing else lib_dir
  fonts_cfg = (env.get("CHROMIUM_FONTS") or "").strip()
  if fonts_cfg:
    fonts_path = Path(fonts_cfg)
    if fonts_path.is_dir() and (fonts_path / "fonts.conf").is_file():
      env["FONTCONFIG_FILE"] = str((fonts_path / "fonts.conf").resolve())
      env["FONTCONFIG_PATH"] = str(fonts_path.resolve())
    else:
      env["FONTCONFIG_FILE"] = fonts_cfg
  return env


def chromium_launch_kwargs(
  explicit: str | Path | None = None,
  *,
  extra_args: Sequence[str] = (),
) -> dict[str, Any]:
  exe = require_chromium_executable(explicit)
  env = chromium_launch_env()
  fc_file = env.get("FONTCONFIG_FILE")
  if fc_file:
    fc_path = Path(fc_file)
    sibling_fonts = fc_path.parent / "fonts"
    if fc_path.is_file() and sibling_fonts.is_dir():
      raw_fc = fc_path.read_text(encoding="utf-8", errors="ignore")
      if str(sibling_fonts.resolve()) not in raw_fc:
        patched_fc = fc_path.parent / "nuu-fonts.conf"
        sys_dir = (
          "  <dir>/usr/share/fonts</dir>\n" if Path("/usr/share/fonts").is_dir() else ""
        )
        patched_fc.write_text(
          '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
          "<fontconfig>\n"
          f"  <dir>{sibling_fonts.resolve()}</dir>\n"
          f"{sys_dir}"
          "  <cachedir>/tmp/fonts-cache/</cachedir>\n"
          "</fontconfig>\n",
          encoding="utf-8",
        )
        env["FONTCONFIG_FILE"] = str(patched_fc.resolve())
  args = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--font-render-hinting=none",
    *extra_args,
  ]
  return {
    "executable_path": exe,
    "headless": True,
    "env": env,
    "args": args,
  }


def ensure_artifacts_dir(directory: Path) -> Path:
  resolved = Path(directory).resolve()
  resolved.mkdir(parents=True, exist_ok=True)
  gitignore = resolved / ".gitignore"
  if not gitignore.exists():
    gitignore.write_text("*\n", encoding="utf-8")
  return resolved


def settle_page(page: Any, timeout_sec: float = 20.0) -> None:
  """Bounded network idle (<= 3 s), fonts.ready, zero [aria-busy='true'], and 2 rAF ticks."""
  idle_ms = int(min(3.0, max(0.2, float(timeout_sec))) * 1000)
  try:
    page.wait_for_load_state("networkidle", timeout=idle_ms)
  except Exception:
    pass
  busy_ms = int(min(5.0, max(0.5, float(timeout_sec))) * 1000)
  try:
    page.wait_for_function(
      """() => {
        const busy = document.querySelectorAll('[aria-busy="true"]');
        return busy.length === 0;
      }""",
      timeout=busy_ms,
    )
  except Exception:
    pass
  try:
    page.evaluate(
      """async () => {
        if (document.fonts && document.fonts.ready) {
          await document.fonts.ready;
        }
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }"""
    )
  except Exception:
    pass


def find_nuu_config(
  project_root: Path | None = None,
  config_path: Path | None = None,
) -> Path | None:
  """Resolve config via 1. -c/--config -> 2. nearest nuu.toml walking up from cwd."""
  if config_path is not None:
    target = Path(config_path).resolve()
    if not target.is_file():
      raise FileNotFoundError(f"nuu config file does not exist: {config_path}")
    return target
  cur = (Path(project_root) if project_root else Path.cwd()).resolve()
  if cur.is_file():
    cur = cur.parent
  while True:
    candidate = cur / "nuu.toml"
    if candidate.is_file():
      return candidate
    if (cur / ".git").exists() or cur.parent == cur or cur == Path("/tmp"):
      break
    cur = cur.parent
  return None


def _toml_key_pos(path: Path, key: str) -> tuple[int, int]:
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except OSError:
    return (1, 1)
  pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
  for idx, line in enumerate(lines, start=1):
    if pat.match(line):
      return (idx, line.find(key) + 1)
  return (1, 1)


def _parse_toml_file(path: Path) -> dict[str, Any]:
  text = path.read_text(encoding="utf-8")
  try:
    parsed = tomllib.loads(text)
  except tomllib.TOMLDecodeError as exc:
    lineno = getattr(exc, "lineno", 1)
    colno = getattr(exc, "colno", 1)
    msg = getattr(exc, "msg", str(exc))
    raise NuuConfigError(f"{path}:{lineno}:{colno}: {msg}") from exc
  if not isinstance(parsed, dict):
    raise NuuConfigError(f"{path}:1:1: nuu.toml must decode to a table")
  return parsed


def _default_config_dict() -> dict[str, Any]:
  if DEFAULT_NUU_TOML.is_file():
    return _parse_toml_file(DEFAULT_NUU_TOML)
  return {
    "paths": {"doc": "docs/design.md", "stylesheet": "references/style.css"},
    "fonts": {"prefer_project_css": True, "sans": "", "mono": "", "files": []},
    "annotate": {"scale": 2, "crop": "none"},
    "measure": {"scale": 4, "tolerance": 30, "redline": ""},
    "board": {"type": "panels", "columns": 2, "scale": 2, "wireframe": False},
    "audit": {
      "breakpoints": list(NAMED_BREAKPOINTS),
      "viewports": [dict(vp) for vp in DEFAULT_VIEWPORTS],
    },
    "checks": {"severity": {"TYP101": "review", "TYP201": "review"}},
  }


def _warn_unknown_keys(
  raw: Mapping[str, Any],
  source_path: Path,
  warn_stream: Any,
) -> list[str]:
  warnings: list[str] = []
  for key, value in raw.items():
    if key == "assets":
      if not isinstance(value, list):
        raise NuuConfigError(
          f"{source_path}:1:1: [[assets]] must be an array of tables"
        )
      for idx, item in enumerate(value):
        if not isinstance(item, dict):
          raise NuuConfigError(f"{source_path}:1:1: assets[{idx}] must be a table")
        for sub in item:
          if sub not in {"path", "source", "license", "attribution"}:
            msg = f"warning: {source_path}: unknown config key 'assets.{sub}'"
            warnings.append(msg)
            print(msg, file=warn_stream)
      continue
    if key not in KNOWN_CONFIG_SCHEMA:
      msg = f"warning: {source_path}: unknown config key '{key}'"
      warnings.append(msg)
      print(msg, file=warn_stream)
      continue
    if not isinstance(value, dict):
      raise NuuConfigError(f"{source_path}:1:1: [{key}] must be a table")
    allowed = KNOWN_CONFIG_SCHEMA[key]
    for sub in value:
      if sub not in allowed:
        msg = f"warning: {source_path}: unknown config key '{key}.{sub}'"
        warnings.append(msg)
        print(msg, file=warn_stream)
  return warnings


def _normalize_viewports(raw_viewports: Any, source_label: str) -> list[dict[str, Any]]:
  if raw_viewports is None:
    return [dict(vp) for vp in DEFAULT_VIEWPORTS]
  if not isinstance(raw_viewports, list) or not raw_viewports:
    raise NuuConfigError(f"{source_label}: [audit].viewports must be a non-empty list")
  normalized: list[dict[str, Any]] = []
  for item in raw_viewports:
    if not isinstance(item, dict):
      raise NuuConfigError(f"{source_label}: [audit].viewports entries must be tables")
    w = item.get("width")
    h = item.get("height")
    if (
      isinstance(w, bool)
      or isinstance(h, bool)
      or not isinstance(w, int)
      or not isinstance(h, int)
      or w <= 0
      or h <= 0
    ):
      raise NuuConfigError(
        f"{source_label}: viewport width and height must be positive integers"
      )
    normalized.append(
      {
        "width": w,
        "height": h,
        "mobile": bool(item.get("mobile", False)),
        "touch": bool(item.get("touch", False)),
        "report": bool(item.get("report", False)),
      }
    )
  return normalized


def load_nuu_config(
  project_root: Path | None = None,
  config_path: Path | None = None,
  *,
  warn_stream: Any = None,
) -> dict[str, Any]:
  """Load and validate nuu.toml v2 with fallback to bundled references/nuu.toml."""
  stream = sys.stderr if warn_stream is None else warn_stream
  found = find_nuu_config(project_root, config_path)
  defaults = _default_config_dict()
  raw: dict[str, Any] = {}
  warnings: list[str] = []
  if found is not None:
    raw = _parse_toml_file(found)
    warnings = _warn_unknown_keys(raw, found, stream)
    project_dir = found.parent.resolve()
    effective_path = found
    used_bundled = False
  else:
    base = (Path(project_root) if project_root else Path.cwd()).resolve()
    project_dir = base.parent if base.is_file() else base
    effective_path = DEFAULT_NUU_TOML
    used_bundled = True

  merged: dict[str, Any] = {}
  for section in ("paths", "fonts", "annotate", "measure", "board", "audit"):
    sec_default = dict(defaults.get(section) or {})
    sec_raw = raw.get(section)
    if isinstance(sec_raw, dict):
      sec_default.update(sec_raw)
    merged[section] = sec_default

  merged["assets"] = list(raw.get("assets") or [])

  severities = dict(DEFAULT_RULE_SEVERITIES)
  default_sev = (defaults.get("checks") or {}).get("severity") or {}
  if isinstance(default_sev, dict):
    severities.update(default_sev)
  checks_raw = raw.get("checks")
  if checks_raw is not None:
    if not isinstance(checks_raw, dict):
      raise NuuConfigError(f"{effective_path}:1:1: [checks] must be a table")
    sev_raw = checks_raw.get("severity", {})
    if not isinstance(sev_raw, dict):
      raise NuuConfigError(f"{effective_path}:1:1: [checks.severity] must be a table")
    for code, sev in sev_raw.items():
      if code not in KNOWN_NUU_RULES or sev not in {"error", "review"}:
        ln, col = _toml_key_pos(effective_path, str(code))
        raise NuuConfigError(
          f"{effective_path}:{ln}:{col}: invalid [checks.severity] setting: {code} = {sev!r}"
        )
      severities[code] = sev
  merged["checks"] = {"severity": severities}

  merged["audit"]["viewports"] = _normalize_viewports(
    merged["audit"].get("viewports"), str(effective_path)
  )
  bps = merged["audit"].get("breakpoints")
  if not isinstance(bps, list) or not all(
    isinstance(bp, str) and bp.strip() for bp in bps
  ):
    raise NuuConfigError(f"{effective_path}: [audit].breakpoints must be strings")

  measure_scale = merged["measure"].get("scale", 4)
  if measure_scale not in {4, 8}:
    raise NuuConfigError(f"{effective_path}: [measure].scale must be 4 or 8")
  tol = merged["measure"].get("tolerance", 30)
  if isinstance(tol, bool) or not isinstance(tol, int) or not 0 <= tol <= 255:
    raise NuuConfigError(f"{effective_path}: [measure].tolerance must be 0..255")

  ann_scale = merged["annotate"].get("scale", 2)
  if (
    isinstance(ann_scale, bool)
    or not isinstance(ann_scale, int)
    or not 1 <= ann_scale <= 6
  ):
    raise NuuConfigError(f"{effective_path}: [annotate].scale must be 1..6")

  sheet_rel = str(merged["paths"].get("stylesheet") or "references/style.css").strip()
  sheet_cand = (
    Path(sheet_rel) if Path(sheet_rel).is_absolute() else (project_dir / sheet_rel)
  )
  stylesheet_path = (
    sheet_cand.resolve() if sheet_cand.is_file() else DEFAULT_STYLESHEET.resolve()
  )

  return {
    "path": str(effective_path),
    "local_path": str(found) if found is not None else None,
    "used_bundled": used_bundled,
    "dir": project_dir,
    "config": merged,
    "stylesheet_path": stylesheet_path,
    "severities": severities,
    "warnings": warnings,
  }


def check_severities(
  project: Path | None = None,
  config_path: Path | None = None,
) -> dict[str, str]:
  return dict(load_nuu_config(project, config_path)["severities"])


def parse_viewport_spec(
  spec: str | None,
  viewports: Sequence[Mapping[str, Any]] = DEFAULT_VIEWPORTS,
) -> dict[str, Any]:
  active_vps = list(viewports) if viewports else list(DEFAULT_VIEWPORTS)
  if spec is None or not str(spec).strip():
    return dict(active_vps[0])
  m = re.fullmatch(r"\s*(\d+)x(\d+)\s*", str(spec), re.IGNORECASE)
  if not m:
    raise ValueError(f"Invalid viewport {spec!r} (expected positive <width>x<height>)")
  w, h = int(m.group(1)), int(m.group(2))
  if w <= 0 or h <= 0:
    raise ValueError(f"Viewport dimensions must be positive: {spec!r}")
  for vp in active_vps:
    if int(vp["width"]) == w and int(vp["height"]) == h:
      return dict(vp)
  is_mob = w <= 480 or h <= 384
  return {"width": w, "height": h, "mobile": is_mob, "touch": is_mob, "report": False}


def enrich_finding_rule(
  finding: dict[str, Any],
  severities: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  code = str(finding.get("rule") or finding.get("id") or "")
  if code:
    finding.setdefault(
      "rule_description", RULE_DESCRIPTIONS.get(code, f"House rule {code}")
    )
    sev_map = severities or DEFAULT_RULE_SEVERITIES
    finding["severity"] = sev_map.get(code, DEFAULT_RULE_SEVERITIES.get(code, "error"))
  return finding


def contained(root: Path, name: str | Path) -> Path:
  path = (Path(root) / name).resolve()
  if not path.is_relative_to(Path(root).resolve()):
    raise ValueError("path leaves the selected project")
  return path


def files(root: Path, suffixes: set[str]) -> Iterable[Path]:
  root_resolved = Path(root).resolve()
  if not root_resolved.is_dir():
    return
  for path in sorted(root_resolved.rglob("*")):
    relative = path.relative_to(root_resolved)
    if any(part in SKIP for part in relative.parts) or path.is_symlink():
      continue
    if path.is_file() and path.suffix.lower() in suffixes:
      yield path


def collect_nonuu_markers(
  path: Path,
  lines: Sequence[str],
) -> tuple[set[str], list[set[str]], list[str], list[dict[str, Any]]]:
  file_ids: set[str] = set()
  line_ids: list[set[str]] = []
  marks: list[str] = []
  suppressions: list[dict[str, Any]] = []
  for number, line in enumerate(lines, 1):
    suppressed: set[str] = set()
    for marker in NONUU_MARKER_RE.finditer(line):
      raw_codes = [
        tok.strip() for tok in re.split(r"[,\s]+", marker.group(1)) if tok.strip()
      ]
      before = line[: marker.start()].strip()
      is_standalone_header = (
        number <= 3 and not re.sub(r"^(?:#|/\*|<!--|//)\s*", "", before).strip()
      )
      for code in raw_codes:
        scope = "unknown"
        if code in KNOWN_NUU_RULES:
          suppressed.add(code)
          scope = "file" if is_standalone_header else "line"
          if scope == "file":
            file_ids.add(code)
        mark_str = f"{path}:{number}: {code} ({scope})"
        marks.append(mark_str)
        suppressions.append(
          {
            "path": str(path),
            "line": number,
            "rule": code,
            "scope": scope,
            "source": line.strip(),
            "mark": mark_str,
          }
        )
    line_ids.append(suppressed)
  return file_ids, line_ids, marks, suppressions


def collect_deviation_markers(
  path: Path,
  lines: Sequence[str],
) -> list[dict[str, Any]]:
  deviations: list[dict[str, Any]] = []
  for number, line in enumerate(lines, 1):
    for match in DEVIATION_MARKER_RE.finditer(line):
      reason = (match.group(1) or "").strip()
      if not reason:
        continue
      mark_str = f"{path}:{number}: nuu-deviation: {reason}"
      deviations.append(
        {
          "reason": reason,
          "path": str(path),
          "line": number,
          "source": line.strip(),
          "mark": mark_str,
        }
      )
  return deviations


def collect_recipe_markers(
  path: Path,
  lines: Sequence[str],
  file_suppressed: set[str] | None = None,
  line_suppressed: Sequence[set[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
  recipes: list[dict[str, Any]] = []
  findings: list[dict[str, Any]] = []
  f_supp = file_suppressed or set()
  for number, line in enumerate(lines, 1):
    l_supp = (
      line_suppressed[number - 1]
      if line_suppressed and 0 < number <= len(line_suppressed)
      else set()
    )
    for match in RECIPE_MARKER_RE.finditer(line):
      slugs = [
        tok.strip() for tok in re.split(r"[,\s]+", match.group(1)) if tok.strip()
      ]
      for slug in slugs:
        recipe_file = RECIPES_DIR / f"{slug}.md"
        exists = recipe_file.is_file()
        recipes.append(
          {
            "slug": slug,
            "valid": exists,
            "path": str(path),
            "line": number,
            "mark": f"{path}:{number}: nuurecipe: {slug}",
          }
        )
        if not exists and "RCP001" not in (f_supp | l_supp):
          findings.append(
            {
              "rule": "RCP001",
              "path": str(path),
              "line": number,
              "column": match.start() + 1,
              "severity": "error",
              "evidence": (
                f"nuurecipe: {slug} has no matching references/recipes/{slug}.md"
              ),
              "criterion": "clause-14",
            }
          )
  return recipes, findings


def collect_all_markers(
  root: Path,
  severities: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  suppressions: list[dict[str, Any]] = []
  deviations: list[dict[str, Any]] = []
  recipes: list[dict[str, Any]] = []
  findings: list[dict[str, Any]] = []
  marks: list[str] = []
  for path in files(root, MARKER_SCAN_SUFFIXES):
    try:
      text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
      continue
    lines = text.splitlines()
    f_ids, l_ids, f_marks, f_supps = collect_nonuu_markers(path, lines)
    marks.extend(f_marks)
    suppressions.extend(f_supps)
    deviations.extend(collect_deviation_markers(path, lines))
    rec_items, rec_findings = collect_recipe_markers(path, lines, f_ids, l_ids)
    recipes.extend(rec_items)
    findings.extend(rec_findings)
  for item in findings:
    enrich_finding_rule(item, severities)
  return {
    "marks": marks,
    "suppressions": suppressions,
    "deviations": deviations,
    "recipes": recipes,
    "findings": findings,
  }


def terms(text: str) -> list[str]:
  return re.findall(r"\w+", text.casefold())


def search(
  query: str,
  dataset: str | None = None,
  stack: str | None = None,
  limit: int = 10,
) -> dict[str, Any]:
  """Rank vendored catalog records across the 13 datasets and 22 stacks."""
  if dataset and dataset not in VALID_DATASETS:
    valid = ", ".join(sorted(VALID_DATASETS))
    raise ValueError(f"Unknown dataset {dataset!r} (expected one of: {valid})")
  if stack and stack not in VALID_STACKS:
    valid = ", ".join(sorted(VALID_STACKS))
    raise ValueError(f"Unknown stack {stack!r} (expected one of: {valid})")
  if not 1 <= int(limit) <= 100:
    raise ValueError("limit must be from 1 through 100")

  rows: list[tuple[str, int, dict[str, Any], Counter[str]]] = []
  for path in sorted(CATALOG.rglob("*.csv")):
    name = path.relative_to(CATALOG).with_suffix("").as_posix()
    if dataset and name != dataset:
      continue
    if stack and name != f"stacks/{stack}":
      continue
    with path.open(encoding="utf-8-sig", newline="") as stream:
      for line, row in enumerate(csv.DictReader(stream), 2):
        values = " ".join(str(value) for value in row.values() if value is not None)
        rows.append((name, line, row, Counter(terms(values))))
  requested = set(terms(query))
  frequencies = Counter(word for _, _, _, words in rows for word in words)
  average = sum(sum(words.values()) for _, _, _, words in rows) / max(len(rows), 1)
  ranked: list[dict[str, Any]] = []
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
  ranked.sort(key=lambda item: (-item["score"], item["dataset"], item["line"]))
  matched = [item for item in ranked if item["score"] > 0]
  selected = (matched or ranked)[:limit]
  source_meta = json.loads((CATALOG / "source.json").read_text(encoding="utf-8"))
  return {
    "query": query,
    "fallback": not bool(matched),
    "results": selected,
    "source": source_meta,
    "coverage": {"records": len(rows)},
  }


def extract(root: Path) -> dict[str, Any]:
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
    "status": "proposal",
  }


class SourcePage(HTMLParser):
  RAW_TEXT_TAGS = ("script", "style", "code", "pre", "textarea", "template")
  VAGUE_BUTTON_LABELS = frozenset({"yes", "no", "okay", "ok"})
  PRIMARY_BUTTON_CLASSES = frozenset(
    {"primary", "button--accent", "btn-primary", "button-primary"}
  )
  VOID_TAGS = frozenset(
    {
      "area",
      "base",
      "br",
      "col",
      "embed",
      "hr",
      "img",
      "input",
      "link",
      "meta",
      "param",
      "source",
      "track",
      "wbr",
    }
  )

  def __init__(self, path: Path) -> None:
    super().__init__(convert_charrefs=True)
    self.path = path
    self.findings: list[dict[str, Any]] = []
    self.labels: set[str] = set()
    self.ids: set[str] = set()
    self.controls: list[tuple[dict[str, str | None], bool, tuple[int, int]]] = []
    self.label_depth = 0
    self.raw_text_depth = 0
    self.tag_depth = 0
    self.card_scopes: list[tuple[str, int, list[tuple[int, int]]]] = []
    self.button_stack: list[
      tuple[dict[str, str | None], tuple[int, int], list[str]]
    ] = []

  def finding(
    self,
    rule: str,
    criterion: str,
    evidence: str,
    position: tuple[int, int],
  ) -> None:
    line, column = position
    self.findings.append(
      {
        "rule": rule,
        "path": str(self.path),
        "line": line,
        "column": column + 1,
        "severity": DEFAULT_RULE_SEVERITIES[rule],
        "evidence": evidence,
        "criterion": criterion,
      }
    )

  @classmethod
  def _is_primary_button(cls, attrs: Mapping[str, str | None]) -> bool:
    classes = set((attrs.get("class") or "").split())
    if classes & cls.PRIMARY_BUTTON_CLASSES:
      return True
    return (attrs.get("data-variant") or "").strip().lower() == "primary"

  def _check_button_action(
    self,
    attrs: Mapping[str, str | None],
    label_text: str,
    pos: tuple[int, int],
  ) -> None:
    cleaned = re.sub(r"[.!?\s]+$", "", label_text.strip())
    if cleaned and cleaned.casefold() in self.VAGUE_BUTTON_LABELS:
      self.finding(
        "HRC001",
        "clause-23",
        f"Button uses vague label '{label_text.strip()}' instead of an explicit action",
        pos,
      )
    if self.card_scopes and self._is_primary_button(attrs):
      self.card_scopes[-1][2].append(pos)

  def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
    attrs = dict(attributes)
    pos = self.getpos()
    classes = set((attrs.get("class") or "").split())
    role = (attrs.get("role") or "").strip().lower()
    if tag not in self.VOID_TAGS:
      self.tag_depth += 1
      if tag == "dialog" or role in {"dialog", "alertdialog"} or "card" in classes:
        self.card_scopes.append((tag, self.tag_depth, []))
    if tag in self.RAW_TEXT_TAGS:
      self.raw_text_depth += 1
    if attrs.get("id"):
      self.ids.add(str(attrs["id"]))
    if tag == "label":
      self.label_depth += 1
      if attrs.get("for"):
        self.labels.add(str(attrs["for"]))
    if tag == "img" and "alt" not in attrs:
      self.finding("ACC001", "clause-9", "Image has no alt attribute", pos)
    if tag == "button":
      self.button_stack.append((attrs, pos, []))
    if tag in {"input", "select", "textarea"}:
      self.controls.append((attrs, bool(self.label_depth), pos))
      if tag == "input" and (attrs.get("type") or "").lower() in {
        "button",
        "submit",
        "reset",
      }:
        self._check_button_action(
          attrs, str(attrs.get("value") or attrs.get("aria-label") or ""), pos
        )

  def handle_endtag(self, tag: str) -> None:
    if tag == "button" and self.button_stack:
      attrs, pos, chunks = self.button_stack.pop()
      label = "".join(chunks).strip() or (attrs.get("aria-label") or "").strip()
      if not label and not (attrs.get("title") or "").strip():
        refs = set((attrs.get("aria-labelledby") or "").split()) & self.ids
        if not refs:
          self.finding(
            "ACC002", "clause-9", "Button has no source naming evidence", pos
          )
      self._check_button_action(attrs, label, pos)
    if (
      self.card_scopes
      and self.card_scopes[-1][0] == tag
      and self.card_scopes[-1][1] == self.tag_depth
    ):
      _, _, primaries = self.card_scopes.pop()
      if len(primaries) > 1:
        self.finding(
          "HRC001",
          "clause-23",
          f"Card or dialog contains {len(primaries)} primary buttons (at most 1 allowed)",
          primaries[1],
        )
    if tag not in self.VOID_TAGS:
      self.tag_depth = max(0, self.tag_depth - 1)
    if tag in self.RAW_TEXT_TAGS:
      self.raw_text_depth = max(0, self.raw_text_depth - 1)
    if tag == "label":
      self.label_depth = max(0, self.label_depth - 1)

  def handle_data(self, data: str) -> None:
    if self.button_stack and data:
      self.button_stack[-1][2].append(data)
    if self.raw_text_depth > 0 or not data:
      return
    match = RAW_UI_TIMESTAMP_RE.search(data)
    if match:
      self.finding(
        "HRC201",
        "clause-24",
        f"Raw or unlocalized timestamp in UI text: {match[0]}",
        self.getpos(),
      )
    placeholder = PLACEHOLDER_COPY_RE.search(data)
    if placeholder:
      self.finding(
        "HRC203",
        "clause-25",
        f"Placeholder copy in UI markup: {placeholder[0]}",
        self.getpos(),
      )

  def finish(self) -> None:
    while self.card_scopes:
      _, _, primaries = self.card_scopes.pop()
      if len(primaries) > 1:
        self.finding(
          "HRC001",
          "clause-23",
          f"Card or dialog contains {len(primaries)} primary buttons (at most 1 allowed)",
          primaries[1],
        )
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
          "ACC002", "clause-9", "Control has no source naming evidence", position
        )


def _has_custom_focus_ring(clean_css: str) -> bool:
  for rule_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean_css):
    selector, body = rule_match[1], rule_match[2]
    if not re.search(r":focus(?:-visible|-within)?\b", selector) or ":not(" in selector:
      continue
    for decl in body.split(";"):
      if ":" not in decl:
        continue
      prop, val = (part.strip().lower() for part in decl.split(":", 1))
      val_clean = re.sub(r"\s*!important\s*$", "", val).strip()
      if prop in {"outline", "box-shadow"} and val_clean not in {
        "",
        "none",
        "0",
        "0px",
      }:
        return True
      if prop in {"border", "border-color"} and val_clean not in {
        "",
        "none",
        "0",
        "0px",
        "transparent",
      }:
        return True
  return False


def _has_design_doc(root: Path, design_doc_rel: str = "docs/design.md") -> bool:
  rel = (design_doc_rel or "docs/design.md").strip()
  rel_path = Path(rel)
  if rel_path.is_absolute():
    return rel_path.is_file()
  cur = Path(root).resolve()
  if cur.is_file():
    cur = cur.parent
  while True:
    if (cur / rel).is_file():
      return True
    if rel == "docs/design.md":
      docs_dir = cur / "docs"
      if docs_dir.is_dir() and any(
        p.is_file() and not p.is_symlink() for p in docs_dir.rglob("design.md")
      ):
        return True
    if (
      (cur / ".git").exists()
      or cur.parent == cur
      or cur == Path("/tmp")
      or cur.parent == Path("/tmp")
    ):
      break
    cur = cur.parent
  return False


def check_icons(
  root: Path,
  severities: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  counts: Counter[str] = Counter()
  findings: list[dict[str, Any]] = []
  license_files = [
    path.relative_to(root).as_posix()
    for path in sorted(root.rglob("*"))
    if path.is_file()
    and not path.is_symlink()
    and not any(part in SKIP for part in path.relative_to(root).parts)
    and re.search(r"(?:licen[cs]e|copying)", path.name, re.IGNORECASE)
  ]
  inline_license_seen = False
  for path in files(root, {".html", ".js", ".jsx", ".ts", ".tsx", ".md"}):
    text = path.read_text(encoding="utf-8")
    if re.search(
      r"<!--[^>]*\b(?:lucide|simple[\s-]*icons|phosphor)[^>]*\blicense\b[^>]*-->"
      r"|/\*[^*]*\b(?:isc|mit)\s+license\b[^*]*\*/",
      text,
      re.IGNORECASE,
    ):
      inline_license_seen = True
    if path.suffix.lower() == ".md":
      continue
    lines = text.splitlines()
    f_ids, l_ids, _, _ = collect_nonuu_markers(path, lines)
    current: list[dict[str, Any]] = []
    sizes: dict[str, int] = {}
    for svg_match in re.finditer(
      r"<svg\b([^>]*)>([\s\S]*?)</svg>", text, re.IGNORECASE
    ):
      attrs = svg_match[1]
      body = svg_match[2]
      line_no = text.count("\n", 0, svg_match.start()) + 1
      class_match = re.search(
        r'\bclass\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE
      )
      classes = class_match[1].lower() if class_match else ""
      viewbox_match = re.search(
        r'\bviewBox\s*=\s*["\']([^"\']*)["\']', attrs, re.IGNORECASE
      )
      viewbox = viewbox_match[1].strip() if viewbox_match else ""
      stroke_w_match = re.search(
        r'\bstroke-width\s*=\s*["\']([0-9.]+)["\']', attrs, re.IGNORECASE
      )
      stroke_w = float(stroke_w_match[1]) if stroke_w_match else 2.0
      if (
        "lucide" in classes
        or (viewbox == "0 0 24 24" and "stroke=" in attrs and "stroke-linecap" in attrs)
      ) and math.isclose(stroke_w, 2.0, abs_tol=1e-3):
        icon_source = "lucide"
      elif (
        "simple-icon" in classes
        or "markdown-icon" in classes
        or (viewbox == "0 0 24 24" and "<path" in body and "stroke=" not in attrs)
      ):
        icon_source = "simple-icons"
      elif "phosphor" in classes or viewbox == "0 0 256 256":
        icon_source = "phosphor"
      else:
        icon_source = "custom"
        current.append(
          {
            "rule": "ICN002",
            "path": str(path),
            "line": line_no,
            "column": 1,
            "severity": "review",
            "evidence": "Inline <svg> outside Lucide -> Simple Icons -> Phosphor or stroke != 2",
            "criterion": "clause-1",
          }
        )
      counts[icon_source] += 1
      w_match = re.search(r'\bwidth\s*=\s*["\']([0-9.]+)["\']', attrs, re.IGNORECASE)
      h_match = re.search(r'\bheight\s*=\s*["\']([0-9.]+)["\']', attrs, re.IGNORECASE)
      if w_match and h_match:
        sizes.setdefault(f"{w_match[1]}x{h_match[1]}", line_no)
    if len(sizes) > 1:
      first_line = min(sizes.values())
      current.append(
        {
          "rule": "ICN101",
          "path": str(path),
          "line": first_line,
          "column": 1,
          "severity": "review",
          "evidence": f"Mixed inline icon sizes on surface: {', '.join(sorted(sizes))}",
          "criterion": "clause-2",
        }
      )
    for finding in current:
      ln = finding["line"]
      ignored = f_ids | (l_ids[ln - 1] if 0 < ln <= len(l_ids) else set())
      if finding["rule"] not in ignored:
        findings.append(enrich_finding_rule(finding, severities))

  for path in files(root, {".css"}):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    f_ids, l_ids, _, _ = collect_nonuu_markers(path, lines)
    clean = re.sub(
      r"/\*[\s\S]*?\*/",
      lambda m: "\n" * m.group(0).count("\n"),
      text,
    )
    for rule_match in re.finditer(r"([^{};]+)\{([^}]*)\}", clean):
      selector = rule_match[1].strip()
      body = rule_match[2]
      is_control = bool(
        re.search(
          r"(?:^|[\s,>+~:])(?:select|button|input|summary)\b|\[role=",
          selector,
          re.IGNORECASE,
        )
      )
      prop_match = re.search(
        r"\b(?:background(?:-image)?\s*:[^;]*(?:(?:linear|radial|conic)-gradient|url)\s*\(|(?:-webkit-)?mask(?:-image)?\s*:[^;]*url\s*\()",
        body,
        re.IGNORECASE,
      )
      if not prop_match:
        continue
      if not is_control and not re.search(
        r"(?:-webkit-)?mask(?:-image)?\s*:[^;]*url\s*\(",
        prop_match.group(0),
        re.IGNORECASE,
      ):
        continue
      line_no = clean.count("\n", 0, rule_match.start(2) + prop_match.start()) + 1
      rule_line = clean.count("\n", 0, rule_match.start(1)) + 1
      ignored = set(f_ids)
      for check_ln in range(max(1, rule_line), min(len(lines), line_no) + 1):
        ignored.update(l_ids[check_ln - 1])
      counts["custom"] += 1
      if "ICN002" not in ignored:
        findings.append(
          enrich_finding_rule(
            {
              "rule": "ICN002",
              "path": str(path),
              "line": line_no,
              "column": 1,
              "severity": "review",
              "evidence": (
                "CSS background-image/mask-image control icon outside "
                "Lucide -> Simple Icons -> Phosphor"
              ),
              "criterion": "clause-1",
            },
            severities,
          )
        )
  if sum(counts.values()) > 0 and not license_files and not inline_license_seen:
    findings.append(
      enrich_finding_rule(
        {
          "rule": "ICN001",
          "path": str(root),
          "line": 1,
          "column": 1,
          "severity": "review",
          "evidence": "Inline icons present without a companion license file or inline license comment",
          "criterion": "clause-1",
        },
        severities,
      )
    )
  return {
    "status": "checked",
    "ladder": ["lucide", "simple-icons", "phosphor"],
    "counts": dict(counts),
    "licenses": license_files,
    "findings": findings,
  }


def validate_assets_config(
  root: Path,
  assets_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
  findings: list[dict[str, Any]] = []
  for index, row in enumerate(assets_entries):
    if not isinstance(row, dict):
      raise TypeError("[[assets]] entry must be a table")
    missing = [
      name
      for name in ("path", "source", "license")
      if not isinstance(row.get(name), str) or not str(row[name]).strip()
    ]
    if missing:
      findings.append(
        {
          "asset": index,
          "reason": "missing_metadata",
          "fields": missing,
          "evidence": f"[[assets]][{index}] missing required fields: {', '.join(missing)}",
        }
      )
      continue
    path = contained(root, str(row["path"]))
    if not path.is_file():
      findings.append(
        {
          "asset": index,
          "reason": "missing_file",
          "path": str(path),
          "evidence": f"[[assets]][{index}] file does not exist: {row['path']}",
        }
      )
      continue
    if path.suffix.lower() == ".svg":
      svg_text = path.read_text(encoding="utf-8")
      if re.search(
        r"<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|\bon\w+\s*=",
        svg_text,
        re.IGNORECASE,
      ):
        findings.append(
          {
            "asset": index,
            "reason": "active_svg",
            "path": str(path),
            "evidence": f"[[assets]][{index}] SVG contains active content: {row['path']}",
          }
        )
  return {"assets": list(assets_entries), "findings": findings}


def validated_url(url: str) -> str:
  parsed = urllib.parse.urlsplit(str(url).strip())
  if (
    parsed.scheme not in {"http", "https"}
    or not parsed.hostname
    or parsed.username
    or parsed.password
  ):
    raise ValueError("URL must be HTTP(S) without embedded credentials")
  return str(url).strip()


class AssetRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, request, fp, code, message, headers, newurl):
    validated_url(newurl)
    return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_asset(
  root: Path,
  url: str,
  destination: str | Path,
  authorized: bool,
) -> dict[str, Any]:
  if not authorized:
    raise ValueError("asset fetch requires -A / --authorize-fetch")
  validated_url(url)
  dest_path = Path(destination)
  try:
    path = (
      contained(root, dest_path)
      if not dest_path.is_absolute()
      else contained(root, dest_path.relative_to(root.resolve()))
      if dest_path.resolve().is_relative_to(root.resolve())
      else (_ for _ in ()).throw(RuntimeError("path leaves the selected project"))
    )
  except ValueError as exc:
    raise RuntimeError(str(exc)) from exc
  if path.exists():
    raise FileExistsError("asset destination already exists")
  with urllib.request.build_opener(AssetRedirect()).open(url, timeout=20) as response:
    content = response.read(10 * 1024 * 1024 + 1)
  if len(content) > 10 * 1024 * 1024:
    raise RuntimeError("asset exceeds the 10 MiB fetch limit")
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("xb") as stream:
    stream.write(content)
  rel = path.relative_to(root.resolve()).as_posix()
  return {
    "path": str(path),
    "relative_path": rel,
    "source": url,
    "sha256": hashlib.sha256(content).hexdigest(),
    "bytes": len(content),
  }


def lint(
  root: Path,
  *,
  config_path: Path | None = None,
  sheet_path: Path | None = None,
  scale: float | None = None,
) -> dict[str, Any]:
  cfg_info = load_nuu_config(root, config_path)
  project_dir = Path(root).resolve() if root is not None else cfg_info["dir"]
  severities = cfg_info["severities"]
  effective_scale = int(scale or cfg_info["config"]["measure"].get("scale", 4))
  effective_sheet = (
    Path(sheet_path).resolve()
    if sheet_path is not None
    else cfg_info["stylesheet_path"]
  )
  if not effective_sheet.is_file():
    raise FileNotFoundError(f"Stylesheet does not exist: {effective_sheet}")

  findings: list[dict[str, Any]] = []
  coverage: list[dict[str, Any]] = []
  nonuu_by_path: dict[Path, tuple[set[str], list[set[str]]]] = {}
  list_rules: list[tuple[Path, int]] = []
  list_reset = {"indent": False, "padding": False, "position": False}

  marker_bundle = collect_all_markers(project_dir, severities)
  findings.extend(marker_bundle["findings"])

  for path in files(project_dir, {".html", ".css", ".js", ".jsx", ".ts", ".tsx"}):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    file_ids, line_ids, _, _ = collect_nonuu_markers(path, lines)
    nonuu_by_path[path] = (file_ids, line_ids)
    supported = path.suffix.lower() in {".html", ".css"}
    coverage.append(
      {
        "path": str(path),
        "status": "checked" if supported else "not_checked",
        "scope": "selected source patterns" if supported else "rendered check required",
      }
    )
    current: list[dict[str, Any]] = []
    if path.suffix.lower() == ".html":
      parser = SourcePage(path)
      parser.feed(text)
      parser.close()
      parser.finish()
      current.extend(parser.findings)
    if path.suffix.lower() == ".css":
      clean = re.sub(
        r"/\*.*?\*/",
        lambda match: "".join("\n" if ch == "\n" else " " for ch in match[0]),
        text,
        flags=re.DOTALL,
      )
      list_selector = re.compile(
        r"(?:^|[\s>+~,:()])(?:ul|ol|li)(?=$|[\s.#:\[>+~),])", re.IGNORECASE
      )
      list_container = re.compile(
        r"(?:^|[\s>+~,:()])(?:ul|ol)(?=$|[\s.#:\[>+~),])", re.IGNORECASE
      )
      for list_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean):
        selector_text = list_match[1]
        if not list_selector.search(selector_text):
          continue
        declarations: dict[str, str] = {}
        for declaration in list_match[2].split(";"):
          if ":" not in declaration:
            continue
          name, value = declaration.split(":", 1)
          declarations[name.strip().lower()] = re.sub(
            r"\s*!important\s*$", "", value.strip(), flags=re.IGNORECASE
          ).lower()
        list_properties = {
          "list-style",
          "list-style-position",
          "list-style-type",
          "padding",
          "padding-inline-start",
          "padding-left",
          "padding-right",
          "--indent",
        }
        if not list_properties.intersection(declarations):
          continue
        rule_line = text.count("\n", 0, list_match.start(1)) + 1
        list_rules.append((path, rule_line))
        if list_container.search(selector_text):
          list_reset["indent"] |= declarations.get("--indent") == "2ch"
          list_reset["padding"] |= declarations.get("padding-inline-start") == "2ch"
          list_reset["position"] |= declarations.get("list-style-position") == "outside"
        inside_match = re.search(
          r"(?:list-style-position\s*:\s*inside\b|list-style\s*:[^;}]*\binside\b)",
          list_match[2],
          re.IGNORECASE,
        )
        if inside_match:
          line = text.count("\n", 0, list_match.start(2) + inside_match.start()) + 1
          current.append(
            {
              "rule": "TYP202",
              "path": str(path),
              "line": line,
              "column": 1,
              "severity": "error",
              "evidence": "list-style-position: inside",
              "criterion": "clause-15",
            }
          )
        for indent_match in re.finditer(
          r"(?<![\w-])(--indent|padding-inline-start)\s*:\s*([^;}]+)",
          list_match[2],
          re.IGNORECASE,
        ):
          value = re.sub(
            r"\s*!important\s*$", "", indent_match[2].strip(), flags=re.IGNORECASE
          ).lower()
          if value == "2ch":
            continue
          line = text.count("\n", 0, list_match.start(2) + indent_match.start(1)) + 1
          current.append(
            {
              "rule": "TYP202",
              "path": str(path),
              "line": line,
              "column": 1,
              "severity": "error",
              "evidence": f"{indent_match[1]}: {indent_match[2].strip()}",
              "criterion": "clause-15",
            }
          )
      if not _has_custom_focus_ring(clean):
        for match in re.finditer(
          r"outline\s*:\s*(?:none|0(?:px)?)\s*(?:!important\s*)?[;}]", clean
        ):
          current.append(
            {
              "rule": "ACC201",
              "path": str(path),
              "line": text.count("\n", 0, match.start()) + 1,
              "column": 1,
              "severity": "error",
              "evidence": match[0],
              "criterion": "clause-9",
            }
          )
      for rule_match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", clean):
        selector_text = rule_match[1]
        if not re.search(
          r"(?::hover|:focus(?:-visible|-within)?|:active|:disabled|\[disabled\b|\[aria-busy\b)",
          selector_text,
        ):
          continue
        body_start = rule_match.start(2)
        for prop_match in re.finditer(
          r"(?:^|[;\s])((?:(?:min|max)-)?(?:width|height)|padding(?:-(?:top|right|bottom|left|inline|block))?|border(?:-(?:top|right|bottom|left))?-width|margin(?:-(?:top|right|bottom|left|inline|block))?|font-size|line-height)\s*:\s*([^;}]+)",
          rule_match[2],
        ):
          prop_pos = body_start + prop_match.start(1)
          current.append(
            {
              "rule": "ACC204",
              "path": str(path),
              "line": text.count("\n", 0, prop_pos) + 1,
              "column": 1,
              "severity": "error",
              "evidence": f"{prop_match[1]}: {prop_match[2].strip()}",
              "criterion": "clause-9",
            }
          )
    if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
      clean_js = re.sub(
        r"/\*.*?\*/",
        lambda match: "".join("\n" if ch == "\n" else " " for ch in match[0]),
        text,
        flags=re.DOTALL,
      )
      for number, raw_line in enumerate(clean_js.splitlines(), 1):
        code_part = raw_line.split("//", 1)[0]
        ts_match = UNADJUSTED_JS_TIMESTAMP_RE.search(code_part)
        if ts_match:
          current.append(
            {
              "rule": "HRC201",
              "path": str(path),
              "line": number,
              "column": ts_match.start() + 1,
              "severity": "error",
              "evidence": f"Unlocalized or raw timestamp assignment: {ts_match[0].strip()}",
              "criterion": "clause-24",
            }
          )
        ph_match = PLACEHOLDER_COPY_RE.search(code_part)
        if ph_match:
          current.append(
            {
              "rule": "HRC203",
              "path": str(path),
              "line": number,
              "column": ph_match.start() + 1,
              "severity": "error",
              "evidence": f"Placeholder copy in script: {ph_match[0]}",
              "criterion": "clause-25",
            }
          )
    for finding in current:
      ln = finding["line"]
      ignored = file_ids | (line_ids[ln - 1] if 0 < ln <= len(line_ids) else set())
      if finding["rule"] not in ignored:
        findings.append(finding)

  if list_rules and not all(list_reset.values()):
    reported_paths = {
      finding["path"] for finding in findings if finding.get("rule") == "TYP202"
    }
    for list_path, list_line in list_rules:
      if str(list_path) in reported_paths:
        continue
      file_ids, line_ids = nonuu_by_path[list_path]
      ignored = file_ids | (
        line_ids[list_line - 1] if 0 < list_line <= len(line_ids) else set()
      )
      if "TYP202" in ignored:
        continue
      findings.append(
        {
          "rule": "TYP202",
          "path": str(list_path),
          "line": list_line,
          "column": 1,
          "severity": "error",
          "evidence": (
            "List CSS lacks --indent: 2ch, padding-inline-start: 2ch, "
            "or list-style-position: outside"
          ),
          "criterion": "clause-15",
        }
      )
      break

  design_doc_rel = str(cfg_info["config"]["paths"].get("doc") or "docs/design.md")
  design_doc_path = (
    Path(design_doc_rel)
    if Path(design_doc_rel).is_absolute()
    else (project_dir / design_doc_rel)
  )
  if not _has_design_doc(project_dir, design_doc_rel):
    findings.append(
      {
        "rule": "DOC001",
        "path": str(design_doc_path),
        "line": 1,
        "column": 1,
        "severity": "review",
        "evidence": f"Missing {design_doc_rel}",
        "criterion": "clause-11",
      }
    )

  icons_res = check_icons(project_dir, severities)
  findings.extend(icons_res["findings"])

  assets_res = validate_assets_config(project_dir, cfg_info["config"].get("assets", []))
  for asset_err in assets_res["findings"]:
    findings.append(
      {
        "rule": "ICN001",
        "path": str(asset_err.get("path") or cfg_info["path"]),
        "line": 1,
        "column": 1,
        "severity": "error",
        "evidence": asset_err["evidence"],
        "criterion": "assets",
      }
    )

  for finding in findings:
    if finding.get("criterion") != "assets":
      enrich_finding_rule(finding, severities)
    else:
      finding.setdefault("rule_description", RULE_DESCRIPTIONS["ICN001"])

  design_res = extract(project_dir)
  return {
    "findings": findings,
    "icons": icons_res,
    "assets": assets_res,
    "design": design_res,
    "tokens": design_res["tokens"],
    "breakpoints": design_res["breakpoints"],
    "components": design_res["components"],
    "marks": marker_bundle["marks"],
    "suppressions": marker_bundle["suppressions"],
    "deviations": marker_bundle["deviations"],
    "recipes": marker_bundle["recipes"],
    "scale": effective_scale,
    "stylesheet": str(effective_sheet),
    "coverage": coverage,
  }


_LIVE_AUDIT_PROBE_JS = r"""
() => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const overflow = document.documentElement.scrollWidth > vw + 1;
  const skipTags = new Set([
    'SCRIPT', 'STYLE', 'NOSCRIPT', 'META', 'LINK', 'HEAD', 'TITLE', 'BR', 'WBR', 'TEMPLATE',
  ]);
  const svgTags = new Set([
    ' SVG ', 'PATH', 'CIRCLE', 'RECT', 'LINE', 'POLYLINE', 'POLYGON',
    'G', 'DEFS', 'USE', 'CLIPPATH', 'MASK', 'LINEARGRADIENT', 'RADIALGRADIENT', 'STOP',
  ]);

  function parseRgba(str) {
    if (!str) return [0, 0, 0, 0];
    const m = str.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?\s*\)/i);
    if (!m) return [0, 0, 0, 0];
    return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])];
  }
  function blend(fg, bg) {
    const a = fg[3] + bg[3] * (1 - fg[3]);
    if (a <= 0) return [0, 0, 0, 0];
    return [
      (fg[0] * fg[3] + bg[0] * bg[3] * (1 - fg[3])) / a,
      (fg[1] * fg[3] + bg[1] * bg[3] * (1 - fg[3])) / a,
      (fg[2] * fg[3] + bg[2] * bg[3] * (1 - fg[3])) / a,
      a,
    ];
  }
  function effectiveBg(el) {
    let acc = [0, 0, 0, 0];
    let cur = el;
    const chain = [];
    while (cur && cur.nodeType === 1) {
      chain.unshift(cur);
      cur = cur.parentElement;
    }
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    acc = dark ? [15, 15, 18, 1] : [255, 255, 255, 1];
    for (const node of chain) {
      const bg = parseRgba(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0) acc = blend(bg, acc);
    }
    return acc;
  }
  function relLum(rgb) {
    const ch = rgb.slice(0, 3).map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  }
  function contrastRatio(c1, c2) {
    const l1 = relLum(c1);
    const l2 = relLum(c2);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return '?';
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    const cls = Array.from(el.classList || []).slice(0, 2).join('.');
    return cls ? `${tag}.${cls}` : tag;
  }
  function isVisible(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function accName(el) {
    const al = (el.getAttribute('aria-label') || '').trim();
    if (al) return al;
    const lby = (el.getAttribute('aria-labelledby') || '').trim();
    if (lby) {
      const txt = lby.split(/\s+/).map((id) => {
        const ref = document.getElementById(id);
        return ref ? (ref.textContent || '').trim() : '';
      }).join(' ').trim();
      if (txt) return txt;
    }
    if (el.labels && el.labels.length) {
      const ltxt = Array.from(el.labels).map((l) => (l.textContent || '').trim()).join(' ').trim();
      if (ltxt) return ltxt;
    }
    const txt = (el.innerText || el.textContent || '').trim();
    if (txt) return txt;
    return (el.getAttribute('title') || el.getAttribute('placeholder') || el.value || '').trim();
  }

  const findings = [];
  const elements = [];
  const idCounts = new Map();
  for (const node of document.querySelectorAll('[id]')) {
    const id = node.getAttribute('id');
    if (id) idCounts.set(id, (idCounts.get(id) || 0) + 1);
  }
  for (const [id, count] of idCounts.entries()) {
    if (count > 1) {
      findings.push({
        rule: 'ACC006',
        selector: `#${id}`,
        evidence: `Duplicate id="${id}" (${count} elements)`,
      });
    }
  }

  let prevHeading = 0;
  const allNodes = Array.from(document.querySelectorAll('body *'));
  for (const el of allNodes) {
    const tag = el.tagName.toUpperCase();
    if (skipTags.has(tag) || (el instanceof SVGElement && tag !== 'SVG')) continue;
    for (const attr of ['aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-owns']) {
      const raw = (el.getAttribute(attr) || '').trim();
      if (!raw) continue;
      for (const targetId of raw.split(/\s+/)) {
        if (targetId && !document.getElementById(targetId)) {
          findings.push({
            rule: 'ACC004',
            selector: selectorFor(el),
            evidence: `${attr} references missing id "${targetId}"`,
          });
        }
      }
    }
    if (!isVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const sel = selectorFor(el);

    if (/^H[1-6]$/.test(tag)) {
      const lvl = Number(tag.slice(1));
      if (prevHeading > 0 && lvl > prevHeading + 1) {
        findings.push({
          rule: 'ACC007',
          selector: sel,
          evidence: `Heading jumps from h${prevHeading} to h${lvl}`,
        });
      }
      prevHeading = lvl;
    }

    const interactiveSel = [
      'a[href], button, input:not([type="hidden"]), select, textarea',
      'summary, [role="button"], [role="link"], [role="tab"]',
    ].join(', ');
    const isInteractive = el.matches(interactiveSel);
    if (isInteractive) {
      if (!accName(el)) {
        findings.push({
          rule: 'ACC003',
          selector: sel,
          evidence: `${sel} has an empty accessible name`,
        });
      }
      const nestedParent = el.parentElement
        ? el.parentElement.closest('a[href], button, [role="button"], [role="link"]')
        : null;
      if (nestedParent) {
        findings.push({
          rule: 'ACC005',
          selector: sel,
          evidence: `${sel} is nested inside interactive ${selectorFor(nestedParent)}`,
        });
      }
    }

    const directText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => (n.nodeValue || '').trim())
      .join(' ')
      .trim();
    if (directText) {
      if (/\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|\b\d{2}:\d{2}(?::\d{2})?\s*(?:UTC|GMT)\b/.test(directText)) {
        findings.push({
          rule: 'HRC202',
          selector: sel,
          evidence: `Visible raw/UTC timestamp in ${sel}: ${directText.slice(0, 60)}`,
        });
      }
      if (/\b(?:lorem\s+ipsum|dolor\s+sit\s+amet|consectetur\s+adipiscing)\b/i.test(directText)) {
        findings.push({
          rule: 'HRC203',
          selector: sel,
          evidence: `Visible placeholder copy in ${sel}`,
        });
      }
      const fg = blend(parseRgba(cs.color), effectiveBg(el));
      const bg = effectiveBg(el);
      const ratio = contrastRatio(fg, bg);
      const fsPx = parseFloat(cs.fontSize) || 16;
      const fw = parseInt(cs.fontWeight, 10) || 400;
      const large = fsPx >= 24 || (fsPx >= 18.66 && fw >= 700);
      const minRatio = large ? 3.0 : 4.5;
      if (ratio + 0.02 < minRatio) {
        findings.push({
          rule: 'ACC101',
          selector: sel,
          evidence: `${sel} text contrast is ${ratio.toFixed(2)}:1 (min ${minRatio}:1)`,
        });
      }
    }

    if (tag === 'SVG' && el.parentElement) {
      const parent = el.parentElement;
      const siblingText = Array.from(parent.childNodes).some((n) => {
        if (n.nodeType === 3) return (n.nodeValue || '').trim().length > 0;
        return n.nodeType === 1 && n !== el && (n.textContent || '').trim().length > 0;
      });
      if (siblingText) {
        const pcs = getComputedStyle(parent);
        const lh = parseFloat(pcs.lineHeight);
        if (Number.isFinite(lh) && lh > 0 && Math.abs(rect.height - lh) > 1.0) {
          const rh = rect.height.toFixed(1);
          findings.push({
            rule: 'ICN102',
            selector: sel,
            evidence: `${sel} height ${rh}px differs from paired text line-height ${lh.toFixed(1)}px`,
          });
        }
      }
    }

    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'tooltip' || el.matches('[popover]:popover-open, dialog[open], [role="dialog"]')) {
      if (rect.left < -1 || rect.top < -1 || rect.right > vw + 1 || rect.bottom > vh + 1) {
        const coords = [
          Math.round(rect.left), Math.round(rect.top),
          Math.round(rect.width), Math.round(rect.height),
        ].join(',');
        findings.push({
          rule: 'ACC203',
          selector: sel,
          evidence: `${sel} clips viewport (${vw}x${vh}) at (${coords})`,
        });
      }
      if (role === 'tooltip' || el.hasAttribute('popover')) {
        const ownBg = parseRgba(cs.backgroundColor);
        if (ownBg[3] < 0.1 && cs.backgroundImage === 'none') {
          findings.push({
            rule: 'ACC203',
            selector: sel,
            evidence: `${sel} has transparent surface background (${cs.backgroundColor})`,
          });
        }
      }
    }

    if (tag !== 'SVG') {
      const svgs = el.querySelectorAll(':scope > svg');
      const iconOnly = isInteractive && svgs.length > 0 && !(el.innerText || '').trim();
      elements.push({
        selector: sel,
        tag: tag.toLowerCase(),
        role,
        classes: Array.from(el.classList || []),
        text: directText,
        icon_only: iconOnly,
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
          'gap': cs.gap,
          'row-gap': cs.rowGap,
          'column-gap': cs.columnGap,
          'border-radius': cs.borderTopLeftRadius,
          'overflow-x': cs.overflowX,
          'overflow-y': cs.overflowY,
          'text-overflow': cs.textOverflow,
        },
      });
    }
  }
  return { width: vw, height: vh, overflow, findings, elements };
}
"""


def run_live_audit(
  url: str,
  *,
  cfg_info: Mapping[str, Any],
  artifacts_dir: Path,
  executable: str | Path | None = None,
  timeout_sec: int = 20,
) -> dict[str, Any]:
  """Run live Python Playwright audit across configured viewports (0 Node calls)."""
  validated_url(url)
  from measure import check as check_measurements
  from measure import check_density, parse_sheet

  try:
    from playwright.sync_api import sync_playwright
  except ImportError as exc:
    raise RuntimeError(f"RUN102: playwright is not installed: {exc}") from exc

  launch_kwargs = chromium_launch_kwargs(executable)
  out_dir = ensure_artifacts_dir(artifacts_dir)
  viewports = cfg_info["config"]["audit"]["viewports"]
  severities = cfg_info["severities"]
  sheet_tokens = parse_sheet(cfg_info["stylesheet_path"].read_text(encoding="utf-8"))
  scale = int(cfg_info["config"]["measure"].get("scale", 4))

  findings: list[dict[str, Any]] = []
  captures: list[str] = []

  with sync_playwright() as pw:
    try:
      browser_inst = pw.chromium.launch(**launch_kwargs)
    except Exception as exc:
      raise RuntimeError(f"RUN102: Failed to launch Chromium: {exc}") from exc
    try:
      for vp in viewports:
        vw, vh = int(vp["width"]), int(vp["height"])
        variants: list[tuple[str, str]] = [("dark", "no-preference")]
        if vp.get("report"):
          variants.extend([("light", "no-preference"), ("dark", "reduce")])
        for scheme, motion in variants:
          ctx = browser_inst.new_context(
            viewport={"width": vw, "height": vh},
            color_scheme=scheme,
            reduced_motion=motion,
            is_mobile=bool(vp.get("mobile")),
            has_touch=bool(vp.get("touch")),
          )
          page = ctx.new_page()
          runtime_errors: list[str] = []
          page.on(
            "console",
            lambda msg, errs=runtime_errors: (
              errs.append(msg.text) if msg.type == "error" else None
            ),
          )
          page.on("pageerror", lambda err, errs=runtime_errors: errs.append(str(err)))
          try:
            page.goto(
              url, wait_until="domcontentloaded", timeout=max(1, timeout_sec) * 1000
            )
          except Exception as exc:
            findings.append(
              enrich_finding_rule(
                {
                  "rule": "RUN102",
                  "location": f"{vw}x{vh} {scheme}",
                  "evidence": f"Navigation to {url} failed: {exc}",
                },
                severities,
              )
            )
            ctx.close()
            continue

          settle_page(page, timeout_sec)
          shot_name = f"{vw}x{vh}-{scheme}-{motion}.png"
          page.screenshot(path=str(out_dir / shot_name))
          captures.append(shot_name)

          for err_text in runtime_errors:
            findings.append(
              enrich_finding_rule(
                {
                  "rule": "RUN001",
                  "location": f"{vw}x{vh} {scheme}",
                  "evidence": f"Runtime console/page error: {err_text}",
                },
                severities,
              )
            )

          if motion == "no-preference":
            probe = page.evaluate(_LIVE_AUDIT_PROBE_JS)
            loc_label = f"{vw}x{vh} {scheme}"
            if probe.get("overflow"):
              findings.append(
                enrich_finding_rule(
                  {
                    "rule": "SPC206",
                    "location": loc_label,
                    "evidence": f"Horizontal overflow at {vw}x{vh}",
                  },
                  severities,
                )
              )
            for item in probe.get("findings", []):
              findings.append(
                enrich_finding_rule(
                  {
                    "rule": item["rule"],
                    "selector": item.get("selector"),
                    "location": loc_label,
                    "evidence": item["evidence"],
                  },
                  severities,
                )
              )
            if scheme == "dark":
              report_payload = {"width": vw, "elements": probe.get("elements", [])}
              for m_item in check_measurements(report_payload, sheet_tokens, scale):
                m_code = (
                  m_item.split()[0]
                  if RULE_CODE_RE.match(m_item)
                  else "TYP001"
                  if ": font-size is " in m_item
                  else "SPC002"
                  if ": border-radius" in m_item
                  else "SPC001"
                )
                findings.append(
                  enrich_finding_rule(
                    {
                      "rule": m_code,
                      "location": loc_label,
                      "evidence": m_item,
                    },
                    severities,
                  )
                )
              for d_row in check_density(report_payload):
                findings.append(
                  enrich_finding_rule(
                    {
                      "rule": d_row["rule"],
                      "selector": d_row.get("selector"),
                      "location": loc_label,
                      "evidence": d_row["evidence"],
                    },
                    severities,
                  )
                )
          ctx.close()
    finally:
      browser_inst.close()
  return {"status": "checked", "findings": findings, "captures": captures}


def doctor(
  root: Path | None = None,
  config_path: Path | None = None,
  executable: str | Path | None = None,
) -> dict[str, Any]:
  cfg_info = load_nuu_config(root, config_path)
  project_dir = cfg_info["dir"]

  source_meta = json.loads((CATALOG / "source.json").read_text(encoding="utf-8"))
  mismatches = [
    row["path"]
    for row in source_meta["files"]
    if not (CATALOG / row["path"]).is_file()
    or hashlib.sha256((CATALOG / row["path"]).read_bytes()).hexdigest() != row["sha256"]
  ]

  pw_ok = False
  pw_detail = "missing"
  try:
    import playwright as pw_pkg

    pw_ok = True
    pw_detail = getattr(pw_pkg, "__version__", "installed")
  except ImportError as exc:
    pw_detail = f"missing ({exc})"

  chromium_path: str | None = None
  chromium_error: str | None = None
  try:
    chromium_path = resolve_chromium_executable(executable)
  except FileNotFoundError as exc:
    chromium_error = str(exc)

  from annotate import resolve_project_typography

  typo = resolve_project_typography(project_dir, config_path)
  assets_report = validate_assets_config(
    project_dir, cfg_info["config"].get("assets", [])
  )

  errors: list[str] = []
  if executable is not None and chromium_error:
    errors.append(chromium_error)
  if mismatches:
    errors.append(f"catalog checksum mismatches: {', '.join(mismatches)}")
  for item in assets_report["findings"]:
    errors.append(item["evidence"])

  return {
    "version": VERSION,
    "python": sys.version.split()[0],
    "playwright": {"ok": pw_ok, "detail": pw_detail},
    "chromium": {
      "ok": bool(chromium_path),
      "path": chromium_path,
      "error": chromium_error,
    },
    "config": {
      "path": cfg_info["path"],
      "used_bundled": cfg_info["used_bundled"],
      "project_dir": str(project_dir),
    },
    "configuration": {
      "status": "checked",
      "path": cfg_info["path"],
    },
    "stylesheet": {
      "ok": cfg_info["stylesheet_path"].is_file(),
      "path": str(cfg_info["stylesheet_path"]),
    },
    "fonts": {
      "sans": typo["font_sans"],
      "mono": typo["font_mono"],
      "embedded": typo["embedded_fonts"],
    },
    "catalog": {
      "files": len(source_meta["files"]),
      "mismatches": mismatches,
    },
    "assets": assets_report,
    "errors": errors,
  }


def _format_finding_line(idx: int, item: Mapping[str, Any]) -> str:
  rule = item.get("rule", "?")
  sev = item.get("severity", "error")
  loc = item.get("location") or (
    f"{Path(item['path']).name}:{item.get('line', 1)}" if item.get("path") else "static"
  )
  evidence = str(item.get("evidence") or "").strip()
  return f"{idx}. `{rule}` ({sev}) `{loc}`: {evidence}"


def format_audit_markdown(
  *,
  findings: Sequence[Mapping[str, Any]],
  suppressions: Sequence[Mapping[str, Any]],
  deviations: Sequence[Mapping[str, Any]],
  recipes: Sequence[Mapping[str, Any]],
  captures: Sequence[str],
  live_checked: bool,
  viewport_count: int,
  exit_code: int,
) -> str:
  blocking_count = sum(1 for f in findings if f.get("severity") == "error")
  review_count = sum(1 for f in findings if f.get("severity") == "review")
  if exit_code == 1:
    status_line = "findings (exit 1)"
  elif exit_code == 3:
    status_line = "unrun (exit 3)"
  elif review_count:
    status_line = "review (exit 0)"
  else:
    status_line = "pass (exit 0)"

  unrun_line = (
    f"- None (static + {viewport_count} house viewports checked)"
    if live_checked
    else "- Live browser checks (`-u <http-url>` not supplied)"
  )
  finding_lines = (
    "\n".join(_format_finding_line(i, f) for i, f in enumerate(findings, 1))
    if findings
    else "- None"
  )
  deviation_lines = (
    "\n".join(
      f"- `{Path(d['path']).as_posix()}:{d['line']}`: `nuu-deviation: {d['reason']}`"
      for d in deviations
    )
    if deviations
    else "- None"
  )
  capture_lines = (
    "\n".join(f"- `{name}`" for name in captures) if captures else "- None"
  )
  return (
    "# UI Audit\n\n"
    f"## Status\n{status_line}\n\n"
    "## Counts\n"
    f"- Blocking errors: {blocking_count}\n"
    f"- Review findings: {review_count}\n"
    f"- Suppressions (`nonuu:`): {len(suppressions)}\n"
    f"- Deviations (`nuu-deviation:`): {len(deviations)}\n"
    f"- Recipes (`nuurecipe:`): {len(recipes)}\n\n"
    f"## Unrun Checks\n{unrun_line}\n\n"
    f"## Automated Findings\n{finding_lines}\n\n"
    "## Visual Judgments\n"
    "- [Agent: inspect captures and fill Clause 20-32 visual judgments or write `None`.]\n\n"
    f"## Deviations\n{deviation_lines}\n\n"
    f"## Captures\n{capture_lines}\n"
  )


def format_lint_markdown(result: Mapping[str, Any], exit_code: int) -> str:
  findings = result.get("findings", [])
  blocking = sum(1 for f in findings if f.get("severity") == "error")
  review = sum(1 for f in findings if f.get("severity") == "review")
  rows = (
    "\n".join(_format_finding_line(i, f) for i, f in enumerate(findings, 1))
    if findings
    else "- None"
  )
  return (
    "# Static Lint\n\n"
    f"## Status\n{'findings' if blocking else 'pass'} (exit {exit_code})\n\n"
    "## Counts\n"
    f"- Blocking errors: {blocking}\n"
    f"- Review findings: {review}\n"
    f"- Suppressions (`nonuu:`): {len(result.get('suppressions', []))}\n"
    f"- Deviations (`nuu-deviation:`): {len(result.get('deviations', []))}\n"
    f"- Recipes (`nuurecipe:`): {len(result.get('recipes', []))}\n\n"
    f"## Findings\n{rows}\n"
  )


def format_collect_markdown(bundle: Mapping[str, Any], exit_code: int) -> str:
  supps = bundle.get("suppressions", [])
  devs = bundle.get("deviations", [])
  recs = bundle.get("recipes", [])
  findings = bundle.get("findings", [])
  supp_lines = (
    "\n".join(
      f"- `{s['path']}:{s['line']}`: `nonuu: {s['rule']}` ({s['scope']})" for s in supps
    )
    if supps
    else "- None"
  )
  dev_lines = (
    "\n".join(
      f"- `{d['path']}:{d['line']}`: `nuu-deviation: {d['reason']}`" for d in devs
    )
    if devs
    else "- None"
  )
  rec_lines = (
    "\n".join(
      f"- `{r['path']}:{r['line']}`: `nuurecipe: {r['slug']}` ({'ok' if r['valid'] else 'missing'})"
      for r in recs
    )
    if recs
    else "- None"
  )
  finding_lines = (
    "\n".join(_format_finding_line(i, f) for i, f in enumerate(findings, 1))
    if findings
    else "- None"
  )
  return (
    "# Marker Inventory\n\n"
    f"## Status\n{'findings' if exit_code else 'pass'} (exit {exit_code})\n\n"
    "## Counts\n"
    f"- Suppressions (`nonuu:`): {len(supps)}\n"
    f"- Deviations (`nuu-deviation:`): {len(devs)}\n"
    f"- Recipes (`nuurecipe:`): {len(recs)}\n\n"
    f"## Suppressions\n{supp_lines}\n\n"
    f"## Deviations\n{dev_lines}\n\n"
    f"## Recipes\n{rec_lines}\n\n"
    f"## Findings\n{finding_lines}\n"
  )


def format_search_markdown(res: Mapping[str, Any]) -> str:
  lines = [
    f"# Catalog Search: `{res['query']}`",
    "",
    f"- Matches: `{len(res['results'])}` (`fallback={res['fallback']}`)",
    f"- Scanned records: `{res['coverage']['records']}`",
    "",
    "| Rank | Dataset | Line | Score | Matched Terms | Summary |",
    "| --- | --- | --- | --- | --- | --- |",
  ]
  for idx, row in enumerate(res["results"], 1):
    rec = row["record"]
    preview_parts = [
      f"{k}: {v}" for k, v in list(rec.items())[:3] if v is not None and str(v).strip()
    ]
    preview = "; ".join(preview_parts).replace("|", "\\|").replace("\n", " ")[:120]
    matched = ", ".join(row["matched_terms"]) or "-"
    lines.append(
      f"| {idx} | `{row['dataset']}` | {row['line']} | {row['score']:.2f} | `{matched}` | {preview} |"
    )
  return "\n".join(lines) + "\n"


def format_fetch_markdown(receipt: Mapping[str, Any]) -> str:
  return (
    "# Fetched Asset Receipt\n\n"
    f"- Path: `{receipt['path']}`\n"
    f"- Source: `{receipt['source']}`\n"
    f"- Bytes: `{receipt['bytes']}`\n"
    f"- SHA-256: `{receipt['sha256']}`\n\n"
    "Add attribution to `nuu.toml`:\n\n"
    "```toml\n"
    "[[assets]]\n"
    f'path = "{receipt["relative_path"]}"\n'
    f'source = "{receipt["source"]}"\n'
    'license = "TODO"\n'
    'attribution = "TODO"\n'
    "```\n"
  )


def format_doctor_markdown(doc: Mapping[str, Any], exit_code: int) -> str:
  chrom = doc["chromium"]
  chrom_cell = chrom["path"] or chrom["error"] or "not found in PATH or $CHROMIUM_BIN"
  cat = doc["catalog"]
  cat_cell = (
    f"{cat['files']} files verified"
    if not cat["mismatches"]
    else f"mismatches: {', '.join(cat['mismatches'])}"
  )
  asset_cnt = len(doc["assets"]["assets"])
  asset_errs = len(doc["assets"]["findings"])
  lines = [
    f"# Nuu Doctor (`v{doc['version']}`)",
    "",
    f"## Status\n{'healthy' if exit_code == 0 else 'errors'} (exit {exit_code})",
    "",
    "| Check | Status | Detail |",
    "| --- | --- | --- |",
    f"| Python | pass | `{doc['python']}` |",
    f"| Playwright | {'pass' if doc['playwright']['ok'] else 'fail'} | `{doc['playwright']['detail']}` |",
    f"| Chromium | {'pass' if chrom['ok'] else 'warn'} | `{chrom_cell}` |",
    f"| Config (`nuu.toml`) | pass | `{doc['config']['path']}` |",
    f"| Stylesheet | {'pass' if doc['stylesheet']['ok'] else 'fail'} | `{doc['stylesheet']['path']}` |",
    f"| Fonts | pass | sans=`{doc['fonts']['sans']}`, embedded=`{doc['fonts']['embedded']}` |",
    f"| Catalogs | {'pass' if not cat['mismatches'] else 'fail'} | {cat_cell} |",
    (
      f"| Assets (`[[assets]]`) | {'pass' if not asset_errs else 'fail'} | "
      f"`{asset_cnt}` configured, `{asset_errs}` findings |"
    ),
  ]
  return "\n".join(lines) + "\n"


def _emit_markdown(markdown: str, output_path: Path | None) -> None:
  if output_path is not None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
  print(markdown, end="")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="nuu",
    description="Python-only UI/UX design-sheet inspector, walker, redliner, and board compositor.",
  )
  parser.add_argument("--version", action="version", version=f"nuu {VERSION}")
  commands = parser.add_subparsers(dest="command", required=True)

  p_audit = commands.add_parser(
    "audit", help="Run static and optional live UI conformance audit"
  )
  p_audit.add_argument("-c", "--config", type=Path, default=None)
  p_audit.add_argument("-u", "--url", default=None)
  p_audit.add_argument("-o", "--output", type=Path, default=None)
  p_audit.add_argument("-d", "--artifacts", type=Path, default=Path("./nuu-artifacts"))
  p_audit.add_argument("-x", "--executable", default=None)
  p_audit.add_argument("-t", "--timeout", type=int, default=20)

  p_walk = commands.add_parser(
    "walk", help="Crawl a page, run @step walk.py, or drive an MCP session"
  )
  p_walk.add_argument("verb_or_target", nargs=argparse.REMAINDER)
  p_walk.add_argument("-c", "--config", type=Path, default=None)
  p_walk.add_argument("-S", "--session", default="default")
  p_walk.add_argument("-o", "--output", type=Path, default=Path("./nuu-artifacts"))
  p_walk.add_argument("-g", "--gif", type=Path, default=None)
  p_walk.add_argument("--video", nargs="?", const="walk.webm", default=None)
  p_walk.add_argument("-t", "--timeout", type=int, default=20)
  p_walk.add_argument("-x", "--executable", default=None)

  p_measure = commands.add_parser(
    "measure", help="Render DevTools tints, Figma guides, or drop-point rays"
  )
  p_measure.add_argument("-c", "--config", type=Path, default=None)
  p_measure.add_argument("-u", "--url", default=None)
  p_measure.add_argument("-i", "--input", type=Path, default=None)
  p_measure.add_argument("-s", "--selector", default=None)
  p_measure.add_argument("-d", "--drop", action="append", default=[])
  p_measure.add_argument("-o", "--output", type=Path, default=None)
  p_measure.add_argument("-T", "--tolerance", type=int, default=None)
  p_measure.add_argument("--to", dest="to_target", default=None)
  p_measure.add_argument("--from", dest="from_point", default=None)
  p_measure.add_argument("-k", "--scale", type=int, choices=(4, 8), default=None)
  p_measure.add_argument("-v", "--viewport", default=None)
  p_measure.add_argument("-x", "--executable", default=None)

  p_annotate = commands.add_parser(
    "annotate", help="Render dark-mode boxes, labels, arrows, and callouts"
  )
  p_annotate.add_argument("-c", "--config", type=Path, default=None)
  p_annotate.add_argument("-i", "--input", type=Path, default=None)
  p_annotate.add_argument("-u", "--url", default=None)
  p_annotate.add_argument("-s", "--selector", action="append", default=[])
  p_annotate.add_argument("-o", "--output", type=Path, required=True)
  p_annotate.add_argument("-b", "--box", action="append", default=[])
  p_annotate.add_argument("-l", "--label", action="append", default=[])
  p_annotate.add_argument("-a", "--arrow", action="append", default=[])
  p_annotate.add_argument("-n", "--callout", action="append", default=[])
  p_annotate.add_argument("-C", "--crop", default=None)
  p_annotate.add_argument("-S", "--scale", type=int, choices=range(1, 7), default=None)
  p_annotate.add_argument("-v", "--viewport", default=None)
  p_annotate.add_argument("-x", "--executable", default=None)

  board_presets = (
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "canvas",
    "lay-of-the-land",
    "deep-dive",
    "example-views",
    "component-states",
    "global-components",
    "style-guide",
    "anatomy",
    "responsive",
    "a11y-focus",
    "panels",
    "custom",
  )
  p_board = commands.add_parser(
    "board", help="Compose multi-panel or preset design boards"
  )
  p_board.add_argument("-c", "--config", type=Path, default=None)
  p_board.add_argument("-o", "--output", type=Path, required=True)
  p_board.add_argument("-T", "--type", choices=board_presets, default=None)
  p_board.add_argument("-O", "--only", action="append", default=[])
  p_board.add_argument("-t", "--title", default=None)
  p_board.add_argument("-p", "--panel", action="append", default=[])
  p_board.add_argument("-n", "--callout", action="append", default=[])
  p_board.add_argument("-s", "--spec", type=Path, default=None)
  p_board.add_argument("-S", "--scale", type=int, choices=range(1, 7), default=None)
  p_board.add_argument("-C", "--columns", type=int, default=None)
  p_board.add_argument("-W", "--wireframe", action="store_true", default=None)
  p_board.add_argument("-x", "--executable", default=None)

  p_lint = commands.add_parser(
    "lint", help="Run static HTML, CSS, icon, marker, and asset checks"
  )
  p_lint.add_argument("-c", "--config", type=Path, default=None)
  p_lint.add_argument("-o", "--output", type=Path, default=None)
  p_lint.add_argument("-s", "--sheet", type=Path, default=None)
  p_lint.add_argument("-k", "--scale", type=int, choices=(4, 8), default=None)

  p_collect = commands.add_parser(
    "collect", help="Collect nonuu:, nuu-deviation:, and nuurecipe: markers"
  )
  p_collect.add_argument("-c", "--config", type=Path, default=None)
  p_collect.add_argument("-o", "--output", type=Path, default=None)

  p_search = commands.add_parser(
    "search", help="Search vendored catalogs or fetch an external asset"
  )
  p_search.add_argument("query", nargs="?", default=None)
  p_search.add_argument("-c", "--config", type=Path, default=None)
  p_search.add_argument("-o", "--output", type=Path, default=None)
  p_search.add_argument("-d", "--dataset", default=None)
  p_search.add_argument("-s", "--stack", default=None)
  p_search.add_argument("-l", "--limit", type=int, default=10)
  p_search.add_argument("-f", "--fetch", default=None)
  p_search.add_argument("-A", "--authorize-fetch", action="store_true")

  p_doctor = commands.add_parser(
    "doctor", help="Check Python, Playwright, Chromium, nuu.toml, and catalogs"
  )
  p_doctor.add_argument("-c", "--config", type=Path, default=None)
  p_doctor.add_argument("-o", "--output", type=Path, default=None)
  p_doctor.add_argument("-x", "--executable", default=None)

  return parser


def main(argv: Sequence[str] | None = None) -> int:
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  raw_args = list(sys.argv[1:] if argv is None else argv)
  if (
    raw_args
    and raw_args[0] == "walk"
    and "-h" not in raw_args
    and "--help" not in raw_args
  ):
    from walk import main as walk_main

    return walk_main(raw_args[1:])

  parser = build_parser()
  args = parser.parse_args(raw_args)

  if args.command == "annotate" and bool(args.input) == bool(args.url):
    parser.error("annotate requires exactly one of -i/--input or -u/--url")
  if args.command == "measure":
    has_source = bool(args.url) or bool(args.input)
    has_pair = bool(args.from_point) and bool(args.to_target)
    if bool(args.url) and bool(args.input):
      parser.error("measure accepts either -u/--url or -i/--input, not both")
    if has_source and not (args.selector or args.drop):
      parser.error("measure with -u/-i requires -s/--selector or -d/--drop")
    if has_source and args.output is None:
      parser.error("measure with -u/-i requires -o/--output <png>")
    if not has_source and not has_pair:
      parser.error(
        "measure requires (-u|-i) + (-s|-d) + -o, or --from <x,y> --to <x,y>"
      )

  try:
    resolve_renderer()
    if args.command == "walk":
      from walk import main as walk_main

      return walk_main(raw_args[1:])

    if args.command == "measure":
      from measure import run_measure_cli

      return run_measure_cli(args)

    cfg_info = load_nuu_config(None, args.config)
    project_dir = cfg_info["dir"]
    severities = cfg_info["severities"]

    if args.command == "search":
      if args.fetch:
        if args.output is None:
          raise ValueError("search --fetch requires -o/--output <dest>")
        receipt = fetch_asset(
          project_dir, args.fetch, args.output, args.authorize_fetch
        )
        print(format_fetch_markdown(receipt), end="")
        return 0
      if not args.query:
        raise ValueError("search requires a <query> or -f/--fetch <http-url>")
      res = search(args.query, args.dataset, args.stack, args.limit)
      _emit_markdown(format_search_markdown(res), args.output)
      return 0

    if args.command == "doctor":
      doc = doctor(project_dir, args.config, args.executable)
      code = 1 if doc["errors"] else 0
      _emit_markdown(format_doctor_markdown(doc, code), args.output)
      return code

    if args.command == "collect":
      bundle = collect_all_markers(project_dir, severities)
      has_err = any(f.get("severity") == "error" for f in bundle["findings"])
      code = 1 if has_err else 0
      _emit_markdown(format_collect_markdown(bundle, code), args.output)
      return code

    if args.command == "lint":
      lint_res = lint(
        project_dir,
        config_path=args.config,
        sheet_path=args.sheet,
        scale=args.scale,
      )
      has_err = any(f.get("severity") == "error" for f in lint_res["findings"])
      code = 1 if has_err else 0
      _emit_markdown(format_lint_markdown(lint_res, code), args.output)
      return code

    if args.command == "annotate":
      from annotate import run_annotate_cli

      return run_annotate_cli(args, cfg_info)

    if args.command == "board":
      from annotate import run_board_cli

      return run_board_cli(args, cfg_info)

    # audit
    if args.timeout <= 0:
      raise ValueError("timeout must be positive")
    static_res = lint(project_dir, config_path=args.config)
    all_findings = list(static_res["findings"])
    captures: list[str] = []
    live_checked = False
    if args.url:
      live_res = run_live_audit(
        args.url,
        cfg_info=cfg_info,
        artifacts_dir=args.artifacts,
        executable=args.executable,
        timeout_sec=args.timeout,
      )
      all_findings.extend(live_res["findings"])
      captures = list(live_res["captures"])
      live_checked = True

    has_blocking = any(f.get("severity") == "error" for f in all_findings)
    exit_code = 1 if has_blocking else (0 if live_checked else 3)
    md = format_audit_markdown(
      findings=all_findings,
      suppressions=static_res["suppressions"],
      deviations=static_res["deviations"],
      recipes=static_res["recipes"],
      captures=captures,
      live_checked=live_checked,
      viewport_count=len(cfg_info["config"]["audit"]["viewports"]),
      exit_code=exit_code,
    )
    _emit_markdown(md, args.output)
    return exit_code
  except (ValueError, TypeError) as error:
    if isinstance(error, NuuConfigError):
      print(f"error: {error}", file=sys.stderr)
      return 1
    print(f"error: {error}", file=sys.stderr)
    return 2
  except (FileNotFoundError, OSError, RuntimeError, csv.Error) as error:
    print(f"error: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
