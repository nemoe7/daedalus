"""Tests for the module version gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_versions
import pytest
import yaml

ROOT = Path(__file__).parents[2]


def version_git(repo: Path, *args: str) -> str:
  return subprocess.run(
    [
      "git",
      "-c",
      "core.hooksPath=/dev/null",
      "-c",
      "commit.gpgsign=false",
      "-c",
      "user.name=Version Test",
      "-c",
      "user.email=version@example.invalid",
      *args,
    ],
    cwd=repo,
    check=True,
    capture_output=True,
    text=True,
  ).stdout.strip()


@pytest.fixture
def version_repository(tmp_path):
  repo = tmp_path / "repo"
  repo.mkdir()
  version_git(repo, "init", "--quiet")
  (repo / "hooks").mkdir()
  (repo / "hooks/a.py").write_text("# version: 1.0.0\nvalue = 1\n")
  version_git(repo, "add", ".")
  version_git(repo, "commit", "--quiet", "-m", "baseline")
  return repo, version_git(repo, "rev-parse", "HEAD")


def version_check(repo: Path, base: str, head: str):
  return subprocess.run(
    [
      sys.executable,
      str(ROOT / "scripts/check_versions.py"),
      "--base",
      base,
      "--head",
      head,
    ],
    cwd=repo,
    capture_output=True,
    text=True,
    check=False,
  )


@pytest.mark.parametrize("missing", ["base", "head", "both"])
def test_missing_git_refs_fail(version_repository, missing):
  repo, base = version_repository
  result = version_check(
    repo,
    "missing-audit-ref" if missing in ("base", "both") else base,
    "missing-audit-ref" if missing in ("head", "both") else "HEAD",
  )
  assert result.returncode != 0
  assert result.stderr


@pytest.mark.parametrize(
  ("change", "expected"),
  [
    ("unchanged-version", 1),
    ("increase", 0),
    ("new", 0),
    ("new-unversioned", 1),
    ("delete", 0),
    ("unwatched", 0),
    ("new-tab", 0),
    ("new-tab-unversioned", 1),
    ("rename", 0),
  ],
)
def test_version_check_with_real_git_changes(version_repository, change, expected):
  repo, base = version_repository
  path = repo / "hooks/a.py"
  if change == "delete":
    path.unlink()
  elif change == "rename":
    path.rename(repo / "hooks/renamed.py")
  elif change.startswith("new-tab"):
    text = "value = 1\n"
    if change == "new-tab":
      text = "# version: 1.0.0\n" + text
    (repo / "hooks/new\tmodule.py").write_text(text)
  elif change == "new":
    (repo / "hooks/new.py").write_text("# version: 1.0.0\nvalue = 1\n")
  elif change == "new-unversioned":
    (repo / "hooks/new.py").write_text("value = 1\n")
  elif change == "unwatched":
    (repo / "README.md").write_text("Example\n")
  else:
    version = "1.0.1" if change == "increase" else "1.0.0"
    path.write_text(f"# version: {version}\nvalue = 2\n")
  version_git(repo, "add", "-A")
  version_git(repo, "commit", "--quiet", "-m", "change")
  result = version_check(repo, base, "HEAD")
  assert result.returncode == expected, result.stdout + result.stderr
  if change == "new-tab":
    assert "hooks/new\tmodule.py" in result.stdout


@pytest.mark.parametrize("stage", ["merge-base", "diff", "before", "after"])
def test_failed_git_reads_fail_the_gate(version_repository, monkeypatch, capsys, stage):
  repo, base = version_repository
  (repo / "hooks/a.py").write_text("# version: 1.0.1\nvalue = 2\n")
  version_git(repo, "add", ".")
  version_git(repo, "commit", "--quiet", "-m", "increase")
  head = version_git(repo, "rev-parse", "HEAD")
  original = subprocess.run

  def fail_read(command, **kwargs):
    selected = command[0] == "git" and (
      command[1] == stage
      or (
        stage in ("before", "after")
        and command[1] == "show"
        and command[2] == f"{base if stage == 'before' else head}:hooks/a.py"
      )
    )
    if selected:
      error = "test Git read failure"
      if kwargs.get("check"):
        raise subprocess.CalledProcessError(1, command, output="", stderr=error)
      return subprocess.CompletedProcess(command, 1, stdout="", stderr=error)
    return original(command, **kwargs)

  monkeypatch.chdir(repo)
  monkeypatch.setattr(check_versions.subprocess, "run", fail_read)
  assert check_versions.main(["--base", base, "--head", head]) != 0
  assert "test Git read failure" in capsys.readouterr().err


def test_version_job_fetches_the_pinned_head():
  data = yaml.safe_load((ROOT / ".github/workflows/pr-check.yml").read_text())
  steps = data["jobs"]["versions"]["steps"]
  fetched = [
    i for i, step in enumerate(steps) if "scripts/pr_snapshot.py" in step.get("run", "")
  ]
  checked = [
    i
    for i, step in enumerate(steps)
    if "scripts/check_versions.py" in step.get("run", "")
  ]
  assert len(fetched) == len(checked) == 1
  assert fetched[0] < checked[0]
  step = steps[fetched[0]]
  assert step["env"]["PR_NUMBER"] == "${{ github.event.pull_request.number }}"
  assert step["env"]["PR_HEAD"] == "${{ github.event.pull_request.head.sha }}"
  assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
  assert step["run"] == 'python3 scripts/pr_snapshot.py "$PR_NUMBER" "$PR_HEAD"'


def test_the_version_of_a_plugin_frontmatter():
  assert check_versions.version_of('"""\ntitle: GitHub\nversion: 3.0.1\n"""\n') == (
    3,
    0,
    1,
  )


def test_the_version_of_a_hook_frontmatter():
  assert check_versions.version_of("# ---\n# version: 1.0.2\n# ---\n") == (1, 0, 2)


def test_shipped_openwebui_python_plugins_disable_black_after_frontmatter():
  plugins = sorted((ROOT / "integrations/openwebui/functions").glob("*.py"))
  plugins += sorted((ROOT / "integrations/openwebui/tools").glob("*.py"))
  assert plugins
  for path in plugins:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '"""', path.relative_to(ROOT)
    closing = lines.index('"""', 1)
    first_source_line = next(
      (line for line in lines[closing + 1 :] if line.strip()), ""
    )
    assert first_source_line == "# fmt: off", path.relative_to(ROOT)


def test_a_file_without_a_version_line():
  assert check_versions.version_of("import os\n") is None
  assert check_versions.version_of(None) is None


def test_a_bump_passes():
  assert check_versions.failures([("hooks/a.py", (1, 0, 1), (1, 0, 2))]) == []


def test_a_flat_version_fails():
  found = check_versions.failures(
    [("integrations/openwebui/tools/a.py", (3, 0, 1), (3, 0, 1))]
  )
  assert found == [
    "integrations/openwebui/tools/a.py changed and its version stayed at 3.0.1"
  ]


def test_a_version_that_goes_back_fails():
  found = check_versions.failures([("hooks/a.py", (1, 0, 2), (1, 0, 1))])
  assert found == ["hooks/a.py changed and its version went back to 1.0.1"]


def test_a_new_module_needs_a_version():
  assert check_versions.failures([("hooks/new.py", None, None)]) == [
    "hooks/new.py is new and holds no version line"
  ]


def test_a_new_file_of_another_kind_needs_no_version():
  assert check_versions.failures([("hooks/notes.md", None, None)]) == []


def test_a_lost_version_line_fails():
  assert check_versions.failures([("hooks/a.py", (1, 0, 1), None)]) == [
    "hooks/a.py lost its version line"
  ]


def test_a_gained_version_line_passes():
  assert check_versions.failures([("hooks/a.py", None, (1, 0, 0))]) == []


def test_only_the_versioned_folders_count():
  assert check_versions.watched("daedalus/store/__init__.py") is False
  assert check_versions.watched("docs/hooks.md") is False
  assert check_versions.watched("hooks/served_model.py") is True
  assert check_versions.watched("integrations/openwebui/tools/github.py") is True
  assert (
    check_versions.watched("integrations/openwebui/functions/served_model.py") is True
  )
  assert (
    check_versions.watched("integrations/openwebui/skills/deep-research/SKILL.md")
    is True
  )
  assert (
    check_versions.watched(
      "integrations/openwebui/tools/__pycache__/github.cpython-311.pyc"
    )
    is False
  )
