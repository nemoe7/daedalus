"""Tests for the one Open WebUI manager tool.

The tool file is loaded by path, because Open WebUI tools are standalone
Python files and not part of the daedalus package. The HTTP layer is
replaced with a stub, so no test reaches the network.
"""

import asyncio
import importlib.util
import json
import pathlib
import sys
import types

# The tool imports aiohttp, which the daedalus venv does not hold. The stub satisfies it.
fake = types.ModuleType("aiohttp")
fake.ClientTimeout = lambda **kwargs: None
fake.ClientSession = object
fake.ClientError = Exception
fake.ContentTypeError = Exception
sys.modules.setdefault("aiohttp", fake)

TOOL = (
  pathlib.Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "tools"
  / "owui_manager.py"
)
SPEC = importlib.util.spec_from_file_location("owui_manager", TOOL)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["owui_manager"] = MOD
SPEC.loader.exec_module(MOD)
Tools = MOD.Tools

KNOWLEDGE = [
  "list_knowledge_bases",
  "list_knowledge_files",
  "create_knowledge_base",
  "update_knowledge_base",
  "create_knowledge_directory",
  "ensure_knowledge_directory_path",
  "create_knowledge_markdown",
  "create_knowledge_markdown_at_path",
  "update_knowledge_file",
  "read_knowledge_file",
  "update_knowledge_file_at_path",
  "read_knowledge_file_at_path",
  "upsert_knowledge_markdown_at_path",
  "rename_knowledge_file",
  "rename_knowledge_directory",
  "move_knowledge_directory",
  "delete_knowledge_file",
  "delete_knowledge_file_at_path",
  "delete_knowledge_directory",
  "search_knowledge_files",
  "find_and_read_knowledge_file",
  "get_knowledge_tree",
  "delete_knowledge_base",
]
FILES = [
  "list_files",
  "search_files",
  "upload_file",
  "rename_file",
  "read_file_content",
  "delete_file",
]
SKILLS = [
  "list_skills",
  "show_skill",
  "install_skill",
  "create_skill",
  "update_skill",
  "delete_skill",
]
TOOLS = [
  "list_tools",
  "show_tool",
  "create_tool",
  "update_tool",
  "toggle_tool",
  "delete_tool",
]
FUNCTIONS = [
  "list_functions",
  "show_function",
  "create_function",
  "update_function",
  "toggle_function",
  "delete_function",
]


class FakeSession:
  async def __aenter__(self):
    return self

  async def __aexit__(self, *args):
    return False


def public_names():
  """The public async tool names of the class."""
  names = []
  for name in dir(Tools):
    if name.startswith("_"):
      continue
    value = getattr(Tools, name)
    if callable(value) and getattr(value, "__qualname__", "").startswith("Tools."):
      names.append(name)
  return sorted(names)


def make_tool(answers=None):
  """A tool whose HTTP layer is a stub, plus the call log."""
  tool = Tools()
  calls = []
  answers = answers or {}

  async def fake_request(session, method, path, **kwargs):
    calls.append((method, path, kwargs))
    for key, value in answers.items():
      wanted_method, _, prefix = key.partition(" ")
      if method == wanted_method and path.startswith(prefix):
        return value, None
    return {}, None

  async def fake_open(request):
    return FakeSession()

  tool._request = fake_request
  tool._open_session = fake_open
  return tool, calls


def test_the_one_file_holds_the_five_groups():
  """The names of the plan are all there, and no valve tool joins them."""
  names = public_names()
  for group in (KNOWLEDGE, FILES, SKILLS, TOOLS, FUNCTIONS):
    for name in group:
      assert name in names, name
  assert not [name for name in names if "valve" in name]
  assert len(KNOWLEDGE) == 23


def test_the_shipped_permission_holds_a_mutation():
  """Always ask refuses a mutation before the network, with no dialog."""
  tool, calls = make_tool()
  assert tool.valves.permissions == "Always ask"
  answer = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert answer["result"]["denied"] is True
  assert calls == []


def test_always_allow_runs_the_mutation():
  """Always allow runs the mutation."""
  tool, calls = make_tool({"DELETE /files/f1": {}})
  tool.valves.permissions = "Always allow"
  answer = asyncio.run(tool.delete_file("f1", __request__=object()))
  assert "deleted file f1" in answer
  assert calls[0][0] == "DELETE"


def test_the_gate_denies_when_no_dialog_is_possible():
  """The shipped permission refuses when no event call reaches the tool."""
  tool, calls = make_tool()
  answer = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert answer["result"]["denied"] is True
  assert "Not confirmed" in answer["result"]["reason"]
  assert calls == []


def test_allow_reads_frees_a_read_and_holds_a_mutation():
  """Allow reads lets a read run, and the gated calls still ask."""
  tool, calls = make_tool({"GET /files/": {}})
  tool.valves.permissions = "Allow reads"
  answer = asyncio.run(tool.list_files(__request__=object()))
  assert "No files found" in answer
  denied = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert denied["result"]["denied"] is True
  assert [call[0] for call in calls] == ["GET"]


def test_a_new_item_runs_free_and_joins_the_presets():
  """A create runs free, and its id joins the preset meta list."""
  presets = {
    "items": [
      {"id": "m1", "name": "Model 1", "write_access": True, "meta": {}},
    ],
    "total": 1,
  }
  tool, calls = make_tool()
  tool.valves.permissions = "Allow reads"

  async def fake_request(session, method, path, **kwargs):
    calls.append((method, path, kwargs))
    if method == "GET" and path.startswith("/models/list"):
      return (
        presets
        if kwargs.get("params", {}).get("page") == 1
        else {"items": [], "total": 1}
      ), None
    if method == "POST" and path.startswith("/skills/create"):
      return {"id": "my-skill"}, None
    if method == "GET" and path.startswith("/models/model"):
      return {"id": "m1", "name": "Model 1", "write_access": True, "meta": {}}, None
    return {}, None

  tool._request = fake_request
  answer = asyncio.run(tool.create_skill("My Skill", "body", __request__=object()))
  assert "created skill My Skill (id=my-skill)" in answer
  assert "added to 1" in answer
  writes = [call for call in calls if call[1] == "/models/model/update"]
  assert len(writes) == 1
  payload = writes[0][2]["json"]
  assert payload["meta"]["skillIds"] == ["my-skill"]


def test_the_preset_filter_takes_ids_and_names():
  """PRESET_MODELS picks the presets by id or by name, empty serves all."""
  tool, _ = make_tool()
  assert tool._preset_selected({"id": "m1", "name": "One"}) is True
  tool.valves.PRESET_MODELS = "m2, Two"
  assert tool._preset_selected({"id": "m1", "name": "One"}) is False
  assert tool._preset_selected({"id": "m2", "name": "Two"}) is True
  assert tool._preset_selected({"id": "m3", "name": "Two"}) is True


def test_the_attach_can_remove_a_tool_from_the_presets():
  """The attach holds both directions, and functionIds rides the payload."""
  presets = {
    "items": [
      {
        "id": "m1",
        "name": "One",
        "write_access": True,
        "meta": {"toolIds": ["t1"], "functionIds": ["f1"]},
      },
    ],
    "total": 1,
  }
  tool, calls = make_tool()

  async def fake_request(session, method, path, **kwargs):
    calls.append((method, path, kwargs))
    if method == "GET" and path.startswith("/models/list"):
      return presets, None
    if method == "GET" and path.startswith("/models/model"):
      return presets["items"][0], None
    return {}, None

  tool._request = fake_request
  answer = asyncio.run(
    tool._attach_to_presets(
      {}, object(), remove={"toolIds": ["t1"], "functionIds": ["f1"]}
    )
  )
  assert "added to 1" in answer
  write = next(call for call in calls if call[1] == "/models/model/update")
  payload = write[2]["json"]
  assert payload["meta"]["toolIds"] == []
  assert payload["meta"]["functionIds"] == []


def test_the_merge_keeps_the_order_and_the_ids():
  """The merge drops the removed entries, adds the new ones and keeps order."""
  merged = MOD._merge_ids(
    [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
    [{"id": "c", "name": "C"}, {"id": "a", "name": "A"}],
    ["b"],
  )
  assert [MOD._entry_key(item) for item in merged] == ["a", "c"]
  assert MOD._split_ids("a, b" + chr(10) + "c, a") == ["a", "b", "c"]
