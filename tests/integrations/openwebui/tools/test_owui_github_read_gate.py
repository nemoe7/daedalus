"""Check the read gate before each GitHub transport call."""

import asyncio

import pytest
import test_owui_github_tool as github

CASES = [
  ("fetch_issue", {"issue_number": 1, "repository_full_name": "a/b"}),
  ("fetch_issue_comments", {"repo_full_name": "a/b", "issue_number": 1}),
  ("fetch_pr", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_pr_comments", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_pr_patch", {"repo_full_name": "a/b", "pr_number": 1}),
  ("fetch_pr_file_patch", {"repo_full_name": "a/b", "pr_number": 1, "path": "file"}),
  ("fetch_github_file", {"repository_full_name": "a/b", "path": "file"}),
  ("fetch_blob", {"repository_full_name": "a/b", "blob_sha": "abc"}),
  ("fetch_github_url", {"url": "https://api.github.com/repos/a/b"}),
  ("code_scanning_alerts", {"repo_full_name": "a/b"}),
  ("secret_scanning_alerts", {"repo_full_name": "a/b"}),
  ("dependabot_alerts", {"repo_full_name": "a/b"}),
  ("compare_commits", {"repo_full_name": "a/b", "base": "main", "head": "change"}),
  ("search_github_code", {"query": "term"}),
  ("fetch_commit", {"repository_full_name": "a/b", "commit_sha": "abc"}),
  ("list_github_check_runs", {"repo_full_name": "a/b", "ref": "main"}),
  ("fetch_commit_workflow_runs", {"repo_full_name": "a/b", "commit_sha": "abc"}),
  ("fetch_workflow_run_jobs", {"repo_full_name": "a/b", "run_id": 1}),
  ("fetch_workflow_job_steps", {"repo_full_name": "a/b", "job_id": 1}),
  ("fetch_workflow_job_logs", {"repo_full_name": "a/b", "job_id": 1}),
  ("fetch_workflow_run_artifacts", {"repo_full_name": "a/b", "run_id": 1}),
  ("actions_minutes", {"owner": "a"}),
  ("list_github_releases", {"repo_full_name": "a/b"}),
  ("list_github_tags", {"repo_full_name": "a/b"}),
  ("list_github_packages", {}),
  ("list_gists", {}),
  ("fetch_gist", {"gist_id": "abc"}),
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
  result = asyncio.run(tool.fetch_gist("gist", __user__=user))
  assert result == {
    "result": {"gist": {"id": "gist", "files": {"note.md": {"content": "text"}}}}
  }
  assert len(calls) == 1
