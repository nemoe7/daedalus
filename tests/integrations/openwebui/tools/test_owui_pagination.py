"""Check discovery summaries and explicit continuation controls."""

import asyncio
import json

import pytest
import test_owui_github_tool as github
import test_owui_google_tool as google
import test_owui_manager as manager


def test_repository_offsets_skip_exactly_and_keep_action_fields():
  tool = github.client()
  calls = []

  async def request(*args, **kwargs):
    calls.append(kwargs["params"])
    return [
      {
        "id": i,
        "node_id": f"repo-{i}",
        "full_name": f"a/repo-{i}",
        "html_url": f"https://github.com/a/repo-{i}",
        "description": "Repository",
        "private": False,
        "visibility": "public",
        "default_branch": "main",
        "permissions": {"pull": True, "push": False},
        "owner": {"avatar_url": "x" * 10000},
      }
      for i in (2, 3)
    ]

  tool._request = request
  out = asyncio.run(tool.list_repositories(page_size=2, page_offset=3))["result"]
  assert [row["id"] for row in out["repositories"]] == [3]
  assert out["repositories"][0]["permissions"] == {"pull": True, "push": False}
  assert "owner" not in out["repositories"][0]
  assert out["next_offset"] == 4 and calls[0]["page"] == 2


@pytest.mark.parametrize("scope", ["issues", "prs"])
def test_issue_search_pages_and_short_records(scope):
  tool = github.client()
  calls = []

  async def request(*args, **kwargs):
    calls.append(kwargs["params"])
    return {
      "total_count": 3,
      "items": [
        {
          "id": 7,
          "node_id": "issue-7",
          "number": 4,
          "title": "Issue",
          "html_url": "https://github.com/a/b/issues/4",
          "state": "open",
          "body": "x" * 100000,
          "user": {"login": "author", "avatar_url": "x" * 10000},
          "labels": [{"name": "bug"}],
        }
      ],
    }

  tool._request = request
  out = asyncio.run(tool.search_github("query", scope=scope, topn=1, page=2))["result"]
  assert calls[0]["page"] == 2 and out["next_page"] == 3
  row = out["issues"][0]
  assert row["id"] == 7 and row["node_id"] == "issue-7" and row["number"] == 4
  assert "body" not in row and "display_title" not in row and "display_url" not in row


@pytest.mark.parametrize("name", ["search_mail", "agenda", "search_files"])
def test_google_list_tokens_reach_the_api_and_return_to_the_agent(name):
  tool = google.reader()
  calls = []

  async def request(*args, **kwargs):
    calls.append(kwargs["params"])
    return {"messages": [], "items": [], "files": [], "nextPageToken": "next-token"}

  tool._request = request
  kwargs = {"page_token": "old-token"}
  if name == "agenda":
    kwargs.update(time_min="2026-01-01T00:00:00Z", time_max="2026-01-02T00:00:00Z")
    result = asyncio.run(tool.agenda(**kwargs))["result"]
    assert (
      calls[0]["timeMin"] == kwargs["time_min"]
      and calls[0]["timeMax"] == kwargs["time_max"]
    )
  else:
    result = asyncio.run(getattr(tool, name)("query", **kwargs))["result"]
  assert calls[0]["pageToken"] == "old-token"
  assert result["next_page_token"] == "next-token"
  if name == "search_files":
    assert "nextPageToken" in calls[0]["fields"]


def test_manager_list_pages_keep_ids_and_end_without_a_gap():
  tool, _ = manager.make_tool(
    {"GET /tools/": [{"id": str(i), "name": f"Tool {i}"} for i in range(3)]}
  )
  tool.valves.permissions = "Allow reads"
  first = json.loads(
    asyncio.run(tool.list_tools(offset=1, max_results=1, __request__=object()))
  )
  assert [row["id"] for row in first["items"]] == ["1"]
  assert first["next_offset"] == 2 and first["total_count"] == 3
  last = json.loads(
    asyncio.run(tool.list_tools(offset=2, max_results=1, __request__=object()))
  )
  assert [row["id"] for row in last["items"]] == ["2"] and last["next_offset"] is None


def test_skill_manifest_pages_keep_the_version():
  tool, _ = manager.make_tool(
    {
      "GET /skills/id/s/files": {
        "version_id": "v1",
        "files": [{"path": str(i), "size": 5} for i in range(3)],
      }
    }
  )
  tool.valves.permissions = "Allow reads"
  out = json.loads(
    asyncio.run(
      tool.list_skills(
        skill_id="s", version_id="v1", offset=1, max_results=1, __request__=object()
      )
    )
  )
  assert out["version_id"] == "v1" and out["next_offset"] == 2
  assert out["items"][0]["path"] == "1"


def test_thread_reads_request_headers_without_message_bodies():
  tool = google.reader()
  calls = []

  async def request(*args, **kwargs):
    calls.append(kwargs["params"])
    return {"messages": []}

  tool._request = request
  asyncio.run(tool.read_thread("thread"))
  assert calls == [
    {"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]}
  ]


@pytest.mark.parametrize("scope", ["repositories", "issues", "prs"])
@pytest.mark.parametrize(
  "total,page,size,expected",
  [(2, 2, 1, None), (3, 2, 1, 3), (1200, 10, 100, None), (0, 1, 20, None)],
)
def test_github_search_stops_at_the_total_or_search_limit(
  scope, total, page, size, expected
):
  tool = github.client()
  tool.valves.max_output_bytes = 131072

  async def request(*args, **kwargs):
    return {
      "total_count": total,
      "items": [{"id": i} for i in range(size if total else 0)],
    }

  tool._request = request
  out = asyncio.run(tool.search_github("query", scope=scope, topn=size, page=page))[
    "result"
  ]
  assert out["next_page"] == expected
  assert out["search_limited"] == (total > 1000)


@pytest.mark.parametrize(
  "name,path,page_size",
  [
    ("list_knowledge_bases", "/knowledge/", 30),
    ("list_skills", "/skills/list", 30),
    ("list_files", "/files/", 50),
  ],
)
def test_manager_native_pages_keep_every_row_and_access_field(name, path, page_size):
  tool, calls = manager.make_tool()
  tool.valves.permissions = "Allow reads"
  rows = [
    {
      "id": str(i),
      "name": f"Name {i}",
      "filename": f"file{i}",
      "write_access": False,
      "access_grants": [{"principal_id": "g", "permission": "read"}],
      "access_control": {"read": {"group_ids": ["g"]}},
      "content": "x" * 100000,
    }
    for i in range(page_size + 1)
  ]

  async def request(session, method, url, **kwargs):
    calls.append((url, kwargs["params"]))
    start = (kwargs["params"]["page"] - 1) * page_size
    return {"items": rows[start : start + page_size], "total": len(rows)}, None

  tool._request = request
  seen = []
  page, offset = 1, 0
  for _ in range(10):
    out = json.loads(
      asyncio.run(
        getattr(tool, name)(
          page=page, offset=offset, max_results=20, __request__=object()
        )
      )
    )
    seen.extend(row["id"] for row in out["items"])
    assert all("content" not in row for row in out["items"])
    if name != "list_files":
      assert all(
        row["write_access"] is False
        and row["access_grants"] == rows[0]["access_grants"]
        and row["access_control"] == rows[0]["access_control"]
        for row in out["items"]
      )
    if out["next_page"] is None:
      assert out["next_offset"] is None
      break
    page, offset = out["next_page"], out["next_offset"]
  assert seen == [row["id"] for row in rows]
  assert len(calls) == (page_size + 19) // 20 + 1
  assert all(url == path for url, _ in calls)
  if name == "list_files":
    assert all(params["content"] == "false" for _, params in calls)


def test_manager_local_pages_fit_bytes_without_cutting_ids():
  rows = [{"id": f"tool-{i}", "name": "🦉" * 80} for i in range(5)]
  tool, _ = manager.make_tool({"GET /tools/": rows})
  tool.valves.permissions = "Allow reads"
  tool.valves.max_output_bytes = 1024
  offset, seen = 0, []
  for _ in range(6):
    raw = asyncio.run(
      tool.list_tools(offset=offset, max_results=100, __request__=object())
    )
    assert len(raw.encode("utf-8")) <= 1024
    out = json.loads(raw)
    assert out["count"] > 0
    seen.extend(out["items"])
    offset = out["next_offset"]
    if offset is None:
      break
  assert seen == rows
  tool, _ = manager.make_tool({"GET /tools/": [{"id": "large", "name": "x" * 5000}]})
  tool.valves.permissions = "Allow reads"
  tool.valves.max_output_bytes = 1024
  raw = asyncio.run(tool.list_tools(__request__=object()))
  out = json.loads(raw)
  assert out["error"] == "tool_output_too_large" and out["identifiers"]["id"] == "large"
  assert len(raw.encode("utf-8")) <= 1024 and "next_offset" not in out


def test_knowledge_directory_pages_preserve_navigation_and_fetch_one_page():
  data = {
    "directories": [{"id": "d", "name": "Folder", "parent_id": "parent"}],
    "breadcrumbs": [{"id": "parent", "name": "Parent"}],
    "items": [{"file": {"id": "f", "filename": "file.md"}}],
    "total": 1,
  }
  tool, calls = manager.make_tool({"GET /knowledge/k/files": data})
  tool.valves.permissions = "Allow reads"
  first = json.loads(
    asyncio.run(
      tool.list_knowledge_files(
        "k", directory_id="parent", max_results=1, __request__=object()
      )
    )
  )
  assert len(calls) == 1 and calls[0][2]["params"] == {
    "directory_id": "parent",
    "page": 1,
  }
  assert first["items"] == [
    {"kind": "directory", "directory_id": "d", "name": "Folder", "parent_id": "parent"}
  ]
  assert first["next_page"] == 1 and first["next_offset"] == 1
  last = json.loads(
    asyncio.run(
      tool.list_knowledge_files(
        "k", directory_id="parent", offset=1, max_results=1, __request__=object()
      )
    )
  )
  assert last["items"] == [{"kind": "file", "file_id": "f", "filename": "file.md"}]
  assert last["breadcrumbs"] == data["breadcrumbs"] and last["next_page"] is None


def test_file_search_sends_skip_limit_and_ends_on_no_matches():
  tool, calls = manager.make_tool(
    {"GET /files/search": [{"id": "f", "filename": "one.md"}]}
  )
  tool.valves.permissions = "Allow reads"
  out = json.loads(
    asyncio.run(
      tool.list_files(filename="*.md", offset=5, max_results=1, __request__=object())
    )
  )
  assert out["next_offset"] == 6 and out["items"][0]["id"] == "f"
  assert calls[0][2]["params"] == {
    "filename": "*.md",
    "content": "false",
    "skip": 5,
    "limit": 1,
  }
  assert calls[0][2]["expected"] == (200, 404)
  tool, _ = manager.make_tool({"GET /files/search": {"detail": "not found"}})
  tool.valves.permissions = "Allow reads"
  out = json.loads(
    asyncio.run(
      tool.list_files(filename="*.md", offset=6, max_results=1, __request__=object())
    )
  )
  assert out["items"] == [] and out["next_offset"] is None


def test_function_pages_keep_active_and_global_state_without_source():
  row = {
    "id": "f",
    "name": "Filter",
    "type": "filter",
    "is_active": True,
    "is_global": False,
  }
  tool, _ = manager.make_tool({"GET /functions/": [{**row, "content": "x" * 100000}]})
  tool.valves.permissions = "Allow reads"
  out = json.loads(asyncio.run(tool.list_functions(__request__=object())))
  assert out["items"] == [row] and out["next_offset"] is None


def test_private_directory_lookup_still_checks_all_pages_for_duplicates():
  tool, _ = manager.make_tool()
  pages = []

  async def request(session, method, path, **kwargs):
    page = kwargs["params"]["page"]
    pages.append(page)
    return {"items": [{"id": str(page), "filename": "same.md"}], "total": 2}, None

  tool._request = request
  item, error = asyncio.run(tool._find_file_in_directory(None, "k", "", "same.md"))
  assert item is None and error.startswith("Multiple matching files")
  assert pages == [1, 2]


@pytest.mark.parametrize("name", ["search_mail", "agenda", "search_files"])
def test_google_lists_end_without_a_continuation_token(name):
  tool = google.reader()

  async def request(*args, **kwargs):
    return {}

  tool._request = request
  out = asyncio.run(
    getattr(tool, name)(**({} if name == "agenda" else {"query": "query"}))
  )["result"]
  assert out["next_page_token"] is None
