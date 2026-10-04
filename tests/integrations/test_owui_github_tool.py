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

TOOL = Path(__file__).resolve().parents[2] / "integrations" / "openwebui" / "github.py"
SPEC = importlib.util.spec_from_file_location("owui_github_tool", TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

TOOLS = [
  "add_comment_to_issue",
  "add_issue_assignees",
  "add_issue_labels",
  "add_reaction_to_issue_comment",
  "add_reaction_to_pr",
  "add_reaction_to_pr_review_comment",
  "add_review_to_pr",
  "compare_commits",
  "convert_pull_request_to_draft",
  "create_blob",
  "create_branch",
  "create_commit",
  "create_file",
  "create_issue",
  "create_pull_request",
  "create_pr_with_files",
  "code_scanning_alerts",
  "create_tree",
  "delete_file",
  "dependabot_alerts",
  "dismiss_pull_request_review",
  "download_user_content",
  "download_workflow_artifact",
  "enable_auto_merge",
  "fetch",
  "fetch_blob",
  "fetch_commit",
  "fetch_commit_workflow_runs",
  "fetch_file",
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
  "get_issue_comment_reactions",
  "get_pr_diff",
  "get_pr_info",
  "get_pr_reactions",
  "get_pr_review_comment_reactions",
  "get_profile",
  "get_repo",
  "get_repo_collaborator_permission",
  "get_user_login",
  "get_users_recent_prs_in_repo",
  "label_pr",
  "list_installations",
  "list_installed_accounts",
  "list_commits",
  "list_pr_changed_filenames",
  "list_pull_request_review_threads",
  "list_pull_request_reviews",
  "list_recent_issues",
  "list_repositories",
  "list_repositories_by_affiliation",
  "list_repositories_by_installation",
  "list_user_org_memberships",
  "list_tree",
  "list_user_orgs",
  "lock_issue_conversation",
  "mark_pull_request_ready_for_review",
  "merge_pull_request",
  "remove_issue_assignees",
  "remove_issue_label",
  "remove_pull_request_reviewers",
  "remove_reaction_from_issue_comment",
  "remove_reaction_from_pr",
  "remove_reaction_from_pr_review_comment",
  "reply_to_review_comment",
  "request_pull_request_reviewers",
  "rerun_failed_workflow_run_jobs",
  "rerun_workflow_job",
  "resolve_review_thread",
  "search",
  "search_branches",
  "search_commits",
  "search_installed_repositories_streaming",
  "search_installed_repositories_v2",
  "search_issues",
  "search_prs",
  "search_repositories",
  "secret_scanning_alerts",
  "unlock_issue_conversation",
  "unresolve_review_thread",
  "update_file",
  "update_issue",
  "update_issue_comment",
  "update_pull_request",
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
  assert len(TOOLS) == 95


def test_the_gate_defaults_to_ask_with_sixty_seconds():
  instance = tool.Tools()
  assert instance.valves.default_mode == "ask"
  assert instance.valves.timeout_seconds == 60
  assert tool.Tools.UserValves().mode == "default"
  assert tool.Tools.UserValves().timeout_seconds == 0


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
  assert "nothing was sent" in out["result"]["reason"]
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
