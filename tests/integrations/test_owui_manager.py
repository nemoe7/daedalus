"""Tests for the one Open WebUI manager tool.

Tests load the tool file by path because Open WebUI tools are standalone
Python files and not part of the daedalus package. A stub replaces the HTTP
layer, so no test reaches the network.
"""

import asyncio
import importlib.util
import inspect
import json
import pathlib
import sys
import types

import pytest

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
  "list_skill_files",
  "read_skill_file",
  "install_skill",
  "create_skill",
  "update_skill",
  "update_skill_files",
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
  assert "did not confirm" in answer["result"]["reason"]
  assert calls == []


def test_allow_reads_frees_a_read_and_holds_a_mutation():
  """Allow reads lets a read run, and the gated calls still ask."""
  tool, calls = make_tool({"GET /files/": {}})
  tool.valves.permissions = "Allow reads"
  answer = asyncio.run(tool.list_files(__request__=object()))
  assert json.loads(answer)["items"] == []
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


def test_list_skills_uses_the_v012_paged_route():
  """The paged route returns items and the total, with write access."""
  row = {"id": "s1", "name": "Skill One", "is_active": True, "write_access": False}
  tool, calls = make_tool({"GET /skills/list": {"items": [row], "total": 1}})
  tool.valves.permissions = "Allow reads"
  answer = json.loads(asyncio.run(tool.list_skills(__request__=object())))
  assert answer["items"] == [row] and answer["next_page"] is None
  assert calls[0][1] == "/skills/list" and calls[0][2]["params"] == {"page": 1}


def test_create_skill_sends_the_root_and_supporting_files():
  """Skill creation keeps SKILL.md and each UTF-8 supporting file in one snapshot."""
  tool, calls = make_tool(
    {
      "POST /skills/create": {"id": "my-skill", "version_id": "v1"},
      "GET /models/list": {"items": []},
    }
  )
  tool.valves.permissions = "Allow reads"

  answer = asyncio.run(
    tool.create_skill(
      "My Skill",
      "# Instructions",
      files=[
        {"path": "references/api.md", "content": "API notes"},
        {"path": "scripts/check.py", "content": "print('ok')"},
      ],
      commit_message="Initial package",
      __request__=object(),
    )
  )

  assert "created skill My Skill (id=my-skill)" in answer
  assert "files=3" in answer
  create = next(call for call in calls if call[1] == "/skills/create")
  assert create[2]["json"]["files"] == [
    {"path": "SKILL.md", "content": "# Instructions"},
    {"path": "references/api.md", "content": "API notes"},
    {"path": "scripts/check.py", "content": "print('ok')"},
  ]
  assert create[2]["json"]["commit_message"] == "Initial package"


def test_show_and_read_skill_files_use_the_versioned_routes():
  """A skill read exposes its snapshot and pages one UTF-8 supporting file."""
  tool, calls = make_tool(
    {
      "GET /skills/id/s1/files/content": {"_text": "0123456789"},
      "GET /skills/id/s1/files": {
        "version_id": "v7",
        "files": [
          {"path": "SKILL.md", "size": 20, "encoding": None},
          {"path": "references/a.md", "size": 10, "encoding": None},
        ],
      },
      "GET /skills/id/s1": {
        "id": "s1",
        "name": "Skill One",
        "description": "one",
        "content": "# Skill One",
        "is_active": True,
        "version_id": "v7",
      },
    }
  )
  tool.valves.permissions = "Allow reads"

  shown = asyncio.run(tool.show_skill("s1", __request__=object()))
  content = asyncio.run(
    tool.read_skill_file(
      "s1",
      "references/a.md",
      version_id="v7",
      offset=2,
      max_chars=4,
      __request__=object(),
    )
  )

  assert "version_id=v7" in shown
  assert "references/a.md (size=10, encoding=text)" in shown
  manifest = next(call for call in calls if call[1] == "/skills/id/s1/files")
  assert manifest[2]["params"] == {"version_id": "v7"}
  assert "content=2345" in content
  assert "next_offset=6" in content
  read = next(call for call in calls if call[1] == "/skills/id/s1/files/content")
  assert read[2]["params"] == {"path": "references/a.md", "version_id": "v7"}
  assert read[2]["as_text"] is True


def test_update_skill_files_stays_behind_the_write_gate():
  """The shipped permission blocks a file update before any API request."""
  tool, calls = make_tool()

  answer = json.loads(
    asyncio.run(
      tool.update_skill_files(
        "s1",
        [{"op": "put", "path": "SKILL.md", "content": "# New"}],
        __request__=object(),
      )
    )
  )

  assert answer["result"]["denied"] is True
  assert calls == []


def test_update_skill_files_posts_operations_with_the_current_version():
  """A multi-file edit uses optimistic concurrency and preserves untouched files."""
  tool, calls = make_tool(
    {
      "POST /skills/id/s1/update": {"id": "s1", "version_id": "v8"},
      "GET /skills/id/s1": {
        "id": "s1",
        "name": "Skill One",
        "description": "one",
        "meta": {"tags": ["test"]},
        "is_active": True,
        "access_grants": [],
        "version_id": "v7",
      },
    }
  )
  tool.valves.permissions = "Always allow"
  operations = [
    {"op": "put", "path": "references/a.md", "content": "new"},
    {"op": "move", "path": "scripts/old.py", "destination": "scripts/check.py"},
    {"op": "delete", "path": "assets/old.png"},
  ]

  answer = asyncio.run(
    tool.update_skill_files(
      "s1", operations, commit_message="Refresh files", __request__=object()
    )
  )

  assert "updated skill files s1 (version_id=v8)" in answer
  update = next(call for call in calls if call[1] == "/skills/id/s1/update")
  body = update[2]["json"]
  assert body["expected_version_id"] == "v7"
  assert body["operations"] == operations
  assert body["commit_message"] == "Refresh files"
  assert "meta" not in body
  assert "access_grants" not in body


def test_install_skill_uses_openwebui_url_import_and_keeps_packages():
  """A URL import creates every native package, including binary supporting files."""
  packages = [
    {
      "id": "alpha",
      "name": "Alpha",
      "description": "first",
      "meta": {},
      "files": [
        {"path": "SKILL.md", "content": "# Alpha"},
        {"path": "assets/icon.png", "content": "AA==", "encoding": "base64"},
      ],
    },
    {
      "id": "beta",
      "name": "Beta",
      "description": "second",
      "meta": {},
      "files": [{"path": "SKILL.md", "content": "# Beta"}],
    },
  ]
  tool, calls = make_tool(
    {
      "POST /skills/load/url": packages,
      "POST /skills/create": {},
      "GET /models/list": {"items": []},
    }
  )
  tool.valves.permissions = "Allow reads"
  attached = []

  async def fake_attach(add, request, remove=None):
    attached.append(add)
    return "preset_models: none"

  tool._attach_to_presets = fake_attach
  target = "https://github.com/example/skills/tree/main/packages?token=secret#readme"

  answer = asyncio.run(tool.install_skill(target, __request__=object()))

  assert "imported Alpha (id=alpha, files=2)" in answer
  assert "imported Beta (id=beta, files=1)" in answer
  load = next(call for call in calls if call[1] == "/skills/load/url")
  assert load[2]["json"] == {"url": target.rsplit("#", 1)[0]}
  creates = [call for call in calls if call[1] == "/skills/create"]
  assert [call[2]["json"]["id"] for call in creates] == ["alpha", "beta"]
  assert creates[0][2]["json"]["files"][1]["encoding"] == "base64"
  assert creates[0][2]["json"]["commit_message"] == (
    "Imported from https://github.com/example/skills/tree/main/packages"
  )
  assert attached == [{"skillIds": ["alpha", "beta"]}]


@pytest.mark.parametrize(
  ("target", "message"),
  [
    ("http://github.com/example/skill.md", "must start with https://"),
    ("https://user:secret@github.com/example/skill.md", "must not contain credentials"),
    ("https://example.com/skill.md", "is not a trusted domain"),
  ],
)
def test_install_skill_rejects_unsafe_or_untrusted_urls(target, message):
  """Rejected imports never reach either Open WebUI or the remote host."""
  tool, calls = make_tool({})
  tool.valves.permissions = "Allow reads"

  answer = asyncio.run(tool.install_skill(target, __request__=object()))

  assert message in answer
  assert calls == []


def test_legacy_skill_update_keeps_its_single_content_request():
  """The v0.12 file operations do not change the existing instructions CRUD call."""
  tool, calls = make_tool({"POST /skills/id/s1/update": {"id": "s1"}})
  tool.valves.permissions = "Always allow"

  answer = asyncio.run(
    tool.update_skill("s1", "Skill One", "# Revised", "one", __request__=object())
  )

  assert answer == "updated skill s1"
  update = next(call for call in calls if call[1] == "/skills/id/s1/update")
  assert update[2]["json"] == {
    "id": "s1",
    "name": "Skill One",
    "description": "one",
    "content": "# Revised",
    "meta": {},
    "is_active": True,
  }


def test_the_preset_filter_takes_ids_and_names():
  """PRESET_MODELS selects the presets by id or by name, empty serves all."""
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


@pytest.mark.parametrize(
  ("operation", "arguments"),
  [
    ("list_knowledge_bases", {}),
    ("list_files", {}),
    ("list_skills", {}),
    ("list_tools", {}),
    ("list_functions", {}),
    ("create_skill", {"name": "status-test", "content": "instructions"}),
    ("delete_skill", {"skill_id": "s"}),
  ],
)
@pytest.mark.parametrize("enabled", [True, False])
def test_operation_status_preserves_results_and_requests(operation, arguments, enabled):
  tool, calls = make_tool()
  tool.valves.permissions = "Always allow"
  tool.valves.SHOW_STATUS = enabled
  arguments = {**arguments, "__request__": object()}
  method = getattr(tool, operation)
  expected = asyncio.run(method(**arguments))
  expected_calls = calls.copy()
  calls.clear()
  events = []

  async def emit(event):
    events.append(event)

  actual = asyncio.run(method(**arguments, __event_emitter__=emit))
  assert actual == expected
  assert calls == expected_calls
  assert events == (
    [
      {"type": "status", "data": {"description": operation, "done": False}},
      {"type": "status", "data": {"description": operation, "done": True}},
    ]
    if enabled
    else []
  )


def test_status_emitter_is_an_optional_injected_parameter():
  methods = inspect.getmembers(Tools, inspect.iscoroutinefunction)
  assert methods
  for name, method in methods:
    if name.startswith("_"):
      continue
    parameters = inspect.signature(method).parameters
    assert "__event_emitter__" in parameters, name
    parameter = parameters["__event_emitter__"]
    assert parameter.default is None, name
    assert parameter.annotation == MOD.Callable | None, name


@pytest.mark.parametrize("failure_at", [False, True])
def test_status_emission_failure_does_not_change_the_result(failure_at, caplog):
  tool, calls = make_tool()
  tool.valves.permissions = "Always allow"
  expected = asyncio.run(tool.list_skills(__request__=object()))
  expected_calls = calls.copy()
  calls.clear()
  events = []

  async def emit(event):
    events.append(event)
    if event["data"]["done"] == failure_at:
      raise RuntimeError("event transport failed")

  assert (
    asyncio.run(tool.list_skills(__request__=object(), __event_emitter__=emit))
    == expected
  )
  assert calls == expected_calls
  assert [event["data"]["done"] for event in events] == [False, True]
  assert len(caplog.records) == 1
  assert caplog.records[0].name == MOD.__name__
  assert caplog.records[0].levelname == "WARNING"
  assert caplog.records[0].args == ("list_skills",)


@pytest.mark.parametrize("error_type", [ValueError, asyncio.CancelledError])
def test_status_finishes_when_the_operation_raises(error_type):
  tool, _ = make_tool()
  events = []
  error = error_type("operation failed")

  async def emit(event):
    events.append(event)

  @MOD._bounded
  async def operation(self, __event_emitter__: MOD.Callable | None = None):
    raise error

  with pytest.raises(error_type) as caught:
    asyncio.run(operation(tool, __event_emitter__=emit))
  assert caught.value is error
  assert events == [
    {"type": "status", "data": {"description": "operation", "done": False}},
    {"type": "status", "data": {"description": "operation", "done": True}},
  ]


def test_status_finishes_after_permission_denial():
  tool, calls = make_tool()
  events = []

  async def emit(event):
    events.append(event)

  result = asyncio.run(
    tool.delete_skill("s", __request__=object(), __event_emitter__=emit)
  )
  assert json.loads(result)["result"]["denied"] is True
  assert calls == []
  assert [event["data"]["done"] for event in events] == [False, True]
