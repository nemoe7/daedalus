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
