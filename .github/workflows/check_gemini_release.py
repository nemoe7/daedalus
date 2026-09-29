"""Run offline release-history, reduction and API-boundary checks."""

import os
import tempfile
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
  for phase in ("chunk", "combine", "release"):
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
  print("Offline Gemini release checks passed")


if __name__ == "__main__":
  check()
