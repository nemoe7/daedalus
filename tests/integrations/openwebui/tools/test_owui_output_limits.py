"""Check bounded tool results and explicit continuation controls."""

import asyncio
import base64
import inspect
import json

import pytest
import test_owui_github_tool as github
import test_owui_google_tool as google
import test_owui_manager as manager


def size(value):
  text = (
    value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
  )
  return len(text.encode("utf-8"))


def test_commit_lists_are_short_and_keep_page_controls():
  tool = github.client()
  calls = []

  async def request(*args, **kwargs):
    calls.append(kwargs["params"])
    return [
      {
        "sha": "a" * 40,
        "html_url": "https://github.com/a/b/commit/a",
        "commit": {
          "message": "subject\n" + "x" * 100000,
          "author": {"name": "Author", "date": "2026-01-01T00:00:00Z"},
          "verification": {"payload": "y" * 100000},
        },
      }
    ]

  tool._request = request
  result = asyncio.run(tool.list_commits("a/b", page=2, per_page=7))["result"]
  commit = result["commits"][0]
  assert commit["sha"] == "a" * 40
  assert commit["message"] == "subject"
  assert "verification" not in commit
  assert result["page"] == 2 and result["per_page"] == 7
  assert calls == [
    {"path": None, "sha": None, "since": None, "until": None, "per_page": 7, "page": 2}
  ]
  assert size(result) < 1024


def test_github_guard_preserves_identifiers_and_never_repeats_writes():
  tool = github.client()
  tool.valves.permissions = "Always allow"
  calls = []

  async def request(*args, **kwargs):
    calls.append(args)
    return {"id": 7, "sha": "a" * 40, "body": "🦉\n\x01" * 40000}

  tool._request = request
  result = asyncio.run(tool.create_issue("a/b", "title", "body"))
  assert result["truncated"] is True and result["identifiers"]["id"] == 7
  assert size(result) <= tool.valves.max_output_bytes == 32768
  assert len(calls) == 1
  tool.valves.permissions = "Always ask"
  result = asyncio.run(tool.create_issue("a/b", "title", "body"))
  assert result["result"]["denied"] is True and len(calls) == 1


def test_github_error_outputs_are_bounded_without_hiding_small_errors():
  tool = github.client()

  async def request(*args, **kwargs):
    raise github.tool.GitHubError(
      502, "x" * 100000, "GET", "https://api.github.com/test"
    )

  tool._request = request
  result = asyncio.run(tool.fetch_commit("a/b", "abc"))
  assert result["truncated"] is True and size(result) <= 32768

  async def small(*args, **kwargs):
    raise github.tool.GitHubError(502, "failed", "GET", "https://api.github.com/test")

  tool._request = small
  with pytest.raises(github.tool.GitHubError, match="failed"):
    asyncio.run(tool.fetch_commit("a/b", "abc"))


def test_github_blob_ranges_fit_unicode_and_metadata():
  tool = github.client()
  tool.valves.max_output_bytes = 1024
  text = "🦉\n\x01" * 1000

  async def request(*args, **kwargs):
    return {
      "content": base64.b64encode(text.encode()).decode(),
      "encoding": "base64",
      "sha": "abc",
      "size": len(text.encode()),
    }

  tool._request = request
  out = asyncio.run(
    tool.fetch_github_file("a/b", blob_sha="abc", offset=3, max_chars=10000)
  )
  result = out["result"]
  assert result["sha"] == "abc" and result["offset"] == 3
  assert result["content"] == text[3 : result["next_offset"]]
  assert result["next_offset"] > 3 and result["truncated"] is True
  assert size(out) <= 1024


def test_google_thread_pages_and_message_ranges():
  tool = google.reader()
  text = "🦉" * 10000
  messages = [
    {
      "id": str(i),
      "threadId": "t",
      "payload": {
        "mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
      },
    }
    for i in range(10)
  ]

  async def request(method, url, **kwargs):
    return {"messages": messages} if "/threads/" in url else messages[3]

  tool._request = request
  out = asyncio.run(tool.read_thread("t", offset=3, max_results=2))["result"]
  assert [message["id"] for message in out["messages"]] == ["3", "4"]
  assert all("text" not in message for message in out["messages"])
  assert out["next_offset"] == 5 and out["total_count"] == 10
  tool.valves.max_output_bytes = 1024
  out = asyncio.run(tool.read_message("3", offset=2, max_chars=10000))
  result = out["result"]
  assert result["id"] == "3" and result["text"] == text[2 : result["next_offset"]]
  assert size(out) <= 1024


def test_manager_file_ranges_and_oversized_results():
  text = "🦉\n" * 10000
  tool, calls = manager.make_tool({"GET /files/f/data/content": {"content": text}})
  tool.valves.permissions = "Allow reads"
  tool.valves.max_output_bytes = 1024
  out = asyncio.run(
    tool.read_file_content("f", offset=3, max_chars=10000, __request__=object())
  )
  result = json.loads(out)
  assert (
    result["file_id"] == "f" and result["content"] == text[3 : result["next_offset"]]
  )
  assert result["next_offset"] > 3 and size(out) <= 1024 and len(calls) == 1


def test_public_signatures_and_valve_bounds():
  for module in (github.tool, google.tool, manager.MOD):
    with pytest.raises(ValueError):
      module.Tools.Valves(max_output_bytes=0)
    assert module.Tools.Valves().max_output_bytes == 32768
    for name, method in vars(module.Tools).items():
      if name.startswith("_") or not inspect.iscoroutinefunction(method):
        continue
      signature = inspect.signature(method)
      assert "self" in signature.parameters
      assert not any(
        p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        for p in signature.parameters.values()
      )
      assert method.__name__ == name and method.__doc__


def test_all_guards_count_serialized_bytes_and_keep_complete_identifiers():
  for module in (github.tool, google.tool, manager.MOD):
    tool = module.Tools()
    tool.valves.max_output_bytes = 1024
    assert tool._output("x" * 1024) == "x" * 1024
    huge_id = "🦉" * 1024
    original = {"result": {"id": huge_id, "sha": "abc", "body": "\x01" * 2000}}
    out = tool._output(original)
    assert size(out) <= 1024 and out["truncated"] is True
    assert out["identifiers"]["sha"] == "abc"
    assert "id" not in out["identifiers"]
    assert original["result"]["id"] == huge_id
    assert json.loads(tool._output("x" * 1025, as_text=True))["truncated"] is True


def test_text_pages_round_trip_and_stop_at_the_end():
  tool = github.client()
  tool.valves.max_output_bytes = 1024
  text = "🦉\n\x01" * 200
  offset, parts = 0, []
  for _ in range(50):
    page = tool._page({"content": text, "sha": "abc"}, offset, 200000)
    assert size({"result": page}) <= 1024
    parts.append(page["content"])
    if page["next_offset"] is None:
      break
    assert page["next_offset"] > offset
    offset = page["next_offset"]
  else:
    pytest.fail("Text paging did not finish")
  assert "".join(parts) == text
  end = tool._page({"content": text}, len(text) + 100, 10)
  assert end["content"] == "" and end["next_offset"] is None and not end["truncated"]
  oversized = {"content": text, "id": "x" * 5000}
  assert size({"result": {**oversized, "content": ""}}) > tool.valves.max_output_bytes
  with pytest.raises(ValueError):
    tool._page(oversized, 0, 10)


def test_google_document_ranges_and_denied_message_reads():
  tool = google.reader()
  tool.valves.max_output_bytes = 1024
  text = "🦉" * 1000
  calls = []

  async def request(method, url, **kwargs):
    calls.append((url, kwargs))
    if kwargs.get("raw"):
      return text
    return {"id": "d", "name": "Doc", "mimeType": "text/plain"}

  tool._request = request
  out = asyncio.run(tool.read_document("d", offset=4, max_chars=10000))
  result = out["result"]
  assert result["text"] == text[4 : result["next_offset"]] and size(out) <= 1024
  tool.valves.permissions = "Always ask"
  denied = asyncio.run(tool.read_message("m"))
  assert denied["result"]["denied"] is True and len(calls) == 2


def test_google_and_manager_generic_results_obey_the_guard():
  tool = google.reader()

  async def request(*args, **kwargs):
    return {"files": [{"id": "f", "name": "🦉" * 100000}]}

  tool._request = request
  out = asyncio.run(tool.search_files("query"))
  assert out["truncated"] is True and size(out) <= 32768
  assert out["identifiers"]["id"] == "f"
  tool, _ = manager.make_tool(
    {"GET /tools/id/t": {"id": "t", "name": "Tool", "content": "x" * 100000}}
  )
  tool.valves.permissions = "Allow reads"
  out = asyncio.run(tool.show_tool("t", __request__=object()))
  parsed = json.loads(out)
  assert parsed["truncated"] is True and size(out) <= 32768
  assert parsed["tool_id"] == "t" and parsed["next_offset"] > 0
  tool, _ = manager.make_tool(
    {"GET /tools/": [{"id": str(i), "name": "x" * 1000} for i in range(100)]}
  )
  tool.valves.permissions = "Allow reads"
  out = asyncio.run(tool.list_tools(__request__=object()))
  assert json.loads(out)["next_offset"] > 0 and size(out) <= 32768


def test_all_guards_record_call_arguments_and_root_ids_before_large_child_lists():
  byte_limit = 1024
  expected_repo = "nemoe7/clankers"
  expected_tool = "github"
  expected_thread = "thread-1"
  expected_tree = "tree-root-sha"
  children = [
    {"sha": f"child-{i}", "url": f"https://example.invalid/{i}", "body": "x" * 600}
    for i in range(120)
  ]
  arguments = {
    "repository_full_name": expected_repo,
    "tool_id": expected_tool,
    "thread_id": expected_thread,
    "ref": "",
    "recursive": True,
  }
  payload = {"result": {"tree_sha": expected_tree, "items": children}}
  for module in (github.tool, google.tool, manager.MOD):
    instance = module.Tools()
    instance.valves.max_output_bytes = byte_limit
    out = instance._output(payload, arguments=arguments)
    assert size(out) <= byte_limit and out["truncated"] is True
    assert out["identifiers"]["repository_full_name"] == expected_repo
    assert out["identifiers"]["tool_id"] == expected_tool
    assert out["identifiers"]["thread_id"] == expected_thread
    assert out["identifiers"]["tree_sha"] == expected_tree
    assert "ref" not in out["identifiers"]
    assert "recursive" not in out["identifiers"]
    assert out["identifiers"].get("sha") in (None, "child-0")
