"""Check prose extraction and scan results with synthetic source files."""

import json
import subprocess
from pathlib import Path

import lint_prose


def test_tracked_scope_uses_exclusions_and_keeps_new_folders(monkeypatch):
  names = [
    "future folder/message.py",
    "docs/guide.md",
    ".agents/skills/vendor/script.py",
    ".github/workflows/gemini-release.yml",
    ".github/workflows/gemini-release-evidence.json",
    ".github/workflows/gemini_release.py",
    "daedalus/routing/classifier.py",
    "LICENSE.md",
    "docs/private.md",
  ]
  monkeypatch.setattr(
    lint_prose.subprocess,
    "run",
    lambda *args, **kwargs: subprocess.CompletedProcess(
      args, 0, stdout="\0".join(names) + "\0"
    ),
  )
  assert lint_prose.tracked_files(["docs/private.md"]) == [
    Path("future folder/message.py"),
    Path("docs/guide.md"),
  ]


def test_python_extraction_keeps_locations_and_marks_string_candidates(tmp_path):
  source = tmp_path / "sample.py"
  source.write_text(
    '# A comment.\n"""A module description."""\n'
    'def answer(name):\n  """A function description."""\n'
    '  return f"Hello {name}."\n',
    encoding="utf-8",
  )
  parts, manual = lint_prose.extract(source)
  assert {(line, kind) for line, kind, _ in parts} == {
    (1, "comment"),
    (2, "docstring"),
    (4, "docstring"),
    (5, "candidate"),
  }
  assert len(manual) == 1
  assert (
    next(text for _, kind, text in parts if kind == "candidate") == "Hello {value}."
  )


def test_locale_values_exclude_syntax_and_keep_source_lines(tmp_path):
  source = tmp_path / "en.js"
  source.write_text(
    '"use strict";\nglobalThis.DAEDALUS_LOCALE = {\n'
    '  "test.first": "Alpha; beta.",\n'
    '  "test.second": "First line.\\nSecond line."\n};\n',
    encoding="utf-8",
  )
  parts, manual = lint_prose.extract(source)
  assert manual == []
  assert parts == [
    (3, "locale", "Alpha; beta."),
    (4, "locale", "First line.\nSecond line."),
  ]
  result = lint_prose.scan_file(source)
  assert any(
    finding["line"] == 3 and finding["rule"] == "semicolon"
    for finding in result["findings"]
  )


def test_scan_keeps_advisories_and_reports_unsupported_or_broken_files(tmp_path):
  source = tmp_path / "guide.md"
  source.write_text("The file is saved.\n", encoding="utf-8")
  result = lint_prose.scan_file(source)
  assert any(finding["level"] == "advisory" for finding in result["findings"])
  assert not result["manual"]
  assert lint_prose.scan([source], as_json=True) == 1
  script = tmp_path / "setup.ps1"
  script.write_text('Write-Host "Hello"\n', encoding="utf-8")
  assert lint_prose.scan_file(script)["manual"]
  assert lint_prose.scan([script], as_json=True) == 2
  source = tmp_path / "broken.py"
  source.write_text("def broken(\n", encoding="utf-8")
  assert lint_prose.scan_file(source)["manual"]
  source = tmp_path / "en.js"
  source.write_text('globalThis.DAEDALUS_LOCALE = {"key": 1};\n', encoding="utf-8")
  assert lint_prose.scan_file(source)["manual"]
  assert json.dumps(result)
  link = tmp_path / "linked.md"
  link.symlink_to(source)
  assert lint_prose.scan_file(link)["manual"]
  source = tmp_path / "clean.md"
  source.write_text("A short paragraph.\n", encoding="utf-8")
  assert lint_prose.scan([source], as_json=True) == 0


def test_structural_command_keeps_custom_markdown_exclusions(tmp_path, monkeypatch):
  (tmp_path / "kept.md").write_text("A short paragraph.\n", encoding="utf-8")
  (tmp_path / "excluded.md").write_text(" ".join(["word"] * 40), encoding="utf-8")
  monkeypatch.setattr(lint_prose, "ROOT", tmp_path)
  monkeypatch.setattr(
    lint_prose.sys, "argv", ["lint_prose.py", "--exclude", "excluded.md"]
  )
  run = subprocess.run

  def with_tracked_paths(args, **kwargs):
    if args[0] == "git":
      return subprocess.CompletedProcess(args, 0, stdout="kept.md\0excluded.md\0")
    return run(args, **kwargs)

  monkeypatch.setattr(lint_prose.subprocess, "run", with_tracked_paths)
  assert lint_prose.main() == 0


def test_default_command_checks_markdown_ste_findings(tmp_path, monkeypatch):
  source = tmp_path / "guide.md"
  monkeypatch.setattr(lint_prose, "ROOT", tmp_path)
  monkeypatch.setattr(lint_prose.sys, "argv", ["lint_prose.py"])
  monkeypatch.setattr(lint_prose, "tracked_files", lambda excludes: [Path("guide.md")])
  for text, expected in (
    ("Alpha; beta.\n", 1),
    ("The file is saved.\n", 1),
    ("Save the file.\n", 0),
  ):
    source.write_text(text, encoding="utf-8")
    assert lint_prose.main() == expected


def test_default_command_counts_python_advisories(tmp_path, monkeypatch):
  source = tmp_path / "sample.py"
  monkeypatch.setattr(lint_prose, "ROOT", tmp_path)
  monkeypatch.setattr(lint_prose.sys, "argv", ["lint_prose.py"])
  monkeypatch.setattr(lint_prose, "tracked_files", lambda excludes: [Path("sample.py")])
  for text, expected in (
    ('"""The file is saved."""\n', 1),
    ("# The file is saved.\n", 1),
    ('"""Alpha; beta."""\n', 1),
    ('"""Save the file."""\nmessage = "The file is saved."\n', 0),
  ):
    source.write_text(text, encoding="utf-8")
    assert lint_prose.main() == expected


def test_default_command_reports_python_linter_failures(tmp_path, monkeypatch, capsys):
  source = tmp_path / "sample.py"
  source.write_text('"""Save the file."""\n', encoding="utf-8")
  monkeypatch.setattr(lint_prose, "ROOT", tmp_path)
  monkeypatch.setattr(lint_prose.sys, "argv", ["lint_prose.py"])
  monkeypatch.setattr(lint_prose, "tracked_files", lambda excludes: [Path("sample.py")])
  for code, output, error in ((2, "", "synthetic failure"), (0, "{", "")):
    monkeypatch.setattr(
      lint_prose.subprocess,
      "run",
      lambda *args, code=code, output=output, error=error, **kwargs: (
        subprocess.CompletedProcess(args, code, stdout=output, stderr=error)
      ),
    )
    assert lint_prose.main() == 1
    captured = capsys.readouterr().out
    assert "sample.py" in captured
    if error:
      assert error in captured
