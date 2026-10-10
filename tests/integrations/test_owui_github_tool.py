"""The Open WebUI GitHub tool holds the spec surface, gates every write and propagates errors."""

import ast
import asyncio
import base64
import importlib.util
import io
import json
import time
from pathlib import Path
from typing import Self
from urllib.error import HTTPError

import pytest

TOOL = (
  Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "tools"
  / "github.py"
)
SPEC = importlib.util.spec_from_file_location("owui_github_tool", TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

TOOLS = [
  "actions_minutes",
  "add_comment_to_issue",
  "add_issue_assignees",
  "add_issue_labels",
  "add_review_to_pr",
  "compare_commits",
  "convert_pull_request_to_draft",
  "create_branch",
  "create_file",
  "create_gist",
  "create_issue",
  "create_pull_request",
  "create_pr_with_files",
  "code_scanning_alerts",
  "check_runs",
  "delete_file",
  "delete_gist",
  "dependabot_alerts",
  "dismiss_pull_request_review",
  "download_workflow_artifact",
  "fetch_github_url",
  "fetch_blob",
  "fetch_commit",
  "fetch_commit_workflow_runs",
  "fetch_file",
  "fetch_gist",
  "fetch_issue",
  "fetch_issue_comments",
  "fetch_pr",
  "fetch_pr_comments",
  "fetch_pr_file_patch",
  "fetch_pr_patch",
  "fetch_workflow_job_logs",
  "fetch_workflow_job_steps",
  "fetch_workflow_run_artifacts",
  "fetch_workflow_run_jobs",
  "get_commit_combined_status",
  "gists",
  "get_pr_diff",
  "get_pr_info",
  "get_profile",
  "get_repo",
  "get_repo_collaborator_permission",
  "label_pr",
  "list_installations",
  "list_commits",
  "list_pr_changed_filenames",
  "list_pull_request_review_threads",
  "list_pull_request_reviews",
  "list_recent_issues",
  "list_repositories",
  "list_tree",
  "list_workflows",
  "packages",
  "list_user_orgs",
  "mark_pull_request_ready_for_review",
  "merge_pull_request",
  "remove_issue_assignees",
  "remove_issue_label",
  "releases",
  "reply_to_review_comment",
  "request_pull_request_reviewers",
  "rerun_failed_workflow_run_jobs",
  "resolve_review_thread",
  "search",
  "search_branches",
  "search_commits",
  "search_issues",
  "search_prs",
  "tags",
  "search_repositories",
  "secret_scanning_alerts",
  "update_code_scanning_alert",
  "unresolve_review_thread",
  "update_file",
  "update_dependabot_alert",
  "update_issue",
  "update_gist",
  "update_issue_comment",
  "update_pull_request",
  "update_secret_scanning_alert",
  "update_ref",
  "update_review_comment",
]


class Answer:
  """A FakeResponse for urlopen: read returns the body, the context manager closes."""

  def __init__(self, body: str):
    self.body = body.encode()

  def read(self) -> bytes:
    return self.body

  def __enter__(self) -> Self:
    return self

  def __exit__(self, *args: object) -> bool:
    return False


def opener(monkeypatch, body, calls=None, error=None):
  """Patch module urlopen and record each request. A list body answers in order."""
  replies = list(body) if isinstance(body, list) else None
  served = 0

  def urlopen(request, timeout=None):
    nonlocal served
    if calls is not None:
      calls.append(
        {
          "method": request.get_method(),
          "url": request.full_url,
          "headers": dict(request.headers),
          "data": request.data,
        }
      )
    if error is not None:
      raise error
    if replies is None:
      return Answer(body)
    answer = replies[min(served, len(replies) - 1)]
    served += 1
    return Answer(answer)

  monkeypatch.setattr(tool, "urlopen", urlopen)


def client(token: str = "t0ken") -> "tool.Tools":
  """A tool with a token, bypassing the environment."""

  instance = tool.Tools()
  instance.valves.github_token = token
  instance.valves.permissions = "Allow reads"
  return instance


def test_the_surface_holds_the_tool_list():
  tree = ast.parse(TOOL.read_text())
  cls = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "Tools"
  )
  found = [
    node.name
    for node in cls.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and not node.name.startswith("_")
  ]
  assert sorted(found) == sorted(TOOLS)
  assert len(TOOLS) == 83


def test_the_gate_defaults_to_always_ask_with_sixty_seconds():
  instance = tool.Tools()
  assert instance.valves.permissions == "Always ask"
  assert instance.valves.timeout_seconds == 60
  assert tool.Tools.UserValves().mode == "default"
  assert tool.Tools.UserValves().timeout_seconds == 0


def test_always_ask_holds_a_read_without_a_dialog():
  instance = tool.Tools()
  instance.valves.github_token = "t0ken"
  out = asyncio.run(instance.list_commits("owner/repo"))
  assert out["result"]["denied"] is True


def test_every_write_goes_through_the_gate():
  tree = ast.parse(TOOL.read_text())
  cls = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "Tools"
  )
  writers = {"POST", "PUT", "PATCH", "DELETE"}
  for node in cls.body:
    if not isinstance(
      node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) or node.name.startswith("_"):
      continue
    source = ast.dump(node)
    mutates = any(f"'{method}'" in source for method in writers)
    gated = "_write" in source
    assert mutates <= gated, f"{node.name} sends a write without the gate"


def test_an_unanswered_write_is_denied_and_sends_nothing(monkeypatch):
  calls = []
  opener(monkeypatch, "{}", calls)
  out = asyncio.run(client().label_pr("o/r", 1, "bug", __event_call__=None))
  assert out["result"]["denied"] is True
  assert "I sent nothing" in out["result"]["reason"]
  assert calls == []


def test_a_confirmed_write_sends_one_request(monkeypatch):
  calls = []
  opener(monkeypatch, '{"ok": true}', calls)

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(client().label_pr("o/r", 7, "bug", __event_call__=answer))
  assert out["result"]["success"] is True
  assert calls[0]["method"] == "POST"
  assert calls[0]["url"] == "https://api.github.com/repos/o/r/issues/7/labels"
  assert json.loads(calls[0]["data"]) == {"labels": ["bug"]}
  assert calls[0]["headers"]["Authorization"] == "Bearer t0ken"


def test_a_slow_answer_denies_at_the_timeout(monkeypatch):
  calls = []
  opener(monkeypatch, "{}", calls)
  instance = client()
  instance.valves.timeout_seconds = 1

  async def slow(payload):
    await asyncio.sleep(5)
    return True

  started = time.monotonic()
  out = asyncio.run(instance.remove_issue_label("o/r", 3, "stale", __event_call__=slow))
  assert out["result"]["denied"] is True
  assert time.monotonic() - started < 3
  assert calls == []


def test_a_user_timeout_wins_over_the_tool_valve(monkeypatch):
  calls = []
  opener(monkeypatch, "{}", calls)
  instance = client()
  instance.valves.timeout_seconds = 60
  for user in (
    {"valves": tool.Tools.UserValves(timeout_seconds=1)},
    {"valves": {"timeout_seconds": 1}},
  ):

    async def slow(payload):
      await asyncio.sleep(5)
      return True

    started = time.monotonic()
    out = asyncio.run(
      instance.remove_issue_label("o/r", 3, "stale", __user__=user, __event_call__=slow)
    )
    assert out["result"]["denied"] is True
    assert "1s" in out["result"]["reason"]
    assert time.monotonic() - started < 3
  assert calls == []


def test_file_reads_decode_and_search_hits_the_global_route(monkeypatch):
  calls = []
  body = json.dumps(
    {
      "name": "a.py",
      "path": "a.py",
      "sha": "s1",
      "encoding": "base64",
      "content": base64.b64encode(b"print(1)\n").decode(),
      "html_url": "https://github.com/o/r/blob/main/a.py",
    }
  )
  opener(monkeypatch, body, calls)
  out = asyncio.run(client().fetch_file("o/r", "a.py", ref="feat/x"))
  assert out["result"]["content"] == "print(1)\n"
  assert (
    calls[0]["url"] == "https://api.github.com/repos/o/r/contents/a.py?ref=feat%2Fx"
  )

  calls.clear()
  opener(monkeypatch, json.dumps({"items": [{"path": "p", "html_url": "u"}]}), calls)
  out = asyncio.run(client().search("needle", 5, repository_name="o/r"))
  assert calls[0]["url"].startswith("https://api.github.com/search/code?")
  assert "q=needle+repo%3Ao%2Fr" in calls[0]["url"]
  assert out["result"]["results"][0]["display_url"] == "u"


def test_list_commits_filters_by_path(monkeypatch):
  calls = []
  opener(monkeypatch, json.dumps([{"sha": "abc", "commit": {"message": "x"}}]), calls)
  out = asyncio.run(
    client().list_commits("o/r", path="src/app.py", since="2026-01-01T00:00:00Z")
  )
  assert out["result"]["commits"][0]["sha"] == "abc"
  assert "path=src%2Fapp.py" in calls[0]["url"]
  assert "since=2026-01-01T00%3A00%3A00Z" in calls[0]["url"]


def test_list_tree_is_recursive_and_filtered(monkeypatch):
  calls = []
  body = json.dumps(
    {
      "truncated": False,
      "tree": [
        {"path": "src/a.py", "type": "blob", "sha": "1"},
        {"path": "docs/b.md", "type": "blob", "sha": "2"},
      ],
    }
  )
  opener(monkeypatch, body, calls)
  out = asyncio.run(client().list_tree("o/r", ref="main", path_prefix="src/"))
  assert [entry["path"] for entry in out["result"]["tree"]] == ["src/a.py"]
  assert out["result"]["truncated"] is False
  assert (
    calls[0]["url"] == "https://api.github.com/repos/o/r/git/trees/main?recursive=1"
  )


def test_create_pr_with_files_moves_a_branch_in_one_gate(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps({"default_branch": "main"}),
      json.dumps({"object": {"sha": "parent1"}}),
      json.dumps({"tree": {"sha": "tree0"}}),
      json.dumps({"sha": "blob1"}),
      json.dumps({"sha": "tree1"}),
      json.dumps({"sha": "commit1"}),
      json.dumps({"ref": "refs/heads/feat/x"}),
      json.dumps([{"number": 5, "html_url": "u", "title": "t"}]),
    ],
    calls,
  )

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(
    client().create_pr_with_files(
      "o/r",
      [{"path": "a.py", "content": "print(1)\n"}, {"path": "b.md", "delete": True}],
      "feat/x",
      title="t",
      __event_call__=answer,
    )
  )
  assert out["result"]["commit_sha"] == "commit1"
  assert out["result"]["updated"] is True
  assert out["result"]["pull_request"]["number"] == 5
  assert [call["method"] for call in calls] == [
    "GET",
    "GET",
    "GET",
    "POST",
    "POST",
    "POST",
    "PATCH",
    "GET",
  ]
  tree = json.loads(calls[4]["data"])
  assert tree["base_tree"] == "tree0"
  assert tree["tree"][0]["sha"] == "blob1"
  assert tree["tree"][1] == {
    "path": "b.md",
    "mode": "100644",
    "type": "blob",
    "sha": None,
  }


def test_create_pr_with_files_without_a_dialog_sends_nothing(monkeypatch):
  calls = []
  opener(monkeypatch, "{}", calls)
  out = asyncio.run(
    client().create_pr_with_files(
      "o/r", [{"path": "a.py", "content": "x"}], "feat/x", __event_call__=None
    )
  )
  assert out["result"]["denied"] is True
  assert calls == []


def test_code_scanning_alerts_filter_and_read_instances(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [json.dumps({"alerts": []}), json.dumps({"number": 7}), json.dumps([])],
    calls,
  )
  out = asyncio.run(
    client().code_scanning_alerts("o/r", tool_name="CodeQL", severity="error")
  )
  assert out["result"]["alerts"] == {"alerts": []}
  assert "tool_name=CodeQL" in calls[0]["url"]
  assert "severity=error" in calls[0]["url"]
  assert "state=open" in calls[0]["url"]
  detail = asyncio.run(client().code_scanning_alerts("o/r", alert_number=7))
  assert detail["result"]["alert"]["number"] == 7
  assert calls[1]["url"].endswith("/code-scanning/alerts/7")
  assert calls[2]["url"].endswith("/code-scanning/alerts/7/instances")


def test_secret_scanning_alerts_read_locations(monkeypatch):
  calls = []
  opener(
    monkeypatch, [json.dumps({"number": 3}), json.dumps([{"path": "a.env"}])], calls
  )
  out = asyncio.run(client().secret_scanning_alerts("o/r", alert_number=3))
  assert out["result"]["locations"][0]["path"] == "a.env"
  assert calls[0]["url"].endswith("/secret-scanning/alerts/3")
  assert calls[1]["url"].endswith("/secret-scanning/alerts/3/locations")


def test_dependabot_alerts_filter_and_read_one(monkeypatch):
  calls = []
  opener(monkeypatch, [json.dumps([{"number": 1}]), json.dumps({"number": 9})], calls)
  out = asyncio.run(client().dependabot_alerts("o/r", severity="high", ecosystem="pip"))
  assert out["result"]["alerts"] == [{"number": 1}]
  assert "severity=high" in calls[0]["url"]
  assert "ecosystem=pip" in calls[0]["url"]
  detail = asyncio.run(client().dependabot_alerts("o/r", alert_number=9))
  assert detail["result"]["alert"]["number"] == 9
  assert calls[1]["url"].endswith("/dependabot/alerts/9")


def test_check_runs_list_and_annotations(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps(
        {
          "total_count": 1,
          "check_runs": [{"id": 4, "name": "ci", "conclusion": "failure"}],
        }
      ),
      json.dumps({"id": 4, "name": "ci"}),
      json.dumps([{"path": "a.py", "annotation_level": "failure", "message": "boom"}]),
    ],
    calls,
  )
  out = asyncio.run(
    client().check_runs("o/r", "1595387", check_name="ci", filter="latest")
  )
  assert out["result"]["check_runs"][0]["conclusion"] == "failure"
  assert "check_name=ci" in calls[0]["url"]
  assert "filter=latest" in calls[0]["url"]
  assert calls[0]["url"].startswith(
    "https://api.github.com/repos/o/r/commits/1595387/check-runs?"
  )
  detail = asyncio.run(client().check_runs("o/r", "1595387", check_run_id=4))
  assert detail["result"]["annotations"][0]["message"] == "boom"
  assert calls[1]["url"].endswith("/check-runs/4")
  assert calls[2]["url"].endswith("/check-runs/4/annotations")


def test_list_workflows(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    json.dumps({"total_count": 2, "workflows": [{"id": 1}, {"id": 2}]}),
    calls,
  )
  out = asyncio.run(client().list_workflows("o/r"))
  assert out["result"]["total_count"] == 2
  assert len(out["result"]["workflows"]) == 2
  assert calls[0]["url"].endswith("/actions/workflows?per_page=30&page=1")


def test_actions_minutes_org_and_user(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [json.dumps({"total_minutes_used": 10}), json.dumps({"total_minutes_used": 2})],
    calls,
  )
  out = asyncio.run(client().actions_minutes("acme"))
  assert out["result"]["minutes"]["total_minutes_used"] == 10
  assert calls[0]["url"] == "https://api.github.com/orgs/acme/settings/billing/actions"
  out = asyncio.run(client().actions_minutes("nemo", owner_type="user"))
  assert out["result"]["minutes"]["total_minutes_used"] == 2
  assert calls[1]["url"] == "https://api.github.com/users/nemo/settings/billing/actions"


def test_releases_list_tag_and_id(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [json.dumps([{"id": 1}]), json.dumps({"id": 2}), json.dumps({"id": 3})],
    calls,
  )
  out = asyncio.run(client().releases("o/r"))
  assert out["result"]["releases"][0]["id"] == 1
  assert calls[0]["url"].endswith("/releases?per_page=30&page=1")
  out = asyncio.run(client().releases("o/r", tag="v1.2.3"))
  assert out["result"]["release"]["id"] == 2
  assert calls[1]["url"].endswith("/releases/tags/v1.2.3")
  out = asyncio.run(client().releases("o/r", release_id=3))
  assert out["result"]["release"]["id"] == 3
  assert calls[2]["url"].endswith("/releases/3")


def test_tags_and_packages(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps([{"name": "v1"}]),
      json.dumps([{"name": "left-pad"}]),
      json.dumps({"name": "left-pad", "package_type": "npm"}),
      json.dumps([{"name": "1.0.0"}]),
    ],
    calls,
  )
  out = asyncio.run(client().tags("o/r"))
  assert out["result"]["tags"][0]["name"] == "v1"
  assert calls[0]["url"].endswith("/tags?per_page=30&page=1")
  out = asyncio.run(client().packages("npm"))
  assert out["result"]["packages"][0]["name"] == "left-pad"
  assert calls[1]["url"] == "https://api.github.com/user/packages?package_type=npm"
  out = asyncio.run(client().packages("npm", owner="acme", package_name="left-pad"))
  assert out["result"]["package"]["name"] == "left-pad"
  assert out["result"]["versions"][0]["name"] == "1.0.0"
  assert calls[2]["url"] == "https://api.github.com/orgs/acme/packages/npm/left-pad"
  assert calls[3]["url"].endswith("/packages/npm/left-pad/versions?per_page=30&page=1")


def test_gists_list_and_fetch(monkeypatch):
  calls = []
  opener(monkeypatch, [json.dumps([{"id": "abc"}]), json.dumps({"id": "abc"})], calls)
  out = asyncio.run(client().gists())
  assert out["result"]["gists"][0]["id"] == "abc"
  assert calls[0]["url"].startswith("https://api.github.com/gists?")
  out = asyncio.run(client().gists("nemo"))
  assert calls[1]["url"].startswith("https://api.github.com/users/nemo/gists?")
  out = asyncio.run(client().fetch_gist("abc"))
  assert out["result"]["gist"]["id"] == "abc"
  assert calls[2]["url"].endswith("/gists/abc")


def test_gist_writes_are_gated(monkeypatch):
  calls = []
  opener(monkeypatch, [json.dumps({"id": "new"}), "", json.dumps({"id": "new"})], calls)
  denied = asyncio.run(
    client().create_gist({"a.txt": {"content": "x"}}, __event_call__=None)
  )
  assert denied["result"]["denied"] is True
  assert calls == []

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(
    client().create_gist(
      {"a.txt": {"content": "x"}}, description="d", __event_call__=answer
    )
  )
  assert out["result"]["gist"]["id"] == "new"
  body = json.loads(calls[0]["data"])
  assert body["files"] == {"a.txt": {"content": "x"}}
  assert body["public"] is False
  out = asyncio.run(client().delete_gist("new", __event_call__=answer))
  assert out["result"]["deleted"] is True
  assert calls[1]["method"] == "DELETE"
  assert calls[1]["url"].endswith("/gists/new")


def test_gist_update_payload(monkeypatch):
  calls = []
  opener(monkeypatch, [json.dumps({"id": "abc"})], calls)

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(
    client().update_gist(
      "abc", files={"a.txt": {"content": "y"}}, description="d", __event_call__=answer
    )
  )
  assert out["result"]["gist"]["id"] == "abc"
  body = json.loads(calls[0]["data"])
  assert body == {"files": {"a.txt": {"content": "y"}}, "description": "d"}


def test_alert_writes_patch_and_deny(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [json.dumps({"number": 7}), json.dumps({"number": 8}), json.dumps({"number": 9})],
    calls,
  )
  denied = asyncio.run(client().update_dependabot_alert("o/r", 9, __event_call__=None))
  assert denied["result"]["denied"] is True
  assert calls == []

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(
    client().update_code_scanning_alert(
      "o/r", 7, reason="won't fix", comment="c", __event_call__=answer
    )
  )
  assert out["result"]["alert"]["number"] == 7
  assert json.loads(calls[0]["data"]) == {
    "state": "dismissed",
    "dismissed_reason": "won't fix",
    "dismissed_comment": "c",
  }
  assert calls[0]["method"] == "PATCH"
  assert calls[0]["url"].endswith("/code-scanning/alerts/7")
  out = asyncio.run(
    client().update_secret_scanning_alert(
      "o/r", 8, resolution="revoked", comment="c", __event_call__=answer
    )
  )
  assert out["result"]["alert"]["number"] == 8
  assert json.loads(calls[1]["data"]) == {
    "state": "resolved",
    "resolution": "revoked",
    "resolution_comment": "c",
  }
  assert calls[1]["url"].endswith("/secret-scanning/alerts/8")
  out = asyncio.run(
    client().update_dependabot_alert(
      "o/r", 9, reason="tolerable_risk", __event_call__=answer
    )
  )
  assert out["result"]["alert"]["number"] == 9
  assert json.loads(calls[2]["data"]) == {
    "state": "dismissed",
    "dismissed_reason": "tolerable_risk",
  }
  assert calls[2]["url"].endswith("/dependabot/alerts/9")


def test_a_github_error_propagates(monkeypatch):
  error = HTTPError(
    "https://api.github.com/x",
    404,
    "Not Found",
    {},
    io.BytesIO(b'{"message":"Not Found"}'),
  )
  opener(monkeypatch, "{}", None, error)
  with pytest.raises(tool.GitHubError) as info:
    asyncio.run(client().get_repo("o/r"))
  assert info.value.status == 404
  assert "Not Found" in str(info.value)


def test_the_url_reader_carries_a_github_name():
  """The GitHub url reader names GitHub, and its 422 names the builtin reader."""
  assert hasattr(tool.Tools, "fetch_github_url"), "the tool carries a GitHub name"
  assert not hasattr(tool.Tools, "fetch"), "the short name that hid the builtin is gone"
  with pytest.raises(tool.GitHubError) as info:
    asyncio.run(client().fetch_github_url("https://example.com/page"))
  assert info.value.status == 422
  assert "fetch_url" in str(info.value), "the error names the builtin reader"
