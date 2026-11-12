"""Load nemoizer config, apply per-file overrides, and hold the active config.

Read `ACTIVE_CONFIG` and `BASE_CONFIG` as `config.<name>`: `file_scope` rebinds them.
"""

import contextlib
import fnmatch
import re
import tomllib
from pathlib import Path

from markers import collect_markers
from prose.extract import COMMENT_BLOCK_CAP
from prose.rotate import ROTATIONS, mask_protected_spans
from prose.ui import NMU100_MAX_WORDS

from rules import KNOWN_RULE_IDS

SENTENCE_CAPS = {"instruction": 20, "description": 25}


PARAGRAPH_CAP = 4


LIST_SENTENCE_CAP = 2


HEADING_SENTENCE_CAP = 1


THE_DENSITY_CAP = 20


MIN_THE_COUNT = 3


DEFAULT_CONFIG = {
  "root": None,
  "exclude": None,
  "extend_exclude": (),
  "respect_gitignore": True,
  "context": None,
  "instruction_sentence_cap": SENTENCE_CAPS["instruction"],
  "description_sentence_cap": SENTENCE_CAPS["description"],
  "paragraph_cap": PARAGRAPH_CAP,
  "list_sentence_cap": LIST_SENTENCE_CAP,
  "heading_sentence_cap": HEADING_SENTENCE_CAP,
  "comment_block_cap": COMMENT_BLOCK_CAP,
  "the_density_cap": THE_DENSITY_CAP,
  "min_the_count": MIN_THE_COUNT,
  "nmu100_max_words": NMU100_MAX_WORDS,
  "test_helpers": (),
  "select": ("ALL",),
  "ignore": (),
  "per_file_ignores": {},
  "files": (),
}


CAP_KEYS = (
  ("instruction-sentence-cap", "instruction_sentence_cap"),
  ("description-sentence-cap", "description_sentence_cap"),
  ("list-sentence-cap", "list_sentence_cap"),
  ("heading-sentence-cap", "heading_sentence_cap"),
  ("comment-block-cap", "comment_block_cap"),
  ("min-the-count", "min_the_count"),
  ("nmu100-max-words", "nmu100_max_words"),
  ("nmu100_max_words", "nmu100_max_words"),
)


FILE_ENTRY_KEYS = {
  "path",
  "paths",
  "context",
  "paragraph-cap",
  "the-density-cap",
  "select",
  "ignore",
  "lint",
  *(toml_key for toml_key, _ in CAP_KEYS),
}


NUMERIC_HEADER_KEYS = {
  *(toml_key for toml_key, _ in CAP_KEYS),
  "paragraph-cap",
  "the-density-cap",
}


HEADER_KEYS = {"context", *NUMERIC_HEADER_KEYS}


HEADER_MARKER = re.compile(r"\bnemoizer:[ \t]*(.*)")


HEADER_STOP = re.compile(r"--!?>|\*/")


BASE_CONFIG = dict(DEFAULT_CONFIG)


ACTIVE_CONFIG = dict(DEFAULT_CONFIG)


def _match_selectors(code, selectors):
  """True when a rule ID matches any configured rule ID, prefix, or ALL."""
  for item in selectors:
    token = str(item).strip().upper()
    if token == "ALL" or code == token or code.startswith(token):
      return True
  return False


def match_glob(rel_posix, name, pattern):
  """Match a path against a Ruff-style file or directory glob pattern."""
  pat = pattern.strip().replace("\\", "/")
  if pat.endswith("/**"):
    prefix = pat[:-3].rstrip("/")
    return rel_posix == prefix or rel_posix.startswith(prefix + "/")
  if "/**/" in pat:
    collapsed = pat
    while "/**/" in collapsed:
      collapsed = collapsed.replace("/**/", "/")
    if match_glob(rel_posix, name, collapsed):
      return True
  if pat.startswith("**/"):
    suffix = pat[3:]
    return (
      fnmatch.fnmatchcase(name, suffix)
      or fnmatch.fnmatchcase(rel_posix, suffix)
      or fnmatch.fnmatchcase(rel_posix, pat)
    )
  if "/" not in pat:
    return (
      pat in Path(rel_posix).parts
      or fnmatch.fnmatchcase(name, pat)
      or fnmatch.fnmatchcase(rel_posix, pat)
    )
  return fnmatch.fnmatchcase(rel_posix, pat)


def _config_overrides(table):
  """Return the validated context, cap, and rule keys of one config table."""
  overrides = {}
  if "context" in table:
    context = str(table["context"])
    if context not in {*SENTENCE_CAPS, "unknown"}:
      raise ValueError(f"invalid context in config: {context}")
    overrides["context"] = context
  for toml_key, cfg_key in CAP_KEYS:
    if toml_key in table:
      val = int(table[toml_key])
      if val < 1:
        raise ValueError(f"{toml_key} must be at least 1")
      overrides[cfg_key] = val
  if "the-density-cap" in table:
    density_cap = int(table["the-density-cap"])
    if not 1 <= density_cap <= 100:
      raise ValueError("the-density-cap must be between 1 and 100")
    overrides["the_density_cap"] = density_cap
  if "paragraph-cap" in table:
    paragraph_cap = int(table["paragraph-cap"])
    if not 0 < paragraph_cap <= PARAGRAPH_CAP:
      raise ValueError(f"paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
    overrides["paragraph_cap"] = paragraph_cap
  lint = table.get("lint", {})
  if isinstance(lint, dict):
    if "select" in lint:
      overrides["select"] = tuple(str(item) for item in lint["select"])
    if "ignore" in lint:
      overrides["ignore"] = tuple(str(item) for item in lint["ignore"])
    for lint_key in ("nmu100_max_words", "nmu100-max-words"):
      if lint_key in lint:
        val = int(lint[lint_key])
        if val < 1:
          raise ValueError(f"{lint_key} must be at least 1")
        overrides["nmu100_max_words"] = val
  return overrides


def _file_entry(entry, index):
  """Return the paths and rule overrides of one `[[files]]` entry."""
  table = entry if isinstance(entry, dict) else {}
  unknown = sorted(set(table) - FILE_ENTRY_KEYS)
  if unknown:
    raise ValueError(f"files entry {index} has unknown keys: {unknown}")
  raw_paths = table.get("paths", table.get("path"))
  if isinstance(raw_paths, str):
    paths = (raw_paths,)
  elif isinstance(raw_paths, (list, tuple)) and raw_paths:
    paths = tuple(str(item) for item in raw_paths)
  else:
    raise ValueError(f"files entry {index} needs a path or paths list")
  overrides = {
    key: value for key, value in table.items() if key not in {"path", "paths", "lint"}
  }
  lint = dict(table.get("lint") or {})
  for key in ("select", "ignore"):
    if key in table:
      lint[key] = overrides.pop(key, None)
  if lint:
    overrides["lint"] = lint
  return {"paths": paths, "overrides": _config_overrides(overrides)}


def load_config(targets=(), config_path=None, no_config=False):
  """Discover and parse nemoizer.toml or [tool.nemoizer] in pyproject.toml."""
  config = dict(DEFAULT_CONFIG)
  if no_config:
    return config
  root = None
  table = None
  if config_path is not None:
    candidate = Path(config_path)
    if not candidate.is_file():
      raise ValueError(f"config file does not exist: {candidate}")
    raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
    table = (
      raw.get("tool", {}).get("nemoizer", {})
      if candidate.name == "pyproject.toml"
      else raw
    )
    root = candidate.resolve().parent
  else:
    first = Path(targets[0]).resolve() if targets else Path.cwd().resolve()
    start = first if first.is_dir() else first.parent
    for directory in (start, *start.parents):
      for name in ("nemoizer.toml", ".nemoizer.toml"):
        candidate = directory / name
        if candidate.is_file():
          table = tomllib.loads(candidate.read_text(encoding="utf-8"))
          root = directory
          break
      if table is not None:
        break
      pyproject = directory / "pyproject.toml"
      if pyproject.is_file():
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        section = raw.get("tool", {}).get("nemoizer")
        if isinstance(section, dict):
          table = section
          root = directory
          break
      if (directory / ".git").exists():
        break
  if not isinstance(table, dict):
    return config
  config["root"] = root
  if "exclude" in table:
    config["exclude"] = tuple(str(item) for item in table["exclude"])
  if "extend-exclude" in table:
    config["extend_exclude"] = tuple(str(item) for item in table["extend-exclude"])
  if "respect-gitignore" in table:
    if not isinstance(table["respect-gitignore"], bool):
      raise ValueError("respect-gitignore must be a boolean")
    config["respect_gitignore"] = table["respect-gitignore"]
  config.update(_config_overrides(table))
  if "files" in table:
    files = table["files"]
    if not isinstance(files, list):
      raise ValueError("files must be an array of tables")
    config["files"] = tuple(
      _file_entry(entry, index) for index, entry in enumerate(files, 1)
    )
  if "test-helpers" in table:
    config["test_helpers"] = tuple(
      (root / str(item)).resolve() if root else Path(str(item)).resolve()
      for item in table["test-helpers"]
    )
  lint = table.get("lint", {})
  if isinstance(lint, dict):
    per_file = lint.get("per-file-ignores", {})
    if isinstance(per_file, dict):
      for pattern, rules in per_file.items():
        pat_lower = str(pattern).lower()
        rule_tuple = tuple(str(rule) for rule in rules)
        if "test" in pat_lower and _match_selectors("NMT100", rule_tuple):
          raise ValueError(
            f"per-file-ignores cannot suppress NMT100 on test pattern: {pattern}"
          )
      config["per_file_ignores"] = {
        str(pattern): tuple(str(rule) for rule in rules)
        for pattern, rules in per_file.items()
      }
  return config


def config_relative(path, root=None):
  """Return a path relative to the config root in POSIX form when possible."""
  candidate = Path(path)
  if root is None:
    return candidate.as_posix()
  try:
    return candidate.resolve().relative_to(Path(root).resolve()).as_posix()
  except ValueError:
    return candidate.as_posix()


def header_config(text, path=None):
  """Return cap and context overrides from a `nemoizer:` header on lines 1-3."""
  overrides = {}
  for number, line in enumerate(text.splitlines()[:3], 1):
    visible = mask_protected_spans(line)
    marker = HEADER_MARKER.search(visible)
    if marker is None:
      continue
    before = visible[: marker.start()]
    if any(
      re.search(pattern, before, flags=re.IGNORECASE) for _, pattern, _ in ROTATIONS
    ):
      continue
    for pair in re.split(r"[,;]", HEADER_STOP.split(marker[1], maxsplit=1)[0]):
      key, separator, value = pair.partition("=")
      key, value = key.strip().lower(), value.strip()
      if not separator or key not in HEADER_KEYS:
        raise ValueError(
          f"{path or 'header'}:{number}: invalid nemoizer header key: {key or pair.strip()}"
        )
      if key in NUMERIC_HEADER_KEYS and not re.fullmatch(r"\d+", value):
        raise ValueError(
          f"{path or 'header'}:{number}: {key} needs a whole number, got: {value}"
        )
      overrides.update(_config_overrides({key: value}))
  return overrides


def file_config(path, text=None):
  """Return the base config merged with matched `[[files]]` entries and a header."""
  config = dict(BASE_CONFIG)
  relative = config_relative(path, BASE_CONFIG.get("root"))
  name = Path(path).name
  for entry in BASE_CONFIG.get("files", ()):
    if any(match_glob(relative, name, pattern) for pattern in entry["paths"]):
      config.update(entry["overrides"])
  if text is not None:
    config.update(header_config(text, path))
  return config


@contextlib.contextmanager
def file_scope(path, text=None):
  """Apply the `[[files]]` and header config of one file, then restore it."""
  global ACTIVE_CONFIG
  previous = ACTIVE_CONFIG
  ACTIVE_CONFIG = file_config(path, text)
  try:
    yield ACTIVE_CONFIG
  finally:
    ACTIVE_CONFIG = previous


def config_file_exclusions(path, config=None):
  """Return rule IDs suppressed for path by select, ignore, and per-file-ignores."""
  cfg = ACTIVE_CONFIG if config is None else config
  select = cfg.get("select", ("ALL",))
  ignore = cfg.get("ignore", ())
  per_file = cfg.get("per_file_ignores", {})
  disabled = {
    code
    for code in KNOWN_RULE_IDS
    if not _match_selectors(code, select) or _match_selectors(code, ignore)
  }
  if per_file:
    candidate = Path(path)
    rel = config_relative(candidate, cfg.get("root"))
    for pattern, rules in per_file.items():
      if match_glob(rel, candidate.name, pattern):
        disabled.update(
          code for code in KNOWN_RULE_IDS if _match_selectors(code, rules)
        )
  return disabled


def exclusions(path, lines, audit=False):
  """Collect every marker and the known rule IDs it suppresses."""
  file_ids, line_ids, marks = collect_markers(
    path, lines, KNOWN_RULE_IDS, ROTATIONS, audit=audit
  )
  return file_ids | config_file_exclusions(path), line_ids, marks


def suppressed(code, ids, name=None):
  """Return whether the active marker set suppresses a rule ID."""
  return code in ids
