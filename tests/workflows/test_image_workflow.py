"""One Docker workflow publishes development and release images."""

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
  # The release job only reads the approval, so it never asks for a publish scope.
  assert data["jobs"]["release"]["permissions"] == {
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
  assert "Pin the tested commit" in content
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
  # A merge job puts the tags on the manifest list of the 2 digests.
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
