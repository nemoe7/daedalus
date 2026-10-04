"""The Open WebUI knowledge tool writes a preset back whole and attaches a new base to every preset."""

import asyncio
import importlib.util
import sys
import types
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Self

# The tool imports aiohttp, which the daedalus venv does not hold. The stub satisfies it.
fake = types.ModuleType("aiohttp")
fake.ClientTimeout = lambda **kwargs: None
fake.ClientSession = object
fake.ClientError = Exception
fake.ContentTypeError = Exception
sys.modules.setdefault("aiohttp", fake)

TOOL = (
  Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "tools"
  / "knowledge_manager.py"
)
SPEC = importlib.util.spec_from_file_location("owui_knowledge_manager", TOOL)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

TOOLS = [
  "create_knowledge_base",
  "create_knowledge_directory",
  "create_knowledge_markdown",
  "create_knowledge_markdown_at_path",
  "delete_knowledge_base",
  "delete_knowledge_directory",
  "delete_knowledge_file",
  "delete_knowledge_file_at_path",
  "ensure_knowledge_directory_path",
  "find_and_read_knowledge_file",
  "get_knowledge_tree",
  "list_knowledge_bases",
  "list_knowledge_files",
  "move_knowledge_directory",
  "read_knowledge_file",
  "read_knowledge_file_at_path",
  "rename_knowledge_directory",
  "rename_knowledge_file",
  "search_knowledge_files",
  "update_knowledge_base",
  "update_knowledge_file",
  "update_knowledge_file_at_path",
  "upsert_knowledge_markdown_at_path",
]

PRESETS: dict[str, dict[str, Any]] = {
  "own/preset": {
    "id": "own/preset",
    "name": "Mine",
    "base_model_id": "daedalus/auto",
    "meta": {
      "knowledge": [{"id": "kb-1", "name": "Base", "type": "collection"}],
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


def run(coro: Coroutine[Any, Any, Any]) -> Any:
  """Run one tool call."""
  return asyncio.run(coro)


class Session:
  """A fake aiohttp session that records each update body."""

  def __init__(self) -> None:
    self.posted: list[dict[str, Any]] = []

  async def __aenter__(self) -> Self:
    return self

  async def __aexit__(self, *args: object) -> bool:
    return False


class Tools(module.Tools):
  """The tool with its HTTP calls answered from the preset fixtures."""

  def __init__(self, session: Session) -> None:
    super().__init__()
    self.session = session

  async def _open_session(self, request: object) -> Session:
    return self.session

  async def _request(
    self,
    session: Session,
    method: str,
    path: str,
    *,
    timeout=None,
    expected=(200,),
    **kwargs,
  ) -> tuple[Any, str | None]:
    if (method, path) == ("GET", "/models/list"):
      page = (kwargs.get("params") or {}).get("page", 1)
      items = list(PRESETS.values()) if page == 1 else []
      return {"items": items, "total": len(PRESETS)}, None
    if (method, path) == ("GET", "/models/model"):
      return PRESETS.get((kwargs.get("params") or {}).get("id")), None
    if method == "GET" and path.startswith("/knowledge/"):
      knowledge_id = path.rsplit("/", 1)[-1]
      return {
        "id": knowledge_id,
        "name": f"KB {knowledge_id}",
        "description": f"about {knowledge_id}",
      }, None
    if (method, path) == ("POST", "/knowledge/create"):
      body = kwargs.get("json") or {}
      return {
        "id": "kb-9",
        "name": body.get("name"),
        "description": body.get("description"),
      }, None
    if (method, path) == ("POST", "/models/model/update"):
      session.posted.append(kwargs.get("json"))
      return {}, None
    raise AssertionError(f"unexpected call {method} {path}")


def test_the_tool_surface_holds() -> None:
  """The 20 knowledge tools stay public, and the preset helpers stay private."""
  names = [name for name in dir(module.Tools) if not name.startswith("_")]
  assert sorted(
    name for name in names if callable(getattr(module.Tools, name))
  ) == sorted(TOOLS)
  for name in (
    "_attach_knowledge_to_presets",
    "_list_model_presets",
    "_update_all_model_presets",
  ):
    assert callable(getattr(module.Tools, name)), name


def test_the_parser_and_the_merge() -> None:
  """Ids split on commas and newlines. The merge keeps the order and drops the removed ids."""
  assert module._split_ids("a, b\nc ,, b") == ["a", "b", "c"]
  assert module._split_ids("") == []
  assert module._split_ids(None) == []
  assert module._merge_ids(["a", "b"], ["c", "a"], ["b"]) == ["a", "c"]
  assert module._merge_ids(None, [{"id": "kb-1"}], []) == [{"id": "kb-1"}]
  assert module._merge_ids([{"id": "kb-1"}, {"id": "kb-2"}], [], ["kb-1"]) == [
    {"id": "kb-2"}
  ]


def test_one_preset_keeps_the_whole_record() -> None:
  """A merge writes the full record back: the other meta keys, the prompt and the grants stay."""
  session = Session()
  out = run(
    Tools(session)._update_model_preset(
      "own/preset",
      add_skill_ids="skill-9,skill-9",
      remove_tool_ids="tool-1",
      __request__=object(),
    )
  )
  assert "changed toolIds, skillIds" in out, out
  payload = session.posted[0]
  assert payload["meta"]["skillIds"] == ["skill-9"], payload
  assert payload["meta"]["toolIds"] == [], payload
  assert payload["meta"]["knowledge"] == [
    {"id": "kb-1", "name": "Base", "type": "collection"}
  ], payload
  assert payload["meta"]["params"] == {"x": 1}, "the other meta keys stay"
  assert payload["params"] == {"system": "keep me"}, "the system prompt stays"
  assert payload["access_grants"], "the grants go back"
  assert payload["base_model_id"] == "daedalus/auto", payload
  assert payload["is_active"] is True, payload


def test_a_knowledge_add_stores_the_reference_object() -> None:
  """The tool resolves a knowledge id to the reference object the model editor stores."""
  session = Session()
  out = run(
    Tools(session)._update_model_preset(
      "third/preset", add_knowledge_ids="kb-2", __request__=object()
    )
  )
  assert "changed knowledge" in out, out
  assert session.posted[0]["meta"]["knowledge"] == [
    {
      "id": "kb-2",
      "name": "KB kb-2",
      "type": "collection",
      "description": "about kb-2",
    }
  ], session.posted[0]


def test_a_read_only_preset_is_skipped() -> None:
  """The tool names a preset without write access and sends no update."""
  session = Session()
  out = run(
    Tools(session)._update_model_preset(
      "other/preset", add_knowledge_ids="kb-2", __request__=object()
    )
  )
  assert "no write access" in out, out
  assert session.posted == [], session.posted


def test_a_missing_preset_is_reported() -> None:
  """An unknown id reports the miss and sends nothing."""
  session = Session()
  out = run(
    Tools(session)._update_model_preset(
      "gone/preset", add_tool_ids="t", __request__=object()
    )
  )
  assert out == "Model preset not found: gone/preset", out
  assert session.posted == []


def test_all_presets_updates_each_writable_one() -> None:
  """The run walks the page, skips the read-only preset and posts 1 update for each other."""
  session = Session()
  out = run(
    Tools(session)._update_all_model_presets(
      add_skill_ids="skill-9,skill-8", __request__=object()
    )
  )
  assert "presets seen=3" in out, out
  assert "skipped, no write access: other/preset" in out, out
  assert "- Mine (id=own/preset): changed skillIds" in out, out
  assert "- Empty (id=third/preset): changed skillIds" in out, out
  assert len(session.posted) == 2, session.posted
  assert session.posted[1]["meta"]["skillIds"] == ["skill-9", "skill-8"], (
    session.posted[1]
  )


def test_an_empty_change_set_stops_before_any_call() -> None:
  """No add and no remove list returns the error line before the first request."""
  session = Session()
  out = run(Tools(session)._update_all_model_presets(__request__=object()))
  assert out.startswith("Error: no ids given"), out
  assert session.posted == []


def test_the_list_shows_the_attachment_counts() -> None:
  """The list keeps the preset ids, the write access and the 5 attachment counts."""
  out = run(Tools(Session())._list_model_presets(__request__=object()))
  assert "total=3" in out, out
  assert "(id=own/preset" in out and "knowledge=1" in out and "tools=1" in out, out
  assert "(id=other/preset" in out and "write_access=False" in out, out


def test_a_new_knowledge_base_lands_on_every_writable_preset() -> None:
  """A create attaches the new base to the writable presets and names the skipped ones."""
  session = Session()
  out = run(Tools(session).create_knowledge_base(name="New base", __request__=object()))
  assert "knowledge_id=kb-9" in out, out
  assert "preset_models: added to 2" in out, out
  assert "skipped, no write access: other/preset" in out, out
  assert len(session.posted) == 2, session.posted
  assert session.posted[0]["meta"]["knowledge"] == [
    {"id": "kb-1", "name": "Base", "type": "collection"},
    {
      "id": "kb-9",
      "name": "KB kb-9",
      "type": "collection",
      "description": "about kb-9",
    },
  ], session.posted[0]
