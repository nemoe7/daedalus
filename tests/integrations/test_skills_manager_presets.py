"""The Open WebUI skills manager tool merges the preset lists and writes the whole record back."""

import asyncio
import importlib.util
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

TOOL = (
  Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "tools"
  / "skills_manager.py"
)
SPEC = importlib.util.spec_from_file_location("owui_skills_manager", TOOL)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

PRESET_TOOLS = ["list_model_presets", "update_all_model_presets", "update_model_preset"]

PRESETS: dict[str, dict[str, Any]] = {
  "own/preset": {
    "id": "own/preset",
    "name": "Mine",
    "base_model_id": "daedalus/auto",
    "meta": {
      "skillIds": ["skill-1"],
      "toolIds": ["tool-1"],
      "params": {"x": 1},
    },
    "params": {"system": "keep me"},
    "access_grants": [
      {"principal_type": "user", "principal_id": "u1", "permission": "read"}
    ],
    "is_active": True,
    "write_access": True,
  },
  "other/preset": {
    "id": "other/preset",
    "name": "Read only",
    "base_model_id": None,
    "meta": {"skillIds": ["skill-1"]},
    "params": {},
    "access_grants": [],
    "is_active": True,
    "write_access": False,
  },
  "third/preset": {
    "id": "third/preset",
    "name": "Empty",
    "base_model_id": None,
    "meta": {},
    "params": {},
    "access_grants": None,
    "is_active": False,
    "write_access": True,
  },
}

CALLS: list[tuple[str, str, Any, Any]] = []


def fake_api_call(
  request, method, base_url, path, params=None, payload=None, timeout=30.0
):
  """Answer the preset routes from the fixtures and record each call."""
  CALLS.append((method, path, params, payload))
  if (method, path) == ("GET", "/models/list"):
    page = (params or {}).get("page", 1)
    items = list(PRESETS.values()) if page == 1 else []
    return {"items": items, "total": len(PRESETS)}, None
  if (method, path) == ("GET", "/models/model"):
    return PRESETS.get((params or {}).get("id")), None
  if method == "GET" and path.startswith("/knowledge/"):
    knowledge_id = path.rsplit("/", 1)[-1]
    return {
      "id": knowledge_id,
      "name": f"KB {knowledge_id}",
      "description": f"about {knowledge_id}",
    }, None
  if (method, path) == ("POST", "/models/model/update"):
    return {"id": payload.get("id")}, None
  raise AssertionError(f"unexpected call {method} {path}")


module._owui_api_call = fake_api_call


def run(coro: Coroutine[Any, Any, Any]) -> Any:
  """Run one tool call."""
  return asyncio.run(coro)


def posts() -> list[Any]:
  """The update bodies sent so far."""
  return [call[3] for call in CALLS if call[0] == "POST"]


def test_the_preset_tools_are_on_the_surface() -> None:
  """The 6 skill tools and the 3 preset tools stay public, and the base URL valve ships."""
  for name in PRESET_TOOLS + [
    "create_skill",
    "delete_skill",
    "install_skill",
    "list_skills",
    "show_skill",
    "update_skill",
  ]:
    assert callable(getattr(module.Tools, name)), name
  assert module.Tools().valves.OWUI_API_BASE.endswith("/api/v1")


def test_the_github_host_check_parses_the_host() -> None:
  """A path that only carries the string github.com does not pass the host check."""
  assert module._is_github_host("https://github.com/o/r/blob/main/a.md") is True
  assert module._is_github_host("https://www.github.com/o/r") is True
  assert module._is_github_host("https://evil.com/github.com/o/r") is False
  assert module._is_github_host("https://github.com.evil.com/o/r") is False
  assert module._is_github_host("not a url") is False
  assert (
    module._normalize_url("https://github.com/o/r/blob/main/a.md")
    == "https://raw.githubusercontent.com/o/r/main/a.md"
  )


def test_the_parser_and_the_merge() -> None:
  """Ids split on commas and newlines. The merge keeps the order and drops the removed ids."""
  assert module._split_ids("a, b\nc ,, b") == ["a", "b", "c"]
  assert module._split_ids("") == []
  assert module._split_ids(None) == []
  assert module._merge_ids(["a", "b"], ["c", "a"], ["b"]) == ["a", "c"]
  assert module._merge_ids([{"id": "kb-1"}, {"id": "kb-2"}], [], ["kb-1"]) == [
    {"id": "kb-2"}
  ]


def test_one_preset_keeps_the_whole_record() -> None:
  """A skill add writes the full record back: the other meta keys, the prompt and the grants stay."""
  CALLS.clear()
  out = run(
    module.Tools().update_model_preset(
      model_id="own/preset",
      add_skill_ids="skill-9,skill-9",
      remove_tool_ids="tool-1",
      __user__={"id": "u1", "language": "en-US"},
    )
  )
  assert out.get("success") is True, out
  assert out["changed"] == ["toolIds", "skillIds"], out
  payload = posts()[0]
  assert payload["meta"]["skillIds"] == ["skill-1", "skill-9"], payload
  assert payload["meta"]["toolIds"] == [], payload
  assert payload["meta"]["params"] == {"x": 1}, "the other meta keys stay"
  assert payload["params"] == {"system": "keep me"}, "the system prompt stays"
  assert payload["access_grants"], "the grants go back"
  assert payload["base_model_id"] == "daedalus/auto", payload
  assert payload["is_active"] is True, payload


def test_a_knowledge_add_stores_the_reference_object() -> None:
  """The tool turns a knowledge id into the reference object the model editor stores."""
  CALLS.clear()
  out = run(
    module.Tools().update_model_preset(
      model_id="third/preset",
      add_knowledge_ids="kb-2",
      __user__={"id": "u1", "language": "en-US"},
    )
  )
  assert out["changed"] == ["knowledge"], out
  assert posts()[0]["meta"]["knowledge"] == [
    {
      "id": "kb-2",
      "name": "KB kb-2",
      "type": "collection",
      "description": "about kb-2",
    }
  ], posts()[0]


def test_a_read_only_preset_is_skipped() -> None:
  """A preset without write access reports the skip and sends no update."""
  CALLS.clear()
  out = run(
    module.Tools().update_model_preset(
      model_id="other/preset",
      add_skill_ids="skill-9",
      __user__={"id": "u1", "language": "en-US"},
    )
  )
  assert "no write access" in str(out.get("error")), out
  assert posts() == [], posts()


def test_all_presets_updates_each_writable_one() -> None:
  """The run walks the page, skips the read-only preset and posts 1 update for each other."""
  CALLS.clear()
  out = run(
    module.Tools().update_all_model_presets(
      add_skill_ids="skill-9", __user__={"id": "u1", "language": "en-US"}
    )
  )
  assert out.get("success") is True, out
  assert out["seen"] == 3, out
  assert out["updated"] == ["own/preset", "third/preset"], out
  assert out["skipped"] == ["other/preset"], out
  assert len(posts()) == 2, posts()


def test_an_empty_change_set_stops_before_any_call() -> None:
  """No add and no remove list returns the error before the first request."""
  CALLS.clear()
  out = run(module.Tools().update_all_model_presets(__user__={"id": "u1"}))
  assert "No ids given" in str(out.get("error")), out
  assert CALLS == [], CALLS


def test_the_list_shows_the_attachment_counts() -> None:
  """The list keeps the preset ids, the write access and the 5 attachment counts."""
  CALLS.clear()
  out = run(module.Tools().list_model_presets(__user__={"id": "u1"}))
  assert out["count"] == 3, out
  assert out["presets"][0]["skills"] == 1, out
  assert out["presets"][1]["write_access"] is False, out
