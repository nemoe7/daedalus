"""Test GitHub tool discovery, permission gates, consolidated modes, and errors."""

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
from urllib.parse import parse_qs, urlsplit

import pytest

TOOL = (
  Path(__file__).resolve().parents[4]
  / "integrations"
  / "openwebui"
  / "tools"
  / "github.py"
)
SPEC = importlib.util.spec_from_file_location("owui_github_tool", TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

TOOLS = [
  "add_review_to_pr",
  "compare_commits",
  "create_issue",
  "create_pr_with_files",
  "create_pull_request",
  "dismiss_pull_request_review",
  "edit_assignees",
  "edit_labels",
  "fetch_commit",
  "fetch_github_file",
  "fetch_github_url",
  "fetch_issue",
  "fetch_pr",
  "fetch_pr_comments",
  "fetch_pr_diff",
  "get_repo",
  "inspect_github_account",
  "inspect_workflow_run",
  "list_commits",
  "list_github_check_runs",
  "list_github_packages",
  "list_github_releases",
  "list_pull_request_reviews",
  "list_recent_issues",
  "list_repo_refs",
  "list_repositories",
  "list_security_alerts",
  "list_tree",
  "merge_pull_request",
  "read_gist",
  "request_pull_request_reviewers",
  "rerun_failed_workflow_run_jobs",
  "search_github",
  "set_review_thread",
  "update_issue",
  "update_pull_request",
  "update_security_alert",
  "write_branch",
  "write_gist",
  "write_github_file",
  "write_issue_comment",
  "write_review_comment",
]
EXPECTED_TOOL_COUNT = 42


class Answer:
  """Wrap a text response for urlopen with context-manager support."""

  def __init__(self, body: str):
    self.body = body.encode()

  def read(self) -> bytes:
    return self.body

  def __enter__(self) -> Self:
    return self

  def __exit__(self, *args: object) -> bool:
    return False


def opener(monkeypatch, body, calls=None, error=None):
  """Record requests and return body or each list item in order."""
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
  """Build a `Tools` client with a token and `Allow reads` enabled."""
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
  assert len(found) == EXPECTED_TOOL_COUNT


def test_the_gate_defaults_to_confirmation_and_user_inheritance():
  instance = tool.Tools()
  assert instance.valves.permissions == "Always ask"
  assert instance._wait_seconds() > 0
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
    mutates = any(f"'{method}'" in source for method in writers) or any(
      isinstance(value, ast.Constant)
      and isinstance(value.value, str)
      and value.value.lstrip().startswith("mutation")
      for value in ast.walk(node)
    )
    gated = "_write" in source
    assert mutates <= gated, f"{node.name} sends a write without the gate"


def test_an_unanswered_write_is_denied_and_sends_nothing(monkeypatch):
  calls = []
  opener(monkeypatch, "{}", calls)
  out = asyncio.run(client().edit_labels("o/r", 1, "bug", __event_call__=None))
  assert out["result"]["denied"] is True
  assert isinstance(out["result"]["reason"], str) and out["result"]["reason"].strip()
  assert calls == []


@pytest.mark.parametrize(
  "method, args, kwargs",
  [
    ("set_review_thread", ("thread-id",), {"resolved": True}),
    ("set_review_thread", ("thread-id",), {"resolved": False}),
    ("dismiss_pull_request_review", ("review-id", "New review needed."), {}),
    ("update_pull_request", ("o/r", 1), {"draft": True}),
    ("update_pull_request", ("o/r", 1), {"draft": False}),
  ],
)
def test_graphql_writes_without_a_dialog_send_nothing(
  monkeypatch, method, args, kwargs
):
  calls = []
  opener(monkeypatch, '{"node_id": "PR_test"}', calls)
  out = asyncio.run(getattr(client(), method)(*args, **kwargs, __event_call__=None))
  assert calls == []
  assert out["result"]["denied"] is True
  assert isinstance(out["result"]["reason"], str) and out["result"]["reason"].strip()


def test_a_confirmed_write_sends_one_request(monkeypatch):
  calls = []
  opener(monkeypatch, '[{"name": "bug"}]', calls)

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(client().edit_labels("o/r", 7, "bug", __event_call__=answer))
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
  out = asyncio.run(
    instance.edit_labels("o/r", 3, "stale", action="remove", __event_call__=slow)
  )
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
      instance.edit_labels(
        "o/r",
        3,
        "stale",
        action="remove",
        __user__=user,
        __event_call__=slow,
      )
    )
    assert out["result"]["denied"] is True
    assert instance._wait_seconds(user) == 1
    assert isinstance(out["result"]["reason"], str) and out["result"]["reason"].strip()
    assert time.monotonic() - started < 3
  assert calls == []


def test_issue_comments_assignees_and_review_comments(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps([{"id": 10, "body": "first"}]),
      json.dumps({"id": 11, "body": "added"}),
      json.dumps({"id": 11, "body": "edited"}),
      json.dumps({"number": 2, "html_url": "u", "title": "t"}),
      json.dumps({"number": 2, "html_url": "u", "title": "t"}),
      json.dumps({"id": 21, "body": "reply"}),
      json.dumps({"id": 21, "body": "updated"}),
    ],
    calls,
  )

  async def answer(payload):
    return payload["type"] == "confirmation"

  comments = asyncio.run(
    client().fetch_issue(2, repository_full_name="o/r", comments_only=True)
  )
  assert comments["result"]["comments"][0]["id"] == 10
  added = asyncio.run(
    client().write_issue_comment("o/r", "added", issue_number=2, __event_call__=answer)
  )
  assert added["result"]["id"] == 11
  edited = asyncio.run(
    client().write_issue_comment("o/r", "edited", comment_id=11, __event_call__=answer)
  )
  assert edited["result"]["body"] == "edited"
  asyncio.run(
    client().edit_assignees("o/r", 2, ["alice"], action="add", __event_call__=answer)
  )
  asyncio.run(
    client().edit_assignees("o/r", 2, ["alice"], action="remove", __event_call__=answer)
  )
  assert calls[3]["method"] == "POST" and calls[4]["method"] == "DELETE"
  reply = asyncio.run(
    client().write_review_comment(
      "o/r",
      "reply",
      pr_number=5,
      reply_to_comment_id=9,
      __event_call__=answer,
    )
  )
  assert reply["result"]["id"] == 21
  updated = asyncio.run(
    client().write_review_comment(
      "o/r", "updated", comment_id=21, __event_call__=answer
    )
  )
  assert updated["result"]["body"] == "updated"


def test_pr_reads_diffs_reviews_and_draft_toggle(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps({"number": 4, "title": "PR"}),
      "diff --git a/x b/x",
      json.dumps([{"filename": "x.py", "patch": "@@ -1 +1 @@", "blob_url": "u"}]),
      json.dumps([{"filename": "x.py", "patch": "@@ -1 +1 @@", "blob_url": "u"}]),
      json.dumps(
        {
          "data": {
            "repository": {
              "pullRequest": {
                "reviews": {"nodes": [{"id": "R1"}]},
              }
            }
          }
        }
      ),
      json.dumps(
        {
          "data": {
            "repository": {
              "pullRequest": {
                "reviewThreads": {"nodes": [{"id": "T1", "isResolved": False}]},
              }
            }
          }
        }
      ),
      json.dumps({"data": {"repository": {"pullRequest": {"id": "PR_4"}}}}),
      json.dumps(
        {
          "data": {
            "convertPullRequestToDraft": {
              "pullRequest": {"id": "PR_4", "isDraft": True}
            }
          }
        }
      ),
    ],
    calls,
  )

  async def answer(payload):
    return payload["type"] == "confirmation"

  info = asyncio.run(client().fetch_pr("o/r", 4, include_diff=False))
  assert info["result"]["number"] == 4
  patch = asyncio.run(client().fetch_pr_diff("o/r", 4, mode="patch"))
  assert patch["result"]["diff"].startswith("diff --git")
  names = asyncio.run(client().fetch_pr_diff("o/r", 4, mode="filenames"))
  assert names["result"]["filenames"] == ["x.py"]
  one_file = asyncio.run(client().fetch_pr_diff("o/r", 4, mode="file", path="x.py"))
  assert one_file["result"]["patch"]["filename"] == "x.py"
  both = asyncio.run(client().list_pull_request_reviews("o/r", 4, target="both"))
  assert both["result"]["reviews"][0]["id"] == "R1"
  assert both["result"]["review_threads"][0]["id"] == "T1"
  drafted = asyncio.run(
    client().update_pull_request("o/r", 4, draft=True, __event_call__=answer)
  )
  assert drafted["result"]["isDraft"] is True


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
  out = asyncio.run(client().fetch_github_file("o/r", "a.py", ref="feat/x"))
  assert out["result"]["content"] == "print(1)\n"
  assert (
    calls[0]["url"] == "https://api.github.com/repos/o/r/contents/a.py?ref=feat%2Fx"
  )

  calls.clear()
  opener(monkeypatch, json.dumps({"items": [{"path": "p", "html_url": "u"}]}), calls)
  out = asyncio.run(
    client().search_github("needle", scope="code", topn=5, repository_full_name="o/r")
  )
  assert calls[0]["url"].startswith("https://api.github.com/search/code?")
  assert "q=needle+repo%3Ao%2Fr" in calls[0]["url"]
  assert out["result"]["results"][0]["display_url"] == "u"


def test_write_file_and_branch_actions(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps({"commit": {"sha": "c1"}, "content": {"sha": "b1"}}),
      json.dumps({"commit": {"sha": "c2"}, "content": {"sha": "b2"}}),
      json.dumps({"commit": {"sha": "c3"}}),
      json.dumps({"ref": "refs/heads/feat/x"}),
      json.dumps({"ref": "refs/heads/feat/x"}),
    ],
    calls,
  )

  async def answer(payload):
    return payload["type"] == "confirmation"

  created = asyncio.run(
    client().write_github_file(
      "o/r", "a.py", "add a", action="create", content="1\n", __event_call__=answer
    )
  )
  assert created["result"]["commit_sha"] == "c1"
  updated = asyncio.run(
    client().write_github_file(
      "o/r",
      "a.py",
      "edit a",
      action="update",
      content="2\n",
      sha="b1",
      __event_call__=answer,
    )
  )
  assert updated["result"]["commit_sha"] == "c2"
  deleted = asyncio.run(
    client().write_github_file(
      "o/r", "a.py", "rm a", action="delete", sha="b2", __event_call__=answer
    )
  )
  assert deleted["result"]["commit_sha"] == "c3"
  branch = asyncio.run(
    client().write_branch(
      "o/r", "feat/x", action="create", sha="c3", __event_call__=answer
    )
  )
  assert branch["result"]["branch"] == "feat/x"
  moved = asyncio.run(
    client().write_branch(
      "o/r", "feat/x", action="move", sha="c2", force=True, __event_call__=answer
    )
  )
  assert moved["result"]["success"] is True


def test_list_commits_filters_by_path_and_commit_status(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps([{"sha": "abc", "commit": {"message": "x"}}]),
      json.dumps(
        {"state": "success", "statuses": [{"context": "ci"}], "total_count": 1}
      ),
    ],
    calls,
  )
  out = asyncio.run(
    client().list_commits("o/r", path="src/app.py", since="2026-01-01T00:00:00Z")
  )
  assert out["result"]["commits"][0]["sha"] == "abc"
  assert "path=src%2Fapp.py" in calls[0]["url"]
  assert "since=2026-01-01T00%3A00%3A00Z" in calls[0]["url"]
  status = asyncio.run(client().fetch_commit("o/r", "abc", status_only=True))
  assert status["result"]["state"] == "success"


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


def test_security_alerts_read_all_three_scanners(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps({"alerts": []}),
      json.dumps({"number": 7}),
      json.dumps([]),
      json.dumps({"number": 3}),
      json.dumps([{"path": "a.env"}]),
      json.dumps([{"number": 1}]),
      json.dumps({"number": 9}),
    ],
    calls,
  )
  out = asyncio.run(
    client().list_security_alerts(
      "o/r", alert_type="code_scanning", tool_name="CodeQL", severity="error"
    )
  )
  assert out["result"]["alerts"] == {"alerts": []}
  assert "tool_name=CodeQL" in calls[0]["url"]
  assert "severity=error" in calls[0]["url"]
  detail = asyncio.run(
    client().list_security_alerts("o/r", alert_type="code_scanning", alert_number=7)
  )
  assert detail["result"]["alert"]["number"] == 7
  assert calls[2]["url"].endswith("/code-scanning/alerts/7/instances")

  secret = asyncio.run(
    client().list_security_alerts("o/r", alert_type="secret_scanning", alert_number=3)
  )
  assert secret["result"]["locations"][0]["path"] == "a.env"
  assert calls[4]["url"].endswith("/secret-scanning/alerts/3/locations")

  dep_list = asyncio.run(
    client().list_security_alerts(
      "o/r", alert_type="dependabot", severity="high", ecosystem="pip"
    )
  )
  assert dep_list["result"]["alerts"] == [{"number": 1}]
  assert "ecosystem=pip" in calls[5]["url"]
  dep_one = asyncio.run(
    client().list_security_alerts("o/r", alert_type="dependabot", alert_number=9)
  )
  assert dep_one["result"]["alert"]["number"] == 9


def test_check_runs_and_workflow_inspection(monkeypatch):
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
      json.dumps({"total_count": 2, "workflows": [{"id": 1}, {"id": 2}]}),
      json.dumps({"jobs": [{"id": 8}]}),
      "step log line",
      json.dumps({"id": 12, "name": "bundle", "archive_download_url": "https://u"}),
    ],
    calls,
  )
  out = asyncio.run(
    client().list_github_check_runs("o/r", "1595387", check_name="ci", filter="latest")
  )
  assert out["result"]["check_runs"][0]["conclusion"] == "failure"
  detail = asyncio.run(
    client().list_github_check_runs("o/r", "1595387", check_run_id=4)
  )
  assert detail["result"]["annotations"][0]["message"] == "boom"

  workflows = asyncio.run(client().inspect_workflow_run("o/r", target="workflows"))
  assert workflows["result"]["total_count"] == 2
  jobs = asyncio.run(client().inspect_workflow_run("o/r", target="jobs", run_id=55))
  assert jobs["result"]["jobs"][0]["id"] == 8
  logs = asyncio.run(client().inspect_workflow_run("o/r", target="job_logs", job_id=8))
  assert logs["result"]["content"] == "step log line"
  artifact = asyncio.run(
    client().inspect_workflow_run("o/r", target="artifact_download", artifact_id=12)
  )
  assert artifact["result"]["file_name"] == "bundle.zip"


def test_account_inspection_and_actions_minutes(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps({"id": 5, "login": "nemo", "name": "Nemo"}),
      json.dumps([{"login": "acme"}]),
      json.dumps({"total_minutes_used": 10}),
      json.dumps({"total_minutes_used": 2}),
    ],
    calls,
  )
  profile = asyncio.run(client().inspect_github_account(target="profile"))
  assert profile["result"]["nickname"] == "nemo"
  orgs = asyncio.run(client().inspect_github_account(target="orgs"))
  assert orgs["result"]["orgs"] == ["acme"]
  out = asyncio.run(
    client().inspect_github_account(target="actions_minutes", owner="acme")
  )
  assert out["result"]["minutes"]["total_minutes_used"] == 10
  assert calls[2]["url"] == "https://api.github.com/orgs/acme/settings/billing/actions"
  out = asyncio.run(
    client().inspect_github_account(
      target="actions_minutes", owner="nemo", owner_type="user"
    )
  )
  assert out["result"]["minutes"]["total_minutes_used"] == 2
  assert calls[3]["url"] == "https://api.github.com/users/nemo/settings/billing/actions"


def test_releases_refs_collaborators_and_packages(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps([{"id": 1}]),
      json.dumps({"id": 2}),
      json.dumps({"id": 3}),
      json.dumps([{"name": "v1"}]),
      json.dumps([{"name": "feat/ui", "commit": {"sha": "s1"}}]),
      json.dumps({"permission": "write", "user": {"login": "nemo"}}),
      json.dumps([{"name": "left-pad"}]),
      json.dumps({"name": "left-pad", "package_type": "npm"}),
      json.dumps([{"name": "1.0.0"}]),
    ],
    calls,
  )
  out = asyncio.run(client().list_github_releases("o/r"))
  assert out["result"]["releases"][0]["id"] == 1
  out = asyncio.run(client().list_github_releases("o/r", tag="v1.2.3"))
  assert out["result"]["release"]["id"] == 2
  out = asyncio.run(client().list_github_releases("o/r", release_id=3))
  assert out["result"]["release"]["id"] == 3
  tags = asyncio.run(client().list_repo_refs("o/r", ref_type="tags"))
  assert tags["result"]["tags"][0]["name"] == "v1"
  branches = asyncio.run(
    client().list_repo_refs("o/r", ref_type="branches", query="ui")
  )
  assert branches["result"]["branches"][0]["branch"] == "feat/ui"
  collab = asyncio.run(
    client().get_repo(repository_full_name="o/r", collaborator="nemo")
  )
  assert collab["result"]["permission"] == "write"
  pkgs = asyncio.run(client().list_github_packages("npm"))
  assert pkgs["result"]["packages"][0]["name"] == "left-pad"
  pkg = asyncio.run(
    client().list_github_packages("npm", owner="acme", package_name="left-pad")
  )
  assert pkg["result"]["package"]["name"] == "left-pad"
  assert pkg["result"]["versions"][0]["name"] == "1.0.0"


def test_gists_read_and_write_gates(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      json.dumps([{"id": "abc"}]),
      json.dumps([{"id": "abc"}]),
      json.dumps({"id": "abc"}),
      json.dumps({"id": "new"}),
      json.dumps({"id": "new"}),
      "",
    ],
    calls,
  )
  out = asyncio.run(client().read_gist())
  assert out["result"]["gists"][0]["id"] == "abc"
  assert calls[0]["url"].startswith("https://api.github.com/gists?")
  out = asyncio.run(client().read_gist(username="nemo"))
  assert calls[1]["url"].startswith("https://api.github.com/users/nemo/gists?")
  out = asyncio.run(client().read_gist(gist_id="abc"))
  assert out["result"]["gist"]["id"] == "abc"
  assert calls[2]["url"].endswith("/gists/abc")

  denied = asyncio.run(
    client().write_gist(
      action="create", files={"a.txt": {"content": "x"}}, __event_call__=None
    )
  )
  assert denied["result"]["denied"] is True
  assert len(calls) == 3

  async def answer(payload):
    return payload["type"] == "confirmation"

  created = asyncio.run(
    client().write_gist(
      action="create",
      files={"a.txt": {"content": "x"}},
      description="d",
      __event_call__=answer,
    )
  )
  assert created["result"]["gist"]["id"] == "new"
  edited = asyncio.run(
    client().write_gist(
      action="update",
      gist_id="new",
      files={"a.txt": {"content": "y"}},
      description="d",
      __event_call__=answer,
    )
  )
  assert edited["result"]["gist"]["id"] == "new"
  deleted = asyncio.run(
    client().write_gist(action="delete", gist_id="new", __event_call__=answer)
  )
  assert deleted["result"]["deleted"] is True
  assert calls[-1]["method"] == "DELETE"


def test_alert_writes_patch_and_deny(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [json.dumps({"number": 7}), json.dumps({"number": 8}), json.dumps({"number": 9})],
    calls,
  )
  denied = asyncio.run(
    client().update_security_alert(
      "o/r", 9, alert_type="dependabot", __event_call__=None
    )
  )
  assert denied["result"]["denied"] is True
  assert calls == []

  async def answer(payload):
    return payload["type"] == "confirmation"

  out = asyncio.run(
    client().update_security_alert(
      "o/r",
      7,
      alert_type="code_scanning",
      reason="won't fix",
      comment="c",
      __event_call__=answer,
    )
  )
  assert out["result"]["alert"]["number"] == 7
  assert json.loads(calls[0]["data"]) == {
    "state": "dismissed",
    "dismissed_reason": "won't fix",
    "dismissed_comment": "c",
  }
  out = asyncio.run(
    client().update_security_alert(
      "o/r",
      8,
      alert_type="secret_scanning",
      resolution="revoked",
      comment="c",
      __event_call__=answer,
    )
  )
  assert out["result"]["alert"]["number"] == 8
  assert json.loads(calls[1]["data"]) == {
    "state": "resolved",
    "resolution": "revoked",
    "resolution_comment": "c",
  }
  out = asyncio.run(
    client().update_security_alert(
      "o/r",
      9,
      alert_type="dependabot",
      reason="tolerable_risk",
      __event_call__=answer,
    )
  )
  assert out["result"]["alert"]["number"] == 9
  assert json.loads(calls[2]["data"]) == {
    "state": "dismissed",
    "dismissed_reason": "tolerable_risk",
  }


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
  """Verify GitHub URL reader keeps `fetch_github_url` and names `fetch_url` on 422."""
  assert hasattr(tool.Tools, "fetch_github_url")
  assert not hasattr(tool.Tools, "fetch")
  with pytest.raises(tool.GitHubError) as info:
    asyncio.run(client().fetch_github_url("https://example.com/page"))
  assert info.value.status == 422
  assert "fetch_url" in str(info.value)


@pytest.mark.parametrize("repos", [["fixture/one", "fixture/two"], "fixture/one"])
def test_code_search_qualifies_each_repo(monkeypatch, repos):
  calls = []
  query, org, page_limit = "needle", "fixture", 100
  opener(monkeypatch, json.dumps({"items": []}), calls)
  for topn in (page_limit, page_limit * 2):
    asyncio.run(
      client().search_github(
        query, scope="code", topn=topn, repository_full_name=repos, org=org
      )
    )
  params = [parse_qs(urlsplit(call["url"]).query) for call in calls]
  assert int(params[0]["per_page"][0]) == page_limit
  assert params[1]["per_page"] == params[0]["per_page"]
  names = repos if isinstance(repos, list) else [repos]
  expected = [query, *[f"repo:{name}" for name in names], f"org:{org}"]
  assert all(row["q"][0].split() == expected for row in params)


@pytest.mark.parametrize("encoding", ["utf-8", "base64"])
def test_file_range_applies_to_each_encoding(monkeypatch, encoding):
  lines = ["first", "second", "third"]
  start, end = 2, 2
  blob = base64.b64encode("\n".join(lines).encode()).decode()
  opener(
    monkeypatch,
    json.dumps({"content": blob, "encoding": "base64", "sha": "fixture-sha"}),
  )
  answer = asyncio.run(
    client().fetch_github_file(
      "fixture/repo",
      "file.txt",
      encoding=encoding,
      start_line=start,
      end_line=end,
    )
  )
  content = answer["result"]["content"]
  if encoding == "base64":
    content = base64.b64decode(content).decode()
  assert content == "\n".join(lines[start - 1 : end])


@pytest.mark.parametrize("repo_ids", [7, [7, 9]])
def test_commit_search_resolves_repo_ids(monkeypatch, repo_ids):
  query = "needle"
  ids = repo_ids if isinstance(repo_ids, list) else [repo_ids]
  names = [f"fixture/repo-{value}" for value in ids]
  calls = []
  opener(
    monkeypatch,
    [json.dumps({"full_name": name}) for name in names] + [json.dumps({"items": []})],
    calls,
  )
  asyncio.run(client().search_github(query, scope="commits", repository_id=repo_ids))
  assert [urlsplit(call["url"]).path for call in calls] == [
    *[f"/repositories/{value}" for value in ids],
    "/search/commits",
  ]
  sent = parse_qs(urlsplit(calls[-1]["url"]).query)["q"][0]
  assert sent.split() == [query, *[f"repo:{name}" for name in names]]


def test_file_base64_without_range_preserves_original_blob(monkeypatch):
  blob = base64.b64encode(b"first\n\xff\x00").decode() + "\n"
  opener(monkeypatch, json.dumps({"content": blob, "encoding": "base64"}))
  answer = asyncio.run(
    client().fetch_github_file("fixture/repo", "file.bin", encoding="base64")
  )
  assert answer["result"]["content"] == blob


def test_commit_search_permission_precedes_id_lookup(monkeypatch):
  calls = []
  opener(monkeypatch, json.dumps({"items": []}), calls)
  instance = client()
  instance.valves.permissions = "Always ask"
  answer = asyncio.run(
    instance.search_github("needle", scope="commits", repository_id=7)
  )
  assert answer["result"]["denied"] is True
  assert calls == []


def test_commit_search_id_lookup_error_prevents_unrestricted_search(monkeypatch):
  calls = []
  error = HTTPError(
    "https://api.github.com/repositories/7",
    404,
    "missing",
    {},
    io.BytesIO(b"{}"),
  )
  opener(monkeypatch, {}, calls, error)
  with pytest.raises(tool.GitHubError):
    asyncio.run(client().search_github("needle", scope="commits", repository_id=7))
  assert [urlsplit(call["url"]).path for call in calls] == ["/repositories/7"]
