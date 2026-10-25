"""Check read permission gates before each GitHub transport call."""

import asyncio

import pytest
import test_owui_github_tool as github

CASES = [
  ("fetch_issue", {"issue_number": 1, "repository_full_name": "a/b"}),
  ("list_recent_issues", {}),
  ("fetch_pr", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_pr_comments", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_pr_diff", {"repo_full_name": "a/b", "pr_number": 1}),
  ("list_pull_request_reviews", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_github_file", {"repository_full_name": "a/b", "path": "file"}),
  ("fetch_github_file", {"repository_full_name": "a/b", "blob_sha": "abc"}),
  ("fetch_github_url", {"url": "https://api.github.com/repos/a/b"}),
  ("list_tree", {"repository_full_name": "a/b", "ref": "main"}),
  ("list_commits", {"repository_full_name": "a/b"}),
  ("fetch_commit", {"repository_full_name": "a/b", "commit_sha": "abc"}),
  ("compare_commits", {"repo_full_name": "a/b", "base": "main", "head": "change"}),
  ("get_repo", {"repository_full_name": "a/b"}),
  ("list_repositories", {}),
  ("list_repo_refs", {"repo_full_name": "a/b"}),
  ("list_github_releases", {"repo_full_name": "a/b"}),
  ("list_github_packages", {}),
  ("inspect_github_account", {}),
  ("search_github", {"query": "term", "scope": "code"}),
  ("list_security_alerts", {"repo_full_name": "a/b"}),
  ("list_github_check_runs", {"repo_full_name": "a/b", "ref": "main"}),
  ("inspect_workflow_run", {"repo_full_name": "a/b"}),
  ("read_gist", {}),
  ("read_gist", {"gist_id": "abc"}),
]


@pytest.mark.parametrize("name,arguments", CASES)
def test_each_read_denies_without_network_then_accepts_permission(name, arguments):
  tool = github.client()
  calls = []

  async def request(*args, **kwargs):
    calls.append((args, kwargs))
    raise RuntimeError("transport reached")

  tool._request = request
  tool.valves.permissions = "Always ask"
  method = getattr(tool, name)
  denied = asyncio.run(method(**arguments))
  assert denied["result"]["denied"] is True and calls == []

  async def refuse(event):
    assert event["type"] == "confirmation"
    return False

  denied = asyncio.run(method(**arguments, __event_call__=refuse))
  assert denied["result"]["denied"] is True and calls == []

  for mode in ("Allow reads", "Always allow"):
    tool.valves.permissions = mode
    with pytest.raises(RuntimeError, match="transport reached"):
      asyncio.run(method(**arguments))
    assert len(calls) == 1
    calls.clear()
    denied = asyncio.run(
      method(**arguments, __user__={"valves": {"mode": "Always ask"}})
    )
    assert denied["result"]["denied"] is True and calls == []

  tool.valves.permissions = "Always ask"

  async def approve(event):
    assert event["type"] == "confirmation"
    return True

  with pytest.raises(RuntimeError, match="transport reached"):
    asyncio.run(method(**arguments, __event_call__=approve))
  assert len(calls) == 1
  calls.clear()
  with pytest.raises(RuntimeError, match="transport reached"):
    asyncio.run(method(**arguments, __user__={"valves": {"mode": "Allow reads"}}))
  assert len(calls) == 1


def test_read_gate_accepts_model_user_valves_and_keeps_the_result():
  tool = github.client()
  tool.valves.permissions = "Always ask"
  calls = []

  async def request(*args, **kwargs):
    calls.append(args)
    return {"id": "gist", "files": {"note.md": {"content": "text"}}}

  tool._request = request
  user = {"valves": tool.UserValves(mode="Allow reads")}
  result = asyncio.run(tool.read_gist("gist", __user__=user))
  assert result == {
    "result": {"gist": {"id": "gist", "files": {"note.md": {"content": "text"}}}}
  }
  assert len(calls) == 1
