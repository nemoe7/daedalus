"""Tests for the module version gate."""

from __future__ import annotations

import check_versions


def test_the_version_of_a_plugin_frontmatter():
  assert check_versions.version_of('"""\ntitle: GitHub\nversion: 3.0.1\n"""\n') == (
    3,
    0,
    1,
  )


def test_the_version_of_a_hook_frontmatter():
  assert check_versions.version_of("# ---\n# version: 1.0.2\n# ---\n") == (1, 0, 2)


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
    check_versions.watched("integrations/openwebui/skills/deep-research.md") is True
  )
  assert (
    check_versions.watched(
      "integrations/openwebui/tools/__pycache__/github.cpython-311.pyc"
    )
    is False
  )
