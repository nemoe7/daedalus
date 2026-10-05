"""The Open WebUI chat metadata Filter: 1 system message at the top, drawn again on each turn."""

import asyncio
import importlib.util
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

FILTER = (
  Path(__file__).resolve().parents[2]
  / "integrations"
  / "openwebui"
  / "functions"
  / "chat_metadata.py"
)
SPEC = importlib.util.spec_from_file_location("owui_chat_metadata", FILTER)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def run(coro: Coroutine[Any, Any, Any]) -> Any:
  """Run one Filter call."""
  return asyncio.run(coro)


class Request:
  """A stand-in for the Open WebUI request object, for its headers."""

  def __init__(self, headers: dict[str, str] | None = None) -> None:
    self.headers = headers or {}


class Clock:
  """A stand-in for `datetime` whose `now` hands back the armed time."""

  value: Any = None

  @classmethod
  def now(cls) -> Any:
    return cls.value


class Time:
  """A stand-in for the aware time of `.now().astimezone()`."""

  def __init__(self, stamp: str, zone: str) -> None:
    self.stamp = stamp
    self.zone = zone

  def astimezone(self) -> "Time":
    return self

  def replace(self, **kwargs: Any) -> "Time":
    return self

  def isoformat(self) -> str:
    return self.stamp

  def tzname(self) -> str:
    return self.zone


def test_the_block_goes_in_as_one_system_message_at_the_top(
  monkeypatch: Any,
) -> None:
  """One system message with the block draws above the messages, and the other keys stay."""
  monkeypatch.setattr(module, "datetime", Clock)
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  body = {
    "model": "daedalus/auto",
    "messages": [
      {"role": "user", "content": "hi"},
      {"role": "assistant", "content": "hello"},
    ],
  }
  out = run(module.Filter().inlet(body, Request()))
  assert out["model"] == "daedalus/auto"
  assert [message["role"] for message in out["messages"]] == [
    "system",
    "user",
    "assistant",
  ]
  content = out["messages"][0]["content"]
  assert content.startswith("[chat metadata]\n")
  assert "Current date/time: 2026-10-06T09:30:00+08:00" in content
  assert "Timezone: PST" in content
  assert body["messages"][0]["role"] == "user", "the body itself stays untouched"


def test_the_date_and_the_clock_are_drawn_again_on_each_turn(
  monkeypatch: Any,
) -> None:
  """Two turns of one chat carry their own date and clock."""
  monkeypatch.setattr(module, "datetime", Clock)
  filt = module.Filter()
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  first = run(filt.inlet({"messages": [{"role": "user", "content": "hi"}]}))[
    "messages"
  ][0]["content"]
  Clock.value = Time("2026-10-06T09:31:00+08:00", "PST")
  second = run(filt.inlet({"messages": [{"role": "user", "content": "again"}]}))[
    "messages"
  ][0]["content"]
  assert "2026-10-06T09:30:00+08:00" in first
  assert "2026-10-06T09:31:00+08:00" in second


def test_the_place_line_wears_the_approximate_label(monkeypatch: Any) -> None:
  """An IP lookup says `Approximate location`, and a precise one says `Location`."""
  monkeypatch.setattr(module, "datetime", Clock)
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  for approximate, label in ((True, "Approximate location"), (False, "Location")):
    filt = module.Filter()
    filt.valves.location = "Antipolo, Calabarzon, Philippines"
    filt.valves.location_approximate = approximate
    content = run(filt.inlet({"messages": []}))["messages"][0]["content"]
    assert f"{label}: Antipolo, Calabarzon, Philippines" in content


def test_the_language_reads_the_valve_then_the_header(monkeypatch: Any) -> None:
  """A set language valve wins, and an empty one reads the first `Accept-Language` value."""
  monkeypatch.setattr(module, "datetime", Clock)
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  filt = module.Filter()
  filt.valves.language = "fr"
  assert "Language: fr" in run(filt.inlet({"messages": []}))["messages"][0]["content"]
  filt.valves.language = ""
  headers = {"accept-language": "en-PH,en;q=0.9,fr;q=0.8"}
  content = run(filt.inlet({"messages": []}, Request(headers)))["messages"][0][
    "content"
  ]
  assert "Language: en-PH" in content


def test_a_repeat_replaces_the_block(monkeypatch: Any) -> None:
  """A body that already carries the block keeps 1 of them, at the top."""
  monkeypatch.setattr(module, "datetime", Clock)
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  body = {
    "messages": [
      {"role": "system", "content": "[chat metadata]\nCurrent date/time: old"},
      {"role": "user", "content": "hi"},
    ]
  }
  out = run(module.Filter().inlet(body))
  contents = [message["content"] for message in out["messages"]]
  assert sum(content.startswith("[chat metadata]") for content in contents) == 1
  assert contents[0].startswith("[chat metadata]")
  assert contents[1] == "hi"


def test_a_disabled_valve_leaves_the_body_alone(monkeypatch: Any) -> None:
  """The enabled valve off returns the body as it came in."""
  monkeypatch.setattr(module, "datetime", Clock)
  Clock.value = Time("2026-10-06T09:30:00+08:00", "PST")
  filt = module.Filter()
  filt.valves.enabled = False
  body = {"messages": [{"role": "user", "content": "hi"}]}
  assert run(filt.inlet(body)) is body


def test_the_valves_draw_a_schema() -> None:
  """Open WebUI builds the Valves panel from the schema of the Valves class."""
  schema = module.Filter.Valves.model_json_schema()
  assert set(schema["properties"]) == {
    "enabled",
    "location",
    "location_approximate",
    "language",
  }
  assert schema["properties"]["enabled"]["description"] == "Draw the block."
  assert module.Filter().valves.location_approximate is False
