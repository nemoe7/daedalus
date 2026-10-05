"""
title: Served model
author: nemo
description: The daedalus served model line, as a status above the answer body. It reads the usage of the stream chunks, and it needs no key.
required_open_webui_version: 0.10.0
version: 1.0.0
licence: daedalus Noncommercial License 1.0.0
"""

from typing import Any


class Filter:
  """One status line above the answer body, from the `daedalus` key of the final chunk."""

  class Valves:
    # Draw the line. A Valve, so the line can be silenced without uninstalling the Filter.
    enabled: bool = True
    # Text before the line, for example `served`. Empty by default.
    prefix: str = ""

  def __init__(self) -> None:
    self.valves = self.Valves()

  async def stream(
    self, event: dict[str, Any], __event_emitter__: Any = None
  ) -> dict[str, Any]:
    """Read one stream chunk, draw the line when it carries the served model, and the chunk goes on.

    :param event: the stream chunk of the answer
    """
    if getattr(self.valves, "enabled", True) and callable(__event_emitter__):
      usage = event.get("usage") if isinstance(event, dict) else None
      served = usage.get("daedalus") if isinstance(usage, dict) else None
      line = served.get("line") if isinstance(served, dict) else None
      if isinstance(line, str) and line:
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
