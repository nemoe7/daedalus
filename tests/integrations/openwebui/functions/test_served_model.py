"""The Open WebUI served model Filter: it draws the line of the chunk that carries it, and passes every chunk on."""

import asyncio
import importlib.util
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

FILTER = (
  Path(__file__).resolve().parents[4]
  / "integrations"
  / "openwebui"
  / "functions"
  / "served_model.py"
)
SPEC = importlib.util.spec_from_file_location("owui_served_model", FILTER)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def run(coro: Coroutine[Any, Any, Any]) -> Any:
  """Run one Filter call."""
  return asyncio.run(coro)


def test_the_line_goes_out() -> None:
  """A chunk with the served model emits one status, and the chunk itself comes back unchanged."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  chunk = {
    "choices": [{"finish_reason": "stop"}],
    "usage": {"daedalus": {"line": "A \u00b7 kilo/x"}},
  }
  assert run(module.Filter().stream(chunk, __event_emitter__=emit)) is chunk
  assert seen == [
    {"type": "status", "data": {"description": "A \u00b7 kilo/x", "done": True}}
  ]


def test_a_chunk_without_the_key_draws_nothing() -> None:
  """A chunk without the served model key, and a turned-off Valve, draw nothing and still pass the chunk on."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  chunk = {"choices": []}
  assert run(plugin.stream(chunk, __event_emitter__=emit)) is chunk
  plugin.valves.enabled = False
  selected = {"usage": {"daedalus": {"line": "B \u00b7 kilo/y"}}}
  assert run(plugin.stream(selected, __event_emitter__=emit)) is selected
  assert seen == []


def test_an_empty_line_draws_nothing() -> None:
  """A served model with an empty or a non-string line draws no row, so the list has no empty entry."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  for selection in ({"line": ""}, {"line": None}, {"line": 7}, {}):
    chunk = {"usage": {"daedalus": selection}}
    assert run(plugin.stream(chunk, __event_emitter__=emit)) is chunk
  assert seen == []


def test_the_on_change_valve_draws_only_a_move() -> None:
  """With the `on change` Valve, the line goes out on a new served model, and a repeat stays quiet."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  plugin.valves.when = "on change"
  meta = {"chat_id": "c1"}
  first = {"usage": {"daedalus": {"line": "A \u00b7 kilo/x", "model": "kilo/x"}}}
  same = {"usage": {"daedalus": {"line": "A \u00b7 kilo/x", "model": "kilo/x"}}}
  moved = {"usage": {"daedalus": {"line": "B \u00b7 kilo/y", "model": "kilo/y"}}}
  other = {"usage": {"daedalus": {"line": "A \u00b7 kilo/x", "model": "kilo/x"}}}
  assert run(plugin.stream(first, __event_emitter__=emit, __metadata__=meta)) is first
  assert run(plugin.stream(same, __event_emitter__=emit, __metadata__=meta)) is same
  assert run(plugin.stream(moved, __event_emitter__=emit, __metadata__=meta)) is moved
  assert (
    run(plugin.stream(other, __event_emitter__=emit, __metadata__={"chat_id": "c2"}))
    is other
  )
  assert [event["data"]["description"] for event in seen] == [
    "A \u00b7 kilo/x",
    "B \u00b7 kilo/y",
    "A \u00b7 kilo/x",
  ], seen


def test_a_repeat_of_the_message_line_draws_once() -> None:
  """In 1 message a repeat of the last line draws no row, a move draws, and another message draws."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  first = {"chat_id": "c1", "message_id": "m1"}
  moved = {"usage": {"daedalus": {"line": "B \u00b7 kilo/y", "model": "kilo/y"}}}
  same = {"usage": {"daedalus": {"line": "A \u00b7 kilo/x", "model": "kilo/x"}}}
  run(plugin.stream(same, __event_emitter__=emit, __metadata__=first))
  run(plugin.stream(same, __event_emitter__=emit, __metadata__=first))
  run(plugin.stream(moved, __event_emitter__=emit, __metadata__=first))
  run(plugin.stream(moved, __event_emitter__=emit, __metadata__=first))
  run(
    plugin.stream(
      same, __event_emitter__=emit, __metadata__={"chat_id": "c1", "message_id": "m2"}
    )
  )
  assert [event["data"]["description"] for event in seen] == [
    "A \u00b7 kilo/x",
    "B \u00b7 kilo/y",
    "A \u00b7 kilo/x",
  ], seen


def test_the_always_valve_draws_every_line() -> None:
  """The default `always` Valve draws each line, even a repeat of the same served model."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  chunk = {"usage": {"daedalus": {"line": "A \u00b7 kilo/x", "model": "kilo/x"}}}
  run(plugin.stream(chunk, __event_emitter__=emit, __metadata__={"chat_id": "c1"}))
  run(plugin.stream(chunk, __event_emitter__=emit, __metadata__={"chat_id": "c1"}))
  assert len(seen) == 2, seen


def test_the_valves_draw_a_schema() -> None:
  """Open WebUI builds the Valves panel from the schema of Valves class."""
  schema = module.Filter.Valves.model_json_schema()
  assert set(schema["properties"]) == {
    "enabled",
    "prefix",
    "append_provider",
    "when",
  }
  assert schema["properties"]["when"]["enum"] == ["always", "on change"]
  description = schema["properties"]["enabled"]["description"]
  assert isinstance(description, str) and description.strip()
  valves = module.Filter().valves
  assert valves.when == "always"
  assert valves.append_provider is False


def test_append_provider_valve_appends_provider_when_enabled() -> None:
  """The `append_provider` Valve appends the provider from `usage.daedalus.model`."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  first = {
    "usage": {
      "daedalus": {"line": "Laguna S 2.1", "model": "kilo/poolside/laguna-s-2.1"}
    }
  }
  second = {
    "usage": {
      "daedalus": {"line": "Laguna S 2.1", "model": "openrouter/poolside/laguna-s-2.1"}
    }
  }
  run(plugin.stream(first, __event_emitter__=emit, __metadata__={"message_id": "m1"}))
  plugin.valves.append_provider = True
  run(plugin.stream(first, __event_emitter__=emit, __metadata__={"message_id": "m2"}))
  run(plugin.stream(second, __event_emitter__=emit, __metadata__={"message_id": "m2"}))
  assert [event["data"]["description"] for event in seen] == [
    "Laguna S 2.1",
    "Laguna S 2.1 \u00b7 kilo",
    "Laguna S 2.1 \u00b7 openrouter",
  ], seen


def test_tracking_caches_keep_the_bound_without_evicting_on_updates() -> None:
  for name in ("drawn", "lines"):
    plugin = module.Filter()
    book = getattr(plugin, name)
    for index in range(200):
      assert plugin._keep(book, str(index), "value") is None
    assert plugin._keep(book, "199", "changed") == "value"
    assert len(book) == 200 and book["0"] == "value"
    assert plugin._keep(book, "200", "new") is None
    assert len(book) == 200 and "0" not in book
    assert book["199"] == "changed" and book["200"] == "new"
    assert plugin._keep(book, "1", "changed") == "value"
    assert len(book) == 200 and book["2"] == "value"
