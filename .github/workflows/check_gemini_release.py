"""Run offline release-history, reduction and API-boundary checks."""

import hashlib
import io
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import gemini_release as release


def fails(call, message):
  try:
    call()
  except RuntimeError as error:
    assert message in str(error), str(error)
  else:
    raise AssertionError(f"Expected error: {message}")


def check():
  original = Path.cwd()
  with tempfile.TemporaryDirectory() as directory:
    os.chdir(directory)

    def git(*args):
      return release.git(*args).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Check")
    git("config", "user.email", "check@example.invalid")
    Path("data").write_text("root marker\n")
    git("add", ".")
    git("commit", "-m", "root change")
    root = git("rev-parse", "HEAD")
    git("tag", "v1")
    fails(lambda: release.tag_sha("1.0.0"), "refs/tags/1.0.0^{commit}")
    fails(lambda: release.tag_sha("1.0.0"), "fatal:")
    fails(lambda: release.tag_sha("1.0.0"), "Push that exact tag before dispatch")

    git("checkout", "-b", "side")
    Path("side").write_text("side marker\n")
    git("add", ".")
    git("commit", "-m", "side change")
    git("checkout", "main")
    Path("data").write_text("root marker\nnext marker\n")
    Path("binary").write_bytes(b"\0\1\2\3")
    git("add", ".")
    git("commit", "-m", "next change")
    git("merge", "--no-ff", "side", "-m", "merge side")
    git("tag", "-a", "v2", "-m", "annotated target")
    target = release.tag_sha("v2")
    git("update-ref", "refs/remotes/origin/main", target)
    assert release.target_commit(root, "main") == root
    assert release.target_commit("", "main") == target
    fails(lambda: release.target_commit("HEAD", "main"), "complete commit SHA")
    tag_object = git("rev-parse", "refs/tags/v2")
    fails(lambda: release.target_commit(tag_object, "main"), "not a tag object")
    rows = [
      {
        "tag_name": "v1",
        "draft": False,
        "prerelease": True,
        "published_at": "2026-01-01T00:00:00Z",
      }
    ]
    assert release.baseline(rows, "v2", target) == ("v1", root)
    assert release.baseline([], "v2", target) == ("", "")
    with patch.object(release, "releases", return_value=rows):
      fails(lambda: release.current_base("owner/repo", target), "prerelease")
    with (
      patch.object(
        release, "releases", return_value=[{**rows[0], "id": 5, "prerelease": False}]
      ),
      patch.object(release, "remote_tag_sha", return_value=root),
    ):
      assert release.current_base("owner/repo", target) == (
        "v1",
        root,
        {"id": 5, "published_at": rows[0]["published_at"]},
      )
    fails(lambda: release.baseline(rows, "v2", target, "absent"), "no published")
    units = release.history(target, root)
    all_units = release.history(target, "")
    text = "\n".join(value for _, value in units)
    assert (
      "side marker" in text and "next marker" in text and "GIT binary patch" in text
    )
    assert root + ":message" not in dict(units)
    assert root + ":message" in dict(all_units)
    assert len([key for key, _ in units if key.startswith(target + ":parent:")]) == 2
    split = release.pieces([("unicode", "é🦀abc" * 20)], size=7)
    assert "".join(p["text"] for p in split) == "é🦀abc" * 20
    os.chdir(original)
  secret = "sentinel-secret-do-not-log"
  with (
    patch.dict(os.environ, {"GH_TOKEN": secret, "GEMINI_API_KEY": secret}),
    patch.object(
      release.subprocess,
      "run",
      return_value=subprocess.CompletedProcess(
        ["gh", "api"],
        1,
        b"",
        f'fatal: denied {secret} "https://storage.invalid/file?sig=private-signature"'.encode(),
      ),
    ),
  ):
    fails(
      lambda: release.run("gh", "api", "repos/owner/repo"), "fatal: denied [REDACTED]"
    )
    try:
      release.run("gh", "api", secret)
    except RuntimeError as error:
      assert secret not in str(error)
      assert "private-signature" not in str(error)
  calls = []

  def generate(context, evidence, model):
    calls.append(evidence)
    return "summary"

  units = [(str(n), "x" * 100) for n in range(8)]
  assert release.release_body(units, {}, "test", generate, limit=350) == "summary"
  raw = [
    item["id"]
    for call in calls
    for item in call
    if not item["id"].startswith("summary:")
  ]
  assert raw == [item["id"] for item in release.pieces(units)]
  fails(lambda: release.release_body([], {}, "test", generate), "No commits")
  fails(
    lambda: release.release_body(
      units, {}, "test", lambda *args: "x" * 1000, limit=350
    ),
    "did not shrink",
  )
  assert (
    release.response_text(
      {
        "candidates": [
          {
            "finishReason": "STOP",
            "content": {
              "parts": [{"text": "hidden", "thought": True}, {"text": "notes"}]
            },
          }
        ]
      }
    )
    == "notes"
  )
  fails(
    lambda: release.response_text({"candidates": [{"finishReason": "MAX_TOKENS"}]}),
    "incomplete",
  )
  fails(
    lambda: release.response_text({"promptFeedback": {"blockReason": "SAFETY"}}),
    "blocked",
  )
  with (
    patch.object(release, "remote_tag_sha", return_value="abc"),
    patch.object(release, "releases", return_value=[{"id": 1, "tag_name": "v2"}]),
    patch.object(release, "api", return_value={"draft": False}) as api,
  ):
    fails(lambda: release.save_draft("owner/repo", "v2", "notes", "abc"), "published")
    assert api.call_count == 1
  phases = []

  def phased(context, evidence, model):
    phases.append(context.get("phase", "release"))
    return "summary"

  release.release_body(
    [(str(n), "x" * 100) for n in range(80)], {}, "test", phased, limit=350
  )
  assert set(phases) == {"chunk", "combine", "release"}
  for phase in ("chunk", "combine", "release", "version"):
    with patch.object(
      release,
      "gemini_call",
      side_effect=[
        {"totalTokens": 20},
        {
          "candidates": [
            {"finishReason": "STOP", "content": {"parts": [{"text": "notes"}]}}
          ]
        },
      ],
    ) as call:
      assert release.generate({"phase": phase}, [], "test") == "notes"
      assert (
        call.call_args_list[1].args[2]["systemInstruction"]["parts"][0]["text"]
        == (release.HERE / f"gemini-{phase}-prompt.txt").read_text()
      )
  payload = {
    "id": 2,
    "tag_name": "v2",
    "name": "v2",
    "body": "notes",
    "draft": True,
    "html_url": "https://github.com/owner/repo/releases/2",
    "target_commitish": "abc",
  }
  with (
    patch.object(release, "remote_tag_sha", return_value="abc"),
    patch.object(release, "releases", return_value=[]),
    patch.object(release, "api", return_value=payload) as api,
  ):
    assert release.save_draft("owner/repo", "v2", "notes", "abc") == payload["html_url"]
    assert api.call_args_list[0].args[1] == "POST"
    assert api.call_args_list[0].args[2]["draft"] is True
  assert release.next_version("", "minor") == "0.1.0"
  assert release.next_version("0.2.5", "major") == "0.3.0"
  assert release.next_version("0.2.5", "patch") == "0.2.6"
  assert release.next_version("0.2.5", "none") is None
  assert release.next_version("0.2.5", "minor", promote=True) == "1.0.0"
  fails(lambda: release.next_version("1.0.0-rc.1", "patch"), "prerelease")
  fails(lambda: release.next_version("1.01.0", "patch"), "stable numeric")
  fails(lambda: release.next_version("1.0.0", "review"), "review")
  sample = {
    "repository": "owner/repo",
    "target": "a" * 40,
    "previous": "",
    "base": "",
    "baseline_release": None,
    "impact": "patch",
    "promote": False,
    "version": "0.1.0",
    "tag": "0.1.0",
    "body": release.HERE.joinpath("gemini-release-template.md")
    .read_text()
    .replace("{{summary}}", "test")
    .replace("{{features}}", "None")
    .replace("{{fixes}}", "None")
    .replace("{{breaking_changes}}", "None")
    .replace("{{upgrade_notes}}", "None")
    .replace("{{comparison_url}}", "https://github.com/owner/repo/commits/" + "a" * 40),
  }
  api_run = {
    "id": 42,
    "run_attempt": 1,
    "workflow_id": 9,
    "path": ".github/workflows/gemini-release.yml",
    "event": "workflow_dispatch",
    "status": "completed",
    "conclusion": "success",
    "head_branch": "main",
    "head_sha": "b" * 40,
    "repository": {"id": 7},
    "head_repository": {"id": 7},
  }
  artifact = {
    "name": "gemini-release-proposal",
    "expired": False,
    "size_in_bytes": 1000,
    "workflow_run": {
      "id": 42,
      "repository_id": 7,
      "head_repository_id": 7,
      "head_sha": "b" * 40,
      "head_branch": "main",
    },
  }

  def test_api(path, method="GET", payload=None):
    if path.endswith("/actions/runs/42"):
      return api_run
    if path.endswith("/actions/workflows/gemini-release.yml"):
      return {"id": 9}
    if path == "repos/owner/repo":
      return {"id": 7}
    if path.endswith("/artifacts?per_page=100"):
      return {"total_count": 1, "artifacts": [artifact]}
    if path.endswith("/git/refs") and method == "POST":
      assert payload == {"ref": "refs/tags/0.1.0", "sha": "a" * 40}
      return {"ref": payload["ref"]}
    raise AssertionError(f"Unexpected API write/read: {path} {method}")

  with (
    patch.dict(os.environ, {"PROPOSAL_RUN_ID": "42"}),
    patch.object(release, "api", side_effect=test_api),
    patch.object(release, "download_proposal", side_effect=lambda *args: sample.copy()),
    patch.object(release, "check_remote_target"),
    patch.object(
      release, "generate", side_effect=AssertionError("Approval called Gemini")
    ),
    patch.object(release, "target_commit", return_value="a" * 40),
    patch.object(release, "current_base", return_value=("", "", None)),
    patch.object(release, "releases", return_value=[]),
    patch.object(release, "remote_ref", return_value=None) as ref,
    patch.object(release, "remote_tag_sha", return_value="a" * 40),
    patch.object(
      release, "save_draft", return_value="https://example.invalid/draft"
    ) as save,
    patch.object(release, "summary"),
  ):
    release.approve("owner/repo", "main")
    assert ref.call_count == 1 and save.call_count == 1
    assert save.call_args.args == ("owner/repo", "0.1.0", sample["body"], "a" * 40)
    api_run["conclusion"] = "failure"
    fails(lambda: release.approve("owner/repo", "main"), "not a successful")
    assert ref.call_count == 1, "failed run must stop before tag lookup"
    api_run["conclusion"] = "success"
    api_run["run_attempt"] = 2
    fails(lambda: release.approve("owner/repo", "main"), "not a successful")
    api_run["run_attempt"] = 1
    artifact["expired"] = True
    fails(lambda: release.approve("owner/repo", "main"), "expired")
    artifact["expired"] = False
    sample["tag"] = "1.0.0"
    fails(lambda: release.approve("owner/repo", "main"), "does not match")
    assert ref.call_count == 1, "altered version must stop before tag lookup"
    sample["tag"] = "0.1.0"
    with patch.object(
      release,
      "current_base",
      return_value=("v0.1.0", "c" * 40, {"id": 1, "published_at": "now"}),
    ):
      fails(lambda: release.approve("owner/repo", "main"), "baseline changed")
    with patch.object(release, "remote_tag_sha", return_value="c" * 40):
      fails(lambda: release.approve("owner/repo", "main"), "conflicts")
    with patch.object(
      release, "remote_ref", return_value={"object": {"sha": "a" * 40}}
    ):
      release.approve("owner/repo", "main")
    assert save.call_count == 2
    with patch.object(release, "save_draft", side_effect=RuntimeError("draft failure")):
      fails(lambda: release.approve("owner/repo", "main"), "draft failure")
    with patch.object(
      release, "releases", return_value=[{"tag_name": "0.1.0", "draft": False}]
    ):
      fails(lambda: release.approve("owner/repo", "main"), "already published")

  def zipped(text, name="proposal.json"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
      archive.writestr(name, text)
    return stream.getvalue()

  for text, name, error in (
    (json.dumps(sample), "proposal.json", ""),
    (json.dumps(sample), "../proposal.json", "unexpected files"),
    ('{"tag":"a","tag":"b"}', "proposal.json", "Duplicate"),
  ):
    data = zipped(text, name)
    item = {"id": 12, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
    with patch.object(release, "run", return_value=data) as download:
      if error:
        fails(lambda item=item: release.download_proposal("owner/repo", item), error)
      else:
        assert release.download_proposal("owner/repo", item) == sample
        assert (
          download.call_args.args[-1] == "repos/owner/repo/actions/artifacts/12/zip"
        )
        assert download.call_args.kwargs["binary"] is True
      item["digest"] = "sha256:" + "0" * 64
      fails(
        lambda item=item: release.download_proposal("owner/repo", item),
        "digest or size mismatch",
      )
      item["digest"] = None
      fails(
        lambda item=item: release.download_proposal("owner/repo", item),
        "no valid digest",
      )
  assert (
    (release.HERE / "gemini-version-prompt.txt")
    .read_text()
    .strip()
    .startswith("Classify the release impact of the supplied commit messages")
  )
  assert release.version_tag("v0.2.0", "0.2.1") == "v0.2.1"
  assert release.next_version("1.2.3", "major") == "2.0.0"
  assert release.next_version("1.2.3", "minor") == "1.3.0"
  fails(lambda: release.next_version("", "minor", promote=True), "Promotion requires")
  fails(
    lambda: release.next_version("1.2.3", "patch", promote=True), "Promotion requires"
  )
  with (
    tempfile.TemporaryDirectory() as directory,
    patch.dict(
      os.environ,
      {
        "PROPOSAL_PATH": str(Path(directory) / "proposal.json"),
        "IMPACT_OVERRIDE": "patch",
        "PROMOTE_TO_STABLE": "false",
      },
    ),
    patch.object(release, "target_commit", return_value="a" * 40),
    patch.object(release, "check_remote_target"),
    patch.object(release, "current_base", return_value=("", "", None)),
    patch.object(release, "api", side_effect=AssertionError("Proposal made API write")),
    patch.object(release, "history", return_value=[("id", "diff")]),
    patch.object(release, "releases", return_value=[]),
    patch.object(release, "remote_ref", return_value=None),
    patch.object(release, "release_body", return_value=sample["body"]) as notes,
    patch.object(release, "summary"),
  ):
    sample["tag"] = "0.1.0"
    release.propose("owner/repo", "main")
    stored = json.loads(Path(directory, "proposal.json").read_text())
    assert stored["target"] == "a" * 40 and stored["version"] == "0.1.0"
    assert notes.call_count == 1, "the override avoids a classifier call"
    with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
      fails(lambda: release.propose("owner/repo", "main"), "reruns are not allowed")
    with patch.dict(os.environ, {"IMPACT_OVERRIDE": "none"}):
      release.propose("owner/repo", "main")
      assert not Path(directory, "proposal.json").exists(), "none must not upload"
  with patch.object(release, "release_body", return_value="minor"):
    assert release.classify([("id", "diff")], {}, "test") == "minor"
  with patch.object(release, "release_body", return_value="maybe minor"):
    fails(
      lambda: release.classify([("id", "diff")], {}, "test"),
      "invalid version classification",
    )
  with patch.object(
    release, "api", side_effect=[{"commit": {"sha": "b" * 40}}, {"status": "diverged"}]
  ):
    fails(
      lambda: release.check_remote_target("owner/repo", "main", "a" * 40),
      "no longer reachable",
    )
  with (
    patch.object(release, "git"),
    patch.object(release, "api", side_effect=RuntimeError("HTTP 401")),
  ):
    fails(lambda: release.remote_ref("owner/repo", "0.1.0"), "HTTP 401")
  with (
    patch.object(release, "git"),
    patch.object(release, "api", side_effect=RuntimeError("HTTP 404")),
  ):
    assert release.remote_ref("owner/repo", "0.1.0") is None
  print("Offline Gemini release checks passed")


if __name__ == "__main__":
  check()
