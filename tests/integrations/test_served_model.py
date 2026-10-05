"""The Open WebUI served model Filter: it draws the line of the chunk that carries it, and passes every chunk on."""

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
  picked = {"usage": {"daedalus": {"line": "B \u00b7 kilo/y"}}}
  assert run(plugin.stream(picked, __event_emitter__=emit)) is picked
  assert seen == []


def test_an_empty_line_draws_nothing() -> None:
  """A served model with an empty or a non-string line draws no row, so the list has no empty entry."""
  seen: list[dict[str, Any]] = []

  async def emit(event: dict[str, Any]) -> None:
    seen.append(event)

  plugin = module.Filter()
  for pick in ({"line": ""}, {"line": None}, {"line": 7}, {}):
    chunk = {"usage": {"daedalus": pick}}
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
