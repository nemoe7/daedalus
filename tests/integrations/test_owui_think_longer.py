"""Tests for the Open WebUI think-longer action.

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
  / "think_longer.py"
)
SPEC = importlib.util.spec_from_file_location("think_longer", ACTION)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["think_longer"] = MOD
SPEC.loader.exec_module(MOD)
Action = MOD.Action


class FakeRequest:
  """The call of the action, as Open WebUI hands it: the base URL and the caller headers."""

  def __init__(self, base_url="http://webui.test:8080/", headers=None):
    self.base_url = base_url
    self.headers = (
      headers
      if headers is not None
      else {
        "authorization": "Bearer user-token",
        "cookie": "owui=1",
      }
    )


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
  session = FakeSession(answers or {MOD.CHAT: (200, ANSWER)})

  async def fake_open():
    return session

  action._open_session = fake_open
  return action, session


def run(action, body=None, emitter=None, request=None):
  return asyncio.run(
    action.action(
      body or BODY, __event_emitter__=emitter, __request__=request or FakeRequest()
    )
  )


def test_the_surface_of_the_action():
  """The action holds the light-bulb icon, the Open WebUI route, and no daedalus key."""
  assert MOD.icon_url.startswith("data:image/svg+xml;base64,")
  assert MOD.CHAT == "/api/chat/completions" and MOD.FIELD == "think_longer"
  assert MOD.STEPS == 1
  valves = Action.Valves()
  assert valves.timeout_seconds == 300
  assert valves.SHOW_STATUS is True
  assert not hasattr(valves, "DAEDALUS_API_KEY"), "the action holds no daedalus key"


def test_one_press_asks_to_think_longer_and_answers_again():
  """The action asks Open WebUI for the pressed turn at the next level."""
  action, session = make_action()
  found = run(action)
  assert len(session.calls) == 1
  chat = session.calls[0]
  assert chat["url"] == "http://webui.test:8080/api/chat/completions"
  assert chat["headers"]["Authorization"] == "Bearer user-token"
  assert chat["headers"]["Cookie"] == "owui=1"
  assert chat["json"] == {
    "model": "daedalus/auto",
    "messages": [{"role": "user", "content": "why is this slow"}],
    "think_longer": 1,
    "stream": False,
  }, chat["json"]
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
  run(
    action,
    {
      "messages": [first, one, second, pressed],
      "id": "m4",
      "model": "daedalus/auto",
    },
  )
  assert session.calls[0]["json"]["messages"] == [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "and now?"},
  ]


def test_the_base_url_loses_a_trailing_slash():
  """The request base URL joins the route with 1 slash."""
  action, session = make_action()
  run(action, request=FakeRequest(base_url="http://webui.test:8080"))
  assert session.calls[0]["url"] == "http://webui.test:8080/api/chat/completions"


def test_a_call_with_no_request_fails():
  """An action call with no request names the fault, and no call leaves the action."""
  action, session = make_action()
  with pytest.raises(ValueError, match="no request"):
    asyncio.run(action.action(BODY, __event_emitter__=None, __request__=None))
  assert session.calls == []


def test_a_call_with_no_messages_fails():
  """A call with no messages names the fault, and no call leaves the action."""
  action, session = make_action()
  with pytest.raises(ValueError, match="no messages"):
    run(action, {"id": "m1", "model": "daedalus/auto"})
  assert session.calls == []


def test_a_call_with_no_model_fails():
  """A call with no model names the fault, and no call leaves the action."""
  action, session = make_action()
  with pytest.raises(ValueError, match="no model"):
    run(action, {"messages": [TURN, PRESSED], "id": "m2"})
  assert session.calls == []


def test_a_call_with_no_message_id_fails():
  """A call with no pressed message id names the fault, and no call leaves the action."""
  action, session = make_action()
  with pytest.raises(ValueError, match="no message id"):
    run(
      action,
      {"messages": [{"role": "user", "content": "hi"}], "model": "daedalus/auto"},
    )
  assert session.calls == []


def test_an_error_of_open_webui_names_the_route():
  """A 500 of the chat route stops the press and names the status."""
  action, _ = make_action({MOD.CHAT: (500, {"detail": "boom"})})
  with pytest.raises(ValueError, match="500"):
    run(action)


def test_an_empty_answer_fails():
  """An answer with no text stops the press."""
  action, _ = make_action({MOD.CHAT: (200, {"choices": []})})
  with pytest.raises(ValueError, match="no answer text"):
    run(action)


def test_the_status_line_names_the_step():
  """The status of a press names the model and the step that was asked."""
  action, _ = make_action()
  events = []

  async def emit(event):
    events.append(event)

  run(action, emitter=emit)
  assert len(events) == 1
  assert events[0]["type"] == "status" and events[0]["data"]["done"] is True
  assert events[0]["data"]["description"] == "daedalus/auto · think longer +1"


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
  found = run(
    action, {"messages": [TURN, pressed], "id": "m2", "model": "daedalus/auto"}
  )
  assert found["messages"][0]["id"] == "m2"
  assert "error" not in found["messages"][0]
  assert found["messages"][0]["content"] == "the new answer"
