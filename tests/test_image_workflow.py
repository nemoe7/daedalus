from pathlib import Path

from ruamel.yaml import YAML


def test_image_workflow_release_triggers_and_tag_flow():
  workflow = Path(".github/workflows/image.yml")
  data = YAML(typ="safe").load(workflow.read_text())

  triggers = data["on"]
  assert triggers["push"]["tags"] == ["v*"]
  assert triggers["workflow_run"] == {
    "workflows": ["Gemini Release Draft"],
    "types": ["completed"],
  }
  assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True

  content = workflow.read_text()
  assert 'select(.name == "draft")' in content
  assert 'select(.name == "Approve Proposal")' in content
  assert "Approved proposal tag not found in job logs" in content
  assert "type=raw,value=${{ steps.release.outputs.tag }}" in content
  assert "ref: ${{ steps.release.outputs.tag }}" in content
  assert "build-args: VERSION=${{ steps.release.outputs.tag }}" in content


def test_dev_image_workflow_triggers_and_publishes_dev_tags() -> None:
  """The Dev Image workflow publishes dev and dev-COMMIT after CI on main."""
  workflow = Path(".github/workflows/dev-image.yml")
  data = YAML(typ="safe").load(workflow.read_text())

  triggers = data["on"]
  assert triggers["workflow_run"] == {
    "workflows": ["CI"],
    "types": ["completed"],
    "branches": ["main"],
  }
  assert triggers["workflow_dispatch"] is None
  assert data["concurrency"] == {"group": "dev-image", "cancel-in-progress": True}
  assert data["permissions"] == {
    "actions": "read",
    "contents": "read",
    "packages": "write",
  }

  content = workflow.read_text()
  assert "github.event.workflow_run.conclusion == 'success'" in content
  assert "ref: ${{ github.event.workflow_run.head_sha || github.sha }}" in content
  assert "type=raw,value=dev\n" in content
  assert "type=raw,value=${{ steps.dev.outputs.version }}" in content
  assert "build-args: VERSION=${{ steps.dev.outputs.version }}" in content
  assert 'startswith("dev-")' in content
  assert "users/$OWNER" in content
  assert "orgs/$OWNER" in content
