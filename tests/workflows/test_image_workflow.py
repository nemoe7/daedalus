"""One Docker workflow publishes development and release images."""

import os
import subprocess
from pathlib import Path

from ruamel.yaml import YAML

WORKFLOW = Path(".github/workflows/docker.yml")


def test_docker_workflow_keeps_the_release_triggers_and_tag_flow() -> None:
  """A tag push, a Gemini run, or a release dispatch starts the release job."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())

  assert data["name"] == "Docker"
  triggers = data["on"]
  assert triggers["push"]["tags"] == ["v*"]
  assert triggers["workflow_run"] == {
    "workflows": ["CI", "Gemini Release Draft"],
    "types": ["completed"],
  }

  content = WORKFLOW.read_text()
  assert 'select(.name == "draft")' in content
  assert 'select(.name == "Approve Proposal")' in content
  assert "Approved proposal tag not found in job logs" in content
  # The release job reads the tag, and the build and merge jobs take it from its output.
  assert "tag: ${{ steps.release.outputs.tag }}" in content
  assert "type=raw,value=${{ needs.release.outputs.tag }}" in content
  assert "ref: ${{ needs.release.outputs.tag }}" in content
  assert "build-args: VERSION=${{ needs.release.outputs.tag }}" in content


def test_docker_workflow_release_job_validates_the_tag_and_the_sha() -> None:
  """The release job refuses a tag it cannot read and a commit it did not expect."""
  content = WORKFLOW.read_text()
  release = content[content.index("  release:") :]

  assert "Invalid release tag" in release
  assert "Verify Release Commit" in release
  assert 'git rev-list -n 1 "$TAG^{commit}"' in release
  assert "Tag $TAG points to $actual_sha, expected $EXPECTED_SHA" in release
  assert "type=raw,value=latest" in release


def test_docker_workflow_release_job_keeps_its_own_queue() -> None:
  """Release merges share a queue without cancellation and have a 10-minute timeout."""
  jobs = YAML(typ="safe").load(WORKFLOW.read_text())["jobs"]
  assert jobs["release-merge"]["concurrency"] == {
    "group": "docker-release",
    "cancel-in-progress": False,
  }
  assert jobs["release-merge"]["timeout-minutes"] == 10


def test_docker_workflow_keeps_least_privilege_at_the_top() -> None:
  """The workflow defaults to read-only access, and publishing jobs request package-write access."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())

  assert data["permissions"] == {"contents": "read"}
  for job in ("dev", "dev-merge", "release-build", "release-merge"):
    assert data["jobs"][job]["permissions"] == {
      "actions": "read",
      "contents": "read",
      "packages": "write",
    }
  # Read-only jobs ask for Actions and contents access without package write access.
  for job in ("changes", "release"):
    assert data["jobs"][job]["permissions"] == {
      "actions": "read",
      "contents": "read",
    }


def test_docker_workflow_dev_job_publishes_dev_tags() -> None:
  """A successful CI run on main publishes dev and dev-COMMIT."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())

  dev = data["jobs"]["dev"]
  assert dev["concurrency"] == {
    "group": "docker-dev-${{ matrix.arch }}",
    "cancel-in-progress": True,
  }

  content = WORKFLOW.read_text()
  assert "github.event.workflow_run.name == 'CI'" in content
  assert "github.event.workflow_run.conclusion == 'success'" in content
  assert "github.event.workflow_run.head_branch == 'main'" in content
  # The checkout uses a fixed trusted ref and only compares the commit from CI.
  assert "ref: main" in content
  assert "Pin tested commit" in content
  assert "type=raw,value=dev\n" in content
  assert "type=raw,value=${{ steps.dev.outputs.version }}" in content
  assert "build-args: VERSION=${{ steps.dev.outputs.version }}" in content
  assert 'startswith("dev-")' in content
  assert "users/$OWNER" in content
  assert "orgs/$OWNER" in content


def test_docker_workflow_dev_job_prunes_the_old_dev_versions() -> None:
  """The rolling dev tag moves, so each build drops the dev versions past the newest 10."""
  content = WORKFLOW.read_text()
  assert "Prune Old Dev Versions" in content
  assert "KEEP: 10" in content
  assert "gh api --method DELETE" in content


def test_docker_workflow_dispatch_chooses_dev_or_release() -> None:
  """One manual run selects a mode, and the release mode asks for a tag."""
  inputs = YAML(typ="safe").load(WORKFLOW.read_text())["on"]["workflow_dispatch"][
    "inputs"
  ]
  assert inputs["mode"]["type"] == "choice"
  assert inputs["mode"]["options"] == ["dev", "release"]
  assert inputs["mode"]["default"] == "dev"
  assert inputs["tag"]["type"] == "string"
  assert inputs["tag"]["required"] is False


def test_docker_workflow_splits_the_two_jobs() -> None:
  """The test checks development and release modes and the Gemini Release Draft workflow name."""
  content = WORKFLOW.read_text()
  assert "inputs.mode == 'dev'" in content
  assert "inputs.mode == 'release'" in content
  assert "github.event.workflow_run.name == 'Gemini Release Draft'" in content


def test_docker_workflow_builds_each_platform_on_a_native_runner() -> None:
  """Each platform uses a native runner without emulation."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())
  content = WORKFLOW.read_text()

  for job in ("dev", "release-build"):
    matrix = data["jobs"][job]["strategy"]["matrix"]["include"]
    assert [entry["arch"] for entry in matrix] == ["amd64", "arm64"]
    assert [entry["runner"] for entry in matrix] == ["ubuntu-24.04", "ubuntu-24.04-arm"]

  assert "docker/setup-qemu-action" not in content
  assert content.count("platforms: linux/${{ matrix.arch }}") == 2
  assert content.count("push-by-digest=true") == 2
  # A merge job puts tags on the manifest list of the 2 digests.
  assert content.count("docker buildx imagetools create") == 2
  assert content.count("merge-multiple: true") == 2
  assert "name: digests-dev-${{ matrix.arch }}" in content
  assert "name: digests-release-${{ matrix.arch }}" in content
  # Check disabled attestations and missing-tag/digest guards.
  assert content.count("provenance: false") == 2
  assert content.count("sbom: false") == 2
  assert content.count("The metadata output holds no tag") == 2
  assert content.count("No digest landed in /tmp/digests") == 2
  # Each -t flag and its tag leave jq as 2 argv entries, or buildx reads a bad reference.
  assert content.count('"-t", .') == 2
  assert '"-t " + .' not in content


def test_docker_workflow_replaces_the_two_known_image_workflow_files() -> None:
  """`docker.yml` exists, while `dev-image.yml` and `image.yml` do not."""
  assert not Path(".github/workflows/dev-image.yml").exists()
  assert not Path(".github/workflows/image.yml").exists()
  assert "docker.yml" in [path.name for path in Path(".github/workflows").glob("*.yml")]


def test_docker_workflow_dev_job_waits_for_ci_on_main() -> None:
  """The workflow-run path requires successful CI on main."""
  content = WORKFLOW.read_text()
  dev = content[content.index("  dev:") : content.index("  release:")]
  assert "github.event.workflow_run.name == 'CI'" in dev
  assert "github.event.workflow_run.conclusion == 'success'" in dev
  assert "github.event.workflow_run.head_branch == 'main'" in dev


DOCKERFILE = Path("Dockerfile")


def test_dockerfile_removes_pip_from_the_image() -> None:
  """The runtime image removes system pip and setuptools."""
  content = DOCKERFILE.read_text()

  # The uninstall needs the system Python: PATH puts the uv venv first and it has no pip.
  assert "/usr/local/bin/python -m pip uninstall -y pip setuptools" in content
  assert "\n  && python -m pip" not in content
  assert "pip install" not in content


def _git(repo: Path, *args: str) -> str:
  """Run one Git command inside a temporary test repo."""
  return subprocess.run(
    ["git", *args],
    cwd=repo,
    capture_output=True,
    text=True,
    check=True,
    env={
      **os.environ,
      "GIT_AUTHOR_NAME": "test",
      "GIT_AUTHOR_EMAIL": "test@example.com",
      "GIT_COMMITTER_NAME": "test",
      "GIT_COMMITTER_EMAIL": "test@example.com",
    },
  ).stdout.strip()


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> str:
  """Write one file in `repo`, commit it, and return the commit hash."""
  target = repo / rel_path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(content, encoding="utf-8")
  _git(repo, "add", rel_path)
  _git(repo, "commit", "-m", message)
  return _git(repo, "rev-parse", "HEAD")


def test_docker_workflow_dev_build_waits_for_image_paths() -> None:
  """The dev image builds only when commits since the last dev publish touched image paths."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())
  jobs = data["jobs"]
  assert jobs["dev"]["needs"] == "changes"
  assert "needs.changes.outputs.build == 'yes'" in jobs["dev"]["if"]
  changes = jobs["changes"]
  assert changes["outputs"] == {"build": "${{ steps.filter.outputs.build }}"}
  assert changes["permissions"] == {"actions": "read", "contents": "read"}
  checkout = changes["steps"][0]
  assert checkout["uses"].startswith("actions/checkout@")
  assert checkout["with"]["ref"] == "main"
  assert checkout["with"]["fetch-depth"] == 0
  step = next(item for item in changes["steps"] if item.get("id") == "filter")
  assert step["env"]["SHA"] == (
    "${{ github.event.workflow_run.head_sha || github.sha }}"
  )
  run = step["run"]
  assert "workflow_dispatch" in run, "a manual dev run always builds"
  for path in (
    "daedalus/*",
    "config/*",
    "hooks/*",
    "pyproject.toml",
    "uv.lock",
    "Dockerfile",
    ".dockerignore",
  ):
    assert path in run, f"{path} changes the image"
  assert "ARENA.md" not in run and "*.md" not in run, (
    "docs alone leave the image the same"
  )


def test_docker_workflow_filter_reads_all_commits_after_last_dev_merge(
  tmp_path: Path,
) -> None:
  """The filter checks every commit after the last `dev-merge` success, not only the tip."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())
  script = next(
    item["run"]
    for item in data["jobs"]["changes"]["steps"]
    if item.get("id") == "filter"
  )
  repo = tmp_path / "repo"
  repo.mkdir()
  _git(repo, "init", "-b", "main")
  base_sha = _commit_file(repo, "README.md", "init\n", "docs: init")
  docs_sha = _commit_file(repo, "ARENA.md", "rules\n", "docs: rules")
  _commit_file(repo, "config/providers/free.yml", "models: {}\n", "feat: models")
  tip_sha = _commit_file(repo, "tests/e2e/package.json", "{}\n", "build: e2e")

  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  gh_stub = bin_dir / "gh"
  gh_stub.write_text(
    "#!/usr/bin/env bash\n"
    'if [[ "$*" == *"workflows/docker.yml/runs"* ]]; then\n'
    '  [[ -n "${NO_PRIOR:-}" ]] && exit 0\n'
    f'  printf "202 {docs_sha}\\n101 {base_sha}\\n"\n'
    'elif [[ "$*" == *"runs/202/jobs"* ]]; then\n'
    "  exit 0\n"
    'elif [[ "$*" == *"runs/101/jobs"* ]]; then\n'
    '  printf "success\\n"\n'
    f'elif [[ "$*" == *"commits/{tip_sha}"* ]]; then\n'
    '  printf "tests/e2e/package.json\\n"\n'
    "else\n"
    '  echo "unexpected gh call: $*" >&2\n'
    "  exit 1\n"
    "fi\n",
    encoding="utf-8",
  )
  gh_stub.chmod(0o755)

  def run_filter(sha: str, event: str = "workflow_run", no_prior: str = "") -> str:
    out_file = tmp_path / "output.txt"
    out_file.write_text("", encoding="utf-8")
    env = {
      **os.environ,
      "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
      "GITHUB_REPOSITORY": "nemoe7/daedalus",
      "GITHUB_EVENT_NAME": event,
      "GITHUB_OUTPUT": str(out_file),
      "SHA": sha,
      "NO_PRIOR": no_prior,
    }
    subprocess.run(["bash", "-c", script], cwd=repo, env=env, check=True)
    return out_file.read_text(encoding="utf-8").strip()

  assert run_filter(tip_sha) == "build=yes"
  assert run_filter(docs_sha) == "build=no"
  assert run_filter(docs_sha, no_prior="1") == "build=yes"
  assert run_filter(docs_sha, event="workflow_dispatch") == "build=yes"


def test_docker_workflow_pin_step_checks_out_tested_main_ancestor(
  tmp_path: Path,
) -> None:
  """The pin step detaches at a tested `main` ancestor and rejects unmerged commits."""
  data = YAML(typ="safe").load(WORKFLOW.read_text())
  pin_script = next(
    item["run"]
    for item in data["jobs"]["dev"]["steps"]
    if item.get("name") == "Pin tested commit"
  )

  repo = tmp_path / "repo"
  repo.mkdir()
  _git(repo, "init", "-b", "main")
  tested_sha = _commit_file(repo, "daedalus/app.py", "x = 1\n", "feat: app")
  _commit_file(repo, "ARENA.md", "sync\n", "chore: sync")

  _git(repo, "checkout", "-b", "side", tested_sha)
  unmerged_sha = _commit_file(repo, "daedalus/app.py", "x = 2\n", "feat: side")
  _git(repo, "checkout", "main")

  rejected = subprocess.run(
    ["bash", "-c", pin_script],
    cwd=repo,
    env={**os.environ, "EXPECTED": unmerged_sha},
    capture_output=True,
    text=True,
    check=False,
  )
  assert rejected.returncode != 0

  accepted = subprocess.run(
    ["bash", "-c", pin_script],
    cwd=repo,
    env={**os.environ, "EXPECTED": tested_sha},
    capture_output=True,
    text=True,
    check=False,
  )
  assert accepted.returncode == 0, accepted.stderr
  assert _git(repo, "rev-parse", "HEAD") == tested_sha
  for job_name in ("dev", "dev-merge"):
    checkout = data["jobs"][job_name]["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["fetch-depth"] == 0
