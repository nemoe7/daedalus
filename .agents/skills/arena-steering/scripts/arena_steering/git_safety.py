"""Repository checks for pre-command Git safeguards."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .display_paths import home_relative


class GitCheckError(Exception):
  """A Git result that cannot justify allowing a guarded operation."""


class DirtyTreeError(GitCheckError):
  """Task completion found tracked or untracked checkout changes."""


def _git(
  cwd: Path, *args: str, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
  try:
    result = subprocess.run(
      ["git", *args],
      cwd=cwd,
      capture_output=True,
      text=True,
      check=False,
      timeout=30,
      env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
  except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
    raise GitCheckError("Git could not complete the comparison") from exc
  if result.returncode not in allowed:
    raise GitCheckError(f"Git {' '.join(args[:2])} exited {result.returncode}")
  return result


def target_cwd(cwd: Path, words: list[str]) -> Path:
  """Apply Git's -C options before a commit or push subcommand."""
  index = 1
  while index < len(words) and words[index] not in {"commit", "push"}:
    option = words[index]
    if option == "-C" and index + 1 < len(words):
      index += 1
      cwd = (cwd / words[index]).resolve()
    elif option.startswith("-C") and len(option) > 2:
      cwd = (cwd / option[2:]).resolve()
    index += 1
  return cwd


def managed_worktree(cwd: Path, checkout: Path) -> bool:
  """True if cwd is another worktree of the installed checkout."""
  try:
    top = Path(_git(cwd, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    common = _git(cwd, "rev-parse", "--git-common-dir").stdout.strip()
    owned = _git(checkout, "rev-parse", "--git-common-dir").stdout.strip()
  except GitCheckError:
    return False
  return (
    top != checkout.resolve()
    and (cwd / common).resolve() == (checkout / owned).resolve()
  )


def staged_warning(
  cwd: Path,
  ids: tuple[str, ...],
  *,
  renamable_ids: tuple[str, ...] = (),
  include_tracked: bool = False,
) -> str:
  """Check changes a commit would stage, including tracked files with -a."""
  try:
    names = [
      _git(cwd, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout
    ]
    if include_tracked:
      names.append(_git(cwd, "diff", "--name-only", "--diff-filter=ACMR", "-z").stdout)
    captures = [
      name
      for listing in names
      for name in listing.split("\0")
      if name.lower().endswith("-wireframe.png")
    ]
    if captures:
      return "Wireframe found staged. Remove before commit. Captures: " + ", ".join(
        home_relative(cwd / name) for name in captures
      )
    if not ids:
      return ""
    diffs = [_git(cwd, "diff", "--cached", "-U0", "--no-color", "--").stdout]
    if include_tracked:
      diffs.append(_git(cwd, "diff", "-U0", "--no-color", "--").stdout)
  except GitCheckError:
    return "Git changes failed detect. Retry after recover."
  patterns = {
    ident: re.compile(rf"(?<![0-9A-Za-z]){re.escape(ident)}(?![0-9A-Za-z])")
    for ident in ids
  }
  for diff in diffs:
    path = ""
    number = 0
    for line in diff.splitlines():
      if line.startswith("+++ b/"):
        path = line[6:]
      elif line.startswith("@@ "):
        match = re.search(r"\+(\d+)", line)
        if match:
          number = int(match.group(1)) - 1
      elif line.startswith("+") and not line.startswith("+++"):
        number += 1
        if path.endswith(("assets/mermaid.min.js", "assets/markdown-it.min.js")):
          continue
        for ident, pattern in patterns.items():
          if pattern.search(line):
            warning = (
              "VIOLATION: Cited session-local ID. "
              f"{home_relative(cwd / path)}:{number}: {ident}. "
              "Remove the citation and retry."
            )
            if ident in renamable_ids:
              warning += " If name clash `steer ren OLD_ID NEW_ID` and retry."
            return warning
  return ""


def _push_args(cwd: Path, words: list[str]) -> tuple[str, list[str]]:
  """Separate the remote and refspecs from ordinary push options."""
  args = words[words.index("push") + 1 :]
  remote_option = ""
  targets: list[str] = []
  index = 0
  while index < len(args):
    word = args[index]
    option, equal, value = word.partition("=")
    if option in {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}:
      if not equal:
        index += 1
        value = args[index] if index < len(args) else ""
      if option == "--repo":
        remote_option = value
    elif not word.startswith("-"):
      targets.append(word)
    index += 1
  if remote_option:
    return remote_option, targets
  if targets:
    return targets[0], targets[1:]
  branch = _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
  for key in (
    f"branch.{branch}.pushRemote",
    "remote.pushDefault",
    f"branch.{branch}.remote",
  ):
    remote = _git(cwd, "config", "--get", key, allowed=(0, 1)).stdout.strip()
    if remote:
      return remote, []
  return "origin", []


def _pushes_default(cwd: Path, refs: list[str], default: str) -> bool:
  """Reject a refspec that can update the remote default branch."""
  if not refs:
    mode = _git(cwd, "config", "--get", "push.default", allowed=(0, 1)).stdout.strip()
    if mode in {"matching", "upstream"}:
      return True
    refs = ["HEAD"]
  for ref in refs:
    if "*" in ref:
      return True
    destination = ref.split(":", 1)[-1].removeprefix("refs/heads/").lstrip("+")
    if destination in {"HEAD", "@", ""}:
      destination = _git(
        cwd, "symbolic-ref", "--quiet", "--short", "HEAD"
      ).stdout.strip()
    if destination == default:
      return True
  return False


def push_warning(cwd: Path, words: list[str]) -> str:
  """Fetch the detected remote default and refuse unsafe or unverified pushes."""
  if any(word in {"--help", "-h", "--dry-run", "-n"} for word in words):
    return ""
  try:
    remote, refs = _push_args(cwd, words)
    remote_head = _git(cwd, "ls-remote", "--symref", remote, "HEAD").stdout
    match = re.search(r"^ref:\s+refs/heads/(\S+)\s+HEAD$", remote_head, re.MULTILINE)
    if not match:
      raise GitCheckError("remote HEAD did not name a branch")
    default = match.group(1)
    if _pushes_default(cwd, refs, default):
      current = _git(
        cwd, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)
      ).stdout.strip()
      allowed = current or "a non-default branch"
      return (
        f"VIOLATION: Push blocked. Only push {allowed}. "
        f"Forbidden target: {remote}/{default}."
      )
    _git(cwd, "fetch", "--quiet", "--no-tags", remote, f"refs/heads/{default}")
    ancestry = _git(
      cwd, "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD", allowed=(0, 1)
    )
    if ancestry.returncode == 1:
      return (
        "Push blocked. Rebase default and retry. "
        f"{remote}/{default} is not an ancestor of HEAD."
      )
  except GitCheckError:
    return "Push blocked. remote default compare failed. Check remote and retry."
  return ""


def behind_main_pair(cwd: Path) -> tuple[str, str] | None:
  """Return HEAD and fresh origin/main only when main is not an ancestor."""
  try:
    _git(
      cwd,
      "fetch",
      "--quiet",
      "--no-tags",
      "origin",
      "+refs/heads/main:refs/remotes/origin/main",
    )
    base = _git(cwd, "rev-parse", "refs/remotes/origin/main").stdout.strip()
    head = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    verdict = _git(cwd, "merge-base", "--is-ancestor", base, head, allowed=(0, 1))
  except GitCheckError:
    return None
  return (head, base) if verdict.returncode == 1 else None


def require_clean_tree(cwd: Path) -> None:
  """Refuse task completion when Git sees tracked or untracked checkout changes."""
  if _git(cwd, "status", "--porcelain=v1", "--untracked-files=all").stdout:
    raise DirtyTreeError("Task completion requires a clean Git tree")


def completion_tree_check(state_dir: Path) -> Callable[[], None] | None:
  """Guard only the installed checkout state, not isolated or in-memory stores."""
  if state_dir.name != "arena-state":
    return None
  checkout = state_dir.parent.resolve()
  if not (checkout / ".git").exists():
    return None
  return lambda: require_clean_tree(checkout)


def verify_completion_commit(cwd: Path, commit_hash: str) -> str:
  """Require a clean checkout and a commit on its pushed branch."""
  if not isinstance(commit_hash, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_hash):
    raise GitCheckError("A full 40-character commit hash is required")
  require_clean_tree(cwd)
  branch = _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
  remote = (
    _git(
      cwd, "config", "--get", f"branch.{branch}.remote", allowed=(0, 1)
    ).stdout.strip()
    or "origin"
  )
  ref = (
    _git(
      cwd, "config", "--get", f"branch.{branch}.merge", allowed=(0, 1)
    ).stdout.strip()
    or f"refs/heads/{branch}"
  )
  if not ref.startswith("refs/heads/"):
    raise GitCheckError("Remote branch comparison unavailable")
  try:
    _git(cwd, "fetch", "--quiet", "--no-tags", remote, ref)
    remote_contains_head = _git(
      cwd, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD", allowed=(0, 1)
    )
  except GitCheckError as exc:
    raise GitCheckError("Remote branch comparison unavailable") from exc
  if remote_contains_head.returncode:
    raise GitCheckError("Local-only commits remain; push before finishing the task")
  commit = _git(
    cwd, "rev-parse", "--verify", f"{commit_hash}^{{commit}}"
  ).stdout.strip()
  on_branch = _git(cwd, "merge-base", "--is-ancestor", commit, "HEAD", allowed=(0, 1))
  if on_branch.returncode:
    raise GitCheckError("Commit is not on the current branch")
  return commit
