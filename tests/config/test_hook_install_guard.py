"""A dashboard install writes only the scanned commit and file bytes."""

import json
from pathlib import Path

import pytest
from test_hook_sources import API, ARCHIVE, COMMIT, answers, block, source, tar

from daedalus.config import remote


def test_expected_commit_and_hash_install_only_scanned_bytes(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A matching commit and hash write the selected file."""
  text = block()
  answers(
    monkeypatch,
    {API: json.dumps({"sha": COMMIT}).encode(), ARCHIVE: tar({"hooks/one.py": text})},
  )
  lock = tmp_path / "hooks.lock.json"
  moved = remote.update(
    [source()],
    tmp_path,
    lock,
    take=["owner/name/one.py"],
    expected_commit=COMMIT,
    expected_hashes={"owner/name/one.py": remote.digest(text.encode())},
  )
  assert moved == ["owner/name/one.py"]
  assert (tmp_path / "owner/name/one.py").read_text() == text


def test_moved_ref_or_changed_hash_writes_nothing(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A moved ref or changed hash keeps the disk and lock unchanged."""
  text = block()
  answers(
    monkeypatch,
    {API: json.dumps({"sha": COMMIT}).encode(), ARCHIVE: tar({"hooks/one.py": text})},
  )
  lock = tmp_path / "hooks.lock.json"
  before = {
    "owner/name/one.py": {
      "sha256": "b" * 64,
      "version": "1.0.0",
      "repo": "owner/name",
      "commit": "c" * 40,
    }
  }
  remote.write_records(before, lock)
  report: list[str] = []
  assert (
    remote.update(
      [source()],
      tmp_path,
      lock,
      take=["owner/name/one.py"],
      report=report,
      expected_commit="d" * 40,
      expected_hashes={"owner/name/one.py": remote.digest(text.encode())},
    )
    == []
  )
  assert remote.read_records(lock) == before
  assert report
  report.clear()
  assert (
    remote.update(
      [source()],
      tmp_path,
      lock,
      take=["owner/name/one.py"],
      report=report,
      expected_commit=COMMIT,
      expected_hashes={"owner/name/one.py": "e" * 64},
    )
    == []
  )
  assert remote.read_records(lock) == before
  assert not (tmp_path / "owner/name/one.py").exists()
  assert report
