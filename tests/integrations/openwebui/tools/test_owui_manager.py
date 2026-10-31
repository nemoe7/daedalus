"""Test the standalone Open WebUI manager with a mocked HTTP layer."""

import asyncio
import importlib.util
import inspect
import json
import pathlib
import sys

import aiohttp
import pytest
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartParser

TOOL = (
  pathlib.Path(__file__).resolve().parents[4]
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
  "save_knowledge_base",
  "delete_knowledge_base",
  "list_knowledge_files",
  "search_knowledge_files",
  "read_knowledge_file",
  "save_knowledge_file",
  "delete_knowledge_file",
  "manage_knowledge_directory",
]
FILES = [
  "list_files",
  "read_file_content",
  "save_file",
  "delete_file",
]
SKILLS = [
  "list_skills",
  "show_skill",
  "save_skill",
  "delete_skill",
]
TOOLS = [
  "list_tools",
  "show_tool",
  "save_tool",
  "delete_tool",
]
FUNCTIONS = [
  "list_functions",
  "show_function",
  "save_function",
  "delete_function",
]
EXPECTED_TOOL_COUNT = 25


class FakeSession:
  async def __aenter__(self):
    return self

  async def __aexit__(self, *args):
    return False


def public_names():
  """Return sorted public async tools of `Tools`."""
  names = []
  for name in dir(Tools):
    if name.startswith("_"):
      continue
    value = getattr(Tools, name)
    if inspect.iscoroutinefunction(value) and getattr(
      value, "__qualname__", ""
    ).startswith("Tools."):
      names.append(name)
  return sorted(names)


def make_tool(answers=None):
  """Build a `Tools` instance backed by an HTTP stub and call log."""
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
  """Verify all five consolidated groups total 25 public tools without valve methods."""
  names = public_names()
  expected = sorted(KNOWLEDGE + FILES + SKILLS + TOOLS + FUNCTIONS)
  assert names == expected
  assert len(names) == EXPECTED_TOOL_COUNT
  assert not [name for name in names if "valve" in name]


def test_the_shipped_permission_holds_a_mutation():
  """Verify `Always ask` denies mutations before any network call."""
  tool, calls = make_tool()
  assert tool.valves.permissions == "Always ask"
  answer = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert answer["result"]["denied"] is True
  assert calls == []


def test_always_allow_runs_the_mutation():
  """Verify `Always allow` executes delete mutations."""
  tool, calls = make_tool({"DELETE /files/f1": {}})
  tool.valves.permissions = "Always allow"
  answer = asyncio.run(tool.delete_file("f1", __request__=object()))
  assert "deleted file f1" in answer
  assert calls[0][0] == "DELETE"


def test_the_gate_denies_when_no_dialog_is_possible():
  """Verify `Always ask` refuses mutations when no confirmation callback exists."""
  tool, calls = make_tool()
  answer = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert answer["result"]["denied"] is True
  assert "did not confirm" in answer["result"]["reason"]
  assert calls == []


def test_allow_reads_frees_a_read_and_holds_a_mutation():
  """Verify `Allow reads` permits reads while gating mutations."""
  tool, calls = make_tool({"GET /files/": {}})
  tool.valves.permissions = "Allow reads"
  answer = asyncio.run(tool.list_files(__request__=object()))
  assert json.loads(answer)["items"] == []
  denied = json.loads(asyncio.run(tool.delete_file("f1", __request__=object())))
  assert denied["result"]["denied"] is True
  assert [call[0] for call in calls] == ["GET"]


def test_a_new_item_runs_free_and_joins_the_presets():
  """Verify skill creation runs under `Allow reads` and attaches to writable presets."""
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
  answer = asyncio.run(
    tool.save_skill(
      action="create", name="My Skill", content="body", __request__=object()
    )
  )
  assert "created skill My Skill (id=my-skill)" in answer
  assert "added to 1" in answer
  writes = [call for call in calls if call[1] == "/models/model/update"]
  assert len(writes) == 1
  payload = writes[0][2]["json"]
  assert payload["meta"]["skillIds"] == ["my-skill"]


def test_list_skills_uses_the_v012_paged_route():
  """Verify `list_skills` calls `/skills/list` and preserves write-access fields."""
  row = {"id": "s1", "name": "Skill One", "is_active": True, "write_access": False}
  tool, calls = make_tool({"GET /skills/list": {"items": [row], "total": 1}})
  tool.valves.permissions = "Allow reads"
  answer = json.loads(asyncio.run(tool.list_skills(__request__=object())))
  assert answer["items"] == [row] and answer["next_page"] is None
  assert calls[0][1] == "/skills/list" and calls[0][2]["params"] == {"page": 1}


def test_create_skill_sends_the_root_and_supporting_files():
  """Verify `save_skill(action='create')` sends `SKILL.md` and UTF-8 supporting files."""
  tool, calls = make_tool(
    {
      "POST /skills/create": {"id": "my-skill", "version_id": "v1"},
      "GET /models/list": {"items": []},
    }
  )
  tool.valves.permissions = "Allow reads"

  answer = asyncio.run(
    tool.save_skill(
      action="create",
      name="My Skill",
      content="# Instructions",
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
  """Verify `show_skill` reads both skill manifests and character-paged supporting files."""
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
    tool.show_skill(
      "s1",
      path="references/a.md",
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
  """Verify `save_skill(action='update_files')` requires write approval."""
  tool, calls = make_tool()

  answer = json.loads(
    asyncio.run(
      tool.save_skill(
        action="update_files",
        skill_id="s1",
        operations=[{"op": "put", "path": "SKILL.md", "content": "# New"}],
        __request__=object(),
      )
    )
  )

  assert answer["result"]["denied"] is True
  assert calls == []


def test_update_skill_files_posts_operations_with_the_current_version():
  """Verify `save_skill(action='update_files')` posts optimistic-concurrency operations."""
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
    tool.save_skill(
      action="update_files",
      skill_id="s1",
      operations=operations,
      commit_message="Refresh files",
      __request__=object(),
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
  """Verify `save_skill(action='import_url')` imports packages and binary files."""
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

  answer = asyncio.run(
    tool.save_skill(action="import_url", url=target, __request__=object())
  )

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
  """Verify rejected URL imports fail before any HTTP request."""
  tool, calls = make_tool({})
  tool.valves.permissions = "Allow reads"

  answer = asyncio.run(
    tool.save_skill(action="import_url", url=target, __request__=object())
  )

  assert message in answer
  assert calls == []


def test_legacy_skill_update_keeps_its_single_content_request():
  """Verify `save_skill(action='update')` preserves single-content updates."""
  tool, calls = make_tool({"POST /skills/id/s1/update": {"id": "s1"}})
  tool.valves.permissions = "Always allow"

  answer = asyncio.run(
    tool.save_skill(
      action="update",
      skill_id="s1",
      name="Skill One",
      content="# Revised",
      description="one",
      __request__=object(),
    )
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


def test_consolidated_crud_and_toggles_across_groups():
  """Verify consolidated base, directory, file, tool, and function actions."""
  tool, calls = make_tool(
    {
      "POST /knowledge/create": {"id": "kb1", "name": "KB", "description": "d"},
      "GET /knowledge/kb1": {"id": "kb1", "name": "KB", "description": "d"},
      "POST /knowledge/kb1/update": {"name": "KB2", "description": "d"},
      "POST /knowledge/kb1/dirs/d1/update": {"name": "Renamed", "parent_id": "p1"},
      "DELETE /knowledge/kb1/dirs/d1/delete": {},
      "POST /files/f1/rename": {},
      "POST /skills/id/s1/toggle": {"is_active": False},
      "POST /tools/create": {"id": "t1"},
      "POST /tools/id/t1/update": {},
      "POST /functions/create": {"id": "fn1"},
      "POST /functions/id/fn1/update": {},
      "POST /functions/id/fn1/toggle": {"is_active": True, "is_global": False},
      "GET /models/list": {"items": []},
    }
  )
  tool.valves.permissions = "Always allow"

  assert "knowledge_id=kb1" in asyncio.run(
    tool.save_knowledge_base(name="KB", description="d", __request__=object())
  )
  assert "name=KB2" in asyncio.run(
    tool.save_knowledge_base(name="KB2", knowledge_id="kb1", __request__=object())
  )
  assert "new_name=Renamed" in asyncio.run(
    tool.manage_knowledge_directory(
      "kb1",
      action="rename",
      directory_id="d1",
      new_name="Renamed",
      __request__=object(),
    )
  )
  assert "parent_id=p1" in asyncio.run(
    tool.manage_knowledge_directory(
      "kb1",
      action="move",
      directory_id="d1",
      target_parent_id="p1",
      __request__=object(),
    )
  )
  assert "deleted" in asyncio.run(
    tool.manage_knowledge_directory(
      "kb1", action="delete", directory_id="d1", __request__=object()
    )
  )
  assert "renamed file f1 to next.md" in asyncio.run(
    tool.save_file("next.md", file_id="f1", __request__=object())
  )
  assert "toggled skill s1" in asyncio.run(
    tool.save_skill(action="toggle", skill_id="s1", __request__=object())
  )
  assert "created tool My Tool (id=my_tool)" in asyncio.run(
    tool.save_tool(
      action="create", name="My Tool", content="pass", __request__=object()
    )
  )
  assert "updated tool t1" in asyncio.run(
    tool.save_tool(
      action="update", tool_id="t1", name="T1", content="pass", __request__=object()
    )
  )
  assert "created function My Fn (id=my_fn)" in asyncio.run(
    tool.save_function(
      action="create", name="My Fn", content="pass", __request__=object()
    )
  )
  assert "updated function fn1" in asyncio.run(
    tool.save_function(
      action="update",
      function_id="fn1",
      name="Fn1",
      content="pass",
      __request__=object(),
    )
  )
  assert "toggled function fn1" in asyncio.run(
    tool.save_function(action="toggle", function_id="fn1", __request__=object())
  )
  assert calls


def test_the_preset_filter_takes_ids_and_names():
  """Verify `PRESET_MODELS` matches preset IDs and names or accepts all when empty."""
  tool, _ = make_tool()
  assert tool._preset_selected({"id": "m1", "name": "One"}) is True
  tool.valves.PRESET_MODELS = "m2, Two"
  assert tool._preset_selected({"id": "m1", "name": "One"}) is False
  assert tool._preset_selected({"id": "m2", "name": "Two"}) is True
  assert tool._preset_selected({"id": "m3", "name": "Two"}) is True


def test_the_attach_can_remove_a_tool_from_the_presets():
  """Verify `_attach_to_presets` removes tool and function IDs cleanly."""
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
  """Verify `_merge_ids` drops removed entries, appends new ones, and preserves order."""
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
    (
      "save_skill",
      {"action": "create", "name": "status-test", "content": "instructions"},
    ),
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


PATH_OPERATIONS = [
  (
    "ensure_path",
    "manage_knowledge_directory",
    {"action": "ensure_path"},
    "directory_id=new-dir",
  ),
  (
    "create",
    "save_knowledge_file",
    {"content": "body", "mode": "create"},
    "file_id=uploaded",
  ),
  ("read", "read_knowledge_file", {}, "file_id=f"),
  ("update", "save_knowledge_file", {"content": "body", "mode": "update"}, "file_id=f"),
  ("upsert", "save_knowledge_file", {"content": "body", "mode": "upsert"}, "file_id=f"),
  ("delete", "delete_knowledge_file", {"confirm": True}, "file_id=f"),
]


@pytest.mark.parametrize(("kind", "operation", "kwargs", "identifier"), PATH_OPERATIONS)
def test_knowledge_path_operations_normalize_and_resolve(
  kind, operation, kwargs, identifier
):
  tool, calls = make_tool(
    {
      "GET /knowledge/k/files": {
        "directories": [{"id": "d", "name": "folder"}],
        "items": [{"file": {"id": "f", "filename": "note.md"}}],
        "total": 1,
      },
      "POST /knowledge/k/dirs/create": {"id": "new-dir", "name": "child"},
      "GET /files/f/data/content": {"content": "body"},
    }
  )
  tool.valves.permissions = "Always allow"
  uploads = []

  async def upload(session, knowledge_id, directory_id, filename, content):
    uploads.append((knowledge_id, directory_id, filename, content))
    return {"id": "uploaded", "filename": filename}, None

  tool._upload = upload
  leaf = "child" if kind == "ensure_path" else "note.md"
  path = " /folder/ /\\" + leaf + "/ "
  result = asyncio.run(
    getattr(tool, operation)(
      knowledge_id="k", path=path, **kwargs, __request__=object()
    )
  )
  assert identifier in result
  assert calls[0][1] == "/knowledge/k/files"
  writes = [call for call in calls if call[0] != "GET"]
  if kind == "ensure_path":
    assert len(writes) == 1
    assert writes[0][2]["json"] == {"name": "child", "parent_id": "d"}
  elif kind == "create":
    assert uploads == [("k", "d", "note.md", "body")]
  elif kind in ("update", "upsert"):
    assert len(writes) == 1
    assert writes[0][1] == "/files/f/data/content/update"
    assert writes[0][2]["json"] == {"content": "body"}
  elif kind == "delete":
    assert [(method, url) for method, url, _ in writes] == [("DELETE", "/files/f")]
  else:
    assert not writes
    assert "body" in result


@pytest.mark.parametrize(("kind", "operation", "kwargs", "identifier"), PATH_OPERATIONS)
@pytest.mark.parametrize("path", [None, "", "folder/../note.md", "folder/./note.md"])
def test_knowledge_path_operations_reject_invalid_paths_before_requests(
  kind, operation, kwargs, identifier, path
):
  tool, calls = make_tool()
  tool.valves.permissions = "Always allow"
  result = asyncio.run(
    getattr(tool, operation)(
      knowledge_id="k", path=path, **kwargs, __request__=object()
    )
  )
  assert "Error:" in result
  assert calls == []


@pytest.mark.parametrize(
  ("mode", "user_mode", "decision", "denied", "asked"),
  [
    ("Always ask", None, None, True, False),
    ("Always ask", None, False, True, True),
    ("Always ask", None, True, False, True),
    ("Allow reads", None, None, False, False),
    ("Always allow", None, None, False, False),
    ("Always allow", "Always ask", False, True, True),
    ("Always ask", "Always allow", None, False, False),
    ("Always ask", "Allow reads", None, False, False),
    ("Allow reads", "Always ask", True, False, True),
  ],
)
def test_directory_path_creation_uses_the_new_item_gate(
  mode, user_mode, decision, denied, asked
):
  tool, calls = make_tool({"POST /knowledge/k/dirs/create": {"id": "d"}})
  tool.valves.permissions = mode
  questions = []

  async def confirm(event):
    assert calls == []
    questions.append(event)
    assert "new" in event["data"]["message"]
    return decision

  user = {"valves": {"mode": user_mode}} if user_mode else None
  result = asyncio.run(
    tool.manage_knowledge_directory(
      "k",
      action="ensure_path",
      path="new",
      __request__=object(),
      __user__=user,
      __event_call__=confirm if decision is not None else None,
    )
  )
  assert bool(questions) is asked
  if denied:
    assert json.loads(result)["result"]["denied"] is True
    assert calls == []
  else:
    assert "directory_id=d" in result
    assert [(method, url) for method, url, _ in calls] == [
      ("GET", "/knowledge/k/files"),
      ("POST", "/knowledge/k/dirs/create"),
    ]


@pytest.mark.parametrize(
  ("operation", "arguments", "targets"),
  [
    (
      "list_knowledge_files",
      {
        "knowledge_id": "knowledge-target",
        "directory_id": "directory-target",
        "page": 2,
      },
      ("knowledge-target", "directory-target", "2"),
    ),
    ("read_knowledge_file", {"file_id": "file-target"}, ("file-target",)),
    (
      "read_knowledge_file",
      {"knowledge_id": "knowledge-target", "path": "folder/note.md"},
      ("knowledge-target", "folder/note.md"),
    ),
    ("search_knowledge_files", {"query": "query-target"}, ("query-target",)),
    (
      "search_knowledge_files",
      {"query": "query-target", "exact_filename": "note-target.md", "read_match": True},
      ("query-target", "note-target.md"),
    ),
    (
      "list_knowledge_files",
      {"knowledge_id": "knowledge-target", "tree": True},
      ("knowledge-target",),
    ),
    ("list_files", {"filename": "file-target"}, ("file-target",)),
    ("read_file_content", {"file_id": "file-target"}, ("file-target",)),
    ("show_skill", {"skill_id": "skill-target"}, ("skill-target",)),
    ("show_tool", {"tool_id": "tool-target"}, ("tool-target",)),
    ("show_function", {"function_id": "function-target"}, ("function-target",)),
  ],
)
@pytest.mark.parametrize("approved", [False, True])
def test_read_confirmation_identifies_targets_before_requests(
  operation, arguments, targets, approved
):
  tool, calls = make_tool()
  events = []
  before = []

  async def confirm(event):
    events.append(event)
    before.append(bool(calls))
    return approved

  result = asyncio.run(
    getattr(tool, operation)(
      **arguments,
      __request__=object(),
      __event_call__=confirm,
    )
  )
  assert len(events) == 1
  assert events[0]["type"] == "confirmation"
  for target in targets:
    assert target in events[0]["data"]["message"]
  assert before == [False]
  if approved:
    assert calls
  else:
    assert json.loads(result)["result"]["denied"] is True
    assert calls == []


@pytest.mark.parametrize(
  "filename, content",
  [
    ("notes.md", "plain text"),
    ('quoted"name.md', "text"),
    ("spaced name.md", "first\nsecond\r\n"),
    ("nóta.md", "日本語\ntext"),
    ("", ""),
    (
      "notes.md",
      (
        "first\r\n--daedalus-openwebui-manager\r\n"
        'Content-Disposition: form-data; name="extra"\r\n\r\nsecond'
      ),
    ),
  ],
)
@pytest.mark.parametrize("permissions", ["Always allow", "Allow reads"])
def test_upload_file_multipart_round_trip(filename, content, permissions):
  async def run():
    tool, calls = make_tool({"POST /files/": {"id": "fixture-file"}})
    tool.valves.permissions = permissions
    answer = await tool.save_file(filename, content, __request__=object())
    assert "id=fixture-file" in answer
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/files/")
    assert kwargs["expected"] == (200, 201)
    body = kwargs["data"]
    chunks = []

    class Writer:
      async def write(self, chunk):
        chunks.append(chunk)

    if isinstance(body, aiohttp.FormData):
      payload = body()
      content_type = payload.headers["Content-Type"]
      await payload.write(Writer())
    else:
      content_type = kwargs["headers"]["Content-Type"]
      chunks.append(body)

    async def stream():
      yield b"".join(chunks)

    form = await MultiPartParser(
      Headers({"Content-Type": content_type}), stream()
    ).parse()
    try:
      assert list(form) == ["file"]
      uploaded = form["file"]
      assert uploaded.filename == (filename.strip() or "untitled.txt")
      assert uploaded.content_type == "text/markdown"
      assert await uploaded.read() == content.encode("utf-8")
    finally:
      await form.close()

  asyncio.run(run())


@pytest.mark.parametrize("filename", ["bad\rname.md", "bad\nname.md", "name.md\r\n"])
def test_upload_file_rejects_filename_line_breaks(filename):
  async def run():
    tool, calls = make_tool()
    tool.valves.permissions = "Always allow"
    answer = await tool.save_file(filename, "text", __request__=object())
    assert answer.startswith("Error:")
    assert calls == []

  asyncio.run(run())


def test_upload_file_denial_precedes_request():
  async def run():
    tool, calls = make_tool()
    answer = await tool.save_file("notes.md", "text", __request__=object())
    assert json.loads(answer)["result"]["denied"] is True
    assert calls == []

  asyncio.run(run())
