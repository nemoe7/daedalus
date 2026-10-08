"""
title: Served model
author: nemo
description: The daedalus served model line above the answer body. It reads the stream usage. No key.
required_open_webui_version: 0.10.0
version: 1.0.3
licence: daedalus Noncommercial License 1.0.0
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Filter:
  """One status line above the answer body, from the `daedalus` key of the final chunk."""

  class Valves(BaseModel):
    enabled: bool = Field(default=True, description="Draw the line.")
    prefix: str = Field(
      default="", description="Text before the line, for example `served`."
    )
    when: Literal["always", "on change"] = Field(
      default="always",
      description="`always` draws each answer. `on change` draws a line only when the served model moves.",
    )

  def __init__(self) -> None:
    self.valves = self.Valves()
    # The served model of the last line of each chat, for the `on change` valve.
    self.drawn: dict[str, str] = {}
    # The last line of each message, so a repeat of it adds no row.
    self.lines: dict[str, str] = {}

  def _keep(self, book: dict[str, str], key: str, value: str) -> str | None:
    """The value of that key before this one, and this value takes its place, with 200 keys at most."""
    before = book.get(key)
    if len(book) > 200:
      book.pop(next(iter(book)))
    book[key] = value
    return before

  def _changed(self, chat: str, name: str) -> bool:
    """True when the served model moved for this chat, and keep it as the last one."""
    return self._keep(self.drawn, chat, name) != name

  async def stream(
    self,
    event: dict[str, Any],
    __event_emitter__: Any = None,
    __metadata__: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    """Read one stream chunk, draw the line when it carries the served model, and the chunk goes on.

    :param event: the stream chunk of the answer
    :param __metadata__: the chat ids of the call, for the `on change` valve
    """
    if getattr(self.valves, "enabled", True) and callable(__event_emitter__):
      usage = event.get("usage") if isinstance(event, dict) else None
      served = usage.get("daedalus") if isinstance(usage, dict) else None
      line = served.get("line") if isinstance(served, dict) else None
      if isinstance(line, str) and line:
        chat, message = "", ""
        if isinstance(__metadata__, dict):
          chat = str(__metadata__.get("chat_id") or "")
          message = str(__metadata__.get("message_id") or "")
        name = str(served.get("model") or line)
        # The lines of 1 message ride in its own list: a repeat of its last line adds no row.
        if message and self._keep(self.lines, message, line) == line:
          return event
        if self.valves.when == "on change" and not self._changed(chat, name):
          return event
        await __event_emitter__(
          {
            "type": "status",
            "data": {
              "description": f"{getattr(self.valves, 'prefix', '')}{line}",
              "done": True,
            },
          }
        )
    return event
