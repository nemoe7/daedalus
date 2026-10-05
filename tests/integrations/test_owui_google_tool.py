"""The Open WebUI Google tool reads freely, gates every write and propagates errors."""

import ast
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from typing import Self
from urllib.error import HTTPError

TOOL = (
  Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "tools"
  / "google.py"
)
SPEC = importlib.util.spec_from_file_location("owui_google_tool", TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

FUNCS = [
  "agenda",
  "append_to_document",
  "create_event",
  "read_document",
  "read_thread",
  "search_files",
  "search_mail",
  "send_mail",
]


class Answer:
  """A FakeResponse for urlopen: read returns the body, the context manager closes."""

  def __init__(self, body: str):
    self.body = body.encode()

  def read(self) -> bytes:
    return self.body

  def __enter__(self) -> Self:
    return self

  def __exit__(self, *args: object) -> bool:
    return False


def opener(monkeypatch, body, calls=None, error=None):
  """Patch module urlopen and record each request. A list body answers in order."""
  replies = list(body) if isinstance(body, list) else None
  served = 0

  def urlopen(request, timeout=None):
    nonlocal served
    if calls is not None:
      calls.append(
        {
          "method": request.get_method(),
          "url": request.full_url,
          "headers": dict(request.headers),
          "data": request.data,
        }
      )
    if error is not None:
      raise error
    if replies is None:
      return Answer(body)
    answer = replies[min(served, len(replies) - 1)]
    served += 1
    return Answer(answer)

  monkeypatch.setattr(tool, "urlopen", urlopen)


def token_body(token="at1", expires=3600) -> str:
  return json.dumps({"access_token": token, "expires_in": expires})


def client() -> object:
  instance = tool.Tools()
  instance.valves.google_client_id = "client-id"
  instance.valves.google_client_secret = "client-secret"
  instance.valves.google_refresh_token = "refresh-token"
  instance.valves.max_results = 10
  return instance


async def answer(payload):
  return payload["type"] == "confirmation"


def reader() -> object:
  """A client whose read valve is allow, so a read runs without a dialog."""
  instance = client()
  instance.valves.permissions = "Allow reads"
  return instance


def test_the_surface_holds_the_eight_functions():
  tree = ast.parse(TOOL.read_text())
  cls = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "Tools"
  )
  found = [
    node.name
    for node in cls.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and not node.name.startswith("_")
  ]
  assert sorted(found) == sorted(FUNCS)


def test_the_gate_defaults_to_ask_with_sixty_seconds():
  instance = tool.Tools()
  assert instance.valves.permissions == "Always ask"
  assert instance.valves.timeout_seconds == 60
  assert instance.valves.calendar_id == "primary"
  assert tool.Tools.UserValves().mode == "default"
  assert tool.Tools.UserValves().timeout_seconds == 0


def test_every_write_goes_through_the_gate():
  tree = ast.parse(TOOL.read_text())
  cls = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "Tools"
  )
  for node in cls.body:
    if not isinstance(
      node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) or node.name.startswith("_"):
      continue
    body = ast.get_source_segment(TOOL.read_text(), node) or ""
    if any(method in body for method in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"')):
      assert "self._write(" in body, f"{node.name} mutates without the gate"


def test_the_shipped_permission_asks_on_a_read(monkeypatch):
  calls = []
  opener(monkeypatch, [token_body()], calls)
  out = asyncio.run(client().search_mail("from:ana"))
  assert out["result"]["denied"] is True
  assert calls == []


def test_allow_reads_runs_without_a_dialog(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      token_body(),
      json.dumps({"messages": [{"id": "m1", "threadId": "t1"}]}),
      json.dumps(
        {
          "id": "m1",
          "threadId": "t1",
          "snippet": "hello",
          "payload": {
            "mimeType": "text/plain",
            "headers": [
              {"name": "From", "value": "ana@example.com"},
              {"name": "Subject", "value": "Hi"},
            ],
          },
        }
      ),
    ],
    calls,
  )
  out = asyncio.run(reader().search_mail("from:ana"))
  assert out["result"]["messages"][0]["subject"] == "Hi"
  assert calls[0]["url"] == "https://oauth2.googleapis.com/token"
  assert "q=from%3Aana" in calls[1]["url"]
  assert "metadataHeaders=From" in calls[2]["url"]
  assert calls[2]["headers"]["Authorization"] == "Bearer at1"


def test_the_token_is_cached(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      token_body(),
      json.dumps({"messages": []}),
      json.dumps({"messages": []}),
    ],
    calls,
  )
  instance = reader()
  asyncio.run(instance.search_mail("a"))
  asyncio.run(instance.search_mail("b"))
  tokens = [
    call for call in calls if call["url"] == "https://oauth2.googleapis.com/token"
  ]
  assert len(tokens) == 1


def test_send_mail_is_denied_without_a_dialog(monkeypatch):
  calls = []
  opener(monkeypatch, token_body(), calls)
  out = asyncio.run(
    client().send_mail("ana@example.com", "hi", "body", __event_call__=None)
  )
  assert out["result"]["denied"] is True
  assert calls == []


def test_send_mail_sends_one_message(monkeypatch):
  calls = []
  opener(monkeypatch, [token_body(), json.dumps({"id": "s1", "threadId": "t9"})], calls)
  out = asyncio.run(
    client().send_mail("ana@example.com", "Hello", "the body", __event_call__=answer)
  )
  assert out["result"]["id"] == "s1"
  assert calls[1]["url"].endswith("/gmail/v1/users/me/messages/send")
  raw = json.loads(calls[1]["data"])["raw"]
  decoded = base64.urlsafe_b64decode(raw + "==").decode()
  assert "Subject: Hello" in decoded
  assert "the body" in decoded


def test_agenda_reads_the_window(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      token_body(),
      json.dumps(
        {
          "items": [
            {
              "id": "e1",
              "summary": "Standup",
              "start": {"dateTime": "2026-10-06T09:00:00+08:00"},
              "end": {"dateTime": "2026-10-06T09:15:00+08:00"},
              "status": "confirmed",
            }
          ]
        }
      ),
    ],
    calls,
  )
  out = asyncio.run(reader().agenda(days=3))
  assert out["result"]["events"][0]["summary"] == "Standup"
  assert "/calendars/primary/events?" in calls[1]["url"]
  assert "singleEvents=true" in calls[1]["url"]


def test_create_event_is_gated_and_posts_the_window(monkeypatch):
  calls = []
  opener(monkeypatch, [token_body(), json.dumps({"id": "e2", "htmlLink": "l"})], calls)
  denied = asyncio.run(
    client().create_event(
      "Dinner", "2026-10-06T19:00:00+08:00", "2026-10-06T21:00:00+08:00"
    )
  )
  assert denied["result"]["denied"] is True
  assert calls == []
  out = asyncio.run(
    client().create_event(
      "Dinner",
      "2026-10-06T19:00:00+08:00",
      "2026-10-06T21:00:00+08:00",
      location="Home",
      __event_call__=answer,
    )
  )
  assert out["result"]["id"] == "e2"
  body = json.loads(calls[1]["data"])
  assert body["summary"] == "Dinner"
  assert body["location"] == "Home"
  assert body["start"] == {"dateTime": "2026-10-06T19:00:00+08:00"}


def test_search_files_wraps_plain_words(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [token_body(), json.dumps({"files": [{"id": "f1", "name": "Notes"}]})],
    calls,
  )
  out = asyncio.run(reader().search_files("quarterly notes"))
  assert out["result"]["files"][0]["name"] == "Notes"
  assert "fullText+contains" in calls[1]["url"]


def test_read_document_exports_a_google_doc(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    [
      token_body(),
      json.dumps(
        {"id": "d1", "name": "Doc", "mimeType": "application/vnd.google-apps.document"}
      ),
      "the document text",
    ],
    calls,
  )
  out = asyncio.run(reader().read_document("d1", max_chars=1000))
  assert out["result"]["text"] == "the document text"
  assert out["result"]["truncated"] is False
  assert calls[1]["url"].endswith("/drive/v3/files/d1?fields=id%2Cname%2CmimeType")
  assert "/export?mimeType=text%2Fplain" in calls[2]["url"]


def test_append_to_document_posts_the_batch(monkeypatch):
  calls = []
  opener(monkeypatch, [token_body(), json.dumps({"replies": []})], calls)
  out = asyncio.run(
    client().append_to_document("d1", "\nnew line", __event_call__=answer)
  )
  assert out["result"]["appended"] == 9
  body = json.loads(calls[1]["data"])
  assert body == {
    "requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "\nnew line"}}]
  }
  assert calls[1]["url"].endswith("/documents/d1:batchUpdate")


def test_a_google_error_propagates(monkeypatch):
  calls = []
  opener(
    monkeypatch,
    token_body(),
    calls,
    error=HTTPError("https://gmail.googleapis.com/", 404, "Not Found", {}, None),
  )
  try:
    asyncio.run(reader().search_mail("x"))
  except tool.GoogleError as error:
    assert error.status == 404
  else:
    raise AssertionError("GoogleError did not propagate")
