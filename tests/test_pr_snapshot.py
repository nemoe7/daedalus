"""Pinned PR input stays data and cannot change trusted files."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import check_pr

SCRIPT = Path("scripts/pr_snapshot.py").resolve()


def git(directory: Path, *args: str, data: bytes | None = None) -> bytes:
  return subprocess.run(
    ["git", *args],
    cwd=directory,
    input=data,
    capture_output=True,
    check=True,
    env={
      **os.environ,
      "GIT_AUTHOR_NAME": "Test",
      "GIT_AUTHOR_EMAIL": "test@example.invalid",
      "GIT_COMMITTER_NAME": "Test",
      "GIT_COMMITTER_EMAIL": "test@example.invalid",
    },
  ).stdout


def test_the_snapshot_reads_blobs_and_rejects_unsafe_input():
  with TemporaryDirectory() as directory:
    root = Path(directory)
    remote = root / "remote"
    base = root / "base"
    remote.mkdir()
    base.mkdir()
    git(remote, "init", "--bare", "--quiet")
    git(base, "init", "--bare", "--quiet")
    git(base, "remote", "add", "origin", str(remote))
    marker = root / "executed"
    program = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode()
    blob = git(remote, "hash-object", "-w", "--stdin", data=program).strip()
    tree = git(
      remote, "mktree", data=b"100755 blob " + blob + b"\tcheck_pr.py\n"
    ).strip()
    sha = (
      git(remote, "commit-tree", tree.decode(), "-m", "ci: add checks").strip().decode()
    )
    git(remote, "update-ref", "refs/pull/7/head", sha)
    output = root / "input"
    result = subprocess.run(
      [sys.executable, str(SCRIPT), "7", sha, "--output", str(output)],
      cwd=base,
      capture_output=True,
      check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "check_pr.py").read_bytes() == program
    assert not (output / "check_pr.py").stat().st_mode & 0o111
    assert not marker.exists()
    assert git(base, "rev-parse", "refs/pr-check/7").strip().decode() == sha
    for number, expected in (("0", sha), ("7", "invalid"), ("7", "0" * 40)):
      result = subprocess.run(
        [sys.executable, str(SCRIPT), number, expected],
        cwd=base,
        capture_output=True,
        check=False,
      )
      assert result.returncode == 1, result.stdout + result.stderr
    for mode, kind, object_id in (
      (b"120000", b"blob", blob),
      (b"160000", b"commit", sha.encode()),
    ):
      tree = git(
        remote, "mktree", data=mode + b" " + kind + b" " + object_id + b"\tlink\n"
      ).strip()
      linked = (
        git(remote, "commit-tree", tree.decode(), "-m", "ci: add a link")
        .strip()
        .decode()
      )
      git(remote, "update-ref", "refs/pull/8/head", linked)
      result = subprocess.run(
        [sys.executable, str(SCRIPT), "8", linked, "--output", str(root / "linked")],
        cwd=base,
        capture_output=True,
        check=False,
      )
      assert result.returncode == 1, result.stdout + result.stderr
      assert b"regular file" in result.stderr
      assert not (root / "linked").exists()
    assert not marker.exists()


def test_commit_input_preserves_real_record_boundaries():
  with TemporaryDirectory() as directory:
    repo = Path(directory)
    git(repo, "init", "--bare", "--quiet")
    tree = git(repo, "mktree", data=b"").strip().decode()
    base = git(repo, "commit-tree", tree, "-m", "ci: start").strip().decode()
    message = "ci: add checks\x1e" + "a" * 40 + "\x1fci: add other checks"
    head = (
      git(repo, "commit-tree", tree, "-p", base, data=message.encode()).strip().decode()
    )
    original = check_pr.ROOT
    check_pr.ROOT = repo
    try:
      commits = check_pr.commits_in_range(f"{base}..{head}")
      assert commits == [(head, message)]
      assert check_pr.validate_commit(commits[0][1])
      banged = (
        git(
          repo,
          "commit-tree",
          tree,
          "-p",
          base,
          data=b"\nfeat(server)!: remove an endpoint\n",
        )
        .strip()
        .decode()
      )
      body = repo / "body.md"
      body.write_text(
        "## Summary\n\nRemove an endpoint.\n\n## Changes\n\n- Remove the endpoint.\n\n"
        "## Validation\n\n- [x] Run the checks.\n",
        encoding="utf-8",
      )
      failures = check_pr.check(f"{base}..{banged}", None, str(body), False)
      assert any("breaking marker" in failure.message for failure in failures)
    finally:
      check_pr.ROOT = original
