"""Tests for the Open WebUI reasoning-bump action.

The action file is loaded by path, because Open WebUI plugins are standalone Python files and
not part of the daedalus package. The HTTP layer is replaced with a stub, so no test reaches
the network.
"""

import asyncio
import importlib.util
import json as jsonlib
import pathlib
import sys
import types

import pytest

# The action imports aiohttp, which the daedalus venv does not hold. The stub satisfies it.
fake = types.ModuleType("aiohttp")
fake.ClientTimeout = lambda **kwargs: None
fake.ClientError = Exception
fake.ContentTypeError = Exception


class FakeResponse:
  """One canned HTTP answer."""

  def __init__(self, status, body):
    self.status = status
    self._body = body

  async def text(self):
    return self._body


class FakePost:
  """The async context manager of 1 POST."""

  def __init__(self, response):
    self.response = response

  async def __aenter__(self):
    return self.response

  async def __aexit__(self, *args):
    return False


class FakeSession:
  """A session that answers each path from a canned map, and keeps every call."""

  def __init__(self, answers=None, **kwargs):
    self.answers = answers or {}
    self.calls = []

  async def __aenter__(self):
    return self

  async def __aexit__(self, *args):
    return False

  def post(self, url, json=None, headers=None):
    self.calls.append({"url": url, "json": json, "headers": headers})
    for path, (status, body) in self.answers.items():
      if url.endswith(path):
        return FakePost(FakeResponse(status, jsonlib.dumps(body)))
    return FakePost(FakeResponse(404, jsonlib.dumps({"detail": "no route"})))


fake.ClientSession = FakeSession
sys.modules.setdefault("aiohttp", fake)

ACTION = (
  pathlib.Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "actions"
  / "effort_bump.py"
)
SPEC = importlib.util.spec_from_file_location("effort_bump", ACTION)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["effort_bump"] = MOD
SPEC.loader.exec_module(MOD)
Action = MOD.Action

PLAN = {
  "model": "daedalus/deinos",
  "pool": "deinos",
  "tier": 3,
  "tier_name": "TIER-B",
  "reasoning_effort": "medium",
  "before": {"tier": 2, "tier_name": "TIER-C", "reasoning_effort": "low"},
  "top": False,
}
TURN = {"role": "user", "content": "why is this slow", "id": "m1"}
PRESSED = {"role": "assistant", "content": "the old answer", "id": "m2"}
BODY = {
  "messages": [TURN, PRESSED],
  "id": "m2",
  "model": "daedalus/auto",
  "chat_id": "c1",
}
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "the new answer"}}]}


def make_action(answers=None):
  """An action whose HTTP layer is a stub, plus the stub session."""
  action = Action()
  action.valves.DAEDALUS_API_KEY = "test-key"
  session = FakeSession(answers or {MOD.LADDER: (200, PLAN), MOD.CHAT: (200, ANSWER)})

  async def fake_open():
    return session

  action._open_session = fake_open
  return action, session


def run(action, body=None, emitter=None):
  return asyncio.run(action.action(body or BODY, __event_emitter__=emitter))


def test_the_surface_of_the_action():
  """The action holds the light-bulb icon, the 2 routes and the shipped valves."""
  assert MOD.icon_url.startswith("data:image/svg+xml;base64,")
  assert MOD.LADDER == "/v1/hook/auto_reasoning" and MOD.CHAT == "/v1/chat/completions"
  valves = Action.Valves()
  assert valves.DAEDALUS_API_BASE == "http://127.0.0.1:3357"
  assert valves.DAEDALUS_API_KEY == "" and valves.timeout_seconds == 300
  assert valves.SHOW_STATUS is True


def test_one_press_bumps_then_answers_again():
  """The ladder answers the pool, and the chat runs the pressed turn again on it."""
  action, session = make_action()
  found = run(action)
  assert len(session.calls) == 2
  ladder, chat = session.calls
  assert ladder["url"] == "http://127.0.0.1:3357/v1/hook/auto_reasoning"
  assert ladder["headers"]["Authorization"] == "Bearer test-key"
  assert ladder["json"] == {
    "messages": [
      {"role": "user", "content": "why is this slow"},
      {"role": "assistant", "content": "the old answer"},
    ]
  }, ladder["json"]
  assert chat["url"] == "http://127.0.0.1:3357/v1/chat/completions"
  assert chat["json"]["model"] == "daedalus/deinos"
  assert chat["json"]["reasoning_effort"] == "medium"
  assert chat["json"]["stream"] is False
  assert chat["json"]["messages"] == [{"role": "user", "content": "why is this slow"}]
  assert found == {
    "messages": [{"role": "assistant", "id": "m2", "content": "the new answer"}]
  }, found


def test_the_context_keeps_every_turn_to_the_last_user_turn():
  """The pressed answer stays out of the new call, and the turns before it stay in."""
  action, session = make_action()
  first = {"role": "user", "content": "hi"}
  one = {"role": "assistant", "content": "hello"}
  second = {"role": "user", "content": "and now?"}
  pressed = {"role": "assistant", "content": "the old answer", "id": "m4"}
  run(action, {"messages": [first, one, second, pressed], "id": "m4"})
  assert session.calls[1]["json"]["messages"] == [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "and now?"},
  ]


def test_the_base_url_loses_a_trailing_slash():
  """The valve base URL joins the route with 1 slash."""
  action, session = make_action()
  action.valves.DAEDALUS_API_BASE = "http://host.test:1/"
  run(action)
  assert session.calls[0]["url"] == "http://host.test:1/v1/hook/auto_reasoning"


def test_a_missing_key_stops_before_the_network():
  """An empty key valve names itself, and no call leaves the action."""
  action, session = make_action()
  action.valves.DAEDALUS_API_KEY = ""
  with pytest.raises(ValueError, match="DAEDALUS_API_KEY"):
    run(action)
  assert session.calls == []


def test_a_call_with_no_messages_fails():
  """A call with no messages names the fault, and no call leaves the action."""
  action, session = make_action()
  with pytest.raises(ValueError, match="no messages"):
    run(action, {"id": "m1"})
  assert session.calls == []


def test_a_daedalus_error_names_the_route():
  """A 500 of the ladder stops the press and names the status."""
  action, session = make_action({MOD.LADDER: (500, {"detail": "boom"})})
  with pytest.raises(ValueError, match="500"):
    run(action)
  assert len(session.calls) == 1


def test_an_empty_answer_fails():
  """An answer with no text stops the press."""
  action, _ = make_action({MOD.LADDER: (200, PLAN), MOD.CHAT: (200, {"choices": []})})
  with pytest.raises(ValueError, match="no answer text"):
    run(action)


def test_a_plan_with_no_model_fails():
  """The ladder must name a model, or the chat call never runs."""
  action, session = make_action({MOD.LADDER: (200, {"reasoning_effort": "low"})})
  with pytest.raises(ValueError, match="no model"):
    run(action)
  assert len(session.calls) == 1


def test_the_status_line_names_the_plan():
  """The status of a press names the pool and the effort."""
  action, _ = make_action()
  events = []

  async def emit(event):
    events.append(event)

  run(action, emitter=emit)
  assert len(events) == 1
  assert events[0]["type"] == "status" and events[0]["data"]["done"] is True
  assert "daedalus/deinos" in events[0]["data"]["description"]
  assert "medium" in events[0]["data"]["description"]


def test_a_top_plan_marks_the_status():
  """A chat on the top rung says so in the status."""
  action, _ = make_action(
    {MOD.LADDER: (200, {**PLAN, "top": True}), MOD.CHAT: (200, ANSWER)}
  )
  events = []

  async def emit(event):
    events.append(event)

  run(action, emitter=emit)
  assert events[0]["data"]["description"].endswith("· top")


def test_no_status_when_the_valve_is_off():
  """The status valve turns the line off."""
  action, _ = make_action()
  action.valves.SHOW_STATUS = False
  events = []

  async def emit(event):
    events.append(event)

  run(action, emitter=emit)
  assert events == []


def test_the_pressed_message_keeps_its_id_and_drops_an_error():
  """The answer replaces the pressed text, and a stale error mark goes away."""
  action, _ = make_action()
  pressed = {
    "role": "assistant",
    "content": "the old answer",
    "id": "m2",
    "error": {"content": "x"},
  }
  found = run(action, {"messages": [TURN, pressed], "id": "m2"})
  assert found["messages"][0]["id"] == "m2"
  assert "error" not in found["messages"][0]
  assert found["messages"][0]["content"] == "the new answer"
