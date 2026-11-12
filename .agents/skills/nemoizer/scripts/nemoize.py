# nonemo: NMO100
# nonemo: NMO901
"""Check prose and test candidates, rotate words, and collect exclusions."""

import argparse
import configparser
import json
import os
import subprocess
import sys
import tokenize
import tomllib
from pathlib import Path

import config
from assertions import is_test_file, review_tests
from config import (
  PARAGRAPH_CAP,
  SENTENCE_CAPS,
  file_config,
  load_config,
)
from files import text_file, walk
from markers import (
  audit_lines,
)
from prose.review import (
  docs_drift_findings,
  format_paths,
  prose_review,
  scan,
  source_prose,
)
from prose.rotate import (
  rotate,
)
from report import (
  describe_finding,
  format_finding,
  format_summary,
  is_review,
)
from runner import run_with_timeouts

from rules import (
  ADVISORY_RULE_IDS,
  TEST_RULE_IDS,
)


def add_config_arguments(parser):
  parser.add_argument("--config", type=Path, default=None)
  parser.add_argument("--no-config", action="store_true")


def activate_config(parser, targets, config_path, no_config):
  """Load the discovered or named config as the active config, or exit with usage."""
  try:
    config.BASE_CONFIG = load_config(targets, config_path, no_config)
  except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
    parser.error(str(error))
  config.ACTIVE_CONFIG = config.BASE_CONFIG


def prose_main(argv=None):
  parser = argparse.ArgumentParser(description="Review explicitly scoped prose counts.")
  parser.add_argument("targets", nargs="+")
  parser.add_argument(
    "--context", choices=[*SENTENCE_CAPS, "unknown"], default="unknown"
  )
  parser.add_argument("--paragraph-cap", type=int, default=PARAGRAPH_CAP)
  add_config_arguments(parser)
  arguments = parser.parse_args(argv)
  if not 0 < arguments.paragraph_cap <= PARAGRAPH_CAP:
    parser.error(f"--paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
  for target in arguments.targets:
    if not Path(target).exists():
      parser.error(f"target does not exist: {target}")
  activate_config(parser, arguments.targets, arguments.config, arguments.no_config)
  findings, coverage, errors = [], [], []
  for path in walk(arguments.targets, []):
    text = text_file(path)
    coverage.append(
      {
        "path": str(path),
        "context": arguments.context,
        "sentences": "checked"
        if text is not None and arguments.context in {*SENTENCE_CAPS, "unknown"}
        else "not_checked",
        "paragraphs": "checked" if text is not None else "not_checked",
      }
    )
    if text is not None:
      try:
        findings.extend(
          source_prose(path, text, arguments.context, arguments.paragraph_cap)
        )
      except (
        SyntaxError,
        tokenize.TokenError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        RuntimeError,
      ) as error:
        coverage[-1].update(
          sentences="not_checked", paragraphs="not_checked", reason="parse_error"
        )
        errors.append(f"{path}: {type(error).__name__}: {error}")
  print(json.dumps({"findings": findings, "coverage": coverage}))
  if errors:
    print("\n".join(errors), file=sys.stderr)
    return 2
  return int(bool(findings))


def review_tests_main(argv=None):
  parser = argparse.ArgumentParser(
    description="Review explicitly scoped test assertions and waits."
  )
  parser.add_argument("targets", nargs="+")
  add_config_arguments(parser)
  args = parser.parse_args(argv)
  for target in args.targets:
    if not Path(target).exists():
      parser.error(f"target does not exist: {target}")
  activate_config(parser, args.targets, args.config, args.no_config)
  try:
    result = review_tests(args.targets)
  except ValueError as error:
    parser.error(str(error))
  print(json.dumps(result))
  return (
    2
    if result["errors"]
    else int(any(row.get("status") != "manual_review" for row in result["findings"]))
  )


def main(argv=None):
  # Windows consoles default to cp1252. Force UTF-8 output so findings with any
  # decoded character print instead of crashing the report.
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8", errors="replace")
  argv = list(sys.argv[1:] if argv is None else argv)
  if argv[:1] == ["prose"]:
    return prose_main(argv[1:])
  if argv[:1] == ["review-tests"]:
    return review_tests_main(argv[1:])
  if argv[:1] in (["audit"], ["collect"]):
    argv = ["--audit", *argv[1:]]
  parser = argparse.ArgumentParser(description="Check prose and test candidates.")
  parser.add_argument("targets", nargs="*", default=["."])
  parser.add_argument(
    "--ignore",
    action="append",
    default=[],
    help="skip a directory or file, relative to the target",
  )
  parser.add_argument(
    "-v",
    "--verbose",
    action="store_true",
    help="print each file as it is scanned and processed",
  )
  parser.add_argument(
    "--ignores", action="store_true", help="list every nonemo marker instead"
  )
  parser.add_argument(
    "--audit",
    action="store_true",
    help="list every nonemo marker and manual-review finding",
  )
  parser.add_argument(
    "--format", action="store_true", help="apply the rotations in place"
  )
  parser.add_argument(
    "--selftest", action="store_true", help="run built-in self-tests and exit"
  )
  parser.add_argument("--context", choices=[*SENTENCE_CAPS, "unknown"], default=None)
  parser.add_argument("--paragraph-cap", type=int, default=None)
  parser.add_argument("--test-helper", action="append", type=Path, default=[])
  add_config_arguments(parser)
  arguments = parser.parse_args(argv)
  if arguments.selftest:
    assert rotate("Transmit the kind.") == "Send the type."
    sample = prose_review(
      Path("sample.md"), "Start; stop.\n", "description", PARAGRAPH_CAP
    )
    assert any(row["rule"] == "STE100" for row in sample)
    return 0
  targets = arguments.targets or ["."]
  for target in targets:
    if not Path(target).exists():
      parser.error(f"target does not exist: {target}")
  activate_config(parser, targets, arguments.config, arguments.no_config)
  context = (
    arguments.context
    if arguments.context is not None
    else (config.ACTIVE_CONFIG.get("context") or "unknown")
  )
  paragraph_cap = (
    arguments.paragraph_cap
    if arguments.paragraph_cap is not None
    else (config.ACTIVE_CONFIG.get("paragraph_cap") or PARAGRAPH_CAP)
  )
  if not 0 < paragraph_cap <= PARAGRAPH_CAP:
    parser.error(f"--paragraph-cap must be between 1 and {PARAGRAPH_CAP}")
  test_helpers = [
    *config.ACTIVE_CONFIG.get("test_helpers", ()),
    *arguments.test_helper,
  ]
  for helper in test_helpers:
    if not Path(helper).is_file():
      parser.error(f"test helper does not exist: {helper}")
  if arguments.format:
    try:
      changed = format_paths(targets, arguments.ignore)
    except ValueError as error:
      parser.error(str(error))
    print(f"{changed} lines rotated", file=sys.stderr)
    return 0
  try:
    findings, marks = scan(
      targets, arguments.ignore, audit=arguments.audit, verbose=arguments.verbose
    )
  except ValueError as error:
    parser.error(str(error))
  if arguments.ignores:
    print("\n".join(marks))
    return 0
  errors, tests = [], []
  helpers = {Path(path).resolve() for path in test_helpers}
  files = list(walk([*targets, *test_helpers], arguments.ignore))

  def process_one(path):
    result = {"test": None, "error": None, "rows": [], "text": None}
    text = text_file(path)
    result["text"] = text
    try:
      if is_test_file(path, text or "", helpers):
        result["test"] = str(path)
      if text is None:
        return result
      file_cfg = file_config(path, text)
      result["rows"] = source_prose(
        path,
        text,
        context
        if arguments.context is not None
        else (file_cfg.get("context") or "unknown"),
        paragraph_cap
        if arguments.paragraph_cap is not None
        else (file_cfg.get("paragraph_cap") or PARAGRAPH_CAP),
      )
    except (
      SyntaxError,
      tokenize.TokenError,
      OSError,
      ValueError,
      configparser.Error,
      subprocess.SubprocessError,
      RuntimeError,
      RecursionError,
    ) as error:
      result["error"] = f"{path}: {type(error).__name__}: {error}"
    return result

  results, _ = run_with_timeouts(
    files,
    process_one,
    min(8, (os.cpu_count() or 4)),
    verbose=arguments.verbose,
  )
  for path in files:
    result = results.get(path) or {
      "test": None,
      "error": None,
      "rows": [],
      "text": None,
    }
    if result["test"]:
      tests.append(result["test"])
    if result["error"]:
      errors.append(result["error"])
      continue
    text = result["text"]
    if text is None:
      continue
    lines = text.splitlines()
    findings.extend(
      format_finding(
        row["path"],
        row["line"],
        row["rule"],
        describe_finding(row),
        lines[row["line"] - 1] if 0 < row["line"] <= len(lines) else None,
        status=row.get("status"),
      )
      for row in result["rows"]
    )
    if arguments.audit:
      findings.extend(docs_drift_findings(path, text, config.ACTIVE_CONFIG.get("root")))
  try:
    result = review_tests(tests)
  except ValueError as error:
    parser.error(str(error))
  for row in result["findings"]:
    test_lines = (text_file(Path(row["path"])) or "").splitlines()
    findings.append(
      format_finding(
        row["path"],
        row["line"],
        row["rule"],
        row["evidence"],
        test_lines[row["line"] - 1] if 0 < row["line"] <= len(test_lines) else None,
        status=row.get("status"),
      )
    )
  for row in result["coverage"]:
    if row["status"] != "checked":
      errors.append(f"{row['path']}: tests not checked: {row['reason']}")
  errors.extend(f"{row['path']}: {row['type']}" for row in result["errors"])
  if arguments.audit:
    print(
      "\n".join(
        audit_lines(marks, findings, {*ADVISORY_RULE_IDS, *TEST_RULE_IDS.values()})
      )
    )
    if errors:
      print("\n".join(errors), file=sys.stderr)
      return 2
    return 0
  if tests:
    print(
      "Test scope: static paths and declarations. Dynamic helpers may be unresolved.",
      file=sys.stderr,
    )
  print("\n".join(findings) + ("\n" + format_summary(findings) if findings else ""))
  if errors:
    print("\n".join(errors), file=sys.stderr)
    return 2
  return int(any(not is_review(finding) for finding in findings))


if __name__ == "__main__":
  sys.exit(main())
